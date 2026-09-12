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
    CommanderTurnResult, RunSupervisor, SupervisorActionResult, range_boundary_reason,
)
from tests.test_agent_supervisor import _epoch
from tests.test_agent import _PlanningDriver, _PROFILES


def _current(node, *, state='ready', waiting_on='stage2.blueprint.review'):
    return SimpleNamespace(state=state, code_version='code-1',
        action=None if state == 'pipeline_complete' else SimpleNamespace(
            node=node, waiting_on=waiting_on if state == 'blocked' else None),
        execution_token=SimpleNamespace(node=node) if state == 'ready' else None)


@pytest.mark.parametrize('node,state', [
    ('stage2.blueprint', 'ready'), ('stage2.blueprint', 'blocked'),
    ('stage4.design', 'ready'), ('stage5.write', 'blocked'),
    (None, 'pipeline_complete'), ('future.unknown', 'ready'),
])
def test_supervisor_range_stops_before_any_ready_review_or_finalize(node, state):
    events, drained = [], []
    supervisor = RunSupervisor(run_id='range-stop', append_event=events.append,
        make_epoch=lambda *_: pytest.fail('范围命中不滚新 epoch'), open_epoch=lambda _: None,
        close_epoch=lambda epoch, turn: (drained.append((epoch.epoch_id, turn.outcome)) or 'drained'),
        run_commander_turn=lambda *_: pytest.fail('不能启动整轮 Commander'),
        epoch_identity_current=lambda _: True, cancel_commander=lambda: True,
        initial_epoch=_epoch('range-epoch', 1, 'initial'),
        read_operator_next=lambda _: _current(node, state=state),
        run_ready_action=lambda *_: pytest.fail('边界后的 Executor 不得启动'),
        run_reviewer=lambda *_: pytest.fail('边界后的 Reviewer 不得启动'),
        finalize_run=lambda *_: pytest.fail('范围收口不做全链交付'),
        pause_before_node='stage2.blueprint')
    result = supervisor.run()
    assert result.turn.exit_code == 2 and result.turn.outcome.state == 'stopped'
    assert '范围' in result.turn.outcome.reason
    assert len(drained) == 1 and drained[0][1] == result.turn.outcome
    assert [event.kind for event in events] == ['epoch_closed']
    assert events[0].drain_state == 'drained'


def test_range_allows_upstream_repair_then_stops_at_blueprint_without_reviewer():
    actions = [_current('stage2.global'), _current('stage2.blueprint', state='blocked')]
    calls, events = [], []

    def ready(_epoch, current, _resume, ticket):
        calls.append(current.action.node)
        ticket.commit()
        actions.pop(0)
        return SupervisorActionResult(0, AgentDeliveryOutcome(
            state='stopped', checked_at=1, reason='上游修复已落盘'), True)

    supervisor = RunSupervisor(run_id='range-repair', append_event=events.append,
        make_epoch=lambda *_: pytest.fail('无身份漂移'), open_epoch=lambda _: None,
        close_epoch=lambda *_: 'drained', run_commander_turn=lambda *_: pytest.fail('不用整轮 Commander'),
        epoch_identity_current=lambda _: True, cancel_commander=lambda: True,
        initial_epoch=_epoch('range-epoch', 1, 'initial'), read_operator_next=lambda _: actions[0],
        run_ready_action=ready, run_reviewer=lambda *_: pytest.fail('Blueprint C 不得启动'),
        finalize_run=lambda *_: pytest.fail('不得全链定稿'), pause_before_node='stage2.blueprint')
    result = supervisor.run()
    assert calls == ['stage2.global']
    assert result.turn.outcome.state == 'stopped'
    assert 'stage2.blueprint' in result.turn.outcome.reason


def test_joint_owner_and_repair_owner_cannot_evade_range_order():
    assert range_boundary_reason(_current('stage2.analyze'), 'stage2.global') is not None
    assert range_boundary_reason(_current('stage2.analyze'), 'stage2.characters') is not None
    assert range_boundary_reason(_current('stage2.analyze'), 'stage2.blueprint') is None
    assert range_boundary_reason(_current('stage0.edit'), 'stage0.approve') is None
    assert range_boundary_reason(_current('stage0.edit'), 'stage0.edit') is not None
    with pytest.raises(ValidationError):
        AgentRunRequest(sid='42', pause_before_node='stage3.retired')


