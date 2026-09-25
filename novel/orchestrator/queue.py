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

from contracts.artifacts import (
    STAGE2_GLOBAL_EDIT,
)

import json
import os
import threading
import time
import traceback
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from contracts.invariants import (
    LEGACY_STAGE5_MODES, NODES, RETIRED_NODES, RETIRED_STAGES, STAGES,
    SYSTEM_INVALID_REVALIDATE_ACTOR, SYSTEM_INVALID_REVALIDATE_REASON,
)
from contracts.schema.api import ApprovalDoc
from contracts.schema.orchestration import (
    NovelOverview, QueueCounts, QueueEntry, QueueEvent, QueueItem,
    QueueLaneCounts, QueuePlan, QueueRun, QueueStart,
)
# ⚠ F8A：`contracts.schema.stage6` 的 import 随 Stage6 整层退役删除（F6d 登记）。
from contracts.artifacts import STAGE4_BLIND_CANDIDATE
from novel import paths, prov
from novel.appendlog import append_line
from novel import exit_codes as X
from novel.exit_codes import CRASH
from novel.errors import (
    ApprovalRequired, ArtifactUnreadable, BlockingFindingsUnresolved,
    GateBlocked, QueueConflict, TerminalState, UpstreamMissing, UpstreamStale,
    UpstreamUnproven,
)
from novel.orchestrator import approvals, jobs, state as st
from novel.orchestrator.nodes import build_steps, flag_value, shell_line
from novel.stages.stage4.phase_state import current_infeasible_binding
from novel.store import ArtifactStore

#: 人做的那几个动作。**队列永不自动排它们**——「人看过之后的裁决」机器替不了。
#: ⚠ 它们进 `blocked` 而不是被跳过：被跳过的闸门在界面上与「不需要它」长得一样。
#: ⚠ `stage6.approve` 不算人工闸（2026-09-15 全自动裁决）：默认槽位序自动定稿是
#:   队列的普通 todo。`stage0.approve` / `stage0.edit` / `stage7.approve` 与
#:   prepare 接受闸均已退役（批次 B1/B4）；现役 ``HUMAN_NODES`` 为空。
HUMAN_NODES: frozenset[str] = frozenset()

#: 待人工清单非空时挡队列的那个理由（`INBOX_KIND` 的 `invalid`）。
WAITING_ON_INBOX = 'inbox:invalid'
# ⚠ Stage2 语义审阅退役：``approval:stage2-*`` 阻断与 reviewed-edit 纠正/终态
# 恢复随之删除。保留的 EDIT_COMMIT/EDIT_REVALIDATE 是普通 typed 编辑与
# INVALID 提升的 0-wire WAL 补尾（无审阅语义）。
WAITING_ON_STAGE2_EDIT_COMMIT = 'recovery:stage2-global-edit-commit'
WAITING_ON_STAGE2_EDIT_REVALIDATE = (
    'recovery:stage2-global-edit-revalidate')
#: 批次 C：live `.INVALID` 的 0-wire 自动提升。常量进入
#: ``DETERMINISTIC_RECOVERY_WAITING_ON``，由 Supervisor 铸 recovery permit
#: 后交给 Executor；不再等人填 actor/reason，也不把提升做成普通 ready todo
#: （避免无书目录时 ``build_steps`` 误伤规划）。
WAITING_ON_INVALID_REVALIDATE = 'recovery:invalid-revalidate'
WAITING_ON_ANALYZE_RESPONSE_REPAIR = 'human:stage2-analyze-response-repair'
WAITING_ON_ANALYZE_RESPONSE_TERMINAL = 'human:stage2-analyze-response-terminal'
WAITING_ON_ANALYZE_SOURCE = 'human:stage2-analyze-source-unavailable'
WAITING_ON_ANALYZE_DRAFT_REPAIR = 'human:stage2-analyze-owner-draft-repair'
# ⚠⚠ Stage4 四道人工闸已整体退役（2026-09-15 全自动裁决）：design 落盘
# （机械有效）即自动进入 plan，plan 落盘即自动进入 context，唯一打开条件是
# 统一机械准入（`novel/orchestrator/mechanical_admission.py`）。下列常量不再
# 由本模块作为 blocker 发出；仅保留名字供历史账只读解码
# （`novel/orchestrator/reviewer_legacy.py`）引用。
WAITING_ON_STAGE4_DESIGN_REVIEW = 'approval:stage4-design-review'
WAITING_ON_STAGE4_BINDING_RESOLUTION = 'approval:stage4-binding-resolution'
WAITING_ON_STAGE4_STRUCTURE_REPLAN = 'approval:stage4-structure-replan'
WAITING_ON_STAGE4_STRUCTURE_REVIEW = 'approval:stage4-structure-review'
# 兼容只 import 常量名的旧调用者；同一退役裁决下的只读别名。
WAITING_ON_STAGE4_PHASE_APPROVAL = WAITING_ON_STAGE4_DESIGN_REVIEW
WAITING_ON_STAGE4_REPLAN = WAITING_ON_STAGE4_BINDING_RESOLUTION
# 仍是现役免费恢复面：repair/replan prepared 尾窗的 0-wire 补尾
# （`stage4.design --recover-repair-aggregate`）。
WAITING_ON_STAGE4_DESIGN_REPAIR_COMMIT = \
    'recovery:stage4-design-repair-aggregate-commit'
