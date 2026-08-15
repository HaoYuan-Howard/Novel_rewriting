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

import collections
import glob
import os
import re
from typing import Any

from contracts.invariants import STOP_REWRITE_AFTER
from contracts.rules import REGISTRY
from contracts.schema.common import Finding, WontFixEntry
from contracts.schema.report import CheckReport, finding
from contracts.schema.stage2 import GlobalGrounding
from contracts.schema.stage3 import StyleGrounding
from contracts.schema.stage4 import WriterContext
from contracts.schema.stage5 import (
    ContextSource, ContextSourceDetail, DraftSources, Review, ReviewChapter, ReviewProvenance,
    ReviewSources,
)
from novel import prov, wontfix
from novel.errors import TerminalState, UpstreamMissing
from novel.report.builder import build
from novel.run import RunContext
from novel.stages.stage5 import checks
from novel.stages.stage5 import measure as M
from novel.store import ArtifactStore

MAIN_TEXT = 'stage1/main_text.json'
GLOBAL_GROUNDING = 'stage2/global_grounding.json'
STYLE_GROUNDING = 'stage3/style_grounding.json'
WRITER_CONTEXT = 'stage4/writer_context.json'
#: ⚠ 从 `novel/wontfix.py` 派生，不手写第二个字符串——两层共用同一张模板。
WONTFIX = wontfix.rel_of('stage5')

#: `stage5/review_<n>.json`。`<n>` 由盘上已有的最大序号 **+1**。
REVIEW_TPL = 'stage5/review_{n}.json'
_REVIEW_RX = re.compile(r'^review_(\d+)\.json$')


# ─────────────────────────────────────────────────────────────────────────────
# 一、上游
# ─────────────────────────────────────────────────────────────────────────────

