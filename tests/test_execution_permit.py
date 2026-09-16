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
from contracts.schema.stage5 import ReviewerRewriteExecutionState
from novel import paths
from novel import exit_codes as X
from novel.cli.main import main
from novel.errors import ArtifactUnreadable, TerminalState, UnsupportedRunMode
from novel.orchestrator import execution_permit as P
from novel.orchestrator import operator as O
from novel.orchestrator import queue as Q
from novel.orchestrator.nodes import EXECUTOR_PROFILED_RUNS
from novel.orchestrator import nodes as NODE_POLICY
from novel.llm import stage4_runs


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


def _stage4_repair_journal(source_run: str) -> dict[str, object]:
    """只造 permit 所需的真实 v3 envelope；业务 contents 留给 producer 复验。"""
    record: dict[str, object] = {
        field: None
        for field in stage4_runs._DESIGN_REPAIR_AGGREGATE_FIELDS  # noqa: SLF001
    }
    record.update({
        'version': stage4_runs._DESIGN_REPAIR_AGGREGATE_VERSION,  # noqa: SLF001
        'kind': stage4_runs._DESIGN_REPAIR_AGGREGATE_KIND,  # noqa: SLF001
        'state': 'prepared',
        'run_id': source_run,
        'sid': SID,
        'node': 'stage4.design',
    })
    record['commit_sha256'] = stage4_runs._design_repair_aggregate_hash(  # noqa: SLF001
        record)
    return record


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
        waiting_on=Q.WAITING_ON_INBOX)

    got = P.permit_from_next(current, 'stage2.global', ['--renormalize'])

    assert got.kind == 'recovery'
    assert got.next_state == 'blocked'
    assert got.waiting_on == Q.WAITING_ON_INBOX
    assert got.operator_action_sha256 is None
    assert got.permit_sha256 != current.action.action_sha256


def test_blueprint_workpack_recovery_survives_a_generic_downstream_blocker(
        monkeypatch, tmp_path):
    """Stage4 人工闸 blocker 已退役；同族纯读工作包只按通用 blocker 恢复。"""
    _stable(monkeypatch)
    current = _next(
        tmp_path, state='blocked', node='stage4.plan',
        waiting_on=Q.WAITING_ON_INBOX)

    got = P.permit_from_next(
        current, 'stage2.blueprint', ['--emit-edit-template'])

    assert got.kind == 'recovery'
    assert got.paid is False
    assert got.writes == ''
    assert got.waiting_on == Q.WAITING_ON_INBOX
    assert got.node == 'stage2.blueprint'
    assert got.flags == ['--emit-edit-template']


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
            current, 'stage2.blueprint', ['--emit-edit-template'])


def test_pipeline_complete_and_unregistered_paid_recovery_fail_closed(
        monkeypatch, tmp_path):
    _stable(monkeypatch)
    complete = _next(tmp_path, state='pipeline_complete')
    with pytest.raises(UnsupportedRunMode, match='pipeline_complete'):
        P.permit_from_next(complete, 'stage7.rename', ['--recheck'])

    blocked = _next(
        tmp_path, state='blocked', node='stage4.design',
        waiting_on=Q.WAITING_ON_INBOX)
    with pytest.raises(UnsupportedRunMode, match='未登记'):
        P.permit_from_next(blocked, 'stage2.global', ['--resample'])


def test_retired_stage4_typed_recovery_flags_are_unregistered(
        monkeypatch, tmp_path):
    """人工闸退役后 design 的 review-token 恢复路整体注销，不允许任何 paid 恢复。"""
    _stable(monkeypatch)
    current = _next(
        tmp_path, state='blocked', node='stage4.design',
        waiting_on=Q.WAITING_ON_INBOX)
    for flags in (
        ['--replan', '--review-token', 's4rr1_' + 'd' * 64],
        ['--review-token', 's4rr1_' + 'd' * 64],
    ):
        with pytest.raises(UnsupportedRunMode):
            P.permit_from_next(current, 'stage4.design', flags)


def test_external_input_hash_binds_raw_bytes_and_changes_the_permit(
        monkeypatch, tmp_path):
    _stable(monkeypatch)
    novel_dir = tmp_path / f'平台_测试书_{SID}'
    edit = novel_dir / 'stage4' / 'plan_edit.json'
    edit.parent.mkdir(parents=True)
    edit.write_bytes(b'{"value":1}\n')
    current = _next(
        tmp_path, state='blocked', node='stage4.plan',
        waiting_on=Q.WAITING_ON_INBOX)

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


