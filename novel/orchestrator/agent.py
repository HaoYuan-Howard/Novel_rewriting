#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent.py —— 前端一键唤起供应商中立 **Commander** 代跑一本书。

    Output/_service/agent_runs.jsonl        append-only 事件流（生死与改判）
    Output/_service/logs/agent-<run_id>.log agent 进程的 stdout+stderr，按字节追加

【它为什么不是 `jobs.jsonl` 的一种新作业】`JobStart` 的后置条件钉死
`node ∈ NODES`，而 agent 代跑**不是流水线节点**——它是「拉起一个会自己去跑
节点的进程」。往 `NODES` 塞一个假节点，污染的是完成度/贵贱/写盘效应的
全部派生源头。所以它有自己的事件文件，形状照抄 `jobs.py`（同一条
append-only 理由：永不读-改-写，被 kill 最坏只损坏最后一行）。

【供应商边界】本模块只做整书编排与事件账；Claude/Codex 的 argv、stdin、协议解析
与进程树取消全部在 ``novel.commanders``。prompt 永远只走 stdin，事件里的 argv
因此可以安全展示和写日志，不会把整份任务原文重复泄进进程日志。

【冻结边界】preview / 单本 start / batch 入队共用 ``plan``。profile 在那里关闭式
校验并深拷贝；排队项携带整份 plan，出队不再读取默认或重新解释请求。当前服务没有
默认 profile，``profiles=None`` 是显式终态，不拿 capability 第一项替用户选择。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import warnings
from collections.abc import Callable, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, BinaryIO, Iterator, NamedTuple

from contracts.artifacts import (
    STAGE2_GLOBAL_EDIT, STAGE2_GLOBAL_EDIT_COMMIT,
    STAGE2_GLOBAL_EDIT_FAILURE, STAGE2_GLOBAL_REVIEW_DECISIONS,
    STAGE2_BLUEPRINT_EDIT,
    STAGE5_REVIEWER_REWRITE_REQUEST,
)
from contracts.invariants import (
    AGENT_EXECUTOR_NODES, COMMANDER_MAX_ATTEMPTS_PER_SEAT,
    COMMANDER_RETRY_BACKOFF_SECONDS,
    STAGE2_BLUEPRINT_MAX_CONCURRENCY,
    commander_work_unit,
)
from contracts.schema import decode_artifact
from contracts.schema.api import (
    BatchTrimResolutionRequest, ExecutionPermit, OperatorNext,
    Stage2FinalReviewRequest, Stage5ReviewerRewriteRequest,
)
from contracts.schema.evaluation import GlobalReviewDecisionSet
from contracts.schema.plot_edit import PlotEdit
from contracts.schema.stage2 import GlobalEdit, GlobalEditCommit, GlobalEditFailure
from contracts.schema.orchestration import (
    AgentDeliveryOutcome, AgentPending, AgentQueueView, AgentRejected,
    AgentRunEvent, AgentRunExitOrigin,
    AgentExecutorBackend, AgentProfile, AgentRunRecord, AgentRunStart, RunContinuationSource,
    CommanderSessionPointer, ExecutionEpoch, ExecutorBackend,
    PipelineAgentProfiles, ReviewerActionRecord, RunContractV2,
    ReviewerSchemaRecoveryProof, ReviewerSeatLedger, ReviewerSeatTurn,
)
from novel import agent_session_audit, paths, prov, run_identity
from novel.agent_capabilities import (
    AgentProfileRefused, provider_for_driver, reported_model_matches, validate_pipeline_profiles,
)
from novel.appendlog import (
    append_line, append_line_under_process_lock, append_transaction,
)
from novel.cli.exit_codes import CRASH, landed
from novel.commander_context import (
    COMMANDER_ADMISSION_ID_ENV, COMMANDER_CONTRACT_SHA256_ENV,
    COMMANDER_EPOCH_ID_ENV,
    COMMANDER_EPOCH_SHA256_ENV, COMMANDER_RUN_ID_ENV, env_with_context,
    action_admission_id_belongs_to_epoch,
)
from novel.commanders import (
    CommanderConfigurationError, CommanderDriver, CommanderInvocation,
    CommanderObservation, CommanderProcessResult, create_commander_driver,
)
from novel.commanders.base import _prepare_output_schema
from novel.config import load_config
from novel.agents.base import _signal_process_tree
from novel.errors import (
    ApprovalRequired, NovelError, TerminalState, UnsupportedRunMode,
    UpstreamMissing,
)
from novel.llm.ledger import (
    append_commander_call, ledger_path as cost_ledger_path, read_jsonl_rows,
)
from novel.model_slots import (
    model_slot_from_environment, prepare_model_slot_gate,
    pipeline_model_slot, MODEL_BOOK_DIR_ENV,
)
from novel.orchestrator import (
    admission, commander_completion, execution_permit, jobs, operator, queue,
    review_seats, reviewer_action_evidence, reviewer_seat_ledger,
    reviewer_protocol_recovery, reviewer_schema_recovery, reviewer_revalidation,
    reviewer_tasks, service_lifecycle,
    stage2_reviewer_wire, stage4_reviewer_wire, state, uploads,
)
from novel.orchestrator.agent_supervisor import (
    CommanderTurnResult, LaunchTicket, PrelaunchIdentityDrift, RunSupervisor,
    SupervisorActionResult,
)
from novel.orchestrator.jobs import JobConflict, JobRegistry, service_dir
from novel.orchestrator.nodes import shell_line
from novel.store import ArtifactStore
from novel.work_units import (
    COMMANDER_FAILURE_RECONCILED, WorkUnitBudget, WorkUnitRefused, ContentReservation,
    require_workflow, validate_reviewer_record, commander_budget_for_record,
)

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
#: ⚠ 为什么必须有闸：一本整书代跑会保持一个 Commander 会话，
#: 各付费节点又会创建各自隔离的 executor 会话；两层都可能产生外部模型费用。
#: 没有闸的话，界面上勾 10 本点一下就是 10 份同时烧，且 API 配额是共享的。
#: ⇒ 超出上限的不报错、进队列，由结束的那个 agent 把下一个带起来（`_drain`）。
#: ⚠ 它与锁是**两件事**：锁保证「同一本书不被两个 agent 同时写」，
#: 闸保证「同时烧的钱不超过 N 份」。把两者合成一个数字就会得到
#: 「想并发就得放弃同书互斥」的假选择。
MAX_CONCURRENT = 3

# 顶层 Claude/Codex CLI 返回后，300 秒只是**报警与尝试 cancel 的阈值**，不是
# 释放锁的期限。admission claim 或 beacon execution 任一未闭合时都必须继续持有
# 书锁/并发槽；否则迟到写盘会与下一位写手并发。最终闭合后若曾越线，交付仍记
# unknown，不把“后来终于结束”改写成“生命周期始终可证”。
_EXECUTION_CLOSE_TIMEOUT_SECONDS = 300.0
_EXECUTION_CLOSE_POLL_SECONDS = 0.25
_ACTION_FAILURE_TAIL_BYTES = 16 * 1024
_ACTION_FAILURE_DETAIL_CHARS = 4096

_THREADS_LOCK = threading.Lock()
_THREADS: dict[str, threading.Thread] = {}
_COMMANDERS_LOCK = threading.Lock()
_COMMANDERS: dict[str, CommanderDriver] = {}
_COMMANDER_SLOT_CANCEL: dict[str, threading.Event] = {}
#: 每个 run 当前 Commander turn 的协议事件汇：driver 首个事件里的 provider
#: session id 经它在线落盘（``CommanderSessionPointer``）。
_COMMANDER_EVENT_SINKS: dict[str, Callable[[object, str | None], None]] = {}
#: 同席重试前的退避；测试替换为零等待。
_retry_sleep: Callable[[float], None] = time.sleep
#: 同席 resume 的续接提示：上一进程已因网络/瞬态故障终止，续接同一 provider
#: 会话完成同一 immutable workpack；不重发工作包，不允许换请求文件路径。
_REVIEWER_RESUME_PROMPT = (
    '上一进程因网络或瞬态故障中断，本轮是同一席位的续接。请继续同一 immutable '
    'workpack 与同一 typed 请求文件路径完成审阅并写出请求；不要重读盘上产物，'
    '不要更改请求文件路径，也不要重复已经写出的请求。'
)


