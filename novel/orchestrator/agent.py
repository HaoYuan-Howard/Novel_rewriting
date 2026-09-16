#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent.py —— 前端一键唤起供应商中立的 headless executor 代跑一本书。

    Output/_service/agent_runs.jsonl        append-only 事件流（生死与改判）
    Output/_service/logs/agent-<run_id>.log agent 进程的 stdout+stderr，按字节追加

【它为什么不是 `jobs.jsonl` 的一种新作业】`JobStart` 的后置条件钉死
`node ∈ NODES`，而 agent 代跑**不是流水线节点**——它是「拉起一个会自己去跑
节点的进程」。往 `NODES` 塞一个假节点，污染的是完成度/贵贱/写盘效应的
全部派生源头。所以它有自己的事件文件，形状照抄 `jobs.py`（同一条
append-only 理由：永不读-改-写，被 kill 最坏只损坏最后一行）。

【角色边界】受托 Commander reviewer 已退役（2026-09-15 用户裁决）：本模块
只保留确定性编排与事件账——Supervisor 执行 ready executor token、登记过的
deterministic recovery 与交付终验；走到人工审批闸时 parent 以 exit 2 停止，
等用户在 Stage 页面过闸后用原配置续跑。executor 子进程的 argv、stdin 与
进程树取消在 ``novel/agents`` 与 ``run_step``；历史 commander 会话行、
reviewer 席位账与成本行仍按 append-only 原字节只读解码。

【冻结边界】preview / 单本 start / batch 入队共用 ``plan``。profile 在那里关闭式
校验并深拷贝；排队项携带整份 plan，出队不再读取默认或重新解释请求。当前服务没有
默认 profile，``profiles=None`` 是显式终态，不拿 capability 第一项替用户选择。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from contextlib import nullcontext
from dataclasses import dataclass, replace
from pathlib import Path
from typing import NamedTuple

from contracts.artifacts import STAGE5_REVIEWER_REWRITE_REQUEST
from contracts.invariants import NODES
from contracts.schema.api import ExecutionPermit, OperatorNext, OperatorNode
from contracts.schema.orchestration import (
    AgentDeliveryOutcome, AgentPending, AgentQueueView, AgentRejected,
    AgentRunEvent, AgentRunExitOrigin,
    AgentExecutorBackend, AgentRunRecord, AgentRunStart, RunContinuationSource,
    ExecutionEpoch, PipelineAgentProfiles, RunContractV2,
)
from novel import paths, prov, run_identity
from novel.agents.capabilities import (
    AgentProfileRefused, validate_pipeline_profiles,
)
from novel.appendlog import (
    append_line, append_line_under_process_lock, append_transaction,
)
from novel.exit_codes import CRASH, landed
from novel.orchestrator.execution_context import (
    env_with_context, action_admission_id_belongs_to_epoch,
)
from novel.agents.base import _signal_process_tree
from novel.errors import (
    ApprovalRequired, TerminalState, UnsupportedRunMode, UpstreamMissing,
)
from novel.model_slots import MODEL_BOOK_DIR_ENV
from novel.orchestrator import (
    admission, execution_permit, jobs, operator, queue,
    reviewer_action_evidence, service_lifecycle, state,
)
from novel.orchestrator.agent_supervisor import (
    ExecutionTurnResult, LaunchTicket, PrelaunchIdentityDrift, RunSupervisor,
    SupervisorActionResult,
)
from novel.orchestrator.jobs import JobConflict, JobRegistry, service_dir
from novel.orchestrator.nodes import shell_line
from novel.store import ArtifactStore
from novel.work_units import require_workflow

RUNS_FILENAME = 'agent_runs.jsonl'
LOG_PREFIX = 'agent-'

#: 仓根：agent.py → orchestrator → novel → 仓根。agent 的 cwd 钉死在这里——
#: 正式 `-m novel.cli` 与 runner 子进程使用此仓根 cwd。
REPO_ROOT = Path(__file__).resolve().parents[2]

#: **表和键都住在 `jobs.py`**（`AGENT_REGISTRY` / `agent_lock_key`）——
#: `run()`/`start()` 拿它做反向互斥，而 jobs 不 import 本模块，环就这样断掉。
#: ⚠ 键的构造**不在这里重写一遍**：两个落点会在改了一边时静默分家。
REGISTRY = jobs.AGENT_REGISTRY
lock_key = jobs.agent_lock_key

#: 同时在跑的 agent 上限。**改这一个数字就是改并发。**
#:
#: ⚠ 为什么必须有闸：一本整书代跑会保持一个 parent execution epoch，
#: 各付费节点又会创建各自隔离的 executor 会话；两层都可能产生外部模型费用。
#: 没有闸的话，界面上勾 10 本点一下就是 10 份同时烧，且 API 配额是共享的。
#: ⇒ 超出上限的不报错、进队列，由结束的那个 agent 把下一个带起来（`_drain`）。
#: ⚠ 它与锁是**两件事**：锁保证「同一本书不被两个 agent 同时写」，
#: 闸保证「同时烧的钱不超过 N 份」。把两者合成一个数字就会得到
#: 「想并发就得放弃同书互斥」的假选择。
MAX_CONCURRENT = 3

# 顶层 canonical runner 返回后，300 秒只是**报警与尝试 cancel 的阈值**，不是
# 释放锁的期限。admission claim 或 beacon execution 任一未闭合时都必须继续持有
# 书锁/并发槽；否则迟到写盘会与下一位写手并发。最终闭合后若曾越线，交付仍记
# unknown，不把“后来终于结束”改写成“生命周期始终可证”。
_EXECUTION_CLOSE_TIMEOUT_SECONDS = 300.0
_EXECUTION_CLOSE_POLL_SECONDS = 0.25
_ACTION_FAILURE_TAIL_BYTES = 16 * 1024
_ACTION_FAILURE_DETAIL_CHARS = 4096

#: executor-only epoch 的协议身份。历史 epoch 的 protocol_version 锚定 Commander
#: driver wire；Commander 退役后新 epoch 没有顶层 provider 协议，这一格只登记
#: Supervisor→executor 派发契约自身，供只读投影与哈希链继续覆盖。
_EPOCH_PROTOCOL_VERSION = 'supervisor-executor-v1'

_THREADS_LOCK = threading.Lock()
_THREADS: dict[str, threading.Thread] = {}
_ACTIONS_LOCK = threading.Lock()
_ACTIONS: dict[str, subprocess.Popen[bytes]] = {}
_SUPERVISORS_LOCK = threading.Lock()
_SUPERVISORS: dict[str, RunSupervisor] = {}

#: `R-11.11` 在这边的样子：服务重启后，agent 子进程是死是活我们真的不知道。
RESTART_NOTE = ('服务重启前它是死是活我们不知道——'
                '产物与信标都在盘上，照 `state` 与进度屏核实后再决定要不要重拉')


# ─────────────────────────────────────────────────────────────────────────────
# 一、Commander-free 启动计划（纯函数；preview 与真跑共用——`R-11.8` 同构）
# ─────────────────────────────────────────────────────────────────────────────

def _freeze_profiles(profiles: PipelineAgentProfiles | None,
                     ) -> PipelineAgentProfiles:
    """关闭式解析并深复制；``FrozenModel`` 本身不会冻结嵌套 dict。"""
    if profiles is None:
        raise UnsupportedRunMode(
            '服务端尚未设置默认 agent profiles；请显式选择默认 executor 与逐节点覆盖')
    try:
        resolved = validate_pipeline_profiles(profiles)
    except AgentProfileRefused as exc:
        raise UnsupportedRunMode(str(exc)) from exc
    return PipelineAgentProfiles.model_validate(
        resolved.model_dump(mode='json'))


def _copy_profiles(profiles: PipelineAgentProfiles) -> PipelineAgentProfiles:
    """复制嵌套 overrides；对外 view 不得与内存队列共享可变 dict。"""
    return PipelineAgentProfiles.model_validate(
        profiles.model_dump(mode='json'))


@dataclass(frozen=True)
class AgentRunPlan:
    """一次 preview / start 共用的不可变 executor 代跑启动计划。

    Commander 退役后 plan 不再构造顶层进程 prompt/argv；授权与 egress 事实
    由 contract/profiles 冻结，执行细节由 Supervisor 逐动作铸 permit。
    """

    base: str
    sid: str
    authorized: bool
    egress_authorized: bool
    code_version: str
    profiles: PipelineAgentProfiles
    continuation: RunContinuationSource | None = None
    pause_before_node: OperatorNode | None = None


class _UnspecifiedRunBoundary:
    """只用于 Python 调用口区分省略与显式解除；不进入任何持久载体。"""


_RUN_BOUNDARY_UNSPECIFIED = _UnspecifiedRunBoundary()


def _plan_resolved(
        base: str | os.PathLike[str], sid: str, *, authorized: bool,
        egress_authorized: bool, profiles: PipelineAgentProfiles,
        code_version: str | None = None,
        pause_before_node: OperatorNode | None = None) -> AgentRunPlan:
    base_abs = os.path.abspath(os.fspath(base))
    frozen = _copy_profiles(profiles)
    code = code_version or prov.code_version()
    if pause_before_node is not None and pause_before_node not in NODES:
        raise UnsupportedRunMode('运行范围上界必须是当前已登记节点')
    return AgentRunPlan(
        base=base_abs, sid=sid, authorized=authorized,
        egress_authorized=egress_authorized, code_version=code,
        profiles=frozen, pause_before_node=pause_before_node)


def plan(
        base: str | os.PathLike[str], sid: str, *, authorized: bool,
        egress_authorized: bool,
        profiles: PipelineAgentProfiles | None,
        continuation: RunContinuationSource | None = None,
        pause_before_node: OperatorNode | None | _UnspecifiedRunBoundary = _RUN_BOUNDARY_UNSPECIFIED,
        ) -> AgentRunPlan:
    """preview、单本启动与批量入队的唯一 planner；不发模型请求、不写盘。"""
    if continuation is not None:
        from novel.orchestrator import agent_continuation
        profiles = agent_continuation.validate(base, sid, continuation, profiles)
        if isinstance(pause_before_node, _UnspecifiedRunBoundary):
            source = agent_continuation.inspect(base, continuation.run_id)
            if not source.available or source.source != continuation:
                raise UnsupportedRunMode('续跑范围来源已漂移，不能猜测旧边界')
            pause_before_node = source.pause_before_node
    elif isinstance(pause_before_node, _UnspecifiedRunBoundary):
        pause_before_node = None
    planned = _plan_resolved(
        base, sid, authorized=authorized,
        egress_authorized=egress_authorized,
        profiles=_freeze_profiles(profiles), pause_before_node=pause_before_node)
    return replace(planned, continuation=continuation)


