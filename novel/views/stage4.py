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
（`R-11.4`）。本屏只摆事实：语义契约、预算、新场景与写手任务。
"""
from __future__ import annotations

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_GROUNDING,
    STAGE4_PLAN_CHAPTERS, STAGE4_WRITER_CONTEXT,
)

import os

from contracts.schema.api import (
    PlanChapterLine, PlanSceneLine, Stage4IndexRow, Stage4View,
)
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import ChapterBlueprints, GlobalGrounding
from contracts.schema.stage4 import CtxChapter, PlanChapters, WriterContext
from novel import paths, prov
from novel.errors import ArtifactUnreadable, UpstreamMissing
from novel.orchestrator.state import artifact_state, readonly_ctx
from novel.stages.stage4.beats import beat_index
from novel.stages.stage4.context import build_writer_context
from novel.stages.stage4.measure import (
    computed_structure_change_kinds, identity_plan, semantic_contract_of,
    structure_metrics_of, structure_subject_sha256,
)
from novel.stages.stage5.prompts import target_band
from novel.store import ArtifactStore

NODE = 'stage4.plan'
PLAN_REL = STAGE4_PLAN_CHAPTERS
CTX_REL = STAGE4_WRITER_CONTEXT
MAIN_TEXT = STAGE1_MAIN_TEXT
BLUEPRINTS = STAGE2_CHAPTER_BLUEPRINTS
GLOBAL_GROUNDING = STAGE2_GLOBAL_GROUNDING


def stage4_view(base: str | os.PathLike[str], sid: str, ordinal: int) -> Stage4View:
    """S-07 的一章。**纯读**：恒等基线现算在内存里，一个字节不落盘。"""
    novel_dir = paths.resolve_novel_dir(base, sid)
    store = ArtifactStore(readonly_ctx(novel_dir, NODE, prov.code_version()))

    plan_state = artifact_state(store, PLAN_REL)
    ctx_state = artifact_state(store, CTX_REL)
    blueprints = store.read(ChapterBlueprints, BLUEPRINTS).model_dump(by_alias=True)
    blueprint_by_ordinal = {
        int(row['chapter_ordinal']): row
        for row in blueprints['blueprints']
    }

    if plan_state.exists:
        # 在而读不回 ⇒ `ArtifactUnreadable` 一路上抛成 409——「坏了」不是「没跑」。
        plan_art, _raw = store.read_own(PlanChapters, PLAN_REL)
        # rows / 章详情 / subject 必须由同一个已读入的 plan 快照派生。
        # 不另读一次路径，否则计划在两次读之间被替换时，
        # 屏幕会把旧 rows 冒充成新 subject 的审批表面。
        structure_subject = structure_subject_sha256(plan_art)
        plan_raw = plan_art.model_dump(by_alias=True)
        identity = False
        plan_prov = plan_art.provenance
        ctx_chapters, ctx_prov = _ctx_from_disk(store)
    else:
        plan_raw, ctx_chapters = _identity(store, blueprints)
        structure_subject = None
        identity = True
        plan_prov = None
        ctx_prov = None

    chapters = [int(c['output_ordinal']) for c in plan_raw['output_chapters']]
    if ordinal not in chapters:
        raise UpstreamMissing(f'第 {ordinal} 章不在计划的输出章集里（{chapters}）')

    by_ord = {c.output_ordinal: c for c in (ctx_chapters or [])}
    index = [
        _index_row(pc, blueprint_by_ordinal[int(pc['output_ordinal'])])
        for pc in plan_raw['output_chapters']
    ]

    pc = next(c for c in plan_raw['output_chapters'] if int(c['output_ordinal']) == ordinal)
    ctx_entry = by_ord.get(ordinal)
    target = None
    if ctx_entry is not None:
        target = target_band(ctx_entry.model_dump(by_alias=True))

    return Stage4View(
        sid=paths.derive_source_meta(novel_dir.name)[0],
        title=paths.title_of(novel_dir),
        identity_plan=identity,
        structure_subject_sha256=structure_subject,
        chapters=chapters,
        ordinal=ordinal,
        index=index,
        plan_chapter=PlanChapterLine(
            output_ordinal=int(pc['output_ordinal']),
            from_source_ordinals=list(pc['from_source_ordinals']),
            chapter_goal=pc['chapter_goal'],
            open_hook_goal=pc['open_hook_goal'],
            end_hook_goal=pc['end_hook_goal'],
            structure_decision=_decision_view(
                pc, blueprint_by_ordinal[int(pc['output_ordinal'])]),
            semantic_contract=semantic_contract_of(
                blueprint_by_ordinal[int(pc['output_ordinal'])]),
            chapter_rewrite_brief=pc['chapter_rewrite_brief'],
            scenes=[PlanSceneLine(
                i=i,
                slot=str(scene['slot']),
                budget_chars=int(scene['budget_chars']),
                purpose=str(scene['purpose']),
                role=scene['role'],
                delivery=scene['delivery'],
                transition=scene['transition'],
                land_fact_refs=list(scene['land_fact_refs']),
                context_fact_refs=list(scene.get('context_fact_refs') or []),
                turn_fact_ref=str(scene['turn_fact_ref']),
                entry_state_refs=list(scene.get('entry_state_refs') or []),
                exit_state_refs=list(scene.get('exit_state_refs') or []),
            ) for i, scene in enumerate(pc['scenes'], start=1)]),
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


def _identity(store: ArtifactStore, blueprints: dict):
    """恒等基线：与 `run_identity` 同一对纯函数，**现算不落盘**。

    stage1/stage2 缺席 ⇒ `UpstreamMissing`（连基线都算不出，这一屏真的没有内容）。
    """
    for rel in (MAIN_TEXT, BLUEPRINTS, GLOBAL_GROUNDING):
        if not store.exists(rel):
            raise UpstreamMissing(
                f'{rel} 不在盘上——没跑过 stage4 时本屏显示恒等基线，'
                f'而基线也要吃 stage1 与 stage2 的产出')
    main_raw = store.read(MainText, MAIN_TEXT).model_dump(by_alias=True)
    gg_raw = store.read(GlobalGrounding, GLOBAL_GROUNDING).model_dump(by_alias=True)
    bidx = beat_index(blueprints, main_raw)
    plan_raw = identity_plan(blueprints, main_raw, bidx)
    asm = build_writer_context(plan_raw, gg_raw, blueprints, main_raw)
    chapters = [CtxChapter.model_validate(c) for c in (asm.ctx.get('chapters') or [])]
    return plan_raw, chapters


def _index_row(pc: dict, blueprint: dict) -> Stage4IndexRow:
    budgets = [scene.get('budget_chars') for scene in pc['scenes']]
    return Stage4IndexRow(
        output_ordinal=int(pc['output_ordinal']),
        chapter_goal=_goal_label(pc['chapter_goal']),
        structure_decision=_decision_view(pc, blueprint),
        budget_chars=sum(b for b in budgets if b is not None) or None,
        scenes=len(pc['scenes']),
    )


def _decision_view(chapter: dict, blueprint: dict) -> dict:
    """把算法读数放在 API 视图，不污染人工决定或计划字节。"""
    metrics = structure_metrics_of(blueprint, chapter)
    return {
        **chapter['structure_decision'],
        'computed_change_kinds': computed_structure_change_kinds(metrics),
    }


def _goal_label(goal: object) -> str:
    """Typed goal → 稳定展示行；界面不另存展示性解释。"""
    if not isinstance(goal, dict):
        return str(goal)
    intent = str(goal.get('intent') or '')
    refs = [
        f'{ref.get("kind")}:{ref.get("ref")}'
        for ref in (goal.get('fact_refs') or []) if isinstance(ref, dict)
    ]
    return ' · '.join(part for part in (intent, '、'.join(refs)) if part)
