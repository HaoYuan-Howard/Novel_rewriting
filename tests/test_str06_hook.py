#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_hook —— F6a Hook 契约与免费反例 + 迁移包依赖守卫。

【验证边界】只用 canonical 重建 Context 一致；悬空引用／源 Hook 混入／误载
义务被拒；跨场冷开乱序、第三人称多视角、单拍部分披露；隐藏身份在称谓、
presentation、正文字面三层被挡；Hook 窄改不扩大正文失效；机械通过不升级为
语义已验；迁移包依赖规则对真实模块 AST 执行。真实成文质量归 F6c 人工核读。
"""
from __future__ import annotations

import ast
from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.schema.design import (
    BodyDesign, ChapterAssignment, SceneDelivery, SceneDriver, TargetBeat,
    TargetScene,
)
from contracts.schema.hook import (
    HookDesign, HookPov, HookReceipt,
)
from contracts.schema.source import (
    CharactersArtifactV7, NameStructure,
)
from novel.stages.stage4.design import assemble_body_design
from novel.stages.stage4.hook_context import compile_hook_context
from novel.stages.stage4.hook import assemble_hook_design, publish_graph
from novel.stages.stage5.hook_writer import (
    MAX_REWRITES, build_hook_user_prompt, build_rewrite_user_prompt,
    validate_hook_prose,
)
from novel.stages.stage4.compile import compile_execution_plan
from novel.stages.stage4.grounding import derive_semantic_binding
from novel.store import Str06Repository
from novel.errors import PathRefused
from novel.llm.ledger import CostLedger
from novel.paths import check_rel_family
from tests.str06_facilities import (anchor_stub, make_context, stub_body_runner,
                                    synthetic_ref, v13_beat, v13_chapter)
from tests.str06_expression_inputs import (
    sample_narration, sample_requirements, sample_book_expression, sample_design_expression,
)

H64 = 'a' * 64
SRC = '苏芸娘嫁入陆家，唤夫君为陆大哥，苏姑娘陪嫁，陆兄迎取。'


def _declared_prose(text: str) -> str:
    """rc26（命名前移批次 C）：identity-text 协议退役后成文一律普通文本。

    本助手保留为直通：旧声明行为（呗名标记）不再有语义；疑似旧名残留与提前揭名改由
    `novel/core/name_residue.py` 定位报告（不阻断），机械面只查协议l定界符残片。
    """
    return text


# ───────────────────────── fixture 构造器 ─────────────────────────

def _characters() -> CharactersArtifactV7:
    return CharactersArtifactV7.model_validate({'initial_knowledge': [],
            'characters': {
                'fu_jun': {'identity_bindings': [], 'canonical': '夫君', 'surfaces': [
                    {'surface': '夫君', 'from_chapter': 1, 'until_chapter': None,
                     'source_refs': ['e01_01']},
                    {'surface': '陆大哥', 'from_chapter': 1, 'until_chapter': None,
                     'source_refs': ['e01_01']},
                    {'surface': '陆兄', 'from_chapter': 1, 'until_chapter': None,
                     'source_refs': ['e01_01']}]},
                'yun_niang': {'identity_bindings': [], 'canonical': '苏芸娘', 'surfaces': [
                    {'surface': '苏芸娘', 'from_chapter': 1, 'until_chapter': None,
                     'source_refs': ['e01_01']},
                    {'surface': '芸娘', 'from_chapter': 1, 'until_chapter': None,
                     'source_refs': ['e01_01']},
                    {'surface': '苏姑娘', 'from_chapter': 1, 'until_chapter': None,
                     'source_refs': ['e01_01']}]}},
            'initial_state': [],
            'name_structure': {
                'families': {
                    'fam_lu': {'literal': '陆', 'forms': [
                        {'literal': '陆家', 'form': '{family}家', 'source_refs': ['e01_01']}]},
                    'fam_su': {'literal': '苏', 'forms': []}},
                'people': {
                    'fu_jun': {'family_id': 'fam_lu', 'given': None, 'aliases': [],
                               'surface_forms': {'夫君': None, '陆大哥': '{family}大哥',
                                                 '陆兄': '{family}兄'}},
                    'yun_niang': {'family_id': 'fam_su', 'given': '芸娘', 'aliases': [],
                                  'surface_forms': {'苏芸娘': '{family}{given}',
                                                    '芸娘': '{given}',
                                                    '苏姑娘': '{family}姑娘'}}}}})


def _body_wire() -> dict:
    """stage4-design-v6 形状（R2）：无 ID／章切片／相对权重数组；threads＋entry。"""
    return {**sample_design_expression(('yun_niang',)),
        'protocol_version': 'stage4-design-v6',
        'book_style': '冷峻克制的限知叙事，重日常细节里的异样。',
        'initial_state': [
            {'domain': 'character', 'subjects': ['yun_niang'], 'holder': None,
             'key': 'marital_status', 'op': 'set', 'modality': 'actual', 'value': '待字闺中'}],
        'initial_surfaces': [
            {'char_id': 'yun_niang', 'surfaces': ['苏芸娘']},
            {'char_id': 'fu_jun', 'surfaces': ['夫君']}],
        'scenes': [
            {'state_reads': [
                 {'domain': 'character', 'subjects': ['yun_niang'],
                  'holder': None, 'key': 'marital_status'}],
             'driver': {'focal_actor': 'yun_niang', 'immediate_want': '安度新婚',
                        'opposition': '夫君的怪异', 'turn': '立约'},
             'realization_brief': '重心在拜堂到立约的落差；从婚礼喧闹写起，展开洞房对坐，'
                                  '在旧约出口处收住，衔接下一场夜半异动。',
             'beats': [
                 {'event': '拜堂成亲', 'participants': ['yun_niang', 'fu_jun'],
                  'delivery_hint': '', 'target_delta': [
                      {'domain': 'relation', 'subjects': ['yun_niang', 'fu_jun'], 'holder': None,
                       'key': 'marital_status', 'op': 'set', 'modality': 'actual', 'value': '夫妻', 'scope': 'book'}],
                  'delivery_hint': '', 'surface_introductions': []},
                 {'event': '洞房夜夫君立约', 'participants': ['yun_niang', 'fu_jun'],
                  'delivery_hint': '', 'target_delta': [
                      {'domain': 'knowledge', 'subjects': ['fu_jun'], 'holder': 'yun_niang',
                       'key': 'agreement', 'op': 'set', 'value': '夫君身负报恩之约',
                       'scope': 'book', 'knowledge_mode': 'known'}],
                  'delivery_hint': '',
                  'surface_introductions': [{'char_id': 'yun_niang', 'surfaces': ['芸娘']}]},
                 {'event': '芸娘立誓查清', 'participants': ['yun_niang'],
                  'delivery_hint': '', 'target_delta': [
                      {'domain': 'knowledge', 'subjects': ['yun_niang'], 'holder': 'yun_niang',
                       'key': 'intent', 'op': 'set', 'value': '她决意查清真相',
                       'scope': 'book', 'knowledge_mode': 'known'}],
                  'delivery_hint': '', 'surface_introductions': []}],
             'delivery': {'pov': 'yun_niang', 'voice': '冷峻', 'avoid': []}},
            {'state_reads': [],
             'driver': {'focal_actor': 'yun_niang', 'immediate_want': '查明真相',
                        'opposition': '无凭据', 'turn': '发现玉佩'},
             'realization_brief': '重心在玉佩的发现；从空房冷起，压缩等待，在字面露出的瞬间收束。',
             'beats': [
                 {'event': '夫君夜半离家', 'participants': ['fu_jun'],
                  'delivery_hint': '', 'target_delta': [
                      {'domain': 'character', 'subjects': ['fu_jun'], 'holder': None,
                       'key': 'residence', 'op': 'set', 'modality': 'actual', 'value': '去向不明', 'scope': 'book'}],
                  'delivery_hint': '', 'surface_introductions': []},
                 {'event': '芸娘发现玉佩秘密', 'participants': ['yun_niang'],
                  'delivery_hint': '', 'target_delta': [
                      {'domain': 'knowledge', 'subjects': ['fu_jun'], 'holder': 'yun_niang',
                       'key': 'secret', 'op': 'set', 'value': '玉佩刻着沈字',
                       'scope': 'book', 'knowledge_mode': 'known'}],
                  'delivery_hint': '', 'surface_introductions': []}],
             'delivery': {'pov': 'yun_niang', 'voice': '冷峻', 'avoid': []}}],
        'chapters': [{'purpose': '新婚之夜埋下旧约之疑', 'scenes': 1},
                     {'purpose': '玉佩之谜浮出水面', 'scenes': 1,
                      'entry': {'transition': 'time_skip', 'recap': 'do_not_recap'}}],
        'scene_weights': [1, 1],
    }


def _hook_wire(**over) -> dict:
    """测试用坐标材料：v3 组织与期待显式给出，helper 只换目录引用。"""
    wire = {
        'protocol_version': 'stage4-hook-design-v1',
        'pov': {'person': 'first', 'focalizers': ['yun_niang']},
        'opening_facts': [
            {'ref': 'init', 'atom_ordinal': 0,
             'address': {'domain': 'character', 'subjects': ['yun_niang'], 'holder': None,
                         'key': 'marital_status'},
             'audience': 'world'},
            {'ref': {'scene': 'sc01', 'beat': 'b03'}, 'atom_ordinal': 0,
             'address': {'domain': 'knowledge', 'subjects': ['yun_niang'], 'holder': 'yun_niang',
                         'key': 'intent'},
             'audience': 'reader'}],
        'materials': [
            {'beat_ref': {'scene': 'sc02', 'beat': 'b01'}, 'withheld': [],
             'presentation': '深夜，一个身影翻出后墙',
             'emphasis': 'develop', 'transition': 'start'},
            {'beat_ref': {'scene': 'sc01', 'beat': 'b02'}, 'withheld': [],
             'presentation': '新婚夜，丈夫低声说起一桩旧约',
             'emphasis': 'brief', 'transition': 'flashback'}],
        'concealed': ['fu_jun'],
        'surface_releases': [
            {'char_id': 'yun_niang', 'surfaces': ['苏芸娘', '芸娘']}],
        'forbidden_reveals': [
            {'ref': {'scene': 'sc02', 'beat': 'b02'}, 'atom_ordinal': 0,
             'address': {'domain': 'knowledge', 'subjects': ['fu_jun'], 'holder': 'yun_niang',
                         'key': 'secret'}}],
        'reader_expectation': {
            'kind': 'fact', 'focus': '玉佩与那桩旧约有什么关联？',
            'answer': {'ref': {'scene': 'sc02', 'beat': 'b02'}, 'atom_ordinal': 0,
                       'address': {'domain': 'knowledge', 'subjects': ['fu_jun'],
                                   'holder': 'yun_niang', 'key': 'secret'}}},
        'local_focus': '重心在眼前异样与旧约的反差，冷面叙述，短句，不解释。',
    }
    wire.update(over)
    return wire


def _design_wire_v7() -> dict:
    """stage4-design-v8 形状（CS-3）：real 承载标注替 bind 的落点判断；v5 的
    自由键 knowledge 增量在 v6 无面（知情一律经 real＋Brief 锁派生），fake 链
    以 character 域事实断言承载同一信息。义务 ob01 的接受拍＝末次标注拍
    （sc01#b02），sc01#b01 是支撑位——与旧 bind wire 的 entries/supporting 等价。"""
    wire = {
        'protocol_version': 'stage4-design-v13',
        'book_style': '冷峻克制的限知叙事，重日常细节里的异样。',
        'character_voices': [{'char_id': 'yun_niang', 'voice': '冷峻'}],
        'initial_state': [
            {'domain': 'character', 'subjects': ['yun_niang'], 'holder': None,
             'key': 'marital_status', 'value': '待字闺中'}],
        'initial_surfaces': [
            {'char_id': 'yun_niang', 'surfaces': ['苏芸娘']},
            {'char_id': 'fu_jun', 'surfaces': ['夫君']}],
        'scenes': [
            {'driver': {'focal_actor': 'yun_niang', 'immediate_want': '安度新婚',
                        'opposition': '夫君的怪异', 'turn': '立约'},
             'brief': '重心在拜堂到立约的落差；从婚礼喧闹写起，展开洞房对坐，'
                      '在旧约出口处收住，衔接下一场夜半异动。',
             'reads': [{'domain': 'character', 'subjects': ['yun_niang'],
                        'holder': None, 'key': 'marital_status'}],
             'beats': [
                 {'ev': '拜堂成亲', 'who': ['yun_niang', 'fu_jun'], 'real': ['ob01'],
                  'state': [{'at': 'yun_niang+fu_jun', 'key': 'marital_status',
                             'val': '夫妻'}]},
                 {'ev': '洞房夜夫君立约', 'who': ['yun_niang', 'fu_jun'], 'real': ['ob01'],
                  'release': [{'char_id': 'yun_niang', 'surfaces': ['芸娘']}]},
                 {'ev': '芸娘立誓查清', 'who': ['yun_niang'],
                  'state': [{'at': 'yun_niang', 'key': 'intent', 'val': '她决意查清真相'}]}]},
            {'driver': {'focal_actor': 'yun_niang', 'immediate_want': '查明真相',
                        'opposition': '无凭据', 'turn': '发现玉佩'},
             'brief': '重心在玉佩的发现；从空房冷起，压缩等待，在字面露出的瞬间收束。',
             'reads': [],
             'beats': [
                 {'ev': '夫君夜半离家', 'who': ['fu_jun'],
                  'state': [{'at': 'fu_jun', 'key': 'residence', 'val': '去向不明'}]},
                 {'ev': '芸娘发现玉佩秘密', 'who': ['yun_niang'],
                  'state': [{'at': 'yun_niang', 'key': 'secret', 'val': '玉佩刻着沈字'}]}]},
        ],
        'chapters': [{'purpose': '新婚之夜埋下旧约之疑', 'scenes': 1},
                     {'purpose': '玉佩之谜浮出水面', 'scenes': 1,
                      'entry': {'transition': 'time_skip'}}],
    }
    # rc26：v12 自有自然语言一律普通目标文本——无标讴可声明＋无候选覆盖硬闸。
    return wire


