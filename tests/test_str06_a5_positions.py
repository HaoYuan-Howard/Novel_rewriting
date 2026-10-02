#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_str06_a5_positions —— R10a a5 判据面的定向红绿例（Bind series 同拍
校验＋epistemic_lock_order 锁位置 Guard＋run_bind 接受闸进修订轮）。

【验证边界】
· `project_binding_space` 带出 series 与锁摘要（Brief 锁面的渲染，承载选择
  依据）；
· `assemble_binding`：series 成员绑定同一拍 → `series_collapsed`（C3/C5 的
  承载面，同节点修订）；
· `lock_order_violations`：kp 键知情升级/读者确认早于授权该模式的义务承载
  位置 → `epistemic_lock_order`（C1/C6 的位置面）；授权位置取义务全部承载
  点的最早者（支撑拍是承载的一部分——怀疑渐进到证实合法，P2）；故事发生
  顺序不查（合法倒叙不拦，P5）；自由键不查；
· 回派路由与 `run_compile_stage4` 的回派编排（owner=design）；
· `run_bind`：接受闸接进 ask 同节点修订轮——坏稿先重问（R-5.5 改写），
  修订好转正式位；粘性坏稿 ValidationStop 后写 `.INVALID` 孪生并停。
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
    assemble_binding, assemble_preserve, project_binding_space, project_brief,
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
                      {'domain': 'reader', 'subjects': [], 'holder': None,
                       'key': kp_x, 'op': 'set', 'value': '床底藏物属实',
                       'scope': 'book',
                       'confirms': {'domain': 'world', 'key': 'evidence_fact',
                                    'subjects': [], 'holder': None}}],
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
                       'key': _kf_expected(brief), 'op': 'set', 'value': '约半月后归家',
                       'scope': 'book', 'modality': 'expected'}],
                  'expression_refs': [], 'surface_introductions': []},
             ]}],
        'chapters': [{'purpose': '疑到证', 'scenes': 1}],
        'scene_weights': [1],
    }


def _assemble_design(brief: ReconstructionBrief, **over):
    return assemble_body_design(
        _design_wire(brief, **over), known_char_ids={'yun_niang', 'fu_jun'},
        expression_requirements=brief.expression,
        source_narration=brief.source_narration, brief=brief)


def _positions(body):
    return {s.scene_id: {b.beat_id for b in s.beats} for s in body.scenes}


#: 绿 bind wire：ob01 复合承载（supp b02→验收 b03）、ob02→b04、series 分别
#: b05/b06、kf→b07。
def _bind_wire() -> dict:
    return {'protocol_version': 'stage4-bind-v1', 'entries': [
        {'obligation_id': 'ob01', 'scene': 'sc01', 'beat': 'b03',
         'supporting': [{'scene': 'sc01', 'beat': 'b02'}]},
        {'obligation_id': 'ob02', 'scene': 'sc01', 'beat': 'b04',
         'supporting': []},
        {'obligation_id': 'ob03', 'scene': 'sc01', 'beat': 'b05',
         'supporting': []},
        {'obligation_id': 'ob04', 'scene': 'sc01', 'beat': 'b06',
         'supporting': []},
        {'obligation_id': 'ob05', 'scene': 'sc01', 'beat': 'b07',
         'supporting': []},
    ], 'unbound': []}


def _green_graph_and_binding():
    brief = _brief()
    body, errors = _assemble_design(brief)
    assert body is not None and not errors, errors
    binding, errors = assemble_binding(_bind_wire(), _checkpoint(), _positions(body))
    assert binding is not None and not errors, errors
    return body, binding, brief


# ───────────────────────── 案例登记与路由 ─────────────────────────

def test_case_registry_pins_a5_faces():
    """CASES_BY_BATCH['R10a-a5'] 覆盖 C3/C5/C6/P5 的红绿落点（防删例）。"""
    ids = {c.case_id for c in A5_CASES}
    assert {'C3', 'C5', 'C6', 'P5'} <= ids


