#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AgentRun v2 reconciled 必须是 admission owner/CAS 的生命周期投影。"""
from __future__ import annotations

from contracts.schema.orchestration import (
    AgentProfile, AgentRunEvent, AgentRunStart, PipelineAgentProfiles,
    RunContractV2,
)
from novel.orchestrator import admission, agent


def _started(base, run_id='agent-reconcile-v2', sid='42'):
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
    admission.open_gate(base, run_id, sid)
    return run_id


def test_reconcile_run_reuses_admission_subject_and_appends_one_v2_event(tmp_path):
    run_id = _started(tmp_path)
    subject = admission.reconcile_subject(tmp_path, run_id)

    first = agent.reconcile_run(
        tmp_path, run_id, actor='恢复值守员', reason='已确认原 owner 停止',
        subject_sha256=subject)
    second = agent.reconcile_run(
        tmp_path, run_id, actor='恢复值守员', reason='已确认原 owner 停止',
        subject_sha256=subject)

    assert first.state == second.state == 'unknown'
    assert first.note == second.note
    assert subject in (first.note or '')
    assert admission.snapshot(tmp_path, run_id).terminal_kind == 'reconciled'
    events = [
        event for event in agent.read_events(tmp_path)
        if event.run_id == run_id and event.kind == 'reconciled']
    assert len(events) == 1 and events[0].wire_version == 2
