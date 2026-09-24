#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""context —— WriterContext 双视图（F0 RC）。

【单份 Derived artifact】
VerificationIR（Guard/Judge 用）与 WriterBrief（Writer 主消费）是**同一份产物里的
两个视图**，从同一执行输入确定性投影——不是两份文件、不是两次生成。试点教训：
Judge 与 Writer 各拿各的输入会产生「跨场回指误报」（judge 缺前场义务），根因修复
是把两视图钉在同一份 context 上。

【同源锚】
两视图共享 `scene_id` + `execution_slice_sha256`：构造期校验一致。Guard 只机械
验证 IR；Judge 验证正文对 IR 义务的实现；Writer 只读 brief——**Detection ≠
Ownership**，视图只承载各自的消费面，不承载修改权。
"""
from __future__ import annotations

from pydantic import Field, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.str06.design import TargetDelta
from contracts.schema.str06.execution import Establishment
from contracts.schema.str06.state import StateAddress, SurfaceRelease
from contracts.schema.str06.grounding import SourceObligation
from typing import Literal

__all__ = ['KnowledgeEntry', 'StateEntry', 'SurfaceRelease', 'VerificationIR',
           'WriterBrief', 'WriterContext']


class StateEntry(StateAddress):
    """IR 的 entry_state 原子：established_by 溯源（M2/M3 机械 Guard 的查询面）。"""

    value: str | None
    status: Literal['set', 'unset', 'unknown'] = 'set'
    established_by: str = Field(min_length=1, description='建立位置锚（场景/拍级）')


class KnowledgeEntry(StrictModel):
    """IR 的 entry_knowledge 原子：holder + 命题 + 溯源。"""

    holder: str = Field(min_length=1)
    proposition: str = Field(min_length=1)
    established_by: str = Field(min_length=1)
    subjects: tuple[str, ...] = ()
    key: str = Field(min_length=1)
    status: Literal['set', 'unset', 'unknown'] = 'set'
    mode: Literal['known', 'suspected', 'heard']


class VerificationIR(StrictModel):
    """机械可查视图：Guard（写前）与 Judge（写后）的验证输入。"""

    scene_id: str = Field(min_length=1)
    obligations: tuple[SourceObligation, ...] = Field(min_length=0, description='本场完整义务及满足条件')
    entry_state: tuple[StateEntry, ...] = Field(default=(), min_length=0)
    entry_knowledge: tuple[KnowledgeEntry, ...] = Field(default=(), min_length=0)
    released_surfaces: tuple[SurfaceRelease, ...] = Field(default=(), min_length=0)
    planned_delta: tuple[TargetDelta, ...] = Field(default=(), min_length=0)
    continuity_fact_view: tuple[str, ...] = Field(
        default=(), min_length=0,
        description='前场已接受义务的事实视图（试点 judge 跨场回指误报的根因修复输入）')


class WriterBrief(StrictModel):
    """写作视图：Writer 的主消费面。不含 entry 溯源、不含义务 id 记账——那是 IR 的。"""

    scene_id: str = Field(min_length=1)
    task: str = Field(min_length=1)
    scene_driver_summary: str = Field(min_length=1)
    beat_outlines: tuple[str, ...] = Field(min_length=1)
    constraints: tuple[str, ...] = Field(default=(), min_length=0)
    length_budget: int = Field(ge=1)
    voice: str = Field(min_length=1)
    pov: str = Field(min_length=1)
    avoid: tuple[str, ...] = ()
    delivery_hints: tuple[str, ...] = ()
    required_content: tuple[str, ...] = ()
    entry_facts: tuple[str, ...] = ()
    continuity: tuple[str, ...] = ()
    usable_surfaces: tuple[SurfaceRelease, ...] = ()


class WriterContext(StrictModel):
    """双视图载体：单份 Derived artifact（`stage4/context/scene_{nnn}.json`）。"""

    contract_version: Literal['str06-rc3'] = 'str06-rc3'
    scene_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    execution_slice_sha256: str = Field(min_length=64, max_length=64)
    verification_ir: VerificationIR
    writer_brief: WriterBrief

    @model_validator(mode='after')
    def _views_agree(self) -> 'WriterContext':
        if self.verification_ir.scene_id != self.scene_id:
            raise ValueError('VerificationIR 与 WriterContext 的 scene_id 必须一致（双视图同源）')
        if self.writer_brief.scene_id != self.scene_id:
            raise ValueError('WriterBrief 与 WriterContext 的 scene_id 必须一致（双视图同源）')
        return self
