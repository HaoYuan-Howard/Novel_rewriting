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
    无序命题候选左右互换视为同一候选；悬念与续场按时间方向核对；
  • verdict 只执行 same／pair／continue——distinct 保持分离，unresolved 保持未决拒发，
    compile 消费面在 `tests/test_str06_stage2_publish.py`；
  • 因果候选（D-2 A）＝loop 配对拍对＋facts 流拍对，从**已判定的身份答案**派生。
语义判断（哪些候选是同一）归付费 semantic unit，保持 unver 披露；本文件全部
确定性构造，零模型调用。
"""
from __future__ import annotations

import itertools

import pytest

from contracts.schema.observation import AssociateObservation
from novel.stages.stage2 import (
    associate_candidates_prompt, associate_patch_locate, near_synonym_pair,
    associate_observation_issues, associate_unresolved_issues,
    compile_associate_observation, compile_cross_cause, compile_source_truth,
    project_association_space, project_identity_candidates,
    project_cause_candidates,
    inspect_identity_associations,
)
from novel.stages.stage2.associate import chapter_scene_layout
from tests.test_str06_stage2_publish import (
    CHAR_FJ, CHAR_YN, CPV, EVIDENCE, _associate_wire, _basis, _beat, _book_wire,
    _candidates, _names_observation, _observations, _plain_effect, _quiet_beat,
    _space, _space_pair,
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
    """无序命题候选左右互换视为同一候选（答案键按候选归一）。"""
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


def test_cause_fact_flow_keeps_every_local_reference_and_deduplicates_loop_pairs():
    """同一个转写命题跨多拍出现时，每一拍都进入事实流；与 loop 重叠的拍对只问一次。"""
    book = _book_wire()
    book['facts'] = [
        {'fact_id': 'f01', 'statement': '夫君藏身床底',
         'basis': _basis('e01_01'), 'known_at_start': []}]
    chapter_wires = {
        1: {'protocol_version': CPV, 'beats': [
            _beat('先获知床底藏人', ('e01_01',), participants=('yun_niang',),
                  cognition=[{'proposition': {'ref': 'p_drift', 'statement': '夫君藏身于床底'},
                              'holder': 'yun_niang', 'mode': 'learns',
                              'basis': _basis('e01_01')}],
                  loops=[{'ref': 'q_id', 'op': 'open', 'question': '床底是谁？'}]),
            _beat('再次确认同一命题', ('e01_02',), participants=('yun_niang',),
                  scene_boundary='continue',
                  cognition=[{'proposition': {'ref': 'p_drift'},
                              'holder': 'yun_niang', 'mode': 'confirms',
                              'basis': _basis('e01_02')}])]},
        2: {'protocol_version': CPV, 'beats': [
            _beat('翌日事实得到确认', ('e02_01',), participants=('yun_niang',),
                  cognition=[{'proposition': {'ref': 'f01'},
                              'holder': 'yun_niang', 'mode': 'confirms',
                              'basis': _basis('e02_01')}],
                  loops=[{'ref': 'q_id', 'op': 'close', 'resolution': '床底是夫君'}])]}}
    book_obs, chapters = _observations(book, chapter_wires)
    space = project_association_space(book_obs, chapters)
    candidates = project_identity_candidates(space, book_obs)
    identity, errors = compile_associate_observation(_answer_wire(
        candidates,
        same_proposition=[{'left': c.left, 'right': c.right, 'verdict': 'same'}
                          for c in candidates.same_proposition],
        loop_pairing=[{'open': c.open, 'close': c.close, 'verdict': 'pair'}
                      for c in candidates.loop_pairing]), space, candidates)
    assert identity is not None and not errors, errors
    causes = project_cause_candidates(space, chapters, book_obs, identity)
    assert [(c.from_beat, c.to_beat) for c in causes] == [
        ('ch01.b1', 'ch02.b1'), ('ch01.b2', 'ch02.b1')]
    # 真实 compiler 消费同一份判定：两拍的命题都已并入 f01，因果落到下游章。
    final = identity.model_copy(update={'cross_chapter_cause': compile_cross_cause(
        {'protocol_version': 'stage2-cross-cause-v1', 'causes': [
            {'from_beat': c.from_beat, 'to_beat': c.to_beat, 'kind': 'enables'}
            for c in causes]}, causes)[0]})
    outcome = compile_source_truth(book=book_obs, chapters=chapters,
                                   names=_names_observation(book_obs),
                                   associate=final, evidence=EVIDENCE)
    assert outcome.publishable, (outcome.errors, outcome.undecided)
    assert {c.proposition.ref for beat in outcome.blueprint.chapters[0].beats
            for c in beat.cognition} == {'f01'}
    assert len(outcome.blueprint.chapters[1].causal_edges) == 2


@pytest.mark.parametrize('bridge_verdict', ['same', 'distinct', 'unresolved'])
@pytest.mark.parametrize('reverse_answers', [False, True])
def test_cause_fact_flow_uses_same_transitive_closure_only(bridge_verdict, reverse_answers):
    """fact↔A↔B 的已接受 same 传递关系决定事实流；distinct/unresolved 不扩大流。"""
    book = _book_wire()
    book['facts'] = [
        {'fact_id': 'f01', 'statement': '夫君藏身床底',
         'basis': _basis('e01_01'), 'known_at_start': []}]
    statements = {1: '夫君藏身于床底', 2: '藏身于床底的夫君'}
    chapter_wires = {
        no: {'protocol_version': CPV, 'beats': [
            _beat(f'第{no}章确认', (f'e{no:02d}_01',), participants=('yun_niang',),
                  cognition=[{'proposition': {'ref': 'p_drift', 'statement': statement},
                              'holder': 'yun_niang', 'mode': 'learns',
                              'basis': _basis(f'e{no:02d}_01')}])]}
        for no, statement in statements.items()}
    chapter_wires[3] = {'protocol_version': CPV, 'beats': [
        _beat('末章引用词表', ('e03_01',), participants=('yun_niang',),
              cognition=[{'proposition': {'ref': 'f01'},
                          'holder': 'yun_niang', 'mode': 'confirms',
                          'basis': _basis('e03_01')}])]}
    book_obs, chapters = _observations(book, chapter_wires)
    space = project_association_space(book_obs, chapters)
    candidates = project_identity_candidates(space, book_obs)
    # B 未过与 fact 的相似度筛子，只经 A 的同一性断言传递归入词表。
    assert {(c.left, c.right) for c in candidates.same_proposition} == {
        ('ch01.p_drift', 'f01'), ('ch01.p_drift', 'ch02.p_drift')}
    answers = [
        {'left': 'ch01.p_drift', 'right': 'f01', 'verdict': 'same'},
        {'left': 'ch01.p_drift', 'right': 'ch02.p_drift', 'verdict': bridge_verdict}]
    if reverse_answers:
        answers = [{'left': row['right'], 'right': row['left'], 'verdict': row['verdict']}
                   for row in reversed(answers)]
    identity, errors = compile_associate_observation(
        _answer_wire(candidates, same_proposition=answers), space, candidates)
    assert identity is not None and not errors, errors
    causes = project_cause_candidates(space, chapters, book_obs, identity)
    expected = [('ch01.b1', 'ch03.b1')]
    if bridge_verdict == 'same':
        expected = [('ch01.b1', 'ch02.b1'), ('ch01.b1', 'ch03.b1'),
                    ('ch02.b1', 'ch03.b1')]
    assert [(c.from_beat, c.to_beat) for c in causes] == expected
    # 下游 compile 与候选投影消费相同的身份闭包。
    causes, cause_errors = compile_cross_cause(
        {'protocol_version': 'stage2-cross-cause-v1', 'causes': [
            {'from_beat': c.from_beat, 'to_beat': c.to_beat, 'kind': 'none'}
            for c in project_cause_candidates(space, chapters, book_obs, identity)]},
        project_cause_candidates(space, chapters, book_obs, identity))
    assert causes is not None and not cause_errors, cause_errors
    final = identity.model_copy(update={'cross_chapter_cause': causes})
    outcome = compile_source_truth(book=book_obs, chapters=chapters,
                                   names=_names_observation(book_obs),
                                   associate=final, evidence=EVIDENCE)
    if bridge_verdict != 'unresolved':
        assert outcome.publishable, (outcome.errors, outcome.undecided)
        second_ref = outcome.blueprint.chapters[1].beats[0].cognition[0].proposition.ref
        assert (second_ref == 'f01') == (bridge_verdict == 'same')
    else:
        assert not outcome.publishable
        assert any(issue.code == 'association_unresolved_verdict'
                   for issue in outcome.undecided)


# ───────────────────────── 续场候选：场景序同源＋最小文本 ─────────────────────────

def _scene_case(ch2: tuple[str, ...], ch3: tuple[str, ...]) -> dict[int, dict]:
    """三章各自的 scene_boundary 序列；事件名 ``c{章}b{拍}`` 唯一，便于核对候选文本。"""
    def chapter(no: int, boundaries: tuple[str, ...]) -> dict:
        return {'protocol_version': CPV, 'beats': [
            _beat(f'c{no}b{index}', (f'e{no:02d}_01',), scene_boundary=boundary)
            for index, boundary in enumerate(boundaries, 1)]}
    return {1: chapter(1, ('new',)), 2: chapter(2, ch2), 3: chapter(3, ch3)}


_PATTERNS = [pattern for length in (1, 2, 3)
             for pattern in itertools.product(('new', 'continue'), repeat=length)]


@pytest.mark.parametrize('ch3', [('new',), ('continue',)])
@pytest.mark.parametrize('ch2', _PATTERNS, ids=lambda p: '-'.join(x[0] for x in p))
def test_scene_handles_follow_compile_scene_order_for_every_boundary_pattern(ch2, ch3):
    """判据（D#2）：代码给出的续场候选，模型答 continue 后 compile 必须接得住。

    候选句柄 ``chNN.scK`` 的序号来自 `chapter_scene_layout`，必须与 compile 的
    SceneSpan 序逐位一致——首拍 continue 的章，前章带入的延续场景占 sc1；若关联
    空间只数 `new` 边界，代码生成的 (ch02.sc1→ch03.sc1) 会被 compile 报
    `continuation_left`，自己拒绝自己。全排列守住两边同源。"""
    book_obs, chapters = _observations(_book_wire(), _scene_case(ch2, ch3))
    space = project_association_space(book_obs, chapters)
    candidates = project_identity_candidates(space, book_obs)
    # 候选面：右场景自称 new 才有 seam 候选（首拍 continue＝已续前场，无需配对）；
    # 左端是左章 compile 眼中的最后一个场景。
    expected = []
    if ch2[0] == 'new':
        expected.append(('ch01.sc1', 'ch02.sc1'))
    if ch3[0] == 'new':
        expected.append((f'ch02.sc{len(chapter_scene_layout(ch2))}', 'ch03.sc1'))
    assert [(c.left, c.right) for c in candidates.scene_continuation] == expected
    # 最小文本：左场景末拍与右场景首拍的 event。
    for candidate in candidates.scene_continuation:
        left, right = space.scenes[candidate.left], space.scenes[candidate.right]
        assert candidate.left_text == f'c{left.chapter}b{left.closing_beat}'
        assert candidate.right_text == f'c{right.chapter}b{right.opening_beat}'
    # 全部判 continue：compile 无任何续场布局未决，且蓝图场景分段与关联空间逐位一致。
    wire = _answer_wire(candidates, scene_continuation=[
        {'left': c.left, 'right': c.right, 'verdict': 'continue'}
        for c in candidates.scene_continuation])
    observation, errors = compile_associate_observation(wire, space, candidates)
    assert observation is not None and errors == [], errors
    outcome = compile_source_truth(
        book=book_obs, chapters=chapters, names=_names_observation(book_obs),
        associate=observation, evidence=EVIDENCE)
    assert outcome.errors == [] and outcome.undecided == [], (
        outcome.errors, [str(issue) for issue in outcome.undecided])
    for bp in outcome.blueprint.chapters:
        handles = sorted((h for h, s in space.scenes.items() if s.chapter == bp.chapter),
                         key=lambda h: space.scenes[h].ordinal)
        assert len(handles) == len(bp.scenes), (bp.chapter, handles)
        for handle, span in zip(handles, bp.scenes):
            scene = space.scenes[handle]
            assert span.beat_ids[0] == f'b{bp.chapter:02d}_{scene.opening_beat:02d}'
            assert span.beat_ids[-1] == f'b{bp.chapter:02d}_{scene.closing_beat:02d}'


def test_seam_after_a_carried_chapter_still_gets_a_candidate():
    """回归（D#2 的另一面）：ch2 整章延续 ch1 的场景（首拍 continue），ch3 自称 new
    ——ch2 的末场景（带入的延续场景）与 ch3 首场景仍是合法 seam，必须有候选，
    而不是因为 ch2 没有自己的 new 边界就整章无场景、候选消失。"""
    book_obs, chapters = _observations(_book_wire(), _scene_case(('continue',), ('new',)))
    space = project_association_space(book_obs, chapters)
    assert space.scenes['ch02.sc1'].carried and not space.scenes['ch03.sc1'].carried
    candidates = project_identity_candidates(space, book_obs)
    assert [(c.left, c.right) for c in candidates.scene_continuation] == [
        ('ch02.sc1', 'ch03.sc1')]


def test_scene_candidates_show_the_boundary_beats_text_in_the_prompt():
    """判据（D#1）：请求面给模型的是末拍／首拍的事件文本，而不是两个不透明的句柄。"""
    book_obs, chapters = _observations(_book_wire(), _scene_case(('new', 'new'), ('new',)))
    space = project_association_space(book_obs, chapters)
    candidates = project_identity_candidates(space, book_obs)
    prompt = associate_candidates_prompt(candidates)
    assert prompt == associate_candidates_prompt(
        project_identity_candidates(space, book_obs)), '同输入逐字节可复算'
    assert '[ch02.sc2]（末拍：c2b2）' in prompt and '[ch03.sc1]（首拍：c3b1）' in prompt
    assert '[ch01.sc1]（末拍：c1b1）' in prompt and '[ch02.sc1]（首拍：c2b1）' in prompt


