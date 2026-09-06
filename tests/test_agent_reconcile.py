#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AgentRun v2 reconciled 必须是 admission owner/CAS 的生命周期投影。"""
from __future__ import annotations

import json
import importlib

import pytest

from contracts.schema.orchestration import (
    AgentDeliveryOutcome, AgentProfile, AgentRunEvent, AgentRunStart,
    CommanderAdmissionEvent, ExecutionEpoch, PipelineAgentProfiles,
    ProgressEvent, RunContractV2,
)
from novel.errors import TerminalState
from novel.orchestrator import admission, agent, beacon
from novel.orchestrator.jobs import JobConflict


def _started(
        base, run_id='agent-reconcile-v2', sid='42', *,
        epoch_id='epoch-reconcile-v2', admission_id=None,
        open_admission=True,
):
    profile = AgentProfile(
        driver='codex', model='gpt-5.6-sol', reasoning_effort='high',
        execution_mode='standard')
    profiles = PipelineAgentProfiles(
        commander=profile, executor_default=profile, executor_overrides={})
    start = AgentRunStart(
        sid=sid, base=str(base), argv=['codex', 'exec'], prompt='测试',
        authorized=True, egress_authorized=True, code_version='code-v1',
        skill_sha256='1' * 64, profiles=profiles)
    contract = RunContractV2(
        sid=sid, base=str(base), authorized=True,
        egress_authorized=True, profiles=profiles)
    agent.append(base, AgentRunEvent(
        wire_version=2, run_id=run_id, kind='started', ts=1,
        start=start, contract=contract))
    epoch = ExecutionEpoch(
        epoch_id=epoch_id, ordinal=2, reason='code_change',
        argv=['codex', 'exec'], prompt_sha256='2' * 64,
        protocol_version='test/v1', code_version='code-v2',
        skill_sha256='3' * 64, session_run_id='session-reconcile-v2')
    agent.append(base, AgentRunEvent(
        wire_version=2, run_id=run_id, kind='epoch_opened', ts=2,
        epoch=epoch))
    gate_id = admission_id or epoch_id
    if open_admission:
        admission.open_gate(
            base, gate_id, sid, lifecycle_run_id=run_id)
    return run_id, gate_id


def _write_legacy_open(base, admission_id, sid='42'):
    """写入升级前真实 v1 形状（没有 lifecycle_run_id 键）。"""
    payload = {
        'wire_version': 1, 'parent_run_id': admission_id,
        'sid': sid, 'kind': 'opened', 'ts': 3,
        'claim_id': None, 'node': None, 'claim_sha256': None,
        'actor': None, 'reason': None, 'subject_sha256': None, 'force': None,
    }
    path = admission.admission_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False) + '\n', encoding='utf-8')


def test_reconcile_run_reuses_admission_subject_and_appends_one_v2_event(tmp_path):
    run_id, admission_id = _started(
        tmp_path, admission_id='epoch-reconcile-v2-t2')
    subject = admission.reconcile_subject(tmp_path, admission_id)

    first = agent.reconcile_run(
        tmp_path, run_id, admission_id=admission_id,
        actor='恢复值守员', reason='已确认原 owner 停止',
        subject_sha256=subject)
    second = agent.reconcile_run(
        tmp_path, run_id, admission_id=admission_id,
        actor='恢复值守员', reason='已确认原 owner 停止',
        subject_sha256=subject)

    assert first.state == second.state == 'unknown'
    assert first.note == second.note
    assert subject in (first.note or '')
    assert first.active_epoch_id is None
    assert first.ended is None and first.exit_code is None and first.outcome is None
    gate = admission.snapshot(tmp_path, admission_id)
    assert gate.terminal_kind == 'reconciled'
    assert gate.lifecycle_run_id == run_id
    events = [
        event for event in agent.read_events(tmp_path)
        if event.run_id == run_id and event.kind == 'reconciled']
    assert len(events) == 1 and events[0].wire_version == 2
    assert not [
        event for event in agent.read_events(tmp_path)
        if event.run_id == admission_id]
    assert not [
        event for event in agent.read_events(tmp_path)
        if event.kind in ('epoch_closed', 'ended')]


