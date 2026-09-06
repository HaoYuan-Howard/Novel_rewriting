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
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import BinaryIO, Iterator, NamedTuple

from contracts.artifacts import (
    STAGE2_GLOBAL_EDIT, STAGE2_GLOBAL_EDIT_COMMIT,
    STAGE2_GLOBAL_EDIT_FAILURE, STAGE2_GLOBAL_REVIEW_DECISIONS,
)
from contracts.invariants import (
    AGENT_EXECUTOR_NODES, COMMANDER_MAX_ATTEMPTS_PER_SEAT,
    COMMANDER_RETRY_BACKOFF_SECONDS, COMMANDER_REVIEWER_MAX_CORRECTIONS,
    STAGE2_BLUEPRINT_MAX_CONCURRENCY,
)
from contracts.schema import decode_artifact
from contracts.schema.api import (
    ExecutionPermit, OperatorNext, Stage2FinalReviewRequest,
)
from contracts.schema.evaluation import GlobalReviewDecisionSet
from contracts.schema.stage2 import GlobalEdit, GlobalEditCommit, GlobalEditFailure
from contracts.schema.orchestration import (
    AgentDeliveryOutcome, AgentPending, AgentQueueView, AgentRejected, AgentRunEvent,
    AgentExecutorBackend, AgentProfile, AgentRunRecord, AgentRunStart,
    CommanderSessionPointer, ExecutionEpoch, ExecutorBackend,
    PipelineAgentProfiles, ReviewerActionRecord, RunContractV2,
    Stage2PrereviewRecord,
)
from novel import agent_session_audit, paths, prov, run_identity, skill_contract
from novel.agent_capabilities import (
    AgentProfileRefused, provider_for_driver, validate_pipeline_profiles,
)
from novel.appendlog import (
    append_line, append_line_under_process_lock, append_transaction,
)
from novel.cli.exit_codes import CRASH, landed
from novel.commander_context import (
    COMMANDER_ADMISSION_ID_ENV, COMMANDER_CONTRACT_SHA256_ENV,
    COMMANDER_EPOCH_ID_ENV,
    COMMANDER_EPOCH_SHA256_ENV, COMMANDER_RUN_ID_ENV, env_with_context,
    action_admission_id_belongs_to_epoch, stage2_prereview_admission_id,
)
from novel.commanders import (
    CommanderConfigurationError, CommanderDriver, CommanderInvocation,
    CommanderObservation, CommanderProcessResult, create_commander_driver,
)
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
)
from novel.orchestrator import (
    admission, commander_completion, execution_permit, jobs, operator, queue,
    review_seats, reviewer_seat_ledger, reviewer_tasks, service_lifecycle,
    stage2_review_ledger, stage2_reviewer_wire, state, uploads,
)
from novel.orchestrator.agent_supervisor import (
    CommanderTurnResult, LaunchTicket, PrelaunchIdentityDrift, RunSupervisor,
    SupervisorActionResult,
)
from novel.orchestrator.jobs import JobConflict, JobRegistry, service_dir
from novel.orchestrator.nodes import shell_line
from novel.store import ArtifactStore

RUNS_FILENAME = 'agent_runs.jsonl'
LOG_PREFIX = 'agent-'

#: 仓根：agent.py → orchestrator → novel → 仓根。agent 的 cwd 钉死在这里——
#: 两侧发现 shim、neutral `.workflow/skills/` 与 `-m novel.cli` 的 import 都吃这个 cwd。
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
_MAX_REVIEWER_CORRECTIONS = COMMANDER_REVIEWER_MAX_CORRECTIONS
_MAX_STAGE2_COMMANDER_TURNS = stage2_review_ledger.MAX_COMMANDER_CALLS
_MAX_STAGE2_EDIT_SEAT = stage2_review_ledger.MAX_EDIT_SEAT
_ACTION_FAILURE_TAIL_BYTES = 16 * 1024
_ACTION_FAILURE_DETAIL_CHARS = 4096

_THREADS_LOCK = threading.Lock()
_THREADS: dict[str, threading.Thread] = {}
_COMMANDERS_LOCK = threading.Lock()
_COMMANDERS: dict[str, CommanderDriver] = {}
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

    ⚠ 终审包的落点由 `paths.review_package_path` 现算，**不在这里拼第二份文件名**。
    书目录还没落地时（preview 可以在摄入之前调）拼不出书名，那时给出规则而不是
    猜一个名字——`state` 会打印书目录，届时按同一条规则得到同一个名字。
    """
    base_abs = os.path.abspath(os.fspath(base))
    code_version = code_version or prov.code_version()
    py = Path(sys.executable).as_posix()
    novel_dir = paths.find_novel_dir(base_abs, sid)
    #: ⚠ 成书名**按书名派生**，不是固定的 `book.txt`——唯一落点是 `paths.book_txt_rel`。
    #: 这一句此前硬编码了一个不存在的文件名：agent 会照 SKILL.md 自我纠正，
    #: 但 headless 下没有第二个人能纠正启动 prompt，而它是任务的第一句话。
    book_target = (
        paths.book_txt_rel(novel_dir) if novel_dir is not None
        else 'stage7/<书名>_rewriting.txt（书名规则同下）')
    review_target = (
        paths.review_package_path(novel_dir).as_posix() if novel_dir is not None
        else f'<书目录>/<书名>{paths.REVIEW_SUFFIX}'
             f'（书目录还没落地；`state {sid}` 会打印它，书名就是目录名去掉'
             f'平台前缀与 `_{sid}` 尾巴的那一段）')
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
    canonical_skill = (skill_contract.SKILL_REL / 'SKILL.md').as_posix()
    return f"""你是小说工作流的 Commander/Reviewer，不是调度器。仓根是
`{REPO_ROOT.as_posix()}`，审阅对象属于 sid={sid}、base=`{base_abs}`。

确定性 RunSupervisor 独占状态机、execution token、节点调度、Executor 调度、遥测与终审报告；
你只会在一个已登记的 reviewer blocker 上被唤醒。每一轮实际提示都会内嵌同一时点冻结的
immutable workpack 与唯一 typed request 落点。先逐项审阅，再做一次跨项复核，最后只提交
一份 typed 判断。不得启动任何小说节点或 Executor，不得修改正式产物、服务账或 Markdown，
不得从目录存在性、旧对话或退出码猜测 PASS；schema 提供 UNVER 时保持 UNVER，
否则只走当前 blocker 明示的关闭式出口，read-only 或材料不足不得制造付费动作。

本次 parent contract 的审计起点：code_version=`{code_version}`；成书目标=`{book_target}`；
终审包目标=`{review_target}`。这些目标仅供识别对象，不授予写权限。

付费授权：{payment_authorization}
正文外发授权：{egress_authorization}

冻结角色：
- Commander：{commander.driver}/{commander.model}/{commander.reasoning_effort}/{commander.execution_mode}
- 默认 Executor：{_backend_label(executor)}
- Executor 节点覆盖：{overrides}

本 run 冻结的 operator skill 为 `{canonical_skill}`（整树指纹已入 epoch 审计）。受托 reviewer 轮次
只以随轮提示下发的 reviewer 契约与 workpack 明示的 review contract 为准，不读取该 skill；
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
        profiles: PipelineAgentProfiles | None) -> AgentRunPlan:
    """preview、单本启动与批量入队的唯一 planner；不发模型请求、不写盘。"""
    return _plan_resolved(
        base, sid, authorized=authorized,
        egress_authorized=egress_authorized,
        profiles=_freeze_profiles(profiles))


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
            'started': None, 'ended': None, 'exit_code': None,
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
                ended=e.ts, exit_code=e.exit_code, outcome=e.outcome,
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
                     'landed_red' if slot['exit_code'] == 1 else
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
                COMMANDER_ADMISSION_ID_ENV):
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
        with _COMMANDERS_LOCK:
            _COMMANDERS[run_id] = driver
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
        # Stage2 只读预审与 blueprint 子进程可被 Supervisor 放进同一份
        # 跨进程总槽；普通 Commander 没有环境契约时严格保持原行为。
        with model_slot_from_environment(runtime_env):
            return driver.run(runtime_invocation, log=log, run_id=run_id)

    return go


def _close_commander_owner(run_id: str) -> None:
    """drain 闭合后才归还 driver owner；custom spawn 没有 owner。"""
    with _COMMANDERS_LOCK:
        driver = _COMMANDERS.pop(run_id, None)
    if driver is not None:
        driver.close()


def _cancel_commander_owner(run_id: str) -> str:
    """超时只尝试 cancel，不据返回值猜 claims/executions 已结束。"""
    with _COMMANDERS_LOCK:
        driver = _COMMANDERS.get(run_id)
    if driver is None:
        return '没有可用 Commander driver owner（custom spawn 或句柄已丢）'
    try:
        confirmed = driver.cancel(run_id)
    except Exception as exc:  # noqa: BLE001 —— 只作报警证据，drain 仍继续
        return f'Commander cancel 异常：{type(exc).__name__}: {exc}'
    return ('Commander driver 已确认 cancel 请求'
            if confirmed else 'Commander driver 未确认 cancel 请求')


