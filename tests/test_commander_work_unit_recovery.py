"""真实 Commander 状态机：完整回答与成本、会话尾账之间的崩溃窗。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel import agent_session_audit
from novel.orchestrator import agent
from novel import work_units
from tests.test_agent import (
    _PROFILES, _stage2_work_unit_harness, _install_stage5_live_batch_harness,
    base, corpus, no_real_commander_cli, stable_code_version,
)


@pytest.mark.parametrize('window', ['raw_after', 'cost_before', 'cost_after', 'attempt_before', 'turn_before', 'turn_after'])
def test_saved_commander_receipt_reconciles_without_wire(base, corpus, monkeypatch, window):
    progress, calls, budget_type = _stage2_work_unit_harness(base, monkeypatch, mode='accept')
    original_cost = agent.append_commander_call
    original_append = agent_session_audit.append_event
    original_turn = agent_session_audit.append_commander_turn
    original_write = work_units._write
    fault = {'raised': False, 'active': True}

    def trip(label):
        if label == window and not fault['raised']:
            fault['raised'] = True
            raise OSError('合成原回答保存后的尾账中断')

    def cost(*args, **kwargs):
        trip('cost_before')
        original_cost(*args, **kwargs)
        trip('cost_after')

    def event(base_arg, item):
        if item.kind == 'attempt':
            trip('attempt_before')
        return original_append(base_arg, item)

    def turn(*args, **kwargs):
        trip('turn_before')
        result = original_turn(*args, **kwargs)
        trip('turn_after')
        return result

    def write(base_arg, path, document):
        result = original_write(base_arg, path, document)
        if isinstance(document, work_units.WorkUnitBlobV1) and 'final_response' in document.payload:
            if window == 'raw_after' and fault['active']:
                # 整个 parent 的回调持续中断，恢复必须发生在下一次启动。
                fault['raised'] = True
                raise OSError('原回答已保存，当前进程的提交回调持续中断')
        return result

    monkeypatch.setattr(agent, 'append_commander_call', cost)
    monkeypatch.setattr(agent_session_audit, 'append_event', event)
    monkeypatch.setattr(agent_session_audit, 'append_commander_turn', turn)
    monkeypatch.setattr(work_units, '_write', write)
    first = agent.start(base, '42', authorized=True, egress_authorized=True, profiles=_PROFILES, spawn=None)
    assert agent.wait(first.run_id, 15)
    assert fault['raised'] and len(calls) == 1
    assert progress['applied'] == []
    fault['active'] = False
    second = agent.start(base, '42', authorized=True, egress_authorized=True, profiles=_PROFILES, spawn=None)
    assert agent.wait(second.run_id, 15)
    assert len(calls) == 1 and progress['applied'] == ['stage2.global']
    unit = budget_type(base, '42', 'stage2.global', 'commander', 'book')
    assert unit.used == 1 and unit.latest_turn().state == 'adopted'
    rows = [json.loads(line) for line in (Path(base) / '_service' / 'ledger.jsonl').read_text('utf-8').splitlines()]
    commander_rows = [row for row in rows if row.get('role') == 'commander']
    assert len(commander_rows) == 1 and commander_rows[0]['run_id'] == first.run_id
    sessions = agent_session_audit.fold(agent_session_audit.read_events(base))
    assert len(sessions) == 1 and len(sessions[0].turns) == 1
    assert len(sessions[0].attempts) == 1 and sessions[0].end is not None


def test_tampered_commander_cost_cannot_authorize_saved_decision(base, corpus, monkeypatch):
    progress, calls, budget_type = _stage2_work_unit_harness(base, monkeypatch, mode='crash')
    first = agent.start(base, '42', authorized=True, egress_authorized=True, profiles=_PROFILES, spawn=None)
    assert agent.wait(first.run_id, 15)
    cost_path = Path(base) / '_service' / 'ledger.jsonl'
    rows = [json.loads(line) for line in cost_path.read_text('utf-8').splitlines()]
    rows[0]['request_model'] = 'another-model'
    cost_path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    second = agent.start(base, '42', authorized=True, egress_authorized=True, profiles=_PROFILES, spawn=None)
    assert agent.wait(second.run_id, 15)
    assert len(calls) == 1 and progress['applied'] == ['stage2.global']
    [done] = [row for row in agent.records(base) if row.run_id == second.run_id]
    assert done.exit_code == 70
    unit = budget_type(base, '42', 'stage2.global', 'commander', 'book')
    assert unit.used == 1 and unit.latest_pending_response() is not None


def test_stage5_raw_response_tail_recovers_without_rebuying_authority(base, corpus, monkeypatch):
    """原回答已落但返回回调中断；重启仍能形成严格 Stage5 返工权威。"""
    harness = _install_stage5_live_batch_harness(base, monkeypatch, reviewer_rewrite=True)
    original_write = work_units._write
    fault = {'raised': False, 'active': True}

    def write(base_arg, path, document):
        result = original_write(base_arg, path, document)
        if (fault['active'] and isinstance(document, work_units.WorkUnitBlobV1)
                and 'final_response' in document.payload):
            fault['raised'] = True
            raise OSError('原回答 blob 已提交，交付状态回写前中断')
        return result

    monkeypatch.setattr(work_units, '_write', write)
    first = agent.start(base, '42', authorized=True, egress_authorized=True, profiles=_PROFILES, spawn=None)
    assert agent.wait(first.run_id, 15)
    assert fault['raised'] and len(harness.invocations) == 1 and harness.executions == []
    fault['active'] = False
    second = agent.start(base, '42', authorized=True, egress_authorized=True, profiles=_PROFILES, spawn=None)
    assert agent.wait(second.run_id, 15)
    assert len(harness.invocations) == len(harness.executions) == 1
    unit = work_units.WorkUnitBudget(base, '42', 'stage5.review', 'commander', 'closure')
    assert unit.used == 1 and unit.latest_turn().state == 'adopted'
    costs = [json.loads(line) for line in (Path(base) / '_service' / 'ledger.jsonl').read_text('utf-8').splitlines()]
    assert len([row for row in costs if row.get('role') == 'commander']) == 1
