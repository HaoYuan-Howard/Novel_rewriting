#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_chapter_slim —— 章观察瘦 wire（CS-2 第一步，2026-10-03；
RC-27/28 升 v5，2026-10-04）。

覆盖 `contracts/schema/observation.py` 的瘦模型、
`novel/stages/stage2/slim.py::expand_chapter_wire／slim_chapter_wire` 的
确定性派生、`compile_chapter_observation`／`chapter_patch_locate` 的字面量
路由（判定与定位同一把尺，条目路径用 state／know 词汇），以及
slim → expand → 引文定位 还原 canonical 的回归（模本地句柄重命名）。语义
判据不因瘦身放宽：basis 逐字证据（wire 只给引文，段由 Resolver 定位）、
闭集闸、D2 引文归一全部照旧（反例见各条）。
"""
from __future__ import annotations

import copy

from contracts.schema.observation import (
    STAGE2_CHAPTERS_PROTOCOL, STAGE2_CHAPTERS_WIRE_PROTOCOL,
)
from novel.stages.stage2.chapters import (
    chapter_patch_locate, compile_chapter_observation,
)
from novel.stages.stage2.slim import expand_chapter_wire, slim_chapter_wire

PV_WIRE = STAGE2_CHAPTERS_WIRE_PROTOCOL
PV_CANON = STAGE2_CHAPTERS_PROTOCOL

#: 合成证据闭集：同章连续段，文本含半角标点（D2 引文归一的反例材料）。
EVIDENCE = {
    'e01_01': {'chapter': 1, 'text': '新婚夜,夫君坦白：幼时落水,伤了根本,不能生,也不能行房。'},
    'e01_02': {'chapter': 1, 'text': '夫君说求娶是为报恩。'},
    'e01_03': {'chapter': 1, 'text': '芸娘落泪。'},
    'e01_04': {'chapter': 1, 'text': '夫君提出向表弟借种,一家三口过日子。'},
}
SLUGS = ('lu_fujun', 'yun_niang', 'pei_shuyuan')


def _slim_wire(**overrides):
    beat = {
        'ev': '新婚夜，夫君向芸娘坦白不能生育，并求娶报恩。',
        'who': ['lu_fujun', 'yun_niang'],
        'src': 'e01_01..e01_02',
        'new_scene': True,
        'state': [{
            'at': 'lu_fujun', 'key': 'body_condition',
            'val': '幼时落水伤了根本，不能生育，不能行房事',
            'basis': ['幼时落水,伤了根本,不能生,也不能行房。'],
        }],
        'know': [{
            'who': 'yun_niang', 'how': 'learns',
            'what': '夫君幼时落水伤了根本，不能生育，也不能行房事',
            'basis': ['幼时落水,伤了根本,不能生,也不能行房。'],
        }],
    }
    beat.update(overrides)
    return {'protocol_version': PV_WIRE, 'beats': [beat]}


# ── expand：默认值、地址串、区间 ────────────────────────────────────────────

def test_expand_defaults_addresses_and_range():
    wire = _slim_wire()
    wire['beats'][0]['state'].append({
        'at': 'lu_fujun+pei_shuyuan', 'key': 'kinship', 'val': 'cousins',
        'basis': ['新婚夜,夫君坦白'],
    })
    wire['beats'][0]['state'].append({
        'at': 'world', 'key': 'social_norm', 'val': 'women_cannot_stand_alone',
        'basis': ['夫君说求娶是为报恩。'],
    })
    wire['beats'][0]['know'].append({
        'who': 'reader', 'how': 'learns', 'what': '夫君求娶芸娘是为报恩',
        'basis': ['夫君说求娶是为报恩。'],
    })
    rc12, issues = expand_chapter_wire(wire, chapter=1)
    assert issues == []
    beat = rc12['beats'][0]
    # 默认值全部回填成 rc12 形状（拍序即列表序、无拍号字段）。
    assert beat['scene_boundary'] == 'new'
    assert beat['temporal_mode'] == 'single'
    assert beat['participants'] == ['lu_fujun', 'yun_niang']
    assert beat['source_refs'] == ['e01_01', 'e01_02']      # 区间已枚举
    assert rc12['protocol_version'] == PV_CANON
    char_eff, rel_eff, world_eff = beat['effects']
    assert char_eff['target'] == {'kind': 'character', 'char_id': 'lu_fujun',
                                  'subject': None, 'object': None}
    assert rel_eff['target'] == {'kind': 'relation', 'char_id': None,
                                 'subject': 'lu_fujun', 'object': 'pei_shuyuan'}
    assert world_eff['target']['kind'] == 'world'
    for eff in (char_eff, rel_eff, world_eff):
        assert eff['op'] == 'set'
        assert eff['modality'] == 'actual'
        assert eff['scope'] == 'book'
        assert eff['disclosure'] == {'visibility': 'on_page', 'known_by': []}
        assert eff['proposition'] is None


def test_expand_defaults_on_omitted_exception_flags():
    wire = _slim_wire()
    del wire['beats'][0]['new_scene']
    wire['beats'][0]['iterative'] = True
    wire['beats'][0]['src'] = ['e01_01', 'e01_04']            # 不连续给数组
    rc12, issues = expand_chapter_wire(wire, chapter=1)
    assert issues == []
    beat = rc12['beats'][0]
    assert beat['scene_boundary'] == 'continue'
    assert beat['temporal_mode'] == 'iterative'
    assert beat['source_refs'] == ['e01_01', 'e01_04']


def test_expand_registered_scope_derives_from_table():
    """登记键（scene_location）的作用域由表固定，模型不写、写错也被覆盖。"""
    wire = _slim_wire()
    wire['beats'][0]['state'][0] = {
        'at': 'yun_niang', 'key': 'scene_location', 'val': '婚房',
        'basis': ['新婚夜,夫君坦白'],
    }
    rc12, issues = expand_chapter_wire(wire, chapter=1)
    assert issues == []
    assert rc12['beats'][0]['effects'][0]['scope'] == 'scene'


def test_expand_concealed_and_prop_text_resolution():
    """concealed 例外面与 prop 引用按文本归并（跨拍：know 先建、state 后引）。"""
    wire = _slim_wire()
    wire['beats'][0]['know'].append({
        'who': 'lu_fujun', 'how': 'asserts', 'what': '借种计划仍在推进',
        'basis': ['一家三口过日子。'],
    })
    second = {
        'ev': '深夜，夫君仍按计划行事。',
        'who': ['lu_fujun'],
        'src': 'e01_04',
        'state': [{
            'at': 'lu_fujun', 'key': 'plan', 'val': '借种计划仍在推进',
            'hidden': {'by': ['lu_fujun'], 'what': '借种计划仍在推进'},
            'basis': ['一家三口过日子。'],
        }, {
            'at': 'world', 'key': 'narration_fact', 'val': '表弟确已到府',
            'prop': '表弟确已到府',
            'basis': ['一家三口过日子。'],
        }],
        'know': [{
            'who': 'reader', 'how': 'learns', 'what': '借种计划仍在推进',
            'basis': ['一家三口过日子。'],
        }],
    }
    wire['beats'].append(second)
    rc12, issues = expand_chapter_wire(wire, chapter=1)
    assert issues == []
    first_beat, second_beat = rc12['beats']
    established = first_beat['cognition'][1]['proposition']     # asserts 首现建立
    assert established['statement'] == '借种计划仍在推进'
    hidden = second_beat['effects'][0]
    assert hidden['disclosure'] == {'visibility': 'concealed', 'known_by': ['lu_fujun']}
    assert hidden['proposition'] == {'ref': established['ref'], 'statement': None}
    # know 重引（learn 同一命题）也不带 statement——「文本只建立一次」由形状保持。
    assert second_beat['cognition'][0]['proposition'] == {
        'ref': established['ref'], 'statement': None}
    # prop（on_page 证实引用）在无首现时按建立处理。
    assert second_beat['effects'][1]['proposition']['statement'] == '表弟确已到府'


def test_expand_prop_handles_norm_merge_and_fork():
    """know 重引归并走 norm()（标点宽度不 fork）；改写一字即另一条命题。"""
    wire = _slim_wire()
    text = '夫君幼时落水伤了根本，不能生育，也不能行房事'
    wire['beats'][0]['know'].append({       # 标点宽度漂移的重引
        'who': 'reader', 'how': 'learns', 'what': '夫君幼时落水伤了根本,不能生育,也不能行房事',
        'basis': ['伤了根本,不能生'],
    })
    wire['beats'][0]['know'].append({       # 真改写 → 新句柄
        'who': 'yun_niang', 'how': 'suspects', 'what': '夫君另有隐情未全说',
        'basis': ['伤了根本,不能生'],
    })
    rc12, issues = expand_chapter_wire(wire, chapter=1)
    assert issues == []
    first, repeat, other = rc12['beats'][0]['cognition']
    assert first['proposition']['statement'] == text          # 首现建立
    assert repeat['proposition'] == {'ref': first['proposition']['ref'],
                                     'statement': None}       # norm 等价 → 重引
    assert other['proposition']['ref'] != first['proposition']['ref']
    assert other['proposition']['statement'] == '夫君另有隐情未全说'


def test_expand_bad_range_and_address_are_item_issues():
    wire = _slim_wire()
    wire['beats'][0]['src'] = 'e01_04..e01_02'
    rc12, issues = expand_chapter_wire(wire, chapter=1)
    assert rc12 is None
    assert issues[0][0] == ('beats', 0)
    assert 'src 区间非法' in issues[0][1]

    wire = _slim_wire()
    wire['beats'][0]['state'][0]['at'] = 'a+b+c'
    rc12, issues = expand_chapter_wire(wire, chapter=1)
    assert rc12 is None
    assert issues[0][0] == ('beats', 0, 'state', 0)
    assert 'at 地址非法' in issues[0][1]


# ── 路由：compile／locate 同一把尺，路径词汇＝state／know ───────────────────

def test_compile_routes_v3_to_expanded_pipeline():
    observation, errors = compile_chapter_observation(
        _slim_wire(), evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
    assert errors == [], errors
    # 落盘形状是 canonical 字面量——下游消费者不变；basis 段号由引文定位。
    assert observation.protocol_version == PV_CANON
    assert observation.beats[0].event.startswith('新婚夜')
    assert observation.beats[0].source_refs == ('e01_01', 'e01_02')
    assert observation.beats[0].effects[0].basis[0].segment == 'e01_01'


def test_compile_v2_wire_still_accepted():
    """canonical 直通（盘面 canonical 与 wire_dir 领养的读面，S-01 兼容层）；
    直通缺段号按协议拒收，不由代码代补（只有瘦 wire 的引文才定位）。"""
    observation, errors = compile_chapter_observation(
        _slim_wire(), evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
    assert errors == [], errors
    canonical = observation.model_dump(mode='json')
    again, errors = compile_chapter_observation(
        canonical, evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
    assert errors == [], errors
    assert again == observation
    expanded, issues = expand_chapter_wire(_slim_wire(), chapter=1)
    assert issues == [] and expanded['beats'][0]['effects'][0]['basis'][0]['segment'] is None
    rejected, errors = compile_chapter_observation(
        expanded, evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
    assert rejected is None and any('协议校验失败' in e for e in errors)


def test_compile_v3_gates_use_slim_vocabulary():
    """闭集／人物闸照旧拦，错误消息与条目路径用 state／know 词汇。"""
    wire = _slim_wire()
    wire['beats'][0]['who'].append('stranger')                  # 人物闭集
    wire['beats'][0]['src'] = ['e01_01', 'e09_99']              # 段不在闭集
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
    assert observation is None
    assert any('participants 引用未知人物 stranger' in e for e in errors)
    assert any('e09_99' in e for e in errors)

    located = chapter_patch_locate(wire, errors, evidence_ids=EVIDENCE,
                                   chapter=1, character_slugs=SLUGS)
    assert located is not None
    paths = {path for path, _ in located}
    assert ('beats', 0) in paths                                # 人物/段问题定位到拍
    for path, messages in located:
        for message in messages:
            assert 'effects[' not in message and 'cognition[' not in message


def test_patch_locate_positions_slim_transfer_items():
    """know 条目的引文逐字失败 → 定位到 ('beats', 0, 'know', 0)。"""
    wire = _slim_wire()
    wire['beats'][0]['know'][0]['basis'] = ['原文里没有这句话']
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
    assert observation is None and errors
    located = chapter_patch_locate(wire, errors, evidence_ids=EVIDENCE,
                                   chapter=1, character_slugs=SLUGS)
    assert located is not None
    assert [path for path, _ in located] == [('beats', 0, 'know', 0)]
    assert any('know[1]' in message for _path, messages in located
               for message in messages)


def test_patch_locate_falls_back_when_unlocatable():
    """协议形状失败（多余字段）不可条目定位 → 退整份返修。"""
    wire = _slim_wire()
    wire['beats'][0]['surprise'] = 'unknown field'
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
    assert observation is None and errors
    assert chapter_patch_locate(wire, errors, evidence_ids=EVIDENCE,
                                chapter=1, character_slugs=SLUGS) is None


def test_v3_quote_canonicalization_still_applies():
    """D2 引文归一回取不因瘦身失效：全角逗号的真摘抄落盘为原文切片
    （norm() 尺剥标点——回取切片是原文连续子串，尾随标点不计入内容）。"""
    wire = _slim_wire()
    wire['beats'][0]['state'][0]['basis'] = ['幼时落水，伤了根本，不能生，也不能行房。']
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
    assert errors == [], errors
    quote = observation.beats[0].effects[0].basis[0].quote
    assert quote == '幼时落水,伤了根本,不能生,也不能行房'   # 原文半角字节切片


# ── 回归：expand(slim(x)) == x（模本地句柄重命名） ─────────────────────────

def _renamed(obs: dict) -> dict:
    """本地命题句柄按首现序重标，两侧可比较（句柄是标签不是语义）。"""
    obs = copy.deepcopy(obs)
    table: dict[str, str] = {}
    for beat in obs['beats']:
        for transfer in beat.get('effects', []) + beat.get('cognition', []):
            prop = transfer.get('proposition')
            if prop and prop.get('statement') is not None and prop['ref'] not in table:
                table[prop['ref']] = f'k{len(table) + 1:02d}'
    for beat in obs['beats']:
        for transfer in beat.get('effects', []) + beat.get('cognition', []):
            prop = transfer.get('proposition')
            if prop:
                assert prop['ref'] in table, f"悬空句柄 {prop['ref']}"
                prop['ref'] = table[prop['ref']]
    return obs


def test_round_trip_rich_observation():
    """覆盖全部分支的 canonical 观察：瘦下去（basis 只留引文）再经 expand 与
    引文定位编译回来，逐字段还原（段号由 Resolver 重新定位得到）。"""
    observation = {
        'protocol_version': PV_CANON,
        'beats': [
            {
                'event': '新婚夜坦白。', 'participants': ['lu_fujun', 'yun_niang'],
                'source_refs': ['e01_01', 'e01_02'],
                'scene_boundary': 'new', 'temporal_mode': 'single',
                'effects': [{
                    'target': {'kind': 'character', 'char_id': 'lu_fujun',
                               'subject': None, 'object': None},
                    'key': 'body_condition', 'op': 'set', 'modality': 'actual',
                    'value': '伤了根本', 'scope': 'book',
                    'disclosure': {'visibility': 'on_page', 'known_by': []},
                    'proposition': None,
                    'basis': [{'segment': 'e01_01', 'quote': '伤了根本,不能生'}],
                }],
                'cognition': [{
                    'proposition': {'ref': 'p_impotent', 'statement': '夫君不能生育'},
                    'holder': 'yun_niang', 'mode': 'learns',
                    'basis': [{'segment': 'e01_01', 'quote': '伤了根本,不能生'}],
                }],
                'loops': [{'ref': 'loop_plan', 'op': 'open',
                           'question': '借种之计能否实现？', 'resolution': None}],
            },
            {
                'event': '次日芸娘自问。', 'participants': ['yun_niang'],
                'source_refs': ['e01_01', 'e01_03', 'e01_04'],
                'scene_boundary': 'continue', 'temporal_mode': 'iterative',
                'effects': [{
                    'target': {'kind': 'relation', 'char_id': None,
                               'subject': 'lu_fujun', 'object': 'pei_shuyuan'},
                    'key': 'trust', 'op': 'unset', 'modality': 'expected',
                    'value': None, 'scope': 'scene',
                    'disclosure': {'visibility': 'concealed',
                                   'known_by': ['lu_fujun']},
                    'proposition': {'ref': 'p_hidden', 'statement': '借种仍在推进'},
                    'basis': [{'segment': 'e01_04', 'quote': '一家三口过日子。'}],
                }],
                'cognition': [
                    {'proposition': {'ref': 'p_impotent', 'statement': None},
                     'holder': 'reader', 'mode': 'confirms',
                     'basis': [{'segment': 'e01_01', 'quote': '伤了根本,不能生'}]},
                    {'proposition': {'ref': 'p_worry', 'statement': '芸娘担忧无子'},
                     'holder': 'yun_niang', 'mode': 'believes',
                     'basis': [{'segment': 'e01_03', 'quote': '芸娘落泪。'}]},
                ],
                'loops': [],
            },
        ],
    }
    slim = slim_chapter_wire(observation)
    assert slim['protocol_version'] == PV_WIRE
    # 瘦面形态：默认值不出现、例外保留。
    first, second = slim['beats']
    assert first['ev'] == '新婚夜坦白。'
    assert first['src'] == 'e01_01..e01_02'
    assert first['new_scene'] is True and 'iterative' not in first
    assert first['state'][0]['at'] == 'lu_fujun' and 'op' not in first['state'][0]
    assert first['loops'] == observation['beats'][0]['loops']
    assert 'new_scene' not in second and second['iterative'] is True
    assert second['state'][0] == {
        'at': 'lu_fujun+pei_shuyuan', 'key': 'trust', 'op': 'unset',
        'modality': 'expected', 'scope': 'scene',
        'hidden': {'by': ['lu_fujun'], 'what': '借种仍在推进'},
        'basis': ['一家三口过日子。'],
    }
    assert second['src'] == ['e01_01', 'e01_03', 'e01_04']
    assert second['know'][0]['what'] == '夫君不能生育'      # 重引文本经句柄表还原
    compiled, errors = compile_chapter_observation(
        slim, evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
    assert errors == [], errors
    assert _renamed(compiled.model_dump(mode='json')) == _renamed(observation)
