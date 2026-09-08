#!/usr/bin/env python3
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from contracts.schema.api import (
    ApprovalBackfillResult, ApprovalDoc, ApprovalWorkpack, BlueprintSeenRequest, Stage2GlobalSeenRequest,
    DesignReviewerKind,
    DesignReviewRequest, DesignReviewTemplate, GateKey, SeenKey,
    Stage5ClosureRequest, Stage5ClosureTemplate,
    Stage6ApprovalWorkpack, Stage7ApprovalWorkpack,
    StructureApprovalRequest, WritingApprovalRequest,
    Stage2NodeReviewRequest,
)
from novel.api.deps import checked_sid, get_base
from novel.errors import ApprovalRequired
from novel.orchestrator import approvals
from novel.orchestrator.write_admission import operation_scope
from novel.views.stage6 import stage6_approval_workpack
from novel.views.stage7 import stage7_approval_workpack

router = APIRouter(prefix='/novels/{sid}/approvals')


@router.post('/stage2-review', response_model=ApprovalDoc)
def submit_stage2_review(
    request: Stage2NodeReviewRequest,
    sid: str = Depends(checked_sid), base: str = Depends(get_base),
) -> ApprovalDoc:
    """提交绑定当前工作包的 Global 或 Blueprint 独立裁决。"""
    with operation_scope(base, sid, operation='approval-stage2-review', details={
            'node': request.node, 'review_token': request.review_token,
            'decision': request.decision, 'actor': request.actor}):
        return approvals.approve_stage2_review(base, sid, request)


@router.get('', response_model=ApprovalDoc)
def get_approvals(
    sid: str = Depends(checked_sid), base: str = Depends(get_base),
) -> ApprovalDoc:
    return approvals.get(base, sid)


@router.get('/design/workpack', response_model=ApprovalWorkpack)
def get_design_workpack(
    sid: str = Depends(checked_sid), base: str = Depends(get_base),
) -> ApprovalWorkpack:
    """当前 Stage4A subject 的只读全章审阅包；不代签、不启动 B。"""
    return approvals.design_workpack(base, sid)


@router.get('/structure/workpack', response_model=ApprovalWorkpack)
def get_structure_workpack(
    sid: str = Depends(checked_sid), base: str = Depends(get_base),
) -> ApprovalWorkpack:
    """当前 Stage4B 结构 subject 的只读全章审批包；不组装 Context。"""
    return approvals.structure_workpack(base, sid)


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
    details = {
        'actor': request.actor,
        'reviewer_kind': request.reviewer_kind,
        'review_run_id': request.review_run_id,
        'review_token': request.review_token,
        'strategy': {
            'decision': request.strategy.decision,
        },
        'chapters': [
            {
                'output_ordinal': row.output_ordinal,
                'decision': row.decision,
            }
            for row in request.chapters
        ],
    }
    with operation_scope(
            base, sid, operation='approval-design', details=details):
        return approvals.approve_design(base, sid, request)


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


@router.post('/structure', response_model=ApprovalDoc)
def approve_structure(
    request: StructureApprovalRequest,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> ApprovalDoc:
    """保存 hash-bound 逐章结构裁决；不在服务进程触发任何重规划作业。"""
    details = {
        'actor': request.actor,
        'review_token': request.review_token,
        'chapters': [
            {
                'output_ordinal': row.output_ordinal,
                'decision': row.decision,
            }
            for row in request.chapters
        ],
    }
    with operation_scope(
            base, sid, operation='approval-structure', details=details):
        return approvals.approve_structure(base, sid, request)


@router.post('/backfill', response_model=ApprovalBackfillResult)
def backfill_approvals(
    sid: str = Depends(checked_sid), base: str = Depends(get_base),
) -> ApprovalBackfillResult:
    with operation_scope(base, sid, operation='approval-backfill'):
        return approvals.backfill_from_disk(base, sid)


@router.post('/seen/{key}', response_model=ApprovalDoc)
def mark_seen(
    key: SeenKey,
    request: BlueprintSeenRequest | Stage2GlobalSeenRequest | None = None,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> ApprovalDoc:
    """两个 Stage2 视图都只接受 hash-bound 阅读身份；没有“打开即已读”。"""
    blueprint_subject: BlueprintSeenRequest | None = None
    global_subject: Stage2GlobalSeenRequest | None = None
    if key == 'read':
        if not isinstance(request, BlueprintSeenRequest):
            raise ApprovalRequired(
                'seen.read 必须携带实际读过的 Blueprint 工作包身份；请刷新阅读页后重试')
        blueprint_subject = request
        details: dict[str, object] = {
            'key': key, 'review_token': request.review_token}
    else:
        if not isinstance(request, Stage2GlobalSeenRequest):
            raise ApprovalRequired(
                'seen.global 必须携带实际读过的 Global 审阅面身份（review_token + actor）；'
                '请读完当前 review 后再确认')
        global_subject = request
        details = {
            'key': key, 'review_token': request.review_token,
            'actor': request.actor,
        }
    with operation_scope(
            base, sid, operation='approval-seen', details=details):
        return approvals.mark_seen(
            base, sid, key, blueprint_subject=blueprint_subject,
            global_subject=global_subject)


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