def _write_session_pointer(
        base: str, sid: str, pointer: CommanderSessionPointer) -> None:
    path = paths.commander_session_pointer_path(base, sid, pointer.session_run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (pointer.model_dump_json(indent=2) + '\n').encode('utf-8')
    temporary = path.with_suffix(f'.{os.getpid()}.{threading.get_ident()}.tmp')
    temporary.write_bytes(payload)
    os.replace(temporary, path)
_ACTIONS_LOCK = threading.Lock()
_ACTIONS: dict[str, subprocess.Popen[bytes]] = {}
_SUPERVISORS_LOCK = threading.Lock()
_SUPERVISORS: dict[str, RunSupervisor] = {}

#: `R-11.11` 在这边的样子：服务重启后，agent 子进程是死是活我们真的不知道。
RESTART_NOTE = ('服务重启前它是死是活我们不知道——'
                '产物与信标都在盘上，照 `state` 与进度屏核实后再决定要不要重拉')


# ─────────────────────────────────────────────────────────────────────────────
# 一、提示词与 Commander plan（纯函数；preview 与真跑共用——`R-11.8` 同构）
# ─────────────────────────────────────────────────────────────────────────────

def _agent_egress_routes(profiles: PipelineAgentProfiles) -> str:
    """展开本次真正可达的第三方路由；被全部 override 的默认项不得扩入授权。"""
    commander = profiles.commander
    lines = [
        '- role=`commander`：第三方接收方 '
        f'provider=`{provider_for_driver(commander.driver)}`；'
        f'driver=`{commander.driver}`；model=`{commander.model}`；'
        f'reasoning_effort=`{commander.reasoning_effort}`；'
        f'execution_mode=`{commander.execution_mode}`',
    ]
    grouped: dict[tuple[str, str, str, str, str, str | None], list[str]] = {}
    for node in AGENT_EXECUTOR_NODES:
        backend = profiles.executor_overrides.get(node, profiles.executor_default)
        if isinstance(backend, AgentExecutorBackend):
            profile = backend.profile
            key = (
                'agent', provider_for_driver(profile.driver), profile.driver,
                profile.model, profile.reasoning_effort, profile.execution_mode,
            )
        else:
            profile = backend.profile
            key = (
                'legacy', profile.provider, '-', profile.model,
                profile.reasoning_effort, profile.thinking_mode,
            )
        grouped.setdefault(key, []).append(node)
    for (kind, provider, driver, model, effort, mode), nodes in grouped.items():
        tail = (
            f'driver=`{driver}`；execution_mode=`{mode}`'
            if kind == 'agent'
            else f'thinking_mode=`{mode}`'
        )
        lines.append(
            f'- role=`executor`，kind=`{kind}`，nodes=`{",".join(nodes)}`：'
            f'第三方接收方 provider=`{provider}`；model=`{model}`；'
            f'reasoning_effort=`{effort}`；{tail}')
    return '\n'.join(lines)


def _backend_label(backend: ExecutorBackend) -> str:
    if isinstance(backend, AgentExecutorBackend):
        profile = backend.profile
        return (
            f'agent:{profile.driver}/{profile.model}/'
            f'{profile.reasoning_effort}/{profile.execution_mode}')
    profile = backend.profile
    return (
        f'legacy:{profile.provider}/{profile.model}/'
        f'{profile.reasoning_effort}/{profile.thinking_mode}')


def build_prompt(sid: str, base: str | os.PathLike[str], *,
                 authorized: bool, egress_authorized: bool,
                 profiles: PipelineAgentProfiles,
                 code_version: str | None = None) -> str:
    """发给 agent 的提示词。两项授权原文都在这里，布尔字段只是机读摘要。

    核心构造函数故意不设默认值：API/UI 可以提供产品默认，但到达 prompt 边界时
    两项事实必须都已经物化，不能让一个布尔值静默扩大成另一项授权。

    """
    base_abs = os.path.abspath(os.fspath(base))
    code_version = code_version or prov.code_version()
    py = Path(sys.executable).as_posix()
    novel_dir = paths.find_novel_dir(base_abs, sid)
    #: ⚠ 成书名**按书名派生**，不是固定的 `book.txt`——唯一落点是 `paths.book_txt_rel`。
    #: 这一句此前硬编码了一个不存在的文件名：运行入口必须按同一规则派生，
    #: 但 headless 下没有第二个人能纠正启动 prompt，而它是任务的第一句话。
    book_target = (
        paths.book_txt_rel(novel_dir) if novel_dir is not None
        else 'stage7/<书名>_rewriting.txt（摄入后按书目录派生）')
    payment_authorization = (
        '**付费授权已授予**：本会话允许产生外部模型调用费用，工作流契约中的付费命令逐条直接执行、'
        '不再询问；节点、用量与判据读数由 Supervisor 和权威账自动留痕。'
        if authorized else
        '**付费授权未授予**：不得执行会产生外部模型调用费用的命令；在第一条付费命令之前停下，'
        '但仍须完成只读的 state、inbox 与下一节点 preview；停止原因与恢复坐标由 Supervisor '
        '写入权威账，然后结束。')
    egress_routes = _agent_egress_routes(profiles)
    egress_authorization = (
        '**正文外发授权已授予（具体第三方目的地已冻结）**：本次 launcher 依据同一启动请求中'
        '冻结的 `egress_authorized=true` 与 profiles，允许把小说流水线所必需的小说正文及其派生'
        '上下文仅发送到以下具体第三方接收方：\n'
        f'{egress_routes}\n'
        '授权仅覆盖上列 frozen commander 与 node→executor 路由，不覆盖任何其他第三方 provider、'
        'driver、model、站点或接收方；started contract 与 execution permit 仍须逐项复核。'
        '不得把 API key、token、cookie 或其他凭据内容放入模型输入、命令参数、日志或产物；'
        'CLI 只能按自身认证机制使用凭据。这项授权不代表已经授予付费授权。'
        if egress_authorized else
        '**正文外发授权未授予**：没有任何 provider、driver、model、站点或接收方获得正文外发授权。'
        '以下冻结路由只供 0 成本 preview 与身份核对，不构成授权：\n'
        f'{egress_routes}\n'
        '不得把小说正文或其派生上下文发送给外部模型；'
        '即使付费授权已经授予，也只能先完成只读的 state、inbox 与下一节点 preview，'
        '并在首个需要正文外发的步骤前停下。')
    commander = profiles.commander
    executor = profiles.executor_default
    overrides = ', '.join(
        f'{node}={_backend_label(backend)}'
        for node, backend in sorted(profiles.executor_overrides.items())) or '无'
    return f"""你是小说工作流的 Commander/Reviewer，不是调度器。仓根是
`{REPO_ROOT.as_posix()}`，审阅对象属于 sid={sid}、base=`{base_abs}`。

确定性 RunSupervisor 独占状态机、execution token、节点调度、Executor 调度、遥测与交付终验；
你只会在一个已登记的 reviewer blocker 上被唤醒。每一轮实际提示都会内嵌同一时点冻结的
immutable workpack 与唯一 typed request 落点。先逐项审阅，再做一次跨项复核，最后只提交
一份 typed 判断。不得启动任何小说节点或 Executor，不得修改正式产物、服务账或 Markdown，
不得从目录存在性、旧对话或退出码猜测 PASS；schema 提供 UNVER 时保持 UNVER，
否则只走当前 blocker 明示的关闭式出口，read-only 或材料不足不得制造付费动作。

本次 parent contract 的审计起点：code_version=`{code_version}`；成书目标=`{book_target}`。
该目标仅供识别对象，不授予写权限。

付费授权：{payment_authorization}
正文外发授权：{egress_authorization}

冻结角色：
- Commander：{commander.driver}/{commander.model}/{commander.reasoning_effort}/{commander.execution_mode}
- 默认 Executor：{_backend_label(executor)}
- Executor 节点覆盖：{overrides}

受托 reviewer 轮次只以随轮提示下发的 reviewer 契约与 workpack 明示的 review contract 为准；
若两者与当前 typed schema 冲突，关闭式拒绝并让本轮结束，不自行发明兼容写法。"""


def _freeze_profiles(profiles: PipelineAgentProfiles | None,
                     ) -> PipelineAgentProfiles:
    """关闭式解析并深复制；``FrozenModel`` 本身不会冻结嵌套 dict。"""
    if profiles is None:
        raise UnsupportedRunMode(
            '服务端尚未设置默认 agent profiles；请显式选择 Commander 与 executor')
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


def _validate_invocation(
        invocation: CommanderInvocation, *, prompt: str,
        profile: AgentProfile,
        expected_cwd: str | os.PathLike[str] = REPO_ROOT) -> None:
    """planner 与启动边界共用的 stdin-only 后置条件。"""
    if invocation.profile != profile:
        raise UnsupportedRunMode(
            'Commander invocation.profile 与冻结 commander profile 不一致')
    if invocation.stdin_text != prompt:
        raise UnsupportedRunMode('Commander invocation.stdin 与 preview prompt 不一致')
    if any(prompt in item for item in invocation.argv):
        raise UnsupportedRunMode('Commander prompt 只允许走 stdin，不得进入 argv')
    if any(prompt in value for value in invocation.env_overrides.values()):
        raise UnsupportedRunMode('Commander prompt 只允许走 stdin，不得进入 env')
    if (os.path.normcase(os.path.abspath(invocation.cwd))
            != os.path.normcase(os.path.abspath(expected_cwd))):
        raise UnsupportedRunMode(
            f'Commander cwd 必须冻结在 {os.path.abspath(expected_cwd)}')


@dataclass(frozen=True)
class AgentRunPlan:
    """一次 preview / start 共用的不可变 Commander 启动计划。"""

    base: str
    sid: str
    authorized: bool
    egress_authorized: bool
    code_version: str
    profiles: PipelineAgentProfiles
    prompt: str
    invocation: CommanderInvocation
    continuation: RunContinuationSource | None = None


def _plan_resolved(
        base: str | os.PathLike[str], sid: str, *, authorized: bool,
        egress_authorized: bool, profiles: PipelineAgentProfiles,
        code_version: str | None = None) -> AgentRunPlan:
    base_abs = os.path.abspath(os.fspath(base))
    frozen = _copy_profiles(profiles)
    code = code_version or prov.code_version()
    prompt = build_prompt(
        sid, base_abs, authorized=authorized,
        egress_authorized=egress_authorized, profiles=frozen,
        code_version=code)
    driver: CommanderDriver | None = None
    try:
        driver = create_commander_driver(frozen.commander)
        invocation = driver.build_invocation(
            frozen.commander, prompt, repo=REPO_ROOT, base=base_abs)
        _validate_invocation(
            invocation, prompt=prompt, profile=frozen.commander)
    except (AgentProfileRefused, CommanderConfigurationError) as exc:
        raise UnsupportedRunMode(str(exc)) from exc
    finally:
        if driver is not None:
            driver.close()
    return AgentRunPlan(
        base=base_abs, sid=sid, authorized=authorized,
        egress_authorized=egress_authorized, code_version=code,
        profiles=frozen, prompt=prompt, invocation=invocation)


def plan(
        base: str | os.PathLike[str], sid: str, *, authorized: bool,
        egress_authorized: bool,
        profiles: PipelineAgentProfiles | None,
        continuation: RunContinuationSource | None = None) -> AgentRunPlan:
    """preview、单本启动与批量入队的唯一 planner；不发模型请求、不写盘。"""
    if continuation is not None:
        from novel.orchestrator import agent_continuation
        profiles = agent_continuation.validate(base, sid, continuation, profiles)
    planned = _plan_resolved(
        base, sid, authorized=authorized,
        egress_authorized=egress_authorized,
        profiles=_freeze_profiles(profiles))
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
            out.append(AgentRunEvent.model_validate(data))
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

Spawn = Callable[
    [CommanderInvocation, dict[str, str], BinaryIO], CommanderProcessResult,
]


def _model_runtime_env(base: Path, sid: str, profiles: PipelineAgentProfiles) -> dict[str, str]:
    """实际子执行只用本次 plan 冻结的并发配置，不继承宿主的隐式提速参数。"""
    from contracts.schema.orchestration import PipelineConcurrency
    from novel.config import DEFAULT_CONCURRENCY, ENV_KEYS
    policy = profiles.concurrency or PipelineConcurrency()
    return {ENV_KEYS['concurrency_policy']: policy.model_dump_json(),
            ENV_KEYS['concurrency']: str(DEFAULT_CONCURRENCY),
            MODEL_BOOK_DIR_ENV: str(paths.resolve_novel_dir(base, sid))}


def _default_spawn(
        run_id: str, *, on_registered: Callable[[], None] | None = None) -> Spawn:
    """由选定 driver 拉起一棵 Commander 进程树，并只登记这一个 owner。"""

    def go(invocation: CommanderInvocation, env: dict[str, str],
           log: BinaryIO) -> CommanderProcessResult:
        # 宿主全环境先铺底，driver 冻结的生命周期键再覆盖；最后只把 launcher
        # 从已 append AgentRunStart 铸出的三枚 contract 指针放回最高优先级。
        # 反过来 ``invocation → env`` 会让宿主同名变量悄悄解冻 Claude 后台任务。
        runtime_env = dict(env)
        runtime_env.update(invocation.env_overrides)
        for key in (
                run_identity.RUN_ID_ENV,
                COMMANDER_RUN_ID_ENV,
                COMMANDER_CONTRACT_SHA256_ENV,
                COMMANDER_EPOCH_ID_ENV,
                COMMANDER_EPOCH_SHA256_ENV,
                COMMANDER_ADMISSION_ID_ENV, MODEL_BOOK_DIR_ENV,
                'NOVEL_CONCURRENCY_POLICY', 'NOVEL_CONCURRENCY'):
            if key not in env:
                continue
            runtime_env[key] = env[key]
        # ``CommanderInvocation.output_schema`` 顶层虽已冻结，内部 JSON 容器仍是
        # Python 可变对象。运行时重建只能从构造期规范字节恢复，不能重新信任
        # 调用方曾传入的 mapping；否则 argv 仍带 --output-schema、driver 却看不见
        # 同一份 schema，或审计身份与真正 wire 分叉。
        runtime_output_schema = (
            None if invocation.output_schema_bytes is None
            else json.loads(invocation.output_schema_bytes))
        runtime_invocation = CommanderInvocation(
            argv=invocation.argv,
            stdin_text=invocation.stdin_text,
            cwd=invocation.cwd,
            env_overrides=runtime_env,
            profile=invocation.profile,
            protocol=invocation.protocol,
            cli_version=invocation.cli_version,
            resume_provider_session_id=(
                invocation.resume_provider_session_id),
            structured_response=invocation.structured_response,
            output_schema=runtime_output_schema,
            expected_output_schema_sha256=(
                invocation.expected_output_schema_sha256),
        )
        driver = create_commander_driver(runtime_invocation.profile)
        slot_cancel = threading.Event()
        with _COMMANDERS_LOCK:
            _COMMANDERS[run_id] = driver
            _COMMANDER_SLOT_CANCEL[run_id] = slot_cancel
            event_sink = _COMMANDER_EVENT_SINKS.get(run_id)
        if event_sink is not None:
            event_setter = getattr(driver, 'set_event_callback', None)
            identify = getattr(driver, 'session_id_of_event', None)
            if event_setter is not None:
                event_setter(lambda event: event_sink(
                    event, None if identify is None else identify(event)))
        if on_registered is not None:
            setter = getattr(driver, 'set_launch_callback', None)
            if setter is None:
                raise CommanderConfigurationError(
                    'Commander driver 不支持 Popen 后 launch ticket')
            setter(on_registered)
        # owner 故意不在顶层 invocation 返回时移除：worker 接下来还要 drain
        # close 前 claim / beacon。300 秒阈值与人工 stop 都应尽可能仍能向这棵
        # driver 进程树发 cancel。真正 close 在 durable drain 闭合之后统一完成。
        # Commander 与同根 Executor 共用整书、供应商总槽；取消等待不启动进程。
        from novel.config import load_config
        policy = load_config(env=runtime_env).concurrency_policy
        book_dir = runtime_env.get(MODEL_BOOK_DIR_ENV)
        total_slot = (pipeline_model_slot(Path(book_dir), driver.provider,
            per_book=policy.per_book, per_provider=policy.per_provider,
            cancelled=slot_cancel.is_set) if book_dir else nullcontext())
        with model_slot_from_environment(runtime_env, cancelled=slot_cancel.is_set), total_slot:
            return driver.run(runtime_invocation, log=log, run_id=run_id)

    return go


def _close_commander_owner(run_id: str) -> None:
    """drain 闭合后才归还 driver owner；custom spawn 没有 owner。"""
    with _COMMANDERS_LOCK:
        driver = _COMMANDERS.pop(run_id, None)
        slot_cancel = _COMMANDER_SLOT_CANCEL.pop(run_id, None)
        if slot_cancel is not None:
            slot_cancel.set()
    if driver is not None:
        driver.close()


def _cancel_commander_owner(run_id: str) -> str:
    """超时只尝试 cancel，不据返回值猜 claims/executions 已结束。"""
    with _COMMANDERS_LOCK:
        driver = _COMMANDERS.get(run_id)
        slot_cancel = _COMMANDER_SLOT_CANCEL.get(run_id)
        if slot_cancel is not None:
            slot_cancel.set()
    if driver is None:
        return '没有可用 Commander driver owner（custom spawn 或句柄已丢）'
    try:
        confirmed = driver.cancel(run_id)
    except Exception as exc:  # noqa: BLE001 —— 只作报警证据，drain 仍继续
        return f'Commander cancel 异常：{type(exc).__name__}: {exc}'
    return ('Commander driver 已确认 cancel 请求'
            if confirmed else 'Commander driver 未确认 cancel 请求')


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


@contextmanager
def _stage2_reviewer_input(
        base: str, sid: str, action: reviewer_tasks.ReviewerNodeAction,
) -> Iterator[Path]:
    """把 Commander 的一次性 Stage2 typed 输入放进 producer 认可的服务根。

    scratch 只承载本 epoch 的 prompt、request 与 execution token；
    ``stage2.global --review-decisions`` 的生产路径契约则只接受当前书或
    ``_service/edits/<sid>``。这里复用 HTTP 同一个 typed 保存口，避免 permit
    放行后又在 producer 处因路径口径不同而失败。
    """
    payload = action.input_payload
    if payload is None:  # pragma: no cover —— 调用方只在有 payload 时进入
        raise RuntimeError('Stage2 reviewer input 缺 typed payload')
    tokens = set(action.flags)
    if '--review-decisions' in tokens and '--apply-edit' not in tokens:
        decoded = decode_artifact(STAGE2_GLOBAL_REVIEW_DECISIONS, payload)
        decisions = GlobalReviewDecisionSet.model_validate(decoded.payload)
        path = uploads.save_global_review_decisions(base, sid, decisions)
    elif '--apply-edit' in tokens and '--review-decisions' not in tokens:
        if action.node == 'stage2.blueprint':
            decoded = decode_artifact(STAGE2_BLUEPRINT_EDIT, payload)
            edit = PlotEdit.model_validate(decoded.payload)
            path = uploads.save_blueprint_edit(base, sid, edit)
        else:
            decoded = decode_artifact(STAGE2_GLOBAL_EDIT, payload)
            edit = GlobalEdit.model_validate(decoded.payload)
            path = uploads.save_global_edit(base, sid, edit)
    else:
        raise RuntimeError(
            'Stage2 reviewer input 必须恰好属于 review-decisions 或 apply-edit')
    try:
        yield path
    finally:
        try:
            uploads.discard_edit_plan(base, path)
        except NovelError as exc:
            warnings.warn(
                f'reviewer 节点已结束，但一次性 Stage2 typed 输入清理失败：{exc}',
                RuntimeWarning, stacklevel=2)


@contextmanager
def _stage5_trim_reviewer_input(
        base: str, sid: str, action: reviewer_tasks.ReviewerNodeAction,
) -> Iterator[Path]:
    """把 Commander 已逐项填写的 batch manifest 放进登记的服务编辑根。"""
    payload = action.input_payload
    if payload is None:  # pragma: no cover —— 调用方只在有 payload 时进入
        raise RuntimeError('Stage5 batch trim reviewer input 缺 typed payload')
    request = BatchTrimResolutionRequest.model_validate(payload)
    path = uploads.save_stage5_trim_batch(base, sid, request)
    try:
        yield path
    finally:
        try:
            uploads.discard_edit_plan(base, path)
        except NovelError as exc:
            warnings.warn(
                f'reviewer 节点已结束，但一次性 Stage5 batch manifest 清理失败：{exc}',
                RuntimeWarning, stacklevel=2)


@contextmanager
def _stage5_reviewer_rewrite_input(
        base: str, sid: str, action: reviewer_tasks.ReviewerNodeAction,
        *, reviewer_action_record_path: Path | None,
) -> Iterator[Path]:
    """把 Commander 决定与已验证 durable 身份合成 producer 受信输入。"""
    payload = action.input_payload
    if payload is None:  # pragma: no cover —— 调用方只在有 payload 时进入
        raise RuntimeError('Stage5 reviewer rewrite 缺 typed payload')
    if reviewer_action_record_path is None:
        raise RuntimeError('Stage5 reviewer rewrite 缺 durable action record')
    request = Stage5ReviewerRewriteRequest.model_validate(payload)
    target = reviewer_action_record_path.resolve()
    matched = [
        record for path, record in uploads.load_reviewer_actions(base, sid)
        if path == target
    ]
    if len(matched) != 1:
        raise RuntimeError(
            'Stage5 reviewer rewrite durable action record 不唯一或已缺失')
    record = matched[0]
    if (
        record.node != action.node
        or record.flags != action.flags
        or record.input_payload != request.model_dump(mode='json')
        or record.stage5_reviewer_contract_sha256 is None
        or record.reviewer_session_run_id is None
        or record.reviewer_seat_turn_id is None
        or record.provider_response_sha256 is None
    ):
        raise RuntimeError(
            'Stage5 reviewer rewrite durable action 与待执行 typed payload/席位不闭合')
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise RuntimeError(
            f'Stage5 reviewer rewrite durable action record 不可读：{exc!r}') from exc
    record_sha256 = hashlib.sha256(raw).hexdigest()
    if target.name != f'reviewer-action-{record_sha256}.json':
        raise RuntimeError(
            'Stage5 reviewer rewrite durable action record 内容地址不闭合')
    path = uploads.save_stage5_reviewer_rewrite_input(
        base, sid, request,
        reviewer_action_record_sha256=record_sha256,
        business_sha256=record.business_sha256,
        reviewer_contract_sha256=record.stage5_reviewer_contract_sha256,
        reviewer_session_run_id=record.reviewer_session_run_id,
        reviewer_seat_turn_id=record.reviewer_seat_turn_id,
        provider_response_sha256=record.provider_response_sha256,
    )
    try:
        yield path
    finally:
        try:
            uploads.discard_edit_plan(base, path)
        except NovelError as exc:
            warnings.warn(
                f'reviewer 节点已结束，但一次性 Stage5 rewrite request 清理失败：{exc}',
                RuntimeWarning, stacklevel=2)


@contextmanager
def _reviewer_node_flags(
        base: str, sid: str, action: reviewer_tasks.ReviewerNodeAction,
        *, reviewer_action_record_path: Path | None = None,
) -> Iterator[list[str]]:
    """把可信 typed payload 物化成 permit 与真实 CLI 共用的 exact flags。"""
    flags = list(action.flags)
    if action.input_payload is None:
        yield flags
        return
    if action.node == 'stage2.global':
        input_context = _stage2_reviewer_input(base, sid, action)
    elif (
        action.node == 'stage5.review'
        and action.flags == ('--retain-pretrim-batch', '@reviewer-input')
    ):
        input_context = _stage5_trim_reviewer_input(base, sid, action)
    elif (
        action.node == 'stage5.review'
        and action.flags == (
            '--request-reviewer-rewrite', '@reviewer-input')
    ):
        input_context = _stage5_reviewer_rewrite_input(
            base, sid, action,
            reviewer_action_record_path=reviewer_action_record_path)
    else:
        raise RuntimeError(f'{action.node} 尚未登记 reviewer 外部输入物化入口')
    with input_context as payload_path:
        materialized = [
            str(payload_path) if value == '@reviewer-input' else value
            for value in flags
        ]
        if materialized == flags:
            raise RuntimeError('reviewer input_payload 缺 @reviewer-input flag 占位符')
        yield materialized


def _execute_reviewer_node_action(
        base: str, sid: str, action: reviewer_tasks.ReviewerNodeAction,
        run_token: Callable[[ExecutionPermit], SupervisorActionResult],
        *, reviewer_action_record_path: Path | None = None,
) -> SupervisorActionResult:
    """在一次性输入仍存活时铸 permit 并同步跑完整 canonical runner。"""
    with _reviewer_node_flags(
            base, sid, action,
            reviewer_action_record_path=reviewer_action_record_path) as flags:
        permit = operator.permit(base, sid, action.node, flags)
        return run_token(permit)


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


def _reviewer_approval_argv(
        base: str, sid: str, epoch: ExecutionEpoch,
        action: reviewer_tasks.ReviewerApprovalAction) -> list[str]:
    """给同一 surface 的每轮 typed 决定保留独立输入，再构造公开 CLI argv。"""
    scratch = service_lifecycle.open_scratch_session(
        base, sid, epoch.epoch_id)
    payload = (json.dumps(
        action.payload, ensure_ascii=False, indent=2,
        sort_keys=True) + '\n').encode('utf-8')
    payload_path = _write_identity_bound_scratch(
        scratch, stem=f'reviewer-{action.surface}', kind='input',
        identity_sha256=hashlib.sha256(payload).hexdigest(), payload=payload)
    tail = [
        'approval', 'apply', action.surface, sid, '--base', base,
        '--input', str(payload_path)]
    return [sys.executable, '-B', '-m', 'novel.cli', *tail]


@dataclass
class _ReviewerSeat:
    """非 Stage2 reviewer blocker 的一席：同一 binding 内首审与纠错共用的 logical session。"""

    session_run_id: str
    prompt_sha256: str
    waiting_on: str
    business_sha256: str
    output_schema_sha256: str | None = None
    work_unit_scope: str | None = None
    provider_session_id: str | None = None
    turns_recorded: int = 0
    started: bool = False
    last_observation: CommanderObservation | None = None
    last_rc: int = CRASH
    last_outcome: AgentDeliveryOutcome | None = None
    last_error_kind: str | None = None


@dataclass(frozen=True)
class _ReplayableReviewerAction:
    """盘上一份仍适用于当前 blocker、且已重新通过 loader 的 approval 决定。"""

    path: Path
    action: reviewer_tasks.ReviewerAction
    record: ReviewerActionRecord


def _reviewer_activity(task: reviewer_tasks.ReviewerTask) -> str:
    """给成本账一枚人读分段；生产 node 身份仍保持 ``None``。"""
    option_ids = {
        str(option.get('id')) for option in task.submission_options
        if isinstance(option, dict)
    }
    if task.waiting_on in queue.STAGE2_REVIEWER_WAITING_ON:
        node, _scope = commander_work_unit(task.waiting_on)
        suffix = 'review-or-edit' if any('apply-edit' in value for value in option_ids) else 'review'
        return f'{node}.{suffix}'
    return f'reviewer:{task.waiting_on}'


def _record_reviewer_action(
        base: str, sid: str, *, run_id: str, epoch: ExecutionEpoch,
        task: reviewer_tasks.ReviewerTask,
        action: reviewer_tasks.ReviewerAction,
        reviewer_session_run_id: str | None = None,
        reviewer_seat_turn_id: str | None = None,
        provider_response_sha256: str | None = None,
        work_unit_id: str | None = None,
        work_unit_turn_id: str | None = None,
        schema_recovery_response: dict[str, Any] | None = None,
        reviewer_progress_sha256: str | None = None) -> Path | None:
    """typed loader 一通过就按内容寻址持久化可执行决定（R-11.25）。

    写盘点必须在本 reviewer turn 内、早于 canonical scratch 晋升及任何身份闸：
    那些步骤后面就是 epoch 关闭与 scratch 清理。approval 的持久化失败必须向上
    传播为未知终态，不能继续消费一份重启后无法零成本重放的付费决定。
    Stage2 的书内 typed 产物只在 canonical action landed 后出现；在此之前也
    必须保存 full action，并额外绑定产出席位与 provider 原始回答 digest。
    """
    if isinstance(action, reviewer_tasks.ReviewerStopAction):
        return None
    common = {
        'sid': sid, 'run_id': run_id, 'epoch_id': epoch.epoch_id,
        'waiting_on': task.waiting_on, 'surface_seal': task.surface_seal,
        'business_sha256': task.business_sha256,
        'recorded_at': int(time.time()),
    }
    stage2_fields: dict[str, object] = {}
    if work_unit_id is not None:
        stage2_fields = {
            'work_unit_id': work_unit_id, 'work_unit_turn_id': work_unit_turn_id,
            'reviewer_seat_turn_id': work_unit_turn_id,
            'reviewer_session_run_id': reviewer_session_run_id,
            'provider_response_sha256': provider_response_sha256,
            'stage5_review_sha256': task.stage5_review_sha256,
            'stage5_reviewer_contract_sha256': task.stage5_reviewer_contract_sha256,
        }
    elif task.waiting_on in queue.STAGE2_REVIEWER_WAITING_ON:
        raise WorkUnitRefused('Stage2 决定必须绑定现役工作单元；旧独立席位账已退役')
    if isinstance(action, reviewer_tasks.ReviewerApprovalAction):
        if work_unit_id is None and task.waiting_on in reviewer_schema_recovery.WAITING_ON:
            ledger = reviewer_seat_ledger.read(
                base, sid, waiting_on=task.waiting_on,
                business_sha256=task.business_sha256)
            if ledger is not None and any(row.schema_recovery is not None for row in ledger.turns):
                turn = ledger.turns[-1]
                if (turn.parent_run_id != run_id or turn.epoch_id != epoch.epoch_id
                        or provider_response_sha256 is None
                        or schema_recovery_response is None
                        or turn.provider_response_sha256 != provider_response_sha256):
                    raise reviewer_tasks.ReviewerTaskError('schema recovery 决定与实际最新轮/回答不闭合')
                selection = schema_recovery_response.get('selection')
                options = [option for option in task.submission_options
                           if isinstance(selection, dict)
                           and option.get('id') == selection.get('option_id')]
                if len(options) != 1:
                    raise reviewer_tasks.ReviewerTaskError('schema recovery 原回答没有唯一 immutable option')
                stage2_fields = {
                    'reviewer_session_run_id': turn.session_run_id,
                    'reviewer_seat_turn_id': turn.turn_id,
                    'provider_response_sha256': provider_response_sha256,
                    'schema_recovery_response': {
                        'response': schema_recovery_response,
                        'exact_request': options[0]['exact_request'],
                    },
                }
        token = action.payload.get('review_token')
        record = ReviewerActionRecord(
            **common,
            surface=action.surface, payload=dict(action.payload),
            review_token=(
                token if isinstance(token, str) and token.strip() else None),
            **stage2_fields)
    elif isinstance(action, reviewer_tasks.ReviewerNodeAction):
        token = None
        stage5_fields: dict[str, object] = {}
        try:
            token_index = action.flags.index('--review-token') + 1
            token = action.flags[token_index]
        except (ValueError, IndexError):
            pass
        if (
            work_unit_id is None and token is None
            and action.node == 'stage5.review'
            and action.flags in {
                ('--retain-pretrim-batch', '@reviewer-input'),
                ('--request-reviewer-rewrite', '@reviewer-input'),
            }
        ):
            # trim 人审主体只认 Review + 原完整有序双 SHA + contract。
            # closure token 会随 resolution/wontfix/disclosure 重构，不得带入
            # durable record 或普通 token replay 判定。
            if (task.stage5_review_sha256 is None
                    or task.stage5_reviewer_contract_sha256 is None
                    or reviewer_session_run_id is None
                    or reviewer_seat_turn_id is None
                    or provider_response_sha256 is None):
                raise RuntimeError(
                    'Stage5 trim disposition durable action 缺原 Review/contract/'
                    '席位/provider 回答身份')
            stage5_fields = {
                'stage5_review_sha256': task.stage5_review_sha256,
                'stage5_reviewer_contract_sha256': (
                    task.stage5_reviewer_contract_sha256),
                'reviewer_session_run_id': reviewer_session_run_id,
                'reviewer_seat_turn_id': reviewer_seat_turn_id,
                'provider_response_sha256': provider_response_sha256,
            }
        record = ReviewerActionRecord(
            **common,
            node=action.node, flags=tuple(action.flags),
            input_payload=(
                dict(action.input_payload)
                if action.input_payload is not None else None),
            review_token=(
                token if isinstance(token, str) and token.strip() else None),
            **stage2_fields, **stage5_fields)
    else:  # pragma: no cover —— ReviewerAction union 的未来扩展须显式接线
        raise TypeError(f'未接线的 reviewer action：{type(action).__name__}')
    return uploads.save_reviewer_action(base, sid, record)


def _discard_reviewer_action_record(base: str, path: Path) -> None:
    try:
        uploads.discard_edit_plan(base, path)
    except NovelError as exc:
        warnings.warn(
            f'reviewer 决定记录清理失败：{exc}', RuntimeWarning, stacklevel=2)


def _stage5_recorded_response_sha256(record: ReviewerActionRecord) -> str:
    return reviewer_action_evidence.recorded_response_sha256(record)


@contextmanager
def _stage5_record_owner_guard(
        base: str, *, record_parent_run_id: str,
        current_parent_run_id: str | None,
        ) -> Iterator[bool]:
    with reviewer_action_evidence.record_owner_guard(
            base, record_parent_run_id=record_parent_run_id,
            current_parent_run_id=current_parent_run_id) as same_parent:
        yield same_parent


def _validate_stage5_action_record_ledger(
        base: str, sid: str, *, record: ReviewerActionRecord,
        current_parent_run_id: str | None = None) -> None:
    if (record.run_id in REGISTRY.live()
            and record.run_id != current_parent_run_id):
        raise reviewer_tasks.ReviewerTaskError(
            'Stage5 durable disposition 原 parent 仍在本进程活动，不能从旁恢复')
    reviewer_action_evidence.validate_stage5_action_record_ledger(
        base, sid, record=record,
        current_parent_run_id=current_parent_run_id)

def _stage5_pending_supersedes_record(
        base: str, sid: str, *,
        ledger: ReviewerSeatLedger,
        record: ReviewerActionRecord,
        record_turn: ReviewerSeatTurn,
        current_parent_run_id: str | None = None,
        ) -> bool:
    """只在拒因由更新的成功 provider turn 独立证明时退休 action。

    ``recorded_at`` 只有秒精度，文件名也只是内容地址，都不是时序。
    唯一可用的先后是 seat ``ordinal``；但 pending 本身也不能自证，
    因此还要它指向 ledger 最后一轮，并与该轮的成功 wire attempt、
    logical-session turn 和带 response digest 的成本行共同闭合。
    """
    pending = ledger.pending_correction
    if pending is None:
        return False
    source_rows = [
        row for row in ledger.turns
        if row.turn_id == pending.source_turn_id]
    if len(source_rows) != 1:
        raise reviewer_tasks.ReviewerTaskError(
            'Stage5 durable batch pending correction 来源不唯一')
    source = source_rows[0]
    if source.ordinal < record_turn.ordinal:
        return False
    if ledger.turns and ledger.turns[-1].turn_id != source.turn_id:
        # correction 已 reserve/完成时，旧 pending 仍指向紧邻前一轮。
        # 它不能压过更新 action，也不能在扫到旧 record 时提前
        # 阻断候选排序；若最后一轮没有 record，后续 latest-turn
        # validator 仍会保留旧记录并关闭。
        return False
    same_parent = source.parent_run_id == current_parent_run_id
    if source.parent_run_id in REGISTRY.live() and not same_parent:
        raise reviewer_tasks.ReviewerTaskError(
            'Stage5 durable batch 更新 pending 的原 parent 仍在活动')
    try:
        with _stage5_record_owner_guard(
                base, record_parent_run_id=source.parent_run_id,
                current_parent_run_id=current_parent_run_id):
            # owner-stopped lease 后重读，不用 lease 前的时序快照退休记录。
            current = reviewer_seat_ledger.read(
                base, sid, waiting_on=record.waiting_on,
                business_sha256=record.business_sha256)
            if current != ledger or current is None:
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage5 durable batch pending 证据在 owner lease 前后漂移')
            if (
                not current.turns
                or current.turns[-1].turn_id != source.turn_id
                or any(row.outcome == 'started' for row in current.turns)
                or source.outcome != 'ok'
                or source.exit_code != 0
                or source.parent_run_id != record.run_id
                or source.epoch_id != record.epoch_id
                or source.session_run_id != pending.session_run_id
                or source.session_run_id != record.reviewer_session_run_id
                or not source.attempts
            ):
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage5 durable batch 更新 pending 未指向原 parent/'
                    '同 epoch 的最后一轮成功席位')
            attempt = source.attempts[-1]
            if (
                attempt.finished_at is None
                or attempt.exit_code != 0
                or attempt.error_kind is not None
                or not attempt.wire_evidence
                or not attempt.provider_session_id
                or pending.provider_session_id != attempt.provider_session_id
            ):
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage5 durable batch 更新 pending 缺成功 provider attempt 证据')
            if source.provider_response_sha256 != pending.request_sha256:
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage5 durable batch 更新 pending 与 seat-bound response 不一致')
            if source.ordinal == record_turn.ordinal and (
                pending.request_sha256 != record.provider_response_sha256
                or source.provider_response_sha256
                != record.provider_response_sha256
            ):
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage5 durable batch 同轮 pending 与 durable action 回答不一致')

            sessions = [
                row for row in agent_session_audit.fold(
                    agent_session_audit.read_events(base))
                if row.session_run_id == source.session_run_id]
            if len(sessions) != 1:
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage5 durable batch 更新 pending 缺唯一 logical session')
            session = sessions[0]
            audit_attempts = [
                row for row in session.attempts
                if row.attempt_id == attempt.attempt_id]
            audit_turns = [
                row for row in session.turns
                if row.attempt_id == attempt.attempt_id]
            if (
                session.start.parent_run_id != record.run_id
                or session.start.role != 'commander'
                or session.start.node is not None
                or len(audit_attempts) != 1
                or len(audit_turns) != 1
                or session.attempts[-1].attempt_id != attempt.attempt_id
                or session.turns[-1].attempt_id != attempt.attempt_id
                or audit_attempts[0].error_kind is not None
                or audit_turns[0].error_kind is not None
                or audit_attempts[0].parent_run_id != source.parent_run_id
                or audit_turns[0].parent_run_id != source.parent_run_id
                or audit_attempts[0].provider_session_id
                != attempt.provider_session_id
                or audit_turns[0].provider_session_id
                != attempt.provider_session_id
                or audit_attempts[0].prompt_sha256
                != audit_turns[0].prompt_sha256
                or audit_attempts[0].schema_sha256
                != audit_turns[0].schema_sha256
                or audit_attempts[0].profile != audit_turns[0].profile
                or audit_attempts[0].requested_model
                != audit_turns[0].requested_model
                or audit_attempts[0].requested_effort
                != audit_turns[0].requested_effort
            ):
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage5 durable batch 更新 pending 与 logical session turn 不闭合')
            audit_turn = audit_turns[0]

            cost_rows = [
                row for row in read_jsonl_rows(cost_ledger_path(base))
                if row.get('run_id') == source.parent_run_id
                and row.get('session_run_id') == source.session_run_id
                and row.get('sid') == sid
                and row.get('role') == 'commander'
                and row.get('attempt_id') == attempt.attempt_id
                and row.get('activity') ==
                f'reviewer:{queue.WAITING_ON_STAGE5_CLOSURE}'
            ]
            if (
                len(cost_rows) != 1
                or cost_rows[0].get('outcome') != 'ok'
                or cost_rows[0].get('exit_code') != 0
                or cost_rows[0].get('error_kind') is not None
                or cost_rows[0].get('provider_session_id')
                != attempt.provider_session_id
                or cost_rows[0].get('response_sha256')
                != pending.request_sha256
                or cost_rows[0].get('code') != audit_turn.start_code_version
                or cost_rows[0].get('request_model')
                != audit_turn.requested_model
                or cost_rows[0].get('model') != audit_turn.actual_model
                or cost_rows[0].get('reasoning_effort')
                != audit_turn.requested_effort
                or cost_rows[0].get('actual_reasoning_effort')
                != audit_turn.actual_effort
                or cost_rows[0].get('protocol_version')
                != audit_turn.protocol_version
                or cost_rows[0].get('cli_version') != audit_turn.cli_version
            ):
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage5 durable batch 更新 pending 与成本账 response 不闭合')

            if session.end is None and not same_parent:
                if (
                    audit_turn.turn_index is None
                    or audit_turn.provider_session_id is None
                    or session.start.subject_sha256 is None
                    or session.start.profile is None
                    or audit_turn.actual_model is None
                    or audit_turn.prompt_sha256 is None
                    or audit_turn.schema_sha256 is None
                    or session.start.start_code_version is None
                ):
                    raise reviewer_tasks.ReviewerTaskError(
                        'Stage5 durable batch 更新 pending 缺 session 补尾身份')
                agent_session_audit.finish_restored_session(
                    base, {
                        'session_run_id': source.session_run_id,
                        'attempt_id': attempt.attempt_id,
                        'provider_session_id': audit_turn.provider_session_id,
                        'accepted_turn_index': audit_turn.turn_index,
                        'subject_sha256': session.start.subject_sha256,
                        'profile': session.start.profile.model_dump(mode='json'),
                        'model': audit_turn.actual_model,
                        'accepted_prompt_sha256': audit_turn.prompt_sha256,
                        'accepted_schema_sha256': audit_turn.schema_sha256,
                    }, process_state='ok', delivery_state='unknown',
                    end_code_version=session.start.start_code_version,
                    only_if_open=True)
                sessions = [
                    row for row in agent_session_audit.fold(
                        agent_session_audit.read_events(base))
                    if row.session_run_id == source.session_run_id]
                if len(sessions) != 1:
                    raise reviewer_tasks.ReviewerTaskError(
                        'Stage5 durable batch pending session 补尾后不唯一')
                session = sessions[0]
            if session.end is not None and (
                session.process_state != 'ok'
                or session.error_kind is not None
                or session.delivery_state not in {'unknown', 'blocked'}
            ):
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage5 durable batch pending session 未证明 process ok')
            if session.end is None and not same_parent:
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage5 durable batch pending 跨 parent 补尾后 session 仍未闭合')
            final = reviewer_seat_ledger.read(
                base, sid, waiting_on=record.waiting_on,
                business_sha256=record.business_sha256)
            if final is None or final.pending_correction != pending or any(
                    row.outcome == 'started' for row in final.turns):
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage5 durable batch pending 补尾后席位账漂移')
            return True
    except reviewer_tasks.ReviewerTaskError:
        raise
    except (
        NovelError, OSError, ValueError,
        reviewer_seat_ledger.ReviewerSeatRefused,
    ) as exc:
        raise reviewer_tasks.ReviewerTaskError(
            'Stage5 durable batch 更新 pending 无法确定性闭合：'
            f'{type(exc).__name__}: {exc}') from exc


