from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.schema.evaluation import GlobalReviewDecisionSet
from contracts.schema import decode_artifact
from novel.api import approval_stamp
from novel.api.routes import edits
from novel.errors import (
    ApprovalRequired, ArtifactUnreadable, BlockingFindingsUnresolved,
    GateBlocked, UpstreamMissing, UpstreamStale, UpstreamUnproven,
)
from novel.orchestrator import uploads


def _request() -> GlobalReviewDecisionSet:
    return GlobalReviewDecisionSet.model_validate({
        'actor': 'reviewer-a',
        'decisions': [{
            'item_id': (
                'alias_identity:characters.characters.char_001.aliases.alias_01:'
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


def _scope_request() -> GlobalReviewDecisionSet:
    return GlobalReviewDecisionSet.model_validate({
        'actor': 'reviewer-scope',
        'decisions': [],
        'blocking_scope': {
            'expected_source_sha256': 'a' * 64,
            'expected_blocking_scope_sha256': 'b' * 64,
            'expected_workpack_sha256': 'c' * 64,
            'expected_item_count': 12,
            'reviewed_chapter_ordinals': [1, 2, 3],
            'state': 'PASS',
            'reason': '已逐项通读当前工作包与所列全部正文章',
            'evidence': [],
        },
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


@pytest.mark.parametrize('closure_error', [
    ApprovalRequired('stage2 global review is not all PASS'),
    UpstreamMissing('stage2/chapter_blueprints.json is required'),
    ArtifactUnreadable('stage2/chapter_blueprints.json has a broken envelope'),
    GateBlocked(
        'stage2 blueprint has an unresolved invalid sibling',
        invalid_path='stage2/chapter_blueprints.INVALID.json'),
    UpstreamStale('stage2/chapter_blueprints.json consumed inputs changed'),
    UpstreamUnproven('stage2/chapter_blueprints.json provenance is unknown'),
    BlockingFindingsUnresolved(
        'stage2 joint HARD is not clear', rules=('synthetic_joint_hard',)),
], ids=lambda error: type(error).__name__)
def test_all_pass_review_lands_before_stage2_closure_without_rolling_back(
        monkeypatch, tmp_path, closure_error):
    """s2 proof 暂缓不得撤销已提交的逐项决定或伪装 HTTP 失败。"""
    decision_path = tmp_path / 'decision.json'
    landed = SimpleNamespace(exit_code=0, marker='already-landed-review')
    discarded: list[Path] = []

    monkeypatch.setattr(
        edits.uploads, 'save_global_review_decisions',
        lambda _base, _sid, _request: decision_path)
    monkeypatch.setattr(
        edits.uploads, 'discard_edit_plan',
        lambda _base, path: discarded.append(Path(path)))
    monkeypatch.setattr(edits.jobs, 'run', lambda *_args, **_kwargs: landed)

    def stage2_not_closed(*_args, **_kwargs):
        raise closure_error

    # `_stamp` 是 `approval_stamp.stamp_approval_after_landed` 的别名；
    # 守卫要 patch 唯一生产 helper 的真实依赖，不能要求 edits 重新导出旧 seam。
    monkeypatch.setattr(
        approval_stamp.approvals, 'approve_gate', stage2_not_closed)

    with pytest.warns(RuntimeWarning, match='人工决定已落盘.*s2'):
        result = edits.decide_global_review(
            _request(), sid='book-a', base='private')

    assert result is landed
    assert discarded == [decision_path]


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
    assert raw['_schema_version'] == 3
    decoded = decode_artifact('stage2/global_review_decisions.json', raw).payload
    assert GlobalReviewDecisionSet.model_validate(decoded) == decisions


def test_api_upload_preserves_the_compact_scope_binding(tmp_path):
    decisions = _scope_request()

    path = uploads.save_global_review_decisions(tmp_path, 'book-a', decisions)

    raw = json.loads(path.read_text(encoding='utf-8'))
    decoded = decode_artifact('stage2/global_review_decisions.json', raw).payload
    assert GlobalReviewDecisionSet.model_validate(decoded) == decisions
    assert decoded['blocking_scope']['expected_workpack_sha256'] == 'c' * 64
