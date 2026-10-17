#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Execution permit：ready 精确复用、blocked 恢复闭集与关闭式拒绝。

⚠ F8b-3（S-05）：旧链恢复旗标族（--revalidate/--apply-edit/--revise/
--recover-*/--rebind-review）随旧链整层退役，其 blocker 只能由旧链 planner
发出、生产入口已拒识；新链的免费复检是节点级 ``--check-only``，付费节点的
恢复是候选 runner 自身续跑（F7c），不经 permit 表发布付费恢复动作。本文件
保留 permit 层的形状守卫（exact 复用、关闭式拒绝、canonical envelope、
CLI 转发），旧机制的专属反例随机制一并退役。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.invariants import (
    EXECUTION_PERMIT_KINDS, FREE_ENTRIES, INBOX_KIND, NODES, VALUED_FLAGS,
)
from contracts.schema.api import (
    ExecutionPermit, InboxView, OperatorAction, OperatorNext,
    OperatorNextDecision,
)
from contracts.schema.orchestration import NovelOverview, OverviewSummary
from novel import exit_codes as X
from novel.cli.main import main
from novel.errors import TerminalState, UnsupportedRunMode
from novel.orchestrator import execution_permit as P
from novel.orchestrator import operator as O
from novel.orchestrator import queue as Q
from novel.orchestrator import nodes as NODE_POLICY


SID = 'book-1'
NODE = 'str06.stage5.body'


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
        tmp_path, state='ready', node=NODE, flags=['--check-only'])

    first = P.permit_from_next(current, NODE, ['--check-only'])
    second = P.permit_from_next(current, NODE, ['--check-only'])

    assert first == second
    assert first.kind == 'ready'
    assert first.operator_action_sha256 == 'c' * 64
    assert first.permit_sha256 != first.operator_action_sha256
    assert first.flags == ['--check-only']
    assert first.paid is False
    with pytest.raises(UnsupportedRunMode, match='exact node/flags'):
        P.permit_from_next(current, NODE, [])


def test_blocked_recovery_gets_a_new_permit_not_the_blocker_hash(
        monkeypatch, tmp_path):
    _stable(monkeypatch)
    current = _next(
        tmp_path, state='blocked', node=NODE,
        waiting_on=Q.WAITING_ON_INBOX)

    got = P.permit_from_next(current, NODE, ['--check-only'])

    assert got.kind == 'recovery'
    assert got.next_state == 'blocked'
    assert got.waiting_on == Q.WAITING_ON_INBOX
    assert got.operator_action_sha256 is None
    assert got.permit_sha256 != current.action.action_sha256
    assert got.paid is False


def test_pipeline_complete_and_unknown_flag_recovery_fail_closed(
        monkeypatch, tmp_path):
    _stable(monkeypatch)
    complete = _next(tmp_path, state='pipeline_complete')
    with pytest.raises(UnsupportedRunMode, match='pipeline_complete'):
        P.permit_from_next(complete, 'str06.stage4.naming', ['--check-only'])

    # stage1.partition 的 --dry-run 过 NODES 旗标面但不在恢复表：
    # 「旗标合法但不是登记的恢复组合」仍必须关闭式拒绝。
    blocked = _next(
        tmp_path, state='blocked', node='stage1.partition',
        waiting_on=Q.WAITING_ON_INBOX)
    with pytest.raises(UnsupportedRunMode, match='未登记'):
        P.permit_from_next(blocked, 'stage1.partition', ['--dry-run'])


def test_retired_old_chain_recovery_flags_are_unregistered(
        monkeypatch, tmp_path):
    """旧链恢复族整体不在新链节点旗标面上：validate_run 先按 NODES 拒收。"""
    _stable(monkeypatch)
    current = _next(
        tmp_path, state='blocked', node=NODE,
        waiting_on=Q.WAITING_ON_INBOX)
    for flags in (
        ['--revalidate', '--actor', 'a', '--reason', 'r'],
        ['--apply-edit', 'stage2/global_edit.json'],
        ['--rebind-review'],
        ['--revise'],
        ['--chapters', '1'],
    ):
        with pytest.raises(UnsupportedRunMode):
            P.permit_from_next(current, NODE, flags)


def test_recovery_cannot_jump_past_the_current_blocker(monkeypatch, tmp_path):
    _stable(monkeypatch)
    current = _next(
        tmp_path, state='blocked', node='stage1.partition',
        waiting_on=Q.WAITING_ON_INBOX)
    with pytest.raises(UnsupportedRunMode, match='位于当前 blocker'):
        P.permit_from_next(current, 'str06.stage4.naming', ['--check-only'])


