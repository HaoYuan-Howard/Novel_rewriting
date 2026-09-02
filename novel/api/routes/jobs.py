#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
jobs.py —— 作业面板 P-01：列表 / 详情 / 日志 / 预览 / 异步启动。

通用启动只接收结构化 `{sid,node,flags,executor_backend?,egress_authorized?}`，
先用与预览相同的 `plan_start()` 构造命令，再交给后台执行器。同一本书的第二个请求
由书级锁返回 409；不同小说互不阻塞。
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from contracts.invariants import stage2_review_required
from contracts.schema.api import JobList, LogChunk, LogStreamEvent, PreviewRequest
from contracts.schema.orchestration import JobRecord, JobStart
from novel.api.deps import checked_sid, get_base
from novel.errors import UnsupportedRunMode, UpstreamMissing
from novel.orchestrator import approvals, jobs, nodes

router = APIRouter(prefix='/jobs')


def _chapter_selection(flags: list[str]) -> list[int] | None:
    if '--chapters' not in nodes.flag_tokens(flags):
        return None
    raw = nodes.flag_value(flags, '--chapters')
    try:
        chapters = [int(value) for value in (raw or '').split(',')]
    except ValueError as exc:
        raise UnsupportedRunMode(
            '--chapters must be comma-separated ordinals') from exc
    if chapters != sorted(set(chapters)) or any(value < 1 for value in chapters):
        raise UnsupportedRunMode(
            '--chapters ordinals must be sorted, unique and positive')
    return chapters


def _refuse_prepared_recovery_api(node: str, flags: list[str]) -> None:
    """prepared recovery 必须复用 target run；通用 HTTP job 做不到这件事。"""
    tokens = set(nodes.flag_tokens(flags))
    recovery = tokens & {
        '--emit-prepared-recovery-workpack', '--recover-prepared'}
    if node == 'stage5.write' and recovery:
        selected = ', '.join(sorted(recovery))
        raise UnsupportedRunMode(
            f'{selected} 只允许由能显式复用同一 target run_id 的 CLI runner 执行；'
            'POST /api/jobs/preview 与 POST /api/jobs 会为每个 job 生成新的 run_id，'
            '不能安全承载 hash-bound prepared recovery')


def _one(base: str, job_id: str) -> JobRecord:
    rec = next((r for r in jobs.records(base) if r.job_id == job_id), None)
    if rec is None:
        raise UpstreamMissing(f'没有 job_id={job_id!r} 的作业记录')
    return rec


@router.get('', response_model=JobList)
def list_jobs(sid: str | None = Query(default=None),
              base: str = Depends(get_base)) -> JobList:
    """作业记录，新的在前。`?sid=` 只筛不改判。

    ⚠ **`running` 一律由 `fold()` 对着进程内的活锁重新折**（`R-11.11`）：
    服务重启后那把锁是空的，于是上一次留下的 `started` 会被折成 **`unknown`**
    而不是「成功」。那一条在 `novel/orchestrator/jobs.py` 里，本路由**不重折一遍**。
    """
    recs = jobs.records(base)
    if sid is not None:
        recs = [r for r in recs if r.start.sid == sid]
    return JobList(jobs=recs)


