#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_a5_positions —— R10a a5 判据面的定向红绿例（承载位派生的
series 同拍校验＋epistemic_lock_order 锁位置 Guard＋design 修订轮）。

【验证边界】（CS-3 bind 退役后承载位＝design wire 的 real 标注）
· `derive_semantic_binding`：series 成员接受拍相同 → `series_collapsed`
  （C3/C5 的承载面，并入 design 同节点修订轮）；无标注的非必保义务进
  unbound（义务不删）；闭集与越界精确拒；
· design prompt 的义务清单渲染锁摘要（原 `project_binding_space` 的锁面
  送达职责迁入 `project_design_input`）；
· `lock_order_violations`：kp 键知情升级/读者确认早于授权该模式的义务承载
  位置 → `epistemic_lock_order`（C1/C6 的位置面）；授权位置取义务全部承载
  点的最早者（支撑拍是承载的一部分——怀疑渐进到证实合法，P2）；故事发生
  顺序不查（合法倒叙不拦，P5）；自由键不查；
· 回派路由与 `run_compile_stage4` 的回派编排（owner=design）；
· `run_body_design`：binding 派生错误并入 ask 同节点修订轮——坏稿先重问，
  修订好转正式位；粘性坏稿 ValidationStop 停（attempt 留证）。
「源理解是否正确」归 Stage2/golden，不在此。
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from contracts.schema.grounding import PreserveCheckpoint, ReconstructionBrief
from contracts.schema.source import BlueprintsArtifactV15, CharactersCore
from contracts.schema.workflow import route_owner_issue
from novel.stages.stage4.design import assemble_body_design
from novel.stages.stage4.grounding import (
    assemble_preserve, derive_semantic_binding, project_brief,
)
from novel.stages.stage4.guard import check_epistemic_lock_order
from tests.str06_expression_inputs import sample_narration, sample_readings
from tests.str06_facilities import v13_beat, v13_effect
from tests.str06_semantic_cases import CASES_BY_BATCH

A5_CASES = CASES_BY_BATCH['R10a-a5']

BASIS = ({'segment': 'e01_01', 'quote': '芸娘觉得夫君像是看穿了什么'},)
CONFIRM_BASIS = ({'segment': 'e01_02', 'quote': '芸娘亲眼证实了此事'},)
READER_BASIS = ({'segment': 'e01_02', 'quote': '证据摆到了明处'},)


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


def _expression():
    from contracts.schema.source import SourceExpression
    return SourceExpression.model_validate({
        'narration': {'person': 'third', 'focalizers': ['narrator']},
        'items': [
            {'ex_id': 'ex_01', 'kind': 'narration', 'statement': '第三人称旁白。',
             'source_refs': ['e01_01']},
            {'ex_id': 'ex_02', 'kind': 'contrast',
             'statement': '同一身影反复出现的对照意象。', 'source_refs': ['e01_01']},
        ],
        'readings': sample_readings().model_dump(mode='json')})


