#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inbox_projection.py —— 「待人工」八类清单的**生产者**（BE-04 第一条；HTTP 路由属第二阶段）。

【为什么在 orchestrator 而不在 views】它的输入是 operator 快照
（`capture_operator_snapshot`）与审批 current 投影，输出同时喂
`operator next`（嵌进决策 DTO）、交付检查（blocking/disclosure 计数）
与 CLI/API 读面——这是「运行状态 → 待办领域状态」的编排域投影，
不是展示层组屏。放在 views 会让 orchestrator 反向 import 视图层，
形成 views↔orchestrator 包级环；2026-09 架构迁移时整体下沉，
逻辑逐字未动。views 层保持只做 DTO 组屏、单向依赖 orchestrator。

它是修复路由的 `human` 落面：`deterministic` 有 `Rule.fix`、`llm` 有反哺回路，
而「只有人能修 / 该人拍板」的那些，此前**没有生产者**——八类语义在前端登记了
（`InboxScreen.KIND_META`），后端却没有任何函数产出它们。

【八类的数据源】（全部现存机制，本模块**一个判定都不新造**）
  · `invalid`    盘上 `.INVALID` 在、同名正式稿**不在**：首跑被落盘闸门拦下，在等人改
  · `rejected`   `.INVALID` 与正式稿**并存**：通常是付费自救/人工补丁被闸门驳回，
                 正式稿仍是旧轮——「钱花了，稿丢了」。恢复路先查 producer 真有的 flag
                 （**UG-12 的闭环**：被驳回后下一条真实恢复路径写在这行字里）
  · `stalled`    stage5 最新验收里 HARD/INFO 结论的
                 `unresolved_rounds >= STOP_REWRITE_AFTER` 且未豁免：修复已判
                 「根因不在写手」主动跳过，等人工改稿或豁免；METRIC/UNVER
                 是读数或判据边界，重复出现也不是待修故障
  · `repairable` 0 成本复检里已绑定确定性 `Rule.fix` 路由的本层 HARD 命中
  · `human_only` 各层 0 成本复检里 `status=hit` 且 `remediation=human` 的结论逐条展开
                 （stage4 计划轻重告警自然入列——**UG-11 的闭环**：处置=归人判断）
  · `verification` stage2 全书评测中可由人补证据消解的 `pending` / `UNVER`
  · `upstream`   `sev=hard` 且根因第一嫌疑（`owner`）在**别的层**的命中
  · `approval`   人定稿闸门（stage6 文案 / stage7 人名表）还没盖章
                 （⚠ 按前端契约 v1 不含 stage0——那一闸由审批状态机自己呈现；
                 ⚠ Stage6 三候选定稿闸已随 Stage6 整层退役（F8b）：新链 Hook 单
                 输出，不存在待人工核验的候选集，本类现只剩 stage7 机械定稿披露）

【三条纪律】
  ⚠ **通用 check 报告的 `unver` 永不入列**（`invariants.INBOX_KIND` 的纪律）：
    那是判据的边界不是待办。TODO-24 全书评测的可人工消解项是显式例外。
  ⚠ **豁免不占清单**：`wontfix` 过的结论只计数（「有人看过并决定不修」≠「没人看过」）。
    ⚠ 那张账**逐层各一份**（`<stage>/wontfix.json`，2026-08-15 裁决），
    计数在本模块跨层求和；语义结论的豁免也走同一份账（`novel/wontfix.py`）。
  ⚠ 只捕「数据说走不下去」那一族（`UpstreamMissing`/`ArtifactUnreadable`/`TerminalState`
    ——没跑到的层跳过，不编空结论）；`RefusedByStructure`
    是我们自己的 bug，**不捕**（`novel/errors.py` 开头那条：不许降级成一句温和的提示）。
