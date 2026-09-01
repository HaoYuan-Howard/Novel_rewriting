#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批次四 parent run / execution epoch 的零模型生命周期测试。"""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

from contracts.schema.orchestration import (
    AgentDeliveryOutcome, AgentProfile, AgentRunEvent, ExecutionEpoch,
)
from novel.commanders import (
    ClaudeCodeCommanderDriver, CodexCommanderDriver,
    CommanderProtocolError,
)
from novel.orchestrator.jobs import JobConflict
from novel.orchestrator.agent_supervisor import (
    CommanderTurnResult, RunSupervisor, SupervisorActionResult,
)


def _outcome(state: str = 'stopped') -> AgentDeliveryOutcome:
    return AgentDeliveryOutcome(state=state, reason='测试事实', checked_at=1)


def _epoch(epoch_id: str, ordinal: int, reason: str,
           resume: str | None = None) -> ExecutionEpoch:
    return ExecutionEpoch(
        epoch_id=epoch_id, ordinal=ordinal, reason=reason,
        argv=['commander', '-'], prompt_sha256='b' * 64,
        protocol_version='test-protocol', cli_version='test-cli',
        code_version=f'code-{ordinal}',
        skill_sha256='a' * 64, session_run_id='session-1',
        resume_provider_session_id=resume)


def test_supervisor_exact_resumes_without_artificial_turn_limit():
    events: list[AgentRunEvent] = []
    calls: list[str | None] = []
    initial = _epoch('run-1', 1, 'initial')

    def run_turn(_identity, resume):
        calls.append(resume)
        return CommanderTurnResult(
            exit_code=0, provider_session_id='provider-1',
            continue_ready=len(calls) < 4, outcome=_outcome())

    supervisor = RunSupervisor(
        run_id='run-1', append_event=events.append,
        make_epoch=lambda *_args: pytest.fail('身份未漂移不应滚 epoch'),
        open_epoch=lambda _epoch: None,
        close_epoch=lambda _epoch, _turn: 'drained',
        run_commander_turn=run_turn,
        epoch_identity_current=lambda _epoch: True,
        cancel_commander=lambda: True,
        initial_epoch=initial)

    result = supervisor.run()

    assert result.stopped is False
    assert calls == [None, 'provider-1', 'provider-1', 'provider-1']
    assert [event.kind for event in events] == ['epoch_closed']
    assert events[0].epoch_id == initial.epoch_id


def test_v2_supervisor_routes_ready_review_and_complete_without_commander_dispatch():
    """ready/complete 属于确定性面；只有闭集 blocked 才进入 reviewer callback。"""
    events: list[AgentRunEvent] = []
    calls: list[str] = []
    states = ['ready', 'blocked', 'pipeline_complete']

    def current():
        state = states[0]
        action = None
        token = None
        if state == 'ready':
            token = SimpleNamespace(node='stage0.remove')
            action = SimpleNamespace(waiting_on=None, action_sha256='a' * 64)
        elif state == 'blocked':
            action = SimpleNamespace(
                waiting_on='stage0.approve', action_sha256='b' * 64)
        return SimpleNamespace(
            state=state, code_version='code-1', surface_seal='c' * 64,
            action=action, execution_token=token)

    def ready(_epoch, snapshot, ticket):
        calls.append(f'ready:{snapshot.execution_token.node}')
        ticket.commit()
        states.pop(0)
        return SupervisorActionResult(0, _outcome(), True)

    def reviewer(_epoch, snapshot, resume, ticket):
        calls.append(f'reviewer:{snapshot.action.waiting_on}:{resume}')
        ticket.commit()
        states.pop(0)
        return CommanderTurnResult(0, 'provider-1', True, _outcome())

    def finalize(_epoch, ticket):
        calls.append('finalize')
        ticket.commit()
        states.pop(0)
        return SupervisorActionResult(0, _outcome('ready'), False)

    supervisor = RunSupervisor(
        run_id='run-v2-route', append_event=events.append,
        make_epoch=lambda *_args: pytest.fail('身份未漂移不应滚 epoch'),
        open_epoch=lambda _epoch: None,
        close_epoch=lambda _epoch, _turn: 'drained',
        run_commander_turn=lambda *_args: pytest.fail('v2 不得落回 legacy Commander'),
        epoch_identity_current=lambda _epoch: True,
        cancel_commander=lambda: True,
        initial_epoch=_epoch('epoch-v2-route', 1, 'initial'),
        read_operator_next=lambda _epoch: current(),
        run_ready_action=ready,
        run_reviewer=reviewer,
        finalize_run=finalize)

    result = supervisor.run()

    assert result.stopped is False
    assert calls == [
        'ready:stage0.remove', 'reviewer:stage0.approve:None', 'finalize']
    assert states == []
    assert [event.kind for event in events] == ['epoch_closed']


