#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Execution permit：ready 精确复用、blocked 恢复闭集与外部字节身份。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.invariants import (
    EXECUTION_PERMIT_KINDS, FREE_ENTRIES, INBOX_KIND, NODES,
    STAGE5_CLOSED_EXEMPT_FLAGS, VALUED_FLAGS,
)
from contracts.schema.api import (
    ExecutionPermit, InboxView, OperatorAction, OperatorNext,
    OperatorNextDecision,
)
from contracts.schema.orchestration import NovelOverview, OverviewSummary
from novel.cli import exit_codes as X
from novel.cli.main import main
from novel.errors import ArtifactUnreadable, TerminalState, UnsupportedRunMode
from novel.orchestrator import execution_permit as P
from novel.orchestrator import operator as O
from novel.orchestrator import queue as Q
from novel.orchestrator.nodes import EXECUTOR_PROFILED_RUNS
from novel.orchestrator import nodes as NODE_POLICY


SID = 'book-1'


def _overview() -> NovelOverview:
    return NovelOverview(
        sid=SID, title='测试书', dirname=f'平台_测试书_{SID}', stages=[],
        summary=OverviewSummary(
            verdict='pending', text='待继续', detail='下一步待判',
            artifact_count=0, latest_mtime=None),
    )


def _inbox() -> InboxView:
    return InboxView(
        items=[], counts={kind: 0 for kind in INBOX_KIND}, total=0, wontfix=0)


def _next(
    base: Path, *, state: str, node: str | None = None,
    flags: list[str] | None = None, waiting_on: str | None = None,
) -> OperatorNextDecision:
    action = None
    if node is not None:
        action = OperatorAction(
            node=node, flags=flags or [], resume_verdict='run', why='测试盘面',
            waiting_on=waiting_on,
            paid=NODE_POLICY.is_paid(node, flags or []),
            writes=NODE_POLICY.write_effect(node, flags or []),
            action_sha256='c' * 64,
        )
    return OperatorNextDecision(
        base=str(base.resolve()), sid=SID, code_version='code-v1',
        surface_seal='a' * 64, state=state,
        overview=_overview(), inbox=_inbox(), action=action,
    )


def _stable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(P.prov, 'code_version', lambda: 'code-v1')
    monkeypatch.setattr(P, 'read_surface_seal', lambda *_args, **_kwargs: 'a' * 64)


def test_ready_permit_accepts_only_the_exact_current_action(monkeypatch, tmp_path):
    _stable(monkeypatch)
    current = _next(
        tmp_path, state='ready', node='stage2.global', flags=['--renormalize'])

    first = P.permit_from_next(current, 'stage2.global', ['--renormalize'])
    second = P.permit_from_next(current, 'stage2.global', ['--renormalize'])

    assert first == second
    assert first.kind == 'ready'
    assert first.operator_action_sha256 == 'c' * 64
    assert first.permit_sha256 != first.operator_action_sha256
    assert first.flags == ['--renormalize']
    with pytest.raises(UnsupportedRunMode, match='exact node/flags'):
        P.permit_from_next(current, 'stage2.global', [])


def test_blocked_recovery_gets_a_new_permit_not_the_blocker_hash(
        monkeypatch, tmp_path):
    _stable(monkeypatch)
    current = _next(
        tmp_path, state='blocked', node='stage4.design',
        waiting_on=Q.WAITING_ON_STAGE2_REVIEW)

    got = P.permit_from_next(current, 'stage2.global', ['--renormalize'])

    assert got.kind == 'recovery'
    assert got.next_state == 'blocked'
    assert got.waiting_on == Q.WAITING_ON_STAGE2_REVIEW
    assert got.operator_action_sha256 is None
    assert got.permit_sha256 != current.action.action_sha256


