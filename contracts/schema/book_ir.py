#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""book_ir —— WriterBookIR（全书静态写作 IR）与 ChapterWriterPacket（章包三件套）。

【Derived 定位与失效语义】
WriterBookIR 由 compiler 从（BodyDesign, ExecutionPlan, SemanticBinding, 篇幅
政策）确定性投影，唯一落盘 `stage4/ir/writer_book_ir.json`；不是第五类 truth。
完整 WriterIR 保留供存储与审计；**运行时按章确定性投影章包（D2），不另落
重复章包文件（I-16）**。正文请求身份只绑定 Writer 实际可见的章包：某章章包
变化只使该章回执失效，无关的未来剧情变化不再波及（精确失效表，
`docs/schema/str06.md`）。

【两层结构】
BookBrief 汇集全书约束与按阅读序的确定性导航（导航与称谓总表只作审计面，
不进章包）：不调用 LLM 总结、不取场景 POV 众数、不补造统一文风。ChapterIR
携带章目的、篇幅目标、本章场景的 WriterContext（双视图沿用 context.py 载体）
与章级验证材料（称谓许可、禁用字面——compile 时算好，D2）；场景上下文在
Stage4 一次成型，Stage5 不反向读取正文补写 IR。

【章包三件套（D2，B4）】
`ChapterWriterPacket`＝PacketBrief（整书风格、稳定声线与表达规则；不含导航、
其他章内容与称谓总表）＋ChapterIR（含验证材料）＋RealizationContract（写作
协议与交付要求）。Writer 与 Validator 只收当前章包：不读前文正文、Design、
Characters 或其他章 IR。章包是运行时确定性投影，从不落盘。rc8（2026-09-27
B4）：章 IR 增章级验证材料；新增章包模型。

【R2（rc10，2026-09-28）：continuity 分区数据面＋world_rules 进书级层】
`ChapterIR.continuity` 把本章 `entry`（Design 声明的承接）与上一章末场的
机械投影（驱动转折、末拍事件、收尾时所在的末场与在场人物）合成章级承接的
数据面；renderer 负责措辞成自然语言（「上一章收在……；本章以时间跳跃开场，
不复述上章结尾」）。Design 没有地点字段，故「收尾时所在」只投影末场锚
（不猜地点语义，I-21）。`world_rules`（目标侧世界边界）随书级层进每章。
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.common import FreeProvenance, LlmProvenance
from contracts.schema.context import WriterContext
from contracts.schema.design import ChapterEntry, CharacterVoice
from contracts.schema.state import SurfaceRelease

__all__ = ['BookBrief', 'ChapterContinuity', 'ChapterIR', 'ChapterNavEntry',
           'ChapterWriterPacket', 'PacketBrief', 'PreviousChapterEnding',
           'RealizationContract', 'ReleaseScheduleEntry', 'WriterBookIR']


class ChapterNavEntry(StrictModel):
    """BookBrief 的导航行：一章一行，只做确定性索引，不展开语义。"""

    chapter_ordinal: int = Field(ge=1)
    purpose: str = Field(min_length=1, description='canonical ChapterAssignment.purpose 原样投影')
    scene_ids: tuple[str, ...] = Field(min_length=1)
    scene_lines: tuple[str, ...] = Field(
        min_length=1, description='每场一行确定性摘要（与 scene_ids 等长、同序）')


class ReleaseScheduleEntry(StrictModel):
    """称谓释放导航：一个人物的称谓面从哪一章起可用（释放位置的投影）。"""

    char_id: str = Field(min_length=1)
    surfaces: tuple[str, ...] = Field(min_length=1)
    from_chapter: int = Field(ge=1)


from contracts.schema.hook import HookPov
from contracts.schema.expression import ExpressionDecision


