#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent E2E 验收器的零模型身份联结测试。"""
from __future__ import annotations

import copy
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from contracts.invariants import AGENT_EXECUTOR_NODES
from contracts.schema.orchestration import (
    AgentDeliveryOutcome, AgentRunEvent, AgentRunStart, ProgressEvent,
)
from novel import agent_session_audit as audit
from novel.orchestrator import admission, beacon
from novel.llm.ledger import append_commander_call, ledger_path, read_jsonl_rows
from tools import verify_agent_e2e as verifier


_RUN_ID = 'commander-1'
_SID = '42'
_SHA = 'a' * 64
_PROFILE = {
    'driver': 'codex',
    'model': 'gpt-5.6-sol',
    'reasoning_effort': 'max',
    'execution_mode': 'standard',
}


def _session(
    *, session_id: str, role: str, attempt_id: str,
    node: str | None, actual_effort: str | None = 'max',
    parent_run_id: str = _RUN_ID,
) -> audit.AgentSessionRecord:
    common = {
        'session_run_id': session_id,
        'parent_run_id': parent_run_id,
        'role': role,
        'node': node,
        'chapter': None,
        'subject_sha256': _SHA,
        'profile': _PROFILE,
        'requested_model': _PROFILE['model'],
        'requested_effort': _PROFILE['reasoning_effort'],
        'protocol_version': 'agent-session/v1',
        'start_code_version': 'clean-code',
    }
    start = audit.AgentSessionEvent(
        **common, prompt_sha256=_SHA, schema_sha256=_SHA,
        kind='started', ts=1)
    turn = audit.AgentSessionEvent(
        **common, provider_session_id=f'provider-{session_id}',
        turn_index=1, attempt_id=attempt_id,
        actual_model=_PROFILE['model'], actual_effort=actual_effort,
        prompt_sha256=_SHA, schema_sha256=_SHA,
        kind='turn', ts=2)
    end = audit.AgentSessionEvent(
        **common, provider_session_id=f'provider-{session_id}',
        actual_model=_PROFILE['model'], actual_effort=actual_effort,
        end_code_version='clean-code', process_state='ok',
        delivery_state='delivered', kind='ended', ts=3)
    return audit.AgentSessionRecord(
        session_run_id=session_id,
        start=start,
        turns=(turn,),
        end=end,
        provider_session_ids=(f'provider-{session_id}',),
    )


def _closed_chain(*, producer_run_id: str | None = None):
    commander_attempt = 'attempt-commander'
    sessions = [_session(
        session_id='session-commander', role='commander',
        attempt_id=commander_attempt, node=None)]
    ledger = [{
        'run_id': _RUN_ID, 'sid': _SID, 'role': 'commander',
        'node': None, 'purpose': 'commander', 'chapter': None,
        'attempt_id': commander_attempt, 'outcome': 'ok',
        'session_run_id': 'session-commander',
        'model': _PROFILE['model'],
        'reasoning_effort': _PROFILE['reasoning_effort'],
        'actual_reasoning_effort': _PROFILE['reasoning_effort'],
    }]
    adoptions = []
    executor_run_id = producer_run_id or _RUN_ID
    for index, node in enumerate(AGENT_EXECUTOR_NODES, start=1):
        attempt_id = f'attempt-{index}'
        # 一条 delivered turn 可合法保留 actual effort unknown；
        # requested 不得回填它。
        actual_effort = None if index == 1 else _PROFILE['reasoning_effort']
        sessions.append(_session(
            session_id=f'session-{index}', role='executor',
            attempt_id=attempt_id, node=node,
            actual_effort=actual_effort, parent_run_id=executor_run_id))
        ledger_row = {
            'run_id': executor_run_id, 'sid': _SID, 'node': node,
            'purpose': node, 'chapter': None, 'attempt_id': attempt_id,
            'outcome': 'ok', 'model': _PROFILE['model'],
            'reasoning_effort': _PROFILE['reasoning_effort'],
        }
        adoption = {
            'kind': 'artifact_adoption',
            'run_id': executor_run_id, 'sid': _SID, 'node': node,
            'purpose': node, 'chapter': None, 'attempt_id': attempt_id,
            'artifact': f'artifact-{index}',
            'artifact_fingerprint': _SHA,
        }
        if producer_run_id is not None:
            ledger_row['parent_run_id'] = _RUN_ID
            adoption['parent_run_id'] = _RUN_ID
        ledger.append(ledger_row)
        adoptions.append(adoption)
    return ledger, adoptions, sessions