def _blueprint() -> BlueprintsArtifactV15:
    return BlueprintsArtifactV15.model_validate({
        'source_expression': _expression().model_dump(mode='json'),
        'world_constraints': [],
        'chapters': [
            {'chapter': 1,
             'beats': [
                 v13_beat('b01_01', '芸娘起疑；凭据成立', participants=('yun_niang',),
                          source_refs=('e01_01',),
                          effects=(
                              v13_effect({'kind': 'world'}, 'evidence_fact',
                                         '床底藏物属实',
                                         basis=BASIS),),
                          cognition=({'proposition': {
                                          'ref': 'p_x', 'statement': '夫君像是看穿了什么'},
                                      'holder': 'yun_niang', 'mode': 'suspects',
                                      'basis': BASIS},)),
                 v13_beat('b01_02', '芸娘证实；读者获知', participants=('yun_niang',),
                          source_refs=('e01_02',),
                          cognition=(
                              {'proposition': {'ref': 'p_x'},
                               'holder': 'yun_niang', 'mode': 'confirms',
                               'basis': CONFIRM_BASIS},
                              {'proposition': {'ref': 'p_x'},
                               'holder': 'reader', 'mode': 'learns',
                               'basis': READER_BASIS})),
                 v13_beat(
                     'b01_03', '巷口第一次现身', source_refs=('e01_03',),
                     effects=(
                         v13_effect(
                             {'kind': 'world'}, 'apparition_count', '第一次',
                             basis=({'segment': 'e01_03', 'quote': '第一次现身'},),
                         ),
                     ),
                 ),
                 v13_beat(
                     'b01_04', '夜市第二次现身', source_refs=('e01_04',),
                     effects=(
                         v13_effect(
                             {'kind': 'world'}, 'apparition_count', '第二次',
                             basis=({'segment': 'e01_04', 'quote': '第二次现身'},),
                         ),
                     ),
                 ),
                 v13_beat(
                     'b01_05', '约定归期（预计）', participants=('fu_jun',),
                     source_refs=('e01_05',),
                     effects=(
                         v13_effect(
                             {'kind': 'world'}, 'return_time', '约半月',
                             modality='expected',
                             proposition={'ref': 'p_return',
                                          'statement': '夫君约半月后归家'},
                             basis=({'segment': 'e01_05', 'quote': '约莫半月便回'},),
                         ),
                     ),
                 ),
             ],
             'scenes': [{'scene_id': 'sc01',
                         'beat_ids': ['b01_01', 'b01_02', 'b01_03', 'b01_04',
                                      'b01_05']}],
             'causal_edges': []},
        ],
        'evidence': {'e01_01': '芸娘觉得夫君像是看穿了什么，床底藏物属实。',
                     'e01_02': '芸娘亲眼证实了此事，证据摆到了明处。',
                     'e01_03': '巷口第一次现身。',
                     'e01_04': '夜市第二次现身。',
                     'e01_05': '他只说约莫半月便回。'},
    })


def _preserve_wire() -> dict:
    return {'protocol_version': 'stage4-preserve-v2', 'obligations': [
        {'kind': 'event_core', 'statement': '芸娘从起疑到证实同一件事',
         'anchors': ['b01_01.c1', 'b01_02.c1'],
         'must_preserve': True, 'satisfaction': '怀疑渐进到证实',
         'requires': []},
        {'kind': 'event_core', 'statement': '读者得知实证',
         'anchors': ['b01_02.c2'],
         'must_preserve': True, 'satisfaction': '证实拍让读者获知',
         'requires': []},
        {'kind': 'event_core', 'statement': '身影第一次现身',
         'anchors': ['b01_03.e1'], 'must_preserve': False, 'satisfaction': '',
         'requires': []},
        {'kind': 'event_core', 'statement': '身影第二次现身',
         'anchors': ['b01_04.e1'], 'must_preserve': False, 'satisfaction': '',
         'requires': []},
        {'kind': 'event_core', 'statement': '夫君约定归期',
         'anchors': ['b01_05.e1'],
         'must_preserve': True, 'satisfaction': '归期是预计而非已发生',
         'requires': []},
    ],
        'series': [{'members': [3, 4], 'mechanism': 'ex_02'}]}


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


def _kp_x(brief: ReconstructionBrief) -> str:
    return next(p.kp_id for p in brief.propositions
                if p.statement == '夫君像是看穿了什么')


def _kf_expected(brief: ReconstructionBrief) -> str:
    return next(f.kf_id for f in brief.fact_locks if f.kind == 'expected')


