#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline, text-free ground-truth labelling workflow.

The workflow deliberately carries only stable case identities, hashes, and
artifact locators.  Source excerpts, prompts, and model responses remain in the
private corpus addressed by those locators and are never copied into a pack or
the final manifest.

Commands are restart-safe: an existing output is accepted only when its JSON
content is byte-semantically identical to what would be written now.  A
different existing output is never overwritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, Literal, Sequence

from pydantic import Field, ValidationError, model_validator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts.schema.base import StrictModel
from contracts.schema.evaluation import (
    Ability,
    EVALUATOR_ABILITIES,
    EvidenceRef,
    GroundTruthCase,
    GroundTruthManifest,
)


WORKFLOW_VERSION = 1
AGREEMENT_THRESHOLD = 0.8
HumanVerdict = Literal['PASS', 'FAIL', 'UNVER']


def _canonical(value: Any) -> bytes:
    if isinstance(value, StrictModel):
        value = value.model_dump(mode='json')
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _not_blank(value: str, field: str) -> str:
    if not value.strip():
        raise ValueError(f'{field} must not be blank')
    return value


class CandidateCase(StrictModel):
    """A case identity and private-corpus locator, never copied source text."""

    case_id: str = Field(pattern=r'^[a-z0-9][a-z0-9_.-]{7,127}$')
    ability: Ability
    genre: str = Field(min_length=1)
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    evidence: list[EvidenceRef] = Field(min_length=1)

    @model_validator(mode='after')
    def _genre_not_blank(self) -> 'CandidateCase':
        _not_blank(self.genre, 'genre')
        return self


class CandidateCatalog(StrictModel):
    workflow_version: Literal[1]
    dataset_id: str = Field(min_length=1)
    rubric_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    created_at: str = Field(min_length=1)
    cases: list[CandidateCase] = Field(min_length=1)

    @model_validator(mode='after')
    def _candidate_floor(self) -> 'CandidateCatalog':
        _not_blank(self.dataset_id, 'dataset_id')
        _not_blank(self.created_at, 'created_at')
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError('candidate case_id must be unique')
        for ability in EVALUATOR_ABILITIES:
            rows = [case for case in self.cases if case.ability == ability]
            if len(rows) < 100:
                raise ValueError(f'{ability}: need >=100 held-out candidates')
            genres: dict[str, int] = {}
            for case in rows:
                genres[case.genre] = genres.get(case.genre, 0) + 1
            eligible = [genre for genre, count in genres.items() if count >= 10]
            if len(eligible) < 4:
                raise ValueError(f'{ability}: need >=4 genres with >=10 candidates each')
        return self


class BlindCase(StrictModel):
    blind_id: str = Field(pattern=r'^blind_[0-9a-f]{24}$')
    ability: Ability
    genre: str = Field(min_length=1)
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    evidence: list[EvidenceRef] = Field(min_length=1)


class BlindPack(StrictModel):
    workflow_version: Literal[1]
    dataset_id: str = Field(min_length=1)
    pack_id: str = Field(pattern=r'^pack_[ab]_[0-9a-f]{16}$')
    annotator_slot: Literal['A', 'B']
    rubric_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    cases: list[BlindCase] = Field(min_length=1)


class IndexCase(CandidateCase):
    blind_a: str = Field(pattern=r'^blind_[0-9a-f]{24}$')
    blind_b: str = Field(pattern=r'^blind_[0-9a-f]{24}$')


class WorkflowIndex(StrictModel):
    workflow_version: Literal[1]
    dataset_id: str = Field(min_length=1)
    rubric_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    created_at: str = Field(min_length=1)
    catalog_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    seed_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    pack_a_id: str = Field(pattern=r'^pack_a_[0-9a-f]{16}$')
    pack_b_id: str = Field(pattern=r'^pack_b_[0-9a-f]{16}$')
    pack_a_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    pack_b_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    cases: list[IndexCase] = Field(min_length=1)

    @model_validator(mode='after')
    def _unique_mappings(self) -> 'WorkflowIndex':
        for field in ('case_id', 'blind_a', 'blind_b'):
            values = [getattr(case, field) for case in self.cases]
            if len(values) != len(set(values)):
                raise ValueError(f'workflow index {field} must be unique')
        return self


