#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run.py —— `GET /api/novels/:sid/run`。agent 代跑时的进度屏数据。

【这一层只【组装】别人算好的东西】信标折叠在 `orchestrator/beacon.py::fold`，
完成度在 `orchestrator/state.py::overview`——本路由不新造任何判定
（`checks.py` 头那条 `R-11.4` 的同构纪律）。信标是**观察轨迹**：
`beacon='ok'` 只说「那条命令 exit 0」，「这层过没过」的唯一判官仍是
check 报告的 badge（`R-13.6`）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from contracts.invariants import STAGES
from contracts.schema.orchestration import RunProgressView
from novel.api.deps import checked_sid, get_base
from novel.errors import UpstreamMissing
from novel.orchestrator import beacon, state

router = APIRouter(prefix='/novels')


@router.get('/{sid}/run', response_model=RunProgressView)
def get_run_progress(sid: str = Depends(checked_sid),
                     base: str = Depends(get_base)) -> RunProgressView:
    """14 步跑到哪了。**纯读，0 成本，不写盘。**

    ⚠ **书目录不存在也是 200，不是 404**：stage0.remove 的打点先于书目录
    落地，那时进度屏就得开得开——`stage_done` 全 `None`。「没这本书」与
    「这本书坏了」是两件事，后者由 `overview` 抛、按 errors 表翻。
    """
    events = [e for e in beacon.read_events(base) if e.sid == sid]
    try:
        ov = state.overview(base, sid)
        stage_done = {st.stage: st.done for st in ov.stages}
    except UpstreamMissing:
        stage_done = {s: None for s in STAGES}
    return RunProgressView(
        sid=sid,
        steps=beacon.fold(events),
        stage_done=stage_done,
        events_tail=events[-beacon.TAIL:],
    )
