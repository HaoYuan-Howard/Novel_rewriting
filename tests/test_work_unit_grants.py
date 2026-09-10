"""真实 HTTP → 工作单元账 → SessionClient 的零费用额度链路。"""
from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from contracts.schema.api import WorkUnitGrantRequest
from novel.agents.session_client import SessionAgentClient
from novel.api.app import create_app
from novel.llm.client import LlmFailed
from novel.orchestrator import admission, work_unit_grants
from novel.work_units import WorkUnitBudget, WorkUnitBudgetExhausted, WorkUnitRefused
from tests.test_agent_session_client import _Answer, _FakeDriver, _ctx, _profile


def _exhaust(base, *, accepted_last=False):
    ctx = _ctx(base)
    replies = ['malformed-1', 'malformed-2', 'malformed-3', '{"value":4}' if accepted_last else 'malformed-4']
    driver = _FakeDriver(replies, session_id='source-' + ctx.run_id)
    client = SessionAgentClient(ctx, _profile(), driver)
    try:
        if accepted_last:
            client.ask_json('SYS', 'TASK', model_cls=_Answer, purpose=ctx.node, max_tokens=4096,
                            recovery_policy='stage2-knowledge-v2')
            checkpoint = client.accepted_checkpoint(ctx.node)
        else:
            with pytest.raises(LlmFailed) as failure:
                client.ask_json('SYS', 'TASK', model_cls=_Answer, purpose=ctx.node, max_tokens=4096,
                                recovery_policy='stage2-knowledge-v2')
            assert len(driver.calls) == 4, str(failure.value)
            checkpoint = None
        client.finish(process_state='ok' if accepted_last else 'failed',
                      delivery_state='delivered' if accepted_last else 'blocked')
    finally:
        client.close()
    assert len(driver.calls) == 4
    budget = WorkUnitBudget(base, ctx.sid, ctx.node)
    assert budget.used == 4 and budget.remaining == 0
    return ctx, budget, checkpoint


def _request(view, **changes):
    return WorkUnitGrantRequest(preview_token=view.preview_token, intent_id=uuid4().hex,
        additional=view.additional, reason='确认允许本工作单元继续一次修订', **changes)


def test_http_grant_survives_restart_without_reissuing_base_allowance(tmp_path):
    ctx, budget, _ = _exhaust(tmp_path)
    before = budget._read()
    costs = ctx.ledger.path.read_bytes()
    original_files = {str(path): path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    endpoint = f'/api/novels/{ctx.sid}/work-units/{budget.unit_id}/grant'
    with TestClient(create_app(tmp_path)) as api:
        preview = api.get(endpoint)
        assert preview.status_code == 200, preview.text
        view = preview.json()
        assert view['available'], view['reason']
        assert view['costs']['calls'] == 4 and view['costs']['total_tokens'] == 52
        assert {str(path): path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()} == original_files
        request = dict(preview_token=view['preview_token'], intent_id=uuid4().hex,
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
    driver = _FakeDriver(['{"value":9}'], session_id='new-' + later.run_id)
    client = SessionAgentClient(later, _profile(), driver)
    try:
        assert client.ask_json('SYS', 'TASK', model_cls=_Answer, purpose=later.node,
                               max_tokens=4096, recovery_policy='stage2-knowledge-v2').value == 9
        client.finish(process_state='ok', delivery_state='delivered')
    finally:
        client.close()
    assert len(driver.calls) == 1 and budget.used == 5 and budget.remaining == 0
    with pytest.raises(WorkUnitBudgetExhausted):
        budget.reserve('9' * 64, run_id='another-run')


@pytest.mark.parametrize('damage', ['surface', 'ledger', 'live_owner', 'delivered', 'unknown', 'group', 'producer', 'beacon_tail'])
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
    elif damage in ('delivered', 'unknown'):
        budget.finish(budget.latest_turn().turn_id, outcome=damage)
    elif damage == 'group':
        budget.save_group_producer_recovery({'target': '未完成的候选组'})
    elif damage == 'producer':
        budget.save_producer_recovery(budget.latest_turn().turn_id, {'target': '未补提交的候选'})
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
        work_unit_grants.apply(tmp_path, ctx.sid, budget.unit_id,
                              request.model_copy(update={'additional': 1}))
    assert budget._read().content_limit == 8 and len(budget._read().grants) == 1


def test_grant_allows_fifth_logical_checkpoint_turn_without_resetting_links(tmp_path):
    ctx, budget, checkpoint = _exhaust(tmp_path, accepted_last=True)
    assert checkpoint['accepted_turn_index'] == 4
    view = work_unit_grants.inspect(tmp_path, ctx.sid, budget.unit_id)
    assert view.available, view.reason
    work_unit_grants.apply(tmp_path, ctx.sid, budget.unit_id, _request(view))
    driver = _FakeDriver(['{"value":5}'], session_id='recovered-' + ctx.run_id)
    client = SessionAgentClient(ctx, _profile(), driver)
    try:
        client.restore_checkpoint_acceptance(ctx.node, checkpoint, system='SYS', user='TASK',
            max_tokens=4096, recovery_policy='stage2-knowledge-v2')
        assert client.queue_restored_code_validation_feedback([{'rule': 'synthetic_evidence', 'count': 1}], purpose=ctx.node)
        result = client.ask_json('SYS', 'TASK', model_cls=_Answer, purpose=ctx.node,
                                 max_tokens=4096, recovery_policy='stage2-knowledge-v2')
        assert result.value == 5
        accepted = client.accepted_checkpoint(ctx.node)
        client.finish(process_state='ok', delivery_state='delivered')
    finally:
        client.close()
    assert accepted['accepted_turn_index'] == 5 and len(accepted['attempt_links']) == 5
    assert budget.used == 5


def test_second_grant_is_cumulative_and_cannot_change_prior_authorization(tmp_path):
    ctx, budget, _ = _exhaust(tmp_path)
    first = work_unit_grants.apply(tmp_path, ctx.sid, budget.unit_id,
        _request(work_unit_grants.inspect(tmp_path, ctx.sid, budget.unit_id)))
    next_ctx = _ctx(tmp_path)
    driver = _FakeDriver(['still-malformed'], session_id='second-' + next_ctx.run_id)
    client = SessionAgentClient(next_ctx, _profile(), driver)
    try:
        with pytest.raises(LlmFailed):
            client.ask_json('SYS', 'TASK', model_cls=_Answer, purpose=next_ctx.node,
                max_tokens=4096, recovery_policy='stage2-knowledge-v2')
        client.finish(process_state='failed', delivery_state='blocked')
    finally:
        client.close()
    assert budget.used == 5 and len(driver.calls) == 1
    view = work_unit_grants.inspect(tmp_path, ctx.sid, budget.unit_id, additional=2)
    assert view.available, view.reason
    work_unit_grants.apply(tmp_path, ctx.sid, budget.unit_id, _request(view))
    assert budget.remaining == 2 and budget._read().content_limit == 7
    assert budget._read().grants[0] == first.grant
    assert view.costs.calls == 5 and view.costs.total_tokens == 65
