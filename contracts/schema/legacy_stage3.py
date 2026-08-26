#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""退役 ``stage3/style_grounding.json`` v2 的冻结只读解码器。

这里只服务两件历史兼容：旧 Stage5/6 provenance freshness 与审批 v4 的显式
backfill。它不在 ``ARTIFACT_SPECS``，没有 producer、写入口或公开 API。
"""
from __future__ import annotations

from typing import Any

from pydantic import Field

from contracts.schema.base import StrictModel
from contracts.schema.common import FreeProvenance


class LegacyChapterCharStats(StrictModel):
    min: int = Field(ge=0)
    max: int = Field(ge=0)
    avg: float = Field(ge=0)


class LegacyPerChapterSentence(StrictModel):
    min: float = Field(ge=0)
    max: float = Field(ge=0)


class LegacyMeasurableBlock(StrictModel):
    chars: int = Field(ge=0)
    paragraphs: int = Field(ge=0)
    sentences: int = Field(ge=1)
    avg_paragraph_chars: float = Field(ge=0)
    paragraph_sep_chars: float = Field(ge=0)
    avg_sentence_chars: float = Field(ge=0)
    dialogue_ratio: float | None
    dialogue_ratio_applicable: bool
    quote_styles: list[str]


class LegacyMeasurableBody(LegacyMeasurableBlock):
    chapter_chars: LegacyChapterCharStats
    per_chapter_avg_sentence: LegacyPerChapterSentence


class LegacyMeasurable(StrictModel):
    body: LegacyMeasurableBody
    hook: LegacyMeasurableBlock


class LegacyStyleProvenance(FreeProvenance):
    measurement_contract: str = Field(min_length=1)


class LegacyStyleGrounding(StrictModel):
    measurable: LegacyMeasurable
    provenance: LegacyStyleProvenance = Field(alias='_provenance')


def decode_legacy_stage3_v2(data: Any) -> LegacyStyleGrounding:
    """只接受原始 v2 信封；无标记、低版、高版或多余字段均拒读。"""
    if not isinstance(data, dict) or data.get('_schema_version') != 2:
        marker = data.get('_schema_version') if isinstance(data, dict) else None
        raise ValueError(f'退役 Stage3 只读解码器只接受 v2，收到 {marker!r}')
    return LegacyStyleGrounding.model_validate({
        key: value for key, value in data.items() if key != '_schema_version'
    })


__all__ = ['LegacyStyleGrounding', 'decode_legacy_stage3_v2']