@pytest.mark.parametrize(
    'waiting_on',
    [
        Q.WAITING_ON_STAGE4_DESIGN_REVIEW,
        Q.WAITING_ON_STAGE4_BINDING_RESOLUTION,
        Q.WAITING_ON_STAGE4_STRUCTURE_REVIEW,
    ],
)
def test_blueprint_workpack_remains_reachable_for_stage4_gate_bootstrap(
        waiting_on, monkeypatch, tmp_path):
    """Stage2 收口后补 seen.read 时，纯读工作包不能被 Stage4 blocker 锁死。"""
    _stable(monkeypatch)
    current = _next(
        tmp_path, state='blocked', node='stage4.plan', waiting_on=waiting_on)

    got = P.permit_from_next(
        current, 'stage2.blueprint', ['--emit-review-workpack'])

    assert got.kind == 'recovery'
    assert got.paid is False
    assert got.writes == ''
    assert got.waiting_on == waiting_on
    assert got.node == 'stage2.blueprint'
    assert got.flags == ['--emit-review-workpack']


@pytest.mark.parametrize(
    'waiting_on',
    [Q.WAITING_ON_REVIEW, Q.WAITING_ON_STAGE5_CLOSURE],
)
def test_blueprint_workpack_cannot_jump_to_a_stage5_blocker(
        waiting_on, monkeypatch, tmp_path):
    """只补 Stage4 bootstrap 缝，不把只读恢复入口无限放宽到下游。"""
    _stable(monkeypatch)
    current = _next(
        tmp_path, state='blocked', node='stage5.write', waiting_on=waiting_on)

    with pytest.raises(UnsupportedRunMode, match='不能处理当前 blocker'):
        P.permit_from_next(
            current, 'stage2.blueprint', ['--emit-review-workpack'])


def test_pipeline_complete_and_unregistered_paid_recovery_fail_closed(
        monkeypatch, tmp_path):
    _stable(monkeypatch)
    complete = _next(tmp_path, state='pipeline_complete')
    with pytest.raises(UnsupportedRunMode, match='pipeline_complete'):
        P.permit_from_next(complete, 'stage7.rename', ['--recheck'])

    blocked = _next(
        tmp_path, state='blocked', node='stage4.design',
        waiting_on=Q.WAITING_ON_STAGE2_REVIEW)
    with pytest.raises(UnsupportedRunMode, match='未登记'):
        P.permit_from_next(blocked, 'stage2.global', ['--resample'])


@pytest.mark.parametrize(
    ('node', 'flags', 'expected'),
    [
        ('stage4.design', [
            '--replan', '--review-token', 's4rr1_' + 'd' * 64], 'replan'),
        ('stage4.design', [
            '--review-token', 's4rr1_' + 'd' * 64], 'repair'),
    ],
)
def test_paid_stage4_recovery_reuses_the_typed_production_authorizer(
        node, flags, expected, monkeypatch, tmp_path):
    _stable(monkeypatch)
    current = _next(
        tmp_path, state='blocked', node='stage4.plan',
        waiting_on=Q.WAITING_ON_STAGE4_DESIGN_REVIEW)
    calls = []
    monkeypatch.setattr(
        P.approvals, 'resolve_design_action',
        lambda base, sid, sha, *, expected: calls.append(
            (base, sid, sha, expected)) or object())

    got = P.permit_from_next(current, node, flags)

    assert got.paid is True and got.kind == 'recovery'
    assert calls == [(
        str(tmp_path.resolve()), SID,
        's4rr1_' + 'd' * 64, expected,
    )]


def test_external_input_hash_binds_raw_bytes_and_changes_the_permit(
        monkeypatch, tmp_path):
    _stable(monkeypatch)
    novel_dir = tmp_path / f'平台_测试书_{SID}'
    edit = novel_dir / 'stage4' / 'plan_edit.json'
    edit.parent.mkdir(parents=True)
    edit.write_bytes(b'{"value":1}\n')
    current = _next(
        tmp_path, state='blocked', node='stage4.context',
        waiting_on=Q.WAITING_ON_STAGE4_STRUCTURE_REVIEW)

    first = P.permit_from_next(
        current, 'stage4.plan', ['--apply-edit', 'stage4/plan_edit.json'])
    edit.write_bytes(b'{"value":2}\n')
    second = P.permit_from_next(
        current, 'stage4.plan', ['--apply-edit', 'stage4/plan_edit.json'])

    assert len(first.external_inputs) == 1
    bound = first.external_inputs[0]
    assert bound.flag == '--apply-edit'
    assert bound.sha256 == hashlib.sha256(b'{"value":1}\n').hexdigest()
    assert bound.bytes == len(b'{"value":1}\n')
    assert Path(bound.resolved_path) == edit.resolve()
    assert first.permit_sha256 != second.permit_sha256


