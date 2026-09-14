#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""已完成的传输通知误拒：原始四证复验、零 wire 重放与耐崩溃边界。"""
from __future__ import annotations

import copy
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from contracts.schema.orchestration import (
    AgentProfile, AgentRunRecord, AgentRunStart, ExecutionEpoch,
    PipelineAgentProfiles, RunContractV2,
)
from novel import paths
from novel.agents import session_audit as audit
from novel.commanders.base import CommanderProtocolError
from novel.commanders.codex import CodexCommanderDriver
from novel.llm.ledger import ledger_path
from novel.orchestrator import (
    admission, agent, queue, reviewer_protocol_recovery as recovery,
    reviewer_seat_ledger as seats, reviewer_tasks, service_lifecycle, uploads,
)


SID = '42'
WAITING = queue.WAITING_ON_STAGE4_STRUCTURE_REVIEW
BUSINESS = 'b' * 64
CODE = 'c' * 40
SKILL = 'd' * 64
PROVIDER = 'provider-1'


@pytest.fixture
def source(tmp_path, monkeypatch):
    base = str(tmp_path)
    profile = AgentProfile(driver='codex', model='gpt-5.6-sol',
                           reasoning_effort='high')
    profiles = PipelineAgentProfiles(commander=profile, executor_default=profile)
    epoch = ExecutionEpoch(
        epoch_id='old-epoch', ordinal=1, reason='initial', argv=['supervisor'],
        prompt_sha256='e' * 64, protocol_version='codex-jsonl-v1',
        code_version=CODE, skill_sha256=SKILL, session_run_id='producer-1')
    contract = RunContractV2(sid=SID, base=base, authorized=True,
                             egress_authorized=True, profiles=profiles)
    parent = AgentRunRecord(
        run_id='old-parent', start=AgentRunStart(
            sid=SID, base=base, argv=['supervisor'], prompt='测试授权',
            authorized=True, egress_authorized=True, code_version=CODE,
            skill_sha256=SKILL, profiles=profiles),
        contract=contract, epochs=[epoch], started=1, ended=10,
        exit_code=70, state='failed')
    new_epoch = epoch.model_copy(update={'epoch_id': 'new-epoch', 'code_version': 'f' * 40})
    target = parent.model_copy(update={
        'run_id': 'new-parent', 'ended': None, 'exit_code': None,
        'state': 'running', 'epochs': [new_epoch], 'active_epoch_id': 'new-epoch'})
    parents = [parent, target]
    monkeypatch.setattr(agent, 'records', lambda _base: parents)
    for method in ('build_invocation', 'run'):
        monkeypatch.setattr(CodexCommanderDriver, method,
                            lambda *a, **k: pytest.fail('恢复不准调用 CLI 或模型'))
    identity = dict(waiting_on=WAITING, business_sha256=BUSINESS, turn_id='review-1')
    seats.reserve(base, SID, **identity, purpose='review', session_run_id='session-1',
                  parent_run_id=parent.run_id, epoch_id=epoch.epoch_id, now=2)
    seats.record_attempt(base, SID, **identity, attempt_id='attempt-1', kind='launch', now=2)
    seats.finish_attempt(base, SID, **identity, attempt_id='attempt-1', exit_code=70,
                         error_kind='protocol', provider_session_id=PROVIDER,
                         wire_evidence=True, now=8)
    seats.finish(base, SID, **identity, exit_code=70, now=9)
    usage = audit.AgentSessionUsage(
        prompt_tokens=100, prompt_cache_hit_tokens=20, prompt_cache_miss_tokens=80,
        completion_tokens=30, reasoning_tokens=10, total_tokens=130)
    common = dict(session_run_id='session-1', parent_run_id=parent.run_id,
                  role='commander', profile=profile, protocol_version='codex-jsonl-v1',
                  subject_sha256='e' * 64, requested_model=profile.model,
                  requested_effort=profile.reasoning_effort,
                  start_code_version=CODE, skill_sha256=SKILL,
                  prompt_sha256='e' * 64, schema_sha256='a' * 64)
    events = [
        audit.AgentSessionEvent(**common, kind='started', ts=2),
        audit.AgentSessionEvent(**common, kind='attempt', ts=8, attempt_id='attempt-1',
                                provider_session_id=PROVIDER, error_kind='protocol',
                                delivery_attempts=1, usage=usage),
        audit.AgentSessionEvent(**common, kind='turn', ts=8, attempt_id='attempt-1',
                                turn_index=1, provider_session_id=PROVIDER,
                                error_kind='commander_protocol', usage=usage),
        audit.AgentSessionEvent(**common, kind='ended', ts=9, provider_session_id=PROVIDER,
                                process_state='failed', delivery_state='unknown',
                                error_kind='commander_protocol', end_code_version=CODE),
    ]
    for event in events:
        audit.append_event(base, event)
    cost = {
        'run_id': parent.run_id, 'sid': SID, 'role': 'commander', 'node': None,
        'attempt_id': 'attempt-1', 'session_run_id': 'session-1',
        'activity': f'reviewer:{WAITING}', 'provider_session_id': PROVIDER,
        'outcome': 'error', 'error_kind': 'commander_protocol', 'exit_code': 70,
        'response_sha256': None, 'code': CODE, 'request_model': profile.model,
        'reasoning_effort': 'high', 'model': None, 'actual_reasoning_effort': None,
        'protocol_version': 'codex-jsonl-v1', 'cli_version': None, **usage.to_dict(),
    }
    ledger_path(base).write_text(json.dumps(cost) + '\n', encoding='utf-8')
    current = SimpleNamespace(
        state='blocked', surface_seal='2' * 64, code_version=new_epoch.code_version,
        action=SimpleNamespace(waiting_on=WAITING, action_sha256='3' * 64))
    payload = {
        'actor': 'agent:novel-pipeline-operator', 'reason': '逐章对照因果与结构',
        'review_token': 's4sr1_' + '5' * 64,
        'chapters': [
            {'output_ordinal': 1, 'decision': 'accept', 'reason': '结构闭合'},
            {'output_ordinal': 2, 'decision': 'request_replan', 'reason': '结果先于触发事件'},
        ],
    }
    document = reviewer_tasks._request_wrapper(current, WAITING, {
        'kind': 'approval', 'surface': 'structure', 'payload': payload})
    original = copy.deepcopy(document)
    original['surface_seal'] = '1' * 64
    wire = [
        {'type': 'thread.started', 'thread_id': PROVIDER},
        {'type': 'turn.started'},
        {'type': 'item.completed', 'item': {
            'id': 'item_0', 'type': 'error',
            'message': 'Falling back from WebSockets to HTTPS transport. tls handshake eof'}},
        {'type': 'item.completed', 'item': {
            'id': 'item_1', 'type': 'agent_message', 'text': json.dumps(original)}},
        {'type': 'turn.completed', 'usage': {
            'input_tokens': 100, 'cached_input_tokens': 20,
            'output_tokens': 30, 'reasoning_output_tokens': 10}},
    ]
    log = agent.run_log_path(base, parent.run_id)
    log.parent.mkdir(parents=True, exist_ok=True)

    def write_log():
        # 复现 Windows CRCRLF 与空协议块，不能靠普通 LF fixture 掩盖真实消费 bug。
        lines = ['$ codex exec', '[stderr]', 'Reconnecting...', '[stdout protocol]']
        for event in wire:
            lines.extend(['[stdout protocol]', json.dumps(event)])
        lines.append(recovery._LEGACY_REJECTION)
        log.write_bytes(('\r\r\n'.join(lines) + '\r\r\n').encode('utf-8'))

    write_log()
    scratch = service_lifecycle.open_scratch_session(base, SID, new_epoch.epoch_id)
    task = reviewer_tasks.ReviewerTask(
        waiting_on=WAITING, surface_seal=current.surface_seal,
        business_sha256=BUSINESS, request_path=scratch / 'request.json', prompt='审阅',
        submission_options=({'id': 'approval:structure', 'exact_request': document,
                             'editable_fields': [], 'binding_fields': [], 'allowed_values': {}},))
    return SimpleNamespace(
        base=base, parent=parent, parents=parents, target=target, epoch=new_epoch,
        task=task, current=current, log=log, wire=wire, write_log=write_log,
        cost=cost, events=events, payload=payload, original=original)


