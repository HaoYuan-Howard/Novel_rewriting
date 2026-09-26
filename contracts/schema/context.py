#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""context —— WriterContext 双视图（F5 RC4）。

【单份 Derived artifact 的两个视图】
VerificationIR（事实约束视图）与 WriterBrief（呈现视图）是**同一份产物里的
两个视图**，从同一 Design/Binding 输入确定性投影——不是两份文件、不是两次
生成。试点教训：Judge 与 Writer 各拿各的输入会产生「跨场回指误报」（judge 缺
前场义务），根因修复是把两视图钉在同一份 context 上。

【F5 的职责重划】
旧 WriterBrief 里重复的事件、义务、状态清单全部移回 VerificationIR——避免两
份内容分别解释同一语义。现在：

  • VerificationIR 回答「什么必须成立」：起笔状态、人物知情、本场行动与义务、
    满足条件、逐拍结果（BeatIR 的 event/delta 定位）、信息释放位置与明确禁止
    事项。它表示应满足的条件，**不表示正文已经通过语义验证**。
  • WriterBrief 回答「怎样呈现」：canonical `realization_brief` 原样投影，
    加上 driver 摘要、POV、voice 与拍级 delivery hints——不新增、不改写、
    不压缩任何语义。

【同源锚】
两视图共享 `scene_id` + `execution_slice_sha256`：构造期校验一致。attempt/
attempt 记账属于运行时（章级请求身份与回执），不进静态 IR（观测元数据不进
领域 schema，I-38）。
"""
from __future__ import annotations

from pydantic import Field, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.design import TargetBeat, TargetDelta
from contracts.schema.execution import Establishment
from contracts.schema.state import StateAddress, SurfaceRelease
from contracts.schema.grounding import SourceObligation
from typing import Literal

__all__ = ['BeatIR', 'KnowledgeEntry', 'StateEntry', 'SurfaceRelease', 'VerificationIR',
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
    #: v13（D14）补 believes：持有者以为为真，不代表世界为真。
    mode: Literal['known', 'suspected', 'heard', 'believes']


class BeatIR(StrictModel):
    """拍级事实定位：本场每一拍的行动内核、逐拍结果与释放位置。

    回答「哪一拍发生什么、哪些状态在该拍结束、哪些称谓从该拍起可用」——
    补齐旧投影只有扁平 delta 清单、缺事件与拍级变化定位的缺口。
    """

    beat_ref: str = Field(min_length=1, description='场景#场内拍 的完整引用')
    event: str = Field(min_length=1)
    dramatic_intent: str = Field(min_length=1)
    deltas: tuple[TargetDelta, ...] = Field(default=(), min_length=0)
    surface_introductions: tuple[str, ...] = Field(
        default=(), min_length=0,
        description='该拍首次释放的称谓字面（释放位置的可追溯投影）')

    @model_validator(mode='after')
    def _ref_matches_owner(self) -> 'BeatIR':
        owner = self.beat_ref.split('#', 1)[0]
        # 场景归属由 WriterContext 构造方核对；这里只锁引用形状带场景前缀。
        if '#' not in self.beat_ref or not owner:
            raise ValueError(f'beat_ref 必须是 场景#拍 形式：{self.beat_ref!r}')
        return self


class PendingConfirmation(StrictModel):
    """尚未向读者确认的事实（D15）：compiler 从 Design 的 reader delta 逐场派生。

    `fact` 是 reader delta 的 value（命题内容）；`position` 是确认位置
    （`场景#拍`）；`in_scene`＝确认拍是否就在本场（本场内的标注「自第 X 拍起
    可确认」，后场的写手在此之前不得让读者确认）。
    """

    fact: str = Field(min_length=1)
    key: str = Field(min_length=1)
    subjects: tuple[str, ...] = ()
    position: str = Field(min_length=1)
    in_scene: bool


class VerificationIR(StrictModel):
    """事实约束视图：写前机械 Guard 的验证输入与写手的硬边界清单。"""

    scene_id: str = Field(min_length=1)
    obligations: tuple[SourceObligation, ...] = Field(min_length=0, description='本场完整义务及满足条件')
    beats: tuple[BeatIR, ...] = Field(min_length=1, description='逐拍行动与结果定位')
    entry_state: tuple[StateEntry, ...] = Field(default=(), min_length=0)
    entry_knowledge: tuple[KnowledgeEntry, ...] = Field(default=(), min_length=0)
    released_surfaces: tuple[SurfaceRelease, ...] = Field(default=(), min_length=0)
    planned_delta: tuple[TargetDelta, ...] = Field(default=(), min_length=0)
    #: 尚未向读者确认的事实（D15）：本场开头及以后才发生的 reader 确认。
    pending_reader_confirmations: tuple[PendingConfirmation, ...] = Field(
        default=(), min_length=0)
    #: 明确禁止事项：只从已有显式限制（delivery.avoid）投影；不从未来剧情猜政策。
    prohibitions: tuple[str, ...] = Field(default=(), min_length=0)
    continuity_fact_view: tuple[str, ...] = Field(
        default=(), min_length=0,
        description='前场已接受义务的事实视图（试点 judge 跨场回指误报的根因修复输入）')

    @model_validator(mode='after')
    def _beats_belong_to_scene(self) -> 'VerificationIR':
        stray = [b.beat_ref for b in self.beats
                 if b.beat_ref.split('#', 1)[0] != self.scene_id]
        if stray:
            raise ValueError(f'BeatIR 挂在了别的场景上：{stray}')
        return self


class WriterBrief(StrictModel):
    """呈现视图：Writer 的主消费面。只承载「怎样演」——事实与义务在 IR。"""

    scene_id: str = Field(min_length=1)
    #: canonical `realization_brief` 的原样投影（重心／理解／展开压缩／转折／
    #: 收束／衔接）；renderer 与 writer 都不得改写或压缩。
    realization_brief: str = Field(min_length=1)
    scene_driver_summary: str = Field(min_length=1)
    pov: str = Field(min_length=1)
    voice: str = Field(min_length=1)
    delivery_hints: tuple[str, ...] = Field(default=(), min_length=0)
    usable_surfaces: tuple[SurfaceRelease, ...] = Field(default=(), min_length=0)


class WriterContext(StrictModel):
    """双视图载体（Derived；F5 起嵌入 ChapterIR，不再单独落盘成场景文件）。

    RC5（v13 质量改进批次二）：VerificationIR 带「尚未向读者确认的事实」清单。"""

    contract_version: Literal['str06-rc5'] = 'str06-rc5'
    scene_id: str = Field(min_length=1)
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


def beats_ir(scene_id: str, beats: tuple[TargetBeat, ...]) -> tuple[BeatIR, ...]:
    """canonical 拍 → BeatIR 的确定性投影（供 compiler 复用，单一产地）。"""
    return tuple(
        BeatIR(beat_ref=f'{scene_id}#{b.beat_id}', event=b.event,
               dramatic_intent=b.dramatic_intent, deltas=b.target_delta,
               surface_introductions=tuple(
                   s for intro in b.surface_introductions for s in intro.surfaces))
        for b in beats)
