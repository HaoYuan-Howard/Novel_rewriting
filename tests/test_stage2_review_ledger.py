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
    blocker_count: int = 2, purpose: str | None = None,
):
    return stage2_review_ledger.reserve(
        base, '42', source_sha256=SOURCE, main_text_sha256=MAIN,
        business_sha256=BUSINESS, workpack_sha256=WORKPACK,
        progress_sha256=progress or str(ordinal) * 64,
        blocker_count=blocker_count,
        purpose=purpose or ('review' if ordinal == 1 else 'correction'),
        parent_run_id=f'parent-{ordinal}', epoch_id='epoch-1',
        session_run_id=f'stage2-session-{ordinal}', now=ordinal)


def _ledger(base) -> Stage2ReviewLedger:
    path = stage2_review_ledger.ledger_path(
        base, '42', source_sha256=SOURCE, main_text_sha256=MAIN)
    return Stage2ReviewLedger.model_validate_json(path.read_bytes())


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


def test_started_turn_consumes_persistent_seat_and_fourth_seat_is_final_only(
        tmp_path):
    first = _reserve(tmp_path, 1)
    assert first.outcome == 'started'
    stage2_review_ledger.finish(
        tmp_path, '42', source_sha256=SOURCE, main_text_sha256=MAIN,
        session_run_id=first.session_run_id, exit_code=0, now=11)
    _reserve(tmp_path, 2)
    _reserve(tmp_path, 3, blocker_count=0)

    ledger = _ledger(tmp_path)
    assert ledger.schema_version == 2
    assert ledger.state == 'active', '前三席用完仍留第四席给只读终审'
    assert [row.ordinal for row in ledger.turns] == [1, 2, 3]
    assert len({row.session_run_id for row in ledger.turns}) == 3
    assert ledger.turns[1].outcome == 'started', '崩溃前 started 也已消费第二席'

    with pytest.raises(
            stage2_review_ledger.Stage2ReviewBudgetRefused,
            match='只保留给只读 final_review'):
        _reserve(tmp_path, 4, progress='4' * 64, blocker_count=0)
    with pytest.raises(
            stage2_review_ledger.Stage2ReviewBudgetRefused,
            match='仍有未闭合'):
        _reserve(tmp_path, 4, progress='4' * 64, blocker_count=1,
                 purpose='final_review')

    final = _reserve(
        tmp_path, 4, progress='4' * 64, blocker_count=0,
        purpose='final_review')
    assert final.ordinal == 4
    assert _ledger(tmp_path).state == 'exhausted'
    with pytest.raises(
            stage2_review_ledger.Stage2ReviewBudgetRefused,
            match='用满 4 席'):
        _reserve(tmp_path, 5, progress='5' * 64, blocker_count=0,
                 purpose='final_review')


def test_final_review_reopen_allows_blocker_growth_then_one_more_correction(
        tmp_path):
    """review → 终审 reopen → 订正 → 终审：reopen 造成的未闭合回升不是空转。"""
    _reserve(tmp_path, 1, blocker_count=1)
    final_first = _reserve(
        tmp_path, 2, blocker_count=0, purpose='final_review')
    assert final_first.ordinal == 2
    correction = _reserve(tmp_path, 3, blocker_count=1, purpose='correction')
    assert correction.ordinal == 3, '终审之后允许 blocker 从 0 回到 1'
    with pytest.raises(
            stage2_review_ledger.Stage2ReviewBudgetRefused,
            match='只保留给只读 final_review'):
        _reserve(tmp_path, 4, blocker_count=1, purpose='correction')
    final_second = _reserve(
        tmp_path, 4, blocker_count=0, purpose='final_review')
    assert final_second.ordinal == 4
    assert _ledger(tmp_path).state == 'exhausted'


def test_attempts_share_one_seat_and_never_reserve_again(tmp_path):
    seat = _reserve(tmp_path, 1)
    kwargs = dict(
        source_sha256=SOURCE, main_text_sha256=MAIN,
        session_run_id=seat.session_run_id)
    launched = stage2_review_ledger.record_attempt(
        tmp_path, '42', attempt_id='att-1', kind='launch', now=2, **kwargs)
    assert [row.kind for row in launched.attempts] == ['launch']
    with pytest.raises(
            stage2_review_ledger.Stage2ReviewBudgetRefused,
            match='attempt_id 重复'):
        stage2_review_ledger.record_attempt(
            tmp_path, '42', attempt_id='att-1', kind='launch', now=3, **kwargs)
    finished = stage2_review_ledger.finish_attempt(
        tmp_path, '42', attempt_id='att-1', exit_code=1,
        error_kind='connection', provider_session_id='thread-1',
        wire_evidence=True, now=4, **kwargs)
    assert finished.attempts[0].exit_code == 1
    assert finished.attempts[0].wire_evidence is True
    resumed = stage2_review_ledger.record_attempt(
        tmp_path, '42', attempt_id='att-2', kind='resume',
        provider_session_id='thread-1', now=5, **kwargs)
    assert [row.kind for row in resumed.attempts] == ['launch', 'resume']
    assert len(_ledger(tmp_path).turns) == 1, 'resume 不占新席'
    with pytest.raises(
            stage2_review_ledger.Stage2ReviewBudgetRefused,
            match='已有冲突终态'):
        stage2_review_ledger.finish_attempt(
            tmp_path, '42', attempt_id='att-1', exit_code=0,
            error_kind=None, provider_session_id=None,
            wire_evidence=True, now=6, **kwargs)
    stage2_review_ledger.finish(
        tmp_path, '42', source_sha256=SOURCE, main_text_sha256=MAIN,
        session_run_id=seat.session_run_id, exit_code=0, now=7)
    with pytest.raises(
            stage2_review_ledger.Stage2ReviewBudgetRefused,
            match='已终态'):
        stage2_review_ledger.record_attempt(
            tmp_path, '42', attempt_id='att-3', kind='launch', now=8, **kwargs)


