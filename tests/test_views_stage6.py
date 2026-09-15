#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_views_stage6.py —— S-11「开篇文案」那一屏的组装。

view 只做三件现算（`chars` / `index` / 候选级 `approved`），本份逐件验证 +
异常面 + 纯读。判据行为归 `tests/test_stage6.py`，HTTP 行为归 `test_api_screens.py`。

⚠ 遗留 `mkdtemp()` 由 conftest 收进受管根；新增用例使用 `tmp_path`。**原语料一个字节都不许动**。
"""
from __future__ import annotations

import os

import pytest

from contracts.invariants import HOOK_SLOTS
from novel.core.measure import measure
from novel.errors import HumanEditProtected, UpstreamMissing
from novel.views.stage6 import stage6_view
from tests import corpus, sandbox
from tests.markers import corpus_params

#: 组装这一屏只要产物自己；上游缺席只影响 freshness（ghosts 点名），不拦读。
INPUTS = ('stage1/hook_copy.json', 'stage2/global_grounding.json',
          'stage4/writer_context.json',
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
    """候选级 `approved` 是从 `hook.approved.slot` 派生的，两格说同一件事。"""
    v = stage6_view(box.root, box.sid)
    assert [c.index for c in v.candidates] == [1, 2, 3]
    flagged = [c for c in v.candidates if c.approved]
    if v.approved is None:
        assert not flagged
    elif v.approved.slot is None:
        assert not flagged, 'slot=None 是无候选前身的人工稿，没有候选可自称定稿'
    else:
        assert [c.index for c in flagged] == [HOOK_SLOTS.index(v.approved.slot) + 1]
        assert v.approved.chars == (measure(v.approved.text).chars
                                    if measure(v.approved.text) else 0)


def test_candidate_lines_carry_mechanical_identity_only(box):
    """判官退役（2026-09-15）后候选行只剩机械身份：subject_sha256 仍逐条绑定字节，
    语义族字段不再出现在这一屏的形状里。"""
    v = stage6_view(box.root, box.sid)
    assert all(len(candidate.subject_sha256) == 64 for candidate in v.candidates)
    assert all(not hasattr(candidate, field)
               for candidate in v.candidates
               for field in ('semantic_state', 'quality', 'quality_rank'))


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


def test_stage6_view_closes_on_legacy_judged_paths(tmp_path, monkeypatch):
    """旧 Hook 即便仍合当前外壳，也不得把 deep/shallow 主体展示成 current。"""
    from types import SimpleNamespace
    from novel.views import stage6 as view

    (tmp_path / '书名_123').mkdir()
    art = SimpleNamespace(judged={'stage5/deep/ch01.json': object()})
    store = SimpleNamespace(
        path=lambda rel: tmp_path / '书名_123' / rel,
        read_own=lambda *_args, **_kwargs: (art, {}),
    )
    monkeypatch.setattr(view, 'ArtifactStore', lambda _ctx: store)
    monkeypatch.setattr(view, 'artifact_state', lambda *_args: object())

    with pytest.raises(HumanEditProtected, match='legacy_incompatible'):
        stage6_view(tmp_path, '123')


def test_stage6_view_reports_live_legacy_before_missing_downstream(tmp_path):
    """只有旧 Stage5 目录、没有 Hook 时仍是 legacy，不得退化成 404。"""
    book = tmp_path / '书名_123'
    (book / 'stage5' / 'deep').mkdir(parents=True)
    before = _tree(tmp_path)

    with pytest.raises(HumanEditProtected, match='legacy_incompatible'):
        stage6_view(tmp_path, '123')

    assert _tree(tmp_path) == before
    assert not (book / 'stage6' / 'hook.json').exists()
    assert not (tmp_path / '_service' / 'ledger.jsonl').exists()


def test_the_view_writes_nothing(box):
    before = _tree(box.root)
    stage6_view(box.root, box.sid)
    stage6_view(box.root, box.sid)
    assert _tree(box.root) == before, '视图往盘上写了东西'
    assert not (box.root / '_service').exists()
    assert len(before) >= 4, f'只扫到 {len(before)} 个文件——沙箱多半没建起来'