def test_epistemic_lock_order_routes_to_body_design():
    action, target = route_owner_issue('epistemic_lock_order')
    assert (action, target) == ('revise_upstream', 'str06.stage4.body_design')


# ───────────────────────── project_binding_space ─────────────────────────

def test_binding_space_carries_series_and_lock_digest():
    """series（正式 ID＋mechanism）与按义务渲染的锁摘要进承载选择面；
    不带 brief 保持旧最小形状。"""
    body, binding, brief = _green_graph_and_binding()
    checkpoint = _checkpoint()
    space = project_binding_space(checkpoint, body, brief)
    assert len(space['series']) == 1
    assert space['series'][0]['members'] == ['ob03', 'ob04']
    assert space['series'][0]['mechanism'] == 'ex_02'
    assert space['series'][0]['note']
    by_id = {o['obligation_id']: o for o in space['obligations']}
    assert by_id['ob03']['series_group'] == by_id['ob04']['series_group']
    assert by_id['ob03']['series_mechanism'] == 'ex_02'
    assert len(by_id['ob01']['knowledge_locks']) == 1
    assert 'suspected' in by_id['ob01']['knowledge_locks'][0]
    assert 'known' in by_id['ob01']['knowledge_locks'][0]
    assert '夫君像是看穿了什么' in by_id['ob01']['knowledge_locks'][0]
    assert 'expected' in by_id['ob05']['fact_locks'][0]
    minimal = project_binding_space(checkpoint, body)
    assert all('knowledge_locks' not in o and 'fact_locks' not in o
               and 'series_group' not in o for o in minimal['obligations'])


# ───────────────────────── series 同拍（C3/C5） ─────────────────────────

def test_series_members_same_beat_rejected_and_split_passes():
    """C3/C5 红例：series 两成员绑同一拍 → series_collapsed；绿：分别承载。"""
    body, _binding, _brief = _green_graph_and_binding()
    checkpoint = _checkpoint()
    collapsed = deepcopy(_bind_wire())
    for entry in collapsed['entries']:
        if entry['obligation_id'] in ('ob03', 'ob04'):
            entry['beat'] = 'b05'
    binding, errors = assemble_binding(collapsed, checkpoint, _positions(body))
    assert binding is None
    assert any('series_collapsed' in e and 'ob03' in e and 'ob04' in e
               for e in errors), errors
    split, errors = assemble_binding(_bind_wire(), checkpoint, _positions(body))
    assert split is not None and not errors, errors


def test_unbound_series_member_does_not_collapse():
    """unbound 成员无承载位，不参与同拍比较（必保未承载另有回派）。"""
    body, _binding, _brief = _green_graph_and_binding()
    checkpoint = _checkpoint()
    wire = deepcopy(_bind_wire())
    wire['entries'] = [e for e in wire['entries']
                       if e['obligation_id'] != 'ob04']
    wire['unbound'] = [{'obligation_id': 'ob04', 'owner': 'design',
                        'reason': '变异：第二次现身暂无承载'}]
    binding, errors = assemble_binding(wire, checkpoint, _positions(body))
    assert binding is not None and not errors, errors


# ───────────────────────── epistemic_lock_order（C1/C6 位置面） ─────────────

def test_knowledge_upgrade_before_authorized_position_rejected():
    """C1 位置红例：known 升级（b03）早于授权义务承载位（ob01→b04 无支撑）
    → epistemic_lock_order，消息带升级/授权位置与源盲义务。"""
    body, _binding, brief = _green_graph_and_binding()
    checkpoint = _checkpoint()
    wire = deepcopy(_bind_wire())
    for entry in wire['entries']:
        if entry['obligation_id'] == 'ob01':
            entry['beat'] = 'b04'
            entry['supporting'] = []
    binding, errors = assemble_binding(wire, checkpoint, _positions(body))
    assert binding is not None and not errors, errors
    problems = check_epistemic_lock_order(body, binding)
    assert problems, '升级早于授权承载位必须红'
    assert any('epistemic_lock_order' in p and _kp_x(brief) in p
               and 'sc01#b03' in p and 'sc01#b04' in p for p in problems), problems