def _cancel_action_owner(run_id: str) -> bool:
    """取消 Supervisor 直接启动的 canonical runner/finalizer 进程树。"""
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
def _reviewer_node_flags(
        base: str, sid: str, action: reviewer_tasks.ReviewerNodeAction,
) -> Iterator[list[str]]:
    """把可信 typed payload 物化成 permit 与真实 CLI 共用的 exact flags。"""
    flags = list(action.flags)
    if action.input_payload is None:
        yield flags
        return
    if action.node != 'stage2.global':
        raise RuntimeError(f'{action.node} 尚未登记 reviewer 外部输入物化入口')
    with _stage2_reviewer_input(base, sid, action) as payload_path:
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
) -> SupervisorActionResult:
    """在一次性输入仍存活时铸 permit 并同步跑完整 canonical runner。"""
    with _reviewer_node_flags(base, sid, action) as flags:
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
    runner = (
        REPO_ROOT / '.workflow' / 'skills' / 'novel-pipeline-operator'
        / 'scripts' / 'run_step.py')
    return [
        sys.executable, '-B', str(runner),
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
    if action.surface == 'stage2_final_review':
        tail = [
            'approval', 'close-stage2', sid, '--base', base,
            '--input', str(payload_path)]
    else:
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


@dataclass(frozen=True)
class _ReplayableStage2Prereview:
    """盘上一份仍与当前 Global 业务面完全同一的未绑定预审决定。"""

    path: Path
    record: Stage2PrereviewRecord


@dataclass(frozen=True)
class _Stage2PrereviewState:
    """同一 parent 内一次失败预审的纠错坐标；成功决定只认 durable record。"""

    task: reviewer_tasks.Stage2PrereviewTask
    session_run_id: str
    error: str | None
    response_sha256: str | None = None


def _reviewer_activity(task: reviewer_tasks.ReviewerTask) -> str:
    """给成本账一枚人读分段；生产 node 身份仍保持 ``None``。"""
    option_ids = {
        str(option.get('id')) for option in task.submission_options
        if isinstance(option, dict)
    }
    if task.waiting_on == queue.WAITING_ON_STAGE2_FINAL_REVIEW:
        return 'stage2.global.final-review'
    if task.waiting_on in queue.STAGE2_REVIEWER_WAITING_ON:
        if any('apply-edit' in option_id for option_id in option_ids):
            return 'stage2.global.review-or-edit'
        return 'stage2.global.review'
    return f'reviewer:{task.waiting_on}'


def _record_reviewer_action(
        base: str, sid: str, *, run_id: str, epoch: ExecutionEpoch,
        task: reviewer_tasks.ReviewerTask,
        action: reviewer_tasks.ReviewerAction,
        reviewer_session_run_id: str | None = None,
        provider_response_sha256: str | None = None,
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
    if task.waiting_on in queue.STAGE2_REVIEWER_WAITING_ON:
        effective_progress = reviewer_progress_sha256 or task.progress_sha256
        metadata = (
            task.workpack_sha256, task.source_sha256, task.main_text_sha256,
            effective_progress, task.blocker_count,
            reviewer_session_run_id, provider_response_sha256,
        )
        if not all(value is not None for value in metadata):
            raise RuntimeError(
                'Stage2 typed-valid 决定缺 workpack/source/main/progress/'
                'blocker/席位/provider 回答身份')
        stage2_fields = {
            'workpack_sha256': task.workpack_sha256,
            'source_sha256': task.source_sha256,
            'main_text_sha256': task.main_text_sha256,
            'progress_sha256': effective_progress,
            'blocker_count': task.blocker_count,
            'reviewer_session_run_id': reviewer_session_run_id,
            'provider_response_sha256': provider_response_sha256,
        }
    if isinstance(action, reviewer_tasks.ReviewerApprovalAction):
        token = action.payload.get('review_token')
        record = ReviewerActionRecord(
            **common,
            surface=action.surface, payload=dict(action.payload),
            review_token=(
                token if isinstance(token, str) and token.strip() else None),
            **stage2_fields)
    elif isinstance(action, reviewer_tasks.ReviewerNodeAction):
        token = None
        try:
            token_index = action.flags.index('--review-token') + 1
            token = action.flags[token_index]
        except (ValueError, IndexError):
            pass
        record = ReviewerActionRecord(
            **common,
            node=action.node, flags=tuple(action.flags),
            input_payload=(
                dict(action.input_payload)
                if action.input_payload is not None else None),
            review_token=(
                token if isinstance(token, str) and token.strip() else None),
            **stage2_fields)
    else:  # pragma: no cover —— ReviewerAction union 的未来扩展须显式接线
        raise TypeError(f'未接线的 reviewer action：{type(action).__name__}')
    return uploads.save_reviewer_action(base, sid, record)


def _discard_reviewer_action_record(base: str, path: Path) -> None:
    try:
        uploads.discard_edit_plan(base, path)
    except NovelError as exc:
        warnings.warn(
            f'reviewer 决定记录清理失败：{exc}', RuntimeWarning, stacklevel=2)


def _stage2_prereview_identity(
        task: reviewer_tasks.ReviewerTask
        | reviewer_tasks.Stage2PrereviewTask,
        ) -> tuple[str, str, str, str, str, int] | None:
    """返回可跨 parent 证明同一预审主体的完整稳定身份。"""
    values = (
        task.business_sha256, task.workpack_sha256,
        task.source_sha256, task.main_text_sha256,
        task.progress_sha256,
    )
    if (not all(isinstance(value, str) for value in values)
            or not isinstance(task.blocker_count, int)):
        return None
    return (
        str(task.business_sha256), str(task.workpack_sha256),
        str(task.source_sha256), str(task.main_text_sha256),
        str(task.progress_sha256), task.blocker_count,
    )


def _record_stage2_prereview(
        base: str, sid: str, *, run_id: str, epoch: ExecutionEpoch,
        task: reviewer_tasks.Stage2PrereviewTask,
        input_payload: dict[str, object], reviewer_session_run_id: str,
        provider_response_sha256: str) -> Path:
    """typed prereview loader 一通过就保存未绑定决定，而不是伪造 live action。"""
    identity = _stage2_prereview_identity(task)
    if identity is None:
        raise RuntimeError('Stage2 typed-valid 预审缺完整 durable 恢复身份')
    (business_sha256, workpack_sha256, source_sha256,
     main_text_sha256, progress_sha256, blocker_count) = identity
    return uploads.save_stage2_prereview(base, sid, Stage2PrereviewRecord(
        sid=sid, run_id=run_id, epoch_id=epoch.epoch_id,
        business_sha256=business_sha256,
        workpack_sha256=workpack_sha256,
        source_sha256=source_sha256,
        main_text_sha256=main_text_sha256,
        progress_sha256=progress_sha256,
        blocker_count=blocker_count,
        reviewer_session_run_id=reviewer_session_run_id,
        provider_response_sha256=provider_response_sha256,
        input_payload=dict(input_payload), recorded_at=int(time.time())))


def _find_replayable_stage2_prereview(
        base: str, sid: str, *,
        task: reviewer_tasks.ReviewerTask
        | reviewer_tasks.Stage2PrereviewTask,
        ) -> _ReplayableStage2Prereview | None:
    """查找完全同一业务面的未绑定预审；失配清理，多份命中关闭式拒绝。"""
    records = uploads.load_stage2_prereviews(base, sid)
    if not records:
        return None
    identity = _stage2_prereview_identity(task)
    if identity is None:
        raise reviewer_tasks.ReviewerTaskError(
            '当前 Stage2 workpack 缺完整身份，不能判定持久预审是否适用')
    matches: list[tuple[Path, Stage2PrereviewRecord]] = []
    for path, record in records:
        record_identity = (
            record.business_sha256, record.workpack_sha256,
            record.source_sha256, record.main_text_sha256,
            record.progress_sha256, record.blocker_count,
        )
        if record_identity == identity:
            matches.append((path, record))
        else:
            _discard_reviewer_action_record(base, path)
    if len(matches) > 1:
        raise reviewer_tasks.ReviewerTaskError(
            '同一 Stage2 业务/workpack 身份存在多份未绑定预审记录；'
            '无法唯一证明应消费哪一席')
    if not matches:
        return None
    path, record = matches[0]
    # record 不能替席位账自证主体：先核原 turn 的全量身份。provider exit 0
    # + typed loader 后才会有本记录，所以新 parent 可幂等补完崩溃窗里的
    # ledger.finish；补完返回值仍必须是 ok/0，才允许进入 live bind。
    try:
        ledger_path = stage2_review_ledger.ledger_path(
            base, sid, source_sha256=record.source_sha256,
            main_text_sha256=record.main_text_sha256)
        _raw, archive = stage2_review_ledger.read_any_version(ledger_path)
        turns = [
            turn for turn in archive.turns
            if turn.session_run_id == record.reviewer_session_run_id]
        if len(turns) != 1:
            raise reviewer_tasks.ReviewerTaskError(
                'Stage2 持久预审找不到唯一的原席位账')
        turn = turns[0]
        if (
            turn.purpose != 'prereview'
            or turn.business_sha256 != record.business_sha256
            or turn.workpack_sha256 != record.workpack_sha256
            or turn.progress_sha256 != record.progress_sha256
            or turn.blocker_count != record.blocker_count
        ):
            raise reviewer_tasks.ReviewerTaskError(
                'Stage2 持久预审与原席位账的完整 identity 不一致')
        if turn.outcome == 'started':
            # 崩溃若恰落在 record save → ledger.finish 之间，只能由三份已经
            # 提交的独立事实共同证明 provider 成功；缺任一份都保持 started。
            if record.run_id in REGISTRY.live():
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage2 预审原 parent 仍在活动，不能从旁补完其席位账')
            if not turn.attempts:
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage2 预审 started 席缺进程 attempt 证据')
            last_attempt = turn.attempts[-1]
            if (
                last_attempt.finished_at is None
                or last_attempt.exit_code != 0
                or last_attempt.error_kind is not None
                or not last_attempt.wire_evidence
            ):
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage2 预审 started 席没有 wire+exit0 attempt 证据')
            sessions = [
                session for session in agent_session_audit.fold(
                    agent_session_audit.read_events(base))
                if session.session_run_id == record.reviewer_session_run_id]
            if len(sessions) != 1:
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage2 预审 started 席没有唯一 logical session 审计')
            session = sessions[0]
            audit_attempts = [
                attempt for attempt in session.attempts
                if attempt.attempt_id == last_attempt.attempt_id]
            if (
                session.start.parent_run_id != record.run_id
                or session.start.role != 'commander'
                or session.start.node is not None
                or session.end is None
                or session.process_state != 'ok'
                or session.error_kind is not None
                or len(audit_attempts) != 1
                or audit_attempts[0].error_kind is not None
            ):
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage2 预审 started 席的 session 审计未证明 process ok')
            cost_rows = [
                row for row in read_jsonl_rows(cost_ledger_path(base))
                if row.get('run_id') == record.run_id
                and row.get('session_run_id') == record.reviewer_session_run_id
                and row.get('sid') == sid
                and row.get('role') == 'commander'
                and row.get('attempt_id') == last_attempt.attempt_id
                and row.get('activity') == 'stage2.global.prereview'
            ]
            if (
                len(cost_rows) != 1
                or cost_rows[0].get('outcome') != 'ok'
                or cost_rows[0].get('exit_code') != 0
                or cost_rows[0].get('error_kind') is not None
            ):
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage2 预审 started 席的成本账未证明同 attempt exit 0')
            turn = stage2_review_ledger.finish(
                base, sid, source_sha256=record.source_sha256,
                main_text_sha256=record.main_text_sha256,
                session_run_id=record.reviewer_session_run_id, exit_code=0)
        if turn.outcome != 'ok' or turn.exit_code != 0:
            raise reviewer_tasks.ReviewerTaskError(
                'Stage2 持久预审的原席位没有成功闭账')
    except reviewer_tasks.ReviewerTaskError:
        raise
    except (
        NovelError, OSError, ValueError,
        stage2_review_ledger.Stage2ReviewBudgetRefused,
    ) as exc:
        raise reviewer_tasks.ReviewerTaskError(
            'Stage2 持久预审的原席位账无法确定性补完：'
            f'{type(exc).__name__}: {exc}') from exc
    return _ReplayableStage2Prereview(path=path, record=record)


def _validate_stage2_action_record_ledger(
        base: str, sid: str, *, record: ReviewerActionRecord,
        task: reviewer_tasks.ReviewerTask) -> bool:
    """执行 durable Stage2 action 前，复核并在强证据下补完原席位。

    live reviewer 的 typed loader 与 action record 保存发生在
    ``Stage2ReviewLedger.finish`` 之前。宿主若恰好死在两次原子写之间，记录会
    指向一席仍为 ``started`` 的 turn；只有 attempt、logical-session audit 与成本账
    同时证明同一进程已经 wire+exit0，且原 parent 已不活动，才允许幂等补 finish。
    这份 record 自身不是席位成功证明，更不是 execution permit。
    """
    identity = _stage2_prereview_identity(task)
    if identity is None:
        raise reviewer_tasks.ReviewerTaskError(
            '当前 Stage2 workpack 缺完整身份，不能复核 durable action 原席位')
    (business_sha256, workpack_sha256, source_sha256,
     main_text_sha256, _base_progress_sha256, blocker_count) = identity
    if (record.business_sha256 != business_sha256
            or record.workpack_sha256 != workpack_sha256
            or (record.source_sha256 is not None
                and record.source_sha256 != source_sha256)
            or (record.main_text_sha256 is not None
                and record.main_text_sha256 != main_text_sha256)
            or (record.blocker_count is not None
                and record.blocker_count != blocker_count)
            or record.reviewer_session_run_id is None):
        raise reviewer_tasks.ReviewerTaskError(
            'Stage2 durable action 与当前 business/workpack/source/main/blocker '
            '身份不一致')
    try:
        ledger_path = stage2_review_ledger.ledger_path(
            base, sid, source_sha256=source_sha256,
            main_text_sha256=main_text_sha256)
        _raw, archive = stage2_review_ledger.read_any_version(ledger_path)
        turns = [
            turn for turn in archive.turns
            if turn.session_run_id == record.reviewer_session_run_id]
        if len(turns) != 1:
            raise reviewer_tasks.ReviewerTaskError(
                'Stage2 durable action 找不到唯一的原席位账')
        turn = turns[0]
        if (
            turn.business_sha256 != business_sha256
            or turn.workpack_sha256 != workpack_sha256
            or (record.progress_sha256 is not None
                and turn.progress_sha256 != record.progress_sha256)
            or turn.blocker_count != blocker_count
        ):
            raise reviewer_tasks.ReviewerTaskError(
                'Stage2 durable action 与原席位账的完整 identity 不一致')
        allowed_purposes = {
            queue.WAITING_ON_STAGE2_REVIEW: {'prereview', 'review', 'correction'},
            queue.WAITING_ON_STAGE2_EDIT_CORRECTION: {'correction'},
            queue.WAITING_ON_STAGE2_FINAL_REVIEW: {'final_review'},
        }.get(task.waiting_on, set())
        if turn.purpose not in allowed_purposes:
            raise reviewer_tasks.ReviewerTaskError(
                'Stage2 durable action 的原席位 purpose 与 live blocker 不一致')
        if turn.rejections:
            # 拒因只可能在该席已 ok 后出现，且是 canonical/typed 边界已经消费
            # 回答的更晚事实。崩在“拒因落账 → action record 删除”之间时，
            # record 绝不能盖过拒因再次执行；调用方可安全删除它并继续走
            # pending-rejection/correction 路。
            return False
        if turn.outcome == 'started':
            if record.run_id in REGISTRY.live():
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage2 durable action 原 parent 仍在活动，不能从旁补完其席位账')
            if not turn.attempts:
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage2 durable action started 席缺进程 attempt 证据')
            last_attempt = turn.attempts[-1]
            if (
                last_attempt.finished_at is None
                or last_attempt.exit_code != 0
                or last_attempt.error_kind is not None
                or not last_attempt.wire_evidence
            ):
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage2 durable action started 席没有 wire+exit0 attempt 证据')
            sessions = [
                session for session in agent_session_audit.fold(
                    agent_session_audit.read_events(base))
                if session.session_run_id == record.reviewer_session_run_id]
            if len(sessions) != 1:
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage2 durable action started 席没有唯一 logical session 审计')
            session = sessions[0]
            audit_attempts = [
                attempt for attempt in session.attempts
                if attempt.attempt_id == last_attempt.attempt_id]
            if (
                session.start.parent_run_id != record.run_id
                or session.start.role != 'commander'
                or session.start.node is not None
                or session.end is None
                or session.process_state != 'ok'
                or session.error_kind is not None
                or len(audit_attempts) != 1
                or audit_attempts[0].error_kind is not None
            ):
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage2 durable action started 席的 session 审计未证明 process ok')
            activity = (
                'stage2.global.prereview'
                if turn.purpose == 'prereview' else _reviewer_activity(task))
            cost_rows = [
                row for row in read_jsonl_rows(cost_ledger_path(base))
                if row.get('run_id') == record.run_id
                and row.get('session_run_id') == record.reviewer_session_run_id
                and row.get('sid') == sid
                and row.get('role') == 'commander'
                and row.get('attempt_id') == last_attempt.attempt_id
                and row.get('activity') == activity
            ]
            if (
                len(cost_rows) != 1
                or cost_rows[0].get('outcome') != 'ok'
                or cost_rows[0].get('exit_code') != 0
                or cost_rows[0].get('error_kind') is not None
            ):
                raise reviewer_tasks.ReviewerTaskError(
                    'Stage2 durable action started 席的成本账未证明同 attempt exit 0')
            turn = stage2_review_ledger.finish(
                base, sid, source_sha256=source_sha256,
                main_text_sha256=main_text_sha256,
                session_run_id=record.reviewer_session_run_id, exit_code=0)
        if turn.outcome != 'ok' or turn.exit_code != 0:
            raise reviewer_tasks.ReviewerTaskError(
                'Stage2 durable action 的原席位没有成功闭账')
    except reviewer_tasks.ReviewerTaskError:
        raise
    except (
        NovelError, OSError, ValueError,
        stage2_review_ledger.Stage2ReviewBudgetRefused,
    ) as exc:
        raise reviewer_tasks.ReviewerTaskError(
            'Stage2 durable action 的原席位账无法确定性补完：'
            f'{type(exc).__name__}: {exc}') from exc
    return True


def _stage2_task_from_action_record(
        path: Path, record: ReviewerActionRecord,
        ) -> reviewer_tasks.ReviewerTask:
    """只为 durable 补账重建原席位 identity，不冒充当前 live workpack。"""
    metadata = (
        record.workpack_sha256, record.source_sha256,
        record.main_text_sha256, record.progress_sha256,
        record.blocker_count, record.reviewer_session_run_id,
        record.provider_response_sha256,
    )
    if not all(value is not None for value in metadata):
        raise reviewer_tasks.ReviewerTaskError(
            f'Stage2 durable action {path.name} 缺完整席位身份')
    assert record.workpack_sha256 is not None
    assert record.source_sha256 is not None
    assert record.main_text_sha256 is not None
    assert record.progress_sha256 is not None
    assert record.blocker_count is not None
    return reviewer_tasks.ReviewerTask(
        waiting_on=record.waiting_on,
        surface_seal=record.surface_seal,
        business_sha256=record.business_sha256,
        request_path=path,
        prompt='durable Stage2 action ledger reconciliation',
        submission_options=(),
        workpack_sha256=record.workpack_sha256,
        source_sha256=record.source_sha256,
        main_text_sha256=record.main_text_sha256,
        progress_sha256=record.progress_sha256,
        blocker_count=record.blocker_count,
    )


def _stage2_apply_edit_record_sha256(
        store: ArtifactStore, record: ReviewerActionRecord,
        ) -> str | None:
    """复算一份 durable action 真正会写到 canonical edit 的完整文件 SHA。"""
    if (
        record.node != 'stage2.global'
        or record.input_payload is None
        or record.flags is None
        or tuple(record.flags[:1]) != ('--apply-edit',)
        or len(record.flags) != 2
    ):
        return None
    try:
        decoded = decode_artifact(STAGE2_GLOBAL_EDIT, record.input_payload)
        edit = GlobalEdit.model_validate(decoded.payload)
        return store.prospective_sparse_sha256(STAGE2_GLOBAL_EDIT, edit)
    except Exception as exc:  # noqa: BLE001 —— 坏 durable 记录不能被静默当作不匹配
        raise reviewer_tasks.ReviewerTaskError(
            f'Stage2 durable apply-edit {record.run_id} 的 payload 无法复算') from exc


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


def _safe_stage2_loader_rejection(exc: Exception) -> str:
    """把旧 durable action 的当前-loader 拒因压成不含 payload 的稳定摘要。"""
    diagnostic_sha256 = hashlib.sha256(
        str(exc).encode('utf-8')).hexdigest()
    trusted_codes = (
        'collection_split_output_reuses_subject',
        'collection_touched_input_duplicate',
        'collection_output_duplicate',
        'collection_input_output_overlap',
        'direct_patch_subject_removed_by_collection_edit',
    )
    matched = [code for code in trusted_codes if code in str(exc)]
    category = matched[0] if len(matched) == 1 else 'typed_contract'
    return (
        'Stage2 durable action 未通过当前 typed loader；'
        f'category={category}；diagnostic_sha256={diagnostic_sha256}')


def _reconcile_stage2_apply_edit_actions(
        base: str, sid: str, *, code: str | None = None) -> None:
    """在 planner 前闭合 canonical action 与 producer 事实的双写窗。

    ReviewerActionRecord 先于 canonical 子进程存在。进程可能已经留下两类更晚事实，
    却在 Agent 收到退出码前硬终止：终态 failure sidecar 证明这份回答已被拒绝；
    committed formal journal 证明它已经落地。两者都必须先绑回原席位/退休 record，
    才能让新 planner 发 correction 或继续下游。prepared/canonical_pending 仍是进行中
    的同一动作，只能交 deterministic Executor 补尾，不能提前记成拒绝。
    """
    action_records = [
        (path, record)
        for path, record in uploads.load_reviewer_actions(base, sid)
        if record.node == 'stage2.global'
        and record.flags is not None
        and '--apply-edit' in record.flags
    ]
    if not action_records:
        return

    novel_dir = paths.resolve_novel_dir(base, sid)
    store = ArtifactStore(state.readonly_ctx(
        novel_dir, 'stage2.global', code or prov.code_version()))
    target_sha256: str | None = None
    failure: GlobalEditFailure | None = None
    committed: GlobalEditCommit | None = None

    if store.exists(STAGE2_GLOBAL_EDIT_FAILURE):
        failure = store.read_own(
            GlobalEditFailure, STAGE2_GLOBAL_EDIT_FAILURE)[0]
        if failure.failure_kind != 'canonical_pending':
            # 新人工 edit 可以在旧 sidecar 归档前成为 current；只有 exact
            # canonical input 仍等于 failed edit 时，sidecar 才能消费旧回答。
            if (store.full_sha256_or_none(STAGE2_GLOBAL_EDIT)
                    == failure.failed_edit_sha256):
                target_sha256 = failure.failed_edit_sha256
            else:
                failure = None
    if store.exists(STAGE2_GLOBAL_EDIT_COMMIT):
        journal = store.read_own(
            GlobalEditCommit, STAGE2_GLOBAL_EDIT_COMMIT)[0]
        # revalidate journal 证明的是既有 INVALID 的 0-wire 提升，不携带一份
        # Reviewer apply-edit 动作。即使兼容记录里仍有 failed_edit_sha256，
        # 也不能借它退休/消费一份 durable ReviewerActionRecord。
        if journal.state == 'committed' and journal.action_kind == 'apply_edit':
            if target_sha256 is not None:
                raise reviewer_tasks.ReviewerTaskError(
                    '同一 Stage2 edit 同时声称 terminal failure 与 committed')
            committed = journal
            target_sha256 = journal.failed_edit_sha256
    if target_sha256 is None:
        return

    matches = [
        (path, record)
        for path, record in action_records
        if _stage2_apply_edit_record_sha256(store, record) == target_sha256
    ]
    if len(matches) > 1:
        raise reviewer_tasks.ReviewerTaskError(
            '同一 canonical Stage2 edit 命中多份 durable action；无法唯一补账')
    if not matches:
        return

    path, record = matches[0]
    task = _stage2_task_from_action_record(path, record)
    if failure is not None:
        if (
            record.source_sha256 != failure.expected_source_sha256
            or record.workpack_sha256
            != failure.global_review_workpack_sha256
        ):
            raise reviewer_tasks.ReviewerTaskError(
                'Stage2 terminal failure 与 durable action 的 source/workpack 漂移')
        if not _validate_stage2_action_record_ledger(
                base, sid, record=record, task=task):
            # 拒因已经 durable，只差 record 退休；绝不能再执行旧 action。
            uploads.discard_edit_plan(base, path)
            return
        assert record.reviewer_session_run_id is not None
        assert record.provider_response_sha256 is not None
        assert record.source_sha256 is not None
        assert record.main_text_sha256 is not None
        assert record.workpack_sha256 is not None
        stage2_review_ledger.record_rejection(
            base, sid,
            source_sha256=record.source_sha256,
            main_text_sha256=record.main_text_sha256,
            business_sha256=record.business_sha256,
            workpack_sha256=record.workpack_sha256,
            session_run_id=record.reviewer_session_run_id,
            kind='canonical_action',
            request_sha256=record.provider_response_sha256,
            reason=(
                f'{failure.reason_code}: {failure.reason_summary}'))
        uploads.discard_edit_plan(base, path)
        return

    assert committed is not None
    if (
        record.source_sha256 != committed.expected_source_sha256
        or record.workpack_sha256
        != committed.global_review_workpack_sha256
    ):
        raise reviewer_tasks.ReviewerTaskError(
            'Stage2 committed journal 与 durable action 的 source/workpack 漂移')
    if not _validate_stage2_action_record_ledger(
            base, sid, record=record, task=task):
        raise reviewer_tasks.ReviewerTaskError(
            'Stage2 action 已有 durable rejection，却又出现 committed formal journal')
    _discard_reviewer_action_record(base, path)


def _reconcile_landed_stage2_final_actions(base: str, sid: str) -> None:
    """在 planner 读取前补完 final approval 已落地后的 ledger/record 双写窗。

    ``approval close-stage2`` 是 canonical 正式写；它成功后 operator 已可能越过
    final-review blocker，因此不能指望下一 parent 再构造旧 ReviewerTask 来清理。
    这里不重放 approval，只以当前 effective s2/seen、盘上明文 token 与原席位账
    证明同一回答已经落地：先幂等 ``mark_complete``，再退休 action record。
    """
    final_records = [
        (path, record)
        for path, record in uploads.load_reviewer_actions(base, sid)
        if record.surface == 'stage2_final_review'
    ]
    if not final_records:
        return

    # 局部导入避免 approvals 的大依赖树进入 agent 模块初始化环。
    from novel.orchestrator import approvals

    approval = approvals.get(base, sid, include_spawns=False)
    if not approval.gate.get('s2'):
        # canonical 尚未落地：保留 record 给正常 final-review blocker 零模型重放。
        return
    if not all(approval.seen.values()):
        raise reviewer_tasks.ReviewerTaskError(
            'current s2 approval 与 seen.read/seen.global 投影不闭合')
    try:
        head = json.loads(approvals.approval_head_bytes(base, sid))
        seen_subjects = head['seen_subjects']
        current_global_token = seen_subjects['global']['review_token']
        current_blueprint_token = seen_subjects['read']['review_token']
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise reviewer_tasks.ReviewerTaskError(
            'current s2 approval 缺可与 durable final action 对证的 seen token') from exc

    matching: list[tuple[Path, ReviewerActionRecord]] = []
    stale: list[Path] = []
    for path, record in final_records:
        try:
            request = Stage2FinalReviewRequest.model_validate(record.payload)
        except Exception as exc:  # noqa: BLE001 —— 坏 durable 记录不得静默略过
            raise reviewer_tasks.ReviewerTaskError(
                f'Stage2 final action record {path.name} 的 payload 不合法') from exc
        if (
            request.global_review_token == current_global_token
            and request.blueprint_review_token == current_blueprint_token
        ):
            matching.append((path, record))
        else:
            stale.append(path)
    if len(matching) > 1:
        raise reviewer_tasks.ReviewerTaskError(
            'current s2 approval 命中多份 durable final action，无法唯一补账')
    if not matching:
        # 当前 approval 已由另一份精确主体事实闭合；旧 token 的 action 不再可执行。
        for path in stale:
            _discard_reviewer_action_record(base, path)
        return

    path, record = matching[0]
    task = _stage2_task_from_action_record(path, record)
    if not _validate_stage2_action_record_ledger(
            base, sid, record=record, task=task):
        raise reviewer_tasks.ReviewerTaskError(
            'current s2 approval 对应的原 final-review 席已记录拒因，不能自动补账')
    assert record.source_sha256 is not None
    assert record.main_text_sha256 is not None
    stage2_review_ledger.mark_complete(
        base, sid,
        source_sha256=record.source_sha256,
        main_text_sha256=record.main_text_sha256)
    _discard_reviewer_action_record(base, path)
    for stale_path in stale:
        _discard_reviewer_action_record(base, stale_path)


def _find_replayable_reviewer_action(
        base: str, sid: str, *, epoch: ExecutionEpoch,
        current: OperatorNext,
        task: reviewer_tasks.ReviewerTask,
        ) -> _ReplayableReviewerAction | None:
    """唤起模型前先查盘上是否已有适用于当前 blocker 的 typed 决定。

    普通决定只认同 ``waiting_on`` 且 surface seal、opaque review_token 或稳定
    business identity 至少一项相同的记录。Stage0 没有 review_token，
    其 revision/完整保留文本/removed identity/contract 已全部进入
    ``business_sha256``，因此代码 epoch 改变后仍能零成本重放。
    Stage2 必须同时命中稳定 business 与 workpack；surface seal 可因纯代码
    epoch 变化，但 full action 仍须重走今天的 loader 与当前 permit。
    命中的记录仍要重走 ``load_action``：schema、sentinel、all-accept
    等确定性判据以今天的代码为准。
    """
    waiting_on = task.waiting_on
    current_token = reviewer_tasks.approval_review_token(
        task.submission_options)
    candidates: list[tuple[int, Path, ReviewerActionRecord]] = []
    for path, record in uploads.load_reviewer_actions(base, sid):
        if record.waiting_on != waiting_on:
            continue
        seal_match = record.surface_seal == current.surface_seal
        token_match = (
            record.review_token is not None and current_token is not None
            and record.review_token == current_token)
        business_match = record.business_sha256 == task.business_sha256
        if record.workpack_sha256 is not None:
            matches = (
                business_match
                and task.workpack_sha256 is not None
                and record.workpack_sha256 == task.workpack_sha256
                and record.reviewer_session_run_id is not None
                and record.provider_response_sha256 is not None)
        else:
            matches = seal_match or token_match or business_match
        if matches:
            candidates.append((record.recorded_at, path, record))
        else:
            _discard_reviewer_action_record(base, path)
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1].name))
    _recorded_at, path, record = candidates[-1]
    for _stale_at, stale_path, _stale in candidates[:-1]:
        _discard_reviewer_action_record(base, stale_path)
    if record.workpack_sha256 is not None:
        # 先证明 paid turn 已成功闭账；不能让一份 action record 自证席位成功，
        # 更不能在 started/审计不完整时把它交给 canonical Executor。
        if not _validate_stage2_action_record_ledger(
                base, sid, record=record, task=task):
            # 该席已有 durable rejection；这是比 action record 更新的业务事实。
            # 退休失败不能吞成 warning，否则 pending-rejection 会先启动下一席，
            # 被拒 action 却跨 parent 永久泄漏并可能在未来被误重放。
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
            replay_path, current, submission_options=task.submission_options)
        # Stage2 fresh-session 纠错不依赖旧 provider 上下文；若重放动作随后被
        # canonical 边界拒绝，新会话仍需要上一份精确请求字节来铸 progress SHA。
        task.request_path.parent.mkdir(parents=True, exist_ok=True)
        task.request_path.write_bytes(payload)
    except reviewer_tasks.ReviewerTaskError as exc:
        if record.workpack_sha256 is None:
            warnings.warn(
                f'盘上 reviewer 决定记录 {path.name} 未通过当前 loader，已删除：{exc}',
                RuntimeWarning, stacklevel=2)
            _discard_reviewer_action_record(base, path)
            return None
        if (not replay_path.is_file()
                or any(isinstance(cause, OSError) for cause in (
                    exc, exc.__cause__, exc.__context__))):
            raise reviewer_tasks.ReviewerTaskError(
                'Stage2 durable action 的当前 loader 发生读取异常；'
                '不能把 unknown 记成业务拒因') from exc
        if (
            record.reviewer_session_run_id is None
            or record.provider_response_sha256 is None
            or record.source_sha256 is None
            or record.main_text_sha256 is None
            or record.workpack_sha256 is None
        ):
            raise reviewer_tasks.ReviewerTaskError(
                'Stage2 durable action 当前 loader 已拒绝，但原席位/raw digest '
                '身份不完整；拒绝删除恢复凭据') from exc
        # 原席/六元账已在本函数上半段验证。今天的 loader 若因 schema 或
        # immutable binding 收紧而拒绝旧 typed-valid action，必须先把拒因绑回
        # 原 provider raw digest，成功后才严格退休 record；否则同 progress 的
        # reserve 会被旧席挡死，回答却无从纠错。
        stage2_review_ledger.record_rejection(
            base, sid,
            source_sha256=record.source_sha256,
            main_text_sha256=record.main_text_sha256,
            business_sha256=record.business_sha256,
            workpack_sha256=record.workpack_sha256,
            session_run_id=record.reviewer_session_run_id,
            kind='typed_loader',
            request_sha256=record.provider_response_sha256,
            reason=_safe_stage2_loader_rejection(exc))
        uploads.discard_edit_plan(base, path)
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