@pytest.mark.parametrize('force', [False, True])
def test_fresh_epoch_reconcile_checks_outer_owner_lease(tmp_path, force):
    run_id, admission_id = _started(
        tmp_path, run_id='agent-live-outer', epoch_id='fresh-epoch-live',
        admission_id='fresh-epoch-live-t2')
    subject = admission.reconcile_subject(tmp_path, admission_id)

    with admission.owner_lease(tmp_path, run_id):
        with pytest.raises(TerminalState, match='owner lease'):
            agent.reconcile_run(
                tmp_path, run_id, admission_id=admission_id,
                actor='恢复值守员', reason='live outer 不可销账',
                subject_sha256=subject, force=force)

    current = admission.snapshot(tmp_path, admission_id)
    assert current.closed is False and current.terminal_kind is None


def test_reconcile_retry_supplements_outer_event_but_rejects_actor_drift(tmp_path):
    run_id, admission_id = _started(
        tmp_path, run_id='agent-retry-outer', epoch_id='fresh-epoch-retry')
    subject = admission.reconcile_subject(tmp_path, admission_id)
    admission.reconcile_gate(
        tmp_path, admission_id, lifecycle_run_id=run_id,
        actor='值守员-A', reason='已核实原进程树退出',
        subject_sha256=subject)

    done = agent.reconcile_run(
        tmp_path, run_id, admission_id=admission_id,
        actor='值守员-A', reason='已核实原进程树退出',
        subject_sha256=subject)
    assert done.active_epoch_id is None
    with pytest.raises(TerminalState, match='actor/reason/force'):
        agent.reconcile_run(
            tmp_path, run_id, admission_id=admission_id,
            actor='值守员-B', reason='另一份恢复决定',
            subject_sha256=subject)


def test_invalidated_agent_seal_can_be_reconciled_again_with_new_subject(
        tmp_path):
    run_id, admission_id = _started(
        tmp_path, run_id='agent-reseal-outer',
        epoch_id='fresh-epoch-reseal')
    first_subject = admission.reconcile_subject(tmp_path, admission_id)
    agent.reconcile_run(
        tmp_path, run_id, admission_id=admission_id,
        actor='值守员-A', reason='首次核查',
        subject_sha256=first_subject)

    for phase, ts in (('start', 10), ('end', 11)):
        beacon.append(tmp_path, ProgressEvent(
            ts=ts, sid='42', run_id=run_id,
            execution_id='late-outer-complete', node='stage4.design',
            phase=phase, exit_code=0 if phase == 'end' else None))
    invalidated = admission.snapshot(tmp_path, admission_id)
    assert invalidated.terminal_kind is None
    assert invalidated.reconcile_invalidated is True
    assert admission.unresolved(tmp_path, '42') == (invalidated,)

    second_subject = admission.reconcile_subject(tmp_path, admission_id)
    assert second_subject != first_subject
    done = agent.reconcile_run(
        tmp_path, run_id, admission_id=admission_id,
        actor='值守员-B', reason='复核迟到 outer beacon',
        subject_sha256=second_subject)

    assert done.state == 'unknown'
    assert done.active_epoch_id is None and done.ended is None
    assert admission.snapshot(
        tmp_path, admission_id).terminal_kind == 'reconciled'
    lifecycle = [
        event for event in agent.read_events(tmp_path)
        if event.run_id == run_id]
    assert [event.kind for event in lifecycle].count('reconciled') == 2
    assert not [
        event for event in lifecycle
        if event.kind in ('epoch_closed', 'ended')]