def test_policy_tables_are_closed_and_all_rows_are_free_rechecks():
    assert EXECUTION_PERMIT_KINDS == ('ready', 'recovery')
    keys = [(row.node, row.tokens) for row in P.RECOVERY_RUN_POLICIES]
    assert len(keys) == len(set(keys))
    assert all(row.node in NODES and row.tokens <= set(NODES[row.node].flags)
               for row in P.RECOVERY_RUN_POLICIES)
    # F8b-3：恢复表只登记免费 --check-only 复检；不设任何付费恢复行。
    assert all(row.tokens == frozenset({'--check-only'}) for row in P.RECOVERY_RUN_POLICIES)
    assert not any(row.paid for row in P.RECOVERY_RUN_POLICIES)
    # compile/assemble 结构性禁付且无旗标，不设恢复行。
    assert {row.node for row in P.RECOVERY_RUN_POLICIES} == {
        node for node, spec in NODES.items() if '--check-only' in spec.flags}
    # 旧链外部文件输入族随旧链退役：现役旗标面无文件型 flag ⇒ 空表。
    assert P.EXTERNAL_INPUT_POLICIES == {}
    # 旧 paid 恢复行已删：贵贱判定给 True 时必须关闭式拒绝。
    blocked = _next(
        Path('E:/nowhere').resolve(), state='blocked', node=NODE,
        waiting_on=Q.WAITING_ON_INBOX)
    with pytest.raises(UnsupportedRunMode):
        P._require_recovery_policy(blocked, NODE, ())


def test_paid_recovery_rows_are_gone_and_drift_is_rejected():
    """付费节点的空旗标组合是付费判定；恢复表全免费 ⇒ 贵贱漂移必须响。"""
    assert all(not row.paid for row in P.RECOVERY_RUN_POLICIES)
    assert not {'--revise', '--reviewer-rewrite'} & set(VALUED_FLAGS)
    assert '--rebind-review' not in FREE_ENTRIES
    assert '--rebind-review' not in VALUED_FLAGS
    for node in ('str06.stage2.book', 'str06.stage5.body', 'str06.stage4.naming'):
        assert '--rebind-review' not in NODES[node].flags


def test_execution_permit_schema_forbids_reusing_blocker_hash():
    with pytest.raises(ValidationError, match='不得复用'):
        ExecutionPermit(
            base='E:/Output', sid=SID, code_version='code-v1',
            surface_seal='a' * 64, next_state='blocked', waiting_on='x',
            kind='recovery', node=NODE, flags=['--check-only'],
            paid=False, writes='stage5/body/ch01.txt',
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

    assert O.permit(tmp_path, SID, 'str06.stage4.naming', ['--check-only']) == 'ok'
    assert calls == [
        ('capture', tmp_path, SID),
        ('next', 'snapshot'),
        ('permit', current, 'str06.stage4.naming', ['--check-only']),
    ]


def test_operator_permit_cli_forwards_every_flag_and_returns_strict_json(
        monkeypatch, tmp_path, capsys):
    _stable(monkeypatch)
    current = _next(
        tmp_path, state='ready', node=NODE,
        flags=['--check-only'])
    minted = P.permit_from_next(
        current, NODE, ['--check-only'])
    calls = []
    monkeypatch.setattr(
        O, 'permit',
        lambda base, sid, node, flags: calls.append(
            (base, sid, node, flags)) or minted)

    rc = main([
        'operator', 'permit', SID, NODE,
        '--base', str(tmp_path), '--json', '--', '--check-only'])

    assert rc == X.OK
    assert calls == [(str(tmp_path), SID, NODE, ['--check-only'])]
    assert json.loads(capsys.readouterr().out) == minted.model_dump(mode='json')


def test_execution_permit_envelope_round_trips_and_rejects_tampering(
        monkeypatch, tmp_path):
    _stable(monkeypatch)
    current = _next(
        tmp_path, state='ready', node=NODE,
        flags=['--check-only'])
    minted = P.permit_from_next(
        current, NODE, ['--check-only'])
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
        tmp_path, state='ready', node=NODE,
        flags=['--check-only'])
    minted = P.permit_from_next(
        current, NODE, ['--check-only'])
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
        tmp_path, state='ready', node=NODE,
        flags=['--check-only'])
    minted = P.permit_from_next(
        decision, NODE, ['--check-only'])
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


def test_claim_side_revalidation_rejects_active_surface_drift(
        monkeypatch, tmp_path):
    _stable(monkeypatch)
    current = _next(
        tmp_path, state='ready', node=NODE,
        flags=['--check-only'])
    minted = P.permit_from_next(
        current, NODE, ['--check-only'])
    monkeypatch.setattr(P, 'read_surface_seal', lambda *_a, **_k: 'b' * 64)

    from novel.errors import ArtifactUnreadable
    with pytest.raises(ArtifactUnreadable, match='active artifact'):
        P.revalidate_active_execution_surface(minted)
