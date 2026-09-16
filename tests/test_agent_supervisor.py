#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批次四 parent run / execution epoch 的零模型生命周期测试。

Commander reviewer 已退役（2026-09-15 用户裁决）：Supervisor 只保留确定性主路
（ready executor token、登记的 deterministic recovery、交付终验）与人工审批闸
停止语义；本文件按新接口守住 pause / resume / stop / epoch 滚动 / 恢复边界。
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from contracts.schema.orchestration import (
    AgentDeliveryOutcome, AgentRunEvent, ExecutionEpoch,
)
from novel.orchestrator.agent_supervisor import (
    ExecutionTurnResult, RunSupervisor, SupervisorActionResult,
)
from novel.orchestrator.jobs import JobConflict


def _outcome(state: str = 'stopped') -> AgentDeliveryOutcome:
    return AgentDeliveryOutcome(state=state, reason='测试事实', checked_at=1)


def _epoch(epoch_id: str, ordinal: int, reason: str,
           resume: str | None = None) -> ExecutionEpoch:
    """构造一条现役形状的 epoch；``resume`` 只为历史行解码兼容保留。"""
    return ExecutionEpoch(
        epoch_id=epoch_id, ordinal=ordinal, reason=reason,
        argv=None, prompt_sha256=None,
        protocol_version='supervisor-executor-v1', cli_version=None,
        code_version=f'code-{ordinal}', skill_sha256=None,
        session_run_id=f'session-{epoch_id}',
        resume_provider_session_id=resume)


def _current(*, state: str, waiting_on: str | None = None,
             node: str = 'stage0.remove') -> SimpleNamespace:
    action = None
    token = None
    if state == 'ready':
        token = SimpleNamespace(node=node)
        action = SimpleNamespace(node=node, waiting_on=None, action_sha256='a' * 64)
    elif state == 'blocked':
        action = SimpleNamespace(
            node=node, flags=[], waiting_on=waiting_on, action_sha256='b' * 64)
    return SimpleNamespace(state=state, code_version='code-1',
                           action=action, execution_token=token)


# ── 人工审批闸：非确定性 blocked 停止 parent，不启动任何模型进程 ───────────

def test_non_deterministic_blocked_stops_at_human_approval_gate():
    """批次 B4：HUMAN_NODES 已空；非登记 blocker 停在阻断面，指引恢复后续跑。"""
    events = []
    closed = []
    current = _current(state='blocked', waiting_on='stage0.approve')

    supervisor = RunSupervisor(
        run_id='run-human-gate', append_event=events.append,
        make_epoch=lambda *_args: pytest.fail('阻断面不滚新 epoch'),
        open_epoch=lambda _epoch: None,
        close_epoch=lambda epoch, turn: (
            closed.append((epoch.epoch_id, turn.exit_code)) or 'drained'),
        epoch_identity_current=lambda _epoch: True,
        cancel_active=lambda: True,
        initial_epoch=_epoch('epoch-human-gate', 1, 'initial'),
        read_operator_next=lambda _epoch: current,
        run_ready_action=lambda *_args: pytest.fail('阻断面不得执行 ready'),
        run_blocked_action=lambda *_args: pytest.fail('阻断面不得执行恢复动作'),
        deterministic_blockers=frozenset({'recovery:stage4-design-action'}),
        finalize_run=lambda *_args: pytest.fail('阻断面不做交付终验'))

    result = supervisor.run()

    assert result.stopped is False
    assert result.turn is not None
    assert result.turn.exit_code == 2
    assert result.turn.continue_ready is False
    assert result.turn.outcome.state == 'stopped'
    assert result.turn.outcome.reason == (
        '停在阻断面：stage0.approve；按该 waiting_on 对应恢复入口收口后用原配置续跑')
    assert closed == [('epoch-human-gate', 2)]
    assert [event.kind for event in events] == ['epoch_closed']


def test_deterministic_blocked_still_dispatches_registered_recovery():
    """闭集内的 deterministic blocker 仍由 callback 执行，不落入人工闸。"""
    events: list[AgentRunEvent] = []
    calls: list[str] = []
    states = ['blocked', 'pipeline_complete']

    def read_next(_epoch):
        if states[0] == 'pipeline_complete':
            return SimpleNamespace(state='pipeline_complete', code_version='code-1')
        return _current(state='blocked', waiting_on='recovery:stage4-design-action',
                        node='stage4.design')

    def blocked(_epoch, current, ticket):
        calls.append(current.action.waiting_on)
        ticket.commit()
        states.pop(0)
        return SupervisorActionResult(0, _outcome(), True)

    def finalize(_epoch, ticket):
        calls.append('finalize')
        ticket.commit()
        states.pop(0)
        return SupervisorActionResult(0, _outcome('ready'), False)

    supervisor = RunSupervisor(
        run_id='run-deterministic-recovery', append_event=events.append,
        make_epoch=lambda *_args: pytest.fail('身份未漂移不应滚 epoch'),
        open_epoch=lambda _epoch: None,
        close_epoch=lambda _epoch, _turn: 'drained',
        epoch_identity_current=lambda _epoch: True,
        cancel_active=lambda: True,
        initial_epoch=_epoch('epoch-deterministic-recovery', 1, 'initial'),
        read_operator_next=read_next,
        run_ready_action=lambda *_args: pytest.fail('本例没有 ready node'),
        run_blocked_action=blocked,
        deterministic_blockers=frozenset({'recovery:stage4-design-action'}),
        finalize_run=finalize)

    result = supervisor.run()

    assert result.stopped is False
    assert calls == ['recovery:stage4-design-action', 'finalize']
    assert [event.kind for event in events] == ['epoch_closed']


