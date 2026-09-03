#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
queue.py —— 批量队列（`R-13`）。**一次排入多本书，人只在需要裁决的地方介入。**

【`plan()` 是纯函数】给它一批 sid 与一个目标 stage，它算出「这条链长什么样」——
**不跑、不写、不发请求**。于是「排这一批会花多少次调用、会写哪些文件、
命令逐条长什么样」可以 0 成本地摊开给人看，而那正是 `R-13.3` 要人确认的东西。

【`create()` + runner 才是真队列】确认后的整份命令快照追加进
`_service/queues.jsonl`；每本书一条 lane，不同书并行。同书首项失败后其余项记
`not_run`，人工闸门持久写 `waiting_on`。服务重启时，旧 running 先成为 unknown，
再对照 jobs 事件与产物存在性/新鲜度恢复；runner 从不临时扩张已确认的命令面。

【⚠ 队列本身不产生新的判定】`R-13.6` → `R-11.4`：**造第二个判官就是造第二个落点。**
这条**由结构保证**：本模块不许 import `novel.report`，由 `tests/test_layering.py`
的一条 AST 测试钉着。队列读的是完成度（`state.py`），而完成度读的是盘上有什么。

【为什么「停在那里」不是保守】批量的风险与单本不同：
**单本跑错人当场看得见；批量跑错是一批产物同时坏掉且没人在看。**
所以 `R-13.4` 的「任一项失败 → 停在那里」是这条能力**成立的前提**，不是一个保守选项。
"""
from __future__ import annotations

from contracts.artifacts import STAGE0_REVISIONS, STAGE6_HOOK, STAGE7_NAME_MAP

import json
import os
import threading
import time
import traceback
import uuid
from collections.abc import Iterable, Sequence
from pathlib import Path

from contracts.invariants import (
    LEGACY_STAGE5_MODES, NODES, RETIRED_NODES, RETIRED_STAGES, STAGES,
    stage2_review_required,
)
from contracts.schema.api import ApprovalDoc
from contracts.schema.orchestration import (
    NovelOverview, QueueCounts, QueueEntry, QueueEvent, QueueItem,
    QueueLaneCounts, QueuePlan, QueueRun, QueueStart,
)
from contracts.schema.stage0 import Stage0Revisions
from contracts.schema.stage6 import Hook
from contracts.schema.stage7 import NameMap
from novel import paths, prov
from novel.appendlog import append_line
from novel.cli import exit_codes as X
from novel.cli.exit_codes import CRASH
from novel.errors import (
    ApprovalRequired, ArtifactUnreadable, BlockingFindingsUnresolved,
    GateBlocked, QueueConflict, TerminalState, UpstreamMissing, UpstreamStale,
    UpstreamUnproven,
)
from novel.orchestrator import approvals, jobs, state as st
from novel.orchestrator.nodes import build_steps, flag_value, shell_line
from novel.stages.stage4.phase_state import current_infeasible_binding
from novel.store import ArtifactStore

#: 人做的那几个动作。**队列永不自动排它们**——`stage6.approve` 换一条候选、
#: `--apply-edit` 改一个字，都是「人看过之后的裁决」，机器替不了。
#: ⚠ 它们进 `blocked` 而不是被跳过：被跳过的闸门在界面上与「不需要它」长得一样。
HUMAN_NODES: frozenset[str] = frozenset(
    {n for n in NODES if n.endswith('.approve')} | {'stage0.edit'})

#: ``stage0.edit`` is a human capability, not a mandatory gate.  Listing it as
#: an automatic step would make every book wait forever for an edit the user
#: may neither need nor want.
OPTIONAL_HUMAN_NODES: frozenset[str] = frozenset({'stage0.edit'})

#: 待人工清单非空时挡队列的那个理由（`INBOX_KIND` 的 `invalid`）。
WAITING_ON_INBOX = 'inbox:invalid'
WAITING_ON_STAGE2_REVIEW = 'approval:stage2-global-review'
WAITING_ON_STAGE2_EDIT_CORRECTION = 'recovery:stage2-global-reviewed-edit'
STAGE2_REVIEWER_WAITING_ON = frozenset({
    WAITING_ON_STAGE2_REVIEW,
    WAITING_ON_STAGE2_EDIT_CORRECTION,
})
WAITING_ON_STAGE4_DESIGN_REVIEW = 'approval:stage4-design-review'
WAITING_ON_FROZEN_V6_DESIGN_AUTHORITY = \
    'legacy_frozen_v6_design_authority_unavailable'
WAITING_ON_STAGE4_BINDING_RESOLUTION = 'approval:stage4-binding-resolution'
WAITING_ON_STAGE4_STRUCTURE_REPLAN = 'approval:stage4-structure-replan'
WAITING_ON_STAGE4_DESIGN_ACTION = 'recovery:stage4-design-action'
WAITING_ON_STAGE4_STRUCTURE_REVIEW = 'approval:stage4-structure-review'
WAITING_ON_FROZEN_V6_CONTEXT = 'legacy_frozen_v6_context_unavailable'
# 兼容只 import 常量名的旧调用者；值已收敛到两张真实决定面。
WAITING_ON_STAGE4_PHASE_APPROVAL = WAITING_ON_STAGE4_DESIGN_REVIEW
WAITING_ON_STAGE4_REPLAN = WAITING_ON_STAGE4_BINDING_RESOLUTION
WAITING_ON_WRITING_APPROVAL = 'approval:stage5-writing'
WAITING_ON_REVIEW = 'stage5:review-currentness'
WAITING_ON_STAGE5_CLOSURE = 'approval:stage5-closure'
WAITING_ON_LEGACY = 'legacy_incompatible'
WAITING_ON_RESUME_DECISION = 'resume:manual-decision'
WAITING_ON_RESTART_CONFIRMATION = 'queue:restart-confirmation'
WAITING_ON_REPLAN = 'queue:plan-changed'

#: 已有 typed 人审决定、只差按它执行的 blocker。它不是新的 Commander 任务：
#: Supervisor 必须现场铸 recovery permit，再把 exact node/flags 交给 Executor。
#: 独立成闭集，避免任何普通人工 blocker 因“碰巧带 steps”被自动执行。
DETERMINISTIC_RECOVERY_WAITING_ON: frozenset[str] = frozenset({
    WAITING_ON_STAGE4_DESIGN_ACTION,
})


def chapters_from_flags(flags: Sequence[str]) -> list[int] | None:
    """取 Stage5 定向章列表；不带则是全书。

    队列只会读自己冻结过的命令，但恢复时仍要关闭式校验：旧
    `--deep/--shallow-rest` 快照必须走 replan，不得在这里被解释成新语义。
    """
    raw = flag_value(flags, '--chapters')
    if raw is None:
        return None
    try:
        values = [int(value) for value in raw.split(',')]
    except ValueError as exc:
        raise ApprovalRequired('--chapters 必须是逗号分隔的正整数章号') from exc
    if values != sorted(set(values)) or any(value < 1 for value in values):
        raise ApprovalRequired('--chapters 必须升序、去重且全为正整数')
    return values


def _stage_index(stage: str) -> int:
    return STAGES.index(stage)


def nodes_up_to(until_stage: str) -> list[str]:
    """处理链上到 `until_stage` 为止的节点。**顺序即 `NODES` 的声明顺序。**

    ⚠ 顺序不在这里另定：`NODES` 的注释写着「顺序即处理链顺序，队列与断点续跑都按它排」。
    在这儿再排一次序就是第二个落点，而两处排序分家的样子是**队列偶尔倒着跑一层**。
    """
    if until_stage not in STAGES:
        raise ValueError(f'未知 stage {until_stage!r}，取值面是 {list(STAGES)}')
    cap = _stage_index(until_stage)
    return [n for n, spec in NODES.items() if _stage_index(spec.stage) <= cap]


def _upstream_invalid(ov, stage: str) -> list[str]:
    """目标节点真实依赖的严格上游里，有几份被闸门拦下的稿。

    ⚠ 只看上游不看本层：本层自己的 `.INVALID` 是**本节点该处理的事**
    （0 成本提升路 `--revalidate`），不是挡住它自己的理由。

    当前节点都沿主链消费前序层；Stage3 已退役，编号空洞不改变依赖顺序。
    """
    upstream_stages = {
        name for name in STAGES if _stage_index(name) < _stage_index(stage)
    }
    return [a.rel for s in ov.stages if s.stage in upstream_stages
            for a in s.artifacts if a.invalid]


def _gate_is_reachable(ov, stage: str) -> bool:
    """这道人工闸门**此刻够得着吗**——本层盘上有没有东西待人裁决。

    ⚠⚠ **这一格是实跑盘上语料改出来的，不是设计出来的。**
    第一版让人工闸门无条件挡住其后所有项，于是 `stage0.approve`（处理链第二个节点）
    把**整条链从头挡死**：盘上 stage0 一份产物都没有，那道闸根本还够不着，
    而队列却说「在等人批准 stage0」——**一句看起来精确的假话**，
    且它让队列在任何一本新书上都排不出一项。
    ⇒ 一道**还没有对象**的闸门不是「在等人」，它只是还没轮到。
    """
    s = next((x for x in ov.stages if x.stage == stage), None)
    return bool(s and any(a.exists for a in s.artifacts))


def _human_gate_satisfied(base: str | os.PathLike[str], sid: str, node: str) -> bool:
    """Read the gate's existing typed artifact; do not invent a queue verdict.

    These three fields are the same fields consumed by their downstream
    stages.  Reading them here keeps the queue from permanently blocking an
    already approved book while still leaving the actual decision to a human.
    """
    model_rel = {
        'stage0.approve': (Stage0Revisions, STAGE0_REVISIONS),
        'stage6.approve': (Hook, STAGE6_HOOK),
        'stage7.approve': (NameMap, STAGE7_NAME_MAP),
    }.get(node)
    if model_rel is None:
        return False
    model, rel = model_rel
    try:
        novel_dir = paths.resolve_novel_dir(base, sid)
        store = ArtifactStore(st.readonly_ctx(
            novel_dir, node, approvals.projection_code()))
        artifact, _ = store.read_own(model, rel)
        if node == 'stage6.approve':
            from novel.stages.stage6 import hook
            return hook.approval_is_current(store, artifact)
        if node == 'stage7.approve':
            from novel.stages.stage7 import rename
            return rename.approval_is_current(store, artifact)
    except TerminalState:
        return False
    if node == 'stage0.approve':
        # 唯一落点在 eligibility，队列不自己复述批准条件；不具执行资格
        # 在这里等于「闸门未过」——队列会继续说出在等 stage0.approve。
        from novel.stages.stage0.eligibility import is_stage0_executable
        from novel.stages.stage0 import revisions as stage0_revisions
        stage0_revisions.verify_chain(artifact)
        return is_stage0_executable(stage0_revisions.snapshot_of(artifact))
    raise AssertionError(f'未登记的人工闸门 current 判定：{node}')


def _stage4_replan_required(base: str | os.PathLike[str], sid: str) -> bool:
    """B 已对当前 A 落下 fresh 负结论时，队列只能停给显式 A 重规划。"""
    novel_dir = paths.resolve_novel_dir(base, sid)
    store = ArtifactStore(st.readonly_ctx(
        novel_dir, 'stage4.plan', approvals.projection_code()))
    return current_infeasible_binding(store) is not None


def plan_book(base: str | os.PathLike[str], sid: str, *, until_stage: str,
              follow_check: bool = True) -> tuple[list[QueueItem], list[QueueItem]]:
    """一本书的 `(要跑的, 被挡住的)`。**纯函数**（只读盘，不写、不发请求）。

    完成度只在这个薄入口读取一次；已经持有 invocation-scoped operator 快照的
    调用方必须改走 :func:`plan_book_from_overview`，不能为同一个下一步再读一份
    overview。两条入口以下共享完全相同的节点、闸门与恢复判定。
    """
    return plan_book_from_overview(
        base, sid, st.overview(base, sid), until_stage=until_stage,
        follow_check=follow_check)


def plan_book_from_overview(
    base: str | os.PathLike[str],
    sid: str,
    ov: NovelOverview,
    *,
    until_stage: str,
    follow_check: bool = True,
    approval_doc: ApprovalDoc | None = None,
    allow_deterministic_redo: bool = False,
) -> tuple[list[QueueItem], list[QueueItem]]:
    """按一份已冻结完成度规划 lane；判定正文与 :func:`plan_book` 唯一共享。

    ``allow_deterministic_redo`` 只供每步后都会重新采快照的 AgentRun operator。
    普通多步队列保持默认 ``False``，不能把一份冻结计划扩张成后续重采授权。
    """
    if approval_doc is not None and approval_doc.sid != sid:
        raise ValueError(
            f'审批投影属于 {approval_doc.sid!r}，不是当前书 {sid!r}')
    todo: list[QueueItem] = []
    blocked: list[QueueItem] = []
    #: ⚠ **一旦挡住，其后的项一律跟着挡住**——那才叫「挡住队列」（`R-13.5`）。
    #: 只挡住那一项而让后面的照跑，等于让下游拿着一份没人签过字的上游继续走。
    stuck: str | None = None
    # Review 的当前 freshness 只描述**建队列这一刻**的主体。
    # 同一快照若已排入可执行 writer，它成功后 canonical 章集/正文必然改变；
    # 此时不能拿旧主体上的 fresh 回答“写完后还要不要
    # 验收”。这格只记本次 plan 已经真正排入的 runnable writer；
    # blocked writer 仍由 ``stuck`` 传递，不伪装成会在本次改变主体。
    stage5_write_will_run = False
    naming_projection_blocked = st.stage2_global_projection_blocked(ov)
    reviewed_edit_correction = st.stage2_reviewed_edit_correction_needed(
        base, sid, ov)

    for node in nodes_up_to(until_stage):
        spec = NODES[node]
        stage4_design_action_flags: tuple[str, ...] | None = None
        projection_recovery = (
            node == 'stage2.global'
            and st.stage2_projection_recovery_needed(ov)
        )
        projection_blocked = (
            node == 'stage2.global'
            and naming_projection_blocked
        )
        blueprint_invalid_recovery = (
            node == 'stage2.blueprint'
            and st.stage2_blueprint_invalid_recovery_needed(ov)
        )
        legacy_blueprint_recovery = (
            node == 'stage2.blueprint'
            and st.stage2_legacy_blueprint_recovery_needed(ov)
        )
        why_blocked = stuck
        if why_blocked is None and node in HUMAN_NODES:
            if node in OPTIONAL_HUMAN_NODES:
                continue                             # 可选人工编辑不是流水线必经闸门
            if not _gate_is_reachable(ov, spec.stage):
                continue                             # 还没轮到它，不是「在等人」
            if _human_gate_satisfied(base, sid, node):
                continue                             # 人已经完成这道闸
            why_blocked = node                       # 在等这一步的那个人
        if (why_blocked is None and node == 'stage2.global'
                and reviewed_edit_correction):
            # reviewed GlobalEdit 已经执行到 HARD 闸、但 Commander 的最后一次纠正
            # 尚未落地。必须先恢复这一个逻辑动作；若让最新 INVALID 投影把 planner
            # 推到 Blueprint REDO，既丢原 reviewer 上下文，也会把一次纠正扩成新任务。
            why_blocked = WAITING_ON_STAGE2_EDIT_CORRECTION
        if (why_blocked is None
                and (projection_blocked
                     or (blueprint_invalid_recovery
                         and not legacy_blueprint_recovery))):
            # 两种状态都已有可恢复的问题稿，且下一步需要人提供 typed 决策。
            # 队列不能替人造 actor/reason，更不能把 RUN 解释成默认再付一次钱。
            why_blocked = WAITING_ON_INBOX
        # 人审移到 Stage4 的最低消费边界。队列的完整节点序列必经 A；在这里挡住后，
        # ``stuck`` 会把 B、context 与后续全部保持 blocked。Stage2 blueprint 因而能
        # 先跑，而恢复 global/blueprint 的 0 成本入口也不会被这道门反向堵死。
        # ⚠ 必须先于通用 upstream-invalid 分类：Stage2 的 `.INVALID` 本来就是完整
        # closure 的一种 typed 终态；若先压成 `inbox:invalid`，规定的
        # `approval:stage2-global-review` 阻塞项与恢复文案就永远到不了。
        if (why_blocked is None and node == 'stage4.design'
                and stage2_review_required(node, paid=spec.paid)
                and not st.stage2_frozen_v6_subject_validated(ov)):
            try:
                approvals.require_stage2_review(base, sid, node)
            except (
                ApprovalRequired, UpstreamMissing, ArtifactUnreadable,
                GateBlocked, UpstreamStale, UpstreamUnproven,
                BlockingFindingsUnresolved,
            ):
                why_blocked = WAITING_ON_STAGE2_REVIEW
            else:
                # Global typed review current 仍不等于完整 Stage2 审批链 current。
                # Blueprint/Global 在 review 后重建会让 raw seen/gate proof 暂停；
                # operator 已经冻结了同一时点的完整审批投影，就必须先把这项
                # 恢复路由交回 Stage2 reviewer。否则队列会前移到 Stage4A，
                # 而 design workpack/template 又按生产闸正确拒绝缺失 gate.s2，
                # 形成一个无法由 Commander 自己越过的确定性死锁。
                if approval_doc is not None and not approval_doc.gate['s2']:
                    why_blocked = WAITING_ON_STAGE2_REVIEW

        if why_blocked is None:
            ghosts = _upstream_invalid(ov, spec.stage)
            if ghosts:
                why_blocked = WAITING_ON_INBOX

        verdict, why = st.resume_verdict(ov, node)
        deterministic_redo_flags: tuple[str, ...] | None = None
        frozen_v6 = st.stage2_frozen_v6_subject_validated(ov)
        if (why_blocked is None and node == 'stage4.design'
                and verdict == st.RESUME_SKIP):
            # A 候选仍 current 不代表 A 相已经结束：若最新 typed 决定要求 repair /
            # replan，真正下一步是按 opaque action token 精确恢复 A。这个状态此前会
            # 被通用 SKIP 吞掉，随后 planner 前移到 B/结构人审，续跑便反复唤醒
            # Commander。operator 路复用冻结审批投影；普通 queue 入口只读补取一份。
            design_doc = approval_doc
            if design_doc is None:
                try:
                    design_doc = approvals.get(
                        base, sid, include_spawns=False,
                        operator_projection=True)
                except (
                    ApprovalRequired, UpstreamMissing, ArtifactUnreadable,
                    GateBlocked, UpstreamStale, UpstreamUnproven,
                    BlockingFindingsUnresolved,
                ):
                    # 普通多步 queue 入口的残缺/混合盘面仍沿既有 plan 阻塞面；
                    # operator 已传冻结投影，不会走到这一兼容分支。
                    design_doc = None
            design_view = (
                None if design_doc is None else design_doc.design_review)
            if design_view is not None and design_view.state in (
                    'repair_requested', 'strategy_replan_requested'):
                token = design_view.action_review_token
                if token is None:
                    raise ArtifactUnreadable(
                        'Stage4A typed 修订态缺 action review token')
                stage4_design_action_flags = (
                    ('--replan', '--review-token', token)
                    if design_view.state == 'strategy_replan_requested'
                    else ('--review-token', token)
                )
                why_blocked = WAITING_ON_STAGE4_DESIGN_ACTION
        if (
            why_blocked is None
            and verdict == st.RESUME_RUN
            and st.live_invalid_artifacts(ov, node)
            and not projection_recovery
            and not legacy_blueprint_recovery
        ):
            # ``resume_verdict`` 必须如实描述“默认重跑会发生什么”，但 queue 不能
            # 把这句警告继续冻结成一条付费 argv。活动 INVALID 先停在已登记的
            # 0 LLM recovery 面；若该节点没有安全恢复路，也保持 blocked 交给人，
            # 绝不把“没实现恢复”解释成“同意再付一次钱”。
            why_blocked = WAITING_ON_INBOX
        if why_blocked is None and node == 'stage4.plan':
            if _stage4_replan_required(base, sid):
                # B 的负结论已经是正式终态；这份队列不得继续暴露同一个 B argv，
                # 更不能把旧的一次性授权解释成重采 A。恢复须另行 preview/confirm。
                why_blocked = WAITING_ON_STAGE4_BINDING_RESOLUTION
            else:
                design_verdict, _design_why = st.resume_verdict(ov, 'stage4.design')
            if (why_blocked is None
                    and design_verdict != st.RESUME_SKIP):
                # A 还没有一份当前候选时，本次队列只能确认并执行 A。
                # B 必须等 A 落盘、人看过候选后重新 plan/preview/--yes；
                # 否则“队列一次确认”会偷偷合并产品明定的两次付费决定。
                why_blocked = WAITING_ON_STAGE4_DESIGN_REVIEW
            if why_blocked is None:
                frozen_approval_doc = approval_doc
                design_state = None
                frozen_existing_b = bool(
                    verdict == st.RESUME_SKIP
                    and st.stage2_frozen_v6_subject_validated(ov)
                )
                if approval_doc is not None:
                    design_state = approval_doc.design_review.state
                    design_current = design_state == 'accepted'
                elif frozen_v6:
                    # 普通 queue 入口没有 operator 已冻结的审批投影。只在这条
                    # 冻结 v6 窄例外里补读一次完整 effective view；
                    # 除了既有 B 例外，还要区分「尚未审阅」与「raw all-accept
                    # 已落账但验证权威暂停」。否则后者会被误报成重复审批待办。
                    # 其余书仍沿用原来的单项 currentness 读取，避免把所有 queue
                    # 调用都扩张成一次全审批物化。design 与 structure/gate
                    # 必须同时派生自这一份 doc，不能跨两次锁窗口拼成混合时点。
                    frozen_approval_doc = approvals.get(
                        base, sid, include_spawns=False,
                        operator_projection=True)
                    if frozen_approval_doc.sid != sid:
                        raise ValueError(
                            f'审批投影属于 {frozen_approval_doc.sid!r}，'
                            f'不是当前书 {sid!r}')
                    design_state = frozen_approval_doc.design_review.state
                    design_current = design_state == 'accepted'
                else:
                    design_current = approvals.design_review_is_current(
                        base, sid)
                frozen_existing_b_is_accepted = bool(
                    frozen_existing_b
                    and frozen_approval_doc is not None
                    and frozen_approval_doc.gate['s4']
                    and frozen_approval_doc.structure.state == 'accepted'
                )
                if not design_current and not frozen_existing_b_is_accepted:
                    why_blocked = (
                        WAITING_ON_FROZEN_V6_DESIGN_AUTHORITY
                        if frozen_v6 and design_state == 'suspended'
                        else WAITING_ON_STAGE4_DESIGN_REVIEW
                    )
        if why_blocked is None and node == 'stage4.context':
            if frozen_v6 and verdict != st.RESUME_SKIP:
                # 冻结桥只能消费已经存在且逐字可证的 Context，不能拿 v6 Blueprint
                # 调 current v7 producer。发布一条必然失败的免费动作仍会浪费一轮
                # Commander，并让“只读兼容”看起来像迁移能力。
                why_blocked = WAITING_ON_FROZEN_V6_CONTEXT
            context_approval_doc = approval_doc
            if context_approval_doc is None:
                # 普通 queue 入口不像 operator 那样自带冻结审批投影，但仍要
                # 区分「B 尚未审」与「B 已明确 request_replan」。只调用
                # authorize_context 会把两者都压成 ApprovalRequired，随后又把
                # 后者错送回同一份 B 给 Commander 重审。
                try:
                    context_approval_doc = approvals.get(
                        base, sid, include_spawns=False,
                        operator_projection=True)
                except (
                    ApprovalRequired, UpstreamMissing, ArtifactUnreadable,
                    GateBlocked, UpstreamStale, UpstreamUnproven,
                ):
                    context_approval_doc = None
            context_current = (
                context_approval_doc is not None
                and context_approval_doc.gate['s4']
                and context_approval_doc.structure.state == 'accepted'
            )
            if context_approval_doc is None:
                try:
                    approvals.authorize_context(base, sid)
                    context_current = True
                except (
                    ApprovalRequired, UpstreamMissing, ArtifactUnreadable,
                    GateBlocked, UpstreamStale, UpstreamUnproven,
                ):
                    context_current = False
            if why_blocked is None and not context_current:
                structure_state = (
                    context_approval_doc.structure.state
                    if context_approval_doc is not None else None)
                why_blocked = (
                    WAITING_ON_STAGE4_STRUCTURE_REPLAN
                    if structure_state == 'replan_requested'
                    else WAITING_ON_STAGE4_STRUCTURE_REVIEW
                )
        if why_blocked is None and spec.stage == 'stage6':
            # Stage5 Review 通过仍不等于 Commander 已逐项闭合。只消费审批服务
            # 的 current view；缺回执、旧主体、blocked workpack 与 read failure
            # 全部关闭式停在 Stage6 之前。若本次 lane 还会写 Stage5，盘上旧 closed
            # 也必然会被新主体作废，不能拿来提前放行同一批下游。
            if approval_doc is not None:
                closure_state = approval_doc.closure.state
            else:
                try:
                    closure_state = approvals.stage5_closure_view(base, sid).state
                except (
                    ApprovalRequired, UpstreamMissing, ArtifactUnreadable,
                    GateBlocked, UpstreamStale, UpstreamUnproven,
                    BlockingFindingsUnresolved,
                ):
                    closure_state = 'unreadable'
            if stage5_write_will_run or closure_state != 'closed':
                why_blocked = WAITING_ON_STAGE5_CLOSURE
        review_follows_planned_write = (
            node == 'stage5.review'
            and stage5_write_will_run
            and verdict == st.RESUME_SKIP)
        if (why_blocked is None and verdict == st.RESUME_SKIP
                and not review_follows_planned_write):
            continue                                 # 已存在即跳过
        if why_blocked is None and review_follows_planned_write:
            # 当前 Review 即使 fresh，前一项 writer 落盘后也必须按新主体
            # 重算。把它显式排入零成本 step；writer 若失败，队列的
            # “停在那里”仍会让这一项 not_run。
            verdict = st.RESUME_RUN
            why = 'stage5.write 已排入本次队列；落盘后必须按新 canonical 主体零成本重算 Review'

        if why_blocked is None and verdict == st.RESUME_LEGACY:
            why_blocked = WAITING_ON_LEGACY
        if (why_blocked is None and verdict == st.RESUME_REDO
                and allow_deterministic_redo):
            deterministic_redo_flags = st.operator_redo_flags(ov, node)
        if (why_blocked is None and verdict == st.RESUME_UNKNOWN) or (
            why_blocked is None
            and verdict == st.RESUME_REDO
            and deterministic_redo_flags is None
        ):
            # UNKNOWN 永远不能猜成 RUN。REDO 在普通队列也一律停；只有 AgentRun
            # 单步 operator 能拿到 state 登记并证明过的 canonical flags。仅仅把
            # ``allow`` 打开并不放宽 file_changed/unreadable/INVALID 等关闭式分支。
            why_blocked = WAITING_ON_RESUME_DECISION

        # 称名投影是 Global 的确定性伴生产物。缺/坏/stale 时只冻结免费物化
        # 命令，绝不能因 ``produces`` 少一份而把默认付费 Global argv 排进队列。
        flags: list[str] = (
            list(stage4_design_action_flags)
            if stage4_design_action_flags is not None
            else list(deterministic_redo_flags)
            if deterministic_redo_flags is not None
            else ['--renormalize']
            if projection_recovery or legacy_blueprint_recovery else []
        )
        steps = None
        if (projection_blocked
                or (blueprint_invalid_recovery
                    and not legacy_blueprint_recovery)
                or (naming_projection_blocked
                    and stuck == WAITING_ON_INBOX)):
            # 称名面未收口时，Global 之后的 blocked 项也不能残留被冻结的
            # 付费命令；否则恢复确认界面仍会把“不允许执行”展示成潜在花费。
            steps = []
        if node == 'stage5.write':
            stage5_state = next(
                (stage for stage in ov.stages if stage.stage == 'stage5'), None)
            has_live_invalid = bool(
                stage5_state is not None
                and any(a.invalid for a in stage5_state.artifacts))
            review_not_current = bool(
                stage5_state is not None
                and stage5_state.review_state not in (None, 'missing', 'fresh'))
            review_blocks_paid_write = bool(
                review_not_current and deterministic_redo_flags is None)
            writing_not_current = not (
                bool(approval_doc.writing and approval_doc.gate['s4'])
                if approval_doc is not None
                else approvals.writing_is_current(base, sid)
            )

            # 本层三个闸的顺序是恢复成本顺序：先处理 0 LLM INVALID；普通续写再
            # 免费重跑旧 Review；最后才向人索取付费写作授权。若 operator 已证明
            # 上游 consumed-change 并导出精确重写章，旧 Review 绑定的是旧正文，
            # 不能反向卡住重建；新章落盘后下一轮自然会免费重算 Review。
            # 其余上游 stuck / legacy / REDO / UNKNOWN 已在上面占住 blocker。
            if why_blocked is None and has_live_invalid:
                # canonical 与更新的 INVALID 可并存；默认写作只补缺章，无法处理
                # 这份候选，反而会以“没有要写的章”结束。actor/reason 又只能由
                # 人提供，所以队列停在 0 LLM revalidate 入口，不冻结一条假命令。
                why_blocked = WAITING_ON_INBOX
            if why_blocked is None and review_blocks_paid_write:
                why_blocked = WAITING_ON_REVIEW
            if why_blocked is None and writing_not_current:
                # Stage5 v2 只有一条全书 canonical 闭卷写作命令。授权是独立人工决定；
                # 队列不从「默认全书」反推出同意花钱。
                why_blocked = WAITING_ON_WRITING_APPROVAL
            if has_live_invalid or review_blocks_paid_write or writing_not_current:
                steps = []

        if why_blocked in (
            WAITING_ON_INBOX,
            WAITING_ON_STAGE2_REVIEW,
            WAITING_ON_STAGE2_EDIT_CORRECTION,
            WAITING_ON_STAGE4_DESIGN_REVIEW,
            WAITING_ON_STAGE4_BINDING_RESOLUTION,
            WAITING_ON_STAGE4_STRUCTURE_REPLAN,
            WAITING_ON_STAGE4_STRUCTURE_REVIEW,
            WAITING_ON_FROZEN_V6_DESIGN_AUTHORITY,
            WAITING_ON_FROZEN_V6_CONTEXT,
            WAITING_ON_STAGE5_CLOSURE,
        ):
            # 人审/相位决定完成前，当前队列快照里不放下游付费 argv。状态改变后
            # 必须重新 plan，不能把旧快照重解释为新授权。
            steps = []
        if (why_blocked == WAITING_ON_STAGE4_DESIGN_ACTION
                and node != 'stage4.design'):
            # 只有当前 A 恢复动作本身携带 exact argv；同一 blocker 向下游传播时
            # 仍不得提前冻结 B/写作等命令或把它们计入本轮付费面。
            steps = []

        if verdict == st.RESUME_LEGACY or why_blocked == WAITING_ON_RESUME_DECISION:
            steps = []

        if steps is None and why_blocked is not None and node in HUMAN_NODES:
            # ⚠⚠ **人还没签字之前，这道闸没有一条「诚实的可执行命令」可以快照。**
            # `stage0.approve` / `stage6.approve` / `stage7.approve` 的 argv 必填
            # `--actor` / `--reason`（人此刻还没给出的东西）⇒ `build_steps` 会抛
            # `UnsupportedRunMode`。而这一项本来就是 `blocked`，它要回答的是
            # 「在等谁」，不是「怎么跑」。
            # ⚠ 曾经这里无条件 `build_steps`：于是一本「stage0 还没人批」的书会让
            #   **整条队列计划当场抛异常**——CLI `queue --plan-only` 退出码 2、
            #   HTTP 400，而真正的事实只是「在等人批准 stage0」。
            # ⇒ 与上面 `stage5.write` 缺写作授权时**同一处置**：留空命令面。
            #   人给出裁决之后命令面本来就会变，那时旧队列本就需要重排。
            #
            # `why_blocked` 也可能是更早节点留下的 ``stuck``。此时后续人工节点
            # 同样不能调用 ``build_steps``：它缺少尚未发生的人类 pick/actor/reason/hash，
            # 规划器的职责只是把上游阻塞传播到底，而不是提前伪造一条人工命令。
            steps = []
        if steps is None:
            steps = build_steps(
                node, sid, flags, base=base,
                follow_check=follow_check)

        item = QueueItem(
            sid=sid, node=node, flags=flags, steps=steps,
            why=(f'{verdict}：{why}' if why_blocked is None
                 else _blocked_why(
                     why_blocked, why, legacy_frozen_v6=frozen_v6)),
            waiting_on=why_blocked,
        )
        (blocked if why_blocked else todo).append(item)
        if node == 'stage5.write' and why_blocked is None:
            stage5_write_will_run = True
        if why_blocked and stuck is None:
            stuck = why_blocked
    return todo, blocked


def _blocked_why(
    waiting_on: str, verdict_why: str, *, legacy_frozen_v6: bool = False,
) -> str:
    if waiting_on == WAITING_ON_INBOX:
        return (
            '上游或本层有被闸门拦下的稿要人处理（0 LLM 提升路是 '
            '`--revalidate --actor <id> --reason <why>`，同轮重判并留不可变审计）；'
            f'{verdict_why}')
    if waiting_on == WAITING_ON_WRITING_APPROVAL:
        return ('还没有当前 Stage5 全书闭卷写作授权；先在 Stage5 空状态明确授权，'
                f'队列不会从默认路径反推同意花钱；{verdict_why}')
    if waiting_on == WAITING_ON_STAGE5_CLOSURE:
        return (
            'Stage5 当前 typed closure 尚未 closed：缺回执、Review/章稿漂移或工作包仍有'
            '阻断项都会停在这里。先经 `approval template stage5` 与审批提交收口；'
            '需要改稿/重判时只走登记的 0 成本恢复入口，再重算 Review；'
            f'{verdict_why}')
    if waiting_on == WAITING_ON_STAGE2_REVIEW:
        return (
            'Stage2 尚未在当前完整主体上收口：global/blueprint 可能缺失、过期或有'
            '问题稿/联合 HARD，也可能是全局逐项复核尚未全部 PASS，或 current '
            'seen/gate.s2 proof 尚未随最新主体恢复。global 与 blueprint '
            '可以先后生成并在人工阅读期间并行推进，但 Stage4A/B、写作上下文、后续'
            '付费调用与最终成书都不会越过这道门；处理完具体终态后请重新预览队列；'
            f'{verdict_why}')
    if waiting_on == WAITING_ON_STAGE4_DESIGN_REVIEW:
        if legacy_frozen_v6:
            return (
                '冻结 v6 Blueprint 没有 v7 relation/preflight authority；本次 A 人审'
                '可如实提交 raw all-accept，但 effective 投影保持 suspended，'
                '不能授权新 B。只有真实 request_repair 才会签发零 wire '
                'full-chapter edit 票，不得为获票伪造问题。'
                '需要新 B 时必须在 clean v7 base 重建；'
                f'{verdict_why}')
        return (
            'Stage4A 尚无当前全书 all-accept typed 盲审；若候选尚未落盘，本次'
            '队列只能跑 A。A 落盘后请完成策略与逐章审阅，再重新预览并明确批准'
            '付费 Stage4B；'
            f'{verdict_why}')
    if waiting_on == WAITING_ON_STAGE4_BINDING_RESOLUTION:
        return (
            'Stage4B 已对当前 Stage4A 候选落盘 fresh typed infeasible 结论；'
            '队列不会再次暴露或执行同一个 B，也不会替人猜 repair/replan。'
            '请先提交当前 hash-bound design resolution，执行对应 A 修订后再单独确认 B；'
            f'{verdict_why}')
    if waiting_on == WAITING_ON_STAGE4_STRUCTURE_REPLAN:
        return (
            'Stage4B 当前最终结构决定已有 request_replan；不能再次审同一份 B，'
            '也不能把结构理由直接当模型指令。先由 Commander 在当前 A 工作包上'
            '提交新的 typed request_repair/request_replan 决定；决定落盘后只由 '
            'Executor 按 opaque token 精确恢复 Stage4A；'
            f'{verdict_why}')
    if waiting_on == WAITING_ON_STAGE4_DESIGN_ACTION:
        return (
            'Stage4A 已有当前 hash-bound typed 修订决定；不再调用 Commander。'
            '下一步只按该决定发布的 opaque token 启动 Executor 精确 repair/replan，'
            '完成后再重新规划并单独确认 Stage4B；'
            f'{verdict_why}')
    if waiting_on == WAITING_ON_STAGE4_STRUCTURE_REVIEW:
        return (
            'Stage4B 的最终 Plan 尚未在当前主体上逐章 all-accept；免费 '
            '`stage4.context` 也必须等这张最终结构票，审批后重新预览才会出现；'
            f'{verdict_why}')
    if waiting_on == WAITING_ON_STAGE2_EDIT_CORRECTION:
        return (
            '上一份带 post_edit_review 的 Stage2 GlobalEdit 已执行，但被确定性 HARD '
            '闸拒绝；盘上的 edit、INVALID 与不可变基线仍精确闭合。当前只允许恢复为'
            '同一逻辑任务的第二次、也是最后一次 Commander 纠正，不重跑 Blueprint，'
            '也不重新发放两轮模型预算；'
            f'{verdict_why}')
    if waiting_on == WAITING_ON_FROZEN_V6_DESIGN_AUTHORITY:
        return (
            '冻结 v6 的当前 Stage4A raw all-accept 已如实落账，无需重复'
            '审阅；但该主体缺 v7 narrative_relations/preflight validation '
            'authority，effective design review 因此保持 suspended。本路径不签发 '
            's4er1，不开放付费 repair/replan 或 Stage4B；需要新 B 时只能在 '
            f'clean v7 base 重建；{verdict_why}')
    if waiting_on == WAITING_ON_FROZEN_V6_CONTEXT:
        return (
            '冻结 v6 只读桥只允许消费逐字可证的既有 WriterContext；当前 Context '
            '缺失或不匹配，不能调用 v7 producer 冒充迁移。需要新 Context/B 时必须'
            f'在 clean v7 base 重建；{verdict_why}')
    if waiting_on == WAITING_ON_REVIEW:
        return ('Stage5 Review 不是 fresh，旧结论不得驱动付费作业；'
                f'先零成本运行 `stage5.review`；{verdict_why}')
    if waiting_on == WAITING_ON_LEGACY:
        return f'{verdict_why}；旧产物只读保留，必须显式重建 canonical Stage5'
    if waiting_on == WAITING_ON_RESUME_DECISION:
        return (
            '续跑结论要求人工决定，队列不会把 REDO/UNKNOWN 猜成可执行命令；'
            f'{verdict_why}')
    return f'在等 `{waiting_on}`——这一步是人做的裁决，机器替不了；{verdict_why}'


def plan(base: str | os.PathLike[str], sids: Sequence[str], *, until_stage: str,
         follow_check: bool = True) -> QueuePlan:
    """一批书的计划。**顺序 = 书的顺序 × 处理链顺序。**

    ⚠ 不做「把同一层的所有书凑一批」那种重排：那会让一本书的失败
    影响到另一本书**已经跑到一半的**链，而 `R-13.4` 要的是「停在那里」，
    停在哪里必须说得清。
    """
    items: list[QueueItem] = []
    blocked: list[QueueItem] = []
    for sid in sids:
        todo, stop = plan_book(base, sid, until_stage=until_stage,
                               follow_check=follow_check)
        items += todo
        blocked += stop
    return QueuePlan(items=items, blocked=blocked,
                     paid_items=sum(1 for it in items + blocked
                                    if any(s.paid for s in it.steps)))


# ─────────────────────────────────────────────────────────────────────────────
# 一次性预先确认（`R-13.3` / `Q-10`）
# ─────────────────────────────────────────────────────────────────────────────

def confirm(p: QueuePlan, *, yes: bool) -> None:
    """花钱的项必须**一次性预先确认**——不能因为「排进队列了」就变成静默扣费。

    ⚠ 摊开的是**将要执行的那些命令**（复数），不是一句「你确定要跑吗」：
    人靠它确认参数没点错。⚠ **不显示预估花费**（`Q-10`：猜一个数写在界面上比不显示更坏）。
    """
    if yes or not p.paid_items:
        return
    lines = [f'这批计划里有 {p.paid_items} 项会花钱。将要执行的命令：']
    for it in p.items + p.blocked:
        for s in it.steps:
            if s.paid:
                lines.append(f'  $ {shell_line(s.argv)}')
    raise ApprovalRequired('\n'.join(lines) + '\n⇒ 确认无误后加 `--yes` 重跑')


# ─────────────────────────────────────────────────────────────────────────────
# 「停在那里」的计数（`R-13.4`）
# ─────────────────────────────────────────────────────────────────────────────

def fold_counts(outcomes: Iterable[bool], total: int) -> QueueCounts:
    """按顺序吃每一项的成败，**第一项失败就停**，剩下的全算 `not_run`。

    ⭐ **三个数不许合并。** 合并成「N 成功 / M+1 失败」是一次真实的信息损失：
    `not_run` 那些**既没有失败也没有成功，它们根本没被执行**——
    而人要决定的恰恰是「修完这一项之后从哪儿续」。
    """
    done = failed = 0
    for ok in outcomes:
        if ok:
            done += 1
            continue
        failed = 1
        break
    if done + failed > total:
        raise ValueError(f'跑了 {done + failed} 项，而计划里只有 {total} 项')
    return QueueCounts(done=done, failed=failed, not_run=total - done - failed)


# ─────────────────────────────────────────────────────────────────────────────
# 持久队列：append-only 事件、折叠视图与 runner（TODO-01）
# ─────────────────────────────────────────────────────────────────────────────

QUEUES_FILENAME = 'queues.jsonl'
RESTART_NOTE = '服务重启前这项已经交给作业执行器，但队列没有记到终态；先对照作业与产物恢复'
_STATE_LOCK = threading.RLock()


class QueueRegistry:
    """Process-local queue runners; persisted truth remains the event stream."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._live: set[str] = set()

    def acquire(self, queue_id: str) -> None:
        with self._lock:
            if queue_id in self._live:
                raise QueueConflict(f'队列 {queue_id} 已有 runner 在消费')
            self._live.add(queue_id)

    def release(self, queue_id: str) -> None:
        with self._lock:
            self._live.discard(queue_id)

    def live(self) -> set[str]:
        with self._lock:
            return set(self._live)


