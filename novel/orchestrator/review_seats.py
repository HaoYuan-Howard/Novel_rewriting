#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 两位 Commander 的动作分类；基础与人工追加额度只由 work_units 持久化。

Global 与 Blueprint 分属独立工作单元，每个工作单元可以连续审阅和订正。
这里不读盘、不调用模型，也不依据候选版本或 blocker 重新授予预算。
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from contracts.invariants import STAGE2_COMMANDER_SEAT_PURPOSES
from novel.orchestrator import queue


#: 首次完整审阅的 typed 提交项 id；其它 Stage2 节点选项都属于订正。
FULL_REVIEW_OPTION_ID = 'node:stage2.global.review'


@dataclass(frozen=True)
class SeatDecision:
    """一席的语义与它能写什么。"""

    purpose: str
    read_only: bool

    def __post_init__(self) -> None:
        if self.purpose not in STAGE2_COMMANDER_SEAT_PURPOSES:
            raise ValueError(f'未登记的 Stage2 席位 purpose：{self.purpose!r}')


def seat_for(
    waiting_on: str,
    submission_option_ids: Sequence[str],
    *,
    prereview: bool = False,
) -> SeatDecision:
    """分类现役动作，不另设轮次上限；不存在预审或额外只读终审。"""
    if prereview:
        raise ValueError('Stage2 预审已退役；必须先接受 Global 再生产 Blueprint')
    if waiting_on == queue.WAITING_ON_STAGE2_FINAL_REVIEW:
        raise ValueError('Stage2 额外只读终审已退役；close-stage2 只核验已有的两份接受')
    if waiting_on == queue.WAITING_ON_STAGE2_BLUEPRINT_REVIEW:
        return SeatDecision(
            purpose='blueprint_review', read_only=False)
    if waiting_on == queue.WAITING_ON_STAGE2_EDIT_CORRECTION:
        return SeatDecision(
            purpose='correction', read_only=False)
    if waiting_on == queue.WAITING_ON_STAGE2_REVIEW:
        return SeatDecision(
            purpose='review', read_only=False)
    raise ValueError(f'{waiting_on!r} 不是 Stage2 Commander 席位 blocker')


def option_ids(submission_options: Sequence[object]) -> list[str]:
    """从 immutable workpack 的 submission_options 取出 id 列表（容忍 fake 注入）。"""
    ids: list[str] = []
    for option in submission_options:
        if isinstance(option, dict) and isinstance(option.get('id'), str):
            ids.append(option['id'])
    return ids


__all__ = [
    'FULL_REVIEW_OPTION_ID', 'SeatDecision',
    'option_ids', 'seat_for',
]
