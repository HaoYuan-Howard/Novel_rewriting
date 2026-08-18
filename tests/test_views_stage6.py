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
from tests.markers import corpus_params

#: 组装这一屏只要产物自己；上游缺席只影响 freshness（ghosts 点名），不拦读。
INPUTS = ('stage1/hook_copy.json', 'stage2/global_grounding.json',
          'stage3/style_grounding.json', 'stage4/writer_context.json',
          'stage6/hook.json')


def books() -> list[str]:
    return sorted({a.book_dir for a in corpus.iter_artifacts()
                   if a.rel == 'stage6/hook.json'})


@pytest.fixture(params=corpus_params(
    books(), reason='盘上没有 stage6 语料——这些集成用例没在查'),
    ids=lambda b: b[:10] if b else 'no-corpus')
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


def test_missing_quality_review_is_visible_and_still_needs_a_human(box):
    """缺审不能默认放行，但必须保留明示的人工 verification 路。"""
    v = stage6_view(box.root, box.sid)
    assert all(candidate.semantic_state == 'unreviewed' for candidate in v.candidates)
    assert all(candidate.human_resolvable for candidate in v.candidates)
    assert all(len(candidate.subject_sha256) == 64 for candidate in v.candidates)
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


def test_stage6_view_rejects_an_unknown_synthetic_book(tmp_path):
    with pytest.raises(UpstreamMissing):
        stage6_view(tmp_path, '9999999999999999999')


def test_the_view_reads_only_fields_the_assessment_can_answer():
    """⚠⚠ 这一屏的正例**全部挂在 corpus 上**，非 corpus 批只跑得到上面那条 404。

    后果实测过一次：把 `CandidateAssessment.selectable` 删掉之后，默认套件全绿，
    而 `stage6_view` 在线上 `AttributeError`。这条用最小成本堵住那个缺口。

    ⚠ 它**不重拼** view 的组装逻辑——从 view 自己的源码里取出「从 assessments 上取了哪几格」，
    再拿一个真的 `CandidateAssessment` 逐个 `getattr`。view 改了取法，这里跟着变。
    """
    import ast
    import inspect

    from novel.stages.stage6.semantic import CandidateAssessment
    from novel.views import stage6 as view

    tree = ast.parse(inspect.getsource(view.stage6_view).lstrip())
    read = {
        node.attr for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Subscript)
        and getattr(node.value.value, 'id', None) == 'assessments'
    }
    assert read, 'view 已经不从 assessments 上取值了？那这条守卫在看空气'
    probe = CandidateAssessment(state='unreviewed', reason='合成探针')
    missing = sorted(name for name in read if not hasattr(probe, name))
    assert not missing, (
        f'`stage6_view` 从 CandidateAssessment 上取了它没有的格：{missing}\n'
        f'→ 删字段时同时改 view；这一屏的正例都在 corpus 批，默认套件不会替你发现')


def test_the_view_writes_nothing(box):
    before = _tree(box.root)
    stage6_view(box.root, box.sid)
    stage6_view(box.root, box.sid)
    assert _tree(box.root) == before, '视图往盘上写了东西'
    assert not (box.root / '_service').exists()
    assert len(before) >= 4, f'只扫到 {len(before)} 个文件——沙箱多半没建起来'
