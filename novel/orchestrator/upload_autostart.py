#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""upload_autostart.py —— 上传即授权自动开跑（outbox，R-11.28）。

【它解决什么】「上传+自动开跑意图已接受」与「agent.start 已成功」是两件事：
在 stage0 / 付费 start 前先按客户端幂等键耐久；崩溃后由 drain 对
stage0 已成功的意图补一次 agent.start。stage0 失败绝不开付费 run。

【边界】账是 base 级全局文件（幂等键在 sid 已知前由客户端给出）。
内容寻址去重仍由 ``uploads.save_upload`` 负责，本模块不另造书身份。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable

from contracts.schema.orchestration import (
    AgentRunRecord,
    JobRecord,
    PipelineAgentProfiles,
    UploadAutostartEventV1,
)
from novel import paths
from novel.appendlog import append_line
from novel.errors import NovelError, TerminalState

AgentStart = Callable[..., AgentRunRecord]


@dataclass(frozen=True)
class FoldedIntent:
    """同一幂等键折叠后的最新状态。"""

    requested: UploadAutostartEventV1
    terminal: UploadAutostartEventV1 | None

    @property
    def closed(self) -> bool:
        return self.terminal is not None

    @property
    def sid(self) -> str:
        return self.requested.sid


def intents_path(base: str | os.PathLike[str]):
    return paths.upload_autostart_intents_path(base)


def append_event(base: str | os.PathLike[str], event: UploadAutostartEventV1) -> None:
    append_line(intents_path(base),
                json.dumps(event.model_dump(mode='json'), ensure_ascii=False))


def read_events(base: str | os.PathLike[str]) -> list[UploadAutostartEventV1]:
    """读意图账；只容忍截断的最后一行（同队列账的读法）。"""
    p = intents_path(base)
    if not p.is_file():
        return []
    raw = p.read_bytes().decode('utf-8', errors='replace').splitlines()
    out: list[UploadAutostartEventV1] = []
    for index, line in enumerate(raw):
        if not line.strip():
            continue
        try:
            out.append(UploadAutostartEventV1.model_validate(json.loads(line)))
        except Exception:  # noqa: BLE001 - 只有被打断的尾行可恢复
            if index == len(raw) - 1:
                break
            raise
    return out


def find_by_key(base: str | os.PathLike[str], key: str) -> FoldedIntent | None:
    """按幂等键折叠：取最早 requested 与之后任一终态事件。"""
    requested: UploadAutostartEventV1 | None = None
    terminal: UploadAutostartEventV1 | None = None
    for event in read_events(base):
        if event.idempotency_key != key:
            continue
        if event.event == 'requested':
            if requested is None:
                requested = event
            continue
        terminal = event
    if requested is None:
        return None
    return FoldedIntent(requested=requested, terminal=terminal)


def record_requested(
        base: str | os.PathLike[str], *,
        idempotency_key: str, sid: str, source_sha256: str, filename: str,
        authorized: bool, egress_authorized: bool,
        profiles: PipelineAgentProfiles, reason: str = '',
) -> UploadAutostartEventV1:
    """在任何 stage0 / agent 作业前铸一条自动开跑意图（先耐久）。"""
    event = UploadAutostartEventV1(
        event='requested',
        idempotency_key=idempotency_key,
        sid=sid,
        source_sha256=source_sha256,
        filename=filename,
        authorized=authorized,
        egress_authorized=egress_authorized,
        profiles=profiles,
        stage0_job_id=None,
        run_id=None,
        created_at=int(time.time()),
        reason=reason,
    )
    append_event(base, event)
    return event


def _mark(base: str | os.PathLike[str], template: UploadAutostartEventV1, *,
          event: str, stage0_job_id: str | None = None,
          run_id: str | None = None, reason: str = '') -> UploadAutostartEventV1:
    row = UploadAutostartEventV1(
        event=event,  # type: ignore[arg-type]
        idempotency_key=template.idempotency_key,
        sid=template.sid,
        source_sha256=template.source_sha256,
        filename=template.filename,
        authorized=template.authorized,
        egress_authorized=template.egress_authorized,
        profiles=template.profiles,
        stage0_job_id=stage0_job_id if stage0_job_id is not None else template.stage0_job_id,
        run_id=run_id if run_id is not None else template.run_id,
        created_at=int(time.time()),
        reason=reason,
    )
    append_event(base, row)
    return row


def mark_stage0_failed(
        base: str | os.PathLike[str], template: UploadAutostartEventV1, *,
        stage0_job_id: str | None, reason: str) -> UploadAutostartEventV1:
    return _mark(base, template, event='stage0_failed',
                 stage0_job_id=stage0_job_id, reason=reason)


def mark_run_started(
        base: str | os.PathLike[str], template: UploadAutostartEventV1, *,
        stage0_job_id: str | None, run_id: str,
        reason: str = '') -> UploadAutostartEventV1:
    return _mark(base, template, event='run_started',
                 stage0_job_id=stage0_job_id, run_id=run_id, reason=reason)


