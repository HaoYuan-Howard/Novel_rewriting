#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
queue.py —— 批量队列（`R-13`）。**一次排入多本书，人只在需要裁决的地方介入。**

【`plan()` 是纯函数】给它一批 sid 与一个目标 stage，它算出「这条链长什么样」——
**不跑、不写、不发请求**。于是「排这一批会花多少次调用、会写哪些文件、
命令逐条长什么样」可以 0 成本地摊开给人看，而那正是 `R-13.3` 要人确认的东西。

【`create()` + runner 才是真队列】确认后的整份命令快照追加进
`_service/queues.jsonl`；每本书一条 lane，不同书并行。同书首项失败后其余项记
`not_run`，人工闸门持久写 `waiting_on`。服务重启时，旧 running 先成为 unknown，
再对照 jobs 事件与产物存在性/新鲜度恢复；runner 从不临时扩张已确认的命令面。

【⚠ 队列本身不产生新的判定】`R-13.6` → `R-11.4`：**造第二个判官就是造第二个落点。**
这条**由结构保证**：本模块不许 import `novel.report`，由 `tests/test_layering.py`
的一条 AST 测试钉着。队列读的是完成度（`state.py`），而完成度读的是盘上有什么。

【为什么「停在那里」不是保守】批量的风险与单本不同：
**单本跑错人当场看得见；批量跑错是一批产物同时坏掉且没人在看。**
所以 `R-13.4` 的「任一项失败 → 停在那里」是这条能力**成立的前提**，不是一个保守选项。
"""
from __future__ import annotations

from contracts.artifacts import STAGE0_REMOVAL_REPORT, STAGE6_HOOK, STAGE7_NAME_MAP

import json
import os
import threading
import time
import traceback
import uuid
from collections.abc import Iterable, Sequence
from pathlib import Path

from contracts.invariants import NODES, STAGES
from contracts.schema.orchestration import (
    QueueCounts, QueueEntry, QueueEvent, QueueItem, QueueLaneCounts, QueuePlan,
    QueueRun, QueueStart,
)
from contracts.schema.stage0 import RemovalReport
from contracts.schema.stage6 import Hook
from contracts.schema.stage7 import NameMap
from novel import paths, prov
from novel.appendlog import append_line
from novel.cli import exit_codes as X
from novel.cli.exit_codes import CRASH
from novel.errors import ApprovalRequired, ArtifactUnreadable, QueueConflict, UpstreamMissing
from novel.orchestrator import approvals, jobs, state as st
from novel.orchestrator.nodes import build_steps, flag_value, shell_line
from novel.store import ArtifactStore

#: 人做的那几个动作。**队列永不自动排它们**——`stage6.approve` 换一条候选、
#: `--apply-edit` 改一个字，都是「人看过之后的裁决」，机器替不了。
#: ⚠ 它们进 `blocked` 而不是被跳过：被跳过的闸门在界面上与「不需要它」长得一样。
HUMAN_NODES: frozenset[str] = frozenset(
    {n for n in NODES if n.endswith('.approve')} | {'stage0.edit'})

#: ``stage0.edit`` is a human capability, not a mandatory gate.  Listing it as
#: an automatic step would make every book wait forever for an edit the user
#: may neither need nor want.
OPTIONAL_HUMAN_NODES: frozenset[str] = frozenset({'stage0.edit'})

#: 待人工清单非空时挡队列的那个理由（`INBOX_KIND` 的 `invalid`）。
WAITING_ON_INBOX = 'inbox:invalid'
WAITING_ON_DEPTH_SELECTION = 'approval:stage5-depth'
WAITING_ON_RESTART_CONFIRMATION = 'queue:restart-confirmation'
WAITING_ON_REPLAN = 'queue:plan-changed'


def _stage_index(stage: str) -> int:
    return STAGES.index(stage)


def nodes_up_to(until_stage: str) -> list[str]:
    """处理链上到 `until_stage` 为止的节点。**顺序即 `NODES` 的声明顺序。**

    ⚠ 顺序不在这里另定：`NODES` 的注释写着「顺序即处理链顺序，队列与断点续跑都按它排」。
    在这儿再排一次序就是第二个落点，而两处排序分家的样子是**队列偶尔倒着跑一层**。
    """
    if until_stage not in STAGES:
        raise ValueError(f'未知 stage {until_stage!r}，取值面是 {list(STAGES)}')
    cap = _stage_index(until_stage)
    return [n for n, spec in NODES.items() if _stage_index(spec.stage) <= cap]


def _upstream_invalid(ov, stage: str) -> list[str]:
    """**严格上游**那些层里，盘上有几份被闸门拦下的稿。

    ⚠ 只看上游不看本层：本层自己的 `.INVALID` 是**本节点该处理的事**
    （0 成本提升路 `--revalidate`），不是挡住它自己的理由。
    """
    cap = _stage_index(stage)
    return [a.rel for s in ov.stages if _stage_index(s.stage) < cap
            for a in s.artifacts if a.invalid]


def _gate_is_reachable(ov, stage: str) -> bool:
    """这道人工闸门**此刻够得着吗**——本层盘上有没有东西待人裁决。

    ⚠⚠ **这一格是实跑盘上语料改出来的，不是设计出来的。**
    第一版让人工闸门无条件挡住其后所有项，于是 `stage0.approve`（处理链第二个节点）
    把**整条链从头挡死**：盘上 stage0 一份产物都没有，那道闸根本还够不着，
    而队列却说「在等人批准 stage0」——**一句看起来精确的假话**，
    且它让队列在任何一本新书上都排不出一项。
    ⇒ 一道**还没有对象**的闸门不是「在等人」，它只是还没轮到。
    """
    s = next((x for x in ov.stages if x.stage == stage), None)
    return bool(s and any(a.exists for a in s.artifacts))


def _human_gate_satisfied(base: str | os.PathLike[str], sid: str, node: str) -> bool:
    """Read the gate's existing typed artifact; do not invent a queue verdict.

    These three fields are the same fields consumed by their downstream
    stages.  Reading them here keeps the queue from permanently blocking an
    already approved book while still leaving the actual decision to a human.
    """
    model_rel = {
        'stage0.approve': (RemovalReport, STAGE0_REMOVAL_REPORT),
        'stage6.approve': (Hook, STAGE6_HOOK),
        'stage7.approve': (NameMap, STAGE7_NAME_MAP),
    }.get(node)
    if model_rel is None:
        return False
    model, rel = model_rel
    try:
        novel_dir = paths.resolve_novel_dir(base, sid)
        store = ArtifactStore(st.readonly_ctx(novel_dir, node, prov.code_version()))
        artifact, _ = store.read_own(model, rel)
    except (UpstreamMissing, ArtifactUnreadable):
        return False
    if node == 'stage0.approve':
        # 唯一落点在 eligibility，队列不自己复述批准条件；不具执行资格
        # 在这里等于「闸门未过」——队列会继续说出在等 stage0.approve。
        from novel.stages.stage0.eligibility import is_stage0_executable
        return is_stage0_executable(artifact)
    return artifact.approved is not None


def plan_book(base: str | os.PathLike[str], sid: str, *, until_stage: str,
              follow_check: bool = True) -> tuple[list[QueueItem], list[QueueItem]]:
    """一本书的 `(要跑的, 被挡住的)`。**纯函数**（只读盘，不写、不发请求）。"""
    ov = st.overview(base, sid)
    todo: list[QueueItem] = []
    blocked: list[QueueItem] = []
    #: ⚠ **一旦挡住，其后的项一律跟着挡住**——那才叫「挡住队列」（`R-13.5`）。
    #: 只挡住那一项而让后面的照跑，等于让下游拿着一份没人签过字的上游继续走。
    stuck: str | None = None

    for node in nodes_up_to(until_stage):
        spec = NODES[node]
        why_blocked = stuck
        if why_blocked is None and node in HUMAN_NODES:
            if node in OPTIONAL_HUMAN_NODES:
                continue                             # 可选人工编辑不是流水线必经闸门
            if not _gate_is_reachable(ov, spec.stage):
                continue                             # 还没轮到它，不是「在等人」
            if _human_gate_satisfied(base, sid, node):
                continue                             # 人已经完成这道闸
            why_blocked = node                       # 在等这一步的那个人
        if why_blocked is None:
            ghosts = _upstream_invalid(ov, spec.stage)
            if ghosts:
                why_blocked = WAITING_ON_INBOX

        verdict, why = st.resume_verdict(ov, node)
        if why_blocked is None and verdict == st.RESUME_SKIP:
            continue                                 # 已存在即跳过

        mode = spec.modes[0] if spec.modes else None
        flags: list[str] = []
        steps = None
        if node == 'stage5.write':
            # The normal writing entry is one composite command.  Choosing the
            # first arm here used to turn every chapter into the expensive deep
            # route and silently discard the persisted human selection.
            mode = None
            selected = approvals.current_depth_selection(base, sid)
            if selected is not None:
                flags = [
                    '--deep', ','.join(str(value) for value in selected) or 'none',
                    '--shallow-rest',
                ]
            else:
                if why_blocked is None:
                    why_blocked = WAITING_ON_DEPTH_SELECTION
                # No person has selected the depth arm yet, therefore there is
                # no honest executable command to snapshot.  Once selected, the
                # command surface changes and the old queue requires replanning.
                steps = []

        if steps is None and why_blocked == node and node in HUMAN_NODES:
            # ⚠⚠ **人还没签字之前，这道闸没有一条「诚实的可执行命令」可以快照。**
            # `stage0.approve` / `stage6.approve` / `stage7.approve` 的 argv 必填
            # `--actor` / `--reason`（人此刻还没给出的东西）⇒ `build_steps` 会抛
            # `UnsupportedRunMode`。而这一项本来就是 `blocked`，它要回答的是
            # 「在等谁」，不是「怎么跑」。
            # ⚠ 曾经这里无条件 `build_steps`：于是一本「stage0 还没人批」的书会让
            #   **整条队列计划当场抛异常**——CLI `queue --plan-only` 退出码 2、
            #   HTTP 400，而真正的事实只是「在等人批准 stage0」。
            # ⇒ 与上面 `stage5.write` 缺深度选择时**同一处置**：留空命令面。
            #   人给出裁决之后命令面本来就会变，那时旧队列本就需要重排。
            steps = []
        if steps is None:
            steps = build_steps(
                node, sid, flags, base=base, mode=mode,
                follow_check=follow_check)

        item = QueueItem(
            sid=sid, node=node, mode=mode, flags=flags, steps=steps,
            why=(f'{verdict}：{why}' if why_blocked is None
                 else _blocked_why(why_blocked, why)),
            waiting_on=why_blocked,
        )
        (blocked if why_blocked else todo).append(item)
        if why_blocked and stuck is None:
            stuck = why_blocked
    return todo, blocked


def _blocked_why(waiting_on: str, verdict_why: str) -> str:
    if waiting_on == WAITING_ON_INBOX:
        return (
            '上游有被闸门拦下的稿要人处理（0 LLM 提升路是 '
            '`--revalidate --actor <id> --reason <why>`，同轮重判并留不可变审计）；'
            f'{verdict_why}')
    if waiting_on == WAITING_ON_DEPTH_SELECTION:
        return ('还没有持久化的深度章节选择；先在「挑深度章节」屏保存一次，'
                f'队列不会猜成全 deep 或全 shallow；{verdict_why}')
    return f'在等 `{waiting_on}`——这一步是人做的裁决，机器替不了；{verdict_why}'


def plan(base: str | os.PathLike[str], sids: Sequence[str], *, until_stage: str,
         follow_check: bool = True) -> QueuePlan:
    """一批书的计划。**顺序 = 书的顺序 × 处理链顺序。**

    ⚠ 不做「把同一层的所有书凑一批」那种重排：那会让一本书的失败
    影响到另一本书**已经跑到一半的**链，而 `R-13.4` 要的是「停在那里」，
    停在哪里必须说得清。
    """
    items: list[QueueItem] = []
    blocked: list[QueueItem] = []
    for sid in sids:
        todo, stop = plan_book(base, sid, until_stage=until_stage,
                               follow_check=follow_check)
        items += todo
        blocked += stop
    return QueuePlan(items=items, blocked=blocked,
                     paid_items=sum(1 for it in items + blocked
                                    if any(s.paid for s in it.steps)))


# ─────────────────────────────────────────────────────────────────────────────
# 一次性预先确认（`R-13.3` / `Q-10`）
# ─────────────────────────────────────────────────────────────────────────────

def confirm(p: QueuePlan, *, yes: bool) -> None:
    """花钱的项必须**一次性预先确认**——不能因为「排进队列了」就变成静默扣费。

    ⚠ 摊开的是**将要执行的那些命令**（复数），不是一句「你确定要跑吗」：
    人靠它确认参数没点错。⚠ **不显示预估花费**（`Q-10`：猜一个数写在界面上比不显示更坏）。
    """
    if yes or not p.paid_items:
        return
    lines = [f'这批计划里有 {p.paid_items} 项会花钱。将要执行的命令：']
    for it in p.items + p.blocked:
        for s in it.steps:
            if s.paid:
                lines.append(f'  $ {shell_line(s.argv)}')
    raise ApprovalRequired('\n'.join(lines) + '\n⇒ 确认无误后加 `--yes` 重跑')


# ─────────────────────────────────────────────────────────────────────────────
# 「停在那里」的计数（`R-13.4`）
# ─────────────────────────────────────────────────────────────────────────────

def fold_counts(outcomes: Iterable[bool], total: int) -> QueueCounts:
    """按顺序吃每一项的成败，**第一项失败就停**，剩下的全算 `not_run`。

    ⭐ **三个数不许合并。** 合并成「N 成功 / M+1 失败」是一次真实的信息损失：
    `not_run` 那些**既没有失败也没有成功，它们根本没被执行**——
    而人要决定的恰恰是「修完这一项之后从哪儿续」。
    """
    done = failed = 0
    for ok in outcomes:
        if ok:
            done += 1
            continue
        failed = 1
        break
    if done + failed > total:
        raise ValueError(f'跑了 {done + failed} 项，而计划里只有 {total} 项')
    return QueueCounts(done=done, failed=failed, not_run=total - done - failed)


# ─────────────────────────────────────────────────────────────────────────────
# 持久队列：append-only 事件、折叠视图与 runner（TODO-01）
# ─────────────────────────────────────────────────────────────────────────────

QUEUES_FILENAME = 'queues.jsonl'
RESTART_NOTE = '服务重启前这项已经交给作业执行器，但队列没有记到终态；先对照作业与产物恢复'
_STATE_LOCK = threading.RLock()


class QueueRegistry:
    """Process-local queue runners; persisted truth remains the event stream."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._live: set[str] = set()

    def acquire(self, queue_id: str) -> None:
        with self._lock:
            if queue_id in self._live:
                raise QueueConflict(f'队列 {queue_id} 已有 runner 在消费')
            self._live.add(queue_id)

    def release(self, queue_id: str) -> None:
        with self._lock:
            self._live.discard(queue_id)

    def live(self) -> set[str]:
        with self._lock:
            return set(self._live)


