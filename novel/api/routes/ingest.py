#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''上传、Stage 0 审校动作与 Stage 1 只读对照。

本模块的产物变更都经 jobs.run 起 CLI 子进程；HTTP 进程只写 _service 下的原始上传、
作业记录、日志、编辑计划与 stage0 审批状态；候选裁决在失败后保留到成功重放。
'''
from __future__ import annotations

import warnings

from fastapi import APIRouter, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool

from contracts.schema.api import (
    Stage0ApprovalRequest,
    Stage0CandidateDecisionRequest,
    Stage0CandidateView,
    Stage0EditRequest,
    Stage0EditResult,
    Stage0Report,
    Stage0Segments,
    Stage1View,
    UploadResult,
)
from contracts.schema.orchestration import JobRecord
from contracts.schema.stage0 import Stage0CandidatePlan
from novel.api.deps import checked_sid, get_base
from novel.errors import HumanEditProtected, NovelError, SourcePathRejected
from novel.orchestrator import approvals, jobs, uploads
from novel.views import ingest

router = APIRouter()


@router.get('/novels/{sid}/stage0/report', response_model=Stage0Report)
def get_stage0_report(
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> Stage0Report:
    return ingest.stage0_report(base, sid)


@router.get('/novels/{sid}/stage0/segments', response_model=Stage0Segments)
def get_stage0_segments(
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> Stage0Segments:
    return ingest.stage0_segments(base, sid)


@router.post('/novels/{sid}/stage0/edit', response_model=Stage0EditResult)
def edit_stage0(
    request: Stage0EditRequest,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> Stage0EditResult:
    plan = ingest.stage0_edit_plan(base, sid, request)
    target_revision_id = ingest.stage0_edit_target_revision_id(base, sid, plan)
    plan_path = uploads.save_edit_plan(base, sid, plan)
    try:
        record = jobs.run(
            base,
            'stage0.edit',
            sid,
            ['--apply-edit-plan', str(plan_path)],
            follow_check=False,
        )
        return Stage0EditResult(
            job=record,
            revision_id=(target_revision_id
                         if record.state in ('ok', 'landed_red') else None),
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
    current = ingest.stage0_report(base, sid)
    if request.expected_revision_id != current.revision_id:
        raise HumanEditProtected(
            'Stage0 批准页面已过期：'
            f'工作包={request.expected_revision_id!r}，当前={current.revision_id!r}')
    record = jobs.run(
        base, 'stage0.approve', sid,
        ['--expected-revision-id', request.expected_revision_id,
         '--actor', request.actor, '--reason', request.reason],
    )
    if record.state == 'ok':
        approvals.approve_gate(base, sid, 's0')
    return record


@router.get('/novels/{sid}/stage0/candidate', response_model=Stage0CandidateView)
def get_stage0_candidate(
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> Stage0CandidateView:
    return ingest.stage0_candidate(base, sid)


def _run_candidate_decision(
    base: str, sid: str, request: Stage0CandidateDecisionRequest, action: str,
) -> JobRecord:
    plan = Stage0CandidatePlan(
        action=action,
        candidate_revision_id=request.candidate_revision_id,
        expected_revision_id=request.expected_revision_id,
        actor=request.actor,
        reason=request.reason,
    )
    plan_path = uploads.save_candidate_plan(base, sid, plan)
    record = jobs.run(
        base, 'stage0.edit', sid,
        ['--apply-candidate-plan', str(plan_path)], follow_check=False,
    )
    # ``ok`` and ``landed_red`` both mean the decision landed; the latter only
    # says its deterministic report still has red rows.  Process failure or an
    # exception is not terminal: retain the content-addressed decision so the
    # next identical API request replays it.
    if record.state in ('ok', 'landed_red'):
        try:
            uploads.discard_edit_plan(base, plan_path)
        except NovelError as exc:
            warnings.warn(
                f'Stage 0 候选裁决作业已结束，但一次性决定清理失败：{exc}',
                RuntimeWarning, stacklevel=2,
            )
    return record


@router.post('/novels/{sid}/stage0/candidate/adopt', response_model=JobRecord)
def adopt_stage0_candidate(
    request: Stage0CandidateDecisionRequest,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> JobRecord:
    return _run_candidate_decision(base, sid, request, 'adopt')


@router.post('/novels/{sid}/stage0/candidate/dismiss', response_model=JobRecord)
def dismiss_stage0_candidate(
    request: Stage0CandidateDecisionRequest,
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> JobRecord:
    return _run_candidate_decision(base, sid, request, 'dismiss')


@router.post('/novels/{sid}/stage1/partition', response_model=JobRecord)
def partition_stage1(
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> JobRecord:
    return jobs.run(base, 'stage1.partition', sid)


@router.get('/novels/{sid}/stage1', response_model=Stage1View)
def get_stage1_view(
    sid: str = Depends(checked_sid),
    base: str = Depends(get_base),
) -> Stage1View:
    return ingest.stage1_view(base, sid)


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