class HumanLabel(StrictModel):
    blind_id: str = Field(pattern=r'^blind_[0-9a-f]{24}$')
    verdict: HumanVerdict
    reason: str = Field(min_length=1)
    evidence: list[EvidenceRef]

    @model_validator(mode='after')
    def _reason_not_blank(self) -> 'HumanLabel':
        _not_blank(self.reason, 'label reason')
        return self


class LabelFile(StrictModel):
    workflow_version: Literal[1]
    dataset_id: str = Field(min_length=1)
    pack_id: str = Field(pattern=r'^pack_[ab]_[0-9a-f]{16}$')
    annotator: str = Field(min_length=1)
    labels: list[HumanLabel] = Field(min_length=1)

    @model_validator(mode='after')
    def _unique_labels(self) -> 'LabelFile':
        _not_blank(self.annotator, 'annotator')
        ids = [row.blind_id for row in self.labels]
        if len(ids) != len(set(ids)):
            raise ValueError('each blind_id must be labelled exactly once')
        return self


class AgreementReport(StrictModel):
    workflow_version: Literal[1]
    dataset_id: str = Field(min_length=1)
    metric: Literal['cohen_kappa']
    threshold: float = Field(ge=0.0, le=1.0)
    annotator_a: str = Field(min_length=1)
    annotator_b: str = Field(min_length=1)
    labels_a_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    labels_b_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    total: int = Field(ge=1)
    exact_agreements: int = Field(ge=0)
    raw_agreement: float = Field(ge=0.0, le=1.0)
    agreement: float = Field(ge=-1.0, le=1.0)
    passed: bool
    conflict_case_ids: list[str]
    conflict_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def _fail_closed_threshold(self) -> 'AgreementReport':
        if self.exact_agreements > self.total:
            raise ValueError('exact agreements cannot exceed total')
        if self.passed != (self.agreement >= self.threshold):
            raise ValueError('agreement passed flag does not match locked threshold')
        return self


class ConflictCase(StrictModel):
    case_id: str = Field(pattern=r'^[a-z0-9][a-z0-9_.-]{7,127}$')
    ability: Ability
    genre: str = Field(min_length=1)
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    evidence: list[EvidenceRef] = Field(min_length=1)
    annotator_a_verdict: HumanVerdict
    annotator_a_reason: str = Field(min_length=1)
    annotator_a_evidence: list[EvidenceRef]
    annotator_b_verdict: HumanVerdict
    annotator_b_reason: str = Field(min_length=1)
    annotator_b_evidence: list[EvidenceRef]


class ConflictPack(StrictModel):
    workflow_version: Literal[1]
    dataset_id: str = Field(min_length=1)
    agreement_metric: Literal['cohen_kappa']
    agreement: float = Field(ge=-1.0, le=1.0)
    threshold: float = Field(ge=0.0, le=1.0)
    status: Literal['ready_for_adjudication', 'rubric_relabel_required']
    conflict_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    conflicts: list[ConflictCase]


class AdjudicationDecision(StrictModel):
    case_id: str = Field(pattern=r'^[a-z0-9][a-z0-9_.-]{7,127}$')
    verdict: Literal['PASS', 'FAIL']
    reason: str = Field(min_length=1)
    evidence: list[EvidenceRef]

    @model_validator(mode='after')
    def _reason_not_blank(self) -> 'AdjudicationDecision':
        _not_blank(self.reason, 'adjudication reason')
        return self


class AdjudicationFile(StrictModel):
    workflow_version: Literal[1]
    dataset_id: str = Field(min_length=1)
    conflict_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    adjudicator: str = Field(min_length=1)
    decisions: list[AdjudicationDecision]

    @model_validator(mode='after')
    def _unique_decisions(self) -> 'AdjudicationFile':
        _not_blank(self.adjudicator, 'adjudicator')
        ids = [row.case_id for row in self.decisions]
        if len(ids) != len(set(ids)):
            raise ValueError('each conflict must be adjudicated exactly once')
        return self


