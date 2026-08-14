#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inbox.py —— 「待人工」六类清单的**生产者**（BE-04 第一条；HTTP 路由属第二阶段）。

它是修复路由的 `human` 落面：`deterministic` 有 `Rule.fix`、`llm` 有反哺回路，
而「只有人能修 / 该人拍板」的那些，此前**没有生产者**——六类语义在前端登记了
（`InboxScreen.KIND_META`），后端却没有任何函数产出它们。

【六类的数据源】（全部现存机制，本模块**一个判定都不新造**——照 `views/stage3.py` 纪律）
  · `invalid`    盘上 `.INVALID` 在、同名正式稿**不在**：首跑被落盘闸门拦下，在等人改
  · `rejected`   `.INVALID` 与正式稿**并存**：付费自救/人工补丁被闸门驳回，
                 正式稿仍是旧轮——「钱花了，稿丢了」。`how` 带 `_rounds/` 归档指针
                 （**UG-12 的闭环**：被驳回后下一条恢复路径写在这行字里）
  · `stalled`    stage5 最新验收里 `unresolved_rounds >= STOP_REWRITE_AFTER` 且未豁免：
                 修复已判「根因不在写手」主动跳过，等人工改稿或豁免
  · `human_only` 各层 0 成本复检里 `status=hit` 且 `remediation=human` 的结论逐条展开
                 （stage4 计划轻重告警自然入列——**UG-11 的闭环**：处置=归人判断）
  · `upstream`   `sev=hard` 且根因第一嫌疑（`owner`）在**别的层**的命中
  · `approval`   两个人定稿闸门（stage6 文案 / stage7 人名表）还没盖章
                 （⚠ 按前端契约 v1 不含 stage0——那一闸由审批状态机自己呈现）

【三条纪律】
  ⚠ **`unver` 永不入列**（`invariants.INBOX_KIND` 的纪律）：那是判据的边界不是待办。
  ⚠ **豁免不占清单**：`wontfix` 过的结论只计数（「有人看过并决定不修」≠「没人看过」）。
  ⚠ 只捕「数据说走不下去」那一族（`UpstreamMissing`/`ArtifactUnreadable`/`TerminalState`/
    `RunModeNotImplemented`——没跑到的层跳过，不编空结论）；`RefusedByStructure`
    是我们自己的 bug，**不捕**（`novel/errors.py` 开头那条：不许降级成一句温和的提示）。