REGISTRY = QueueRegistry()
_THREADS_LOCK = threading.Lock()
_THREADS: dict[str, threading.Thread] = {}


def queue_path(base: str | os.PathLike[str]) -> Path:
    return jobs.service_dir(base) / QUEUES_FILENAME


def append_event(base: str | os.PathLike[str], event: QueueEvent) -> None:
    """Append one durable queue transition; never read-modify-write the log.

    ⚠ 写入方既有服务进程也有 CLI `queue`，而每本书一条 lane 是并行的
    （`R-13.7`）⇒ 互斥必须跨进程，落点是 `novel/appendlog.py`；
    `read_events` 那条「只容忍最后一行」要靠它才成立。
    """
    append_line(
        queue_path(base),
        json.dumps(event.model_dump(mode='json'), ensure_ascii=False))


def read_events(base: str | os.PathLike[str]) -> list[QueueEvent]:
    """Read queue events, tolerating only a truncated final line."""
    p = queue_path(base)
    if not p.is_file():
        return []
    raw = p.read_bytes().decode('utf-8', errors='replace').splitlines()
    out: list[QueueEvent] = []
    for index, line in enumerate(raw):
        if not line.strip():
            continue
        try:
            out.append(QueueEvent.model_validate(json.loads(line)))
        except Exception:  # noqa: BLE001 - only the interrupted tail is recoverable
            if index == len(raw) - 1:
                break
            raise
    return out


