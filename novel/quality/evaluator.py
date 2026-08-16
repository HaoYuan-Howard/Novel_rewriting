#!/usr/bin/env python3
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from contracts.schema.evaluation import (
    Ability,
    EvaluatorFingerprint,
    EvaluatorProfile,
    EvidenceRef,
    JudgeDecision,
    SemanticDecision,
)


def wilson_lower(successes: int, total: int, *, z: float = 1.959963984540054) -> float:
    """Lower endpoint of the two-sided 95% Wilson score interval."""
    if total <= 0:
        return 0.0
    if successes < 0 or successes > total:
        raise ValueError('successes must be within [0, total]')
    p = successes / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return max(0.0, (centre - margin) / denominator)


def profile_matches(
    profile: EvaluatorProfile,
    ability: Ability,
    current: EvaluatorFingerprint,
) -> bool:
    """Every per-capability calibration fingerprint is an equality gate."""
    row = next((item for item in profile.capabilities if item.ability == ability), None)
    return row is not None and row.fingerprint == current


def capability_enabled(
    profile: EvaluatorProfile | None,
    ability: Ability,
    current: EvaluatorFingerprint,
) -> tuple[bool, str]:
    if profile is None:
        return False, '没有已校准 evaluator profile'
    row = next((item for item in profile.capabilities if item.ability == ability), None)
    if row is None:
        return False, f'profile 未登记能力 {ability}'
    if not profile_matches(profile, ability, current):
        return False, 'dataset/model/prompt/schema/decoder 指纹与该能力校准 profile 不一致'
    if not row.enabled or not row.threshold_passed():
        return False, f'{ability} 未达到预先锁定的启用门，保持人工-only'
    return True, ''


def _closed_evidence(decision: JudgeDecision) -> bool:
    """A non-UNVER verdict must point to at least one well-formed subject span."""
    return decision.verdict == 'UNVER' or bool(decision.evidence)


def double_blind_decision(
    *,
    ability: Ability,
    subject_key: str,
    first: JudgeDecision,
    second: JudgeDecision,
    profile: EvaluatorProfile | None,
    current: EvaluatorFingerprint,
) -> SemanticDecision:
    """Admit an automatic verdict only on calibrated, agreeing, evidenced judges."""
    enabled, why = capability_enabled(profile, ability, current)
    judges = [first, second]
    evidence: list[EvidenceRef] = []
    seen: set[tuple] = set()
    for row in judges:
        for ref in row.evidence:
            key = (ref.artifact, ref.subject_sha256, ref.start, ref.end)
            if key not in seen:
                seen.add(key)
                evidence.append(ref)

    if not enabled:
        return SemanticDecision(
            ability=ability, subject_key=subject_key,
            verdict='UNVER', automated=False, evidence=evidence,
            reason=why, profile_id=profile.profile_id if profile else None, judges=judges)
    if first.verdict == 'UNVER' or second.verdict == 'UNVER':
        return SemanticDecision(
            ability=ability, subject_key=subject_key,
            verdict='UNVER', automated=False, evidence=evidence,
            reason='至少一名盲评 judge 明确判为 UNVER', profile_id=profile.profile_id,
            judges=judges)
    if first.verdict != second.verdict:
        return SemanticDecision(
            ability=ability, subject_key=subject_key,
            verdict='UNVER', automated=False, evidence=evidence,
            reason='两名盲评 judge 结论不一致', profile_id=profile.profile_id,
            judges=judges)
    if not all(_closed_evidence(row) for row in judges):
        return SemanticDecision(
            ability=ability, subject_key=subject_key,
            verdict='UNVER', automated=False, evidence=evidence,
            reason='judge 结论缺少可闭合证据', profile_id=profile.profile_id,
            judges=judges)
    return SemanticDecision(
        ability=ability, subject_key=subject_key,
        verdict=first.verdict, automated=True, evidence=evidence,
        reason='已校准双盲 judge 同结论且证据闭合', profile_id=profile.profile_id,
        judges=judges)


@dataclass(frozen=True)
class Confusion:
    true_pass: int = 0
    false_pass: int = 0
    true_fail: int = 0
    false_fail: int = 0

    @property
    def pass_precision_lower(self) -> float:
        return wilson_lower(self.true_pass, self.true_pass + self.false_pass)

    @property
    def fail_precision_lower(self) -> float:
        return wilson_lower(self.true_fail, self.true_fail + self.false_fail)

    @property
    def violation_recall_lower(self) -> float:
        return wilson_lower(self.true_fail, self.true_fail + self.false_pass)


def confusion(expected_and_actual: Iterable[tuple[str, str]]) -> Confusion:
    counts = {'true_pass': 0, 'false_pass': 0, 'true_fail': 0, 'false_fail': 0}
    for expected, actual in expected_and_actual:
        if actual not in {'PASS', 'FAIL'}:
            continue
        if expected == 'PASS' and actual == 'PASS':
            counts['true_pass'] += 1
        elif expected == 'FAIL' and actual == 'PASS':
            counts['false_pass'] += 1
        elif expected == 'FAIL' and actual == 'FAIL':
            counts['true_fail'] += 1
        elif expected == 'PASS' and actual == 'FAIL':
            counts['false_fail'] += 1
        else:
            raise ValueError(f'unsupported label pair: {expected!r}, {actual!r}')
    return Confusion(**counts)
