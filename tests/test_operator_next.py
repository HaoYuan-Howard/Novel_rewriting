#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""确定性 operator next：共享 lane 判定、严格状态与 CLI/HTTP 只读出口。"""
from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest

from pydantic import ValidationError

from contracts.invariants import INBOX_KIND, NODES, OPERATOR_NEXT_STATES, RESUME_VERDICTS
from contracts.schema.api import (
    InboxView, OperatorAction, OperatorNext, OperatorNextDecision,
)
from contracts.schema.orchestration import (
    NovelOverview, OverviewSummary, QueueItem, RunResumeFrontierView, Step,
)
from novel import exit_codes as X
from novel.cli.main import main
from novel.errors import ArtifactUnreadable
from novel.orchestrator import execution_permit as P
from novel.orchestrator import operator as O
from novel.orchestrator import queue as Q
from novel.orchestrator.inspection import OperatorReadSnapshot
from novel.paths import derive_source_meta


def _overview(sid: str = 'book-1') -> NovelOverview:
    return NovelOverview(
        sid=sid, title='测试书', dirname=f'平台_测试书_{sid}', stages=[],
        summary=OverviewSummary(
            verdict='pending', text='待继续', detail='下一步待判',
            artifact_count=0, latest_mtime=None),
    )


def _inbox() -> InboxView:
    return InboxView(
        items=[], counts={kind: 0 for kind in INBOX_KIND}, total=0, wontfix=0)


def _snapshot(tmp_path: Path, *, seal: str = 'a' * 64) -> OperatorReadSnapshot:
    novel_dir = tmp_path / '平台_测试书_book-1'
    return OperatorReadSnapshot(
        base=tmp_path.resolve(), sid='book-1', novel_dir=novel_dir,
        code='code-v1', surface_seal=seal, overview=_overview(),
        checks=MappingProxyType({}),
    )


def _step(node: str) -> Step:
    return Step(
        argv=['python', '-m', 'novel.cli', 'run', node], cwd='.', label=node,
        paid=False, writes=NODES[node].produces[0] if NODES[node].produces else node)


def _ready(node: str, *, flags: list[str] | None = None) -> QueueItem:
    return QueueItem(
        sid='book-1', node=node, flags=flags or [], steps=[_step(node)],
        why=f'run：{node} 产物缺失')


def _blocked(node: str, waiting_on: str) -> QueueItem:
    return QueueItem(
        sid='book-1', node=node, flags=[], steps=[],
        why=f'在等 {waiting_on}', waiting_on=waiting_on)


# （历史）approval_doc 预计算/跨书投影用例已随 approvals 解码链删除
# （2026-10-04 清理批）；inbox_from_snapshot 不再有该参数。


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    snapshot: OperatorReadSnapshot,
    *, todo: list[QueueItem], blocked: list[QueueItem],
) -> list[object]:
    """把 next 的四个外部依赖换成可观测替身；审批投影只留 invocation 坐标。

    F8c／2026-10-04 清理批：审批投影上下文已随 approvals 模块删除；
    `_next_from_snapshot` 直接消费 snapshot 的 overview/code 坐标投影
    inbox 与 queue lane——替身据此断言「同一次 next 只有一个代码时点」。
    """
    calls: list[object] = []
    monkeypatch.setattr(
        O.paths, 'resolve_novel_dir',
        lambda base, sid: snapshot.novel_dir)
    monkeypatch.setattr(
        O, 'capture_operator_snapshot',
        lambda base, sid: calls.append(('capture', base, sid)) or snapshot)
    monkeypatch.setattr(
        P.prov, 'code_version',
        lambda: pytest.fail('ready next 不得重算第二次仓库 code fingerprint'))

    def inbox(got, *, _final_cas_owner: object = None):
        assert _final_cas_owner is O._OPERATOR_FINAL_CAS
        calls.append(('inbox', got))
        return _inbox()

    monkeypatch.setattr(O, 'inbox_from_snapshot', inbox)

    def plan(
            base, sid, overview, *, until_stage, follow_check,
            allow_deterministic_redo: bool,
    ):
        assert allow_deterministic_redo is True
        calls.append((
            'plan', base, sid, overview, until_stage, follow_check,
            allow_deterministic_redo))
        return todo, blocked

    monkeypatch.setattr(O.queue, 'plan_book_from_overview', plan)
    monkeypatch.setattr(
        O, 'read_surface_seal',
        lambda base, sid, *, code: calls.append(('seal', base, sid, code))
        or snapshot.surface_seal)
    monkeypatch.setattr(
        O.state, 'resume_verdict', lambda _overview, node, *, flags=(): ('run', f'{node} 缺失'))
    return calls


