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
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.schema.design import (
    BodyDesign, ChapterAssignment, SceneDelivery, SceneDriver, TargetBeat,
    TargetScene,
)
from contracts.schema.hook import (
    HookConstraints, HookDesign, HookPov, HookReceipt,
)
from contracts.schema.source import (
    CharactersArtifactV4, NameStructure,
)
from novel.stages.stage4.design import assemble_body_design
from novel.stages.stage5.hook_context import compile_hook_context
from novel.stages.stage5.hook import assemble_hook_design, publish_graph
from novel.stages.stage5.hook_writer import (
    MAX_REWRITES, build_hook_user_prompt, build_rewrite_user_prompt,
    validate_hook_prose,
)
from novel.stages.stage4.compile import compile_execution_plan
from novel.stages.stage4.grounding import assemble_binding
from novel.store import Str06Repository
from novel.errors import PathRefused
from novel.llm.ledger import CostLedger
from novel.paths import check_rel_family
from tests.str06_facilities import make_context

H64 = 'a' * 64
SRC = '苏芸娘嫁入陆家，唤夫君为陆大哥，苏姑娘陪嫁，陆兄迎娶。'


# ───────────────────────── fixture 构造器 ─────────────────────────

def _characters() -> CharactersArtifactV4:
    return CharactersArtifactV4.model_validate({
            'characters': {
                'fu_jun': {'canonical': '夫君', 'surfaces': [
                    {'surface': '夫君', 'from_chapter': 1, 'until_chapter': None,
                     'source_refs': ['e01_01']},
                    {'surface': '陆大哥', 'from_chapter': 1, 'until_chapter': None,
                     'source_refs': ['e01_01']},
                    {'surface': '陆兄', 'from_chapter': 1, 'until_chapter': None,
                     'source_refs': ['e01_01']}]},
                'yun_niang': {'canonical': '苏芸娘', 'surfaces': [
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
    return {
        'initial_state': [
            {'domain': 'character', 'subjects': ['yun_niang'], 'holder': None,
             'key': 'marital_status', 'op': 'set', 'value': '待字闺中', 'scope': 'book'}],
        'initial_surfaces': [
            {'char_id': 'yun_niang', 'surfaces': ['苏芸娘']},
            {'char_id': 'fu_jun', 'surfaces': ['夫君']}],
        'scenes': [
            {'scene_id': 's01', 'state_reads': [],
             'driver': {'focal_actor': 'yun_niang', 'immediate_want': '安度新婚',
                        'opposition': '夫君的怪异', 'turn': '立约'},
             'realization_brief': '重心在拜堂到立约的落差；从婚礼喧闹写起，展开洞房对坐，'
                                  '在旧约出口处收住，衔接下一场夜半异动。',
             'beats': [
                 {'beat_id': 'bt1', 'event': '拜堂成亲', 'participants': ['yun_niang', 'fu_jun'],
                  'dramatic_intent': '成礼', 'target_delta': [
                      {'domain': 'relation', 'subjects': ['yun_niang', 'fu_jun'], 'holder': None,
                       'key': 'marital_status', 'op': 'set', 'value': '夫妻', 'scope': 'book'}],
                  'delivery_hint': '', 'surface_introductions': []},
                 {'beat_id': 'bt2', 'event': '洞房夜夫君立约', 'participants': ['yun_niang', 'fu_jun'],
                  'dramatic_intent': '埋线', 'target_delta': [
                      {'domain': 'knowledge', 'subjects': ['fu_jun'], 'holder': 'yun_niang',
                       'key': 'debt_agreement', 'op': 'set', 'value': '夫君身负报恩之约',
                       'scope': 'book', 'knowledge_mode': 'known'}],
                  'delivery_hint': '',
                  'surface_introductions': [{'char_id': 'yun_niang', 'surfaces': ['芸娘']}]},
                 {'beat_id': 'bt3', 'event': '芸娘立誓查清', 'participants': ['yun_niang'],
                  'dramatic_intent': '立意', 'target_delta': [
                      {'domain': 'knowledge', 'subjects': ['yun_niang'], 'holder': 'yun_niang',
                       'key': 'vow', 'op': 'set', 'value': '她决意查清真相',
                       'scope': 'book', 'knowledge_mode': 'known'}],
                  'delivery_hint': '', 'surface_introductions': []}],
             'delivery': {'pov': 'yun_niang', 'voice': '冷峻', 'avoid': []}},
            {'scene_id': 's02', 'state_reads': [],
             'driver': {'focal_actor': 'yun_niang', 'immediate_want': '查明真相',
                        'opposition': '无凭据', 'turn': '发现玉佩'},
             'realization_brief': '重心在玉佩的发现；从空房冷起，压缩等待，在字面露出的瞬间收束。',
             'beats': [
                 {'beat_id': 'bt1', 'event': '夫君夜半离家', 'participants': ['fu_jun'],
                  'dramatic_intent': '悬念', 'target_delta': [
                      {'domain': 'character', 'subjects': ['fu_jun'], 'holder': None,
                       'key': 'whereabouts', 'op': 'set', 'value': '去向不明', 'scope': 'book'}],
                  'delivery_hint': '', 'surface_introductions': []},
                 {'beat_id': 'bt2', 'event': '芸娘发现玉佩秘密', 'participants': ['yun_niang'],
                  'dramatic_intent': '揭示', 'target_delta': [
                      {'domain': 'knowledge', 'subjects': ['fu_jun'], 'holder': 'yun_niang',
                       'key': 'jade_secret', 'op': 'set', 'value': '玉佩刻着沈字',
                       'scope': 'book', 'knowledge_mode': 'known'}],
                  'delivery_hint': '', 'surface_introductions': []}],
             'delivery': {'pov': 'yun_niang', 'voice': '冷峻', 'avoid': []}}],
        'chapters': [{'chapter_ordinal': 1, 'purpose': '新婚之夜埋下旧约之疑', 'scene_ids': ['s01']},
                     {'chapter_ordinal': 2, 'purpose': '玉佩之谜浮出水面', 'scene_ids': ['s02']}],
        'chapter_weights': {'s01': 0.5, 's02': 0.5},
    }


def _hook_wire(**over) -> dict:
    wire = {
        'pov': {'person': 'first', 'focalizers': ['yun_niang']},
        'opening_facts': [
            {'ref': 'init',
             'address': {'domain': 'character', 'subjects': ['yun_niang'], 'holder': None,
                         'key': 'marital_status'},
             'audience': 'world'},
            {'ref': 's01#bt3',
             'address': {'domain': 'knowledge', 'subjects': ['yun_niang'], 'holder': 'yun_niang',
                         'key': 'vow'},
             'audience': 'reader'}],
        'materials': [
            {'beat_ref': 's02#bt1', 'withheld': [],
             'presentation': '深夜，一个身影翻出后墙'},
            {'beat_ref': 's01#bt2', 'withheld': [],
             'presentation': '新婚夜，丈夫低声说起一桩旧约'}],
        'concealed': ['fu_jun'],
        'surface_releases': [
            {'char_id': 'yun_niang', 'surfaces': ['苏芸娘', '芸娘']}],
        'forbidden_reveals': [
            {'ref': 's02#bt2',
             'address': {'domain': 'knowledge', 'subjects': ['fu_jun'], 'holder': 'yun_niang',
                         'key': 'jade_secret'}}],
        'design_brief': '从深夜冷开倒叙入新婚夜旧约，信息逐层升级后在新婚夜收束，玉佩之谜不露。',
        'delivery_brief': '第一人称冷面叙述，短句，不解释。',
    }
    wire.update(over)
    return wire


def _projection():
    return _characters().name_projection()


def _green_pair():
    body, errors = assemble_body_design(_body_wire(),
                                        known_char_ids={'fu_jun', 'yun_niang'})
    assert body is not None and not errors, errors
    hook, errors = assemble_hook_design(_hook_wire(), body=body,
                                        known_char_ids={'fu_jun', 'yun_niang'},
                                        name_projection=_projection())
    assert hook is not None and not errors, errors
    graph = publish_graph(body, hook)
    context, errors = compile_hook_context(
        graph, _projection(), HookConstraints(target_chars=280, hard_max_chars=360))
    assert context is not None and not errors, errors
    return body, graph, context


# ───────────────────────── Context 重建与投影 ─────────────────────────

def test_context_rebuild_is_byte_identical_after_delete(tmp_path):
    _, graph, _ = _green_pair()
    repo = Str06Repository(tmp_path)
    repo.attach_context(make_context(tmp_path, ledger=CostLedger(path=None)))
    def build():
        context, errors = compile_hook_context(
            graph, _projection(), HookConstraints(target_chars=280, hard_max_chars=360))
        assert context is not None and not errors, errors
        return repo.write('stage4/context/hook/hook.json', context).read_bytes()
    first = build()
    repo.path('stage4/context/hook/hook.json').unlink()
    assert build() == first


def test_pov_comes_from_design_not_body_majority():
    wire = _hook_wire()
    wire['pov'] = {'person': 'first', 'focalizers': ['yun_niang']}
    body, _ = assemble_body_design(_body_wire(), known_char_ids={'fu_jun', 'yun_niang'})
    # 正文两场 pov 都是 yun_niang；这里反向构造：改正文 pov 为 fu_jun 众数，
    # Hook 的 POV 仍取自 HookDesign 本身。
    wire_body = _body_wire()
    for scene in wire_body['scenes']:
        scene['delivery']['pov'] = 'fu_jun'
    body2, _ = assemble_body_design(wire_body, known_char_ids={'fu_jun', 'yun_niang'})
    hook, errors = assemble_hook_design(wire, body=body2,
                                        known_char_ids={'fu_jun', 'yun_niang'},
                                        name_projection=_projection())
    assert hook is not None and not errors, errors
    graph = publish_graph(body2, hook)
    context, errors = compile_hook_context(
        graph, _projection(), HookConstraints(target_chars=280, hard_max_chars=360))
    assert context is not None and not errors, errors
    assert context.pov.person == 'first'
    assert context.pov.focalizers == ('yun_niang',)
    prompt = build_hook_user_prompt(context)
    # Hook 请求没有源文哨兵与源 id
    assert '〔第' not in prompt and 'e01_01' not in prompt


def test_third_person_multi_focalizer_passes():
    wire = _hook_wire()
    wire['pov'] = {'person': 'third', 'focalizers': ['yun_niang', 'fu_jun']}
    body, _ = assemble_body_design(_body_wire(), known_char_ids={'fu_jun', 'yun_niang'})
    hook, errors = assemble_hook_design(wire, body=body,
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
        {'beat_ref': 's01#bt2', 'withheld': [
            {'domain': 'knowledge', 'subjects': ['fu_jun'], 'holder': 'yun_niang',
             'key': 'debt_agreement'}], 'presentation': None},
    ]
    wire['opening_facts'] = [wire['opening_facts'][0]]
    body, _ = assemble_body_design(_body_wire(), known_char_ids={'fu_jun', 'yun_niang'})
    hook, errors = assemble_hook_design(wire, body=body,
                                        known_char_ids={'fu_jun', 'yun_niang'},
                                        name_projection=_projection())
    assert hook is not None and not errors, errors
    graph = publish_graph(body, hook)
    context, errors = compile_hook_context(
        graph, _projection(), HookConstraints(target_chars=280, hard_max_chars=360))
    assert context is not None and not errors, errors
    material = context.materials[0]
    assert material.event == '洞房夜夫君立约'
    assert material.deltas == ()  # 该拍唯一 delta 被 withheld：单拍部分披露


# ───────────────────────── 反例：引用／混入／时序 ─────────────────────────

def _assemble(wire, body=None):
    body = body if body is not None else assemble_body_design(
        _body_wire(), known_char_ids={'fu_jun', 'yun_niang'})[0]
    return assemble_hook_design(wire, body=body,
                                known_char_ids={'fu_jun', 'yun_niang'},
                                name_projection=_projection())


def test_dangling_refs_rejected():
    wire = _hook_wire()
    wire['materials'][0]['beat_ref'] = 's09#bt1'
    _, errors = _assemble(wire)
    assert any('不存在的拍' in e for e in errors)

    wire = _hook_wire()
    wire['opening_facts'][0]['ref'] = 's09#bt1'
    _, errors = _assemble(wire)
    assert any('不存在的拍' in e or '未建立' in e for e in errors)

    wire = _hook_wire()
    wire['concealed'] = ['ghost']
    _, errors = _assemble(wire)
    assert any('未知人物' in e for e in errors)


def test_source_and_obligation_fields_rejected():
    body, _ = assemble_body_design(_body_wire(), known_char_ids={'fu_jun', 'yun_niang'})
    for smuggled in (
            {'source_hook_text': '娘子的嫁妆'},
            {'source_refs': ['e01_01']},
            {'obligations': [{'obligation_id': 'ob1'}]},
            {'state0': ['红烛高照']}):
        wire = _hook_wire(**smuggled)
        _, errors = _assemble(wire, body)
        assert any('未知字段' in e for e in errors), smuggled


def test_opening_fact_must_be_established_and_not_material_delta():
    wire = _hook_wire()
    wire['opening_facts'] = [
        {'ref': 's02#bt2',
         'address': {'domain': 'knowledge', 'subjects': ['fu_jun'], 'holder': 'yun_niang',
                     'key': 'jade_secret'},
         'audience': 'world'}]
    _, errors = _assemble(wire)
    assert any('早于首个材料' in e for e in errors)   # 时序：world 晚于首个材料
    assert any('禁止揭示' in e for e in errors)        # 落在禁止集内

    wire = _hook_wire()
    wire['opening_facts'].append(
        {'ref': 's01#bt2',
         'address': {'domain': 'knowledge', 'subjects': ['fu_jun'], 'holder': 'yun_niang',
                     'key': 'debt_agreement'},
         'audience': 'world'})
    _, errors = _assemble(wire)
    assert any('材料拍' in e for e in errors)          # 不能是材料拍的 delta


def test_reader_fact_beyond_horizon_rejected():
    wire = _hook_wire()
    wire['opening_facts'].append(
        {'ref': 's02#bt2',
         'address': {'domain': 'knowledge', 'subjects': ['fu_jun'], 'holder': 'yun_niang',
                     'key': 'jade_secret'},
         'audience': 'reader'})
    wire['forbidden_reveals'] = []
    _, errors = _assemble(wire)
    assert any('材料视界' in e for e in errors)


def test_forbidden_conflicts_with_material():
    wire = _hook_wire()
    wire['concealed'] = []
    wire['materials'].append({'beat_ref': 's02#bt2', 'withheld': [], 'presentation': None})
    _, errors = _assemble(wire)
    assert any('既是材料又禁止揭示' in e for e in errors)


def test_withheld_must_belong_to_beat_delta():
    wire = _hook_wire()
    wire['materials'][0]['withheld'] = [
        {'domain': 'character', 'subjects': ['yun_niang'], 'holder': None,
         'key': 'whereabouts'}]
    _, errors = _assemble(wire)
    assert any('不属于该拍 delta' in e for e in errors)


# ───────────────────────── 隐藏身份的三层防线 ─────────────────────────

def test_concealed_identity_three_layers():
    body, _ = assemble_body_design(_body_wire(), known_char_ids={'fu_jun', 'yun_niang'})
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
    # 第四层（正文字面）：withheld_literals 含全部未释放专名，validator 挡成文
    _, _, context = _green_pair()
    assert {'陆', '陆大哥', '陆兄', '苏', '苏姑娘'} <= set(context.withheld_literals)
    prose = '苏芸娘望着空房，想起陆大哥。'
    errors = validate_hook_prose(prose, context)
    assert any('withheld_literals' in e and '陆大哥' in e for e in errors)


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
    hook_b, _ = _assemble(_hook_wire(design_brief='换一种组织方式：先婚后查，冷开收束。'), body)
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
        lineage=SourceLineage(blueprint_refs=('b01_01',), character_ids=('yun_niang',)),
        must_preserve=False, satisfaction='', requires=())
    checkpoint = PreserveCheckpoint(
        checkpoint_id='cp1', policy_id='p', policy_version=1,
        characters_sha256=H64, blueprints_sha256=H64, provider='x', model='y',
        request_sha256=H64, obligations=(obligation,), checkpoint_sha256=H64)
    binding, errors = assemble_binding(
        {'entries': [], 'unbound': [{'obligation_id': 'ob1', 'owner': 'design',
                                     'reason': '随人物携带'}]}, checkpoint,
        {s.scene_id: {b.beat_id for b in s.beats} for s in body.scenes})
    assert errors == [], errors
    return binding


def test_non_material_beat_change_keeps_context():
    _, graph, context = _green_pair()
    # BodyDesign 视图上没有 hook 字段：正文 slice 消费从类型上与 Hook 无关
    assert 'hook' not in BodyDesign.model_fields
    body_dump = graph.model_dump(mode='json')
    body_dump['scenes'][1]['beats'][1]['event'] = '芸娘发现玉佩上另有刻字（改）'
    body_dump.pop('hook')
    body2 = BodyDesign.model_validate(body_dump)
    graph2 = publish_graph(body2, graph.hook)
    context2, errors = compile_hook_context(
        graph2, _projection(), HookConstraints(target_chars=280, hard_max_chars=360))
    assert errors == [], errors
    assert context2.model_dump(mode='json') == context.model_dump(mode='json')


# ───────────────────────── Hook Writer 机械验 ─────────────────────────

def test_hook_writer_length_rewrite_same_context():
    _, _, context = _green_pair()
    long_prose = '夜' * (context.hard_max_chars + 1)
    ok_prose = '苏芸娘望着空房。'
    prompts = []

    def ask_text(owner, prompt):
        prompts.append(prompt)
        return (long_prose, [], f'id-{len(prompts)}') if len(prompts) == 1 \
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

    def ask_text(owner, prompt):
        calls.append(prompt)
        return ('夜' * (context.hard_max_chars + 1), [], f'id-{len(calls)}')

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
            ('苏芸娘想起陆大哥。', 'withheld_literals'),
            ('苏芸娘看着陆家大门。', 'withheld_literals')):
        errors = validate_hook_prose(prose, context)
        assert any(code in e for e in errors), (prose, errors)


def test_mechanical_pass_never_claims_semantic_verification():
    _, _, context = _green_pair()
    prose = '苏芸娘望着空房。'
    assert validate_hook_prose(prose, context) == []
    receipt = HookReceipt(
        hook_context_sha256=H64, prose_sha256=H64, request_identity='r1',
        validator_version='v1', status='accepted',
        mechanical_checks=('non_empty', 'length'))
    assert receipt.semantic == 'unverified'
    with pytest.raises(ValidationError):
        # semantic 不是自由字段：机械面不能宣称语义已验
        HookReceipt(**{**receipt.model_dump(), 'semantic': 'verified'})


# ───────────────────────── 契约形状补充 ─────────────────────────

def test_duplicate_materials_and_constraints():
    with pytest.raises(ValidationError, match='重复'):
        HookDesign.model_validate({
            **_hook_wire(),
            'materials': [
                {'beat_ref': 's01#bt2', 'withheld': [], 'presentation': 'p'},
                {'beat_ref': 's01#bt2', 'withheld': [], 'presentation': 'q'}]})
    HookConstraints(target_chars=280, hard_max_chars=360)
    with pytest.raises(ValidationError):
        HookConstraints(target_chars=400, hard_max_chars=360)


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
                if r.module == 'novel.stages.stage5.hook_context')
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
    assert check_rel_family('stage7/naming/name_map.json')
    assert check_rel_family('stage7/final/rewritten.txt')
    assert check_rel_family('stage4/context/hook/hook.json')
    with pytest.raises(PathRefused):
        check_rel_family('stage7/other/file.json')
    with pytest.raises(PathRefused):
        check_rel_family('stage6/hook/hook.txt')


# ───────────────────────── 工作流（stage 层，fake ask） ─────────────────────────

def _full_chain_wires():
    chars = _characters()
    book = {**chars.core_view().model_dump(mode='json'), 'world_constraints': []}
    hook_wire = _hook_wire()
    naming_wire = {'entries': [
        {'char_ref': 'fu_jun', 'name': {'family': '顾', 'given': None}, 'aliases': {},
         'keep_reason': None},
        {'char_ref': 'yun_niang', 'name': {'family': '柳', 'given': '婉'}, 'aliases': {},
         'keep_reason': None}]}
    return {
        'book': book,
        'ch01': {'beats': [
            {'beat_id': 'b01_01', 'event': '苏芸娘嫁入陆家，夫君立下旧约。',
             'participants': ['yun_niang', 'fu_jun'], 'source_refs': ['e01_01'],
             'effects': [], 'reveals': []}], 'causal_edges': []},
        'ch02': {'beats': [
            {'beat_id': 'b02_01', 'event': '夫君夜半离家，芸娘发现玉佩之秘。',
             'participants': ['yun_niang', 'fu_jun'], 'source_refs': ['e02_01'],
             'effects': [], 'reveals': []}], 'causal_edges': []},
        'names': chars.name_structure.model_dump(mode='json'),
        'preserve': {'obligations': [
            {'obligation_id': 'ob01', 'kind': 'event_core',
             'statement': '苏芸娘嫁入陆家且夫君立下旧约',
             'lineage': {'blueprint_refs': ['b01_01'],
                         'character_ids': ['yun_niang', 'fu_jun']},
             'must_preserve': True,
             'satisfaction': '正文呈现婚约成立且夫君立下旧约', 'requires': []}]},
        'design': _body_wire(),
        'bind': {'entries': [
            {'obligation_id': 'ob01', 'scene_id': 's01', 'beat_ref': 's01#bt2',
             'supporting_beat_refs': ['s01#bt1']}], 'unbound': []},
        'hook_design': hook_wire,
        'naming': naming_wire,
    }


def test_through_stage7_full_chain_replay_and_authorization_gate(tmp_path):
    from novel.stages.chain import PurchaseRequired, run_workflow
    wires = _full_chain_wires()
    chapters = [
        {'chapter_ordinal': 1, 'text': '苏芸娘嫁入陆家，唤夫君为陆大哥，苏姑娘陪嫁，陆兄迎娶。'},
        {'chapter_ordinal': 2, 'text': '夫君夜半翻墙而出，苏芸娘发现玉佩刻着沈字。'}]

    def make_ask(wires, counter, repo):
        def ask(owner, prompt, validate):
            if owner not in wires:
                raise PurchaseRequired(owner)
            counter.append(owner)
            # 假 ask 也要履行付费边界的采用登记（provenance 的生成事实）。
            repo.context.note_adoption(owner=owner, model='fake',
                                       prompts={'user': prompt})
            return deepcopy(wires[owner])
        return ask

    def make_text(repo):
        def ask_text(owner, prompt):
            repo.context.note_adoption(owner=owner, model='fake',
                                       prompts={'user': prompt})
            return '苏芸娘望着空房。', [], 'wire-adoption'
        return ask_text

    def ask_body(owner, prompt):
        raise AssertionError('body_chapter_limit=0 不应调用正文 owner')

    from copy import deepcopy
    repo = Str06Repository(tmp_path / 'run')
    repo.attach_context(make_context(tmp_path / 'run', ledger=CostLedger(path=None)))
    counter: list[str] = []
    report = run_workflow(repository=repo, chapters=chapters,
                          ask=make_ask(wires, counter, repo),
                          ask_text=make_text(repo),
                          ask_body=ask_body,
                          budget=2000, through='stage7', book_title='报恩',
                          hook_constraints=HookConstraints(target_chars=10,
                                                           hard_max_chars=360),
                          body_chapter_limit=0)
    assert report['assembly']['status'] == 'not_ready'
    assert report['assembly']['reason'] == 'body_not_implemented'
    assert report['accepted_body'] is False
    # 状态显式：assembly_not_ready（无 accepted body，归 F5）
    assert repo.read('runtime/workflow_status.json')['status'] == 'assembly_not_ready'
    # stage7/final/ 不落任何产物；改名 Hook 只进 runtime/delivery/
    assert not (tmp_path / 'run/stage7/final').exists()
    assert repo.read_text('runtime/delivery/hook_renamed.txt') == '柳婉望着空房。'
    assert repo.read_text('stage5/hook/hook.txt') == '苏芸娘望着空房。'
    assert repo.inspect('stage5/hook/receipt.json')['semantic'] == 'unverified'

    # 重跑：零新调用（wire 全命中），字节一致
    before = {rel: repo.path(rel).read_bytes() for rel in (
        'stage4/design/presentation_graph.json', 'stage4/context/hook/hook.json',
        'stage4/ir/writer_book_ir.json',
        'stage5/hook/hook.txt', 'stage7/naming/name_map.json',
        'runtime/delivery/hook_renamed.txt')}
    counter.clear()
    run_workflow(repository=repo, chapters=chapters,
                 ask=make_ask(wires, counter, repo), ask_text=make_text(repo),
                 ask_body=ask_body,
                 budget=2000, through='stage7', book_title='报恩',
                 hook_constraints=HookConstraints(target_chars=10, hard_max_chars=360),
                 body_chapter_limit=0)
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
