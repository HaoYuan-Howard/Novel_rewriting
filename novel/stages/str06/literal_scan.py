#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""literal_scan —— 共享字面扫描：单遍、长名优先、认领区间（F6）。

【为什么是共享纯函数】
Stage5 hook validator（「认领已释放称谓后不得再出现 withheld 字面」）与 Stage7
ApplyNames（单遍替换、残留检查跳过认领区间）需要**同一条**扫描语义。各自实现
就会出现「validator 看不见的区间被 Apply 替换了」这类裂缝。

【认领区间语义】
一次扫描按字面长度降序（同长按字典序）组成 alternation，`re.sub` 天然单遍——
文本中每个位置只被**最长的**匹配认领一次，不存在「裴书远被裴先拆掉」。
区间是左闭右开 `[start, end)`；重叠不可能出现（单遍扫描的每字符只消费一次）。
"""
from __future__ import annotations

import re
from typing import NamedTuple


class Claim(NamedTuple):
    """一条认领区间：命中的字面与其在文本中的位置。"""

    literal: str
    start: int
    end: int

    def covers(self, start: int, end: int) -> bool:
        return self.start <= start and end <= self.end


def _ordered(literals) -> tuple[str, ...]:
    uniq = sorted(set(literals), key=lambda s: (-len(s), s))
    if any(not s for s in uniq):
        raise ValueError('字面扫描不接受空字面')
    return tuple(uniq)


def claim_spans(text: str, literals) -> tuple[Claim, ...]:
    """单遍长名优先扫描：返回全部认领区间（按出现位置排序）。"""
    ordered = _ordered(literals)
    if not ordered:
        return ()
    pattern = re.compile('|'.join(re.escape(s) for s in ordered))
    claims: list[Claim] = []
    for match in pattern.finditer(text):
        claims.append(Claim(match.group(0), match.start(), match.end()))
    return tuple(claims)


def uncovered_occurrences(text: str, literals, claimed: tuple[Claim, ...] = ()) -> list[Claim]:
    """字面在文本中的出现，且不在已给认领区间内——「应替换/应隐藏而残留」的判定面。

    claimed 通常是一次更早的扫描（如已释放称谓/已替换区间的认领账）。两次扫描
    用同一长名优先语义：更长的字面先认领，短字面的残余出现才算未覆盖。
    """
    protected = tuple(claimed)
    problems: list[Claim] = []
    for claim in claim_spans(text, literals):
        if any(p.covers(claim.start, claim.end) for p in protected):
            continue
        problems.append(claim)
    return problems


def apply_replacements(text: str, replacements: dict[str, str]) -> tuple[str, tuple[Claim, ...]]:
    """单遍替换：键按长名优先认领，值替换后**不再参与**本次扫描（无链式替换）。"""
    ordered = _ordered(replacements)
    if not ordered:
        return text, ()
    pattern = re.compile('|'.join(re.escape(s) for s in ordered))
    claims: list[Claim] = []

    def _sub(match: re.Match) -> str:
        claims.append(Claim(match.group(0), match.start(), match.end()))
        return replacements[match.group(0)]

    return pattern.sub(_sub, text), tuple(claims)
