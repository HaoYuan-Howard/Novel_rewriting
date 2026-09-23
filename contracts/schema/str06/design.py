#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""design —— PresentationGraph v1（Target Design，source-blind）（F0 RC）。

【source-blind 的类型层表达】
Target Design 完全盲于 Source binding：本模块所有模型**没有** source_ref /
source_beat_ref / evidence_ref / source_intent_ref / source chapter 字段，且
`extra='forbid'` 拒绝任何同义混入。试点 v2 的 `target_beat.source_refs` 形状已被
终态裁决退役——按旧形状迁移会引发第二轮 shape break（见 DECISIONS 2026-09-23）。

【结构对应终态裁决】
TargetScene = SceneDriver{focal_actor, immediate_want, opposition, turn} +
OrderedTargetBeats + Delivery；TargetBeat = Event + Participants + DramaticIntent +
TargetDelta + DeliveryHint。归章（ChapterAssignment）由 Design 决定；Compiler 只
验证章数、归属、顺序和完整性——其中「自洽」的部分（章号连续 1..N、场景全覆盖、
每章非空）在构造期就拒，语义合理性仍归上游判断。
"""
from __future__ import annotations

from pydantic import Field, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.str06.state import StateAddress, StateChange, SurfaceIntroduction
from typing import Literal
import math


class SceneDriver(StrictModel):
    """场景的戏剧驱动：Design 对「这一场为什么存在」的显式承担。"""

    focal_actor: str = Field(min_length=1)
    immediate_want: str = Field(min_length=1)
    opposition: str = Field(min_length=1)
    turn: str = Field(min_length=1)


class TargetDelta(StateChange):
    """typed effect：目标拍的语义效果声明。

    domain 闭集保留「人物／关系、人物知情、世界事实、读者揭示」的必要区别，
    不迁旧 PlotState ontology。value 必须 mechanically applicable——表达成键值
    （enum/受控词表），不是自由散文；具体取值面 F1 反例标定后收紧。
    """

    #: 语义等价归上游判断；Compiler 不解释自然语言补猜事实——所以这里没有
    #: 「等价于源 XX」的备注字段，要写等价判断就升到 Design 上游的语义层。


class TargetBeat(StrictModel):
    beat_id: str = Field(min_length=1)
    event: str = Field(min_length=1, description='事件内核（target 语言）')
    participants: tuple[str, ...] = Field(min_length=0)
    dramatic_intent: str = Field(min_length=1)
    target_delta: tuple[TargetDelta, ...] = Field(default=(), min_length=0)
    delivery_hint: str = Field(default='', min_length=0)
    surface_introductions: tuple[SurfaceIntroduction, ...] = ()


class SceneDelivery(StrictModel):
    """场景叙述交付：pov 与口吻归 Target Design（试点以 Stage5 静态规则过渡的
    教训——口吻硬编码写手层会丢书与书之间的差异）。"""

    pov: str = Field(min_length=1, description='视角 char_id 或 narrator')
    voice: str = Field(min_length=1, description='口吻/register 声明')
    avoid: tuple[str, ...] = Field(default=(), min_length=0)


class TargetScene(StrictModel):
    scene_id: str = Field(min_length=1)
    driver: SceneDriver
    beats: tuple[TargetBeat, ...] = Field(min_length=1)
    delivery: SceneDelivery
    state_reads: tuple[StateAddress, ...]

    @model_validator(mode='after')
    def _unique_beat_ids(self) -> 'TargetScene':
        ids = [b.beat_id for b in self.beats]
        if len(set(ids)) != len(ids):
            raise ValueError(f'scene {self.scene_id!r} 内 beat_id 必须唯一')
        return self


class ChapterAssignment(StrictModel):
    """Scene 归章：输出保持 N 个非空目标章、连续章号 1..N。"""

    chapter_ordinal: int = Field(ge=1)
    scene_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def _no_duplicate_scenes(self) -> 'ChapterAssignment':
        if len(set(self.scene_ids)) != len(self.scene_ids):
            raise ValueError(f'章 {self.chapter_ordinal} 内 scene_ids 不得重复')
        return self


class PresentationGraph(StrictModel):
    """完整 Target Design canonical artifact（目标 v1，RC）。"""

    contract_version: Literal['str06-rc2'] = 'str06-rc2'
    graph_id: str = Field(min_length=1)
    initial_state: tuple[TargetDelta, ...]
    initial_surfaces: tuple[SurfaceIntroduction, ...] = ()
    scenes: tuple[TargetScene, ...] = Field(min_length=1)
    chapters: tuple[ChapterAssignment, ...] = Field(min_length=1)
    #: 章／场权重：目标预算分配的依据（不对照同号源章长度）
    chapter_weights: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode='after')
    def _chapters_consistent(self) -> 'PresentationGraph':
        ordinals = [c.chapter_ordinal for c in self.chapters]
        expected = list(range(1, len(self.chapters) + 1))
        if sorted(ordinals) != expected:
            raise ValueError(f'章号必须从 1 连续编号，实得 {sorted(ordinals)}')
        scene_ids = {s.scene_id for s in self.scenes}
        if len(scene_ids) != len(self.scenes):
            raise ValueError('scene_id 全书唯一')
        if any('#' in b.beat_id for s in self.scenes for b in s.beats):
            raise ValueError('canonical beat_id 必须为场内短 ID；完整引用由 compiler 组成')
        if any(d.scope != 'book' for d in self.initial_state):
            raise ValueError('目标初始状态必须为 book 作用域')
        assigned = [sid for c in self.chapters for sid in c.scene_ids]
        if len(set(assigned)) != len(assigned):
            raise ValueError('同一 scene 不得归入多章')
        missing = scene_ids - set(assigned)
        if missing:
            raise ValueError(f'场景未归章：{sorted(missing)}')
        unknown = set(assigned) - scene_ids
        if unknown:
            raise ValueError(f'归章引用了不存在的场景：{sorted(unknown)}')
        unknown_w = set(self.chapter_weights) - scene_ids
        if unknown_w:
            raise ValueError(f'chapter_weights 引用了不存在的场景：{sorted(unknown_w)}')
        if self.chapter_weights and (
                set(self.chapter_weights) != scene_ids
                or any(not math.isfinite(w) or w <= 0 for w in self.chapter_weights.values())
                or not math.isclose(sum(self.chapter_weights.values()), 1.0, abs_tol=1e-6)):
            raise ValueError('场权重必须完整、有限、为正且归一到 1')
        ordered = [sid for c in sorted(self.chapters, key=lambda c: c.chapter_ordinal)
                   for sid in c.scene_ids]
        if ordered != [s.scene_id for s in self.scenes]:
            raise ValueError('scenes 必须按目标章内阅读序排列')
        return self
