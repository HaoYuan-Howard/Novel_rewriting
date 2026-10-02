#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_a1_semantics —— R10a a1 判据面的定向红绿例（引文逐字／段归属／
modality 槽位／限定词读数）。

【验证边界】`tests/str06_semantic_cases.py::CASES_BY_BATCH['R10a-a1']` 的
机械面：C4（引文错位）红在 resolver 逐字判定与段归属判定；P3（原以为／后来
证实）证明限定词只进 fold 读数、不进门；fold 的证据读数四件套（引文长度
分布、引文等于称谓字面、命题含判断限定词比例、modality 计数）如实计数。
「预计而 actual」的语义错误不归 a1 机械面（C2 转绿在 a4 的 kf 一致性）——
a1 只保证 expected 有独立槽位可表达且读数可见。
"""
from __future__ import annotations

from novel.stages.chain import build_evidence
from novel.stages.stage2 import (
    ChapterObservation, compile_chapter_observation, evidence_metrics,
)
from tests.str06_semantic_cases import CASES_BY_BATCH

A1_CASES = CASES_BY_BATCH['R10a-a1']

# 合成底座：一段含动作细节的章文本（C4 的错位引文素材）＋一段含「原以为／
# 证实」转折（P3 素材）＋一段含预计限定（C2 素材）。
CHAPTERS = [
    {'chapter_ordinal': 1,
     'text': '她攥紧了帕子，指节发白。她原以为丈夫只是性子冷，后来终于证实'
             '他心里有她。他说此去约莫半个月便回。'},
]
EVIDENCE = build_evidence(CHAPTERS)


def _beat(event: str, refs: tuple[str, ...], **over) -> dict:
    beat = {'event': event, 'participants': [], 'source_refs': list(refs),
            'scene_boundary': 'new', 'temporal_mode': 'single',
            'effects': [], 'cognition': [], 'loops': []}
    beat.update(over)
    return beat


def _compile(beats: list[dict]):
    wire = {'protocol_version': 'stage2-chapters-v2', 'beats': beats}
    return compile_chapter_observation(
        wire, evidence_ids=EVIDENCE, chapter=1,
        character_slugs=frozenset({'qi_niang', 'zhang_fu'}))


# ───────────────────────── C4：引文错位（红）与逐字命中（绿） ─────────────────────────

def test_c4_quote_not_in_segment_is_rejected_precisely():
    """红：引文转写（「他」代「她」）不逐字存在于句段 → quote_not_in_segment。

    段 id 与拍 refs 都合法——错误只在引文本身，issue 定位到该条 basis。
    """
    case = next(c for c in A1_CASES if c.case_id == 'C4')
    assert '她攥紧了帕子' in EVIDENCE['e01_01']['text']
    _obs, errors = _compile([
        _beat(case.title, ('e01_01',),
              effects=[{'target': {'kind': 'character', 'char_id': 'qi_niang'},
                        'key': 'mood', 'op': 'set', 'value': '紧',
                        'scope': 'book', 'modality': 'actual',
                        'disclosure': {'visibility': 'on_page'},
                        'basis': [{'segment': 'e01_01', 'quote': '他攥紧了帕子'}]}]),
    ])
    assert any('quote_not_in_segment' in e or '不逐字存在' in e for e in errors), errors


def test_c4_misattributed_quote_feedback_names_verbatim_home():
    """R10c（2026-10-02）：引文逐字正确但挂错段号 → 反馈指认真实命中段。

    模型常引对原文却写错 segment id（对话分句的收尾「」孤儿段使 id↔句子
    对齐反直觉，R10c c1 ch09 实证）。命中段扫描仍纯逐字、判定仍拒绝——
    只改反馈形状，不放宽匹配；修订轮据此直接改段号。
    """
    assert '他说此去约莫半个月便回' in EVIDENCE['e01_03']['text']
    _obs, errors = _compile([
        _beat('归期', ('e01_01', 'e01_03'),
              effects=[{'target': {'kind': 'character', 'char_id': 'zhang_fu'},
                        'key': 'promise', 'op': 'set', 'value': '半月归',
                        'scope': 'book', 'modality': 'actual',
                        'disclosure': {'visibility': 'on_page'},
                        'basis': [{'segment': 'e01_01',
                                   'quote': '他说此去约莫半个月便回'}]}]),
    ])
    hit = [e for e in errors if '不逐字存在' in e]
    assert hit and '逐字命中于句段' in hit[0], errors
    assert "'e01_03'" in hit[0], errors
    # 转写引文（闭集内任何段都不逐字命中）→ 反馈如实说未命中，不猜段。
    _obs2, errors2 = _compile([
        _beat('归期', ('e01_01',),
              effects=[{'target': {'kind': 'character', 'char_id': 'zhang_fu'},
                        'key': 'promise', 'op': 'set', 'value': '半月归',
                        'scope': 'book', 'modality': 'actual',
                        'disclosure': {'visibility': 'on_page'},
                        'basis': [{'segment': 'e01_01',
                                   'quote': '他说大约过半个月就会回来'}]}]),
    ])
    hit2 = [e for e in errors2 if '不逐字存在' in e]
    assert hit2 and '未在闭集任何句段逐字命中' in hit2[0], errors2


def test_c4_segment_outside_beat_refs_is_rejected():
    """红：basis 引的段不在本拍 source_refs 内 → basis_segment_outside_refs
    （引你所引——不得引用未声明的段；段本身存在且引文逐字命中也不行）。"""
    assert '她攥紧了帕子' in EVIDENCE['e01_01']['text']
    _obs, errors = _compile([
        _beat('攥帕子', ('e01_02',),
              effects=[{'target': {'kind': 'character', 'char_id': 'qi_niang'},
                        'key': 'mood', 'op': 'set', 'value': '紧',
                        'scope': 'book', 'modality': 'actual',
                        'disclosure': {'visibility': 'on_page'},
                        'basis': [{'segment': 'e01_01', 'quote': '她攥紧了帕子'}]}]),
    ])
    assert any('不在条目自身 source_refs' in e for e in errors), errors


def test_c4_verbatim_quote_from_declared_segment_is_green():
    """绿：逐字引用、段属本拍 refs → 无引文错误，观察可成形。"""
    obs, errors = _compile([
        _beat('攥帕子', ('e01_01',),
              effects=[{'target': {'kind': 'character', 'char_id': 'qi_niang'},
                        'key': 'mood', 'op': 'set', 'value': '紧',
                        'scope': 'book', 'modality': 'actual',
                        'disclosure': {'visibility': 'on_page'},
                        'basis': [{'segment': 'e01_01', 'quote': '她攥紧了帕子'}]}]),
    ])
    assert errors == [] and obs is not None
    assert obs.beats[0].effects[0].basis[0].quote == '她攥紧了帕子'


# ───────────────────────── P3：限定词只进读数，不进门 ─────────────────────────

def test_p3_hedged_then_confirmed_statement_is_not_blocked():
    """绿（P3 正例）：「原以为…后来终于证实」的命题 statement 不被关键词拦
    ——限定词是判断信号（进 fold 读数），不是拒绝条件。"""
    case = next(c for c in A1_CASES if c.case_id == 'P3')
    assert '原以为' in EVIDENCE['e01_02']['text']
    obs, errors = _compile([
        _beat('心结解开', ('e01_02',),
              cognition=[{'proposition': {'ref': 'p_warm',
                                          'statement': '丈夫心里有她'},
                          'holder': 'qi_niang', 'mode': 'confirms',
                          'basis': [{'segment': 'e01_02',
                                     'quote': '后来终于证实他心里有她'}]}]),
    ])
    assert errors == [] and obs is not None
    assert obs.beats[0].cognition[0].proposition.statement == '丈夫心里有她'


def test_c2_expected_modality_slot_exists_and_reads_out():
    """C2 的 a1 面：「约莫半个月便回」声明 modality=expected 有独立槽位可表达
    （actual 也过 schema——语义正确性归后续锁与核读，机械面不猜）；「约」进
    限定词读数。"""
    assert '约莫半个月便回' in EVIDENCE['e01_03']['text']
    obs, errors = _compile([
        _beat('辞行', ('e01_03',),
              effects=[{'target': {'kind': 'relation', 'subject': 'zhang_fu',
                                   'object': 'qi_niang'},
                        'key': 'apart_duration', 'op': 'set', 'value': '半月',
                        'scope': 'book', 'modality': 'expected',
                        'disclosure': {'visibility': 'on_page'},
                        'basis': [{'segment': 'e01_03',
                                   'quote': '此去约莫半个月便回'}]}]),
    ])
    assert errors == [] and obs is not None
    assert obs.beats[0].effects[0].modality == 'expected'


# ───────────────────────── fold 证据读数四件套 ─────────────────────────

def test_fold_evidence_metrics_count_quotes_hedges_and_modality():
    """读数如实计数：引文总数与长度、引文等于称谓字面、命题含限定词比例、
    modality 计数（度量不是门，§四诊断表）。"""
    from contracts.schema.source import BlueprintsArtifactV15, ChapterBlueprint
    from tests.str06_facilities import v13_beat

    quote = '她攥紧了帕子'
    beat = v13_beat(
        'b01_01', '攥帕子', participants=('qi_niang',), source_refs=('e01_01',),
        effects=({'target': {'kind': 'character', 'char_id': 'qi_nuang'},
                  'key': 'mood', 'op': 'set', 'value': '紧', 'scope': 'book',
                  'modality': 'expected',
                  'disclosure': {'visibility': 'on_page'},
                  'basis': ({'segment': 'e01_01', 'quote': quote},)},
                 {'target': {'kind': 'character', 'char_id': 'qi_niang'},
                  'key': 'surface_echo', 'op': 'set', 'value': 'x', 'scope': 'book',
                  'modality': 'actual',
                  'disclosure': {'visibility': 'on_page'},
                  'basis': ({'segment': 'e01_01', 'quote': '夫君'},)}),
        cognition=({'proposition': {'ref': 'p1', 'statement': '他以为天要塌了'},
                    'holder': 'qi_niang', 'mode': 'suspects',
                    'basis': ({'segment': 'e01_01', 'quote': quote},)},
                   {'proposition': {'ref': 'p2', 'statement': '帕子攥紧了'},
                    'holder': 'qi_niang', 'mode': 'learns',
                    'basis': ({'segment': 'e01_01', 'quote': quote},)}))
    blueprint = BlueprintsArtifactV15(
        source_expression=__import__(
            'tests.str06_expression_inputs', fromlist=['sample_blueprint_expression']
        ).sample_blueprint_expression(),
        chapters=(ChapterBlueprint(
            chapter=1, beats=(beat,),
            scenes=({'scene_id': 'sc01', 'beat_ids': ('b01_01',)},),
            causal_edges=()),),
        evidence={'e01_01': EVIDENCE['e01_01']['text']})
    from contracts.schema.source import CharacterEntry, CharactersCore, SurfaceWindow
    characters = CharactersCore(characters={
        'qi_niang': CharacterEntry(
            canonical='夫君',
            surfaces=(SurfaceWindow(surface='夫君', from_chapter=1,
                                    source_refs=('e01_01',)),))},
        initial_state=())
    metrics = evidence_metrics(blueprint, characters)
    assert metrics['quote_total'] == 4
    assert metrics['quote_length_min'] == len('夫君') == 2
    assert metrics['quote_length_max'] == len(quote)
    assert metrics['quotes_equal_surface'] == 1, '「夫君」引文＝称谓字面被点出'
    assert metrics['cognition_total'] == 2 and metrics['cognition_hedged'] == 1
    assert metrics['cognition_hedged_ratio'] == 0.5
    assert metrics['effect_modality_counts'] == {'actual': 1, 'expected': 1}
