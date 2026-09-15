#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage6/7 紧凑终审包：稳定主体、真实证据与精确幂等。"""
from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from contracts.schema.api import (
    CompactApprovalCheck, Stage6ApprovalMaterials, Stage6ApprovalWorkpack, Stage7ApprovalWorkpack,
)
from contracts.schema.stage6 import ApprovedHook, HookApprovalResolution
from contracts.schema.stage7 import (
    ApprovedNameMap, ExtraSub, NameDecision, Residual, StemBaseline,
)
from novel.stages.stage6 import hook, semantic
from novel.stages.stage7 import rename
from tests.factories import TEXT_A, TEXT_B, TEXT_C
from tests.factories import stage6_hook as _hook


def _compact_check() -> CompactApprovalCheck:
    return CompactApprovalCheck(
        rules_scanned=0,
        counts={'ok': 0, 'hit': 0, 'unver': 0, 'skipped': 0},
        provenance={'freshness': 'fresh', 'reason': 'match'},
    )


class _Report(SimpleNamespace):
    """工作包的 validation seal 需要完整报告载荷；测试桩也必须交出同一接口。"""

    def model_dump(self, **_kwargs) -> dict[str, object]:
        return {'node': getattr(self, 'node', 'synthetic-approval-check')}


def _stage6_materials() -> Stage6ApprovalMaterials:
    return Stage6ApprovalMaterials.model_validate({
        'prepare_subject_sha256': 'd' * 64,
        'source_hook': '新婚夜收到来信。',
        'source_blurb': {
            'facts': [{'kind': 'hook_supported', 'claim_id': 'hook_001',
                       'claim': '新婚夜收到来信', 'hook_evidence': {
                           'source': {'text': '新婚夜收到来信。', 'start': 0, 'end': 8},
                           'unresolved_locator': None}}],
            'opening': {'kind': 'proposed', 'through_chapter': 1, 'rationale': '合成开篇范围',
                        'boundary_evidence': {'chapter_ordinal': 1,
                                              'source': {'text': '天亮了。', 'start': 0, 'end': 4}}},
        },
        'rewritten_chapters': [{'chapter_ordinal': 1, 'text': '天亮了。'}],
    })


def _stage6_snapshot_env(monkeypatch):
    art = _hook([TEXT_A, TEXT_B, TEXT_C])
    monkeypatch.setattr(hook, '_Upstreams', lambda *_args: object())
    monkeypatch.setattr(hook, '_approval_materials', lambda *_args: _stage6_materials())
    monkeypatch.setattr(hook, '_judge_artifact', lambda *_args: [])
    monkeypatch.setattr(
        hook, '_report', lambda *_args, **_kwargs: _Report(node='stage6.approve'))
    monkeypatch.setattr(
        hook, 'compact_approval_check', lambda _report: _compact_check())
    monkeypatch.setattr(
        hook.prov, 'rules_fingerprint', lambda _stage: 'rules-stage6')
    store = SimpleNamespace(ctx=SimpleNamespace(sid='book-6', code='code-6'))
    return store, art