class BookBrief(StrictModel):
    """全书约束与导航：Writer 阅读面的书级事实，全部为确定性投影。

    rc7（B3 前半）：整书风格与稳定人物声线从 TargetDesign 结构化字段投影
    （`BodyDesign.book_style`／`character_voices`）——不再只写在导演说明里。
    rc10（R2）：`world_rules`（目标侧世界边界，`BodyDesign.world_rules` 投影）
    以 must_respect 进每章——「作为世界边界，不要讲解」。"""

    book_title: str = Field(default='', min_length=0)
    #: 整书风格（canonical `BodyDesign.book_style` 原样投影）。
    book_style: str = Field(min_length=1)
    narration: HookPov
    expression_decisions: tuple[ExpressionDecision, ...] = Field(min_length=1)
    #: 书级世界边界（canonical `BodyDesign.world_rules` 原样投影，R2.5）。
    world_rules: tuple[str, ...] = Field(default=(), min_length=0)
    #: 稳定人物声线（canonical `BodyDesign.character_voices` 原样投影）。
    character_voices: tuple[CharacterVoice, ...] = Field(default=(), min_length=0)
    target_chapter_count: int = Field(ge=1)
    total_length_budget: int = Field(ge=1)
    #: 章篇幅带（相对本章预算的合法区间）；篇幅偏差是唯一可披露放行的软检查。
    length_band_low: float = Field(gt=0)
    length_band_high: float = Field(gt=0)
    chapters: tuple[ChapterNavEntry, ...] = Field(min_length=1)
    release_schedule: tuple[ReleaseScheduleEntry, ...] = Field(default=(), min_length=0)

    @model_validator(mode='after')
    def _band_ordered(self) -> 'BookBrief':
        if self.length_band_low >= self.length_band_high:
            raise ValueError('篇幅带下界必须小于上界')
        return self


class PreviousChapterEnding(StrictModel):
    """上一章末场的机械投影（R2.6 continuity 分区的数据面之一）。

    全部字段从 canonical 机械推导，不做语义推断（I-21）：末场锚（`ending_
    scene_id`）、该场驱动转折（`driver.turn` 原样）、末拍事件（原样）、末拍
    在场人物（participants 原序）。Design 无地点字段，「收尾时所在」只投影
    末场锚——地点语义若要进契约须先扩 Design，不由 compiler 猜。
    """

    chapter_ordinal: int = Field(ge=1)
    ending_scene_id: str = Field(min_length=1)
    driver_turn: str = Field(min_length=1)
    final_beat_event: str = Field(min_length=1)
    present_chars: tuple[str, ...] = Field(min_length=0)


class ChapterContinuity(StrictModel):
    """章级承接的数据面（R2.6）：本章 entry＋上一章末场机械投影。

    renderer 负责把它措辞成 continuity 分区的自然语言；IR 只携带事实。
    第 1 章没有上一章（`previous_ending=None`），entry 固定开篇。"""

    entry: ChapterEntry
    previous_ending: PreviousChapterEnding | None = None
    anchor_event: str | None = None


class ChapterIR(StrictModel):
    """单章静态写作输入：章目的、篇幅目标、本章场景的双视图上下文与验证材料。

    rc8（D2，B4）：称谓许可（章级已释放字面，validator 的认领面）与禁用字面
    （validator 的检查面；不进 Writer 阅读面）在 compile 时算好并随章 IR
    携带——Stage5 只做语言实现，不再从 Design／Characters 现场推导。
    rc10（R2）：`continuity` 必填——本章 entry＋上一章末场机械投影（章缝合
    契约的数据面；缺失即拒，不用空值冒充「已考虑承接」）。
    """

    chapter_ordinal: int = Field(ge=1)
    purpose: str = Field(min_length=1)
    length_budget: int = Field(ge=1)
    scenes: tuple[WriterContext, ...] = Field(min_length=1)
    #: 章级承接数据面（R2.6：entry＋上一章末场机械投影；必填）。
    continuity: ChapterContinuity
    #: 章级已释放称谓字面（本章含之前全部释放位置的并集；机械 validator 的认领面）。
    released_surfaces: tuple[str, ...] = Field(default=(), min_length=0)
    #: 本章禁用字面（全部专名／带名 surface 与家族级构词 − 已释放称谓；
    #: 机械 validator 的检查面，不渲染进 Writer 阅读面）。
    withheld_literals: tuple[str, ...] = Field(default=(), min_length=0)

    @model_validator(mode='after')
    def _scenes_unique(self) -> 'ChapterIR':
        ids = [s.scene_id for s in self.scenes]
        if len(set(ids)) != len(ids):
            raise ValueError(f'章 {self.chapter_ordinal} 内 scene_id 重复')
        return self