class _Stage2ReviewerPreflightRejected(TerminalState):
    """producer 已确定性拒绝 durable Stage2 action；不是运行时 unknown。"""

    def __init__(self, safe_summary: str) -> None:
        super().__init__(safe_summary)
        self.safe_summary = safe_summary


def _preflight_stage2_reviewer_action(
        base: str, sid: str, *, epoch: ExecutionEpoch,
        action: reviewer_tasks.ReviewerNodeAction) -> None:
    """用 canonical producer 的纯内存 builder 预检一份 durable apply-edit。"""
    if (
        action.node != 'stage2.global'
        or action.input_payload is None
        or tuple(action.flags[:1]) != ('--apply-edit',)
    ):
        return
    decoded = decode_artifact(STAGE2_GLOBAL_EDIT, action.input_payload)
    edit = GlobalEdit.model_validate(decoded.payload)
    # 局部 import 避开 orchestrator 启动时把整个 Stage2 producer 图提前载入；
    # readonly ctx 是额外物理约束，真正判据仍由 producer 唯一实现。
    from novel.stages.stage2 import extract as stage2_extract

    novel_dir = paths.resolve_novel_dir(base, sid)
    try:
        stage2_extract.preflight_global_edit(
            state.readonly_ctx(
                novel_dir, 'stage2.global', epoch.code_version),
            edit,
        )
    except stage2_extract.GlobalEditCandidateRejected as exc:
        # producer 的完整 diagnostics 可能含模型提供的 ID/正文；席位账与
        # correction prompt 只允许传播它给出的稳定脱敏摘要。
        raise _Stage2ReviewerPreflightRejected(exc.safe_summary) from exc


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


def _reconcile_completed_work_unit_actions(
        base: str, sid: str, *, code: str,
        current_parent_run_id: str | None = None) -> None:
    """补齐正式审批成功、采用状态或活动记录尚未收尾的崩溃窗。

    当前主体或配置可以已经改变。退休仅凭原工作单元的采用事实或 exact 提交证明，
    不把旧决定交给新工作包，不授予任何新审批或模型额度。
    """
    from novel.orchestrator import approvals, reviewer_node_landed

    for path, record in uploads.load_reviewer_actions(base, sid):
        if record.work_unit_id is None or record.work_unit_turn_id is None:
            continue
        identity = commander_work_unit(record.waiting_on)
        if identity is None:
            raise WorkUnitRefused('活动审阅记录没有登记的工作单元属主')
        node, scope = identity
        budget = commander_budget_for_record(base, sid, record)
        if budget.unit_id != record.work_unit_id:
            raise WorkUnitRefused('活动审阅记录与登记工作单元不一致')
        if budget.turn(record.work_unit_turn_id).state not in ('delivered', 'adopted'):
            continue
        owner = (nullcontext() if record.run_id == current_parent_run_id
                 else admission.require_owner_stopped(base, record.run_id))
        with owner:
            verified = validate_reviewer_record(base, sid, record)
            turn = verified.turn(record.work_unit_turn_id)
            if turn.state == 'delivered':
                if record.surface is not None and record.payload is not None:
                    landed = approvals.reviewer_approval_payload_landed(
                        base, sid, surface=record.surface, payload=record.payload)
                elif record.node in ('stage0.approve', 'stage6.approve', 'stage7.approve'):
                    landed = reviewer_node_landed.approval_action_landed(
                        base, sid, node=record.node, flags=tuple(record.flags), code=code)
                else:
                    # typed 生产补丁仍由各 producer 的 committed journal 证明。
                    continue
                if not landed:
                    continue
                verified.mark_adopted(record.work_unit_turn_id)
            elif turn.state != 'adopted':
                raise WorkUnitRefused('取得原属主租约后交付状态发生变化')
            uploads.discard_edit_plan(base, path)


def _reconcile_landed_stage2_work_unit_actions(
        base: str, sid: str, *, code: str,
        current_parent_run_id: str | None = None) -> None:
    """免费 producer 补提交后先收原内容账，再让 planner 越过原审阅 blocker。

    prepared 仅授权重放，不证明提交。原回答、typed 动作、成本/会话证明与
    当前 committed 产物与合法提交后继闭合后才能 adopt；只写服务账，不补造审批。
    逐项否定/UNVER 决定没有接受后继，不能因节点仍阻断而无限重放已落决定。
    """
    from novel.orchestrator import approvals
    from novel.stages.stage2 import extract, global_review, plot_edit

    for path, record in uploads.load_reviewer_actions(base, sid):
        if (record.work_unit_id is None or record.work_unit_turn_id is None
                or record.waiting_on not in queue.STAGE2_REVIEWER_WAITING_ON):
            continue
        node, scope = commander_work_unit(record.waiting_on)
        budget = commander_budget_for_record(base, sid, record)
        if budget.unit_id != record.work_unit_id:
            raise WorkUnitRefused('Stage2 退休记录与登记工作单元不一致')
        if budget.turn(record.work_unit_turn_id).state == 'failed':
            # 拒因先写、record 后删的尾窗由当前 Reviewer 拒绝恢复处理，不当作落盘。
            continue
        owner = (nullcontext() if record.run_id == current_parent_run_id
                 else admission.require_owner_stopped(base, record.run_id))
        with owner:
            budget = validate_reviewer_record(base, sid, record)
            if budget.turn(record.work_unit_turn_id).state == 'adopted':
                uploads.discard_edit_plan(base, path)
                continue
            submitted = False
            if record.surface in ('stage2_global_review', 'stage2_blueprint_review'):
                if record.payload is not None:
                    submitted = approvals.stage2_review_payload_landed(base, sid, record.payload)
            elif record.node in ('stage2.global', 'stage2.blueprint') and record.input_payload is not None:
                if record.node == 'stage2.global' and '--apply-edit' in record.flags:
                    store = ArtifactStore(state.readonly_ctx(
                        paths.resolve_novel_dir(base, sid), record.node, code))
                    if store.exists(STAGE2_GLOBAL_EDIT_COMMIT):
                        journal = store.read_own(GlobalEditCommit, STAGE2_GLOBAL_EDIT_COMMIT)[0]
                        submitted = (journal.state == 'committed'
                                     and extract.submitted_global_patch_is_current(base, sid, record.input_payload))
                elif record.node == 'stage2.global' and '--review-decisions' in record.flags:
                    submitted = global_review.submitted_decisions_is_current(
                        base, sid, record.input_payload, require_action_complete=True)
                elif record.node == 'stage2.blueprint' and '--apply-edit' in record.flags:
                    submitted = plot_edit.submitted_edit_is_current(base, sid, record.input_payload)
                if submitted and record.node != 'stage2.blueprint' and '--review-decisions' not in record.flags:
                    try:
                        approvals.require_stage2_node_accepted(base, sid, record.node)
                    except ApprovalRequired:
                        submitted = False
            if submitted:
                # 先标原回答已消费，再退休活动记录；删除中断可按 adopted 原证明补尾。
                budget.mark_adopted(record.work_unit_turn_id)
                uploads.discard_edit_plan(base, path)


def _protocol_recovery_sources(
        base: str, sid: str, *, ledger: ReviewerSeatLedger,
        current_parent_run_id: str | None, validation_code: str,
        submission_options: tuple[dict[str, Any], ...] = (),
        ) -> reviewer_protocol_recovery.RecoveredReviewerResponse | None:
    """调用方持原 owner-stopped lease；纯读重验原协议误拒的全部来源。"""
    if not ledger.turns:
        return None
    source_run_id = ledger.turns[0].parent_run_id
    parents = {row.run_id: row for row in records(base)}
    source = parents.get(source_run_id)
    target = parents.get(current_parent_run_id or '')
    if source is None or target is None or target.contract is None:
        raise reviewer_tasks.ReviewerTaskError('协议恢复缺 source/target parent contract')
    recovered = reviewer_protocol_recovery.inspect_response(
        base, sid, ledger=ledger, parent=source,
        log_path=run_log_path(base, source_run_id),
        profile=target.contract.profiles.commander, validation_code=validation_code,
        submission_options=submission_options)
    if recovered is not None:
        # 按登记 policy 重验原字符串。尾部 option 包装恢复也必须先拒绝重复键，
        # 不能先重新序列化，或把任意 Extra data 当成可忽略文本。
        strict_document = reviewer_protocol_recovery.decode_recovered_response(
            recovered, submission_options)
        if strict_document != recovered.document:
            raise reviewer_tasks.ReviewerTaskError('协议恢复的原始回答解析不一致')
    return recovered


def _schema_recovery_sources(
        base: str, sid: str, *, ledger: ReviewerSeatLedger,
        current_parent_run_id: str, validation_code: str,
        replacement_schema_sha256: str, replacement_subject_sha256: str,
        ) -> ReviewerSchemaRecoveryProof | None:
    """由真实 ended parent 和当前授权 profile 形成技术拒绝证明，不生成审阅决定。"""
    if not reviewer_schema_recovery.is_recovery_candidate(ledger):
        return None
    source_run_id = ledger.turns[-1].parent_run_id
    if source_run_id == current_parent_run_id:
        return None
    parents = {row.run_id: row for row in records(base)}
    source, target = parents.get(source_run_id), parents.get(current_parent_run_id)
    if source is None or target is None or target.contract is None:
        raise reviewer_tasks.ReviewerTaskError('schema recovery 缺 source/target parent 契约')
    reviewer_schema_recovery.validate_recovery_chain(
        base, sid, ledger=ledger, parents=parents,
        log_path_for=lambda identity: run_log_path(base, identity))
    with admission.require_owner_stopped(base, source_run_id):
        current = reviewer_seat_ledger.read(
            base, sid, waiting_on=ledger.waiting_on,
            business_sha256=ledger.business_sha256)
        if current != ledger:
            raise reviewer_tasks.ReviewerTaskError('schema recovery 来源账在 owner lease 前后漂移')
        return reviewer_schema_recovery.inspect_rejection(
            base, sid, ledger=ledger, parent=source,
            log_path=run_log_path(base, source_run_id),
            profile=target.contract.profiles.commander,
            replacement_schema_sha256=replacement_schema_sha256,
            validation_code_version=validation_code,
            replacement_subject_sha256=replacement_subject_sha256)


def _recover_protocol_reviewer_action(
        base: str, sid: str, *, epoch: ExecutionEpoch,
        current: OperatorNext, task: reviewer_tasks.ReviewerTask,
        current_parent_run_id: str | None,
        ) -> _ReplayableReviewerAction | None:
    if task.waiting_on in queue.STAGE2_REVIEWER_WAITING_ON:
        return None
    token = reviewer_tasks.approval_review_token(task.submission_options)
    if token is None:
        return None
    ledger = reviewer_seat_ledger.read(
        base, sid, waiting_on=task.waiting_on, business_sha256=task.business_sha256)
    if ledger is None or not reviewer_protocol_recovery.is_recovery_candidate(ledger):
        return None
    source = ledger.turns[0]
    if source.parent_run_id == current_parent_run_id:
        return None
    with admission.require_owner_stopped(base, source.parent_run_id):
        sealed_ledger = reviewer_seat_ledger.read(
            base, sid, waiting_on=task.waiting_on, business_sha256=task.business_sha256)
        if sealed_ledger != ledger:
            raise reviewer_tasks.ReviewerTaskError('协议恢复的原席位在取得 owner lease 前后漂移')
        if (ledger.pending_correction is None
                and not reviewer_protocol_recovery.has_registered_rejection(
                    base, run_log_path(base, source.parent_run_id))):
            return None
        recovered = _protocol_recovery_sources(
            base, sid, ledger=ledger, current_parent_run_id=current_parent_run_id,
            validation_code=epoch.code_version,
            submission_options=task.submission_options)
        if recovered is None:
            return None
        original = recovered.document
        raw_action = original['action']
        if raw_action['payload'].get('review_token') != token:
            raise reviewer_tasks.ReviewerTaskError('协议恢复的原 review token 已变化')
        source_turns = [row for row in ledger.turns
                        if row.turn_id == recovered.proof.turn_id]
        if len(source_turns) != 1:
            raise reviewer_tasks.ReviewerTaskError('协议恢复的原席位轮次不唯一')
        source = source_turns[0]
        record = ReviewerActionRecord(
            sid=sid, run_id=source.parent_run_id, epoch_id=source.epoch_id,
            waiting_on=task.waiting_on, business_sha256=task.business_sha256,
            surface_seal=original['surface_seal'], surface=raw_action['surface'],
            payload=raw_action['payload'], review_token=token,
            protocol_recovery=recovered.proof,
            recorded_at=source.finished_at or source.started_at)
        document = reviewer_tasks.replay_request_document(record, current)

        def persist(action: reviewer_tasks.ReviewerAction, _digest: str) -> Path:
            if not isinstance(action, reviewer_tasks.ReviewerApprovalAction):
                raise reviewer_tasks.ReviewerTaskError('协议恢复不得变更为另一类动作')
            reviewer_protocol_recovery.validate_record(record, recovered)
            reviewer_protocol_recovery.archive_record(base, sid, record)
            return uploads.save_reviewer_action(base, sid, record)

        action, _digest, path = _materialize_reviewer_document(
            task, current, document,
            response_digest=_reviewer_response_document(
                json.dumps(document, ensure_ascii=False))[1],
            persist_validated=persist,
            preserve_validated=lambda candidate, digest: (
                _preserve_validated_reviewer_residue(base, sid, candidate, digest)))
        if path is None:
            raise RuntimeError('协议恢复未产生 durable action')
        return _ReplayableReviewerAction(path=path, action=action, record=record)


def _retire_landed_protocol_reviewer_action(
        base: str, sid: str, *, path: Path, record: ReviewerActionRecord,
        ) -> bool:
    """审批已落而活动记录未退休的崩溃窗，只凭正式原决定和原来源补尾。"""
    from novel.orchestrator import approvals

    if (record.protocol_recovery is None
            or record.surface not in {'design', 'structure'}
            or record.payload is None
            or not approvals.stage4_review_payload_landed(
                base, sid, surface=record.surface, payload=record.payload)):
        return False
    with admission.require_owner_stopped(base, record.run_id):
        ledger = reviewer_seat_ledger.read(
            base, sid, waiting_on=record.waiting_on,
            business_sha256=record.business_sha256)
        if ledger is None:
            raise reviewer_tasks.ReviewerTaskError('已落协议恢复决定的原席位账缺失')
        # 原主体可能已返修换代。这份 option 只用于重验原公开回答与 proof，
        # 不交当前 loader、不铸 permit；退休权来自上面正式审批的 exact payload。
        original_options = ({
            'id': f'approval:{record.surface}',
            'exact_request': {
                'schema': reviewer_tasks.REQUEST_SCHEMA,
                'waiting_on': record.waiting_on,
                'surface_seal': record.surface_seal,
                'action': {'kind': 'approval', 'surface': record.surface,
                           'payload': record.payload},
            },
        },)
        recovered = _protocol_recovery_sources(
            base, sid, ledger=ledger,
            # 这里只补已提交决定的退休；新 run 换模型不能推翻旧提交事实。
            # 复验原 parent 冻结 profile，不把当前配置当作旧证据的一部分。
            current_parent_run_id=record.run_id,
            validation_code=record.protocol_recovery.validation_code_version,
            submission_options=original_options)
        if recovered is None:
            raise reviewer_tasks.ReviewerTaskError('已落协议恢复决定的原来源不闭合')
        reviewer_protocol_recovery.validate_record(record, recovered)
        reviewer_protocol_recovery.archive_record(base, sid, record)
        # 删除失败须保持 unknown，不能吞成 warning 后另开模型。
        uploads.discard_edit_plan(base, path)
    return True