REGISTRY = QueueRegistry()
_THREADS_LOCK = threading.Lock()
_THREADS: dict[str, threading.Thread] = {}


def queue_path(base: str | os.PathLike[str]) -> Path:
    return jobs.service_dir(base) / QUEUES_FILENAME


def append_event(base: str | os.PathLike[str], event: QueueEvent) -> None:
    """Append one durable queue transition; never read-modify-write the log.

    ⚠ 写入方既有服务进程也有 CLI `queue`，而每本书一条 lane 是并行的
    （`R-13.7`）⇒ 互斥必须跨进程，落点是 `novel/appendlog.py`；
    `read_events` 那条「只容忍最后一行」要靠它才成立。
    """
    append_line(
        queue_path(base),
        json.dumps(event.model_dump(mode='json'), ensure_ascii=False))


def read_events(base: str | os.PathLike[str]) -> list[QueueEvent]:
    """Read queue events, tolerating only a truncated final line."""
    p = queue_path(base)
    if not p.is_file():
        return []
    raw = p.read_bytes().decode('utf-8', errors='replace').splitlines()
    out: list[QueueEvent] = []
    for index, line in enumerate(raw):
        if not line.strip():
            continue
        try:
            out.append(_decode_event(json.loads(line)))
        except Exception:  # noqa: BLE001 - only the interrupted tail is recoverable
            if index == len(raw) - 1:
                break
            raise
    return out


