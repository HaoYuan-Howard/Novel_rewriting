#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""整书 AgentRun 的纯生命周期监督器。

Supervisor 不理解小说节点，也不直接调用任何模型 SDK。真正的一轮 Commander
由调用方注入；本类只决定普通 turn 何时保持同一 provider session 精确续会、何时因身份
漂移滚动 execution epoch，以及 pause / resume / stop 应落在哪个安全边界。少数已登记
的 deterministic recovery 本身就是新 producer 边界：它们在执行前同样关闭旧
epoch，但不启动 Commander。Stage2 reviewer 的 fresh-session 策略由注入回调自行
实现。

pause 不是“杀进程后猜它停了”：请求只关闭当前 Commander turn 的 admission，
阻止它再 claim 新节点；已经取得 claim 的节点自然完成、release 并 drain，最后才写
``paused``。因此书级 writer/OS owner lease 可以由外层一直持有，已花钱的节点不会
因暂停白跑，迟到子进程也不能越过已关闭 gate。只有 stop 才取消 Commander 进程。
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from contracts.schema.api import OperatorNext
from contracts.schema.orchestration import (
    AgentDeliveryOutcome, AgentRunEvent, ExecutionEpoch,
)
from novel.orchestrator.jobs import JobConflict


class PrelaunchIdentityDrift(RuntimeError):
    """callback 在 launch ticket commit 前证明 execution identity 已漂移。"""


class _LaunchDeferredByControl(JobConflict):
    """pause/stop 先于 launch ticket 取得线性化点。

    它是 ``RunSupervisor.run`` 内部捕获的控制信号，既不属于
    Supervisor 崩溃，也不越过 HTTP 边界；公开控制路由只回 202。
    """


@dataclass(frozen=True)
class CommanderTurnResult:
    """调用方完成一轮 Commander、审计与确定性 next 检查后的事实。"""

    exit_code: int
    provider_session_id: str | None
    continue_ready: bool
    outcome: AgentDeliveryOutcome
    # canonical reviewer 动作若在正式写入前被拒，下一启动窗口必须先完成
    # 同一 immutable workpack 的紧凑纠错；不能先重取全局 next，让失败尝试
    # 留下的 INVALID/stale surface 抢走原 reviewer 绑定。
    continue_reviewer: bool = False
    # fresh reviewer 席位的事实：provider 身份与本席已记 turn 数只供同一
    # binding 内的纠错续用，不进入 epoch 的 provider session 传递；
    # ``seat_started`` 表示本席 logical session 已落 started，收席时须补 ended。
    seat_provider_session_id: str | None = None
    seat_turns_recorded: int = 0
    seat_started: bool = False
    # 只有最后一次成功 provider attempt 可交付该文本；Supervisor
    # 必须先走 reviewer typed loader，不得把它直接当成动作。
    final_response: str | None = None


@dataclass(frozen=True)
class SupervisorResult:
    """Supervisor 退出循环时的最后事实；外层据此写 session/parent ended。"""

    turn: CommanderTurnResult | None
    stopped: bool


@dataclass(frozen=True)
class SupervisorActionResult:
    """Supervisor 直接执行一个确定性动作后的事实。"""

    exit_code: int
    outcome: AgentDeliveryOutcome
    continue_ready: bool = True
    failure_detail: str | None = None
    # ready action 可在自己的启动窗内并行完成一轮只读 Commander 预审；
    # 普通 reviewer 可回传 provider 身份续会；Stage2 fresh-session reviewer 不回传。
    provider_session_id: str | None = None
    # 只有 action 已经自行落下可验证的同 run 恢复凭据时才出现。Supervisor
    # 据此保持当前 epoch/书锁并开放 resume；普通 exit 2 仍永久结束本次 parent。
    pause_note: str | None = None

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
MakeEpoch = Callable[[str, int, str | None], ExecutionEpoch]
OpenEpoch = Callable[[ExecutionEpoch], None]
CloseEpoch = Callable[[ExecutionEpoch, CommanderTurnResult], str]
RunCommanderTurn = Callable[[ExecutionEpoch, str | None], CommanderTurnResult]
EpochIdentityCurrent = Callable[[ExecutionEpoch], bool]
CancelCommander = Callable[[], bool]
SetCommanderQuiesced = Callable[[bool], bool]
ReadOperatorNext = Callable[[ExecutionEpoch], OperatorNext]
RunReadyAction = Callable[
    [ExecutionEpoch, OperatorNext, str | None, LaunchTicket],
    SupervisorActionResult,
]
RunBlockedAction = Callable[
    [ExecutionEpoch, OperatorNext, LaunchTicket], SupervisorActionResult,
]
RunReviewer = Callable[
    [ExecutionEpoch, OperatorNext, str | None, LaunchTicket],
    CommanderTurnResult | None,
]
FinalizeRun = Callable[
    [ExecutionEpoch, LaunchTicket], SupervisorActionResult,
]


