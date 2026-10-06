#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_associate_v3 —— 关联协议 v3（CS-H H4，RC-65）的判据套件。

【验证边界】维度句柄层的合同行为（spec repair_convergence.md CS-H E 节）：
  • 同（作用对象，键）地址的全部写入聚合成一个维度句柄——请求面与配对面
    **不随同键写入数增长**（完成判据①；v2 的 89/90 空操作配对按构造不可能）；
  • 章内已自开自闭的 close 不进可配对闭集（完成判据②）；
  • 维度配对只对键不同但同维的维度有意义；规范键由编译器按地址传播到整类
    （两类全部写入，含多写维度）；
  • 条目定位器（RC-57 覆盖）：机械验收与干跑未决按句柄反查配对条目路径，
    需新增条目或歧义即返回 None 退整份。
语义判断（哪些键同维）归付费 semantic unit，保持 unver 披露；本文件全部
确定性构造，零模型调用。
"""
from __future__ import annotations

import pytest

from novel.stages.stage2 import (
    associate_observation_prompt, associate_patch_locate,
    compile_associate_observation, project_association_space,
)
from novel.stages.stage2.associate import state_dimension_address
from tests.test_str06_stage2_publish import (
    CHAR_FJ, CHAR_YN, CPV, _basis, _beat, _book_wire, _dim_handle,
    _initial_atom, _observations, _plain_effect, _quiet_beat,
)


def _state_chapter(no: int, beats: int, target: dict, key: str) -> dict:
    """第 no 章 beats 拍，每拍写一条同键状态（判据①的重复写入源）。"""
    return {'protocol_version': CPV, 'beats': [
        _beat(f'第{no}章第{i}拍写状态', (f'e{no:02d}_01',), participants=('fu_jun',),
              scene_boundary='new' if i == 1 else 'continue',
              effects=[_plain_effect(target, key, f'值{no}{i}', ref=f'e{no:02d}_01')])
        for i in range(1, beats + 1)]}


def _same_key_wires(writes_per_chapter: int) -> dict:
    """book 初态 trust＋三章各 N 条 trust 写入（同键同对象的重复写入底座）。"""
    book = _book_wire()
    book['initial_state'] = [_initial_atom(CHAR_FJ, 'trust', '低')]
    return {'book': book,
            **{f'ch{no:02d}': _state_chapter(no, writes_per_chapter, CHAR_FJ, 'trust')
               for no in (1, 2, 3)}}


def test_same_key_writes_collapse_into_one_dimension_and_output_does_not_grow():
    """完成判据①：同键状态很多时，维度面与配对面不随写入数增长。

    v2 里模型要为每对同键写入选 same_state_dimension（csf4 根 90 对里 89 对
    是这种空操作）；v3 里它们已是一个维度句柄——配对条目数＝0，请求面的
    维度行数恒为 1。"""
    small = _same_key_wires(1)      # 1 初态＋3 写入
    large = _same_key_wires(3)      # 1 初态＋9 写入
    book_s, chapters_s = _observations(small['book'], {n: small[f'ch{n:02d}'] for n in (1, 2, 3)})
    book_l, chapters_l = _observations(large['book'], {n: large[f'ch{n:02d}'] for n in (1, 2, 3)})
    space_s = project_association_space(book_s, chapters_s)
    space_l = project_association_space(book_l, chapters_l)
    assert len(space_s.states) == 4 and len(space_l.states) == 10, '写入句柄如实逐条登记'
    address = state_dimension_address(CHAR_FJ, 'trust')
    for space in (space_s, space_l):
        dims = [h for h, e in space.dimensions.items()
                if state_dimension_address(e.target, e.key) == address]
        assert dims == ['dim01'], '同键同对象＝一个维度句柄（首次写入序编号）'
    prompt_s = associate_observation_prompt(space_s)
    prompt_l = associate_observation_prompt(space_l)
    assert prompt_s.count('.trust') == 1 and prompt_l.count('.trust') == 1, \
        '请求面每个（作用对象，键）地址只出现一行'
    # 维度面行数不随写入数增长（多出的只是「（共 N 次写入）」计数与拍事件行——
    # 后者是跨章因果的合法输入，不是状态配对面）。
    assert prompt_l.count('[dim') == prompt_s.count('[dim')
    line_s = next(l for l in prompt_s.splitlines() if '.trust ' in l or '.trust（' in l)
    line_l = next(l for l in prompt_l.splitlines() if '.trust ' in l or '.trust（' in l)
    assert line_l.startswith(line_s.split('（')[0]) and len(line_l) - len(line_s) <= 4
    # 空配对即合法结论：同键合并不需要任何 same_state_dimension 条目。
    wire = {'protocol_version': 'stage2-associate-v3',
            'same_proposition': [], 'same_state_dimension': [], 'loop_pairing': [],
            'cross_chapter_cause': [], 'scene_continuation': []}
    observation, errors = compile_associate_observation(wire, space_l)
    assert observation is not None and errors == []


def test_cross_key_dimension_pairing_propagates_canonical_key_to_every_write():
    """规范键按地址传播整类（RC-65）：两个维度各有多条写入，配对后两类全部
    写入都归一到首次写入最早者的键——不只配对条目点到的那两条。"""
    from novel.stages.stage2.compile import _BookCompile
    wires = _same_key_wires(2)
    wires['ch03'] = {'protocol_version': CPV, 'beats': [
        _beat('近义键写入一', ('e03_01',), participants=('fu_jun',),
              effects=[_plain_effect(CHAR_FJ, 'trust_level', '回升', ref='e03_01')]),
        _beat('近义键写入二', ('e03_02',), participants=('fu_jun',),
              scene_boundary='continue',
              effects=[_plain_effect(CHAR_FJ, 'trust_level', '高', ref='e03_02')])]}
    book, chapters = _observations(wires['book'], {n: wires[f'ch{n:02d}'] for n in (1, 2, 3)})
    space = project_association_space(book, chapters)
    left, right = _dim_handle(wires, CHAR_FJ, 'trust'), _dim_handle(wires, CHAR_FJ, 'trust_level')
    observation, errors = compile_associate_observation(
        {'protocol_version': 'stage2-associate-v3', 'same_proposition': [],
         'same_state_dimension': [{'left': left, 'right': right}],
         'loop_pairing': [], 'cross_chapter_cause': [], 'scene_continuation': []}, space)
    assert observation is not None and errors == [], errors
    state = _BookCompile(slugs=set(book.characters), associate=observation, space=space)
    undecided: list[str] = []
    for handle, entry in space.states.items():
        key = state.state_classes.canonical_key(
            handle, entry.target, entry.key, handle, undecided)
        assert key == 'trust', f'{handle} 应归一到类内最早的 trust'
    assert undecided == []


def test_in_chapter_self_closed_close_is_not_pairable():
    """完成判据②：章内已自开自闭的 close 不进可配对闭集——提示词不列、wire
    引用即精确 issue（带「已在章内自开自闭」说明），不是普通未知句柄。"""
    book = _book_wire()
    ch01 = {'protocol_version': CPV, 'beats': [
        _beat('悬念开场', ('e01_01',), loops=[
            {'ref': 'q_open', 'op': 'open', 'question': '床底的人是谁？'}]),
        _beat('跨章悬念开', ('e01_02',), scene_boundary='continue', loops=[
            {'ref': 'q_far', 'op': 'open', 'question': '借种之约如何收场？'}])]}
    ch02 = {'protocol_version': CPV, 'beats': [
        _beat('章内自开自闭', ('e02_01',), loops=[
            {'ref': 'q_local', 'op': 'open', 'question': '今晚还有响动吗？'}]),
        _beat('章内收束', ('e02_02',), scene_boundary='continue', loops=[
            {'ref': 'q_local', 'op': 'close', 'resolution': '没有响动'}])]}
    book_obs, chapters = _observations(book, {1: ch01, 2: ch02})
    space = project_association_space(book_obs, chapters)
    assert 'ch02.q_local' in space.loop_closes
    assert 'ch02.q_local' not in space.pairable_closes, '章内自开自闭不进可配对闭集'
    assert 'ch01.q_open' not in space.pairable_closes, '悬空 open 也不是可配对 close'
    prompt = associate_observation_prompt(space)
    assert '悬念 close [ch02.q_local]' not in prompt, '提示词不列章内已闭合 close'
    wire = {'protocol_version': 'stage2-associate-v3', 'same_proposition': [],
            'same_state_dimension': [],
            'loop_pairing': [{'open': 'ch01.q_far', 'close': 'ch02.q_local'}],
            'cross_chapter_cause': [], 'scene_continuation': []}
    observation, errors = compile_associate_observation(wire, space)
    assert observation is None
    assert any('已在章内自开自闭' in e and getattr(e, 'code', '') == 'association_unknown_handle'
               for e in errors), errors
    # 绿对照：真正跨章的 close 仍在可配对闭集里。
    ch03 = {'protocol_version': CPV, 'beats': [
        _beat('跨章收束', ('e03_01',), loops=[
            {'ref': 'q_far', 'op': 'close', 'resolution': '约成身退'}])]}
    book_obs, chapters = _observations(book, {1: ch01, 2: ch02, 3: ch03})
    space = project_association_space(book_obs, chapters)
    wire['loop_pairing'] = [{'open': 'ch01.q_far', 'close': 'ch03.q_far'}]
    observation, errors = compile_associate_observation(wire, space)
    assert observation is not None and errors == [], errors


def test_v2_write_handle_pairings_are_refused_with_the_dimension_closed_set():
    """v2 wire（配对逐条写入句柄）对 v3 闭集精确拒绝：写入句柄不在维度闭集里。"""
    wires = _same_key_wires(1)
    wires['ch03'] = {'protocol_version': CPV, 'beats': [
        _beat('近义键', ('e03_01',), participants=('fu_jun',),
              effects=[_plain_effect(CHAR_FJ, 'trust_level', '回升', ref='e03_01')]),
        _quiet_beat('收尾', 'e03_02')]}
    book, chapters = _observations(wires['book'], {n: wires[f'ch{n:02d}'] for n in (1, 2, 3)})
    space = project_association_space(book, chapters)
    wire = {'protocol_version': 'stage2-associate-v3', 'same_proposition': [],
            'same_state_dimension': [{'left': 'book.e1.trust',
                                      'right': 'ch03.b1.e1.trust_level'}],
            'loop_pairing': [], 'cross_chapter_cause': [], 'scene_continuation': []}
    observation, errors = compile_associate_observation(wire, space)
    assert observation is None and len(errors) == 2, errors
    assert all('不在投影闭集内' in e for e in errors)
    assert all(getattr(e, 'code', '') == 'association_unknown_handle' for e in errors)


def test_associate_patch_locate_maps_errors_to_pairing_entry_paths():
    """条目定位（RC-57）：机械验收错误按句柄反查条目路径 `(kind, i)`；同一批
    错误对象原样返回（ask_json 以集合相等核对覆盖）。"""
    book = _book_wire()
    ch01 = {'protocol_version': CPV, 'beats': [
        _beat('一拍', ('e01_01',), participants=('fu_jun',),
              effects=[_plain_effect(CHAR_FJ, 'trust', '低')]),
        _beat('二拍', ('e01_02',),
              effects=[_plain_effect(CHAR_YN, 'mood', '安', ref='e01_02')])]}
    book_obs, chapters = _observations(book, {1: ch01})
    space = project_association_space(book_obs, chapters)
    good = {'left': _dim_handle({'book': book, 'ch01': ch01}, CHAR_FJ, 'trust'),
            'right': _dim_handle({'book': book, 'ch01': ch01}, CHAR_YN, 'mood')}
    wire = {'protocol_version': 'stage2-associate-v3', 'same_proposition': [],
            'same_state_dimension': [good, {'left': 'dim99', 'right': 'dim98'}],
            'loop_pairing': [], 'cross_chapter_cause': [], 'scene_continuation': []}
    _observation, errors = compile_associate_observation(wire, space)
    assert len(errors) == 2, '只有第二个条目的两个句柄不在闭集'
    targets = associate_patch_locate(wire, errors, space=space)
    assert targets == [(('same_state_dimension', 1), list(errors))], targets


def test_associate_patch_locate_returns_none_for_unmappable_or_ambiguous_errors():
    """定位不了的退整份（None）：错误指向尚不存在的条目（如悬空命题引用需要
    新增配对）、或句柄在多条配对里出现（无法唯一指定替换目标）。"""
    from novel.stages.stage2.associate import association_issue
    book = _book_wire()
    ch01 = {'protocol_version': CPV, 'beats': [
        _beat('一拍', ('e01_01',), participants=('fu_jun',),
              effects=[_plain_effect(CHAR_FJ, 'trust', '低')])]}
    book_obs, chapters = _observations(book, {1: ch01})
    space = project_association_space(book_obs, chapters)
    dim = _dim_handle({'book': book, 'ch01': ch01}, CHAR_FJ, 'trust')
    wire = {'protocol_version': 'stage2-associate-v3', 'same_proposition': [],
            'same_state_dimension': [{'left': dim, 'right': dim},
                                     {'left': dim, 'right': dim}],
            'loop_pairing': [], 'cross_chapter_cause': [], 'scene_continuation': []}
    # 悬空命题引用（需要新增 same_proposition 条目）：句柄不在任何既有条目里。
    dangling = association_issue('proposition_unpaired', ('ch03.p_hide',), '…')
    assert associate_patch_locate(wire, [dangling], space=space) is None
    # 歧义：错误句柄命中两条配对。
    ambiguous = association_issue('state_target_mismatch', (dim,), '…')
    assert associate_patch_locate(wire, [ambiguous], space=space) is None
    # 无结构化位置的字符串错误（存量兼容面）同样退整份。
    assert associate_patch_locate(wire, ['裸字符串错误'], space=space) is None


def test_dimension_of_maps_every_write_to_its_dimension():
    """登记表内部一致性：每个写入句柄都映射到其地址维度；维度的 writes 覆盖
    该地址全部写入且按登记序（审计读数面）。"""
    wires = _same_key_wires(2)
    book, chapters = _observations(wires['book'], {n: wires[f'ch{n:02d}'] for n in (1, 2, 3)})
    space = project_association_space(book, chapters)
    assert set(space.dimension_of) == set(space.states)
    address = state_dimension_address(CHAR_FJ, 'trust')
    trust_dim = space.dimension_of['book.e1.trust']
    assert space.dimensions[trust_dim].writes == tuple(
        h for h, e in space.states.items()
        if state_dimension_address(e.target, e.key) == address)
    assert space.dimensions[trust_dim].first_rank == (0, 0, 1), '首次写入＝book 初态'