def _blind_id(seed: str, slot: str, case_id: str) -> str:
    digest = hashlib.sha256(f'{seed}\0{slot}\0{case_id}'.encode('utf-8')).hexdigest()
    return f'blind_{digest[:24]}'


def _ordered(seed: str, slot: str, cases: Sequence[CandidateCase]) -> list[CandidateCase]:
    return sorted(
        cases,
        key=lambda row: hashlib.sha256(
            f'{seed}\0order\0{slot}\0{row.case_id}'.encode('utf-8'),
        ).digest(),
    )


def prepare(catalog: CandidateCatalog, *, seed: str) -> tuple[BlindPack, BlindPack, WorkflowIndex]:
    """Build two differently aliased and ordered packs from text-free locators."""
    _not_blank(seed, 'seed')
    catalog_sha = _sha256(catalog)
    pack_ids: dict[str, str] = {}
    for slot in ('A', 'B'):
        identity = hashlib.sha256(
            f'{catalog_sha}\0{slot}'.encode('utf-8'),
        ).hexdigest()[:16]
        pack_ids[slot] = f'pack_{slot.lower()}_{identity}'

    mappings: dict[str, tuple[str, str]] = {}
    for case in catalog.cases:
        mappings[case.case_id] = (
            _blind_id(seed, 'A', case.case_id),
            _blind_id(seed, 'B', case.case_id),
        )
    if len({value for pair in mappings.values() for value in pair}) != len(mappings) * 2:
        raise ValueError('blind alias collision; choose another seed')

    packs: dict[str, BlindPack] = {}
    for slot in ('A', 'B'):
        position = 0 if slot == 'A' else 1
        packs[slot] = BlindPack(
            workflow_version=WORKFLOW_VERSION,
            dataset_id=catalog.dataset_id,
            pack_id=pack_ids[slot],
            annotator_slot=slot,
            rubric_sha256=catalog.rubric_sha256,
            cases=[BlindCase(
                blind_id=mappings[row.case_id][position],
                ability=row.ability,
                genre=row.genre,
                subject_sha256=row.subject_sha256,
                evidence=row.evidence,
            ) for row in _ordered(seed, slot, catalog.cases)],
        )

    index = WorkflowIndex(
        workflow_version=WORKFLOW_VERSION,
        dataset_id=catalog.dataset_id,
        rubric_sha256=catalog.rubric_sha256,
        created_at=catalog.created_at,
        catalog_sha256=catalog_sha,
        seed_sha256=hashlib.sha256(seed.encode('utf-8')).hexdigest(),
        pack_a_id=packs['A'].pack_id,
        pack_b_id=packs['B'].pack_id,
        pack_a_sha256=_sha256(packs['A']),
        pack_b_sha256=_sha256(packs['B']),
        cases=[IndexCase(
            **case.model_dump(mode='python'),
            blind_a=mappings[case.case_id][0],
            blind_b=mappings[case.case_id][1],
        ) for case in sorted(catalog.cases, key=lambda row: row.case_id)],
    )
    return packs['A'], packs['B'], index


def _validate_label_set(
    index: WorkflowIndex,
    labels: LabelFile,
    *,
    slot: Literal['A', 'B'],
) -> dict[str, HumanLabel]:
    expected_pack = index.pack_a_id if slot == 'A' else index.pack_b_id
    if labels.dataset_id != index.dataset_id or labels.pack_id != expected_pack:
        raise ValueError(f'annotator {slot} labels are bound to another dataset or pack')
    key = 'blind_a' if slot == 'A' else 'blind_b'
    expected = {getattr(case, key) for case in index.cases}
    got = {row.blind_id for row in labels.labels}
    if got != expected:
        missing = sorted(expected - got)
        extra = sorted(got - expected)
        raise ValueError(
            f'annotator {slot} must label every case exactly once; '
            f'missing={missing[:3]!r} extra={extra[:3]!r}')
    return {row.blind_id: row for row in labels.labels}


