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

import json
import os
import threading
import time
import traceback
import uuid
from collections.abc import Iterable, Sequence
from pathlib import Path

from contracts.invariants import (
    LEGACY_STAGE5_MODES, NODES, RETIRED_NODES, RETIRED_STAGES, STAGES,
)
from contracts.schema.orchestration import (
    NovelOverview, QueueCounts, QueueEntry, QueueEvent, QueueItem,
    QueueLaneCounts, QueuePlan, QueueRun, QueueStart,
)
from novel import prov
from novel.appendlog import append_line
from novel import exit_codes as X
from novel.exit_codes import CRASH
from novel.errors import (
    ApprovalRequired, QueueConflict, UpstreamMissing,
)
from novel.orchestrator import jobs, state as st
from novel.orchestrator.nodes import build_steps, flag_value, shell_line

#: 人做的那几个动作。**队列永不自动排它们**——「人看过之后的裁决」机器替不了。
#: ⚠ 它们进 `blocked` 而不是被跳过：被跳过的闸门在界面上与「不需要它」长得一样。
#: ⚠ `stage6.approve` 不算人工闸（2026-09-15 全自动裁决）：默认槽位序自动定稿是
#:   队列的普通 todo。`stage0.approve` / `stage0.edit` / `stage7.approve` 与
#:   prepare 接受闸均已退役（批次 B1/B4）；现役 ``HUMAN_NODES`` 为空。
HUMAN_NODES: frozenset[str] = frozenset()

#: 待人工清单非空时挡队列的那个理由（`INBOX_KIND` 的 `invalid`）。
WAITING_ON_INBOX = 'inbox:invalid'
# ⚠⚠ F8c：以下常量是**旧链 waiter 名**，只供存量队列账／审批账的只读解码
# （`novel/orchestrator/reviewer_legacy.py`、`reviewer_action_evidence.py` 与
# 折叠历史 run 时按原字节解释）。现役规划器只发 `WAITING_ON_INBOX`／
# `WAITING_ON_LEGACY`／`WAITING_ON_RESUME_DECISION` 三个 blocker；旧链的
# edit-commit／invalid-revalidate／analyze 修复／stage4 repair／stage5 closure／
# reviewer-rewrite／stage6 prepare 补尾路只能由旧链节点写出，生产入口已拒识。
WAITING_ON_STAGE2_EDIT_COMMIT = 'recovery:stage2-global-edit-commit'
WAITING_ON_STAGE2_EDIT_REVALIDATE = (
    'recovery:stage2-global-edit-revalidate')
WAITING_ON_INVALID_REVALIDATE = 'recovery:invalid-revalidate'
WAITING_ON_ANALYZE_RESPONSE_REPAIR = 'human:stage2-analyze-response-repair'
WAITING_ON_ANALYZE_RESPONSE_TERMINAL = 'human:stage2-analyze-response-terminal'
WAITING_ON_ANALYZE_SOURCE = 'human:stage2-analyze-source-unavailable'
WAITING_ON_ANALYZE_DRAFT_REPAIR = 'human:stage2-analyze-owner-draft-repair'
WAITING_ON_STAGE4_DESIGN_REVIEW = 'approval:stage4-design-review'
WAITING_ON_STAGE4_BINDING_RESOLUTION = 'approval:stage4-binding-resolution'
WAITING_ON_STAGE4_STRUCTURE_REPLAN = 'approval:stage4-structure-replan'
WAITING_ON_STAGE4_STRUCTURE_REVIEW = 'approval:stage4-structure-review'
# 兼容只 import 常量名的旧调用者；同一退役裁决下的只读别名。
WAITING_ON_STAGE4_PHASE_APPROVAL = WAITING_ON_STAGE4_DESIGN_REVIEW
WAITING_ON_STAGE4_REPLAN = WAITING_ON_STAGE4_BINDING_RESOLUTION
WAITING_ON_STAGE4_DESIGN_REPAIR_COMMIT = \
    'recovery:stage4-design-repair-aggregate-commit'
