#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_r2_engine —— R2 引擎与渲染面：ThreadFold、解析来源依赖边、
背景补齐、世界约束通道、WriterPacket 分区渲染、Hook 分层与指标落盘。

【验什么】PLAN.md「R2 Stage4 语义送达与两层 IR」第 1–8 项的引擎侧（契约面
见 tests/test_str06_r2_contracts.py）：

  · ThreadFold（第 2/4 项）：独立线程折叠（共用 guard.scene_order 的阅读序），
    与 `expected_thread_faces`（M8 对账基准）逐场一致；
  · 依赖边（第 2 项）：改由**实际解析来源**生成——声明读取解析到的建立场、
    以及背景补齐的事实来源场；`last_writer` 位置扫描删除（红例一：拍级读取
    解析到本场自写值时，旧扫描仍产出跨场假边；红例二：参与者背景的事实来源
    场，旧扫描不产边）；
  · 背景补齐（第 3 项）：project_context 按参与者机械补入入场状态（人物域、
    参与者之间的关系域、持有者为参与者的知情域），以 background 分区呈现；
  · 世界约束通道（第 5 项）：Blueprint 的 world_constraints 机械填入
    ReconstructionBrief.book_constraints；BodyDesign 的 world_rules 以
    must_respect 进每章；
  · 分区渲染（第 1/5/6/7 项）：render_chapter_packet 渲染 WriterPacket 六分区
    （协议 v5），写手视图无内部 ID／字段名；承接分区由 entry＋上一章末场合成；
    Hook 分层渲染，线程收束并入禁区（E-3 读法 b：compiler 机械并入，不动 wire）；
  · 指标（第 8 项，E-6）：compile 写 stage4/acceptance/packet_metrics.json，
    produces／产物表／路径家族／送达登记（audit_only）同批闭合。