def new_queue_id(now: float | None = None) -> str:
    return f'q-{int(now if now is not None else time.time())}-{uuid.uuid4().hex[:8]}'


def _ordered_items(start: QueueStart) -> list[QueueItem]:
    """Restore book order and node order after QueuePlan's runnable/blocked split."""
    rank = {node: index for index, node in enumerate(NODES)}
    all_items = start.plan.items + start.plan.blocked
    out: list[QueueItem] = []
    for sid in start.sids:
        lane = [item for item in all_items if item.sid == sid]
        out.extend(sorted(lane, key=lambda item: rank[item.node]))
    return out


def _lane_counts(entries: list[QueueEntry]) -> QueueLaneCounts:
    states = ('done', 'landed_red', 'failed', 'not_run', 'pending', 'running', 'unknown',
              'cancelled', 'blocked')
    values = {state: sum(entry.state == state for entry in entries) for state in states}
    return QueueLaneCounts(**values)


def _run_state(entries: list[QueueEntry]) -> str:
    states = {entry.state for entry in entries}
    if 'running' in states:
        return 'running'
    if 'failed' in states:
        return 'failed'
    if 'landed_red' in states:
        return 'landed_red'
    if 'unknown' in states:
        return 'unknown'
    if 'pending' in states:
        return 'pending'
    if 'blocked' in states:
        return 'blocked'
    if 'cancelled' in states:
        return 'cancelled'
    return 'ok'