class RunSupervisor:
    """一个 parent run 的单写手状态机；可由 HTTP 线程并发请求控制。"""

    def __init__(
        self, *, run_id: str, append_event: AppendEvent,
        make_epoch: MakeEpoch, open_epoch: OpenEpoch,
        close_epoch: CloseEpoch, run_commander_turn: RunCommanderTurn,
        epoch_identity_current: EpochIdentityCurrent,
        cancel_commander: CancelCommander,
        set_commander_quiesced: SetCommanderQuiesced | None = None,
        initial_epoch: ExecutionEpoch | None = None,
        read_operator_next: ReadOperatorNext | None = None,
        run_ready_action: RunReadyAction | None = None,
        run_blocked_action: RunBlockedAction | None = None,
        deterministic_blockers: frozenset[str] = frozenset(),
        fresh_epoch_blockers: frozenset[str] = frozenset(),
        run_reviewer: RunReviewer | None = None,
        finalize_run: FinalizeRun | None = None,
    ) -> None:
        self.run_id = run_id
        self._append = append_event
        self._make_epoch = make_epoch
        self._open_epoch = open_epoch
        self._close_epoch = close_epoch
        self._run_turn = run_commander_turn
        self._identity_current = epoch_identity_current
        self._cancel = cancel_commander
        self._set_quiesced = set_commander_quiesced or (lambda _value: True)
        self._initial_epoch = initial_epoch
        deterministic = (
            read_operator_next, run_ready_action, run_reviewer, finalize_run)
        if any(callback is not None for callback in deterministic) and not all(
                callback is not None for callback in deterministic):
            raise ValueError('Supervisor 确定性主路的四个 callback 必须同时提供')
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
        self._run_ready = run_ready_action
        self._run_blocked = run_blocked_action
        self._deterministic_blockers = deterministic_blockers
        self._fresh_epoch_blockers = fresh_epoch_blockers
        self._run_reviewer = run_reviewer
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
        # 只封住新 claim，不取消正在运行的 Commander / Executor；调用者在 turn
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
        # stop 与 pause 不同：既封住新 claim，也取消顶层 Commander。
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
        """运行到流水线终态或 stop；没有 turn/epoch 上限。"""
        if self._read_next is not None:
            return self._run_deterministic()
        return self._run_legacy()

    def _run_legacy(self) -> SupervisorResult:
        """兼容已经存在的 Commander 驱动 v1；新 v2 启动不得落回此路。"""
        with self._condition:
            if self._running:
                raise JobConflict(f'run_id={self.run_id!r} Supervisor 已在运行')
            self._running = True
        ordinal = 0
        provider_session_id: str | None = None
        reason = 'initial'
        last: CommanderTurnResult | None = None

        def close_epoch(
                epoch: ExecutionEpoch,
                turn: CommanderTurnResult | None,
                *, provider_id: str | None) -> None:
            """任何已打开 epoch 都只在这里落一次关闭事实。

            Commander 可能在首 turn 建进程、解析 provider session 或打开 admission
            之前就抛异常。那时没有可复用的 ``last``；用明确的 70/unknown 事实关闭
            本 epoch，而不是借上一 epoch 的 turn（那会把退出码和交付结论串账）。
            ``_close_epoch`` 自己若无法证明 drain，也只能返回 ``unknown``。
            """
            closing_turn = turn or CommanderTurnResult(
                exit_code=70,
                provider_session_id=provider_id,
                continue_ready=False,
                outcome=AgentDeliveryOutcome(
                    state='unknown', checked_at=int(time.time()),
                    reason='Commander turn 在形成可审计结果前异常，交付状态未知'),
            )
            drain_state = self._close_epoch(epoch, closing_turn)
            self._append(AgentRunEvent(
                wire_version=2, run_id=self.run_id,
                kind='epoch_closed', ts=int(time.time()),
                epoch_id=epoch.epoch_id,
                exit_code=closing_turn.exit_code,
                drain_state=drain_state,
                provider_session_id=provider_id))

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
                    # parent 在 worker 启动前只 durable 写了 epoch_opened；legacy
                    # Commander 兼容路仍须在首个 turn 前打开 logical session。
                    # 新确定性主路传入的是 no-op，并只在真实 reviewer Popen 后记账。
                    self._open_epoch(epoch)
                else:
                    epoch = self._make_epoch(reason, ordinal, provider_session_id)
                    self._append(AgentRunEvent(
                        wire_version=2, run_id=self.run_id,
                        kind='epoch_opened', ts=int(time.time()),
                        epoch=epoch))
                    self._open_epoch(epoch)
                # ``last`` 是整个 parent 的最后事实，只用于 SupervisorResult；epoch
                # 关闭必须只引用本 epoch 的 turn，故每开一轮身份窗口都重新置空。
                epoch_last: CommanderTurnResult | None = None
                while True:
                    try:
                        turn = self._run_turn(epoch, provider_session_id)
                    except BaseException:
                        close_epoch(
                            epoch, epoch_last,
                            provider_id=provider_session_id)
                        raise
                    last = turn
                    epoch_last = turn
                    if turn.provider_session_id is not None:
                        if (provider_session_id is not None
                                and turn.provider_session_id != provider_session_id):
                            close_epoch(
                                epoch, epoch_last,
                                provider_id=provider_session_id)
                            raise RuntimeError(
                                'Commander exact resume 返回不同 provider session id')
                        provider_session_id = turn.provider_session_id

                    with self._condition:
                        pause_requested = self._pause_requested
                        stop_requested = self._stop_requested
                    identity_changed = not self._identity_current(epoch)

                    # stop 是永久终止，必须在本 turn 已自然闭合、drain 后关闭
                    # identity epoch；它与 pause 的可恢复语义不同。
                    if stop_requested:
                        close_epoch(
                            epoch, turn, provider_id=provider_session_id)
                        return SupervisorResult(turn=turn, stopped=True)

                    # 只有 fresh delivery 已经给出真实可交付终态，才允许它优先于
                    # 一枚已受理的 pause。``continue_ready=False`` 不能当终态：
                    # pause 已关闭 admission 时，Commander 可能恰因迟到 claim 被拒
                    # 而非零退出；blocked / unknown 也同样没有 ready action，却仍有
                    # 待人处理的后续。拿这个布尔值抢在 pause 前结束，会把 quiesce
                    # 自己制造的退出冒充 pipeline complete。
                    delivery_terminal = turn.outcome.state in (
                        'ready', 'ready_with_disclosures')
                    if delivery_terminal:
                        close_epoch(
                            epoch, turn, provider_id=provider_session_id)
                        return SupervisorResult(turn=turn, stopped=False)

                    resumed_from_pause = False
                    if pause_requested:
                        # 一轮 Commander 返回时，它自己的 admission 已关闭且 claim /
                        # beacon 已 drain。暂停只冻结“接下来是否再开一轮”，不关闭
                        # execution epoch；代码未变时 resume 必须沿用同一 epoch 与
                        # exact provider session。
                        stopped = self._pause_barrier(
                            note='用户请求 pause；当前 execution 已关闭并 drain')
                        if stopped:
                            close_epoch(
                                epoch, turn, provider_id=provider_session_id)
                            return SupervisorResult(turn=turn, stopped=True)
                        resumed_from_pause = True
                        identity_changed = not self._identity_current(epoch)
                        if not identity_changed:
                            if provider_session_id is None:
                                raise RuntimeError(
                                    'pause 恢复后仍有 ready action，但 Commander 没有返回'
                                    '可精确恢复的 provider session id')
                            continue

                    # 没有 pause 时维持既有停点语义：blocked、真实
                    # pipeline_complete 与异常都结束本次 parent；异常事实已经由
                    # 调用方写入 turn/session audit，不在这里洗成成功。pause 分支则
                    # 已在安全 barrier 等到用户明确 resume，不能再用旧 turn 的
                    # continue_ready=False 把刚恢复的 parent 当场结束。
                    if not turn.continue_ready and not resumed_from_pause:
                        close_epoch(
                            epoch, turn, provider_id=provider_session_id)
                        return SupervisorResult(turn=turn, stopped=False)

                    if not identity_changed:
                        if provider_session_id is None:
                            raise RuntimeError(
                                'operator next 仍有 ready action，但 Commander 没有返回'
                                '可精确恢复的 provider session id')
                        # 同一 epoch 身份稳定：下一轮只允许 exact resume。
                        continue

                    close_epoch(
                        epoch, turn, provider_id=provider_session_id)
                    # 身份变化不能把旧 provider 对话偷偷带进新代码；新 epoch
                    # 从新会话开始，旧会话 id 仍完整留在 epoch_closed/turn audit。
                    provider_session_id = None
                    reason = 'code_change'
                    break
        finally:
            with self._condition:
                self._running = False
                self._paused = False
                self._resume_requested = False
                self._condition.notify_all()

    def _run_deterministic(self) -> SupervisorResult:
        """v2 主路：Supervisor 读 next、执行 token，只在登记闸启动 reviewer。"""
        assert self._read_next is not None
        assert self._run_ready is not None
        assert self._run_reviewer is not None
        assert self._finalize is not None
        with self._condition:
            if self._running:
                raise JobConflict(f'run_id={self.run_id!r} Supervisor 已在运行')
            self._running = True
        ordinal = 0
        provider_session_id: str | None = None
        reason = 'initial'
        last: CommanderTurnResult | None = None
        active_epoch: ExecutionEpoch | None = None
        active_epoch_last: CommanderTurnResult | None = None
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

        def action_turn(result: SupervisorActionResult) -> CommanderTurnResult:
            return CommanderTurnResult(
                exit_code=result.exit_code,
                provider_session_id=(
                    result.provider_session_id or provider_session_id),
                continue_ready=result.continue_ready,
                outcome=result.outcome)

        def close_epoch(
                epoch: ExecutionEpoch,
                turn: CommanderTurnResult | None) -> None:
            nonlocal active_epoch_closed
            if active_epoch is not epoch:
                raise RuntimeError('试图关闭非当前 execution epoch')
            if active_epoch_closed:
                return
            closing = turn or CommanderTurnResult(
                exit_code=70,
                provider_session_id=provider_session_id,
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
                provider_session_id=provider_session_id))
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
                    epoch = self._make_epoch(reason, ordinal, None)
                    self._append(AgentRunEvent(
                        wire_version=2, run_id=self.run_id,
                        kind='epoch_opened', ts=int(time.time()), epoch=epoch))
                    active_epoch = epoch
                    active_epoch_last = None
                    active_epoch_closed = False
                    self._open_epoch(epoch)
                epoch_last: CommanderTurnResult | None = None
                reviewer_continuation: OperatorNext | None = None

                while True:
                    resumed_from_action_pause = False
                    if not self._identity_current(epoch):
                        identity_turn = epoch_last or CommanderTurnResult(
                            exit_code=0,
                            provider_session_id=provider_session_id,
                            continue_ready=True,
                            outcome=AgentDeliveryOutcome(
                                state='unknown', checked_at=int(time.time()),
                                reason='动作启动前 execution epoch 身份已变化'),
                        )
                        close_epoch(epoch, identity_turn)
                        provider_session_id = None
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

                    if reviewer_continuation is None:
                        try:
                            current = self._read_next(epoch)
                        except Exception:  # noqa: BLE001 —— 只在已证实身份漂移时换 epoch
                            # code/skill 可能在上面的前置 CAS 与 operator 的末端
                            # CAS 之间变化。此时 operator 内部关闭式拒绝并不是
                            # parent run 崩溃；封存旧 epoch 后用新身份重读即可。
                            # 身份仍 current 时原异常必须原样上抛，不能洗白真 bug。
                            if self._identity_current(epoch):
                                raise
                            identity_turn = epoch_last or CommanderTurnResult(
                                exit_code=0,
                                provider_session_id=provider_session_id,
                                continue_ready=True,
                                outcome=AgentDeliveryOutcome(
                                    state='unknown',
                                    checked_at=int(time.time()),
                                    reason=(
                                        'operator next 读取期间 execution epoch '
                                        '身份已变化')),
                            )
                            close_epoch(epoch, identity_turn)
                            provider_session_id = None
                            reason = 'code_change'
                            break
                    else:
                        # 只跨 reviewer 的下一启动窗口保留一次。执行动作本身仍由
                        # callback 现场铸 live permit；这里保留的是 typed request 的
                        # immutable workpack binding，不是执行授权。
                        current = reviewer_continuation
                        reviewer_continuation = None
                    if (current.code_version != epoch.code_version
                            or not self._identity_current(epoch)):
                        identity_turn = epoch_last or CommanderTurnResult(
                            exit_code=0,
                            provider_session_id=provider_session_id,
                            continue_ready=True,
                            outcome=AgentDeliveryOutcome(
                                state='unknown', checked_at=int(time.time()),
                                reason='operator next 与 execution epoch 身份不一致'),
                        )
                        close_epoch(epoch, identity_turn)
                        provider_session_id = None
                        reason = 'code_change'
                        break

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
                        executed = self._launch(
                            lambda ticket: self._run_ready(
                                epoch, current, provider_session_id, ticket))
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
                                boundary_turn = epoch_last or CommanderTurnResult(
                                    exit_code=0,
                                    provider_session_id=provider_session_id,
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
                                provider_session_id = None
                                reason = 'continue'
                                break
                            try:
                                recovered = self._launch(
                                    lambda ticket: self._run_blocked(
                                        epoch, current, ticket))
                            except _LaunchDeferredByControl:
                                # next 已返回、ticket 尚未发放的窄窗里，控制请求
                                # 赢得线性化点是正常 barrier，不是 70。pause 后
                                # 必须重读 next/CAS，不能执行暂停前的旧快照。
                                if self._pause_barrier(
                                        note=(
                                            '用户请求 pause；当前 execution '
                                            '尚未启动动作')):
                                    stopped_turn = epoch_last or CommanderTurnResult(
                                        exit_code=2,
                                        provider_session_id=provider_session_id,
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
                            except PrelaunchIdentityDrift:
                                # 只接受 callback 显式证明的 pre-Popen 信号；
                                # 已 commit 后的任意异常仍由外层按 70 处理，不能
                                # 借一次同时发生的 code drift 洗白真实执行故障。
                                if self._identity_current(epoch):
                                    raise
                                identity_turn = epoch_last or CommanderTurnResult(
                                    exit_code=0,
                                    provider_session_id=provider_session_id,
                                    continue_ready=True,
                                    outcome=AgentDeliveryOutcome(
                                        state='unknown',
                                        checked_at=int(time.time()),
                                        reason=(
                                            'deterministic permit 形成前 '
                                            'execution identity 已变化'),
                                    ),
                                )
                                close_epoch(epoch, identity_turn)
                                provider_session_id = None
                                reason = 'code_change'
                                break
                            assert isinstance(recovered, SupervisorActionResult)
                            turn = action_turn(recovered)
                            last = epoch_last = active_epoch_last = turn
                            action_pause_note = recovered.pause_note
                        else:
                            reviewed = self._launch(
                                lambda ticket: self._run_reviewer(
                                    epoch, current, provider_session_id, ticket))
                            if reviewed is None:
                                stopped = CommanderTurnResult(
                                    exit_code=2,
                                    provider_session_id=provider_session_id,
                                    continue_ready=False,
                                    outcome=AgentDeliveryOutcome(
                                        state='stopped', checked_at=int(time.time()),
                                        reason=(
                                            '当前 blocker 没有登记为 Commander reviewer task：'
                                            f'{current.action.waiting_on}')))
                                last = epoch_last = active_epoch_last = stopped
                                close_epoch(epoch, stopped)
                                return SupervisorResult(turn=stopped, stopped=False)
                            turn = reviewed
                            last = epoch_last = active_epoch_last = turn
                            if turn.continue_reviewer:
                                reviewer_continuation = current

                    if turn.provider_session_id is not None:
                        if (provider_session_id is not None
                                and turn.provider_session_id
                                != provider_session_id):
                            close_epoch(epoch, turn)
                            raise RuntimeError(
                                'Commander exact resume 返回不同 provider session id')
                        provider_session_id = turn.provider_session_id

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
                        provider_session_id = None
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
    'CommanderTurnResult', 'LaunchTicket', 'PrelaunchIdentityDrift',
    'RunSupervisor',
    'SupervisorActionResult', 'SupervisorResult',
]