def test_all_eight_agent_nodes_close_ledger_session_and_adoption_chain():
    ledger, adoptions, sessions = _closed_chain()
    result = verifier.linkage_check(
        run_id=_RUN_ID, sid=_SID,
        ledger_rows=ledger, audit_rows=adoptions, sessions=sessions)
    assert result['state'] == 'pass'
    assert result['facts']['missing_adoption_nodes'] == []
    assert result['facts']['adoption_unlinked'] == 0


def test_epoch_producer_rows_link_through_explicit_agent_parent():
    producer_run_id = 'epoch-producer-1'
    ledger, adoptions, sessions = _closed_chain(
        producer_run_id=producer_run_id)

    result = verifier.linkage_check(
        run_id=_RUN_ID, sid=_SID,
        ledger_rows=ledger, audit_rows=adoptions, sessions=sessions,
        producer_run_ids={producer_run_id})

    assert result['state'] == 'pass'
    assert result['facts']['adoption_unlinked'] == 0
    assert result['facts']['turn_mismatches'] == 0


def test_independent_commander_seats_close_without_claiming_book_delivery():
    ledger, adoptions, sessions = _closed_chain()
    first = sessions[0]
    sessions[0] = replace(first, end=replace(first.end, delivery_state='blocked'))
    second = _session(
        session_id='reviewer-two', role='commander',
        attempt_id='commander-two', node=None)
    sessions.append(replace(second, end=replace(second.end, delivery_state='blocked')))
    ledger.append({**ledger[0], 'attempt_id': 'commander-two',
                   'session_run_id': 'reviewer-two'})
    result = verifier.linkage_check(
        run_id=_RUN_ID, sid=_SID, ledger_rows=ledger,
        audit_rows=adoptions, sessions=sessions)
    assert result['state'] == 'pass'
    ledger.pop()
    assert verifier.linkage_check(
        run_id=_RUN_ID, sid=_SID, ledger_rows=ledger,
        audit_rows=adoptions, sessions=sessions)['state'] == 'fail'


@pytest.mark.parametrize('cli_version, model, expected', [
    ('codex-cli 0.test', 'unknown', 'pass'),
    (None, 'unknown', 'fail'),
    ('codex-cli 0.test', 'different-model', 'fail'),
])
def test_unreported_actual_model_uses_registered_driver_evidence(
        cli_version, model, expected):
    ledger, adoptions, sessions = _closed_chain()
    record = sessions[1]
    record = replace(record, turns=(replace(
        record.turns[0], actual_model=None, cli_version=cli_version),))
    sessions[1] = record
    ledger[1]['model'] = model
    ledger[1]['request_model'] = _PROFILE['model']
    result = verifier.linkage_check(
        run_id=_RUN_ID, sid=_SID, ledger_rows=ledger,
        audit_rows=adoptions, sessions=sessions)
    assert result['state'] == expected
    assert record.turns[0].actual_model is None
    assert ledger[1]['model'] == model


def test_resume_mode_discloses_scope_and_still_requires_each_executed_adoption():
    ledger, adoptions, sessions = _closed_chain()
    ledger, adoptions, sessions = ledger[:2], adoptions[:1], sessions[:2]
    kwargs = dict(run_id=_RUN_ID, sid=_SID, ledger_rows=ledger,
                  audit_rows=adoptions, sessions=sessions)
    assert verifier.linkage_check(**kwargs)['state'] == 'fail'
    resumed = verifier.linkage_check(**kwargs, resume=True)
    assert resumed['state'] == 'pass'
    assert resumed['facts']['scope'] == 'current_run_attempts'
    kwargs['audit_rows'] = []
    assert verifier.linkage_check(**kwargs, resume=True)['state'] == 'fail'