def recover(s):
    return agent._find_replayable_reviewer_action(
        s.base, SID, epoch=s.epoch, current=s.current, task=s.task,
        current_parent_run_id=s.target.run_id)


def test_original_decisions_replay_without_wire_or_rewriting_failed_history(source):
    s = source
    seat_path = seats.ledger_path(s.base, SID, waiting_on=WAITING, business_sha256=BUSINESS)
    before = (seat_path.read_bytes(), ledger_path(s.base).read_bytes(), audit.read_events(s.base))
    first = recover(s)
    assert first.action.payload == s.payload
    assert first.action.payload['chapters'][1]['decision'] == 'request_replan'
    assert json.loads(s.task.request_path.read_text('utf-8'))['surface_seal'] == s.current.surface_seal
    assert first.record.surface_seal == s.original['surface_seal']
    assert first.record.protocol_recovery.validation_code_version == s.epoch.code_version
    second = recover(s)
    assert second.record == first.record and second.action == first.action
    assert before == (seat_path.read_bytes(), ledger_path(s.base).read_bytes(), audit.read_events(s.base))
    uploads.discard_edit_plan(s.base, first.path)
    archives = list(paths.reviewer_protocol_recoveries_dir(s.base, SID).glob('*.json'))
    assert len(archives) == 1
    assert archives[0].read_bytes() == (first.record.model_dump_json() + '\n').encode('utf-8')


