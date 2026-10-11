# -*- coding: utf-8 -*-
"""service run/scratch 不污染书目录，且清理始终精确有界。"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from contracts.schema.orchestration import ProgressEvent
from novel import paths
from novel.errors import PathRefused
from novel.orchestrator import admission, beacon, service_lifecycle


def test_scratch_session_finishes_only_the_exact_session(tmp_path):
    first = service_lifecycle.open_scratch_session(tmp_path, '42', 'session-a')
    second = service_lifecycle.open_scratch_session(tmp_path, '42', 'session-b')
    (first / 'permit.json').write_text('{}', encoding='utf-8')
    (second / 'next.json').write_text('{}', encoding='utf-8')

    assert service_lifecycle.finish_scratch_session(
        tmp_path, '42', 'session-a') is True
    assert not first.exists()
    assert second.is_dir()
    assert service_lifecycle.finish_scratch_session(
        tmp_path, '42', 'session-a') is False


def test_scratch_json_once_is_confined_and_never_overwrites(tmp_path):
    root = tmp_path / 'scratch'
    root.mkdir()
    target = root / 'request.json'

    service_lifecycle.write_scratch_json_once(
        root, target, {'schema': 'test', 'value': 1})

    assert json.loads(target.read_text(encoding='utf-8')) == {
        'schema': 'test', 'value': 1}
    with pytest.raises(FileExistsError):
        service_lifecycle.write_scratch_json_once(
            root, target, {'schema': 'test', 'value': 2})
    with pytest.raises(PathRefused):
        service_lifecycle.write_scratch_json_once(
            root, tmp_path / 'outside.json', {'schema': 'test'})


def test_scratch_ttl_is_explicit_shallow_and_bounded(tmp_path):
    old = service_lifecycle.open_scratch_session(tmp_path, '42', 'old')
    fresh = service_lifecycle.open_scratch_session(tmp_path, '42', 'fresh')
    os.utime(old, (100.0, 100.0))
    os.utime(fresh, (950.0, 950.0))

    result = service_lifecycle.sweep_expired_scratch(
        tmp_path, ttl_seconds=100, now=1000.0, max_sessions=2)

    assert result.examined == 2
    assert result.removed == ('42/old',)
    assert not old.exists() and fresh.is_dir()
    with pytest.raises(ValueError, match='max_sessions'):
        service_lifecycle.sweep_expired_scratch(
            tmp_path, ttl_seconds=1, now=2000.0, max_sessions=0)


def test_scratch_ttl_never_removes_a_sid_with_unresolved_admission(tmp_path):
    old = service_lifecycle.open_scratch_session(tmp_path, '42', 'epoch-a')
    (old / 'execution-token.json').write_text('{}', encoding='utf-8')
    os.utime(old, (100.0, 100.0))
    admission.open_gate(tmp_path, 'parent-a', '42')

    protected = service_lifecycle.sweep_expired_scratch(
        tmp_path, ttl_seconds=100, now=1000.0)

    assert protected.removed == ()
    assert old.is_dir(), '未闭合 admission 的长节点 scratch 被 mtime TTL 删掉'
    admission.close_gate(tmp_path, 'parent-a')
    admission.mark_drained(tmp_path, 'parent-a')
    swept = service_lifecycle.sweep_expired_scratch(
        tmp_path, ttl_seconds=100, now=1000.0)
    assert swept.removed == ('42/epoch-a',)


def test_scratch_ttl_never_removes_an_orphan_open_beacon(tmp_path):
    old = service_lifecycle.open_scratch_session(tmp_path, '42', 'epoch-b')
    os.utime(old, (100.0, 100.0))
    beacon.append(tmp_path, ProgressEvent(
        ts=100, sid='42', run_id='orphan-parent', execution_id='execution-a',
        node='stage4.design', phase='start'))

    protected = service_lifecycle.sweep_expired_scratch(
        tmp_path, ttl_seconds=100, now=1000.0)

    assert protected.removed == () and old.is_dir()
    beacon.append(tmp_path, ProgressEvent(
        ts=101, sid='42', run_id='orphan-parent', execution_id='execution-a',
        node='stage4.design', phase='end', exit_code=0))
    swept = service_lifecycle.sweep_expired_scratch(
        tmp_path, ttl_seconds=100, now=1000.0)
    assert swept.removed == ('42/epoch-b',)


def test_retention_defaults_disable_all_destructive_categories(tmp_path):
    policy = service_lifecycle.RetentionPolicy()
    plan = service_lifecycle.plan_retention(tmp_path, policy, now=1000)

    assert policy.to_dict() == {
        'log_ttl_seconds': None,
        'telemetry_ttl_seconds': None,
        'trash_ttl_seconds': None,
        'checkpoint_ttl_seconds': None,
        'max_entries': 512,
    }
    assert plan.examined == 0 and plan.candidates == ()


def test_tree_identity_refuses_to_scan_beyond_the_explicit_bound(tmp_path):
    root = tmp_path / 'tree'
    root.mkdir()
    (root / 'one').write_text('1', encoding='utf-8')
    (root / 'two').write_text('2', encoding='utf-8')

    with pytest.raises(RuntimeError, match='max_entries=1'):
        service_lifecycle.tree_identity(root, max_entries=1)


def test_retention_resumes_after_first_unlink_before_item_commit(
        tmp_path, monkeypatch):
    logs = paths.service_dir(tmp_path) / 'logs'
    logs.mkdir(parents=True)
    first = logs / 'job-a.log'
    second = logs / 'job-b.log'
    first.write_text('a', encoding='utf-8')
    second.write_text('b', encoding='utf-8')
    policy = service_lifecycle.RetentionPolicy(log_ttl_seconds=1)
    candidates = tuple(service_lifecycle._candidate(
        tmp_path, kind='log', sid='42', path=target,
        evidence={'owner_state': 'ended'}, max_entries=policy.max_entries)
        for target in (first, second))
    plan = service_lifecycle.RetentionPlan.create(
        generated_at=1000, policy=policy, examined=2,
        candidates=candidates)
    monkeypatch.setattr(
        service_lifecycle, '_ended_log_owners',
        lambda _base: {'job-a.log': '42', 'job-b.log': '42'})

    original_unlink = Path.unlink
    interrupted = False

    def unlink_then_interrupt(path, *args, **kwargs):
        nonlocal interrupted
        result = original_unlink(path, *args, **kwargs)
        if path == first and not interrupted:
            interrupted = True
            raise RuntimeError('fault after unlink')
        return result

    monkeypatch.setattr(Path, 'unlink', unlink_then_interrupt)
    with pytest.raises(RuntimeError, match='fault after unlink'):
        service_lifecycle.apply_retention(tmp_path, plan)

    assert not first.exists() and second.is_file()
    audit = (
        paths.service_dir(tmp_path) / 'lifecycle_receipts'
        / f'{plan.plan_token}.json')
    first_item = audit.parent / f'{plan.plan_token}.items' / '0000.json'
    assert json.loads(audit.read_text(encoding='utf-8'))['state'] == 'prepared'
    assert json.loads(first_item.read_text(encoding='utf-8'))['state'] == 'prepared'

    monkeypatch.setattr(Path, 'unlink', original_unlink)
    result = service_lifecycle.apply_retention(tmp_path, plan)

    assert result.removed == (
        first.relative_to(tmp_path).as_posix(),
        second.relative_to(tmp_path).as_posix())
    assert result.receipts == ()
    assert not second.exists()
    assert json.loads(first_item.read_text(encoding='utf-8'))['state'] == 'committed'
    assert json.loads(audit.read_text(encoding='utf-8'))['state'] == 'committed'
    assert service_lifecycle.apply_retention(tmp_path, plan) == result