"""
from __future__ import annotations

import os
import re

from contracts.invariants import NODES, STAGES, STOP_REWRITE_AFTER
from contracts.schema.api import InboxItem, InboxView
from contracts.schema.report import CheckReport
from contracts.schema.stage6 import Hook
from contracts.schema.stage7 import NameMap
from novel import paths, prov
from novel.errors import (
    ArtifactUnreadable, RunModeNotImplemented, TerminalState, UpstreamMissing,
)
from novel.orchestrator.nodes import check_nodes
from novel.orchestrator.state import readonly_ctx
from novel.stages import check_only
from novel.store import ArtifactStore

_CH_RX = re.compile(r'ch(\d+)')

#: 兜底的 0 成本人工路（有 `Rule.fix` 时用 fix 那条，更具体）。
_HUMAN_HOW = '0 成本人工路：`--apply-edit` 改内容 / `--wontfix` 记「不修」的裁决'


def inbox_view(base: str | os.PathLike[str], sid: str) -> InboxView:
    """一本书的待人工清单。**纯读**：不写一个字节、不发一个请求、不产生新判定。

    ⚠ 它对每个已移植层各跑一次 0 成本复检（stage2/4 会重算判据，大书上是秒级）
    ——第二阶段挂 HTTP 时若嫌慢，缓存加在路由层，不加在这里。
    """
    novel_dir = paths.resolve_novel_dir(base, sid)
    code = prov.code_version()
    items: list[InboxItem] = []
    wontfix_total = 0

    for stage in STAGES:
        nodes = check_nodes(stage)
        node = nodes[0] if nodes else f'{stage}.review'
        ctx = readonly_ctx(novel_dir, node, code)
        store = ArtifactStore(ctx)

        items += _invalid_items(store, stage)
        if stage == 'stage5':
            items += _stage5_chapter_invalids(store)
            stalled, wontfix_total = _stage5_stalled_and_wontfix(store)
            items += stalled

        report = _report_of(ctx, stage)
        if report is not None:
            items += _report_items(report, stage)

    items += _approval_items(novel_dir, code)

    order = {s: i for i, s in enumerate(STAGES)}
    items.sort(key=lambda it: (order.get(it.stage, 99),
                               it.chapter if it.chapter is not None else -1,
                               it.kind, it.rule))
    counts = {kind: 0 for kind in
              ('invalid', 'rejected', 'stalled', 'human_only', 'upstream', 'approval')}
    for item in items:
        counts[item.kind] += 1
    return InboxView(items=items, counts=counts, total=len(items), wontfix=wontfix_total)


def _report_of(ctx, stage: str) -> CheckReport | None:
    try:
        return check_only(stage)(ctx)
    except (UpstreamMissing, ArtifactUnreadable, TerminalState, RunModeNotImplemented):
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
        if store.exists(rel):
            rounds = sorted(store.seen_rounds(rel))
            out.append(InboxItem(
                kind='rejected', stage=stage, chapter=None, rule=node,
                what=f'{inv}：付费自救/人工补丁被落盘闸门驳回，正式稿仍是旧轮',
                how=f'历史轮次在 `{paths.rounds_dir_of(rel)}/`（已见 r{rounds[-1] if rounds else "?"}）；'
                    f'改好 `.INVALID` 后跑 `{node} --revalidate` 提升，或改判据后重判'))
        else:
            out.append(InboxItem(
                kind='invalid', stage=stage, chapter=None, rule=node,
                what=f'{inv}：首跑被落盘闸门拦下，还没有现役产物',
                how=f'改好 `.INVALID` 后跑 `{node} --revalidate` 提升（0 成本）'))
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
            how=f'改好后跑 `stage5.write --revalidate` 提升（0 成本）；'
                f'历史轮次在 `{paths.rounds_dir_of(formal)}/`'))
    return out


def _stage5_stalled_and_wontfix(store: ArtifactStore) -> tuple[list[InboxItem], int]:
    from novel.stages.stage5 import review
    wontfix_total = len(review.load_wontfix(store))
    got = review.latest_review(store)
    if got is None:
        return [], wontfix_total
    _seq, rev = got
    out: list[InboxItem] = []

    def one(chapter: int | None, finding) -> None:
        if finding.wontfix is not None:
            return
        if int(finding.unresolved_rounds or 0) < STOP_REWRITE_AFTER:
            return
        out.append(InboxItem(
            kind='stalled', stage='stage5', chapter=chapter, rule=finding.rule,
            what=finding.detail,
            how=f'连续 {finding.unresolved_rounds} 轮未消，修复已判「根因不在写手」'
                f'主动跳过——人工改稿（`stage5.write --apply-edit`）或豁免（`--wontfix`）'))

    for row in rev.chapters:
        for finding in row.findings:
            one(row.output_ordinal, finding)
    for finding in rev.book:
        one(None, finding)
    return out, wontfix_total


def _report_items(report: CheckReport, stage: str) -> list[InboxItem]:
    out: list[InboxItem] = []
    for line in report.rules:
        if line.status != 'hit':
            continue
        for finding in line.findings:
            if finding.wontfix is not None:
                continue                                   # 豁免过：计数不占清单
            stalled = int(finding.unresolved_rounds or 0) >= STOP_REWRITE_AFTER
            if stage == 'stage5' and stalled:
                continue                                   # 已由 stalled 类列出，别双份
            if line.sev == 'hard' and finding.owner not in (None, stage, 'corpus'):
                out.append(InboxItem(
                    kind='upstream', stage=stage, chapter=None, rule=line.rule,
                    what=finding.detail,
                    how=f'阻断级且根因第一嫌疑在 {finding.owner}——'
                        f'先修上游那一层，再回来重跑本层复检'))
            elif line.remediation == 'human':
                fix = line.fix
                how = (f'跑 `{fix.node} {" ".join(fix.flags)}`'
                       f'（{"付费" if fix.paid else "0 成本"}）' if fix is not None
                       else _HUMAN_HOW)
                out.append(InboxItem(
                    kind='human_only', stage=stage, chapter=None, rule=line.rule,
                    what=finding.detail, how=how))
    return out


def _approval_items(novel_dir, code: str) -> list[InboxItem]:
    """两个「人定稿」闸门。⚠ 它们**不是故障**，但确实该在这张清单上。"""
    out: list[InboxItem] = []

    store6 = ArtifactStore(readonly_ctx(novel_dir, 'stage6.hook', code))
    if store6.exists('stage6/hook.json'):
        try:
            art, _raw = store6.read_own(Hook, 'stage6/hook.json')
        except ArtifactUnreadable:
            art = None
        if art is not None and art.approved is None:
            out.append(InboxItem(
                kind='approval', stage='stage6', chapter=None, rule='stage6.approve',
                what='开篇文案已产三候选，还没有人定稿——定稿是本层现役的唯一闸门',
                how='跑 `stage6.approve --pick N`（1 起）；语义/机械判据结论在文案屏可查'))

    store7 = ArtifactStore(readonly_ctx(novel_dir, 'stage7.rename', code))
    if store7.exists('stage7/name_map.json'):
        try:
            art7, _raw = store7.read_own(NameMap, 'stage7/name_map.json')
        except ArtifactUnreadable:
            art7 = None
        if art7 is not None and art7.approved is None:
            out.append(InboxItem(
                kind='approval', stage='stage7', chapter=None, rule='stage7.approve',
                what='人名映射表已产出，还没有人定稿——定稿是给整份盖章',
                how='核对新旧对照与「漏网叫法候选」后跑 `stage7.approve`'))
    return out
