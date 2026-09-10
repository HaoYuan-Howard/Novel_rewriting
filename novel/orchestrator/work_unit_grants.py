"""工作单元人工追加：只读预览、同书互斥、原 owner 停止证明与锁内 CAS。

只追加现有工作单元的不可变授权，不清理轮次，不改费用、语义决定或原配置。
模型工作包、CLI 生成参数和 Supervisor 均没有自动批准接口。
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
import time
from uuid import uuid4

from contracts.invariants import WORK_UNIT_GRANT_ADDITIONS
from contracts.schema.api import (
    WorkUnitCostView, WorkUnitGrantPreview, WorkUnitGrantRequest, WorkUnitGrantResult,
)
from contracts.schema.orchestration import AgentRunEvent
from contracts.schema.workflow import BudgetGrant, WorkUnitLedgerV2
from novel import agent_session_audit, paths, prov, work_units
from novel.appendlog import path_transaction_lock
from novel.errors import TerminalState
from novel.llm.ledger import ledger_path, read_strict_jsonl_objects
from novel.orchestrator import admission, approvals, beacon, jobs
from novel.orchestrator.inspection import read_surface_seal


def _rows(path: Path, label: str) -> list[dict]:
    return read_strict_jsonl_objects(path, label=label) if path.is_file() else []


def _load(base: str | Path, sid: str, unit_id: str) -> work_units.WorkUnitBudget:
    path = paths.work_unit_dir(base, sid, unit_id) / 'ledger.json'
    ledger = work_units._read(base, path, WorkUnitLedgerV2)
    budget = work_units.WorkUnitBudget(base, sid, ledger.node, ledger.role, ledger.scope)
    if budget.unit_id != unit_id or unit_id not in budget._registry().unit_ids:
        raise work_units.WorkUnitRefused('工作单元未登记或身份漂移')
    budget._read()
    return budget


def _costs(budget: work_units.WorkUnitBudget, ledger: WorkUnitLedgerV2) -> tuple[WorkUnitCostView, list[dict], set[str]]:
    rows = _rows(ledger_path(budget.base), '工作单元累计成本账')
    selected: list[dict] = []
    owners: set[str] = set()
    for turn in ledger.turns:
        turn_owners: set[str] = set()
        for attempt in turn.attempts:
            if attempt.outcome in ('started', 'unknown', 'interrupted'):
                raise work_units.WorkUnitRefused('内容轮仍有未知或未结束的网络尝试，须先对账')
            found = [row for row in rows if row.get('attempt_id') == attempt.attempt_id]
            if not found and attempt.outcome == 'not_started':
                continue
            if len(found) != 1:
                raise work_units.WorkUnitRefused('内容轮的网络尝试与成本账不是一一对应')
            row = found[0]
            if (row.get('run_id') != turn.run_id or row.get('sid') != ledger.sid
                    or row.get('role', 'executor') != ledger.role
                    or (ledger.role == 'executor' and row.get('node') != ledger.node)
                    or attempt.request_fingerprint is not None and row.get('request_fingerprint') != attempt.request_fingerprint):
                raise work_units.WorkUnitRefused('内容轮与成本归属不一致')
            turn_owners.add(str(row.get('parent_run_id') or turn.run_id))
            selected.append(row)
        if len(turn_owners) > 1:
            raise work_units.WorkUnitRefused('同一内容轮映射到多个 owner')
        owners.update(turn_owners or {turn.run_id})
    def total(field: str) -> int | None:
        values = [row.get(field) for row in selected]
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values):
            return None
        return sum(values)
    prompt, completion = total('prompt_tokens'), total('completion_tokens')
    return WorkUnitCostView(
        calls=sum(row.get('cached') is not True for row in selected),
        cached_calls=sum(row.get('cached') is True for row in selected),
        failed_calls=sum(row.get('outcome') != 'ok' for row in selected),
        prompt_tokens=prompt, completion_tokens=completion,
        total_tokens=None if prompt is None or completion is None else prompt + completion,
        missing_usage_calls=sum(row.get('prompt_tokens') is None or row.get('completion_tokens') is None
                                for row in selected)), selected, owners


def _owner_proof(budget: work_units.WorkUnitBudget, ledger: WorkUnitLedgerV2,
                 owners: set[str], cost_rows: list[dict]) -> dict:
    from novel.orchestrator import agent
    run_rows = _rows(agent.runs_path(budget.base), '额度追加 AgentRun 账')
    runs = agent.fold([AgentRunEvent.model_validate(row) for row in run_rows])
    sessions = agent_session_audit.fold([
        agent_session_audit.AgentSessionEvent.from_dict(row)
        for row in _rows(agent_session_audit.audit_path(budget.base), '额度追加 session 账')])
    selected_sessions = []
    for turn in ledger.turns:
        cost_owners = {str(row.get('parent_run_id') or turn.run_id) for row in cost_rows
                       if row.get('attempt_id') in {item.attempt_id for item in turn.attempts}}
        for owner in cost_owners - {turn.run_id}:
            matches = [run for run in runs if run.run_id == owner and run.start.sid == ledger.sid
                       and sum(epoch.session_run_id == turn.run_id for epoch in run.epochs) == 1]
            if len(matches) != 1:
                raise work_units.WorkUnitRefused('成本 owner 与原 producer 的 execution epoch 不闭合')
        if not turn.session_run_id:
            if any(item.outcome != 'not_started' for item in turn.attempts):
                raise work_units.WorkUnitRefused('真实调用缺原会话身份，不能证明已安全结束')
            continue
        found = [row for row in sessions if row.session_run_id == turn.session_run_id]
        if (len(found) != 1 or found[0].end is None
                or found[0].start.parent_run_id != turn.run_id
                or found[0].start.role != ledger.role
                or (ledger.role == 'executor' and found[0].start.node != ledger.node)):
            raise work_units.WorkUnitRefused('原内容会话缺可靠结束证明，须先恢复或对账')
        # session 账没有 sid；由成本账的 sid + run + 精确 attempt 闭合归属，
        # 不能为了这里的查询给审计账另造一个未经 producer 填写的字段。
        expected = {row.attempt_id for row in turn.attempts if row.outcome != 'not_started'}
        if not expected <= {row.attempt_id for row in (*found[0].attempts, *found[0].turns)}:
            raise work_units.WorkUnitRefused('原工作单元尝试未完整进入相应 session')
        selected_sessions.append(found[0].end.to_dict())
    relevant_runs = [run for run in runs if run.run_id in owners]
    if any(run.ended is None or run.state in ('running', 'pending', 'paused', 'unknown') for run in relevant_runs):
        raise work_units.WorkUnitRefused('原 owner 尚未安全结束，不能追加额度')
    for run in relevant_runs:
        own = [row for row in run_rows if row.get('run_id') == run.run_id]
        if sum(row['kind'] == 'started' for row in own) != 1 or sum(row['kind'] == 'ended' for row in own) != 1:
            raise work_units.WorkUnitRefused('原 owner 的开始或结束记录不唯一')
        for epoch in run.epochs:
            closed = [row for row in own if row['kind'] == 'epoch_closed' and row.get('epoch_id') == epoch.epoch_id]
            if len(closed) != 1 or closed[0].get('drain_state') != 'drained':
                raise work_units.WorkUnitRefused('原 execution epoch 没有可靠排空证明')
    # 其他工作单元的当前 owner 也可能持有同书产物，预览不能承诺可与它并行写入。
    if any(run.start.sid == ledger.sid and run.state in ('running', 'pending', 'paused') for run in runs):
        raise work_units.WorkUnitRefused('本书仍有活动运行，须先等待安全结束')
    job_rows = jobs.records(budget.base)
    if any(row.job_id in owners and (row.ended is None or row.state == 'unknown') for row in job_rows):
        raise work_units.WorkUnitRefused('原作业缺安全终态，不能追加额度')
    admission_proof = []
    for owner in sorted(owners):
        snapshot = admission.readonly_snapshot(budget.base, owner)
        if snapshot.active_claims or (snapshot.opened and not snapshot.closed) or beacon.open_executions(budget.base, owner):
            raise work_units.WorkUnitRefused('原 owner 的 claim 或子执行尚未闭合')
        admission_proof.append((owner, snapshot.opened, snapshot.closed, snapshot.terminal_subject_sha256))
    return {'owners': sorted(owners), 'sessions': selected_sessions,
            'runs': [run.model_dump(mode='json') for run in relevant_runs], 'admission': admission_proof}


@dataclass(frozen=True)
class _Inspection:
    view: WorkUnitGrantPreview
    budget: work_units.WorkUnitBudget
    ledger: WorkUnitLedgerV2
    surface: str
    owners: tuple[str, ...]


def _inspect(base: str | Path, sid: str, unit_id: str, additional: int) -> _Inspection:
    if type(additional) is not int or additional not in WORK_UNIT_GRANT_ADDITIONS:
        raise ValueError('单次人工追加只能为 1～4 次')
    budget = _load(base, sid, unit_id)
    ledger = budget._read()
    costs, cost_rows, owners = _costs(budget, ledger)
    code = prov.code_version()
    surface = read_surface_seal(base, sid, code=code)
    reason = None
    if len(ledger.turns) < ledger.content_limit:
        reason = '当前仍有可用额度，无需追加'
    elif any(row.state in ('reserved', 'unknown', 'delivered') for row in ledger.turns):
        reason = '已有未闭合调用或已保存回答，须先免费恢复或对账'
    elif ledger.group_producer_sha256 is not None and not ledger.group_producer_completed:
        reason = '整组候选待免费补提交，不能用额度追加绕过'
    elif any(row.producer_sha256 is not None and row.ordinal > ledger.committed_through for row in ledger.turns):
        reason = '已保存 producer 提交目标，须先免费补提交'
    owner_proof = _owner_proof(budget, ledger, owners, cost_rows)
    token = work_units._digest({'kind': 'work-unit-budget-grant-v1', 'unit_id': unit_id,
        'ledger': ledger.model_dump(mode='json'), 'surface': surface, 'code': code,
        'costs': cost_rows, 'owners': owner_proof, 'additional': additional})
    return _Inspection(WorkUnitGrantPreview(
        sid=sid, unit_id=unit_id, available=reason is None,
        reason=reason or '可对本工作单元明确追加；历史调用、费用与语义停点均保留',
        additional=additional, allowed_additions=list(WORK_UNIT_GRANT_ADDITIONS),
        preview_token=token if reason is None else None, unit=work_units.unit_view(ledger),
        costs=costs, history=list(ledger.grants)), budget, ledger, surface, tuple(sorted(owners)))


def inspect(base: str | Path, sid: str, unit_id: str, additional: int = 1) -> WorkUnitGrantPreview:
    """失败只令本恢复投影 unknown，不影响独立 pause/stop/drain 入口。"""
    paths.work_unit_dir(base, sid, unit_id)
    if type(additional) is not int or additional not in WORK_UNIT_GRANT_ADDITIONS:
        raise ValueError('单次人工追加只能为 1～4 次')
    try:
        return _inspect(base, sid, unit_id, additional).view
    except (TerminalState, OSError, ValueError) as exc:
        return WorkUnitGrantPreview(sid=sid, unit_id=unit_id, available=False,
            reason=f'当前追加状态未知或尚未闭合：{exc}', additional=additional,
            allowed_additions=list(WORK_UNIT_GRANT_ADDITIONS))


def apply(base: str | Path, sid: str, unit_id: str, request: WorkUnitGrantRequest) -> WorkUnitGrantResult:
    request = WorkUnitGrantRequest.model_validate(request.model_dump(mode='json'))
    budget = _load(base, sid, unit_id)
    writer_id = 'budget-grant-' + uuid4().hex
    key = jobs.acquire_job_writer(base, sid, budget.identity['node'], writer_id)
    try:
        with approvals.approval_file_lock(base, sid), path_transaction_lock(budget.path):
            ledger = budget._read()
            existing = next((row for row in ledger.grants if row.intent_id == request.intent_id), None)
            if existing is not None:
                if (existing.preview_token != request.preview_token or existing.additional != request.additional
                        or existing.reason != request.reason.strip()):
                    raise work_units.WorkUnitRefused('同一追加意图已经绑定不同的授权内容')
                return WorkUnitGrantResult(grant=existing, current=inspect(base, sid, unit_id, request.additional))
            current = _inspect(base, sid, unit_id, request.additional)
            if not current.view.available or current.view.preview_token != request.preview_token:
                raise work_units.WorkUnitRefused('追加预览已漂移或不可用；' + current.view.reason)
            with ExitStack() as stopped:
                for owner in current.owners:
                    stopped.enter_context(admission.require_owner_stopped(base, owner))
                # owner lease、账与审批锁齐备后重新计算全部来源，不使用锁前状态。
                checked = _inspect(base, sid, unit_id, request.additional)
                if checked.view.preview_token != request.preview_token or not checked.view.available:
                    raise work_units.WorkUnitRefused('追加授权在锁内复验时漂移')
                grant = BudgetGrant(
                    intent_id=request.intent_id, preview_token=request.preview_token,
                    additional=request.additional, reason=request.reason.strip(), created_at=int(time.time()),
                    before_ledger_sha256=work_units._digest(checked.ledger.model_dump(mode='json')),
                    surface_sha256=checked.surface, authorized_after_ordinal=len(checked.ledger.turns),
                    owner_run_ids=checked.owners)
                updated = WorkUnitLedgerV2.model_validate({**checked.ledger.model_dump(mode='json'),
                    'grants': [*checked.ledger.grants, grant]})
                work_units._write(base, budget.path, updated)
        return WorkUnitGrantResult(grant=grant, current=inspect(base, sid, unit_id, request.additional))
    finally:
        jobs.REGISTRY.release(key, writer_id)