@pytest.mark.parametrize('flag', ['--validate-edit', '--apply-edit'])
def test_stage4_chapter_edit_binds_book_request_raw_bytes(
        flag, monkeypatch, tmp_path):
    _stable(monkeypatch)
    novel_dir = tmp_path / f'平台_测试书_{SID}'
    edit = novel_dir / 'stage4' / 'blind_chapter_edit.json'
    edit.parent.mkdir(parents=True)
    edit.write_bytes(b'{"review_token":"s4er1_draft"}\n')
    current = _next(
        tmp_path, state='blocked', node='stage4.plan',
        waiting_on=Q.WAITING_ON_STAGE4_DESIGN_REVIEW)

    permit = P.permit_from_next(
        current, 'stage4.design', [flag, str(edit)])

    assert len(permit.external_inputs) == 1
    bound = permit.external_inputs[0]
    assert bound.flag == flag
    assert bound.sha256 == hashlib.sha256(edit.read_bytes()).hexdigest()
    edit.write_bytes(b'{"review_token":"s4er1_changed"}\n')
    with pytest.raises(ArtifactUnreadable, match='外部输入'):
        P.revalidate_active_execution_surface(permit)


def test_stage5_external_edit_is_limited_to_book_or_service_sid(
        monkeypatch, tmp_path):
    _stable(monkeypatch)
    novel_dir = tmp_path / f'平台_测试书_{SID}'
    novel_dir.mkdir()
    outside = tmp_path / 'foreign-stage5-edit.json'
    outside.write_bytes(b'{}\n')
    current = _next(
        tmp_path, state='blocked', node='stage5.write',
        waiting_on=Q.WAITING_ON_INBOX)

    with pytest.raises(UnsupportedRunMode, match='当前书'):
        P.permit_from_next(
            current, 'stage5.write', ['--preview-edit', str(outside)])
    with pytest.raises(UnsupportedRunMode, match='当前书'):
        P.permit_from_next(
            current, 'stage5.write',
            ['--preview-edit', '../foreign-stage5-edit.json'])

    service_edit = tmp_path / '_service' / 'edits' / SID / 'ch01.json'
    service_edit.parent.mkdir(parents=True)
    service_edit.write_bytes(b'{"segments":[]}\n')
    got = P.permit_from_next(
        current, 'stage5.write', ['--preview-edit', str(service_edit)])
    assert Path(got.external_inputs[0].resolved_path) == service_edit.resolve()


@pytest.mark.parametrize(
    ('picked', 'flags', 'match'),
    [
        ({}, ['--revise'], '没有 actionable/UNIQUE'),
        ({1: [['rule', 'ev']]}, ['--revise', '--chapters', '2'], '非空子集'),
    ],
)
def test_stage5_paid_revise_refuses_non_picked_chapters(
        picked, flags, match, monkeypatch, tmp_path):
    _stable(monkeypatch)
    novel_dir = tmp_path / f'平台_测试书_{SID}'
    novel_dir.mkdir()
    current = _next(
        tmp_path, state='blocked', node='stage6.hook',
        waiting_on=Q.WAITING_ON_STAGE5_CLOSURE)
    authorized = []
    monkeypatch.setattr(
        P.approvals, 'authorize_writing',
        lambda *args, **kwargs: authorized.append((args, kwargs)))
    from novel.stages.stage5 import write
    monkeypatch.setattr(
        write, 'revise_targets', lambda _store: (picked, [], 'review-fp'))

    with pytest.raises(UnsupportedRunMode, match=match):
        P.permit_from_next(current, 'stage5.write', flags)
    assert authorized, '必须先复用 writing/current Review 授权口'


