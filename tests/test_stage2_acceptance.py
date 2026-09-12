#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""退役五轮工具仅核对历史报告，不能接收现役报告或产生当前验收许可。"""
from __future__ import annotations

from copy import deepcopy

from tools import stage2_acceptance as A


def _report(index: int, *, seconds: float | None = None) -> dict:
    wall = 1_500 + index if seconds is None else seconds
    run_id = f'clean-stage2-{index}'
    return {
        'schema': A.WORKFLOW_SCHEMA,
        'sid': 'book-sid',
        'run_id': run_id,
        'identity': {'experiment_valid': True},
        'stage2': {
            'scope': 'target_run',
            'expected_architecture': 'extraction-ir-v3.2',
            'architecture': 'extraction-ir-v3.2',
            'cold_cache': {'state': 'ok', 'hits': 0, 'unknown_rows': 0},
            'global_artifact': {
                'formal_present': True, 'current_schema': True,
                'invalid_present': False,
                'run_binding': {'state': 'ok'}},
            'blueprint_artifact': {
                'formal_present': True, 'current_schema': True,
                'invalid_present': False, 'chapters': 20,
                'run_binding': {'state': 'ok'}},
            'global_review': {'state': 'ok', 'generation_safe': True},
            'mechanical_check': {'state': 'ok', 'hard_hits': 0,
                                 'hard_skipped': 0},
            'blueprint_wire': {
                'errors': 0, 'outcome_unknown': 0, 'latency_state': 'ok',
                'models': {'executor-new': 20},
                'model_unknown_rows': 0,
                'reasoning_efforts': {'high': 20},
                'reasoning_effort_unknown_rows': 0,
                'chapter_latency_seconds': {'p50': 90, 'p95': 150},
                'first_call_landing': {'units': 20, 'first_call': 19}},
            'commander': {
                'calls': 2, 'errors': 0,
                'request_models': {'commander-new': 2},
                'request_model_unknown_rows': 0,
                'requested_efforts': {'high': 2},
                'reasoning_effort_unknown_rows': 0,
                'actual_models': {'commander-new': 2},
                'actual_model_unknown_rows': 0,
                'actual_efforts': {'high': 2},
                'actual_effort_unknown_rows': 0},
            'role_lifecycle': {
                'state': 'ok', 'sequence': 'ok',
                'commanders': {
                    node: {'content_used': 1, 'content_limit': 4}
                    for node in ('stage2.global', 'stage2.blueprint')
                }},
            'wall_clock': {'state': 'ok', 'seconds': wall},
        },
    }


def _manual(reports: list[dict]) -> dict:
    return {
        'schema': A.MANUAL_SCHEMA,
        'runs': [
            {'run_id': report['run_id'], 'decision': 'pass',
             'sampled_chapters': [1, 5, 10, 15, 20], 'reviewer': 'operator',
             'reason': '事实、因果、人物状态与证据定位抽查可接受'}
            for report in reports
        ],
    }


def test_five_clean_runs_can_close_all_three_claims_without_an_ab() -> None:
    reports = [_report(index) for index in range(5)]

    result = A.evaluate(
        reports, manual_quality=_manual(reports),
        baseline_seconds=3_044, minimum_speedup_percent=20)

    assert result['run_set_state'] == 'pass'
    assert result['stability']['state'] == 'pass'
    assert result['first_call_landing']['rate'] == 0.95
    assert result['speed']['target_state'] == 'pass'
    assert result['speed']['significant_state'] == 'pass'
    assert result['quality']['state'] == 'pass'
    assert result['claim_ready'] is True


def test_missing_manual_quality_stays_unver_instead_of_becoming_green() -> None:
    reports = [_report(index) for index in range(5)]
    manual = _manual(reports)
    manual['runs'].pop()

    result = A.evaluate(
        reports, manual_quality=manual,
        baseline_seconds=3_044, minimum_speedup_percent=20)

    assert result['quality']['state'] == 'unver'
    assert result['claim_ready'] is False


