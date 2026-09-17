#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''上传、Stage 0 审校动作与 Stage 1 只读对照。

本模块的产物变更都经 jobs.run 起 CLI 子进程；HTTP 进程只写 _service 下的原始上传、
作业记录、日志、编辑计划与 stage0 审批状态；候选裁决在失败后保留到成功重放。

上传即授权自动开跑（R-11.28）：幂等键 + 两项授权 + 可解析 profiles 时，先铸
upload_autostart 意图再跑 stage0，成功后 agent.start；stage0 失败不开付费 run。
'''
from __future__ import annotations

import hashlib
import json
import warnings

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from contracts.schema.api import (
    Stage0Report,
    Stage0Segments,
    Stage1View,
    UploadResult,
)
from contracts.schema.orchestration import JobRecord, PipelineAgentProfiles
from novel.orchestrator.executor_capabilities import (
    AgentProfileRefused, validate_pipeline_profiles,
)
from novel.api.deps import checked_sid, get_base
from novel.errors import (
    SourcePathRejected,
    UnsupportedRunMode,
)
from novel.orchestrator import agent, agent_settings, jobs, upload_autostart, uploads
from novel.orchestrator.jobs import JobConflict
from novel.views import ingest

router = APIRouter()

IDEMPOTENCY_HEADER = 'X-Novel-Idempotency-Key'


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


def _resolve_idempotency_key(
        header_key: str | None, query_key: str | None) -> str | None:
    key = (header_key or query_key or '').strip() or None
    if key is None:
        return None
    if not (8 <= len(key) <= 128) or any(
            ch for ch in key if not (ch.isalnum() or ch in '_-')):
        raise SourcePathRejected(
            'idempotency_key 须为 8–128 位字母数字/_/-')
    return key


def _resolve_profiles(
        base: str, profiles_json: str | None,
        *, require: bool) -> PipelineAgentProfiles | None:
    if profiles_json is not None and profiles_json.strip():
        try:
            payload = json.loads(profiles_json)
            profiles = PipelineAgentProfiles.model_validate(payload)
            return validate_pipeline_profiles(profiles)
        except (json.JSONDecodeError, ValidationError, AgentProfileRefused, ValueError) as exc:
            raise SourcePathRejected(
                f'profiles 查询参数无法按当前契约解析：{type(exc).__name__}') from exc
    settings = agent_settings.read(base)
    if settings.state == 'configured' and settings.profiles is not None:
        return settings.profiles
    if require:
        raise UnsupportedRunMode(
            '上传自动开跑需要可解析的 profiles：请传 profiles 查询参数，'
            '或先在设置页保存常用 Agent 配置')
    return None


def _replay_upload(base: str, folded) -> UploadResult:
    projected = upload_autostart.replay_result(base, folded)
    job = projected['job']
    if job is None:
        raise UnsupportedRunMode(
            '同幂等键意图仍在飞行且尚无 stage0 作业记录；请等待或稍后重试')
    return UploadResult(
        sid=projected['sid'],
        job=job,
        run=projected['run'],
        idempotency_key=projected['idempotency_key'],
        autostart=projected['autostart'],
    )


@router.post('/upload', response_model=UploadResult)
async def upload_source(
    request: Request,
    filename: str = Query(min_length=1),
    base: str = Depends(get_base),
    idempotency_key: str | None = Query(default=None),
    authorized: bool = Query(default=False),
    egress_authorized: bool = Query(default=True),
    profiles: str | None = Query(default=None),
    x_novel_idempotency_key: str | None = Header(
        default=None, alias=IDEMPOTENCY_HEADER),
) -> UploadResult:
    '''接收原始 octet-stream；在内存中越过上限的下一块立刻拒绝。

    authorized=true 时要求幂等键，并在 stage0 成功后自动 agent.start；
    authorized=false 保持仅 stage0 的兼容路径（合成测试）。
    '''
    announced = request.headers.get('content-length')
    if announced is not None:
        try:
            announced_size = int(announced)
        except ValueError as exc:
            raise SourcePathRejected(f'Content-Length 不是整数：{announced!r}') from exc
        if announced_size < 0 or announced_size > uploads.MAX_UPLOAD_BYTES:
            raise SourcePathRejected(
                f'Content-Length={announced_size}，上传上限为 {uploads.MAX_UPLOAD_BYTES} 字节')

    key = _resolve_idempotency_key(x_novel_idempotency_key, idempotency_key)
    want_autostart = bool(authorized and egress_authorized)
    if authorized and key is None:
        raise SourcePathRejected(
            'authorized=true 时必须提供幂等键（Header X-Novel-Idempotency-Key 或 query idempotency_key）')

    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > uploads.MAX_UPLOAD_BYTES:
            raise SourcePathRejected(
                f'上传内容超过 {uploads.MAX_UPLOAD_BYTES} 字节上限')

    body = bytes(raw)

    def _run() -> UploadResult:
        if key is not None:
            folded = upload_autostart.find_by_key(base, key)
            if folded is not None:
                return _replay_upload(base, folded)

        source_path, sid = uploads.save_upload(base, filename, body)
        digest = hashlib.sha256(body).hexdigest()

        if not want_autostart:
            job = jobs.run(
                base,
                'stage0.remove',
                None,
                ['--source', str(source_path)],
                follow_check=False,
            )
            return UploadResult(sid=sid, job=job)

        resolved_profiles = _resolve_profiles(base, profiles, require=True)
        assert resolved_profiles is not None
        intent = upload_autostart.record_requested(
            base,
            idempotency_key=key,
            sid=sid,
            source_sha256=digest,
            filename=filename,
            authorized=True,
            egress_authorized=True,
            profiles=resolved_profiles,
        )
        job = jobs.run(
            base,
            'stage0.remove',
            None,
            ['--source', str(source_path)],
            follow_check=False,
        )
        if job.state != 'ok':
            upload_autostart.mark_stage0_failed(
                base, intent,
                stage0_job_id=job.job_id,
                reason=f'stage0.remove state={job.state} exit={job.exit_code}')
            return UploadResult(
                sid=sid, job=job, idempotency_key=key,
                autostart='skipped_stage0_failed')
        try:
            run = agent.start(
                base, sid,
                authorized=True,
                egress_authorized=True,
                profiles=resolved_profiles,
            )
        except JobConflict as exc:
            # 书锁冲突：意图保持 pending，留给 drain；不伪造成功。
            warnings.warn(
                f'上传自动开跑暂忙（sid={sid}）：{exc}',
                RuntimeWarning, stacklevel=2)
            return UploadResult(
                sid=sid, job=job, idempotency_key=key, autostart=None)
        upload_autostart.mark_run_started(
            base, intent,
            stage0_job_id=job.job_id,
            run_id=run.run_id)
        return UploadResult(
            sid=sid, job=job, run=run,
            idempotency_key=key, autostart='started')

    return await run_in_threadpool(_run)
