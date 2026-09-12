"""Core 主体的共享证据引用；正式锚点只保存可复验坐标与原文字节摘要。"""
from __future__ import annotations

from typing import Annotated, Self
from pydantic import Field, model_validator
from contracts.schema.base import StrictModel
from contracts.schema.common import Sha256

EvidenceId = Annotated[str, Field(pattern=r'^ev_[0-9a-f]{24}$')]
CoreEvidenceCandidateRef = Annotated[str, Field(pattern=r'^e[0-9]{6}$')]


class CoreEvidenceAnchor(StrictModel):
    chapter_ordinal: int = Field(ge=1, strict=True)
    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)
    text_sha256: Sha256

    @model_validator(mode='after')
    def _nonempty(self) -> Self:
        if self.end <= self.start:
            raise ValueError('证据区间必须非空且左闭右开')
        return self


class CoreEvidenceLocator(StrictModel):
    chapter_ordinal: int = Field(ge=1, strict=True)
    locator: str = Field(min_length=1, max_length=160)


CoreEvidenceEditRef = EvidenceId | CoreEvidenceLocator