def _hook_wire_v7(**over) -> dict:
    """全链配套的 hook wire：三处地址引用按 v6 canonical 的行域改写
    （knowledge 自由键行不存在——见 `_design_wire_v7` 注释）。"""
    wire = _hook_wire()
    wire['opening_facts'][1] = {
        'ref': {'scene': 'sc01', 'beat': 'b03'}, 'atom_ordinal': 0,
        'address': {'domain': 'character', 'subjects': ['yun_niang'], 'holder': None,
                    'key': 'intent'},
        'audience': 'reader'}
    wire['forbidden_reveals'] = [{
        'ref': {'scene': 'sc02', 'beat': 'b02'}, 'atom_ordinal': 0,
        'address': {'domain': 'character', 'subjects': ['yun_niang'], 'holder': None,
                    'key': 'secret'}}]
    wire['reader_expectation']['answer'] = deepcopy(wire['forbidden_reveals'][0])
    wire.update(over)
    return wire


def _assemble_coordinate_fixture(wire, **kwargs):
    from tests.factories import current_hook_wire
    return assemble_hook_design(current_hook_wire(wire, body=kwargs.get('body')), **kwargs)


def _projection():
    return _characters().name_projection()


def _hook_map():
    """hook 侦件的全部保留 NameMapV6 替身（关注改名的用例自己构造 entries）。"""
    from tests.str06_facilities import name_map_of
    return name_map_of(_characters())


def _green_pair():
    body, errors = assemble_body_design(_body_wire(),
                                        known_char_ids={'fu_jun', 'yun_niang'}, expression_requirements=sample_requirements(), source_narration=sample_narration())
    assert body is not None and not errors, errors
    hook, errors = _assemble_coordinate_fixture(_hook_wire(), body=body,
                                        known_char_ids={'fu_jun', 'yun_niang'},
                                        name_projection=_projection())
    assert hook is not None and not errors, errors
    graph = publish_graph(body, hook)
    context, errors = compile_hook_context(
        graph, _projection(), _characters().name_structure, total_budget=2000, name_map=_hook_map())
    assert context is not None and not errors, errors
    return body, graph, context


