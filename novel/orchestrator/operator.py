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

from contracts.artifacts import STAGE2_GLOBAL_EDIT
from contracts.invariants import (
    INBOX_KIND, NODES, STAGES, STAGE2_LEGACY_REVALIDATE_ACTOR,
    STAGE2_LEGACY_REVALIDATE_REASON,
)
from contracts.schema.api import (
    ApprovalDoc, ExecutionPermit, InboxItem, InboxView, OperatorAction, OperatorNext,
    OperatorNextDecision,
)
from contracts.schema.orchestration import (
    NovelOverview, QueueItem, RunResumeFrontierView,
)
from novel import paths
from novel.errors import ArtifactUnreadable
from novel.orchestrator import approvals, queue, state
from novel.orchestrator.inspection import (
    OperatorReadSnapshot,
    capture_operator_snapshot,
    read_surface_seal,
)
from novel.orchestrator.nodes import is_paid, write_effect
from novel.store import readonly_snapshot_memo
from novel.views.inbox import _OPERATOR_FINAL_CAS, inbox_from_snapshot


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


def _stage2_commit_recovery_surface(
    snapshot: OperatorReadSnapshot,
) -> tuple[_LanePlan, InboxView] | None:
    """Stage2 action/terminal 必须在 approvals/reviewer workpack 读取前分流。"""
    recovery_flags = state.stage2_reviewed_edit_commit_recovery_flags(
        snapshot.base, snapshot.sid, code=snapshot.code)
    action_kind = (
        'revalidate' if recovery_flags and recovery_flags[0] == '--revalidate'
        else 'apply_edit' if recovery_flags else None
    )
    if action_kind is None and state.stage2_reviewed_edit_revalidation_needed(
            snapshot.base, snapshot.sid, code=snapshot.code):
        action_kind = 'revalidate'
        recovery_flags = (
            '--revalidate',
            '--actor', STAGE2_LEGACY_REVALIDATE_ACTOR,
            '--reason', STAGE2_LEGACY_REVALIDATE_REASON,
        )
    ordinary_terminal = (
        action_kind is None
        and state.stage2_global_edit_terminal_failure_needed(
            snapshot.base, snapshot.sid, code=snapshot.code)
    )
    if action_kind is None and not ordinary_terminal:
        return None
    if action_kind == 'apply_edit':
        assert recovery_flags is not None
        flags = list(recovery_flags)
        waiting_on = queue.WAITING_ON_STAGE2_EDIT_COMMIT
        why = (
            'Stage2 reviewed-edit prepared commit 尚未逐件收敛；只允许 Executor '
            '重放 canonical edit，0 wire、0 Commander')
        rule = 'global_edit_prepared_commit'
        what = (
            'reviewed GlobalEdit 的正式 Global/review/projection 提交被硬中断，'
            '盘上 prepared journal 可证明每份仍为 baseline 或 target')
        how = (
            'Supervisor 将以 stage2.global --apply-edit '
            'stage2/global_edit.json 交给 Executor 确定性补尾；不会调用模型')
    elif action_kind == 'revalidate':
        assert recovery_flags is not None
        flags = list(recovery_flags)
        waiting_on = queue.WAITING_ON_STAGE2_EDIT_REVALIDATE
        why = (
            'Stage2 Global revalidate 已留下可恢复的 exact 审计身份，或 reviewed '
            'INVALID 已按真实尺子无 HARD；只允许 Executor 0-wire 提升/补尾')
        rule = 'global_edit_revalidation_commit'
        what = (
            'reviewed GlobalEdit 的 live INVALID 可确定性提升，或提升 bundle '
            '已有 prepared journal 尚未收敛')
        how = 'Supervisor 将逐字复用本次 actor/reason 重放 stage2.global --revalidate'
    else:
        flags = []
        waiting_on = queue.WAITING_ON_STAGE2_EDIT_TERMINAL
        why = (
            'Stage2 GlobalEdit 已留下需人工后继的 exact terminal failure；'
            '等待新的 typed edit 或人工修改 INVALID 后 revalidate')
        rule = 'global_edit_terminal_failure'
        what = 'GlobalEdit 已确定失败或 INVALID 已被人修改；重放旧 edit 不会产生新结论'
        how = '提交新的 typed edit，或人工修改 live INVALID 后显式 revalidate'
    item = QueueItem(
        sid=snapshot.sid,
        node='stage2.global',
        flags=flags,
        steps=[],
        why=why,
        waiting_on=waiting_on,
    )
    lane = _LanePlan(
        state='blocked',
        action=_action(
            base=os.fspath(snapshot.base), sid=snapshot.sid,
            code=snapshot.code, surface_seal=snapshot.surface_seal,
            overview=snapshot.overview, item=item),
        verified_skipped_nodes=(),
    )
    inbox_item = InboxItem(
        kind='repairable', stage='stage2', chapter=None,
        rule=rule,
        what=what,
        how=how,
    )
    counts = {kind: 0 for kind in INBOX_KIND}
    counts['repairable'] = 1
    inbox = InboxView(
        items=[inbox_item], counts=counts, total=1, wontfix=0)
    return lane, inbox


