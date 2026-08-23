#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent.py —— 前端一键唤起 **headless agent**（`claude -p`）代跑一本书。

    Output/_service/agent_runs.jsonl        append-only 事件流（生死与改判）
    Output/_service/logs/agent-<run_id>.log agent 进程的 stdout+stderr，按字节追加

【它为什么不是 `jobs.jsonl` 的一种新作业】`JobStart` 的后置条件钉死
`node ∈ NODES`，而 agent 代跑**不是流水线节点**——它是「拉起一个会自己去跑
节点的进程」。往 `NODES` 塞一个假节点，污染的是完成度/贵贱/写盘效应的
全部派生源头。所以它有自己的事件文件，形状照抄 `jobs.py`（同一条
append-only 理由：永不读-改-写，被 kill 最坏只损坏最后一行）。

【Spike 结论（2026-08-15，`claude` 2.1.219 本机实测）】
1. `claude -p`（cwd=仓根）**自动加载** `.claude/skills/`——提示词里仍写明
   SKILL.md 的路径，双保险不花钱。
2. 白名单规则 `Bash(D:/Miniconda/envs/novel_refactor/python.exe *)` 命中
   skill 的全部命令形态；复合命令逐段判，`echo`/`grep` 等只读命令属于
   内建免批集合，不需要规则。⇒ Bash 规则只需要**一条 python 前缀**。
3. **`Write(<路径>)` 规则被接受但从不被查阅**（官方原文；2.1.219 实测三次全拒）。
   文件写的作用域要写在 **`Edit(<路径>)`** 上（它覆盖所有改文件的工具），
   且 Windows 路径先归一成 POSIX 再加 `//` 锚：`E:\\x\\y` → `//e/x/y`。
4. `shutil.which('claude')` 得 `D:\\npm-global\\claude.CMD`，而 `.cmd` 确实不能被
   `CreateProcess` 直接执行。**但答案不是套一层 `cmd /c`**——`cmd` 在参数的第一个
   换行处截断，而提示词是多行的。npm 的 `.cmd` 只是转发 shim，真入口是包内的
   `.exe`，直接拉它（见 `_claude_entry`）。
5. `claude -p` 永不弹工作区信任框，也**不读**项目 `.claude/settings.json` 的
   allow 规则（官方原文明写）——白名单只能靠 `--allowedTools` 逐次带。

【权限姿态】`--permission-mode manual` + 上面的白名单：白名单外一律拒
（headless 没有人可问）。这把 SKILL 的写路径白名单从「纪律」升级成
「结构拦截」——agent 想直改产物 JSON 或仓代码，权限层当场拒掉。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import BinaryIO, NamedTuple

from contracts.schema.orchestration import (
    AgentDeliveryOutcome, AgentPending, AgentQueueView, AgentRejected, AgentRunEvent,
    AgentRunRecord, AgentRunStart,
)
from novel import paths, prov
from novel.appendlog import append_line
from novel.cli.exit_codes import CRASH
from novel.errors import UpstreamMissing
from novel.orchestrator import jobs
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
#: ⚠ 为什么必须有闸：一个 agent 是一整个 `claude` 会话，它自己烧 token，
#: 而它起的 `novel.cli` 子进程还会再烧一遍 DeepSeek——**一本书两笔钱**。
#: 没有闸的话，界面上勾 10 本点一下就是 10 份同时烧，且 API 配额是共享的。
#: ⇒ 超出上限的不报错、进队列，由结束的那个 agent 把下一个带起来（`_drain`）。
#: ⚠ 它与锁是**两件事**：锁保证「同一本书不被两个 agent 同时写」，
#: 闸保证「同时烧的钱不超过 N 份」。把两者合成一个数字就会得到
#: 「想并发就得放弃同书互斥」的假选择。
MAX_CONCURRENT = 3

_THREADS_LOCK = threading.Lock()
_THREADS: dict[str, threading.Thread] = {}
_PROCS_LOCK = threading.Lock()
_PROCS: dict[str, subprocess.Popen] = {}

#: `R-11.11` 在这边的样子：服务重启后，agent 子进程是死是活我们真的不知道。
RESTART_NOTE = ('服务重启前它是死是活我们不知道——'
                '产物与信标都在盘上，照 `state` 与进度屏核实后再决定要不要重拉')


