#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_phase2_facts —— facts 词表、带性质证据与 know.fact 引用判据。

【验证边界】书级命题词表（Book facts）与章观察 know.fact 引用的合同行为：
  • book wire v11：facts 无编号（F 编号 f{nn} 由 expand 按列表序派发，I-21）、
    evidence 内引文＝段全文派生、evidence_refs 闭集、known_at_start 持有者闭集
    ＝人物 slug（D-5：不收 public／reader——reader 知情 Phase 3 派生）；
  • canonical v8：facts 带 fact_id 与带性质和极性的 evidence；无 address 字段；
  • chapters wire v11：know 的 fact 与 what 恰写其一（引用不携带文本）；
    f{nn} 引用闭集核对（词表成员资格）；canonical 直通面 fact 引用不带
    statement；
  • compile：fact 引用直接解析（正式 ID＝fact 编号、文本＝词表文本），
    near-synonym 转写经 same verdict 并入 fact 类；
  • D-5：cognition holder=public 在 compile 闭集拒收；
  • fold：facts 的知情时间线读数（known_at_start 起步＋逐认知转移事件＋
    初始已知者又被告知的冗余读数——引用 e00 的开场拍重演豁免（2026-10-08
    口径统一：引子内已完成的告知算开场已知）；T-5：只读读数，不进下游 API）。
