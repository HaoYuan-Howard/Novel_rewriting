#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""grounding —— Preserve / Bind 两职的 RC 契约（F0）。

【链路位置】
SourceTruth → Preserve（本文件的 SourceObligation / PreserveCheckpoint）→ 确定性
ReconstructionBrief → Target Design → Bind（SemanticBinding）。

【三条冻结规则在这里的类型落点】
  ① Preserve 隔离：`PreserveCheckpoint` 上不存在任何 Design / Bind 字段
     （`extra='forbid'` 兜底）。它只锚 SourceTruth 与 Policy 身份。
  ② Design 不读 checkpoint 内部：`ReconstructionBrief` 只携带 checkpoint 的
     hash 身份（`preserve_checkpoint_sha256`），不携带 obligations 原文；Brief
     模型上没有源章 / 源拍 / 叙述顺序 / 证据定位字段——source-blind 由
     「没有这些字段 + forbid extra」双重保证，而不是靠 prompt 自觉。
  ③ Bind 完整发布：`SemanticBinding` 是完整 snapshot，没有 partial / draft
     字段；unbound 是显式列表（保留原义务 + 返回 owner），不是被删掉的沉默。
     「obligations 必须与采用的 Preserve 结果一致」是跨产物不变量，归 F4
     机械 Guard（本模型只保证自身自洽：entries 与 unbound 无交集、id 唯一）。
"""
from __future__ import annotations

from pydantic import Field, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.str06.policy import PreserveKind


class SourceLineage(StrictModel):
    """Obligation 的源主体引用：可综合多个源主体。

    计划原文：Obligation 是有 source lineage 的语义投影。蓝图拍、人物条目都可
    引用；两项都空 = 没有来源的断言，构造期即拒（lineage 为必填非空集合）。
    """

    blueprint_refs: tuple[str, ...] = Field(min_length=0)
    character_ids: tuple[str, ...] = Field(min_length=0)

    @model_validator(mode='after')
    def _not_empty(self) -> 'SourceLineage':
        if not (self.blueprint_refs or self.character_ids):
            raise ValueError('SourceLineage 必须至少引用一个源主体（blueprint 或 characters）')
        return self


class SourceObligation(StrictModel):
    """一条保存义务（Preserve 的输出原子）。

    `must_preserve=True` 时 `satisfaction` 必填非空——「必保要求必须有实质满足
    条件」的构造期表达；可综合类（must_preserve=False）允许留空 satisfaction，
    但 lineage 仍必填（没有来源就不是义务）。
    """

    obligation_id: str = Field(min_length=1)
    kind: PreserveKind
    statement: str = Field(min_length=1)
    lineage: SourceLineage
    must_preserve: bool
    satisfaction: str = ''

    @model_validator(mode='after')
    def _must_preserve_needs_satisfaction(self) -> 'SourceObligation':
        if self.must_preserve and not self.satisfaction.strip():
            raise ValueError(
                f'必保义务 {self.obligation_id!r} 必须给出实质满足条件（satisfaction）')
        return self


class PreserveCheckpoint(StrictModel):
    """已购 Preserve 结果的身份与内容（paid checkpoint）。

    经过校验、hash-bound、可恢复；**不是下游读取接口**——下游消费
    ReconstructionBrief / SemanticBinding。执行身份记录 provider/model/请求
    hash，供「同请求身份下历史采用」与成本对账（I-37/I-43）。
    """

    checkpoint_id: str = Field(min_length=1)
    policy_id: str = Field(min_length=1)
    policy_version: int = Field(ge=1)
    characters_sha256: str = Field(min_length=64, max_length=64)
    blueprints_sha256: str = Field(min_length=64, max_length=64)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    request_sha256: str = Field(min_length=64, max_length=64)
    obligations: tuple[SourceObligation, ...] = Field(min_length=1)
    checkpoint_sha256: str = Field(min_length=64, max_length=64)

    @model_validator(mode='after')
    def _unique_obligation_ids(self) -> 'PreserveCheckpoint':
        ids = [o.obligation_id for o in self.obligations]
        if len(set(ids)) != len(ids):
            raise ValueError('obligation_id 在 checkpoint 内必须唯一（稳定 ID，I-31）')
        return self


class IdentityProjection(StrictModel):
    """Design 的输入之一：稳定 char_id → 身份声明。

    来源 Characters（公共投影）；只给身份与稳定约束，不给源坐标。Design 由此
    在 source-blind 前提下仍能引用「同一个人」。
    """

    char_id: str = Field(min_length=1)
    identity_statement: str = Field(min_length=1)
    stable_constraints: tuple[str, ...] = Field(default=(), min_length=0)


class EventCoreNote(StrictModel):
    """Brief 中的事件内核：语义压缩后的「发生了什么」，无源章坐标。"""

    core_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
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

    brief_id: str = Field(min_length=1)
    preserve_checkpoint_sha256: str = Field(min_length=64, max_length=64)
    event_cores: tuple[EventCoreNote, ...] = Field(min_length=1)
    book_constraints: tuple[str, ...] = Field(default=(), min_length=0)

    @model_validator(mode='after')
    def _unique_core_ids(self) -> 'ReconstructionBrief':
        ids = [c.core_id for c in self.event_cores]
        if len(set(ids)) != len(ids):
            raise ValueError('core_id 在 Brief 内必须唯一')
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
    beat_ref: str | None = None

    @model_validator(mode='after')
    def _beat_ref_scene_consistent(self) -> 'BindingEntry':
        # beat_ref 若给出，必须指 scene 内的拍（`<scene_id>#<beat_id>`）。
        if self.beat_ref is not None and not self.beat_ref.startswith(f'{self.scene_id}#'):
            raise ValueError(f'beat_ref {self.beat_ref!r} 必须落在所属场景 {self.scene_id!r} 内')
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

    binding_id: str = Field(min_length=1)
    preserve_checkpoint_sha256: str = Field(min_length=64, max_length=64)
    entries: tuple[BindingEntry, ...] = Field(min_length=0)
    unbound: tuple[UnboundEntry, ...] = Field(min_length=0)

    @model_validator(mode='after')
    def _no_double_bookkeeping(self) -> 'SemanticBinding':
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