def test_scene_continuation_candidates_keep_the_forward_direction():
    """续场的前章 left／后章 right 不可交换，owner 验收与下游 compiler 同向。"""
    book_obs, chapters = _observations(_book_wire(), _scene_case(('new',), ('new',)))
    space = project_association_space(book_obs, chapters)
    candidates = project_identity_candidates(space, book_obs)
    wire = _answer_wire(candidates, scene_continuation=[
        {'left': c.right, 'right': c.left, 'verdict': 'continue'}
        for c in candidates.scene_continuation])
    identity, errors = compile_associate_observation(wire, space, candidates)
    assert identity is None
    assert {issue.code for issue in errors} == {
        'association_unknown_candidate', 'association_missing_answer'}
    targets = associate_patch_locate(wire, errors)
    assert targets is not None
    assert {path for path, _issues in targets} == {
        ('scene_continuation', index) for index in range(len(candidates.scene_continuation))}
    wire['scene_continuation'] = [
        {'left': c.left, 'right': c.right, 'verdict': 'continue'}
        for c in candidates.scene_continuation]
    identity, errors = compile_associate_observation(wire, space, candidates)
    assert identity is not None and not errors, errors
    outcome = compile_source_truth(book=book_obs, chapters=chapters,
                                   names=_names_observation(book_obs),
                                   associate=identity, evidence=EVIDENCE)
    assert outcome.publishable, (outcome.errors, outcome.undecided)
    assert len({span.scene_id for chapter in outcome.blueprint.chapters
                for span in chapter.scenes}) == 1


