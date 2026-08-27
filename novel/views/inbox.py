#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inbox.py —— 「待人工」八类清单的**生产者**（BE-04 第一条；HTTP 路由属第二阶段）。

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
  · `approval`   两个人定稿闸门（stage6 文案 / stage7 人名表）还没盖章
                 （⚠ 按前端契约 v1 不含 stage0——那一闸由审批状态机自己呈现）

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
    STAGE2_GLOBAL_REVIEW, STAGE6_HOOK, STAGE7_NAME_MAP,
)

import os
import re

from contracts.invariants import (
    INBOX_KIND, NODES, STAGES, STOP_REWRITE_AFTER, review_effect_of, slot_of,)
from contracts.rules import REGISTRY
from contracts.schema.api import InboxDecision, InboxItem, InboxView
from contracts.schema.evaluation import GlobalReview
from contracts.schema.report import CheckReport
from contracts.schema.stage6 import Hook
from contracts.schema.stage7 import NameMap
from novel import paths, prov, wontfix
from novel.quality.global_review import review_item_kind
from novel.errors import ArtifactUnreadable, TerminalState, UpstreamMissing
from novel.orchestrator.nodes import check_node
from novel.orchestrator.state import readonly_ctx
from novel.stages import check_only
from novel.store import ArtifactStore

_CH_RX = re.compile(r'ch(\d+)')

#: 兜底文案里会被点名的两条 0 成本人工路。`(flag, 这条路在干什么)`。
#: ⚠ **顺序即优先级**：改内容排在记豁免前面——先问「能不能修」，再问「要不要不修」。
_HUMAN_FLAGS: tuple[tuple[str, str], ...] = (
    ('--apply-edit', '改内容'),
    ('--wontfix', '记「不修」的裁决'),
)


