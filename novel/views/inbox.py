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
  · `approval`   Stage4 A/B 当前 subject 及两个人定稿闸门
                 （stage6 文案 / stage7 人名表）还没盖章
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
    STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_EDIT, STAGE2_GLOBAL_REVIEW,
    STAGE6_HOOK, STAGE7_NAME_MAP,
)

import os
import re

from contracts.invariants import (
    INBOX_KIND, NODES, STAGES, STOP_REWRITE_AFTER, review_effect_of,)
from contracts.rules import REGISTRY
from contracts.schema.api import (
    ApprovalDoc, InboxDecision, InboxItem, InboxView,
    Stage6ApprovalWorkpack, Stage7ApprovalWorkpack,
)
from contracts.schema.evaluation import GlobalReview
from contracts.schema.report import CheckReport
from novel import paths, wontfix
from novel.quality.global_review import review_item_kind
from novel.errors import (
    ApprovalRequired, ArtifactUnreadable, TerminalState, UpstreamMissing,
)
from novel.orchestrator import approvals
from novel.orchestrator.inspection import (
    OperatorReadSnapshot, capture_operator_snapshot, read_surface_seal,
)
from novel.orchestrator.nodes import check_node
from novel.orchestrator.state import (
    readonly_ctx, stage2_legacy_blueprint_recovery_needed,
    stage2_frozen_v6_subject_validated,
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
    if stage == 'stage2' and rule == 'conflict_stage_coverage':
        return (
            '用 GlobalEdit 的 `conflict_stages_replace` 完整重画分段，再跑 '
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
    legacy_blueprint_recovery = stage2_legacy_blueprint_recovery_needed(ov)

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

        items += _invalid_items(
            store, stage,
            suppress_legacy_blueprint=legacy_blueprint_recovery)
        if stage == 'stage5':
            items += _stage5_chapter_invalids(store)
            items += _stage5_rejected_revisions(store)
            items += _stage5_stalled(store)

        report = snapshot.check(stage).for_view()
        if report is not None:
            items += _report_items_for_inbox(report, stage)

    items += _verification_items(novel_dir, code)
    items += _stage2_projection_recovery_items(ov)
    items += _stage2_legacy_blueprint_recovery_items(ov)
    items += _stage2_frozen_v6_disclosure_items(ov)
    items += _trim_verification_items(novel_dir, code, audits=trim_audits)
    stage6_workpack = _stage6_workpack_or_none(novel_dir, code)
    stage7_workpack = _stage7_workpack_or_none(novel_dir, code)
    items += _hook_verification_items(
        novel_dir, code, workpack=stage6_workpack, workpack_checked=True)
    items += _stage4_approval_items(
        base, sid, novel_dir, snapshot=snapshot, approval_doc=approval_doc)
    items += _approval_items(
        novel_dir, code,
        stage6_workpack=stage6_workpack,
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
        what=('Stage2 Blueprint 称名投影缺失、损坏或未绑定当前最新 '
              'formal/INVALID Global；不得把它当成整份 Global 未生成'),
        how=('跑 `stage2.global --renormalize`：只从当前 Global + MainText '
             '确定性重建投影，0 LLM、不会重采付费 Global'),
    )]


def _stage2_legacy_blueprint_recovery_items(ov) -> list[InboxItem]:
    """旧 v6 来源边只需换账，不应重采已落盘的 Blueprint。"""
    if not stage2_legacy_blueprint_recovery_needed(ov):
        return []
    return [InboxItem(
        kind='repairable', stage='stage2', chapter=None,
        rule='chapter_blueprints_legacy_inputs',
        what=('当前 ChapterBlueprints v6 仍记录旧 `{MainText, Global}` 来源边；'
              '称名投影已经 current/fresh，旧账不能证明真实消费 freshness'),
        how=('跑 `stage2.blueprint --renormalize`：只重编译并刷新来源边，'
             '0 LLM、不会重采付费 Blueprint'),
    )]


def _stage2_frozen_v6_disclosure_items(ov) -> list[InboxItem]:
    """旧书可续不等于 v7：永久披露缺失的关系权威面。"""
    if not stage2_frozen_v6_subject_validated(ov):
        return []
    return [InboxItem(
        kind='human_only', stage='stage2', chapter=None,
        rule='chapter_blueprints_frozen_v6_disclosure',
        what=(
            'ChapterBlueprints 是已由现存 Stage4A v1 truth 精确绑定的冻结 schema v6；'
            '来源与既有候选仍匹配，但当时没有 v7 narrative_relations 权威面'),
        how=(
            '只允许保留既有审阅决定或走 Stage4A 零 wire 完整章替换；不得手补关系、'
            '自动迁移、启动付费 relation repair/replan，交付必须保留本披露'),
    )]


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
            and (
                review_effect_of(review_item_kind(item.rule)) == 'disclosure'
                or (
                    item.decision is not None
                    and item.decision.generation_disposition
                    == 'quarantine_group'
                )
            ))
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


