#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent.py —— `/api/agent/runs`：前端一键唤起 headless agent 代跑一本书。

【这一层只组装与转发】提示词与命令行的唯一构造函数在
`novel/orchestrator/agent.py`（`build_prompt` / `build_argv`）——preview 与真跑
调同一对函数，「界面上摊开的那条」与「真的被 Popen 的那条」是同一个对象
（`R-11.8` 同构）。变更路由全部在全局 CSRF 中间件后面，不另开闸。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from contracts.schema.api import (
    AgentRunList, AgentRunPreview, AgentRunRequest, LogChunk,
)
from contracts.schema.orchestration import AgentRunRecord
from novel.api.deps import checked_sid, get_base
from novel.errors import UpstreamMissing
from novel.orchestrator import agent, jobs

router = APIRouter(prefix='/agent/runs')


def _one(base: str, run_id: str) -> AgentRunRecord:
    rec = next((r for r in agent.records(base) if r.run_id == run_id), None)
    if rec is None:
        raise UpstreamMissing(f'没有 run_id={run_id!r} 的 agent 代跑记录')
    return rec


@router.post('/preview', response_model=AgentRunPreview)
def preview_run(req: AgentRunRequest, base: str = Depends(get_base)) -> AgentRunPreview:
    """把**将要拉起的那条命令与提示词全文**摊开。**0 成本：只算，不跑、不写。**

    ⚠ 与真跑共用 `build_prompt` / `build_argv`（`R-11.8` 同构）——
    两个授权勾选框的文案都会在提示词原文里逐字变化，确认框里给人看的就是全文。
    """
    sid = checked_sid(req.sid)
    prompt = agent.build_prompt(sid, base, authorized=req.authorized,
                                egress_authorized=req.egress_authorized)
    return AgentRunPreview(argv=agent.build_argv(prompt, base), prompt=prompt,
                           authorized=req.authorized,
                           egress_authorized=req.egress_authorized)


@router.post('', response_model=AgentRunRecord, status_code=202)
def start_run(req: AgentRunRequest, base: str = Depends(get_base)) -> AgentRunRecord:
    """拉起一次 agent 代跑。**全局一把锁 + 该书无 API 作业在跑**，冲突 409。

    ⚠ `authorized` 只表示付费授权；`egress_authorized` 独立表示正文外发授权。
    二者的原文进提示词，机读值随事件留档。
    """
    sid = checked_sid(req.sid)
    return agent.start(base, sid, authorized=req.authorized,
                       egress_authorized=req.egress_authorized)


@router.get('', response_model=AgentRunList)
def list_runs(sid: str | None = Query(default=None),
              base: str = Depends(get_base)) -> AgentRunList:
    """代跑记录，新的在前。`?sid=` 只筛不改判（同 `GET /api/jobs`）。"""
    recs = agent.records(base)
    if sid is not None:
        recs = [r for r in recs if r.start.sid == sid]
    return AgentRunList(runs=recs)


@router.get('/{run_id}/log', response_model=LogChunk)
def get_run_log(run_id: str, offset: int = Query(default=0, ge=0),
                base: str = Depends(get_base)) -> LogChunk:
    """按**字节**续读 agent 进程日志（与作业日志同一条读路，
    含「先确认存在」那道闸——见 `jobs.py` 日志路由的注释）。"""
    _one(base, run_id)
    text, nxt = jobs.read_log(base, agent.LOG_PREFIX + run_id, from_byte=offset)
    return LogChunk(text=text, next_byte=nxt)


@router.post('/{run_id}/stop', response_model=AgentRunRecord)
def stop_run(run_id: str, base: str = Depends(get_base)) -> AgentRunRecord:
    """熄火。**付费进程的必备闸**：Windows 上 `taskkill /T` 整棵树端掉
    （cmd → node → agent 起的 `novel.cli` 孙进程）。"""
    _one(base, run_id)
    return agent.stop(base, run_id)