def _design_wire(brief: ReconstructionBrief, *, flashback: bool = False) -> dict:
    """sc01 七拍绿基座；flashback=True 时交换怀疑/证实承载（P5 合法倒叙）。"""
    kp_x = _kp_x(brief)
    suspect_beat, confirm_beat = (3, 2) if flashback else (2, 3)

    def _knowledge(mode: str) -> dict:
        return {'domain': 'knowledge', 'subjects': [], 'holder': 'yun_niang',
                'key': kp_x, 'op': 'set', 'value': f'她{mode}了此事',
                'scope': 'book', 'knowledge_mode': mode}

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
             'driver': {'focal_actor': 'yun_niang', 'immediate_want': '查明',
                        'opposition': '沉默', 'turn': '证实'},
             'realization_brief': '从异样写起，在证实处收住。',
             'delivery': {'pov': 'narrator', 'voice': '冷峻', 'avoid': []},
             'beats': [
                 {'event': '凭据成立', 'participants': ['yun_niang'],
                  'delivery_hint': '', 'target_delta': [
                      {'domain': 'world', 'subjects': [], 'holder': None,
                       'key': 'evidence_fact', 'op': 'set', 'value': '床底藏物属实',
                       'scope': 'book', 'modality': 'actual'}],
                  'expression_refs': [], 'surface_introductions': []},
                 {'event': f'拍{suspect_beat} 怀疑', 'participants': ['yun_niang'],
                  'delivery_hint': '', 'target_delta': [_knowledge('suspected')],
                  'expression_refs': [], 'surface_introductions': []},
                 {'event': f'拍{confirm_beat} 证实', 'participants': ['yun_niang'],
                  'delivery_hint': '', 'target_delta': [_knowledge('known')],
                  'expression_refs': [], 'surface_introductions': []},
                 {'event': '读者获知', 'participants': ['yun_niang'],
                  'delivery_hint': '', 'target_delta': [
                      {'domain': 'knowledge', 'subjects': [], 'holder': 'reader',
                       'key': kp_x, 'op': 'set', 'value': '床底藏物属实',
                       'scope': 'book', 'knowledge_mode': 'known', 'confirms': {'domain': 'world', 'key': 'evidence_fact'}}],
                  'expression_refs': [], 'surface_introductions': []},
                 {'event': '第一次现身', 'participants': [],
                  'delivery_hint': '', 'target_delta': [],
                  'expression_refs': [], 'surface_introductions': []},
                 {'event': '第二次现身', 'participants': [],
                  'delivery_hint': '', 'target_delta': [],
                  'expression_refs': [], 'surface_introductions': []},
                 {'event': '约定归期', 'participants': ['fu_jun'],
                  'delivery_hint': '', 'target_delta': [
                      {'domain': 'world', 'subjects': [], 'holder': None,
                       'key': 'return_time', 'op': 'set', 'value': '约半月后归家',
                       'scope': 'book', 'modality': 'expected'}],
                  'expression_refs': [], 'surface_introductions': []},
             ]}],
        'chapters': [{'purpose': '疑到证', 'scenes': 1}],
        'scene_weights': [1],
    }


def _without_fact_realization(brief: ReconstructionBrief) -> ReconstructionBrief:
    """本文件的 v5 基座只验承载位／知情锁位置，不经 v8 `lock` 标注通道；事实锁的
    接受与映射见 `test_str06_a4_design_locks.py` 的 CS-F2 一节（真实 v8 入口）。"""
    return brief.model_copy(update={'requirements': tuple(
        r.model_copy(update={'realize_locks': ()}) for r in brief.requirements)})


def _assemble_design(brief: ReconstructionBrief, **over):
    return assemble_body_design(
        _design_wire(brief, **over), known_char_ids={'yun_niang', 'fu_jun'},
        expression_requirements=brief.expression,
        source_narration=brief.source_narration,
        brief=_without_fact_realization(brief))


def _positions(body):
    return {s.scene_id: {b.beat_id for b in s.beats} for s in body.scenes}


#: 绿 real 标注投影（ob 编号 → 标注拍 (scene 序, beat 序) 阅读序）：ob01 复合
#: 承载（支撑 b02→接受 b03）、ob02→b04、series 分别 b05/b06、kf→b07。
def _placements(**over) -> dict[str, list[tuple[int, int]]]:
    placements = {
        'ob01': [(0, 1), (0, 2)],
        'ob02': [(0, 3)],
        'ob03': [(0, 4)],
        'ob04': [(0, 5)],
        'ob05': [(0, 6)],
    }
    placements.update(over)
    return {k: list(v) for k, v in placements.items() if v}


def _green_graph_and_binding(placements=None):
    brief = _brief()
    body, errors = _assemble_design(brief)
    assert body is not None and not errors, errors
    binding, errors = derive_semantic_binding(
        _placements() if placements is None else placements, body, _checkpoint())
    assert binding is not None and not errors, errors
    return body, binding, brief


