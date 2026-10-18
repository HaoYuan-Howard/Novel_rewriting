#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_r2_contracts —— R2 契约面：Design v2、两层 IR、回执身份拆分、M7/M8。

【验什么】首版 PLAN.md（Git 2fc03832）「R2 Stage4 语义送达与两层 IR」第 1、2、4、6、9 项的契约侧：

  · Design v2（第 4/6 项）：`TargetBeat.threads`（与源侧 loops 对称的独立线程
    结构：open 带 kind 闭集＋主体＋文本、advance 带进展、close 带按 kind 限定
    的结局闭集＋简短收束）、书级 `world_rules`、章级薄 `entry`；四个静态跨章
    列表退役（AUD-04：它们没有任何消费者）。
  · 已解析读取（第 2 项，AUD-03）：拍级读取的解析值挂到对应拍（BeatIR.reads）。
  · 两层 IR（第 1 项）：WriterPacket 分区模型按 I-11 落成唯一 canonical——按
    delivery_mode 分节、全部自然语言条目、无内部 ID 与字段名。
  · 章缝合（第 6 项）：`entry`＋上一章末场的机械投影合成 ChapterIR 的
    continuity 分区数据面。
  · 回执身份拆分（第 1 项）：写手视图摘要（含 renderer 与写手协议版本）与
    验证输入摘要（禁用字面、称谓许可、篇幅带）分别绑定；Machine 摘要仅审计，
    只改 Machine 层字段不触发重购。
  · Guard M7（每条声明读取都已解析并进入 IR）与 M8（线程生命周期合法、
    应投影线程已进入 IR）。
  · 升版（第 9 项）：STAGE4_DESIGN_PROTOCOL v2、rc10（rc9 已被 R1 占用）、
    回执 rc6、RENDERER v5、WRITER_PROTOCOL v5。

