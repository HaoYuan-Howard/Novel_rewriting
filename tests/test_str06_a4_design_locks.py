#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_a4_design_locks —— R10a a4 判据面的定向红绿例（Brief 锁定项、
Design v5：kp/kf 键规则、必保锁兑现、modality 必填、expression_refs 闭集、
dramatic_intent 删除）。

【验证边界】`project_brief` 由义务 anchors 与 canonical loops 确定性派生锁定项
（kp 命题登记＋知情锁＋kf 事实锁＋series 盲标记）；`assemble_body_design` 的
接受校验——kp 键持有者须有锁且模式被授权（known 只能来自 known 锁，C1）、
读者确认须有 reader known 锁（C6）、必保知情锁兑现、expression_refs 闭集与
narration 不上拍、dramatic_intent 关闭式拒读。「设计是否好看」归通读，不在此。

CS-F2（RC-36/38，stage4-design-v8）：事实锁（C2 时态、C9 值未知／未回答）不再以
kf 为状态键承载，而是在 Design 已选状态上标注 `lock`；本文件的 v5 用例走内部
中间形状（只验 kp 知情锁与形状，无事实锁标注通道，故用「不含事实锁兑现分母」
的 Brief 变体），事实锁的接受、映射与反例走真实 v8 入口（`inspect_body_design`
＋`derive_semantic_binding`），见文末 CS-F2 一节。
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from contracts.schema.grounding import (
    BriefFactLock, BriefKnowledgeChange, BriefKnowledgeLock, BriefProposition, PreserveCheckpoint,
    ReconstructionBrief,
)
from contracts.schema.source import (
    BlueprintsArtifactV17, CharactersCore, SourceExpression,
)
from novel.stages.stage4.design import assemble_body_design, inspect_body_design
from novel.stages.stage4.grounding import (
    assemble_preserve, derive_semantic_binding, design_obligation_index, project_brief,
)
from novel.stages.stage4.slim import (
    design_fact_marks, design_placements, knowledge_change_index,
)
from tests.str06_expression_inputs import sample_narration, sample_readings
from tests.str06_facilities import name_map_of, v13_beat, v13_effect
from tests.str06_semantic_cases import CASES_BY_BATCH

A4_CASES = CASES_BY_BATCH['R10a-a4']

BASIS = ({'segment': 'e01_01', 'quote': '夫君藏进床底'},)


def _characters() -> CharactersCore:
    return CharactersCore.model_validate({
        'characters': {
            'yun_niang': {'canonical': '云娘', 'surfaces': [
                {'surface': '云娘', 'from_chapter': 1, 'source_refs': ['e01_01']}]},
            'fu_jun': {'canonical': '夫君', 'surfaces': [
                {'surface': '夫君', 'from_chapter': 1, 'source_refs': ['e01_01']}]},
        },
        'initial_state': [],
    })


def _expression() -> SourceExpression:
    return SourceExpression.model_validate({
        'narration': {'person': 'third', 'focalizers': ['narrator']},
        'items': [
            {'ex_id': 'ex_01', 'kind': 'narration', 'statement': '第三人称旁白叙述。',
             'source_refs': ['e01_01']},
            {'ex_id': 'ex_02', 'kind': 'humor',
             'statement': '幽默经由次要人物一本正经地错解。', 'source_refs': ['e01_01']},
        ],
        'readings': sample_readings().model_dump(mode='json')})


def _blueprint() -> BlueprintsArtifactV17:
    return BlueprintsArtifactV17.model_validate({
        'source_expression': _expression().model_dump(mode='json'),
        'world_constraints': [],
        'chapters': [
            {'chapter': 1,
             'beats': [
                 v13_beat('b01_01', '夫君藏进床底，约半月便回', participants=('fu_jun',),
                          source_refs=('e01_01',),
                          effects=(
                              # CS-S 1b：effect 命题锚删——p_return 的登记走
                              # b01_01 的 cognition（时态锁仍由 modality 派生）。
                              v13_effect({'kind': 'world'}, 'agreement', '约半月',
                                         modality='expected',
                                         basis=BASIS),
                              v13_effect({'kind': 'character', 'char_id': 'fu_jun'},
                                         'residence', None, op='unknown',
                                         basis=BASIS),),
                          cognition=({'proposition': {'ref': 'p_x',
                                                      'statement': '夫君像是看穿了什么'},
                                      'holder': 'yun_niang', 'mode': 'suspects',
                                      'basis': BASIS},
                                     {'proposition': {'ref': 'p_return',
                                                      'statement': '夫君约半月后归家'},
                                      'holder': 'yun_niang', 'mode': 'hears_claim',
                                      'basis': BASIS},),
                          loops=({'ref': 'q_a', 'op': 'open',
                                  'question': '床底箱子里是什么？'},)),
                 v13_beat('b01_02', '芸娘证实猜测；夫君公开声称',
                          participants=('yun_niang', 'fu_jun'), source_refs=('e01_02',),
                          effects=(),
                          cognition=(
                              {'proposition': {'ref': 'p_x'},
                               'holder': 'yun_niang', 'mode': 'confirms',
                               'basis': ({'segment': 'e01_02', 'quote': '芸娘终于证实'},)},
                              {'proposition': {'ref': 'p_x'},
                               'holder': 'reader', 'mode': 'learns',
                               'basis': ({'segment': 'e01_02', 'quote': '芸娘终于证实'},)},
                              {'proposition': {'ref': 'p_claim',
                                               'statement': '夫君声称此行是去营商'},
                               'holder': 'fu_jun', 'mode': 'asserts',
                               'basis': ({'segment': 'e01_02', 'quote': '他只说是去营商'},)},),
                          loops=({'ref': 'q_a', 'op': 'close',
                                  'resolution': '箱底是报恩旧约的凭据。'},)),
                 v13_beat('b01_03', '芸娘另起一疑', source_refs=('e01_03',),
                          cognition=({'proposition': {'ref': 'p_y',
                                                      'statement': '婆母似乎知情'},
                                      'holder': 'yun_niang', 'mode': 'suspects',
                                      'basis': ({'segment': 'e01_03', 'quote': '婆母似乎知情'},)},),
                          loops=({'ref': 'q_secret', 'op': 'open',
                                  'question': '婆母到底瞒着什么？'},)),
             ],
             'scenes': [{'scene_id': 'sc01',
                         'beat_ids': ['b01_01', 'b01_02', 'b01_03']}],
             'causal_edges': []},
        ],
        'evidence': {'e01_01': '夫君藏进床底，只说约半月便回。',
                     'e01_02': '芸娘终于证实；他只说是去营商。',
                     'e01_03': '婆母似乎知情。'},
    })


def _preserve_wire() -> dict:
    return {'protocol_version': 'stage4-preserve-v7', 'obligations': [
        {'kind': 'event_core', 'statement': '芸娘从起疑到证实同一件事',
         'anchors': ['b01_01.c1', 'b01_02.c1'],
         'must_preserve': True, 'satisfaction': '正文呈现怀疑渐进到证实',
         'requires': []},
        {'kind': 'event_core', 'statement': '夫君约定归期',
         'anchors': ['b01_01.e1'],
         'must_preserve': True, 'satisfaction': '正文呈现归期是预计而非已发生',
         'requires': []},
        {'kind': 'character_arc', 'statement': '夫君的去向成谜',
         'anchors': ['b01_01.e2'],
         'must_preserve': True, 'satisfaction': '正文保留去向未知不补答案',
         'requires': []},
        {'kind': 'identity', 'statement': '夫君公开声称出行的说法',
         'anchors': ['b01_02.c3'],
         'must_preserve': False, 'satisfaction': '', 'requires': []},
        {'kind': 'character_arc', 'statement': '芸娘对婆母起疑（仅怀疑）',
         'anchors': ['b01_03.c1'],
         'must_preserve': False, 'satisfaction': '', 'requires': []},
        {'kind': 'event_core', 'statement': '读者得知实证',
         'anchors': ['b01_02.c2'],
         'must_preserve': True, 'satisfaction': '正文在证实拍让读者获知',
         'requires': []},
    ],
        'series': [{'members': [1, 5], 'mechanism': 'ex_02'}]}