@router.post('', response_model=JobRecord, status_code=202)
def start_job(req: PreviewRequest, base: str = Depends(get_base)) -> JobRecord:
    sid = checked_sid(req.sid) if req.sid is not None else None
    _refuse_prepared_recovery_api(req.node, req.flags)
    planned = jobs.plan_start(
        req.node, sid, req.flags, base=base,
        executor_backend=req.executor_backend,
        egress_authorized=req.egress_authorized)
    prepares: list[Callable[[], None]] = []
    # API 只做占住同书锁后的早反馈；CLI 仍是所有调用方共用的最终执行门。
    # global 与 blueprint 两个 producer 明确豁免，Stage4 起的付费消费、免费
    # context 和 assemble 则必须绑定当前全局逐项复核。preview 不走这里，始终可用。
    if stage2_review_required(req.node, paid=planned.paid):
        if sid is None:
            raise UnsupportedRunMode(
                f'{req.node} requires a novel sid for Stage2 review verification')
        prepares.append(
            lambda: approvals.require_stage2_review(base, sid, req.node))
    tokens = nodes.flag_tokens(req.flags)
    # A 的付费返修/重规划必须消费当前 hash-bound typed 决定；默认首次设计没有
    # 这张票。占住书锁后先解析一次可执行 scope，CLI 仍在 subprocess 内终验。
    if req.node == 'stage4.design' and (
        '--review-token' in tokens or '--replan' in tokens
    ):
        if sid is None:
            raise UnsupportedRunMode('stage4.design repair requires a novel sid')
        review_token = nodes.flag_value(req.flags, '--review-token')
        if '--replan' not in tokens:
            prepares.append(lambda review_token=review_token: (
                approvals.resolve_design_action(
                    base, sid, review_token, expected='repair')))
        else:
            prepares.append(lambda review_token=review_token: (
                approvals.resolve_design_action(
                    base, sid, review_token, expected='replan')))
    elif req.node == 'stage4.design' and not tokens:
        if sid is None:
            raise UnsupportedRunMode('stage4.design requires a novel sid')
        prepares.append(lambda: approvals.authorize_design_initial(base, sid))
    # Stage4B 的两条“产新 Plan”入口共用 A all-accept 票；check/revalidate/
    # apply-edit/dry-run 是旧 Plan 的零成本恢复/诊断，不得被 A 反向堵死。
    if req.node == 'stage4.plan' and (
        not tokens or '--apply-binding' in tokens
    ):
        if sid is None:
            raise UnsupportedRunMode('stage4.plan requires a novel sid')
        prepares.append(lambda: approvals.authorize_plan(base, sid))
    if req.node == 'stage4.context':
        if sid is None:
            raise UnsupportedRunMode('stage4.context requires a novel sid')
        prepares.append(lambda: approvals.authorize_context(base, sid))
    # writing 授权保护的是正文外发 / 付费 LLM 调用。Stage5 同一节点下还有
    # rebuild/recheck/revalidate/dry-run 等零成本恢复路；若也绑 writing，恢复路
    # 会在入口处被自己堵死。`planned.paid` 与 preview 展示的是同一份折叠结论，
    # 本路由不再按 flag 重算一份贵贱。
    if req.node == 'stage5.write' and planned.paid:
        if sid is None:
            raise UnsupportedRunMode('stage5 writing requires a novel sid')
        chapters = _chapter_selection(req.flags)
        revise = '--revise' in tokens
        prepares.append(lambda: approvals.authorize_writing(
            base, sid, chapters, requires_fresh_review=revise))

    def prepare() -> None:
        for check in prepares:
            check()

    return jobs.start(
        base, req.node, sid, req.flags,
        executor_backend=planned.executor_backend,
        egress_authorized=planned.egress_authorized,
        steps=planned.steps, prepare=prepare if prepares else None)


@router.get('/{job_id}', response_model=JobRecord)
def get_job(job_id: str, base: str = Depends(get_base)) -> JobRecord:
    return _one(base, job_id)


@router.get('/{job_id}/log', response_model=LogChunk)
def get_job_log(job_id: str, offset: int = Query(default=0, ge=0),
                base: str = Depends(get_base)) -> LogChunk:
    """按**字节**续读子进程日志。

    ⚠ 先确认这个作业存在再读日志：一个不存在的 job_id 直接读会拿到
    「空日志 + offset 原样」——那与「作业在跑、还没输出」**长得一模一样**，
    而前者该是 404。
    ⚠ 中文一字跨 3 字节，日志是按字节追加的 ⇒ 只在读的一侧解码，
    末尾切在字中间的那几个字节由 `jobs.read_log` 裁掉、留给下一次。
    """
    _one(base, job_id)
    text, nxt = jobs.read_log(base, job_id, from_byte=offset)
    return LogChunk(text=text, next_byte=nxt)


