"""受管运行排他上界：真实 Supervisor 拦启动，HTTP 持久范围并按原配置续跑。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from contracts.schema.api import AgentRunRequest
from contracts.schema.orchestration import AgentDeliveryOutcome, AgentRunStart, RunContractV2
from novel.api.app import UI_HEADER, create_app
from novel.orchestrator import admission, agent, agent_continuation
from novel.orchestrator.agent_supervisor import (
    ExecutionTurnResult, RunSupervisor, SupervisorActionResult, range_boundary_reason,
)
from tests.test_agent_supervisor import _epoch
from tests.test_agent import _PROFILES


def _current(node, *, state='ready', waiting_on='manual-unregistered-blocker'):
    return SimpleNamespace(state=state, code_version='code-1',
        action=None if state == 'pipeline_complete' else SimpleNamespace(
            node=node, waiting_on=waiting_on if state == 'blocked' else None),
        execution_token=SimpleNamespace(node=node) if state == 'ready' else None)


@pytest.mark.parametrize('node,state', [
    ('str06.stage4.body_design', 'ready'),
    ('str06.stage4.body_design', 'blocked'),
    ('str06.stage4.hook_design', 'ready'), ('str06.stage5.body', 'blocked'),
    (None, 'pipeline_complete'), ('future.unknown', 'ready'),
])
def test_supervisor_range_stops_before_any_ready_review_or_finalize(node, state):
    events, drained = [], []
    supervisor = RunSupervisor(run_id='range-stop', append_event=events.append,
        make_epoch=lambda *_: pytest.fail('范围命中不滚新 epoch'), open_epoch=lambda _: None,
        close_epoch=lambda epoch, turn: (drained.append((epoch.epoch_id, turn.outcome)) or 'drained'),
        epoch_identity_current=lambda _: True, cancel_active=lambda: True,
        initial_epoch=_epoch('range-epoch', 1, 'initial'),
        read_operator_next=lambda _: _current(node, state=state),
        run_ready_action=lambda *_: pytest.fail('边界后的 Executor 不得启动'),
        finalize_run=lambda *_: pytest.fail('范围收口不做全链交付'),
        pause_before_node='str06.stage4.body_design')
    result = supervisor.run()
    assert result.turn.exit_code == 2 and result.turn.outcome.state == 'stopped'
    assert '范围' in result.turn.outcome.reason
    assert len(drained) == 1 and drained[0][1] == result.turn.outcome
    assert [event.kind for event in events] == ['epoch_closed']
    assert events[0].drain_state == 'drained'


def test_range_allows_upstream_repair_then_stops_at_preserve_without_reviewer():
    actions = [_current('str06.stage2.names'),
               _current('str06.stage4.preserve', state='blocked')]
    calls, events = [], []

    def ready(_epoch, current, ticket):
        calls.append(current.action.node)
        ticket.commit()
        actions.pop(0)
        return SupervisorActionResult(0, AgentDeliveryOutcome(
            state='stopped', checked_at=1, reason='上游修复已落盘'), True)

    supervisor = RunSupervisor(run_id='range-repair', append_event=events.append,
        make_epoch=lambda *_: pytest.fail('无身份漂移'), open_epoch=lambda _: None,
        close_epoch=lambda *_: 'drained',
        epoch_identity_current=lambda _: True, cancel_active=lambda: True,
        initial_epoch=_epoch('range-epoch', 1, 'initial'), read_operator_next=lambda _: actions[0],
        run_ready_action=ready,
        finalize_run=lambda *_: pytest.fail('不得全链定稿'),
        pause_before_node='str06.stage4.preserve')
    result = supervisor.run()
    assert calls == ['str06.stage2.names']
    assert result.turn.outcome.state == 'stopped'
    assert 'str06.stage4.preserve' in result.turn.outcome.reason


def test_joint_owner_and_repair_owner_cannot_evade_range_order():
    # F8a 候选链无多 owner 面；B2（2026-09-27）后取 stage2 链首节点
    # `str06.stage2.book` 作样本。现役守卫方向：上界处的动作不启动，
    # 严格早于上界才放行。
    assert range_boundary_reason(
        _current('str06.stage2.book'), 'str06.stage2.book') is not None
    assert range_boundary_reason(
        _current('str06.stage2.book'), 'str06.stage2.names') is None
    # 旧联合节点 stage2.analyze 已退役：不在运行范围取值面里，关闭式拒排，
    # 不借历史编排位逃过现役上界。
    assert range_boundary_reason(
        _current('stage2.analyze'), 'str06.stage2.names') is not None
    # stage0.approve / stage0.edit 已退役（2026-09-15）：退役节点不在运行范围
    # 取值面里，关闭式拒排（不猜测节点先后），不再有「编辑归属 remove 槽」的重映射。
    assert range_boundary_reason(_current('stage0.edit'), 'stage0.remove') is not None
    assert range_boundary_reason(_current('stage0.remove'), 'stage1.partition') is None
    assert range_boundary_reason(_current('stage0.remove'), 'stage0.remove') is not None
    with pytest.raises(ValidationError):
        AgentRunRequest(sid='42', pause_before_node='stage3.retired')


def test_range_uses_registered_owner_not_blocked_consumer():
    from contracts.invariants import NODES
    from novel.orchestrator.queue import WAITING_ON_STAGE4_DESIGN_REVIEW
    # Stage4 审阅席位已随 2026-09-15 全自动裁决退役；STR-06 关闭批删除
    # 历史 blocker→owner 只读表后，历史审批 blocker 与未登记 blocker 统一
    # 按通则处理：不做席位重映射，blocked 消费者按自身编排位参与范围判定。
    # STR-06 关闭批：历史席位只读表（RETIRED_APPROVAL_BINDINGS）已删；
    # 历史审批 blocker 一律无归属 ⇒ 未登记 blocker 的关闭式拒排语义
    # 由下方未登记反例等价覆盖。
    # 未登记 blocker 不做历史席位重映射：blocked 消费者按自身编排位参与范围判定。
    consumer = _current('str06.stage2.book', state='blocked',
                        waiting_on='unregistered-blocker')
    assert range_boundary_reason(consumer, 'str06.stage4.compile') is None
    assert range_boundary_reason(consumer, 'str06.stage2.book') is not None


def test_none_boundary_keeps_original_persistent_contract_identity():
    start = AgentRunStart(sid='42', argv=['commander'], prompt='原提示',
        authorized=True, egress_authorized=True, profiles=_PROFILES, identity_version=2)
    contract = RunContractV2(sid='42', base='E:/Output', authorized=True,
        egress_authorized=True, profiles=_PROFILES)
    for model in (start, contract):
        original = model.model_dump(mode='json')
        assert 'pause_before_node' not in original
        assert type(model).model_validate({**original, 'pause_before_node': None}).model_dump(mode='json') == original
        bounded = model.model_copy(update={'pause_before_node': 'str06.stage2.book'})
        assert type(model).model_validate(bounded.model_dump(mode='json')).pause_before_node == 'str06.stage2.book'


def test_http_range_start_drains_owner_and_restart_continuation_preserves_boundary(tmp_path, monkeypatch):
    """不替换 operator、Supervisor 或启动分支；本书第一个真实动作即是受管上界。"""
    from novel import prov, work_units
    work_units.initialize_workflow(tmp_path)
    (tmp_path / 'FQ_范围测试_42').mkdir()
    monkeypatch.setattr(prov, 'code_version', lambda: 'test-range-code')
    # 范围命中发生在任何模型、节点或审阅启动之前；plan 不再构造 Commander invocation。
    request = {'sid': '42', 'authorized': True, 'egress_authorized': True,
               'profiles': _PROFILES.model_dump(mode='json'), 'pause_before_node': 'stage0.remove'}
    with TestClient(create_app(str(tmp_path))) as client:
        client.headers[UI_HEADER] = '1'
        preview = client.post('/api/agent/runs/preview', json=request)
        assert preview.status_code == 200, preview.text
        assert preview.json()['pause_before_node'] == 'stage0.remove'
        started = client.post('/api/agent/runs', json=request)
        assert started.status_code == 202, started.text
        run_id = started.json()['run_id']
        assert agent.wait(run_id, 60)
    first = next(row for row in agent.records(tmp_path) if row.run_id == run_id)
    assert first.state == 'stopped' and first.outcome.state == 'stopped'
    assert '范围' in first.outcome.reason
    # Commander 退役：argv/prompt 不再是启动审计列。
    assert first.start.argv is None and first.start.prompt is None
    assert 'prompt' not in preview.json() or preview.json()['prompt'] is None
    assert first.start.pause_before_node == first.contract.pause_before_node == 'stage0.remove'
    assert all(event.drain_state == 'drained' for event in agent.read_events(tmp_path)
               if event.run_id == run_id and event.kind == 'epoch_closed')
    assert admission.readonly_snapshot(tmp_path, run_id).active_claims == ()
    with TestClient(create_app(str(tmp_path))) as restarted:
        restarted.headers[UI_HEADER] = '1'
        continuation = restarted.get(f'/api/agent/runs/{run_id}/continuation')
        assert continuation.status_code == 200, continuation.text
        old = continuation.json()
        assert old['available'] and old['pause_before_node'] == 'stage0.remove'
        continued = {key: value for key, value in request.items() if key != 'pause_before_node'}
        continued['continuation'] = old['source']
        inherited = restarted.post('/api/agent/runs/preview', json=continued)
        assert inherited.status_code == 200, inherited.text
        assert inherited.json()['pause_before_node'] == 'stage0.remove'
        expanded = restarted.post('/api/agent/runs/preview', json={**continued, 'pause_before_node': None})
        assert expanded.status_code == 200, expanded.text
        assert expanded.json()['pause_before_node'] is None
        assert expanded.json()['profiles'] == old['profiles']
        explicit = restarted.post('/api/agent/runs/preview', json={**continued, 'pause_before_node': 'str06.stage2.book'})
        assert explicit.status_code == 200, explicit.text
        assert explicit.json()['pause_before_node'] == 'str06.stage2.book'
        second = restarted.post('/api/agent/runs', json=continued)
        assert second.status_code == 202, second.text
        assert agent.wait(second.json()['run_id'], 60)
    resumed = next(row for row in agent.records(tmp_path) if row.run_id == second.json()['run_id'])
    assert resumed.state == 'stopped' and resumed.contract.pause_before_node == 'stage0.remove'
    assert resumed.contract.profiles == first.contract.profiles
    assert agent_continuation.inspect(tmp_path, resumed.run_id).available
    ledger = tmp_path / '_service' / 'ledger.jsonl'
    assert not ledger.exists() or not ledger.read_bytes()
