#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""人工动作账：稳定身份、同进程并发与坏尾关闭式处理。"""
from __future__ import annotations

import json
import threading

import pytest
from pydantic import ValidationError

from novel import human_actions
from novel.errors import ProvenanceRefused


def _kwargs() -> dict:
    return {
        'sid': '人工动作_001',
        'output_ordinal': 1,
        'actor': 'editor',
        'reason': '逐项核对当前预览后改稿',
        'confirmation': 'working_draft',
        'current_chapter_sha256': 'a' * 64,
        'preview_subject_sha256': 'b' * 64,
        'result_chapter_sha256': 'c' * 64,
        'introduced_hard_rules': ['meta_leak'],
        'destination_rel': 'stage5/ch01.json',
        'run_id': 'run-human-edit-1',
        'code': 'test-human-actions',
    }


def test_same_human_action_is_idempotent_and_reads_back(tmp_path):
    first = human_actions.append_edit(tmp_path, **_kwargs())
    second = human_actions.append_edit(tmp_path, **_kwargs())

    assert second == first
    assert human_actions.read_events(tmp_path) == [first]
    lines = human_actions.actions_path(tmp_path).read_text(
        encoding='utf-8').splitlines()
    assert len(lines) == 2


def test_two_threads_share_the_same_per_path_dedup_transaction(tmp_path):
    barrier = threading.Barrier(3)
    results = []
    failures = []

    def append() -> None:
        try:
            barrier.wait()
            results.append(human_actions.append_edit(tmp_path, **_kwargs()))
        except BaseException as exc:  # noqa: BLE001 - thread errors must reach assertion
            failures.append(exc)

    threads = [threading.Thread(target=append) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=10)

    assert not failures
    assert len(results) == 2 and results[0].action_id == results[1].action_id
    assert len(human_actions.read_events(tmp_path)) == 1


def test_committed_request_cannot_land_a_second_result(tmp_path):
    human_actions.append_edit(tmp_path, **_kwargs())
    changed = {**_kwargs(), 'result_chapter_sha256': 'd' * 64}

    with pytest.raises(ProvenanceRefused, match='另一份结果'):
        human_actions.append_edit(tmp_path, **changed)

    assert len(human_actions.read_events(tmp_path)) == 1


@pytest.mark.parametrize(('confirmation', 'introduced'), [
    ('formal', ['meta_leak']),
    ('working_draft', []),
])
def test_confirmation_and_introduced_hard_rules_must_agree(
        confirmation, introduced):
    kwargs = {
        **_kwargs(),
        'confirmation': confirmation,
        'introduced_hard_rules': introduced,
    }
    with pytest.raises(ValidationError, match='introduced HARD'):
        human_actions._event(phase='prepared', **kwargs)


def test_truncated_tail_is_quarantined_before_a_following_append(tmp_path):
    path = human_actions.actions_path(tmp_path)
    path.parent.mkdir(parents=True)
    broken = b'{"wire_version":1,"action":"stage5_edit"'
    path.write_bytes(broken)

    assert human_actions.read_events(tmp_path) == []
    landed = human_actions.append_edit(tmp_path, **_kwargs())

    assert human_actions.read_events(tmp_path) == [landed]
    receipts = list(human_actions.tail_recovery_dir(tmp_path).glob('*.json'))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding='utf-8'))
    assert receipt['tail_bytes'] == len(broken)
    assert len(path.read_text(encoding='utf-8').splitlines()) == 2


def test_a_complete_but_invalid_last_line_is_not_treated_as_truncation(tmp_path):
    path = human_actions.actions_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"not":"a human action"}\n')

    with pytest.raises(ProvenanceRefused, match='第 1 行'):
        human_actions.read_events(tmp_path)
    with pytest.raises(ProvenanceRefused, match='第 1 行'):
        human_actions.append_edit(tmp_path, **_kwargs())


def test_valid_json_without_the_final_newline_is_still_an_uncommitted_tail(tmp_path):
    human_actions.append_edit(tmp_path, **_kwargs())
    path = human_actions.actions_path(tmp_path)
    raw = path.read_bytes()
    assert raw.endswith(b'\n')
    path.write_bytes(raw[:-1])

    assert human_actions.read_events(tmp_path) == [], (
        '末行没完整换行就不是落稳 committed，不能因 JSON 恰好可解析而放行')
    landed = human_actions.append_edit(tmp_path, **_kwargs())

    assert human_actions.read_events(tmp_path) == [landed]
    assert len(list(human_actions.tail_recovery_dir(tmp_path).glob('*.json'))) == 1
    assert len(path.read_text(encoding='utf-8').splitlines()) == 2


def test_truncated_committed_phase_is_rebuilt_from_the_stable_prepared_phase(
        tmp_path):
    human_actions.append_edit(tmp_path, **_kwargs())
    path = human_actions.actions_path(tmp_path)
    prepared, committed = path.read_bytes().splitlines(keepends=True)
    broken_committed = committed[:max(1, len(committed) // 2)]
    path.write_bytes(prepared + broken_committed)

    assert human_actions.read_events(tmp_path) == []
    rebuilt = human_actions.commit_edit(tmp_path, **_kwargs())

    assert human_actions.read_events(tmp_path) == [rebuilt]
    assert len(path.read_text(encoding='utf-8').splitlines()) == 2
    receipt = next(human_actions.tail_recovery_dir(tmp_path).glob('*.json'))
    payload = json.loads(receipt.read_text(encoding='utf-8'))
    assert payload['tail_bytes'] == len(broken_committed)


def test_equal_tail_bytes_under_different_prefixes_have_distinct_receipts(tmp_path):
    path = human_actions.actions_path(tmp_path)
    path.parent.mkdir(parents=True)
    repeated_tail = b'{"wire_version"'
    path.write_bytes(repeated_tail)
    human_actions.append_edit(tmp_path, **_kwargs())
    with path.open('ab') as handle:
        handle.write(repeated_tail)
    human_actions.append_edit(tmp_path, **_kwargs())

    receipts = list(human_actions.tail_recovery_dir(tmp_path).glob('*.json'))
    assert len(receipts) == 2
    assert len(human_actions.read_events(tmp_path)) == 1


def test_duplicate_committed_phase_is_closed_as_a_corrupt_audit_sequence(tmp_path):
    human_actions.append_edit(tmp_path, **_kwargs())
    path = human_actions.actions_path(tmp_path)
    committed = path.read_text(encoding='utf-8').splitlines()[-1]
    with path.open('a', encoding='utf-8', newline='') as handle:
        handle.write(committed + '\n')

    with pytest.raises(ProvenanceRefused, match='重复 committed'):
        human_actions.read_events(tmp_path)
    with pytest.raises(ProvenanceRefused, match='重复 committed'):
        human_actions.append_edit(tmp_path, **_kwargs())
