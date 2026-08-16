#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline, text-free validation for cross-genre style-profile fields.

The protocol (including every threshold and field kind) is frozen separately
from the private per-book summaries.  The resulting manifest contains only
opaque book identities, source hashes, genres, aggregate counts/metrics and a
``diagnostic``/``hard`` eligibility decision.  It never calls a model or the
network and refuses JSON keys commonly used to carry source/model text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


SCHEMA_VERSION = 1
DIMENSIONS = (
    'transferability',
    'position_coverage',
    'ab_discrimination',
    'material_partition',
)
Z_95 = 1.959963984540054
_FORBIDDEN_KEY_TOKENS = frozenset({'excerpt', 'prompt', 'response', 'text'})
_ID_RE = r'^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$'
_FIELD_RE = r'^[a-z][a-z0-9_.-]{1,63}$'
_SHA256_RE = r'^[0-9a-f]{64}$'


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Thresholds(StrictModel):
    """All decision thresholds; validation refuses permissive structural floors."""

    confidence: Literal[0.95]
    min_books: int = Field(ge=6)
    min_genres: int = Field(ge=4)
    min_book_trials: int = Field(ge=1)
    min_training_trials: int = Field(ge=1)
    min_genre_trials: int = Field(ge=1)
    # A field called ``hard`` must satisfy the same 0.90 lower-bound floor as
    # the calibrated semantic evaluator; a protocol may tighten, not weaken it.
    min_wilson_lower: float = Field(ge=0.9, le=1.0)
    max_holdout_rate_drop: float = Field(ge=0.0, le=0.05)


class FieldPolicy(StrictModel):
    field_id: str = Field(pattern=_FIELD_RE)
    kind: Literal['semantic', 'deterministic']


class ValidationProtocol(StrictModel):
    schema_version: Literal[1]
    protocol_id: str = Field(pattern=_ID_RE)
    declared_at: str = Field(
        pattern=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$',
    )
    thresholds_locked_before_evaluation: Literal[True]
    thresholds: Thresholds
    fields: list[FieldPolicy] = Field(min_length=1)

    @model_validator(mode='after')
    def _unique_fields(self) -> 'ValidationProtocol':
        ids = [row.field_id for row in self.fields]
        if len(ids) != len(set(ids)):
            raise ValueError('protocol field_id must be unique')
        return self


class TrialCount(StrictModel):
    passed: int = Field(ge=0)
    total: int = Field(ge=1)

    @model_validator(mode='after')
    def _passed_within_total(self) -> 'TrialCount':
        if self.passed > self.total:
            raise ValueError('passed cannot exceed total')
        return self


class FieldMetrics(StrictModel):
    transferability: TrialCount
    position_coverage: TrialCount
    ab_discrimination: TrialCount
    material_partition: TrialCount


class BookFieldSummary(StrictModel):
    field_id: str = Field(pattern=_FIELD_RE)
    metrics: FieldMetrics


class BookSummary(StrictModel):
    schema_version: Literal[1]
    sid: str = Field(pattern=_ID_RE)
    source_sha256: str = Field(pattern=_SHA256_RE)
    genre: str = Field(min_length=1, max_length=64)
    fields: list[BookFieldSummary] = Field(min_length=1)

    @model_validator(mode='after')
    def _identity_and_fields(self) -> 'BookSummary':
        if not self.genre.strip() or '\n' in self.genre or '\r' in self.genre:
            raise ValueError('genre must be a non-blank single-line label')
        ids = [row.field_id for row in self.fields]
        if len(ids) != len(set(ids)):
            raise ValueError(f'{self.sid}: field_id must be unique')
        return self


class MetricResult(StrictModel):
    passed: int
    total: int
    rate: float
    wilson_lower_95: float
    enough_trials: bool
    lower_bound_passed: bool


class BookFieldResult(StrictModel):
    field_id: str
    kind: Literal['semantic', 'deterministic']
    eligibility: Literal['diagnostic', 'hard']
    metrics: dict[str, MetricResult]


class BookResult(StrictModel):
    sid: str
    source_sha256: str
    genre: str
    fields: list[BookFieldResult]


