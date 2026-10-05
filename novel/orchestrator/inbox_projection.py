#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inbox_projection.py —— 「待人工」清单的**生产者**（BE-04 第一条；HTTP 路由属第二阶段）。

【为什么在 orchestrator 而不在 views】它的输入是 operator 快照
（`capture_operator_snapshot`）与审批 current 投影，输出同时喂
`operator next`（嵌进决策 DTO）、交付检查（blocking/disclosure 计数）
与 CLI/API 读面——这是「运行状态 → 待办领域状态」的编排域投影，
不是展示层组屏。放在 views 会让 orchestrator 反向 import 视图层，
形成 views↔orchestrator 包级环；2026-09 架构迁移时整体下沉，
逻辑逐字未动。views 层保持只做 DTO 组屏、单向依赖 orchestrator。

它是修复路由的 `human` 落面：`deterministic` 有 `Rule.fix`、`llm` 有反哺回路，
而「只有人能修 / 该人拍板」的那些，此前**没有生产者**——语义在前端登记了
（`InboxScreen.KIND_META`），后端却没有任何函数产出它们。

【各类的数据源】（全部现存机制，本模块**一个判定都不新造**）
  · `invalid`    盘上 `.INVALID` 在、同名正式稿**不在**：首跑被落盘闸门拦下，在等人改
  · `rejected`   `.INVALID` 与正式稿**并存**：通常是付费自救/人工补丁被闸门驳回，
                 正式稿仍是旧轮——「钱花了，稿丢了」。恢复路如实写明该节点当前有没有
                 INVALID 提升入口，不拼不存在的命令
                 （**UG-12 的闭环**：被驳回后下一条真实恢复路径写在这行字里）
  · `repairable` 0 成本复检里已绑定确定性 `Rule.fix` 路由的本层 HARD 命中
  · `human_only` 各层 0 成本复检里 `status=hit` 且 `remediation=human` 的结论逐条展开
                 （stage4 计划轻重告警自然入列——**UG-11 的闭环**：处置=归人判断）
  · `upstream`   `sev=hard` 且根因第一嫌疑（`owner`）在**别的层**的命中
  · `verification` 可由人补证据消解的披露类（Q-13 披露分层）；旧链的 trim 审计
                 与 stage7 rename 定稿披露两个生产者已随旧链退役，类与
                 `delivery_effect` 映射保留

⚠ F8c 随旧链退役的投影面：`stalled`（stage5 Review 收敛账）、`approval`
（stage6 文案 / stage7 人名表定稿闸）、stage5 章级 `.INVALID`/rejected
revisions、trim 审计待核验、stage2 称名投影免费物化待办——它们的节点与旗标
整体不在现役 `NODES`，人工待办面随之消失；live `.INVALID` 的阻断改由
`_invalid_items` 的通用投影与 queue 的收件箱分类负责。

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

import os

from contracts.invariants import (
    INBOX_KIND, NODES, STAGES,)
from contracts.schema.api import (
    InboxItem, InboxView,
)
from contracts.schema.report import CheckReport
from novel import paths, wontfix
from novel.errors import (
    ArtifactUnreadable,
)
from novel.orchestrator.inspection import (
    OperatorReadSnapshot, capture_operator_snapshot, read_surface_seal,
)
from novel.orchestrator.nodes import check_node
from novel.orchestrator.state import (
    readonly_ctx,
)
from novel.store import ArtifactStore

#: 兜底文案里会被点名的 0 成本人工路 flag。`(flag, 这条路在干什么)`。
#: ⚠ **顺序即优先级**：改内容排在记豁免前面——先问「能不能修」，再问「要不要不修」。
#: ⚠ 现役 `NODES` 没有任何节点接受这两个 flag（新链付费节点的免费复检是节点级
#:   `--check-only`）：清单据此如实回答「这一层没有 0 成本入口」，不背一句
#:   兑现不了的常量；将来某节点真登记了它们，文案自动跟上。
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
    del rule  # 判据级 owner 未登记（见上）；这里只按层答，不假装精确到单条结论

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


def _invalid_recovery_how(node: str) -> str:
    """INVALID 的恢复说明：现役节点没有 INVALID 提升入口，如实说明。

    旧链的 ``--revalidate`` 提升旗标族已随旧节点整体退役（`NODES` 不登记任何
    这类 flag），所以这里不拼命令——不能给节点指一条不存在的恢复路。
    """
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
    snapshot: OperatorReadSnapshot, *,
    _final_cas_owner: object | None = None,
) -> InboxView:
    """从同一次 code/overview/check 快照投影待办清单，不重跑判据。

    standalone 调用始终在返回前复核 surface。唯一例外是 ``operator next``
    传入本模块私有 capability，并在 inbox、queue 全部投影完成后执行更外层的
    同一 CAS；普通 bool/任意对象都不能关闭这道自检。
    """
    base = snapshot.base
    sid = snapshot.sid
    novel_dir = snapshot.novel_dir
    code = snapshot.code
    items: list[InboxItem] = []
    wontfix_total = 0

    for stage in STAGES:
        # ctx/store 仍用于本层旁路账；报告只取 snapshot 中已经跑过的那一份。
        ctx = readonly_ctx(novel_dir, check_node(stage), code)
        store = ArtifactStore(ctx)

        # 「有人看过并决定不修」是**逐层各一份账**（2026-08-15 裁决：层由文件路径承载）
        # ⇒ 计数要跨层求和。
        try:
            wontfix_total += len(wontfix.load(store, stage))
        except ArtifactUnreadable:
            # 一份旧豁免账不该让整本待办清单终态；该文件仍会在产物矩阵里显示
            # unreadable。这里不猜它有几条，按 0 聚合，保留其余层可用的待办。
            pass

        items += _invalid_items(store, stage)

        report = snapshot.check(stage).for_view()
        if report is not None:
            items += _report_items(report, stage)

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
        recovery = _invalid_recovery_how(node)
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
