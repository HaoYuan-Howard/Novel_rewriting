#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_target —— Preserve／Brief／Bind 确定性组件反例（F3）。

【验证边界】assemble/project 的红例绿对照：lineage/引用闭合、requires 成环、
checkpoint 内容 hash 稳定、Brief 去源坐标投影、Bind 分母完整与 M4 承载真实。
真实付费链路的语义质量由 F3 冒烟回答（单书已授权执行）。
"""
from __future__ import annotations

from contracts.schema.str06.grounding import SemanticBinding
from contracts.schema.str06.policy import PreserveKind
from contracts.schema.str06.source import (
    BlueprintsArtifactV12, ChapterBlueprint, CharactersCore,
)
from novel.stages.str06.grounding import (
    assemble_binding, assemble_preserve, project_brief,
)
from novel.stages.str06.design_compile import assemble_body_design


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
    bp = BlueprintsArtifactV12(
        world_constraints=[], chapters=(
            ChapterBlueprint(chapter=1, beats=(
                {'beat_id': 'b01_01', 'event': '开篇', 'participants': ['fu_jun'],
                 'source_refs': ['e01_01'], 'effects': [], 'reveals': []},), causal_edges=()),))
    return chars, bp


def _preserve_wire():
    return {'obligations': [
        {'obligation_id': 'ob01', 'kind': 'event_core', 'statement': '借种之约成立',
         'lineage': {'blueprint_refs': ['b01_01'], 'character_ids': ['fu_jun', 'yun']},
         'must_preserve': True, 'satisfaction': '正文呈现约定被提出且被接受',
         'requires': []},
        {'obligation_id': 'ob02', 'kind': 'reveal', 'statement': '云娘知晓真相',
         'lineage': {'blueprint_refs': ['b01_01'], 'character_ids': ['yun']},
         'must_preserve': True, 'satisfaction': '正文呈现知晓时刻与反应',
         'requires': ['ob01']},
        {'obligation_id': 'ob03', 'kind': 'identity', 'statement': '夫君身份约束',
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

def test_assemble_preserve_green_and_hash_stable():
    chars, bp, cp = _assemble()
    assert len(cp.obligations) == 3
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

    cyclic = {'obligations': [
        {'obligation_id': 'obA', 'kind': 'event_core', 'statement': 'a',
         'lineage': {'blueprint_refs': ['b01_01'], 'character_ids': []},
         'must_preserve': False, 'satisfaction': '', 'requires': ['obB']},
        {'obligation_id': 'obB', 'kind': 'event_core', 'statement': 'b',
         'lineage': {'blueprint_refs': ['b01_01'], 'character_ids': []},
         'must_preserve': False, 'satisfaction': '', 'requires': ['obA']}]}
    _, errors = assemble_preserve(
        cyclic, policy_id='p', policy_version=1, characters=chars, blueprint=bp,
        provider='x', model='y', request_sha256='r' * 64)
    assert any('成环' in e for e in errors)


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


# ───────────────────────── Bind ─────────────────────────

def test_assemble_binding_full_denominator():
    chars, bp, cp = _assemble()
    scenes = {'s01': {'bt1'}}
    wire = {'entries': [
        {'obligation_id': 'ob01', 'scene_id': 's01', 'beat_ref': 's01#bt1'},
        {'obligation_id': 'ob02', 'scene_id': 's01', 'beat_ref': 's01#bt1'},
    ], 'unbound': [
        {'obligation_id': 'ob03', 'owner': 'design', 'reason': '身份约束随人物携带'}]}
    binding, errors = assemble_binding(wire, cp, scenes)
    assert binding is not None and not errors, errors
    assert isinstance(binding, SemanticBinding)

    # 红：义务消失（半份 Binding）
    dropped = {'entries': wire['entries'][:1], 'unbound': wire['unbound']}
    _, errors = assemble_binding(dropped, cp, scenes)
    assert any('消失' in e for e in errors)

    # 红：beat_ref 指向不存在的拍（M4 面）
    ghost = {'entries': [
        {'obligation_id': 'ob01', 'scene_id': 's01', 'beat_ref': 's01#ghost'},
        {'obligation_id': 'ob02', 'scene_id': 's01', 'beat_ref': 's01#bt1'}],
        'unbound': wire['unbound']}
    _, errors = assemble_binding(ghost, cp, scenes)
    assert any('真实拍' in e for e in errors)

    # 红：unbound 混入 checkpoint 外义务
    invented = {'entries': wire['entries'],
                'unbound': wire['unbound'] + [
                    {'obligation_id': 'obX', 'owner': 'design', 'reason': 'x'}]}
    _, errors = assemble_binding(invented, cp, scenes)
    assert any('checkpoint 外' in e for e in errors)


# ───────────────────────── Design 组装 ─────────────────────────

def _design_wire():
    return {
        'initial_state': [],
        'scenes': [
            {'scene_id': 's01', 'state_reads': [],
             'driver': {'focal_actor': 'yun', 'immediate_want': '保全婚姻',
                        'opposition': '家族压力', 'turn': '约定'},
             'realization_brief': '重心在约定成立的承诺瞬间；从家族施压的晚宴写起，'
                                  '展开两人独处的对峙，在立约处收住，衔接下一场的兑现。',
             'beats': [
                 {'beat_id': 'bt1', 'event': '约定成立', 'participants': ['yun', 'fu_jun'],
                  'dramatic_intent': '立誓', 'target_delta': [
                      {'domain': 'relation', 'subjects': ['yun', 'fu_jun'], 'op': 'set', 'scope': 'book', 'key': 'marital_status', 'value': '夫妻'}],
                  'delivery_hint': ''}],
             'delivery': {'pov': 'yun', 'voice': '冷峻', 'avoid': []}}],
        'chapters': [{'chapter_ordinal': 1, 'purpose': '婚姻从名分走向盟约', 'scene_ids': ['s01']}],
        'chapter_weights': {'s01': 1.0},
    }


def test_assemble_design_green_and_refusals():
    body, errors = assemble_body_design(
        _design_wire(), known_char_ids={'yun', 'fu_jun'})
    assert body is not None and not errors, errors
    assert body.scenes[0].beats[0].target_delta[0].domain == 'relation'
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

    bad = _design_wire()
    bad['chapters'][0]['scene_ids'] = ['sX']
    _, errors = assemble_body_design(bad, known_char_ids={'yun', 'fu_jun'})
    assert errors

    # source-blind：混入 source_ref 即拒（schema 层）
    bad = _design_wire()
    bad['scenes'][0]['source_ref'] = 'b01_01'
    _, errors = assemble_body_design(bad, known_char_ids={'yun', 'fu_jun'})
    assert errors
