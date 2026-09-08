"""永久写作授权绑定单次内容证明，不因连续审阅的后续 turn 漂移。"""
from dataclasses import asdict
import hashlib
import json

import pytest

from contracts.schema.orchestration import ReviewerActionRecord
from novel.orchestrator import reviewer_action_evidence as evidence, reviewer_tasks
from novel.work_units import WorkUnitBudget


def seed_commander_response(base, *, sid='42', node='stage5.review', scope='closure',
                            waiting_on='approval:stage5-closure', action=None,
                            run_id='original-parent', session_run_id='session',
                            business_sha256='2' * 64, profile=None):
    """保存真实 request/raw/receipt、会话与成本，供新工作单元恢复消费。"""
    from dataclasses import replace
    from novel import agent_session_audit as audit
    from novel.llm import ledger
    from tests.test_agent_session_audit import _start, _turn

    action = action or reviewer_tasks.ReviewerNodeAction(
        kind='node', node='stage5.review',
        flags=('--request-reviewer-rewrite', '@reviewer-input'),
        input_payload={'actor': '审阅者', 'reason': '明确返工范围'})
    prompt = '完整合成审阅工作包'
    prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()
    common = dict(role='commander', node=None, chapter=None, parent_run_id=run_id)
    if profile is not None:
        common.update(profile=profile, requested_model=profile.model,
                      requested_effort=profile.reasoning_effort)
    start = replace(_start(session_run_id=session_run_id, prompt_sha256=prompt_sha), **common)
    turn = replace(_turn(session_run_id=session_run_id, prompt_sha256=prompt_sha,
                         actual_model=profile.model if profile else 'gpt-5.6-sol',
                         actual_effort=profile.reasoning_effort if profile else 'max'), **common)
    attempt = replace(turn, kind='attempt', turn_index=None, delivery_attempts=1)
    budget = WorkUnitBudget(base, sid, node, 'commander', scope)
    reservation = budget.reserve('1' * 64, run_id=run_id, session_run_id=session_run_id,
                                 request_payload={'prompt': prompt, 'schema_sha256': turn.schema_sha256})
    budget.record_attempt(reservation.turn_id, attempt_id=attempt.attempt_id, outcome='ok',
                          provider_session_id=attempt.provider_session_id)
    response = {'schema': reviewer_tasks.REQUEST_SCHEMA, 'surface_seal': 'original-seal',
                'waiting_on': waiting_on, 'action': asdict(action)}
    raw = json.dumps(response, ensure_ascii=False)
    digest = hashlib.sha256((json.dumps(
        response, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()).hexdigest()
    kwargs = dict(run_id=run_id, session_run_id=session_run_id, sid=sid,
                  attempt_id=attempt.attempt_id, request_model=turn.requested_model,
                  requested_effort=turn.requested_effort, actual_model=turn.actual_model,
                  actual_effort=turn.actual_effort, code=turn.start_code_version,
                  protocol_version=turn.protocol_version, cli_version=turn.cli_version,
                  provider_session_id=turn.provider_session_id, usage=turn.usage.to_dict(),
                  outcome='ok', exit_code=0, latency_ms=20, response_sha256=digest,
                  activity=f'reviewer:{waiting_on}')
    budget.save_response(reservation.turn_id, {
        'final_response': raw, 'session_run_id': session_run_id,
        'business_sha256': business_sha256,
        'prepared_receipt': {'kind': 'commander-result-receipt-v1', 'cost_kwargs': kwargs,
                             'attempt_event': attempt.to_dict(), 'turn_event': turn.to_dict(),
                             'logical_prompt_sha256': prompt_sha, 'request_prompt': prompt}})
    for event in (start, attempt, turn):
        audit.append_event(base, event)
    ledger.append_commander_call(base, **kwargs)
    budget.save_recovery(reservation.turn_id, {
        'business_sha256': business_sha256, 'waiting_on': waiting_on, 'action': asdict(action),
        'action_sha256': reviewer_tasks.action_business_sha256(action),
        'provider_response_sha256': digest})
    fields = (dict(node=action.node, flags=action.flags, input_payload=action.input_payload)
              if isinstance(action, reviewer_tasks.ReviewerNodeAction)
              else dict(surface=action.surface, payload=action.payload))
    record = ReviewerActionRecord(
        sid=sid, run_id='recovery-parent', epoch_id='new-epoch',
        waiting_on=waiting_on, surface_seal='new-seal',
        business_sha256=business_sha256, **fields,
        reviewer_session_run_id=session_run_id, reviewer_seat_turn_id=reservation.turn_id,
        provider_response_sha256=digest, work_unit_id=budget.unit_id,
        work_unit_turn_id=reservation.turn_id, recorded_at=1)
    [cost] = ledger.read_jsonl_rows(ledger.ledger_path(base))
    return budget, reservation, record, cost


@pytest.fixture
def content_evidence(tmp_path):
    return seed_commander_response(tmp_path)


def test_sealed_response_stays_stable_after_adoption_and_later_content(tmp_path, content_evidence):
    budget, reservation, record, _cost = content_evidence
    before = evidence._closed_evidence_hashes(tmp_path, '42', record)
    budget.mark_adopted(reservation.turn_id)
    later = budget.reserve('5' * 64, run_id='original-parent', session_run_id='session')
    budget.save_response(later.turn_id, {'final_response': '下一轮回答'})
    assert evidence._closed_evidence_hashes(tmp_path, '42', record) == before
    assert evidence._record_evidence_path(tmp_path, '42', record) == budget.path


@pytest.mark.parametrize('field,value', [('response_sha256', 'f' * 64), ('run_id', 'recovery-parent'),
                                        ('provider_session_id', 'another-provider'), ('outcome', 'error')])
def test_work_unit_authority_still_requires_exact_original_cost(
        tmp_path, content_evidence, field, value):
    _budget, _reservation, record, cost = content_evidence
    cost[field] = value
    from novel.llm.ledger import ledger_path
    ledger_path(tmp_path).write_text(json.dumps(cost) + '\n', encoding='utf-8')
    with pytest.raises(reviewer_tasks.ReviewerTaskError, match='成本'):
        evidence._closed_evidence_hashes(tmp_path, '42', record)
