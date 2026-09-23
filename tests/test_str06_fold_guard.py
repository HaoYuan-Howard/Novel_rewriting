#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_fold_guard —— Fold 引擎与写前机械 Guard（M 系 RC 版）的反例套件。

【反例谱系】试点 `_tmp_vnext_guard.py` 的 ce1–ce6 逐条迁移到新坐标：
ce1→M2、ce2→M3、ce3→M1、ce4→M4、ce6→M6；ce5（M5 surface 时机）依赖 surface
窗口引擎，留 F4（guard.py 模块头已登记）。每个 M 一条红例 + 绿对照。
"""
from __future__ import annotations

from contracts.schema.str06 import (
    BindingEntry,
    ChapterAssignment,
    Establishment,
    EffectDomain,
    PreserveCheckpoint,
    PreserveKind,
    PresentationGraph,
    SceneDelivery,
    SceneDriver,
    SemanticBinding,
    SourceLineage,
    SourceObligation,
    SurfaceRelease,
    TargetBeat,
    TargetScene,
    VerificationIR,
    KnowledgeEntry,
    StateEntry,
    WriterBrief,
    WriterContext,
)
from contracts.schema.str06.execution import SceneExecutionPlan
from novel.stages.str06.fold import FoldedState, fold_accepted_prefix
from novel.stages.str06.guard import (
    check_binding_beat_refs,
    check_cast_legality,
    check_entry_timing,
    check_obligation_coverage,
    run_mechanical_guard,
    scene_order,
)

H64 = 'a' * 64


def _scene(scene_id: str, *participants: str) -> TargetScene:
    return TargetScene(state_reads=(),
        scene_id=scene_id,
        driver=SceneDriver(focal_actor=participants[0] if participants else 'ch1',
                           immediate_want='w', opposition='o', turn='t'),
        beats=(TargetBeat(beat_id='bt1', event='e', participants=participants or ('ch1',),
                          dramatic_intent='d', target_delta=()),),
        delivery=SceneDelivery(pov='ch1', voice='v'))


def _graph() -> PresentationGraph:
    return PresentationGraph(initial_state=(),
        graph_id='g1',
        scenes=(_scene('s01', 'ch1'), _scene('s02', 'ch1', 'ch2'), _scene('s03', 'ch1')),
        chapters=(
            ChapterAssignment(chapter_ordinal=1, scene_ids=('s01',)),
            ChapterAssignment(chapter_ordinal=2, scene_ids=('s02',)),
            ChapterAssignment(chapter_ordinal=3, scene_ids=('s03',)),
        ))


def _binding(*entries: BindingEntry) -> SemanticBinding:
    return SemanticBinding(
        binding_id='bd1', preserve_checkpoint_sha256=H64,
        obligations=tuple(_ob(e.obligation_id) for e in entries),
        entries=tuple(entries), unbound=())


def _ob(oid):
    return SourceObligation(obligation_id=oid, kind='event_core', statement=oid,
                            lineage=SourceLineage(blueprint_refs=('b01_01',), character_ids=()),
                            must_preserve=True, satisfaction='明确呈现')


def _context(
    scene_id: str,
    *,
    obligations=(),
    entry_state=(),
    entry_knowledge=(),
    released=(),
) -> WriterContext:
    return WriterContext(
        scene_id=scene_id, attempt_id='a1', execution_slice_sha256=H64,
        verification_ir=VerificationIR(
            scene_id=scene_id, obligations=tuple(_ob(o) for o in obligations),
            entry_state=tuple(entry_state),
            entry_knowledge=tuple(entry_knowledge),
            released_surfaces=tuple(released)),
        writer_brief=WriterBrief(pov='ch1',
            scene_id=scene_id, task='t', scene_driver_summary='d',
            beat_outlines=('b1',), length_budget=800, voice='v'))


def _est(domain: EffectDomain, key: str, value: str, scope: str = 'book') -> Establishment:
    holder = key.split('::', 1)[0] if domain is EffectDomain.KNOWLEDGE else None
    if holder:
        key = key.split('::', 1)[1]
    return Establishment(domain=domain, key=key, value=value, scope=scope,
                         subjects=('ch1',) if domain is EffectDomain.CHARACTER else (),
                         holder=holder, knowledge_mode='known' if holder else None,
                         op='set', established_by='s01#bt1')



# ───────────────────────── Fold 引擎 ─────────────────────────

def test_fold_init_book_scope_and_scene_expiry():
    plans = (
        SceneExecutionPlan(scene_id='s01', planned_delta=(
            _est(EffectDomain.WORLD, 'season', '秋'),
            _est(EffectDomain.CHARACTER, 'mood', '冷哼', scope='scene'),
        )),
        SceneExecutionPlan(scene_id='s02'),
    )
    state = fold_accepted_prefix(plans, initial=(_est(EffectDomain.WORLD, 'dynasty', '明'),))
    # T₀：init 进入 book 级状态
    assert state.value_of(EffectDomain.WORLD, 'dynasty') == '明'
    # book 级跨场持续；scene 级在 s01 边界失效
    assert state.value_of(EffectDomain.WORLD, 'season') == '秋'
    assert state.value_of(EffectDomain.CHARACTER, 'mood', subjects=('ch1',)) is None


def test_fold_established_by_trace_and_replay_rebuild():
    plans = (
        SceneExecutionPlan(scene_id='s01', planned_delta=(
            _est(EffectDomain.KNOWLEDGE, 'ch2::secret', '身世'),)),
        SceneExecutionPlan(scene_id='s02'),
    )
    state = fold_accepted_prefix(plans)
    knowledge = state.entry_knowledge_view()
    assert knowledge and knowledge[0].holder == 'ch2'
    assert knowledge[0].established_by.startswith('s01#')

    # 恢复语义：同前缀重放 ⇒ 相同状态（删除 runtime 可重建）
    rebuilt = fold_accepted_prefix(plans)
    assert rebuilt.entry_state_view('s02') == state.entry_state_view('s02')
    assert rebuilt.entry_knowledge_view() == state.entry_knowledge_view()


def test_fold_overwrite_by_later_establishment():
    plans = (
        SceneExecutionPlan(scene_id='s01', planned_delta=(
            _est(EffectDomain.CHARACTER, 'whereabouts', '陆家'),)),
        SceneExecutionPlan(scene_id='s02', planned_delta=(
            _est(EffectDomain.CHARACTER, 'whereabouts', '城南').model_copy(update={'established_by': 's02#bt1'}),)),
    )
    state = fold_accepted_prefix(plans)
    assert state.value_of(EffectDomain.CHARACTER, 'whereabouts', subjects=('ch1',)) == '城南'
    entry = [e for e in state.entry_state_view('s03') if e.key == 'whereabouts']
    assert entry and entry[0].established_by.startswith('s02#')


# ───────────────────────── 机械 Guard：绿对照 ─────────────────────────

def test_guard_green_path():
    graph = _graph()
    from contracts.schema.str06.state import SurfaceIntroduction
    graph.scenes[1].beats[0].surface_introductions = (SurfaceIntroduction(char_id='ch2', surfaces=('二郎',)),)
    binding = _binding(
        BindingEntry(beat_ref=('s01' + '#bt1'), obligation_id='ob1', scene_id='s01'),
        BindingEntry(obligation_id='ob2', scene_id='s02', beat_ref='s02#bt1'),
    )
    prefix = fold_accepted_prefix(
        (SceneExecutionPlan(scene_id='s01', planned_delta=(
            _est(EffectDomain.CHARACTER, 'ch2', '在场'),)),))
    context = _context(
        's02', obligations=('ob2',),
        entry_state=(StateEntry(subjects=('ch1',), domain='character', key='ch2', value='在场',
                                established_by='s01#scene'),),
        released=(SurfaceRelease(char_id='ch2', surfaces=('二郎',)),))
    problems = run_mechanical_guard(
        binding=binding, context=context, graph=graph)
    assert problems == []


# ───────────────────────── 机械 Guard：试点反例逐条迁移 ─────────────────────────

def test_m1_missing_obligation_in_ir():
    binding = _binding(BindingEntry(beat_ref=('s02' + '#bt1'), obligation_id='ob2', scene_id='s02'))
    context = _context('s02', obligations=())  # ce3：漏掉本场义务
    problems = check_obligation_coverage(binding, context)
    assert problems and problems[0].startswith('M1 义务覆盖') and 'ob2' in problems[0]


def test_m2_premature_knowledge():
    graph = _graph()
    order = scene_order(graph)
    # ce1：把后期（s03）才揭示的 knowledge 塞进 s02 的 entry_knowledge
    context = _context('s02', entry_knowledge=(
        KnowledgeEntry(key='secret', mode='known', holder='ch1', proposition='身世',
                       established_by='s03#bt1'),))
    problems = check_entry_timing(context, order)
    assert problems and problems[0].startswith('M2 提前揭示')
    # init 锚恒合法（前史初始知识）
    ok = _context('s02', entry_knowledge=(
        KnowledgeEntry(key='secret', mode='known', holder='ch1', proposition='婚约', established_by='init'),))
    assert check_entry_timing(ok, order) == []


def test_m3_premature_state():
    graph = _graph()
    order = scene_order(graph)
    # ce2：把后期建立的 state 塞进 s02 的 entry_state
    context = _context('s02', entry_state=(
        StateEntry(domain='world', key='season', value='冬',
                   established_by='s03#scene'),))
    problems = check_entry_timing(context, order)
    assert problems and problems[0].startswith('M3 提前效应')


def test_m4_unreal_carriage_position():
    graph = _graph()
    # ce4：beat_ref 指向不存在的拍；以及整场景不存在
    assert check_binding_beat_refs(
        _binding(BindingEntry(obligation_id='ob1', scene_id='s01', beat_ref='s01#ghost')),
        graph)
    assert check_binding_beat_refs(
        _binding(BindingEntry(obligation_id='ob1', scene_id='sXX', beat_ref='sXX#bt1')),
        graph)


def test_m6_illegal_cast_member():
    from novel.stages.str06.guard import appeared_chars, scene_order
    graph = _graph()
    order = scene_order(graph)
    # ce6：chX 从未在任何拍出场，却出现在 released_surfaces
    context = _context('s02', released=(SurfaceRelease(char_id='chX', surfaces=('某',)),))
    problems = check_cast_legality(context, graph, appeared_chars(graph, order, 's02'))
    assert problems and problems[0].startswith('M6 人物合法性')
    # 在先前场景出场后即合法（累计合法在场与 M5 引擎同源）
    assert 'ch1' in appeared_chars(graph, order, 's02')
    context_ok = _context('s02', released=(SurfaceRelease(char_id='ch1', surfaces=('某',)),))
    assert check_cast_legality(context_ok, graph, appeared_chars(graph, order, 's02')) == []


def test_guard_reports_do_not_mutate_inputs():
    graph = _graph()
    binding = _binding(BindingEntry(beat_ref=('s01' + '#bt1'), obligation_id='ob1', scene_id='s01'))
    context = _context('s01', obligations=())
    before = context.model_dump_json()
    run_mechanical_guard(binding=binding, context=context, graph=graph)
    assert context.model_dump_json() == before  # I-24：validator 不修改输入
