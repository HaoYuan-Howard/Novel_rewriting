#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from novel.errors import TerminalState
from novel.orchestrator import admission, beacon, write_admission


C = importlib.import_module('novel.cli.main')


def test_approval_read_stays_read_only_but_mutation_holds_generic_claim(
        monkeypatch, tmp_path):
    admitted = []

    @contextmanager
    def scope(base, sid, *, operation, details):
        admitted.append((base, sid, operation, details))
        yield object()

    monkeypatch.setattr(write_admission, 'operation_scope', scope)
    monkeypatch.setattr(C, '_cmd_approval_admitted', lambda _a: 17)

    read = SimpleNamespace(
        approval_action='show', base=str(tmp_path), sid='42')
    assert C._cmd_approval(read) == 17
    assert admitted == []

    write = SimpleNamespace(
        approval_action='gate', gate='s2', base=str(tmp_path), sid='42')
    assert C._cmd_approval(write) == 17
    assert admitted == [(
        str(tmp_path), '42', 'approval-gate',
        {'action': 'gate', 'gate': 's2'},
    )]


def test_response_repair_holds_generic_claim_without_hashing_reason(
        monkeypatch, tmp_path):
    admitted = []

    @contextmanager
    def scope(base, sid, *, operation, details):
        admitted.append((base, sid, operation, details))
        yield object()

    monkeypatch.setattr(write_admission, 'operation_scope', scope)
    monkeypatch.setattr(C, '_cmd_response_repair_admitted', lambda _a: 23)
    args = SimpleNamespace(
        base=str(tmp_path), sid='42', attempt='a' * 16, batch=None,
        run_id='run-1', decline=False, decisions=None, actor='reviewer',
        reason='不应进入 generic claim details 的人工理由')

    assert C._cmd_response_repair(args) == 23
    [(base, sid, operation, details)] = admitted
    assert (base, sid, operation) == (str(tmp_path), '42', 'response-repair')
    assert details['attempt'] == 'a' * 16
    assert 'reason' not in details


def test_commander_mark_reuses_a_preclose_node_claim_after_gate_closed(
        monkeypatch, tmp_path):
    parent_run_id = 'parent-1'
    admission.open_gate(tmp_path, parent_run_id, '42')
    claim = admission.claim(
        tmp_path, parent_run_id, '42', node='stage2.global',
        claim_sha256='a' * 64)
    admission.close_gate(tmp_path, parent_run_id)
    monkeypatch.setenv(admission.CLAIM_ID_ENV, claim.claim_id)
    monkeypatch.setenv(C.run_identity.RUN_ID_ENV, parent_run_id)
    monkeypatch.setattr(
        C.commander_context, 'load_from_environment',
        lambda *_args, **_kwargs: SimpleNamespace(run_id=parent_run_id))
    monkeypatch.setattr(
        write_admission, 'operation_scope',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('Commander mark 不得在 close 后新开 generic claim')))

    args = SimpleNamespace(
        base=str(tmp_path), sid='42', node='stage2.global', phase='start',
        execution_id='execution-1', exit=None, note='test',
        run_id=parent_run_id)
    assert C._cmd_mark(args) == 0
    [event] = beacon.read_events(tmp_path)
    assert event.run_id == parent_run_id
    assert event.execution_id == 'execution-1'
    admission.release(tmp_path, claim)


def test_commander_end_closes_exact_open_execution_through_drift_loader(
        monkeypatch, tmp_path):
    parent_run_id = 'parent-close'
    admission.open_gate(tmp_path, parent_run_id, '42')
    claim = admission.claim(
        tmp_path, parent_run_id, '42', node='stage2.blueprint',
        claim_sha256='b' * 64)
    beacon.append(tmp_path, C.ProgressEvent(
        ts=1, sid='42', node='stage2.blueprint', phase='start',
        run_id=parent_run_id, execution_id='execution-close'))
    monkeypatch.setenv(admission.CLAIM_ID_ENV, claim.claim_id)
    monkeypatch.setenv(C.run_identity.RUN_ID_ENV, parent_run_id)

    def strict_loader(*_args, **_kwargs):
        raise AssertionError('phase=end 不得再走会被当前 code/skill 漂移阻断的执行 loader')

    monkeypatch.setattr(
        C.commander_context, 'load_from_environment', strict_loader)
    monkeypatch.setattr(
        C.commander_context, 'load_for_execution_close_from_environment',
        lambda *_args, **_kwargs: SimpleNamespace(run_id=parent_run_id))
    args = SimpleNamespace(
        base=str(tmp_path), sid='42', node='stage2.blueprint', phase='end',
        execution_id='execution-close', exit=2, note='code drift',
        run_id=parent_run_id)

    assert C._cmd_mark(args) == 0
    assert [event.phase for event in beacon.read_events(tmp_path)] == [
        'start', 'end']
    assert beacon.open_executions(tmp_path, parent_run_id) == ()
    admission.release(tmp_path, claim)


