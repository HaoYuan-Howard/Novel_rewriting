#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
review.py —— `stage5.review`：**全书验收**（0 成本，不调 LLM）。**判成品，不判计划。**

两条路：
    默认            算一份新的 `stage5/review_<n>.json`（`<n>` = 盘上最大序号 +1）
    `--check-only`  **读产物不重算**——所以要先报「我判的那几份稿还对不对得上」
    `--wontfix`     人的「这条我不修」，落 `stage5/wontfix.json`（**不产验收报告**）

⚠⚠ **本层徽章结构上永远不可能是 `pass`**：判据表有 **5 条 `unver`**，其中
`must_hold_unver` 每本都产（全书那一组按定义查不了文面）⇒ 最好情况是 `amber`、退出码 0。
**这不是缺陷，是本层「能查的都查了，查不了的写在脸上」的可见形态**，与 stage4 相反
（那一层没有一条 `unver`，徽章可以是真绿）。

⚠ **判据表只有一张**，与落盘闸门共用（`checks.chapter_findings`）。本相的全部增量
只有事件落点 / 材料形态 / 书级那几条。
"""
from __future__ import annotations

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_GLOBAL_GROUNDING,
    STAGE4_WRITER_CONTEXT, STAGE5_REVIEW, artifact_rel,
)
from contracts.schema import artifact_spec

import collections
import glob
import json
import os
import re
from dataclasses import dataclass
from typing import Any

from contracts.rules import REGISTRY
from contracts.schema.common import Finding, WontFixEntry, WontFixNote
from contracts.schema.report import CheckReport, finding
from contracts.schema.stage2 import GlobalGrounding
from contracts.schema.stage4 import WriterContext
from contracts.schema.stage5 import (
    ContextSource, ContextSourceDetail, DraftSources, Review, ReviewChapter, ReviewProvenance,
    ReviewSources,
)
from novel import prov, wontfix
from novel.core.evidence import without_global_evidence
from novel.core.measure import body_style_metrics, measure_body
from novel.core.naming import project_character_names
from novel.errors import (
    ArtifactUnreadable, BlockingFindingsUnresolved, GateBlocked, TerminalState,
    UpstreamMissing,
)
from novel.llm import stage5_runs
from novel.report.builder import build
from novel.run import RunContext
from novel.stages.stage5 import checks
from novel.stages.stage5 import measure as M
from novel.snapshot_clone import readonly_snapshot_clone
from novel.store import ArtifactStore

MAIN_TEXT = STAGE1_MAIN_TEXT
GLOBAL_GROUNDING = STAGE2_GLOBAL_GROUNDING
WRITER_CONTEXT = STAGE4_WRITER_CONTEXT
#: ⚠ 从 `novel/wontfix.py` 派生，不手写第二个字符串——两层共用同一张模板。
WONTFIX = wontfix.rel_of('stage5')

#: `stage5/review_<n>.json`。`<n>` 由盘上已有的最大序号 **+1**。
REVIEW_TPL = STAGE5_REVIEW
_REVIEW_RX = re.compile(r'^review_(\d+)\.json$')


# ─────────────────────────────────────────────────────────────────────────────
# 一、上游
# ─────────────────────────────────────────────────────────────────────────────

def load_book(ctx: RunContext, store: ArtifactStore) -> M.Book:
    """三份上游 → `Book`。**写作相与验收相共用这一个入口。**

    ⚠⚠ **secrets 直接读 stage2 全量，不从各章 `facts.secrets` 取并集**：
    并集的成员资格恰好等于「至少被某一章下发过」，于是一条**全书从未下发**的 secret 会让
    `secret_leak` 的关键片段成空列表 ⇒ **这条 secret 的泄漏检查从此永远不执行**，
    而判据照常输出一个「没报违规」。**收窄下发面是修复，收窄检测器是削弱。**
    """
    for rel in (MAIN_TEXT, GLOBAL_GROUNDING, WRITER_CONTEXT):
        if not store.exists(rel):
            raise UpstreamMissing(f'{rel} 不在盘上——先跑 {rel.split("/")[0]}')
    main = store.read(_main_text_model(), MAIN_TEXT)
    from novel.stages.stage2 import global_review as stage2_global_review
    gg = stage2_global_review.generation_grounding(store)
    wc = store.read(WriterContext, WRITER_CONTEXT)
    measurable = body_style_metrics(measure_body(main))
    global_projected = without_global_evidence(gg.model_dump(by_alias=True))
    assert isinstance(global_projected, dict)
    source_text = {s.chapter_ordinal: (s.text or '') for s in main.root}
    context_payload = wc.model_dump(by_alias=True)
    for entry in context_payload.get('chapters') or []:
        try:
            M.assert_writer_context_v4(entry, M.chapter_basis(entry, source_text))
        except ValueError as exc:
            raise TerminalState(
                f'{exc}。请免费重建 stage4.context，不要继续用漂移的任务书。'
            ) from exc
    characters = ((global_projected.get('entities') or {}).get('characters') or {})
    name_registry, _substitutions, unattested = project_character_names(
        characters, '\n'.join(source_text.values()))
    if unattested:
        raise TerminalState(
            f'Stage5 无法从当前 Stage1/2 派生有据名字基线：{unattested[:8]}。'
            '先修正 Stage2 人物称呼并免费重建 stage4.context。')
    return M.Book(
        title=ctx.title,
        ctx=context_payload,
        measurable=measurable,
        source_text=source_text,
        secrets=((global_projected.get('canon') or {}).get('secrets') or []),
        characters=name_registry,
    )


def _main_text_model():
    from contracts.schema import ARTIFACTS
    return ARTIFACTS[MAIN_TEXT]


# ─────────────────────────────────────────────────────────────────────────────
# 二、序号与「这条我不修」
# ─────────────────────────────────────────────────────────────────────────────

def review_seqs(store: ArtifactStore) -> list[int]:
    """盘上已有的验收报告序号（升序）。"""
    out: list[int] = []
    for p in glob.glob(os.path.join(store.path('stage5'), 'review_*.json')):
        if (m := _REVIEW_RX.match(os.path.basename(p))) is not None:
            out.append(int(m.group(1)))
    return sorted(out)


def latest_review(store: ArtifactStore) -> tuple[int, Review] | None:
    """最近一份**当前 schema**验收报告。没有 ⇒ `None`。

    v1 Review 是双臂折平前的历史主体，不能参与 v2 收敛计数；但 producer
    `stage5.review` 又必须能在它后面零成本写出新一轮。因此只在这个“上轮收敛基线”
    入口忽略最新的低版本；`review_status` 仍会把它明确报成 `legacy_incompatible`。
    """
    seqs = review_seqs(store)
    if not seqs:
        return None
    n = seqs[-1]
    rel = REVIEW_TPL.format(n=n)
    if store.schema_version(rel) != _current_version(rel):
        return None
    art, _raw = store.read_own(Review, rel)
    return n, art


@dataclass(frozen=True)
class ReviewStatus:
    """当前 Review 能否代表当前 canonical Stage5 主体。

    ``fresh`` 的含义比通用 provenance 更完整：同一结果还核 RULES、计划章集合、当前正文
    投影与 ``judged`` 键和值。调用方只消费这一份结论，禁止
    state/queue/wontfix/revise/approval 各自拼一套“差不多”的 freshness。
    """

    state: str  # fresh | stale | unknown | missing | legacy_incompatible
    reasons: tuple[str, ...]
    review_rel: str | None = None
    seq: int | None = None
    review: Review | None = None

    @property
    def fresh(self) -> bool:
        return self.state == 'fresh'


def _current_version(rel: str) -> int | None:
    spec = artifact_spec(rel)
    return spec.current_version if spec is not None else None


def _legacy_versioned_paths(store: ArtifactStore, latest_rel: str | None) -> list[str]:
    """识别仍在 live 位置的旧 Review/trim；旧序号 Review 只要最新已重建就留作历史。"""
    candidates: list[str] = []
    if latest_rel is not None:
        candidates.append(latest_rel)
    for pattern in ('trim_audit_ch*.json', 'trim_resolution_ch*.json'):
        for path in glob.glob(os.path.join(store.path('stage5'), pattern)):
            candidates.append(f'stage5/{os.path.basename(path)}')
    old: list[str] = []
    for rel in sorted(set(candidates)):
        found = store.schema_version(rel)
        current = _current_version(rel)
        if current is not None and (found is None or found < current):
            old.append(f'{rel}:v{found if found is not None else "unversioned"}->v{current}')
    return old


def legacy_versioned_paths(store: ArtifactStore) -> list[str]:
    """列出仍占据 live 位置的旧 Review/trim schema，供所有只读视图共用。

    Review 只看最新序号：较早轮次本来就是历史；trim 没有轮次，任何旧版 live
    文件都会让当前人工销账语义变得不可信。
    """
    seqs = review_seqs(store)
    latest_rel = REVIEW_TPL.format(n=seqs[-1]) if seqs else None
    return _legacy_versioned_paths(store, latest_rel)


def review_status(ctx: RunContext, store: ArtifactStore | None = None) -> ReviewStatus:
    """一次关闭式判定：旧布局、旧 schema、RULES、上游或正文漂移都不 fresh。"""
    store = store or ArtifactStore(ctx)
    if getattr(ctx, 'readonly', False):
        return readonly_snapshot_clone(
            ctx, ('stage5-review-status',),
            lambda: _review_status_uncached(ctx, store),
        )
    return _review_status_uncached(ctx, store)


def _review_status_uncached(
    ctx: RunContext, store: ArtifactStore,
) -> ReviewStatus:
    legacy_layout = M.legacy_stage5_paths(store)
    seqs = review_seqs(store)
    seq = seqs[-1] if seqs else None
    rel = REVIEW_TPL.format(n=seq) if seq is not None else None
    legacy_versions = legacy_versioned_paths(store)
    if legacy_layout or legacy_versions:
        details = [*(f'旧双臂路径:{path}' for path in legacy_layout[:8]),
                   *(f'旧 schema:{path}' for path in legacy_versions[:8])]
        return ReviewStatus(
            'legacy_incompatible', tuple(details), review_rel=rel, seq=seq)
    if rel is None:
        return ReviewStatus(
            'missing', ('盘上没有 Stage5 Review',), review_rel=None, seq=None)

    found_version = store.schema_version(rel)
    current_version = _current_version(rel)
    if current_version is not None and found_version != current_version:
        state = ('legacy_incompatible'
                 if found_version is None or found_version < current_version else 'unknown')
        return ReviewStatus(
            state,
            (f'{rel} schema v{found_version} != current v{current_version}',),
            review_rel=rel, seq=seq)
    try:
        rev, _raw = store.read_own(Review, rel)
    except ArtifactUnreadable as exc:
        return ReviewStatus('unknown', (f'{rel} 读不了：{exc}',), review_rel=rel, seq=seq)

    stale: list[str] = []
    unknown: list[str] = []
    try:
        freshness = store.freshness(rel)
    except (ArtifactUnreadable, UpstreamMissing) as exc:
        unknown.append(f'Review provenance 判不了：{exc}')
        freshness = None
    if freshness is not None and freshness.state == 'stale':
        changed = ','.join(edge.key for edge in freshness.stale_edges) or freshness.reason
        stale.append(f'Review 上游已变：{changed}')
    elif freshness is not None and freshness.state == 'unknown':
        unknown.append(f'Review 上游来源未知：{freshness.reason}')

    recorded_rules = ((rev.provenance.prompts or {}).get('RULES')
                      if rev.provenance is not None else None)
    expected_rules = prov.fingerprint(prov.rules_fingerprint('stage5'))
    if recorded_rules is None:
        unknown.append('Review 没有 RULES 指纹')
    elif recorded_rules != expected_rules:
        stale.append(f'RULES 已变：{recorded_rules}->{expected_rules}')

    try:
        by_ord, _invalids, judged_now = M.load_drafts(store, require_current=False)
    except (ArtifactUnreadable, UpstreamMissing, TerminalState) as exc:
        return ReviewStatus(
            'unknown', tuple([*stale, *unknown, f'canonical 成稿读不了：{exc}']),
            review_rel=rel, seq=seq, review=rev)

    review_rows = {row.output_ordinal: row for row in rev.chapters}
    row_paths = {row.relpath for row in rev.chapters if row.relpath is not None}
    current_paths = set(judged_now)
    judged_paths = set(rev.judged)
    if row_paths != judged_paths:
        stale.append(f'Review chapters.relpath 与 judged 键不一致：{sorted(row_paths ^ judged_paths)}')
    if judged_paths != current_paths:
        stale.append(f'judged 主体集合已变：{sorted(judged_paths ^ current_paths)}')
    if set(by_ord) != {jd.output_ordinal for jd in judged_now.values()}:
        unknown.append('canonical 章节键与 judged.output_ordinal 自相矛盾')
    for path in sorted(judged_paths & current_paths):
        was = rev.judged[path]
        now = judged_now[path]
        if was.output_ordinal != now.output_ordinal or was.consumed != now.consumed:
            stale.append(f'{path} 正文投影已变')
        row = review_rows.get(was.output_ordinal)
        if row is None or row.relpath != path or row.draft_text_sha1 != now.consumed.text_sha1:
            stale.append(f'{path} Review 章行没有绑定当前正文摘要')
        try:
            chapter_fresh = store.freshness(path)
        except (ArtifactUnreadable, UpstreamMissing) as exc:
            unknown.append(f'{path} 来源判不了：{exc}')
        else:
            if chapter_fresh.state == 'stale':
                stale.append(f'{path} 上游已变：{chapter_fresh.reason}')
            elif chapter_fresh.state == 'unknown':
                unknown.append(f'{path} 上游来源未知：{chapter_fresh.reason}')

    # 只有通用上游仍 fresh 时才模型化当前 writer_context；已知 stale 时继续读只会被
    # store 的生产闸拒绝，而且不会增加一条比“上游已变”更有用的理由。
    if freshness is not None and freshness.state == 'fresh':
        try:
            book = load_book(ctx, store)
        except (ArtifactUnreadable, UpstreamMissing, TerminalState) as exc:
            unknown.append(f'当前计划读不了：{exc}')
        else:
            plan = set(book.chapters)
            if set(review_rows) != plan:
                stale.append(f'Review 章集合与当前计划不一致：{sorted(set(review_rows) ^ plan)}')

    if stale:
        return ReviewStatus('stale', tuple(dict.fromkeys([*stale, *unknown])), rel, seq, rev)
    if unknown:
        return ReviewStatus('unknown', tuple(dict.fromkeys(unknown)), rel, seq, rev)
    return ReviewStatus('fresh', (), rel, seq, rev)


def require_fresh_review(ctx: RunContext, store: ArtifactStore | None = None) -> ReviewStatus:
    """付费返工、豁免、审批等消费入口的共同关闭式闸。

    ``review_status`` 为验证 Review 会读完整 canonical 主体；这些读取是
    **这道闸的校验材料**，不是下游 producer 的正文输入。隔离作用域防止这些读取
    泄漏进 Stage6/7 的来源账；真正构造产物时读到的上游仍由各节点照常自动记账。
    """
    with ctx.validation_reads():
        status = review_status(ctx, store)
    if status.fresh:
        return status
    reason = '；'.join(status.reasons[:6]) or status.state
    raise TerminalState(
        f'Stage5 Review 当前状态是 {status.state}：{reason}。'
        '旧结论不得驱动豁免、付费返工或审批；请先零成本运行 `stage5.review` 重算当前报告')


def require_accepted_review(ctx: RunContext, store: ArtifactStore | None = None) -> ReviewStatus:
    """正式下游的共同闸：Review 不仅 fresh，而且当前 Stage5 主体可接受。"""
    store = store or ArtifactStore(ctx)
    status = require_fresh_review(ctx, store)
    assert status.review is not None

    invalids = sorted(glob.glob(os.path.join(store.path('stage5'), 'ch*.INVALID.json')))
    if invalids:
        raise GateBlocked(
            f'Stage5 仍有 {len(invalids)} 份 `.INVALID` 未裁决，不能进入正式下游',
            invalid_path=str(store.path('stage5')),
            hint='stage5.write --revalidate --actor <id> --reason <why>（0 成本提升），'
                 '或 stage5.write --apply-edit <chNN.json 路径> 继续人工修复')

    missing = [row.output_ordinal for row in status.review.chapters if row.relpath is None]
    if missing:
        raise UpstreamMissing(
            f'Stage5 Review 仍缺正式章稿：{missing}',
            hint='先用 stage5.write 补齐计划中的缺章，再零成本重跑 stage5.review')

    findings = [f for row in status.review.chapters for f in row.findings]
    findings.extend(status.review.book)
    hard = sorted({f.rule for f in findings if f.sev == 'hard'})
    if hard:
        raise BlockingFindingsUnresolved(
            f'Stage5 当前 Review 仍有阻断级判定 {hard}，不能批准、组装或交付',
            rules=hard,
            hint='按 Review 定位后用 stage5.write --apply-edit <chNN.json 路径> 修复，'
                 '或定向重写该章；随后零成本重跑 stage5.review')
    return status


def load_wontfix(store: ArtifactStore) -> dict[str, WontFixEntry]:
    return wontfix.load(store, 'stage5')


#: 收敛键的构造器。⚠ 全链只有 `novel/wontfix.py::key` 一份实现，本名是转发。
wontfix_key = wontfix.key


def mark_wontfix(ctx: RunContext, spec: str) -> CheckReport:
    """`--wontfix`：把一条「这条我不修」记进 `stage5/wontfix.json`。**不产验收报告。**

    机制在 `novel/wontfix.py`（全链一份，stage6 用的是同一套）；本函数只负责
    交出**这一层当下的结论池**与审计账。

    三条约束，一条都不许松：
      · **`why` 必填**——写不出理由的豁免，与「没看过」在盘上没有区别；
      · **不给不存在的结论发豁免**——当前结论池里没有那条 ⇒ **显式终态**（缺省是
        「不知道」，不许猜）；
      · **豁免不删结论**——照常出现在报告与界面上，只是不进反哺、不占「待人工」，并单独计数。
    """
    with stage5_runs.book_commit_ownership(ctx.novel_dir):
        store = ArtifactStore(ctx)
        current = require_fresh_review(ctx, store)
        assert current.seq is not None and current.review is not None
        seq, rev = current.seq, current.review

        def pool_of(ordinal: int | None) -> list[Finding]:
            if ordinal is not None:
                return list(next((c.findings for c in rev.chapters
                                  if c.output_ordinal == ordinal), []))
            return list(rev.book)

        wontfix.mark(store, 'stage5', spec, pool_of=pool_of, at=f'review_{seq}')
        return _report_of_review(ctx, store, rev)


# ─────────────────────────────────────────────────────────────────────────────
# 三、算一份验收报告
# ─────────────────────────────────────────────────────────────────────────────

def _downgrade(f: Finding, **kw: Any) -> Finding:
    """改一条结论的 `actionable` / `identity_*` / `wontfix`。**走 `model_validate` 重新过闸**
    ——`model_copy(update=)` 会绕开 `Finding` 那三条后置条件。"""
    return Finding.model_validate({**f.model_dump(by_alias=True), **kw})


def _context_source(store: ArtifactStore, book: M.Book) -> ContextSource:
    """`writer_context` 这一条上游的新鲜度。

    ⚠⚠ **两套新鲜度机制并存，而本轮裁决是「保留两个字段、只算一次」**（2026-08-12）：
    这一格是 `store.freshness()` 的**投影**，不是第二次计算。各算一份的后果很具体——
    报告第一节说 `fresh`、产物里这一格说 `stale`，而两边都不出声。
    """
    fr = store.freshness(WRITER_CONTEXT)
    return ContextSource(
        state='fresh' if fr.state == 'fresh' else ('stale' if fr.state == 'stale' else 'unknown'),
        detail=[ContextSourceDetail(key=e.key, was=e.recorded_file, now=e.now_file)
                for e in fr.stale_edges],
        code=((book.ctx.get('_provenance') or {}).get('code')))


def build_review(ctx: RunContext, store: ArtifactStore, book: M.Book) -> Review:
    """算出整份验收（**只读盘、不写盘**）。"""
    by_ord, invalids, judged = M.load_drafts(store)
    prev = latest_review(store)

    # ── ① 先报来源，再报判定 ────────────────────────────────────────────────
    #    对不上时报出来的一堆违规是「拿新尺子量旧布」，不说破就会被当成一次回归。
    stale: list[str] = []
    no_prov: list[str] = []
    for rel in sorted(judged):
        fr = store.freshness(rel)
        if fr.reason == 'no_provenance' or fr.reason == 'no_inputs':
            no_prov.append(rel)
        elif fr.state == 'stale':
            stale.append(rel)
    sources = ReviewSources(
        writer_context=_context_source(store, book),
        # ⚠ 这一半原型**只在非空时才报**，而 `Q-1` 要求**空也要报**。
        drafts=DraftSources(stale=stale, no_prov=no_prov))

    # ── ② 逐章 ──────────────────────────────────────────────────────────────
    rows: list[ReviewChapter] = []
    bodies: dict[int, str] = {}
    inv_numbers: list[tuple[int, str, list[str]]] = []
    inv_names: list[tuple[int, list[str]]] = []
    land: collections.Counter = collections.Counter()
    ident_hits: collections.Counter = collections.Counter()

    for o in sorted(book.chapters):
        entry = book.chapters[o]
        rec = by_ord.get(o)
        basis = M.chapter_basis(entry, book.source_text)
        assert (basis.source_ordinal is not None and basis.source_chars is not None
                and basis.budget_chars is not None)
        base = {'output_ordinal': o,
                'from_source_ordinals': [basis.source_ordinal],
                'budget_chars': basis.budget_chars,
                'source_chars': basis.source_chars}
        if rec is None:
            rows.append(ReviewChapter(**base, findings=checks.with_output_ordinal([finding(
                'draft_missing',
                detail=f'计划里有第 {o} 章，stage5 没有它的成稿——'
                       f'**本章的全部判定都没有跑，不是跑了没问题**',
                evidence=[str(o)])], o)))
            continue
        fs: list[Finding] = []
        body = rec.get('text') or ''
        bodies[o] = body
        mat = _material(entry, book)
        # `rec['segments']` 是 Chapter 模型 dump 出来的原件；写作相与 Full Review
        # 都把它交给同一个 segment-event/章尾读数入口。
        fs += checks.chapter_findings(body, entry, book, given=mat,
                                      segments=rec.get('segments'))
        extra, one_land = checks.review_chapter_findings(body, entry, book)
        fs += extra
        land.update(one_land)

        ident = checks.identity_rules(entry, book)
        for r_ in ident:
            ident_hits[r_] += 1
        for f in fs:
            if f.rule == 'invented_number':
                inv_numbers.append((o, body, list(f.evidence)))
            elif f.rule == 'invented_name':
                inv_names.append((o, list(f.evidence)))
        # 【逐章豁免】源章自己就触发这条规则时，成稿触发它**不携带任何信息**——
        # 那是这本书章间的自然波动，不是写手漂了。把它反哺回去，写手会被逼着把这一章
        # 改得比原作还「像这本书的平均值」，也就是**照它修会让产物更差**。
        # ⚠ **只降 `actionable`，不删这条结论**：「查过、但这一章原文自己也这样」
        #   与「没查」必须分得开。
        fs = [_downgrade(f, identity_hit=True, actionable=False)
              if (f.rule in ident and f.rule not in checks.IDENTITY_NA) else f
              for f in fs]
        rows.append(ReviewChapter(
            **base, relpath=rec['_relpath'],
            # ⚠ **两把指纹各管一件事，这是被一次误判逼出来的**：来源账、`warns`、归一
            #   这类 0 成本元数据修复会改整份文件的指纹，但写手**一个字都没再写**——
            #   拿整份指纹去推进收敛计数，等于把一次重判记成「写手又试了一次」。
            draft_round=judged[rec['_relpath']].round,
            draft_sha1=judged[rec['_relpath']].sha1,
            draft_text_sha1=prov.fingerprint(body),
            chars=M.chars_of(body), findings=checks.with_output_ordinal(fs, o)))

    # ── ③ 书级 ──────────────────────────────────────────────────────────────
    row_dicts = [r.model_dump(by_alias=True) for r in rows]
    book_fs = checks.book_findings(book, rows=row_dicts, bodies=bodies,
                                   inv_numbers=inv_numbers, inv_names=inv_names)

    # ── ③.5 恒等基线的**书级闸门**（逐章豁免之外还要这一道）────────────────────
    #
    # 两道闸问的是**不同的问题**，都要：逐章问「这一条 finding 被本章源文的同一现象
    # 混淆了吗」；书级问「这条判据在这本书上，比它自己的**基础发生率**强吗」。
    # 只做逐章会漏掉这种情形：源文 3/8 章命中、成稿也 3/8 章命中，**但是不同的章**——
    # 逐章看每条都「干净」，而整体上这条判据什么也没测出来。
    # 【两边都按「命中了几章」数，不按「几条 finding」数】`ident_hits` 是逐章去重的，
    # 拿一个「条数」去和它比，同一章报两条的规则会凭空显得更强。
    hit_rules = collections.Counter(r_ for row in rows for r_ in {f.rule for f in row.findings})
    dead = {r_ for r_ in set(ident_hits) - checks.IDENTITY_NA - checks.IDENTITY_UPSTREAM
            if ident_hits[r_] >= hit_rules.get(r_, 0)}
    rows = [row.model_copy(update={'findings': [
        _downgrade(f, actionable=False, identity_dead=True)
        if (f.rule in dead and f.actionable) else f for f in row.findings]}) for row in rows]

    # A trim audit still bound to this exact draft is already the authoritative
    # conclusion and closure path for its over-length text.  Do not emit a second
    # ``length_over`` finding for the same root cause; the verification item
    # carries the explicit current length/target and the audit before hash.
    # ⚠ ``current_trim_audits`` **含已销账的那一档**（待办那一侧才用
    #   ``current_unresolved_audits``）：销账关掉的是待办，不是「这一稿确实超限」
    #   这个事实；换成窄的那个集合，下一轮 Review 会把人刚关掉的决定重新打开。
    from novel.stages.stage5 import trim_resolution
    current_trim = set(trim_resolution.current_trim_audits(store))
    rows = _merge_length_over_into_trim(rows, current_trim)

    # ── ③.6 人的「这条我不修」──────────────────────────────────────────────
    wf = load_wontfix(store)
    rows, book_fs = _apply_wontfix(rows, book_fs, wf)

    # ── ④ 收敛 ──────────────────────────────────────────────────────────────
    rows, book_fs = _converge(rows, book_fs, prev[1] if prev else None)

    # ── ⑤ 基线（判据 31：没有基线数字的判据，等于不知道自己在说什么）───────────
    nch = len([r for r in rows if r.relpath])
    # 这份确定性验收的恒等基线只覆盖机械判据。语义判据有自己按审判产物
    # 记录的覆盖账；把它们塞进这里会伪造出「0/n 章命中」的机械基线，
    # 还会让未运行的付费审判看起来像已经跑过。
    conclusive = sorted(r.rule for r in REGISTRY.of_stage('stage5')
                        if r.sev in ('hard', 'info') and r.check_kind == 'mechanical')
    # baseline 的单位明写的是「命中了几章」，不是 finding 条数。同一章既有
    # 句型漂移又有段型漂移时会产两条 `band_drift`；按条数会报出 20/15 章。
    hits = collections.Counter(rule for row in rows for rule in {f.rule for f in row.findings})
    baselines = {r_: f'{hits.get(r_, 0)}/{nch}' for r_ in conclusive}
    baselines['_fact_landing'] = (
        f"{land['ok']}/{land['ok'] + land['missed']} 事实有落点"
        f"（另 {land['noatom']} 事实提不出原子、{land['ident']} 事实原子提错，均未检查）")
    baselines_identity = {r_: f'{ident_hits.get(r_, 0)}/{nch}' for r_ in conclusive}

    counts: collections.Counter = collections.Counter()
    for f in [f for r in rows for f in r.findings] + book_fs:
        counts[f.sev] += 1

    return Review(
        sources=sources, judged=judged, chapters=rows, book=book_fs,
        baselines=baselines, baselines_identity=baselines_identity, counts=dict(counts),
        _provenance=ctx.free_provenance(ReviewProvenance,
                                        prompts={'RULES': prov.rules_fingerprint('stage5')}),
    )


def _merge_length_over_into_trim(
    rows: list[ReviewChapter],
    current_trim: set[int],
) -> list[ReviewChapter]:
    """Remove duplicate ``length_over`` findings owned by a current trim audit."""
    merged: list[ReviewChapter] = []
    for row in rows:
        if row.output_ordinal not in current_trim:
            merged.append(row)
            continue
        findings = [hit for hit in row.findings if hit.rule != 'length_over']
        merged.append(row.model_copy(update={'findings': findings}))
    return merged


def _material(entry: dict[str, Any], book: M.Book) -> str:
    """该章写手实际看见的文字；Stage5 只有这一份正式下发面。"""
    from novel.stages.stage5 import prompts
    return prompts.build_material(entry, book).given


def _apply_wontfix(rows: list[ReviewChapter], book_fs: list[Finding],
                   wf: dict[str, WontFixEntry]) -> tuple[list[ReviewChapter], list[Finding]]:
    """**只降 `actionable` 并打标，不删结论**：豁免过的条目照常列在报告与界面上，
    只是不再进反哺、也不再算进「待人工」。**「有人看过并决定不修」与「没人看过」
    必须分得开**——那正是加这个机制的全部理由。

    """
    return ([r.model_copy(update={'findings': wontfix.applied(
                r.findings, wf, ordinal=r.output_ordinal)}) for r in rows],
            wontfix.applied(book_fs, wf))


def with_current_wontfix(store: ArtifactStore, rev: Review) -> Review:
    """把当前独立 wontfix 决定账叠加到一份 Review 的只读投影。

    ``stage5.review --wontfix`` 刻意只写 ``stage5/wontfix.json``，不改写也不
    新产一轮 Review。因此报告、HTTP view 等每个读出口都必须共用这里，不能把
    盘上 Review 自带的旧 ``Finding.wontfix`` 当成当前决定面。
    """
    rows, book_fs = _apply_wontfix(
        list(rev.chapters), list(rev.book), load_wontfix(store))
    if rows == list(rev.chapters) and book_fs == list(rev.book):
        return rev
    return rev.model_copy(update={'chapters': rows, 'book': book_fs})


def _converge(rows: list[ReviewChapter], book_fs: list[Finding], prev: Review | None
              ) -> tuple[list[ReviewChapter], list[Finding]]:
    """给每条结论标 `new` / `unresolved_N` / `same_draft`，并补一条收敛账。

    键是 `(章, rule, evidence_key)`。**只按 `rule` 去重是不够的**：修好了第 1 句 verbatim
    却丢了第 2 句时 rule 集合一个字没变，差集为空，**一轮把问题换了个位置会被读成「已修复」**。

    ⚠⚠ **计数只在稿子真的变过时才前进**（`R-8.4`）。拿同一份成稿重跑一次验收（改判据、
    补上游、或者手滑跑了两遍）**不是「写手又试了一次没修好」**——那时把计数 +1，
    等于让一次 **0 成本重算**把「停止重写」的闸门推到跟前。
    判别用**正文**指纹 `draft_text_sha1`；`draft_sha1` 是整份 JSON 的指纹，只管来源与过期。
    ⚠ **`converge` 由「稿子变没变」决定，不由计数值决定**：拿 `n` 去判会让一条
    「重写过一次、还没消」的结论在重跑验收时从 `same_draft` 翻成 `unresolved_1`，
    而那一轮写手根本没落笔。
    """
    old: dict[tuple[int | None, str, str], Finding] = {}
    old_text: dict[int | None, str | None] = {}
    if prev is not None:
        for r in prev.chapters:
            old_text[r.output_ordinal] = r.draft_text_sha1
            for f in r.findings:
                old[(r.output_ordinal, f.rule, f.evidence_key)] = f
        for f in prev.book:
            old[(None, f.rule, f.evidence_key)] = f
    now_text = {r.output_ordinal: r.draft_text_sha1 for r in rows}

    def rewritten(o: int | None) -> bool:
        if o is None:                      # 书级条目跟着「有没有任何一章重写过」走
            return any(rewritten(k) for k in now_text)
        return now_text.get(o) != old_text.get(o)

    resolved = dict(old)

    def mark(o: int | None, f: Finding) -> Finding:
        key = (o, f.rule, f.evidence_key)
        was = old.get(key)
        resolved.pop(key, None)
        if was is None:
            return _downgrade(f, converge='new', unresolved_rounds=0)
        if rewritten(o):
            n = 1 + int(was.unresolved_rounds or 0)
            return _downgrade(f, converge=f'unresolved_{n}', unresolved_rounds=n)
        # 同一份稿：计数原样带过来，状态说清「**这不是新的一次观测**」。
        return _downgrade(f, converge='same_draft',
                          unresolved_rounds=int(was.unresolved_rounds or 0))

    rows = [r.model_copy(update={'findings': [mark(r.output_ordinal, f) for f in r.findings]})
            for r in rows]
    book_fs = [mark(None, f) for f in book_fs]
    if prev is not None:
        book_fs.append(finding(
            'convergence_report',
            detail=f'与上一份验收相比：消掉 {len(resolved)} 条'
                   + (f'（{sorted({k[1] for k in resolved})}）' if resolved else ''),
            evidence=[str(len(resolved))]))
    return rows, book_fs


# ─────────────────────────────────────────────────────────────────────────────
# 四、报告
# ─────────────────────────────────────────────────────────────────────────────

def _outcomes(rev: Review, book: M.Book | None) -> list:
    """`Review` → 一条注册判据恰好一行。"""
    by_rule: dict[str, list[Finding]] = collections.defaultdict(list)
    for r in rev.chapters:
        for f in r.findings:
            by_rule[f.rule].append(f)
    for f in rev.book:
        by_rule[f.rule].append(f)

    # Review 是逐章事实账，因此保留每章的 band_drift；CheckReport /
    # CLI / API 则是操作面，同一方向的系统性漂移只能算一件书级待办。
    # 聚合 finding 的 evidence 放入可机器展开的逐章 JSON，不丢 wontfix /
    # convergence 信息，也不改写盘上 Review 本体。
    if band_findings := by_rule.get('band_drift'):
        by_rule['band_drift'] = [
            _aggregate_band_drift(band_findings, total_chapters=len(rev.chapters))]

    nch = len([r for r in rev.chapters if r.relpath])
    scanned = {r_: f'{rev.baselines.get(r_, "")} 章（恒等基线 '
                   f'{rev.baselines_identity.get(r_, "?")}）'
               for r_ in rev.baselines if r_ != '_fact_landing'}
    scanned['_fact_landing'] = rev.baselines['_fact_landing']
    for r_ in ('secret_timeline', 'no_secrets_in_book'):
        scanned[r_] = f'{len(book.secrets) if book else "?"} 条 secret'
    for r_ in ('draft_missing', 'book_length', 'book_length_report',
               'convergence_report', 'name_surface_survey', 'must_hold_unver',
               'fact_landing', 'fact_landing_unver', 'end_hook_surface_report',
               'end_hook_unver',
               'cross_chapter_number'):
        scanned.setdefault(r_, f'{nch}/{len(rev.chapters)} 章有成稿')

    # ⚠⚠ **`secret_leak` 报绿灯时同一行必须打盲区计数**：实测 21 条 secret 里 19 条的字面
    #   探针**连它自己的揭示章都命中不了** ⇒「0 命中」这句话的信息量接近 0，而它是 7 条
    #   HARD 之一。⇒ 盲区 100% 时落 `unver` **而不是沉默**。不降级 sev。
    blind: dict[str, str] = {}
    if book is not None and book.secrets:
        n_blind = sum(1 for f in rev.book if f.rule == 'secret_timeline'
                      and '连揭示章自己都没命中' in f.detail)
        scanned['secret_leak'] = (f'{len(book.secrets)} 条 secret，'
                                  f'其中 {n_blind} 条的字面探针零判别力')
        if n_blind == len(book.secrets):
            blind['secret_leak'] = (
                f'这本书 {len(book.secrets)} 条 secret 的字面探针**全部**连它自己的'
                f'揭示章都命中不了 ⇒ 本条这一次没有判别力样本。'
                f'「0 命中」在这里不是一个结论；全链没有书级语义审判节点，'
                f'改写式泄底由 Stage2 后见之明判据与交付前成书逐字对账复核')

    # ⚠⚠ **保真那三条的分母是「下发了多少条材料」，不是「命中了几章」。**
    #   `baselines` 记的是后者，而两者在报告上长得一模一样：一条材料都没下发时，
    #   「0/31 章命中」读起来就是「31 章全守住了」。
    #   真发生过：`beat_index` → `verbatim_pieces` 的形态断过一版，五本书下发面全空，
    #   这三条 HARD 一路报绿，**而闭卷写手手里一个原文字节都没有**。
    #   ⇒ 分母无条件打出来；分母为零时落 `unver`（「没东西可查」≠「查过、干净」）。
    #   根因判据在上一层：`stage4` 的 `ctx_hinge_delivered`。
    for rule_, field_, what_ in (('verbatim_missing', 'verbatim', '句【原样写入】'),
                                 ('keep_value_missing', 'keep_value', '处【数值不得改动】'),
                                 ('must_appear_missing', 'must_appear', '个【必须在场】')):
        n_mat = sum(len(scene.get(field_) or [])
                    for c in ((book.ctx.get('chapters') if book else None) or [])
                    for scene in (c.get('scenes') or []))
        scanned[rule_] = f'{n_mat} {what_}的材料（{scanned.get(rule_, "?")}）'
        if not n_mat:
            blind[rule_] = (
                f'这本书的下发面上一条{what_}的材料都没有 ⇒ 本条没有分母。'
                f'**这不是「守住了」**：闭卷写手拿原文的唯一通道就是这四组点，'
                f'空的意味着它这一章什么原文都没拿到。先看 `stage4` 的 '
                f'`ctx_hinge_delivered` 那一行——那里说得出是 stage2 没抽到，'
                f'还是组装这一步把材料吃掉了')
    # 人称探针只有“单 narration + 全章边界 + first/third + 非空锚”这一窄域可判。
    # 混合输入时每章的 metric 明写 UNVER；整本分母为零时，聚合规则也必须落 unver，
    # 不能把 0 finding 渲染成 OK。
    person_reports = by_rule.get('narration_person_report', [])
    n_person = sum(1 for hit in person_reports
                   if 'probe:judgeable' in set(hit.evidence))
    scanned['narration_person_drift'] = (
        f'{n_person}/{len(person_reports)} 个有成稿章节落在弱信号可判域；其余逐章明确 UNVER')
    if person_reports and not n_person:
        blind['narration_person_drift'] = (
            'second / 多 narration / 无段界 / 无锚覆盖了全部有成稿章节，'
            '本条这次没有一个可判样本；逐章原因见 narration_person_report')

    return checks.check(
        chapter_findings_by_rule=by_rule, scanned=scanned,
        unver_empty={
            'draft_missing': f'计划 {len(rev.chapters)} 章全部有成稿——没有一章的判定被跳过',
            'no_secrets_in_book': (f'这本书有 {len(book.secrets)} 条 secret'
                                   if book and book.secrets else '这本书没有可查的秘密'),
            'must_hold_unver': '这本书没有【必须成立】那一组材料',
            'fact_landing_unver': '每个必落事实都提得出可回查的原子',
            'end_hook_unver': '没有成稿章节可供人工复核章尾语义交付',
        },
        blind=blind)


def _aggregate_band_drift(
    findings: list[Finding], *, total_chapters: int,
) -> Finding:
    """Collapse chapter facts into one lossless book-level report finding."""
    ordered = sorted(
        findings,
        key=lambda hit: (
            hit.output_ordinal is None,
            hit.output_ordinal or 0,
            hit.detail,
            hit.evidence_key,
        ),
    )
    chapters = sorted({
        hit.output_ordinal for hit in ordered if hit.output_ordinal is not None})
    evidence = [json.dumps({
        'output_ordinal': hit.output_ordinal,
        'detail': hit.detail,
        'evidence': list(hit.evidence),
        'evidence_key': hit.evidence_key,
        'actionable': hit.actionable,
        'identity_hit': hit.identity_hit,
        'identity_dead': hit.identity_dead,
        'converge': hit.converge,
        'unresolved_rounds': hit.unresolved_rounds,
        'wontfix': (
            hit.wontfix.model_dump(mode='json') if hit.wontfix is not None else None),
    }, ensure_ascii=False, sort_keys=True, separators=(',', ':')) for hit in ordered]

    all_wontfix = bool(ordered) and all(hit.wontfix is not None for hit in ordered)
    combined_wontfix = None
    if all_wontfix:
        combined_wontfix = WontFixNote(
            why='；'.join(
                f'ch{hit.output_ordinal}:{hit.wontfix.why}' for hit in ordered
                if hit.wontfix is not None),
            at='；'.join(
                f'ch{hit.output_ordinal}:{hit.wontfix.at}' for hit in ordered
                if hit.wontfix is not None),
        )
    states = sorted({hit.converge for hit in ordered})
    converge = states[0] if len(states) == 1 else f'mixed({",".join(states)})'
    detail = (
        f'书级档位漂移：{len(chapters)}/{total_chapters} 章命中，'
        f'涉及章节 {chapters}，共 {len(ordered)} 条逐章事实；'
        'detail、原始凭据、wontfix 与 convergence 状态可在 evidence 逐条展开')
    aggregate = finding(
        'band_drift', detail=detail, evidence=evidence,
        converge=converge,
        unresolved_rounds=max((hit.unresolved_rounds for hit in ordered), default=0),
        wontfix=combined_wontfix,
    )
    return _downgrade(
        aggregate,
        actionable=any(hit.actionable for hit in ordered),
        identity_hit=all(hit.identity_hit for hit in ordered),
        identity_dead=(True if ordered and all(hit.identity_dead is True for hit in ordered)
                       else None),
    )


def _report_of_review(ctx: RunContext, store: ArtifactStore, rev: Review,
                      book: M.Book | None = None, *,
                      freshness_rel: str | None = None) -> CheckReport:
    """机械结论 + 已落盘豁免 → 一份验收报告。

    """
    # `wontfix.json` 是独立于 `review_<n>.json` 的人的决定账。`--wontfix`
    # 刻意不重算/不落一份新 Review，所以每个读出口都必须在**读时**叠加当前账；
    # 只在 `build_review` 时烘焙一次会让刚落下的决定在 check/inbox 里继续显示
    # pending，直到有人额外重跑一次 0 成本验收。重套是幂等的：匹配身份仍是
    # `(章|book, rule, evidence_key)`，已有标记只会被当前账的同一条目刷新。
    current = with_current_wontfix(store, rev)

    seqs = review_seqs(store)
    rel = freshness_rel or (REVIEW_TPL.format(n=seqs[-1]) if seqs else WRITER_CONTEXT)
    return build(sid=ctx.sid, node=ctx.node, freshness=store.freshness(rel),
                 outcomes=list(_outcomes(current, book)),
                 accounting=None, invalid=False)


def run_review(ctx: RunContext) -> tuple[Review, CheckReport]:
    """默认路：算一份新的验收报告并落盘。**0 成本，不调 LLM。**"""
    with stage5_runs.book_commit_ownership(ctx.novel_dir):
        store = ArtifactStore(ctx)
        legacy = M.legacy_stage5_paths(store)
        if legacy:
            raise TerminalState(
                f'legacy_incompatible：发现旧 Stage5 双臂路径 {legacy[:8]}。'
                '旧 base 是只读历史证据，不在原地迁移；'
                '请在新的 canonical base 从 Stage5 producer 重建')
        book = load_book(ctx, store)
        rev = build_review(ctx, store, book)
        n = (review_seqs(store)[-1] + 1) if review_seqs(store) else 1
        store.write(REVIEW_TPL.format(n=n), rev)
        return rev, _report_of_review(ctx, store, rev, book)


def run_check_only(ctx: RunContext) -> CheckReport:
    """`--check-only`：**读产物不重算**。

    ⚠ 正因为不重算，第一节报的新鲜度就是全部的来源交代：产物过期时它报出来的一切
    都是**旧结论**。缺 `judged` 记作「不知道」，不是「匹配」。
    """
    store = ArtifactStore(ctx)
    current = require_fresh_review(ctx, store)
    assert current.review is not None
    book = load_book(ctx, store) if store.exists(GLOBAL_GROUNDING) else None
    return _report_of_review(
        ctx, store, current.review, book, freshness_rel=current.review_rel)
