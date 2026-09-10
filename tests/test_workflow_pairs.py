"""九组配对分母、冻结配置和具名质量收据的反例。"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from tools import workflow_pairs as P
from tools.workflow_metrics import WORKFLOW_REPORT_SCHEMA


def _manifest(tmp_path, *, scenario='local_rework'):
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    books = []
    for book in range(3):
        sid = str(1000 + book)
        pairs = []
        for repeat in range(1, 4):
            arms = {}
            for label, count in [('baseline', 1000), ('candidate', 600)]:
                index = (label == 'candidate') if repeat % 2 else (label == 'baseline')
                began = start + timedelta(days=repeat, hours=int(index) * 2)
                ended = began + timedelta(hours=1)
                run_id = f'{label}-{repeat}'
                code = label + '-clean'
                report = {
                    'schema': WORKFLOW_REPORT_SCHEMA, 'sid': sid, 'run_id': run_id,
                    'identity': {'experiment_valid': True, 'code_identities': [code]},
                    'performance': {'ledger_state': 'ok', 'stages': {stage: {
                        'wire': {'total_tokens': count}, 'observed_active_seconds': count / 10,
                    } for stage in ('stage2', 'stage4', 'stage5')}},
                }
                path = tmp_path / f'{sid}-{run_id}.json'
                raw = json.dumps(report).encode()
                path.write_bytes(raw)
                sha = hashlib.sha256(raw).hexdigest()
                freeze = P.Freeze(code=code, source_sha256='a' * 64, model_effort_sha256='b' * 64,
                    non_target_concurrency_sha256='c' * 64, body_target_sha256='d' * 64,
                    quality_samples_sha256='e' * 64, target_concurrency=4,
                    output_namespace_sha256=hashlib.sha256(f'{sid}-{run_id}'.encode()).hexdigest())
                arms[label] = {'run_id': run_id, 'report_path': path.name, 'report_sha256': sha,
                    'started_at': began.isoformat(), 'finished_at': ended.isoformat(),
                    'freeze': freeze.model_dump(), 'review': {
                        'reviewer_kind': 'human', 'actor': '实验审阅者',
                        'reason': '固定样例逐项对照，配置与两臂交付均已核对',
                        'reviewed_at': (ended + timedelta(minutes=1)).isoformat(),
                        'report_sha256': sha, 'freeze_sha256': P.freeze_sha256(freeze),
                        'output_sha256': 'f' * 64, 'configuration': 'verified',
                        'completion': 'complete', 'quality': {key: 'not_worse' for key in P.QUALITY_DIMENSIONS},
                    }}
            pairs.append({'repeat': repeat, **arms})
        books.append({'sid': sid, 'pairs': pairs})
    return {'schema': P.SCHEMA, 'registered_at': start.isoformat(),
            'scenario': scenario, 'target_node': None, 'books': books}


def _compare(tmp_path, raw):
    return P.compare(P.Manifest.model_validate(raw), tmp_path)


def test_nine_independent_pairs_report_books_before_pooled_median(tmp_path):
    result = _compare(tmp_path, _manifest(tmp_path))
    assert len(result['books']) == 3
    assert all(len(book['pairs']) == 3 for book in result['books'])
    stage5 = result['summary']['stage5.tokens']
    assert stage5 == {'registered_pairs': 9, 'eligible_pairs': 9, 'unknown_or_ineligible_pairs': 0,
                     'median_reduction': .4, 'target_reduction': .2, 'target_met': True}
    rendered = P.render(result)
    assert rendered.index('1000') < rendered.index('三书汇总')
    assert '具名人工实验收据' in rendered


@pytest.mark.parametrize('failure', ['missing', 'tampered', 'unknown_usage', 'incomplete',
                                     'quality', 'receipt', 'source', 'models', 'order', 'dirty'])
def test_failed_or_unknown_arm_cannot_disappear_or_become_savings(tmp_path, failure):
    raw = _manifest(tmp_path)
    pair = raw['books'][0]['pairs'][0]
    arm = pair['candidate']
    if failure in {'missing', 'tampered', 'unknown_usage'}:
        path = tmp_path / arm['report_path']
        if failure == 'missing':
            path.unlink()
        elif failure == 'tampered':
            path.write_text('{}', encoding='utf-8')
        else:
            report = json.loads(path.read_bytes())
            report['performance']['stages']['stage5']['wire']['total_tokens'] = None
            payload = json.dumps(report).encode()
            path.write_bytes(payload)
            arm['report_sha256'] = arm['review']['report_sha256'] = hashlib.sha256(payload).hexdigest()
    elif failure == 'incomplete':
        arm['review']['completion'] = 'incomplete'
    elif failure == 'quality':
        arm['review']['quality']['disclosure'] = 'worse'
    elif failure == 'receipt':
        arm['review'] = None
    elif failure == 'source':
        arm['freeze']['source_sha256'] = '0' * 64
    elif failure == 'models':
        arm['freeze']['model_effort_sha256'] = '0' * 64
    elif failure == 'order':
        arm['started_at'] = pair['baseline']['started_at']
    elif failure == 'dirty':
        arm['freeze']['code'] += '-dirty'
        with pytest.raises(ValidationError, match='干净代码'):
            _compare(tmp_path, raw)
        return
    result = _compare(tmp_path, raw)
    metric = result['summary']['stage5.tokens']
    assert metric['registered_pairs'] == 9
    invalid = 9 if failure == 'models' else 3 if failure == 'source' else 1
    assert metric['eligible_pairs'] == 9 - invalid
    assert metric['unknown_or_ineligible_pairs'] == invalid
    assert metric['median_reduction'] is None and metric['target_met'] is None
    assert result['books'][0]['pairs'][0]['metrics']['stage5.tokens']['errors']


def test_incomplete_manifest_and_duplicate_runs_are_refused(tmp_path):
    raw = _manifest(tmp_path)
    duplicate = copy.deepcopy(raw)
    duplicate['books'][0]['pairs'][1]['baseline']['run_id'] = 'baseline-1'
    with pytest.raises(ValidationError, match='独立配对'):
        P.Manifest.model_validate(duplicate)
    raw['books'][0]['pairs'].pop()
    with pytest.raises(ValidationError):
        P.Manifest.model_validate(raw)


def test_fault_recovery_never_claims_first_run_or_rework_savings(tmp_path):
    raw = _manifest(tmp_path, scenario='fault_recovery')
    result = _compare(tmp_path, raw)
    row = result['books'][0]['pairs'][0]['metrics']['stage5.tokens']
    assert row['observed_reduction'] == .4
    assert row['credited_reduction'] is None and row['target_met'] is None


def test_unassigned_content_rounds_cannot_make_first_input_look_free():
    trail = ('performance', 'stages', 'stage5', 'role_content_phases', 'executor', 'first', 'prompt_tokens')
    def report(tokens, unknown):
        return {'performance': {'stages': {'stage5': {
            'role_content_phases': {'executor': {'first': {'prompt_tokens': tokens}}},
            'attempt_link_unknown': unknown, 'work_unit_errors': [],
        }}}}
    row = P._metric(report(100, 0), report(0, 1), trail, .2, [])
    assert row['observed_reduction'] == 1
    assert row['credited_reduction'] is None and row['target_met'] is None
    assert 'candidate_content_phase_unproven' in row['errors']


def test_zero_baseline_increase_is_visible_and_never_credited():
    row = P._metric({'failures': 0}, {'failures': 1}, ('failures',), 0, [])
    assert row['absolute_change'] == 1
    assert row['observed_reduction'] is None
    assert row['credited_reduction'] is None and row['target_met'] is None


def test_json_cli_reports_failure_without_dropping_pair_or_exposing_paths(tmp_path, capsys):
    raw = _manifest(tmp_path)
    raw['books'][0]['pairs'][0]['candidate']['review']['completion'] = 'failed'
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert P.main(['--manifest', str(path), '--json']) == 0
    output = capsys.readouterr().out
    assert str(tmp_path) not in output and 'report_path' not in output
    result = json.loads(output)
    assert result['summary']['stage5.tokens']['registered_pairs'] == 9
    assert result['summary']['stage5.tokens']['target_met'] is None
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}
