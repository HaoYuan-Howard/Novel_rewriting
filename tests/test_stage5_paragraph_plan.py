#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage5 段落 slot 计划、精确反馈与 reflow shadow（P1-1，零 wire）。"""
from __future__ import annotations

import pytest

from novel.errors import ParagraphPlanInfeasible
from novel.stages.stage5 import measure as M


def _book(avg_paragraph_chars: int = 25):
    return M.Book(
        title='段落计划测试', ctx={},
        measurable={
            'avg_paragraph_chars': avg_paragraph_chars,
            'paragraph_sep_chars': 1,
        },
        source_text={}, secrets=[], characters=[])


def _entry(budget: int, scene_budgets: list[int]):
    return {
        'budget_chars': budget,
        'scenes': [
            {'slot': f's{index:02d}', 'budget_chars': value}
            for index, value in enumerate(scene_budgets, 1)
        ],
    }


# ── paragraph_slot_plan ─────────────────────────────────────────────────────

def test_plan_sums_exactly_to_the_clamped_target():
    book = _book()
    entry = _entry(1200, [500, 300, 400])
    plan = M.paragraph_slot_plan(entry, book)
    lo, hi = M.target_paragraphs(entry, book)
    assert sum(plan) == min(max(int(round(1200 / 26)), lo), hi)
    assert all(count >= 1 for count in plan)
    # 预算权重大的场分到更多段（largest-remainder 的方向性）。
    assert plan[0] >= plan[1] and plan[2] >= plan[1]


def test_plan_is_deterministic_and_ties_break_by_slot_order():
    book = _book()
    entry = _entry(1000, [200, 200, 200, 200])
    plan = M.paragraph_slot_plan(entry, book)
    assert plan == M.paragraph_slot_plan(entry, book)
    lo, hi = M.target_paragraphs(entry, book)
    assert sum(plan) == min(max(int(round(1000 / 26)), lo), hi)
    assert all(count >= 1 for count in plan)


def test_infeasible_plan_is_refused_before_any_wire():
    book = _book()
    entry = _entry(400, [10] * 40)  # 40 场必然超合法段数上限
    with pytest.raises(ParagraphPlanInfeasible, match='paragraph_plan_infeasible'):
        M.paragraph_slot_plan(entry, book)


def test_chapter_without_scenes_returns_whole_chapter_target():
    book = _book()
    assert M.paragraph_slot_plan({'budget_chars': 1200, 'scenes': []}, book) == [
        min(max(int(round(1200 / 26)), 3), 10 ** 6)]


# ── reflow_shadow ───────────────────────────────────────────────────────────

def test_reflow_shadow_preserves_non_whitespace_bytes():
    text = '第一段比较短。\n第二段也很短。\n第三段有一点点长，里面说了很多事情。\n第四段收尾。'
    shadow = M.reflow_shadow(text, target=3)
    assert shadow is not None
    assert len([line for line in shadow.split('\n') if line.strip()]) == 3
    assert ''.join(shadow.split()) == ''.join(text.split())


def test_reflow_shadow_splits_at_sentence_ends_only():
    text = ('这一段非常长，写了很多内容，第一句说到这里。'
            '然后第二句继续说其他的事情，还有更多的细节描写。最后一句收束全段。')
    shadow = M.reflow_shadow(text, target=3)
    assert shadow is not None
    lines = [line for line in shadow.split('\n') if line.strip()]
    assert len(lines) == 3
    assert all(line[-1] in '。！？…!?」』”' for line in lines)
    assert ''.join(shadow.split()) == ''.join(text.split())


def test_reflow_shadow_never_merges_across_dialogue():
    text = '他说。\n「你好。」\n另一句普通叙述。'
    # 任何合并都会把对白段卷进去 ⇒ 不可行，且必须与 None 区分。
    assert M.reflow_shadow(text, target=2) is None


def test_reflow_shadow_reports_infeasible_instead_of_forcing():
    # 目标段数高于「段内可拆分边界」的总和时，如实返回 None。
    assert M.reflow_shadow('只有一句。', target=3) is None
