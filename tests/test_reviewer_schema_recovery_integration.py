#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""供应商 stdout schema 拒绝到新 Run 恢复、正式审批与后继动作的消费回归。

零语料 typed subject 隔离产物输入，Python 子进程只代替收费 CLI。原始失败的
席位、session、成本与 stdout 日志由真实 Supervisor/driver 生成，恢复不手补账。
"""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.schema.api import DesignReviewRequest
from contracts.schema.orchestration import AgentDeliveryOutcome
from novel import agent_session_audit
from novel.agents import jsonl_runner
from novel.commanders import CodexCommanderDriver
from novel.commanders import base as commander_base
from novel.llm.ledger import ledger_path
from novel.orchestrator import agent, approvals, queue, reviewer_tasks, uploads
from novel.orchestrator import stage4_reviewer_wire as wire
from tests.test_design_review import SID, isolated as design_isolated
from tests.test_stage4_reviewer_integration import (
    PROFILES, _canonical, _current, _install_task_source, _pack, _response,
)


WAITING = queue.WAITING_ON_STAGE4_DESIGN_REVIEW


def _probe_driver(root, next_response, invocations):
    """保留生产 Popen/二进制 stdin/输出解析/日志，仅替换外部可执行文件。"""
    root.mkdir(parents=True, exist_ok=True)
    script = root / 'provider.py'
    script.write_text('''import hashlib, json, pathlib, sys
config_path = pathlib.Path(sys.argv[1])
config = json.loads(config_path.read_text(encoding='utf-8'))
argv = sys.argv[2:]
schema = pathlib.Path(argv[argv.index('--output-schema') + 1]).read_bytes()
prompt = sys.stdin.buffer.read()
config_path.with_suffix('.receipt').write_text(json.dumps({
    'argv': argv, 'schema_sha256': hashlib.sha256(schema).hexdigest(),
    'schema_hex': schema.hex(), 'prompt_sha256': hashlib.sha256(prompt).hexdigest(),
}), encoding='utf-8')
print(json.dumps({'type': 'thread.started', 'thread_id': config['session']}), flush=True)
print(json.dumps({'type': 'turn.started'}), flush=True)
if config['error'] is not None:
    message = json.dumps(config['error'])
    print(json.dumps({'type': 'error', 'message': message}), flush=True)
    print(json.dumps({'type': 'turn.failed', 'error': {'message': message}}), flush=True)
    sys.exit(1)
print(json.dumps({'type': 'item.completed', 'item': {
    'type': 'agent_message', 'text': json.dumps(config['response'], ensure_ascii=False),
}}), flush=True)
print(json.dumps({'type': 'turn.completed', 'usage': {
    'input_tokens': 10, 'cached_input_tokens': 2, 'output_tokens': 5,
    'reasoning_output_tokens': 1,
}}), flush=True)
''', encoding='utf-8')

    class ProbeRunner(commander_base._SubprocessCommanderRunner):
        def run(self, invocation, **kwargs):
            invocations.append(invocation)
            config_path = root / f'call-{len(invocations)}.json'
            config_path.write_bytes(_canonical(next_response(len(invocations))))
            probe_invocation = replace(invocation, argv=(
                sys.executable, '-B', str(script), str(config_path), *invocation.argv[1:]))
            return super().run(probe_invocation, **kwargs)

    return CodexCommanderDriver(executable='codex-schema-probe.exe', runner=ProbeRunner())


@pytest.fixture
def schema_run(request, monkeypatch):
    actual_write = approvals._write
    base, state = request.getfixturevalue('design_isolated')
    state['candidate_proof_sha256'] = state['subjects'].candidate_sha256
    # 恢复正式原子落盘与 raw 读取，不能用 fixture 内存 record 冒充跨 Run 审批。
    monkeypatch.setattr(approvals, '_write', actual_write)
    monkeypatch.setattr(approvals, '_effective_record', lambda _dir, record: record)
    monkeypatch.setattr(approvals, '_read_current', approvals._read_raw)
    monkeypatch.setattr(approvals, '_read_state', lambda _base, _sid: (
        approvals._read_raw(_base, _sid), approvals._read_raw(_base, _sid)))
    actual_write(base, state['record'])
    live = {'code': '1' * 40, 'schema_revision': 'old', 'finished': False,
            'scenarios': ['schema_reject', 'success'], 'tasks': [], 'applied': [],
            'repairs': [], 'invocations': [], 'popens': [], 'runs': [], 'schema_calls': []}
    current = _current(WAITING)
    _install_task_source(monkeypatch, current, _pack(base, 'design'))
    monkeypatch.setattr(agent.prov, 'code_version', lambda: live['code'])
    monkeypatch.setattr(agent, '_assess_delivery', lambda *_a, **_k:
                        AgentDeliveryOutcome(state='stopped', checked_at=1,
                                             reason='零模型测试后继动作'))
    actual_build = reviewer_tasks.build_task

    def build_task(*args, **kwargs):
        task = actual_build(*args, **kwargs)
        if task is not None:
            live['tasks'].append(task)
        return task

    monkeypatch.setattr(reviewer_tasks, 'build_task', build_task)
    actual_schema = wire.output_schema_for

    def schema_for(task):
        schema = actual_schema(task)
        # 供应商拒绝是外部观测；用合法注解区分修前/后编译器版本，保持生产
        # 预检全程开启。旧 $ref sibling 的归一反例另由公共 schema 测试守住。
        result = {**schema, 'description': f'测试运输编译器版本：{live["schema_revision"]}'}
        live['schema_calls'].append((
            live['code'], live['schema_revision'], hashlib.sha256(_canonical(result)).hexdigest()))
        return result

    monkeypatch.setattr(wire, 'output_schema_for', schema_for)
    monkeypatch.setattr(queue, 'nodes_up_to', lambda _until: ['stage4.design', 'stage4.plan'])
    monkeypatch.setattr(queue.st, 'resume_verdict', lambda _ov, node: (
        (queue.st.RESUME_SKIP, '当前 A 候选') if node == 'stage4.design'
        else (queue.st.RESUME_RUN, 'B 等待重建')))
    monkeypatch.setattr(queue.st, 'stage2_global_projection_blocked', lambda _ov: False)
    monkeypatch.setattr(queue.st, 'stage2_reviewed_edit_correction_needed', lambda *_a: False)
    monkeypatch.setattr(queue.st, 'stage2_frozen_v6_subject_validated', lambda _ov: False)
    monkeypatch.setattr(queue.approvals, 'require_stage2_review', lambda *_a: None)

    def next_action(_base, _sid, *, code):
        if live['finished']:
            terminal = _current('manual-test-schema-recovery-next-action')
            terminal.code_version = code
            return terminal
        doc = approvals.get(base, SID)
        if doc.design_review.state == 'repair_requested':
            _todo, blocked = queue.plan_book_from_overview(
                base, SID, SimpleNamespace(stages=[]), until_stage='stage4',
                follow_check=False, approval_doc=doc)
            planned = blocked[0]
            assert planned.waiting_on == queue.WAITING_ON_STAGE4_DESIGN_ACTION
            assert planned.flags == ['--review-token', doc.design_review.action_review_token]
            return SimpleNamespace(
                state='blocked', code_version=code, surface_seal='c' * 64,
                execution_token=None, action=SimpleNamespace(
                    node=planned.node, flags=planned.flags, waiting_on=planned.waiting_on,
                    action_sha256='d' * 64))
        current.code_version = code
        return current

    monkeypatch.setattr(agent.operator, 'next_action_at_code', next_action)
    monkeypatch.setattr(agent.execution_permit, 'permit_from_next',
                        lambda _current, node, flags: SimpleNamespace(node=node, flags=flags))

    def repair_argv(_base, _sid, epoch, permit, *, stem):
        assert stem == 'recovery-stage4.design'
        scope = approvals.resolve_design_action(
            base, SID, permit.flags[-1], expected='repair')
        assert scope.target_chapters == [1]
        live['repairs'].append((epoch.session_run_id, tuple(permit.flags)))
        live['finished'] = True
        return [sys.executable, '-B', '-c', 'pass']

    monkeypatch.setattr(agent, '_run_step_argv', repair_argv)
    actual_approval_argv = agent._reviewer_approval_argv

    def approval_argv(_base, _sid, epoch, action):
        argv = actual_approval_argv(_base, _sid, epoch, action)
        payload = json.loads(Path(argv[argv.index('--input') + 1]).read_text('utf-8'))
        assert payload == action.payload
        approvals.approve_design(base, SID, DesignReviewRequest.model_validate(payload))
        live['applied'].append(payload)
        return [sys.executable, '-B', '-c', 'pass']

    monkeypatch.setattr(agent, '_reviewer_approval_argv', approval_argv)
    probe = Path(base) / 'schema-probe'
    actual_popen = jsonl_runner.subprocess.Popen

    def observed_popen(argv, **kwargs):
        process = actual_popen(argv, **kwargs)
        if str(probe / 'provider.py') in argv:
            live['popens'].append(process.pid)
        return process

    monkeypatch.setattr(jsonl_runner.subprocess, 'Popen', observed_popen)

    def next_response(ordinal):
        scenario = live['scenarios'][min(ordinal - 1, len(live['scenarios']) - 1)]
        invocation = live['invocations'][-1]
        provider_session = (invocation.resume_provider_session_id
                            or f'schema-provider-{ordinal}')
        if scenario in ('schema_reject', 'unknown_400', 'wrong_code', 'wrong_param'):
            error = {'type': 'error', 'status': 400, 'error': {
                'type': 'invalid_request_error', 'code': (
                    'unknown_error' if scenario in ('unknown_400', 'wrong_code')
                    else 'invalid_json_schema'),
                'param': ('model' if scenario in ('unknown_400', 'wrong_param')
                          else 'text.format.schema'),
                'message': (
                    "Invalid schema for response_format 'codex_output_schema': "
                    "context=('properties', 'observation_alias'), "
                    "$ref cannot have keywords {'title'}."
                    if scenario != 'unknown_400' else 'Unrecognized request field.'),
            }}
            return {'session': provider_session, 'error': error, 'response': None}
        response = _response(live['tasks'][-1], 'repair')
        if scenario == 'typed_invalid':
            response['id'] = 'approval:design'
            response['selection']['payload']['general_reason'] += f'；本次是第 {ordinal} 轮'
        return {'session': provider_session, 'error': None, 'response': response}

    monkeypatch.setattr(agent, 'create_commander_driver', lambda _profile, **_kwargs:
                        _probe_driver(probe, next_response, live['invocations']))
    monkeypatch.setattr(agent, '_retry_sleep', lambda _seconds:
                        pytest.fail('schema/未知 400 不允许同 Run 运输重试'))

    def run_once(*, code=None, schema_revision=None):
        if code is not None:
            live['code'] = code
        if schema_revision is not None:
            live['schema_revision'] = schema_revision
        rec = agent.start(str(base), SID, authorized=True, egress_authorized=True,
                          profiles=PROFILES, spawn=None)
        assert agent.wait(rec.run_id, 20)
        done = next(row for row in agent.records(base) if row.run_id == rec.run_id)
        live['runs'].append(done)
        return done

    return SimpleNamespace(base=base, state=state, live=live, probe=probe, run=run_once)


def _seat(harness):
    task = harness.live['tasks'][0]
    return agent.reviewer_seat_ledger.read(
        harness.base, SID, waiting_on=WAITING, business_sha256=task.business_sha256)


def _first_failure(harness, *, expected_error='agent_schema'):
    first = harness.run()
    assert first.exit_code == 1
    assert harness.live['applied'] == [] and harness.live['repairs'] == []
    assert len(harness.live['invocations']) == 1
    ledger = _seat(harness)
    assert ledger is not None and len(ledger.turns) == 1
    turn = ledger.turns[0]
    assert turn.purpose == 'review' and turn.outcome == 'failed'
    assert turn.exit_code == 1 and turn.provider_response_sha256 is None
    assert ledger.pending_correction is None
    [attempt] = turn.attempts
    assert attempt.wire_evidence is True and attempt.error_kind == expected_error
    assert attempt.exit_code == 1
    source_log = agent.run_log_path(harness.base, first.run_id)
    raw_log = source_log.read_bytes()
    assert b'[stdout protocol]' in raw_log and b'"turn.failed"' in raw_log
    assert b'"agent_message"' not in raw_log and b'[stderr]' not in raw_log
    costs = [json.loads(line) for line in ledger_path(harness.base).read_text('utf-8').splitlines()
             if json.loads(line).get('attempt_id') == attempt.attempt_id]
    [cost] = costs
    assert cost['outcome'] == 'error' and cost['will_retry'] is False
    assert cost['response_sha256'] is None
    for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
        assert cost[key] is None
    events = [row for row in agent_session_audit.read_events(harness.base)
              if row.session_run_id == turn.session_run_id]
    assert {row.kind for row in events} == {'started', 'attempt', 'turn', 'ended'}
    assert all(row.usage is None for row in events)
    return SimpleNamespace(
        run=first, turn=turn.model_dump(mode='json'), source_log=source_log,
        log_bytes=raw_log, session_id=turn.session_run_id,
        audit_bytes=agent_session_audit.audit_path(harness.base).read_bytes(),
        cost_bytes=ledger_path(harness.base).read_bytes())


def _assert_original_preserved(harness, source):
    assert _seat(harness).turns[0].model_dump(mode='json') == source.turn
    assert source.source_log.read_bytes() == source.log_bytes
    assert agent_session_audit.audit_path(harness.base).read_bytes().startswith(source.audit_bytes)
    assert ledger_path(harness.base).read_bytes().startswith(source.cost_bytes)


@pytest.mark.parametrize('typed_correction', [False, True])
def test_stdout_schema_rejection_recovers_in_new_run_and_consumes_canonical_action(
        schema_run, typed_correction):
    h = schema_run
    h.live['scenarios'] = ['schema_reject'] + (
        ['typed_invalid', 'success'] if typed_correction else ['success'])
    source = _first_failure(h)
    second = h.run(code='2' * 40, schema_revision='fixed')
    assert second.exit_code == 2, agent.run_log_path(h.base, second.run_id).read_text('utf-8')
    assert len(h.live['applied']) == 1 and len(h.live['repairs']) == 1
    assert approvals.stage4_review_payload_landed(
        h.base, SID, surface='design', payload=h.live['applied'][0])
    assert uploads.load_reviewer_actions(h.base, SID) == []
    ledger = _seat(h)
    assert [row.purpose for row in ledger.turns] == (
        ['review', 'schema_recovery', 'correction'] if typed_correction
        else ['review', 'schema_recovery'])
    recovery_turn = ledger.turns[1]
    assert recovery_turn.schema_recovery is not None
    proof = recovery_turn.schema_recovery
    assert proof.policy == 'codex-output-schema-rejection-v1'
    assert proof.source_turn_id == source.turn['turn_id']
    assert proof.source_parent_run_id == source.run.run_id
    assert proof.source_session_run_id == source.session_id
    assert proof.source_code_version == '1' * 40
    assert proof.validation_code_version == '2' * 40
    assert recovery_turn.session_run_id != source.session_id
    assert recovery_turn.parent_run_id == second.run_id
    assert recovery_turn.ordinal == 2 and ledger.pending_correction is None
    first, fresh = h.live['invocations'][:2]
    assert fresh.resume_provider_session_id is None
    assert first.output_schema_sha256 != fresh.output_schema_sha256
    assert proof.source_schema_sha256 == first.output_schema_sha256
    assert proof.replacement_schema_sha256 == fresh.output_schema_sha256
    assert fresh.expected_output_schema_sha256 is None
    if typed_correction:
        correction = h.live['invocations'][2]
        assert ledger.turns[2].session_run_id == recovery_turn.session_run_id
        assert correction.resume_provider_session_id == 'schema-provider-2'
        assert correction.output_schema_bytes == fresh.output_schema_bytes
    for index, invocation in enumerate(h.live['invocations'], start=1):
        receipt = json.loads((h.probe / f'call-{index}.receipt').read_text('utf-8'))
        assert bytes.fromhex(receipt['schema_hex']) == invocation.output_schema_bytes
        assert receipt['schema_sha256'] == invocation.output_schema_sha256
        assert receipt['prompt_sha256'] == hashlib.sha256(
            invocation.stdin_text.encode('utf-8')).hexdigest()
    _assert_original_preserved(h, source)


@pytest.mark.parametrize('variant', [
    'same_schema', 'same_code', 'unknown_400', 'wrong_code', 'wrong_param',
])
def test_schema_recovery_never_reissues_unchanged_or_unproven_request(schema_run, variant):
    h = schema_run
    if variant in ('unknown_400', 'wrong_code', 'wrong_param'):
        h.live['scenarios'][0] = variant
    source = _first_failure(
        h, expected_error='agent_command' if variant == 'unknown_400' else 'agent_schema')
    second = h.run(
        code='1' * 40 if variant == 'same_code' else '2' * 40,
        schema_revision='old' if variant == 'same_schema' else 'fixed')
    assert second.exit_code == 2
    assert len(h.live['invocations']) == 1
    assert h.live['applied'] == [] and h.live['repairs'] == []
    assert len(_seat(h).turns) == 1
    _assert_original_preserved(h, source)
    if variant == 'same_code':
        # 即便恢复入口先按 code 拒绝、尚未铸 schema，也独立证明新 task 的
        # raw schema 已不同；只有 SHA 漂移不能授权重新调用。
        wire.output_schema_for(h.live['tasks'][-1])
        schema_calls = h.live['schema_calls']
        assert {row[0] for row in schema_calls} == {'1' * 40}
        assert len({row[2] for row in schema_calls}) >= 2
    if variant in ('same_schema', 'same_code'):
        # 一次未满足恢复条件的重启不能污染原失败证据；真正修好后的下次
        # Run 仍能自动接管，也不能把那次零 wire 拒绝计成订正席位。
        third = h.run(code='3' * 40, schema_revision='actually-fixed')
        assert third.exit_code == 2
        assert len(h.live['invocations']) == 2
        assert len(h.live['applied']) == len(h.live['repairs']) == 1
        assert [row.purpose for row in _seat(h).turns] == ['review', 'schema_recovery']
        _assert_original_preserved(h, source)


def test_repeated_schema_repairs_consume_existing_budget_without_reset(schema_run):
    h = schema_run
    h.live['scenarios'] = ['schema_reject'] * 3
    source = _first_failure(h)
    second = h.run(code='2' * 40, schema_revision='fixed-once')
    third = h.run(code='3' * 40, schema_revision='fixed-twice')
    assert second.exit_code == third.exit_code == 1
    ledger = _seat(h)
    assert [row.purpose for row in ledger.turns] == ['review', 'schema_recovery', 'schema_recovery']
    assert [row.ordinal for row in ledger.turns] == [1, 2, 3]
    assert len({row.session_run_id for row in ledger.turns}) == 3
    assert all(row.exit_code == 1 for row in ledger.turns)
    fourth = h.run(code='4' * 40, schema_revision='another-fix')
    assert fourth.exit_code == 2
    assert len(h.live['invocations']) == len(_seat(h).turns) == 3
    assert h.live['applied'] == [] and h.live['repairs'] == []
    _assert_original_preserved(h, source)


def test_schema_recovery_and_last_invalid_correction_stop_before_an_extra_popen(schema_run):
    h = schema_run
    # 两个错误回答的原始字节不同，不能靠 no-progress 判重提前停机掩盖预算错误。
    h.live['scenarios'] = ['schema_reject', 'typed_invalid', 'typed_invalid']
    source = _first_failure(h)
    second = h.run(code='2' * 40, schema_revision='fixed')
    assert second.exit_code == 2
    ledger = _seat(h)
    assert [row.purpose for row in ledger.turns] == ['review', 'schema_recovery', 'correction']
    assert ledger.turns[1].provider_response_sha256 != ledger.turns[2].provider_response_sha256
    assert len(h.live['invocations']) == len(h.live['popens']) == 3
    assert ledger.pending_correction is not None
    assert h.live['applied'] == [] and h.live['repairs'] == []
    _assert_original_preserved(h, source)


@pytest.mark.parametrize('source_drift', [None, 'original_log', 'recovery_proof'])
def test_durable_schema_recovery_action_replays_after_finish_crash_only_with_intact_source(
        schema_run, monkeypatch, source_drift):
    h = schema_run
    source = _first_failure(h)
    seats = agent.reviewer_seat_ledger
    actual_finish = seats.finish
    finish_failures = []

    def interrupted_finish(base, sid, *, turn_id, exit_code, **kwargs):
        ledger = seats.read(
            base, sid, waiting_on=kwargs['waiting_on'],
            business_sha256=kwargs['business_sha256'])
        turn = next(row for row in ledger.turns if row.turn_id == turn_id)
        if (turn.purpose == 'schema_recovery' and exit_code == 0
                and not finish_failures):
            [(path, record)] = uploads.load_reviewer_actions(base, sid)
            assert record.reviewer_seat_turn_id == turn_id
            assert record.reviewer_session_run_id == turn.session_run_id
            assert record.provider_response_sha256 == turn.provider_response_sha256
            assert turn.provider_response_sha256 is not None
            assert turn.outcome == 'started'
            assert approvals._read_raw(base, sid).design_decision is None
            finish_failures.append((path, path.read_bytes()))
            raise OSError('测试：有效决定已持久化，Reviewer 席位封账前进程中断')
        return actual_finish(base, sid, turn_id=turn_id, exit_code=exit_code, **kwargs)

    monkeypatch.setattr(seats, 'finish', interrupted_finish)
    interrupted = h.run(code='2' * 40, schema_revision='fixed')
    assert interrupted.exit_code == 70
    assert len(finish_failures) == 1
    assert len(h.live['invocations']) == len(h.live['popens']) == 2
    assert h.live['applied'] == [] and h.live['repairs'] == []
    assert _seat(h).turns[1].outcome == 'started'
    _assert_original_preserved(h, source)
    active_path, active_bytes = finish_failures[0]
    assert active_path.read_bytes() == active_bytes
    recovery_log = agent.run_log_path(h.base, interrupted.run_id)
    recovery_log_bytes = recovery_log.read_bytes()
    costs_before_replay = ledger_path(h.base).read_bytes()
    task = h.live['tasks'][0]
    seat_path = seats.ledger_path(
        h.base, SID, waiting_on=WAITING, business_sha256=task.business_sha256)
    if source_drift == 'original_log':
        source.source_log.write_bytes(source.log_bytes + b'\n$ evidence-drift\n')
    elif source_drift == 'recovery_proof':
        changed = json.loads(seat_path.read_text('utf-8'))
        changed['turns'][1]['schema_recovery']['source_log_sha256'] = 'f' * 64
        seat_path.write_bytes(_canonical(changed) + b'\n')
    before_replay_seat = seat_path.read_bytes()
    monkeypatch.setattr(seats, 'finish', actual_finish)

    recovered = h.run()

    assert len(h.live['invocations']) == len(h.live['popens']) == 2
    assert ledger_path(h.base).read_bytes() == costs_before_replay
    assert recovery_log.read_bytes() == recovery_log_bytes
    if source_drift is not None:
        assert recovered.exit_code == 70
        assert recovered.outcome.state == 'unknown'
        assert h.live['applied'] == [] and h.live['repairs'] == []
        assert approvals._read_raw(h.base, SID).design_decision is None
        assert active_path.read_bytes() == active_bytes
        assert seat_path.read_bytes() == before_replay_seat
        return
    assert recovered.exit_code == 2
    assert len(h.live['applied']) == len(h.live['repairs']) == 1
    assert approvals.stage4_review_payload_landed(
        h.base, SID, surface='design', payload=h.live['applied'][0])
    assert uploads.load_reviewer_actions(h.base, SID) == []
    ledger = _seat(h)
    assert len(ledger.turns) == 2
    assert ledger.turns[1].outcome == 'ok' and ledger.turns[1].exit_code == 0
    assert ledger.turns[1].parent_run_id == interrupted.run_id
    _assert_original_preserved(h, source)