def _cohen_kappa(a: Sequence[str], b: Sequence[str]) -> tuple[float, float, int]:
    if len(a) != len(b) or not a:
        raise ValueError('agreement needs equally sized, non-empty label vectors')
    labels = ('PASS', 'FAIL', 'UNVER')
    exact = sum(left == right for left, right in zip(a, b))
    observed = exact / len(a)
    expected = sum(
        (sum(value == label for value in a) / len(a))
        * (sum(value == label for value in b) / len(b))
        for label in labels
    )
    if math.isclose(expected, 1.0):
        kappa = 1.0 if math.isclose(observed, 1.0) else 0.0
    else:
        kappa = (observed - expected) / (1.0 - expected)
    return round(kappa, 6), round(observed, 6), exact


def reconcile(
    index: WorkflowIndex,
    labels_a: LabelFile,
    labels_b: LabelFile,
) -> tuple[AgreementReport, ConflictPack]:
    """Import two complete, independent label sets and expose every conflict."""
    if labels_a.annotator.strip().casefold() == labels_b.annotator.strip().casefold():
        raise ValueError('the two blind packs require two distinct self-reported annotators')
    by_a = _validate_label_set(index, labels_a, slot='A')
    by_b = _validate_label_set(index, labels_b, slot='B')

    vector_a: list[str] = []
    vector_b: list[str] = []
    conflicts: list[ConflictCase] = []
    for case in index.cases:
        left = by_a[case.blind_a]
        right = by_b[case.blind_b]
        vector_a.append(left.verdict)
        vector_b.append(right.verdict)
        # A shared PASS/FAIL is final.  Shared UNVER still needs adjudication.
        if left.verdict != right.verdict or left.verdict == 'UNVER':
            conflicts.append(ConflictCase(
                case_id=case.case_id,
                ability=case.ability,
                genre=case.genre,
                subject_sha256=case.subject_sha256,
                evidence=case.evidence,
                annotator_a_verdict=left.verdict,
                annotator_a_reason=left.reason,
                annotator_a_evidence=left.evidence,
                annotator_b_verdict=right.verdict,
                annotator_b_reason=right.reason,
                annotator_b_evidence=right.evidence,
            ))

    agreement, raw_agreement, exact = _cohen_kappa(vector_a, vector_b)
    conflict_ids = [row.case_id for row in conflicts]
    conflict_sha = _sha256(conflict_ids)
    passed = agreement >= AGREEMENT_THRESHOLD
    report = AgreementReport(
        workflow_version=WORKFLOW_VERSION,
        dataset_id=index.dataset_id,
        metric='cohen_kappa',
        threshold=AGREEMENT_THRESHOLD,
        annotator_a=labels_a.annotator,
        annotator_b=labels_b.annotator,
        labels_a_sha256=_sha256(labels_a),
        labels_b_sha256=_sha256(labels_b),
        total=len(index.cases),
        exact_agreements=exact,
        raw_agreement=raw_agreement,
        agreement=agreement,
        passed=passed,
        conflict_case_ids=conflict_ids,
        conflict_sha256=conflict_sha,
    )
    pack = ConflictPack(
        workflow_version=WORKFLOW_VERSION,
        dataset_id=index.dataset_id,
        agreement_metric='cohen_kappa',
        agreement=agreement,
        threshold=AGREEMENT_THRESHOLD,
        status='ready_for_adjudication' if passed else 'rubric_relabel_required',
        conflict_sha256=conflict_sha,
        conflicts=conflicts,
    )
    return report, pack


def _dedupe_evidence(refs: Sequence[EvidenceRef]) -> list[EvidenceRef]:
    out: list[EvidenceRef] = []
    seen: set[tuple[str, str, int | None, int | None]] = set()
    for ref in refs:
        key = (ref.artifact, ref.subject_sha256, ref.start, ref.end)
        if key not in seen:
            seen.add(key)
            out.append(ref)
    return out