def test_supervisor_requires_the_full_deterministic_main_path():
    """确定性主路三个 callback 必须同时提供；blocked 路由与闭集成对。"""
    common = dict(
        run_id='run-incomplete', append_event=lambda _event: None,
        make_epoch=lambda *_args: pytest.fail('不应滚 epoch'),
        open_epoch=lambda _epoch: None,
        close_epoch=lambda _epoch, _turn: 'drained',
        epoch_identity_current=lambda _epoch: True,
        cancel_active=lambda: True,
        initial_epoch=_epoch('epoch-incomplete', 1, 'initial'),
        read_operator_next=lambda _epoch: _current(state='pipeline_complete'))
    with pytest.raises(ValueError, match='三个 callback 必须同时提供'):
        RunSupervisor(**{**common,
                         'run_ready_action': lambda *_args: pytest.fail('x')})
    with pytest.raises(ValueError, match='成对提供'):
        RunSupervisor(**{**common,
                         'run_ready_action': lambda *_args: pytest.fail('x'),
                         'finalize_run': lambda *_args: pytest.fail('x'),
                         'run_blocked_action': lambda *_args: pytest.fail('x')})
    with pytest.raises(ValueError, match='成对提供'):
        RunSupervisor(**{**common,
                         'run_ready_action': lambda *_args: pytest.fail('x'),
                         'finalize_run': lambda *_args: pytest.fail('x'),
                         'deterministic_blockers': frozenset({'recovery:x'})})
    with pytest.raises(ValueError, match='子集'):
        RunSupervisor(**{**common,
                         'run_ready_action': lambda *_args: pytest.fail('x'),
                         'finalize_run': lambda *_args: pytest.fail('x'),
                         'run_blocked_action': lambda *_args: pytest.fail('x'),
                         'deterministic_blockers': frozenset({'recovery:x'}),
                         'fresh_epoch_blockers': frozenset({'recovery:other'})})


# ── ready producer 边界与 epoch 滚动 ─────────────────────────────────────

def test_ready_producer_boundary_drains_then_rereads_without_new_turn():
    events, calls = [], []
    done = False
    initial = _epoch('first', 1, 'initial')

    def read(epoch):
        calls.append(('read', epoch.session_run_id))
        return SimpleNamespace(
            state='pipeline_complete' if done else 'ready', code_version='code-1',
            action=SimpleNamespace(node='stage4.design', flags=[]),
            execution_token=SimpleNamespace(node='stage4.design'))

    def fresh(reason, ordinal):
        assert (reason, ordinal) == ('continue', 2)
        return _epoch('second', 2, reason).model_copy(update={
            'code_version': 'code-1'})

    def ready(epoch, current, ticket):
        nonlocal done
        assert current.execution_token is not None
        calls.append(('run', epoch.session_run_id))
        ticket.commit()
        done = True
        return SupervisorActionResult(0, _outcome(), True)

    def finish(epoch, ticket):
        ticket.commit()
        return SupervisorActionResult(0, _outcome('ready'), False)

    supervisor = RunSupervisor(run_id='ready-rebuild', append_event=events.append,
        make_epoch=fresh, open_epoch=lambda _epoch: None,
        close_epoch=lambda epoch, _turn: (calls.append(('drain', epoch.session_run_id)) or 'drained'),
        epoch_identity_current=lambda _epoch: True, cancel_active=lambda: True,
        initial_epoch=initial, read_operator_next=read, run_ready_action=ready,
        ready_requires_fresh_epoch=lambda epoch, _current: epoch.session_run_id == 'session-first',
        run_blocked_action=lambda *_: pytest.fail('重建边界不执行恢复'),
        deterministic_blockers=frozenset({'recovery:x'}),
        finalize_run=finish)
    assert not supervisor.run().stopped
    assert calls == [('read', 'session-first'), ('drain', 'session-first'),
                     ('read', 'session-second'), ('run', 'session-second'),
                     ('read', 'session-second'), ('drain', 'session-second')]
    assert [event.kind for event in events].count('epoch_closed') == 2


@pytest.mark.parametrize('flags', [
    ['--review-token', 's4rr1_' + 'd' * 64],
    ['--replan', '--review-token', 's4rr1_' + 'e' * 64],
], ids=['targeted-repair', 'strategy-replan'])
def test_stage4_design_action_rolls_fresh_epoch_before_executor(flags):
    """repair/replan 都先换 producer epoch，再重读、铸权；不占人工闸。"""
    events: list[AgentRunEvent] = []
    calls: list[tuple[str, str]] = []
    reads: list[str] = []
    opened: list[str] = []
    states = ['blocked', 'pipeline_complete']

    def current(epoch):
        reads.append(epoch.epoch_id)
        if states[0] == 'pipeline_complete':
            return SimpleNamespace(
                state='pipeline_complete', code_version='code-1')
        return _current(state='blocked', waiting_on='recovery:stage4-design-action',
                        node='stage4.design')

    def blocked(epoch, snapshot, ticket):
        calls.append((epoch.session_run_id, snapshot.action.node))
        ticket.commit()
        states.pop(0)
        return SupervisorActionResult(0, _outcome(), True)

    def finalize(_epoch, ticket):
        calls.append((_epoch.session_run_id, 'finalize'))
        ticket.commit()
        states.pop(0)
        return SupervisorActionResult(0, _outcome('ready'), False)

    def make_epoch(reason, ordinal):
        assert (reason, ordinal) == ('continue', 2)
        return _epoch('epoch-v2-recovery-fresh', ordinal, reason).model_copy(
            update={'code_version': 'code-1'})

    supervisor = RunSupervisor(
        run_id='run-v2-recovery', append_event=events.append,
        make_epoch=make_epoch,
        open_epoch=lambda epoch: opened.append(epoch.epoch_id),
        close_epoch=lambda _epoch, _turn: 'drained',
        epoch_identity_current=lambda _epoch: True,
        cancel_active=lambda: True,
        initial_epoch=_epoch('epoch-v2-recovery', 1, 'initial'),
        read_operator_next=current,
        run_ready_action=lambda *_args: pytest.fail('本例没有 ready node'),
        run_blocked_action=blocked,
        deterministic_blockers=frozenset({'recovery:stage4-design-action'}),
        fresh_epoch_blockers=frozenset({'recovery:stage4-design-action'}),
        finalize_run=finalize)

    result = supervisor.run()

    assert result.stopped is False
    assert calls == [
        ('session-epoch-v2-recovery-fresh', 'stage4.design'),
        ('session-epoch-v2-recovery-fresh', 'finalize')]
    assert reads == [
        'epoch-v2-recovery', 'epoch-v2-recovery-fresh',
        'epoch-v2-recovery-fresh']
    assert opened == ['epoch-v2-recovery-fresh']
    assert states == []
    assert [event.kind for event in events] == [
        'epoch_closed', 'epoch_opened', 'epoch_closed']
    assert events[1].epoch is not None
    assert events[1].epoch.reason == 'continue'
    assert events[1].epoch.argv is None and events[1].epoch.prompt_sha256 is None
    assert events[1].epoch.resume_provider_session_id is None