def test_typed_approvals_are_checked_even_when_service_checklist_disagrees(monkeypatch):
    approval = SimpleNamespace(
        gate=dict.fromkeys(('s0', 's1', 's2', 's4', 's5', 's6', 's7'), True),
        seen={'read': True, 'global': True})
    approval.gate.update(s6=False, s7=False)
    seen_nodes = []

    def current(_base, _sid, node):
        seen_nodes.append(node)
        return True

    monkeypatch.setattr(verifier.queue, '_human_gate_satisfied', current)
    assert verifier.current_approval_check(None, _SID, approval)['state'] == 'pass'
    assert seen_nodes == ['stage6.approve', 'stage7.approve']
    assert approval.gate['s6'] is False
    approval.gate.update(s6=True, s7=True)
    monkeypatch.setattr(verifier.queue, '_human_gate_satisfied', lambda *_: False)
    result = verifier.current_approval_check(None, _SID, approval)
    assert result['state'] == 'fail'
    assert result['facts']['missing_gates'] == ['s6', 's7']


@pytest.mark.parametrize('start_code, epoch_code, expected', [
    ('a' * 40, 'a' * 40, 'pass'),
    ('unknown', 'unknown', 'fail'),
    ('a' * 40 + '-dirty.123', 'a' * 40 + '-dirty.123', 'fail'),
    ('a' * 40, 'b' * 40, 'fail'),
])
def test_zero_wire_resume_uses_durable_code_without_inventing_an_experiment(
        start_code, epoch_code, expected):
    identity = dict(code_identities=[], experiment_valid=False,
                    experiment_invalid_reasons=['没有模型调用'])
    outer = SimpleNamespace(start=SimpleNamespace(code_version=start_code),
                            epochs=[SimpleNamespace(code_version=epoch_code)])
    result = verifier.execution_identity_check(
        identity, outer, resume=True, ledger_lines=0)
    assert result['state'] == expected
    assert result['facts']['experiment_valid'] is False
    assert verifier.execution_identity_check(
        identity, outer, resume=False, ledger_lines=0)['state'] == 'fail'
    assert verifier.execution_identity_check(
        identity, outer, resume=True, ledger_lines=1)['state'] == 'fail'


@pytest.mark.parametrize('failure', [None, 'missing_close', 'duplicate_close',
                                     'undrained', 'end_before_close'])
def test_v2_epoch_lifecycle_checks_real_sequence_without_legacy_telemetry(failure):
    def event(kind, **values):
        return SimpleNamespace(run_id=_RUN_ID, kind=kind, **values)

    opened = event('epoch_opened', epoch=SimpleNamespace(epoch_id='epoch-1'))
    closed = event('epoch_closed', epoch_id='epoch-1', drain_state='drained', exit_code=0)
    ended = event('ended', exit_code=0)
    events = [opened, closed, ended]
    if failure == 'missing_close':
        events = [opened, ended]
    elif failure == 'duplicate_close':
        events = [opened, closed, closed, ended]
    elif failure == 'undrained':
        closed.drain_state = 'reconciled'
    elif failure == 'end_before_close':
        events = [opened, ended, closed]
    result = verifier.epoch_lifecycle_check(events, _RUN_ID)
    assert result['state'] == ('pass' if failure is None else 'fail')
    assert result['facts']['source'] == 'agent_run_v2_epochs'


def test_missing_one_paid_node_adoption_is_not_a_complete_agent_only_book():
    ledger, adoptions, sessions = _closed_chain()
    adoptions = [
        row for row in adoptions if row['node'] != 'stage7.rename'
    ]
    result = verifier.linkage_check(
        run_id=_RUN_ID, sid=_SID,
        ledger_rows=ledger, audit_rows=adoptions, sessions=sessions)
    assert result['state'] == 'fail'
    assert result['facts']['missing_adoption_nodes'] == ['stage7.rename']


def test_attempt_identity_collision_is_unknown_not_a_business_failure():
    ledger, adoptions, sessions = _closed_chain()
    ledger.append(dict(ledger[1]))
    result = verifier.linkage_check(
        run_id=_RUN_ID, sid=_SID,
        ledger_rows=ledger, audit_rows=adoptions, sessions=sessions)
    assert result['state'] == 'unknown'
    assert result['facts']['identity_collision'] is True


