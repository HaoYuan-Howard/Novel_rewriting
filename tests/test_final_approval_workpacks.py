#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage6/7 紧凑终审包：稳定主体、真实证据与精确幂等。"""
from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest

from contracts.schema.api import CompactApprovalCheck
from contracts.schema.stage6 import ApprovedHook, HookApprovalResolution
from contracts.schema.stage7 import (
    ApprovedNameMap, ExtraSub, NameDecision, Residual, StemBaseline,
)
from novel.stages.stage6 import hook, semantic
from novel.stages.stage7 import rename
from tests.test_stage6_semantic import TEXT_A, TEXT_B, TEXT_C
from tests.test_stage6_style_ab_review import _hook


def _compact_check() -> CompactApprovalCheck:
    return CompactApprovalCheck(
        rules_scanned=0,
        counts={'ok': 0, 'hit': 0, 'unver': 0, 'skipped': 0},
        provenance={'freshness': 'fresh', 'reason': 'match'},
    )


def _stage6_snapshot_env(monkeypatch):
    art = _hook([TEXT_A, TEXT_B, TEXT_C])
    monkeypatch.setattr(
        semantic, 'current_assessments',
        lambda _store, current: {
            semantic.text_sha1(candidate.text): semantic.CandidateAssessment(
                state='unreviewed', reason='合成：尚无当前语义审判')
            for candidate in current.candidates
        })
    monkeypatch.setattr(
        semantic, 'current_judge_rankings', lambda _store, _art: None)
    monkeypatch.setattr(hook, '_Upstreams', lambda *_args: object())
    monkeypatch.setattr(hook, '_judge_artifact', lambda *_args: [])
    monkeypatch.setattr(
        hook, '_report', lambda *_args, **_kwargs: SimpleNamespace())
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
    before, _ = hook._approval_workpack_snapshot(store, art)
    candidate = art.candidates[0]
    resolution = HookApprovalResolution(
        actor='reviewer-a', reason='逐项核验 A/B 与机械证据',
        subject_sha256=before.candidates[0].subject_sha256,
        basis='human_verification',
        workpack_sha256=before.workpack_sha256,
    )
    approved = ApprovedHook(
        slot='c1', text=candidate.text, origin='human',
        mech=candidate.mech, findings=list(candidate.findings),
        resolution=resolution,
    )

    current, _ = hook._approval_workpack_snapshot(
        store, art.model_copy(update={'approved': approved}))

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

    legacy_resolution = resolution.model_copy(update={'workpack_sha256': None})
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
    assert changed_code.disposition == 'review_required'


def test_stage6_workpack_pins_its_report_node_when_rechecked_by_stage7(
    monkeypatch,
) -> None:
    """下游消费闸重算 Stage6 工作包时，不得拿 Stage7 判据表解释 hook 结论。"""
    store, art = _stage6_snapshot_env(monkeypatch)
    store.ctx.node = 'stage7.rename'
    seen: dict[str, str | None] = {}

    def capture_report(*_args, report_node=None, **_kwargs):
        seen['report_node'] = report_node
        return SimpleNamespace()

    monkeypatch.setattr(hook, '_report', capture_report)

    hook._approval_workpack_snapshot(store, art)

    assert seen == {'report_node': 'stage6.approve'}


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
        basis='human_verification',
        workpack_sha256=review_pack.workpack_sha256,
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
        lambda *_args, **_kwargs: (
            semantic.CandidateAssessment(
                state='unreviewed', reason='人工终审'),
            'human_verification',
        ))
    monkeypatch.setattr(hook, '_Upstreams', lambda *_args: object())
    monkeypatch.setattr(hook, '_judge_artifact', lambda *_args: [])
    monkeypatch.setattr(hook, '_report', lambda *_args: report)

    common = {
        'pick': 1,
        'actor': 'reviewer-a',
        'subject_sha256': resolution.subject_sha256,
        'workpack_sha256': current_pack.workpack_sha256,
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
        hook_consumed={'present': True, 'approved_text_sha1': 'c' * 10},
        sources={'writer_context': {'state': 'fresh'}},
        book=[],
        identity=None,
        provenance={'code': 'artifact-code'},
    )


def _stage7_snapshot_env(monkeypatch):
    rules = {'value': 'rules-stage7'}
    monkeypatch.setattr(rename, '_Upstreams', lambda *_args: object())
    monkeypatch.setattr(rename, '_judge', lambda *_args: [])
    monkeypatch.setattr(rename, '_report', lambda *_args: SimpleNamespace())
    monkeypatch.setattr(
        rename, 'compact_approval_check', lambda _report: _compact_check())
    monkeypatch.setattr(
        rename.prov, 'rules_fingerprint', lambda _stage: rules['value'])
    store = SimpleNamespace(ctx=SimpleNamespace(sid='book-7', code='code-7'))
    return store, _name_map(), rules


def test_stage7_workpack_keeps_real_hits_residual_context_and_stable_subject(
    monkeypatch,
) -> None:
    store, art, rules = _stage7_snapshot_env(monkeypatch)
    before, _ = rename._approval_workpack_snapshot(store, art)

    assert before.kept == ['沈大娘'] and before.kept_count == 1
    assert before.replacement_hits == {'沈姑娘': 3, '沈府': 3, '沈月': 3}
    assert before.characters[0].hits == 6
    assert before.residual[0].context == '她站在沈家门前，仍记得旧日承诺。'

    approval = ApprovedNameMap(
        origin='human', actor='reviewer-seven', reason='逐项核验人物与残留',
        subject_sha256=before.decision_subject_sha256,
        workpack_sha256=before.workpack_sha256,
    )
    approved_art = art.model_copy(update={'approved': approval})
    current, _ = rename._approval_workpack_snapshot(store, approved_art)
    delivered, _ = rename._approval_workpack_snapshot(
        store, approved_art.model_copy(update={
            'book_txt': {'sha1': 'd' * 40, 'chars': 123, 'chapters': 2},
        }))

    assert current.workpack_sha256 == before.workpack_sha256
    assert delivered.workpack_sha256 == before.workpack_sha256
    assert current.artifact_subject_sha256 == before.artifact_subject_sha256
    assert current.disposition == delivered.disposition == 'already_current'

    legacy, _ = rename._approval_workpack_snapshot(
        store, art.model_copy(update={'approved': approval.model_copy(update={
            'workpack_sha256': None,
        })}))
    assert legacy.disposition == 'review_required'

    store.ctx.code = 'code-7-next'
    changed_code, _ = rename._approval_workpack_snapshot(store, approved_art)
    assert changed_code.workpack_sha256 != before.workpack_sha256
    assert changed_code.disposition == 'review_required'

    store.ctx.code = 'code-7'
    rules['value'] = 'rules-stage7-next'
    changed_rules, _ = rename._approval_workpack_snapshot(store, approved_art)
    assert changed_rules.workpack_sha256 != before.workpack_sha256
    assert changed_rules.disposition == 'review_required'

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
    )
    current = art.model_copy(update={'approved': approval})
    assert rename.approval_proof_sha256(store, current) is not None

    legacy = current.model_copy(update={'approved': approval.model_copy(update={
        'workpack_sha256': None,
    })})
    assert rename.approval_proof_sha256(store, legacy) is None

    store.ctx.code = 'code-7-next'
    assert rename.approval_proof_sha256(store, current) is None


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
                subject_sha256=old_pack.candidates[0].subject_sha256,
                workpack_sha256=old_pack.workpack_sha256,
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
                subject_sha256=old_pack.decision_subject_sha256,
                workpack_sha256=old_pack.workpack_sha256,
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
