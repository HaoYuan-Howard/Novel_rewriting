"""executor 轮确定性 wire 拒收的免费工程收口（R10c 2026-10-02）。

背景：旧 wire 把不可重试的 ``TransportError``（如 DeepSeek 402 拒付）记成
unknown，而该形状此前没有收口面——`novel/llm/paid_requests.py` 的
`unknown_turn` 闸会把同身份重购永久卡死（R10c c1 ch09 实证）。本文件钉住
`novel/orchestrator/work_unit_recovery.py` 的 executor 分支：
正例＝preview→apply 把已确认失败的 unknown 轮收口为 failed 并恢复可续跑；
反例＝非闭集种类、成本证据缺失、token 漂移均关闭式拒绝；结果真未知的
（read_timeout）不进闭集。wire 层「确定性拒收直接记 failed」的行为另见
`tests/test_str06_f7a_wiring.py`。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from contracts.schema.work_unit_recovery import CommanderFailureRecoveryRequest
from novel.llm.ledger import CostLedger, ledger_path
from novel.orchestrator import work_unit_recovery
from novel.work_units import (
    WorkUnitBudget, initialize_workflow, COMMANDER_FAILURE_RECONCILED,
)
from tests.test_work_unit_grants import _append_ended_agent_run

NODE = 'str06.stage4.bind'
SID = '42'


def _refused_unknown_turn(base: Path, *, reason_code: str = 'http_error',
                          record_cost_row: bool = True):
    """构造旧 wire 留下的「确定性拒收→unknown」最后一轮（无回答、有成本行）。"""
    initialize_workflow(base)
    run_id = 'run-refused-1'
    _append_ended_agent_run(base, run_id=run_id, epoch_id=run_id, sid=SID)
    ledger = CostLedger(path=ledger_path(base), run_id=run_id,
                        parent_run_id='cand-sess-1')
    budget = WorkUnitBudget(base, SID, NODE)
    fingerprint = hashlib.sha256(b'req-refused-1').hexdigest()
    turn = budget.reserve(
        fingerprint, run_id=run_id, agent_run_id=run_id, agent_epoch_id=run_id,
        candidate_session_run_id='cand-sess-1',
        request_payload={'prompt': 'p', 'schema_sha256': 'x'})
    budget.record_attempt(turn.turn_id, attempt_id='att-refused-1',
                          outcome='unknown', request_fingerprint=fingerprint)
    budget.finish(turn.turn_id, outcome='unknown', reason_code=reason_code)
    # executor 单元目录还带按 attempt 键名的接受分项记录（非摘要名）；
    # 收口预检须容忍这一正常产物族（R10c：Commander 时代助手的反例）。
    budget.save_acceptance(turn.turn_id, 'att-refused-1',
                           {'accepted': False, 'errors': ['http_error']})
    if record_cost_row:
        ledger.record(sid=SID, node=NODE, model='deepseek-flash', code='code-x',
                      cached=False, cache_policy='attempt', outcome='error',
                      error_kind=reason_code, purpose='bind',
                      attempt_id='att-refused-1', request_fingerprint=fingerprint,
                      latency_ms=5, will_retry=False)
    return budget, turn


def test_refused_unknown_turn_closes_to_failed_and_unblocks_new_round(tmp_path):
    base = tmp_path / 'out'
    base.mkdir()
    budget, turn = _refused_unknown_turn(base)
    view = work_unit_recovery.inspect(base, SID, budget.unit_id, turn.turn_id)
    assert view.available, view.reason
    assert view.before.state == 'unknown' and view.before.reason_code == 'http_error'

    receipt = work_unit_recovery.apply(base, SID, budget.unit_id, turn.turn_id,
        CommanderFailureRecoveryRequest(preview_token=view.preview_token,
                                        actor='开发维护 agent',
                                        reason='DeepSeek 402 拒付（request_id 实证），服务端未处理该请求'))
    assert receipt.kind == 'executor-wire-refusal-reconciliation-v1'
    closed = budget._read().turns[-1]
    assert closed.state == 'failed'
    assert closed.reason_code == COMMANDER_FAILURE_RECONCILED
    # 收口后同一请求身份可再取新内容轮——unknown 楔子解除。
    again = budget.reserve(hashlib.sha256(b'req-refused-1').hexdigest(),
                           run_id='run-next')
    assert again.ordinal == 2
    # 已收口轮的复验：每次 _read 都重新验证收口证明（幂等重放同一请求）。
    replay = work_unit_recovery.apply(base, SID, budget.unit_id, turn.turn_id,
        CommanderFailureRecoveryRequest(preview_token=view.preview_token,
                                        actor='开发维护 agent',
                                        reason='DeepSeek 402 拒付（request_id 实证），服务端未处理该请求'))
    assert replay == receipt


def test_genuinely_unknown_outcomes_stay_out_of_closed_set(tmp_path):
    base = tmp_path / 'out'
    base.mkdir()
    budget, turn = _refused_unknown_turn(base, reason_code='read_timeout')
    view = work_unit_recovery.inspect(base, SID, budget.unit_id, turn.turn_id)
    assert not view.available and '闭集' in view.reason


def test_missing_cost_evidence_refuses_closure(tmp_path):
    base = tmp_path / 'out'
    base.mkdir()
    budget, turn = _refused_unknown_turn(base, record_cost_row=False)
    view = work_unit_recovery.inspect(base, SID, budget.unit_id, turn.turn_id)
    assert not view.available


def test_drifted_preview_token_refuses_apply(tmp_path):
    base = tmp_path / 'out'
    base.mkdir()
    budget, turn = _refused_unknown_turn(base)
    view = work_unit_recovery.inspect(base, SID, budget.unit_id, turn.turn_id)
    assert view.available
    with pytest.raises(Exception, match='漂移'):
        work_unit_recovery.apply(base, SID, budget.unit_id, turn.turn_id,
            CommanderFailureRecoveryRequest(
                preview_token='0' * 64, actor='开发维护 agent', reason='伪造 token'))
    assert budget._read().turns[-1].state == 'unknown', '拒绝后不得改动原轮'


def test_cli_preview_reports_json(tmp_path, capsys):
    base = tmp_path / 'out'
    base.mkdir()
    budget, turn = _refused_unknown_turn(base)
    rc = work_unit_recovery.main([
        '--base', str(base), '--sid', SID,
        '--unit-id', budget.unit_id, '--turn-id', turn.turn_id])
    assert rc == 0
    view = json.loads(capsys.readouterr().out)
    assert view['available'] and view['before']['state'] == 'unknown'