def test_stage4_design_action_rolls_once_per_action_sha_without_hot_loop():
    """同一 action 留在盘上时可重试执行，但不能每次重读都再滚 epoch。"""
    events: list[AgentRunEvent] = []
    executions: list[str] = []
    complete = False
    makes = 0

    def next_action(_epoch):
        if complete:
            return SimpleNamespace(
                state='pipeline_complete', code_version='code-1')
        return _current(state='blocked', waiting_on='recovery:stage4-design-action',
                        node='stage4.design')

    def make_epoch(reason, ordinal):
        nonlocal makes
        makes += 1
        return _epoch('fresh-once', ordinal, reason).model_copy(
            update={'code_version': 'code-1'})

    def recover(epoch, _current, ticket):
        nonlocal complete
        executions.append(epoch.epoch_id)
        ticket.commit()
        complete = len(executions) == 2
        return SupervisorActionResult(0, _outcome(), True)

    supervisor = RunSupervisor(
        run_id='run-fresh-once', append_event=events.append,
        make_epoch=make_epoch, open_epoch=lambda _epoch: None,
        close_epoch=lambda _epoch, _turn: 'drained',
        epoch_identity_current=lambda _epoch: True,
        cancel_active=lambda: True,
        initial_epoch=_epoch('old-once', 1, 'initial'),
        read_operator_next=next_action,
        run_ready_action=lambda *_args: pytest.fail('没有 ready'),
        run_blocked_action=recover,
        deterministic_blockers=frozenset({'recovery:stage4-design-action'}),
        fresh_epoch_blockers=frozenset({'recovery:stage4-design-action'}),
        finalize_run=lambda _epoch, ticket: (
            ticket.commit() or SupervisorActionResult(
                0, _outcome('ready'), False)))

    result = supervisor.run()

    assert result.stopped is False
    assert makes == 1
    assert executions == ['fresh-once', 'fresh-once']
    assert [row.kind for row in events].count('epoch_opened') == 1


def test_each_outer_parent_rolls_the_same_stage4_action_once():
    """去重只属于当前 outer parent；重启的新 parent 仍须建立自己的 producer 边界。"""
    opened: list[tuple[str, str]] = []

    for index in (1, 2):
        complete = False

        def next_action(_epoch):
            if complete:
                return SimpleNamespace(
                    state='pipeline_complete', code_version='code-1')
            return _current(state='blocked', waiting_on='recovery:stage4-design-action',
                            node='stage4.design')

        def recover(_epoch, _current, ticket):
            nonlocal complete
            complete = True
            ticket.commit()
            return SupervisorActionResult(0, _outcome(), True)

        def make_epoch(reason, ordinal):
            opened.append((f'parent-{index}', reason))
            return _epoch(f'fresh-parent-{index}', ordinal, reason).model_copy(
                update={'code_version': 'code-1'})

        RunSupervisor(
            run_id=f'parent-{index}', append_event=lambda _event: None,
            make_epoch=make_epoch, open_epoch=lambda _epoch: None,
            close_epoch=lambda _epoch, _turn: 'drained',
            epoch_identity_current=lambda _epoch: True,
            cancel_active=lambda: True,
            initial_epoch=_epoch(f'old-parent-{index}', 1, 'initial'),
            read_operator_next=next_action,
            run_ready_action=lambda *_args: pytest.fail('没有 ready'),
            run_blocked_action=recover,
            deterministic_blockers=frozenset({'recovery:stage4-design-action'}),
            fresh_epoch_blockers=frozenset({'recovery:stage4-design-action'}),
            finalize_run=lambda _epoch, ticket: (
                ticket.commit() or SupervisorActionResult(
                    0, _outcome('ready'), False)),
        ).run()

    assert opened == [('parent-1', 'continue'), ('parent-2', 'continue')]


@pytest.mark.parametrize('control', ['pause', 'stop'])
def test_control_after_stage4_boundary_precedes_fresh_epoch(control):
    """fresh epoch 重读返回后、launch 前的控制请求进入 barrier，不得变成 70。"""
    events: list[AgentRunEvent] = []
    makes: list[str] = []
    executions: list[str] = []
    holder: dict[str, RunSupervisor] = {}
    complete = False
    injected = False
    closed: list[tuple[str, int]] = []

    def next_action(epoch):
        nonlocal injected
        if complete:
            return SimpleNamespace(
                state='pipeline_complete', code_version='code-1')
        if epoch.ordinal == 2 and not injected:
            injected = True
            if control == 'pause':
                holder['supervisor'].request_pause()
            else:
                holder['supervisor'].request_stop()
        return _current(state='blocked', waiting_on='recovery:stage4-design-action',
                        node='stage4.design')

    def close_epoch(epoch, turn):
        closed.append((epoch.epoch_id, turn.exit_code))
        return 'drained'

    def make_epoch(reason, ordinal):
        makes.append(reason)
        return _epoch('fresh-after-control', ordinal, reason).model_copy(
            update={'code_version': 'code-1'})

    def recover(epoch, _current, ticket):
        nonlocal complete
        executions.append(epoch.epoch_id)
        complete = True
        ticket.commit()
        return SupervisorActionResult(0, _outcome(), True)

    supervisor = RunSupervisor(
        run_id=f'run-boundary-{control}', append_event=events.append,
        make_epoch=make_epoch, open_epoch=lambda _epoch: None,
        close_epoch=close_epoch,
        epoch_identity_current=lambda _epoch: True,
        cancel_active=lambda: True,
        set_quiesced=lambda _value: True,
        initial_epoch=_epoch(f'old-{control}', 1, 'initial'),
        read_operator_next=next_action,
        run_ready_action=lambda *_args: pytest.fail('没有 ready'),
        run_blocked_action=recover,
        deterministic_blockers=frozenset({'recovery:stage4-design-action'}),
        fresh_epoch_blockers=frozenset({'recovery:stage4-design-action'}),
        finalize_run=lambda _epoch, ticket: (
            ticket.commit() or SupervisorActionResult(
                0, _outcome('ready'), False)))
    holder['supervisor'] = supervisor
    results: list[object] = []
    worker = threading.Thread(target=lambda: results.append(supervisor.run()))
    worker.start()

    if control == 'pause':
        deadline = time.time() + 5
        while time.time() < deadline and not supervisor.paused:
            time.sleep(0.01)
        assert supervisor.paused
        assert makes == ['continue'] and executions == []
        supervisor.request_resume()
    worker.join(5)

    assert not worker.is_alive()
    if control == 'stop':
        assert makes == ['continue'] and executions == []
        assert results and results[0].stopped is True
        assert closed == [(f'old-{control}', 0), ('fresh-after-control', 2)]
    else:
        assert makes == ['continue']
        assert executions == ['fresh-after-control']
        assert results and results[0].stopped is False
        assert all(exit_code != 70 for _epoch_id, exit_code in closed)


