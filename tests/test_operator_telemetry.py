#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Commander/operator 阶段遥测：独立账、隐私边界、折叠与 skill 薄入口。"""
from __future__ import annotations

import json

import pytest

from novel import operator_telemetry as T


SID = '2007780338055790990'
SESSION = '11111111-1111-4111-8111-111111111111'
PHASE_A = '22222222-2222-4222-8222-222222222222'
PHASE_B = '33333333-3333-4333-8333-333333333333'
RUN_ID = '44444444-4444-4444-8444-444444444444'
OTHER_SESSION = '55555555-5555-4555-8555-555555555555'
SHA_A = 'a' * 64
SHA_B = 'b' * 64


def _start(base, *, session_id=SESSION):
    return T.start_session(
        base, SID, start_code_version='code-v1', source='interactive',
        session_id=session_id, started_at_ms=100_000)


def test_append_fold_and_summary_are_separate_from_node_cost_and_provider_logs(tmp_path):
    _start(tmp_path)
    T.begin_phase(
        tmp_path, SESSION, phase='review', action='stage6.semantic',
        run_id=RUN_ID, evidence_sha256=SHA_A,
        phase_execution_id=PHASE_A, started_at_ms=101_000)
    T.finish_phase(
        tmp_path, SESSION, PHASE_A, state='completed',
        evidence_sha256=SHA_B, ended_at_ms=103_500,
        measured_elapsed_ms=2_400)
    T.begin_phase(
        tmp_path, SESSION, phase='approve', action='stage6.approve',
        phase_execution_id=PHASE_B, started_at_ms=104_000)
    T.finish_phase(
        tmp_path, SESSION, PHASE_B, state='completed', exit_code=0,
        ended_at_ms=104_750)
    T.finish_session(
        tmp_path, SESSION, state='completed', end_code_version='code-v1',
        ended_at_ms=105_000)

    service = tmp_path / '_service'
    assert [path.name for path in service.iterdir()] == ['operator_phases']
    assert sorted(path.name for path in T.telemetry_dir(tmp_path).iterdir()) == [
        f'{SESSION}.jsonl', f'{SESSION}.jsonl.lock']
    assert not any((service / name).exists() for name in (
        'ledger.jsonl', 'agent_progress.jsonl', 'agent_sessions.jsonl'))

    [record] = T.fold(T.read_events(tmp_path))
    assert record.session_id == SESSION
    assert record.state == 'completed' and record.elapsed_ms == 5_000
    assert [phase.phase for phase in record.phases] == ['review', 'approve']
    assert record.phases[0].elapsed_ms == 2_400
    assert record.phases[0].duration_source == 'monotonic'
    assert record.phases[1].elapsed_ms == 750
    assert record.phases[1].duration_source == 'wall_clock'

    summary = T.summarize(tmp_path, SID, started_at=100)
    assert summary['schema'] == 'novel-operator-phase-summary-v1'
    assert summary['session_count'] == 1
    assert summary['phase_count'] == summary['completed_phase_count'] == 2
    assert summary['unfinished_phase_count'] == 0
    assert summary['measured_phase_elapsed_ms_sum'] == 3_150
    assert summary['sessions'][0]['code_version_match'] is True
    assert summary['model_usage_state'] == 'unavailable_host_boundary'

    encoded = T.telemetry_path(tmp_path, SESSION).read_text('utf-8')
    assert encoded.endswith('\n') and '\r\n' not in encoded
    assert all(word not in encoded for word in (
        'prompt_tokens', 'completion_tokens', 'model', 'price', 'argv',
        'stdout', 'content'))


def test_no_session_is_unknown_not_zero_and_carries_no_usage_numbers(tmp_path):
    summary = T.summarize(tmp_path, SID, started_at=100)
    assert summary['session_count'] == 0
    assert summary['attributed_wall_ms'] is None
    assert summary['unattributed_wall_ms'] is None
    assert summary['model_usage_state'] == 'unavailable_host_boundary'
    encoded = json.dumps(summary, ensure_ascii=False)
    assert 'prompt_tokens' not in encoded
    assert 'completion_tokens' not in encoded
    assert 'price' not in encoded
    assert 'cost' not in encoded


def test_completed_session_refuses_open_phase_without_polluting_append_log(tmp_path):
    _start(tmp_path)
    T.begin_phase(
        tmp_path, SESSION, phase='review', action='stage6.semantic',
        phase_execution_id=PHASE_A, started_at_ms=101_000)
    path = T.telemetry_path(tmp_path, SESSION)
    before = path.read_bytes()

    with pytest.raises(ValueError, match='仍有未结束'):
        T.finish_session(
            tmp_path, SESSION, state='completed', end_code_version='code-v1',
            ended_at_ms=102_000)

    assert path.read_bytes() == before
    summary = T.summarize(tmp_path, SID)
    assert summary['unfinished_phase_count'] == 1
    assert summary['sessions'][0]['state'] is None
    assert summary['sessions'][0]['attributed_wall_ms'] is None
    assert summary['sessions'][0]['unattributed_wall_ms'] is None
    assert summary['attributed_wall_ms'] is None
    assert summary['unattributed_wall_ms'] is None


