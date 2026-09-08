#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''人工写回入口：关系、章节改稿、文案定稿与人名定稿。

所有写回都是**单步 0 成本作业**：产物变更一律经 `jobs.run` 起 CLI 子进程（`R-11.3`），
HTTP 进程只写 `_service/`（一次性人工稿）。CLI 内失败（越界 pick、章号不在计划等）
= **HTTP 200 + `JobRecord.state='failed'`**——作业失败不是 HTTP 错误
（`stage0.edit` 同形），前端读 state 与日志。

⚠ **`stage6/edit` 与 `stage7/edit` 刻意不在**（2026-08-17 用户裁决）：
改字/改表是 `--apply-edit` 节点，契约把编辑文件钉死在书目录内（`_edit_rel`），
而服务进程不写产物盘 ⇒ 浏览器文本没有合规通道。那两条 0 成本路走 CLI；
FE-01 矩阵记「明确不可用」，界面上对应入口禁用并说明。
'''
from __future__ import annotations

import warnings

from fastapi import APIRouter, Depends

from contracts.schema.api import (
    BatchTrimResolutionRequest,
    BlueprintRelationEditPreview,
    DraftEditPreview, DraftEditPreviewRequest, DraftEditRequest,
    HookApproveRequest, NameMapApproveRequest,
    ResidualAdoptionRequest, TrimResolutionRequest,
)
from contracts.schema.evaluation import GlobalReviewDecisionSet
from contracts.schema.orchestration import JobRecord
from contracts.schema.stage2 import BlueprintRelationEdit
from contracts.schema.stage5 import ChapterResponse
from novel.api.approval_stamp import stamp_approval_after_landed as _stamp
from novel.api.deps import checked_sid, get_base
from novel.cli import exit_codes as X
from novel.errors import (
    NovelError, SourceConflict, TerminalState,
)
from novel.orchestrator import jobs, uploads
from novel.run import RunContext
from novel.stages.stage2 import relation_edit
from novel.stages.stage5 import write

router = APIRouter(prefix='/novels')


@router.post('/{sid}/review/{ordinal}/edit', response_model=JobRecord)
def edit_draft(
    ordinal: int,
    request: DraftEditRequest,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> JobRecord:
    '''章节改稿：typed `segments` → `_service/edits/` 一次性稿 → `stage5.write --apply-edit`。

    人工稿走**与只读 preview 同一个判定落点**（归一 → 判据 → 归档 → 写回，
    `origin=human`）。无新增 HARD 才接受 ``formal``；新增一般 HARD 只接受显式
    ``working_draft``，段落预算 HARD 无条件拒绝。任何已落稿都会使旧 Review 过期，
    多次编辑完成后由人只跑一次最终 Review。
    '''
    path = uploads.save_draft_edit(
        base, sid, ordinal, ChapterResponse(
            segments=[segment.model_dump() for segment in request.segments]))
    try:
        return jobs.run(
            base,
            'stage5.write',
            sid,
            [
                '--apply-edit', str(path),
                '--actor', request.actor,
                '--reason', request.reason,
                '--current-chapter-sha256', request.current_chapter_sha256,
                '--preview-subject-sha256', request.preview_subject_sha256,
                '--edit-confirmation', request.confirmation,
            ],
            follow_check=False,
        )
    finally:
        try:
            uploads.discard_edit_plan(base, path)
        except NovelError as exc:
            warnings.warn(
                f'改稿作业已结束，但一次性人工稿清理失败：{exc}',
                RuntimeWarning,
                stacklevel=2,
            )


@router.post('/{sid}/review/{ordinal}/edit/preview', response_model=DraftEditPreview)
def preview_draft_edit(
    ordinal: int,
    request: DraftEditPreviewRequest,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> DraftEditPreview:
    """只读改稿预览：与 CLI apply 共用归一、判据与 subject 构造，不写一次性文件。"""
    ctx = RunContext.open(
        base, sid, 'stage5.write', paid=False, write_ledger=False)
    response = ChapterResponse(
        segments=[segment.model_dump() for segment in request.segments])
    try:
        return write.prepare_edit_preview(ctx, ordinal, response).preview()
    except TerminalState as exc:
        # 裸 TerminalState 只来自本次候选的 slot/归一契约；给同步 HTTP 一份已登记的
        # 409 身份。其余带具体类型的 missing/stale/unreadable 仍按原类上浮。
        if type(exc) is TerminalState:
            raise SourceConflict(str(exc)) from exc
        raise


@router.post(
    '/{sid}/stage2/relations/edit/preview',
    response_model=BlueprintRelationEditPreview,
)
def preview_blueprint_relations(
    request: BlueprintRelationEdit,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> BlueprintRelationEditPreview:
    """Stage2 v7 关系整章集合替换的只读预检。"""
    ctx = RunContext.open(
        base, sid, 'stage2.blueprint', paid=False, write_ledger=False)
    try:
        return relation_edit.prepare_edit(ctx, request).preview()
    except TerminalState as exc:
        if type(exc) is TerminalState:
            raise SourceConflict(str(exc)) from exc
        raise


@router.post(
    '/{sid}/stage2/relations/edit',
    response_model=JobRecord,
)
def apply_blueprint_relations(
    request: BlueprintRelationEdit,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> JobRecord:
    """typed request → service 一次性文件 → canonical CLI 子进程。"""
    path = uploads.save_blueprint_relation_edit(base, sid, request)
    try:
        return jobs.run(
            base,
            'stage2.blueprint',
            sid,
            ['--apply-edit', str(path)],
            follow_check=False,
        )
    finally:
        try:
            uploads.discard_edit_plan(base, path)
        except NovelError as exc:
            warnings.warn(
                f'Stage2 关系编辑作业已结束，但一次性请求清理失败：{exc}',
                RuntimeWarning,
                stacklevel=2,
            )


@router.post('/{sid}/stage2/global-review', response_model=JobRecord)
def decide_global_review(
    request: GlobalReviewDecisionSet,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> JobRecord:
    """Apply self-reported human decisions through the CLI artifact boundary."""
    path = uploads.save_global_review_decisions(base, sid, request)
    try:
        record = jobs.run(
            base, 'stage2.global', sid,
            ['--review-decisions', str(path)], follow_check=False)
        # CHECK_FAILED means the batch landed but the current blocking surface
        # has not closed.  Unlike ordinary human approvals, that state must not
        # open s2; disclosure rows may remain non-PASS and stay in the audit view.
        # Even on OK, blueprint may still be running/missing; `_stamp` is
        # intentionally best-effort so the already-landed decisions remain valid.
        if record.exit_code == X.OK:
            _stamp(base, sid, 's2')
        return record
    finally:
        try:
            uploads.discard_edit_plan(base, path)
        except NovelError as exc:
            warnings.warn(
                f'复核作业已结束，但一次性决策文件清理失败：{exc}',
                RuntimeWarning, stacklevel=2)


@router.post('/{sid}/stage5/trim/{ordinal}/retain-pretrim', response_model=JobRecord)
def retain_pretrim(
    ordinal: int,
    request: TrimResolutionRequest,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> JobRecord:
    """Close one current trim verification without adopting its candidate."""
    return jobs.run(
        base, 'stage5.review', sid,
        ['--retain-pretrim', '--chapters', str(ordinal),
         '--actor', request.actor, '--reason', request.reason],
        follow_check=False,
    )


@router.post('/{sid}/stage5/trim/retain-pretrim-batch', response_model=JobRecord)
def retain_pretrim_batch(
    request: BatchTrimResolutionRequest,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> JobRecord:
    """一次整批销账多章 trim：整批预检、按章幂等写 hash-bound resolution。"""
    path = uploads.save_stage5_trim_batch(base, sid, request)
    try:
        return jobs.run(
            base, 'stage5.review', sid,
            ['--retain-pretrim-batch', str(path)], follow_check=False)
    finally:
        try:
            uploads.discard_edit_plan(base, path)
        except NovelError as exc:
            warnings.warn(
                f'批量销账作业已结束，但一次性 manifest 清理失败：{exc}',
                RuntimeWarning, stacklevel=2)


@router.post('/{sid}/stage6/approve', response_model=JobRecord)
def approve_hook(
    request: HookApproveRequest,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> JobRecord:
    '''文案定稿：候选序号 + actor/reason + opaque workpack token。

    正式 typed 定稿是唯一事实；公开 s6 现场读取 producer 的 current 证明，
    不再追加服务检查单写入。作业退出状态与校验报告仍各自原样返回。
    '''
    record = jobs.run(
        base, 'stage6.approve', sid,
        ['--pick', str(request.pick),
         '--actor', request.actor,
         '--reason', request.reason,
         '--review-token', request.review_token])
    return record


@router.post('/{sid}/stage7/approve', response_model=JobRecord)
def approve_names(
    request: NameMapApproveRequest,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> JobRecord:
    '''人名表定稿：actor/reason + opaque workpack token 乐观锁。

    后端会在任何归档/写盘前复核 token，并拒绝仍有 HARD 的人物表；
    公开 s7 现场读取正式 typed 定稿的 current 证明，不另写服务检查单。
    '''
    record = jobs.run(
        base, 'stage7.approve', sid,
        ['--actor', request.actor,
         '--reason', request.reason,
         '--review-token', request.review_token])
    return record


@router.post('/{sid}/stage7/adopt-residual', response_model=JobRecord)
def adopt_residual(
    request: ResidualAdoptionRequest,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> JobRecord:
    """Adopt only conflict-free high-confidence residual suggestions."""
    return jobs.run(
        base, 'stage7.rename', sid,
        ['--adopt-residual', request.confidence],
    )
