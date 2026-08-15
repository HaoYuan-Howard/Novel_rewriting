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
    ⚠ 那张账**逐层各一份**（`<stage>/wontfix.json`，2026-08-15 裁决），
    计数在本模块跨层求和；语义结论的豁免也走同一份账（`novel/wontfix.py`）。
  ⚠ 只捕「数据说走不下去」那一族（`UpstreamMissing`/`ArtifactUnreadable`/`TerminalState`
    ——没跑到的层跳过，不编空结论）；`RefusedByStructure`
    是我们自己的 bug，**不捕**（`novel/errors.py` 开头那条：不许降级成一句温和的提示）。
"""
from __future__ import annotations

from contracts.artifacts import STAGE6_HOOK, STAGE7_NAME_MAP, artifact_rel

import os
import re

from contracts.invariants import DEPTH_MODES, NODES, STAGES, STOP_REWRITE_AFTER
from contracts.schema.api import InboxItem, InboxView
from contracts.schema.report import CheckReport
from contracts.schema.stage6 import Hook
from contracts.schema.stage7 import NameMap
from novel import paths, prov, wontfix
from novel.errors import ArtifactUnreadable, TerminalState, UpstreamMissing
from novel.orchestrator.nodes import check_nodes
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
    ⇒ 从 `NODES` 派生。谎话在结构上就说不出口了，而且新入口一上线它自动跟上
    （stage2 的 `--apply-edit` 就是这么被这句话认领的）。

    ⚠ 一条都没有时**如实说没有**，不要编一个看起来像下一步的句子：
    「这一层没有 0 成本入口」与「有而我没告诉你」是两件事。
    """
    have = {flag for spec in NODES.values() if spec.stage == stage for flag in spec.flags}
    parts = [f'`{flag}` {what}' for flag, what in _HUMAN_FLAGS if flag in have]
    if not parts:
        return (f'{stage} 没有 0 成本人工入口——处置归人判断：'
                f'要么改上游重跑本层，要么这条结论就这样留在报告上')
    return '0 成本人工路：' + ' / '.join(parts)


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


def _stage5_rejected_revisions(store: ArtifactStore) -> list[InboxItem]:
    """列出修订退化证据；它与 `.INVALID` 是两种不同的 rejected。"""
    from contracts.schema.stage5 import RejectedRevision

    out: list[InboxItem] = []
    for mode in DEPTH_MODES:
        directory_rel = artifact_rel('stage5.chapter', mode=mode, nn=1).rsplit('/', 1)[0]
        directory = store.path(directory_rel)
        if not directory.is_dir():
            continue
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
                     f'`stage5.write --mode {mode} --apply-edit <chNN.txt>`，'
                     f'或在确认修复方向后用 `--revise --chapters {chapter}` 再采样')))
    return out


def _stage5_stalled(store: ArtifactStore) -> list[InboxItem]:
    """连续 N 轮未消、修复已判「根因不在写手」主动跳过的那些。

    ⚠ 豁免计数**不在这里数**：它逐层各一份账，由 `inbox_view` 跨层求和
    （从前这里顺手数了 stage5 那一份，收成跨层之后会数两次）。
    """
    from novel.stages.stage5 import review
    got = review.latest_review(store)
    if got is None:
        return []
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
                f'主动跳过——人工改稿（`stage5.write --apply-edit`）'
                f'或豁免（`stage5.review --wontfix`）'))

    for row in rev.chapters:
        for finding in row.findings:
            one(row.output_ordinal, finding)
    for finding in rev.book:
        one(None, finding)
    return out


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
                       else _human_how(stage))
                out.append(InboxItem(
                    kind='human_only', stage=stage, chapter=None, rule=line.rule,
                    what=finding.detail, how=how))
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
                how='跑 `stage6.approve --pick N`（1 起）；语义/机械判据结论在文案屏可查'))

    store7 = ArtifactStore(readonly_ctx(novel_dir, 'stage7.rename', code))
    if store7.exists(STAGE7_NAME_MAP):
        try:
            art7, _raw = store7.read_own(NameMap, STAGE7_NAME_MAP)
        except ArtifactUnreadable:
            art7 = None
        if art7 is not None and art7.approved is None:
            out.append(InboxItem(
                kind='approval', stage='stage7', chapter=None, rule='stage7.approve',
                what='人名映射表已产出，还没有人定稿——定稿是给整份盖章',
                how='核对新旧对照与「漏网叫法候选」后跑 `stage7.approve`'))
    return out
