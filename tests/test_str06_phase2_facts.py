#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_phase2_facts —— CS-S Phase 2 的 facts 词表与 know.f引用判据。

【验证边界】书级命题词表（Book facts）与章观察 know.fact 引用的合同行为：
  • book wire v9：facts 无编号（F 编号 f{nn} 由 expand 按列表序派发，I-21）、
    basis 引文＝段全文派生、evidence_refs 闭集、known_at_start 持有者闭集
    ＝人物 slug（D-5：不收 public／reader——reader 知情 Phase 3 派生）；
  • canonical v6：facts 带 fact_id 与 basis；D-3：无 address 字段；
  • chapters wire v9：know 的 fact 与 what 恰写其一（引用不携带文本）；
    f{nn} 引用闭集核对（词表成员资格）；canonical 直通面 fact 引用不带
    statement；
  • compile：fact 引用直接解析（正式 ID＝fact 编号、文本＝词表文本），
    near-synonym 转写经 same verdict 并入 fact 类；
  • D-5：cognition holder=public 在 compile 闭集拒收；
  • fold：facts 的知情时间线读数（known_at_start 起步＋逐认知转移事件＋
    初始已知者又被告知的冗余读数；T-5：只读读数，不进下游 API）。
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
            {'surface': '夫君', 'source_refs': ['e01_01']}]},
        'yun_niang': {'canonical': '云娘', 'surfaces': [
            {'surface': '云娘', 'source_refs': ['e01_02']}]},
    }
    return {**sample_book_expression(characters),
            'protocol_version': 'stage2-book-v10',
            'characters': characters, 'initial_state': [], 'world_constraints': [],
            'facts': list(facts)}


def test_wire_facts_dispatch_f_ids_and_derive_basis_quotes():
    """wire v9 → canonical v6：F 编号按列表序派发（I-21），引文＝段全文派生
    （模型只选段、代码切原文），known_at_start 闭集＝人物 slug。"""
    wire = _book_wire_v9(
        {'statement': '夫君藏身床底', 'evidence_refs': ['e01_01'],
         'known_at_start': ['yun_niang']},
        {'statement': '玉佩刻着沈字', 'evidence_refs': ['e01_01'],
         'known_at_start': []})
    observation, errors = compile_book_observation(wire, evidence=EVIDENCE)
    assert errors == [], errors
    assert [fact.fact_id for fact in observation.facts] == ['f01', 'f02']
    assert observation.facts[0].known_at_start == ('yun_niang',)
    assert observation.facts[0].basis[0].quote == EVIDENCE['e01_01']['text'], \
        '引文＝段全文（构造上不存在转写）'
    assert not hasattr(observation.facts[0], 'address'), 'D-3：facts 不带 address'
    # 位置路径：book_patch_locate 同管道（facts[i] 条目可定位）。
    from novel.stages.stage2 import book_patch_locate
    bad = deepcopy(wire)
    bad['facts'][0]['evidence_refs'] = ['e99_99']
    _obs, errors = compile_book_observation(bad, evidence=EVIDENCE)
    assert any('e99_99' in e for e in errors), errors
    targets = book_patch_locate(bad, errors, evidence=EVIDENCE)
    assert targets is not None and ('facts', 0) in [t[0] for t in targets], targets


def test_wire_facts_reject_unknown_holders_and_bad_shapes():
    """known_at_start 引用未知人物／evidence_refs 越闭集逐条精确拒。"""
    wire = _book_wire_v9(
        {'statement': '命题甲', 'evidence_refs': ['e01_01'],
         'known_at_start': ['ghost']})
    _obs, errors = compile_book_observation(wire, evidence=EVIDENCE)
    assert any('known_at_start' in e and 'ghost' in e for e in errors), errors
    wire = _book_wire_v9({'statement': '命题甲', 'evidence_refs': ['e09_99']})
    _obs, errors = compile_book_observation(wire, evidence=EVIDENCE)
    assert any('e09_99' in e for e in errors), errors
    # D-5 反例：reader 也不是词表持有者（Phase 3 才由 compile 派生读者知情）。
    wire = _book_wire_v9(
        {'statement': '命题甲', 'evidence_refs': ['e01_01'],
         'known_at_start': ['reader']})
    _obs, errors = compile_book_observation(wire, evidence=EVIDENCE)
    assert any('reader' in e for e in errors), errors