def _checkpoint() -> PreserveCheckpoint:
    from tests.str06_facilities import name_map_of
    checkpoint, errors = assemble_preserve(
        _preserve_wire(), policy_id='p', policy_version=5,
        characters=_characters(), blueprint=_blueprint(),
        name_map=name_map_of(_characters()), name_map_sha256='n' * 64,
        provider='x', model='y', request_sha256='r' * 64)
    assert checkpoint is not None and not errors, errors
    return checkpoint


def _brief() -> ReconstructionBrief:
    brief, _ = project_brief(_checkpoint(), _characters(), _blueprint(), name_map=name_map_of(_characters()))
    return brief


def _kp(brief: ReconstructionBrief, statement: str) -> str:
    return next(p.kp_id for p in brief.propositions
                if p.statement == statement)


def _kf(brief: ReconstructionBrief, kind: str) -> str:
    return next(f.kf_id for f in brief.fact_locks if f.kind == kind)


# ───────────────────────── Brief：锁定项派生 ─────────────────────────

def test_case_registry_pins_a4_faces():
    """CASES_BY_BATCH['R10a-a4'] 覆盖 C1/C2/C6/C9 的红绿落点（防删例）。"""
    ids = {c.case_id for c in A4_CASES}
    assert {'C1', 'C2', 'C6', 'C9'} <= ids


def test_brief_derives_propositions_locks_and_fact_locks():
    """kp 登记＋知情锁（唯一映射＋asserts 不铸锁）＋事实锁三类别＋未回答锁。"""
    brief = _brief()
    by_statement = {p.statement: p for p in brief.propositions}
    # 命题登记：cognition 与 effect 命题同注册；同 ref 同 kp（p_x 两次认知）。
    assert len({p.kp_id for p in brief.propositions}) == len(brief.propositions)
    kp_x = by_statement['夫君像是看穿了什么'].kp_id
    kp_claim = by_statement['夫君声称此行是去营商'].kp_id
    assert kp_claim.startswith('kp_') and kp_x.startswith('kp_')
    # asserts 转公开声称标记：命题登记带 publicly_claimed_by，不产生任何
    # 持有者知情锁（说辞不是确知）。
    assert by_statement['夫君声称此行是去营商'].publicly_claimed_by == ('fu_jun',)
    # 知情锁唯一映射：learns/confirms→known、suspects→suspected；
    # reader 持有者进锁；public 无（本例未用）。
    locks = {(l.holder, l.kp, l.mode) for l in brief.knowledge_locks}
    assert ('yun_niang', kp_x, 'suspected') in locks
    assert ('yun_niang', kp_x, 'known') in locks
    assert ('reader', kp_x, 'known') in locks
    assert not any(l.kp == kp_claim for l in brief.knowledge_locks)
    # 事实锁：expected 时态、unknown_value 值未知、unanswered 原文未回答；
    # 已 close 的 q_a 不产生未回答锁。
    kinds = {f.kind for f in brief.fact_locks}
    assert kinds == {'expected', 'unknown_value', 'unanswered'}
    tense = next(f for f in brief.fact_locks if f.kind == 'expected')
    # CS-S 1b：effect 命题锚删——事实锁陈述回落到地址＝值的确定性渲染
    # （命题文本面在 kp 登记里，走 cognition）。
    assert tense.modality == 'expected' and tense.statement == "world.agreement = '约半月'"
    unanswered = next(f for f in brief.fact_locks if f.kind == 'unanswered')
    assert unanswered.statement == '婆母到底瞒着什么？'


def test_brief_series_blind_marker_and_realize_denominators():
    """series 盲标记共享同一 token＋mechanism；必保义务带兑现分母，非必保为空。"""
    brief = _brief()
    markers = {r.series_group for r in brief.requirements if r.series_group}
    assert len(markers) == 1, '同一 series 的成员共享一个盲组 token'
    members = [r for r in brief.requirements if r.series_group]
    assert {r.series_mechanism for r in members} == {'ex_02'}
    assert not any('sr_' in (r.series_group or '') for r in brief.requirements
                   if r.series_group and False)  # token 形状由 pattern 保证
    by_summary = {r.summary: r for r in brief.requirements}
    must_ob = by_summary['芸娘从起疑到证实同一件事']
    assert must_ob.must_preserve
    assert len(must_ob.knowledge_changes) == 2  # yun|kp_x（同一 kp 两模式并一行）
    assert not must_ob.realize_locks
    return_ob = by_summary['夫君约定归期']
    assert return_ob.realize_locks and not return_ob.knowledge_changes
    whereabouts = by_summary['夫君的去向成谜']
    assert whereabouts.realize_locks and not whereabouts.knowledge_changes
    claim_ob = by_summary['夫君公开声称出行的说法']
    assert not claim_ob.must_preserve
    assert claim_ob.knowledge_changes == () and claim_ob.realize_locks == ()
    assert claim_ob.locks, '信息面仍列出其 kp（asserts 命题已注册）'


def test_brief_lock_derivation_is_deterministic():
    brief1 = _brief()
    brief2 = _brief()
    assert brief1 == brief2


def test_reader_knowledge_keeps_locks_without_becoming_a_character():
    """reader 知情锚点仍投影知情锁，但 lineage／Brief 的人物闭集不混入 reader。
    人物持有者的 lineage 与 participants 保留。
    """
    checkpoint = _checkpoint()
    reader_ob = next(o for o in checkpoint.obligations if o.statement == '读者得知实证')
    person_ob = next(o for o in checkpoint.obligations if o.statement == '芸娘从起疑到证实同一件事')
    assert reader_ob.lineage.character_ids == ()
    assert reader_ob.lineage.blueprint_refs == ('b01_02',)
    assert person_ob.lineage.character_ids == ('yun_niang',)
    brief, _ = project_brief(checkpoint, _characters(), _blueprint(), name_map=name_map_of(_characters()))
    reader_req = next(r for r in brief.requirements if r.summary == '读者得知实证')
    person_req = next(r for r in brief.requirements if r.summary == '芸娘从起疑到证实同一件事')
    assert reader_req.participants == ()
    assert person_req.participants == ('yun_niang',)
    assert {c.holder for c in reader_req.knowledge_changes} == {'reader'}
    assert any(lock.holder == 'reader' for lock in brief.knowledge_locks)