@pytest.mark.parametrize('control', ['pause', 'stop'])
def test_launch_ticket_never_accepts_control_before_process_is_registered(control):
    """pause/stop 返回即代表它已经看见本次 Popen，不能随后再冒出新进程。"""
    entered = threading.Event()
    allow_commit = threading.Event()
    allow_return = threading.Event()
    control_returned = threading.Event()
    process_registered = threading.Event()
    events: list[AgentRunEvent] = []

    current = SimpleNamespace(
        state='ready', code_version='code-1', surface_seal='d' * 64,
        action=SimpleNamespace(waiting_on=None, action_sha256='e' * 64),
        execution_token=SimpleNamespace(node='stage0.remove'))

    def ready(_epoch, _current, ticket):
        entered.set()
        assert allow_commit.wait(5)
        process_registered.set()
        ticket.commit()
        assert allow_return.wait(5)
        return SupervisorActionResult(0, _outcome(), False)

    supervisor = RunSupervisor(
        run_id=f'run-ticket-{control}', append_event=events.append,
        make_epoch=lambda *_args: pytest.fail('不应滚 epoch'),
        open_epoch=lambda _epoch: None,
        close_epoch=lambda _epoch, _turn: 'drained',
        run_commander_turn=lambda *_args: pytest.fail('不应走 legacy'),
        epoch_identity_current=lambda _epoch: True,
        cancel_commander=lambda: True,
        set_commander_quiesced=lambda _value: True,
        initial_epoch=_epoch(f'epoch-ticket-{control}', 1, 'initial'),
        read_operator_next=lambda _epoch: current,
        run_ready_action=ready,
        run_reviewer=lambda *_args: None,
        finalize_run=lambda *_args: pytest.fail('不应 finalize'))
    worker = threading.Thread(target=supervisor.run)
    worker.start()
    assert entered.wait(5)

    def request_control():
        if control == 'pause':
            supervisor.request_pause()
        else:
            supervisor.request_stop()
        control_returned.set()

    requester = threading.Thread(target=request_control)
    requester.start()
    assert not control_returned.wait(0.1)
    allow_commit.set()
    assert control_returned.wait(5)
    assert process_registered.is_set(), '控制请求在 Popen/owner 登记前提前返回'
    allow_return.set()
    if control == 'pause':
        deadline = time.time() + 5
        while time.time() < deadline and not supervisor.paused:
            time.sleep(0.01)
        assert supervisor.paused
        supervisor.request_resume()
    requester.join(5)
    worker.join(5)
    assert not worker.is_alive()


def test_supervisor_rolls_epoch_and_drops_old_provider_on_identity_change():
    events: list[AgentRunEvent] = []
    resumes: list[str | None] = []
    identity_checks = 0

    def run_turn(_identity, resume):
        resumes.append(resume)
        return CommanderTurnResult(
            exit_code=0,
            provider_session_id=('provider-old' if len(resumes) == 1
                                 else 'provider-new'),
            continue_ready=len(resumes) == 1, outcome=_outcome())

    def identity_current(_epoch):
        nonlocal identity_checks
        identity_checks += 1
        return identity_checks > 1

    supervisor = RunSupervisor(
        run_id='run-2', append_event=events.append,
        make_epoch=lambda reason, ordinal, resume: _epoch(
            'epoch-2', ordinal, reason, resume),
        open_epoch=lambda _epoch: None,
        close_epoch=lambda _epoch, _turn: 'drained',
        run_commander_turn=run_turn,
        epoch_identity_current=identity_current,
        cancel_commander=lambda: True,
        initial_epoch=_epoch('run-2', 1, 'initial'))

    supervisor.run()

    assert resumes == [None, None]
    assert [event.kind for event in events] == [
        'epoch_closed', 'epoch_opened', 'epoch_closed']
    assert events[1].epoch is not None
    assert events[1].epoch.reason == 'code_change'