def test_initial_reader_confirmation_requires_an_actual_initial_fact():
    from contracts.schema.design import BodyDesign, TargetDelta
    from novel.stages.stage4 import compile_execution_plan
    body, binding, brief = _green_graph_and_binding()
    confirmation = TargetDelta(domain='knowledge', key=_kp_x(brief), holder='reader',
                               op='set', scope='book', value='开篇前已确认', knowledge_mode='known',
                               confirms={'domain': 'world', 'key': 'initial_evidence'})
    fact = TargetDelta(domain='world', key='initial_evidence', op='set', scope='book',
                       value='证据已存在', modality='actual')
    for changes, passes in (((fact, confirmation), True), ((confirmation,), False),
                            ((fact.model_copy(update={'modality': 'planned'}),
                              confirmation), False)):
        graph = BodyDesign.model_validate(body.model_copy(update={
            'initial_state': changes}).model_dump())
        plan, errors = compile_execution_plan(graph, binding)
        if passes:
            assert plan is not None and not errors, errors
        else:
            assert plan is None and any('初始态的读者确认地址' in e for e in errors), errors


def test_v7_composite_obligation_preserves_cognitive_change_positions():
    from novel.stages.stage4.grounding import design_obligation_index
    from novel.stages.stage4.slim import knowledge_change_index
    checkpoint, brief = _checkpoint(), _brief()
    obligations = design_obligation_index(checkpoint, brief)
    source = _design_wire(brief)
    beats = []
    carriers = {1: ['ob01'], 2: ['ob01'], 3: ['ob02'],
                4: ['ob03'], 5: ['ob04'], 6: ['ob05']}
    for i, beat in enumerate(source['scenes'][0]['beats']):
        state = []
        for d in beat['target_delta']:
            if d['domain'] == 'knowledge':
                continue
            row = {'at': 'world', 'key': d['key'], 'val': d['value'],
                   'modality': d.get('modality')}
            if d.get('modality') == 'expected':       # 事实锁：在已选状态上标注
                row['lock'] = _kf_expected(brief)
            state.append(row)
        beats.append({'ev': beat['event'], 'who': beat['participants'],
                      'real': carriers.get(i, []), 'state': state})
    for ref, (ob, change) in knowledge_change_index(obligations).items():
        position = 3 if change.holder == 'reader' else 1 if change.mode == 'suspected' else 2
        placement = {'ref': ref}
        if change.holder == 'reader':
            placement['confirms'] = {'domain': 'world', 'key': 'evidence_fact'}
        beats[position].setdefault('know', []).append(placement)
    wire = {'protocol_version': 'stage4-design-v8', 'book_style': source['book_style'],
            'scenes': [{'driver': source['scenes'][0]['driver'], 'brief': '由起疑推进到证实',
                        'beats': beats}], 'chapters': source['chapters']}
    body, errors = assemble_body_design(
        wire, known_char_ids={'yun_niang', 'fu_jun'}, expression_requirements=brief.expression,
        source_narration=brief.source_narration, brief=brief, obligations=obligations)
    assert not errors, errors
    assert body.scenes[0].beats[1].target_delta[0].knowledge_mode == 'suspected'
    assert body.scenes[0].beats[2].target_delta[0].knowledge_mode == 'known'
    assert body.scenes[0].beats[3].target_delta[0].confirms.key == 'evidence_fact'
    binding, errors = derive_semantic_binding(
        {ob: [(0, i) for i, beat in enumerate(beats) if ob in beat['real']]
         for ob in obligations}, body, checkpoint)
    assert not errors, errors
    assert check_epistemic_lock_order(body, binding) == []


def test_same_summary_does_not_merge_obligation_knowledge_modes():
    from novel.stages.stage4.grounding import design_obligation_index
    checkpoint = _checkpoint()
    rows = list(checkpoint.obligations)
    rows[1] = rows[1].model_copy(update={'statement': rows[0].statement})
    checkpoint = checkpoint.model_copy(update={'obligations': tuple(rows)})
    brief, _ = project_brief(checkpoint, _characters(), _blueprint())
    index = design_obligation_index(checkpoint, brief)
    assert index['ob01'].core_id != index['ob02'].core_id
    assert {c.holder for c in index['ob01'].knowledge_changes} == {'yun_niang'}
    assert {c.holder for c in index['ob02'].knowledge_changes} == {'reader'}


# ───────────────────────── 案例登记与路由 ─────────────────────────

def test_case_registry_pins_a5_faces():
    """CASES_BY_BATCH['R10a-a5'] 覆盖 C3/C5/C6/P5 的红绿落点（防删例）。"""
    ids = {c.case_id for c in A5_CASES}
    assert {'C3', 'C5', 'C6', 'P5'} <= ids


