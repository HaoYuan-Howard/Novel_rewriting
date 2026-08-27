#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
beacon.py —— 进度信标：agent 代跑 CLI 时的**观察轨迹**。

    <base>/_service/agent_progress.jsonl   append-only，一行一个 `ProgressEvent`

【它为什么不是 `jobs.jsonl` 的一种新事件】作业记录是**服务进程拉起子进程**的账
（job_id / 日志文件 / `R-11.11` 的 reconciled 改判）；信标是 agent **直跑**
`novel.cli` 时的人为打点——没有进程可追、没有日志可续。捏在一起会让
`jobs.fold` 的「重启后 running → unknown」规则对一堆没有 job_id 的行开火。

【它与判定的距离】信标只说「哪条命令何时开始、何时以什么退出码结束」。
**exit 0 ≠ 合格**（`F-087`）；「这层过没过」的唯一判官仍是 check 报告的
badge（`R-13.6`）。本模块因此**不 import 任何判据**，折叠出来的也只是观察态。

写入方是 CLI 的 `mark` 子命令（agent 受人委托代跑），**不是服务进程**——
`R-11.3`（服务进程绝不落产物盘）不受这条影响：`_service/` 本来就是编排层的
写字台，而服务进程对这一份仍然只读。
"""
from __future__ import annotations

import json
import os
import time

from contracts.invariants import BEACON_STATES, NODES, PIPELINE_STEPS
from contracts.schema.orchestration import ProgressEvent, RunStepView
from novel import run_identity
from novel.appendlog import append_line
from novel.cli import exit_codes as X
from novel.orchestrator import nodes as N
from novel.orchestrator.jobs import service_dir

#: 文件名只有这一处。⚠ 与 `jobs.jsonl` 分文件：两份记录的写入方、容错语义、
#: 消费方全不同（见模块 docstring），合一份就要在折叠规则里互相打招呼。
BEACON_FILENAME = 'agent_progress.jsonl'

#: `events_tail` 的长度——人读时间线只要最近这一段，全量永远在盘上。
TAIL = 20


def beacon_path(base: str | os.PathLike[str]):
    return service_dir(base) / BEACON_FILENAME


def append(base: str | os.PathLike[str], event: ProgressEvent) -> None:
    """追加一行。**永不读-改-写**（理由同 `jobs.append_event`：
    被 kill 时最坏只损坏最后一行，而读的那一侧把半截行当没有）。

    ⚠⚠ **这一份的写入方是 N 个进程**，不是 N 个线程：`mark` 是 CLI 子命令，
    每个 agent 各自拉起自己的 `novel.cli mark`，而 `R-13.7` 明说不同小说可以并行。
    ⇒ 光有线程锁时，A 进程的半截行会被 B 进程紧接着写的完整行**顶到中间**，
    而 `read_events` 对**中间**的坏行是 `raise`（只容忍最后一行）——
    一次交错就让所有书的进度屏一起打不开。互斥因此在 `novel/appendlog.py`
    （线程锁 + 操作系统文件锁），全仓只有那一份实现。
    """
    # ``mark`` 显式收到的全局 --run-id 已在 event 上，以它为准；
    # agent 旧调用没有显式值时，才从根进程环境在这一个写口补齐。
    # 读旧行不走这里，因此不会把当前 run 误填给历史事件。
    if event.run_id is None:
        inherited = run_identity.inherited_run_id()
        if inherited is not None:
            event = event.model_copy(update={'run_id': inherited})
    append_line(
        beacon_path(base),
        json.dumps(event.model_dump(mode='json'), ensure_ascii=False))


def read_events(base: str | os.PathLike[str]) -> list[ProgressEvent]:
    """读回事件流。**半截的最后一行当没有，不当失败**（同 `jobs.read_events`）。

    ⚠ 同样只容忍**最后一行**——中间有坏行说明这份文件被别的东西写过
    （比如手改），那是另一回事，一起吞掉会让「记录不全」变成静默。
    ⚠ 这条「只容忍最后一行」**要靠写侧的跨进程锁才成立**：没有那把锁时，
    并发的两个 `mark` 进程能把半截行留在中间，于是这里会对一次正常的并行跑开火。
    锁在 `novel/appendlog.py`。
    """
    p = beacon_path(base)
    if not p.is_file():
        return []
    raw = p.read_bytes().decode('utf-8', errors='replace').splitlines()
    out: list[ProgressEvent] = []
    for i, line in enumerate(raw):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(ProgressEvent.model_validate(json.loads(line)))
        except Exception:                       # noqa: BLE001 —— 半截行容错，见 docstring
            if i == len(raw) - 1:
                break                           # 被 kill 截在半路，允许
            raise
    return out


def fold(events: list[ProgressEvent], *, now: int | None = None) -> list[RunStepView]:
    """事件流 → `PIPELINE_STEPS` 的当前样子。**折叠规则只有这一处。**

    同一节点取**最后**一条事件：`start` 无 `end` ⇒ `running`（`elapsed=now-ts`）；
    `end` ⇒ 按退出码 `ok` / `landed_red` / `failed`；无事件 ⇒ `pending`。
    ⚠ 修复回路里同一节点会跑多次——进度屏只关心**最近一次**，
    历史在 `events_tail` 里，不在这里折。
    ⚠ 调用方传入前应按 sid 过滤；本函数不猜「这些事件是哪些书的」。
    """
    if now is None:
        now = int(time.time())
    last: dict[str, ProgressEvent] = {}
    for e in events:
        last[e.node] = e                       # 文件序 ⇒ 后写的覆盖，无需比较 ts
    steps: list[RunStepView] = []
    for seq, node in enumerate(PIPELINE_STEPS, start=1):
        e = last.get(node)
        if e is None:
            beacon, last_exit, last_ts, elapsed = 'pending', None, None, None
        elif e.phase == 'start':
            beacon, last_exit, last_ts, elapsed = 'running', None, e.ts, now - e.ts
        else:
            beacon = ('ok' if e.exit_code == X.OK else
                      'landed_red' if X.landed_but_red(e.exit_code) else 'failed')
            last_exit, last_ts, elapsed = e.exit_code, e.ts, None
        assert beacon in BEACON_STATES          # 折叠出口只许登记过的值
        steps.append(RunStepView(
            seq=seq, node=node, label=NODES[node].label,
            paid=N.is_paid(node, ()),
            beacon=beacon, last_exit=last_exit, last_ts=last_ts, elapsed=elapsed))
    return steps
