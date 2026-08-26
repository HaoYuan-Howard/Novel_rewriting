#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""即时机械测量的 typed 值；它不是盘上 artifact schema。"""
from __future__ import annotations

from pydantic import Field

from contracts.schema.base import StrictModel


class ChapterCharStats(StrictModel):
    min: int = Field(ge=0)
    max: int = Field(ge=0)
    avg: float = Field(ge=0)


class PerChapterSentence(StrictModel):
    min: float = Field(ge=0)
    max: float = Field(ge=0)


class MeasurableBlock(StrictModel):
    chars: int = Field(ge=0)
    paragraphs: int = Field(ge=0)
    sentences: int = Field(ge=1)
    avg_paragraph_chars: float = Field(ge=0)
    paragraph_sep_chars: float = Field(ge=0)
    avg_sentence_chars: float = Field(ge=0)
    dialogue_ratio: float | None
    quote_styles: list[str]


class MeasurableBody(MeasurableBlock):
    chapter_chars: ChapterCharStats
    per_chapter_avg_sentence: PerChapterSentence


__all__ = [
    'ChapterCharStats', 'MeasurableBlock', 'MeasurableBody', 'PerChapterSentence',
]