def test_reader_confirmation_before_authorized_position_rejected():
    """C6 位置红例：读者确认（b04）早于授权锁承载位（ob02→b05）。"""
    body, _binding, brief = _green_graph_and_binding()
    checkpoint = _checkpoint()
    wire = deepcopy(_bind_wire())
    for entry in wire['entries']:
        if entry['obligation_id'] == 'ob02':
            entry['beat'] = 'b05'
    binding, errors = assemble_binding(wire, checkpoint, _positions(body))
    assert binding is not None and not errors, errors
    problems = check_epistemic_lock_order(body, binding)
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
    binding, errors = assemble_binding(_bind_wire(), _checkpoint(), _positions(body))
    assert binding is not None and not errors, errors
    assert check_epistemic_lock_order(body, binding) == []


def test_free_key_and_unauthorized_mode_not_checked():
    """自由键（非 kp_*）的 knowledge set 不进位置判据；无必保授权的模式
    无位置分母（模式合法性归 a4）。"""
    body, _binding, brief = _green_graph_and_binding()
    wire = deepcopy(_bind_wire())
    # ob03/ob04 非必保：它们的承载拍（b05/b06）晚于 b04 的 reader 确认无关——
    # reader 的授权义务是 ob02（b04），绿基座已证。这里改一条自由键 set：
    body_wire_free = body.model_dump(mode='json')
    kp_x = _kp_x(brief)
    beat = body_wire_free['scenes'][0]['beats'][1]
    beat['target_delta'].append(
        {'domain': 'knowledge', 'subjects': [], 'holder': 'yun_niang',
         'key': 'free_guess', 'op': 'set', 'value': '自由键', 'scope': 'book',
         'knowledge_mode': 'known'})
    del beat
    from contracts.schema.design import BodyDesign
    mutated = BodyDesign.model_validate(body_wire_free)
    binding, errors = assemble_binding(wire, _checkpoint(), _positions(mutated))
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
        source_narration=brief.source_narration, brief=brief)
    assert body is not None and not errors, errors
    binding, errors = assemble_binding(_bind_wire(), _checkpoint(), _positions(body))
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
    wire = deepcopy(_bind_wire())
    for entry in wire['entries']:
        if entry['obligation_id'] == 'ob01':
            entry['beat'] = 'b04'
            entry['supporting'] = []
    bad_binding, errors = assemble_binding(wire, _checkpoint(), _positions(body))
    assert bad_binding is not None and not errors, errors
    with pytest.raises(OwnerRevisionRequired) as excinfo:
        _backfill_epistemic_lock_order(repo, body, bad_binding)
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


# ───────────────────────── run_bind 修订轮（stage，fake transport） ──────────

#: series 两成员同拍的坏 bind wire（looped 基座：ob01/ob02 都锚 b01_01.e1，
#: preserve wire 带 series [1,2]）。
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
    wires['bind'] = {'protocol_version': 'stage4-bind-v1', 'entries': [
        {'obligation_id': 'ob01', 'scene': 'sc01', 'beat': 'b02',
         'supporting': [{'scene': 'sc01', 'beat': 'b01'}]},
        {'obligation_id': 'ob02', 'scene': 'sc01', 'beat': 'b01',
         'supporting': []}], 'unbound': []}
    return wires


_BAD_BIND = {'protocol_version': 'stage4-bind-v1', 'entries': [
    {'obligation_id': 'ob01', 'scene': 'sc01', 'beat': 'b02',
     'supporting': [{'scene': 'sc01', 'beat': 'b01'}]},
    {'obligation_id': 'ob02', 'scene': 'sc01', 'beat': 'b02',
     'supporting': []}], 'unbound': []}