WAITING_ON_WRITING_APPROVAL = 'approval:stage5-writing'
WAITING_ON_REVIEW = 'stage5:review-currentness'
WAITING_ON_STAGE5_CLOSURE = 'approval:stage5-closure'
WAITING_ON_STAGE6_PREPARE = 'approval:stage6-prepare'
WAITING_ON_STAGE6_PREPARE_COMMIT = 'recovery:stage6-prepare-edit'
WAITING_ON_STAGE5_REVIEWER_REWRITE = 'recovery:stage5-reviewer-rewrite'
WAITING_ON_PREPARED_DELIVERY = 'recovery:prepared-delivery'
WAITING_ON_PREPARED_DELIVERY_UNKNOWN = 'human:prepared-unknown'
WAITING_ON_LEGACY = 'legacy_incompatible'
WAITING_ON_RESUME_DECISION = 'resume:manual-decision'
WAITING_ON_RESTART_CONFIRMATION = 'queue:restart-confirmation'
WAITING_ON_REPLAN = 'queue:plan-changed'

#: 已有 typed 决定、只差按它执行的 blocker 闭集：Supervisor 只对这个集合里的
#: waiting_on 现场铸 recovery permit 并交给 Executor，避免任何普通人工 blocker
#: 因“碰巧带 steps”被自动执行。
#: ⚠⚠ F8c：**现役为空集**。旧链的 0-wire 补尾 waiter（edit-commit／invalid-
#: revalidate／stage4 repair／reviewer-rewrite／prepared-delivery／stage6
#: prepare-edit）随旧链退役；新链的恢复是候选 runner 自身续跑（`--check-only`
#: 免费复检与精确补尾，F7c），不经队列发布补尾 argv。blocked 即停在那里并如实
#: 报告，不猜成可执行命令。
DETERMINISTIC_RECOVERY_WAITING_ON: frozenset[str] = frozenset()


