#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage2_compile_v2.py —— extraction IR 族（含 v2 引用式响应）确定性编译器的判定面。

覆盖四族差别（`novel/stages/stage2/compile_v2.py` 模块头）：
① 游标分割与全部非法形（稳定 finding 措辞，不抛裸异常）；
② hinge 拍区间内唯一定位（exact / normalized / 多处命中 / 他处 / 编的 / cascade）；
③ place 判别三态与相邻同层继承（链头落账）；
④ summary 注入与 debug 拼接。
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
    return {
        'end_para': end_para,
        'time_layer': layer,
        'place': place or {'mode': 'P_UNKNOWN'},
        'present_names': list(names),
        'event': event,
        'hinges': [dict(h) for h in hinges],
    }


def _narration() -> dict:
    return {'grammatical_person': 'third', 'narrator_name': None,
            'focalizer_name': None, 'pronoun_surfaces': []}


def _data(*beats: dict, ordinal: int = 1) -> dict:
    return {'blueprints': [{
        'chapter_ordinal': ordinal,
        'beats': list(beats),
        'narration': [_narration()],
    }]}


def _compile(data: dict, text: str = CH1, **kw):
    return compile_v2(data, _main_text(text), CHARACTERS, **kw)


def _clean_data() -> dict:
    return _data(
        _beat(2, place={'mode': 'P_EXPLICIT', 'name': '院子'},
              names=['李宝珠', '母亲'],
              event='母亲宣布第一课是挑水',
              hinges=[{'kind': '要害台词', 'phrase': '今天的第一课'}]),
        _beat(4, place={'mode': 'P_INHERIT'}, names=['李宝珠', '母亲'],
              event='她挑了两担水，母亲点评',
              hinges=[{'kind': '硬数字', 'phrase': '两担水'}]),
        _beat(5, layer='T_RECALL', place={'mode': 'P_INHERIT'}, names=['李宝珠'],
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
        assert CH1[s['start']:s['end']] == s['text'] and s['recovery'] == 'exact'
        assert 'end_para' not in b, '响应形的键不许留在产物上'

    # ② time_layer 前缀枚举映射回产物值。
    assert [b['time_layer'] for b in beats] == ['present', 'present', 'recall']

    # ③ place：EXPLICIT 取名；相邻同层 INHERIT 继承；跨层 INHERIT 落 null + 账。
    assert [b['place'] for b in beats] == ['院子', '院子', None]
    assert acc['place_inherit_without_base'] == ['b01_03']

    # ④ hinge 全部唯一命中，字节精确（逗号不是句末闭合标点，不被扩展吞入）。
    assert [h['source']['text'] for b in beats for h in b['hinges']] == \
        ['今天的第一课', '两担水', '三十文']

    # 人物解析照走（与 v3 同一份 resolve_character_refs）。
    assert beats[0]['present_char_ids'] == ['char_001', 'char_002']


def test_summaries_are_injected_and_debug_join_is_clearly_debug():
    injected, _ = _compile(_clean_data(), summaries={1: '这是摘要批给的。'})
    assert injected['blueprints'][0]['summary'] == '这是摘要批给的。'

    joined, _ = _compile(_clean_data())
    summary = joined['blueprints'][0]['summary']
    assert summary.startswith('本章依次：') and '两担水' in summary


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


def test_v2_refs_keeps_selected_ids_authoritative_and_projects_readable_names():
    raw = {'blueprints': [{
        'chapter_ordinal': 1,
        'beats': [{
            'end_para': 5,
            'time_layer': 'T_PRESENT',
            'place': '井边',
            'present_char_ids': ['char_002', 'char_001', 'char_002'],
            'event': '母亲让李宝珠挑水',
            'hinges': [{'kind': '硬数字', 'phrase': '两担水'}],
        }],
        'narration': [_narration()],
    }]}
    data, accounting = compile_v2_refs(
        raw, _main_text(), CHARACTERS, summaries={1: '母亲让李宝珠挑水。'})
    beat = data['blueprints'][0]['beats'][0]
    assert beat['present_char_ids'] == ['char_002', 'char_001']
    assert beat['present_names'] == ['母亲', '李宝珠']
    assert beat['place'] == '井边'
    assert accounting['char_refs']['invented'] == 0
    assert accounting['char_refs']['pronoun'] == 0


def test_v2_refs_rejects_an_id_outside_the_visible_closed_set():
    raw = {'blueprints': [{
        'chapter_ordinal': 1,
        'beats': [{
            'end_para': 5, 'time_layer': 'T_PRESENT', 'place': None,
            'present_char_ids': ['char_999'], 'event': '一件事', 'hinges': [],
        }],
        'narration': [_narration()],
    }]}
    with pytest.raises(ValueError, match='不在本章可见集合'):
        compile_v2_refs(raw, _main_text(), CHARACTERS, summaries={1: '摘要。'})


def test_v31_candidate_ids_compile_to_exact_sources_and_closed_narration_refs():
    candidates = hinge_candidates(CH1)
    hard_number = next(row for row in candidates
                       if row['source']['text'] == '两担')
    raw = {'blueprints': [{
        'chapter_ordinal': 1,
        'micro_events': [
            {'end_para': 3, 'cue': '母亲布置挑水，李宝珠完成'},
            {'end_para': 5, 'cue': '她把钱放到桌上'},
        ],
        'beats': [{
            'end_para': 3, 'time_layer': 'T_PRESENT', 'place': '井边',
            'present_char_ids': ['char_001', 'char_002'],
            'event': '母亲让李宝珠挑水，李宝珠完成两担',
            'hinges': [{'kind': '硬数字',
                        'candidate_id': hard_number['candidate_id']}],
        }, {
            'end_para': 5, 'time_layer': 'T_PRESENT', 'place': '屋内',
            'present_char_ids': ['char_001'],
            'event': '李宝珠把三十文钱放在桌上',
            'hinges': [{'kind': '硬数字', 'phrase': '三十文'}],
        }],
        'narration': [{
            'grammatical_person': 'third',
            'narrator_char_id': 'char_001',
            'focalizer_char_id': 'char_001',
            'pronoun_surfaces': [],
        }],
    }]}
    data, accounting = compile_v31_refs(
        raw, _main_text(), CHARACTERS, summaries={1: '本章完成挑水并收好钱。'})
    blueprint = data['blueprints'][0]
    assert 'micro_events' not in blueprint, '召回脚手架不得泄漏到正式产物'
    candidate_hinge = blueprint['beats'][0]['hinges'][0]
    assert candidate_hinge['source'] == hard_number['source']
    assert blueprint['beats'][1]['hinges'][0]['source']['text'] == '三十文', \
        '候选覆盖不到时的 phrase 兜底仍走既有唯一定位'
    narration = blueprint['narration'][0]
    assert narration['narrator_char_id'] == 'char_001'
    assert narration['focalizer_char_id'] == 'char_001'
    assert narration['narrator_name'] == '李宝珠'
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

    assert len(candidates) == 32
    assert any('因为真相终于揭开' in surface for surface in surfaces), \
        '显式因果族必须有保底，不能被章首引号填满候选表'
    assert '「最后的答复」' in surfaces, \
        '同族也要沿章节位置取样，不能永远只留下前 32 条对白'
    assert [row['candidate_id'] for row in candidates] == [
        f'h{index:03d}' for index in range(1, 33)]
    assert [row['source']['start'] for row in candidates] == sorted(
        row['source']['start'] for row in candidates)


def test_v31_rejects_a_candidate_from_outside_its_beat_even_without_call_gate():
    raw = {'blueprints': [{
        'chapter_ordinal': 1,
        'micro_events': [{'end_para': 2, 'cue': '开课'},
                         {'end_para': 5, 'cue': '收钱'}],
        'beats': [{
            'end_para': 2, 'time_layer': 'T_PRESENT', 'place': None,
            'present_char_ids': [], 'event': '母亲开课',
            # h004 在第 5 段，不能挂到止于第 2 段的拍。
            'hinges': [{'kind': '硬数字', 'candidate_id': 'h004'}],
        }, {
            'end_para': 5, 'time_layer': 'T_PRESENT', 'place': None,
            'present_char_ids': [], 'event': '事情收束', 'hinges': [],
        }],
        'narration': [{
            'grammatical_person': 'third', 'narrator_char_id': None,
            'focalizer_char_id': None, 'pronoun_surfaces': [],
        }],
    }]}
    with pytest.raises(ValueError, match='不在所属拍'):
        compile_v31_refs(raw, _main_text(), CHARACTERS, summaries={1: '摘要。'})


def test_v31_projection_is_a_valid_repair_baseline_and_round_trips():
    compiled, _ = compile_v31_refs({
        'blueprints': [{
            'chapter_ordinal': 1,
            'micro_events': [{'end_para': 5, 'cue': '完成挑水'}],
            'beats': [{
                'end_para': 5, 'time_layer': 'T_PRESENT', 'place': '井边',
                'present_char_ids': ['char_001'], 'event': '李宝珠完成挑水',
                'hinges': [{'kind': '硬数字', 'candidate_id': 'h003'}],
            }],
            'narration': [{
                'grammatical_person': 'third', 'narrator_char_id': 'char_001',
                'focalizer_char_id': 'char_001', 'pronoun_surfaces': [],
            }],
        }],
    }, _main_text(), CHARACTERS, summaries={1: '李宝珠完成挑水。'})
    artifact = compiled['blueprints'][0]
    projected = canonical_ir_projection_v31(artifact, CH1)
    assert projected['micro_events'][0]['cue'] == artifact['beats'][0]['event']
    assert projected['micro_events'][0]['function'] == 'M_ACTION'
    assert projected['micro_events'][0]['fidelity'] == 'F_FACT'
    assert set(projected['narration'][0]) == {
        'grammatical_person', 'narrator_char_id', 'focalizer_char_id',
        'pronoun_surfaces'}
    replayed, _ = compile_v31_refs(
        {'blueprints': [projected]}, _main_text(), CHARACTERS,
        summaries={1: artifact['summary']})
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
    assert acc['boundary_nudges'] == []
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
    assert acc['boundary_nudges'] == [
        ('b01_01', 2, 3, '止段没盖住本拍自己的 hinge')]
    beats = data['blueprints'][0]['beats']
    assert beats[0]['hinges'][0]['source']['text'] == '两担水'
    # 外扩改的是**两拍**：下一拍的起点跟着挪，且两拍仍然首尾相接。
    assert beats[1]['span']['start'] == beats[0]['span']['end'] + 1


def test_a_tail_one_paragraph_short_is_nudged_to_the_last_paragraph():
    data, acc = _compile(_data(_beat(2), _beat(4)))
    assert _span_causes(acc) == []
    assert acc['boundary_nudges'] == [
        ('b01_02', 4, 5, '末拍没有到达本章最后一段')]
    assert data['blueprints'][0]['beats'][-1]['span']['end'] == len(CH1)


def test_a_nudge_that_would_starve_the_next_beat_is_refused():
    """外扩不许越过下一拍的止段——挤空它比留着 drift 更坏。"""
    _, acc = _compile(_data(
        _beat(2, hinges=[{'kind': '硬数字', 'phrase': '两担水'}]),   # 在第 3 段
        _beat(3),                                                    # 下一拍只有第 3 段
        _beat(5),
    ))
    assert acc['boundary_nudges'] == []
    assert _hinge_rows(acc)['b01_01'] == '短语不在它所属拍的区间里，而出现在别处'


def test_a_nudge_that_would_steal_the_next_beats_own_anchor_is_refused():
    """下一拍原本定得住的锚点，不许被外扩抢走。"""
    _, acc = _compile(_data(
        _beat(2, hinges=[{'kind': '硬数字', 'phrase': '两担水'}]),   # 在第 3 段
        _beat(4, hinges=[{'kind': '硬数字', 'phrase': '两担水'}]),   # 它自己也压在第 3 段
        _beat(5),
    ))
    assert acc['boundary_nudges'] == []
    assert _hinge_rows(acc)['b01_01'] == '短语不在它所属拍的区间里，而出现在别处'


def test_an_ambiguous_phrase_is_never_fixed_by_moving_a_boundary():
    """拍内已经多处命中 ⇒ 那是候选选择那条路，边界一动歧义只会更糊。"""
    text = '她把纸鸢递过去。\n他接过纸鸢看了半天。\n收场白。'
    _, acc = _compile(_data(_beat(2, hinges=[{'kind': '物件', 'phrase': '纸鸢'}]),
                            _beat(3)), text=text)
    assert acc['boundary_nudges'] == []
    assert acc['hinge_ambiguous_pending'] == [('b01_01', '物件', '纸鸢')]


def test_a_nudge_that_would_create_ambiguity_is_refused():
    """外扩后仍要**唯一**命中：修不好就不改，编译器不做无效改写。"""
    text = '开场白。\n她把纸鸢递过去，他又把纸鸢还回来。\n收场白。'
    _, acc = _compile(_data(_beat(1, hinges=[{'kind': '物件', 'phrase': '纸鸢'}]),
                            _beat(3)), text=text)
    assert acc['boundary_nudges'] == []
    assert _hinge_rows(acc)['b01_01'] == '短语不在它所属拍的区间里，而出现在别处'


def test_a_chapter_with_an_illegal_end_para_is_never_nudged():
    """止段序列已经坏了，这一章的分段主张就不成立——不在坏序列上推边界。"""
    _, acc = _compile(_data(
        _beat(99, hinges=[{'kind': '硬数字', 'phrase': '两担水'}]),
        _beat(5),
    ))
    assert acc['boundary_nudges'] == []


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


def test_a_byte_mismatched_phrase_is_recovered_in_content_space_with_a_ledger_row():
    data, acc = _compile(_data(_beat(5, hinges=[
        {'kind': '要害台词', 'phrase': '今天的第一课,'},   # 半角逗号，原文是全角
    ])))
    hinge = data['blueprints'][0]['beats'][0]['hinges'][0]
    assert hinge['source']['recovery'] == 'normalized'
    assert ('b01_01', 'hinge.phrase', '今天的第一课,', hinge['source']['text']) \
        in acc['verbatim_rewrites']


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
    assert data['blueprints'][0]['beats'][0]['hinges'] == \
        [{'kind': '物件', 'source': None}]


# ─────────────────────────────────────────────────────────────────────────────
# 四、round-trip：compile(project(artifact)) == artifact
# ─────────────────────────────────────────────────────────────────────────────

def test_projection_then_compile_reproduces_the_artifact_byte_for_byte():
    artifact, _ = _compile(_clean_data(), summaries={1: '一段摘要。'})
    blueprint = artifact['blueprints'][0]

    projected = canonical_ir_projection(copy.deepcopy(blueprint), CH1)
    assert 'summary' not in projected
    recompiled, acc = _compile({'blueprints': [projected]},
                               summaries={1: '一段摘要。'})
    assert acc['verbatim_failures'] == []
    got = recompiled['blueprints'][0]

    # ⚠ P_INHERIT 已坍缩：投影只能给 P_EXPLICIT(同名)/P_UNKNOWN ⇒ place 值不变，
    #   但 place_inherit_without_base 账不会复现——round-trip 断言的是**产物**相等。
    assert got == blueprint


def test_projection_is_honest_about_failed_hinges():
    artifact, _ = _compile(_data(_beat(5, hinges=[
        {'kind': '物件', 'phrase': '这句话原文里没有'},
    ])))
    projected = canonical_ir_projection(artifact['blueprints'][0], CH1)
    assert projected['beats'][0]['hinges'] == \
        [{'kind': '物件', 'phrase': None}], '失败的 hinge 不许把 phrase 兜底成别的'


# ─────────────────────────────────────────────────────────────────────────────
# 五、`place_explicit_unlocatable`：`extraction-ir-v1.1` 的配套探针
# ─────────────────────────────────────────────────────────────────────────────
#
# 【它在防什么】ir1.1 把 `P_EXPLICIT` 从「三选一」提回「首选档」，为的是救回地点
# 填充率（ir1 相对 v3 掉 −59%，两个模型上都掉 ⇒ 契约的责任）。加压时模型会找最省力
# 的出路，而 place 是**单字段**、没有别的字段可牺牲 ⇒ 唯一的规避路径是**编一个地点**。
#
# ⚠⚠ 【匹配面是「截止本章正文」，这一族测试的核心就是钉住它】三个面在盘上
#     （Pro×v3 无情道，132 个有地点的拍）实测：本拍区间 **78%** 找不到 ·
#     本章全文 42% · 截止本章正文 **13%**。按本拍区间判等于让噪声淹死信号。
# ⚠ 全族**只断言账，不断言产物被改**——它是 INFO，不改产物是它定义的一部分。


def test_a_place_written_in_the_source_is_not_flagged():
    """地点名在截止本章的正文里找得到 ⇒ 不记账。"""
    _, prov = _compile(_data(_beat(2, place={'mode': 'P_EXPLICIT', 'name': '井边'}),
                             _beat(5)))
    assert prov['place_explicit_unlocatable'] == []


def test_a_place_outside_its_own_beat_is_still_not_flagged():
    """⚠⚠ **本族最重要的一条**：地点写在**别的拍**里照样算找得到。

    「三十文」只在第 5 段出现，而第一拍只盖 1–2 段。按「本拍区间」判会把它报成
    找不到——那正是 78% 假阳率的来源，也正是这条探针曾经的错误设计。
    """
    _, prov = _compile(_data(_beat(2, place={'mode': 'P_EXPLICIT', 'name': '三十文'}),
                             _beat(5)))
    assert prov['place_explicit_unlocatable'] == [], \
        '匹配面退回「本拍区间」了——合法的上文地点会被整批误报'


def test_a_place_absent_from_the_source_is_flagged_but_left_in_the_artifact():
    """原文里根本没有那个说法 ⇒ 记一笔，**产物照留**。"""
    artifact, prov = _compile(_data(
        _beat(2, place={'mode': 'P_EXPLICIT', 'name': '雁门关'}), _beat(5)))
    assert prov['place_explicit_unlocatable'] == [('b01_01', '雁门关')]
    assert artifact['blueprints'][0]['beats'][0]['place'] == '雁门关', \
        'INFO 探针不许改产物——报出来是给人读的，不是拿来兜底的'


def test_a_repeated_place_still_counts_as_found():
    """多处命中算找得到：地点名重复出现正常，它不像 hinge 需要唯一。"""
    assert CH1.count('母亲') >= 2, '这条测试的前提是该字面出现 ≥2 次'
    _, prov = _compile(_data(_beat(5, place={'mode': 'P_EXPLICIT', 'name': '母亲'})))
    assert prov['place_explicit_unlocatable'] == []


def test_the_scope_stops_at_the_current_chapter():
    """只在**后文**出现的说法算找不到——那本身就是后见之明。

    两章：`雁门关` 只在第 2 章。第 1 章的拍引用它 ⇒ 记账；第 2 章的拍 ⇒ 不记。
    """
    ch2 = '他们一路向北，终于望见雁门关的城楼。\n关下风沙很大。'
    mt = [{'chapter_ordinal': 1, 'text': CH1}, {'chapter_ordinal': 2, 'text': ch2}]
    data = {'blueprints': [
        {'chapter_ordinal': 1, 'beats': [_beat(5, place={'mode': 'P_EXPLICIT',
                                                         'name': '雁门关'})],
         'narration': [_narration()]},
        {'chapter_ordinal': 2, 'beats': [_beat(2, place={'mode': 'P_EXPLICIT',
                                                         'name': '雁门关'})],
         'narration': [_narration()]},
    ]}
    _, prov = compile_v2(data, mt, CHARACTERS, summaries={1: '甲。', 2: '乙。'})
    assert [b for b, _n in prov['place_explicit_unlocatable']] == ['b01_01'], \
        '「截止本章」这条界线没生效：要么整本都能引，要么第 2 章自己也被误报'


def test_the_scope_is_built_from_main_text_not_from_the_compiled_batch():
    """⚠ 只编译第 2 章时，第 1 章的正文仍要算进匹配面。

    定向重生成（`--chapters`）只把几章带进 `blueprints`，而 `main_text` 恒是全书。
    从 batch 累积会让前面的章凭空消失，于是「原文里有没有」被算成「这一批里有没有」。
    """
    ch2 = '她又回到那口井边，水汽照旧。'
    mt = [{'chapter_ordinal': 1, 'text': CH1}, {'chapter_ordinal': 2, 'text': ch2}]
    only_ch2 = {'blueprints': [
        {'chapter_ordinal': 2, 'beats': [_beat(1, place={'mode': 'P_EXPLICIT',
                                                         'name': '肩膀'})],
         'narration': [_narration()]}]}
    _, prov = compile_v2(only_ch2, mt, CHARACTERS, summaries={2: '乙。'})
    assert '肩膀' in CH1 and '肩膀' not in ch2, '前提：该字面只在第 1 章'
    assert prov['place_explicit_unlocatable'] == [], \
        '匹配面是从 blueprints 累积的——定向重生成会把上游章当成不存在'


def test_inherited_and_unknown_places_never_reach_the_probe():
    """只有 `P_EXPLICIT` 走探针。

    ⚠ 继承来的地点即使在原文里找不到也不记账：`P_INHERIT` 的语义本来就是
    「这一拍没写、延续上一拍」，拿字面去判它等于判它的定义违规。
    """
    data = _data(_beat(2, place={'mode': 'P_EXPLICIT', 'name': '井边'}),
                 _beat(4, place={'mode': 'P_INHERIT'}),
                 _beat(5, place={'mode': 'P_UNKNOWN'}))
    artifact, prov = _compile(data)
    assert prov['place_explicit_unlocatable'] == []
    assert [b['place'] for b in artifact['blueprints'][0]['beats']] == \
        ['井边', '井边', None], '继承链本身没被这次改动动过'


def test_the_probe_key_is_always_present_even_when_empty():
    """键**恒在**（空表也写）：`bounded-fidelity-v3` 缺键报「不适用」，
    而 ir1 系产物空表是「查过、没有」——两者在报告上必须分得开。"""
    _, prov = _compile(_clean_data())
    assert 'place_explicit_unlocatable' in prov