def test_next_uses_one_snapshot_and_the_shared_queue_lane(monkeypatch, tmp_path):
    snapshot = _snapshot(tmp_path)
    calls = _wire(
        monkeypatch, snapshot,
        todo=[_ready('str06.stage2.book')],
        blocked=[_blocked('stage1.partition', Q.WAITING_ON_INBOX)])

    got = O.next_action(tmp_path, 'book-1')

    assert got.state == 'blocked'
    assert got.action is not None
    assert got.action.node == 'stage1.partition'
    assert got.action.waiting_on == Q.WAITING_ON_INBOX
    assert got.overview == snapshot.overview
    assert len([call for call in calls if call[0] == 'capture']) == 1
    assert ('inbox', snapshot) in calls
    plan_call = next(call for call in calls if call[0] == 'plan')
    assert plan_call[3] is snapshot.overview
    assert plan_call[4:] == ('stage7', True, True)
    assert [call for call in calls if call[0] == 'seal'] == [
        ('seal', snapshot.base, 'book-1', 'code-v1')]


def test_retired_blocker_has_no_execution_token(
        monkeypatch, tmp_path):
    snapshot = _snapshot(tmp_path)
    blocker = _blocked(
        'str06.stage5.body', 'legacy_frozen_v6_design_authority_unavailable')
    _wire(monkeypatch, snapshot, todo=[], blocked=[blocker])

    got = O.next_action(tmp_path, 'book-1')

    assert got.state == 'blocked'
    assert got.execution_token is None
    assert got.action is not None
    assert got.action.waiting_on == 'legacy_frozen_v6_design_authority_unavailable'
    assert got.action.waiting_on != Q.WAITING_ON_STAGE4_DESIGN_REVIEW


def test_ready_action_carries_the_canonical_safety_axes_and_stable_hash(
        monkeypatch, tmp_path):
    snapshot = _snapshot(tmp_path)
    _wire(monkeypatch, snapshot, todo=[_ready('str06.stage2.book')], blocked=[])

    first = O.next_action(tmp_path, 'book-1')
    second = O.next_action(tmp_path, 'book-1')

    assert first.state == 'ready'
    assert first.action is not None
    assert first.action.resume_verdict == 'run'
    assert first.action.paid is True
    assert first.action.writes == ' · '.join(NODES['str06.stage2.book'].produces)
    assert first.action.action_sha256 == second.action.action_sha256
    assert len(first.action.action_sha256) == 64
    assert first.execution_token is not None
    assert first.execution_token == second.execution_token
    assert first.execution_token.node == first.action.node
    assert first.execution_token.flags == first.action.flags
    assert first.execution_token.operator_action_sha256 == first.action.action_sha256


def test_ready_redo_keeps_its_verdict_and_gets_an_execution_token(
        monkeypatch, tmp_path):
    """REDO 被安全准入后仍叫 REDO，不靠改名成 RUN 绕过五态契约。"""
    snapshot = _snapshot(tmp_path)
    item = QueueItem(
        sid='book-1', node='str06.stage2.book', flags=[],
        steps=[_step('str06.stage2.book')],
        why='redo：源语义的实际消费投影已更新')
    _wire(monkeypatch, snapshot, todo=[item], blocked=[])
    monkeypatch.setattr(
        O.state, 'resume_verdict',
        lambda _overview, _node, *, flags=(): ('redo', '源语义的实际消费投影已更新'))

    got = O.next_action(tmp_path, 'book-1')

    assert got.state == 'ready'
    assert got.action is not None and got.action.resume_verdict == 'redo'
    assert got.action.waiting_on is None
    assert got.execution_token is not None
    assert got.execution_token.node == 'str06.stage2.book'


