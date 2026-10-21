"""每条现役故事 HARD 实际执行红绿对照，不扫描源码冒充覆盖。"""
from __future__ import annotations

import pytest

from contracts.rules import REGISTRY
from tests.hard_cases import CASES, _source_schema


def test_every_current_hard_rule_has_executable_red_and_green_case():
    hard = {row.rule: row.stage for row in REGISTRY.all()
            if row.sev == 'hard' and row.stage in ('stage2', 'stage4', 'stage5', 'stage7')}
    assert hard
    assert set(CASES) == set(hard)
    assert {rule: case.stage for rule, case in CASES.items()} == hard


@pytest.mark.parametrize('rule', sorted(CASES))
def test_production_hard_rule_red_and_green(rule, tmp_path):
    case = CASES[rule]
    assert case.check(True, tmp_path / 'green') == [], f'{rule} 的合法底座失败'
    findings = case.check(False, tmp_path / 'red')
    assert findings, f'{rule} 的反例未产出生产结论'
    assert all(isinstance(finding, str) and finding.strip() for finding in findings)
    assert any(case.expect in finding for finding in findings), (
        f'{rule} 没命中 {case.expect!r}，实际结论：{findings}')


@pytest.mark.parametrize('change,expected', [
    (lambda value: value.update(contract_version='stage2-observation-v17'), 'contract_version'),
    (lambda value: value.update(events={}), 'events'),
    (lambda value: value.update(unknown_field=True), 'unknown_field'),
])
def test_source_old_version_empty_and_unknown_fields_are_not_valid_empty_values(change, expected, tmp_path):
    findings = _source_schema(False, tmp_path, change=change)
    assert findings and any(expected in finding for finding in findings)


def test_unknown_world_truth_cannot_be_promoted_to_reader_confirmation(tmp_path):
    def change(value):
        prop = value['exposures'][0]['proposition_ref']
        value['propositions'][prop]['world_status'] = 'undetermined'
        value['exposures'][0]['mode'] = 'confirmed'
    findings = _source_schema(False, tmp_path, change=change)
    assert findings and any('confirmed' in finding or '确认' in finding for finding in findings)


def test_evidence_text_tamper_is_rejected_by_current_source_index():
    from tests.test_source_story import _compiled
    from novel.stages.stage2.story import validate_source_story
    index, _, _, story = _compiled()
    key, evidence = next(iter(story.evidence.items()))
    story.evidence[key] = evidence.model_copy(update={'text': evidence.text + '伪造字节'})
    issues = validate_source_story(story, index=index)
    assert any(issue.code == 'evidence_changed' for issue in issues)
