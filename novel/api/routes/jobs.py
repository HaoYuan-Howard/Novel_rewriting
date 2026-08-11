#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
jobs.py —— 作业面板 P-01 的四个端点：列表 / 详情 / 日志 / **预览**。

⚠⚠ **E 期没有 `POST /api/jobs`（真启动作业）。** 这一层今天读得到作业记录、
摊得开将要执行的命令，但**发不出一个作业**——那会第一次让浏览器能花钱。
⇒ `R-11.10`「第二个请求 409」在 E 期**只验到状态码映射表那一层**
（`novel/api/errors.py::STATUS[JobConflict] == 409`），行为半边没验。
**别把它读成验过了**（登记在 `docs/schema/_api.md` §七 与仓根 `README.md`）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from contracts.schema.api import JobList, LogChunk, PreviewRequest
from contracts.schema.orchestration import JobRecord, JobStart
from novel.api.deps import get_base
from novel.errors import UpstreamMissing
from novel.orchestrator import jobs

router = APIRouter(prefix='/jobs')


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


@router.post('/preview', response_model=JobStart)
def preview_job(req: PreviewRequest, base: str = Depends(get_base)) -> JobStart:
    """把**将要执行的那条命令**摊开（`R-11.8`）。**0 成本：只算，不跑、不写、不发请求。**

    ⚠⚠ **它与真跑共用 `build_steps`，而响应就是 `JobStart` 本身**——
    真跑时 `jobs.run()` 构造的就是这个类。于是「预览与真跑是同一个对象」
    不是一句纪律，是**字面事实**。原型那条裂缝（前端用 `argv.slice(4)`
    硬编码切掉解释器前缀来渲染）在这里连发生的地方都没有。

    ⚠ 整份的 `paid` / `writes` 由 `JobStart` 的后置条件**逐步折叠**出来
    （`paid == any(s.paid for s in steps)`），本路由一个字都不算——`R-11.7`。

    ⚠ 它是本服务**唯一的 POST**，所以 CSRF 闸门（`R-11.2`）今天全靠它验。
    """
    return jobs.plan_start(req.node, req.sid, req.flags, base=base, mode=req.mode)