# ⚠⚠ Stage5 两道人工闸已随批次 B3 退役（2026-09-15 全自动裁决）：
#   `approval:stage5-writing`（写作授权）并入 run 启动授权（上传即授权本书
#   全部付费节点），队列不再为 stage5.write 索取任何授权 blocker；
#   `approval:stage5-closure`（闭合回执）由 Review 机械判定自动闭合
#   （producer 以 `system:stage5-auto` 落账）。常量仅为历史账只读解码保留，
#   见 `_blocked_why` 与 `reviewer_legacy` 的历史投影。
WAITING_ON_WRITING_APPROVAL = 'approval:stage5-writing'
WAITING_ON_REVIEW = 'stage5:review-currentness'
WAITING_ON_STAGE5_CLOSURE = 'approval:stage5-closure'
# ⚠⚠ stage6.prepare 接受闸已随批次 B4 退役：机械校验通过即
#   `system:stage6-prepare-auto` 自动 accept。常量 ``WAITING_ON_STAGE6_PREPARE``
#   只读保留供历史账解码，现役队列不再发出。
WAITING_ON_STAGE6_PREPARE = 'approval:stage6-prepare'
WAITING_ON_STAGE6_PREPARE_COMMIT = 'recovery:stage6-prepare-edit'
WAITING_ON_STAGE5_REVIEWER_REWRITE = 'recovery:stage5-reviewer-rewrite'
WAITING_ON_PREPARED_DELIVERY = 'recovery:prepared-delivery'
WAITING_ON_PREPARED_DELIVERY_UNKNOWN = 'human:prepared-unknown'
WAITING_ON_LEGACY = 'legacy_incompatible'
WAITING_ON_RESUME_DECISION = 'resume:manual-decision'
WAITING_ON_RESTART_CONFIRMATION = 'queue:restart-confirmation'
WAITING_ON_REPLAN = 'queue:plan-changed'

