#!/usr/bin/env python3
from __future__ import annotations

from fastapi import APIRouter, Depends

from contracts.schema.api import (
    ApprovalBackfillResult, ApprovalDoc, GateKey, SeenKey,
    StructureApprovalRequest, WritingApprovalRequest,
)
from novel.api.deps import checked_sid, get_base
from novel.orchestrator import approvals

router = APIRouter(prefix='/novels/{sid}/approvals')


@router.get('', response_model=ApprovalDoc)
def get_approvals(
    sid: str = Depends(checked_sid), base: str = Depends(get_base),
) -> ApprovalDoc:
    return approvals.get(base, sid)


@router.post('/gate/{key}', response_model=ApprovalDoc)
def approve_gate(
    key: GateKey, sid: str = Depends(checked_sid), base: str = Depends(get_base),
) -> ApprovalDoc:
    return approvals.approve_gate(base, sid, key)


@router.post('/structure', response_model=ApprovalDoc)
def approve_structure(
    request: StructureApprovalRequest,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> ApprovalDoc:
    """保存 hash-bound 逐章结构裁决；不在服务进程触发任何重规划作业。"""
    return approvals.approve_structure(base, sid, request)


@router.post('/backfill', response_model=ApprovalBackfillResult)
def backfill_approvals(
    sid: str = Depends(checked_sid), base: str = Depends(get_base),
) -> ApprovalBackfillResult:
    return approvals.backfill_from_disk(base, sid)


@router.post('/seen/{key}', response_model=ApprovalDoc)
def mark_seen(
    key: SeenKey, sid: str = Depends(checked_sid), base: str = Depends(get_base),
) -> ApprovalDoc:
    return approvals.mark_seen(base, sid, key)


@router.post('/writing', response_model=ApprovalDoc)
def approve_writing(
    request: WritingApprovalRequest | None = None,
    sid: str = Depends(checked_sid), base: str = Depends(get_base),
) -> ApprovalDoc:
    """新 typed body 可审计；空 body 只兼容既有 UI，二者都不启动付费作业。"""
    return (
        approvals.begin_writing(base, sid)
        if request is None
        else approvals.approve_writing(base, sid, request)
    )


@router.post('/reset', response_model=ApprovalDoc)
def reset_approvals(
    sid: str = Depends(checked_sid), base: str = Depends(get_base),
) -> ApprovalDoc:
    return approvals.reset(base, sid)
