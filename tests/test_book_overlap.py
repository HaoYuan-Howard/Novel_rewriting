#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`tools/book_overlap.py` 的守卫。

只钉**它自己那两个踩过的陷阱**与**口径来源**，不重测 `overlap_ratio`
（那是 stage5 的实现，有自己的测试）。
"""
from __future__ import annotations

import pytest

from contracts.invariants import BOOK_BLOCK_SEP
from tools.book_overlap import back_map, split_book


def _book(*chapters: str) -> str:
    """按 `assemble_txt` 的形态拼一本：文案 + (章号块 + 正文块)*。"""
    parts = ['文案一句。']
    for i, body in enumerate(chapters, 1):
        parts += [str(i), body]
    return BOOK_BLOCK_SEP.join(parts) + chr(10)


def test_a_chapter_body_that_has_blank_lines_is_not_cut_at_its_first_paragraph():
    """⚠ 正文自己也用 `BOOK_BLOCK_SEP` 分段。

    首版按「数字块的下一块就是正文」切，量出来每章只有 9~24 字、重合 1.000
    ——**每一章都只取到了第一段**。
    """
    body = BOOK_BLOCK_SEP.join(['第一段。', '第二段。', '第三段。'])
    got = split_book(_book(body, '另一章。'))
    assert got[1] == body, '正文被切在第一段了'
    assert got[2] == '另一章。'


def test_a_bare_number_inside_a_body_is_not_mistaken_for_a_chapter_head():
    """⚠ 正文里会有单独成段的纯数字（灵网跟帖、楼层号）。

    只认**恰好等于「上一章号 + 1」**的数字块，依据是产品定义：
    输出章号从 1 连续。否则一个楼层号就会把一章劈成两半。
    """
    body = BOOK_BLOCK_SEP.join(['他点开帖子。', '999', '楼上说得对。'])
    got = split_book(_book(body))
    assert len(got) == 1, '正文里的裸数字被当成章号了'
    assert got[1] == body


def test_the_hook_block_has_no_chapter_number_and_never_enters_the_table():
    """文案（`stage6.hook`）在第一个章号之前，它不是任何一章。"""
    got = split_book(_book('正文。'))
    assert list(got) == [1]
    assert '文案' not in got[1]


def test_the_reverse_table_comes_from_subs_not_from_characters():
    """⚠⚠ 反转的必须是 `subs`——那才是 `assemble_txt` 用的那一张。

    从 `characters` 重拼会漏掉 `surname_map` 派生项与人工 `extra_subs`：
    实测无情道 23 章里 ch18 一章因此对不上（0.532 vs stage5 的 0.547）。
    """
    nm = {'subs': {'旧甲': '新甲', '旧乙': '新乙'},
          'kept': ['师姐'],
          # 故意与 subs 不一致：若实现去读它，这条断言就会红
          'characters': [{'renamed': True, 'old_canonical': '不该被读到',
                          'new_canonical': '也不该被读到', 'aliases': []}]}
    back, kept = back_map(nm)
    assert back == {'新甲': '旧甲', '新乙': '旧乙'}
    assert kept == ('师姐',)


def test_a_non_invertible_mapping_is_a_hard_stop_not_a_silent_pick():
    """两个旧名换成同一个新名 ⇒ 反转不回去。

    ⚠ 静默取其一会让读数**悄悄偏低**而没有任何人知道——那正是本仓
    「不知道不能记成匹配」要防的形状。
    """
    with pytest.raises(SystemExit, match='不可逆'):
        back_map({'subs': {'旧甲': '同一个新名', '旧乙': '同一个新名'}})