def fold(events: Iterable[QueueEvent], *, live: set[str] | None = None) -> list[QueueRun]:
    """Fold the durable stream; stale ``running`` becomes explicit ``unknown``."""
    live = set() if live is None else set(live)
    starts: dict[str, tuple[int, QueueStart]] = {}
    transitions: dict[str, list[QueueEvent]] = {}
    updated: dict[str, int] = {}
    for event in events:
        updated[event.queue_id] = event.ts
        if event.kind == 'created':
            if event.queue_id in starts:
                raise ValueError(f'duplicate created event for queue {event.queue_id}')
            assert event.start is not None
            starts[event.queue_id] = (event.ts, event.start)
        else:
            transitions.setdefault(event.queue_id, []).append(event)

    out: list[QueueRun] = []
    for queue_id, (created, start) in starts.items():
        entries = [QueueEntry(
            index=index, item=item,
            state='blocked' if item.waiting_on else 'pending',
            waiting_on=item.waiting_on,
            note=item.why if item.waiting_on else None,
        ) for index, item in enumerate(_ordered_items(start))]
        for event in transitions.get(queue_id, []):
            assert event.item_index is not None and event.state is not None
            if event.item_index >= len(entries):
                raise ValueError(
                    f'queue {queue_id} event points outside plan: {event.item_index}')
            old = entries[event.item_index]
            entries[event.item_index] = QueueEntry(
                index=old.index, item=old.item, state=event.state,
                job_id=event.job_id, exit_code=event.exit_code,
                waiting_on=event.waiting_on, note=event.note,
            )
        if queue_id not in live:
            entries = [
                entry.model_copy(update={'state': 'unknown', 'note': RESTART_NOTE})
                if entry.state == 'running' else entry
                for entry in entries
            ]
        counts = {
            sid: _lane_counts([entry for entry in entries if entry.item.sid == sid])
            for sid in start.sids
        }
        out.append(QueueRun(
            queue_id=queue_id, created=created,
            updated=updated.get(queue_id, created), start=start,
            state=_run_state(entries), entries=entries, counts_by_sid=counts,
        ))
    return sorted(out, key=lambda record: record.created, reverse=True)


def records(base: str | os.PathLike[str], *,
            registry: QueueRegistry = REGISTRY) -> list[QueueRun]:
    return fold(read_events(base), live=registry.live())


def _one(base: str | os.PathLike[str], queue_id: str, *,
         registry: QueueRegistry = REGISTRY) -> QueueRun:
    record = next((record for record in records(base, registry=registry)
                   if record.queue_id == queue_id), None)
    if record is None:
        raise UpstreamMissing(f'没有 queue_id={queue_id!r} 的持久队列')
    return record


def create(base: str | os.PathLike[str], sids: Sequence[str], *, until_stage: str,
           yes: bool, follow_check: bool = True,
           queue_id: str | None = None) -> QueueRun:
    """Plan, confirm and persist a queue without starting its runner."""
    unique = list(dict.fromkeys(sids))
    if not unique or len(unique) != len(sids):
        raise QueueConflict('队列必须至少包含一本书，且 sid 不得重复')
    planned = plan(base, unique, until_stage=until_stage, follow_check=follow_check)
    confirm(planned, yes=yes)
    queue_id = queue_id or new_queue_id()
    if any(event.queue_id == queue_id for event in read_events(base)):
        raise QueueConflict(f'queue_id={queue_id!r} 已存在')
    start = QueueStart(
        sids=unique, until_stage=until_stage, plan=planned,
        paid_confirmed=bool(yes or not planned.paid_items),
    )
    append_event(base, QueueEvent(
        queue_id=queue_id, kind='created', ts=int(time.time()), start=start))
    return _one(base, queue_id)


def _append_state(base: str | os.PathLike[str], queue_id: str, index: int,
                  state: str, *, job_id: str | None = None,
                  exit_code: int | None = None, waiting_on: str | None = None,
                  note: str | None = None) -> None:
    append_event(base, QueueEvent(
        queue_id=queue_id, kind='item_state', ts=int(time.time()),
        item_index=index, state=state, job_id=job_id, exit_code=exit_code,
        waiting_on=waiting_on, note=note,
    ))