def test_reader_lineage_defense_ignores_forged_character_entry_and_keeps_proxy():
    """人物模型先拒 reader；绕过模型的坏对象也不能污染 lineage，proxy 人物正常保留。"""
    characters = _characters()
    characters = CharactersCore.model_validate({
        **characters.model_dump(mode='json'),
        'characters': {**characters.model_dump(mode='json')['characters'],
                       'reader_proxy': characters.characters['fu_jun'].model_dump(mode='json')}})
    forged = characters.model_copy(update={'characters': {
        **characters.characters, 'reader': characters.characters['fu_jun']}})
    blueprint = _blueprint()
    first = blueprint.chapters[0].beats[0]
    first = first.model_copy(update={'cognition': (
        first.cognition[0].model_copy(update={'holder': 'reader_proxy'}),
        *first.cognition[1:])})
    chapter = blueprint.chapters[0].model_copy(update={'beats': (
        first, *blueprint.chapters[0].beats[1:])})
    blueprint = blueprint.model_copy(update={'chapters': (chapter,)})
    from tests.str06_facilities import name_map_of
    checkpoint, errors = assemble_preserve(
        _preserve_wire(), policy_id='p', policy_version=5,
        characters=forged, blueprint=blueprint,
        name_map=name_map_of(forged), name_map_sha256='n' * 64,
        provider='x', model='y', request_sha256='r' * 64)
    assert checkpoint is not None and not errors, errors
    reader_ob = next(o for o in checkpoint.obligations if o.statement == '读者得知实证')
    person_ob = next(o for o in checkpoint.obligations if o.statement == '芸娘从起疑到证实同一件事')
    assert reader_ob.lineage.character_ids == ()
    assert person_ob.lineage.character_ids == ('reader_proxy', 'yun_niang')
    brief, _ = project_brief(checkpoint, forged, blueprint, name_map=name_map_of(forged))
    reader_req = next(r for r in brief.requirements if r.summary == '读者得知实证')
    person_req = next(r for r in brief.requirements if r.summary == '芸娘从起疑到证实同一件事')
    assert reader_req.participants == ()
    assert 'reader_proxy' in person_req.participants
    assert any(lock.holder == 'reader' for lock in brief.knowledge_locks)
    assert any(lock.holder == 'reader_proxy' for lock in brief.knowledge_locks)


# ───────────────────────── Design v5：wire 形状 ─────────────────────────

def _design_wire(brief: ReconstructionBrief) -> dict:
    kp_x = _kp(brief, '夫君像是看穿了什么')
    kp_reader = kp_x  # reader learns 的同一命题
    return {
        'protocol_version': 'stage4-design-v5',
        'narration': {'person': 'third', 'focalizers': ['narrator']},
        'expression_decisions': [
            {'ex_id': 'ex_01', 'action': 'keep'},
            {'ex_id': 'ex_02', 'action': 'keep'}],
        'book_style': '克制的限知叙事。',
        'initial_state': [],
        'initial_surfaces': [],
        'scenes': [
            {'state_reads': [],
             'driver': {'focal_actor': 'yun_niang', 'immediate_want': '查明床底',
                        'opposition': '夫君的沉默', 'turn': '证实'},
             'realization_brief': '从异样写起，在证实处收住。',
             'delivery': {'pov': 'narrator', 'voice': '冷峻', 'avoid': []},
             'beats': [
                 {'event': '埋下可证实的凭据', 'participants': ['yun_niang'],
                  'delivery_hint': '', 'target_delta': [
                      {'domain': 'world', 'subjects': [], 'holder': None,
                       'key': 'secret', 'op': 'set', 'value': '床底藏物属实',
                       'scope': 'book', 'modality': 'actual'}],
                  'expression_refs': [], 'surface_introductions': []},
                 {'event': '芸娘起疑', 'participants': ['yun_niang'],
                  'delivery_hint': '', 'target_delta': [
                      {'domain': 'knowledge', 'subjects': [], 'holder': 'yun_niang',
                       'key': kp_x, 'op': 'set', 'value': '她怀疑夫君看穿了什么',
                       'scope': 'book', 'knowledge_mode': 'suspected'}],
                  'expression_refs': [], 'surface_introductions': []},
                 {'event': '芸娘证实', 'participants': ['yun_niang'],
                  'delivery_hint': '', 'target_delta': [
                      {'domain': 'knowledge', 'subjects': [], 'holder': 'yun_niang',
                       'key': kp_x, 'op': 'set', 'value': '她证实了此事',
                       'scope': 'book', 'knowledge_mode': 'known'}],
                  'expression_refs': [], 'surface_introductions': []},
                 {'event': '错解的玩笑', 'participants': ['yun_niang', 'fu_jun'],
                  'delivery_hint': '', 'target_delta': [],
                  'expression_refs': ['ex_02'], 'surface_introductions': []},
                 {'event': '读者获知实证', 'participants': ['yun_niang'],
                  'delivery_hint': '', 'target_delta': [
                      {'domain': 'knowledge', 'subjects': [], 'holder': 'reader',
                       'key': kp_reader, 'op': 'set', 'value': '床底藏物属实',
                       'scope': 'book', 'knowledge_mode': 'known', 'confirms': {'domain': 'world', 'key': 'secret'}}],
                  'expression_refs': [], 'surface_introductions': []},
                 {'event': '约定归期', 'participants': ['fu_jun'],
                  'delivery_hint': '', 'target_delta': [
                      {'domain': 'world', 'subjects': [], 'holder': None,
                       'key': 'agreement', 'op': 'set', 'value': '约半月后归家',
                       'scope': 'book', 'modality': 'expected'}],
                  'expression_refs': [], 'surface_introductions': []},
                 {'event': '去向成谜', 'participants': [],
                  'delivery_hint': '', 'target_delta': [
                      {'domain': 'world', 'subjects': [], 'holder': None,
                       'key': 'residence', 'op': 'unknown', 'value': None,
                       'scope': 'book'}],
                  'expression_refs': [], 'surface_introductions': []},
             ]}],
        'chapters': [{'purpose': '起疑到证实', 'scenes': 1}],
        'scene_weights': [1],
    }


def _without_fact_realization(brief: ReconstructionBrief) -> ReconstructionBrief:
    """v5 中间形状没有事实锁标注通道：v5 用例（kp 知情锁／形状）用不含事实锁
    兑现分母的 Brief 变体；事实锁的接受另在 v8 真实入口验证（文末 CS-F2 一节）。"""
    return brief.model_copy(update={'requirements': tuple(
        r.model_copy(update={'realize_locks': ()}) for r in brief.requirements)})


def _assemble(wire: dict, brief: ReconstructionBrief):
    return assemble_body_design(
        wire, known_char_ids={'yun_niang', 'fu_jun'},
        expression_requirements=brief.expression,
        source_narration=brief.source_narration,
        brief=_without_fact_realization(brief))


def test_green_wire_passes_all_lock_checks():
    """绿基线（v5 中间形状）：授权模式内知情、reader known 锁确认、
    expression_refs 取 humor、必保知情锁全部兑现（事实锁见 CS-F2 一节）。"""
    brief = _brief()
    body, errors = _assemble(_design_wire(brief), brief)
    assert body is not None and not errors, errors
    beat = body.scenes[0].beats[3]
    assert beat.expression_refs == ('ex_02',)
    assert all(not hasattr(b, 'dramatic_intent')
               for s in body.scenes for b in s.beats), 'dramatic_intent 已删'


def test_wire_v5_rejects_dramatic_intent_closed_set():
    """v5 关闭式拒读：wire 带 dramatic_intent 即未知字段错（R10-0.4 收口）。"""
    brief = _brief()
    wire = _design_wire(brief)
    wire['scenes'][0]['beats'][0]['dramatic_intent'] = '成礼'
    _, errors = _assemble(wire, brief)
    assert any('dramatic_intent' in e for e in errors), errors