# ─────────────────────────────────────────────────────────────────────────────
# 二、事件流（append-only；写读纪律与 `jobs.py` 逐条同构）
# ─────────────────────────────────────────────────────────────────────────────

def runs_path(base: str | os.PathLike[str]) -> Path:
    return service_dir(base) / RUNS_FILENAME


def run_log_path(base: str | os.PathLike[str], run_id: str) -> Path:
    return jobs.log_path(base, LOG_PREFIX + run_id)


def append(base: str | os.PathLike[str], event: AgentRunEvent) -> None:
    """追加一行。**永不读-改-写**（同 `jobs.append_event` 那条理由）。

    ⚠ 这一份目前只有服务进程写，但互斥仍走 `novel/appendlog.py` 那一个落点：
    五份 append-only JSONL 里留一份「自己持锁」会在下一次改布局时分家，
    而 `R-14.6` 已经登记了「多实例绕得过进程内的锁」这条限制。
    """
    append_line(
        runs_path(base),
        json.dumps(event.model_dump(mode='json'), ensure_ascii=False))


def read_events(base: str | os.PathLike[str]) -> list[AgentRunEvent]:
    """读回事件流。**只容忍半截的最后一行**（同 `jobs.read_events` 那条）。"""
    p = runs_path(base)
    if not p.is_file():
        return []
    raw = p.read_bytes().decode('utf-8', errors='replace').splitlines()
    out: list[AgentRunEvent] = []
    for i, line in enumerate(raw):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            out.append(AgentRunEvent.replay_validate(data))
        except Exception:                       # noqa: BLE001 —— 半截行容错
            if i == len(raw) - 1:
                break
            raise
    return out


def fold(events: list[AgentRunEvent], *, live: set[str] | None = None) -> list[AgentRunRecord]:
    """事件流 → 运行记录。**折叠规则只有这一处**（与 `jobs.fold` 同一条纪律）。

    `started` 无 `ended`：在活锁里 ⇒ `running`；不在 ⇒ **`unknown` 不是「成功」**
    （`R-11.11`——服务重启后子进程死活未知，`note` 必须说出这句话）。
    """
    live = live or set()
    order: list[str] = []
    acc: dict[str, dict] = {}
    for e in events:
        slot = acc.setdefault(e.run_id, {
            'start': None, 'contract': None, 'epochs': [],
            'active_epoch_id': None, 'last_kind': None,
            'started': None, 'ended': None, 'exit_code': None, 'exit_origin': None,
            'note': None, 'outcome': None,
        })
        if e.run_id not in order:
            order.append(e.run_id)
        if e.kind == 'started':
            if e.wire_version == 2:
                assert e.contract is not None
                start = e.start or e.contract.legacy_start()
                if start is None:
                    # started 没有首次审计投影时无法为旧 UI/CLI 伪造 argv/prompt；
                    # 早期 v2 行只有在 contract 内确实留有 legacy 字节时才可恢复。
                    continue
                slot.update(
                    start=start,
                    contract=e.contract, started=e.ts)
            else:
                slot.update(start=e.start, started=e.ts)
        elif e.kind == 'epoch_opened':
            assert e.epoch is not None
            slot['epochs'].append(e.epoch)
            slot['active_epoch_id'] = e.epoch.epoch_id
        elif e.kind == 'epoch_closed':
            if slot['active_epoch_id'] == e.epoch_id:
                slot['active_epoch_id'] = None
        elif e.kind == 'ended':
            # ended 是 parent 的永久终态。若坏尾/异常路径漏写 epoch_closed，
            # 不能继续向外投影一个“已结束但仍有 active epoch”的不可能状态；
            # 丢失的 drain 证据仍由 outcome/note 保持 unknown，而不是伪造关闭事件。
            slot.update(
                ended=e.ts, exit_code=e.exit_code, exit_origin=e.exit_origin, outcome=e.outcome,
                active_epoch_id=None)
        elif e.kind == 'reconciled':
            # 人工恢复只能证明 admission owner 已停并封住旧执行，不能补造
            # epoch_closed/ended 的退出码、drain 或 delivery 结论；只清除会误导
            # UI/恢复路的 active 指针，整条 parent lifecycle 继续保持 unknown。
            slot.update(note=e.note, active_epoch_id=None)
        else:
            slot['note'] = e.note
        slot['last_kind'] = e.kind
    out: list[AgentRunRecord] = []
    for run_id in reversed(order):              # 新的在前（同 JobList 的约定）
        slot = acc[run_id]
        if slot['start'] is None:
            continue                            # 半截 started 事件：没有载荷就无法成记录
        if slot['exit_code'] is not None:
            state = ('ok' if slot['exit_code'] == 0 else
                     'landed_red' if slot['exit_code'] == 1
                     and slot['exit_origin'] == 'novel_cli' else
                     'stopped' if slot['exit_code'] == 2
                     and slot['outcome'] is not None
                     and slot['outcome'].state == 'stopped' else 'failed')
            if state == 'landed_red':
                slot['note'] = slot['note'] or jobs.LANDED_RED_NOTE
            if slot['outcome'] is None:
                slot['outcome'] = AgentDeliveryOutcome(
                    state='unknown', checked_at=slot['ended'],
                    reason='历史 ended 事件没有交付终验载荷，不能从进程退出码补推可交付')
        elif slot['last_kind'] == 'reconciled':
            state = 'unknown'
        elif run_id in live:
            if slot['last_kind'] == 'paused':
                state = 'paused'
            elif slot['contract'] is not None \
                    and slot['active_epoch_id'] is None:
                state = 'pending'
            else:
                state = 'running'
        else:
            state = 'unknown'
            slot['note'] = slot['note'] or RESTART_NOTE
        if slot['contract'] is None:
            allowed_controls = ['stop'] if state == 'running' else []
        elif state in ('pending', 'running'):
            allowed_controls = ['pause', 'stop']
        elif state == 'paused':
            allowed_controls = ['resume', 'stop']
        else:
            allowed_controls = []
        out.append(AgentRunRecord(
            run_id=run_id, start=slot['start'], started=slot['started'],
            contract=slot['contract'], epochs=slot['epochs'],
            active_epoch_id=slot['active_epoch_id'],
            ended=slot['ended'], exit_code=slot['exit_code'],
            exit_origin=slot['exit_origin'],
            state=state, note=slot['note'], outcome=slot['outcome'],
            allowed_controls=allowed_controls))
    return out


def records(base: str | os.PathLike[str], *, registry: JobRegistry = REGISTRY,
            ) -> list[AgentRunRecord]:
    return fold(read_events(base), live=registry.live())


class AdmissionLifecycleBinding(NamedTuple):
    """一枚 gate 的 outer lifecycle，以及它是否属于 AgentRun。"""

    admission_id: str
    lifecycle_run_id: str
    agent_run: AgentRunRecord | None


def resolve_admission_lifecycle(
        base: str | os.PathLike[str], admission_id: str,
) -> AdmissionLifecycleBinding:
    """解析 admission→outer 的唯一、精确且关闭式联结。

    新 v2 admission 自带 durable ``lifecycle_run_id``。旧 v1 只允许用同 sid
    的唯一 ``AgentRunEvent(kind=epoch_opened)`` 补证，且 gate id 必须是该
    epoch 的 canonical action id（exact epoch 或合法 ``-tN``）；零条才按
    generic self gate 兼容，多条或账间漂移一律拒绝。
    """
    checked = run_identity.check_run_id(admission_id)
    gate = admission.snapshot(base, checked)
    if not gate.opened:
        raise UpstreamMissing(f'没有 admission_id={checked!r} 的 Commander gate')

    run_events = read_events(base)
    folded = {record.run_id: record for record in fold(run_events, live=set())}
    candidates: list[tuple[str, str, AgentRunRecord]] = []
    for event in run_events:
        if event.wire_version != 2 or event.kind != 'epoch_opened':
            continue
        assert event.epoch is not None
        if not action_admission_id_belongs_to_epoch(
                checked, event.epoch.epoch_id):
            continue
        starts = [
            item for item in run_events
            if item.wire_version == 2 and item.kind == 'started'
            and item.run_id == event.run_id
        ]
        if len(starts) != 1:
            raise TerminalState(
                f'admission {checked!r} 命中的 AgentRun {event.run_id!r} '
                f'必须恰有一条 wire v2 started，实得 {len(starts)} 条')
        start = starts[0]
        assert start.contract is not None
        if start.contract.sid != gate.sid:
            raise TerminalState(
                f'admission {checked!r} 精确命中 AgentRun '
                f'{event.run_id!r} 的 epoch_opened，但 sid 从 '
                f'{start.contract.sid!r} 漂移为 {gate.sid!r}；'
                '不得降级为 generic self gate')
        record = folded.get(event.run_id)
        if record is None or record.contract is None:
            raise TerminalState(
                f'admission {checked!r} 命中的 AgentRun {event.run_id!r} '
                '无法折叠为 wire v2 lifecycle')
        candidates.append((event.run_id, event.epoch.epoch_id, record))

    if len(candidates) > 1:
        identities = ', '.join(
            f'{run_id}/{epoch_id}'
            for run_id, epoch_id, _record in candidates)
        raise TerminalState(
            f'admission {checked!r} 命中多条 AgentRun epoch_opened：'
            f'{identities}；拒绝猜测 outer lifecycle')

    candidate_run_id = candidates[0][0] if candidates else None
    if gate.lifecycle_linked:
        if candidate_run_id is not None:
            if gate.lifecycle_run_id != candidate_run_id:
                raise TerminalState(
                    f'admission {checked!r} 的 durable lifecycle_run_id='
                    f'{gate.lifecycle_run_id!r} 与唯一 epoch owner '
                    f'{candidate_run_id!r} 不一致')
            return AdmissionLifecycleBinding(
                checked, gate.lifecycle_run_id, candidates[0][2])
        if gate.lifecycle_run_id != checked:
            raise TerminalState(
                f'admission {checked!r} 指向 outer lifecycle '
                f'{gate.lifecycle_run_id!r}，但找不到同 sid 的精确 epoch_opened')
        return AdmissionLifecycleBinding(checked, checked, None)

    if candidates:
        return AdmissionLifecycleBinding(
            checked, candidate_run_id, candidates[0][2])
    return AdmissionLifecycleBinding(checked, checked, None)