# ───────────────────────── Context 重建与投影 ─────────────────────────

def test_context_rebuild_is_byte_identical_after_delete(tmp_path):
    _, graph, _ = _green_pair()
    repo = Str06Repository(tmp_path)
    repo.attach_context(make_context(tmp_path, ledger=CostLedger(path=None)))
    def build():
        context, errors = compile_hook_context(
            graph, _projection(), _characters().name_structure, total_budget=2000, name_map=_hook_map())
        assert context is not None and not errors, errors
        return repo.write('stage4/context/hook/hook.json', context).read_bytes()
    first = build()
    repo.path('stage4/context/hook/hook.json').unlink()
    assert build() == first


def test_pov_comes_from_design_not_body_majority():
    wire = _hook_wire()
    wire['pov'] = {'person': 'first', 'focalizers': ['yun_niang']}
    body, _ = assemble_body_design(_body_wire(), known_char_ids={'fu_jun', 'yun_niang'}, expression_requirements=sample_requirements(), source_narration=sample_narration())
    # 正文两场 pov 都是 yun_niang；这里反向构造：改正文 pov 为 fu_jun 众数，
    # Hook 的 POV 仍取自 HookDesign 本身。
    wire_body = _body_wire()
    for scene in wire_body['scenes']:
        scene['delivery']['pov'] = 'fu_jun'
    wire_body['narration']['focalizers'] = ['fu_jun']
    body2, _ = assemble_body_design(wire_body, known_char_ids={'fu_jun', 'yun_niang'}, expression_requirements=sample_requirements(), source_narration=sample_narration())
    hook, errors = _assemble_coordinate_fixture(wire, body=body2,
                                        known_char_ids={'fu_jun', 'yun_niang'},
                                        name_projection=_projection())
    assert hook is not None and not errors, errors
    graph = publish_graph(body2, hook)
    context, errors = compile_hook_context(
        graph, _projection(), _characters().name_structure, total_budget=2000, name_map=_hook_map())
    assert context is not None and not errors, errors
    assert context.pov.person == 'first'
    assert context.pov.focalizers == ('yun_niang',)
    prompt = build_hook_user_prompt(context)
    # Hook 请求没有源文哨兵与源 id
    assert '〔第' not in prompt and 'e01_01' not in prompt


def test_third_person_multi_focalizer_passes():
    wire = _hook_wire()
    wire['pov'] = {'person': 'third', 'focalizers': ['yun_niang', 'fu_jun']}
    body, _ = assemble_body_design(_body_wire(), known_char_ids={'fu_jun', 'yun_niang'}, expression_requirements=sample_requirements(), source_narration=sample_narration())
    hook, errors = _assemble_coordinate_fixture(wire, body=body,
                                        known_char_ids={'fu_jun', 'yun_niang'},
                                        name_projection=_projection())
    assert hook is not None and not errors, errors


def test_first_person_rejects_multiple_or_narrator():
    with pytest.raises(ValidationError):
        HookPov(person='first', focalizers=('yun_niang', 'fu_jun'))
    with pytest.raises(ValidationError, match='narrator'):
        HookPov(person='first', focalizers=('narrator',))


def test_partial_disclosure_single_beat():
    wire = _hook_wire()
    wire['concealed'] = []
    wire['materials'] = [
        {'beat_ref': {'scene': 'sc01', 'beat': 'b02'}, 'emphasis': 'develop', 'transition': 'start', 'withheld': [
            {'domain': 'knowledge', 'subjects': ['fu_jun'], 'holder': 'yun_niang',
             'key': 'agreement'}], 'presentation': None},
    ]
    wire['opening_facts'] = [wire['opening_facts'][0]]
    body, _ = assemble_body_design(_body_wire(), known_char_ids={'fu_jun', 'yun_niang'}, expression_requirements=sample_requirements(), source_narration=sample_narration())
    hook, errors = _assemble_coordinate_fixture(wire, body=body,
                                        known_char_ids={'fu_jun', 'yun_niang'},
                                        name_projection=_projection())
    assert hook is not None and not errors, errors
    graph = publish_graph(body, hook)
    context, errors = compile_hook_context(
        graph, _projection(), _characters().name_structure, total_budget=2000, name_map=_hook_map())
    assert context is not None and not errors, errors
    material = context.materials[0]
    assert material.event == '洞房夜夫君立约'
    assert material.deltas == ()  # 该拍唯一 delta 被 withheld：单拍部分披露


def _same_beat_forbidden_fixture():
    """同拍有两条事实：只禁旧约，灯盏状态仍可作为 Hook 材料。"""
    body_wire = _body_wire()
    body_wire['scenes'][0]['beats'][1]['target_delta'].append({
        'domain': 'character', 'subjects': ['yun_niang'], 'holder': None,
        'key': 'lamp_status', 'op': 'set', 'modality': 'actual',
        'value': '灯盏已燃尽', 'scope': 'book'})
    body, errors = assemble_body_design(
        body_wire, known_char_ids={'fu_jun', 'yun_niang'},
        expression_requirements=sample_requirements(), source_narration=sample_narration())
    assert body is not None and not errors, errors
    forbidden_address = {
        'domain': 'knowledge', 'subjects': ['fu_jun'], 'holder': 'yun_niang',
        'key': 'agreement'}
    wire = _hook_wire(concealed=[])
    wire['opening_facts'] = [wire['opening_facts'][0]]
    wire['materials'] = [{
        'beat_ref': {'scene': 'sc01', 'beat': 'b02'},
        'emphasis': 'develop', 'transition': 'start',
        'withheld': [], 'presentation': None}]
    wire['forbidden_reveals'] = [{
        'ref': {'scene': 'sc01', 'beat': 'b02'}, 'address': forbidden_address}]
    return body, wire, forbidden_address


@pytest.mark.parametrize('mask', ['withheld', 'presentation'])
def test_same_beat_forbidden_fact_mask_reaches_hook_writer(mask):
    body, wire, address = _same_beat_forbidden_fixture()
    if mask == 'withheld':
        wire['materials'][0]['withheld'] = [address]
    else:
        wire['materials'][0]['presentation'] = '烛火熄了，两人仍在桌边相对而坐。'
    wire_before = deepcopy(wire)
    body_before = body.model_dump(mode='json')
    hook, errors = _assemble_coordinate_fixture(
        wire, body=body, known_char_ids={'fu_jun', 'yun_niang'},
        name_projection=_projection())
    assert hook is not None and not errors, errors
    assert wire == wire_before and body.model_dump(mode='json') == body_before
    if mask == 'withheld':
        assert hook.forbidden_reveals[0].address.address() == hook.materials[0].withheld[0].address()
        assert hook.forbidden_reveals[0].fact_ref == hook.materials[0].withheld[0].fact_ref
    else:
        assert hook.materials[0].presentation == wire['materials'][0]['presentation']
    graph = publish_graph(body, hook)
    # 正式 canonical 读回复验与初答共用守卫，免费重编不能再误拒该遮罩。
    graph = type(graph).model_validate_json(graph.model_dump_json())
    assert graph.hook.opening_facts[0].fact_ref == hook.opening_facts[0].fact_ref
    assert graph.hook.forbidden_reveals[0].fact_ref == hook.forbidden_reveals[0].fact_ref
    assert graph.hook.reader_expectation.answer.fact_ref == hook.reader_expectation.answer.fact_ref
    if mask == 'withheld':
        assert graph.hook.materials[0].withheld[0].fact_ref == hook.materials[0].withheld[0].fact_ref
    context, errors = compile_hook_context(graph, _projection(), _characters().name_structure, total_budget=2000,
                            name_map=_hook_map())
    assert context is not None and not errors, errors
    material = context.materials[0]
    if mask == 'withheld':
        assert material.event == '洞房夜夫君立约' and material.presentation is None
        assert len(material.deltas) == 1 and '灯盏已燃尽' in material.deltas[0]
    else:
        assert material.presentation == wire['materials'][0]['presentation']
        assert material.event is None and material.deltas == ()
    assert len(context.forbidden_reveals) == 1
    assert '夫君身负报恩之约' in context.forbidden_reveals[0]
    prompt = build_hook_user_prompt(context)
    positive, forbidden_tail = prompt.split('【禁止提前兑现】', 1)
    forbidden, organization = forbidden_tail.split('【叙述与组织】', 1)
    assert '夫君身负报恩之约' not in positive + organization
    assert '夫君身负报恩之约' in forbidden
    assert ('灯盏已燃尽' if mask == 'withheld'
            else wire['materials'][0]['presentation']) in positive