def test_profile_drift_and_one_slow_run_are_reported_separately() -> None:
    reports = [_report(index) for index in range(5)]
    reports[-1] = _report(4, seconds=1_801)
    drifted = deepcopy(reports[-1])
    drifted['stage2']['blueprint_wire']['models'] = {'executor-other': 20}
    reports[-1] = drifted

    result = A.evaluate(
        reports, manual_quality=_manual(reports),
        baseline_seconds=3_044, minimum_speedup_percent=20)

    assert result['stability']['state'] == 'fail'
    assert result['speed']['target_state'] == 'fail'
    assert result['quality']['state'] == 'pass'
    assert result['claim_ready'] is False


def test_missing_profile_identity_and_too_small_manual_sample_stay_unver() -> None:
    reports = [_report(index) for index in range(5)]
    reports[0]['stage2']['blueprint_wire']['model_unknown_rows'] = 1
    manual = _manual(reports)
    manual['runs'][0]['sampled_chapters'] = [1, 10, 20]

    result = A.evaluate(
        reports, manual_quality=manual,
        baseline_seconds=3_044, minimum_speedup_percent=20)

    assert result['stability']['state'] == 'unver'
    assert result['quality']['state'] == 'unver'
    assert result['quality']['insufficient_sample_run_ids'] == [
        reports[0]['run_id']]
    assert result['claim_ready'] is False


def test_stale_workflow_report_schema_cannot_enter_the_five_run_claim() -> None:
    reports = [_report(index) for index in range(5)]
    reports[0]['schema'] = 'novel-workflow-metrics-v0'

    result = A.evaluate(
        reports, manual_quality=_manual(reports),
        baseline_seconds=3_044, minimum_speedup_percent=20)

    assert result['stability']['state'] == 'fail'
    assert result['claim_ready'] is False


def test_current_workflow_report_cannot_enter_the_retired_five_run_acceptance() -> None:
    from tools.workflow_metrics import WORKFLOW_REPORT_SCHEMA

    reports = [_report(index) for index in range(5)]
    for report in reports:
        report['schema'] = WORKFLOW_REPORT_SCHEMA
    result = A.evaluate(
        reports, manual_quality=_manual(reports),
        baseline_seconds=3_044, minimum_speedup_percent=20)
    assert result['stability']['state'] == 'fail'
    assert result['claim_ready'] is False


def test_current_plot_architecture_cannot_be_relabelled_as_a_historical_report() -> None:
    from contracts.invariants import BLUEPRINT_ARCHITECTURE

    reports = [_report(index) for index in range(5)]
    for report in reports:
        report['stage2']['architecture'] = BLUEPRINT_ARCHITECTURE
        report['stage2']['expected_architecture'] = BLUEPRINT_ARCHITECTURE
    result = A.evaluate(reports, manual_quality=_manual(reports),
        baseline_seconds=3_044, minimum_speedup_percent=20)
    assert result['stability']['state'] == 'fail'
    assert all('expected_architecture_mismatch' in row['failures']
               for row in result['stability']['observations'])
    assert result['claim_ready'] is False


def test_each_commander_gets_four_content_calls_and_network_attempts_do_not_count() -> None:
    reports = [_report(index) for index in range(5)]
    reports[0]['stage2']['commander']['calls'] = 12
    budgets = reports[0]['stage2']['role_lifecycle']['commanders']
    budgets['stage2.global']['content_used'] = 4
    budgets['stage2.blueprint']['content_used'] = 4

    accepted = A.evaluate(
        reports, manual_quality=_manual(reports),
        baseline_seconds=3_044, minimum_speedup_percent=20)

    assert accepted['run_set_state'] == 'pass'
    budgets['stage2.blueprint']['content_used'] = 5
    refused = A.evaluate(
        reports, manual_quality=_manual(reports),
        baseline_seconds=3_044, minimum_speedup_percent=20)
    assert refused['run_set_state'] == 'pass'
    assert refused['stability']['state'] == 'fail'
    assert 'stage2.blueprint_content_budget_exceeded' in (
        refused['stability']['observations'][0]['failures'])