def test_epistemic_lock_order_routes_to_body_design():
    action, target = route_owner_issue('epistemic_lock_order')
    assert (action, target) == ('revise_upstream', 'str06.stage4.body_design')


# ───────────────────────── 锁摘要送达（design 义务清单） ─────────────────────

def test_design_obligation_table_carries_series_and_lock_digest():
    """锁摘要与 series 面经 `project_design_input` 进 design prompt（原
    `project_binding_space` 锁面的迁移落点——承载选择依据随 bind 退役并入
    design 的义务清单）。"""
    from novel.stages.stage4.slim import project_design_input
    from novel.stages.stage4.grounding import design_obligation_index

    body, binding, brief = _green_graph_and_binding()
    checkpoint = _checkpoint()
    index = design_obligation_index(checkpoint, brief)
    table = project_design_input(brief, index)['requirements']
    # ob01：锁列带 holder|kp（a4 授权面）与 kf；realize 分母渲染进清单
    cognitive = list(table['ob01']['knowledge_changes'].values())
    assert len(cognitive) == 2
    assert all(row['holder'] == 'yun_niang' and row['kp'].startswith('kp_') for row in cognitive)
    assert {row['mode'] for row in cognitive} == {'known', 'suspected'}
    # ob05：事实锁（expected）进锁列
    assert any(lock.startswith('kf_') for lock in table['ob05']['realize_locks'])
    # series 面在 Brief.requirements（series_group/series_mechanism），
    # 义务清单行带必保与 satisfaction
    assert table['ob01']['must_preserve'] is True
    assert '怀疑渐进到证实' in table['ob01']['satisfaction']
    # 派生 binding 的 series 成员接受拍互斥（绿基座证不同拍）
    assert len(binding.entries) == 5 and not binding.unbound


# ───────────────────────── series 同拍（C3/C5） ─────────────────────────

def test_series_members_same_beat_rejected_and_split_passes():
    """C3/C5 红例：series 两成员接受拍相同 → series_collapsed；绿：分别承载。"""
    body, _binding, _brief = _green_graph_and_binding()
    checkpoint = _checkpoint()
    collapsed = _placements(ob04=[(0, 4)])
    binding, errors = derive_semantic_binding(collapsed, body, checkpoint)
    assert binding is None
    assert any('series_collapsed' in e and 'ob03' in e and 'ob04' in e
               for e in errors), errors
    split, errors = derive_semantic_binding(_placements(), body, checkpoint)
    assert split is not None and not errors, errors


def test_unbound_series_member_does_not_collapse():
    """无标注成员进 unbound（义务不删、owner=design），不参与同拍比较。"""
    body, _binding, _brief = _green_graph_and_binding()
    checkpoint = _checkpoint()
    placements = _placements(ob04=[])
    binding, errors = derive_semantic_binding(placements, body, checkpoint)
    assert binding is not None and not errors, errors
    assert [u.obligation_id for u in binding.unbound] == ['ob04']
    assert binding.unbound[0].owner == 'design'


# ───────────────────────── epistemic_lock_order（C1/C6 位置面） ─────────────

def test_knowledge_upgrade_before_authorized_position_rejected():
    """C1 位置红例：known 升级（b03）早于授权义务承载位（ob01→b04 无支撑）
    → epistemic_lock_order，消息带升级/授权位置与源盲义务。"""
    body, _binding, brief = _green_graph_and_binding(
        placements=_placements(ob01=[(0, 3)]))
    problems = check_epistemic_lock_order(body, _binding)
    assert problems, '升级早于授权承载位必须红'
    assert any('epistemic_lock_order' in p and _kp_x(brief) in p
               and 'sc01#b03' in p and 'sc01#b04' in p for p in problems), problems


def test_reader_confirmation_before_authorized_position_rejected():
    """C6 位置红例：读者确认（b04）早于授权锁承载位（ob02→b05）。"""
    body, _binding, brief = _green_graph_and_binding(
        placements=_placements(ob02=[(0, 4)]))
    problems = check_epistemic_lock_order(body, _binding)
    assert any('读者确认' in p and 'sc01#b04' in p and 'sc01#b05' in p
               for p in problems), problems