def cancel(base: str | os.PathLike[str], queue_id: str, item_index: int, *,
           registry: QueueRegistry = REGISTRY) -> QueueRun:
    """Cancel only an item that has never started, then stop its novel lane."""
    with _STATE_LOCK:
        run = _one(base, queue_id, registry=registry)
        if item_index < 0 or item_index >= len(run.entries):
            raise QueueConflict(f'队列 {queue_id} 没有第 {item_index} 项')
        target = run.entries[item_index]
        if target.state not in ('pending', 'blocked'):
            raise QueueConflict(
                f'第 {item_index} 项当前是 {target.state}；只能取消尚未开始的 pending/blocked 项，'
                '运行中的 LLM 调用不能伪装成可无损取消')
        reason = f'人工取消尚未开始的第 {item_index} 项'
        _append_state(base, queue_id, item_index, 'cancelled', note=reason)
        for entry in run.entries[item_index + 1:]:
            if entry.item.sid == target.item.sid and entry.state in ('pending', 'blocked'):
                _append_state(
                    base, queue_id, entry.index, 'not_run',
                    note=f'同书较早的第 {item_index} 项被取消；本项未启动')
    return _one(base, queue_id, registry=registry)


def _command_surface(item: QueueItem) -> tuple[object, ...]:
    """The immutable part of a queue item that was shown and confirmed."""
    return (
        item.node, item.mode, tuple(item.flags),
        tuple((tuple(step.argv), step.cwd, step.paid, step.writes)
              for step in item.steps),
    )


def _current_plan_items(base: str | os.PathLike[str], start: QueueStart) -> list[QueueItem]:
    """Rebuild the currently required command surface in lane/node order."""
    rank = {node: index for index, node in enumerate(NODES)}
    out: list[QueueItem] = []
    for sid in start.sids:
        todo, blocked = plan_book(base, sid, until_stage=start.until_stage)
        out.extend(sorted(todo + blocked, key=lambda item: rank[item.node]))
    return out


def _live_disposition(base: str | os.PathLike[str], run: QueueRun,
                      entry: QueueEntry) -> tuple[str, str | None, str]:
    """Recheck disk state before launch; return state/waiting_on/reason."""
    todo, blocked = plan_book(
        base, entry.item.sid, until_stage=run.start.until_stage)
    same_node = [item for item in todo + blocked if item.node == entry.item.node]
    if not same_node:
        return 'done', None, '盘上产物存在且当前新鲜，恢复时跳过，不重复调用'
    matches = [item for item in same_node if item.mode == entry.item.mode]
    current = matches[0] if matches else same_node[0]
    if _command_surface(current) != _command_surface(entry.item):
        return (
            'blocked', WAITING_ON_REPLAN,
            '盘面或人工选择已改变，当前命令与队列创建时确认的快照不同；'
            '旧队列不会执行另一条命令，请重新预览并创建队列',
        )
    if current.waiting_on:
        return 'blocked', current.waiting_on, current.why
    return 'pending', None, current.why


def _remaining_paid_entries(run: QueueRun) -> list[QueueEntry]:
    """Paid frozen entries that a new resume turn could still reach."""
    terminal = {'done', 'landed_red', 'failed', 'not_run', 'cancelled'}
    return [
        entry for entry in run.entries
        if entry.state not in terminal and any(step.paid for step in entry.item.steps)
    ]


def frozen_resume_commands(
    base: str | os.PathLike[str], queue_id: str, *,
    registry: QueueRegistry = REGISTRY,
) -> list[str]:
    """Exact persisted paid commands which must be shown before resume."""
    run = _one(base, queue_id, registry=registry)
    return [
        shell_line(step.argv)
        for entry in _remaining_paid_entries(run)
        for step in entry.item.steps
        if step.paid
    ]


def _confirm_resume(
    base: str | os.PathLike[str], queue_id: str, *, yes: bool,
    registry: QueueRegistry,
) -> QueueRun:
    """Authorize one resume turn and release only restart-confirmation blocks.

    ``run``/``start`` deliberately continue to refuse the persisted restart
    marker.  This function is the sole path which may remove it, after showing
    and explicitly confirming the exact frozen commands.
    """
    with _STATE_LOCK:
        run = _one(base, queue_id, registry=registry)
        commands = frozen_resume_commands(base, queue_id, registry=registry)
        if commands and not yes:
            lines = [
                f'队列 {queue_id} 的本次恢复仍可能花钱。创建时冻结、现在必须重新确认的命令：',
                *(f'  $ {command}' for command in commands),
                '⇒ CLI 加 `--yes`；API 发送 `{"confirmed": true}`。未确认不会启动任何命令',
            ]
            raise ApprovalRequired('\n'.join(lines))

        # Startup persists a special block so calling run/start directly cannot
        # reinterpret it as ordinary pending work.  Once this resume turn has
        # explicit authorization, restore each such entry's *current* disk/gate
        # disposition; command-surface drift still becomes WAITING_ON_REPLAN.
        for entry in run.entries:
            if not (entry.state == 'blocked'
                    and entry.waiting_on == WAITING_ON_RESTART_CONFIRMATION):
                continue
            disposition, waiting_on, reason = _live_disposition(base, run, entry)
            _append_state(
                base, queue_id, entry.index, disposition,
                waiting_on=waiting_on,
                note=('本次恢复已重新展示并确认冻结付费命令；' + reason),
            )
        return _one(base, queue_id, registry=registry)