def finalize(
    index: WorkflowIndex,
    labels_a: LabelFile,
    labels_b: LabelFile,
    persisted_report: AgreementReport,
    adjudications: AdjudicationFile,
) -> GroundTruthManifest:
    """Admit a manifest only after agreement and exactly-once adjudication gates."""
    report, conflict_pack = reconcile(index, labels_a, labels_b)
    if persisted_report != report:
        raise ValueError('agreement report no longer matches labels/index; reconcile again')
    if not report.passed:
        raise ValueError('agreement < 0.8; revise the rubric and independently relabel')
    if adjudications.dataset_id != index.dataset_id:
        raise ValueError('adjudication file is bound to another dataset')
    if adjudications.conflict_sha256 != conflict_pack.conflict_sha256:
        raise ValueError('adjudication file is stale for the current conflict set')
    if adjudications.adjudicator.strip().casefold() in {
        labels_a.annotator.strip().casefold(), labels_b.annotator.strip().casefold(),
    }:
        raise ValueError('adjudicator must be a third self-reported actor')

    expected_conflicts = set(report.conflict_case_ids)
    decisions = {row.case_id: row for row in adjudications.decisions}
    if set(decisions) != expected_conflicts:
        missing = sorted(expected_conflicts - set(decisions))
        extra = sorted(set(decisions) - expected_conflicts)
        raise ValueError(
            'every conflict must be adjudicated exactly once; '
            f'missing={missing[:3]!r} extra={extra[:3]!r}')

    by_a = _validate_label_set(index, labels_a, slot='A')
    by_b = _validate_label_set(index, labels_b, slot='B')
    cases: list[GroundTruthCase] = []
    for case in sorted(index.cases, key=lambda row: row.case_id):
        left = by_a[case.blind_a]
        right = by_b[case.blind_b]
        decision = decisions.get(case.case_id)
        if decision is None:
            if left.verdict != right.verdict or left.verdict == 'UNVER':
                raise AssertionError('unresolved conflict escaped exactly-once gate')
            verdict: Literal['PASS', 'FAIL'] = left.verdict
            evidence = _dedupe_evidence([*case.evidence, *left.evidence, *right.evidence])
        else:
            verdict = decision.verdict
            evidence = _dedupe_evidence([*case.evidence, *decision.evidence])
        cases.append(GroundTruthCase(
            case_id=case.case_id,
            ability=case.ability,
            genre=case.genre,
            expected=verdict,
            subject_sha256=case.subject_sha256,
            evidence=evidence,
        ))

    dataset_payload = {
        'dataset_id': index.dataset_id,
        'rubric_sha256': index.rubric_sha256,
        'cases': [case.model_dump(mode='json') for case in cases],
    }
    return GroundTruthManifest(
        manifest_version=1,
        dataset_id=index.dataset_id,
        dataset_sha256=_sha256(dataset_payload),
        rubric_sha256=index.rubric_sha256,
        created_at=index.created_at,
        annotators=2,
        adjudicator=adjudications.adjudicator,
        agreement=report.agreement,
        cases=cases,
    )


def validate_manifest(manifest: GroundTruthManifest) -> None:
    """Recompute the documented dataset identity in addition to schema checks."""
    payload = {
        'dataset_id': manifest.dataset_id,
        'rubric_sha256': manifest.rubric_sha256,
        'cases': [case.model_dump(mode='json') for case in manifest.cases],
    }
    actual = _sha256(payload)
    if actual != manifest.dataset_sha256:
        raise ValueError(
            f'dataset_sha256 mismatch: manifest={manifest.dataset_sha256} actual={actual}')


def _load(path: Path, model: type[StrictModel]) -> Any:
    return model.model_validate(json.loads(path.read_text(encoding='utf-8')))


