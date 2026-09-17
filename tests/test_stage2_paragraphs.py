#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 2 的段落编号表 —— 下发、回取、反投影**同一张表**。

⚠⚠ 本文件守的是一类**不会报错**的病：渲染时把某一行算成第 12 段、回取时算成第 13 段，
产出的是一段错位的原文偏移，而 `SourceSpan` 照样构造得出来、判据照样绿。
⇒ 这里的断言一律是「两边算出来的是同一个东西」，不是「各自看起来合理」。
"""
from __future__ import annotations

import pytest

from novel.core.spans import content_only
from novel.stages.stage2.shared.paragraphs import (
    LINE_FORMAT,
    ParagraphIndexError,
    paragraph_at,
    paragraph_range_of,
    paragraph_slice,
    paragraph_spans,
    render_numbered,
    span_of,
)

#: 三段正文 + 空行分隔，形状照本仓语料（「每句一段」，段间一个空行）。
CHAPTER = '甲推门进屋。\n\n乙抬头看了他一眼。\n\n[你来做什么?]甲问。\n'

#: 段间**没有**空行的排版（同样合法，两种都要过）。
DENSE = '第一句。\n第二句。\n第三句。'


def test_paragraphs_are_non_empty_lines_and_carry_real_offsets():
    assert paragraph_spans(CHAPTER) == [(0, 6), (8, 17), (19, 30)]
    for start, end in paragraph_spans(CHAPTER):
        chunk = CHAPTER[start:end]
        assert '\n' not in chunk, '区间不许跨行——那会把下一段吃进来'
        assert chunk.strip() == chunk.strip(), '区间逐字取自原文，不做 strip'
        assert chunk.strip(), 'strip 后为空的行不该占编号'


def test_blank_lines_do_not_take_a_number_but_do_take_coordinates():
    """空行只是排版分隔：不占编号，**但占坐标**。

    ⚠ 这是本模块最容易写错的一处——游标忘了为空行前进，后面每一段的偏移全错，
    而它不会报错。
    """
    assert len(paragraph_spans(CHAPTER)) == 3
    assert CHAPTER[6:8] == '\n\n', '第 1 段与第 2 段之间确实隔着两个换行'
    assert paragraph_spans(DENSE) == [(0, 4), (5, 9), (10, 14)]


def test_rendered_numbering_is_derived_from_the_same_table():
    """⭐ 渲染与区间**同源**：编号块里第 n 行的正文逐字等于 `paragraph_spans` 的第 n 段。

    ⚠ 守卫调的是生产那一个落点（`LINE_FORMAT`），不在这里重拼一份格式字符串——
    重拼的那一份哪天与生产分家，本条会继续绿着（`docs/LESSONS.md` 第 21 条）。
    """
    for text in (CHAPTER, DENSE):
        table = paragraph_spans(text)
        lines = render_numbered(text).split('\n')
        assert len(lines) == len(table), '渲染行数必须等于段数——空行不得下发'
        for n, (start, end) in enumerate(table, start=1):
            assert lines[n - 1] == LINE_FORMAT.format(n=n, text=text[start:end])


def test_the_marker_does_not_collide_with_the_bracket_this_corpus_uses_for_speech():
    """本语料把台词引号写成 `[...]`（盘上三本 36 章共 127 对）⇒ 标记不能也用方括号。

    ⚠ 断言的是「标记字符不出现在**正文**里」，不是「不出现在渲染块里」——
    渲染块当然含标记，那正是它的作用。
    """
    assert '[你来做什么?]' in CHAPTER, '样本必须真的带着会撞的那种括号'
    marker = LINE_FORMAT.format(n=1, text='').strip()
    assert not (set(marker) & set(CHAPTER)), \
        f'编号标记 {marker!r} 的字符在正文里出现过 —— 模型要在「第几段」与「台词」之间猜'


def test_span_of_and_paragraph_range_of_are_inverse():
    """⭐ 下发 → 回取 → 反投影，三步必须回到同一个段号区间。"""
    table = paragraph_spans(CHAPTER)
    for a in range(1, len(table) + 1):
        for b in range(a, len(table) + 1):
            start, end = span_of(CHAPTER, a, b)
            assert (start, end) == (table[a - 1][0], table[b - 1][1])
            assert paragraph_range_of(CHAPTER, start, end) == (a, b)


def test_multi_paragraph_spans_swallow_the_separators_and_still_normalize():
    """跨段区间**含中间的换行与空行** —— 相邻两拍因此首尾相接。

    `SourceSpan.normalized` 走 `content_only`，分隔符会被滤掉 ⇒ 不影响判据，
    而 `min_length=1` 那条约束仍然满足。
    """
    start, end = span_of(CHAPTER, 1, 3)
    assert CHAPTER[start:end] == '甲推门进屋。\n\n乙抬头看了他一眼。\n\n[你来做什么?]甲问。'
    assert content_only(CHAPTER[start:end])


def test_a_range_that_starts_mid_paragraph_still_projects_back_by_intersection():
    """反投影按**相交**算，不按包含。

    旧形状的锚点是句子级的、两端本来就落在段内；按「包含」算会把首尾两段整个丢掉，
    而 `response_shape` 那份基线的用途正是「不许减少已有内容」。
    """
    assert paragraph_range_of(CHAPTER, 2, 12) == (1, 2)


@pytest.mark.parametrize('args,probe', [
    ((0, 1), '越界'),
    ((1, 4), '越界'),
    ((3, 2), '倒序'),
    ((1.0, 2), '不是整数'),
])
def test_bad_paragraph_numbers_are_refused_with_a_cause_not_an_estimate(args, probe):
    """定位不到就抛，**绝不退回估算**——估算方向会整个反过来。

    消息明确说明失败根因，供当前编译/审阅恢复反馈，不用估算坐标掩盖越界。
    """
    with pytest.raises(ParagraphIndexError, match=probe):
        span_of(CHAPTER, *args)


def test_paragraph_slice_locates_one_exact_evidence_paragraph():
    """证据段定位只返回该段的准确字符边界。"""
    assert paragraph_slice(CHAPTER, 2) == (8, 17)
    assert CHAPTER[slice(*paragraph_slice(CHAPTER, 2))] == '乙抬头看了他一眼。'
    with pytest.raises(ParagraphIndexError, match='越界'):
        paragraph_slice(CHAPTER, 4)


def test_offsets_inside_the_gap_belong_to_no_paragraph():
    """段间空隙不属于任何一段 —— 三态里的「不知道」，不许就近归给邻段。"""
    assert paragraph_at(CHAPTER, 0) == 1
    assert paragraph_at(CHAPTER, 5) == 1
    assert paragraph_at(CHAPTER, 6) is None
    assert paragraph_at(CHAPTER, 8) == 2
    assert paragraph_at(CHAPTER, len(CHAPTER) - 1) is None


def test_an_empty_chapter_has_no_paragraphs_and_refuses_every_index():
    for text in ('', '\n\n\n', '   \n  \n'):
        assert paragraph_spans(text) == []
        assert render_numbered(text) == ''
        with pytest.raises(ParagraphIndexError, match='越界'):
            span_of(text, 1, 1)
