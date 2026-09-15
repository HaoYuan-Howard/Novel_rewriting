#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent.py —— `/api/agent/runs`：前端一键唤起 headless agent 代跑一本书。

【这一层只组装与转发】冻结 profiles 与范围上界的唯一 planner 在
`novel/orchestrator/agent.py::plan`——preview、单本启动与 batch 入队
共享它，「界面上摊开的那份」与「真被 Supervisor 执行的那份」是同一个对象
（`R-11.8` 同构）。变更路由全部在全局 CSRF 中间件后面，不另开闸。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from contracts.schema.api import (
    AgentBatchRequest, AgentRunList, AgentRunPreview, AgentRunRequest, AgentRunContinuationView, LogChunk,
)
from contracts.schema.orchestration import AgentQueueView, AgentRunRecord
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
    """把**将要冻结的配置全文**摊开。**0 成本：只算，不跑、不写。**

    ⚠ 与真跑共用 ``agent.plan``（`R-11.8` 同构）——Commander 退役后不再有
    顶层进程命令行；摊开的是关闭式校验后的完整 profiles、两项授权与范围上界。
    """
    sid = checked_sid(req.sid)
    planned = agent.plan(
        base, sid, authorized=req.authorized,
        egress_authorized=req.egress_authorized, profiles=req.profiles,
        **({'continuation': req.continuation} if req.continuation is not None else {}),
        **({'pause_before_node': req.pause_before_node} if 'pause_before_node' in req.model_fields_set else {}))
    return AgentRunPreview(argv=None, prompt=None,
                           authorized=req.authorized,
                           egress_authorized=req.egress_authorized,
                           profiles=planned.profiles, continuation=planned.continuation,
                           pause_before_node=planned.pause_before_node)


@router.post('', response_model=AgentRunRecord, status_code=202)
def start_run(req: AgentRunRequest, base: str = Depends(get_base)) -> AgentRunRecord:
    """拉起一次 agent 代跑。**全局一把锁 + 该书无 API 作业在跑**，冲突 409。

    ⚠ `authorized` 只表示付费授权；`egress_authorized` 独立表示正文外发授权。
    二者的原文进提示词，机读值随事件留档。
    """
    sid = checked_sid(req.sid)
    return agent.start(base, sid, authorized=req.authorized,
                       egress_authorized=req.egress_authorized,
                       profiles=req.profiles,
                       **({'continuation': req.continuation} if req.continuation is not None else {}),
                       **({'pause_before_node': req.pause_before_node} if 'pause_before_node' in req.model_fields_set else {}))


@router.get('/{run_id}/continuation', response_model=AgentRunContinuationView)
def get_continuation(run_id: str, base: str = Depends(get_base)) -> AgentRunContinuationView:
    from novel.orchestrator import agent_continuation
    return agent_continuation.inspect(base, run_id)


@router.post('/batch', response_model=AgentQueueView, status_code=202)
def start_batch(req: AgentBatchRequest,
                base: str = Depends(get_base)) -> AgentQueueView:
    """一次交 N 本：能起的立刻起，超出并发闸的排队。**202 = 已受理，不是已跑完。**

    ⚠ **部分失败不回滚**，逐本的落点全在响应里（`running` / `pending` / `rejected`）。
    抛一个「批量失败」会把已经起来的那几本藏掉——人看到报错，盘上却真有进程在跑。
    ⚠ 每个 sid 仍走 `checked_sid` 那道边界校验：一个写错的 sid 不该让整批停下，
    但也不该被当成一本书塞进队列。
    """
    sids = [checked_sid(s) for s in req.sids]
    return agent.enqueue(base, sids, authorized=req.authorized,
                         egress_authorized=req.egress_authorized,
                         profiles=req.profiles)


@router.get('/queue', response_model=AgentQueueView)
def get_queue(base: str = Depends(get_base)) -> AgentQueueView:
    """谁在跑、谁在排、闸开多大。**排队部分只在内存**——服务重启即清空
    （`R-14.6` 那条互斥锁的同族，界面要照实写「重启不保留」）。"""
    return agent.queue_view(base)


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


@router.post('/{run_id}/stop', response_model=AgentRunRecord, status_code=202)
def stop_run(run_id: str, base: str = Depends(get_base)) -> AgentRunRecord:
    """异步熄火。**202 只表示已受理**：精确 driver 先取消进程树，
    worker 完成 claim/beacon drain 后才写最终 ended；路由层不用 10 秒超时
    把“仍在安全收尾”伪报成失败。"""
    _one(base, run_id)
    return agent.request_stop(base, run_id)


@router.post('/{run_id}/pause', response_model=AgentRunRecord, status_code=202)
def pause_run(run_id: str, base: str = Depends(get_base)) -> AgentRunRecord:
    """异步暂停：立即挡住新 claim，已执行节点自然闭合后才进入 paused。"""
    _one(base, run_id)
    return agent.request_pause(base, run_id)


@router.post('/{run_id}/resume', response_model=AgentRunRecord, status_code=202)
def resume_run(run_id: str, base: str = Depends(get_base)) -> AgentRunRecord:
    """只恢复本服务进程仍持有 writer/owner lease 的 paused run。"""
    _one(base, run_id)
    return agent.resume(base, run_id)
