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

from contracts.invariants import LEGACY_STAGE5_MODES
from contracts.schema.orchestration import (
    LegacyExecutorBackend, LegacyLlmProfile, ExecutorBackend,
    JobEvent, JobRecord, JobStart, ProgressEvent, Step,
)
from novel import prov, run_identity
from novel.appendlog import append_line
from novel import exit_codes as X
from novel.exit_codes import CRASH
from novel.errors import ApprovalRequired, TerminalState
from novel.orchestrator.nodes import (
    build_steps, child_env, resolve_executor_backend, resolve_executor_egress,
    shell_line,
)
from novel.paths import SERVICE_DIRNAME, service_dir

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

def active_key(sid: str | None, node: str) -> str:
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

    Stage5 v2 不再有并发写作臂；所有作业均以书为互斥单元。
    旧 JSONL 的臂字段只在 ``_decode_event`` 中被消化，不进入锁接口。
    """
    key = sid or '_global'
    return key


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
                    '同一本书的产物变更必须串行')
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
#: 「API 起作业」必须共享同书写互斥，避免覆盖产物与审计。
#: ⚠ 一本书一把（键见 `agent_lock_key`），所以这张表里**可以有多条**——
#: 条数上限由 `agent.MAX_CONCURRENT` 那道并发闸管，不由这张表管。
AGENT_REGISTRY = JobRegistry()

#: 作业表与 Commander 表的**跨表协调锁**。
#:
#: 两张 ``JobRegistry`` 各自的 ``_lock`` 只能保证「一张表里」的 acquire 原子；
#: 它保证不了下面这组跨表事务：
#:
#:   1. jobs.run/start：确认没有 Commander writer → 占作业 writer；
#:   2. agent.start：确认没有作业 writer → 占 Commander writer。
#:
#: 若两边各自先查、再进各自的表，两个线程可以同时看见空表，然后各占一张，
#: 同一本书便真的有两个写者。这里不承担整段运行期互斥；它只把「双方检查 +
#: 一方占锁」收进同一个很短的临界区。运行期仍由拿到的书级 registry entry 守住，
#: 因此不同书不被这把协调锁串行。
_WRITER_COORDINATION_LOCK = threading.Lock()

_THREADS_LOCK = threading.Lock()
_THREADS: dict[str, threading.Thread] = {}


#: agent 代跑互斥键的前缀。**唯一落点在这里**，不在 `agent.py`——
#: 表（`AGENT_REGISTRY`）住在本模块，键的形状就该跟着表走。
#: ⚠ 两边各写一遍这个字符串，就是 `active_key` 那个真 bug 的同款形状：
#: 改了一边不报错，它只在边角上悄悄换一个答案。
AGENT_LOCK_PREFIX = 'agent-run'


def agent_lock_key(sid: str) -> str:
    """一本书在 agent 互斥表里的键。**一本书一把锁**，不是全局一把。

    同书写者共享产物、审批和审计，必须互斥；异书各自拥有目录与运行上下文，
    可以并行。曾经不带 sid 的全局锁错误扩大了保护范围，见 `R-13.7`。

    ⚠ 「同时最多几个」是**另一件事**，不归锁管——它归
    `novel/orchestrator/agent.py::MAX_CONCURRENT` 那道并发闸。
    """
    return f'{AGENT_LOCK_PREFIX}:{sid}'


def _refuse_if_unresolved_admission(base: str | os.PathLike[str],
                                    sid: str | None) -> None:
    """盘上仍可能有孤儿 writer 时关闭式拒绝；调用方须持协调锁。"""
    # 内存 AGENT_REGISTRY 会随服务重启清空；durable admission 不会。旧父 run
    # 仍 open 或仍有 active claim 时，孤儿子进程依然可能迟到写盘。新写手必须
    # 先从盘上关闭式拒绝，不能把“服务刚重启”误当成“没有 agent”。局部 import
    # 避免 admission 为复用 service_dir 反向 import 本模块时形成模块加载环。
    from novel.orchestrator import admission
    target = admission.admission_path(base)
    target_lock = target.with_name(target.name + '.lock')
    if not target.is_file() and not target_lock.is_file():
        # 同进程 agent.start 与本函数共用 _WRITER_COORDINATION_LOCK，且 agent 在
        # open_gate 前已占 AGENT_REGISTRY；没有 durable surface 时可直接查内存表，
        # 不为每个普通 API/telemetry 写入口凭空制造 admission/beacon lock 文件。
        return
    # ``active_key`` 把 None/'' 都归到同一 ``_global``。durable 查询必须用
    # 同一把尺子；把空串原样传入会错误地变成「只查 sid=''」而不是全局扫描。
    unresolved = admission.unresolved(base, sid or None)
    if unresolved:
        runs = ', '.join(item.parent_run_id for item in unresolved[:3])
        suffix = '' if len(unresolved) <= 3 else f' 等 {len(unresolved)} 个'
        raise JobConflict(
            f'盘上仍有未闭合 Commander admission（{runs}{suffix}）；'
            '服务重启不能证明孤儿执行已停止，须显式核实并 reconcile 后再写盘')


def _refuse_if_agent_running_locked(
        base: str | os.PathLike[str], sid: str | None, *,
        agent_registry: JobRegistry = AGENT_REGISTRY) -> None:
    """``_WRITER_COORDINATION_LOCK`` 内检查 durable / in-memory Commander。"""
    _refuse_if_unresolved_admission(base, sid)
    if not sid:
        if agent_registry.live():
            raise JobConflict(
                '有 agent 代跑正在跑——摄入要推导目录名，而 agent 可能正在建目录，'
                '等它结束（或先熄火）再上传')
        return
    holder = agent_registry.holder(agent_lock_key(sid))
    if holder is not None:
        raise JobConflict(
            f'这本书有 agent 代跑正在跑（{holder}）——它会自己起 CLI 子进程写盘，'
            f'等它结束（或先熄火）再起作业')


def _refuse_if_agent_running(base: str | os.PathLike[str],
                             sid: str | None) -> None:
    """**这本书**上有 agent 代跑活着时，它的 API 作业不许起（反向互斥的唯一落点）。

    ⚠ 从「有任何 agent 在跑就全拒」收窄成「这本书上有才拒」：agent 放开并发之后，
    前者会让 A 书的 agent 把 B 书的每一个节点作业一起挡死——而两者写的
    根本不是同一片盘，那道拒绝没有任何东西在保护。

    ⚠ **`sid is None` 仍然一律拒**（摄入那条路，`active_key` 把它归 `'_global'`）：
    上传要推导目录名，而任何一个 agent 都可能正在建目录。这一格宁可严。
    """
    with _WRITER_COORDINATION_LOCK:
        _refuse_if_agent_running_locked(base, sid)


def acquire_job_writer(
        base: str | os.PathLike[str], sid: str | None, node: str,
        writer_id: str, *, registry: JobRegistry = REGISTRY,
        agent_registry: JobRegistry = AGENT_REGISTRY) -> str:
    """原子完成「没有 Commander writer + 占作业 writer」，返回唯一互斥键。

    ``sid=None`` 是摄入的 ``_global`` writer：它与任意一本书的 Commander 都冲突。
    这一格不能只靠 ``JobRegistry`` 的前缀规则，因为 ``_global`` 刻意不是某个 sid
    的父键；跨表语义只在这个协调口实现一次。
    """
    key = active_key(sid, node)
    with _WRITER_COORDINATION_LOCK:
        _refuse_if_agent_running_locked(
            base, sid, agent_registry=agent_registry)
        registry.acquire(key, writer_id)
    return key


def acquire_agent_writer(
        base: str | os.PathLike[str], sid: str, run_id: str, *,
        admit: Callable[[str, str, JobRegistry], None],
        registry: JobRegistry = AGENT_REGISTRY,
        job_registry: JobRegistry = REGISTRY) -> str:
    """原子完成「没有作业 writer + 占 Commander writer」。

    ``admit`` 是 agent 层已有的「并发名额 + registry.acquire」唯一落点；让它在
    同一协调临界区里执行，既不复制 ``MAX_CONCURRENT``，也不留下跨表竞态。
    ``_global`` 摄入会挡住任意 sid 的 Commander，反方向由
    :func:`acquire_job_writer` 对称守住。
    """
    key = agent_lock_key(sid)
    with _WRITER_COORDINATION_LOCK:
        _refuse_if_unresolved_admission(base, sid)
        holder = job_registry.holder('_global')
        if holder is None:
            holder = job_registry.conflicting_holder(active_key(sid, 'agent.run'))
        if holder is not None:
            raise JobConflict(
                f'这本书已有 API 维护/节点作业在跑（{holder}）——等它结束再拉 agent')
        admit(key, run_id, registry)
    return key


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
            out.append(_decode_event(json.loads(line)))
        except Exception:                       # noqa: BLE001
            if i == len(raw) - 1:
                break                           # 被 kill 截在半路，允许
            raise
    return out


def _decode_event(payload: object) -> JobEvent:
    """把 append-only v1 记录投影成现役公共模型。

    ``mode`` 不是 ``JobStart`` 的字段；只有磁盘上旧
    ``stage5.write`` 的 deep/shallow 值可在这一个私有入口被剥离。
    新值、其他节点或其他层级出现该键仍关闭式拒读。
    投影后 API/OpenAPI 无 mode，作业记录本身也没有重放入口。
    """
    if not isinstance(payload, dict):
        return JobEvent.model_validate(payload)
    data = dict(payload)
    start = data.get('start')
    if isinstance(start, dict) and 'mode' in start:
        projected = dict(start)
        legacy_mode = projected.pop('mode')
        if legacy_mode is not None and not (
            projected.get('node') == 'stage5.write'
            and legacy_mode in LEGACY_STAGE5_MODES
        ):
            raise ValueError(
                'jobs.jsonl 的 mode 只允许旧 stage5.write deep/shallow')
        if _has_retired_stage5_selector(projected, legacy_mode):
            projected = _redact_retired_stage5_start(projected)
        data['start'] = projected
    elif isinstance(start, dict) and _has_retired_stage5_selector(start, None):
        data['start'] = _redact_retired_stage5_start(start)
    return JobEvent.model_validate(data)


def _has_retired_stage5_selector(start: dict, legacy_mode: object) -> bool:
    if start.get('node') != 'stage5.write':
        return False
    tokens = list(start.get('flags') or [])
    for step in start.get('steps') or []:
        if isinstance(step, dict):
            tokens.extend(step.get('argv') or [])
    return legacy_mode in LEGACY_STAGE5_MODES or bool(
        {'--mode', '--deep', '--shallow-rest'} & set(tokens))


def _redact_retired_stage5_start(start: dict) -> dict:
    """精确命令留在 append-only 原记录；公共投影不发布已退役入口。"""
    projected = dict(start)
    projected['flags'] = []
    steps: list[object] = []
    for raw_step in projected.get('steps') or []:
        if not isinstance(raw_step, dict):
            steps.append(raw_step)
            continue
        step = dict(raw_step)
        step['argv'] = ['legacy_incompatible']
        step['label'] = '旧 Stage5 作业（命令已退役）'
        step['writes'] = (
            '旧 Stage5 产物（只读历史证据）'
            if step.get('writes') else '')
        step['note'] = '原命令仅保留在 append-only 原记录，公共面不可重放'
        steps.append(step)
    projected['steps'] = steps
    return projected


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

#: `landed_red` 的人读处置：它不是「命令没跑成」，而是「正式结果已落盘、
#: 结论不通过」。多数是报告硬红，也可以是 Stage4B typed `infeasible`；
#: 与 `failed` 分态后仍保留这句人读指引。
LANDED_RED_NOTE = (
    '正式结果**已经落盘**，但结论不通过（exit 1）。'
    '⚠ 这不是「没跑成」：该做的是打开对应层的屏查看负结论并按'
    '它给出的恢复路裁决，不是把它当成进程失败盲目重跑')


def _default_note(state: str, code: int | None) -> str | None:
    if state == 'unknown':
        return RECONCILE_NOTE
    if state == 'landed_red':
        return LANDED_RED_NOTE
    return None


def reconcile(base: str | os.PathLike[str], *,
              registry: JobRegistry = REGISTRY,
              skip_sids: Iterable[str] = ()) -> list[str]:
    """服务启动时调一次：上次没跑完就退出的作业，一律改判 **`unknown`**。

    **不是 `ok` 也不是 `failed`** —— 重启前它是死是活我们真的不知道，
    而「缺失记作不知道」是 `Q-2` 的原话。写成 `ok` 就是凭空发一张通行证。

    ⚠⚠ **改判是追加一条 `reconciled` 事件，不是回去改 `started` 那一行。**
    回去改就毁掉了 append-only 的全部意义，而那正是「被 kill 不损坏」这条性质的来源。
    """
    skipped = frozenset(skip_sids)
    live = registry.live()
    changed = []
    for rec in fold(read_events(base), live=live):
        # queue startup 遇到 durable unresolved Commander 时，不得替对应 lane
        # 读盘推断或追加恢复状态。sid=None 是全局写手；任一书仍 unresolved 时
        # 同样保守跳过，避免启动恢复把全局控制面先行推进。
        if skipped and (rec.start.sid is None or rec.start.sid in skipped):
            continue
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
               base: str | os.PathLike[str],
               executor_backend: ExecutorBackend | None = None,
               executor_profile: LegacyLlmProfile | None = None,
               egress_authorized: bool | None = None,
               follow_check: bool = True,
               placeholders: bool = False,
               steps: Sequence[Step] | None = None,
               run_id: str | None = None,
               recovered_from_job_id: str | None = None) -> JobStart:
    """一个作业的**全部静态字段**。纯函数：不占键、不落事件、不起进程。

    ⚠⚠ **`R-11.8` 在这里从一句纪律变成字面事实。** `run()` 与
    `POST /api/jobs/preview` 调的是**同一个函数**，于是「界面上摊开给人看的那条命令」
    与「真的会被 `Popen` 的那条」是同一个对象——不是两份长得像的。
    原型那条裂缝（前端用 `argv.slice(4)` 硬编码切掉解释器前缀来渲染，
    `build_argv` 前缀长度一变就静默错位）在这里连发生的地方都没有。

    ⚠ `paid` / `writes` 在这里逐步折叠一次，而 `JobStart` 的后置条件会**反过来断言**
    它等于逐步折出来的——两处一致才构造得出来（`R-11.7`：贵贱只有一处算）。
    """
    from novel.orchestrator import retired_formats
    if executor_profile is not None:
        old_backend = LegacyExecutorBackend(profile=executor_profile)
        if executor_backend is None:
            executor_backend = old_backend
        elif executor_backend != old_backend:
            raise ValueError('executor_backend 与旧 executor_profile 不一致')
    retired_formats.refuse_agent_backend('作业执行', executor_backend)
    plan = list(steps) if steps is not None else build_steps(
        node, sid, flags, base=base, follow_check=follow_check,
        executor_backend=executor_backend,
        egress_authorized=egress_authorized,
        placeholders=placeholders)
    step_backends = [
        step.executor_backend for step in plan
        if step.executor_backend is not None
    ]
    if executor_backend is None and step_backends:
        executor_backend = step_backends[0]
    if executor_backend is not None:
        retired_formats.refuse_agent_backend('作业执行', executor_backend)
        executor_backend = resolve_executor_backend(
            node, flags, executor_backend)
        if any(backend != executor_backend for backend in step_backends):
            raise ValueError('传入 executor_backend 与冻结 steps[] 不一致')
    profiled_steps = [
        step for step in plan if step.executor_backend is not None
    ]
    step_egress = [step.egress_authorized for step in profiled_steps]
    if egress_authorized is None:
        explicit_step_egress = [value for value in step_egress if value is not None]
        if explicit_step_egress:
            egress_authorized = explicit_step_egress[0]
    egress_authorized = resolve_executor_egress(
        executor_backend, egress_authorized)
    if executor_backend is not None:
        if egress_authorized is None:
            if any(value is not None for value in step_egress):
                raise ValueError('JobStart 与冻结 steps[] 的正文外发授权不一致')
        elif any(value is not egress_authorized for value in step_egress):
            raise ValueError('传入正文外发授权与付费 executor steps[] 不一致')
    # TODO-45 批次 3（R-13.3）：run 身份与 execution 身份是两根轴。
    # `run_id` 是业务 run（首跑/恢复/聚合不变，账本、session、checkpoint、
    # run 目录都以它为键）；`job_id` 是本次进程 attempt。预览形（run_id=None）
    # 诚实表示「尚未绑定执行身份」——`R-11.8` 的 preview/start 同构是**命令**，
    # 不是执行身份；执行边界（`run`/`start` 落 started 事件之前）必须解析出
    # run_id：显式传入优先（恢复续跑带原 run），否则新造，绝不从 job_id 倒推。
    return JobStart(
        sid=sid, node=node, flags=list(flags), steps=plan,
        python=sys.version, code=prov.code_version(),
        paid=any(s.paid for s in plan),
        writes=any(bool(s.writes) for s in plan),
        executor_backend=executor_backend,
        egress_authorized=egress_authorized,
        run_id=run_identity.check_run_id(run_id) if run_id else None,
        recovered_from_job_id=recovered_from_job_id)


def _resolve_run_identity(start_meta: JobStart) -> JobStart:
    """执行边界的 run 身份解析：None ⇒ 新造；已带（恢复续跑）原样保留。"""
    if start_meta.run_id:
        return start_meta
    return start_meta.model_copy(
        update={'run_id': run_identity.new_run_id()})


def _require_executor_egress(start_meta: JobStart) -> None:
    """真执行前的独立正文外发闸；preview 仍可冻结 false 命令。"""
    if (start_meta.executor_backend is not None
            and start_meta.egress_authorized is not True):
        raise ApprovalRequired(
            'executor 会向所选 provider 外发小说正文；正文外发授权必须显式给 '
            'egress_authorized=true（手敲 CLI 用 --allow-egress）。'
            '这与 --yes 付费确认是两份独立授权')


def _run_steps(base: str | os.PathLike[str], job_id: str,
               plan: Sequence[Step], spawn: Spawn, *,
               run_id: str) -> int:
    p = log_path(base, job_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    rc = 0
    # 一个 job 里的 producer + follow-check 是同一次可恢复运行；
    # 两步必须继承同一 run_id（业务身份）与同一 job_id（进程 attempt），
    # 且不污染长驻服务进程的环境。
    env = run_identity.env_with_run_id(run_id, child_env())
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
                     phase: str, *, run_id: str,
                     exit_code: int | None = None,
                     execution_id: str | None = None) -> None:
    """把 UI / 队列作业投影进与 agent CLI 共用的进度信标。

    局部 import 避开模块环：`beacon` 复用本模块的 `service_dir`，而作业执行器
    只在真正开始/结束时才需要它。上传入口的 stage0.remove 在建书前 `sid=None`，
    没有可归属的书级进度，仍只记作业账。
    """
    if not sid:
        return
    from novel.orchestrator import beacon

    beacon.append(base, ProgressEvent(
        ts=int(time.time()), sid=sid, run_id=run_id, node=node, phase=phase,
        exit_code=exit_code, execution_id=execution_id))


def run(base: str | os.PathLike[str], node: str, sid: str | None, flags: Sequence[str] = (), *,
        executor_backend: ExecutorBackend | None = None,
        executor_profile: LegacyLlmProfile | None = None,
        egress_authorized: bool | None = None,
        follow_check: bool = True,
        spawn: Spawn = default_spawn, registry: JobRegistry = REGISTRY,
        steps: Sequence[Step] | None = None,
        job_id: str | None = None,
        run_id: str | None = None,
        recovered_from_job_id: str | None = None) -> JobRecord:
    """跑一个作业（**同步**）：占键 → 记 `started` → 逐步起子进程 → 记 `ended` → 还键。

    ⚠⚠ **拿到锁之后到真跑起来之前，任何一步抛出去都要把锁还回去**——
    不还的话，这本书就被一个**从未存在过的作业**永久锁死
    （与 `sid=None` 那个 bug 同一种形状：拿到了不还，只是触发条件更少见）。
    ⇒ 整段包在 `finally` 里，而不是靠「记得在每个失败分支上还一次」。

    ⚠ 任一步非零即停（`R-13.4` 在单个作业内的样子）：后面那些步一步都不跑。
    """
    from novel.orchestrator import retired_formats
    if executor_profile is not None and executor_backend is None:
        executor_backend = LegacyExecutorBackend(profile=executor_profile)
    retired_formats.refuse_agent_backend('作业执行', executor_backend)
    plan = list(steps) if steps is not None else build_steps(
        node, sid, flags, base=base, follow_check=follow_check,
        executor_backend=executor_backend,
        egress_authorized=egress_authorized)
    start_meta = _resolve_run_identity(plan_start(
        node, sid, flags, base=base, steps=plan,
        executor_backend=executor_backend,
        executor_profile=executor_profile,
        egress_authorized=egress_authorized,
        run_id=run_id, recovered_from_job_id=recovered_from_job_id))
    _require_executor_egress(start_meta)
    if start_meta.writes:
        from novel.work_units import check_workflow_write
        check_workflow_write(base)
    job_id = job_id or new_job_id()
    if any(event.job_id == job_id for event in read_events(base)):
        raise JobConflict(f'job_id={job_id!r} already exists')
    key = acquire_job_writer(
        base, sid, node, job_id, registry=registry)
    try:
        start_event = JobEvent(
            job_id=job_id, kind='started', ts=int(time.time()),
            start=start_meta)
        append_event(base, start_event)
        _append_progress(base, sid, node, 'start', run_id=start_meta.run_id,
                         execution_id=job_id)
        try:
            rc = _run_steps(base, job_id, plan, spawn,
                            run_id=start_meta.run_id)
        except BaseException:
            # 同步入口也必须闭合事件与进度轨迹。否则一次 spawn 异常会让 UI 永久
            # 停在 running，作业账也只能等服务重启后猜成 unknown。
            append_event(base, JobEvent(
                job_id=job_id, kind='ended', ts=int(time.time()), exit_code=CRASH))
            _append_progress(
                base, sid, node, 'end', run_id=start_meta.run_id,
                exit_code=CRASH, execution_id=job_id)
            raise
        append_event(base, JobEvent(
            job_id=job_id, kind='ended', ts=int(time.time()), exit_code=rc))
        _append_progress(base, sid, node, 'end', run_id=start_meta.run_id,
                         exit_code=rc, execution_id=job_id)
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
    executor_backend: ExecutorBackend | None = None,
    executor_profile: LegacyLlmProfile | None = None,
    egress_authorized: bool | None = None,
    follow_check: bool = True,
    spawn: Spawn = default_spawn,
    registry: JobRegistry = REGISTRY,
    steps: Sequence[Step] | None = None,
    prepare: Callable[[], None] | None = None,
    job_id: str | None = None,
    run_id: str | None = None,
    recovered_from_job_id: str | None = None,
) -> JobRecord:
    # 无人显式 wait 的 Web 作业在下一次启动时回收；正在 wait 的作业要保留到 join。
    with _THREADS_LOCK:
        for finished_id, finished in list(_THREADS.items()):
            if not finished.is_alive():
                _THREADS.pop(finished_id, None)
    from novel.orchestrator import retired_formats
    if executor_profile is not None and executor_backend is None:
        executor_backend = LegacyExecutorBackend(profile=executor_profile)
    retired_formats.refuse_agent_backend('作业执行', executor_backend)
    plan = list(steps) if steps is not None else build_steps(
        node, sid, flags, base=base, follow_check=follow_check,
        executor_backend=executor_backend,
        egress_authorized=egress_authorized)
    start_meta = _resolve_run_identity(plan_start(
        node, sid, flags, base=base, steps=plan,
        executor_backend=executor_backend,
        executor_profile=executor_profile,
        egress_authorized=egress_authorized,
        run_id=run_id, recovered_from_job_id=recovered_from_job_id))
    _require_executor_egress(start_meta)
    if start_meta.writes:
        from novel.work_units import check_workflow_write
        check_workflow_write(base)
    job_id = job_id or new_job_id()
    if any(event.job_id == job_id for event in read_events(base)):
        raise JobConflict(f'job_id={job_id!r} already exists')
    key = acquire_job_writer(
        base, sid, node, job_id, registry=registry)
    try:
        if prepare is not None:
            prepare()
        start_event = JobEvent(
            job_id=job_id, kind='started', ts=int(time.time()),
            start=start_meta)
        append_event(base, start_event)
        _append_progress(base, sid, node, 'start', run_id=start_meta.run_id,
                         execution_id=job_id)
    except Exception:
        registry.release(key, job_id)
        raise

    def worker() -> None:
        rc = CRASH
        try:
            rc = _run_steps(base, job_id, plan, spawn,
                            run_id=start_meta.run_id)
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
                _append_progress(
                    base, sid, node, 'end', run_id=start_meta.run_id,
                    exit_code=rc, execution_id=job_id)
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
        _append_progress(
            base, sid, node, 'end', run_id=start_meta.run_id,
            exit_code=CRASH, execution_id=job_id)
        raise
    return next(
        record for record in fold(read_events(base), live=registry.live())
        if record.job_id == job_id)

def records(base: str | os.PathLike[str], *,
            registry: JobRegistry = REGISTRY) -> list[JobRecord]:
    """全部作业记录，新的在前。"""
    return fold(read_events(base), live=registry.live())
