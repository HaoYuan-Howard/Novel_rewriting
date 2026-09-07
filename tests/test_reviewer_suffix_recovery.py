#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 冗余 option 尾巴：最新付费回答的零模型复验与关闭式反例。"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from contracts.schema.orchestration import (
    AgentDeliveryOutcome, AgentProfile, AgentRunRecord, AgentRunStart, ExecutionEpoch,
    PipelineAgentProfiles, RunContractV2,
)
from novel import agent_session_audit as audit, paths
from novel.commanders.base import CommanderProtocolError
from novel.commanders.codex import CodexCommanderDriver
from novel.llm.ledger import ledger_path
from novel.orchestrator import (
    admission, agent, queue, reviewer_protocol_recovery as recovery,
    reviewer_seat_ledger as seats, reviewer_tasks, service_lifecycle, uploads,
)


SID = '42'
WAITING = queue.WAITING_ON_STAGE4_DESIGN_REVIEW
BUSINESS = 'b' * 64
CODE = 'c' * 40
SKILL = 'd' * 64
PROVIDER = 'provider-1'
TAIL = ',"id":"approval:design"}'


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def design_document():
    return {
        'schema': reviewer_tasks.REQUEST_SCHEMA, 'waiting_on': WAITING,
        'surface_seal': '1' * 64,
        'action': {'kind': 'approval', 'surface': 'design', 'payload': {
            'actor': 'agent:novel-pipeline-operator', 'reviewer_kind': 'delegated_operator',
            'review_run_id': 'original-review', 'general_reason': '逐章复核具体因果',
            'review_token': 's4dr1_' + '5' * 64,
            'strategy': {'decision': 'accept', 'reason': '策略可行', 'replan_constraints': []},
            'chapters': [{'output_ordinal': 1, 'decision': 'request_repair',
                          'reason': '最新章需补齐场景入口', 'constraints': [
                              {'code': 'entry_anchor', 'scene_refs': [],
                               'opaque_aliases': ['a_' + '1' * 16], 'effect': None}]}],
        }},
    }


def option(document):
    return {'id': 'approval:' + document['action']['surface'],
            'exact_request': copy.deepcopy(document), 'editable_fields': [],
            'binding_fields': [], 'allowed_values': {}}


