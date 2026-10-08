#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_source —— Stage2 源语义五 owner 引擎的机械反例与 fold 读数
（B5 换轨：旧联合抽取 compile_book／ChapterCompiler 已随 S-05 删除）。

【验证边界】book／chapters 观察 owner 的机械闸、compile（str06.stage2.compile）
的命题两遍解析与人物闭集、source fold 的折叠语义与 e00 只读读数、章前文字
（源引子）e00 证据切分；开发路径源输入（--source-json）形状。绿底座与完整
反例套件在 `tests/test_str06_stage2_publish.py`／`_owners.py`／`_protocol.py`；
本文件保留的是别处没有的最小反例（同拍引用、fold 读数族、e00 读数）。
真实 LLM wire 的形状问题由真书冒烟回答（已授权，独立执行，不在本文件）。
"""
from __future__ import annotations

import pytest

from novel.stages.chain import build_evidence, load_dev_source
from tests.test_str06_stage2_publish import (
    EVIDENCE, _associate_wire, _basis, _book_wire, _chapter_wires, _names_structure,
)
from novel.stages.stage2 import (
    AssociateObservation, BookObservation, ChapterObservation, NamesObservation,
    character_core_view, compile_source_truth,
)

PV = 'stage2-names-v4'   # 名称同一性留证协议
CPV = 'stage2-chapters-v7'     # 章观察（CS-S 1b：v5——死字段收口）


def _outcome(*, book=None, chapters=None, names=None, associate=None):
    """四份观察 → `compile_source_truth` 的最小合成底座（绿可整体覆盖成红）。"""
    ch_wires = chapters if chapters is not None else _chapter_wires()
    return compile_source_truth(
        book=BookObservation.model_validate(
            book if book is not None else _book_wire()),
        chapters={no: ChapterObservation.model_validate(ch_wires[f'ch{no:02d}'])
                  for no in range(1, len(ch_wires) + 1)},
        names=NamesObservation.model_validate({
            'protocol_version': PV,
            'structure': names if names is not None else _names_structure()}),
        associate=AssociateObservation.model_validate(
            associate if associate is not None else _associate_wire()),
        evidence=EVIDENCE)


def _beat(event, refs, **over):
    beat = {'event': event, 'participants': [], 'source_refs': list(refs),
            'scene_boundary': 'new', 'temporal_mode': 'single',
            'effects': [], 'cognition': [], 'loops': []}
    beat.update(over)
    return beat


# ───────────────────────── book 观察owner：闭集与初态 ─────────────────────────

def test_book_observation_rejects_bad_slug_and_unknown_initial_state_target():
    """R-2.1／R-2.9：slug 闭集自洽、初态目标人物必须在书级人物账内。"""
    bad = _book_wire()
    bad['characters']['Fu-Jun'] = bad['characters'].pop('fu_jun')
    _observation, errors = compile_book_observation_of(bad)
    assert any('slug 非法' in e for e in errors)

    ghost = _book_wire()
    ghost['initial_state'] = [
        {'target': {'kind': 'relation', 'subject': 'ghost', 'object': 'yun_niang'},
         'key': 'marital_status', 'value': '夫妻', 'persistence': 'stable',
         'source_refs': ['e01_01'], 'basis': _basis('e01_01')}]
    _observation, errors = compile_book_observation_of(ghost)
    assert any('未知人物' in e for e in errors)


def compile_book_observation_of(wire):
    from novel.stages.stage2 import compile_book_observation
    return compile_book_observation(wire, evidence=EVIDENCE)


def test_from_chapter_derivation_uses_resolver_segments():
    """D12/I-15：from_chapter 由 Resolver 按锚点派生；章前锚点→第 1 章、
    晚章锚点→该章；所引段不含字面即精确拒（R-2.10 唯一实现）。"""
    segments = {
        'e01_01': {'chapter': 1, 'text': '夫君走进门来。'},
        'e01_02': {'chapter': 1, 'text': '云娘坐在窗边。'},
        'e00_01': {'chapter': 0, 'pre_chapter': True,
                   'text': '章前文字：夫君坦白借种之约。'},
        'e03_02': {'chapter': 3, 'text': '夫君推门进来。'},
    }
    core, errors = character_core_view(
        BookObservation.model_validate(_book_wire()), evidence=segments)
    assert errors == [] and core is not None
    assert core.characters['fu_jun'].surfaces[0].from_chapter == 1
    # 锚点指向更晚的章（第 3 章才逐字出现）⇒ 派生 3
    wire2 = _book_wire()
    wire2['characters']['fu_jun']['surfaces'][0]['source_refs'] = ['e03_02']
    core2, errors2 = character_core_view(
        BookObservation.model_validate(wire2), evidence=segments)
    assert errors2 == [] and core2.characters['fu_jun'].surfaces[0].from_chapter == 3
    # 红：所引证据段都不含该字面 ⇒ 拒（抽取把锚点指错了地方）
    bad = _book_wire()
    bad['characters']['fu_jun']['surfaces'][0]['source_refs'] = ['e01_02']
    _core, errors3 = character_core_view(
        BookObservation.model_validate(bad), evidence=segments)
    assert any('锚点不含该字面' in e for e in errors3)


# ───────────────────────── compile：命题两遍解析与人物闭集 ─────────────────────────

def test_same_beat_establish_and_reference_is_legal():
    """D5：同拍先引用、后建立（引用条目在建立条目之前）——拍内两遍解析合法。

    CS-S 1b：effect 不再带命题锚，同拍两遍的面在 cognition 内（前条引用、
    后条建立）；单遍会误判悬空，两遍后合法，而跨拍悬空引用保持未决
    （compile 拒发布，D8）。
    """
    chapters = _chapter_wires()
    chapters['ch02'] = {'protocol_version': CPV, 'beats': [
        _beat('证实', ['e02_01'], participants=['yun_niang'],
              effects=[{'target': {'kind': 'relation', 'subject': 'fu_jun',
                                   'object': 'yun_niang'},
                        'key': 'relationship', 'op': 'set', 'value': '所言不虚',
                        'modality': 'actual',
                        'basis': _basis('e02_01')}],
              cognition=[{'proposition': {'ref': 'p_trust'},
                          'holder': 'yun_niang', 'mode': 'suspects',
                          'basis': _basis('e02_01')},
                         {'proposition': {'ref': 'p_trust',
                                          'statement': '夫君的话可信'},
                          'holder': 'yun_niang', 'mode': 'confirms',
                          'basis': _basis('e02_01')}])]}
    outcome = _outcome(chapters=chapters)
    assert outcome.errors == [] and outcome.undecided == [], (outcome.errors,
                                                              outcome.undecided)
    beat = outcome.blueprint.chapters[1].beats[0]
    # 同一命题共用一个正式 ID（引用与建立同拍解析到同一处）
    assert beat.cognition[0].proposition.ref == beat.cognition[1].proposition.ref
    assert beat.cognition[1].proposition.ref.startswith('k')

    # 跨拍悬空引用（本章内没有建立、也无配对）⇒ 未决
    chapters['ch02']['beats'][0]['cognition'][1]['proposition'] = {'ref': 'p_ghost'}
    outcome = _outcome(chapters=chapters)
    assert any('悬空的跨章引用' in item for item in outcome.undecided)


def test_compile_closes_participants_and_holders_against_the_book():
    """R-2.3：participants／cognition holder 的闭集归 compile（章观察只含
    章内句柄，人物账在书级；CS-S 1b 起 effect 不再有 known_by 面）。"""
    chapters = _chapter_wires()
    chapters['ch01']['beats'][0]['participants'] = ['fu_jun', 'ghost']
    outcome = _outcome(chapters=chapters)
    assert any('participants 引用未知人物 ghost' in e for e in outcome.errors)

    chapters = _chapter_wires()
    chapters['ch02']['beats'][0]['cognition'][0]['holder'] = 'ghost'
    outcome = _outcome(chapters=chapters)
    assert any('holder 引用未知人物 ghost' in e for e in outcome.errors)


def test_first_beat_of_book_must_open_a_scene():
    """全书第一个拍必须 new：无可延续场景的 continue 即拒。"""
    chapters = _chapter_wires()
    chapters['ch01']['beats'][0]['scene_boundary'] = 'continue'
    outcome = _outcome(chapters=chapters)
    assert any('没有可延续的场景' in e for e in outcome.errors)


# ───────────────────────── source fold（R-2.17：度量不是门） ─────────────────────────

def test_fold_scene_scope_expires_but_book_scope_persists():
    """scene 作用域状态换场失效；book 作用域延续（D16 叠加原语）。"""
    chapters = _chapter_wires()
    chapters['ch02'] = {'protocol_version': CPV, 'beats': [
        _beat('场景一', ['e02_01'],
              effects=[{'target': {'kind': 'character', 'char_id': 'fu_jun'},
                        'key': 'scene_location', 'op': 'set', 'value': '正厅',
                        'modality': 'actual',
                        'basis': _basis('e02_01')}]),
        _beat('场景二', ['e02_01'],
              effects=[{'target': {'kind': 'character', 'char_id': 'fu_jun'},
                        'key': 'residence', 'op': 'set', 'value': '陆家',
                        'modality': 'actual',
                        'basis': _basis('e02_01')}]),
    ]}
    outcome = _outcome(chapters=chapters)
    assert outcome.errors == [] and outcome.undecided == []
    after = '\n'.join(outcome.fold['snapshots'][2])
    assert "scene_location = '正厅'" not in after        # 换场失效
    assert "residence = '陆家'" in after                 # book 延续


def test_fold_reports_stable_rewrite_and_loop_lifecycle():
    """stable 初态被改写、open/close 的悬念进退快照——fold 事件如实报告。"""
    book = _book_wire()
    book['initial_state'] = [
        {'target': {'kind': 'relation', 'subject': 'fu_jun', 'object': 'yun_niang'},
         'key': 'marital_status', 'value': '夫妻', 'persistence': 'stable',
         'source_refs': ['e01_01'], 'basis': _basis('e01_01')}]
    chapters = _chapter_wires()
    chapters['ch02'] = {'protocol_version': CPV, 'beats': [
        _beat('改写婚约', ['e02_01'],
              effects=[{'target': {'kind': 'relation', 'subject': 'fu_jun',
                                   'object': 'yun_niang'},
                        'key': 'marital_status', 'op': 'set', 'value': '破裂',
                        'modality': 'actual',
                        'basis': _basis('e02_01')}],
              loops=[{'ref': 'mystery', 'op': 'open', 'question': '谁在暗中窥伺'}])]}
    chapters['ch03']['beats'] = [
        _beat('收束悬念', ['e03_01'],
              loops=[{'ref': 'mystery', 'op': 'close', 'resolution': '是管家'}])]
    # 跨章 close 走 associate 配对（D8）：ch02.mystery ↔ ch03.mystery。
    associate = _unpaired_associate()
    associate['loop_pairing'] = [{'open': 'ch02.mystery', 'close': 'ch03.mystery',
                               'verdict': 'pair'}]
    outcome = _outcome(book=book, chapters=chapters, associate=associate)
    assert outcome.errors == [] and outcome.undecided == []
    assert any('stable 事实被改写' in e for e in outcome.fold['events'])
    assert any('谁在暗中窥伺' in line for line in outcome.fold['snapshots'][2])
    assert not any('谁在暗中窥伺' in line for line in outcome.fold['snapshots'][3])


def test_fold_counts_empty_transition_beats_per_chapter():
    """D7：三类转移全空的拍合法，但按章报出比例（只读读数）。"""
    chapters = _chapter_wires()
    chapters['ch02'] = {'protocol_version': CPV, 'beats': [
        _beat('有转移', ['e02_01'],
              effects=[{'target': {'kind': 'character', 'char_id': 'fu_jun'},
                        'key': 'emotional_state', 'op': 'set', 'value': '沉',
                        'modality': 'actual',
                        'basis': _basis('e02_01')}]),
        _beat('空转移一', ['e02_01'], scene_boundary='continue'),
        _beat('空转移二', ['e02_01'], scene_boundary='continue'),
    ]}
    outcome = _outcome(chapters=chapters)
    stats = outcome.fold['chapters']['2']
    assert stats == {'beats': 3, 'empty_transition_beats': 2, 'empty_ratio': 0.667}
    assert outcome.fold['chapters']['1']['empty_transition_beats'] == 0


def test_fold_keeps_unset_and_unknown_distinct_from_never_established():
    """unset（解除）、unknown（显式未知）与未建立三者分开（I-42）。"""
    chapters = _chapter_wires()
    chapters['ch02'] = {'protocol_version': CPV, 'beats': [
        _beat('立', ['e02_01'],
              effects=[{'target': {'kind': 'character', 'char_id': 'fu_jun'},
                        'key': 'health', 'op': 'set', 'value': '风寒',
                        'modality': 'actual',
                        'basis': _basis('e02_01')}]),
        _beat('解', ['e02_01'], scene_boundary='continue',
              effects=[{'target': {'kind': 'character', 'char_id': 'fu_jun'},
                        'key': 'health', 'op': 'unset', 'value': None,
                                                'modality': 'actual',
                                                'basis': _basis('e02_01')}]),
        _beat('未知', ['e02_01'], scene_boundary='continue',
              effects=[{'target': {'kind': 'character', 'char_id': 'fu_jun'},
                        'key': 'scene_location', 'op': 'unknown', 'value': None,
                                                'modality': 'actual',
                                                'basis': _basis('e02_01')}]),
    ]}
    outcome = _outcome(chapters=chapters)
    assert outcome.errors == [] and outcome.undecided == []
    after = '\n'.join(outcome.fold['snapshots'][2])
    assert 'char:fu_jun.health = unset' in after
    assert 'char:fu_jun.scene_location = unknown' in after


# ───────────────────────── e00（章前文字）fold 读数：只报告，不阻断 ─────────────────────────

def _e00_book() -> dict:
    """初态引自 e00 证据段的书级观察（婚约在故事开始前已成立）。"""
    book = _book_wire()
    book['initial_state'] = [
        {'target': {'kind': 'relation', 'subject': 'fu_jun', 'object': 'yun_niang'},
         'key': 'marital_status', 'value': '夫妻', 'persistence': 'stable',
         'source_refs': ['e00_01'], 'basis': _basis('e00_01')}]
    return book


def _unpaired_associate() -> dict:
    """空配对的 associate 观察：引用句柄的自定义章 wire 不带默认跨章配对。"""
    associate = _associate_wire()
    for key in ('same_proposition', 'loop_pairing',
                'cross_chapter_cause', 'scene_continuation'):
        associate[key] = []
    return associate


def _e00_chapters(opening: dict) -> dict:
    """ch01 换成开场拍；ch03 去掉引用 ch01 句柄的收束/确认拍。"""
    chapters = _chapter_wires()
    chapters['ch01']['beats'] = [opening]
    chapters['ch03']['beats'] = [_beat('后续推进', ['e03_01'])]
    return chapters


def test_fold_reads_opening_beat_reestablishing_initial_state():
    """读数一：引自 e00 的开场拍对初态同键 set 同值 → 报告，不阻断。"""
    opening = _beat('开场重述', ['e00_01'], participants=['fu_jun'],
                    effects=[{'target': {'kind': 'relation', 'subject': 'fu_jun',
                                         'object': 'yun_niang'},
                              'key': 'marital_status', 'op': 'set', 'value': '夫妻',
                                    'modality': 'actual',
                                    'basis': _basis('e00_01')}])
    outcome = _outcome(book=_e00_book(), chapters=_e00_chapters(opening),
                       associate=_unpaired_associate())
    assert outcome.errors == [] and outcome.undecided == []
    assert any('同键 set 同值' in e for e in outcome.fold['events']), outcome.fold['events']
    # canonical 折叠本身不受影响（章末快照仍含该键）
    assert any('marital_status' in line for line in outcome.fold['snapshots'][1])
    # 绿：同一拍不引 e00 ⇒ 无此读数
    quiet = _beat('正常推进', ['e01_01'], participants=['fu_jun'],
                  effects=[{'target': {'kind': 'relation', 'subject': 'fu_jun',
                                       'object': 'yun_niang'},
                            'key': 'marital_status', 'op': 'set', 'value': '夫妻',
                                'modality': 'actual',
                                'basis': _basis('e01_01')}])
    outcome = _outcome(book=_e00_book(), chapters=_e00_chapters(quiet))
    assert not any('同键 set 同值' in e for e in outcome.fold['events'])


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


# ───────────────────────── schema 层的形状兜底 ─────────────────────────

def test_effect_dead_fields_are_refused_at_the_schema_layer():
    """schema 层拒直接构造的死字段（CS-S 1b：scope／disclosure／proposition
    从 Effect 删除——旧形状按 extra_forbidden 关闭式拒收）。"""
    from contracts.schema.source import Effect
    from pydantic import ValidationError
    base = {'target': {'kind': 'character', 'char_id': 'fu_jun'},
            'key': 'secret', 'op': 'set', 'value': 'x',
            'modality': 'actual',
            'basis': _basis('e01_01')}
    Effect.model_validate(base)
    for dead in ({'scope': 'scene'},
                 {'disclosure': {'visibility': 'on_page'}},
                 {'proposition': {'ref': 'p1'}}):
        with pytest.raises(ValidationError):
            Effect.model_validate({**base, **dead})


def test_loop_question_text_cannot_be_rewritten_at_close():
    """close 只许 resolution：借机改写问题文本在 schema 层即拒。"""
    from contracts.schema.source import LoopAction
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        LoopAction.model_validate({'ref': 'loop01', 'op': 'close',
                                   'resolution': 'x', 'question': '改写'})


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


def test_first_appearance_gate_flags_only_pre_anchor_chapters():
    """R8c 首现闸：判出「参与者早于最早称谓锚」的章；锚点后合法出场不误报
    （canary：daughter 锚 ch10 ⇒ 只判 ch09，不判 ch10/11）；无锚不判。"""
    from types import SimpleNamespace

    from novel.stages.stage2.compile import first_appearance_anchors, first_appearance_issues

    def surface(ch):
        return SimpleNamespace(from_chapter=ch)

    def beat(participants=(), holder=None):
        cognition = (SimpleNamespace(holder=holder),) if holder else ()
        return SimpleNamespace(participants=tuple(participants), cognition=cognition)

    def chapter(no, beats):
        return SimpleNamespace(chapter=no, beats=beats)

    characters = SimpleNamespace(characters={
        'daughter': SimpleNamespace(surfaces=[surface(10)]),
        'yun_niang': SimpleNamespace(surfaces=[surface(1)]),
        'no_anchor': SimpleNamespace(surfaces=[]),
    })
    blueprint = SimpleNamespace(chapters=[
        chapter(9, [beat(participants=('daughter', 'yun_niang'))]),
        chapter(10, [beat(participants=('daughter',), holder='daughter')]),
        chapter(11, [beat(participants=('daughter', 'no_anchor'))]),
    ])
    anchors = first_appearance_anchors(characters)
    issues = [issue for ch in blueprint.chapters
              for issue in first_appearance_issues(anchors, chapter=ch.chapter, beats=ch.beats)]
    assert issues == [
        'participant_before_first_appearance[daughter@ch09]：'
        'daughter 在第 9 章作为参与者/认知持有者出场，但其最早称谓锚点在'
        '第 10 章——首现早于任何称谓证据（QP-01 同类：观察把既有人物误绑到'
        '晚出场角色）；恢复走该章观察重问或 owner patch'], issues
