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

【R2（rc10，2026-09-28）：拍级读取解析值挂拍＋线程投影面＋WriterPacket】
AUD-03：拍级读取（`StateRead.at` 指向场内拍）此前只做闭合证明、解析值不进
IR——本版起 `BeatIR.reads` 携带该拍定位点上已解析的读取值（先写后读），
以 must_respect 呈现「本场依赖的事实」（Guard M7 的判据面）。
`VerificationIR.thread_ops`／`open_threads` 是线程投影的数据面（M8 判据；
三种呈现身份——must_realize／continuity／guard_only——由 renderer 按 E-4
分支分节，IR 只携带事实）。`WriterPacket` 是写手层章包的唯一 canonical
（I-11）：按 delivery_mode 分节、全部自然语言条目、无内部 ID 与字段名。
"""
from __future__ import annotations

from pydantic import Field, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.design import TargetBeat, TargetDelta, ThreadKind
from contracts.schema.execution import Establishment
from contracts.schema.state import StateAddress, SurfaceRelease
from contracts.schema.grounding import SourceObligation
from typing import Literal, Mapping, Sequence

__all__ = ['BeatIR', 'KnowledgeEntry', 'OpenThreadIR', 'StateEntry',
           'SurfaceRelease', 'ThreadOpIR', 'VerificationIR', 'WriterBrief',
           'WriterContext', 'WriterPacket']


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
    #: 拍级读取的解析值（R2，AUD-03）：定位到本拍的已声明读取在该拍定位点上
    #: 解析到的事实（先写后读），StateEntry＝非知情域、KnowledgeEntry＝知情域。
    #: 入场读取的值不在这里（在 entry_state／entry_knowledge——「起笔已成立」）。
    reads: tuple[StateEntry | KnowledgeEntry, ...] = Field(
        default=(), min_length=0,
        description='挂到本拍的已解析读取值（Guard M7 的判据面）')

    @model_validator(mode='after')
    def _ref_matches_owner(self) -> 'BeatIR':
        owner = self.beat_ref.split('#', 1)[0]
        # 场景归属由 WriterContext 构造方核对；这里只锁引用形状带场景前缀。
        if '#' not in self.beat_ref or not owner:
            raise ValueError(f'beat_ref 必须是 场景#拍 形式：{self.beat_ref!r}')
        return self


class PendingConfirmation(StrictModel):
    """尚未向读者确认的事实（D15 派生；B3 前半起按地址投影，D4）。

    `fact` 是 reader delta 的 value（命题内容）；`address` 是它确认的事实
    地址（reader delta 的 `confirms`，D4 必填的机械面）；`position` 是确认
    位置（`场景#拍`）；`in_scene`＝确认拍是否就在本场（本场内的标注「自第
    X 拍起可确认」，后场的写手在此之前不得让读者确认）。投影只进入实际
    消费该地址的章包（读取或写入该地址的场景）；确认前不作为已确认信息。
    """

    fact: str = Field(min_length=1)
    key: str = Field(min_length=1)
    subjects: tuple[str, ...] = ()
    address: StateAddress
    position: str = Field(min_length=1)
    in_scene: bool


class ThreadOpIR(StrictModel):
    """本场线程操作投影（R2.4 三种呈现身份中 must_realize 的数据面）。

    `kind`／`subject`／`text` 是线程声明内容的**投影副本**（来自 open，
    文本只写一次）——渲染「这一场推进或收束它」需要声明上下文；载荷按
    op 落位（advance 带 progress；close 带 ending＋resolution；open 全带）。
    """

    ref: str = Field(min_length=1, description='compiler 派发的线程 id（th{nn}）')
    op: Literal['open', 'advance', 'close']
    kind: ThreadKind
    subject: str = Field(min_length=1)
    text: str = Field(min_length=1, description='声明文本（open 处只写一次的那份）')
    progress: str | None = Field(default=None, min_length=1)
    ending: str | None = Field(default=None, min_length=1)
    resolution: str | None = Field(default=None, min_length=1)


class OpenThreadIR(StrictModel):
    """入场时开放、与参与者相关、本场无操作的线程（continuity／guard_only 的
    数据面，R2.4）。

    呈现身份由 renderer 按 E-4 分支决定：kind=question → guard_only（不得
    提前回答，也不主动提醒读者）；其余 → continuity（仍影响人物选择，不必
    点明）。不相关的开放线程不投影（全场景保护不等于每场提醒）。
    """

    ref: str = Field(min_length=1)
    kind: ThreadKind
    subject: str = Field(min_length=1)
    text: str = Field(min_length=1)
    opened_at: str = Field(min_length=1, description='开启位置（场景#拍）')


class VerificationIR(StrictModel):
    """事实约束视图：写前机械 Guard 的验证输入与写手的硬边界清单。"""

    scene_id: str = Field(min_length=1)
    obligations: tuple[SourceObligation, ...] = Field(default=(), min_length=0, description='本场完整义务及满足条件')
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
    #: 本场拍上的线程操作（R2.4，must_realize 数据面；拍阅读序）。
    thread_ops: tuple[ThreadOpIR, ...] = Field(default=(), min_length=0)
    #: 入场时开放、与参与者相关、本场无操作的线程（R2.4，continuity／
    #: guard_only 数据面；主体在场过滤后的投影）。
    open_threads: tuple[OpenThreadIR, ...] = Field(default=(), min_length=0)

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

    rc7（B3 前半）：PendingConfirmation 带事实地址（D4），投影按地址精确到
    消费该地址的章包。rc10（R2）：拍级读取值挂 BeatIR.reads；线程投影面
    thread_ops／open_threads 进 IR。"""

    contract_version: Literal['str06-rc10'] = 'str06-rc10'
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


