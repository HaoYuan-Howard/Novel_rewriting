#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''上传、Stage 0 审校动作与 Stage 1 只读对照。

本模块的产物变更都经 jobs.run 起 CLI 子进程；HTTP 进程只写 _service 下的原始上传、
作业记录、日志、编辑计划与 stage0 审批状态；merge 决定在失败后保留到成功重放。
'''
from __future__ import annotations

import warnings

from fastapi import APIRouter, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool

from contracts.schema.api import (
    HookCopyView,
    Stage0ApprovalRequest,
    Stage0EditRequest,
    Stage0MergeDecisionRequest,
    Stage0Segments,
    Stage1Chapters,
    Stage1Segments,
    UploadResult,
)
from contracts.schema.orchestration import JobRecord
from contracts.schema.stage0 import RemovalReport, Stage0MergeCandidate, Stage0MergeDecisionPlan
from novel.api.deps import checked_sid, get_base
from novel.errors import NovelError, SourcePathRejected
from novel.orchestrator import approvals, jobs, uploads
from novel.views import ingest

router = APIRouter()


@router.get('/novels/{sid}/stage0/report', response_model=RemovalReport)
def get_stage0_report(
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> RemovalReport:
    return ingest.stage0_report(base, sid)


@router.get('/novels/{sid}/stage0/segments', response_model=Stage0Segments)
def get_stage0_segments(
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> Stage0Segments:
    return ingest.stage0_segments(base, sid)


@router.post('/novels/{sid}/stage0/edit', response_model=JobRecord)
def edit_stage0(
    request: Stage0EditRequest,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> JobRecord:
    plan = ingest.stage0_edit_plan(base, sid, request)
    plan_path = uploads.save_edit_plan(base, sid, plan)
    try:
        return jobs.run(
            base,
            'stage0.edit',
            sid,
            ['--apply-edit-plan', str(plan_path)],
            follow_check=False,
        )
    finally:
        try:
            uploads.discard_edit_plan(base, plan_path)
        except NovelError as exc:
            warnings.warn(
                f'Stage 0 作业已结束，但一次性编辑计划清理失败：{exc}',
                RuntimeWarning,
                stacklevel=2,
            )


@router.post('/novels/{sid}/stage0/approve', response_model=JobRecord)
def approve_stage0(
    request: Stage0ApprovalRequest,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> JobRecord:
    record = jobs.run(
        base, 'stage0.approve', sid,
        ['--actor', request.actor, '--reason', request.reason],
    )
    if record.state == 'ok':
        approvals.approve_gate(base, sid, 's0')
    return record


@router.get('/novels/{sid}/stage0/merge', response_model=Stage0MergeCandidate)
def get_stage0_merge_candidate(
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> Stage0MergeCandidate:
    return ingest.stage0_merge_candidate(base, sid)


def _run_merge_decision(
    base: str, sid: str, request: Stage0MergeDecisionRequest, action: str,
) -> JobRecord:
    plan = Stage0MergeDecisionPlan(
        action=action,
        candidate_id=request.candidate_id,
        expected_round=request.expected_round,
        expected_source_sha1=request.expected_source_sha1,
        expected_clean_sha1=request.expected_clean_sha1,
        resolutions=request.resolutions if action == 'accept' else [],
        actor=request.actor,
        reason=request.reason,
    )
    plan_path = uploads.save_merge_plan(base, sid, plan)
    record = jobs.run(
        base, 'stage0.edit', sid,
        ['--apply-merge-plan', str(plan_path)], follow_check=False,
    )
    # ``ok`` and ``landed_red`` both mean the merge action landed; the latter
    # only says its deterministic report still has red rows.  Process failure
    # or an exception is not terminal for the merge transaction: retain the
    # content-addressed decision so the next identical API request replays it.
    if record.state in ('ok', 'landed_red'):
        try:
            uploads.discard_edit_plan(base, plan_path)
        except NovelError as exc:
            warnings.warn(
                f'Stage 0 合并作业已结束，但一次性决定清理失败：{exc}',
                RuntimeWarning, stacklevel=2,
            )
    return record


@router.post('/novels/{sid}/stage0/merge/resolve', response_model=JobRecord)
def resolve_stage0_merge(
    request: Stage0MergeDecisionRequest,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> JobRecord:
    return _run_merge_decision(base, sid, request, 'accept')


@router.post('/novels/{sid}/stage0/merge/reject', response_model=JobRecord)
def reject_stage0_merge(
    request: Stage0MergeDecisionRequest,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> JobRecord:
    return _run_merge_decision(base, sid, request, 'reject')


@router.post('/novels/{sid}/stage1/partition', response_model=JobRecord)
def partition_stage1(
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> JobRecord:
    return jobs.run(base, 'stage1.partition', sid)


@router.get('/novels/{sid}/stage1/segments', response_model=Stage1Segments)
def get_stage1_segments(
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> Stage1Segments:
    return ingest.stage1_segments(base, sid)


@router.get('/novels/{sid}/stage1/chapters', response_model=Stage1Chapters)
def get_stage1_chapters(
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> Stage1Chapters:
    return ingest.stage1_chapters(base, sid)


@router.get('/novels/{sid}/stage1/hook_copy', response_model=HookCopyView)
def get_stage1_hook_copy(
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> HookCopyView:
    return ingest.stage1_hook_copy(base, sid)


@router.post('/upload', response_model=UploadResult)
async def upload_source(
    request: Request,
    filename: str = Query(min_length=1),
    base: str = Depends(get_base),
) -> UploadResult:
    '''接收原始 octet-stream；在内存中越过上限的下一块立刻拒绝。'''
    announced = request.headers.get('content-length')
    if announced is not None:
        try:
            announced_size = int(announced)
        except ValueError as exc:
            raise SourcePathRejected(f'Content-Length 不是整数：{announced!r}') from exc
        if announced_size < 0 or announced_size > uploads.MAX_UPLOAD_BYTES:
            raise SourcePathRejected(
                f'Content-Length={announced_size}，上传上限为 {uploads.MAX_UPLOAD_BYTES} 字节')

    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > uploads.MAX_UPLOAD_BYTES:
            raise SourcePathRejected(
                f'上传内容超过 {uploads.MAX_UPLOAD_BYTES} 字节上限')

    def _run() -> UploadResult:
        source_path, sid = uploads.save_upload(base, filename, bytes(raw))
        job = jobs.run(
            base,
            'stage0.remove',
            None,
            ['--source', str(source_path)],
            follow_check=False,
        )
        return UploadResult(sid=sid, job=job)

    return await run_in_threadpool(_run)