# ───────────────────────── 身份答案不得携带跨章因果 ─────────────────────────

def test_identity_answer_must_not_carry_cross_chapter_cause():
    """判据（D#3a）：因果只由窄调用经 run_associate 组装；身份答案里的任何因果行
    都不对应已给候选、compile 又不再核对它——按“答案超出候选”机械拒收。"""
    stray = [{'from_beat': 'ch99.b9', 'to_beat': 'ch98.b1', 'kind': 'causes'}]
    wire = _associate_wire(cross_chapter_cause=stray)
    observation, errors = compile_associate_observation(wire, _space(), _candidates())
    assert observation is None
    assert [getattr(e, 'code', None) for e in errors] == ['association_stray_cause']
    assert errors[0].location == ('cross_chapter_cause', 'ch99.b9', 'ch98.b1')
    assert errors[0].owner == 'associate'
    # 多余的行没有可替换的目标条目：退整份返修（不假装能条目定位）。
    assert associate_patch_locate(wire, errors) is None
    # 绿对照：同一份答案去掉因果行即通过；最终观察模型仍可承载因果行（落盘形状不变）。
    clean, errors = compile_associate_observation(
        _associate_wire(), _space(), _candidates())
    assert clean is not None and errors == [] and clean.cross_chapter_cause == ()
    final = AssociateObservation.model_validate(_associate_wire(cross_chapter_cause=[
        {'from_beat': 'ch01.b2', 'to_beat': 'ch03.b2', 'kind': 'none'}]))
    assert final.cross_chapter_cause[0].kind == 'none'


