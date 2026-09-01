#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""项目与 service 生命周期维护端点。

retention apply 只改 ``_service``；book restore 的真实目录移动仍只发生在
``novel.cli.trash_restore`` 子进程。两者都必须先 preview，再把完整 token/CAS
原样交回并显式确认。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from contracts.schema.api import (
    RetentionApplyRequest, RetentionApplyResultView, RetentionPlanView,
    RetentionPolicyRequest, TrashNovelRequest, TrashNovelResult,
    TrashRestoreApplyRequest, TrashRestorePlanView,
    TrashRestorePreviewRequest, TrashRestoreResultView,
)
from novel.api.deps import checked_sid, get_base
from novel.errors import ApprovalRequired
from novel.orchestrator import service_lifecycle, trash

router = APIRouter()


@router.post('/novels/{sid}/trash', response_model=TrashNovelResult)
def trash_novel(
    request: TrashNovelRequest,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> TrashNovelResult:
    """把整本项目移入可恢复回收站；书名确认与 sid 双重绑定当前目标。"""
    return trash.run(base, sid, request.expected_title)


@router.post('/maintenance/retention/preview', response_model=RetentionPlanView)
def preview_retention(
    request: RetentionPolicyRequest,
    base: str = Depends(get_base),
) -> RetentionPlanView:
    """0 成本、只读生成完整 retention 计划；默认四类 TTL 全关闭。"""
    policy = service_lifecycle.RetentionPolicy.from_mapping(
        request.model_dump(mode='python'))
    return RetentionPlanView.model_validate(
        service_lifecycle.plan_retention(base, policy).to_dict())


@router.post('/maintenance/retention/apply', response_model=RetentionApplyResultView)
def apply_retention(
    request: RetentionApplyRequest,
    base: str = Depends(get_base),
) -> RetentionApplyResultView:
    """只消费 preview 的完整计划；显式确认后仍逐项重验 CAS。"""
    if not request.confirmed:
        raise ApprovalRequired('retention apply 必须显式 confirmed=true')
    plan = service_lifecycle.RetentionPlan.from_mapping(
        request.plan.model_dump(mode='python'))
    result = service_lifecycle.apply_retention(base, plan)
    return RetentionApplyResultView(
        plan_token=result.plan_token,
        removed=list(result.removed), receipts=list(result.receipts))


@router.post('/maintenance/trash/restore/preview', response_model=TrashRestorePlanView)
def preview_trash_restore(
    request: TrashRestorePreviewRequest,
    base: str = Depends(get_base),
) -> TrashRestorePlanView:
    """只读验证完整 v3 trash，并冻结 book/approval/service run 三面身份。"""
    return TrashRestorePlanView.model_validate(trash.preview_restore(
        base, request.trash_id, request.expected_title).to_dict())


@router.post('/maintenance/trash/restore/apply', response_model=TrashRestoreResultView)
def apply_trash_restore(
    request: TrashRestoreApplyRequest,
    base: str = Depends(get_base),
) -> TrashRestoreResultView:
    """显式确认后把冻结计划交受控 CLI；浏览器/服务进程均不直接移动产物。"""
    if not request.confirmed:
        raise ApprovalRequired('trash restore apply 必须显式 confirmed=true')
    plan = trash.TrashRestorePlan.from_mapping(
        request.plan.model_dump(mode='python'))
    return TrashRestoreResultView.model_validate(
        trash.run_restore(base, plan))
