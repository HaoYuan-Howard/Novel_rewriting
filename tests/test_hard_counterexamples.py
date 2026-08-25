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


def test_fact_coverage_rejects_both_missing_and_duplicate_atoms():
    """事实守恒的两支都要见过红灯；它不再以 beat 完整性代替事实完整性。"""
    from tests.hard_cases import _clean_plan4

    missing = copy.deepcopy(_clean_plan4())
    missing['output_chapters'][0]['scenes'][0]['land_fact_refs'] = []
    assert 'fact_coverage' in _stage4_rules(missing)

    duplicate = copy.deepcopy(_clean_plan4())
    duplicate['output_chapters'][0]['scenes'][1]['land_fact_refs'].append('f01_001')
    assert 'fact_coverage' in _stage4_rules(duplicate)

    assert 'fact_coverage' not in _stage4_rules(_clean_plan4())


def test_material_coverage_rejects_both_missing_and_duplicate_hinges():
    from tests.hard_cases import _clean_plan4

    missing = copy.deepcopy(_clean_plan4())
    for scene in missing['output_chapters'][0]['scenes']:
        scene['material_refs'] = []
    assert 'material_coverage' in _stage4_rules(missing)

    duplicate = copy.deepcopy(_clean_plan4())
    target = next(scene for scene in duplicate['output_chapters'][0]['scenes']
                  if scene['material_refs'])
    target['material_refs'].append(target['material_refs'][0])
    assert 'material_coverage' in _stage4_rules(duplicate)


def test_scene_state_chain_rejects_an_unconsumed_previous_exit_state():
    from tests.hard_cases import _clean_plan4

    plan = copy.deepcopy(_clean_plan4())
    scenes = plan['output_chapters'][0]['scenes']
    scenes[0]['exit_state_refs'] = ['f01_001']
    scenes[1]['entry_state_refs'] = []
    assert 'state_closure' in _stage4_rules(plan)
