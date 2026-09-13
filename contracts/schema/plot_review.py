"""Blueprint 阅读范围与生成进度；章审裁决模型已随 Stage2 语义审阅退役。

旧盘的审阅决定不迁移也不由本模块解码：V6 及更早输出根按冻结来源代码读取。
本模块只保留现役阅读工作包（``ReviewScope``）与生成进度投影所需的最小类型。
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from contracts.schema.base import FrozenModel

ReviewScope = Annotated[str, Field(pattern=r'^(chapter:[1-9]\d*|cross_chapter)$')]


class BlueprintChapterProgress(FrozenModel):
    chapter_ordinal: int = Field(ge=1)
    run_id: str | None = None
    checkpoint_state: Literal['missing', 'current', 'expired', 'unver']
    processing_state: Literal['pending', 'generated', 'mechanical', 'history_adopted',
                              'cached', 'failed', 'unknown'] | None = None
    reason: str | None = None


class BlueprintRunProgress(FrozenModel):
    """生成与接受分别计数；未知不会作为零或未运行显示。"""
    total_chapters: int | None = Field(default=None, ge=0)
    generated_chapters: int | None = Field(default=None, ge=0)
    checkpoint_chapters: int | None = Field(default=None, ge=0)
    submitted_chapters: int | None = Field(default=None, ge=0)
    artifact_state: Literal['missing', 'formal', 'invalid', 'unreadable']
    chapters: list[BlueprintChapterProgress]
    unavailable_reason: str | None = None
