"""章级蓝图审阅：固定维度与实际问题，禁止逐节点正向回执。"""
from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator
from contracts.invariants import PLOT_REVIEW_DIMENSIONS, PLOT_DIMENSION_STATES

from contracts.schema.base import FrozenModel, StrictModel
from contracts.schema.common import Sha256
from contracts.schema.dependencies import DependencyManifest
from contracts.schema.plot import PlotSubjectRef

ReviewScope = Annotated[str, Field(pattern=r'^(chapter:[1-9]\d*|cross_chapter)$')]
Dimension = Literal[PLOT_REVIEW_DIMENSIONS]
IssueId = Annotated[str, Field(pattern=r'^pi_[0-9a-f]{24}$')]
LocalIssueId = Annotated[str, Field(pattern=r'^new:[1-9]\d*$')]


class ReviewEvidenceRef(FrozenModel):
    chapter_ordinal: int = Field(ge=1, strict=True)
    evidence_ref: str = Field(min_length=1)


class DimensionVerdict(StrictModel):
    state: Literal[PLOT_DIMENSION_STATES]
    issue_refs: list[str]
    reason: str | None = None

    @model_validator(mode='after')
    def _meaning(self) -> Self:
        if len(set(self.issue_refs)) != len(self.issue_refs):
            raise ValueError('同一维度不能重复引用问题')
        if (self.state == 'issue') != bool(self.issue_refs):
            raise ValueError('issue 维度必须指向实际问题，其余状态不能引用未解决问题')
        if self.state in ('unver', 'not_applicable') and not (
                self.reason and self.reason.strip()):
            raise ValueError('无法判定和不适用必须分别说明原因')
        return self


class ChapterReviewCoverage(StrictModel):
    completeness: DimensionVerdict
    attribution: DimensionVerdict
    causal_continuity: DimensionVerdict
    epistemic_reveal: DimensionVerdict
    carry_state: DimensionVerdict


class NewPlotIssue(StrictModel):
    issue_key: LocalIssueId
    dimension: Dimension
    problem: str = Field(min_length=1)
    subjects: list[PlotSubjectRef] = Field(min_length=1)
    evidence_refs: list[ReviewEvidenceRef] = Field(min_length=1)

    @model_validator(mode='after')
    def _subjects_unique(self) -> Self:
        if len({row.key() for row in self.subjects}) != len(self.subjects):
            raise ValueError('同一问题的主体使用不能重复；两种表面身份各自保留')
        return self


class PlotIssueDisposition(StrictModel):
    issue_id: IssueId
    state: Literal['open', 'resolved', 'withdrawn']
    reason: str = Field(min_length=1)


class PlotReviewResponse(StrictModel):
    decision: Literal['accept', 'revise', 'unver']
    reason: str = Field(min_length=1)
    coverage: ChapterReviewCoverage
    new_issues: list[NewPlotIssue]
    dispositions: list[PlotIssueDisposition]

    @model_validator(mode='after')
    def _consistent(self) -> Self:
        keys = [row.issue_key for row in self.new_issues]
        ids = [row.issue_id for row in self.dispositions]
        if len(keys) != len(set(keys)) or len(ids) != len(set(ids)):
            raise ValueError('新增问题或历史问题处置不能重复')
        states = [row.state for row in self.coverage.__dict__.values()]
        if self.decision == 'accept' and (
                any(s not in ('pass', 'not_applicable') for s in states)
                or self.new_issues or any(r.state == 'open' for r in self.dispositions)):
            raise ValueError('接受与未解决问题或未核验维度冲突')
        if self.decision == 'revise' and 'issue' not in states:
            raise ValueError('返修必须明确实际问题维度')
        if self.decision == 'unver' and 'unver' not in states:
            raise ValueError('无法判定必须明确无法判断的维度')
        return self


class PlotIssueEvent(FrozenModel):
    decision_sha256: Sha256
    state: Literal['open', 'resolved', 'withdrawn']
    reason: str = Field(min_length=1)


class PlotIssueRecord(FrozenModel):
    issue_id: IssueId
    first_scope: ReviewScope
    dimension: Dimension
    problem: str = Field(min_length=1)
    subjects: tuple[PlotSubjectRef, ...]
    evidence_refs: tuple[ReviewEvidenceRef, ...]
    history: tuple[PlotIssueEvent, ...] = Field(min_length=1)

    @property
    def state(self) -> str:
        return self.history[-1].state


class PlotScopeAcceptance(FrozenModel):
    scope: ReviewScope
    decision_sha256: Sha256
    subject_sha256: Sha256


class PlotReviewRecord(FrozenModel):
    scope: ReviewScope
    actor: str = Field(min_length=1)
    subject_sha256: Sha256
    request_sha256: Sha256
    decision_sha256: Sha256
    dependency_manifest: DependencyManifest
    response: PlotReviewResponse
    issues: tuple[PlotIssueRecord, ...]


class PlotScopeStatus(FrozenModel):
    scope: ReviewScope
    state: Literal['missing', 'accepted', 'expired', 'rework', 'uncertain', 'stopped']
    subject_sha256: Sha256 | None = None
    decision_sha256: Sha256 | None = None
    open_issue_ids: list[IssueId]


class PlotReviewProgress(FrozenModel):
    total_chapters: int = Field(ge=1)
    generated_chapters: int = Field(ge=0)
    accepted_chapters: int = Field(ge=0)
    next_scope: ReviewScope | None
    scopes: list[PlotScopeStatus]
    unresolved_issues: int = Field(ge=0)
    accepted: bool


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
    review: PlotReviewProgress | None = None
    chapters: list[BlueprintChapterProgress]
    unavailable_reason: str | None = None