def _sse(event: str, payload: LogStreamEvent) -> str:
    """一条 SSE；`id` 就是下一次字节 offset，不能用字符数代替。"""
    data = json.dumps(payload.model_dump(mode='json'), ensure_ascii=False)
    return f'id: {payload.next_byte}\nevent: {event}\ndata: {data}\n\n'


async def _job_log_events(base: str, job_id: str, offset: int, request: Request):
    current = offset
    heartbeat_at = asyncio.get_running_loop().time() + 10.0
    while True:
        if await request.is_disconnected():
            return
        text, nxt = jobs.read_log(base, job_id, from_byte=current)
        if nxt > current:
            current = nxt
            yield _sse('log', LogStreamEvent(
                text=text, next_byte=current, done=False, state=None))
            # 大日志一次最多读 2 MiB；有余量时立即续，不人为加 100ms 延迟。
            continue

        rec = _one(base, job_id)
        if rec.state != 'running':
            yield _sse('end', LogStreamEvent(
                text='', next_byte=current, done=True, state=rec.state))
            return

        now = asyncio.get_running_loop().time()
        if now >= heartbeat_at:
            yield ': keep-alive\n\n'
            heartbeat_at = now + 10.0
        await asyncio.sleep(0.1)


@router.get('/{job_id}/stream')
async def stream_job_log(job_id: str, request: Request,
                         offset: int = Query(default=0, ge=0),
                         base: str = Depends(get_base)) -> StreamingResponse:
    """SSE 日志流；query offset 与浏览器 `Last-Event-ID` 取较新的那个。

    日志仍以轮询端点的同一条 `jobs.read_log` 为权威读取面。断线不会建第二套游标：
    每个 data event 的 SSE id 都是 `next_byte`，重连从该字节继续；作业一进终态，
    先排空已落盘日志，再发唯一 `end` event 并结束生成器。
    """
    _one(base, job_id)
    last = request.headers.get('last-event-id')
    if last is not None:
        try:
            resumed = int(last)
        except ValueError:
            resumed = offset
        if resumed >= 0:
            offset = max(offset, resumed)
    return StreamingResponse(
        _job_log_events(base, job_id, offset, request),
        media_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        },
    )


@router.post('/preview', response_model=JobStart)
def preview_job(req: PreviewRequest, base: str = Depends(get_base)) -> JobStart:
    """把**将要执行的那条命令**摊开（`R-11.8`）。**0 成本：只算，不跑、不写、不发请求。**

    ⚠⚠ **它与真跑共用 `build_steps`，而响应就是 `JobStart` 本身**——
    真跑时 `jobs.start()` 构造的就是这个类。于是「预览与真跑是同一个对象」
    不是一句纪律，是**字面事实**。原型那条裂缝（前端用 `argv.slice(4)`
    硬编码切掉解释器前缀来渲染）在这里连发生的地方都没有。

    ⚠ 整份的 `paid` / `writes` 由 `JobStart` 的后置条件**逐步折叠**出来
    （`paid == any(s.paid for s in steps)`），本路由一个字都不算——`R-11.7`。

    ⚠ 它与真启动端点都经过全局 CSRF 中间件；预览本身仍然 0 成本、无写盘。
    """
    sid = checked_sid(req.sid) if req.sid is not None else None
    _refuse_prepared_recovery_api(req.node, req.flags)
    # Preview accepts an otherwise well-shaped action before the human has
    # supplied its required audit values.  ``nodes.build_steps`` fills those
    # omissions with visibly non-executable ``待填·...`` tokens.  POST /jobs
    # intentionally does *not* enable this mode, so NodeSpec still rejects
    # missing values and rejects the placeholder tokens themselves.
    return jobs.plan_start(
        req.node, sid, req.flags, base=base,
        executor_backend=req.executor_backend,
        egress_authorized=req.egress_authorized,
        placeholders=True,
    )
