from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.schema.evaluation import GlobalReviewDecisionSet
from contracts.schema import decode_artifact
from novel.api.routes import edits
from novel.orchestrator import uploads


def _request() -> GlobalReviewDecisionSet:
    return GlobalReviewDecisionSet.model_validate({
        'actor': 'reviewer-a',
        'decisions': [{
            'item_id': (
                'knowledge_event:canon.secrets.secret_01.knowledge_events.0:'
                'aaaaaaaaaaaa'),
            'expected_subject_sha256': 'a' * 64,
            'state': 'PASS',
            'reason': '已经逐项对照原文与抽取集合',
            'evidence': [{
                'artifact': 'stage1/main_text.json#chapter/1',
                'subject_sha256': 'b' * 64,
                'start': 0,
                'end': 4,
            }],
        }],
    })


@pytest.mark.parametrize('exit_code,stamp_expected', [(0, True), (1, False), (2, False)])
def test_global_review_http_stamps_s2_only_after_all_pass(
    monkeypatch, tmp_path, exit_code, stamp_expected,
):
    decision_path = tmp_path / 'decision.json'
    calls: list[tuple] = []
    monkeypatch.setattr(
        edits.uploads, 'save_global_review_decisions',
        lambda base, sid, request: decision_path)
    monkeypatch.setattr(
        edits.uploads, 'discard_edit_plan',
        lambda base, path: calls.append(('discard', base, Path(path))))

    def run(base, node, sid, flags, **kwargs):
        calls.append(('run', base, node, sid, flags, kwargs))
        return SimpleNamespace(exit_code=exit_code)

    monkeypatch.setattr(edits.jobs, 'run', run)
    monkeypatch.setattr(
        edits, '_stamp', lambda base, sid, gate: calls.append(('stamp', base, sid, gate)))

    result = edits.decide_global_review(_request(), sid='book-a', base='private')
    assert result.exit_code == exit_code
    assert ('run', 'private', 'stage2.global', 'book-a',
            ['--review-decisions', str(decision_path)], {'follow_check': False}) in calls
    assert (('stamp', 'private', 'book-a', 's2') in calls) is stamp_expected
    assert ('discard', 'private', decision_path) in calls


def test_global_review_request_rejects_blank_self_reported_audit_fields():
    with pytest.raises(ValueError, match='actor/reason'):
        GlobalReviewDecisionSet.model_validate({
            'actor': ' ',
            'decisions': [{
                'item_id': 'row-a',
                'expected_subject_sha256': 'a' * 64,
                'state': 'PASS',
                'reason': ' ',
                'evidence': [],
            }],
        })


def test_api_upload_uses_the_same_versioned_decision_contract(tmp_path):
    decisions = _request()

    path = uploads.save_global_review_decisions(tmp_path, 'book-a', decisions)

    raw = json.loads(path.read_text(encoding='utf-8'))
    assert raw['_schema_version'] == 1
    decoded = decode_artifact('stage2/global_review_decisions.json', raw).payload
    assert GlobalReviewDecisionSet.model_validate(decoded) == decisions
