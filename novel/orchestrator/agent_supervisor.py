#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""整书 AgentRun 的纯生命周期监督器。

Supervisor 不理解小说节点，也不直接调用任何模型 SDK。受托 Commander
reviewer 已退役（2026-09-15 用户裁决：审批一律人工）：本类只执行确定性
动作（ready executor token、登记的 deterministic recovery、交付终验），
在需要人工裁决的审批闸停止 parent run，等待用户在 Stage 页面过闸后
原配置续跑。代码或执行契约漂移时滚动 execution epoch；pause / resume /
stop 落在安全边界。

pause 不是“杀进程后猜它停了”：请求只关闭当前 admission，阻止再 claim
新节点；已经取得 claim 的节点自然完成、release 并 drain，最后才写
``paused``。因此书级 writer/OS owner lease 可以由外层一直持有，已花钱的
节点不会因暂停白跑，迟到子进程也不能越过已关闭 gate。只有 stop 才取消
当前进程树。
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from contracts.invariants import NODES, PIPELINE_STEPS, approval_work_unit
from contracts.schema.api import OperatorNext, OperatorNode
from contracts.schema.orchestration import (
    AgentDeliveryOutcome, AgentRunEvent, AgentRunExitOrigin, ExecutionEpoch,
)
from novel.orchestrator.jobs import JobConflict


class PrelaunchIdentityDrift(RuntimeError):
    """callback 在 launch ticket commit 前证明 execution identity 已漂移。"""


def range_boundary_reason(current: OperatorNext, boundary: OperatorNode) -> str | None:
    """按登记工作流与实际动作 owner 守排他上界，返修不按发现问题的下游位置猜。

    联合首抽会物化两侧，不能借其较早的编排名跨过上游上界；未知闭拒。
    """
    rank = {node: index for index, node in enumerate(PIPELINE_STEPS)}
    if boundary not in rank or set(rank) != set(NODES):
        return '运行范围登记未知或不完整；停止并排空，不能猜测节点先后'
    if current.state == 'pipeline_complete':
        return f'本次范围边界为 {boundary}；已无待执行节点，停止范围运行，不启动全链交付终验'
    node = getattr(current.action, 'node', None)
    if node not in rank:
        return f'本次范围边界为 {boundary}；当前动作 owner 未登记，停止并排空'
    # blocked.node 是被挡住的下游，尚不是本轮动作的裁决属主。
    # 复用唯一的 blocker→owner 登记，让上游在更晚层的上界前完成裁决；
    # 不能因为“下游正等待上游”就把上游的处置也截掉。
    if current.state == 'blocked':
        waiting_on = getattr(current.action, 'waiting_on', None)
        review_owner = approval_work_unit(waiting_on) if isinstance(waiting_on, str) else None
        if review_owner is not None:
            node = review_owner[0]
    # ⚠ F8c：旧链 `stage2.analyze` 的 repair_owners 展开与 `stage0.edit` 的
    # 产物 owner 改派随两节点退役；现役每个动作的 owner 就是它自己
    # （`NODES[node].lifecycle.repair_owners` 亦为 (node,)）。
    owners = (NODES[node].lifecycle.repair_owners if node in NODES else (node,))
    if not owners or any(owner not in rank for owner in owners):
        return f'本次范围边界为 {boundary}；当前动作物化归属未知，停止并排空'
    if max(rank[owner] for owner in owners) >= rank[boundary] or node == boundary:
        return f'已到本次运行范围边界：{boundary}；当前动作 {node} 未启动，停止并排空 owner'
    return None


class _LaunchDeferredByControl(JobConflict):
    """pause/stop 先于 launch ticket 取得线性化点。

    它是 ``RunSupervisor.run`` 内部捕获的控制信号，既不属于
    Supervisor 崩溃，也不越过 HTTP 边界；公开控制路由只回 202。
    """


@dataclass(frozen=True)
class ExecutionTurnResult:
    """调用方完成一轮确定性动作、审计与 next 检查后的事实。

    历史名 ``CommanderTurnResult``；Commander reviewer 退役后一轮只可能是
    ready 执行、deterministic recovery、交付终验或停闸事实。
    """

    exit_code: int
    continue_ready: bool
    outcome: AgentDeliveryOutcome
    # 数字退出码只在其 producer 域内有意义；外部进程的 1 不是
    # novel CLI 的“正式结果已落盘”。合成终态默认由 Supervisor 负责。
    exit_origin: AgentRunExitOrigin = 'supervisor'


