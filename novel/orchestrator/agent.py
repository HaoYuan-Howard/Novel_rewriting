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
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, NamedTuple

from contracts.schema.orchestration import (
    AgentDeliveryOutcome, AgentPending, AgentQueueView, AgentRejected, AgentRunEvent,
    AgentProfile, AgentRunRecord, AgentRunStart, PipelineAgentProfiles,
)
from novel import agent_session_audit, paths, prov, run_identity, skill_contract
from novel.agent_capabilities import (
    AgentProfileRefused, validate_pipeline_profiles,
)
from novel.appendlog import append_line
from novel.cli.exit_codes import CRASH
from novel.commander_context import (
    COMMANDER_CONTRACT_SHA256_ENV, COMMANDER_RUN_ID_ENV, env_with_context,
)
from novel.commanders import (
    CommanderConfigurationError, CommanderDriver, CommanderInvocation,
    CommanderObservation, CommanderProcessResult, create_commander_driver,
)
from novel.errors import ApprovalRequired, UnsupportedRunMode, UpstreamMissing
from novel.llm.ledger import append_commander_call
from novel.orchestrator import admission, commander_completion, jobs
from novel.orchestrator.jobs import JobConflict, JobRegistry, service_dir
from novel.orchestrator.nodes import shell_line

RUNS_FILENAME = 'agent_runs.jsonl'
LOG_PREFIX = 'agent-'

#: 仓根：agent.py → orchestrator → novel → 仓根。agent 的 cwd 钉死在这里——
#: `.claude/skills/` 的自动加载、`-m novel.cli` 的 import 都吃这个 cwd。
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

_THREADS_LOCK = threading.Lock()
_THREADS: dict[str, threading.Thread] = {}
_COMMANDERS_LOCK = threading.Lock()
_COMMANDERS: dict[str, CommanderDriver] = {}

#: `R-11.11` 在这边的样子：服务重启后，agent 子进程是死是活我们真的不知道。
RESTART_NOTE = ('服务重启前它是死是活我们不知道——'
                '产物与信标都在盘上，照 `state` 与进度屏核实后再决定要不要重拉')