def test_overall_keeps_fail_and_unknown_separate():
    assert verifier._overall([
        {'state': 'pass'}, {'state': 'pass'}]) == 'pass'
    assert verifier._overall([
        {'state': 'pass'}, {'state': 'unknown'}]) == 'unknown'
    assert verifier._overall([
        {'state': 'unknown'}, {'state': 'fail'}]) == 'fail'


def test_execution_lifecycle_requires_closed_gate_and_no_active_work(tmp_path):
    admission.open_gate(tmp_path, _RUN_ID, _SID)
    claim = admission.claim(
        tmp_path, _RUN_ID, _SID, node='stage2.global',
        claim_sha256=_SHA, claim_id='claim-1')
    admission.close_gate(tmp_path, _RUN_ID)
    assert verifier.execution_lifecycle_check(
        tmp_path, _RUN_ID, _SID)['state'] == 'fail'

    admission.release(tmp_path, claim)
    admission.mark_drained(tmp_path, _RUN_ID)
    result = verifier.execution_lifecycle_check(tmp_path, _RUN_ID, _SID)
    assert result['state'] == 'pass'
    assert result['facts']['active_claims'] == 0


def test_execution_lifecycle_rejects_an_open_node_execution(tmp_path):
    admission.open_gate(tmp_path, _RUN_ID, _SID)
    admission.close_gate(tmp_path, _RUN_ID)
    beacon.append(tmp_path, ProgressEvent(
        sid=_SID, node='stage2.global', phase='start', ts=1,
        run_id=_RUN_ID, execution_id='execution-1'))

    result = verifier.execution_lifecycle_check(tmp_path, _RUN_ID, _SID)
    assert result['state'] == 'fail'
    assert result['facts']['open_executions'] == 1


def test_execution_lifecycle_treats_a_broken_admission_log_as_unknown(tmp_path):
    path = admission.admission_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'{"wire_version":1}\nnot-json\n')

    result = verifier.execution_lifecycle_check(tmp_path, _RUN_ID, _SID)
    assert result['state'] == 'unknown'
    assert result['facts']['error_type']


