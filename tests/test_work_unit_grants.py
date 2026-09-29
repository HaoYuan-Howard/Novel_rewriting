"""真实 HTTP → 工作单元账的零费用额度链路（C5：直连 WorkUnitBudget，无 Agent CLI）。"""
from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from contracts.schema.api import WorkUnitGrantRequest
from contracts.schema.orchestration import LegacyExecutorBackend, LegacyLlmProfile
from novel import prov
from novel.api.app import create_app
from novel.llm.cache import CachePolicy
from novel.llm.client import LlmClient
from novel.llm.ledger import CostLedger, ledger_path
from novel.llm.transport import ChatResponse, FakeTransport
from novel.orchestrator import admission, work_unit_grants
from novel.run import RunContext
from novel.work_units import (
    WorkUnitBudget, WorkUnitBudgetExhausted, WorkUnitRefused, initialize_workflow,
)


class _Answer(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    value: int


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _profile() -> LegacyLlmProfile:
    return LegacyLlmProfile(
        provider='deepseek', model='deepseek-v4-pro',
        reasoning_effort='high', thinking_mode='on')


def _ctx(base, node: str = 'str06.stage4.body_design') -> RunContext:
    initialize_workflow(base)
    book = base / '额度链路_42'
    book.mkdir(exist_ok=True)
    return RunContext(
        novel_dir=book, node=node, paid=True,
        code=prov.code_version(), cache=CachePolicy.default(),
        ledger=CostLedger(path=ledger_path(base)),
        executor_backend=LegacyExecutorBackend(profile=_profile()),
    )


def _exhaust(base, *, accepted_last: bool = False):
    """耗尽基础四次内容额度；可选最后一次采纳成功（留 delivered+response）。"""
    ctx = _ctx(base)
    budget = WorkUnitBudget(base, ctx.sid, ctx.node)
    checkpoint = None
    for index in range(4):
        fingerprint = _sha(f'req-{index}')
        turn = budget.reserve(fingerprint, run_id=ctx.run_id)
        budget.record_attempt(
            turn.turn_id, attempt_id=f'att-{index}',
            outcome='ok' if (accepted_last and index == 3) else 'error',
            request_fingerprint=fingerprint)
        ctx.ledger.record(
            sid=ctx.sid, node=ctx.node, model=_profile().model,
            code=ctx.code, cached=False, cache_policy=ctx.cache.name,
            outcome='ok' if (accepted_last and index == 3) else 'error',
            prompt_tokens=10, completion_tokens=3, reasoning_tokens=2,
            purpose=ctx.node, attempt_id=f'att-{index}',
            request_fingerprint=fingerprint, latency_ms=12)
        if accepted_last and index == 3:
            budget.save_response(turn.turn_id, {'final_response': '{"value":4}'})
            budget.mark_adopted(turn.turn_id)
            checkpoint = {
                'accepted_turn_index': 4,
                'attempt_links': [
                    {'attempt_id': f'att-{i}'} for i in range(4)],
            }
        else:
            budget.finish(turn.turn_id, outcome='failed')
    assert budget.used == 4 and budget.remaining == 0
    return ctx, budget, checkpoint


def _request(view, **changes):
    return WorkUnitGrantRequest(
        preview_token=view.preview_token, intent_id=uuid4().hex,
        additional=view.additional,
        reason='确认允许本工作单元继续一次修订', **changes)


def test_http_grant_survives_restart_without_reissuing_base_allowance(tmp_path):
    ctx, budget, _ = _exhaust(tmp_path)
    before = budget._read()
    costs = ctx.ledger.path.read_bytes()
    original_files = {
        str(path): path.read_bytes()
        for path in tmp_path.rglob('*') if path.is_file()}
    endpoint = f'/api/novels/{ctx.sid}/work-units/{budget.unit_id}/grant'
    with TestClient(create_app(tmp_path)) as api:
        preview = api.get(endpoint)
        assert preview.status_code == 200, preview.text
        view = preview.json()
        assert view['available'], view['reason']
        assert view['costs']['calls'] == 4
        assert {str(path): path.read_bytes()
                for path in tmp_path.rglob('*') if path.is_file()} == original_files
        request = dict(
            preview_token=view['preview_token'], intent_id=uuid4().hex,
            additional=1, reason='补充一次完整修订')
        assert api.post(endpoint, json=request).status_code == 403
        result = api.post(endpoint, json=request, headers={'x-novel-ui': '1'})
        assert result.status_code == 200, result.text
        assert result.json()['current']['unit']['content_limit'] == 5
    with TestClient(create_app(tmp_path)) as restarted:
        repeat = restarted.post(endpoint, json=request, headers={'x-novel-ui': '1'})
        assert repeat.status_code == 200, repeat.text
        assert repeat.json()['grant'] == result.json()['grant']
        assert restarted.get(endpoint).json()['unit']['content_granted'] == 1
    assert budget._read().turns == before.turns
    assert budget._read().unit_id == before.unit_id and len(budget._read().grants) == 1
    assert ctx.ledger.path.read_bytes() == costs
    later = _ctx(tmp_path)
    transport = FakeTransport.always(ChatResponse(content='{"value":9}'))
    client = LlmClient(
        later, transport, model=_profile().model, enable_model_fallback=False)
    assert client.ask_json(
        'SYS', 'TASK', model_cls=_Answer, purpose=later.node).value == 9
    attempt_id = client.accepted_attempt(later.node)
    assert attempt_id is not None
    client.mark_checkpointed(attempt_id)
    assert len(transport.calls) == 1
    assert budget.used == 5 and budget.remaining == 0
    with pytest.raises(WorkUnitBudgetExhausted):
        budget.reserve('9' * 64, run_id='another-run')


@pytest.mark.parametrize('damage', [
    'surface', 'ledger', 'live_owner', 'delivered', 'unknown',
    'group', 'producer', 'beacon_tail',
])
def test_grant_cannot_bypass_changed_or_pending_work(tmp_path, damage):
    ctx, budget, _ = _exhaust(tmp_path)
    view = work_unit_grants.inspect(tmp_path, ctx.sid, budget.unit_id)
    assert view.available, view.reason
    request = _request(view)
    if damage == 'surface':
        # 现役 active 面：新链登记产物（global_grounding 已随旧链退役，
        # 不再进入 surface seal）。
        target = ctx.novel_dir / 'stage2' / 'characters' / 'characters.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{"changed":true}', encoding='utf-8')
    elif damage == 'ledger':
        budget.save_feedback(budget.latest_turn().turn_id, {'reason': '新的精确拒因'})
    elif damage == 'delivered':
        # 已失败轮补完整回答 → delivered，追加须先免费恢复
        budget.save_response(
            budget.latest_turn().turn_id, {'final_response': '{"value":1}'})
    elif damage == 'unknown':
        budget.finish(budget.latest_turn().turn_id, outcome='unknown')
    elif damage == 'group':
        budget.save_group_producer_recovery({'target': '未完成的候选组'})
    elif damage == 'producer':
        budget.save_response(
            budget.latest_turn().turn_id, {'final_response': '{"value":1}'})
        budget.save_producer_recovery(
            budget.latest_turn().turn_id, {'target': '未补提交的候选'})
    elif damage == 'beacon_tail':
        from novel.orchestrator import beacon
        beacon.beacon_path(tmp_path).write_bytes(b'{')
    if damage == 'live_owner':
        with admission.owner_lease(tmp_path, ctx.run_id):
            with pytest.raises(Exception, match='owner|parent|持有|活动|停止'):
                work_unit_grants.apply(tmp_path, ctx.sid, budget.unit_id, request)
    else:
        with pytest.raises((WorkUnitRefused, ValueError)):
            work_unit_grants.apply(tmp_path, ctx.sid, budget.unit_id, request)
    assert len(budget._read().grants) == 0 and budget.remaining == 0


