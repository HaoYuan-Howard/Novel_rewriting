#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""book_ir —— WriterBookIR 的确定性编译（F5，Stage4）。

【compiler 只投影，不解释】
从（BodyDesign, ExecutionPlan, SemanticBinding）按阅读序折叠出每场的双视图，
加上章目的、确定性章预算与导航，组装全书静态写作 IR。不调用 LLM、不修改
输入、不从自然语言补语义；canonical `realization_brief`／`purpose` 原样投影。

【章预算】
按 Design 场景权重汇总成章权重，整数分配保证总和恰等于书级目标
（largest remainder，同余数按章序先后）——不对照同号源章长度。章预算是
ChapterIR 的业务内容：改变预算即改变全书 IR 身份。
"""
from __future__ import annotations

import math

from contracts.schema.book_ir import (
    BookBrief, ChapterIR, ChapterNavEntry, ReleaseScheduleEntry, WriterBookIR,
)
from contracts.schema.design import BodyDesign
from contracts.schema.execution import ExecutionPlan
from contracts.schema.grounding import SemanticBinding
from novel.stages.stage4.fold import fold_accepted_prefix
from novel.stages.common import project_context


def chapter_scene_weights(graph: BodyDesign) -> dict[int, float]:
    """章权重 = 章内场景权重之和（未登记权重时场景均分）。"""
    scene_ids = [s.scene_id for s in graph.scenes]
    weights = graph.chapter_weights or {sid: 1 / len(scene_ids) for sid in scene_ids}
    per_chapter: dict[int, float] = {}
    for chapter in sorted(graph.chapters, key=lambda c: c.chapter_ordinal):
        per_chapter[chapter.chapter_ordinal] = math.fsum(
            weights[sid] for sid in chapter.scene_ids)
    return per_chapter


def allocate_chapter_budgets(graph: BodyDesign, total_budget: int) -> dict[int, int]:
    """整数章预算：floor 分配 + 最大余数补齐，总和恰等于 total_budget。"""
    weights = chapter_scene_weights(graph)
    ordinals = sorted(weights)
    raw = {ch: total_budget * weights[ch] for ch in ordinals}
    floors = {ch: int(raw[ch]) for ch in ordinals}
    remainder = total_budget - sum(floors.values())
    order = sorted(ordinals, key=lambda ch: (-(raw[ch] - floors[ch]), ch))
    for ch in order[:remainder]:
        floors[ch] += 1
    assert sum(floors.values()) == total_budget
    return floors


def _release_schedule(graph: BodyDesign) -> tuple[ReleaseScheduleEntry, ...]:
    """称谓释放导航：每人物每个称谓面的最早可用章（initial 即第 1 章）。"""
    first: dict[str, dict[str, int]] = {}
    for intro in graph.initial_surfaces:
        for surface in intro.surfaces:
            first.setdefault(intro.char_id, {}).setdefault(surface, 1)
    chapter_of = {sid: ch.chapter_ordinal for ch in graph.chapters for sid in ch.scene_ids}
    for scene in graph.scenes:
        chapter = chapter_of[scene.scene_id]
        for beat in scene.beats:
            for intro in beat.surface_introductions:
                for surface in intro.surfaces:
                    first.setdefault(intro.char_id, {}).setdefault(surface, chapter)
    return tuple(sorted(
        (ReleaseScheduleEntry(char_id=cid, surfaces=tuple(sorted(by_surface)),
                              from_chapter=min(by_surface.values()))
         for cid, by_surface in first.items()),
        key=lambda e: e.char_id))


def _scene_line(graph: BodyDesign, scene_id: str) -> str:
    """导航一行：确定性摘要（id + POV + driver），不新造语义。"""
    scene = next(s for s in graph.scenes if s.scene_id == scene_id)
    return (f'{scene_id}（pov={scene.delivery.pov}）'
            f'{scene.driver.focal_actor}想「{scene.driver.immediate_want}」'
            f'受阻于「{scene.driver.opposition}」')


def compile_writer_book_ir(
    *, graph: BodyDesign, plan: ExecutionPlan, binding: SemanticBinding,
    total_budget: int, length_band: tuple[float, float], book_title: str = '',
) -> WriterBookIR:
    """全书静态写作 IR 的唯一产地。纯函数：不落盘、不调 LLM、不修改输入。"""
    budgets = allocate_chapter_budgets(graph, total_budget)
    chapters: list[ChapterIR] = []
    nav: list[ChapterNavEntry] = []
    scenes_before = 0
    for assignment in sorted(graph.chapters, key=lambda c: c.chapter_ordinal):
        contexts, lines = [], []
        for scene_id in assignment.scene_ids:
            context, _slice = project_context(
                scene_id=scene_id, graph=graph, plan=plan, binding=binding,
                folded_prefix=fold_accepted_prefix(
                    plan.scenes[:scenes_before], initial=plan.initial_establishments))
            contexts.append(context)
            lines.append(_scene_line(graph, scene_id))
            scenes_before += 1
        chapters.append(ChapterIR(
            chapter_ordinal=assignment.chapter_ordinal, purpose=assignment.purpose,
            length_budget=budgets[assignment.chapter_ordinal], scenes=tuple(contexts)))
        nav.append(ChapterNavEntry(
            chapter_ordinal=assignment.chapter_ordinal, purpose=assignment.purpose,
            scene_ids=tuple(assignment.scene_ids), scene_lines=tuple(lines)))
    brief = BookBrief(
        book_title=book_title, target_chapter_count=len(graph.chapters),
        total_length_budget=total_budget, length_band_low=length_band[0],
        length_band_high=length_band[1], chapters=tuple(nav),
        release_schedule=_release_schedule(graph))
    return WriterBookIR(book_brief=brief, chapters=tuple(chapters))
