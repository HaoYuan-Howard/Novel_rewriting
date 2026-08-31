#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_views_stage4.py —— S-07「计划 ↔ 写作上下文」那一屏的组装。

本文件盯三件事：①真计划与恒等基线**两态都有内容**（`04_delivery.md:133`）；
②`writer_target` 与写手**同一把尺子**（`prompts.target_band`）；
③恒等基线**现算不落盘**（view 与 `run_identity` 都只做内存诊断）。

⚠ 沙箱一律 `tempfile.mkdtemp()`；**原语料一个字节都不许动**。
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from novel.errors import UpstreamMissing
from novel.stages.stage4.measure import structure_review_subject_sha256
from novel.stages.stage5.prompts import target_band
from novel.views.stage4 import stage4_view
from tests import corpus, sandbox
from tests.markers import corpus_params

INPUTS = ('stage1/main_text.json', 'stage2/global_grounding.json',
          'stage2/chapter_blueprints.json',
          'stage4/evidence_binding.json',
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


@pytest.fixture(params=corpus_params(
    books(), reason='盘上没有可读的 stage4 语料——这些集成用例没在查'),
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


def test_the_real_plan_forwards_goals_and_sums_budgets(box):
    plan = sandbox.cut(box.book, 'stage4/plan_chapters.json')
    binding = sandbox.cut(box.book, 'stage4/evidence_binding.json')
    o = plan['output_chapters'][0]['output_ordinal']
    v = stage4_view(box.root, box.sid, o)
    assert not v.identity_plan
    assert v.structure_subject_sha256 == structure_review_subject_sha256(
        plan, binding.get('binding_concerns') or []), \
        'rows 与 subject 必须来自同一份 plan，不许在 view 里二次读盘'
    pc = plan['output_chapters'][0]
    assert v.plan_chapter.chapter_goal.model_dump(mode='json') == pc['chapter_goal']
    assert [scene.land_fact_refs for scene in v.plan_chapter.scenes] == \
        [scene['land_fact_refs'] for scene in pc['scenes']]
    assert [scene.slot for scene in v.plan_chapter.scenes] == \
        [scene['slot'] for scene in pc['scenes']]
    row = next(r for r in v.index if r.output_ordinal == o)
    want = sum(scene['budget_chars'] for scene in pc['scenes']
               if scene.get('budget_chars'))
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
    assert v.structure_subject_sha256 is None, '现算恒等基线不是可审批主体'
    assert v.plan_chapter.structure_decision.mode == 'retain'
    assert v.plan_chapter.scenes and v.ctx_chapter is not None
    assert v.plan_provenance is None and v.ctx_provenance is None
    assert _tree(box.root) == before, '恒等基线把东西写到盘上了（那是 --identity 的事）'


def test_a_missing_ctx_does_not_take_the_plan_down(box):
    """计划在而 ctx 不在 ⇒ 右半边 `null`，左半边照常（两半各自失败）。"""
    (box.dir / 'stage4' / 'writer_context.json').unlink()
    v = stage4_view(box.root, box.sid, 1)
    assert not v.identity_plan and v.plan_chapter.scenes
    assert v.ctx_chapter is None and v.writer_target is None
    assert v.ctx_state.exists is False
    assert v.index[0].scenes >= 1


def test_an_ordinal_outside_the_plan_is_a_terminal_state(box):
    with pytest.raises(UpstreamMissing):
        stage4_view(box.root, box.sid, 999)


def test_stage4_view_rejects_an_unknown_synthetic_book(tmp_path):
    with pytest.raises(UpstreamMissing):
        stage4_view(tmp_path, '9999999999999999999', 1)


def test_formal_review_surface_hashes_the_single_plan_snapshot(monkeypatch):
    """review rows 与 subject 只读一次 plan；不给 TOCTOU 留二次读缝。"""
    import novel.views.stage4 as view_module
    from pathlib import Path
    from contracts.schema.orchestration import ArtifactState
    from contracts.schema.stage4 import PlanChapters
    from tests.hard_cases import _BPS4, _clean_plan4

    raw = _clean_plan4()
    raw['_provenance'] = {
        'code': 'test',
        'root_clean_sha1': 'c' * 40,
        'inputs': {},
        'origin': 'compiled',
        'blind_candidate_sha256': 'a' * 64,
        'evidence_binding_sha256': 'b' * 64,
    }
    snapshot = PlanChapters.model_validate(raw)
    plan_reads = 0

    class FakeStore:
        def __init__(self, _ctx):
            pass

        def read_own(self, _model, rel):
            nonlocal plan_reads
            if rel == view_module.PLAN_REL:
                plan_reads += 1
                return snapshot, b'plan-snapshot'
            if rel == view_module.STAGE4_EVIDENCE_BINDING:
                return SimpleNamespace(status='bound', binding_concerns=[]), b'binding'
            raise UpstreamMissing(rel)

        def read(self, _model, rel):
            if rel == view_module.BLUEPRINTS:
                return type('Dumped', (), {
                    'model_dump': lambda self, **_kwargs: _BPS4,
                })()
            raise UpstreamMissing(rel)

    def state(_store, rel):
        if rel == view_module.PLAN_REL:
            return ArtifactState(
                rel=rel, exists=True, bytes=1, mtime=1,
                freshness='fresh', display_freshness='fresh', reason='match',
                invalid_freshness=None, invalid_reason=None, condition='ready',
            )
        return ArtifactState(
            rel=rel, exists=False, bytes=None, mtime=None,
            freshness='unknown', display_freshness='missing',
            reason='no_provenance', invalid_freshness=None,
            invalid_reason=None, condition='missing',
        )

    monkeypatch.setattr(view_module.paths, 'resolve_novel_dir',
                        lambda _base, _sid: Path('测试_123'))
    monkeypatch.setattr(view_module, 'readonly_ctx', lambda *_args: object())
    monkeypatch.setattr(view_module, 'ArtifactStore', FakeStore)
    monkeypatch.setattr(view_module, 'artifact_state', state)

    got = stage4_view('unused', '123', 1)
    assert plan_reads == 1, 'subject 不许为了算 hash 重读 plan'
    assert got.structure_subject_sha256 == structure_review_subject_sha256(snapshot, [])
    assert got.plan_chapter.structure_decision.mode == \
        snapshot.output_chapters[0].structure_decision.mode
    assert got.plan_chapter.structure_decision.computed_change_kinds == []


def test_the_view_writes_nothing(box):
    before = _tree(box.root)
    stage4_view(box.root, box.sid, 1)
    stage4_view(box.root, box.sid, 1)
    assert _tree(box.root) == before
    assert not (box.root / '_service').exists()
    assert len(before) >= 4, f'只扫到 {len(before)} 个文件——沙箱多半没建起来'
