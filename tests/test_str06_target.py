#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_target —— Preserve／Brief／Bind 确定性组件反例（F3）。

【验证边界】assemble/project 的红例绿对照：lineage/引用闭合、requires 成环、
checkpoint 内容 hash 稳定、Brief 去源坐标投影、Bind 分母完整与 M4 承载真实。
真实付费链路的语义质量由 F3 冒烟回答（单书已授权执行）。
"""
from __future__ import annotations

from contracts.schema.grounding import SemanticBinding
from contracts.schema.policy import PreserveKind
from contracts.schema.source import (
    BlueprintsArtifactV13, ChapterBlueprint, CharactersCore,
)
from tests.str06_facilities import v13_beat
from novel.stages.stage4.grounding import (
    assemble_binding, assemble_preserve, project_brief,
)
from novel.stages.stage4.design import assemble_body_design


def _source_pair():
    chars = CharactersCore(
        characters={
            'fu_jun': {'canonical': '夫君',
                       'surfaces': [{'surface': '夫君', 'from_chapter': 1,
                                     'until_chapter': None, 'source_refs': ['e01_01']}]},
            'yun': {'canonical': '云娘',
                    'surfaces': [{'surface': '云娘', 'from_chapter': 1,
                                  'until_chapter': None, 'source_refs': ['e01_01']}]}},
        initial_state=[])
    bp = BlueprintsArtifactV13(
        world_constraints=[], chapters=(
            ChapterBlueprint(chapter=1,
                             beats=(v13_beat('b01_01', '开篇', participants=('fu_jun',)),),
                             scenes=({'scene_id': 'sc01', 'beat_ids': ('b01_01',)},),
                             causal_edges=()),))
    return chars, bp


def _preserve_wire():
    """stage4-preserve-v1 形状（B3 前半）：模型不输出义务编号，requires 用
    1-based 列表序号表达。"""
    return {'protocol_version': 'stage4-preserve-v1', 'obligations': [
        {'kind': 'event_core', 'statement': '借种之约成立',
         'lineage': {'blueprint_refs': ['b01_01'], 'character_ids': ['fu_jun', 'yun']},
         'must_preserve': True, 'satisfaction': '正文呈现约定被提出且被接受',
         'requires': []},
        {'kind': 'reveal', 'statement': '云娘知晓真相',
         'lineage': {'blueprint_refs': ['b01_01'], 'character_ids': ['yun']},
         'must_preserve': True, 'satisfaction': '正文呈现知晓时刻与反应',
         'requires': [1]},
        {'kind': 'identity', 'statement': '夫君身份约束',
         'lineage': {'blueprint_refs': [], 'character_ids': ['fu_jun']},
         'must_preserve': False, 'satisfaction': '', 'requires': []},
    ]}


def _assemble():
    chars, bp = _source_pair()
    cp, errors = assemble_preserve(
        _preserve_wire(), policy_id='preserve-default', policy_version=1,
        characters=chars, blueprint=bp, provider='deepseek', model='flash',
        request_sha256='r' * 64)
    assert cp is not None and not errors, errors
    return chars, bp, cp


# ───────────────────────── Preserve ─────────────────────────

def test_preserve_rules_stay_in_sync_with_preserve_kind():
    """B2：prompt 里的七类 kind 与 `PreserveKind` 闭集同源（不多不少）。"""
    from novel.stages.common import PRESERVE_KIND_DEFS, PRESERVE_RULES
    assert set(PRESERVE_KIND_DEFS) == {kind.value for kind in PreserveKind}
    for kind in PreserveKind:
        assert kind.value in PRESERVE_RULES
    # 保存对象与反事实判据都写进了规则文本
    assert '反事实判据' in PRESERVE_RULES and '布尔' in PRESERVE_RULES
    assert 'satisfaction' in PRESERVE_RULES and 'statement' in PRESERVE_RULES


def test_assemble_preserve_green_and_hash_stable():
    chars, bp, cp = _assemble()
    assert len(cp.obligations) == 3
    # compiler 派发（I-21）：义务编号按列表序生成；requires 序号重写为正式 ID
    assert [o.obligation_id for o in cp.obligations] == ['ob01', 'ob02', 'ob03']
    assert cp.obligations[1].requires == ('ob01',)
    # 同输入重放 → 同 checkpoint hash（paid checkpoint 的可恢复身份）
    cp2, _ = assemble_preserve(
        _preserve_wire(), policy_id='preserve-default', policy_version=1,
        characters=chars, blueprint=bp, provider='deepseek', model='flash',
        request_sha256='r' * 64)
    assert cp2.checkpoint_sha256 == cp.checkpoint_sha256
    # Truth 变化 → hash 变（绑定的 Source 身份有效）
    chars2, _ = _source_pair()
    chars2.characters['fu_jun'].canonical = '别人'
    cp3, _ = assemble_preserve(
        _preserve_wire(), policy_id='preserve-default', policy_version=1,
        characters=chars2, blueprint=bp, provider='deepseek', model='flash',
        request_sha256='r' * 64)
    assert cp3.checkpoint_sha256 != cp.checkpoint_sha256


def test_assemble_preserve_rejects_dangling_and_cycles():
    chars, bp = _source_pair()
    bad = _preserve_wire()
    bad['obligations'][0]['lineage']['blueprint_refs'] = ['b99_99']
    _, errors = assemble_preserve(
        bad, policy_id='p', policy_version=1, characters=chars, blueprint=bp,
        provider='x', model='y', request_sha256='r' * 64)
    assert any('未知 beat' in e for e in errors)

    cyclic = {'protocol_version': 'stage4-preserve-v1', 'obligations': [
        {'kind': 'event_core', 'statement': 'a',
         'lineage': {'blueprint_refs': ['b01_01'], 'character_ids': []},
         'must_preserve': False, 'satisfaction': '', 'requires': [2]},
        {'kind': 'event_core', 'statement': 'b',
         'lineage': {'blueprint_refs': ['b01_01'], 'character_ids': []},
         'must_preserve': False, 'satisfaction': '', 'requires': [1]}]}
    _, errors = assemble_preserve(
        cyclic, policy_id='p', policy_version=1, characters=chars, blueprint=bp,
        provider='x', model='y', request_sha256='r' * 64)
    assert any('成环' in e for e in errors)

    # 编号不是模型的输出（I-21）：带 obligation_id 的旧形状拒收
    stale = _preserve_wire()
    stale['obligations'][0]['obligation_id'] = 'ob01'
    _, errors = assemble_preserve(
        stale, policy_id='p', policy_version=1, characters=chars, blueprint=bp,
        provider='x', model='y', request_sha256='r' * 64)
    assert any('未知字段' in e for e in errors)

    # requires 序号越界 / 自指精确拒
    oob = _preserve_wire()
    oob['obligations'][1]['requires'] = [9]
    _, errors = assemble_preserve(
        oob, policy_id='p', policy_version=1, characters=chars, blueprint=bp,
        provider='x', model='y', request_sha256='r' * 64)
    assert any('越界' in e for e in errors)
    self_ref = _preserve_wire()
    self_ref['obligations'][1]['requires'] = [2]
    _, errors = assemble_preserve(
        self_ref, policy_id='p', policy_version=1, characters=chars, blueprint=bp,
        provider='x', model='y', request_sha256='r' * 64)
    assert any('自指' in e for e in errors)

    # 协议版本漏带/错带即拒（I-13 无隐式迁移）
    wrong = _preserve_wire()
    wrong['protocol_version'] = 'stage4-preserve-v9'
    _, errors = assemble_preserve(
        wrong, policy_id='p', policy_version=1, characters=chars, blueprint=bp,
        provider='x', model='y', request_sha256='r' * 64)
    assert any('protocol_version' in e for e in errors)


# ───────────────────────── Brief 投影 ─────────────────────────

def test_project_brief_source_blind_projection():
    chars, bp, cp = _assemble()
    brief, identity = project_brief(cp, chars)
    # 只有 event_core 进骨架；reveal/identity 义务不进 cores
    assert len(brief.requirements) == len(cp.obligations)
    assert {c.satisfaction for c in brief.requirements} == {o.satisfaction for o in cp.obligations}
    assert next(c for c in brief.requirements if c.kind is PreserveKind.EVENT_CORE).participants == ('fu_jun', 'yun')
    # Brief 不携带源 beat/章坐标（参与者是 char_id、因果是义务 id）
    import json
    dumped = json.dumps(brief.model_dump(), ensure_ascii=False)
    assert 'b01_01' not in dumped and 'chapter' not in dumped
    # IdentityProjection 给 char_id 与称谓
    assert {i.char_id for i in identity} == {'fu_jun', 'yun'}
    assert '夫君' in identity[0].identity_statement


def test_identity_stable_constraints_come_only_from_the_fact_layer():
    """2B：身份投影的 stable_constraints 只取事实层（initial_state 的 stable 人物事实）。

    v13 起源侧有 cognition（命题＋持有者＋模式）与 concealed 效应；「谁以为什么」
    与隐藏事实都不得进入稳定身份约束——认知归属 effects/fact 层管，身份约束只承载
    叙述确立的稳定事实。CharactersCore 不含 cognition 字段，这里钉住投影不越层。
    """
    from novel.stages.stage2 import ChapterObservation
    chars, bp, cp = _assemble()
    # v13 的章观察形状确实承载 cognition/concealed（B5：旧联合抽取 ChapterCompiler
    # 已删，改按观察协议直接构造同一形状）；其文本若泄漏进身份约束，下面两条
    # 断言会红。
    observation = ChapterObservation.model_validate({
        'protocol_version': 'stage2-observation-v1', 'beats': [{
            'event': '试探', 'participants': ['fu_jun', 'yun'],
            'source_refs': ['e01_01'], 'scene_boundary': 'new',
            'temporal_mode': 'single',
            'effects': [{'target': {'kind': 'character', 'char_id': 'fu_jun'},
                         'key': 'scene_location', 'op': 'set', 'value': '床底',
                         'scope': 'scene',
                         'disclosure': {'visibility': 'concealed',
                                        'known_by': ['fu_jun']},
                         'proposition': {'ref': 'p_hide',
                                         'statement': '夫君藏身床底'}}],
            'cognition': [{'proposition': {'ref': 'p_believe',
                                           'statement': '芸娘以为夫君已离开'},
                           'holder': 'yun', 'mode': 'believes'}],
            'loops': []}]})
    assert observation.beats[0].cognition
    assert observation.beats[0].effects[0].disclosure.visibility == 'concealed'
    _brief, identity = project_brief(cp, chars)
    dumped = ' '.join(' '.join(i.stable_constraints) for i in identity)
    assert '床底' not in dumped and '藏身' not in dumped
    assert '以为' not in dumped and '离开' not in dumped
    # 事实层的 stable 初始事实照常进入（这里 initial_state 为空 ⇒ 约束为空）
    assert all(i.stable_constraints == () for i in identity)


# ───────────────────────── Bind ─────────────────────────

BPV = 'stage4-bind-v1'


def test_assemble_binding_full_denominator():
    chars, bp, cp = _assemble()
    scenes = {'sc01': {'b01'}}
    wire = {'protocol_version': BPV, 'entries': [
        {'obligation_id': 'ob01', 'scene': 'sc01', 'beat': 'b01'},
        {'obligation_id': 'ob02', 'scene': 'sc01', 'beat': 'b01'},
    ], 'unbound': [
        {'obligation_id': 'ob03', 'owner': 'design', 'reason': '身份约束随人物携带'}]}
    binding, errors = assemble_binding(wire, cp, scenes)
    assert binding is not None and not errors, errors
    assert isinstance(binding, SemanticBinding)
    # 复合引用由 compiler 组成（I-21）：wire 只给 {scene, beat} 分量
    assert binding.entries[0].beat_ref == 'sc01#b01'
    assert binding.entries[0].scene_id == 'sc01'

    # 红：义务消失（半份 Binding）
    dropped = {'protocol_version': BPV, 'entries': wire['entries'][:1],
               'unbound': wire['unbound']}
    _, errors = assemble_binding(dropped, cp, scenes)
    assert any('消失' in e for e in errors)

    # 红：拍分量指向不存在的拍（M4 面；闭集外精确拒，不猜笔误）
    ghost = {'protocol_version': BPV, 'entries': [
        {'obligation_id': 'ob01', 'scene': 'sc01', 'beat': 'b99'},
        {'obligation_id': 'ob02', 'scene': 'sc01', 'beat': 'b01'}],
        'unbound': wire['unbound']}
    _, errors = assemble_binding(ghost, cp, scenes)
    assert any('真实拍' in e for e in errors)

    # 红：未知场景
    stray = {'protocol_version': BPV, 'entries': [
        {'obligation_id': 'ob01', 'scene': 'sc99', 'beat': 'b01'}],
        'unbound': [{'obligation_id': 'ob02', 'owner': 'design', 'reason': 'x'},
                    {'obligation_id': 'ob03', 'owner': 'design', 'reason': 'y'}]}
    _, errors = assemble_binding(stray, cp, scenes)
    assert any('未知场景' in e for e in errors)

    # 红：unbound 混入 checkpoint 外义务
    invented = {'protocol_version': BPV, 'entries': wire['entries'],
                'unbound': wire['unbound'] + [
                    {'obligation_id': 'obX', 'owner': 'design', 'reason': 'x'}]}
    _, errors = assemble_binding(invented, cp, scenes)
    assert any('checkpoint 外' in e for e in errors)

    # 红：模型写复合引用（旧形状）即拒——引用由 compiler 组成
    stale = {'protocol_version': BPV, 'entries': [
        {'obligation_id': 'ob01', 'scene_id': 'sc01', 'beat_ref': 'sc01#b01'}],
        'unbound': wire['unbound']}
    _, errors = assemble_binding(stale, cp, scenes)
    assert any('未知字段' in e for e in errors)

    # 红：协议版本错带
    wrong = dict(wire, protocol_version='stage4-bind-v9')
    _, errors = assemble_binding(wrong, cp, scenes)
    assert any('protocol_version' in e for e in errors)


# ───────────────────────── Design 组装 ─────────────────────────

def _design_wire():
    """stage4-design-v2 形状（R2）：模型不输出任何 ID／归章引用／归一权重。"""
    return {
        'protocol_version': 'stage4-design-v2',
        'book_style': '冷峻克制的第三人称叙事，重对话张力。',
        'initial_state': [],
        'scenes': [
            {'state_reads': [],
             'driver': {'focal_actor': 'yun', 'immediate_want': '保全婚姻',
                        'opposition': '家族压力', 'turn': '约定'},
             'realization_brief': '重心在约定成立的承诺瞬间；从家族施压的晚宴写起，'
                                  '展开两人独处的对峙，在立约处收住，衔接下一场的兑现。',
             'beats': [
                 {'event': '约定成立', 'participants': ['yun', 'fu_jun'],
                  'dramatic_intent': '立誓', 'target_delta': [
                      {'domain': 'relation', 'subjects': ['yun', 'fu_jun'], 'op': 'set', 'scope': 'book', 'key': 'marital_status', 'value': '夫妻'}],
                  'delivery_hint': ''}],
             'delivery': {'pov': 'yun', 'voice': '冷峻', 'avoid': []}}],
        'chapters': [{'purpose': '婚姻从名分走向盟约', 'scenes': 1}],
        'scene_weights': [2],
    }


def test_assemble_design_green_and_refusals():
    body, errors = assemble_body_design(
        _design_wire(), known_char_ids={'yun', 'fu_jun'})
    assert body is not None and not errors, errors
    # compiler 派发（I-21）：场景/拍 ID、归章引用、权重归一都在 canonical 落地
    assert body.scenes[0].scene_id == 'sc01'
    assert body.scenes[0].beats[0].beat_id == 'b01'
    assert body.scenes[0].beats[0].target_delta[0].domain == 'relation'
    assert body.chapters[0].chapter_ordinal == 1
    assert body.chapters[0].scene_ids == ('sc01',)
    assert body.chapter_weights == {'sc01': 1.0}
    assert body.book_style.startswith('冷峻克制')
    # RC4：导演说明与章节目的作为 canonical 内容被组装保留
    assert body.scenes[0].realization_brief.startswith('重心在')
    assert body.chapters[0].purpose == '婚姻从名分走向盟约'

    bad = _design_wire()
    bad['scenes'][0]['beats'][0]['participants'].append('ghost')
    _, errors = assemble_body_design(bad, known_char_ids={'yun', 'fu_jun'})
    assert any('未知 char_id' in e for e in errors)

    bad = _design_wire()
    bad['scenes'][0]['beats'][0]['target_delta'][0]['domain'] = 'plot'
    _, errors = assemble_body_design(bad, known_char_ids={'yun', 'fu_jun'})
    assert errors

    # 章切片的场数与场景总数不一致（每章非空、全覆盖由 compiler 核对）
    bad = _design_wire()
    bad['chapters'][0]['scenes'] = 2
    _, errors = assemble_body_design(bad, known_char_ids={'yun', 'fu_jun'})
    assert any('章切片总数' in e for e in errors)

    # source-blind：混入 source_ref 即拒（wire 白名单 + schema 层双保险）
    bad = _design_wire()
    bad['scenes'][0]['source_ref'] = 'b01_01'
    _, errors = assemble_body_design(bad, known_char_ids={'yun', 'fu_jun'})
    assert errors

    # 协议版本漏带/错带即拒（I-13 无隐式迁移）
    bad = _design_wire()
    bad['protocol_version'] = 'stage4-design-v9'
    _, errors = assemble_body_design(bad, known_char_ids={'yun', 'fu_jun'})
    assert any('protocol_version' in e for e in errors)

    # 模型写可派生字段即拒（I-21：ID 由 compiler 生成）
    bad = _design_wire()
    bad['scenes'][0]['scene_id'] = 's01'
    _, errors = assemble_body_design(bad, known_char_ids={'yun', 'fu_jun'})
    assert any('未知字段' in e for e in errors)
    bad = _design_wire()
    bad['scenes'][0]['beats'][0]['beat_id'] = 'bt1'
    _, errors = assemble_body_design(bad, known_char_ids={'yun', 'fu_jun'})
    assert any('未知字段' in e for e in errors)

    # 权重是相对重点数组，不是归一 dict（I-21：归一由 compiler 完成）
    bad = _design_wire()
    bad['scene_weights'] = {'sc01': 1.0}
    _, errors = assemble_body_design(bad, known_char_ids={'yun', 'fu_jun'})
    assert any('scene_weights' in e for e in errors)
    bad = _design_wire()
    bad['scene_weights'] = [0]
    _, errors = assemble_body_design(bad, known_char_ids={'yun', 'fu_jun'})
    assert any('正数数组' in e for e in errors)


def test_design_pov_shape_normalization_and_refusal():
    """B6-A 实证：pov 三种失形（描述句→数组）的机械面。

    单元素数组无损折叠（再组合 canonical 前缀归一）；多元素数组不猜「主要
    视角是谁」——逐场 typed 拒，修订轮可据此修复，不落 pydantic 报错墙。
    """
    wire = _design_wire()
    wire['scenes'][0]['delivery']['pov'] = ['yun']
    body, errors = assemble_body_design(wire, known_char_ids={'yun', 'fu_jun'})
    assert body is not None and not errors, errors
    assert body.scenes[0].delivery.pov == 'yun'

    # 单元素数组先折叠、再走 canonical 前缀归一（F5e 面与新折叠的组合）
    wire = _design_wire()
    wire['scenes'][0]['delivery']['pov'] = ['云娘第三人称限知']
    body, errors = assemble_body_design(
        wire, known_char_ids={'yun', 'fu_jun'},
        char_canonicals={'云娘': 'yun', '夫君': 'fu_jun'})
    assert body is not None and not errors, errors
    assert body.scenes[0].delivery.pov == 'yun'

    # 多元素数组：选谁是语义判断，compiler 不猜（I-21）
    wire = _design_wire()
    wire['scenes'][0]['delivery']['pov'] = ['yun', 'fu_jun']
    body, errors = assemble_body_design(wire, known_char_ids={'yun', 'fu_jun'})
    assert body is None
    assert any('delivery.pov 必须是单个字符串' in e for e in errors), errors

    # 空数组／非字符串元素同面拒收
    for bad_pov in ([], ['']):
        wire = _design_wire()
        wire['scenes'][0]['delivery']['pov'] = bad_pov
        body, errors = assemble_body_design(
            wire, known_char_ids={'yun', 'fu_jun'})
        assert body is None
        assert any('delivery.pov 必须是单个字符串' in e for e in errors)


def test_design_rules_pin_pov_shape():
    """B6-A 实证钉：DESIGN_RULES 必须写明 pov 的目标形状（单字符串闭集）。

    canary 三轮失形（描述句→描述句→char_id 数组）的根因是 wire 形状节只给了
    键名没给值形——修订轮在三种形状间振荡。此钉防形状约束再次从 prompt 里
    静默消失（协议字面量不动，不涉语义冻结闸）。
    """
    from novel.stages.common import DESIGN_RULES
    assert 'pov 是**单个字符串**' in DESIGN_RULES
    assert '一场只有一个视角' in DESIGN_RULES
    assert '不写数组' in DESIGN_RULES
    assert '"narrator"' in DESIGN_RULES


def test_design_surface_release_must_be_registered():
    """称谓闭合（A3）：放行面只能取 Characters 登记过的 surface。

    Design 放行未登记字面时替换表找不到对应新名（F7e 评估实证）——生产端尽早
    失败；`registered_surfaces` 缺省不查（source-blind 单测面）。
    """
    registered = {'yun': {'云娘', '云姑娘'}, 'fu_jun': {'夫君'}}
    wire = _design_wire()
    wire['initial_surfaces'] = [{'char_id': 'yun', 'surfaces': ['云姑娘']}]
    body, errors = assemble_body_design(
        wire, known_char_ids={'yun', 'fu_jun'}, registered_surfaces=registered)
    assert body is not None and not errors, errors

    bad = _design_wire()
    bad['initial_surfaces'] = [{'char_id': 'yun', 'surfaces': ['云娘子']}]
    _, errors = assemble_body_design(
        bad, known_char_ids={'yun', 'fu_jun'}, registered_surfaces=registered)
    assert any('未登记面' in e for e in errors)

    bad = _design_wire()
    bad['scenes'][0]['beats'][0]['surface_introductions'] = [
        {'char_id': 'fu_jun', 'surfaces': ['陆大哥']}]
    _, errors = assemble_body_design(
        bad, known_char_ids={'yun', 'fu_jun'}, registered_surfaces=registered)
    assert any('未登记面' in e and '陆大哥' in e for e in errors)