def _decode_event(payload: object) -> QueueEvent:
    """只在磁盘边界消化旧队列的 ``mode``。

    ``mode=null`` 是旧 serializer 在所有条目上留下的噪声，直接剥离。
    任一非空 deep/shallow 表明这是旧 Stage5 双臂快照：整份计划
    投影为 ``queue:plan-changed`` 且清空命令，保证 API 不暴露 mode，
    runner 也永远不会把旧快照解释成 canonical 写作。
    """
    if not isinstance(payload, dict):
        return QueueEvent.model_validate(payload)
    data = dict(payload)
    start = data.get('start')
    if not isinstance(start, dict):
        return QueueEvent.model_validate(data)
    projected_start = dict(start)
    plan = projected_start.get('plan')
    if not isinstance(plan, dict):
        return QueueEvent.model_validate(data)
    projected_plan = dict(plan)
    has_legacy_arm = False
    buckets: dict[str, list[object]] = {}
    for bucket in ('items', 'blocked'):
        raw_items = projected_plan.get(bucket, [])
        if not isinstance(raw_items, list):
            return QueueEvent.model_validate(data)
        projected_items: list[object] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                projected_items.append(raw_item)
                continue
            item = dict(raw_item)
            if 'mode' in item:
                legacy_mode = item.pop('mode')
                if legacy_mode is not None:
                    if not (
                        item.get('node') == 'stage5.write'
                        and legacy_mode in LEGACY_STAGE5_MODES
                    ):
                        raise ValueError(
                            'queues.jsonl 的 mode 只允许旧 stage5.write deep/shallow')
            else:
                legacy_mode = None
            if jobs._has_retired_stage5_selector(item, legacy_mode):
                has_legacy_arm = True
            projected_items.append(item)
        buckets[bucket] = projected_items

    if has_legacy_arm:
        reason = (
            '旧 Stage5 deep/shallow 队列快照与 canonical 契约不兼容；'
            '本记录只读，必须重新预览并创建队列')
        blocked: list[object] = []
        for raw_item in buckets['items'] + buckets['blocked']:
            if not isinstance(raw_item, dict):
                blocked.append(raw_item)
                continue
            item = dict(raw_item)
            item['steps'] = []
            item['why'] = reason
            item['waiting_on'] = WAITING_ON_REPLAN
            blocked.append(item)
        projected_plan['items'] = []
        projected_plan['blocked'] = blocked
        projected_plan['paid_items'] = 0
    else:
        projected_plan['items'] = buckets['items']
        projected_plan['blocked'] = buckets['blocked']
    projected_start['plan'] = projected_plan
    data['start'] = projected_start
    return QueueEvent.model_validate(data)