def test_failed_phase_requires_sanitized_kind_and_duplicate_end_is_refused(tmp_path):
    _start(tmp_path)
    T.begin_phase(
        tmp_path, SESSION, phase='verify', action='delivery.verify',
        phase_execution_id=PHASE_A, started_at_ms=101_000)
    with pytest.raises(ValueError, match='error_kind'):
        T.finish_phase(
            tmp_path, SESSION, PHASE_A, state='failed', ended_at_ms=102_000)
    with pytest.raises(Exception, match='error_kind'):
        T.finish_phase(
            tmp_path, SESSION, PHASE_A, state='failed',
            error_kind='这里不能放异常正文', ended_at_ms=102_000)

    T.finish_phase(
        tmp_path, SESSION, PHASE_A, state='failed',
        error_kind='delivery_mismatch', ended_at_ms=102_000)
    before = T.telemetry_path(tmp_path, SESSION).read_bytes()
    with pytest.raises(ValueError, match='已结束'):
        T.finish_phase(
            tmp_path, SESSION, PHASE_A, state='failed',
            error_kind='delivery_mismatch', ended_at_ms=103_000)
    assert T.telemetry_path(tmp_path, SESSION).read_bytes() == before


def test_action_and_evidence_cannot_become_a_content_side_channel(tmp_path):
    _start(tmp_path)
    with pytest.raises(Exception, match='action'):
        T.begin_phase(
            tmp_path, SESSION, phase='review', action='她其实是凶手',
            phase_execution_id=PHASE_A)
    with pytest.raises(Exception, match='evidence_sha256'):
        T.begin_phase(
            tmp_path, SESSION, phase='review', action='stage6.semantic',
            evidence_sha256='候选正文', phase_execution_id=PHASE_A)
    assert len(T.read_events(tmp_path)) == 1


def test_reader_only_tolerates_a_truncated_last_line(tmp_path):
    _start(tmp_path)
    path = T.telemetry_path(tmp_path, SESSION)
    with open(path, 'ab') as handle:
        handle.write(b'{"wire_version":1,"session_id"')
    truncated = path.read_bytes()
    assert len(T.read_events(tmp_path)) == 1

    # 读侧保留 unknown 不等于写侧可以把下一条 JSON 拼在半截后面；该 session
    # 必须关闭式停住，调用方另开 session，旧坏尾一个字节也不改。
    with pytest.raises(ValueError, match='截断'):
        T.begin_phase(
            tmp_path, SESSION, phase='inspect', action='state',
            phase_execution_id=PHASE_A)
    assert path.read_bytes() == truncated

    # 完整坏行一旦落在中间就关闭式失败。
    path.write_bytes(b'{"broken":true}\n' + path.read_bytes())
    with pytest.raises(ValueError):
        T.read_events(tmp_path)


def test_newline_is_the_event_commit_marker_even_when_json_is_complete(tmp_path):
    _start(tmp_path)
    path = T.telemetry_path(tmp_path, SESSION)
    committed = path.read_bytes()
    path.write_bytes(committed.rstrip(b'\n'))

    assert T.read_events(tmp_path) == []
    assert T._decode_session_start(path) is None
    with pytest.raises(ValueError, match='截断'):
        T.begin_phase(
            tmp_path, SESSION, phase='inspect', action='state',
            phase_execution_id=PHASE_A)


def test_reader_does_not_mistake_a_newline_terminated_bad_tail_for_a_kill(tmp_path):
    _start(tmp_path)
    path = T.telemetry_path(tmp_path, SESSION)
    with open(path, 'ab') as handle:
        handle.write(b'{"broken":true}\n')

    with pytest.raises(ValueError):
        T.read_events(tmp_path)


def test_reader_refuses_a_non_lf_boundary_before_the_tail(tmp_path):
    _start(tmp_path)
    path = T.telemetry_path(tmp_path, SESSION)
    committed = path.read_bytes()
    path.write_bytes(committed.rstrip(b'\n') + b'\r' + committed)

    with pytest.raises(ValueError, match='不是 LF 提交'):
        T.read_events(tmp_path)


def test_summary_session_probe_distinguishes_truncated_utf8_from_complete_bad_line(
        tmp_path):
    path = T.telemetry_path(tmp_path, OTHER_SESSION)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'{"sid":"\xe4')
    assert T._decode_session_start(path) is None

    path.write_bytes(b'{"sid":"\xe4\n')
    with pytest.raises(UnicodeDecodeError):
        T._decode_session_start(path)


def test_event_wire_rejects_unknown_fields_and_false_precision():
    event = T.OperatorPhaseEvent(
        session_id=SESSION, sid=SID, source='interactive',
        kind='session_started', ts_ms=1, start_code_version='code-v1')
    payload = event.model_dump(mode='json')
    payload['prompt'] = '正文'
    with pytest.raises(Exception, match='prompt'):
        T.OperatorPhaseEvent.model_validate(payload)

    with pytest.raises(ValueError, match='elapsed_ms'):
        T.OperatorPhaseEvent(
            session_id=SESSION, sid=SID, source='interactive',
            kind='phase_ended', ts_ms=2,
            phase_execution_id=PHASE_A, phase='review', state='completed')