def _write_once(path: Path, value: StrictModel) -> None:
    """Atomic and resumable, without silently overwriting a prior human state."""
    payload = value.model_dump(mode='json')
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    if path.exists():
        current = json.loads(path.read_text(encoding='utf-8'))
        if _canonical(current) == _canonical(payload):
            return
        raise FileExistsError(f'refusing to overwrite different workflow output: {path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    temporary.write_text(rendered, encoding='utf-8')
    temporary.replace(path)


def _print_summary(**values: Any) -> None:
    print(json.dumps(values, ensure_ascii=False, sort_keys=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)

    prepare_parser = commands.add_parser('prepare', help='generate two deterministic blind packs')
    prepare_parser.add_argument('--catalog', required=True, type=Path)
    prepare_parser.add_argument('--out-dir', required=True, type=Path)
    prepare_parser.add_argument('--seed', required=True)

    reconcile_parser = commands.add_parser('reconcile', help='import two independent label files')
    reconcile_parser.add_argument('--index', required=True, type=Path)
    reconcile_parser.add_argument('--labels-a', required=True, type=Path)
    reconcile_parser.add_argument('--labels-b', required=True, type=Path)
    reconcile_parser.add_argument('--report', required=True, type=Path)
    reconcile_parser.add_argument('--conflicts', required=True, type=Path)

    finalize_parser = commands.add_parser('finalize', help='adjudicate conflicts and emit manifest')
    finalize_parser.add_argument('--index', required=True, type=Path)
    finalize_parser.add_argument('--labels-a', required=True, type=Path)
    finalize_parser.add_argument('--labels-b', required=True, type=Path)
    finalize_parser.add_argument('--report', required=True, type=Path)
    finalize_parser.add_argument('--adjudications', required=True, type=Path)
    finalize_parser.add_argument('--out-manifest', required=True, type=Path)

    validate_parser = commands.add_parser('validate', help='validate a text-free final manifest')
    validate_parser.add_argument('--manifest', required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == 'prepare':
            catalog = _load(args.catalog, CandidateCatalog)
            pack_a, pack_b, index = prepare(catalog, seed=args.seed)
            _write_once(args.out_dir / 'blind_a.json', pack_a)
            _write_once(args.out_dir / 'blind_b.json', pack_b)
            _write_once(args.out_dir / 'index.json', index)
            _print_summary(
                status='prepared', cases=len(index.cases),
                blind_a=str(args.out_dir / 'blind_a.json'),
                blind_b=str(args.out_dir / 'blind_b.json'),
                index=str(args.out_dir / 'index.json'),
            )
            return 0
        if args.command == 'reconcile':
            index = _load(args.index, WorkflowIndex)
            labels_a = _load(args.labels_a, LabelFile)
            labels_b = _load(args.labels_b, LabelFile)
            report, conflicts = reconcile(index, labels_a, labels_b)
            _write_once(args.report, report)
            _write_once(args.conflicts, conflicts)
            _print_summary(
                status=conflicts.status, agreement=report.agreement,
                conflicts=len(conflicts.conflicts), report=str(args.report),
            )
            return 0 if report.passed else 2
        if args.command == 'finalize':
            manifest = finalize(
                _load(args.index, WorkflowIndex),
                _load(args.labels_a, LabelFile),
                _load(args.labels_b, LabelFile),
                _load(args.report, AgreementReport),
                _load(args.adjudications, AdjudicationFile),
            )
            _write_once(args.out_manifest, manifest)
            _print_summary(
                status='finalized', cases=len(manifest.cases),
                dataset_sha256=manifest.dataset_sha256,
                manifest=str(args.out_manifest),
            )
            return 0
        manifest = _load(args.manifest, GroundTruthManifest)
        validate_manifest(manifest)
        _print_summary(
            status='valid', dataset_id=manifest.dataset_id,
            cases=len(manifest.cases), agreement=manifest.agreement,
        )
        return 0
    except (OSError, ValueError, ValidationError, json.JSONDecodeError) as exc:
        print(json.dumps({
            'status': 'blocked', 'error': f'{type(exc).__name__}: {exc}',
        }, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