def new_queue_id(now: float | None = None) -> str:
    return f'q-{int(now if now is not None else time.time())}-{uuid.uuid4().hex[:8]}'


def _ordered_items(start: QueueStart) -> list[QueueItem]:
    """Restore book order and node order after QueuePlan's runnable/blocked split."""
    rank = {node: index for index, node in enumerate(NODES)}
    all_items = start.plan.items + start.plan.blocked
    out: list[QueueItem] = []
    for sid in start.sids:
        lane = [item for item in all_items if item.sid == sid]
        out.extend(sorted(lane, key=lambda item: rank[item.node]))
    return out


def _lane_counts(entries: list[QueueEntry]) -> QueueLaneCounts:
    states = ('done', 'landed_red', 'failed', 'not_run', 'pending', 'running', 'unknown',
              'cancelled', 'blocked')
    values = {state: sum(entry.state == state for entry in entries) for state in states}
    return QueueLaneCounts(**values)


def _run_state(entries: list[QueueEntry]) -> str:
    states = {entry.state for entry in entries}
    if 'running' in states:
        return 'running'
    if 'failed' in states:
        return 'failed'
    if 'landed_red' in states:
        return 'landed_red'
    if 'unknown' in states:
        return 'unknown'
    if 'pending' in states:
        return 'pending'
    if 'blocked' in states:
        return 'blocked'
    if 'cancelled' in states:
        return 'cancelled'
    return 'ok'


