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
    WorkUnitGrantPreview, WorkUnitGrantRequest, WorkUnitGrantResult,
)
from contracts.schema.workflow import BudgetGrant, WorkUnitLedgerV2
from novel import paths, prov, work_units
from novel.appendlog import path_transaction_lock
from novel.errors import TerminalState
from novel.orchestrator import admission, approvals, jobs
from novel.orchestrator.inspection import read_surface_seal


from novel.orchestrator.work_unit_evidence import (
    load_budget as _load, cost_evidence as _costs, owner_stop_proof as _owner_proof,
)


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
