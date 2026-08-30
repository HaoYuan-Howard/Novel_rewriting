#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""确定性 operator 下一动作。

本模块不拥有任何 stage 特例：节点顺序、恢复 flags 与人工闸全部由
``queue.plan_book_from_overview`` 产生；本模块只把同一次 operator 快照上的
overview、inbox 与 lane 的最早一项组合成严格响应。它纯读、不缓存、不写服务账。
"""
from __future__ import annotations

import hashlib
import json
import os

from contracts.invariants import NODES, STAGES
from contracts.schema.api import ExecutionPermit, OperatorAction, OperatorNext
from contracts.schema.orchestration import NovelOverview, QueueItem
from novel.errors import ArtifactUnreadable
from novel.orchestrator import queue, state
from novel.orchestrator.inspection import (
    capture_operator_snapshot,
    read_surface_seal,
)
from novel.orchestrator.nodes import is_paid, write_effect
from novel.views.inbox import inbox_from_snapshot


def canonical_sha256(value: object) -> str:
    """对外可复算的 JSON 尺子：UTF-8、键排序、无空白、禁止 NaN。"""
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False,
    ).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def _action(
    *, base: str, sid: str, code: str, surface_seal: str,
    overview: NovelOverview, item: QueueItem,
) -> OperatorAction:
    resume_verdict, _resume_why = state.resume_verdict(
        overview, item.node)
    paid = is_paid(item.node, item.flags)
    writes = write_effect(item.node, item.flags)
    payload = {
        'node': item.node,
        'flags': list(item.flags),
        'resume_verdict': resume_verdict,
        'why': item.why,
        'waiting_on': item.waiting_on,
        'paid': paid,
        'writes': writes,
    }
    return OperatorAction(
        **payload,
        action_sha256=canonical_sha256({
            'base': base,
            'sid': sid,
            'code_version': code,
            'surface_seal': surface_seal,
            **payload,
        }),
    )


def next_action(base: str | os.PathLike[str], sid: str) -> OperatorNext:
    """返回当前盘面的唯一下一动作；blocked/complete 都是成功读到的状态。"""
    snapshot = capture_operator_snapshot(base, sid)
    inbox = inbox_from_snapshot(snapshot)
    todo, blocked = queue.plan_book_from_overview(
        snapshot.base, sid, snapshot.overview,
        until_stage=STAGES[-1], follow_check=True)
    rank = {node: index for index, node in enumerate(NODES)}
    selected = min(
        (*todo, *blocked),
        key=lambda item: rank[item.node],
        default=None,
    )

    action = None
    next_state = 'pipeline_complete'
    if selected is not None:
        action = _action(
            base=os.fspath(snapshot.base), sid=sid, code=snapshot.code,
            surface_seal=snapshot.surface_seal,
            overview=snapshot.overview, item=selected)
        next_state = 'blocked' if selected.waiting_on else 'ready'

    if read_surface_seal(
        snapshot.base, sid, code=snapshot.code,
    ) != snapshot.surface_seal:
        raise ArtifactUnreadable(
            'operator next 规划期间本书代码/产物/审批盘面发生变化；'
            '拒绝返回混合时点的下一动作，请重试')

    return OperatorNext(
        base=os.fspath(snapshot.base), sid=sid,
        code_version=snapshot.code, surface_seal=snapshot.surface_seal,
        state=next_state, overview=snapshot.overview, inbox=inbox,
        action=action,
    )


def permit(
    base: str | os.PathLike[str], sid: str, node: str,
    flags: tuple[str, ...] | list[str] = (),
) -> ExecutionPermit:
    """按当前 ``operator next`` 为 exact node/flags 铸纯读执行许可。"""
    current = next_action(base, sid)
    # 延迟导入：permit 策略消费 OperatorNext，但 next 本身不依赖许可层。
    from novel.orchestrator.execution_permit import permit_from_next
    return permit_from_next(current, node, flags)


__all__ = ['canonical_sha256', 'next_action', 'permit']
