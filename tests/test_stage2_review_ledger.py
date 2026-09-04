#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 Global Commander 的跨重启预算与单调进展账。"""
from __future__ import annotations

import json

import pytest

from contracts.schema.orchestration import (
    AgentDeliveryOutcome, AgentProfile, AgentRunEvent, AgentRunStart,
    PipelineAgentProfiles, RunContractV2, Stage2ReviewLedger,
)
from novel import agent_session_audit
from novel.errors import TerminalState
from novel.orchestrator import agent, stage2_review_ledger


SOURCE = 'a' * 64
MAIN = 'b' * 64
WORKPACK = 'c' * 64
BUSINESS = 'd' * 64
NO_WIRE_SESSION = 'stage2-no-wire-session'
NO_WIRE_PARENT = 'stage2-no-wire-parent'


def _reserve(
    base, ordinal: int, *, progress: str | None = None,
    blocker_count: int = 2,
):
    return stage2_review_ledger.reserve(
        base, '42', source_sha256=SOURCE, main_text_sha256=MAIN,
        business_sha256=BUSINESS, workpack_sha256=WORKPACK,
        progress_sha256=progress or str(ordinal) * 64,
        blocker_count=blocker_count,
        purpose='review' if ordinal == 1 else 'correction',
        parent_run_id=f'parent-{ordinal}', epoch_id='epoch-1',
        session_run_id=f'stage2-session-{ordinal}', now=ordinal)


def _ended_parent_with_known_log(base) -> None:
    profile = AgentProfile(
        driver='codex', model='gpt-5.6-sol', reasoning_effort='high',
        execution_mode='standard')
    profiles = PipelineAgentProfiles(
        commander=profile, executor_default=profile, executor_overrides={})
    start = AgentRunStart(
        sid='42', base=str(base), argv=['codex', 'exec'], prompt='测试',
        authorized=True, egress_authorized=True, code_version='code-v1',
        skill_sha256='1' * 64, profiles=profiles)
    contract = RunContractV2(
        sid='42', base=str(base), authorized=True,
        egress_authorized=True, profiles=profiles)
    agent.append(base, AgentRunEvent(
        wire_version=2, run_id=NO_WIRE_PARENT, kind='started', ts=1,
        start=start, contract=contract))
    agent.append(base, AgentRunEvent(
        wire_version=2, run_id=NO_WIRE_PARENT, kind='ended', ts=2,
        exit_code=2, outcome=AgentDeliveryOutcome(
            state='stopped', reason='Stage2 预算被旧 bug 提前耗尽', checked_at=2)))
    log = agent.run_log_path(base, NO_WIRE_PARENT)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        'Traceback (most recent call last):\n'
        'ValueError: admission id 必须属于当前 epoch\n',
        encoding='utf-8')


def _legacy_false_reservation(base) -> None:
    stage2_review_ledger.reserve(
        base, '42', source_sha256=SOURCE, main_text_sha256=MAIN,
        business_sha256=BUSINESS, workpack_sha256=WORKPACK,
        progress_sha256='1' * 64, blocker_count=2, purpose='prereview',
        parent_run_id=NO_WIRE_PARENT, epoch_id=NO_WIRE_PARENT,
        session_run_id=NO_WIRE_SESSION, now=1)
    stage2_review_ledger.finish(
        base, '42', source_sha256=SOURCE, main_text_sha256=MAIN,
        session_run_id=NO_WIRE_SESSION, exit_code=70, now=1)
    _ended_parent_with_known_log(base)


def test_started_turn_consumes_persistent_budget_and_fourth_is_refused(tmp_path):
    first = _reserve(tmp_path, 1)
    assert first.outcome == 'started'
    stage2_review_ledger.finish(
        tmp_path, '42', source_sha256=SOURCE, main_text_sha256=MAIN,
        session_run_id=first.session_run_id, exit_code=0, now=11)
    _reserve(tmp_path, 2)
    _reserve(tmp_path, 3)

    path = stage2_review_ledger.ledger_path(
        tmp_path, '42', source_sha256=SOURCE, main_text_sha256=MAIN)
    ledger = Stage2ReviewLedger.model_validate_json(path.read_bytes())
    assert ledger.state == 'exhausted'
    assert [row.ordinal for row in ledger.turns] == [1, 2, 3]
    assert len({row.session_run_id for row in ledger.turns}) == 3
    assert ledger.turns[1].outcome == 'started', '崩溃前 started 也已消费第二格'

    with pytest.raises(
            stage2_review_ledger.Stage2ReviewBudgetRefused,
            match='用满 3 次'):
        _reserve(tmp_path, 4, progress='4' * 64)


def test_repeated_progress_and_blocker_growth_are_refused(tmp_path):
    _reserve(tmp_path, 1, blocker_count=2)
    with pytest.raises(
            stage2_review_ledger.Stage2ReviewBudgetRefused,
            match='fingerprint 没有变化'):
        _reserve(tmp_path, 2, progress='1' * 64, blocker_count=2)
    with pytest.raises(
            stage2_review_ledger.Stage2ReviewBudgetRefused,
            match='倒退增加'):
        _reserve(tmp_path, 2, progress='2' * 64, blocker_count=3)

    second = _reserve(tmp_path, 2, progress='2' * 64, blocker_count=1)
    assert second.ordinal == 2


def test_complete_ledger_cannot_be_reopened(tmp_path):
    _reserve(tmp_path, 1)
    stage2_review_ledger.mark_complete(
        tmp_path, '42', source_sha256=SOURCE, main_text_sha256=MAIN)

    with pytest.raises(
            stage2_review_ledger.Stage2ReviewBudgetRefused,
            match='已标 complete'):
        _reserve(tmp_path, 2, progress='2' * 64, blocker_count=1)