def fold(events: Iterable[QueueEvent], *, live: set[str] | None = None) -> list[QueueRun]:
    """Fold the durable stream; stale ``running`` becomes explicit ``unknown``."""
    live = set() if live is None else set(live)
    starts: dict[str, tuple[int, QueueStart]] = {}
    transitions: dict[str, list[QueueEvent]] = {}
    updated: dict[str, int] = {}
    for event in events:
        updated[event.queue_id] = event.ts
        if event.kind == 'created':
            if event.queue_id in starts:
                raise ValueError(f'duplicate created event for queue {event.queue_id}')
            assert event.start is not None
            starts[event.queue_id] = (event.ts, event.start)
        else:
            transitions.setdefault(event.queue_id, []).append(event)

    out: list[QueueRun] = []
    for queue_id, (created, start) in starts.items():
        entries = [QueueEntry(
            index=index, item=item,
            state='blocked' if item.waiting_on else 'pending',
            waiting_on=item.waiting_on,
            note=item.why if item.waiting_on else None,
        ) for index, item in enumerate(_ordered_items(start))]
        for event in transitions.get(queue_id, []):
            assert event.item_index is not None and event.state is not None
            if event.item_index >= len(entries):
                raise ValueError(
                    f'queue {queue_id} event points outside plan: {event.item_index}')
            old = entries[event.item_index]
            entries[event.item_index] = QueueEntry(
                index=old.index, item=old.item, state=event.state,
                job_id=event.job_id, exit_code=event.exit_code,
                waiting_on=event.waiting_on, note=event.note,
            )
        if queue_id not in live:
            entries = [
                entry.model_copy(update={'state': 'unknown', 'note': RESTART_NOTE})
                if entry.state == 'running' else entry
                for entry in entries
            ]
        counts = {
            sid: _lane_counts([entry for entry in entries if entry.item.sid == sid])
            for sid in start.sids
        }
        out.append(QueueRun(
            queue_id=queue_id, created=created,
            updated=updated.get(queue_id, created), start=start,
            state=_run_state(entries), entries=entries, counts_by_sid=counts,
        ))
    return sorted(out, key=lambda record: record.created, reverse=True)


def records(base: str | os.PathLike[str], *,
            registry: QueueRegistry = REGISTRY) -> list[QueueRun]:
    return fold(read_events(base), live=registry.live())


def _one(base: str | os.PathLike[str], queue_id: str, *,
         registry: QueueRegistry = REGISTRY) -> QueueRun:
    record = next((record for record in records(base, registry=registry)
                   if record.queue_id == queue_id), None)
    if record is None:
        raise UpstreamMissing(f'没有 queue_id={queue_id!r} 的持久队列')
    return record


