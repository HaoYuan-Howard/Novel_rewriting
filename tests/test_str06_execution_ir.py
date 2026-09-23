#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_execution_ir —— Compile／双视图投影反例（F4）。

【验证边界】四类执行语义的编译正确性（含净终值、依赖成环拒绝、M5 释放）、
双视图同源、slice 身份稳定；--no-prose 全链干跑在 harness（零调用）执行。
"""
from __future__ import annotations

from contracts.schema.str06.design import (
    ChapterAssignment, PresentationGraph, SceneDelivery, SceneDriver,
    TargetBeat, TargetDelta, TargetScene,
)
from contracts.schema.str06.execution import EffectDomain
from contracts.schema.str06.grounding import BindingEntry, SourceLineage
from contracts.schema.str06.policy import PreserveKind
from contracts.schema.str06.source import (
    ChapterBlueprint, CharactersArtifactV4,
)
from novel.stages.str06.compile import compile_execution_plan, init_establishments
from novel.stages.str06.fold import FoldedState, fold_accepted_prefix
from novel.stages.str06.project import project_context

H64 = 'a' * 64


def _ob(oid, kind=PreserveKind.EVENT_CORE, must=True, requires=(), chars=('ch1',)):
    from contracts.schema.str06.grounding import SourceObligation
    return SourceObligation(
        obligation_id=oid, kind=kind, statement=f'义务{oid}',
        lineage=SourceLineage(blueprint_refs=('b01_01',), character_ids=chars),
        must_preserve=must, satisfaction='正文呈现' if must else '',
        requires=tuple(requires))


def _cp(*obligations):
    from contracts.schema.str06.grounding import PreserveCheckpoint
    return PreserveCheckpoint(
        checkpoint_id='cp1', policy_id='p', policy_version=1,
        characters_sha256=H64, blueprints_sha256=H64, provider='x', model='y',
        request_sha256=H64, obligations=obligations, checkpoint_sha256=H64)


def _scene(sid, *deltas):
    return TargetScene(
        scene_id=sid,
        driver=SceneDriver(focal_actor='ch1', immediate_want='w', opposition='o', turn='t'),
        beats=(TargetBeat(
            beat_id='bt1', event=f'事件{sid}', participants=('ch1',),
            dramatic_intent='d', target_delta=tuple(
                TargetDelta(domain=dm, key=k, value=v) for dm, k, v in deltas)),),
        delivery=SceneDelivery(pov='ch1', voice='v'))


def _graph():
    return PresentationGraph(
        graph_id='g1',
        scenes=(_scene('s01', ('world', 'season', '秋'), ('character', 'mood', '冷')),
                _scene('s02', ('character', 'mood', '回暖'))),
        chapters=(ChapterAssignment(chapter_ordinal=1, scene_ids=('s01', 's02')),),
        chapter_weights={'s01': 0.6, 's02': 0.4})


def _binding(cp):
    from contracts.schema.str06.grounding import SemanticBinding
    entries = tuple(
        BindingEntry(obligation_id=o.obligation_id, scene_id='s01')
        if o.kind is not PreserveKind.CAUSAL else
        BindingEntry(obligation_id=o.obligation_id, scene_id='s02')
        for o in cp.obligations)
    return SemanticBinding(
        binding_id='b1', preserve_checkpoint_sha256=H64,
        entries=entries, unbound=())


def test_compile_four_semantics_and_net_delta():
    cp = _cp(_ob('ob1'), _ob('ob2', PreserveKind.IDENTITY),
             _ob('ob3', PreserveKind.CAUSAL, requires=('ob1',)))
    graph, binding = _graph(), _binding(cp)
    plan, errors = compile_execution_plan(graph, binding, cp)
    assert plan is not None and not errors, errors
    s01, s02 = plan.scenes
    # establishment：拍级 delta 全量（含同 key 两次的时序）
    assert len(s01.establishments) == 2
    # planned_delta：净终值——s02 的 mood 覆盖后只留一次
    assert len(s02.planned_delta) == 1
    assert s02.planned_delta[0].value == '回暖'
    # constraint：identity 类必保义务进约束
    assert [c.key for c in s01.constraints] == ['ob2']
    # dependency：ob1(s01) ← requires ob3(s02) → s01 依赖 s01? no：ob3 requires ob1，
    # ob3 在 s02、ob1 在 s01 → s02 依赖 s01
    assert [(e.before, e.after) for e in s02.dependencies] == [('s01', 's02')]
    assert s01.dependencies == ()


def test_compile_rejects_scene_dependency_cycle():
    # obA(s01) requires obB(s02)；obB(s02) requires obA(s01) → 场景环
    cp = _cp(_ob('obA', requires=('obB',)), _ob('obB', requires=('obA',)))
    from contracts.schema.str06.grounding import BindingEntry, SemanticBinding
    binding = SemanticBinding(
        binding_id='b1', preserve_checkpoint_sha256=H64,
        entries=(BindingEntry(obligation_id='obA', scene_id='s01'),
                 BindingEntry(obligation_id='obB', scene_id='s02')), unbound=())
    _, errors = compile_execution_plan(_graph(), binding, cp)
    assert any('成环' in e for e in errors)


def test_init_establishments_from_source_truth():
    chars = CharactersArtifactV4(
        characters={
            'ch1': {'canonical': '甲', 'surfaces': [
                {'surface': '甲', 'from_chapter': 1, 'until_chapter': None,
                 'source_refs': ['e01_01']}]},
            'ch2': {'canonical': '乙', 'surfaces': [
                {'surface': '乙', 'from_chapter': 1, 'until_chapter': None,
                 'source_refs': ['e01_01']}]}},
        initial_state=[{
            'state_id': 's0_rel', 'target': {'kind': 'relation',
                                             'subject': 'ch1', 'object': 'ch2'},
            'key': 'marital_status', 'value': '夫妻', 'persistence': 'stable',
            'knowledge': {'known_by': ['ch1'], 'reader_knows': True},
            'source_refs': ['e01_01']}])
    bp = ChapterBlueprint(chapter=1, beats=(
        {'beat_id': 'b01_01', 'event': 'e', 'participants': [],
         'source_refs': ['e01_01'], 'effects': [], 'reveals': []},), causal_edges=())
    from contracts.schema.str06.source import BlueprintsArtifactV12
    blueprint = BlueprintsArtifactV12(
        world_constraints=[{'constraint_id': 'wc_era', 'kind': 'setting_constant',
                            'statement': '古代江南', 'source_refs': ['e01_01']}],
        chapters=(bp,))
    inits = init_establishments(chars, blueprint)
    keys = {(e.domain, e.key) for e in inits}
    assert (EffectDomain.RELATION, 'ch1->ch2::marital_status') in keys
    assert (EffectDomain.KNOWLEDGE, 'ch1::ch1->ch2::marital_status') in keys
    assert (EffectDomain.WORLD, 'wc_era') in keys


def test_project_context_dual_view_and_slice_identity():
    cp = _cp(_ob('ob1'), _ob('ob2', PreserveKind.IDENTITY))
    graph, binding = _graph(), _binding(cp)
    plan, _ = compile_execution_plan(graph, binding, cp)
    from contracts.schema.str06.grounding import IdentityProjection
    identity = (IdentityProjection(char_id='ch1', identity_statement='甲（可用称呼：阿甲）'),)
    folded = fold_accepted_prefix(plan.scenes[:1])
    ctx, slice_ = project_context(
        scene_id='s02', attempt_id='a1', graph=graph, plan=plan,
        binding=binding, checkpoint=cp, identity=identity,
        surfaces_by_char={'ch1': ('阿甲',)}, folded_prefix=folded, total_budget=1000)
    # 双视图同源
    assert ctx.verification_ir.scene_id == ctx.writer_brief.scene_id == 's02'
    # IR：本场义务 + 前场 continuity + M5 释放（ch1 已出场）
    assert ctx.verification_ir.obligations == ()
    assert any('义务ob1' in fact for fact in ctx.verification_ir.continuity_fact_view)
    assert ctx.verification_ir.released_surfaces[0].char_id == 'ch1'
    # 预算按权重
    assert ctx.writer_brief.length_budget == 400
    # slice 身份与 context 同源且稳定
    assert slice_.execution_slice_sha256 == ctx.execution_slice_sha256
    ctx2, slice2 = project_context(
        scene_id='s02', attempt_id='a1', graph=graph, plan=plan,
        binding=binding, checkpoint=cp, identity=identity,
        surfaces_by_char={'ch1': ('阿甲',)}, folded_prefix=folded, total_budget=1000)
    assert slice2.execution_slice_sha256 == slice_.execution_slice_sha256
    # 输入变（预算变不动 slice；义务承载变才动）——预算属 brief，不进 slice
    ctx3, slice3 = project_context(
        scene_id='s02', attempt_id='a1', graph=graph, plan=plan,
        binding=binding, checkpoint=cp, identity=identity,
        surfaces_by_char={'ch1': ('阿甲',)}, folded_prefix=folded, total_budget=2000)
    assert slice3.execution_slice_sha256 == slice_.execution_slice_sha256
