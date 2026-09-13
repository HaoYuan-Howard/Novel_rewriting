"""章级 B / 结构审阅共用的证据闭包；证据原文只有源材料表保存。"""
from __future__ import annotations
from typing import Literal
from pydantic import Field, JsonValue
from contracts.schema.base import FrozenModel
from contracts.schema.plot import ActorBinding, PlotBlueprint, PlotSubjectRef


class ReviewSourceRange(FrozenModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1)


class ReviewSourceChapter(FrozenModel):
    """实际下发的证据区间；完整源文身份由 canonical Blueprint/Global 和提交 CAS 绑定。"""
    chapter_ordinal: int = Field(ge=1)
    ranges: list[ReviewSourceRange] = Field(min_length=1)


class ReviewEvidenceAnchor(FrozenModel):
    evidence_ref: str
    chapter_ordinal: int = Field(ge=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class ReviewSupportSubject(FrozenModel):
    subject_ref: PlotSubjectRef
    value: JsonValue


class PlotChapterReviewMaterials(FrozenModel):
    contract: Literal['plot-chapter-evidence-v2'] = 'plot-chapter-evidence-v2'
    blueprint: PlotBlueprint
    support_subjects: list[ReviewSupportSubject]
    actor_bindings: list[ActorBinding]
    global_subjects: list[ReviewSupportSubject]
    source_context_catalog: dict[str, JsonValue]
    evidence_anchors: list[ReviewEvidenceAnchor]
    source_materials: list[ReviewSourceChapter]