def test_workflow_readings_are_scoped_to_the_requested_run(tmp_path):
    """验收器传入 run_id；同 sid 的旧失败不能进入本轮 stability 读数。"""
    old_run = 'old-run'
    book = tmp_path / f'验收样书_{_SID}'
    book.mkdir()
    service = tmp_path / '_service'
    service.mkdir()

    old_ledger = [
        {
            'sid': _SID, 'run_id': old_run, 'node': 'stage2.global',
            'purpose': 'stage2.global', 'chapter': None,
            'attempt_id': f'old-{index}', 'code': 'old-code-dirty',
            'cached': False, 'outcome': 'error', 'error_kind': 'agent_timeout',
            'prompt_tokens': 50_000, 'completion_tokens': 0,
            'model': 'old-model', 'reasoning_effort': 'max',
        }
        for index in range(25)
    ]
    current_ledger = {
        'sid': _SID, 'run_id': _RUN_ID, 'node': 'stage2.global',
        'purpose': 'stage2.global', 'chapter': None,
        'attempt_id': 'current-attempt', 'code': 'clean-code',
        'cached': False, 'outcome': 'ok', 'prompt_tokens': 73,
        'completion_tokens': 19, 'reasoning_tokens': 7,
        'model': _PROFILE['model'], 'reasoning_effort': 'max',
    }
    (service / 'ledger.jsonl').write_text(
        '\n'.join(json.dumps(row) for row in [*old_ledger, current_ledger]) + '\n',
        encoding='utf-8')

    old_audit = [
        {
            'sid': _SID, 'run_id': old_run, 'kind': 'gate_decision',
            'node': 'stage2.global', 'purpose': 'stage2.global', 'chapter': None,
            'attempt_id': f'old-{index}', 'source': 'wire',
            'decision': 'rejected', 'gate': 'validator', 'resample': True,
        }
        for index in range(25)
    ]
    current_audit = [
        {
            'sid': _SID, 'run_id': _RUN_ID, 'kind': 'gate_decision',
            'node': 'stage2.global', 'purpose': 'stage2.global', 'chapter': None,
            'attempt_id': 'current-attempt', 'source': 'wire',
            'decision': 'accepted', 'resample': False,
        },
        {
            'sid': _SID, 'run_id': _RUN_ID, 'kind': 'artifact_adoption',
            'node': 'stage2.global', 'purpose': 'stage2.global', 'chapter': None,
            'attempt_id': 'current-attempt',
        },
    ]
    (service / 'llm_audit.jsonl').write_text(
        '\n'.join(json.dumps(row) for row in [*old_audit, *current_audit]) + '\n',
        encoding='utf-8')

    for index in range(25):
        beacon.append(tmp_path, ProgressEvent(
            sid=_SID, run_id=old_run, execution_id=f'old-execution-{index}',
            node='stage2.global', phase='start', ts=index * 10))
        beacon.append(tmp_path, ProgressEvent(
            sid=_SID, run_id=old_run, execution_id=f'old-execution-{index}',
            node='stage2.global', phase='end', ts=index * 10 + 9, exit_code=70))
    beacon.append(tmp_path, ProgressEvent(
        sid=_SID, run_id=_RUN_ID, execution_id='current-execution',
        node='stage2.global', phase='start', ts=1_000))
    beacon.append(tmp_path, ProgressEvent(
        sid=_SID, run_id=_RUN_ID, execution_id='current-execution',
        node='stage2.global', phase='end', ts=1_004, exit_code=0))

    metrics = verifier._workflow_metrics_for_run(tmp_path, _SID, _RUN_ID)
    assert metrics['run_id'] == _RUN_ID
    assert metrics['ledger']['lines'] == 1
    assert metrics['ledger']['wire_error'] == 0
    assert metrics['ledger']['prompt_tokens'] == 73
    assert metrics['audit']['gate_by_decision'] == {'accepted': 1}
    assert metrics['audit']['resamples'] == 0
    assert metrics['audit']['first_call_landing']['stage2.global']['first_call'] == 1
    assert metrics['cli']['events'] == 2
    assert metrics['cli']['per_node']['stage2.global']['exit_codes'] == {'0': 1}
    assert metrics['identity']['code_identities'] == ['clean-code']
    assert metrics['identity']['experiment_valid'] is True



def _commander_retry_chain(tmp_path, *, failures=(1,), failure_kind='timeout',
                           first_provider=True, usage_without_provider=False,
                           known_prompt_tokens=10, end_error_kind=None):
    """用正式成本/会话 writer 铸造 R-11.26 尝试账；不发模型、不运行验收 CLI。"""
    ledger, adoptions, sessions = _closed_chain()
    common = {
        'session_run_id': 'session-commander', 'parent_run_id': _RUN_ID,
        'role': 'commander', 'node': None, 'chapter': None,
        'subject_sha256': _SHA, 'profile': _PROFILE,
        'requested_model': _PROFILE['model'], 'requested_effort': _PROFILE['reasoning_effort'],
        'protocol_version': 'codex-jsonl-v1', 'start_code_version': 'clean-code',
        'skill_sha256': 'c' * 64, 'schema_sha256': 'd' * 64,
    }
    start = audit.AgentSessionEvent(**common, prompt_sha256=_SHA, kind='started', ts=1)
    audit.append_event(tmp_path, start)
    invocation = 0
    turn_index = 0
    for failures_in_group in failures:
        for ordinal in range(1, failures_in_group + 2):
            invocation += 1
            succeeds = ordinal == failures_in_group + 1
            provider = None if invocation == 1 and not first_provider else 'provider-commander'
            usage = (audit.AgentSessionUsage(prompt_tokens=known_prompt_tokens, completion_tokens=3)
                     if succeeds or (provider is None and usage_without_provider) else None)
            # 未获 provider 时下一次是原请求 relaunch；有 provider 后使用独立
            # resume prompt。只有本次 attempt 与本次 turn 的 prompt 必须相同。
            prompt = _SHA if turn_index == 0 else f'{invocation:064x}'
            attempt_id = f'retry-attempt-{invocation}'
            error = None if succeeds else failure_kind
            event = audit.AgentSessionEvent(
                **common, prompt_sha256=prompt, attempt_id=attempt_id,
                provider_session_id=provider, usage=usage, error_kind=error,
                delivery_attempts=ordinal, kind='attempt', ts=2 * invocation)
            audit.append_event(tmp_path, event)
            append_commander_call(
                tmp_path, run_id=_RUN_ID, sid=_SID, session_run_id='session-commander',
                attempt_id=attempt_id, request_model=_PROFILE['model'],
                requested_effort=_PROFILE['reasoning_effort'], code='clean-code',
                protocol_version='codex-jsonl-v1', provider_session_id=provider,
                usage=None if usage is None else usage.to_dict(),
                outcome='ok' if succeeds else 'error', exit_code=0 if succeeds else 1,
                error_kind=error, response_sha256='b' * 64 if succeeds else None)
            if provider is not None:
                turn_index += 1
                audit.append_commander_turn(tmp_path, audit.AgentSessionEvent(
                    **common, prompt_sha256=prompt, attempt_id=attempt_id,
                    provider_session_id=provider, usage=usage, error_kind=error,
                    turn_index=turn_index, kind='turn', ts=2 * invocation + 1))
    audit.append_event(tmp_path, audit.AgentSessionEvent(
        **common, prompt_sha256=_SHA, provider_session_id='provider-commander',
        end_code_version='clean-code', process_state='ok', delivery_state='blocked',
        error_kind=end_error_kind, kind='ended', ts=2 * invocation + 2))
    [sessions[0]] = audit.fold(audit.read_events(tmp_path))
    ledger = [*read_jsonl_rows(ledger_path(tmp_path)), *ledger[1:]]
    return ledger, adoptions, sessions