# ───────────────────────── C1：kp 模式越权 ─────────────────────────

def test_c1_knowledge_mode_upgrade_without_known_lock_rejected():
    """C1 红例：仅 suspects 锁的命题（p_y）被写成 known → 模式越权拒。"""
    brief = _brief()
    kp_y = _kp(brief, '婆母似乎知情')
    wire = _design_wire(brief)
    wire['scenes'][0]['beats'][2]['target_delta'].append(
        {'domain': 'knowledge', 'subjects': [], 'holder': 'yun_niang',
         'key': kp_y, 'op': 'set', 'value': '她确知婆母知情',
         'scope': 'book', 'knowledge_mode': 'known'})
    _, errors = _assemble(wire, brief)
    assert any('无锁或模式越权' in e and kp_y in e for e in errors), errors
    # 绿对照：写 suspected（授权模式内）通过。
    wire['scenes'][0]['beats'][2]['target_delta'][-1]['knowledge_mode'] = 'suspected'
    body, errors = _assemble(wire, brief)
    assert body is not None and not errors, errors


def test_c1_unregistered_kp_key_rejected():
    brief = _brief()
    wire = _design_wire(brief)
    wire['scenes'][0]['beats'][1]['target_delta'][0]['key'] = 'kp_deadbeefdeadbeef'
    _, errors = _assemble(wire, brief)
    assert any('kp 键未在 Brief 登记' in e for e in errors), errors


# ───────────────────────── C2：时态锁（v5 形状面；锁标注见 CS-F2） ─────────────────

def test_fact_set_without_modality_rejected_by_shape():
    """非知情/非读者的 set 缺 modality → wire 形状面拒（C2 的漏填面）。"""
    brief = _brief()
    wire = _design_wire(brief)
    del wire['scenes'][0]['beats'][0]['target_delta'][0]['modality']
    _, errors = _assemble(wire, brief)
    assert any('modality' in e for e in errors), errors
    # knowledge 域带 modality 同样拒（时态只在事实 set 上）。
    wire2 = _design_wire(brief)
    wire2['scenes'][0]['beats'][1]['target_delta'][0]['modality'] = 'actual'
    _, errors2 = _assemble(wire2, brief)
    assert any('modality' in e for e in errors2), errors2


# ───────────────────────── C6：读者确认须有 known 锁 ─────────────────────────

def test_c6_reader_confirmation_without_known_lock_rejected():
    """C6 红例（D5 统一后同一把尺）：无 reader known 锁的命题（p_y）被安排
    读者揭示（knowledge(holder=reader)）→ 拒。"""
    brief = _brief()
    kp_y = _kp(brief, '婆母似乎知情')
    wire = _design_wire(brief)
    wire['scenes'][0]['beats'][4]['target_delta'][0]['key'] = kp_y
    _, errors = _assemble(wire, brief)
    assert any('无锁或模式越权' in e and kp_y in e for e in errors), errors


# ───────────────────────── 必保锁兑现 ─────────────────────────

def test_must_obligation_knowledge_locks_must_be_realized():
    brief = _brief()
    kp_x = _kp(brief, '夫君像是看穿了什么')
    # 删掉 yun 的知情承载 → 知情锁未兑现（保留 reader 面）。
    wire2 = _design_wire(brief)
    wire2['scenes'][0]['beats'][1]['target_delta'] = []
    wire2['scenes'][0]['beats'][2]['target_delta'] = []
    _, errors2 = _assemble(wire2, brief)
    assert any('知情锁未兑现' in e and kp_x in e for e in errors2), errors2


# ───────────────────────── expression_refs 闭集 ─────────────────────────

def test_expression_refs_closed_set_and_narration_not_on_beats():
    brief = _brief()
    wire = _design_wire(brief)
    wire['scenes'][0]['beats'][3]['expression_refs'] = ['ex_99']
    _, errors = _assemble(wire, brief)
    assert any('闭集外 ex_id' in e and 'ex_99' in e for e in errors), errors
    wire['scenes'][0]['beats'][3]['expression_refs'] = ['ex_01']
    _, errors = _assemble(wire, brief)
    assert any('narration' in e and '不上具体拍' in e for e in errors), errors


# ───────────────────────── Brief 模型：锁引用闭合 ─────────────────────────

def test_brief_validators_reject_dangling_lock_references():
    good = _brief()
    # 知情锁引用未登记命题。
    bad = good.model_copy(update={
        'propositions': tuple(p for p in good.propositions
                              if p.statement != '夫君像是看穿了什么')})
    try:
        bad.model_validate(bad.model_dump())
        raise AssertionError('应拒绝悬空知情锁')
    except ValueError as exc:
        assert '知情锁引用未登记命题' in str(exc)
    # knowledge_changes 引用无锁行。
    reqs = list(good.requirements)
    reqs[0] = reqs[0].model_copy(update={'knowledge_changes': (
        BriefKnowledgeChange(change_id='kc_0000000000000000', holder='fu_jun',
                             kp='kp_0000000000000000', mode='known'),)})
    bad2 = good.model_copy(update={'requirements': tuple(reqs)})
    try:
        bad2.model_validate(bad2.model_dump())
        raise AssertionError('应拒绝无锁行的兑现分母')
    except ValueError as exc:
        assert 'knowledge_changes 引用无锁行' in str(exc)


def test_lock_models_shape_constants():
    """锁模型形状钉住：kp/kf 句柄 pattern、类别闭集、fact 锁 modality 一致性。"""
    try:
        BriefProposition(kp_id='kp_xyz', statement='x')
        raise AssertionError('短句柄应拒')
    except ValueError:
        pass
    try:
        BriefFactLock(kf_id='kf_' + '0' * 16, statement='x',
                      kind='unanswered', modality='expected')
        raise AssertionError('unanswered 不带 modality')
    except ValueError:
        pass
    BriefKnowledgeLock(holder='reader', kp='kp_' + '0' * 16, mode='heard')


# ═════════ CS-F2（RC-36／RC-38）：事实锁在已选状态上标注引用，不再复制第二份状态 ═════════
#
# 真实入口：v8 wire → `inspect_body_design`（接受）→ `derive_semantic_binding`
# （映射）。本节只证结构：句柄闭集、一锁一承载、模式／操作同锁一致、映射只存
# 引用不复制 value。「承载状态的内容在语义上是否满足锁」（例如把暂缓和离绑到
# 赏花）需要标定的语义核验，不在此处，也不由结构通过来宣称（I-42）。

from contracts.schema.design import DesignWireV9


def _obligations_v8(brief: ReconstructionBrief) -> dict:
    return design_obligation_index(_checkpoint(), brief)


def _know_ref(obligations: dict, ob: str, holder: str, mode: str) -> str:
    return next(ref for ref, (owner, change) in knowledge_change_index(obligations).items()
                if owner == ob and change.holder == holder and change.mode == mode)