class _BindFaultTransport:
    """bind 原始请求回同拍坏 wire；修订轮（prompt 带〔上一版〕）粘性时
    仍回坏 wire（同指纹 → 无进展停），非粘性交给内层干净 wire。"""

    def __init__(self, wires, sticky=False):
        from tests.test_str06_body import _RoutingTransport
        self._inner = _RoutingTransport(wires)
        self._sticky = sticky

    @property
    def calls(self):
        return self._inner.calls

    def send(self, request):
        import json as _json
        from novel.llm.transport import ChatResponse
        if self._inner._owner(request) == 'bind' and (
                self._sticky or '上一版' not in request.user):
            self._inner.calls.append('bind')
            return ChatResponse(
                content=_json.dumps(_BAD_BIND, ensure_ascii=False),
                finish_reason='stop',
                usage={'prompt_tokens': 100, 'completion_tokens': 40},
                model='fake')
        return self._inner.send(request)


def _run_to_bind(tmp_path, transport_factory, monkeypatch):
    from contracts.schema.hook import HookConstraints
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
                         'str06.stage4.body_design', 'str06.stage4.bind'])
    return runner, repo, transport


def test_bind_acceptance_gate_runs_inside_revision_loop(tmp_path, monkeypatch):
    """R-5.5 改写：坏 wire（series 同拍）在修订轮内被重问——bind 恰两次调用，
    修订好转正、不写 INVALID。"""
    runner, repo, transport = _run_to_bind(
        tmp_path, lambda wires: _BindFaultTransport(wires), monkeypatch)
    runner()
    assert transport.calls.count('bind') == 2
    assert repo.path('stage4/grounding/semantic_binding.json').is_file()
    assert not repo.path('stage4/grounding/semantic_binding.INVALID.json').exists()


def test_bind_exhaustion_writes_invalid_twin_and_stops(tmp_path, monkeypatch):
    """粘性坏 wire：修订轮无进展 ValidationStop → `.INVALID` 孪生（wire 原样
    ＋问题清单）留证、正式位不写；bind 恰两次（原轮＋一轮修订即同指纹停）。"""
    from novel.llm.paid_requests import ValidationStop

    runner, repo, transport = _run_to_bind(
        tmp_path, lambda wires: _BindFaultTransport(wires, sticky=True),
        monkeypatch)
    with pytest.raises(ValidationStop) as excinfo:
        runner()
    assert excinfo.value.code == 'validation_no_progress'
    assert excinfo.value.adoption is not None, \
        'typed 停止须携带被拦稿的显式采用引用（INVALID 铸来源账）'
    assert transport.calls.count('bind') == 2
    invalid = repo.path('stage4/grounding/semantic_binding.INVALID.json')
    assert invalid.is_file(), '耗尽必须留 INVALID 孪生'
    import json as _json
    payload = _json.loads(invalid.read_text(encoding='utf-8'))
    assert payload['wire'] == _BAD_BIND
    assert any('series_collapsed' in e for e in payload['errors'])
    assert payload['_provenance']['gates_skipped'], '闸门问题清单留痕'
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


# ───────────────── R10c：positive_material_leak 机械归因回派 ─────────────────