def test_upgrade_at_supporting_beat_is_legal():
    """P2 绿：怀疑承载在支撑拍（b02＝义务最早承载点）合法——怀疑渐进到
    证实是义务内涵，不得因验收拍在 b03 而误拦。"""
    body, binding, _brief = _green_graph_and_binding()
    assert check_epistemic_lock_order(body, binding) == []


def test_legal_flashback_not_blocked():
    """P5 绿：先叙述证实、再回顾怀疑（合法倒叙）不拦——Guard 只对授权位
    比较各模式位置，不按「怀疑必须先于证实」的阅读序拦。"""
    brief = _brief()
    body, errors = _assemble_design(brief, flashback=True)
    assert body is not None and not errors, errors
    binding, errors = derive_semantic_binding(_placements(), body, _checkpoint())
    assert binding is not None and not errors, errors
    assert check_epistemic_lock_order(body, binding) == []


def test_free_key_and_unauthorized_mode_not_checked():
    """自由键（非 kp_*）的 knowledge set 不进位置判据；无必保授权的模式
    无位置分母（模式合法性归 a4）。"""
    body, _binding, brief = _green_graph_and_binding()
    # ob03/ob04 非必保：它们的承载拍（b05/b06）晚于 b04 的 reader 确认无关——
    # reader 的授权义务是 ob02（b04），绿基座已证。这里改一条自由键 set：
    body_wire_free = body.model_dump(mode='json')
    beat = body_wire_free['scenes'][0]['beats'][1]
    beat['target_delta'].append(
        {'domain': 'knowledge', 'subjects': [], 'holder': 'yun_niang',
         'key': 'free_guess', 'op': 'set', 'value': '自由键', 'scope': 'book',
         'knowledge_mode': 'known'})
    del beat
    from contracts.schema.design import BodyDesign
    mutated = BodyDesign.model_validate(body_wire_free)
    binding, errors = derive_semantic_binding(_placements(), mutated, _checkpoint())
    assert binding is not None, errors
    assert check_epistemic_lock_order(mutated, binding) == [], \
        '自由键不是锁句柄，位置判据不查'


def test_initial_state_knowledge_not_in_position_judgement():
    """initial_state 的 kp set 不进位置判据（初态＝开篇前成立，无披露时点；
    其锁面归 a4 接受校验）。"""
    brief = _brief()
    wire = _design_wire(brief)
    wire['initial_state'] = [
        {'domain': 'knowledge', 'subjects': [], 'holder': 'yun_niang',
         'key': _kp_x(brief), 'op': 'set', 'value': '开篇前已知', 'scope': 'book',
         'knowledge_mode': 'suspected'}]
    body, errors = assemble_body_design(
        wire, known_char_ids={'yun_niang', 'fu_jun'},
        expression_requirements=brief.expression,
        source_narration=brief.source_narration,
        brief=_without_fact_realization(brief))
    assert body is not None and not errors, errors
    binding, errors = derive_semantic_binding(_placements(), body, _checkpoint())
    assert binding is not None, errors
    assert check_epistemic_lock_order(body, binding) == []


# ───────────────────────── compile 回派编排 ─────────────────────────

def _repo_with_design_products(tmp_path):
    from tests.str06_faults import looped_full_chain_wires
    from tests.test_str06_body import _make_session

    wires = looped_full_chain_wires()
    repo, _session, _transport = _make_session(tmp_path, wires, name='a5route')
    from tests.str06_facilities import make_context, synthetic_ref
    from novel.llm.ledger import CostLedger
    repo.attach_context(make_context(repo.root, ledger=CostLedger(path=None)))
    return repo