def chapters_from_flags(flags: Sequence[str]) -> list[int] | None:
    """取 Stage5 定向章列表；不带则是全书。

    队列只会读自己冻结过的命令，但恢复时仍要关闭式校验：旧
    `--deep/--shallow-rest` 快照必须走 replan，不得在这里被解释成新语义。
    """
    raw = flag_value(flags, '--chapters')
    if raw is None:
        return None
    try:
        values = [int(value) for value in raw.split(',')]
    except ValueError as exc:
        raise ApprovalRequired('--chapters 必须是逗号分隔的正整数章号') from exc
    if values != sorted(set(values)) or any(value < 1 for value in values):
        raise ApprovalRequired('--chapters 必须升序、去重且全为正整数')
    return values


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
    """目标节点真实依赖的严格上游里，有几份被闸门拦下的稿。

    ⚠ 只看上游不看本层：本层自己的 `.INVALID` 是**本节点该处理的事**
    （0 成本提升路 `--revalidate`），不是挡住它自己的理由。

    当前节点都沿主链消费前序层；Stage3 已退役，编号空洞不改变依赖顺序。
    """
    upstream_stages = {
        name for name in STAGES if _stage_index(name) < _stage_index(stage)
    }
    return [a.rel for s in ov.stages if s.stage in upstream_stages
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

    批次 B4 后 ``HUMAN_NODES`` 为空；本函数仅保留给历史调用方，恒 False。
    """
    return False


def plan_book(base: str | os.PathLike[str], sid: str, *, until_stage: str,
              follow_check: bool = True) -> tuple[list[QueueItem], list[QueueItem]]:
    """一本书的 `(要跑的, 被挡住的)`。**纯函数**（只读盘，不写、不发请求）。

    完成度只在这个薄入口读取一次；已经持有 invocation-scoped operator 快照的
    调用方必须改走 :func:`plan_book_from_overview`，不能为同一个下一步再读一份
    overview。两条入口以下共享完全相同的节点、闸门与恢复判定。
    """
    return plan_book_from_overview(
        base, sid, st.overview(base, sid), until_stage=until_stage,
        follow_check=follow_check)


def plan_book_from_overview(
    base: str | os.PathLike[str],
    sid: str,
    ov: NovelOverview,
    *,
    until_stage: str,
    follow_check: bool = True,
    allow_deterministic_redo: bool = False,
) -> tuple[list[QueueItem], list[QueueItem]]:
    """按一份已冻结完成度规划 lane；判定正文与 :func:`plan_book` 唯一共享。

    ``allow_deterministic_redo`` 只供每步后都会重新采快照的 AgentRun operator。
    普通多步队列保持默认 ``False``，不能把一份冻结计划扩张成后续重采授权。

    ⚠⚠ F8c：规划器只按 `NODES`（现役 11 个 `str06.` 节点＋stage0/1）**通用派生**。
    旧链的节点专属分支全部随旧链退役删除：stage2 analyze/characters/global/
    blueprint 的恢复面、stage4 design/plan 的人工闸与统一机械准入名册、
    stage5 write/review 的闭合回执与 reviewer-rewrite、stage6 prepare 接受闸、
    prepared delivery 0-wire 补尾。理由不是「它们没用了」而是「它们只能由旧链
    节点写出，而生产入口已拒识旧节点」——新书的盘面不可能再产生那些状态；
    新链的恢复是候选 runner 自身续跑（F7c：binding-only 精确补尾、同指纹熔断），
    不经队列发布补尾 argv。live `.INVALID` 一律走收件箱关闭式阻断：现役节点
    旗标面只有 `--check-only`，没有 0-wire 自动提升 argv，绝不能把「没有实现
    恢复」解释成「同意再付一次钱」。
    """
    todo: list[QueueItem] = []
    blocked: list[QueueItem] = []
    #: ⚠ **一旦挡住，其后的项一律跟着挡住**——那才叫「挡住队列」（`R-13.5`）。
    #: 只挡住那一项而让后面的照跑，等于让下游拿着一份没人签过字的上游继续走。
    stuck: str | None = None
    for node in nodes_up_to(until_stage):
        spec = NODES[node]
        why_blocked = stuck
        if why_blocked is None:
            ghosts = _upstream_invalid(ov, spec.stage)
            if ghosts:
                # 严格上游里有被闸门拦下的稿：本节点的正式输入不完整。
                why_blocked = WAITING_ON_INBOX
        verdict, why = st.resume_verdict(ov, node)
        if why_blocked is None and verdict == st.RESUME_LEGACY:
            why_blocked = WAITING_ON_LEGACY
        if (why_blocked is None and verdict == st.RESUME_RUN
                and st.live_invalid_artifacts(ov, node)):
            # 本层自己的 live `.INVALID`：没有可自动排入的提升路 ⇒ 收件箱阻断，
            # 且不冻结任何默认付费 argv（见模块 docstring 的 F8c 段）。
            why_blocked = WAITING_ON_INBOX
        deterministic_redo_flags: tuple[str, ...] | None = None
        if (why_blocked is None and verdict == st.RESUME_REDO
                and allow_deterministic_redo):
            deterministic_redo_flags = st.operator_redo_flags(ov, node)
        if (why_blocked is None and verdict == st.RESUME_UNKNOWN) or (
            why_blocked is None
            and verdict == st.RESUME_REDO
            and deterministic_redo_flags is None
        ):
            # UNKNOWN 永远不能猜成 RUN。REDO 在普通队列也一律停；只有 AgentRun
            # 单步 operator 能拿到 state 登记并证明过的 canonical flags。仅仅把
            # ``allow`` 打开并不放宽 file_changed/unreadable/INVALID 等关闭式分支。
            why_blocked = WAITING_ON_RESUME_DECISION
        if why_blocked is None and verdict == st.RESUME_SKIP:
            continue                                 # 已存在即跳过
        flags: list[str] = (
            list(deterministic_redo_flags)
            if deterministic_redo_flags is not None
            else []
        )
        steps = None
        if why_blocked is not None:
            # 任何 blocker 都不冻结命令面（尤其是下游付费 argv）：状态改变后必须
            # 重新 plan，不能把旧快照重解释为新授权。
            steps = []
        if steps is None:
            steps = build_steps(
                node, sid, flags, base=base,
                follow_check=follow_check)

        item = QueueItem(
            sid=sid, node=node, flags=flags, steps=steps,
            why=(f'{verdict}：{why}' if why_blocked is None
                 else _blocked_why(why_blocked, why)),
            waiting_on=why_blocked,
        )
        (blocked if why_blocked else todo).append(item)
        if why_blocked and stuck is None:
            stuck = why_blocked
    return todo, blocked
def _blocked_why(
    waiting_on: str, verdict_why: str,
) -> str:
    # F8c：现役队列只发三个 blocker（INBOX／LEGACY／RESUME_DECISION）；旧链的
    # stage2 edit-commit／stage4 repair／stage5 closure／reviewer-rewrite／
    # stage6 prepare 提升路随旧链退役删除。`approval:` 前缀保留为编排错误哨兵
    # （现役链没有任何审批闸，出现即 fail loud）；其余未登记名字走通用句式，
    # 存量队列账（`read_events` 折叠的历史 run）按原字节解码，不改写历史。
    if waiting_on == WAITING_ON_INBOX:
        return (
            '上游或本层有被闸门拦下的稿：现役节点旗标面只有 `--check-only`，'
            '没有 0-wire 自动提升 argv ⇒ 队列关闭式阻断，不冻结默认付费重跑；'
            f'{verdict_why}')
    if waiting_on == WAITING_ON_LEGACY:
        return f'{verdict_why}；旧产物只读保留，必须显式重建 canonical Stage5'
    if waiting_on == WAITING_ON_RESUME_DECISION:
        return (
            '续跑结论要求人工决定，队列不会把 REDO/UNKNOWN 猜成可执行命令；'
            f'{verdict_why}')
    if waiting_on.startswith('approval:'):
        return (
            f'编排错误：现役队列不应再发出已退役审批闸 `{waiting_on}`；'
            f'{verdict_why}')
    return f'在等 `{waiting_on}`——{verdict_why}'


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
            out.append(_decode_event(json.loads(line)))
        except Exception:  # noqa: BLE001 - only the interrupted tail is recoverable
            if index == len(raw) - 1:
                break
            raise
    return out


def _decode_event(payload: object) -> QueueEvent:
    """只在磁盘边界消化旧队列的 ``mode``。

    ``mode=null`` 是旧 serializer 在所有条目上留下的噪声，直接剥离。
    任一非空 deep/shallow 表明这是旧 Stage5 双臂快照：整份计划
    投影为 ``queue:plan-changed`` 且清空命令，保证 API 不暴露 mode，
    runner 也永远不会把旧快照解释成 canonical 写作。
    """
    if not isinstance(payload, dict):
        return QueueEvent.model_validate(payload)
    data = dict(payload)
    start = data.get('start')
    if not isinstance(start, dict):
        return QueueEvent.model_validate(data)
    projected_start = dict(start)
    plan = projected_start.get('plan')
    if not isinstance(plan, dict):
        return QueueEvent.model_validate(data)
    projected_plan = dict(plan)
    has_legacy_arm = False
    buckets: dict[str, list[object]] = {}
    for bucket in ('items', 'blocked'):
        raw_items = projected_plan.get(bucket, [])
        if not isinstance(raw_items, list):
            return QueueEvent.model_validate(data)
        projected_items: list[object] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                projected_items.append(raw_item)
                continue
            item = dict(raw_item)
            if 'mode' in item:
                legacy_mode = item.pop('mode')
                if legacy_mode is not None:
                    if not (
                        item.get('node') == 'stage5.write'
                        and legacy_mode in LEGACY_STAGE5_MODES
                    ):
                        raise ValueError(
                            'queues.jsonl 的 mode 只允许旧 stage5.write deep/shallow')
            else:
                legacy_mode = None
            if jobs._has_retired_stage5_selector(item, legacy_mode):
                has_legacy_arm = True
            projected_items.append(item)
        buckets[bucket] = projected_items

    if has_legacy_arm:
        reason = (
            '旧 Stage5 deep/shallow 队列快照与 canonical 契约不兼容；'
            '本记录只读，必须重新预览并创建队列')
        blocked: list[object] = []
        for raw_item in buckets['items'] + buckets['blocked']:
            if not isinstance(raw_item, dict):
                blocked.append(raw_item)
                continue
            item = dict(raw_item)
            item['steps'] = []
            item['why'] = reason
            item['waiting_on'] = WAITING_ON_REPLAN
            blocked.append(item)
        projected_plan['items'] = []
        projected_plan['blocked'] = blocked
        projected_plan['paid_items'] = 0
    else:
        projected_plan['items'] = buckets['items']
        projected_plan['blocked'] = buckets['blocked']
    projected_start['plan'] = projected_plan
    data['start'] = projected_start
    return QueueEvent.model_validate(data)


def new_queue_id(now: float | None = None) -> str:
    return f'q-{int(now if now is not None else time.time())}-{uuid.uuid4().hex[:8]}'


def _ordered_items(start: QueueStart) -> list[QueueItem]:
    """Restore book order and node order after QueuePlan's runnable/blocked split."""
    # ⚠ F8b-3：历史 queues.jsonl 可含退役节点条目（旧账只读可解码）；排序
    # 名册只覆盖现役 NODES，退役节点按注册顺序排在现役面之后，不参与新调度。
    rank = {node: index for index, node in enumerate(NODES)}
    retired_rank = {node: len(rank) + index
                    for index, node in enumerate(RETIRED_NODES)}
    rank.update(retired_rank)
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
    from novel.work_units import check_workflow_write
    check_workflow_write(base)
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
        item.node, tuple(item.flags),
        tuple((tuple(step.argv), step.cwd, step.paid, step.writes)
              for step in item.steps),
    )


