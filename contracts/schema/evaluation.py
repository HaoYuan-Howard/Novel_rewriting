#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Versioned contracts for calibrated semantic evaluation.

The evaluator is deliberately fail-closed.  A model verdict is evidence only
when the profile is enabled *and* every fingerprint still matches the run.
Human labels and source excerpts live outside the repository; committed
manifests contain identities, counts, hashes, and adjudicated labels only.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from contracts.invariants import DEPTH_MODES
from contracts.schema.base import StrictModel
from contracts.schema.common import FreeProvenance


EVALUATOR_ABILITIES = (
    'event_order',
    'verbatim_fidelity',
    'hard_number_fidelity',
    'reference_boundary',
    'secret_timeline',
    'world_rule_consistency',
    'trim_equivalence',
    'style_transfer',
)

Ability = Literal[EVALUATOR_ABILITIES]
SemanticVerdict = Literal['PASS', 'FAIL', 'UNVER']


class EvidenceRef(StrictModel):
    """A locator, never a copied source excerpt."""

    artifact: str = Field(min_length=1)
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)

    @model_validator(mode='after')
    def _ordered_span(self) -> 'EvidenceRef':
        if (self.start is None) != (self.end is None):
            raise ValueError('evidence span must provide start and end together')
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError('evidence end must be greater than start')
        return self


class GroundTruthCase(StrictModel):
    case_id: str = Field(pattern=r'^[a-z0-9][a-z0-9_.-]{7,127}$')
    ability: Ability
    genre: str = Field(min_length=1)
    expected: Literal['PASS', 'FAIL']
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    evidence: list[EvidenceRef] = Field(min_length=1)


class GroundTruthManifest(StrictModel):
    """The public, text-free identity of a private labelled dataset."""

    manifest_version: int = Field(ge=1)
    dataset_id: str = Field(min_length=1)
    dataset_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    rubric_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    created_at: str = Field(min_length=1)
    annotators: int = Field(ge=2)
    adjudicator: str = Field(min_length=1)
    agreement: float = Field(ge=0.0, le=1.0)
    cases: list[GroundTruthCase] = Field(min_length=1)

    @model_validator(mode='after')
    def _held_out_floor(self) -> 'GroundTruthManifest':
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError('ground-truth case_id must be unique')
        if self.agreement < 0.8:
            raise ValueError('inter-annotator agreement < 0.8; revise rubric and relabel')
        # Calibration is capability-scoped.  A manifest registers an ability
        # by actually containing cases for it; unregistered abilities remain
        # explicit disabled n=0 rows in the published profile instead of
        # forcing unrelated annotation work up front.
        registered = sorted({case.ability for case in self.cases})
        for ability in registered:
            rows = [case for case in self.cases if case.ability == ability]
            passed = [case for case in rows if case.expected == 'PASS']
            failed = [case for case in rows if case.expected == 'FAIL']
            if len(rows) < 100 or len(passed) < 40 or len(failed) < 40:
                raise ValueError(
                    f'{ability}: need >=100 held-out cases with PASS/FAIL each >=40')
            for label, group in (('PASS', passed), ('FAIL', failed)):
                genres: dict[str, int] = {}
                for case in group:
                    genres[case.genre] = genres.get(case.genre, 0) + 1
                if sum(count >= 10 for count in genres.values()) < 4:
                    raise ValueError(
                        f'{ability}/{label}: need >=4 genres represented by >=10 cases each')
        return self