def test_code_drift_after_stage4_boundary_does_not_roll_continue_forever():
    """continue epoch 若随即漂移，应再滚 code_change；同 action 不得第三次滚 continue。"""
    events: list[AgentRunEvent] = []
    current_code = 'code-1'
    complete = False
    makes: list[str] = []
    reads: list[str] = []
    executions: list[str] = []

    def make_epoch(reason, ordinal):
        makes.append(reason)
        code = current_code
        return _epoch(f'epoch-{reason}', ordinal, reason).model_copy(
            update={'code_version': code})

    def open_epoch(epoch):
        nonlocal current_code
        if epoch.reason == 'continue':
            current_code = 'code-2'

    def next_action(epoch):
        reads.append(epoch.epoch_id)
        if complete:
            return SimpleNamespace(
                state='pipeline_complete', code_version=current_code)
        return SimpleNamespace(
            state='blocked', code_version=current_code,
            action=SimpleNamespace(
                node='stage4.design', flags=['--replan', '--review-token', 't'],
                waiting_on='recovery:stage4-design-action',
                action_sha256='a' * 64),
            execution_token=None)

    def recover(epoch, _current, ticket):
        nonlocal complete
        executions.append(epoch.epoch_id)
        complete = True
        ticket.commit()
        return SupervisorActionResult(0, _outcome(), True)

    supervisor = RunSupervisor(
        run_id='run-boundary-drift', append_event=events.append,
        make_epoch=make_epoch, open_epoch=open_epoch,
        close_epoch=lambda _epoch, _turn: 'drained',
        epoch_identity_current=lambda epoch: epoch.code_version == current_code,
        cancel_active=lambda: True,
        initial_epoch=_epoch('epoch-initial', 1, 'initial'),
        read_operator_next=next_action,
        run_ready_action=lambda *_args: pytest.fail('没有 ready'),
        run_blocked_action=recover,
        deterministic_blockers=frozenset({'recovery:stage4-design-action'}),
        fresh_epoch_blockers=frozenset({'recovery:stage4-design-action'}),
        finalize_run=lambda _epoch, ticket: (
            ticket.commit() or SupervisorActionResult(
                0, _outcome('ready'), False)))

    result = supervisor.run()

    assert result.stopped is False
    assert makes == ['continue', 'code_change']
    assert reads == ['epoch-initial', 'epoch-code_change', 'epoch-code_change']
    assert executions == ['epoch-code_change']
    assert [
        row.epoch.reason for row in events
        if row.kind == 'epoch_opened' and row.epoch is not None
    ] == ['continue', 'code_change']


def test_operator_read_failure_after_code_drift_rolls_epoch():
    """读 next 期间发生代码漂移时，旧 epoch 应换代而不是把 parent 打成 70。"""
    events: list[AgentRunEvent] = []
    closed: list[tuple[str, int, str]] = []
    reads: list[str] = []
    current_code = 'code-1'

    def identity_current(epoch):
        return epoch.code_version == current_code

    def read_next(epoch):
        nonlocal current_code
        reads.append(epoch.epoch_id)
        if epoch.ordinal == 1:
            current_code = 'code-2'
            raise ValueError(
                'readonly snapshot memo code is code-1, not code-2')
        return SimpleNamespace(
            state='pipeline_complete', code_version='code-2')

    def close(epoch, turn):
        closed.append((epoch.epoch_id, turn.exit_code, turn.outcome.state))
        return 'drained'

    def finalize(_epoch, ticket):
        ticket.commit()
        return SupervisorActionResult(0, _outcome('ready'), False)

    supervisor = RunSupervisor(
        run_id='run-read-drift', append_event=events.append,
        make_epoch=lambda reason, ordinal: _epoch(
            'epoch-after-read-drift', ordinal, reason),
        open_epoch=lambda _epoch: None,
        close_epoch=close,
        epoch_identity_current=identity_current,
        cancel_active=lambda: True,
        initial_epoch=_epoch('epoch-before-read-drift', 1, 'initial'),
        read_operator_next=read_next,
        run_ready_action=lambda *_args: pytest.fail('本例没有 ready node'),
        run_blocked_action=lambda *_args: pytest.fail('本例没有 blocked node'),
        deterministic_blockers=frozenset({'recovery:x'}),
        finalize_run=finalize)

    result = supervisor.run()

    assert result.stopped is False
    assert result.turn is not None and result.turn.exit_code == 0
    assert reads == ['epoch-before-read-drift', 'epoch-after-read-drift']
    assert closed == [
        ('epoch-before-read-drift', 0, 'unknown'),
        ('epoch-after-read-drift', 0, 'ready'),
    ]
    assert [event.kind for event in events] == [
        'epoch_closed', 'epoch_opened', 'epoch_closed']
    assert events[1].epoch is not None
    assert events[1].epoch.reason == 'code_change'
    assert events[1].epoch.resume_provider_session_id is None


