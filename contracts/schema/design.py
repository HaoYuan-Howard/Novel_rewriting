#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""design —— BodyDesign／PresentationGraph（Target Design，source-blind）（F5 RC4）。

【source-blind 的类型层表达】
Target Design 完全盲于 Source binding：本模块所有模型**没有** source_ref /
source_beat_ref / evidence_ref / source_intent_ref / source chapter 字段，且
`extra='forbid'` 拒绝任何同义混入。试点 v2 的 `target_beat.source_refs` 形状已被
终态裁决退役——按旧形状迁移会引发第二轮 shape break（见 DECISIONS 2026-09-23）。

【结构对应终态裁决】
TargetScene = SceneDriver{focal_actor, immediate_want, opposition, turn} +
OrderedTargetBeats + Delivery + RealizationBrief；TargetBeat = Event +
Participants + DramaticIntent + TargetDelta + DeliveryHint。归章
（ChapterAssignment.purpose）由 Design 决定；Compiler 只验证章数、归属、顺序和
完整性——其中「自洽」的部分（章号连续 1..N、场景全覆盖、每章非空）在构造期就拒，
语义合理性仍归上游判断。

【导演说明与章节目的（F5 RC4）】
`TargetScene.realization_brief`（本场怎样演出来：重心、理解推进、展开与压缩、
转折显现、收束点、邻场衔接）与 `ChapterAssignment.purpose`（本章承担的核心变化，
不单独增加事件或结果）是 Design 的必填、非空语义输出。两者只承载呈现方向；
brief 中涉及关键行动、持久认知变化、秘密、承诺或因果条件的内容必须同时落在
正式故事字段（driver/event/target_delta/surface_introductions）——compiler 只查
形状与引用闭合，不用关键词规则声称发现自然语言冲突。旧 RC3 Design 缺这两项，
明确拒读：不用空字符串兜底，也不由 compiler 补写（F5e 对现有样书重跑 Design）。