def test_stage6_workpack_hash_is_stable_after_approval_and_old_approval_is_not_current(
    monkeypatch,
) -> None:
    store, art = _stage6_snapshot_env(monkeypatch)
    checked_subjects = []

    def judge_candidate(subject, *_args):
        checked_subjects.append(subject)
        return []

    monkeypatch.setattr(hook, '_judge_artifact', judge_candidate)
    before, before_report = hook._approval_workpack_snapshot(store, art)
    public = before.model_dump(mode='json')
    assert before.v == 4
    assert before.review_contract_version == 'stage6-final-review-v5'
    assert public['check_basis'] == 'candidate_without_approval'
    with pytest.raises(ValidationError, match='check_basis'):
        Stage6ApprovalWorkpack.model_validate({**public, 'check_basis': 'current_approval'})
    assert 'code_version' not in public and 'rules_fingerprint' not in public
    with pytest.raises(ValidationError, match='disposition'):
        Stage6ApprovalWorkpack.model_validate({
            **public, 'disposition': 'suspended',
        })
    candidate = art.candidates[0]
    resolution = HookApprovalResolution(
        actor='reviewer-a', reason='逐项核验 A/B 与机械证据',
        subject_sha256=before.candidates[0].subject_sha256,
        basis='human_selection',
        workpack_sha256=before.workpack_sha256,
        review_identity_sha256=before.review_identity_sha256,
    )
    approved = ApprovedHook(
        slot='c1', text=candidate.text, origin='human',
        mech=candidate.mech, findings=list(candidate.findings),
        resolution=resolution,
    )

    current, current_report = hook._approval_workpack_snapshot(
        store, art.model_copy(update={'approved': approved}))

    assert len(checked_subjects) == 2
    assert all(subject.approved is None for subject in checked_subjects)
    assert current.check == before.check
    assert current_report.model_dump() == before_report.model_dump()
    assert current.review_identity_sha256 == before.review_identity_sha256
    assert current.workpack_sha256 == before.workpack_sha256
    assert current.artifact_subject_sha256 == before.artifact_subject_sha256
    assert current.disposition == 'already_current'
    assert current.current_approval == resolution

    # resolution / workpack 仍在但定稿正文被单独改坏时，UI/Inbox 不能误报
    # already_current；下游会按正文 SHA 拒绝，两边必须使用同一 currentness。
    tampered, _ = hook._approval_workpack_snapshot(
        store, art.model_copy(update={'approved': approved.model_copy(update={
            'text': candidate.text + '（被单独改坏）',
        })}))
    assert tampered.workpack_sha256 == before.workpack_sha256
    assert tampered.disposition == 'review_required'
    assert tampered.current_approval is None

    legacy_resolution = resolution.model_copy(update={
        'review_identity_sha256': None,
    })
    legacy, _ = hook._approval_workpack_snapshot(
        store, art.model_copy(update={'approved': approved.model_copy(update={
            'resolution': legacy_resolution,
        })}))
    assert legacy.workpack_sha256 == before.workpack_sha256
    assert legacy.disposition == 'review_required'
    assert legacy.current_approval is None

    store.ctx.code = 'code-6-next'
    changed_code, _ = hook._approval_workpack_snapshot(
        store, art.model_copy(update={'approved': approved}))
    assert changed_code.workpack_sha256 != before.workpack_sha256
    assert changed_code.validation_seal_sha256 != before.validation_seal_sha256
    assert changed_code.review_identity_sha256 == before.review_identity_sha256
    assert changed_code.disposition == 'already_current'


def test_stage6_workpack_pins_its_report_node_when_rechecked_by_stage7(
    monkeypatch,
) -> None:
    """下游消费闸重算 Stage6 工作包时，不得拿 Stage7 判据表解释 hook 结论。"""
    store, art = _stage6_snapshot_env(monkeypatch)
    store.ctx.node = 'stage7.rename'
    seen: dict[str, str | None] = {}

    def capture_report(*_args, report_node=None, **_kwargs):
        seen['report_node'] = report_node
        return _Report(node=report_node)

    monkeypatch.setattr(hook, '_report', capture_report)

    hook._approval_workpack_snapshot(store, art)

    assert seen == {'report_node': 'stage6.approve'}


@pytest.mark.parametrize('part', ['hook', 'claim', 'body'])
def test_stage6_visible_fact_materials_invalidate_the_review_identity(monkeypatch, part):
    store, art = _stage6_snapshot_env(monkeypatch)
    before, _ = hook._approval_workpack_snapshot(store, art)
    data = _stage6_materials().model_dump(mode='json')
    if part == 'hook':
        data['source_hook'] += '原钩子新增限定。'
    elif part == 'claim':
        data['source_blurb']['facts'][0]['claim'] += '并确认了寄信人'
    else:
        data['rewritten_chapters'][0]['text'] += '她并未收到来信。'
    changed_materials = Stage6ApprovalMaterials.model_validate(data)
    monkeypatch.setattr(hook, '_approval_materials', lambda *_args: changed_materials)
    after, _ = hook._approval_workpack_snapshot(store, art)
    assert after.artifact_subject_sha256 == before.artifact_subject_sha256
    assert after.review_identity_sha256 != before.review_identity_sha256
    assert after.review_token != before.review_token