def test_commander_end_drift_loader_cannot_append_an_orphan_execution(
        monkeypatch, tmp_path):
    parent_run_id = 'parent-orphan'
    admission.open_gate(tmp_path, parent_run_id, '42')
    claim = admission.claim(
        tmp_path, parent_run_id, '42', node='stage2.blueprint',
        claim_sha256='c' * 64)
    monkeypatch.setenv(admission.CLAIM_ID_ENV, claim.claim_id)
    monkeypatch.setenv(C.run_identity.RUN_ID_ENV, parent_run_id)
    monkeypatch.setattr(
        C.commander_context, 'load_for_execution_close_from_environment',
        lambda *_args, **_kwargs: SimpleNamespace(run_id=parent_run_id))
    args = SimpleNamespace(
        base=str(tmp_path), sid='42', node='stage2.blueprint', phase='end',
        execution_id='execution-never-started', exit=2, note=None,
        run_id=parent_run_id)

    with pytest.raises(TerminalState, match='恰有一个未闭合 start'):
        C._cmd_mark(args)
    assert beacon.read_events(tmp_path) == []
    admission.release(tmp_path, claim)


def test_contextless_mark_uses_writer_coordination_scope(monkeypatch, tmp_path):
    admitted = []
    events = []

    @contextmanager
    def scope(base, sid, *, operation, details):
        admitted.append((base, sid, operation, details))
        yield object()

    monkeypatch.setattr(
        C.commander_context, 'load_from_environment',
        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(write_admission, 'operation_scope', scope)
    monkeypatch.setattr(C.beacon, 'append', lambda _base, event: events.append(event))
    args = SimpleNamespace(
        base=str(tmp_path), sid='42', node='stage2.global', phase='end',
        execution_id='execution-1', exit=0, note=None, run_id='direct-1')

    assert C._cmd_mark(args) == 0
    assert admitted[0][2] == 'progress-mark'
    assert admitted[0][3]['execution_id'] == 'execution-1'
    assert events[0].exit_code == 0


@pytest.mark.parametrize('execution_id,run_id,match', [
    (None, 'parent-1', '--execution-id'),
    ('execution-1', 'other-parent', '与 Commander parent'),
])
def test_commander_mark_requires_exact_execution_and_parent_identity(
        monkeypatch, tmp_path, execution_id, run_id, match):
    parent_run_id = 'parent-1'
    admission.open_gate(tmp_path, parent_run_id, '42')
    claim = admission.claim(
        tmp_path, parent_run_id, '42', node='stage2.global',
        claim_sha256='a' * 64)
    monkeypatch.setenv(admission.CLAIM_ID_ENV, claim.claim_id)
    monkeypatch.setenv(C.run_identity.RUN_ID_ENV, parent_run_id)
    monkeypatch.setattr(
        C.commander_context, 'load_from_environment',
        lambda *_args, **_kwargs: SimpleNamespace(run_id=parent_run_id))
    args = SimpleNamespace(
        base=str(tmp_path), sid='42', node='stage2.global', phase='start',
        execution_id=execution_id, exit=None, note=None, run_id=run_id)

    with pytest.raises(TerminalState, match=match):
        C._cmd_mark(args)
    assert beacon.read_events(tmp_path) == []


def test_commander_mark_does_not_let_explicit_run_id_mask_inherited_drift(
        monkeypatch, tmp_path):
    parent_run_id = 'parent-1'
    admission.open_gate(tmp_path, parent_run_id, '42')
    claim = admission.claim(
        tmp_path, parent_run_id, '42', node='stage2.global',
        claim_sha256='a' * 64)
    monkeypatch.setenv(admission.CLAIM_ID_ENV, claim.claim_id)
    monkeypatch.setenv(C.run_identity.RUN_ID_ENV, 'other-parent')
    monkeypatch.setattr(
        C.commander_context, 'load_from_environment',
        lambda *_args, **_kwargs: SimpleNamespace(run_id=parent_run_id))
    args = SimpleNamespace(
        base=str(tmp_path), sid='42', node='stage2.global', phase='start',
        execution_id='execution-1', exit=None, note=None, run_id=parent_run_id)

    with pytest.raises(TerminalState, match='NOVEL_RUN_ID'):
        C._cmd_mark(args)
    assert beacon.read_events(tmp_path) == []
