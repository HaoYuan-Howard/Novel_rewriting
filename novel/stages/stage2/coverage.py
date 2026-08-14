#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
coverage.py —— beat 覆盖区间的**共享纯函数**（判据与读屏同源消费）。

【为什么提出来】`checks._uncovered`（判据侧）与 S-04 读屏的分区渲染（view 侧）
问的是同一个问题：「原文哪几段没被任何 beat 的 span 盖住」。两边各写一遍区间行走，
就是同一件事的第二个落点——阈值、去重、尾段处理任何一处分家，判据报的「漏」
与屏上画的「洞」就对不上了。

⚠ 与 `novel/core/spans.py` 不是一回事：那边管**锚点定位**（starts_with/ends_with
的游标行走），这边管定位完成之后的**覆盖分区**（beat 区间 → 连续分区与空隙）。

【口径】输入是 `[(beat_id, start, end)]`（已定位成功的那些，坐标是 Python 码点
半开区间），按 `(start, end)` 排序行走；重叠的 span 以游标 `max(cur, end)` 吸收
（与原 `_uncovered` 逐字节同口径）。
"""
from __future__ import annotations

from typing import Any

#: 一个分区：`(beat_id | None, start, end)`。`None` = 空隙（hole）。
Segment = tuple[Any, int, int]


def walk_segments(text_len: int, spans: list[tuple[Any, int, int]]) -> list[Segment]:
    """整章 `[0, text_len)` 的连续分区：beat 与空隙首尾相接、无缝覆盖。

    ⚠ **重叠 span 的吸收规则**：游标只前进（`max(cur, end)`），后一个 span 与
    游标重叠的部分被截掉（分区不回头）。零长残段不产出。
    """
    out: list[Segment] = []
    cur = 0
    for bid, s, e in sorted(spans, key=lambda t: (t[1], t[2])):
        if s > cur:
            out.append((None, cur, s))
        if e > cur:
            out.append((bid, max(cur, s), e))
            cur = e
    if text_len > cur:
        out.append((None, cur, text_len))
    return out


def hole_intervals(text_len: int, spans: list[tuple[Any, int, int]],
                   min_chars: int) -> list[tuple[int, int]]:
    """≥ `min_chars` 的空隙。**几个字的空隙通常只是过渡句**——
    是漏抓还是过渡，交人工看摘录判断，不自动定性（判据侧的原话）。
    """
    return [(s, e) for bid, s, e in walk_segments(text_len, spans)
            if bid is None and e - s >= min_chars]
