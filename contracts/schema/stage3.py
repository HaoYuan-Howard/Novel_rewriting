#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage3 v2：正文与开篇文案的确定性机械基线。

Stage3 曾同时保存 LLM 抽取的定性文风与代码测得的机械数字。真实下游实验没有证明
定性包带来稳定收益，因此 v2 删除 ``voice``、``surface_habits``、``hook_style``、
``chapter_styles`` 及全部模型调用/修复审计，只保留可免费重算、可逐项复核的
``measurable``。

JSON 信封版本与测量契约版本是两根轴：前者由 ``ARTIFACT_SPECS`` 管形状，后者落在
免费 provenance 的 ``measurement_contract``，并进入 Stage3 自身的 consumed 投影。
算法口径改变时即使字段形状没变，freshness 也会把旧数字判为 stale。
"""
from __future__ import annotations

from pydantic import Field

from contracts.schema.base import StrictModel
from contracts.schema.common import FreeProvenance


class ChapterCharStats(StrictModel):
    """正文逐章篇幅的最小值、最大值和均值。"""

    min: int = Field(ge=0)
    max: int = Field(ge=0)
    avg: float = Field(ge=0)


class PerChapterSentence(StrictModel):
    """各章平均句长的最小值与最大值。"""

    min: float = Field(ge=0)
    max: float = Field(ge=0)


class MeasurableBlock(StrictModel):
    """正文聚合与原作文案共有的机械字段。

    ``chars`` 统计段内字符，不含段间换行；``paragraph_sep_chars`` 单独记录分隔符
    的平均占字，避免下游把两种口径直接相除。对白比例不适用时
    ``dialogue_ratio_applicable=False`` 且 ``dialogue_ratio=None``。
    """

    chars: int = Field(ge=0)
    paragraphs: int = Field(ge=0)
    sentences: int = Field(ge=1)
    avg_paragraph_chars: float = Field(ge=0)
    paragraph_sep_chars: float = Field(ge=0)
    avg_sentence_chars: float = Field(ge=0)
    dialogue_ratio: float | None
    dialogue_ratio_applicable: bool
    quote_styles: list[str]


class MeasurableBody(MeasurableBlock):
    """正文机械块比原作文案多出的逐章统计。"""

    chapter_chars: ChapterCharStats
    per_chapter_avg_sentence: PerChapterSentence


class Measurable(StrictModel):
    """Stage3 的完整机械基线；正文与原作文案分开测量。"""

    body: MeasurableBody
    hook: MeasurableBlock


class StyleProvenance(FreeProvenance):
    """Stage3 v2 的免费来源账。

    ``measurement_contract`` 记录数字的算法口径；结构上没有 ``model``、模型 prompt、
    attempt、repair、revalidation 或人工来源字段的专属扩展。
    """

    # 不用 Literal[当前版本]：算法升版时旧 v2 仍须可读，随后由 consumed 投影
    # 明确判 stale；若载体先拒读，freshness 反而说不出「只是测量口径过期」。
    measurement_contract: str = Field(min_length=1)


class StyleGrounding(StrictModel):
    """``stage3/style_grounding.json`` v2 的业务载荷。"""

    measurable: Measurable
    provenance: StyleProvenance = Field(alias='_provenance')
