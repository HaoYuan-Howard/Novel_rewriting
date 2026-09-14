"""旧 Stage2 席位账仅供历史审计，不能恢复、退款或重建现役额度。"""
from __future__ import annotations

import hashlib
import json

import pytest

from novel.cli.main import _parser
from novel.orchestrator import stage2_review_ledger as audit
from novel.work_units import WorkUnitBudget, WorkUnitBudgetExhausted, WorkUnitRefused, initialize_workflow


def _legacy(base, version=2):
    path = audit.ledger_path(base, '42', source_sha256='a' * 64, main_text_sha256='b' * 64)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'schema_version': version, 'sid': '42', 'source_sha256': 'a' * 64,
        'main_text_sha256': 'b' * 64, 'max_calls': 3 if version == 1 else 4,
        'state': 'exhausted', 'turns': [{
            'ordinal': 1, 'purpose': 'review', 'parent_run_id': 'old-parent',
            'epoch_id': 'old-epoch', 'session_run_id': 'old-session',
            'business_sha256': 'c' * 64, 'workpack_sha256': 'd' * 64,
            'progress_sha256': 'e' * 64, 'blocker_count': 2,
            'started_at': 1, 'finished_at': 2, 'exit_code': 70, 'outcome': 'failed',
        }],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    return path


@pytest.mark.parametrize('version', [1, 2])
def test_old_audit_is_read_only_and_keeps_original_unknown_call(tmp_path, version):
    path = _legacy(tmp_path, version)
    original = path.read_bytes()
    [shown] = audit.list_ledgers(tmp_path, '42')
    assert shown['read_only'] and shown['retired']
    assert shown['schema_version'] == version
    assert shown['original_sha256'] == hashlib.sha256(original).hexdigest()
    assert shown['turns'][0]['exit_code'] == 70
    assert path.read_bytes() == original
    with pytest.raises(WorkUnitRefused, match='旧运行记录'):
        initialize_workflow(tmp_path)
    assert path.read_bytes() == original


@pytest.mark.parametrize('name', [
    'reserve', 'finish', 'record_attempt', 'finish_attempt', 'record_rejection',
    'mark_complete', 'reopen_archive', 'reopen_archive_by_sha',
    'reconcile_no_wire_reservation', 'inspect_no_wire_reconciliation',
])
def test_old_ledger_has_no_mutating_or_recovery_entry(name):
    assert not hasattr(audit, name)


@pytest.mark.parametrize('flag', [
    '--stage2-review-no-wire-session-run-id', '--stage2-review-reopen', '--stage2-review-sid',
])
def test_cli_rejects_retired_stage2_recovery_flags_without_touching_history(tmp_path, flag):
    path = _legacy(tmp_path)
    original = path.read_bytes()
    with pytest.raises(SystemExit) as result:
        _parser().parse_args(['reconcile', '--base', str(tmp_path), flag])
    assert result.value.code == 2
    assert path.read_bytes() == original


def test_replacing_old_audit_cannot_reset_current_work_unit(tmp_path):
    unit = WorkUnitBudget(tmp_path, '42', 'stage2.global', role='executor', scope='book')
    for index in range(4):
        turn = unit.reserve(hashlib.sha256(str(index).encode()).hexdigest(), run_id=f'parent-{index}')
        unit.finish(turn.turn_id, outcome='failed', reason_code='typed_invalid')
    ledger_before = unit.path.read_bytes()
    path = _legacy(tmp_path)
    for version in (1, 2):
        payload = json.loads(path.read_bytes())
        payload.update(schema_version=version, turns=[], state='active')
        path.write_text(json.dumps(payload), encoding='utf-8')
        with pytest.raises(WorkUnitBudgetExhausted):
            WorkUnitBudget(tmp_path, '42', 'stage2.global', role='executor', scope='book').reserve(
                'f' * 64, run_id='attempted-budget-reset')
        assert unit.path.read_bytes() == ledger_before
        assert unit.used == 4


def test_corrupt_old_audit_is_unknown_and_never_repaired_by_read(tmp_path):
    path = _legacy(tmp_path)
    path.write_bytes(b'{"schema_version":2,"turns":')
    original = path.read_bytes()
    with pytest.raises(audit.Stage2ReviewBudgetRefused):
        audit.list_ledgers(tmp_path, '42')
    assert path.read_bytes() == original
