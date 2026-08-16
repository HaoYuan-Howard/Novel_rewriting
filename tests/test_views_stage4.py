#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_views_stage4.py —— S-07「计划 ↔ 写作上下文」那一屏的组装。

本文件盯三件事：①真计划与恒等基线**两态都有内容**（`04_delivery.md:133`）；
②`writer_target` 与写手**同一把尺子**（`prompts.target_band`）；
③恒等基线**现算不落盘**（`run_identity` 那条 CLI 路会写盘，view 不许）。

⚠ 沙箱一律 `tempfile.mkdtemp()`；**原语料一个字节都不许动**。
"""
from __future__ import annotations

import os

import pytest

from novel.errors import UpstreamMissing
from novel.stages.stage4.measure import IDENTITY_GOAL
from novel.stages.stage5.prompts import target_band
from novel.views.stage4 import stage4_view
from tests import corpus, sandbox

INPUTS = ('stage1/main_text.json', 'stage2/global_grounding.json',
          'stage2/chapter_blueprints.json',
          'stage4/plan_chapters.json', 'stage4/writer_context.json')


def books() -> list[str]:
    """同时具备当前计划与蓝图的书。"""
    out = []
    for book in sorted({a.book_dir for a in corpus.iter_artifacts()
                        if a.rel == 'stage4/plan_chapters.json'}):
        plan = sandbox.cut(book, 'stage4/plan_chapters.json')
        bp = sandbox.cut(book, 'stage2/chapter_blueprints.json')
        if plan is not None and bp is not None:
            out.append(book)
    return out


pytestmark = [
    pytest.mark.corpus,
    pytest.mark.skipif(
        not books(), reason='盘上没有可读的 stage4 语料——这几条**没在查**，不是「查过没事」'),
]


@pytest.fixture(params=books(), ids=lambda b: b[:10])
def box(request):
    s = sandbox.Sandbox(request.param, INPUTS)
    yield s
    s.close()


def _tree(root) -> set:
    out = set()
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            st = os.stat(p)
            out.add((os.path.relpath(p, root), st.st_size, st.st_mtime_ns))
    return out


def test_the_real_plan_forwards_goals_and_sums_budgets(box):
    plan = sandbox.cut(box.book, 'stage4/plan_chapters.json')
    o = plan['output_chapters'][0]['output_ordinal']
    v = stage4_view(box.root, box.sid, o)
    assert not v.identity_plan
    pc = plan['output_chapters'][0]
    assert v.plan_chapter.chapter_goal.model_dump(mode='json') == pc['chapter_goal']
    assert [b.from_beats for b in v.plan_chapter.beats] == \
        [b['from_beats'] for b in pc['segments']]
    assert [b.slot for b in v.plan_chapter.beats] == [b['slot'] for b in pc['segments']]
    row = next(r for r in v.index if r.output_ordinal == o)
    want = sum(b['budget_chars'] for b in pc['segments'] if b.get('budget_chars'))
    assert row.budget_chars == (want or None)


def test_writer_target_walks_the_same_ruler_as_the_writer(box):
    """⭐ `writer_target` 必须来自 `prompts.target_band`——写手按 A 写、屏上显 B，
    谁也不会出声。"""
    v = stage4_view(box.root, box.sid, v0 := 1)
    assert v.ctx_chapter is not None
    assert v.writer_target == target_band(v.ctx_chapter.model_dump(by_alias=True))
    assert v.ordinal == v0


def test_without_a_plan_the_screen_shows_the_identity_baseline(box):
    """⭐ `04_delivery.md:133`：没跑过 stage4 时**也有内容**——恒等基线，且现算不落盘。"""
    (box.dir / 'stage4' / 'plan_chapters.json').unlink()
    before = _tree(box.root)
    v = stage4_view(box.root, box.sid, 1)
    assert v.identity_plan
    assert v.reconstruction_strategy == IDENTITY_GOAL
    assert v.plan_chapter.chapter_goal.rationale == IDENTITY_GOAL, \
        '恒等的 goal 是显式标记，不是真决策'
    assert v.plan_chapter.beats and v.ctx_chapter is not None
    assert v.plan_provenance is None and v.ctx_provenance is None
    assert _tree(box.root) == before, '恒等基线把东西写到盘上了（那是 --identity 的事）'


def test_a_missing_ctx_does_not_take_the_plan_down(box):
    """计划在而 ctx 不在 ⇒ 右半边 `null`，左半边照常（两半各自失败）。"""
    (box.dir / 'stage4' / 'writer_context.json').unlink()
    v = stage4_view(box.root, box.sid, 1)
    assert not v.identity_plan and v.plan_chapter.beats
    assert v.ctx_chapter is None and v.writer_target is None
    assert v.ctx_state.exists is False
    row = v.index[0]
    assert row.span_chars is None and row.hidden is None, 'ctx 不在还给出了 ctx 侧的数'


def test_an_ordinal_outside_the_plan_is_a_terminal_state(box):
    with pytest.raises(UpstreamMissing):
        stage4_view(box.root, box.sid, 999)


def test_the_view_writes_nothing(box):
    before = _tree(box.root)
    stage4_view(box.root, box.sid, 1)
    stage4_view(box.root, box.sid, 1)
    assert _tree(box.root) == before
    assert not (box.root / '_service').exists()
    assert len(before) >= 4, f'只扫到 {len(before)} 个文件——沙箱多半没建起来'
