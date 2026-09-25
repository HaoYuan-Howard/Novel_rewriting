#!/usr/bin/env python3
from __future__ import annotations

from fastapi import APIRouter, Depends

from contracts.schema.api import (
    ApprovalBackfillResult, ApprovalDoc, GateKey,
)
from novel.api.deps import checked_sid, get_base
from novel.orchestrator import approvals, inspection
from novel.orchestrator.write_admission import operation_scope

router = APIRouter(prefix='/novels/{sid}/approvals')

# ⚠ Stage6/7 终审工作包（approval workpack）面已随人工闸退役整体移除（F8b）：
# Stage6 三候选定稿随整层退役；Stage7 人名表工作包的 view 读旧链 rename 节点，
# 该节点已不在 NODES。新链的命名与交付监控走 `/api/str06/candidates/{candidate}/*`。


@router.get('', response_model=ApprovalDoc)
def get_approvals(
    sid: str = Depends(checked_sid), base: str = Depends(get_base),
) -> ApprovalDoc:
    with inspection.approval_read_snapshot(base, sid) as (overview, code):
        return approvals.get(base, sid, overview=overview, code=code)


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
