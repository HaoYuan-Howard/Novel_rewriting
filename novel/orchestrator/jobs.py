#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
jobs.py —— 作业执行器：互斥 · 子进程 · 事件流 · 重启改判。

【`R-11.3` 已于 2026-08-11（骨架 D）收窄成「绝不落**产物**盘」，理由是一句话】
**「子进程被 OOM 杀掉了」这条记录只有父进程写得出来**——子进程写不了自己的死讯，
而退出码正是编排层判成败的依据（`R-12.3`）。
⇒ 服务进程写、且只写这两处，它们**不是产物**（不进来源账、不进 `_provenance`、
`ARTIFACTS` 里没有它们）：

    Output/_service/jobs.jsonl          append-only 事件流
    Output/_service/logs/<job_id>.log   子进程 stdout+stderr，按字节追加

【为什么是 append-only】与成本账同一条理由：**永不需要读-改-写** ⇒
并发安全、被 kill 不损坏、可 grep。
⚠ 原型每次状态变化都 `os.replace` 整份 `<job_id>.json`，并**为此写了一段重试**——
`PermissionError: [WinError 5]`，因为服务一边写一边被 SSE 轮询读，实测在测试里必现。

【两种入口共用同一执行内核】`run()` 给专用同步端点和测试使用；`start()` 在后台线程
执行同一组 `Step` 并立即返回 `running` 记录，供通用 `POST /api/jobs` 轮询。
两者共用互斥键、事件流、日志与 `_run_steps()`，预览和真跑仍是同一份命令对象。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import traceback
import uuid
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import BinaryIO

from contracts.invariants import NODES
from contracts.schema.orchestration import (
    JobEvent, JobRecord, JobStart, ProgressEvent, Step,
)
from novel.appendlog import append_line
from novel.cli import exit_codes as X
from novel.cli.exit_codes import CRASH
from novel.errors import TerminalState
from novel.orchestrator.nodes import build_steps, child_env, shell_line
from novel.paths import SERVICE_DIRNAME, service_dir
from novel.prov import code_version

JOBS_FILENAME = 'jobs.jsonl'
LOGS_DIRNAME = 'logs'


class JobConflict(TerminalState):
    """这本书已经有一个作业在跑（`R-11.10`）。**E 期在 HTTP 层把它翻成 409。**

    ⚠ 它是 `TerminalState` 不是 `RefusedByStructure`：并发请求不是 bug，
    是两个人（或两个标签页）同时点了按钮——那是**流程**说这一步现在不该开始。
    """


# ─────────────────────────────────────────────────────────────────────────────
# 一、互斥（`R-11.10`）
# ─────────────────────────────────────────────────────────────────────────────

def active_key(sid: str | None, node: str, mode: str | None = None) -> str:
    """一本书在互斥表里的键。**必须只有这一个落点。**

    ⚠⚠ **这条纪律是拿一个真 bug 换来的。** 原型 `start` 写进去的是
    `sid or '_global'`、而释放时拿 `meta['sid']` 去查——同一个键**算了两遍**，
    于是在 `sid=None` 上分家：写的是 `'_global'`、删的时候查 `None`，
    **锁永远留在表里**。而上传新书那条路恰恰不带 sid，
    所以第一本上传成功、第二本起全部 409，**直到重启服务**。
    判据 25 的一个实例：同一条规则的第二份实现不报错，它只在边角上悄悄换一个答案。

    ⚠ `sid=None` 一律归到 `'_global'` 这**一个**键（而不是各走各的）是刻意的：
    摄入是 0 成本且很快，而两个并发的摄入可能推导出同一个目录名、互相覆盖
    ——**串行比并发安全**。

    ⚠ **例外是「同一个节点的两个 mode 值」，不是「两个 kind」**：
    写作两条臂只差 (system prompt, 给不给源章原文)，产物形状逐字节相同，
    判据表只有一张 ⇒ 它们是一个节点的参数。原型给了它们两个 kind，
    于是不得不在互斥表里为这两个名字开一条特例分支——**特例是命名错误的下游症状**。
    """
    key = sid or '_global'
    has_arms = bool(NODES[node].modes) if node in NODES else False
    return f'{key}:{node}:{mode}' if has_arms and mode is not None else key


