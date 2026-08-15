#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage4.py —— S-07「计划 ↔ 写作上下文」那一屏（`GET /api/novels/:sid/stage4/:o`）。

【三态，`04_delivery.md:133` 钉死的那一条】
  · 计划在盘上 ⇒ 真计划 + 盘上 ctx（ctx 缺席/读不回 ⇒ 右半边 `null`，
    `ctx_state` 说明处境——计划这一侧不陪葬）；
  · 计划不在 ⇒ **恒等基线**：`identity_plan` + `build_writer_context`
    同一对纯函数**现算不落盘**——「如果计划什么都不改，写手手里会有什么」。
    与 `--identity` 那条跑法共用内核，view 不写第二个组装器；
  · 恒等也算不出（stage1/stage2 缺）⇒ 404（`UpstreamMissing`）。

【判定不在这里】`in_band` / `style_hits` 是判据结论，唯一来源是 `/check/stage4`
（`R-11.4`）。本屏只摆事实：预算、跨度、拍、材料。
"""
from __future__ import annotations

import os

from contracts.schema.api import (
    PlanBeatLine, PlanChapterLine, Stage4IndexRow, Stage4View,
)
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import ChapterBlueprints, GlobalGrounding
from contracts.schema.stage4 import CtxChapter, PlanChapters, WriterContext
from novel import paths, prov
from novel.errors import ArtifactUnreadable, UpstreamMissing
from novel.orchestrator.state import artifact_state, readonly_ctx
from novel.stages.stage4.beats import beat_index, group_beats
from novel.stages.stage4.context import build_writer_context
from novel.stages.stage4.measure import identity_plan
from novel.stages.stage5.prompts import target_band
from novel.store import ArtifactStore

NODE = 'stage4.plan'
PLAN_REL = 'stage4/plan_chapters.json'
CTX_REL = 'stage4/writer_context.json'
MAIN_TEXT = 'stage1/main_text.json'
BLUEPRINTS = 'stage2/chapter_blueprints.json'
GLOBAL_GROUNDING = 'stage2/global_grounding.json'


def stage4_view(base: str | os.PathLike[str], sid: str, ordinal: int) -> Stage4View:
    """S-07 的一章。**纯读**：恒等基线现算在内存里，一个字节不落盘。"""
    novel_dir = paths.resolve_novel_dir(base, sid)
    store = ArtifactStore(readonly_ctx(novel_dir, NODE, prov.code_version()))

    plan_state = artifact_state(store, PLAN_REL)
    ctx_state = artifact_state(store, CTX_REL)

    if plan_state.exists:
        # 在而读不回 ⇒ `ArtifactUnreadable` 一路上抛成 409——「坏了」不是「没跑」。
        plan_art, _raw = store.read_own(PlanChapters, PLAN_REL)
        plan_raw = plan_art.model_dump(by_alias=True)
        identity = False
        plan_prov = plan_art.provenance
        ctx_chapters, ctx_prov = _ctx_from_disk(store)
    else:
        plan_raw, ctx_chapters = _identity(store)
        identity = True
        plan_prov = None
        ctx_prov = None

    chapters = [int(c['output_ordinal']) for c in plan_raw['output_chapters']]
    if ordinal not in chapters:
        raise UpstreamMissing(f'第 {ordinal} 章不在计划的输出章集里（{chapters}）')

    by_ord = {c.output_ordinal: c for c in (ctx_chapters or [])}
    index = [_index_row(pc, by_ord.get(int(pc['output_ordinal'])))
             for pc in plan_raw['output_chapters']]

    pc = next(c for c in plan_raw['output_chapters'] if int(c['output_ordinal']) == ordinal)
    ctx_entry = by_ord.get(ordinal)
    target = None
    if ctx_entry is not None:
        target = target_band(ctx_entry.model_dump(by_alias=True))

    return Stage4View(
        sid=paths.derive_source_meta(novel_dir.name)[0],
        title=paths.title_of(novel_dir),
        identity_plan=identity,
        reconstruction_strategy=plan_raw['reconstruction_strategy'],
        chapters=chapters,
        ordinal=ordinal,
        index=index,
        plan_chapter=PlanChapterLine(
            output_ordinal=int(pc['output_ordinal']),
            from_source_ordinals=list(pc['from_source_ordinals']),
            chapter_goal=pc['chapter_goal'],
            open_hook_goal=pc['open_hook_goal'],
            end_hook_goal=pc['end_hook_goal'],
            beats=[PlanBeatLine(i=i, from_beats=list(b['from_beats']),
                                budget_chars=b.get('budget_chars'))
                   for i, b in enumerate(pc['beats'], start=1)]),
        ctx_chapter=ctx_entry,
        writer_target=target,
        plan_state=plan_state,
        ctx_state=ctx_state,
        _provenance=plan_prov,
        _ctx_provenance=ctx_prov,
    )


def _ctx_from_disk(store: ArtifactStore):
    """盘上的 `writer_context`。缺席 / 读不回 ⇒ `(None, None)`——
    右半边缺席不带走计划那一侧（`ctx_state` 已把「为什么没有」说清）。"""
    try:
        art, _raw = store.read_own(WriterContext, CTX_REL)
    except (UpstreamMissing, ArtifactUnreadable):
        return None, None
    return list(art.chapters), art.provenance


def _identity(store: ArtifactStore):
    """恒等基线：与 `run_identity` 同一对纯函数，**现算不落盘**。

    stage1/stage2 缺席 ⇒ `UpstreamMissing`（连基线都算不出，这一屏真的没有内容）。
    """
    for rel in (MAIN_TEXT, BLUEPRINTS, GLOBAL_GROUNDING):
        if not store.exists(rel):
            raise UpstreamMissing(
                f'{rel} 不在盘上——没跑过 stage4 时本屏显示恒等基线，'
                f'而基线也要吃 stage1 与 stage2 的产出')
    main_raw = store.read(MainText, MAIN_TEXT).model_dump(by_alias=True)
    bp_raw = store.read(ChapterBlueprints, BLUEPRINTS).model_dump(by_alias=True)
    gg_raw = store.read(GlobalGrounding, GLOBAL_GROUNDING).model_dump(by_alias=True)
    bidx = beat_index(bp_raw, main_raw)
    groups = group_beats(bp_raw, main_raw, bidx)
    plan_raw = identity_plan(bp_raw, main_raw, bidx, groups)
    asm = build_writer_context(plan_raw, gg_raw, bp_raw, main_raw)
    chapters = [CtxChapter.model_validate(c) for c in (asm.ctx.get('chapters') or [])]
    return plan_raw, chapters


def _index_row(pc: dict, ctx_entry: CtxChapter | None) -> Stage4IndexRow:
    budgets = [b.get('budget_chars') for b in pc['beats']]
    span = None
    hidden = None
    if ctx_entry is not None:
        chars = [b.source_chars for b in ctx_entry.beats if b.source_chars is not None]
        span = sum(chars) if chars else None
        hidden = len(ctx_entry.must_not_reveal)
    return Stage4IndexRow(
        output_ordinal=int(pc['output_ordinal']),
        budget_chars=sum(b for b in budgets if b is not None) or None,
        span_chars=span,
        beats=len(pc['beats']),
        hidden=hidden)
