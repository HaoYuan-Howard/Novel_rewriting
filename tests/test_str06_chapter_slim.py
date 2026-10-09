#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_chapter_slim —— 章观察瘦 wire（CS-2 第一步，2026-10-03；
2026-10-07 basis 指针化升 v6；CS-S 1a 升 v8——start 单段＋区间代码派生、
on_stage、SlimEffect 删 scope/hidden/prop）。

覆盖 `contracts/schema/observation.py` 的瘦模型、
`novel/stages/stage2/slim.py::expand_chapter_wire／slim_chapter_wire` 的
确定性派生（含 `derive_beat_intervals` 的区间纪律）、
`compile_chapter_observation`／`chapter_patch_locate` 的字面量
路由（判定与定位同一把尺，条目路径用 state／know 词汇），以及
slim → expand → 段派生 还原 canonical 的回归（模本地句柄重命名）。语义
判据不因瘦身放宽：basis 只给段 id（引文＝段全文由代码切出，2026-10-07
裁决：模型只选段、代码切原文）、闭集闸全部照旧（反例见各条）。
"""
from __future__ import annotations

import copy

import pytest

from pydantic import ValidationError

from contracts.schema.observation import (
    STAGE2_CHAPTERS_PROTOCOL, STAGE2_CHAPTERS_WIRE_PROTOCOL,
    ChapterObservationWire,
)
from novel.stages.stage2.chapters import (
    chapter_patch_locate, compile_chapter_observation,
)
from novel.stages.stage2.slim import (
    derive_beat_intervals, expand_chapter_wire, ordered_segment_sequence,
    slim_chapter_wire,
)

PV_WIRE = STAGE2_CHAPTERS_WIRE_PROTOCOL
PV_CANON = STAGE2_CHAPTERS_PROTOCOL

#: 合成证据闭集：同章连续段（basis 指针派生的素材：引文＝段全文）。
EVIDENCE = {
    'e01_01': {'chapter': 1, 'text': '新婚夜,夫君坦白：幼时落水,伤了根本,不能生,也不能行房。'},
    'e01_02': {'chapter': 1, 'text': '夫君说求娶是为报恩。'},
    'e01_03': {'chapter': 1, 'text': '芸娘落泪。'},
    'e01_04': {'chapter': 1, 'text': '夫君提出向表弟借种,一家三口过日子。'},
}
SEQUENCE = ('e01_01', 'e01_02', 'e01_03', 'e01_04')
SLUGS = ('lu_fujun', 'yun_niang', 'pei_shuyuan')


def _slim_wire(**overrides):
    beat = {
        'event': '新婚夜，夫君向芸娘坦白不能生育，并求娶报恩。',
        'on_stage': ['lu_fujun', 'yun_niang'],
        'start': 'e01_01',
        'new_scene': True,
        'change': [{
            'at': 'lu_fujun', 'key': 'health',
            'val': '幼时落水伤了根本，不能生育，不能行房事',
            'evidence_refs': ['e01_01'],
        }],
        'know': [{
            'who': 'yun_niang', 'how': 'learns',
            'what': '夫君幼时落水伤了根本，不能生育，也不能行房事',
            'evidence_refs': ['e01_01'],
        }],
    }
    beat.update(overrides)
    return {'protocol_version': PV_WIRE, 'beats': [beat]}


def test_revision_scope_allows_derived_id_shift_and_rejects_boundary_changes():
    """新增知情导致后续 k 编号顺延不算越界；改证据边界/删拍仍被拒。"""
    from copy import deepcopy
    from novel.stages.stage2.chapters import chapter_revision_scope_issues
    wire = _slim_wire(know=[])
    wire['beats'].append({'event': '芸娘获知报恩说法', 'start': 'e01_03', 'know': [{
        'who': 'yun_niang', 'how': 'hears_claim', 'what': '夫君求娶是为报恩',
        'evidence_refs': ['e01_03']}]})

    def compile(w):
        result, errors = compile_chapter_observation(w, chapter=1,
            evidence_ids=EVIDENCE, character_slugs=SLUGS)
        assert not errors, errors
        return result

    previous = compile(wire)
    revised = deepcopy(wire)
    revised['beats'][0]['know'] = _slim_wire()['beats'][0]['know']
    after = compile(revised)
    assert previous.beats[1].cognition[0].proposition.ref != after.beats[1].cognition[0].proposition.ref
    assert chapter_revision_scope_issues(previous, after, beat_indexes=frozenset({0})) == []
    # canonical 采用也不能靠保留 start、缩短 source_refs 绕过范围闸。
    shrunk = after.model_copy(update={'beats': (
        after.beats[0], after.beats[1].model_copy(update={'source_refs': ('e01_03',)}))})
    assert any('证据覆盖范围' in e for e in chapter_revision_scope_issues(
        previous, shrunk, beat_indexes=frozenset({0})))
    revised['beats'][1]['start'] = 'e01_02'
    assert any('start' in e for e in chapter_revision_scope_issues(
        previous, compile(revised), beat_indexes=frozenset({1})))
    revised['beats'].pop()
    assert any('增删' in e for e in chapter_revision_scope_issues(
        previous, compile(revised), beat_indexes=frozenset({0})))


# ── 区间派生（v8）：start 纪律全部机械可判 ──────────────────────────────────

def test_intervals_derived_from_starts_sequence_position():
    """区间＝[start, 下一拍 start−1]、末拍到章末；「......」分隔段按构造归属
    其前最近一个 start（今日两本 383 拍的派生重放：378/383 逐字一致，其余
    4 拍只多吸收分隔段、1 拍＝ch04 owner patch 错位类——正是本批消除的形态）。"""
    intervals, issues = derive_beat_intervals(
        ['e01_01', 'e01_03', 'e01_04'], SEQUENCE, chapter=1)
    assert issues == []
    assert intervals == [['e01_01', 'e01_02'], ['e01_03'], ['e01_04']]


def test_intervals_numeric_order_not_lexicographic():
    """≥100 段的章按数值序，不按字典序（e01_100 不会排到 e01_99 前）。"""
    seq = ordered_segment_sequence(
        {'e01_9': {}, 'e01_99': {}, 'e01_100': {}, 'e01_101': {}})
    assert seq == ('e01_9', 'e01_99', 'e01_100', 'e01_101')
    intervals, issues = derive_beat_intervals(
        ['e01_9', 'e01_100'], seq, chapter=1)
    assert issues == []
    assert intervals == [['e01_9', 'e01_99'], ['e01_100', 'e01_101']]


def test_intervals_first_beat_may_skip_only_leading_e00():
    """第 1 章允许跳过前导 e00（引子弃权）；跳过非引子段即拒。"""
    ch1 = ('e00_01', 'e00_02', 'e01_01', 'e01_02')
    intervals, issues = derive_beat_intervals(['e01_01'], ch1, chapter=1)
    assert issues == []
    assert intervals == [['e01_01', 'e01_02']]
    # 首拍从引子中段开始（弃权 e00_01）：合法。
    intervals, issues = derive_beat_intervals(['e00_02', 'e01_01'], ch1, chapter=1)
    assert issues == []
    assert intervals == [['e00_02'], ['e01_01', 'e01_02']]
    # 跳过本章正文段：拒（首拍必须是章首段）。
    intervals, issues = derive_beat_intervals(['e01_02'], ch1, chapter=1)
    assert intervals is None
    assert issues[0][0] == ('beats', 0)
    assert '非引子段' in issues[0][1]
    # 其余章没有 e00 可跳：直接从章首段以外开始即拒。
    intervals, issues = derive_beat_intervals(
        ['e02_02'], ('e02_01', 'e02_02', 'e02_03'), chapter=2)
    assert intervals is None and '非引子段' in issues[0][1]


def test_intervals_start_out_of_closed_set_or_non_increasing():
    intervals, issues = derive_beat_intervals(['e09_99'], SEQUENCE, chapter=1)
    assert intervals is None
    assert issues[0][0] == ('beats', 0)
    assert '不在本章证据闭集内' in issues[0][1]

    intervals, issues = derive_beat_intervals(
        ['e01_03', 'e01_02'], SEQUENCE, chapter=1)
    assert intervals is None
    assert issues[0][0] == ('beats', 1)
    assert '严格递增' in issues[0][1]


# ── expand：默认值、地址串、区间 ────────────────────────────────────────────

def test_expand_defaults_addresses_and_intervals():
    wire = _slim_wire()
    wire['beats'].append({
        'event': '芸娘落泪后，夫君提出借种之计。',
        'on_stage': ['yun_niang'],
        'start': 'e01_03',
        'change': [{
            'at': 'lu_fujun+pei_shuyuan', 'key': 'kinship', 'val': 'cousins',
            'evidence_refs': ['e01_04'],
        }, {
            'at': 'world', 'key': 'social_standing', 'val': 'women_cannot_stand_alone',
            'evidence_refs': ['e01_03'],
        }],
        'know': [{
            'who': 'yun_niang', 'how': 'learns', 'what': '夫君求娶芸娘是为报恩',
            'evidence_refs': ['e01_02'],
        }],
    })
    rc12, issues = expand_chapter_wire(wire, chapter=1, sequence=SEQUENCE)
    assert issues == []
    first, second = rc12['beats']
    # 默认值全部回填成 rc12 形状（拍序即列表序、无拍号字段）。
    assert first['scene_boundary'] == 'new'
    assert first['temporal_mode'] == 'single'
    assert first['participants'] == ['lu_fujun', 'yun_niang']
    assert first['source_refs'] == ['e01_01', 'e01_02']       # 区间＝到下一拍 start−1
    assert second['source_refs'] == ['e01_03', 'e01_04']      # 末拍到章末
    assert rc12['protocol_version'] == PV_CANON
    char_eff = first['effects'][0]
    rel_eff, world_eff = second['effects']
    assert char_eff['target'] == {'kind': 'character', 'char_id': 'lu_fujun',
                                  'subject': None, 'object': None}
    assert rel_eff['target'] == {'kind': 'relation', 'char_id': None,
                                 'subject': 'lu_fujun', 'object': 'pei_shuyuan'}
    assert world_eff['target']['kind'] == 'world'
    for eff in (char_eff, rel_eff, world_eff):
        assert eff['op'] == 'set'
        assert eff['modality'] == 'actual'
        # CS-S 1b：scope／disclosure／proposition 不再进 canonical。
        assert set(eff) == {'target', 'key', 'op', 'value', 'modality', 'basis'}


def test_scene_location_is_beat_metadata_not_a_state_effect():
    """拍级 scene_location（2026-10-07 二次收口③）：compiler 合成登记键 world
    效应（作用域由表固定为 scene、op/modality/disclosure 全默认），证据面＝
    本拍派生区间全体；state 里出现 scene_location 即按条目拒（单一通路，S-04）。"""
    wire = _slim_wire()
    wire['beats'].append({'event': '尾声。', 'on_stage': [], 'start': 'e01_04'})
    wire['beats'][0]['scene_location'] = '婚房'
    rc12, issues = expand_chapter_wire(wire, chapter=1, sequence=SEQUENCE)
    assert issues == []
    scene = rc12['beats'][0]['effects'][0]
    assert scene == {
        'target': {'kind': 'world', 'char_id': None, 'subject': None, 'object': None},
        'key': 'scene_location', 'op': 'set', 'modality': 'actual', 'value': '婚房',
        'basis': [{'segment': 'e01_01', 'quote': None},
                  {'segment': 'e01_02', 'quote': None},
                  {'segment': 'e01_03', 'quote': None}],
    }
    # 单一通路（CS-S Phase 3 收紧）：change 里再写 scene_location 直接被 wire
    # 的键闭集拒绝（scene_location 不在模型面 CHANGE_KEYS——compiler 专用合成键）。
    wire = _slim_wire()
    wire['beats'][0]['change'].append({
        'at': 'world', 'key': 'scene_location', 'val': '婚房',
        'evidence_refs': ['e01_01'],
    })
    with pytest.raises(ValidationError, match='scene_location'):
        ChapterObservationWire.model_validate(wire)


def test_registered_keys_compile_with_derived_scope():
    """登记键照常编译：作用域读时按表派生（fold 侧 REGISTERED_STATE_KEYS），
    canonical 效应不再携带 scope 字段。"""
    wire = _slim_wire()
    wire['beats'][0]['change'][0] = {
        'at': 'yun_niang', 'key': 'body_state', 'val': '头套',
        'evidence_refs': ['e01_01'],
    }
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
    assert errors == [] and observation is not None
    assert set(observation.beats[0].effects[0].model_dump()) == {
        'target', 'key', 'op', 'value', 'modality', 'basis'}


def test_wire_rejects_dead_fields_scope_hidden_prop():
    """v8 死字段闭合：SlimEffect 的 scope／hidden／prop 出现即按条目形状拒收
    （5 观察集 404 效应实证 0 命中——不是瘦负担，是死字段）。"""
    for dead in ({'scope': 'scene'}, {'prop': '表弟确已到府'},
                 {'hidden': {'by': ['lu_fujun'], 'what': '借种计划仍在推进'}}):
        wire = _slim_wire()
        wire['beats'][0]['change'][0].update(dead)
        with pytest.raises(Exception):
            expand_chapter_wire(wire, chapter=1, sequence=SEQUENCE)
        observation, errors = compile_chapter_observation(
            wire, evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
        assert observation is None and any('协议校验失败' in e for e in errors), errors
        located = chapter_patch_locate(wire, errors, evidence_ids=EVIDENCE,
                                       chapter=1, character_slugs=SLUGS)
        assert located is not None and located[0][0] == ('beats', 0, 'change', 0)


def test_expand_know_text_resolution():
    """know 命题文本归并（跨拍：首现建立、重引不带 statement）。"""
    wire = _slim_wire()
    wire['beats'][0]['know'].append({       # 标点宽度漂移的重引
        'who': 'lu_fujun', 'how': 'learns', 'what': '夫君幼时落水伤了根本,不能生育,也不能行房事',
        'evidence_refs': ['e01_01'],
    })
    wire['beats'][0]['know'].append({       # 真改写 → 新句柄
        'who': 'yun_niang', 'how': 'suspects', 'what': '夫君另有隐情未全说',
        'evidence_refs': ['e01_01'],
    })
    rc12, issues = expand_chapter_wire(wire, chapter=1, sequence=SEQUENCE)
    assert issues == []
    first, repeat, other = rc12['beats'][0]['cognition']
    text = '夫君幼时落水伤了根本，不能生育，也不能行房事'
    assert first['proposition']['statement'] == text          # 首现建立
    assert repeat['proposition'] == {'ref': first['proposition']['ref'],
                                     'statement': None}       # norm 等价 → 重引
    assert other['proposition']['ref'] != first['proposition']['ref']
    assert other['proposition']['statement'] == '夫君另有隐情未全说'


def test_expand_bad_start_and_address_are_item_issues():
    wire = _slim_wire()
    wire['beats'][0]['start'] = 'e09_99'
    rc12, issues = expand_chapter_wire(wire, chapter=1, sequence=SEQUENCE)
    assert rc12 is None
    assert issues[0][0] == ('beats', 0)
    assert '不在本章证据闭集内' in issues[0][1]

    wire = _slim_wire()
    wire['beats'][0]['change'][0]['at'] = 'a+b+c'
    rc12, issues = expand_chapter_wire(wire, chapter=1, sequence=SEQUENCE)
    assert rc12 is None
    assert issues[0][0] == ('beats', 0, 'change', 0)
    assert 'at 地址非法' in issues[0][1]


# ── 路由：compile／locate 同一把尺，路径词汇＝state／know ───────────────────

def test_compile_routes_wire_to_expanded_pipeline():
    observation, errors = compile_chapter_observation(
        _slim_wire(), evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
    assert errors == [], errors
    # 落盘形状是 canonical 字面量——下游消费者不变；basis 引文由代码按段全文切出。
    assert observation.protocol_version == PV_CANON
    assert observation.beats[0].event.startswith('新婚夜')
    # 单拍章：start 到章末的派生区间（e01_01..e01_04）。
    assert observation.beats[0].source_refs == tuple(SEQUENCE)
    assert observation.beats[0].effects[0].basis[0].segment == 'e01_01'
    assert observation.beats[0].effects[0].basis[0].quote == EVIDENCE['e01_01']['text']


def test_compile_canonical_wire_still_accepted():
    """canonical 直通（盘面 canonical 与 wire_dir 领养的读面，S-01 兼容层）；
    直通缺引文按协议拒收，不由代码代补（只有瘦 wire 的段 id 才派生）。"""
    observation, errors = compile_chapter_observation(
        _slim_wire(), evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
    assert errors == [], errors
    canonical = observation.model_dump(mode='json')
    again, errors = compile_chapter_observation(
        canonical, evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
    assert errors == [], errors
    assert again == observation
    expanded, issues = expand_chapter_wire(_slim_wire(), chapter=1, sequence=SEQUENCE)
    assert issues == [] and expanded['beats'][0]['effects'][0]['basis'][0] == {
        'segment': 'e01_01', 'quote': None}
    rejected, errors = compile_chapter_observation(
        expanded, evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
    assert rejected is None and any('协议校验失败' in e for e in errors)


def test_compile_gates_use_slim_vocabulary():
    """闭集／人物闸照旧拦，错误消息与条目路径用 state／know 词汇（v8：坏
    start 使区间派生短路，与人物闭集分开验证）。"""
    wire = _slim_wire()
    wire['beats'][0]['on_stage'].append('stranger')              # 人物闭集
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
    assert observation is None
    assert any('participants 引用未知人物 stranger' in e for e in errors)
    located = chapter_patch_locate(wire, errors, evidence_ids=EVIDENCE,
                                   chapter=1, character_slugs=SLUGS)
    assert located is not None
    assert [path for path, _ in located] == [('beats', 0)]
    for path, messages in located:
        for message in messages:
            assert 'effects[' not in message and 'cognition[' not in message

    wire = _slim_wire()
    wire['beats'][0]['start'] = 'e09_99'                         # 段不在闭集
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
    assert observation is None
    assert any('e09_99' in e for e in errors)


def test_patch_locate_positions_slim_transfer_items():
    """know 条目的 basis 段不在闭集 → 定位到 ('beats', 0, 'know', 0)。"""
    wire = _slim_wire()
    wire['beats'][0]['know'][0]['evidence_refs'] = ['e01_99']
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
    assert observation is None and errors
    located = chapter_patch_locate(wire, errors, evidence_ids=EVIDENCE,
                                   chapter=1, character_slugs=SLUGS)
    assert located is not None
    assert [path for path, _ in located] == [('beats', 0, 'know', 0)]
    assert any('know[1]' in message for _path, messages in located
               for message in messages)


def test_patch_locate_routes_evidence_outside_derived_interval_to_beat():
    """CS-S 1a：evidence_refs 落在派生区间外 → 整拍路径，指引改选区间内的段、
    边界错位走整章修订（v8 的区间由 start 派生，不能只扩一拍）。"""
    wire = _slim_wire()
    wire['beats'].append({'event': '尾声。', 'on_stage': [], 'start': 'e01_03'})
    wire['beats'][0]['change'][0]['evidence_refs'] = ['e01_04']  # 属下一拍区间
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
    assert observation is None and errors
    assert any('派生区间' in e and '整章修订' in e for e in errors), errors
    located = chapter_patch_locate(wire, errors, evidence_ids=EVIDENCE,
                                   chapter=1, character_slugs=SLUGS)
    assert located is not None
    assert [path for path, _ in located] == [('beats', 0)]


def test_patch_locate_falls_back_only_for_top_level_shape_failures():
    """2026-10-07 二次收口④：形状错误按 pydantic 路径的可修性路由——条目内
    多余字段（beats[0].surprise）定位到该拍窄修；顶层多余字段没有可编辑条目，
    退整份返修。"""
    wire = _slim_wire()
    wire['beats'][0]['surprise'] = 'unknown field'
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
    assert observation is None and errors
    assert chapter_patch_locate(wire, errors, evidence_ids=EVIDENCE,
                                chapter=1, character_slugs=SLUGS) == [
        (('beats', 0), errors)]

    wire = _slim_wire()
    wire['surprise'] = 'unknown top-level field'
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
    assert observation is None and errors
    assert chapter_patch_locate(wire, errors, evidence_ids=EVIDENCE,
                                chapter=1, character_slugs=SLUGS) is None


def test_wire_rejects_quote_text_in_evidence_refs():
    """2026-10-07 裁决的形状闸：evidence_refs 只收段 id——旧协议的逐字引文
    字符串（哪怕逐字正确）按条目定位窄修；转写失败类错误构造上不再存在
    （I-13：换字面量 v8，旧回答不做推断采用）。"""
    wire = _slim_wire()
    wire['beats'][0]['change'][0]['evidence_refs'] = ['新婚夜,夫君坦白：幼时落水,伤了根本。']
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
    assert observation is None and any('协议校验失败' in e for e in errors), errors
    assert chapter_patch_locate(wire, errors, evidence_ids=EVIDENCE,
                                chapter=1, character_slugs=SLUGS) == [
        (('beats', 0, 'change', 0), errors)]


# ── 回归：expand(slim(x)) == x（模本地句柄重命名） ─────────────────────────

def _renamed(obs: dict) -> dict:
    """本地命题句柄按首现序重标，两侧可比较（句柄是标签不是语义）。

    Phase 3：直通 fixture 缺省的 access 按 canonical 默认补齐（witnessed）。
    """
    obs = copy.deepcopy(obs)
    table: dict[str, str] = {}
    for beat in obs['beats']:
        beat.setdefault('access', 'witnessed')
        for transfer in beat.get('cognition', []):
            prop = transfer.get('proposition')
            if prop and prop.get('statement') is not None and prop['ref'] not in table:
                table[prop['ref']] = f'k{len(table) + 1:02d}'
    for beat in obs['beats']:
        for transfer in beat.get('cognition', []):
            prop = transfer.get('proposition')
            if prop:
                assert prop['ref'] in table, f"悬空句柄 {prop['ref']}"
                prop['ref'] = table[prop['ref']]
    return obs


def test_round_trip_rich_observation():
    """覆盖可表示分支的 canonical 观察：瘦下去（basis 只留段 id）再经 expand
    与引文派生编译回来，逐字段还原（v8 可表示面：区间连续、effect 无
    concealed／命题锚、无表外 scene 作用域——死字段面由 1b 全删）。"""
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
                    'key': 'health', 'op': 'set', 'modality': 'actual',
                    'value': '伤了根本',
                    'basis': [{'segment': 'e01_01', 'quote': '新婚夜,夫君坦白：幼时落水,伤了根本,不能生,也不能行房。'}],
                }],
                'cognition': [{
                    'proposition': {'ref': 'p_impotent', 'statement': '夫君不能生育'},
                    'holder': 'yun_niang', 'mode': 'learns',
                    'basis': [{'segment': 'e01_01', 'quote': '新婚夜,夫君坦白：幼时落水,伤了根本,不能生,也不能行房。'}],
                }],
                'loops': [{'ref': 'loop_plan', 'op': 'open',
                           'question': '借种之计能否实现？', 'resolution': None}],
            },
            {
                'event': '次日芸娘自问。', 'participants': ['yun_niang'],
                'source_refs': ['e01_03', 'e01_04'],
                'scene_boundary': 'continue', 'temporal_mode': 'iterative',
                'effects': [
                    {'target': {'kind': 'world', 'char_id': None,
                                'subject': None, 'object': None},
                     'key': 'scene_location', 'op': 'set', 'modality': 'actual',
                     'value': '婚房',
                     'basis': [{'segment': 'e01_03', 'quote': '芸娘落泪。'},
                               {'segment': 'e01_04',
                                'quote': '夫君提出向表弟借种,一家三口过日子。'}]},
                    {'target': {'kind': 'relation', 'char_id': None,
                                'subject': 'lu_fujun', 'object': 'pei_shuyuan'},
                     'key': 'attitude', 'op': 'unset', 'modality': 'expected',
                     'value': None,
                     'basis': [{'segment': 'e01_04', 'quote': '夫君提出向表弟借种,一家三口过日子。'}],
                     },
                ],
                'cognition': [
                    # CS-S Phase 3：观察面禁 reader——重引（statement=None）改由
                    # 人物条目承载（蓝图里的 reader 是 compile 派生物，不逆映射）。
                    {'proposition': {'ref': 'p_impotent', 'statement': None},
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
    assert first['event'] == '新婚夜坦白。'
    assert first['start'] == 'e01_01'
    assert first['on_stage'] == ['lu_fujun', 'yun_niang']
    assert first['new_scene'] is True and 'iterative' not in first
    assert first['change'][0]['at'] == 'lu_fujun' and 'op' not in first['change'][0]
    assert first['loops'] == observation['beats'][0]['loops']
    assert 'new_scene' not in second and second['iterative'] is True
    # scene_location 效应还原为拍级字段（合成效应的逆），不留在 state。
    assert second['scene_location'] == '婚房'
    assert second['change'][0] == {
        'at': 'lu_fujun+pei_shuyuan', 'key': 'attitude', 'op': 'unset',
        'modality': 'expected',
        'evidence_refs': ['e01_04'],
    }
    assert second['start'] == 'e01_03'
    assert second['know'][0]['what'] == '夫君不能生育'      # 重引文本经句柄表还原
    compiled, errors = compile_chapter_observation(
        slim, evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
    assert errors == [], errors
    assert _renamed(compiled.model_dump(mode='json')) == _renamed(observation)


def test_slim_refuses_unrepresentable_concealed_or_prop():
    """canonical 效应带 wire 不可表达的字段（1b 删掉的 disclosure／proposition／
    scope 一类旧面）：逆映射显式拒绝，不静默丢语义（I-34——迁移面不制造语义
    丢失）。守卫按“现役字段集之外一律拒”判定，不点名死字段——任意多余字段同拒。"""
    observation = {
        'protocol_version': PV_CANON,
        'beats': [{
            'event': 'x', 'participants': [], 'source_refs': ['e01_01'],
            'scene_boundary': 'new', 'temporal_mode': 'single',
            'effects': [{
                'target': {'kind': 'world', 'char_id': None,
                           'subject': None, 'object': None},
                'key': 'narration_fact', 'op': 'set', 'modality': 'actual',
                'value': '表弟确已到府', 'scope': 'book',
                'disclosure': {'visibility': 'concealed', 'known_by': ['lu_fujun']},
                'proposition': {'ref': 'p1', 'statement': '表弟确已到府'},
                'basis': [{'segment': 'e01_01', 'quote': '…'}],
            }],
            'cognition': [], 'loops': [],
        }],
    }
    with pytest.raises(ValueError, match='不可表达'):
        slim_chapter_wire(observation)


def test_scene_location_synthesis_does_not_shift_change_paths():
    """实证反例（2026-10-08 审阅 B#2）：拍级 scene_location 由 expand 合成登记键
    效应并 `insert(0)`（canonical 效应句柄 `bNN.eK` 按序派发，顺序不能动），
    而条目路径与消息标签原先按 canonical effects 下标报——带 scene_location 的
    拍里 change 的路径与序号整体 +1，窄修会指向不存在的条目（真正坏的那条
    没被指到）。同一条错误在有／无 scene_location 时路径与标签必须一致。"""
    results = {}
    for with_location in (False, True):
        wire = _slim_wire()
        beat = wire['beats'][0]
        beat['change'].append({
            'at': 'yun_niang', 'key': 'intent', 'val': '想借种',
            'evidence_refs': ['e02_01'],   # 章外段：该转移（change[2]）的精确 issue
        })
        if with_location:
            beat['scene_location'] = '婚房'
        observation, errors = compile_chapter_observation(
            wire, evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
        assert observation is None
        located = chapter_patch_locate(
            wire, errors, evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
        results[with_location] = (errors, located)
    plain_errors, plain_located = results[False]
    loc_errors, loc_located = results[True]
    assert len(plain_errors) == len(loc_errors) == 1
    assert 'change[2]' in plain_errors[0] and 'change[2]' in loc_errors[0], loc_errors
    assert len(plain_located) == len(loc_located) == 1
    assert plain_located[0][0] == loc_located[0][0] == ('beats', 0, 'change', 1)
    # canonical 顺序不变：合成的 scene_location 仍是首条效应（句柄稳定）。
    good = _slim_wire(scene_location='婚房')
    canonical, issues = expand_chapter_wire(good, chapter=1, sequence=SEQUENCE)
    assert issues == [] and canonical['beats'][0]['effects'][0]['key'] == 'scene_location'


def test_know_holder_and_change_target_closed_at_chapter_acceptance():
    """实证反例（2026-10-08 审阅 B#5）：participants 闭集已左移到本章验收（R3），
    但 know.who 与 change 的作用对象仍要到 compile 才报——`who:'public'`（D-5
    已删的持有者）或任何非人物 slug 通过本章验收、付费章响应被接受后 compile
    才失败，白白再购一轮。两者对书级人物表的闭集在本章验收即查，且是条目级
    路径（know[i]／change[j]），带 scene_location 的拍里 change 下标也不错位。"""
    wire = _slim_wire(scene_location='婚房')
    wire['beats'][0]['know'][0]['who'] = 'public'
    wire['beats'][0]['change'][0]['at'] = 'ghost'
    wire['beats'][0]['change'].append({
        'at': 'yun_niang+ghost', 'key': 'attitude', 'val': '戒备',
        'evidence_refs': ['e01_02']})
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
    assert observation is None
    assert any('cognition holder 引用未知人物 public' in e and 'know[1]' in e
               for e in errors), errors
    assert any('effect 引用未知人物 ghost' in e and 'change[1]' in e for e in errors), errors
    assert any('effect 引用未知人物 ghost' in e and 'change[2]' in e for e in errors), errors
    located = chapter_patch_locate(wire, errors, evidence_ids=EVIDENCE, chapter=1,
                                   character_slugs=SLUGS)
    assert located is not None
    assert sorted(path for path, _ in located) == [
        ('beats', 0, 'change', 0), ('beats', 0, 'change', 1), ('beats', 0, 'know', 0)]
    # 闭集内人物不误报；reader 在 wire 层即拒（观察面禁 reader）。
    good = _slim_wire(scene_location='婚房')
    assert compile_chapter_observation(
        good, evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)[1] == []
    bad = _slim_wire()
    bad['beats'][0]['know'][0]['who'] = 'reader'
    assert compile_chapter_observation(
        bad, evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)[0] is None


def test_change_key_outside_closed_set_gets_item_level_guidance():
    """实证反例（2026-10-08 审阅 A#6）：wire 的 `SlimEffect.key` 用闭集正则，
    scene_location 在 change 里被 pydantic 先拒、报的是整段正则——slim／book 里
    “scene_location 是拍级字段”的引导分支根本不可达，模型拿不到正确去向。现在
    越闭集的 key 是条目级错误（可窄修），消息给出去向：scene_location→拍级
    字段，其余→回到【变化键闭集】清单。"""
    wire = _slim_wire()
    wire['beats'][0]['change'][0]['key'] = 'scene_location'
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
    assert observation is None and len(errors) == 1, errors
    assert '拍级字段' in errors[0] and 'scene_location' in errors[0]
    assert 'String should match' not in errors[0]
    located = chapter_patch_locate(
        wire, errors, evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
    assert located is not None and [p for p, _ in located] == [('beats', 0, 'change', 0)]

    wire = _slim_wire()
    wire['beats'][0]['change'][0]['key'] = 'plan_x'
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
    assert observation is None and len(errors) == 1, errors
    assert '变化键闭集' in errors[0] and 'String should match' not in errors[0]
    located = chapter_patch_locate(
        wire, errors, evidence_ids=EVIDENCE, chapter=1, character_slugs=SLUGS)
    assert located is not None and [p for p, _ in located] == [('beats', 0, 'change', 0)]


def test_slim_refuses_any_unrepresentable_effect_field_and_reads_only_fact_ids_from_the_registry():
    """逆映射的多余字段守卫不依赖死字段名单（原先点名读 disclosure／proposition，
    1b 之后那是读已删字段的死代码）；事实编号判定与章验收同一份定义
    `is_fact_ref`（`f01` 是词表引用；一位数的 `f1` 是章内本地句柄，不是引用）。"""
    base = {
        'event': 'x', 'participants': [], 'source_refs': ['e01_01'],
        'scene_boundary': 'new', 'temporal_mode': 'single', 'loops': [],
        'effects': [{
            'target': {'kind': 'world', 'char_id': None, 'subject': None, 'object': None},
            'key': 'narration_fact', 'op': 'set', 'modality': 'actual',
            'value': '表弟确已到府', 'extra_field': 1,
            'basis': [{'segment': 'e01_01', 'quote': '…'}]}],
        'cognition': []}
    with pytest.raises(ValueError, match='不可表达'):
        slim_chapter_wire({'protocol_version': PV_CANON, 'beats': [base]})

    def cognition(ref: str) -> dict:
        beat = copy.deepcopy(base)
        beat['effects'] = []
        beat['cognition'] = [{
            'proposition': {'ref': ref, 'statement': '夫君藏身床底'},
            'holder': 'yun_niang', 'mode': 'learns',
            'basis': [{'segment': 'e01_01', 'quote': '…'}]}]
        return slim_chapter_wire({'protocol_version': PV_CANON, 'beats': [beat]})

    assert cognition('f01')['beats'][0]['know'][0].get('fact') == 'f01'
    local = cognition('f1')['beats'][0]['know'][0]
    assert 'fact' not in local and local['what'] == '夫君藏身床底'


@pytest.mark.parametrize('defect', ['extra', 'planned', 'unknown', 'narrow_basis', 'duplicate'])
def test_slim_scene_location_never_discards_unrepresentable_semantics(defect):
    """位置分支同样先验字段及可表达面；非默认效应、证据缩窄与重复不得静默丢失。"""
    effect = {
        'target': {'kind': 'world'}, 'key': 'scene_location', 'op': 'set',
        'modality': 'actual', 'value': '饭厅',
        'basis': [{'segment': ref, 'quote': '…'} for ref in ('e01_01', 'e01_02')],
    }
    beat = {
        'event': '来到饭厅', 'participants': [], 'source_refs': ['e01_01', 'e01_02'],
        'scene_boundary': 'new', 'temporal_mode': 'single',
        'effects': [effect], 'cognition': [], 'loops': [],
    }
    assert slim_chapter_wire({'beats': [beat]})['beats'][0]['scene_location'] == '饭厅'
    if defect == 'extra':
        effect['extra_field'] = 1
    elif defect == 'planned':
        effect['modality'] = 'planned'
    elif defect == 'unknown':
        effect.update(op='unknown', value=None)
    elif defect == 'narrow_basis':
        effect['basis'].pop()
    else:
        beat['effects'].append(copy.deepcopy(effect))
    with pytest.raises(ValueError, match='不可表达'):
        slim_chapter_wire({'beats': [beat]})