def _invalid_items(
        store: ArtifactStore, stage: str, *,
        suppress_legacy_blueprint: bool = False) -> list[InboxItem]:
    out: list[InboxItem] = []
    for rel in _stage_rels(stage):
        # 旧来源边的 live INVALID 有唯一的 0-LLM renormalize 路。若这里再产
        # 一条通用 revalidate，inbox 会同时给出两条互相冲突的操作指令。
        if suppress_legacy_blueprint and rel == STAGE2_CHAPTER_BLUEPRINTS:
            continue
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
        disposition = row.generation_disposition
        if disposition is not None:
            scope = '（仍为 UNVER；所属事实组已从写作视图确定性隔离，保留终审披露）'
        else:
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
                 'FAIL 用 typed GlobalEdit 修复；可隔离 UNVER 的 quarantine_group '
                 '不会改名为 PASS，仍在本清单和终审包披露；'
                 'subject hash 已变的旧决策会被拒绝'),
            attention=('pending' if row.state == 'pending' else 'reviewed_retained'),
            decision=(None if row.state == 'pending' else InboxDecision(
                actor=row.actor or '', reason=row.reason or '',
                subject_sha256=row.subject_sha256, state=row.state,
                decided_at=row.decided_at or '',
                generation_disposition=(
                    None if disposition is None else disposition.action),
                generation_disposition_reason=(
                    None if disposition is None else disposition.reason),
            ))))
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


def _stage6_workpack_or_none(
    novel_dir, code: str,
) -> Stage6ApprovalWorkpack | None:
    """只建一次与真正 approve 同 node/code 的 Stage6 fresh 工作包。"""
    from novel.stages.stage6 import hook

    store = ArtifactStore(readonly_ctx(novel_dir, 'stage6.approve', code))
    if not store.exists(STAGE6_HOOK):
        return None
    try:
        return hook.approval_workpack(store)
    except (ArtifactUnreadable, TerminalState, UpstreamMissing):
        return None


def _stage7_workpack_or_none(
    novel_dir, code: str,
) -> Stage7ApprovalWorkpack | None:
    """只建一次与真正 approve 同 node/code 的 Stage7 fresh 工作包。"""
    from novel.stages.stage7 import rename

    store = ArtifactStore(readonly_ctx(novel_dir, 'stage7.approve', code))
    if not store.exists(STAGE7_NAME_MAP):
        return None
    try:
        return rename.approval_workpack(store)
    except (ArtifactUnreadable, TerminalState, UpstreamMissing):
        return None


def _hook_verification_items(
    novel_dir, code: str, *,
    workpack: Stage6ApprovalWorkpack | None = None,
    workpack_checked: bool = False,
) -> list[InboxItem]:
    """每条候选都等着一次 hash 绑定的人工核验——本仓没有自动放行门。

    ⚠ 这里曾按 `assessment.human_resolvable` 过滤。`semantic_state` 收成
    `unreviewed` / `uncertain` 两态之后那个谓词恒真，过滤是空转；
    **两个状态都要人看**，差别只在「没人判过」还是「两名判官的带证据结论等着人读」。
    """
    if not workpack_checked:
        workpack = _stage6_workpack_or_none(novel_dir, code)
    if workpack is None or workpack.disposition == 'already_current':
        return []
    out: list[InboxItem] = []
    seen: set[str] = set()
    for candidate in workpack.candidates:
        subject = candidate.subject_sha256
        if subject in seen:
            continue
        seen.add(subject)
        out.append(InboxItem(
            kind='verification', stage='stage6', chapter=None,
            # ⚠ 收敛键按**位置身份**，不按策略名：同一种策略两条候选会共用一个键，
            #   而那正是「换了个位置的同类问题」被当成同一条的形状。
            rule=f'reference_boundary:{candidate.slot}',
            what=(f'候选 {candidate.index}（{candidate.approach}）当前为 '
                  f'{candidate.semantic_state}：{candidate.semantic_reason}'),
            how=(f'人工读完同一终审工作包的 A/B 与机械证据后跑 '
                 f'`stage6.approve --pick {candidate.index} '
                 f'--actor <自报身份> --reason <理由> '
                 f'--review-token {workpack.review_token}`；'
                 'token 在内部绑定 exact 工作包，长期决定只绑定实际可见内容与'
                 '显式审阅契约；候选或可见证据变化会拒绝旧决定')))
    return out


