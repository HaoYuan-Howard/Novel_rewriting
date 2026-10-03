#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_a2_preserve —— R10a a2 判据面的定向红绿例（Preserve 证据视图、
anchors 锚定、series、evidence_conflicts 形状）。

【验证边界】`project_preserve_view` 确定性渲染（句柄闭集＋逐字引文＋证据
窗口，取代 Blueprint JSON 倾倒）；`assemble_preserve` v2 的机械闸——义务
anchors 闭集（C4 锚定纪律）、lineage 由 compiler 从锚点派生（I-15/I-21）、
series 成员闭集互斥（C3/C5 的机械面）、冲突报告只验形状（消费归 a3）。
「多次发生是否合并」「义务选得对不对」是语义，归 Preserve 与核读，不在此。
"""
from __future__ import annotations

from contracts.schema.grounding import ObligationSeries, PreserveCheckpoint
from contracts.schema.source import (
    BlueprintsArtifactV15, ChapterBlueprint, CharactersCore,
)
from novel.stages.stage4.grounding import (
    assemble_preserve, project_preserve_view,
)
from tests.str06_expression_inputs import sample_blueprint_expression
from tests.str06_facilities import v13_beat, v13_effect
from tests.str06_semantic_cases import CASES_BY_BATCH

A2_CASES = CASES_BY_BATCH['R10a-a2']

BASIS = ({'segment': 'e01_01', 'quote': '夫君藏进床底'},)


def _characters() -> CharactersCore:
    return CharactersCore.model_validate({
        'characters': {
            'yun_niang': {'canonical': '云娘', 'surfaces': [
                {'surface': '云娘', 'from_chapter': 1, 'source_refs': ['e01_01']}]},
            'fu_jun': {'canonical': '夫君', 'surfaces': [
                {'surface': '夫君', 'from_chapter': 1, 'source_refs': ['e01_01']}]},
        },
        'initial_state': [
            {'state_id': 's0_01', 'target': {'kind': 'character', 'char_id': 'fu_jun'},
             'key': 'birthright', 'value': '养子', 'persistence': 'stable',
             'knowledge': None, 'source_refs': ['e01_01'], 'basis': [BASIS[0]]}],
    })


def _blueprint() -> BlueprintsArtifactV15:
    return BlueprintsArtifactV15.model_validate({
        'source_expression': sample_blueprint_expression(),
        'world_constraints': [
            {'constraint_id': 'wc_01', 'kind': 'hard_rule',
             'statement': '术法须以寿命为代价', 'source_refs': ['e01_01'],
             'basis': [BASIS[0]]}],
        'chapters': [
            {'chapter': 1,
             'beats': [
                 v13_beat('b01_01', '夫君藏进床底', participants=('fu_jun',),
                          source_refs=('e01_01',),
                          effects=(v13_effect(
                              {'kind': 'character', 'char_id': 'fu_jun'},
                              'scene_location', '床底', scope='scene',
                              visibility='concealed', known_by=('fu_jun',),
                              proposition={'ref': 'p_hide', 'statement': '夫君藏身床底'},
                              basis=BASIS),),
                          cognition=({'proposition': {'ref': 'p_guess',
                                                      'statement': '夫君像是看穿了什么'},
                                      'holder': 'yun_niang', 'mode': 'suspects',
                                      'basis': BASIS},)),
                 v13_beat('b01_02', '云娘起疑', source_refs=('e01_02',),
                          cognition=({'proposition': {'ref': 'p_hide'},
                                      'holder': 'yun_niang', 'mode': 'suspects',
                                      'basis': ({'segment': 'e01_02',
                                                 'quote': '云娘听见床板响动'},)},)),
             ],
             'scenes': [{'scene_id': 'sc01', 'beat_ids': ['b01_01', 'b01_02']}],
             'causal_edges': []},
        ],
        'evidence': {'e01_01': '夫君藏进床底。', 'e01_02': '云娘听见床板响动。'},
    })


def _wire(**over) -> dict:
    wire = {'protocol_version': 'stage4-preserve-v2', 'obligations': [
        {'kind': 'event_core', 'statement': '夫君藏身床底',
         'anchors': ['b01_01.e1'],
         'must_preserve': True, 'satisfaction': '正文呈现藏身与佯离的关系',
         'requires': []},
        {'kind': 'character_arc', 'statement': '云娘从起疑到怀疑加深',
         'anchors': ['b01_01.c1', 'b01_02.c1'],
         'must_preserve': True, 'satisfaction': '正文呈现怀疑的渐进', 'requires': []},
        {'kind': 'identity', 'statement': '夫君身世',
         'anchors': ['s0_01'],
         'must_preserve': False, 'satisfaction': '', 'requires': []},
    ]}
    wire.update(over)
    return wire


def _assemble(wire: dict):
    return assemble_preserve(
        wire, policy_id='p', policy_version=5, characters=_characters(),
        blueprint=_blueprint(), provider='x', model='y', request_sha256='r' * 64)


# ───────────────────────── 投影视图：确定性渲染 ─────────────────────────

def test_project_preserve_view_renders_handles_quotes_and_windows():
    """视图含全部句柄类别（s0/wc/b拍.e|c|l）、逐字引文、证据窗口（邻段并入）
    与 ex_id 闭集节；不含本地命题句柄。"""
    view = project_preserve_view(_characters(), _blueprint())
    for handle in ('[s0_01]', '[wc_01]', '[b01_01.e1]', '[b01_01.c1]', '[b01_02.c1]'):
        assert handle in view, handle
    assert '「夫君藏进床底」' in view, '逐字引文按 basis 渲染'
    assert 'concealed' in view and 'expected' not in view.split('【证据窗口')[0]
    # 事实行带 modality/可见性与命题（命题按 canonical 的正式 ref 原样渲染）
    assert 'actual' in view and 'suspects' in view
    assert '命题 p_hide「夫君藏身床底」' in view
    # 证据窗口：被引段全文（含邻段合并文本）
    assert 'e01_01：' in view and '夫君藏进床底' in view.split('【证据窗口')[1]


def test_project_preserve_view_is_deterministic_and_pure():
    """同一输入两次渲染逐字节一致；输入模型不被修改（I-24）。"""
    from copy import deepcopy
    chars, bp = _characters(), _blueprint()
    chars_before, bp_before = deepcopy(chars.model_dump()), deepcopy(bp.model_dump())
    first = project_preserve_view(chars, bp)
    second = project_preserve_view(chars, bp)
    assert first == second
    assert chars.model_dump() == chars_before and bp.model_dump() == bp_before


# ───────────────────────── assemble v2：锚定与派生 ─────────────────────────

def test_green_wire_publishes_with_derived_lineage_and_typed_anchors():
    """绿：anchors 闭集内 → checkpoint 发布；lineage 从锚点派生（blueprint_refs
    ＝锚点主体、character_ids＝效应对象＋认知 holder）；anchors 是 compiler
    类型化拷贝（C1 的 a2 面：cognition.mode 原样保留 suspects，不升格）。"""
    checkpoint, errors = _assemble(_wire())
    assert errors == [] and checkpoint is not None, errors
    first = checkpoint.obligations[0]
    assert first.lineage.blueprint_refs == ('b01_01',)
    assert set(first.lineage.character_ids) == {'fu_jun'}
    assert first.anchors[0].source_kind == 'effect'
    assert first.anchors[0].modality == 'actual'
    assert first.anchors[0].visibility == 'concealed'
    second = checkpoint.obligations[1]
    assert second.lineage.blueprint_refs == ('b01_01', 'b01_02')
    assert second.lineage.character_ids == ('yun_niang',)
    # C1（a2 面）：mode 由 compiler 从 cognition 拷贝——怀疑保持怀疑
    assert all(a.mode == 'suspects' for a in second.anchors)
    identity_ob = checkpoint.obligations[2]
    assert identity_ob.lineage.blueprint_refs == ('s0_01',)
    assert identity_ob.lineage.character_ids == ('fu_jun',)


def test_obligation_without_anchors_or_unknown_handle_is_rejected():
    """红：缺 anchors（义务必须锚定原子）；未知句柄（闭集外）精确拒。"""
    missing = _wire()
    missing['obligations'][0].pop('anchors')
    _cp, errors = _assemble(missing)
    assert any('缺 anchors' in e for e in errors), errors
    bad = _wire()
    bad['obligations'][0]['anchors'] = ['b01_01.e9', 'b99_99.c1']
    _cp, errors = _assemble(bad)
    assert any("未知句柄：'b01_01.e9'" in e for e in errors), errors
    assert any("未知句柄：'b99_99.c1'" in e for e in errors), errors


def test_lineage_key_is_refused_in_wire():
    """红（I-21）：wire 带 lineage（v1 旧形状）即拒——来源由 compiler 派生。"""
    stale = _wire()
    stale['obligations'][0]['lineage'] = {
        'blueprint_refs': ['b01_01'], 'character_ids': []}
    _cp, errors = _assemble(stale)
    assert any('未知字段' in e and 'lineage' in e for e in errors), errors


# ───────────────────────── series：同一机制的多次发生 ─────────────────────────

def test_series_green_publishes_and_members_resolve_to_formal_ids():
    """绿：members 序号（1-based）→ 正式 ID；mechanism 取源表达 ex_id。"""
    from contracts.schema.expression import SourceExpressionItem
    wire = _wire()
    # 用第 1、2 条义务（各自独立的兑现时点）登记同一机制
    ex_id = _blueprint().source_expression.items[0].ex_id
    wire['series'] = [{'members': [1, 2], 'mechanism': ex_id}]
    checkpoint, errors = _assemble(wire)
    assert errors == [] and checkpoint is not None, errors
    assert checkpoint.series == (ObligationSeries(
        members=('ob01', 'ob02'), mechanism=ex_id),)
    # mechanism=None 合法（没有合用的源表达）
    wire['series'] = [{'members': [1, 2], 'mechanism': None}]
    checkpoint, errors = _assemble(wire)
    assert errors == [] and checkpoint.series[0].mechanism is None


def test_series_red_shapes_are_rejected():
    """红（C3/C5 的机械面）：成员越界／少于 2／mechanism 不在 ex_id 闭集；
    同一义务进两个 series 由 schema 互斥闸拒。"""
    oob = _wire()
    oob['series'] = [{'members': [1, 9], 'mechanism': None}]
    _cp, errors = _assemble(oob)
    assert any('成员序号越界' in e for e in errors), errors

    single = _wire()
    single['series'] = [{'members': [1], 'mechanism': None}]
    _cp, errors = _assemble(single)
    assert any('至少 2 个成员' in e for e in errors), errors

    bad_mech = _wire()
    bad_mech['series'] = [{'members': [1, 2], 'mechanism': 'ex_99'}]
    _cp, errors = _assemble(bad_mech)
    assert any('ex_id 闭集' in e for e in errors), errors

    # schema 互斥：同一义务进两个 series——wire 两个组都含义务 1。R10c 硬化
    # （6d0e6231）：构造期 ValidationError 归入修订反馈（不再裸穿 exit 70），
    # 故 assemble 拒签 errors（话术含互斥），供同节点修订轮重问。
    import pytest
    from pydantic import ValidationError
    overlap = _wire()
    overlap['series'] = [{'members': [1, 2], 'mechanism': None},
                         {'members': [1, 3], 'mechanism': None}]
    _cp, errors = _assemble(overlap)
    assert any('互斥' in e for e in errors), errors

    # schema 闭集：成员不在义务闭集内（直接构造 checkpoint 才可能越界——
    # wire 序号路径由 assemble 先拦；这里钉 schema 兜底）
    base = _assemble(_wire(series=[{'members': [1, 2], 'mechanism': None}]))[0]
    with pytest.raises(ValidationError, match='闭集'):
        PreserveCheckpoint.model_validate({**base.model_dump(),
                                           'series': [{'members': ['ob01', 'ghost']}]})


# ───────────────────────── evidence_conflicts：只验形状 ─────────────────────────

def test_conflict_shape_is_validated_but_checkpoint_still_publishes():
    """冲突条目：锚点/句段闭集、句段属锚点拍的证据、note 非空；有冲突时
    checkpoint 照常构造（「不发布、回派」是 a3 run_preserve 的编排决定，
    不混入 validator——I-36）。"""
    wire = _wire()
    wire['evidence_conflicts'] = [
        {'anchor': 'b01_01.e1', 'segment': 'e01_01', 'note': '引文与效应矛盾'}]
    checkpoint, errors = _assemble(wire)
    assert errors == [] and checkpoint is not None, errors

    wrong_segment = _wire()
    wrong_segment['evidence_conflicts'] = [
        {'anchor': 'b01_01.e1', 'segment': 'e01_02', 'note': '句段不属该拍'}]
    _cp, errors = _assemble(wrong_segment)
    assert any('不属于锚点' in e for e in errors), errors

    unknown_anchor = _wire()
    unknown_anchor['evidence_conflicts'] = [
        {'anchor': 'b09_99.e1', 'segment': 'e01_01', 'note': 'x'}]
    _cp, errors = _assemble(unknown_anchor)
    assert any('未知锚点' in e for e in errors), errors

    no_note = _wire()
    no_note['evidence_conflicts'] = [
        {'anchor': 'b01_01.e1', 'segment': 'e01_01', 'note': ''}]
    _cp, errors = _assemble(no_note)
    assert any('缺 note' in e for e in errors), errors


# ───────────────────────── a2 案例面（str06_semantic_cases 索引） ─────────────────────────

def test_a2_batch_cases_are_consumed_with_mechanical_faces():
    """CASES_BY_BATCH['R10a-a2'] 在册案例全部有机械面描述（消费入口自洽）；
    C1 的 a2 面（mode 拷贝）与 P1 的 a2 面（跨章锚点合法）由上方用例覆盖。"""
    assert {c.case_id for c in A2_CASES} >= {'C1', 'C3', 'C5', 'P1'}
    assert all(c.mechanical_face for c in A2_CASES if c.mechanical)
    # P1（跨章证据支持同一次揭示）：anchors 引不同章句柄不因跨章拒。
    cross = _wire()
    cross['obligations'][1]['anchors'] = ['b01_01.c1', 'b01_02.c1']
    checkpoint, errors = _assemble(cross)
    assert errors == [] and checkpoint is not None, errors


def test_checkpoint_hash_binds_series_and_anchors():
    """checkpoint_sha256 覆盖 anchors 与 series：任一变化即不同内容 hash
    （同请求身份下历史采用的边界）。"""
    base_cp, _ = _assemble(_wire())
    other, _ = _assemble(_wire(series=[{'members': [1, 2], 'mechanism': None}]))
    assert base_cp.checkpoint_sha256 != other.checkpoint_sha256
    assert isinstance(base_cp, PreserveCheckpoint)


# 真实返修曾误回 canonical anchors 对象；所有 JSON 容器先验形状再查闭集。
def test_malformed_preserve_containers_report_errors_without_mutation():
    from copy import deepcopy

    malformed = [None, [], 'checkpoint', _wire(obligations={}),
                 _wire(obligations=[None]), _wire(obligations=['ob01'])]
    for field, value in [('anchors', [{'handle': 'b01_01.e1'}]),
                         ('anchors', 'b01_01.e1'), ('requires', 1)]:
        wire = _wire()
        wire['obligations'][0][field] = value
        malformed.append(wire)
    malformed.extend([
        _wire(series={}), _wire(series=[[]]),
        _wire(series=[{'members': 1, 'mechanism': None}]),
        _wire(series=[{'members': [1, 2], 'mechanism': {}}]),
        _wire(series=[{'members': [1, 1], 'mechanism': None}]),
        _wire(series=[{'members': [True, 2], 'mechanism': None}]),
        _wire(evidence_conflicts={}), _wire(evidence_conflicts=[[]]),
        _wire(evidence_conflicts=[{'anchor': {}, 'segment': 'e01_01', 'note': 'x'}]),
        _wire(evidence_conflicts=[{'anchor': 'b01_01.e1', 'segment': [], 'note': 'x'}]),
        _wire(evidence_conflicts=[{'anchor': 'b01_01.e1', 'segment': 'e01_01', 'note': {}}]),
    ])
    for wire in malformed:
        before = deepcopy(wire)
        checkpoint, errors = _assemble(wire)
        assert checkpoint is None and errors, wire
        assert wire == before, 'validator 不得偷偷修复输入'


def test_preserve_revision_wire_roundtrips_semantics_without_derived_fields():
    from novel.stages.stage4.grounding import preserve_revision_wire
    from copy import deepcopy

    checkpoint, errors = _assemble(_wire(series=[{'members': [1, 2], 'mechanism': None}]))
    assert not errors
    before = deepcopy(checkpoint.model_dump())
    wire = preserve_revision_wire(checkpoint)
    assert wire['protocol_version'] == 'stage4-preserve-v2'
    assert wire['obligations'][0]['anchors'] == ['b01_01.e1']
    assert wire['series'][0]['members'] == [1, 2]
    assert not any(k in wire for k in ('checkpoint_id', 'checkpoint_sha256', 'provider'))
    assert all(set(o) == {'kind', 'statement', 'anchors', 'must_preserve',
                          'satisfaction', 'requires'} for o in wire['obligations'])
    again, errors = _assemble(wire)
    assert not errors and again == checkpoint
    assert checkpoint.model_dump() == before
