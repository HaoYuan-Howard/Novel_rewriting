#!/usr/bin/env python3
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from contracts.schema.api import (
    ApprovalBackfillResult, ApprovalDoc, DesignReviewerKind,
    DesignReviewRequest, DesignReviewTemplate, GateKey, SeenKey,
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


@router.get('/design/template', response_model=DesignReviewTemplate)
def get_design_review_template(
    actor: str = Query(min_length=1),
    reviewer_kind: DesignReviewerKind = Query(),
    review_run_id: str = Query(
        pattern=r'^[a-z0-9][a-z0-9_-]{0,127}$'),
    reason: str = Query(min_length=1),
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> DesignReviewTemplate:
    """按当前 A 候选生成关闭式全书盲审模板；只读且不代签。"""
    return approvals.design_template(
        base, sid,
        actor=actor,
        reviewer_kind=reviewer_kind,
        review_run_id=review_run_id,
        reason=reason,
    )


@router.post('/design', response_model=ApprovalDoc)
def approve_design_review(
    request: DesignReviewRequest,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> ApprovalDoc:
    """原子保存 Stage4A typed 盲审；不启动修订或 Stage4B。"""
    return approvals.approve_design(base, sid, request)


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