class HoldoutMetricResult(StrictModel):
    training: MetricResult
    held_out: MetricResult
    held_out_rate_drop: float
    passed: bool


class HoldoutResult(StrictModel):
    sid: str
    source_sha256: str
    genre: str
    metrics: dict[str, HoldoutMetricResult]
    passed: bool


class GenreResult(StrictModel):
    genre: str
    books: int
    metrics: dict[str, MetricResult]
    passed: bool


class FieldDecision(StrictModel):
    field_id: str
    kind: Literal['semantic', 'deterministic']
    eligibility: Literal['diagnostic', 'hard']
    reasons: list[str]
    leave_one_book: list[HoldoutResult]
    cross_genre: list[GenreResult]


class ValidationManifest(StrictModel):
    schema_version: Literal[1]
    protocol_id: str
    protocol_sha256: str = Field(pattern=_SHA256_RE)
    input_sha256: str = Field(pattern=_SHA256_RE)
    thresholds: Thresholds
    books: list[BookResult]
    fields: list[FieldDecision]
    output_sha256: str = Field(pattern=_SHA256_RE)


def _canonical(value: Any) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode='json')
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _reject_private_keys(value: Any, *, at: str = '$') -> None:
    """Reject likely text-bearing fields before schema validation can discard them."""
    if isinstance(value, dict):
        for key, child in value.items():
            tokens = {
                token for token in re.split(r'[^a-z0-9]+', str(key).casefold()) if token
            }
            leaked = sorted(tokens & _FORBIDDEN_KEY_TOKENS)
            if leaked:
                raise ValueError(
                    f'private text-bearing key refused at {at}.{key}: {leaked[0]}')
            _reject_private_keys(child, at=f'{at}.{key}')
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_private_keys(child, at=f'{at}[{index}]')


def load_json(path: Path, model: type[StrictModel]) -> Any:
    raw = json.loads(path.read_text(encoding='utf-8'))
    _reject_private_keys(raw)
    return model.model_validate(raw)


def wilson_lower(passed: int, total: int) -> float:
    """Unrounded lower end of the predeclared two-sided 95% Wilson interval."""
    if total <= 0:
        raise ValueError('Wilson interval requires total > 0')
    rate = passed / total
    z2 = Z_95 * Z_95
    centre = rate + z2 / (2 * total)
    radius = Z_95 * math.sqrt(
        rate * (1 - rate) / total + z2 / (4 * total * total))
    return (centre - radius) / (1 + z2 / total)


def _metric_result(
    counts: TrialCount,
    *,
    min_trials: int,
    min_lower: float,
) -> MetricResult:
    lower = wilson_lower(counts.passed, counts.total)
    return MetricResult(
        passed=counts.passed,
        total=counts.total,
        rate=round(counts.passed / counts.total, 6),
        wilson_lower_95=round(lower, 6),
        enough_trials=counts.total >= min_trials,
        lower_bound_passed=lower >= min_lower,
    )


def _sum_counts(rows: Sequence[TrialCount]) -> TrialCount:
    return TrialCount(
        passed=sum(row.passed for row in rows),
        total=sum(row.total for row in rows),
    )


def _book_field_map(book: BookSummary) -> dict[str, BookFieldSummary]:
    return {row.field_id: row for row in book.fields}


def _counts(row: BookFieldSummary, dimension: str) -> TrialCount:
    return getattr(row.metrics, dimension)


def _validate_inputs(
    protocol: ValidationProtocol,
    books: Sequence[BookSummary],
) -> list[BookSummary]:
    ordered = sorted(books, key=lambda row: row.sid)
    if len(ordered) < protocol.thresholds.min_books:
        raise ValueError(
            f'need >= {protocol.thresholds.min_books} books; got {len(ordered)}')
    sids = [row.sid for row in ordered]
    if len(sids) != len(set(sids)):
        raise ValueError('sid must be unique')
    source_hashes = [row.source_sha256 for row in ordered]
    if len(source_hashes) != len(set(source_hashes)):
        raise ValueError('source_sha256 must be unique across books')
    genres = {row.genre for row in ordered}
    if len(genres) < protocol.thresholds.min_genres:
        raise ValueError(
            f'need >= {protocol.thresholds.min_genres} genres; got {len(genres)}')
    expected = {row.field_id for row in protocol.fields}
    for book in ordered:
        got = set(_book_field_map(book))
        if got != expected:
            raise ValueError(
                f'{book.sid}: fields must exactly match frozen protocol; '
                f'missing={sorted(expected - got)!r} extra={sorted(got - expected)!r}')
    return ordered