@pytest.mark.parametrize('mask', ['none', 'other_address'])
def test_same_beat_forbidden_fact_requires_its_own_mask(mask):
    body, wire, _ = _same_beat_forbidden_fixture()
    if mask == 'other_address':
        wire['materials'][0]['withheld'] = [{
            'domain': 'character', 'subjects': ['yun_niang'], 'holder': None,
            'key': 'lamp_status'}]
    wire_before = deepcopy(wire)
    hook, errors = _assemble_coordinate_fixture(
        wire, body=body, known_char_ids={'fu_jun', 'yun_niang'},
        name_projection=_projection())
    assert hook is None and wire == wire_before
    leaks = [error for error in errors if error.code == 'hook_forbidden_leak']
    assert len(leaks) == 1, errors
    assert leaks[0].context['wire_paths'] == [
        ['forbidden_reveals', 0], ['materials', 0]]


@pytest.mark.parametrize('mask', ['withheld', 'presentation'])
def test_same_beat_forbidden_event_rejects_fact_masks(mask):
    body, wire, address = _same_beat_forbidden_fixture()
    wire['forbidden_reveals'][0]['address'] = None
    if mask == 'withheld':
        wire['materials'][0]['withheld'] = [address]
    else:
        wire['materials'][0]['presentation'] = '烛火熄了，两人仍在桌边相对而坐。'
    hook, errors = _assemble_coordinate_fixture(
        wire, body=body, known_char_ids={'fu_jun', 'yun_niang'},
        name_projection=_projection())
    assert hook is None
    conflicts = [error for error in errors if error.code == 'hook_forbidden_conflict']
    assert len(conflicts) == 1, errors
    assert conflicts[0].context['wire_paths'] == [
        ['forbidden_reveals', 0], ['materials', 0]]


def test_canonical_hook_recheck_rejects_removed_fact_mask():
    body, wire, address = _same_beat_forbidden_fixture()
    wire['materials'][0]['withheld'] = [address]
    hook, errors = _assemble_coordinate_fixture(
        wire, body=body, known_char_ids={'fu_jun', 'yun_niang'},
        name_projection=_projection())
    assert hook is not None and not errors, errors
    # 模拟盘上已可解码 Hook 丢了遮罩，Context 免费重建仍须重新挡住披露冲突。
    hook = hook.model_copy(update={'materials': (
        hook.materials[0].model_copy(update={'withheld': ()}),)})
    context, errors = compile_hook_context(
        publish_graph(body, hook), _projection(), _characters().name_structure, total_budget=2000, name_map=_hook_map())
    assert context is None
    assert any(error.code == 'hook_forbidden_leak' for error in errors), errors


@pytest.mark.parametrize('other_use', ['opening', 'expectation'])
def test_same_beat_fact_mask_does_not_authorize_opening_or_answer(other_use):
    body, wire, address = _same_beat_forbidden_fixture()
    wire['materials'][0]['withheld'] = [address]
    if other_use == 'opening':
        wire['opening_facts'].append({
            **deepcopy(wire['forbidden_reveals'][0]), 'audience': 'reader'})
        expected_code = 'hook_opening_forbidden'
    else:
        wire['reader_expectation']['answer'] = deepcopy(wire['forbidden_reveals'][0])
        expected_code = 'hook_expectation_horizon'
    hook, errors = _assemble_coordinate_fixture(
        wire, body=body, known_char_ids={'fu_jun', 'yun_niang'},
        name_projection=_projection())
    assert hook is None
    assert any(error.code == expected_code for error in errors), errors


# ───────────────────────── 事实渲染保留认知维度（A2） ─────────────────────────

def _rendered_deltas(*, knowledge_mode: str, holder: str):
    """同一拍、同一事实，只改认知模式／持有人：返回 HookContext 渲染出的事实文本。"""
    wire = _body_wire()
    wire['scenes'][0]['beats'][1]['target_delta'][0]['knowledge_mode'] = knowledge_mode
    wire['scenes'][0]['beats'][1]['target_delta'][0]['holder'] = holder
    body, errors = assemble_body_design(wire, known_char_ids={'fu_jun', 'yun_niang'}, expression_requirements=sample_requirements(), source_narration=sample_narration())
    assert body is not None and not errors, errors
    hook_wire = _hook_wire()
    hook_wire['concealed'] = []
    hook_wire['materials'] = [{'beat_ref': {'scene': 'sc01', 'beat': 'b02'}, 'emphasis': 'develop', 'transition': 'start', 'withheld': [], 'presentation': None}]
    hook_wire['opening_facts'] = [hook_wire['opening_facts'][0]]
    hook, errors = _assemble_coordinate_fixture(hook_wire, body=body,
                                        known_char_ids={'fu_jun', 'yun_niang'},
                                        name_projection=_projection())
    assert hook is not None and not errors, errors
    graph = publish_graph(body, hook)
    context, errors = compile_hook_context(
        graph, _projection(), _characters().name_structure, total_budget=2000, name_map=_hook_map())
    assert context is not None and not errors, errors
    return context.materials[0].deltas


def test_fact_rendering_keeps_holder_and_knowledge_mode():
    """知道／怀疑／听说与持有人不同必须渲染后可区分：不得压成同一个事实句。

    R2 起 key slug（debt_agreement）不进阅读面——认知维度由「持有者＋模式措辞」
    承载，值文本原样来自 delta。"""
    known = _rendered_deltas(knowledge_mode='known', holder='yun_niang')
    suspected = _rendered_deltas(knowledge_mode='suspected', holder='fu_jun')
    heard = _rendered_deltas(knowledge_mode='heard', holder='yun_niang')
    assert len(known) == len(suspected) == len(heard) == 1
    assert '芸娘确知' in known[0] and 'debt_agreement' not in known[0]
    assert '怀疑' in suspected[0] and '确知' not in suspected[0]
    assert '听说' in heard[0]
    assert len({known[0], suspected[0], heard[0]}) == 3, '认知维度丢失：三种模式渲染相同'
    # 认知归属（持有者）进阅读面且可区分
    assert '芸娘' in known[0] and '夫君' in suspected[0]


# ───────────────────────── 反例：引用／混入／时序 ─────────────────────────

def _assemble(wire, body=None):
    body = body if body is not None else assemble_body_design(
        _body_wire(), known_char_ids={'fu_jun', 'yun_niang'}, expression_requirements=sample_requirements(), source_narration=sample_narration())[0]
    # rc26：隐藏人物泄露判据的字面宇宙是目标字面表。
    from novel.core.name_materials import target_face_table
    faces, _face_errors = target_face_table(_characters().name_structure, _hook_map())
    return _assemble_coordinate_fixture(wire, body=body,
                                known_char_ids={'fu_jun', 'yun_niang'},
                                name_projection=_projection(), faces=faces)


def test_dangling_refs_rejected():
    wire = _hook_wire()
    wire['materials'][0]['beat_ref'] = {'scene': 'sc09', 'beat': 'b01'}
    _, errors = _assemble(wire)
    assert any('不存在的拍' in e for e in errors)

    wire = _hook_wire()
    wire['opening_facts'][0]['ref'] = {'scene': 'sc09', 'beat': 'b01'}
    _, errors = _assemble(wire)
    assert any('未知事实引用' in e for e in errors)

    wire = _hook_wire()
    wire['concealed'] = ['ghost']
    _, errors = _assemble(wire)
    assert any('未知人物' in e for e in errors)