def test_range_uses_registered_commander_owner_not_blocked_consumer():
    from novel.orchestrator.queue import WAITING_ON_STAGE2_REVIEW
    current = _current('stage2.blueprint', state='blocked', waiting_on=WAITING_ON_STAGE2_REVIEW)
    assert range_boundary_reason(current, 'stage2.blueprint') is None
    assert range_boundary_reason(current, 'stage2.global') is not None
    from contracts.invariants import NODES
    for waiting_on, _scope in NODES['stage2.blueprint'].lifecycle.commander_bindings:
        downstream = _current('stage4.design', state='blocked', waiting_on=waiting_on)
        assert range_boundary_reason(downstream, 'stage2.blueprint') is not None


@pytest.mark.stage_scope('stage2')
def test_joint_initial_review_closes_before_blueprint_range_boundary(tmp_path):
    """真实首抽与 C typed 接受走实际 planner；范围不得把上游审阅随下游截断。"""
    from contracts.artifacts import STAGE2_CHAPTER_BLUEPRINTS, STAGE2_KNOWLEDGE_LINKS
    from novel.orchestrator import operator, queue
    from novel.stages.stage2 import analyze
    from novel.store import ArtifactStore
    from novel import prov, work_units
    from novel.llm.cache import CachePolicy
    from novel.llm.ledger import CostLedger, ledger_path
    from novel.node_agents import Stage2AnalyzeAgent
    from novel.run import RunContext
    from novel.stages.stage0.removal import _context, run_approve, run_source
    from novel.stages.stage1.partition import run_partition
    from novel.stages.stage2.global_evidence_candidates import GlobalEvidenceCandidateTable
    from tests.stage2_fixtures import character_response, knowledge_response
    from tests.test_stage2 import main_text
    from tests.test_stage2_analyze import _driver
    from tests.test_stage2_character_rebind import accept_current_with_one_response
    from tests.test_stage2_global_agent import _profile

    # 上游也经实际 producer 与 typed 批准建立；Stage1 保留章界换行，不能强行
    # 归一正式正文。逐条核对 e000xxx 仍指向原 fixture 同章同段，随后冻结上游。
    base = tmp_path / 'output'
    work_units.initialize_workflow(base)
    source = tmp_path / 'FQ_联合范围验收_2042.txt'
    chapters = main_text()
    text = '旧宅一封信，掀开了被隐瞒的往事。\n\n' + '\n\n'.join(
        f'{row["chapter_ordinal"]}\n\n{row["text"]}' for row in chapters) + '\n\n（全文完）\n\n'
    source.write_text(text, encoding='utf-8', newline='')
    book, removal, _pending = run_source(source, base)
    _approved, approval_report = run_approve(_context(book, 'stage0.approve'),
        expected_revision_id=removal.report.revision_id,
        actor='stage2-range-fixture', reason='核对完整合成正文与独立开篇后批准')
    assert approval_report.passed
    partitioned, partition_report = run_partition(_context(book, 'stage1.partition'))
    assert partition_report is not None and partition_report.passed
    partitioned_chapters = partitioned.main_text.model_dump(mode='json')
    assert [{**row, 'text': row['text'].strip('\r\n')} for row in partitioned_chapters] == chapters
    actual_candidates = GlobalEvidenceCandidateTable.build(partitioned_chapters)
    expected_candidates = GlobalEvidenceCandidateTable.build(chapters)
    assert [(row.evidence_candidate_id, row.chapter_ordinal, row.text)
            for row in actual_candidates.rows] == [
        (row.evidence_candidate_id, row.chapter_ordinal, row.text)
        for row in expected_candidates.rows]
    actual_texts = {row['chapter_ordinal']: row['text'] for row in partitioned_chapters}
    assert all(actual_texts[row.chapter_ordinal][row.start:row.end] == row.text
               for row in actual_candidates.rows)
    upstream = {path: path.read_bytes() for directory in (book / 'stage0', book / 'stage1')
                for path in directory.rglob('*') if path.is_file()}
    ctx = RunContext(novel_dir=book, node=analyze.NODE, paid=True,
        code=prov.code_version(), cache=CachePolicy.default(),
        ledger=CostLedger(path=ledger_path(base)), executor_profile=_profile())
    response = {'characters': character_response()['characters'], 'global': knowledge_response()}
    Stage2AnalyzeAgent(ctx, driver=_driver(ctx, response)).run()
    ctx.node = 'stage2.global'
    store = ArtifactStore(ctx)
    ledger = ctx.ledger.path.read_bytes()
    receipt = store.path(analyze.REL).read_bytes()
    before = operator.next_action(base, ctx.sid)
    assert before.state == 'blocked' and before.action.node == 'stage2.blueprint'
    assert before.action.waiting_on == queue.WAITING_ON_STAGE2_REVIEW
    calls, events = [], []

    def review(_epoch, current, _resume, ticket):
        assert current.action.waiting_on == queue.WAITING_ON_STAGE2_REVIEW
        ticket.commit()
        calls.append('stage2.global')
        accept_current_with_one_response(ctx)
        return CommanderTurnResult(0, None, True, AgentDeliveryOutcome(
            state='stopped', checked_at=1, reason='合成 C 响应经真实 typed 路径接受'))

    supervisor = RunSupervisor(run_id='joint-range-review', append_event=events.append,
        make_epoch=lambda *_: pytest.fail('本例代码与 owner 身份未变'), open_epoch=lambda _: None,
        close_epoch=lambda *_: 'drained', run_commander_turn=lambda *_: pytest.fail('不用整轮 Commander'),
        epoch_identity_current=lambda epoch: epoch.code_version == prov.code_version(),
        cancel_commander=lambda: True,
        initial_epoch=_epoch('range-epoch', 1, 'initial').model_copy(update={'code_version': ctx.code}),
        read_operator_next=lambda _: operator.next_action(base, ctx.sid),
        run_ready_action=lambda *_: pytest.fail('Blueprint Executor 不得启动'),
        run_reviewer=review, finalize_run=lambda *_: pytest.fail('不得全链定稿'),
        pause_before_node='stage2.blueprint')
    result = supervisor.run()
    after = operator.next_action(base, ctx.sid)
    assert calls == ['stage2.global']
    assert after.state == 'ready' and after.action.node == 'stage2.blueprint'
    assert result.turn.exit_code == 2 and result.turn.outcome.state == 'stopped'
    assert 'stage2.blueprint' in result.turn.outcome.reason
    assert [event.kind for event in events] == ['epoch_closed']
    assert events[-1].drain_state == 'drained'
    assert not store.path(STAGE2_CHAPTER_BLUEPRINTS).exists()
    assert not store.path(STAGE2_KNOWLEDGE_LINKS).exists()
    assert ctx.ledger.path.read_bytes() == ledger
    assert store.path(analyze.REL).read_bytes() == receipt
    assert source.read_text(encoding='utf-8') == text
    assert all(path.read_bytes() == raw for path, raw in upstream.items())