@pytest.fixture
def suffix_source(tmp_path, monkeypatch):
    base = str(tmp_path)
    profile = AgentProfile(driver='codex', model='gpt-5.6-sol', reasoning_effort='high')
    profiles = PipelineAgentProfiles(commander=profile, executor_default=profile)
    epoch = ExecutionEpoch(
        epoch_id='old-epoch', ordinal=1, reason='initial', argv=['supervisor'],
        prompt_sha256='e' * 64, protocol_version='codex-jsonl-v1',
        code_version=CODE, skill_sha256=SKILL, session_run_id='producer-1')
    parent = AgentRunRecord(
        run_id='old-parent', start=AgentRunStart(
            sid=SID, base=base, argv=['supervisor'], prompt='测试授权', authorized=True,
            egress_authorized=True, code_version=CODE, skill_sha256=SKILL, profiles=profiles),
        contract=RunContractV2(sid=SID, base=base, authorized=True,
                               egress_authorized=True, profiles=profiles),
        epochs=[epoch], started=1, ended=50, exit_code=2, state='stopped',
        outcome=AgentDeliveryOutcome(state='stopped', checked_at=50, reason='JSON 订正耗尽'))
    new_epoch = epoch.model_copy(update={'epoch_id': 'new-epoch', 'code_version': 'f' * 40})
    target = parent.model_copy(update={
        'run_id': 'new-parent', 'ended': None, 'exit_code': None, 'state': 'running',
        'epochs': [new_epoch], 'active_epoch_id': 'new-epoch', 'outcome': None})
    parents = [parent, target]
    monkeypatch.setattr(agent, 'records', lambda _base: parents)
    for method in ('build_invocation', 'run'):
        monkeypatch.setattr(CodexCommanderDriver, method,
                            lambda *a, **k: pytest.fail('协议复验不准调用 CLI 或模型'))
    document = design_document()
    common = dict(
        session_run_id='session-1', parent_run_id=parent.run_id, role='commander',
        profile=profile, protocol_version='codex-jsonl-v1', subject_sha256='e' * 64,
        requested_model=profile.model, requested_effort=profile.reasoning_effort,
        start_code_version=CODE, skill_sha256=SKILL,
        prompt_sha256='e' * 64, schema_sha256='a' * 64)
    events = [audit.AgentSessionEvent(**common, kind='started', ts=2)]
    costs, wires = [], []
    for index in range(3):
        turn_id, attempt_id = f'review-{index + 1}', f'attempt-{index + 1}'
        identity = dict(waiting_on=WAITING, business_sha256=BUSINESS, turn_id=turn_id)
        start_at, end_at = 2 + index * 10, 8 + index * 10
        seats.reserve(base, SID, **identity, purpose='review' if index == 0 else 'correction',
                      session_run_id='session-1', parent_run_id=parent.run_id,
                      epoch_id=epoch.epoch_id, now=start_at)
        seats.record_attempt(base, SID, **identity, attempt_id=attempt_id,
                             kind='launch', now=start_at)
        seats.finish_attempt(base, SID, **identity, attempt_id=attempt_id, exit_code=0,
                             error_kind=None, provider_session_id=PROVIDER,
                             wire_evidence=True, now=end_at)
        seats.finish(base, SID, **identity, exit_code=0, now=end_at + 1)
        response_document = copy.deepcopy(document)
        response_document['action']['payload']['chapters'][0]['reason'] = (
            '最新章需补齐场景入口' if index == 2 else f'历史第 {index + 1} 轮理由')
        response = json.dumps(response_document, ensure_ascii=False) + TAIL
        seats.mark_pending_correction(
            base, SID, waiting_on=WAITING, business_sha256=BUSINESS,
            session_run_id='session-1', provider_session_id=PROVIDER,
            request_sha256=digest(response), reason=recovery._SUFFIX_REJECTION,
            now=end_at + 1)
        usage = audit.AgentSessionUsage(
            prompt_tokens=100 + index, prompt_cache_hit_tokens=20,
            prompt_cache_miss_tokens=80 + index, completion_tokens=30,
            reasoning_tokens=10, total_tokens=130 + index)
        per_turn = {**common, 'prompt_sha256': ('e' if index == 0 else str(index)) * 64}
        events.extend([
            audit.AgentSessionEvent(**per_turn, kind='attempt', ts=end_at,
                                    attempt_id=attempt_id, provider_session_id=PROVIDER,
                                    delivery_attempts=1, usage=usage),
            audit.AgentSessionEvent(**per_turn, kind='turn', ts=end_at + 1,
                                    attempt_id=attempt_id, turn_index=index + 1,
                                    provider_session_id=PROVIDER, usage=usage),
        ])
        costs.append({
            'run_id': parent.run_id, 'sid': SID, 'role': 'commander', 'node': None,
            'session_run_id': 'session-1', 'attempt_id': attempt_id,
            'activity': f'reviewer:{WAITING}', 'provider_session_id': PROVIDER,
            'outcome': 'ok', 'error_kind': None, 'exit_code': 0,
            'response_sha256': digest(response), 'code': CODE,
            'request_model': profile.model, 'reasoning_effort': profile.reasoning_effort,
            'model': None, 'actual_reasoning_effort': None,
            'protocol_version': 'codex-jsonl-v1', 'cli_version': None, **usage.to_dict(),
        })
        wires.append([
            {'type': 'thread.started', 'thread_id': PROVIDER}, {'type': 'turn.started'},
            {'type': 'item.completed', 'item': {
                'id': 'item_0', 'type': 'agent_message', 'text': response}},
            {'type': 'turn.completed', 'usage': {
                'input_tokens': 100 + index, 'cached_input_tokens': 20,
                'output_tokens': 30, 'reasoning_output_tokens': 10}},
        ])
    events.append(audit.AgentSessionEvent(
        **common, kind='ended', ts=40, provider_session_id=PROVIDER, process_state='ok',
        delivery_state='blocked', error_kind='commander_reviewer_request', end_code_version=CODE))
    for event in events:
        audit.append_event(base, event)
    log = agent.run_log_path(base, parent.run_id)
    log.parent.mkdir(parents=True, exist_ok=True)

    def write_evidence():
        lines = []
        for wire in wires:
            lines.append('$ codex exec --json')
            for event in wire:
                lines.extend(['[stdout protocol]', json.dumps(event)])
            lines.extend(['[stdout protocol]', ''])
        log.write_bytes(('\r\r\n'.join(lines) + '\r\r\n').encode('utf-8'))
        ledger_path(base).write_text(
            ''.join(json.dumps(cost) + '\n' for cost in costs), encoding='utf-8')

    write_evidence()
    current = SimpleNamespace(
        state='blocked', surface_seal='2' * 64, code_version=new_epoch.code_version,
        action=SimpleNamespace(waiting_on=WAITING, action_sha256='3' * 64))
    current_document = copy.deepcopy(document)
    current_document['surface_seal'] = current.surface_seal
    scratch = service_lifecycle.open_scratch_session(base, SID, new_epoch.epoch_id)
    task = reviewer_tasks.ReviewerTask(
        waiting_on=WAITING, surface_seal=current.surface_seal, business_sha256=BUSINESS,
        request_path=scratch / 'request.json', prompt='审阅',
        submission_options=(option(current_document),))
    return SimpleNamespace(
        base=base, profile=profile, parent=parent, parents=parents, target=target,
        epoch=new_epoch, task=task, current=current, original=document,
        log=log, wires=wires, costs=costs, events=events, write_evidence=write_evidence)