def _stage4_design_repair_commit_recovery_surface(
    snapshot: OperatorReadSnapshot,
) -> tuple[_LanePlan, InboxView] | None:
    """repair/replan prepared 尾窗优先于 stale token 与新 Stage4 审阅。"""
    from novel.llm import stage4_runs

    ctx = state.readonly_ctx(
        snapshot.novel_dir, 'stage4.design', snapshot.code)
    recovery = stage4_runs.inspect_prepared_design_repair_aggregate_recovery(ctx)
    if recovery is None:
        return None
    flags = [
        '--recover-repair-aggregate', recovery.source_run_id,
        '--source-journal-sha256', recovery.journal_sha256,
        '--source-commit-sha256', recovery.commit_sha256,
    ]
    item = QueueItem(
        sid=snapshot.sid,
        node='stage4.design',
        flags=flags,
        steps=[],
        why=(
            'Stage4 repair/replan 已封存候选，但 source producer 在 aggregate '
            'committed 前结束；只允许新 run 0-wire 幂等补尾'),
        waiting_on=queue.WAITING_ON_STAGE4_DESIGN_REPAIR_COMMIT,
    )
    lane = _LanePlan(
        state='blocked',
        action=_action(
            base=os.fspath(snapshot.base), sid=snapshot.sid,
            code=snapshot.code, surface_seal=snapshot.surface_seal,
            overview=snapshot.overview, item=item),
        verified_skipped_nodes=(),
    )
    inbox_item = InboxItem(
        kind='repairable', stage='stage4', chapter=None,
        rule='design_repair_aggregate_prepared_commit',
        what=(
            f'source run {recovery.source_run_id} 的 repair/replan aggregate '
            f'停在 prepared，正式候选当前为 {recovery.target_state}'),
        how=(
            'Supervisor 将用 journal 中 sealed candidate 补齐验证、adoption 与 '
            'committed；不启动 reviewer 或模型会话'),
    )
    counts = {kind: 0 for kind in INBOX_KIND}
    counts['repairable'] = 1
    return lane, InboxView(
        items=[inbox_item], counts=counts, total=1, wontfix=0)


def _prepared_delivery_recovery_surface(
    snapshot: OperatorReadSnapshot,
) -> tuple[_LanePlan, InboxView] | None:
    """原组提交尾窗先于审批投影；正式文件存在不能掩盖未闭采纳。"""
    item = queue.prepared_delivery_recovery_item(snapshot.base, snapshot.sid, code=snapshot.code)
    if item is None:
        return None
    lane = _LanePlan(
        state='blocked',
        action=_action(
            base=os.fspath(snapshot.base), sid=snapshot.sid, code=snapshot.code,
            surface_seal=snapshot.surface_seal, overview=snapshot.overview, item=item),
        verified_skipped_nodes=())
    kind = ('repairable' if item.waiting_on == queue.WAITING_ON_PREPARED_DELIVERY
            else 'human_only')
    counts = {key: 0 for key in INBOX_KIND}
    counts[kind] = 1
    return lane, InboxView(items=[InboxItem(
        kind=kind, stage=NODES[item.node].stage, chapter=None, rule='prepared_delivery',
        what=item.why,
        how=('执行原 source run 与 proof SHA 锁定的 --recover-prepared；不构造模型客户端'
             if kind == 'repairable' else '保留原完成组、成本与采纳记录，核对未知证据；不能重新生成'))],
        counts=counts, total=1, wontfix=0)


