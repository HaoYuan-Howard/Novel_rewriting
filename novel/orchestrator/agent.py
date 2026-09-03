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
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator, NamedTuple

from contracts.artifacts import STAGE2_GLOBAL_EDIT, STAGE2_GLOBAL_REVIEW_DECISIONS
from contracts.invariants import AGENT_EXECUTOR_NODES
from contracts.schema import decode_artifact
from contracts.schema.api import ExecutionPermit, OperatorNext
from contracts.schema.evaluation import GlobalReviewDecisionSet
from contracts.schema.stage2 import GlobalEdit
from contracts.schema.orchestration import (
    AgentDeliveryOutcome, AgentPending, AgentQueueView, AgentRejected, AgentRunEvent,
    AgentExecutorBackend, AgentProfile, AgentRunRecord, AgentRunStart,
    ExecutionEpoch, ExecutorBackend,
    PipelineAgentProfiles, RunContractV2,
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
)
from novel.commanders import (
    CommanderConfigurationError, CommanderDriver, CommanderInvocation,
    CommanderObservation, CommanderProcessResult, create_commander_driver,
)
from novel.agents.base import _signal_process_tree
from novel.errors import (
    ApprovalRequired, NovelError, TerminalState, UnsupportedRunMode,
    UpstreamMissing,
)
from novel.llm.ledger import append_commander_call
from novel.orchestrator import (
    admission, commander_completion, jobs, operator, queue, reviewer_tasks,
    service_lifecycle, uploads,
)
from novel.orchestrator.agent_supervisor import (
    CommanderTurnResult, LaunchTicket, RunSupervisor, SupervisorActionResult,
)
from novel.orchestrator.jobs import JobConflict, JobRegistry, service_dir
from novel.orchestrator.nodes import shell_line

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
_MAX_REVIEWER_CORRECTIONS = 2
_MAX_STAGE2_COMMANDER_TURNS = 2
_ACTION_FAILURE_TAIL_BYTES = 16 * 1024
_ACTION_FAILURE_DETAIL_CHARS = 4096

_THREADS_LOCK = threading.Lock()
_THREADS: dict[str, threading.Thread] = {}
_COMMANDERS_LOCK = threading.Lock()
_COMMANDERS: dict[str, CommanderDriver] = {}
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
不得从目录存在性、旧对话或退出码猜测 PASS；证据不足时保留 UNVER 或拒绝。

本次 parent contract 的审计起点：code_version=`{code_version}`；成书目标=`{book_target}`；
终审包目标=`{review_target}`。这些目标仅供识别对象，不授予写权限。

付费授权：{payment_authorization}
正文外发授权：{egress_authorization}

冻结角色：
- Commander：{commander.driver}/{commander.model}/{commander.reasoning_effort}/{commander.execution_mode}
- 默认 Executor：{_backend_label(executor)}
- Executor 节点覆盖：{overrides}