def test_material_leak_attributes_book_first_and_preserve_same_pass(tmp_path):
    """R10c（2026-10-02，用户裁决 A）：泄漏字面所属人物的表面逐字出现于该章
    源文 → book（surface from_chapter 源保真错误）；全部表面缺席 → preserve
    （statement 混入后文名）。两组同批落账，抛最早执行序一组（book）。"""
    from novel.stages.chain import _backfill_material_leak
    from novel.stages.owner_revision import (
        OwnerRevisionRequired, read_owner_issue_file,
    )
    from novel.llm.ledger import CostLedger
    from novel.store import Str06Repository
    from tests.str06_facilities import make_context

    repo = Str06Repository(tmp_path / 'run')
    repo.attach_context(make_context(tmp_path / 'run', ledger=CostLedger(path=None)))
    from tests.str06_facilities import synthetic_ref
    repo.write('stage2/observations/book.json', {'characters': {}, 'product': 'book'},
               owners=('book',), refs=(synthetic_ref('book', '归因夹具'),))
    repo.write('runtime/preserve_checkpoint.json', {'obligations': []},
               owners=('preserve',), refs=(synthetic_ref('preserve', '归因夹具'),))
    chars = CharactersCore.model_validate({'characters': {
        'pei_shuyuan': {'canonical': '裴书远', 'surfaces': [
            {'surface': '裴书远', 'from_chapter': 2, 'source_refs': ['e01_02']},
            {'surface': '表弟', 'from_chapter': 4, 'source_refs': ['e01_04']}]},
        'shen_zhiyan': {'canonical': '沈知言', 'surfaces': [
            {'surface': '沈知言', 'from_chapter': 6, 'source_refs': ['e01_06']}]},
    }, 'initial_state': []})
    chapters = [{'chapter_ordinal': 1, 'text': '夫君谋划向他表弟借种。'},
                {'chapter_ordinal': 2, 'text': '表弟很快到来。裴书远来了。'}]
    leaks = [
        # ch01 源文无「裴书远」（只有表弟）⇒ statement 混名 → preserve。
        "positive_material_leak[ch01/must_realize/15@34]：未释放专名字面 '裴书远'",
        "positive_material_leak[ch01/must_realize/7@52]：未释放专名字面 '沈知言'",
        # ch02 源文逐字有「裴书远」⇒ withholding 记账晚 → book（姓氏族/表面）。
        "positive_material_leak[ch02/must_realize/2@9]：未释放专名字面 '裴书远'",
        # 姓氏族字面：不属任何 surface、源文该章未出现 ⇒ preserve（不静默跳过）。
        "positive_material_leak[ch01/book_style@3]：未释放专名字面 '苏'",
    ]
    with pytest.raises(OwnerRevisionRequired) as excinfo:
        _backfill_material_leak(repo, leaks, chars, chapters)
    # 最早执行序一组抛出：book（裴书远逐字在 ch02 源文 → withholding 记晚）。
    assert excinfo.value.owner == 'book'
    assert excinfo.value.node == 'str06.stage2.book'
    assert all(i.code == 'material_name_leak' for i in excinfo.value.issues)
    assert any('源保真' in i.message and '裴书远' in i.message
               for i in excinfo.value.issues)
    book_record = read_owner_issue_file(repo, 'book')
    assert book_record.status == 'open'
    assert book_record.raised_by == 'str06.stage4.compile'
    # 同 owner 多 item 合并为一次 discover（issue 文件按 owner 单记录整写；
    # 分开 discover 会把一轮逻辑发现记成多轮、顶满修订额度——R10c c1 实证）。
    assert book_record.rounds_total == 1
    assert {i.item for i in book_record.issues} == {'chapter:2'}
    # 同批落账：preserve 的 statement 混名记录已在案（含姓氏族字面「苏」）。
    preserve_record = read_owner_issue_file(repo, 'preserve')
    assert preserve_record.status == 'open'
    assert preserve_record.rounds_total == 1
    assert any('混名' in i.message for i in preserve_record.issues)
    assert any('苏' in i.message for i in preserve_record.issues)
    # 非人物面字面不归因：不落账、不抛（由调用方保持 typed 停止）。
    repo2 = Str06Repository(tmp_path / 'run2')
    repo2.attach_context(make_context(tmp_path / 'run2', ledger=CostLedger(path=None)))
    from tests.str06_facilities import synthetic_ref
    repo2.write('stage2/observations/book.json', {'characters': {}, 'product': 'book'},
                owners=('book',), refs=(synthetic_ref('book', '归因夹具'),))
    _backfill_material_leak(repo2, [
        "positive_material_leak[ch01/must_realize/1@2]：未释放专名字面 '无名之辈'"],
        chars, chapters)
    assert read_owner_issue_file(repo2, 'book') is None
    assert read_owner_issue_file(repo2, 'preserve') is None
