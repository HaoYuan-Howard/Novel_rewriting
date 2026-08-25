#!/usr/bin/env python3
from __future__ import annotations

from novel.orchestrator import approvals


def test_gate_spawns_follow_the_same_disk_resume_verdict(monkeypatch, tmp_path):
    """FC-05: a fresh downstream artifact must not be offered for paid overwrite."""
    novel_dir = tmp_path / 'book_123'
    novel_dir.mkdir()
    sentinel = object()
    verdicts = {
        'stage4.design': approvals.state.RESUME_SKIP,
        'stage4.plan': approvals.state.RESUME_SKIP,
    }

    monkeypatch.setattr(
        approvals.paths, 'resolve_novel_dir', lambda _base, _sid: novel_dir)
    monkeypatch.setattr(approvals, '_planned', lambda _base, _sid: [1, 2, 3])
    monkeypatch.setattr(approvals.state, 'overview', lambda _base, _sid: sentinel)
    monkeypatch.setattr(
        approvals.state, 'resume_verdict',
        lambda overview, node: (verdicts.get(node, approvals.state.RESUME_RUN), node))

    fresh = approvals.get(tmp_path, '123')
    assert fresh.spawns['s2'] == [], 'fresh Stage4 A/B must not be spawned again'
    assert not (tmp_path / '_service').exists(), 'the dynamic GET remains read-only'

    verdicts['stage4.plan'] = approvals.state.RESUME_RUN
    missing = approvals.get(tmp_path, '123')
    assert [action.kind for action in missing.spawns['s2']] == ['stage4.plan']

    verdicts['stage4.design'] = approvals.state.RESUME_RUN
    both = approvals.get(tmp_path, '123')
    assert [action.kind for action in both.spawns['s2']] == ['stage4.design'], (
        'A/B 必须各自确认；A 未完成时不能把两次付费调用塞进一个 GateBar 动作')


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
    assert doc.v == 3 and not doc.writing
    assert 'deep' not in type(doc).model_fields, 'public v3 不得重新暴露已退役的双臂决定'
    assert not approvals.has_record(tmp_path, '123')

    # 盘上即使已有一份 v4 记录，writing 没有当前 subject proof 仍不是花钱授权。
    approvals._write(tmp_path, approvals._fresh_record('123', [1, 2, 3]))
    assert not approvals.has_record(tmp_path, '123')