def test_operator_read_failure_without_drift_remains_a_crash():
    """身份仍 current 的读错是真故障，不能被宽泛伪装成一次正常换 epoch。"""
    events: list[AgentRunEvent] = []
    closed: list[tuple[str, int]] = []

    def read_next(_epoch):
        raise RuntimeError('stable operator bug')

    supervisor = RunSupervisor(
        run_id='run-stable-read-failure', append_event=events.append,
        make_epoch=lambda *_args: pytest.fail('身份未漂移不得滚 epoch'),
        open_epoch=lambda _epoch: None,
        close_epoch=lambda epoch, turn: (
            closed.append((
                epoch.epoch_id, turn.exit_code, turn.outcome.state))
            or 'unknown'),
        epoch_identity_current=lambda _epoch: True,
        cancel_active=lambda: True,
        initial_epoch=_epoch('epoch-stable-read-failure', 1, 'initial'),
        read_operator_next=read_next,
        run_ready_action=lambda *_args: pytest.fail('不应执行 ready node'),
        run_blocked_action=lambda *_args: pytest.fail('不应执行 blocked node'),
        deterministic_blockers=frozenset({'recovery:x'}),
        finalize_run=lambda *_args: pytest.fail('不应 finalize'))

    with pytest.raises(RuntimeError, match='stable operator bug'):
        supervisor.run()

    assert closed == [('epoch-stable-read-failure', 70, 'unknown')]
    assert [event.kind for event in events] == ['epoch_closed']


# ── typed 恢复暂停：exit 2 + pause_note 不结束 parent ────────────────────

def test_recoverable_action_failure_pauses_and_resumes_the_same_epoch():
    """有 typed 恢复凭据的 exit 2 不结束 parent；继续后仍用原 epoch。"""
    events: list[AgentRunEvent] = []
    quiesced: list[bool] = []
    calls: list[str] = []
    state = 'ready'
    epoch = _epoch('epoch-stage2-delivery-retry', 1, 'initial')

    def current():
        if state == 'ready':
            return _current(state='ready', node='stage2.blueprint')
        return SimpleNamespace(state='pipeline_complete', code_version='code-1')

    def ready(current_epoch, _current, ticket):
        nonlocal state
        assert current_epoch is epoch
        ticket.commit()
        calls.append(current_epoch.epoch_id)
        if len(calls) == 1:
            return SupervisorActionResult(
                2, _outcome(), False,
                pause_note='已保留 13/15 章 checkpoint，只补第 7、9 章')
        state = 'complete'
        return SupervisorActionResult(0, _outcome(), True)

    def finalize(current_epoch, ticket):
        assert current_epoch is epoch
        ticket.commit()
        return SupervisorActionResult(0, _outcome('ready'), False)

    supervisor = RunSupervisor(
        run_id='run-stage2-delivery-retry', append_event=events.append,
        make_epoch=lambda *_args: pytest.fail('同 run 恢复不得滚 epoch'),
        open_epoch=lambda _epoch: None,
        close_epoch=lambda _epoch, _turn: 'drained',
        epoch_identity_current=lambda _epoch: True,
        cancel_active=lambda: True,
        set_quiesced=lambda value: quiesced.append(value) or True,
        initial_epoch=epoch,
        read_operator_next=lambda _epoch: current(),
        run_ready_action=ready,
        run_blocked_action=lambda *_args: pytest.fail('本例没有 blocked'),
        deterministic_blockers=frozenset({'recovery:x'}),
        finalize_run=finalize)
    result_box: list[object] = []
    worker = threading.Thread(target=lambda: result_box.append(supervisor.run()))
    worker.start()
    deadline = time.time() + 5
    while time.time() < deadline and not supervisor.paused:
        time.sleep(0.01)

    assert supervisor.paused
    assert calls == [epoch.epoch_id]
    assert [event.kind for event in events] == ['paused']
    assert events[0].note == '已保留 13/15 章 checkpoint，只补第 7、9 章'

    supervisor.request_resume()
    worker.join(5)

    assert not worker.is_alive()
    assert calls == [epoch.epoch_id, epoch.epoch_id]
    assert [event.kind for event in events] == [
        'paused', 'resumed', 'epoch_closed']
    assert quiesced == [True, False]
    assert result_box and result_box[0].stopped is False


# ── 控制与 launch ticket 线性化 ──────────────────────────────────────────

@pytest.mark.parametrize('branch', ['ready', 'blocked', 'finalize'])
@pytest.mark.parametrize('control', ['pause', 'stop'])
def test_control_between_operator_read_and_launch_never_calls_old_action(branch, control):
    """next 已取到、票尚未发放时的控制请求不能变成 70 或执行旧快照。"""
    read_entered = threading.Event()
    return_snapshot = threading.Event()
    paused = threading.Event()
    events: list[AgentRunEvent] = []
    calls: list[tuple[str, int]] = []
    results, errors, closed, quiesced, cancelled = [], [], [], [], []
    reads = 0
    epoch = _epoch(f'epoch-before-launch-{branch}-{control}', 1, 'initial')
    old = _current(
        state={'ready': 'ready', 'blocked': 'blocked',
               'finalize': 'pipeline_complete'}[branch],
        waiting_on='recovery:x')
    refreshed = SimpleNamespace(
        state='pipeline_complete', code_version='code-1')

    def read_next(current_epoch):
        nonlocal reads
        assert current_epoch is epoch
        reads += 1
        if reads == 1:
            read_entered.set()
            assert return_snapshot.wait(5)
            return old
        assert reads == 2, 'resume 只需重读一次当前 next'
        return refreshed

    def append(event):
        events.append(event)
        if event.kind == 'paused':
            paused.set()

    def ready(_epoch, current, ticket):
        calls.append(('ready', reads))
        ticket.commit()
        return SupervisorActionResult(0, _outcome(), False)

    def blocked(_epoch, current, ticket):
        calls.append(('blocked', reads))
        ticket.commit()
        return SupervisorActionResult(0, _outcome(), False)

    def finalize(current_epoch, ticket):
        assert current_epoch is epoch
        calls.append(('finalize', reads))
        ticket.commit()
        return SupervisorActionResult(0, _outcome('ready'), False)

    def close(current_epoch, turn):
        assert current_epoch is epoch
        closed.append(turn)
        return 'drained'

    supervisor = RunSupervisor(
        run_id=f'run-before-launch-{branch}-{control}', append_event=append,
        make_epoch=lambda *_args: pytest.fail('控制请求不能新增 epoch'),
        open_epoch=lambda _epoch: None, close_epoch=close,
        epoch_identity_current=lambda _epoch: True,
        cancel_active=lambda: cancelled.append(True) or True,
        set_quiesced=lambda value: quiesced.append(value) or True,
        initial_epoch=epoch, read_operator_next=read_next,
        run_ready_action=ready, run_blocked_action=blocked,
        deterministic_blockers=frozenset({'recovery:x'}),
        finalize_run=finalize)

    def run():
        try:
            results.append(supervisor.run())
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert read_entered.wait(5)
        (supervisor.request_pause if control == 'pause' else supervisor.request_stop)()
        assert calls == []
        return_snapshot.set()
        if control == 'pause':
            assert paused.wait(5), errors
            assert supervisor.paused and errors == []
            assert calls == [] and reads == 1 and closed == []
            assert [event.kind for event in events] == ['paused']
            supervisor.request_resume()
        worker.join(5)
        assert not worker.is_alive()
        assert errors == []
        assert len(results) == len(closed) == 1
        result = results[0]
        assert result.stopped is (control == 'stop')
        assert closed[0] is result.turn
        assert events[-1].kind == 'epoch_closed'
        assert events[-1].epoch_id == epoch.epoch_id
        assert events[-1].drain_state == 'drained'
        assert events[-1].exit_code != 70
        if control == 'pause':
            assert calls == [('finalize', 2)], 'resume 不得执行暂停前的旧 action'
            assert reads == 2 and result.turn.exit_code == 0
            assert [event.kind for event in events] == ['paused', 'resumed', 'epoch_closed']
            assert quiesced == [True, False] and cancelled == []
        else:
            assert calls == [] and reads == 1
            assert result.turn.exit_code == 2 and result.turn.outcome.state == 'stopped'
            assert [event.kind for event in events] == ['epoch_closed']
            assert quiesced == [True] and cancelled == [True]
    finally:
        return_snapshot.set()
        if worker.is_alive():
            supervisor.request_stop()
            worker.join(5)