def _approval_items(
    novel_dir, code: str, *,
    stage6_workpack: Stage6ApprovalWorkpack | None = None,
    stage7_workpack: Stage7ApprovalWorkpack | None = None,
    workpacks_checked: bool = False,
) -> list[InboxItem]:
    """两个「人定稿」闸门。⚠ 它们**不是故障**，但确实该在这张清单上。"""
    out: list[InboxItem] = []

    if not workpacks_checked:
        stage6_workpack = _stage6_workpack_or_none(novel_dir, code)
        stage7_workpack = _stage7_workpack_or_none(novel_dir, code)

    if (stage6_workpack is not None
            and stage6_workpack.disposition != 'already_current'):
        out.append(InboxItem(
            kind='approval', stage='stage6', chapter=None, rule='stage6.approve',
            what=('开篇三候选的当前代码/规则/证据工作包还没有有效人工定稿——'
                  '定稿是本层现役的唯一闸门'),
            how=('先读 `approval workpack stage6 <sid> --base <base>`；选择候选 N 后跑 '
                 '`stage6.approve --pick N --actor <自报身份> --reason <理由> '
                 f'--review-token {stage6_workpack.review_token}`。'
                 '候选主体与多枚内部 SHA 由 opaque token 关闭式绑定；同一当前工作包'
                 '已有批准时本项不会发布')))

    if (stage7_workpack is not None
            and stage7_workpack.disposition != 'already_current'):
        out.append(InboxItem(
            kind='approval', stage='stage7', chapter=None, rule='stage7.approve',
            what=('人名映射当前代码/规则/人物证据工作包还没有有效人工定稿——'
                  '定稿是给整份盖章'),
            how=('核对同一工作包的新旧对照、实际命中、明确保留与残留上下文后跑 '
                 '`stage7.approve --actor <自报身份> --reason <理由> '
                 f'--review-token {stage7_workpack.review_token}`；'
                 '人物决定或可见证据变化会拒绝旧决定；仅 execution epoch '
                 '代码漂移不会要求重新签字')))
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


