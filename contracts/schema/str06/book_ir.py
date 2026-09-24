#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""book_ir —— WriterBookIR：全书静态写作 IR（F5 RC4）。

【Derived 定位与失效语义】
WriterBookIR 由 compiler 从（BodyDesign, ExecutionPlan, SemanticBinding, 篇幅
政策）确定性投影，唯一落盘 `stage4/ir/writer_book_ir.json`；不是第五类 truth。
它是正文请求身份的一部分：IR 的任何 Writer 可见变化（包括未来章的场景
brief）改变全书请求，全部正文重新核验——章级回执绑定 `book_ir_sha256`。

【两层结构】
BookBrief 汇集已有全书约束与按阅读序的确定性导航：不调用 LLM 总结、不取场景
POV 众数、不补造统一文风；额外禁止项只从已有明确限制及释放位置投影，不从
未来剧情猜政策。ChapterIR 携带章目的、篇幅目标与本章场景的 WriterContext
（双视图沿用 context.py 载体）；场景上下文在 Stage4 一次成型，Stage5 不反向
读取正文补写 IR。
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.str06.context import WriterContext
from contracts.schema.str06.state import SurfaceRelease

__all__ = ['BookBrief', 'ChapterIR', 'ChapterNavEntry', 'ReleaseScheduleEntry',
           'WriterBookIR']


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


class BookBrief(StrictModel):
    """全书约束与导航：Writer 阅读面的书级事实，全部为确定性投影。"""

    book_title: str = Field(default='', min_length=0)
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


class ChapterIR(StrictModel):
    """单章静态写作输入：章目的、篇幅目标与本章场景的双视图上下文。"""

    chapter_ordinal: int = Field(ge=1)
    purpose: str = Field(min_length=1)
    length_budget: int = Field(ge=1)
    scenes: tuple[WriterContext, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def _scenes_unique(self) -> 'ChapterIR':
        ids = [s.scene_id for s in self.scenes]
        if len(set(ids)) != len(ids):
            raise ValueError(f'章 {self.chapter_ordinal} 内 scene_id 重复')
        return self


class WriterBookIR(StrictModel):
    """全书静态写作 IR（Derived；唯一 writer = stage4 book_ir compiler）。"""

    contract_version: Literal['str06-rc4'] = 'str06-rc4'
    book_brief: BookBrief
    chapters: tuple[ChapterIR, ...] = Field(min_length=1)

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