def _retry_check(values):
    ledger, adoptions, sessions = values
    before = copy.deepcopy(values)
    result = verifier.linkage_check(
        run_id=_RUN_ID, sid=_SID, ledger_rows=ledger, audit_rows=adoptions, sessions=sessions)
    assert values == before, '验收只能读取，原失败与未知用量不能被归一改写'
    return result


@pytest.mark.parametrize('failure_kind', [
    'timeout', 'connection', 'server_error', 'rate_limit', 'provider_transient',
])
def test_recovered_commander_attempts_close_without_erasing_the_failure(tmp_path, failure_kind):
    values = _commander_retry_chain(tmp_path, failure_kind=failure_kind)
    result = _retry_check(values)
    assert result['state'] == 'pass'
    assert result['facts']['recovered_commander_attempts'] == 1
    assert result['facts']['commander_attempt_rows'] == 2
    assert result['facts']['commander_unknown_usage_attempts'] == 1
    failure, success = values[0][:2]
    assert failure['outcome'] == 'error' and success['outcome'] == 'ok'
    assert failure['prompt_tokens'] is None and failure['completion_tokens'] is None
    # 现役 Commander 通用成本格不是交付计数，不得据它们否定真实 1→2 attempt。
    assert failure['attempt'] == success['attempt'] == 1
    assert failure['max_attempts'] == success['max_attempts'] == 1
    assert failure['retryable'] is success['retryable'] is False


@pytest.mark.parametrize('usage_known', [False, True])
def test_relaunch_failure_without_provider_has_cost_and_attempt_but_no_turn(tmp_path, usage_known):
    values = _commander_retry_chain(
        tmp_path, first_provider=False, usage_without_provider=usage_known)
    result = _retry_check(values)
    assert result['state'] == 'pass'
    assert result['facts']['commander_failures_without_provider_turn'] == 1
    assert result['facts']['recovered_commander_attempts'] == 1
    assert result['facts']['commander_unknown_usage_attempts'] == int(not usage_known)
    assert len(values[2][0].attempts) == 2 and len(values[2][0].turns) == 1
    assert values[0][0]['provider_session_id'] is None
    assert values[0][0]['prompt_tokens'] == (10 if usage_known else None)


@pytest.mark.parametrize('groups', [(2,), (1, 0, 1), (0, 1)])
def test_delivery_attempt_ordinals_restart_only_after_a_completed_group(tmp_path, groups):
    values = _commander_retry_chain(tmp_path, failures=groups)
    result = _retry_check(values)
    assert result['state'] == 'pass'
    assert result['facts']['recovered_commander_attempts'] == sum(groups)
    assert result['facts']['commander_attempt_rows'] == sum(groups) + len(groups)