def test_source_and_obligation_fields_rejected():
    body, _ = assemble_body_design(_body_wire(), known_char_ids={'fu_jun', 'yun_niang'}, expression_requirements=sample_requirements(), source_narration=sample_narration())
    for smuggled in (
            {'source_hook_text': '娘子的嫁妆'},
            {'source_refs': ['e01_01']},
            {'obligations': [{'obligation_id': 'ob1'}]},
            {'state0': ['红烛高照']}):
        wire = _hook_wire(**smuggled)
        hook, errors = _assemble(wire, body)
        assert hook is None, '越权源字段和义务字段不得进入Hook正式产物'
        rejected = [problem for error in errors
                    if getattr(error, 'code', None) == 'hook_wire_root_shape'
                    for problem in error.context.get('shape_errors', ())]
        assert any(problem['error_type'] == 'extra_forbidden'
                   and problem['issue_path'] == list(smuggled) for problem in rejected), smuggled


def test_opening_fact_must_be_established_and_not_material_delta():
    wire = _hook_wire()
    wire['opening_facts'] = [
        {'ref': {'scene': 'sc02', 'beat': 'b02'},
         'address': {'domain': 'knowledge', 'subjects': ['fu_jun'], 'holder': 'yun_niang',
                     'key': 'secret'},
         'audience': 'world'}]
    _, errors = _assemble(wire)
    assert any('早于首个材料' in e for e in errors)   # 时序：world 晚于首个材料
    assert any('禁止揭示' in e for e in errors)        # 落在禁止集内

    wire = _hook_wire()
    wire['opening_facts'].append(
        {'ref': {'scene': 'sc01', 'beat': 'b02'},
         'address': {'domain': 'knowledge', 'subjects': ['fu_jun'], 'holder': 'yun_niang',
                     'key': 'agreement'},
         'audience': 'world'})
    _, errors = _assemble(wire)
    assert any('材料拍' in e for e in errors)          # 不能是材料拍的 delta


def test_reader_fact_beyond_horizon_rejected():
    wire = _hook_wire()
    wire['opening_facts'].append(
        {'ref': {'scene': 'sc02', 'beat': 'b02'},
         'address': {'domain': 'knowledge', 'subjects': ['fu_jun'], 'holder': 'yun_niang',
                     'key': 'secret'},
         'audience': 'reader'})
    wire['forbidden_reveals'] = []
    _, errors = _assemble(wire)
    assert any('材料视界' in e for e in errors)


def test_forbidden_conflicts_with_material():
    wire = _hook_wire()
    wire['concealed'] = []
    wire['materials'].append({'beat_ref': {'scene': 'sc02', 'beat': 'b02'},
                             'emphasis': 'develop', 'transition': 'return',
                             'withheld': [], 'presentation': None})
    _, errors = _assemble(wire)
    assert any(e.code == 'hook_forbidden_leak' for e in errors)


def test_withheld_must_belong_to_beat_delta():
    wire = _hook_wire()
    wire['materials'][0]['withheld'] = [
        {'domain': 'character', 'subjects': ['yun_niang'], 'holder': None,
         'key': 'residence', 'atom_ordinal': 0}]
    _, errors = _assemble(wire)
    assert any('不属于该拍 delta' in e or '未知事实引用' in e for e in errors)


# ───────────────────────── 隐藏身份的三层防线 ─────────────────────────

def test_concealed_identity_three_layers():
    body, _ = assemble_body_design(_body_wire(), known_char_ids={'fu_jun', 'yun_niang'}, expression_requirements=sample_requirements(), source_narration=sample_narration())
    # 第一层：材料涉及隐藏人物必须 presentation
    wire = _hook_wire()
    wire['materials'][0]['presentation'] = None
    _, errors = _assemble(wire, body)
    assert any('必须给出 presentation' in e for e in errors)
    # 第二层：presentation 不得含隐藏人物字面
    wire = _hook_wire()
    wire['materials'][0]['presentation'] = '陆大哥翻出后墙'
    _, errors = _assemble(wire, body)
    assert any('presentation 泄漏隐藏人物' in e for e in errors)
    # 第三层（称谓面）：隐藏人物不得释放称谓
    wire = _hook_wire()
    wire['surface_releases'].append({'char_id': 'fu_jun', 'surfaces': ['陆大哥']})
    _, errors = _assemble(wire, body)
    assert any('隐藏人物' in e for e in errors)
    # 第四层：rc26 普通正文主链——未释放完整称谓进监视行（写手材料硬闸），
    # 正文量的同类出现只形成定位报告（R-7.9，不阻断）；裸姓部件不单独成候选。
    from novel.core.name_residue import name_residues
    _, _, context = _green_pair()
    watched = {literal for row in context.name_watch for literal in row.literals}
    assert {'陆大哥', '陆兄', '苏姑娘'} <= watched
    assert not ({'陆', '苏'} & watched), '裸姓部件不进监视行'
    prose = '苏芸娘望着空房，想起陆大哥。'
    assert validate_hook_prose(prose, context) == []
    residues = name_residues(prose, watch=context.name_watch)
    assert any(row.literal == '陆大哥' for row in residues)


def test_release_must_be_declared_surface():
    wire = _hook_wire()
    wire['concealed'] = []
    wire['surface_releases'].append({'char_id': 'fu_jun', 'surfaces': ['沈公子']})
    _, errors = _assemble(wire)
    assert any('已声明的面' in e for e in errors)


# ───────────────────────── Hook 窄改不扩大正文失效 ─────────────────────────

def test_hook_change_does_not_touch_body_slices():
    body, _, _ = _green_pair()
    # 正文 plan 只依赖 body：同一 body 配两个不同 hook，plan 全等
    hook_a, _ = _assemble(_hook_wire(), body)
    hook_b, _ = _assemble(_hook_wire(local_focus='换一种组织方式：先婚后查，冷开收束。'), body)
    graph_a = publish_graph(body, hook_a)
    graph_b = publish_graph(body, hook_b)
    plan_a, errors = compile_execution_plan(graph_a, _binding_for(body))
    assert errors == [], errors
    plan_b, errors = compile_execution_plan(graph_b, _binding_for(body))
    assert errors == [], errors
    assert plan_a.model_dump(mode='json') == plan_b.model_dump(mode='json')


def _binding_for(body):
    from contracts.schema.grounding import PreserveCheckpoint, SourceObligation, SourceLineage
    from contracts.schema.policy import PreserveKind
    obligation = SourceObligation(
        obligation_id='ob1', kind=PreserveKind.EVENT_CORE, statement='成亲',
        anchors=anchor_stub(),
        lineage=SourceLineage(blueprint_refs=('b01_01',), character_ids=('yun_niang',)),
        must_preserve=False, satisfaction='', requires=())
    checkpoint = PreserveCheckpoint(name_map_sha256='n' * 64,
        checkpoint_id='cp1', policy_id='p', policy_version=1,
        characters_sha256=H64, blueprints_sha256=H64, provider='x', model='y',
        request_sha256=H64, obligations=(obligation,), checkpoint_sha256=H64)
    # 无 real 标注 ⇒ 非必保义务进 unbound（owner=design），义务不删。
    binding, errors = derive_semantic_binding({}, body, checkpoint)
    assert errors == [], errors
    assert [u.obligation_id for u in binding.unbound] == ['ob1']
    return binding


def test_non_material_beat_change_keeps_context():
    _, graph, context = _green_pair()
    # BodyDesign 视图上没有 hook 字段：正文 slice 消费从类型上与 Hook 无关
    assert 'hook' not in BodyDesign.model_fields
    body_dump = graph.model_dump(mode='json')
    body_dump['scenes'][1]['beats'][1]['event'] = '芸娘发现玉佩上另有刻字（改）'
    body_dump.pop('hook')
    body_dump.pop('contract_version')
    body2 = BodyDesign.model_validate(body_dump)
    graph2 = publish_graph(body2, graph.hook)
    context2, errors = compile_hook_context(
        graph2, _projection(), _characters().name_structure, total_budget=2000, name_map=_hook_map())
    assert errors == [], errors
    assert context2.model_dump(mode='json') == context.model_dump(mode='json')