def _find_replayable_reviewer_action(
        base: str, sid: str, *, epoch: ExecutionEpoch,
        current: OperatorNext,
        task: reviewer_tasks.ReviewerTask,
        current_parent_run_id: str | None = None,
        ) -> _ReplayableReviewerAction | None:
    """唤起模型前先查盘上是否已有适用于当前 blocker 的 typed 决定。

    普通决定只认同 ``waiting_on`` 且 surface seal、opaque review_token 或稳定
    business identity 至少一项相同的记录。Stage0 没有 review_token，
    其 revision/完整保留文本/removed identity/contract 已全部进入
    ``business_sha256``，因此代码 epoch 改变后仍能零成本重放。
    Stage2 必须同时命中稳定 business 与 workpack；surface seal 可因纯代码
    epoch 变化，但 full action 仍须重走今天的 loader 与当前 permit。
    Stage5 batch 若在章间被 kill，则只在原 Review/完整有序 trim
    双 SHA/contract business 仍成立且盘面恰为 exact 已落前缀 + current
    unresolved 后缀时重交原 manifest；
    全部已落时只退休记录，证据冲突保留记录并关闭式停止。
    命中的记录仍要重走 ``load_action``：schema、sentinel、all-accept
    等确定性判据以今天的代码为准。
    """
    waiting_on = task.waiting_on
    if waiting_on in queue.STAGE2_REVIEWER_WAITING_ON:
        raise WorkUnitRefused('Stage2 旧席位决定重放已退役；只接受现役工作单元恢复')
    current_token = reviewer_tasks.approval_review_token(
        task.submission_options)
    current_stage5_trim = (
        waiting_on == queue.WAITING_ON_STAGE5_CLOSURE
        and any(
            isinstance(option, dict)
            and option.get('id') ==
            'node:stage5.review.retain-pretrim-batch'
            for option in task.submission_options)
    )
    consumed_rewrite_record_sha256: str | None = None
    if waiting_on == queue.WAITING_ON_STAGE5_CLOSURE:
        from novel.stages.stage5 import reviewer_rewrite

        probe = reviewer_rewrite.current_reviewer_rewrite_request(
            state.readonly_ctx(
                paths.resolve_novel_dir(base, sid),
                'stage5.write', current.code_version))
        if probe is not None:
            consumed_rewrite_record_sha256 = (
                probe.request.reviewer_action_record_sha256)
    candidates: list[tuple[
        tuple[int, int, str], Path, ReviewerActionRecord,
        tuple[dict[str, Any], ...] | None, str | None,
    ]] = []
    for path, record in uploads.load_reviewer_actions(base, sid):
        if _retire_landed_protocol_reviewer_action(
                base, sid, path=path, record=record):
            continue
        if record.waiting_on != waiting_on:
            continue
        if (record.waiting_on in reviewer_schema_recovery.WAITING_ON
                and record.business_sha256 == task.business_sha256):
            mixed_ledger = reviewer_seat_ledger.read(
                base, sid, waiting_on=record.waiting_on,
                business_sha256=record.business_sha256)
            if (mixed_ledger is not None
                    and any(row.schema_recovery is not None for row in mixed_ledger.turns)
                    and record.schema_recovery_response is None):
                raise reviewer_tasks.ReviewerTaskError('混合席位账的决定缺实际新 session/原回答证明')
        is_stage5_batch = (
            record.node == 'stage5.review'
            and record.flags in {
                ('--retain-pretrim-batch', '@reviewer-input'),
                ('--request-reviewer-rewrite', '@reviewer-input'),
            }
        )
        if (
            consumed_rewrite_record_sha256 is not None
            and record.flags == (
                '--request-reviewer-rewrite', '@reviewer-input')
            and path.name == (
                'reviewer-action-'
                f'{consumed_rewrite_record_sha256}.json')
        ):
            raise reviewer_tasks.ReviewerTaskError(
                'canonical reviewer rewrite request 已存在但 active authority '
                '尚未由 Agent reconcile/seal；拒绝重放或另开 Commander')
        if current_stage5_trim and not is_stage5_batch:
            # 同一顶部卡的 closure 与 trim 是互斥 action mode；当前纯 trim
            # workpack 不得因旧 surface seal 恰相同而重放 closure-only 回答。
            uploads.discard_edit_plan(base, path)
            continue
        # 先用 record 内部稳定 identity 与 current Review/contract 判 stale。
        # 已证明换代的旧主体可直接退休；不能让它遗留的 started/旧审计反过来
        # 永久阻塞真实新主体。只有同主体才消费原席位恢复证据。
        stage5_binding = reviewer_tasks.stage5_trim_partial_replay_options(
            base, sid, record, current, task.submission_options,
            current_review_sha256=task.stage5_review_sha256)
        if stage5_binding is not None and stage5_binding.state == 'stale':
            uploads.discard_edit_plan(base, path)
            continue
        if is_stage5_batch:
            ledger = reviewer_seat_ledger.read(
                base, sid, waiting_on=record.waiting_on,
                business_sha256=record.business_sha256)
            seat_rows = [] if ledger is None else [
                row for row in ledger.turns
                if row.turn_id == record.reviewer_seat_turn_id]
            if len(seat_rows) != 1:
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage5 durable batch 无法按 turn_id 唯一排序；保留记录并停止')
            seat_row = seat_rows[0]
            if ledger is not None and ledger.pending_correction is not None:
                if _stage5_pending_supersedes_record(
                        base, sid, ledger=ledger, record=record,
                        record_turn=seat_row,
                        current_parent_run_id=current_parent_run_id):
                    # 同 turn 或更新 turn 的拒因比 action record 更新；严格退休
                    # 残留 action。pending 已由最后成功 seat/attempt/
                    # session/cost 四证闭合，不按 recorded_at 秒级时间
                    # 或文件名误选旧回答。
                    uploads.discard_edit_plan(base, path)
                    continue
            order = (1, seat_row.ordinal, path.name)
        else:
            order = (0, record.recorded_at, path.name)
        partial_stage5_options = (
            stage5_binding.submission_options
            if stage5_binding is not None else None)
        seal_match = record.surface_seal == current.surface_seal
        token_match = (
            record.review_token is not None and current_token is not None
            and record.review_token == current_token)
        business_match = record.business_sha256 == task.business_sha256
        if record.protocol_recovery is not None:
            if not (business_match and token_match):
                raise reviewer_tasks.ReviewerTaskError(
                    '协议恢复 record 的当前业务/token 已变化；保留证据并停止')
            matches = True
        elif record.workpack_sha256 is not None:
            matches = (
                business_match
                and task.workpack_sha256 is not None
                and record.workpack_sha256 == task.workpack_sha256
                and record.reviewer_session_run_id is not None
                and record.provider_response_sha256 is not None)
        else:
            matches = seal_match or token_match or business_match
        if (matches or partial_stage5_options is not None
                or stage5_binding is not None
                and stage5_binding.state == 'fully_landed'):
            candidates.append((
                order, path, record, partial_stage5_options,
                stage5_binding.state if stage5_binding is not None else None,
            ))
        else:
            _discard_reviewer_action_record(base, path)
    if not candidates:
        return _recover_protocol_reviewer_action(
            base, sid, epoch=epoch, current=current, task=task,
            current_parent_run_id=current_parent_run_id)
    candidates.sort(key=lambda item: item[0])
    _order, path, record, replay_options, stage5_state = candidates[-1]
    if record.schema_recovery_response is not None:
        if (record.business_sha256 != task.business_sha256
                or record.review_token != current_token):
            raise reviewer_tasks.ReviewerTaskError('schema recovery 决定的业务/token 已变化')
        reviewer_schema_recovery.validate_action_record(
            base, sid, record=record,
            parents={row.run_id: row for row in records(base)},
            log_path_for=lambda identity: run_log_path(base, identity),
            current_parent_run_id=current_parent_run_id)
    if record.protocol_recovery is not None:
        with admission.require_owner_stopped(base, record.run_id):
            ledger = reviewer_seat_ledger.read(
                base, sid, waiting_on=record.waiting_on,
                business_sha256=record.business_sha256)
            if ledger is None:
                raise reviewer_tasks.ReviewerTaskError('协议恢复 record 的原席位账缺失')
            recovered = _protocol_recovery_sources(
                base, sid, ledger=ledger, current_parent_run_id=current_parent_run_id,
                validation_code=record.protocol_recovery.validation_code_version,
                submission_options=task.submission_options)
            if recovered is None:
                raise reviewer_tasks.ReviewerTaskError('协议恢复 record 的来源已不适用')
            reviewer_protocol_recovery.validate_record(record, recovered)
            reviewer_protocol_recovery.archive_record(base, sid, record)
    selected_is_stage5_batch = (
        record.node == 'stage5.review'
        and record.flags in {
            ('--retain-pretrim-batch', '@reviewer-input'),
            ('--request-reviewer-rewrite', '@reviewer-input'),
        })
    if selected_is_stage5_batch:
        same_ordinal = [row for row in candidates if row[0][1] == _order[1]]
        if len(same_ordinal) != 1:
            raise reviewer_tasks.ReviewerTaskError(
                'Stage5 durable batch 同一 seat turn 出现多份 action record；'
                '无法按时间或文件名猜选')
        # durable-first 会先 bind response/save full action，后闭席位。
        # 同主体重放/已落清理前都必须用独立
        # attempt/session/cost/parent 证据把原 started turn 补尾；
        # 不能让 record 自证席位成功。
        _validate_stage5_action_record_ledger(
            base, sid, record=record,
            current_parent_run_id=current_parent_run_id)
    for _stale_order, stale_path, _stale, _stale_options, _state in candidates[:-1]:
        if selected_is_stage5_batch:
            uploads.discard_edit_plan(base, stale_path)
        else:
            _discard_reviewer_action_record(base, stale_path)
    if selected_is_stage5_batch and stage5_state == 'fully_landed':
        uploads.discard_edit_plan(base, path)
        return None
    scratch = service_lifecycle.open_scratch_session(
        base, sid, epoch.epoch_id)
    try:
        document = reviewer_tasks.replay_request_document(record, current)
        if record.node == 'stage2.global' and record.input_payload is not None:
            # durable record 保存的是能直接交给 canonical CLI 的 full
            # action，因此 input_payload 已带 artifact envelope；Reviewer loader
            # 的输入面则是去掉可信版本信封的业务 payload，并由
            # loader 自己重建 envelope。重放必须先做这一次逆投影，
            # 不能把已编码 artifact 直接冒充 provider request。
            flags = set(record.flags or ())
            try:
                if ('--apply-edit' in flags
                        and '--review-decisions' not in flags):
                    decoded = decode_artifact(
                        STAGE2_GLOBAL_EDIT, record.input_payload)
                elif ('--review-decisions' in flags
                      and '--apply-edit' not in flags):
                    decoded = decode_artifact(
                        STAGE2_GLOBAL_REVIEW_DECISIONS, record.input_payload)
                else:
                    raise reviewer_tasks.ReviewerTaskError(
                        'Stage2 durable action 操作形状不唯一')
            except reviewer_tasks.ReviewerTaskError:
                raise
            except Exception as exc:  # noqa: BLE001 —— 坏记录关闭式汰汰
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage2 durable action 的 typed payload 无法重建：'
                    f'{type(exc).__name__}') from exc
            action_document = document.get('action')
            if not isinstance(action_document, dict):  # pragma: no cover
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage2 durable action 重放 wrapper 缺 action')
            action_document['input_payload'] = decoded.payload
        payload = (json.dumps(
            document, ensure_ascii=False, sort_keys=True) + '\n').encode('utf-8')
        replay_path = _write_identity_bound_scratch(
            scratch, stem='reviewer-replay', kind='request',
            identity_sha256=hashlib.sha256(payload).hexdigest(),
            payload=payload)
    except reviewer_tasks.ReviewerTaskError:
        # record/envelope/payload 无法重建是持久证据完整性异常，不是今天的
        # typed loader 对业务回答作出的拒绝；保留 record 并让调用者 exit70。
        raise
    try:
        action = reviewer_tasks.load_action(
            replay_path, current,
            submission_options=(replay_options or task.submission_options))
        # Stage2 fresh-session 纠错不依赖旧 provider 上下文；若重放动作随后被
        # canonical 边界拒绝，新会话仍需要上一份精确请求字节来铸 progress SHA。
        task.request_path.parent.mkdir(parents=True, exist_ok=True)
        task.request_path.write_bytes(payload)
    except reviewer_tasks.ReviewerTaskError as exc:
        if record.protocol_recovery is not None:
            raise reviewer_tasks.ReviewerTaskError(
                '协议恢复的原判断未通过当前 typed loader；保留来源并停止') from exc
        if record.workpack_sha256 is not None:
            raise reviewer_tasks.ReviewerTaskError(
                '旧 Stage2 记录不能经非 Stage2 恢复入口执行；保留原记录') from exc
        warnings.warn(
            f'盘上 reviewer 决定记录 {path.name} 未通过当前 loader，已删除：{exc}',
            RuntimeWarning, stacklevel=2)
        _discard_reviewer_action_record(base, path)
        return None
    return _ReplayableReviewerAction(
        path=path, action=action, record=record)


