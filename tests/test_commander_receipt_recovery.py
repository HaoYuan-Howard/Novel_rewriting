"""Commander 已保存回答的成本与会话尾账恢复，全部使用合成原始事实。"""
from dataclasses import replace

import pytest

# REMOVED novel.agents import session_audit as audit
from novel.errors import ProvenanceRefused
from novel.llm import ledger
from tests.test_agent_session_audit import _end, _start, _turn


def _cost() -> dict:
    return dict(
        run_id='original-parent', session_run_id='original-session', sid='42',
        attempt_id='original-attempt', request_model='gpt-5.6-sol',
        requested_effort='high', code='original-code', protocol_version='session/v1',
        actual_model='gpt-5.6-sol', actual_effort='high',
        provider_session_id='original-provider', cli_version='test-cli',
        usage={'prompt_tokens': 12, 'completion_tokens': 5}, outcome='ok',
        exit_code=0, latency_ms=20, response_sha256='a' * 64)


def _events():
    common = dict(role='commander', node=None, chapter=None)
    start = replace(_start(), **common)
    turn = replace(_turn(), **common)
    attempt = replace(turn, kind='attempt', turn_index=None, delivery_attempts=1)
    end = replace(_end(), **common)
    return start, attempt, turn, end


def test_commander_cost_read_only_does_not_create_missing_evidence(tmp_path):
    before = list(tmp_path.iterdir())
    with pytest.raises(ProvenanceRefused, match='缺失'):
        ledger.reconcile_commander_call(tmp_path, read_only=True, **_cost())
    assert list(tmp_path.iterdir()) == before


def test_commander_cost_replay_is_idempotent_and_preserves_original_owner(tmp_path):
    kwargs = _cost()
    ledger.append_commander_call(tmp_path, **kwargs)
    original = ledger.ledger_path(tmp_path).read_bytes()
    ledger.reconcile_commander_call(tmp_path, **kwargs)
    ledger.reconcile_commander_call(tmp_path, read_only=True, **kwargs)
    assert ledger.ledger_path(tmp_path).read_bytes() == original
    for changes in ({'run_id': 'new-parent'}, {'response_sha256': 'b' * 64},
                    {'usage': {'prompt_tokens': 99}}, {'provider_session_id': None}):
        with pytest.raises(ProvenanceRefused, match='不一致'):
            ledger.reconcile_commander_call(tmp_path, **{**kwargs, **changes})
        assert ledger.ledger_path(tmp_path).read_bytes() == original


def test_commander_cost_missing_row_can_be_completed_once(tmp_path):
    ledger.reconcile_commander_call(tmp_path, **_cost())
    ledger.reconcile_commander_call(tmp_path, **_cost())
    [row] = ledger.read_jsonl_rows(ledger.ledger_path(tmp_path))
    assert row['run_id'] == 'original-parent'
    assert row['prompt_tokens'] == 12 and row['completion_tokens'] == 5
    assert row['response_sha256'] == 'a' * 64


def test_commander_session_receipt_completes_only_missing_events(tmp_path):
    start, attempt, turn, _ = _events()
    audit.append_event(tmp_path, start)
    original = audit.audit_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match='缺失'):
        audit.reconcile_commander_event(tmp_path, attempt, read_only=True)
    assert audit.audit_path(tmp_path).read_bytes() == original
    audit.reconcile_commander_event(tmp_path, attempt)
    audit.reconcile_commander_event(tmp_path, attempt)
    audit.reconcile_commander_event(tmp_path, turn)
    complete = audit.audit_path(tmp_path).read_bytes()
    for event in (attempt, turn):
        audit.reconcile_commander_event(tmp_path, event, read_only=True)
        audit.reconcile_commander_event(tmp_path, event)
    assert audit.audit_path(tmp_path).read_bytes() == complete
    [record] = audit.fold(audit.read_events(tmp_path))
    assert record.attempts == (attempt,) and record.turns == (turn,)


def test_commander_session_identity_drift_and_ended_tail_are_rejected(tmp_path):
    start, attempt, turn, end = _events()
    audit.append_event(tmp_path, start)
    audit.reconcile_commander_event(tmp_path, attempt)
    before = audit.audit_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match='不一致'):
        audit.reconcile_commander_event(tmp_path, replace(attempt, parent_run_id='other-parent'))
    assert audit.audit_path(tmp_path).read_bytes() == before
    audit.append_event(tmp_path, end)
    ended = audit.audit_path(tmp_path).read_bytes()
    with pytest.raises(ValueError, match='ended'):
        audit.reconcile_commander_event(tmp_path, turn)
    assert audit.audit_path(tmp_path).read_bytes() == ended


def test_commander_recovery_never_appends_after_an_uncommitted_tail(tmp_path):
    start, attempt, _, _ = _events()
    audit.append_event(tmp_path, start)
    for path in (audit.audit_path(tmp_path), ledger.ledger_path(tmp_path)):
        with path.open('ab') as stream:
            stream.write(b'{"interrupted":')
    before = {path: path.read_bytes() for path in (
        audit.audit_path(tmp_path), ledger.ledger_path(tmp_path))}
    with pytest.raises(ValueError, match='未提交尾行'):
        audit.reconcile_commander_event(tmp_path, attempt)
    with pytest.raises(ProvenanceRefused, match='未提交尾行'):
        ledger.reconcile_commander_call(tmp_path, **_cost())
    assert {path: path.read_bytes() for path in before} == before
