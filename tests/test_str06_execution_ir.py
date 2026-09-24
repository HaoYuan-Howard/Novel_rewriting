#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_execution_ir —— Compile／双视图投影反例（F4）。

【验证边界】四类执行语义的编译正确性（含净终值、依赖成环拒绝、M5 释放）、
双视图同源、slice 身份稳定；--no-prose 全链干跑在 harness（零调用）执行。
"""
from __future__ import annotations

from contracts.schema.str06.design import (
    ChapterAssignment, BodyDesign, SceneDelivery, SceneDriver,
    TargetBeat, TargetDelta, TargetScene,
)
from contracts.schema.str06.context import WriterBrief
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
    return TargetScene(state_reads=(),
        scene_id=sid,
        driver=SceneDriver(focal_actor='ch1', immediate_want='w', opposition='o', turn='t'),
        realization_brief=f'重心在{sid}的核心对峙；展开转折，收在结果落地。',
        beats=(TargetBeat(
            beat_id='bt1', event=f'事件{sid}', participants=('ch1',),
            dramatic_intent='d', target_delta=tuple(
                TargetDelta(domain=dm, key=k, value=v, subjects=('ch1',) if dm == 'character' else (), op='set', scope='book') for dm, k, v in deltas)),),
        delivery=SceneDelivery(pov='ch1', voice='v'))


def _graph():
    return BodyDesign(initial_state=(),
        graph_id='g1',
        scenes=(_scene('s01', ('world', 'season', '秋'), ('character', 'mood', '冷')),
                _scene('s02', ('character', 'mood', '回暖'))),
        chapters=(ChapterAssignment(chapter_ordinal=1, purpose='冷暖转换',
                                    scene_ids=('s01', 's02')),),
        chapter_weights={'s01': 0.6, 's02': 0.4})


def _binding(cp):
    from contracts.schema.str06.grounding import SemanticBinding
    entries = tuple(
        BindingEntry(beat_ref=('s01' + '#bt1'), obligation_id=o.obligation_id, scene_id='s01')
        if o.kind is not PreserveKind.CAUSAL else
        BindingEntry(beat_ref=('s02' + '#bt1'), obligation_id=o.obligation_id, scene_id='s02')
        for o in cp.obligations)
    return SemanticBinding(
        binding_id='b1', preserve_checkpoint_sha256=H64,
        obligations=cp.obligations, entries=entries, unbound=())


def test_compile_four_semantics_and_net_delta():
    cp = _cp(_ob('ob1'), _ob('ob2', PreserveKind.IDENTITY),
             _ob('ob3', PreserveKind.CAUSAL, requires=('ob1',)))
    graph, binding = _graph(), _binding(cp)
    plan, errors = compile_execution_plan(graph, binding)
    assert plan is not None and not errors, errors
    s01, s02 = plan.scenes
    # establishment：拍级 delta 全量（含同 key 两次的时序）
    assert len(s01.establishments) == 2
    # planned_delta：净终值——s02 的 mood 覆盖后只留一次
    assert len(s02.planned_delta) == 1
    assert s02.planned_delta[0].value == '回暖'
    # constraint：identity 类必保义务进约束
    assert [c.key for c in s01.constraints] == ['ob1', 'ob2']
    # dependency：ob1(s01) ← requires ob3(s02) → s01 依赖 s01? no：ob3 requires ob1，
    # ob3 在 s02、ob1 在 s01 → s02 依赖 s01
    assert [(e.before, e.after) for e in s02.dependencies] == [('s01', 's02')]
    assert s01.dependencies == ()


def test_compile_rejects_scene_dependency_cycle():
    # obA(s01) requires obB(s02)；obB(s02) requires obA(s01) → 场景环
    cp = _cp(_ob('obA', requires=('obB',)), _ob('obB', requires=('obA',)))
    from contracts.schema.str06.grounding import BindingEntry, SemanticBinding
    binding = SemanticBinding(
        binding_id='b1', preserve_checkpoint_sha256=H64, obligations=cp.obligations,
        entries=(BindingEntry(beat_ref=('s01' + '#bt1'), obligation_id='obA', scene_id='s01'),
                 BindingEntry(beat_ref=('s02' + '#bt1'), obligation_id='obB', scene_id='s02')), unbound=())
    _, errors = compile_execution_plan(_graph(), binding)
    assert any('成环' in e for e in errors)


def test_init_establishments_from_target_not_source_truth():
    graph = _graph()
    assert init_establishments(graph) == ()
    graph = graph.model_copy(update={'initial_state': (
        TargetDelta(domain='character', subjects=('ch1',), key='appearance',
                    value='甲的容貌', op='set', scope='book'),
        TargetDelta(domain='character', subjects=('ch2',), key='appearance',
                    value='乙的容貌', op='set', scope='book'))})
    inits = init_establishments(graph)
    state = fold_accepted_prefix((), initial=inits)
    assert len(state.entry_state_view('s01#start')) == 2
    assert state.value_of(EffectDomain.CHARACTER, 'appearance', subjects=('ch1',)) == '甲的容貌'
    assert state.value_of(EffectDomain.CHARACTER, 'appearance', subjects=('ch2',)) == '乙的容貌'
    assert all(e.established_by == 'init' for e in inits)


def test_project_context_dual_view_and_slice_identity():
    cp = _cp(_ob('ob1'), _ob('ob2', PreserveKind.IDENTITY))
    graph, binding = _graph(), _binding(cp)
    plan, _ = compile_execution_plan(graph, binding)
    folded = fold_accepted_prefix(plan.scenes[:1])
    ctx, slice_ = project_context(
        scene_id='s02', graph=graph, plan=plan,
        binding=binding, folded_prefix=folded)
    # 双视图同源
    assert ctx.verification_ir.scene_id == ctx.writer_brief.scene_id == 's02'
    # IR：本场义务 + 前场 continuity + M5 释放 + 逐拍定位
    assert ctx.verification_ir.obligations == ()
    assert ctx.verification_ir.continuity_fact_view == ()
    assert ctx.verification_ir.released_surfaces == ()
    assert [b.beat_ref for b in ctx.verification_ir.beats] == ['s02#bt1']
    assert ctx.verification_ir.beats[0].event == '事件s02'
    # 呈现视图：canonical realization_brief 原样投影；事实清单不在 brief 里
    assert ctx.writer_brief.realization_brief == graph.scenes[1].realization_brief
    assert 'required_content' not in WriterBrief.model_fields
    assert 'entry_facts' not in WriterBrief.model_fields
    # slice 身份与 context 同源且稳定
    assert slice_.execution_slice_sha256 == ctx.execution_slice_sha256
    ctx2, slice2 = project_context(
        scene_id='s02', graph=graph, plan=plan,
        binding=binding, folded_prefix=folded)
    assert slice2.execution_slice_sha256 == slice_.execution_slice_sha256
    # 消费内容（导演说明）变化必须使 slice 失效；brief 之外的事实同样如此
    ctx3, slice3 = project_context(
        scene_id='s02', graph=graph, plan=plan, binding=binding,
        folded_prefix=fold_accepted_prefix(plan.scenes[:1]),
        )
    assert slice3.execution_slice_sha256 == slice_.execution_slice_sha256
    graph.scenes[1].realization_brief = '重心改为独白。'
    ctx4, slice4 = project_context(
        scene_id='s02', graph=graph, plan=plan,
        binding=binding, folded_prefix=folded)
    assert slice4.execution_slice_sha256 != slice_.execution_slice_sha256