def _job_record(base: str | os.PathLike[str], job_id: str,
                registry: jobs.JobRegistry) -> object | None:
    return next((record for record in jobs.records(base, registry=registry)
                 if record.job_id == job_id), None)


def _recover_running(base: str | os.PathLike[str], run: QueueRun,
                     entry: QueueEntry, *, job_registry: jobs.JobRegistry) -> str:
    """Recover one stale running event from job records and artifact truth."""
    assert entry.job_id is not None
    job = _job_record(base, entry.job_id, job_registry)
    if job is not None and job.state == 'running':
        return 'running'
    if job is not None and job.state in ('ok', 'landed_red', 'failed'):
        state = {'ok': 'done', 'landed_red': 'landed_red', 'failed': 'failed'}[job.state]
        note = ('从持久作业终态恢复队列项；' + jobs.LANDED_RED_NOTE
                if job.state == 'landed_red' else '从持久作业终态恢复队列项')
        _append_state(
            base, run.queue_id, entry.index, state, job_id=entry.job_id,
            exit_code=job.exit_code, note=note)
        return state
    disposition, waiting_on, reason = _live_disposition(base, run, entry)
    if disposition == 'done':
        _append_state(
            base, run.queue_id, entry.index, 'done', job_id=entry.job_id,
            note=f'{RESTART_NOTE}；{reason}')
        return 'done'
    _append_state(
        base, run.queue_id, entry.index, disposition,
        waiting_on=waiting_on,
        note=(f'{RESTART_NOTE}；旧作业是 unknown，当前盘面仍需执行；'
              '重试沿用原命令与缓存，不把 unknown 当成功'),
    )
    return disposition


def _stop_reason(failed: QueueEntry) -> str:
    """停下的那一句话。**必须说出上一项到底是哪一种失败**——处置是相反的。

    ⚠⚠ exit 1（产物已落盘、报告有红行）与 exit 2/3/70（压根没跑成）在
    `JOB_STATE` 里已分成 `landed_red` / `failed`。人拿到「第 9 项后停下」
    这句话时要做的事，
    前者是**去看报告并裁决**，后者是**修完重跑**——不说清就等于让人先去猜。
    实测：`stage6.hook` exit 1，`hook.json` 好好地在盘上，后三项被记成
    `not_run`，日志上与一次真崩一模一样。
    """
    if X.landed_but_red(failed.exit_code):
        return (f'同书第 {failed.index} 项（{failed.item.node}）**产物已落盘**、'
                f'但校验报告有阻断级红行（exit 1）；按 `R-13.4` 队列停在这里等人裁决，'
                f'本项没有启动。⚠ 这不是「没跑成」——先看那一层的屏，多半不必重跑')
    return (f'同书第 {failed.index} 项（{failed.item.node}）没跑成'
            f'（exit {failed.exit_code}）；本项没有启动')


def _mark_not_run_after(base: str | os.PathLike[str], run: QueueRun,
                        failed: QueueEntry) -> None:
    reason = _stop_reason(failed)
    for entry in run.entries[failed.index + 1:]:
        if entry.item.sid == failed.item.sid and entry.state in (
                'pending', 'blocked', 'unknown'):
            _append_state(base, run.queue_id, entry.index, 'not_run', note=reason)