class WriterPacket(StrictModel):
    """写手层章包（R2 两层 IR 的第二层；I-11 唯一 canonical 模型）。

    Machine ChapterIR 保持完整（ID、地址、来源、依赖边、Guard 材料——审计
    与 Stage7 核对用）；WriterPacket 是「编辑写的章纲」：按 `delivery_mode`
    分节（audit_only 永不出现——那是验证器与审计的材料）、全部自然语言
    条目、不带内部 ID 与字段名。由确定性 renderer 从 Machine IR＋送达登记
    （`contracts/schema/consumption.py::STR06_FIELD_DELIVERY`）生成，从不
    落盘（章包是派生视图，I-16）；其字节进章回执的写手视图摘要。

    六个分区全部必填（空分区合法——没有该身份的内容）：缺节即拒，不用
    默认空值冒充「已经按该身份考虑过」（I-14/I-42）。
    """

    contract_version: Literal['str06-rc10'] = 'str06-rc10'
    chapter_ordinal: int = Field(ge=1)
    #: 必须写出：本章拍的事件与目标效应；本场义务的满足条件；本场的线程操作。
    must_realize: tuple[str, ...]
    #: 不得违背、不必提及：本场依赖的事实（入场与拍级读取）；知情边界；
    #: 称谓许可；世界规则；场景 avoid。
    must_respect: tuple[str, ...]
    #: 自然延续、不复述：承接（上一章末场＋本章 entry）；与参与者相关、
    #: 本场没有操作的开放线程。
    continuity: tuple[str, ...]
    #: 创作方向：导演说明、场景驱动、书级风格、人物声线、拍级表达提示、章目的。
    creative_guidance: tuple[str, ...]
    #: 需要时才用：编译器按参与者补齐的入场状态（背景是约束，不是写作任务）。
    background: tuple[str, ...]
    #: 禁区：不提前兑现、不主动提示——未到时点的读者确认；开放期间受保护的
    #: 问题线程（写手看到的是约束表述，不是内容提醒）。
    guard_only: tuple[str, ...]

    @model_validator(mode='after')
    def _no_internal_ids_or_field_names(self) -> 'WriterPacket':
        """无内部 ID 与字段名（R0 检查 4 的 schema 面）。

        条目是给写手读的自然语言：`sc01#b02` 式复合引用、`th{nn}` 线程 id、
        `established_by`／`execution_slice_sha256` 等工程字段名出现在任何
        分区里都说明 renderer 把 Machine 材料漏给了写手——构造期即拒。
        """
        import re
        forbidden = (
            re.compile(r'sc\d{2,}#b\d{2,}'),
            re.compile(r'th\d{2,}'),
            re.compile(r'established_by'),
            re.compile(r'execution_slice_sha256'),
        )
        for section in ('must_realize', 'must_respect', 'continuity',
                        'creative_guidance', 'background', 'guard_only'):
            for entry in getattr(self, section):
                for pattern in forbidden:
                    if pattern.search(entry):
                        raise ValueError(
                            f'{section} 的条目携带内部 ID／字段名（{entry!r}；'
                            'WriterPacket 只装自然语言，Machine 材料不进写手面）')
        return self


def beats_ir(scene_id: str, beats: tuple[TargetBeat, ...],
             reads_by_beat: Mapping[str, Sequence[StateEntry | KnowledgeEntry]] | None = None,
             ) -> tuple[BeatIR, ...]:
    """canonical 拍 → BeatIR 的确定性投影（供 compiler 复用，单一产地）。

    `reads_by_beat`（可选）把拍级读取的解析值挂到对应拍（R2，AUD-03）：
    键是场内 beat_id，值是该拍定位点上解析到的条目；缺省不挂（无拍级读取）。
    """
    reads_by_beat = dict(reads_by_beat or {})
    return tuple(
        BeatIR(beat_ref=f'{scene_id}#{b.beat_id}', event=b.event,
               dramatic_intent=b.dramatic_intent, deltas=b.target_delta,
               surface_introductions=tuple(
                   s for intro in b.surface_introductions for s in intro.surfaces),
               reads=tuple(reads_by_beat.get(b.beat_id, ())))
        for b in beats)
