#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_views_stage5.py —— S-08「成稿 ↔ 材料」与 S-09「验收 / 修订」的组装。

本文件盯的三条硬语义：
① `materials` 与写手同一份（`build_material`，材料先于成稿存在）；
② 对齐块是**分区**不是摘录——两侧各自拼回去必须逐字等于原文/成稿；
③ S-09 不重复正文；阅读与改稿只取并行 S-08 的 current `segments`。

⚠ 遗留 `mkdtemp()` 由 conftest 收进受管根；新增用例使用 `tmp_path`。**原语料一个字节都不许动**。
"""
from __future__ import annotations

import json
import os

import pytest

from novel.errors import UpstreamMissing
from novel.views.stage5 import review_view, stage5_view
from tests import corpus, sandbox
from tests.markers import corpus_params

INPUTS = ('stage1/main_text.json', 'stage1/hook_copy.json',
          'stage2/global_grounding.json',
          'stage4/plan_chapters.json', 'stage4/writer_context.json')


def books() -> list[str]:
    out = []
    for book in sorted({a.book_dir for a in corpus.iter_artifacts()
                        if a.rel == 'stage4/writer_context.json'}):
        if any(a.book_dir == book and a.rel.startswith('stage5/review_')
               for a in corpus.iter_artifacts()):
            out.append(book)
    return out


def _stage5_rels(book: str) -> tuple[str, ...]:
    return tuple(sorted(
        a.rel for a in corpus.iter_artifacts()
        if a.book_dir == book and a.rel.startswith('stage5/')
        and '_rounds' not in a.rel and a.rel.endswith('.json')))


@pytest.fixture(params=corpus_params(
    books(), reason='盘上没有 stage5 语料——这些集成用例没在查'),
    ids=lambda b: b[:10] if b else 'no-corpus')
def box(request):
    s = sandbox.Sandbox(request.param, INPUTS + _stage5_rels(request.param))
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


def _first_written(v) -> int:
    return next(r.output_ordinal for r in v.index if r.has_draft)


def test_materials_come_from_the_writer_ruler(box):
    """⭐ 材料块必须含篇幅目标（`target_band` 的数字插值）——它是写手真拿到的那份，
    不是重新组装的第二份。"""
    v = stage5_view(box.root, box.sid, 1)
    assert v.ctx_exists and v.materials is not None
    lo, hi = v.materials.writer_target
    assert 0 < lo <= hi
    assert any(f'{lo}~{hi} 字' in b for b in v.materials.blocks), \
        '篇幅块里没有 writer_target 的数字——材料多半不是 build_material 那份'
    assert v.source_text and v.source_chars_raw == len(v.source_text)


def test_align_rows_partition_both_texts(box):
    """⭐ 对齐块是**分区**：两侧各自拼回去逐字等于原文/成稿。摘录式对齐在这里当场红。"""
    v = stage5_view(box.root, box.sid, 1)
    o = _first_written(v)
    v = stage5_view(box.root, box.sid, o) if o != 1 else v
    got = [d for d in v.drafts if d.align_rows]
    if not got:
        pytest.skip('这一章没有可用锚（align=null 是合法降级态）')
    for d in got:
        assert ''.join(r.l for r in d.align_rows) == v.source_text
        projected = '\n\n'.join(segment.text.strip() for segment in d.segments).strip()
        assert ''.join(r.r for r in d.align_rows) == projected
        assert d.align is not None
        assert d.align.anchors == len(d.align_rows)
        assert 0 < d.align.coverage <= 1


def test_an_invalid_draft_shows_as_invalid_not_as_formal(box):
    """`.INVALID` 稿走 `invalid_drafts`，不混进正式卡。"""
    v0 = stage5_view(box.root, box.sid, 1)
    o = _first_written(v0)
    src = box.dir / 'stage5' / f'ch{o:02d}.json'
    data = json.loads(src.read_text(encoding='utf-8'))
    (src.parent / f'ch{o:02d}.INVALID.json').write_text(
        json.dumps(data, ensure_ascii=False), encoding='utf-8')
    v = stage5_view(box.root, box.sid, o)
    assert v.invalid >= 1
    assert v.invalid_drafts
    assert v.drafts


def test_review_serves_only_review_facts(box):
    """S-09 不重复投影 S-08 的正文、轮次或来源账。"""
    rv = review_view(box.root, box.sid, 1)
    assert rv.review_seq >= 1
    assert set(rv.finding_counts) == {'hard', 'info', 'metric', 'unver'}
    assert set(rv.chapter.model_dump()) == {'output_ordinal', 'findings'}
    assert all(set(row.model_dump()) == {
        'output_ordinal', 'finding_counts', 'unresolved'} for row in rv.index)
    assert set(rv.model_dump(by_alias=True)) == {
        'sid', 'title', 'review_seq', 'review_state', 'review_reasons',
        'finding_counts', 'book', 'chapters', 'ordinal', 'index', 'chapter',
    }


def test_review_does_not_copy_current_draft_fields(box):
    """当前稿被移走会影响顶层状态，但不会改写历史 Review 的结论形状。"""
    rv = review_view(box.root, box.sid, 1)
    p = box.dir / 'stage5' / 'ch01.json'
    if p.exists():
        p.unlink()
    rv2 = review_view(box.root, box.sid, 1)
    assert rv2.chapter == rv.chapter
    assert rv2.index == rv.index


def test_an_ordinal_outside_the_book_is_a_terminal_state(box):
    with pytest.raises(UpstreamMissing):
        stage5_view(box.root, box.sid, 999)
    with pytest.raises(UpstreamMissing):
        review_view(box.root, box.sid, 999)


def test_stage5_views_reject_an_unknown_synthetic_book(tmp_path):
    with pytest.raises(UpstreamMissing):
        stage5_view(tmp_path, '9999999999999999999', 1)
    with pytest.raises(UpstreamMissing):
        review_view(tmp_path, '9999999999999999999', 1)


def test_the_views_write_nothing(box):
    before = _tree(box.root)
    stage5_view(box.root, box.sid, 1)
    review_view(box.root, box.sid, 1)
    stage5_view(box.root, box.sid, 1)
    assert _tree(box.root) == before, '视图往盘上写了东西'
    assert not (box.root / '_service').exists()
    assert len(before) >= 6, f'只扫到 {len(before)} 个文件——沙箱多半没建起来'


def test_current_scene_verbatim_anchors_partition_source_and_draft():
    """当前任务书用 scenes；不能因继续读旧 segments 而静默丢失全部对齐锚。"""
    from novel.views.stage5 import _align

    source = '雨夜开门。先关门。她翻到账册末页。别惊动旁人。灯还亮着。'
    draft = '沈青听见雨声。先关门。她将账册移到灯下。别惊动旁人。周衡点头。'
    entry = {'scenes': [
        {'slot': 's01', 'verbatim': ['先关门。']},
        {'slot': 's02', 'verbatim': ['别惊动旁人。']},
    ]}
    reading, rows = _align(source, draft, entry)
    assert reading is not None and reading.anchors == 2
    assert ''.join(row.l for row in rows) == source
    assert ''.join(row.r for row in rows) == draft