def test_stage5_paid_revise_accepts_only_a_nonempty_picked_subset(
        monkeypatch, tmp_path):
    _stable(monkeypatch)
    novel_dir = tmp_path / f'平台_测试书_{SID}'
    novel_dir.mkdir()
    current = _next(
        tmp_path, state='blocked', node='stage6.hook',
        waiting_on=Q.WAITING_ON_STAGE5_CLOSURE)
    monkeypatch.setattr(P.approvals, 'authorize_writing', lambda *_args, **_kwargs: None)
    from novel.stages.stage5 import write
    monkeypatch.setattr(
        write, 'revise_targets',
        lambda _store: ({1: [['rule', 'ev']], 3: [['rule', 'ev']]}, [], 'fp'))

    got = P.permit_from_next(
        current, 'stage5.write', ['--revise', '--chapters', '3'])

    assert got.paid is True and got.flags[-1] == '3'


def test_recovery_cannot_jump_past_the_current_blocker(monkeypatch, tmp_path):
    _stable(monkeypatch)
    current = _next(
        tmp_path, state='blocked', node='stage0.approve',
        waiting_on=Q.WAITING_ON_INBOX)
    with pytest.raises(UnsupportedRunMode, match='位于当前 blocker'):
        P.permit_from_next(current, 'stage7.rename', ['--recheck'])


def test_policy_tables_are_closed_and_paid_rows_are_exactly_profiled():
    assert EXECUTION_PERMIT_KINDS == ('ready', 'recovery')
    keys = [(row.node, row.tokens) for row in P.RECOVERY_RUN_POLICIES]
    assert len(keys) == len(set(keys))
    assert all(row.node in NODES and row.tokens <= set(NODES[row.node].flags)
               for row in P.RECOVERY_RUN_POLICIES)
    paid = {(row.node, row.tokens) for row in P.RECOVERY_RUN_POLICIES if row.paid}
    assert paid == {
        ('stage4.design', frozenset({'--replan', '--review-token'})),
        ('stage4.design', frozenset({'--review-token'})),
        ('stage5.write', frozenset({'--revise'})),
        ('stage5.write', frozenset({'--revise', '--chapters'})),
    }
    assert all(tokens in EXECUTOR_PROFILED_RUNS[node] for node, tokens in paid)
    expected_files = {
        ('stage0.edit', '--apply-edit'),
        ('stage0.edit', '--apply-edit-plan'),
        ('stage0.edit', '--apply-candidate-plan'),
        ('stage2.global', '--apply-edit'),
        ('stage2.global', '--review-decisions'),
        ('stage2.blueprint', '--validate-edit'),
        ('stage2.blueprint', '--apply-edit'),
        ('stage4.design', '--validate-edit'),
        ('stage4.design', '--apply-edit'),
        ('stage4.plan', '--apply-edit'),
        ('stage4.plan', '--apply-binding'),
        ('stage5.write', '--preview-edit'),
        ('stage5.write', '--apply-edit'),
        ('stage5.review', '--retain-pretrim-batch'),
        ('stage6.hook', '--apply-edit'),
        ('stage7.rename', '--apply-edit'),
        ('stage7.rename', '--validate-edit'),
    }
    assert set(P.EXTERNAL_INPUT_POLICIES) == expected_files
    assert all(any(row.node == node and flag in row.tokens
                   for row in P.RECOVERY_RUN_POLICIES)
               for node, flag in expected_files)


