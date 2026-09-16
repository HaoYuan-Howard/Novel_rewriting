#!/usr/bin/env python3
from __future__ import annotations

from fastapi import APIRouter, Depends

from contracts.schema.api import (
    ApprovalBackfillResult, ApprovalDoc, GateKey,
    Stage6ApprovalWorkpack, Stage7ApprovalWorkpack,
)
from novel.api.deps import checked_sid, get_base
from novel.orchestrator import approvals, inspection
from novel.orchestrator.write_admission import operation_scope
from novel.views.stage6 import stage6_approval_workpack
from novel.views.stage7 import stage7_approval_workpack

router = APIRouter(prefix='/novels/{sid}/approvals')


@router.get('', response_model=ApprovalDoc)
def get_approvals(
    sid: str = Depends(checked_sid), base: str = Depends(get_base),
) -> ApprovalDoc:
    with inspection.approval_read_snapshot(base, sid) as (overview, code):
        return approvals.get(base, sid, overview=overview, code=code)


@router.get('/stage6/workpack', response_model=Stage6ApprovalWorkpack)
def get_stage6_workpack(
    sid: str = Depends(checked_sid), base: str = Depends(get_base),
) -> Stage6ApprovalWorkpack:
    """当前 Stage6 候选集的紧凑、A/B 证据与机械检查绑定终审包。"""
    return stage6_approval_workpack(base, sid)


@router.get('/stage7/workpack', response_model=Stage7ApprovalWorkpack)
def get_stage7_workpack(
    sid: str = Depends(checked_sid), base: str = Depends(get_base),
) -> Stage7ApprovalWorkpack:
    """当前 Stage7 人物决策面的紧凑、真实命中与机械检查绑定终审包。"""
    return stage7_approval_workpack(base, sid)


# ⚠ Stage5 写作授权与闭合回执的人工写面已随批次 B3（2026-09-15 全自动裁决）
#   退役：`POST /writing`、`POST /stage5`、`GET /stage5/template` 删除。
#   闭合由 `stage5.review` producer 以 system:stage5-auto 自动落账；
#   写作授权并入 run 启动授权（上传即授权本书全部付费节点）。


@router.post('/gate/{key}', response_model=ApprovalDoc)
def approve_gate(
    key: GateKey, sid: str = Depends(checked_sid), base: str = Depends(get_base),
) -> ApprovalDoc:
    with operation_scope(
            base, sid, operation='approval-gate', details={'key': key}):
        return approvals.approve_gate(base, sid, key)


@router.post('/backfill', response_model=ApprovalBackfillResult)
def backfill_approvals(
    sid: str = Depends(checked_sid), base: str = Depends(get_base),
) -> ApprovalBackfillResult:
    with operation_scope(base, sid, operation='approval-backfill'):
        return approvals.backfill_from_disk(base, sid)


@router.post('/reset', response_model=ApprovalDoc)
def reset_approvals(
    sid: str = Depends(checked_sid), base: str = Depends(get_base),
) -> ApprovalDoc:
    with operation_scope(base, sid, operation='approval-reset'):
        return approvals.reset(base, sid)
