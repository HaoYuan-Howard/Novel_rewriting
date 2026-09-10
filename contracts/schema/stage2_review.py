"""Stage2 两位 Commander 共用的义务覆盖与不可变裁决回执。"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from contracts.invariants import STAGE2_NODE_REVIEWERS, STAGE2_NODE_REVIEW_OWNERS
from contracts.schema.base import FrozenModel, StrictModel
from contracts.schema.common import Sha256


class Stage2ReviewObligation(FrozenModel):
    obligation_id: str = Field(min_length=1)
    subject_sha256: Sha256
    owner: Literal[STAGE2_NODE_REVIEW_OWNERS]
    chapter_ordinals: list[int]
    subject_path: str = Field(min_length=1)
    kind: Literal['global_item', 'fact', 'structure', 'consistency', 'removed_problem']
    context_refs: list[str]
    problem_subject_sha256: Sha256 | None = None


class Stage2ReviewScope(FrozenModel):
    contract: Literal['stage2-review-obligations-v1'] = 'stage2-review-obligations-v1'
    node: Literal[STAGE2_NODE_REVIEWERS]
    scope_sha256: Sha256
    obligations: list[Stage2ReviewObligation]

    @model_validator(mode='after')
    def _unique(self) -> Stage2ReviewScope:
        ids = [row.obligation_id for row in self.obligations]
        if len(ids) != len(set(ids)):
            raise ValueError('Stage2 审阅义务不可重复')
        return self


class Stage2ReviewUnit(FrozenModel):
    obligation: Stage2ReviewObligation
    decision: Literal['accept', 'rework', 'uncertain', 'stop']
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    source_decision_sha256: Sha256 | None = None


class Stage2ReviewReceipt(FrozenModel):
    scope: Stage2ReviewScope
    units: list[Stage2ReviewUnit]

    @model_validator(mode='after')
    def _complete(self) -> Stage2ReviewReceipt:
        actual = {row.obligation.obligation_id: row.obligation for row in self.units}
        expected = {row.obligation_id: row for row in self.scope.obligations}
        if len(actual) != len(self.units) or actual != expected:
            raise ValueError('Stage2 回执必须完整覆盖当前义务主体')
        return self


class Stage2ReviewCoverage(FrozenModel):
    scope: Stage2ReviewScope
    inherited: list[Stage2ReviewUnit]
    pending_obligation_ids: list[str]
    prior_problems: list[Stage2ReviewUnit] = Field(default_factory=list)

    @model_validator(mode='after')
    def _complete(self) -> Stage2ReviewCoverage:
        expected = {row.obligation_id: row for row in self.scope.obligations}
        retained = {row.obligation.obligation_id: row for row in self.inherited}
        pending = set(self.pending_obligation_ids)
        if (len(retained) != len(self.inherited) or len(pending) != len(self.pending_obligation_ids)
                or retained.keys() & pending or retained.keys() | pending != expected.keys()
                or any(row.decision != 'accept' or row.source_decision_sha256 is None
                       or row.obligation != expected[key] for key, row in retained.items())):
            raise ValueError('待审与有效继承须不重不漏覆盖当前义务')
        return self


class Stage2ReviewException(FrozenModel):
    obligation_id: str = Field(min_length=1)
    decision: Literal['rework', 'uncertain', 'stop']
    reason: str = Field(min_length=1)


class Stage2ReviewSubmission(StrictModel):
    """默认决定只展开到本轮待审项，继承内容和原 actor/reason 由服务核验。"""

    scope_sha256: Sha256
    default_decision: Literal['accept', 'uncertain', 'stop']
    exceptions: list[Stage2ReviewException]

    @model_validator(mode='after')
    def _unique(self) -> Stage2ReviewSubmission:
        ids = [row.obligation_id for row in self.exceptions]
        if len(ids) != len(set(ids)):
            raise ValueError('Stage2 同一义务不能重复给出例外')
        return self