def _materialize_reviewer_response(
        task: reviewer_tasks.ReviewerTask, current: OperatorNext,
        response: str, *,
        persist_validated: Callable[
            [reviewer_tasks.ReviewerAction, str], Path | None] | None = None,
        preserve_validated: Callable[[Path, str], Path] | None = None,
        ) -> tuple[reviewer_tasks.ReviewerAction, str, Path | None]:
    """复用现役 loader；approval 先 durable，再原子晋升 canonical scratch。"""
    document, response_digest = _reviewer_response_document(response)
    candidate_digest = response_digest
    if stage2_reviewer_wire.uses_compact_wire(task):
        try:
            document = stage2_reviewer_wire.assemble(task, document)
        except stage2_reviewer_wire.Stage2ReviewerWireError as exc:
            # compact wire 只是运输层；所有拒绝仍进入现役 ReviewerTaskError
            # 的有界纠错/持久拒因路径，不能越级变成 Supervisor runtime crash。
            raise reviewer_tasks.ReviewerTaskError(str(exc)) from exc
        # provider compact 回答的 digest 负责拒因/no-progress 身份；residue 名称
        # 则必须继续是它实际保存的 assembled full request 的内容地址。
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
        action = reviewer_tasks.load_action(
            candidate, current,
            submission_options=task.submission_options)
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


def _materialize_stage2_prereview_response(
        task: reviewer_tasks.Stage2PrereviewTask,
        response: str, *,
        persist_validated: Callable[[dict[str, object], str], Path] | None = None,
        preserve_validated: Callable[[Path, str], Path] | None = None,
        ) -> tuple[dict[str, object], str, Path | None]:
    """预审由 Supervisor typed-load，并在 scratch 晋升前先 durable。"""
    document, digest = _reviewer_response_document(response)
    candidate = _reviewer_candidate_path(task.request_path)
    validated = False
    promoted = False
    record_path: Path | None = None
    try:
        service_lifecycle.write_scratch_json_once(
            task.request_path.parent, candidate, document)
        payload = reviewer_tasks.load_stage2_prereview(
            replace(task, request_path=candidate))
        validated = True
        record_path = (
            persist_validated(payload, digest)
            if persist_validated is not None else None)
        _promote_reviewer_candidate(candidate, task.request_path)
        promoted = True
        return payload, digest, record_path
    except BaseException as exc:
        if (validated and record_path is None and not promoted
                and preserve_validated is not None):
            try:
                preserved = preserve_validated(candidate, digest)
                if hasattr(exc, 'add_note'):
                    exc.add_note(f'typed-valid Stage2 预审已隔离保留：{preserved}')
            except BaseException as preserve_error:  # noqa: BLE001
                if hasattr(exc, 'add_note'):
                    exc.add_note(
                        'typed-valid Stage2 预审隔离失败：'
                        f'{type(preserve_error).__name__}: {preserve_error}')
        raise
    finally:
        if not validated or promoted:
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass


def _skill_sha256() -> str:
    try:
        return skill_contract.package_sha256(REPO_ROOT)
    except (OSError, skill_contract.SkillContractError) as exc:
        raise UnsupportedRunMode(
            'Commander 启动契约的 canonical operator skill 整树不完整，'
            '不能声称本轮已冻结操作规程') from exc


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
          spawn: Spawn | None = None,
          registry: JobRegistry = REGISTRY) -> AgentRunRecord:
    """解析一次冻结 plan 后拉起 Commander；缺任一授权时不触碰 CLI。"""
    _require_start_authorization(
        authorized=authorized, egress_authorized=egress_authorized)
    return _start_planned(
        plan(base, sid, authorized=authorized,
             egress_authorized=egress_authorized, profiles=profiles),
        spawn=spawn, registry=registry)


