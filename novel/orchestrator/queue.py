#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
queue.py —— 批量队列（`R-13`）。**一次排入多本书，人只在需要裁决的地方介入。**

【`plan()` 是纯函数】给它一批 sid 与一个目标 stage，它算出「这条链长什么样」——
**不跑、不写、不发请求**。于是「排这一批会花多少次调用、会写哪些文件、
命令逐条长什么样」可以 0 成本地摊开给人看，而那正是 `R-13.3` 要人确认的东西。

【⚠ 队列本身不产生新的判定】`R-13.6` → `R-11.4`：**造第二个判官就是造第二个落点。**
这条**由结构保证**：本模块不许 import `novel.report`，由 `tests/test_layering.py`
的一条 AST 测试钉着。队列读的是完成度（`state.py`），而完成度读的是盘上有什么。

【为什么「停在那里」不是保守】批量的风险与单本不同：
**单本跑错人当场看得见；批量跑错是一批产物同时坏掉且没人在看。**
所以 `R-13.4` 的「任一项失败 → 停在那里」是这条能力**成立的前提**，不是一个保守选项。
"""
from __future__ import annotations

import os
from collections.abc import Iterable, Sequence

from contracts.invariants import NODES, STAGES
from contracts.schema.orchestration import QueueCounts, QueueItem, QueuePlan
from novel.errors import ApprovalRequired
from novel.orchestrator import state as st
from novel.orchestrator.nodes import build_steps

#: 人做的那几个动作。**队列永不自动排它们**——`stage6.approve` 换一条候选、
#: `--apply-edit` 改一个字，都是「人看过之后的裁决」，机器替不了。
#: ⚠ 它们进 `blocked` 而不是被跳过：被跳过的闸门在界面上与「不需要它」长得一样。
HUMAN_NODES: frozenset[str] = frozenset(
    {n for n in NODES if n.endswith('.approve')} | {'stage0.edit'})

#: 待人工清单非空时挡队列的那个理由（`INBOX_KIND` 的 `invalid`）。
WAITING_ON_INBOX = 'inbox:invalid'


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
        mode = spec.modes[0] if spec.modes else None
        why_blocked = stuck
        if why_blocked is None and node in HUMAN_NODES:
            if not _gate_is_reachable(ov, spec.stage):
                continue                             # 还没轮到它，不是「在等人」
            why_blocked = node                       # 在等这一步的那个人
        if why_blocked is None:
            ghosts = _upstream_invalid(ov, spec.stage)
            if ghosts:
                why_blocked = WAITING_ON_INBOX

        verdict, why = st.resume_verdict(ov, node)
        if why_blocked is None and verdict == st.RESUME_SKIP:
            continue                                 # 已存在即跳过

        item = QueueItem(
            sid=sid, node=node, mode=mode, flags=[],
            steps=build_steps(node, sid, base=base, mode=mode,
                              follow_check=follow_check),
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
        return f'上游有被闸门拦下的稿要人处理（0 成本提升路是 `--revalidate`）；{verdict_why}'
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
                     paid_items=sum(1 for it in items
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
    for it in p.items:
        for s in it.steps:
            if s.paid:
                lines.append(f'  $ {" ".join(s.argv)}')
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


__all__ = ['HUMAN_NODES', 'WAITING_ON_INBOX', 'confirm', 'fold_counts',
           'nodes_up_to', 'plan', 'plan_book']