def _field_decision(
    policy: FieldPolicy,
    books: Sequence[BookSummary],
    thresholds: Thresholds,
) -> FieldDecision:
    book_rows = [(book, _book_field_map(book)[policy.field_id]) for book in books]
    holdouts: list[HoldoutResult] = []
    for held_book, held_row in book_rows:
        training_rows = [row for book, row in book_rows if book.sid != held_book.sid]
        metrics: dict[str, HoldoutMetricResult] = {}
        for dimension in DIMENSIONS:
            training_counts = _sum_counts([
                _counts(row, dimension) for row in training_rows
            ])
            held_counts = _counts(held_row, dimension)
            training = _metric_result(
                training_counts,
                min_trials=thresholds.min_training_trials,
                min_lower=thresholds.min_wilson_lower,
            )
            held_out = _metric_result(
                held_counts,
                min_trials=thresholds.min_book_trials,
                min_lower=thresholds.min_wilson_lower,
            )
            raw_drop = (
                training_counts.passed / training_counts.total
                - held_counts.passed / held_counts.total
            )
            drop = max(0.0, raw_drop)
            passed = (
                training.enough_trials
                and training.lower_bound_passed
                and held_out.enough_trials
                and held_out.lower_bound_passed
                and drop <= thresholds.max_holdout_rate_drop
            )
            metrics[dimension] = HoldoutMetricResult(
                training=training,
                held_out=held_out,
                held_out_rate_drop=round(drop, 6),
                passed=passed,
            )
        holdouts.append(HoldoutResult(
            sid=held_book.sid,
            source_sha256=held_book.source_sha256,
            genre=held_book.genre,
            metrics=metrics,
            passed=all(metric.passed for metric in metrics.values()),
        ))

    genre_results: list[GenreResult] = []
    for genre in sorted({book.genre for book in books}):
        chosen = [row for book, row in book_rows if book.genre == genre]
        metrics = {
            dimension: _metric_result(
                _sum_counts([_counts(row, dimension) for row in chosen]),
                min_trials=thresholds.min_genre_trials,
                min_lower=thresholds.min_wilson_lower,
            )
            for dimension in DIMENSIONS
        }
        genre_results.append(GenreResult(
            genre=genre,
            books=len(chosen),
            metrics=metrics,
            passed=all(
                result.enough_trials and result.lower_bound_passed
                for result in metrics.values()
            ),
        ))

    reasons: list[str] = []
    if policy.kind == 'deterministic':
        reasons.append('deterministic_metric_is_diagnostic_only')
    failed_holdouts = [row.sid for row in holdouts if not row.passed]
    if failed_holdouts:
        reasons.append('leave_one_book_failed:' + ','.join(failed_holdouts))
    failed_genres = [row.genre for row in genre_results if not row.passed]
    if failed_genres:
        reasons.append('cross_genre_failed:' + ','.join(failed_genres))
    eligibility: Literal['diagnostic', 'hard'] = (
        'hard' if policy.kind == 'semantic' and not reasons else 'diagnostic'
    )
    return FieldDecision(
        field_id=policy.field_id,
        kind=policy.kind,
        eligibility=eligibility,
        reasons=reasons,
        leave_one_book=holdouts,
        cross_genre=genre_results,
    )


