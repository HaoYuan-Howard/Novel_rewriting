#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage7 紧凑终审包：稳定主体、真实证据与精确幂等。

⚠ Stage6 三候选定稿工作包（`Stage6ApprovalWorkpack`／`hook._approval_workpack_snapshot`
／`run_approve`／`run_rebind_review` 一族）已随 Stage6 整层退役（F8b）：新链 Hook
单输出、无候选改选面，本文件的 Stage6 反例随旧 producer 一并注销；Stage7 人名表
工作包在旧链注销前继续由生产者现役语义覆盖。
"""
from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from contracts.schema.api import (
    CompactApprovalCheck, Stage7ApprovalWorkpack,
)
from contracts.schema.stage7 import (
    ApprovedNameMap, ExtraSub, NameDecision, Residual, StemBaseline,
)
from novel.stages.stage7 import rename


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
        rename, '_report', lambda *_args, **_kwargs: _Report(node='stage7.rename'))
    monkeypatch.setattr(
        rename, 'compact_approval_check', lambda _report: _compact_check())
    monkeypatch.setattr(
        rename.prov, 'rules_fingerprint', lambda _stage: rules['value'])
    store = SimpleNamespace(ctx=SimpleNamespace(sid='book-7', code='code-7'))
    return store, _name_map(), rules


@pytest.mark.parametrize('caller_node', ['stage0.approve', 'stage7.assemble'])
def test_stage7_workpack_keeps_own_report_and_identity_across_consumers(
        monkeypatch, caller_node) -> None:
    """报告节点与规则分母不能因 API/组书调用上下文而改变。

    ⚠ 原变体走**真实** report builder 证规则分母稳定；`stage7.rename` 节点随
    旧链退役（F8a）后 builder 对该节点拒绝（`RefusedByStructure`），真实 builder
    变体随旧 producer 一并注销，本处保留桩化报告下的同一反例：调用方 node 不得
    改变工作包的报告节点与三枚身份 hash。
    """
    store, art, _rules = _stage7_snapshot_env(monkeypatch)
    store.ctx.node = 'stage7.rename'
    canonical, canonical_report = rename._approval_workpack_snapshot(store, art)

    store.ctx.node = caller_node
    consumed, consumed_report = rename._approval_workpack_snapshot(store, art)

    assert store.ctx.node == caller_node
    assert canonical_report.node == consumed_report.node == 'stage7.rename'
    assert canonical.check.rules_scanned == 0
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
