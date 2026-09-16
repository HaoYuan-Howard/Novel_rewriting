#!/usr/bin/env python3
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from contracts.schema.api import (
    ApprovalBackfillResult, ApprovalDoc, GateKey,
    Stage5ClosureRequest, Stage5ClosureTemplate,
    Stage6ApprovalWorkpack, Stage7ApprovalWorkpack,
    WritingApprovalRequest,
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


@router.get('/stage5/template', response_model=Stage5ClosureTemplate)
def get_stage5_closure_template(
    sid: str = Depends(checked_sid), base: str = Depends(get_base),
) -> Stage5ClosureTemplate:
    """按当前盘面生成 Stage5 闭合工作包模板；只读、0 成本、不代签。"""
    return approvals.stage5_closure_template(base, sid)


@router.post('/stage5', response_model=ApprovalDoc)
def approve_stage5_closure(
    request: Stage5ClosureRequest,
    sid: str = Depends(checked_sid), base: str = Depends(get_base),
) -> ApprovalDoc:
    """原子提交 Stage5 闭合回执；通过即打开 gate.s5，不启动任何付费作业。"""
    details = {
        'actor': request.actor,
        'review_token': request.review_token,
        'must_appear': [
            {
                'chapter_ordinal': row.chapter_ordinal,
                'evidence_key': row.evidence_key,
                'ruling': row.ruling,
            }
            for row in request.must_appear
        ],
        'group_acks': [
            {
                'group': row.group,
                'acknowledged': row.acknowledged,
            }
            for row in request.group_acks
        ],
    }
    with operation_scope(
            base, sid, operation='approval-stage5', details=details):
        return approvals.approve_stage5_closure(base, sid, request)


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


@router.post('/writing', response_model=ApprovalDoc)
def approve_writing(
    request: WritingApprovalRequest,
    sid: str = Depends(checked_sid), base: str = Depends(get_base),
) -> ApprovalDoc:
    """typed token 授权只写审批账；不启动任何付费作业。"""
    details = {
        'actor': request.actor,
        'review_token': request.review_token,
    }
    with operation_scope(
            base, sid, operation='approval-writing', details=details):
        return approvals.approve_writing(base, sid, request)


@router.post('/reset', response_model=ApprovalDoc)
def reset_approvals(
    sid: str = Depends(checked_sid), base: str = Depends(get_base),
) -> ApprovalDoc:
    with operation_scope(base, sid, operation='approval-reset'):
        return approvals.reset(base, sid)
