#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""checks —— STR-06 跨产物机械判定器（F1 反例批次的判定入口）。

【定位】validator：只验证并报告，不修改输入、不落盘、不调 LLM。这些是 F0 文档
登记的「跨产物不变量」的 RC 判定实现；F4 挂接真实 Compile 产物、F5 挂接 receipt
时直接消费本模块，F8 随迁移包分流进 stage4/stage5 正式判据面（届时才进
REGISTRY——RC 期不注册现役判据）。

【窄修复的两段语义（试点 A 批的免费版）】
  • `directly_affected`：**付费重写范围的判定材料**——只看「本场消费的义务是否
    被改变」。输入未变的场景不进集合，accepted prefix 继续有效。
  • `entry_sensitive_closure`：**重投影候选范围**——依赖了直接受影响场景的下游
    必须重投影核验；但重投影后输入身份未变的结果继续采用（不自动付费重写）。
    扫描范围 ≠ 付费重跑范围，两段分开就是为了让这句话可执行。
"""
from __future__ import annotations

import hashlib
from typing import Iterable, Mapping

from pydantic import BaseModel

from contracts.schema.str06.design import PresentationGraph
from contracts.schema.str06.execution import ExecutionPlan, ExecutionSlice
from contracts.schema.str06.grounding import PreserveCheckpoint, SemanticBinding
from contracts.schema.str06.receipt import SceneReceipt
from contracts.schema.str06.context import WriterContext


def canonical_sha256(model: BaseModel) -> str:
    """规范化内容摘要：pydantic 字段序确定，`model_dump_json` 即规范字节。

    receipt / slice / context 的内容绑定共用这一把尺子（单一身份来源，I-16）。
    """
    payload = model.model_dump_json(exclude_none=False).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def check_binding_covers_checkpoint(
    checkpoint: PreserveCheckpoint, binding: SemanticBinding,
) -> list[str]:
    """Binding 必须与采用的 Preserve 结果一致：全分母、不多不少。

    「不发布半份 canonical Binding」与「unbound 不删义务」的可机检面：
    丢失义务（既不在 entries 也不在 unbound）与混入 checkpoint 外的义务都报。
    """
    problems: list[str] = []
    denominator = {o.obligation_id for o in checkpoint.obligations}
    bound = {e.obligation_id for e in binding.entries}
    returned = {u.obligation_id for u in binding.unbound}
    missing = denominator - bound - returned
    if missing:
        problems.append(f'义务从 Binding 中消失（既未承载也未返回 owner）：{sorted(missing)}')
    invented = (bound | returned) - denominator
    if invented:
        problems.append(f'Binding 混入了 checkpoint 外的义务：{sorted(invented)}')
    if binding.preserve_checkpoint_sha256 != checkpoint.checkpoint_sha256:
        problems.append('Binding 引用的 checkpoint 身份与实际 checkpoint 不一致')
    return problems


def check_plan_matches_graph(
    graph: PresentationGraph, plan: ExecutionPlan,
) -> list[str]:
    """Compiler 只验证章数、归属、顺序和完整性——本函数的「完整性」那一段：
    plan 的场景集合必须与 Design 的场景集合一致，不多不少。"""
    problems: list[str] = []
    design_scenes = {s.scene_id for s in graph.scenes}
    plan_scenes = {s.scene_id for s in plan.scenes}
    extra = plan_scenes - design_scenes
    if extra:
        problems.append(f'plan 引用了 Design 之外的场景：{sorted(extra)}')
    dropped = design_scenes - plan_scenes
    if dropped:
        problems.append(f'Design 的场景未被编译进 plan：{sorted(dropped)}')
    return problems


def check_receipt_binding(
    receipt: SceneReceipt, context: WriterContext,
    *, execution_slice_sha256: str,
) -> list[str]:
    """旧 PASS 配新 Context／slice 必须拒绝：三份绑定逐一核对。"""
    problems: list[str] = []
    if receipt.scene_id != context.scene_id:
        problems.append(f"receipt 场景 {receipt.scene_id!r} 与 context 场景 {context.scene_id!r} 不一致")
    actual_context = canonical_sha256(context)
    if receipt.writer_context_sha256 != actual_context:
        problems.append('writer_context_sha256 与当前 Context 内容不匹配（旧 PASS 配新 Context）')
    if receipt.execution_slice_sha256 != execution_slice_sha256:
        problems.append('execution_slice_sha256 与当前执行切片身份不匹配（旧 PASS 配新 slice）')
    return problems


def directly_affected(
    changed_obligation_ids: Iterable[str],
    slices: Mapping[str, ExecutionSlice],
) -> set[str]:
    """窄修复第一段：直接受影响场景 = 消费了被改义务的场景。

    前缀有效性的判定材料：不在集合内的场景输入未变，已购结果与 accepted
    prefix 继续有效。
    """
    changed = set(changed_obligation_ids)
    return {
        scene_id for scene_id, sl in slices.items()
        if changed.intersection(sl.consumed_obligation_ids)
    }


def entry_sensitive_closure(
    directly_affected_scenes: Iterable[str], plan: ExecutionPlan,
) -> set[str]:
    """窄修复第二段：重投影候选 = 直接受影响 ∪ 其依赖下游闭包。

    只圈「必须重投影核验」的范围；重投影后输入身份未变的场景继续采用，
    不自动进入付费重写。语义依据：planned_delta 的唯一产地是 ExecutionPlan，
    正文重写不改变下游 entry；只有上游约束真实变化时下游才落在闭包里。
    """
    affected = set(directly_affected_scenes)
    dependents: dict[str, set[str]] = {}
    for scene in plan.scenes:
        for edge in scene.dependencies:
            dependents.setdefault(edge.before, set()).add(edge.after)
    frontier = list(affected)
    while frontier:
        current = frontier.pop()
        for nxt in dependents.get(current, ()):  # noqa: E501（BFS 闭包）
            if nxt not in affected:
                affected.add(nxt)
                frontier.append(nxt)
    return affected