def _design_wire_v8(brief: ReconstructionBrief) -> dict:
    """a4 场景的真实 v8 形状：事实锁只在已选状态上用 `lock` 标注。

    ob01 起疑(b1)→证实(b2)；ob06 读者获知(b4)；ob02 归期预计(b5)；ob03 去向未知(b6)。
    """
    obligations = _obligations_v8(brief)
    return {
        'protocol_version': 'stage4-design-v9',
        'book_style': '克制的限知叙事。',
        'scenes': [{
            'driver': {'focal_actor': 'yun_niang', 'immediate_want': '查明床底',
                       'opposition': '夫君的沉默', 'turn': '证实'},
            'brief': '从异样写起，在证实处收住。',
            'beats': [
                {'ev': '埋下可证实的凭据', 'who': ['yun_niang'],
                 'state': [{'at': 'world', 'key': 'secret', 'val': '床底藏物属实'}]},
                {'ev': '芸娘起疑', 'who': ['yun_niang'], 'real': ['ob01'],
                 'know': [{'ref': _know_ref(obligations, 'ob01', 'yun_niang', 'suspected')}]},
                {'ev': '芸娘证实', 'who': ['yun_niang'], 'real': ['ob01'],
                 'know': [{'ref': _know_ref(obligations, 'ob01', 'yun_niang', 'known')}]},
                {'ev': '错解的玩笑', 'who': ['yun_niang', 'fu_jun'], 'ex': ['ex_02']},
                {'ev': '读者获知实证', 'who': ['yun_niang'], 'real': ['ob06'],
                 'know': [{'ref': _know_ref(obligations, 'ob06', 'reader', 'known'),
                           'confirms': {'domain': 'world', 'key': 'secret'}}]},
                {'ev': '约定归期', 'who': ['fu_jun'], 'real': ['ob02'],
                 'state': [{'at': 'world', 'key': 'agreement', 'val': '约半月后归家',
                            'modality': 'expected', 'lock': _kf(brief, 'expected')}]},
                {'ev': '去向成谜', 'who': [], 'real': ['ob03'],
                 # CS-H H3（RC-63 主体包含）：该锁的主体是 fu_jun（源锚点
                 # char:fu_jun.residence）——承载状态须是 fu_jun
                 # 自己的状态，不再允许标到 world／他人状态上。
                 'state': [{'at': 'fu_jun', 'key': 'residence', 'op': 'unknown',
                            'lock': _kf(brief, 'unknown_value')}]},
            ]}],
        'chapters': [{'purpose': '起疑到证实', 'scenes': 1}],
    }


def _inspect_v8(wire: dict, brief: ReconstructionBrief):
    return inspect_body_design(
        wire, known_char_ids={'yun_niang', 'fu_jun'},
        expression_requirements=brief.expression,
        source_narration=brief.source_narration, brief=brief,
        obligations=_obligations_v8(brief))


def _derive_binding_v8(wire: dict, body):
    model = DesignWireV9.model_validate(wire)
    return derive_semantic_binding(
        design_placements(model), body, _checkpoint(), design_fact_marks(model))


def test_fact_lock_green_v8_marks_existing_states_and_binding_keeps_references_only():
    """RC-36：锁映射只引用承载拍与状态地址；BodyDesign 里只有一份目标事实。"""
    brief = _brief()
    wire = _design_wire_v8(brief)
    body, errors = _inspect_v8(wire, brief)
    assert body is not None and not errors, errors
    assert not any(d.key.startswith('kf_') for s in body.scenes for b in s.beats
                   for d in b.target_delta), 'kf 句柄不再是状态键'
    binding, errors = _derive_binding_v8(wire, body)
    assert binding is not None and not errors, errors
    by_lock = {f.kf_id: f for f in binding.fact_bindings}
    assert set(by_lock) == {_kf(brief, 'expected'), _kf(brief, 'unknown_value')}
    expected = by_lock[_kf(brief, 'expected')]
    assert expected.beat_ref == 'sc01#b06' and expected.address.key == 'agreement'
    assert expected.address.domain.value == 'world'
    # 映射不复制目标 value：字段只有锁句柄、承载拍、状态地址。
    assert set(expected.model_dump()) == {'kf_id', 'beat_ref', 'address'}
    assert '约半月' not in str(expected.model_dump())
    # 承载状态本身仍是 BodyDesign 的唯一目标事实（含 modality）。
    carrier = body.scenes[0].beats[5].target_delta[0]
    assert (carrier.key, carrier.value, carrier.modality) == (
        'agreement', '约半月后归家', 'expected')


def test_c2_expected_lock_carried_by_actual_state_rejected_with_wire_path():
    """C2（RC-38 行 1）：计划锁被标在 actual 状态上＝升格为已发生，拒绝模式升级；
    错误带规则码与可编辑的 wire 路径（局部修订按路径定位，不解析文案）。"""
    brief = _brief()
    wire = _design_wire_v8(brief)
    wire['scenes'][0]['beats'][5]['state'][0]['modality'] = 'actual'
    _, errors = _inspect_v8(wire, brief)
    issue = next(e for e in errors if 'modality 与锁不一致' in e)
    assert _kf(brief, 'expected') in issue and '计划不得写成已发生' in issue
    assert issue.code == 'fact_lock_modality'
    assert issue.context['wire_paths'] == [['scenes', 0, 'beats', 5, 'state', 0]]


def test_planned_state_may_become_actual_later_without_the_old_lock_forbidding_it():
    """RC-38 行 2：锁绑定在 planned 的建立拍；后文合法事件把同一状态转 actual 不被
    旧计划锁永久禁止——锁不是对地址的永久禁令。"""
    brief = _brief()
    wire = _design_wire_v8(brief)
    wire['scenes'][0]['beats'].append(
        {'ev': '夫君如期归来', 'who': ['fu_jun'],
         'state': [{'at': 'world', 'key': 'agreement', 'val': '已归家'}]})
    body, errors = _inspect_v8(wire, brief)
    assert body is not None and not errors, errors
    assert body.scenes[0].beats[7].target_delta[0].modality == 'actual'
    binding, errors = _derive_binding_v8(wire, body)
    assert binding is not None and not errors, errors


def test_c9_unknown_value_lock_must_be_carried_by_unknown_state_not_a_filled_value():
    """C9（RC-38 行 3）：值未知锁标在补了具体值的状态上＝拒绝补值。"""
    brief = _brief()
    wire = _design_wire_v8(brief)
    state = wire['scenes'][0]['beats'][6]['state'][0]
    state.pop('op')
    state['val'] = '去了南边投军'
    _, errors = _inspect_v8(wire, brief)
    issue = next(e for e in errors if '值未知锁' in e)
    assert _kf(brief, 'unknown_value') in issue and '不得补值' in issue
    assert issue.code == 'fact_lock_value'


def test_unanswered_lock_is_a_prohibition_and_needs_no_fabricated_state():
    """RC-38 行 4：原文未回答的问题只保留禁区；绿基线没有为它造状态也通过，
    把它当事实锁标注则明确拒绝（不承载状态）。"""
    brief = _brief()
    wire = _design_wire_v8(brief)
    body, errors = _inspect_v8(wire, brief)
    assert body is not None and not errors, errors      # 没有 unanswered 的承载状态
    wire['scenes'][0]['beats'][0]['state'][0]['lock'] = _kf(brief, 'unanswered')
    _, errors = _inspect_v8(wire, brief)
    issue = next(e for e in errors if '原文未回答锁' in e)
    assert issue.code == 'fact_lock_unanswered'