def _run_lane(base: str | os.PathLike[str], queue_id: str, sid: str, *,
              spawn: jobs.Spawn, queue_registry: QueueRegistry,
              job_registry: jobs.JobRegistry) -> None:
    # A successful fake/test subprocess does not necessarily materialize an
    # artifact. Rechecking the item again in the same runner turn would launch
    # it forever. Disk truth is rechecked when a *new* resume turn starts; this
    # set only remembers what this turn itself has just settled.
    settled_this_turn: set[int] = set()
    while True:
        run = _one(base, queue_id, registry=queue_registry)
        lane = [entry for entry in run.entries if entry.item.sid == sid]
        progressed = False
        for entry in lane:
            if entry.state == 'done' and entry.index in settled_this_turn:
                continue
            if (entry.state == 'blocked'
                    and entry.waiting_on == WAITING_ON_RESTART_CONFIRMATION):
                # A service restart expires the old spending authorization.
                # Only _confirm_resume may remove this durable marker; treating
                # it like an ordinary live gate would silently turn it back into
                # pending and execute without a fresh --yes/confirmed=true.
                return
            if entry.state == 'blocked' and entry.waiting_on == WAITING_ON_REPLAN:
                # This is not a human gate that can simply become true.  The
                # frozen queue lacks (or disagrees with) the current command;
                # queue-resume must never turn that into permission to execute
                # an obsolete snapshot.
                return
            if entry.state in ('landed_red', 'failed', 'cancelled', 'not_run'):
                return
            if entry.state in ('running', 'unknown'):
                recovered = _recover_running(
                    base, run, entry, job_registry=job_registry)
                if recovered in ('running', 'blocked', 'landed_red', 'failed'):
                    if recovered in ('landed_red', 'failed'):
                        recovered_run = _one(base, queue_id, registry=queue_registry)
                        _mark_not_run_after(
                            base, recovered_run, recovered_run.entries[entry.index])
                    return
                if recovered == 'done':
                    settled_this_turn.add(entry.index)
                progressed = True
                break

            disposition, waiting_on, reason = _live_disposition(base, run, entry)
            if disposition == 'done':
                if entry.state != 'done':
                    _append_state(base, queue_id, entry.index, 'done', note=reason)
                    settled_this_turn.add(entry.index)
                    progressed = True
                    break
                settled_this_turn.add(entry.index)
                continue
            if disposition == 'blocked':
                if entry.state != 'blocked' or entry.waiting_on != waiting_on:
                    _append_state(
                        base, queue_id, entry.index, 'blocked',
                        waiting_on=waiting_on, note=reason)
                return
            if entry.state == 'done':
                _append_state(
                    base, queue_id, entry.index, 'pending',
                    note='盘面不再完整/新鲜，按原已确认命令恢复执行')
                progressed = True
                break

            job_id = jobs.new_job_id()
            with _STATE_LOCK:
                fresh = _one(base, queue_id, registry=queue_registry).entries[entry.index]
                if fresh.state not in ('pending', 'blocked'):
                    progressed = True
                    break
                _append_state(
                    base, queue_id, entry.index, 'running', job_id=job_id,
                    note=reason)
            try:
                prepare = None
                if (entry.item.node == 'stage5.write'
                        and '--shallow-rest' in entry.item.flags):
                    raw = flag_value(entry.item.flags, '--deep') or ''
                    selected = [] if raw == 'none' else [
                        int(value) for value in raw.split(',') if value]
                    prepare = lambda selected=selected: approvals.begin_writing(
                        base, entry.item.sid, selected)
                jobs.start(
                    base, entry.item.node, entry.item.sid, entry.item.flags,
                    mode=entry.item.mode, steps=entry.item.steps,
                    spawn=spawn, registry=job_registry, job_id=job_id,
                    prepare=prepare)
                jobs.wait(job_id)
                result = _job_record(base, job_id, job_registry)
                if result is None or result.state not in ('ok', 'landed_red', 'failed'):
                    _append_state(
                        base, queue_id, entry.index, 'unknown', job_id=job_id,
                        note='作业线程结束后仍没有可折叠终态')
                    return
                state = {'ok': 'done', 'landed_red': 'landed_red',
                         'failed': 'failed'}[result.state]
                _append_state(
                    base, queue_id, entry.index, state, job_id=job_id,
                    exit_code=result.exit_code,
                    note=(jobs.LANDED_RED_NOTE
                          if result.state == 'landed_red' else None))
            except jobs.JobConflict as exc:
                _append_state(
                    base, queue_id, entry.index, 'blocked',
                    waiting_on=f'job:{entry.item.sid}', note=str(exc))
                return
            except BaseException as exc:  # worker crash is a queue failure, never silent
                _append_state(
                    base, queue_id, entry.index, 'failed', job_id=job_id,
                    exit_code=CRASH, note=f'runner 启动作业失败：{exc!r}')
            current = _one(base, queue_id, registry=queue_registry)
            ended = current.entries[entry.index]
            if ended.state in ('landed_red', 'failed'):
                _mark_not_run_after(base, current, ended)
                return
            if ended.state == 'done':
                settled_this_turn.add(entry.index)
            progressed = True
            break
        if not progressed:
            return


def _execute(base: str | os.PathLike[str], queue_id: str, *,
             spawn: jobs.Spawn, queue_registry: QueueRegistry,
             job_registry: jobs.JobRegistry) -> QueueRun:
    run = _one(base, queue_id, registry=queue_registry)
    lane_threads = [threading.Thread(
        target=_run_lane,
        kwargs={
            'base': base, 'queue_id': queue_id, 'sid': sid, 'spawn': spawn,
            'queue_registry': queue_registry, 'job_registry': job_registry,
        },
        name=f'novel-queue-{queue_id}-{sid}', daemon=True,
    ) for sid in run.start.sids]
    for thread in lane_threads:
        thread.start()
    for thread in lane_threads:
        thread.join()
    return _one(base, queue_id, registry=queue_registry)


def run(base: str | os.PathLike[str], queue_id: str, *,
        spawn: jobs.Spawn = jobs.default_spawn,
        registry: QueueRegistry = REGISTRY,
        job_registry: jobs.JobRegistry = jobs.REGISTRY) -> QueueRun:
    """Synchronously consume each novel lane; different novels run in parallel."""
    registry.acquire(queue_id)
    try:
        return _execute(
            base, queue_id, spawn=spawn, queue_registry=registry,
            job_registry=job_registry)
    finally:
        registry.release(queue_id)


def resume(base: str | os.PathLike[str], queue_id: str, *, yes: bool,
           spawn: jobs.Spawn = jobs.default_spawn,
           registry: QueueRegistry = REGISTRY,
           job_registry: jobs.JobRegistry = jobs.REGISTRY) -> QueueRun:
    """Synchronously resume only after a fresh paid-command confirmation."""
    _confirm_resume(base, queue_id, yes=yes, registry=registry)
    return run(
        base, queue_id, spawn=spawn, registry=registry,
        job_registry=job_registry)


def start(base: str | os.PathLike[str], queue_id: str, *,
          spawn: jobs.Spawn = jobs.default_spawn,
          registry: QueueRegistry = REGISTRY,
          job_registry: jobs.JobRegistry = jobs.REGISTRY) -> QueueRun:
    """Start a persistent queue runner in the background and return immediately."""
    registry.acquire(queue_id)

    def worker() -> None:
        try:
            _execute(
                base, queue_id, spawn=spawn, queue_registry=registry,
                job_registry=job_registry)
        except BaseException:
            # Keep the traceback in the service log. Item-level failures are
            # recorded inside _run_lane; this guard prevents a silent thread death.
            traceback.print_exc()
        finally:
            registry.release(queue_id)
            with _THREADS_LOCK:
                _THREADS.pop(queue_id, None)

    thread = threading.Thread(
        target=worker, name=f'novel-queue-{queue_id}', daemon=True)
    with _THREADS_LOCK:
        _THREADS[queue_id] = thread
    try:
        thread.start()
    except Exception:
        with _THREADS_LOCK:
            _THREADS.pop(queue_id, None)
        registry.release(queue_id)
        raise
    return _one(base, queue_id, registry=registry)