def inspect(s):
    ledger = seats.read(s.base, SID, waiting_on=WAITING, business_sha256=BUSINESS)
    return recovery.inspect_response(
        s.base, SID, ledger=ledger, parent=s.parent, log_path=s.log, profile=s.profile,
        validation_code=s.epoch.code_version, submission_options=s.task.submission_options)


def replay(s):
    return agent._find_replayable_reviewer_action(
        s.base, SID, epoch=s.epoch, current=s.current, task=s.task,
        current_parent_run_id=s.target.run_id)


def test_latest_seat_response_recovered_with_original_ledgers_unchanged(suffix_source):
    s = suffix_source
    seat_path = seats.ledger_path(s.base, SID, waiting_on=WAITING, business_sha256=BUSINESS)
    before = (seat_path.read_bytes(), ledger_path(s.base).read_bytes(), audit.read_events(s.base))
    result = inspect(s)
    assert result.document == s.original
    assert result.proof.policy == 'codex-redundant-option-suffix-v1'
    assert result.proof.turn_id == 'review-3' and result.proof.attempt_id == 'attempt-3'
    assert recovery.decode_recovered_response(result, s.task.submission_options) == s.original
    assert before == (seat_path.read_bytes(), ledger_path(s.base).read_bytes(), audit.read_events(s.base))
    with pytest.raises(seats.ReviewerSeatRefused, match='用尽'):
        seats.reserve(s.base, SID, waiting_on=WAITING, business_sha256=BUSINESS,
                      purpose='correction', session_run_id='session-1', turn_id='review-4',
                      parent_run_id=s.parent.run_id, epoch_id='old-epoch')