def test_action_hash_binds_the_surface_seal(monkeypatch, tmp_path):
    first_snapshot = _snapshot(tmp_path, seal='a' * 64)
    _wire(monkeypatch, first_snapshot, todo=[_ready('stage1.partition')], blocked=[])
    first = O.next_action(tmp_path, 'book-1')

    second_snapshot = _snapshot(tmp_path, seal='b' * 64)
    _wire(monkeypatch, second_snapshot, todo=[_ready('stage1.partition')], blocked=[])
    second = O.next_action(tmp_path, 'book-1')

    assert first.action is not None and second.action is not None
    assert first.action.action_sha256 != second.action.action_sha256
    assert first.execution_token is not None and second.execution_token is not None
    assert first.execution_token.permit_sha256 != second.execution_token.permit_sha256


def test_no_lane_item_is_pipeline_complete_not_delivery_ready(monkeypatch, tmp_path):
    snapshot = _snapshot(tmp_path)
    _wire(monkeypatch, snapshot, todo=[], blocked=[])

    got = O.next_action(tmp_path, 'book-1')

    assert got.state == 'pipeline_complete'
    assert got.action is None


@pytest.mark.parametrize(
    ('item', 'expected_handler'),
    [
        (
            _blocked(
                'str06.stage5.body', Q.WAITING_ON_STAGE5_REVIEWER_REWRITE),
            'unknown',
        ),
        (
            _blocked(
                'str06.stage5.body', Q.WAITING_ON_RESUME_DECISION),
            'unknown',
        ),
    ],
)
def test_progress_snapshot_projects_authoritative_frontier_and_handler(
        monkeypatch, tmp_path, item, expected_handler):
    """运行页看 operator 当前 lane，不从空 beacon 猜成 Stage0 重跑。

    F8c：`DETERMINISTIC_RECOVERY_WAITING_ON` 现为空集——旧链的 0-wire 补尾
    waiter（reviewer-rewrite 等）随旧链退役，任何未登记的 waiting_on 一律
    投影为 `unknown` 处理人（fail-closed），不假装有人在处理。
    """
    snapshot = _snapshot(tmp_path)
    _wire(monkeypatch, snapshot, todo=[], blocked=[item])

    overview, frontier = O.progress_snapshot(tmp_path, 'book-1')

    assert overview is snapshot.overview
    assert frontier.state == 'blocked'
    assert frontier.node == item.node
    assert frontier.seq == list(NODES).index(item.node) + 1
    assert frontier.label == NODES[item.node].label
    assert frontier.waiting_on == item.waiting_on
    assert frontier.next_handler == expected_handler
    assert frontier.verified_skipped_nodes == list(NODES)[:frontier.seq - 1]
    assert frontier.verified_skipped_count == frontier.seq - 1


def test_resume_frontier_rejects_a_frontend_invented_node_label():
    with pytest.raises(ValidationError, match='label 必须来自 NODES'):
        RunResumeFrontierView(
            state='ready', node='stage1.partition', seq=2,
            label='前端自造标签', resume_verdict='run', why='产物缺失',
            paid=False, writes='stage1/main_text.json', next_handler='code',
            verified_skipped_nodes=list(NODES)[:3],
            verified_skipped_count=3,
        )


def _recovery_frontier_fields():
    node = 'str06.stage5.body'
    return dict(
        state='blocked', node=node, seq=list(NODES).index(node) + 1,
        label=NODES[node].label, resume_verdict='run', why='原 owner 尚待免费恢复',
        waiting_on=Q.WAITING_ON_ANALYZE_DRAFT_REPAIR, paid=False,
        writes='stage5/body/ch01.txt', next_handler='human')


@pytest.mark.parametrize('prefix_length', [0, 1])
def test_resume_frontier_accepts_only_actually_verified_short_prefix(prefix_length):
    fields = _recovery_frontier_fields()
    prefix = list(NODES)[:prefix_length]
    for state in ('ready', 'blocked'):
        current = {**fields, 'state': state}
        if state == 'ready':
            current.update(waiting_on=None, next_handler='code')
        result = RunResumeFrontierView(**current, verified_skipped_nodes=prefix,
                                      verified_skipped_count=prefix_length)
        assert result.verified_skipped_nodes == prefix
        assert result.verified_skipped_count < result.seq - 1


