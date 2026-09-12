"""现役报告不能借退役五轮解释器取得验收结论；历史算法不再单独维护。"""
from __future__ import annotations

from tools import stage2_acceptance as A


def _report(index: int) -> dict:
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
            'wall_clock': {'state': 'ok', 'seconds': 1_500 + index},
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


def test_current_workflow_report_cannot_enter_the_retired_five_run_acceptance() -> None:
    from tools.workflow_metrics import WORKFLOW_REPORT_SCHEMA

    reports = [_report(index) for index in range(5)]
    assert A.evaluate(reports, manual_quality=_manual(reports),
                      baseline_seconds=3_044, minimum_speedup_percent=20)['claim_ready'] is True
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
    assert A.evaluate(reports, manual_quality=_manual(reports),
                      baseline_seconds=3_044, minimum_speedup_percent=20)['claim_ready'] is True
    for report in reports:
        report['stage2']['architecture'] = BLUEPRINT_ARCHITECTURE
        report['stage2']['expected_architecture'] = BLUEPRINT_ARCHITECTURE
    result = A.evaluate(reports, manual_quality=_manual(reports),
        baseline_seconds=3_044, minimum_speedup_percent=20)
    assert result['stability']['state'] == 'fail'
    assert all('expected_architecture_mismatch' in row['failures']
               for row in result['stability']['observations'])
    assert result['claim_ready'] is False