@pytest.mark.parametrize('variant', [
    'duplicate_key', 'arbitrary_tail', 'wrong_option', 'two_actions', 'missing_field',
    'incomplete_json', 'no_tail', 'nan', 'missing_token', 'missing_default_field',
])
def test_suffix_decoder_never_repairs_ambiguous_or_incomplete_json(variant):
    document = design_document()
    options = (option(document),)
    text = json.dumps(document, ensure_ascii=False)
    tail = TAIL
    if variant == 'duplicate_key':
        text = text.replace('"decision":', '"decision": "request_replan", "decision":', 1)
    elif variant == 'arbitrary_tail':
        tail += ' extra'
    elif variant == 'wrong_option':
        tail = ',"id":"approval:structure"}'
    elif variant == 'two_actions':
        tail = ',' + text + TAIL
    elif variant == 'missing_field':
        del document['action']['payload']['chapters']
        text = json.dumps(document)
    elif variant == 'incomplete_json':
        text = text[:-1]
    elif variant == 'no_tail':
        tail = ''
    elif variant == 'nan':
        text = text.replace('"output_ordinal": 1', '"output_ordinal": NaN')
    elif variant == 'missing_token':
        del document['action']['payload']['review_token']
        text = json.dumps(document)
    elif variant == 'missing_default_field':
        del document['action']['payload']['strategy']['replan_constraints']
        text = json.dumps(document)
    with pytest.raises(reviewer_tasks.ReviewerTaskError):
        recovery._suffix_document(text + tail, options)


def test_structure_review_suffix_keeps_request_replan_decision():
    document = {
        'schema': reviewer_tasks.REQUEST_SCHEMA,
        'waiting_on': queue.WAITING_ON_STAGE4_STRUCTURE_REVIEW,
        'surface_seal': '1' * 64,
        'action': {'kind': 'approval', 'surface': 'structure', 'payload': {
            'actor': 'agent:novel-pipeline-operator', 'reason': '逐章核过结构',
            'review_token': 's4sr1_' + '5' * 64,
            'chapters': [{'output_ordinal': 1, 'decision': 'request_replan',
                          'reason': '结果先于具体触发事件'}],
        }},
    }
    raw = json.dumps(document) + ',"id":"approval:structure"}'
    assert recovery._suffix_document(raw, (option(document),)) == document


@pytest.mark.parametrize('variant', ['unregistered_reason', 'old_pending', 'not_stage4'])
def test_only_latest_registered_stage4_rejection_enters_recovery(suffix_source, variant):
    s = suffix_source
    ledger = seats.read(s.base, SID, waiting_on=WAITING, business_sha256=BUSINESS)
    if variant == 'unregistered_reason':
        ledger = ledger.model_copy(update={'pending_correction':
            ledger.pending_correction.model_copy(update={'reason': '其他 typed 业务拒因'})})
    elif variant == 'old_pending':
        ledger = ledger.model_copy(update={'pending_correction':
            ledger.pending_correction.model_copy(update={'source_turn_id': 'review-1'})})
    elif variant == 'not_stage4':
        ledger = ledger.model_copy(update={'waiting_on': 'approval:writing'})
    assert not recovery.is_recovery_candidate(ledger)
    assert recovery.inspect_response(
        s.base, SID, ledger=ledger, parent=s.parent, log_path=s.log,
        profile=s.profile, validation_code=s.epoch.code_version,
        submission_options=s.task.submission_options) is None