# ─────────────────────────────────────────────────────────────────────────────
# 一、提示词与 Commander plan（纯函数；preview 与真跑共用——`R-11.8` 同构）
# ─────────────────────────────────────────────────────────────────────────────

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
        '不再询问；每条付费命令的判据读数仍逐条留痕并进终审包。'
        if authorized else
        '**付费授权未授予**：不得执行会产生外部模型调用费用的命令；在第一条付费命令之前停下，'
        '但仍须完成只读的 state、inbox、下一节点 preview 并创建终审包草稿，把「跑到哪、为什么停、'
        '恢复命令」写进去（落点见下），然后结束。')
    egress_authorization = (
        '**正文外发授权已授予**：允许把小说正文及其派生上下文发送给外部模型。'
        '这项授权不代表已经授予付费授权。'
        if egress_authorized else
        '**正文外发授权未授予**：不得把小说正文或其派生上下文发送给外部模型；'
        '即使付费授权已经授予，也只能先完成只读的 state、inbox、下一节点 preview 与终审包草稿，'
        '并在首个需要正文外发的步骤前停下、写明恢复命令。')
    commander = profiles.commander
    executor = profiles.executor_default
    overrides = ', '.join(
        f'{node}={profile.driver}/{profile.model}/{profile.reasoning_effort}/'
        f'{profile.execution_mode}'
        for node, profile in sorted(profiles.executor_overrides.items())) or '无'
    return f"""你在仓库 {REPO_ROOT.as_posix()} 的根目录下工作。launcher 已把 cwd 固定在仓根；
每次 Bash 调用都必须保持在仓根，不得 cd，读取 base 与产物一律使用绝对路径。

任务：把 sid={sid} 这本书端到端跑到 `{book_target}`。
本次启动代码指纹：`{code_version}`。每个写盘节点或付费节点都须在执行前用
`{py} -X utf8 -c "from novel import prov; print(prov.code_version())"`
复算并逐字比较；不一致立即停下并在终审包披露。服务端结束终验也会再次比较，
跨版本运行不会被判为 ready。
base 目录（--base 恒用下面这个绝对路径）：
{base_abs}

付费授权：{payment_authorization}
正文外发授权：{egress_authorization}

本次 agent 选择已经冻结进 append-only run contract：
- Commander：{commander.driver}/{commander.model}/{commander.reasoning_effort}/{commander.execution_mode}
- 默认 executor：{executor.driver}/{executor.model}/{executor.reasoning_effort}/{executor.execution_mode}
- 节点覆盖：{overrides}
所有 node 动作（包括 0 成本复检与恢复）都必须先取当前 `operator next`，再为 exact
node/flags 取得 execution permit，并经 operator 的 ``run_step.py`` 执行；它会用父进程注入的
run id 与 contract SHA-256 重读 started 事件，核验 permit、active claim、executor profile、
代码指纹和两项授权。不得直接调用 `novel.cli run`、自行拼模型参数，或拿当前环境变量覆盖冻结选择。

启动顺序（不可跳步）：
1. 先读 `.claude/skills/novel-pipeline-operator/SKILL.md` 全文，并照它的“按需加载资源”读取
   工作流契约、代批与终审契约及 headless 运行环境；失败时再读修复状态机，按节点名/rule id
   检索经验库。不要依赖旧版章节号或聊天记忆。
2. 先运行 `operator next {sid} --json`；它是 queue、state 与 inbox 同一冻结读面的唯一下一动作。
   `state` / `inbox` 只用于展开证据，不得代替 next，也不得从 Stage 表、目录存在性或旧记忆反推节点。
3. 在任何写盘或付费动作前，按 skill 模板创建终审包草稿；以后每次授权判断、闸门裁决、
   修复与异常都立即更新，不要等结束时凭记忆补写。
4. `operator next` 为 ready，或 blocked 但 skill 登记了 exact recovery 时，先调用
   `operator permit`；把返回的 permit SHA 原样交给 `run_step.py` preview，再执行同一 runner 命令。
   每次动作后都重新取 next 与 permit，旧 hash 不得复用。下一步将调用外部模型时，先按 capability
   与冻结 profile 核对对应 CLI 的本机可探测状态；节点 executor 会在正文外发前再用同一 driver
   做零模型 probe。陈述付费/外发边界，再执行 preview 给出的完整步骤并复验。agent executor
   主路不带 `--response-repair agent`；候选被拒或传输中断时，使用该节点同一 logical session
   与 durable checkpoint 续跑。只有显式选择 legacy DeepSeek 临时回滚、且已到终态时，
   才先按 skill 运行 `doctor --llm-transport`，再用 sidecar `response-repair` 处理终态点名的
   `_llm_rejected/**/candidate.json`；不得自动切到该回滚路。

无人值守纪律：
- 没有用户可以提问。任何 redo/unknown 或闸门争议，按 skill 的修复状态机与代批契约
  处置并把依据写进终审包；无法安全处置就写清现状后停下——停机是合法结局。
- 没有可定位证据时必须保留 `UNVER` 或以 `stopped` 结束；不得猜测 PASS、删除 finding、
  为了“全绿”自行发明阈值，或把 exit 0 当成交付 ready。
- 所有 shell/`run_step.py` 都必须前台同步执行；禁止 background、detached、
  `run_in_background`、`Start-Process` 或先退出外层 shell 再留子进程继续写盘。工具若返回
  可等待的 session/cell id，必须持续 wait 到原命令真实终态，不能把“已启动”当“已结束”。
- node 的 start/end 信标只由 `run_step.py --execute` 在同一 active claim 内生成，不得手工补 `mark`
  或后台延迟打点。typed approval、response-repair、operator telemetry 与 canonical finalizer 不是 node，
  只走 skill 指定 façade；它们各自持 generic write claim。被唤起先重新取 `operator next` 再续跑。
- 命令形态：一律以 `{py}` 前缀开头（权限白名单只认这一个 Bash 形态）；
  正式 stage JSON/TXT、成书、`_service/*.jsonl`、`.llm_cache/`、`_rounds/` 与旧 run 输出严禁
  直接编辑，正式变更只经 `novel.cli`。Write/Edit 只用于 skill 明列的 typed 人工输入和下面
  唯一终审包，且不得超出 base（其余路径权限层直接拒）。
- 持续填写并在结束前完成终审包，落点就是这一条路径（不要另起文件名）：
  {review_target}"""


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
        slot = acc.setdefault(e.run_id, {'start': None, 'started': None,
                                         'ended': None, 'exit_code': None,
                                         'note': None, 'outcome': None})
        if e.run_id not in order:
            order.append(e.run_id)
        if e.kind == 'started':
            slot.update(start=e.start, started=e.ts)
        elif e.kind == 'ended':
            slot.update(ended=e.ts, exit_code=e.exit_code, outcome=e.outcome)
        else:
            slot['note'] = e.note
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
            state = 'running'
        else:
            state = 'unknown'
            slot['note'] = slot['note'] or RESTART_NOTE
        out.append(AgentRunRecord(
            run_id=run_id, start=slot['start'], started=slot['started'],
            ended=slot['ended'], exit_code=slot['exit_code'],
            state=state, note=slot['note'], outcome=slot['outcome']))
    return out


