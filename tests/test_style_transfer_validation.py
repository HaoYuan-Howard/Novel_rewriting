#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TODO-24: private six-book style transferability stays text-free and fail-closed."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.validate_style_transfer import (
    BookSummary,
    ValidationManifest,
    ValidationProtocol,
    evaluate,
    load_json,
    validate_manifest,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _protocol() -> ValidationProtocol:
    return ValidationProtocol.model_validate({
        'schema_version': 1,
        'protocol_id': 'style-transfer-v1',
        'declared_at': '2026-08-16T00:00:00Z',
        'thresholds_locked_before_evaluation': True,
        'thresholds': {
            'confidence': 0.95,
            'min_books': 6,
            'min_genres': 4,
            'min_book_trials': 200,
            'min_training_trials': 1000,
            'min_genre_trials': 200,
            'min_wilson_lower': 0.9,
            'max_holdout_rate_drop': 0.05,
        },
        'fields': [
            {'field_id': 'paragraph_length', 'kind': 'deterministic'},
            {'field_id': 'voice_perspective', 'kind': 'semantic'},
        ],
    })


def _counts(passed: int = 200) -> dict[str, dict[str, int]]:
    return {
        dimension: {'passed': passed, 'total': 200}
        for dimension in (
            'transferability',
            'position_coverage',
            'ab_discrimination',
            'material_partition',
        )
    }


def _books() -> list[BookSummary]:
    genres = ['都市', '都市', '古言', '古言', '悬疑', '科幻']
    return [BookSummary.model_validate({
        'schema_version': 1,
        'sid': f'private-book-{index}',
        'source_sha256': _sha(f'source-{index}'),
        'genre': genre,
        'fields': [
            {'field_id': 'paragraph_length', 'metrics': _counts()},
            {'field_id': 'voice_perspective', 'metrics': _counts()},
        ],
    }) for index, genre in enumerate(genres, 1)]


def _forbidden_key_paths(value: object, at: str = '$') -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            tokens = str(key).casefold().replace('-', '_').split('_')
            if {'excerpt', 'prompt', 'response', 'text'} & set(tokens):
                paths.append(f'{at}.{key}')
            paths.extend(_forbidden_key_paths(child, f'{at}.{key}'))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            paths.extend(_forbidden_key_paths(child, f'{at}[{index}]'))
    return paths


def test_six_book_four_genre_leave_one_out_promotes_only_semantic_fields():
    manifest = evaluate(_protocol(), _books())

    decisions = {row.field_id: row for row in manifest.fields}
    assert decisions['voice_perspective'].eligibility == 'hard'
    assert decisions['voice_perspective'].reasons == []
    assert all(row.passed for row in decisions['voice_perspective'].leave_one_book)
    assert all(row.passed for row in decisions['voice_perspective'].cross_genre)

    deterministic = decisions['paragraph_length']
    assert deterministic.eligibility == 'diagnostic'
    assert deterministic.reasons == ['deterministic_metric_is_diagnostic_only']
    assert all(
        field.eligibility == 'diagnostic'
        for book in manifest.books
        for field in book.fields
        if field.field_id == 'paragraph_length'
    )


def test_any_held_out_or_genre_failure_downgrades_field_to_diagnostic():
    books = _books()
    raw = books[-1].model_dump(mode='json')
    raw['fields'][1]['metrics']['transferability'] = {'passed': 180, 'total': 200}
    books[-1] = BookSummary.model_validate(raw)

    decision = {
        row.field_id: row for row in evaluate(_protocol(), books).fields
    }['voice_perspective']
    assert decision.eligibility == 'diagnostic'
    assert any(reason.startswith('leave_one_book_failed:') for reason in decision.reasons)
    assert 'cross_genre_failed:科幻' in decision.reasons
    failed = [row for row in decision.leave_one_book if row.sid == books[-1].sid]
    assert len(failed) == 1
    assert not failed[0].metrics['transferability'].passed


def test_structural_floors_and_frozen_field_set_are_fail_closed():
    protocol = _protocol()
    books = _books()
    with pytest.raises(ValueError, match='need >= 6 books'):
        evaluate(protocol, books[:5])

    three_genres = []
    for index, book in enumerate(books):
        raw = book.model_dump(mode='json')
        raw['genre'] = ('甲', '乙', '丙')[index % 3]
        three_genres.append(BookSummary.model_validate(raw))
    with pytest.raises(ValueError, match='need >= 4 genres'):
        evaluate(protocol, three_genres)

    missing = books[0].model_dump(mode='json')
    missing['fields'] = missing['fields'][:1]
    books[0] = BookSummary.model_validate(missing)
    with pytest.raises(ValueError, match='exactly match frozen protocol'):
        evaluate(protocol, books)


def test_protocol_can_tighten_but_not_weaken_quality_floors():
    raw = _protocol().model_dump(mode='json')
    raw['thresholds']['min_wilson_lower'] = 0.89
    with pytest.raises(ValueError):
        ValidationProtocol.model_validate(raw)

    raw = _protocol().model_dump(mode='json')
    raw['thresholds']['max_holdout_rate_drop'] = 0.051
    with pytest.raises(ValueError):
        ValidationProtocol.model_validate(raw)


@pytest.mark.parametrize('private_key', [
    'excerpt', 'source_text', 'judge_prompt', 'raw_response',
])
def test_private_text_bearing_keys_are_rejected_before_schema_validation(
    tmp_path: Path,
    private_key: str,
):
    raw = _books()[0].model_dump(mode='json')
    raw[private_key] = 'private material must not enter this workflow'
    path = tmp_path / 'summary.json'
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')

    with pytest.raises(ValueError, match='private text-bearing key refused'):
        load_json(path, BookSummary)


def test_hashes_are_order_independent_reproducible_and_bind_both_inputs():
    protocol = _protocol()
    books = _books()
    manifest = evaluate(protocol, books)
    reordered = evaluate(protocol, list(reversed(books)))
    assert manifest == reordered
    assert len(manifest.protocol_sha256) == 64
    assert len(manifest.input_sha256) == 64
    assert len(manifest.output_sha256) == 64
    validate_manifest(protocol, books, manifest)

    changed_raw = books[0].model_dump(mode='json')
    changed_raw['fields'][1]['metrics']['position_coverage']['passed'] = 199
    changed = [BookSummary.model_validate(changed_raw), *books[1:]]
    with pytest.raises(ValueError, match='does not match'):
        validate_manifest(protocol, changed, manifest)

    tampered_raw = manifest.model_dump(mode='json')
    tampered_raw['books'][0]['genre'] = '被篡改'
    tampered = ValidationManifest.model_validate(tampered_raw)
    with pytest.raises(ValueError, match='output_sha256 mismatch'):
        validate_manifest(protocol, books, tampered)


def test_published_manifest_has_no_text_prompt_response_or_excerpt_fields():
    payload = evaluate(_protocol(), _books()).model_dump(mode='json')
    assert _forbidden_key_paths(payload) == []