def load_book(ctx: RunContext, store: ArtifactStore) -> M.Book:
    """四份上游 → `Book`。**写作相与验收相共用这一个入口。**

    ⚠⚠ **secrets 直接读 stage2 全量，不从各章 `facts.secrets` 取并集**：
    并集的成员资格恰好等于「至少被某一章下发过」，于是一条**全书从未下发**的 secret 会让
    `secret_leak` 的关键片段成空列表 ⇒ **这条 secret 的泄漏检查从此永远不执行**，
    而判据照常输出一个「没报违规」。**收窄下发面是修复，收窄检测器是削弱。**
    """
    for rel in (MAIN_TEXT, GLOBAL_GROUNDING, STYLE_GROUNDING, WRITER_CONTEXT):
        if not store.exists(rel):
            raise UpstreamMissing(f'{rel} 不在盘上——先跑 {rel.split("/")[0]}')
    main = store.read(_main_text_model(), MAIN_TEXT)
    gg = store.read(GlobalGrounding, GLOBAL_GROUNDING)
    style = store.read(StyleGrounding, STYLE_GROUNDING)
    wc = store.read(WriterContext, WRITER_CONTEXT)
    return M.Book(
        title=ctx.title,
        ctx=wc.model_dump(by_alias=True),
        style=style.model_dump(by_alias=True),
        source_text={s.chapter_ordinal: (s.text or '') for s in main.root},
        secrets=[s.model_dump(by_alias=True) for s in gg.canon.secrets],
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
    """最近一份验收报告。没有 ⇒ `None`。"""
    seqs = review_seqs(store)
    if not seqs:
        return None
    n = seqs[-1]
    art, _raw = store.read_own(Review, REVIEW_TPL.format(n=n))
    return n, art


def load_wontfix(store: ArtifactStore) -> dict[str, WontFixEntry]:
    return wontfix.load(store, 'stage5')


#: 收敛键的构造器。⚠ 全链只有 `novel/wontfix.py::key` 一份实现，本名是转发。
wontfix_key = wontfix.key


def _semantic_pool(ctx: RunContext, store: ArtifactStore) -> tuple[list[Finding], str | None]:
    """本层**当前的语义结论**，以及它出自哪一份审判产物（审计账用）。

    ⚠⚠ **它是这条闭环存在的理由。** 语义结论在报告渲染时由
    `stage5/semantic.py::outcomes` 现算，**不进 `review_<n>.json`** ⇒
    只从最近一份验收里反查的旧实现，对 `semantic_secret_leak` 永远查不到，
    人拿到的是一句「不给不存在的结论发豁免」——而那条结论明明就在报告上。
    ⇒ 反查池 = 验收里的机械结论 **∪** 现算的语义结论。

    ⚠ 语义结论**一律 book 级寻址**：它们的章信息在证据键里
    （`semantic_secret_leak` 的证据是 `ch<n>:<正文原字节>`），
    再按章分一次是同一个信息的第二个落点。
    """
    from novel.stages.stage5 import semantic
    book = load_book(ctx, store) if store.exists(GLOBAL_GROUNDING) else None
    outs = semantic.outcomes(store, book)
    seqs = semantic.semantic_seqs(store)
    at = f'semantic_{seqs[-1]}' if seqs else None
    return [f for o in outs for f in o.findings], at


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
    store = ArtifactStore(ctx)
    got = latest_review(store)
    if got is None:
        raise TerminalState(
            '盘上一份验收报告都没有——**不给不存在的结论发豁免**。先跑 `stage5.review`')
    seq, rev = got
    semantic_findings, semantic_at = _semantic_pool(ctx, store)

    def pool_of(ordinal: int | None) -> list[Finding]:
        if ordinal is not None:
            return list(next((c.findings for c in rev.chapters
                              if c.output_ordinal == ordinal), []))
        return list(rev.book) + semantic_findings

    semantic_rules = {f.rule for f in semantic_findings}
    _head, rule, _ev, _why = wontfix.parse_spec(spec)
    at = (f'{semantic_at or "semantic_?"}@review_{seq}'
          if rule in semantic_rules else f'review_{seq}')
    wontfix.mark(store, 'stage5', spec, pool_of=pool_of, at=at)
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
        recs = by_ord.get(o) or []
        base = {'output_ordinal': o,
                'from_source_ordinals': entry['from_source_ordinals'],
                'budget_chars': entry['budget_chars'],
                'source_chars': entry['source_chars']}
        if not recs:
            rows.append(ReviewChapter(**base, findings=[finding(
                'draft_missing',
                detail=f'计划里有第 {o} 章，stage5 没有它的成稿——'
                       f'**本章的全部判定都没有跑，不是跑了没问题**',
                evidence=[str(o)])]))
            continue
        fs: list[Finding] = []
        if len(recs) > 1:
            # ⚠ 三处行为**故意不同**：`assemble_book` 对双份**显式终态**（哪一份是成品
            #   无从判断，不猜），而验收与界面**只报不判死**——把「呈现」变成「判死」
            #   会让人连报告都看不到。
            fs.append(finding(
                'draft_duplicated',
                detail=f'第 {o} 章在 {[r["_mode"] for r in recs]} 里各有一份成稿'
                       f'——哪一份是成品无从判断（`assemble_book` 会在这里显式终态）；'
                       f'下面判的是 {recs[0]["_mode"]} 那一份',
                evidence=[r['_mode'] for r in recs]))
        rec = recs[0]
        body = rec.get('text') or ''
        bodies[o] = body
        mode = rec['_mode']
        mat = _material(entry, book, mode=mode)
        fs += checks.chapter_findings(body, entry, book, mode=mode, given=mat)
        extra, one_land = checks.review_chapter_findings(body, entry, book)
        fs += extra
        land.update(one_land)

        ident = checks.identity_rules(entry, book, mode=mode)
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
            **base, mode=mode, mode_dir=mode, relpath=rec['_relpath'],
            # ⚠ **两把指纹各管一件事，这是被一次误判逼出来的**：来源账、`warns`、归一
            #   这类 0 成本元数据修复会改整份文件的指纹，但写手**一个字都没再写**——
            #   拿整份指纹去推进收敛计数，等于把一次重判记成「写手又试了一次」。
            draft_round=judged[rec['_relpath']].round,
            draft_sha1=judged[rec['_relpath']].sha1,
            draft_text_sha1=prov.fingerprint(body),
            chars=M.chars_of(body), findings=fs))

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
    baselines['_beat_event_landing'] = (
        f"{land['ok']}/{land['ok'] + land['missed']} 拍有落点"
        f"（另 {land['noatom']} 拍提不出原子、{land['ident']} 拍原子提错，均未检查）")
    baselines_identity = {r_: f'{ident_hits.get(r_, 0)}/{nch}' for r_ in conclusive}

    counts: collections.Counter = collections.Counter()
    for f in [f for r in rows for f in r.findings] + book_fs:
        counts[f.sev] += 1

    return Review(
        sources=sources, judged=judged, chapters=rows, book=book_fs,
        baselines=baselines, baselines_identity=baselines_identity, counts=dict(counts),
        _provenance=ctx.free_provenance(ReviewProvenance,
                                        prompts={'RULES': _rules_fingerprint()}),
    )


def _material(entry: dict[str, Any], book: M.Book, *, mode: str) -> str:
    """这一臂**实际看得见**的全部文字 —— 发明越界那两个探针的参照集。

    ⚠ **它必须与真跑发出去的那一份同源**（`prompts.build_material`），否则探测器的参照集
    与被测者当时看得见的东西就不是一回事——实测开卷臂曾因此 4/4 全是假阳。
    """
    from novel.stages.stage5 import prompts
    return prompts.build_material(
        entry, book, with_source_text=(mode == 'shallow')).given


def _apply_wontfix(rows: list[ReviewChapter], book_fs: list[Finding],
                   wf: dict[str, WontFixEntry]) -> tuple[list[ReviewChapter], list[Finding]]:
    """**只降 `actionable` 并打标，不删结论**：豁免过的条目照常列在报告与界面上，
    只是不再进反哺、也不再算进「待人工」。**「有人看过并决定不修」与「没人看过」
    必须分得开**——那正是加这个机制的全部理由。

    ⚠ 这里套的是**落进产物的那一份**（机械结论）。语义结论不进产物，
    它们由 `_report_of_review` 在渲染时套同一份账（`novel/wontfix.py::applied_to_outcomes`）。
    """
    return ([r.model_copy(update={'findings': wontfix.applied(
                r.findings, wf, ordinal=r.output_ordinal)}) for r in rows],
            wontfix.applied(book_fs, wf))


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


