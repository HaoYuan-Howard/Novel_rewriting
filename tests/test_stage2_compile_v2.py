#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage2_compile_v2.py —— extraction IR 族（含 v2 引用式响应）确定性编译器的判定面。

覆盖四族差别（`novel/stages/stage2/compile_v2.py` 模块头）：
① 游标分割与全部非法形（稳定 finding 措辞，不抛裸异常）；
② hinge 拍区间内唯一定位（exact / normalized / 多处命中 / 他处 / 编的 / cascade）；
③ nullable 地点字面与原文探针；
④ 原子事实与关闭式人物引用不产生显示字段副本。
外加 round-trip：`compile(project(artifact)) == artifact`（对 compile_v2 自己产的
artifact 成立——`canonical_ir_projection` 不是逆函数，方向不能反）。
"""
from __future__ import annotations

import copy

import pytest

from contracts.invariants import (
    HINGE_FAILURE_ROUTE, REPAIR_ROUTE, SPAN_FAILURE_CAUSES,
)
from contracts.schema.stage2 import SourceSpan
from novel.stages.stage2.compile_v2 import (
    canonical_ir_projection, canonical_ir_projection_v31, compile_v2,
    compile_v2_refs, compile_v31_refs, visible_character_refs,
)
from novel.stages.stage2.references import hinge_candidates

#: 5 段的合成章。段号（1-based）：1 开门 · 2 第一课台词 · 3 两担水 · 4 母亲说话 · 5 三十文。
CH1 = ('李宝珠推开院门，井边的水汽漫上来。\n'
       '「今天的第一课，」母亲说，「是挑水。」\n'
       '她挑了两担水，肩膀磨出了红印。\n'
       '母亲看着她，又说了一句话。\n'
       '傍晚她回屋，把三十文钱放在桌上。')


def _main_text(text: str = CH1, ordinal: int = 1) -> list[dict]:
    return [{'chapter_ordinal': ordinal, 'text': text}]


CHARACTERS = {
    'char_001': {
        'canonical': '李宝珠', 'name_distinctiveness': 'distinct',
        'address': [{'surface': '李宝珠', 'by': 'narration',
                     'context': 'reference', 'from_chapter': 1}],
        'role': '略', 'fate': None,
    },
    'char_002': {
        'canonical': '母亲', 'name_distinctiveness': 'generic',
        'address': [{'surface': '母亲', 'by': 'narration',
                     'context': 'reference', 'from_chapter': 1}],
        'role': '略', 'fate': None,
    },
}


def _beat(end_para, *, layer='T_PRESENT', place=None, names=(), event='一件事',
          hinges=()) -> dict:
    copied_hinges = [dict(h) for h in hinges]
    evidence = next((str(h.get('phrase') or '') for h in copied_hinges
                     if str(h.get('phrase') or '').strip()), event)
    return {
        'end_para': end_para,
        'time_layer': layer,
        'place': place,
        'present_char_ids': [
            {'李宝珠': 'char_001', '母亲': 'char_002'}[name] for name in names],
        'fact_atoms': [{
            'atom_key': 'pending', 'claim': event, 'kind': 'event',
            'obligation': 'land', 'evidence_phrase': evidence,
            'participant_char_ids': [],
        }],
        'hinges': copied_hinges,
    }


def _narration() -> dict:
    return {'grammatical_person': 'third', 'narrator_char_id': None,
            'focalizer_char_id': None, 'pronoun_surfaces': []}


def _data(*beats: dict, ordinal: int = 1) -> dict:
    return {'blueprints': [_chapter(list(beats), ordinal)]}


def _chapter(beats: list[dict], ordinal: int) -> dict:
    for index, beat in enumerate(beats, 1):
        for atom_index, atom in enumerate(beat.get('fact_atoms') or [], 1):
            atom['atom_key'] = f'a{index:02d}_{atom_index:02d}'
    return {
        'chapter_ordinal': ordinal,
        'chapter_function': '完成本章的事实与状态变化',
        'beats': beats,
        'causal_edges': [],
        'entry_state_refs': [],
        'ending_state_refs': [],
        'narration': [_narration()],
    }


def _atom(key: str, claim: str, evidence: str, ids=()) -> dict:
    return {
        'atom_key': key, 'claim': claim, 'kind': 'event', 'obligation': 'land',
        'evidence_phrase': evidence, 'participant_char_ids': list(ids),
    }


def _compile(data: dict, text: str = CH1):
    return compile_v2(data, _main_text(text), CHARACTERS)


def _clean_data() -> dict:
    return _data(
        _beat(2, place='院子',
              names=['李宝珠', '母亲'],
              event='母亲宣布第一课是挑水',
              hinges=[{'kind': '要害台词', 'phrase': '今天的第一课'}]),
        _beat(4, place='院子', names=['李宝珠', '母亲'],
              event='她挑了两担水，母亲点评',
              hinges=[{'kind': '硬数字', 'phrase': '两担水'}]),
        _beat(5, layer='T_RECALL', place=None, names=['李宝珠'],
              event='她想起桌上那三十文钱',
              hinges=[{'kind': '硬数字', 'phrase': '三十文'}]),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 一、干净底座：分割 / 映射 / 继承 / 定位 / 编号一次全对
# ─────────────────────────────────────────────────────────────────────────────

def test_the_clean_v2_response_compiles_with_zero_failures():
    data, acc = _compile(_clean_data())
    assert acc['verbatim_failures'] == []
    assert acc['hinge_ambiguous_pending'] == []
    beats = data['blueprints'][0]['beats']

    # ① 分割：首拍起 1、相邻相接、末拍到底；span 是精确区间且逐字节等于原文切片。
    assert [b['beat_id'] for b in beats] == ['b01_01', 'b01_02', 'b01_03']
    assert beats[0]['span']['start'] == 0
    assert beats[1]['span']['start'] == beats[0]['span']['end'] + 1  # 段间换行
    assert beats[2]['span']['end'] == len(CH1)
    for b in beats:
        s = b['span']
        assert CH1[s['start']:s['end']] == s['text']
        assert set(s) == {'text', 'start', 'end'}
        assert 'end_para' not in b, '响应形的键不许留在产物上'

    # ② time_layer 前缀枚举映射回产物值。
    assert [b['time_layer'] for b in beats] == ['present', 'present', 'recall']

    # ③ place：响应直接交 nullable 字面，不另存继承模式。
    assert [b['place'] for b in beats] == ['院子', '院子', None]

    # ④ hinge 全部唯一命中，字节精确（逗号不是句末闭合标点，不被扩展吞入）。
    assert [h['source']['text'] for b in beats for h in b['hinges']] == \
        ['今天的第一课', '两担水', '三十文']

    # 人物引用保持模型从闭集选出的 id。
    assert beats[0]['present_char_ids'] == ['char_001', 'char_002']


def test_compile_does_not_create_a_duplicate_summary():
    artifact, _ = _compile(_clean_data())
    assert 'summary' not in artifact['blueprints'][0]


# ─────────────────────────────────────────────────────────────────────────────
# 一·b、两段式 v2：人物关闭式引用与地点字符串
# ─────────────────────────────────────────────────────────────────────────────

def test_visible_character_refs_never_leak_a_future_surface():
    mt = [
        {'chapter_ordinal': 1, 'text': '李宝珠独自推开门。'},
        {'chapter_ordinal': 2, 'text': '母亲这才从门后走出来。'},
    ]
    visible = visible_character_refs(CHARACTERS, mt)
    assert visible[1] == {'char_001': ['李宝珠']}
    assert visible[2] == {
        'char_001': ['李宝珠'], 'char_002': ['母亲']}


def test_visible_refs_do_not_grant_a_future_owner_of_an_already_seen_surface():
    """字面出现过不等于挂这个字面的所有人都已经可见。"""
    characters = copy.deepcopy(CHARACTERS)
    characters['char_003'] = {
        'canonical': '后来者', 'name_distinctiveness': 'distinct',
        'address': [{'surface': '母亲', 'by': 'narration',
                     'context': 'reference', 'from_chapter': 2}],
        'role': '略', 'fate': None,
    }
    mt = [
        {'chapter_ordinal': 1, 'text': '母亲先在门边等着。'},
        {'chapter_ordinal': 2, 'text': '后来者也被人叫作母亲。'},
    ]
    visible = visible_character_refs(characters, mt)
    assert 'char_003' not in visible[1]
    assert visible[2]['char_003'] == ['后来者', '母亲']


def test_canonical_surface_shadows_the_same_address_clock():
    """canonical 已提供同一字面时，address 的较晚登记章不能把它重新关掉。"""
    characters = copy.deepcopy(CHARACTERS)
    characters['char_002']['address'][0]['from_chapter'] = 9

    visible = visible_character_refs(
        characters, [{'chapter_ordinal': 1, 'text': '母亲先在门边等着。'}])

    assert visible[1]['char_002'] == ['母亲']


def test_v2_refs_keeps_selected_ids_authoritative_without_display_name_copies():
    raw = {'blueprints': [{
        'chapter_ordinal': 1,
        'chapter_function': '完成挑水',
        'beats': [{
            'end_para': 5,
            'time_layer': 'T_PRESENT',
            'place': '井边',
            'present_char_ids': ['char_002', 'char_001', 'char_002'],
            'fact_atoms': [_atom('a01', '母亲让李宝珠挑水', '今天的第一课',
                                 ['char_002', 'char_001'])],
            'hinges': [{'kind': '硬数字', 'phrase': '两担水'}],
        }],
        'narration': [_narration()],
    }]}
    data, accounting = compile_v2_refs(raw, _main_text(), CHARACTERS)
    beat = data['blueprints'][0]['beats'][0]
    assert beat['present_char_ids'] == ['char_002', 'char_001']
    assert 'present_names' not in beat
    assert beat['place'] == '井边'
    assert 'char_refs' not in accounting


def test_v2_refs_rejects_an_id_outside_the_visible_closed_set():
    raw = {'blueprints': [{
        'chapter_ordinal': 1,
        'chapter_function': '非法引用样本',
        'beats': [{
            'end_para': 5, 'time_layer': 'T_PRESENT', 'place': None,
            'present_char_ids': ['char_999'],
            'fact_atoms': [_atom('a01', '一件事', '李宝珠', ['char_999'])],
            'hinges': [],
        }],
        'narration': [_narration()],
    }]}
    with pytest.raises(ValueError, match='不在本章可见集合'):
        compile_v2_refs(raw, _main_text(), CHARACTERS)


def test_v31_candidate_ids_compile_to_exact_sources_and_closed_narration_refs():
    candidates = hinge_candidates(CH1)
    hard_number = next(row for row in candidates
                       if row['source']['text'] == '两担')
    raw = {'blueprints': [{
        'chapter_ordinal': 1,
        'chapter_function': '完成挑水并收钱',
        'micro_events': [
            {'end_para': 3, 'cue': '母亲布置挑水，李宝珠完成'},
            {'end_para': 5, 'cue': '她把钱放到桌上'},
        ],
        'beats': [{
            'end_para': 3, 'time_layer': 'T_PRESENT', 'place': '井边',
            'present_char_ids': ['char_001', 'char_002'],
            'fact_atoms': [_atom('a01', '母亲让李宝珠挑水，李宝珠完成两担',
                                 '两担水', ['char_001', 'char_002'])],
            'hinges': [{'kind': '硬数字',
                        'candidate_id': hard_number['candidate_id']}],
        }, {
            'end_para': 5, 'time_layer': 'T_PRESENT', 'place': '屋内',
            'present_char_ids': ['char_001'],
            'fact_atoms': [_atom('a02', '李宝珠把三十文钱放在桌上',
                                 '三十文钱', ['char_001'])],
            'hinges': [{'kind': '硬数字', 'phrase': '三十文'}],
        }],
        'narration': [{
            'grammatical_person': 'third',
            'narrator_char_id': 'char_001',
            'focalizer_char_id': 'char_001',
            'pronoun_surfaces': [],
        }],
    }]}
    data, accounting = compile_v31_refs(raw, _main_text(), CHARACTERS)
    blueprint = data['blueprints'][0]
    assert 'micro_events' not in blueprint, '召回脚手架不得泄漏到正式产物'
    candidate_hinge = blueprint['beats'][0]['hinges'][0]
    assert candidate_hinge['source'] == hard_number['source']
    assert blueprint['beats'][1]['hinges'][0]['source']['text'] == '三十文', \
        '候选覆盖不到时的 phrase 兜底仍走既有唯一定位'
    narration = blueprint['narration'][0]
    assert narration['narrator_char_id'] == 'char_001'
    assert narration['focalizer_char_id'] == 'char_001'
    assert 'narrator_name' not in narration
    assert accounting['verbatim_failures'] == []


def test_v31_candidate_table_drops_punctuation_only_sources_before_publish():
    text = '她只回「?」。他沉默着答「......」。最后说「留下」。'
    candidates = hinge_candidates(text)
    surfaces = {row['source']['text'] for row in candidates}

    assert '「?」' not in surfaces
    assert '「......」' not in surfaces
    assert '「留下」' in surfaces
    assert candidates, '守卫必须有分母，不能让空候选表冒充通过'
    for row in candidates:
        SourceSpan.model_validate(row['source'])


def test_v31_number_candidates_keep_ordinals_whole_and_drop_relative_suffixes():
    text = '第二天他们二人出发，下一秒折返；三天后花了十两。'
    surfaces = {row['source']['text'] for row in hinge_candidates(text)}

    assert {'第二天', '二人', '三天', '十两'} <= surfaces
    assert {'二天', '一秒'}.isdisjoint(surfaces)


def test_v31_candidate_budget_is_stratified_by_family_and_chapter_position():
    quoted = [f'「第{index:02d}句对白」' for index in range(45)]
    text = '\n\n'.join(quoted + [
        '因为真相终于揭开，所以她决定离开。',
        '尾声写着「最后的答复」。',
    ])
    candidates = hinge_candidates(text)
    surfaces = {row['source']['text'] for row in candidates}

    assert len(candidates) == 20
    assert any('因为真相终于揭开' in surface for surface in surfaces), \
        '显式因果族必须有保底，不能被章首引号填满候选表'
    assert '「最后的答复」' in surfaces, \
        '同族也要沿章节位置取样，不能永远只留下章前对白'
    assert [row['candidate_id'] for row in candidates] == [
        f'h{index:03d}' for index in range(1, 21)]
    assert [row['source']['start'] for row in candidates] == sorted(
        row['source']['start'] for row in candidates)


def test_v31_rejects_a_candidate_from_outside_its_beat_even_without_call_gate():
    raw = {'blueprints': [{
        'chapter_ordinal': 1,
        'chapter_function': '完成开课与收钱',
        'micro_events': [{'end_para': 2, 'cue': '开课'},
                         {'end_para': 5, 'cue': '收钱'}],
        'beats': [{
            'end_para': 2, 'time_layer': 'T_PRESENT', 'place': None,
            'present_char_ids': [],
            'fact_atoms': [_atom('a01', '母亲开课', '今天的第一课')],
            # h004 在第 5 段，不能挂到止于第 2 段的拍。
            'hinges': [{'kind': '硬数字', 'candidate_id': 'h004'}],
        }, {
            'end_para': 5, 'time_layer': 'T_PRESENT', 'place': None,
            'present_char_ids': [],
            'fact_atoms': [_atom('a02', '事情收束', '三十文钱')], 'hinges': [],
        }],
        'narration': [{
            'grammatical_person': 'third', 'narrator_char_id': None,
            'focalizer_char_id': None, 'pronoun_surfaces': [],
        }],
    }]}
    with pytest.raises(ValueError, match='不在所属拍'):
        compile_v31_refs(raw, _main_text(), CHARACTERS)


def test_v31_projection_is_a_valid_repair_baseline_and_round_trips():
    compiled, _ = compile_v31_refs({
        'blueprints': [{
            'chapter_ordinal': 1,
            'chapter_function': '完成挑水',
            'micro_events': [{'end_para': 5, 'cue': '完成挑水'}],
            'beats': [{
                'end_para': 5, 'time_layer': 'T_PRESENT', 'place': '井边',
                'present_char_ids': ['char_001'],
                'fact_atoms': [_atom('a01', '李宝珠完成挑水', '两担水', ['char_001'])],
                'hinges': [{'kind': '硬数字', 'candidate_id': 'h003'}],
            }],
            'narration': [{
                'grammatical_person': 'third', 'narrator_char_id': 'char_001',
                'focalizer_char_id': 'char_001', 'pronoun_surfaces': [],
            }],
        }],
    }, _main_text(), CHARACTERS)
    artifact = compiled['blueprints'][0]
    projected = canonical_ir_projection_v31(artifact, CH1)
    assert projected['micro_events'][0]['cue'] == artifact['fact_atoms'][0]['claim']
    assert set(projected['narration'][0]) == {
        'grammatical_person', 'narrator_char_id', 'focalizer_char_id',
        'pronoun_surfaces'}
    replayed, _ = compile_v31_refs(
        {'blueprints': [projected]}, _main_text(), CHARACTERS)
    assert replayed['blueprints'][0] == artifact


# ─────────────────────────────────────────────────────────────────────────────
# 二、非法分割：逐项稳定措辞，不抛裸异常
# ─────────────────────────────────────────────────────────────────────────────

def _span_causes(acc) -> list[str]:
    return [row[2] for row in acc['verbatim_failures'] if row[1] == 'span']


def test_a_non_integer_end_para_is_a_finding_not_a_crash():
    _, acc = _compile(_data(_beat('五'), _beat(5)))
    assert '段号不是整数' in _span_causes(acc)


def test_an_out_of_range_end_para_is_reported():
    _, acc = _compile(_data(_beat(99)))
    assert '段号越界' in _span_causes(acc)


def test_a_repeated_or_backward_end_para_is_reported():
    _, acc = _compile(_data(_beat(3), _beat(3), _beat(5)))
    assert '止段没有越过上一拍的止段（区间倒序或同段重复）' in _span_causes(acc)


def test_a_last_beat_that_stops_far_short_keeps_its_span_but_reddens_the_ledger():
    """差得超过帽子（这里差 3 段）⇒ 那是真的切错了，照旧判 HARD 走整章。"""
    data, acc = _compile(_data(_beat(1), _beat(2)))
    assert '末拍没有到达本章最后一段' in _span_causes(acc)
    # span 本身可定位 ⇒ 不抹掉（信息无罪）；失败态由账承载（HARD 按行数判）。
    assert data['blueprints'][0]['beats'][1]['span'] is not None


def test_an_empty_chapter_text_is_reported():
    _, acc = _compile(_data(_beat(1)), text='')
    assert '本章没有可编号的段落' in _span_causes(acc)


# ─────────────────────────────────────────────────────────────────────────────
# 二·b、层0 护栏式边界外扩（7A 实测的那一个根因：止段比 event 早收 1–2 段）
# ─────────────────────────────────────────────────────────────────────────────

def test_a_boundary_one_paragraph_short_of_its_own_hinge_is_nudged_not_failed():
    """`b13_02` 那一族：event 写到了第 3 段的事，止段却给了 2。"""
    data, acc = _compile(_data(
        _beat(2, hinges=[{'kind': '硬数字', 'phrase': '两担水'}]),   # 在第 3 段
        _beat(5),
    ))
    assert acc['verbatim_failures'] == [], '能算出来的整数，不该去买一次整章重生成'
    beats = data['blueprints'][0]['beats']
    assert beats[0]['hinges'][0]['source']['text'] == '两担水'
    # 外扩改的是**两拍**：下一拍的起点跟着挪，且两拍仍然首尾相接。
    assert beats[1]['span']['start'] == beats[0]['span']['end'] + 1


def test_a_tail_one_paragraph_short_is_nudged_to_the_last_paragraph():
    data, acc = _compile(_data(_beat(2), _beat(4)))
    assert _span_causes(acc) == []
    assert data['blueprints'][0]['beats'][-1]['span']['end'] == len(CH1)


def test_a_nudge_that_would_starve_the_next_beat_is_refused():
    """外扩不许越过下一拍的止段——挤空它比留着 drift 更坏。"""
    _, acc = _compile(_data(
        _beat(2, hinges=[{'kind': '硬数字', 'phrase': '两担水'}]),   # 在第 3 段
        _beat(3),                                                    # 下一拍只有第 3 段
        _beat(5),
    ))
    assert _hinge_rows(acc)['b01_01'] == '短语不在它所属拍的区间里，而出现在别处'


def test_a_nudge_that_would_steal_the_next_beats_own_anchor_is_refused():
    """下一拍原本定得住的锚点，不许被外扩抢走。"""
    _, acc = _compile(_data(
        _beat(2, hinges=[{'kind': '硬数字', 'phrase': '两担水'}]),   # 在第 3 段
        _beat(4, hinges=[{'kind': '硬数字', 'phrase': '两担水'}]),   # 它自己也压在第 3 段
        _beat(5),
    ))
    assert _hinge_rows(acc)['b01_01'] == '短语不在它所属拍的区间里，而出现在别处'


def test_an_ambiguous_phrase_is_never_fixed_by_moving_a_boundary():
    """拍内已经多处命中 ⇒ 那是候选选择那条路，边界一动歧义只会更糊。"""
    text = '她把纸鸢递过去。\n他接过纸鸢看了半天。\n收场白。'
    _, acc = _compile(_data(_beat(2, hinges=[{'kind': '物件', 'phrase': '纸鸢'}]),
                            _beat(3)), text=text)
    assert acc['hinge_ambiguous_pending'] == [('b01_01', '物件', '纸鸢')]


def test_a_nudge_that_would_create_ambiguity_is_refused():
    """外扩后仍要**唯一**命中：修不好就不改，编译器不做无效改写。"""
    text = '开场白。\n她把纸鸢递过去，他又把纸鸢还回来。\n收场白。'
    _, acc = _compile(_data(_beat(1, hinges=[{'kind': '物件', 'phrase': '纸鸢'}]),
                            _beat(3)), text=text)
    assert _hinge_rows(acc)['b01_01'] == '短语不在它所属拍的区间里，而出现在别处'


def test_a_chapter_with_an_illegal_end_para_is_never_nudged():
    """止段序列已经坏了，这一章的分段主张就不成立——不在坏序列上推边界。"""
    _, acc = _compile(_data(
        _beat(99, hinges=[{'kind': '硬数字', 'phrase': '两担水'}]),
        _beat(5),
    ))
    assert '段号越界' in _span_causes(acc)


def test_every_cause_this_compiler_emits_is_registered():
    for cause in ('段号不是整数', '段号越界', '本章没有可编号的段落',
                  '止段没有越过上一拍的止段（区间倒序或同段重复）',
                  '末拍没有到达本章最后一段'):
        assert cause in SPAN_FAILURE_CAUSES


# ─────────────────────────────────────────────────────────────────────────────
# 三、hinge：拍区间内唯一定位的全部出口
# ─────────────────────────────────────────────────────────────────────────────

def _hinge_rows(acc) -> dict[str, str]:
    return {row[0]: row[2] for row in acc['verbatim_failures'] if row[1] == 'hinge'}


def test_a_byte_mismatched_phrase_is_recovered_in_content_space():
    data, acc = _compile(_data(_beat(5, hinges=[
        {'kind': '要害台词', 'phrase': '今天的第一课,'},   # 半角逗号，原文是全角
    ])))
    hinge = data['blueprints'][0]['beats'][0]['hinges'][0]
    assert set(hinge['source']) == {'text', 'start', 'end'}
    assert hinge['source']['text'] == '今天的第一课'
    assert acc['verbatim_failures'] == []


def test_an_ambiguous_phrase_is_blanked_and_parked_for_candidate_selection():
    text = '她把纸鸢递过去。\n他接过纸鸢看了半天。'
    data, acc = _compile(_data(_beat(2, hinges=[{'kind': '物件', 'phrase': '纸鸢'}])),
                         text=text)
    assert _hinge_rows(acc)['b01_01'] == '短语在本拍区间内多处命中'
    assert acc['hinge_ambiguous_pending'] == [('b01_01', '物件', '纸鸢')]
    assert data['blueprints'][0]['beats'][0]['hinges'][0]['source'] is None
    # 根因 → 判据 / 修复层，两张表都接得住。
    assert HINGE_FAILURE_ROUTE['短语在本拍区间内多处命中'] == 'hinge_ambiguous'
    assert REPAIR_ROUTE['短语在本拍区间内多处命中'] == 'field_patch'


def test_a_phrase_from_another_beats_span_is_misplaced_not_invented():
    _, acc = _compile(_data(
        _beat(2, hinges=[{'kind': '硬数字', 'phrase': '三十文'}]),   # 在第 5 段
        _beat(5),
    ))
    assert _hinge_rows(acc)['b01_01'] == '短语不在它所属拍的区间里，而出现在别处'


def test_an_invented_phrase_is_reported_as_void():
    _, acc = _compile(_data(_beat(5, hinges=[
        {'kind': '因果前提', 'phrase': '这句话原文里没有'},
    ])))
    assert _hinge_rows(acc)['b01_01'] == '短语不在本章原文里'


def test_hinges_of_a_failed_beat_are_blanked_without_their_own_cause_rows():
    data, acc = _compile(_data(
        _beat(99, hinges=[{'kind': '物件', 'phrase': '两担水'}]),
    ))
    assert _hinge_rows(acc) == {}, 'span 自己没定位成功 ⇒ cascade，不逐条记 hinge 根因'
    assert data['blueprints'][0]['beats'][0]['hinges'] == [{
        'hinge_id': 'h01_001', 'kind': '物件', 'source': None,
    }]


# ─────────────────────────────────────────────────────────────────────────────
# 四、round-trip：compile(project(artifact)) == artifact
# ─────────────────────────────────────────────────────────────────────────────

def test_projection_then_compile_reproduces_the_artifact_byte_for_byte():
    artifact, _ = _compile(_clean_data())
    blueprint = artifact['blueprints'][0]

    projected = canonical_ir_projection(copy.deepcopy(blueprint), CH1)
    assert 'summary' not in projected
    recompiled, acc = _compile({'blueprints': [projected]})
    assert acc['verbatim_failures'] == []
    got = recompiled['blueprints'][0]

    # nullable 地点字面与人物 id 均可直接往返，不需要显示字段或继承模式。
    assert got == blueprint


def test_projection_is_honest_about_failed_hinges():
    artifact, _ = _compile(_data(_beat(5, hinges=[
        {'kind': '物件', 'phrase': '这句话原文里没有'},
    ])))
    projected = canonical_ir_projection(artifact['blueprints'][0], CH1)
    assert projected['beats'][0]['hinges'] == \
        [{'kind': '物件', 'phrase': None}], '失败的 hinge 不许把 phrase 兜底成别的'


def test_places_are_normalized_without_a_display_only_probe():
    data = _data(_beat(2, place='井边'), _beat(4), _beat(5))
    artifact, prov = _compile(data)
    assert [b['place'] for b in artifact['blueprints'][0]['beats']] == \
        ['井边', None, None]
    assert set(prov) == {
        'verbatim_failures', 'fact_atom_failures', 'hinge_ambiguous_pending'}


# ── 关闭式候选集：分不开的人不下发 ────────────────────────────────────────

def _two_maids_one_name():
    """两个角色在 ch1 只剩同一个可见字面；第三个人可区分。

    `canonical_unique` 只保证**完整** canonical 唯一，而消歧靠的正是尾部区分词，
    `strip_distinguisher` 会把它剥掉 —— 这就是碰撞的真实来路。
    """
    characters = {
        'C001': {'canonical': '小宫女(打翻茶盏)',
                 'address': [{'surface': '小宫女', 'from_chapter': 1}]},
        'C002': {'canonical': '小宫女(捧灯)',
                 'address': [{'surface': '小宫女', 'from_chapter': 1}]},
        'C003': {'canonical': '黎玄',
                 'address': [{'surface': '黎玄', 'from_chapter': 1}]},
    }
    main = [{'chapter_ordinal': 1, 'text': '小宫女端着茶盏，黎玄看了一眼。'}]
    return characters, main


def test_indistinguishable_char_ids_are_never_offered_to_the_model():
    """⭐ **两个不可区分的键 = 让模型抛硬币定人物身份。**

    下发 `{"C001":["小宫女"],"C002":["小宫女"]}` 时，任选一个都能过 validator
    （它只查「在不在集合里」）、也都能过 `compile_v31_refs`（同一条集合）——
    于是一个**错误的人物身份**静默进蓝图，下游改名、视点、在场关系全跟着错。
    """
    from novel.stages.stage2.compile_v2 import visible_character_refs_with_drops

    characters, main = _two_maids_one_name()
    refs, dropped = visible_character_refs_with_drops(characters, main)

    assert sorted(refs[1]) == ['C003'], (
        f'分不开的两个人还在候选集里：{sorted(refs[1])}')
    assert dropped[1] == [['C001', 'C002']], dropped


def test_a_distinguishable_pair_is_still_offered():
    """只要还有一个字面能分开，就不该剔——剔多了是另一个方向的错。"""
    from novel.stages.stage2.compile_v2 import visible_character_refs_with_drops

    characters, main = _two_maids_one_name()
    characters['C002']['address'].append(
        {'surface': '捧灯宫女', 'from_chapter': 1})
    main = [{'chapter_ordinal': 1,
             'text': '小宫女端着茶盏，捧灯宫女跟在后面，黎玄看了一眼。'}]
    refs, dropped = visible_character_refs_with_drops(characters, main)

    assert sorted(refs[1]) == ['C001', 'C002', 'C003']
    assert dropped == {}


def test_the_plain_helper_and_the_accounted_one_agree():
    """三个调用点必须看到逐字相同的一份，否则模型的合法答案会在编译期炸。"""
    from novel.stages.stage2.compile_v2 import (
        visible_character_refs, visible_character_refs_with_drops,
    )

    characters, main = _two_maids_one_name()
    assert visible_character_refs(characters, main) == \
        visible_character_refs_with_drops(characters, main)[0]