# ───────────────────────── Hook Writer 机械验 ─────────────────────────

def test_hook_length_envelope_derives_from_chapter_budget():
    """rc24 包络派生：锚定书级章预算（R-5.3 同一预算源），不绑定源引子长度。"""
    from novel.stages.stage4.hook_context import hook_length_envelope
    # 实书规模：11 章 8999 预算 → 均值 818 → 327/450/573。
    assert hook_length_envelope(8999, 11) == (327, 450, 573)
    assert hook_length_envelope(2000, 2) == (400, 550, 700)
    # 换书即换包络：预算随书缩放，无全书写死常数。
    assert hook_length_envelope(120000, 300) == (160, 220, 280)
    # 极小预算退化仍严格递增（schema 的 low<high<cap 恒可满足）。
    assert hook_length_envelope(1, 1) == (1, 2, 3)
    with pytest.raises(ValueError, match='正的书级预算'):
        hook_length_envelope(0, 3)
    with pytest.raises(ValueError, match='正的书级预算'):
        hook_length_envelope(100, 0)


def test_hook_request_carries_length_envelope_not_source_length():
    """包络进请求字节（从而进请求身份）；换预算的请求不是同一请求。"""
    _, _, context = _green_pair()
    request = build_hook_user_prompt(context)
    assert '目标 400–550 字、硬上限 700 字' in request
    assert '不参照原文引子长度' in request
    assert '凑字数压缩或注水' in request


def test_hook_length_gate_is_cap_only():
    """rc24 篇幅包络：目标区间不拒稿（rc23 的观察结论），只有硬上限拒收。

    `_green_pair` 的 fixture 预算 2000、章数 5 → 包络 400/550/700；
    550–700 字之间（区间外、上限内）不得因字数报错——low/high 是创作指导，
    不是硬闸。
    """
    _, _, context = _green_pair()
    assert (context.length_low, context.length_high, context.length_cap) == (400, 550, 700)
    assert not validate_hook_prose('夜 \n\t' * 10, context)
    assert not validate_hook_prose('夜' * 650, context), '区间外、上限内不因字数拒稿'
    errors = validate_hook_prose('夜' * 1200, context)
    assert errors == ['[length_cap] 楔子 1200 字超过硬上限 700 字'
                      '（目标区间 400–550，非空白字符）']


def test_hook_writer_wrapper_rewrite_same_context():
    _, _, context = _green_pair()
    wrapped_prose = _declared_prose('# 楔子\n苏芸娘望着空房。')
    ok_prose = _declared_prose('苏芸娘望着空房。')
    prompts = []

    def ask_text(owner, prompt, validate=None):
        prompts.append(prompt)
        return (wrapped_prose, [], f'id-{len(prompts)}') if len(prompts) == 1 \
            else (ok_prose, [], f'id-{len(prompts)}')

    from novel.stages.chain import _write_hook
    prose, attempts, identity = _write_hook(context, ask_text)
    assert prose == ok_prose
    assert len(prompts) == 2
    assert '原因' in prompts[1]  # 重写请求 = 同一 Context + 上一稿机械错误
    assert len(attempts) == 2 and identity == 'id-2'


def test_hook_writer_rewrite_limit_exhausted():
    _, _, context = _green_pair()
    calls = []

    def ask_text(owner, prompt, validate=None):
        calls.append(prompt)
        return ('# 楔子', [], f'id-{len(calls)}')

    from novel.stages.chain import _write_hook
    prose, attempts, _ = _write_hook(context, ask_text)
    assert prose is None
    assert len(calls) == 1 + MAX_REWRITES


def test_hook_writer_rejects_wrappers_and_leaks():
    _, _, context = _green_pair()
    for prose, code in (
            ('```json\\n{}\\n```', 'no_wrapper'),
            ('# 楔子', 'no_wrapper'),
            ('楔子', 'no_wrapper'),
            # rc26：身份协议定界符残片仍拒收；未释放专名只进定位报告（R-7.9）
            ('[[name:yun_niang|苏芸娘]]想起陆大哥。', 'no_wrapper'),
            ('苏芸娘想起[[text|陆大哥]]。', 'no_wrapper')):
        errors = validate_hook_prose(prose, context)
        assert any(code in e for e in errors), (prose, errors)


def test_mechanical_pass_never_claims_semantic_verification():
    _, _, context = _green_pair()
    prose = _declared_prose('苏芸娘望着空房。')
    assert validate_hook_prose(prose, context) == []
    receipt = HookReceipt(
        hook_context_sha256=H64, prose_sha256=H64, request_identity='r1',
        validator_version='v1', status='accepted',
        mechanical_checks=('non_empty', 'encoding'))
    assert receipt.semantic == 'unverified'
    with pytest.raises(ValidationError):
        # semantic 不是自由字段：机械面不能宣称语义已验
        HookReceipt(**{**receipt.model_dump(), 'semantic': 'verified'})


# ───────────────────────── 契约形状补充 ─────────────────────────

def test_duplicate_materials_are_rejected_without_length_policy():
    # canonical 形状直接构造（重复检测是 schema 层的职责；wire→canonical 的
    # 分量解析归 assemble_hook_design）。
    _, graph, _ = _green_pair()
    canonical = graph.hook.model_dump(mode='json')
    canonical['materials'] = [
        {'beat_ref': 'sc01#b02', 'withheld': [], 'presentation': 'p',
         'emphasis': 'develop', 'transition': 'start'},
        {'beat_ref': 'sc01#b02', 'withheld': [], 'presentation': 'q',
         'emphasis': 'brief', 'transition': 'continue'}]
    with pytest.raises(ValidationError, match='重复'):
        HookDesign.model_validate(canonical)


def test_receipt_hash_charset():
    with pytest.raises(ValidationError, match='十六进制'):
        HookReceipt(hook_context_sha256='A' * 64, prose_sha256=H64,
                    request_identity='r', validator_version='v', status='accepted',
                                mechanical_checks=('non_empty',))


# ───────────────────────── 迁移包依赖规则：AST 真实执行 ─────────────────────────

ROOT = Path(__file__).resolve().parent.parent


def _module_path(module: str) -> Path:
    return ROOT / (module.replace('.', '/') + '.py')


def _file_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module)
    return mods


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    for pattern in patterns:
        base = pattern[:-2] if pattern.endswith('.*') else pattern
        if name == base or name.startswith(base + '.'):
            return True
    return False


def test_migration_dependency_rules_executed_on_real_modules():
    from contracts.schema.dependency import STR06_MIGRATION_DEPENDENCY_RULES
    scanned = 0
    for rule in STR06_MIGRATION_DEPENDENCY_RULES:
        path = _module_path(rule.module)
        assert path.exists(), f'{rule.module} 未落盘，规则失去执行对象'
        for name in _file_imports(path):
            assert not _matches(name, rule.forbids), \
                f'{rule.module} import 了禁止面 {name}'
            if name.startswith('novel.'):
                assert _matches(name, rule.allows), \
                    f'{rule.module} import 了未登记允许面的 novel 模块 {name}'
            elif name.startswith('contracts.'):
                assert _matches(name, ('contracts.*',)) or _matches(name, rule.allows), (
                    f'{rule.module} import 了未登记的契约模块 {name}')
        scanned += 1
    assert scanned >= 6


def test_dependency_rule_scan_actually_finds_violations():
    from contracts.schema.dependency import STR06_MIGRATION_DEPENDENCY_RULES
    rule = next(r for r in STR06_MIGRATION_DEPENDENCY_RULES
                if r.module == 'novel.stages.stage4.hook_context')
    assert _matches('novel.llm.paid_requests', rule.forbids)
    assert not _matches('contracts.schema.hook', rule.forbids)
    # 若真实文件违例，上面的真实执行测试会红；这里证明扫描器本身有牙齿
    synthetic = 'import novel.llm.transport\nfrom novel.llm import paid_requests\n'
    tree = ast.parse(synthetic)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module)
    assert all(_matches(m, rule.forbids) for m in mods)


# ───────────────────────── families 准入正反例 ─────────────────────────