def test_stage6_current_check_reuses_candidate_baseline_in_one_invocation(
    monkeypatch,
) -> None:
    """已有定稿只补判定稿文本；三个候选沿用同调用栈的去 approval 基准。"""
    judge_artifact = hook._judge_artifact
    store, base_art = _stage6_snapshot_env(monkeypatch)
    candidate = base_art.candidates[0]
    resolution = HookApprovalResolution(
        actor='reviewer-a', reason='逐项复核同一不可变工作包',
        subject_sha256=semantic.text_sha256(candidate.text),
        basis='human_selection',
        review_identity_sha256='e' * 64,
    )
    art = base_art.model_copy(update={'approved': ApprovedHook(
        slot='c1', text=candidate.text, origin='human',
        mech=candidate.mech, findings=list(candidate.findings),
        resolution=resolution,
    )})
    report = _Report(node='stage6.approve')
    baseline = hook._ApprovalBaseline(
        store=store,
        artifact_subject_sha256=hook._approval_subject_sha256(base_art),
        texts=tuple(
            (f'c{index}', row.text, tuple())
            for index, row in enumerate(base_art.candidates, 1)
        ),
        stale=(), missing=(), report=report, materials=_stage6_materials(),
    )
    normalized: list[str] = []
    observed: dict[str, object] = {}

    def normalize(text, _up):
        normalized.append(text)
        return text, candidate.mech, []

    def outcomes(_art, _up, _store, rows, **kwargs):
        observed['rows'] = rows
        observed['approved'] = kwargs['approved']
        return []

    monkeypatch.setattr(hook, '_normalize_and_judge', normalize)
    monkeypatch.setattr(hook, '_outcomes_for_rows', outcomes)
    monkeypatch.setattr(
        hook, 'approval_is_current',
        lambda _store, _art, *, approval_baseline=None:
        approval_baseline is baseline,
    )

    assert judge_artifact(
        art, object(), store, approval_baseline=baseline) == []
    assert normalized == [candidate.text]
    assert len(observed['rows']) == 4
    assert observed['approved'] is True


def test_stage6_workpack_consumes_exact_invocation_baseline_without_rejudge(
    monkeypatch,
) -> None:
    store, art = _stage6_snapshot_env(monkeypatch)
    report = _Report(node='stage6.approve')
    baseline = hook._ApprovalBaseline(
        store=store,
        artifact_subject_sha256=hook._approval_subject_sha256(art),
        texts=(), stale=(), missing=(), report=report, materials=_stage6_materials(),
    )
    monkeypatch.setattr(
        hook, '_judge_artifact',
        lambda *_args, **_kwargs: pytest.fail('exact baseline 不应重新 judge'))

    _pack, got_report = hook._approval_workpack_snapshot_uncached(
        store, art, approval_baseline=baseline)

    assert got_report is report


class _Stage6Store:
    def __init__(self, art) -> None:
        self.ctx = SimpleNamespace(sid='book-6', code='code-6')
        self.art = art
        self.writes = []

    def exists(self, _rel: str) -> bool:
        return True

    def read_own(self, _model, _rel: str):
        return self.art, b'raw'

    def write_round(self, _rel: str, art) -> None:
        self.writes.append(art)
        self.art = art

    def write(self, _rel: str, art) -> None:
        self.writes.append(art)
        self.art = art