def evaluate(
    protocol: ValidationProtocol,
    books: Sequence[BookSummary],
) -> ValidationManifest:
    ordered_books = _validate_inputs(protocol, books)
    policies = sorted(protocol.fields, key=lambda row: row.field_id)
    decisions = [
        _field_decision(policy, ordered_books, protocol.thresholds)
        for policy in policies
    ]
    eligibility = {row.field_id: row.eligibility for row in decisions}
    kind = {row.field_id: row.kind for row in policies}
    book_results: list[BookResult] = []
    for book in ordered_books:
        field_map = _book_field_map(book)
        fields: list[BookFieldResult] = []
        for policy in policies:
            row = field_map[policy.field_id]
            metrics = {
                dimension: _metric_result(
                    _counts(row, dimension),
                    min_trials=protocol.thresholds.min_book_trials,
                    min_lower=protocol.thresholds.min_wilson_lower,
                )
                for dimension in DIMENSIONS
            }
            fields.append(BookFieldResult(
                field_id=policy.field_id,
                kind=kind[policy.field_id],
                eligibility=eligibility[policy.field_id],
                metrics=metrics,
            ))
        book_results.append(BookResult(
            sid=book.sid,
            source_sha256=book.source_sha256,
            genre=book.genre,
            fields=fields,
        ))

    input_payload = {
        'books': [book.model_dump(mode='json') for book in ordered_books],
    }
    payload: dict[str, Any] = {
        'schema_version': SCHEMA_VERSION,
        'protocol_id': protocol.protocol_id,
        'protocol_sha256': _sha256(protocol),
        'input_sha256': _sha256(input_payload),
        'thresholds': protocol.thresholds.model_dump(mode='json'),
        'books': [row.model_dump(mode='json') for row in book_results],
        'fields': [row.model_dump(mode='json') for row in decisions],
    }
    payload['output_sha256'] = _sha256(payload)
    return ValidationManifest.model_validate(payload)


def validate_manifest(
    protocol: ValidationProtocol,
    books: Sequence[BookSummary],
    manifest: ValidationManifest,
) -> None:
    payload = manifest.model_dump(mode='json', exclude={'output_sha256'})
    actual_output = _sha256(payload)
    if actual_output != manifest.output_sha256:
        raise ValueError(
            f'output_sha256 mismatch: manifest={manifest.output_sha256} '
            f'actual={actual_output}')
    expected = evaluate(protocol, books)
    if expected != manifest:
        raise ValueError('manifest does not match the frozen protocol and input summaries')


def _write_once(path: Path, manifest: ValidationManifest) -> None:
    rendered = json.dumps(
        manifest.model_dump(mode='json'),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + '\n'
    if path.exists():
        current = json.loads(path.read_text(encoding='utf-8'))
        if _canonical(current) == _canonical(manifest):
            return
        raise FileExistsError(f'refusing to overwrite different manifest: {path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    temporary.write_text(rendered, encoding='utf-8')
    temporary.replace(path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name, help_text in (
        ('evaluate', 'evaluate private per-book aggregate summaries'),
        ('validate', 'recompute and verify an existing text-free manifest'),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument('--protocol', required=True, type=Path)
        command.add_argument('--summary', required=True, type=Path, action='append')
        command.add_argument('--manifest', required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        protocol = load_json(args.protocol, ValidationProtocol)
        books = [load_json(path, BookSummary) for path in args.summary]
        if args.command == 'evaluate':
            manifest = evaluate(protocol, books)
            _write_once(args.manifest, manifest)
            print(json.dumps({
                'status': 'evaluated',
                'books': len(manifest.books),
                'genres': len({row.genre for row in manifest.books}),
                'hard_fields': sum(row.eligibility == 'hard' for row in manifest.fields),
                'diagnostic_fields': sum(
                    row.eligibility == 'diagnostic' for row in manifest.fields),
                'input_sha256': manifest.input_sha256,
                'output_sha256': manifest.output_sha256,
            }, ensure_ascii=False, sort_keys=True))
            return 0
        manifest = load_json(args.manifest, ValidationManifest)
        validate_manifest(protocol, books, manifest)
        print(json.dumps({
            'status': 'valid',
            'input_sha256': manifest.input_sha256,
            'output_sha256': manifest.output_sha256,
        }, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, ValidationError, json.JSONDecodeError) as exc:
        print(json.dumps({
            'status': 'blocked',
            'error': f'{type(exc).__name__}: {exc}',
        }, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
