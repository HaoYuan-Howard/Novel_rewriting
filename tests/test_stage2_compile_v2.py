#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage2_compile_v2.py —— `extraction-ir-v1` 确定性编译器的判定面。

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

from contracts.invariants import (
    HINGE_FAILURE_ROUTE, REPAIR_ROUTE, SPAN_FAILURE_CAUSES,
)
from novel.stages.stage2.compile_v2 import (
    canonical_ir_projection, compile_v2,
)

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