def test_new_families_admitted_and_rest_refused():
    assert check_rel_family('stage5/hook/hook.txt')
    assert check_rel_family('stage5/hook/receipt.json')
    assert check_rel_family('stage4/naming/name_map.json')
    assert check_rel_family('stage7/final/rewritten.txt')
    assert check_rel_family('stage4/context/hook/hook.json')
    with pytest.raises(PathRefused):
        check_rel_family('stage7/other/file.json')
    with pytest.raises(PathRefused):
        check_rel_family('stage6/hook/hook.txt')


# ───────────────────────── 工作流（stage 层，fake ask） ─────────────────────────

#: rc26：全链瞧件的目标姓名投影（naming wire：yun_niang→柳婉、fu_jun→顾）。
#: 进入创作链的自然语言（design 自有文本）一律用目标姓名；夫君是纯语义谓
#: （构词 None），不改名。
_TARGET_TEXT = {
    '苏芸娘': '柳婉', '芸娘': '婉',
    '陆大哥': '顾大哥', '陆兄': '顾兄',
    '陆家': '顾家', '苏姑娘': '柳姑娘',
}


def _to_target(text: str) -> str:
    """长名优先单遍替换（与字面扫描同尺）。"""
    import re
    if not text:
        return text
    pattern = re.compile('|'.join(sorted(_TARGET_TEXT, key=len, reverse=True)))
    return pattern.sub(lambda match: _TARGET_TEXT[match.group()], text)


def _target_design_wire() -> dict:
    """全链瞧件的 design wire：自有自然语言投影到目标姓名（rc26）。

    释放声明（initial_surfaces/release）保持源登记面——compile 经目标字面表映射。
    """
    wire = _design_wire_v7()
    for scene in wire['scenes']:
        scene['driver'] = {key: (_to_target(value) if key != 'focal_actor' else value)
                           for key, value in scene['driver'].items()}
        scene['brief'] = _to_target(scene['brief'])
        for beat in scene['beats']:
            beat['ev'] = _to_target(beat['ev'])
            for state in beat.get('state', ()):
                state['val'] = _to_target(state['val'])
    return wire


def _full_chain_wires():
    """B2 五节点观察协议下的全链 fake wires（book/chNN/associate 新形状）。"""
    chars = _characters()
    book = {**sample_book_expression({
            slug: {'identity_bindings': [], 'canonical': entry.canonical,
                   'surfaces': [{'surface': s.surface, 'source_refs': ['e01_01']}
                                for s in entry.surfaces]}
            for slug, entry in chars.characters.items()}),
        'protocol_version': 'stage2-book-v8',
        'characters': {
            slug: {'identity_bindings': [], 'canonical': entry.canonical,
                   'surfaces': [{'surface': s.surface, 'source_refs': ['e01_01']}
                                for s in entry.surfaces]}
            for slug, entry in chars.characters.items()},
        'initial_state': [], 'world_constraints': []}

    def beat(event, refs, **over):
        wire = {'event': event, 'participants': [], 'source_refs': list(refs),
                'scene_boundary': 'new', 'temporal_mode': 'single',
                'effects': [], 'cognition': [], 'loops': []}
        wire.update(over)
        return wire

    from tests.factories import current_hook_wire
    hook_wire = current_hook_wire(_hook_wire_v7())
    naming_wire = {'entries': [
        {'char_ref': 'fu_jun', 'name': {'family': '顾', 'given': None}, 'aliases': {},
         'keep_reason': None},
        {'char_ref': 'yun_niang', 'name': {'family': '柳', 'given': '婉'}, 'aliases': {},
         'keep_reason': None}]}
    return {
        'book': book,
        'ch01': {'protocol_version': 'stage2-chapters-v8', 'beats': [
            beat('苏芸娘嫁入陆家，夫君立下旧约。', ('e01_01',),
                 participants=('yun_niang', 'fu_jun'),
                 effects=({'reader_visible': False, 'target': {'kind': 'relation', 'subject': 'yun_niang',
                                      'object': 'fu_jun'},
                           'key': 'marital_status', 'op': 'set', 'value': '夫妻',
                           'modality': 'actual',
                           'basis': ({'segment': 'e01_01',
                                      'quote': '苏芸娘嫁入陆家'},)},))]},
        'ch02': {'protocol_version': 'stage2-chapters-v8', 'beats': [
            beat('夫君夜半离家，芸娘发现玉佩之秘。', ('e02_01',),
                 participants=('yun_niang', 'fu_jun'))]},
        'names': __import__('tests.str06_facilities', fromlist=['names_v6_wire'])
            .names_v6_wire(chars.name_structure.model_dump(mode='json')),
        'associate': {'protocol_version': 'stage2-associate-v5',
                      'same_proposition': [],
                      'loop_pairing': [], 'cross_chapter_cause': [],
                      # 两章各一场：seam 候选判 distinct（各自独立场景）。
                      'scene_continuation': [
                          {'left': 'ch01.sc1', 'right': 'ch02.sc1',
                           'verdict': 'distinct'}]},
        'cross_cause': {'protocol_version': 'stage2-cross-cause-v1', 'causes': []},
        # 命名前移批（policy 12）：义务文本用目标姓名、无身份文本标记；
        # naming wire 把 yun_niang→柳婉、fu_jun→顾（姓位），夫君是保留称谓。
        'preserve': {'protocol_version': 'stage4-preserve-v8', 'obligations': [
            {'kind': 'event_core',
             'statement': '柳婉嫁入顾家且夫君立下旧约',
             'anchors': ['b01_01.e1'],
             'must_preserve': True,
             'satisfaction': '正文呈现婚约成立且夫君立下旧约', 'requires': []}]},
        'design': _target_design_wire(),
        'hook_design': hook_wire,
        'naming': naming_wire,
    }


def test_through_stage7_full_chain_replay_and_authorization_gate(tmp_path, monkeypatch):
    from novel.stages.chain import PurchaseRequired, run_workflow
    wires = _full_chain_wires()
    # chapters 是**源章**文本（stage1 证据），保持源面；Stage5 正文由写手直接以目标姓名成文（本用例被 stub，无正文调用）。
    chapters = [
        {'chapter_ordinal': 1, 'text': '苏芸娘嫁入陆家，唤夫君为陆大哥，苏姑娘陪嫁，陆兄迎取。'},
        {'chapter_ordinal': 2, 'text': '夫君夜半翻墙而出，苏芸娘发现玉佩刻着沈字。'}]

    def make_ask(wires, counter, repo):
        def ask(owner, prompt, validate, patch=None, **kwargs):
            if owner not in wires:
                raise PurchaseRequired(owner)
            counter.append(owner)
            # 假 ask 也要返回显式采用引用（provenance 的生成事实来源）。
            return deepcopy(wires[owner]), synthetic_ref(owner, prompt)
        return ask

    def make_text(repo):
        def ask_text(owner, prompt, validate=None):
            return (_declared_prose('柳婉望着空房。'), [],
                    synthetic_ref(owner, prompt))
        return ask_text

    def ask_body(owner, prompt):
        raise AssertionError('正文 runner 已 stub，不应调用正文 owner')

    from copy import deepcopy
    repo = Str06Repository(tmp_path / 'run')
    repo.attach_context(make_context(tmp_path / 'run', ledger=CostLedger(path=None)))
    stub_body_runner(monkeypatch)
    counter: list[str] = []
    report = run_workflow(repository=repo, chapters=chapters,
                          ask=make_ask(wires, counter, repo),
                          ask_text=make_text(repo),
                          ask_body=ask_body,
                          budget=2000, through='stage7', book_title='报恩')
    assert report['assembly']['status'] == 'not_ready'
    assert report['assembly']['reason'] == 'body_not_implemented'
    assert report['accepted_body'] is False
    # 状态显式：assembly_not_ready（无 accepted body，归 F5）
    assert repo.read('runtime/workflow_status.json')['status'] == 'assembly_not_ready'
    # stage7/final/ 不落任何产物；改名 Hook 只进 runtime/delivery/
    assert not (tmp_path / 'run/stage7/final').exists()
    # rc26：零改名装配——hook 成文即最终文本，无 hook_renamed 派生。
    assert repo.read_text('stage5/hook/hook.txt') == '柳婉望着空房。'
    assert repo.inspect('stage5/hook/receipt.json')['semantic'] == 'unverified'

    # 重跑：零新调用（wire 全命中），字节一致
    before = {rel: repo.path(rel).read_bytes() for rel in (
        'stage4/design/presentation_graph.json', 'stage4/context/hook/hook.json',
        'stage4/ir/writer_book_ir.json',
        'stage5/hook/hook.txt', 'stage4/naming/name_map.json',
            'stage5/hook/hook.txt', 'stage4/naming/name_map.json')}
    counter.clear()
    run_workflow(repository=repo, chapters=chapters,
                 ask=make_ask(wires, counter, repo), ask_text=make_text(repo),
                 ask_body=ask_body,
                 budget=2000, through='stage7', book_title='报恩')
    after = {rel: repo.path(rel).read_bytes() for rel in before}
    assert before == after
    assert counter  # fake wire 命中与真实 wire-dir 采用语义一致（0 付费调用）

    # 缺 wire 且未授权：停在对应 owner，报告需要授权
    missing = {k: v for k, v in wires.items() if k != 'hook_design'}
    repo2 = Str06Repository(tmp_path / 'run2')
    repo2.attach_context(make_context(tmp_path / 'run2', ledger=CostLedger(path=None)))
    with pytest.raises(PurchaseRequired) as excinfo:
        run_workflow(repository=repo2, chapters=chapters,
                     ask=make_ask(missing, [], repo2), ask_text=make_text(repo2),
                     ask_body=ask_body,
                     budget=2000, through='stage7')
    assert excinfo.value.owner == 'hook_design'
    status = repo2.read('runtime/workflow_status.json')
    assert status['status'] == 'needs_authorization'
    assert status['blocked_owner'] == 'hook_design'


