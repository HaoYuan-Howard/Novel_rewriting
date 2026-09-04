#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 Global Commander 席位 reducer：blocker + 工作包形状 → 本席 purpose。

席位语义只在这里定义一次，``agent.py`` 的预留调用、``reviewer_tasks`` 的组包
分支与 ``stage2_review_ledger.reserve`` 的席位规则都消费同一张表。这样
“第四席只读终审”“订正不得占第四席”不会在三处各长出一套条件分支。

它是纯函数：不读盘、不调用模型、不知道 provider session。
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from contracts.invariants import (
    STAGE2_COMMANDER_MAX_EDIT_SEAT, STAGE2_COMMANDER_MAX_SEATS,
    STAGE2_COMMANDER_SEAT_PURPOSES,
)
from novel.orchestrator import queue


#: 首次完整审阅的 typed 提交项 id；其它 Stage2 节点选项都属于订正。
FULL_REVIEW_OPTION_ID = 'node:stage2.global.review'
FINAL_REVIEW_APPROVAL_OPTION_ID = 'approval:stage2_final_review'
FINAL_REVIEW_REOPEN_OPTION_ID = 'node:stage2.global.final-reopen'


@dataclass(frozen=True)
class SeatDecision:
    """一席的语义与它能写什么。"""

    purpose: str
    read_only: bool
    max_ordinal: int

    def __post_init__(self) -> None:
        if self.purpose not in STAGE2_COMMANDER_SEAT_PURPOSES:
            raise ValueError(f'未登记的 Stage2 席位 purpose：{self.purpose!r}')


def is_final_review_blocker(waiting_on: str | None) -> bool:
    return waiting_on == queue.WAITING_ON_STAGE2_FINAL_REVIEW


def seat_for(
    waiting_on: str,
    submission_option_ids: Sequence[str],
    *,
    prereview: bool = False,
) -> SeatDecision:
    """按当前 blocker 与可提交项决定这一席的 purpose。

    - Blueprint 期间的只读预审恒为 ``prereview``；
    - 终审 blocker 恒为 ``final_review``（只能 confirm 或 reopen，不能编辑）；
    - 中断恢复的 reviewed-edit 纠正恒为 ``correction``；
    - 普通 review blocker 下，只有当唯一提交项是完整 ``blocking_scope`` 审阅时才是
      ``review``；任何 GlobalEdit / UNVER 处置 / 隔离选项都意味着订正席。
    """
    if prereview:
        if waiting_on != queue.WAITING_ON_STAGE2_REVIEW:
            raise ValueError('Stage2 只读预审只服务 approval:stage2-global-review')
        return SeatDecision(
            purpose='prereview', read_only=True,
            max_ordinal=STAGE2_COMMANDER_MAX_EDIT_SEAT)
    if waiting_on == queue.WAITING_ON_STAGE2_FINAL_REVIEW:
        return SeatDecision(
            purpose='final_review', read_only=True,
            max_ordinal=STAGE2_COMMANDER_MAX_SEATS)
    if waiting_on == queue.WAITING_ON_STAGE2_EDIT_CORRECTION:
        return SeatDecision(
            purpose='correction', read_only=False,
            max_ordinal=STAGE2_COMMANDER_MAX_EDIT_SEAT)
    if waiting_on == queue.WAITING_ON_STAGE2_REVIEW:
        ids = tuple(submission_option_ids)
        if ids == (FULL_REVIEW_OPTION_ID,):
            return SeatDecision(
                purpose='review', read_only=True,
                max_ordinal=STAGE2_COMMANDER_MAX_EDIT_SEAT)
        return SeatDecision(
            purpose='correction', read_only=False,
            max_ordinal=STAGE2_COMMANDER_MAX_EDIT_SEAT)
    raise ValueError(f'{waiting_on!r} 不是 Stage2 Commander 席位 blocker')


def option_ids(submission_options: Sequence[object]) -> list[str]:
    """从 immutable workpack 的 submission_options 取出 id 列表（容忍 fake 注入）。"""
    ids: list[str] = []
    for option in submission_options:
        if isinstance(option, dict) and isinstance(option.get('id'), str):
            ids.append(option['id'])
    return ids


__all__ = [
    'FINAL_REVIEW_APPROVAL_OPTION_ID', 'FINAL_REVIEW_REOPEN_OPTION_ID',
    'FULL_REVIEW_OPTION_ID', 'SeatDecision', 'is_final_review_blocker',
    'option_ids', 'seat_for',
]