def records(base: str | os.PathLike[str], *, registry: JobRegistry = REGISTRY,
            ) -> list[AgentRunRecord]:
    return fold(read_events(base), live=registry.live())


def active_run(base: str | os.PathLike[str], *, registry: JobRegistry = REGISTRY,
               ) -> AgentRunRecord | None:
    """此刻在跑的 agent 代跑（全局一把锁 ⇒ 至多一个）。**给 jobs 路由的反向互斥用**。"""
    return next((r for r in records(base, registry=registry) if r.state == 'running'),
                None)


# ─────────────────────────────────────────────────────────────────────────────
# 三、拉起与熄火
# ─────────────────────────────────────────────────────────────────────────────

Spawn = Callable[
    [CommanderInvocation, dict[str, str], BinaryIO], CommanderProcessResult,
]


def _default_spawn(run_id: str) -> Spawn:
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
                COMMANDER_CONTRACT_SHA256_ENV):
            runtime_env[key] = env[key]
        runtime_invocation = CommanderInvocation(
            argv=invocation.argv,
            stdin_text=invocation.stdin_text,
            cwd=invocation.cwd,
            env_overrides=runtime_env,
            profile=invocation.profile,
            protocol=invocation.protocol,
            cli_version=invocation.cli_version,
        )
        driver = create_commander_driver(runtime_invocation.profile)
        with _COMMANDERS_LOCK:
            _COMMANDERS[run_id] = driver
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
        base: str, parent_run_id: str) -> _ExecutionDrainResult:
    """先关闭 admission，再等 claim 与 beacon 两面精确闭合。

    300 秒只触发一次报警/cancel。越线后仍无限持有本书锁与 queue slot，且不做
    delivery、不写 ended；只有盘上两面都闭合才返回。这样服务进程活着时不会
    为了“避免卡队列”主动制造孤儿并发；若服务崩溃，下一次 start/jobs 还会由
    durable unresolved admission 继续关闭式挡住。
    """
    from novel.orchestrator import beacon

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
            admission.close_gate(base, parent_run_id)
            # close_gate 的事务只管 admission 顺序；随后取联合快照，才能看见
            # reconciled 之后新增的任一 beacon 对旧 seal 的反证。
            gate = admission.snapshot(base, parent_run_id)
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
                admission.mark_drained(base, parent_run_id)
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
            cancel_note = _cancel_commander_owner(parent_run_id)
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
    attempt_id = jobs.new_job_id()
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
    gate_open_attempted = False
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
            run_id=run_id, kind='started', ts=int(time.time()),
            start=start_payload))
        outer_started = True
        agent_session_audit.append_event(
            base,
            agent_session_audit.AgentSessionEvent(
                session_run_id=session_run_id,
                parent_run_id=run_id,
                role='commander', node=None, chapter=None,
                subject_sha256=prompt_sha256,
                profile=plan.profiles.commander,
                requested_model=plan.profiles.commander.model,
                requested_effort=plan.profiles.commander.reasoning_effort,
                cli_version=invocation.cli_version,
                protocol_version=invocation.protocol,
                start_code_version=code_version,
                skill_sha256=skill_sha256,
                prompt_sha256=prompt_sha256,
                schema_sha256=schema_sha256,
                kind='started', ts=int(time.time()),
            ),
        )
        # 必须先于 Commander spawn 持久化；后续 close/claim 在这同一文件的
        # append transaction 上排序，顶层退出后才启动的迟到 run_step 因而必拒。
        # 先记 attempted：append 可能已经提交一整行、却在返回前抛 I/O 异常；
        # 那种“调用方没拿到返回值”不能被误读成“盘上一定没 gate”。
        gate_open_attempted = True
        admission.open_gate(base, run_id, sid)
    except BaseException as original:
        cleanup_failures: list[str] = []
        try:
            if gate_open_attempted:
                _close_failed_start_gate(base, run_id, original)
            if outer_started:
                try:
                    append(base, AgentRunEvent(
                        run_id=run_id, kind='ended', ts=int(time.time()),
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

    def worker() -> None:
        rc = CRASH
        observation = CommanderObservation(
            provider_session_id=None, actual_model=None,
            actual_effort=None, usage=None)
        runtime_error_kind: str | None = None
        integrity_error_kind: str | None = None
        lifecycle_error_kind: str | None = None
        completion_error_kind: str | None = None
        lifecycle_error_reason: str | None = None
        provider_reuse_owner: str | None = None
        provider_turn_recorded = False
        invoked = False
        try:
            log = run_log_path(base, run_id)
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, 'ab') as fh:
                # prompt 只走 stdin；这一行可公开诊断 argv 而不会复制任务正文。
                fh.write(('$ ' + shell_line(invocation.argv) + '\n').encode('utf-8'))
                fh.flush()
                child_env = env_with_context(
                    run_id=run_id, base=base, start=start_payload,
                    env=os.environ)
                invoked = True
                try:
                    result = (spawn or _default_spawn(run_id))(
                        invocation, child_env, fh)
                finally:
                    # invocation 的 return/raise 边界立刻 close。失败只表示盘上还
                    # 不能证明 close；下面的 drain 会持锁重试，绝不凭内存猜已关。
                    try:
                        admission.close_gate(base, run_id)
                    except Exception:  # noqa: BLE001 —— drain 负责报警、重试与持锁
                        pass
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
        except BaseException as exc:            # noqa: BLE001 —— 崩了也要记 ended
            known_rc = getattr(exc, 'returncode', None)
            if isinstance(known_rc, int) and not isinstance(known_rc, bool):
                rc = known_rc
            runtime_error_kind = 'commander_runtime'
            _append_safe_traceback(base, run_id, prompt=plan.prompt)
        finally:
            try:
                drain = _wait_for_execution_closure(base, run_id)
                if drain.timed_out:
                    lifecycle_error_kind = 'commander_execution_barrier'
                    lifecycle_error_reason = (
                        drain.timeout_reason
                        or 'Commander execution drain 曾超过报警阈值')
                    outcome = AgentDeliveryOutcome(
                        state='unknown', checked_at=int(time.time()),
                        reason=lifecycle_error_reason)
                else:
                    try:
                        end_skill_sha256 = _skill_sha256()
                    except Exception:  # noqa: BLE001 —— skill 终点不可读不能冒充同契约
                        integrity_error_kind = 'operator_skill_unavailable'
                        outcome = AgentDeliveryOutcome(
                            state='unknown', checked_at=int(time.time()),
                            reason=(
                                'Commander 结束时 canonical operator skill 整树'
                                '无法重新取指纹；拒绝声称同一份操作契约'))
                    else:
                        if end_skill_sha256 != skill_sha256:
                            integrity_error_kind = 'operator_skill_drift'
                            outcome = AgentDeliveryOutcome(
                                state='unknown', checked_at=int(time.time()),
                                reason=(
                                    'Commander 运行期间 canonical operator skill '
                                    '整树指纹发生变化；拒绝混用两份操作契约'))
                        else:
                            outcome = _assess_delivery(
                                base, sid, code_version=code_version)
                if (observation.actual_model is not None
                        and observation.actual_model
                        != plan.profiles.commander.model):
                    integrity_error_kind = (
                        integrity_error_kind or 'commander_model_drift')
                if (observation.actual_effort is not None
                        and observation.actual_effort
                        != plan.profiles.commander.reasoning_effort):
                    integrity_error_kind = (
                        integrity_error_kind or 'commander_effort_drift')
                if (lifecycle_error_kind is None
                        and integrity_error_kind is None
                        and runtime_error_kind is None):
                    completion = commander_completion.review_commander_stop(
                        base, sid, outcome, process_exit_code=rc)
                    outcome = completion.outcome
                    completion_error_kind = completion.error_kind
                # 每一次已经尝试的 Commander invocation 都有成本行；usage 未回显
                # 就是 null。它与 session turn 共用 attempt_id，但缺 provider id 时
                # 后者不会被伪造。
                if invoked:
                    try:
                        append_commander_call(
                            base, run_id=run_id,
                            session_run_id=session_run_id,
                            sid=sid, attempt_id=attempt_id,
                            request_model=plan.profiles.commander.model,
                            requested_effort=(
                                plan.profiles.commander.reasoning_effort),
                            code=code_version,
                            protocol_version=invocation.protocol,
                            actual_model=observation.actual_model,
                            actual_effort=observation.actual_effort,
                            provider_session_id=(
                                observation.provider_session_id),
                            cli_version=invocation.cli_version,
                            usage=_usage_dict(observation),
                            outcome=('ok' if rc == 0 else 'error'),
                            exit_code=rc,
                            error_kind=(lifecycle_error_kind
                                        or integrity_error_kind
                                        or completion_error_kind
                                        or runtime_error_kind or (
                                None if rc == 0 else 'commander_process_exit')),
                        )
                    except Exception:  # noqa: BLE001 —— 成本账失败不能吞掉 ended
                        integrity_error_kind = 'commander_ledger'
                        _append_safe_traceback(
                            base, run_id, prompt=plan.prompt)

                if observation.provider_session_id is not None:
                    try:
                        provider_reuse_owner = (
                            agent_session_audit.append_commander_turn(
                                base,
                                agent_session_audit.AgentSessionEvent(
                                    session_run_id=session_run_id,
                                    parent_run_id=run_id,
                                    role='commander', node=None, chapter=None,
                                    subject_sha256=prompt_sha256,
                                    profile=plan.profiles.commander,
                                    provider_session_id=(
                                        observation.provider_session_id),
                                    turn_index=1,
                                    attempt_id=attempt_id,
                                    requested_model=(
                                        plan.profiles.commander.model),
                                    actual_model=observation.actual_model,
                                    requested_effort=(
                                        plan.profiles.commander.reasoning_effort),
                                    actual_effort=observation.actual_effort,
                                    cli_version=invocation.cli_version,
                                    protocol_version=invocation.protocol,
                                    start_code_version=code_version,
                                    skill_sha256=skill_sha256,
                                    prompt_sha256=prompt_sha256,
                                    schema_sha256=schema_sha256,
                                    usage=_session_usage(observation),
                                    error_kind=(lifecycle_error_kind
                                                or integrity_error_kind
                                                or completion_error_kind
                                                or runtime_error_kind),
                                    kind='turn', ts=int(time.time()),
                                ),
                            )
                        )
                        provider_turn_recorded = provider_reuse_owner is None
                    except Exception:  # noqa: BLE001 —— 审计失败关闭交付，不丢 ended
                        integrity_error_kind = 'commander_session_audit'
                        _append_safe_traceback(
                            base, run_id, prompt=plan.prompt)

                if provider_reuse_owner is not None:
                    integrity_error_kind = (
                        agent_session_audit.PROVIDER_SESSION_REUSED)
                    outcome = AgentDeliveryOutcome(
                        state='unknown', checked_at=int(time.time()),
                        reason=(
                            'Commander 返回的 provider session '
                            f'已属于 logical session {provider_reuse_owner!r}；'
                            '拒绝把串会话的运行判为可交付'))

                if (lifecycle_error_reason is not None
                        and provider_reuse_owner is None):
                    outcome = AgentDeliveryOutcome(
                        state='unknown', checked_at=int(time.time()),
                        reason=lifecycle_error_reason)
                elif (integrity_error_kind is not None
                      and provider_reuse_owner is None):
                    outcome = AgentDeliveryOutcome(
                        state='unknown', checked_at=int(time.time()),
                        reason=(
                            '盘面终验已经执行，但 Commander 身份/审计链不闭合：'
                            f'{integrity_error_kind}；拒绝把运行判为可交付'))

                try:
                    end_code_version = prov.code_version()
                except Exception:  # noqa: BLE001 —— 指纹器崩也要留 ended
                    end_code_version = prov.CODE_UNKNOWN
                delivery_state = _session_delivery(outcome)
                if (delivery_state == 'delivered'
                        and end_code_version != code_version):
                    delivery_state = 'unknown'
                    integrity_error_kind = (
                        integrity_error_kind or 'code_version_drift')
                    outcome = AgentDeliveryOutcome(
                        state='unknown', checked_at=int(time.time()),
                        reason='Commander 运行期间代码指纹漂移；拒绝判为可交付')
                owned_provider_session_id = (
                    observation.provider_session_id
                    if provider_turn_recorded else None)
                try:
                    agent_session_audit.append_event(
                        base,
                        agent_session_audit.AgentSessionEvent(
                            session_run_id=session_run_id,
                            parent_run_id=run_id,
                            role='commander', node=None, chapter=None,
                            subject_sha256=prompt_sha256,
                            profile=plan.profiles.commander,
                            provider_session_id=owned_provider_session_id,
                            requested_model=plan.profiles.commander.model,
                            actual_model=observation.actual_model,
                            requested_effort=(
                                plan.profiles.commander.reasoning_effort),
                            actual_effort=observation.actual_effort,
                            cli_version=invocation.cli_version,
                            protocol_version=invocation.protocol,
                            start_code_version=code_version,
                            end_code_version=end_code_version,
                            skill_sha256=skill_sha256,
                            prompt_sha256=prompt_sha256,
                            schema_sha256=schema_sha256,
                            process_state=('ok' if rc == 0 else 'failed'),
                            delivery_state=delivery_state,
                            error_kind=(integrity_error_kind
                                        or lifecycle_error_kind
                                        or completion_error_kind
                                        or runtime_error_kind or (
                                            None if rc == 0 else
                                            'commander_process_exit')),
                            kind='ended', ts=int(time.time()),
                        ),
                    )
                except Exception:  # noqa: BLE001 —— outer ended 仍必须落账
                    integrity_error_kind = 'commander_session_audit'
                    outcome = AgentDeliveryOutcome(
                        state='unknown', checked_at=int(time.time()),
                        reason='Commander ended 审计未能闭合；拒绝判为可交付')
                    _append_safe_traceback(
                        base, run_id, prompt=plan.prompt)
                finally:
                    append(base, AgentRunEvent(
                        run_id=run_id, kind='ended', ts=int(time.time()),
                        exit_code=rc, outcome=outcome))
            finally:
                try:
                    _close_commander_owner(run_id)
                except Exception:  # noqa: BLE001 —— owner close 不得吃掉互斥释放
                    _append_safe_traceback(base, run_id, prompt=plan.prompt)
                finally:
                    try:
                        registry.release(key, run_id)
                        with _THREADS_LOCK:
                            _THREADS.pop(run_id, None)
                        # ⚠ 位置有讲究：锁**已经还掉**才轮到下一个进场，否则 `_drain`
                        # 看到的仍是满的，队列会一直等到下一次事件才动。
                        _drain(registry=registry)
                    finally:
                        # 这才是 worker 的最外层终点：delivery/ended、driver close、
                        # 进程内 writer 和排队 drain 任一步异常都必须释放 OS lease。
                        try:
                            release_owner_lease()
                        except BaseException:  # noqa: BLE001 —— OS close 失败仍留诊断
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
        # thread 根本没起来，不会有 claim；仍把 spawn 前建立的 durable gate
        # 明确 close，避免服务重启后把它读成可能存在孤儿执行。
        try:
            _close_failed_start_gate(base, run_id, original)
        finally:
            try:
                failed = AgentDeliveryOutcome(
                    state='failed', checked_at=int(time.time()),
                    reason='Commander worker 线程未能启动')
                try:
                    agent_session_audit.append_event(
                        base,
                        agent_session_audit.AgentSessionEvent(
                            session_run_id=session_run_id,
                            parent_run_id=run_id,
                            role='commander', node=None, chapter=None,
                            subject_sha256=prompt_sha256,
                            profile=plan.profiles.commander,
                            requested_model=plan.profiles.commander.model,
                            requested_effort=(
                                plan.profiles.commander.reasoning_effort),
                            cli_version=invocation.cli_version,
                            protocol_version=invocation.protocol,
                            start_code_version=code_version,
                            end_code_version=prov.code_version(),
                            skill_sha256=skill_sha256,
                            prompt_sha256=prompt_sha256,
                            schema_sha256=schema_sha256,
                            process_state='failed', delivery_state='failed',
                            error_kind='commander_thread_start',
                            kind='ended', ts=int(time.time()),
                        ),
                    )
                finally:
                    append(base, AgentRunEvent(
                        run_id=run_id, kind='ended', ts=int(time.time()),
                        exit_code=CRASH, outcome=failed))
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


def stop(base: str | os.PathLike[str], run_id: str) -> AgentRunRecord:
    """熄火：只让拥有该 run 的 Commander driver 终止自己那棵进程树。"""
    with _COMMANDERS_LOCK:
        driver = _COMMANDERS.get(run_id)
    if driver is None:
        raise JobConflict(  # 复用「流程说这一步现在不该发生」的那一族，不是 bug
            f'run_id={run_id!r} 没有在跑的子进程（已结束，或服务重启后句柄已丢）')
    if not driver.cancel(run_id):
        raise JobConflict(
            f'run_id={run_id!r} 的 Commander driver 没有确认终止它拥有的进程树')
    if not wait(run_id, 10):
        raise JobConflict(
            f'run_id={run_id!r} 已发熄火命令，但 worker 尚未完成终态落账')
    rec = next((r for r in records(base) if r.run_id == run_id), None)
    if rec is None:
        raise UpstreamMissing(f'没有 run_id={run_id!r} 的 agent 代跑记录')
    return rec