def _human_how(stage: str) -> str:
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
    **不同**的层（`stage2.global` 有 `--apply-edit`，`stage2.blueprint` 没有），
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
    """Copyable strong-audit promotion, never the old credential-free form."""
    return (f'`{node} --revalidate --actor <id> --reason <why>`：0 LLM、同轮重判、'
            '写入不可变审计；不是新一轮人工改写')


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

    ⚠ 它对每个已移植层各跑一次 0 成本复检（stage2/4 会重算判据，大书上是秒级）
    ——第二阶段挂 HTTP 时若嫌慢，缓存加在路由层，不加在这里。
    """
    novel_dir = paths.resolve_novel_dir(base, sid)
    code = prov.code_version()
    items: list[InboxItem] = []
    wontfix_total = 0
    trim_audits = _current_trim_audits(novel_dir, code)

    for stage in STAGES:
        # ⚠ 走 `check_node` 而不是 `nodes[0] if nodes else f'{stage}.review'`：
        #   那条兜底真走到时会**造出一个不存在的节点 id**（`stage6.review`）——
        #   一个编不出来的节点会一路带进 `readonly_ctx`，把「这层没有复检桥」
        #   变成「这层有一座谁都找不到的桥」。单数那一格由落点自己把关。
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

        report = _report_of(ctx, stage)
        if report is not None:
            items += _report_items(report, stage)

    items += _verification_items(novel_dir, code)
    items += _trim_verification_items(novel_dir, code, audits=trim_audits)
    items += _hook_verification_items(novel_dir, code)
    items += _approval_items(novel_dir, code)

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
    return InboxView(
        items=items, counts=counts, attention_counts=attention_counts,
        delivery_counts=delivery_counts, total=len(items), wontfix=wontfix_total)


def _classify(item: InboxItem) -> InboxItem:
    """把正交两轴物化；不因“看起来像人工项”猜一份不存在的裁决。

    ⚠ **这是 `delivery_effect` 的唯一物化口**，所以 stage2 复核项的分级也必须落在
    这里，而不是在 `_verification_items` 里先填一次再被这里覆盖掉——那种形状
    「填了但不生效」不会报错，只会静默恢复成旧口径。
    分类本身仍只从 `review_effect_of` 取，本模块不复制第二张表；`item_id` 的文法
    也只由 `review_item_kind` 反向读，不在这里现拆一次 `split(':')`。
    """
    disclosure = (
        item.kind == 'human_only'
        or (item.kind == 'verification' and item.stage == 'stage6')
        or (item.kind == 'verification' and item.stage == 'stage2'
            and review_effect_of(review_item_kind(item.rule)) == 'disclosure')
    )
    return item.model_copy(update={
        'delivery_effect': 'disclosure' if disclosure else 'blocking',
    })


def _report_of(ctx, stage: str) -> CheckReport | None:
    try:
        return check_only(stage)(ctx)
    except (UpstreamMissing, ArtifactUnreadable, TerminalState):
        # 这一层没跑到 / 读不回来：它的处境由产物矩阵与各自的屏呈现，
        # 清单不替它编条目——「查不了」不是「待办」。
        return None


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
                       else _human_how(stage))
                out.append(InboxItem(
                    kind='human_only', stage=stage, chapter=chapter, rule=line.rule,
                    what=finding.detail, how=how))
    return out


def _verification_items(novel_dir, code: str) -> list[InboxItem]:
    """TODO-24 可人工消解的全书评测项；通用 `unver` 不借机入列。

    ⚠ **分级只改 `delivery_effect`，不改「在不在清单里」。** `disclosure` 的三类
    （名字区分度 / 称谓场合 / 结局）不再挡 Stage4，正因为如此它们**必须**继续
    逐条出现在这里与终审包——「不阻断」一旦顺手变成「不列出」，这次分级就成了
    一次静默删审计。分类只从 `review_effect_of` 取，本模块不复制第二张表。
    """
    store = ArtifactStore(readonly_ctx(novel_dir, 'stage2.global', code))
    if not store.exists(STAGE2_GLOBAL_REVIEW):
        return []
    try:
        review, _raw = store.read_own(GlobalReview, STAGE2_GLOBAL_REVIEW)
    except ArtifactUnreadable:
        return []
    out: list[InboxItem] = []
    for row in review.items:
        if row.state not in ('pending', 'FAIL', 'UNVER'):
            continue
        state = {
            'pending': '待复核',
            'FAIL': '复核未通过（FAIL）',
            'UNVER': '证据不足（UNVER）',
        }[row.state]
        reason = f'；上次理由：{row.reason}' if row.reason else ''
        scope = ('' if review_effect_of(row.kind) == 'blocking'
                 else '（只披露，不挡 Stage4）')
        out.append(InboxItem(
            kind='verification', stage='stage2', chapter=None,
            rule=row.item_id,
            what=f'{row.subject_path}：{state}{scope}{reason}',
            how=('在全局设定屏逐项或批量补 actor、理由与证据，按 subject_sha256 '
                 '提交 PASS/FAIL/UNVER；也可跑 '
                 '`stage2.global --review-decisions <书内json>`'
                 '（模板默认只出 blocking 面，要整份加 `--review-scope all`）；'
                 'subject hash 已变的旧决策会被拒绝'),
            attention=('pending' if row.state == 'pending' else 'reviewed_retained'),
            decision=(None if row.state == 'pending' else InboxDecision(
                actor=row.actor or '', reason=row.reason or '',
                subject_sha256=row.subject_sha256, state=row.state,
                decided_at=row.decided_at or ''))))
    return out


def _current_trim_audits(novel_dir, code: str):
    """One shared definition of a current, unresolved Stage5 trim audit."""
    from novel.stages.stage5 import trim_resolution

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
    store = ArtifactStore(readonly_ctx(novel_dir, 'stage5.write', code))
    out: list[InboxItem] = []
    for ordinal, (audit, _audit_sha256) in sorted(current.items()):
        context = trim_resolution.verification_context(store, audit)
        out.append(InboxItem(
            kind='verification', stage='stage5', chapter=ordinal,
            rule=f'trim_audit_ch{ordinal:02d}',
            what=(f'第 {ordinal} 章超上限且本仓没有自动收缩路径：{audit.reason}；'
                  f'超限根因附注：{context}'),
            how=('当前保留 pretrim 原稿；这是 0 次 LLM 调用的人工销账，'
                 '人工确认保留原稿可跑 `stage5.review --retain-pretrim '
                 f'--chapters {ordinal} --actor <自报身份> --reason <理由>`；'
                 '确需改写则 `stage5.write --apply-edit <路径>`（同样 0 次调用）。'
                 'resolution 同时绑定当前正式章路径/正文 SHA-256、audit 完整 SHA-256 '
                 '与 before hash，任一变化即失效')))
    return out


def _hook_verification_items(novel_dir, code: str) -> list[InboxItem]:
    """每条候选都等着一次 hash 绑定的人工核验——本仓没有自动放行门。

    ⚠ 这里曾按 `assessment.human_resolvable` 过滤。`semantic_state` 收成
    `unreviewed` / `uncertain` 两态之后那个谓词恒真，过滤是空转；
    **两个状态都要人看**，差别只在「没人判过」还是「两名判官的带证据结论等着人读」。
    """
    from novel.stages.stage6 import semantic

    store = ArtifactStore(readonly_ctx(novel_dir, 'stage6.hook', code))
    if not store.exists(STAGE6_HOOK):
        return []
    try:
        art, _raw = store.read_own(Hook, STAGE6_HOOK)
        assessments = semantic.current_assessments(store, art)
    except (ArtifactUnreadable, TerminalState):
        return []
    resolved_sha = (
        art.approved.resolution.subject_sha256
        if art.approved is not None
        and art.approved.resolution.basis == 'human_verification'
        else None)
    out: list[InboxItem] = []
    seen: set[str] = set()
    for index, candidate in enumerate(art.candidates, 1):
        subject = semantic.text_sha256(candidate.text)
        if subject in seen or subject == resolved_sha:
            continue
        seen.add(subject)
        assessment = assessments[semantic.text_sha1(candidate.text)]
        out.append(InboxItem(
            kind='verification', stage='stage6', chapter=None,
            # ⚠ 收敛键按**位置身份**，不按策略名：同一种策略两条候选会共用一个键，
            #   而那正是「换了个位置的同类问题」被当成同一条的形状。
            rule=f'reference_boundary:{slot_of(index)}',
            what=f'候选 {index}（{candidate.approach}）当前为 {assessment.state}：'
                 f'{assessment.reason}',
            how=(f'人工核对全书事实后跑 `stage6.approve --pick {index} '
                 f'--actor <自报身份> --reason <理由> '
                 f'--subject-sha256 {subject}`；候选字节变化会拒绝旧决定')))
    return out


def _approval_items(novel_dir, code: str) -> list[InboxItem]:
    """两个「人定稿」闸门。⚠ 它们**不是故障**，但确实该在这张清单上。"""
    out: list[InboxItem] = []

    store6 = ArtifactStore(readonly_ctx(novel_dir, 'stage6.hook', code))
    if store6.exists(STAGE6_HOOK):
        try:
            art, _raw = store6.read_own(Hook, STAGE6_HOOK)
        except ArtifactUnreadable:
            art = None
        if art is not None and art.approved is None:
            out.append(InboxItem(
                kind='approval', stage='stage6', chapter=None, rule='stage6.approve',
                what='开篇文案已产三候选，还没有人定稿——定稿是本层现役的唯一闸门',
                how=('在文案屏核对语义/机械结论后，跑 '
                     '`stage6.approve --pick N --actor <自报身份> --reason <理由> '
                     '--subject-sha256 <屏上当前 hash>`')))

    store7 = ArtifactStore(readonly_ctx(novel_dir, 'stage7.rename', code))
    if store7.exists(STAGE7_NAME_MAP):
        try:
            art7, _raw = store7.read_own(NameMap, STAGE7_NAME_MAP)
        except ArtifactUnreadable:
            art7 = None
        if art7 is not None and art7.approved is None:
            from novel.stages.stage7.rename import approval_subject_sha256
            subject = approval_subject_sha256(art7)
            out.append(InboxItem(
                kind='approval', stage='stage7', chapter=None, rule='stage7.approve',
                what='人名映射表已产出，还没有人定稿——定稿是给整份盖章',
                how=('核对新旧对照与「漏网叫法候选」后跑 '
                     '`stage7.approve --actor <自报身份> --reason <理由> '
                     f'--subject-sha256 {subject}`；人物决定变化会拒绝旧决定')))
    return out
