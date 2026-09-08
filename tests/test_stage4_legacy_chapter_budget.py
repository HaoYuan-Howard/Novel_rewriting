"""legacy Stage4B 的真实消费者：机械零调用、歧义章预算及跨 run 响应恢复。"""
from __future__ import annotations

import json

import pytest

from contracts.schema.stage4 import EvidenceBinding
from novel.stages.stage4 import baseline as B, plan
from novel.store import ArtifactStore
from novel.work_units import budget_for
from tests.test_stage4_v3_integration import _book, _ctx, _seed_current_stage2, _design, _client


def _seed(tmp_path):
    book = _book(tmp_path)
    bp = _seed_current_stage2(book)
    _design(book, bp)
    return book


def _one_ambiguous(monkeypatch, ordinals=(1,)):
    build = plan.build_mechanical_baseline

    def injected(candidate, blueprints):
        original = build(candidate, blueprints)
        chapters = []
        for first in original.chapters:
            if first.chapter_ordinal not in ordinals:
                chapters.append(first)
                continue
            scene = sorted(first.scene_stage_refs)[0]
            correct = first.scene_stage_refs[scene][0]
            other = next(beat['beat_id'] for beat in blueprints['blueprints'][first.chapter_ordinal - 1]['beats']
                         if beat['beat_id'] != correct)
            chapters.append(B.ChapterBaseline(
                chapter_ordinal=first.chapter_ordinal,
                scene_stage_refs={key: () if key == scene else refs
                                  for key, refs in first.scene_stage_refs.items()},
                scene_material_refs=first.scene_material_refs,
                ambiguities=(B.BaselineAmbiguity(
                    ambiguity_id=str(first.chapter_ordinal) * 16, kind='stage_signature_conflict',
                    chapter_ordinal=first.chapter_ordinal, candidate_scene_id=scene,
                    candidates=(correct, other)),)))
        return B.MechanicalBaseline(chapters=tuple(chapters))

    monkeypatch.setattr(plan, 'build_mechanical_baseline', injected)


def _answer(request):
    lines = request.user.splitlines()
    marker = lines.index(plan.prompts.BLOCK_CHAPTER_AMBIGUITIES)
    ambiguities = json.loads(lines[marker + 1])
    return {'resolutions': [
        {'ambiguity_id': row['ambiguity_id'], 'stage_ref': row['candidates'][0]}
        for row in ambiguities], 'binding_concerns': []}


def test_legacy_mechanical_book_needs_no_content_unit(tmp_path):
    book = _seed(tmp_path)
    ctx = _ctx(book, 'stage4.plan')
    client, wire = _client(ctx, lambda _request: pytest.fail('机械章不应调用模型'))
    assert plan.run_plan(ctx, client).passed
    assert wire.calls == []
    binding = ArtifactStore(ctx).read_own(EvidenceBinding, plan.BINDING_REL)[0]
    assert binding.provenance.origin == 'compiled'
    assert all(row['kind'] == 'baseline' for row in binding.provenance.unit_lineage)


def test_legacy_only_ambiguous_chapter_restores_saved_response_before_commit(
        tmp_path, monkeypatch):
    book = _seed(tmp_path)
    _one_ambiguous(monkeypatch)
    ctx = _ctx(book, 'stage4.plan')
    client, wire = _client(ctx, _answer)
    finalize = plan.finalize_plan_candidate

    def crash(*_args, **_kwargs):
        raise OSError('完整章回答已保存，聚合尚未提交')

    monkeypatch.setattr(plan, 'finalize_plan_candidate', crash)
    with pytest.raises(OSError, match='聚合尚未提交'):
        plan.run_plan(ctx, client)
    assert len(wire.calls) == 1
    assert '第 2 章' not in wire.calls[0].user
    assert budget_for(ctx, scope='chapter:1').used == 1
    monkeypatch.setattr(plan, 'finalize_plan_candidate', finalize)
    resumed = _ctx(book, 'stage4.plan')
    restored, blocked_wire = _client(resumed, lambda _request: pytest.fail('应恢复原回答'))
    assert plan.run_plan(resumed, restored).passed
    assert blocked_wire.calls == []
    binding = ArtifactStore(resumed).read_own(EvidenceBinding, plan.BINDING_REL)[0]
    unit = binding.provenance.unit_lineage[0]
    assert unit['origin'] == 'work_unit_response' and unit['source_run_id'] == ctx.run_id
    assert binding.provenance.usage.calls_made == 1
    assert budget_for(resumed, scope='chapter:1').used == 1
    assert budget_for(resumed, scope='chapter:1').latest_pending_response() is None


@pytest.mark.parametrize('drift', [None, 'model', 'request'])
def test_multi_chapter_adoption_tail_replays_original_responses_with_plan_proof(tmp_path, monkeypatch, drift):
    book = _seed(tmp_path)
    _one_ambiguous(monkeypatch, ordinals=(1, 2))
    ctx = _ctx(book, 'stage4.plan')
    client, wire = _client(ctx, _answer)
    adoption = plan._note_adoption_attempt

    def interrupt_after_first(*args, **kwargs):
        adoption(*args, **kwargs)
        if kwargs.get('chapter') == 1:
            raise OSError('首章已采纳，后章采纳尚未写')

    monkeypatch.setattr(plan, '_note_adoption_attempt', interrupt_after_first)
    with pytest.raises(OSError, match='后章采纳尚未写'):
        plan.run_plan(ctx, client)
    assert len(wire.calls) == 2
    binding_bytes = (book / plan.BINDING_REL).read_bytes()
    plan_bytes = (book / plan.REL).read_bytes()
    assert budget_for(ctx, scope='chapter:1').latest_turn().state == 'adopted'
    assert budget_for(ctx, scope='chapter:2').latest_turn().state == 'delivered'
    monkeypatch.setattr(plan, '_note_adoption_attempt', adoption)
    resumed = _ctx(book, 'stage4.plan')
    restored, blocked_wire = _client(resumed, lambda _request: pytest.fail('尾窗不得重购已采纳首章'))
    if drift == 'model':
        restored._models = ('changed-model',)
        restored.model = 'changed-model'
    elif drift == 'request':
        original_request = plan._plan_chapter_request

        def change_request(*args, **kwargs):
            from dataclasses import replace
            return replace(original_request(*args, **kwargs), system='changed-system')

        monkeypatch.setattr(plan, '_plan_chapter_request', change_request)
    if drift is not None:
        from novel.errors import ProvenanceRefused
        with pytest.raises(ProvenanceRefused, match='恢复.*原响应|原响应.*恢复'):
            plan.run_plan(resumed, restored)
        assert blocked_wire.calls == []
        assert [budget_for(resumed, scope=f'chapter:{chapter}').used for chapter in (1, 2)] == [1, 1]
        return
    assert plan.run_plan(resumed, restored).passed
    assert blocked_wire.calls == []
    assert (book / plan.BINDING_REL).read_bytes() == binding_bytes
    assert (book / plan.REL).read_bytes() == plan_bytes
    assert [budget_for(resumed, scope=f'chapter:{chapter}').used for chapter in (1, 2)] == [1, 1]
