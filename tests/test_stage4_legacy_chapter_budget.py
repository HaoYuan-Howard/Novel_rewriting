"""legacy Stage4B 的真实消费者：机械零调用、歧义章预算及跨 run 响应恢复。"""
from __future__ import annotations

import json
import threading
from typing import Any

import pytest

from contracts.schema.stage4 import EvidenceBinding
from novel.stages.stage4 import plot_binding as baseline_mod
from novel.stages.stage4 import plan
from novel.store import ArtifactStore
from novel.work_units import budget_for
from tests.test_stage4_v3_integration import _book, _ctx, _seed_current_stage2, _design, _client


def _seed(tmp_path):
    book = _book(tmp_path)
    bp = _seed_current_stage2(book)
    _design(book, bp)
    return book


def _one_ambiguous(monkeypatch, ordinals=(1,)):
    # 此处只验证预算与恢复调度；真实舞台歧义由 test_plot_binding 单独验证。
    monkeypatch.setattr(plan, 'build_mechanical_baseline',
                        _inject_stage_ambiguities(plan.build_mechanical_baseline, ordinals))


def _inject_stage_ambiguities(real_build, ordinals: tuple[int, ...]):
    """把指定章第一场的真实舞台引用留给 B 选择，验证直连调度与恢复。"""
    import copy
    from novel.stages.stage4.plot_binding import BaselineAmbiguity, ChapterBaseline

    def injected(candidate_raw: dict[str, Any], bp_raw: dict[str, Any]):
        baseline = real_build(candidate_raw, bp_raw)
        chapters = []
        for chapter in baseline.chapters:
            if chapter.chapter_ordinal not in ordinals:
                chapters.append(chapter)
                continue
            scenes = copy.deepcopy(chapter.scenes)
            first = scenes[0]
            delivery = first['deliveries'][0]
            choices = tuple(delivery['source_context_refs'])
            assert choices, '调度 fixture 必须有原文支持的舞台候选'
            delivery['source_context_refs'] = []
            chapters.append(ChapterBaseline(
                chapter_ordinal=chapter.chapter_ordinal,
                scenes=scenes,
                ambiguities=(BaselineAmbiguity(
                    ambiguity_id=f'{chapter.chapter_ordinal:016x}',
                    kind='source_context_selection',
                    chapter_ordinal=chapter.chapter_ordinal,
                    candidate_scene_id=first['candidate_scene_id'],
                    beat_ref=delivery['beat_ref'],
                    candidates=choices,
                ),),
            ))
        return baseline_mod.MechanicalBaseline(chapters=tuple(chapters))

    return injected


def _answer(request):
    material = json.loads(request.user)
    return {'resolutions': [
        {'ambiguity_id': row['ambiguity_id'], 'selected_refs': [row['candidates'][0]]}
        for row in material['ambiguities']], 'binding_concerns': []}


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


@pytest.mark.parametrize('concurrency', [2, 4, 6, 8])
@pytest.mark.parametrize('interrupt', [False, True], ids=['submit', 'saved-response-interruption'])
def test_legacy_ambiguous_chapters_overlap_and_drain_before_recovery(
        tmp_path, monkeypatch, concurrency, interrupt):
    book = _seed(tmp_path)
    _one_ambiguous(monkeypatch, ordinals=(1, 2))
    barrier = threading.Barrier(2, timeout=15)
    reached = []
    lock = threading.Lock()

    def answer(request):
        with lock:
            reached.append(request.user)
        barrier.wait()
        return _answer(request)

    ctx = _ctx(book, 'stage4.plan')
    client, wire = _client(ctx, answer, concurrency=concurrency)
    ask = plan._LegacyPlanChapterSource.ask
    if interrupt:
        def after_saved(self, work, **kwargs):
            result = ask(self, work, **kwargs)
            if work.chapter_ordinal == 1:
                raise OSError('章回答落盘后中断')
            return result
        monkeypatch.setattr(plan._LegacyPlanChapterSource, 'ask', after_saved)
        with pytest.raises(OSError, match='章回答落盘后中断'):
            plan.run_plan(ctx, client)
        assert not (book / plan.BINDING_REL).exists()
    else:
        assert plan.run_plan(ctx, client).passed
    # barrier 证明两个实际 wire 同时在途，不能用总耗时变短代替并发证据。
    assert len(reached) == len(wire.calls) == 2
    assert [budget_for(ctx, scope=f'chapter:{n}').used for n in (1, 2)] == [1, 1]
    if interrupt:
        monkeypatch.setattr(plan._LegacyPlanChapterSource, 'ask', ask)
        resumed = _ctx(book, 'stage4.plan')
        restored, no_wire = _client(
            resumed, lambda _request: pytest.fail('drain 后两章回答均已保存'),
            concurrency=concurrency)
        assert plan.run_plan(resumed, restored).passed
        assert no_wire.calls == []
        assert [budget_for(resumed, scope=f'chapter:{n}').used for n in (1, 2)] == [1, 1]
        ctx = resumed
    binding = ArtifactStore(ctx).read_own(EvidenceBinding, plan.BINDING_REL)[0]
    assert [row['chapter_ordinal'] for row in binding.provenance.unit_lineage] == [
        chapter.output_ordinal for chapter in binding.chapters]
    assert {row['chapter_ordinal'] for row in binding.provenance.unit_lineage
            if row['kind'] == 'chapter_session'} == {1, 2}
    assert binding.provenance.unit_lineage[-1]['kind'] == 'baseline'