def _start_planned(plan: AgentRunPlan, *, spawn: Spawn | None = None,
                   registry: JobRegistry = REGISTRY) -> AgentRunRecord:
    """拉起一次 agent 代跑（**异步**）：锁 → 记 `started` → 线程跑子进程 → 记 `ended`。

    三道闸，各管一边：
      · 本书一把锁——同一本书一次只跑一个 agent（`lock_key`）；
      · 并发闸——同时在跑的 agent 不超过 `MAX_CONCURRENT`（满了抛 `JobConflict`，
        批量入口 `enqueue` 会把它接住改成排队，单本入口就是明确的 409）；
      · 反向互斥——这本书上有 API 作业在跑 ⇒ 409（agent 会自己起 `novel.cli`
        子进程，两边同时写同一本书就是 skill 禁的并行写盘）。
    ⚠ 两张 registry 各自线程安全仍不够：agent 与 job 若各自「查对面 → 占自己」，
    可以同时看见空表再各拿一把。双方统一经 ``jobs.acquire_*_writer``，在同一个
    短临界区内完成 durable/对表检查与占锁；运行期仍只持本书逻辑 writer，异书并行。
    """
    _require_start_authorization(
        authorized=plan.authorized,
        egress_authorized=plan.egress_authorized)
    base = plan.base
    sid = plan.sid
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
    skill_sha256 = _skill_sha256()
    start_payload = AgentRunStart(
        sid=sid, base=base, argv=list(invocation.argv), prompt=plan.prompt,
        authorized=plan.authorized,
        egress_authorized=plan.egress_authorized,
        code_version=code_version, skill_sha256=skill_sha256,
        profiles=plan.profiles)
    contract_payload = RunContractV2(
        sid=sid, base=base,
        authorized=plan.authorized,
        egress_authorized=plan.egress_authorized,
        profiles=plan.profiles)
    initial_epoch = ExecutionEpoch(
        epoch_id=run_id, ordinal=1, reason='initial',
        argv=list(invocation.argv), prompt_sha256=prompt_sha256,
        protocol_version=invocation.protocol,
        cli_version=invocation.cli_version, code_version=code_version,
        skill_sha256=skill_sha256, session_run_id=session_run_id)
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
                        exit_code=CRASH,
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
    latest_error_kind: str | None = None
    latest_commander_outcome = latest_outcome
    latest_commander_rc = CRASH
    latest_commander_error_kind: str | None = None
    provider_turn_recorded = False
    current_session_code_version = code_version
    current_session_skill_sha256 = skill_sha256
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
    reviewer_correction_task: reviewer_tasks.ReviewerTask | None = None
    reviewer_correction_binding: tuple[str, str] | None = None
    reviewer_correction_reason: str | None = None
    reviewer_correction_request_sha256: str | None = None
    reviewer_correction_attempts = 0
    stage2_commander_turns = 0
    stage2_prereview: _Stage2PrereviewState | None = None
    stage2_fallback_progress: set[str] = set()
    reviewed_blockers: set[tuple[str, str]] = set()
    completed_reviewer_business_actions: set[tuple[str, str]] = set()
    rejected_reviewer_business_actions: set[tuple[str, str]] = set()

    def _reserve_stage2_commander(
            task: reviewer_tasks.ReviewerTask | reviewer_tasks.Stage2PrereviewTask,
            *, epoch: ExecutionEpoch, purpose: str, session_id: str,
            progress_sha256: str | None = None,
    ) -> bool:
        """在唯一调用缝预留一格预算；返回是否写入了持久账。"""
        nonlocal stage2_commander_turns
        progress = progress_sha256 or task.progress_sha256
        metadata = (
            task.source_sha256, task.main_text_sha256,
            task.workpack_sha256, progress, task.blocker_count)
        if (all(isinstance(value, str) for value in metadata[:4])
                and isinstance(task.blocker_count, int)):
            reserved = stage2_review_ledger.reserve(
                base, sid,
                source_sha256=str(task.source_sha256),
                main_text_sha256=str(task.main_text_sha256),
                business_sha256=task.business_sha256,
                workpack_sha256=str(task.workpack_sha256),
                progress_sha256=str(progress),
                blocker_count=task.blocker_count,
                purpose=purpose,
                parent_run_id=run_id,
                epoch_id=epoch.epoch_id,
                session_run_id=session_id,
            )
            stage2_commander_turns = reserved.ordinal
            return True
        # legacy/test injection 没有新版 identity 时仍守同一硬预算；生产 build_task
        # 永远走上面的持久分支，不能靠缺字段绕过跨重启账。
        if stage2_commander_turns >= _MAX_STAGE2_COMMANDER_TURNS:
            raise stage2_review_ledger.Stage2ReviewBudgetRefused(
                f'Stage2 Global 已用满 {_MAX_STAGE2_COMMANDER_TURNS} 席 Commander')
        if (purpose != 'final_review'
                and stage2_commander_turns >= _MAX_STAGE2_EDIT_SEAT):
            raise stage2_review_ledger.Stage2ReviewBudgetRefused(
                f'Stage2 Global 前 {_MAX_STAGE2_EDIT_SEAT} 席已用完；'
                f'第 {stage2_commander_turns + 1} 席只保留给只读 final_review')
        fallback_progress = progress or f'legacy:{stage2_commander_turns + 1}'
        if fallback_progress in stage2_fallback_progress:
            raise stage2_review_ledger.Stage2ReviewBudgetRefused(
                'Stage2 Global progress fingerprint 没有变化')
        stage2_fallback_progress.add(fallback_progress)
        stage2_commander_turns += 1
        return False

    def _finish_stage2_commander(
            task: reviewer_tasks.ReviewerTask | reviewer_tasks.Stage2PrereviewTask,
            *, session_id: str, durable: bool, exit_code: int,
    ) -> None:
        if not durable:
            return
        assert task.source_sha256 is not None
        assert task.main_text_sha256 is not None
        stage2_review_ledger.finish(
            base, sid,
            source_sha256=task.source_sha256,
            main_text_sha256=task.main_text_sha256,
            session_run_id=session_id,
            exit_code=exit_code)

    def _stage2_identity(
            task: reviewer_tasks.ReviewerTask
            | reviewer_tasks.Stage2PrereviewTask,
    ) -> tuple[str, str, str, str] | None:
        """返回能唯一绑定持久席位的业务身份；旧测试注入保持兼容。"""
        identity = (
            task.source_sha256, task.main_text_sha256,
            task.business_sha256, task.workpack_sha256,
        )
        if not all(isinstance(value, str) for value in identity):
            return None
        return tuple(str(value) for value in identity)  # type: ignore[return-value]

    def _record_stage2_rejection(
            task: reviewer_tasks.ReviewerTask
            | reviewer_tasks.Stage2PrereviewTask,
            *, session_run_id: str | None,
            kind: str, request_sha256: str, reason: str,
    ) -> None:
        """先把边界拒因写回产出该回答的席位，再允许任何纠错或停止。"""
        identity = _stage2_identity(task)
        if identity is None:
            # 只兼容没有新版 identity 的旧单测注入；生产 build_task 永远完整。
            return
        if session_run_id is None:
            raise stage2_review_ledger.Stage2ReviewBudgetRefused(
                'Stage2 typed/canonical 拒因缺产出回答的 logical session identity')
        source_sha256, main_text_sha256, business_sha256, workpack_sha256 = (
            identity)
        stage2_review_ledger.record_rejection(
            base, sid,
            source_sha256=source_sha256,
            main_text_sha256=main_text_sha256,
            business_sha256=business_sha256,
            workpack_sha256=workpack_sha256,
            session_run_id=session_run_id,
            kind=kind,
            request_sha256=request_sha256,
            reason=reason)

    def _pending_stage2_rejection(
            task: reviewer_tasks.ReviewerTask,
    ) -> stage2_review_ledger.Stage2PendingRejection | None:
        identity = _stage2_identity(task)
        if identity is None:
            return None
        source_sha256, main_text_sha256, business_sha256, workpack_sha256 = (
            identity)
        return stage2_review_ledger.pending_rejection(
            base, sid,
            source_sha256=source_sha256,
            main_text_sha256=main_text_sha256,
            business_sha256=business_sha256,
            workpack_sha256=workpack_sha256)

    def _complete_stage2_review_ledger(
            task: reviewer_tasks.ReviewerTask) -> None:
        """Stage2 动作以 exit 0 闭合写作面后封账；旧注入 task 没 identity 时跳过。"""
        if task.source_sha256 is None or task.main_text_sha256 is None:
            return
        stage2_review_ledger.mark_complete(
            base, sid,
            source_sha256=task.source_sha256,
            main_text_sha256=task.main_text_sha256)

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
        nonlocal current_session_code_version, current_session_skill_sha256
        nonlocal current_session_invocation, current_session_prompt_sha256
        nonlocal session_started, session_ended
        nonlocal pending_reviewer_action, pending_reviewer_binding
        nonlocal pending_reviewer_business_sha256, pending_reviewer_source_task
        nonlocal pending_reviewer_stage2_session_run_id
        nonlocal pending_reviewer_response_sha256
        nonlocal pending_reviewer_record_path, pending_reviewer_replayed
        nonlocal reviewer_correction_task, reviewer_correction_binding
        nonlocal reviewer_correction_reason, reviewer_correction_request_sha256
        nonlocal reviewer_correction_attempts, stage2_commander_turns
        nonlocal stage2_fallback_progress
        nonlocal stage2_prereview
        current_code = prov.code_version()
        _require_reproducible_code_version(current_code)
        current_skill = _skill_sha256()
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
            # 新 execution epoch 必须从当前代码/skill 重新构造 Commander
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
            current_session_skill_sha256 = current_skill
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
            reviewer_correction_task = None
            reviewer_correction_binding = None
            reviewer_correction_reason = None
            reviewer_correction_request_sha256 = None
            reviewer_correction_attempts = 0
            stage2_commander_turns = 0
            stage2_fallback_progress.clear()
            stage2_prereview = None
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
                skill_sha256=current_skill,
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
            skill_sha256=current_skill,
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
                skill_sha256=current_session_skill_sha256,
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
                skill_sha256=current_session_skill_sha256,
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
                else 'skill_version_drift')
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
                prov.code_version() == epoch.code_version
                and _skill_sha256() == epoch.skill_sha256)
        except Exception:  # noqa: BLE001 —— 取不到身份就必须滚 epoch
            return False

    def _cancel_active() -> bool:
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
        """给 ready/reviewer/finalizer 共用一条严格递增的 admission 序列。"""
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
            read_only: bool = False,
            environment_overrides: dict[str, str] | None = None,
            after_spawn_before_wire: Callable[[], None] | None = None,
            on_registered: Callable[[], None] | None = None,
            activity: str | None = None,
            on_attempt_started: Callable[[str, str, str | None], None] | None = None,
            on_attempt_finished: Callable[
                [str, int, str | None, str | None, bool], None] | None = None,
            max_attempts: int = COMMANDER_MAX_ATTEMPTS_PER_SEAT,
            seat_provider_session_id: str | None = None,
            seat_turns_recorded: int = 0,
            seat_subject_sha256: str | None = None,
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
                    skill_sha256=audit_epoch.skill_sha256,
                    prompt_sha256=fresh_prompt_sha256,
                    schema_sha256=turn_schema_sha256,
                    kind='started', ts=int(time.time()),
                ),
            )
            fresh_session_started = True

        if reviewer_prompt is not None:
            turn_invocation = _review_invocation(
                epoch, reviewer_prompt, effective_provider_session_id,
                output_schema=reviewer_output_schema)
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
        # 预审 Commander 只读并只能返回最终文本，不领取任何
        # producer admission。仍下发一枚不存在的 context id：即使
        # 供应商边界意外放开工具，节点入口也会关闭式拒绝。
        admission_id = (
            stage2_prereview_admission_id(epoch.epoch_id)
            if read_only else _open_action_admission(epoch))
        attempt_id = jobs.new_job_id()
        rc = CRASH
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
            nonlocal invoked, registered, integrity_error_kind
            # reviewer workpack 在 operator next/build_task 时绑定了本
            # execution epoch。从构包末端到真实 Popen 仍有窗口；
            # 必须在任何 seat/budget reserve、logical-session started、
            # attempt/ticket 和 stdin 之前再做一次 code+skill CAS。Popen
            # 已创建不等于已 wire，callback 抛出后公共 runner 会在
            # 发送 prompt 前终止进程树。
            if (reviewer_prompt is not None
                    and not _epoch_identity_current(epoch)):
                raise CommanderConfigurationError(
                    'reviewer 发送前 execution epoch code/skill 已漂移；'
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

        def append_attempt_audit(
                attempt_rc: int, failure: str | None,
                attempt_observation: CommanderObservation) -> None:
            nonlocal integrity_error_kind
            try:
                agent_session_audit.append_event(
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
                        skill_sha256=epoch.skill_sha256,
                        prompt_sha256=_sha256_bytes(
                            attempt_invocation.stdin_text.encode('utf-8')),
                        schema_sha256=turn_schema_sha256,
                        usage=_session_usage(attempt_observation),
                        error_kind=(
                            None if attempt_rc == 0
                            else (failure or 'commander_process_exit')),
                        kind='attempt', ts=int(time.time()),
                    ),
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
                with _COMMANDERS_LOCK:
                    _COMMANDER_EVENT_SINKS[run_id] = session_pointer_sink
                while True:
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
                        attempt_observation = result.observation
                        attempt_failure = result.failure_kind
                        attempt_retryable = bool(result.retryable)
                        attempt_wire = bool(result.wire_evidence)
                        attempt_final_response = result.final_response
                    except BaseException as exc:  # noqa: BLE001 —— 崩了也要闭合本 attempt
                        attempt_crashed = True
                        known_rc = getattr(exc, 'returncode', None)
                        if isinstance(known_rc, int) and not isinstance(known_rc, bool):
                            attempt_rc = known_rc
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
                        turn_latency_ms = int(
                            (time.monotonic() - spawn_started) * 1000)
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
                    retry = (
                        invoked and attempt_rc != 0 and attempt_retryable
                        and attempt_no < max_attempts
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
                                    skill_sha256=epoch.skill_sha256,
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
            if isinstance(known_rc, int) and not isinstance(known_rc, bool):
                rc = known_rc
            runtime_error_kind = 'commander_runtime'
            _append_safe_traceback(base, run_id, prompt=turn_invocation.stdin_text)
        finally:
            with _COMMANDERS_LOCK:
                _COMMANDER_EVENT_SINKS.pop(run_id, None)
            if not read_only:
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
        elif read_only:
            outcome = AgentDeliveryOutcome(
                state='unknown', checked_at=int(time.time()),
                reason='Stage2 Global 只读预审已结束；等待 Blueprint 与 live surface')
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
                and observation.actual_model
                != plan.profiles.commander.model):
            integrity_error_kind = 'commander_model_drift'
        if (observation.actual_effort is not None
                and observation.actual_effort
                != plan.profiles.commander.reasoning_effort):
            integrity_error_kind = (
                integrity_error_kind or 'commander_effort_drift')

        continue_ready = False
        if (not read_only
                and rc == 0
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

        if invoked:
            try:
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
                    activity=activity)
            except Exception:  # noqa: BLE001
                integrity_error_kind = 'commander_ledger'
                error_kind = integrity_error_kind
                _append_safe_traceback(
                    base, run_id, prompt=turn_invocation.stdin_text)

        provider_reuse_owner: str | None = None
        this_turn_recorded = False
        if observation.provider_session_id is not None:
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
                            skill_sha256=epoch.skill_sha256,
                            prompt_sha256=_sha256_bytes(
                                attempt_invocation.stdin_text.encode('utf-8')),
                            schema_sha256=turn_schema_sha256,
                            usage=_session_usage(observation),
                            error_kind=error_kind,
                            kind='turn', ts=int(time.time()),
                        ),
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
                        skill_sha256=audit_epoch.skill_sha256,
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
                    skill_sha256=current_session_skill_sha256,
                    prompt_sha256=seat.prompt_sha256,
                    schema_sha256=schema_sha256,
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
            pause_note: str | None = None) -> SupervisorActionResult:
        nonlocal latest_outcome, latest_rc, latest_error_kind
        latest_rc = rc
        if timed_out:
            latest_error_kind = f'{label}_execution_barrier'
            latest_outcome = AgentDeliveryOutcome(
                state='unknown', checked_at=int(time.time()),
                reason=f'{label} 的 admission/beacon drain 无法证明闭合')
            return SupervisorActionResult(
                exit_code=rc, outcome=latest_outcome, continue_ready=False,
                failure_detail=failure_detail)
        if landed(rc):
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
            exit_code=rc, outcome=latest_outcome,
            # 只有 0/1 能证明本次正式动作已经落盘，才允许重新规划下一步。
            # 2 是显式终态、3 是结构拒绝、70 是崩溃；继续循环会在盘面
            # 未变化时重复执行同一个 ready token，付费节点尤其危险。
            continue_ready=landed(rc), failure_detail=failure_detail,
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
        # Supervisor 已在本调用前后各核一次 epoch identity；这里复用同一 code，
        # 避免每个节点再为整棵 dirty worktree 启三次 git 子进程。
        # reviewed apply-edit 可能已经由 canonical producer 写出 terminal
        # failure/committed journal，却在子进程退出事实返回 Agent 前硬杀。先用
        # durable action + producer 事实补拒因或退休记录；prepared 仍留给下面的
        # deterministic recovery，不在这里把进行中的同一动作误记成失败。
        _reconcile_stage2_apply_edit_actions(
            base, sid, code=epoch.code_version)
        # final-review canonical approval 可能已让 planner 越过旧 blocker，但宿主
        # 尚未来得及封 Stage2 席位账。先用 durable action + current approval/seen
        # 补完服务账并退休记录；不重复执行 approval，也不伪造一个 blocker。
        _reconcile_landed_stage2_final_actions(base, sid)
        return operator.next_action_at_code(
            base, sid, code=epoch.code_version)

    def _run_ready_action(
            epoch: ExecutionEpoch, current: OperatorNext,
            provider_session_id: str | None,
            launch_ticket: LaunchTicket) -> SupervisorActionResult:
        nonlocal stage2_commander_turns, stage2_prereview
        nonlocal latest_outcome, latest_rc, latest_error_kind
        if current.execution_token is None:
            raise RuntimeError('ready next 缺 execution token')
        permit = current.execution_token
        if permit.node != 'stage2.blueprint':
            return _run_token(
                epoch, permit, launch_ticket,
                stem=f'ready-{permit.node}')

        scratch = service_lifecycle.open_scratch_session(
            base, sid, epoch.epoch_id)
        prereview_task = reviewer_tasks.build_stage2_prereview_task(
            base, sid, current,
            scratch / f'stage2-prereview-{jobs.new_job_id()}.json',
            epoch_id=epoch.epoch_id)
        if prereview_task is None:
            return _run_token(
                epoch, permit, launch_ticket,
                stem='ready-stage2.blueprint')

        durable_prereview = _find_replayable_stage2_prereview(
            base, sid, task=prereview_task)
        if durable_prereview is not None:
            log = run_log_path(base, run_id)
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, 'ab') as fh:
                fh.write(
                    f'$ [stage2-prereview-replay] '
                    f'{durable_prereview.path}\n'.encode('utf-8'))
            # 未绑定记录没有 provider session，也不把旧 surface 当执行授权；
            # 这里只跳过第二次预审，Blueprint 仍持当前 permit 正常执行。
            stage2_prereview = None
            return _run_token(
                epoch, permit, launch_ticket,
                stem='ready-stage2.blueprint')

        existing = stage2_prereview
        same_existing = (
            existing is not None
            and _stage2_prereview_identity(existing.task)
            == _stage2_prereview_identity(prereview_task))
        # 一次失败/坏 typed 输出已经是真实调用；没有可复用结果时也只让 Blueprint
        # 继续，live blocker 最多再领取剩余一轮，绝不偷偷重做预审。
        if same_existing:
            return _run_token(
                epoch, permit, launch_ticket,
                stem='ready-stage2.blueprint')

        # 这一格必须与 Blueprint pool 实际能取到的配置上限相同：
        # 配置为 1 时宁可不重叠，也不能因多一个 Reviewer 把并发抬到 2。
        shared_capacity = min(
            STAGE2_BLUEPRINT_MAX_CONCURRENCY, load_config().concurrency)
        slot_env = prepare_model_slot_gate(
            scratch / f'stage2-model-slots-{jobs.new_job_id()}',
            capacity=shared_capacity)
        registered = threading.Event()
        finished = threading.Event()
        holder: dict[str, object] = {}
        review_session_id = jobs.new_job_id()

        def reserve_prereview_call() -> None:
            try:
                durable = _reserve_stage2_commander(
                    prereview_task, epoch=epoch, purpose='prereview',
                    session_id=review_session_id)
            except BaseException as exc:  # noqa: BLE001 —— 传回 worker 收口
                holder['error'] = exc
                raise
            holder['reservation_made'] = True
            holder['durable_reservation'] = durable

        def prereview_attempt_started(
                attempt_id: str, kind: str,
                resume_id: str | None) -> None:
            if holder.get('durable_reservation') is not True:
                return
            assert prereview_task.source_sha256 is not None
            assert prereview_task.main_text_sha256 is not None
            stage2_review_ledger.record_attempt(
                base, sid,
                source_sha256=prereview_task.source_sha256,
                main_text_sha256=prereview_task.main_text_sha256,
                session_run_id=review_session_id,
                attempt_id=attempt_id, kind=kind,
                provider_session_id=resume_id)

        def prereview_attempt_finished(
                attempt_id: str, exit_code: int,
                failure_kind: str | None,
                provider_session_id: str | None,
                wire_evidence: bool) -> None:
            if holder.get('durable_reservation') is not True:
                return
            assert prereview_task.source_sha256 is not None
            assert prereview_task.main_text_sha256 is not None
            stage2_review_ledger.finish_attempt(
                base, sid,
                source_sha256=prereview_task.source_sha256,
                main_text_sha256=prereview_task.main_text_sha256,
                session_run_id=review_session_id,
                attempt_id=attempt_id, exit_code=exit_code,
                error_kind=failure_kind,
                provider_session_id=provider_session_id,
                wire_evidence=wire_evidence)

        def prereview_worker() -> None:
            review_exit_code = CRASH
            try:
                prereview_output_schema = (
                    stage2_reviewer_wire.output_schema_for(prereview_task))
                turn = _run_commander_turn(
                    epoch, None,
                    reviewer_prompt=prereview_task.prompt,
                    reviewer_output_schema=prereview_output_schema,
                    fresh_session_run_id=review_session_id,
                    read_only=True,
                    environment_overrides=slot_env,
                    after_spawn_before_wire=reserve_prereview_call,
                    on_registered=registered.set,
                    activity='stage2.global.prereview',
                    on_attempt_started=prereview_attempt_started,
                    on_attempt_finished=prereview_attempt_finished)
                holder['turn'] = turn
                review_exit_code = turn.exit_code
                if turn.exit_code == 0:
                    if turn.final_response is not None:
                        holder['response_sha256'] = _reviewer_response_sha256(
                            turn.final_response)
                        def persist_prereview(
                                input_payload: dict[str, object],
                                response_sha256: str) -> Path:
                            holder['typed_validated'] = True
                            path = _record_stage2_prereview(
                                base, sid, run_id=run_id, epoch=epoch,
                                task=prereview_task,
                                input_payload=input_payload,
                                reviewer_session_run_id=review_session_id,
                                provider_response_sha256=response_sha256)
                            holder['record_path'] = path
                            return path

                        (_input_payload, _response_sha256,
                         _record_path) = (
                            _materialize_stage2_prereview_response(
                                prereview_task, turn.final_response,
                                persist_validated=persist_prereview,
                                preserve_validated=lambda candidate, digest: (
                                    _preserve_validated_reviewer_residue(
                                        base, sid, candidate, digest))))
                    else:
                        # 历史/测试 spawn 仍可物化旧文件；生产
                        # structured driver 成功却缺回答会在协议层拒绝。
                        input_payload = reviewer_tasks.load_stage2_prereview(
                            prereview_task)
                        response_sha256 = _reviewer_request_sha256(
                            prereview_task.request_path)
                        holder['response_sha256'] = response_sha256
                        holder['typed_validated'] = True
                        holder['record_path'] = _record_stage2_prereview(
                            base, sid, run_id=run_id, epoch=epoch,
                            task=prereview_task,
                            input_payload=input_payload,
                            reviewer_session_run_id=review_session_id,
                            provider_response_sha256=response_sha256)
                else:
                    holder.setdefault(
                        'error', RuntimeError(
                            'Stage2 Global 预审 Commander '
                            f'exit {turn.exit_code}'))
            except BaseException as exc:  # noqa: BLE001 —— Blueprint 仍须独立收尾
                holder['error'] = exc
                if (review_exit_code == 0
                        and isinstance(exc, reviewer_tasks.ReviewerTaskError)
                        and not isinstance(holder.get('record_path'), Path)):
                    holder['rejection_reason'] = str(exc)
                    holder['rejection_request_sha256'] = (
                        str(holder['response_sha256'])
                        if isinstance(holder.get('response_sha256'), str)
                        else _reviewer_request_sha256(
                            prereview_task.request_path))
            finally:
                try:
                    if holder.get('reservation_made') is True:
                        _finish_stage2_commander(
                            prereview_task, session_id=review_session_id,
                            durable=(holder.get('durable_reservation') is True),
                            exit_code=review_exit_code)
                        if (holder.get('durable_reservation') is True
                                and isinstance(
                                    holder.get('rejection_reason'), str)
                                and isinstance(
                                    holder.get('rejection_request_sha256'), str)):
                            _record_stage2_rejection(
                                prereview_task,
                                session_run_id=review_session_id,
                                kind='typed_loader',
                                request_sha256=str(
                                    holder['rejection_request_sha256']),
                                reason=str(holder['rejection_reason']))
                except BaseException as exc:  # noqa: BLE001 —— 坏账必须显式留下
                    holder['error'] = exc
                    holder['ledger_error'] = exc
                finally:
                    finished.set()

        thread = threading.Thread(
            target=prereview_worker,
            name=f'stage2-prereview-{run_id}', daemon=True)
        thread.start()
        # Reviewer 的 Popen 先拿到一槽，随后才把 Blueprint 放进同一总预算。
        # 若它在 Popen 前失败，finished 会放行，Blueprint 不被一条坏预审拖死。
        while not registered.wait(0.05):
            if finished.is_set():
                break

        executed: SupervisorActionResult | None = None
        action_error: BaseException | None = None
        try:
            executed = _run_token(
                epoch, permit, launch_ticket,
                stem='ready-stage2.blueprint',
                environment_overrides=slot_env)
        except BaseException as exc:  # noqa: BLE001 —— 先等只读调用闭账再原样抛
            action_error = exc
        finally:
            thread.join()

        error = holder.get('error')
        stage2_prereview = _Stage2PrereviewState(
            task=prereview_task,
            session_run_id=review_session_id,
            error=(
                f'{type(error).__name__}: {error}' if isinstance(error, BaseException)
                else None),
            response_sha256=(
                str(holder['response_sha256'])
                if isinstance(holder.get('response_sha256'), str) else None),
        )
        if action_error is not None:
            raise action_error
        assert executed is not None
        ledger_error = holder.get('ledger_error')
        if isinstance(ledger_error, BaseException):
            # provider 回答即使已经 durable，席位 finish/rejection 审计失败也
            # 不能消费；记录继续留盘，下一 parent 先修复账面再恢复。
            detail = f'{type(ledger_error).__name__}: {ledger_error}'
            latest_outcome = AgentDeliveryOutcome(
                state='unknown', checked_at=int(time.time()),
                reason=(
                    'Blueprint 动作已结束，但 Stage2 预审席位审计没有完整闭合：'
                    f'{detail}'))
            latest_rc = CRASH
            latest_error_kind = 'stage2_prereview_integrity'
            return SupervisorActionResult(
                exit_code=CRASH, outcome=latest_outcome,
                continue_ready=False, failure_detail=detail,
                provider_session_id=None)
        if (holder.get('typed_validated') is True
                and not isinstance(holder.get('record_path'), Path)):
            # Blueprint 可能已经 landed，但一份已付费、typed-valid 的预审没有
            # 形成跨 stop/restart 的恢复凭据；不能把并发支线故障藏在 Blueprint
            # exit 0 后继续，也不能下一 parent 再买同一席。
            detail = (
                f'{type(error).__name__}: {error}'
                if isinstance(error, BaseException)
                else 'Stage2 typed-valid 预审 durable save 未形成记录')
            latest_outcome = AgentDeliveryOutcome(
                state='unknown', checked_at=int(time.time()),
                reason=(
                    'Blueprint 动作已结束，但 Stage2 typed-valid 预审没有形成'
                    f'可重启恢复记录：{detail}'))
            latest_rc = CRASH
            latest_error_kind = 'stage2_prereview_durable'
            return SupervisorActionResult(
                exit_code=CRASH, outcome=latest_outcome,
                continue_ready=False, failure_detail=detail,
                provider_session_id=None)
        # 两条并发支线都收尾后，parent 的节点事实必须仍由 Blueprint action 决定；
        # Commander 自己的 outcome 只留在 commander audit/session 中。
        latest_outcome = executed.outcome
        latest_rc = executed.exit_code
        latest_error_kind = (
            None if executed.exit_code in (0, 1, 2)
            else 'ready-stage2.blueprint_process_exit')
        # 预审席位只靠 ledger/workpack 连续，不把它的 provider session 注入
        # parent；Supervisor 因而不会用该 session 续会。
        return replace(executed, provider_session_id=None)

    def _run_blocked_action(
            epoch: ExecutionEpoch, current: OperatorNext,
            launch_ticket: LaunchTicket) -> SupervisorActionResult:
        """执行 Supervisor 已按关闭注册表选中的 deterministic recovery。"""
        action = current.action
        if (current.state != 'blocked' or action is None
                or action.waiting_on not in queue.DETERMINISTIC_RECOVERY_WAITING_ON):
            raise RuntimeError('Supervisor 交入了未登记的 deterministic blocker')
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
            launch_ticket: LaunchTicket) -> SupervisorActionResult:
        return _execute_reviewer_node_action(
            base, sid, action,
            lambda permit: _run_token(
                epoch, permit, launch_ticket,
                stem=f'reviewer-{action.node}'))

    open_reviewer_seats: dict[tuple[str, str], _ReviewerSeat] = {}

    def _run_reviewer(
            epoch: ExecutionEpoch, current: OperatorNext,
            provider_session_id: str | None,
            launch_ticket: LaunchTicket) -> CommanderTurnResult | None:
        nonlocal pending_reviewer_action, pending_reviewer_binding
        nonlocal pending_reviewer_business_sha256, pending_reviewer_source_task
        nonlocal pending_reviewer_stage2_session_run_id
        nonlocal pending_reviewer_response_sha256
        nonlocal pending_reviewer_record_path, pending_reviewer_replayed
        nonlocal reviewer_correction_task, reviewer_correction_binding
        nonlocal reviewer_correction_reason, reviewer_correction_request_sha256
        nonlocal reviewer_correction_attempts, stage2_commander_turns
        nonlocal stage2_prereview
        nonlocal latest_outcome, latest_rc, latest_error_kind
        assert current.action is not None
        waiting_on = current.action.waiting_on
        assert waiting_on is not None
        binding = (current.surface_seal, waiting_on)
        stage2_turn_session_run_id: str | None = None

        def persist_pending_correction(
                task: reviewer_tasks.ReviewerTask, *,
                reason: str, request_sha256: str) -> None:
            """非 Stage2 席位把拒因落账后才允许进入内存 correction。"""
            if waiting_on in queue.STAGE2_REVIEWER_WAITING_ON:
                return
            seat = open_reviewer_seats.get(binding)
            if seat is None or seat.provider_session_id is None:
                raise reviewer_seat_ledger.ReviewerSeatRefused(
                    'typed 拒因无法绑定到已证明的 reviewer provider session')
            reviewer_seat_ledger.mark_pending_correction(
                base, sid, waiting_on=task.waiting_on,
                business_sha256=task.business_sha256,
                session_run_id=seat.session_run_id,
                provider_session_id=seat.provider_session_id,
                request_sha256=request_sha256, reason=reason)

        def clear_pending_correction(
                task: reviewer_tasks.ReviewerTask, *,
                expected_request_sha256: str | None = None) -> None:
            if waiting_on in queue.STAGE2_REVIEWER_WAITING_ON:
                return
            ledger = reviewer_seat_ledger.read(
                base, sid, waiting_on=task.waiting_on,
                business_sha256=task.business_sha256)
            if ledger is None or ledger.pending_correction is None:
                return
            reviewer_seat_ledger.clear_pending_correction(
                base, sid, waiting_on=task.waiting_on,
                business_sha256=task.business_sha256,
                expected_request_sha256=expected_request_sha256)

        def resume_stage2_pending_rejection(
                task: reviewer_tasks.ReviewerTask,
                pending: stage2_review_ledger.Stage2PendingRejection,
                ) -> CommanderTurnResult:
            """把已 durable 的最新拒因接到 fresh correction，不重买首审。"""
            nonlocal stage2_commander_turns
            nonlocal reviewer_correction_task, reviewer_correction_binding
            nonlocal reviewer_correction_reason
            nonlocal reviewer_correction_request_sha256
            nonlocal reviewer_correction_attempts
            nonlocal latest_rc, latest_error_kind, latest_outcome

            rejection = pending.rejection
            rejected_turn = pending.turn
            stage2_commander_turns = max(
                stage2_commander_turns, rejected_turn.ordinal)
            if rejected_turn.ordinal >= _MAX_STAGE2_EDIT_SEAT:
                reviewed_blockers.add(binding)
                latest_rc = 2
                latest_error_kind = 'stage2_global_rejection_pending'
                latest_outcome = AgentDeliveryOutcome(
                    state='stopped', checked_at=int(time.time()),
                    reason=(
                        'Stage2 global 已恢复上一 parent 的持久拒因，但前 '
                        f'{_MAX_STAGE2_EDIT_SEAT} 席审阅/订正预算已用尽，'
                        f'第 {_MAX_STAGE2_COMMANDER_TURNS} 席只保留给只读 '
                        'final_review；不会启动一个注定被拒的 Commander。'
                        f'第 {rejected_turn.ordinal} 席 {rejection.kind} '
                        f'原拒因：{rejection.reason}；被拒请求 identity='
                        f'{rejection.request_sha256}。'))
                return CommanderTurnResult(
                    exit_code=2, provider_session_id=None,
                    continue_ready=False, outcome=latest_outcome)
            reviewer_correction_task = task
            reviewer_correction_binding = binding
            reviewer_correction_reason = rejection.reason
            reviewer_correction_request_sha256 = rejection.request_sha256
            # ordinal=1 尚未使用订正席；ordinal=2 已使用一次。这个值只控制
            # 人读 attempt 与同 parent 的旧通用上限，真正预算仍由 ledger 守。
            reviewer_correction_attempts = max(0, rejected_turn.ordinal - 1)
            latest_rc = 2
            latest_error_kind = 'stage2_global_rejection_resumed'
            latest_outcome = AgentDeliveryOutcome(
                state='stopped', checked_at=int(time.time()),
                reason=(
                    '已恢复上一 parent 的 Stage2 typed/canonical 待纠错事实；'
                    '下一席将使用当前 self-contained workpack 与精确拒因启动'
                    ' fresh correction session，不复用隐藏 provider 上下文。'))
            return CommanderTurnResult(
                exit_code=2, provider_session_id=None,
                continue_ready=True, outcome=latest_outcome,
                continue_reviewer=True)

        def prepare_stage2_action_before_finish(
                task: reviewer_tasks.ReviewerTask, current: OperatorNext,
                turn: CommanderTurnResult, session_run_id: str,
                holder: dict[str, object], *,
                effective_progress_sha256: str | None = None) -> None:
            """在 Stage2 席位闭账前完成 loader、action 持久化与 scratch 晋升。

            回调把错误留给闭账后的调用者处理：typed-invalid 必须先把一次成功的
            provider 交付记成 ``ok``，随后才能把 loader 拒因绑定回该席；反之，
            typed-valid 的 full action 已在 finish 失败前落稳，绝不会只存在栈上。
            """
            try:
                if turn.final_response is not None:
                    action, response_sha256, record_path = (
                        _materialize_reviewer_response(
                            task, current, turn.final_response,
                            persist_validated=lambda action, response_sha256: (
                                _record_reviewer_action(
                                    base, sid, run_id=run_id, epoch=epoch,
                                    task=task, action=action,
                                    reviewer_session_run_id=session_run_id,
                                    provider_response_sha256=response_sha256,
                                    reviewer_progress_sha256=(
                                        effective_progress_sha256))),
                            preserve_validated=lambda candidate, digest: (
                                _preserve_validated_reviewer_residue(
                                    base, sid, candidate, digest))))
                else:
                    action = reviewer_tasks.load_action(
                        task.request_path, current,
                        submission_options=task.submission_options)
                    response_sha256 = _reviewer_request_sha256(task.request_path)
                    record_path = _record_reviewer_action(
                        base, sid, run_id=run_id, epoch=epoch, task=task,
                        action=action,
                        reviewer_session_run_id=session_run_id,
                        provider_response_sha256=response_sha256,
                        reviewer_progress_sha256=effective_progress_sha256)
                if record_path is None:
                    raise RuntimeError(
                        'Stage2 typed-valid node 决定没有形成 durable action record')
                holder.update({
                    'action': action,
                    'response_sha256': response_sha256,
                    'record_path': record_path,
                })
            except BaseException as exc:  # noqa: BLE001 —— finish 后按类型收口
                holder['error'] = exc

        def run_reviewer_turn(
                task: reviewer_tasks.ReviewerTask,
                prompt: str, *, purpose: str = 'review',
                progress_sha256: str | None = None,
                activity: str | None = None,
                before_stage2_finish: Callable[
                    [CommanderTurnResult, str], None] | None = None,
                ) -> CommanderTurnResult:
            """Stage2 的模型调用预算是产品边界；确定性动作不占轮次。"""
            nonlocal latest_outcome, latest_rc, latest_error_kind
            nonlocal stage2_turn_session_run_id
            reviewer_prompt = stage2_reviewer_wire.prompt_for(task, prompt)
            reviewer_output_schema = stage2_reviewer_wire.output_schema_for(task)
            if waiting_on in queue.STAGE2_REVIEWER_WAITING_ON:
                review_session_id = jobs.new_job_id()
                stage2_turn_session_run_id = review_session_id
                reservation_made = False
                durable_reservation = False
                reservation_error: BaseException | None = None

                def reserve_reviewer_call() -> None:
                    nonlocal reservation_made, durable_reservation
                    nonlocal reservation_error
                    try:
                        durable_reservation = _reserve_stage2_commander(
                            task, epoch=epoch, purpose=purpose,
                            session_id=review_session_id,
                            progress_sha256=progress_sha256)
                    except BaseException as exc:  # noqa: BLE001 —— 转成 typed stop
                        reservation_error = exc
                        raise
                    reservation_made = True

                def attempt_started(
                        attempt_id: str, kind: str,
                        resume_id: str | None) -> None:
                    if not durable_reservation:
                        return
                    assert task.source_sha256 and task.main_text_sha256
                    stage2_review_ledger.record_attempt(
                        base, sid,
                        source_sha256=task.source_sha256,
                        main_text_sha256=task.main_text_sha256,
                        session_run_id=review_session_id,
                        attempt_id=attempt_id, kind=kind,
                        provider_session_id=resume_id)

                def attempt_finished(
                        attempt_id: str, exit_code: int,
                        failure_kind: str | None,
                        provider_session_id: str | None,
                        wire_evidence: bool) -> None:
                    if not durable_reservation:
                        return
                    assert task.source_sha256 and task.main_text_sha256
                    stage2_review_ledger.finish_attempt(
                        base, sid,
                        source_sha256=task.source_sha256,
                        main_text_sha256=task.main_text_sha256,
                        session_run_id=review_session_id,
                        attempt_id=attempt_id, exit_code=exit_code,
                        error_kind=failure_kind,
                        provider_session_id=provider_session_id,
                        wire_evidence=wire_evidence)

                turn: CommanderTurnResult | None = None
                try:
                    turn = _run_commander_turn(
                        epoch, None,
                        reviewer_prompt=reviewer_prompt,
                        reviewer_output_schema=reviewer_output_schema,
                        fresh_session_run_id=review_session_id,
                        launch_ticket=launch_ticket,
                        after_spawn_before_wire=reserve_reviewer_call,
                        activity=activity,
                        on_attempt_started=attempt_started,
                        on_attempt_finished=attempt_finished)
                    if (durable_reservation and turn.exit_code == 0
                            and before_stage2_finish is not None):
                        # Stage2 的 typed-valid 回答必须先变成 durable full action，
                        # 再把席位从 started 补成 ok。这样宿主死在两次原子写之间时，
                        # 新 parent 仍能凭 action+attempt/session/cost 四证补完并重放；
                        # 不能出现“账已 ok、回答只在调用栈里”的不可恢复空窗。
                        before_stage2_finish(turn, review_session_id)
                    if not isinstance(
                            reservation_error,
                            stage2_review_ledger.Stage2ReviewBudgetRefused):
                        return turn
                    exc = reservation_error
                    try:
                        pending_rejection = _pending_stage2_rejection(task)
                    except stage2_review_ledger.Stage2ReviewBudgetRefused:
                        pending_rejection = None
                    rejection_detail = ''
                    if pending_rejection is not None:
                        rejection = pending_rejection.rejection
                        rejection_detail = (
                            f'；上一席 {rejection.kind} 原拒因：'
                            f'{rejection.reason}；被拒请求 identity='
                            f'{rejection.request_sha256}')
                    latest_rc = 2
                    latest_error_kind = 'stage2_global_turn_budget'
                    latest_outcome = AgentDeliveryOutcome(
                        state='stopped', checked_at=int(time.time()),
                        reason=(
                            'Stage2 global 拒绝继续调用 Commander：'
                            f'{exc}{rejection_detail}。每个主体最多 '
                            f'{_MAX_STAGE2_COMMANDER_TURNS} 席'
                            f'（前 {_MAX_STAGE2_EDIT_SEAT} 席审阅/订正，第 '
                            f'{_MAX_STAGE2_COMMANDER_TURNS} 席只读终审），'
                            '且每席必须减少未闭合项或改变精确拒因'))
                    return CommanderTurnResult(
                        exit_code=2,
                        provider_session_id=None,
                        continue_ready=False,
                        outcome=latest_outcome)
                finally:
                    if reservation_made:
                        _finish_stage2_commander(
                            task, session_id=review_session_id,
                            durable=durable_reservation,
                            exit_code=(
                                turn.exit_code if turn is not None else CRASH))
            # 非 Stage2 blocker：每个 binding 一席 fresh logical/provider session；
            # 同一 binding 内的纠错续用该席，纠错次数按 (sid, blocker, business)
            # 持久记账。surface 变化（如返修落盘）即新 binding、新席。
            seat = open_reviewer_seats.get(binding)
            if seat is None:
                seat = _ReviewerSeat(
                    session_run_id=jobs.new_job_id(),
                    prompt_sha256=_sha256_bytes(reviewer_prompt.encode('utf-8')),
                    waiting_on=waiting_on,
                    business_sha256=task.business_sha256)
                open_reviewer_seats[binding] = seat
            seat_turn_id = jobs.new_job_id()
            seat_reserved = False
            seat_reservation_error: BaseException | None = None
            seat_identity = dict(
                waiting_on=seat.waiting_on,
                business_sha256=seat.business_sha256)

            def reserve_seat_turn() -> None:
                nonlocal seat_reserved, seat_reservation_error
                try:
                    reviewer_seat_ledger.reserve(
                        base, sid, purpose=purpose,
                        session_run_id=seat.session_run_id,
                        turn_id=seat_turn_id, parent_run_id=run_id,
                        epoch_id=epoch.epoch_id, **seat_identity)
                except BaseException as exc:  # noqa: BLE001 —— 转成 typed stop
                    seat_reservation_error = exc
                    raise
                seat_reserved = True

            def seat_attempt_started(
                    attempt_id: str, kind: str, resume_id: str | None) -> None:
                if seat_reserved:
                    reviewer_seat_ledger.record_attempt(
                        base, sid, turn_id=seat_turn_id, attempt_id=attempt_id,
                        kind=kind, provider_session_id=resume_id,
                        **seat_identity)

            def seat_attempt_finished(
                    attempt_id: str, exit_code: int, failure_kind: str | None,
                    provider_id: str | None, wire_evidence: bool) -> None:
                if seat_reserved:
                    reviewer_seat_ledger.finish_attempt(
                        base, sid, turn_id=seat_turn_id, attempt_id=attempt_id,
                        exit_code=exit_code, error_kind=failure_kind,
                        provider_session_id=provider_id,
                        wire_evidence=wire_evidence, **seat_identity)

            seat_turn: CommanderTurnResult | None = None
            try:
                seat_turn = _run_commander_turn(
                    epoch, None,
                    reviewer_prompt=reviewer_prompt,
                    reviewer_output_schema=reviewer_output_schema,
                    fresh_session_run_id=seat.session_run_id,
                    seat_provider_session_id=seat.provider_session_id,
                    seat_turns_recorded=seat.turns_recorded,
                    seat_subject_sha256=seat.prompt_sha256,
                    close_seat=False,
                    launch_ticket=launch_ticket,
                    after_spawn_before_wire=reserve_seat_turn,
                    activity=activity,
                    on_attempt_started=seat_attempt_started,
                    on_attempt_finished=seat_attempt_finished)
                seat.started = seat.started or seat_turn.seat_started
                if seat_turn.seat_provider_session_id is not None:
                    seat.provider_session_id = seat_turn.seat_provider_session_id
                seat.turns_recorded = seat_turn.seat_turns_recorded
                seat.last_observation = latest_observation
                seat.last_rc = latest_commander_rc
                seat.last_outcome = latest_commander_outcome
                seat.last_error_kind = latest_commander_error_kind
                if isinstance(
                        seat_reservation_error,
                        reviewer_seat_ledger.ReviewerSeatRefused):
                    _close_reviewer_seat(binding, error_kind='reviewer_seat_budget')
                    latest_rc = 2
                    latest_error_kind = 'reviewer_seat_budget'
                    latest_outcome = AgentDeliveryOutcome(
                        state='stopped', checked_at=int(time.time()),
                        reason=(
                            f'{waiting_on} 拒绝继续调用 Commander：'
                            f'{seat_reservation_error}'))
                    return CommanderTurnResult(
                        exit_code=2, provider_session_id=None,
                        continue_ready=False, outcome=latest_outcome)
                return seat_turn
            finally:
                if seat_reserved:
                    reviewer_seat_ledger.finish(
                        base, sid, turn_id=seat_turn_id,
                        exit_code=(
                            seat_turn.exit_code if seat_turn is not None
                            else CRASH),
                        **seat_identity)

        if reviewer_correction_task is not None:
            if reviewer_correction_binding != binding:
                _close_reviewer_seat(
                    reviewer_correction_binding,
                    error_kind='commander_reviewer_binding_changed')
                reviewer_correction_task = None
                reviewer_correction_binding = None
                reviewer_correction_reason = None
                reviewer_correction_request_sha256 = None
                reviewer_correction_attempts = 0
                return None
            task = reviewer_correction_task
            reason = reviewer_correction_reason
            previous_request_sha256 = reviewer_correction_request_sha256
            if reason is None or previous_request_sha256 is None:
                raise RuntimeError('pending reviewer correction 缺拒绝事实')
            attempt = reviewer_correction_attempts + 1
            correction_progress = (
                stage2_review_ledger.progress_with_feedback(
                    task.progress_sha256,
                    reason=reason, request_sha256=previous_request_sha256)
                if (waiting_on in queue.STAGE2_REVIEWER_WAITING_ON
                    and task.progress_sha256 is not None) else None)
            prepared_stage2: dict[str, object] = {}
            turn = run_reviewer_turn(
                task,
                reviewer_tasks.correction_prompt(
                    task, reason,
                    request_sha256=previous_request_sha256,
                    attempt=attempt),
                purpose='correction', progress_sha256=correction_progress,
                activity=_reviewer_activity(task),
                before_stage2_finish=(
                    lambda got, session_id: prepare_stage2_action_before_finish(
                        task, current, got, session_id, prepared_stage2,
                        effective_progress_sha256=correction_progress)
                    if waiting_on in queue.STAGE2_REVIEWER_WAITING_ON else None))
            if turn.exit_code != 0:
                return turn
            request_sha256 = _reviewer_turn_response_sha256(
                turn, task.request_path)
            if request_sha256 == previous_request_sha256:
                no_progress_reason = (
                    'Commander 收到 typed 拒绝反馈后没有改变请求字节；'
                    '已阻止无效纠错循环')
                if waiting_on in queue.STAGE2_REVIEWER_WAITING_ON:
                    _record_stage2_rejection(
                        task,
                        session_run_id=stage2_turn_session_run_id,
                        kind='typed_loader',
                        request_sha256=request_sha256,
                        reason=no_progress_reason)
                    durable_no_progress = prepared_stage2.get('record_path')
                    if isinstance(durable_no_progress, Path):
                        # callback 已在 ledger.finish 前保存了 full action。只有
                        # no-progress 拒因成功绑定本席后才消费这份可执行记录；若
                        # 拒因写盘失败，上面的异常会保留 record 并以 unknown 收口。
                        _discard_reviewer_action_record(
                            base, durable_no_progress)
                reviewer_correction_task = None
                reviewer_correction_binding = None
                reviewer_correction_reason = None
                reviewer_correction_request_sha256 = None
                reviewer_correction_attempts = 0
                _close_reviewer_seat(
                    binding, error_kind='commander_reviewer_no_progress')
                latest_rc = 2
                latest_error_kind = 'commander_reviewer_no_progress'
                latest_outcome = AgentDeliveryOutcome(
                    state='stopped', checked_at=int(time.time()),
                    reason=no_progress_reason)
                return CommanderTurnResult(
                    exit_code=2,
                    provider_session_id=turn.provider_session_id,
                    continue_ready=False, outcome=latest_outcome)
            try:
                validated_record_path: Path | None = None
                if waiting_on in queue.STAGE2_REVIEWER_WAITING_ON:
                    prepared_error = prepared_stage2.get('error')
                    if isinstance(prepared_error, BaseException):
                        raise prepared_error
                    candidate_action = prepared_stage2.get('action')
                    candidate_digest = prepared_stage2.get('response_sha256')
                    candidate_record = prepared_stage2.get('record_path')
                    if (not isinstance(candidate_action, (
                            reviewer_tasks.ReviewerNodeAction,
                            reviewer_tasks.ReviewerApprovalAction,
                            reviewer_tasks.ReviewerStopAction))
                            or not isinstance(candidate_digest, str)
                            or not isinstance(candidate_record, Path)):
                        raise RuntimeError(
                            'Stage2 correction finish 前没有完整 durable action')
                    corrected_action = candidate_action
                    request_sha256 = candidate_digest
                    validated_record_path = candidate_record
                elif turn.final_response is not None:
                    (corrected_action, request_sha256,
                     validated_record_path) = (
                        _materialize_reviewer_response(
                            task, current, turn.final_response,
                            persist_validated=lambda action, response_sha256: (
                                _record_reviewer_action(
                                    base, sid, run_id=run_id, epoch=epoch,
                                    task=task, action=action,
                                    reviewer_session_run_id=(
                                        stage2_turn_session_run_id
                                        if waiting_on
                                        in queue.STAGE2_REVIEWER_WAITING_ON
                                        else None),
                                    provider_response_sha256=(
                                        response_sha256
                                        if waiting_on
                                        in queue.STAGE2_REVIEWER_WAITING_ON
                                        else None))),
                            preserve_validated=lambda candidate, digest: (
                                _preserve_validated_reviewer_residue(
                                    base, sid, candidate, digest))))
                else:
                    corrected_action = reviewer_tasks.load_action(
                        task.request_path, current,
                        submission_options=task.submission_options)
                    validated_record_path = _record_reviewer_action(
                        base, sid, run_id=run_id, epoch=epoch, task=task,
                        action=corrected_action,
                        reviewer_session_run_id=(
                            stage2_turn_session_run_id
                            if waiting_on in queue.STAGE2_REVIEWER_WAITING_ON
                            else None),
                        provider_response_sha256=(
                            request_sha256
                            if waiting_on in queue.STAGE2_REVIEWER_WAITING_ON
                            else None))
            except reviewer_tasks.ReviewerTaskError as exc:
                if waiting_on in queue.STAGE2_REVIEWER_WAITING_ON:
                    _record_stage2_rejection(
                        task,
                        session_run_id=stage2_turn_session_run_id,
                        kind='typed_loader',
                        request_sha256=request_sha256,
                        reason=str(exc))
                persist_pending_correction(
                    task, reason=str(exc), request_sha256=request_sha256)
                stage2_edit_budget_exhausted = (
                    waiting_on in queue.STAGE2_REVIEWER_WAITING_ON
                    and stage2_commander_turns >= _MAX_STAGE2_EDIT_SEAT)
                if (stage2_edit_budget_exhausted
                        or attempt >= _MAX_REVIEWER_CORRECTIONS):
                    reviewer_correction_task = None
                    reviewer_correction_binding = None
                    reviewer_correction_reason = None
                    reviewer_correction_request_sha256 = None
                    reviewer_correction_attempts = 0
                    _close_reviewer_seat(
                        binding, error_kind='commander_reviewer_request')
                    latest_rc = 2
                    latest_error_kind = 'commander_reviewer_request'
                    latest_outcome = AgentDeliveryOutcome(
                        state='stopped', checked_at=int(time.time()),
                        reason=(
                            'Commander reviewer typed request 连续纠正后仍未闭合：'
                            f'{exc}'
                            + (
                                f'；Stage2 global 前 {_MAX_STAGE2_EDIT_SEAT} 席'
                                '审阅/订正预算已用尽，第 '
                                f'{_MAX_STAGE2_COMMANDER_TURNS} 席只保留给'
                                '只读 final_review'
                                if stage2_edit_budget_exhausted else '')))
                    return CommanderTurnResult(
                        exit_code=2,
                        provider_session_id=turn.provider_session_id,
                        continue_ready=False, outcome=latest_outcome)
                reviewer_correction_reason = str(exc)
                reviewer_correction_request_sha256 = request_sha256
                reviewer_correction_attempts = attempt
                return CommanderTurnResult(
                    exit_code=turn.exit_code,
                    provider_session_id=turn.provider_session_id,
                    continue_ready=True, outcome=turn.outcome,
                    continue_reviewer=True)

            clear_pending_correction(
                task, expected_request_sha256=previous_request_sha256)
            reviewer_correction_task = None
            reviewer_correction_binding = None
            reviewer_correction_reason = None
            reviewer_correction_request_sha256 = None
            reviewer_correction_attempts = 0
            pending_reviewer_action = corrected_action
            pending_reviewer_binding = binding
            pending_reviewer_business_sha256 = task.business_sha256
            pending_reviewer_source_task = task
            pending_reviewer_response_sha256 = request_sha256
            pending_reviewer_stage2_session_run_id = (
                stage2_turn_session_run_id
                if waiting_on in queue.STAGE2_REVIEWER_WAITING_ON else None)
            pending_reviewer_record_path = validated_record_path
            pending_reviewer_replayed = False
            return CommanderTurnResult(
                exit_code=turn.exit_code,
                provider_session_id=turn.provider_session_id,
                continue_ready=True, outcome=turn.outcome,
                continue_reviewer=True)

        if pending_reviewer_action is not None:
            if pending_reviewer_binding != binding:
                _close_reviewer_seat(
                    pending_reviewer_binding,
                    error_kind='commander_reviewer_binding_changed')
                pending_reviewer_action = None
                pending_reviewer_binding = None
                pending_reviewer_business_sha256 = None
                pending_reviewer_source_task = None
                pending_reviewer_response_sha256 = None
                pending_reviewer_stage2_session_run_id = None
                pending_reviewer_record_path = None
                pending_reviewer_replayed = False
                return None
            action = pending_reviewer_action
            business_sha256 = pending_reviewer_business_sha256
            source_task = pending_reviewer_source_task
            source_response_sha256 = pending_reviewer_response_sha256
            source_stage2_session_run_id = (
                pending_reviewer_stage2_session_run_id)
            pending_reviewer_action = None
            pending_reviewer_binding = None
            pending_reviewer_business_sha256 = None
            pending_reviewer_source_task = None
            pending_reviewer_response_sha256 = None
            pending_reviewer_stage2_session_run_id = None
            record_path = pending_reviewer_record_path
            replayed = pending_reviewer_replayed
            pending_reviewer_record_path = None
            pending_reviewer_replayed = False
            if business_sha256 is None or source_task is None:
                raise RuntimeError('pending reviewer action 缺业务主体或 workpack')
            if (waiting_on in queue.STAGE2_REVIEWER_WAITING_ON
                    and isinstance(action, reviewer_tasks.ReviewerNodeAction)
                    and action.node == 'stage2.global'
                    and tuple(action.flags[:1]) == ('--apply-edit',)):
                try:
                    _preflight_stage2_reviewer_action(
                        base, sid, epoch=epoch, action=action)
                except _Stage2ReviewerPreflightRejected as exc:
                    preflight_reason = exc.safe_summary
                    # 到达这里的 action 已先形成 ReviewerActionRecord，产出它的
                    # Stage2 席位也已经 finish。必须把拒因绑定原 session/raw digest
                    # 后才退休 record；任何账/删除失败都保持 record 并显式 unknown。
                    if (source_stage2_session_run_id is None
                            or source_response_sha256 is None
                            or record_path is None):
                        latest_rc = CRASH
                        latest_error_kind = 'stage2_global_preflight_identity'
                        latest_outcome = AgentDeliveryOutcome(
                            state='unknown', checked_at=int(time.time()),
                            reason=(
                                'Stage2 producer preflight 已拒绝 action，但 durable '
                                'record/原席位/raw digest 身份不完整；拒绝消费或纠错'))
                        return CommanderTurnResult(
                            exit_code=CRASH, provider_session_id=None,
                            continue_ready=False, outcome=latest_outcome)
                    try:
                        _record_stage2_rejection(
                            source_task,
                            session_run_id=source_stage2_session_run_id,
                            # loader 已经接受该回答；这是 canonical producer 的
                            # baseline-aware 业务判据前移，不得伪装成 schema 拒绝。
                            kind='canonical_action',
                            request_sha256=source_response_sha256,
                            reason=preflight_reason)
                        # 这里不能用告警后吞错的通用 best-effort 清理：拒因与
                        # action record 的交接若没闭合，本 parent 必须 exit70。
                        uploads.discard_edit_plan(base, record_path)
                    except Exception as persist_exc:  # noqa: BLE001 —— 审计/交接失败即 unknown
                        latest_rc = CRASH
                        latest_error_kind = 'stage2_global_preflight_audit'
                        latest_outcome = AgentDeliveryOutcome(
                            state='unknown', checked_at=int(time.time()),
                            reason=(
                                'Stage2 producer preflight 拒因未能完整持久交接；'
                                f'{type(persist_exc).__name__}: {persist_exc}；'
                                'durable action record 保留，禁止执行回答'))
                        return CommanderTurnResult(
                            exit_code=CRASH, provider_session_id=None,
                            continue_ready=False, outcome=latest_outcome)
                    action_sha256 = reviewer_tasks.action_business_sha256(action)
                    rejected_reviewer_business_actions.add(
                        (business_sha256, action_sha256))
                    latest_rc = 2
                    latest_error_kind = 'stage2_global_preflight_rejected'
                    latest_outcome = AgentDeliveryOutcome(
                        state='stopped', checked_at=int(time.time()),
                        reason=(
                            'Stage2 durable apply-edit 在 canonical Executor 前被'
                            f'唯一 producer 候选边界拒绝：{preflight_reason}'))
                    if stage2_commander_turns >= _MAX_STAGE2_EDIT_SEAT:
                        return CommanderTurnResult(
                            exit_code=2, provider_session_id=None,
                            continue_ready=False, outcome=latest_outcome)
                    reviewer_correction_task = source_task
                    reviewer_correction_binding = binding
                    reviewer_correction_reason = preflight_reason
                    reviewer_correction_request_sha256 = source_response_sha256
                    reviewer_correction_attempts = 0
                    return CommanderTurnResult(
                        exit_code=2, provider_session_id=None,
                        continue_ready=True, outcome=latest_outcome,
                        continue_reviewer=True)
                except Exception as exc:  # noqa: BLE001 —— 完整性/读取异常不能伪装业务拒绝
                    latest_rc = CRASH
                    latest_error_kind = 'stage2_global_preflight'
                    latest_outcome = AgentDeliveryOutcome(
                        state='unknown', checked_at=int(time.time()),
                        reason=(
                            'Stage2 durable apply-edit 的 producer preflight 结论未知；'
                            f'{type(exc).__name__}: {exc}；action record 保留且未启动 '
                            'canonical Executor'))
                    return CommanderTurnResult(
                        exit_code=CRASH, provider_session_id=None,
                        continue_ready=False, outcome=latest_outcome)
            action_sha256 = reviewer_tasks.action_business_sha256(action)
            business_action = (business_sha256, action_sha256)
            if (business_action in completed_reviewer_business_actions
                    or business_action in rejected_reviewer_business_actions):
                no_progress_reason = (
                    'Commander 在同一 Stage2 业务主体上重复提交了已经执行或拒绝的'
                    '等价 typed 动作；仅 review surface/workpack 变化不构成修复进展，'
                    '已阻止无效循环')
                if waiting_on in queue.STAGE2_REVIEWER_WAITING_ON:
                    if source_stage2_session_run_id is None:
                        raise RuntimeError(
                            'Stage2 等价 typed 动作缺产出席位，不能安全消费 durable '
                            'action record')
                    _record_stage2_rejection(
                        source_task,
                        session_run_id=source_stage2_session_run_id,
                        kind='typed_loader',
                        request_sha256=(
                            source_response_sha256
                            or _reviewer_request_sha256(
                                source_task.request_path)),
                        reason=no_progress_reason)
                # full action 已在 Stage2 席位 finish 前落盘。必须先让拒因
                # durable，再退休它；若拒因持久化失败，上面的异常会保留记录，
                # 下一 parent 关闭式恢复，绝不能把同一决定重新执行。
                if record_path is not None:
                    _discard_reviewer_action_record(base, record_path)
                latest_rc = 2
                latest_error_kind = 'commander_reviewer_no_progress'
                latest_outcome = AgentDeliveryOutcome(
                    state='stopped', checked_at=int(time.time()),
                    reason=no_progress_reason)
                return CommanderTurnResult(
                    exit_code=2,
                    provider_session_id=provider_session_id,
                    continue_ready=False,
                    outcome=latest_outcome)
            if isinstance(action, reviewer_tasks.ReviewerStopAction):
                # 登记 reviewer 的 typed non-approval 是明确终态，不是
                # approval，也不是待 Executor 执行的 node。它不产生任何
                # canonical 子进程/正式写盘，只把模型给出的证据性拒绝理由
                # 原样投影为本次 AgentRun stop。
                completed_reviewer_business_actions.add(business_action)
                clear_pending_correction(source_task)
                _close_reviewer_seat(binding)
                latest_rc = 2
                latest_error_kind = 'commander_reviewer_stopped'
                latest_outcome = AgentDeliveryOutcome(
                    state='stopped', checked_at=int(time.time()),
                    reason=(
                        f'Commander reviewer 明确拒绝批准 {waiting_on}：'
                        f'{action.reason}'))
                return CommanderTurnResult(
                    exit_code=2,
                    provider_session_id=provider_session_id,
                    continue_ready=False, outcome=latest_outcome)
            if isinstance(action, reviewer_tasks.ReviewerNodeAction):
                try:
                    result = _run_node_review_action(
                        epoch, action, launch_ticket)
                except Exception:  # noqa: BLE001 —— 先区分 surface 竞态
                    if waiting_on in queue.STAGE2_REVIEWER_WAITING_ON:
                        raise
                    try:
                        live = _read_operator_next(epoch)
                    except Exception:
                        raise
                    live_waiting_on = (
                        live.action.waiting_on
                        if live.state == 'blocked' and live.action is not None
                        else None)
                    live_binding = (
                        (live.surface_seal, live_waiting_on)
                        if live_waiting_on is not None else None)
                    if live_binding == binding:
                        raise
                    # permit 尚未铸成，canonical 子进程也尚未启动。
                    # durable record 保留：新 surface 若仍属同 business，
                    # 下一窗口会重走当前 loader 后零模型重放；若
                    # business 已变，_find_replayable 会把旧记录汰汰。
                    _close_reviewer_seat(
                        binding,
                        error_kind='commander_reviewer_binding_changed')
                    latest_rc = 2
                    latest_error_kind = 'commander_reviewer_binding_changed'
                    latest_outcome = AgentDeliveryOutcome(
                        state='stopped', checked_at=int(time.time()),
                        reason=(
                            'reviewer typed 动作在铸 permit 前发现 live '
                            'surface 已变；已关闭旧席并返回 planner '
                            '以 durable 决定零模型重放'))
                    return CommanderTurnResult(
                        exit_code=2,
                        provider_session_id=provider_session_id,
                        continue_ready=True, outcome=latest_outcome)
            elif isinstance(action, reviewer_tasks.ReviewerApprovalAction):
                result = _run_approval_action(
                    epoch, action, launch_ticket)
            else:  # pragma: no cover —— ReviewerAction union 的未来扩展须显式接线
                raise TypeError(f'未接线的 reviewer action：{type(action).__name__}')
            stage2_apply_edit_exit2 = (
                result.exit_code == 2
                and waiting_on in queue.STAGE2_REVIEWER_WAITING_ON
                and isinstance(action, reviewer_tasks.ReviewerNodeAction)
                and '--apply-edit' in action.flags
            )
            if stage2_apply_edit_exit2:
                # exit 2 本身分不出“已经形成 terminal failure”与“canonical/
                # formal WAL 已接管、只差 0-wire 补尾”。必须先重读唯一 planner：
                # prepared 优先交 Executor，不能提前给原回答记 rejection；terminal
                # sidecar 则由 planner 前 reconcile 精确绑回原席并消费 action record。
                live = _read_operator_next(epoch)
                live_waiting_on = (
                    live.action.waiting_on
                    if live.state == 'blocked' and live.action is not None
                    else None)
                if live_waiting_on == queue.WAITING_ON_STAGE2_EDIT_COMMIT:
                    latest_rc = 2
                    latest_error_kind = 'stage2_global_action_commit_pending'
                    latest_outcome = AgentDeliveryOutcome(
                        state='stopped', checked_at=int(time.time()),
                        reason=(
                            'Stage2 apply-edit 已由 canonical prepared journal '
                            '接管；返回 planner 以 exact action 交 Executor 零模型补尾'))
                    return CommanderTurnResult(
                        exit_code=2, provider_session_id=None,
                        continue_ready=True, outcome=latest_outcome)
                if live_waiting_on == queue.WAITING_ON_STAGE2_EDIT_CORRECTION:
                    # 正常新 producer 由 _reconcile_stage2_apply_edit_actions 使用
                    # terminal sidecar 落拒因。旧盘/测试若没有可读 record，仍以当前
                    # 子进程的明确 exit2 摘要补同一事实，绝不无因开启 correction。
                    pending_rejection = _pending_stage2_rejection(source_task)
                    stage2_rejection = result.failure_detail or (
                        'canonical Stage2 apply-edit 返回 exit 2，正式产物未落盘')
                    if pending_rejection is None:
                        _record_stage2_rejection(
                            source_task,
                            session_run_id=source_stage2_session_run_id,
                            kind='canonical_action',
                            request_sha256=(
                                source_response_sha256
                                or _reviewer_request_sha256(
                                    source_task.request_path)),
                            reason=stage2_rejection)
                        if record_path is not None:
                            _discard_reviewer_action_record(base, record_path)
                    rejected_reviewer_business_actions.add(business_action)
                    if stage2_commander_turns >= _MAX_STAGE2_EDIT_SEAT:
                        latest_rc = 2
                        latest_error_kind = 'stage2_global_action_rejected'
                        latest_outcome = AgentDeliveryOutcome(
                            state='stopped', checked_at=int(time.time()),
                            reason=(
                                f'Stage2 global 第 {stage2_commander_turns} 次 '
                                'Commander apply-edit 被确定性边界拒绝，持久预算'
                                '已用尽。'
                                f'{pending_rejection.reason if pending_rejection else stage2_rejection}'))
                        return CommanderTurnResult(
                            exit_code=2, provider_session_id=None,
                            continue_ready=False, outcome=latest_outcome)
                    latest_rc = 2
                    latest_error_kind = 'stage2_global_action_replan'
                    latest_outcome = AgentDeliveryOutcome(
                        state='stopped', checked_at=int(time.time()),
                        reason=(
                            'Stage2 apply-edit 拒因已持久绑定原席位；返回 planner '
                            '重读 live reviewed-edit correction 业务面'))
                    return CommanderTurnResult(
                        exit_code=2, provider_session_id=None,
                        continue_ready=True, outcome=latest_outcome)
            if (result.exit_code == 2
                    and waiting_on in queue.STAGE2_REVIEWER_WAITING_ON
                    and (source_stage2_session_run_id is not None
                         or not replayed)):
                # canonical 子进程已经明确证明未落盘；先把精确 stderr 摘要绑回
                # 产出该 typed 动作的席位。后面的 in-memory correction 只是这个
                # 持久事实的即时消费，parent/epoch 中断也不能抹掉根因。
                stage2_rejection = result.failure_detail or (
                    f'canonical {action.kind} action 返回 exit 2，正式产物未落盘')
                _record_stage2_rejection(
                    source_task,
                    session_run_id=source_stage2_session_run_id,
                    kind='canonical_action',
                    request_sha256=(
                        source_response_sha256
                        or _reviewer_request_sha256(source_task.request_path)),
                    reason=stage2_rejection)
            final_stage2_approval_landed = (
                result.exit_code == 0
                and waiting_on == queue.WAITING_ON_STAGE2_FINAL_REVIEW
                and isinstance(action, reviewer_tasks.ReviewerApprovalAction)
            )
            if (record_path is not None and landed(result.exit_code)
                    and not final_stage2_approval_landed):
                # 已落盘的决定不再适用。exit 2 需先重读 live
                # binding 才能判断是真正拒绝还是 surface 竞态，
                # 不能在这里提前删掉；结论未知则一律保留。
                # final approval exit0 还要先封 Stage2 review ledger；它在下方
                # 单独按 landed → mark_complete → retire 顺序处理。
                _discard_reviewer_action_record(base, record_path)
            if (result.exit_code == 2
                    and waiting_on not in queue.STAGE2_REVIEWER_WAITING_ON):
                # canonical 动作执行期间，人工 edit/adopt 可能已经
                # 改变 surface。此时旧 action 的 exit 2 只能证明它没有
                # landed，不能再把旧 workpack/revision 登记成同席纠错；
                # 否则 correction 又被要求逐字保留已过期 binding，必然
                # 浪费下一轮付费席位。
                try:
                    live = _read_operator_next(epoch)
                except Exception as exc:  # noqa: BLE001 —— 证明不了就不纠错
                    _close_reviewer_seat(
                        binding,
                        error_kind='commander_reviewer_surface_recheck')
                    latest_rc = CRASH
                    latest_error_kind = 'commander_reviewer_surface_recheck'
                    latest_outcome = AgentDeliveryOutcome(
                        state='unknown', checked_at=int(time.time()),
                        reason=(
                            'canonical reviewer 动作被拒绝后无法重读 '
                            f'live surface：{type(exc).__name__}: {exc}；'
                            '已禁止沿用旧 workpack 纠错'))
                    return CommanderTurnResult(
                        exit_code=CRASH,
                        provider_session_id=provider_session_id,
                        continue_ready=False, outcome=latest_outcome)
                live_waiting_on = (
                    live.action.waiting_on
                    if live.state == 'blocked' and live.action is not None
                    else None)
                live_binding = (
                    (live.surface_seal, live_waiting_on)
                    if live_waiting_on is not None else None)
                if live_binding != binding:
                    _close_reviewer_seat(
                        binding,
                        error_kind='commander_reviewer_binding_changed')
                    # 返回 planner 重读新 next；不设 continue_reviewer，
                    # 不落 pending correction，也不复用旧 provider 席。
                    return CommanderTurnResult(
                        exit_code=result.exit_code,
                        provider_session_id=provider_session_id,
                        continue_ready=True, outcome=result.outcome)
                # 只有 live transport binding 仍稳定，才能把 exit 2
                # 解释为这份 durable 决定在当前 surface 上真正
                # 被 canonical 拒绝并消费。binding 变化分支已在上方
                # 返回，记录必须留给新窗口按 business identity 复验。
                if record_path is not None:
                    _discard_reviewer_action_record(base, record_path)
            elif result.exit_code == 2 and record_path is not None:
                # Stage2 的决定有独立书内 durable/correction 路，
                # 不套用普通 reviewer 的 transport-binding 重放。
                _discard_reviewer_action_record(base, record_path)
            if landed(result.exit_code):
                completed_reviewer_business_actions.add(business_action)
                clear_pending_correction(source_task)
                _close_reviewer_seat(binding)
            elif result.exit_code == 2 and replayed:
                # Stage2 的纠错包自足且每轮本来就开新会话，所以重放决定被拒后可
                # 直接进入增量纠错；其他 reviewer 仍没有可续的 provider 上下文。
                rejected_reviewer_business_actions.add(business_action)
                if waiting_on in queue.STAGE2_REVIEWER_WAITING_ON:
                    rejection = result.failure_detail or (
                        f'canonical {action.kind} action 返回 exit 2，正式产物未落盘')
                    reviewer_correction_task = source_task
                    reviewer_correction_binding = binding
                    reviewer_correction_reason = (
                        f'{rejection}；请按原 workpack 只修正上一份 typed 动作')
                    reviewer_correction_request_sha256 = (
                        source_response_sha256
                        or _reviewer_request_sha256(source_task.request_path))
                    reviewer_correction_attempts = 0
                    return CommanderTurnResult(
                        exit_code=result.exit_code,
                        provider_session_id=None,
                        continue_ready=True, outcome=result.outcome,
                        continue_reviewer=True)
                # 普通 blocker 的 durable record 已证明该 business 发生过
                # 一次付费首审。重放又被 canonical 拒绝时，不能删掉
                # record 后就把它包装成第二次 review；即使旧版尚未
                # 落 ReviewerSeatLedger，durable 决定本身也是席位已用的
                # 证据。Stage2 有独立书内 durable/correction 路，已在上分支。
                rejection = result.failure_detail or (
                    f'canonical {action.kind} action 返回 exit 2，正式产物未落盘')
                latest_rc = 2
                latest_error_kind = 'commander_reviewer_replay_rejected'
                latest_outcome = AgentDeliveryOutcome(
                    state='stopped', checked_at=int(time.time()),
                    reason=(
                        '持久 reviewer 决定经零模型重放后仍被确定性'
                        f'边界拒绝：{rejection}。同一业务主体的首审席位'
                        '在重启/epoch 切换后不返还；请用人工 typed 入口'
                        '闭合，或先形成真实新业务 surface。'))
                return CommanderTurnResult(
                    exit_code=2,
                    provider_session_id=provider_session_id,
                    continue_ready=False, outcome=latest_outcome)
            elif (result.exit_code == 2
                  and (waiting_on in queue.STAGE2_REVIEWER_WAITING_ON
                       or open_reviewer_seats.get(binding) is not None
                       and open_reviewer_seats[binding].provider_session_id
                       is not None)):
                # Reviewer 的 typed 动作由 Commander 负责修正。显式终态证明没有
                # landed；只要 blocker/surface 未变，就把拒绝事实连同紧凑 workpack
                # 送进新的 Stage2 logical/provider session，而不是结束整书 parent。
                # 等价动作由上面的业务摘要关闭式拦住。
                rejected_reviewer_business_actions.add(business_action)
                rejection = result.failure_detail or (
                    f'canonical {action.kind} action 返回 exit 2，正式产物未落盘')
                if (waiting_on in queue.STAGE2_REVIEWER_WAITING_ON
                        and stage2_commander_turns
                        >= _MAX_STAGE2_EDIT_SEAT):
                    latest_rc = 2
                    latest_error_kind = 'stage2_global_action_rejected'
                    latest_outcome = AgentDeliveryOutcome(
                        state='stopped', checked_at=int(time.time()),
                        reason=(
                            f'Stage2 global 第 {stage2_commander_turns} 次 Commander '
                            '动作仍被确定性边界拒绝，持久预算已用尽。'
                            f'{rejection}'))
                    return CommanderTurnResult(
                        exit_code=2,
                        provider_session_id=provider_session_id,
                        continue_ready=False, outcome=latest_outcome)
                reviewer_correction_task = source_task
                reviewer_correction_binding = binding
                correction_reason = (
                    f'{rejection}；请按原 workpack 只修正上一份 typed 动作')
                correction_request_sha256 = (
                    source_response_sha256
                    or _reviewer_request_sha256(source_task.request_path))
                persist_pending_correction(
                    source_task, reason=correction_reason,
                    request_sha256=correction_request_sha256)
                reviewer_correction_reason = correction_reason
                reviewer_correction_request_sha256 = correction_request_sha256
                reviewer_correction_attempts = 0
                return CommanderTurnResult(
                    exit_code=result.exit_code,
                    provider_session_id=provider_session_id,
                    continue_ready=True, outcome=result.outcome,
                    continue_reviewer=True)
            if final_stage2_approval_landed:
                # 只有第四席只读终审的 confirm 真正收口 s2 后才封账；review-decisions
                # / apply-edit 以 exit 0 结束只说明 blocking 面安全，终审席仍待发放。
                # record 必须跨过 mark_complete 失败窗：下个 parent 即使 planner
                # 已越过旧 blocker，也能凭 current approval/seen 幂等补账；封账成功
                # 后才退休 record，删除失败则由同一恢复口下次清理。
                _complete_stage2_review_ledger(source_task)
                if record_path is not None:
                    _discard_reviewer_action_record(base, record_path)
            return CommanderTurnResult(
                exit_code=result.exit_code,
                provider_session_id=provider_session_id,
                continue_ready=result.continue_ready,
                outcome=result.outcome)

        if binding in reviewed_blockers:
            return None
        scratch = service_lifecycle.open_scratch_session(
            base, sid, epoch.epoch_id)
        request_path = scratch / (
            f'reviewer-request-{current.action.action_sha256[:16]}.json')
        try:
            task = reviewer_tasks.build_task(
                base, sid, current, request_path, epoch_id=epoch.epoch_id)
        except reviewer_tasks.ReviewerTaskStopped as exc:
            # workpack producer 已给出结构化 stopped（例如源文不可得/人工
            # partition 尚无证明）；这发生在 submission options、席位预留和
            # CLI invocation 之前，绝不能再花一轮 Reviewer 来猜。
            reviewed_blockers.add(binding)
            latest_rc = 2
            latest_error_kind = 'reviewer_workpack_stopped'
            latest_outcome = AgentDeliveryOutcome(
                state='stopped', checked_at=int(time.time()),
                reason=(
                    f'{exc.waiting_on} 的只读 reviewer workpack 已明确停止：'
                    f'{exc.reason}'))
            return CommanderTurnResult(
                exit_code=2, provider_session_id=None,
                continue_ready=False, outcome=latest_outcome)
        if task is None:
            return None
        if waiting_on in queue.STAGE2_REVIEWER_WAITING_ON:
            pending_stage2 = _pending_stage2_rejection(task)
            if pending_stage2 is not None:
                # rejection 是比 durable action 更新的业务事实。先严格退休同一
                # 六元身份下可能残留的已拒 action，再允许启动 correction；这闭合
                # “拒因落账成功 → record 删除失败/进程崩溃”的跨 parent 窗。
                try:
                    residual_action = _find_replayable_reviewer_action(
                        base, sid, epoch=epoch, current=current, task=task)
                except Exception as exc:  # noqa: BLE001 —— 退休/校账失败即 unknown
                    reviewed_blockers.add(binding)
                    latest_rc = CRASH
                    latest_error_kind = 'stage2_rejected_action_retirement'
                    latest_outcome = AgentDeliveryOutcome(
                        state='unknown', checked_at=int(time.time()),
                        reason=(
                            'Stage2 已有 durable rejection，但对应 action record '
                            '未能确定性退休；禁止执行旧回答或启动新 correction。'
                            f'failure={type(exc).__name__}'))
                    return CommanderTurnResult(
                        exit_code=CRASH, provider_session_id=None,
                        continue_ready=False, outcome=latest_outcome)
                if residual_action is not None:
                    reviewed_blockers.add(binding)
                    latest_rc = CRASH
                    latest_error_kind = 'stage2_rejection_action_conflict'
                    latest_outcome = AgentDeliveryOutcome(
                        state='unknown', checked_at=int(time.time()),
                        reason=(
                            'Stage2 pending rejection 与一份仍可执行的 durable action '
                            '同时存在；无法证明哪个事实更新，已关闭执行与新模型 wire。'))
                    return CommanderTurnResult(
                        exit_code=CRASH, provider_session_id=None,
                        continue_ready=False, outcome=latest_outcome)
                return resume_stage2_pending_rejection(task, pending_stage2)
        if waiting_on == queue.WAITING_ON_STAGE2_REVIEW:
            # full action record 是 prereview 成功 live bind 后的更晚状态。若宿主
            # 崩在“保存 action → 删除 prereview”之间，优先重放 full action，并
            # 幂等清掉被它取代的未绑定记录，不能再组装第二份 action。
            action_replay = _find_replayable_reviewer_action(
                base, sid, epoch=epoch, current=current, task=task)
            if action_replay is not None:
                for obsolete_path, _obsolete in uploads.load_stage2_prereviews(
                        base, sid):
                    uploads.discard_edit_plan(base, obsolete_path)
                log = run_log_path(base, run_id)
                log.parent.mkdir(parents=True, exist_ok=True)
                with open(log, 'ab') as fh:
                    fh.write(
                        f'$ [reviewer-replay] '
                        f'{action_replay.path}\n'.encode('utf-8'))
                reviewed_blockers.add(binding)
                pending_reviewer_action = action_replay.action
                pending_reviewer_binding = binding
                pending_reviewer_business_sha256 = task.business_sha256
                pending_reviewer_source_task = task
                pending_reviewer_response_sha256 = (
                    action_replay.record.provider_response_sha256)
                pending_reviewer_stage2_session_run_id = (
                    action_replay.record.reviewer_session_run_id)
                pending_reviewer_record_path = action_replay.path
                pending_reviewer_replayed = True
                stage2_prereview = None
                return CommanderTurnResult(
                    exit_code=0, provider_session_id=None,
                    continue_ready=True, outcome=latest_outcome)
            # `_find_replayable_reviewer_action` 可能刚用今天更严的 typed
            # loader 拒绝一份旧 action，并已把拒因绑回原席后退休 record。
            # 该拒因是在本轮最初 pending 检查之后才出现的，须立即转入
            # fresh correction，不能继续买一席伪装成首次 review。
            replay_rejection = _pending_stage2_rejection(task)
            if replay_rejection is not None:
                return resume_stage2_pending_rejection(
                    task, replay_rejection)
            try:
                durable_prereview = _find_replayable_stage2_prereview(
                    base, sid, task=task)
            except (NovelError, reviewer_tasks.ReviewerTaskError) as exc:
                reviewed_blockers.add(binding)
                latest_rc = CRASH
                latest_error_kind = 'stage2_prereview_recovery'
                latest_outcome = AgentDeliveryOutcome(
                    state='unknown', checked_at=int(time.time()),
                    reason=(
                        'Stage2 持久预审无法唯一、完整地恢复；已在任何新 '
                        f'Commander wire 前关闭：{exc}'))
                return CommanderTurnResult(
                    exit_code=CRASH, provider_session_id=None,
                    continue_ready=False, outcome=latest_outcome)
            if durable_prereview is not None:
                stage2_prereview = None
                record = durable_prereview.record
                try:
                    prefetched_action = reviewer_tasks.bind_stage2_prereview(
                        task, current, record.input_payload)
                except reviewer_tasks.ReviewerTaskError as exc:
                    # live loader 的显式拒绝先绑定回产出这份回答的原席；只有
                    # rejection durable 后才消费未绑定记录、进入 fresh correction。
                    _record_stage2_rejection(
                        task,
                        session_run_id=record.reviewer_session_run_id,
                        kind='typed_loader',
                        request_sha256=record.provider_response_sha256,
                        reason=str(exc))
                    uploads.discard_edit_plan(base, durable_prereview.path)
                    pending = _pending_stage2_rejection(task)
                    if pending is not None:
                        stage2_commander_turns = max(
                            stage2_commander_turns, pending.turn.ordinal)
                    latest_rc = 2
                    latest_error_kind = 'stage2_prereview_binding'
                    latest_outcome = AgentDeliveryOutcome(
                        state='stopped', checked_at=int(time.time()),
                        reason=f'Stage2 Global 持久预审未通过 live 重绑定：{exc}')
                    if stage2_commander_turns >= _MAX_STAGE2_EDIT_SEAT:
                        reviewed_blockers.add(binding)
                        latest_outcome = AgentDeliveryOutcome(
                            state='stopped', checked_at=int(time.time()),
                            reason=(
                                'Stage2 Global 持久预审无法绑定 live workpack，且前 '
                                f'{_MAX_STAGE2_EDIT_SEAT} 席审阅/订正预算已用尽；'
                                f'第 {_MAX_STAGE2_COMMANDER_TURNS} 席只保留给'
                                f'只读 final_review。原 typed loader 拒因：{exc}'))
                        return CommanderTurnResult(
                            exit_code=2, provider_session_id=None,
                            continue_ready=False, outcome=latest_outcome)
                    if not task.request_path.is_file():
                        reviewed_blockers.add(binding)
                        latest_outcome = AgentDeliveryOutcome(
                            state='stopped', checked_at=int(time.time()),
                            reason=(
                                'Stage2 Global 持久预审无法绑定 live workpack，'
                                f'且没有形成可纠正的 typed 请求：{exc}'))
                        return CommanderTurnResult(
                            exit_code=2, provider_session_id=None,
                            continue_ready=False, outcome=latest_outcome)
                    reviewer_correction_task = task
                    reviewer_correction_binding = binding
                    reviewer_correction_reason = str(exc)
                    reviewer_correction_request_sha256 = (
                        record.provider_response_sha256)
                    reviewer_correction_attempts = 0
                    return CommanderTurnResult(
                        exit_code=2, provider_session_id=None,
                        continue_ready=True, outcome=latest_outcome,
                        continue_reviewer=True)

                # 这一步才从“无写权预审”变成可执行 full action。必须先保存后者，
                # 再删前者；任一步异常都以 unknown 退出且保留至少一份恢复凭据。
                action_record_path = _record_reviewer_action(
                    base, sid, run_id=run_id, epoch=epoch, task=task,
                    action=prefetched_action,
                    reviewer_session_run_id=record.reviewer_session_run_id,
                    provider_response_sha256=record.provider_response_sha256)
                if action_record_path is None:  # pragma: no cover —— node 恒有记录
                    raise RuntimeError('Stage2 预审重绑定没有形成 durable action record')
                uploads.discard_edit_plan(base, durable_prereview.path)
                log = run_log_path(base, run_id)
                log.parent.mkdir(parents=True, exist_ok=True)
                with open(log, 'ab') as fh:
                    fh.write(
                        f'$ [stage2-prereview-replay] '
                        f'{durable_prereview.path}\n'.encode('utf-8'))
                reviewed_blockers.add(binding)
                pending_reviewer_action = prefetched_action
                pending_reviewer_binding = binding
                pending_reviewer_business_sha256 = task.business_sha256
                pending_reviewer_source_task = task
                pending_reviewer_response_sha256 = (
                    record.provider_response_sha256)
                pending_reviewer_stage2_session_run_id = (
                    record.reviewer_session_run_id)
                pending_reviewer_record_path = action_record_path
                pending_reviewer_replayed = True
                return CommanderTurnResult(
                    exit_code=0, provider_session_id=None,
                    continue_ready=True, outcome=latest_outcome)

            # typed-invalid/Commander failure 没有 durable payload；同 parent 只保留
            # 纠错坐标。完整身份一旦漂移就丢弃，不得把旧错误嫁接到新 workpack。
            if stage2_prereview is not None:
                prereview = stage2_prereview
                stage2_prereview = None
                if (_stage2_prereview_identity(prereview.task)
                        == _stage2_prereview_identity(task)
                        and prereview.error is not None):
                    reviewer_correction_task = task
                    reviewer_correction_binding = binding
                    reviewer_correction_reason = prereview.error
                    reviewer_correction_request_sha256 = (
                        prereview.response_sha256
                        or _reviewer_request_sha256(task.request_path))
                    reviewer_correction_attempts = 0
                    return CommanderTurnResult(
                        exit_code=2, provider_session_id=None,
                        continue_ready=True, outcome=latest_outcome,
                        continue_reviewer=True)
        replay = (
            None
            if waiting_on == queue.WAITING_ON_STAGE2_REVIEW
            else _find_replayable_reviewer_action(
                base, sid, epoch=epoch, current=current, task=task))
        if replay is not None:
            log = run_log_path(base, run_id)
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, 'ab') as fh:
                fh.write(
                    f'$ [reviewer-replay] {replay.path}\n'.encode('utf-8'))
            reviewed_blockers.add(binding)
            pending_reviewer_action = replay.action
            pending_reviewer_binding = binding
            pending_reviewer_business_sha256 = task.business_sha256
            pending_reviewer_source_task = task
            pending_reviewer_response_sha256 = (
                replay.record.provider_response_sha256)
            # Stage2 durable record 把 full action 与原始 compact 回答、
            # 产出席位分开保存；重放后 canonical exit 2 必须精确
            # 回写原席，不得用 assembled request digest 污染拒因。
            pending_reviewer_stage2_session_run_id = (
                replay.record.reviewer_session_run_id)
            pending_reviewer_record_path = replay.path
            pending_reviewer_replayed = True
            return CommanderTurnResult(
                exit_code=0, provider_session_id=provider_session_id,
                continue_ready=True, outcome=latest_outcome)
        if waiting_on in queue.STAGE2_REVIEWER_WAITING_ON:
            replay_rejection = _pending_stage2_rejection(task)
            if replay_rejection is not None:
                return resume_stage2_pending_rejection(
                    task, replay_rejection)
        if waiting_on not in queue.STAGE2_REVIEWER_WAITING_ON:
            try:
                prior_seat = reviewer_seat_ledger.read(
                    base, sid, waiting_on=task.waiting_on,
                    business_sha256=task.business_sha256)
            except reviewer_seat_ledger.ReviewerSeatRefused as exc:
                reviewed_blockers.add(binding)
                latest_rc = CRASH
                latest_error_kind = 'reviewer_seat_ledger'
                latest_outcome = AgentDeliveryOutcome(
                    state='failed', checked_at=int(time.time()),
                    reason=(
                        '既有 reviewer 席位账无法证明，已在任何新模型 wire 前'
                        f'关闭：{exc}'))
                return CommanderTurnResult(
                    exit_code=CRASH, provider_session_id=None,
                    continue_ready=False, outcome=latest_outcome)
            if prior_seat is not None and prior_seat.turns:
                reviewed_blockers.add(binding)
                pending = prior_seat.pending_correction
                if pending is not None:
                    reason = (
                        '已恢复上一次付费 reviewer 席位留下的待纠错事实；'
                        '当前 parent 不得把同一业务主体重新包装成第二次首审。'
                        f'原拒因：{pending.reason}；'
                        f'请求 identity={pending.request_sha256}。'
                        '请用人工 typed 入口闭合该 blocker，或让业务 surface 发生'
                        '真实变化后再启动。')
                    error_kind = 'reviewer_correction_pending'
                else:
                    reason = (
                        '同一业务主体的 reviewer 首审已经发生，但盘上没有可重放'
                        ' approval 决定或完整 pending-correction 身份；重启/换 epoch '
                        '不返还首审席位，已在模型 wire 前关闭。请用人工 typed 入口'
                        '闭合 blocker，或先形成新的业务 surface。')
                    error_kind = 'reviewer_seat_already_used'
                latest_rc = 2
                latest_error_kind = error_kind
                latest_outcome = AgentDeliveryOutcome(
                    state='stopped', checked_at=int(time.time()), reason=reason)
                return CommanderTurnResult(
                    exit_code=2, provider_session_id=None,
                    continue_ready=False, outcome=latest_outcome)
        reviewed_blockers.add(binding)
        seat_purpose = 'review'
        if waiting_on in queue.STAGE2_REVIEWER_WAITING_ON:
            seat_purpose = review_seats.seat_for(
                waiting_on,
                review_seats.option_ids(task.submission_options)).purpose
        prepared_stage2: dict[str, object] = {}
        turn = run_reviewer_turn(
            task, task.prompt, purpose=seat_purpose,
            activity=_reviewer_activity(task),
            before_stage2_finish=(
                lambda got, session_id: prepare_stage2_action_before_finish(
                    task, current, got, session_id, prepared_stage2)
                if waiting_on in queue.STAGE2_REVIEWER_WAITING_ON else None))
        if turn.exit_code != 0:
            return turn
        try:
            validated_record_path: Path | None = None
            if waiting_on in queue.STAGE2_REVIEWER_WAITING_ON:
                prepared_error = prepared_stage2.get('error')
                if isinstance(prepared_error, BaseException):
                    raise prepared_error
                candidate_action = prepared_stage2.get('action')
                candidate_digest = prepared_stage2.get('response_sha256')
                candidate_record = prepared_stage2.get('record_path')
                if (not isinstance(candidate_action, (
                        reviewer_tasks.ReviewerNodeAction,
                        reviewer_tasks.ReviewerApprovalAction,
                        reviewer_tasks.ReviewerStopAction))
                        or not isinstance(candidate_digest, str)
                        or not isinstance(candidate_record, Path)):
                    raise RuntimeError(
                        'Stage2 reviewer finish 前没有完整 durable action')
                pending_reviewer_action = candidate_action
                response_sha256 = candidate_digest
                validated_record_path = candidate_record
            elif turn.final_response is not None:
                (pending_reviewer_action, response_sha256,
                 validated_record_path) = (
                    _materialize_reviewer_response(
                        task, current, turn.final_response,
                        persist_validated=lambda action, response_sha256: (
                            _record_reviewer_action(
                                base, sid, run_id=run_id, epoch=epoch,
                                task=task, action=action,
                                reviewer_session_run_id=(
                                    stage2_turn_session_run_id
                                    if waiting_on
                                    in queue.STAGE2_REVIEWER_WAITING_ON
                                    else None),
                                provider_response_sha256=(
                                    response_sha256
                                    if waiting_on
                                    in queue.STAGE2_REVIEWER_WAITING_ON
                                    else None))),
                        preserve_validated=lambda candidate, digest: (
                            _preserve_validated_reviewer_residue(
                                base, sid, candidate, digest))))
            else:
                pending_reviewer_action = reviewer_tasks.load_action(
                    request_path, current,
                    submission_options=task.submission_options)
                response_sha256 = _reviewer_request_sha256(request_path)
                validated_record_path = _record_reviewer_action(
                    base, sid, run_id=run_id, epoch=epoch, task=task,
                    action=pending_reviewer_action,
                    reviewer_session_run_id=(
                        stage2_turn_session_run_id
                        if waiting_on in queue.STAGE2_REVIEWER_WAITING_ON
                        else None),
                    provider_response_sha256=(
                        response_sha256
                        if waiting_on in queue.STAGE2_REVIEWER_WAITING_ON
                        else None))
        except reviewer_tasks.ReviewerTaskError as exc:
            seat_open = open_reviewer_seats.get(binding)
            if (waiting_on not in queue.STAGE2_REVIEWER_WAITING_ON
                    and (seat_open is None
                         or seat_open.provider_session_id is None)):
                _close_reviewer_seat(
                    binding, error_kind='commander_reviewer_request')
                latest_rc = 2
                latest_error_kind = 'commander_reviewer_request'
                latest_outcome = AgentDeliveryOutcome(
                    state='stopped', checked_at=int(time.time()),
                    reason=f'Commander reviewer typed request 未闭合：{exc}')
                return CommanderTurnResult(
                    exit_code=2,
                    provider_session_id=None,
                    continue_ready=False, outcome=latest_outcome)
            failed_request_sha256 = _reviewer_turn_response_sha256(
                turn, task.request_path)
            if waiting_on in queue.STAGE2_REVIEWER_WAITING_ON:
                _record_stage2_rejection(
                    task,
                    session_run_id=stage2_turn_session_run_id,
                    kind='typed_loader',
                    request_sha256=failed_request_sha256,
                    reason=str(exc))
            persist_pending_correction(
                task, reason=str(exc),
                request_sha256=failed_request_sha256)
            if (waiting_on in queue.STAGE2_REVIEWER_WAITING_ON
                    and stage2_commander_turns >= _MAX_STAGE2_EDIT_SEAT):
                reviewed_blockers.add(binding)
                latest_rc = 2
                latest_error_kind = 'commander_reviewer_request'
                latest_outcome = AgentDeliveryOutcome(
                    state='stopped', checked_at=int(time.time()),
                    reason=(
                        'Commander reviewer typed request 未闭合，且 Stage2 '
                        f'global 前 {_MAX_STAGE2_EDIT_SEAT} 席审阅/订正预算已用尽；'
                        f'第 {_MAX_STAGE2_COMMANDER_TURNS} 席只保留给只读 '
                        f'final_review。原 typed loader 拒因：{exc}'))
                return CommanderTurnResult(
                    exit_code=2,
                    provider_session_id=turn.provider_session_id,
                    continue_ready=False, outcome=latest_outcome)
            reviewer_correction_task = task
            reviewer_correction_binding = binding
            reviewer_correction_reason = str(exc)
            reviewer_correction_request_sha256 = failed_request_sha256
            reviewer_correction_attempts = 0
            return CommanderTurnResult(
                exit_code=turn.exit_code,
                provider_session_id=turn.provider_session_id,
                continue_ready=True, outcome=turn.outcome,
                continue_reviewer=True)
        pending_reviewer_binding = binding
        pending_reviewer_business_sha256 = task.business_sha256
        pending_reviewer_source_task = task
        pending_reviewer_response_sha256 = response_sha256
        pending_reviewer_stage2_session_run_id = (
            stage2_turn_session_run_id
            if waiting_on in queue.STAGE2_REVIEWER_WAITING_ON else None)
        pending_reviewer_record_path = validated_record_path
        pending_reviewer_replayed = False
        return CommanderTurnResult(
            exit_code=turn.exit_code,
            provider_session_id=turn.provider_session_id,
            continue_ready=True, outcome=turn.outcome)

    def _finalize_run(
            epoch: ExecutionEpoch,
            launch_ticket: LaunchTicket) -> SupervisorActionResult:
        script = (
            REPO_ROOT / '.workflow' / 'skills' / 'novel-pipeline-operator'
            / 'scripts' / 'finalize_review.py')
        argv = [
            sys.executable, '-B', str(script), '--base', base, '--sid', sid,
            '--started-at', str(parent_started_at),
            '--start-code-version', epoch.code_version,
        ]
        rc, timed_out, failure_detail = _run_supervisor_process(
            epoch, argv, launch_ticket, label='finalize-review')
        return _action_outcome(
            epoch, rc, timed_out, label='finalize-review',
            failure_detail=failure_detail)

    deterministic_callbacks = ({
        'read_operator_next': _read_operator_next,
        'run_ready_action': _run_ready_action,
        'run_blocked_action': _run_blocked_action,
        'deterministic_blockers': queue.DETERMINISTIC_RECOVERY_WAITING_ON,
        'fresh_epoch_blockers': frozenset({
            queue.WAITING_ON_STAGE4_DESIGN_ACTION,
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
        nonlocal latest_outcome, latest_rc, latest_error_kind
        try:
            supervised = supervisor.run()
            # Supervisor 的最后 typed 事实是 parent ended 的权威输入。
            # 未登记 blocker 等分支不经过 action callback；若不在这里投影，
            # epoch_closed=2/stopped 会与 ended=70/unknown 串账。
            if supervised.turn is not None:
                latest_rc = supervised.turn.exit_code
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
                    # code/skill rollover 已在 epoch_close 把旧 logical session
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
                    outcome=latest_outcome))
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
                ts=int(time.time()), exit_code=CRASH, outcome=failed))
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