@dataclass(frozen=True)
class SupervisorResult:
    """Supervisor 退出循环时的最后事实；外层据此写 session/parent ended。"""

    turn: ExecutionTurnResult | None
    stopped: bool


@dataclass(frozen=True)
class SupervisorActionResult:
    """Supervisor 直接执行一个确定性动作后的事实。"""

    exit_code: int
    outcome: AgentDeliveryOutcome
    continue_ready: bool = True
    failure_detail: str | None = None
    # 只有 action 已经自行落下可验证的同 run 恢复凭据时才出现。Supervisor
    # 据此保持当前 epoch/书锁并开放 resume；普通 exit 2 仍永久结束本次 parent。
    pause_note: str | None = None
    exit_origin: AgentRunExitOrigin = 'supervisor'

    def __post_init__(self) -> None:
        if self.pause_note is not None:
            if not self.pause_note.strip():
                raise ValueError('pause_note 若出现必须是非空文本')
            if self.continue_ready:
                raise ValueError('pause_note 与 continue_ready=true 互相矛盾')


class LaunchTicket:
    """把“准备启动”与 pause/stop 的受理顺序线性化。

    回调必须在子进程已经创建且已登记为可取消 owner 后立即调用
    :meth:`commit`。pause/stop 若撞在 Popen 之前，会等到 commit（或启动失败）
    才完成受理；因此请求一旦返回，后面不可能再冒出一棵它没看见的新进程树。
    """

    def __init__(self, supervisor: RunSupervisor) -> None:
        self._supervisor = supervisor
        self._finished = False

    def commit(self) -> None:
        supervisor = self._supervisor
        with supervisor._condition:
            if self._finished:
                raise RuntimeError('launch ticket 已结束，不能再 commit')
            if not supervisor._launching:
                raise RuntimeError('launch ticket 不属于当前启动窗口')
            supervisor._launch_committed = True
            supervisor._condition.notify_all()

    def _finish(self) -> None:
        supervisor = self._supervisor
        with supervisor._condition:
            self._finished = True
            supervisor._launching = False
            supervisor._launch_committed = False
            supervisor._condition.notify_all()


AppendEvent = Callable[[AgentRunEvent], None]
MakeEpoch = Callable[[str, int], ExecutionEpoch]
OpenEpoch = Callable[[ExecutionEpoch], None]
CloseEpoch = Callable[[ExecutionEpoch, ExecutionTurnResult], str]
EpochIdentityCurrent = Callable[[ExecutionEpoch], bool]
CancelActive = Callable[[], bool]
SetQuiesced = Callable[[bool], bool]
ReadOperatorNext = Callable[[ExecutionEpoch], OperatorNext]
ReadyRequiresFreshEpoch = Callable[[ExecutionEpoch, OperatorNext], bool]
RunReadyAction = Callable[
    [ExecutionEpoch, OperatorNext, LaunchTicket],
    SupervisorActionResult,
]
RunBlockedAction = Callable[
    [ExecutionEpoch, OperatorNext, LaunchTicket], SupervisorActionResult,
]
FinalizeRun = Callable[
    [ExecutionEpoch, LaunchTicket], SupervisorActionResult,
]