def test_legacy_review_rebind_is_one_closed_zero_wire_write_path_per_stage():
    """恢复口在贵贱、主操作、闭合豁免与 permit 表上必须是同一个 token。"""
    expected = {
        'stage6.hook': 'stage6/hook.json',
        'stage7.rename': 'stage7/name_map.json',
    }
    assert '--rebind-review' in FREE_ENTRIES
    assert '--rebind-review' not in VALUED_FLAGS
    assert '--rebind-review' in STAGE5_CLOSED_EXEMPT_FLAGS
    for node, writes in expected.items():
        assert '--rebind-review' in NODES[node].flags
        assert '--rebind-review' in NODE_POLICY._PRIMARY_OPERATIONS[node]
        NODE_POLICY.validate_run(node, ['--rebind-review'])
        assert NODE_POLICY.is_paid(node, ['--rebind-review']) is False
        assert NODE_POLICY.write_effect(node, ['--rebind-review']) == writes
        assert (node, frozenset({'--rebind-review'})) \
            in P.RECOVERY_RUN_POLICY_INDEX


def test_execution_permit_schema_forbids_reusing_blocker_hash():
    with pytest.raises(ValidationError, match='不得复用'):
        ExecutionPermit(
            base='E:/Output', sid=SID, code_version='code-v1',
            surface_seal='a' * 64, next_state='blocked', waiting_on='x',
            kind='recovery', node='stage2.global', flags=['--renormalize'],
            paid=False, writes='stage2/x.json',
            operator_action_sha256='b' * 64, permit_sha256='c' * 64,
        )


def test_operator_permit_captures_one_snapshot_then_uses_the_permit_layer(
        monkeypatch, tmp_path):
    current = _next(tmp_path, state='pipeline_complete')
    calls = []
    monkeypatch.setattr(
        O.paths, 'resolve_novel_dir', lambda _base, _sid: tmp_path)
    monkeypatch.setattr(
        O, 'capture_operator_snapshot',
        lambda base, sid: calls.append(('capture', base, sid)) or 'snapshot')
    monkeypatch.setattr(
        O, '_next_from_snapshot',
        lambda snapshot: calls.append(('next', snapshot)) or current)
    monkeypatch.setattr(
        P, 'permit_from_next',
        lambda got, node, flags: calls.append(('permit', got, node, flags)) or 'ok')

    assert O.permit(tmp_path, SID, 'stage7.rename', ['--recheck']) == 'ok'
    assert calls == [
        ('capture', tmp_path, SID),
        ('next', 'snapshot'),
        ('permit', current, 'stage7.rename', ['--recheck']),
    ]


def test_operator_permit_cli_forwards_every_flag_and_returns_strict_json(
        monkeypatch, tmp_path, capsys):
    _stable(monkeypatch)
    current = _next(
        tmp_path, state='ready', node='stage2.global',
        flags=['--renormalize'])
    minted = P.permit_from_next(
        current, 'stage2.global', ['--renormalize'])
    calls = []
    monkeypatch.setattr(
        O, 'permit',
        lambda base, sid, node, flags: calls.append(
            (base, sid, node, flags)) or minted)

    rc = main([
        'operator', 'permit', SID, 'stage2.global',
        '--base', str(tmp_path), '--json', '--', '--renormalize'])

    assert rc == X.OK
    assert calls == [(str(tmp_path), SID, 'stage2.global', ['--renormalize'])]
    assert json.loads(capsys.readouterr().out) == minted.model_dump(mode='json')


def test_execution_permit_envelope_round_trips_and_rejects_tampering(
        monkeypatch, tmp_path):
    _stable(monkeypatch)
    current = _next(
        tmp_path, state='ready', node='stage2.global',
        flags=['--renormalize'])
    minted = P.permit_from_next(
        current, 'stage2.global', ['--renormalize'])
    env = P.env_with_permit(minted, 1, {})

    assert P.load_permit_envelope(env) == (minted, 1)
    tampered = json.loads(env[P.PERMIT_JSON_ENV])
    tampered['surface_seal'] = 'f' * 64
    env[P.PERMIT_JSON_ENV] = json.dumps(tampered)
    with pytest.raises(TerminalState, match='canonical SHA-256'):
        P.load_permit_envelope(env)
    env = P.env_with_permit(minted, 0, {})
    env[P.STEP_INDEX_ENV] = '01'
    with pytest.raises(TerminalState, match='前导零'):
        P.load_permit_envelope(env)


