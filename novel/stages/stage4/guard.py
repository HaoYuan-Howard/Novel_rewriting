#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""机械 Guard：M1 完整义务内容、M2/M3 已建立输入时序、M4 真实承载拍、
M5 显式称谓释放、M6 合法人物。未知锚拒绝；只报告，不修复或修改输入。
"""
from __future__ import annotations

from typing import Mapping

from contracts.schema.context import WriterContext
from contracts.schema.design import BodyDesign
from contracts.schema.grounding import SemanticBinding



def scene_order(graph: BodyDesign) -> dict[str, int]:
    """场景的阅读序：章序为主、章内声明序为次；init 恒早；未知锚不合法。"""
    order: dict[str, int] = {}
    position = 0
    for chapter in sorted(graph.chapters, key=lambda c: c.chapter_ordinal):
        for scene_id in chapter.scene_ids:
            order[scene_id] = position
            position += 1
    return order


def _anchor_order(anchor: str, order: Mapping[str, int]) -> int:
    if anchor == 'init':
        return -1
    scene_id = anchor.split('#', 1)[0]
    return order.get(scene_id, len(order))


def check_obligation_coverage(
    binding: SemanticBinding, context: WriterContext,
) -> list[str]:
    """M1：本场 owner 的义务必须完整进入 IR（义务完整进入写手输入）。"""
    owned = {e.obligation_id for e in binding.entries
             if e.scene_id == context.scene_id}
    missing = owned - {o.obligation_id for o in context.verification_ir.obligations}
    expected = tuple(sorted((o for o in binding.obligations if o.obligation_id in owned),
                            key=lambda o: o.obligation_id))
    if tuple(context.verification_ir.obligations) != expected:
        return [f'M1 义务覆盖或内容不一致：{sorted(missing)}']
    return []


def check_entry_timing(
    context: WriterContext, order: Mapping[str, int],
) -> list[str]:
    """M2/M3：entry 项的 established_by 必须早于本场起点。"""
    problems: list[str] = []
    start = order.get(context.scene_id, 0)
    for entry in context.verification_ir.entry_state:
        if _anchor_order(entry.established_by, order) >= start:
            problems.append(
                f'M3 提前效应：entry_state[{entry.key}] 的 established_by='
                f'{entry.established_by} 不早于本场起点')
    for entry in context.verification_ir.entry_knowledge:
        if _anchor_order(entry.established_by, order) >= start:
            problems.append(
                f'M2 提前揭示：entry_knowledge[{entry.holder}:{entry.proposition}] '
                f'的 established_by={entry.established_by} 不早于本场起点')
    return problems


def check_binding_beat_refs(
    binding: SemanticBinding, graph: BodyDesign,
) -> list[str]:
    """M4：承载位置必须指向真实场景/拍（跨产物存在性，schema 只能保前缀）。"""
    refs = {f'{s.scene_id}#{b.beat_id}' for s in graph.scenes for b in s.beats}
    problems = []
    for entry in binding.entries:
        for ref in (entry.beat_ref, *entry.supporting_beat_refs):
            if ref not in refs:
                problems.append(f'M4 承载位置：{entry.obligation_id} 的 {ref!r} 不是真实拍')
    return problems



def check_cast_legality(
    context: WriterContext, graph: BodyDesign,
    appeared: set[str],
) -> list[str]:
    """M6：人物须由 Design 声明参与或引入；提及未到场人物同样合法。"""
    problems: list[str] = []
    for release in context.verification_ir.released_surfaces:
        if release.char_id not in appeared:
            problems.append(
                f'M6 人物合法性：char {release.char_id!r} 尚未由 Design 声明参与或引入')
    return problems


def appeared_chars(
    graph: BodyDesign, order: Mapping[str, int], upto_scene: str,
) -> set[str]:
    """upto_scene（含当场）之前合法参与或被引入的人物。"""
    current = order.get(upto_scene, 0)
    appeared = {intro.char_id for intro in graph.initial_surfaces}
    for scene in graph.scenes:
        if order.get(scene.scene_id, 0) <= current:
            for beat in scene.beats:
                appeared.update(beat.participants)
                appeared.update(intro.char_id for intro in beat.surface_introductions)
    return appeared


def run_mechanical_guard(
    *,
    binding: SemanticBinding,
    context: WriterContext,
    graph: BodyDesign,
) -> list[str]:
    """写前 M1–M6 的唯一聚合入口，使用同一称谓释放引擎。"""
    from novel.stages.common import surface_engine_release
    expected_surfaces = surface_engine_release(graph, context.scene_id)
    surface_errors = ([] if context.verification_ir.released_surfaces == expected_surfaces
                      else ['M5 称谓释放与 Design 显式释放集不一致'])
    order = scene_order(graph)
    appeared = appeared_chars(graph, order, context.scene_id)
    return (
        surface_errors + check_obligation_coverage(binding, context)
        + check_entry_timing(context, order)
        + check_binding_beat_refs(binding, graph)
        + check_cast_legality(context, graph, appeared)
    )
