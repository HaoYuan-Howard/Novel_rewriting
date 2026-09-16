#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""非节点 writer lifecycle：generic claim、直接 writer 互斥与真实入口接线。"""
from __future__ import annotations

import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from contracts.schema.orchestration import (
    AgentRunEvent, AgentRunStart, LegacyExecutorBackend, LegacyLlmProfile,
    PipelineAgentProfiles,
)
from novel.orchestrator import operator_telemetry as telemetry
from novel.orchestrator import execution_context
from novel.errors import TerminalState
from novel.orchestrator import admission, agent, jobs, write_admission


SID = '42'
PARENT = 'commander-write-guard'
SESSION = '11111111-1111-4111-8111-111111111111'
PHASE = '22222222-2222-4222-8222-222222222222'


def _profiles() -> PipelineAgentProfiles:
    """C2a 起执行面 legacy-only：parent run fixtures 一律用 legacy backend。"""
    profile = LegacyLlmProfile(
        provider='deepseek', model='deepseek-v4-pro',
        reasoning_effort='high', thinking_mode='on')
    return PipelineAgentProfiles(
        executor_default=LegacyExecutorBackend(profile=profile),
        executor_overrides={})


def _install_commander(base: Path, monkeypatch, *, run_id: str = PARENT) -> str:
    start = AgentRunStart(
        sid=SID, base=str(base.resolve()), argv=['codex', 'exec', '-'],
        prompt='writer lifecycle test', authorized=True,
        egress_authorized=True, code_version='write-guard-code-v1',
        identity_version=2, profiles=_profiles())
    agent.append(base, AgentRunEvent(
        run_id=run_id, kind='started', ts=1, start=start))
    env = execution_context.env_with_context(
        run_id=run_id, base=base, start=start, env={})
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(
        execution_context.prov, 'code_version',
        lambda: 'write-guard-code-v1')
    return run_id


def _without_commander(monkeypatch) -> None:
    monkeypatch.delenv(
        execution_context.COMMANDER_RUN_ID_ENV, raising=False)
    monkeypatch.delenv(
        execution_context.COMMANDER_CONTRACT_SHA256_ENV, raising=False)


def _finish_gate(base: Path, run_id: str) -> None:
    current = admission.snapshot(base, run_id)
    if not current.closed:
        admission.close_gate(base, run_id)
    current = admission.snapshot(base, run_id)
    if current.terminal_kind is None:
        admission.mark_drained(base, run_id)


def test_operation_hash_binds_protocol_base_sid_operation_and_details(tmp_path):
    base = write_admission.operation_sha256(
        tmp_path, '42', 'maintenance-write', details={'started_at': 1})
    variants = {
        write_admission.operation_sha256(
            tmp_path / 'other', '42', 'maintenance-write',
            details={'started_at': 1}),
        write_admission.operation_sha256(
            tmp_path, '43', 'maintenance-write', details={'started_at': 1}),
        write_admission.operation_sha256(
            tmp_path, '42', 'operator-session-end',
            details={'started_at': 1}),
        write_admission.operation_sha256(
            tmp_path, '42', 'maintenance-write', details={'started_at': 2}),
    }
    assert len(base) == 64 and base not in variants and len(variants) == 4
    payload = write_admission.operation_payload(
        tmp_path, '42', 'maintenance-write', details={'started_at': 1})
    assert payload['protocol'] == write_admission.WRITE_ADMISSION_PROTOCOL


def test_closed_parent_rejects_a_late_generic_writer(tmp_path, monkeypatch):
    run_id = _install_commander(tmp_path, monkeypatch)
    admission.open_gate(tmp_path, run_id, SID)
    admission.close_gate(tmp_path, run_id)

    with pytest.raises(TerminalState, match='关闭 admission|迟到'):
        with write_admission.operation_scope(
                tmp_path, SID, operation='maintenance-write'):
            raise AssertionError('closed parent 不得进入 mutation body')

    admission.mark_drained(tmp_path, run_id)