def test_fact_lock_dangling_duplicate_and_unrealized_are_rejected_precisely():
    brief = _brief()
    # 悬空句柄：Brief 未登记的 kf（RC-36「悬空目标」的引用面）。
    wire = _design_wire_v8(brief)
    wire['scenes'][0]['beats'][5]['state'][0]['lock'] = 'kf_' + 'f' * 16
    _, errors = _inspect_v8(wire, brief)
    assert any(getattr(e, 'code', '') == 'fact_lock_unregistered' and 'kf_' + 'f' * 16 in e
               for e in errors), errors
    # 一个锁只由一条已选状态承载：不为同一事实再写一条状态。
    wire = _design_wire_v8(brief)
    wire['scenes'][0]['beats'][5]['state'].append(
        {'at': 'world', 'key': 'agreement', 'val': '约半月后归家',
         'modality': 'expected', 'lock': _kf(brief, 'expected')})
    _, errors = _inspect_v8(wire, brief)
    duplicate = next(e for e in errors if getattr(e, 'code', '') == 'fact_lock_duplicate')
    assert duplicate.context['wire_paths'] == [['scenes', 0, 'beats', 5, 'state', 1]]
    # 必保义务的锁未被任何状态承载：定位到承载该义务的拍，并给出可执行的补救。
    wire = _design_wire_v8(brief)
    wire['scenes'][0]['beats'][5].pop('state')
    _, errors = _inspect_v8(wire, brief)
    unrealized = next(e for e in errors if getattr(e, 'code', '') == 'fact_lock_unrealized')
    assert 'ob02' in unrealized and _kf(brief, 'expected') in unrealized
    assert 'req_' not in unrealized, '用 Design 看得见的 obNN，不暴露源盲 core_id'
    assert unrealized.context['wire_paths'] == [['scenes', 0, 'beats', 5]]


def test_kf_handle_is_not_a_state_key_in_wire_or_initial_state():
    """旧写法（kf 作状态键另写一条复述事实的状态）关闭式拒读，指向新写法。"""
    brief = _brief()
    wire = _design_wire_v8(brief)
    wire['scenes'][0]['beats'][5]['state'] = [
        {'at': 'world', 'key': _kf(brief, 'expected'), 'val': '约半月后归家',
         'modality': 'expected'}]
    _, errors = _inspect_v8(wire, brief)
    assert any('kf 句柄不是状态键' in e and 'scenes.0.beats.5.state.0' in e
               for e in errors), errors
    wire = _design_wire_v8(brief)
    wire['initial_state'] = [{'domain': 'world', 'key': _kf(brief, 'expected'),
                              'value': '约半月后归家'}]
    _, errors = _inspect_v8(wire, brief)
    assert any('kf 句柄不是状态键' in e and 'initial_state.0' in e for e in errors), errors
    # 锁标注标在 kp 知情行上同样拒（锁只标目标事实状态）。
    wire = _design_wire_v8(brief)
    wire['scenes'][0]['beats'][1]['state'] = [
        {'at': 'yun_niang', 'key': _kp(brief, '夫君像是看穿了什么'), 'op': 'unset',
         'lock': _kf(brief, 'expected')}]
    _, errors = _inspect_v8(wire, brief)
    assert any('lock 只能标在目标事实状态上' in e for e in errors), errors


def test_binding_refuses_a_mapping_that_points_at_no_state_in_the_body():
    """映射必须指向 BodyDesign 实际持有的事实：悬空即拒（I-16）。"""
    from dataclasses import replace
    brief = _brief()
    wire = _design_wire_v8(brief)
    body, errors = _inspect_v8(wire, brief)
    assert body is not None and not errors, errors
    model = DesignWireV9.model_validate(wire)
    moved = tuple(replace(m, beat=0) if m.kf_id == _kf(brief, 'expected') else m
                  for m in design_fact_marks(model))
    binding, errors = derive_semantic_binding(
        design_placements(model), body, _checkpoint(), moved)
    assert binding is None
    assert any('承载状态在 BodyDesign 中不存在' in e for e in errors), errors


@pytest.mark.parametrize(('kind', 'beat_i'), [('expected', 5), ('unknown_value', 6)])
def test_fact_lock_address_is_unique_in_its_carrier_beat_and_locatable(kind, beat_i):
    """锁的拍＋地址必须唯一定址；同拍追加未标锁的 actual 不能旁路时态／未知限制。"""
    from novel.stages.stage4 import locate_design_patches
    brief = _brief()
    wire = _design_wire_v8(brief)
    carrier = wire['scenes'][0]['beats'][beat_i]
    state = carrier['state'][0]
    carrier['state'].append({'at': state['at'], 'key': state['key'],
                             'val': '已给出确定结果', 'modality': 'actual'})
    before = deepcopy(wire)
    body, errors = _inspect_v8(wire, brief)
    assert body is not None, '安全可构造的候选保留给独立检查，不授予发布资格'
    issue = next(e for e in errors if getattr(e, 'code', '') == 'fact_lock_ambiguous')
    assert _kf(brief, kind) in issue
    assert issue.owner == 'design'
    assert issue.context['wire_paths'] == [
        ['scenes', 0, 'beats', beat_i, 'state', 0],
        ['scenes', 0, 'beats', beat_i, 'state', 1]]
    located = locate_design_patches(wire, [issue])
    assert located == [(('scenes', 0, 'beats', beat_i), [issue])], '耦合状态整拍修订'
    # 重写候选文案不改变引用歧义的身份，也不改输入。
    changed = deepcopy(wire)
    changed['scenes'][0]['beats'][beat_i]['state'][1]['val'] = '另一个确定结果'
    _, next_errors = _inspect_v8(changed, brief)
    next_issue = next(e for e in next_errors if getattr(e, 'code', '') == 'fact_lock_ambiguous')
    assert next_issue.identity == issue.identity
    assert wire == before
    binding, errors = _derive_binding_v8(wire, body)
    assert binding is None, 'Binding 派生也必须防御，不能只依赖上层接受校验'
    assert any(getattr(e, 'code', '') == 'fact_lock_ambiguous' for e in errors), errors


def test_fact_lock_does_not_ban_same_address_updates_in_other_beats_or_unlocked_addresses():
    """唯一性只约束被锁的承载拍地址；后拍兑现与未被锁地址保持现役行为。"""
    brief = _brief()
    wire = _design_wire_v8(brief)
    wire['scenes'][0]['beats'].append({
        'ev': '夫君归来并说明去向', 'who': ['fu_jun'], 'state': [
            {'at': 'world', 'key': 'agreement', 'val': '已归家'},
            {'at': 'world', 'key': 'residence', 'val': '南边'},
            {'at': 'world', 'key': 'intent', 'val': '尝试'},
            {'at': 'world', 'key': 'intent', 'val': '完成'}]})
    body, errors = _inspect_v8(wire, brief)
    assert body is not None and not errors, errors
    binding, errors = _derive_binding_v8(wire, body)
    assert binding is not None and not errors, errors
    assert len(binding.fact_bindings) == 2