# ─────────────────────────────────────────────────────────────────────────────
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

    ⚠ `spawn` 跟着一起存：它可能是测试注入的假 spawn，必须活到这一项真被起来那一刻。
    ⚠ `base` 也存：队列是进程级的，而「哪个 base」是每一项自己的事。
    """

    plan: AgentRunPlan
    spawn: Spawn | None
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
            record = _start_planned(item.plan, spawn=item.spawn, registry=registry)
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
            f'启动 Commander 前必须显式授予 {"、".join(missing)}；'
            'preview 仍为 0 成本，可在确认完整 prompt/argv 后再启动')


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
            'Commander，请先恢复可复验的仓库代码身份')
    if '-dirty.' in code_version:
        raise UnsupportedRunMode(
            '当前工作树有未提交改动；preview 仍可查看，但不能启动或换入新的付费 '
            'epoch。请在干净的专用 worktree/冻结 commit 中跑书，避免代码漂移导致'
            '重复模型调用')


def enqueue(base: str | os.PathLike[str], sids: Sequence[str], *,
            authorized: bool, egress_authorized: bool,
            profiles: PipelineAgentProfiles | None,
            spawn: Spawn | None = None,
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
                _PENDING.append(_Pending(item_plan, spawn, batch_id))
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


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _reviewer_request_sha256(path: Path) -> str:
    """给失败请求一个稳定身份；读不到也不能让纠错循环失去上限。"""
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError as exc:
        return _sha256_bytes(
            f'unreadable:{type(exc).__name__}'.encode('ascii'))


def _reviewer_response_document(
        response: str) -> tuple[dict[str, object], str]:
    """严格解码 provider 最终回答；围栏、重复键与顶层数组都拒绝。"""

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        document: dict[str, object] = {}
        for key, value in pairs:
            if key in document:
                # key 可能本身含 provider 正文；拒因只给类别，不回显不可信值。
                raise ValueError('duplicate JSON object key')
            document[key] = value
        return document

    try:
        # object_pairs_hook 会递归用于每一层 object，不能只检查顶层后让嵌套
        # keyed edit 被 JSON 的 last-wins 语义静默改写。
        value = json.loads(response, object_pairs_hook=unique_object)
    except (ValueError, TypeError) as exc:
        # Python 3.11+ 会把超出 int digit limit 的 JSON 数字抛成普通
        # ValueError；它仍是 provider 输出不合法，必须进入有界 correction，
        # 不能越过 ReviewerTaskError 变成无界 runtime crash。
        raise reviewer_tasks.ReviewerTaskError(
            'Commander 最终回答不是合法的裸 JSON object') from exc
    if not isinstance(value, dict):
        raise reviewer_tasks.ReviewerTaskError(
            'Commander 最终回答顶层必须是 JSON object')
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode('utf-8') + b'\n'
    except (TypeError, ValueError) as exc:
        raise reviewer_tasks.ReviewerTaskError(
            'Commander 最终 JSON 含不可物化值') from exc
    return value, _sha256_bytes(encoded)


def _reviewer_response_sha256(response: str) -> str:
    """无效 JSON 也要有稳定指纹，防止同一拒因只改空白重放。"""
    try:
        _document, digest = _reviewer_response_document(response)
        return digest
    except reviewer_tasks.ReviewerTaskError:
        return _sha256_bytes(response.strip().encode('utf-8', errors='replace'))


def _reviewer_turn_response_sha256(
        turn: CommanderTurnResult, request_path: Path) -> str:
    if turn.final_response is not None:
        return _reviewer_response_sha256(turn.final_response)
    # 只给历史/测试注入缝保留文件交付；生产 driver 的
    # structured_response 成功却缺最终文本会在协议层已经拒绝。
    return _reviewer_request_sha256(request_path)


def _reviewer_candidate_path(request_path: Path) -> Path:
    return request_path.parent / (
        f'.{request_path.name}.{jobs.new_job_id()}.candidate')


def _preserve_validated_reviewer_residue(
        base: str, sid: str, candidate: Path, digest: str) -> Path:
    """把尚未 durable 的 typed-valid 回答移出 epoch scratch，避免收尾误删。"""
    root = (
        paths.service_runs_dir(base) / paths.check_sid(sid)
        / 'reviewer_residue').absolute()
    service_root = paths.service_dir(base).absolute()
    if service_root.is_symlink():
        raise reviewer_tasks.ReviewerTaskError('_service 根不得是符号链接')
    root.mkdir(parents=True, exist_ok=True)
    probe = root
    while probe != service_root:
        if probe.is_symlink():
            raise reviewer_tasks.ReviewerTaskError(
                f'reviewer residue 路径含符号链接：{probe}')
        probe = probe.parent
    try:
        root.resolve().relative_to(service_root.resolve())
    except ValueError as exc:
        raise reviewer_tasks.ReviewerTaskError(
            'reviewer residue 越出 _service') from exc
    target = root / f'reviewer-response-{digest}.json'
    if target.is_symlink():
        raise reviewer_tasks.ReviewerTaskError(
            'reviewer residue 目标不得是符号链接')
    if target.exists():
        if not target.is_file() or target.read_bytes() != candidate.read_bytes():
            raise reviewer_tasks.ReviewerTaskError(
                'reviewer residue 内容地址已存在不同字节')
        candidate.unlink()
        return target.resolve()
    os.replace(candidate, target)
    return target.resolve()


def _promote_reviewer_candidate(candidate: Path, request_path: Path) -> None:
    """严格 loader 成功后才替换 canonical request；绝不跟随 symlink。"""
    if request_path.is_symlink():
        raise reviewer_tasks.ReviewerTaskError(
            'reviewer canonical request 不得是符号链接')
    if request_path.exists() and not request_path.is_file():
        raise reviewer_tasks.ReviewerTaskError(
            'reviewer canonical request 若已存在必须是普通文件')
    os.replace(candidate, request_path)


class _ReviewerResponseTypedInvalid(reviewer_tasks.ReviewerTaskError):
    """Provider JSON/当前 loader 拒绝；不包含 durable/promote 异常。"""


def _materialize_reviewer_response(
        task: reviewer_tasks.ReviewerTask, current: OperatorNext,
        response: str, *,
        persist_validated: Callable[
            [reviewer_tasks.ReviewerAction, str], Path | None] | None = None,
        preserve_validated: Callable[[Path, str], Path] | None = None,
        ) -> tuple[reviewer_tasks.ReviewerAction, str, Path | None]:
    """复用现役 loader；approval 先 durable，再原子晋升 canonical scratch。"""
    try:
        document, response_digest = _reviewer_response_document(response)
    except reviewer_tasks.ReviewerTaskError as exc:
        raise _ReviewerResponseTypedInvalid(str(exc)) from exc
    wire = (stage4_reviewer_wire if stage4_reviewer_wire.uses_compact_wire(task)
            else stage2_reviewer_wire)
    if wire.uses_compact_wire(task):
        try:
            document = wire.assemble(task, document)
        except (stage2_reviewer_wire.Stage2ReviewerWireError,
                stage4_reviewer_wire.Stage4ReviewerWireError) as exc:
            # compact wire 只是运输层；所有拒绝仍进入现役 ReviewerTaskError
            # 的有界纠错/持久拒因路径，不能越级变成 Supervisor runtime crash。
            raise _ReviewerResponseTypedInvalid(str(exc)) from exc
    return _materialize_reviewer_document(
        task, current, document, response_digest=response_digest,
        persist_validated=persist_validated, preserve_validated=preserve_validated)


def _materialize_reviewer_document(
        task: reviewer_tasks.ReviewerTask, current: OperatorNext,
        document: dict[str, object], *, response_digest: str,
        persist_validated: Callable[
            [reviewer_tasks.ReviewerAction, str], Path | None] | None = None,
        preserve_validated: Callable[[Path, str], Path] | None = None,
        ) -> tuple[reviewer_tasks.ReviewerAction, str, Path | None]:
    """可信组装与已验协议恢复共用最终 loader/durable/promote，不宽免 provider wire。"""
    # provider 原回答摘要用于拒因/no-progress；residue 的内容地址只属于实际
    # 保存的 canonical request。恢复已验 full request 不再套入新的 compact wire。
    candidate_digest = _sha256_bytes(json.dumps(
        document, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False,
    ).encode('utf-8') + b'\n')
    candidate = _reviewer_candidate_path(task.request_path)
    validated = False
    promoted = False
    try:
        service_lifecycle.write_scratch_json_once(
            task.request_path.parent, candidate, document)
        try:
            action = reviewer_tasks.load_action(
                candidate, current,
                submission_options=task.submission_options)
        except reviewer_tasks.ReviewerTaskError as exc:
            raise _ReviewerResponseTypedInvalid(str(exc)) from exc
        validated = True
        record_path = (
            persist_validated(action, response_digest)
            if persist_validated is not None else None)
        _promote_reviewer_candidate(candidate, task.request_path)
        promoted = True
        return action, response_digest, record_path
    except BaseException as exc:
        if validated and not promoted and preserve_validated is not None:
            try:
                preserved = preserve_validated(candidate, candidate_digest)
                if hasattr(exc, 'add_note'):
                    exc.add_note(
                        f'typed-valid reviewer 回答已隔离保留：{preserved}')
            except BaseException as preserve_error:  # noqa: BLE001
                if hasattr(exc, 'add_note'):
                    exc.add_note(
                        'typed-valid reviewer 回答隔离失败：'
                        f'{type(preserve_error).__name__}: {preserve_error}')
        raise
    finally:
        # loader 拒绝的 untrusted 文本没有恢复价值；一旦 typed 验证已通过，
        # durable save 或 canonical promotion 的失败都必须保留唯一候选，供
        # 运维发现/恢复，不能在 finally 里把付费决定抹掉。
        if not validated or promoted:
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass



def _usage_dict(observation: CommanderObservation) -> dict[str, int | None] | None:
    usage = observation.usage
    if usage is None:
        return None
    return {
        'prompt_tokens': usage.prompt_tokens,
        'prompt_cache_hit_tokens': usage.prompt_cache_hit_tokens,
        'prompt_cache_miss_tokens': usage.prompt_cache_miss_tokens,
        'completion_tokens': usage.completion_tokens,
        'reasoning_tokens': usage.reasoning_tokens,
        'total_tokens': usage.total_tokens,
    }


def _session_usage(observation: CommanderObservation,
                   ) -> agent_session_audit.AgentSessionUsage | None:
    usage = _usage_dict(observation)
    if usage is None or all(value is None for value in usage.values()):
        return None
    return agent_session_audit.AgentSessionUsage(**usage)


def _session_delivery(outcome: AgentDeliveryOutcome) -> str:
    if outcome.state in ('ready', 'ready_with_disclosures'):
        return 'delivered'
    if outcome.state == 'stopped':
        return 'blocked'
    if outcome.state == 'failed':
        return 'failed'
    return 'unknown'


def _append_safe_traceback(base: str, run_id: str, *, prompt: str) -> None:
    """留异常类型/栈，但即使异常 repr 夹了 invocation 也不泄露 prompt。"""
    import traceback

    rendered = traceback.format_exc()
    for secret in (prompt, repr(prompt), json.dumps(prompt, ensure_ascii=False)):
        rendered = rendered.replace(secret, '<commander-prompt-redacted>')
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
        raise RuntimeError('Commander execution 收尾等待上限必须大于 0')
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
            commander_cancel = _cancel_commander_owner(parent_run_id)
            action_cancel = _cancel_action_owner(parent_run_id)
            cancel_note = (
                f'{commander_cancel}；canonical action cancel='
                f'{"已确认" if action_cancel else "无活动 owner"}')
            timeout_reason = (
                f'Commander 顶层进程退出后等待 {timeout:g} 秒仍未闭合（'
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
            reason=('Commander 已结束，但交付终验自身崩溃：'
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
          spawn: Spawn | None = None,
          registry: JobRegistry = REGISTRY) -> AgentRunRecord:
    """解析一次冻结 plan 后拉起 Commander；缺任一授权时不触碰 CLI。"""
    _require_start_authorization(
        authorized=authorized, egress_authorized=egress_authorized)
    return _start_planned(
        plan(base, sid, authorized=authorized,
             egress_authorized=egress_authorized, profiles=profiles, continuation=continuation),
        spawn=spawn, registry=registry)


def _start_planned(plan: AgentRunPlan, *, spawn: Spawn | None = None,
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
    invocation = plan.invocation
    _validate_invocation(
        invocation, prompt=plan.prompt, profile=plan.profiles.commander)
    run_id = jobs.new_job_id()
    session_run_id = jobs.new_job_id()
    parent_started_at = int(time.time())
    prompt_sha256 = _sha256_bytes(plan.prompt.encode('utf-8'))
    schema_sha256 = _sha256_bytes(
        f'commander-observation:{invocation.protocol}'.encode('utf-8'))
    start_payload = AgentRunStart(
        sid=sid, base=base, argv=list(invocation.argv), prompt=plan.prompt,
        authorized=plan.authorized,
        egress_authorized=plan.egress_authorized,
        code_version=code_version, identity_version=2,
        profiles=plan.profiles)
    contract_payload = RunContractV2(
        sid=sid, base=base,
        authorized=plan.authorized,
        egress_authorized=plan.egress_authorized,
        profiles=plan.profiles, continuation=plan.continuation)
    initial_epoch = ExecutionEpoch(
        epoch_id=run_id, ordinal=1, reason='initial',
        argv=list(invocation.argv), prompt_sha256=prompt_sha256,
        protocol_version=invocation.protocol,
        cli_version=invocation.cli_version, code_version=code_version,
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
                                'Commander session started 审计未能落盘，'
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
            original.add_note('Commander 启动失败的事件收尾未完全落盘：'
                              + '；'.join(cleanup_failures))
        raise

    epoch_invocations: dict[str, CommanderInvocation] = {
        initial_epoch.epoch_id: invocation,
    }
    active_admission_lock = threading.Lock()
    active_admission_id: str | None = None
    admission_quiesced = False
    commander_stop_requested = threading.Event()
    epoch_action_counts: dict[str, int] = {}
    epoch_drain_states: dict[str, str] = {}
    turn_index = 0
    latest_observation = CommanderObservation(
        provider_session_id=None, actual_model=None,
        actual_effort=None, usage=None)
    latest_outcome = AgentDeliveryOutcome(
        state='unknown', checked_at=int(time.time()),
        reason='Supervisor 尚未完成 Commander turn')
    latest_rc = CRASH
    latest_exit_origin = 'supervisor'
    latest_error_kind: str | None = None
    latest_commander_outcome = latest_outcome
    latest_commander_rc = CRASH
    latest_commander_error_kind: str | None = None
    provider_turn_recorded = False
    current_session_code_version = code_version
    current_session_invocation = invocation
    current_session_prompt_sha256 = prompt_sha256
    # 创建 parent/epoch 不等于调用过模型；只有 reviewer 真正启动前才开
    # Commander logical session，ready-only 的 clean run 不制造一对假 started/ended。
    session_started = False
    session_ended = False
    pending_reviewer_action: reviewer_tasks.ReviewerAction | None = None
    pending_reviewer_binding: tuple[str, str] | None = None
    pending_reviewer_business_sha256: str | None = None
    pending_reviewer_source_task: reviewer_tasks.ReviewerTask | None = None
    # Stage2 compact wire 的 provider 原回答 identity；canonical scratch 保存的是
    # trusted assembler 展开的完整请求，两者不能混作纠错/no-progress 指纹。
    pending_reviewer_response_sha256: str | None = None
    # Stage2 typed-valid 动作会在下一次 deterministic callback 才执行；必须把
    # 产出它的 logical session 一起跨过这条边界，canonical exit 2 才能精确
    # 回写原席位。这里不是 provider session，也绝不用于 resume。
    pending_reviewer_stage2_session_run_id: str | None = None
    # 已通过 loader 的 approval 决定在盘上的记录；重放来的动作被拒时不得走纠错。
    pending_reviewer_record_path: Path | None = None
    pending_reviewer_replayed = False
    pending_reviewer_protocol_recovery = False
    reviewer_correction_task: reviewer_tasks.ReviewerTask | None = None
    reviewer_correction_binding: tuple[str, str] | None = None
    reviewer_correction_reason: str | None = None
    reviewer_correction_request_sha256: str | None = None
    reviewer_correction_attempts = 0
    reviewed_blockers: set[tuple[str, str]] = set()
    completed_reviewer_business_actions: set[tuple[str, str]] = set()
    rejected_reviewer_business_actions: set[tuple[str, str]] = set()


    def _resume_prompt(epoch: ExecutionEpoch) -> str:
        return (
            '继续同一 immutable workpack。先重新读取当前 operator next；'
            '只执行仍为 ready 的 exact action，所有写盘仍须重新取得 permit。'
            f'当前 execution epoch={epoch.epoch_id}，'
            f'code_version={epoch.code_version}。'
        )

    def _resume_invocation(
            epoch: ExecutionEpoch, provider_session_id: str,
            *, full_prompt: bool = False) -> CommanderInvocation:
        prompt = (
            epoch_invocations[epoch.epoch_id].stdin_text
            if full_prompt else _resume_prompt(epoch))
        driver: CommanderDriver | None = None
        try:
            driver = create_commander_driver(plan.profiles.commander)
            resumed = driver.build_invocation(
                plan.profiles.commander, prompt,
                repo=REPO_ROOT, base=base,
                cli_version=epoch.cli_version,
                resume_provider_session_id=provider_session_id)
            _validate_invocation(
                resumed, prompt=prompt, profile=plan.profiles.commander)
            if resumed.resume_provider_session_id != provider_session_id:
                raise UnsupportedRunMode(
                    'Commander driver 没有把 exact provider session id '
                    '冻结进 resume invocation')
            return resumed
        finally:
            if driver is not None:
                driver.close()

    def _review_invocation(
            epoch: ExecutionEpoch, prompt: str,
            provider_session_id: str | None, *,
            output_schema: dict[str, object] | None = None,
            expected_output_schema_sha256: str | None = None,
            ) -> CommanderInvocation:
        """为一份 frozen reviewer task 构造新 turn 或 exact resume。

        reviewer 只从 provider 最终回答交付裸 JSON，不取得 scratch
        写权。合法请求由 Supervisor 严格验证后原子物化；
        launcher/preview 的普通 invocation 仍是 parent contract 审计面。
        """
        driver: CommanderDriver | None = None
        scratch = service_lifecycle.open_scratch_session(
            base, sid, epoch.epoch_id)
        try:
            driver = create_commander_driver(plan.profiles.commander)
            reviewed = driver.build_invocation(
                plan.profiles.commander, prompt,
                repo=REPO_ROOT, base=str(scratch),
                cli_version=epoch.cli_version,
                resume_provider_session_id=provider_session_id,
                structured_response=True,
                output_schema=output_schema,
                expected_output_schema_sha256=(
                    expected_output_schema_sha256))
            _validate_invocation(
                reviewed, prompt=prompt, profile=plan.profiles.commander,
                expected_cwd=scratch)
            if reviewed.resume_provider_session_id != provider_session_id:
                raise UnsupportedRunMode(
                    'Commander reviewer invocation 没有精确冻结 provider session id')
            if ((reviewed.output_schema_sha256 is None)
                    != (output_schema is None)):
                raise UnsupportedRunMode(
                    'Commander reviewer driver 没有精确冻结请求的 output schema')
            if (expected_output_schema_sha256 is not None
                    and reviewed.output_schema_sha256
                    != expected_output_schema_sha256):
                raise UnsupportedRunMode(
                    'Commander reviewer exact resume 的 output schema 身份漂移')
            return reviewed
        finally:
            if driver is not None:
                driver.close()

    def _make_epoch(
            reason: str, ordinal: int,
            provider_session_id: str | None) -> ExecutionEpoch:
        nonlocal session_run_id, turn_index, provider_turn_recorded
        nonlocal latest_observation, latest_outcome, latest_rc, latest_error_kind
        nonlocal latest_commander_outcome, latest_commander_rc
        nonlocal latest_commander_error_kind
        nonlocal current_session_code_version
        nonlocal current_session_invocation, current_session_prompt_sha256
        nonlocal session_started, session_ended
        nonlocal pending_reviewer_action, pending_reviewer_binding
        nonlocal pending_reviewer_business_sha256, pending_reviewer_source_task
        nonlocal pending_reviewer_stage2_session_run_id
        nonlocal pending_reviewer_response_sha256
        nonlocal pending_reviewer_record_path, pending_reviewer_replayed
        nonlocal pending_reviewer_protocol_recovery
        nonlocal reviewer_correction_task, reviewer_correction_binding
        nonlocal reviewer_correction_reason, reviewer_correction_request_sha256
        nonlocal reviewer_correction_attempts
        current_code = prov.code_version()
        _require_reproducible_code_version(current_code)
        epoch_id = jobs.new_job_id()
        if provider_session_id is None:
            # 身份漂移后从新 provider session 进入，但 workpack 仍是 parent
            # contract 那一份，不把授权/profile 重新解释一遍。
            # 若这是“暂停后无法取得 provider id”的恢复，而不是 code drift，
            # 旧 logical session 尚未由 close_epoch 封存；先诚实结束它，再开新会话。
            _end_current_session(
                outcome=latest_commander_outcome, rc=latest_commander_rc,
                error_kind=(latest_commander_error_kind
                            or 'provider_session_unavailable'),
                end_code_version=current_code)
            # 新 execution epoch 必须从当前代码/运行契约 重新构造 Commander
            # 启动包；只换一枚 code 指纹、却继续发送旧 prompt，会把“滚 epoch”
            # 变成审计假象。授权与 profiles 仍只取稳定 parent contract。
            epoch_plan = _plan_resolved(
                base, sid, authorized=plan.authorized,
                egress_authorized=plan.egress_authorized,
                profiles=plan.profiles, code_version=current_code)
            epoch_invocation = epoch_plan.invocation
            session_run_id = jobs.new_job_id()
            turn_index = 0
            provider_turn_recorded = False
            latest_observation = CommanderObservation(
                provider_session_id=None, actual_model=None,
                actual_effort=None, usage=None)
            latest_outcome = AgentDeliveryOutcome(
                state='unknown', checked_at=int(time.time()),
                reason='新 execution epoch 尚未完成 Commander turn')
            latest_rc = CRASH
            latest_error_kind = None
            latest_commander_outcome = latest_outcome
            latest_commander_rc = CRASH
            latest_commander_error_kind = None
            current_session_code_version = current_code
            current_session_invocation = epoch_invocation
            current_session_prompt_sha256 = _sha256_bytes(
                epoch_invocation.stdin_text.encode('utf-8'))
            session_started = False
            session_ended = False
            pending_reviewer_action = None
            pending_reviewer_binding = None
            pending_reviewer_business_sha256 = None
            pending_reviewer_source_task = None
            pending_reviewer_stage2_session_run_id = None
            pending_reviewer_response_sha256 = None
            pending_reviewer_record_path = None
            pending_reviewer_replayed = False
            pending_reviewer_protocol_recovery = False
            reviewer_correction_task = None
            reviewer_correction_binding = None
            reviewer_correction_reason = None
            reviewer_correction_request_sha256 = None
            reviewer_correction_attempts = 0
            reviewed_blockers.clear()
            completed_reviewer_business_actions.clear()
            rejected_reviewer_business_actions.clear()
        else:
            provisional = ExecutionEpoch(
                epoch_id=epoch_id, ordinal=ordinal, reason=reason,
                argv=list(plan.invocation.argv),
                prompt_sha256=current_session_prompt_sha256,
                protocol_version=current_session_invocation.protocol,
                cli_version=current_session_invocation.cli_version,
                code_version=current_code,
                identity_version=2,
                session_run_id=session_run_id,
                resume_provider_session_id=provider_session_id)
            epoch_invocation = _resume_invocation(
                provisional, provider_session_id)
        epoch = ExecutionEpoch(
            epoch_id=epoch_id, ordinal=ordinal, reason=reason,
            argv=list(epoch_invocation.argv),
            prompt_sha256=_sha256_bytes(
                epoch_invocation.stdin_text.encode('utf-8')),
            protocol_version=epoch_invocation.protocol,
            cli_version=epoch_invocation.cli_version,
            code_version=current_code,
            identity_version=2,
            session_run_id=session_run_id,
            resume_provider_session_id=provider_session_id)
        epoch_invocations[epoch_id] = epoch_invocation
        return epoch

    def _open_epoch(
            epoch: ExecutionEpoch, *, actual_prompt_sha256: str | None = None,
    ) -> None:
        # admission 按 provider turn 单独开 gate；epoch 自身只管身份。
        nonlocal session_started, current_session_prompt_sha256
        if session_started:
            return
        if actual_prompt_sha256 is not None:
            current_session_prompt_sha256 = actual_prompt_sha256
        agent_session_audit.append_event(
            base,
            agent_session_audit.AgentSessionEvent(
                session_run_id=epoch.session_run_id,
                parent_run_id=run_id,
                role='commander', node=None, chapter=None,
                subject_sha256=current_session_prompt_sha256,
                profile=plan.profiles.commander,
                requested_model=plan.profiles.commander.model,
                requested_effort=plan.profiles.commander.reasoning_effort,
                cli_version=current_session_invocation.cli_version,
                protocol_version=current_session_invocation.protocol,
                start_code_version=current_session_code_version,

                prompt_sha256=current_session_prompt_sha256,
                schema_sha256=schema_sha256,
                kind='started', ts=int(time.time()),
            ),
        )
        session_started = True

    def _end_current_session(
            *, outcome: AgentDeliveryOutcome, rc: int,
            error_kind: str | None, end_code_version: str) -> None:
        """闭合当前 identity session；pause 不结束 epoch 或同一续会。"""
        nonlocal session_ended
        if not session_started or session_ended:
            return
        owned_provider_session_id = (
            latest_observation.provider_session_id
            if provider_turn_recorded else None)
        agent_session_audit.append_event(
            base,
            agent_session_audit.AgentSessionEvent(
                session_run_id=session_run_id,
                parent_run_id=run_id,
                role='commander', node=None, chapter=None,
                subject_sha256=current_session_prompt_sha256,
                profile=plan.profiles.commander,
                provider_session_id=owned_provider_session_id,
                requested_model=plan.profiles.commander.model,
                actual_model=latest_observation.actual_model,
                requested_effort=plan.profiles.commander.reasoning_effort,
                actual_effort=latest_observation.actual_effort,
                cli_version=current_session_invocation.cli_version,
                protocol_version=current_session_invocation.protocol,
                start_code_version=current_session_code_version,
                end_code_version=end_code_version,

                prompt_sha256=current_session_prompt_sha256,
                schema_sha256=schema_sha256,
                process_state=('ok' if rc == 0 else 'failed'),
                delivery_state=_session_delivery(outcome),
                error_kind=error_kind,
                kind='ended', ts=int(time.time()),
            ),
        )
        session_ended = True

    def _close_epoch(
            epoch: ExecutionEpoch,
            turn: CommanderTurnResult) -> str:
        if not _epoch_identity_current(epoch):
            try:
                end_code = prov.code_version()
            except Exception:
                end_code = prov.CODE_UNKNOWN
            drift_kind = (
                'code_version_drift'
                if end_code != epoch.code_version
                else 'runtime_identity_drift')
            _end_current_session(
                outcome=AgentDeliveryOutcome(
                    state='unknown', checked_at=int(time.time()),
                    reason='execution epoch 身份漂移，旧 provider session 已封存'),
                rc=latest_commander_rc,
                error_kind=(latest_commander_error_kind or drift_kind),
                end_code_version=end_code)
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
        # 同席重试复用已经 commit 的 launch ticket，必须另行记住终止请求；
        # 不能在换 driver 后丢掉旧 driver 上的 cancel 状态。
        commander_stop_requested.set()
        _cancel_commander_owner(run_id)
        _cancel_action_owner(run_id)
        # 安全边界上没有活动 owner 时，stop 仍已通过 launch ticket 阻止新 spawn；
        # 这里的布尔只表示取消请求已送达或当下无需取消，不决定 drain。
        return True

    def _set_commander_quiesced(value: bool) -> bool:
        """pause/stop 只封新 claim；已有 claim 由 turn finally 等到 release。"""
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

    def _run_commander_turn(
            epoch: ExecutionEpoch,
            provider_session_id: str | None,
            *, reviewer_prompt: str | None = None,
            reviewer_output_schema: dict[str, object] | None = None,
            fresh_session_run_id: str | None = None,
            launch_ticket: LaunchTicket | None = None,
            environment_overrides: dict[str, str] | None = None,
            after_spawn_before_wire: Callable[[], None] | None = None,
            on_registered: Callable[[], None] | None = None,
            activity: str | None = None,
            on_attempt_started: Callable[[str, str, str | None], None] | None = None,
            on_attempt_finished: Callable[
                [str, int, str | None, str | None, bool], None] | None = None,
            on_final_response: Callable[[str, dict[str, object]], None] | None = None,
            max_attempts: int = COMMANDER_MAX_ATTEMPTS_PER_SEAT,
            seat_provider_session_id: str | None = None,
            seat_turns_recorded: int = 0,
            seat_subject_sha256: str | None = None,
            seat_output_schema_sha256: str | None = None,
            close_seat: bool = True,
            ) -> CommanderTurnResult:
        nonlocal turn_index, latest_observation, latest_outcome
        nonlocal latest_rc, latest_error_kind, provider_turn_recorded
        nonlocal latest_commander_outcome, latest_commander_rc
        nonlocal latest_commander_error_kind
        nonlocal active_admission_id

        fresh_reviewer_session = fresh_session_run_id is not None
        if fresh_reviewer_session and reviewer_prompt is None:
            raise RuntimeError('只有 reviewer turn 可以声明 fresh logical session')
        audit_epoch = (
            epoch.model_copy(update={'session_run_id': fresh_session_run_id})
            if fresh_session_run_id is not None else epoch)
        # 席位续接：同一 fresh logical session 内的纠错轮续用本席 provider
        # session，不再落第二条 started；turn 序号从本席已记轮数继续。
        seat_continuation = (
            fresh_reviewer_session and seat_provider_session_id is not None)
        effective_provider_session_id = (
            seat_provider_session_id if fresh_reviewer_session
            else provider_session_id)
        # logical session 的 subject 是本席首轮 prompt；纠错续接轮 prompt 不同，
        # 但 session 静态身份不能漂移，因此 subject 取席位记住的首轮 SHA。
        fresh_prompt_sha256 = (
            (seat_subject_sha256 if seat_continuation and seat_subject_sha256
             else _sha256_bytes(reviewer_prompt.encode('utf-8')))
            if fresh_reviewer_session and reviewer_prompt is not None else None)
        fresh_session_started = False

        def start_fresh_reviewer_session() -> None:
            nonlocal fresh_session_started
            if (not fresh_reviewer_session or fresh_session_started
                    or seat_continuation):
                return
            assert fresh_prompt_sha256 is not None
            agent_session_audit.append_event(
                base,
                agent_session_audit.AgentSessionEvent(
                    session_run_id=audit_epoch.session_run_id,
                    parent_run_id=run_id,
                    role='commander', node=None, chapter=None,
                    subject_sha256=fresh_prompt_sha256,
                    profile=plan.profiles.commander,
                    requested_model=plan.profiles.commander.model,
                    requested_effort=plan.profiles.commander.reasoning_effort,
                    cli_version=current_session_invocation.cli_version,
                    protocol_version=current_session_invocation.protocol,
                    start_code_version=audit_epoch.code_version,

                    prompt_sha256=fresh_prompt_sha256,
                    schema_sha256=turn_schema_sha256,
                    kind='started', ts=int(time.time()),
                ),
            )
            fresh_session_started = True

        if reviewer_prompt is not None:
            turn_invocation = _review_invocation(
                epoch, reviewer_prompt, effective_provider_session_id,
                output_schema=reviewer_output_schema,
                expected_output_schema_sha256=(
                    seat_output_schema_sha256 if seat_continuation else None))
        elif effective_provider_session_id is None:
            turn_invocation = epoch_invocations[epoch.epoch_id]
        else:
            turn_invocation = _resume_invocation(
                epoch, effective_provider_session_id)
        # 普通 Commander 沿用 observation protocol 身份；structured reviewer
        # 一旦启用原生 schema，则 session pointer / attempt / turn / ended 必须
        # 全部记录真正送进 CLI 的动态 schema，而不是旧的协议常量 hash。
        turn_schema_sha256 = (
            turn_invocation.output_schema_sha256 or schema_sha256)
        admission_id = _open_action_admission(epoch)
        attempt_id = jobs.new_job_id()
        rc = CRASH
        raw_exit_known = False
        observation = CommanderObservation(
            provider_session_id=None, actual_model=None,
            actual_effort=None, usage=None)
        final_response: str | None = None
        runtime_error_kind: str | None = None
        process_error_kind: str | None = None
        integrity_error_kind: str | None = None
        lifecycle_error_kind: str | None = None
        completion_error_kind: str | None = None
        invoked = False
        registered = False
        turn_latency_ms: int | None = None
        durable_result: dict[str, object] | None = None
        drain = _ExecutionDrainResult(timed_out=False)
        # 同席 attempt 循环：一次 Commander turn 可以由多次真实进程尝试组成
        # （首发 + 因网络/瞬态故障的 resume/relaunch），它们共享同一 logical
        # session、同一席位与同一 admission；只有最后一次的事实进入 turn 结论。
        attempt_no = 0
        fresh_turn_counter = seat_turns_recorded if seat_continuation else 0
        resume_session: str | None = None
        attempt_invocation = turn_invocation
        current_attempt = {'id': attempt_id, 'kind': 'launch'}
        pointer_written: set[str] = set()
        recorded_cost_attempt_id: str | None = None
        recorded_turn_attempt_id: str | None = None

        class StoppedBeforeWire(Exception):
            """已创建重试进程，但还没有发送本次 prompt。"""

        def session_pointer_sink(event: object, session_id: str | None) -> None:
            nonlocal integrity_error_kind
            del event
            if session_id is None or session_id in pointer_written:
                return
            pointer_written.add(session_id)
            try:
                _write_session_pointer(base, sid, CommanderSessionPointer(
                    sid=sid, parent_run_id=run_id, epoch_id=epoch.epoch_id,
                    session_run_id=audit_epoch.session_run_id,
                    attempt_id=current_attempt['id'],
                    attempt_kind=current_attempt['kind'],
                    provider_session_id=session_id,
                    driver=plan.profiles.commander.driver,
                    model=plan.profiles.commander.model,
                    prompt_sha256=_sha256_bytes(
                        attempt_invocation.stdin_text.encode('utf-8')),
                    schema_sha256=turn_schema_sha256,
                    observed_at=int(time.time())))
            except Exception:  # noqa: BLE001 —— 已发生 wire 后关闭式拒绝交付
                integrity_error_kind = 'commander_session_pointer'
                _append_safe_traceback(
                    base, run_id, prompt=turn_invocation.stdin_text)

        def popen_registered() -> None:
            nonlocal invoked, registered, integrity_error_kind, spawn_started
            if commander_stop_requested.is_set():
                raise StoppedBeforeWire()
            # 总槽等待发生在 Popen 前，不计入 Commander 的实际执行区间。
            # 该回调仍早于 stdin/wire；后面的账本 CAS 与原身份守卫保持同一顺序。
            spawn_started = time.monotonic()
            # reviewer workpack 在 operator next/build_task 时绑定了本
            # execution epoch。从构包末端到真实 Popen 仍有窗口；
            # 必须在任何 seat/budget reserve、logical-session started、
            # attempt/ticket 和 stdin 之前再做一次 code+identity CAS。Popen
            # 已创建不等于已 wire，callback 抛出后公共 runner 会在
            # 发送 prompt 前终止进程树。
            if (reviewer_prompt is not None
                    and not _epoch_identity_current(epoch)):
                raise CommanderConfigurationError(
                    'reviewer 发送前 execution epoch code/identity 已漂移；'
                    '本次保持 0-seat/0-wire 并由 Supervisor 重开 epoch')
            # 默认 runner 只在 Popen 成功后、stdin/prompt 发出前触发；同席后续
            # attempt 也会再次触发，但席位预留、session started、launch ticket
            # 只属于第一次。Stage2 预算因此既不会被 env/Popen 的 0-wire 失败吞掉，
            # 又会在任何模型字节出进程前先持久化 started。
            if not registered:
                # 预算/席位预留可能关闭式拒绝。那时虽然 OS 进程已经创建，stdin 仍是
                # 0-wire，logical session 也尚未成立；不能先把它标成 invoked，否则
                # finally 会在没有 started 的 session_run_id 下追加孤立 attempt，继而
                # 让全 base 的 append-only 审计账都无法折叠。
                if after_spawn_before_wire is not None:
                    after_spawn_before_wire()
                if reviewer_prompt is not None:
                    if fresh_reviewer_session:
                        start_fresh_reviewer_session()
                    else:
                        _open_epoch(
                            epoch,
                            actual_prompt_sha256=_sha256_bytes(
                                turn_invocation.stdin_text.encode('utf-8')),
                        )
                # 到这里 logical session 已有 started（或是既有席位的续轮），后续
                # on_registered / launch ticket 即使异常，也必须按一次可审计 attempt
                # 收口。因此 invoked/registered 在它们之前、却在预留与 started 之后置位。
                invoked = True
                registered = True
                if on_registered is not None:
                    on_registered()
                if launch_ticket is not None:
                    launch_ticket.commit()
            if on_attempt_started is not None:
                # attempt/seat 账属于模型 wire 的前置凭证；失败必须让 launch
                # callback 原样抛出，公共 runner 会在 stdin 前终止进程树。
                try:
                    on_attempt_started(
                        current_attempt['id'], current_attempt['kind'],
                        resume_session)
                except BaseException:  # noqa: BLE001 —— 必须标成完整性故障
                    integrity_error_kind = 'commander_attempt_ledger'
                    raise

        def attempt_event(
                attempt_rc: int, failure: str | None,
                attempt_observation: CommanderObservation) -> agent_session_audit.AgentSessionEvent:
            return agent_session_audit.AgentSessionEvent(
                session_run_id=audit_epoch.session_run_id,
                parent_run_id=run_id,
                role='commander', node=None, chapter=None,
                subject_sha256=(
                    fresh_prompt_sha256
                    if fresh_prompt_sha256 is not None
                    else current_session_prompt_sha256),
                profile=plan.profiles.commander,
                provider_session_id=(
                    attempt_observation.provider_session_id),
                attempt_id=current_attempt['id'],
                delivery_attempts=attempt_no,
                requested_model=plan.profiles.commander.model,
                actual_model=attempt_observation.actual_model,
                requested_effort=(
                    plan.profiles.commander.reasoning_effort),
                actual_effort=attempt_observation.actual_effort,
                cli_version=attempt_invocation.cli_version,
                protocol_version=attempt_invocation.protocol,
                start_code_version=epoch.code_version,

                prompt_sha256=_sha256_bytes(
                    attempt_invocation.stdin_text.encode('utf-8')),
                schema_sha256=turn_schema_sha256,
                usage=_session_usage(attempt_observation),
                error_kind=(
                    None if attempt_rc == 0
                    else (failure or 'commander_process_exit')),
                kind='attempt', ts=int(time.time()),
            )

        def append_attempt_audit(
                attempt_rc: int, failure: str | None,
                attempt_observation: CommanderObservation) -> None:
            nonlocal integrity_error_kind
            try:
                agent_session_audit.append_event(
                    base,
                    (agent_session_audit.AgentSessionEvent.from_dict(durable_result['attempt_event'])
                     if durable_result is not None and attempt_rc == 0
                     else attempt_event(attempt_rc, failure, attempt_observation)),
                )
            except Exception:  # noqa: BLE001 —— 已发生 wire，禁止消费回答
                integrity_error_kind = 'commander_attempt_audit'
                _append_safe_traceback(
                    base, run_id, prompt=turn_invocation.stdin_text)

        try:
            log = run_log_path(base, run_id)
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, 'ab') as fh:
                child_env = env_with_context(
                    run_id=run_id, base=base, start=start_payload,
                    contract=contract_payload, epoch=epoch,
                    admission_id=admission_id, env=os.environ)
                if environment_overrides:
                    child_env.update(environment_overrides)
                child_env.update(_model_runtime_env(base, sid, plan.profiles))
                with _COMMANDERS_LOCK:
                    _COMMANDER_EVENT_SINKS[run_id] = session_pointer_sink
                while True:
                    if commander_stop_requested.is_set():
                        break
                    previous_attempt_id = attempt_id
                    previous_invocation = attempt_invocation
                    raw_exit_known = False
                    attempt_no += 1
                    if attempt_no > 1:
                        attempt_id = jobs.new_job_id()
                        # 上一棵进程树已结束；换 driver 前先释放它的 owner。
                        _close_commander_owner(run_id)
                        if resume_session is None:
                            attempt_invocation = turn_invocation
                        elif reviewer_prompt is not None:
                            attempt_invocation = _review_invocation(
                                epoch, _REVIEWER_RESUME_PROMPT, resume_session,
                                output_schema=reviewer_output_schema,
                                expected_output_schema_sha256=(
                                    turn_invocation.output_schema_sha256))
                        else:
                            attempt_invocation = _resume_invocation(
                                epoch, resume_session)
                    current_attempt['id'] = attempt_id
                    current_attempt['kind'] = (
                        'launch' if resume_session is None else 'resume')
                    fh.write(('$ ' + shell_line(attempt_invocation.argv) + '\n')
                             .encode('utf-8'))
                    fh.flush()
                    # R-11.23 的第一道身份 CAS 在创建 CLI 进程前；
                    # build_task 末端到这里若已漂移，连 Popen 都不做。
                    # ``popen_registered`` 顶部仍保留第二道 CAS，封住
                    # 本检查与 Popen/stdin 之间的 TOCTOU 窗口。
                    if (reviewer_prompt is not None
                            and not _epoch_identity_current(epoch)):
                        rc = CRASH
                        runtime_error_kind = 'commander_epoch_identity'
                        process_error_kind = 'commander_epoch_identity'
                        break
                    if spawn is None:
                        # 所有生产 Commander 都走同一 Popen 后回调；否则 executable
                        # 不存在也会被成本账误记为一次真实调用。
                        commander_spawn = _default_spawn(
                            run_id, on_registered=popen_registered)
                        register_custom_spawn = False
                    else:
                        # custom spawn 是测试/legacy 注入缝；调用方把它视为已经完成
                        # Popen 登记、尚未发送 stdin 的同一生命周期点。
                        commander_spawn = spawn
                        register_custom_spawn = True
                    # 只量 Commander 子进程本身：模型往返与它内部的工具调用都在
                    # 这段墙钟里；之后的 execution drain 属于 Supervisor，不混进来。
                    spawn_started = time.monotonic()
                    attempt_rc = CRASH
                    attempt_observation = CommanderObservation(
                        provider_session_id=None, actual_model=None,
                        actual_effort=None, usage=None)
                    attempt_final_response: str | None = None
                    attempt_failure: str | None = None
                    attempt_retryable = False
                    attempt_wire = False
                    attempt_crashed = False
                    stopped_before_wire = False
                    try:
                        # 注入 spawn 没有真实 runner 的 Popen 回调；仍把登记放进
                        # 本 attempt 的 try/finally，使账本前置凭证失败也能形成
                        # 明确的失败 attempt，而不是跳过全部收口。
                        if register_custom_spawn:
                            popen_registered()
                        result = commander_spawn(attempt_invocation, child_env, fh)
                        if not isinstance(result, CommanderProcessResult):
                            raise TypeError(
                                'Commander spawn 必须返回 CommanderProcessResult，'
                                '不能只给退出码丢掉供应商观测')
                        if not isinstance(result.observation, CommanderObservation):
                            raise TypeError(
                                'CommanderProcessResult.observation 必须是 '
                                'CommanderObservation，不能传自由 dict')
                        attempt_rc = result.exit_code
                        raw_exit_known = True
                        attempt_observation = result.observation
                        attempt_failure = result.failure_kind
                        attempt_retryable = bool(result.retryable)
                        attempt_wire = bool(result.wire_evidence)
                        attempt_final_response = result.final_response
                        if attempt_rc == 0 and attempt_final_response is not None and on_final_response is not None:
                            # 原回答先保存，再闭会话/成本账；提交或审计失败不能迫使重买回答。
                            received_event = attempt_event(0, None, attempt_observation)
                            received_turn = replace(
                                received_event, kind='turn', delivery_attempts=None,
                                turn_index=fresh_turn_counter + 1)
                            durable_result = {
                                'kind': 'commander-result-receipt-v1',
                                'request_prompt': attempt_invocation.stdin_text,
                                'logical_prompt_sha256': _sha256_bytes(reviewer_prompt.encode('utf-8')),
                                'attempt_event': received_event.to_dict(),
                                'turn_event': received_turn.to_dict(),
                                'cost_kwargs': dict(
                                    run_id=run_id, session_run_id=audit_epoch.session_run_id,
                                    sid=sid, attempt_id=current_attempt['id'],
                                    request_model=plan.profiles.commander.model,
                                    requested_effort=plan.profiles.commander.reasoning_effort,
                                    code=epoch.code_version, protocol_version=attempt_invocation.protocol,
                                    actual_model=attempt_observation.actual_model,
                                    actual_effort=attempt_observation.actual_effort,
                                    provider_session_id=attempt_observation.provider_session_id,
                                    cli_version=attempt_invocation.cli_version,
                                    usage=_usage_dict(attempt_observation), outcome='ok', exit_code=0,
                                    error_kind=None, latency_ms=int((time.monotonic() - spawn_started) * 1000),
                                    activity=activity,
                                    response_sha256=_reviewer_response_sha256(attempt_final_response)),
                            }
                            on_final_response(attempt_final_response, durable_result)
                    except StoppedBeforeWire:
                        # 旧尝试已经结账；这个零 wire 进程没有预留新 attempt，
                        # 继续以最后一次真实调用收口，不能制造第二笔未知成本。
                        stopped_before_wire = True
                        attempt_id = previous_attempt_id
                        attempt_invocation = previous_invocation
                        current_attempt['id'] = attempt_id
                        if not invoked:
                            rc = 2
                            process_error_kind = 'commander_stopped'
                    except BaseException as exc:  # noqa: BLE001 —— 崩了也要闭合本 attempt
                        attempt_crashed = True
                        known_rc = getattr(exc, 'returncode', None)
                        if isinstance(known_rc, int) and not isinstance(known_rc, bool):
                            attempt_rc = known_rc
                            raw_exit_known = True
                        partial = getattr(exc, 'partial_observation', None)
                        if isinstance(partial, CommanderObservation):
                            attempt_observation = partial
                        failure = getattr(exc, 'failure_kind', None)
                        attempt_failure = failure if isinstance(failure, str) else None
                        attempt_retryable = bool(getattr(exc, 'retryable', False))
                        attempt_wire = bool(getattr(exc, 'wire_evidence', False))
                        _append_safe_traceback(
                            base, run_id, prompt=turn_invocation.stdin_text)
                    finally:
                        if not stopped_before_wire:
                            turn_latency_ms = int(
                                (time.monotonic() - spawn_started) * 1000)
                    if stopped_before_wire:
                        break
                    if on_attempt_finished is not None:
                        try:
                            on_attempt_finished(
                                current_attempt['id'], attempt_rc, attempt_failure,
                                attempt_observation.provider_session_id,
                                attempt_wire)
                        except Exception:  # noqa: BLE001
                            integrity_error_kind = 'commander_attempt_ledger'
                            attempt_retryable = False
                            attempt_final_response = None
                            _append_safe_traceback(
                                base, run_id, prompt=turn_invocation.stdin_text)
                    if invoked:
                        append_attempt_audit(
                            attempt_rc, attempt_failure, attempt_observation)
                    # stop 也可能发生在退避期间；保留最后一次真实返回，供
                    # 循环直接退出后统一收口，已写的成本/turn 下方不重复追加。
                    rc = attempt_rc
                    observation = attempt_observation
                    process_error_kind = attempt_failure
                    retry = (
                        invoked and attempt_rc != 0 and attempt_retryable
                        and attempt_no < max_attempts
                        and not commander_stop_requested.is_set()
                        and integrity_error_kind is None)
                    if not retry:
                        rc = attempt_rc
                        observation = attempt_observation
                        final_response = (
                            attempt_final_response
                            if attempt_rc == 0
                            and integrity_error_kind is None else None)
                        process_error_kind = attempt_failure
                        if attempt_crashed:
                            runtime_error_kind = (
                                'commander_protocol'
                                if attempt_failure == 'protocol'
                                else 'commander_runtime')
                        break
                    # 中间失败尝试也是一次真实付费/审计事实：成本账逐次落行；
                    # 已取得 provider session 的失败尝试再记一条带 error_kind 的
                    # provider turn，供 fold 把同席续接联结到同一 logical session。
                    intermediate_kind = attempt_failure or 'commander_process_exit'
                    try:
                        append_commander_call(
                            base, run_id=run_id,
                            session_run_id=audit_epoch.session_run_id,
                            sid=sid, attempt_id=current_attempt['id'],
                            request_model=plan.profiles.commander.model,
                            requested_effort=(
                                plan.profiles.commander.reasoning_effort),
                            code=epoch.code_version,
                            protocol_version=attempt_invocation.protocol,
                            actual_model=attempt_observation.actual_model,
                            actual_effort=attempt_observation.actual_effort,
                            provider_session_id=(
                                attempt_observation.provider_session_id),
                            cli_version=attempt_invocation.cli_version,
                            usage=_usage_dict(attempt_observation),
                            outcome='error', exit_code=attempt_rc,
                            error_kind=intermediate_kind,
                            latency_ms=turn_latency_ms,
                            activity=activity)
                        recorded_cost_attempt_id = attempt_id
                    except Exception:  # noqa: BLE001
                        integrity_error_kind = 'commander_ledger'
                        _append_safe_traceback(
                            base, run_id, prompt=turn_invocation.stdin_text)
                    if integrity_error_kind is not None:
                        rc = attempt_rc
                        observation = attempt_observation
                        process_error_kind = intermediate_kind
                        break
                    reuse_owner: str | None = None
                    if attempt_observation.provider_session_id is not None:
                        if fresh_reviewer_session:
                            fresh_turn_counter += 1
                            intermediate_index = fresh_turn_counter
                        else:
                            turn_index += 1
                            intermediate_index = turn_index
                        try:
                            reuse_owner = agent_session_audit.append_commander_turn(
                                base,
                                agent_session_audit.AgentSessionEvent(
                                    session_run_id=audit_epoch.session_run_id,
                                    parent_run_id=run_id,
                                    role='commander', node=None, chapter=None,
                                    subject_sha256=(
                                        fresh_prompt_sha256
                                        if fresh_prompt_sha256 is not None
                                        else current_session_prompt_sha256),
                                    profile=plan.profiles.commander,
                                    provider_session_id=(
                                        attempt_observation.provider_session_id),
                                    turn_index=intermediate_index,
                                    attempt_id=current_attempt['id'],
                                    requested_model=plan.profiles.commander.model,
                                    actual_model=attempt_observation.actual_model,
                                    requested_effort=(
                                        plan.profiles.commander.reasoning_effort),
                                    actual_effort=attempt_observation.actual_effort,
                                    cli_version=attempt_invocation.cli_version,
                                    protocol_version=attempt_invocation.protocol,
                                    start_code_version=epoch.code_version,

                                    prompt_sha256=_sha256_bytes(
                                        attempt_invocation.stdin_text.encode('utf-8')),
                                    schema_sha256=turn_schema_sha256,
                                    usage=_session_usage(attempt_observation),
                                    error_kind=intermediate_kind,
                                    kind='turn', ts=int(time.time()),
                                ),
                            )
                            if not fresh_reviewer_session and reuse_owner is None:
                                provider_turn_recorded = True
                            if reuse_owner is None:
                                recorded_turn_attempt_id = attempt_id
                        except Exception:  # noqa: BLE001
                            integrity_error_kind = 'commander_session_audit'
                            _append_safe_traceback(
                                base, run_id, prompt=turn_invocation.stdin_text)
                    if integrity_error_kind is not None:
                        rc = attempt_rc
                        observation = attempt_observation
                        process_error_kind = intermediate_kind
                        break
                    if reuse_owner is not None:
                        # provider session 属于别的 logical session：不能拿它续接。
                        rc = attempt_rc
                        observation = attempt_observation
                        integrity_error_kind = (
                            agent_session_audit.PROVIDER_SESSION_REUSED)
                        break
                    resume_session = (
                        attempt_observation.provider_session_id
                        if attempt_wire else None)
                    backoff = COMMANDER_RETRY_BACKOFF_SECONDS[
                        min(attempt_no - 1, len(COMMANDER_RETRY_BACKOFF_SECONDS) - 1)]
                    fh.write((
                        f'$ [commander-retry] attempt={attempt_no} '
                        f'failure={intermediate_kind} '
                        f'resume={resume_session!r} backoff={backoff:g}s\n'
                    ).encode('utf-8'))
                    fh.flush()
                    _retry_sleep(backoff)
        except BaseException as exc:  # noqa: BLE001 —— 崩了也要闭合本 turn
            known_rc = getattr(exc, 'returncode', None)
            raw_exit_known = False
            if isinstance(known_rc, int) and not isinstance(known_rc, bool):
                rc = known_rc
                raw_exit_known = True
            runtime_error_kind = 'commander_runtime'
            _append_safe_traceback(base, run_id, prompt=turn_invocation.stdin_text)
        finally:
            with _COMMANDERS_LOCK:
                _COMMANDER_EVENT_SINKS.pop(run_id, None)
            drain = _wait_for_execution_closure(
                base, admission_id, beacon_run_id=run_id)
            with active_admission_lock:
                if active_admission_id == admission_id:
                    active_admission_id = None
            epoch_drain_states[epoch.epoch_id] = (
                'unknown' if drain.timed_out else 'drained')
            try:
                _close_commander_owner(run_id)
            except Exception:  # noqa: BLE001 —— audit 仍须继续
                _append_safe_traceback(
                    base, run_id, prompt=turn_invocation.stdin_text)

        if runtime_error_kind is not None or rc != 0:
            failure = runtime_error_kind or process_error_kind \
                or 'commander_process_exit'
            outcome = AgentDeliveryOutcome(
                state='unknown', checked_at=int(time.time()),
                reason=(
                    'Commander 未形成可信成功终态：'
                    f'{failure}（exit={rc}）。当前盘面仅作快照，'
                    '不得用下游缺产物覆盖这个直接故障'))
        elif drain.timed_out:
            lifecycle_error_kind = 'commander_execution_barrier'
            outcome = AgentDeliveryOutcome(
                state='unknown', checked_at=int(time.time()),
                reason=(drain.timeout_reason
                        or 'Commander execution drain 曾超过报警阈值'))
        else:
            outcome = _assess_delivery(
                base, sid, code_version=epoch.code_version)

        if (observation.actual_model is not None
                and not reported_model_matches(plan.profiles.commander, observation.actual_model)):
            integrity_error_kind = 'commander_model_drift'
        if (observation.actual_effort is not None
                and observation.actual_effort
                != plan.profiles.commander.reasoning_effort):
            integrity_error_kind = (
                integrity_error_kind or 'commander_effort_drift')

        continue_ready = False
        if (rc == 0
                and lifecycle_error_kind is None
                and integrity_error_kind is None
                and runtime_error_kind is None):
            completion = commander_completion.review_commander_stop(
                base, sid, outcome, process_exit_code=rc)
            outcome = completion.outcome
            completion_error_kind = completion.error_kind
            continue_ready = completion.continue_ready
            if not _epoch_identity_current(epoch) and rc == 0:
                try:
                    continue_ready = commander_completion.ready_action_remains(
                        base, sid)
                except Exception as exc:  # noqa: BLE001
                    continue_ready = False
                    integrity_error_kind = 'commander_next_action_unavailable'
                    outcome = AgentDeliveryOutcome(
                        state='unknown', checked_at=int(time.time()),
                        reason=(
                            'execution epoch 身份漂移后无法读取 operator next：'
                            f'{type(exc).__name__}: {exc}'))

        error_kind = (
            lifecycle_error_kind or integrity_error_kind
            or completion_error_kind or runtime_error_kind
            or process_error_kind
            or (None if rc == 0 else 'commander_process_exit'))

        if invoked and recorded_cost_attempt_id != attempt_id:
            try:
                if durable_result is not None:
                    append_commander_call(base, **durable_result['cost_kwargs'])
                else:
                    append_commander_call(
                        base, run_id=run_id,
                        session_run_id=audit_epoch.session_run_id,
                        sid=sid, attempt_id=attempt_id,
                        request_model=plan.profiles.commander.model,
                        requested_effort=(
                            plan.profiles.commander.reasoning_effort),
                        code=epoch.code_version,
                        protocol_version=attempt_invocation.protocol,
                        actual_model=observation.actual_model,
                        actual_effort=observation.actual_effort,
                        provider_session_id=(
                            observation.provider_session_id),
                        cli_version=attempt_invocation.cli_version,
                        usage=_usage_dict(observation),
                        outcome=('ok' if rc == 0 else 'error'),
                        exit_code=rc, error_kind=error_kind,
                        latency_ms=turn_latency_ms,
                        activity=activity,
                        response_sha256=(
                            _reviewer_response_sha256(final_response)
                            if reviewer_prompt is not None
                            and final_response is not None else None))
            except Exception:  # noqa: BLE001
                integrity_error_kind = 'commander_ledger'
                error_kind = integrity_error_kind
                _append_safe_traceback(
                    base, run_id, prompt=turn_invocation.stdin_text)

        provider_reuse_owner: str | None = None
        this_turn_recorded = recorded_turn_attempt_id == attempt_id
        if observation.provider_session_id is not None and not this_turn_recorded:
            if fresh_reviewer_session:
                fresh_turn_counter += 1
                audit_turn_index = fresh_turn_counter
            else:
                turn_index += 1
                audit_turn_index = turn_index
            try:
                provider_reuse_owner = (
                    agent_session_audit.append_commander_turn(
                        base,
                        (agent_session_audit.AgentSessionEvent.from_dict(durable_result['turn_event'])
                         if durable_result is not None else agent_session_audit.AgentSessionEvent(
                            session_run_id=audit_epoch.session_run_id,
                            parent_run_id=run_id,
                            role='commander', node=None, chapter=None,
                            subject_sha256=(
                                fresh_prompt_sha256
                                if fresh_prompt_sha256 is not None
                                else current_session_prompt_sha256),
                            profile=plan.profiles.commander,
                            provider_session_id=(
                                observation.provider_session_id),
                            turn_index=audit_turn_index,
                            attempt_id=attempt_id,
                            requested_model=(
                                plan.profiles.commander.model),
                            actual_model=observation.actual_model,
                            requested_effort=(
                                plan.profiles.commander.reasoning_effort),
                            actual_effort=observation.actual_effort,
                            cli_version=attempt_invocation.cli_version,
                            protocol_version=attempt_invocation.protocol,
                            start_code_version=epoch.code_version,

                            prompt_sha256=_sha256_bytes(
                                attempt_invocation.stdin_text.encode('utf-8')),
                            schema_sha256=turn_schema_sha256,
                            usage=_session_usage(observation),
                            error_kind=error_kind,
                            kind='turn', ts=int(time.time()),
                        )),
                    )
                )
                this_turn_recorded = provider_reuse_owner is None
                if not fresh_reviewer_session:
                    provider_turn_recorded = (
                        provider_turn_recorded or this_turn_recorded)
            except Exception:  # noqa: BLE001
                integrity_error_kind = 'commander_session_audit'
                error_kind = integrity_error_kind
                continue_ready = False
                _append_safe_traceback(
                    base, run_id, prompt=turn_invocation.stdin_text)

        if provider_reuse_owner is not None:
            integrity_error_kind = (
                agent_session_audit.PROVIDER_SESSION_REUSED)
            error_kind = integrity_error_kind
            continue_ready = False
            outcome = AgentDeliveryOutcome(
                state='unknown', checked_at=int(time.time()),
                reason=(
                    'Commander 返回的 provider session '
                    f'已属于 logical session {provider_reuse_owner!r}；'
                    '拒绝把串会话的运行判为可交付'))
        elif integrity_error_kind is not None:
            outcome = AgentDeliveryOutcome(
                state='unknown', checked_at=int(time.time()),
                reason=(
                    '盘面终验已经执行，但 Commander 身份/审计链不闭合：'
                    f'{integrity_error_kind}；拒绝把运行判为可交付'))

        if integrity_error_kind is not None:
            # 成功文本只有在 attempt/cost/session/pointer 全链闭合时才可交给
            # typed loader；否则即使 provider exit 0 也只是不可采纳的半边事实。
            final_response = None

        if fresh_session_started and close_seat:
            try:
                assert fresh_prompt_sha256 is not None
                agent_session_audit.append_event(
                    base,
                    agent_session_audit.AgentSessionEvent(
                        session_run_id=audit_epoch.session_run_id,
                        parent_run_id=run_id,
                        role='commander', node=None, chapter=None,
                        subject_sha256=fresh_prompt_sha256,
                        profile=plan.profiles.commander,
                        provider_session_id=(
                            observation.provider_session_id
                            if this_turn_recorded else None),
                        requested_model=plan.profiles.commander.model,
                        actual_model=observation.actual_model,
                        requested_effort=plan.profiles.commander.reasoning_effort,
                        actual_effort=observation.actual_effort,
                        cli_version=turn_invocation.cli_version,
                        protocol_version=turn_invocation.protocol,
                        start_code_version=audit_epoch.code_version,
                        end_code_version=(
                            prov.code_version()
                            if _epoch_identity_current(epoch)
                            else prov.CODE_UNKNOWN),

                        prompt_sha256=fresh_prompt_sha256,
                        schema_sha256=turn_schema_sha256,
                        process_state=('ok' if rc == 0 else 'failed'),
                        delivery_state=_session_delivery(outcome),
                        error_kind=error_kind,
                        kind='ended', ts=int(time.time()),
                    ),
                )
            except Exception:  # noqa: BLE001 —— 不能把审计失败洗成可交付
                integrity_error_kind = 'commander_session_audit'
                error_kind = integrity_error_kind
                continue_ready = False
                outcome = AgentDeliveryOutcome(
                    state='unknown', checked_at=int(time.time()),
                    reason='Stage2 reviewer 新 logical session 未能闭合审计')
                _append_safe_traceback(
                    base, run_id, prompt=turn_invocation.stdin_text)

        # 对普通 Commander，完整性故障保留真实进程 rc，并由
        # outcome=unknown 关闭交付。对 structured reviewer，调用者还有
        # 一条“exit 0 但从 legacy scratch 取文件”的测试兼容路；若
        # 审计链断裂仍返回 0，就可能绕过 final_response 禁用。
        # 因此 reviewer 完整性故障必须投影为 CRASH，禁止 loader/执行。
        reported_rc = (
            CRASH
            if reviewer_prompt is not None and integrity_error_kind is not None
            else rc)
        if reported_rc != rc:
            final_response = None
            outcome = AgentDeliveryOutcome(
                state='unknown', checked_at=int(time.time()),
                reason=(
                    'Reviewer Commander 进程虽已返回，但身份/席位/'
                    f'审计链不闭合：{integrity_error_kind}；'
                    '已禁止消费其 structured 回答'))

        latest_observation = observation
        latest_outcome = outcome
        latest_rc = reported_rc
        latest_error_kind = error_kind
        latest_commander_outcome = outcome
        latest_commander_rc = rc
        latest_commander_error_kind = error_kind
        return CommanderTurnResult(
            exit_code=reported_rc,
            exit_origin=(
                'commander' if raw_exit_known and reported_rc == rc else 'supervisor'),
            provider_session_id=(
                observation.provider_session_id
                if this_turn_recorded and not fresh_reviewer_session else None),
            continue_ready=continue_ready,
            outcome=outcome,
            seat_provider_session_id=(
                observation.provider_session_id
                if fresh_reviewer_session and this_turn_recorded
                else seat_provider_session_id),
            seat_turns_recorded=fresh_turn_counter,
            seat_started=fresh_session_started or seat_continuation,
            final_response=final_response)

    def _end_reviewer_seat(
            seat: _ReviewerSeat, *, error_kind: str | None) -> None:
        """给一席 fresh reviewer session 幂等补 ended；未落 started 的席位没有事实可闭。"""
        if not seat.started:
            return
        identity = commander_work_unit(seat.waiting_on)
        if identity is not None:
            budget = WorkUnitBudget(base, sid, identity[0], 'commander', seat.work_unit_scope or identity[1])
            pending = budget.latest_pending_response()
            if pending is not None and pending[1].get('session_run_id') == seat.session_run_id:
                try:
                    from novel.work_units import reconcile_commander_response
                    reconcile_commander_response(budget, pending[0].turn_id, read_only=True)
                except Exception:
                    # 已保存完整回答但尾账未闭时不得先 ended；重启用原证明补尾。
                    return
        seat.started = False
        observation = seat.last_observation or CommanderObservation(
            provider_session_id=None, actual_model=None,
            actual_effort=None, usage=None)
        outcome = seat.last_outcome or AgentDeliveryOutcome(
            state='unknown', checked_at=int(time.time()),
            reason='reviewer 席位在形成可审计结果前关闭')
        try:
            agent_session_audit.append_event(
                base,
                agent_session_audit.AgentSessionEvent(
                    session_run_id=seat.session_run_id,
                    parent_run_id=run_id,
                    role='commander', node=None, chapter=None,
                    subject_sha256=seat.prompt_sha256,
                    profile=plan.profiles.commander,
                    provider_session_id=seat.provider_session_id,
                    requested_model=plan.profiles.commander.model,
                    actual_model=observation.actual_model,
                    requested_effort=plan.profiles.commander.reasoning_effort,
                    actual_effort=observation.actual_effort,
                    cli_version=current_session_invocation.cli_version,
                    protocol_version=current_session_invocation.protocol,
                    start_code_version=current_session_code_version,
                    end_code_version=(
                        prov.code_version()
                        if prov.code_version() == current_session_code_version
                        else prov.CODE_UNKNOWN),

                    prompt_sha256=seat.prompt_sha256,
                    schema_sha256=seat.output_schema_sha256 or schema_sha256,
                    process_state=('ok' if seat.last_rc == 0 else 'failed'),
                    delivery_state=_session_delivery(outcome),
                    error_kind=error_kind or seat.last_error_kind,
                    kind='ended', ts=int(time.time()),
                ),
            )
        except Exception:  # noqa: BLE001 —— 审计失败留痕，不杀 run
            _append_safe_traceback(base, run_id, prompt=plan.prompt)

    def _close_reviewer_seat(
            binding: tuple[str, str] | None, *,
            error_kind: str | None = None) -> None:
        if binding is None:
            return
        seat = open_reviewer_seats.pop(binding, None)
        if seat is not None:
            _end_reviewer_seat(seat, error_kind=error_kind)

    def _close_open_reviewer_seats(*, error_kind: str | None) -> None:
        for binding in list(open_reviewer_seats):
            _close_reviewer_seat(binding, error_kind=error_kind)

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
        _reconcile_completed_work_unit_actions(
            base, sid, code=epoch.code_version, current_parent_run_id=run_id)
        _reconcile_landed_stage2_work_unit_actions(
            base, sid, code=epoch.code_version, current_parent_run_id=run_id)
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
            provider_session_id: str | None,
            launch_ticket: LaunchTicket) -> SupervisorActionResult:
        """所有生产只执行状态机当前许可；Stage2 不再并行发起预审。"""
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
            # 但不能被空 flags 解释成默认付费写作，也不能转交 Commander 重买一席。
            return SupervisorActionResult(
                exit_code=2, continue_ready=False, provider_session_id=None,
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
        if (
            action.waiting_on == queue.WAITING_ON_STAGE2_EDIT_COMMIT
            and recovered.exit_code == 2
        ):
            # reviewed-edit WAL 补尾可以以 terminal sidecar 明确结束：
            # 这不是再停一次等人点“继续”，而是同一 canonical
            # action 已经得到拒因。planner 前 reconcile 会先把它绑回
            # 原 Stage2 席位并退休 action record；只有现役状态已精确
            # 转到 correction 才继续循环。仍是 commit blocker 或任何其他
            # 状态时保持 exit2 停点，避免确定性死循环。
            live = _read_operator_next(epoch)
            live_waiting_on = (
                live.action.waiting_on
                if live.state == 'blocked' and live.action is not None
                else None)
            if live_waiting_on == queue.WAITING_ON_STAGE2_EDIT_CORRECTION:
                return replace(recovered, continue_ready=True)
        return recovered

    def _run_approval_action(
            epoch: ExecutionEpoch,
            action: reviewer_tasks.ReviewerApprovalAction,
            launch_ticket: LaunchTicket) -> SupervisorActionResult:
        rc, timed_out, failure_detail = _run_supervisor_process(
            epoch, _reviewer_approval_argv(base, sid, epoch, action),
            launch_ticket, label=f'reviewer-{action.surface}')
        return _action_outcome(
            epoch, rc, timed_out, label=f'reviewer-{action.surface}',
            failure_detail=failure_detail)

    def _run_node_review_action(
            epoch: ExecutionEpoch,
            action: reviewer_tasks.ReviewerNodeAction,
            launch_ticket: LaunchTicket,
            *, reviewer_action_record_path: Path | None = None,
            ) -> SupervisorActionResult:
        return _execute_reviewer_node_action(
            base, sid, action,
            lambda permit: _run_token(
                epoch, permit, launch_ticket,
                stem=f'reviewer-{action.node}'),
            reviewer_action_record_path=reviewer_action_record_path)

    open_reviewer_seats: dict[tuple[str, str], _ReviewerSeat] = {}

    def _run_reviewer(
            epoch: ExecutionEpoch, current: OperatorNext,
            provider_session_id: str | None,
            launch_ticket: LaunchTicket) -> CommanderTurnResult | None:
        """同业务单元连续审阅；回答、决定、正式提交分别持久化。"""
        nonlocal latest_outcome, latest_rc, latest_error_kind
        assert current.action is not None
        waiting_on = current.action.waiting_on
        if waiting_on is None:
            return None
        identity = commander_work_unit(waiting_on)
        if identity is None:
            return None
        node, scope = identity

        def stopped(reason: str, *, unknown: bool = False) -> CommanderTurnResult:
            nonlocal latest_outcome, latest_rc, latest_error_kind
            latest_rc = CRASH if unknown else 2
            latest_error_kind = 'reviewer_work_unit_unknown' if unknown else 'reviewer_work_unit_stopped'
            latest_outcome = AgentDeliveryOutcome(
                state='unknown' if unknown else 'stopped',
                checked_at=int(time.time()), reason=reason)
            return CommanderTurnResult(exit_code=latest_rc, provider_session_id=None,
                                       continue_ready=False, outcome=latest_outcome)

        scratch = service_lifecycle.open_scratch_session(base, sid, epoch.epoch_id)
        request_path = scratch / f'reviewer-request-{jobs.new_job_id()}.json'
        try:
            task = reviewer_tasks.build_task(base, sid, current, request_path, epoch_id=epoch.epoch_id)
        except reviewer_tasks.ReviewerTaskStopped as exc:
            return stopped(f'{exc.waiting_on} 工作包明确停止：{exc.reason}')
        if task is None:
            return None
        scope = task.work_unit_scope or scope
        budget = WorkUnitBudget(base, sid, node, 'commander', scope)
        binding = (budget.unit_id, scope)
        previous_turn = budget.latest_turn()
        if previous_turn is not None and previous_turn.state == 'adopted' and previous_turn.recovery_sha256:
            previous_decision = budget.recovery_proof(previous_turn.turn_id)
            prior_action = previous_decision.get('action', {})
            decision = prior_action.get('payload', {}).get('decision')
            if prior_action.get('kind') in ('stop', 'uncertain') or decision in ('uncertain', 'stop'):
                return stopped('该工作单元已有明确停止决定：' + str(
                    prior_action.get('reason') or prior_action.get('payload', {}).get('reason')))
            if previous_decision.get('business_sha256') == task.business_sha256:
                return stopped('同一业务主体的审阅决定已执行，当前没有新的审阅进展')
        feedback = budget.latest_feedback(business_sha256=task.business_sha256)
        if feedback is not None and (
                feedback.get('business_sha256') != task.business_sha256
                or feedback.get('waiting_on') != waiting_on):
            feedback = None
        prompt = task.prompt
        if feedback is not None:
            prompt = reviewer_tasks.correction_prompt(
                task, str(feedback['reason']),
                request_sha256=str(feedback['response_sha256']), attempt=budget.used)
        wire = (stage4_reviewer_wire if stage4_reviewer_wire.uses_compact_wire(task)
                else stage2_reviewer_wire)
        reviewer_prompt = wire.prompt_for(task, prompt)
        output_schema = wire.output_schema_for(task)
        output_schema_digest = _prepare_output_schema(output_schema)[2]
        schema_recovery_proof = None
        failure_recovery_validated = False
        if (previous_turn is not None and previous_turn.response_sha256 is None
                and previous_turn.state in ('unknown', 'failed')):
            parents = [item for item in records(base) if item.run_id == previous_turn.run_id]
            if len(parents) == 1 and previous_turn.run_id != run_id:
                with admission.require_owner_stopped(base, previous_turn.run_id):
                    # latest_turn 的权威读路已校验失败收口凭据的原始字节与原账；
                    # 后续锁内 reserve 仍会重验。新运行不必伪造 schema 修复，
                    # 也不重签原凭据、续旧供应商会话或退还旧内容轮。
                    failure_recovery_validated = (
                        previous_turn.state == 'failed'
                        and previous_turn.reason_code == COMMANDER_FAILURE_RECONCILED)
                    if not failure_recovery_validated and output_schema_digest is not None:
                        schema_recovery_proof = reviewer_schema_recovery.inspect_work_unit_rejection(
                            base, sid, budget=budget, parent=parents[0],
                            log_path=run_log_path(base, previous_turn.run_id), profile=plan.profiles.commander,
                            replacement_schema_sha256=output_schema_digest,
                            validation_code_version=epoch.code_version,
                            replacement_subject_sha256=_sha256_bytes(reviewer_prompt.encode('utf-8')))
                        if schema_recovery_proof is not None:
                            budget.finish(previous_turn.turn_id, outcome='failed', reason_code='schema_recovery_validated')
            if schema_recovery_proof is None and not failure_recovery_validated:
                return stopped('上次模型请求没有可恢复回答；缺精确配置修复证明，保留原额度并停止')
        request_sha256 = _sha256_bytes(json.dumps({
            'business_sha256': task.business_sha256, 'waiting_on': task.waiting_on,
            'schema_sha256': output_schema_digest, 'feedback': feedback,
        }, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8'))
        pending = budget.latest_pending_response()
        if pending is not None:
            from novel.work_units import reconcile_commander_response
            original_turn = budget.turn(pending[0].turn_id)
            if original_turn.run_id == run_id:
                reconcile_commander_response(budget, pending[0].turn_id)
            else:
                with admission.require_owner_stopped(base, original_turn.run_id):
                    reconcile_commander_response(budget, pending[0].turn_id)
                    sessions = [item for item in agent_session_audit.fold(
                        agent_session_audit.read_events(base))
                        if item.session_run_id == original_turn.session_run_id]
                    if len(sessions) != 1:
                        raise WorkUnitRefused('恢复回答缺唯一原 Commander 会话')
                    restored_session = sessions[0]
                    if restored_session.end is None:
                        original = restored_session.turns[-1]
                        agent_session_audit.append_event(base, replace(
                            restored_session.start, kind='ended', ts=int(time.time()),
                            provider_session_id=original.provider_session_id,
                            actual_model=original.actual_model, actual_effort=original.actual_effort,
                            process_state='ok', delivery_state='unknown',
                            end_code_version=(epoch.code_version
                                if epoch.code_version == original.start_code_version else prov.CODE_UNKNOWN)))
        active_records = uploads.load_reviewer_actions(base, sid)
        revalidation = None
        original_request = None
        if pending is not None and waiting_on in reviewer_revalidation.WAITING_ON:
            original_request = budget.request_payload(pending[0].turn_id)
            if original_request.get('business_sha256') != pending[1].get('business_sha256'):
                raise WorkUnitRefused('原请求与已购回答的业务身份不一致')
            if (budget.turn(pending[0].turn_id).run_id != run_id
                    or original_request.get('epoch_id') != epoch.epoch_id):
                try:
                    revalidation = reviewer_revalidation.prepare(
                        original_request, task, schema_sha256=output_schema_digest or schema_sha256)
                except reviewer_revalidation.RevalidationRefused as exc:
                    raise WorkUnitRefused(str(exc)) from exc
        pending_record: tuple[Path, ReviewerActionRecord] | None = None
        pending_records: list[tuple[Path, ReviewerActionRecord]] = []
        for path, record in active_records:
            if record.work_unit_id != budget.unit_id:
                continue
            row = budget.turn(record.work_unit_turn_id)
            if row.state == 'failed':
                # 拒因先持久、记录后退休；重启补齐两写之间的中断。
                uploads.discard_edit_plan(base, path)
                continue
            validate_reviewer_record(base, sid, record)
            if pending is not None and record.work_unit_turn_id == pending[0].turn_id:
                if pending_record is not None and revalidation is None:
                    raise WorkUnitRefused('同一回答出现多个活动 typed 决定记录')
                pending_record = (path, record)
                pending_records.append((path, record))
        submitted_patch_recovery = False
        partial_batch_recovery = False
        if pending is not None and pending[1].get('business_sha256') != task.business_sha256:
            if pending_record is not None:
                _path, record = pending_record
                if record.node == 'stage2.blueprint' and record.input_payload is not None:
                    from novel.stages.stage2.plot_edit import submitted_edit_is_current
                    submitted_patch_recovery = submitted_edit_is_current(
                        base, sid, record.input_payload, require_action_complete=False)
                elif record.node == 'stage2.global' and record.input_payload is not None:
                    if '--review-decisions' in record.flags:
                        from novel.stages.stage2.global_review import submitted_decisions_is_current
                        submitted_patch_recovery = submitted_decisions_is_current(base, sid, record.input_payload)
                    else:
                        from novel.stages.stage2.extract import submitted_global_patch_is_current
                        submitted_patch_recovery = submitted_global_patch_is_current(base, sid, record.input_payload)
                if record.node == 'stage5.review' and record.flags == ('--retain-pretrim-batch', '@reviewer-input'):
                    partial = reviewer_tasks.stage5_trim_partial_replay_options(
                        base, sid, record, current, task.submission_options,
                        current_review_sha256=task.stage5_review_sha256)
                    if partial is not None and partial.state == 'fully_landed':
                        budget.mark_adopted(pending[0].turn_id)
                        uploads.discard_edit_plan(base, _path)
                        return CommanderTurnResult(exit_code=0, provider_session_id=None,
                                                   continue_ready=True, outcome=latest_outcome)
                    if partial is not None and partial.state == 'replayable':
                        partial_batch_recovery = True
                        task = replace(task, business_sha256=record.business_sha256,
                                       submission_options=partial.submission_options)
            if (not submitted_patch_recovery and revalidation is None
                    and pending[1].get('business_sha256') != task.business_sha256):
                # 当前候选确已被其他正式动作替代；旧回答失效仍保留已用额度。
                budget.mark_rejected(pending[0].turn_id, reason_code='subject_superseded')
                for path, _record in pending_records:
                    uploads.discard_edit_plan(base, path)
                pending = None

        def rejected(reservation: ContentReservation, digest: str, reason: str) -> CommanderTurnResult:
            repeated = feedback is not None and feedback.get('response_sha256') == digest
            budget.save_feedback(reservation.turn_id, dict(
                business_sha256=task.business_sha256, waiting_on=waiting_on,
                reason=reason, response_sha256=digest))
            if repeated:
                return stopped('Commander 收到反馈后重复提交同一回答，已停止无进展循环：' + reason)
            if budget.remaining == 0:
                return stopped('Commander 内容预算已用尽；仍可免费复验或人工编辑：' + reason)
            return CommanderTurnResult(
                exit_code=2, provider_session_id=None, continue_ready=True,
                continue_reviewer=True, outcome=AgentDeliveryOutcome(
                    state='stopped', checked_at=int(time.time()), reason=reason))

        if pending is not None:
            reservation, saved = pending
            response = saved.get('final_response')
            session_id = saved.get('session_run_id')
            if not isinstance(response, str) or not isinstance(session_id, str):
                return stopped('保存的 Commander 回答缺完整文本或原会话身份', unknown=True)
            # 首次 loader 与重启后的重验共用同一边界。记录先于 scratch 晋升；
            # 工作包只改变 epoch 包装时，以已验 typed 决定重建当前 transport 包装。
            record_path: Path | None = None
            def persist(action: reviewer_tasks.ReviewerAction, digest: str) -> Path | None:
                nonlocal record_path
                candidate_proof = dict(
                    business_sha256=task.business_sha256, waiting_on=task.waiting_on,
                    action_sha256=reviewer_tasks.action_business_sha256(action),
                    provider_response_sha256=digest, action=asdict(action))
                if revalidation is not None and budget.turn(reservation.turn_id).recovery_sha256:
                    existing_proof = budget.recovery_proof(reservation.turn_id)
                    assert original_request is not None
                    if not reviewer_revalidation.proof_matches(
                            original_request, existing_proof,
                            business_sha256=task.business_sha256, waiting_on=task.waiting_on,
                            action=asdict(action), provider_response_sha256=digest):
                        raise WorkUnitRefused('免费重验不得更改原 typed 决定')
                else:
                    budget.save_recovery(reservation.turn_id, candidate_proof)
                # 重建 transport 包装与 typed loader 会先后调用本回调；必须复用
                # 同一持久记录，否则跨秒的 recorded_at 会遗留第二份活动决定。
                if record_path is None:
                    # 后继记录绑定当前 token，原 typed proof 与原回答仍不可变。
                    # 先写并验证新记录，再退休旧记录；中断后可逐份验证同源记录补尾。
                    record_path = (pending_record[0] if pending_record is not None and revalidation is None
                                   else _record_reviewer_action(
                                       base, sid, run_id=run_id, epoch=epoch, task=task, action=action,
                                       reviewer_session_run_id=session_id, provider_response_sha256=digest,
                                       work_unit_id=budget.unit_id, work_unit_turn_id=reservation.turn_id))
                if record_path is not None:
                    records_found = [(path, record) for path, record in uploads.load_reviewer_actions(base, sid)
                                     if path == record_path]
                    if len(records_found) != 1:
                        raise WorkUnitRefused('同一回答没有唯一活动 typed 决定记录')
                    validate_reviewer_record(base, sid, records_found[0][1])
                    if revalidation is not None:
                        for old_path, old_record in pending_records:
                            # 每份源记录均已由 validate_reviewer_record 验过原 immutable
                            # proof，不能把别的回答或语义不同的动作顺带退休。
                            if old_record.provider_response_sha256 != digest:
                                raise WorkUnitRefused('免费重验出现不同来源的活动决定')
                            if old_path != record_path:
                                uploads.discard_edit_plan(base, old_path)
                return record_path
            try:
                try:
                    proof = budget.recovery_proof(reservation.turn_id)
                except WorkUnitRefused:
                    proof = None
                    # 缺证明允许从原回答重验；已有证明损坏不得绕过。
                    turn_row = budget.turn(reservation.turn_id)
                    if turn_row.recovery_sha256 is not None:
                        raise
                if submitted_patch_recovery:
                    assert pending_record is not None
                    record_path, record = pending_record
                    action = reviewer_tasks.ReviewerNodeAction(
                        kind='node', node=record.node, flags=tuple(record.flags),
                        input_payload=record.input_payload)
                    digest = record.provider_response_sha256
                elif revalidation is not None:
                    document, digest = _reviewer_response_document(response)
                    try:
                        document = revalidation.rebind(document)
                    except reviewer_revalidation.RevalidationRefused as exc:
                        raise _ReviewerResponseTypedInvalid(str(exc)) from exc
                    action, digest, record_path = _materialize_reviewer_document(
                        task, current, document, response_digest=digest,
                        persist_validated=persist,
                        preserve_validated=lambda candidate, digest:
                        _preserve_validated_reviewer_residue(base, sid, candidate, digest))
                elif proof is not None:
                    row = proof['action']
                    if row['kind'] in ('stop', 'uncertain'):
                        action = reviewer_tasks.ReviewerStopAction(**row)
                        digest = str(proof['provider_response_sha256'])
                    else:
                        if row['kind'] == 'node':
                            rebuilt = reviewer_tasks.ReviewerNodeAction(
                                kind='node', node=row['node'], flags=tuple(row['flags']),
                                input_payload=row.get('input_payload'))
                        else:
                            rebuilt = reviewer_tasks.ReviewerApprovalAction(**row)
                        digest = str(proof['provider_response_sha256'])
                        record_path = persist(rebuilt, digest)
                        assert record_path is not None
                        records_found = [record for path, record in uploads.load_reviewer_actions(base, sid)
                                         if path == record_path]
                        if len(records_found) != 1:
                            raise WorkUnitRefused('已保存的 typed 决定没有唯一内容地址')
                        document = reviewer_tasks.replay_request_document(records_found[0], current)
                        action, digest, record_path = _materialize_reviewer_document(
                            task, current, document, response_digest=digest, persist_validated=persist)
                else:
                    action, digest, record_path = _materialize_reviewer_response(
                        task, current, response, persist_validated=persist,
                        preserve_validated=lambda candidate, digest:
                        _preserve_validated_reviewer_residue(base, sid, candidate, digest))
            except _ReviewerResponseTypedInvalid as exc:
                return rejected(reservation, _reviewer_response_sha256(response), str(exc))
            if isinstance(action, reviewer_tasks.ReviewerStopAction):
                budget.mark_adopted(reservation.turn_id)
                conclusion = '不确定' if action.kind == 'uncertain' else '明确停止'
                return stopped(f'Commander {conclusion} {waiting_on}：' + action.reason)
            try:
                if isinstance(action, reviewer_tasks.ReviewerNodeAction):
                    _preflight_stage2_reviewer_action(base, sid, epoch=epoch, action=action)
                    result = _run_node_review_action(
                        epoch, action, launch_ticket, reviewer_action_record_path=record_path)
                else:
                    result = _run_approval_action(epoch, action, launch_ticket)
            except _Stage2ReviewerPreflightRejected as exc:
                return rejected(reservation, digest, exc.safe_summary)
            except Exception:
                live = _read_operator_next(epoch)
                if live.surface_seal != current.surface_seal:
                    return CommanderTurnResult(exit_code=2, provider_session_id=None,
                                               continue_ready=True, outcome=latest_outcome)
                raise
            # exit 1 只证明产物落盘；审批未落盘仍保留原决定供 producer 免费补提交。
            if result.exit_code == 0:
                if (isinstance(action, reviewer_tasks.ReviewerNodeAction)
                        and action.node == 'stage5.review'
                        and '--request-reviewer-rewrite' in action.flags):
                    _reconcile_landed_stage5_reviewer_rewrite_action(
                        base, sid, code=epoch.code_version, current_parent_run_id=run_id)
                budget.mark_adopted(reservation.turn_id)
                if record_path is not None:
                    uploads.discard_edit_plan(base, record_path)
                if (isinstance(action, reviewer_tasks.ReviewerApprovalAction)
                        and action.payload.get('decision') in ('uncertain', 'stop')):
                    return stopped(f'Commander 对 {waiting_on} 明确{action.payload["decision"]}：'
                                   + str(action.payload.get('reason', '')))
            elif result.exit_code == 2:
                live = _read_operator_next(epoch)
                if partial_batch_recovery or submitted_patch_recovery:
                    detail = '：' + result.failure_detail if result.failure_detail else ''
                    return stopped('已保存决定的免费补提交尚未闭合，保留原回答' + detail)
                if live.surface_seal == current.surface_seal:
                    detail = result.failure_detail or 'canonical producer 返回 exit 2，明确拒绝 typed 动作'
                    if any(word in detail.lower() for word in ('permission', 'authorization', '权限', '授权')):
                        return stopped(detail)
                    return rejected(reservation, digest, detail)
                return CommanderTurnResult(exit_code=2, provider_session_id=None,
                                           continue_ready=True, outcome=result.outcome)
            return CommanderTurnResult(
                exit_code=result.exit_code, exit_origin=result.exit_origin,
                provider_session_id=None, continue_ready=result.continue_ready,
                outcome=result.outcome)

        from novel.work_units import require_commander_scope_closed
        require_commander_scope_closed(base, sid, node, scope)
        if budget.remaining == 0:
            return stopped('Commander 当前工作单元有效内容额度已用尽；可免费复验或人工编辑，追加额度须由操作者另行授权')
        if feedback is not None and waiting_on in queue.STAGE2_REVIEWER_WAITING_ON:
            # 纠错上下文只服务下一次内容调用。原回答已保存时先走上面的免费
            # 补提交，不因更早的拒稿材料损坏或 blocker 变化而夺走恢复能力。
            rejected_responses = budget.rejected_responses(
                business_sha256=task.business_sha256, waiting_on=waiting_on)
            if not rejected_responses or any(
                    _reviewer_response_sha256(row['final_response']) != row['response_sha256']
                    for row in rejected_responses):
                return stopped('纠错上下文缺原回答或与持久拒因摘要不一致', unknown=True)
            prompt = reviewer_tasks.correction_prompt(
                task, str(feedback['reason']),
                request_sha256=str(feedback['response_sha256']), attempt=budget.used,
                rejected_responses=rejected_responses)
            reviewer_prompt = wire.prompt_for(task, prompt)
        seat = open_reviewer_seats.get(binding)
        if seat is not None and seat.output_schema_sha256 != output_schema_digest:
            _close_reviewer_seat(binding, error_kind='reviewer_schema_changed')
            seat = None
        if seat is None:
            seat = _ReviewerSeat(
                session_run_id=jobs.new_job_id(), prompt_sha256=_sha256_bytes(reviewer_prompt.encode('utf-8')),
                waiting_on=waiting_on, business_sha256=task.business_sha256,
                output_schema_sha256=output_schema_digest, work_unit_scope=scope)
            open_reviewer_seats[binding] = seat
        reservation: ContentReservation | None = None
        reserve_error: BaseException | None = None
        def reserve() -> None:
            nonlocal reservation, reserve_error
            try:
                reservation = budget.reserve(
                    request_sha256=request_sha256, run_id=run_id, session_run_id=seat.session_run_id,
                    request_payload=dict(prompt=reviewer_prompt, output_schema=output_schema,
                                         work_unit_scope=scope,
                                         business_sha256=task.business_sha256, waiting_on=task.waiting_on,
                                         workpack_sha256=task.workpack_sha256, epoch_id=epoch.epoch_id,
                                         schema_sha256=output_schema_digest or schema_sha256,
                                         schema_recovery=(schema_recovery_proof.model_dump(mode='json')
                                                          if schema_recovery_proof is not None else None)))
            except BaseException as exc:
                reserve_error = exc
                raise
        def attempt_started(attempt_id: str, kind: str, resume_id: str | None) -> None:
            if reservation is None:
                raise WorkUnitRefused('Commander attempt 不能先于内容轮次预留')
            budget.record_attempt(reservation.turn_id, attempt_id=attempt_id,
                                  outcome='started', provider_session_id=resume_id)
        def attempt_finished(attempt_id: str, rc: int, error: str | None,
                             provider_id: str | None, wire_evidence: bool) -> None:
            if reservation is not None:
                budget.record_attempt(reservation.turn_id, attempt_id=attempt_id,
                                      outcome='ok' if rc == 0 else 'error', provider_session_id=provider_id)
        saved_receipt: dict[str, object] | None = None
        def save_raw(response: str, receipt: dict[str, object] | None = None) -> None:
            nonlocal saved_receipt
            if receipt is not None:
                saved_receipt = receipt
            if reservation is None:
                raise WorkUnitRefused('完整回答没有对应已预留的内容请求')
            budget.save_response(reservation.turn_id, dict(
                final_response=response, session_run_id=seat.session_run_id,
                business_sha256=task.business_sha256, waiting_on=task.waiting_on,
                prepared_receipt=saved_receipt))
        try:
            turn = _run_commander_turn(
                epoch, None, reviewer_prompt=reviewer_prompt, reviewer_output_schema=output_schema,
                fresh_session_run_id=seat.session_run_id, seat_provider_session_id=seat.provider_session_id,
                seat_turns_recorded=seat.turns_recorded, seat_subject_sha256=seat.prompt_sha256,
                seat_output_schema_sha256=seat.output_schema_sha256, close_seat=False,
                launch_ticket=launch_ticket, after_spawn_before_wire=reserve, activity=_reviewer_activity(task),
                on_attempt_started=attempt_started, on_attempt_finished=attempt_finished,
                on_final_response=save_raw)
        except BaseException:
            if reservation is not None:
                budget.finish(reservation.turn_id, outcome='unknown', reason_code='commander_interrupted')
            raise
        seat.started = seat.started or turn.seat_started
        seat.provider_session_id = turn.seat_provider_session_id or seat.provider_session_id
        seat.turns_recorded = turn.seat_turns_recorded
        seat.last_observation = latest_observation
        seat.last_rc = latest_commander_rc
        seat.last_outcome = latest_commander_outcome
        seat.last_error_kind = latest_commander_error_kind
        if reserve_error is not None:
            return stopped(str(reserve_error))
        if reservation is None:
            return turn
        if turn.final_response is not None:
            save_raw(turn.final_response)
        elif turn.exit_code == 0:
            budget.finish(reservation.turn_id, outcome='unknown', reason_code='missing_final_response')
            return stopped('Commander 结束但没有完整最终回答，保留已用额度与未知状态', unknown=True)
        else:
            budget.finish(reservation.turn_id, outcome='unknown', reason_code='commander_transport_failed')
            return turn
        if turn.exit_code != 0:
            return turn
        # 下一次状态机窗口先重放已保存回答，然后才能开启下一个内容请求。
        return CommanderTurnResult(exit_code=0, provider_session_id=None,
                                   continue_ready=True, outcome=turn.outcome)


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

    deterministic_callbacks = ({
        'read_operator_next': _read_operator_next,
        'ready_requires_fresh_epoch': _ready_requires_fresh_epoch,
        'run_ready_action': _run_ready_action,
        'run_blocked_action': _run_blocked_action,
        'deterministic_blockers': queue.DETERMINISTIC_RECOVERY_WAITING_ON,
        'fresh_epoch_blockers': frozenset({
            queue.WAITING_ON_STAGE4_DESIGN_ACTION,
            queue.WAITING_ON_STAGE5_REVIEWER_REWRITE,
        }),
        'run_reviewer': _run_reviewer,
        'finalize_run': _finalize_run,
    } if spawn is None else {})
    supervisor = RunSupervisor(
        run_id=run_id,
        append_event=lambda event: append(base, event),
        make_epoch=_make_epoch,
        # v2 epoch 本身不是 Commander session；首个 reviewer Popen 后才由
        # launch callback 调 `_open_epoch`。custom spawn 保留旧测试缝。
        open_epoch=(lambda _epoch: None) if spawn is None else _open_epoch,
        close_epoch=_close_epoch,
        run_commander_turn=_run_commander_turn,
        epoch_identity_current=_epoch_identity_current,
        cancel_commander=_cancel_active,
        set_commander_quiesced=_set_commander_quiesced,
        initial_epoch=initial_epoch,
        **deterministic_callbacks)
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
                latest_error_kind = latest_error_kind or 'commander_stopped'
                if supervised.turn is None:
                    latest_rc = CRASH
        except BaseException:  # noqa: BLE001 —— Supervisor 崩溃也必须写 ended
            latest_rc = CRASH
            latest_exit_origin = 'supervisor'
            latest_error_kind = latest_error_kind or 'commander_supervisor'
            latest_outcome = AgentDeliveryOutcome(
                state='unknown', checked_at=int(time.time()),
                reason='RunSupervisor 生命周期异常；拒绝猜测交付状态')
            _append_safe_traceback(base, run_id, prompt=plan.prompt)
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
                try:
                    _close_open_reviewer_seats(
                        error_kind=latest_commander_error_kind)
                except Exception:  # noqa: BLE001
                    _append_safe_traceback(base, run_id, prompt=plan.prompt)
                try:
                    # code/identity rollover 已在 epoch_close 把旧 logical session
                    # 封存；这里只闭合最后仍活动的那一个，不能写第二条 ended。
                    _end_current_session(
                        outcome=latest_commander_outcome,
                        rc=latest_commander_rc,
                        error_kind=latest_commander_error_kind,
                        end_code_version=end_code_version)
                except Exception:  # noqa: BLE001
                    latest_outcome = AgentDeliveryOutcome(
                        state='unknown', checked_at=int(time.time()),
                        reason='Commander ended 审计未能闭合；拒绝判为可交付')
                    _append_safe_traceback(base, run_id, prompt=plan.prompt)
                append(base, AgentRunEvent(
                    wire_version=2, run_id=run_id, kind='ended',
                    ts=int(time.time()), exit_code=latest_rc,
                    exit_origin=latest_exit_origin, outcome=latest_outcome))
            finally:
                try:
                    _close_commander_owner(run_id)
                except Exception:
                    _append_safe_traceback(base, run_id, prompt=plan.prompt)
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
                            _append_safe_traceback(
                                base, run_id, prompt=plan.prompt)

    thread = threading.Thread(target=worker, name=f'novel-agent-{run_id}',
                              daemon=True)
    with _THREADS_LOCK:
        _THREADS[run_id] = thread
    try:
        thread.start()
    except BaseException as original:
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
        # v1 进程内兼容：升级过程中已经登记 driver、尚未登记 Supervisor
        # 的 run 仍可精确熄火；服务重启后二者都会丢失，依旧关闭式拒绝。
        with _COMMANDERS_LOCK:
            driver = _COMMANDERS.get(run_id)
        if driver is None:
            raise JobConflict(
                f'run_id={run_id!r} 没有本进程拥有的 Supervisor/driver（已结束，'
                '或服务重启后句柄已丢）')
        if not driver.cancel(run_id):
            raise JobConflict(
                f'run_id={run_id!r} 的 Commander driver 未确认 cancel')
    else:
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