@pytest.mark.parametrize('kind', [
    'authentication', 'usage_limit', 'permission', 'agent_schema', 'protocol',
    'provider_session_reused', 'agent_command', 'unknown',
])
def test_later_success_never_launders_nonretryable_commander_failure(tmp_path, kind):
    # provider_session_reused 是 fold 专用所有权反例；这里在已完整折叠的
    # 夹具上改两账为同一分类，专门验证验收白名单仍不能接受它。
    values = _commander_retry_chain(tmp_path)
    ledger, _adoptions, sessions = values
    ledger[0].update(error_kind=kind, error=kind)
    record = sessions[0]
    sessions[0] = replace(record,
        attempts=(replace(record.attempts[0], error_kind=kind), *record.attempts[1:]),
        turns=(replace(record.turns[0], error_kind=kind), *record.turns[1:]))
    assert _retry_check(values)['state'] == 'fail'


@pytest.mark.parametrize('change', [
    'missing_cost', 'missing_attempt', 'missing_failure_turn', 'extra_turn',
    'wrong_session', 'wrong_parent', 'wrong_code', 'wrong_model', 'wrong_effort',
    'wrong_provider', 'wrong_schema', 'wrong_skill', 'wrong_prompt',
    'usage_zero_fill', 'turn_usage', 'failed_response', 'ordinal_gap',
    'ordinal_reset_before_success', 'unrecovered', 'end_failed', 'end_unknown',
])
def test_commander_recovery_requires_every_original_identity_and_terminal_success(tmp_path, change):
    values = _commander_retry_chain(tmp_path)
    ledger, _adoptions, sessions = values
    record = sessions[0]
    if change == 'missing_cost':
        ledger.pop(0)
    elif change == 'missing_attempt':
        sessions[0] = replace(record, attempts=record.attempts[1:])
    elif change == 'missing_failure_turn':
        sessions[0] = replace(record, turns=record.turns[1:])
    elif change == 'extra_turn':
        sessions[0] = replace(record, turns=(*record.turns,
            replace(record.turns[-1], attempt_id='unissued', turn_index=3)))
    elif change == 'wrong_session':
        ledger[0]['session_run_id'] = 'another-logical-session'
    elif change == 'wrong_parent':
        ledger[0]['run_id'] = 'another-parent'
    elif change in {'wrong_code', 'wrong_model', 'wrong_effort', 'wrong_provider'}:
        field = {'wrong_code': 'code', 'wrong_model': 'model',
                 'wrong_effort': 'actual_reasoning_effort',
                 'wrong_provider': 'provider_session_id'}[change]
        ledger[0][field] = 'different-identity'
    elif change in {'wrong_schema', 'wrong_skill', 'wrong_prompt'}:
        field = {'wrong_schema': 'schema_sha256', 'wrong_skill': 'skill_sha256',
                 'wrong_prompt': 'prompt_sha256'}[change]
        sessions[0] = replace(record, attempts=(
            replace(record.attempts[0], **{field: '9' * 64}), *record.attempts[1:]))
    elif change == 'usage_zero_fill':
        ledger[0]['prompt_tokens'] = 0
    elif change == 'turn_usage':
        sessions[0] = replace(record, turns=(replace(
            record.turns[0], usage=audit.AgentSessionUsage(prompt_tokens=0)), *record.turns[1:]))
    elif change == 'failed_response':
        ledger[0]['response_sha256'] = 'a' * 64
    elif change == 'ordinal_gap':
        sessions[0] = replace(record, attempts=(record.attempts[0],
            replace(record.attempts[1], delivery_attempts=3)))
    elif change == 'ordinal_reset_before_success':
        sessions[0] = replace(record, attempts=(record.attempts[0],
            replace(record.attempts[1], delivery_attempts=1)))
    elif change == 'unrecovered':
        ledger.pop(1)
        sessions[0] = replace(record, attempts=record.attempts[:1], turns=record.turns[:1])
    elif change == 'end_failed':
        sessions[0] = replace(record, end=replace(record.end, process_state='failed'))
    else:
        sessions[0] = replace(record, end=replace(record.end, delivery_state='unknown'))
    assert _retry_check(values)['state'] == 'fail'