def test_legacy_v1_gate_uses_only_unique_exact_epoch_opened_mapping(tmp_path):
    run_id, admission_id = _started(
        tmp_path, run_id='legacy-outer-a', epoch_id='legacy-fresh-epoch',
        admission_id='legacy-fresh-epoch-t2', open_admission=False)
    _write_legacy_open(tmp_path, admission_id)

    binding = agent.resolve_admission_lifecycle(tmp_path, admission_id)
    assert binding.lifecycle_run_id == run_id
    assert binding.agent_run is not None

    # 合法 action id 只认 exact epoch / canonical -tN；普通前缀不能误联 outer。
    prefixed = 'legacy-fresh-epoch-trash'
    with admission.append_transaction(admission.admission_path(tmp_path)):
        admission._append_locked(  # noqa: SLF001 —— 构造真实旧 wire 回归
            admission.admission_path(tmp_path),
            CommanderAdmissionEvent(
                parent_run_id=prefixed, sid='42', kind='opened', ts=4))
    prefix_binding = agent.resolve_admission_lifecycle(tmp_path, prefixed)
    assert prefix_binding.lifecycle_run_id == prefixed
    assert prefix_binding.agent_run is None

    _started(
        tmp_path, run_id='legacy-outer-b', epoch_id='legacy-fresh-epoch',
        admission_id=admission_id, open_admission=False)
    with pytest.raises(TerminalState, match='多条 AgentRun epoch_opened'):
        agent.resolve_admission_lifecycle(tmp_path, admission_id)


def test_legacy_exact_epoch_match_with_sid_drift_fails_closed(tmp_path):
    _started(
        tmp_path, run_id='legacy-wrong-sid-outer', sid='99',
        epoch_id='legacy-wrong-sid-epoch', open_admission=False)
    admission_id = 'legacy-wrong-sid-epoch-t2'
    _write_legacy_open(tmp_path, admission_id, sid='42')

    with pytest.raises(TerminalState, match='sid.*漂移.*generic self'):
        agent.resolve_admission_lifecycle(tmp_path, admission_id)


def test_cli_preview_and_apply_use_outer_beacon_and_agent_run(
        tmp_path, capsys):
    cli = importlib.import_module('novel.cli.main')
    run_id, admission_id = _started(
        tmp_path, run_id='cli-outer-run', epoch_id='cli-fresh-epoch',
        admission_id='cli-fresh-epoch-t2')
    beacon.append(tmp_path, ProgressEvent(
        ts=10, sid='42', run_id=run_id,
        execution_id='outer-open-execution', node='stage4.design',
        phase='start'))

    assert cli.main([
        'reconcile', '--base', str(tmp_path),
        '--admission-parent-run-id', admission_id]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown['admission_id'] == admission_id
    assert shown['lifecycle_run_id'] == run_id
    assert shown['agent_run_id'] == run_id
    assert shown['open_execution_ids'] == ['outer-open-execution']
    assert shown['force_required_while_open'] is True

    assert cli.main([
        'reconcile', '--base', str(tmp_path),
        '--admission-parent-run-id', admission_id,
        '--actor', '恢复值守员', '--reason', '已核实 outer 进程树停止',
        '--subject-sha256', shown['subject_sha256'], '--force']) == 0
    capsys.readouterr()
    assert admission.snapshot(
        tmp_path, admission_id).terminal_kind == 'reconciled'
    assert agent.records(tmp_path)[0].active_epoch_id is None


@pytest.mark.parametrize('terminal_kind', ['paused', 'ended'])
def test_reconcile_refuses_paused_or_ended_outer_lifecycle(
        tmp_path, terminal_kind):
    base = tmp_path / terminal_kind
    run_id, admission_id = _started(
        base, run_id=f'{terminal_kind}-outer',
        epoch_id=f'{terminal_kind}-epoch')
    if terminal_kind == 'paused':
        agent.append(base, AgentRunEvent(
            wire_version=2, run_id=run_id, kind='paused', ts=3,
            note='用户已明确暂停'))
    else:
        agent.append(base, AgentRunEvent(
            wire_version=2, run_id=run_id, kind='ended', ts=3,
            exit_code=0, outcome=AgentDeliveryOutcome(
                state='ready', reason='正常交付', checked_at=3)))
    subject = admission.reconcile_subject(base, admission_id)

    with pytest.raises(JobConflict, match=terminal_kind):
        agent.reconcile_run(
            base, run_id, admission_id=admission_id,
            actor='恢复值守员', reason='不应覆盖明确 lifecycle',
            subject_sha256=subject)
    current = admission.snapshot(base, admission_id)
    assert current.closed is False and current.terminal_kind is None