def create(base: str | os.PathLike[str], sids: Sequence[str], *, until_stage: str,
           yes: bool, follow_check: bool = True,
           queue_id: str | None = None) -> QueueRun:
    """Plan, confirm and persist a queue without starting its runner."""
    unique = list(dict.fromkeys(sids))
    if not unique or len(unique) != len(sids):
        raise QueueConflict('队列必须至少包含一本书，且 sid 不得重复')
    planned = plan(base, unique, until_stage=until_stage, follow_check=follow_check)
    confirm(planned, yes=yes)
    queue_id = queue_id or new_queue_id()
    if any(event.queue_id == queue_id for event in read_events(base)):
        raise QueueConflict(f'queue_id={queue_id!r} 已存在')
    start = QueueStart(
        sids=unique, until_stage=until_stage, plan=planned,
        paid_confirmed=bool(yes or not planned.paid_items),
    )
    append_event(base, QueueEvent(
        queue_id=queue_id, kind='created', ts=int(time.time()), start=start))
    return _one(base, queue_id)


def _append_state(base: str | os.PathLike[str], queue_id: str, index: int,
                  state: str, *, job_id: str | None = None,
                  exit_code: int | None = None, waiting_on: str | None = None,
                  note: str | None = None) -> None:
    append_event(base, QueueEvent(
        queue_id=queue_id, kind='item_state', ts=int(time.time()),
        item_index=index, state=state, job_id=job_id, exit_code=exit_code,
        waiting_on=waiting_on, note=note,
    ))


def cancel(base: str | os.PathLike[str], queue_id: str, item_index: int, *,
           registry: QueueRegistry = REGISTRY) -> QueueRun:
    """Cancel only an item that has never started, then stop its novel lane."""
    with _STATE_LOCK:
        run = _one(base, queue_id, registry=registry)
        if item_index < 0 or item_index >= len(run.entries):
            raise QueueConflict(f'队列 {queue_id} 没有第 {item_index} 项')
        target = run.entries[item_index]
        if target.state not in ('pending', 'blocked'):
            raise QueueConflict(
                f'第 {item_index} 项当前是 {target.state}；只能取消尚未开始的 pending/blocked 项，'
                '运行中的 LLM 调用不能伪装成可无损取消')
        reason = f'人工取消尚未开始的第 {item_index} 项'
        _append_state(base, queue_id, item_index, 'cancelled', note=reason)
        for entry in run.entries[item_index + 1:]:
            if entry.item.sid == target.item.sid and entry.state in ('pending', 'blocked'):
                _append_state(
                    base, queue_id, entry.index, 'not_run',
                    note=f'同书较早的第 {item_index} 项被取消；本项未启动')
    return _one(base, queue_id, registry=registry)


def _command_surface(item: QueueItem) -> tuple[object, ...]:
    """The immutable part of a queue item that was shown and confirmed."""
    return (
        item.node, tuple(item.flags),
        tuple((tuple(step.argv), step.cwd, step.paid, step.writes)
              for step in item.steps),
    )


def _current_plan_items(base: str | os.PathLike[str], start: QueueStart) -> list[QueueItem]:
    """Rebuild the currently required command surface in lane/node order."""
    return _current_plan_items_for_sids(base, start, start.sids)


def _current_plan_items_for_sids(
        base: str | os.PathLike[str], start: QueueStart,
        sids: Iterable[str],
) -> list[QueueItem]:
    """只重建指定 lane；unresolved lane 的盘面在 startup 中不得触碰。"""
    if start.until_stage in RETIRED_STAGES:
        return []
    rank = {node: index for index, node in enumerate(NODES)}
    out: list[QueueItem] = []
    selected = frozenset(sids)
    for sid in start.sids:
        if sid not in selected:
            continue
        todo, blocked = plan_book(base, sid, until_stage=start.until_stage)
        out.extend(sorted(todo + blocked, key=lambda item: rank[item.node]))
    return out


def _live_disposition(base: str | os.PathLike[str], run: QueueRun,
                      entry: QueueEntry) -> tuple[str, str | None, str]:
    """Recheck disk state before launch; return state/waiting_on/reason."""
    if run.start.until_stage in RETIRED_STAGES or entry.item.node in RETIRED_NODES:
        return (
            'blocked', WAITING_ON_REPLAN,
            '该队列快照包含已经退役的 Stage3 节点；历史仍可读，但不会重新调度，'
            '请按当前节点面重新预览并创建队列',
        )
    todo, blocked = plan_book(
        base, entry.item.sid, until_stage=run.start.until_stage)
    same_node = [item for item in todo + blocked if item.node == entry.item.node]
    if not same_node:
        return 'done', None, '盘上产物存在且当前新鲜，恢复时跳过，不重复调用'
    current = same_node[0]
    if _command_surface(current) != _command_surface(entry.item):
        return (
            'blocked', WAITING_ON_REPLAN,
            '盘面或人工选择已改变，当前命令与队列创建时确认的快照不同；'
            '旧队列不会执行另一条命令，请重新预览并创建队列',
        )
    if current.waiting_on:
        return 'blocked', current.waiting_on, current.why
    return 'pending', None, current.why


def _remaining_paid_entries(run: QueueRun) -> list[QueueEntry]:
    """Paid frozen entries that a new resume turn could still reach."""
    terminal = {'done', 'landed_red', 'failed', 'not_run', 'cancelled'}
    return [
        entry for entry in run.entries
        if entry.state not in terminal and any(step.paid for step in entry.item.steps)
    ]


def frozen_resume_commands(
    base: str | os.PathLike[str], queue_id: str, *,
    registry: QueueRegistry = REGISTRY,
) -> list[str]:
    """Exact persisted paid commands which must be shown before resume."""
    run = _one(base, queue_id, registry=registry)
    return [
        shell_line(step.argv)
        for entry in _remaining_paid_entries(run)
        for step in entry.item.steps
        if step.paid
    ]


def _confirm_resume(
    base: str | os.PathLike[str], queue_id: str, *, yes: bool,
    registry: QueueRegistry,
) -> QueueRun:
    """Authorize one resume turn and release only restart-confirmation blocks.

    ``run``/``start`` deliberately continue to refuse the persisted restart
    marker.  This function is the sole path which may remove it, after showing
    and explicitly confirming the exact frozen commands.
    """
    with _STATE_LOCK:
        run = _one(base, queue_id, registry=registry)
        commands = frozen_resume_commands(base, queue_id, registry=registry)
        if commands and not yes:
            lines = [
                f'队列 {queue_id} 的本次恢复仍可能花钱。创建时冻结、现在必须重新确认的命令：',
                *(f'  $ {command}' for command in commands),
                '⇒ CLI 加 `--yes`；API 发送 `{"confirmed": true}`。未确认不会启动任何命令',
            ]
            raise ApprovalRequired('\n'.join(lines))

        # Startup persists a special block so calling run/start directly cannot
        # reinterpret it as ordinary pending work.  Once this resume turn has
        # explicit authorization, restore each such entry's *current* disk/gate
        # disposition; command-surface drift still becomes WAITING_ON_REPLAN.
        for entry in run.entries:
            if not (entry.state == 'blocked'
                    and entry.waiting_on == WAITING_ON_RESTART_CONFIRMATION):
                continue
            disposition, waiting_on, reason = _live_disposition(base, run, entry)
            _append_state(
                base, queue_id, entry.index, disposition,
                waiting_on=waiting_on,
                note=('本次恢复已重新展示并确认冻结付费命令；' + reason),
            )
        return _one(base, queue_id, registry=registry)


def _job_record(base: str | os.PathLike[str], job_id: str,
                registry: jobs.JobRegistry) -> object | None:
    return next((record for record in jobs.records(base, registry=registry)
                 if record.job_id == job_id), None)


def _prior_execution_identity(
    base: str | os.PathLike[str], queue_id: str, item_index: int,
) -> tuple[str | None, str | None]:
    """本队列项上一次 execution 的 `(run_id, job_id)`；从未启动过为 `(None, None)`。

    只认 durable 事件流：队列项历史里最后一个带 ``job_id`` 的事件指向原 job，
    原 job 的 ``started`` 事件携带业务 ``run_id``（批次 3）。旧盘作业没有
    run_id 字段时返回 ``(None, prior_job_id)``——新 job 照常新造 run 身份，
    不按时间猜、不伪造恢复链。
    """
    prior_job_id: str | None = None
    for event in read_events(base):
        if (event.queue_id == queue_id and event.item_index == item_index
                and event.job_id):
            prior_job_id = event.job_id
    if prior_job_id is None:
        return None, None
    for event in jobs.read_events(base):
        if (event.job_id == prior_job_id and event.kind == 'started'
                and event.start is not None and event.start.run_id):
            return event.start.run_id, prior_job_id
    return None, prior_job_id


def _auto_resume_block_reason(
    base: str | os.PathLike[str], run: QueueRun, entry: QueueEntry,
) -> str | None:
    """批次 3（`R-13.3` 修订）：付费 pending 项能否按原授权自动续跑。

    返回 ``None`` = 允许自动续跑（原授权 durable、原 execution 的代码/profile/
    argv 零漂移、原 run 身份完整可对证）；返回字符串 = 拒绝原因（落
    restart-confirmation，等显式确认，不自动猜）。只适用于有 durable
    queue/job 父记录的恢复执行；手工 CLI 与从未启动过的付费项不在范围。
    """
    if not run.start.paid_confirmed:
        return '队列创建时没有持久化付费确认（paid_confirmed=false）'
    prior_run_id, prior_job_id = _prior_execution_identity(
        base, run.queue_id, entry.index)
    if prior_job_id is None:
        return ('本项在服务重启前从未启动过（没有可对证的执行身份）；'
                '首次执行仍需显式确认')
    if prior_run_id is None:
        return (f'原 job={prior_job_id} 是无 run 身份的旧盘记录；'
                '不自动续跑、不按时间猜')
    prior_start = None
    for event in jobs.read_events(base):
        if event.job_id == prior_job_id and event.kind == 'started':
            prior_start = event.start
    if prior_start is None:
        return f'原 job={prior_job_id} 的 started 记录缺失，无法对证身份'
    if prior_start.code != prov.code_version():
        return (f'代码身份漂移：原 run 是 {prior_start.code}，当前是 '
                f'{prov.code_version()}；恢复必须 replan，不自动续跑')
    step_backends = [
        step.executor_backend for step in entry.item.steps
        if step.executor_backend is not None]
    if (prior_start.executor_backend is not None and step_backends
            and any(backend != prior_start.executor_backend
                    for backend in step_backends)):
        return 'executor backend/provider/模型/effort 与原 execution 不一致；不自动续跑'
    if (any(step.executor_backend for step in entry.item.steps)
            and prior_start.egress_authorized is not True):
        return '原 execution 未持久化正文外发授权；不自动续跑'
    return None


