#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_chapter_closure —— Stage4 章闭包验收（B3 后半）。

【验证边界】对应 `docs/REQUIREMENTS.md`「各批验收细节 · Stage4 章闭包（B3）」逐项：
先读后写、先写后读、跨章续场、作用域失效、unknown/unset/未建立（含 F5e
「unknown 初态垫底、毒化 fold」回归反例）；读者确认缺地址即拒与按地址投影到
章包（D4）；修改无关未来章不改变当前章包、修改真实依赖只影响相关章包、权重
变化按整数预算传导。compiler 只证明「已声明依赖的引用闭合」——语义依赖是否
遗漏归质量验收，机械检查不冒充证明。全程确定性编译，零模型调用。
"""
from __future__ import annotations

import pytest

from contracts.schema.design import (
    BodyDesign, ChapterAssignment, ChapterEntry, SceneDelivery, SceneDriver,
    TargetBeat, TargetDelta, TargetScene,
)
from contracts.schema.grounding import (
    BindingEntry, SemanticBinding, SourceLineage, SourceObligation,
)
from contracts.schema.policy import PreserveKind
from contracts.schema.state import StateAddress, StateRead
from novel.stages.stage4.book_ir import (
    allocate_chapter_budgets, chapters_with_budget_change, compile_writer_book_ir,
)
from novel.stages.stage4.compile import compile_execution_plan, init_establishments
from novel.stages.stage4.design import assemble_body_design
from novel.stages.stage4.fold import fold_accepted_prefix
from novel.stages.common import (
    packet_sha256, project_chapter_packet, project_context, render_chapter_packet,
)
from tests.test_str06_hook import _characters as _name_source


def _projection():
    """最小名称投影（compile 的材料参数）：真实人物表投影，禁用面非空。"""
    return _name_source().name_projection()
from contracts.schema.workflow import STAGE4_DESIGN_PROTOCOL
from tests.str06_expression_inputs import (
    sample_narration, sample_requirements, sample_design_expression, sample_canonical_expression,
)
from tests.str06_facilities import anchor_stub

H64 = 'a' * 64


# ───────────────────────── 构造器 ─────────────────────────

def _delta(domain, key, *, value=None, op='set', scope='book',
           subjects=(), holder=None, confirms=None) -> TargetDelta:
    return TargetDelta(domain=domain, key=key, subjects=subjects, holder=holder,
                       op=op, value=value, scope=scope, confirms=confirms)


def _beat(ordinal, event, deltas=()) -> TargetBeat:
    if isinstance(deltas, TargetDelta):
        deltas = (deltas,)
    return TargetBeat(beat_id=f'b{ordinal:02d}', event=event, participants=('ch1',),
                      dramatic_intent='d', target_delta=tuple(deltas))


def _scene(sid, beats, reads=()) -> TargetScene:
    return TargetScene(
        scene_id=sid, state_reads=tuple(reads),
        driver=SceneDriver(focal_actor='ch1', immediate_want='w', opposition='o', turn='t'),
        realization_brief=f'重心在{sid}的核心对峙；展开转折，收在结果落地。',
        beats=tuple(beats),
        delivery=SceneDelivery(pov='ch1', voice='v'))


def _graph(scenes, chapters, initial=()) -> BodyDesign:
    """canonical 图：chapters = [(章序, (sid, ...))]；权重默认均分。"""
    n = len(scenes)
    return BodyDesign(**sample_canonical_expression(tuple(scenes)),
        graph_id='g', book_style='冷峻克制的第三人称叙事。',
        initial_state=tuple(initial), scenes=tuple(scenes),
        chapters=tuple(ChapterAssignment(chapter_ordinal=o, purpose=f'第{o}章',
                                         scene_ids=sids,
                                         entry=(ChapterEntry.opening() if o == 1 else
                                                ChapterEntry(transition='time_skip',
                                                             recap='do_not_recap')))
                       for o, sids in chapters),
        chapter_weights={s.scene_id: 1 / n for s in scenes})


def _binding(graph) -> SemanticBinding:
    """单义务绑到首场首拍（分母闭合；义务内容与这些测试的状态语义无关）。"""
    first = graph.scenes[0]
    ob = SourceObligation(
        obligation_id='ob1', kind=PreserveKind.EVENT_CORE, statement='义务ob1',
        anchors=anchor_stub(),
        lineage=SourceLineage(blueprint_refs=('b01_01',), character_ids=('ch1',)),
        must_preserve=True, satisfaction='正文呈现')
    return SemanticBinding(
        binding_id='bd', preserve_checkpoint_sha256=H64, obligations=(ob,),
        entries=(BindingEntry(obligation_id='ob1', scene_id=first.scene_id,
                              beat_ref=f'{first.scene_id}#{first.beats[0].beat_id}'),
                 ), unbound=())


def _plan(graph, binding=None):
    binding = binding or _binding(graph)
    plan, errors = compile_execution_plan(graph, binding)
    assert not errors, errors
    return plan, binding


def _slices(graph, binding=None) -> dict[tuple, str]:
    """（章序, 场景）→ 消费身份：章包级失效判定的读取面。"""
    plan, binding = _plan(graph, binding)
    ir = compile_writer_book_ir(graph=graph, plan=plan, binding=binding,
                                total_budget=1000, length_band=(0.6, 1.3),
                                projection=_projection(), structure=None)
    return {(c.chapter_ordinal, ctx.scene_id): ctx.execution_slice_sha256
            for c in ir.chapters for ctx in c.scenes}


def _entry_state(graph, scene_id, binding=None):
    plan, binding = _plan(graph, binding)
    ir = compile_writer_book_ir(graph=graph, plan=plan, binding=binding,
                                total_budget=1000, length_band=(0.6, 1.3),
                                projection=_projection(), structure=None)
    for chapter in ir.chapters:
        for ctx in chapter.scenes:
            if ctx.scene_id == scene_id:
                return ctx.verification_ir.entry_state
    raise AssertionError(scene_id)


# ───────────────────────── 先读后写 ─────────────────────────

def test_read_before_write_keeps_entry_old_value():
    """先读后写：本场会写该地址不再删除入场读取——写手拿得到写入前的旧值。

    旧行为（B3 前）把本场写过的地址从入场读取里整批删掉，写手失去「改写前
    世界是什么」的显式约束；新值另经所写拍的 BeatIR 到达写手。"""
    graph = _graph(
        scenes=[
            _scene('s01', (_beat(1, '开局', (_delta('world', 'x', value='旧值')),),)),
            _scene('s02',
                   (_beat(1, '推进'),
                    _beat(2, '转折并改写', (_delta('world', 'x', value='新值'),))),
                   reads=(StateRead(domain='world', key='x'),)),
        ],
        chapters=[(1, ('s01',)), (2, ('s02',))])
    plan, _ = _plan(graph)
    ir = compile_writer_book_ir(graph=graph, plan=plan, binding=_binding(graph),
                                total_budget=1000, length_band=(0.6, 1.3),
                                projection=_projection(), structure=None)
    s02 = ir.chapter(2).scenes[0]
    entry = [e for e in s02.verification_ir.entry_state if e.key == 'x']
    assert entry and entry[0].value == '旧值', '入场读取必须携带本场写入前的旧值'
    assert entry[0].established_by == 's01#b01'
    beats = {b.beat_ref: b.deltas for b in s02.verification_ir.beats}
    assert [d.value for d in beats['s02#b02']] == ['新值'], \
        '新值经所写拍的逐拍结果到达写手，不改写 entry 语义'


# ───────────────────────── 先写后读 ─────────────────────────

def test_read_after_write_resolves_new_value_and_misplacement_is_refused():
    """先写后读：读取定位到写入拍之后（at=b02）即闭合；定位回入场即拒。"""
    graph = _graph(
        scenes=[
            _scene('s01',
                   (_beat(1, '开场即建立', (_delta('world', 'x', value='本场新值')),),
                    _beat(2, '据新值推进')),
                   reads=(StateRead(domain='world', key='x', at='b02'),)),
        ],
        chapters=[(1, ('s01',))])
    plan, _ = _plan(graph)
    ir = compile_writer_book_ir(graph=graph, plan=plan, binding=_binding(graph),
                                total_budget=1000, length_band=(0.6, 1.3),
                                projection=_projection(), structure=None)
    s01 = ir.chapter(1).scenes[0]
    assert not [e for e in s01.verification_ir.entry_state if e.key == 'x'], \
        '拍读取不是起笔已成立——场内建立值不进 entry 视图（M3 语义不被稀释）'
    assert [d.value for b in s01.verification_ir.beats for d in b.deltas] == ['本场新值']

    misplaced = graph.model_copy(update={
        'scenes': (graph.scenes[0].model_copy(update={
            'state_reads': (StateRead(domain='world', key='x'),)}),)})
    _, errors = compile_execution_plan(misplaced, _binding(misplaced))
    assert any('未建立' in e for e in errors), \
        '读取必须定位到事实建立之后：写入前读取同场未建立事实即拒（章闭包）'


def test_wire_read_anchor_resolves_to_canonical_beat_id():
    """wire 的读取定位是 1-based 拍序，compiler 解析成场内 beat_id（I-21）。"""
    wire = {**sample_design_expression(('ch1',)),
        'protocol_version': STAGE4_DESIGN_PROTOCOL,
        'book_style': '冷峻克制的第三人称叙事。',
        'initial_state': [],
        'scenes': [
            {'state_reads': [{'domain': 'world', 'subjects': [], 'key': 'x', 'at': 2}],
             'driver': {'focal_actor': 'ch1', 'immediate_want': 'w',
                        'opposition': 'o', 'turn': 't'},
             'realization_brief': '重心在核心对峙；展开转折，收在结果落地。',
             'beats': [
                 {'event': '建立', 'participants': ['ch1'], 'dramatic_intent': 'd',
                  'target_delta': [{'domain': 'world', 'subjects': [], 'op': 'set',
                                    'scope': 'book', 'key': 'x', 'value': 'v'}],
                  'delivery_hint': ''},
                 {'event': '读取', 'participants': ['ch1'], 'dramatic_intent': 'd',
                  'target_delta': [], 'delivery_hint': ''}],
             'delivery': {'pov': 'ch1', 'voice': 'v', 'avoid': []}},
        ],
        'chapters': [{'purpose': '一章', 'scenes': 1}],
        'scene_weights': [1],
    }
    body, errors = assemble_body_design(wire, known_char_ids={'ch1'}, expression_requirements=sample_requirements(), source_narration=sample_narration())
    assert not errors, errors
    assert body.scenes[0].state_reads[0].at == 'b02'

    wire['scenes'][0]['state_reads'][0]['at'] = 1
    rejected, errors = assemble_body_design(wire, known_char_ids={'ch1'}, expression_requirements=sample_requirements(), source_narration=sample_narration())
    assert rejected is None and any('未建立' in e for e in errors), \
        '建立拍先读后写：接受阶段就报错，不能等购买 Binding 后才拒'

    wire['scenes'][0]['state_reads'][0]['at'] = 3  # 越界定位精确拒
    _, errors = assemble_body_design(wire, known_char_ids={'ch1'}, expression_requirements=sample_requirements(), source_narration=sample_narration())
    assert any('读取定位非法' in e for e in errors)
    wire['scenes'][0]['state_reads'][0]['at'] = 2
    wire['scenes'][0]['state_reads'][0]['beat_id'] = 'b02'  # 白名单外键即拒（Q-1）
    _, errors = assemble_body_design(wire, known_char_ids={'ch1'}, expression_requirements=sample_requirements(), source_narration=sample_narration())
    assert any('Extra inputs' in e for e in errors), errors


# ───────────────────────── 跨章续场与作用域失效 ─────────────────────────

def test_cross_chapter_continuation_resolves_from_prior_chapter():
    """跨章续场：章边界不重置折叠——第 2 章读取第 1 章建立的书级事实即闭合。"""
    graph = _graph(
        scenes=[
            _scene('s01', (_beat(1, '第一章建立', (_delta('world', 'x', value='延续'),)),)),
            _scene('s02', (_beat(1, '第二章续用'),),
                   reads=(StateRead(domain='world', key='x'),)),
        ],
        chapters=[(1, ('s01',)), (2, ('s02',))])
    entry = _entry_state(graph, 's02')
    rows = [e for e in entry if e.key == 'x']
    assert rows and rows[0].value == '延续' and rows[0].established_by == 's01#b01'


def test_scene_scope_expiry_breaks_read_closure():
    """作用域失效：scene 作用域事实在本场边界失效——下一场读取即未建立。"""
    def _graph_with(scope):
        return _graph(
            scenes=[
                _scene('s01', (_beat(1, '局部事实', (_delta('world', 'x', value='v',
                                                          scope=scope)),),)),
                _scene('s02', (_beat(1, '下一场'),),
                       reads=(StateRead(domain='world', key='x'),)),
            ],
            chapters=[(1, ('s01',)), (2, ('s02',))])

    _, errors = compile_execution_plan(_graph_with('scene'), _binding(_graph_with('scene')))
    assert any('未建立' in e and '作用域已失效' in e for e in errors), errors
    entry = _entry_state(_graph_with('book'), 's02')
    assert [e.value for e in entry if e.key == 'x'] == ['v'], 'book 作用域跨场延续'


# ───────────────────────── T₀ 语义：unknown／unset／空值／未建立 ─────────────

def _t0_graph(reads) -> BodyDesign:
    """T₀ 四态对照图：K=unknown（值未知）、R=unset（显式解除）、E=set 空串
    （合法空值）；reads 声明第 1 章读取哪些地址。"""
    return _graph(
        scenes=[_scene('s01', (_beat(1, '开场'),), reads=reads)],
        chapters=[(1, ('s01',))],
        initial=(
            _delta('world', 'k', op='unknown'),
            _delta('world', 'r', op='unset'),
            _delta('world', 'e', value=''),
        ))


def test_t0_keeps_unknown_unset_empty_and_absent_distinct():
    """T₀ = Init(initial_state) 不按 op 过滤：set（含合法空值）、unset、unknown
    是三种不同事实，「未建立」才是缺席（I-42）；compiler 不再洗白非 set 初态。"""
    initial = (_delta('world', 'k', op='unknown'),
               _delta('world', 'r', op='unset'),
               _delta('world', 'e', value=''))
    graph = _t0_graph(())
    inits = init_establishments(graph)
    assert [e.op for e in inits if e.key in ('k', 'r', 'e')] == ['unknown', 'unset', 'set']
    assert all(e.established_by == 'init' for e in inits)
    folded = fold_accepted_prefix((), initial=inits)
    status = {e.key: (e.status, e.value) for e in folded.entry_state_view('s01')}
    assert status['k'] == ('unknown', None)
    assert status['r'] == ('unset', None), '显式解除不是静默消失'
    assert status['e'] == ('set', ''), '合法空值不是未建立'

    # 合法空值与显式解除都到达消费面
    entry = _entry_state(_t0_graph((StateRead(domain='world', key='e'),
                                    StateRead(domain='world', key='r'))), 's01')
    by_key = {e.key: e for e in entry}
    assert by_key['e'].status == 'set' and by_key['e'].value == ''
    assert by_key['r'].status == 'unset' and by_key['r'].value is None

    # 未建立：没有 T₀ 条目也没有任何建立点 ⇒ 读取即拒
    _, errors = compile_execution_plan(
        _t0_graph((StateRead(domain='world', key='never'),)), _binding(_t0_graph(())))
    assert any('未建立' in e for e in errors), errors


def test_f5e_unknown_padding_poisons_fold_and_is_refused():
    """F5e 回归反例：unknown 初态「垫底」不被 compiler 洗白——以其本面到达
    消费点并被「消费 unknown」闸拦下（毒化 fold 的失败保留，不静默跳过）。

    compile 的章闭包证明 fail fast（免费重验）；写手投影层同一语义兜底
    （与 compile 共用 scene_read_views）。「人物不知道」用知情缺席或 believes
    表达（D14），不用 unknown 初态垫底。"""
    graph = _t0_graph((StateRead(domain='world', key='k'),))
    inits = init_establishments(graph)
    assert [e.op for e in inits if e.key == 'k'] == ['unknown'], \
        '垫底项保留在 T₀，不按 op 丢弃'
    binding = _binding(graph)
    _, errors = compile_execution_plan(graph, binding)
    assert any('unknown' in e and '不得当作通过' in e for e in errors), errors
    # 写手投影层兜底：同一解析实现，消费 unknown 同样抛错（I-42）
    from contracts.schema.execution import ExecutionPlan, SceneExecutionPlan
    plan = ExecutionPlan(
        initial_establishments=inits,
        scenes=(SceneExecutionPlan(scene_id='s01', establishments=inits),))
    with pytest.raises(ValueError, match='unknown'):
        project_context(scene_id='s01', graph=graph, plan=plan, binding=binding,
                        folded_prefix=fold_accepted_prefix((), initial=inits))


# ───────────────────────── 精确失效：章包 ─────────────────────────

def _three_chapter_graph() -> BodyDesign:
    """ch1 s01 写 world:x；ch2 s02 读取 x；ch3 s03 独立（不读不写不确认）。"""
    return _graph(
        scenes=[
            _scene('s01', (_beat(1, '建立', (_delta('world', 'x', value='v1'),)),)),
            _scene('s02', (_beat(1, '消费'),),
                   reads=(StateRead(domain='world', key='x'),)),
            _scene('s03', (_beat(1, '无关章'),)),
        ],
        chapters=[(1, ('s01',)), (2, ('s02',)), (3, ('s03',))])


def test_unrelated_future_change_keeps_current_chapter_packet():
    """修改无关未来章不改变当前章包：ch2 导演说明变化只动 ch2 的消费身份。"""
    graph = _three_chapter_graph()
    before = _slices(graph)
    changed = graph.model_copy(update={
        'scenes': (graph.scenes[0],
                   graph.scenes[1],
                   graph.scenes[2].model_copy(update={
                       'realization_brief': '重心改为无关章的独白；展开后收束。'}))})
    after = _slices(changed)
    assert after[(3, 's03')] != before[(3, 's03')]
    assert after[(1, 's01')] == before[(1, 's01')], '无关未来章变化不波及前章章包'
    assert after[(2, 's02')] == before[(2, 's02')]


def test_real_dependency_change_only_affects_related_chapters():
    """修改真实依赖只影响相关章包：ch1 写的事实值变化 → 写者与消费者失效，
    未消费该事实的 ch3 章包不变。"""
    graph = _three_chapter_graph()
    before = _slices(graph)
    changed_delta = _delta('world', 'x', value='v2')
    changed = graph.model_copy(update={
        'scenes': (graph.scenes[0].model_copy(update={
            'beats': (_beat(1, '建立', (changed_delta,)),)}),
            graph.scenes[1], graph.scenes[2])})
    after = _slices(changed)
    assert after[(1, 's01')] != before[(1, 's01')], '写入方章包失效'
    assert after[(2, 's02')] != before[(2, 's02')], '已声明依赖的消费者章包失效'
    assert after[(3, 's03')] == before[(3, 's03')], '无依赖章的章包不变'


def test_beat_read_from_prior_chapter_reaches_packet_and_identity():
    """AUD-03 反例正身（REQUIREMENTS「AUD 审计反例」第 3 行）：前章建立
    口令、后章在**具体 beat** 读取、当前场不重写——解析值挂到该拍进 IR 与
    写手视图；变异前章值后，读取章的章包身份与阅读面随之改变，无关章不变。
    （R2 前红：解析只做闭合证明，值不进 IR——改前章值后读取章摘要不变。）"""
    def _graph_beat_read(write_value: str) -> BodyDesign:
        return _graph(
            scenes=[
                _scene('s01', (_beat(1, '前章建立口令',
                                     (_delta('world', 'x', value=write_value),)),)),
                # 当前场不重写 x：只有拍级读取（at=b02）
                _scene('s02', (_beat(1, '后章铺垫'), _beat(2, '灯下对出口令')),
                       reads=(StateRead(domain='world', key='x', at='b02'),)),
                _scene('s03', (_beat(1, '无关章'),)),
            ],
            chapters=[(1, ('s01',)), (2, ('s02',)), (3, ('s03',))])

    def _ir(graph: BodyDesign):
        plan, binding = _plan(graph)
        return compile_writer_book_ir(graph=graph, plan=plan, binding=binding,
                                      total_budget=1000, length_band=(0.6, 1.3),
                                      projection=_projection(), structure=None)

    # ── 值到达消费点：挂到读取拍进 IR，不混进 entry 视图 ──
    ir = _ir(_graph_beat_read('口令甲'))
    s02 = ir.chapter(2).scenes[0]
    assert not [e for e in s02.verification_ir.entry_state if e.key == 'x'], \
        '拍级读取不是起笔已成立——值不得混进 entry 视图'
    b02 = next(b for b in s02.verification_ir.beats if b.beat_ref == 's02#b02')
    assert [(e.key, e.value, e.established_by) for e in b02.reads] == \
        [('x', '口令甲', 's01#b01')], '前章建立的值必须挂到后章读取拍'
    b01 = next(b for b in s02.verification_ir.beats if b.beat_ref == 's02#b01')
    assert b01.reads == (), '没有声明读取的拍不带值'

    # ── 写手视图（真实 renderer 的分区字节，不只断言 helper）──
    from tests.test_str06_r2_engine import _blocks
    render2 = render_chapter_packet(project_chapter_packet(ir, 2))
    assert '口令甲' in _blocks(render2)['不可违背'], '拍级读取值以必须遵守送达'
    assert '第2拍依赖的事实' in render2

    # ── 变异前章值：读取章身份与阅读面改变，无关章不变 ──
    before_slices = _slices(_graph_beat_read('口令甲'))
    ir_old = _ir(_graph_beat_read('口令甲'))
    ir_new = _ir(_graph_beat_read('口令乙'))
    after_slices = _slices(_graph_beat_read('口令乙'))
    assert after_slices[(1, 's01')] != before_slices[(1, 's01')]
    assert after_slices[(2, 's02')] != before_slices[(2, 's02')], \
        '拍级读取消费了 s01 的写入——读取章消费身份必须失效'
    assert after_slices[(3, 's03')] == before_slices[(3, 's03')], \
        '未消费该地址的章不受波及'
    old_d = {n: packet_sha256(project_chapter_packet(ir_old, n)) for n in (1, 2, 3)}
    new_d = {n: packet_sha256(project_chapter_packet(ir_new, n)) for n in (1, 2, 3)}
    assert new_d[1] != old_d[1] and new_d[2] != old_d[2] and new_d[3] == old_d[3]
    render_new = render_chapter_packet(project_chapter_packet(ir_new, 2))
    assert '口令乙' in _blocks(render_new)['不可违背']
    assert '口令甲' not in render_new, '旧值不得残留阅读面'


def test_weight_change_propagates_through_integer_budgets():
    """权重变化按失效率传导：受影响章＝整数预算变化的章（最大余数归一），
    预算不变的章不受波及。"""
    graph = _three_chapter_graph().model_copy(update={'chapter_weights': {
        's01': 0.4, 's02': 0.3, 's03': 0.3}})
    shifted = graph.model_copy(update={'chapter_weights': {
        's01': 0.5, 's02': 0.3, 's03': 0.2}})
    assert allocate_chapter_budgets(graph, 1000) == {1: 400, 2: 300, 3: 300}
    assert allocate_chapter_budgets(shifted, 1000) == {1: 500, 2: 300, 3: 200}
    assert chapters_with_budget_change(graph, shifted, 1000) == {1, 3}, \
        '章 2 预算不变 ⇒ 不受波及；权重变化只传导到整数预算变化的章'


# ───────────────────────── 读者确认按地址投影到章包（D4） ───────────────────

def _confirmation_graph() -> BodyDesign:
    """ch1 s01 写 world:hiding；ch2 s02 读取并在 b02 让读者确认；ch3 s03 独立。"""
    confirm = TargetDelta(
        domain='reader', key='hiding', subjects=(), op='set', scope='scene',
        value='床底有人',
        confirms=StateAddress(domain='world', key='hiding', subjects=(), holder=None))
    return _graph(
        scenes=[
            _scene('s01', (_beat(1, '建立', (_delta('world', 'hiding', value='床底有人'),)),)),
            _scene('s02', (_beat(1, '试探'), _beat(2, '确认', (confirm,))),
                   reads=(StateRead(domain='world', key='hiding'),)),
            _scene('s03', (_beat(1, '无关章'),)),
        ],
        chapters=[(1, ('s01',)), (2, ('s02',)), (3, ('s03',))])


def test_reader_confirmation_projects_only_into_consuming_scenes():
    """未确认项只投影到实际消费该地址的场景（读取或写入），确认前不作为
    已确认信息；不消费该地址的场景拿不到这条清单（D4 按地址的精确投影），
    并在消费场景的章包请求中带确认位置标注。"""
    graph = _confirmation_graph()
    plan, binding = _plan(graph)
    ir = compile_writer_book_ir(graph=graph, plan=plan, binding=binding,
                                total_budget=3000, length_band=(0.6, 1.3),
                                projection=_projection(), structure=None)
    pending = {ctx.scene_id: ctx.verification_ir.pending_reader_confirmations
               for c in ir.chapters for ctx in c.scenes}
    assert [(p.fact, p.position, p.in_scene) for p in pending['s01']] == [
        ('床底有人', 's02#b02', False)], '写入该地址的场景是消费者（写手须知道尚未可确认）'
    assert [(p.fact, p.in_scene) for p in pending['s02']] == [
        ('床底有人', True)], '读取并在本场确认的场景是消费者'
    assert pending['s03'] == (), '不消费该地址的场景不投影'
    packets = {c.chapter_ordinal: render_chapter_packet(project_chapter_packet(ir, c.chapter_ordinal))
               for c in ir.chapters}
    assert '须到本场第2拍起才可向读者确认' in packets[2], '确认拍在本场内的标注'
    assert '本章之内不得让读者确认' in packets[1], '后场确认对前章的禁区标注'


def test_reader_confirmation_without_address_is_refused_at_schema():
    """读者确认缺地址即拒（D4，schema 构造期；与 assemble 引用闭合互补）。"""
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match='confirms'):
        TargetDelta(domain='reader', key='hiding', subjects=(), op='set',
                    scope='scene', value='床底有人')
