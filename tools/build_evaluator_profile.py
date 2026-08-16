#!/usr/bin/env python3
"""Build a text-free calibrated evaluator profile from three or more runs.

This tool never calls a model.  Paid/private evaluation runners emit one
``EvaluationRun`` per repetition; this command validates exact case coverage,
per-capability runtime fingerprints, blind-judge independence and then computes
the predeclared fail-closed metrics.  Only the aggregate ``EvaluatorProfile``
is suitable for publication.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Literal, Sequence

from pydantic import Field, model_validator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts.schema.base import StrictModel
from contracts.schema.evaluation import (
    Ability,
    CapabilityMetrics,
    EVALUATOR_ABILITIES,
    EvaluatorFingerprint,
    EvaluatorProfile,
    GroundTruthManifest,
    JudgeDecision,
)
from novel.quality.evaluator import wilson_lower


class CapabilityBinding(StrictModel):
    ability: Ability
    fingerprint: EvaluatorFingerprint


class EvaluationPrediction(StrictModel):
    case_id: str = Field(min_length=1)
    judges: list[JudgeDecision] = Field(min_length=2, max_length=2)

    @model_validator(mode='after')
    def _independent_judges(self) -> 'EvaluationPrediction':
        names = [row.judge.strip().casefold() for row in self.judges]
        if len(set(names)) != 2:
            raise ValueError('each prediction requires two distinct blind judges')
        return self


class EvaluationRun(StrictModel):
    run_version: Literal[1]
    run_id: str = Field(min_length=1)
    evaluated_at: str = Field(min_length=1)
    bindings: list[CapabilityBinding] = Field(min_length=8, max_length=8)
    predictions: list[EvaluationPrediction] = Field(min_length=1)

    @model_validator(mode='after')
    def _unique_surface(self) -> 'EvaluationRun':
        abilities = [row.ability for row in self.bindings]
        if len(set(abilities)) != len(abilities) or set(abilities) != set(EVALUATOR_ABILITIES):
            raise ValueError('run must bind every evaluator ability exactly once')
        ids = [row.case_id for row in self.predictions]
        if len(ids) != len(set(ids)):
            raise ValueError('run must predict each case at most once')
        return self


def _combined(prediction: EvaluationPrediction) -> str:
    first, second = prediction.judges
    if first.verdict == 'UNVER' or second.verdict == 'UNVER':
        return 'UNVER'
    if first.verdict != second.verdict:
        return 'UNVER'
    if not first.evidence or not second.evidence:
        return 'UNVER'
    return first.verdict


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _one_run_metrics(cases, predictions: dict[str, EvaluationPrediction]) -> dict[str, float]:
    expected = {case.case_id: case.expected for case in cases}
    actual = {case_id: _combined(predictions[case_id]) for case_id in expected}
    predicted_pass = [case_id for case_id, value in actual.items() if value == 'PASS']
    predicted_fail = [case_id for case_id, value in actual.items() if value == 'FAIL']
    expected_fail = [case_id for case_id, value in expected.items() if value == 'FAIL']
    judge_verdicts = [
        judge
        for case_id in expected
        for judge in predictions[case_id].judges
        if judge.verdict != 'UNVER'
    ]
    return {
        'pass_precision': wilson_lower(
            sum(expected[case_id] == 'PASS' for case_id in predicted_pass),
            len(predicted_pass)),
        'fail_precision': wilson_lower(
            sum(expected[case_id] == 'FAIL' for case_id in predicted_fail),
            len(predicted_fail)),
        'violation_recall': wilson_lower(
            sum(actual[case_id] == 'FAIL' for case_id in expected_fail),
            len(expected_fail)),
        'evidence_closure': _ratio(
            sum(bool(judge.evidence) for judge in judge_verdicts), len(judge_verdicts)),
    }


def build_profile(
    manifest: GroundTruthManifest,
    runs: Sequence[EvaluationRun],
    *,
    profile_id: str,
    calibrated_at: str,
) -> EvaluatorProfile:
    if len(runs) < 3:
        raise ValueError('at least three complete evaluation runs are required')
    run_ids = [row.run_id for row in runs]
    if len(run_ids) != len(set(run_ids)):
        raise ValueError('evaluation run_id values must be unique')

    expected_ids = {case.case_id for case in manifest.cases}
    prediction_maps: list[dict[str, EvaluationPrediction]] = []
    binding_maps: list[dict[Ability, EvaluatorFingerprint]] = []
    for run in runs:
        predictions = {row.case_id: row for row in run.predictions}
        if set(predictions) != expected_ids:
            missing = sorted(expected_ids - set(predictions))
            extra = sorted(set(predictions) - expected_ids)
            raise ValueError(
                f'{run.run_id}: prediction coverage mismatch; '
                f'missing={missing[:3]!r} extra={extra[:3]!r}')
        bindings = {row.ability: row.fingerprint for row in run.bindings}
        for ability, fingerprint in bindings.items():
            if fingerprint.dataset_sha256 != manifest.dataset_sha256:
                raise ValueError(f'{run.run_id}/{ability}: dataset fingerprint mismatch')
        prediction_maps.append(predictions)
        binding_maps.append(bindings)

    capabilities: list[CapabilityMetrics] = []
    for ability in EVALUATOR_ABILITIES:
        fingerprints = [bindings[ability] for bindings in binding_maps]
        if any(value != fingerprints[0] for value in fingerprints[1:]):
            raise ValueError(f'{ability}: model/prompt/schema/decoder drifted between runs')
        cases = [case for case in manifest.cases if case.ability == ability]
        per_run = [_one_run_metrics(cases, predictions) for predictions in prediction_maps]
        consistent = sum(
            len({
                _combined(predictions[case.case_id])
                for predictions in prediction_maps
            }) == 1
            for case in cases
        )
        raw = dict(
            ability=ability,
            fingerprint=fingerprints[0],
            n=len(cases),
            pass_precision_wilson95_lower=min(row['pass_precision'] for row in per_run),
            fail_precision_wilson95_lower=min(row['fail_precision'] for row in per_run),
            violation_recall_wilson95_lower=min(row['violation_recall'] for row in per_run),
            evidence_closure=min(row['evidence_closure'] for row in per_run),
            rerun_consistency=_ratio(consistent, len(cases)),
        )
        probe = CapabilityMetrics(**raw, enabled=False)
        capabilities.append(probe.model_copy(update={'enabled': probe.threshold_passed()}))

    return EvaluatorProfile(
        profile_version=1,
        profile_id=profile_id,
        calibrated_at=calibrated_at,
        runs=len(runs),
        thresholds_locked_before_evaluation=True,
        capabilities=capabilities,
    )


def _load(path: Path, model: type[StrictModel]) -> Any:
    return model.model_validate_json(path.read_text(encoding='utf-8'))


def _write_once(path: Path, model: StrictModel) -> None:
    payload = model.model_dump(mode='json')
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    if path.exists():
        if json.loads(path.read_text(encoding='utf-8')) == payload:
            return
        raise FileExistsError(f'refusing to overwrite different profile: {path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    temporary.write_text(rendered, encoding='utf-8')
    temporary.replace(path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--run', required=True, action='append', type=Path)
    parser.add_argument('--profile-id', required=True)
    parser.add_argument('--calibrated-at', required=True)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--validate-existing', action='store_true')
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        profile = build_profile(
            _load(args.manifest, GroundTruthManifest),
            [_load(path, EvaluationRun) for path in args.run],
            profile_id=args.profile_id,
            calibrated_at=args.calibrated_at,
        )
        if args.validate_existing:
            current = _load(args.out, EvaluatorProfile)
            if current != profile:
                raise ValueError('existing profile does not match recomputed evaluation results')
        else:
            _write_once(args.out, profile)
        print(json.dumps({
            'status': 'valid' if args.validate_existing else 'built',
            'profile': str(args.out),
            'enabled': [row.ability for row in profile.capabilities if row.enabled],
            'disabled': [row.ability for row in profile.capabilities if not row.enabled],
        }, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({
            'status': 'blocked', 'error': f'{type(exc).__name__}: {exc}',
        }, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
