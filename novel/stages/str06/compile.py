#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compile —— (Design, Binding) → ExecutionPlan 的确定性编译（F4）。

【四类执行语义的编译来源】
  • establishment：每拍 target_delta → book 作用域建立（established_by=场#拍，
    场内即生效；同一批内容同时构成 planned_delta——只有场景被有效接受后才
    向下游传播。RC 限制：Design delta 无 scene/book scope 字段，统一 book，
    scope 精细化留 F5+ 迭代）。
  • constraint：must_preserve 的非事件义务（identity/world_fact/initial_state/
    causal）→ 谓词约束（key=obligation_id，expected=statement）。reveal/arc 类
    由 establishment 与 IR 义务面承载，不重复编译。
  • dependency：义务 requires × 承载位置 → 跨场依赖边（同场不建——拍序已
    表达）；场景级成环是设计错误，编译期拒绝。
  • reinforcement：RC 期为空列表（结构在、来源留 Design 显式信号后接入）——
    类型上无义务通道，0..N 天然不稀释必保。
"""
from __future__ import annotations

from contracts.schema.str06.design import PresentationGraph
from contracts.schema.str06.execution import (
    ConstraintSpec, DependencyEdge, EffectDomain, Establishment,
    ExecutionPlan, SceneExecutionPlan,
)
from contracts.schema.str06.grounding import PreserveCheckpoint, SemanticBinding
from contracts.schema.str06.policy import PreserveKind
from contracts.schema.str06.source import (
    BlueprintsArtifactV12, CharactersArtifactV4,
)
from pydantic import ValidationError

#: 非事件义务 → constraint 的域映射
_CONSTRAINT_DOMAINS = {
    PreserveKind.IDENTITY: EffectDomain.CHARACTER,
    PreserveKind.WORLD_FACT: EffectDomain.WORLD,
    PreserveKind.INITIAL_STATE: EffectDomain.WORLD,
    PreserveKind.CAUSAL: EffectDomain.READER,
}

_SCENE_FOR_KIND = {PreserveKind.REVEAL, PreserveKind.CHARACTER_ARC, PreserveKind.EVENT_CORE}


def compile_execution_plan(
    graph: PresentationGraph, binding: SemanticBinding,
    checkpoint: PreserveCheckpoint,
) -> tuple[ExecutionPlan | None, list[str]]:
    """(D,G) → ExecutionPlan。输入 canonical，输出 Derived；不修改输入。"""
    errors: list[str] = []
    obligation_kind = {o.obligation_id: (o.kind, o.must_preserve, o.statement)
                       for o in checkpoint.obligations}
    scene_of: dict[str, str] = {}
    for entry in binding.entries:
        scene_of[entry.obligation_id] = entry.scene_id

    scenes: list[SceneExecutionPlan] = []
    for scene in graph.scenes:
        establishments: list[Establishment] = []
        constraints: list[ConstraintSpec] = []
        owned = {e.obligation_id for e in binding.entries if e.scene_id == scene.scene_id}
        for beat in scene.beats:
            for delta in beat.target_delta:
                try:
                    establishments.append(Establishment(
                        domain=delta.domain, key=delta.key,
                        value=delta.value if isinstance(delta.value, str) else str(delta.value),
                        scope='book'))
                except ValidationError as exc:
                    errors.append(f'{scene.scene_id}#{beat.beat_id} delta 编译失败：{exc}')
        for oid in owned:
            kind, must, statement = obligation_kind[oid]
            if kind in _CONSTRAINT_DOMAINS and must:
                constraints.append(ConstraintSpec(
                    domain=_CONSTRAINT_DOMAINS[kind], key=oid,
                    expected=statement, scope='book'))
        # planned_delta：拍级建立按序覆盖后的**净终值**（每 (domain,key) 取
        # 最后一次；接受后向下游传播的就是这批净变化）
        net: dict[tuple, Establishment] = {}
        for est in establishments:
            net[(est.domain, est.key)] = est
        scenes.append(SceneExecutionPlan(
            scene_id=scene.scene_id,
            establishments=tuple(establishments),
            constraints=tuple(constraints),
            planned_delta=tuple(net.values())))

    # dependency：requires × 承载场景（同场不建，去重）
    edges: dict[tuple[str, str], DependencyEdge] = {}
    for obligation in checkpoint.obligations:
        target_scene = scene_of.get(obligation.obligation_id)
        if target_scene is None:
            continue
        for req in obligation.requires:
            req_scene = scene_of.get(req)
            if req_scene is None or req_scene == target_scene:
                continue
            key = (req_scene, target_scene)
            edges.setdefault(key, DependencyEdge(
                before=req_scene, after=target_scene, kind='obligation_requires'))
    # 场景级环检测（设计错误，fail fast）
    adj: dict[str, list[str]] = {}
    for edge in edges.values():
        adj.setdefault(edge.before, []).append(edge.after)
    for start in adj:
        stack, seen = list(adj[start]), set()
        while stack:
            node = stack.pop()
            if node == start:
                errors.append(f'场景依赖成环：{start} -> ... -> {node}')
                break
            if node in seen:
                continue
            seen.add(node)
            stack.extend(adj.get(node, []))
    if errors:
        return None, errors
    # 依赖边挂到 after 场（执行该场前必须完成的前置）
    plan_scenes = []
    by_id = {s.scene_id: s for s in scenes}
    for scene in scenes:
        incoming = tuple(e for e in edges.values() if e.after == scene.scene_id)
        plan_scenes.append(SceneExecutionPlan(
            scene_id=scene.scene_id,
            establishments=scene.establishments,
            constraints=scene.constraints,
            dependencies=incoming,
            planned_delta=scene.planned_delta))
    return ExecutionPlan(scenes=tuple(plan_scenes)), []


def init_establishments(
    characters: CharactersArtifactV4, blueprint: BlueprintsArtifactV12,
) -> tuple[Establishment, ...]:
    """T₀ 的书级初始建立：v4 initial_state + v12 world_constraints → init 锚。

    `T₀ = Init(Compile(D,G))` 的 Init 材料；knowledge 型初始态映射为
    `holder::key` 键（与 fold 的 knowledge 键约定一致）。
    """
    out: list[Establishment] = []
    for atom in characters.initial_state:
        domain = (EffectDomain.RELATION if atom.target.kind == 'relation'
                  else EffectDomain.CHARACTER)
        key = atom.key
        if atom.target.kind == 'relation':
            key = f'{atom.target.subject}->{atom.target.object}::{atom.key}'
        if atom.knowledge:
            for holder in atom.knowledge.known_by:
                out.append(Establishment(
                    domain=EffectDomain.KNOWLEDGE, key=f'{holder}::{key}',
                    value=_text(atom.value), scope='book'))
        out.append(Establishment(
            domain=domain, key=key, value=_text(atom.value), scope='book'))
    for wc in blueprint.world_constraints:
        out.append(Establishment(
            domain=EffectDomain.WORLD, key=wc.constraint_id,
            value=wc.statement, scope='book'))
    return tuple(out)


def _text(value: object) -> str:
    return value if isinstance(value, str) else str(value)
