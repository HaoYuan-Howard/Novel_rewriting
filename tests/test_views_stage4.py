#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_views_stage4.py —— S-07「计划 ↔ 写作上下文」那一屏的组装。

本文件盯三件事：①真计划与恒等基线**两态都有内容**（`04_delivery.md:133`）；
②`writer_target` 与写手**同一把尺子**（`prompts.target_band`）；
③恒等基线**现算不落盘**（view 与 `run_identity` 都只做内存诊断）。

⚠ 遗留 `mkdtemp()` 由 conftest 收进受管根；新增用例使用 `tmp_path`。**原语料一个字节都不许动**。
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
          'stage2/chapter_blueprints.json', 'stage2/characters.json',
          'stage2/knowledge_links.json',
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
        plan, binding.get('binding_concerns') or [],
        blueprints=sandbox.cut(box.book, 'stage2/chapter_blueprints.json')), \
        'rows 与 subject 必须来自同一份 plan，不许在 view 里二次读盘'
    pc = plan['output_chapters'][0]
    assert v.plan_chapter.chapter_goal.model_dump(mode='json') == pc['chapter_goal']
    assert [row.model_dump(mode='json') for row in v.plan_chapter.beat_placements] == \
        pc['beat_placements']
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


def test_goal_index_keeps_character_and_relation_presentations_distinct():
    from contracts.schema.stage4 import PlanGoal
    from novel.stages.stage4.measure import identity_plan
    from novel.views.stage4 import _index_row
    from tests.plot_fixtures import MAIN, blueprint

    blueprints = blueprint()
    chapter = identity_plan(blueprints, MAIN)['output_chapters'][0]
    first = {'char_id': 'char_001', 'presentation_ref': 'ap01_01'}
    second = {'char_id': 'char_001', 'presentation_ref': 'ap01_02'}
    unspecified = {'char_id': 'char_002', 'presentation_ref': None}
    refs = [
        {'kind': 'character', 'ref': 'char_001', 'actor': first},
        {'kind': 'character', 'ref': 'char_001', 'actor': second},
        {'kind': 'character', 'ref': 'char_002', 'actor': unspecified},
        *[{'kind': 'relation', 'ref': 'rel_01', 'relation_actors': {
            'subject': actor, 'object': unspecified}} for actor in (first, second)],
    ]
    chapter['chapter_goal'] = PlanGoal.model_validate({
        **chapter['chapter_goal'], 'subject_refs': refs}).model_dump(mode='json')
    row = _index_row(chapter, blueprints['blueprints'][0])
    assert row.chapter_goal == (
        chapter['chapter_goal']['intent'] + ' · '
        'character:ap01_01、character:ap01_02、character:char_002（未指定呈现）、'
        'relation:rel_01（ap01_01 → char_002（未指定呈现））、'
        'relation:rel_01（ap01_02 → char_002（未指定呈现））')