def test_active_generic_claim_blocks_drained_until_scope_exits(
        tmp_path, monkeypatch):
    run_id = _install_commander(tmp_path, monkeypatch)
    admission.open_gate(tmp_path, run_id, SID)

    with write_admission.operation_scope(
            tmp_path, SID, operation='maintenance-write') as receipt:
        current = admission.snapshot(tmp_path, run_id)
        assert len(current.active_claims) == 1
        assert current.active_claims[0].node == 'write:maintenance-write'
        assert current.active_claims[0].claim_sha256 == receipt.claim_sha256
        admission.close_gate(tmp_path, run_id)
        with pytest.raises(TerminalState, match='active claims'):
            admission.mark_drained(tmp_path, run_id)

    assert admission.snapshot(tmp_path, run_id).active_claims == ()
    admission.mark_drained(tmp_path, run_id)


def test_generic_claim_releases_when_mutation_body_raises(tmp_path, monkeypatch):
    run_id = _install_commander(tmp_path, monkeypatch)
    admission.open_gate(tmp_path, run_id, SID)

    with pytest.raises(RuntimeError, match='mutation failed'):
        with write_admission.operation_scope(
                tmp_path, SID, operation='operator-phase-start'):
            raise RuntimeError('mutation failed')

    assert admission.snapshot(tmp_path, run_id).active_claims == ()
    _finish_gate(tmp_path, run_id)


def test_contextless_writer_refuses_an_orphan_admission(tmp_path, monkeypatch):
    _without_commander(monkeypatch)
    admission.open_gate(tmp_path, 'orphan-generic-writer', SID)
    try:
        with pytest.raises(TerminalState, match='未闭合 Commander admission'):
            with write_admission.operation_scope(
                    tmp_path, SID, operation='operator-session-start'):
                raise AssertionError('orphan admission 下不得写盘')
    finally:
        _finish_gate(tmp_path, 'orphan-generic-writer')


def test_contextless_release_failure_does_not_mask_the_mutation_error(
        tmp_path, monkeypatch):
    _without_commander(monkeypatch)
    actual_release = jobs.REGISTRY.release
    monkeypatch.setattr(
        jobs.REGISTRY, 'release',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError('contextless release unavailable')))

    with pytest.raises(RuntimeError, match='body is primary') as caught:
        with write_admission.operation_scope(
                tmp_path, SID, operation='maintenance-write'):
            raise RuntimeError('body is primary')

    notes = '\n'.join(getattr(caught.value, '__notes__', ()))
    assert 'contextless release unavailable' in notes
    holder = jobs.REGISTRY.conflicting_holder(SID)
    assert holder is not None, 'release 失败必须如实留下 active writer'
    actual_release(jobs.active_key(SID, 'write:maintenance-write'), holder)


def test_contextless_release_failure_is_explicit_after_successful_body(
        tmp_path, monkeypatch):
    _without_commander(monkeypatch)
    actual_release = jobs.REGISTRY.release
    monkeypatch.setattr(
        jobs.REGISTRY, 'release',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError('contextless release unavailable')))

    with pytest.raises(OSError, match='contextless release unavailable'):
        with write_admission.operation_scope(
                tmp_path, SID, operation='maintenance-write'):
            pass

    holder = jobs.REGISTRY.conflicting_holder(SID)
    assert holder is not None, 'release 失败必须如实留下 active writer'
    actual_release(jobs.active_key(SID, 'write:maintenance-write'), holder)