def test_same_intent_cannot_change_amount_or_reason(tmp_path):
    ctx, budget, _ = _exhaust(tmp_path)
    view = work_unit_grants.inspect(tmp_path, ctx.sid, budget.unit_id, additional=4)
    request = _request(view)
    work_unit_grants.apply(tmp_path, ctx.sid, budget.unit_id, request)
    assert budget.remaining == 4
    with pytest.raises(WorkUnitRefused, match='不同'):
        work_unit_grants.apply(
            tmp_path, ctx.sid, budget.unit_id,
            request.model_copy(update={'additional': 1}))
    assert budget._read().content_limit == 8 and len(budget._read().grants) == 1


def test_grant_allows_fifth_content_turn_after_direct_llm_exhaustion(tmp_path):
    """旧职责：SessionAgentClient checkpoint 第 5 轮；现役：直连额度追加后可再 reserve。"""
    ctx, budget, _ = _exhaust(tmp_path)
    view = work_unit_grants.inspect(tmp_path, ctx.sid, budget.unit_id)
    assert view.available, view.reason
    work_unit_grants.apply(tmp_path, ctx.sid, budget.unit_id, _request(view))
    turn = budget.reserve(_sha('fifth'), run_id=ctx.run_id)
    budget.record_attempt(
        turn.turn_id, attempt_id='att-5', outcome='ok',
        request_fingerprint=_sha('fifth'))
    budget.save_response(turn.turn_id, {'final_response': '{"value":5}'})
    budget.mark_adopted(turn.turn_id)
    assert budget.used == 5


