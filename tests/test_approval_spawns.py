#!/usr/bin/env python3
from __future__ import annotations

from novel.orchestrator import approvals


def test_gate_spawns_follow_the_same_disk_resume_verdict(monkeypatch, tmp_path):
    """FC-05: a fresh downstream artifact must not be offered for paid overwrite."""
    novel_dir = tmp_path / 'book_123'
    novel_dir.mkdir()
    sentinel = object()
    verdicts = {'stage4.plan': approvals.state.RESUME_SKIP}

    monkeypatch.setattr(
        approvals.paths, 'resolve_novel_dir', lambda _base, _sid: novel_dir)
    monkeypatch.setattr(approvals, '_planned', lambda _base, _sid: [1, 2, 3])
    monkeypatch.setattr(approvals.state, 'overview', lambda _base, _sid: sentinel)
    monkeypatch.setattr(
        approvals.state, 'resume_verdict',
        lambda overview, node: (verdicts.get(node, approvals.state.RESUME_RUN), node))

    fresh = approvals.get(tmp_path, '123')
    assert fresh.spawns['s2'] == [], 'fresh stage4.plan must not be spawned again'
    assert not (tmp_path / '_service').exists(), 'the dynamic GET remains read-only'

    verdicts['stage4.plan'] = approvals.state.RESUME_RUN
    missing = approvals.get(tmp_path, '123')
    assert [action.kind for action in missing.spawns['s2']] == ['stage4.plan']


def test_default_approval_value_is_not_mistaken_for_a_saved_human_choice(
        monkeypatch, tmp_path):
    novel_dir = tmp_path / 'book_123'
    novel_dir.mkdir()
    monkeypatch.setattr(
        approvals.paths, 'resolve_novel_dir', lambda _base, _sid: novel_dir)
    monkeypatch.setattr(approvals, '_planned', lambda _base, _sid: [1, 2, 3])
    monkeypatch.setattr(approvals.state, 'overview', lambda _base, _sid: object())
    monkeypatch.setattr(
        approvals.state, 'resume_verdict',
        lambda _overview, node: (approvals.state.RESUME_RUN, node))

    doc = approvals.get(tmp_path, '123')
    assert doc.deep == [1, 2, 3]
    assert not approvals.has_record(tmp_path, '123')

    # Even a physically persisted v2 record is not spending authority until it
    # carries current s3/s4 and depth-subject proofs.
    approvals._write(tmp_path, approvals._fresh_record('123', [1, 2, 3]))
    assert not approvals.has_record(tmp_path, '123')