【Hook 与正文同一份 graph（F6）】
`BodyDesign` 承载正文选择；`PresentationGraph(BodyDesign)` 增加必填 `hook`。
顺序是 Body Design → Hook Design → publish_graph 发布完整 graph：不让两个
producer 轮流改 canonical，不用空 Hook 默认值冒充完成。Bind／Compile／Context
的类型收窄到 `BodyDesign`，从类型上保证 Hook 不影响正文。
"""
from __future__ import annotations

from pydantic import Field, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.common import FreeProvenance, LlmProvenance
from contracts.schema.hook import HookDesign
from contracts.schema.state import StateAddress, StateChange, SurfaceIntroduction
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
    #: 本场怎样演出来（导演说明）：场景重心、理解推进、展开与压缩、关键转折
    #: 及收束、邻场衔接；不规定逐句正文。必填非空——缺省即拒读（RC4）。
    realization_brief: str = Field(min_length=1)

    @model_validator(mode='after')
    def _unique_beat_ids(self) -> 'TargetScene':
        ids = [b.beat_id for b in self.beats]
        if len(set(ids)) != len(ids):
            raise ValueError(f'scene {self.scene_id!r} 内 beat_id 必须唯一')
        return self


class ChapterAssignment(StrictModel):
    """Scene 归章：输出保持 N 个非空目标章、连续章号 1..N。

    `purpose` 是本章承担的核心变化（概括章内已设计的变化，不单独增加事件或
    结果）——为一次整章写作提供方向；compiler 不从场景事件猜这一层语义（RC4）。
    """

    chapter_ordinal: int = Field(ge=1)
    purpose: str = Field(min_length=1)
    scene_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def _no_duplicate_scenes(self) -> 'ChapterAssignment':
        if len(set(self.scene_ids)) != len(self.scene_ids):
            raise ValueError(f'章 {self.chapter_ordinal} 内 scene_ids 不得重复')
        return self


class BodyDesign(StrictModel):
    """正文选择（canonical body view，RC5：v13 质量改进批次二补 believes 知情模式）。

    Bind、Compile、Context 与 Guard 的类型都收窄到这里——Hook 不是正文
    写前链路的输入，也不增加义务分母。"""
    contract_version: Literal['str06-rc5'] = 'str06-rc5'
    graph_id: str = Field(min_length=1)
    initial_state: tuple[TargetDelta, ...]
    initial_surfaces: tuple[SurfaceIntroduction, ...] = ()
    scenes: tuple[TargetScene, ...] = Field(min_length=1)
    chapters: tuple[ChapterAssignment, ...] = Field(min_length=1)
    #: 章／场权重：目标预算分配的依据（不对照同号源章长度）
    chapter_weights: dict[str, float] = Field(default_factory=dict)

    def beat(self, scene_id: str, beat_id: str) -> 'TargetBeat | None':
        scene = next((s for s in self.scenes if s.scene_id == scene_id), None)
        if scene is None:
            return None
        return next((b for b in scene.beats if b.beat_id == beat_id), None)

    def declared_surfaces(self) -> dict[str, set[str]]:
        """Design 已声明的称谓面（char_id → surface 集合）：Hook 释放校验的基准。"""
        faces: dict[str, set[str]] = {}
        for intro in self.initial_surfaces:
            faces.setdefault(intro.char_id, set()).update(intro.surfaces)
        for scene in self.scenes:
            for beat in scene.beats:
                for intro in beat.surface_introductions:
                    faces.setdefault(intro.char_id, set()).update(intro.surfaces)
        return faces

    @model_validator(mode='after')
    def _chapters_consistent(self) -> 'BodyDesign':
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


class PresentationGraph(BodyDesign):
    """完整 Target Design canonical artifact（目标 v1，RC5：随 BodyDesign 升版）。

    `hook` 必填：没有「正文设计完成但 Hook 未定」的中间态 canonical——
    完整 graph 由唯一 publisher 发布（novel/stages/stage5/hook.py）。
    此处的跨引用校验只保结构闭合（引用的拍/人物在本 graph 内存在）；
    语义合法（withheld ⊆ 该拍 delta、时序、披露自洽）归 assemble_hook_design。
    """
    hook: HookDesign
    #: 来源账（F7a）：盘上键 `_provenance`；`exclude=True` 保 prompt 字节不变。
    provenance: FreeProvenance | LlmProvenance | None = Field(
        default=None, alias='_provenance', exclude=True)

    @model_validator(mode='after')
    def _hook_refs_close(self) -> 'PresentationGraph':
        beat_refs = {f'{s.scene_id}#{b.beat_id}' for s in self.scenes for b in s.beats}
        char_ids: set[str] = set()
        for scene in self.scenes:
            char_ids.add(scene.driver.focal_actor)
            if scene.delivery.pov != 'narrator':
                char_ids.add(scene.delivery.pov)
            for beat in scene.beats:
                char_ids.update(beat.participants)
                for delta in beat.target_delta:
                    char_ids.update(delta.subjects)
                    if delta.holder:
                        char_ids.add(delta.holder)
                for intro in beat.surface_introductions:
                    char_ids.add(intro.char_id)
        for intro in self.initial_surfaces:
            char_ids.add(intro.char_id)
        for delta in self.initial_state:
            char_ids.update(delta.subjects)
            if delta.holder:
                char_ids.add(delta.holder)
        hook = self.hook
        for focal in hook.pov.focalizers:
            if focal != 'narrator' and focal not in char_ids:
                raise ValueError(f'hook.pov 引用了 graph 外的人物：{focal}')
        for fact in hook.opening_facts:
            if fact.ref != 'init' and fact.ref not in beat_refs:
                raise ValueError(f'hook.opening_facts 引用了不存在的拍：{fact.ref}')
            for subject in fact.address.subjects:
                if subject not in char_ids:
                    raise ValueError(f'hook.opening_facts 引用了 graph 外人物：{subject}')
            if fact.address.holder and fact.address.holder not in char_ids:
                raise ValueError(f'hook.opening_facts 引用了 graph 外人物：{fact.address.holder}')
        for material in hook.materials:
            if material.beat_ref == 'init' or material.beat_ref not in beat_refs:
                raise ValueError(f'hook.materials 引用了不存在的拍：{material.beat_ref}')
        for item in hook.forbidden_reveals:
            if item.ref != 'init' and item.ref not in beat_refs:
                raise ValueError(f'hook.forbidden_reveals 引用了不存在的拍：{item.ref}')
        for char_id in hook.concealed:
            if char_id not in char_ids:
                raise ValueError(f'hook.concealed 引用了 graph 外的人物：{char_id}')
        return self
