#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""project —— 双视图投影与场景消费身份（F4）。

【WriterContext 的唯一产地】每场从同一执行输入确定性投影：
VerificationIR（Guard/Judge 面）+ WriterBrief（Writer 面），共享
`execution_slice_sha256`（场景消费身份，三处一致：slice / context / receipt）。

【M5 surface 引擎（RC 版）】released_surfaces 的释放规则：
  • char 首次在先前或本场拍 participants 中出场 → 该 char 的
    IdentityProjection surfaces 全部可用；
  • canonical（真名面）额外要求 identity 类义务已在此前（或本场）承载。
试点教训的简化版：完整版（surface 逐条按 identity/reveal 义务逐面释放）等
F5 用真书暴露问题后再收紧——引擎只有这一份，Guard M5 查的也是它。

【预算】total × scene 权重；无权重场景按场数均分。
"""
from __future__ import annotations

import hashlib
import json
from typing import Iterable, Mapping

from contracts.schema.str06.context import (
    KnowledgeEntry, StateEntry, SurfaceRelease, VerificationIR, WriterBrief,
    WriterContext,
)
from contracts.schema.str06.design import PresentationGraph
from contracts.schema.str06.execution import ExecutionPlan, ExecutionSlice
from contracts.schema.str06.grounding import (
    IdentityProjection, PreserveCheckpoint, SemanticBinding,
)
from contracts.schema.str06.policy import PreserveKind
from contracts.schema.str06 import STR06_RC_VERSION
from novel.stages.str06.fold import FoldedState, fold_accepted_prefix  # noqa: F401（fold_accepted_prefix 供干跑调用方复用）

#: v4 source truth 的 surface 原文来源（IdentityProjection 不带 source 坐标，
#: 引擎从 graph 参与序给目标坐标）
SurfacesByChar = Mapping[str, tuple[str, ...]]


def surface_engine_release(
    graph: PresentationGraph,
    identity: tuple[IdentityProjection, ...],
    binding: SemanticBinding,
    checkpoint: PreserveCheckpoint,
    scene_order: Mapping[str, int],
    at_scene: str,
    surfaces_by_char: SurfacesByChar,
) -> tuple[SurfaceRelease, ...]:
    """M5 引擎：at_scene 起点可用的称谓面（目标坐标）。"""
    current = scene_order.get(at_scene, 0)
    appeared: set[str] = set()
    identity_borne: set[str] = set()
    obligation_kind = {o.obligation_id: o.kind for o in checkpoint.obligations}
    for entry in binding.entries:
        if (obligation_kind.get(entry.obligation_id) is PreserveKind.IDENTITY
                and scene_order.get(entry.scene_id, 0) <= current):
            identity_borne.add(_identity_char(entry, checkpoint))
    for scene in graph.scenes:
        if scene_order.get(scene.scene_id, 0) <= current:
            for beat in scene.beats:
                appeared.update(beat.participants)
    releases: list[SurfaceRelease] = []
    for unit in identity:
        if unit.char_id not in appeared:
            continue
        faces = set(surfaces_by_char.get(unit.char_id, ()))
        canonical = _canonical_of(unit)
        if canonical and unit.char_id in identity_borne:
            faces.add(canonical)
        if faces:
            releases.append(SurfaceRelease(char_id=unit.char_id, surfaces=tuple(sorted(faces))))
    return tuple(releases)


def _identity_char(entry, checkpoint: PreserveCheckpoint) -> str:
    oid = entry.obligation_id
    for o in checkpoint.obligations:
        if o.obligation_id == oid:
            return next(iter(o.lineage.character_ids), '')
    return ''


def _canonical_of(unit: IdentityProjection) -> str:
    # identity_statement 形如「裴书远（可用称呼：…）」——canonical 是首个括号前段
    return unit.identity_statement.split('（', 1)[0]


def _slice_content(
    scene_id: str,
    obligations: Iterable[str],
    entry_state: Iterable[StateEntry],
    entry_knowledge: Iterable[KnowledgeEntry],
) -> str:
    return json.dumps({
        'contract': STR06_RC_VERSION,
        'scene_id': scene_id,
        'obligations': sorted(obligations),
        'entry_state': [e.model_dump() for e in entry_state],
        'entry_knowledge': [e.model_dump() for e in entry_knowledge],
    }, ensure_ascii=False, sort_keys=True)


def project_context(
    *,
    scene_id: str,
    attempt_id: str,
    graph: PresentationGraph,
    plan: ExecutionPlan,
    binding: SemanticBinding,
    checkpoint: PreserveCheckpoint,
    identity: tuple[IdentityProjection, ...],
    surfaces_by_char: SurfacesByChar,
    folded_prefix: FoldedState,
    total_budget: int,
) -> tuple[WriterContext, ExecutionSlice]:
    """单场双视图投影 + 场景消费身份。纯函数；不修改任何输入。

    `folded_prefix` 是本场**之前**的累计状态（调用方 fold 而来）；
    IR 的 continuity_fact_view 从「已在先前场景承载的义务 statement 集」
    确定性渲染（试点 judge 跨场回指误报的根因修复输入）。
    """
    scene = next(s for s in graph.scenes if s.scene_id == scene_id)
    scene_plan = next(s for s in plan.scenes if s.scene_id == scene_id)
    order = {sid: i for i, sid in enumerate(
        sid for c in sorted(graph.chapters, key=lambda c: c.chapter_ordinal)
        for sid in c.scene_ids)}
    owned = tuple(sorted(
        e.obligation_id for e in binding.entries if e.scene_id == scene_id))
    entry_state = folded_prefix.entry_state_view(f'{scene_id}#start')
    entry_knowledge = folded_prefix.entry_knowledge_view()

    carried = {e.obligation_id for e in binding.entries
               if order.get(e.scene_id, 0) < order.get(scene_id, 0)}
    statement_of = {o.obligation_id: o.statement for o in checkpoint.obligations}
    continuity = tuple(statement_of[oid] for oid in sorted(carried))

    released = surface_engine_release(
        graph, identity, binding, checkpoint, order, scene_id, surfaces_by_char)
    # IR 的 planned_delta：本场拍 delta 的净终值（与 compile 的 planned_delta
    # 同序同语义，但保持 Design 原生 TargetDelta 形状——IR 不重复第二份语义）
    net_delta: dict[tuple, object] = {}
    for beat in scene.beats:
        for delta in beat.target_delta:
            net_delta[(delta.domain, delta.key)] = delta
    ir = VerificationIR(
        scene_id=scene_id, obligations=owned,
        entry_state=entry_state, entry_knowledge=entry_knowledge,
        released_surfaces=released, planned_delta=tuple(net_delta.values()),
        continuity_fact_view=continuity)

    weight = graph.chapter_weights.get(scene_id)
    if weight is None or weight <= 0:
        weight = 1.0 / max(len(graph.scenes), 1)
    brief = WriterBrief(
        scene_id=scene_id,
        task=f"兑现 {len(owned)} 条保存义务（{'；'.join(owned[:8])}…）"
        if len(owned) > 8 else f"兑现 {len(owned)} 条保存义务（{'；'.join(owned)}）",
        scene_driver_summary=(f'{scene.driver.focal_actor} 想「{scene.driver.immediate_want}」，'
                              f'受阻于「{scene.driver.opposition}」，转折：{scene.driver.turn}'),
        beat_outlines=tuple(b.event for b in scene.beats),
        constraints=tuple(
            f'{c.key}: {c.expected}' for c in scene_plan.constraints),
        length_budget=max(1, int(total_budget * weight)),
        voice=scene.delivery.voice)

    slice_sha = hashlib.sha256(
        _slice_content(scene_id, owned, entry_state, entry_knowledge)
        .encode('utf-8')).hexdigest()
    context = WriterContext(
        scene_id=scene_id, attempt_id=attempt_id,
        execution_slice_sha256=slice_sha,
        verification_ir=ir, writer_brief=brief)
    slice_ = ExecutionSlice(
        scene_id=scene_id, consumed_obligation_ids=owned,
        entry_state_sha256=hashlib.sha256(
            json.dumps([e.model_dump() for e in entry_state],
                       ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest(),
        entry_knowledge_sha256=hashlib.sha256(
            json.dumps([e.model_dump() for e in entry_knowledge],
                       ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest(),
        execution_slice_sha256=slice_sha)
    return context, slice_