@pytest.mark.parametrize('change', ['steady', 'character', 'surface_drift'])
def test_formal_review_surface_hashes_the_single_plan_snapshot(monkeypatch, change):
    """页面与正式审阅共享完整依赖，同一 plan 不重读，材料漂移不拼接。"""
    import copy
    from pathlib import Path
    import novel.views.stage4 as view_module
    from contracts.schema.orchestration import ArtifactState
    from contracts.schema.stage1 import MainText
    from contracts.schema.stage2 import ChapterBlueprints
    from contracts.schema.stage4 import EvidenceBinding, PlanChapters
    from contracts.artifacts import STAGE2_CHARACTERS, STAGE2_GLOBAL_GROUNDING
    from contracts.schema.knowledge import KnowledgeGrounding
    from novel.orchestrator import approvals, inspection
    from novel.stages.stage4.plot_materials import chapter_materials_identity
    from novel.errors import SourceConflict
    from tests.stage2_fixtures import plot_blueprints, formal_subjects
    from tests.test_stage2 import global_grounding, main_text
    from novel.stages.stage4.measure import identity_plan

    registry, knowledge = formal_subjects(global_grounding())
    character_payload = registry.model_dump(mode='json', by_alias=True)
    grounding_payload = knowledge.model_dump(mode='json', by_alias=True)
    fixture = SimpleNamespace(blueprint=plot_blueprints, MAIN=main_text(),
                              GLOBAL=grounding_payload)
    raw = identity_plan(fixture.blueprint(), fixture.MAIN)
    raw['_provenance'] = {
        'code': 'test', 'root_clean_sha1': 'c' * 40, 'inputs': {}, 'origin': 'compiled',
        'blind_candidate_sha256': 'a' * 64, 'evidence_binding_sha256': 'b' * 64,
    }
    snapshot = PlanChapters.model_validate(raw)
    blueprint_raw = {**fixture.blueprint(), '_provenance': {
        'code': 'fixture', 'root_clean_sha1': 'c' * 40, 'inputs': {},
        'prompts': {'FIXTURE': '0' * 10}, 'model': 'fixture', 'scope_violations': [],
        'context': {'architecture': 'plot-blueprint-v1', 'source_format': 'numbered-paragraphs-v1',
                    'global_projection_contract': 'plot-global-scope-v2'},
    }}
    blueprint = ChapterBlueprints.model_validate(blueprint_raw)
    grounding = KnowledgeGrounding.model_validate(grounding_payload)
    main_text_view = MainText.model_validate(fixture.MAIN)
    binding = EvidenceBinding.model_validate({
        'status': 'bound', 'candidate_sha256': 'a' * 64,
        'chapters': [{'output_ordinal': chapter['output_ordinal'], 'scenes': [{
            'candidate_scene_id': f'c{chapter["output_ordinal"]:02d}{scene["slot"]}', 'deliveries': [{
                'beat_ref': placement['beat_ref'], 'evidence_refs': delivery['evidence_refs'],
                'source_context_refs': delivery['source_context_refs'],
            } for placement in chapter['beat_placements']
              for delivery in placement['deliveries'] if delivery['scene_ref'] == scene['slot']],
        } for scene in chapter['scenes']]} for chapter in raw['output_chapters']],
        '_provenance': {'origin': 'human', 'candidate_sha256': 'a' * 64,
                        'code': 'fixture', 'root_clean_sha1': 'c' * 40, 'inputs': {}},
    })
    global_g = copy.deepcopy(grounding_payload)
    reads = {}

    class FakeStore:
        def __init__(self, ctx):
            self.ctx = ctx

        def exists(self, _rel):
            return False  # 本例没有 INVALID；当前正式快照在下列读取口提供。

        def full_sha256_or_none(self, _rel):
            return 'a' * 64

        def read_own_with_sha256(self, model, rel, **_kwargs):
            return self.read_own(model, rel)

        def read(self, model, rel):
            reads[rel] = reads.get(rel, 0) + 1
            if rel == STAGE2_CHARACTERS:
                return model.model_validate(copy.deepcopy(character_payload))
            value = {STAGE2_GLOBAL_GROUNDING: grounding,
                     view_module.MAIN_TEXT: main_text_view}.get(rel)
            if value is None:
                raise UpstreamMissing(rel)
            return model.model_validate(copy.deepcopy(
                value.model_dump(mode='json', by_alias=True)))

        def read_own(self, model, rel):
            value = {view_module.PLAN_REL: snapshot,
                     view_module.STAGE4_EVIDENCE_BINDING: binding,
                     view_module.BLUEPRINTS: blueprint}.get(rel)
            if value is not None:
                reads[rel] = reads.get(rel, 0) + 1
                return value, value.model_dump(by_alias=True, mode='json')
            payload = self.read(model, rel)
            return payload, payload.model_dump(by_alias=True, mode='json')

    def state(_store, rel):
        exists = rel == view_module.PLAN_REL
        return ArtifactState(
            rel=rel, exists=exists, bytes=1 if exists else None, mtime=1 if exists else None,
            freshness='fresh' if exists else 'unknown', display_freshness='fresh' if exists else 'missing',
            reason='match' if exists else 'no_provenance', invalid_freshness=None,
            invalid_reason=None, condition='ready' if exists else 'missing')

    monkeypatch.setattr(view_module.paths, 'resolve_novel_dir', lambda _base, _sid: Path('测试_123'))
    monkeypatch.setattr(view_module, 'readonly_ctx', lambda *_args: object())
    monkeypatch.setattr(view_module, 'ArtifactStore', FakeStore)
    monkeypatch.setattr(view_module, 'artifact_state', state)
    seals = iter(('before', 'after')) if change == 'surface_drift' else None
    monkeypatch.setattr(inspection, 'read_surface_seal',
                        lambda *_a, **_kw: next(seals) if seals else 'stable')
    if change == 'surface_drift':
        with pytest.raises(SourceConflict, match='材料变化'):
            stage4_view('unused', '123', 1)
        assert reads[view_module.PLAN_REL] == 1
        return

    got = stage4_view('unused', '123', 1)
    assert all(reads[rel] == 1 for rel in (
        view_module.PLAN_REL, view_module.STAGE4_EVIDENCE_BINDING, view_module.BLUEPRINTS))
    chapter = snapshot.output_chapters[0]
    materials = {row.output_ordinal: chapter_materials_identity(
        row.output_ordinal, blueprint_raw, global_g, fixture.MAIN,
        [ref.model_dump(mode='json') for goal in
         (row.chapter_goal, row.open_hook_goal, row.end_hook_goal) for ref in goal.subject_refs],
        characters=character_payload)
        for row in snapshot.output_chapters}
    expected = approvals._decision_sha256({
        'plan_subject': structure_review_subject_sha256(snapshot, [], blueprints=blueprint),
        'chapter_materials': materials})
    assert got.structure_subject_sha256 == expected
    assert got.plan_chapter.structure_decision.mode == chapter.structure_decision.mode
    from novel.stages.stage4.measure import computed_structure_change_kinds, structure_metrics_of
    assert got.plan_chapter.structure_decision.computed_change_kinds == computed_structure_change_kinds(
        structure_metrics_of(blueprint_raw['blueprints'][0], chapter.model_dump(mode='json')))
    assert reads[view_module.MAIN_TEXT] == 1
    if change == 'character':
        character_payload['characters']['char_001']['role'] = '在老宅廊下看落花的家人'
        reads.clear()
        changed = stage4_view('unused', '123', 1)
        assert reads[view_module.PLAN_REL] == 1
        assert changed.structure_subject_sha256 != expected, '必要人物语义必须进入正式审阅身份'


def test_the_view_writes_nothing(box):
    before = _tree(box.root)
    stage4_view(box.root, box.sid, 1)
    stage4_view(box.root, box.sid, 1)
    assert _tree(box.root) == before
    assert not (box.root / '_service').exists()
    assert len(before) >= 4, f'只扫到 {len(before)} 个文件——沙箱多半没建起来'