def test_commander_retry_above_the_registered_limit_stays_failed(tmp_path):
    assert _retry_check(_commander_retry_chain(tmp_path, failures=(3,)))['state'] == 'fail'


@pytest.mark.parametrize('which', ['cost', 'attempt', 'cross_session'])
def test_commander_attempt_collisions_remain_unknown(tmp_path, which):
    values = _commander_retry_chain(tmp_path)
    ledger, _adoptions, sessions = values
    if which == 'cost':
        ledger.append(dict(ledger[0]))
    elif which == 'attempt':
        sessions[0] = replace(sessions[0], attempts=(
            *sessions[0].attempts, sessions[0].attempts[0]))
    else:
        sessions[1] = replace(sessions[1], attempts=(replace(
            sessions[0].attempts[0], session_run_id=sessions[1].session_run_id),))
    result = _retry_check(values)
    assert result['state'] == 'unknown'
    assert result['facts']['identity_collision'] is True


def test_legacy_success_history_is_compatible_but_missing_attempt_failure_is_not(tmp_path):
    legacy = _closed_chain()
    assert _retry_check(legacy)['state'] == 'pass'
    values = _commander_retry_chain(tmp_path)
    values[2][0] = replace(values[2][0], attempts=())
    assert _retry_check(values)['state'] == 'fail'


@pytest.mark.parametrize('raw_value', [True, 1.0])
def test_raw_usage_cannot_equal_typed_integer_by_python_numeric_coercion(tmp_path, raw_value):
    values = _commander_retry_chain(tmp_path, known_prompt_tokens=1)
    values[0][1]['prompt_tokens'] = raw_value
    assert _retry_check(values)['state'] == 'fail'


@pytest.mark.parametrize('end_error_kind, stopped', [
    ('commander_reviewer_binding_changed', False),
    ('commander_reviewer_no_progress', True),
    ('reviewer_seat_budget', True),
    ('commander_reviewer_request', True),
])
def test_transport_linkage_discloses_end_reason_but_cannot_override_stopped_outer_run(
        tmp_path, end_error_kind, stopped):
    values = _commander_retry_chain(tmp_path, end_error_kind=end_error_kind)
    linkage = _retry_check(values)
    assert linkage['state'] == 'pass'
    assert linkage['facts']['commander_end_error_kinds'] == {end_error_kind: 1}
    # 正式 producer 分开记录两轴：上述终止拒因会使 parent 停止；binding
    # 变化则允许 planner 换窗继续。这里通过真实事件 reader/fold 消费对应终态。
    start = AgentRunStart(
        sid=_SID, base=str(tmp_path), argv=['novel', 'agent'],
        prompt='零模型测试的授权记录', authorized=True, egress_authorized=True,
        code_version='clean-code',
        skill_sha256=verifier.skill_contract.package_sha256(verifier.REPO_ROOT),
        profiles={'commander': _PROFILE, 'executor_default': _PROFILE})
    verifier.agent.append(tmp_path, AgentRunEvent(
        run_id=_RUN_ID, kind='started', ts=1, start=start))
    verifier.agent.append(tmp_path, AgentRunEvent(
        run_id=_RUN_ID, kind='ended', ts=20, exit_code=2 if stopped else 0,
        exit_origin='supervisor', outcome=AgentDeliveryOutcome(
            state='stopped' if stopped else 'ready', reason=end_error_kind, checked_at=20)))
    _row, outer = verifier._one_outer_run(tmp_path, _RUN_ID)
    assert outer['state'] == ('fail' if stopped else 'pass')
    assert verifier._overall([linkage, outer]) == ('fail' if stopped else 'pass')
    # 即使运输与父运行已闭合，缺正式 typed 审批仍不可能聚合为整书通过。
    approval = SimpleNamespace(gate={}, seen={})
    approvals = verifier.current_approval_check(tmp_path, _SID, approval)
    assert approvals['state'] == 'fail'
    assert verifier._overall([linkage, outer, approvals]) == 'fail'