# ───────────────────────── 最终组装、未决与免费恢复的同一把尺 ─────────────────────────

def _with_assembled_causes(book, chapters, identity):
    space = project_association_space(book, chapters)
    candidates = project_cause_candidates(space, chapters, book, identity)
    if not candidates:
        return identity.model_copy(update={'cross_chapter_cause': ()})
    causes, errors = compile_cross_cause(
        {'protocol_version': 'stage2-cross-cause-v1', 'causes': [
            {'from_beat': c.from_beat, 'to_beat': c.to_beat, 'kind': 'none'}
            for c in candidates]}, candidates)
    assert causes is not None and not errors, errors
    return identity.model_copy(update={'cross_chapter_cause': causes})


def _assembled_case():
    book, chapters = _space_pair()
    identity = AssociateObservation.model_validate(_associate_wire())
    final = _with_assembled_causes(book, chapters, identity)
    return book, chapters, final


def test_assembled_candidate_validation_is_complete_and_does_not_mutate_input():
    book, chapters, final = _assembled_case()
    space = project_association_space(book, chapters)
    snapshots = (book.model_dump(mode='json'),
                 {no: obs.model_dump(mode='json') for no, obs in chapters.items()},
                 final.model_dump(mode='json'))
    assert not associate_observation_issues(final, space, book, chapters)
    assert not associate_unresolved_issues(final)
    assert snapshots == (book.model_dump(mode='json'),
                         {no: obs.model_dump(mode='json') for no, obs in chapters.items()},
                         final.model_dump(mode='json'))


