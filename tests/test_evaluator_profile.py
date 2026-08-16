from __future__ import annotations

import hashlib

import pytest

from contracts.schema.evaluation import (
    EvidenceRef, GroundTruthCase, GroundTruthManifest, JudgeDecision,
)
from tools.build_evaluator_profile import (
    CapabilityBinding, EvaluationPrediction, EvaluationRun, build_profile,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _manifest() -> GroundTruthManifest:
    abilities = (
        'event_order', 'verbatim_fidelity', 'hard_number_fidelity',
        'reference_boundary', 'secret_timeline', 'world_rule_consistency',
        'trim_equivalence', 'style_transfer',
    )
    cases = []
    for ability in abilities:
        for number in range(100):
            subject = _sha(f'{ability}:{number}')
            cases.append(GroundTruthCase(
                case_id=f'{ability}.{number:03d}', ability=ability,
                genre=f'genre_{number // 25}',
                expected='PASS' if number % 25 < 12 else 'FAIL',
                subject_sha256=subject,
                evidence=[EvidenceRef(
                    artifact=f'private://{ability}/{number}',
                    subject_sha256=subject)],
            ))
    return GroundTruthManifest(
        manifest_version=1, dataset_id='private-v1', dataset_sha256=_sha('dataset'),
        rubric_sha256=_sha('rubric'), created_at='2026-08-16T00:00:00Z',
        annotators=2, adjudicator='reviewer-c', agreement=.9, cases=cases)


def _runs(manifest: GroundTruthManifest) -> list[EvaluationRun]:
    abilities = sorted({case.ability for case in manifest.cases})
    bindings = [CapabilityBinding(ability=ability, fingerprint={
        'dataset_sha256': manifest.dataset_sha256,
        'model': 'judge-v1',
        'prompt_sha256': _sha(f'prompt:{ability}'),
        'schema_sha256': _sha(f'schema:{ability}'),
        'decoder_sha256': _sha(f'decoder:{ability}'),
    }) for ability in abilities]
    runs = []
    for run_number in range(3):
        predictions = []
        for case in manifest.cases:
            evidence = [EvidenceRef(
                artifact=f'private://case/{case.case_id}',
                subject_sha256=case.subject_sha256)]
            predictions.append(EvaluationPrediction(
                case_id=case.case_id,
                judges=[JudgeDecision(
                    judge=f'A-{run_number}', verdict=case.expected,
                    evidence=evidence, reason='frozen rubric'),
                         JudgeDecision(
                    judge=f'B-{run_number}', verdict=case.expected,
                    evidence=evidence, reason='frozen rubric')],
            ))
        runs.append(EvaluationRun(
            run_version=1, run_id=f'run-{run_number}',
            evaluated_at=f'2026-08-16T00:00:0{run_number}Z',
            bindings=bindings, predictions=predictions))
    return runs


def test_profile_uses_worst_run_and_per_capability_fingerprints():
    manifest = _manifest()
    profile = build_profile(
        manifest, _runs(manifest), profile_id='profile-v1',
        calibrated_at='2026-08-16T01:00:00Z')
    assert profile.runs == 3
    assert all(row.enabled for row in profile.capabilities)
    assert len({row.fingerprint.prompt_sha256 for row in profile.capabilities}) == 8


def test_drift_or_incomplete_case_coverage_is_rejected():
    manifest = _manifest()
    runs = _runs(manifest)
    changed = runs[1].bindings[0].model_copy(update={
        'fingerprint': runs[1].bindings[0].fingerprint.model_copy(update={'model': 'judge-v2'})})
    runs[1] = runs[1].model_copy(update={'bindings': [changed, *runs[1].bindings[1:]]})
    with pytest.raises(ValueError, match='drifted'):
        build_profile(
            manifest, runs, profile_id='x', calibrated_at='2026-08-16T00:00:00Z')

    runs = _runs(manifest)
    runs[0] = runs[0].model_copy(update={'predictions': runs[0].predictions[:-1]})
    with pytest.raises(ValueError, match='coverage mismatch'):
        build_profile(
            manifest, runs, profile_id='x', calibrated_at='2026-08-16T00:00:00Z')


def test_disagreement_and_missing_evidence_disable_instead_of_passing():
    manifest = _manifest()
    runs = _runs(manifest)
    target = 'event_order'
    ids = {case.case_id for case in manifest.cases
           if case.ability == target and int(case.case_id.rsplit('.', 1)[1]) < 10}
    replaced = []
    for prediction in runs[1].predictions:
        if prediction.case_id in ids:
            first, second = prediction.judges
            second = second.model_copy(update={
                'verdict': 'FAIL' if first.verdict == 'PASS' else 'PASS',
                'evidence': [],
            })
            prediction = prediction.model_copy(update={'judges': [first, second]})
        replaced.append(prediction)
    runs[1] = runs[1].model_copy(update={'predictions': replaced})
    profile = build_profile(
        manifest, runs, profile_id='profile-v1',
        calibrated_at='2026-08-16T01:00:00Z')
    row = next(item for item in profile.capabilities if item.ability == target)
    assert row.rerun_consistency == .9
    assert row.enabled is False


def test_at_least_three_distinct_runs_are_required():
    manifest = _manifest()
    with pytest.raises(ValueError, match='at least three'):
        build_profile(
            manifest, _runs(manifest)[:2], profile_id='x',
            calibrated_at='2026-08-16T00:00:00Z')