def mark_abandoned(
        base: str | os.PathLike[str], template: UploadAutostartEventV1, *,
        reason: str) -> UploadAutostartEventV1:
    return _mark(base, template, event='abandoned', reason=reason)


def pending_autostarts(base: str | os.PathLike[str]) -> list[UploadAutostartEventV1]:
    """未闭合的 requested（无终态），按提出顺序。"""
    closed = {
        e.idempotency_key for e in read_events(base)
        if e.event != 'requested'
    }
    seen: set[str] = set()
    out: list[UploadAutostartEventV1] = []
    for event in read_events(base):
        if event.event != 'requested':
            continue
        if event.idempotency_key in closed or event.idempotency_key in seen:
            continue
        seen.add(event.idempotency_key)
        out.append(event)
    return out


def _stage0_succeeded(base: str | os.PathLike[str], sid: str) -> bool:
    """盘上已有该书目录且 stage0 正式稿可定位 ⇒ 视为 stage0 成功。"""
    return paths.find_novel_dir(base, sid) is not None


def _lookup_job(base: str | os.PathLike[str], job_id: str | None) -> JobRecord | None:
    if not job_id:
        return None
    from novel.orchestrator import jobs
    for record in jobs.fold(jobs.read_events(base)):
        if record.job_id == job_id:
            return record
    return None


def _lookup_run(base: str | os.PathLike[str], run_id: str | None) -> AgentRunRecord | None:
    if not run_id:
        return None
    from novel.orchestrator import agent
    for record in agent.fold(agent.read_events(base)):
        if record.run_id == run_id:
            return record
    return None


def replay_result(base: str | os.PathLike[str], folded: FoldedIntent,
                  *, job: JobRecord | None = None) -> dict:
    """把折叠意图投影成 UploadResult 可构造字段（不含强制 JobRecord 时由调用方补）。"""
    terminal = folded.terminal
    stage0_job_id = (
        terminal.stage0_job_id if terminal and terminal.stage0_job_id
        else folded.requested.stage0_job_id)
    run_id = terminal.run_id if terminal else None
    resolved_job = job or _lookup_job(base, stage0_job_id)
    run = _lookup_run(base, run_id) if run_id else None
    if terminal is None:
        autostart = 'replay'
    elif terminal.event == 'run_started':
        autostart = 'replay'
    elif terminal.event == 'stage0_failed':
        autostart = 'skipped_stage0_failed'
    else:
        autostart = 'replay'
    return {
        'sid': folded.sid,
        'job': resolved_job,
        'run': run,
        'idempotency_key': folded.requested.idempotency_key,
        'autostart': autostart,
    }


def drain(base: str | os.PathLike[str], *,
          agent_start: AgentStart | None = None) -> str:
    """消费未闭合自动开跑意图；返回 ``idle/done/busy/failed``。

    · ``idle``：无 pending。
    · ``done``：至少一个 pending 成功补到 run_started，或 stage0 失败已收口。
    · ``busy``：agent.start 书锁/并发冲突，意图保持排队。
    · ``failed``：确定性拒绝（授权/配置漂移等）；意图 abandoned。

    只对 stage0 已成功的意图调用 agent.start；stage0 未完成的保持 pending
    （须由同键 HTTP 重试或人工路径继续），绝不在 stage0 失败时开付费 run。
    """
    from novel.orchestrator.jobs import JobConflict

    start = agent_start
    if start is None:
        from novel.orchestrator import agent as agent_mod

        def start(base_, sid, *, authorized, egress_authorized, profiles):
            return agent_mod.start(
                base_, sid, authorized=authorized,
                egress_authorized=egress_authorized, profiles=profiles)

    pendings = pending_autostarts(base)
    if not pendings:
        return 'idle'
    progressed = False
    busy = False
    for row in pendings:
        if not _stage0_succeeded(base, row.sid):
            # stage0 尚未成功：不 start、不收口；留给同键重试或人工。
            continue
        if not (row.authorized and row.egress_authorized):
            mark_abandoned(base, row, reason='意图授权已失效，拒绝补开付费 run')
            progressed = True
            continue
        try:
            record = start(
                base, row.sid,
                authorized=row.authorized,
                egress_authorized=row.egress_authorized,
                profiles=row.profiles,
            )
        except JobConflict:
            busy = True
            continue
        except (NovelError, TerminalState, OSError, ValueError) as exc:
            mark_abandoned(
                base, row,
                reason=f'{type(exc).__name__}: {exc}'[:2000])
            progressed = True
            continue
        mark_run_started(
            base, row,
            stage0_job_id=row.stage0_job_id,
            run_id=record.run_id,
            reason='启动 reconcile 补开')
        progressed = True
    if busy and not progressed:
        return 'busy'
    if progressed:
        return 'done'
    return 'idle'