def test_contextless_scope_and_agent_admission_are_one_barrier_transaction(
        tmp_path, monkeypatch):
    """直接/API writer 与 agent.start 的对表检查+占锁不能双双穿过。"""
    _without_commander(monkeypatch)
    barrier = threading.Barrier(2)
    release = threading.Event()
    entered = threading.Event()
    loser_done = threading.Event()
    winners: list[str] = []
    conflicts: list[str] = []
    failures: list[BaseException] = []
    original_job_admit = jobs.acquire_job_writer

    def synchronized_job_admit(*args, **kwargs):
        barrier.wait(timeout=3)
        return original_job_admit(*args, **kwargs)

    monkeypatch.setattr(jobs, 'acquire_job_writer', synchronized_job_admit)

    def direct_writer() -> None:
        try:
            with write_admission.operation_scope(
                    tmp_path, SID, operation='maintenance-write'):
                winners.append('write')
                entered.set()
                assert release.wait(3)
        except jobs.JobConflict:
            conflicts.append('write')
            loser_done.set()
        except BaseException as exc:  # noqa: BLE001 —— 主线程统一报告
            failures.append(exc)
            loser_done.set()

    def commander_writer() -> None:
        writer_id = 'agent-generic-race'
        key: str | None = None
        try:
            barrier.wait(timeout=3)
            key = jobs.acquire_agent_writer(
                tmp_path, SID, writer_id,
                admit=lambda lock_key, run_id, registry:
                    registry.acquire(lock_key, run_id))
            winners.append('agent')
            entered.set()
            assert release.wait(3)
        except jobs.JobConflict:
            conflicts.append('agent')
            loser_done.set()
        except BaseException as exc:  # noqa: BLE001 —— 主线程统一报告
            failures.append(exc)
            loser_done.set()
        finally:
            if key is not None:
                jobs.AGENT_REGISTRY.release(key, writer_id)

    threads = [threading.Thread(target=direct_writer),
               threading.Thread(target=commander_writer)]
    for thread in threads:
        thread.start()
    try:
        assert entered.wait(2), failures
        assert loser_done.wait(2), 'contextless writer 与 agent 同时越过 admission'
        assert failures == []
        assert len(winners) == len(conflicts) == 1
        assert winners[0] != conflicts[0]
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=3)
    assert all(not thread.is_alive() for thread in threads)


def test_all_four_telemetry_writes_hold_real_generic_claims(
        tmp_path, monkeypatch):
    run_id = _install_commander(tmp_path, monkeypatch)
    admission.open_gate(tmp_path, run_id, SID)
    observed: list[tuple[str, str, str]] = []
    real_scope = write_admission.operation_scope

    @contextmanager
    def observed_scope(base, sid, *, operation, details=None, env=None):
        with real_scope(
                base, sid, operation=operation, details=details,
                env=env) as receipt:
            current = admission.snapshot(base, run_id)
            assert any(
                claim.claim_id == receipt.claim_id
                for claim in current.active_claims)
            observed.append((operation, sid, receipt.parent_run_id or ''))
            yield receipt

    monkeypatch.setattr(telemetry, 'operation_scope', observed_scope)
    telemetry.start_session(
        tmp_path, SID, start_code_version='write-guard-code-v1',
        source='skill', parent_run_id=run_id, session_id=SESSION,
        started_at_ms=1_000)
    telemetry.begin_phase(
        tmp_path, SESSION, phase='review', phase_execution_id=PHASE,
        started_at_ms=1_100)
    telemetry.finish_phase(
        tmp_path, SESSION, PHASE, state='completed', ended_at_ms=1_200)
    telemetry.finish_session(
        tmp_path, SESSION, state='completed',
        end_code_version='write-guard-code-v1', ended_at_ms=1_300)
    before_summary = list(observed)
    assert telemetry.summarize(
        tmp_path, SID, session_id=SESSION)['session_count'] == 1
    assert observed == before_summary, 'summary 是纯读，不得取得 write claim'
    assert observed == [
        ('operator-session-start', SID, run_id),
        ('operator-phase-start', SID, run_id),
        ('operator-phase-end', SID, run_id),
        ('operator-session-end', SID, run_id),
    ]
    assert admission.snapshot(tmp_path, run_id).active_claims == ()
    _finish_gate(tmp_path, run_id)


def test_commander_cannot_append_to_another_parent_telemetry_session(
        tmp_path, monkeypatch):
    _without_commander(monkeypatch)
    telemetry.start_session(
        tmp_path, SID, start_code_version='write-guard-code-v1',
        parent_run_id='other-commander', session_id=SESSION,
        started_at_ms=1_000)
    run_id = _install_commander(tmp_path, monkeypatch)
    admission.open_gate(tmp_path, run_id, SID)

    with pytest.raises(ValueError, match='parent_run_id'):
        telemetry.begin_phase(
            tmp_path, SESSION, phase='review',
            phase_execution_id=PHASE, started_at_ms=1_100)

    assert admission.snapshot(tmp_path, run_id).active_claims == ()
    assert len(telemetry.read_events(tmp_path, SESSION)) == 1
    _finish_gate(tmp_path, run_id)