@pytest.mark.parametrize('branch', ['ready', 'blocked', 'finalize'])
def test_dispatch_failure_is_not_reclassified_as_control(branch):
    """统一控制 catch 不能吞掉已启动 callback 的真实故障。"""
    events: list[AgentRunEvent] = []
    current = _current(
        state={'ready': 'ready', 'blocked': 'blocked',
               'finalize': 'pipeline_complete'}[branch],
        waiting_on='recovery:x')

    def broken(*args):
        args[-1].commit()
        raise RuntimeError('真实 callback 故障')

    supervisor = RunSupervisor(
        run_id=f'run-dispatch-failure-{branch}', append_event=events.append,
        make_epoch=lambda *_args: pytest.fail('不能以换 epoch 隐去异常'),
        open_epoch=lambda _epoch: None, close_epoch=lambda _epoch, _turn: 'drained',
        epoch_identity_current=lambda _epoch: True, cancel_active=lambda: True,
        initial_epoch=_epoch('epoch-dispatch-failure', 1, 'initial'),
        read_operator_next=lambda _epoch: current,
        run_ready_action=broken, run_blocked_action=broken,
        deterministic_blockers=frozenset({'recovery:x'}),
        finalize_run=broken)
    with pytest.raises(RuntimeError, match='真实 callback 故障'):
        supervisor.run()
    assert [event.kind for event in events] == ['epoch_closed']
    assert events[0].exit_code == 70


@pytest.mark.parametrize('control', ['pause', 'stop'])
def test_launch_ticket_never_accepts_control_before_process_is_registered(control):
    """pause/stop 返回即代表它已经看见本次 Popen，不能随后再冒出新进程。"""
    entered = threading.Event()
    allow_commit = threading.Event()
    allow_return = threading.Event()
    control_returned = threading.Event()
    process_registered = threading.Event()
    events: list[AgentRunEvent] = []

    current = _current(state='ready')

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
        epoch_identity_current=lambda _epoch: True,
        cancel_active=lambda: True,
        set_quiesced=lambda _value: True,
        initial_epoch=_epoch(f'epoch-ticket-{control}', 1, 'initial'),
        read_operator_next=lambda _epoch: current,
        run_ready_action=ready,
        run_blocked_action=lambda *_args: pytest.fail('本例没有 blocked'),
        deterministic_blockers=frozenset({'recovery:x'}),
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


# ── epoch 滚动与崩溃闭合 ────────────────────────────────────────────────

def test_supervisor_rolls_epoch_on_identity_change_after_action():
    """动作成功后身份漂移：关闭当前 epoch，用新身份继续同一 parent。"""
    events: list[AgentRunEvent] = []
    executions: list[str] = []
    flipped = {'value': False}
    reads: list[str] = []

    def read_next(epoch):
        reads.append(epoch.epoch_id)
        if epoch.epoch_id == 'run-2':
            return _current(state='ready')
        return SimpleNamespace(
            state='pipeline_complete', code_version=epoch.code_version)

    def ready(_epoch, _current, ticket):
        executions.append(_epoch.epoch_id)
        ticket.commit()
        flipped['value'] = True
        return SupervisorActionResult(0, _outcome(), True)

    def identity_current(epoch):
        return epoch.epoch_id != 'run-2' or not flipped['value']

    supervisor = RunSupervisor(
        run_id='run-2', append_event=events.append,
        make_epoch=lambda reason, ordinal: _epoch('epoch-2', ordinal, reason),
        open_epoch=lambda _epoch: None,
        close_epoch=lambda _epoch, _turn: 'drained',
        epoch_identity_current=identity_current,
        cancel_active=lambda: True,
        initial_epoch=_epoch('run-2', 1, 'initial'),
        read_operator_next=read_next,
        run_ready_action=ready,
        run_blocked_action=lambda *_args: pytest.fail('本例没有 blocked'),
        deterministic_blockers=frozenset({'recovery:x'}),
        finalize_run=lambda _epoch, ticket: (
            ticket.commit() or SupervisorActionResult(
                0, _outcome('ready'), False)))

    supervisor.run()

    assert executions == ['run-2']
    assert reads == ['run-2', 'epoch-2']
    assert [event.kind for event in events] == [
        'epoch_closed', 'epoch_opened', 'epoch_closed']
    assert events[1].epoch is not None
    assert events[1].epoch.reason == 'code_change'


