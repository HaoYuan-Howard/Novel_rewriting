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
from pathlib import Path

from contracts.invariants import BEACON_STATES, NODES, PIPELINE_STEPS
from contracts.schema.orchestration import (
    BeaconTailRepairRecord, ProgressEvent, RunStepView,
)
from novel import run_identity
from novel.appendlog import (
    append_line_under_process_lock, append_transaction,
)
from novel.cli import exit_codes as X
from novel.orchestrator import append_tail_repair
from novel.orchestrator import nodes as N
from novel.orchestrator.jobs import service_dir

#: 文件名只有这一处。⚠ 与 `jobs.jsonl` 分文件：两份记录的写入方、容错语义、
#: 消费方全不同（见模块 docstring），合一份就要在折叠规则里互相打招呼。
BEACON_FILENAME = 'agent_progress.jsonl'
TAIL_REPAIR_DIRNAME = 'agent_progress_tail_repairs'

#: `events_tail` 的长度——人读时间线只要最近这一段，全量永远在盘上。
TAIL = 20


def beacon_path(base: str | os.PathLike[str]):
    return service_dir(base) / BEACON_FILENAME


def tail_repair_dir(base: str | os.PathLike[str]) -> Path:
    return service_dir(base) / TAIL_REPAIR_DIRNAME


def _tail_repair_spec(
        base: str | os.PathLike[str]) -> append_tail_repair.TailRepairSpec:
    return append_tail_repair.TailRepairSpec(
        protocol='novel-agent-progress-tail-repair/v1',
        log_filename=BEACON_FILENAME,
        filename_field='beacon_filename',
        label='进度 beacon 账',
        repair_dir=tail_repair_dir(base),
        record_type=BeaconTailRepairRecord,
    )


BeaconTailRepairSubject = append_tail_repair.TailRepairSubject


def require_appendable_under_transaction(base: str | os.PathLike[str]) -> None:
    """调用方已持 beacon transaction 时，拒绝 non-LF 未提交尾。"""
    append_tail_repair.require_appendable(
        beacon_path(base), label='进度 beacon 账')


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
    path = beacon_path(base)
    rendered = json.dumps(event.model_dump(mode='json'), ensure_ascii=False)
    with append_transaction(path):
        require_appendable_under_transaction(base)
        append_line_under_process_lock(path, rendered)


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
    # LF 是提交标记。``splitlines()`` 会把“完整 JSON 但尚未写出 LF”与已提交
    # 行压成同一种字符串；keepends=True 才能保证最后 non-LF 永不取得事件身份。
    raw = p.read_bytes().splitlines(keepends=True)
    out: list[ProgressEvent] = []
    for i, raw_line in enumerate(raw):
        committed = raw_line.endswith(b'\n')
        if not committed:
            if i == len(raw) - 1:
                break
            raise ValueError(
                f'{p} 第 {i + 1} 行不是 LF 提交的完整 beacon 事件')
        payload = raw_line[:-1]
        if payload.endswith(b'\r'):
            payload = payload[:-1]
        line = payload.decode('utf-8', errors='strict').strip()
        if not line:
            continue
        # 已带 LF 的坏 JSON 是 committed corruption，不因它恰好在末行而吞掉。
        out.append(ProgressEvent.model_validate(json.loads(line)))
    return out


def read_tail_repair_records(
        base: str | os.PathLike[str],
) -> tuple[BeaconTailRepairRecord, ...]:
    return append_tail_repair.read_records(_tail_repair_spec(base))


def tail_repair_subject(
        base: str | os.PathLike[str]) -> BeaconTailRepairSubject:
    """只读给出 committed-prefix/tail 双 hash 的当前恢复主体。"""
    return append_tail_repair.repair_subject(
        beacon_path(base), _tail_repair_spec(base))


def repair_tail(
    base: str | os.PathLike[str], *, actor: str, reason: str,
    subject_sha256: str,
) -> BeaconTailRepairSubject:
    """原样隔离 non-LF 尾并以 immutable 两相记录确认 fsync 截断。"""
    return append_tail_repair.repair_tail(
        beacon_path(base), _tail_repair_spec(base), actor=actor,
        reason=reason, subject_sha256=subject_sha256)


def open_executions(
        base: str | os.PathLike[str], parent_run_id: str,
) -> tuple[ProgressEvent, ...]:
    """返回一个父 run 下尚未 ``end`` 的精确进程 attempt。

    这是 Commander 生命周期屏障使用的唯一折叠口。它按
    ``(run_id, execution_id)`` 配对，不拿 node 的最后一行猜：同一节点可以恢复多次，
    也可以先后产生多个进程 attempt。当前父 run 若出现缺 execution 身份的事件、
    孤儿 end、重复 start/end 或 start/end 的 sid/node 漂移，均关闭式报错；旧历史
    仍能由 ``fold`` 展示，但不能拿来证明一个正在结束的 Commander 已经没有子进程。
    """
    checked_run_id = run_identity.check_run_id(parent_run_id)
    starts: dict[str, ProgressEvent] = {}
    closed: set[str] = set()
    order: list[str] = []
    for event in read_events(base):
        if event.run_id != checked_run_id:
            continue
        execution_id = event.execution_id
        if execution_id is None:
            raise ValueError(
                f'parent run {checked_run_id!r} 的进度事件缺 execution_id，'
                '不能精确证明子进程已经闭合')
        if event.phase == 'start':
            if execution_id in starts:
                raise ValueError(
                    f'execution_id {execution_id!r} 重复 start，拒绝猜哪一轮已结束')
            starts[execution_id] = event
            order.append(execution_id)
            continue
        start = starts.get(execution_id)
        if start is None:
            raise ValueError(
                f'execution_id {execution_id!r} 的 end 没有同父 run start')
        if execution_id in closed:
            raise ValueError(f'execution_id {execution_id!r} 重复 end')
        if (event.sid, event.node) != (start.sid, start.node):
            raise ValueError(
                f'execution_id {execution_id!r} 的 start/end sid 或 node 漂移')
        closed.add(execution_id)
    return tuple(starts[item] for item in order if item not in closed)


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