@pytest.mark.parametrize('missing_kind', [
    'same_proposition', 'loop_pairing', 'scene_continuation', 'cross_chapter_cause'])
def test_assembled_candidate_validation_rejects_missing_answers(missing_kind):
    book, chapters, final = _assembled_case()
    faulty = final.model_copy(update={missing_kind: ()})
    errors = associate_observation_issues(
        faulty, project_association_space(book, chapters), book, chapters)
    assert any(issue.code == 'association_missing_answer'
               and issue.location[0] == missing_kind for issue in errors), errors
    outcome = compile_source_truth(book=book, chapters=chapters,
                                   names=_names_observation(book),
                                   associate=faulty, evidence=EVIDENCE)
    assert not outcome.publishable
    assert any(issue.code == 'association_missing_answer'
               and issue.location[0] == missing_kind for issue in outcome.undecided)


def test_identity_inspection_requires_identity_completeness_and_cannot_publish():
    book, chapters, final = _assembled_case()
    identity = final.model_copy(update={'cross_chapter_cause': ()})
    args = dict(book=book, chapters=chapters, names=_names_observation(book),
                associate=identity, evidence=EVIDENCE)
    errors, undecided = inspect_identity_associations(**args)
    assert not errors and not undecided
    outcome = compile_source_truth(**args)
    assert not outcome.publishable
    assert any(issue.code == 'association_missing_answer'
               and issue.location[0] == 'cross_chapter_cause'
               for issue in outcome.undecided)
    faulty = identity.model_copy(update={'same_proposition': ()})
    errors, undecided = inspect_identity_associations(**{**args, 'associate': faulty})
    assert any(issue.code == 'association_missing_answer'
               and issue.location[0] == 'same_proposition' for issue in undecided)
    # 身份阶段不能偷渡尚未执行的第二窄任务答案。
    errors, undecided = inspect_identity_associations(**{**args, 'associate': final})
    assert any(issue.code == 'association_stray_cause' for issue in undecided)