"""
from __future__ import annotations

from contracts.artifacts import (
    STAGE2_GLOBAL_EDIT, STAGE7_NAME_MAP,
)

import os
import re

from contracts.invariants import (
    INBOX_KIND, NODES, STAGES, STOP_REWRITE_AFTER,)
from contracts.rules import REGISTRY
from contracts.schema.api import (
    ApprovalDoc, InboxItem, InboxView,
    Stage7ApprovalWorkpack,
)
from contracts.schema.report import CheckReport
from novel import paths, wontfix
from novel.errors import (
    ArtifactUnreadable, TerminalState, UpstreamMissing,
)
from novel.orchestrator.inspection import (
    OperatorReadSnapshot, capture_operator_snapshot, read_surface_seal,
)
from novel.orchestrator.nodes import check_node
from novel.orchestrator.state import (
    readonly_ctx,
    stage2_projection_recovery_needed,
)
from novel.store import ArtifactStore

_CH_RX = re.compile(r'ch(\d+)')

#: 兜底文案里会被点名的两条 0 成本人工路。`(flag, 这条路在干什么)`。
#: ⚠ **顺序即优先级**：改内容排在记豁免前面——先问「能不能修」，再问「要不要不修」。
_HUMAN_FLAGS: tuple[tuple[str, str], ...] = (
    ('--apply-edit', '改内容'),
    ('--wontfix', '记「不修」的裁决'),
)


def _human_how(stage: str, rule: str | None = None) -> str:
    """兜底的 0 成本人工路（有 `Rule.fix` 时用 fix 那条，更具体）——
    **按这一层真有的入口说话，不背一句常量。**

    ⚠⚠ **这里原来是一个常量**，逐字写着
    「0 成本人工路：`--apply-edit` 改内容 / `--wontfix` 记「不修」的裁决」。
    而 `--wontfix` **全仓只有 `stage5.review` 有**（`NODES` 可查）：
    stage6 的 `hook_candidate_contradiction` 一命中，清单就会建议人去跑一个
    这一层根本不存在的 flag ⇒ `build_argv` 当场拒收，
    人拿到的是一条**兑现不了的承诺**，而清单上看不出来。

    ⚠ 这与 `contracts/rules.py::_validate` ⑧ 拦的是同一种病——那三道校验的原话是
    「`build_argv` 会当场拒收它 ⇒ 这条修复路**永远兑现不了**，而表里只看得见一句
    『修法：…』」。区别只在于：`Rule.fix` 那一侧有校验，**兜底文案这一侧一个都没有**。
    ⇒ 从 `NODES` 派生。谎话在结构上就说不出口了，而且新入口一上线它自动跟上。

    ⚠⚠ **从 `NODES` 派生还不够——粒度也得对。** 这里原来把整层的 flag 并成一个集合
    （`spec.stage == stage`）再报**裸 flag 名**。`stage2` 是全仓唯一一个两个节点入口
    **不同**的层；当时只有 `stage2.global` 有 `--apply-edit`，Blueprint 的 typed
    关系整章写回尚未上线，
    于是 `present_continuity`（blueprint 的 info 判据）拿到的处置行是
    「0 成本人工路：`--apply-edit` 改内容」——把 global 的入口**借给了** blueprint，
    照着跑仍旧被 `build_argv` 当场拒收。**同一种病，只是从「层」降到了「节点」。**
    ⇒ 每条路一律带上**它真正属于的节点 id**，读者据此自己判断适不适用于手上这条结论。

    ⚠ 已知边界（本函数只拿得到 `stage`）：它答的是「这一层有哪些 0 成本入口、
    分别属于哪个节点」，**不是**「你手上这条 finding 有没有路」。要精确到后者，
    得给 `Rule` 加 node 级 owner（`OWNER` 取值面目前是 stage 级），
    那要动约 200 条判据 ⇒ 未做。带节点前缀已经足以让人不把别的节点的入口当成自己的。

    ⚠ 一条都没有时**如实说没有**，不要编一个看起来像下一步的句子：
    「这一层没有 0 成本入口」与「有而我没告诉你」是两件事。
    """
    if stage == 'stage2' and rule == 'opening_scope_valid':
        return (
            '用 GlobalEdit 的 `source_blurb_replace` 提出有证据的连续开篇范围，再跑 '
            f'`stage2.global --apply-edit {STAGE2_GLOBAL_EDIT}`（0 成本）；'
            '付费 `--resample` 只作最后手段')

    # ⚠ 外层是 flag、内层才是 node：`_HUMAN_FLAGS` 的顺序即优先级（改内容在前），
    #   反过来写会让优先级随 `NODES` 的排列漂。
    routes = [
        f'`{node} {flag}` '
        + ('提供包含所有 producer-owned slot 的整章全文'
           if flag == '--apply-edit' and stage == 'stage5' else what)
        for flag, what in _HUMAN_FLAGS
        for node, spec in NODES.items()
        if spec.stage == stage and flag in spec.flags
    ]
    if not routes:
        return (f'{stage} 没有 0 成本人工入口——处置归人判断：'
                f'要么改上游重跑本层，要么这条结论就这样留在报告上')
    head = '0 LLM 费用的人工路：' if stage == 'stage5' else '0 成本人工路：'
    return head + ' / '.join(routes)


def _revalidate_how(node: str) -> str:
    """Auto 0-wire promotion path; human Stage 页不再发布该用户入口。"""
    from contracts.invariants import (
        SYSTEM_INVALID_REVALIDATE_ACTOR, SYSTEM_INVALID_REVALIDATE_REASON,
    )
    return (
        f'队列/自动恢复将跑 `{node} --revalidate --actor '
        f'{SYSTEM_INVALID_REVALIDATE_ACTOR} --reason <audit>`（0 LLM、同轮重判）；'
        f'固定理由：{SYSTEM_INVALID_REVALIDATE_REASON}；不是新一轮人工改写')


def _invalid_recovery_how(
        store: ArtifactStore, *, node: str, rel: str,
        formal_exists: bool) -> str:
    """返回当前 producer 真正存在的恢复路。

    顺序是契约：通用 INVALID 必须先问 producer 有没有 ``--revalidate``，不能因为
    某个历史节点有过这条 flag，就给所有节点拼一条不存在的命令。
    """
    del store, rel, formal_exists
    spec = NODES.get(node)
    if spec is not None and '--revalidate' in spec.flags:
        return _revalidate_how(node)
    return (
        f'`{node}` 没有可执行的 INVALID 提升入口；先保留 `.INVALID` 证据并按该节点当前恢复契约'
        '处置，不得伪造提升命令'
    )


def inbox_view(base: str | os.PathLike[str], sid: str) -> InboxView:
    """一本书的待人工清单。**纯读**：不写一个字节、不发一个请求、不产生新判定。

    每次调用都新建一次 invocation-scoped 快照；没有跨请求、跨命令或跨写盘缓存。
    """
    return inbox_from_snapshot(capture_operator_snapshot(base, sid))


_OPERATOR_FINAL_CAS = object()


def inbox_from_snapshot(
    snapshot: OperatorReadSnapshot, *, approval_doc: ApprovalDoc | None = None,
    _final_cas_owner: object | None = None,
) -> InboxView:
    """从同一次 code/overview/check 快照投影八类清单，不重跑判据。

    standalone 调用始终在返回前复核 surface。唯一例外是 ``operator next``
    传入本模块私有 capability，并在 inbox、queue 全部投影完成后执行更外层的
    同一 CAS；普通 bool/任意对象都不能关闭这道自检。
    """
    base = snapshot.base
    sid = snapshot.sid
    if approval_doc is not None and approval_doc.sid != sid:
        raise ValueError(
            f'审批投影属于 {approval_doc.sid!r}，不是当前书 {sid!r}')
    novel_dir = snapshot.novel_dir
    code = snapshot.code
    items: list[InboxItem] = []
    wontfix_total = 0
    trim_audits = _current_trim_audits(novel_dir, code)
    ov = snapshot.overview

    for stage in STAGES:
        # ctx/store 仍用于本层旁路账与活动终态；报告只取 snapshot 中已经跑过的那一份。
        ctx = readonly_ctx(novel_dir, check_node(stage), code)
        store = ArtifactStore(ctx)

        # 「有人看过并决定不修」是**逐层各一份账**（2026-08-15 裁决：层由文件路径承载）
        # ⇒ 计数要跨层求和。⚠ 只数一次不数两次：`_stage5_stalled`
        #   从此只交 stalled，不再自己数 stage5 那一份。
        try:
            wontfix_total += len(wontfix.load(store, stage))
        except ArtifactUnreadable:
            # 一份旧豁免账不该让整本待办清单终态；该文件仍会在产物矩阵里显示
            # unreadable。这里不猜它有几条，按 0 聚合，保留其余层可用的待办。
            pass

        items += _invalid_items(store, stage)
        if stage == 'stage5':
            items += _stage5_chapter_invalids(store)
            items += _stage5_rejected_revisions(store)
            items += _stage5_stalled(store)

        report = snapshot.check(stage).for_view()
        if report is not None:
            items += _report_items_for_inbox(report, stage)

    items += _stage2_projection_recovery_items(ov)
    items += _trim_verification_items(novel_dir, code, audits=trim_audits)
    stage7_workpack = _stage7_workpack_or_none(novel_dir, code)
    # Stage4 四道人工闸的审批 inbox 投影已随 2026-09-15 全自动裁决退役：
    # A/B 落盘后的推进由机械准入与预算内自动重采驱动，不再产生待审清单行。
    # Stage6 三候选定稿闸已随 Stage6 整层退役（F8b）：开篇 Hook 由新链
    # stage4.hook_design＋stage5.hook_writer 单输出，不存在待人工核验的候选集。
    items += _approval_items(
        novel_dir, code,
        stage7_workpack=stage7_workpack,
        workpacks_checked=True,
    )

    # “还等不等人”与“挡不挡交付”不能从 kind 在前端重推；由后端在唯一生产口物化。
    items = [_classify(item) for item in items]
    order = {s: i for i, s in enumerate(STAGES)}
    items.sort(key=lambda it: (order.get(it.stage, 99),
                               it.chapter if it.chapter is not None else -1,
                               it.kind, it.rule))
    counts = {kind: 0 for kind in INBOX_KIND}
    for item in items:
        counts[item.kind] += 1
    attention_counts = {key: 0 for key in ('pending', 'reviewed_retained')}
    delivery_counts = {key: 0 for key in ('blocking', 'disclosure')}
    for item in items:
        attention_counts[item.attention] += 1
        delivery_counts[item.delivery_effect] += 1
    view = InboxView(
        items=items, counts=counts, attention_counts=attention_counts,
        delivery_counts=delivery_counts, total=len(items), wontfix=wontfix_total)
    if (
        _final_cas_owner is not _OPERATOR_FINAL_CAS
        and read_surface_seal(base, sid, code=code) != snapshot.surface_seal
    ):
        raise ArtifactUnreadable(
            'Inbox 投影期间本书代码/产物/审批盘面发生变化；'
            '拒绝返回混合时点清单，请重试')
    return view


def _stage2_projection_recovery_items(ov) -> list[InboxItem]:
    """把 state 的唯一恢复判定投影成可执行待办，不在 view 重算一套条件。"""
    if not stage2_projection_recovery_needed(ov):
        return []
    return [InboxItem(
        kind='repairable', stage='stage2', chapter=None,
        rule='blueprint_character_projection',
        what='Blueprint 称名投影缺失、损坏或未绑定当前人物登记',
        how=('队列将跑 `stage2.characters --revalidate --actor '
             'system:projection-recovery`：从当前人物登记免费重建称名投影；'
             '不会重采人物或知识'),
    )]


def _classify(item: InboxItem) -> InboxItem:
    """把正交两轴物化；不因“看起来像人工项”猜一份不存在的裁决。

    ⚠ **这是 `delivery_effect` 的唯一物化口**。批次 C：``verification`` 全转
    披露类（质量/未审面），不再挡交付；机械阻断仍走 invalid/rejected/stalled/
    repairable/upstream/approval。Stage2 语义未审只经报告 ``unver`` 与交付披露。
    """
    disclosure = (
        item.kind == 'human_only'
        or item.kind == 'verification'
    )
    return item.model_copy(update={
        'delivery_effect': 'disclosure' if disclosure else 'blocking',
    })


def _stage_rels(stage: str) -> list[str]:
    """这一层各节点 `produces` 里**可 `is_file()`** 的 JSON 产物（花括号模式除外）。"""
    seen: dict[str, None] = {}
    for spec in NODES.values():
        if spec.stage != stage:
            continue
        for rel in spec.produces:
            if '{' not in rel and rel.endswith('.json'):
                seen[rel] = None
    return list(seen)


def _invalid_items(store: ArtifactStore, stage: str) -> list[InboxItem]:
    out: list[InboxItem] = []
    for rel in _stage_rels(stage):
        inv = paths.invalid_of(rel)
        if not store.exists(inv):
            continue
        node = _producer_of(rel, stage)
        formal_exists = store.exists(rel)
        recovery = _invalid_recovery_how(
            store, node=node, rel=rel, formal_exists=formal_exists)
        if formal_exists:
            rounds = sorted(store.seen_rounds(rel))
            what = f'{inv}：付费自救/人工补丁被落盘闸门驳回，正式稿仍是旧轮'
            how = (f'历史轮次在 `{paths.rounds_dir_of(rel)}/`'
                   f'（已见 r{rounds[-1] if rounds else "?"}）；{recovery}')
            out.append(InboxItem(
                kind='rejected', stage=stage, chapter=None, rule=node,
                what=what, how=how))
        else:
            what = f'{inv}：首跑被落盘闸门拦下，还没有现役产物'
            out.append(InboxItem(
                kind='invalid', stage=stage, chapter=None, rule=node,
                what=what, how=recovery))
    return out


def _producer_of(rel: str, stage: str) -> str:
    """产这份 rel 的**第一个**节点 id（NODES 顺序即处理链顺序）。"""
    for node, spec in NODES.items():
        if spec.stage == stage and rel in spec.produces:
            return node
    return stage


def _stage5_chapter_invalids(store: ArtifactStore) -> list[InboxItem]:
    from novel.stages.stage5 import measure as M
    try:
        _by_ord, invalids, _judged = M.load_drafts(store)
    except (UpstreamMissing, ArtifactUnreadable):
        return []
    out: list[InboxItem] = []
    for inv in sorted(invalids):
        formal = inv.replace('.INVALID.', '.', 1)
        match = _CH_RX.search(inv)
        chapter = int(match.group(1)) if match else None
        kind = 'rejected' if store.exists(formal) else 'invalid'
        out.append(InboxItem(
            kind=kind, stage='stage5', chapter=chapter, rule='stage5.write',
            what=(f'{inv}：自救稿被闸门拦下，正式稿仍是旧轮' if kind == 'rejected'
                  else f'{inv}：这一章还没有过闸的正式稿'),
            how=f'改好后跑 {_revalidate_how("stage5.write")}；'
                f'历史轮次在 `{paths.rounds_dir_of(formal)}/`'))
    return out


def _stage5_rejected_revisions(store: ArtifactStore) -> list[InboxItem]:
    """列出修订退化证据；它与 `.INVALID` 是两种不同的 rejected。"""
    from contracts.schema.stage5 import RejectedRevision

    out: list[InboxItem] = []
    directory_rel = 'stage5'
    directory = store.path(directory_rel)
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob('ch*.rejected.json')):
        rel = f'{directory_rel}/{path.name}'
        match = _CH_RX.search(path.name)
        chapter = int(match.group(1)) if match else None
        try:
            artifact, _raw = store.read_own(RejectedRevision, rel)
        except ArtifactUnreadable:
            continue
        out.append(InboxItem(
            kind='rejected', stage='stage5', chapter=chapter,
            rule='stage5.write',
            what=(f'{rel}：两次修订候选都相对旧正式稿退化；'
                  f'旧稿未覆盖。{artifact.why[0]}'),
            how=(f'检查该文件的 {len(artifact.attempts)} 份候选；人工改好后跑 '
                 f'`stage5.write --apply-edit <chNN.json>`；输入必须提供'
                 f'该章全部 producer-owned slot 的完整 `segments[{{slot,text}}]` 全文（0 LLM 费用），'
                 f'或在确认修复方向后用 `--revise --chapters {chapter}` 再采样')))
    return out


def _stage5_stalled(store: ArtifactStore) -> list[InboxItem]:
    """连续 N 轮未消、修复已判「根因不在写手」主动跳过的 HARD/INFO。

    Review 的收敛账会给每一类 finding 留轮次，包括无条件常驻的 METRIC/UNVER。
    后两类重复出现是读数稳定或边界仍在，不是修复失败；把它们升级成 stalled
    会让一本机械上可交付的书被永久伪阻塞。

    ⚠ 豁免计数**不在这里数**：它逐层各一份账，由 `inbox_view` 跨层求和
    （从前这里顺手数了 stage5 那一份，收成跨层之后会数两次）。
    """
    from novel.stages.stage5 import review
    current = review.review_status(store.ctx, store)
    if current.state == 'missing':
        return []
    if not current.fresh or current.review is None:
        reason = '；'.join(current.reasons[:5]) or current.state
        return [InboxItem(
            kind='upstream', stage='stage5', chapter=None, rule='review_status',
            what=f'Stage5 Review 为 {current.state}：{reason}',
            how=('旧/过期 Review 不得驱动付费返工或豁免。stale/unknown 先零成本运行 '
                 '`stage5.review`；legacy_incompatible 请保留旧 base 为历史证据，在新的 '
                 'canonical base 从 Stage5 producer 重建'))]
    rev = current.review
    out: list[InboxItem] = []
    stalled_band: list[tuple[int, object]] = []

    def one(chapter: int | None, finding) -> None:
        if finding.wontfix is not None:
            return
        if not _is_stage5_stalled(finding.rule, finding):
            return
        if finding.rule == 'band_drift' and chapter is not None:
            stalled_band.append((chapter, finding))
            return
        out.append(InboxItem(
            kind='stalled', stage='stage5', chapter=chapter, rule=finding.rule,
            what=finding.detail,
            how=f'连续 {finding.unresolved_rounds} 轮未消，修复已判「根因不在写手」'
                f'主动跳过——人工提供所有 producer-owned slot 的整章全文'
                f'（`stage5.write --apply-edit`，0 LLM 费用）'
                f'或豁免（`stage5.review --wontfix`）'))

    for row in rev.chapters:
        for finding in row.findings:
            one(row.output_ordinal, finding)
    for finding in rev.book:
        one(None, finding)
    if stalled_band:
        chapters = sorted({chapter for chapter, _finding in stalled_band})
        rounds = max(int(finding.unresolved_rounds or 0)
                     for _chapter, finding in stalled_band)
        details = [f'ch{chapter}: {finding.detail}'
                   for chapter, finding in stalled_band]
        out.append(InboxItem(
            kind='stalled', stage='stage5', chapter=None, rule='band_drift',
            what=(f'书级档位漂移连续未消：涉及章节 {chapters}；'
                  f'逐章明细 {details}'),
            how=(f'最长已连续 {rounds} 轮未消，修复已判「根因不在单章写手」'
                 '主动跳过——按书级风格核对后人工改稿，或在 Review 逐章明细上'
                 '记录 `stage5.review --wontfix`')))
    return out


def _is_stage5_stalled(rule_id: str, finding) -> bool:
    """只有可形成质量结论的 HARD/INFO 才能成为 stalled 待办。"""
    rule = REGISTRY.get(rule_id)
    return (rule.sev in ('hard', 'info')
            and int(finding.unresolved_rounds or 0) >= STOP_REWRITE_AFTER)


def _report_items(
    report: CheckReport,
    stage: str,
) -> list[InboxItem]:
    out: list[InboxItem] = []
    for line in report.rules:
        if line.status != 'hit':
            continue
        findings = []
        for finding in line.findings:
            if finding.wontfix is not None:
                continue                                   # 豁免过：计数不占清单
            stalled = (stage == 'stage5'
                       and _is_stage5_stalled(line.rule, finding))
            if stage == 'stage5' and stalled:
                continue                                   # 已由 stalled 类列出，别双份
            findings.append(finding)

        for finding in findings:
            chapter = getattr(finding, 'output_ordinal', None)
            if line.sev == 'hard' and finding.owner not in (None, stage, 'corpus'):
                out.append(InboxItem(
                    kind='upstream', stage=stage, chapter=chapter, rule=line.rule,
                    what=finding.detail,
                    how=f'阻断级且根因第一嫌疑在 {finding.owner}——'
                        f'先修上游那一层，再回来重跑本层复检'))
            elif (line.sev == 'hard' and finding.owner == stage
                  and line.fix is not None):
                fix = line.fix
                if fix.available:
                    how = (f'跑 `{fix.node} {" ".join(fix.flags)}`'
                           f'（{"付费" if fix.paid else "0 成本"}）')
                else:
                    how = f'机器修复路由暂不可执行：{fix.unavailable_reason}'
                out.append(InboxItem(
                    kind='repairable', stage=stage, chapter=chapter, rule=line.rule,
                    what=finding.detail, how=how))
            elif line.remediation == 'human':
                fix = line.fix
                how = (f'跑 `{fix.node} {" ".join(fix.flags)}`'
                       f'（{"付费" if fix.paid else "0 成本"}）'
                       if fix is not None and fix.available
                       else f'机器修复路由暂不可执行：{fix.unavailable_reason}'
                       if fix is not None
                       else _human_how(stage, line.rule))
                out.append(InboxItem(
                    kind='human_only', stage=stage, chapter=chapter, rule=line.rule,
                    what=finding.detail, how=how))
    return out


def _current_trim_audits(novel_dir, code: str):
    """One shared definition of a current, unresolved Stage5 trim audit."""
    from contracts.artifacts import STAGE5_TRIM_AUDIT
    from novel.stages.stage5 import trim_resolution

    # ⚠ 旧链 trim 审计面（F8c 随旧链删除）：新链书没有 trim 审计产物；
    # 在构造旧节点只读 ctx 之前按纯目录存在性早退，清单组装热路不得崩。
    if not (novel_dir / STAGE5_TRIM_AUDIT.rsplit('/', 1)[0]).is_dir():
        return {}
    store = ArtifactStore(readonly_ctx(novel_dir, 'stage5.write', code))
    try:
        return trim_resolution.current_unresolved_audits(store)
    except (ArtifactUnreadable, TerminalState):
        # 旧 v1 trim 由统一 review_status 作为 legacy_incompatible 出口；这里不能在
        # 清单组装开头先崩掉，也不能把它迁移成一份“当前 audit”。
        return {}


def _trim_verification_items(novel_dir, code: str, *, audits=None) -> list[InboxItem]:
    """Durable over-length audits whose retained pretrim still needs attention."""
    from novel.stages.stage5 import trim_resolution

    current = audits if audits is not None else _current_trim_audits(novel_dir, code)
    if not current:
        # 旧链 trim 审计面（F8c 随旧链删除）：无当前审计就不构造旧节点 ctx。
        return []
    store = ArtifactStore(readonly_ctx(novel_dir, 'stage5.write', code))
    out: list[InboxItem] = []
    for ordinal, (audit, _audit_sha256) in sorted(current.items()):
        context = trim_resolution.verification_context(
            store,
            audit,
            ordinal=ordinal,
        )
        out.append(InboxItem(
            kind='verification', stage='stage5', chapter=ordinal,
            rule=f'trim_audit_ch{ordinal:02d}',
            what=(f'第 {ordinal} 章超上限且本仓没有自动收缩路径：{audit.reason}；'
                  f'超限根因附注：{context}'),
            how=('超限事实随交付披露（Q-13 / degradable）；批次 B3 后由 '
                 '`stage5.review` producer 机械处置（预算内改写或机械保留），'
                 '不再等人提交 retain-pretrim')))
    return out


def _stage7_workpack_or_none(
    novel_dir, code: str,
) -> Stage7ApprovalWorkpack | None:
    """只建一次与真正 approve 同 node/code 的 Stage7 fresh 工作包。"""
    from novel.stages.stage7 import rename

    store = ArtifactStore(readonly_ctx(novel_dir, 'stage7.rename', code))
    if not store.exists(STAGE7_NAME_MAP):
        return None
    try:
        return rename.approval_workpack(store)
    except (ArtifactUnreadable, TerminalState, UpstreamMissing):
        return None


def _approval_items(
    novel_dir, code: str, *,
    stage7_workpack: Stage7ApprovalWorkpack | None = None,
    workpacks_checked: bool = False,
) -> list[InboxItem]:
    """stage7 人名表的机械定稿披露项。⚠ 它**不是故障**，但确实该在这张清单上。"""
    out: list[InboxItem] = []

    if not workpacks_checked:
        stage7_workpack = _stage7_workpack_or_none(novel_dir, code)

    if (stage7_workpack is not None
            and stage7_workpack.disposition != 'already_current'):
        out.append(InboxItem(
            kind='verification', stage='stage7', chapter=None, rule='stage7.rename',
            what=('人名映射尚未机械定稿——rename producer 在校验通过后自动 seal'),
            how=('运行 stage7.rename --recheck（0 成本）触发机械 seal；'
                 '无 HARD 时 producer 会以 system:stage7-auto 自动定稿；'
                 '本项为披露，不挡交付终验以外的机械准入')))
    return out


def _report_items_for_inbox(
    report: CheckReport,
    stage: str,
) -> list[InboxItem]:
    """Stage4 INFO 只由 B 工作包投影一次，其余既有修复路完整保留。

    Stage4 工作包还负责把同一批 disclosure 在 current typed decision 下统一
    标成 ``reviewed_retained``。若通用 report 路也发一份，它既会重复，又会
    永远停在 pending；这里只去掉 INFO/human 那一类，不触碰 HARD 的
    upstream、repairable 或 human-only。
    """
    items = _report_items(report, stage)
    if stage != 'stage4':
        return items
    return [
        item for item in items
        if not (
            item.kind == 'human_only'
            and REGISTRY.get(item.rule).sev == 'info'
        )
    ]
