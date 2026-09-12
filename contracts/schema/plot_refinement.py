"""下游提出疑似上游缺陷；只申请原文核查，不授权生成或改写蓝图。"""
from __future__ import annotations

from typing import Literal, Self
from pydantic import Field, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.plot_review import Dimension


class BlueprintRefinementRequest(StrictModel):
    origin: Literal['design', 'structure']
    review_token: str = Field(min_length=1)
    actor: str = Field(min_length=1, max_length=256)
    chapter_ordinal: int = Field(ge=1, strict=True)
    dimension: Dimension
    subject_refs: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=4096)

    @model_validator(mode='after')
    def _explicit(self) -> Self:
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError('疑似缺陷必须有提出者和具体问题')
        if (len(set(self.subject_refs)) != len(self.subject_refs)
                or any(not value.strip() for value in self.subject_refs)):
            raise ValueError('疑似缺陷引用不能为空或重复')
        return self


class BlueprintRefinementCatalog(StrictModel):
    origin: Literal['design', 'structure']
    review_token: str = Field(min_length=1)
    # A 仅含不带源序的 opaque alias；chapter 是缺失主体的本章边界。
    subjects_by_chapter: dict[int, list[str]]