@pytest.mark.parametrize('kind', ['same_proposition', 'loop_pairing', 'scene_continuation'])
def test_unresolved_verdict_keeps_a_stable_patch_path_and_blocks_final_publish(kind):
    book, chapters, final = _assembled_case()
    rows = getattr(final, kind)
    faulty = final.model_copy(update={kind: (
        rows[0].model_copy(update={'verdict': 'unresolved'}), *rows[1:])})
    faulty = _with_assembled_causes(book, chapters, faulty)
    issues = associate_unresolved_issues(faulty)
    assert len(issues) == 1 and issues[0].code == 'association_unresolved_verdict'
    assert issues[0].owner == 'associate' and issues[0].location[0] == kind
    assert associate_patch_locate(faulty.model_dump(mode='json'), issues) == [
        ((kind, 0), issues)]
    if kind == 'same_proposition':
        swapped = rows[0].model_copy(update={'left': rows[0].right,
                                            'right': rows[0].left,
                                            'verdict': 'unresolved'})
        reversed_identity = faulty.model_copy(update={kind: (swapped, *rows[1:])})
        assert associate_unresolved_issues(reversed_identity)[0].location == issues[0].location
    outcome = compile_source_truth(book=book, chapters=chapters,
                                   names=_names_observation(book),
                                   associate=faulty, evidence=EVIDENCE)
    assert not outcome.publishable
    assert any(issue.code == 'association_unresolved_verdict'
               and issue.location == issues[0].location for issue in outcome.undecided)