def _recover_running(base: str | os.PathLike[str], run: QueueRun,
                     entry: QueueEntry, *, job_registry: jobs.JobRegistry) -> str:
    """Recover one stale running event from job records and artifact truth."""
    assert entry.job_id is not None
    job = _job_record(base, entry.job_id, job_registry)
    if job is not None and job.state == 'running':
        return 'running'
    if job is not None and job.state in ('ok', 'landed_red', 'failed'):
        state = {'ok': 'done', 'landed_red': 'landed_red', 'failed': 'failed'}[job.state]
        note = ('从持久作业终态恢复队列项；' + jobs.LANDED_RED_NOTE
                if job.state == 'landed_red' else '从持久作业终态恢复队列项')
        _append_state(
            base, run.queue_id, entry.index, state, job_id=entry.job_id,
            exit_code=job.exit_code, note=note)
        return state
    disposition, waiting_on, reason = _live_disposition(base, run, entry)
    if disposition == 'done':
        _append_state(
            base, run.queue_id, entry.index, 'done', job_id=entry.job_id,
            note=f'{RESTART_NOTE}；{reason}')
        return 'done'
    _append_state(
        base, run.queue_id, entry.index, disposition,
        waiting_on=waiting_on,
        note=(f'{RESTART_NOTE}；旧作业是 unknown，当前盘面仍需执行；'
              '重试沿用原命令与缓存，不把 unknown 当成功'),
    )
    return disposition


def _stop_reason(failed: QueueEntry) -> str:
    """停下的那一句话。**必须说出上一项到底是哪一种失败**——处置是相反的。

    ⚠⚠ exit 1（正式结果已落盘、结论不通过）与 exit 2/3/70（压根没跑成）在
    `JOB_STATE` 里已分成 `landed_red` / `failed`。人拿到「第 9 项后停下」
    这句话时要做的事，
    前者是**查看负结论并裁决**，后者是**修完重跑**——不说清就等于让人先去猜。
    实测：`stage6.hook` exit 1，`hook.json` 好好地在盘上，后三项被记成
    `not_run`，日志上与一次真崩一模一样。
    """
    if X.landed_but_red(failed.exit_code):
        return (f'同书第 {failed.index} 项（{failed.item.node}）**正式产物已落盘**、'
                f'但结论不通过（exit 1）；按 `R-13.4` 队列停在这里等人裁决，'
                f'本项没有启动。⚠ 这不是「没跑成」——先看那一层的屏与恢复指引，'
                f'不要当进程失败盲目重跑')
    return (f'同书第 {failed.index} 项（{failed.item.node}）没跑成'
            f'（exit {failed.exit_code}）；本项没有启动')


def _mark_not_run_after(base: str | os.PathLike[str], run: QueueRun,
                        failed: QueueEntry) -> None:
    reason = _stop_reason(failed)
    for entry in run.entries[failed.index + 1:]:
        if entry.item.sid == failed.item.sid and entry.state in (
                'pending', 'blocked', 'unknown'):
            _append_state(base, run.queue_id, entry.index, 'not_run', note=reason)


def _run_lane(base: str | os.PathLike[str], queue_id: str, sid: str, *,
              spawn: jobs.Spawn, queue_registry: QueueRegistry,
              job_registry: jobs.JobRegistry) -> None:
    # A successful fake/test subprocess does not necessarily materialize an
    # artifact. Rechecking the item again in the same runner turn would launch
    # it forever. Disk truth is rechecked when a *new* resume turn starts; this
    # set only remembers what this turn itself has just settled.
    settled_this_turn: set[int] = set()
    while True:
        run = _one(base, queue_id, registry=queue_registry)
        lane = [entry for entry in run.entries if entry.item.sid == sid]
        progressed = False
        for entry in lane:
            if entry.state == 'done' and entry.index in settled_this_turn:
                continue
            if (entry.state == 'blocked'
                    and entry.waiting_on == WAITING_ON_RESTART_CONFIRMATION):
                # A service restart expires the old spending authorization.
                # Only _confirm_resume may remove this durable marker; treating
                # it like an ordinary live gate would silently turn it back into
                # pending and execute without a fresh --yes/confirmed=true.
                return
            if entry.state == 'blocked' and entry.waiting_on == WAITING_ON_REPLAN:
                # This is not a human gate that can simply become true.  The
                # frozen queue lacks (or disagrees with) the current command;
                # queue-resume must never turn that into permission to execute
                # an obsolete snapshot.
                return
            if entry.state in ('landed_red', 'failed', 'cancelled', 'not_run'):
                return
            if entry.state in ('running', 'unknown'):
                recovered = _recover_running(
                    base, run, entry, job_registry=job_registry)
                if recovered in ('running', 'blocked', 'landed_red', 'failed'):
                    if recovered in ('landed_red', 'failed'):
                        recovered_run = _one(base, queue_id, registry=queue_registry)
                        _mark_not_run_after(
                            base, recovered_run, recovered_run.entries[entry.index])
                    return
                if recovered == 'done':
                    settled_this_turn.add(entry.index)
                progressed = True
                break

            disposition, waiting_on, reason = _live_disposition(base, run, entry)
            if disposition == 'done':
                if entry.state != 'done':
                    _append_state(base, queue_id, entry.index, 'done', note=reason)
                    settled_this_turn.add(entry.index)
                    progressed = True
                    break
                settled_this_turn.add(entry.index)
                continue
            if disposition == 'blocked':
                if entry.state != 'blocked' or entry.waiting_on != waiting_on:
                    _append_state(
                        base, queue_id, entry.index, 'blocked',
                        waiting_on=waiting_on, note=reason)
                return
            if entry.state == 'done':
                _append_state(
                    base, queue_id, entry.index, 'pending',
                    note='盘面不再完整/新鲜，按原已确认命令恢复执行')
                progressed = True
                break

            job_id = jobs.new_job_id()
            # 批次 3：恢复续跑 = 新 job_id（execution）+ 原 run_id（业务 run）。
            prior_run_id, prior_job_id = _prior_execution_identity(
                base, queue_id, entry.index)
            with _STATE_LOCK:
                fresh = _one(base, queue_id, registry=queue_registry).entries[entry.index]
                if fresh.state not in ('pending', 'blocked'):
                    progressed = True
                    break
                _append_state(
                    base, queue_id, entry.index, 'running', job_id=job_id,
                    note=reason)
            try:
                prepare = None
                if entry.item.node == 'stage4.design' and not entry.item.flags:
                    prepare = lambda: approvals.authorize_design_initial(
                        base, entry.item.sid)
                elif entry.item.node == 'stage4.plan':
                    prepare = lambda: approvals.authorize_plan(
                        base, entry.item.sid)
                elif entry.item.node == 'stage4.context':
                    prepare = lambda: approvals.authorize_context(
                        base, entry.item.sid)
                elif entry.item.node == 'stage5.write':
                    chapters = chapters_from_flags(entry.item.flags)
                    prepare = lambda chapters=chapters: approvals.authorize_writing(
                        base, entry.item.sid, chapters)
                jobs.start(
                    base, entry.item.node, entry.item.sid, entry.item.flags,
                    steps=entry.item.steps,
                    spawn=spawn, registry=job_registry, job_id=job_id,
                    run_id=prior_run_id,
                    recovered_from_job_id=prior_job_id,
                    prepare=prepare)
                jobs.wait(job_id)
                result = _job_record(base, job_id, job_registry)
                if result is None or result.state not in ('ok', 'landed_red', 'failed'):
                    _append_state(
                        base, queue_id, entry.index, 'unknown', job_id=job_id,
                        note='作业线程结束后仍没有可折叠终态')
                    return
                state = {'ok': 'done', 'landed_red': 'landed_red',
                         'failed': 'failed'}[result.state]
                _append_state(
                    base, queue_id, entry.index, state, job_id=job_id,
                    exit_code=result.exit_code,
                    note=(jobs.LANDED_RED_NOTE
                          if result.state == 'landed_red' else None))
            except jobs.JobConflict as exc:
                _append_state(
                    base, queue_id, entry.index, 'blocked',
                    waiting_on=f'job:{entry.item.sid}', note=str(exc))
                return
            except BaseException as exc:  # worker crash is a queue failure, never silent
                _append_state(
                    base, queue_id, entry.index, 'failed', job_id=job_id,
                    exit_code=CRASH, note=f'runner 启动作业失败：{exc!r}')
            current = _one(base, queue_id, registry=queue_registry)
            ended = current.entries[entry.index]
            if ended.state in ('landed_red', 'failed'):
                _mark_not_run_after(base, current, ended)
                return
            if ended.state == 'done':
                settled_this_turn.add(entry.index)
            progressed = True
            break
        if not progressed:
            return


def _execute(base: str | os.PathLike[str], queue_id: str, *,
             spawn: jobs.Spawn, queue_registry: QueueRegistry,
             job_registry: jobs.JobRegistry,
             lane_sids: Sequence[str] | None = None) -> QueueRun:
    run = _one(base, queue_id, registry=queue_registry)
    selected = list(run.start.sids if lane_sids is None else lane_sids)
    if len(selected) != len(set(selected)) or any(
            sid not in run.start.sids for sid in selected):
        raise QueueConflict('queue startup lane_sids 必须是冻结队列 sid 的无重复子集')
    lane_threads = [threading.Thread(
        target=_run_lane,
        kwargs={
            'base': base, 'queue_id': queue_id, 'sid': sid, 'spawn': spawn,
            'queue_registry': queue_registry, 'job_registry': job_registry,
        },
        name=f'novel-queue-{queue_id}-{sid}', daemon=True,
    ) for sid in selected]
    for thread in lane_threads:
        thread.start()
    for thread in lane_threads:
        thread.join()
    return _one(base, queue_id, registry=queue_registry)