def test_supervisor_closes_first_epoch_when_first_action_raises():
    """已写 epoch_opened 后即使首 action 未形成结果，也必须以 70/unknown 闭合。"""
    events: list[AgentRunEvent] = []
    closed: list[tuple[str, int]] = []
    epoch = _epoch('epoch-first-failure', 1, 'initial')

    def ready(_epoch, _current, ticket):
        ticket.commit()
        raise RuntimeError('spawn failed')

    supervisor = RunSupervisor(
        run_id='run-first-failure', append_event=events.append,
        make_epoch=lambda *_args: pytest.fail('不应滚新 epoch'),
        open_epoch=lambda _epoch: None,
        close_epoch=lambda current, turn: (
            closed.append((current.epoch_id, turn.exit_code)) or 'unknown'),
        epoch_identity_current=lambda _epoch: True,
        cancel_active=lambda: True,
        initial_epoch=epoch,
        read_operator_next=lambda _epoch: _current(state='ready'),
        run_ready_action=ready,
        run_blocked_action=lambda *_args: pytest.fail('本例没有 blocked'),
        deterministic_blockers=frozenset({'recovery:x'}),
        finalize_run=lambda *_args: pytest.fail('不应 finalize'))

    with pytest.raises(RuntimeError, match='spawn failed'):
        supervisor.run()

    assert closed == [('epoch-first-failure', 70)]
    assert [event.kind for event in events] == ['epoch_closed']
    assert events[0].epoch_id == 'epoch-first-failure'
    assert events[0].exit_code == 70


def test_new_epoch_first_action_failure_never_reuses_previous_epoch_result():
    """跨 epoch 的首 action 失败不能拿上一 epoch 的成功退出码关闭新身份。"""
    events: list[AgentRunEvent] = []
    closed: list[tuple[str, int]] = []
    flipped = {'value': False}

    def ready(epoch, _current, ticket):
        ticket.commit()
        if not flipped['value']:
            flipped['value'] = True
            return SupervisorActionResult(0, _outcome(), True)
        assert epoch.epoch_id == 'epoch-new'
        raise RuntimeError('new epoch failed')

    supervisor = RunSupervisor(
        run_id='run-cross-epoch-failure', append_event=events.append,
        make_epoch=lambda reason, ordinal: _epoch(
            'epoch-new', ordinal, reason).model_copy(
            update={'code_version': 'code-1'}),
        open_epoch=lambda _epoch: None,
        close_epoch=lambda current, turn: (
            closed.append((current.epoch_id, turn.exit_code)) or 'unknown'),
        epoch_identity_current=lambda epoch: (
            epoch.epoch_id != 'epoch-old' or not flipped['value']),
        cancel_active=lambda: True,
        initial_epoch=_epoch('epoch-old', 1, 'initial'),
        read_operator_next=lambda _epoch: _current(state='ready'),
        run_ready_action=ready,
        run_blocked_action=lambda *_args: pytest.fail('本例没有 blocked'),
        deterministic_blockers=frozenset({'recovery:x'}),
        finalize_run=lambda *_args: pytest.fail('不应 finalize'))

    with pytest.raises(RuntimeError, match='new epoch failed'):
        supervisor.run()

    assert closed == [('epoch-old', 0), ('epoch-new', 70)]
    assert [event.kind for event in events] == [
        'epoch_closed', 'epoch_opened', 'epoch_closed']
    assert events[-1].epoch_id == 'epoch-new'
    assert events[-1].exit_code == 70


# ── pause / resume 语义 ─────────────────────────────────────────────────

def test_pause_keeps_the_same_epoch_and_rereads_after_resume():
    """pause 只冻结下一动作；代码未变时不得伪造一次 epoch rollover。"""
    events: list[AgentRunEvent] = []
    calls: list[str] = []
    initial = _epoch('run-pause', 1, 'initial')
    reads = {'count': 0}

    def read_next(epoch):
        reads['count'] += 1
        if reads['count'] == 1:
            return _current(state='ready')
        return SimpleNamespace(state='pipeline_complete', code_version='code-1')

    def ready(epoch, _current, ticket):
        calls.append(epoch.epoch_id)
        ticket.commit()
        return SupervisorActionResult(0, _outcome(), True)

    supervisor = RunSupervisor(
        run_id='run-pause', append_event=events.append,
        make_epoch=lambda *_args: pytest.fail('同身份 pause/resume 不得滚 epoch'),
        open_epoch=lambda _epoch: None,
        close_epoch=lambda _epoch, _turn: 'drained',
        epoch_identity_current=lambda _epoch: True,
        cancel_active=lambda: True,
        initial_epoch=initial,
        read_operator_next=read_next,
        run_ready_action=ready,
        run_blocked_action=lambda *_args: pytest.fail('本例没有 blocked'),
        deterministic_blockers=frozenset({'recovery:x'}),
        finalize_run=lambda _epoch, ticket: (
            ticket.commit() or SupervisorActionResult(
                0, _outcome('ready'), False)))
    supervisor.request_pause()
    worker = threading.Thread(target=supervisor.run)
    worker.start()

    for _ in range(100):
        if supervisor.paused:
            break
        time.sleep(0.01)
    assert supervisor.paused
    assert calls == [] and reads['count'] == 0, 'pause 必须先于首个动作受理'
    assert [event.kind for event in events] == ['paused']

    supervisor.request_resume()
    worker.join(5)

    assert not worker.is_alive()
    assert calls == ['run-pause']
    assert [event.kind for event in events] == [
        'paused', 'resumed', 'epoch_closed']
    assert [event.kind for event in events].count('epoch_opened') == 0