def _current_plan_items(base: str | os.PathLike[str], start: QueueStart) -> list[QueueItem]:
    """Rebuild the currently required command surface in lane/node order."""
    return _current_plan_items_for_sids(base, start, start.sids)


def _current_plan_items_for_sids(
        base: str | os.PathLike[str], start: QueueStart,
        sids: Iterable[str],
) -> list[QueueItem]:
    """只重建指定 lane；unresolved lane 的盘面在 startup 中不得触碰。"""
    if start.until_stage in RETIRED_STAGES:
        return []
    rank = {node: index for index, node in enumerate(NODES)}
    out: list[QueueItem] = []
    selected = frozenset(sids)
    for sid in start.sids:
        if sid not in selected:
            continue
        todo, blocked = plan_book(base, sid, until_stage=start.until_stage)
        out.extend(sorted(todo + blocked, key=lambda item: rank[item.node]))
    return out


def _live_disposition(base: str | os.PathLike[str], run: QueueRun,
                      entry: QueueEntry) -> tuple[str, str | None, str]:
    """Recheck disk state before launch; return state/waiting_on/reason."""
    if run.start.until_stage in RETIRED_STAGES or entry.item.node in RETIRED_NODES:
        return (
            'blocked', WAITING_ON_REPLAN,
            '该队列快照包含已经退役的 Stage3 节点；历史仍可读，但不会重新调度，'
            '请按当前节点面重新预览并创建队列',
        )
    todo, blocked = plan_book(
        base, entry.item.sid, until_stage=run.start.until_stage)
    same_node = [item for item in todo + blocked if item.node == entry.item.node]
    if not same_node:
        return 'done', None, '盘上产物存在且当前新鲜，恢复时跳过，不重复调用'
    current = same_node[0]
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
    from novel.work_units import check_workflow_write
    check_workflow_write(base)
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


