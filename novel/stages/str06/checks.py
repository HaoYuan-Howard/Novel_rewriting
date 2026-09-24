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
import json
from typing import Iterable, Mapping

from pydantic import BaseModel

from contracts.schema.str06.design import BodyDesign
from contracts.schema.str06.execution import ExecutionPlan, ExecutionSlice
from contracts.schema.str06.grounding import PreserveCheckpoint, SemanticBinding
from contracts.schema.str06.receipt import ChapterReceipt
from contracts.schema.str06.context import WriterContext


def canonical_sha256(model: BaseModel) -> str:
    """规范化内容摘要：JSON 对象键排序，数组保留业务顺序，不依赖进程 hash seed。

    receipt / slice / context 的内容绑定共用这一把尺子（单一身份来源，I-16）。
    """
    payload = json.dumps(model.model_dump(mode='json'), ensure_ascii=False,
                         sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def check_binding_covers_checkpoint(
    checkpoint: PreserveCheckpoint, binding: SemanticBinding,
) -> list[str]:
    """Binding 必须与采用的 Preserve 结果一致：全分母、不多不少。

    「不发布半份 canonical Binding」与「unbound 不删义务」的可机检面：
    丢失义务（既不在 entries 也不在 unbound）与混入 checkpoint 外的义务都报。
    """
    problems: list[str] = []
    if binding.obligations != checkpoint.obligations:
        problems.append('Binding 义务内容与采用的 Preserve 不一致')
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
    graph: BodyDesign, plan: ExecutionPlan,
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


def check_chapter_receipt(
    receipt: ChapterReceipt, *, book_ir_sha256: str,
    prior_text_sha256: str, prose_sha256: str,
) -> list[str]:
    """章级回执与当前输入的绑定核对：旧回执配新输入必须拒绝。

    全书 IR 变化使全部回执失效；第 k 章前文变化使该章回执失效；正文字节变化
    使回执与文本脱钩。三份绑定逐一核对（I-16：回执不是第二份业务权威）。
    """
    problems: list[str] = []
    if receipt.status != 'accepted':
        problems.append(f'回执状态为 {receipt.status}，不构成有效提交')
    if receipt.book_ir_sha256 != book_ir_sha256:
        problems.append('book_ir_sha256 与当前全书 IR 不一致（IR 变化使全书回执失效）')
    if receipt.prior_text_sha256 != prior_text_sha256:
        problems.append('prior_text_sha256 与当前有效前文不一致（前缀变化使本章失效）')
    if receipt.prose_sha256 != prose_sha256:
        problems.append('prose_sha256 与章文本不匹配（回执与正文脱钩）')
    return problems


def affected_chapters(
    directly_affected_scenes: Iterable[str], graph: BodyDesign,
) -> set[int]:
    """场景级窄修复 → 章级失效范围：受影响场景所在的目标章。

    第 k 章内任何场景输入变化 ⇒ 第 k 章回执失效；后续章因前文变化按
    「连续有效章前缀」语义另行截断（见 check_chapter_receipt 的 prior 绑定）。
    """
    scene_chapter = {sid: ch.chapter_ordinal for ch in graph.chapters for sid in ch.scene_ids}
    return {scene_chapter[sid] for sid in directly_affected_scenes if sid in scene_chapter}


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
