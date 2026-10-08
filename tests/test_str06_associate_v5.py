#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_associate_v5 —— 关联协议 v5（CS-S Phase 3）的判据套件。

【验证边界】条目级窄 resolver 合同（docs/REQUIREMENTS.md CS-S Phase 3）：
  • 维度候选面消失：change 键入 `CHANGE_KEYS` 闭集＝键即 canonical——
    `same_state_dimension` 协议面删除，v4 wire 关闭式拒读；同键多写不再聚合
    维度句柄（states／dimensions 登记面整体退役）；
  • 候选由代码给出（确定性相似度＋结构投影）——近义命题对（含 facts 词表）、
    跨章 open×close（时序正）、相邻章 seam 续场对；无候选＝零调用；
  • 逐候选恰答一次：闭集＝候选集合（编造句柄对都拒），漏答／多答＝机械缺陷；
    无序候选左右互换视为同一候选；
  • verdict 只执行 same／pair／continue——distinct／unresolved 保持分离，
    compile 消费面在 `tests/test_str06_stage2_publish.py`；
  • 因果候选（D-2 A）＝loop 配对拍对＋facts 流拍对，从**已判定的身份答案**派生。
语义判断（哪些候选是同一）归付费 semantic unit，保持 unver 披露；本文件全部
确定性构造，零模型调用。
"""
from __future__ import annotations

from contracts.schema.observation import AssociateObservation
from novel.stages.stage2 import (
    associate_candidates_prompt, associate_patch_locate, near_synonym_pair,
    compile_associate_observation, compile_cross_cause, project_association_space,
    project_identity_candidates, project_cause_candidates,
)
from tests.test_str06_stage2_publish import (
    CHAR_FJ, CHAR_YN, CPV, _basis, _beat, _book_wire,
    _observations, _plain_effect, _quiet_beat,
)


def _answer_wire(candidates, **over) -> dict:
    """恰答全部候选的默认答案（全部 distinct），测试按需覆写个别类。"""
    wire = {
        'protocol_version': 'stage2-associate-v5',
        'same_proposition': [
            {'left': c.left, 'right': c.right, 'verdict': 'distinct'}
            for c in candidates.same_proposition],
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


def _chapters_with_key_writes():
    """三章各写一条 attitude（同键同对象——v4 时代曾聚合成一个维度句柄）。"""
    return {f'ch{no:02d}': {'protocol_version': CPV, 'beats': [
        _beat(f'第{no}章写态度', (f'e{no:02d}_01',), participants=('fu_jun',),
              scene_boundary='new' if no == 1 else 'continue',
              effects=[_plain_effect(CHAR_FJ, 'attitude', f'值{no}', ref=f'e{no:02d}_01')])]}
        for no in (1, 2, 3)}


def test_same_key_writes_generate_no_candidates_and_v4_wire_is_refused():
    """判据①（Phase 3）：同键多写不产生任何候选（键即 canonical，无维度面）；
    v4 wire（带 same_state_dimension）对 v5 协议关闭式拒读。"""
    book = _book_wire()
    chapters_wires = _chapters_with_key_writes()
    book_obs, chapters = _observations(
        book, {no: chapters_wires[f'ch{no:02d}'] for no in (1, 2, 3)})
    space = project_association_space(book_obs, chapters)
    # 登记面退役：states／dimensions 不存在（S-05 死面删除）。
    assert not hasattr(space, 'states') and not hasattr(space, 'dimensions')
    candidates = project_identity_candidates(space, book_obs)
    assert not candidates, '同键多写＝零候选（v5 无维度配对面）'
    prompt = associate_candidates_prompt(candidates)
    assert 'attitude' not in prompt, '闭集键不进请求面'
    assert '（无候选——该类给空数组）' in prompt
    # v4 wire 关闭式拒读：same_state_dimension 是 v4 的输出面。
    wire = _answer_wire(candidates)
    wire['same_state_dimension'] = []
    _obs, errors = compile_associate_observation(wire, space, candidates)
    assert _obs is None and any('same_state_dimension' in str(e) for e in errors), errors


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


def test_unordered_candidates_accept_swapped_left_right():
    """无序候选（命题／续场）左右互换视为同一候选（答案键按候选归一）。"""
    book = _book_wire()
    book['facts'] = [
        {'fact_id': 'f01', 'statement': '夫君藏身床底',
         'basis': _basis('e01_01'), 'known_at_start': []}]
    ch01 = {'protocol_version': CPV, 'beats': [_quiet_beat('开场', 'e01_01')]}
    ch02 = {'protocol_version': CPV, 'beats': [
        _beat('词表外转写', ('e02_01',), participants=('yun_niang',),
              cognition=[{'proposition': {'ref': 'p_drift', 'statement': '夫君藏身于床底'},
                          'holder': 'yun_niang', 'mode': 'learns',
                          'basis': _basis('e02_01')}])]}
    ch03 = {'protocol_version': CPV, 'beats': [_quiet_beat('收尾', 'e03_01')]}
    book_obs, chapters = _observations(book, {1: ch01, 2: ch02, 3: ch03})
    space = project_association_space(book_obs, chapters)
    candidates = project_identity_candidates(space, book_obs)
    assert ('ch02.p_drift', 'f01') in {
        (c.left, c.right) for c in candidates.same_proposition}, '词表近义候选'
    swapped = _answer_wire(candidates, same_proposition=[
        {'left': 'f01', 'right': 'ch02.p_drift', 'verdict': 'same'}])
    observation, errors = compile_associate_observation(swapped, space, candidates)
    assert observation is not None and errors == [], errors


def test_associate_patch_locate_maps_errors_to_answer_rows():
    """条目定位（RC-57 延续）：机械验收错误按句柄对反查答案行 `(kind, i)`。"""
    book = _book_wire()
    ch01 = {'protocol_version': CPV, 'beats': [
        _beat('一拍', ('e01_01',), participants=('fu_jun',),
              effects=[_plain_effect(CHAR_FJ, 'attitude', '低')]),
        _beat('二拍', ('e01_02',),
              effects=[_plain_effect(CHAR_YN, 'emotional_state', '安', ref='e01_02')])]}
    book_obs, chapters = _observations(book, {1: ch01})
    space = project_association_space(book_obs, chapters)
    candidates = project_identity_candidates(space, book_obs)
    assert not candidates, '闭集键不产生候选'
    wire = _answer_wire(candidates, same_proposition=[
        {'left': 'ch09.p_ghost', 'right': 'ch08.p_ghost', 'verdict': 'same'}])
    _observation, errors = compile_associate_observation(wire, space, candidates)
    assert len(errors) == 1, errors  # 编造句柄对即拒
    targets = associate_patch_locate(wire, errors)
    assert targets == [(('same_proposition', 0), list(errors))], targets


def test_associate_patch_locate_returns_none_for_unmappable_errors():
    """定位不了的退整份（None）：missing_answer 指向尚不存在的行、无结构化
    位置的字符串错误。"""
    from novel.stages.stage2.associate import association_issue
    book = _book_wire()
    ch01 = {'protocol_version': CPV, 'beats': [
        _beat('一拍', ('e01_01',), participants=('fu_jun',),
              effects=[_plain_effect(CHAR_FJ, 'attitude', '低')])]}
    book_obs, chapters = _observations(book, {1: ch01})
    space = project_association_space(book_obs, chapters)
    candidates = project_identity_candidates(space, book_obs)
    wire = _answer_wire(candidates, same_proposition=[
        {'left': 'ch09.p_ghost', 'right': 'ch08.p_ghost', 'verdict': 'same'}])
    # missing_answer（候选无既有行可替换）。
    missing = association_issue(
        'missing_answer', ('same_proposition', 'ch03.p_hide', 'ch01.p_hide'), '…')
    assert associate_patch_locate(wire, [missing]) is None
    # 无结构化位置的字符串错误（存量兼容面）同样退整份。
    assert associate_patch_locate(wire, ['裸字符串错误']) is None


def test_near_synonym_pair_is_deterministic_and_recall_oriented():
    """确定性相似度：norm 全等／≥4 字面包含／bigram Jaccard 过阈值；短句包含
    不误报（CONTAINMENT_MIN_CHARS）。"""
    assert near_synonym_pair('夫君藏身床底', '夫君藏身，床底。', threshold=0.5)
    assert near_synonym_pair('attitude', 'attitude_level', threshold=0.5), '包含判定'
    assert near_synonym_pair('夫君藏身床底', '夫君藏身于床底', threshold=0.5)
    assert not near_synonym_pair('夫君藏身床底', '床底藏着人', threshold=0.5), \
        '低相似对不是候选（保持分离，不默认合并）'
    assert not near_synonym_pair('他是谁', '后来大家才知道他是谁的哥哥', threshold=0.5), \
        '短句包含不误报'


def test_proposition_fact_candidates_and_cause_candidates_from_identity():
    """Phase 2 主径（v5 延续）：know.fact 引用不进章命题登记表；近义 what 文本
    与词表命题生成候选；因果候选（D-2 A）＝loop 配对拍对＋facts 流拍对（含经
    same 并入词表的命题），从已判定身份答案派生；窄调用恰答一次。"""
    book = _book_wire()
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