# ─────────────────────────────────────────────────────────────────────────────
# 一、提示词与命令行（纯函数；preview 与真跑共用——`R-11.8` 同构）
# ─────────────────────────────────────────────────────────────────────────────

def _python_rule() -> str:
    """白名单认得的唯一 Bash 形态。**与 skill 工作流契约的命令前缀同源**：
    服务进程跑在哪个解释器下，agent 的 CLI 调用就走哪个——两处不许分家。"""
    return f'Bash({Path(sys.executable).as_posix()} *)'


def _posix_anchor(path: str | os.PathLike[str]) -> str:
    """Windows 绝对路径 → gitignore `//` 锚（Spike 3）：`E:\\x\\y` → `//e/x/y`。"""
    posix = Path(path).resolve().as_posix()
    m = re.match(r'^([A-Za-z]):/(.*)$', posix)
    return f'//{m.group(1).lower()}/{m.group(2)}' if m else f'//{posix}'


def build_prompt(sid: str, base: str | os.PathLike[str], *,
                 authorized: bool, egress_authorized: bool,
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

启动顺序（不可跳步）：
1. 先读 `.claude/skills/novel-pipeline-operator/SKILL.md` 全文，并照它的“按需加载资源”读取
   工作流契约、代批与终审契约及 headless 运行环境；失败时再读修复状态机，按节点名/rule id
   检索经验库。不要依赖旧版章节号或聊天记忆。
2. 先运行 `state {sid}` 与 `inbox {sid}`，只按盘上事实确定续跑位置。
3. 在任何写盘或付费动作前，按 skill 模板创建终审包草稿；以后每次授权判断、闸门裁决、
   修复与异常都立即更新，不要等结束时凭记忆补写。
4. 下一步将调用外部模型时，先运行 `doctor --llm-transport`；随后 preview 下一动作，
   陈述付费/外发边界，再执行 preview 给出的完整步骤并复验。

无人值守纪律：
- 没有用户可以提问。任何 redo/unknown 或闸门争议，按 skill 的修复状态机与代批契约
  处置并把依据写进终审包；无法安全处置就写清现状后停下——停机是合法结局。
- 没有可定位证据时必须保留 `UNVER` 或以 `stopped` 结束；不得猜测 PASS、删除 finding、
  为了“全绿”自行发明阈值，或把 exit 0 当成交付 ready。
- 打点纪律照工作流契约：主链每条命令与修复回路的每条命令，前后各 `mark` 一次；
  `preview` 不打点。被唤起先 `state {sid}` 判位置再续跑。
- 命令形态：一律以 `{py}` 前缀开头（权限白名单只认这一个 Bash 形态）；
  正式 stage JSON/TXT、成书、`_service/*.jsonl`、`.llm_cache/`、`_rounds/` 与旧 run 输出严禁
  直接编辑，正式变更只经 `novel.cli`。Write/Edit 只用于 skill 明列的 typed 人工输入和下面
  唯一终审包，且不得超出 base（其余路径权限层直接拒）。
- 持续填写并在结束前完成终审包，落点就是这一条路径（不要另起文件名）：
  {review_target}"""


#: npm 生成的 `.cmd` shim 里那一行转发目标：`"%dp0%\\...\\claude.exe"   %*`。
#: 只认 `.exe`——`%dp0%` 是 shim 自己的目录，展开后就是真入口的绝对路径。
_SHIM_TARGET = re.compile(r'"%dp0%\\?(?P<rel>[^"]+\.exe)"', re.IGNORECASE)


def _entry_from_shim(shim: str) -> str | None:
    """从 npm 的 `.cmd` shim 原文里读出它真正转发到的可执行文件。

    ⚠ **不硬编码 `node_modules/@anthropic-ai/claude-code/bin/`**：那是 npm 的布局，
    不是我们的契约，它换一次我们就静默地退回坏路径。shim 自己写着目标在哪，读它。
    """
    try:
        text = Path(shim).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None
    m = _SHIM_TARGET.search(text)
    if m is None:
        return None
    target = Path(shim).parent / m.group('rel').lstrip('\\/')
    return str(target) if target.is_file() else None


def _claude_entry() -> str:
    """能被 `CreateProcess` **直接**拉起的 claude 入口。

    ⚠⚠ **绝不退回 `cmd /c <shim>`。** `cmd` 在参数的第一个换行处截断，而
    `build_prompt` 是多行的：2026-08-19 实测五本书全部只收到提示词首行
    （任务、sid、两项授权、终审包落点全丢），agent 于是自己找事做，
    **然后 exit 0**——事件流折成 `ok`、界面显示成功、盘上一步没动。
    这条路最坏的地方不是它错，是它**安静地**错。

    ⇒ 解析不出真入口时**返回 shim 本身**，让 `Popen` 当场 `WinError 193` 崩掉
    （记 `exit_code=70`「结论未知」，日志留全栈）。响亮地崩比截断后假装成功好。
    """
    found = shutil.which('claude')
    if found is None:
        return 'claude'
    if not found.lower().endswith(('.cmd', '.bat')):
        return found                            # 已经可以直接执行
    return _entry_from_shim(found) or found


def build_argv(prompt: str, base: str | os.PathLike[str]) -> list[str]:
    """拉起 agent 的完整命令行（解析后的真实形态，随事件全文留档）。

    ⚠ 提示词是 argv 里**独立的一项**，不经任何 shell 拼接（见 `_claude_entry`）；
    `--add-dir` 把 base 纳入文件访问面（base 不在 cwd 下时读权限照样成立）。
    """
    base_abs = os.path.abspath(os.fspath(base))
    return [_claude_entry(), '-p', prompt, '--permission-mode', 'manual',
            '--add-dir', base_abs,
            '--allowedTools', _python_rule(), f'Edit({_posix_anchor(base_abs)}/**)']


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

Spawn = Callable[[Sequence[str], str, dict, BinaryIO], int]


def _default_spawn(run_id: str) -> Spawn:
    """拉起子进程并把句柄留在 `_PROCS`（`stop` 要用）。日志写法同 `jobs.default_spawn`。"""

    def go(argv: Sequence[str], cwd: str, env: dict, log: BinaryIO) -> int:
        kw = {}
        if os.name == 'nt':
            # CREATE_NEW_PROCESS_GROUP：让 `taskkill /T` 能整棵树端掉
            # （agent 自己还会起 `novel.cli` 孙进程，只杀根杀不干净）。
            kw['creationflags'] = (subprocess.CREATE_NO_WINDOW
                                   | subprocess.CREATE_NEW_PROCESS_GROUP)
        proc = subprocess.Popen(list(argv), cwd=cwd, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, bufsize=0, **kw)
        assert proc.stdout is not None
        with _PROCS_LOCK:
            _PROCS[run_id] = proc
        try:
            for block in iter(lambda: proc.stdout.read(4096), b''):
                log.write(block)
                log.flush()
            return proc.wait()
        finally:
            with _PROCS_LOCK:
                _PROCS.pop(run_id, None)

    return go


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

    base: str
    sid: str
    authorized: bool
    egress_authorized: bool
    spawn: Spawn | None


#: 待跑队列。**只在内存**——与 `AGENT_REGISTRY` 同一条理由（`R-14.6` 那张表）：
#: 写下来就要有陈旧项的清理策略，清理策略又要有守卫，而一份**会说谎的队列**
#: 比没有队列更坏。⇒ 服务重启即清空，界面上照实写「重启不保留」。
_PENDING_LOCK = threading.Lock()
_PENDING: list[_Pending] = []


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


def _drain(*, registry: JobRegistry = REGISTRY) -> None:
    """把队首能起的起起来。由结束的 worker 调用，`enqueue` 受理完也调一次。

    ⚠ 两种失败**处理方式相反**，这正是 `AgentBusy` 单独成类的原因：
      · 满闸（`AgentBusy`）⇒ **放回队首**并停下——名额是别人的，等他还回来；
      · 这本书起不来（同书已有 agent / 该书有 API 作业在写盘）⇒ **丢弃并继续**下一本。
        留在队里反复重试只会把同一条冲突刷成一片日志，而真正能跑的下一本永远轮不到。
    """
    while True:
        with _ADMIT_LOCK:
            if len(registry.live()) >= MAX_CONCURRENT:
                return
        with _PENDING_LOCK:
            if not _PENDING:
                return
            item = _PENDING.pop(0)
        try:
            start(item.base, item.sid, authorized=item.authorized,
                  egress_authorized=item.egress_authorized,
                  spawn=item.spawn, registry=registry)
        except AgentBusy:
            with _PENDING_LOCK:
                _PENDING.insert(0, item)
            return
        except Exception:                       # noqa: BLE001 —— 见 docstring
            continue


def enqueue(base: str | os.PathLike[str], sids: Sequence[str], *,
            authorized: bool, egress_authorized: bool,
            spawn: Spawn | None = None,
            registry: JobRegistry = REGISTRY) -> AgentQueueView:
    """批量受理 N 本：能起的立刻起，起不动的排队。**返回受理后的全景。**

    ⚠ 一本起不来不回滚其余：那本书上有 API 作业在写盘（唯一会失败的原因）
    与「另外八本能不能跑」无关，而抛一个「批量失败」会把已经起来的那几本藏掉——
    人看到的是一条报错，盘上却真的有进程在跑，那是最坏的一种不一致。
    ⇒ 逐本的落点全在返回值里：`running` / `pending` / `rejected` 三者之和 = 你提交的。
    """
    base_abs = os.path.abspath(os.fspath(base))
    rejected: list[AgentRejected] = []
    for sid in sids:
        with _PENDING_LOCK:
            queued = {p.sid for p in _PENDING if p.base == base_abs}
        if sid in queued:
            rejected.append(AgentRejected(sid=sid, reason='已经在队列里了'))
            continue
        if registry.holder(lock_key(sid)) is not None:
            rejected.append(AgentRejected(sid=sid, reason='这本书已有 agent 在跑'))
            continue
        with _PENDING_LOCK:
            _PENDING.append(_Pending(base_abs, sid, authorized,
                                     egress_authorized, spawn))
    _drain(registry=registry)
    view = queue_view(base, registry=registry)
    return AgentQueueView(running=view.running, pending=view.pending,
                          rejected=rejected, max_concurrent=view.max_concurrent)


def queue_view(base: str | os.PathLike[str], *,
               registry: JobRegistry = REGISTRY) -> AgentQueueView:
    """当前这个 base 上：谁在跑、谁在排队、闸开多大。"""
    base_abs = os.path.abspath(os.fspath(base))
    running = [r for r in records(base, registry=registry) if r.state == 'running']
    with _PENDING_LOCK:
        pending = [AgentPending(sid=p.sid, position=i + 1)
                   for i, p in enumerate(
                       [q for q in _PENDING if q.base == base_abs])]
    return AgentQueueView(running=running, pending=pending,
                          max_concurrent=MAX_CONCURRENT)


def start(base: str | os.PathLike[str], sid: str, *, authorized: bool,
          egress_authorized: bool,
          spawn: Spawn | None = None,
          registry: JobRegistry = REGISTRY) -> AgentRunRecord:
    """拉起一次 agent 代跑（**异步**）：锁 → 记 `started` → 线程跑子进程 → 记 `ended`。

    三道闸，各管一边：
      · 本书一把锁——同一本书一次只跑一个 agent（`lock_key`）；
      · 并发闸——同时在跑的 agent 不超过 `MAX_CONCURRENT`（满了抛 `JobConflict`，
        批量入口 `enqueue` 会把它接住改成排队，单本入口就是明确的 409）；
      · 反向互斥——这本书上有 API 作业在跑 ⇒ 409（agent 会自己起 `novel.cli`
        子进程，两边同时写同一本书就是 skill 禁的并行写盘）。
    ⚠ 顺序有讲究：**先占 agent 锁再查 API 作业**——`jobs.run/start` 起作业前
    必查 `AGENT_REGISTRY`，锁一到手就不会有新的 API 作业插进来；已跑着的
    由 `records()` 那一查查出来。反过来先查后占会留一条竞争缝。
    """
    code_version = prov.code_version()
    prompt = build_prompt(sid, base, authorized=authorized,
                          egress_authorized=egress_authorized,
                          code_version=code_version)
    argv = build_argv(prompt, base)
    run_id = jobs.new_job_id()
    key = lock_key(sid)
    _admit(key, run_id, registry)
    try:
        holder = jobs.REGISTRY.conflicting_holder(sid)
        if holder is not None:
            raise JobConflict(
                f'这本书已有 API 维护/节点作业在跑（{holder}）——等它结束再拉 agent')
        running = [r for r in jobs.records(base)
                   if r.state == 'running' and r.start.sid == sid]
        if running:
            raise JobConflict(
                f'这本书已有 API 作业在跑（{running[0].job_id}）——'
                f'agent 代跑与节点作业写同一片盘，等它结束再拉 agent')
        append(base, AgentRunEvent(
            run_id=run_id, kind='started', ts=int(time.time()),
            start=AgentRunStart(sid=sid, argv=list(argv), prompt=prompt,
                                 authorized=authorized,
                                 egress_authorized=egress_authorized,
                                 code_version=code_version)))
    except Exception:
        registry.release(key, run_id)
        raise

    def worker() -> None:
        rc = CRASH
        try:
            log = run_log_path(base, run_id)
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, 'ab') as fh:
                fh.write(('$ ' + shell_line(argv) + '\n').encode('utf-8'))
                fh.flush()
                rc = (spawn or _default_spawn(run_id))(
                    argv, str(REPO_ROOT), dict(os.environ), fh)
        except BaseException:                   # noqa: BLE001 —— 崩了也要记 ended
            import traceback
            with open(run_log_path(base, run_id), 'ab') as fh:
                fh.write(traceback.format_exc().encode('utf-8'))
        finally:
            try:
                if rc == 0:
                    try:
                        from novel.orchestrator import delivery
                        outcome = delivery.assess(
                            base, sid, start_code_version=code_version)
                    except Exception as exc:  # noqa: BLE001 —— 终验崩溃也必须留下 ended
                        outcome = AgentDeliveryOutcome(
                            state='unknown', checked_at=int(time.time()),
                            reason=('agent 进程已正常结束，但交付终验自身崩溃：'
                                    f'{type(exc).__name__}: {exc}'))
                else:
                    outcome = AgentDeliveryOutcome(
                        state='failed', checked_at=int(time.time()),
                        reason=f'headless agent 进程退出码为 {rc}，未执行成功交付终验')
                append(base, AgentRunEvent(
                    run_id=run_id, kind='ended', ts=int(time.time()),
                    exit_code=rc, outcome=outcome))
            finally:
                registry.release(key, run_id)
                with _THREADS_LOCK:
                    _THREADS.pop(run_id, None)
                # ⚠ 位置有讲究：锁**已经还掉**才轮到下一个进场，否则 `_drain`
                # 看到的仍是满的，队列会一直等到下一次事件才动。
                _drain(registry=registry)

    thread = threading.Thread(target=worker, name=f'novel-agent-{run_id}',
                              daemon=True)
    with _THREADS_LOCK:
        _THREADS[run_id] = thread
    try:
        thread.start()
    except Exception:
        with _THREADS_LOCK:
            _THREADS.pop(run_id, None)
        registry.release(key, run_id)
        append(base, AgentRunEvent(run_id=run_id, kind='ended',
                                   ts=int(time.time()), exit_code=CRASH,
                                   outcome=AgentDeliveryOutcome(
                                       state='failed', checked_at=int(time.time()),
                                       reason='agent worker 线程未能启动')))
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
    """熄火：**付费进程的必备闸**。句柄不在（已结束/别的实例拉的）⇒ 显式终态。

    返回前必须确认根进程已经退出。只调用 ``taskkill`` 而忽略退出码会把“发过停止命令”
    误报成“已经熄火”；受限 Windows 会话里该命令可能失败，而付费进程仍继续运行。
    """
    with _PROCS_LOCK:
        proc = _PROCS.get(run_id)
    if proc is None:
        raise JobConflict(  # 复用「流程说这一步现在不该发生」的那一族，不是 bug
            f'run_id={run_id!r} 没有在跑的子进程（已结束，或服务重启后句柄已丢）')
    if os.name == 'nt':
        # /T 整棵树（claude → agent 起的 novel.cli 孙进程），/F 强制。
        killed = subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                                capture_output=True)
        if killed.returncode != 0 and proc.poll() is None:
            # 至少保证记在句柄里的付费根进程停下；taskkill 成功时仍由 /T 负责整棵树。
            proc.kill()
    else:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired as exc:
            raise JobConflict(
                f'run_id={run_id!r} 已发熄火命令，但进程 {proc.pid} 仍未退出；'
                '不能把“发过命令”误报成“已经熄火”') from exc
    rec = next((r for r in records(base) if r.run_id == run_id), None)
    if rec is None:
        raise UpstreamMissing(f'没有 run_id={run_id!r} 的 agent 代跑记录')
    return rec