def reconcile_run(
        base: str | os.PathLike[str], run_id: str, *, admission_id: str,
        actor: str,
        reason: str, subject_sha256: str, force: bool = False,
) -> AgentRunRecord:
    """收口一枚 action admission，并投影到它所属的 outer AgentRun。

    ``reconciled`` 不是第二套恢复判定：admission seal 才是权威。这里仅在 seal
    当前仍绑定同一 subject 时追加生命周期投影；若进程恰死在两账之间，重试会
    识别已提交 seal 并幂等补上 AgentRun 事件。
    """
    checked = run_identity.check_run_id(run_id)
    checked_admission = run_identity.check_run_id(admission_id)
    binding = resolve_admission_lifecycle(base, checked_admission)
    if binding.lifecycle_run_id != checked or binding.agent_run is None \
            or binding.agent_run.run_id != checked:
        raise JobConflict(
            f'admission {checked_admission!r} 不唯一属于 AgentRun {checked!r}')
    rec = binding.agent_run
    if rec.contract is None:
        raise JobConflict('只有 wire v2 AgentRun 可与 Commander admission 联合 reconcile')
    normalized_actor = actor.strip()
    normalized_reason = reason.strip()
    if not normalized_actor or not normalized_reason:
        raise TerminalState('AgentRun reconcile 的 actor/reason 不得为空')
    note = (
        f'{normalized_actor}：{normalized_reason} '
        f'[admission_id={checked_admission} '
        f'admission_subject={subject_sha256}]')
    lifecycle_events = [
        event for event in read_events(base) if event.run_id == checked]
    if not lifecycle_events:
        raise UpstreamMissing(f'AgentRun {checked!r} 没有 lifecycle 事件')
    last_lifecycle = lifecycle_events[-1]
    if rec.ended is not None or last_lifecycle.kind == 'ended':
        raise JobConflict(
            f'AgentRun {checked!r} 已正常 ended；孤儿 gate 不得改写其终态')
    if last_lifecycle.kind == 'paused':
        raise JobConflict(
            f'AgentRun {checked!r} 已明确 paused；不得以崩溃 reconcile 覆盖')
    if last_lifecycle.kind == 'reconciled':
        gate = admission.snapshot(base, checked_admission)
        if (gate.terminal_kind == 'reconciled'
                and last_lifecycle.note != note):
            raise JobConflict(
                f'AgentRun {checked!r} 已由另一枚 reconcile 投影收口；'
                'actor/reason/force 或 subject 漂移，只允许 exact 补尾重试')
        if (gate.terminal_kind != 'reconciled'
                and not gate.reconcile_invalidated):
            raise JobConflict(
                f'AgentRun {checked!r} 已由另一枚 reconcile 投影收口；'
                '当前 admission 没有迟到事实反证，不得另开恢复决定')
    elif rec.state != 'unknown':
        raise JobConflict(
            f'AgentRun {checked!r} 当前 state={rec.state!r}；'
            '只有重启后 unknown lifecycle 可人工 reconcile')
    try:
        admission.reconcile_gate(
            base, checked_admission, actor=normalized_actor,
            reason=normalized_reason, subject_sha256=subject_sha256,
            force=force, lifecycle_run_id=checked)
    except TerminalState:
        current = admission.snapshot(base, checked_admission)
        if (current.terminal_kind != 'reconciled'
                or current.reconcile_invalidated
                or current.terminal_subject_sha256 != subject_sha256):
            raise
        if (current.terminal_actor != normalized_actor
                or current.terminal_reason != normalized_reason
                or current.terminal_force != bool(force)):
            raise TerminalState(
                'admission reconcile 已提交，但 actor/reason/force 与本次补尾请求漂移')

    # retry 补尾也必须重新证明 owner 已停；force 从不越过这枚 OS lease。
    with admission.require_owner_stopped(base, checked):
        current = admission.snapshot(base, checked_admission)
        if (current.terminal_kind != 'reconciled'
                or current.reconcile_invalidated
                or current.terminal_subject_sha256 != subject_sha256):
            raise TerminalState('admission reconcile seal 已变化，拒绝补 AgentRun 事件')
        if (current.lifecycle_run_id != checked
                or current.terminal_actor != normalized_actor
                or current.terminal_reason != normalized_reason
                or current.terminal_force != bool(force)):
            raise TerminalState(
                'admission reconcile 的 lifecycle/actor/reason/force 漂移，'
                '拒绝补 AgentRun 事件')
        event = AgentRunEvent(
            wire_version=2, run_id=checked, kind='reconciled',
            ts=int(time.time()), note=note)
        path = runs_path(base)
        with append_transaction(path):
            if not any(
                    existing.wire_version == 2
                    and existing.run_id == checked
                    and existing.kind == 'reconciled'
                    and existing.note == note
                    for existing in read_events(base)):
                append_line_under_process_lock(
                    path, json.dumps(
                        event.model_dump(mode='json'), ensure_ascii=False))
    done = next((item for item in records(base) if item.run_id == checked), None)
    if done is None:
        raise UpstreamMissing(f'AgentRun reconciled 事件落盘后记录消失：{checked!r}')
    return done


def active_run(base: str | os.PathLike[str], *, registry: JobRegistry = REGISTRY,
               ) -> AgentRunRecord | None:
    """此刻在跑的 agent 代跑（全局一把锁 ⇒ 至多一个）。**给 jobs 路由的反向互斥用**。"""
    return next((r for r in records(base, registry=registry)
                 if r.state in ('pending', 'running', 'paused')),
                None)


# ─────────────────────────────────────────────────────────────────────────────
# 三、拉起与熄火
# ─────────────────────────────────────────────────────────────────────────────


def _model_runtime_env(base: Path, sid: str, profiles: PipelineAgentProfiles) -> dict[str, str]:
    """实际子执行只用本次 plan 冻结的并发配置，不继承宿主的隐式提速参数。"""
    from contracts.schema.orchestration import PipelineConcurrency
    from novel.config import DEFAULT_CONCURRENCY, ENV_KEYS
    policy = profiles.concurrency or PipelineConcurrency()
    return {ENV_KEYS['concurrency_policy']: policy.model_dump_json(),
            ENV_KEYS['concurrency']: str(DEFAULT_CONCURRENCY),
            MODEL_BOOK_DIR_ENV: str(paths.resolve_novel_dir(base, sid))}


def _cancel_action_owner(run_id: str) -> bool:
    """取消 Supervisor 直接启动的 canonical runner/交付校验 进程树。"""
    with _ACTIONS_LOCK:
        process = _ACTIONS.get(run_id)
    if process is None or process.poll() is not None:
        return False
    return _signal_process_tree(process, force=True)