@pytest.mark.parametrize('skipped,count,reason', [
    ([list(NODES)[1]], 1, '连续 NODES 前缀'),
    ([list(NODES)[0], list(NODES)[2]], 2, '连续 NODES 前缀'),
    (['str06.stage7.assemble'], 1, '位于续跑点之前'),
    ([list(NODES)[1], list(NODES)[0]], 2, '按序且不重复'),
    ([list(NODES)[0], list(NODES)[0]], 2, '按序且不重复'),
    (['unregistered'], 1, '来自 NODES'),
    ([], 1, '节点列表不一致'),
])
def test_resume_frontier_short_prefix_keeps_identity_order_and_count_guards(
        skipped, count, reason):
    with pytest.raises(ValidationError, match=reason):
        RunResumeFrontierView(**_recovery_frontier_fields(),
                              verified_skipped_nodes=skipped, verified_skipped_count=count)


@pytest.mark.parametrize('state,handler,skipped', [
    ('unknown', 'unknown', [list(NODES)[0]]),
    ('pipeline_complete', 'none', []),
])
def test_resume_frontier_short_prefix_does_not_weaken_terminal_evidence(state, handler, skipped):
    with pytest.raises(ValidationError, match='前缀不完整'):
        RunResumeFrontierView(state=state, why='终态投影', next_handler=handler,
                              verified_skipped_nodes=skipped, verified_skipped_count=len(skipped))


def test_next_refuses_a_surface_that_changes_during_planning(monkeypatch, tmp_path):
    snapshot = _snapshot(tmp_path)
    _wire(monkeypatch, snapshot, todo=[_ready('stage1.partition')], blocked=[])
    monkeypatch.setattr(O, 'read_surface_seal', lambda *_args, **_kwargs: 'b' * 64)

    with pytest.raises(ArtifactUnreadable, match='混合时点'):
        O.next_action(tmp_path, 'book-1')


def test_operator_states_and_resume_verdicts_are_closed_and_schema_keeps_them():
    assert OPERATOR_NEXT_STATES == ('ready', 'blocked', 'pipeline_complete')
    assert RESUME_VERDICTS == (
        'run', 'skip', 'redo', 'unknown', 'legacy_incompatible')
    action = OperatorAction(
        node='stage1.partition', flags=[], resume_verdict='run', why='缺失',
        waiting_on=None, paid=False, writes='stage1/main_text.json',
        action_sha256='a' * 64)
    unknown = OperatorAction(
        node='stage1.partition', flags=[], resume_verdict='unknown', why='判不了',
        waiting_on=None, paid=False, writes='stage1/main_text.json',
        action_sha256='d' * 64)
    with pytest.raises(ValidationError, match='run 或已准入的 redo'):
        OperatorNextDecision(
            base='E:/Output', sid='book-1', code_version='code-v1',
            surface_seal='b' * 64, state='ready',
            overview=_overview(), inbox=_inbox(), action=unknown)
    with pytest.raises(ValidationError, match='pipeline_complete'):
        OperatorNext(
            base='E:/Output', sid='book-1', code_version='code-v1',
            surface_seal='b' * 64, state='pipeline_complete',
            overview=_overview(), inbox=_inbox(), action=action)
    with pytest.raises(ValidationError, match='waiting_on'):
        OperatorNext(
            base='E:/Output', sid='book-1', code_version='code-v1',
            surface_seal='b' * 64, state='blocked',
            overview=_overview(), inbox=_inbox(), action=action)
    with pytest.raises(ValidationError, match='execution_token'):
        OperatorNext(
            base='E:/Output', sid='book-1', code_version='code-v1',
            surface_seal='b' * 64, state='ready',
            overview=_overview(), inbox=_inbox(), action=action)


