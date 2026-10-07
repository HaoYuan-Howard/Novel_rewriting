#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_associate_v4 —— 关联协议 v4（CS-S Phase 2，S2-9 落地）的判据套件。

【验证边界】条目级窄 resolver 的合同行为（docs/REQUIREMENTS.md CS-S Phase 2）：
  • 候选由代码给出（确定性相似度＋结构投影）——近义命题对（含 facts 词表）、
    同作用对象的近义键维度对、跨章 open×close（时序正）、相邻章 seam 续场对；
    无候选＝零调用（`AssociationCandidates.__bool__`）；
  • 同（作用对象，键）地址的全部写入仍聚合为一个维度句柄（RC-65 延续），
    章内已自开自闭的 close 不进可配对闭集；
  • 逐候选恰答一次：闭集＝候选集合（v2 wire 的写入句柄配对、编造句柄对都拒），
    漏答／多答＝机械缺陷；无序候选左右互换视为同一候选；
  • verdict 只执行 same／pair／continue——distinct／unresolved 保持分离，
    compile 消费面在 `tests/test_str06_stage2_publish.py`；
  • 因果候选（D-2 A）＝loop 配对拍对＋facts 流拍对，从**已判定的身份答案**派生。
语义判断（哪些候选是同一）归付费 semantic unit，保持 unver 披露；本文件全部
确定性构造，零模型调用。
"""
from __future__ import annotations

import re

from contracts.schema.observation import AssociateObservation
from novel.stages.stage2 import (
    associate_candidates_prompt, associate_patch_locate, near_synonym_pair,
    compile_associate_observation, compile_cross_cause, project_association_space,
    project_identity_candidates, project_cause_candidates,
)
from novel.stages.stage2.associate import state_dimension_address
from tests.test_str06_stage2_publish import (
    CHAR_FJ, CHAR_YN, CPV, _basis, _beat, _book_wire, _dim_handle,
    _initial_atom, _observations, _plain_effect, _quiet_beat,
)


def _state_chapter(no: int, beats: int, target: dict, key: str) -> dict:
    """第 no 章 beats 拍，每拍写一条同键状态（重复写入源的判据①底座）。"""
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


def _compiled(wires: dict):
    numbers = sorted(int(m.group(1)) for key in wires
                     if (m := re.fullmatch(r'ch(\d{2})', key)))
    book, chapters = _observations(wires['book'], {
        n: wires[f'ch{n:02d}'] for n in numbers})
    return book, chapters, project_association_space(book, chapters)


def _answer_wire(candidates, **over) -> dict:
    """恰答全部候选的默认答案（全部 distinct），测试按需覆写个别类。"""
    wire = {
        'protocol_version': 'stage2-associate-v4',
        'same_proposition': [
            {'left': c.left, 'right': c.right, 'verdict': 'distinct'}
            for c in candidates.same_proposition],
        'same_state_dimension': [
            {'left': c.left, 'right': c.right, 'verdict': 'distinct'}
            for c in candidates.same_state_dimension],
        'loop_pairing': [
            {'open': c.open, 'close': c.close, 'verdict': 'distinct'}
            for c in candidates.loop_pairing],
        'cross_chapter_cause': [],
        'scene_continuation': [
            {'left': c.left, 'right': c.right, 'verdict': 'distinct'}
            for c in candidates.scene_continuation],
    }
    wire.update(over)
    return wire


def test_same_key_writes_collapse_into_one_dimension_and_no_candidates():
    """判据①（RC-65 延续）：同键状态很多时只有一个维度句柄；同键 ⇒ 不是
    近义键 ⇒ 零维度候选——v4 请求面不随写入数增长，无候选即零调用。"""
    small = _same_key_wires(1)      # 1 初态＋3 写入
    large = _same_key_wires(3)      # 1 初态＋9 写入
    book_s, _ch_s, space_s = _compiled(small)
    book_l, _ch_l, space_l = _compiled(large)
    assert len(space_s.states) == 4 and len(space_l.states) == 10, '写入句柄如实逐条登记'
    address = state_dimension_address(CHAR_FJ, 'trust')
    for space in (space_s, space_l):
        dims = [h for h, e in space.dimensions.items()
                if state_dimension_address(e.target, e.key) == address]
        assert dims == ['dim01'], '同键同对象＝一个维度句柄（首次写入序编号）'
    candidates_l = project_identity_candidates(space_l, book_l)
    candidates_s = project_identity_candidates(space_s, book_s)
    assert candidates_l.same_state_dimension == (), '同键写入不是近义键候选'
    assert candidates_s.same_state_dimension == ()
    # 全部候选判 distinct 即合法结论（无同键空操作——那是 v2 的负担）。
    observation, errors = compile_associate_observation(
        _answer_wire(candidates_l), space_l, candidates_l)
    assert observation is not None and errors == [], errors


def test_cross_key_dimension_candidate_and_canonical_key_propagation():
    """近义键候选（规范键按地址传播整类，RC-65）：两个维度各有多条写入，
    verdict=same 后两类全部写入归一到首次写入最早者的键；distinct 保持分离。"""
    from novel.stages.stage2.compile import _BookCompile
    wires = _same_key_wires(2)
    wires['ch03'] = {'protocol_version': CPV, 'beats': [
        _beat('近义键写入一', ('e03_01',), participants=('fu_jun',),
              effects=[_plain_effect(CHAR_FJ, 'trust_level', '回升', ref='e03_01')]),
        _beat('近义键写入二', ('e03_02',), participants=('fu_jun',),
              scene_boundary='continue',
              effects=[_plain_effect(CHAR_FJ, 'trust_level', '高', ref='e03_02')])]}
    book, _chapters, space = _compiled(wires)
    candidates = project_identity_candidates(space, book)
    pairs = {(c.left, c.right) for c in candidates.same_state_dimension}
    left, right = _dim_handle(wires, CHAR_FJ, 'trust'), _dim_handle(wires, CHAR_FJ, 'trust_level')
    assert (left, right) in pairs or (right, left) in pairs, pairs
    prompt = associate_candidates_prompt(candidates)
    assert 'trust ×' in prompt and 'trust_level' in prompt
    same = _answer_wire(candidates, same_state_dimension=[
        {'left': left, 'right': right, 'verdict': 'same'}])
    observation, errors = compile_associate_observation(same, space, candidates)
    assert observation is not None and errors == [], errors
    state = _BookCompile(slugs=set(book.characters), associate=observation, space=space)
    undecided: list[str] = []
    for handle, entry in space.states.items():
        key = state.state_classes.canonical_key(
            handle, entry.target, entry.key, handle, undecided)
        assert key == 'trust', f'{handle} 应归一到类内最早的 trust'
    assert undecided == []
    distinct = _answer_wire(candidates)
    observation, errors = compile_associate_observation(distinct, space, candidates)
    assert observation is not None and errors == [], errors
    state = _BookCompile(slugs=set(book.characters), associate=observation, space=space)
    keys = {state.state_classes.canonical_key(handle, entry.target, entry.key, handle, [])
            for handle, entry in space.states.items()}
    assert keys == {'trust', 'trust_level'}, 'distinct 保持分离'


def test_in_chapter_self_closed_close_is_not_pairable():
    """判据②（RC-65 延续）：章内已自开自闭的 close 不进候选；时序倒转
    （open 晚于 close）不生成候选。"""
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
    candidates = project_identity_candidates(space, book_obs)
    assert 'ch02.q_local' not in {
        h for pair in ((c.open, c.close) for c in candidates.loop_pairing)
        for h in pair}, '章内自开自闭的 close 不进任何候选'
    prompt = associate_candidates_prompt(candidates)
    assert 'ch02.q_local' not in prompt, '提示词不列章内已闭合 close'
    # 绿对照：真正跨章的 close（ch1 开、ch3 收）仍生成候选。
    ch03 = {'protocol_version': CPV, 'beats': [
        _beat('跨章收束', ('e03_01',), loops=[
            {'ref': 'q_far', 'op': 'close', 'resolution': '约成身退'}])]}
    book_obs, chapters = _observations(book, {1: ch01, 2: ch02, 3: ch03})
    space = project_association_space(book_obs, chapters)
    candidates = project_identity_candidates(space, book_obs)
    assert ('ch01.q_far', 'ch03.q_far') in {
        (c.open, c.close) for c in candidates.loop_pairing}
    # 候选时序恒正：open 章 < close 章（倒转配对在 compile 必挂，不生成）。
    for candidate in candidates.loop_pairing:
        open_chapter = space.loop_opens[candidate.open].chapter
        close_chapter = space.pairable_closes[candidate.close].chapter
        assert open_chapter < close_chapter, candidate


def test_v3_write_handle_pairings_are_refused_with_the_candidate_set():
    """v3 wire（整书投影答案）对 v4 候选闭集精确拒绝：写入句柄对不是候选。"""
    wires = _same_key_wires(1)
    wires['ch03'] = {'protocol_version': CPV, 'beats': [
        _beat('近义键', ('e03_01',), participants=('fu_jun',),
              effects=[_plain_effect(CHAR_FJ, 'trust_level', '回升', ref='e03_01')]),
        _quiet_beat('收尾', 'e03_02')]}
    book, _chapters, space = _compiled(wires)
    candidates = project_identity_candidates(space, book)
    wire = {'protocol_version': 'stage2-associate-v4', 'same_proposition': [],
            'same_state_dimension': [{'left': 'book.e1.trust',
                                      'right': 'ch03.b1.e1.trust_level',
                                      'verdict': 'same'}],
            'loop_pairing': [], 'cross_chapter_cause': [], 'scene_continuation': []}
    observation, errors = compile_associate_observation(wire, space, candidates)
    assert observation is None, '编造句柄对即拒'
    assert any('不是已给候选' in e and 'book.e1.trust' in e for e in errors), errors
    assert all(getattr(e, 'code', '') in (
        'association_unknown_candidate', 'association_missing_answer') for e in errors)


def test_unordered_candidates_accept_swapped_left_right():
    """无序候选（命题／维度／续场）左右互换视为同一候选（答案键按候选归一）。"""
    wires = _same_key_wires(1)
    wires['ch03'] = {'protocol_version': CPV, 'beats': [
        _beat('近义键', ('e03_01',), participants=('fu_jun',),
              effects=[_plain_effect(CHAR_FJ, 'trust_level', '回升', ref='e03_01')])]}
    book, _chapters, space = _compiled(wires)
    candidates = project_identity_candidates(space, book)
    assert candidates.same_state_dimension, '近义键候选存在'
    pair = candidates.same_state_dimension[0]
    swapped = _answer_wire(candidates, same_state_dimension=[
        {'left': pair.right, 'right': pair.left, 'verdict': 'same'}])
    observation, errors = compile_associate_observation(swapped, space, candidates)
    assert observation is not None and errors == [], errors


def test_associate_patch_locate_maps_errors_to_answer_rows():
    """条目定位（RC-57 延续）：机械验收错误按句柄对反查答案行 `(kind, i)`。"""
    book = _book_wire()
    ch01 = {'protocol_version': CPV, 'beats': [
        _beat('一拍', ('e01_01',), participants=('fu_jun',),
              effects=[_plain_effect(CHAR_FJ, 'trust', '低')]),
        _beat('二拍', ('e01_02',),
              effects=[_plain_effect(CHAR_YN, 'mood', '安', ref='e01_02')])]}
    book_obs, chapters = _observations(book, {1: ch01})
    space = project_association_space(book_obs, chapters)
    dim = _dim_handle({'book': book, 'ch01': ch01}, CHAR_FJ, 'trust')
    candidates = project_identity_candidates(space, book_obs)
    assert candidates.same_state_dimension == (), '该夹具无近义键（只用写入句柄）'
    wire = _answer_wire(candidates, same_state_dimension=[
        {'left': 'dim99', 'right': 'dim98', 'verdict': 'same'}])
    _observation, errors = compile_associate_observation(wire, space, candidates)
    assert len(errors) == 1, errors  # 编造句柄对即拒
    targets = associate_patch_locate(wire, errors, space=space)
    assert targets == [(('same_state_dimension', 0), list(errors))], targets


def test_associate_patch_locate_returns_none_for_unmappable_errors():
    """定位不了的退整份（None）：missing_answer 指向尚不存在的行、句柄在多条
    答案里出现（无法唯一指定替换目标）、无结构化位置的字符串错误。"""
    from novel.stages.stage2.associate import association_issue
    book = _book_wire()
    ch01 = {'protocol_version': CPV, 'beats': [
        _beat('一拍', ('e01_01',), participants=('fu_jun',),
              effects=[_plain_effect(CHAR_FJ, 'trust', '低')])]}
    book_obs, chapters = _observations(book, {1: ch01})
    space = project_association_space(book_obs, chapters)
    dim = _dim_handle({'book': book, 'ch01': ch01}, CHAR_FJ, 'trust')
    candidates = project_identity_candidates(space, book_obs)
    assert candidates.same_state_dimension == (), '该夹具无近义键'
    wire = _answer_wire(candidates, same_state_dimension=[
        {'left': 'dim99', 'right': 'dim98', 'verdict': 'same'}])
    # missing_answer（候选无既有行可替换）。
    missing = association_issue(
        'missing_answer', ('same_proposition', 'ch03.p_hide', 'ch01.p_hide'), '…')
    assert associate_patch_locate(wire, [missing], space=space) is None
    # 歧义：错误句柄命中多条答案。
    ambiguous = association_issue('state_target_mismatch', (dim,), '…')
    assert associate_patch_locate(wire, [ambiguous], space=space) is None
    # 无结构化位置的字符串错误（存量兼容面）同样退整份。
    assert associate_patch_locate(wire, ['裸字符串错误'], space=space) is None


def test_near_synonym_pair_is_deterministic_and_recall_oriented():
    """确定性相似度：norm 全等／≥4 字面包含／bigram Jaccard 过阈值；短句包含
    不误报（CONTAINMENT_MIN_CHARS）。"""
    assert near_synonym_pair('夫君藏身床底', '夫君藏身，床底。', threshold=0.5)
    assert near_synonym_pair('trust', 'trust_level', threshold=0.5), '包含判定'
    assert near_synonym_pair('夫君藏身床底', '夫君藏身于床底', threshold=0.5)
    assert not near_synonym_pair('夫君藏身床底', '床底藏着人', threshold=0.5), \
        '低相似对不是候选（保持分离，不默认合并）'
    assert not near_synonym_pair('他是谁', '后来大家才知道他是谁的哥哥', threshold=0.5), \
        '短句包含不误报'


def test_dimension_of_maps_every_write_to_its_dimension():
    """登记表内部一致性：每个写入句柄都映射到其地址维度；维度的 writes 覆盖
    该地址全部写入且按登记序（审计读数面，RC-65 延续）。"""
    wires = _same_key_wires(2)
    book, _chapters, space = _compiled(wires)
    assert set(space.dimension_of) == set(space.states)
    address = state_dimension_address(CHAR_FJ, 'trust')
    trust_dim = space.dimension_of['book.e1.trust']
    assert space.dimensions[trust_dim].writes == tuple(
        h for h, e in space.states.items()
        if state_dimension_address(e.target, e.key) == address)
    assert space.dimensions[trust_dim].first_rank == (0, 0, 1), '首次写入＝book 初态'


def test_proposition_fact_candidates_and_cause_candidates_from_identity():
    """Phase 2 主径：know.fact 引用不进章命题登记表；近义 what 文本与词表命题
    生成候选；因果候选（D-2 A）＝loop 配对拍对＋facts 流拍对（含经 same 并入
    词表的命题），从已判定身份答案派生；窄调用恰答一次。"""
    book = _book_wire()
    # canonical 直通面（v6）：facts 带 F 编号与 basis（引文＝段全文前 6 字）。
    book['facts'] = [
        {'fact_id': 'f01', 'statement': '夫君藏身床底',
         'basis': _basis('e01_01'), 'known_at_start': []}]
    ch01 = {'protocol_version': CPV, 'beats': [
        _beat('云娘起疑', ('e01_01',), participants=('yun_niang',),
              cognition=[{'proposition': {'ref': 'f01', 'statement': None},
                          'holder': 'yun_niang', 'mode': 'suspects',
                          'basis': _basis('e01_01')}],
              loops=[{'ref': 'q_id', 'op': 'open', 'question': '床底的人是谁？'}])]}
    ch02 = {'protocol_version': CPV, 'beats': [
        _beat('词表外转写', ('e02_01',), participants=('yun_niang',),
              cognition=[{'proposition': {'ref': 'p_drift', 'statement': '夫君藏身于床底'},
                          'holder': 'yun_niang', 'mode': 'learns',
                          'basis': _basis('e02_01')}])]}
    ch03 = {'protocol_version': CPV, 'beats': [
        _beat('跨章收束', ('e03_01',),
              loops=[{'ref': 'q_id', 'op': 'close', 'resolution': '床底的人是夫君'}])]}
    book_obs, chapters = _observations(book, {1: ch01, 2: ch02, 3: ch03})
    space = project_association_space(book_obs, chapters)
    assert 'ch01.f01' not in space.propositions, 'fact 引用不是章内命题'
    candidates = project_identity_candidates(space, book_obs)
    assert ('ch02.p_drift', 'f01') in {
        (c.left, c.right) for c in candidates.same_proposition}, '词表近义候选'
    identity = AssociateObservation.model_validate(_answer_wire(
        candidates,
        same_proposition=[
            {'left': 'ch02.p_drift', 'right': 'f01', 'verdict': 'same'}],
        loop_pairing=[
            {'open': 'ch01.q_id', 'close': 'ch03.q_id', 'verdict': 'pair'}]))
    # 因果候选：loop 配对（ch01.b1→ch03.b1）＋fact f01 流（ch01.b1→ch02.b1，
    # 直接引用与并入的转写命题各占一拍）。
    causes = project_cause_candidates(space, chapters, book_obs, identity)
    pairs = {(c.from_beat, c.to_beat) for c in causes}
    assert pairs == {('ch01.b1', 'ch03.b1'), ('ch01.b1', 'ch02.b1')}, pairs
    # 窄调用验收：恰答一次，kind 或否决。
    accepted, errors = compile_cross_cause(
        {'protocol_version': 'stage2-cross-cause-v1', 'causes': [
            {'from_beat': 'ch01.b1', 'to_beat': 'ch03.b1', 'kind': 'setup_payoff'},
            {'from_beat': 'ch01.b1', 'to_beat': 'ch02.b1', 'kind': 'none'}]},
        causes)
    assert accepted is not None and errors == [], errors
    _causes, errors = compile_cross_cause(
        {'protocol_version': 'stage2-cross-cause-v1', 'causes': [
            {'from_beat': 'ch01.b1', 'to_beat': 'ch03.b1', 'kind': 'causes'}]},
        causes)
    assert any('没有回答' in e for e in errors), errors
    _causes, errors = compile_cross_cause(
        {'protocol_version': 'stage2-cross-cause-v1', 'causes': [
            {'from_beat': 'ch01.b1', 'to_beat': 'ch03.b1', 'kind': 'causes'},
            {'from_beat': 'ch02.b1', 'to_beat': 'ch03.b1', 'kind': 'enables'}]},
        causes)
    assert any('不是已给候选' in e for e in errors), errors
