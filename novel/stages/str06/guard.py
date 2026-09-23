#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""guard —— 写前机械 Guard（M 系）RC 版（F2 免费前置；试点 M1–M6 知识接管）。

【mechanical-first】试点三轮的生产结论：机械检查免费、两代 run 全绿、反例全中，
常态化前置；语义检查走异常路径。本模块只做机械面，零 LLM。

【M 系在新坐标下的正式定义（试点定义逐条迁移）】
  M1 义务覆盖：本场 owner 的义务（Binding entries 归属本场者）必须全部进入
     WriterContext 的 VerificationIR.obligations。
  M2 提前揭示：IR.entry_knowledge 的 established_by 位置不得晚于本场起点。
  M3 提前效应：IR.entry_state 的 established_by 位置不得晚于本场起点。
  M4 承载位置真实：BindingEntry.beat_ref 必须指向所属场景的真实拍。
     （试点「bridge 拍不得携带义务」在新链无 bridge 拍，职责转译为承载位真实。）
  M6 在场合法性：IR.released_surfaces 的 char 必须是本场拍参与者，或已被
     CHARACTER 域 establishment 建立（累计合法在场）。
  M5 surface 时机（released == 引擎释放集）依赖 surface 窗口引擎，**留 F4**：
     需要 Characters 投影 × Binding 的目标坐标释放计算，本模块不猜。

【时序坐标】场景序整数（presentation_graph.chapters 的序 + 场在章内序）。
established_by 锚 `<scene_id>#…` 只取 scene_id 的序比较；`init` 恒合法。
"""
from __future__ import annotations

from typing import Mapping

from contracts.schema.str06.context import WriterContext
from contracts.schema.str06.design import PresentationGraph
from contracts.schema.str06.grounding import SemanticBinding
from contracts.schema.str06.execution import EffectDomain


def scene_order(graph: PresentationGraph) -> dict[str, int]:
    """场景的阅读序：章序为主、章内声明序为次；init/未知取 -1（恒早）。"""
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
    return order.get(scene_id, -1)


def check_obligation_coverage(
    binding: SemanticBinding, context: WriterContext,
) -> list[str]:
    """M1：本场 owner 的义务必须完整进入 IR（义务完整进入写手输入）。"""
    owned = {e.obligation_id for e in binding.entries
             if e.scene_id == context.scene_id}
    missing = owned - set(context.verification_ir.obligations)
    return [f'M1 义务覆盖：本场义务 {sorted(missing)} 未进入 VerificationIR'] if missing else []


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
    binding: SemanticBinding, graph: PresentationGraph,
) -> list[str]:
    """M4：承载位置必须指向真实场景/拍（跨产物存在性，schema 只能保前缀）。"""
    beats_by_scene = {
        scene.scene_id: {b.beat_id for b in scene.beats} for scene in graph.scenes}
    problems: list[str] = []
    for entry in binding.entries:
        if entry.scene_id not in beats_by_scene:
            problems.append(
                f'M4 承载位置：义务 {entry.obligation_id} 指向不存在的场景 {entry.scene_id!r}')
            continue
        if entry.beat_ref is not None:
            beat_id = entry.beat_ref.split('#', 1)[1] if '#' in entry.beat_ref else ''
            if beat_id not in beats_by_scene[entry.scene_id]:
                problems.append(
                    f'M4 承载位置：义务 {entry.obligation_id} 的 beat_ref '
                    f'{entry.beat_ref!r} 不是 {entry.scene_id!r} 的真实拍')
    return problems


def check_cast_legality(
    context: WriterContext, graph: PresentationGraph,
    folded_state,
) -> list[str]:
    """M6：released char ⊆ 累计合法在场（本场拍参与者 ∪ 已建立 CHARACTER 域）。

    `folded_state` 是本场**之前**的 FoldedState（调用方先 fold 前缀再查本场）。
    """
    problems: list[str] = []
    scene = next((s for s in graph.scenes if s.scene_id == context.scene_id), None)
    participants = {p for b in scene.beats for p in b.participants} if scene else set()
    for release in context.verification_ir.released_surfaces:
        if release.char_id in participants:
            continue
        if folded_state.value_of(EffectDomain.CHARACTER, release.char_id) is not None:
            continue
        problems.append(
            f'M6 在场合法性：char {release.char_id!r} 既非本场拍参与者，'
            f'也未在此前建立（累计合法在场）')
    return problems


def run_mechanical_guard(
    *,
    binding: SemanticBinding,
    context: WriterContext,
    graph: PresentationGraph,
    folded_prefix_state,
) -> list[str]:
    """写前机械 Guard 的聚合入口（M1/M2/M3/M4/M6；M5 见模块 docstring 留 F4）。

    只报告，不修改输入（I-24）；报告出口是 `stage4/acceptance/guard/` 机械报告。
    """
    order = scene_order(graph)
    return (
        check_obligation_coverage(binding, context)
        + check_entry_timing(context, order)
        + check_binding_beat_refs(binding, graph)
        + check_cast_legality(context, graph, folded_prefix_state)
    )