def resume_start(base: str | os.PathLike[str], queue_id: str, *, yes: bool,
                 spawn: jobs.Spawn = jobs.default_spawn,
                 registry: QueueRegistry = REGISTRY,
                 job_registry: jobs.JobRegistry = jobs.REGISTRY) -> QueueRun:
    """Asynchronously resume after the same confirmation used by the CLI."""
    _confirm_resume(base, queue_id, yes=yes, registry=registry)
    return start(
        base, queue_id, spawn=spawn, registry=registry,
        job_registry=job_registry)


def wait(queue_id: str, timeout: float | None = None) -> bool:
    with _THREADS_LOCK:
        thread = _THREADS.get(queue_id)
    if thread is None:
        return True
    thread.join(timeout=timeout)
    return not thread.is_alive()


def resume_incomplete(base: str | os.PathLike[str], *,
                      spawn: jobs.Spawn = jobs.default_spawn,
                      registry: QueueRegistry = REGISTRY,
                      job_registry: jobs.JobRegistry = jobs.REGISTRY) -> list[str]:
    """Service-start hook with a fresh disk/command/cost safety audit.

    An old ``--yes`` authorizes the old plan once.  A service restart is a new
    execution turn: fresh free commands with an unchanged snapshot may resume,
    while any remaining paid command waits for an explicit ``queue-resume``.
    A changed command surface is never substituted into the frozen queue.
    """
    before = records(base, registry=registry)
    if not before:
        return []
    jobs.reconcile(base, registry=job_registry)
    resumed: list[str] = []
    for record in records(base, registry=registry):
        if record.state in ('ok', 'landed_red', 'failed', 'cancelled'):
            continue

        recovered_any = False
        for entry in record.entries:
            if entry.state not in ('running', 'unknown'):
                continue
            recovered = _recover_running(
                base, record, entry, job_registry=job_registry)
            recovered_any = True
            if recovered in ('landed_red', 'failed'):
                current = _one(base, record.queue_id, registry=registry)
                _mark_not_run_after(base, current, current.entries[entry.index])

        # Re-evaluate every non-terminal/done entry against current disk truth.
        # A previously done free item may safely become pending again; a changed
        # command is turned into an explicit replan block by _live_disposition.
        current = _one(base, record.queue_id, registry=registry)
        if current.state in ('landed_red', 'failed', 'cancelled'):
            continue
        for entry in current.entries:
            if entry.state not in ('pending', 'blocked', 'done'):
                continue
            disposition, waiting_on, reason = _live_disposition(base, current, entry)
            if disposition == entry.state and (
                    disposition != 'blocked' or waiting_on == entry.waiting_on):
                continue
            _append_state(
                base, current.queue_id, entry.index, disposition,
                waiting_on=waiting_on, note=reason)
            recovered_any = True

        current = _one(base, record.queue_id, registry=registry)
        current_items = _current_plan_items(base, current.start)
        remaining = [entry.item for entry in current.entries
                     if entry.state in ('pending', 'blocked', 'unknown', 'running')]
        if ([_command_surface(item) for item in remaining]
                != [_command_surface(item) for item in current_items]):
            targets = [entry for entry in current.entries
                       if entry.state in ('pending', 'blocked', 'unknown', 'running')]
            if not targets and current.entries:
                targets = [current.entries[-1]]
            reason = (
                '服务重启后重新规划得到的节点/命令集合与创建时未完成快照不同；'
                '旧队列不会增加、删除或替换命令，请重新预览并创建队列')
            for entry in targets:
                _append_state(
                    base, current.queue_id, entry.index, 'blocked',
                    waiting_on=WAITING_ON_REPLAN, note=reason)
            continue

        pending = [entry for entry in current.entries if entry.state == 'pending']
        if any(step.paid for entry in pending for step in entry.item.steps):
            reason = (
                '服务重启后不会复用旧队列几小时前的 `--yes` 静默执行付费节点；'
                '当前命令快照未变，重新展示冻结命令并经 CLI `queue-resume --yes` '
                '或 API `confirmed=true` 确认后才能恢复')
            for entry in pending:
                _append_state(
                    base, current.queue_id, entry.index, 'blocked',
                    waiting_on=WAITING_ON_RESTART_CONFIRMATION, note=reason)
            continue

        current = _one(base, record.queue_id, registry=registry)
        if any(entry.state in ('blocked', 'unknown', 'running')
               for entry in current.entries):
            continue
        if any(entry.state == 'pending' for entry in current.entries):
            start(
                base, current.queue_id, spawn=spawn, registry=registry,
                job_registry=job_registry)
            resumed.append(current.queue_id)
        elif recovered_any and current.state == 'ok':
            resumed.append(current.queue_id)
    return resumed


__all__ = [
    'HUMAN_NODES', 'OPTIONAL_HUMAN_NODES', 'WAITING_ON_INBOX',
    'WAITING_ON_DEPTH_SELECTION', 'WAITING_ON_RESTART_CONFIRMATION', 'WAITING_ON_REPLAN',
    'QueueRegistry', 'append_event', 'cancel', 'confirm', 'create', 'fold',
    'fold_counts', 'frozen_resume_commands', 'new_queue_id', 'nodes_up_to', 'plan', 'plan_book',
    'queue_path', 'read_events', 'records', 'resume_incomplete', 'run', 'start',
    'resume', 'resume_start', 'wait',
]