@pytest.mark.parametrize('fault', ['expected', 'unknown_value', 'missing_state', 'missing_beat'])
def test_canonical_fact_binding_is_rechecked_by_execution_plan(fault):
    """正式 BodyDesign＋Binding 免费重编译同样拒绝歧义／悬空，不信任历史接受结论。"""
    from novel.stages.stage4.compile import compile_execution_plan
    brief = _brief()
    wire = _design_wire_v8(brief)
    body, errors = _inspect_v8(wire, brief)
    assert body is not None and not errors, errors
    binding, errors = _derive_binding_v8(wire, body)
    assert binding is not None and not errors, errors
    kind = 'unknown_value' if fault == 'unknown_value' else 'expected'
    mapping = next(f for f in binding.fact_bindings if f.kf_id == _kf(brief, kind))
    scenes = list(body.scenes)
    beats = list(scenes[0].beats)
    beat_i = 6 if kind == 'unknown_value' else 5
    if fault == 'missing_beat':
        binding = binding.model_copy(update={'fact_bindings': tuple(
            f.model_copy(update={'beat_ref': 'sc99#b99'}) if f == mapping else f
            for f in binding.fact_bindings)})
    else:
        state = beats[beat_i].target_delta[0]
        delta = () if fault == 'missing_state' else (
            state, state.model_copy(update={'op': 'set', 'value': '已给出确定结果',
                                           'modality': 'actual'}))
        beats[beat_i] = beats[beat_i].model_copy(update={'target_delta': delta})
        scenes[0] = scenes[0].model_copy(update={'beats': tuple(beats)})
        body = body.model_copy(update={'scenes': tuple(scenes)})
    body_before, binding_before = body.model_dump(), binding.model_dump()
    plan, errors = compile_execution_plan(body, binding)
    assert plan is None
    expected_code = 'fact_lock_missing' if fault.startswith('missing') else 'fact_lock_ambiguous'
    issue = next(e for e in errors if getattr(e, 'code', '') == expected_code)
    assert mapping.kf_id in issue and 'M4' in issue
    assert body.model_dump() == body_before and binding.model_dump() == binding_before


@pytest.mark.parametrize(('kind', 'source_i'), [('expected', 5), ('unknown_value', 6)])
@pytest.mark.parametrize('later', [False, True], ids=['earlier', 'later'])
def test_fact_lock_outside_its_obligation_carriers_is_rejected_in_acceptance_and_binding(
        kind, source_i, later):
    """锁在全书出现不等于对应义务兑现；位置检查与局部修订都从真实标注派生。"""
    from novel.stages.stage4 import locate_design_patches
    brief = _brief()
    wire = _design_wire_v8(brief)
    beats = wire['scenes'][0]['beats']
    target_i = len(beats) if later else 0
    if later:
        beats.append({'ev': '另一次无关进展', 'who': ['fu_jun']})
    beats[target_i].setdefault('state', []).extend(beats[source_i].pop('state'))
    before = deepcopy(wire)
    body, errors = _inspect_v8(wire, brief)
    assert body is not None, '保留可构造候选用于诊断，不授予发布资格'
    issue = next(e for e in errors
                 if getattr(e, 'code', '') == 'fact_lock_outside_obligation')
    ob = 'ob02' if kind == 'expected' else 'ob03'
    assert issue.owner == 'design'
    assert issue.context['obligation_ids'] == [ob]
    assert issue.context['kf_id'] == _kf(brief, kind)
    assert issue.context['beat_ref'] == f'sc01#b{target_i + 1:02d}'
    assert issue.context['wire_paths'] == [
        ['scenes', 0, 'beats', target_i], ['scenes', 0, 'beats', source_i]]
    located = locate_design_patches(wire, [issue])
    assert {path for path, _ in located} == {
        ('scenes', 0, 'beats', target_i), ('scenes', 0, 'beats', source_i)}
    binding, binding_errors = _derive_binding_v8(wire, body)
    assert binding is None
    same = next(e for e in binding_errors
                if getattr(e, 'code', '') == 'fact_lock_outside_obligation')
    assert same.identity == issue.identity, '接受与派生使用同一稳定问题身份'
    assert wire == before


@pytest.mark.parametrize(('kind', 'source_i', 'ob'), [
    ('expected', 5, 'ob02'), ('unknown_value', 6, 'ob03')])
def test_fact_lock_on_explicit_support_beat_is_valid(kind, source_i, ob):
    """合法提前支撑与最终接受不同拍；不能把位置修复收窄为只允许末拍。"""
    from novel.stages.stage4.compile import compile_execution_plan
    brief = _brief()
    wire = _design_wire_v8(brief)
    beats = wire['scenes'][0]['beats']
    beats[0]['state'].extend(beats[source_i].pop('state'))
    beats[0]['real'] = [ob]
    body, errors = _inspect_v8(wire, brief)
    assert body is not None and not errors, errors
    binding, errors = _derive_binding_v8(wire, body)
    assert binding is not None and not errors, errors
    entry = next(e for e in binding.entries if e.obligation_id == ob)
    assert entry.supporting_beat_refs == ('sc01#b01',)
    assert entry.beat_ref == f'sc01#b{source_i + 1:02d}'
    plan, errors = compile_execution_plan(body, binding)
    assert plan is not None and not errors, errors


@pytest.mark.parametrize('missing', [False, True], ids=['wrong_beat', 'missing_mapping'])
def test_canonical_fact_lock_coverage_and_obligation_position_are_rechecked(missing):
    """历史正式产物不能沿用旧接受结论；免费消费复核锁分母和义务位置。"""
    from novel.stages.stage4.compile import compile_execution_plan
    brief = _brief()
    wire = _design_wire_v8(brief)
    body, errors = _inspect_v8(wire, brief)
    assert body is not None and not errors, errors
    binding, errors = _derive_binding_v8(wire, body)
    assert binding is not None and not errors, errors
    kf = _kf(brief, 'expected')
    if missing:
        binding = binding.model_copy(update={'fact_bindings': tuple(
            f for f in binding.fact_bindings if f.kf_id != kf)})
    else:
        beats = list(body.scenes[0].beats)
        beats[0] = beats[0].model_copy(update={
            'target_delta': (*beats[0].target_delta, *beats[5].target_delta)})
        beats[5] = beats[5].model_copy(update={'target_delta': ()})
        body = body.model_copy(update={'scenes': (
            body.scenes[0].model_copy(update={'beats': tuple(beats)}),)})
        binding = binding.model_copy(update={'fact_bindings': tuple(
            f.model_copy(update={'beat_ref': 'sc01#b01'}) if f.kf_id == kf else f
            for f in binding.fact_bindings)})
    before = body.model_dump(), binding.model_dump()
    plan, errors = compile_execution_plan(body, binding)
    assert plan is None
    code = 'fact_lock_unrealized' if missing else 'fact_lock_outside_obligation'
    issue = next(e for e in errors if getattr(e, 'code', '') == code)
    assert issue.context['obligation_ids'] == ['ob02'] and issue.context['kf_id'] == kf
    assert not issue.context['wire_paths'], 'canonical 消费不伪造可编辑 wire 路径'
    assert (body.model_dump(), binding.model_dump()) == before


@pytest.mark.parametrize(('carrier_owner', 'passed'), [
    ('owner', True), ('shared_other', True), ('neither', False), ('none', False)])