def test_concurrent_resume_is_accepted_exactly_once():
    """worker 离开 barrier 前的第二个 resume 也必须关闭式冲突。"""
    events: list[AgentRunEvent] = []
    current = _current(state='ready')

    def ready(_epoch, _current, ticket):
        ticket.commit()
        return SupervisorActionResult(0, _outcome(), False)

    supervisor = RunSupervisor(
        run_id='run-one-resume', append_event=events.append,
        make_epoch=lambda *_args: pytest.fail('不应滚 epoch'),
        open_epoch=lambda _epoch: None,
        close_epoch=lambda _epoch, _turn: 'drained',
        epoch_identity_current=lambda _epoch: True,
        cancel_active=lambda: True,
        initial_epoch=_epoch('epoch-one-resume', 1, 'initial'),
        read_operator_next=lambda _epoch: current,
        run_ready_action=ready,
        run_blocked_action=lambda *_args: pytest.fail('本例没有 blocked'),
        deterministic_blockers=frozenset({'recovery:x'}),
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
    """已受理 pause 后动作返回非零退出：先落安全 paused，再按原事实结束。"""
    events: list[AgentRunEvent] = []
    calls: list[str] = []
    closed: list[ExecutionTurnResult] = []
    initial = _epoch('run-pause-quiesce', 1, 'initial')
    box: dict[str, RunSupervisor] = {}

    def read_next(_epoch):
        if len(calls) == 0:
            return _current(state='ready')
        return SimpleNamespace(state='pipeline_complete', code_version='code-1')

    def ready(_epoch, _current, ticket):
        calls.append('action')
        ticket.commit()
        # 动作执行中用户请求 pause：quiesce 已封住新 claim，本动作按显式
        # 终态 exit 2 返回；这枚 False 不能抢在已受理 pause 前结束 parent。
        box['supervisor'].request_pause()
        return SupervisorActionResult(2, _outcome('stopped'), False)

    def close(epoch, turn):
        assert epoch is initial
        closed.append(turn)
        return 'drained'

    supervisor = RunSupervisor(
        run_id='run-pause-quiesce', append_event=events.append,
        make_epoch=lambda *_args: pytest.fail('同身份恢复不得滚 epoch'),
        open_epoch=lambda _epoch: None,
        close_epoch=close,
        epoch_identity_current=lambda _epoch: True,
        cancel_active=lambda: True,
        initial_epoch=initial,
        read_operator_next=read_next,
        run_ready_action=ready,
        run_blocked_action=lambda *_args: pytest.fail('本例没有 blocked'),
        deterministic_blockers=frozenset({'recovery:x'}),
        finalize_run=lambda *_args: pytest.fail('不应 finalize'))
    box['supervisor'] = supervisor
    results: list[object] = []
    worker = threading.Thread(target=lambda: results.append(supervisor.run()))
    worker.start()

    for _ in range(100):
        if supervisor.paused:
            break
        time.sleep(0.01)
    assert supervisor.paused
    assert calls == ['action'] and closed == []
    assert [event.kind for event in events] == ['paused']

    supervisor.request_resume()
    worker.join(5)

    assert not worker.is_alive()
    assert [event.kind for event in events] == [
        'paused', 'resumed', 'epoch_closed']
    assert closed and closed[0].exit_code == 2
    assert results and results[0].stopped is False
    assert results[0].turn.exit_code == 2


def test_code_drift_while_paused_opens_a_new_epoch_without_old_context():
    events: list[AgentRunEvent] = []
    reads: list[str] = []
    drifted = False

    def read_next(epoch):
        reads.append(epoch.epoch_id)
        if epoch.epoch_id == 'run-pause-drift':
            return _current(state='ready')
        return SimpleNamespace(state='pipeline_complete', code_version='code-2')

    def ready(epoch, _current, ticket):
        ticket.commit()
        return SupervisorActionResult(0, _outcome(), True)

    def identity_current(epoch):
        return epoch.epoch_id != 'run-pause-drift' or not drifted

    supervisor = RunSupervisor(
        run_id='run-pause-drift', append_event=events.append,
        make_epoch=lambda reason, ordinal: _epoch(
            'epoch-after-drift', ordinal, reason),
        open_epoch=lambda _epoch: None,
        close_epoch=lambda _epoch, _turn: 'drained',
        epoch_identity_current=identity_current,
        cancel_active=lambda: True,
        initial_epoch=_epoch('run-pause-drift', 1, 'initial'),
        read_operator_next=read_next,
        run_ready_action=ready,
        run_blocked_action=lambda *_args: pytest.fail('本例没有 blocked'),
        deterministic_blockers=frozenset({'recovery:x'}),
        finalize_run=lambda _epoch, ticket: (
            ticket.commit() or SupervisorActionResult(
                0, _outcome('ready'), False)))
    supervisor.request_pause()
    worker = threading.Thread(target=supervisor.run)
    worker.start()

    for _ in range(100):
        if supervisor.paused:
            break
        time.sleep(0.01)
    assert supervisor.paused
    drifted = True
    supervisor.request_resume()
    worker.join(5)

    assert not worker.is_alive()
    assert [event.kind for event in events] == [
        'paused', 'resumed', 'epoch_closed', 'epoch_opened', 'epoch_closed']
    assert events[3].epoch is not None
    assert events[3].epoch.reason == 'code_change'
    assert events[3].epoch.resume_provider_session_id is None


# ── 退出域保真 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize('route', ['ready', 'blocked', 'finalize'])
def test_canonical_action_exit_origin_survives_supervisor_conversion(route):
    """三个确定性消费入口都保留 action 的退出域，关闭 epoch 不覆盖来源。"""
    events = []
    closed = []
    current = _current(
        state={'ready': 'ready', 'blocked': 'blocked',
               'finalize': 'pipeline_complete'}[route],
        waiting_on='test-recovery')

    def execute(*args):
        args[-1].commit()
        return SupervisorActionResult(
            exit_code=1, outcome=_outcome('unknown'), continue_ready=False,
            exit_origin='novel_cli')

    def close(_epoch, turn):
        closed.append(turn)
        return 'drained'

    supervisor = RunSupervisor(
        run_id='origin-run', append_event=events.append,
        make_epoch=lambda *_args: pytest.fail('无需滚 epoch'),
        open_epoch=lambda _epoch: None, close_epoch=close,
        epoch_identity_current=lambda _epoch: True,
        cancel_active=lambda: True,
        initial_epoch=_epoch('origin-epoch', 1, 'initial'),
        read_operator_next=lambda _epoch: current,
        run_ready_action=execute, run_blocked_action=execute,
        deterministic_blockers=frozenset({'test-recovery'}),
        finalize_run=execute)
    result = supervisor.run()
    assert result.turn.exit_code == 1 and result.turn.exit_origin == 'novel_cli'
    assert result.turn.outcome.state == 'unknown'
    assert len(closed) == 1 and closed[0] == result.turn
