#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_core.py —— `novel/core/` 里那些**确定性算子**。

这一份里的每一条都钉着**一次实测的账**，而不是「函数能跑」：
原型的这些函数是逐字搬过来的，搬错一个字符类、少一个前瞻断言，
结果不会报错——只会让某条判据在某类输入上**静默地不再拦**。
所以测的一律是**边界与反例**，不是 happy path。

（2026-10-04 清理批：归一链 `normalize.py`、人名专名表 `naming.py`、
全局证据槽 `evidence.py` 整模块，以及 `text.py` 的旧链探针
（`locate_norm`／`split_by_speech_tag`／`min_hamming`／`render_book`／
`TERMINAL_PUNCT` 等）随旧链退役整批删除，其用例同批移除；
`content_equivalent_slice` 的现役用例见 `test_str06_quote_canon.py`。）
"""
from __future__ import annotations

from novel.core.text import quote_count, window_overlap


# ── 重合率与引语条数 ──────────────────────────────────────────────────────

def test_window_overlap_says_unmeasurable_not_zero():
    """⭐ 短于一个窗口 ⇒ **`None`（量不了）**，不是 `0.0`（没重合）。两者必须分得开。"""
    assert window_overlap('太短了', '一' * 100) is None
    assert window_overlap('一' * 30, '') is None, '没有源也是量不了'
    assert window_overlap('一' * 30, '一' * 40) == 1.0
    # 标点改写不能绕过：`norm` 空间里算
    assert window_overlap('甲，乙。' * 20, '甲乙' * 20) == 1.0


def test_quote_count_counts_lines_not_characters():
    """⭐ 它与 `dialogue_ratio` 都要：那一个量**占多少字**，这一个量**说了几句**。

    台词被压成转述时前者可以纹丝不动（叙述里复述一遍同样长），后者掉一半
    ——原型正是靠它抓到「闭卷把台词压成转述」这条效应量最大的跨书结论。
    """
    assert quote_count('「甲。」他说。「乙。」') == 2
    assert quote_count('他说甲，然后又说乙。') == 0
    assert quote_count('“甲。”和「乙。」') == 2, '四种引号体全表都数（归一前后都要成立）'
