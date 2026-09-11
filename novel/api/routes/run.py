#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run.py —— `GET /api/novels/:sid/run`。agent 代跑时的进度屏数据。

【这一层只【组装】别人算好的东西】信标折叠在 `orchestrator/beacon.py::fold`，
完成度与续跑前沿在 `orchestrator/operator.py::progress_snapshot`——本路由不新造任何判定
（`checks.py` 头那条 `R-11.4` 的同构纪律）。信标是**观察轨迹**：
`beacon='ok'` 只说「那条命令 exit 0」，「这层过没过」的唯一判官仍是
check 报告的 badge（`R-13.6`）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from contracts.invariants import STAGES, commander_work_unit
from contracts.schema.orchestration import RunProgressView, RunResumeFrontierView
from contracts.schema.api import WorkUnitGrantPreview, WorkUnitGrantRequest, WorkUnitGrantResult
from novel.api.deps import checked_sid, get_base
from novel.errors import UpstreamMissing
from novel.orchestrator import beacon, operator, work_unit_grants
from novel.views import work_units, run_metrics
from contracts.schema.run_metrics import RunMetricsView

router = APIRouter(prefix='/novels')


@router.get('/{sid}/work-units/{unit_id}/grant', response_model=WorkUnitGrantPreview)
def preview_work_unit_grant(unit_id: str, sid: str = Depends(checked_sid),
                           additional: int = Query(default=1, ge=1, le=4),
                           base: str = Depends(get_base)) -> WorkUnitGrantPreview:
    return work_unit_grants.inspect(base, sid, unit_id, additional)


@router.post('/{sid}/work-units/{unit_id}/grant', response_model=WorkUnitGrantResult)
def apply_work_unit_grant(unit_id: str, request: WorkUnitGrantRequest,
                         sid: str = Depends(checked_sid), base: str = Depends(get_base)) -> WorkUnitGrantResult:
    return work_unit_grants.apply(base, sid, unit_id, request)


@router.get('/{sid}/run/metrics', response_model=RunMetricsView)
def get_run_metrics(sid: str = Depends(checked_sid), base: str = Depends(get_base),
                    run_id: str | None = Query(default=None, min_length=1)) -> RunMetricsView:
    return run_metrics.get(base, sid, run_id)


@router.get('/{sid}/run', response_model=RunProgressView)
def get_run_progress(sid: str = Depends(checked_sid),
                     base: str = Depends(get_base)) -> RunProgressView:
    """`NODES` 全图跑到哪了。**纯读，0 成本，不写盘。**

    ⚠ **书目录不存在也是 200，不是 404**：stage0.remove 的打点先于书目录
    落地，那时进度屏就得开得开——`stage_done` 全 `None`。「没这本书」与
    「这本书坏了」是两件事，后者由 `overview` 抛、按 errors 表翻。
    """
    events = [e for e in beacon.read_events(base) if e.sid == sid]
    try:
        ov, resume_frontier = operator.progress_snapshot(base, sid)
        stage_done = {st.stage: st.done for st in ov.stages}
    except UpstreamMissing:
        stage_done = {s: None for s in STAGES}
        resume_frontier = RunResumeFrontierView(
            state='unknown',
            why='书目录尚未形成，当前无法判定续跑点',
            next_handler='unknown',
            verified_skipped_nodes=[],
            verified_skipped_count=0,
        )
    units, units_unavailable = work_units.get(base, sid)
    identity = commander_work_unit(resume_frontier.waiting_on or '')
    current_node = identity[0] if identity else resume_frontier.node
    current_scope = identity[1] if identity else None
    recovery = None if units is None or resume_frontier.state == 'unknown' else [
        f"{row['scope']}: {row['recovery_action']}" for row in units
        if row['node'] == current_node and row['role'] == resume_frontier.next_handler
        and (current_scope is None or row['scope'] == current_scope)
        and row['recovery_action'] is not None]
    return RunProgressView(
        sid=sid,
        resume_frontier=resume_frontier,
        steps=beacon.fold(events),
        stage_done=stage_done,
        events_tail=[beacon.event_view(event) for event in events[-beacon.TAIL:]],
        work_units=units, work_units_unavailable_reason=units_unavailable,
        current_recovery_actions=recovery,
    )