def _write_scratch_bytes(path: Path, payload: bytes) -> None:
    """同目录原子落一份本 run scratch；不同字节不覆盖。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() == payload:
            return
        raise JobConflict(f'scratch 已存在且字节不同，拒绝覆盖：{path}')
    fd, temporary = tempfile.mkstemp(
        prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            if path.read_bytes() != payload:
                raise JobConflict(f'scratch 并发落下不同字节：{path}')
            return
        os.replace(temporary, path)
        temporary = ''
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _action_failure_detail(path: Path, start_offset: int) -> str | None:
    """从本次 canonical action 的有界日志片段提取明确拒绝事实。"""
    try:
        size = path.stat().st_size
        begin = max(start_offset, size - _ACTION_FAILURE_TAIL_BYTES)
        with path.open('rb') as stream:
            stream.seek(begin)
            raw = stream.read(_ACTION_FAILURE_TAIL_BYTES)
    except OSError:
        return None
    lines = [line.strip() for line in raw.decode(
        'utf-8', errors='replace').splitlines() if line.strip()]
    if not lines:
        return None
    explicit = [line for line in lines if line.startswith('✗')]
    detail = explicit[-1] if explicit else '\n'.join(lines[-8:])
    if len(detail) <= _ACTION_FAILURE_DETAIL_CHARS:
        return detail
    # 只保留尾部会把“✗ 节点/规则/退出原因”切掉，最终只剩一段 SHA 或
    # remaining item 列表。失败详情仍保持有界，但同时保留诊断首尾。
    marker = '\n…[中间过长，已省略]…\n'
    room = _ACTION_FAILURE_DETAIL_CHARS - len(marker)
    head = room // 2
    return detail[:head] + marker + detail[-(room - head):]


def _write_identity_bound_scratch(
        directory: Path, *, stem: str, kind: str,
        identity_sha256: str, payload: bytes) -> Path:
    """按执行身份给同一 epoch 的多轮输入分配互不覆盖的文件名。

    Reviewer 可以在同一个 execution epoch 内多次处理同一 node/surface；固定
    文件名会把后一次合法的新 permit 或 typed 决定误判成覆盖。摘要前缀进入
    文件名后，相同身份仍幂等复用，不同身份各自保留；极低概率的前缀碰撞仍由
    底层写一次保护关闭式拒绝，不会覆盖旧证据。
    """
    path = directory / f'{stem}-{identity_sha256[:20]}.{kind}.json'
    _write_scratch_bytes(path, payload)
    return path


def _run_step_argv(
        base: str, sid: str, epoch: ExecutionEpoch, permit: ExecutionPermit,
        *, stem: str) -> list[str]:
    """物化完整 permit，并构造 Supervisor 唯一的 canonical runner argv。"""
    scratch = service_lifecycle.open_scratch_session(
        base, sid, epoch.epoch_id)
    token_path = _write_identity_bound_scratch(
        scratch, stem=stem, kind='execution-token',
        identity_sha256=permit.permit_sha256,
        payload=(permit.model_dump_json(indent=2) + '\n').encode('utf-8'))
    return [
        sys.executable, '-B', '-m', 'novel.orchestrator.run_step',
        '--base', base, '--sid', sid,
        '--expect-code-version', epoch.code_version,
        '--operator-permit-file', str(token_path), '--execute',
        permit.node, '--', *permit.flags,
    ]


def _reconcile_landed_stage5_reviewer_rewrite_action(
        base: str, sid: str, *, code: str,
        current_parent_run_id: str | None = None) -> None:
    """typed/current request 成功后把 active action 原字节移入 authority archive。

    归档而非删除，保证 queue/permit/paid/recovery 始终能重验 Commander 来源；
    active→archive 任一硬杀窗都可由下一 parent 幂等补完，scanner 不会把已被
    canonical request 精确引用的回答再次当作未执行 action。
    """
    from novel.stages.stage5 import reviewer_rewrite

    novel_dir = paths.resolve_novel_dir(base, sid)
    ctx = state.readonly_ctx(novel_dir, 'stage5.write', code)
    if not ArtifactStore(ctx).exists(STAGE5_REVIEWER_REWRITE_REQUEST):
        return
    request, request_file_sha256 = (
        reviewer_rewrite.load_reviewer_rewrite_batch(ctx))
    reviewer_action_evidence.seal_reviewer_rewrite_authority(
        base, sid, request, request_file_sha256,
        current_parent_run_id=current_parent_run_id or '')


# 三、并发闸与排队
# ─────────────────────────────────────────────────────────────────────────────

class AgentBusy(JobConflict):
    """并发闸满了。**与「这本书上有东西在写盘」分成两个类型不是洁癖**：

    前者**等一等就好**（队列该把这一项放回去），后者等也没用（该丢弃并说明）。
    `_drain` 靠这个区别决定是重排还是放弃——用错就会把整条队列卡死在第一本上，
    或者反过来把还能跑的书悄悄丢掉。
    """


#: 「数人数 + 占锁」必须是**同一个临界区**。
#: ⚠ 拆成两步的话，两个同时到达的请求会双双看到「还有空位」然后各占一把锁——
#: 闸就成了摆设，而且它**只在并发下失效**，也就是只在真的有很多本书要跑时失效。
_ADMIT_LOCK = threading.Lock()


class _Pending(NamedTuple):
    """排队中的一本。

    ⚠ `base` 也存：队列是进程级的，而「哪个 base」是每一项自己的事。
    """

    plan: AgentRunPlan
    batch_id: str


class _DrainResult(NamedTuple):
    """一次 drain **为调用它的那一批**实际启动/拒绝了哪些书。"""

    started: list[AgentRunRecord]
    rejected: list[AgentRejected]


#: 待跑队列。**只在内存**——与 `AGENT_REGISTRY` 同一条理由（`R-14.6` 那张表）：
#: 写下来就要有陈旧项的清理策略，清理策略又要有守卫，而一份**会说谎的队列**
#: 比没有队列更坏。⇒ 服务重启即清空，界面上照实写「重启不保留」。
_PENDING_LOCK = threading.Lock()
_PENDING: list[_Pending] = []

#: 同一时刻只许一个 drain 从队列取书。否则两个批量请求会互相启动对方的项，
#: 各自响应就无法再满足「running + pending + rejected = 本批提交」的后置条件。
_DRAIN_LOCK = threading.Lock()


def _admit(key: str, run_id: str, registry: JobRegistry) -> None:
    """闸 + 占锁，一个临界区内完成。

    满闸抛 `AgentBusy`；这本书已经有 agent 在跑由 `registry.acquire` 抛 `JobConflict`。
    """
    with _ADMIT_LOCK:
        live = len(registry.live())
        if live >= MAX_CONCURRENT:
            raise AgentBusy(
                f'已有 {live} 个 agent 在跑（上限 {MAX_CONCURRENT}）——'
                f'等一个结束，或用批量入口 `POST /api/agent/runs/batch` 排队')
        registry.acquire(key, run_id)


def _drain_locked(*, registry: JobRegistry,
                  reject_batch: str | None = None) -> _DrainResult:
    """``_DRAIN_LOCK`` 内的真实 drain；返回值只归 ``reject_batch``。"""
    started: list[AgentRunRecord] = []
    rejected: list[AgentRejected] = []
    with _PENDING_LOCK:
        remaining = len(_PENDING)
    while remaining > 0:
        with _ADMIT_LOCK:
            if len(registry.live()) >= MAX_CONCURRENT:
                break
        with _PENDING_LOCK:
            if not _PENDING:
                break
            item = _PENDING.pop(0)
        remaining -= 1
        try:
            record = _start_planned(item.plan, registry=registry)
        except AgentBusy:
            with _PENDING_LOCK:
                _PENDING.insert(0, item)
            break
        except UpstreamMissing as exc:
            # 这本书**不会因为等一会儿就回来**（已被移入回收站 / 目录被搬走）。
            # 挪到队尾等于让 `_drain` 每次事件都对它重试一遍，而队列会一直显示
            # 一本已经不存在的书 ⇒ 「一份会说谎的队列比没有队列更坏」。
            # ⇒ 出队丢弃；本批有槽就如实 rejected，旧项留一条结构化观测。
            if item.batch_id == reject_batch:
                rejected.append(AgentRejected(
                    sid=item.plan.sid,
                    reason=str(exc).strip() or 'UpstreamMissing：这本书不在盘上'))
            else:
                _observe_pending_dropped(
                    item.plan.base, item.plan.sid, str(exc))
            continue
        except Exception as exc:                # noqa: BLE001 —— 逐本显式归类
            if item.batch_id == reject_batch:
                reason = str(exc).strip() or f'{type(exc).__name__}：agent 启动失败'
                rejected.append(AgentRejected(sid=item.plan.sid, reason=reason))
            else:
                # 受理响应已经返回的旧项没有可回填的 rejected 槽；留在 pending
                # 才不会静默丢书。挪到队尾，让后面的无冲突书仍有机会启动。
                with _PENDING_LOCK:
                    _PENDING.append(item)
            continue
        if item.batch_id == reject_batch:
            started.append(record)
    return _DrainResult(started=started, rejected=rejected)


#: 出队丢弃的审计账。队列本身**只在内存**（陈旧队列会说谎），但「某本书从队列里
#: 消失了」这件事必须有一个答得出来的地方——否则界面上它就只是不见了。
#: 追加式、只写 `_service/`（`R-11.3`），没有清理策略也不会说谎。
QUEUE_DROPS_FILENAME = 'agent_queue_drops.jsonl'


def queue_drops_path(base: str | os.PathLike[str]) -> Path:
    return service_dir(base) / QUEUE_DROPS_FILENAME


def _observe_pending_dropped(base: str | os.PathLike[str], sid: str,
                             why: str) -> None:
    """一本排队中的书被**出队丢弃**时留一行；界面上它会直接从队列消失。"""
    append_line(queue_drops_path(base), json.dumps(
        {'ts': int(time.time()), 'sid': sid, 'why': why}, ensure_ascii=False))


def drop_pending(base: str | os.PathLike[str], sid: str) -> int:
    """把这本书从待跑队列里摘掉，返回摘掉几条。**回收站在用的那一半。**

    ⚠ 它必须由**仍持有该书作业锁的那一方**调用（`trash.run` 就在锁里调）。
    否则「摘队列」与「下一次 drain」之间还有缝：刚摘完，另一个 worker 结束触发
    `_drain`，而这本书又被别处 `enqueue` 回来了。持锁调用时 `start` 那道
    `conflicting_holder` 会挡住这中间的任何一次启动，缝就闭合了。
    ⚠ 取 `_DRAIN_LOCK` 而不只是 `_PENDING_LOCK`：drain 是「pop 出来 → 起进程」
    两步，只锁列表会让一条刚被 pop、还没启动的项从摘除里漏过去。
    """
    base_abs = os.path.abspath(os.fspath(base))
    with _DRAIN_LOCK, _PENDING_LOCK:
        keep = [item for item in _PENDING
                if not (item.plan.base == base_abs and item.plan.sid == sid)]
        dropped = len(_PENDING) - len(keep)
        _PENDING[:] = keep
    if dropped:
        _observe_pending_dropped(
            base_abs, sid, f'项目已移入回收站，摘掉 {dropped} 条排队项')
    return dropped


def _drain(*, registry: JobRegistry = REGISTRY) -> _DrainResult:
    """把队首能起的起起来。由结束的 worker 调用，`enqueue` 受理完也调一次。

    ⚠ 两种失败**处理方式相反**，这正是 `AgentBusy` 单独成类的原因：
      · 满闸（`AgentBusy`）⇒ **放回队首**并停下——名额是别人的，等他还回来；
      · 这本书起不来（同书已有 agent / 该书有 API 作业在写盘）⇒ 移到队尾并继续；
        已经返回过受理响应的项没有可回填的 rejected 槽，留在 pending 才不会静默丢书。
    """
    with _DRAIN_LOCK:
        return _drain_locked(registry=registry)


def _require_start_authorization(*, authorized: bool,
                                 egress_authorized: bool) -> None:
    """真启动在任何 CLI 解析/探针/模型 wire 之前同时要求两项显式授权。"""
    missing: list[str] = []
    if not authorized:
        missing.append('付费授权')
    if not egress_authorized:
        missing.append('正文外发授权')
    if missing:
        raise ApprovalRequired(
            f'启动 agent 代跑前必须显式授予 {"、".join(missing)}；'
            'preview 仍为 0 成本，可在确认冻结 profiles 后再启动')


def _require_reproducible_code_version(code_version: str) -> None:
    """付费启动前必须有可复验且干净的代码身份；preview 仍可照实展示。

    ``unknown == unknown`` 只证明两次都没问出来，不证明整书跑没有
    跨版本。放它进付费路，结束终验也注定只能 unknown，是可预知的白烧。
    dirty digest 虽然可复算，却会让运行中任何编辑触发 epoch rollover；新 epoch
    无法保证所有付费单元都能跨 run 采用，因此生产跑只接受冻结 commit。
    """
    if (not isinstance(code_version, str) or not code_version.strip()
            or code_version == prov.CODE_UNKNOWN):
        raise UnsupportedRunMode(
            '当前代码指纹不可用；preview 仍可查看，但不能启动付费 '
            'agent 代跑，请先恢复可复验的仓库代码身份')
    if '-dirty.' in code_version:
        raise UnsupportedRunMode(
            '当前工作树有未提交改动；preview 仍可查看，但不能启动或换入新的付费 '
            'epoch。请在干净的专用 worktree/冻结 commit 中跑书，避免代码漂移导致'
            '重复模型调用')


def enqueue(base: str | os.PathLike[str], sids: Sequence[str], *,
            authorized: bool, egress_authorized: bool,
            profiles: PipelineAgentProfiles | None,
            registry: JobRegistry = REGISTRY) -> AgentQueueView:
    """批量受理 N 本：能起的立刻起，起不动的排队。**返回本批的完整分区。**

    ⚠ 一本起不来不回滚其余：那本书上有 API 作业在写盘（唯一会失败的原因）
    与「另外八本能不能跑」无关，而抛一个「批量失败」会把已经起来的那几本藏掉——
    人看到的是一条报错，盘上却真的有进程在跑，那是最坏的一种不一致。
    ⇒ 逐本的落点全在返回值里：`running` / `pending` / `rejected` 三者之和 = 你提交的。
    """
    # ⚠ 必须早于 profile factory / CLI 入口解析：缺授权时不能用一次探针当试运行。
    _require_start_authorization(
        authorized=authorized, egress_authorized=egress_authorized)
    base_abs = os.path.abspath(os.fspath(base))
    resolved_profiles = _freeze_profiles(profiles)
    code_version = prov.code_version()
    _require_reproducible_code_version(code_version)
    batch_id = jobs.new_job_id()
    rejected: list[AgentRejected] = []
    with _DRAIN_LOCK:
        for sid in sids:
            with _PENDING_LOCK:
                queued = {
                    p.plan.sid for p in _PENDING if p.plan.base == base_abs
                }
            if sid in queued:
                rejected.append(AgentRejected(sid=sid, reason='已经在队列里了'))
                continue
            if registry.holder(lock_key(sid)) is not None:
                rejected.append(AgentRejected(sid=sid, reason='这本书已有 agent 在跑'))
                continue
            if paths.find_novel_dir(base_abs, sid) is None:
                rejected.append(AgentRejected(
                    sid=sid, reason=f'{base_abs} 下没有 id={sid} 的书目录——'
                                    '这本书已经不在语料库里（多半在回收站）'))
                continue
            item_plan = _plan_resolved(
                base_abs, sid, authorized=authorized,
                egress_authorized=egress_authorized,
                profiles=resolved_profiles, code_version=code_version)
            with _PENDING_LOCK:
                _PENDING.append(_Pending(item_plan, batch_id))
        drained = _drain_locked(registry=registry, reject_batch=batch_id)
        rejected.extend(drained.rejected)
        with _PENDING_LOCK:
            base_pending = [
                item for item in _PENDING if item.plan.base == base_abs
            ]
            pending = [
                AgentPending(
                    sid=item.plan.sid, position=index + 1,
                    profiles=_copy_profiles(item.plan.profiles))
                for index, item in enumerate(base_pending)
                if item.batch_id == batch_id
            ]
    return AgentQueueView(running=drained.started, pending=pending,
                          rejected=rejected, max_concurrent=MAX_CONCURRENT)


def queue_view(base: str | os.PathLike[str], *,
               registry: JobRegistry = REGISTRY) -> AgentQueueView:
    """当前这个 base 上：谁在跑、谁在排队、闸开多大。

    drain 的线性化区间是“从 pending pop → ``_start_planned`` 登记
    running”。读快照若不取同一把锁，前端会在这条缝里同时
    看到 running/pending 为空，误以为任务消失。
    """
    base_abs = os.path.abspath(os.fspath(base))
    with _DRAIN_LOCK:
        running = [
            r for r in records(base, registry=registry)
            if r.state == 'running']
        with _PENDING_LOCK:
            pending = [AgentPending(
                           sid=p.plan.sid, position=i + 1,
                           profiles=_copy_profiles(p.plan.profiles))
                       for i, p in enumerate(
                           [q for q in _PENDING if q.plan.base == base_abs])]
    return AgentQueueView(running=running, pending=pending,
                          max_concurrent=MAX_CONCURRENT)


def _session_delivery(outcome: AgentDeliveryOutcome) -> str:
    if outcome.state in ('ready', 'ready_with_disclosures'):
        return 'delivered'
    if outcome.state == 'stopped':
        return 'blocked'
    if outcome.state == 'failed':
        return 'failed'
    return 'unknown'


def _append_safe_traceback(base: str, run_id: str) -> None:
    """留异常类型/栈；日志仍只进本 run 的 append-only 文件，不外发。"""
    import traceback

    rendered = traceback.format_exc()
    try:
        path = run_log_path(base, run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'ab') as fh:
            fh.write(rendered.encode('utf-8', errors='replace'))
    except OSError:
        # 日志目录不可写时，事件账/成本账仍要尽力闭合；
        # 不能让「记不下调试栈」再遮住原始异常。
        return


def _close_failed_start_gate(
        base: str, run_id: str, original: BaseException) -> None:
    """启动失败时尽力 close/drain，但绝不让清理异常遮住原始失败。

    close 与 drained 是两次 durable append，任一都可能因磁盘/坏尾失败。两步必须
    各自尝试；失败会作为 exception note 留在原异常上。调用方再用 ``finally``
    释放内存 writer，这样盘上 unresolved 会继续关闭式拦截，进程内也不会死锁。
    """
    failures: list[str] = []
    for label, operation in (
        ('close_gate', lambda: admission.close_gate(base, run_id)),
        ('mark_drained', lambda: admission.mark_drained(base, run_id)),
    ):
        try:
            operation()
        except BaseException as exc:  # noqa: BLE001 —— 正在清理原异常，不能反客为主
            failures.append(f'{label}: {type(exc).__name__}: {exc}')
    if failures and hasattr(original, 'add_note'):
        original.add_note(
            'Commander 启动失败后的 admission 清理未完全落盘；盘上 gate 将继续'
            '关闭式阻断新写手：' + '；'.join(failures))


@dataclass(frozen=True)
class _ExecutionDrainResult:
    timed_out: bool
    timeout_reason: str | None = None


def _append_lifecycle_warning(base: str, parent_run_id: str, message: str) -> None:
    """drain 报警只进本 run 日志，不制造一条提前 ended。"""
    try:
        path = run_log_path(base, parent_run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'ab') as handle:
            handle.write((f'\n[LIFECYCLE] {message}\n').encode(
                'utf-8', errors='replace'))
            handle.flush()
    except OSError:
        return


def _wait_for_execution_closure(
        base: str, admission_id: str, *,
        beacon_run_id: str | None = None) -> _ExecutionDrainResult:
    """先关闭 admission，再等 claim 与 beacon 两面精确闭合。

    300 秒只触发一次报警/cancel。越线后仍无限持有本书锁与 queue slot，且不做
    delivery、不写 ended；只有盘上两面都闭合才返回。这样服务进程活着时不会
    为了“避免卡队列”主动制造孤儿并发；若服务崩溃，下一次 start/jobs 还会由
    durable unresolved admission 继续关闭式挡住。
    """
    from novel.orchestrator import beacon

    parent_run_id = beacon_run_id or admission_id

    timeout = _EXECUTION_CLOSE_TIMEOUT_SECONDS
    if timeout <= 0:
        raise RuntimeError('canonical execution 收尾等待上限必须大于 0')
    deadline = time.monotonic() + timeout
    timed_out = False
    timeout_reason: str | None = None
    while True:
        gate_error: Exception | None = None
        beacon_error: Exception | None = None
        gate = None
        opened = None
        try:
            # 第一次循环发生在顶层 invocation 刚 return/raise 的 finally 边界；
            # close 与 run_step.claim 使用同一个 append transaction。重试幂等，
            # 短暂 I/O 故障恢复后仍能把 gate 真正关上，而不是凭内存布尔值猜。
            admission.close_gate(base, admission_id)
            # close_gate 的事务只管 admission 顺序；随后取联合快照，才能看见
            # reconciled 之后新增的任一 beacon 对旧 seal 的反证。
            gate = admission.snapshot(base, admission_id)
        except Exception as exc:  # noqa: BLE001 —— 证明不了 close 就继续持锁
            gate_error = exc
        if gate is not None:
            try:
                opened = beacon.open_executions(base, parent_run_id)
            except Exception as exc:  # noqa: BLE001 —— 坏账也不能当“没有 execution”
                beacon_error = exc
        if gate is not None and gate.reconcile_invalidated:
            reason = (
                'Commander admission 的人工 reconcile 已被迟到 release/beacon '
                '反证；当前服务拒绝继续终验，按 unknown 收口并保留 unresolved')
            _append_lifecycle_warning(base, parent_run_id, reason)
            return _ExecutionDrainResult(
                timed_out=True, timeout_reason=reason)
        if gate is not None and gate.terminal_kind == 'reconciled':
            # CLI apply 会拒绝本进程 live owner；若仍观察到这一态，说明另一个
            # 服务进程/人工恢复者已经显式接管。继续尝试 mark_drained 永远不会
            # 成功，只会永久占锁；按 unknown 收口且绝不再做 delivery。
            reason = (
                'Commander admission 已被外部人工 reconciled 接管；'
                '当前服务拒绝继续终验，按 unknown 收口')
            _append_lifecycle_warning(base, parent_run_id, reason)
            return _ExecutionDrainResult(
                timed_out=True, timeout_reason=reason)
        if (gate is not None and gate.closed and not gate.active_claims
                and opened == ()):
            # “双面此刻为空”要物化成 durable 终态；否则服务恰在 delivery/ended
            # 前崩溃，重启后无法区分“已安全 drain”与“只看过一次空快照”。
            try:
                admission.mark_drained(base, admission_id)
            except Exception as exc:  # noqa: BLE001 —— 没写成 durable 终态仍须持锁
                gate_error = exc
            else:
                return _ExecutionDrainResult(
                    timed_out=timed_out, timeout_reason=timeout_reason)

        remaining = deadline - time.monotonic()
        if remaining <= 0 and not timed_out:
            timed_out = True
            parts: list[str] = []
            if gate_error is not None:
                parts.append(
                    f'admission close/read 失败：{type(gate_error).__name__}: '
                    f'{gate_error}')
            elif gate is not None and gate.active_claims:
                detail = '、'.join(
                    f'{item.node}/{item.claim_id}'
                    for item in gate.active_claims[:3])
                suffix = ('' if len(gate.active_claims) <= 3
                          else f' 等 {len(gate.active_claims)} 个')
                parts.append(f'active claims：{detail}{suffix}')
            if beacon_error is not None:
                parts.append(
                    f'beacon 折叠失败：{type(beacon_error).__name__}: '
                    f'{beacon_error}')
            elif opened:
                detail = '、'.join(
                    f'{event.node}/{event.execution_id}' for event in opened[:3])
                suffix = '' if len(opened) <= 3 else f' 等 {len(opened)} 个'
                parts.append(f'open executions：{detail}{suffix}')
            if not parts:
                parts.append('admission/beacon 尚未形成可证闭合状态')
            action_cancel = _cancel_action_owner(parent_run_id)
            cancel_note = (
                'canonical action cancel='
                f'{"已确认" if action_cancel else "无活动 owner"}')
            timeout_reason = (
                f'canonical action 结束后等待 {timeout:g} 秒仍未闭合（'
                + '；'.join(parts) + f'）；{cancel_note}。'
                '继续持有本书锁与并发槽，最终闭合后仍按 unknown 收口')
            _append_lifecycle_warning(base, parent_run_id, timeout_reason)
        time.sleep(_EXECUTION_CLOSE_POLL_SECONDS)


def _assess_delivery(base: str, sid: str, *, code_version: str,
                      ) -> AgentDeliveryOutcome:
    """execution 已闭合后重验盘；验盘自身崩溃只得到 unknown。"""
    try:
        from novel.orchestrator import delivery
        outcome = delivery.assess(
            base, sid, start_code_version=code_version)
        if not isinstance(outcome, AgentDeliveryOutcome):
            raise TypeError(
                'delivery.assess 必须返回 AgentDeliveryOutcome')
        return outcome
    except Exception as exc:  # noqa: BLE001 —— 终验崩溃也必须留下 ended
        return AgentDeliveryOutcome(
            state='unknown', checked_at=int(time.time()),
            reason=('execution 已闭合，但交付终验自身崩溃：'
                    f'{type(exc).__name__}: {exc}'))


def _delivery_pause_note(
        base: str, sid: str, epoch: ExecutionEpoch,
        node: str, rc: int,
) -> str | None:
    """只从 producer 自己落下并严格校验的凭据开放同 run resume。"""
    if rc != 2 or node not in ('stage2.blueprint', 'stage4.plan'):
        return None
    novel_dir = paths.find_novel_dir(base, sid)
    if novel_dir is None:
        return None
    if node == 'stage2.blueprint':
        from novel.llm import stage2_runs
        recovery = stage2_runs.read_delivery_recovery(
            novel_dir, epoch.epoch_id, sid=sid, code=epoch.code_version)
        label = 'Stage2 blueprint'
        unit = '章'
    else:
        from novel.llm import stage4_runs
        recovery = stage4_runs.read_plan_delivery_recovery(
            novel_dir, epoch.epoch_id, sid=sid, code=epoch.code_version)
        label = 'Stage4 plan'
        unit = '个歧义章'
    if recovery is None:
        return None
    completed = recovery['completed_chapters']
    pending = recovery['pending_chapters']
    failures = recovery['failures']
    kind_labels = {
        'authentication': '登录状态失效',
        'connection': '网络连接中断',
        'provider_transient': '供应商瞬时拒绝',
        'rate_limit': '供应商限流',
        'server_error': '供应商服务异常',
        'timeout': '请求超时',
        'usage_limit': '账号用量窗口已满',
    }
    failure_labels = '、'.join(sorted({
        f'{kind_labels.get(str(row["error_kind"]), row["error_kind"])}'
        f'{"/HTTP " + str(row["status"]) if row["status"] else ""}'
        f'/已尝试 {row["attempts"]} 次'
        for row in failures
    }))
    pending_text = '、'.join(str(chapter) for chapter in pending[:12])
    if len(pending) > 12:
        pending_text += '…'
    return (
        f'{label} 的 Agent 交付暂不可用（{failure_labels}）；本轮已保留 '
        f'{len(completed)}/{recovery["total_chapters"]} {unit} checkpoint，'
        f'待补第 {pending_text} 章。当前 parent/epoch 保持不变；'
        '确认账号、额度或网络恢复后点“继续”，只请求缺章。')


def start(base: str | os.PathLike[str], sid: str, *, authorized: bool,
          egress_authorized: bool,
          profiles: PipelineAgentProfiles | None,
          continuation: RunContinuationSource | None = None,
          pause_before_node: OperatorNode | None | _UnspecifiedRunBoundary = _RUN_BOUNDARY_UNSPECIFIED,
          registry: JobRegistry = REGISTRY) -> AgentRunRecord:
    """解析一次冻结 plan 后拉起确定性代跑；缺任一授权时不触碰 CLI。"""
    _require_start_authorization(
        authorized=authorized, egress_authorized=egress_authorized)
    return _start_planned(
        plan(base, sid, authorized=authorized,
             egress_authorized=egress_authorized, profiles=profiles, continuation=continuation,
             pause_before_node=pause_before_node),
        registry=registry)


def _start_planned(plan: AgentRunPlan, *,
                   registry: JobRegistry = REGISTRY) -> AgentRunRecord:
    """拉起一次 agent 代跑（**异步**）：锁 → 记 `started` → 线程跑子进程 → 记 `ended`。

    三道闸，各管一边：
      · 本书一把锁——同一本书一次只跑一个 agent（`lock_key`）；
      · 并发闸——同时在跑的 agent 不超过 `MAX_CONCURRENT`（满了抛 `JobConflict`，
        批量入口 `enqueue` 会把它接住改成排队，单本入口就是明确的 409）；
      · 反向互斥——这本书上有 API 作业在跑 ⇒ 409（agent 会自己起 `novel.cli`
        子进程，两边同时写同一本书就是 同书互斥禁止的并行写盘）。
    ⚠ 两张 registry 各自线程安全仍不够：agent 与 job 若各自「查对面 → 占自己」，
    可以同时看见空表再各拿一把。双方统一经 ``jobs.acquire_*_writer``，在同一个
    短临界区内完成 durable/对表检查与占锁；运行期仍只持本书逻辑 writer，异书并行。
    """
    _require_start_authorization(
        authorized=plan.authorized,
        egress_authorized=plan.egress_authorized)
    base = plan.base
    sid = plan.sid
    require_workflow(base)
    code_version = plan.code_version
    _require_reproducible_code_version(code_version)
    run_id = jobs.new_job_id()
    session_run_id = jobs.new_job_id()
    parent_started_at = int(time.time())
    # Commander 退役后新 run 没有顶层进程 argv/prompt；两格保持 None 并在
    # 序列化时省略，历史行原值只读解码（见 AgentRunStart/ExecutionEpoch 注释）。
    start_payload = AgentRunStart(
        sid=sid, base=base, argv=None, prompt=None,
        authorized=plan.authorized,
        egress_authorized=plan.egress_authorized,
        code_version=code_version, identity_version=2,
        profiles=plan.profiles, pause_before_node=plan.pause_before_node)
    contract_payload = RunContractV2(
        sid=sid, base=base,
        authorized=plan.authorized,
        egress_authorized=plan.egress_authorized,
        profiles=plan.profiles, continuation=plan.continuation,
        pause_before_node=plan.pause_before_node)
    initial_epoch = ExecutionEpoch(
        epoch_id=run_id, ordinal=1, reason='initial',
        argv=None, prompt_sha256=None,
        protocol_version=_EPOCH_PROTOCOL_VERSION,
        cli_version=None, code_version=code_version,
        identity_version=2, session_run_id=session_run_id)
    key = jobs.acquire_agent_writer(
        base, sid, run_id, admit=_admit, registry=registry)
    owner_guard = admission.owner_lease(base, run_id)
    owner_guard_lock = threading.Lock()
    owner_guard_active = False

    def release_owner_lease() -> None:
        nonlocal owner_guard_active
        with owner_guard_lock:
            if not owner_guard_active:
                return
            owner_guard_active = False
        owner_guard.__exit__(None, None, None)

    outer_started = False
    try:
        # ⚠ 「书还在不在」必须**占锁之后**再查，而且要单独查一次。
        #   上面的共享 writer 只覆盖「删除**正在进行**」（`trash.run` 持
        #   同一把书级键）；删除**已经完成**时那把锁早还回去了，而队列里可能还压着
        #   这本书的旧 pending 项——那正是实测复现的「书删完了 agent 才起来」。
        #   ⇒ 这一格是永久性拒绝（`UpstreamMissing`），`_drain_locked` 据此把它
        #   从队列里**丢掉**而不是挪到队尾无限重试。
        if paths.find_novel_dir(base, sid) is None:
            raise UpstreamMissing(
                f'{base} 下已经没有 id={sid} 的书目录（多半是刚被移入回收站）——'
                'agent 代跑不对一本不存在的书启动')
        # owner lease 必须先于 admission open；这样 tail repair 扫到 lease 时，
        # owner 要么已被证明停止，要么尚未进入可启动 Commander 的 durable 边界。
        source_guard = (admission.require_owner_stopped(base, plan.continuation.run_id)
                        if plan.continuation is not None else nullcontext())
        with source_guard:
            if plan.continuation is not None:
                from novel.orchestrator import agent_continuation
                agent_continuation.validate(base, sid, plan.continuation, plan.profiles)
            owner_guard.__enter__()
            with owner_guard_lock:
                owner_guard_active = True
            append(base, AgentRunEvent(
                wire_version=2, run_id=run_id, kind='started',
                ts=parent_started_at, contract=contract_payload,
                start=start_payload))
        outer_started = True
        append(base, AgentRunEvent(
            wire_version=2, run_id=run_id, kind='epoch_opened',
            ts=int(time.time()), epoch=initial_epoch))
    except BaseException as original:
        cleanup_failures: list[str] = []
        try:
            if outer_started:
                try:
                    append(base, AgentRunEvent(
                        wire_version=2, run_id=run_id, kind='ended',
                        ts=int(time.time()),
                        exit_code=CRASH, exit_origin='supervisor',
                        outcome=AgentDeliveryOutcome(
                            state='failed', checked_at=int(time.time()),
                            reason=(
                                'agent run started 审计未能落盘，'
                                '未启动进程'))))
                except BaseException as exc:  # noqa: BLE001 —— 保留最初的启动失败
                    cleanup_failures.append(
                        f'agent ended: {type(exc).__name__}: {exc}')
        finally:
            # close / drained / ended 任一落盘失败，都不能把进程内书锁永远留住。
            # 盘上未闭合 gate 会继续由 durable admission 关闭式阻断新写手。
            try:
                release_owner_lease()
            except BaseException as exc:  # noqa: BLE001 —— 保留最初的启动失败
                cleanup_failures.append(
                    f'owner lease: {type(exc).__name__}: {exc}')
            finally:
                registry.release(key, run_id)
        if cleanup_failures and hasattr(original, 'add_note'):
            original.add_note('agent 启动失败的事件收尾未完全落盘：'
                              + '；'.join(cleanup_failures))
        raise

    active_admission_lock = threading.Lock()
    active_admission_id: str | None = None
    admission_quiesced = False
    epoch_action_counts: dict[str, int] = {}
    epoch_drain_states: dict[str, str] = {}
    latest_outcome = AgentDeliveryOutcome(
        state='unknown', checked_at=int(time.time()),
        reason='Supervisor 尚未完成任何 action')
    latest_rc = CRASH
    latest_exit_origin = 'supervisor'
    latest_error_kind: str | None = None

    def _make_epoch(reason: str, ordinal: int) -> ExecutionEpoch:
        """按新身份滚动 execution epoch；没有 provider 会话可续接。

        历史上 reason=continue 时可以带 provider session id 续接同一 Commander
        会话；Commander 退役后每次滚 epoch 都是全新 executor 生产边界，新
        session_run_id 同时充当下游 executor 成本账的 run 身份。
        """
        nonlocal session_run_id
        current_code = prov.code_version()
        _require_reproducible_code_version(current_code)
        session_run_id = jobs.new_job_id()
        return ExecutionEpoch(
            epoch_id=jobs.new_job_id(), ordinal=ordinal, reason=reason,
            argv=None, prompt_sha256=None,
            protocol_version=_EPOCH_PROTOCOL_VERSION,
            cli_version=None, code_version=current_code,
            identity_version=2, session_run_id=session_run_id)

    def _close_epoch(epoch: ExecutionEpoch, turn: ExecutionTurnResult) -> str:
        drain_state = epoch_drain_states.get(
            epoch.epoch_id,
            'drained' if epoch_action_counts.get(epoch.epoch_id, 0) == 0
            else 'unknown')
        if drain_state == 'drained':
            service_lifecycle.finish_scratch_session(
                base, sid, epoch.epoch_id)
        return drain_state

    def _epoch_identity_current(epoch: ExecutionEpoch) -> bool:
        try:
            return (
                epoch.identity_version == 2
                and epoch.skill_sha256 is None
                and epoch.code_version != prov.CODE_UNKNOWN
                and prov.code_version() == epoch.code_version)
        except Exception:  # noqa: BLE001 —— 取不到身份就必须滚 epoch
            return False

    def _cancel_active() -> bool:
        _cancel_action_owner(run_id)
        # 安全边界上没有活动 owner 时，stop 仍已通过 launch ticket 阻止新启动；
        # 这里的布尔只表示取消请求已送达或当下无需取消，不决定 drain。
        return True

    def _set_quiesced(value: bool) -> bool:
        """pause/stop 只封新 claim；已有 claim 由 action finally 等到 release。"""
        nonlocal admission_quiesced
        with active_admission_lock:
            admission_quiesced = value
            current = active_admission_id
        if value and current is not None:
            admission.close_gate(base, current)
        return True

    def _open_action_admission(epoch: ExecutionEpoch) -> str:
        """给 ready/reviewer/交付校验 共用一条严格递增的 admission 序列。"""
        nonlocal active_admission_id
        count = epoch_action_counts.get(epoch.epoch_id, 0) + 1
        epoch_action_counts[epoch.epoch_id] = count
        admission_id = (
            epoch.epoch_id if count == 1 else f'{epoch.epoch_id}-t{count}')
        try:
            admission.open_gate(
                base, admission_id, sid, lifecycle_run_id=run_id)
        except BaseException as original:
            _close_failed_start_gate(base, admission_id, original)
            raise
        with active_admission_lock:
            active_admission_id = admission_id
            close_immediately = admission_quiesced
        if close_immediately:
            admission.close_gate(base, admission_id)
        return admission_id

    def _run_supervisor_process(
            epoch: ExecutionEpoch, argv: Sequence[str],
            launch_ticket: LaunchTicket, *,
            label: str,
            environment_overrides: dict[str, str] | None = None,
            ) -> tuple[int, bool, str | None]:
        """启动并 drain 一条 Supervisor canonical 子进程；Popen 后才 commit。"""
        nonlocal active_admission_id
        admission_id = _open_action_admission(epoch)
        process: subprocess.Popen[bytes] | None = None
        drain = _ExecutionDrainResult(timed_out=False)
        rc = CRASH
        log_path = run_log_path(base, run_id)
        log_start = 0
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            if log_path.exists():
                log_start = log_path.stat().st_size
            child_env = env_with_context(
                run_id=run_id, base=base, start=start_payload,
                contract=contract_payload, epoch=epoch,
                admission_id=admission_id, env=os.environ)
            if environment_overrides:
                child_env.update(environment_overrides)
            child_env.update(_model_runtime_env(base, sid, plan.profiles))
            options: dict[str, object] = {}
            if os.name == 'nt':
                options['creationflags'] = (
                    subprocess.CREATE_NO_WINDOW
                    | subprocess.CREATE_NEW_PROCESS_GROUP)
            else:
                options['start_new_session'] = True
            with open(log_path, 'ab') as fh:
                fh.write((f'$ [{label}] ' + shell_line(argv) + '\n').encode('utf-8'))
                fh.flush()
                process = subprocess.Popen(
                    list(argv), cwd=str(REPO_ROOT), env=child_env,
                    stdin=subprocess.DEVNULL, stdout=fh, stderr=fh,
                    shell=False, **options)
                with _ACTIONS_LOCK:
                    if run_id in _ACTIONS:
                        _signal_process_tree(process, force=True)
                        raise JobConflict(
                            f'run_id={run_id!r} 已有 canonical action owner')
                    _ACTIONS[run_id] = process
                launch_ticket.commit()
                rc = int(process.wait())
        except BaseException:
            if process is not None and process.poll() is None:
                _signal_process_tree(process, force=True)
                process.wait()
            raise
        finally:
            drain = _wait_for_execution_closure(
                base, admission_id, beacon_run_id=run_id)
            with _ACTIONS_LOCK:
                if _ACTIONS.get(run_id) is process:
                    _ACTIONS.pop(run_id, None)
            with active_admission_lock:
                if active_admission_id == admission_id:
                    active_admission_id = None
            epoch_drain_states[epoch.epoch_id] = (
                'unknown' if drain.timed_out else 'drained')
        detail = (
            _action_failure_detail(log_path, log_start) if rc != 0 else None)
        return rc, drain.timed_out, detail

    def _action_outcome(
            epoch: ExecutionEpoch, rc: int, timed_out: bool,
            *, label: str,
            failure_detail: str | None = None,
            pause_note: str | None = None,
            exit_origin: AgentRunExitOrigin = 'novel_cli') -> SupervisorActionResult:
        nonlocal latest_outcome, latest_rc, latest_error_kind
        latest_rc = rc
        if timed_out:
            latest_error_kind = f'{label}_execution_barrier'
            latest_outcome = AgentDeliveryOutcome(
                state='unknown', checked_at=int(time.time()),
                reason=f'{label} 的 admission/beacon drain 无法证明闭合')
            return SupervisorActionResult(
                exit_code=rc, outcome=latest_outcome, continue_ready=False,
                exit_origin=exit_origin, failure_detail=failure_detail)
        action_landed = exit_origin == 'novel_cli' and landed(rc)
        if rc == 0 or action_landed:
            latest_outcome = _assess_delivery(
                base, sid, code_version=epoch.code_version)
            latest_error_kind = None
        elif rc == 2:
            latest_error_kind = f'{label}_explicit_stop'
            latest_outcome = AgentDeliveryOutcome(
                state='stopped', checked_at=int(time.time()),
                reason=(
                    f'{label} 返回显式终态 exit 2，正式产物未落盘'
                    + (f'；{failure_detail}' if failure_detail else '')))
        else:
            latest_error_kind = f'{label}_process_exit'
            latest_outcome = AgentDeliveryOutcome(
                state='unknown', checked_at=int(time.time()),
                reason=(
                    f'{label} 子进程 exit {rc}，执行结论未知'
                    + (f'；{failure_detail}' if failure_detail else '')))
        return SupervisorActionResult(
            exit_code=rc, outcome=latest_outcome, exit_origin=exit_origin,
            # 正常成功或 novel CLI 的已落盘负结论才允许重新规划下一步。
            # 2 是显式终态、3 是结构拒绝、70 是崩溃；继续循环会在盘面
            # 未变化时重复执行同一个 ready token，付费节点尤其危险。
            continue_ready=(rc == 0 or action_landed), failure_detail=failure_detail,
            pause_note=pause_note)

    def _run_token(
            epoch: ExecutionEpoch, permit: ExecutionPermit,
            launch_ticket: LaunchTicket, *, stem: str,
            environment_overrides: dict[str, str] | None = None,
            ) -> SupervisorActionResult:
        argv = _run_step_argv(base, sid, epoch, permit, stem=stem)
        rc, timed_out, failure_detail = _run_supervisor_process(
            epoch, argv, launch_ticket, label=stem,
            environment_overrides=environment_overrides)
        pause_note = (
            None if timed_out else
            _delivery_pause_note(base, sid, epoch, permit.node, rc))
        return _action_outcome(
            epoch, rc, timed_out, label=stem,
            failure_detail=failure_detail, pause_note=pause_note)

    def _read_operator_next(epoch: ExecutionEpoch) -> OperatorNext:
        # 新 base 不读取旧四席次数账。producer journal / typed 接受各自补提交；
        # 工作单元保存原回答，planner 重启不会重新发放预算。
        _reconcile_landed_stage5_reviewer_rewrite_action(
            base, sid, code=epoch.code_version,
            current_parent_run_id=run_id)
        return operator.next_action_at_code(
            base, sid, code=epoch.code_version)

    def _ready_requires_fresh_epoch(epoch: ExecutionEpoch, current: OperatorNext) -> bool:
        action = current.action
        if (current.state != 'ready' or action is None
                or action.node not in {'stage4.design', 'stage4.plan'}
                or tuple(action.flags) not in {(), ('--reaggregate',)}):
            return False
        from novel.stages.stage4.design_rebuild import producer_requires_new_run
        ctx = state.readonly_ctx(paths.resolve_novel_dir(base, sid), action.node, epoch.code_version)
        ctx.run_id, ctx.parent_run_id = epoch.session_run_id, run_id
        from novel.llm.ledger import CostLedger
        ctx.ledger = CostLedger(path=None, run_id=ctx.run_id, parent_run_id=ctx.parent_run_id)
        backend = plan.profiles.executor_overrides.get(action.node, plan.profiles.executor_default)
        if isinstance(backend, AgentExecutorBackend):
            ctx.executor_profile = backend.profile
        return producer_requires_new_run(ctx)

    def _run_ready_action(
            epoch: ExecutionEpoch, current: OperatorNext,
            launch_ticket: LaunchTicket) -> SupervisorActionResult:
        """所有生产只执行状态机当前许可；Supervisor 不并行发起预审。"""
        if current.execution_token is None:
            raise RuntimeError('ready next 缺 execution token')
        permit = current.execution_token
        return _run_token(epoch, permit, launch_ticket, stem=f'ready-{permit.node}')

    def _run_blocked_action(
            epoch: ExecutionEpoch, current: OperatorNext,
            launch_ticket: LaunchTicket) -> SupervisorActionResult:
        """执行 Supervisor 已按关闭注册表选中的 deterministic recovery。"""
        action = current.action
        if (current.state != 'blocked' or action is None
                or action.waiting_on not in queue.DETERMINISTIC_RECOVERY_WAITING_ON):
            raise RuntimeError('Supervisor 交入了未登记的 deterministic blocker')
        if (action.waiting_on == queue.WAITING_ON_STAGE5_REVIEWER_REWRITE
                and not action.flags):
            # queue 的 unknown 分支明确没有可执行 argv；它仍占住专属 waiter，
            # 但不能被空 flags 解释成默认付费写作，也不能重开模型席位。
            return SupervisorActionResult(
                exit_code=2, continue_ready=False,
                outcome=AgentDeliveryOutcome(
                    state='unknown', checked_at=int(time.time()),
                    reason=('Stage5 Reviewer 改写执行证据尚不能证明可恢复；'
                            '当前没有可执行动作，保留原候选与执行账等待恢复')))
        try:
            permit = execution_permit.permit_from_next(
                current, action.node, action.flags)
        except Exception as exc:
            # permit 是 Popen 前的末端 live CAS。只有这里能证明 ticket 尚未
            # commit；身份已漂移时通知 Supervisor 换 epoch 重读。身份仍 current
            # 的 schema/surface 错误必须原样成为故障，不能泛化成 rollover。
            if not _epoch_identity_current(epoch):
                raise PrelaunchIdentityDrift(
                    'deterministic permit 形成前 execution identity 已漂移') from exc
            raise
        recovered = _run_token(
            epoch, permit, launch_ticket,
            stem=f'recovery-{permit.node}')
        return recovered

    def _finalize_run(
            epoch: ExecutionEpoch,
            launch_ticket: LaunchTicket) -> SupervisorActionResult:
        argv = [
            sys.executable, '-B', '-m', 'novel.orchestrator.delivery',
            '--base', base, '--sid', sid,
            '--start-code-version', epoch.code_version,
        ]
        rc, timed_out, failure_detail = _run_supervisor_process(
            epoch, argv, launch_ticket, label='delivery-assess')
        return _action_outcome(
            epoch, rc, timed_out, label='delivery-assess',
            # 交付终验的 1 表示校验未通过，属于自身状态域；只有
            # novel CLI 的 1 才承诺正式结果落盘，不能仅按 canonical 身份套用。
            failure_detail=failure_detail, exit_origin='supervisor')

    supervisor = RunSupervisor(
        run_id=run_id,
        append_event=lambda event: append(base, event),
        make_epoch=_make_epoch,
        # v2 epoch 本身不是模型会话；executor 子进程各自在 session_audit
        # 开 node 级 logical session，parent 侧没有需要物化的 session 起点。
        open_epoch=lambda _epoch: None,
        close_epoch=_close_epoch,
        epoch_identity_current=_epoch_identity_current,
        cancel_active=_cancel_active,
        set_quiesced=_set_quiesced,
        initial_epoch=initial_epoch,
        read_operator_next=_read_operator_next,
        ready_requires_fresh_epoch=_ready_requires_fresh_epoch,
        run_ready_action=_run_ready_action,
        run_blocked_action=_run_blocked_action,
        deterministic_blockers=queue.DETERMINISTIC_RECOVERY_WAITING_ON,
        # stage4 design action 的人工 typed 恢复已随四道闸退役；
        # fresh-epoch 保护只剩 stage5 reviewer rewrite。
        fresh_epoch_blockers=frozenset({
            queue.WAITING_ON_STAGE5_REVIEWER_REWRITE,
        }),
        finalize_run=_finalize_run,
        pause_before_node=plan.pause_before_node)
    with _SUPERVISORS_LOCK:
        _SUPERVISORS[run_id] = supervisor

    def worker() -> None:
        nonlocal latest_outcome, latest_rc, latest_error_kind, latest_exit_origin
        try:
            supervised = supervisor.run()
            # Supervisor 的最后 typed 事实是 parent ended 的权威输入。
            # 未登记 blocker 等分支不经过 action callback；若不在这里投影，
            # epoch_closed=2/stopped 会与 ended=70/unknown 串账。
            if supervised.turn is not None:
                latest_rc = supervised.turn.exit_code
                latest_exit_origin = supervised.turn.exit_origin
                latest_outcome = supervised.turn.outcome
            if supervised.stopped:
                latest_outcome = AgentDeliveryOutcome(
                    state='stopped', checked_at=int(time.time()),
                    reason='外层操作员请求停止 AgentRun；execution 已关闭并 drain')
                latest_error_kind = latest_error_kind or 'operator_stop'
                if supervised.turn is None:
                    latest_rc = CRASH
        except BaseException:  # noqa: BLE001 —— Supervisor 崩溃也必须写 ended
            latest_rc = CRASH
            latest_exit_origin = 'supervisor'
            latest_error_kind = latest_error_kind or 'run_supervisor'
            latest_outcome = AgentDeliveryOutcome(
                state='unknown', checked_at=int(time.time()),
                reason='RunSupervisor 生命周期异常；拒绝猜测交付状态')
            _append_safe_traceback(base, run_id)
        finally:
            try:
                try:
                    end_code_version = prov.code_version()
                except Exception:
                    end_code_version = prov.CODE_UNKNOWN
                delivery_state = _session_delivery(latest_outcome)
                # 交付只与最后执行 epoch 的代码比较；parent contract 的首次指纹
                # 是审计起点，不再把一次合法 epoch rollover 判成整条 run 失效。
                current_epochs = next(
                    (record.epochs for record in records(base)
                     if record.run_id == run_id), [])
                expected_end_code = (
                    current_epochs[-1].code_version
                    if current_epochs else code_version)
                if (delivery_state == 'delivered'
                        and end_code_version != expected_end_code):
                    delivery_state = 'unknown'
                    latest_error_kind = (
                        latest_error_kind or 'code_version_drift')
                    latest_outcome = AgentDeliveryOutcome(
                        state='unknown', checked_at=int(time.time()),
                        reason='最后 execution epoch 结束后代码指纹又发生漂移')
                append(base, AgentRunEvent(
                    wire_version=2, run_id=run_id, kind='ended',
                    ts=int(time.time()), exit_code=latest_rc,
                    exit_origin=latest_exit_origin, outcome=latest_outcome))
            finally:
                try:
                    registry.release(key, run_id)
                    with _THREADS_LOCK:
                        _THREADS.pop(run_id, None)
                    with _SUPERVISORS_LOCK:
                        _SUPERVISORS.pop(run_id, None)
                    _drain(registry=registry)
                finally:
                    try:
                        release_owner_lease()
                    except BaseException:
                        _append_safe_traceback(base, run_id)

    thread = threading.Thread(target=worker, name=f'novel-agent-{run_id}',
                              daemon=True)
    with _THREADS_LOCK:
        _THREADS[run_id] = thread
    try:
        thread.start()
    except BaseException:
        with _THREADS_LOCK:
            _THREADS.pop(run_id, None)
        with _SUPERVISORS_LOCK:
            _SUPERVISORS.pop(run_id, None)
        # v2 只在真实 action launch 时开 admission；线程没起来时没有空 gate。
        # 但 epoch_opened 已经 durable append，仍必须在 ended 前以
        # 70/unknown 闭合，不能把“无 action”误写成一枚存活 active epoch。
        try:
            failed = AgentDeliveryOutcome(
                state='failed', checked_at=int(time.time()),
                reason='RunSupervisor worker 线程未能启动')
            append(base, AgentRunEvent(
                wire_version=2, run_id=run_id, kind='epoch_closed',
                ts=int(time.time()), epoch_id=initial_epoch.epoch_id,
                exit_code=CRASH, drain_state='drained',
                provider_session_id=None))
            append(base, AgentRunEvent(
                wire_version=2, run_id=run_id, kind='ended',
                ts=int(time.time()), exit_code=CRASH,
                exit_origin='supervisor', outcome=failed))
        finally:
            try:
                release_owner_lease()
            finally:
                registry.release(key, run_id)
        raise
    return next(r for r in records(base, registry=registry)
                if r.run_id == run_id)


def wait(run_id: str, timeout: float | None = None) -> bool:
    """等 worker 线程收尾（同 `jobs.wait`）。**只用于测试与短等待**。"""
    with _THREADS_LOCK:
        thread = _THREADS.get(run_id)
    if thread is None:
        return True
    thread.join(timeout)
    return not thread.is_alive()


def request_stop(base: str | os.PathLike[str], run_id: str) -> AgentRunRecord:
    """异步请求 Supervisor 熄火；只确认请求已交给当前 owner。

    公开 HTTP 不能用一个很短的请求超时冒充进程树已经 drain。这里先关闭
    admission、触发精确 cancel，再返回当前 durable 投影；最终 ``ended`` 仍只由
    worker 在 claims / beacons 收净后写入。
    """
    with _SUPERVISORS_LOCK:
        supervisor = _SUPERVISORS.get(run_id)
    if supervisor is None:
        raise JobConflict(
            f'run_id={run_id!r} 没有本进程拥有的 Supervisor（已结束，'
            '或服务重启后句柄已丢）')
    supervisor.request_stop()
    rec = next((r for r in records(base) if r.run_id == run_id), None)
    if rec is None:
        raise UpstreamMissing(f'没有 run_id={run_id!r} 的 agent 代跑记录')
    return rec


def stop(base: str | os.PathLike[str], run_id: str) -> AgentRunRecord:
    """请求 Supervisor 熄火并短等终态；仅供测试与同步诊断入口。"""
    request_stop(base, run_id)
    if not wait(run_id, 10):
        raise JobConflict(
            f'run_id={run_id!r} 已发 stop，但 worker 尚未完成 drain/终态落账')
    rec = next((r for r in records(base) if r.run_id == run_id), None)
    if rec is None:
        raise UpstreamMissing(f'没有 run_id={run_id!r} 的 agent 代跑记录')
    return rec


def request_pause(base: str | os.PathLike[str], run_id: str) -> AgentRunRecord:
    """异步请求协作式暂停；不取消已取得 claim 的付费节点。"""
    with _SUPERVISORS_LOCK:
        supervisor = _SUPERVISORS.get(run_id)
    if supervisor is None:
        raise JobConflict(
            f'run_id={run_id!r} 没有本进程拥有的 Supervisor；'
            '服务重启后的 unknown 不能假装可 pause')
    supervisor.request_pause()
    rec = next((item for item in records(base)
                if item.run_id == run_id), None)
    if rec is None:
        raise UpstreamMissing(f'没有 run_id={run_id!r} 的 agent 代跑记录')
    return rec


def pause(base: str | os.PathLike[str], run_id: str,
          *, timeout: float = 10.0) -> AgentRunRecord:
    """协作式暂停并短等 paused；仅供测试与同步诊断入口。"""
    request_pause(base, run_id)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rec = next((item for item in records(base)
                    if item.run_id == run_id), None)
        if rec is None:
            raise UpstreamMissing(f'没有 run_id={run_id!r} 的 agent 代跑记录')
        if rec.state == 'paused' or rec.ended is not None:
            return rec
        time.sleep(0.05)
    raise JobConflict(
        f'run_id={run_id!r} 已请求 pause，但当前 turn 尚未完成 drain')


def resume(base: str | os.PathLike[str], run_id: str) -> AgentRunRecord:
    """恢复本进程仍持有书锁的 paused run；重启后的 unknown 不自动认领。"""
    with _SUPERVISORS_LOCK:
        supervisor = _SUPERVISORS.get(run_id)
    if supervisor is None:
        raise JobConflict(
            f'run_id={run_id!r} 没有本进程拥有的 Supervisor；'
            '服务重启后的 unknown 只能先人工核实')
    supervisor.request_resume()
    rec = next((item for item in records(base)
                if item.run_id == run_id), None)
    if rec is None:
        raise UpstreamMissing(f'没有 run_id={run_id!r} 的 agent 代跑记录')
    return rec
