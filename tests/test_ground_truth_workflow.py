from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from contracts.schema.evaluation import EVALUATOR_ABILITIES, EvidenceRef
from tools.ground_truth_workflow import (
    AdjudicationDecision,
    AdjudicationFile,
    CandidateCase,
    CandidateCatalog,
    HumanLabel,
    LabelFile,
    finalize,
    main,
    prepare,
    reconcile,
    validate_manifest,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _catalog(
    *,
    cases_per_ability: int = 100,
    abilities=EVALUATOR_ABILITIES,
) -> CandidateCatalog:
    cases = []
    for ability in abilities:
        for number in range(cases_per_ability):
            subject = _hash(f'{ability}:{number}')
            cases.append(CandidateCase(
                case_id=f'{ability}.case.{number:03d}',
                ability=ability,
                genre=f'genre_{number % 4}',
                subject_sha256=subject,
                evidence=[EvidenceRef(
                    artifact=f'private://held-out/{ability}/{number:03d}',
                    subject_sha256=subject,
                    start=0,
                    end=10,
                )],
            ))
    return CandidateCatalog(
        workflow_version=1,
        dataset_id='held-out-v1',
        rubric_sha256=_hash('locked rubric v1'),
        created_at='2026-08-16T00:00:00Z',
        cases=cases,
    )


def _expected(case_id: str) -> str:
    # Four genres each receive 12 PASS and 13 FAIL cases per ability.
    number = int(case_id.rsplit('.', 1)[1])
    return 'PASS' if number // 4 < 12 else 'FAIL'


def _label_file(index, slot: str, actor: str, *, flip: set[str] | None = None):
    flip = flip or set()
    pack_id = index.pack_a_id if slot == 'A' else index.pack_b_id
    alias = 'blind_a' if slot == 'A' else 'blind_b'
    labels = []
    for case in index.cases:
        verdict = _expected(case.case_id)
        if case.case_id in flip:
            verdict = 'FAIL' if verdict == 'PASS' else 'PASS'
        labels.append(HumanLabel(
            blind_id=getattr(case, alias),
            verdict=verdict,
            reason='按冻结口径独立核对',
            evidence=[],
        ))
    return LabelFile(
        workflow_version=1,
        dataset_id=index.dataset_id,
        pack_id=pack_id,
        annotator=actor,
        labels=labels,
    )


def _assert_no_private_payload(value):
    forbidden = {'text', 'prompt', 'response', 'expected', 'raw'}
    if isinstance(value, dict):
        assert forbidden.isdisjoint(value)
        for child in value.values():
            _assert_no_private_payload(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_private_payload(child)


def test_prepare_is_deterministic_differently_blinded_and_text_free(tmp_path):
    catalog = _catalog()
    first_a, first_b, first_index = prepare(catalog, seed='fixed-seed-v1')
    second_a, second_b, second_index = prepare(catalog, seed='fixed-seed-v1')

    assert first_a == second_a
    assert first_b == second_b
    assert first_index == second_index
    assert [row.blind_id for row in first_a.cases] != [row.blind_id for row in first_b.cases]
    assert [row.subject_sha256 for row in first_a.cases] != [
        row.subject_sha256 for row in first_b.cases]
    assert {row.blind_id for row in first_a.cases}.isdisjoint(
        row.blind_id for row in first_b.cases)
    _assert_no_private_payload(first_a.model_dump(mode='json'))
    _assert_no_private_payload(first_b.model_dump(mode='json'))
    _assert_no_private_payload(first_index.model_dump(mode='json'))

    catalog_path = tmp_path / 'catalog.json'
    catalog_path.write_text(catalog.model_dump_json(indent=2), encoding='utf-8')
    out = tmp_path / 'blind'
    assert main([
        'prepare', '--catalog', str(catalog_path), '--out-dir', str(out),
        '--seed', 'fixed-seed-v1',
    ]) == 0
    # Identical reruns are resumable; a changed seed may not overwrite state.
    assert main([
        'prepare', '--catalog', str(catalog_path), '--out-dir', str(out),
        '--seed', 'fixed-seed-v1',
    ]) == 0
    assert main([
        'prepare', '--catalog', str(catalog_path), '--out-dir', str(out),
        '--seed', 'changed-seed',
    ]) == 2


def test_two_independent_labels_finalize_a_valid_manifest():
    _, _, index = prepare(_catalog(), seed='fixed-seed-v1')
    labels_a = _label_file(index, 'A', 'reviewer-a')
    labels_b = _label_file(index, 'B', 'reviewer-b')
    report, conflicts = reconcile(index, labels_a, labels_b)

    assert report.metric == 'cohen_kappa'
    assert report.agreement == 1
    assert report.passed is True
    assert conflicts.conflicts == []
    adjudication = AdjudicationFile(
        workflow_version=1,
        dataset_id=index.dataset_id,
        conflict_sha256=report.conflict_sha256,
        adjudicator='reviewer-c',
        decisions=[],
    )
    manifest = finalize(index, labels_a, labels_b, report, adjudication)
    validate_manifest(manifest)
    assert len(manifest.cases) == 800
    assert {row.expected for row in manifest.cases} == {'PASS', 'FAIL'}
    dumped = manifest.model_dump(mode='json')
    assert '按冻结口径独立核对' not in json.dumps(dumped, ensure_ascii=False)


def test_one_ability_catalog_reaches_the_official_prepare_and_finalize_path(tmp_path):
    catalog = _catalog(abilities=('secret_timeline',))
    catalog_path = tmp_path / 'catalog.json'
    catalog_path.write_text(catalog.model_dump_json(indent=2), encoding='utf-8')
    out = tmp_path / 'blind'

    assert main([
        'prepare', '--catalog', str(catalog_path), '--out-dir', str(out),
        '--seed', 'partial-secret-timeline-v1',
    ]) == 0
    index_payload = json.loads((out / 'index.json').read_text(encoding='utf-8'))
    _, _, index = prepare(catalog, seed='partial-secret-timeline-v1')
    assert index.model_dump(mode='json') == index_payload

    labels_a = _label_file(index, 'A', 'reviewer-a')
    labels_b = _label_file(index, 'B', 'reviewer-b')
    report, conflicts = reconcile(index, labels_a, labels_b)
    assert conflicts.conflicts == []
    manifest = finalize(index, labels_a, labels_b, report, AdjudicationFile(
        workflow_version=1,
        dataset_id=index.dataset_id,
        conflict_sha256=report.conflict_sha256,
        adjudicator='reviewer-c',
        decisions=[],
    ))
    validate_manifest(manifest)
    assert len(manifest.cases) == 100
    assert {case.ability for case in manifest.cases} == {'secret_timeline'}


def test_reconcile_finalize_and_validate_cli_wiring(tmp_path):
    _, _, index = prepare(_catalog(), seed='fixed-seed-v1')
    labels_a = _label_file(index, 'A', 'reviewer-a')
    labels_b = _label_file(index, 'B', 'reviewer-b')
    report, _ = reconcile(index, labels_a, labels_b)
    files = {
        'index': index,
        'labels_a': labels_a,
        'labels_b': labels_b,
    }
    for name, model in files.items():
        (tmp_path / f'{name}.json').write_text(
            model.model_dump_json(indent=2), encoding='utf-8')

    report_path = tmp_path / 'agreement.json'
    conflicts_path = tmp_path / 'conflicts.json'
    assert main([
        'reconcile', '--index', str(tmp_path / 'index.json'),
        '--labels-a', str(tmp_path / 'labels_a.json'),
        '--labels-b', str(tmp_path / 'labels_b.json'),
        '--report', str(report_path), '--conflicts', str(conflicts_path),
    ]) == 0

    adjudication = AdjudicationFile(
        workflow_version=1,
        dataset_id=index.dataset_id,
        conflict_sha256=report.conflict_sha256,
        adjudicator='reviewer-c',
        decisions=[],
    )
    adjudication_path = tmp_path / 'adjudications.json'
    adjudication_path.write_text(adjudication.model_dump_json(indent=2), encoding='utf-8')
    manifest_path = tmp_path / 'manifest.json'
    assert main([
        'finalize', '--index', str(tmp_path / 'index.json'),
        '--labels-a', str(tmp_path / 'labels_a.json'),
        '--labels-b', str(tmp_path / 'labels_b.json'),
        '--report', str(report_path), '--adjudications', str(adjudication_path),
        '--out-manifest', str(manifest_path),
    ]) == 0
    assert main(['validate', '--manifest', str(manifest_path)]) == 0


def test_low_agreement_fails_closed_even_after_conflicts_are_resolved():
    _, _, index = prepare(_catalog(), seed='fixed-seed-v1')
    labels_a = _label_file(index, 'A', 'reviewer-a')
    flips = {case.case_id for case in index.cases[:200]}
    labels_b = _label_file(index, 'B', 'reviewer-b', flip=flips)
    report, conflicts = reconcile(index, labels_a, labels_b)

    assert report.agreement < .8
    assert report.passed is False
    assert conflicts.status == 'rubric_relabel_required'
    decisions = [AdjudicationDecision(
        case_id=case.case_id,
        verdict=_expected(case.case_id),
        reason='第三方按冻结口径仲裁',
        evidence=[],
    ) for case in conflicts.conflicts]
    adjudication = AdjudicationFile(
        workflow_version=1,
        dataset_id=index.dataset_id,
        conflict_sha256=report.conflict_sha256,
        adjudicator='reviewer-c',
        decisions=decisions,
    )
    with pytest.raises(ValueError, match='agreement < 0.8'):
        finalize(index, labels_a, labels_b, report, adjudication)


def test_high_agreement_requires_every_conflict_exactly_once():
    _, _, index = prepare(_catalog(), seed='fixed-seed-v1')
    labels_a = _label_file(index, 'A', 'reviewer-a')
    flips = {case.case_id for case in index.cases[:20]}
    labels_b = _label_file(index, 'B', 'reviewer-b', flip=flips)
    report, conflicts = reconcile(index, labels_a, labels_b)
    assert report.agreement >= .8
    assert len(conflicts.conflicts) == 20

    missing = AdjudicationFile(
        workflow_version=1,
        dataset_id=index.dataset_id,
        conflict_sha256=report.conflict_sha256,
        adjudicator='reviewer-c',
        decisions=[],
    )
    with pytest.raises(ValueError, match='exactly once'):
        finalize(index, labels_a, labels_b, report, missing)

    complete = AdjudicationFile(
        workflow_version=1,
        dataset_id=index.dataset_id,
        conflict_sha256=report.conflict_sha256,
        adjudicator='reviewer-c',
        decisions=[AdjudicationDecision(
            case_id=case.case_id,
            verdict=_expected(case.case_id),
            reason='第三方按冻结口径仲裁',
            evidence=[],
        ) for case in conflicts.conflicts],
    )
    manifest = finalize(index, labels_a, labels_b, report, complete)
    validate_manifest(manifest)


def test_label_sets_must_be_complete_and_independent():
    _, _, index = prepare(_catalog(), seed='fixed-seed-v1')
    labels_a = _label_file(index, 'A', 'same-actor')
    labels_b = _label_file(index, 'B', 'same-actor')
    with pytest.raises(ValueError, match='distinct'):
        reconcile(index, labels_a, labels_b)

    labels_b = _label_file(index, 'B', 'reviewer-b')
    incomplete = labels_b.model_copy(update={'labels': labels_b.labels[:-1]})
    with pytest.raises(ValueError, match='every case exactly once'):
        reconcile(index, labels_a, incomplete)


def test_candidate_and_final_distribution_floors_are_hard_gates():
    with pytest.raises(ValidationError, match='need >=100'):
        _catalog(cases_per_ability=99)

    _, _, index = prepare(_catalog(), seed='fixed-seed-v1')
    all_pass_a = _label_file(index, 'A', 'reviewer-a')
    all_pass_b = _label_file(index, 'B', 'reviewer-b')
    for row in all_pass_a.labels:
        row.verdict = 'PASS'
    for row in all_pass_b.labels:
        row.verdict = 'PASS'
    report, _ = reconcile(index, all_pass_a, all_pass_b)
    adjudication = AdjudicationFile(
        workflow_version=1,
        dataset_id=index.dataset_id,
        conflict_sha256=report.conflict_sha256,
        adjudicator='reviewer-c',
        decisions=[],
    )
    with pytest.raises(ValidationError, match='PASS/FAIL each >=40'):
        finalize(index, all_pass_a, all_pass_b, report, adjudication)


def test_validate_rejects_tampered_dataset_identity():
    _, _, index = prepare(_catalog(), seed='fixed-seed-v1')
    labels_a = _label_file(index, 'A', 'reviewer-a')
    labels_b = _label_file(index, 'B', 'reviewer-b')
    report, _ = reconcile(index, labels_a, labels_b)
    manifest = finalize(index, labels_a, labels_b, report, AdjudicationFile(
        workflow_version=1,
        dataset_id=index.dataset_id,
        conflict_sha256=report.conflict_sha256,
        adjudicator='reviewer-c',
        decisions=[],
    ))
    tampered = manifest.model_copy(update={'dataset_sha256': '0' * 64})
    with pytest.raises(ValueError, match='dataset_sha256 mismatch'):
        validate_manifest(tampered)