def test_compile_backfills_epistemic_lock_order_to_design(tmp_path):
    """锁位置违例 → 回派 body_design：owner=design、item=kp 句柄、
    context 带升级/授权位置与源盲 core_id。"""
    from novel.stages.chain import _backfill_epistemic_lock_order
    from novel.stages.owner_revision import (
        OwnerRevisionRequired, read_owner_issue_file,
    )

    repo = _repo_with_design_products(tmp_path)
    body, binding, brief = _green_graph_and_binding()
    from tests.str06_facilities import synthetic_ref
    repo.write('stage4/design/body_design.json', body, owners=('design',),
               refs=(synthetic_ref('design', '已购输入'),))
    bad_body, bad_binding, brief = _green_graph_and_binding(
        placements=_placements(ob01=[(0, 3)]))
    assert bad_binding is not None
    with pytest.raises(OwnerRevisionRequired) as excinfo:
        _backfill_epistemic_lock_order(repo, bad_body, bad_binding)
    assert excinfo.value.owner == 'design'
    assert excinfo.value.node == 'str06.stage4.body_design'
    issues = excinfo.value.issues
    assert all(i.code == 'epistemic_lock_order' for i in issues)
    assert any(i.item == _kp_x(brief) for i in issues)
    contexts = [i.context for i in issues if i.item == _kp_x(brief)]
    # known（b03）与 suspected（b02）都早于改后的授权位 b04——两条都报，
    # 这里钉 known 面（C1 的升级面）与源盲 core_id。
    assert any(c['mode'] == 'known' and c['upgrade_position'] == 'sc01#b03'
               and c['authorized_position'] == 'sc01#b04' for c in contexts)
    assert all(c['authorized_core_id'].startswith('req_') for c in contexts)
    record = read_owner_issue_file(repo, 'design')
    assert record.status == 'open' and record.raised_by == 'str06.stage4.compile'


# ───────────── binding 派生错误并入 design 修订轮（stage，fake transport） ─────

#: series 两成员接受拍同拍的坏 design wire（looped 基座：preserve wire 带
#: series [1,2]；坏稿把 ob02 的 real 也标到 b02——与 ob01 接受拍同拍）。
def _a5_wires():
    from tests.str06_faults import looped_full_chain_wires

    wires = deepcopy(looped_full_chain_wires())
    wires['preserve'] = {
        'protocol_version': 'stage4-preserve-v2',
        'obligations': [
            {'kind': 'event_core', 'statement': '苏芸娘嫁入陆家且夫君立下旧约',
             'anchors': ['b01_01.e1'], 'must_preserve': True,
             'satisfaction': '正文呈现婚约成立且夫君立下旧约', 'requires': []},
            {'kind': 'event_core', 'statement': '同一婚约的第二次回响',
             'anchors': ['b01_01.e1'], 'must_preserve': False, 'satisfaction': '',
             'requires': []}],
        'series': [{'members': [1, 2], 'mechanism': None}]}
    # design wire（v6）：ob01 支撑 b01＋接受 b02；ob02 接受 b01（绿基座）。
    wires['design']['scenes'][0]['beats'][0]['real'] = ['ob01']
    wires['design']['scenes'][0]['beats'][1]['real'] = ['ob01']
    return wires


def _bad_design(wires):
    """series 同拍坏稿：ob02 的 real 标到 b02（与 ob01 接受拍相同）。"""
    bad = deepcopy(wires['design'])
    bad['scenes'][0]['beats'][1]['real'] = ['ob01', 'ob02']
    return bad


class _DesignFaultTransport:
    """design 原始请求回 series 同拍坏 wire；修订轮（prompt 带上一版）粘性时
    仍回坏 wire（同指纹 → 无进展停），非粘性交给内层干净 wire。"""

    def __init__(self, wires, sticky=False):
        from tests.test_str06_body import _RoutingTransport
        self._inner = _RoutingTransport(wires)
        self._bad = _bad_design(wires)
        self._sticky = sticky

    @property
    def calls(self):
        return self._inner.calls

    def send(self, request):
        import json as _json
        from novel.llm.transport import ChatResponse
        if self._inner._owner(request) == 'design' and (
                self._sticky or '上一版' not in request.user):
            self._inner.calls.append('design')
            return ChatResponse(
                content=_json.dumps(self._bad, ensure_ascii=False),
                finish_reason='stop',
                usage={'prompt_tokens': 100, 'completion_tokens': 40},
                model='fake')
        return self._inner.send(request)


def _run_to_design(tmp_path, transport_factory, monkeypatch):
    from novel.stages.chain import run_workflow
    from tests.str06_facilities import json_ask, stub_body_runner, text_ask
    from tests.test_str06_body import _chapters, _make_session

    wires = _a5_wires()
    repo, session, transport = _make_session(
        tmp_path, wires, name='a5bind', transport=transport_factory(wires))
    stub_body_runner(monkeypatch)

    def runner():
        return run_workflow(
            repository=repo, chapters=_chapters(), ask=json_ask(session),
            ask_text=text_ask(session),
            ask_body=lambda o, p: (_ for _ in ()).throw(AssertionError('stub')),
            budget=4000, through='stage4',
            node_filter=['str06.stage2.book', 'str06.stage2.chapters',
                         'str06.stage2.names', 'str06.stage2.associate',
                         'str06.stage2.compile', 'str06.stage4.preserve',
                         'str06.stage4.body_design'])
    return runner, repo, transport


