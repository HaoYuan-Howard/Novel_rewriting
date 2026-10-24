#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_execution_ir —— Compile／双视图投影反例（F4）。

【验证边界】四类执行语义的编译正确性（含净终值、依赖成环拒绝、M5 释放）、
双视图同源、slice 身份稳定；--no-prose 全链干跑在 harness（零调用）执行。
"""
from __future__ import annotations

from contracts.schema.design import (
    ChapterEntry,
    ChapterAssignment, BodyDesign, SceneDelivery, SceneDriver,
    TargetBeat, TargetDelta, TargetScene,
)
from contracts.schema.state import StateAddress
from contracts.schema.context import WriterBrief
from contracts.schema.execution import EffectDomain
from contracts.schema.grounding import BindingEntry, SourceLineage
from contracts.schema.policy import PreserveKind
from contracts.schema.source import (
    ChapterBlueprint, CharactersArtifactV7,
)
from novel.stages.stage4.compile import compile_execution_plan, init_establishments
from novel.stages.stage4.fold import FoldedState, fold_accepted_prefix
from novel.stages.common import (
    project_chapter_packet, project_context, render_chapter_packet,
)
from novel.stages.stage4.book_ir import compile_writer_book_ir
from tests.test_str06_hook import _characters as _name_source
from tests.str06_expression_inputs import (
    sample_canonical_expression,
)
from tests.str06_facilities import anchor_stub, name_map_of


def _projection():
    """最小名称投影（compile 的材料参数，D2）：真实人物表投影。"""
    return _name_source().name_projection()

H64 = 'a' * 64


def _ob(oid, kind=PreserveKind.EVENT_CORE, must=True, requires=(), chars=('ch1',)):
    from contracts.schema.grounding import SourceObligation
    return SourceObligation(
        obligation_id=oid, kind=kind, statement=f'义务{oid}',
        anchors=anchor_stub(),
        lineage=SourceLineage(blueprint_refs=('b01_01',), character_ids=chars),
        must_preserve=must, satisfaction='正文呈现' if must else '',
        requires=tuple(requires))


def _cp(*obligations):
    from contracts.schema.grounding import PreserveCheckpoint
    return PreserveCheckpoint(name_map_sha256='n' * 64,
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
            delivery_hint='', target_delta=tuple(
                TargetDelta(domain=dm, key=k, value=v, subjects=('ch1',) if dm == 'character' else (), op='set', scope='book', modality='actual') for dm, k, v in deltas)),),
        delivery=SceneDelivery(pov='ch1', voice='v'))


def _graph():
    return BodyDesign(**sample_canonical_expression((_scene('s01', ('world', 'season', '秋'), ('character', 'mood', '冷')),
                _scene('s02', ('character', 'mood', '回暖')))), initial_state=(),
        graph_id='g1', book_style='冷暖转换的第三人称叙事。',
        scenes=(_scene('s01', ('world', 'season', '秋'), ('character', 'mood', '冷')),
                _scene('s02', ('character', 'mood', '回暖'))),
        chapters=(ChapterAssignment(chapter_ordinal=1, purpose='冷暖转换',
                                    scene_ids=('s01', 's02'),
                                    entry=ChapterEntry.opening()),),
        chapter_weights={'s01': 0.6, 's02': 0.4})


def _binding(cp):
    from contracts.schema.grounding import SemanticBinding
    entries = tuple(
        BindingEntry(beat_ref=('s01' + '#bt1'), obligation_id=o.obligation_id, scene_id='s01')
        if o.kind is not PreserveKind.CAUSAL else
        BindingEntry(beat_ref=('s02' + '#bt1'), obligation_id=o.obligation_id, scene_id='s02')
        for o in cp.obligations)
    return SemanticBinding(contract_version='str06-rc27',
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
    from contracts.schema.grounding import BindingEntry, SemanticBinding
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
        TargetDelta(modality='actual',domain='character', subjects=('ch1',), key='appearance',
                    value='甲的容貌', op='set', scope='book'),
        TargetDelta(modality='actual',domain='character', subjects=('ch2',), key='appearance',
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


def _reader_scene(sid, *, establish=None, confirms=None, reads=()):
    """reader 图的场景构造：establish＝本场建立的 world 事实；confirms＝本场
    读者确认的（事实地址, 命题内容）。"""
    deltas = []
    if establish is not None:
        key, value = establish
        deltas.append(TargetDelta(modality='actual',domain='world', key=key, value=value,
                                  subjects=(), op='set', scope='book'))
    for key, value in (confirms or ()):
        deltas.append(TargetDelta(
            domain='knowledge', key='kp_' + ('1' if key == 'hiding' else '2') * 16, value='true', subjects=(),
            holder='reader', op='set', scope='scene',
            knowledge_mode='known', confirms={'domain': 'world', 'key': key}))
    return TargetScene(state_reads=reads, scene_id=sid,
        driver=SceneDriver(focal_actor='ch1', immediate_want='w', opposition='o', turn='t'),
        realization_brief=f'重心在{sid}的核心对峙；展开转折，收在结果落地。',
        beats=(TargetBeat(beat_id='bt1', event=f'事件{sid}', participants=('ch1',),
                          delivery_hint='', target_delta=tuple(deltas)),),
        delivery=SceneDelivery(pov='ch1', voice='v'))


def _reader_delta_graph():
    """三场两确认（D4 地址维度）：
    s01 建立 world:hiding（不确认）；s02 确认 hiding 并建立 world:wardrobe；
    s03 确认 wardrobe。s01 不消费 wardrobe ⇒ 其清单不含 wardrobe 确认。"""
    return BodyDesign(**sample_canonical_expression((
            _reader_scene('s01', establish=('hiding', '床底有人')),
            _reader_scene('s02', establish=('wardrobe', '衣柜藏身'),
                          confirms=(('hiding', '床底有人'),)),
            _reader_scene('s03', confirms=(('wardrobe', '衣柜藏身'),)))), initial_state=(), graph_id='g2',
        book_style='冷暖转换的第三人称叙事。',
        scenes=(
            _reader_scene('s01', establish=('hiding', '床底有人')),
            _reader_scene('s02', establish=('wardrobe', '衣柜藏身'),
                          confirms=(('hiding', '床底有人'),)),
            _reader_scene('s03', confirms=(('wardrobe', '衣柜藏身'),))),
        chapters=(ChapterAssignment(chapter_ordinal=1, purpose='两次揭示',
                                    scene_ids=('s01', 's02', 's03'),
                                    entry=ChapterEntry.opening()),),
        chapter_weights={'s01': 1 / 3, 's02': 1 / 3, 's03': 1 / 3})


def test_pending_reader_confirmations_are_projected_by_address():
    """D15＋D5：确认前按命题投影——只进实际动该命题（同 key 读写）的场景清单。

    时序：本场起点之后的确认才列（确认场标注确认拍，确认后不再列入）；
    命题：本场不读不写该 key，其后续确认不进本场清单（后章读者确认变化只
    影响章包引用该命题的章）。"""
    from novel.stages.common import pending_reader_confirmations
    graph = _reader_delta_graph()
    # s01 只写 world:hiding：清单含 hiding@s02，不含 wardrobe@s03（地址不相干）
    assert [(p.fact, p.position, p.in_scene) for p in
            pending_reader_confirmations(graph, 0)] == [
        ('true', 's02#bt1', False)]
    # s02 确认 hiding（in_scene）并写 wardrobe：清单含 wardrobe@s03
    assert [(p.fact, p.position, p.in_scene) for p in
            pending_reader_confirmations(graph, 1)] == [
        ('true', 's02#bt1', True), ('true', 's03#bt1', False)]
    # s03 确认 wardrobe：hiding 已在过去确认，不再列入
    assert [(p.fact, p.position, p.in_scene) for p in
            pending_reader_confirmations(graph, 2)] == [
        ('true', 's03#bt1', True)]
    # 每条清单带读者知情地址（D5 机械面：knowledge/holder=reader）
    items = pending_reader_confirmations(graph, 1)
    # 原始 Design 保留成立值；命题文本在下方真实 IR／Writer 投影测试中回填。
    assert [(p.key, p.polarity) for p in items] == [
        ('kp_1111111111111111', 'positive'), ('kp_2222222222222222', 'positive')]
    assert [(p.address.domain.value, p.address.key) for p in items] == [
        ('world', 'hiding'), ('world', 'wardrobe')]
    assert all(p.address.holder is None for p in items)


def test_pending_confirmations_render_into_the_writer_packet():
    """清单进正文请求（renderer v5：禁区分区，自然时点表述，无地址机械位）。"""
    from novel.stages.common import render_chapter_packet
    cp = _cp(_ob('ob1'))
    graph = _reader_delta_graph()
    binding = _binding(cp)
    plan, _ = compile_execution_plan(graph, binding)
    book_ir = compile_writer_book_ir(
        graph=graph, plan=plan, binding=binding, total_budget=1000,
        length_band=(0.6, 1.3), book_title='t',
        projection=_projection(), structure=_name_source().name_structure,
        kp_statements={'kp_1111111111111111': '床底有人',
                       'kp_2222222222222222': '衣柜藏身'},
        name_map=name_map_of(_name_source()))
    packet = render_chapter_packet(project_chapter_packet(book_ir, 1))
    assert '【禁区】' in packet
    # 确认拍在本场内：自然拍序表述；不再带 world:key／拍 id 机械位
    assert '「床底有人」须到本场第1拍起才可向读者确认' in packet
    assert 'world:hiding' not in packet and 'bt1' not in packet
    # 后场确认：本章禁区表述
    assert '「衣柜藏身」本章之内不得让读者确认' in packet


def test_believes_knowledge_enters_writer_view_without_becoming_world_fact():
    """D14：believes 从 Design 进入写手入场知情；不被当作世界事实。

    模式：s01 建立「以为夫君已离开」（knowledge/believes），s02 读取它——
    场内自建立的地址不是入场消费（F5e），误以为要在后场才进 entry_knowledge。
    """
    establish = TargetScene(
        state_reads=(), scene_id='s01',
        driver=SceneDriver(focal_actor='ch1', immediate_want='w', opposition='o', turn='t'),
        realization_brief='重心在误以为夫君已离开；展开试探，收在决心留守。',
        beats=(TargetBeat(
            beat_id='bt1', event='误以为', participants=('ch1',),
            delivery_hint='',
            target_delta=(TargetDelta(
                domain='knowledge', key='left_home', value='以为夫君已离开',
                holder='ch1', op='set', scope='book', knowledge_mode='believes'),)),),
        delivery=SceneDelivery(pov='ch1', voice='v'))
    consume = TargetScene(
        state_reads=({'domain': 'knowledge', 'subjects': [], 'holder': 'ch1',
                      'key': 'left_home'},),
        scene_id='s02',
        driver=SceneDriver(focal_actor='ch1', immediate_want='w2', opposition='o', turn='t'),
        realization_brief='重心在据误以为行动；展开试探，收在结果落地。',
        beats=(TargetBeat(
            beat_id='bt1', event='据误以为行动', participants=('ch1',),
            delivery_hint='', target_delta=()),),
        delivery=SceneDelivery(pov='ch1', voice='v'))
    graph = BodyDesign(**sample_canonical_expression((establish, consume)), initial_state=(), graph_id='g3', book_style='误以为。',
                       scenes=(establish, consume),
        chapters=(ChapterAssignment(chapter_ordinal=1, purpose='误以为',
                                    scene_ids=('s01', 's02'),
                                    entry=ChapterEntry.opening()),),
        chapter_weights={'s01': 0.5, 's02': 0.5})
    binding = _binding(_cp(_ob('ob1')))
    plan, errors = compile_execution_plan(graph, binding)
    assert not errors, errors
    folded = fold_accepted_prefix(plan.scenes)
    knowledge = folded.entry_knowledge_view()
    assert [e.mode for e in knowledge] == ['believes']
    assert knowledge[0].holder == 'ch1'
    # 人物域状态视图不含这条「以为」——它不是世界事实
    assert all(e.holder is None for e in folded.entry_state_view('s02#start'))
    ctx, _ = project_context(scene_id='s02', graph=graph, plan=plan,
                             binding=binding,
                             folded_prefix=fold_accepted_prefix(plan.scenes[:1]))
    assert [e.mode for e in ctx.verification_ir.entry_knowledge] == ['believes']
    assert ctx.verification_ir.entry_state == ()


# ───────────── D3（2026-10-03 CS-1）：锚点最大源拍位置 × 承载序单调 ─────────────

def _beat_anchor_binding(ob_early, ob_late, *, late_first: bool):
    """两个 event/reveal 义务 + 显式承载位（早/晚锚点可换序承载）。"""
    from contracts.schema.grounding import BindingEntry, SemanticBinding
    cp = _cp(ob_early, ob_late)
    early_entry = BindingEntry(beat_ref='s02#bt1', obligation_id=ob_early.obligation_id,
                               scene_id='s02')
    late_entry = BindingEntry(beat_ref='s01#bt1', obligation_id=ob_late.obligation_id,
                              scene_id='s01')
    entries = (late_entry, early_entry) if late_first else (
        BindingEntry(beat_ref='s01#bt1', obligation_id=ob_early.obligation_id,
                     scene_id='s01'),
        BindingEntry(beat_ref='s02#bt1', obligation_id=ob_late.obligation_id,
                     scene_id='s02'))
    return SemanticBinding(binding_id='b1', preserve_checkpoint_sha256=H64,
                           obligations=cp.obligations, entries=entries, unbound=())


def test_source_anchor_order_allows_legal_target_reordering():
    """D3：锚点晚的义务（b02_01）承载在前场、锚点早的（b01_01）承载在后场
    → 锚点序与承载序倒置，按结构剧视拦下；允许并列、正序放行见下一例。"""
    from tests.str06_facilities import anchor_stub
    ob_early = _ob('obE').model_copy(update={'anchors': anchor_stub('b01_01')})
    ob_late = _ob('obL', PreserveKind.REVEAL).model_copy(
        update={'anchors': anchor_stub('b02_01')})
    binding = _beat_anchor_binding(ob_early, ob_late, late_first=True)
    _, errors = compile_execution_plan(_graph(), binding)
    assert errors == [], errors
    # 加上正式因果依赖后相同重排必须拒绝；故事源序本身不等于依赖。
    dependent = ob_late.model_copy(update={'requires': (ob_early.obligation_id,)})
    _, causal_errors = compile_execution_plan(
        _graph(), _beat_anchor_binding(ob_early, dependent, late_first=True))
    assert any('义务依赖逆序' in e for e in causal_errors), causal_errors
    assert any('obE' in e and 'obL' in e for e in causal_errors), causal_errors


def test_anchor_max_position_order_allows_forward_and_ties():
    """正序放行；同锚点最大位置（并列）承载序任意——跨度事件的揭示可合法
    发生在过程内部，不按最小位置排（r10b 实证 ob17/ob16 误报源）。"""
    from tests.str06_facilities import anchor_stub
    ob_early = _ob('obA').model_copy(update={'anchors': anchor_stub('b01_01')})
    ob_late = _ob('obB').model_copy(update={'anchors': anchor_stub('b02_01')})
    plan, errors = compile_execution_plan(
        _graph(), _beat_anchor_binding(ob_early, ob_late, late_first=False))
    assert plan is not None and not errors, errors
    # 并列：两个义务锚点同拍（b02_01），承载任意序
    ob_tie = _ob('obT').model_copy(update={'anchors': anchor_stub('b02_01')})
    from contracts.schema.grounding import BindingEntry, SemanticBinding
    cp = _cp(ob_late, ob_tie)
    binding = SemanticBinding(
        binding_id='b1', preserve_checkpoint_sha256=H64, obligations=cp.obligations,
        entries=(BindingEntry(beat_ref='s02#bt1', obligation_id=ob_tie.obligation_id,
                              scene_id='s02'),
                 BindingEntry(beat_ref='s01#bt1', obligation_id=ob_late.obligation_id,
                              scene_id='s01')), unbound=())
    plan, errors = compile_execution_plan(_graph(), binding)
    assert plan is not None and not errors, errors