class EvaluatorFingerprint(StrictModel):
    dataset_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    model: str = Field(min_length=1)
    prompt_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    schema_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    decoder_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class CapabilityMetrics(StrictModel):
    ability: Ability
    #: Prompt/schema/decoder surfaces differ by ability.  Keeping this binding
    #: on the capability row prevents one global fingerprint from pretending
    #: to describe eight incompatible judge protocols.
    fingerprint: EvaluatorFingerprint
    #: Registered abilities have the held-out floor (>=100).  An ability that
    #: is absent from the manifest remains visible as disabled with n=0.
    n: int = Field(ge=0)
    pass_precision_wilson95_lower: float = Field(ge=0.0, le=1.0)
    fail_precision_wilson95_lower: float = Field(ge=0.0, le=1.0)
    violation_recall_wilson95_lower: float = Field(ge=0.0, le=1.0)
    evidence_closure: float = Field(ge=0.0, le=1.0)
    rerun_consistency: float = Field(ge=0.0, le=1.0)
    enabled: bool

    def threshold_passed(self) -> bool:
        return (
            self.n >= 100
            and self.pass_precision_wilson95_lower >= 0.90
            and self.fail_precision_wilson95_lower >= 0.90
            and self.violation_recall_wilson95_lower >= 0.90
            and self.evidence_closure >= 0.95
            and self.rerun_consistency >= 0.95
        )

    @model_validator(mode='after')
    def _enabled_only_after_fixed_thresholds(self) -> 'CapabilityMetrics':
        if 0 < self.n < 100:
            raise ValueError(f'{self.ability}: n must be 0 or >=100')
        if self.n == 0:
            observed = (
                self.pass_precision_wilson95_lower,
                self.fail_precision_wilson95_lower,
                self.violation_recall_wilson95_lower,
                self.evidence_closure,
                self.rerun_consistency,
            )
            if self.enabled or any(value != 0.0 for value in observed):
                raise ValueError(
                    f'{self.ability}: missing capability must be disabled with n=0 metrics')
        if self.enabled and not self.threshold_passed():
            raise ValueError(f'{self.ability}: enabled despite failing fixed activation thresholds')
        return self


class EvaluatorProfile(StrictModel):
    profile_version: int = Field(ge=1)
    profile_id: str = Field(min_length=1)
    calibrated_at: str = Field(min_length=1)
    runs: int = Field(ge=3)
    thresholds_locked_before_evaluation: bool
    capabilities: list[CapabilityMetrics] = Field(min_length=8, max_length=8)

    @model_validator(mode='after')
    def _complete_and_precommitted(self) -> 'EvaluatorProfile':
        if not self.thresholds_locked_before_evaluation:
            raise ValueError('evaluator thresholds must be locked before evaluation')
        abilities = [row.ability for row in self.capabilities]
        if len(set(abilities)) != len(abilities) or set(abilities) != set(EVALUATOR_ABILITIES):
            raise ValueError('profile must contain every evaluator ability exactly once')
        return self


class JudgeDecision(StrictModel):
    judge: str = Field(min_length=1)
    verdict: SemanticVerdict
    evidence: list[EvidenceRef]
    reason: str = Field(min_length=1)


class SemanticDecision(StrictModel):
    ability: Ability
    subject_key: str = Field(min_length=1)
    verdict: SemanticVerdict
    automated: bool
    evidence: list[EvidenceRef]
    reason: str = Field(min_length=1)
    profile_id: str | None = None
    judges: list[JudgeDecision] = Field(min_length=2, max_length=2)


GlobalReviewState = Literal['pending', 'PASS', 'FAIL', 'UNVER']


class GlobalReviewItem(StrictModel):
    item_id: str = Field(min_length=1)
    kind: Literal[
        'world_rule_version', 'secret', 'reveal', 'known_by',
        'name_distinctiveness', 'address_context', 'world_rule_kind',
        'world_rule_set_complete', 'secret_set_complete',
    ]
    subject_path: str = Field(min_length=1)
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    state: GlobalReviewState
    evidence: list[EvidenceRef]
    actor: str | None = None
    decided_at: str | None = None
    reason: str | None = None

    @model_validator(mode='after')
    def _decision_has_audit_fields(self) -> 'GlobalReviewItem':
        decided = self.state != 'pending'
        values = (self.actor, self.decided_at, self.reason)
        if decided and any(not value for value in values):
            raise ValueError('decided global-review item requires actor, decided_at, and reason')
        if not decided and any(value is not None for value in values):
            raise ValueError('pending global-review item cannot claim decision audit fields')
        return self