def test_supervisor_closes_first_epoch_when_first_turn_raises():
    """已写 epoch_opened 后即使首 turn 未形成结果，也必须以 70/unknown 闭合。"""
    events: list[AgentRunEvent] = []
    closed: list[tuple[str, int]] = []
    epoch = _epoch('epoch-first-failure', 1, 'initial')

    def run_turn(_epoch, _resume):
        raise RuntimeError('spawn failed')

    supervisor = RunSupervisor(
        run_id='run-first-failure', append_event=events.append,
        make_epoch=lambda *_args: pytest.fail('不应滚新 epoch'),
        open_epoch=lambda _epoch: None,
        close_epoch=lambda current, turn: (
            closed.append((current.epoch_id, turn.exit_code)) or 'unknown'),
        run_commander_turn=run_turn,
        epoch_identity_current=lambda _epoch: True,
        cancel_commander=lambda: True,
        initial_epoch=epoch)

    with pytest.raises(RuntimeError, match='spawn failed'):
        supervisor.run()

    assert closed == [('epoch-first-failure', 70)]
    assert [event.kind for event in events] == ['epoch_closed']
    assert events[0].epoch_id == 'epoch-first-failure'
    assert events[0].exit_code == 70


def test_new_epoch_first_turn_failure_never_reuses_previous_epoch_result():
    """跨 epoch 的首 turn 失败不能拿上一 epoch 的成功退出码关闭新身份。"""
    events: list[AgentRunEvent] = []
    closed: list[tuple[str, int]] = []
    calls = 0

    def run_turn(epoch, _resume):
        nonlocal calls
        calls += 1
        if calls == 1:
            return CommanderTurnResult(
                exit_code=0, provider_session_id='provider-old',
                continue_ready=True, outcome=_outcome())
        assert epoch.epoch_id == 'epoch-new'
        raise RuntimeError('new epoch failed')

    supervisor = RunSupervisor(
        run_id='run-cross-epoch-failure', append_event=events.append,
        make_epoch=lambda reason, ordinal, resume: _epoch(
            'epoch-new', ordinal, reason, resume),
        open_epoch=lambda _epoch: None,
        close_epoch=lambda current, turn: (
            closed.append((current.epoch_id, turn.exit_code)) or 'unknown'),
        run_commander_turn=run_turn,
        # 首 turn 后滚 epoch；新 epoch 的首 turn 在再次检查身份前就异常。
        epoch_identity_current=lambda epoch: epoch.epoch_id != 'epoch-old',
        cancel_commander=lambda: True,
        initial_epoch=_epoch('epoch-old', 1, 'initial'))

    with pytest.raises(RuntimeError, match='new epoch failed'):
        supervisor.run()

    assert closed == [('epoch-old', 0), ('epoch-new', 70)]
    assert [event.kind for event in events] == [
        'epoch_closed', 'epoch_opened', 'epoch_closed']
    assert events[-1].epoch_id == 'epoch-new'
    assert events[-1].exit_code == 70


