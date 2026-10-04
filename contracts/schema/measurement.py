#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""即时机械测量的 typed 值；它不是盘上 artifact schema。

旧链聚合形状（`MeasurableBody`／`ChapterCharStats`／`PerChapterSentence`）
已随投影构建族删除（2026-10-04 清理批）；现役正文机械检查在
`novel/stages/stage5/body_checks.py` 消费点即时计算，不经本 schema 中转。
"""
from __future__ import annotations

from pydantic import Field

from contracts.schema.base import StrictModel


class MeasurableBlock(StrictModel):
    chars: int = Field(ge=0)
    paragraphs: int = Field(ge=0)
    sentences: int = Field(ge=1)
    avg_paragraph_chars: float = Field(ge=0)
    paragraph_sep_chars: float = Field(ge=0)
    avg_sentence_chars: float = Field(ge=0)
    dialogue_ratio: float | None
    quote_styles: list[str]


__all__ = ['MeasurableBlock']
