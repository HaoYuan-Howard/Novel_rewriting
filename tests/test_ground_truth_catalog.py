from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.schema.evaluation import EVALUATOR_ABILITIES
from tools.build_ground_truth_catalog import (
    PrivateBundleInventory,
    PrivateCaseRecipe,
    PrivateCorpusInventory,
    RenderedSubject,
    build_bundle_catalog,
    build_catalog,
    main,
    render_recipe,
    render_stored_recipe,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _subject_sha(value: dict) -> str:
    subject = RenderedSubject.model_validate(value).model_dump(mode='json')
    canonical = json.dumps(
        subject, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(canonical).hexdigest()


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def _private_corpus(root: Path, *, genres: int = 4, stale_stage2: int | None = None):
    books = []
    for book_number in range(genres):
        book_id = f'private-book-{book_number}'
        genre = f'genre-{book_number}'
        directory = f'books/book-{book_number}'
        books.append({
            'book_id': book_id,
            'genre': genre,
            'directory': directory,
            'rewrite_modes': ['deep'],
        })
        book = root / directory
        sections = []
        for ordinal in range(1, 27):
            token = f'{book_number}_{ordinal}'
            text = (
                f'甲按标记{token}先打开门。'
                f'乙随后记录了{1000 + book_number * 100 + ordinal}枚印章。'
                f'丙依照暗号{token}最后锁门。'
            )
            sections.append({
                'section_id': f'sec_{ordinal:04d}',
                'chapter_ordinal': ordinal,
                'source_chapter_label': str(ordinal),
                'text': text,
            })
            _write(book / f'stage5/deep/ch{ordinal:02d}.json', {
                '_schema_version': 2,
                'segments': [{'slot': 's01', 'text': f'改写候选{token}，保留线索。'}],
            })
        _write(book / 'stage1/main_text.json', {
            '_schema_version': 1,
            'payload': sections,
        })
        secrets = [{
            'secret_id': f'secret_{index + 1:02d}',
            'fact': f'隐情{book_number}_{index}只在第二章后公开',
            'reader_reveal_events': [{'chapter': 2}],
        } for index in range(25)]
        rules = [{
            'rule_id': f'wr_{index + 1:02d}',
            'versions': [{
                'effective_from_chapter': 1,
                'desc': f'规则{book_number}_{index}要求印章必须登记',
            }],
        } for index in range(25)]
        _write(book / 'stage2/global_grounding.json', {
            '_schema_version': 1 if stale_stage2 == book_number else 2,
            'canon': {'secrets': secrets, 'world_rules': rules},
        })
        chapter_styles = [{
            'source_ordinal': ordinal,
            'voice': {
                'narrative_distance': f'题材{book_number}的近距离叙述{ordinal}',
                'register_tone': f'题材{book_number}的克制语调{ordinal}',
            },
            'surface_habits': [f'先动作后判断的章级习惯{book_number}_{ordinal}'],
        } for ordinal in range(1, 27)]
        _write(book / 'stage3/style_grounding.json', {
            '_schema_version': 2,
            'chapter_styles': chapter_styles,
        })
    inventory = {
        'inventory_version': 1,
        'dataset_id': 'private-held-out-v1',
        'rubric_sha256': _sha('locked rubric'),
        'created_at': '2026-08-16T00:00:00Z',
        'books': books,
    }
    return PrivateCorpusInventory.model_validate(inventory)


def test_builds_exact_text_free_catalog_from_current_private_artifacts(tmp_path):
    inventory = _private_corpus(tmp_path)
    result = build_catalog(tmp_path, inventory)

    assert result.audit.status == 'ready', result.audit
    assert result.catalog is not None
    assert len(result.catalog.cases) == 800
    assert len(result.recipes) == 800
    for ability in EVALUATOR_ABILITIES:
        rows = [row for row in result.catalog.cases if row.ability == ability]
        assert len(rows) == 100
        assert sorted({row.genre for row in rows}) == [
            'genre-0', 'genre-1', 'genre-2', 'genre-3']
        assert all(sum(row.genre == genre for row in rows) >= 10
                   for genre in ('genre-0', 'genre-1', 'genre-2', 'genre-3'))

    catalog_json = result.catalog.model_dump_json()
    assert '改写候选' not in catalog_json
    assert '隐情' not in catalog_json
    assert 'variant' not in catalog_json
    recipe_json = ''.join(row.model_dump_json() for row in result.recipes)
    assert '改写候选' not in recipe_json
    assert '隐情' not in recipe_json
    assert 'expected' not in recipe_json
    assert all(row.evidence[0].artifact.startswith('private://ground-truth/')
               for row in result.catalog.cases)


def test_render_recomputes_subject_and_stale_source_fails_closed(tmp_path, capsys):
    inventory = _private_corpus(tmp_path)
    result = build_catalog(tmp_path, inventory)
    assert result.catalog is not None
    recipe = next(row for row in result.recipes if row.ability == 'event_order')
    rendered = render_recipe(recipe, tmp_path)
    assert rendered.ability == 'event_order'
    assert len(rendered.panels) == 2

    case_store = tmp_path / 'private-case-store'
    case_store.mkdir()
    (case_store / f'{recipe.record_id}.json').write_text(
        recipe.model_dump_json(indent=2), encoding='utf-8')
    case = next(row for row in result.catalog.cases
                if row.subject_sha256 == recipe.subject_sha256)
    assert main([
        'render', '--corpus-root', str(tmp_path), '--case-store', str(case_store),
        '--artifact', case.evidence[0].artifact,
        '--subject-sha256', case.subject_sha256,
    ]) == 0
    assert '候选顺序' in capsys.readouterr().out

    source_path = tmp_path / recipe.bundles[0].refs[0].artifact
    source_path.write_text(source_path.read_text(encoding='utf-8') + ' ', encoding='utf-8')
    with pytest.raises(ValueError, match='stale source'):
        render_recipe(recipe, tmp_path)


def test_schema_neutral_bundle_adapter_needs_no_pipeline_or_paid_rebuild(
    tmp_path, capsys,
):
    families = []
    for ability in EVALUATOR_ABILITIES:
        for number in range(100):
            genre = (f'genre-{number % 4}' if number < 40
                     else f'tail-{number:03d}')
            payload = {
                'ability': ability,
                'question': f'{ability} 的冻结人工判据',
                'panels': [
                    {'role': '来源', 'text': f'私有来源 {ability} {number}'},
                    {'role': '候选', 'text': f'私有候选 {ability} {number}'},
                ],
            }
            artifact = f'private-subjects/{ability}-{number:03d}.json'
            _write(tmp_path / artifact, payload)
            raw = (tmp_path / artifact).read_bytes()
            variants = [{
                'variant_id': f'v_{number:03d}',
                'subject': {
                    'artifact': artifact,
                    'artifact_sha256': hashlib.sha256(raw).hexdigest(),
                    'subject_sha256': _subject_sha(payload),
                },
            }]
            if number == 0:
                alternate = {
                    **payload,
                    'panels': [*payload['panels'][:-1], {
                        'role': '候选', 'text': f'私有候选变体 {ability} {number}',
                    }],
                }
                alternate_artifact = f'private-subjects/{ability}-{number:03d}-b.json'
                _write(tmp_path / alternate_artifact, alternate)
                alternate_raw = (tmp_path / alternate_artifact).read_bytes()
                variants.append({
                    'variant_id': f'v_{number:03d}_b',
                    'subject': {
                        'artifact': alternate_artifact,
                        'artifact_sha256': hashlib.sha256(alternate_raw).hexdigest(),
                        'subject_sha256': _subject_sha(alternate),
                    },
                })
            families.append({
                'family_id': f'fam-{ability}-{number:03d}',
                'ability': ability,
                'genre': genre,
                'variants': variants,
            })
    inventory = PrivateBundleInventory.model_validate({
        'inventory_version': 1,
        'dataset_id': 'schema-neutral-private-v1',
        'rubric_sha256': _sha('locked rubric'),
        'created_at': '2026-08-16T00:00:00Z',
        'families': families,
    })
    result = build_bundle_catalog(tmp_path, inventory)

    assert result.audit.status == 'ready', result.audit
    assert result.catalog is not None
    assert len(result.catalog.cases) == 800
    assert len(result.recipes) == 800
    assert all(row.recipe_kind == 'external' for row in result.recipes)
    assert len({row.family_id for row in result.recipes}) == 800
    for ability in EVALUATOR_ABILITIES:
        rows = [row for row in result.catalog.cases if row.ability == ability]
        assert len(rows) == 100
        assert sum(sum(row.genre == genre for row in rows) >= 10
                   for genre in {row.genre for row in rows}) >= 4
    assert '私有来源' not in result.catalog.model_dump_json()
    assert '私有候选' not in ''.join(row.model_dump_json() for row in result.recipes)
    assert not (tmp_path / 'stage1').exists()
    rendered = render_stored_recipe(result.recipes[0], tmp_path)
    assert rendered.ability == result.recipes[0].ability

    recipe = result.recipes[0]
    case_store = tmp_path / 'resolver-store'
    case_store.mkdir()
    (case_store / f'{recipe.record_id}.json').write_text(
        recipe.model_dump_json(indent=2), encoding='utf-8')
    case = next(row for row in result.catalog.cases
                if row.subject_sha256 == recipe.subject_sha256)
    assert main([
        'render', '--corpus-root', str(tmp_path), '--case-store', str(case_store),
        '--artifact', case.evidence[0].artifact,
        '--subject-sha256', case.subject_sha256,
    ]) == 0
    assert '私有候选' in capsys.readouterr().out


def test_insufficient_genres_and_stale_schema_emit_honest_blockers_without_catalog(
    tmp_path, capsys,
):
    inventory = _private_corpus(tmp_path, genres=4, stale_stage2=3)
    result = build_catalog(tmp_path, inventory)
    assert result.catalog is None
    assert result.audit.status == 'blocked'
    blocked = {row.ability for row in result.audit.abilities if row.blockers}
    assert {'secret_timeline', 'world_rule_consistency'} <= blocked
    assert any('schema v1' in issue for issue in result.audit.source_issues)

    inventory_path = tmp_path / 'inventory.json'
    inventory_path.write_text(inventory.model_dump_json(indent=2), encoding='utf-8')
    catalog_path = tmp_path / 'out/catalog.json'
    case_store = tmp_path / 'out/cases'
    audit_path = tmp_path / 'out/audit.json'
    assert main([
        'build', '--corpus-root', str(tmp_path), '--inventory', str(inventory_path),
        '--catalog', str(catalog_path), '--case-store', str(case_store),
        '--audit', str(audit_path),
    ]) == 2
    assert audit_path.is_file()
    assert not catalog_path.exists()
    assert not case_store.exists()
    assert 'blocked_abilities' in capsys.readouterr().out


def test_inventory_requires_explicit_nonblank_genre_and_confined_paths():
    base = {
        'inventory_version': 1,
        'dataset_id': 'private-held-out-v1',
        'rubric_sha256': _sha('rubric'),
        'created_at': '2026-08-16T00:00:00Z',
        'books': [{
            'book_id': 'book-001', 'genre': 'genre', 'directory': 'book',
        }],
    }
    blank = json.loads(json.dumps(base))
    blank['books'][0]['genre'] = '  '
    with pytest.raises(ValidationError, match='genre must not be blank'):
        PrivateCorpusInventory.model_validate(blank)
    escaped = json.loads(json.dumps(base))
    escaped['books'][0]['directory'] = '../outside'
    with pytest.raises(ValidationError, match='relative path'):
        PrivateCorpusInventory.model_validate(escaped)

    bundle = {
        'inventory_version': 1,
        'dataset_id': 'private-held-out-v1',
        'rubric_sha256': _sha('rubric'),
        'created_at': '2026-08-16T00:00:00Z',
        'families': [{
            'family_id': 'family-001',
            'ability': 'event_order',
            'genre': '   ',
            'variants': [{
                'variant_id': 'v_001',
                'subject': {
                    'artifact': 'private/case.json',
                    'artifact_sha256': '1' * 64,
                    'subject_sha256': '2' * 64,
                },
            }],
        }],
    }
    with pytest.raises(ValidationError, match='genre must not be blank'):
        PrivateBundleInventory.model_validate(bundle)
    bundle['families'][0]['genre'] = 'genre'
    bundle['families'][0]['variants'][0]['variant_id'] = 'expected-pass'
    with pytest.raises(ValidationError, match='must not encode an answer'):
        PrivateBundleInventory.model_validate(bundle)


def test_recipe_rejects_answer_or_source_text_smuggling():
    source = {
        'artifact': 'books/book/stage1/main_text.json',
        'artifact_sha256': '1' * 64,
        'json_pointer': '/payload/0/text',
        'start': 0,
        'end': 4,
    }
    with pytest.raises(ValidationError):
        PrivateCaseRecipe.model_validate({
            'recipe_version': 1,
            'recipe_kind': 'derived',
            'record_id': 'case_' + '0' * 24,
            'dataset_id': 'dataset',
            'ability': 'event_order',
            'genre': 'genre',
            'variant': 'unchanged',
            'bundles': [
                {'name': 'event_a', 'refs': [source]},
                {'name': 'event_b', 'refs': [source]},
            ],
            'parameters': {},
            'subject_sha256': '0' * 64,
            'expected': 'PASS',
            'text': 'copyrighted excerpt',
        })