def test_fact_wire_model_is_id_free_and_exactly_shaped():
    """FactWire（模型输出面）无编号位、无 address 位：编造编号即形状失败。"""
    good = FactWire.model_validate(
        {'statement': '甲', 'evidence_refs': ['e01_01'], 'known_at_start': ()})
    assert good.statement == '甲'
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        FactWire.model_validate(
            {'statement': '甲', 'evidence_refs': ['e01_01'],
             'known_at_start': (), 'fact_id': 'f01'}), '编号不进 wire（I-21）'
    with pytest.raises(ValidationError):
        FactWire.model_validate(
            {'statement': '甲', 'evidence_refs': ['e01_01'],
             'known_at_start': (), 'address': 'world'}), 'D-3：无 address 字段'


def _book_v6(characters: dict, facts: list) -> dict:
    """canonical v6 直通面：facts 带 F 编号与 basis（引文＝段全文前 6 字）。"""
    return {**sample_book_expression(characters),
            'protocol_version': 'stage2-book-v7',
            'characters': characters, 'initial_state': [], 'world_constraints': [],
            'facts': facts}


def _chapter_wire_v9(know: list[dict]) -> dict:
    """章瘦 wire v9（模型输出面）：fact 与 what 恰写其一。"""
    return {'protocol_version': 'stage2-chapters-v10', 'beats': [
        {'event': '云娘起疑', 'on_stage': ['yun_niang'], 'start': 'e01_01',
         'change': [], 'know': know, 'loops': []}]}


def test_slim_know_fact_xor_what_and_closed_set():
    """wire v9：fact 与 what 恰写其一；f{nn} 引用闭集核对（词表成员资格）。"""
    wire = _chapter_wire_v9([{'who': 'yun_niang', 'how': 'suspects',
                              'fact': 'f01', 'evidence_refs': ['e01_01']}])
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
             'what': '夫君藏身床底', 'evidence_refs': ['e01_01']}]))
    with pytest.raises(ValidationError):
        expand_chapter_wire(_chapter_wire_v9([
            {'who': 'yun_niang', 'how': 'suspects',
             'evidence_refs': ['e01_01']}]))
    # 闭集：词表外的 f 编号即拒（fail fast——fact_ids 缺省同样拒）。
    _obs, errors = compile_chapter_observation(
        _chapter_wire_v9([{'who': 'yun_niang', 'how': 'suspects',
                           'fact': 'f99', 'evidence_refs': ['e01_01']}]),
        evidence_ids=EVIDENCE, chapter=1,
        character_slugs=('fu_jun', 'yun_niang'), fact_ids=frozenset({'f01'}))
    assert any('词表之外' in e and 'f99' in e for e in errors), errors
    _obs, errors = compile_chapter_observation(
        _chapter_wire_v9([{'who': 'yun_niang', 'how': 'suspects',
                           'fact': 'f01', 'evidence_refs': ['e01_01']}]),
        evidence_ids=EVIDENCE, chapter=1, character_slugs=('fu_jun', 'yun_niang'))
    assert any('词表之外' in e for e in errors), '未提供词表闭集即 fail fast'