def run(base: str | os.PathLike[str], queue_id: str, *,
        spawn: jobs.Spawn = jobs.default_spawn,
        registry: QueueRegistry = REGISTRY,
        job_registry: jobs.JobRegistry = jobs.REGISTRY) -> QueueRun:
    """Synchronously consume each novel lane; different novels run in parallel."""
    registry.acquire(queue_id)
    try:
        return _execute(
            base, queue_id, spawn=spawn, queue_registry=registry,
            job_registry=job_registry)
    finally:
        registry.release(queue_id)


def resume(base: str | os.PathLike[str], queue_id: str, *, yes: bool,
           spawn: jobs.Spawn = jobs.default_spawn,
           registry: QueueRegistry = REGISTRY,
           job_registry: jobs.JobRegistry = jobs.REGISTRY) -> QueueRun:
    """Synchronously resume only after a fresh paid-command confirmation."""
    _confirm_resume(base, queue_id, yes=yes, registry=registry)
    return run(
        base, queue_id, spawn=spawn, registry=registry,
        job_registry=job_registry)


def start(base: str | os.PathLike[str], queue_id: str, *,
          spawn: jobs.Spawn = jobs.default_spawn,
          registry: QueueRegistry = REGISTRY,
          job_registry: jobs.JobRegistry = jobs.REGISTRY,
          _lane_sids: Sequence[str] | None = None) -> QueueRun:
    """Start a persistent queue runner in the background and return immediately."""
    registry.acquire(queue_id)

    def worker() -> None:
        try:
            _execute(
                base, queue_id, spawn=spawn, queue_registry=registry,
                job_registry=job_registry, lane_sids=_lane_sids)
        except BaseException:
            # Keep the traceback in the service log. Item-level failures are
            # recorded inside _run_lane; this guard prevents a silent thread death.
            traceback.print_exc()
        finally:
            registry.release(queue_id)
            with _THREADS_LOCK:
                _THREADS.pop(queue_id, None)

    thread = threading.Thread(
        target=worker, name=f'novel-queue-{queue_id}', daemon=True)
    with _THREADS_LOCK:
        _THREADS[queue_id] = thread
    try:
        thread.start()
    except Exception:
        with _THREADS_LOCK:
            _THREADS.pop(queue_id, None)
        registry.release(queue_id)
        raise
    return _one(base, queue_id, registry=registry)


def resume_start(base: str | os.PathLike[str], queue_id: str, *, yes: bool,
                 spawn: jobs.Spawn = jobs.default_spawn,
                 registry: QueueRegistry = REGISTRY,
                 job_registry: jobs.JobRegistry = jobs.REGISTRY) -> QueueRun:
    """Asynchronously resume after the same confirmation used by the CLI."""
    _confirm_resume(base, queue_id, yes=yes, registry=registry)
    return start(
        base, queue_id, spawn=spawn, registry=registry,
        job_registry=job_registry)


def wait(queue_id: str, timeout: float | None = None) -> bool:
    with _THREADS_LOCK:
        thread = _THREADS.get(queue_id)
    if thread is None:
        return True
    thread.join(timeout=timeout)
    return not thread.is_alive()


def resume_incomplete(base: str | os.PathLike[str], *,
                      spawn: jobs.Spawn = jobs.default_spawn,
                      registry: QueueRegistry = REGISTRY,
                      job_registry: jobs.JobRegistry = jobs.REGISTRY) -> list[str]:
    """Service-start hook with a fresh disk/command/cost safety audit.

    An old ``--yes`` authorizes the old plan once.  A service restart is a new
    execution turn: fresh free commands with an unchanged snapshot may resume,
    while any remaining paid command waits for an explicit ``queue-resume``.
    A changed command surface is never substituted into the frozen queue.
    """
    before = records(base, registry=registry)
    if not before:
        return []
    from novel.orchestrator import admission

    unresolved_sids = frozenset(
        current.sid for current in admission.unresolved(base))
    jobs.reconcile(
        base, registry=job_registry, skip_sids=unresolved_sids)
    resumed: list[str] = []
    for record in records(base, registry=registry):
        if record.state in ('ok', 'landed_red', 'failed', 'cancelled'):
            continue
        eligible_sids = tuple(
            sid for sid in record.start.sids if sid not in unresolved_sids)
        if not eligible_sids:
            # durable gate 未闭合时整条 lane 保持原样：不做 artifact 判定、
            # 不追加 queue 状态、不启动。typed reconcile 后由人显式 resume。
            continue
        eligible = frozenset(eligible_sids)

        recovered_any = False
        for entry in record.entries:
            if entry.item.sid not in eligible:
                continue
            if entry.state not in ('running', 'unknown'):
                continue
            recovered = _recover_running(
                base, record, entry, job_registry=job_registry)
            recovered_any = True
            if recovered in ('landed_red', 'failed'):
                current = _one(base, record.queue_id, registry=registry)
                _mark_not_run_after(base, current, current.entries[entry.index])

        # Re-evaluate every non-terminal/done entry against current disk truth.
        # A previously done free item may safely become pending again; a changed
        # command is turned into an explicit replan block by _live_disposition.
        current = _one(base, record.queue_id, registry=registry)
        if current.state in ('landed_red', 'failed', 'cancelled'):
            continue
        for entry in current.entries:
            if entry.item.sid not in eligible:
                continue
            if entry.state not in ('pending', 'blocked', 'done'):
                continue
            disposition, waiting_on, reason = _live_disposition(base, current, entry)
            if disposition == entry.state and (
                    disposition != 'blocked' or waiting_on == entry.waiting_on):
                continue
            _append_state(
                base, current.queue_id, entry.index, disposition,
                waiting_on=waiting_on, note=reason)
            recovered_any = True

        current = _one(base, record.queue_id, registry=registry)
        if len(eligible_sids) == len(current.start.sids):
            current_items = _current_plan_items(base, current.start)
        else:
            current_items = _current_plan_items_for_sids(
                base, current.start, eligible_sids)
        remaining = [entry.item for entry in current.entries
                     if entry.item.sid in eligible
                     and entry.state in ('pending', 'blocked', 'unknown', 'running')]
        if ([_command_surface(item) for item in remaining]
                != [_command_surface(item) for item in current_items]):
            targets = [entry for entry in current.entries
                       if entry.item.sid in eligible
                       and entry.state in ('pending', 'blocked', 'unknown', 'running')]
            if not targets and current.entries:
                targets = [entry for entry in reversed(current.entries)
                           if entry.item.sid in eligible][:1]
            reason = (
                '服务重启后重新规划得到的节点/命令集合与创建时未完成快照不同；'
                '旧队列不会增加、删除或替换命令，请重新预览并创建队列')
            for entry in targets:
                _append_state(
                    base, current.queue_id, entry.index, 'blocked',
                    waiting_on=WAITING_ON_REPLAN, note=reason)
            continue

        pending = [entry for entry in current.entries
                   if entry.item.sid in eligible and entry.state == 'pending']
        paid_pending = [entry for entry in pending
                        if any(step.paid for step in entry.item.steps)]
        if paid_pending:
            # 批次 3（R-13.3 修订，2026-08-29 用户裁定）：durable queue 父记录
            # 且身份完全闭合的同一 run，原付费授权可跨重启自动续跑（新
            # execution、原 run_id、recovered_from_job_id 联结）。任一前提缺失
            # 或漂移 → 仍落持久 restart-confirmation，等显式确认，不自动猜。
            blocked_any = False
            for entry in paid_pending:
                reason = _auto_resume_block_reason(base, current, entry)
                if reason is None:
                    continue
                blocked_any = True
                _append_state(
                    base, current.queue_id, entry.index, 'blocked',
                    waiting_on=WAITING_ON_RESTART_CONFIRMATION,
                    note=('服务重启后不会自动续跑：' + reason
                          + '；当前命令快照未变，重新展示冻结命令并经 CLI '
                            '`queue-resume --yes` 或 API `confirmed=true` 确认后'
                            '才能恢复'))
            if blocked_any:
                continue

        current = _one(base, record.queue_id, registry=registry)
        if any(entry.state in ('blocked', 'unknown', 'running')
               for entry in current.entries if entry.item.sid in eligible):
            continue
        if any(entry.state == 'pending' for entry in current.entries
               if entry.item.sid in eligible):
            start(
                base, current.queue_id, spawn=spawn, registry=registry,
                job_registry=job_registry, _lane_sids=eligible_sids)
            resumed.append(current.queue_id)
        elif recovered_any and current.state == 'ok':
            resumed.append(current.queue_id)
    return resumed


__all__ = [
    'HUMAN_NODES', 'OPTIONAL_HUMAN_NODES', 'WAITING_ON_INBOX',
    'WAITING_ON_STAGE2_REVIEW', 'WAITING_ON_STAGE2_EDIT_CORRECTION',
    'STAGE2_REVIEWER_WAITING_ON',
    'WAITING_ON_STAGE4_DESIGN_REVIEW',
    'WAITING_ON_STAGE4_BINDING_RESOLUTION',
    'WAITING_ON_STAGE4_STRUCTURE_REPLAN',
    'WAITING_ON_STAGE4_DESIGN_ACTION',
    'WAITING_ON_STAGE4_STRUCTURE_REVIEW',
    'WAITING_ON_STAGE4_PHASE_APPROVAL', 'WAITING_ON_STAGE4_REPLAN',
    'WAITING_ON_WRITING_APPROVAL', 'WAITING_ON_REVIEW', 'WAITING_ON_LEGACY',
    'WAITING_ON_RESUME_DECISION', 'WAITING_ON_RESTART_CONFIRMATION', 'WAITING_ON_REPLAN',
    'DETERMINISTIC_RECOVERY_WAITING_ON',
    'QueueRegistry', 'append_event', 'cancel', 'confirm', 'create', 'fold',
    'fold_counts', 'frozen_resume_commands', 'new_queue_id', 'nodes_up_to', 'plan', 'plan_book',
    'queue_path', 'read_events', 'records', 'resume_incomplete', 'run', 'start',
    'resume', 'resume_start', 'wait',
]