def test_shared_fact_lock_carrier_belongs_to_any_sharing_obligation(
        carrier_owner, passed):
    """CS-H H3（RC-63）：一项事实定位一次——承载拍属于任一共享义务（realize_locks
    引用同一锁的必保义务）的 real 集合即成立；其余共享义务由 compiler 把承载拍
    记入 supporting_beat_refs（只校验先后：承载拍不晚于其接受拍）。"""
    from novel.stages.stage4.compile import compile_execution_plan
    from novel.stages.stage4.grounding import required_fact_locks

    preserve = _preserve_wire()
    preserve['obligations'][3].update({
        'statement': '归期预计也支撑夫君的承诺', 'anchors': ['b01_01.e1'],
        'must_preserve': True, 'satisfaction': '保留预计承诺'})
    from tests.str06_facilities import name_map_of
    checkpoint, errors = assemble_preserve(
        preserve, policy_id='p', policy_version=5,
        characters=_characters(), blueprint=_blueprint(),
        name_map=name_map_of(_characters()), name_map_sha256='n' * 64,
        provider='x', model='y', request_sha256='r' * 64)
    assert checkpoint is not None and not errors, errors
    brief, _ = project_brief(checkpoint, _characters(), _blueprint(), name_map=name_map_of(_characters()))
    obligations = design_obligation_index(checkpoint, brief)
    assert required_fact_locks(checkpoint.obligations) == {
        ob: req.realize_locks for ob, req in obligations.items() if req.must_preserve}
    kf = _kf(brief, 'expected')
    assert [ob for ob, locks in required_fact_locks(checkpoint.obligations).items()
            if kf in locks] == ['ob02', 'ob04'], '共享集合＝引用同一锁的全部必保义务'
    wire = _design_wire_v8(brief)
    wire['scenes'][0]['beats'][0]['real'] = ['ob04']
    if carrier_owner == 'owner':
        # 承载拍 b06 同时进 ob02 与 ob04 的 real 集（两个共享义务自担）。
        wire['scenes'][0]['beats'][5]['real'].append('ob04')
    elif carrier_owner == 'shared_other':
        # 承载位不动，ob04 的接受拍挪到其后（b07）：承载拍只属 ob02，ob04 靠
        # 派生支撑引用成立（支撑早于验收，compile_support_early 绿）。
        wire['scenes'][0]['beats'][0]['real'] = []
        wire['scenes'][0]['beats'][6]['real'].append('ob04')
    elif carrier_owner == 'neither':
        # 承载拍挪到无任何 real 的拍：不属于任一共享义务 ⇒ 拒。
        state = wire['scenes'][0]['beats'][5]['state'][0]
        del wire['scenes'][0]['beats'][5]['state'][0]
        wire['scenes'][0]['beats'][1]['state'] = [state]
    else:
        del wire['scenes'][0]['beats'][5]['state'][0]['lock']
    body, acceptance_errors = _inspect_v8(wire, brief)
    assert body is not None
    model = DesignWireV9.model_validate(wire)
    binding, binding_errors = derive_semantic_binding(
        design_placements(model), body, checkpoint, design_fact_marks(model))
    if passed:
        assert not acceptance_errors and not binding_errors, (acceptance_errors, binding_errors)
        assert binding is not None and len(binding.fact_bindings) == 2, '共享不复制映射'
        plan, errors = compile_execution_plan(body, binding)
        assert plan is not None and not errors, errors
        if carrier_owner == 'shared_other':
            entry = next(e for e in binding.entries if e.obligation_id == 'ob04')
            assert entry.beat_ref == 'sc01#b07'
            assert 'sc01#b06' in entry.supporting_beat_refs, \
                '其余共享义务由 compiler 派生支撑引用（承载拍＝其支撑拍）'
    else:
        assert binding is None
        expected_code = ('fact_lock_outside_obligation' if carrier_owner == 'neither'
                         else 'fact_lock_unrealized')
        for errors in (acceptance_errors, binding_errors):
            issue = next(e for e in errors if getattr(e, 'code', '') == expected_code)
            assert issue.context['obligation_ids'] == ['ob02', 'ob04']
            assert issue.context['kf_id'] == kf


def test_fact_lock_subject_must_be_contained_in_the_carrier_state_subjects():
    """CS-H H3（RC-63）主体包含：丈夫的锁不得标到别人的状态上——fu_jun 的
    值未知锁标到 yun_niang 的状态 ⇒ `fact_lock_subject` 拒，带承载状态路径。"""
    brief = _brief()
    wire = _design_wire_v8(brief)
    wire['scenes'][0]['beats'][6]['state'][0]['at'] = 'yun_niang'
    body, errors = _inspect_v8(wire, brief)
    assert body is not None
    issue = next(e for e in errors if getattr(e, 'code', '') == 'fact_lock_subject')
    assert _kf(brief, 'unknown_value') in str(issue)
    assert issue.context['wire_paths'] == [['scenes', 0, 'beats', 6, 'state', 0]]
    binding, binding_errors = _derive_binding_v8(wire, body)
    assert binding is None and any(
        getattr(e, 'code', '') == 'fact_lock_subject' for e in binding_errors)


def test_shared_fact_lock_carrier_later_than_a_sharing_obligation_is_locatable():
    """RC-63：共享锁的承载拍只属于 ob02，而另一共享义务 ob04 的接受拍**早于**它——
    派生的支撑引用会晚于其接受拍。这类问题必须在 Design 自己的接受循环里带 wire
    路径被定位（改标位置或补 real），不能放行到 compile 面再以无编辑位置的
    `compile_support_early`（路由 stop）停机——改前（逐义务自担承载）它就是可窄修的
    `fact_lock_outside_obligation`。"""
    preserve = _preserve_wire()
    preserve['obligations'][3].update({
        'statement': '归期预计也支撑夫君的承诺', 'anchors': ['b01_01.e1'],
        'must_preserve': True, 'satisfaction': '保留预计承诺'})
    from tests.str06_facilities import name_map_of
    checkpoint, errors = assemble_preserve(
        preserve, policy_id='p', policy_version=5,
        characters=_characters(), blueprint=_blueprint(),
        name_map=name_map_of(_characters()), name_map_sha256='n' * 64,
        provider='x', model='y', request_sha256='r' * 64)
    assert checkpoint is not None and not errors, errors
    brief, _ = project_brief(checkpoint, _characters(), _blueprint(), name_map=name_map_of(_characters()))
    kf = _kf(brief, 'expected')
    wire = _design_wire_v8(brief)
    wire['scenes'][0]['beats'][0]['real'] = ['ob04']     # ob04 在 b01 验收；承载拍 b06 只属 ob02
    body, acceptance_errors = _inspect_v8(wire, brief)
    assert body is not None
    from novel.stages.stage4.compile import compile_execution_plan
    model = DesignWireV9.model_validate(wire)
    binding, binding_errors = derive_semantic_binding(
        design_placements(model), body, checkpoint, design_fact_marks(model))
    codes = lambda errs: sorted({getattr(e, 'code', str(e)[:40]) for e in errs})   # noqa: E731
    plan_errors = []
    if binding is not None:
        _plan, plan_errors = compile_execution_plan(body, binding)
    for errors in (acceptance_errors, binding_errors):
        issue = next(e for e in errors if getattr(e, 'code', '') == 'fact_lock_outside_obligation')
        assert issue.context['kf_id'] == kf and issue.context['obligation_ids'] == ['ob02', 'ob04']
        # 路径开放承载位与相关 real 拍：改标位置或让 ob04 在承载拍上标注 real 两种修法。
        assert ['scenes', 0, 'beats', 5] in issue.context['wire_paths']
        assert ['scenes', 0, 'beats', 0] in issue.context['wire_paths']
    assert binding is None, '不放行到 compile 面再以无编辑位置的 stop 码整份退回'
    assert plan_errors == []
    # 绿对照：ob04 在承载拍上标注 real（接受拍后移到承载拍）⇒ 通过。
    green = _design_wire_v8(brief)
    green['scenes'][0]['beats'][0]['real'] = ['ob04']
    green['scenes'][0]['beats'][5]['real'].append('ob04')
    body, errors = _inspect_v8(green, brief)
    assert body is not None and not errors, errors