def test_pause_keeps_the_same_epoch_and_exact_provider_session():
    """pause 只冻结下一 turn；代码未变时不得伪造一次 epoch rollover。"""
    events: list[AgentRunEvent] = []
    calls: list[tuple[str, str | None]] = []
    first_returned = threading.Event()
    allow_second = threading.Event()
    initial = _epoch('run-pause', 1, 'initial')

    def run_turn(epoch, resume):
        calls.append((epoch.epoch_id, resume))
        if len(calls) == 1:
            first_returned.set()
            return CommanderTurnResult(
                exit_code=0, provider_session_id='provider-pause',
                continue_ready=True, outcome=_outcome())
        assert allow_second.wait(5)
        return CommanderTurnResult(
            exit_code=0, provider_session_id='provider-pause',
            continue_ready=False, outcome=_outcome())

    supervisor = RunSupervisor(
        run_id='run-pause', append_event=events.append,
        make_epoch=lambda *_args: pytest.fail('同身份 pause/resume 不得滚 epoch'),
        open_epoch=lambda _epoch: None,
        close_epoch=lambda _epoch, _turn: 'drained',
        run_commander_turn=run_turn,
        epoch_identity_current=lambda _epoch: True,
        cancel_commander=lambda: True,
        initial_epoch=initial)
    supervisor.request_pause()
    worker = threading.Thread(target=supervisor.run)
    worker.start()

    assert first_returned.wait(5)
    for _ in range(100):
        if supervisor.paused:
            break
        __import__('time').sleep(0.01)
    assert supervisor.paused
    assert [event.kind for event in events] == ['paused']

    supervisor.request_resume()
    allow_second.set()
    worker.join(5)

    assert not worker.is_alive()
    assert calls == [
        ('run-pause', None),
        ('run-pause', 'provider-pause'),
    ]
    assert [event.kind for event in events] == [
        'paused', 'resumed', 'epoch_closed']


def test_concurrent_resume_is_accepted_exactly_once():
    """worker 离开 barrier 前的第二个 resume 也必须关闭式冲突。"""
    events: list[AgentRunEvent] = []
    current = SimpleNamespace(
        state='ready', code_version='code-1', surface_seal='f' * 64,
        action=SimpleNamespace(waiting_on=None, action_sha256='a' * 64),
        execution_token=SimpleNamespace(node='stage0.remove'))

    def ready(_epoch, _current, ticket):
        ticket.commit()
        return SupervisorActionResult(0, _outcome(), False)

    supervisor = RunSupervisor(
        run_id='run-one-resume', append_event=events.append,
        make_epoch=lambda *_args: pytest.fail('不应滚 epoch'),
        open_epoch=lambda _epoch: None,
        close_epoch=lambda _epoch, _turn: 'drained',
        run_commander_turn=lambda *_args: pytest.fail('不走 legacy'),
        epoch_identity_current=lambda _epoch: True,
        cancel_commander=lambda: True,
        initial_epoch=_epoch('epoch-one-resume', 1, 'initial'),
        read_operator_next=lambda _epoch: current,
        run_ready_action=ready,
        run_reviewer=lambda *_args: None,
        finalize_run=lambda *_args: pytest.fail('不应 finalize'))
    supervisor.request_pause()
    worker = threading.Thread(target=supervisor.run)
    worker.start()
    deadline = time.time() + 5
    while time.time() < deadline and not supervisor.paused:
        time.sleep(0.01)
    assert supervisor.paused

    supervisor.request_resume()
    with pytest.raises(JobConflict):
        supervisor.request_resume()
    worker.join(5)

    assert not worker.is_alive()
    assert [event.kind for event in events].count('resumed') == 1


def test_pause_quiesce_exit_is_not_mistaken_for_pipeline_terminal():
    """关闭 gate 导致的非零/无 ready 退出仍须先落安全 paused。"""
    events: list[AgentRunEvent] = []
    calls: list[str | None] = []
    initial = _epoch('run-pause-quiesce', 1, 'initial')

    def run_turn(_epoch, resume):
        calls.append(resume)
        if len(calls) == 1:
            return CommanderTurnResult(
                exit_code=2, provider_session_id='provider-quiesced',
                continue_ready=False, outcome=_outcome('stopped'))
        return CommanderTurnResult(
            exit_code=0, provider_session_id='provider-quiesced',
            continue_ready=False, outcome=_outcome('ready'))

    supervisor = RunSupervisor(
        run_id='run-pause-quiesce', append_event=events.append,
        make_epoch=lambda *_args: pytest.fail('同身份恢复不得滚 epoch'),
        open_epoch=lambda _epoch: None,
        close_epoch=lambda _epoch, _turn: 'drained',
        run_commander_turn=run_turn,
        epoch_identity_current=lambda _epoch: True,
        cancel_commander=lambda: True,
        initial_epoch=initial)
    supervisor.request_pause()
    worker = threading.Thread(target=supervisor.run)
    worker.start()

    for _ in range(100):
        if supervisor.paused:
            break
        __import__('time').sleep(0.01)
    assert supervisor.paused
    assert calls == [None]
    assert [event.kind for event in events] == ['paused']

    supervisor.request_resume()
    worker.join(5)

    assert not worker.is_alive()
    assert calls == [None, 'provider-quiesced']
    assert [event.kind for event in events] == [
        'paused', 'resumed', 'epoch_closed']