def test_complete_permit_file_uses_the_same_pure_canonical_parser(
        monkeypatch, tmp_path):
    _stable(monkeypatch)
    current = _next(
        tmp_path, state='ready', node='stage2.global',
        flags=['--renormalize'])
    minted = P.permit_from_next(
        current, 'stage2.global', ['--renormalize'])
    token = tmp_path / 'execution-token.json'
    token.write_text(minted.model_dump_json(), encoding='utf-8')

    assert P.parse_execution_permit(minted.model_dump(mode='json')) == minted
    assert P.load_execution_permit_file(token) == minted

    tampered = minted.model_dump(mode='json')
    tampered['writes'] = '伪造写面'
    token.write_text(json.dumps(tampered), encoding='utf-8')
    with pytest.raises(TerminalState, match='canonical SHA-256'):
        P.load_execution_permit_file(token)


def test_ready_operator_next_wrapper_is_the_opaque_execution_token_file(
        monkeypatch, tmp_path):
    """调用方原样保存 next JSON；loader 提取 token，不要求人工搬运 SHA。"""
    _stable(monkeypatch)
    decision = _next(
        tmp_path, state='ready', node='stage2.global',
        flags=['--renormalize'])
    minted = P.permit_from_next(
        decision, 'stage2.global', ['--renormalize'])
    wrapper = OperatorNext(
        **decision.model_dump(mode='python'), execution_token=minted)
    token = tmp_path / 'operator-next.json'
    token.write_text(wrapper.model_dump_json(), encoding='utf-8')

    assert P.load_execution_permit_file(token) == minted

    tampered = wrapper.model_dump(mode='json')
    tampered['execution_token']['writes'] = '伪造写面'
    token.write_text(json.dumps(tampered), encoding='utf-8')
    with pytest.raises(TerminalState, match='无效'):
        P.load_execution_permit_file(token)


def test_claim_side_revalidation_is_lightweight_and_binds_external_bytes(
        monkeypatch, tmp_path):
    _stable(monkeypatch)
    novel_dir = tmp_path / f'平台_测试书_{SID}'
    novel_dir.mkdir()
    edit = tmp_path / '_service' / 'edits' / SID / 'relations.json'
    edit.parent.mkdir(parents=True)
    edit.write_bytes(b'{"chapters":[]}\n')
    current = _next(
        tmp_path, state='blocked', node='stage4.design',
        waiting_on=Q.WAITING_ON_STAGE2_REVIEW)
    minted = P.permit_from_next(
        current, 'stage2.blueprint', ['--apply-edit', str(edit)])

    # 本口只有 registry/code/seal/input CAS，没有 next/queue/check-only 参数。
    P.revalidate_active_execution_surface(
        minted, base=tmp_path, sid=SID, node='stage2.blueprint',
        flags=['--apply-edit', str(edit)])

    edit.write_bytes(b'{"chapters":[1]}\n')
    with pytest.raises(ArtifactUnreadable, match='外部输入'):
        P.revalidate_active_execution_surface(minted)


def test_claim_side_revalidation_rejects_active_surface_drift(
        monkeypatch, tmp_path):
    _stable(monkeypatch)
    current = _next(
        tmp_path, state='ready', node='stage2.global',
        flags=['--renormalize'])
    minted = P.permit_from_next(
        current, 'stage2.global', ['--renormalize'])
    monkeypatch.setattr(P, 'read_surface_seal', lambda *_a, **_k: 'b' * 64)

    with pytest.raises(ArtifactUnreadable, match='active artifact'):
        P.revalidate_active_execution_surface(minted)