def test_none_boundary_keeps_original_persistent_contract_identity():
    start = AgentRunStart(sid='42', argv=['commander'], prompt='原提示',
        authorized=True, egress_authorized=True, profiles=_PROFILES, identity_version=2)
    contract = RunContractV2(sid='42', base='E:/Output', authorized=True,
        egress_authorized=True, profiles=_PROFILES)
    for model in (start, contract):
        original = model.model_dump(mode='json')
        assert 'pause_before_node' not in original
        assert type(model).model_validate({**original, 'pause_before_node': None}).model_dump(mode='json') == original
        bounded = model.model_copy(update={'pause_before_node': 'stage2.blueprint'})
        assert type(model).model_validate(bounded.model_dump(mode='json')).pause_before_node == 'stage2.blueprint'


def test_http_range_start_drains_owner_and_restart_continuation_preserves_boundary(tmp_path, monkeypatch):
    """不替换 operator、Supervisor 或启动分支；本书第一个真实动作即是受管上界。"""
    from novel import prov, work_units
    work_units.initialize_workflow(tmp_path)
    (tmp_path / 'FQ_范围测试_42').mkdir()
    monkeypatch.setattr(prov, 'code_version', lambda: 'test-range-code')
    # 只替换外部 Commander invocation 构建；范围命中前没有模型、节点或审阅启动。
    monkeypatch.setattr(agent, 'create_commander_driver', lambda profile, **_: _PlanningDriver(profile))
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
    assert first.start.prompt == preview.json()['prompt']
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
        explicit = restarted.post('/api/agent/runs/preview', json={**continued, 'pause_before_node': 'stage2.blueprint'})
        assert explicit.status_code == 200, explicit.text
        assert explicit.json()['pause_before_node'] == 'stage2.blueprint'
        second = restarted.post('/api/agent/runs', json=continued)
        assert second.status_code == 202, second.text
        assert agent.wait(second.json()['run_id'], 60)
    resumed = next(row for row in agent.records(tmp_path) if row.run_id == second.json()['run_id'])
    assert resumed.state == 'stopped' and resumed.contract.pause_before_node == 'stage0.remove'
    assert resumed.contract.profiles == first.contract.profiles
    assert agent_continuation.inspect(tmp_path, resumed.run_id).available
    ledger = tmp_path / '_service' / 'ledger.jsonl'
    assert not ledger.exists() or not ledger.read_bytes()