审阅规范以 `{canonical_skill}` 及当前 workpack 明示的 review contract 为准；若两者与当前
typed schema 冲突，关闭式拒绝并让本轮结束，不自行发明兼容写法。"""


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
        profile: AgentProfile) -> None:
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
            != os.path.normcase(os.path.abspath(REPO_ROOT))):
        raise UnsupportedRunMode('Commander cwd 必须冻结在仓根')


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
                     'landed_red' if slot['exit_code'] == 1 else 'failed')
            if state == 'landed_red':
                slot['note'] = slot['note'] or jobs.LANDED_RED_NOTE
            if slot['outcome'] is None:
                slot['outcome'] = AgentDeliveryOutcome(
                    state='unknown', checked_at=slot['ended'],
                    reason='历史 ended 事件没有交付终验载荷，不能从进程退出码补推可交付')
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


def reconcile_run(
        base: str | os.PathLike[str], run_id: str, *, actor: str,
        reason: str, subject_sha256: str, force: bool = False,
) -> AgentRunRecord:
    """复用 admission 的 owner/CAS 收口，并为同一 v2 AgentRun 落真实事件。

    ``reconciled`` 不是第二套恢复判定：admission seal 才是权威。这里仅在 seal
    当前仍绑定同一 subject 时追加生命周期投影；若进程恰死在两账之间，重试会
    识别已提交 seal 并幂等补上 AgentRun 事件。
    """
    checked = run_identity.check_run_id(run_id)
    rec = next((item for item in records(base) if item.run_id == checked), None)
    if rec is None:
        raise UpstreamMissing(f'没有 run_id={checked!r} 的 agent 代跑记录')
    if rec.contract is None:
        raise JobConflict('只有 wire v2 AgentRun 可与 Commander admission 联合 reconcile')
    if not actor.strip() or not reason.strip():
        raise TerminalState('AgentRun reconcile 的 actor/reason 不得为空')
    note = (
        f'{actor.strip()}：{reason.strip()} '
        f'[admission_subject={subject_sha256}]')
    try:
        admission.reconcile_gate(
            base, checked, actor=actor, reason=reason,
            subject_sha256=subject_sha256, force=force)
    except TerminalState:
        current = admission.snapshot(base, checked)
        if (current.terminal_kind != 'reconciled'
                or current.reconcile_invalidated
                or current.terminal_subject_sha256 != subject_sha256):
            raise

    # retry 补尾也必须重新证明 owner 已停；force 从不越过这枚 OS lease。
    with admission.require_owner_stopped(base, checked):
        current = admission.snapshot(base, checked)
        if (current.terminal_kind != 'reconciled'
                or current.reconcile_invalidated
                or current.terminal_subject_sha256 != subject_sha256):
            raise TerminalState('admission reconcile seal 已变化，拒绝补 AgentRun 事件')
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
        )
        driver = create_commander_driver(runtime_invocation.profile)
        with _COMMANDERS_LOCK:
            _COMMANDERS[run_id] = driver
        if on_registered is not None:
            setter = getattr(driver, 'set_launch_callback', None)
            if setter is None:
                raise CommanderConfigurationError(
                    'Commander driver 不支持 Popen 后 launch ticket')
            setter(on_registered)
        # owner 故意不在顶层 invocation 返回时移除：worker 接下来还要 drain
        # close 前 claim / beacon。300 秒阈值与人工 stop 都应尽可能仍能向这棵
        # driver 进程树发 cancel。真正 close 在 durable drain 闭合之后统一完成。
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
    return detail[-_ACTION_FAILURE_DETAIL_CHARS:]


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
    if action.surface == 'blueprint_seen':
        tail = [
            'approval', 'close-stage2', sid, '--base', base,
            '--input', str(payload_path)]
    else:
        tail = [
            'approval', 'apply', action.surface, sid, '--base', base,
            '--input', str(payload_path)]
    return [sys.executable, '-B', '-m', 'novel.cli', *tail]


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
    """付费启动前必须有可复验的代码身份；preview 可以照实展示 unknown。

    ``unknown == unknown`` 只证明两次都没问出来，不证明整书跑没有
    跨版本。放它进付费路，结束终验也注定只能 unknown，是可预知的白烧。
    """
    if (not isinstance(code_version, str) or not code_version.strip()
            or code_version == prov.CODE_UNKNOWN):
        raise UnsupportedRunMode(
            '当前代码指纹不可用；preview 仍可查看，但不能启动付费 '
            'Commander，请先恢复可复验的仓库代码身份')


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
    """当前这个 base 上：谁在跑、谁在排队、闸开多大。"""
    base_abs = os.path.abspath(os.fspath(base))
    running = [r for r in records(base, registry=registry) if r.state == 'running']
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
        return f'unreadable:{type(exc).__name__}'


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
        with open(run_log_path(base, run_id), 'ab') as fh:
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
    reviewer_correction_task: reviewer_tasks.ReviewerTask | None = None
    reviewer_correction_binding: tuple[str, str] | None = None
    reviewer_correction_reason: str | None = None
    reviewer_correction_request_sha256: str | None = None
    reviewer_correction_attempts = 0
    stage2_commander_turns = 0
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
            provider_session_id: str | None) -> CommanderInvocation:
        """为一份 frozen reviewer task 构造新 turn 或 exact resume。"""
        driver: CommanderDriver | None = None
        try:
            driver = create_commander_driver(plan.profiles.commander)
            reviewed = driver.build_invocation(
                plan.profiles.commander, prompt,
                repo=REPO_ROOT, base=base,
                cli_version=epoch.cli_version,
                resume_provider_session_id=provider_session_id)
            _validate_invocation(
                reviewed, prompt=prompt, profile=plan.profiles.commander)
            if reviewed.resume_provider_session_id != provider_session_id:
                raise UnsupportedRunMode(
                    'Commander reviewer invocation 没有精确冻结 provider session id')
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
        nonlocal reviewer_correction_task, reviewer_correction_binding
        nonlocal reviewer_correction_reason, reviewer_correction_request_sha256
        nonlocal reviewer_correction_attempts, stage2_commander_turns
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
            reviewer_correction_task = None
            reviewer_correction_binding = None
            reviewer_correction_reason = None
            reviewer_correction_request_sha256 = None
            reviewer_correction_attempts = 0
            stage2_commander_turns = 0
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
            admission.open_gate(base, admission_id, sid)
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
            label: str) -> tuple[int, bool, str | None]:
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
            launch_ticket: LaunchTicket | None = None,
            ) -> CommanderTurnResult:
        nonlocal turn_index, latest_observation, latest_outcome
        nonlocal latest_rc, latest_error_kind, provider_turn_recorded
        nonlocal latest_commander_outcome, latest_commander_rc
        nonlocal latest_commander_error_kind
        nonlocal active_admission_id

        if reviewer_prompt is not None:
            turn_invocation = _review_invocation(
                epoch, reviewer_prompt, provider_session_id)
        elif provider_session_id is None:
            turn_invocation = epoch_invocations[epoch.epoch_id]
        else:
            turn_invocation = _resume_invocation(
                epoch, provider_session_id)
        admission_id = _open_action_admission(epoch)
        attempt_id = jobs.new_job_id()
        rc = CRASH
        observation = CommanderObservation(
            provider_session_id=None, actual_model=None,
            actual_effort=None, usage=None)
        runtime_error_kind: str | None = None
        integrity_error_kind: str | None = None
        lifecycle_error_kind: str | None = None
        completion_error_kind: str | None = None
        invoked = False
        drain = _ExecutionDrainResult(timed_out=False)
        try:
            log = run_log_path(base, run_id)
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, 'ab') as fh:
                fh.write(('$ ' + shell_line(turn_invocation.argv) + '\n').encode('utf-8'))
                fh.flush()
                child_env = env_with_context(
                    run_id=run_id, base=base, start=start_payload,
                    contract=contract_payload, epoch=epoch,
                    admission_id=admission_id, env=os.environ)
                invoked = True
                if spawn is None:
                    def reviewer_started() -> None:
                        if reviewer_prompt is not None:
                            _open_epoch(
                                epoch,
                                actual_prompt_sha256=_sha256_bytes(
                                    turn_invocation.stdin_text.encode('utf-8')),
                            )
                        if launch_ticket is not None:
                            launch_ticket.commit()

                    commander_spawn = _default_spawn(
                        run_id,
                        on_registered=(
                            reviewer_started
                            if (launch_ticket is not None
                                or reviewer_prompt is not None) else None))
                else:
                    if launch_ticket is not None:
                        # custom spawn 是测试/legacy 注入缝；生产 v2 只走上面的
                        # driver owner 登记后 commit 路。
                        launch_ticket.commit()
                    commander_spawn = spawn
                result = commander_spawn(turn_invocation, child_env, fh)
                if not isinstance(result, CommanderProcessResult):
                    raise TypeError(
                        'Commander spawn 必须返回 CommanderProcessResult，'
                        '不能只给退出码丢掉供应商观测')
                if not isinstance(result.observation, CommanderObservation):
                    raise TypeError(
                        'CommanderProcessResult.observation 必须是 '
                        'CommanderObservation，不能传自由 dict')
                rc = result.exit_code
                observation = result.observation
        except BaseException as exc:  # noqa: BLE001 —— 崩了也要闭合本 turn
            known_rc = getattr(exc, 'returncode', None)
            if isinstance(known_rc, int) and not isinstance(known_rc, bool):
                rc = known_rc
            runtime_error_kind = 'commander_runtime'
            _append_safe_traceback(base, run_id, prompt=turn_invocation.stdin_text)
        finally:
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

        if drain.timed_out:
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
        if (lifecycle_error_kind is None
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
            or (None if rc == 0 else 'commander_process_exit'))

        if invoked:
            try:
                append_commander_call(
                    base, run_id=run_id,
                    session_run_id=epoch.session_run_id,
                    sid=sid, attempt_id=attempt_id,
                    request_model=plan.profiles.commander.model,
                    requested_effort=(
                        plan.profiles.commander.reasoning_effort),
                    code=epoch.code_version,
                    protocol_version=turn_invocation.protocol,
                    actual_model=observation.actual_model,
                    actual_effort=observation.actual_effort,
                    provider_session_id=(
                        observation.provider_session_id),
                    cli_version=turn_invocation.cli_version,
                    usage=_usage_dict(observation),
                    outcome=('ok' if rc == 0 else 'error'),
                    exit_code=rc, error_kind=error_kind)
            except Exception:  # noqa: BLE001
                integrity_error_kind = 'commander_ledger'
                error_kind = integrity_error_kind
                _append_safe_traceback(
                    base, run_id, prompt=turn_invocation.stdin_text)

        provider_reuse_owner: str | None = None
        this_turn_recorded = False
        if observation.provider_session_id is not None:
            turn_index += 1
            try:
                provider_reuse_owner = (
                    agent_session_audit.append_commander_turn(
                        base,
                        agent_session_audit.AgentSessionEvent(
                            session_run_id=epoch.session_run_id,
                            parent_run_id=run_id,
                            role='commander', node=None, chapter=None,
                            subject_sha256=current_session_prompt_sha256,
                            profile=plan.profiles.commander,
                            provider_session_id=(
                                observation.provider_session_id),
                            turn_index=turn_index,
                            attempt_id=attempt_id,
                            requested_model=(
                                plan.profiles.commander.model),
                            actual_model=observation.actual_model,
                            requested_effort=(
                                plan.profiles.commander.reasoning_effort),
                            actual_effort=observation.actual_effort,
                            cli_version=turn_invocation.cli_version,
                            protocol_version=turn_invocation.protocol,
                            start_code_version=epoch.code_version,
                            skill_sha256=epoch.skill_sha256,
                            prompt_sha256=_sha256_bytes(
                                turn_invocation.stdin_text.encode('utf-8')),
                            schema_sha256=schema_sha256,
                            usage=_session_usage(observation),
                            error_kind=error_kind,
                            kind='turn', ts=int(time.time()),
                        ),
                    )
                )
                this_turn_recorded = provider_reuse_owner is None
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

        latest_observation = observation
        latest_outcome = outcome
        latest_rc = rc
        latest_error_kind = error_kind
        latest_commander_outcome = outcome
        latest_commander_rc = rc
        latest_commander_error_kind = error_kind
        return CommanderTurnResult(
            exit_code=rc,
            provider_session_id=(
                observation.provider_session_id
                if this_turn_recorded else None),
            continue_ready=continue_ready,
            outcome=outcome)

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
        latest_outcome = _assess_delivery(
            base, sid, code_version=epoch.code_version)
        latest_error_kind = None if rc in (0, 1, 2) else f'{label}_process_exit'
        return SupervisorActionResult(
            exit_code=rc, outcome=latest_outcome,
            # 只有 0/1 能证明本次正式动作已经落盘，才允许重新规划下一步。
            # 2 是显式终态、3 是结构拒绝、70 是崩溃；继续循环会在盘面
            # 未变化时重复执行同一个 ready token，付费节点尤其危险。
            continue_ready=landed(rc), failure_detail=failure_detail,
            pause_note=pause_note)

    def _run_token(
            epoch: ExecutionEpoch, permit: ExecutionPermit,
            launch_ticket: LaunchTicket, *, stem: str) -> SupervisorActionResult:
        argv = _run_step_argv(base, sid, epoch, permit, stem=stem)
        rc, timed_out, failure_detail = _run_supervisor_process(
            epoch, argv, launch_ticket, label=stem)
        pause_note = (
            None if timed_out else
            _delivery_pause_note(base, sid, epoch, permit.node, rc))
        return _action_outcome(
            epoch, rc, timed_out, label=stem,
            failure_detail=failure_detail, pause_note=pause_note)

    def _read_operator_next(epoch: ExecutionEpoch) -> OperatorNext:
        # Supervisor 已在本调用前后各核一次 epoch identity；这里复用同一 code，
        # 避免每个节点再为整棵 dirty worktree 启三次 git 子进程。
        return operator.next_action_at_code(
            base, sid, code=epoch.code_version)

    def _run_ready_action(
            epoch: ExecutionEpoch, current: OperatorNext,
            launch_ticket: LaunchTicket) -> SupervisorActionResult:
        if current.execution_token is None:
            raise RuntimeError('ready next 缺 execution token')
        return _run_token(
            epoch, current.execution_token, launch_ticket,
            stem=f'ready-{current.execution_token.node}')

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

    def _run_reviewer(
            epoch: ExecutionEpoch, current: OperatorNext,
            provider_session_id: str | None,
            launch_ticket: LaunchTicket) -> CommanderTurnResult | None:
        nonlocal pending_reviewer_action, pending_reviewer_binding
        nonlocal pending_reviewer_business_sha256, pending_reviewer_source_task
        nonlocal reviewer_correction_task, reviewer_correction_binding
        nonlocal reviewer_correction_reason, reviewer_correction_request_sha256
        nonlocal reviewer_correction_attempts, stage2_commander_turns
        nonlocal latest_outcome, latest_rc, latest_error_kind
        assert current.action is not None
        waiting_on = current.action.waiting_on
        assert waiting_on is not None
        binding = (current.surface_seal, waiting_on)

        def run_reviewer_turn(prompt: str) -> CommanderTurnResult:
            """Stage2 的模型调用预算是产品边界；确定性动作不占轮次。"""
            nonlocal stage2_commander_turns
            nonlocal latest_outcome, latest_rc, latest_error_kind
            if waiting_on in queue.STAGE2_REVIEWER_WAITING_ON:
                if (waiting_on == queue.WAITING_ON_STAGE2_EDIT_CORRECTION
                        and stage2_commander_turns == 0):
                    # 盘上 durable correction 证明第一次 Commander 修改已经执行到
                    # HARD 闸；新进程只能领取剩下的最后一格，不能重置为两轮。
                    stage2_commander_turns = 1
                if stage2_commander_turns >= _MAX_STAGE2_COMMANDER_TURNS:
                    latest_rc = 2
                    latest_error_kind = 'stage2_global_turn_budget'
                    latest_outcome = AgentDeliveryOutcome(
                        state='stopped', checked_at=int(time.time()),
                        reason=(
                            'Stage2 global 已用完 2 次 Commander 调用；'
                            '拒绝启动第三轮。第二轮返工没有闭合时请人工检查 typed 输入，'
                            '而不是继续消耗模型'))
                    return CommanderTurnResult(
                        exit_code=2,
                        provider_session_id=provider_session_id,
                        continue_ready=False,
                        outcome=latest_outcome)
                stage2_commander_turns += 1
            return _run_commander_turn(
                epoch, provider_session_id,
                reviewer_prompt=prompt, launch_ticket=launch_ticket)

        if reviewer_correction_task is not None:
            if reviewer_correction_binding != binding:
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
            turn = run_reviewer_turn(reviewer_tasks.correction_prompt(
                task, reason, attempt=attempt))
            if turn.exit_code != 0 or turn.provider_session_id is None:
                return turn
            request_sha256 = _reviewer_request_sha256(task.request_path)
            if request_sha256 == previous_request_sha256:
                reviewer_correction_task = None
                reviewer_correction_binding = None
                reviewer_correction_reason = None
                reviewer_correction_request_sha256 = None
                reviewer_correction_attempts = 0
                latest_rc = 2
                latest_error_kind = 'commander_reviewer_no_progress'
                latest_outcome = AgentDeliveryOutcome(
                    state='stopped', checked_at=int(time.time()),
                    reason=(
                        'Commander 收到 typed 拒绝反馈后没有改变请求字节；'
                        '已阻止无效纠错循环'))
                return CommanderTurnResult(
                    exit_code=2,
                    provider_session_id=turn.provider_session_id,
                    continue_ready=False, outcome=latest_outcome)
            try:
                corrected_action = reviewer_tasks.load_action(
                    task.request_path, current,
                    submission_options=task.submission_options)
            except reviewer_tasks.ReviewerTaskError as exc:
                if attempt >= _MAX_REVIEWER_CORRECTIONS:
                    reviewer_correction_task = None
                    reviewer_correction_binding = None
                    reviewer_correction_reason = None
                    reviewer_correction_request_sha256 = None
                    reviewer_correction_attempts = 0
                    latest_rc = 2
                    latest_error_kind = 'commander_reviewer_request'
                    latest_outcome = AgentDeliveryOutcome(
                        state='stopped', checked_at=int(time.time()),
                        reason=(
                            'Commander reviewer typed request 连续纠正后仍未闭合：'
                            f'{exc}'))
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

            reviewer_correction_task = None
            reviewer_correction_binding = None
            reviewer_correction_reason = None
            reviewer_correction_request_sha256 = None
            reviewer_correction_attempts = 0
            pending_reviewer_action = corrected_action
            pending_reviewer_binding = binding
            pending_reviewer_business_sha256 = task.business_sha256
            pending_reviewer_source_task = task
            return CommanderTurnResult(
                exit_code=turn.exit_code,
                provider_session_id=turn.provider_session_id,
                continue_ready=True, outcome=turn.outcome,
                continue_reviewer=True)

        if pending_reviewer_action is not None:
            if pending_reviewer_binding != binding:
                pending_reviewer_action = None
                pending_reviewer_binding = None
                pending_reviewer_business_sha256 = None
                pending_reviewer_source_task = None
                return None
            action = pending_reviewer_action
            business_sha256 = pending_reviewer_business_sha256
            source_task = pending_reviewer_source_task
            pending_reviewer_action = None
            pending_reviewer_binding = None
            pending_reviewer_business_sha256 = None
            pending_reviewer_source_task = None
            if business_sha256 is None or source_task is None:
                raise RuntimeError('pending reviewer action 缺业务主体或 workpack')
            action_sha256 = reviewer_tasks.action_business_sha256(action)
            business_action = (business_sha256, action_sha256)
            if (business_action in completed_reviewer_business_actions
                    or business_action in rejected_reviewer_business_actions):
                latest_rc = 2
                latest_error_kind = 'commander_reviewer_no_progress'
                latest_outcome = AgentDeliveryOutcome(
                    state='stopped', checked_at=int(time.time()),
                    reason=(
                        'Commander 在同一 Stage2 业务主体上重复提交了已经执行或拒绝的'
                        '等价 typed 动作；'
                        '仅 review surface/workpack 变化不构成修复进展，已阻止无效循环'))
                return CommanderTurnResult(
                    exit_code=2,
                    provider_session_id=provider_session_id,
                    continue_ready=False,
                    outcome=latest_outcome)
            if isinstance(action, reviewer_tasks.ReviewerNodeAction):
                result = _run_node_review_action(
                    epoch, action, launch_ticket)
            else:
                result = _run_approval_action(
                    epoch, action, launch_ticket)
            if landed(result.exit_code):
                completed_reviewer_business_actions.add(business_action)
            elif result.exit_code == 2 and provider_session_id is not None:
                # Reviewer 的 typed 动作由 Commander 负责修正。显式终态证明没有
                # landed；只要 blocker/surface 未变，就把拒绝事实送回同一 provider
                # session，而不是结束整书 parent。等价动作由上面的业务摘要关闭式拦住。
                rejected_reviewer_business_actions.add(business_action)
                rejection = result.failure_detail or (
                    f'canonical {action.kind} action 返回 exit 2，正式产物未落盘')
                if (waiting_on in queue.STAGE2_REVIEWER_WAITING_ON
                        and stage2_commander_turns
                        >= _MAX_STAGE2_COMMANDER_TURNS):
                    latest_rc = 2
                    latest_error_kind = 'stage2_global_second_action_rejected'
                    latest_outcome = AgentDeliveryOutcome(
                        state='stopped', checked_at=int(time.time()),
                        reason=(
                            'Stage2 global 第二次 Commander 返工仍被确定性边界拒绝；'
                            f'拒绝启动第三轮。{rejection}'))
                    return CommanderTurnResult(
                        exit_code=2,
                        provider_session_id=provider_session_id,
                        continue_ready=False, outcome=latest_outcome)
                reviewer_correction_task = source_task
                reviewer_correction_binding = binding
                reviewer_correction_reason = (
                    f'{rejection}；请按原 workpack 只修正上一份 typed 动作')
                reviewer_correction_request_sha256 = _reviewer_request_sha256(
                    source_task.request_path)
                reviewer_correction_attempts = 0
                return CommanderTurnResult(
                    exit_code=result.exit_code,
                    provider_session_id=provider_session_id,
                    continue_ready=True, outcome=result.outcome,
                    continue_reviewer=True)
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
        task = reviewer_tasks.build_task(
            base, sid, current, request_path, epoch_id=epoch.epoch_id)
        if task is None:
            return None
        reviewed_blockers.add(binding)
        turn = run_reviewer_turn(task.prompt)
        if turn.exit_code != 0:
            return turn
        try:
            pending_reviewer_action = reviewer_tasks.load_action(
                request_path, current,
                submission_options=task.submission_options)
        except reviewer_tasks.ReviewerTaskError as exc:
            if turn.provider_session_id is None:
                latest_rc = 2
                latest_error_kind = 'commander_reviewer_request'
                latest_outcome = AgentDeliveryOutcome(
                    state='stopped', checked_at=int(time.time()),
                    reason=f'Commander reviewer typed request 未闭合：{exc}')
                return CommanderTurnResult(
                    exit_code=2,
                    provider_session_id=None,
                    continue_ready=False, outcome=latest_outcome)
            reviewer_correction_task = task
            reviewer_correction_binding = binding
            reviewer_correction_reason = str(exc)
            reviewer_correction_request_sha256 = _reviewer_request_sha256(
                task.request_path)
            reviewer_correction_attempts = 0
            return CommanderTurnResult(
                exit_code=turn.exit_code,
                provider_session_id=turn.provider_session_id,
                continue_ready=True, outcome=turn.outcome,
                continue_reviewer=True)
        pending_reviewer_binding = binding
        pending_reviewer_business_sha256 = task.business_sha256
        pending_reviewer_source_task = task
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
