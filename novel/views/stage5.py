#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage5.py —— S-08「成稿 ↔ 材料」与 S-09「验收 / 修订」两屏。

【S-08 的三条硬语义】（`02_screens.md` S-08 · `04_delivery.md:134`）
  · `ctx_exists=false` ⇒ **不显示假材料**——给「先跑 stage4」，右侧成稿照常列；
  · `materials` 与写手**同一份**（`prompts.build_material(...).blocks`，按闭卷臂组装
    ——开卷臂多出的那块就是源章原文，而原文本屏单独有落点）；
  · 原文↔成稿对齐块：分界线只由**双侧都命中的 verbatim** 定（`R-11.9`），
    无锚的拍并进相邻块；锚点覆盖率常驻；**这是对应关系，不是判定**。

【S-09 发的是验收产物（历史事实）】与「现在复检一遍」（`/check/stage5`）是两件事。
`stale` 逐章回答「验收判的那份与盘上当前那份还是不是同一份」（整文件指纹，
与 `Review.judged` 的审计尺子同一把）。

【纪律】纯读；判定不产不抄（`R-11.4`）；成稿一律走 `load_drafts`
（`read_judged` 那条路——`store.read` 对三级路径是 `PathRefused`，那是设计不是障碍）。
"""
from __future__ import annotations

from contracts.artifacts import STAGE1_MAIN_TEXT, STAGE4_WRITER_CONTEXT

import os
from typing import Any

from contracts.invariants import ANCHOR_COVERAGE_WARN, DEPTH_MODES, SEVERITY
from contracts.schema.api import (
    DraftAlignRow, DraftAlignView, DraftView, MaterialView,
    ReviewChapterView, ReviewIndexRow, ReviewView,
    Stage5IndexRow, Stage5View,
)
from contracts.schema.stage1 import MainText
from novel import paths, prov
from novel.errors import ArtifactUnreadable, UpstreamMissing
from novel.orchestrator.state import artifact_state, planned_chapters, readonly_ctx
from novel.stages.stage5 import review
from novel.stages.stage5.measure import load_drafts
from novel.stages.stage5.prompts import build_material, target_band
from novel.store import ArtifactStore

WRITE_NODE = 'stage5.write'
REVIEW_NODE = 'stage5.review'
CTX_REL = STAGE4_WRITER_CONTEXT
MAIN_TEXT = STAGE1_MAIN_TEXT

_ALIGN_NOTE = ('这是对应关系，不是判定——分界线由「必须逐字写入的原话」双侧锚定，'
               '无锚的拍并进相邻块')


# ─────────────────────────────────────────────────────────────────────────────
# S-08 成稿 ↔ 材料
# ─────────────────────────────────────────────────────────────────────────────

def stage5_view(base: str | os.PathLike[str], sid: str, ordinal: int) -> Stage5View:
    """S-08 的一章。**纯读**。"""
    novel_dir = paths.resolve_novel_dir(base, sid)
    rctx = readonly_ctx(novel_dir, WRITE_NODE, prov.code_version())
    store = ArtifactStore(rctx)

    ctx_state = artifact_state(store, CTX_REL)
    text_of = _source_text_of(store)
    by_ord, invalid_rels, _judged = load_drafts(store)

    planned_list = planned_chapters(store)
    chapters = sorted(set(planned_list or sorted(text_of)) | set(by_ord))
    if ordinal not in chapters:
        raise UpstreamMissing(f'第 {ordinal} 章不在本书的章集里（{chapters}）')

    book, entries = _book_and_entries(rctx, store, ctx_state)
    entry = entries.get(ordinal)

    materials = None
    if entry is not None and book is not None:
        mat = build_material(entry, book, with_source_text=False)
        materials = MaterialView(blocks=list(mat.blocks), chars=len(mat.given),
                                 writer_target=target_band(entry))

    invalid_by_ord = _invalids_by_ord(invalid_rels)
    index = [_index_row(store, o, by_ord.get(o, []), invalid_by_ord.get(o, []),
                        entries.get(o))
             for o in chapters]

    source_text = text_of.get(ordinal)
    drafts = [_draft_view(store, rec, invalid=False,
                          source_text=source_text, entry=entry)
              for rec in _mode_sorted(by_ord.get(ordinal, []))]
    invalid_drafts = [v for rel in invalid_by_ord.get(ordinal, [])
                      if (v := _invalid_draft_view(store, rel, source_text, entry))
                      is not None]

    written = sorted(by_ord)
    return Stage5View(
        sid=paths.derive_source_meta(novel_dir.name)[0],
        title=paths.title_of(novel_dir),
        ctx_exists=bool(ctx_state.exists and ctx_state.readable),
        planned=len(planned_list) if planned_list is not None else None,
        written=len(written),
        invalid=len(invalid_rels),
        missing=[o for o in (planned_list or []) if o not in by_ord],
        duplicates=[o for o, recs in sorted(by_ord.items()) if len(recs) > 1],
        chapters=chapters,
        ordinal=ordinal,
        index=index,
        source_text=source_text,
        source_chars_raw=len(source_text) if source_text is not None else None,
        materials=materials,
        drafts=drafts,
        invalid_drafts=invalid_drafts,
        ctx_state=ctx_state,
    )


def _source_text_of(store: ArtifactStore) -> dict[int, str]:
    """stage1 各章原文。**stage1 不在 ⇒ 404**（本屏至少要有章集与原文半边）。"""
    main = store.read(MainText, MAIN_TEXT)
    return {s.chapter_ordinal: (s.text or '') for s in main.root}


def _book_and_entries(rctx, store, ctx_state):
    """材料的来源。四份上游任一缺席 / 读不回 ⇒ `(None, {})`——
    材料缺席不带走成稿那一侧（`ctx_state` 与各稿的 freshness 已把处境说清）。"""
    if not (ctx_state.exists and ctx_state.readable):
        return None, {}
    try:
        book = review.load_book(rctx, store)
    except (UpstreamMissing, ArtifactUnreadable):
        return None, {}
    return book, dict(book.chapters)


def _mode_sorted(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {m: i for i, m in enumerate(DEPTH_MODES)}
    return sorted(recs, key=lambda r: order.get(r.get('_mode'), 99))


def _invalids_by_ord(invalid_rels: list[str]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for rel in invalid_rels:
        digits = ''.join(c for c in os.path.basename(rel).split('.')[0] if c.isdigit())
        if digits:
            out.setdefault(int(digits), []).append(rel)
    return out


def _index_row(store, o: int, recs, invalid_rels: list[str], entry) -> Stage5IndexRow:
    recs = _mode_sorted(recs)
    primary = recs[0] if recs else None
    fresh = None
    if primary is not None:
        fresh = artifact_state(store, primary['_relpath']).freshness
    budget = None
    if entry is not None:
        budget = entry.get('budget_chars')
    elif primary is not None:
        budget = primary.get('budget_chars')
    return Stage5IndexRow(
        output_ordinal=o,
        budget_chars=budget,
        modes=[r['_mode'] for r in recs],
        invalid_modes=sorted({rel.split('/')[1] for rel in invalid_rels}),
        dup=len(recs) > 1,
        chars=primary.get('chars') if primary is not None else None,
        overlap_excl_verbatim=(primary.get('overlap_excl_verbatim')
                               if primary is not None else None),
        freshness=fresh)


def _draft_view(store, rec: dict[str, Any], *, invalid: bool,
                source_text: str | None, entry) -> DraftView:
    rel = rec['_relpath']
    # ⚠ `.INVALID` 稿不走 `artifact_state`（`invalid_of` 对已是 INVALID 的路径显式拒收，
    #   「有没有孪生 INVALID」这个问题对它自己没有意义）——直接问它的来源新鲜度。
    try:
        rep = store.freshness(rel)
        fresh, reason = rep.state, rep.reason
    except (UpstreamMissing, ArtifactUnreadable):
        fresh, reason = 'unknown', 'no_provenance'
    p = rec.get('_provenance') or {}
    align, rows = _align(source_text, rec.get('text') or '', entry)
    return DraftView(
        mode=rec['_mode'],
        relpath=rel,
        invalid=invalid,
        round=int(p.get('round') or 1),
        origin=str(p.get('origin') or 'llm'),
        chars=int(rec.get('chars') or 0),
        budget_chars=rec.get('budget_chars'),
        source_chars=int(rec.get('source_chars') or 0),
        overlap_excl_verbatim=rec.get('overlap_excl_verbatim'),
        segments=list(rec.get('segments') or []),
        text=rec['text'],
        align=align,
        align_rows=rows,
        freshness=fresh,
        reason=reason,
        _provenance=p,
    )


def _invalid_draft_view(store, rel: str, source_text: str | None, entry) -> DraftView | None:
    """一份 `.INVALID` 稿。读不回 ⇒ 跳过（它的处境由 inbox 的 invalid/rejected 条目呈现，
    本屏不替它编卡片）。"""
    from contracts.schema.stage5 import Chapter
    from novel.stages.stage5.measure import draft_projection
    try:
        chapter, _fp, _cfp = store.read_judged(Chapter, rel, project=draft_projection)
    except (UpstreamMissing, ArtifactUnreadable):
        return None
    rec = chapter.model_dump(by_alias=True)
    rec['text'] = chapter.text
    rec['_relpath'], rec['_mode'] = rel, rel.split('/')[1]
    return _draft_view(store, rec, invalid=True, source_text=source_text, entry=entry)


# ── 对齐：最小 verbatim 锚版 ────────────────────────────────────────────────

def _align(source: str | None, draft: str,
           entry) -> tuple[DraftAlignView | None, list[DraftAlignRow]]:
    """原文 ↔ 成稿的对齐块。**锚 = 双侧都命中的 verbatim 原话**（`R-11.9`）。

    算法（确定性，无估算）：每拍取第一条在**两侧都出现**的 verbatim 作候选锚，
    按拍序贪心保留**两侧位置都单调递增**的那些（乱序的记进 `inversions`，不猜）；
    锚是块的起点，锚间即一块，首块从 0 起。**没有任何锚 ⇒ `align=null` + 空 rows**
    ——对齐辅助诚实缺席，不编分界线。
    """
    if source is None or entry is None or not draft:
        return None, []
    beats = list(entry.get('segments') or [])
    if not beats:
        return None, []

    candidates: list[tuple[int, int, int]] = []
    for i, be in enumerate(beats, start=1):
        for v in (be.get('verbatim') or []):
            sp, dp = source.find(v), draft.find(v)
            if sp >= 0 and dp >= 0:
                candidates.append((i, sp, dp))
                break

    kept: list[tuple[int, int, int]] = []
    inversions = 0
    last_s = last_d = -1
    for i, sp, dp in candidates:
        if sp > last_s and dp > last_d:
            kept.append((i, sp, dp))
            last_s, last_d = sp, dp
        else:
            inversions += 1

    view = DraftAlignView(
        beats=len(beats), anchors=len(kept), rows=len(kept),
        inversions=inversions,
        coverage=len(kept) / len(beats) if beats else 0.0,
        coverage_threshold=ANCHOR_COVERAGE_WARN,
        coverage_state=('low' if len(kept) / len(beats) < ANCHOR_COVERAGE_WARN
                        else 'adequate'),
        note=_ALIGN_NOTE)
    if not kept:
        return None, []

    rows: list[DraftAlignRow] = []
    for k, (beat_i, sp, dp) in enumerate(kept):
        s0 = 0 if k == 0 else sp
        d0 = 0 if k == 0 else dp
        s1, d1 = (kept[k + 1][1], kept[k + 1][2]) if k + 1 < len(kept) \
            else (len(source), len(draft))
        beat_from = 1 if k == 0 else beat_i
        beat_to = (kept[k + 1][0] - 1) if k + 1 < len(kept) else len(beats)
        covered = list(range(beat_from, beat_to + 1))
        rows.append(DraftAlignRow(
            beats=covered, l=source[s0:s1], r=draft[d0:d1],
            anchored=len(covered) == 1))
    return view, rows


# ─────────────────────────────────────────────────────────────────────────────
# S-09 验收 / 修订
# ─────────────────────────────────────────────────────────────────────────────

def review_view(base: str | os.PathLike[str], sid: str, ordinal: int) -> ReviewView:
    """S-09 的一章。**纯读**。一份验收都没有 ⇒ 404（先跑 stage5.review）。"""
    novel_dir = paths.resolve_novel_dir(base, sid)
    store = ArtifactStore(readonly_ctx(novel_dir, REVIEW_NODE, prov.code_version()))

    got = review.latest_review(store)
    if got is None:
        raise UpstreamMissing('盘上一份 stage5/review_*.json 都没有——先跑 stage5.review')
    seq, rev = got

    chapters = [rc.output_ordinal for rc in rev.chapters]
    if ordinal not in chapters:
        raise UpstreamMissing(f'第 {ordinal} 章不在这份验收的章集里（{chapters}）')

    by_ord, _invalids, judged_now = load_drafts(store)

    index = [_review_index_row(rc, judged_now) for rc in rev.chapters]
    rc = next(c for c in rev.chapters if c.output_ordinal == ordinal)

    return ReviewView(
        sid=paths.derive_source_meta(novel_dir.name)[0],
        title=paths.title_of(novel_dir),
        review_seq=seq,
        sources=rev.sources,
        finding_counts=_dense_counts(
            [f for c in rev.chapters for f in c.findings] + list(rev.book)),
        baselines=dict(rev.baselines),
        baselines_identity=dict(rev.baselines_identity),
        book=rev.book,
        wontfix=len(review.load_wontfix(store)),
        chapters=chapters,
        ordinal=ordinal,
        index=index,
        chapter=_review_chapter(store, rc, by_ord, judged_now),
        _provenance=rev.provenance,
    )


def _dense_counts(findings) -> dict[str, int]:
    """按 sev 现数，**四键恒在（0 也写）**——产物的 `counts` 是稀疏 Counter（登记病），
    出线侧不抄它。"""
    out = {sev: 0 for sev in SEVERITY}
    for f in findings:
        if f.sev in out:
            out[f.sev] += 1
    return out


def _review_index_row(rc, judged_now) -> ReviewIndexRow:
    return ReviewIndexRow(
        output_ordinal=rc.output_ordinal,
        mode=rc.mode,
        round=rc.draft_round,
        chars=rc.chars,
        budget_chars=rc.budget_chars,
        finding_counts=_dense_counts(rc.findings),
        unresolved=sum(1 for f in rc.findings
                       if int(f.unresolved_rounds or 0) > 0 and f.wontfix is None),
        stale=_stale_of(rc, judged_now))


def _stale_of(rc, judged_now) -> bool | None:
    """验收判的那份 vs 盘上当前那份（**整文件指纹**，与 `judged` 的审计尺子同一把）。

    `null` = 判不了：本章验收时就无稿、或验收后成稿被移走。
    """
    if rc.relpath is None or rc.draft_sha1 is None:
        return None
    now = judged_now.get(rc.relpath)
    if now is None:
        return None
    return now.sha1 != rc.draft_sha1


def _review_chapter(store, rc, by_ord, judged_now) -> ReviewChapterView:
    """一章明细。`text` / `chars` / `origin` 取**当前盘上**那份稿
    （优先验收判过的那条路径，缺了退主稿）——改稿入口要贴着现役正文。"""
    recs = {r['_relpath']: r for r in by_ord.get(rc.output_ordinal, [])}
    rec = recs.get(rc.relpath) if rc.relpath is not None else None
    if rec is None and by_ord.get(rc.output_ordinal):
        rec = _mode_sorted(by_ord[rc.output_ordinal])[0]
    p = (rec or {}).get('_provenance') or {}
    rounds: list[int] = []
    if rc.relpath is not None:
        rounds = sorted(store.seen_rounds(rc.relpath))
    return ReviewChapterView(
        output_ordinal=rc.output_ordinal,
        findings=rc.findings,
        mode=rc.mode,
        chars=(rec or {}).get('chars'),
        budget_chars=rc.budget_chars,
        round=rc.draft_round,
        origin=(str(p.get('origin')) if p.get('origin') else
                ('llm' if rec is not None else None)),
        segments=(list(rec.get('segments') or []) if rec is not None else None),
        text=(rec or {}).get('text'),
        rounds=rounds,
        stale=_stale_of(rc, judged_now),
    )