全程 fake/确定性构造，零付费；不跑 golden set。
"""
from __future__ import annotations

from contracts.schema.design import ChapterEntry, TargetDelta
from contracts.schema.state import EffectDomain, StateRead
from novel.stages.common import (
    RENDERER_VERSION, WRITER_PROTOCOL, WRITER_PROTOCOL_VERSION,
    project_chapter_packet,
)
from tests.str06_facilities import anchor_stub
from tests.str06_expression_inputs import (
    sample_narration, sample_requirements, sample_design_expression, sample_canonical_expression,
)

H64 = 'a' * 64

# ═════════════════════════ 富线程设计基座（三 章／四 场） ═════════════════════════


def _chars():
    from tests.test_str06_hook import _characters
    return _characters()


def _rich_wire() -> dict:
    """stage4-design-v5：threads（四条线覆盖开启前/推进/收束/结尾仍开放）、
    world_rules、entry、拍级读取与读者确认。线程文本避开第 1 章禁用字面。"""
    return {**sample_design_expression(('yun_niang',)),
        'protocol_version': 'stage4-design-v5',
        'book_style': '冷峻克制的限知叙事，重日常细节里的异样。',
        'world_rules': ['术法须以寿命为代价', '官府不涉异事'],
        'character_voices': [
            {'char_id': 'yun_niang', 'voice': '短句、少感叹，怀疑压在动作里'}],
        'initial_state': [
            {'domain': 'character', 'subjects': ['yun_niang'], 'holder': None,
             'key': 'appearance', 'op': 'set', 'modality': 'actual', 'value': '眉间一点朱砂'}],
        'initial_surfaces': [
            {'char_id': 'yun_niang', 'surfaces': ['苏芸娘']},
            {'char_id': 'fu_jun', 'surfaces': ['夫君']}],
        'scenes': [
            {  # sc01（第 1 章）
             'state_reads': [],
             'driver': {'focal_actor': 'yun_niang', 'immediate_want': '安度新婚',
                        'opposition': '夫君的怪异', 'turn': '立约'},
             'realization_brief': '重心在拜堂到立约的落差；在旧约出口处收住。',
             'beats': [
                 {'event': '拜堂成亲', 'participants': ['yun_niang', 'fu_jun'],
                  'delivery_hint': '', 'target_delta': [
                      {'domain': 'relation', 'subjects': ['yun_niang', 'fu_jun'],
                       'holder': None, 'key': 'marital_status', 'op': 'set', 'modality': 'actual',
                       'value': '结为夫妻', 'scope': 'book'}],
                  'delivery_hint': '喧闹与冷清对照', 'surface_introductions': []},
                 {'event': '洞房夜夫君立约',
                  'participants': ['yun_niang', 'fu_jun'],
                  'delivery_hint': '', 'target_delta': [],
                  'surface_introductions': [{'char_id': 'yun_niang',
                                             'surfaces': ['芸娘']}],
                  'threads': [
                      {'op': 'open', 'tag': 'q1', 'kind': 'question',
                       'subject': 'yun_niang', 'text': '玉佩上的字是谁刻的'},
                      {'op': 'open', 'tag': 'g1', 'kind': 'goal',
                       'subject': 'fu_jun', 'text': '夺回家产'}]},
                 {'event': '芸娘暗自起疑', 'participants': ['yun_niang'],
                  'delivery_hint': '', 'target_delta': [
                      {'domain': 'knowledge', 'subjects': ['yun_niang'],
                       'holder': 'yun_niang', 'key': 'vow', 'op': 'set',
                       'value': '她决意查清真相', 'scope': 'book',
                       'knowledge_mode': 'known'}],
                  'delivery_hint': '',
                  'surface_introductions': [],
                  'threads': [
                      {'op': 'open', 'tag': 'q2', 'kind': 'question',
                       'subject': 'yun_niang', 'text': '夫家为何避而不谈'}]}],
             'delivery': {'pov': 'yun_niang', 'voice': '冷峻', 'avoid': ['倒叙']}},
            {  # sc02（第 2 章）
             'state_reads': [
                 {'domain': 'relation', 'subjects': ['yun_niang', 'fu_jun'],
                  'holder': None, 'key': 'marital_status'},
                 {'domain': 'world', 'subjects': [], 'holder': None,
                  'key': 'light', 'at': 2}],
             'driver': {'focal_actor': 'yun_niang', 'immediate_want': '查明真相',
                        'opposition': '无凭据', 'turn': '发现玉佩'},
             'realization_brief': '重心在玉佩的发现；在字面露出的瞬间收束。',
             'beats': [
                 {'event': '夫君夜半离家', 'participants': ['fu_jun'],
                  'delivery_hint': '', 'target_delta': [
                      {'domain': 'character', 'subjects': ['fu_jun'], 'holder': None,
                       'key': 'whereabouts', 'op': 'set', 'modality': 'actual', 'value': '去向不明',
                       'scope': 'book'},
                      {'domain': 'world', 'subjects': [], 'holder': None,
                       'key': 'light', 'op': 'set', 'modality': 'actual', 'value': '烛火亮起',
                       'scope': 'scene'}],
                  'delivery_hint': '',
                  'surface_introductions': [],
                  'threads': [
                      {'op': 'advance', 'ref': 'q1', 'progress': '查到玉佩来历'}]},
                 {'event': '芸娘发现玉佩秘密', 'participants': ['yun_niang'],
                  'delivery_hint': '', 'target_delta': [
                      {'domain': 'knowledge', 'subjects': ['fu_jun'],
                       'holder': 'yun_niang', 'key': 'jade_secret', 'op': 'set',
                       'value': '玉佩刻着沈字', 'scope': 'book',
                       'knowledge_mode': 'known'},
                      {'domain': 'knowledge', 'subjects': [], 'holder': 'reader',
                       'key': 'reveal_jade', 'op': 'set',
                       'value': '玉佩刻着沈字，指向旧案', 'scope': 'book',
                       'knowledge_mode': 'known'}],
                  'delivery_hint': '',
                  'surface_introductions': [],
                  'threads': [
                      {'op': 'close', 'ref': 'q1', 'ending': 'answered',
                       'resolution': '刻着沈字，指向旧案'}]},
                 {'event': '夫君许诺坦白', 'participants': ['fu_jun'],
                  'delivery_hint': '', 'target_delta': [],
                  'surface_introductions': [],
                  'threads': [
                      {'op': 'open', 'tag': 'p1', 'kind': 'promise',
                       'subject': 'fu_jun', 'text': '再不隐瞒'}]}],
             'delivery': {'pov': 'yun_niang', 'voice': '冷峻', 'avoid': []}},
            {  # sc03（第 2 章，无线程操作：开放线程按参与者相关性投影）
             'state_reads': [],
             'driver': {'focal_actor': 'yun_niang', 'immediate_want': '试探夫君',
                        'opposition': '夫君搪塞', 'turn': '话到嘴边收回'},
             'realization_brief': '重心在同处一室的沉默张力；不点破玉佩。',
             'beats': [
                 {'event': '同处一室无言', 'participants': ['yun_niang', 'fu_jun'],
                  'delivery_hint': '', 'target_delta': [],
                  'surface_introductions': []}],
             'delivery': {'pov': 'yun_niang', 'voice': '冷峻', 'avoid': []}},
            {  # sc04（第 3 章：收线）
             'state_reads': [],
             'driver': {'focal_actor': 'yun_niang', 'immediate_want': '了断',
                        'opposition': '旧案牵连', 'turn': '各自转身'},
             'realization_brief': '重心在收束的干脆；留一条线不收。',
             'beats': [
                 {'event': '家产之争落幕', 'participants': ['yun_niang', 'fu_jun'],
                  'delivery_hint': '', 'target_delta': [],
                  'surface_introductions': [],
                  'threads': [
                      {'op': 'close', 'ref': 'g1', 'ending': 'abandoned',
                       'resolution': '家产已散，无力再争'},
                      {'op': 'close', 'ref': 'p1', 'ending': 'broken',
                       'resolution': '夫君仍有一事瞒她'}]}],
             'delivery': {'pov': 'yun_niang', 'voice': '冷峻', 'avoid': []}}],
        'chapters': [
            {'purpose': '新婚之夜埋下旧约之疑', 'scenes': 1},
            {'purpose': '玉佩之谜浮出水面', 'scenes': 2,
             'entry': {'transition': 'time_skip', 'carryover': '芸娘的疑心未消',
                       'recap': 'do_not_recap'}},
            {'purpose': '旧案各自了断', 'scenes': 1,
             'entry': {'transition': 'location_cut', 'recap': 'brief_orientation'}}],
        'scene_weights': [1, 1, 1, 1],
    }


def _rich_design():
    from novel.stages.stage4.design import assemble_body_design
    body, errors = assemble_body_design(_rich_wire(),
                                        known_char_ids={'yun_niang', 'fu_jun'}, expression_requirements=sample_requirements(), source_narration=sample_narration())
    assert body is not None and not errors, errors
    return body


def _rich_binding(body):
    from contracts.schema.grounding import (
        BindingEntry, PreserveCheckpoint, SemanticBinding, SourceLineage,
        SourceObligation,
    )
    from contracts.schema.policy import PreserveKind
    ob = SourceObligation(
        obligation_id='ob01', kind=PreserveKind.EVENT_CORE,
        statement='夫君立下报恩之约且芸娘起疑',
        anchors=anchor_stub(),
        lineage=SourceLineage(blueprint_refs=('b01_01',),
                              character_ids=('yun_niang', 'fu_jun')),
        must_preserve=True, satisfaction='正文呈现旧约成立与芸娘起疑')
    cp = PreserveCheckpoint(
        checkpoint_id='cp', policy_id='p', policy_version=1, characters_sha256=H64,
        blueprints_sha256=H64, provider='x', model='y', request_sha256='r' * 64,
        obligations=(ob,), checkpoint_sha256=H64)
    # CS-3 bind 退役：real 标注投影派生（支撑 sc01#b01→接受 sc01#b02）。
    sites = {f'{s.scene_id}#{b.beat_id}': (i, j)
             for i, s in enumerate(body.scenes) for j, b in enumerate(s.beats)}
    from novel.stages.stage4.grounding import derive_semantic_binding
    binding, errors = derive_semantic_binding(
        {'ob01': [sites['sc01#b01'], sites['sc01#b02']]}, body, cp)
    assert binding is not None and not errors, errors
    return binding


def _rich_plan():
    from novel.stages.stage4.compile import compile_execution_plan
    body = _rich_design()
    binding = _rich_binding(body)
    plan, errors = compile_execution_plan(body, binding)
    assert not errors, errors
    return body, binding, plan


def _rich_ir():
    from novel.stages.stage4.book_ir import compile_writer_book_ir
    body, binding, plan = _rich_plan()
    chars = _chars()
    ir = compile_writer_book_ir(graph=body, plan=plan, binding=binding,
                                total_budget=4000, length_band=(0.6, 1.3),
                                book_title='报恩',
                                projection=chars.name_projection(),
                                structure=chars.name_structure)
    return body, binding, plan, ir


# ═════════════════ 一、ThreadFold（独立线程折叠，第 2/4 项） ═════════════════

def test_thread_fold_faces_match_the_guard_baseline_on_every_scene():
    """ThreadFold 折叠结果与 M8 对账基准（expected_thread_faces）逐场一致：
    生产方（fold）与判据方（guard）各自实现、语义不漂移。"""
    from novel.stages.stage4.fold import ThreadFold
    from novel.stages.stage4.guard import expected_thread_faces
    body = _rich_design()
    fold = ThreadFold()
    for scene in body.scenes:
        ops, open_threads = fold.apply(scene)
        assert (ops, open_threads) == expected_thread_faces(body, scene), (
            f'{scene.scene_id} 的 ThreadFold 投影与 M8 基准不一致')


def test_thread_fold_covers_the_thread_lifecycle_faces():
    """逐类覆盖：开启前、本场操作（must_realize 数据面）、跨章携入、推进、
    收束、关闭后不再投影、结尾仍开放、与参与者相关性过滤。"""
    from novel.stages.stage4.fold import ThreadFold
    body = _rich_design()
    fold = ThreadFold()
    faces = {scene.scene_id: fold.apply(scene) for scene in body.scenes}
    # sc01（开启前）：三条线都在本场才开——入场快照不含尚未开启的线程；
    # 开启操作以 must_realize 数据面落位
    ops_sc01, open_sc01 = faces['sc01']
    assert [(t.ref, t.op) for t in ops_sc01] == [
        ('th01', 'open'), ('th02', 'open'), ('th03', 'open')]
    assert open_sc01 == (), '开启前（入场时点）不得投影尚未开启的线程'
    # sc02：advance＋close＋open 都在本场（must_realize 数据面）
    ops_sc02, open_sc02 = faces['sc02']
    assert [(t.ref, t.op) for t in ops_sc02] == [
        ('th01', 'advance'), ('th01', 'close'), ('th04', 'open')]
    # 声明内容来自 open（文本只写一次）；载荷按 op 落位
    assert ops_sc02[0].text == '玉佩上的字是谁刻的'
    assert ops_sc02[0].progress == '查到玉佩来历'
    assert ops_sc02[1].ending == 'answered'
    assert ops_sc02[1].resolution == '刻着沈字，指向旧案'
    # 入场快照不含本场操作过的线程（th01 已操作、th04 本场才开）
    assert {t.ref for t in open_sc02} == {'th02', 'th03'}
    # sc03（无操作）：跨章携入的开放线程按参与者相关性投影；th01 已关闭不投影
    ops_sc03, open_sc03 = faces['sc03']
    assert ops_sc03 == ()
    assert {t.ref for t in open_sc03} == {'th02', 'th03', 'th04'}
    # sc04：收线后书末仍开放的 th03（question，结尾仍开放）继续投影
    _ops_sc04, open_sc04 = faces['sc04']
    assert {t.ref for t in open_sc04} == {'th03'}


def test_compile_projects_thread_faces_into_ir_and_m8_passes():
    """真实编译链：ThreadFold 的投影进 VerificationIR（thread_ops／open_threads），
    M8 对账全绿（红例：投影缺位时 M8 必红，见契约批 M8 测试）。"""
    from novel.stages.stage4.guard import run_mechanical_guard
    body, binding, plan, ir = _rich_ir()
    sc02 = ir.chapter(2).scenes[0]
    assert [(t.ref, t.op) for t in sc02.verification_ir.thread_ops] == [
        ('th01', 'advance'), ('th01', 'close'), ('th04', 'open')]
    for chapter in ir.chapters:
        for context in chapter.scenes:
            problems = run_mechanical_guard(binding=binding, context=context,
                                            graph=body)
            assert not problems, (context.scene_id, problems)


def test_sc03_open_threads_split_by_kind_in_ir():
    """E-4 分支的数据面：开放线程按参与者相关性进 open_threads，kind 供
    renderer 分区（question→禁区，其余→承接）。"""
    _body, _binding, _plan, ir = _rich_ir()
    sc03 = ir.chapter(2).scenes[1]
    assert {(t.ref, t.kind) for t in sc03.verification_ir.open_threads} == {
        ('th02', 'goal'), ('th03', 'question'), ('th04', 'promise')}
    assert sc03.verification_ir.thread_ops == ()


def test_design_thread_mutations_flow_to_packets_by_consumer():
    """AUD-04 反例正身（REQUIREMENTS「AUD 审计反例」第 4 行）：Design 级线程的
    增／改／删经真实编译链改变**实际消费该条目**的章包身份与阅读面；无操作
    且主体不相关的章不变。R2 前红：旧静态跨章列表加字段后 WriterIR 完全不变。
    基座：ch1 开 goal(th01)＋question(th02)；ch2 推进 th01、收束 th02；
    ch3（ch1 参与、无操作）携入开放的 th01；ch4（只有 ch3）不相关不投影。"""
    from contracts.schema.design import (
        BodyDesign, ChapterAssignment, ChapterEntry, SceneDelivery, SceneDriver,
        TargetBeat, TargetScene, ThreadAction,
    )
    from contracts.schema.state import SurfaceIntroduction
    from novel.stages.stage4.book_ir import compile_writer_book_ir
    from novel.stages.stage4.compile import compile_execution_plan
    from novel.stages.stage4.guard import run_mechanical_guard
    from novel.stages.common import packet_sha256

    def _scene(sid, participants, threads=()):
        return TargetScene(
            scene_id=sid, state_reads=(),
            driver=SceneDriver(focal_actor=participants[0], immediate_want='w',
                               opposition='o', turn='t'),
            realization_brief=f'重心在{sid}；展开后收束。',
            beats=(TargetBeat(beat_id='b01', event=f'{sid}的当拍事件',
                              participants=participants, delivery_hint='',
                              target_delta=(), threads=threads),),
            delivery=SceneDelivery(pov=participants[0], voice='冷峻'))

    def _design():
        return BodyDesign(**sample_canonical_expression((_scene('s01', ('ch1', 'ch2'), (
                        ThreadAction(ref='th01', op='open', kind='goal',
                                     subject='ch1', text='夺回家产'),
                        ThreadAction(ref='th02', op='open', kind='question',
                                     subject='ch1', text='玉佩藏着什么'))),
                    _scene('s02', ('ch1', 'ch2'), (
                        ThreadAction(ref='th01', op='advance', progress='查到线索'),
                        ThreadAction(ref='th02', op='close', ending='answered',
                                     resolution='刻着沈字，指向旧案'))),
                    _scene('s03', ('ch1',)),
                    _scene('s04', ('ch3',)))),
            graph_id='tm', book_style='冷峻克制的第三人称叙事。',
            initial_state=(),
            initial_surfaces=(SurfaceIntroduction(char_id='ch1', surfaces=('阿砚',)),
                              SurfaceIntroduction(char_id='ch2', surfaces=('阿璧',)),
                              SurfaceIntroduction(char_id='ch3', surfaces=('掌柜',))),
            scenes=(_scene('s01', ('ch1', 'ch2'), (
                        ThreadAction(ref='th01', op='open', kind='goal',
                                     subject='ch1', text='夺回家产'),
                        ThreadAction(ref='th02', op='open', kind='question',
                                     subject='ch1', text='玉佩藏着什么'))),
                    _scene('s02', ('ch1', 'ch2'), (
                        ThreadAction(ref='th01', op='advance', progress='查到线索'),
                        ThreadAction(ref='th02', op='close', ending='answered',
                                     resolution='刻着沈字，指向旧案'))),
                    _scene('s03', ('ch1',)),
                    _scene('s04', ('ch3',))),
            chapters=(
                ChapterAssignment(chapter_ordinal=1, purpose='开线',
                                  scene_ids=('s01',), entry=ChapterEntry.opening()),
                ChapterAssignment(chapter_ordinal=2, purpose='推进与收束',
                                  scene_ids=('s02',),
                                  entry=ChapterEntry(transition='time_skip',
                                                     recap='do_not_recap')),
                ChapterAssignment(chapter_ordinal=3, purpose='携入',
                                  scene_ids=('s03',),
                                  entry=ChapterEntry(transition='location_cut',
                                                     recap='brief_orientation')),
                ChapterAssignment(chapter_ordinal=4, purpose='不相关',
                                  scene_ids=('s04',),
                                  entry=ChapterEntry(transition='new_scene',
                                                     recap='do_not_recap'))))

    def _compile(graph):
        binding = _no_obligation_binding()
        plan, errors = compile_execution_plan(graph, binding)
        assert not errors, errors
        ir = compile_writer_book_ir(graph=graph, plan=plan, binding=binding,
                                    total_budget=1000, length_band=(0.6, 1.3),
                                    projection=_chars().name_projection(),
                                    structure=None)
        for chapter in ir.chapters:
            for context in chapter.scenes:
                problems = run_mechanical_guard(binding=binding, context=context,
                                                graph=graph)
                assert not problems, (context.scene_id, problems)
        return ir

    def _digests(ir):
        return {n: packet_sha256(project_chapter_packet(ir, n))
                for n in (1, 2, 3, 4)}

    def _with_scene_threads(graph, sid, threads):
        scenes = tuple(s.model_copy(update={'beats': (
            s.beats[0].model_copy(update={'threads': tuple(threads)}),)})
            if s.scene_id == sid else s for s in graph.scenes)
        return graph.model_copy(update={'scenes': scenes})

    # ── 基线：各消费面把线程送达对应分区（M8 对账全绿）──
    base_ir = _compile(_design())
    base = _digests(base_ir)
    assert '推进线索' in _blocks(_render(base_ir, 2))['必须写出']
    assert '查到线索' in _blocks(_render(base_ir, 2))['必须写出']
    assert '收束线索' in _blocks(_render(base_ir, 2))['必须写出']
    assert '刻着沈字，指向旧案' in _blocks(_render(base_ir, 2))['必须写出']
    assert '夺回家产' in _blocks(_render(base_ir, 3))['承接'], \
        '开放的 goal 由无操作的相关章以 continuity 携入'
    assert '玉佩藏着什么' not in _render(base_ir, 3), \
        'th02 已收束——收束后不再投影'
    assert '夺回家产' not in _render(base_ir, 4), \
        '主体不相关的章不投影（全场景保护不等于每场提醒）'

    # ── 增：ch1 新开一条 ch3 为主体的线——操作章与主体相关的 ch4 变，其余不变 ──
    added = _with_scene_threads(_design(), 's01', (
        ThreadAction(ref='th01', op='open', kind='goal', subject='ch1',
                     text='夺回家产'),
        ThreadAction(ref='th02', op='open', kind='question', subject='ch1',
                     text='玉佩藏着什么'),
        ThreadAction(ref='th03', op='open', kind='goal', subject='ch3',
                     text='守住药铺')))
    ir_added = _compile(added)
    changed = {n for n, d in _digests(ir_added).items() if d != base[n]}
    assert changed == {1, 4}, changed
    assert '守住药铺' in _blocks(_render(ir_added, 4))['承接'], \
        '跨章携入的新线到达主体相关的远章'
    assert '守住药铺' not in _render(ir_added, 3)

    # ── 改（open 文本只写一次）：操作章、携推进载荷的章与携入章都变 ──
    retitled = _with_scene_threads(_design(), 's01', (
        ThreadAction(ref='th01', op='open', kind='goal', subject='ch1',
                     text='夺回家业'),
        ThreadAction(ref='th02', op='open', kind='question', subject='ch1',
                     text='玉佩藏着什么')))
    ir_retitled = _compile(retitled)
    changed = {n for n, d in _digests(ir_retitled).items() if d != base[n]}
    assert changed == {1, 2, 3}, changed
    assert '夺回家业' in _blocks(_render(ir_retitled, 2))['必须写出']
    assert '夺回家业' in _blocks(_render(ir_retitled, 3))['承接']
    assert '夺回家产' not in _render(ir_retitled, 3)

    # ── 改（advance 进展）：只动承载该操作的章 ──
    progressed = _with_scene_threads(_design(), 's02', (
        ThreadAction(ref='th01', op='advance', progress='查到新线索'),
        ThreadAction(ref='th02', op='close', ending='answered',
                     resolution='刻着沈字，指向旧案')))
    ir_progressed = _compile(progressed)
    changed = {n for n, d in _digests(ir_progressed).items() if d != base[n]}
    assert changed == {2}, changed
    assert '查到新线索' in _blocks(_render(ir_progressed, 2))['必须写出']

    # ── 删（收束）：th02 结尾仍开放（合法显式状态）→ 携入章新增禁区 ──
    unclosed = _with_scene_threads(_design(), 's02', (
        ThreadAction(ref='th01', op='advance', progress='查到线索'),))
    ir_unclosed = _compile(unclosed)
    changed = {n for n, d in _digests(ir_unclosed).items() if d != base[n]}
    assert changed == {2, 3}, changed
    assert '玉佩藏着什么' in _blocks(_render(ir_unclosed, 3))['禁区'], \
        '删除收束后开放的问题线程改以 guard_only 保护'


# ═════════════════ 二、依赖边改由实际解析来源生成（第 2 项） ═════════════════

def _character_delta(key: str, value: str) -> TargetDelta:
    return TargetDelta(modality='actual',domain=EffectDomain.CHARACTER, subjects=('ch1',),
                       holder=None, key=key, op='set', value=value, scope='book')


def _world_delta(key: str, value: str, scope: str = 'book') -> TargetDelta:
    return TargetDelta(modality='actual',domain=EffectDomain.WORLD, subjects=(), holder=None,
                       key=key, op='set', value=value, scope=scope)


def _no_obligation_binding():
    """一条非必保、未承载的占位义务：义务分母闭合，边只来自解析与背景。"""
    from contracts.schema.grounding import (
        SemanticBinding, SourceLineage, SourceObligation, UnboundEntry,
    )
    from contracts.schema.policy import PreserveKind
    return SemanticBinding(
        binding_id='b', preserve_checkpoint_sha256=H64,
        obligations=(SourceObligation(
            obligation_id='ob00', kind=PreserveKind.EVENT_CORE, statement='占位',
            anchors=anchor_stub(),
            lineage=SourceLineage(blueprint_refs=('b01_01',), character_ids=()),
            must_preserve=False, satisfaction=''),),
        entries=(),
        unbound=(UnboundEntry(obligation_id='ob00', owner='design',
                              reason='依赖边用例不承载'),))


def _plan_edges(graph):
    from novel.stages.stage4.compile import compile_execution_plan
    plan, errors = compile_execution_plan(graph, _no_obligation_binding())
    assert not errors, errors
    return {edge.before: edge for scene in plan.scenes
            for edge in scene.dependencies}


def test_beat_read_resolving_to_own_scene_write_yields_no_cross_scene_edge():
    """红例一（旧 last_writer 的假边）：拍级读取解析到**本场自写**的
    scene 作用域值时，实际解析来源在本场——不应产出跨场依赖边。"""
    from tests.test_str06_r2_contracts import _assignment, _beat, _graph, _scene
    graph = _graph(
        [_scene('s01', [_beat('b01', '建立', deltas=(_world_delta('x', 'v1'),))]),
         _scene('s02', [_beat('b01', '场内点灯',
                              deltas=(_world_delta('x', '场内新值', scope='scene'),)),
                         _beat('b02', '灯下翻检')],
                reads=(StateRead(domain=EffectDomain.WORLD, key='x',
                                 subjects=(), holder=None, at='b02'),))],
        [_assignment(1, ('s01',)),
         _assignment(2, ('s02',), ChapterEntry(transition='new_scene',
                                               recap='do_not_recap'))])
    edges = _plan_edges(graph)
    assert edges == {}, (
        '拍级读取解析到本场自写值时不应有跨场边（旧 last_writer 扫描会产出假边 '
        f'(s01, s02)）：{edges}')


def test_participant_background_source_scene_yields_dependency_edge():
    """红例二（旧扫描漏边）：s02 没有声明读取，但携带参与者——其背景补齐
    事实的来源场（s01）必须成为依赖边（依赖算完整，Machine IR 以完整为先）。"""
    from tests.test_str06_r2_contracts import _assignment, _beat, _graph, _scene
    graph = _graph(
        [_scene('s01', [_beat('b01', '建立人物事实',
                              deltas=(_character_delta('appearance', '眉间一点朱砂'),))]),
         _scene('s02', [_beat('b01', '后场')])],
        [_assignment(1, ('s01',)),
         _assignment(2, ('s02',), ChapterEntry(transition='new_scene',
                                               recap='do_not_recap'))])
    edges = _plan_edges(graph)
    assert 's01' in edges and edges['s01'].after == 's02', (
        f'参与者背景的事实来源场应为依赖边：{edges}')


def test_declared_read_resolution_source_yields_edge():
    """绿对照：入场读取解析到 s01 的 book 写入 → 边 (s01, s02)。"""
    from tests.test_str06_r2_contracts import _assignment, _beat, _graph, _scene
    graph = _graph(
        [_scene('s01', [_beat('b01', '建立', deltas=(_world_delta('x', 'v1'),))]),
         _scene('s02', [_beat('b01', '后场')],
                reads=(StateRead(domain=EffectDomain.WORLD, key='x',
                                 subjects=(), holder=None),))],
        [_assignment(1, ('s01',)),
         _assignment(2, ('s02',), ChapterEntry(transition='new_scene',
                                               recap='do_not_recap'))])
    edges = _plan_edges(graph)
    assert 's01' in edges and edges['s01'].after == 's02', edges


# ═════════════════ 三、背景补齐（第 3 项，background 呈现） ═════════════════

def _kaddr(entry) -> tuple:
    return ('knowledge', entry.subjects, entry.holder, entry.key)


def test_project_context_supplements_participant_background():
    """按参与者机械补齐入场状态，R10b 起写手面再收窄（C7/C10）：

    · 补齐面（`participant_background` 全量，依赖边生成同消费）不受收窄
      影响——这里同时断言全量面与投影后的写手面；
    · 写手面只留书级初态立足点；只写一次无消费者的拍状态（vow／
      whereabouts／jade_secret）不向后投影；
    · 已声明读取的地址不重复进背景（must_respect 已送达）；
    · 世界域不是补齐面。"""
    from novel.stages.common import participant_background
    from novel.stages.stage4.fold import fold_accepted_prefix
    _body, _binding, _plan, ir = _rich_ir()
    sc02 = ir.chapter(2).scenes[0]
    vir = sc02.verification_ir
    # 声明读取（关系 marital_status 入场）解析进 entry 视图
    assert [e.key for e in vir.entry_state] == ['marital_status']
    assert vir.entry_state[0].value == '结为夫妻'
    # 补齐面（无收窄）：人物域（appearance）＋知情域（vow，持有者参与）；
    # 关系域已被声明读取送达，不重复进背景。
    scene_ids = [s.scene_id for s in _body.scenes]
    folded = fold_accepted_prefix(
        _plan.scenes[:scene_ids.index('sc02')],
        initial=_plan.initial_establishments)
    full_state, full_knowledge = participant_background(
        scene=next(s for s in _body.scenes if s.scene_id == 'sc02'),
        folded_prefix=folded)
    assert [e.key for e in full_state] == ['appearance']
    assert [e.key for e in full_knowledge] == ['vow']
    # 写手面收窄：appearance（init 立足点）保留；vow（sc01 拍建立、后文
    # 无消费者）不向后投影（C7）。
    assert [e.key for e in vir.background_state] == ['appearance']
    assert [e.key for e in vir.background_knowledge] == []
    # sc03（无声明读取）：whereabouts／jade_secret／vow 同为无消费者拍
    # 状态，均不进背景；只剩 init 立足点。
    sc03 = ir.chapter(2).scenes[1]
    assert [e.key for e in sc03.verification_ir.background_state] == [
        'appearance']
    assert sc03.verification_ir.background_knowledge == ()


def test_background_excludes_declared_read_addresses_and_world():
    _body, _binding, _plan, ir = _rich_ir()
    sc02 = ir.chapter(2).scenes[0]
    vir = sc02.verification_ir
    entry_addrs = ({e.address() for e in vir.entry_state}
                   | {_kaddr(e) for e in vir.entry_knowledge})
    bg_addrs = ({e.address() for e in vir.background_state}
                | {_kaddr(e) for e in vir.background_knowledge})
    assert not entry_addrs & bg_addrs
    assert all(e.domain is not EffectDomain.WORLD for e in vir.background_state)


# ═════════════════ 四、世界约束通道（第 5 项） ═════════════════

def test_project_brief_fills_book_constraints_from_blueprint():
    """Blueprint 的 world_constraints 机械填入 ReconstructionBrief.book_constraints
    （原为恒空——AUD 反例「世界约束没有通道」）。"""
    from contracts.schema.source import WorldConstraint
    from novel.stages.stage4.grounding import assemble_preserve, project_brief
    from tests.test_str06_target import _preserve_wire, _source_pair
    chars, bp = _source_pair()
    wire = _preserve_wire()
    cp, errors = assemble_preserve(wire, policy_id='p', policy_version=1,
                                   characters=chars, blueprint=bp,
                                   provider='x', model='y', request_sha256='r' * 64)
    assert not errors, errors
    empty_brief, _ = project_brief(cp, chars, bp)
    assert empty_brief.book_constraints == ()
    # 带世界约束的 Blueprint：statement 原样机械投影（无语义推断）
    bp = bp.model_copy(update={'world_constraints': (
        WorldConstraint(constraint_id='wc_1', kind='hard_rule',
                        statement='术法须以寿命为代价',
                        source_refs=('b01_01',),
                        basis=({'segment': 'e01_01', 'quote': '术法须以寿命为代价'},)),
        WorldConstraint(constraint_id='wc_2', kind='social_norm',
                        statement='官府不涉异事', source_refs=('b01_01',),
                        basis=({'segment': 'e01_01', 'quote': '官府不涉异事'},)))})
    brief, _ = project_brief(cp, chars, bp)
    assert [(w.constraint_id, w.statement) for w in brief.book_constraints] ==         [('wc_1', '术法须以寿命为代价'), ('wc_2', '官府不涉异事')]


def test_world_rules_reach_every_chapter_as_must_respect():
    """BodyDesign.world_rules 以 must_respect 进每章（世界边界，不要讲解）。"""
    _body, _binding, _plan, ir = _rich_ir()
    assert ir.book_brief.world_rules == ('术法须以寿命为代价', '官府不涉异事')
    for ordinal in (1, 2, 3):
        packet = project_chapter_packet(ir, ordinal)
        assert packet.book_brief.world_rules == ir.book_brief.world_rules


# ═════════════════ 五、WriterPacket 分区渲染（第 1/5/6/7 项） ═════════════════

SECTIONS = ('必须写出', '不可违背', '承接', '导演说明', '背景', '禁区')


def _render(ir, ordinal):
    from novel.stages.common import render_chapter_packet
    return render_chapter_packet(project_chapter_packet(ir, ordinal))


def _blocks(text: str) -> dict[str, str]:
    """按六分区标题切块（分区归属断言的比较面；协议文本不使用【】标题）。"""
    marks = [f'【{name}】' for name in SECTIONS]
    starts = [text.find(m) for m in marks]
    assert -1 not in starts, f'渲染缺分区标题：{marks}（{starts}）'
    ordered = sorted(range(len(marks)), key=lambda i: starts[i])
    blocks = {}
    for pos, i in enumerate(ordered):
        end = starts[ordered[pos + 1]] if pos + 1 < len(ordered) else len(text)
        blocks[SECTIONS[i]] = text[starts[i]:end]
    return blocks


def test_render_has_six_partitions_and_protocol_v5():
    _body, _binding, _plan, ir = _rich_ir()
    text = _render(ir, 2)
    for name in SECTIONS:
        assert f'【{name}】' in text
    # 协议 v7 按三类身份组织（六分区仍在）；版本常量由契约批登记
    assert WRITER_PROTOCOL_VERSION == 'str06-body-protocol-v7'
    assert RENDERER_VERSION == 'str06-render-v8'     # 批0 D6：命题内容送达
    for name in SECTIONS:
        assert name in WRITER_PROTOCOL
    # 同章包 ⇒ 同字节
    assert text == _render(ir, 2)


def test_writer_view_carries_no_internal_ids_or_field_names():
    """写手视图清零：无 场景/拍 复合 ID、线程 id、established_by、字段名、
    char slug、禁用字面、知情模式 slug。"""
    _body, _binding, _plan, ir = _rich_ir()
    for ordinal in (1, 2, 3):
        text = _render(ir, ordinal)
        for token in ('sc01', 'sc02', 'sc03', 'sc04', 'b01', 'b02', 'b03',
                      'th01', 'th02', 'th03', 'th04',
                      'established_by', 'execution_slice_sha256', 'lineage',
                      'blueprint_refs', 'marital_status', 'jade_secret',
                      'yun_niang', 'fu_jun', 'known', 'suspected',
                      'contract_version', 'release_schedule'):
            assert token not in text, f'第{ordinal}章渲染泄漏 {token!r}'
        # 禁用字面（第 1 章未释放的专名构词）不进阅读面
        for literal in ('陆大哥', '陆家', '苏姑娘'):
            assert literal not in _render(ir, 1), literal


def test_partition_membership_of_delivered_values():
    """R0 检查 3 的分区面：各送达值只出现在它 delivery_mode 对应的分区。"""
    _body, _binding, _plan, ir = _rich_ir()
    blocks1 = _blocks(_render(ir, 1))
    blocks2 = _blocks(_render(ir, 2))
    # must_realize：拍事件、义务满足条件（义务承载在 sc01）、线程操作
    assert '夫君夜半离家' in blocks2['必须写出']
    assert '夫君立下报恩之约且芸娘起疑' in blocks1['必须写出']
    assert '正文呈现旧约成立与芸娘起疑' in blocks1['必须写出']
    assert '玉佩上的字是谁刻的' in blocks2['必须写出']
    assert '刻着沈字，指向旧案' in blocks2['必须写出']
    # must_respect：入场/拍级依赖事实、世界规则、称谓许可、场景 avoid（sc01）
    assert '结为夫妻' in blocks2['不可违背']
    assert '烛火亮起' in blocks2['不可违背']
    assert '术法须以寿命为代价' in blocks2['不可违背']
    assert '倒叙' in blocks1['不可违背']
    # continuity：上一章末场＋entry 合成；无操作的相关开放线程
    assert '上一章收在' in blocks2['承接']
    assert '芸娘暗自起疑' in blocks2['承接']      # 第 1 章末场末拍事件
    assert '芸娘的疑心未消' in blocks2['承接']    # entry.carryover
    assert '夺回家产' in blocks2['承接']          # th02（goal）开放、与参与者相关
    # creative_guidance：导演说明、驱动、风格、声线、表达提示、章目的
    assert '重心在玉佩的发现' in blocks2['导演说明']
    assert '冷峻克制的限知叙事' in blocks2['导演说明']
    assert '短句、少感叹' in blocks2['导演说明']
    assert '玉佩之谜浮出水面' in blocks2['导演说明']
    # background：编译器按参与者补齐的入场状态
    assert '眉间一点朱砂' in blocks2['背景']
    # guard_only：受保护的问题线程与未到时点的读者确认
    assert '夫家为何避而不谈' in blocks2['禁区']
    assert '回答' in blocks2['禁区']


def test_continuity_partition_phrases_entry_naturally():
    """承接措辞：上一章收在……＋transition/recap 措辞；第 1 章固定开篇。"""
    _body, _binding, _plan, ir = _rich_ir()
    blocks2 = _blocks(_render(ir, 2))
    assert '上一章收在' in blocks2['承接']
    assert '时间跳跃' in blocks2['承接']       # transition=time_skip
    assert '不复述' in blocks2['承接']         # recap=do_not_recap
    blocks3 = _blocks(_render(ir, 3))
    assert '同处一室无言' in blocks3['承接']   # 第 2 章末场末拍事件
    assert '重新定向' in blocks3['承接']       # recap=brief_orientation
    blocks1 = _blocks(_render(ir, 1))
    assert '开篇' in blocks1['承接']


def test_pending_confirmation_phrased_naturally_in_guard_only():
    """读者确认以自然时点表述（不带 地址/拍 id 机械位），落在禁区。"""
    _body, _binding, _plan, ir = _rich_ir()
    blocks2 = _blocks(_render(ir, 2))
    assert '玉佩刻着沈字，指向旧案' in blocks2['禁区']
    assert '才可向读者确认' in blocks2['禁区']


def test_build_writer_packet_is_the_partition_model():
    """WriterPacket 由 renderer 从章包确定性生成（I-11 唯一 canonical 模型）。"""
    from novel.stages.common import build_writer_packet
    _body, _binding, _plan, ir = _rich_ir()
    packet = project_chapter_packet(ir, 2)
    wp = build_writer_packet(packet)
    assert wp.chapter_ordinal == 2
    assert wp.must_realize and wp.must_respect and wp.continuity
    assert wp.creative_guidance and wp.background and wp.guard_only
    assert build_writer_packet(packet) == wp        # 确定性
    mutated = build_writer_packet(packet.model_copy(update={
        'chapter': packet.chapter.model_copy(update={'purpose': '变异章目九九'})}))
    assert mutated.creative_guidance != wp.creative_guidance


# ═════════════════ 六、Hook 分层渲染与线程收束禁区（E-3 读法 b） ═════════════════

def _hook_pair():
    from contracts.schema.hook import HookConstraints
    from novel.stages.stage4.hook import assemble_hook_design, publish_graph
    from novel.stages.stage4.hook_context import compile_hook_context
    body = _rich_design()
    wire = {
        'protocol_version': 'stage4-hook-design-v1',
        'pov': {'person': 'first', 'focalizers': ['yun_niang']},
        'opening_facts': [
            {'ref': 'init',
             'address': {'domain': 'character', 'subjects': ['yun_niang'],
                         'holder': None, 'key': 'appearance'},
             'audience': 'world'}],
        'materials': [
            {'beat_ref': {'scene': 'sc02', 'beat': 'b01'}, 'withheld': [],
             'presentation': '深夜，一个身影翻出后墙'},
            {'beat_ref': {'scene': 'sc01', 'beat': 'b02'}, 'withheld': [],
             'presentation': '新婚夜，丈夫低声说起一桩旧约'}],
        'concealed': ['fu_jun'],
        'surface_releases': [
            {'char_id': 'yun_niang', 'surfaces': ['苏芸娘', '芸娘']}],
        'forbidden_reveals': [
            {'ref': {'scene': 'sc02', 'beat': 'b02'},
             'address': {'domain': 'knowledge', 'subjects': ['fu_jun'],
                         'holder': 'yun_niang', 'key': 'jade_secret'}}],
        'design_brief': '从深夜冷开倒叙入新婚夜旧约，玉佩之谜不露。',
        'delivery_brief': '第一人称冷面叙述，短句，不解释。',
    }
    hook, errors = assemble_hook_design(wire, body=body,
                                        known_char_ids={'yun_niang', 'fu_jun'},
                                        name_projection=_chars().name_projection())
    assert hook is not None and not errors, errors
    graph = publish_graph(body, hook)
    context, errors = compile_hook_context(
        graph, _chars().name_projection(),
        HookConstraints(target_chars=280, hard_max_chars=360))
    assert context is not None and not errors, errors
    return graph, context


def test_hook_forbidden_zone_merges_unreached_thread_closes():
    """E-3 读法 b：compiler 把未到时点的线程 close 机械并入禁区（不动 wire）——
    材料视界（sc02#b01）外收束的线索（th01/th02）与视界内开启未收的未决问题
    （th03）都进 forbidden_reveals；视界后才开启的线索（th04）不进。"""
    _graph, context = _hook_pair()
    forbidden = '\n'.join(context.forbidden_reveals)
    # 设计侧条目照旧（玉佩事实）
    assert '玉佩' in forbidden
    # th01（question，视界 sc02#b01 之后于 sc02#b02 收束）：收束不得提前兑现
    assert '玉佩上的字是谁刻的' in forbidden
    assert '刻着沈字，指向旧案' in forbidden
    # th02（goal，sc04 才收束）：结局不得提前兑现
    assert '夺回家产' in forbidden and '家产已散，无力再争' in forbidden
    # th03（question，视界内开启、全书未收）：不得在楔子中给出答案
    assert '夫家为何避而不谈' in forbidden
    # th04（promise，视界 sc02#b01 之后才开启）：不在禁区
    assert '再不隐瞒' not in forbidden and '夫君仍有一事瞒她' not in forbidden


def test_hook_prompt_is_layered_without_json_dump():
    """Hook 请求分层渲染：材料必须写出、禁区、起笔事实（延续/背景）；
    不再整份 JSON 倾倒（withheld_literals／beat_ref／contract_version 不进）。"""
    from novel.stages.stage5.hook_writer import build_hook_user_prompt
    _graph, context = _hook_pair()
    prompt = build_hook_user_prompt(context)
    assert 'HookContext:' not in prompt and '```' not in prompt
    for token in ('contract_version', 'established_by', 'sc02', 'b01',
                  'audience', 'yun_niang', 'first'):
        assert token not in prompt, f'Hook 渲染泄漏 {token!r}'
    # withheld_literals 是 validator 材料，不进阅读面（列表字面不出现）
    assert '陆大哥' not in prompt and '陆家' not in prompt
    # 分层：材料／禁区／起笔事实
    assert '必须写出' in prompt and '禁止' in prompt and '起笔' in prompt
    assert '深夜，一个身影翻出后墙' in prompt
    assert '玉佩' in prompt
    assert '眉间一点朱砂' in prompt


def test_hook_writer_system_layers_the_same_way():
    from novel.stages.common import HOOK_WRITER_SYSTEM
    for token in ('必须写出', '禁止', '起笔'):
        assert token in HOOK_WRITER_SYSTEM
    assert 'forbidden_reveals' not in HOOK_WRITER_SYSTEM


# ═════════════════ 七、packet_metrics（第 8 项，E-6 同批闭合） ═════════════════

def test_packet_metrics_reports_per_chapter_readouts():
    from novel.stages.common import packet_metrics
    _body, _binding, _plan, ir = _rich_ir()
    metrics = packet_metrics(ir)
    chapters = {c['chapter_ordinal']: c for c in metrics['chapters']}
    assert sorted(chapters) == [1, 2, 3]
    for ordinal, chapter in chapters.items():
        assert chapter['writer_view_chars'] > 0
        for mode in ('must_realize', 'must_respect', 'continuity',
                     'creative_guidance', 'background', 'guard_only'):
            assert chapter['section_entries'][mode] >= 0
        assert chapter['world_rules'] == 2
        assert chapter['target_chars'] == ir.chapter(ordinal).length_budget
        assert chapter['must_realize_density'] == round(
            chapter['section_entries']['must_realize']
            / chapter['target_chars'], 4)
    # 活跃线程（本场有操作）与受保护线程（开放中的问题线）
    assert chapters[2]['active_threads'] == 2      # th01（推进＋收束）、th04（开启）
    assert chapters[2]['protected_threads'] == 1   # th03（question 开放）
    assert chapters[3]['active_threads'] == 2      # th02、th04 收束
    # 相邻章在延续与背景两节中的重复比例：第 1 章无上一章
    assert chapters[1]['continuity_background_repeat_ratio'] is None
    for ordinal in (2, 3):
        ratio = chapters[ordinal]['continuity_background_repeat_ratio']
        assert ratio is None or 0.0 <= ratio <= 1.0
    # 基线事实数（R10b 收窄后）：sc02/sc03 各剩 init 立足点 appearance
    #（vow／whereabouts／jade_secret 为无消费者拍状态，不再投影，C7）；
    # 第 2 章跨场重复剔除 1 条（echoes_dropped）。
    assert chapters[2]['baseline_facts'] == 2
    assert chapters[2]['echoes_dropped'] == 1
    assert chapters[2]['carried_locks'] == 0
    assert chapters[2]['duplicate_line_ratio'] == 0.0


def test_packet_metrics_file_is_registered_and_written(tmp_path, monkeypatch):
    """E-6：produces／产物表／路径家族／送达登记同批闭合；真实链 compile 落盘。"""
    from contracts.artifacts import ARTIFACT_PATHS, STR06_ARTIFACT_RC_VERSIONS
    from contracts.invariants import NODES
    from contracts.schema.consumption import (
        STR06_FIELD_DELIVERY, DeliveryMode, DeliveryScope,
    )
    rel = 'stage4/acceptance/packet_metrics.json'
    assert rel in NODES['str06.stage4.compile'].produces
    assert rel in ARTIFACT_PATHS and rel in STR06_ARTIFACT_RC_VERSIONS
    spec = STR06_FIELD_DELIVERY[rel]
    assert spec.delivery_mode is DeliveryMode.AUDIT_ONLY
    assert spec.scope is DeliveryScope.NOT_DELIVERED

    # 真实链（fake transport）：compile 节点写出指标文件
    from contracts.schema.hook import HookConstraints
    from novel.stages.chain import run_workflow
    from tests.str06_faults import looped_full_chain_wires
    from tests.str06_facilities import anchor_stub, json_ask, stub_body_runner, text_ask
    from tests.test_str06_body import _chapters, _make_session
    wires = looped_full_chain_wires()
    repo, session, _transport = _make_session(tmp_path, wires, name='metrics')

    def ask_body(owner, prompt):
        raise AssertionError('指标用例不得走到正文 owner')

    stub_body_runner(monkeypatch)
    run_workflow(repository=repo, chapters=_chapters(), ask=json_ask(session),
                 ask_text=text_ask(session), ask_body=ask_body, budget=2000,
                 through='stage7', book_title='报恩',
                 hook_constraints=HookConstraints(target_chars=10,
                                                  hard_max_chars=360))
    metrics_path = repo.path(rel)
    assert metrics_path.is_file(), 'compile 必须写出 packet_metrics.json'
    import json
    payload = json.loads(metrics_path.read_text(encoding='utf-8'))
    assert payload['chapters'] and payload['renderer_version'] == RENDERER_VERSION