def _human_gate_turn(waiting_on: str) -> ExecutionTurnResult:
    """现役全自动后不应再停在人工审批闸；精确区分故障面。

    - ``approval:*``：编排 bug（闸已退役却仍发出）→ failed
    - ``human:*`` / ``inbox:*`` / ``resume:*``：精确故障话术 → stopped
    - 其余：保留可续跑指引 → stopped
    pause/stop 不经本函数。

    ⚠ F8c：Stage5 机械闭合闸（`WAITING_ON_STAGE5_CLOSURE`）与 stage5.review
    收敛账随旧链退役，其专属话术分支一并删除；更早层的等待面统一走
    ``human:``/``inbox:``/``resume:`` 前缀与末尾兜底。
    """
    if waiting_on.startswith('approval:'):
        return ExecutionTurnResult(
            exit_code=1,
            continue_ready=False,
            outcome=AgentDeliveryOutcome(
                state='failed', checked_at=int(time.time()),
                reason=(
                    f'编排错误：现役队列不应再发出已退役审批闸 `{waiting_on}`；'
                    '请修复调度后再跑')))
    if waiting_on.startswith('human:'):
        reason = (
            f'停在人工恢复面：{waiting_on}；按该 blocker 对应的 typed 恢复'
            '入口收口后用原配置续跑（不是 Stage 页审批闸）')
    elif waiting_on.startswith('inbox:'):
        reason = (
            f'停在待人工清单：{waiting_on}；处理 inbox 中的 INVALID/阻断项'
            '后用原配置续跑')
    elif waiting_on.startswith('resume:'):
        reason = (
            f'停在续跑裁决：{waiting_on}；队列不会把 REDO/UNKNOWN 猜成命令，'
            '请人工决定下一步后用原配置续跑')
    else:
        reason = (
            f'停在阻断面：{waiting_on}；按该 waiting_on 对应恢复入口收口后'
            '用原配置续跑')
    return ExecutionTurnResult(
        exit_code=2,
        continue_ready=False,
        outcome=AgentDeliveryOutcome(
            state='stopped', checked_at=int(time.time()),
            reason=reason))