def test_code_drift_while_paused_opens_a_new_epoch_without_old_provider_context():
    events: list[AgentRunEvent] = []
    calls: list[tuple[str, str | None]] = []
    drifted = False

    def run_turn(epoch, resume):
        calls.append((epoch.epoch_id, resume))
        return CommanderTurnResult(
            exit_code=0,
            provider_session_id=(
                'provider-old' if epoch.epoch_id == 'run-pause-drift'
                else 'provider-new'),
            # pause 关闭 admission 后即使旧 turn 看不见 ready action，resume
            # 发现身份漂移时仍必须滚新 epoch，不能拿这枚 False 结束 parent。
            continue_ready=False,
            outcome=_outcome())

    def identity_current(epoch):
        return epoch.epoch_id != 'run-pause-drift' or not drifted

    supervisor = RunSupervisor(
        run_id='run-pause-drift', append_event=events.append,
        make_epoch=lambda reason, ordinal, resume: _epoch(
            'epoch-after-drift', ordinal, reason, resume),
        open_epoch=lambda _epoch: None,
        close_epoch=lambda _epoch, _turn: 'drained',
        run_commander_turn=run_turn,
        epoch_identity_current=identity_current,
        cancel_commander=lambda: True,
        initial_epoch=_epoch('run-pause-drift', 1, 'initial'))
    supervisor.request_pause()
    worker = threading.Thread(target=supervisor.run)
    worker.start()

    for _ in range(100):
        if supervisor.paused:
            break
        __import__('time').sleep(0.01)
    assert supervisor.paused
    drifted = True
    supervisor.request_resume()
    worker.join(5)

    assert not worker.is_alive()
    assert calls == [
        ('run-pause-drift', None),
        ('epoch-after-drift', None),
    ]
    assert [event.kind for event in events] == [
        'paused', 'resumed', 'epoch_closed', 'epoch_opened', 'epoch_closed']
    assert events[3].epoch is not None
    assert events[3].epoch.reason == 'code_change'
    assert events[3].epoch.resume_provider_session_id is None


@pytest.mark.parametrize('driver_name', ['codex', 'claude-code'])
def test_commander_resume_argv_and_returned_session_are_exact(
        tmp_path, driver_name):
    repo = tmp_path / 'repo'
    base = tmp_path / 'base'
    repo.mkdir()
    base.mkdir()
    expected = 'provider-session-1'
    profile = AgentProfile(
        driver=driver_name,
        model=('gpt-5.6-sol' if driver_name == 'codex' else 'claude-opus-5'),
        reasoning_effort=('max' if driver_name == 'codex' else 'high'),
        execution_mode='standard')
    if driver_name == 'codex':
        stdout = ''.join(json.dumps(row) + '\n' for row in (
            {'type': 'thread.started', 'thread_id': 'wrong-session'},
            {'type': 'turn.completed'},
        ))
        driver = CodexCommanderDriver(
            executable='codex.exe',
            runner=lambda argv, **_kwargs: __import__('subprocess').CompletedProcess(
                argv, 0, stdout, ''))
    else:
        stdout = json.dumps({
            'type': 'result', 'subtype': 'success', 'is_error': False,
            'session_id': 'wrong-session', 'usage': {},
        }) + '\n'
        driver = ClaudeCodeCommanderDriver(
            executable='claude.exe',
            runner=lambda argv, **_kwargs: __import__('subprocess').CompletedProcess(
                argv, 0, stdout, ''))
    invocation = driver.build_invocation(
        profile, '继续', repo=repo, base=base,
        resume_provider_session_id=expected)

    assert invocation.resume_provider_session_id == expected
    assert expected in invocation.argv
    if driver_name == 'codex':
        assert invocation.argv.index('--add-dir') < invocation.argv.index('resume')
        assert invocation.argv[-3:] == ('resume', expected, '-')
    with pytest.raises(CommanderProtocolError, match='不同'):
        driver.run(invocation, log=__import__('io').BytesIO(), run_id='run-1')