# ──────────── 请求面派生标注与结构失败时的材料预检（2026-10-10 裁决） ────────────

def _superseded_body(*, leaky_agreement: bool = False):
    """init 的 relation marital_status 与 sc01#b01 同地址覆盖（追加初始项）。

    `leaky_agreement` 把 sc01#b02 知情增量的 value 换成含未释放专名的内容
    （非 kp 句柄时 value 即渲染内容），复现 rc23 升版重跑的名字泄漏层。
    """
    wire = _body_wire()
    wire['initial_state'] = [*wire['initial_state'], {
        'domain': 'relation', 'subjects': ['yun_niang', 'fu_jun'], 'holder': None,
        'key': 'marital_status', 'op': 'set', 'modality': 'actual',
        'value': '待字闺中', 'scope': 'book'}]
    if leaky_agreement:
        wire['scenes'][0]['beats'][1]['target_delta'][0]['value'] = '陆大哥与苏芸娘有旧约'
    body, errors = assemble_body_design(
        wire, known_char_ids={'fu_jun', 'yun_niang'},
        expression_requirements=sample_requirements(), source_narration=sample_narration())
    assert body is not None and not errors, errors
    return body


def test_request_catalog_annotates_supersede_writes():
    from novel.stages.stage4.hook_facts import fact_ref_of
    from novel.stages.stage4.hook_wire import annotated_hook_catalog
    body = _superseded_body()
    catalog = annotated_hook_catalog(body)
    init_ref = fact_ref_of('init', ('relation', ('yun_niang', 'fu_jun'), '', 'marital_status'), 1)
    beat_ref = fact_ref_of('sc01#b01', ('relation', ('yun_niang', 'fu_jun'), '', 'marital_status'), 0)
    assert [row['fact_ref'] for row in catalog[init_ref]['later_writes']] == [beat_ref]
    assert catalog[init_ref]['later_writes'][0]['at'] == {'scene': 'sc01', 'beat': 'b01'}
    assert 'later_writes' not in catalog[beat_ref]
    only_once = fact_ref_of('sc02#b02', ('knowledge', ('fu_jun',), 'yun_niang', 'secret'), 0)
    assert 'later_writes' not in catalog[only_once]


def test_request_catalog_annotates_name_exposure_by_person():
    from novel.stages.stage4.hook_facts import fact_ref_of
    from novel.stages.stage4.hook_wire import annotated_hook_catalog
    from novel.core.name_materials import person_name_bearing_targets, target_face_table
    body = _superseded_body(leaky_agreement=True)
    faces = person_name_bearing_targets(
        _characters().name_structure,
        target_face_table(_characters().name_structure, _hook_map())[0])
    catalog = annotated_hook_catalog(body, faces=faces)
    agreement = fact_ref_of('sc01#b02', ('knowledge', ('fu_jun',), 'yun_niang', 'agreement'), 0)
    # 暴露只报本条文本里实际出现的带名目标字面（全部保留替身下目标面＝源面）。
    assert catalog[agreement]['name_exposure'] == {
        'fu_jun': ['陆大哥'],
        'yun_niang': ['苏芸娘', '芸娘']}
    plain = fact_ref_of('sc02#b02', ('knowledge', ('fu_jun',), 'yun_niang', 'secret'), 0)
    assert 'name_exposure' not in catalog[plain]


def test_structural_failure_still_aggregates_material_exposure():
    """rc23 升版重跑 6/6 耗尽的回归反例：stale（结构层）与未释放专名
    （材料层）同份首答并存时，一轮反馈必须同时暴露两层，不逐层付费排雷。"""
    from types import SimpleNamespace

    from tests.factories import current_hook_wire
    from novel.stages.stage4.preflight import hook_candidate_material_errors
    body = _superseded_body(leaky_agreement=True)
    wire = _hook_wire(
        opening_facts=[
            {'ref': 'init',
             'address': {'domain': 'relation', 'subjects': ['yun_niang', 'fu_jun'],
                         'holder': None, 'key': 'marital_status'},
             'audience': 'world'},
            {'ref': {'scene': 'sc01', 'beat': 'b02'},
             'address': {'domain': 'knowledge', 'subjects': ['fu_jun'],
                         'holder': 'yun_niang', 'key': 'agreement'},
             'audience': 'pov'}])
    hook, errors = _assemble_coordinate_fixture(
        wire, body=body, known_char_ids={'fu_jun', 'yun_niang'},
        name_projection=_projection())
    # 结构层：init 旧值在冷开起点 sc02#b01 前已被 sc01#b01 覆盖。
    assert hook is None and any('已变化、解除或失效' in str(e) for e in errors), errors
    binding = SimpleNamespace(obligations=(), fact_bindings=())
    diag = hook_candidate_material_errors(
        current_hook_wire(wire, body=body), body=body, chars=_characters(), binding=binding,
        name_map=_hook_map())
    exposed = [e for e in diag if e.code == 'hook_design_material_exposure']
    assert exposed and exposed[0].location == ('opening_facts', 1), [str(e) for e in diag]
    assert '陆大哥' in str(exposed[0]) and '（材料预检）' in str(exposed[0])
    assert [list(p) for p in exposed[0].context['wire_paths']] == [['opening_facts', 1]]


def test_green_wire_material_preview_reports_no_exposure():
    from types import SimpleNamespace

    from tests.factories import current_hook_wire
    from novel.stages.stage4.preflight import hook_candidate_material_errors
    body = _superseded_body(leaky_agreement=True)
    # 只释放芸娘面、不选含名条目的合法设计：预检零误报。
    wire = _hook_wire(
        opening_facts=[{
            'ref': {'scene': 'sc01', 'beat': 'b03'},
            'address': {'domain': 'knowledge', 'subjects': ['yun_niang'],
                        'holder': 'yun_niang', 'key': 'intent'},
            'audience': 'pov'}])
    hook, errors = _assemble_coordinate_fixture(
        wire, body=body, known_char_ids={'fu_jun', 'yun_niang'},
        name_projection=_projection())
    assert hook is not None and not errors, errors
    binding = SimpleNamespace(obligations=(), fact_bindings=())
    assert hook_candidate_material_errors(
        current_hook_wire(wire, body=body), body=body, chars=_characters(), binding=binding,
        name_map=_hook_map()) == []