def _rules_fingerprint() -> str:
    """**判据表自身的指纹** —— 这份报告的「尺子版本」。

    判据表改了而报告没重跑，这个指纹是唯一说得出来的地方。
    """
    return prov.fingerprint(repr(sorted(
        (r.rule, r.sev, r.check_kind, r.item, r.owner, r.repair, r.directive)
        for r in REGISTRY.of_stage('stage5'))))


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

    nch = len([r for r in rev.chapters if r.relpath])
    scanned = {r_: f'{rev.baselines.get(r_, "")} 章（恒等基线 '
                   f'{rev.baselines_identity.get(r_, "?")}）'
               for r_ in rev.baselines if r_ != '_beat_event_landing'}
    scanned['_beat_event_landing'] = rev.baselines['_beat_event_landing']
    for r_ in ('secret_timeline', 'no_secrets_in_book'):
        scanned[r_] = f'{len(book.secrets) if book else "?"} 条 secret'
    for r_ in ('draft_missing', 'draft_duplicated', 'book_length', 'book_length_report',
               'convergence_report', 'name_surface_survey', 'must_hold_unver',
               'event_landing', 'event_landing_unver', 'material_malformed',
               'cross_chapter_number', 'style_block_carries_plot'):
        scanned.setdefault(r_, f'{nch}/{len(rev.chapters)} 章有成稿')

    # ⚠⚠ **`secret_leak` 报绿灯时同一行必须打盲区计数**：实测 21 条 secret 里 19 条的字面
    #   探针**连它自己的揭示章都命中不了** ⇒「0 命中」这句话的信息量接近 0，而它是 7 条
    #   HARD 之一。⇒ 盲区 100% 时落 `unver` **而不是沉默**。不降级 sev。
    blind = None
    if book is not None and book.secrets:
        n_blind = sum(1 for f in rev.book if f.rule == 'secret_timeline'
                      and '连揭示章自己都没命中' in f.detail)
        scanned['secret_leak'] = (f'{len(book.secrets)} 条 secret，'
                                  f'其中 {n_blind} 条的字面探针零判别力')
        if n_blind == len(book.secrets):
            blind = (f'这本书 {len(book.secrets)} 条 secret 的字面探针**全部**连它自己的'
                     f'揭示章都命中不了 ⇒ 本条这一次没有判别力样本。'
                     f'「0 命中」在这里不是一个结论；改写式泄底由独立的书级语义层判定')
    return checks.check(
        chapter_findings_by_rule=by_rule, scanned=scanned,
        unver_empty={
            'draft_missing': f'计划 {len(rev.chapters)} 章全部有成稿——没有一章的判定被跳过',
            'draft_duplicated': '同一输出章没有出现在两条臂里',
            'no_secrets_in_book': (f'这本书有 {len(book.secrets)} 条 secret'
                                   if book and book.secrets else '这本书没有可查的秘密'),
            'must_hold_unver': '这本书没有【必须成立】那一组材料',
            'event_landing_unver': '每一拍都提得出可回查的原子',
        },
        secret_blind=blind)


def _report_of_review(ctx: RunContext, store: ArtifactStore, rev: Review,
                      book: M.Book | None = None, *,
                      freshness_rel: str | None = None) -> CheckReport:
    """⚠ **语义那一半的豁免在这里套，不在 `build_review` 里。**

    机械结论落进 `review_<n>.json`（豁免跟着落盘，人的决定是产物的一部分），
    而语义结论是渲染时现算的 ⇒ 它们的豁免只能在**这一层**套上。
    漏了这一步，人明明豁免过一条 `semantic_secret_leak`，报告照旧把它算成待办
    ——「有人看过并决定不修」与「没人看过」又同形了。
    """
    from novel.stages.stage5 import semantic
    seqs = review_seqs(store)
    rel = freshness_rel or (REVIEW_TPL.format(n=seqs[-1]) if seqs else WRITER_CONTEXT)
    wf = load_wontfix(store)
    return build(sid=ctx.sid, node=ctx.node, freshness=store.freshness(rel),
                 outcomes=[*_outcomes(rev, book),
                           *wontfix.applied_to_outcomes(semantic.outcomes(store, book), wf)],
                 accounting=None, invalid=False)


def run_review(ctx: RunContext) -> tuple[Review, CheckReport]:
    """默认路：算一份新的验收报告并落盘。**0 成本，不调 LLM。**"""
    store = ArtifactStore(ctx)
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
    got = latest_review(store)
    if got is None:
        raise UpstreamMissing(
            f'盘上一份 {REVIEW_TPL.format(n="<n>")} 都没有——先跑 `stage5.review`（0 成本）')
    _seq, rev = got
    book = load_book(ctx, store) if store.exists(GLOBAL_GROUNDING) else None
    return _report_of_review(ctx, store, rev, book)