def test_cli_surface_returns_session_and_phase_ids_for_skill_reuse(tmp_path, capsys):
    rc = T.cli_main([
        'session-start', '--base', str(tmp_path), '--sid', SID,
        '--start-code-version', 'code-v1', '--started-at', '100',
    ])
    assert rc == 0
    session = json.loads(capsys.readouterr().out)
    session_id = session['session_id']

    rc = T.cli_main([
        'phase-start', '--base', str(tmp_path), '--session-id', session_id,
        '--phase', 'review', '--action', 'stage6.semantic',
    ])
    assert rc == 0
    phase = json.loads(capsys.readouterr().out)
    assert phase['phase_execution_id']

    rc = T.cli_main([
        'phase-end', '--base', str(tmp_path), '--session-id', session_id,
        '--phase-execution-id', phase['phase_execution_id'],
        '--state', 'completed',
    ])
    assert rc == 0
    capsys.readouterr()

    rc = T.cli_main([
        'summary', '--base', str(tmp_path), '--sid', SID,
        '--started-at', '100', '--session-id', session_id,
    ])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary['session_count'] == 1
    assert summary['completed_phase_count'] == 1


def test_session_start_inherits_outer_commander_run_by_default(
        tmp_path, capsys, monkeypatch):
    monkeypatch.setenv(T.run_identity.RUN_ID_ENV, 'outer-commander-1')

    assert T.cli_main([
        'session-start', '--base', str(tmp_path), '--sid', SID,
        '--start-code-version', 'code-v1',
    ]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload['parent_run_id'] == 'outer-commander-1'
    [session] = T.fold(T.read_events(tmp_path))
    assert session.parent_run_id == 'outer-commander-1'


def test_summary_does_not_call_phase_sum_session_wall_when_phases_overlap(tmp_path):
    _start(tmp_path)
    T.begin_phase(
        tmp_path, SESSION, phase='inspect', action='state',
        phase_execution_id=PHASE_A, started_at_ms=101_000)
    T.begin_phase(
        tmp_path, SESSION, phase='inspect', action='inbox',
        phase_execution_id=PHASE_B, started_at_ms=101_500)
    T.finish_phase(
        tmp_path, SESSION, PHASE_A, state='completed', ended_at_ms=103_000)
    T.finish_phase(
        tmp_path, SESSION, PHASE_B, state='completed', ended_at_ms=103_500)
    T.finish_session(
        tmp_path, SESSION, state='completed', end_code_version='code-v1',
        ended_at_ms=104_000)

    summary = T.summarize(tmp_path, SID)
    assert summary['measured_phase_elapsed_ms_sum'] == 4_000
    assert summary['sessions'][0]['elapsed_ms'] == 4_000
    assert summary['sessions'][0]['attributed_wall_ms'] == 2_500
    assert summary['sessions'][0]['unattributed_wall_ms'] == 1_500
    assert summary['attributed_wall_ms'] == 2_500
    assert summary['unattributed_wall_ms'] == 1_500


def test_reversed_event_timestamps_are_refused_without_zero_washing(tmp_path):
    _start(tmp_path)
    with pytest.raises(ValueError, match='早于 session_started'):
        T.begin_phase(
            tmp_path, SESSION, phase='inspect', action='state',
            phase_execution_id=PHASE_A, started_at_ms=99_999)
    T.begin_phase(
        tmp_path, SESSION, phase='inspect', action='state',
        phase_execution_id=PHASE_A, started_at_ms=101_000)
    with pytest.raises(ValueError, match='不能早于'):
        T.finish_phase(
            tmp_path, SESSION, PHASE_A, state='completed', ended_at_ms=100_999)
    T.finish_phase(
        tmp_path, SESSION, PHASE_A, state='completed', ended_at_ms=102_000)
    with pytest.raises(ValueError, match='早于 session_started/phase_ended'):
        T.finish_session(
            tmp_path, SESSION, state='completed', end_code_version='code-v1',
            ended_at_ms=101_999)


def test_phase_append_reads_only_the_current_session_not_historical_logs(
        tmp_path, monkeypatch):
    """长期规模不随历史 session 增长；全量枚举只发生在只读终审 summary。"""
    _start(tmp_path)
    _start(tmp_path, session_id=OTHER_SESSION)
    # 历史 session 即使很大，也不该进入当前 session 的追加临界区。
    old_path = T.telemetry_path(tmp_path, OTHER_SESSION)
    with open(old_path, 'ab') as handle:
        handle.write((b'{"historical-padding":"not-a-current-session-read"}\n') * 1000)

    original = T._decode_lines
    seen = []

    def observed(path):
        seen.append(path)
        return original(path)

    monkeypatch.setattr(T, '_decode_lines', observed)
    T.begin_phase(
        tmp_path, SESSION, phase='inspect', action='state',
        phase_execution_id=PHASE_A, started_at_ms=101_000)

    assert seen == [T.telemetry_path(tmp_path, SESSION)]