class RunSupervisor:
    """一个 parent run 的单写手状态机；可由 HTTP 线程并发请求控制。"""

    def __init__(
        self, *, run_id: str, append_event: AppendEvent,
        make_epoch: MakeEpoch, open_epoch: OpenEpoch,
        close_epoch: CloseEpoch,
        epoch_identity_current: EpochIdentityCurrent,
        cancel_active: CancelActive,
        set_quiesced: SetQuiesced | None = None,
        initial_epoch: ExecutionEpoch | None = None,
        read_operator_next: ReadOperatorNext | None = None,
        ready_requires_fresh_epoch: ReadyRequiresFreshEpoch | None = None,
        run_ready_action: RunReadyAction | None = None,
        run_blocked_action: RunBlockedAction | None = None,
        deterministic_blockers: frozenset[str] = frozenset(),
        fresh_epoch_blockers: frozenset[str] = frozenset(),
        finalize_run: FinalizeRun | None = None,
        pause_before_node: OperatorNode | None = None,
    ) -> None:
        self.run_id = run_id
        self._append = append_event
        self._make_epoch = make_epoch
        self._open_epoch = open_epoch
        self._close_epoch = close_epoch
        self._identity_current = epoch_identity_current
        self._cancel = cancel_active
        self._set_quiesced = set_quiesced or (lambda _value: True)
        self._initial_epoch = initial_epoch
        deterministic = (read_operator_next, run_ready_action, finalize_run)
        if any(callback is not None for callback in deterministic) and not all(
                callback is not None for callback in deterministic):
            raise ValueError('Supervisor 确定性主路的三个 callback 必须同时提供')
        if pause_before_node is not None and (
                pause_before_node not in NODES or not all(callback is not None for callback in deterministic)):
            raise ValueError('运行范围上界必须是登记节点，且只能用于受管确定性主路')
        self._pause_before_node = pause_before_node
        if ready_requires_fresh_epoch is not None and not all(callback is not None for callback in deterministic):
            raise ValueError('ready producer 边界只随完整确定性主路提供')
        if (run_blocked_action is not None or deterministic_blockers) and not all(
                callback is not None for callback in deterministic):
            raise ValueError(
                '确定性 blocked 路由只能随完整 Supervisor 主路提供')
        if (run_blocked_action is None) != (not deterministic_blockers):
            raise ValueError(
                'run_blocked_action 与 deterministic_blockers 必须成对提供')
        if not fresh_epoch_blockers.issubset(deterministic_blockers):
            raise ValueError(
                'fresh_epoch_blockers 必须是 deterministic_blockers 的子集')
        self._read_next = read_operator_next
        self._ready_requires_fresh_epoch = ready_requires_fresh_epoch or (lambda _epoch, _next: False)
        self._run_ready = run_ready_action
        self._run_blocked = run_blocked_action
        self._deterministic_blockers = deterministic_blockers
        self._fresh_epoch_blockers = fresh_epoch_blockers
        self._finalize = finalize_run
        self._condition = threading.Condition()
        self._pause_requested = False
        self._resume_requested = False
        self._stop_requested = False
        self._paused = False
        self._running = False
        self._launching = False
        self._launch_committed = False

    @property
    def paused(self) -> bool:
        with self._condition:
            return self._paused

    def request_pause(self) -> None:
        """请求在当前 claim 自然闭合、drain 后进入 paused。"""
        with self._condition:
            while self._launching and not self._launch_committed:
                self._condition.wait()
            if self._stop_requested:
                raise JobConflict(f'run_id={self.run_id!r} 已请求 stop，不能再 pause')
            if self._paused or self._pause_requested:
                return
            self._pause_requested = True
        # 只封住新 claim，不取消正在运行的 Executor；调用者在动作
        # 自然返回并完成 drain 之前不能把状态提前写成 paused。
        self._set_quiesced(True)

    def request_resume(self) -> None:
        """只唤醒仍由本进程持锁的 paused supervisor。"""
        with self._condition:
            if not self._paused:
                raise JobConflict(
                    f'run_id={self.run_id!r} 当前不是可恢复的 paused 状态')
            if self._resume_requested:
                raise JobConflict(
                    f'run_id={self.run_id!r} 已受理 resume，不能并发重复恢复')
            # ``_paused`` 要等 worker 真正越过 barrier 后才清除；单独占住
            # resume ticket，避免两个 HTTP 请求都在这段交接窗内看到 paused。
            self._resume_requested = True
            self._pause_requested = False
            self._set_quiesced(False)
            self._condition.notify_all()

    def request_stop(self) -> None:
        """请求终止；ended 仍由 worker 在 drain 与审计闭合后统一落账。"""
        with self._condition:
            while self._launching and not self._launch_committed:
                self._condition.wait()
            if self._stop_requested:
                return
            self._stop_requested = True
            self._pause_requested = False
            self._condition.notify_all()
        # stop 与 pause 不同：既封住新 claim，也取消当前进程树。
        self._set_quiesced(True)
        self._cancel()

    def _request_recovery_pause(self) -> None:
        """action 在安全边界请求内部暂停；与用户 pause 共用唯一 barrier。"""
        with self._condition:
            if self._stop_requested:
                return
            self._pause_requested = True
        self._set_quiesced(True)

    def _pause_barrier(self, *, note: str) -> bool:
        """返回 True 表示 stop 已取代 resume，调用方应结束循环。"""
        with self._condition:
            if not self._pause_requested or self._stop_requested:
                return self._stop_requested
            self._paused = True
            self._resume_requested = False
        self._append(AgentRunEvent(
            wire_version=2, run_id=self.run_id, kind='paused',
            ts=int(time.time()), note=note))
        with self._condition:
            while self._pause_requested and not self._stop_requested:
                self._condition.wait()
            stopped = self._stop_requested
            self._paused = False
            self._resume_requested = False
        if not stopped:
            self._append(AgentRunEvent(
                wire_version=2, run_id=self.run_id, kind='resumed',
                ts=int(time.time()),
                note='同一 parent run 在 durable paused 边界恢复'))
        return stopped

    def _launch(self, callback: Callable[[LaunchTicket], object]) -> object:
        """只在无控制请求的窗口发放一枚单次 launch ticket。"""
        with self._condition:
            if self._stop_requested:
                raise _LaunchDeferredByControl(
                    f'run_id={self.run_id!r} 已请求 stop')
            if self._pause_requested:
                raise _LaunchDeferredByControl(
                    f'run_id={self.run_id!r} 已请求 pause')
            if self._launching:
                raise RuntimeError('Supervisor 同时存在两枚 launch ticket')
            self._launching = True
            self._launch_committed = False
        ticket = LaunchTicket(self)
        try:
            return callback(ticket)
        finally:
            ticket._finish()

    def run(self) -> SupervisorResult:
        """运行到流水线终态、人工闸或 stop；没有 turn/epoch 上限。"""
        assert self._read_next is not None, (
            'Commander 兼容主路已随角色退役；Supervisor 只在确定性主路下构造')
        return self._run_deterministic()

    def _run_deterministic(self) -> SupervisorResult:
        """确定性主路：读 next、执行 token；人工审批闸停止 parent。"""
        assert self._read_next is not None
        assert self._run_ready is not None
        assert self._finalize is not None
        with self._condition:
            if self._running:
                raise JobConflict(f'run_id={self.run_id!r} Supervisor 已在运行')
            self._running = True
        ordinal = 0
        reason = 'initial'
        last: ExecutionTurnResult | None = None
        active_epoch: ExecutionEpoch | None = None
        active_epoch_last: ExecutionTurnResult | None = None
        active_epoch_closed = True
        # 同一 typed action 在一个 parent 内只建一次 producer 边界。
        # 要先记后关：若 close/open 中途崩溃，本 parent 不会在内存中
        # 热循环；新 outer parent 有自己的空集合，会再建一次新边界。
        freshened_actions: set[tuple[str, str]] = set()
        if self._initial_epoch is not None:
            # initial epoch 已由 parent 在 worker 启动前 durable append；stop 若
            # 恰在首循环前受理，也必须由 finally 关闭，不能留下孤儿 opened。
            active_epoch = self._initial_epoch
            active_epoch_closed = False

        def action_turn(result: SupervisorActionResult) -> ExecutionTurnResult:
            return ExecutionTurnResult(
                exit_code=result.exit_code,
                exit_origin=result.exit_origin,
                continue_ready=result.continue_ready,
                outcome=result.outcome)

        def close_epoch(
                epoch: ExecutionEpoch,
                turn: ExecutionTurnResult | None) -> None:
            nonlocal active_epoch_closed
            if active_epoch is not epoch:
                raise RuntimeError('试图关闭非当前 execution epoch')
            if active_epoch_closed:
                return
            closing = turn or ExecutionTurnResult(
                exit_code=70,
                continue_ready=False,
                outcome=AgentDeliveryOutcome(
                    state='unknown', checked_at=int(time.time()),
                    reason='execution epoch 在形成可审计动作结果前异常'),
            )
            close_error: BaseException | None = None
            try:
                drain_state = self._close_epoch(epoch, closing)
            except BaseException as exc:  # noqa: BLE001 —— 仍须落 epoch_closed
                drain_state = 'unknown'
                close_error = exc
            self._append(AgentRunEvent(
                wire_version=2, run_id=self.run_id,
                kind='epoch_closed', ts=int(time.time()),
                epoch_id=epoch.epoch_id,
                exit_code=closing.exit_code,
                drain_state=drain_state,
                provider_session_id=None))
            active_epoch_closed = True
            if close_error is not None:
                raise close_error

        try:
            while True:
                if self._initial_epoch is None:
                    if self._pause_barrier(
                            note='用户请求 pause；当前 execution 已关闭并 drain'):
                        return SupervisorResult(turn=last, stopped=True)
                with self._condition:
                    if self._stop_requested:
                        return SupervisorResult(turn=last, stopped=True)

                ordinal += 1
                if self._initial_epoch is not None:
                    epoch = self._initial_epoch
                    self._initial_epoch = None
                    ordinal = epoch.ordinal
                    active_epoch = epoch
                    active_epoch_last = None
                    active_epoch_closed = False
                else:
                    epoch = self._make_epoch(reason, ordinal)
                    self._append(AgentRunEvent(
                        wire_version=2, run_id=self.run_id,
                        kind='epoch_opened', ts=int(time.time()), epoch=epoch))
                    active_epoch = epoch
                    active_epoch_last = None
                    active_epoch_closed = False
                    self._open_epoch(epoch)
                epoch_last: ExecutionTurnResult | None = None

                while True:
                    resumed_from_action_pause = False
                    if not self._identity_current(epoch):
                        identity_turn = epoch_last or ExecutionTurnResult(
                            exit_code=0,
                            continue_ready=True,
                            outcome=AgentDeliveryOutcome(
                                state='unknown', checked_at=int(time.time()),
                                reason='动作启动前 execution epoch 身份已变化'),
                        )
                        close_epoch(epoch, identity_turn)
                        reason = 'code_change'
                        break

                    if self._pause_barrier(
                            note='用户请求 pause；当前 execution 已关闭并 drain'):
                        close_epoch(epoch, epoch_last)
                        return SupervisorResult(turn=last, stopped=True)
                    with self._condition:
                        if self._stop_requested:
                            close_epoch(epoch, epoch_last)
                            return SupervisorResult(turn=last, stopped=True)

                    try:
                        current = self._read_next(epoch)
                    except Exception:  # noqa: BLE001 —— 只在已证实身份漂移时换 epoch
                        # code/skill 可能在上面的前置 CAS 与 operator 的末端
                        # CAS 之间变化。此时 operator 内部关闭式拒绝并不是
                        # parent run 崩溃；封存旧 epoch 后用新身份重读即可。
                        # 身份仍 current 时原异常必须原样上抛，不能洗白真 bug。
                        if self._identity_current(epoch):
                            raise
                        identity_turn = epoch_last or ExecutionTurnResult(
                            exit_code=0,
                            continue_ready=True,
                            outcome=AgentDeliveryOutcome(
                                state='unknown',
                                checked_at=int(time.time()),
                                reason=(
                                    'operator next 读取期间 execution epoch '
                                    '身份已变化')),
                        )
                        close_epoch(epoch, identity_turn)
                        reason = 'code_change'
                        break
                    if (current.code_version != epoch.code_version
                            or not self._identity_current(epoch)):
                        identity_turn = epoch_last or ExecutionTurnResult(
                            exit_code=0,
                            continue_ready=True,
                            outcome=AgentDeliveryOutcome(
                                state='unknown', checked_at=int(time.time()),
                                reason='operator next 与 execution epoch 身份不一致'),
                        )
                        close_epoch(epoch, identity_turn)
                        reason = 'code_change'
                        break

                    boundary_reason = (range_boundary_reason(current, self._pause_before_node)
                                       if self._pause_before_node is not None else None)
                    if boundary_reason is not None:
                        boundary_turn = ExecutionTurnResult(
                            exit_code=2,
                            continue_ready=False, outcome=AgentDeliveryOutcome(
                                state='stopped', checked_at=int(time.time()), reason=boundary_reason))
                        last = epoch_last = active_epoch_last = boundary_turn
                        close_epoch(epoch, boundary_turn)
                        # stopped 标志只用于外部 stop 控制。范围终止保留本条准确原因，
                        # 不让 worker 改写成“用户刚请求停止”，也不冒充 pipeline_complete。
                        return SupervisorResult(turn=boundary_turn, stopped=False)

                    try:
                        if current.state == 'pipeline_complete':
                            finalized = self._launch(
                                lambda ticket: self._finalize(epoch, ticket))
                            assert isinstance(finalized, SupervisorActionResult)
                            turn = action_turn(finalized)
                            last = epoch_last = active_epoch_last = turn
                            close_epoch(epoch, turn)
                            return SupervisorResult(turn=turn, stopped=False)

                        if current.state == 'ready':
                            if current.execution_token is None:
                                raise RuntimeError(
                                    'ready operator next 没有同快照 execution token')
                            if self._ready_requires_fresh_epoch(epoch, current):
                                boundary_turn = epoch_last or ExecutionTurnResult(
                                    exit_code=0,
                                    continue_ready=True, outcome=AgentDeliveryOutcome(
                                        state='unknown', checked_at=int(time.time()),
                                        reason='当前 ready 作业需要独立 producer 身份'))
                                close_epoch(epoch, boundary_turn)
                                reason = 'continue'
                                break
                            executed = self._launch(
                                lambda ticket: self._run_ready(epoch, current, ticket))
                            assert isinstance(executed, SupervisorActionResult)
                            turn = action_turn(executed)
                            last = epoch_last = active_epoch_last = turn
                            action_pause_note = executed.pause_note
                        else:
                            action_pause_note = None
                            assert current.action is not None
                            assert current.action.waiting_on is not None
                            if (current.action.waiting_on
                                    in self._deterministic_blockers):
                                assert self._run_blocked is not None
                                action_identity = (
                                    current.action.waiting_on,
                                    current.action.action_sha256,
                                )
                                if (
                                    current.action.waiting_on
                                    in self._fresh_epoch_blockers
                                    and action_identity not in freshened_actions
                                ):
                                    # typed Stage4 repair/replan 不是原 producer 的
                                    # same-run resume。边界必须在 permit/Popen 之前，
                                    # 并由下一 epoch 重读 next；不把旧快照直接
                                    # 搬过去执行。
                                    freshened_actions.add(action_identity)
                                    boundary_turn = epoch_last or ExecutionTurnResult(
                                        exit_code=0,
                                        continue_ready=True,
                                        outcome=AgentDeliveryOutcome(
                                            state='unknown',
                                            checked_at=int(time.time()),
                                            reason=(
                                                'typed recovery 已进入新 producer '
                                                'execution epoch 边界'),
                                        ),
                                    )
                                    close_epoch(epoch, boundary_turn)
                                    reason = 'continue'
                                    break
                                try:
                                    recovered = self._launch(
                                        lambda ticket: self._run_blocked(
                                            epoch, current, ticket))
                                except PrelaunchIdentityDrift:
                                    # 只接受 callback 显式证明的 pre-Popen 信号；
                                    # 已 commit 后的任意异常仍由外层按 70 处理，不能
                                    # 借一次同时发生的 code drift 洗白真实执行故障。
                                    if self._identity_current(epoch):
                                        raise
                                    identity_turn = epoch_last or ExecutionTurnResult(
                                        exit_code=0,
                                        continue_ready=True,
                                        outcome=AgentDeliveryOutcome(
                                            state='unknown', checked_at=int(time.time()),
                                            reason=(
                                                'deterministic permit 形成前 '
                                                'execution identity 已变化'),
                                        ),
                                    )
                                    close_epoch(epoch, identity_turn)
                                    reason = 'code_change'
                                    break
                                assert isinstance(recovered, SupervisorActionResult)
                                turn = action_turn(recovered)
                                last = epoch_last = active_epoch_last = turn
                                action_pause_note = recovered.pause_note
                            else:
                                # Commander reviewer 已退役：审批闸属于人工。
                                # 停止本次 parent，等待用户过闸后原配置续跑。
                                stopped = _human_gate_turn(
                                    current.action.waiting_on)
                                last = epoch_last = active_epoch_last = stopped
                                close_epoch(epoch, stopped)
                                return SupervisorResult(turn=stopped, stopped=False)

                    except _LaunchDeferredByControl:
                        # next 已返回、ticket 尚未发放的窄窗里，控制请求
                        # 赢得线性化点是正常 barrier，不是 70。pause 后
                        # 必须重读 next/CAS，不能执行暂停前的旧快照。
                        if self._pause_barrier(
                                note=(
                                    '用户请求 pause；当前 execution '
                                    '尚未启动动作')):
                            stopped_turn = epoch_last or ExecutionTurnResult(
                                exit_code=2,
                                continue_ready=False,
                                outcome=AgentDeliveryOutcome(
                                    state='stopped',
                                    checked_at=int(time.time()),
                                    reason=(
                                        '操作员在动作启动前请求 stop；'
                                        '未发放 launch ticket'),
                                ),
                            )
                            last = active_epoch_last = stopped_turn
                            close_epoch(epoch, stopped_turn)
                            return SupervisorResult(
                                turn=stopped_turn, stopped=True)
                        # resume 后也从循环顶重新核 identity 并重读 next；
                        # 若此时已经漂移，顶部会用明确的 0/unknown 边界
                        # 关闭，而不是把无 action 的 epoch 误记成 70。
                        continue

                    with self._condition:
                        pause_requested = self._pause_requested
                        stop_requested = self._stop_requested
                    if stop_requested:
                        close_epoch(epoch, turn)
                        return SupervisorResult(turn=turn, stopped=True)
                    if action_pause_note is not None:
                        self._request_recovery_pause()
                        if self._pause_barrier(note=action_pause_note):
                            close_epoch(epoch, turn)
                            return SupervisorResult(turn=turn, stopped=True)
                        resumed_from_action_pause = True
                    if pause_requested:
                        if self._pause_barrier(
                                note='用户请求 pause；当前 execution 已关闭并 drain'):
                            close_epoch(epoch, turn)
                            return SupervisorResult(turn=turn, stopped=True)
                    if not self._identity_current(epoch):
                        close_epoch(epoch, turn)
                        reason = 'code_change'
                        break
                    if not turn.continue_ready and not resumed_from_action_pause:
                        close_epoch(epoch, turn)
                        return SupervisorResult(turn=turn, stopped=False)
        finally:
            try:
                if active_epoch is not None and not active_epoch_closed:
                    close_epoch(active_epoch, active_epoch_last)
            finally:
                with self._condition:
                    self._running = False
                    self._paused = False
                    self._resume_requested = False
                    self._launching = False
                    self._launch_committed = False
                    self._condition.notify_all()


__all__ = [
    'ExecutionTurnResult', 'LaunchTicket', 'PrelaunchIdentityDrift',
    'RunSupervisor',
    'SupervisorActionResult', 'SupervisorResult',
]