def test_second_grant_is_cumulative_and_cannot_change_prior_authorization(tmp_path):
    ctx, budget, _ = _exhaust(tmp_path)
    first = work_unit_grants.apply(
        tmp_path, ctx.sid, budget.unit_id,
        _request(work_unit_grants.inspect(tmp_path, ctx.sid, budget.unit_id)))
    # 直连耗尽追加后的第 5 次：不走 LlmClient 多档重采，避免一次失败烧多轮额度。
    fingerprint = _sha('fifth-fail')
    turn = budget.reserve(fingerprint, run_id=ctx.run_id)
    budget.record_attempt(
        turn.turn_id, attempt_id='att-5', outcome='error',
        request_fingerprint=fingerprint)
    ctx.ledger.record(
        sid=ctx.sid, node=ctx.node, model=_profile().model,
        code=ctx.code, cached=False, cache_policy=ctx.cache.name,
        outcome='error', prompt_tokens=10, completion_tokens=3,
        reasoning_tokens=2, purpose=ctx.node, attempt_id='att-5',
        request_fingerprint=fingerprint, latency_ms=12)
    budget.finish(turn.turn_id, outcome='failed')
    assert budget.used == 5 and budget.remaining == 0
    view = work_unit_grants.inspect(tmp_path, ctx.sid, budget.unit_id, additional=2)
    assert view.available, view.reason
    work_unit_grants.apply(tmp_path, ctx.sid, budget.unit_id, _request(view))
    assert budget.remaining == 2 and budget._read().content_limit == 7
    assert budget._read().grants[0] == first.grant


# ── R8a-5：agent 流执行身份三格的 owner 停止证明（canary 20260929b 反例） ──

def _append_ended_agent_run(base, *, run_id: str, epoch_id: str, sid: str) -> None:
    """手工落一条已结束且排空的 AgentRun 账（v2：started/epoch_opened/epoch_closed/ended）。"""
    from contracts.schema.orchestration import (
        AgentDeliveryOutcome, AgentRunEvent, AgentRunStart, ExecutionEpoch,
        RunContractV2,
    )
    from novel.orchestrator import agent as agent_mod

    profiles = {
        'schema_version': 4,
        'executor_default': {
            'kind': 'legacy', 'profile': {
                'provider': 'deepseek', 'model': 'deepseek-v4-pro',
                'reasoning_effort': 'high', 'thinking_mode': 'on'}},
        'executor_overrides': {},
    }
    agent_mod.append(base, AgentRunEvent(
        wire_version=2, run_id=run_id, kind='started', ts=10,
        start=AgentRunStart(sid=sid, base=str(base), argv=None, prompt=None,
                            authorized=True, egress_authorized=True),
        contract=RunContractV2.model_validate(
            {'sid': sid, 'base': str(base), 'authorized': True,
             'egress_authorized': True, 'profiles': profiles})))
    agent_mod.append(base, AgentRunEvent(
        wire_version=2, run_id=run_id, kind='epoch_opened', ts=11,
        epoch=ExecutionEpoch(
            epoch_id=epoch_id, ordinal=1, reason='initial',
            protocol_version='llm-executor/v1', code_version=prov.code_version(),
            identity_version=3, engine_kind='direct_llm',
            engine_version='r8a-test', session_run_id=run_id + '-session')))
    agent_mod.append(base, AgentRunEvent(
        wire_version=2, run_id=run_id, kind='epoch_closed', ts=20,
        epoch_id=epoch_id, exit_code=2, drain_state='drained'))
    agent_mod.append(base, AgentRunEvent(
        wire_version=2, run_id=run_id, kind='ended', ts=21, exit_code=2,
        outcome=AgentDeliveryOutcome(
            state='stopped', reason='额度耗尽停给人（测试夹具）', checked_at=21)))


def _exhaust_under_agent(base, *, run_id: str, epoch_id: str,
                         candidate_session: str):
    """agent 流耗尽四轮：成本行 run_id=AgentRun id、parent_run_id=候选根会话。"""
    from novel.orchestrator import agent as agent_mod

    _append_ended_agent_run(base, run_id=run_id, epoch_id=epoch_id,
                            sid='42')
    ctx = _ctx(base)
    ledger = CostLedger(path=ledger_path(base), run_id=run_id,
                        parent_run_id=candidate_session)
    ctx.ledger = ledger
    budget = WorkUnitBudget(base, ctx.sid, ctx.node)
    for index in range(4):
        fingerprint = _sha(f'agent-req-{index}')
        turn = budget.reserve(
            fingerprint, run_id=run_id, agent_run_id=run_id,
            agent_epoch_id=epoch_id,
            candidate_session_run_id=candidate_session)
        budget.record_attempt(
            turn.turn_id, attempt_id=f'a-att-{index}', outcome='error',
            request_fingerprint=fingerprint)
        ledger.record(
            sid=ctx.sid, node=ctx.node, model=_profile().model,
            code=ctx.code, cached=False, cache_policy=ctx.cache.name,
            outcome='error', prompt_tokens=10, completion_tokens=3,
            reasoning_tokens=2, purpose=ctx.node, attempt_id=f'a-att-{index}',
            request_fingerprint=fingerprint, latency_ms=12)
        budget.finish(turn.turn_id, outcome='failed')
    assert budget.used == 4
    return ctx, budget


