#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""execution —— ExecutionPlan（四类执行语义）与场景消费身份（F0 RC）。

【Derived 定位】
ExecutionPlan 由 Compile 从（PresentationGraph, SemanticBinding）确定性产出，
唯一落盘 `stage4/compiled/execution_plan.json`；不是第四 truth。试点 shape 的
execution_table / resolved_plan 概念已退役，本模块是终态替代。

【四类执行语义】
establishment（建立）、constraint（谓词+作用域）、dependency（DAG）、
reinforcement（强化）。**必保义务只落在 establishment / constraint 上**；
reinforcement 类型上没有 obligation_id 字段——0..N 强化天然不能稀释必保，
这是「不能被 reinforcement 稀释」的类型层表达，而不是注释提醒。

【场景消费身份】
`ExecutionSlice` 是「本场实际消费的规范化业务内容」的身份：F4 的确定性投影函数
计算内容并取 SHA-256；本模块只定义身份字段。入口、freshness、历史采用与 receipt
共用这一份投影（单一身份来源，I-16）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.common import FreeProvenance, LlmProvenance
from contracts.schema.state import EffectDomain, StateChange


class Establishment(StateChange):
    """执行期建立：state/knowledge 的显式建立点（established_by 的锚）。"""

    #: 作用域：book（书级持续）| scene（场内有效，边界即失效——试点「场景局部
    #: 状态无衰减」缺陷的对策）。unknown/解除/未运行不在本类型上，它们是
    #: runtime 状态语义（F4 的 Init/Apply 表达）。
    established_by: str = Field(min_length=1)
    #: 事实时态（a4，TargetDelta 同形透传）：折叠面只按地址/操作/值折叠，
    #: modality 不参与折叠判等；renderer v7（R10b）经拍级 delta 直接送达。
    modality: "Literal['actual', 'planned', 'expected', 'conditional'] | None" = None


class ConstraintSpec(StrictModel):
    """执行期谓词约束：Predicate + Scope。"""

    domain: EffectDomain
    key: str = Field(min_length=1)
    expected: str = Field(min_length=1)
    scope: str = Field(pattern=r'^(book|scene)$')


class DependencyEdge(StrictModel):
    """场景/拍级先后依赖（DAG 边）。环检测归 F4 Guard；这里只保证边自身形状。"""

    before: str = Field(min_length=1)
    after: str = Field(min_length=1)
    kind: str = Field(min_length=1)

    @model_validator(mode='after')
    def _not_self_loop(self) -> 'DependencyEdge':
        if self.before == self.after:
            raise ValueError('依赖边不得自指（DAG）')
        return self


class Reinforcement(StrictModel):
    """强化：对已建立事实的重复强调，0..N。

    刻意**没有** obligation_id / must_preserve 字段——强化不是承载义务的位置；
    必保只能由 establishment / constraint 满足。
    """

    target_key: str = Field(min_length=1)
    note: str = Field(default='', min_length=0)


class SceneExecutionPlan(StrictModel):
    """单场执行计划：四类执行项 + planned_delta。

    `planned_delta` 只在场景**有效接受后**由 runtime 应用（Apply planned delta）；
    禁止从正文反抽 semantic delta——所以 delta 的唯一产地是本计划（Design+Bind
    编译），runtime 只重放。establishments 允许同 (domain,key) 多次出现：
    拍级覆盖是合法语义（fold 按序应用，后者生效），唯一性不是拍序的职责。
    """

    scene_id: str = Field(min_length=1)
    establishments: tuple[Establishment, ...] = Field(default=(), min_length=0)
    constraints: tuple[ConstraintSpec, ...] = Field(default=(), min_length=0)
    dependencies: tuple[DependencyEdge, ...] = Field(default=(), min_length=0)
    reinforcements: tuple[Reinforcement, ...] = Field(default=(), min_length=0)
    planned_delta: tuple[Establishment, ...] = Field(default=(), min_length=0)


class ExecutionPlan(StrictModel):
    """全书执行计划（Derived；内容身份由 repository 记，模型不自持 hash）。"""

    contract_version: Literal['str06-rc12'] = 'str06-rc12'
    initial_establishments: tuple[Establishment, ...] = ()
    scenes: tuple[SceneExecutionPlan, ...] = Field(min_length=1)
    provenance: FreeProvenance | LlmProvenance | None = Field(
        default=None, alias='_provenance', exclude=True)

    @model_validator(mode='after')
    def _unique_scene_ids(self) -> 'ExecutionPlan':
        ids = [s.scene_id for s in self.scenes]
        if len(set(ids)) != len(ids):
            raise ValueError('ExecutionPlan 内 scene_id 必须唯一')
        return self


class ExecutionSlice(StrictModel):
    """场景消费身份：本场实际消费的规范化业务内容的摘要绑定。

    entry_state / entry_knowledge 的 SHA-256 绑定重放输入；`execution_slice_sha256`
    绑定（slice 内容 + 必要契约身份）的规范化序列化。计算函数 F4 提供；
    旧 PASS 配新 Context／slice 在此身份上必然不匹配——这是「场景有效性硬匹配」
    的唯一材料，全书 plan hash、审计时间戳不进本类型（见 receipt.py）。
    """

    scene_id: str = Field(min_length=1)
    consumed_obligation_ids: tuple[str, ...] = Field(min_length=0)
    entry_state_sha256: str = Field(min_length=64, max_length=64)
    entry_knowledge_sha256: str = Field(min_length=64, max_length=64)
    execution_slice_sha256: str = Field(min_length=64, max_length=64)