#: 已有 typed 人审决定、只差按它执行的 blocker。它不是新的 Commander 任务：
#: Supervisor 必须现场铸 recovery permit，再把 exact node/flags 交给 Executor。
#: 独立成闭集，避免任何普通人工 blocker 因“碰巧带 steps”被自动执行。
DETERMINISTIC_RECOVERY_WAITING_ON: frozenset[str] = frozenset({
    WAITING_ON_STAGE2_EDIT_COMMIT,
    WAITING_ON_STAGE2_EDIT_REVALIDATE,
    WAITING_ON_INVALID_REVALIDATE,
    WAITING_ON_STAGE4_DESIGN_REPAIR_COMMIT,
    WAITING_ON_STAGE5_REVIEWER_REWRITE,
    WAITING_ON_PREPARED_DELIVERY,
    WAITING_ON_STAGE6_PREPARE_COMMIT,
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


@dataclass(frozen=True)
class AnalyzeInitialRecoverySurface:
    """原 Analyze 待恢复来源的只读坐标；不解析修复候选为通过，也不补任何账。"""
    waiting_on: str | None
    why: str
    source_paths: tuple[Path, ...]


def analyze_initial_recovery_surface(base, sid: str, source_run: str, *, code: str | None = None
                                     ) -> AnalyzeInitialRecoverySurface:
    from novel import work_units
    from novel.llm import run_journal as agent_runs, response_repair
    from novel.llm.ledger import ledger_path
    from novel.stages.stage2 import analyze
    book = paths.resolve_novel_dir(base, sid)
    current_code = code if code is not None else prov.code_version()
    current = st.readonly_ctx(book, analyze.NODE, current_code)
    historical, _record, run_path = agent_runs._historical_run_context(
        current, analyze.run_spec(), source_run_id=source_run, ledger_source_path=ledger_path(base))
    source_paths = [run_path]
    budget = work_units.budget_for(current, scope=work_units.executor_scope(
        analyze.NODE, analyze.NODE, {'chapter': None}))
    source_paths.append(budget.path)
    last = budget.latest_turn()
    if last is not None:
        if last.run_id != source_run:
            raise ArtifactUnreadable('原 Analyze owner 与工作单元最后内容轮不一致')
        source_paths.extend(budget.directory / f'{digest}.json' for digest in (
            last.request_payload_sha256, last.response_sha256, last.recovery_sha256) if digest is not None)
    checkpoint_dir = analyze.run_spec().checkpoint_dir(historical)
    # 已形成 checkpoint/prepared 的恢复仍由原 producer 验证；调度不重建同一候选。
    has_checkpoint = checkpoint_dir.is_dir() and any(checkpoint_dir.glob('rev-*.json'))
    waiting_on = None
    why = '复验原联合回答并补齐首次冻结提交（0 模型；必须使用原代码）'
    if historical.code != current_code:
        waiting_on = WAITING_ON_ANALYZE_SOURCE
        why = '原 Analyze 代码与当前不同；用冻结原代码排空旧 owner，不能跨代码恢复或重新首抽'
    elif not has_checkpoint and (last is None or last.response_sha256 is None):
        waiting_on = WAITING_ON_ANALYZE_SOURCE
        why = '原 Analyze 尚无可核验的完整响应或 checkpoint；保留原 owner，核对原执行状态，不能重新购买'
    if last is not None and last.recovery_sha256 is not None:
        custody = budget.recovery_proof(last.turn_id)
        acceptance = custody.get('acceptance')
        if custody.get('kind') != 'executor-response-custody-v1' or not isinstance(acceptance, dict):
            raise ArtifactUnreadable('原 Analyze 完整响应缺运输 custody')
        identity = agent_runs.response_repair_identity(historical, analyze.run_spec(), acceptance)
        chain = agent_runs.canonical_sha256(identity)
        directory = paths.rejected_response_path(book, chain, str(acceptance['attempt_id']))
        source_paths.extend(directory / name for name in (
            response_repair.REJECTION_FILE, response_repair.ORIGINAL_FILE,
            response_repair.RESOLUTION_FILE, response_repair.OUTCOME_FILE))
        records = response_repair.records_for_chain(book, chain)
        if len(records) > 1:
            raise ArtifactUnreadable('原 Analyze 响应修复链有多个候选，不能猜测调度')
        if records:
            record = records[0]
            if (record.root != directory
                    or record.rejection.get('agent_response_repair_identity') != identity
                    or record.rejection.get('run_id') != source_run):
                raise ArtifactUnreadable('原 Analyze 响应修复记录与原 owner 不一致')
            if record.resolution is None:
                source_paths.append(directory / response_repair.CANDIDATE_FILE)
                waiting_on = WAITING_ON_ANALYZE_RESPONSE_REPAIR
                why = (f'原 Analyze 响应待免费修复：attempt={record.attempt_id}；编辑隔离 candidate.json '
                       '并以 response-repair 封存决定后，再恢复同一原 run')
            elif (record.resolution.get('action') == 'decline'
                    or (record.outcome is not None and record.outcome.get('decision') in {'rejected', 'declined'})):
                waiting_on = WAITING_ON_ANALYZE_RESPONSE_TERMINAL
                why = '原 Analyze 响应修复已放弃或复验失败；唯一修复额度已用，保留原响应与 owner，禁止自动重买'
            else:
                # 提交只证明候选封存；真实 schema/HARD 仍由免费 producer 重验。
                agent_runs.response_repair_binding(historical, analyze.run_spec(), acceptance, record)
                if historical.code == current_code:
                    waiting_on = None
                    why = '原 Analyze 响应修复决定已封存；免费复验同一原 schema 并补原 run 提交，不新增内容轮'
    return AnalyzeInitialRecoverySurface(waiting_on, why, tuple(sorted(set(source_paths))))


def stage2_core_recovery_item(base, sid: str, *, code: str | None = None) -> QueueItem | None:
    """先完成已保存的恢复事实；部分提交不能被重新抽取遮蔽。"""
    from novel.stages.stage2 import analyze
    from novel.stages.stage2.analyze import (
        draft_recovery as analyze_draft_recovery,
        recovery as analyze_recovery,
    )
    book = paths.find_novel_dir(base, sid)
    if book is None:
        return None
    source_run = analyze.pending_initial_run(book)
    if source_run is not None:
        flags = ['--recover-analyze-run', source_run]
        surface = analyze_initial_recovery_surface(base, sid, source_run, code=code)
        return QueueItem(sid=sid, node='stage2.analyze', flags=flags,
            steps=[] if surface.waiting_on else build_steps('stage2.analyze', sid, flags, base=base),
            waiting_on=surface.waiting_on, why=surface.why)
    drafts = analyze_draft_recovery.status(book, code=code)
    prepared = [row for row in drafts if row['state'] == 'prepared']
    if prepared:
        # 独立 owner WAL 在正式文件存在后也必须先补尾；不能启动另一侧普通写。
        owner = prepared[0]['owner']
        flags = ['--recover-analyze-draft']
        return QueueItem(sid=sid, node=owner, flags=flags,
            steps=build_steps(owner, sid, flags, base=base),
            why='按原 owner 草稿订正决定补齐冻结提交（0 模型）')
    if analyze_recovery.pending(base, sid, code=code) is not None:
        issue = analyze_recovery.pending_compilation_issue(base, sid, code=code)
        if issue is not None:
            return QueueItem(sid=sid, node='stage2.global', flags=['--emit-analyze-draft-template'],
                steps=[], waiting_on=WAITING_ON_ANALYZE_DRAFT_REPAIR, why=issue)
        flags = ['--recover-analyze']
        return QueueItem(sid=sid, node='stage2.global', flags=flags,
            steps=build_steps('stage2.global', sid, flags, base=base),
            why='人物已修好，从原联合回答免费编译 GlobalDraft（0 模型）')
    invalid_drafts = [row for row in drafts if row['state'] == 'draft_invalid']
    if invalid_drafts:
        owner = invalid_drafts[0]['owner']
        return QueueItem(sid=sid, node=owner, flags=['--emit-analyze-draft-template'], steps=[],
            waiting_on=WAITING_ON_ANALYZE_DRAFT_REPAIR,
            why=(f'原联合回答的 {owner} 草稿尚不能编译；使用 --emit-analyze-draft-template '
                 '查看原候选与冻结身份，订正后 --repair-analyze-draft 提交；不重新购买联合首抽'))
    return None


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

    批次 B4 后 ``HUMAN_NODES`` 为空；本函数仅保留给历史调用方，恒 False。
    """
    return False


def _stage4_replan_required(base: str | os.PathLike[str], sid: str) -> bool:
    """B 已对当前 A 落下 fresh typed infeasible 终态时，唯一后继是 A 重采。

    2026-09-15 全自动裁决后这不再是人工闸：队列直接把预算内
    `stage4.design --replan` 排成普通 todo；预算耗尽由工作单元
    WorkUnitBudgetExhausted 给出终态。
    """
    novel_dir = paths.resolve_novel_dir(base, sid)
    store = ArtifactStore(st.readonly_ctx(
        novel_dir, 'stage4.plan', approvals.projection_code()))
    return current_infeasible_binding(store) is not None


def prepared_delivery_recovery_item(
    base: str | os.PathLike[str], sid: str, *, code: str, until_stage: str = 'stage7',
) -> QueueItem | None:
    """producer 严格检查后才发布免费补尾；此查询不补账、不因文件存在发命令。

    ⚠ F8b-3（S-05 deprecation）：旧链尾窗恢复面随旧链退役——它的两个 producer
    （stage4 design/plan prepared recovery）只能由旧链节点写出，而生产入口已
    拒识旧节点，队列可规划的书不可能再产生新的未闭提交组。新链的恢复是候选
    runner 自身续跑（F7c 精确补尾），不再经队列发布补尾项。本函数停用为恒
    None，随 F8c 与旧链整层删除。
    """
    return None


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
    if STAGES.index(until_stage) >= STAGES.index('stage2'):
        core_recovery = stage2_core_recovery_item(base, sid, code=approvals.projection_code())
        if core_recovery is not None:
            return ([], [core_recovery]) if core_recovery.waiting_on else ([core_recovery], [])
    if STAGES.index(until_stage) >= STAGES.index('stage4'):
        recovery = prepared_delivery_recovery_item(base, sid, code=approvals.projection_code(), until_stage=until_stage)
        if recovery is not None:
            # 先补原提交，不能让 fresh formal/已有 semantic 序号吞掉采纳尾窗，
            # 更不能让后续 approve 改动原目标，致原证明永远无法补齐。
            return [], [recovery]
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
    #: 批次 B3：机械闭合回执待落账时本 lane 已排入 0 成本 review 补算项；
    #  stage6 在同一快照内必须等它完成后再重新规划。
    closure_relearn_planned = False
    planned_nodes = nodes_up_to(until_stage)
    if 'stage2.analyze' in planned_nodes:
        # Characters v3：冷启动永不优先联合 analyze；人物/知识走独立 producer。
        # pending analyze recovery 仍由上方 stage2_core_recovery_item 单独处理。
        planned_nodes.remove('stage2.analyze')
    reviewer_rewrite_execution = None
    closure_state = 'unreadable'
    if 'stage5.review' in planned_nodes:
        # Stage5 机械闭合视图一次读取（批次 B3）：review 节点的闭合补算
        # 消费同一份结论，不在 lane 内各自重判。
        if approval_doc is not None:
            closure_view = approval_doc.closure
        else:
            try:
                closure_view = approvals.stage5_closure_view(base, sid)
            except (
                ApprovalRequired, UpstreamMissing, ArtifactUnreadable,
                GateBlocked, UpstreamStale, UpstreamUnproven,
                BlockingFindingsUnresolved,
            ):
                closure_view = None
        closure_state = (
            'unreadable' if closure_view is None else closure_view.state)
    if 'stage5.write' in planned_nodes:
        # canonical request 是已完成 Commander 决定的唯一消费入口。它必须在
        # stage5.write 的普通 SKIP、旧 Review currentness 与写作授权之前读取；
        # 否则完整章集会把待执行 request 吞成“已经写完”，或旧 Review 会把
        # 精确恢复反向堵住。不存在 request 才返回 None；坏文件一律上抛。
        from novel.stages.stage5 import reviewer_rewrite

        novel_dir = paths.find_novel_dir(base, sid)
        # 尚未摄入的书没有 request；仍让冻结 overview 给出最早缺失节点。
        # 已有书的坏 request 继续由 producer 上抛，不能当成不存在。
        if novel_dir is not None:
            reviewer_rewrite_execution = (
                reviewer_rewrite.reviewer_rewrite_execution_state(
                    st.readonly_ctx(
                        novel_dir, 'stage5.write', approvals.projection_code())))
    planning_stage2_global = 'stage2.global' in planned_nodes
    naming_projection_blocked = (
        planning_stage2_global and st.stage2_global_projection_blocked(ov)
    )
    edit_commit_flags = (
        st.stage2_edit_commit_recovery_flags(base, sid)
        if planning_stage2_global else None
    )
    edit_commit_action = (
        'revalidate'
        if edit_commit_flags
        and edit_commit_flags[0] == '--revalidate'
        else 'apply_edit' if edit_commit_flags else None
    )
    edit_commit = edit_commit_action == 'apply_edit'
    edit_revalidate = bool(
        planning_stage2_global
        and edit_commit_action == 'revalidate'
    )

    for node in planned_nodes:
        spec = NODES[node]
        blueprint_repair_reason = None
        stage4_design_replan_flags: tuple[str, ...] | None = None
        stage5_reviewer_rewrite_flags: tuple[str, ...] | None = None
        stage5_reviewer_rewrite_no_action = False
        prepare_auto_accept = False
        invalid_auto_revalidate = False
        stage2_rework = None
        projection_recovery = (
            node == 'stage2.characters'
            and st.stage2_projection_recovery_needed(ov)
        )
        knowledge_rebind = (
            node == 'stage2.global'
            and st.stage2_knowledge_rebind_needed(base, sid, ov)
        )
        projection_blocked = (
            node == 'stage2.characters'
            and naming_projection_blocked
        )
        blueprint_invalid_recovery = (
            node == 'stage2.blueprint'
            and st.stage2_blueprint_invalid_recovery_needed(ov)
        )
        why_blocked = stuck
        if why_blocked is None and node in HUMAN_NODES:
            if not _gate_is_reachable(ov, spec.stage):
                continue                             # 还没轮到它，不是「在等人」
            if _human_gate_satisfied(base, sid, node):
                continue                             # 人已经完成这道闸
            why_blocked = node                       # 在等这一步的那个人
        if (why_blocked is None and node == 'stage2.global'
                and edit_commit):
            # prepared journal 优先于 partial 提交的表象；只让 Executor 重放
            # book canonical edit（0 wire）。
            why_blocked = WAITING_ON_STAGE2_EDIT_COMMIT
        if (why_blocked is None and node == 'stage2.global'
                and edit_revalidate):
            why_blocked = WAITING_ON_STAGE2_EDIT_REVALIDATE
        if (why_blocked is None
                and (projection_blocked or blueprint_invalid_recovery)):
            # 两种状态都已有可恢复的问题稿，且下一步需要人提供 typed 决策。
            # 队列不能替人造 actor/reason，更不能把 RUN 解释成默认再付一次钱。
            why_blocked = WAITING_ON_INBOX
        # Blueprint 与 Stage4 付费相由统一机械准入衔接：必要产物可读、当前、
        # 无未恢复 INVALID，且所需机械检查全部通过（语义未审只披露不阻断）。
        # 2026-09-15 全自动裁决后 stage4.plan 的打开条件同样是机械准入；
        # stage4.context（免费）在下方单独判同一族机械条件。
        # ⚠ 先于通用 upstream-invalid 分类：Stage2/Stage4 的 `.INVALID` 是明确的
        # 准入阻断，理由要精确到产物。
        if why_blocked is None and spec.paid and node in (
                'stage2.blueprint', 'stage4.design', 'stage4.plan'):
            from novel.orchestrator import mechanical_admission as admission
            gate = (admission.blueprint_generation_ready(base, sid)
                    if node == 'stage2.blueprint'
                    else admission.stage4_plan_ready(base, sid)
                    if node == 'stage4.plan'
                    else admission.stage4_ready(base, sid))
            if not gate.ok:
                why_blocked = WAITING_ON_INBOX
                blueprint_repair_reason = '统一机械准入未通过：' + '；'.join(gate.blocking)
            elif node == 'stage2.blueprint':
                from novel.stages.stage2.blueprint.diagnostics import read_repair_diagnostics
                try:
                    diagnostic = read_repair_diagnostics(base, sid, code=approvals.projection_code())
                except (ArtifactUnreadable, UpstreamMissing, UpstreamStale, UpstreamUnproven) as exc:
                    why_blocked = WAITING_ON_INBOX
                    blueprint_repair_reason = str(exc)
                else:
                    if diagnostic is not None and diagnostic['diagnostics']:
                        why_blocked = WAITING_ON_INBOX
                        blueprint_repair_reason = '; '.join(
                            row['detail'] + '。' + row['recovery'] for row in diagnostic['diagnostics'])

        if why_blocked is None:
            ghosts = _upstream_invalid(ov, spec.stage)
            if ghosts:
                why_blocked = WAITING_ON_INBOX

        verdict, why = st.resume_verdict(ov, node)
        if blueprint_repair_reason is not None:
            why = blueprint_repair_reason
        if projection_recovery and why_blocked is None:
            verdict, why = st.RESUME_RUN, '人物正式登记可用；免费重建称名投影'
        elif knowledge_rebind and why_blocked is None:
            verdict, why = st.RESUME_RUN, '仅人物独立登记改变；免费重验已有知识引用并重绑来源'
        deterministic_redo_flags: tuple[str, ...] | None = None
        if (why_blocked is None and node == 'stage5.write'
                and reviewer_rewrite_execution is not None):
            execution = reviewer_rewrite_execution
            if execution.state == 'ready_paid':
                stage5_reviewer_rewrite_flags = (
                    '--reviewer-rewrite', execution.request_rel)
                why_blocked = WAITING_ON_STAGE5_REVIEWER_REWRITE
            elif execution.state in {
                    'recoverable_checkpoint', 'recoverable_prepared'}:
                if execution.source_run_id is None:
                    raise ArtifactUnreadable(
                        'Reviewer rewrite 可恢复态缺 source run_id')
                recovery_flags = (
                    '--recover-reviewer-rewrite-prepared',
                    execution.source_run_id,
                    '--reviewer-rewrite-request-sha256',
                    execution.request_sha256,
                )
                if execution.state == 'recoverable_prepared':
                    if (execution.recovery_target_run_id is None
                            or execution.recovery_journal_sha256 is None):
                        raise ArtifactUnreadable(
                            'Reviewer rewrite prepared recovery 缺 exact target WAL identity')
                    recovery_flags += (
                        '--reviewer-rewrite-recovery-target-run-id',
                        execution.recovery_target_run_id,
                        '--reviewer-rewrite-recovery-journal-sha256',
                        execution.recovery_journal_sha256,
                    )
                stage5_reviewer_rewrite_flags = recovery_flags
                why_blocked = WAITING_ON_STAGE5_REVIEWER_REWRITE
            elif execution.state == 'expired_unstarted':
                stage5_reviewer_rewrite_flags = (
                    '--retire-reviewer-rewrite-request',
                    execution.request_rel,
                    '--reviewer-rewrite-request-sha256',
                    execution.request_sha256,
                )
                why_blocked = WAITING_ON_STAGE5_REVIEWER_REWRITE
            elif execution.state == 'in_flight_or_unknown':
                # 旧 owner/执行状态尚不能证明可重放；没有任何诚实 argv。
                # 仍保持在 rewrite 专属 waiter，且显式 steps=[]；不能退回 stale
                # Review、默认 writer 或新 Reviewer，也不能把空 flags 误作 paid argv。
                why_blocked = WAITING_ON_STAGE5_REVIEWER_REWRITE
                stage5_reviewer_rewrite_no_action = True
            elif execution.state in {'landed_invalid', 'landed_rejected'}:
                # request 已消费，绝不能再发同一 paid rewrite。现役 inbox 同时
                # 展示 .INVALID 与 rejected revision，并提供各自人工恢复面。
                why_blocked = WAITING_ON_INBOX
            elif execution.state == 'consumed_resolved':
                # request 已由 immutable terminal/expiry receipt 消费；它不再
                # 挡住正常 Stage5 currentness，下一格会按 stale Review 发布
                # 0 成本 stage5.review，而不是再次打开 Writer 或 Reviewer。
                pass
            elif execution.state != 'landed_formal':
                raise ArtifactUnreadable(
                    f'未知 Reviewer rewrite execution state：{execution.state!r}')
        if (why_blocked is None and node == 'stage4.design'
                and verdict == st.RESUME_SKIP
                and _stage4_replan_required(base, sid)):
            # B 的 typed infeasible 已是正式终态：2026-09-15 全自动裁决后不再
            # 等人工 resolution，唯一后继是预算内自动整书重采 A（普通付费
            # todo）。生产者按机器铸造的 typed scope 消费它；预算耗尽时该
            # 工作单元以 WorkUnitBudgetExhausted 终态失败。
            stage4_design_replan_flags = ('--replan',)
            verdict = st.RESUME_RUN
            why = ('Stage4B 已对当前候选落盘 typed infeasible；预算内自动整书'
                   '重采 Stage4A，预算耗尽即本工作单元 WorkUnitBudgetExhausted 终态')
        if (
            why_blocked is None
            and verdict == st.RESUME_RUN
            and st.live_invalid_artifacts(ov, node)
            and not projection_recovery
            and stage2_rework is None
        ):
            # ``resume_verdict`` 必须如实描述“默认重跑会发生什么”，但 queue 不能
            # 把这句警告继续冻结成一条付费 argv。批次 C：有 ``--revalidate`` 的
            # 节点由队列以固定 system 审计身份自动排 0-wire 提升；没有该入口的
            # 节点仍 blocked，绝不把“没实现恢复”解释成“同意再付一次钱”。
            if '--revalidate' in spec.flags:
                invalid_auto_revalidate = True
                why_blocked = WAITING_ON_INVALID_REVALIDATE
                verdict, why = st.RESUME_RUN, (
                    '本层有 live .INVALID——零成本 --revalidate（'
                    f'{SYSTEM_INVALID_REVALIDATE_ACTOR}）同轮重判并留不可变审计')
            else:
                why_blocked = WAITING_ON_INBOX
        # Stage4 人工闸已退役（2026-09-15 全自动裁决）：design 落盘（机械有效）
        # 即自动进入 plan（上方统一机械准入），plan 落盘（机械有效）即自动
        # 进入 context。context 的机械准入单独判，理由精确到产物。
        if why_blocked is None and node == 'stage4.context' \
                and verdict != st.RESUME_SKIP:
            from novel.orchestrator import mechanical_admission as admission
            gate = admission.stage4_context_ready(base, sid)
            if not gate.ok:
                why_blocked = WAITING_ON_INBOX
                why = '统一机械准入未通过：' + '；'.join(gate.blocking)
        # ⚠ F8A：Stage6 机械闭合闸与 prepare 接受闸两个 planning 块随 Stage6
        #   整层退役删除（F6d 登记）。Hook 的必要选择进 Stage4 正式 Design，
        #   成文在 Stage5 单产（`str06.stage5.hook`），没有定稿链也没有
        #   prepare 接受面。
        review_follows_planned_write = (
            node == 'stage5.review'
            and stage5_write_will_run
            and verdict == st.RESUME_SKIP)
        closure_relearn_needed = (
            node == 'stage5.review'
            and not stage5_write_will_run
            and verdict == st.RESUME_SKIP
            and closure_state in ('ready', 'expired'))
        if (why_blocked is None and verdict == st.RESUME_SKIP
                and not review_follows_planned_write
                and not closure_relearn_needed):
            continue                                 # 已存在即跳过
        if why_blocked is None and review_follows_planned_write:
            # 当前 Review 即使 fresh，前一项 writer 落盘后也必须按新主体
            # 重算。把它显式排入零成本 step；writer 若失败，队列的
            # “停在那里”仍会让这一项 not_run。
            verdict = st.RESUME_RUN
            why = 'stage5.write 已排入本次队列；落盘后必须按新 canonical 主体零成本重算 Review'
        if why_blocked is None and closure_relearn_needed:
            # 机械闭合回执（批次 B3）由 review producer 自动落账；工作包已可
            # 闭合而回执未落（ready/expired）时，唯一缺的就是一次 0 成本重算。
            verdict = st.RESUME_RUN
            closure_relearn_planned = True
            why = ('Stage5 机械闭合待落账：零成本重算 Review，producer 以 '
                   'system:stage5-auto 自动落机械闭合回执后再放行 Stage6')

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

        # 称名投影只由人物 producer 拥有，缺失时沿同一免费复验入口恢复；
        # 人物独立登记改变后，Global 沿免费 `--renormalize` 重验引用并重绑来源。
        flags: list[str] = (
            list(edit_commit_flags or ())
            if node == 'stage2.global' and edit_commit
            else list(edit_commit_flags or ())
            if node == 'stage2.global' and edit_revalidate
            else list(stage4_design_replan_flags)
            if stage4_design_replan_flags is not None
            else list(stage5_reviewer_rewrite_flags)
            if stage5_reviewer_rewrite_flags is not None
            else list(deterministic_redo_flags)
            if deterministic_redo_flags is not None
            else ['--revalidate', '--actor', 'system:projection-recovery',
                  '--reason', '从当前人物登记免费重建称名投影']
            if projection_recovery
            else ['--renormalize']
            if knowledge_rebind
            else ['--revalidate']
            if prepare_auto_accept
            else ['--revalidate', '--actor', SYSTEM_INVALID_REVALIDATE_ACTOR,
                  '--reason', SYSTEM_INVALID_REVALIDATE_REASON]
            if invalid_auto_revalidate
            else []
        )
        if node == 'stage4.plan' and why_blocked is None and not flags:
            from novel.stages.stage4 import plan_recovery
            book = paths.find_novel_dir(base, sid)
            if book is not None and (book / STAGE4_BLIND_CANDIDATE).is_file():
                if plan_recovery.reaggregate_available(
                        st.readonly_ctx(book, node, approvals.projection_code())):
                    flags = ['--reaggregate']
                    why = '当前所有章可机械确定或采用有效历史；免费聚合后进入结构审阅'
        if node == 'stage4.design' and why_blocked is None and not flags:
            from novel.stages.stage4 import design_response_recovery, plan_recovery
            book = paths.find_novel_dir(base, sid)
            if book is not None:
                recovery_ctx = st.readonly_ctx(book, node, approvals.projection_code())
                if design_response_recovery.available(recovery_ctx):
                    flags = ['--recover-responses']
                    why = '已保存原回答但 checkpoint 未完成；先免费恢复再计算缺失工作'
                elif plan_recovery.reaggregate_available(recovery_ctx):
                    flags = ['--reaggregate']
                    why = '当前策略与全部章节均有有效历史响应；免费聚合后进入设计审阅'
        steps = None
        if stage5_reviewer_rewrite_no_action:
            steps = []
        if (projection_blocked
                or blueprint_invalid_recovery
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

            # 本层两道闸按恢复成本排序（批次 B3：独立写作授权已并入 run 启动
            # 授权，队列不再索取）：先处理 0 LLM INVALID；普通续写再免费重跑
            # 旧 Review。若 operator 已证明上游 consumed-change 并导出精确重写
            # 章，旧 Review 绑定的是旧正文，不能反向卡住重建；新章落盘后下一
            # 轮自然会免费重算 Review。其余上游 stuck / legacy / REDO / UNKNOWN
            # 已在上面占住 blocker。
            if why_blocked is None and has_live_invalid and not invalid_auto_revalidate:
                # 通用 live-INVALID 分支未接手时（例如缺 --revalidate），仍 blocked。
                why_blocked = WAITING_ON_INBOX
            if why_blocked is None and review_blocks_paid_write:
                why_blocked = WAITING_ON_REVIEW
            if (stage5_reviewer_rewrite_flags is None
                    and ((has_live_invalid and not invalid_auto_revalidate)
                         or review_blocks_paid_write)):
                steps = []

        if why_blocked in (
            WAITING_ON_INBOX,
            WAITING_ON_STAGE5_CLOSURE,
            WAITING_ON_INVALID_REVALIDATE,
        ):
            # 人审/相位决定完成前，当前队列快照里不放下游付费 argv。状态改变后
            # 必须重新 plan，不能把旧快照重解释为新授权。
            # INVALID 自动提升的 exact argv 由 flags 携带，steps 留给 Supervisor
            # 铸 recovery permit 后再冻结——规划期不解析书目录。
            steps = []
        if (why_blocked == WAITING_ON_STAGE5_REVIEWER_REWRITE
                and node != 'stage5.write'):
            # 只有当前 writer 携 exact request/recovery argv；同一 blocker
            # 向下游传播时不得冻结任何后续付费命令。
            steps = []
        if (why_blocked == WAITING_ON_STAGE2_EDIT_COMMIT
                and node != 'stage2.global'):
            # 只有当前 prepared commit 本身携 canonical 0-wire argv；向下游传播
            # blocker 时绝不冻结任何后续付费命令。
            steps = []
        if (why_blocked == WAITING_ON_STAGE2_EDIT_REVALIDATE
                and node != 'stage2.global'):
            # revalidate 只属于当前 Stage2 Global；下游不得继承其 argv。
            steps = []

        if verdict == st.RESUME_LEGACY or why_blocked == WAITING_ON_RESUME_DECISION:
            steps = []

        if steps is None and why_blocked is not None and node in HUMAN_NODES:
            # ⚠⚠ **人还没签字之前，这道闸没有一条「诚实的可执行命令」可以快照。**
            # `stage7.approve` 的 argv 必填 `--actor` / `--reason`（人此刻还没给出的
            # 东西）⇒ `build_steps` 会抛 `UnsupportedRunMode`。而这一项本来就是
            # `blocked`，它要回答的是「在等谁」，不是「怎么跑」。
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
                 else _blocked_why(why_blocked, why)),
            waiting_on=why_blocked,
        )
        (blocked if why_blocked else todo).append(item)
        if node == 'stage5.write' and why_blocked is None:
            stage5_write_will_run = True
        if why_blocked and stuck is None:
            stuck = why_blocked
    return todo, blocked


def _blocked_why(
    waiting_on: str, verdict_why: str,
) -> str:
    if waiting_on == WAITING_ON_INVALID_REVALIDATE:
        return (
            '本层 live `.INVALID` 待 0-wire 自动提升：Supervisor 将以 '
            f'`{SYSTEM_INVALID_REVALIDATE_ACTOR}` 重放 `--revalidate`；'
            f'{verdict_why}')
    if waiting_on == WAITING_ON_INBOX:
        return (
            '上游或本层有被闸门拦下的稿：有 ``--revalidate`` 的节点由队列以 '
            f'`{SYSTEM_INVALID_REVALIDATE_ACTOR}` 自动 0-wire 提升；本项仍 blocked '
            '表示当前节点没有可自动排入的提升路或另有机械准入阻断；'
            f'{verdict_why}')
    if waiting_on == WAITING_ON_STAGE5_CLOSURE:
        return (
            'Stage5 机械闭合未落账：闭合由 Review 机械判定自动完成（批次 B3，'
            'producer 以 system:stage5-auto 落回执），没有人工回执路径。缺章/'
            '.INVALID/阻断级判定先走各自恢复面；工作包可闭合（ready/expired）'
            '时零成本重跑 `stage5.review` 即自动落账；'
            f'{verdict_why}')
    if waiting_on == WAITING_ON_STAGE5_REVIEWER_REWRITE:
        return (
            'Stage5 顶部 Commander 已把批量关闭式 rewrite 决定落成 current '
            'hash-bound request；当前只允许按 execution journal 发布 exact paid '
            'writer 或 0-wire prepared recovery，不再调用 Commander，也不退回默认'
            f'写作。执行终态后重新规划并免费重算 Review；{verdict_why}')
    if waiting_on == WAITING_ON_STAGE2_EDIT_COMMIT:
        return (
            'Stage2 typed 编辑已留下 hash-bound prepared journal；当前只能由 '
            'Executor 以 canonical global_edit.json 零 wire 补齐正式产物并 '
            f'readback，不能重采付费 Global；{verdict_why}')
    if waiting_on == WAITING_ON_STAGE2_EDIT_REVALIDATE:
        return (
            'Stage2 INVALID 已按与 producer 相同的 restore/normalize/HARD '
            '尺子确认无阻断；当前只允许 Executor 按冻结 actor/reason 做 '
            '0-wire revalidate，并由 prepared journal 补齐正式 bundle；'
            f'{verdict_why}')
    if waiting_on == WAITING_ON_REVIEW:
        return ('Stage5 Review 不是 fresh，旧结论不得驱动付费作业；'
                f'先零成本运行 `stage5.review`；{verdict_why}')
    if waiting_on == WAITING_ON_LEGACY:
        return f'{verdict_why}；旧产物只读保留，必须显式重建 canonical Stage5'
    if waiting_on == WAITING_ON_RESUME_DECISION:
        return (
            '续跑结论要求人工决定，队列不会把 REDO/UNKNOWN 猜成可执行命令；'
            f'{verdict_why}')
    # ⚠ F8A：以下两支只服务存量队列账的只读解码；现役队列不再发出 stage6 blocker。
    if waiting_on == 'stage6.prepare':
        return (
            '等待 stage6.prepare 机械通过并由 system:stage6-prepare-auto '
            f'自动接受；{verdict_why}')
    if waiting_on.startswith('approval:'):
        return (
            f'编排错误：现役队列不应再发出已退役审批闸 `{waiting_on}`；'
            f'{verdict_why}')
    return f'在等 `{waiting_on}`——{verdict_why}'


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
    from novel.work_units import check_workflow_write
    check_workflow_write(base)
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
    from novel.work_units import check_workflow_write
    check_workflow_write(base)
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
    from novel.orchestrator import retired_formats
    if retired_formats.detect_retired_agent_backend(
            prior_start.model_dump(mode='json', by_alias=True)):
        return ('原 execution 属于已退役的 Agent CLI executor'
                '（retired_executor_backend）；不自动续跑、不静默降级，'
                '历史账原样保留只读')
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
                # Stage4 三相与 Stage5 写作的人工审批授权均已随 2026-09-15
                # 全自动裁决退役（批次 B2/B3）：付费打开条件唯一化为机械事实
                # 与 run 启动授权（上传即授权本书全部付费节点），由 plan 与
                # producer 自身的关闭式校验把守；executor 内的机械授权仍由
                # `novel/api/routes/jobs.py` 的 prepare 面执行。
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
    from novel.work_units import check_workflow_write
    check_workflow_write(base)
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
    from novel.work_units import check_workflow_write
    check_workflow_write(base)
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
    from novel.work_units import WorkUnitRefused, check_workflow_write
    try:
        check_workflow_write(base)
    except WorkUnitRefused:
        # 旧或不可判工作流只提供历史读取，服务启动不得补锁、改事件或自动执行。
        # 读视图仍自行呈现损坏/未知；不因后台恢复不可用而阻止只读 UI 启动。
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
    'HUMAN_NODES', 'WAITING_ON_INBOX',
    'WAITING_ON_STAGE2_EDIT_COMMIT',
    'WAITING_ON_STAGE2_EDIT_REVALIDATE',
    'WAITING_ON_STAGE4_DESIGN_REVIEW',
    'WAITING_ON_STAGE4_DESIGN_REPAIR_COMMIT',
    'WAITING_ON_STAGE4_BINDING_RESOLUTION',
    'WAITING_ON_STAGE4_STRUCTURE_REPLAN',
    'WAITING_ON_STAGE4_STRUCTURE_REVIEW',
    'WAITING_ON_STAGE4_PHASE_APPROVAL', 'WAITING_ON_STAGE4_REPLAN',
    'WAITING_ON_WRITING_APPROVAL', 'WAITING_ON_REVIEW',
    'WAITING_ON_STAGE5_CLOSURE', 'WAITING_ON_STAGE5_REVIEWER_REWRITE',
    'WAITING_ON_LEGACY',
    'WAITING_ON_RESUME_DECISION', 'WAITING_ON_RESTART_CONFIRMATION', 'WAITING_ON_REPLAN',
    'DETERMINISTIC_RECOVERY_WAITING_ON',
    'QueueRegistry', 'append_event', 'cancel', 'confirm', 'create', 'fold',
    'fold_counts', 'frozen_resume_commands', 'new_queue_id', 'nodes_up_to', 'plan', 'plan_book',
    'queue_path', 'read_events', 'records', 'resume_incomplete', 'run', 'start',
    'resume', 'resume_start', 'wait',
]