def test_stage4_plan_edit_binds_book_request_raw_bytes(
        monkeypatch, tmp_path):
    """permit 只绑路径字符串不够：外部编辑文件必须绑原始字节。"""
    _stable(monkeypatch)
    novel_dir = tmp_path / f'平台_测试书_{SID}'
    edit = novel_dir / 'stage4' / 'plan_edit.json'
    edit.parent.mkdir(parents=True)
    edit.write_bytes(b'{"value":1}\n')
    current = _next(
        tmp_path, state='blocked', node='stage4.plan',
        waiting_on=Q.WAITING_ON_INBOX)

    permit = P.permit_from_next(
        current, 'stage4.plan', ['--apply-edit', str(edit)])

    assert len(permit.external_inputs) == 1
    bound = permit.external_inputs[0]
    assert bound.flag == '--apply-edit'
    assert bound.sha256 == hashlib.sha256(edit.read_bytes()).hexdigest()
    edit.write_bytes(b'{"value":2}\n')
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
    # stage0.approve 已退役（2026-09-15）：用现役早层节点钉同一条「不得越闸」。
    current = _next(
        tmp_path, state='blocked', node='stage1.partition',
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
    # stage4.design 的人工 typed 恢复动作已随四道闸退役（2026-09-15 全自动
    # 裁决）；--replan 只作为普通付费 todo 经 ready permit 执行。
    assert paid == {
        ('stage5.write', frozenset({'--revise'})),
        ('stage5.write', frozenset({'--revise', '--chapters'})),
        ('stage5.write', frozenset({'--reviewer-rewrite'})),
    }
    assert all(tokens in EXECUTOR_PROFILED_RUNS[node] for node, tokens in paid)
    expected_files = {
        ('stage2.global', '--apply-edit'),
        ('stage2.blueprint', '--validate-edit'),
        ('stage2.blueprint', '--apply-edit'),
        ('stage4.design', '--recover-repair-aggregate'),
        ('stage4.plan', '--apply-edit'),
        ('stage4.plan', '--apply-binding'),
        ('stage5.write', '--preview-edit'),
        ('stage5.write', '--apply-edit'),
        # （stage5.review 的 trim manifest 外部输入已随批次 B3 退役。）
        ('stage5.write', '--reviewer-rewrite'),
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
        waiting_on=Q.WAITING_ON_INBOX)
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


def test_retired_stage5_trim_manifest_recovery_combos_are_rejected(
        monkeypatch, tmp_path):
    """批次 B3：trim 三选一恢复面退役，flag 组合不再有任何 policy。"""
    _stable(monkeypatch)
    novel_dir = tmp_path / f'平台_测试书_{SID}'
    novel_dir.mkdir()
    current = _next(
        tmp_path, state='blocked', node='stage6.hook',
        waiting_on=Q.WAITING_ON_STAGE5_CLOSURE)
    for flags in (
        ['--retain-pretrim-batch', 'batch.json'],
        ['--retain-pretrim', '--chapters', '2', '--actor', 'a', '--reason', 'r'],
        ['--request-reviewer-rewrite', 'reviewer-input.json'],
    ):
        # validate_run 先按 NODES flag 表拒收（trim 三选一 flag 已退役）。
        with pytest.raises(UnsupportedRunMode, match='不接受'):
            P.permit_from_next(current, 'stage5.review', flags)


def test_stage5_prepared_rewrite_permit_binds_target_wal_and_request_raw_bytes(
        monkeypatch, tmp_path):
    """prepared recovery 逐字绑定旧 target WAL；不能换 run 重建第二份。"""
    _stable(monkeypatch)
    novel_dir = tmp_path / f'平台_测试书_{SID}'
    request_path = novel_dir / 'stage5' / 'reviewer_rewrite_request.json'
    request_path.parent.mkdir(parents=True)
    request_raw = b'{"typed":"request"}\n'
    request_path.write_bytes(request_raw)
    flags = [
        '--recover-reviewer-rewrite-prepared', 'source-run',
        '--reviewer-rewrite-request-sha256', 'a' * 64,
        '--reviewer-rewrite-recovery-target-run-id', 'target-run',
        '--reviewer-rewrite-recovery-journal-sha256', 'b' * 64,
    ]
    current = _next(
        tmp_path, state='blocked', node='stage5.write', flags=flags,
        waiting_on=Q.WAITING_ON_STAGE5_REVIEWER_REWRITE)
    from novel.stages.stage5 import reviewer_rewrite
    from novel.orchestrator import state as state_module

    execution = ReviewerRewriteExecutionState(
        request_rel='stage5/reviewer_rewrite_request.json',
        request_file_sha256=hashlib.sha256(request_raw).hexdigest(),
        request_sha256='a' * 64,
        target_ordinal=2,
        state='recoverable_prepared',
        source_run_id='source-run',
        checkpoint_sha256='c' * 64,
        journal_sha256='d' * 64,
        recovery_target_run_id='target-run',
        recovery_journal_sha256='b' * 64,
    )
    monkeypatch.setattr(P.paths, 'resolve_novel_dir', lambda *_args: novel_dir)
    monkeypatch.setattr(state_module, 'readonly_ctx', lambda *_args: object())
    monkeypatch.setattr(
        reviewer_rewrite, 'reviewer_rewrite_execution_state',
        lambda _ctx: execution)

    permit = P.permit_from_next(current, 'stage5.write', flags)

    assert permit.paid is False
    assert permit.flags == flags
    [external] = permit.external_inputs
    assert external.flag == '--reviewer-rewrite-request-sha256'
    assert external.sha256 == hashlib.sha256(request_raw).hexdigest()
    P.revalidate_active_execution_surface(
        permit, base=tmp_path, sid=SID,
        node='stage5.write', flags=flags)

    request_path.write_bytes(b'{"typed":"changed"}\n')
    with pytest.raises(ArtifactUnreadable, match='外部输入'):
        P.revalidate_active_execution_surface(
            permit, base=tmp_path, sid=SID,
            node='stage5.write', flags=flags)
    request_path.write_bytes(request_raw)

    execution = execution.model_copy(update={
        'recovery_journal_sha256': 'e' * 64,
    })
    with pytest.raises(UnsupportedRunMode, match='execution state 已变化'):
        P.permit_from_next(current, 'stage5.write', flags)


def test_stage2_edit_commit_permit_recovers_before_canonical_input_exists(
        monkeypatch, tmp_path):
    """WAL 是该窗的输入事实；尚未生成的 canonical 是 target，不是前置。"""
    _stable(monkeypatch)
    current = _next(
        tmp_path, state='blocked', node='stage2.global',
        flags=['--apply-edit', 'stage2/global_edit.json'],
        waiting_on=Q.WAITING_ON_STAGE2_EDIT_COMMIT)

    got = P.permit_from_next(
        current, 'stage2.global',
        ['--apply-edit', 'stage2/global_edit.json'])

    assert got.external_inputs == []
    P.revalidate_active_execution_surface(
        got, base=tmp_path, sid=SID, node='stage2.global',
        flags=['--apply-edit', 'stage2/global_edit.json'])


def test_stage4_repair_commit_permit_binds_exact_source_journal_bytes(
        monkeypatch, tmp_path):
    _stable(monkeypatch)
    novel_dir = tmp_path / f'平台_测试书_{SID}'
    novel_dir.mkdir()
    source_run = 'stage4-source-run'
    journal_path = (
        paths.stage4_run_path(
            novel_dir, source_run, 'stage4.design')
        / stage4_runs.DESIGN_REPAIR_AGGREGATE_COMMIT_FILE)
    journal_path.parent.mkdir(parents=True)
    journal = _stage4_repair_journal(source_run)
    commit_sha = str(journal['commit_sha256'])
    journal_path.write_text(
        json.dumps(journal, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    journal_sha = hashlib.sha256(journal_path.read_bytes()).hexdigest()
    exact = [
        '--recover-repair-aggregate', source_run,
        '--source-journal-sha256', journal_sha,
        '--source-commit-sha256', commit_sha,
    ]
    current = _next(
        tmp_path, state='blocked', node='stage4.design', flags=exact,
        waiting_on=Q.WAITING_ON_STAGE4_DESIGN_REPAIR_COMMIT)

    permit = P.permit_from_next(current, 'stage4.design', exact)

    assert permit.kind == 'recovery'
    assert permit.paid is False
    assert permit.flags == exact
    assert len(permit.external_inputs) == 1
    assert Path(permit.external_inputs[0].resolved_path) == journal_path.resolve()
    assert permit.external_inputs[0].sha256 == journal_sha
    P.revalidate_active_execution_surface(
        permit, base=tmp_path, sid=SID,
        node='stage4.design', flags=exact)

    journal_path.write_bytes(journal_path.read_bytes() + b'\n')
    with pytest.raises(ArtifactUnreadable, match='外部输入|SHA'):
        P.revalidate_active_execution_surface(
            permit, base=tmp_path, sid=SID,
            node='stage4.design', flags=exact)


def test_stage4_repair_commit_permit_refuses_stale_journal_self_hash(
        monkeypatch, tmp_path):
    """raw flag 与字段自报都匹配，也不能放过内部已篡改 journal。"""
    _stable(monkeypatch)
    novel_dir = tmp_path / f'平台_测试书_{SID}'
    novel_dir.mkdir()
    source_run = 'stage4-source-run'
    journal_path = (
        paths.stage4_run_path(
            novel_dir, source_run, 'stage4.design')
        / stage4_runs.DESIGN_REPAIR_AGGREGATE_COMMIT_FILE)
    journal_path.parent.mkdir(parents=True)
    journal = _stage4_repair_journal(source_run)
    commit_sha = str(journal['commit_sha256'])
    journal['primary_artifact'] = 'stage4/tampered.json'
    journal_path.write_text(
        json.dumps(journal, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    journal_sha = hashlib.sha256(journal_path.read_bytes()).hexdigest()
    exact = [
        '--recover-repair-aggregate', source_run,
        '--source-journal-sha256', journal_sha,
        '--source-commit-sha256', commit_sha,
    ]
    current = _next(
        tmp_path, state='blocked', node='stage4.design', flags=exact,
        waiting_on=Q.WAITING_ON_STAGE4_DESIGN_REPAIR_COMMIT)

    with pytest.raises(ArtifactUnreadable, match='哈希|SHA'):
        P.permit_from_next(current, 'stage4.design', exact)


@pytest.mark.parametrize('field', ['source', 'journal', 'commit'])
def test_stage4_repair_commit_permit_refuses_non_exact_operator_identity(
        field, monkeypatch, tmp_path):
    _stable(monkeypatch)
    novel_dir = tmp_path / f'平台_测试书_{SID}'
    novel_dir.mkdir()
    source_run = 'stage4-source-run'
    journal_path = (
        paths.stage4_run_path(
            novel_dir, source_run, 'stage4.design')
        / stage4_runs.DESIGN_REPAIR_AGGREGATE_COMMIT_FILE)
    journal_path.parent.mkdir(parents=True)
    journal = _stage4_repair_journal(source_run)
    commit_sha = str(journal['commit_sha256'])
    journal_path.write_text(
        json.dumps(journal, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    journal_sha = hashlib.sha256(journal_path.read_bytes()).hexdigest()
    exact = [
        '--recover-repair-aggregate', source_run,
        '--source-journal-sha256', journal_sha,
        '--source-commit-sha256', commit_sha,
    ]
    changed = list(exact)
    index = {'source': 1, 'journal': 3, 'commit': 5}[field]
    changed[index] = (
        'other-source-run' if field == 'source' else 'd' * 64)
    current = _next(
        tmp_path, state='blocked', node='stage4.design', flags=exact,
        waiting_on=Q.WAITING_ON_STAGE4_DESIGN_REPAIR_COMMIT)

    with pytest.raises(UnsupportedRunMode, match='逐字重放|不能处理'):
        P.permit_from_next(current, 'stage4.design', changed)


@pytest.mark.parametrize('flags', [
    ['--renormalize'],
    ['--revalidate', '--actor', 'human:reviewer', '--reason', 'x'],
    ['--apply-edit', 'stage2/other_global_edit.json'],
    ['--apply-edit', '_service/edits/book-1/global-edit.json'],
])
def test_stage2_edit_commit_permit_only_accepts_exact_canonical_apply(
        flags, monkeypatch, tmp_path):
    _stable(monkeypatch)
    current = _next(
        tmp_path, state='blocked', node='stage2.global',
        flags=['--apply-edit', 'stage2/global_edit.json'],
        waiting_on=Q.WAITING_ON_STAGE2_EDIT_COMMIT)

    with pytest.raises(UnsupportedRunMode, match='exact canonical|不能处理'):
        P.permit_from_next(current, 'stage2.global', flags)


def test_stage2_revalidate_commit_permit_replays_operator_exact_human_audit(
        monkeypatch, tmp_path):
    _stable(monkeypatch)
    exact = [
        '--revalidate', '--actor', 'human:reviewer',
        '--reason', '人工修正 INVALID 后继续提交',
    ]
    current = _next(
        tmp_path, state='blocked', node='stage2.global', flags=exact,
        waiting_on=Q.WAITING_ON_STAGE2_EDIT_REVALIDATE)

    got = P.permit_from_next(current, 'stage2.global', exact)
    assert got.flags == exact and got.external_inputs == []
    with pytest.raises(UnsupportedRunMode, match='exact actor/reason'):
        P.permit_from_next(current, 'stage2.global', [
            '--revalidate', '--actor', 'system:other', '--reason', 'other',
        ])
    with pytest.raises(UnsupportedRunMode, match='不能处理'):
        P.permit_from_next(current, 'stage2.global', ['--renormalize'])