【边界】WriterPacket 的分区渲染（renderer 改写、写手协议正文 v5、Hook 分层）
与 ThreadFold 的引擎接线归 R2 引擎批；本文件锁**契约形状与判据**。
"""
from __future__ import annotations

from contracts.schema.receipt import BODY_VALIDATOR_VERSION, BODY_MECHANICAL_CHECKS

import pytest
from pydantic import ValidationError

from contracts.schema.context import BeatIR, WriterPacket
from contracts.schema.design import (
    THREAD_ENDINGS, BodyDesign, ChapterAssignment, ChapterEntry, SceneDelivery,
    SceneDriver, TargetBeat, TargetDelta, TargetScene, ThreadAction,
)
from contracts.schema.receipt import ChapterLengthDisclosure, ChapterReceipt
from contracts.schema.state import EffectDomain, StateRead
from tests.str06_expression_inputs import (
    sample_narration, sample_requirements, sample_design_expression, sample_canonical_expression,
)
from tests.str06_facilities import anchor_stub

H64 = 'a' * 64


# ───────────────────────── 构造器 ─────────────────────────

def _delta(key: str, value: str) -> TargetDelta:
    return TargetDelta(modality='actual',domain=EffectDomain.WORLD, subjects=(), key=key,
                       op='set', value=value, scope='book')


def _beat(beat_id: str, event: str, participants=('ch1',),
          threads=(), deltas=()) -> TargetBeat:
    return TargetBeat(beat_id=beat_id, event=event,
                      participants=tuple(participants),
                      delivery_hint='', target_delta=tuple(deltas),
                      threads=tuple(threads))


def _scene(scene_id: str, beats, participants=('ch1',), reads=()) -> TargetScene:
    return TargetScene(
        scene_id=scene_id, state_reads=tuple(reads),
        driver=SceneDriver(focal_actor=participants[0], immediate_want='w',
                           opposition='o', turn=f'{scene_id} 的转折'),
        realization_brief=f'重心在{scene_id}；展开后收束。',
        beats=tuple(beats),
        delivery=SceneDelivery(pov=participants[0], voice='冷峻'))


def _assignment(ordinal: int, scene_ids, entry: ChapterEntry | None = None) -> ChapterAssignment:
    return ChapterAssignment(
        chapter_ordinal=ordinal, purpose=f'第{ordinal}章目的',
        scene_ids=tuple(scene_ids), entry=entry or ChapterEntry.opening())


def _graph(scenes, chapters, **over) -> BodyDesign:
    base = dict(
        **sample_canonical_expression(scenes),
        graph_id='g1', initial_state=(), book_style='冷峻克制的第三人称叙事。',
        scenes=tuple(scenes), chapters=tuple(chapters))
    base.update(over)
    return BodyDesign(**base)


def _open(ref: str, kind: str, subject: str = 'ch1', text: str = '玉佩藏着什么') -> ThreadAction:
    return ThreadAction(ref=ref, op='open', kind=kind, subject=subject, text=text)


# ═════════════════ 一、TargetBeat.threads（R2.4 模型面） ═════════════════

def test_thread_open_requires_kind_subject_and_text():
    """open 必须带 kind／主体／文本；question 不写答案＝open 不得带结局载荷。"""
    assert _open('th01', 'question').text == '玉佩藏着什么'
    for bad in (
        dict(ref='th01', op='open', subject='ch1', text='查清真相'),          # 缺 kind
        dict(ref='th01', op='open', kind='goal', text='查清真相'),            # 缺主体
        dict(ref='th01', op='open', kind='goal', subject='ch1'),              # 缺文本
        dict(ref='th01', op='open', kind='question', subject='ch1',
             text='玉佩刻着沈字', resolution='答案不得写在 open'),             # question 带答案
        dict(ref='th01', op='open', kind='goal', subject='ch1', text='查',
             progress='不该带的进展'),
    ):
        with pytest.raises(ValidationError):
            ThreadAction(**bad)


def test_thread_advance_carries_progress_only():
    ThreadAction(ref='th01', op='advance', progress='查到玉佩来历')
    for bad in (
        dict(ref='th01', op='advance'),                                      # 缺进展
        dict(ref='th01', op='advance', progress='推进', text='改写文本'),
        dict(ref='th01', op='advance', progress='推进', kind='goal'),
        dict(ref='th01', op='advance', progress='推进', resolution='不该带'),
    ):
        with pytest.raises(ValidationError):
            ThreadAction(**bad)


def test_thread_close_requires_ending_and_resolution():
    ThreadAction(ref='th01', op='close', ending='answered', resolution='刻着沈字')
    for bad in (
        dict(ref='th01', op='close', resolution='缺结局'),
        dict(ref='th01', op='close', ending='answered'),                      # 缺收束
        dict(ref='th01', op='close', ending='answered', resolution='收',
             text='close 不改写声明文本'),
    ):
        with pytest.raises(ValidationError):
            ThreadAction(**bad)


def test_thread_endings_form_kind_closed_sets():
    """结局闭集按 kind 限定（登记面）；kind 面与闭集面成对出现。"""
    assert set(THREAD_ENDINGS) == {'goal', 'action', 'question', 'promise'}
    for kind, endings in THREAD_ENDINGS.items():
        assert len(set(endings)) == len(endings) and endings
    assert 'answered' in THREAD_ENDINGS['question']
    assert 'fulfilled' in THREAD_ENDINGS['promise']


def test_thread_close_ending_must_come_from_its_kind_closed_set():
    """close 的结局取自所属线程 kind 的闭集（kind 由 open 声明，close 不重复）。"""
    with pytest.raises(ValidationError, match='闭集'):
        _graph(
            [_scene('s01', [_beat('b01', '开问', threads=(_open('th01', 'question'),))]),
             _scene('s02', [_beat('b01', '收束', threads=(
                 ThreadAction(ref='th01', op='close', ending='fulfilled',
                              resolution='兑现'),))])],
            [_assignment(1, ('s01',)),
             _assignment(2, ('s02',), ChapterEntry(transition='time_skip',
                                                  recap='do_not_recap'))])


def test_thread_lifecycle_open_once_and_no_post_close_ops():
    open_q = _open('th01', 'question')
    advance = ThreadAction(ref='th01', op='advance', progress='追查')
    close = ThreadAction(ref='th01', op='close', ending='answered', resolution='收')
    _graph(
        [_scene('s01', [_beat('b01', '开', threads=(open_q,)),
                        _beat('b02', '推进', threads=(advance,))]),
         _scene('s02', [_beat('b01', '收', threads=(close,))])],
        [_assignment(1, ('s01',)),
         _assignment(2, ('s02',), ChapterEntry(transition='new_scene',
                                              recap='brief_orientation'))])
    # open 两次
    with pytest.raises(ValidationError, match='只能开启一次'):
        _graph([_scene('s01', [_beat('b01', '开', threads=(open_q, open_q))])],
               [_assignment(1, ('s01',))])
    # 操作早于开启
    with pytest.raises(ValidationError, match='早于开启'):
        _graph([_scene('s01', [_beat('b01', '推进', threads=(advance,))])],
               [_assignment(1, ('s01',))])
    # 关闭后继续操作
    with pytest.raises(ValidationError, match='已关闭'):
        _graph(
            [_scene('s01', [_beat('b01', '开', threads=(open_q,)),
                            _beat('b02', '收', threads=(close,)),
                            _beat('b03', '再推进', threads=(advance,))])],
            [_assignment(1, ('s01',))])


def test_thread_refs_use_compiler_dispatched_ids():
    """canonical ref 是 compiler 派发的 th{nn}（I-21）：本地标签形状即拒。"""
    with pytest.raises(ValidationError):
        ThreadAction(ref='q1', op='open', kind='question', subject='ch1', text='t')
    with pytest.raises(ValidationError):
        ThreadAction(ref='th-1', op='open', kind='question', subject='ch1', text='t')


def test_static_cross_chapter_lists_are_retired():
    """四个静态跨章列表退役（AUD-04 无消费者；裁决 3 改独立线程结构）。"""
    with pytest.raises(ValidationError):
        _graph([_scene('s01', [_beat('b01', 'e')])], [_assignment(1, ('s01',))],
               standing_goals=())
    with pytest.raises(ValidationError):
        _graph([_scene('s01', [_beat('b01', 'e')])], [_assignment(1, ('s01',))],
               ongoing_actions=())
    with pytest.raises(ValidationError):
        _graph([_scene('s01', [_beat('b01', 'e')])], [_assignment(1, ('s01',))],
               open_questions=())
    with pytest.raises(ValidationError):
        _graph([_scene('s01', [_beat('b01', 'e')])], [_assignment(1, ('s01',))],
               promises=())
    import contracts.schema.design as design_module
    for retired in ('StandingGoal', 'OngoingAction', 'OpenQuestion', 'Promise'):
        assert not hasattr(design_module, retired)


def test_world_rules_live_on_design():
    graph = _graph([_scene('s01', [_beat('b01', 'e')])], [_assignment(1, ('s01',))],
                   world_rules=('术法须以寿命为代价', '官府不涉异事'))
    assert graph.world_rules == ('术法须以寿命为代价', '官府不涉异事')


# ═════════════════ 二、ChapterAssignment.entry（R2.6 章缝合） ═════════════════

def test_chapter_entry_transition_is_a_closed_set():
    for good in ('immediate_continuation', 'time_skip', 'location_cut',
                 'pov_shift', 'new_scene', 'flashback'):
        ChapterEntry(transition=good, recap='do_not_recap',
                     anchor='s01#b01' if good in ('immediate_continuation', 'flashback') else None)
    with pytest.raises(ValidationError):
        ChapterEntry(transition='flash_back', recap='do_not_recap')
    with pytest.raises(ValidationError):
        ChapterEntry(transition='time_skip', recap='recap_everything')


def test_first_chapter_entry_is_fixed_opening():
    """第 1 章固定为开篇：new_scene＋不复述＋无 carryover（编译器不猜）。"""
    assert ChapterEntry.opening() == ChapterEntry(
        transition='new_scene', carryover=None, recap='do_not_recap')
    with pytest.raises(ValidationError, match='开篇'):
        _graph([_scene('s01', [_beat('b01', 'e')])],
               [ChapterAssignment(chapter_ordinal=1, purpose='p', scene_ids=('s01',),
                                  entry=ChapterEntry(transition='immediate_continuation',
                                                     recap='do_not_recap', anchor='s01#b01'))])


def test_entry_is_required_on_every_chapter():
    """entry 必填：缺失即拒（不用默认值冒充 Design 的承接声明，裁决 7）。"""
    with pytest.raises(ValidationError):
        ChapterAssignment(chapter_ordinal=2, purpose='p', scene_ids=('s02',))


# ═════════════════ 三、拍级读取解析值挂拍（AUD-03） ═════════════════

def _read_graph() -> BodyDesign:
    """init 建立 light；s02 b01 写新值；s02 同时声明入场读取（旧值）与拍级
    读取（at=b02，先写后读拿新值）。"""
    return _graph(
        [_scene('s01', [_beat('b01', '夜里对坐')]),
         _scene('s02', [_beat('b01', '点灯', deltas=(_delta('light', '烛火亮起'),)),
                        _beat('b02', '灯下翻检')],
                reads=(StateRead(domain=EffectDomain.WORLD, key='light',
                                 subjects=(), holder=None),
                       StateRead(domain=EffectDomain.WORLD, key='light',
                                 subjects=(), holder=None, at='b02')))],
        [_assignment(1, ('s01', 's02'))],
        initial_state=(_delta('light', '烛火未点'),))


def _binding():
    from contracts.schema.grounding import (
        BindingEntry, SemanticBinding, SourceLineage, SourceObligation,
    )
    from contracts.schema.policy import PreserveKind
    return SemanticBinding(
        binding_id='bd1', preserve_checkpoint_sha256=H64,
        obligations=(SourceObligation(
            obligation_id='ob01', kind=PreserveKind.EVENT_CORE, statement='义务',
            anchors=anchor_stub(),
            lineage=SourceLineage(blueprint_refs=('b01_01',), character_ids=('ch1',)),
            must_preserve=True, satisfaction='正文呈现'),),
        entries=(BindingEntry(obligation_id='ob01', scene_id='s01',
                              beat_ref='s01#b01'),), unbound=())


def _project(scene_id: str, graph: BodyDesign):
    from contracts.schema.grounding import SemanticBinding
    from novel.stages.common import project_context
    from novel.stages.stage4.compile import compile_execution_plan
    from novel.stages.stage4.fold import fold_accepted_prefix
    binding = _binding()
    plan, errors = compile_execution_plan(graph, binding)
    assert not errors, errors
    index = next(i for i, s in enumerate(graph.scenes) if s.scene_id == scene_id)
    context, _slice = project_context(
        scene_id=scene_id, graph=graph, plan=plan, binding=binding,
        folded_prefix=fold_accepted_prefix(plan.scenes[:index],
                                           initial=plan.initial_establishments))
    return context


def test_beat_level_read_resolves_onto_the_beat():
    """AUD-03：拍级读取的值挂到对应拍（先写后读：b02 读到 b01 写入的值）。"""
    context = _project('s02', _read_graph())
    beats = context.verification_ir.beats
    assert [b.beat_ref for b in beats] == ['s02#b01', 's02#b02']
    assert beats[0].reads == ()                    # 没有声明读取的拍不带值
    resolved = beats[1].reads
    assert len(resolved) == 1
    entry = resolved[0]
    assert entry.value == '烛火亮起'               # b01 写入的新值（先写后读）
    assert entry.established_by == 's02#b01'


def test_entry_reads_stay_in_entry_view_only():
    context = _project('s02', _read_graph())
    ir = context.verification_ir
    # 入场读取拿写入前旧值（init 的烛火未点），不混进拍级视图
    assert [e.key for e in ir.entry_state] == ['light']
    assert ir.entry_state[0].value == '烛火未点'
    assert ir.entry_state[0].established_by == 'init'


def test_m7_flags_declared_read_missing_from_ir():
    from contracts.schema.context import (
        StateEntry, VerificationIR, WriterBrief, WriterContext,
    )
    from novel.stages.stage4.guard import check_read_projection
    graph = _read_graph()
    scene = graph.scenes[1]
    ir = VerificationIR(scene_id='s02', obligations=(),
                        beats=(BeatIR(beat_ref='s02#b01', event='点灯'),
                               BeatIR(beat_ref='s02#b02', event='灯下翻检')))
    brief = WriterBrief(scene_id='s02', realization_brief='重心；收束。',
                        scene_driver_summary='ch1 想查', pov='ch1', voice='冷峻')
    context = WriterContext(scene_id='s02', execution_slice_sha256=H64,
                            verification_ir=ir, writer_brief=brief)
    problems = check_read_projection(scene, context)
    # 入场读取与拍级读取双双缺位 → 各自一条 M7
    assert len([p for p in problems if p.startswith('M7')]) == 2, problems
    # 绿对照：入场值进 entry_state、拍级值挂对应拍后 M7 通过
    fixed = context.model_copy(update={'verification_ir': ir.model_copy(update={
        'entry_state': (StateEntry(domain=EffectDomain.WORLD, key='light',
                                   subjects=(), holder=None, value='烛火未点',
                                   established_by='init'),),
        'beats': (ir.beats[0],
                  BeatIR(beat_ref='s02#b02', event='灯下翻检',

                         reads=(StateEntry(domain=EffectDomain.WORLD, key='light',
                                           subjects=(), holder=None,
                                           value='烛火亮起',
                                           established_by='s02#b01'),)))})})
    assert check_read_projection(scene, fixed) == []


# ═════════════════ 四、WriterPacket 分区模型（I-11 唯一 canonical） ═════════════════

def _packet_sections(**over) -> dict:
    base = dict(chapter_ordinal=1,
                must_realize=('拜堂这场写出两人各怀心事的细节',),
                must_respect=('云娘此刻尚不知道夫君的去向',),
                continuity=('上一章收在雨夜分别，本章自然接续',),
                creative_guidance=('重心在试探，压缩过场',),
                background=('府中仆役均已睡下，需要时可用',),
                guard_only=('玉佩的秘密本章不得揭示，也不主动提醒读者',))
    base.update(over)
    return base


def test_writer_packet_has_six_partitions_all_required():
    packet = WriterPacket(**_packet_sections())
    assert packet.must_realize and packet.guard_only
    for section in ('must_realize', 'must_respect', 'continuity',
                    'creative_guidance', 'background', 'guard_only'):
        missing = _packet_sections()
        del missing[section]
        with pytest.raises(ValidationError):
            WriterPacket(**missing)


def test_writer_packet_rejects_internal_ids_and_field_names():
    """无内部 ID 与字段名（R0 检查 4 的 schema 面）：ID 样式即拒。"""
    for section, leak in (
        ('must_realize', '写出 sc01#b02 的事件'),
        ('continuity', '延续线程 th01'),
        ('background', 'established_by=init 的入场状态'),
        ('guard_only', 'execution_slice_sha256 未变化'),
    ):
        with pytest.raises(ValidationError, match='内部'):
            WriterPacket(**_packet_sections(**{section: (leak,)}))
    # 干净的自然语言条目照常通过
    WriterPacket(**_packet_sections())


# ═════════════════ 五、continuity 分区数据面（R2.6） ═════════════════

def test_chapter_ir_continuity_composes_entry_and_previous_ending():
    from tests.test_str06_hook import _characters
    from novel.stages.stage4.book_ir import compile_writer_book_ir
    entry2 = ChapterEntry(transition='time_skip', carryover='云娘的追问没有答复',
                          recap='brief_orientation')
    graph = _graph(
        [_scene('s01', [_beat('b01', '夜里点灯',
                              participants=('ch1', 'ch2'))]),
         _scene('s02', [_beat('b01', '翌日对质', participants=('ch1', 'ch2'))])],
        [_assignment(1, ('s01',)), _assignment(2, ('s02',), entry2)])
    from novel.stages.stage4.compile import compile_execution_plan
    binding = _binding()
    plan, errors = compile_execution_plan(graph, binding)
    assert not errors, errors
    ir = compile_writer_book_ir(graph=graph, plan=plan, binding=binding,
                                total_budget=1000, length_band=(0.6, 1.3),
                                projection=_characters().name_projection(),
                                structure=None)
    first, second = ir.chapters
    # 第 1 章：entry 固定开篇，无上一章末场
    assert first.continuity.entry == ChapterEntry.opening()
    assert first.continuity.previous_ending is None
    # 第 2 章：entry 原样投影＋上一章末场机械投影
    assert second.continuity.entry == entry2
    ending = second.continuity.previous_ending
    assert ending.chapter_ordinal == 1 and ending.ending_scene_id == 's01'
    assert ending.driver_turn == 's01 的转折'
    assert ending.final_beat_event == '夜里点灯'
    assert ending.present_chars == ('ch1', 'ch2')


# ═════════════════ 六、章回执身份拆分（R2.1） ═════════════════

def _green_ir():
    from tests.test_str06_body import _green_book_ir
    return _green_book_ir()[3]


def test_chapter_receipt_version_and_split_fields():
    """回执升 rc6：packet_sha256 单摘要拆成写手视图／验证输入／Machine 审计。"""
    receipt = ChapterReceipt(
        name_mentions=(), non_name_spans=(),
        contract_version='str06-rc25', chapter_ordinal=1,
        writer_view_sha256=H64, validation_sha256='b' * 64,
        machine_view_sha256='c' * 64,
        prose_sha256='d' * 64, request_identity='r1',
        protocol_version='p1', renderer_version='r1', validator_version='v1',
        status='accepted', mechanical_checks=('non_empty',),
        length=ChapterLengthDisclosure(char_count=900, target_chars=1000,
                                       band_low=600, band_high=1300,
                                       within_band=True, deviation_ratio=-0.1))
    assert receipt.contract_version == 'str06-rc25'
    with pytest.raises(ValidationError):
        ChapterReceipt(
        name_mentions=(), non_name_spans=(),
            contract_version='str06-rc25', chapter_ordinal=1,
            writer_view_sha256=H64, validation_sha256='b' * 64,
            machine_view_sha256='c' * 64,
            packet_sha256=H64,                        # 旧单摘要形状关闭式拒读
            prose_sha256='d' * 64, request_identity='r1',
            protocol_version='p1', renderer_version='r1', validator_version='v1',
            status='accepted', mechanical_checks=('non_empty',),
            length=ChapterLengthDisclosure(char_count=900, target_chars=1000,
                                           band_low=600, band_high=1300,
                                           within_band=True, deviation_ratio=-0.1))
    with pytest.raises(ValidationError):
        ChapterReceipt(                               # 缺验证输入摘要即拒
            contract_version='str06-rc25', chapter_ordinal=1,
            writer_view_sha256=H64, machine_view_sha256='c' * 64,
            prose_sha256='d' * 64, request_identity='r1',
            protocol_version='p1', renderer_version='r1', validator_version='v1',
            status='accepted', mechanical_checks=('non_empty',),
            length=ChapterLengthDisclosure(char_count=900, target_chars=1000,
                                           band_low=600, band_high=1300,
                                           within_band=True, deviation_ratio=-0.1))


def test_receipt_binds_writer_view_and_validation_separately():
    from novel.stages.common import (
        BODY_WRITER_SYSTEM, RENDERER_VERSION, chapter_identities, check_chapter_receipt,
        project_chapter_packet, render_chapter_packet,
    )
    ir = _green_ir()
    packet = project_chapter_packet(ir, 1)
    identities = chapter_identities(packet)
    # 实际正文 system 与用户阅读面共同绑定，不能只按章包判断旧稿有效。
    material = render_chapter_packet(packet)
    import hashlib, json
    expect_writer = hashlib.sha256(json.dumps(
        {'writer_system': BODY_WRITER_SYSTEM,
         'renderer_version': RENDERER_VERSION,
         'writer_protocol_version': packet.realization_contract.protocol_version,
         'writer_view_text': material},
        ensure_ascii=False, sort_keys=True, separators=(',', ':')
    ).encode('utf-8')).hexdigest()
    assert identities.writer_view_sha256 == expect_writer
    # 三把摘要互不相同：分层各管各的
    assert len({identities.writer_view_sha256, identities.validation_sha256,
                identities.machine_view_sha256}) == 3

    def receipt(**over):
        base = dict(contract_version='str06-rc25', chapter_ordinal=1, name_mentions=(), non_name_spans=(),
                    writer_view_sha256=identities.writer_view_sha256,
                    validation_sha256=identities.validation_sha256,
                    machine_view_sha256=identities.machine_view_sha256,
                    prose_sha256='e' * 64, request_identity='r1',
                    protocol_version='p1', renderer_version='r1',
                    validator_version=BODY_VALIDATOR_VERSION, status='accepted',
                    mechanical_checks=BODY_MECHANICAL_CHECKS,
                    length=ChapterLengthDisclosure(
                        char_count=900, target_chars=1000, band_low=600,
                        band_high=1300, within_band=True, deviation_ratio=-0.1))
        base.update(over)
        return ChapterReceipt(**base)

    assert check_chapter_receipt(
        receipt(), identities=identities, prose_sha256='e' * 64) == []
    # 写手视图变化（导演说明改字）→ 重购
    from tests.test_str06_semantic_delivery import _patch_scene_brief
    mutated_ir = _patch_scene_brief(ir, 1, 'sc01', realization_brief='变异导演九九')
    mutated = chapter_identities(project_chapter_packet(mutated_ir, 1))
    assert mutated.writer_view_sha256 != identities.writer_view_sha256
    assert any('写手视图' in p for p in check_chapter_receipt(
        receipt(), identities=mutated, prose_sha256='e' * 64))
    # 验证输入变化（禁用字面）→ 重购；但不改写手视图摘要
    from tests.test_str06_semantic_delivery import _patch_chapter
    chapter = next(c for c in ir.chapters if c.chapter_ordinal == 2)
    withheld_ir = _patch_chapter(ir, 2, withheld_literals=chapter.withheld_literals +
                                 ('禁露字面九九',))
    withheld = chapter_identities(project_chapter_packet(withheld_ir, 2))
    base2 = chapter_identities(project_chapter_packet(ir, 2))
    assert withheld.validation_sha256 != base2.validation_sha256
    assert withheld.writer_view_sha256 == base2.writer_view_sha256


def test_machine_only_change_does_not_invalidate_receipt():
    """Machine 摘要仅审计：只改 Machine 层字段（渲染不可见的消费身份摘要）
    不触发重购——写手看到的与验证器核对的都没变。"""
    from novel.stages.common import (
        chapter_identities, check_chapter_receipt, project_chapter_packet,
    )
    ir = _green_ir()
    packet = project_chapter_packet(ir, 1)
    identities = chapter_identities(packet)
    machine_only = ir.model_copy(update={'chapters': tuple(
        ch.model_copy(update={'scenes': tuple(
            sc.model_copy(update={'execution_slice_sha256': 'b' * 64})
            if sc.scene_id == 'sc01' else sc for sc in ch.scenes)})
        if ch.chapter_ordinal == 1 else ch for ch in ir.chapters)})
    mutated = chapter_identities(project_chapter_packet(machine_only, 1))
    assert mutated.machine_view_sha256 != identities.machine_view_sha256
    assert mutated.writer_view_sha256 == identities.writer_view_sha256
    assert mutated.validation_sha256 == identities.validation_sha256
    receipt = ChapterReceipt(
        name_mentions=(), non_name_spans=(),
        contract_version='str06-rc25', chapter_ordinal=1,
        writer_view_sha256=identities.writer_view_sha256,
        validation_sha256=identities.validation_sha256,
        machine_view_sha256=identities.machine_view_sha256,
        prose_sha256='e' * 64, request_identity='r1',
        protocol_version='p1', renderer_version='r1', validator_version=BODY_VALIDATOR_VERSION,
        status='accepted', mechanical_checks=BODY_MECHANICAL_CHECKS,
        length=ChapterLengthDisclosure(char_count=900, target_chars=1000,
                                       band_low=600, band_high=1300,
                                       within_band=True, deviation_ratio=-0.1))
    assert check_chapter_receipt(
        receipt, identities=mutated, prose_sha256='e' * 64) == []


def test_body_writer_produces_split_receipt():
    """stage5 写手的回执按新身份产出（写手视图＋验证输入＋Machine 审计）。"""
    from novel.stages.stage5.body import write_chapter
    from tests.test_str06_body import AnsweredText
    from novel.stages.common import chapter_identities, project_chapter_packet
    ir = _green_ir()
    packet = project_chapter_packet(ir, 1)
    prose = ('云娘在灯下坐着，听见外头脚步声停了又起。' * 12)
    outcome = write_chapter(
        packet=packet,
        ask=lambda prompt, validate: AnsweredText(text=prose, finish_reason='stop',
                                        identity='i1', cached=False))
    assert outcome.receipt is not None
    identities = chapter_identities(packet)
    assert outcome.receipt.writer_view_sha256 == identities.writer_view_sha256
    assert outcome.receipt.validation_sha256 == identities.validation_sha256
    assert outcome.receipt.machine_view_sha256 == identities.machine_view_sha256


# ═════════════════ 七、Guard M8（线程生命周期与投影） ═════════════════

def _thread_graph() -> BodyDesign:
    open_q = _open('th01', 'question', 'ch1', '玉佩藏着什么')
    open_goal = _open('th02', 'goal', 'ch2', '夺回家产')
    advance = ThreadAction(ref='th01', op='advance', progress='查到玉佩来历')
    close_goal = ThreadAction(ref='th02', op='close', ending='abandoned',
                              resolution='家产已散，无力再争')
    return _graph(
        [_scene('s01', [_beat('b01', '开两条线', participants=('ch1', 'ch2'),
                              threads=(open_q, open_goal))]),
         _scene('s02', [_beat('b01', '推进与收束', participants=('ch1',),
                              threads=(advance, close_goal))]),
         _scene('s03', [_beat('b01', '同场无操作', participants=('ch1', 'ch3'))]),
         _scene('s04', [_beat('b01', '他人场面', participants=('ch3',))],
                participants=('ch3',))],
        [_assignment(1, ('s01',)),
         _assignment(2, ('s02', 's03', 's04'),
                     ChapterEntry(transition='immediate_continuation',
                                  recap='do_not_recap', anchor='s01#b01'))])


def test_m8_thread_projection_rules_and_guard():
    from novel.stages.stage4.guard import (
        check_thread_lifecycle, check_thread_projection, expected_thread_faces,
    )
    graph = _thread_graph()
    assert check_thread_lifecycle(graph) == []

    # s02：本场操作 → thread_ops（must_realize 数据面）；两条线都被操作 → 无开放投影
    ops, open_threads = expected_thread_faces(graph, graph.scenes[1])
    assert [(t.ref, t.op) for t in ops] == [('th01', 'advance'), ('th02', 'close')]
    assert ops[0].kind == 'question' and ops[0].subject == 'ch1'
    assert ops[0].text == '玉佩藏着什么' and ops[0].progress == '查到玉佩来历'
    assert ops[1].ending == 'abandoned'
    assert open_threads == ()

    # M8b 红例：IR 缺线程投影即拒；绿对照：按期望投影后通过
    context = _project('s02', _thread_graph())
    problems = check_thread_projection(graph, context)
    assert any('M8' in p for p in problems), problems
    fixed = context.model_copy(update={'verification_ir':
                                       context.verification_ir.model_copy(update={
                                           'thread_ops': tuple(ops),
                                           'open_threads': tuple(open_threads)})})
    assert check_thread_projection(graph, fixed) == []


def test_open_threads_project_by_participant_relevance():
    """E-4 分支的机械执行面：先判本场操作，再按参与者相关性投影开放线程
    （不相关不投影——全场景保护不等于每场提醒）。"""
    from novel.stages.stage4.guard import expected_thread_faces
    graph = _thread_graph()
    # s03（ch1 参与、无操作）：th01 仍开放且主体在场 → 投影（question 类，
    # renderer 按 guard_only 身份分节）；th02 已关闭 → 不投影
    _ops, open_s3 = expected_thread_faces(graph, graph.scenes[2])
    assert _ops == ()
    assert [(t.ref, t.kind, t.opened_at) for t in open_s3] == \
        [('th01', 'question', 's01#b01')]
    # s04（只有 ch3）：th01 开放中但主体不在场 → 不投影
    _ops, open_s4 = expected_thread_faces(graph, graph.scenes[3])
    assert _ops == () and open_s4 == ()


def test_m8_lifecycle_is_schema_carried_and_guard_reverifies():
    """生命周期判据（open 一次、操作不早于 open、close 后无操作、结局闭集）
    由 canonical schema 构造期承载；guard 对读回的图按同一判据复核（绿对照）。"""
    from novel.stages.stage4.guard import check_thread_lifecycle
    assert check_thread_lifecycle(_thread_graph()) == []


# ═════════════════ 八、升版常量（R2.9） ═════════════════

def test_r2_version_constants():
    from contracts.artifacts import STR06_ARTIFACT_RC_VERSIONS
    from contracts.schema.workflow import (
        STAGE4_DESIGN_PROTOCOL, STR06_RC_LABELS, STR06_RC_VERSION,
    )
    from novel.stages.common import RENDERER_VERSION, WRITER_PROTOCOL_VERSION
    assert STAGE4_DESIGN_PROTOCOL == 'stage4-design-v5'
    assert STR06_RC_VERSION == 'str06-rc26'          # 命名前移批升版式头；Design 形状本批不变。
    assert 'str06-rc10' in STR06_RC_LABELS
    assert RENDERER_VERSION == 'str06-render-v13'
    assert WRITER_PROTOCOL_VERSION == 'str06-body-protocol-v12'
    assert STR06_ARTIFACT_RC_VERSIONS['stage4/design/body_design.json'] == 'str06-rc25'
    assert STR06_ARTIFACT_RC_VERSIONS['stage4/design/presentation_graph.json'] == 'str06-rc25'
    # rc17（CS-H H3）：IR 因显示面分表（display_fallback）、必保揭名
    # （requires_released）与拍级释放（release_introductions）再升版。
    # rc18（CS-S 1b 补升）：IR 经 VerificationIR.obligations 内嵌 ObligationAnchor，
    # visibility 删除使 IR 形状变化——模型字面量与产物表同批钉 rc18（I-13）。
    assert STR06_ARTIFACT_RC_VERSIONS['stage4/ir/writer_book_ir.json'] == 'str06-rc25'
    from contracts.schema.book_ir import WriterBookIR
    assert WriterBookIR.model_fields['contract_version'].default == 'str06-rc25'
    # rc17（CS-H H3，RC-61）：BODY_VALIDATOR v2——必保揭名必现字面进章级机械校验。
    assert STR06_ARTIFACT_RC_VERSIONS['stage5/acceptance/body/ch{nn}.json'] \
        == 'str06-rc25'
    # rc16（CS-F2）：Binding 增 fact_bindings 而升版；LLM 面 Design wire 升 v8
    # （kf 句柄不再是状态键）。旧 rc12 Binding 按字面量拒读，不迁移（I-13）。
    # rc17（CS-H H3，RC-63）：BriefFactLock 增 subjects 再升版；
    # rc18（CS-S 1b）：ObligationAnchor.visibility 删——binding 再升版。
    from contracts.schema.design import STAGE4_DESIGN_WIRE_PROTOCOL
    from contracts.schema.grounding import SemanticBinding
    assert STR06_ARTIFACT_RC_VERSIONS['stage4/grounding/semantic_binding.json'] \
        == 'str06-rc26'
    assert STAGE4_DESIGN_WIRE_PROTOCOL == 'stage4-design-v11'
    assert SemanticBinding.model_fields['contract_version'].default == 'str06-rc26'


# ═════════════════ 九、Design wire v2（生产侧同步） ═════════════════

def _wire_v2() -> dict:
    """两章三拍的 design wire v2：threads（本地标签）＋world_rules＋entry。"""
    return {**sample_design_expression(('yun',)),
        'protocol_version': 'stage4-design-v5',
        'book_style': '冷峻克制的第三人称叙事。',
        'world_rules': ['术法须以寿命为代价'],
        'initial_state': [
            {'domain': 'world', 'subjects': [], 'holder': None,
             'key': 'possession', 'op': 'set', 'modality': 'actual', 'value': '烛火未点'}],
        'scenes': [
            {'state_reads': [],
             'driver': {'focal_actor': 'yun', 'immediate_want': '安度新婚',
                        'opposition': '夫君的怪异', 'turn': '立约'},
             'realization_brief': '重心在立约的落差；在旧约出口处收住。',
             'beats': [
                 {'event': '拜堂', 'participants': ['yun', 'fu_jun'],
                  'delivery_hint': '', 'target_delta': [],
                  'threads': [{'op': 'open', 'tag': 't1', 'kind': 'question',
                               'subject': 'yun', 'text': '玉佩上的字是谁刻的'}]},
                 {'event': '云娘起疑', 'participants': ['yun'],
                  'delivery_hint': '', 'target_delta': [],
                  'threads': [{'op': 'open', 'tag': 't2', 'kind': 'promise',
                               'subject': 'fu_jun', 'text': '再不隐瞒'}]}],
             'delivery': {'pov': 'yun', 'voice': '冷峻', 'avoid': []}},
            {'state_reads': [
                {'domain': 'world', 'subjects': [], 'key': 'possession', 'at': 1}],
             'driver': {'focal_actor': 'yun', 'immediate_want': '求证',
                        'opposition': '无凭据', 'turn': '发现玉佩'},
             'realization_brief': '重心在玉佩的发现；在字面露出时收束。',
             'beats': [
                 {'event': '灯下翻检', 'participants': ['yun'],
                  'delivery_hint': '', 'target_delta': [
                      {'domain': 'world', 'subjects': [], 'op': 'set', 'modality': 'actual', 'scope': 'book',
                       'key': 'possession', 'value': '烛火亮起'}],
                  'delivery_hint': '',
                  'threads': [{'op': 'advance', 'ref': 't1', 'progress': '查到玉佩来历'}]},
                 {'event': '玉佩收线', 'participants': ['yun', 'fu_jun'],
                  'delivery_hint': '', 'target_delta': [],
                  'threads': [{'op': 'close', 'ref': 't1', 'ending': 'answered',
                               'resolution': '刻着沈字，指向旧案'},
                              {'op': 'close', 'ref': 't2', 'ending': 'broken',
                               'resolution': '夫君仍有一事瞒她'}]}],
             'delivery': {'pov': 'yun', 'voice': '冷峻', 'avoid': []}}],
        'chapters': [{'purpose': '新婚之夜埋下旧约之疑', 'scenes': 1},
                     {'purpose': '玉佩之谜收线', 'scenes': 1,
                      'entry': {'transition': 'time_skip',
                                'carryover': '云娘的疑心未消',
                                'recap': 'brief_orientation'}}],
        'scene_weights': [1, 1],
    }


def _assemble_v2(wire):
    from novel.stages.stage4.design import assemble_body_design
    return assemble_body_design(wire, known_char_ids={'yun', 'fu_jun'}, expression_requirements=sample_requirements(), source_narration=sample_narration())


def test_wire_v2_assembles_threads_world_rules_and_entry():
    body, errors = _assemble_v2(_wire_v2())
    assert body is not None and not errors, errors
    # 线程：本地标签 → th{nn}（open 先后派发）；payload 按闭集落位
    beats = [b for s in body.scenes for b in s.beats]
    threads = [t for b in beats for t in b.threads]
    assert [(t.ref, t.op) for t in threads] == [
        ('th01', 'open'), ('th02', 'open'), ('th01', 'advance'),
        ('th01', 'close'), ('th02', 'close')]
    assert threads[0].kind == 'question' and threads[0].subject == 'yun'
    # world_rules 与 entry 原样落 canonical；第 1 章 entry 固定开篇
    assert body.world_rules == ('术法须以寿命为代价',)
    assert body.chapters[0].entry == ChapterEntry.opening()
    assert body.chapters[1].entry.carryover == '云娘的疑心未消'


def test_wire_v2_refuses_retired_four_lists_and_bad_shapes():
    bad = _wire_v2()
    bad['standing_goals'] = [{'char_id': 'yun', 'goal': '查清底细'}]
    _, errors = _assemble_v2(bad)
    assert any('Extra inputs' in e and 'standing_goals' in e for e in errors), errors

    bad = _wire_v2()
    bad['scenes'][1]['beats'][0]['threads'][0]['ref'] = 't_unknown'
    _, errors = _assemble_v2(bad)
    assert any('未开启' in e or '未知' in e for e in errors), errors

    bad = _wire_v2()
    bad['scenes'][0]['beats'][1]['threads'][0]['tag'] = 't1'   # 标签重复开启
    _, errors = _assemble_v2(bad)
    assert any('重复' in e for e in errors), errors


def test_wire_v2_entry_required_from_second_chapter():
    bad = _wire_v2()
    del bad['chapters'][1]['entry']
    _, errors = _assemble_v2(bad)
    assert any('entry' in e for e in errors), errors
    # 第 1 章 entry 可省（compiler 填固定开篇），但给错值即拒
    ok = _wire_v2()
    ok['chapters'][0]['entry'] = {'transition': 'time_skip', 'recap': 'do_not_recap'}
    _, errors = _assemble_v2(ok)
    assert any('开篇' in e for e in errors), errors
    good = _wire_v2()      # 第 1 章不带 entry：照常通过
    body, errors = _assemble_v2(good)
    assert body is not None and not errors, errors
    assert body.chapters[0].entry == ChapterEntry.opening()


def test_wire_v2_thread_subjects_close_against_known_chars():
    bad = _wire_v2()
    bad['scenes'][0]['beats'][0]['threads'][0]['subject'] = 'ghost'
    _, errors = _assemble_v2(bad)
    assert any('ThreadAction' in e and 'ghost' in e for e in errors), errors


def test_wire_v2_protocol_literal_is_checked():
    bad = _wire_v2()
    bad['protocol_version'] = 'stage4-design-v1'
    _, errors = _assemble_v2(bad)
    assert any('protocol_version' in e for e in errors), errors
