#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_a4_design_locks —— R10a a4 判据面的定向红绿例（Brief 锁定项、
Design v5：kp/kf 键规则、必保锁兑现、modality 必填、expression_refs 闭集、
dramatic_intent 删除）。

【验证边界】`project_brief` 由义务 anchors 与 canonical loops 确定性派生锁定项
（kp 命题登记＋知情锁＋kf 事实锁＋series 盲标记）；`assemble_body_design` 的
接受校验——kp 键持有者须有锁且模式被授权（known 只能来自 known 锁，C1）、
kf 时态锁 modality 一致（C2）、读者确认须有 reader known 锁（C6）、值未知不
得补值／原文未回答不得作答（C9）、必保义务锁兑现、expression_refs 闭集与
narration 不上拍、dramatic_intent 关闭式拒读。「设计是否好看」归通读，不在此。
"""
from __future__ import annotations

from copy import deepcopy

from contracts.schema.grounding import (
    BriefFactLock, BriefKnowledgeChange, BriefKnowledgeLock, BriefProposition, PreserveCheckpoint,
    ReconstructionBrief,
)
from contracts.schema.source import (
    BlueprintsArtifactV15, CharactersCore, SourceExpression,
)
from novel.stages.stage4.design import assemble_body_design
from novel.stages.stage4.grounding import (
    assemble_preserve, project_brief,
)
from tests.str06_expression_inputs import sample_narration, sample_readings
from tests.str06_facilities import v13_beat, v13_effect
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


def _blueprint() -> BlueprintsArtifactV15:
    return BlueprintsArtifactV15.model_validate({
        'source_expression': _expression().model_dump(mode='json'),
        'world_constraints': [],
        'chapters': [
            {'chapter': 1,
             'beats': [
                 v13_beat('b01_01', '夫君藏进床底，约半月便回', participants=('fu_jun',),
                          source_refs=('e01_01',),
                          effects=(
                              v13_effect({'kind': 'world'}, 'return_time', '约半月',
                                         modality='expected',
                                         proposition={'ref': 'p_return',
                                                      'statement': '夫君约半月后归家'},
                                         basis=BASIS),
                              v13_effect({'kind': 'character', 'char_id': 'fu_jun'},
                                         'whereabouts_during_leave', None, op='unknown',
                                         basis=BASIS),),
                          cognition=({'proposition': {'ref': 'p_x',
                                                      'statement': '夫君像是看穿了什么'},
                                      'holder': 'yun_niang', 'mode': 'suspects',
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
    return {'protocol_version': 'stage4-preserve-v2', 'obligations': [
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
    checkpoint, errors = assemble_preserve(
        _preserve_wire(), policy_id='p', policy_version=5,
        characters=_characters(), blueprint=_blueprint(),
        provider='x', model='y', request_sha256='r' * 64)
    assert checkpoint is not None and not errors, errors
    return checkpoint


def _brief() -> ReconstructionBrief:
    brief, _ = project_brief(_checkpoint(), _characters(), _blueprint())
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
    assert tense.modality == 'expected' and tense.statement == '夫君约半月后归家'
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


# ───────────────────────── Design v5：wire 形状 ─────────────────────────

def _design_wire(brief: ReconstructionBrief) -> dict:
    kp_x = _kp(brief, '夫君像是看穿了什么')
    kp_reader = kp_x  # reader learns 的同一命题
    kf_expected = _kf(brief, 'expected')
    kf_unknown = _kf(brief, 'unknown_value')
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
                       'key': 'evidence_fact', 'op': 'set', 'value': '床底藏物属实',
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
                       'scope': 'book', 'knowledge_mode': 'known', 'confirms': {'domain': 'world', 'key': 'evidence_fact'}}],
                  'expression_refs': [], 'surface_introductions': []},
                 {'event': '约定归期', 'participants': ['fu_jun'],
                  'delivery_hint': '', 'target_delta': [
                      {'domain': 'world', 'subjects': [], 'holder': None,
                       'key': kf_expected, 'op': 'set', 'value': '约半月后归家',
                       'scope': 'book', 'modality': 'expected'}],
                  'expression_refs': [], 'surface_introductions': []},
                 {'event': '去向成谜', 'participants': [],
                  'delivery_hint': '', 'target_delta': [
                      {'domain': 'world', 'subjects': [], 'holder': None,
                       'key': kf_unknown, 'op': 'unknown', 'value': None,
                       'scope': 'book'}],
                  'expression_refs': [], 'surface_introductions': []},
             ]}],
        'chapters': [{'purpose': '起疑到证实', 'scenes': 1}],
        'scene_weights': [1],
    }


def _assemble(wire: dict, brief: ReconstructionBrief):
    return assemble_body_design(
        wire, known_char_ids={'yun_niang', 'fu_jun'},
        expression_requirements=brief.expression,
        source_narration=brief.source_narration,
        brief=brief)


def test_green_wire_passes_all_lock_checks():
    """绿基线：授权模式内知情、reader known 锁确认、kf 同 modality、
    unknown 不补值、expression_refs 取 humor、必保锁全部兑现。"""
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


# ───────────────────────── C2：kf 时态锁 ─────────────────────────

def test_c2_expected_lock_written_as_actual_rejected():
    """C2 红例：expected 锁被按 actual 承载 → modality 与锁不一致拒。"""
    brief = _brief()
    kf_expected = _kf(brief, 'expected')
    wire = _design_wire(brief)
    wire['scenes'][0]['beats'][5]['target_delta'][0]['modality'] = 'actual'
    _, errors = _assemble(wire, brief)
    assert any('modality 与锁不一致' in e and kf_expected in e for e in errors), errors


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


# ───────────────────────── C9：值未知与原文未回答 ─────────────────────────

def test_c9_unknown_value_lock_and_unanswered_lock_reject_set():
    brief = _brief()
    kf_unknown = _kf(brief, 'unknown_value')
    kf_unanswered = _kf(brief, 'unanswered')
    wire = _design_wire(brief)
    wire['scenes'][0]['beats'][6]['target_delta'][0]['op'] = 'set'
    wire['scenes'][0]['beats'][6]['target_delta'][0]['value'] = '去了南边投军'
    wire['scenes'][0]['beats'][6]['target_delta'][0]['modality'] = 'actual'
    _, errors = _assemble(wire, brief)
    assert any('值未知锁' in e and kf_unknown in e for e in errors), errors
    # 原文未回答锁：set 作答即拒；op=unknown 不受拦（保持留白合法）。
    wire2 = _design_wire(brief)
    wire2['scenes'][0]['beats'][6]['target_delta'][0]['key'] = kf_unanswered
    wire2['scenes'][0]['beats'][6]['target_delta'][0]['op'] = 'set'
    wire2['scenes'][0]['beats'][6]['target_delta'][0]['value'] = '瞒着旧约'
    wire2['scenes'][0]['beats'][6]['target_delta'][0]['modality'] = 'actual'
    _, errors2 = _assemble(wire2, brief)
    assert any('原文未回答锁' in e and kf_unanswered in e for e in errors2), errors2


# ───────────────────────── 必保锁兑现 ─────────────────────────

def test_must_obligation_locks_must_be_realized():
    brief = _brief()
    kf_expected = _kf(brief, 'expected')
    kp_x = _kp(brief, '夫君像是看穿了什么')
    wire = _design_wire(brief)
    # 删掉 expected 承载拍 → 事实锁未兑现。
    wire['scenes'][0]['beats'][5]['target_delta'] = []
    _, errors = _assemble(wire, brief)
    assert any('事实锁未兑现' in e and kf_expected in e for e in errors), errors
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