def test_stage5_trim_batch_manifest_is_bound_only_in_book_or_service_edit(
        monkeypatch, tmp_path):
    _stable(monkeypatch)
    novel_dir = tmp_path / f'平台_测试书_{SID}'
    novel_dir.mkdir()
    current = _next(
        tmp_path, state='blocked', node='stage6.hook',
        waiting_on=Q.WAITING_ON_STAGE5_CLOSURE)
    outside = tmp_path / 'foreign-trim-batch.json'
    outside.write_bytes(b'{}\n')
    with pytest.raises(UnsupportedRunMode, match='当前书'):
        P.permit_from_next(
            current, 'stage5.review',
            ['--retain-pretrim-batch', str(outside)])

    service = tmp_path / '_service' / 'edits' / SID / 'trim-batch.json'
    service.parent.mkdir(parents=True)
    service.write_bytes(b'{"items":[]}\n')
    got = P.permit_from_next(
        current, 'stage5.review',
        ['--retain-pretrim-batch', str(service)])
    assert Path(got.external_inputs[0].resolved_path) == service.resolve()


def test_stage2_review_decisions_bind_service_edit_but_reject_scratch(
        monkeypatch, tmp_path):
    """Reviewer 输入复用正式 service edit；scratch 不能变成第三种 producer 路径。"""
    _stable(monkeypatch)
    novel_dir = tmp_path / f'平台_测试书_{SID}'
    novel_dir.mkdir()
    current = _next(
        tmp_path, state='blocked', node='stage4.design',
        waiting_on=Q.WAITING_ON_STAGE2_REVIEW)

    scratch = (
        tmp_path / '_service' / 'scratch' / SID / 'epoch-1'
        / 'reviewer-node-input.json')
    scratch.parent.mkdir(parents=True)
    scratch.write_bytes(b'{"_schema_version":2}\n')
    with pytest.raises(UnsupportedRunMode, match='当前书'):
        P.permit_from_next(
            current, 'stage2.global',
            ['--review-decisions', str(scratch)])

    other_sid = (
        tmp_path / '_service' / 'edits' / 'book-2'
        / 'global-review.json')
    other_sid.parent.mkdir(parents=True)
    other_sid.write_bytes(b'{"_schema_version":2}\n')
    with pytest.raises(UnsupportedRunMode, match='当前书'):
        P.permit_from_next(
            current, 'stage2.global',
            ['--review-decisions', str(other_sid)])

    service = (
        tmp_path / '_service' / 'edits' / SID
        / 'global-review.json')
    service.parent.mkdir(parents=True)
    service.write_bytes(b'{"_schema_version":2}\n')
    got = P.permit_from_next(
        current, 'stage2.global',
        ['--review-decisions', str(service)])

    assert Path(got.external_inputs[0].resolved_path) == service.resolve()
    assert got.external_inputs[0].sha256 == hashlib.sha256(
        service.read_bytes()).hexdigest()


def test_stage2_global_edit_uses_the_same_sid_scoped_service_root(
        monkeypatch, tmp_path):
    _stable(monkeypatch)
    (tmp_path / f'平台_测试书_{SID}').mkdir()
    current = _next(
        tmp_path, state='blocked', node='stage4.design',
        waiting_on=Q.WAITING_ON_STAGE2_REVIEW)
    edit = tmp_path / '_service' / 'edits' / SID / 'global-edit.json'
    edit.parent.mkdir(parents=True)
    edit.write_bytes(b'{"_schema_version":3}\n')

    got = P.permit_from_next(
        current, 'stage2.global', ['--apply-edit', str(edit)])
    assert Path(got.external_inputs[0].resolved_path) == edit.resolve()

    scratch = (
        tmp_path / '_service' / 'scratch' / SID / 'epoch-1'
        / 'global-edit.json')
    scratch.parent.mkdir(parents=True)
    scratch.write_bytes(edit.read_bytes())
    with pytest.raises(UnsupportedRunMode, match='当前书'):
        P.permit_from_next(
            current, 'stage2.global', ['--apply-edit', str(scratch)])

    sibling = tmp_path / '_service' / 'edits' / 'other' / 'global-edit.json'
    sibling.parent.mkdir(parents=True)
    sibling.write_bytes(edit.read_bytes())
    with pytest.raises(UnsupportedRunMode, match='当前书'):
        P.permit_from_next(
            current, 'stage2.global', ['--apply-edit', str(sibling)])
