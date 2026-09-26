#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_source —— Source Truth v4/v12 compile 与 source fold 的合成反例（F2b）。

【验证边界】确定性 compile 机械臂的正反例（wire dict → canonical）与折叠语义；
真实 LLM wire 的形状问题由 F2b 真书冒烟回答（已授权，独立执行，不在本文件）。
"""
from __future__ import annotations

from contracts.schema.source import (
    BlueprintsArtifactV12, CharactersArtifactV4, CharactersCore,
)
from novel.stages.stage2.source import (
    ChapterCompiler, assemble_blueprint, compile_book, source_fold,
)

EVID = ('e01_01', 'e01_02', 'e02_01', 'e03_01')


def _book_wire() -> dict:
    return {
        'characters': {
            'fu_jun': {
                'canonical': '陆夫君',
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


def _chapter_wire(ch: int = 2) -> dict:
    return {
        'beats': [
            {'beat_id': f'b{ch:02d}_01', 'event': '云娘向夫君试探',
             'participants': ['yun_niang', 'fu_jun'], 'source_refs': ['e02_01'],
             'effects': [
                 {'target': {'kind': 'character', 'char_id': 'yun_niang'},
                  'key': 'wariness', 'op': 'set', 'value': '高'},
             ],
             'reveals': [
                 {'knowledge_key': 'loan_secret', 'statement': '借种之约是夫妻共同秘密',
                  'recipients': ['yun_niang', 'fu_jun'], 'mode': 'learns'},
             ]},
        ],
        'causal_edges': [],
    }


def _compile_pair():
    chars, wcs, errors = compile_book(_book_wire(), evidence_ids=EVID)
    assert errors == [] and chars is not None and wcs is not None
    compiler = ChapterCompiler(chars, evidence_ids=EVID)
    ch1, errs = compiler.compile({
        'beats': [
            {'beat_id': 'b01_01', 'event': '开篇', 'participants': ['fu_jun'],
             'source_refs': ['e01_01'], 'effects': [], 'reveals': []}],
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


# ───────────────────────── ChapterCompiler ─────────────────────────

def test_chapter_compiler_green_and_statement_registry():
    chars, wcs, compiler, ch1 = _compile_pair()
    ch2, errs = compiler.compile(_chapter_wire(), 2)
    assert ch2 is not None and errs == []
    # 复用 knowledge_key 且 statement 原样 → 绿
    ch3_wire = {
        'beats': [
            {'beat_id': 'b03_01', 'event': '回忆', 'participants': ['yun_niang'],
             'source_refs': ['e03_01'], 'effects': [], 'reveals': [
                 {'knowledge_key': 'loan_secret', 'statement': '借种之约是夫妻共同秘密',
                  'recipients': ['yun_niang'], 'mode': 'confirms'}]}],
        'causal_edges': []}
    ch3, errs = compiler.compile(ch3_wire, 3)
    assert ch3 is not None and errs == []
    # 改写 statement → 拒
    ch3_bad = {
        'beats': [
            {'beat_id': 'b03_01', 'event': '回忆', 'participants': ['yun_niang'],
             'source_refs': ['e03_01'], 'effects': [], 'reveals': [
                 {'knowledge_key': 'loan_secret', 'statement': '另一个说法',
                  'recipients': ['yun_niang'], 'mode': 'confirms'}]}],
        'causal_edges': []}
    _, errs = compiler.compile(ch3_bad, 3)
    assert any('原样复制' in e for e in errs)


def test_chapter_compiler_rejects_unknown_participant_and_dual_owner():
    chars, _, compiler, _ = _compile_pair()
    bad = _chapter_wire()
    bad['beats'][0]['participants'].append('ghost')
    _, errs = compiler.compile(bad, 2)
    assert any('未知人物' in e for e in errs)

    dual = {
        'beats': [
            {'beat_id': 'b02_01', 'event': '变心', 'participants': ['fu_jun'],
             'source_refs': ['e02_01'], 'effects': [
                 {'target': {'kind': 'character', 'char_id': 'fu_jun'},
                  'key': 'affection_toward_yun', 'op': 'set', 'value': '淡'},
                 {'target': {'kind': 'relation', 'subject': 'fu_jun', 'object': 'yun_niang'},
                  'key': 'estranged', 'op': 'set', 'value': True}],
             'reveals': []}],
        'causal_edges': []}
    _, errs = compiler.compile(dual, 2)
    assert any('双 owner' in e for e in errs)


def test_chapter_compiler_preserves_adjacent_causality():
    chars, _, compiler, _ = _compile_pair()
    wire = {
        'beats': [
            {'beat_id': 'b02_01', 'event': 'a', 'participants': [], 'source_refs': ['e02_01'],
             'effects': [], 'reveals': []},
            {'beat_id': 'b02_02', 'event': 'b', 'participants': [], 'source_refs': ['e02_01'],
             'effects': [], 'reveals': []}],
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
        'beats': [
            {'beat_id': 'b02_01', 'event': 'a', 'participants': [], 'source_refs': ['e02_01'],
             'effects': [], 'reveals': []}],
        'causal_edges': [
            {'from_beat': 'b02_01', 'to_beat': 'b01_01', 'kind': 'causes'}]}
    _, errs = compiler.compile(wire, 2)
    assert any('方向反了' in e for e in errs)


# ───────────────────────── assemble + source_fold ─────────────────────────

def test_assemble_and_fold_semantics():
    chars, wcs, compiler, ch1 = _compile_pair()
    ch2, _ = compiler.compile(_chapter_wire(), 2)
    bp = assemble_blueprint(wcs, [ch1, ch2])
    assert isinstance(bp, BlueprintsArtifactV12)
    assert bp.chapters[0].chapter == 1 and bp.chapters[1].chapter == 2

    snapshots, events = source_fold(chars, bp)
    # 章起点快照含 initial_state（stable 婚姻），第 2 章后含 wariness
    assert any('marital_status' in line for line in snapshots[2])
    assert any('wariness' in line for line in snapshots[2])
    assert not any('wariness' in line for line in snapshots[1])


def test_fold_reports_stable_rewrite_and_loop_ops():
    chars, wcs, compiler, ch1 = _compile_pair()
    ch2_wire = {
        'beats': [
            {'beat_id': 'b02_01', 'event': 'x', 'participants': [], 'source_refs': ['e02_01'],
             'effects': [
                 {'target': {'kind': 'relation', 'subject': 'fu_jun', 'object': 'yun_niang'},
                  'key': 'marital_status', 'op': 'set', 'value': '破裂'},   # 改写 stable
                 {'target': {'kind': 'loop', 'loop_id': 'loop_mystery'},
                  'key': '悬而未决', 'op': 'open', 'value': '谁在暗中窥伺'},
             ],
             'reveals': []}],
        'causal_edges': []}
    ch2, errs = compiler.compile(ch2_wire, 2)
    assert ch2 is not None and errs == []
    ch3_wire = {
        'beats': [
            {'beat_id': 'b03_01', 'event': 'y', 'participants': [], 'source_refs': ['e03_01'],
             'effects': [
                 {'target': {'kind': 'loop', 'loop_id': 'loop_mystery'},
                  'key': '悬而未决', 'op': 'close', 'value': None},
             ],
             'reveals': []}],
        'causal_edges': []}
    ch3, errs = compiler.compile(ch3_wire, 3)
    assert ch3 is not None and errs == []
    bp = assemble_blueprint(wcs, [ch1, ch2, ch3])
    snapshots, events = source_fold(chars, bp)
    assert any('stable 事实被改写' in e for e in events)
    # close 后 loop 键不再出现在第 3 章之后的快照
    assert any('loop:loop_mystery' in line for line in snapshots[2])
    assert not any('loop:loop_mystery' in line for line in snapshots[3])
