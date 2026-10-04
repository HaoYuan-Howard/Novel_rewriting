#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""确定性 operator 下一动作。

本模块不拥有任何 stage 特例：节点顺序、恢复 flags 与人工闸全部由
``queue.plan_book_from_overview`` 产生；本模块只把同一次 operator 快照上的
overview、inbox 与 lane 的最早一项组合成严格响应。它纯读、不写服务账；相同文件
只在本次 before/after surface seal 之间复用解析结果，调用结束立即丢弃。
"""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import dataclass

from contracts.invariants import INBOX_KIND, NODES, STAGES
from contracts.schema.api import (
    ExecutionPermit, InboxItem, InboxView, OperatorAction, OperatorNext,
    OperatorNextDecision,
)
from contracts.schema.orchestration import (
    NovelOverview, QueueItem, RunResumeFrontierView,
)
from novel import paths
from novel.errors import ArtifactUnreadable
from novel.orchestrator import queue, state
from novel.orchestrator.inspection import (
    OperatorReadSnapshot,
    capture_operator_snapshot,
    read_surface_seal,
)
from novel.orchestrator.nodes import is_paid, write_effect
from novel.store import readonly_snapshot_memo
from novel.orchestrator.inbox_projection import _OPERATOR_FINAL_CAS, inbox_from_snapshot


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
        overview, item.node, flags=tuple(item.flags))
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


@dataclass(frozen=True)
class _LanePlan:
    state: str
    action: OperatorAction | None
    verified_skipped_nodes: tuple[str, ...]


def _lane_from_snapshot(
    snapshot: OperatorReadSnapshot,
) -> _LanePlan:
    """从同一 overview 坐标选最早动作，并保留被 planner 跳过的前缀。"""
    sid = snapshot.sid
    todo, blocked = queue.plan_book_from_overview(
        snapshot.base, sid, snapshot.overview,
        until_stage=STAGES[-1], follow_check=True,
        # AgentRun 每个 action 落盘后都会重新进入本函数采新快照；因此能消费
        # queue/state 共同证明的 canonical REDO，不会冻结一串旧下游 argv。
        allow_deterministic_redo=True)
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

    frontier = len(NODES) if selected is None else rank[selected.node]
    planned = {item.node for item in (*todo, *blocked)}
    skipped = tuple(
        node for node in list(NODES)[:frontier]
        if node not in planned
    )
    return _LanePlan(
        state=next_state, action=action,
        verified_skipped_nodes=skipped)


def _require_snapshot_current(snapshot: OperatorReadSnapshot) -> None:
    if read_surface_seal(
        snapshot.base, snapshot.sid, code=snapshot.code,
    ) != snapshot.surface_seal:
        raise ArtifactUnreadable(
            'operator next 规划期间本书代码/产物/审批盘面发生变化；'
            '拒绝返回混合时点的下一动作，请重试')


def _next_from_snapshot(snapshot: OperatorReadSnapshot) -> OperatorNext:
    """只消费一份已冻结快照规划 next，并为 ready 同次铸 execution token。"""
    sid = snapshot.sid
    # ⚠ F8b-3（S-05 deprecation）：旧链 prepared 提交尾窗 lane（stage2 edit
    # commit／stage4 repair aggregate／prepared delivery）随旧链退役拆除——
    # 这些 journal 只能由旧链节点写出，生产入口已拒识；新链恢复是候选 runner
    # 自身续跑（F7c），不再经 operator 发布补尾 blocker。
    # inbox、queue 与审批 currentness 必须共享这一个 invocation 坐标。
    # 之前这里只复用了 overview，却让每个 gate 单独重跑整仓 code_version；
    # 一本后期书会在一次 next 内 fork 出二十多个代码时点与 git 子进程。
    # F8c：审批投影随人工闸整层退役——新链无 gate、无 closure、无 spawns，
    # operator 不再构造 ApprovalDoc（2026-10-04 裁决：旧审批账解码链整体删除）。
    inbox = inbox_from_snapshot(
        snapshot, _final_cas_owner=_OPERATOR_FINAL_CAS)
    lane = _lane_from_snapshot(snapshot)

    decision = OperatorNextDecision(
        base=os.fspath(snapshot.base), sid=sid,
        code_version=snapshot.code, surface_seal=snapshot.surface_seal,
        state=lane.state, overview=snapshot.overview, inbox=inbox,
        action=lane.action,
    )
    execution_token = None
    if decision.state == 'ready':
        # 延迟导入避免 permit 层反向复用 canonical_sha256 时形成 import 环。
        # 该 helper 只读取 exact external inputs；下面仍是本次规划唯一的
        # active-surface CAS，绝不再采第二份七层 snapshot。
        from novel.orchestrator.execution_permit import (
            ready_execution_token_from_next,
        )
        execution_token = ready_execution_token_from_next(decision)

    _require_snapshot_current(snapshot)

    return OperatorNext(
        **decision.model_dump(mode='python'),
        execution_token=execution_token,
    )


def _frontier_view(lane: _LanePlan) -> RunResumeFrontierView:
    # F8c：人工闸与语义审阅面整层退役——`review_states`（gate/prepare 的
    # 审批原状态投影）不再有数据源，诚实在形状上保持 null，不造空列表冒充
    # 「审过且全 not_accepted」。字段本身留给兼容旧前端读数。
    action = lane.action
    if lane.state == 'pipeline_complete':
        return RunResumeFrontierView(
            state='pipeline_complete',
            why='当前快照下现役节点已全部越过；最终交付仍以终验包为准',
            next_handler='none', review_states=None,
            verified_skipped_nodes=list(lane.verified_skipped_nodes),
            verified_skipped_count=len(lane.verified_skipped_nodes),
        )
    if action is None:  # pragma: no cover —— _LanePlan 构造边界已闭合
        raise RuntimeError(f'{lane.state} lane 缺 action')
    if lane.state == 'ready':
        handler = 'executor' if action.paid else 'code'
    elif action.waiting_on in queue.DETERMINISTIC_RECOVERY_WAITING_ON:
        handler = 'executor'
    else:
        # 人工闸已整层退役（2026-09-15 全自动裁决 → STR-06 新链）：除登记过的
        # deterministic recovery 外没有等待面；走到这里说明 planner 给了
        # 未登记的 waiting_on，按 fail-closed 报 unknown 而不是假装有人在处理。
        handler = 'unknown'
    return RunResumeFrontierView(
        state=lane.state,
        node=action.node,
        seq=list(NODES).index(action.node) + 1,
        label=NODES[action.node].label,
        resume_verdict=action.resume_verdict,
        why=action.why,
        waiting_on=action.waiting_on,
        paid=action.paid,
        writes=action.writes,
        next_handler=handler, review_states=None,
        verified_skipped_nodes=list(lane.verified_skipped_nodes),
        verified_skipped_count=len(lane.verified_skipped_nodes),
    )


def next_action(base: str | os.PathLike[str], sid: str) -> OperatorNext:
    """返回当前盘面的唯一下一动作；一次调用只采一份完整快照。"""
    novel_dir = paths.resolve_novel_dir(base, sid).resolve()
    with readonly_snapshot_memo(novel_dir):
        return _next_from_snapshot(capture_operator_snapshot(base, sid))


def progress_snapshot(
    base: str | os.PathLike[str], sid: str,
) -> tuple[NovelOverview, RunResumeFrontierView]:
    """给运行页复用一份 overview + operator lane，不生成沉重 Inbox。"""
    with progress_read_snapshot(base, sid) as result:
        return result


@contextmanager
def progress_read_snapshot(
    base: str | os.PathLike[str], sid: str,
) -> Iterator[tuple[NovelOverview, RunResumeFrontierView]]:
    """进度及工作单元共用本次快照；离开读域前复验盘面，绝不跨请求缓存。"""
    novel_dir = paths.resolve_novel_dir(base, sid).resolve()
    with readonly_snapshot_memo(novel_dir):
        snapshot = capture_operator_snapshot(base, sid)
        # ⚠ F8b-3（S-05）：旧链 prepared 提交尾窗 lane 已随 operator 拆除，
        # 进度前沿与 next_action 消费同一条 lane 推导。
        lane = _lane_from_snapshot(snapshot)
        yield snapshot.overview, _frontier_view(lane)
        _require_snapshot_current(snapshot)


def next_action_at_code(
    base: str | os.PathLike[str], sid: str, *, code: str,
) -> OperatorNext:
    """复用 Supervisor 已双向 CAS 的 execution-epoch code 坐标。

    这不是公开的“自报代码版本”入口：调用者必须在本函数前后各验证一次
    epoch identity。普通 CLI/API 始终走 :func:`next_action` 现场取指纹。
    """
    if not code:
        raise ValueError('next_action_at_code 缺 execution epoch code')
    novel_dir = paths.resolve_novel_dir(base, sid).resolve()
    with readonly_snapshot_memo(novel_dir, code=code):
        return _next_from_snapshot(
            capture_operator_snapshot(base, sid, code=code))


def permit(
    base: str | os.PathLike[str], sid: str, node: str,
    flags: tuple[str, ...] | list[str] = (),
) -> ExecutionPermit:
    """显式铸 exact 许可；保留给 blocked recovery 与诊断兼容。"""
    novel_dir = paths.resolve_novel_dir(base, sid).resolve()
    with readonly_snapshot_memo(novel_dir):
        snapshot = capture_operator_snapshot(base, sid)
        current = _next_from_snapshot(snapshot)
    # 延迟导入：permit 策略消费 OperatorNext，但 next 本身不依赖许可层。
    from novel.orchestrator.execution_permit import permit_from_next
    return permit_from_next(current, node, flags)


__all__ = [
    'canonical_sha256', 'next_action', 'next_action_at_code', 'permit',
    'progress_snapshot',
]