@pytest.mark.parametrize('variant', [
    'latest_invalid', 'wire_reordered', 'extra_call', 'duplicate_message', 'tool',
    'missing_cost', 'duplicate_cost', 'cost_digest', 'first_cost', 'missing_schema',
    'turn_prompt', 'turn_index', 'live_parent', 'wrong_profile', 'missing_option',
])
def test_source_disagreement_refuses_without_searching_historical_answer(
        suffix_source, monkeypatch, variant):
    s = suffix_source
    if variant == 'latest_invalid':
        # 同步真实计量与 pending 身份，让失败来自末轮语法；旧轮合法也不后退挑稿。
        s.wires[-1][2]['item']['text'] = '{broken' + TAIL
        value = digest(s.wires[-1][2]['item']['text'])
        s.costs[-1]['response_sha256'] = value
        seats.mark_pending_correction(
            s.base, SID, waiting_on=WAITING, business_sha256=BUSINESS,
            session_run_id='session-1', provider_session_id=PROVIDER,
            request_sha256=value, reason=recovery._SUFFIX_REJECTION, now=39)
    elif variant == 'wire_reordered':
        s.wires[0], s.wires[2] = s.wires[2], s.wires[0]
    elif variant == 'extra_call':
        s.wires.append(copy.deepcopy(s.wires[-1]))
    elif variant == 'duplicate_message':
        s.wires[-1].insert(3, copy.deepcopy(s.wires[-1][2]))
    elif variant == 'tool':
        s.wires[-1].insert(2, {'type': 'item.started', 'item': {'type': 'command_execution'}})
    elif variant == 'missing_cost':
        s.costs.pop()
    elif variant == 'duplicate_cost':
        s.costs.append(copy.deepcopy(s.costs[-1]))
    elif variant == 'cost_digest':
        s.costs[-1]['response_sha256'] = '9' * 64
    elif variant == 'first_cost':
        s.costs[0]['completion_tokens'] += 1
    elif variant == 'missing_schema':
        s.events[-2] = replace(s.events[-2], schema_sha256='9' * 64)
    elif variant == 'turn_prompt':
        s.events[-2] = replace(s.events[-2], prompt_sha256='9' * 64)
    elif variant == 'turn_index':
        s.events[-2] = replace(s.events[-2], turn_index=4)
    elif variant == 'live_parent':
        s.parent = s.parent.model_copy(update={'ended': None})
    elif variant == 'wrong_profile':
        s.profile = s.profile.model_copy(update={'model': 'another-model'})
    elif variant == 'missing_option':
        s.task = replace(s.task, submission_options=())
    monkeypatch.setattr(audit, 'read_events', lambda _base: s.events)
    s.write_evidence()
    with pytest.raises((reviewer_tasks.ReviewerTaskError, CommanderProtocolError, ValueError)):
        inspect(s)
    assert uploads.load_reviewer_actions(s.base, SID) == []


def test_canonical_replay_is_durable_idempotent_and_preserves_original_history(suffix_source):
    s = suffix_source
    first = replay(s)
    assert first.record.payload == s.original['action']['payload']
    assert first.record.surface_seal == s.original['surface_seal']
    assert json.loads(s.task.request_path.read_text('utf-8'))['surface_seal'] == s.current.surface_seal
    assert replay(s).record == first.record
    uploads.discard_edit_plan(s.base, first.path)
    archives = list(paths.reviewer_protocol_recoveries_dir(s.base, SID).glob('*.json'))
    assert len(archives) == 1
    assert archives[0].read_bytes() == (first.record.model_dump_json() + '\n').encode('utf-8')


def test_replay_after_promotion_crash_uses_saved_latest_decision(suffix_source, monkeypatch):
    promote = agent._promote_reviewer_candidate
    monkeypatch.setattr(agent, '_promote_reviewer_candidate',
                        lambda *a: (_ for _ in ()).throw(OSError('模拟晋升失败')))
    with pytest.raises(OSError, match='模拟晋升失败'):
        replay(suffix_source)
    monkeypatch.setattr(agent, '_promote_reviewer_candidate', promote)
    assert replay(suffix_source).record.payload == suffix_source.original['action']['payload']
    assert len(uploads.load_reviewer_actions(suffix_source.base, SID)) == 1


def test_active_source_owner_refuses_suffix_recovery(suffix_source):
    with admission.owner_lease(suffix_source.base, suffix_source.parent.run_id):
        with pytest.raises(Exception, match='owner|所有者|活|运行|停止'):
            replay(suffix_source)