class WriterBookIR(StrictModel):
    """全书静态写作 IR（Derived；唯一 writer = stage4 book_ir compiler）。

    rc7（B3 前半）：BookBrief 带整书风格与稳定人物声线（TargetDesign 结构化
    投影）；VerificationIR 的「尚未向读者确认的事实」按地址投影（D4）。
    rc8（D2，B4）：ChapterIR 增章级验证材料；完整 IR 只作存储与审计面，
    运行时经 `ChapterWriterPacket` 按章投影。"""

    contract_version: Literal['str06-rc14'] = 'str06-rc14'
    book_brief: BookBrief
    chapters: tuple[ChapterIR, ...] = Field(min_length=1)
    provenance: FreeProvenance | LlmProvenance | None = Field(
        default=None, alias='_provenance', exclude=True)

    def chapter(self, ordinal: int) -> ChapterIR:
        return next(c for c in self.chapters if c.chapter_ordinal == ordinal)

    @model_validator(mode='after')
    def _book_consistent(self) -> 'WriterBookIR':
        ordinals = [c.chapter_ordinal for c in self.chapters]
        if ordinals != list(range(1, len(self.chapters) + 1)):
            raise ValueError(f'ChapterIR 章号必须从 1 连续编号，实得 {ordinals}')
        nav = self.book_brief.chapters
        if [n.chapter_ordinal for n in nav] != ordinals:
            raise ValueError('BookBrief 导航与 ChapterIR 章序不一致')
        for entry, chapter in zip(nav, self.chapters):
            if entry.purpose != chapter.purpose:
                raise ValueError(f'章 {chapter.chapter_ordinal} 的导航目的与章 IR 不一致')
            scene_ids = [s.scene_id for s in chapter.scenes]
            if entry.scene_ids != tuple(scene_ids):
                raise ValueError(f'章 {chapter.chapter_ordinal} 的导航场景与章 IR 不一致')
            if len(entry.scene_lines) != len(scene_ids):
                raise ValueError(f'章 {chapter.chapter_ordinal} 的导航行数与场景数不一致')
        if self.book_brief.target_chapter_count != len(self.chapters):
            raise ValueError('BookBrief 章数与 ChapterIR 数量不一致')
        seen: set[str] = set()
        for chapter in self.chapters:
            for scene in chapter.scenes:
                if scene.scene_id in seen:
                    raise ValueError(f'scene {scene.scene_id} 在全书 IR 中重复')
                seen.add(scene.scene_id)
        return self


class PacketBrief(StrictModel):
    """章包书级层（D2）：整书风格、世界边界、稳定人物声线与表达规则。

    不含导航（`BookBrief.chapters`）、其他章内容与称谓总表
    （`BookBrief.release_schedule`）——那些是全书审计面，不是本章写手的输入。
    """

    book_title: str = Field(default='', min_length=0)
    book_style: str = Field(min_length=1)
    narration: HookPov
    expression_decisions: tuple[ExpressionDecision, ...] = Field(min_length=1)
    world_rules: tuple[str, ...] = Field(default=(), min_length=0)
    character_voices: tuple[CharacterVoice, ...] = Field(default=(), min_length=0)
    target_chapter_count: int = Field(ge=1)
    total_length_budget: int = Field(ge=1)
    length_band_low: float = Field(gt=0)
    length_band_high: float = Field(gt=0)

    @model_validator(mode='after')
    def _band_ordered(self) -> 'PacketBrief':
        if self.length_band_low >= self.length_band_high:
            raise ValueError('篇幅带下界必须小于上界')
        return self


class RealizationContract(StrictModel):
    """章包交付层（D2）：写作协议版本与本章交付要求（compiler 从章预算与篇幅带派生）。

    协议正文（表达自由边界、确认清单纪律、称谓纪律等固定条款）由 renderer 按
    `protocol_version` 渲染；这里只携带身份与逐章参数，不复制协议文本
    （I-11：协议文本的唯一权威在 `novel/stages/common.py::WRITER_PROTOCOL`）。
    """

    protocol_version: str = Field(min_length=1)
    target_chars: int = Field(ge=1)
    band_low: int = Field(ge=0)
    band_high: int = Field(ge=0)

    @model_validator(mode='after')
    def _band_ordered(self) -> 'RealizationContract':
        if self.band_low > self.band_high:
            raise ValueError('篇幅带下界不得大于上界')
        return self


class ChapterWriterPacket(StrictModel):
    """章包三件套（D2）：Writer 与 Validator 的**唯一**输入面。

    PacketBrief＋ChapterIR（含验证材料）＋RealizationContract。运行时由
    compiler 从 WriterBookIR 确定性投影（`novel/stages/common.py::
    project_chapter_packet`），**从不落盘**（I-16：完整 WriterIR 是唯一正式
    产物，章包是它的派生视图）；章回执的分层摘要（`chapter_identities`：
    写手视图／验证输入／Machine 审计）与请求身份从章包派生。Writer 不读
    前文正文、Design、Characters 或其他章 IR。
    """

    book_brief: PacketBrief
    chapter: ChapterIR
    realization_contract: RealizationContract

    @model_validator(mode='after')
    def _chapter_in_book(self) -> 'ChapterWriterPacket':
        if self.chapter.chapter_ordinal > self.book_brief.target_chapter_count:
            raise ValueError(
                f'章 {self.chapter.chapter_ordinal} 超出全书章数 '
                f'{self.book_brief.target_chapter_count}')
        return self
