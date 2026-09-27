#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_source —— Source Truth v4/v13 compile 与 source fold 的合成反例（F2b；v13 质量改进批次二）。

【验证边界】确定性 compile 机械臂的正反例（wire dict → canonical）与折叠语义；
章前文字（源引子）e00 证据切分与两条 fold 读数；v13 三类转移（effects／cognition／
loops）、场景分段、登记键作用域、命题与 loop 引用闭合；真实 LLM wire 的形状问题由
真书冒烟回答（已授权，独立执行，不在本文件）。
"""
from __future__ import annotations

import pytest

from contracts.schema.source import (
    BlueprintsArtifactV13, CharactersArtifactV4, CharactersCore,
)
from novel.stages.chain import build_evidence, load_dev_source
from novel.stages.stage2.source import (
    ChapterCompiler, assemble_blueprint, compile_book, source_fold,
)

EVID = ('e01_01', 'e01_02', 'e02_01', 'e03_01')


def _book_wire() -> dict:
    return {
        'characters': {
            'fu_jun': {
                'canonical': '夫君',
                'surfaces': [
                    {'surface': '夫君', 'from_chapter': 1, 'until_chapter': None,
                     'source_refs': ['e01_01']},
                ],
            },
            'yun_niang': {
                'canonical': '云娘',
                'surfaces': [
                    {'surface': '云娘', 'from_chapter': 1, 'until_chapter': None,
                     'source_refs': ['e01_02']},
                ],
            },
        },
        'initial_state': [
            {'state_id': 's0_marriage', 'target': {'kind': 'relation',
                                                   'subject': 'fu_jun', 'object': 'yun_niang'},
             'key': 'marital_status', 'value': '夫妻', 'persistence': 'stable',
             'knowledge': None, 'source_refs': ['e01_01']},
        ],
        'world_constraints': [
            {'constraint_id': 'wc_era', 'kind': 'setting_constant',
             'statement': '故事发生在古代江南', 'source_refs': ['e01_01']},
        ],
    }


def _effect(target: dict, key: str, value, *, op: str = 'set',
            scope: str = 'book', visibility: str = 'on_page',
            proposition: dict | None = None) -> dict:
    eff = {'target': target, 'key': key, 'op': op, 'value': value, 'scope': scope,
           'disclosure': {'visibility': visibility}}
    if visibility == 'concealed':
        eff['disclosure']['known_by'] = ['fu_jun']
        eff['proposition'] = proposition or {
            'ref': f'p_{key}', 'statement': f'{key} 的隐藏事实'}
    elif proposition is not None:
        eff['proposition'] = proposition
    return eff


def _beat(beat_id: str, event: str, *, participants=(), source_refs=('e02_01',),
          effects=(), cognition=(), loops=(), boundary: str = 'new',
          temporal: str = 'single') -> dict:
    return {'beat_id': beat_id, 'event': event, 'participants': list(participants),
            'source_refs': list(source_refs), 'scene_boundary': boundary,
            'temporal_mode': temporal, 'effects': list(effects),
            'cognition': list(cognition), 'loops': list(loops)}


def _chapter_wire(ch: int = 2) -> dict:
    return {
        'beats': [
            _beat(f'b{ch:02d}_01', '云娘向夫君试探',
                  participants=['yun_niang', 'fu_jun'],
                  effects=[_effect({'kind': 'character', 'char_id': 'yun_niang'},
                                   'wariness', '高')],
                  cognition=[{'proposition': {'ref': 'p_secret',
                                              'statement': '借种之约是夫妻共同秘密'},
                              'holder': 'yun_niang', 'mode': 'learns'}]),
        ],
        'causal_edges': [],
    }


def _compile_pair():
    chars, wcs, errors = compile_book(_book_wire(), evidence_ids=EVID)
    assert errors == [] and chars is not None and wcs is not None
    compiler = ChapterCompiler(chars, evidence_ids=EVID)
    ch1, errs = compiler.compile({
        'beats': [_beat('b01_01', '开篇', participants=['fu_jun'],
                        source_refs=('e01_01',))],
        'causal_edges': []}, 1)
    assert ch1 is not None and errs == []
    return chars, wcs, compiler, ch1


# ───────────────────────── compile_book ─────────────────────────

def test_compile_book_splits_truths():
    chars, wcs, errors = compile_book(_book_wire(), evidence_ids=EVID)
    assert errors == []
    assert isinstance(chars, CharactersCore)
    assert set(chars.characters) == {'fu_jun', 'yun_niang'}
    assert wcs and wcs[0]['constraint_id'] == 'wc_era'


def test_compile_book_rejects_dangling_evidence():
    bad = _book_wire()
    bad['characters']['fu_jun']['surfaces'][0]['source_refs'] = ['e99_99']
    chars, wcs, errors = compile_book(bad, evidence_ids=EVID)
    assert chars is None and any('悬空' in e for e in errors)


def test_compile_book_rejects_bad_slug():
    bad = _book_wire()
    bad['characters']['Fu-Jun'] = bad['characters'].pop('fu_jun')
    _, _, errors = compile_book(bad, evidence_ids=EVID)
    assert any('slug 非法' in e for e in errors)


def test_compile_book_rejects_unknown_initial_state_ref():
    bad = _book_wire()
    bad['initial_state'][0]['target']['subject'] = 'ghost'
    _, _, errors = compile_book(bad, evidence_ids=EVID)
    assert any('未知人物' in e for e in errors)


def test_compile_book_requires_canonical_to_be_a_registered_surface():
    """称谓闭合（A3）：canonical 逐字等于某个 surface 才放行。"""
    chars, _, errors = compile_book(_book_wire(), evidence_ids=EVID)
    assert errors == [] and chars is not None
    assert chars.characters['fu_jun'].canonical == '夫君'

    bad = _book_wire()
    bad['characters']['fu_jun']['canonical'] = '陆夫君'   # 未登记字面
    _, _, errors = compile_book(bad, evidence_ids=EVID)
    assert any('canonical' in e and 'surface' in e for e in errors)

    ok = _book_wire()
    ok['characters']['fu_jun']['surfaces'].append(
        {'surface': '陆大哥', 'from_chapter': 2, 'until_chapter': None,
         'source_refs': ['e01_01']})
    ok['characters']['fu_jun']['canonical'] = '陆大哥'
    chars2, _, errors = compile_book(ok, evidence_ids=EVID)
    assert errors == [] and chars2.characters['fu_jun'].canonical == '陆大哥'


def test_compile_book_rejects_scene_scoped_key_in_initial_state():
    """登记键闭集（D8）：scene_location 是场景作用域，不进 initial_state。"""
    bad = _book_wire()
    bad['initial_state'].append({
        'state_id': 's0_where', 'target': {'kind': 'character', 'char_id': 'yun_niang'},
        'key': 'scene_location', 'value': '陆家卧房', 'persistence': 'mutable',
        'knowledge': None, 'source_refs': ['e01_02']})
    _, _, errors = compile_book(bad, evidence_ids=EVID)
    assert any('场景作用域' in e for e in errors)


def test_from_chapter_is_derived_from_the_evidence_anchor():
    """D12/I-15：from_chapter 由 compiler 按锚点派生并覆盖声明值；章前锚点→第 1 章。"""
    wire = _book_wire()
    # 章前文字（e00）里首次出现的称呼：声明 0 也会被派生覆盖成 1
    wire['characters']['fu_jun']['surfaces'][0]['from_chapter'] = 0
    segments = {
        'e01_01': {'chapter': 1, 'text': '夫君走进门来。'},
        'e01_02': {'chapter': 1, 'text': '云娘坐在窗边。'},
        'e00_01': {'chapter': 0, 'text': '章前文字：夫君坦白借种之约。'},
        'e02_01': {'chapter': 2, 'text': '第二夜窗外有响动。'},
        'e03_01': {'chapter': 3, 'text': '第三夜芸娘惊醒。'},
        'e03_02': {'chapter': 3, 'text': '夫君推门进来。'},
    }
    chars, _, errors = compile_book(wire, evidence_ids=segments)
    assert errors == [] and chars is not None
    assert chars.characters['fu_jun'].surfaces[0].from_chapter == 1
    # 锚点指向更晚的章（第 3 章才逐字出现）⇒ 派生 3，覆盖声明的 1
    wire2 = _book_wire()
    wire2['characters']['fu_jun']['surfaces'][0]['source_refs'] = ['e03_02']
    chars2, _, errors2 = compile_book(wire2, evidence_ids=segments)
    assert errors2 == [] and chars2 is not None
    assert chars2.characters['fu_jun'].surfaces[0].from_chapter == 3
    # 红：所引证据段都不含该字面 ⇒ 拒（抽取把锚点指错了地方）
    bad = _book_wire()
    bad['characters']['fu_jun']['surfaces'][0]['source_refs'] = ['e02_01']
    _, _, errors3 = compile_book(bad, evidence_ids=segments)
    assert any('锚点不含该字面' in e for e in errors3)


# ───────────────────────── ChapterCompiler：形状与闭集 ─────────────────────────

def test_chapter_compiler_green_and_proposition_registry():
    chars, wcs, compiler, ch1 = _compile_pair()
    ch2, errs = compiler.compile(_chapter_wire(), 2)
    assert ch2 is not None and errs == []
    # 新建命题由 compiler 派正式 ID；canonical 只存正式 ID 与建立处文本
    assert compiler.proposition_registry() == {'k01': '借种之约是夫妻共同秘密'}
    assert ch2.beats[0].cognition[0].proposition.ref == 'k01'
    assert ch2.beats[0].cognition[0].proposition.statement == '借种之约是夫妻共同秘密'
    # 跨章引用正式 ID、不重抄文本 → 绿
    ch3, errs = compiler.compile({
        'beats': [_beat('b03_01', '回忆', participants=['fu_jun'],
                        source_refs=('e03_01',),
                        cognition=[{'proposition': {'ref': 'k01'},
                                    'holder': 'fu_jun', 'mode': 'confirms'}])],
        'causal_edges': []}, 3)
    assert ch3 is not None and errs == []
    assert ch3.beats[0].cognition[0].proposition.statement is None
    # 复用正式 ID 但重抄不同文本 → 拒
    _, errs = compiler.compile({
        'beats': [_beat('b03_01', '回忆', participants=['fu_jun'],
                        source_refs=('e03_01',),
                        cognition=[{'proposition': {'ref': 'k01', 'statement': '另一个说法'},
                                    'holder': 'fu_jun', 'mode': 'confirms'}])],
        'causal_edges': []}, 3)
    assert any('重抄的文本与登记不一致' in e for e in errs)


def test_chapter_compiler_rejects_dangling_proposition_and_duplicate_label():
    chars, _, compiler, _ = _compile_pair()
    _, errs = compiler.compile({
        'beats': [_beat('b02_01', 'a',
                        cognition=[{'proposition': {'ref': 'k99'},
                                    'holder': 'fu_jun', 'mode': 'learns'}])],
        'causal_edges': []}, 2)
    assert any('引用未登记命题' in e for e in errs)

    wire = _chapter_wire()
    wire['beats'].append(_beat('b02_02', 'b',
                               cognition=[{'proposition': {'ref': 'p_secret',
                                                          'statement': '同一标签两次新建'},
                                           'holder': 'fu_jun', 'mode': 'learns'}]))
    _, errs = compiler.compile(wire, 2)
    assert any('重抄的文本与登记不一致' in e for e in errs)


def test_chapter_compiler_rejects_unknown_participant_and_dual_owner():
    chars, _, compiler, _ = _compile_pair()
    bad = _chapter_wire()
    bad['beats'][0]['participants'].append('ghost')
    _, errs = compiler.compile(bad, 2)
    assert any('未知人物' in e for e in errs)

    dual = {
        'beats': [
            _beat('b02_01', '变心', participants=['fu_jun'],
                  effects=[
                      {'target': {'kind': 'character', 'char_id': 'fu_jun'},
                       'key': 'affection_toward_yun', 'op': 'set', 'value': '淡',
                       'scope': 'book', 'disclosure': {'visibility': 'on_page'}},
                      {'target': {'kind': 'relation', 'subject': 'fu_jun',
                                  'object': 'yun_niang'},
                       'key': 'estranged', 'op': 'set', 'value': True,
                       'scope': 'book', 'disclosure': {'visibility': 'on_page'}},
                  ]),
        ],
        'causal_edges': []}
    _, errs = compiler.compile(dual, 2)
    assert any('双 owner' in e for e in errs)


def test_same_beat_establish_and_reference_is_legal():
    """D5：证实拍建事实效应并引用同一拍 cognition 新建的命题——拍内两遍解析。

    真书实证（v13 首跑 ch02 b02_09）：模型把命题文本建在证实拍的 cognition 上，
    同拍 effect 引用它。单遍（effect 先于 cognition）会误判悬空；两遍后合法，
    而跨拍悬空引用仍拒（下一条）。
    """
    chars, _, compiler, _ = _compile_pair()
    ch2, errs = compiler.compile({
        'beats': [
            _beat('b02_01', '证实',
                  effects=[_effect({'kind': 'relation', 'subject': 'fu_jun',
                                    'object': 'yun_niang'},
                                   'intimacy', '所言不虚',
                                   proposition={'ref': 'p1'}),
                           ],
                  cognition=[{'proposition': {'ref': 'p1',
                                              'statement': '夫君虽不能行房事，但能以其他方式满足'},
                              'holder': 'yun_niang', 'mode': 'confirms'}]),
        ],
        'causal_edges': []}, 2)
    assert ch2 is not None and errs == []
    assert ch2.beats[0].cognition[0].proposition.ref == 'k01'
    assert ch2.beats[0].effects[0].proposition.ref == 'k01'
    # 跨拍悬空引用仍拒
    _, errs = compiler.compile({
        'beats': [
            _beat('b03_01', '悬空',
                  effects=[_effect({'kind': 'character', 'char_id': 'fu_jun'},
                                   'mood', 'x', proposition={'ref': 'k99'})]),
        ],
        'causal_edges': []}, 3)
    assert any('引用未登记命题' in e for e in errs)


def test_chapter_compiler_rejects_unknown_cognition_holder_and_known_by():
    chars, _, compiler, _ = _compile_pair()
    _, errs = compiler.compile({
        'beats': [_beat('b02_01', 'a',
                        cognition=[{'proposition': {'ref': 'p1', 'statement': 'x'},
                                    'holder': 'ghost', 'mode': 'learns'}])],
        'causal_edges': []}, 2)
    assert any('holder 引用未知人物' in e for e in errs)

    eff = _effect({'kind': 'character', 'char_id': 'fu_jun'}, 'secret', '甲',
                  visibility='concealed')
    eff['disclosure']['known_by'] = ['ghost']
    _, errs = compiler.compile({
        'beats': [_beat('b02_01', 'a', effects=[eff])],
        'causal_edges': []}, 2)
    assert any('known_by 引用未知人物' in e for e in errs)


def test_chapter_compiler_preserves_adjacent_causality():
    chars, _, compiler, _ = _compile_pair()
    wire = {
        'beats': [
            _beat('b02_01', 'a', participants=[]),
            _beat('b02_02', 'b', participants=[], boundary='continue')],
        'causal_edges': [
            {'from_beat': 'b02_01', 'to_beat': 'b02_02', 'kind': 'causes'},   # 相邻因果同样保留
            {'from_beat': 'b01_01', 'to_beat': 'b02_02', 'kind': 'setup_payoff'},  # 跨章：保留
        ]}
    ch2, errs = compiler.compile(wire, 2)
    assert ch2 is not None and errs == []
    assert len(ch2.causal_edges) == 2
    assert ch2.causal_edges[1].from_beat == 'b01_01'


def test_chapter_compiler_rejects_reversed_cross_chapter_edge():
    chars, _, compiler, _ = _compile_pair()
    wire = {
        'beats': [_beat('b02_01', 'a', participants=[])],
        'causal_edges': [
            {'from_beat': 'b02_01', 'to_beat': 'b01_01', 'kind': 'causes'}]}
    _, errs = compiler.compile(wire, 2)
    assert any('方向反了' in e for e in errs)


# ───────────────────────── v13：场景分段 ─────────────────────────

def test_scene_segmentation_new_continue_and_cross_chapter():
    chars, wcs, compiler, ch1 = _compile_pair()
    ch2, errs = compiler.compile({
        'beats': [
            _beat('b02_01', '场景一', boundary='new'),
            _beat('b02_02', '场景一续', boundary='continue'),
            _beat('b02_03', '场景二', boundary='new'),
        ],
        'causal_edges': []}, 2)
    assert ch2 is not None and errs == []
    assert [s.scene_id for s in ch2.scenes] == ['sc02', 'sc03']
    assert ch2.scenes[0].beat_ids == ('b02_01', 'b02_02')
    assert ch2.scenes[1].beat_ids == ('b02_03',)
    # 跨章延续：第 3 章第一个拍 continue 第 2 章的开放场景
    ch3, errs = compiler.compile({
        'beats': [
            _beat('b03_01', '场景二跨章续', boundary='continue'),
            _beat('b03_02', '新场景', boundary='new'),
        ],
        'causal_edges': []}, 3)
    assert ch3 is not None and errs == []
    assert [s.scene_id for s in ch3.scenes] == ['sc03', 'sc04']


def test_first_beat_of_book_must_open_a_scene():
    chars, _, _compiler, _ = _compile_pair()
    fresh = ChapterCompiler(chars, evidence_ids=EVID)
    _, errs = fresh.compile({
        'beats': [_beat('b01_01', '开头', boundary='continue')],
        'causal_edges': []}, 1)
    assert any('continue' in e and '没有可延续的场景' in e for e in errs)


def test_scene_scope_expires_at_new_boundary():
    """scene 作用域状态换场失效；book 作用域延续（工程验证：场景结束及跨章延续）。"""
    chars, wcs, compiler, ch1 = _compile_pair()
    ch2, errs = compiler.compile({
        'beats': [
            _beat('b02_01', '场景一', boundary='new',
                  effects=[_effect({'kind': 'character', 'char_id': 'fu_jun'},
                                   'scene_location', '正厅', scope='scene')]),
            _beat('b02_02', '场景二', boundary='new',
                  effects=[_effect({'kind': 'character', 'char_id': 'fu_jun'},
                                   'residence', '陆家', scope='book')]),
        ],
        'causal_edges': []}, 2)
    assert ch2 is not None and errs == []
    fold = source_fold(chars, assemble_blueprint(wcs, [ch1, ch2]))
    after = '\n'.join(fold.snapshots[2])
    assert "scene_location = '正厅'" not in after      # 换场失效
    assert "residence = '陆家'" in after               # book 延续


# ───────────────────────── v13：登记键与已退役位置键 ─────────────────────────

def test_registered_key_scope_is_bound_by_the_compiler():
    """登记键作用域由 compiler 绑定（I-15）：模型声明与表不符→归一化，不是拒绝。

    真书实证（v13 首跑 ch04／ch05）：模型系统性把身体状态声明为 scene；作用域是
    契约固定事实，属可推导坐标——compiler 绑定，canonical 恒带表内作用域。
    """
    chars, _, compiler, _ = _compile_pair()
    for key, declared, bound in (('scene_location', 'book', 'scene'),
                                 ('residence', 'scene', 'book'),
                                 ('body_consciousness', 'scene', 'book')):
        ch2, errs = compiler.compile({
            'beats': [_beat('b02_01', 'a',
                            effects=[_effect({'kind': 'character', 'char_id': 'fu_jun'},
                                             key, 'x', scope=declared)])],
            'causal_edges': []}, 2)
        assert ch2 is not None and errs == [], (key, errs)
        assert ch2.beats[0].effects[0].scope == bound


def test_registered_key_scope_is_enforced_at_the_schema_layer():
    """schema 层仍拒直接构造的错作用域（compiler 归一化之前的兜底）。"""
    from contracts.schema.source import Effect
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Effect.model_validate({'target': {'kind': 'character', 'char_id': 'fu_jun'},
                               'key': 'scene_location', 'op': 'set', 'value': 'x',
                               'scope': 'book', 'disclosure': {'visibility': 'on_page'}})


def test_retired_whereabouts_key_is_refused_with_a_pointer():
    chars, _, compiler, _ = _compile_pair()
    _, errs = compiler.compile({
        'beats': [_beat('b02_01', 'a',
                        effects=[_effect({'kind': 'character', 'char_id': 'fu_jun'},
                                         'whereabouts', '陆家卧房内，藏于床底')])],
        'causal_edges': []}, 2)
    assert any('whereabouts' in e and 'scene_location' in e for e in errs)


# ───────────────────────── v13：loops 独立数组 ─────────────────────────

def test_loops_open_once_and_close_with_resolution():
    chars, wcs, compiler, ch1 = _compile_pair()
    ch2, errs = compiler.compile({
        'beats': [_beat('b02_01', '悬念开',
                        loops=[{'ref': 'mystery', 'op': 'open',
                                'question': '谁在暗中窥伺'}])],
        'causal_edges': []}, 2)
    assert ch2 is not None and errs == []
    assert ch2.beats[0].loops[0].ref == 'loop01'
    assert compiler.open_loop_registry() == {'loop01': '谁在暗中窥伺'}
    # 重复 open（标签或正式 ID）→ 拒
    _, errs = compiler.compile({
        'beats': [_beat('b03_01', '再开',
                        loops=[{'ref': 'loop01', 'op': 'open',
                                'question': '改写后的问题'}])],
        'causal_edges': []}, 3)
    assert any('重复 open' in e for e in errs)
    # close 未开放 / 未知 → 拒
    _, errs = compiler.compile({
        'beats': [_beat('b03_01', '关错',
                        loops=[{'ref': 'loop99', 'op': 'close',
                                'resolution': '收束'}])],
        'causal_edges': []}, 3)
    assert any('未登记的悬念' in e for e in errs)
    # 绿：跨章引用正式 ID close，带 resolution
    ch3, errs = compiler.compile({
        'beats': [_beat('b03_01', '收束',
                        loops=[{'ref': 'loop01', 'op': 'close',
                                'resolution': '是管家在窥伺'}])],
        'causal_edges': []}, 3)
    assert ch3 is not None and errs == []
    assert compiler.open_loop_registry() == {}


def test_loop_question_text_cannot_be_rewritten_at_close():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        LoopClose = {'ref': 'loop01', 'op': 'close', 'resolution': 'x', 'question': '改写'}
        from contracts.schema.source import LoopAction
        LoopAction.model_validate(LoopClose)


# ───────────────────────── assemble + source_fold ─────────────────────────

def test_assemble_and_fold_semantics():
    chars, wcs, compiler, ch1 = _compile_pair()
    ch2, _ = compiler.compile(_chapter_wire(), 2)
    bp = assemble_blueprint(wcs, [ch1, ch2])
    assert isinstance(bp, BlueprintsArtifactV13)
    assert bp.chapters[0].chapter == 1 and bp.chapters[1].chapter == 2

    fold = source_fold(chars, bp)
    # 章起点快照含 initial_state（stable 婚姻），第 2 章后含 wariness（逐条带来源）
    assert any('marital_status' in line and 's0_marriage 建立' in line
               for line in fold.snapshots[2])
    assert any('wariness' in line and 'b02_01 建立' in line for line in fold.snapshots[2])
    assert not any('wariness' in line for line in fold.snapshots[1])
    # 认知层：命题＋持有者＋模式进快照（D11 标识登记）
    assert any('prop k01 知情: yun_niang=learns(b02_01)' in line
               for line in fold.snapshots[2])


def test_fold_reports_stable_rewrite_and_loop_lifecycle():
    chars, wcs, compiler, ch1 = _compile_pair()
    ch2, errs = compiler.compile({
        'beats': [_beat('b02_01', 'x', participants=[],
                        effects=[_effect({'kind': 'relation', 'subject': 'fu_jun',
                                          'object': 'yun_niang'},
                                         'marital_status', '破裂'),
                                 ],
                        loops=[{'ref': 'mystery', 'op': 'open',
                                'question': '谁在暗中窥伺'}])],
        'causal_edges': []}, 2)
    assert ch2 is not None and errs == []
    ch3, errs = compiler.compile({
        'beats': [_beat('b03_01', 'y', participants=[],
                        loops=[{'ref': 'loop01', 'op': 'close',
                                'resolution': '是管家'}])],
        'causal_edges': []}, 3)
    assert ch3 is not None and errs == []
    fold = source_fold(chars, assemble_blueprint(wcs, [ch1, ch2, ch3]))
    assert any('stable 事实被改写' in e for e in fold.events)
    # open 后快照含该 loop；close 后不再含
    assert any('loop loop01' in line for line in fold.snapshots[2])
    assert not any('loop loop01' in line for line in fold.snapshots[3])


def test_fold_counts_empty_transition_beats_per_chapter():
    """D7：三类转移全空的拍合法，但按章报出比例（只读读数）。"""
    chars, wcs, compiler, ch1 = _compile_pair()
    ch2, errs = compiler.compile({
        'beats': [
            _beat('b02_01', '有转移',
                  effects=[_effect({'kind': 'character', 'char_id': 'fu_jun'},
                                   'mood', '沉')]),
            _beat('b02_02', '空转移一', boundary='continue'),
            _beat('b02_03', '空转移二', boundary='continue'),
        ],
        'causal_edges': []}, 2)
    assert ch2 is not None and errs == []
    fold = source_fold(chars, assemble_blueprint(wcs, [ch1, ch2]))
    assert fold.chapter_stats[2] == {'beats': 3, 'empty_transition_beats': 2,
                                     'empty_ratio': 0.667}
    assert fold.chapter_stats[1]['empty_transition_beats'] == 1


def test_fold_reports_concealed_unrevealed_at_book_end():
    """concealed 到书末仍无读者揭示 → 只读报告；reader 的 learns 构成确认。"""
    chars, wcs, compiler, ch1 = _compile_pair()
    hidden = _effect({'kind': 'character', 'char_id': 'fu_jun'}, 'scene_location',
                     '床底', scope='scene', visibility='concealed')
    ch2, errs = compiler.compile({
        'beats': [_beat('b02_01', '藏身', effects=[hidden])],
        'causal_edges': []}, 2)
    assert ch2 is not None and errs == []
    fold = source_fold(chars, assemble_blueprint(wcs, [ch1, ch2]))
    assert [c['proposition'] for c in fold.concealed_unrevealed] == ['k01']
    assert fold.concealed_unrevealed[0]['known_by'] == ['fu_jun']
    # 读者在后续章 learns 同一命题 → 不再进未揭示报告
    ch3, errs = compiler.compile({
        'beats': [_beat('b03_01', '现身',
                        cognition=[{'proposition': {'ref': 'k01'},
                                    'holder': 'reader', 'mode': 'learns'}])],
        'causal_edges': []}, 3)
    assert ch3 is not None and errs == []
    fold2 = source_fold(chars, assemble_blueprint(wcs, [ch1, ch2, ch3]))
    assert fold2.concealed_unrevealed == []


def test_fold_keeps_unset_and_unknown_distinct_from_never_established():
    """unset（解除）、unknown（显式未知）与未建立三者分开（I-42）。"""
    chars, wcs, compiler, ch1 = _compile_pair()
    ch2, errs = compiler.compile({
        'beats': [
            _beat('b02_01', '立',
                  effects=[_effect({'kind': 'character', 'char_id': 'fu_jun'},
                                   'illness', '风寒')]),
            _beat('b02_02', '解', boundary='continue',
                  effects=[{'target': {'kind': 'character', 'char_id': 'fu_jun'},
                            'key': 'illness', 'op': 'unset', 'value': None,
                            'scope': 'book', 'disclosure': {'visibility': 'on_page'}}]),
            _beat('b02_03', '未知', boundary='continue',
                  effects=[{'target': {'kind': 'character', 'char_id': 'fu_jun'},
                            'key': 'scene_location', 'op': 'unknown', 'value': None,
                            'scope': 'scene', 'disclosure': {'visibility': 'on_page'}}]),
        ],
        'causal_edges': []}, 2)
    assert ch2 is not None and errs == []
    fold = source_fold(chars, assemble_blueprint(wcs, [ch1, ch2]))
    after = '\n'.join(fold.snapshots[2])
    assert 'char:fu_jun.illness = unset' in after
    assert 'char:fu_jun.scene_location = unknown' in after


# ───────────────────────── 章前文字（源引子）e00 证据切分（C 批） ─────────────────────────

_CH01 = {'chapter_ordinal': 1, 'text': '云娘嫁入陆家。' * 40}
_CH02 = {'chapter_ordinal': 2, 'text': '第二夜，窗外有响动。' * 40}


def test_build_evidence_splits_hook_copy_into_e00_segments():
    """引子切 e00_* 并标 pre_chapter；无引子时一个 e00 都不产。"""
    hook = '新婚夜，夫君坦白借种之约。' * 30
    evidence = build_evidence([_CH01, _CH02], hook)
    pre = {k: v for k, v in evidence.items() if k.startswith('e00_')}
    assert pre and all(v['chapter'] == 0 and v['pre_chapter'] for v in pre.values())
    assert sorted(pre) == [f'e00_{i:02d}' for i in range(1, len(pre) + 1)]
    plain = build_evidence([_CH01, _CH02])
    assert {k: v for k, v in evidence.items() if not k.startswith('e00_')} == plain

    for empty in (None, '', '   \n'):
        assert not [k for k in build_evidence([_CH01], empty) if k.startswith('e00_')]


def test_pre_chapter_evidence_only_reaches_book_and_first_chapter():
    """e00 只进书级调用与第 1 章观察的证据面；其余各章不变（B2 换轨到五节点）。"""
    evidence = build_evidence([_CH01, _CH02], '引子第一句。引子第二句。')
    pre = {k for k, v in evidence.items() if v.get('pre_chapter')}
    assert pre and all(k.startswith('e00_') for k in pre)
    for no in (1, 2):
        visible = [k for k, v in evidence.items()
                   if v['chapter'] == no or (no == 1 and v.get('pre_chapter'))]
        if no == 1:
            assert set(pre) <= set(visible)
        else:
            assert not (set(visible) & pre)


# ───────────────────────── source_fold 章前文字读数（只报告，不阻断） ─────────────────────────

#: 含章前证据段的证据面（e00 读数反例专用）
EVID_PRE = EVID + ('e00_01',)


def _fold_pair():
    chars, wcs, errors = compile_book(_book_wire(), evidence_ids=EVID_PRE)
    assert not errors and chars is not None
    compiler = ChapterCompiler(chars, evidence_ids=EVID_PRE)
    ch1, errs = compiler.compile({
        'beats': [_beat('b01_01', '开篇', participants=['fu_jun'],
                        source_refs=('e01_01',))],
        'causal_edges': []}, 1)
    assert ch1 is not None and not errs
    return chars, wcs, compiler, ch1


def test_fold_reads_opening_beat_reestablishing_initial_state():
    """读数一：引自 e00 的拍对初态同键 set 同值 → 报告，不阻断。"""
    chars, wcs, compiler, ch1 = _fold_pair()
    wire = {
        'beats': [
            _beat('b02_01', '开场重述', participants=['fu_jun'],
                  source_refs=('e00_01',),
                  effects=[_effect({'kind': 'relation', 'subject': 'fu_jun',
                                    'object': 'yun_niang'},
                                   'marital_status', '夫妻')]),
        ],
        'causal_edges': []}
    ch2, errs = compiler.compile(wire, 2)
    assert ch2 is not None and not errs
    fold = source_fold(chars, assemble_blueprint(wcs, [ch1, ch2]),
                       pre_chapter_evidence=frozenset({'e00_01'}))
    assert any('同键 set 同值' in e for e in fold.events), fold.events
    # 只报告：canonical 折叠本身不受影响（章末快照仍含该键）
    assert any('marital_status' in line for line in fold.snapshots[2])
    # 绿：同一拍不引 e00 ⇒ 无此读数
    quiet = {
        'beats': [
            _beat('b02_01', '正常推进', participants=['fu_jun'],
                  source_refs=('e02_01',),
                  effects=[_effect({'kind': 'relation', 'subject': 'fu_jun',
                                    'object': 'yun_niang'},
                                   'marital_status', '夫妻')]),
        ],
        'causal_edges': []}
    ch2b, errs = compiler.compile(quiet, 2)
    assert ch2b is not None and not errs
    fold2 = source_fold(chars, assemble_blueprint(wcs, [ch1, ch2b]),
                        pre_chapter_evidence=frozenset({'e00_01'}))
    assert not any('同键 set 同值' in e for e in fold2.events)


def test_fold_reads_initial_knowledge_retold_in_opening_beat():
    """读数二：初态已知者又在引用同一 e00 证据段的拍里被告知 → 报告，不阻断。

    QP-07 修复：按**持有者与命题**核对——只有该持有者本就在 initial_state 的
    known_by 里才报告，且同时给出 initial_state 条与命题 ID（是否同一事实留人工
    核读），不再只凭共享证据段下结论。
    """
    wire = _book_wire()
    wire['initial_state'][0]['knowledge'] = {
        'known_by': ['fu_jun', 'yun_niang'], 'reader_knows': True}
    wire['initial_state'][0]['source_refs'] = ['e00_01']
    chars, wcs, errors = compile_book(wire, evidence_ids=EVID_PRE)
    assert not errors and chars is not None
    compiler = ChapterCompiler(chars, evidence_ids=EVID_PRE)
    ch1, errs = compiler.compile({
        'beats': [_beat('b01_01', '开场告知', participants=['fu_jun'],
                        source_refs=('e00_01',),
                        cognition=[{'proposition': {'ref': 'p_marriage',
                                                    'statement': '两人是夫妻'},
                                    'holder': 'fu_jun', 'mode': 'learns'}])],
        'causal_edges': []}, 1)
    assert ch1 is not None and not errs
    fold = source_fold(chars, assemble_blueprint(wcs, [ch1]),
                       pre_chapter_evidence=frozenset({'e00_01'}))
    assert any('初始已知' in e and 's0_marriage' in e and 'k01' in e
               for e in fold.events), fold.events
    # 绿：持有者不在 known_by 里（开场新获知）⇒ 不报告
    ch1b, errs = compiler.compile({
        'beats': [_beat('b01_01', '开场告知', participants=['fu_jun'],
                        source_refs=('e00_01',),
                        cognition=[{'proposition': {'ref': 'p_other',
                                                    'statement': '另一件事'},
                                    'holder': 'shen_zhiyan', 'mode': 'learns'}])],
        'causal_edges': []}, 1)
    assert ch1b is None and errs   # shen_zhiyan 不在人物表：holder 闭集先拦
    # 绿：初态不带 knowledge ⇒ 无此读数（不知道时点约束，不侧写）
    plain_chars, _wcs, _errors = compile_book(_book_wire(), evidence_ids=EVID)
    fold2 = source_fold(plain_chars, assemble_blueprint(wcs, [ch1]),
                        pre_chapter_evidence=frozenset({'e00_01'}))
    assert not any('初始已知' in e for e in fold2.events)


# ───────────────────────── 开发路径源输入（C 批） ─────────────────────────

def test_dev_source_requires_explicit_hook_copy():
    """--source-json 必须显式给出引子或显式声明无引子；裸章数组不再接受。"""
    chapters = [{'chapter_ordinal': 1, 'text': '正文。'}]
    parsed, hook = load_dev_source({'chapters': chapters, 'hook_copy': '引子。'})
    assert parsed == chapters and hook == '引子。'
    parsed, hook = load_dev_source({'chapters': chapters, 'hook_copy': None})
    assert parsed == chapters and hook == ''
    for bad in (chapters, {'chapters': chapters}, {'chapters': [], 'hook_copy': None},
                {'chapters': chapters, 'hook_copy': '', 'extra': 1}):
        with pytest.raises(ValueError):
            load_dev_source(bad)