def test_agent_flow_grant_closes_on_recorded_execution_identity(tmp_path):
    """R8a-5 正例：三格在案的轮，owner 证明按记录关系闭合——不再把成本行
    parent_run_id（候选根 epoch 会话）当 AgentRun（canary 20260929b 反例）。"""
    ctx, budget = _exhaust_under_agent(
        tmp_path, run_id='agent-run-r8a', epoch_id='epoch-r8a-1',
        candidate_session='candidate-epoch-session-1')
    view = work_unit_grants.inspect(tmp_path, ctx.sid, budget.unit_id)
    assert view.available, view.reason
    work_unit_grants.apply(tmp_path, ctx.sid, budget.unit_id, _request(view))
    assert budget.remaining == 1


def test_agent_flow_legacy_turns_close_on_producer_run_as_agent_run(tmp_path):
    """R8a-5 历史轮次：三格为空的 R8a 前轮，按「成本行 run_id＝turn.run_id＝
    已结束且排空的 AgentRun」闭合（canary 根的存量轮即此形态）。"""
    from novel.orchestrator import agent as agent_mod

    _append_ended_agent_run(tmp_path, run_id='agent-run-legacy',
                            epoch_id='epoch-legacy-1', sid='42')
    ctx = _ctx(tmp_path)
    ledger = CostLedger(path=ledger_path(tmp_path), run_id='agent-run-legacy',
                        parent_run_id='candidate-epoch-session-old')
    ctx.ledger = ledger
    budget = WorkUnitBudget(tmp_path, ctx.sid, ctx.node)
    for index in range(4):
        fingerprint = _sha(f'legacy-req-{index}')
        turn = budget.reserve(fingerprint, run_id='agent-run-legacy')
        budget.record_attempt(
            turn.turn_id, attempt_id=f'l-att-{index}', outcome='error',
            request_fingerprint=fingerprint)
        ledger.record(
            sid=ctx.sid, node=ctx.node, model=_profile().model,
            code=ctx.code, cached=False, cache_policy=ctx.cache.name,
            outcome='error', prompt_tokens=10, completion_tokens=3,
            reasoning_tokens=2, purpose=ctx.node, attempt_id=f'l-att-{index}',
            request_fingerprint=fingerprint, latency_ms=12)
        budget.finish(turn.turn_id, outcome='failed')
    view = work_unit_grants.inspect(tmp_path, ctx.sid, budget.unit_id)
    assert view.available, view.reason


def test_agent_flow_recorded_identity_must_exist_in_agent_account(tmp_path):
    """R8a-5 反例：三格在案但记录的 AgentRun 不在账上 ⇒ 显式拒绝（不静默放行）。"""
    from novel.orchestrator.work_unit_evidence import (
        cost_evidence, load_budget, owner_stop_proof,
    )

    ctx = _ctx(tmp_path)
    ledger = CostLedger(path=ledger_path(tmp_path), run_id='ghost-agent-run',
                        parent_run_id='candidate-session-ghost')
    budget = WorkUnitBudget(tmp_path, ctx.sid, ctx.node)
    turn = budget.reserve(
        _sha('ghost-req'), run_id='ghost-agent-run',
        agent_run_id='ghost-agent-run', agent_epoch_id='ghost-epoch',
        candidate_session_run_id='candidate-session-ghost')
    budget.record_attempt(
        turn.turn_id, attempt_id='g-att-0', outcome='error',
        request_fingerprint=_sha('ghost-req'))
    ledger.record(
        sid=ctx.sid, node=ctx.node, model=_profile().model,
        code=ctx.code, cached=False, cache_policy=ctx.cache.name,
        outcome='error', prompt_tokens=10, completion_tokens=3,
        reasoning_tokens=2, purpose=ctx.node, attempt_id='g-att-0',
        request_fingerprint=_sha('ghost-req'), latency_ms=12)
    budget.finish(turn.turn_id, outcome='failed')
    loaded = load_budget(tmp_path, ctx.sid, budget.unit_id)
    ledger_view = loaded._read()
    _costs, rows, owners = cost_evidence(loaded, ledger_view)
    with pytest.raises(WorkUnitRefused, match='AgentRun'):
        owner_stop_proof(loaded, ledger_view, owners, rows)