def test_stage6_exact_replay_is_idempotent_but_a_new_reason_is_a_new_statement(
    monkeypatch, tmp_path,
) -> None:
    snapshot_store, base_art = _stage6_snapshot_env(monkeypatch)
    review_pack, report = hook._approval_workpack_snapshot(
        snapshot_store, base_art)
    candidate = base_art.candidates[0]
    resolution = HookApprovalResolution(
        actor='reviewer-a', reason='第一份人工声明',
        subject_sha256=review_pack.candidates[0].subject_sha256,
        basis='human_selection',
        workpack_sha256=review_pack.workpack_sha256,
        review_identity_sha256=review_pack.review_identity_sha256,
    )
    art = base_art.model_copy(update={'approved': ApprovedHook(
        slot='c1', text=candidate.text, origin='human',
        mech=candidate.mech, findings=list(candidate.findings),
        resolution=resolution,
    )})
    current_pack, _ = hook._approval_workpack_snapshot(snapshot_store, art)
    store = _Stage6Store(art)
    monkeypatch.setattr(hook, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(
        hook, '_approval_workpack_snapshot',
        lambda _store, _art: (current_pack, report))
    monkeypatch.setattr(
        semantic, 'require_human_resolution',
        lambda *_args, **_kwargs: 'human_selection')
    monkeypatch.setattr(hook, '_Upstreams', lambda *_args: object())
    monkeypatch.setattr(hook, '_judge_artifact', lambda *_args: [])
    monkeypatch.setattr(hook, '_report', lambda *_args: report)

    common = {
        'pick': 1,
        'actor': 'reviewer-a',
        'review_token': current_pack.review_token,
    }
    same, _ = hook.run_approve(
        SimpleNamespace(novel_dir=tmp_path), reason='第一份人工声明', **common)
    assert same is art and store.writes == []

    changed, _ = hook.run_approve(
        SimpleNamespace(novel_dir=tmp_path),
        reason='补充说明：重新核对了证据范围', **common)
    assert len(store.writes) == 1
    assert changed.approved is not None
    assert changed.approved.resolution.reason == '补充说明：重新核对了证据范围'


def _legacy_stage6_approval(monkeypatch):
    snapshot_store, art = _stage6_snapshot_env(monkeypatch)
    pack, _report = hook._approval_workpack_snapshot(snapshot_store, art)
    candidate = art.candidates[0]
    resolution = HookApprovalResolution(
        actor='legacy-reviewer', reason='旧契约下逐项核过三候选与机械证据',
        subject_sha256=pack.candidates[0].subject_sha256,
        basis='human_selection',
        workpack_sha256=pack.workpack_sha256,
        review_identity_sha256=None,
    )
    approved = ApprovedHook(
        slot='c1', text=candidate.text, origin='human',
        mech=candidate.mech, findings=list(candidate.findings),
        resolution=resolution,
    )
    return art.model_copy(update={'approved': approved})


def test_stage6_legacy_rebind_only_adds_stable_identity_and_preserves_statement(
        monkeypatch, tmp_path) -> None:
    legacy = _legacy_stage6_approval(monkeypatch)
    store = _Stage6Store(legacy)
    monkeypatch.setattr(hook, 'ArtifactStore', lambda _ctx: store)

    before_candidates = list(legacy.candidates)
    before_provenance = legacy.provenance
    before_approval = legacy.approved
    before_resolution = legacy.approved.resolution
    rebound, _report = hook.run_rebind_review(
        SimpleNamespace(novel_dir=tmp_path))

    assert len(store.writes) == 1
    assert rebound.candidates == before_candidates
    assert rebound.provenance == before_provenance
    assert rebound.approved is not None
    resolution = rebound.approved.resolution
    assert (resolution.actor, resolution.reason, resolution.subject_sha256) == (
        before_resolution.actor,
        before_resolution.reason,
        before_resolution.subject_sha256,
    )
    assert resolution.basis == before_resolution.basis
    assert resolution.workpack_sha256 == before_resolution.workpack_sha256
    assert resolution.review_identity_sha256 is not None
    assert before_approval is not None
    assert rebound.approved.model_copy(update={
        'resolution': resolution.model_copy(update={
            'review_identity_sha256': None,
        }),
    }) == before_approval
    assert hook.approval_is_current(store, rebound)


def test_stage6_legacy_rebind_refuses_visible_evidence_drift_without_writing(
        monkeypatch, tmp_path) -> None:
    from novel.errors import TerminalState

    legacy = _legacy_stage6_approval(monkeypatch)
    candidate = legacy.candidates[0]
    changed = candidate.model_copy(update={
        'mech': candidate.mech.model_copy(update={
            'quotes_fixed': candidate.mech.quotes_fixed + 1,
        }),
    })
    drifted = legacy.model_copy(update={
        'candidates': [changed, *legacy.candidates[1:]],
    })
    store = _Stage6Store(drifted)
    monkeypatch.setattr(hook, 'ArtifactStore', lambda _ctx: store)
    before = drifted.model_dump(by_alias=True, mode='json')

    with pytest.raises(TerminalState, match='机械证据'):
        hook.run_rebind_review(SimpleNamespace(novel_dir=tmp_path))

    assert store.writes == []
    assert store.art.model_dump(by_alias=True, mode='json') == before


def _json_value(value: Any) -> Any:
    dump = getattr(value, 'model_dump', None)
    if callable(dump):
        return dump(mode='json', by_alias=True)
    if isinstance(value, SimpleNamespace):
        return {key: _json_value(item) for key, item in vars(value).items()}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


class _NameMap(SimpleNamespace):
    def model_copy(self, *, update: dict[str, Any]):
        return _NameMap(**(vars(self) | update))

    def model_dump(self, **_kwargs) -> dict[str, Any]:
        return {key: _json_value(value) for key, value in vars(self).items()}


def _name_map() -> _NameMap:
    decision = NameDecision(
        char_id='char_001', old_canonical='沈月', new_canonical='陆月',
        renamed=True, not_renamed_reason=None,
        aliases=[{'old': '沈姑娘', 'new': '陆姑娘'}],
        reason='新姓避开原作姓氏，名保留人物气质。',
    )
    return _NameMap(
        surname_map={'沈': '陆'},
        characters=[decision],
        extra_subs=[ExtraSub(
            old='沈府', new='陆府', origin='human')],
        subs={'沈姑娘': '陆姑娘', '沈月': '陆月', '沈府': '陆府'},
        kept=['沈大娘'],
        applied=SimpleNamespace(
            chapters={
                '1': SimpleNamespace(counts={'沈月': 2, '沈姑娘': 1}),
                '2': SimpleNamespace(counts={'沈月': 1, '沈府': 3}),
            },
            hook=SimpleNamespace(counts={'沈姑娘': 2}),
        ),
        residual=[Residual(
            stem='沈', where='ch2', hit='沈家门',
            context='她站在沈家门前，仍记得旧日承诺。',
            confidence='high', unit='沈家', suggest='陆家',
        )],
        baselines={'沈': StemBaseline(
            total=10, covered=8, residual=2, ratio=0.8)},
        approved=None,
        book_txt=None,
        judged={'stage5/ch01.json': {'sha1': 'a' * 10}},
        hook_sha1='b' * 10,
        hook_consumed={'present': True, 'candidates_sha1': 'c' * 10},
        sources={'writer_context': {'state': 'fresh'}},
        book=[],
        identity=None,
        provenance={'code': 'artifact-code'},
    )


def _stage7_snapshot_env(monkeypatch):
    rules = {'value': 'rules-stage7'}
    monkeypatch.setattr(rename, '_Upstreams', lambda *_args: object())
    monkeypatch.setattr(rename, '_judge', lambda *_args: [])
    monkeypatch.setattr(
        rename, '_report', lambda *_args, **_kwargs: _Report(node='stage7.approve'))
    monkeypatch.setattr(
        rename, 'compact_approval_check', lambda _report: _compact_check())
    monkeypatch.setattr(
        rename.prov, 'rules_fingerprint', lambda _stage: rules['value'])
    store = SimpleNamespace(ctx=SimpleNamespace(sid='book-7', code='code-7'))
    return store, _name_map(), rules


@pytest.mark.parametrize('caller_node', ['stage0.approve', 'stage7.assemble'])
def test_stage7_workpack_keeps_own_report_and_identity_across_consumers(
        monkeypatch, caller_node) -> None:
    """真实 report builder 的规则分母不能因 API/组书调用上下文而改变。"""
    from novel.prov import FreshnessReport

    report_builder = rename._report
    compact_check = rename.compact_approval_check
    store, art, _rules = _stage7_snapshot_env(monkeypatch)
    monkeypatch.setattr(rename, '_report', report_builder)
    monkeypatch.setattr(rename, 'compact_approval_check', compact_check)
    store.exists = lambda rel: rel == rename.REL
    store.freshness = lambda _rel: FreshnessReport('fresh', 'match')
    art.provenance = SimpleNamespace(model_dump=lambda **_kwargs: {})
    store.ctx.node = 'stage7.approve'
    canonical, canonical_report = rename._approval_workpack_snapshot(store, art)

    store.ctx.node = caller_node
    consumed, consumed_report = rename._approval_workpack_snapshot(store, art)

    assert store.ctx.node == caller_node
    assert canonical_report.node == consumed_report.node == 'stage7.approve'
    assert canonical.check.rules_scanned > 0
    assert consumed.check == canonical.check
    assert consumed.review_identity_sha256 == canonical.review_identity_sha256
    assert consumed.validation_seal_sha256 == canonical.validation_seal_sha256
    assert consumed.workpack_sha256 == canonical.workpack_sha256


def test_stage7_workpack_keeps_real_hits_residual_context_and_stable_subject(
    monkeypatch,
) -> None:
    store, art, rules = _stage7_snapshot_env(monkeypatch)
    checked_subjects = []

    def judge_candidate(_ctx, _store, subject, _up):
        checked_subjects.append(subject)
        return []

    monkeypatch.setattr(rename, '_judge', judge_candidate)
    before, before_report = rename._approval_workpack_snapshot(store, art)
    with pytest.raises(ValidationError, match='disposition'):
        Stage7ApprovalWorkpack.model_validate({
            **before.model_dump(mode='json'), 'disposition': 'suspended',
        })
    public = before.model_dump(mode='json')
    assert before.v == 3
    assert before.review_contract_version == 'stage7-final-review-v4'
    assert public['check_basis'] == 'name_map_without_approval_or_delivery'
    with pytest.raises(ValidationError, match='check_basis'):
        Stage7ApprovalWorkpack.model_validate({**public, 'check_basis': 'current_delivery'})
    assert 'code_version' not in public and 'rules_fingerprint' not in public

    assert before.kept == ['沈大娘'] and before.kept_count == 1
    assert before.replacement_hits == {'沈姑娘': 3, '沈府': 3, '沈月': 3}
    assert before.characters[0].hits == 6
    assert before.residual[0].context == '她站在沈家门前，仍记得旧日承诺。'

    approval = ApprovedNameMap(
        origin='human', actor='reviewer-seven', reason='逐项核验人物与残留',
        subject_sha256=before.decision_subject_sha256,
        workpack_sha256=before.workpack_sha256,
        review_identity_sha256=before.review_identity_sha256,
    )
    approved_art = art.model_copy(update={'approved': approval})
    current, current_report = rename._approval_workpack_snapshot(store, approved_art)
    delivered, delivered_report = rename._approval_workpack_snapshot(
        store, approved_art.model_copy(update={
            'book_txt': {'sha1': 'd' * 40, 'chars': 123, 'chapters': 2},
        }))

    assert len(checked_subjects) == 3
    assert all(subject.approved is None and subject.book_txt is None
               for subject in checked_subjects)
    assert current.check == delivered.check == before.check
    assert current_report.model_dump() == delivered_report.model_dump() == before_report.model_dump()
    assert current.review_identity_sha256 == delivered.review_identity_sha256 == before.review_identity_sha256
    assert current.workpack_sha256 == before.workpack_sha256
    assert delivered.workpack_sha256 == before.workpack_sha256
    assert current.artifact_subject_sha256 == before.artifact_subject_sha256
    assert current.disposition == delivered.disposition == 'already_current'

    legacy, _ = rename._approval_workpack_snapshot(
        store, art.model_copy(update={'approved': approval.model_copy(update={
            'review_identity_sha256': None,
        })}))
    assert legacy.disposition == 'review_required'

    store.ctx.code = 'code-7-next'
    changed_code, _ = rename._approval_workpack_snapshot(store, approved_art)
    assert changed_code.workpack_sha256 != before.workpack_sha256
    assert changed_code.validation_seal_sha256 != before.validation_seal_sha256
    assert changed_code.review_identity_sha256 == before.review_identity_sha256
    assert changed_code.disposition == 'already_current'

    store.ctx.code = 'code-7'
    rules['value'] = 'rules-stage7-next'
    changed_rules, _ = rename._approval_workpack_snapshot(store, approved_art)
    assert changed_rules.workpack_sha256 != before.workpack_sha256
    assert changed_rules.validation_seal_sha256 != before.validation_seal_sha256
    assert changed_rules.review_identity_sha256 == before.review_identity_sha256
    assert changed_rules.disposition == 'already_current'

    rules['value'] = 'rules-stage7'
    changed_artifact, _ = rename._approval_workpack_snapshot(
        store, approved_art.model_copy(update={'kept': ['沈大娘', '沈掌柜']}))
    assert changed_artifact.workpack_sha256 != before.workpack_sha256
    assert changed_artifact.disposition == 'review_required'


def test_stage7_proof_refuses_legacy_or_noncurrent_workpack_approval(
    monkeypatch,
) -> None:
    store, art, _rules = _stage7_snapshot_env(monkeypatch)
    pack, _ = rename._approval_workpack_snapshot(store, art)
    approval = ApprovedNameMap(
        origin='human', actor='reviewer-seven', reason='逐项核验人物证据',
        subject_sha256=pack.decision_subject_sha256,
        workpack_sha256=pack.workpack_sha256,
        review_identity_sha256=pack.review_identity_sha256,
    )
    current = art.model_copy(update={'approved': approval})
    assert rename.approval_proof_sha256(store, current) is not None

    legacy = current.model_copy(update={'approved': approval.model_copy(update={
        'review_identity_sha256': None,
    })})
    assert rename.approval_proof_sha256(store, legacy) is None

    store.ctx.code = 'code-7-next'
    assert rename.approval_proof_sha256(store, current) is not None


def _legacy_stage7_approval(monkeypatch):
    snapshot_store, art, _rules = _stage7_snapshot_env(monkeypatch)
    pack, _report = rename._approval_workpack_snapshot(snapshot_store, art)
    approval = ApprovedNameMap(
        origin='human', actor='legacy-name-reviewer',
        reason='旧契约下逐人核过替换命中、残留与人物决定',
        subject_sha256=pack.decision_subject_sha256,
        workpack_sha256=pack.workpack_sha256,
        review_identity_sha256=None,
    )
    return art.model_copy(update={
        'approved': approval,
        'book_txt': {'sha1': 'd' * 40, 'chars': 321, 'chapters': 2},
    })


def test_stage7_legacy_rebind_refuses_when_old_visible_evidence_was_not_persisted(
        monkeypatch, tmp_path) -> None:
    from novel.errors import TerminalState

    legacy = _legacy_stage7_approval(monkeypatch)
    ctx = SimpleNamespace(novel_dir=tmp_path, sid='book-7', code='code-7')
    store = _Stage7Store(ctx, legacy)
    monkeypatch.setattr(rename, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(
        rename, '_retire_book_txt',
        lambda *_args: pytest.fail('rebind 绝不能退休既有成书'))
    delivered = tmp_path / 'book.txt'
    delivered.write_bytes('既有成书字节\n'.encode('utf-8'))
    before_bytes = delivered.read_bytes()
    before_artifact = legacy.model_dump(mode='json', by_alias=True)

    with pytest.raises(TerminalState, match='immutable workpack / stable evidence digest'):
        rename.run_rebind_review(ctx)

    assert store.writes == []
    assert delivered.read_bytes() == before_bytes
    assert store.art.model_dump(mode='json', by_alias=True) == before_artifact


def test_stage7_legacy_rebind_refuses_decision_subject_drift_without_writing(
        monkeypatch, tmp_path) -> None:
    from novel.errors import TerminalState

    legacy = _legacy_stage7_approval(monkeypatch)
    changed_character = legacy.characters[0].model_copy(update={
        'new_canonical': '顾月',
    })
    drifted = legacy.model_copy(update={
        'characters': [changed_character],
    })
    ctx = SimpleNamespace(novel_dir=tmp_path, sid='book-7', code='code-7')
    store = _Stage7Store(ctx, drifted)
    monkeypatch.setattr(rename, 'ArtifactStore', lambda _ctx: store)
    before = drifted.model_dump(mode='json', by_alias=True)

    with pytest.raises(TerminalState, match='决策主体已变化'):
        rename.run_rebind_review(ctx)

    assert store.writes == []
    assert store.art.model_dump(mode='json', by_alias=True) == before


def test_stage6_approve_rechecks_after_shared_lock_and_never_overwrites_edit(
    monkeypatch, tmp_path,
) -> None:
    """旧 workpack 等锁期间被改稿；批准进锁后读新正式稿并零写入拒绝。"""
    from novel.llm import stage6_hook_runs
    from novel.errors import TerminalState

    snapshot_store, art = _stage6_snapshot_env(monkeypatch)
    old_pack, _ = hook._approval_workpack_snapshot(snapshot_store, art)
    store = _Stage6Store(art)
    entered = threading.Event()

    def store_factory(_ctx):
        entered.set()
        return store

    monkeypatch.setattr(hook, 'ArtifactStore', store_factory)
    outcome: list[BaseException | None] = []

    def approve() -> None:
        try:
            hook.run_approve(
                SimpleNamespace(novel_dir=tmp_path), 1,
                actor='reviewer', reason='核验旧工作包',
                review_token=old_pack.review_token,
            )
        except BaseException as exc:  # thread boundary: carry exact terminal back
            outcome.append(exc)

    with stage6_hook_runs.commit_ownership(tmp_path):
        worker = threading.Thread(target=approve)
        worker.start()
        assert not entered.wait(timeout=0.1), '批准在持锁者释放前穿过了共享提交锁'
        edited = art.candidates[0].model_copy(update={'text': TEXT_A + '（人工改稿）'})
        store.art = art.model_copy(update={
            'candidates': [edited, *art.candidates[1:]],
        })
    worker.join(timeout=5)

    assert len(outcome) == 1 and isinstance(outcome[0], TerminalState)
    assert store.writes == []
    assert store.art.candidates[0].text.endswith('（人工改稿）')


class _Stage7Store:
    def __init__(self, ctx, art) -> None:
        self.ctx = ctx
        self.art = art
        self.writes: list[Any] = []

    def exists(self, _rel: str) -> bool:
        return True

    def read_own(self, _model, _rel: str):
        return self.art, b'raw'

    def write(self, _rel: str, art) -> None:
        self.writes.append(art)
        self.art = art


def test_stage7_approve_rechecks_after_shared_lock_and_never_overwrites_edit(
    monkeypatch, tmp_path,
) -> None:
    """Stage7 同样在共享 rename 锁内读正式稿，旧人物包不能覆盖新编辑。"""
    from novel.llm import stage7_runs
    from novel.errors import TerminalState

    snapshot_store, art, _rules = _stage7_snapshot_env(monkeypatch)
    old_pack, _ = rename._approval_workpack_snapshot(snapshot_store, art)
    ctx = SimpleNamespace(
        novel_dir=tmp_path, sid='book-7', code='code-7')
    store = _Stage7Store(ctx, art)
    entered = threading.Event()

    def store_factory(_ctx):
        entered.set()
        return store

    monkeypatch.setattr(rename, 'ArtifactStore', store_factory)
    outcome: list[BaseException | None] = []

    def approve() -> None:
        try:
            rename.run_approve(
                ctx, actor='reviewer', reason='核验旧工作包',
                review_token=old_pack.review_token,
            )
        except BaseException as exc:
            outcome.append(exc)

    with stage7_runs.commit_ownership(tmp_path):
        worker = threading.Thread(target=approve)
        worker.start()
        assert not entered.wait(timeout=0.1), '批准在持锁者释放前穿过了共享提交锁'
        store.art = art.model_copy(update={'kept': [*art.kept, '沈掌柜']})
    worker.join(timeout=5)

    assert len(outcome) == 1 and isinstance(outcome[0], TerminalState)
    assert store.writes == []
    assert store.art.kept == ['沈大娘', '沈掌柜']