def _stage4_approval_items(
    base: str | os.PathLike[str], sid: str, novel_dir, *,
    snapshot: OperatorReadSnapshot | None = None,
    approval_doc: ApprovalDoc | None = None,
) -> list[InboxItem]:
    """Stage4 A/B 当前态 + 不可擦除的 B 披露审阅证据。

    workpack 是披露的唯一投影面；Inbox 只把它的 disclosure 组转成
    人工清单行，不重算 INFO / baseline / concern。当前结构决定已
    精确绑定同一 subject 时，行仍保留，只转为 ``reviewed_retained``
    并携带 typed actor/reason/hash/time；这与「从没看见」不是一个状态。
    """
    del novel_dir  # 路径只由 approvals 的 current-subject 生产口解析。
    doc = approval_doc or (
        approvals.get(base, sid)
        if snapshot is None
        else approvals.get(
            base, sid, overview=snapshot.overview, code=snapshot.code)
    )
    out: list[InboxItem] = []
    design = doc.design_review
    structure = doc.structure
    if (
        design.state in ('missing', 'expired')
        and structure.state != 'accepted'
    ):
        out.append(InboxItem(
            kind='approval', stage='stage4', chapter=None,
            rule='stage4.design',
            what=(
                'Stage4A 当前盲候选尚未逐项审阅'
                if design.state == 'missing'
                else 'Stage4A 旧审阅已不再绑定当前候选 subject'
            ),
            how=(
                '先跑 `approval workpack design <sid> --base <base>` 读完异常与'
                '全章 coverage，再用 design template/apply 提交完整 typed 决定'),
        ))
    elif design.state == 'repair_requested' and design.current_decision is not None:
        decision = design.current_decision
        chapters = [
            row.output_ordinal for row in design.current_decision.chapters
            if row.decision == 'request_repair'
        ]
        capability = {
            row.output_ordinal: row
            for row in decision.repair_capability.chapters
        }
        agent_chapters = [
            ordinal for ordinal in chapters
            if (
                bool(capability[ordinal].agent_repair_available)
                if decision.repair_capability.is_v3
                else bool(capability[ordinal].request_repair_available)
            )
        ]
        edit_chapters = [
            ordinal for ordinal in chapters
            if (
                bool(capability[ordinal].full_chapter_edit_available)
                if decision.repair_capability.is_v3 else False
            )
        ]
        routes: list[str] = []
        if agent_chapters:
            routes.append(
                f'可发 agent 的章 {agent_chapters} 按服务端当前 action token 运行 '
                '`stage4.design --review-token '
                f'{design.action_review_token}`')
        if edit_chapters:
            routes.append(
                f'章 {edit_chapters} 也可从 design immutable workpack 取该章单一 '
                'review_token，提交完整 BlindChapterEditRequest JSON，先 '
                '`stage4.design --validate-edit <路径>`，再 '
                '`stage4.design --apply-edit <路径>`（两步均为 0 次模型调用）')
        if not routes:
            routes.append('当前决定没有可执行恢复路，请刷新 immutable workpack')
        out.append(InboxItem(
            kind='approval', stage='stage4', chapter=None,
            rule='stage4.design',
            what=f'Stage4A 当前 typed 决定要求定向修订章 {chapters}',
            how=(
                '；'.join(routes)
                + '；两条路都只替换目标章，不会把单章耗尽自动升级为整书 replan；'
                '这一行只呈现已有决定，不会自动启动模型'),
        ))
    elif (
        design.state == 'strategy_replan_requested'
        and design.current_decision is not None
    ):
        out.append(InboxItem(
            kind='approval', stage='stage4', chapter=None,
            rule='stage4.design',
            what='Stage4A 当前 typed 策略决定是 request_replan',
            how=(
                '显式运行 `stage4.design --replan --review-token '
                f'{design.action_review_token}`；Inbox/Workpack 只呈现'
                '已有决定，不会代为发起付费请求'),
        ))

    if (
        structure.state in ('missing', 'expired')
        and design.state != 'suspended'
    ):
        out.append(InboxItem(
            kind='approval', stage='stage4', chapter=None,
            rule='stage4.plan',
            what=(
                'Stage4B 当前正式计划还没有全章 typed 结构决定'
                if structure.state == 'missing'
                else 'Stage4B 旧结构决定已不再绑定当前 Plan+B subject'
            ),
            how=(
                '先跑 `approval workpack structure <sid> --base <base>` 核对阻断、'
                'UNVER、INFO 与全章 coverage，再用 structure template/apply 提交'),
        ))
    elif (
        structure.state == 'replan_requested'
        and structure.current_decision is not None
    ):
        chapters = [
            row.output_ordinal for row in structure.current_decision.chapters
            if row.decision == 'request_replan'
        ]
        out.append(InboxItem(
            kind='approval', stage='stage4', chapter=None,
            rule='stage4.plan',
            what=f'Stage4B 当前结构决定要求重规划章 {chapters}',
            how=(
                '回到 Stage4A 显式提交 request_replan 并另行启动；'
                '当前清单不从决定自动 spawn'),
        ))

    if (
        design.state == 'suspended'
        and structure.state in ('unavailable', 'missing', 'expired')
    ):
        # raw A 已审完但 frozen validation authority 永久不足；既没有可读 B
        # workpack，也没有新的 reviewer 动作。继续调用 structure_workpack 只会
        # 把同一兼容边界重新包装成异常或审批待办。
        return out

    try:
        if snapshot is None:
            workpack = approvals.structure_workpack(base, sid)
        else:
            # 同一调用已经跑过 Stage4 生产复检；不可用就维持上面的 A/B
            # 当前态，不另起一次 check 把七层快照膨胀成第八次判据执行。
            report = snapshot.check('stage4').for_view()
            if report is None:
                return out
            workpack = approvals.structure_workpack(
                base, sid,
                overview=snapshot.overview,
                code=snapshot.code,
                check_report=report,
            )
    except (UpstreamMissing, TerminalState, ApprovalRequired):
        # Plan/B 还没有到可组 workpack 的相位；上面的 A/B state
        # 仍是当前可读结论。载体损坏则不在此处吞，必须 fail closed。
        return out
    disclosure = next(
        group for group in workpack.groups if group.group == 'disclosure')
    current_decision = structure.current_decision
    decision_is_current_for_workpack = (
        current_decision is not None
        and current_decision.review_token == workpack.review_token
    )
    decision_by_chapter = (
        {
            row.output_ordinal: row.decision
            for row in current_decision.chapters
        }
        if current_decision is not None else {}
    )
    for item in disclosure.items:
        retained = decision_is_current_for_workpack
        evidence = None
        if retained and current_decision is not None:
            evidence = InboxDecision(
                actor=current_decision.actor,
                reason=current_decision.reason,
                subject_sha256=workpack.subject_sha256,
                state=(
                    decision_by_chapter.get(item.chapter_ordinal)
                    if item.chapter_ordinal is not None
                    else structure.state
                ) or structure.state,
                decided_at=current_decision.decided_at,
            )
        out.append(InboxItem(
            kind='human_only',
            stage='stage4',
            chapter=item.chapter_ordinal,
            rule=item.rule or item.code,
            what=item.detail,
            how=(
                '当前 hash-bound 结构决定已覆盖该披露；保留证据供审计'
                if retained
                else '在结构 workpack 中阅读该披露，并用完整全章 '
                'structure typed decision 表达处置'
            ),
            attention='reviewed_retained' if retained else 'pending',
            decision=evidence,
        ))
    return out