def _prior_execution_identity(
    base: str | os.PathLike[str], queue_id: str, item_index: int,
) -> tuple[str | None, str | None]:
    """本队列项上一次 execution 的 `(run_id, job_id)`；从未启动过为 `(None, None)`。

    只认 durable 事件流：队列项历史里最后一个带 ``job_id`` 的事件指向原 job，
    原 job 的 ``started`` 事件携带业务 ``run_id``（批次 3）。旧盘作业没有
    run_id 字段时返回 ``(None, prior_job_id)``——新 job 照常新造 run 身份，
    不按时间猜、不伪造恢复链。
    """
    prior_job_id: str | None = None
    for event in read_events(base):
        if (event.queue_id == queue_id and event.item_index == item_index
                and event.job_id):
            prior_job_id = event.job_id
    if prior_job_id is None:
        return None, None
    for event in jobs.read_events(base):
        if (event.job_id == prior_job_id and event.kind == 'started'
                and event.start is not None and event.start.run_id):
            return event.start.run_id, prior_job_id
    return None, prior_job_id


def _auto_resume_block_reason(
    base: str | os.PathLike[str], run: QueueRun, entry: QueueEntry,
) -> str | None:
    """批次 3（`R-13.3` 修订）：付费 pending 项能否按原授权自动续跑。

    返回 ``None`` = 允许自动续跑（原授权 durable、原 execution 的代码/profile/
    argv 零漂移、原 run 身份完整可对证）；返回字符串 = 拒绝原因（落
    restart-confirmation，等显式确认，不自动猜）。只适用于有 durable
    queue/job 父记录的恢复执行；手工 CLI 与从未启动过的付费项不在范围。
    """
    if not run.start.paid_confirmed:
        return '队列创建时没有持久化付费确认（paid_confirmed=false）'
    prior_run_id, prior_job_id = _prior_execution_identity(
        base, run.queue_id, entry.index)
    if prior_job_id is None:
        return ('本项在服务重启前从未启动过（没有可对证的执行身份）；'
                '首次执行仍需显式确认')
    if prior_run_id is None:
        return (f'原 job={prior_job_id} 是无 run 身份的旧盘记录；'
                '不自动续跑、不按时间猜')
    prior_start = None
    for event in jobs.read_events(base):
        if event.job_id == prior_job_id and event.kind == 'started':
            prior_start = event.start
    if prior_start is None:
        return f'原 job={prior_job_id} 的 started 记录缺失，无法对证身份'
    if prior_start.code != prov.code_version():
        return (f'代码身份漂移：原 run 是 {prior_start.code}，当前是 '
                f'{prov.code_version()}；恢复必须 replan，不自动续跑')
    from novel.orchestrator import retired_formats
    if retired_formats.detect_retired_agent_backend(
            prior_start.model_dump(mode='json', by_alias=True)):
        return ('原 execution 属于已退役的 Agent CLI executor'
                '（retired_executor_backend）；不自动续跑、不静默降级，'
                '历史账原样保留只读')
    step_backends = [
        step.executor_backend for step in entry.item.steps
        if step.executor_backend is not None]
    if (prior_start.executor_backend is not None and step_backends
            and any(backend != prior_start.executor_backend
                    for backend in step_backends)):
        return 'executor backend/provider/模型/effort 与原 execution 不一致；不自动续跑'
    if (any(step.executor_backend for step in entry.item.steps)
            and prior_start.egress_authorized is not True):
        return '原 execution 未持久化正文外发授权；不自动续跑'
    return None


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

    ⚠⚠ exit 1（正式结果已落盘、结论不通过）与 exit 2/3/70（压根没跑成）在
    `JOB_STATE` 里已分成 `landed_red` / `failed`。人拿到「第 9 项后停下」
    这句话时要做的事，
    前者是**查看负结论并裁决**，后者是**修完重跑**——不说清就等于让人先去猜。
    实测：`stage6.hook` exit 1，`hook.json` 好好地在盘上，后三项被记成
    `not_run`，日志上与一次真崩一模一样。
    """
    if X.landed_but_red(failed.exit_code):
        return (f'同书第 {failed.index} 项（{failed.item.node}）**正式产物已落盘**、'
                f'但结论不通过（exit 1）；按 `R-13.4` 队列停在这里等人裁决，'
                f'本项没有启动。⚠ 这不是「没跑成」——先看那一层的屏与恢复指引，'
                f'不要当进程失败盲目重跑')
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
            # 批次 3：恢复续跑 = 新 job_id（execution）+ 原 run_id（业务 run）。
            prior_run_id, prior_job_id = _prior_execution_identity(
                base, queue_id, entry.index)
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
                # Stage4 三相与 Stage5 写作的人工审批授权均已随 2026-09-15
                # 全自动裁决退役（批次 B2/B3）：付费打开条件唯一化为机械事实
                # 与 run 启动授权（上传即授权本书全部付费节点），由 plan 与
                # producer 自身的关闭式校验把守；executor 内的机械授权仍由
                # `novel/api/routes/jobs.py` 的 prepare 面执行。
                jobs.start(
                    base, entry.item.node, entry.item.sid, entry.item.flags,
                    steps=entry.item.steps,
                    spawn=spawn, registry=job_registry, job_id=job_id,
                    run_id=prior_run_id,
                    recovered_from_job_id=prior_job_id,
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
             job_registry: jobs.JobRegistry,
             lane_sids: Sequence[str] | None = None) -> QueueRun:
    run = _one(base, queue_id, registry=queue_registry)
    selected = list(run.start.sids if lane_sids is None else lane_sids)
    if len(selected) != len(set(selected)) or any(
            sid not in run.start.sids for sid in selected):
        raise QueueConflict('queue startup lane_sids 必须是冻结队列 sid 的无重复子集')
    lane_threads = [threading.Thread(
        target=_run_lane,
        kwargs={
            'base': base, 'queue_id': queue_id, 'sid': sid, 'spawn': spawn,
            'queue_registry': queue_registry, 'job_registry': job_registry,
        },
        name=f'novel-queue-{queue_id}-{sid}', daemon=True,
    ) for sid in selected]
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
    from novel.work_units import check_workflow_write
    check_workflow_write(base)
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
          job_registry: jobs.JobRegistry = jobs.REGISTRY,
          _lane_sids: Sequence[str] | None = None) -> QueueRun:
    """Start a persistent queue runner in the background and return immediately."""
    from novel.work_units import check_workflow_write
    check_workflow_write(base)
    registry.acquire(queue_id)

    def worker() -> None:
        try:
            _execute(
                base, queue_id, spawn=spawn, queue_registry=registry,
                job_registry=job_registry, lane_sids=_lane_sids)
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
    from novel.work_units import WorkUnitRefused, check_workflow_write
    try:
        check_workflow_write(base)
    except WorkUnitRefused:
        # 旧或不可判工作流只提供历史读取，服务启动不得补锁、改事件或自动执行。
        # 读视图仍自行呈现损坏/未知；不因后台恢复不可用而阻止只读 UI 启动。
        return []
    from novel.orchestrator import admission

    unresolved_sids = frozenset(
        current.sid for current in admission.unresolved(base))
    jobs.reconcile(
        base, registry=job_registry, skip_sids=unresolved_sids)
    resumed: list[str] = []
    for record in records(base, registry=registry):
        if record.state in ('ok', 'landed_red', 'failed', 'cancelled'):
            continue
        eligible_sids = tuple(
            sid for sid in record.start.sids if sid not in unresolved_sids)
        if not eligible_sids:
            # durable gate 未闭合时整条 lane 保持原样：不做 artifact 判定、
            # 不追加 queue 状态、不启动。typed reconcile 后由人显式 resume。
            continue
        eligible = frozenset(eligible_sids)

        recovered_any = False
        for entry in record.entries:
            if entry.item.sid not in eligible:
                continue
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
            if entry.item.sid not in eligible:
                continue
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
        if len(eligible_sids) == len(current.start.sids):
            current_items = _current_plan_items(base, current.start)
        else:
            current_items = _current_plan_items_for_sids(
                base, current.start, eligible_sids)
        remaining = [entry.item for entry in current.entries
                     if entry.item.sid in eligible
                     and entry.state in ('pending', 'blocked', 'unknown', 'running')]
        if ([_command_surface(item) for item in remaining]
                != [_command_surface(item) for item in current_items]):
            targets = [entry for entry in current.entries
                       if entry.item.sid in eligible
                       and entry.state in ('pending', 'blocked', 'unknown', 'running')]
            if not targets and current.entries:
                targets = [entry for entry in reversed(current.entries)
                           if entry.item.sid in eligible][:1]
            reason = (
                '服务重启后重新规划得到的节点/命令集合与创建时未完成快照不同；'
                '旧队列不会增加、删除或替换命令，请重新预览并创建队列')
            for entry in targets:
                _append_state(
                    base, current.queue_id, entry.index, 'blocked',
                    waiting_on=WAITING_ON_REPLAN, note=reason)
            continue

        pending = [entry for entry in current.entries
                   if entry.item.sid in eligible and entry.state == 'pending']
        paid_pending = [entry for entry in pending
                        if any(step.paid for step in entry.item.steps)]
        if paid_pending:
            # 批次 3（R-13.3 修订，2026-08-29 用户裁定）：durable queue 父记录
            # 且身份完全闭合的同一 run，原付费授权可跨重启自动续跑（新
            # execution、原 run_id、recovered_from_job_id 联结）。任一前提缺失
            # 或漂移 → 仍落持久 restart-confirmation，等显式确认，不自动猜。
            blocked_any = False
            for entry in paid_pending:
                reason = _auto_resume_block_reason(base, current, entry)
                if reason is None:
                    continue
                blocked_any = True
                _append_state(
                    base, current.queue_id, entry.index, 'blocked',
                    waiting_on=WAITING_ON_RESTART_CONFIRMATION,
                    note=('服务重启后不会自动续跑：' + reason
                          + '；当前命令快照未变，重新展示冻结命令并经 CLI '
                            '`queue-resume --yes` 或 API `confirmed=true` 确认后'
                            '才能恢复'))
            if blocked_any:
                continue

        current = _one(base, record.queue_id, registry=registry)
        if any(entry.state in ('blocked', 'unknown', 'running')
               for entry in current.entries if entry.item.sid in eligible):
            continue
        if any(entry.state == 'pending' for entry in current.entries
               if entry.item.sid in eligible):
            start(
                base, current.queue_id, spawn=spawn, registry=registry,
                job_registry=job_registry, _lane_sids=eligible_sids)
            resumed.append(current.queue_id)
        elif recovered_any and current.state == 'ok':
            resumed.append(current.queue_id)
    return resumed


__all__ = [
    'HUMAN_NODES', 'WAITING_ON_INBOX',
    'WAITING_ON_STAGE2_EDIT_COMMIT',
    'WAITING_ON_STAGE2_EDIT_REVALIDATE',
    'WAITING_ON_STAGE4_DESIGN_REVIEW',
    'WAITING_ON_STAGE4_DESIGN_REPAIR_COMMIT',
    'WAITING_ON_STAGE4_BINDING_RESOLUTION',
    'WAITING_ON_STAGE4_STRUCTURE_REPLAN',
    'WAITING_ON_STAGE4_STRUCTURE_REVIEW',
    'WAITING_ON_STAGE4_PHASE_APPROVAL', 'WAITING_ON_STAGE4_REPLAN',
    'WAITING_ON_WRITING_APPROVAL', 'WAITING_ON_REVIEW',
    'WAITING_ON_STAGE5_CLOSURE', 'WAITING_ON_STAGE5_REVIEWER_REWRITE',
    'WAITING_ON_LEGACY',
    'WAITING_ON_RESUME_DECISION', 'WAITING_ON_RESTART_CONFIRMATION', 'WAITING_ON_REPLAN',
    'DETERMINISTIC_RECOVERY_WAITING_ON',
    'QueueRegistry', 'append_event', 'cancel', 'confirm', 'create', 'fold',
    'fold_counts', 'frozen_resume_commands', 'new_queue_id', 'nodes_up_to', 'plan', 'plan_book',
    'queue_path', 'read_events', 'records', 'resume_incomplete', 'run', 'start',
    'resume', 'resume_start', 'wait',
]
