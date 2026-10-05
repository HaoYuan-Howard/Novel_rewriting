#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""book_ir —— WriterBookIR 的确定性编译（F5，Stage4）。

【compiler 只投影，不解释】
从（BodyDesign, ExecutionPlan, SemanticBinding）按阅读序折叠出每场的双视图，
加上章目的、确定性章预算、导航与章级验证材料，组装全书静态写作 IR。不调用
LLM、不修改输入、不从自然语言补语义；canonical `realization_brief`／`purpose`
原样投影。

【章预算】
按 Design 场景权重汇总成章权重，整数分配保证总和恰等于书级目标
（largest remainder，同余数按章序先后）——不对照同号源章长度。章预算是
ChapterIR 的业务内容：改变预算即改变全书 IR 身份。

【章级验证材料（D2，B4）】
称谓许可（第 k 章含之前已释放的称谓字面）与禁用字面（全部专名／带名 surface
及家族级构词 − 已释放称谓）由 compiler 在 compile 时算好并写进 ChapterIR；
Stage5 的 Writer／Validator 只收章包，不再现场推导（不读 Design／Characters）。
`chapter_withheld_literals` 与 Hook 的 withheld 同一推导纪律：不写死人名。

【线程折叠（R2.4，设计原则 4）】跨章语义不走状态叠加：`compile_writer_book_ir`
用独立的 `ThreadFold` 按阅读序重放拍上的线程操作，逐场把 thread_ops（本场
操作，must_realize 数据面）与 open_threads（入场快照，continuity／guard_only
数据面）交给 `project_context` 进 VerificationIR；M8 与
`guard.expected_thread_faces` 对账，漂移当场红。
"""
from __future__ import annotations

import math
from typing import Mapping

from contracts.schema.book_ir import (
    BookBrief, ChapterContinuity, ChapterIR, ChapterNavEntry,
    PreviousChapterEnding, ReleaseScheduleEntry, WriterBookIR,
)
from contracts.schema.design import BodyDesign, ChapterAssignment
from contracts.schema.execution import ExecutionPlan
from contracts.schema.grounding import BriefFactLock, SemanticBinding
from contracts.schema.source import NameProjection, NameStructure
from contracts.schema.state import WithheldNameBinding
from novel.stages.stage4.fold import ThreadFold, fold_accepted_prefix
from novel.core.name_materials import withheld_name_bindings
from novel.stages.common import (
    consumption_closure, project_context, unanswered_guards,
    withheld_name_literals, expand_surface_releases,
)


def chapter_surface_releases(graph: BodyDesign, chapter_ordinal: int, structure: NameStructure | None = None) -> set[str]:
    """第 k 章（含）之前已释放的称谓字面：initial + 前 k 章内引入位置（D2：compile
    时算好的章级称谓许可，机械 validator 的认领面）。"""
    releases = {intro.char_id: set(intro.surfaces) for intro in graph.initial_surfaces}
    chapter_of = {sid: ch.chapter_ordinal for ch in graph.chapters for sid in ch.scene_ids}
    for scene in graph.scenes:
        if chapter_of[scene.scene_id] > chapter_ordinal:
            continue
        for beat in scene.beats:
            for intro in beat.surface_introductions:
                releases.setdefault(intro.char_id, set()).update(intro.surfaces)
    return {s for faces in expand_surface_releases(releases, structure).values() for s in faces}


def chapter_withheld_literals(
    projection: NameProjection, graph: BodyDesign, chapter_ordinal: int,
    structure: NameStructure | None = None,
) -> tuple[str, ...]:
    """章级释放表与共用专名／家族构词推导的差集。"""
    return withheld_name_literals(projection, chapter_surface_releases(graph, chapter_ordinal, structure),
                                  structure)


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


def chapters_with_budget_change(
    old: BodyDesign, new: BodyDesign, total_budget: int,
) -> set[int]:
    """场景相对篇幅（权重）变化的失效传导（精确失效表第二行的机械规则）。

    章预算是全书 largest-remainder 归一（`allocate_chapter_budgets`）——一个
    场景的权重变化会改变整数余数分配，可能波及多章；受影响章＝整数预算变化
    的章。这是「重投影候选范围」的判定材料（付费重写范围仍由义务/导演说明的
    直接受影响集另行判定）。纯函数。
    """
    before = allocate_chapter_budgets(old, total_budget)
    after = allocate_chapter_budgets(new, total_budget)
    return {ch for ch in set(before) | set(after)
            if before.get(ch) != after.get(ch)}


def _release_schedule(graph: BodyDesign) -> tuple[ReleaseScheduleEntry, ...]:
    """称谓释放导航：每个（人物, 实际释放章）一条，按（章, 人物）排序。

    同一人物在不同章释放的面分成多条：写手按章拿到的材料必须能回答「这一章
    哪些面已放开」，按人物取最早章会把后章才放开的称谓提前暴露给前章（F7e
    评估实证：Design 把 canonical 当作身份名交给写手，导航却显示首章即全开）。
    形状不变——仍由 `body_checks.chapter_surface_releases` 逐章对账。
    """
    by_chapter: dict[int, dict[str, set[str]]] = {}
    for intro in graph.initial_surfaces:
        by_chapter.setdefault(1, {}).setdefault(intro.char_id, set()).update(intro.surfaces)
    chapter_of = {sid: ch.chapter_ordinal for ch in graph.chapters for sid in ch.scene_ids}
    for scene in graph.scenes:
        chapter = chapter_of[scene.scene_id]
        for beat in scene.beats:
            for intro in beat.surface_introductions:
                by_chapter.setdefault(chapter, {}).setdefault(
                    intro.char_id, set()).update(intro.surfaces)
    return tuple(
        ReleaseScheduleEntry(char_id=cid, surfaces=tuple(sorted(surfaces)), from_chapter=chapter)
        for chapter in sorted(by_chapter)
        for cid, surfaces in sorted(by_chapter[chapter].items()))


def _scene_line(graph: BodyDesign, scene_id: str) -> str:
    """导航一行：确定性摘要（id + POV + driver），不新造语义。"""
    scene = next(s for s in graph.scenes if s.scene_id == scene_id)
    return (f'{scene_id}（pov={scene.delivery.pov}）'
            f'{scene.driver.focal_actor}想「{scene.driver.immediate_want}」'
            f'受阻于「{scene.driver.opposition}」')


def _chapter_continuity(
    graph: BodyDesign, assignment_previous: ChapterAssignment | None,
    assignment: ChapterAssignment,
) -> ChapterContinuity:
    """章级承接数据面（R2.6）：本章 entry＋上一章末场的机械投影。

    纯机械推导（I-21）：上一章末场＝该章 scene_ids 的最后一场；驱动转折＝
    末场 driver.turn 原样；末拍事件＝末场最后一拍 event 原样；在场人物＝
    末拍 participants 原序；「收尾时所在」只投影末场锚——Design 无地点字段，
    不猜地点语义。第 1 章 `previous_ending=None`（entry 固定开篇）。
    """
    previous_ending: PreviousChapterEnding | None = None
    if assignment_previous is not None:
        last_scene_id = assignment_previous.scene_ids[-1]
        last_scene = next(s for s in graph.scenes if s.scene_id == last_scene_id)
        last_beat = last_scene.beats[-1]
        previous_ending = PreviousChapterEnding(
            chapter_ordinal=assignment_previous.chapter_ordinal,
            ending_scene_id=last_scene_id,
            driver_turn=last_scene.driver.turn,
            final_beat_event=last_beat.event,
            present_chars=tuple(last_beat.participants))
    return ChapterContinuity(entry=assignment.entry,
                             previous_ending=previous_ending,
                             anchor_event=(graph.beat(*assignment.entry.anchor.split('#')).event
                                           if assignment.entry.anchor is not None else None))


def compile_writer_book_ir(
    *, graph: BodyDesign, plan: ExecutionPlan, binding: SemanticBinding,
    total_budget: int, length_band: tuple[float, float], book_title: str = '',
    projection: NameProjection, structure: NameStructure | None = None,
    fact_locks: tuple[BriefFactLock, ...] = (),
    kp_statements: Mapping[str, str] = {},
    world_statements: Mapping[str, str] = {},
) -> WriterBookIR:
    """全书静态写作 IR 的唯一产地。纯函数：不落盘、不调 LLM、不修改输入。

    `projection`／`structure`（Characters 的名称投影与名称结构）只用于在
    compile 时算好章级验证材料（D2）；材料写进 ChapterIR 后，Stage5 不再
    接触 Characters 或 Design。R10b 起 `fact_locks`（Brief 的事实锁）用于
    派生原文未回答禁区（`unanswered_guards`，从承载位置起进章包）；
    背景投影经 `consumption_closure` 收窄（书级初态立足点＋后文仍被消费
    的当前状态，C7）。`kp_statements`（Brief.propositions 的 kp→statement
    表）回填知情/读者增量的命题文本——value 恒为 "true"，不回填则写手面
    全是「确知「true」」（R10b 锁以内容送达；2026-10-03 D6 渲染修复）。
    `world_statements`（Brief 世界约束的 wc_{nn}→statement 表）同尺回填
    world 规则键的 value（CS-3：布尔规则键渲染零信息的收口）。
    """
    budgets = allocate_chapter_budgets(graph, total_budget)
    guards = unanswered_guards(graph, binding, fact_locks)
    consumers = consumption_closure(graph, binding.fact_bindings)
    chapters: list[ChapterIR] = []
    nav: list[ChapterNavEntry] = []
    scenes_before = 0
    previous_assignment: ChapterAssignment | None = None
    # 线程独立折叠（R2.4）：跨章重放一次，逐场把 thread_ops／open_threads 交给
    # 投影（M8 与 guard.expected_thread_faces 对账；缺省不接线时 M8 当场红）。
    thread_fold = ThreadFold()
    for assignment in sorted(graph.chapters, key=lambda c: c.chapter_ordinal):
        contexts, lines = [], []
        for scene_id in assignment.scene_ids:
            scene = next(s for s in graph.scenes if s.scene_id == scene_id)
            thread_ops, open_threads = thread_fold.apply(scene)
            context, _slice = project_context(
                scene_id=scene_id, graph=graph, plan=plan, binding=binding,
                folded_prefix=fold_accepted_prefix(
                    plan.scenes[:scenes_before], initial=plan.initial_establishments),
                thread_ops=thread_ops, open_threads=open_threads,
                consumers=consumers, unanswered_guards=guards,
                kp_statements=kp_statements, world_statements=world_statements)
            contexts.append(context)
            lines.append(_scene_line(graph, scene_id))
            scenes_before += 1
        ordinal = assignment.chapter_ordinal
        withheld = chapter_withheld_literals(projection, graph, ordinal, structure)
        released_here = tuple(sorted(chapter_surface_releases(graph, ordinal, structure)))
        chapters.append(ChapterIR(
            chapter_ordinal=ordinal, purpose=assignment.purpose,
            length_budget=budgets[ordinal], scenes=tuple(contexts),
            continuity=_chapter_continuity(graph, previous_assignment, assignment),
            released_surfaces=released_here,
            withheld_literals=withheld,
            withheld_bindings=tuple(
                WithheldNameBinding(literal=literal, char_id=char_id, enclosing=enclosing)
                for literal, char_id, enclosing
                in withheld_name_bindings(withheld, structure, released_here))))
        nav.append(ChapterNavEntry(
            chapter_ordinal=ordinal, purpose=assignment.purpose,
            scene_ids=tuple(assignment.scene_ids), scene_lines=tuple(lines)))
        previous_assignment = assignment
    brief = BookBrief(
        book_title=book_title,
        book_style=graph.book_style,
        narration=graph.narration, expression_decisions=graph.expression_decisions,
        world_rules=tuple(graph.world_rules),
        character_voices=tuple(graph.character_voices),
        target_chapter_count=len(graph.chapters),
        total_length_budget=total_budget, length_band_low=length_band[0],
        length_band_high=length_band[1], chapters=tuple(nav),
        release_schedule=_release_schedule(graph))
    return WriterBookIR(book_brief=brief, chapters=tuple(chapters))