class GlobalReview(StrictModel):
    source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    items: list[GlobalReviewItem]

    @model_validator(mode='after')
    def _unique_items(self) -> 'GlobalReview':
        ids = [item.item_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError('global review item_id must be unique')
        return self

    @property
    def all_pass(self) -> bool:
        """Row-only fact; gate approval additionally requires current grounding."""
        return bool(self.items) and all(item.state == 'PASS' for item in self.items)


class GlobalReviewDecision(StrictModel):
    item_id: str = Field(min_length=1)
    expected_subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    state: Literal['PASS', 'FAIL', 'UNVER']
    reason: str = Field(min_length=1)
    evidence: list[EvidenceRef]


class GlobalReviewDecisionSet(StrictModel):
    """A human-produced optimistic-lock decision file/API payload."""

    actor: str = Field(min_length=1)
    decisions: list[GlobalReviewDecision] = Field(min_length=1)

    @model_validator(mode='after')
    def _exactly_once(self) -> 'GlobalReviewDecisionSet':
        if not self.actor.strip() or any(not row.reason.strip() for row in self.decisions):
            raise ValueError('global-review actor/reason must not be blank')
        ids = [row.item_id for row in self.decisions]
        if len(ids) != len(set(ids)):
            raise ValueError('each global-review item may be decided at most once per request')
        return self


class Claim(StrictModel):
    kind: Literal['character', 'place', 'causality', 'time', 'ending']
    key: str = Field(min_length=1)
    value_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class TrimEvidenceSpan(StrictModel):
    """A judge-returned locator into one of the two in-memory trim subjects.

    The model never gets to assert an artifact identity or hash.  Runtime code
    supplies those after checking the offsets against the exact before/after
    strings, then converts these spans into :class:`EvidenceRef` values.
    """

    side: Literal['before', 'after']
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode='after')
    def _ordered_span(self) -> 'TrimEvidenceSpan':
        if self.end <= self.start:
            raise ValueError('trim evidence end must be greater than start')
        return self


class TrimJudgeResponse(StrictModel):
    """The only accepted wire shape for a blind trim-equivalence judge."""

    verdict: SemanticVerdict
    evidence: list[TrimEvidenceSpan]
    reason: str = Field(min_length=1)

    @model_validator(mode='after')
    def _verdict_has_candidate_evidence(self) -> 'TrimJudgeResponse':
        sides = {row.side for row in self.evidence}
        if self.verdict == 'PASS' and sides != {'before', 'after'}:
            raise ValueError('trim PASS requires closed evidence on before and after')
        if self.verdict == 'FAIL' and 'after' not in sides:
            raise ValueError('trim FAIL requires evidence on the candidate')
        if self.verdict == 'UNVER' and self.evidence:
            raise ValueError('trim UNVER must not pretend to have closed evidence')
        return self


class TrimAudit(StrictModel):
    output_ordinal: int = Field(ge=1)
    mode: Literal[DEPTH_MODES]
    before_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    after_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    before_claims: list[Claim]
    after_claims: list[Claim]
    deterministic_passed: bool
    evaluator_fingerprint: EvaluatorFingerprint | None = None
    semantic: SemanticDecision | None
    adopted: bool
    reason: str = Field(min_length=1)

    @model_validator(mode='after')
    def _adoption_is_fail_closed(self) -> 'TrimAudit':
        if (self.semantic is None) != (self.evaluator_fingerprint is None):
            raise ValueError(
                'trim semantic decision and evaluator_fingerprint must appear together')
        if self.semantic is not None and self.semantic.subject_key != self.after_sha256:
            raise ValueError('trim semantic decision must bind the audited after_sha256')
        if not self.deterministic_passed and self.semantic is not None:
            raise ValueError('deterministic trim failure must not call or record semantic judges')
        if self.adopted:
            if self.before_sha256 == self.after_sha256:
                raise ValueError('adopted trim must differ from its pretrim subject')
            if not self.deterministic_passed:
                raise ValueError('trim cannot be adopted after deterministic regression')
            if self.semantic is None or self.semantic.verdict != 'PASS' or not self.semantic.automated:
                raise ValueError('trim adoption requires calibrated double-judge PASS')
        return self


class TrimResolution(StrictModel):
    """A hash-bound human decision to keep the already-retained pretrim text.

    ``actor`` is a self-reported audit label, not an authenticated identity.
    There is deliberately no action that can adopt a trim candidate: only the
    calibrated automated gate may set :class:`TrimAudit.adopted`.
    """

    output_ordinal: int = Field(ge=1)
    mode: Literal[DEPTH_MODES]
    action: Literal['retain_pretrim']
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    audit_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    before_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    after_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    chapter_rel: str = Field(min_length=1)
    chapter_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    provenance: FreeProvenance = Field(alias='_provenance')

    @model_validator(mode='after')
    def _audit_text_is_not_blank(self) -> 'TrimResolution':
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError('actor/reason must not be blank')
        expected = f'stage5/{self.mode}/ch{self.output_ordinal:02d}.json'
        if self.chapter_rel != expected:
            raise ValueError(
                f'chapter_rel 必须精确绑定 mode/ordinal：期望 {expected}，实得 {self.chapter_rel}')
        if self.chapter_sha256 != self.before_sha256:
            raise ValueError('retain_pretrim 的 chapter_sha256 必须等于 audit.before_sha256')
        return self