全部确定性构造，零模型调用。
"""
from __future__ import annotations

from copy import deepcopy

from contracts.schema.observation import FactWire
from novel.stages.chain import build_evidence
from novel.stages.stage2 import (
    compile_book_observation, compile_chapter_observation, compile_source_truth,
    source_fold,
)
from tests.str06_expression_inputs import sample_book_expression
from tests.test_str06_stage2_publish import (
    CPV, EVIDENCE, _basis, _beat, _names_observation, _names_wire,
    _observations, _plain_effect,
)

HOOK = '新婚夜，夫君坦白借种之约。'


def _book_wire_v9(*facts: dict) -> dict:
    """book wire v9（模型输出面）：facts 无编号、evidence_refs 只给段 id。"""
    characters = {
        'fu_jun': {'canonical': '夫君', 'surfaces': [
            {'surface': '夫君', 'source_refs': ['e01_01']}], 'identity_bindings': []},
        'yun_niang': {'canonical': '云娘', 'surfaces': [
            {'surface': '云娘', 'source_refs': ['e01_02']}], 'identity_bindings': []},
    }
    return {**sample_book_expression(characters),
            'protocol_version': 'stage2-book-v11',
            'characters': characters, 'initial_state': [], 'world_constraints': [],
            'facts': list(facts)}


def test_wire_facts_dispatch_f_ids_and_derive_basis_quotes():
    """wire v9 → canonical v6：F 编号按列表序派发（I-21），引文＝段全文派生
    （模型只选段、代码切原文），known_at_start 闭集＝人物 slug。"""
    wire = _book_wire_v9(
        {'statement': '夫君藏身床底', 'evidence': [{'kind': 'confirmed', 'polarity': 'positive', 'holder': 'yun_niang', 'evidence_refs': ['e01_01']}],
         'known_at_start': [{'holder': 'yun_niang', 'polarity': 'positive'}]},
        {'statement': '玉佩刻着沈字', 'evidence': [{'kind': 'confirmed', 'polarity': 'positive', 'holder': None, 'evidence_refs': ['e01_01']}],
         'known_at_start': []})
    observation, errors = compile_book_observation(wire, evidence=EVIDENCE)
    assert errors == [], errors
    assert [fact.fact_id for fact in observation.facts] == ['f01', 'f02']
    assert [(item.holder, item.polarity) for item in observation.facts[0].known_at_start] == [('yun_niang', 'positive')]
    assert observation.facts[0].evidence[0].basis[0].quote == EVIDENCE['e01_01']['text'], \
        '引文＝段全文（构造上不存在转写）'
    assert not hasattr(observation.facts[0], 'address'), 'D-3：facts 不带 address'
    # 位置路径：book_patch_locate 同管道（facts[i] 条目可定位）。
    from novel.stages.stage2 import book_patch_locate
    bad = deepcopy(wire)
    bad['facts'][0]['evidence'][0]['evidence_refs'] = ['e99_99']
    _obs, errors = compile_book_observation(bad, evidence=EVIDENCE)
    assert any('e99_99' in e for e in errors), errors
    targets = book_patch_locate(bad, errors, evidence=EVIDENCE)
    assert targets is not None and ('facts', 0, 'evidence', 0) in [t[0] for t in targets], targets


def test_wire_facts_reject_unknown_holders_and_bad_shapes():
    """known_at_start 引用未知人物／evidence_refs 越闭集逐条精确拒。"""
    wire = _book_wire_v9(
        {'statement': '命题甲', 'evidence': [{'kind': 'confirmed', 'polarity': 'positive', 'holder': None, 'evidence_refs': ['e01_01']}],
         'known_at_start': [{'holder': 'ghost', 'polarity': 'positive'}]})
    _obs, errors = compile_book_observation(wire, evidence=EVIDENCE)
    assert any('known_at_start' in e and 'ghost' in e for e in errors), errors
    wire = _book_wire_v9({'statement': '命题甲', 'evidence': [{'kind': 'confirmed', 'polarity': 'positive', 'holder': None, 'evidence_refs': ['e09_99']}], 'known_at_start': []})
    _obs, errors = compile_book_observation(wire, evidence=EVIDENCE)
    assert any('e09_99' in e for e in errors), errors
    # D-5 反例：reader 也不是词表持有者（Phase 3 才由 compile 派生读者知情）。
    wire = _book_wire_v9(
        {'statement': '命题甲', 'evidence': [{'kind': 'confirmed', 'polarity': 'positive', 'holder': None, 'evidence_refs': ['e01_01']}],
         'known_at_start': [{'holder': 'reader', 'polarity': 'positive'}]})
    _obs, errors = compile_book_observation(wire, evidence=EVIDENCE)
    assert any('reader' in e for e in errors), errors


def test_fact_malformed_containers_report_shape_errors_without_crashing():
    """坏 facts／known_at_start 容器和不可 hash 元素进入免费形状修复。

    wire 验证前的事实闭集检查不能对 list/dict 做 set 查询，也不能迭代数字。
    """
    from novel.stages.stage2 import book_patch_locate
    for holders in ([['fu_jun']], [{'char_id': 'fu_jun'}], 7, 'fu_jun'):
        wire = _book_wire_v9({'statement': '甲', 'evidence': [{'kind': 'confirmed', 'polarity': 'positive', 'holder': None, 'evidence_refs': ['e01_01']}],
                              'known_at_start': holders})
        before = deepcopy(wire)
        observation, errors = compile_book_observation(wire, evidence=EVIDENCE)
        assert observation is None and errors, holders
        targets = book_patch_locate(wire, errors, evidence=EVIDENCE)
        assert targets is not None and [path for path, _errors in targets] == [('facts', 0)]
        assert wire == before
    for facts in (7, {'statement': '甲'}):
        wire = _book_wire_v9()
        wire['facts'] = facts
        observation, errors = compile_book_observation(wire, evidence=EVIDENCE)
        assert observation is None and errors, facts
        # 顶层数组本身坏形状必须整份返修；无需伪造条目定位。
        assert book_patch_locate(wire, errors, evidence=EVIDENCE) is None


def test_reader_is_reserved_in_all_character_carriers_but_reader_proxy_is_valid():
    """读者认知 holder 不能伪装成书级人物；三人物载体共享精确保留字闸。"""
    import pytest
    from pydantic import ValidationError
    from contracts.schema.observation import BookObservation, BookObservationWire
    from contracts.schema.source import CharactersCore
    from novel.stages.stage2 import book_patch_locate, character_core_view

    wire = _book_wire_v9()
    wire['characters']['reader'] = deepcopy(wire['characters']['fu_jun'])
    with pytest.raises(ValidationError, match='人物键禁 reader') as caught:
        BookObservationWire.model_validate(wire)
    assert any(e['loc'][:2] == ('characters', 'reader')
               for e in caught.value.errors())
    canonical = _book_v6(wire['characters'], [])
    with pytest.raises(ValidationError, match='人物键禁 reader'):
        BookObservation.model_validate(canonical)
    core = {'initial_knowledge': [], 'characters': {'reader': {'canonical': '夫君', 'surfaces': [
        {'surface': '夫君', 'from_chapter': 1, 'source_refs': ['e01_01']}], 'identity_bindings': []}},
        'initial_state': []}
    with pytest.raises(ValidationError, match='人物键禁 reader'):
        CharactersCore.model_validate(core)
    observation, errors = compile_book_observation(wire, evidence=EVIDENCE)
    assert observation is None and errors
    targets = book_patch_locate(wire, errors, evidence=EVIDENCE)
    assert targets is not None and [path for path, _ in targets] == [('characters', 'reader')]

    wire['characters']['reader_proxy'] = wire['characters'].pop('reader')
    wire['facts'] = [{'statement': '甲', 'evidence': [{'kind': 'confirmed', 'polarity': 'positive', 'holder': 'reader_proxy', 'evidence_refs': ['e01_01']}],
                     'known_at_start': [{'holder': 'reader_proxy', 'polarity': 'positive'}]}]
    BookObservationWire.model_validate(wire)
    observation, errors = compile_book_observation(wire, evidence=EVIDENCE)
    assert not errors and [(item.holder, item.polarity) for item in observation.facts[0].known_at_start] == [('reader_proxy', 'positive')]
    projected, errors = character_core_view(observation, evidence=EVIDENCE)
    assert not errors and 'reader_proxy' in projected.characters
    BookObservation.model_validate(observation.model_dump(mode='json'))
    CharactersCore.model_validate(projected.model_dump(mode='json'))


def test_reader_initial_knowledge_is_refused_even_when_raw_characters_register_it():
    """同份坏 wire 把 reader 注册成人物不能让 facts 初始读者知情获得合法性。"""
    from novel.stages.stage2 import book_patch_locate
    wire = _book_wire_v9({'statement': '甲', 'evidence': [{'kind': 'confirmed', 'polarity': 'positive', 'holder': None, 'evidence_refs': ['e01_01']}],
                          'known_at_start': [{'holder': 'reader', 'polarity': 'positive'}]})
    wire['characters']['reader'] = deepcopy(wire['characters']['fu_jun'])
    observation, errors = compile_book_observation(wire, evidence=EVIDENCE)
    assert observation is None
    assert any('known_at_start' in error and 'reader' in error for error in errors)
    targets = book_patch_locate(wire, errors, evidence=EVIDENCE)
    assert targets is not None and {path for path, _ in targets} == {
        ('characters', 'reader'), ('facts', 0)}


def test_fact_wire_model_is_id_free_and_exactly_shaped():
    """FactWire（模型输出面）无编号位、无 address 位：编造编号即形状失败。"""
    good = FactWire.model_validate(
        {'statement': '甲', 'evidence': [{'kind': 'confirmed', 'polarity': 'positive', 'holder': None, 'evidence_refs': ['e01_01']}], 'known_at_start': ()})
    assert good.statement == '甲'
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        FactWire.model_validate(
            {'statement': '甲', 'evidence': [{'kind': 'confirmed', 'polarity': 'positive', 'holder': None, 'evidence_refs': ['e01_01']}],
             'known_at_start': (), 'fact_id': 'f01'}), '编号不进 wire（I-21）'
    with pytest.raises(ValidationError):
        FactWire.model_validate(
            {'statement': '甲', 'evidence': [{'kind': 'confirmed', 'polarity': 'positive', 'holder': None, 'evidence_refs': ['e01_01']}],
             'known_at_start': (), 'address': 'world'}), 'D-3：无 address 字段'


def _book_v6(characters: dict, facts: list) -> dict:
    """canonical v6 直通面：facts 带 F 编号与 basis（引文＝段全文前 6 字）。"""
    return {**sample_book_expression(characters),
            'protocol_version': 'stage2-book-v8',
            'characters': characters, 'initial_state': [], 'world_constraints': [],
            'facts': facts}


def _chapter_wire_v9(know: list[dict]) -> dict:
    """章瘦 wire v9（模型输出面）：fact 与 what 恰写其一。"""
    return {'protocol_version': 'stage2-chapters-v11', 'beats': [
        {'location_reader_visible': False, 'event': '云娘起疑', 'on_stage': ['yun_niang'], 'start': 'e01_01',
         'change': [], 'know': know, 'loops': []}]}


def test_slim_know_fact_xor_what_and_closed_set():
    """wire v9：fact 与 what 恰写其一；f{nn} 引用闭集核对（词表成员资格）。"""
    wire = _chapter_wire_v9([{'who': 'yun_niang', 'how': 'suspects',
                              'fact': 'f01', 'evidence_refs': ['e01_01'], 'op': 'set', 'polarity': 'positive', 'support': 'inference', 'reader_visible': False}])
    observation, errors = compile_chapter_observation(
        wire, evidence_ids=EVIDENCE, chapter=1,
        character_slugs=('fu_jun', 'yun_niang'), fact_ids=frozenset({'f01'}))
    assert observation is not None and errors == [], errors
    prop = observation.beats[0].cognition[0].proposition
    assert prop.ref == 'f01' and prop.statement is None, '引用不携带文本（expand 直通）'

    import pytest
    from pydantic import ValidationError
    from novel.stages.stage2.slim import expand_chapter_wire
    # 形状：fact 与 what 恰写其一。
    with pytest.raises(ValidationError):
        expand_chapter_wire(_chapter_wire_v9([
            {'who': 'yun_niang', 'how': 'suspects', 'fact': 'f01',
             'what': '夫君藏身床底', 'evidence_refs': ['e01_01'], 'op': 'set', 'polarity': 'positive', 'support': 'inference', 'reader_visible': False}]))
    with pytest.raises(ValidationError):
        expand_chapter_wire(_chapter_wire_v9([
            {'who': 'yun_niang', 'how': 'suspects',
             'evidence_refs': ['e01_01'], 'op': 'set', 'polarity': 'positive', 'support': 'inference', 'reader_visible': False}]))
    # 闭集：词表外的 f 编号即拒（fail fast——fact_ids 缺省同样拒）。
    _obs, errors = compile_chapter_observation(
        _chapter_wire_v9([{'who': 'yun_niang', 'how': 'suspects',
                           'fact': 'f99', 'evidence_refs': ['e01_01'], 'op': 'set', 'polarity': 'positive', 'support': 'inference', 'reader_visible': False}]),
        evidence_ids=EVIDENCE, chapter=1,
        character_slugs=('fu_jun', 'yun_niang'), fact_ids=frozenset({'f01'}))
    assert any('词表之外' in e and 'f99' in e for e in errors), errors
    _obs, errors = compile_chapter_observation(
        _chapter_wire_v9([{'who': 'yun_niang', 'how': 'suspects',
                           'fact': 'f01', 'evidence_refs': ['e01_01'], 'op': 'set', 'polarity': 'positive', 'support': 'inference', 'reader_visible': False}]),
        evidence_ids=EVIDENCE, chapter=1, character_slugs=('fu_jun', 'yun_niang'))
    assert any('词表之外' in e for e in errors), '未提供词表闭集即 fail fast'


def test_compile_resolves_fact_refs_and_merges_near_synonym_via_verdict():
    """compile：fact 引用直接解析（正式 ID＝fact 编号、文本＝词表文本）；
    near-synonym 转写经 verdict=same 并入 fact 类——fact 侧为 canonical。"""
    from contracts.schema.observation import AssociateObservation
    characters = {
        'fu_jun': {'canonical': '夫君', 'surfaces': [
            {'surface': '夫君', 'source_refs': ['e01_01']}], 'identity_bindings': []},
        'yun_niang': {'canonical': '云娘', 'surfaces': [
            {'surface': '云娘', 'source_refs': ['e01_02']}], 'identity_bindings': []}}
    book = _book_v6(characters, [{
        'fact_id': 'f01', 'statement': '夫君藏身床底',
        'evidence': [{'kind': 'confirmed', 'polarity': 'positive', 'holder': 'yun_niang', 'basis': [{'segment': 'e01_01', 'quote': EVIDENCE['e01_01']['text'][:6]}]}],
        'known_at_start': [{'holder': 'yun_niang', 'polarity': 'positive'}]}])
    ch01 = {'protocol_version': CPV, 'beats': [
        _beat('云娘起疑', ('e01_01',), participants=('yun_niang',),
              cognition=[{'proposition': {'ref': 'f01', 'statement': None},
                          'holder': 'yun_niang', 'mode': 'suspects',
                          'basis': _basis('e01_01'), 'op': 'set', 'polarity': 'positive', 'support': 'inference', 'reader_visible': False}])]}
    ch02 = {'protocol_version': CPV, 'beats': [
        _beat('词表外转写', ('e02_01',), participants=('yun_niang',),
              cognition=[{'proposition': {'ref': 'p_drift', 'statement': '夫君藏身于床底'},
                          'holder': 'yun_niang', 'mode': 'confirms',
                          'basis': _basis('e02_01'), 'op': 'set', 'polarity': 'positive', 'support': 'confirmed', 'reader_visible': False}])]}
    ch03 = {'protocol_version': CPV, 'beats': [
        _beat('揭示', ('e03_01',), participants=('yun_niang', 'fu_jun'),
              access='narrated',
              cognition=[{'proposition': {'ref': 'f01', 'statement': None},
                          'holder': 'yun_niang', 'mode': 'confirms',
                          'basis': _basis('e03_01'), 'op': 'set', 'polarity': 'positive', 'support': 'confirmed', 'reader_visible': True}])]}
    book_obs, chapters = _observations(book, {1: ch01, 2: ch02, 3: ch03})
    associate = AssociateObservation.model_validate({
        'protocol_version': 'stage2-associate-v5',
        'same_proposition': [{'left': 'ch02.p_drift', 'right': 'f01',
                              'verdict': 'same'}],
        'loop_pairing': [], 'cross_chapter_cause': [],
        'scene_continuation': []})
    from tests.str06_facilities import complete_associate_fixture
    associate = complete_associate_fixture(book_obs, chapters, associate)
    outcome = compile_source_truth(book=book_obs, chapters=chapters,
                                   names=_names_observation(book_obs),
                                   associate=associate, evidence=EVIDENCE)
    assert outcome.errors == [] and outcome.undecided == [], (
        outcome.errors, [str(i) for i in outcome.undecided])
    chapters_out = outcome.blueprint.chapters
    assert chapters_out[0].beats[0].cognition[0].proposition.ref == 'f01'
    assert chapters_out[0].beats[0].cognition[0].proposition.statement == '夫君藏身床底'
    # near-synonym 转写并入 fact 类：正式 ID＝fact 编号、文本＝词表文本。
    merged = chapters_out[1].beats[0].cognition[0].proposition
    assert merged.ref == 'f01' and merged.statement == '夫君藏身床底'
    assert chapters_out[2].beats[0].cognition[0].proposition.ref == 'f01'
    # fold 时间线：known_at_start 起步＋逐认知转移事件；冗余告知读数（度量不是门）。
    timeline = outcome.fold['knowledge']['f01']
    assert timeline['statement'] == '夫君藏身床底'
    assert timeline['known_at_start'] == [{'holder': 'yun_niang', 'polarity': 'positive'}]
    assert outcome.characters.initial_knowledge[0].holder == 'yun_niang'
    assert outcome.characters.initial_knowledge[0].proposition.ref == 'f01'
    assert 'yun_niang=learns[positive](known_at_start)' in '\n'.join(outcome.fold['snapshots'][0])
    # 逐认知转移事件（拍序，同一持有者的多次转移都在）：ch1 起疑、ch2 经 same
    # 并入的转写证实、ch3 叙述直陈再证实＋派生的读者 learns。
    assert [(event['holder'], event['mode'], event['at'])
            for event in timeline['events']] == [
        ('yun_niang', 'suspects', 'b01_01'), ('yun_niang', 'confirms', 'b02_01'),
        ('yun_niang', 'confirms', 'b03_01'), ('reader', 'learns', 'b03_01')]
    assert any('初始已知者 yun_niang 又被拍' in line and 'confirms' in line
               for line in outcome.fold['events']), outcome.fold['events']


def test_fold_redundancy_reading_exempts_pre_chapter_replay_beats():
    """判据（2026-10-08 口径统一）：「故事开始」＝第 1 章第一拍开始——引子
    （e00）内已完成的告知算开场已知（结果记 known_at_start），引用 e00 的
    开场拍重演获知过程是预期形态，不作「两处落账」错位读数；不引用章前文字
    的章内告知保持信号。豁免只影响读数，时间线逐转移照记。"""
    from contracts.schema.observation import AssociateObservation
    characters = {
        'fu_jun': {'canonical': '夫君', 'surfaces': [
            {'surface': '夫君', 'source_refs': ['e01_01']}], 'identity_bindings': []},
        'yun_niang': {'canonical': '云娘', 'surfaces': [
            {'surface': '云娘', 'source_refs': ['e01_02']}], 'identity_bindings': []}}
    book = _book_v6(characters, [{
        'fact_id': 'f01', 'statement': '夫君藏身床底',
        'evidence': [{'kind': 'confirmed', 'polarity': 'positive', 'holder': 'yun_niang', 'basis': _basis('e00_01')}],
        'known_at_start': [{'holder': 'yun_niang', 'polarity': 'positive'}]}])
    ch01 = {'protocol_version': CPV, 'beats': [
        _beat('新婚夜坦白', ('e00_01',), participants=('yun_niang', 'fu_jun'),
              cognition=[{'proposition': {'ref': 'f01', 'statement': None},
                          'holder': 'yun_niang', 'mode': 'learns',
                          'basis': _basis('e00_01'), 'op': 'set', 'polarity': 'positive', 'support': 'confirmed', 'reader_visible': False}])]}
    ch02 = {'protocol_version': CPV, 'beats': [
        _beat('事后又证实', ('e02_01',), participants=('yun_niang',),
              cognition=[{'proposition': {'ref': 'f01', 'statement': None},
                          'holder': 'yun_niang', 'mode': 'confirms',
                          'basis': _basis('e02_01'), 'op': 'set', 'polarity': 'positive', 'support': 'confirmed', 'reader_visible': False}])]}
    book_obs, chapters = _observations(book, {1: ch01, 2: ch02})
    from tests.str06_facilities import complete_associate_fixture
    associate = complete_associate_fixture(
        book_obs, chapters, AssociateObservation.model_validate({
            'protocol_version': 'stage2-associate-v5',
            'same_proposition': [], 'loop_pairing': [],
            'cross_chapter_cause': [], 'scene_continuation': []}))
    outcome = compile_source_truth(book=book_obs, chapters=chapters,
                                   names=_names_observation(book_obs),
                                   associate=associate, evidence=EVIDENCE)
    assert outcome.errors == [] and outcome.undecided == [], (
        outcome.errors, [str(i) for i in outcome.undecided])
    events = outcome.fold['events']
    assert not any('初始已知者 yun_niang 又被拍 b01_01' in line
                   for line in events), events
    assert any('初始已知者 yun_niang 又被拍 b02_01' in line and 'confirms' in line
               for line in events), events
    assert [(event['holder'], event['mode'], event['at'])
            for event in outcome.fold['knowledge']['f01']['events']] == [
        ('yun_niang', 'learns', 'b01_01'), ('yun_niang', 'confirms', 'b02_01')]


def test_fold_timeline_keeps_every_transition_not_just_the_last_per_holder():
    """判据（D#9b）：知情时间线是“逐认知转移事件”——同一持有者先起疑、后证实，
    两次转移都在，首次获知时点不被后来的转移覆盖（认知层当前态只留最后一次，
    时间线读数另记流水）。"""
    from contracts.schema.observation import EvidenceQuote, Fact
    from contracts.schema.source import (
        Beat, BlueprintsArtifactV18, ChapterBlueprint, CharactersCore,
        Cognition, PropositionRef, SceneSpan,
    )
    from novel.stages.stage2.expression import compile_source_expression
    core = CharactersCore(initial_knowledge=[], characters={
        'yun_niang': {'canonical': '云娘', 'surfaces': [
            {'surface': '云娘', 'from_chapter': 1, 'until_chapter': None,
             'source_refs': ['e01_02']}], 'identity_bindings': []}})
    fact = Fact(fact_id='f01', statement='夫君藏身床底',
                evidence=({'kind': 'confirmed', 'polarity': 'positive', 'holder': None, 'basis': (EvidenceQuote(segment='e01_01', quote='夫君藏进床底'),)},),
                known_at_start=())

    def beat(no: int, mode: str) -> Beat:
        return Beat(
            beat_id=f'b01_{no:02d}', event=f'事件{no}', participants=('yun_niang',),
            source_refs=('e01_01',), scene_boundary='new' if no == 1 else 'continue',
            temporal_mode='single',
            cognition=(Cognition(
                proposition=PropositionRef(ref='f01', statement='夫君藏身床底'),
                holder='yun_niang', mode=mode,
                basis=(EvidenceQuote(segment='e01_01', quote='夫君藏进床底'),), op='set', polarity='positive', support='confirmed', reader_visible=True),))
    book_obs = _book_obs_for_blueprint()
    blueprint = BlueprintsArtifactV18(
        source_expression=compile_source_expression(book_obs.expression, texts=None),
        chapters=(ChapterBlueprint(
            chapter=1, beats=(beat(1, 'suspects'), beat(2, 'confirms')),
            scenes=(SceneSpan(scene_id='sc01', beat_ids=('b01_01', 'b01_02')),)),),
        evidence={'e01_01': EVIDENCE['e01_01']['text']})
    timeline = source_fold(core, blueprint, facts=(fact,)).knowledge_timeline['f01']
    assert [(e['holder'], e['mode'], e['at']) for e in timeline['events']] == [
        ('yun_niang', 'suspects', 'b01_01'), ('yun_niang', 'confirms', 'b01_02')]


def _book_obs_for_blueprint():
    from contracts.schema.observation import BookObservation
    return BookObservation.model_validate(_book_v6({
        'yun_niang': {'canonical': '云娘', 'surfaces': [
            {'surface': '云娘', 'source_refs': ['e01_02']}], 'identity_bindings': []}}, []))


def test_one_proposition_same_as_two_facts_stays_undecided():
    """判据（D#5b）：同一章命题同时 same 于 f01 与 f02，等于声称两条词表命题是
    同一件事。并查集会把两个 fact 连成一类，蓝图里它们却仍是两个正式 ID——
    保持未决（词表去重属 book 观察），拒绝发布。"""
    from contracts.schema.observation import AssociateObservation
    characters = {
        'fu_jun': {'canonical': '夫君', 'surfaces': [
            {'surface': '夫君', 'source_refs': ['e01_01']}], 'identity_bindings': []},
        'yun_niang': {'canonical': '云娘', 'surfaces': [
            {'surface': '云娘', 'source_refs': ['e01_02']}], 'identity_bindings': []}}
    book = _book_v6(characters, [
        {'fact_id': 'f01', 'statement': '夫君藏身床底',
         'evidence': [{'kind': 'confirmed', 'polarity': 'positive', 'holder': None, 'basis': _basis('e01_01')}], 'known_at_start': []},
        {'fact_id': 'f02', 'statement': '夫君藏身在床底',
         'evidence': [{'kind': 'confirmed', 'polarity': 'positive', 'holder': None, 'basis': _basis('e01_02')}], 'known_at_start': []}])
    chapters = {
        1: {'protocol_version': CPV, 'beats': [_beat(
            '起疑', ('e01_01',), participants=('yun_niang',),
            cognition=[{'proposition': {'ref': 'f01', 'statement': None},
                        'holder': 'yun_niang', 'mode': 'suspects',
                        'basis': _basis('e01_01'), 'op': 'set', 'polarity': 'positive', 'support': 'inference', 'reader_visible': False}])]},
        2: {'protocol_version': CPV, 'beats': [_beat(
            '转写', ('e02_01',), participants=('yun_niang',),
            cognition=[{'proposition': {'ref': 'pb', 'statement': '夫君藏身于床底'},
                        'holder': 'yun_niang', 'mode': 'learns',
                        'basis': _basis('e02_01'), 'op': 'set', 'polarity': 'positive', 'support': 'confirmed', 'reader_visible': False}])]}}
    book_obs, parsed = _observations(book, chapters)

    def compile_with(verdicts):
        associate = AssociateObservation.model_validate({
            'protocol_version': 'stage2-associate-v5', 'loop_pairing': [],
            'cross_chapter_cause': [], 'scene_continuation': [],
            'same_proposition': verdicts})
        from tests.str06_facilities import complete_associate_fixture
        associate = complete_associate_fixture(book_obs, parsed, associate)
        return compile_source_truth(book=book_obs, chapters=parsed,
                                    names=_names_observation(book_obs),
                                    associate=associate, evidence=EVIDENCE)

    result = compile_with([{'left': 'ch02.pb', 'right': 'f01', 'verdict': 'same'},
                           {'left': 'ch02.pb', 'right': 'f02', 'verdict': 'same'}])
    assert not result.publishable and result.errors == []
    merged = [issue for issue in result.undecided if issue.code == 'association_fact_merge']
    assert [issue.location for issue in merged] == [('f01', 'f02')]
    assert merged[0].owner == 'associate'
    # 绿对照：只并入其中一个 fact，另一个判 distinct，自洽、可发布。
    clean = compile_with([{'left': 'ch02.pb', 'right': 'f01', 'verdict': 'same'},
                          {'left': 'ch02.pb', 'right': 'f02', 'verdict': 'distinct'}])
    assert clean.publishable, (clean.errors, [str(i) for i in clean.undecided])


def test_compile_rejects_public_holder_after_d5():
    """D-5：public 不在认知持有者闭集——compile 防御按条目拒（Stage4 的显式
    豁免分支对新根不可达，观察面先拒）。"""
    from contracts.schema.observation import AssociateObservation
    characters = {
        'fu_jun': {'canonical': '夫君', 'surfaces': [
            {'surface': '夫君', 'source_refs': ['e01_01']}], 'identity_bindings': []},
        'yun_niang': {'canonical': '云娘', 'surfaces': [
            {'surface': '云娘', 'source_refs': ['e01_02']}], 'identity_bindings': []}}
    book = _book_v6(characters, [])
    ch01 = {'protocol_version': CPV, 'beats': [
        _beat('公开知情', ('e01_01',), participants=('fu_jun',),
              cognition=[{'proposition': {'ref': 'p_pub', 'statement': '满城皆知'},
                          'holder': 'public', 'mode': 'learns',
                          'basis': _basis('e01_01'), 'op': 'set', 'polarity': 'positive', 'support': 'confirmed', 'reader_visible': False}])]}
    book_obs, chapters = _observations(book, {1: ch01})
    associate = AssociateObservation.model_validate({
        'protocol_version': 'stage2-associate-v5', 'same_proposition': [],
        'loop_pairing': [], 'cross_chapter_cause': [],
        'scene_continuation': []})
    outcome = compile_source_truth(book=book_obs, chapters=chapters,
                                   names=_names_observation(book_obs),
                                   associate=associate, evidence=EVIDENCE)
    assert any('holder 引用未知人物 public' in e for e in outcome.errors), \
        (outcome.errors, [str(i) for i in outcome.undecided])
    assert not outcome.publishable, '机械缺陷即拒发（run_compile 据此 typed 停止）'


def test_fold_knowledge_timeline_reads_only():
    """T-5：知情时间线是 fold 结构化只读读数——无 facts 时空读数；有 facts 时
    只按词表命题聚合，不改动事实层快照。"""
    from contracts.schema.source import CharactersCore, InitialKnowledgeAtom
    core = CharactersCore(initial_knowledge=[], characters={
        'fu_jun': {'canonical': '夫君', 'surfaces': [
            {'surface': '夫君', 'from_chapter': 1, 'until_chapter': None,
             'source_refs': ['e01_01']}], 'identity_bindings': []}})
    empty = source_fold(core, _blueprint_stub(), facts=())
    assert empty.knowledge_timeline == {}
    # 直接用契约模型构造最小 fact（不经过 book 验收——fold 只读）。
    from contracts.schema.observation import Fact, EvidenceQuote
    fact = Fact(fact_id='f01', statement='甲',
                evidence=({'kind': 'confirmed', 'polarity': 'positive', 'holder': None, 'basis': (EvidenceQuote(segment='e01_01', quote='甲'),)},),
                known_at_start=[{'holder': 'fu_jun', 'polarity': 'positive'}])
    with_facts = source_fold(core, _blueprint_stub(), facts=(fact,))
    assert with_facts.knowledge_timeline == {
        'f01': {'statement': '甲', 'known_at_start': [{'holder': 'fu_jun', 'polarity': 'positive'}], 'events': []}}
    assert with_facts.snapshots[0] == [], '词表只产生读数，不能给正式初态补知识'
    formal = core.model_copy(update={'initial_knowledge': (
        InitialKnowledgeAtom(
            knowledge_id='k0_01', holder='fu_jun',
            proposition={'ref': 'f01', 'statement': '甲'}, polarity='positive',
            basis=(EvidenceQuote(segment='e01_01', quote='甲'),)),)})
    assert 'fu_jun=learns[positive](known_at_start)' in '\n'.join(
        source_fold(formal, _blueprint_stub()).snapshots[0])


def _blueprint_stub():
    """最小蓝图（source_fold 只读消费；一拍一场景）。"""
    from contracts.schema.source import (
        Beat, BlueprintsArtifactV18, ChapterBlueprint, SceneSpan,
    )
    beat = Beat(beat_id='b01_01', event='事件', participants=('fu_jun',),
                source_refs=('e01_01',), scene_boundary='new', temporal_mode='single')
    from contracts.schema.observation import BookObservation
    from novel.stages.stage2.expression import compile_source_expression
    book_obs = BookObservation.model_validate(_book_v6({
        'fu_jun': {'canonical': '夫君', 'surfaces': [
            {'surface': '夫君', 'source_refs': ['e01_01']}], 'identity_bindings': []}}, []))
    return BlueprintsArtifactV18(
        source_expression=compile_source_expression(book_obs.expression, texts=None),
        chapters=(ChapterBlueprint(chapter=1, beats=(beat,),
                                   scenes=(SceneSpan(scene_id='sc01',
                                                     beat_ids=('b01_01',)),)),),
        evidence={'e01_01': EVIDENCE['e01_01']['text']})


# ───────── 2026-10-08 审阅整改：book wire 形状错误的条目级路由 ─────────

_GOOD_ATOM = {
    'target': {'kind': 'character', 'char_id': 'fu_jun'}, 'key': 'identity',
    'value': 'x', 'persistence': 'stable', 'source_refs': ['e01_01'],
    'evidence_refs': ['e01_01']}


def _wire_with_atom(**overrides) -> dict:
    atom = {**_GOOD_ATOM, **overrides}
    for key in [k for k, v in overrides.items() if v is None]:
        atom.pop(key)
    wire = _book_wire_v9()
    wire['initial_state'] = [atom]
    return wire


def test_book_wire_shape_errors_get_item_level_paths():
    """实证反例（2026-10-08 审阅 B#3／A#7）：book wire 的 pydantic 形状错误原先
    全部退整份返修——`wire_shape_issues(kind='book')` 没有 facts 分支，且 wire
    层已按条目报告之后，canonical 复验又追加一条 path=None 的重复整份错误，
    使 `book_patch_locate` 全部返回 None（模型最容易在 initial_state 自造键，
    每次整本重购）。四类形状错误现在各得一条条目级路径、不再有重复整份项。"""
    from novel.stages.stage2 import book_patch_locate
    cases = {
        'fact evidence_refs 为空': (
            _book_wire_v9({'statement': '甲', 'evidence': [{'kind': 'confirmed', 'polarity': 'positive', 'holder': None, 'evidence_refs': []}],
                           'known_at_start': []}), ('facts', 0)),
        'fact 缺 statement': (
            _book_wire_v9({'evidence': [{'kind': 'confirmed', 'polarity': 'positive', 'holder': None, 'evidence_refs': ['e01_01']}], 'known_at_start': []}), ('facts', 0)),
        'initial_state 表外键': (_wire_with_atom(key='foo_bar'), ('initial_state', 0)),
        'initial_state 缺 persistence': (
            _wire_with_atom(persistence=None), ('initial_state', 0)),
    }
    for label, (wire, expected_path) in cases.items():
        _obs, errors = compile_book_observation(wire, evidence=EVIDENCE)
        assert errors, label
        assert all('book 观察协议校验失败' not in e for e in errors), (label, errors)
        located = book_patch_locate(wire, errors, evidence=EVIDENCE)
        assert located is not None, (label, errors)
        assert [path for path, _ in located] == [expected_path], (label, located)


def test_book_initial_key_outside_closed_set_reports_once_with_guidance():
    """表外键只报一条（wire 模型与 ④ 闸原先各报一遍），消息带闭集与位置去向的
    指引而不是 pydantic 的整段正则；scene_location 另有拍级字段的去向说明。"""
    _obs, errors = compile_book_observation(
        _wire_with_atom(key='foo_bar'), evidence=EVIDENCE)
    assert len(errors) == 1, errors
    assert '变化键闭集' in errors[0] and 'String should match' not in errors[0]
    _obs, errors = compile_book_observation(
        _wire_with_atom(key='scene_location'), evidence=EVIDENCE)
    assert len(errors) == 1, errors
    assert 'residence' in errors[0] and '第 1 章' in errors[0]


def test_book_initial_key_with_unhashable_value_is_an_item_error_not_a_crash():
    """key 是 list／dict 时原先 `key not in frozenset` 抛 TypeError，绕过返修。"""
    from novel.stages.stage2 import book_patch_locate
    for bad in (['identity'], {'a': 1}):
        wire = _wire_with_atom(key=bad)
        _obs, errors = compile_book_observation(wire, evidence=EVIDENCE)
        assert errors, bad
        located = book_patch_locate(wire, errors, evidence=EVIDENCE)
        assert located is not None and [p for p, _ in located] == [('initial_state', 0)]
    # canonical 直通面同样不崩。
    characters = {'fu_jun': {'canonical': '夫君', 'surfaces': [
        {'surface': '夫君', 'source_refs': ['e01_01']}], 'identity_bindings': []}}
    canonical = _book_v6(characters, [])
    canonical['initial_state'] = [{
        'target': {'kind': 'character', 'char_id': 'fu_jun'}, 'key': ['identity'],
        'value': 'x', 'persistence': 'stable', 'source_refs': ['e01_01'],
        'basis': [{'segment': 'e01_01', 'quote': EVIDENCE['e01_01']['text']}]}]
    _obs, errors = compile_book_observation(canonical, evidence=EVIDENCE)
    assert errors


def test_book_facts_must_be_a_dense_sequence_at_model_level():
    """实证反例（2026-10-08 审阅 A#5）：`BookObservation.facts` 没有模型级
    fact_id 唯一＋按序连续不变量——wire 路径由 enumerate 派发天然满足，canonical
    直通与手改 book.json 却可重复 f02，compile 的 `{fact.fact_id: fact}` 静默
    覆盖前一条（命题丢失）。"""
    import pytest
    from pydantic import ValidationError
    from contracts.schema.observation import BookObservation
    characters = {'fu_jun': {'canonical': '夫君', 'surfaces': [
        {'surface': '夫君', 'source_refs': ['e01_01']}], 'identity_bindings': []}}

    def fact(fact_id: str, statement: str) -> dict:
        return {'fact_id': fact_id, 'statement': statement,
                'evidence': [{'kind': 'confirmed', 'polarity': 'positive', 'holder': None, 'basis': [{'segment': 'e01_01', 'quote': '新婚夜'}]}],
                'known_at_start': []}

    ok = BookObservation.model_validate(
        _book_v6(characters, [fact('f01', '甲'), fact('f02', '乙')]))
    assert [f.fact_id for f in ok.facts] == ['f01', 'f02']
    for ids in (('f01', 'f01'), ('f02', 'f02'), ('f01', 'f03'), ('f02', 'f01')):
        with pytest.raises(ValidationError, match='fact_id'):
            BookObservation.model_validate(_book_v6(
                characters, [fact(i, f'命题{n}') for n, i in enumerate(ids)]))
    # 直通验收面：重复编号是报告出来的错误，不是静默覆盖。
    dup = _book_v6(characters, [fact('f02', '甲'), fact('f02', '乙')])
    _obs, errors = compile_book_observation(dup, evidence=EVIDENCE)
    assert errors and any('fact_id' in e for e in errors), errors