def test_promotion_crash_leaves_durable_action_and_next_parent_can_replay(source, monkeypatch):
    original = agent._promote_reviewer_candidate
    monkeypatch.setattr(agent, '_promote_reviewer_candidate',
                        lambda *a: (_ for _ in ()).throw(OSError('模拟晋升失败')))
    with pytest.raises(OSError, match='模拟晋升失败'):
        recover(source)
    monkeypatch.setattr(agent, '_promote_reviewer_candidate', original)
    replay = recover(source)
    assert replay.action.payload == source.payload
    assert len(uploads.load_reviewer_actions(source.base, SID)) == 1


@pytest.mark.parametrize('variant', [
    'tool', 'unknown_notice', 'no_completed', 'duplicate_response', 'wrong_provider',
    'duplicate_key', 'usage', 'missing_cost', 'missing_schema', 'live_parent', 'token',
])
def test_incomplete_or_changed_sources_never_create_an_action(source, monkeypatch, variant):
    s = source
    if variant == 'tool':
        s.wire.insert(3, {'type': 'item.updated', 'item': {'type': 'command_execution'}})
    elif variant == 'unknown_notice':
        s.wire[2]['item']['message'] = 'unknown error'
    elif variant == 'no_completed':
        s.wire.pop()
    elif variant == 'duplicate_response':
        s.wire.insert(4, copy.deepcopy(s.wire[3]))
    elif variant == 'wrong_provider':
        s.wire[0]['thread_id'] = 'another-provider'
    elif variant == 'duplicate_key':
        text = s.wire[3]['item']['text']
        s.wire[3]['item']['text'] = text.replace('"action":', '"action": {}, "action":', 1)
    elif variant == 'usage':
        s.cost['completion_tokens'] = 31
        ledger_path(s.base).write_text(json.dumps(s.cost) + '\n', encoding='utf-8')
    elif variant == 'missing_cost':
        ledger_path(s.base).write_text('', encoding='utf-8')
    elif variant == 'missing_schema':
        monkeypatch.setattr(audit, 'read_events', lambda _base: [
            replace(e, schema_sha256=None) for e in s.events])
    elif variant == 'live_parent':
        s.parents[0] = s.parent.model_copy(update={'ended': None})
    elif variant == 'token':
        s.task.submission_options[0]['exact_request']['action']['payload']['review_token'] = 's4sr1_' + '6' * 64
    s.write_log()
    with pytest.raises((reviewer_tasks.ReviewerTaskError, CommanderProtocolError)):
        recover(s)
    assert uploads.load_reviewer_actions(s.base, SID) == []


def test_unregistered_protocol_failure_keeps_existing_stop_route(source):
    source.log.write_text('another protocol failure\n', encoding='utf-8')
    source.parents.clear()  # 未命中已登记策略时甚至不需要查询旧 parent。
    assert recover(source) is None


@pytest.mark.parametrize('variant', ['source_changed', 'payload_changed', 'business_changed'])
def test_durable_recovery_conflict_preserves_evidence_and_refuses(source, variant):
    s = source
    first = recover(s)
    active_path = first.path
    if variant == 'source_changed':
        with s.log.open('ab') as stream:
            stream.write(b'changed\n')
    elif variant == 'payload_changed':
        payload = copy.deepcopy(first.record.payload)
        payload['chapters'][1]['decision'] = 'accept'
        uploads.discard_edit_plan(s.base, first.path)
        active_path = uploads.save_reviewer_action(
            s.base, SID, first.record.model_copy(update={'payload': payload}))
    else:
        s.task = replace(s.task, business_sha256='9' * 64)
    with pytest.raises(reviewer_tasks.ReviewerTaskError):
        recover(s)
    assert active_path.exists()


def test_active_owner_refuses_even_if_parent_event_claims_ended(source):
    with admission.owner_lease(source.base, source.parent.run_id):
        with pytest.raises(Exception, match='owner|所有者|活|运行|停止'):
            recover(source)


def test_archive_failure_does_not_promote_or_erase_original_response(source, monkeypatch):
    monkeypatch.setattr(recovery, 'archive_record',
                        lambda *a: (_ for _ in ()).throw(OSError('模拟审计写入失败')))
    raw = source.log.read_bytes()
    with pytest.raises(OSError, match='模拟审计写入失败'):
        recover(source)
    assert not source.task.request_path.exists()
    assert uploads.load_reviewer_actions(source.base, SID) == []
    assert source.log.read_bytes() == raw
