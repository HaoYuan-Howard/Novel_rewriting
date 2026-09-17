#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage4.py —— S-07「计划 ↔ 写作上下文」那一屏（`GET /api/novels/:sid/stage4/:o`）。

【三态，`04_delivery.md:133` 钉死的那一条】
  · Design + bound Grounding 在盘上 ⇒ 内存编译计划视图 + 盘上 ctx（ctx 缺席/读不回
    ⇒ 右半边 `null`，`ctx_state` 说明处境——计划这一侧不陪葬）；
  · 尚无正式 Design/Grounding ⇒ **恒等基线**：`identity_plan` + `build_writer_context`
    同一对纯函数**现算不落盘**——「如果计划什么都不改，写手手里会有什么」。
    与 `--identity` 那条跑法共用内核，view 不写第二个组装器；
  · 恒等也算不出（stage1/stage2 缺）⇒ 404（`UpstreamMissing`）。

【判定不在这里】`in_band` / `style_hits` 是判据结论，唯一来源是 `/check/stage4`
（`R-11.4`）。本屏只摆事实：语义契约、预算、新场景与写手任务。
"""
from __future__ import annotations

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS, STAGE2_GLOBAL_GROUNDING,
    STAGE4_BLIND_CANDIDATE, STAGE4_EVIDENCE_BINDING, STAGE4_WRITER_CONTEXT,
)

import os
from pathlib import Path

from contracts.schema.api import (
    CtxChapterView, PlanChapterLine, PlanSceneLine, Stage4IndexRow, Stage4View,
)
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import ChapterBlueprints, GlobalGrounding
from contracts.schema.stage4 import (
    CtxChapter, EvidenceBinding, WriterContext,
)
from novel import paths, prov
from novel.errors import ArtifactUnreadable, UpstreamMissing, NovelError, SourceConflict
from novel.orchestrator.state import artifact_state, readonly_ctx
from novel.stages.stage4.beats import beat_index
from novel.stages.stage4.context import build_writer_context
from novel.stages.stage4.measure import (
    computed_structure_change_kinds, identity_plan, semantic_contract_of,
    structure_metrics_of,
)
from novel.stages.stage5.prompts import target_band
from novel.stages.stage5.measure import chapter_basis
from novel.store import ArtifactStore, readonly_snapshot_memo

NODE = 'stage4.plan'
PLAN_REL = STAGE4_BLIND_CANDIDATE
BINDING_REL = STAGE4_EVIDENCE_BINDING
CTX_REL = STAGE4_WRITER_CONTEXT
MAIN_TEXT = STAGE1_MAIN_TEXT
BLUEPRINTS = STAGE2_CHAPTER_BLUEPRINTS
GLOBAL_GROUNDING = STAGE2_GLOBAL_GROUNDING


def stage4_view(base: str | os.PathLike[str], sid: str, ordinal: int) -> Stage4View:
    """S-07 的一章。**纯读**：恒等基线现算在内存里，一个字节不落盘。"""
    from novel.orchestrator.inspection import read_surface_seal

    novel_dir = paths.resolve_novel_dir(base, sid)
    code = prov.code_version()
    before = read_surface_seal(base, sid, code=code)
    with readonly_snapshot_memo(novel_dir, code=code):
        result = _stage4_view(novel_dir, ordinal, code)
    if read_surface_seal(base, sid, code=code) != before:
        raise SourceConflict('读取设计页面期间材料变化，请刷新')
    return result


def _stage4_view(novel_dir: Path, ordinal: int, code: str) -> Stage4View:
    store = ArtifactStore(readonly_ctx(novel_dir, NODE, code))

    plan_state = artifact_state(store, PLAN_REL)
    binding_state = artifact_state(store, BINDING_REL)
    ctx_state = artifact_state(store, CTX_REL)
    unavailable = None
    blueprint_art, blueprint_raw = store.read_own(ChapterBlueprints, BLUEPRINTS)
    blueprints = blueprint_art.model_dump(by_alias=True)
    blueprint_by_ordinal = {
        int(row['chapter_ordinal']): row
        for row in blueprints['blueprints']
    }

    if plan_state.exists and binding_state.exists:
        binding, _binding_raw = store.read_own(EvidenceBinding, BINDING_REL)
        if binding.status == 'bound':
            from novel.stages.stage4.plan import compile_plan_view
            from novel.orchestrator.approvals import _structure_snapshot_from_artifacts
            plan_art, plan_raw = compile_plan_view(store)
            try:
                snapshot = _structure_snapshot_from_artifacts(
                    store, (plan_art, plan_raw), (binding, _binding_raw),
                    (blueprint_art, blueprint_raw))
            except NovelError as exc:
                structure_subject = None
                unavailable = str(exc)
            else:
                structure_subject = snapshot.subject_sha256
            identity = False
            plan_prov = plan_art.provenance
            ctx_chapters, ctx_prov = _ctx_from_disk(store)
        else:
            plan_raw, ctx_chapters = _identity(store, blueprints)
            structure_subject = None
            unavailable = '证据绑定尚未 bound，尚无正式已审计划'
            identity = True
            plan_prov = None
            ctx_prov = None
    else:
        plan_raw, ctx_chapters = _identity(store, blueprints)
        structure_subject = None
        unavailable = '免费参考基线，尚无正式已审计划'
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
        structure_subject_unavailable_reason=unavailable,
        blueprint=blueprint_by_ordinal[ordinal],
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
            beat_placements=pc['beat_placements'],
            anchor_bindings=pc['anchor_bindings'],
            scenes=[PlanSceneLine(
                **scene, i=i, beat_refs=[p['beat_ref'] for p in pc['beat_placements']
                    if any(d['scene_ref'] == scene['slot'] for d in p['deliveries'])]
            ) for i, scene in enumerate(pc['scenes'], start=1)]),
        ctx_chapter=_chapter_view(store, ctx_entry) if ctx_entry is not None else None,
        writer_target=target,
        plan_state=plan_state,
        ctx_state=ctx_state,
        _provenance=plan_prov,
        _ctx_provenance=ctx_prov,
    )


def _chapter_view(store: ArtifactStore, chapter: CtxChapter) -> CtxChapterView:
    """同一份任务书和当前源文现算展示读数；源文未知不吞掉可读的任务书。"""
    payload = chapter.model_dump(by_alias=True)
    try:
        main, _raw = store.read_own(MainText, MAIN_TEXT)
        source = {row.chapter_ordinal: row.text for row in main.root}
    except (UpstreamMissing, ArtifactUnreadable):
        source = {}
    basis = chapter_basis(
        payload, source if chapter.output_ordinal in source else None)
    assert basis.budget_chars is not None
    return CtxChapterView(
        **payload, from_source_ordinals=[chapter.output_ordinal],
        budget_chars=basis.budget_chars, source_chars=basis.source_chars)


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
    from novel.stages.stage2.knowledge import grounding as stage2_grounding
    gg_raw = stage2_grounding.read(store).model_dump(by_alias=True)
    bidx = beat_index(blueprints, main_raw)
    plan_raw = identity_plan(blueprints, main_raw, bidx)
    from novel.stages.stage2.knowledge.grounding import read_registry
    characters = read_registry(store).model_dump(mode='json', by_alias=True)
    asm = build_writer_context(plan_raw, gg_raw, blueprints, main_raw,
                               characters=characters)
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
    def actor_label(actor: dict) -> str:
        # 审校页可见内部引用，不能把两份呈现压成同一 char_id，
        # 也不能替 null 现场猜 canonical 或某个绑定。
        return actor['presentation_ref'] if actor.get('presentation_ref') else (
            f'{actor["char_id"]}（未指定呈现）')

    def subject_label(ref: dict) -> str:
        if ref['kind'] == 'character':
            return 'character:' + actor_label(ref['actor'])
        label = f'{ref["kind"]}:{ref["ref"]}'
        if ref['kind'] == 'relation':
            endpoints = ref['relation_actors']
            label += f'（{actor_label(endpoints["subject"])} → {actor_label(endpoints["object"])}）'
        return label

    refs = [subject_label(ref) for ref in (goal.get('subject_refs') or [])
            if isinstance(ref, dict)]
    return ' · '.join(part for part in (intent, '、'.join(refs)) if part)