def test_binding_derivation_gate_runs_inside_design_revision_loop(
        tmp_path, monkeypatch):
    """series 同拍坏稿在 design 修订轮内被重问——design 恰两次调用，修订后
    双产物（body_design＋semantic_binding）转正。"""
    runner, repo, transport = _run_to_design(
        tmp_path, lambda wires: _DesignFaultTransport(wires), monkeypatch)
    runner()
    assert transport.calls.count('design') == 2
    assert repo.path('stage4/design/body_design.json').is_file()
    assert repo.path('stage4/grounding/semantic_binding.json').is_file()


def test_sticky_bad_design_stops_with_no_progress(tmp_path, monkeypatch):
    """粘性坏稿：修订轮无进展 ValidationStop 停——正式位不写半份产物
    （attempt 由 ask 层留证；INVALID 孪生面随 bind 节点退役）。"""
    from novel.llm.paid_requests import ValidationStop

    runner, repo, transport = _run_to_design(
        tmp_path, lambda wires: _DesignFaultTransport(wires, sticky=True),
        monkeypatch)
    with pytest.raises(ValidationStop) as excinfo:
        runner()
    assert excinfo.value.code == 'validation_no_progress'
    assert transport.calls.count('design') == 2
    assert not repo.path('stage4/design/body_design.json').exists()
    assert not repo.path('stage4/grounding/semantic_binding.json').exists()


# ───────── R10c1b：preserve 构造期模型校验归入修订反馈 ─────────

def test_preserve_model_validation_error_feeds_revision_not_crash():
    """R10c1b 实证（2026-10-02）：series 互斥等 PreserveCheckpoint 构造期校验
    此前裸穿成 exit 70「结论未知」，修订环拿不到反馈。assemble_preserve 必须
    把 ValidationError 归入 errors（同节点修订轮带精确问题重问）。"""
    from novel.stages.stage4.grounding import assemble_preserve
    from contracts.schema.source import (
        BlueprintsArtifactV15, ChapterBlueprint, CharacterEntry,
        CharactersCore, SurfaceWindow,
    )
    from tests.str06_expression_inputs import sample_blueprint_expression
    from tests.str06_facilities import v13_beat, v13_effect
    entry = {'kind': 'event_core', 'statement': '婚约成立',
             'must_preserve': True, 'satisfaction': '明确呈现',
             'anchors': ['b01_01.e1']}
    entry2 = {'kind': 'event_core', 'statement': '婚约的第二次回响',
              'must_preserve': False, 'satisfaction': '',
              'anchors': ['b01_01.e1']}
    try:
        checkpoint, errors = assemble_preserve(
            {'protocol_version': 'stage4-preserve-v2',
             'obligations': [entry, entry2],
             'series': [{'members': [1, 2], 'mechanism': None},
                        {'members': [1, 2], 'mechanism': None}]},  # 全员双 series
            policy_id='preserve-default', policy_version=1,
            characters=CharactersCore(characters={'ch1': CharacterEntry(
                canonical='苏姑娘',
                surfaces=(SurfaceWindow(surface='苏姑娘', from_chapter=1,
                                        source_refs=('e01_01',)),))}),
            blueprint=BlueprintsArtifactV15(
                source_expression=sample_blueprint_expression(),
                chapters=(ChapterBlueprint(
                    chapter=1,
                    beats=(v13_beat('b01_01', 'e', effects=(
                        v13_effect({'kind': 'world'}, 'fate', '多舛'),)),),
                    scenes=({'scene_id': 'sc01', 'beat_ids': ('b01_01',)},),
                    causal_edges=()),),
                evidence={'e01_01': '苏姑娘初到陆家。'}),
            provider='fake', model='fake', request_sha256='f' * 64)
    except Exception as exc:  # 修复前：ValidationError 裸穿
        raise AssertionError(f'构造期校验不得裸穿：{type(exc).__name__}: {exc}')
    assert checkpoint is None
    assert any('模型校验失败' in e and 'series' in e for e in errors), errors