def _write_v1_ledger(base, *, parent_run_id: str) -> None:
    path = stage2_review_ledger.ledger_path(
        base, '42', source_sha256=SOURCE, main_text_sha256=MAIN)
    path.parent.mkdir(parents=True, exist_ok=True)
    turns = []
    for ordinal, purpose in ((1, 'prereview'), (2, 'review'), (3, 'correction')):
        turns.append({
            'ordinal': ordinal, 'purpose': purpose,
            'parent_run_id': parent_run_id, 'epoch_id': parent_run_id,
            'session_run_id': f'v1-session-{ordinal}',
            'business_sha256': BUSINESS, 'workpack_sha256': WORKPACK,
            'progress_sha256': str(ordinal) * 64, 'blocker_count': 3 - ordinal,
            'started_at': ordinal, 'finished_at': ordinal + 1,
            'exit_code': 0, 'outcome': 'ok',
        })
    path.write_text(json.dumps({
        'schema_version': 1, 'kind': 'stage2-global-review-ledger',
        'sid': '42', 'source_sha256': SOURCE, 'main_text_sha256': MAIN,
        'max_calls': 3, 'state': 'exhausted', 'turns': turns,
        'reconciliations': [],
    }, ensure_ascii=False, indent=2), encoding='utf-8')


def test_v1_three_seat_ledger_is_refused_until_explicitly_archived(tmp_path):
    _ended_parent_with_known_log(tmp_path)
    _write_v1_ledger(tmp_path, parent_run_id=NO_WIRE_PARENT)

    with pytest.raises(
            stage2_review_ledger.Stage2ReviewBudgetRefused,
            match='旧席位账'):
        _reserve(tmp_path, 1)

    shown = stage2_review_ledger.list_ledgers(tmp_path, '42')
    assert len(shown) == 1
    assert shown[0]['schema_version'] == 1
    assert shown[0]['max_calls'] == 3
    assert [row['purpose'] for row in shown[0]['turns']] == [
        'prereview', 'review', 'correction']

    archived = stage2_review_ledger.reopen_archive_by_sha(
        tmp_path, '42', original_sha256=shown[0]['original_sha256'],
        actor='恢复值守员', reason='旧三席契约已升四席；归档后续跑', now=50)
    assert archived.original_schema_version == 1
    assert archived.original_max_calls == 3
    assert len(archived.turns) == 3

    fresh = _ledger(tmp_path)
    assert fresh.schema_version == 2
    assert fresh.turns == []
    assert fresh.state == 'active'
    assert [row.actor for row in fresh.archived_ledgers] == ['恢复值守员']
    assert _reserve(tmp_path, 1).ordinal == 1

    with pytest.raises(TerminalState, match='命中 0 份'):
        stage2_review_ledger.reopen_archive_by_sha(
            tmp_path, '42', original_sha256=shown[0]['original_sha256'],
            actor='恢复值守员', reason='重复归档')


def test_reopen_refuses_started_seats_or_live_parent_runs(tmp_path):
    seat = _reserve(tmp_path, 1)
    with pytest.raises(TerminalState, match='仍有 started 席位'):
        stage2_review_ledger.reopen_archive(
            tmp_path, '42', source_sha256=SOURCE, main_text_sha256=MAIN,
            actor='恢复值守员', reason='试图归档运行中的席位')
    stage2_review_ledger.finish(
        tmp_path, '42', source_sha256=SOURCE, main_text_sha256=MAIN,
        session_run_id=seat.session_run_id, exit_code=70, now=2)
    with pytest.raises(TerminalState, match='不在 agent_runs 记录中'):
        stage2_review_ledger.reopen_archive(
            tmp_path, '42', source_sha256=SOURCE, main_text_sha256=MAIN,
            actor='恢复值守员', reason='parent 未登记')


def test_reopen_cli_is_read_only_then_typed_apply(tmp_path, capsys):
    from novel.cli import main as cli

    _ended_parent_with_known_log(tmp_path)
    _write_v1_ledger(tmp_path, parent_run_id=NO_WIRE_PARENT)
    base = str(tmp_path)
    inspect_argv = [
        'reconcile', '--base', base,
        '--stage2-review-reopen', '--stage2-review-sid', '42',
    ]
    assert cli.main(inspect_argv) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown['ledgers'][0]['schema_version'] == 1
    assert shown['apply_requires'][0] == '--actor'

    assert cli.main([
        *inspect_argv, '--actor', '恢复值守员',
        '--reason', '四席契约升版后归档旧账',
        '--subject-sha256', shown['ledgers'][0]['original_sha256'],
    ]) == 0
    assert '已整体归档' in capsys.readouterr().out
    fresh = _ledger(tmp_path)
    assert fresh.turns == [] and len(fresh.archived_ledgers) == 1


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
    assert shown.remaining_calls == 2
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
    after = Stage2ReviewLedger.model_validate_json(path.read_bytes())
    assert after.state == 'active', '第三席用完仍留第四席给终审'
    assert len(after.turns) == 3
    assert len(after.reconciliations) == 1


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
    assert shown['remaining_calls_after_apply'] == 4
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