def test_compile_resolves_fact_refs_and_merges_near_synonym_via_verdict():
    """compile：fact 引用直接解析（正式 ID＝fact 编号、文本＝词表文本）；
    near-synonym 转写经 verdict=same 并入 fact 类——fact 侧为 canonical。"""
    from contracts.schema.observation import AssociateObservation
    characters = {
        'fu_jun': {'canonical': '夫君', 'surfaces': [
            {'surface': '夫君', 'source_refs': ['e01_01']}]},
        'yun_niang': {'canonical': '云娘', 'surfaces': [
            {'surface': '云娘', 'source_refs': ['e01_02']}]}}
    book = _book_v6(characters, [{
        'fact_id': 'f01', 'statement': '夫君藏身床底',
        'basis': [{'segment': 'e01_01', 'quote': EVIDENCE['e01_01']['text'][:6]}],
        'known_at_start': ['yun_niang']}])
    ch01 = {'protocol_version': CPV, 'beats': [
        _beat('云娘起疑', ('e01_01',), participants=('yun_niang',),
              cognition=[{'proposition': {'ref': 'f01', 'statement': None},
                          'holder': 'yun_niang', 'mode': 'suspects',
                          'basis': _basis('e01_01')}])]}
    ch02 = {'protocol_version': CPV, 'beats': [
        _beat('词表外转写', ('e02_01',), participants=('yun_niang',),
              cognition=[{'proposition': {'ref': 'p_drift', 'statement': '夫君藏身于床底'},
                          'holder': 'yun_niang', 'mode': 'confirms',
                          'basis': _basis('e02_01')}])]}
    ch03 = {'protocol_version': CPV, 'beats': [
        _beat('揭示', ('e03_01',), participants=('yun_niang', 'fu_jun'),
              access='narrated',
              cognition=[{'proposition': {'ref': 'f01', 'statement': None},
                          'holder': 'yun_niang', 'mode': 'confirms',
                          'basis': _basis('e03_01')}])]}
    book_obs, chapters = _observations(book, {1: ch01, 2: ch02, 3: ch03})
    associate = AssociateObservation.model_validate({
        'protocol_version': 'stage2-associate-v5',
        'same_proposition': [{'left': 'ch02.p_drift', 'right': 'f01',
                              'verdict': 'same'}],
        'loop_pairing': [], 'cross_chapter_cause': [],
        'scene_continuation': []})
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
    assert timeline['known_at_start'] == ['yun_niang']
    assert [event['holder'] for event in timeline['events']] == ['reader', 'yun_niang']
    assert any('初始已知者 yun_niang 又被拍' in line and 'confirms' in line
               for line in outcome.fold['events']), outcome.fold['events']


def test_compile_rejects_public_holder_after_d5():
    """D-5：public 不在认知持有者闭集——compile 防御按条目拒（Stage4 的显式
    豁免分支对新根不可达，观察面先拒）。"""
    from contracts.schema.observation import AssociateObservation
    characters = {
        'fu_jun': {'canonical': '夫君', 'surfaces': [
            {'surface': '夫君', 'source_refs': ['e01_01']}]},
        'yun_niang': {'canonical': '云娘', 'surfaces': [
            {'surface': '云娘', 'source_refs': ['e01_02']}]}}
    book = _book_v6(characters, [])
    ch01 = {'protocol_version': CPV, 'beats': [
        _beat('公开知情', ('e01_01',), participants=('fu_jun',),
              cognition=[{'proposition': {'ref': 'p_pub', 'statement': '满城皆知'},
                          'holder': 'public', 'mode': 'learns',
                          'basis': _basis('e01_01')}])]}
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
    from contracts.schema.source import CharactersCore
    core = CharactersCore(characters={
        'fu_jun': {'canonical': '夫君', 'surfaces': [
            {'surface': '夫君', 'from_chapter': 1, 'until_chapter': None,
             'source_refs': ['e01_01']}]}})
    empty = source_fold(core, _blueprint_stub(), facts=())
    assert empty.knowledge_timeline == {}
    # 直接用契约模型构造最小 fact（不经过 book 验收——fold 只读）。
    from contracts.schema.observation import Fact, EvidenceQuote
    fact = Fact(fact_id='f01', statement='甲',
                basis=(EvidenceQuote(segment='e01_01', quote='甲'),),
                known_at_start=('fu_jun',))
    with_facts = source_fold(core, _blueprint_stub(), facts=(fact,))
    assert with_facts.knowledge_timeline == {
        'f01': {'statement': '甲', 'known_at_start': ['fu_jun'], 'events': []}}


def _blueprint_stub():
    """最小蓝图（source_fold 只读消费；一拍一场景）。"""
    from contracts.schema.source import (
        Beat, BlueprintsArtifactV17, ChapterBlueprint, SceneSpan,
    )
    beat = Beat(beat_id='b01_01', event='事件', participants=('fu_jun',),
                source_refs=('e01_01',), scene_boundary='new', temporal_mode='single')
    from contracts.schema.observation import BookObservation
    from novel.stages.stage2.expression import compile_source_expression
    book_obs = BookObservation.model_validate(_book_v6({
        'fu_jun': {'canonical': '夫君', 'surfaces': [
            {'surface': '夫君', 'source_refs': ['e01_01']}]}}, []))
    return BlueprintsArtifactV17(
        source_expression=compile_source_expression(book_obs.expression, texts=None),
        chapters=(ChapterBlueprint(chapter=1, beats=(beat,),
                                   scenes=(SceneSpan(scene_id='sc01',
                                                     beat_ids=('b01_01',)),)),),
        evidence={'e01_01': EVIDENCE['e01_01']['text']})
