#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent E2E 验收器的零模型身份联结测试。"""
from __future__ import annotations

import json

from contracts.invariants import AGENT_EXECUTOR_NODES
from contracts.schema.orchestration import ProgressEvent
from novel import agent_session_audit as audit
from novel.orchestrator import admission, beacon
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
) -> audit.AgentSessionRecord:
    common = {
        'session_run_id': session_id,
        'parent_run_id': _RUN_ID,
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


def _closed_chain():
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
    for index, node in enumerate(AGENT_EXECUTOR_NODES, start=1):
        attempt_id = f'attempt-{index}'
        # 一条 delivered turn 可合法保留 actual effort unknown；
        # requested 不得回填它。
        actual_effort = None if index == 1 else _PROFILE['reasoning_effort']
        sessions.append(_session(
            session_id=f'session-{index}', role='executor',
            attempt_id=attempt_id, node=node,
            actual_effort=actual_effort))
        ledger.append({
            'run_id': _RUN_ID, 'sid': _SID, 'node': node,
            'purpose': node, 'chapter': None, 'attempt_id': attempt_id,
            'outcome': 'ok', 'model': _PROFILE['model'],
            'reasoning_effort': _PROFILE['reasoning_effort'],
        })
        adoptions.append({
            'kind': 'artifact_adoption',
            'run_id': _RUN_ID, 'sid': _SID, 'node': node,
            'purpose': node, 'chapter': None, 'attempt_id': attempt_id,
            'artifact': f'artifact-{index}',
            'artifact_fingerprint': _SHA,
        })
    return ledger, adoptions, sessions


def test_all_eight_agent_nodes_close_ledger_session_and_adoption_chain():
    ledger, adoptions, sessions = _closed_chain()
    result = verifier.linkage_check(
        run_id=_RUN_ID, sid=_SID,
        ledger_rows=ledger, audit_rows=adoptions, sessions=sessions)
    assert result['state'] == 'pass'
    assert result['facts']['missing_adoption_nodes'] == []
    assert result['facts']['adoption_unlinked'] == 0


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