class JobRegistry:
    """进程内的互斥表。**不落盘**——见 `_orchestration.md` §8 第 5 条。

    ⚠ 锁写下来就要有陈旧清理策略，清理策略又要有守卫，而
    **一把会说谎的锁比没有锁更坏**（它让人以为互斥成立）。
    ⇒ 这条限制**登记在 `R-14.6` 那张表里，不假装它不存在**：
    服务重启即全丢，多实例、人在终端手敲 CLI 都绕得过去。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, str] = {}

    def acquire(self, key: str, job_id: str) -> None:
        with self._lock:
            conflict = next((
                (active, holder)
                for active, holder in self._active.items()
                if (active == key or active.startswith(key + ':')
                    or key.startswith(active + ':'))
            ), None)
            if conflict is not None:
                _active, cur = conflict
                raise JobConflict(
                    f'这本书已有作业在跑（{cur}）——等它结束，或换一本。'
                    f'⚠ 唯一的例外是写作的两条臂（deep/shallow）可以并发')
            self._active[key] = job_id

    def release(self, key: str, job_id: str) -> None:
        """⚠ **只还自己那一把。** 拿 `key` 无条件 `pop` 会在「上一个作业已经超时被
        别人接管」时把别人的锁还掉，而那时两个作业在同时写同一份产物。"""
        with self._lock:
            if self._active.get(key) == job_id:
                del self._active[key]

    def holder(self, key: str) -> str | None:
        with self._lock:
            return self._active.get(key)

    def conflicting_holder(self, key: str) -> str | None:
        """返回与 key 同书冲突的占用者；规则与 ``acquire`` 完全相同。"""
        with self._lock:
            return next((
                holder for active, holder in self._active.items()
                if (active == key or active.startswith(key + ':')
                    or key.startswith(active + ':'))
            ), None)

    def live(self) -> set[str]:
        with self._lock:
            return set(self._active.values())


#: 进程内那一份。E 期的服务进程共用它。
REGISTRY = JobRegistry()

#: **agent 代跑那把锁住在这里**（`novel/orchestrator/agent.py` 共用），
#: 这样 `run()`/`start()` 不 import agent 就能完成反向互斥——
#: agent 会自己起 `novel.cli` 子进程写同一 base 的盘，「agent 在跑」与
#: 「API 起作业」撞上的后果就是 SKILL 禁的并行写盘。
#: ⚠ 一本书一把（键见 `agent_lock_key`），所以这张表里**可以有多条**——
#: 条数上限由 `agent.MAX_CONCURRENT` 那道并发闸管，不由这张表管。
AGENT_REGISTRY = JobRegistry()

_THREADS_LOCK = threading.Lock()
_THREADS: dict[str, threading.Thread] = {}


#: agent 代跑互斥键的前缀。**唯一落点在这里**，不在 `agent.py`——
#: 表（`AGENT_REGISTRY`）住在本模块，键的形状就该跟着表走。
#: ⚠ 两边各写一遍这个字符串，就是 `active_key` 那个真 bug 的同款形状：
#: 改了一边不报错，它只在边角上悄悄换一个答案。
AGENT_LOCK_PREFIX = 'agent-run'


def agent_lock_key(sid: str) -> str:
    """一本书在 agent 互斥表里的键。**一本书一把锁**，不是全局一把。

    ⚠⚠ 这里曾经是一个不带 sid 的固定字符串，理由写着「SKILL：两本书不并行」。
    那是把 SKILL 那条**读大了**：它管的是「**一个 agent 会话**别同时管两本书」——
    会串的是证据、闸门裁决与终审包，而那些东西都长在单个会话里。
    N 个 headless agent 各自只看见自己那一本，每个都完整遵守 SKILL。
    ⇒ 锁该按它真正保护的资源分，而那个资源是**一本书**：同书两个 agent 会并行写
    同一片盘（SKILL 明禁），异书两个 agent 谁也碰不到谁的目录。
    ⚠ 那一次只改了代码对 SKILL 的解读、没动 SKILL 原文，于是那句话继续被读成
    「系统不支持多书并行」（本次实测：它确实又误导了一轮）。⇒ SKILL.md 与
    `references/runtime-profiles.md` 现已把**作用域**写进原文（「本次会话内」），
    语义未变；`R-13.7` 才是跨书并行那一侧的权威。

    ⚠ 「同时最多几个」是**另一件事**，不归锁管——它归
    `novel/orchestrator/agent.py::MAX_CONCURRENT` 那道并发闸。
    """
    return f'{AGENT_LOCK_PREFIX}:{sid}'


def _refuse_if_agent_running(sid: str | None) -> None:
    """**这本书**上有 agent 代跑活着时，它的 API 作业不许起（反向互斥的唯一落点）。

    ⚠ 从「有任何 agent 在跑就全拒」收窄成「这本书上有才拒」：agent 放开并发之后，
    前者会让 A 书的 agent 把 B 书的每一个节点作业一起挡死——而两者写的
    根本不是同一片盘，那道拒绝没有任何东西在保护。

    ⚠ **`sid is None` 仍然一律拒**（摄入那条路，`active_key` 把它归 `'_global'`）：
    上传要推导目录名，而任何一个 agent 都可能正在建目录。这一格宁可严。
    """
    if sid is None:
        if AGENT_REGISTRY.live():
            raise JobConflict(
                '有 agent 代跑正在跑——摄入要推导目录名，而 agent 可能正在建目录，'
                '等它结束（或先熄火）再上传')
        return
    holder = AGENT_REGISTRY.holder(agent_lock_key(sid))
    if holder is not None:
        raise JobConflict(
            f'这本书有 agent 代跑正在跑（{holder}）——它会自己起 CLI 子进程写盘，'
            f'等它结束（或先熄火）再起作业')


# ─────────────────────────────────────────────────────────────────────────────
# 二、事件流（append-only）
# ─────────────────────────────────────────────────────────────────────────────

# `service_dir` / `SERVICE_DIRNAME` 从 `novel/paths.py` 引入（**路径形状归它**）：
# `<base>/_service/` 这个字面还有第二个消费方——界面上传的原文按内容地址存在它底下，
# 而「记下来的绝对路径断了、文件其实还在」要靠同一段路径认回来
# （`novel/core/source.py`）。两处各写一份就会在下一次改布局时分家。


def jobs_path(base: str | os.PathLike[str]) -> Path:
    return service_dir(base) / JOBS_FILENAME


def log_path(base: str | os.PathLike[str], job_id: str) -> Path:
    return service_dir(base) / LOGS_DIRNAME / f'{job_id}.log'


def append_event(base: str | os.PathLike[str], event: JobEvent) -> None:
    """追加一行。**永不读-改-写。**

    ⚠ 一行一个 JSON、末尾带 `\\n`：被 kill 时最坏只损坏**最后一行**，
    而读的那一侧把半截行当没有（见 `read_events`）。
    整份 `os.replace` 的写法在这一点上是反的——它可能损坏**整份**记录。

    ⚠ 写入方不止服务进程：CLI `queue` 也走 `_run_lane` → `jobs.start`，
    而不同小说可以并行（`R-13.7`）⇒ 互斥必须跨进程，落点是 `novel/appendlog.py`。
    """
    # 不用 exclude_none：JobStart.sid 是必填可空，上传新书时必须把字面的 null 留下。
    # 全局剥 None 会把「字段存在且为 null」偷换成「字段缺失」，事件下一次就读不回来。
    append_line(
        jobs_path(base),
        json.dumps(event.model_dump(mode='json'), ensure_ascii=False))


def read_events(base: str | os.PathLike[str]) -> list[JobEvent]:
    """读回事件流。**半截的最后一行当没有，不当失败。**

    ⚠ 与 `ResponseCache` 那条同形：一份被 kill 截断的记录**不该让整个服务起不来**。
    ⚠ 但只容忍**最后一行**——中间有坏行说明这份文件被别的东西写过，那是另一回事，
    而把它一起吞掉会让「记录不全」变成静默。
    """
    p = jobs_path(base)
    if not p.is_file():
        return []
    raw = p.read_bytes().decode('utf-8', errors='replace').splitlines()
    out: list[JobEvent] = []
    for i, line in enumerate(raw):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(JobEvent.model_validate(json.loads(line)))
        except Exception:                       # noqa: BLE001
            if i == len(raw) - 1:
                break                           # 被 kill 截在半路，允许
            raise
    return out


def fold(events: Iterable[JobEvent], *, live: set[str] | None = None) -> list[JobRecord]:
    """事件流 → 作业记录。**折叠规则只有这一处。**

    ⚠ `state` 的五态：有 `ended` 看退出码；只有 `started` 而这个进程还占着锁 ⇒
    `running`；只有 `started` 而没人占着 ⇒ **`unknown`**（`R-11.11`）。
    **不是 `ok` 也不是 `failed`**——重启前它是死是活我们真的不知道。
    """
    live = set() if live is None else live
    by_id: dict[str, dict] = {}
    for e in events:
        cur = by_id.setdefault(e.job_id, {'steps': 0, 'note': None})
        if e.kind == 'started':
            cur['start'], cur['started'] = e.start, e.ts
        elif e.kind == 'step':
            cur['steps'] += 1
        elif e.kind == 'ended':
            cur['ended'], cur['exit_code'] = e.ts, e.exit_code
        elif e.kind == 'reconciled':
            cur['note'] = e.note

    out: list[JobRecord] = []
    for job_id, c in by_id.items():
        if 'start' not in c:
            continue                            # 只有尾巴没有头：那一行的头被截掉了
        code = c.get('exit_code')
        if code is not None:
            state = ('ok' if code == 0 else
                     'landed_red' if X.landed_but_red(code) else 'failed')
        elif job_id in live:
            state = 'running'
        else:
            state = 'unknown'
        out.append(JobRecord(
            job_id=job_id, start=c['start'], started=c['started'],
            ended=c.get('ended'), exit_code=code, state=state,
            steps_started=c['steps'],
            note=c['note'] or _default_note(state, code)))
    return sorted(out, key=lambda r: r.started, reverse=True)


RECONCILE_NOTE = '服务重启前这个作业还在跑；它是跑完了还是被一起杀掉了，无从判断'

#: `landed_red` 的人读处置：它不是「命令没跑成」，而是「产物已落盘、
#: 报告有阻断红行」。与 `failed` 分态后仍保留这句人读指引。
LANDED_RED_NOTE = (
    '产物**已经落盘**，但校验报告有阻断级红行（exit 1）。'
    '⚠ 这不是「没跑成」：该做的是打开对应层的屏看红行并裁决，不是修完重跑')


def _default_note(state: str, code: int | None) -> str | None:
    if state == 'unknown':
        return RECONCILE_NOTE
    if state == 'landed_red':
        return LANDED_RED_NOTE
    return None


def reconcile(base: str | os.PathLike[str], *,
              registry: JobRegistry = REGISTRY) -> list[str]:
    """服务启动时调一次：上次没跑完就退出的作业，一律改判 **`unknown`**。

    **不是 `ok` 也不是 `failed`** —— 重启前它是死是活我们真的不知道，
    而「缺失记作不知道」是 `Q-2` 的原话。写成 `ok` 就是凭空发一张通行证。

    ⚠⚠ **改判是追加一条 `reconciled` 事件，不是回去改 `started` 那一行。**
    回去改就毁掉了 append-only 的全部意义，而那正是「被 kill 不损坏」这条性质的来源。
    """
    live = registry.live()
    changed = []
    for rec in fold(read_events(base), live=live):
        if rec.state == 'unknown' and rec.note == RECONCILE_NOTE:
            append_event(base, JobEvent(job_id=rec.job_id, kind='reconciled',
                                        ts=int(time.time()), note=RECONCILE_NOTE))
            changed.append(rec.job_id)
    return changed


# ─────────────────────────────────────────────────────────────────────────────
# 三、日志：按字节存，只在读的时候解码
# ─────────────────────────────────────────────────────────────────────────────

def _trim_partial_utf8(chunk: bytes) -> bytes:
    """砍掉结尾处**不完整的** UTF-8 序列，返回完整的前缀。（原型这一段是对的，照搬）

    ⚠ 中文一个字跨 3 字节，而日志是按字节切块读的：切在字中间时
    `errors='replace'` 会把那个字变成 `�` —— 一个**不可逆**的损坏，
    而且下一次从 `end` 续读也补不回来。所以宁可少读几个字节，留给下一次。
    ⚠ **不能靠「切到最后一个换行」来代替**：日志里可能整段没有换行（实测就挂在这上面）。
    """
    n = len(chunk)
    if not n:
        return chunk
    k = 0                                        # 结尾连续的续接字节（0b10xxxxxx）个数
    while k < 3 and k < n and 0x80 <= chunk[n - 1 - k] <= 0xBF:
        k += 1
    i = n - 1 - k
    if i < 0:
        return chunk
    lead = chunk[i]
    if lead < 0x80:
        need = 1
    elif 0xC0 <= lead < 0xE0:
        need = 2
    elif 0xE0 <= lead < 0xF0:
        need = 3
    elif 0xF0 <= lead < 0xF8:
        need = 4
    else:
        return chunk                             # 非法前导字节：不是我们该修的
    return chunk if k + 1 >= need else chunk[:i]


def read_log(base: str | os.PathLike[str], job_id: str, from_byte: int = 0,
             max_bytes: int = 2 << 20) -> tuple[str, int]:
    """按字节读日志，返回 `(文本, 下一次的起始字节)`。**只在这里解码。**"""
    p = log_path(base, job_id)
    if not p.is_file():
        return '', from_byte
    size = p.stat().st_size
    if from_byte >= size:
        return '', size
    with open(p, 'rb') as f:
        f.seek(from_byte)
        chunk = f.read(min(max_bytes, size - from_byte))
    chunk = _trim_partial_utf8(chunk)
    return chunk.decode('utf-8', errors='replace'), from_byte + len(chunk)


# ─────────────────────────────────────────────────────────────────────────────
# 四、跑
# ─────────────────────────────────────────────────────────────────────────────

#: 起一个子进程的那条缝。**开着它，执行器就能零成本地跑在测试里**
#: （同 `novel/llm/transport.py::FakeTransport` 的理由：一条缝换来一整层的可测性）。
Spawn = Callable[[Sequence[str], str, dict, BinaryIO], int]


def default_spawn(argv: Sequence[str], cwd: str, env: dict, log: BinaryIO) -> int:
    """真的起一个子进程，stdout+stderr 合流按字节写进日志。

    ⚠ `stderr=STDOUT`：两条流分开存，人就要在两个文件之间猜先后顺序，
    而报错与它前一句上下文恰恰要连着读。
    ⚠ `stdin=DEVNULL`：子进程永远不该等一个不会来的输入——那会挂住整个队列。
    """
    kw = {}
    if os.name == 'nt':
        kw['creationflags'] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(list(argv), cwd=cwd, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, bufsize=0, **kw)
    assert proc.stdout is not None
    for block in iter(lambda: proc.stdout.read(4096), b''):
        log.write(block)
        log.flush()
    return proc.wait()


def new_job_id(now: float | None = None) -> str:
    """单调前缀 + 随机后缀。**不是自增数**——多进程下自增会撞。"""
    return f'{int(now if now is not None else time.time())}-{uuid.uuid4().hex[:8]}'


def plan_start(node: str, sid: str | None, flags: Sequence[str] = (), *,
               base: str | os.PathLike[str], mode: str | None = None,
               follow_check: bool = True,
               placeholders: bool = False,
               steps: Sequence[Step] | None = None) -> JobStart:
    """一个作业的**全部静态字段**。纯函数：不占键、不落事件、不起进程。

    ⚠⚠ **`R-11.8` 在这里从一句纪律变成字面事实。** `run()` 与
    `POST /api/jobs/preview` 调的是**同一个函数**，于是「界面上摊开给人看的那条命令」
    与「真的会被 `Popen` 的那条」是同一个对象——不是两份长得像的。
    原型那条裂缝（前端用 `argv.slice(4)` 硬编码切掉解释器前缀来渲染，
    `build_argv` 前缀长度一变就静默错位）在这里连发生的地方都没有。

    ⚠ `paid` / `writes` 在这里逐步折叠一次，而 `JobStart` 的后置条件会**反过来断言**
    它等于逐步折出来的——两处一致才构造得出来（`R-11.7`：贵贱只有一处算）。
    """
    plan = list(steps) if steps is not None else build_steps(
        node, sid, flags, base=base, mode=mode, follow_check=follow_check,
        placeholders=placeholders)
    return JobStart(
        sid=sid, node=node, mode=mode, flags=list(flags), steps=plan,
        python=sys.version, code=code_version(),
        paid=any(s.paid for s in plan),
        writes=any(bool(s.writes) for s in plan))


def _run_steps(base: str | os.PathLike[str], job_id: str,
               plan: Sequence[Step], spawn: Spawn) -> int:
    p = log_path(base, job_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    rc = 0
    env = child_env()
    with open(p, 'ab') as log:
        for index, step in enumerate(plan):
            event = JobEvent(job_id=job_id, kind='step',
                             ts=int(time.time()), step_index=index)
            append_event(base, event)
            command = '$ ' + shell_line(step.argv) + '\n'
            log.write(command.encode('utf-8'))
            log.flush()
            rc = spawn(step.argv, step.cwd, env, log)
            if rc != 0:
                break
    return rc


def _append_progress(base: str | os.PathLike[str], sid: str | None, node: str,
                     phase: str, *, exit_code: int | None = None) -> None:
    """把 UI / 队列作业投影进与 agent CLI 共用的进度信标。

    局部 import 避开模块环：`beacon` 复用本模块的 `service_dir`，而作业执行器
    只在真正开始/结束时才需要它。上传入口的 stage0.remove 在建书前 `sid=None`，
    没有可归属的书级进度，仍只记作业账。
    """
    if not sid:
        return
    from novel.orchestrator import beacon

    beacon.append(base, ProgressEvent(
        ts=int(time.time()), sid=sid, node=node, phase=phase,
        exit_code=exit_code))


def run(base: str | os.PathLike[str], node: str, sid: str | None, flags: Sequence[str] = (), *,
        mode: str | None = None, follow_check: bool = True,
        spawn: Spawn = default_spawn, registry: JobRegistry = REGISTRY,
        steps: Sequence[Step] | None = None,
        job_id: str | None = None) -> JobRecord:
    """跑一个作业（**同步**）：占键 → 记 `started` → 逐步起子进程 → 记 `ended` → 还键。

    ⚠⚠ **拿到锁之后到真跑起来之前，任何一步抛出去都要把锁还回去**——
    不还的话，这本书就被一个**从未存在过的作业**永久锁死
    （与 `sid=None` 那个 bug 同一种形状：拿到了不还，只是触发条件更少见）。
    ⇒ 整段包在 `finally` 里，而不是靠「记得在每个失败分支上还一次」。

    ⚠ 任一步非零即停（`R-13.4` 在单个作业内的样子）：后面那些步一步都不跑。
    """
    _refuse_if_agent_running(sid)
    plan = list(steps) if steps is not None else build_steps(
        node, sid, flags, base=base, mode=mode, follow_check=follow_check)
    key = active_key(sid, node, mode)
    job_id = job_id or new_job_id()
    if any(event.job_id == job_id for event in read_events(base)):
        raise JobConflict(f'job_id={job_id!r} already exists')
    registry.acquire(key, job_id)
    try:
        start_event = JobEvent(
            job_id=job_id, kind='started', ts=int(time.time()),
            start=plan_start(node, sid, flags, base=base, mode=mode, steps=plan))
        append_event(base, start_event)
        _append_progress(base, sid, node, 'start')
        try:
            rc = _run_steps(base, job_id, plan, spawn)
        except BaseException:
            # 同步入口也必须闭合事件与进度轨迹。否则一次 spawn 异常会让 UI 永久
            # 停在 running，作业账也只能等服务重启后猜成 unknown。
            append_event(base, JobEvent(
                job_id=job_id, kind='ended', ts=int(time.time()), exit_code=CRASH))
            _append_progress(base, sid, node, 'end', exit_code=CRASH)
            raise
        append_event(base, JobEvent(
            job_id=job_id, kind='ended', ts=int(time.time()), exit_code=rc))
        _append_progress(base, sid, node, 'end', exit_code=rc)
        return next(r for r in fold(read_events(base)) if r.job_id == job_id)
    finally:
        registry.release(key, job_id)


def wait(job_id: str, timeout: float | None = None) -> bool:
    with _THREADS_LOCK:
        thread = _THREADS.get(job_id)
    if thread is None:
        return True
    thread.join(timeout=timeout)
    done = not thread.is_alive()
    if done:
        with _THREADS_LOCK:
            if _THREADS.get(job_id) is thread:
                _THREADS.pop(job_id, None)
    return done



def start(
    base: str | os.PathLike[str],
    node: str,
    sid: str | None,
    flags: Sequence[str] = (),
    *,
    mode: str | None = None,
    follow_check: bool = True,
    spawn: Spawn = default_spawn,
    registry: JobRegistry = REGISTRY,
    steps: Sequence[Step] | None = None,
    prepare: Callable[[], None] | None = None,
    job_id: str | None = None,
) -> JobRecord:
    # 无人显式 wait 的 Web 作业在下一次启动时回收；正在 wait 的作业要保留到 join。
    with _THREADS_LOCK:
        for finished_id, finished in list(_THREADS.items()):
            if not finished.is_alive():
                _THREADS.pop(finished_id, None)
    _refuse_if_agent_running(sid)
    plan = list(steps) if steps is not None else build_steps(
        node, sid, flags, base=base, mode=mode, follow_check=follow_check)
    key = active_key(sid, node, mode)
    job_id = job_id or new_job_id()
    if any(event.job_id == job_id for event in read_events(base)):
        raise JobConflict(f'job_id={job_id!r} already exists')
    registry.acquire(key, job_id)
    try:
        if prepare is not None:
            prepare()
        start_event = JobEvent(
            job_id=job_id, kind='started', ts=int(time.time()),
            start=plan_start(node, sid, flags, base=base, mode=mode, steps=plan))
        append_event(base, start_event)
        _append_progress(base, sid, node, 'start')
    except Exception:
        registry.release(key, job_id)
        raise

    def worker() -> None:
        rc = CRASH
        try:
            rc = _run_steps(base, job_id, plan, spawn)
        except BaseException:
            error_log = log_path(base, job_id)
            error_log.parent.mkdir(parents=True, exist_ok=True)
            with open(error_log, 'ab') as log:
                log.write(traceback.format_exc().encode('utf-8'))
        finally:
            try:
                append_event(base, JobEvent(
                    job_id=job_id, kind='ended', ts=int(time.time()),
                    exit_code=rc))
                _append_progress(base, sid, node, 'end', exit_code=rc)
            finally:
                registry.release(key, job_id)
                # 留给 `wait()` 真正 join 后再移除。worker 自己先 pop 会制造一个
                # 「表里没有但线程还没完全返回」的窗口，Windows 清理日志会撞占用。

    thread = threading.Thread(
        target=worker, name=f'novel-job-{job_id}', daemon=True)
    with _THREADS_LOCK:
        _THREADS[job_id] = thread
    try:
        thread.start()
    except Exception:
        with _THREADS_LOCK:
            _THREADS.pop(job_id, None)
        registry.release(key, job_id)
        append_event(base, JobEvent(
            job_id=job_id, kind='ended', ts=int(time.time()), exit_code=CRASH))
        _append_progress(base, sid, node, 'end', exit_code=CRASH)
        raise
    return next(
        record for record in fold(read_events(base), live=registry.live())
        if record.job_id == job_id)

def records(base: str | os.PathLike[str], *,
            registry: JobRegistry = REGISTRY) -> list[JobRecord]:
    """全部作业记录，新的在前。"""
    return fold(read_events(base), live=registry.live())