def test_hash_bound_no_wire_reconcile_preserves_history_and_restores_one_call(
        tmp_path):
    _legacy_false_reservation(tmp_path)
    for ordinal, purpose in ((2, 'correction'), (3, 'review')):
        turn = stage2_review_ledger.reserve(
            tmp_path, '42', source_sha256=SOURCE, main_text_sha256=MAIN,
            business_sha256=BUSINESS, workpack_sha256=WORKPACK,
            progress_sha256=str(ordinal) * 64, blocker_count=2,
            purpose=purpose, parent_run_id=NO_WIRE_PARENT,
            epoch_id=NO_WIRE_PARENT,
            session_run_id=f'stage2-real-session-{ordinal}', now=ordinal)
        stage2_review_ledger.finish(
            tmp_path, '42', source_sha256=SOURCE, main_text_sha256=MAIN,
            session_run_id=turn.session_run_id, exit_code=0, now=ordinal + 10)

    shown = stage2_review_ledger.inspect_no_wire_reconciliation(
        tmp_path, '42', session_run_id=NO_WIRE_SESSION)
    assert shown.active_calls == 3
    assert shown.remaining_calls == 1
    assert shown.already_reconciled is False

    first = stage2_review_ledger.reconcile_no_wire_reservation(
        tmp_path, '42', session_run_id=NO_WIRE_SESSION,
        actor='恢复值守员', reason='双账无 session 且日志命中已知 pre-spawn bug',
        subject_sha256=shown.subject_sha256, now=20)
    second = stage2_review_ledger.reconcile_no_wire_reservation(
        tmp_path, '42', session_run_id=NO_WIRE_SESSION,
        actor='恢复值守员', reason='双账无 session 且日志命中已知 pre-spawn bug',
        subject_sha256=shown.subject_sha256, now=30)
    assert first == second

    path = stage2_review_ledger.ledger_path(
        tmp_path, '42', source_sha256=SOURCE, main_text_sha256=MAIN)
    repaired = Stage2ReviewLedger.model_validate_json(path.read_bytes())
    assert repaired.state == 'active'
    assert [row.ordinal for row in repaired.turns] == [1, 2]
    assert [row.session_run_id for row in repaired.turns] == [
        'stage2-real-session-2', 'stage2-real-session-3']
    assert repaired.reconciliations == [first]
    assert first.removed_turn.session_run_id == NO_WIRE_SESSION

    final = stage2_review_ledger.reserve(
        tmp_path, '42', source_sha256=SOURCE, main_text_sha256=MAIN,
        business_sha256=BUSINESS, workpack_sha256=WORKPACK,
        progress_sha256='4' * 64, blocker_count=1, purpose='correction',
        parent_run_id='next-parent', epoch_id='next-epoch',
        session_run_id='stage2-real-session-4', now=40)
    assert final.ordinal == 3
    exhausted = Stage2ReviewLedger.model_validate_json(path.read_bytes())
    assert exhausted.state == 'exhausted'
    assert len(exhausted.turns) == 3
    assert len(exhausted.reconciliations) == 1


def test_no_wire_reconcile_refuses_subject_drift_or_any_wire_audit(tmp_path):
    _legacy_false_reservation(tmp_path)
    shown = stage2_review_ledger.inspect_no_wire_reconciliation(
        tmp_path, '42', session_run_id=NO_WIRE_SESSION)
    log = agent.run_log_path(tmp_path, NO_WIRE_PARENT)
    with log.open('a', encoding='utf-8') as handle:
        handle.write('迟到日志字节\n')
    with pytest.raises(TerminalState, match='subject SHA 已变化'):
        stage2_review_ledger.reconcile_no_wire_reservation(
            tmp_path, '42', session_run_id=NO_WIRE_SESSION,
            actor='恢复值守员', reason='旧主体已漂移',
            subject_sha256=shown.subject_sha256)

    audit = agent_session_audit.audit_path(tmp_path)
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(
        json.dumps({'session_run_id': NO_WIRE_SESSION}) + '\n',
        encoding='utf-8')
    with pytest.raises(TerminalState, match='不能把这次调用认定为 0-wire'):
        stage2_review_ledger.inspect_no_wire_reconciliation(
            tmp_path, '42', session_run_id=NO_WIRE_SESSION)


def test_no_wire_reconcile_cli_is_read_only_then_typed_apply(tmp_path, capsys):
    from novel.cli import main as cli

    _legacy_false_reservation(tmp_path)
    base = str(tmp_path)
    inspect_argv = [
        'reconcile', '--base', base,
        '--stage2-review-no-wire-session-run-id', NO_WIRE_SESSION,
        '--stage2-review-sid', '42',
    ]
    assert cli.main(inspect_argv) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown['active_calls'] == 1
    assert shown['remaining_calls_after_apply'] == 3
    assert shown['apply_requires'] == [
        '--actor', '--reason', '--subject-sha256']

    assert cli.main([
        *inspect_argv, '--actor', '恢复值守员',
        '--reason', '确认旧 admission 校验在 Popen 前失败',
        '--subject-sha256', shown['subject_sha256'],
    ]) == 0
    repaired = stage2_review_ledger.inspect_no_wire_reconciliation(
        tmp_path, '42', session_run_id=NO_WIRE_SESSION)
    assert repaired.already_reconciled is True
    assert repaired.active_calls == 0
