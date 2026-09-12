#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VAL-02：Stage4/6/7 每条 HARD 都有真实会红的最小反例。"""
from __future__ import annotations

import copy

import pytest

from contracts.schema.common import Finding
from tests.hard_cases import CASES

_STAGES = ('stage4', 'stage6', 'stage7')


def _hard_rules() -> dict[str, str]:
    from contracts.rules import REGISTRY
    return {row.rule: row.stage for row in REGISTRY.all()
            if row.sev == 'hard' and row.stage in _STAGES}


def test_every_hard_rule_of_stage4_6_7_has_a_counterexample():
    hard = _hard_rules()
    assert hard, '三层一条 HARD 都没有——判据表多半没被 import'
    assert not (missing := sorted(set(hard) - set(CASES))), (
        f'这些 HARD 没有登记反例：{missing}')
    assert not (stray := sorted(set(CASES) - set(hard))), (
        f'CASES 点名了不存在或非 HARD 的判据：{stray}')
    assert not (wrong := sorted(rule for rule, case in CASES.items()
                                if case.stage != hard[rule])), (
        f'这些样本登记的层与判据表不符：{wrong}')


@pytest.mark.parametrize('rule', sorted(CASES))
def test_each_counterexample_actually_reddens_its_target_rule(rule):
    findings = CASES[rule].build()
    assert findings, f'{rule} 的样本一条结论都没产出来'
    assert all(isinstance(finding, Finding) for finding in findings)
    got = {finding.rule for finding in findings}
    assert rule in got, f'{rule} 的样本只命中了 {sorted(got)}'


def _stage4_rules(plan: dict) -> set[str]:
    from tests.hard_cases import _find4
    return {finding.rule for finding in _find4(plan)}


@pytest.mark.parametrize('damage', [
    'missing_node', 'duplicate_placement', 'duplicate_delivery', 'foreign_node', 'foreign_scene',
    'early_context', 'missing_evidence', 'duplicate_evidence', 'foreign_evidence',
    'missing_stage', 'foreign_stage', 'missing_state_endpoint', 'wrong_state_endpoint',
    'missing_constraint_endpoint', 'wrong_constraint_endpoint', 'same_scene_violation',
])
def test_presentation_hazards_reject_at_the_production_guard(damage):
    """旧事实/材料/状态反例迁入完整节点契约，各分支仍经过同一正式判据。"""
    from tests.hard_cases import _clean_plan4, _find4, _BPS4
    plan = copy.deepcopy(_clean_plan4())
    blueprint = copy.deepcopy(_BPS4)
    chapter = plan['output_chapters'][0]
    placements = chapter['beat_placements']
    first = placements[0]['deliveries'][0]
    if damage == 'missing_node':
        placements.pop()
    elif damage == 'duplicate_placement':
        placements.append(copy.deepcopy(placements[0]))
    elif damage == 'duplicate_delivery':
        placements[0]['deliveries'].append(copy.deepcopy(first))
    elif damage == 'foreign_node':
        placements[0]['beat_ref'] = 'b02_01'
    elif damage == 'foreign_scene':
        first['scene_ref'] = 's99'
    elif damage == 'early_context':
        chapter['scenes'][0]['context_beat_refs'] = ['b01_03']
    elif damage == 'missing_evidence':
        first['evidence_refs'] = []
    elif damage == 'duplicate_evidence':
        first['evidence_refs'] *= 2
    elif damage == 'foreign_evidence':
        first['evidence_refs'] = ['e02_01']
    elif damage == 'missing_stage':
        first['source_context_refs'] = []
    elif damage == 'foreign_stage':
        first['source_context_refs'] = placements[1]['deliveries'][0]['source_context_refs']
    elif damage == 'same_scene_violation':
        blueprint['blueprints'][0]['constraints'][0]['kind'] = 'same_scene'
    else:
        last = placements[-1]['deliveries'][0]
        last['presentation_instruction'] = '完成决定留下的呈现'
        earlier = copy.deepcopy(last)
        earlier.update(scene_ref='s02', presentation_instruction='先铺垫决定的犹豫')
        placements[-1]['deliveries'].insert(0, earlier)
        state = {'kind': 'state', 'ref': 'st01_01', 'endpoint': 'established', 'scene_ref': 's03'}
        constraint = {'kind': 'constraint', 'ref': 'c01_01', 'endpoint': 'after', 'scene_ref': 's03'}
        chapter['anchor_bindings'] = [state, constraint]
        if damage == 'missing_state_endpoint':
            chapter['anchor_bindings'].remove(state)
        elif damage == 'wrong_state_endpoint':
            state['scene_ref'] = 's01'
        elif damage == 'missing_constraint_endpoint':
            chapter['anchor_bindings'].remove(constraint)
        elif damage == 'wrong_constraint_endpoint':
            constraint['scene_ref'] = 's01'
        else:
            raise AssertionError(damage)
    assert 'placement_ref' in {row.rule for row in _find4(plan, blueprints=blueprint)}
    assert 'placement_ref' not in _stage4_rules(_clean_plan4())


@pytest.mark.parametrize('damage', [
    'missing_body', 'changed_body', 'missing_constraint', 'changed_stage', 'missing_state',
    'changed_state_permission',
])
def test_context_does_not_drop_or_rewrite_plot_material(damage):
    from tests.hard_cases import _clean_plan4, _ctx4, _find4
    plan = _clean_plan4()
    ctx = _ctx4(plan)
    chapter = ctx['chapters'][0]
    if damage == 'missing_body':
        chapter['beats'].pop()
    elif damage == 'changed_body':
        chapter['beats'][0]['content'] = '邀请来自另一个人。'
    elif damage == 'missing_constraint':
        chapter['constraints'] = []
    elif damage == 'changed_stage':
        chapter['scenes'][0]['deliveries'][0]['stages'][0]['place'] = '错误地点'
    elif damage == 'missing_state':
        chapter['states'] = []
    else:
        chapter['scenes'][-1]['state_events'] = []
    rules = {row.rule for row in _find4(plan, ctx=ctx)}
    assert 'ctx_plot_exact' in rules
    if damage == 'changed_state_permission':
        assert 'ctx_permission_exact' in rules
    assert not {row.rule for row in _find4(plan) if row.sev == 'hard'}


def test_shared_evidence_and_ordinary_split_are_legal_without_full_background_release():
    from tests.hard_cases import _clean_plan4, _find4
    plan = _clean_plan4()
    chapter = plan['output_chapters'][0]
    target = chapter['beat_placements'][-1]
    target['deliveries'][0]['presentation_instruction'] = '完成决定留下的呈现'
    earlier = copy.deepcopy(target['deliveries'][0])
    earlier.update(scene_ref='s02', presentation_instruction='先铺垫决定的犹豫')
    target['deliveries'].insert(0, earlier)
    chapter['anchor_bindings'] = [
        {'kind': 'state', 'ref': 'st01_01', 'endpoint': 'established', 'scene_ref': 's03'},
        {'kind': 'constraint', 'ref': 'c01_01', 'endpoint': 'after', 'scene_ref': 's03'},
    ]
    assert 'placement_ref' not in {row.rule for row in _find4(plan)}
    chapter['scenes'][1]['context_beat_refs'] = ['b01_03']
    assert 'placement_ref' in {row.rule for row in _find4(plan)}