@pytest.mark.parametrize('state', ['blocked', 'pipeline_complete'])
def test_cli_json_keeps_blocked_and_complete_at_exit_zero(
        state, monkeypatch, tmp_path, capsys):
    action = None
    if state == 'blocked':
        action = OperatorAction(
            node='str06.stage4.naming', flags=['--check-only'], resume_verdict='run',
            why='命名残留需 0 成本复检后交付',
            waiting_on='str06.stage4.naming', paid=False,
            writes='stage4/naming/name_map.json', action_sha256='c' * 64)
    result = OperatorNext(
        base=str(tmp_path), sid='book-1', code_version='code-v1',
        surface_seal='a' * 64, state=state,
        overview=_overview(), inbox=_inbox(), action=action)
    monkeypatch.setattr(O, 'next_action', lambda _base, _sid: result)

    rc = main(['operator', 'next', 'book-1', '--base', str(tmp_path), '--json'])

    assert rc == X.OK
    assert json.loads(capsys.readouterr().out)['state'] == state


def test_http_operator_next_uses_the_same_producer(monkeypatch, tmp_path):
    pytest.importorskip('fastapi')
    from fastapi.testclient import TestClient
    from novel.api.app import create_app
    from novel.api.routes import novels

    result = OperatorNext(
        base=str(tmp_path), sid='book-1', code_version='code-v1',
        surface_seal='a' * 64, state='pipeline_complete',
        overview=_overview(), inbox=_inbox(), action=None)
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        novels, 'next_action',
        lambda base, sid: calls.append((base, sid)) or result)

    with TestClient(create_app(tmp_path)) as client:
        response = client.get('/api/novels/book-1/operator-next')

    assert response.status_code == 200
    assert response.json() == result.model_dump(mode='json')
    assert calls == [(str(tmp_path), 'book-1')]


def test_queue_public_entry_delegates_to_the_shared_overview_planner(
        monkeypatch, tmp_path):
    overview = _overview()
    monkeypatch.setattr(Q.st, 'overview', lambda base, sid: overview)
    calls: list[tuple] = []
    monkeypatch.setattr(
        Q, 'plan_book_from_overview',
        lambda *args, **kwargs: calls.append((args, kwargs)) or ([], []))

    assert Q.plan_book(tmp_path, 'book-1', until_stage='stage7') == ([], [])
    assert calls == [(
        (tmp_path, 'book-1', overview),
        {'until_stage': 'stage7', 'follow_check': True},
    )]


def test_next_action_accepts_directory_name_sid_end_to_end(tmp_path):
    """目录名与 source_id 两种可定位 sid 形态必须给出同一份 next 判定。

    历史缺陷：快照 sid 原样保留调用形态，而 ``overview_at`` 的 sid 恒从
    per-novel 目录名派生；传 ``书名_123`` 目录名时 ``OperatorNextDecision``
    的 sid 一致性校验以未捕获 ValidationError 崩溃（exit 70），CLI 手动逐步
    跑书拿不到 ready/blocked 判定。快照入口把 sid 收口为目录名派生形态后，
    两种调用形态的 base/sid/seal/action 指纹必须逐项一致。
    """
    from tests.fixtures_ingest import CASES
    from novel.stages.stage0 import removal as stage0_removal
    from novel.stages.stage1.partition import run_partition

    case = next(c for c in CASES if c.name == 'end_marker_then_fanwai')
    source = tmp_path / 'FQ_dir_sid_123.txt'
    source.write_bytes(case.to_bytes())
    base = tmp_path / 'Output'

    novel_dir, _removal, _initial = stage0_removal.run_source(source, base)
    run_partition(stage0_removal._context(novel_dir, 'stage1.partition'))

    canonical_sid = derive_source_meta(novel_dir.name)[0]
    assert canonical_sid != novel_dir.name, '用例必须覆盖两种形态确实不同的书名'

    by_dirname = O.next_action(base, novel_dir.name)
    by_source_id = O.next_action(base, canonical_sid)

    assert by_dirname.sid == by_source_id.sid == canonical_sid
    assert by_dirname.state == 'ready'
    assert by_dirname.action is not None
    assert by_dirname.action.action_sha256 == by_source_id.action.action_sha256
    assert by_dirname.surface_seal == by_source_id.surface_seal
