#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_quote_canon —— D2 引文标点宽度回取（2026-10-03 批 0）。

【验证边界】源文标点以半角为主、模型逐字摘抄常写成全角：逐字判定把真摘抄
误报成 quote_not_in_segment。回取规则＝比对用 `norm()` 尺（内容字相同即同一
内容）、**唯一**命中才回取段原文切片、落盘永远是原文字节；0 处/多处不改写、
判定不放宽。validator 仍只读（I-24），回取发生在 compiler 副本上。
"""
from __future__ import annotations

import pytest

from novel.core.text import content_equivalent_slice
from novel.stages.stage2.book import compile_book_observation
from novel.stages.stage2.chapters import compile_chapter_observation
from novel.stages.stage2.resolver import EvidenceResolver
from contracts.schema.observation import STAGE2_CHAPTERS_PROTOCOL

SEGMENTS = {
    'e01_01': {'chapter': 1, 'text': '夫君说,借种之事,你意下如何?'},
    'e01_02': {'chapter': 1, 'text': '云娘低头,不答,也不看他。'},
    'e01_03': {'chapter': 1, 'text': '他说,不行,他说,不行。'},  # 同内容出现两次
}


# ───────────────────────── text 层：回取尺 ─────────────────────────

def test_content_equivalent_slice_recovers_original_bytes():
    """全角引文在半角源文里唯一命中 → 返回内容跨度的原文切片（半角字节
    原样；尾部标点是 norm 尺的非内容字，不在回取跨度内——回取值仍是段文本
    的逐字连续子串，逐字判定以此通过）。"""
    body = SEGMENTS['e01_01']['text']
    assert content_equivalent_slice('夫君说，借种之事，你意下如何？', body) == '夫君说,借种之事,你意下如何'


def test_content_equivalent_slice_refuses_ambiguous_and_absent():
    assert content_equivalent_slice('他说，不行', SEGMENTS['e01_03']['text']) is None, \
        '相同内容出现两次：拒绝猜位置（与 whitespace_equivalent_slice 同纪律）'
    assert content_equivalent_slice('这句原文没有', SEGMENTS['e01_01']['text']) is None
    assert content_equivalent_slice('', SEGMENTS['e01_01']['text']) is None


def test_resolver_canonical_quote_keeps_verbatim_and_falls_back():
    resolver = EvidenceResolver(SEGMENTS)
    verbatim = SEGMENTS['e01_01']['text'][:4]
    assert resolver.canonical_quote('e01_01', verbatim) == verbatim, '逐字已在段内：原样'
    assert resolver.canonical_quote(
        'e01_01', '夫君说，借种之事') == '夫君说,借种之事', '归一唯一命中：回取原文切片'
    assert resolver.canonical_quote('e01_01', '原文没有的引文') == '原文没有的引文', \
        '0 处命中：不改写，判定不放宽'
    assert resolver.canonical_quote('e01_03', '他说，不行') == '他说，不行', \
        '多处命中：不改写（拒绝猜位置）'
    assert resolver.canonical_quote('e99_99', '任意') == '任意', '段不在闭集：原样'


# ───────────────────────── 章观察：compile 侧回取落盘 ─────────────────────────

def _beat(quote: str) -> dict:
    return {
        'event': '夫君试探借种',
        'participants': ['yun_niang', 'fu_jun'],
        'source_refs': ['e01_01'],
        'scene_boundary': 'new',
        'temporal_mode': 'single',
        'effects': [{'target': {'kind': 'character', 'char_id': 'yun_niang'},
                     'key': 'wariness', 'op': 'set', 'value': '高', 'scope': 'book',
                     'modality': 'actual',
                     'disclosure': {'visibility': 'on_page'},
                     'basis': [{'segment': 'e01_01', 'quote': quote}]}],
        'cognition': [],
        'loops': [],
    }


def test_chapter_observation_canonicalizes_fullwidth_quote():
    """全角摘抄通过验收且落盘为原文切片；调用方 wire 不被修改（I-24）。"""
    wire = {'protocol_version': STAGE2_CHAPTERS_PROTOCOL,
            'beats': [_beat('夫君说，借种之事，你意下如何？')]}
    snapshot = repr(wire)
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=SEGMENTS, chapter=1,
        character_slugs=('yun_niang', 'fu_jun'))
    assert observation is not None and not errors, errors
    stored = observation.beats[0].effects[0].basis[0].quote
    assert stored == '夫君说,借种之事,你意下如何', '落盘是段原文切片（内容跨度）'
    assert '，' not in stored and ',' in stored, '原文字节（半角）原样保留'
    assert repr(wire) == snapshot, 'compiler 归一只动副本，wire 原样'


def test_chapter_observation_still_rejects_wrong_quote():
    """改了字（非标点宽度差异）的引文照旧报 quote_not_in_segment，不放宽。"""
    wire = {'protocol_version': STAGE2_CHAPTERS_PROTOCOL,
            'beats': [_beat('夫君说，借种之事，你可愿意')]}
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=SEGMENTS, chapter=1,
        character_slugs=('yun_niang', 'fu_jun'))
    assert observation is None
    assert any('不逐字存在' in e for e in errors)


def test_chapter_observation_refuses_ambiguous_recovery():
    """归一命中两处：不猜位置，报 issue（引文须逐字可定位）。"""
    wire = {'protocol_version': STAGE2_CHAPTERS_PROTOCOL,
            'beats': [_beat('他说，不行')]}
    # 段指向出现两次的 e01_03；basis 段属 source_refs 需一致
    wire['beats'][0]['source_refs'] = ['e01_03']
    wire['beats'][0]['effects'][0]['basis'] = [{'segment': 'e01_03', 'quote': '他说，不行'}]
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=SEGMENTS, chapter=1,
        character_slugs=('yun_niang', 'fu_jun'))
    assert observation is None
    assert any('不逐字存在' in e for e in errors)


# ───────────────────────── book 观察：同一规则 ─────────────────────────

def _book_wire(quote: str) -> dict:
    from tests.str06_expression_inputs import sample_book_expression
    wire = {
        'protocol_version': 'stage2-book-v4',
        'characters': {
            'fu_jun': {'canonical': '夫君', 'surfaces': [
                {'surface': '夫君', 'source_refs': ['e01_01']}]},
        },
        'initial_state': [
            {'target': {'kind': 'character', 'char_id': 'fu_jun'},
             'key': 'temper', 'value': '温和', 'persistence': 'stable',
             'knowledge': None, 'source_refs': ['e01_02'],
             'basis': [{'segment': 'e01_02', 'quote': quote}]},
        ],
        'world_constraints': [
            {'kind': 'setting_constant', 'statement': '世道艰难',
             'source_refs': ['e01_01'],
             'basis': [{'segment': 'e01_01', 'quote': '夫君说,借种之事,你意下如何?'}]},
        ],
    }
    wire.update(sample_book_expression(
        {'fu_jun': {'surfaces': [{'surface': '夫君', 'source_refs': ['e01_01']}]}}))
    return wire


def test_book_observation_canonicalizes_basis_quote():
    wire = _book_wire('云娘低头，不答，也不看他。')
    observation, errors = compile_book_observation(wire, evidence=SEGMENTS)
    assert observation is not None and not errors, errors
    stored = observation.initial_state[0].basis[0].quote
    assert stored == '云娘低头,不答,也不看他', '初态 basis 同样回取原文切片（内容跨度）'
