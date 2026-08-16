#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_views_stage6.py —— S-11「开篇文案」那一屏的组装。

view 只做三件现算（`chars` / `index` / 候选级 `approved`），本份逐件验证 +
异常面 + 纯读。判据行为归 `tests/test_stage6.py`，HTTP 行为归 `test_api_screens.py`。

⚠ 沙箱一律 `tempfile.mkdtemp()`；**原语料一个字节都不许动**。
"""
from __future__ import annotations

import os

import pytest

from novel.core.measure import measure
from novel.errors import UpstreamMissing
from novel.views.stage6 import stage6_view
from tests import corpus, sandbox

#: 组装这一屏只要产物自己；上游缺席只影响 freshness（ghosts 点名），不拦读。
INPUTS = ('stage1/hook_copy.json', 'stage2/global_grounding.json',
          'stage3/style_grounding.json', 'stage4/writer_context.json',
          'stage6/hook.json')


def books() -> list[str]:
    return sorted({a.book_dir for a in corpus.iter_artifacts()
                   if a.rel == 'stage6/hook.json'})


pytestmark = [
    pytest.mark.corpus,
    pytest.mark.skipif(
        not books(), reason='盘上没有 stage6 语料——这几条**没在查**，不是「查过没事」'),
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


def test_chars_walk_the_same_ruler_as_the_rules(box):
    """⭐ `stage6.Candidate` 裁 `chars` 时明写的条件：**现算必须走 `measure()` 那把尺子。**

    `len(text)` 与 `measure().chars` 差过 9.5%——两个字数并排出现在同一页上，
    人不知道该信哪个。
    """
    v = stage6_view(box.root, box.sid)
    for c in v.candidates:
        want = measure(c.text)
        assert c.chars == (want.chars if want else 0)
        assert c.chars != len(c.text) or not c.text.strip(), (
            f'候选 {c.index} 的 chars 恰好等于 len(text)——多半是拿错了尺子')


def test_the_approved_flag_and_the_approved_block_tell_one_story(box):
    """候选级 `approved` 是从 `hook.approved.approach` 派生的，两格说同一件事。"""
    v = stage6_view(box.root, box.sid)
    assert [c.index for c in v.candidates] == [1, 2, 3]
    flagged = [c for c in v.candidates if c.approved]
    if v.approved is None:
        assert not flagged
    elif v.approved.approach == 'human_edit':
        assert not flagged, 'human_edit 是无前身的人工稿，没有候选可自称定稿'
    else:
        assert [c.approach for c in flagged] == [v.approved.approach]
        assert v.approved.chars == (measure(v.approved.text).chars
                                    if measure(v.approved.text) else 0)


def test_missing_quality_review_is_visible_and_disables_selection(box):
    """沙箱刻意不复制 semantic_N.json：缺审必须是 unreviewed，不能默认 clear。"""
    v = stage6_view(box.root, box.sid)
    assert all(candidate.semantic_state == 'unreviewed' for candidate in v.candidates)
    assert all(not candidate.selectable for candidate in v.candidates)
    assert all(candidate.quality is None and candidate.quality_rank is None
               for candidate in v.candidates)


def test_the_view_forwards_the_artifact_without_reshaping_it(box):
    """`judged` 与生成说明原样转发——转发不是改写。"""
    v = stage6_view(box.root, box.sid)
    art = sandbox.cut(box.book, 'stage6/hook.json')
    assert set(v.judged) == set(art['judged'])
    assert v.rationale == art['rationale'] and v.lead_upto == art['lead_upto']


def test_a_missing_artifact_is_a_terminal_state(box):
    """本屏没有产物就没有内容 ⇒ 缺席是 `UpstreamMissing`（404），不是空屏。"""
    (box.dir / 'stage6' / 'hook.json').unlink()
    with pytest.raises(UpstreamMissing):
        stage6_view(box.root, box.sid)


def test_the_view_writes_nothing(box):
    before = _tree(box.root)
    stage6_view(box.root, box.sid)
    stage6_view(box.root, box.sid)
    assert _tree(box.root) == before, '视图往盘上写了东西'
    assert not (box.root / '_service').exists()
    assert len(before) >= 4, f'只扫到 {len(before)} 个文件——沙箱多半没建起来'