def _prepared_commit_recovery_surface(
    snapshot: OperatorReadSnapshot,
) -> tuple[_LanePlan, InboxView] | None:
    """同时检查已登记的 prepared ownership，不按节点顺序猜一份。"""
    stage2 = _stage2_commit_recovery_surface(snapshot)
    stage4 = _stage4_design_repair_commit_recovery_surface(snapshot)
    delivery = _prepared_delivery_recovery_surface(snapshot)
    pending = [entry for entry in (stage2, stage4, delivery) if entry is not None]
    if len(pending) > 1:
        raise ArtifactUnreadable(
            'operator 同时发现多个阶段的 prepared 提交尾窗；'
            '两者都可能改写 sealed/current input，不能按节点'
            '顺序选一份执行')
    return pending[0] if pending else None


def _lane_from_snapshot(
    snapshot: OperatorReadSnapshot, approval_doc: ApprovalDoc,
) -> _LanePlan:
    """从同一 overview/approval 坐标选最早动作，并保留被 planner 跳过的前缀。"""
    sid = snapshot.sid
    todo, blocked = queue.plan_book_from_overview(
        snapshot.base, sid, snapshot.overview,
        until_stage=STAGES[-1], follow_check=True,
        approval_doc=approval_doc,
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
    recovery = _prepared_commit_recovery_surface(snapshot)
    if recovery is not None:
        # partial bundle 上的 approvals/inbox/reviewer workpack 本来就可能因
        # source mismatch 关闭；prepared journal 是更早、且唯一可执行的事实。
        lane, inbox = recovery
    else:
        # inbox、queue 与审批 currentness 必须共享这一个 invocation 坐标。
        # 之前这里只复用了 overview，却让每个 gate 单独重跑整仓 code_version；
        # 一本后期书会在一次 next 内 fork 出二十多个代码时点与 git 子进程。
        with approvals.read_projection(
                overview=snapshot.overview, code=snapshot.code):
            approval_doc = approvals.get(
                snapshot.base, sid,
                overview=snapshot.overview, code=snapshot.code,
                include_spawns=False, operator_projection=True)
            inbox = inbox_from_snapshot(
                snapshot, approval_doc=approval_doc,
                _final_cas_owner=_OPERATOR_FINAL_CAS)
            lane = _lane_from_snapshot(snapshot, approval_doc)

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


def _review_state_projection(approval_doc: ApprovalDoc | None) -> list[dict[str, str]] | None:
    """同一 operator 快照的有效审批原状态；节点路径只从 NODES 登记读取。"""
    if approval_doc is None:
        return None
    source = approval_doc.model_dump(mode='python')
    rows = []
    for node, spec in NODES.items():
        life = spec.lifecycle
        if life is None or not life.approval_projection:
            continue
        if node == 'stage2.blueprint':
            review = approval_doc.stage2_reviews.get(node)
            progress = None if review is None else review.plot_progress
            if progress is not None:
                rows.extend({'node': node, 'scope': row.scope, 'state': row.state}
                            for row in progress.scopes)
            else:
                rows.append({'node': node, 'scope': 'cross_chapter', 'state': 'unknown'})
            continue
        value = source
        for key in life.approval_projection:
            value = value.get(key) if isinstance(value, dict) else None
        status = ('accepted' if value else 'not_accepted') if isinstance(value, bool) else value
        rows.append({'node': node, 'scope': life.commander_bindings[0][1],
                     'state': status if isinstance(status, str) else 'unknown'})
    return rows


def _frontier_view(lane: _LanePlan, approval_doc: ApprovalDoc | None = None) -> RunResumeFrontierView:
    action = lane.action
    review_states = _review_state_projection(approval_doc)
    if lane.state == 'pipeline_complete':
        return RunResumeFrontierView(
            state='pipeline_complete',
            why='当前快照下现役节点已全部越过；最终交付仍以终验包为准',
            next_handler='none', review_states=review_states,
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
        # 延迟导入：reviewer_tasks 本身依赖 queue/schema，不让 operator 顶层形成环。
        from novel.orchestrator import reviewer_tasks
        handler = (
            'commander'
            if action.waiting_on is not None
            and reviewer_tasks.is_registered(action.waiting_on)
            else 'human'
        )
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
        next_handler=handler, review_states=review_states,
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
        with approvals.read_projection(overview=snapshot.overview, code=snapshot.code):
            approval_doc = None
            recovery = _prepared_commit_recovery_surface(snapshot)
            if recovery is not None:
                lane, _inbox = recovery
            else:
                approval_doc = approvals.get(
                    snapshot.base, sid,
                    overview=snapshot.overview, code=snapshot.code,
                    include_spawns=False, operator_projection=True)
                lane = _lane_from_snapshot(snapshot, approval_doc)
            yield snapshot.overview, _frontier_view(lane, approval_doc)
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