@pytest.mark.parametrize('problem', ['missing_identity', 'unresolved', 'stray_cause'])
def test_associate_free_patch_preview_uses_the_real_identity_wire_phase(problem):
    from contracts.schema.hook import HookConstraints
    from novel.stages.review import dry_run_patch

    book, chapters, final = _assembled_case()
    identity = final.model_copy(update={'cross_chapter_cause': ()})
    if problem == 'missing_identity':
        faulty = identity.model_copy(update={'same_proposition': ()})
        expected = 'association_missing_answer'
    elif problem == 'unresolved':
        rows = identity.scene_continuation
        faulty = identity.model_copy(update={'scene_continuation': (
            rows[0].model_copy(update={'verdict': 'unresolved'}), *rows[1:])})
        expected = 'association_unresolved_verdict'
    else:
        faulty = final
        expected = 'association_stray_cause'

    class ObservationRepository:
        def read_model(self, rel):
            if rel == 'stage2/observations/book.json':
                return book
            if rel == 'stage2/observations/names.json':
                return _names_observation(book)
            return {f'stage2/observations/ch{no:02d}.json': obs
                    for no, obs in chapters.items()}[rel]

    snapshot = faulty.model_dump(mode='json')
    args = dict(evidence=EVIDENCE, budget=2000, chapter_count=len(chapters),
                hook_constraints=HookConstraints(target_chars=100, hard_max_chars=200))
    preview = dry_run_patch(ObservationRepository(), 'associate', snapshot, **args)
    assert preview['status'] == 'ran'
    assert preview['association_phase'] == 'identity' and preview['final_status'] == 'not_run'
    assert any(issue.code == expected for issue in preview['undecided'])
    assert snapshot == faulty.model_dump(mode='json')
    green = dry_run_patch(ObservationRepository(), 'associate',
                          identity.model_dump(mode='json'), **args)
    assert not green['errors'] and not green['undecided'], green
    assert green['association_phase'] == 'identity' and green['final_status'] == 'not_run'


def test_pairable_opens_exclude_locally_closed_loops():
    """判据（2026-10-08，《夫君的报恩》实证 loop_already_closed 死锁）：章内
    自开自闭的 open 已有归属，**不得再进跨章配对候选的 open 端**——close 端
    早已排除（RC-65）而 open 端曾直接遍历 loop_opens 全集，候选（章内已闭环
    open × 后章 close）一旦被模型按语义答 pair，compile 执行到章内 close 之后
    的跨章 close 必判重复 close，重问轮全部白烧。跨章同 ref 的正常配对候选
    （open 与 close 分属两章、句柄不同）不受影响。"""
    from novel.stages.stage2.associate import loop_pairing_issues
    book = _book_wire()
    ch01 = {'protocol_version': CPV, 'beats': [
        _beat('开合都在本章', ('e01_01',), loops=[
            {'ref': 'q1', 'op': 'open', 'question': '本章自问'},
            {'ref': 'q1', 'op': 'close', 'resolution': '本章自答'}]),
        _beat('未闭合的悬念', ('e01_02',), loops=[
            {'ref': 'q2', 'op': 'open', 'question': '跨章悬念'}]),
    ]}
    ch02 = {'protocol_version': CPV, 'beats': [
        _beat('两笔跨章收束', ('e02_01',), loops=[
            {'ref': 'q3', 'op': 'close', 'resolution': '异名收束'},
            {'ref': 'q2', 'op': 'close', 'resolution': '同名收束'}]),
    ]}
    book_obs, chapters = _observations(book, {1: ch01, 2: ch02})
    space = project_association_space(book_obs, chapters)
    # 两端对称排除：q1 章内自开自闭——pairable_opens／pairable_closes 都不含。
    assert 'ch01.q1' not in space.pairable_opens and 'ch01.q1' in space.loop_opens
    assert 'ch01.q1' not in space.pairable_closes
    candidates = project_identity_candidates(space, book_obs)
    pairs = {(c.open, c.close) for c in candidates.loop_pairing}
    # 缺陷反例：章内已闭环 open 不产生跨章候选。
    assert ('ch01.q1', 'ch02.q3') not in pairs
    # 正常面保留：未闭合 open × 跨章 close（异名）；跨章同 ref 的显式配对。
    assert ('ch01.q2', 'ch02.q3') in pairs
    assert ('ch01.q2', 'ch02.q2') in pairs
    # 验收层对称防御（wire 直通／手改面）：open 端章内已闭环的 pair 拒收。
    stray = AssociateObservation.model_validate({
        'protocol_version': 'stage2-associate-v5',
        'same_proposition': [], 'loop_pairing': [
            {'open': 'ch01.q1', 'close': 'ch02.q3', 'verdict': 'pair'}],
        'cross_chapter_cause': [], 'scene_continuation': []})
    assert any('ch01.q1' in i and '自开自闭' in i
               for i in loop_pairing_issues(stray, space))
