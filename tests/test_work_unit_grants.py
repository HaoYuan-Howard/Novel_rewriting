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


def _ctx(base, node: str = 'stage2.global') -> RunContext:
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
        target = ctx.novel_dir / 'stage2' / 'global_grounding.json'
        target.parent.mkdir(exist_ok=True)
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
