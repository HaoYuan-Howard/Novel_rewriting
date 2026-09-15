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
    Stage0Report,
    Stage0Segments,
    Stage1View,
    UploadResult,
)
from contracts.schema.orchestration import JobRecord
from novel.api.deps import checked_sid, get_base
from novel.errors import HumanEditProtected, NovelError, SourcePathRejected
from novel.orchestrator import jobs, uploads
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
