#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Preserve/Brief/Bind RC2：Preserve 隔离于设计反馈，Brief 全语义去源坐标，
Binding 发布完整义务与单一验收位置。paid checkpoint 不是下游读取接口。
跨场复合义务通过较早支撑拍表达，不能把义务整体提前当成已成立事实。
"""
from __future__ import annotations

from pydantic import Field, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.common import FreeProvenance, LlmProvenance
from contracts.schema.policy import PreserveKind
from typing import Literal
from contracts.schema.expression import ExpressionRequirement, ExpressionReadings
from contracts.schema.hook import HookPov


class SourceLineage(StrictModel):
    """Obligation 的源主体引用：可综合多个源主体。

    计划原文：Obligation 是有 source lineage 的语义投影。蓝图拍、人物条目都可
    引用；两项都空 = 没有来源的断言，构造期即拒（lineage 为必填非空集合）。
    R10a a2 起 lineage 不再由 Preserve wire 输出——compiler 从义务 anchors
    （投影句柄）确定性派生（I-15/I-21），本模型保留为 canonical 侧的类型化结果。
    """

    blueprint_refs: tuple[str, ...] = Field(min_length=0)
    character_ids: tuple[str, ...] = Field(min_length=0)

    @model_validator(mode='after')
    def _not_empty(self) -> 'SourceLineage':
        if not (self.blueprint_refs or self.character_ids):
            raise ValueError('SourceLineage 必须至少引用一个源主体（blueprint 或 characters）')
        return self


#: 投影句柄的原子类别闭集（R10a a2）：拍内效应／认知／悬念、书级初态、世界约束。
ANCHOR_SOURCE_KINDS: tuple[str, ...] = (
    'effect', 'cognition', 'loop', 'initial_state', 'world_constraint')


class ObligationAnchor(StrictModel):
    """R10a a2：义务锚点——compiler 从投影句柄解析并**类型化拷贝**的源原子。

    Preserve 只给句柄（如 ``b06_21.e1``／``s0_01``／``wc_01``）；本模型的内容
    全部由 compiler 从 canonical 拷贝（持有者、mode、命题、效应目标/值/
    modality/可见性、逐字引文），不由模型复述（I-15/I-21）。a4 的知情/时态
    锁直接消费 holder／mode／proposition_ref／modality。
    """

    handle: str = Field(min_length=1)
    source_kind: str = Field(pattern=r'^(' + '|'.join(ANCHOR_SOURCE_KINDS) + r')$')
    #: 认知持有者（cognition.holder；其余 None）
    holder: str | None = None
    #: 认知模式（cognition.mode；其余 None）
    mode: str | None = None
    #: 命题正式 ID（effect.proposition.ref／cognition.proposition.ref；无则 None）
    proposition_ref: str | None = None
    proposition_statement: str | None = None
    #: 效应渲染地址（如 char:fu_jun.scene_location；初态/约束同形）
    address: str | None = None
    value: object = None
    #: 效应时态（effect.modality；其余 None）。a4 时态锁（kf）的输入。
    modality: str | None = None
    #: 效应可见性（effect.disclosure.visibility；其余 None）
    visibility: str | None = None
    #: 悬念问题或收束（loop.question／resolution）
    question: str | None = None
    #: 逐字引文（basis quotes 原样拷贝；段归属已在 Stage2 判定，不重验证）
    quotes: tuple[str, ...] = Field(default=(), min_length=0)


class SourceObligation(StrictModel):
    """一条保存义务（Preserve 的输出原子）。

    `must_preserve=True` 时 `satisfaction` 必填非空——「必保要求必须有实质满足
    条件」的构造期表达；可综合类（must_preserve=False）允许留空 satisfaction。
    `requires` 是义务间必要因果（F3 起存在；DAG 校验归 compile/Guard，schema
    只保无自指）。R10a a2：`anchors` 必填 ≥1——义务锚定其依赖的源原子（投影
    句柄），`lineage` 由 compiler 从 anchors 派生（wire 不再输出 lineage）。
    """

    obligation_id: str = Field(min_length=1)
    kind: PreserveKind
    statement: str = Field(min_length=1)
    lineage: SourceLineage
    anchors: tuple[ObligationAnchor, ...] = Field(min_length=1)
    must_preserve: bool
    satisfaction: str = ''
    requires: tuple[str, ...] = Field(default=(), min_length=0)

    @model_validator(mode='after')
    def _must_preserve_needs_satisfaction(self) -> 'SourceObligation':
        if self.must_preserve and not self.satisfaction.strip():
            raise ValueError(
                f'必保义务 {self.obligation_id!r} 必须给出实质满足条件（satisfaction）')
        if self.obligation_id in self.requires:
            raise ValueError(f'义务 {self.obligation_id!r} 的 requires 不得包含自身')
        return self


class ObligationSeries(StrictModel):
    """R10a a2：同一机制的多次发生（离散兑现时点分别登记、义务各自独立）。

    members 是正式 obligation_id（compiler 从 wire 的 1-based 序号派发后重写）；
    mechanism 属源表达（blueprint.source_expression.items 的 ex_id）或 None——
    compiler 只查闭集与互斥，不判断「是否同一机制」（语义归 Preserve）。
    """

    members: tuple[str, ...] = Field(min_length=2)
    mechanism: str | None = None

    @model_validator(mode='after')
    def _members_unique(self) -> 'ObligationSeries':
        if len(set(self.members)) != len(self.members):
            raise ValueError(f'series 成员不得重复：{self.members}')
        return self


class PreserveCheckpoint(StrictModel):
    """已购 Preserve 结果的身份与内容（paid checkpoint）。

    经过校验、hash-bound、可恢复；**不是下游读取接口**——下游消费
    ReconstructionBrief / SemanticBinding。执行身份记录 provider/model/请求
    hash，供「同请求身份下历史采用」与成本对账（I-37/I-43）。
    R10a a2：义务带类型化 anchors；顶层增 series（同一机制的多次发生）。
    """

    provenance: FreeProvenance | LlmProvenance | None = Field(
        default=None, alias='_provenance', exclude=True)
    checkpoint_id: str = Field(min_length=1)
    policy_id: str = Field(min_length=1)
    policy_version: int = Field(ge=1)
    characters_sha256: str = Field(min_length=64, max_length=64)
    blueprints_sha256: str = Field(min_length=64, max_length=64)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    request_sha256: str = Field(min_length=64, max_length=64)
    obligations: tuple[SourceObligation, ...] = Field(min_length=1)
    series: tuple[ObligationSeries, ...] = Field(default=(), min_length=0)
    checkpoint_sha256: str = Field(min_length=64, max_length=64)

    @model_validator(mode='after')
    def _unique_obligation_ids(self) -> 'PreserveCheckpoint':
        ids = [o.obligation_id for o in self.obligations]
        if len(set(ids)) != len(ids):
            raise ValueError('obligation_id 在 checkpoint 内必须唯一（稳定 ID，I-31）')
        known = set(ids)
        seen: set[str] = set()
        for group in self.series:
            outside = [m for m in group.members if m not in known]
            if outside:
                raise ValueError(f'series 成员不在义务闭集内：{outside}')
            overlap = sorted(set(group.members) & seen)
            if overlap:
                raise ValueError(f'同一义务进了多个 series（互斥）：{overlap}')
            seen.update(group.members)
        return self


class IdentityProjection(StrictModel):
    """Design 的输入之一：稳定 char_id → 身份声明。

    来源 Characters（公共投影）；只给身份与稳定约束，不给源坐标。Design 由此
    在 source-blind 前提下仍能引用「同一个人」。
    """

    char_id: str = Field(min_length=1)
    identity_statement: str = Field(min_length=1)
    stable_constraints: tuple[str, ...] = Field(default=(), min_length=0)


class BriefRequirement(StrictModel):
    """Brief 中的保存要求：所有类别与满足条件，无源章坐标。"""

    core_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    kind: PreserveKind
    must_preserve: bool
    satisfaction: str
    participants: tuple[str, ...] = Field(default=(), min_length=0)
    # 必要因果的显式边（RC：起止都是 core_id；DAG 校验归 F4 Guard）
    requires_cores: tuple[str, ...] = Field(default=(), min_length=0)


class ReconstructionBrief(StrictModel):
    """确定性发布的 Derived Brief——Design 的唯一语义输入（与 IdentityProjection、
    OutputConstraints 并列）。

    ⚠ 本模型刻意**没有**以下字段，且 `extra='forbid'` 会拒绝任何试图混入的同义
    键：源章号、源拍 id、源叙述顺序、evidence 定位、binding refs。屏蔽源叙述
    拓扑是 Preserve→Design 的冻结边界，字段缺失就是它的类型层表达。
    """

    contract_version: Literal['str06-rc11'] = 'str06-rc11'
    brief_id: str = Field(min_length=1)
    preserve_checkpoint_sha256: str = Field(min_length=64, max_length=64)
    requirements: tuple[BriefRequirement, ...] = Field(min_length=1)
    book_constraints: tuple[str, ...] = Field(default=(), min_length=0)
    source_narration: HookPov
    expression: tuple[ExpressionRequirement, ...] = Field(min_length=1)
    expression_readings: ExpressionReadings
    provenance: FreeProvenance | LlmProvenance | None = Field(
        default=None, alias='_provenance', exclude=True)

    @model_validator(mode='after')
    def _unique_core_ids(self) -> 'ReconstructionBrief':
        ids = [c.core_id for c in self.requirements]
        if len(set(ids)) != len(ids):
            raise ValueError('core_id 在 Brief 内必须唯一')
        if any(set(c.requires_cores) - set(ids) for c in self.requirements):
            raise ValueError('Brief 依赖必须闭合，非事件义务也必须投影')
        return self


class OutputConstraints(StrictModel):
    """Design 的输出边界约束：目标形状的机械可验证部分。

    章数 N 与篇幅边界来自书级配置（不来自源章对照——「保留全书篇幅边界，不对照
    同号源章长度」）；目标预算按 Design 的章/场权重分配（Design 自己的权重字段）。
    """

    target_chapter_count: int = Field(ge=1)
    total_length_budget: int = Field(ge=1, description='全书正文总字数预算上限')


class BindingEntry(StrictModel):
    """一条义务到目标位置的承载声明（Bind 输出原子）。"""

    obligation_id: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    beat_ref: str = Field(min_length=1)
    supporting_beat_refs: tuple[str, ...] = ()

    @model_validator(mode='after')
    def _beat_ref_scene_consistent(self) -> 'BindingEntry':
        # beat_ref 若给出，必须指 scene 内的拍（`<scene_id>#<beat_id>`）。
        if not self.beat_ref.startswith(f'{self.scene_id}#'):
            raise ValueError(f'beat_ref {self.beat_ref!r} 必须落在所属场景 {self.scene_id!r} 内')
        if len(set(self.supporting_beat_refs)) != len(self.supporting_beat_refs):
            raise ValueError('支撑拍不得重复')
        return self


class UnboundEntry(StrictModel):
    """未承载的义务：保留原义务并返回正确 owner——Bind 不得删义务迁就 Design。"""

    obligation_id: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class SemanticBinding(StrictModel):
    """完整 Canonical snapshot（一次发布，无半份）。

    自洽校验：entries 与 unbound 的 obligation_id 并集内无重复（每条义务至多出现
    一次）；跨产物「全分母 = 采用的 checkpoint obligations」归 F4 机械 Guard。
    """

    contract_version: Literal['str06-rc3'] = 'str06-rc3'
    binding_id: str = Field(min_length=1)
    preserve_checkpoint_sha256: str = Field(min_length=64, max_length=64)
    obligations: tuple[SourceObligation, ...] = Field(min_length=1)
    entries: tuple[BindingEntry, ...] = Field(min_length=0)
    unbound: tuple[UnboundEntry, ...] = Field(min_length=0)
    provenance: FreeProvenance | LlmProvenance | None = Field(
        default=None, alias='_provenance', exclude=True)

    @model_validator(mode='after')
    def _no_double_bookkeeping(self) -> 'SemanticBinding':
        ids = [o.obligation_id for o in self.obligations]
        if len(ids) != len(set(ids)):
            raise ValueError('Binding 完整义务内 ID 不得重复')
        bound = [e.obligation_id for e in self.entries]
        returned = [u.obligation_id for u in self.unbound]
        if len(set(bound)) != len(bound):
            raise ValueError(f'同一义务在 entries 内重复承载：{sorted(bound)}')
        if len(set(returned)) != len(returned):
            raise ValueError(f'同一义务在 unbound 内重复登记：{sorted(returned)}')
        overlap = set(bound) & set(returned)
        if overlap:
            raise ValueError(f'义务同时出现在 entries 与 unbound：{sorted(overlap)}')
        return self
