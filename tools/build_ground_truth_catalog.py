#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build an offline, text-free TODO-24 candidate catalog from private artifacts.

The command never calls an LLM and never uses the network.  Its preferred
schema-neutral adapter consumes private rendered-subject bundles, so raw and
legacy sources do not need a paid current-schema rebuild.  An optional adapter
can derive the same shape from current Stage1/2/3/5 artifacts.  Copyrighted text
is represented in catalog/resolver outputs by checked locators and hashes;
``render`` reconstructs one case to stdout and persists no excerpt.

Candidate transformations are deterministic controls/counterexamples, not
labels.  Their verdict remains the job of two independent human annotators and
the adjudication workflow in :mod:`tools.ground_truth_workflow`.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Annotated, Any, Literal, Sequence

from pydantic import Field, TypeAdapter, ValidationError, model_validator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts.schema import artifact_spec
from contracts.schema.base import StrictModel
from contracts.schema.evaluation import Ability, EVALUATOR_ABILITIES, EvidenceRef
from tools.ground_truth_workflow import CandidateCase, CandidateCatalog


CATALOG_BUILDER_VERSION = 1
MIN_CASES_PER_GENRE = 10
MIN_GENRES_PER_ABILITY = 4
CASES_PER_ABILITY = 100
_SHA256_RE = re.compile(r'^[0-9a-f]{64}$')
_RECORD_RE = re.compile(r'^case_[0-9a-f]{24}$')
_CHAPTER_RE = re.compile(r'^ch(\d+)\.json$')
_SENTENCE_RE = re.compile(r'[^\r\n。！？!?]+[。！？!?]+|[^\r\n]+')
_NUMBER_RE = re.compile(r'\d+(?:\.\d+)?|[零〇一二三四五六七八九十百千万两]+')


def _canonical(value: Any) -> bytes:
    if isinstance(value, StrictModel):
        value = value.model_dump(mode='json')
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(value: Any) -> str:
    return _sha256_bytes(_canonical(value))


def _not_blank(value: str, field: str) -> str:
    if not value.strip():
        raise ValueError(f'{field} must not be blank')
    return value


def _relative_path(value: str, field: str) -> str:
    normalized = value.replace('\\', '/')
    path = Path(normalized)
    if not normalized or path.is_absolute() or '..' in path.parts:
        raise ValueError(f'{field} must be a relative path below corpus root')
    return normalized


class PrivateBookSpec(StrictModel):
    book_id: str = Field(pattern=r'^[a-z0-9][a-z0-9_.-]{2,95}$')
    genre: str = Field(min_length=1)
    directory: str = Field(min_length=1)
    rewrite_modes: list[Literal['deep', 'shallow']] = Field(
        default_factory=lambda: ['deep', 'shallow'], min_length=1, max_length=2,
    )

    @model_validator(mode='after')
    def _valid_book(self) -> 'PrivateBookSpec':
        _not_blank(self.genre, 'genre')
        object.__setattr__(
            self, 'directory', _relative_path(self.directory, 'directory'))
        if len(self.rewrite_modes) != len(set(self.rewrite_modes)):
            raise ValueError('rewrite_modes must not contain duplicates')
        return self


class PrivateCorpusInventory(StrictModel):
    inventory_version: Literal[1]
    dataset_id: str = Field(min_length=1)
    rubric_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    created_at: str = Field(min_length=1)
    books: list[PrivateBookSpec] = Field(min_length=1)

    @model_validator(mode='after')
    def _unique_books(self) -> 'PrivateCorpusInventory':
        _not_blank(self.dataset_id, 'dataset_id')
        _not_blank(self.created_at, 'created_at')
        for field in ('book_id', 'directory'):
            values = [getattr(row, field) for row in self.books]
            if len(values) != len(set(values)):
                raise ValueError(f'inventory {field} must be unique')
        return self


class PrivateSourceRef(StrictModel):
    """A hash-bound JSON string locator below the private corpus root."""

    artifact: str = Field(min_length=1)
    artifact_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    json_pointer: str = Field(pattern=r'^/(?:[^/]|~[01])+(?:/(?:[^/]|~[01])+)*$')
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)

    @model_validator(mode='after')
    def _valid_ref(self) -> 'PrivateSourceRef':
        object.__setattr__(
            self, 'artifact', _relative_path(self.artifact, 'artifact'))
        if (self.start is None) != (self.end is None):
            raise ValueError('source slice must provide start and end together')
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError('source slice end must be greater than start')
        return self


class PrivateSourceBundle(StrictModel):
    name: str = Field(pattern=r'^[a-z][a-z0-9_]{1,31}$')
    refs: list[PrivateSourceRef] = Field(min_length=1)
    joiner: str = ''


_VARIANTS = Literal[
    'unchanged', 'reordered', 'mutated', 'correct_attribution',
    'wrong_attribution', 'timeline_original', 'timeline_injected',
    'rule_bound', 'rule_mismatched', 'trim_control', 'trim_deletion',
    'style_in_domain', 'style_cross_domain',
]


class PrivateCaseRecipe(StrictModel):
    """Text-free administrator record used to reconstruct one blind subject."""

    recipe_version: Literal[1]
    recipe_kind: Literal['derived']
    record_id: str = Field(pattern=r'^case_[0-9a-f]{24}$')
    dataset_id: str = Field(min_length=1)
    ability: Ability
    genre: str = Field(min_length=1)
    variant: _VARIANTS
    bundles: list[PrivateSourceBundle] = Field(min_length=1)
    parameters: dict[str, int | str | bool]
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def _valid_shape(self) -> 'PrivateCaseRecipe':
        names = [bundle.name for bundle in self.bundles]
        if len(names) != len(set(names)):
            raise ValueError('recipe bundle names must be unique')
        expected: dict[Ability, tuple[set[str], set[str]]] = {
            'event_order': ({'event_a', 'event_b'}, set()),
            'verbatim_fidelity': ({'excerpt'}, {'position', 'replacement'}),
            'hard_number_fidelity': (
                {'excerpt'}, {'number_start', 'number_end', 'replacement'}),
            'reference_boundary': (
                {'chapter_a', 'chapter_b', 'candidate'},
                {'chapter_a_ordinal', 'chapter_b_ordinal', 'claimed_ordinal'}),
            'secret_timeline': (
                {'secret', 'candidate'}, {'reveal_chapter', 'candidate_chapter'}),
            'world_rule_consistency': ({'rule'}, set()),
            'trim_equivalence': ({'source'}, {'delete_start', 'delete_end'}),
            'style_transfer': ({'profile', 'candidate'}, set()),
        }
        bundle_names, parameter_names = expected[self.ability]
        if set(names) != bundle_names or set(self.parameters) != parameter_names:
            raise ValueError(
                f'{self.ability}: recipe shape mismatch; bundles={sorted(names)!r} '
                f'parameters={sorted(self.parameters)!r}')
        return self


class AbilitySupply(StrictModel):
    ability: Ability
    source_families_by_genre: dict[str, int]
    eligible_genres: list[str]
    selected_genres: list[str]
    selected_cases: int = Field(ge=0)
    blockers: list[str]


class CatalogBuildAudit(StrictModel):
    builder_version: Literal[1]
    status: Literal['ready', 'blocked']
    dataset_id: str = Field(min_length=1)
    inventory_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_snapshot_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_groups: int = Field(ge=1)
    source_issues: list[str]
    abilities: list[AbilitySupply] = Field(min_length=8, max_length=8)


class RenderedPanel(StrictModel):
    role: str = Field(min_length=1)
    text: str


class RenderedSubject(StrictModel):
    ability: Ability
    question: str = Field(min_length=1)
    panels: list[RenderedPanel] = Field(min_length=2)


class PrivateSubjectLocator(StrictModel):
    """One private rendered-subject file; its body never enters catalog JSON."""

    artifact: str = Field(min_length=1)
    artifact_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def _confined_artifact(self) -> 'PrivateSubjectLocator':
        object.__setattr__(
            self, 'artifact', _relative_path(self.artifact, 'artifact'))
        return self


class PrivateVariantSpec(StrictModel):
    variant_id: str = Field(pattern=r'^[a-z0-9][a-z0-9_.-]{2,63}$')
    subject: PrivateSubjectLocator

    @model_validator(mode='after')
    def _opaque_variant_id(self) -> 'PrivateVariantSpec':
        lowered = self.variant_id.casefold()
        forbidden = (
            'pass', 'fail', 'positive', 'negative', 'control', 'counter', 'expected',
        )
        if any(word in lowered for word in forbidden):
            raise ValueError('variant_id must be opaque and must not encode an answer')
        return self


class PrivateVariantFamily(StrictModel):
    """Variants of one source subject; at most one may enter a blind catalog."""

    family_id: str = Field(pattern=r'^[a-z0-9][a-z0-9_.-]{4,95}$')
    ability: Ability
    genre: str = Field(min_length=1)
    variants: list[PrivateVariantSpec] = Field(min_length=1)

    @model_validator(mode='after')
    def _valid_family(self) -> 'PrivateVariantFamily':
        _not_blank(self.genre, 'genre')
        ids = [row.variant_id for row in self.variants]
        hashes = [row.subject.subject_sha256 for row in self.variants]
        if len(ids) != len(set(ids)):
            raise ValueError('variant_id must be unique inside a family')
        if len(hashes) != len(set(hashes)):
            raise ValueError('family variants must render distinct subjects')
        return self


class PrivateBundleInventory(StrictModel):
    """Schema-neutral adapter for raw, legacy, or current private sources."""

    inventory_version: Literal[1]
    dataset_id: str = Field(min_length=1)
    rubric_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    created_at: str = Field(min_length=1)
    families: list[PrivateVariantFamily] = Field(min_length=1)

    @model_validator(mode='after')
    def _unique_families(self) -> 'PrivateBundleInventory':
        _not_blank(self.dataset_id, 'dataset_id')
        _not_blank(self.created_at, 'created_at')
        ids = [row.family_id for row in self.families]
        if len(ids) != len(set(ids)):
            raise ValueError('bundle inventory family_id must be globally unique')
        return self


class PrivateExternalRecipe(StrictModel):
    """Opaque resolver record for one private, schema-neutral subject bundle."""

    recipe_version: Literal[1]
    recipe_kind: Literal['external']
    record_id: str = Field(pattern=r'^case_[0-9a-f]{24}$')
    dataset_id: str = Field(min_length=1)
    ability: Ability
    genre: str = Field(min_length=1)
    family_id: str = Field(min_length=1)
    variant_id: str = Field(min_length=1)
    subject: PrivateSubjectLocator

    @property
    def subject_sha256(self) -> str:
        return self.subject.subject_sha256


StoredCaseRecipe = Annotated[
    PrivateCaseRecipe | PrivateExternalRecipe,
    Field(discriminator='recipe_kind'),
]
_STORED_RECIPE_ADAPTER = TypeAdapter(StoredCaseRecipe)


@dataclass(frozen=True)
class _TextUnit:
    ref: PrivateSourceRef
    text: str


@dataclass
class _BookData:
    spec: PrivateBookSpec
    source: dict[int, list[_TextUnit]]
    source_full: dict[int, _TextUnit]
    rewrites: dict[int, list[_TextUnit]]
    secrets: list[tuple[_TextUnit, int]]
    rules: list[_TextUnit]
    styles: dict[int, list[_TextUnit]]
    issues: list[str]


@dataclass(frozen=True)
class _DraftRecipe:
    family_id: str
    branch: int
    recipe: PrivateCaseRecipe | PrivateExternalRecipe


@dataclass(frozen=True)
class CatalogBuildResult:
    catalog: CandidateCatalog | None
    recipes: tuple[PrivateCaseRecipe | PrivateExternalRecipe, ...]
    audit: CatalogBuildAudit


def _resolve_below(root: Path, relative: str) -> Path:
    root = root.resolve()
    path = (root / _relative_path(relative, 'path')).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f'path escapes corpus root: {relative}') from exc
    return path


def _pointer_get(value: Any, pointer: str) -> Any:
    current = value
    for encoded in pointer.split('/')[1:]:
        token = encoded.replace('~1', '/').replace('~0', '~')
        if isinstance(current, list):
            if re.fullmatch(r'0|[1-9]\d*', token) is None:
                raise ValueError(f'invalid JSON pointer {pointer!r}')
            try:
                current = current[int(token)]
            except IndexError as exc:
                raise ValueError(f'invalid JSON pointer {pointer!r}') from exc
        elif isinstance(current, dict) and token in current:
            current = current[token]
        else:
            raise ValueError(f'invalid JSON pointer {pointer!r}')
    return current


def _pointer_token(value: str) -> str:
    return value.replace('~', '~0').replace('/', '~1')


def _read_json_artifact(
    corpus_root: Path,
    relative: str,
    formal_rel: str,
) -> tuple[Any, str]:
    path = _resolve_below(corpus_root, relative)
    raw = path.read_bytes()
    value = json.loads(raw.decode('utf-8'))
    spec = artifact_spec(formal_rel)
    if spec is None:
        raise ValueError(f'unregistered artifact path: {formal_rel}')
    marker = value.get('_schema_version') if isinstance(value, dict) else None
    if marker != spec.current_version:
        raise ValueError(
            f'{relative}: schema v{marker!r}, current is v{spec.current_version}')
    return value, _sha256_bytes(raw)


def _ref(
    artifact: str,
    artifact_sha256: str,
    pointer: str,
    *,
    start: int | None = None,
    end: int | None = None,
) -> PrivateSourceRef:
    return PrivateSourceRef(
        artifact=artifact, artifact_sha256=artifact_sha256,
        json_pointer=pointer, start=start, end=end,
    )


def _sentences(unit: _TextUnit) -> list[_TextUnit]:
    rows: list[_TextUnit] = []
    for match in _SENTENCE_RE.finditer(unit.text):
        left = match.start()
        right = match.end()
        while left < right and unit.text[left].isspace():
            left += 1
        while right > left and unit.text[right - 1].isspace():
            right -= 1
        if right - left < 4:
            continue
        base = unit.ref.start or 0
        rows.append(_TextUnit(
            ref=unit.ref.model_copy(update={
                'start': base + left, 'end': base + right,
            }),
            text=unit.text[left:right],
        ))
    return rows


def _string_at(value: Any, pointer: str, artifact: str) -> str:
    found = _pointer_get(value, pointer)
    if not isinstance(found, str):
        raise ValueError(f'{artifact}{pointer}: expected string')
    return found


def _load_book(corpus_root: Path, spec: PrivateBookSpec) -> _BookData:
    prefix = spec.directory.rstrip('/')
    issues: list[str] = []
    source: dict[int, list[_TextUnit]] = {}
    source_full: dict[int, _TextUnit] = {}
    rewrites: dict[int, list[_TextUnit]] = {}
    secrets: list[tuple[_TextUnit, int]] = []
    rules: list[_TextUnit] = []
    styles: dict[int, list[_TextUnit]] = {}

    main_rel = f'{prefix}/stage1/main_text.json'
    try:
        main, main_sha = _read_json_artifact(
            corpus_root, main_rel, 'stage1/main_text.json')
        payload = main.get('payload')
        if not isinstance(payload, list):
            raise ValueError(f'{main_rel}: payload must be a list')
        for index, row in enumerate(payload):
            if not isinstance(row, dict) or not isinstance(row.get('chapter_ordinal'), int):
                raise ValueError(f'{main_rel}: invalid chapter row {index}')
            pointer = f'/payload/{index}/text'
            text = _string_at(main, pointer, main_rel)
            ordinal = row['chapter_ordinal']
            unit = _TextUnit(_ref(main_rel, main_sha, pointer), text)
            source_full[ordinal] = unit
            source[ordinal] = _sentences(unit)
    except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        issues.append(f'{spec.book_id}: {exc}')

    global_rel = f'{prefix}/stage2/global_grounding.json'
    try:
        global_data, global_sha = _read_json_artifact(
            corpus_root, global_rel, 'stage2/global_grounding.json')
        canon = global_data.get('canon')
        if not isinstance(canon, dict):
            raise ValueError(f'{global_rel}: canon must be an object')
        raw_secrets = canon.get('secrets')
        if not isinstance(raw_secrets, list):
            raise ValueError(f'{global_rel}: canon.secrets must be a list')
        for index, row in enumerate(raw_secrets):
            pointer = f'/canon/secrets/{index}/fact'
            text = _string_at(global_data, pointer, global_rel)
            reveals = row.get('reader_reveal_events') if isinstance(row, dict) else None
            chapters = [event.get('chapter') for event in (reveals or [])
                        if isinstance(event, dict) and isinstance(event.get('chapter'), int)]
            if chapters:
                secrets.append((_TextUnit(
                    _ref(global_rel, global_sha, pointer), text), min(chapters)))
        raw_rules = canon.get('world_rules')
        if not isinstance(raw_rules, list):
            raise ValueError(f'{global_rel}: canon.world_rules must be a list')
        for rule_index, row in enumerate(raw_rules):
            versions = row.get('versions') if isinstance(row, dict) else None
            if not isinstance(versions, list):
                continue
            for version_index, _version in enumerate(versions):
                pointer = (
                    f'/canon/world_rules/{rule_index}/versions/{version_index}/desc')
                text = _string_at(global_data, pointer, global_rel)
                rules.append(_TextUnit(_ref(global_rel, global_sha, pointer), text))
    except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        issues.append(f'{spec.book_id}: {exc}')

    style_rel = f'{prefix}/stage3/style_grounding.json'
    try:
        style_data, style_sha = _read_json_artifact(
            corpus_root, style_rel, 'stage3/style_grounding.json')
        chapter_styles = style_data.get('chapter_styles')
        if not isinstance(chapter_styles, list):
            raise ValueError(f'{style_rel}: chapter_styles must be a list')
        for index, row in enumerate(chapter_styles):
            if not isinstance(row, dict) or not isinstance(row.get('source_ordinal'), int):
                continue
            units: list[_TextUnit] = []
            voice = row.get('voice')
            if isinstance(voice, dict):
                for key in sorted(voice):
                    pointer = f'/chapter_styles/{index}/voice/{_pointer_token(key)}'
                    text = _string_at(style_data, pointer, style_rel)
                    units.append(_TextUnit(_ref(style_rel, style_sha, pointer), text))
            habits = row.get('surface_habits')
            if isinstance(habits, list):
                for habit_index, _habit in enumerate(habits):
                    pointer = f'/chapter_styles/{index}/surface_habits/{habit_index}'
                    text = _string_at(style_data, pointer, style_rel)
                    units.append(_TextUnit(_ref(style_rel, style_sha, pointer), text))
            if units:
                styles[row['source_ordinal']] = units
    except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        issues.append(f'{spec.book_id}: {exc}')

    seen_ordinals: set[int] = set()
    for mode in spec.rewrite_modes:
        directory = _resolve_below(corpus_root, f'{prefix}/stage5/{mode}')
        if not directory.is_dir():
            issues.append(f'{spec.book_id}: missing {prefix}/stage5/{mode}')
            continue
        for path in sorted(directory.iterdir(), key=lambda value: value.name):
            match = _CHAPTER_RE.fullmatch(path.name)
            if match is None:
                continue
            ordinal = int(match.group(1))
            if ordinal in seen_ordinals:
                continue
            rel = path.relative_to(corpus_root.resolve()).as_posix()
            try:
                data, file_sha = _read_json_artifact(
                    corpus_root, rel, f'stage5/{mode}/ch{ordinal:02d}.json')
                segments = data.get('segments')
                if not isinstance(segments, list) or not segments:
                    raise ValueError(f'{rel}: segments must be a non-empty list')
                units = []
                for index, _segment in enumerate(segments):
                    pointer = f'/segments/{index}/text'
                    text = _string_at(data, pointer, rel)
                    units.append(_TextUnit(_ref(rel, file_sha, pointer), text))
                rewrites[ordinal] = units
                seen_ordinals.add(ordinal)
            except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                issues.append(f'{spec.book_id}: {exc}')
    return _BookData(
        spec=spec, source=source, source_full=source_full, rewrites=rewrites,
        secrets=secrets, rules=rules, styles=styles, issues=issues,
    )


def _bundle(name: str, units: Sequence[_TextUnit], joiner: str = '') -> PrivateSourceBundle:
    return PrivateSourceBundle(name=name, refs=[unit.ref for unit in units], joiner=joiner)


def _bundle_text(
    bundle: PrivateSourceBundle,
    corpus_root: Path,
    cache: dict[str, tuple[Any, str]] | None = None,
) -> str:
    pieces: list[str] = []
    cache = cache if cache is not None else {}
    for ref in bundle.refs:
        if ref.artifact not in cache:
            path = _resolve_below(corpus_root, ref.artifact)
            raw = path.read_bytes()
            actual = _sha256_bytes(raw)
            if actual != ref.artifact_sha256:
                raise ValueError(
                    f'stale source {ref.artifact}: expected {ref.artifact_sha256}, got {actual}')
            cache[ref.artifact] = (json.loads(raw.decode('utf-8')), actual)
        elif cache[ref.artifact][1] != ref.artifact_sha256:
            raise ValueError(
                f'conflicting source hash for {ref.artifact}: '
                f'{ref.artifact_sha256} != {cache[ref.artifact][1]}')
        value = _pointer_get(cache[ref.artifact][0], ref.json_pointer)
        if not isinstance(value, str):
            raise ValueError(f'{ref.artifact}{ref.json_pointer}: expected string')
        if ref.start is not None and ref.end is not None:
            if ref.end > len(value):
                raise ValueError(f'{ref.artifact}{ref.json_pointer}: stale source span')
            value = value[ref.start:ref.end]
        pieces.append(value)
    return bundle.joiner.join(pieces)


_QUESTIONS: dict[Ability, str] = {
    'event_order': '候选是否保持来源事件的先后顺序？',
    'verbatim_fidelity': '候选逐字内容是否与来源闭合一致？',
    'hard_number_fidelity': '候选中的硬数字是否与来源闭合一致？',
    'reference_boundary': '候选是否被归入正确的来源章节边界？',
    'secret_timeline': '候选是否遵守秘密对读者的揭示时间线？',
    'world_rule_consistency': '候选陈述是否与指定世界规则一致？',
    'trim_equivalence': '收缩候选是否与收缩前文本语义等价？',
    'style_transfer': '候选是否满足给定的跨题材文风画像？',
}


def render_recipe(
    recipe: PrivateCaseRecipe,
    corpus_root: Path,
    *,
    _source_cache: dict[str, tuple[Any, str]] | None = None,
) -> RenderedSubject:
    values = {bundle.name: _bundle_text(bundle, corpus_root, _source_cache)
              for bundle in recipe.bundles}
    params = recipe.parameters
    panels: list[RenderedPanel]
    if recipe.ability == 'event_order':
        source = values['event_a'] + '\n' + values['event_b']
        candidate = source if recipe.variant == 'unchanged' else (
            values['event_b'] + '\n' + values['event_a'])
        panels = [RenderedPanel(role='来源顺序', text=source),
                  RenderedPanel(role='候选顺序', text=candidate)]
    elif recipe.ability == 'verbatim_fidelity':
        candidate = values['excerpt']
        if recipe.variant == 'mutated':
            position = int(params['position'])
            candidate = candidate[:position] + str(params['replacement']) + candidate[position + 1:]
        panels = [RenderedPanel(role='来源', text=values['excerpt']),
                  RenderedPanel(role='候选', text=candidate)]
    elif recipe.ability == 'hard_number_fidelity':
        candidate = values['excerpt']
        if recipe.variant == 'mutated':
            left = int(params['number_start'])
            right = int(params['number_end'])
            candidate = candidate[:left] + str(params['replacement']) + candidate[right:]
        panels = [RenderedPanel(role='来源', text=values['excerpt']),
                  RenderedPanel(role='候选', text=candidate)]
    elif recipe.ability == 'reference_boundary':
        reference = (
            f"章节 {params['chapter_a_ordinal']}\n{values['chapter_a']}\n\n"
            f"章节 {params['chapter_b_ordinal']}\n{values['chapter_b']}")
        candidate = f"归入章节 {params['claimed_ordinal']}\n{values['candidate']}"
        panels = [RenderedPanel(role='章节边界', text=reference),
                  RenderedPanel(role='候选归属', text=candidate)]
    elif recipe.ability == 'secret_timeline':
        contract = (
            f"读者揭示章：{params['reveal_chapter']}\n秘密事实：{values['secret']}")
        candidate = values['candidate']
        if recipe.variant == 'timeline_injected':
            candidate = f'{candidate}\n\n{values["secret"]}'
        panels = [RenderedPanel(role='时间线契约', text=contract),
                  RenderedPanel(
                      role=f"第 {params['candidate_chapter']} 章候选", text=candidate)]
    elif recipe.ability == 'world_rule_consistency':
        candidate = (values['rule'] if recipe.variant == 'rule_bound'
                     else f'该规则不成立：{values["rule"]}')
        panels = [RenderedPanel(role='生效规则', text=values['rule']),
                  RenderedPanel(role='候选陈述', text=candidate)]
    elif recipe.ability == 'trim_equivalence':
        candidate = values['source']
        if recipe.variant == 'trim_deletion':
            left = int(params['delete_start'])
            right = int(params['delete_end'])
            candidate = (candidate[:left] + candidate[right:]).strip()
        else:
            candidate = re.sub(r'[ \t]+', ' ', candidate)
        panels = [RenderedPanel(role='收缩前', text=values['source']),
                  RenderedPanel(role='收缩候选', text=candidate)]
    else:
        panels = [RenderedPanel(role='目标文风画像', text=values['profile']),
                  RenderedPanel(role='候选正文', text=values['candidate'])]
    return RenderedSubject(
        ability=recipe.ability, question=_QUESTIONS[recipe.ability], panels=panels)


def _replacement(text: str) -> tuple[int, str]:
    for index, char in enumerate(text):
        if char.isalnum() or '\u4e00' <= char <= '\u9fff':
            return index, '甲' if char != '甲' else '乙'
    raise ValueError('cannot make a deterministic code-point counterexample')


def _number_replacement(value: str) -> str:
    if value.isdigit():
        return str(int(value) + 1)
    if re.fullmatch(r'\d+\.\d+', value):
        return value + '1'
    swaps = {'零': '一', '〇': '一', '一': '二', '二': '三', '两': '三'}
    for index, char in enumerate(value):
        if char in swaps:
            return value[:index] + swaps[char] + value[index + 1:]
    return '一' + value


def _make_recipe(
    inventory: PrivateCorpusInventory,
    corpus_root: Path,
    *,
    ability: Ability,
    genre: str,
    family_id: str,
    branch: int,
    variant: _VARIANTS,
    bundles: Sequence[PrivateSourceBundle],
    parameters: dict[str, int | str | bool],
    source_cache: dict[str, tuple[Any, str]],
) -> _DraftRecipe:
    identity = {
        'builder_version': CATALOG_BUILDER_VERSION,
        'dataset_id': inventory.dataset_id,
        'ability': ability,
        'genre': genre,
        'family_id': family_id,
        'branch': branch,
        'variant': variant,
        'bundles': [row.model_dump(mode='json') for row in bundles],
        'parameters': parameters,
    }
    record_id = f'case_{_sha256(identity)[:24]}'
    provisional = PrivateCaseRecipe(
        recipe_version=1, recipe_kind='derived', record_id=record_id,
        dataset_id=inventory.dataset_id,
        ability=ability, genre=genre, variant=variant, bundles=list(bundles),
        parameters=parameters, subject_sha256='0' * 64,
    )
    rendered = render_recipe(
        provisional, corpus_root, _source_cache=source_cache)
    recipe = provisional.model_copy(update={'subject_sha256': _sha256(rendered)})
    return _DraftRecipe(family_id=family_id, branch=branch, recipe=recipe)


def _family(book: _BookData, ability: Ability, key: str) -> str:
    return _sha256({
        'book': book.spec.book_id, 'genre': book.spec.genre,
        'ability': ability, 'key': key,
    })


def _recipes_for_book(
    inventory: PrivateCorpusInventory,
    corpus_root: Path,
    book: _BookData,
    source_cache: dict[str, tuple[Any, str]],
) -> dict[Ability, list[_DraftRecipe]]:
    out: dict[Ability, list[_DraftRecipe]] = {
        ability: [] for ability in EVALUATOR_ABILITIES
    }
    genre = book.spec.genre

    for ordinal, sentences in sorted(book.source.items()):
        for index in range(len(sentences) - 1):
            left, right = sentences[index:index + 2]
            family = _family(book, 'event_order', f'{ordinal}:{index}')
            bundles = [_bundle('event_a', [left]), _bundle('event_b', [right])]
            for branch, variant in enumerate(('unchanged', 'reordered')):
                out['event_order'].append(_make_recipe(
                    inventory, corpus_root, ability='event_order', genre=genre,
                    family_id=family, branch=branch, variant=variant,
                    bundles=bundles, parameters={}, source_cache=source_cache))

        for index, sentence in enumerate(sentences):
            try:
                position, replacement = _replacement(sentence.text)
            except ValueError:
                continue
            family = _family(book, 'verbatim_fidelity', f'{ordinal}:{index}')
            bundles = [_bundle('excerpt', [sentence])]
            params = {'position': position, 'replacement': replacement}
            for branch, variant in enumerate(('unchanged', 'mutated')):
                out['verbatim_fidelity'].append(_make_recipe(
                    inventory, corpus_root, ability='verbatim_fidelity', genre=genre,
                    family_id=family, branch=branch, variant=variant,
                    bundles=bundles, parameters=params, source_cache=source_cache))
            # One independent case family per sentence.  Multiple numerals in
            # the same excerpt would share an identical unchanged subject and
            # falsely inflate the held-out count.
            match = _NUMBER_RE.search(sentence.text)
            if match is not None:
                family = _family(
                    book, 'hard_number_fidelity', f'{ordinal}:{index}')
                params = {
                    'number_start': match.start(), 'number_end': match.end(),
                    'replacement': _number_replacement(match.group(0)),
                }
                for branch, variant in enumerate(('unchanged', 'mutated')):
                    out['hard_number_fidelity'].append(_make_recipe(
                        inventory, corpus_root, ability='hard_number_fidelity', genre=genre,
                        family_id=family, branch=branch, variant=variant,
                        bundles=bundles, parameters=params, source_cache=source_cache))

    ordinals = sorted(book.source)
    for left_ordinal, right_ordinal in zip(ordinals, ordinals[1:]):
        left_rows = book.source[left_ordinal]
        right_rows = book.source[right_ordinal]
        for side, candidate_rows, actual in (
            ('a', left_rows, left_ordinal), ('b', right_rows, right_ordinal),
        ):
            if not left_rows or not right_rows or not candidate_rows:
                continue
            family = _family(
                book, 'reference_boundary', f'{left_ordinal}:{right_ordinal}:{side}')
            candidate = left_rows[-1] if side == 'a' else right_rows[0]
            bundles = [
                _bundle('chapter_a', [left_rows[-1]]),
                _bundle('chapter_b', [right_rows[0]]),
                _bundle('candidate', [candidate]),
            ]
            base = {
                'chapter_a_ordinal': left_ordinal,
                'chapter_b_ordinal': right_ordinal,
            }
            for branch, (variant, claimed) in enumerate((
                ('correct_attribution', actual),
                ('wrong_attribution', right_ordinal if actual == left_ordinal else left_ordinal),
            )):
                out['reference_boundary'].append(_make_recipe(
                    inventory, corpus_root, ability='reference_boundary', genre=genre,
                    family_id=family, branch=branch, variant=variant,
                    bundles=bundles, parameters={**base, 'claimed_ordinal': claimed},
                    source_cache=source_cache))

    for index, (secret, reveal) in enumerate(book.secrets):
        possible = [ordinal for ordinal in sorted(book.rewrites) if ordinal < reveal]
        if not possible:
            continue
        candidate_ordinal = possible[index % len(possible)]
        family = _family(book, 'secret_timeline', f'{index}:{candidate_ordinal}')
        bundles = [
            _bundle('secret', [secret]),
            _bundle('candidate', book.rewrites[candidate_ordinal], '\n\n'),
        ]
        params = {'reveal_chapter': reveal, 'candidate_chapter': candidate_ordinal}
        for branch, variant in enumerate(('timeline_original', 'timeline_injected')):
            out['secret_timeline'].append(_make_recipe(
                inventory, corpus_root, ability='secret_timeline', genre=genre,
                family_id=family, branch=branch, variant=variant,
                bundles=bundles, parameters=params, source_cache=source_cache))

    for index, rule in enumerate(book.rules):
        family = _family(book, 'world_rule_consistency', str(index))
        bundles = [_bundle('rule', [rule])]
        for branch, variant in enumerate(('rule_bound', 'rule_mismatched')):
            out['world_rule_consistency'].append(_make_recipe(
                inventory, corpus_root, ability='world_rule_consistency', genre=genre,
                family_id=family, branch=branch, variant=variant,
                bundles=bundles, parameters={}, source_cache=source_cache))

    for ordinal, unit in sorted(book.source_full.items()):
        sentences = book.source.get(ordinal) or []
        if len(sentences) < 2:
            continue
        delete = sentences[1].ref
        if delete.start is None or delete.end is None:
            continue
        family = _family(book, 'trim_equivalence', str(ordinal))
        bundles = [_bundle('source', [unit])]
        params = {'delete_start': delete.start, 'delete_end': delete.end}
        for branch, variant in enumerate(('trim_control', 'trim_deletion')):
            out['trim_equivalence'].append(_make_recipe(
                inventory, corpus_root, ability='trim_equivalence', genre=genre,
                family_id=family, branch=branch, variant=variant,
                bundles=bundles, parameters=params, source_cache=source_cache))
    return out


def _add_style_recipes(
    inventory: PrivateCorpusInventory,
    corpus_root: Path,
    books: Sequence[_BookData],
    supplies: dict[Ability, list[_DraftRecipe]],
    source_cache: dict[str, tuple[Any, str]],
) -> None:
    ordered = sorted(books, key=lambda row: row.spec.book_id)
    for book in ordered:
        foreign = [row for row in ordered if row.spec.genre != book.spec.genre and row.rewrites]
        for ordinal, profile in sorted(book.styles.items()):
            own = book.rewrites.get(ordinal)
            if own is None or not foreign:
                continue
            other = foreign[int(_family(book, 'style_transfer', str(ordinal))[:8], 16) % len(foreign)]
            other_ordinals = sorted(other.rewrites)
            candidate_ordinal = other_ordinals[(ordinal - 1) % len(other_ordinals)]
            family = _family(book, 'style_transfer', str(ordinal))
            for branch, (variant, candidate) in enumerate((
                ('style_in_domain', own),
                ('style_cross_domain', other.rewrites[candidate_ordinal]),
            )):
                supplies['style_transfer'].append(_make_recipe(
                    inventory, corpus_root, ability='style_transfer', genre=book.spec.genre,
                    family_id=family, branch=branch, variant=variant,
                    bundles=[_bundle('profile', profile, '\n'),
                             _bundle('candidate', candidate, '\n\n')],
                    parameters={}, source_cache=source_cache))


def _select(
    dataset_id: str,
    supplies: dict[Ability, list[_DraftRecipe]],
    registered_abilities: tuple[Ability, ...] = EVALUATOR_ABILITIES,
) -> tuple[list[PrivateCaseRecipe | PrivateExternalRecipe], list[AbilitySupply]]:
    selected: list[PrivateCaseRecipe | PrivateExternalRecipe] = []
    audits: list[AbilitySupply] = []
    registered = set(registered_abilities)
    for ability in EVALUATOR_ABILITIES:
        if ability not in registered:
            audits.append(AbilitySupply(
                ability=ability, source_families_by_genre={}, eligible_genres=[],
                selected_genres=[], selected_cases=0, blockers=[]))
            continue
        by_genre: dict[
            str,
            dict[str, dict[int, PrivateCaseRecipe | PrivateExternalRecipe]],
        ] = {}
        for item in supplies[ability]:
            family = by_genre.setdefault(item.recipe.genre, {}).setdefault(
                item.family_id, {})
            family[item.branch] = item.recipe
        counts = {
            genre: sum(bool(branches) for branches in families.values())
            for genre, families in by_genre.items()
        }
        eligible = sorted(genre for genre, count in counts.items()
                          if count >= MIN_CASES_PER_GENRE)
        eligible_order = sorted(
            eligible,
            key=lambda genre: _sha256({
                'dataset': dataset_id, 'ability': ability, 'genre': genre,
            }),
        )
        all_genre_order = sorted(
            by_genre,
            key=lambda genre: _sha256({
                'dataset': dataset_id, 'ability': ability,
                'genre': genre, 'all': True,
            }),
        )
        anchor_genres = eligible_order[:MIN_GENRES_PER_ABILITY]
        blockers: list[str] = []
        if len(anchor_genres) < MIN_GENRES_PER_ABILITY:
            detail = ', '.join(f'{genre}={count}' for genre, count in sorted(counts.items()))
            blockers.append(
                f'need {MIN_GENRES_PER_ABILITY} explicit genres with >= '
                f'{MIN_CASES_PER_GENRE} source families; observed [{detail}]')
        if sum(counts.values()) < CASES_PER_ABILITY:
            blockers.append(
                f'need >= {CASES_PER_ABILITY} source families in total; '
                f'observed {sum(counts.values())}')
        ability_selected: list[PrivateCaseRecipe | PrivateExternalRecipe] = []
        if not blockers:
            used_families: set[tuple[str, str]] = set()
            used_subjects: set[str] = set()

            def family_order(genre: str) -> list[str]:
                return sorted(by_genre[genre], key=lambda family_id: _sha256({
                    'dataset': dataset_id, 'ability': ability,
                    'genre': genre, 'family': family_id,
                }))

            def ordered_variants(genre: str, family_id: str):
                branches = by_genre[genre][family_id]
                return [branches[branch] for branch in sorted(branches, key=lambda value: _sha256({
                    'dataset': dataset_id, 'ability': ability,
                    'genre': genre, 'family': family_id, 'branch': value,
                }))]

            def take(genre: str, family_id: str) -> bool:
                key = (genre, family_id)
                if key in used_families:
                    return False
                recipe = next(
                    (row for row in ordered_variants(genre, family_id)
                     if row.subject_sha256 not in used_subjects), None)
                if recipe is None:
                    return False
                used_families.add(key)
                used_subjects.add(recipe.subject_sha256)
                ability_selected.append(recipe)
                return True

            # Reserve the documented minimum in four genres first.  A family
            # is a variant group, not a book: exactly one opaque branch enters
            # the blind catalog.
            for genre in anchor_genres:
                before = len(ability_selected)
                for family_id in family_order(genre):
                    take(genre, family_id)
                    if len(ability_selected) - before == MIN_CASES_PER_GENRE:
                        break
                if len(ability_selected) - before < MIN_CASES_PER_GENRE:
                    blockers.append(
                        f'{genre}: fewer than {MIN_CASES_PER_GENRE} unique rendered subjects')

            remaining = sorted(
                ((genre, family_id) for genre in all_genre_order
                 for family_id in family_order(genre)),
                key=lambda row: _sha256({
                    'dataset': dataset_id, 'ability': ability,
                    'genre': row[0], 'family': row[1], 'fill': True,
                }),
            )
            for genre, family_id in remaining:
                if len(ability_selected) >= CASES_PER_ABILITY:
                    break
                take(genre, family_id)
            if len(ability_selected) < CASES_PER_ABILITY:
                blockers.append(
                    f'only {len(ability_selected)} unique subjects remain after family dedupe; '
                    f'need {CASES_PER_ABILITY}')
            if blockers:
                ability_selected = []
        selected.extend(ability_selected)
        selected_genres = sorted({row.genre for row in ability_selected})
        audits.append(AbilitySupply(
            ability=ability,
            source_families_by_genre=dict(sorted(counts.items())),
            eligible_genres=eligible,
            selected_genres=selected_genres,
            selected_cases=len(ability_selected), blockers=blockers,
        ))
    return selected, audits


def build_catalog(
    corpus_root: Path,
    inventory: PrivateCorpusInventory,
) -> CatalogBuildResult:
    root = corpus_root.resolve()
    if not root.is_dir():
        raise ValueError(f'corpus root is not a directory: {root}')
    books = [_load_book(root, spec) for spec in inventory.books]
    source_cache: dict[str, tuple[Any, str]] = {}
    source_issues = sorted(issue for book in books for issue in book.issues)
    supplies: dict[Ability, list[_DraftRecipe]] = {
        ability: [] for ability in EVALUATOR_ABILITIES
    }
    for book in books:
        generated = _recipes_for_book(
            inventory, root, book, source_cache)
        for ability, rows in generated.items():
            supplies[ability].extend(rows)
    _add_style_recipes(inventory, root, books, supplies, source_cache)
    source_snapshot = sorted({
        (ref.artifact, ref.artifact_sha256)
        for rows in supplies.values()
        for item in rows
        for bundle in item.recipe.bundles
        for ref in bundle.refs
    })
    recipes, ability_audits = _select(inventory.dataset_id, supplies)
    blocked = any(row.blockers for row in ability_audits)
    audit = CatalogBuildAudit(
        builder_version=1, status='blocked' if blocked else 'ready',
        dataset_id=inventory.dataset_id, inventory_sha256=_sha256(inventory),
        source_snapshot_sha256=_sha256(source_snapshot), source_groups=len(books),
        source_issues=source_issues, abilities=ability_audits,
    )
    if blocked:
        return CatalogBuildResult(catalog=None, recipes=(), audit=audit)
    dataset_token = hashlib.sha256(inventory.dataset_id.encode('utf-8')).hexdigest()[:16]
    cases = [CandidateCase(
        case_id=f'{recipe.ability}.{recipe.record_id.removeprefix("case_")}',
        ability=recipe.ability, genre=recipe.genre,
        subject_sha256=recipe.subject_sha256,
        evidence=[EvidenceRef(
            artifact=(
                f'private://ground-truth/{dataset_token}/{recipe.record_id}'),
            subject_sha256=recipe.subject_sha256,
        )],
    ) for recipe in sorted(recipes, key=lambda row: (row.ability, row.record_id))]
    catalog = CandidateCatalog(
        workflow_version=1, dataset_id=inventory.dataset_id,
        rubric_sha256=inventory.rubric_sha256, created_at=inventory.created_at,
        cases=cases,
    )
    return CatalogBuildResult(
        catalog=catalog,
        recipes=tuple(sorted(recipes, key=lambda row: row.record_id)),
        audit=audit,
    )


def _read_private_subject(
    corpus_root: Path,
    locator: PrivateSubjectLocator,
    *,
    ability: Ability | None = None,
) -> RenderedSubject:
    path = _resolve_below(corpus_root, locator.artifact)
    raw = path.read_bytes()
    actual_artifact = _sha256_bytes(raw)
    if actual_artifact != locator.artifact_sha256:
        raise ValueError(
            f'{locator.artifact}: artifact hash mismatch; '
            f'expected {locator.artifact_sha256}, got {actual_artifact}')
    subject = RenderedSubject.model_validate(json.loads(raw.decode('utf-8')))
    if ability is not None and subject.ability != ability:
        raise ValueError(
            f'{locator.artifact}: subject ability {subject.ability!r} != {ability!r}')
    actual_subject = _sha256(subject)
    if actual_subject != locator.subject_sha256:
        raise ValueError(
            f'{locator.artifact}: subject hash mismatch; '
            f'expected {locator.subject_sha256}, got {actual_subject}')
    return subject


def build_bundle_catalog(
    corpus_root: Path,
    inventory: PrivateBundleInventory,
) -> CatalogBuildResult:
    """Build from schema-neutral private subjects extracted by any adapter."""
    root = corpus_root.resolve()
    if not root.is_dir():
        raise ValueError(f'corpus root is not a directory: {root}')
    supplies: dict[Ability, list[_DraftRecipe]] = {
        ability: [] for ability in EVALUATOR_ABILITIES
    }
    issues: list[str] = []
    for family in inventory.families:
        for branch, variant in enumerate(family.variants):
            try:
                _read_private_subject(
                    root, variant.subject, ability=family.ability)
                identity = {
                    'builder_version': CATALOG_BUILDER_VERSION,
                    'adapter': 'private_bundle',
                    'dataset_id': inventory.dataset_id,
                    'family_id': family.family_id,
                    'variant_id': variant.variant_id,
                    'ability': family.ability,
                    'genre': family.genre,
                    'subject': variant.subject.model_dump(mode='json'),
                }
                recipe = PrivateExternalRecipe(
                    recipe_version=1, recipe_kind='external',
                    record_id=f'case_{_sha256(identity)[:24]}',
                    dataset_id=inventory.dataset_id, ability=family.ability,
                    genre=family.genre, family_id=family.family_id,
                    variant_id=variant.variant_id, subject=variant.subject,
                )
                supplies[family.ability].append(_DraftRecipe(
                    family_id=family.family_id, branch=branch, recipe=recipe))
            except (OSError, ValueError, ValidationError, json.JSONDecodeError,
                    UnicodeDecodeError) as exc:
                detail = (f'ValidationError({exc.error_count()} errors)'
                          if isinstance(exc, ValidationError)
                          else f'{type(exc).__name__}: {exc}')
                issues.append(
                    f'{family.family_id}/{variant.variant_id}: {detail}')

    # The schema-neutral adapter registers exactly the abilities named by its
    # families.  An invalid/insufficient registered ability still blocks, but
    # unrelated abilities need no dummy cases merely to publish a partial
    # capability-scoped manifest.
    registered = tuple(sorted({family.ability for family in inventory.families}))
    recipes, ability_audits = _select(
        inventory.dataset_id, supplies, registered)
    blocked = any(row.blockers for row in ability_audits)
    snapshot = sorted({
        (variant.subject.artifact, variant.subject.artifact_sha256,
         variant.subject.subject_sha256)
        for family in inventory.families
        for variant in family.variants
    })
    audit = CatalogBuildAudit(
        builder_version=1, status='blocked' if blocked else 'ready',
        dataset_id=inventory.dataset_id, inventory_sha256=_sha256(inventory),
        source_snapshot_sha256=_sha256(snapshot),
        source_groups=len(inventory.families), source_issues=sorted(issues),
        abilities=ability_audits,
    )
    if blocked:
        return CatalogBuildResult(catalog=None, recipes=(), audit=audit)
    dataset_token = hashlib.sha256(inventory.dataset_id.encode('utf-8')).hexdigest()[:16]
    cases = [CandidateCase(
        case_id=f'{recipe.ability}.{recipe.record_id.removeprefix("case_")}',
        ability=recipe.ability, genre=recipe.genre,
        subject_sha256=recipe.subject_sha256,
        evidence=[EvidenceRef(
            artifact=f'private://ground-truth/{dataset_token}/{recipe.record_id}',
            subject_sha256=recipe.subject_sha256,
        )],
    ) for recipe in sorted(recipes, key=lambda row: (row.ability, row.record_id))]
    catalog = CandidateCatalog(
        workflow_version=1, dataset_id=inventory.dataset_id,
        rubric_sha256=inventory.rubric_sha256, created_at=inventory.created_at,
        cases=cases,
    )
    return CatalogBuildResult(
        catalog=catalog,
        recipes=tuple(sorted(recipes, key=lambda row: row.record_id)),
        audit=audit,
    )


def render_stored_recipe(
    recipe: PrivateCaseRecipe | PrivateExternalRecipe,
    corpus_root: Path,
) -> RenderedSubject:
    if isinstance(recipe, PrivateCaseRecipe):
        return render_recipe(recipe, corpus_root)
    return _read_private_subject(corpus_root, recipe.subject, ability=recipe.ability)


def canonical_subject_bytes(subject: RenderedSubject) -> bytes:
    """Return the exact byte surface bound by ``subject_sha256``.

    Paid/private consumers must not invent a second serialization for the same
    rendered subject.  Keeping this public, tiny adapter next to the catalog
    builder lets a later evaluator independently recompute the manifest hash
    without importing a private implementation detail.
    """
    return _canonical(subject)


def load_stored_recipe(
    artifact: str,
    *,
    case_store: Path,
) -> PrivateCaseRecipe | PrivateExternalRecipe:
    """Resolve one opaque catalog locator to its text-free private recipe."""
    return _recipe_from_uri(artifact, case_store=case_store)


def _load(path: Path, model: type[StrictModel]) -> Any:
    return model.model_validate(json.loads(path.read_text(encoding='utf-8')))


def _rendered_json(value: StrictModel) -> str:
    return json.dumps(
        value.model_dump(mode='json'), ensure_ascii=False, indent=2, sort_keys=True,
    ) + '\n'


def _preflight_write(path: Path, value: StrictModel) -> None:
    if not path.exists():
        return
    current = json.loads(path.read_text(encoding='utf-8'))
    if _canonical(current) != _canonical(value):
        raise FileExistsError(f'refusing to overwrite different output: {path}')


def _write_once(path: Path, value: StrictModel) -> None:
    _preflight_write(path, value)
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    temporary.write_text(_rendered_json(value), encoding='utf-8')
    temporary.replace(path)


def _recipe_from_uri(
    artifact: str,
    *,
    case_store: Path,
) -> PrivateCaseRecipe | PrivateExternalRecipe:
    match = re.fullmatch(
        r'private://ground-truth/([0-9a-f]{16})/(case_[0-9a-f]{24})', artifact)
    if match is None:
        raise ValueError('artifact is not a ground-truth private case locator')
    payload = json.loads(
        (case_store / f'{match.group(2)}.json').read_text(encoding='utf-8'))
    recipe = _STORED_RECIPE_ADAPTER.validate_python(payload)
    dataset_token = hashlib.sha256(recipe.dataset_id.encode('utf-8')).hexdigest()[:16]
    if match.group(1) != dataset_token or recipe.record_id != match.group(2):
        raise ValueError('private case locator is bound to another recipe/dataset')
    return recipe


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)

    def add_build_args(command: argparse.ArgumentParser) -> None:
        command.add_argument('--corpus-root', required=True, type=Path)
        command.add_argument('--inventory', required=True, type=Path)
        command.add_argument('--catalog', required=True, type=Path)
        command.add_argument('--case-store', required=True, type=Path)
        command.add_argument('--audit', required=True, type=Path)

    build = commands.add_parser(
        'build', help='build through the optional current-pipeline artifact adapter')
    add_build_args(build)
    bundles = commands.add_parser(
        'build-bundles', help='build from schema-neutral private case bundles')
    add_build_args(bundles)
    render = commands.add_parser('render', help='render one hash-bound private case to stdout')
    render.add_argument('--corpus-root', required=True, type=Path)
    render.add_argument('--case-store', required=True, type=Path)
    render.add_argument('--artifact', required=True)
    render.add_argument('--subject-sha256', required=True)
    inspect_subject = commands.add_parser(
        'inspect-subject', help='print hashes for one private RenderedSubject JSON')
    inspect_subject.add_argument('--corpus-root', required=True, type=Path)
    inspect_subject.add_argument('--artifact', required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command in {'build', 'build-bundles'}:
            if args.command == 'build':
                inventory = _load(args.inventory, PrivateCorpusInventory)
                result = build_catalog(args.corpus_root, inventory)
            else:
                bundle_inventory = _load(args.inventory, PrivateBundleInventory)
                result = build_bundle_catalog(args.corpus_root, bundle_inventory)
            targets: list[tuple[Path, StrictModel]] = [(args.audit, result.audit)]
            if result.catalog is not None:
                targets.append((args.catalog, result.catalog))
                targets.extend(
                    (args.case_store / f'{row.record_id}.json', row)
                    for row in result.recipes)
            resolved_targets = [path.resolve() for path, _value in targets]
            if len(resolved_targets) != len(set(resolved_targets)):
                raise ValueError('catalog, audit, and case-store targets must be distinct')
            for path, value in targets:
                _preflight_write(path, value)
            for path, value in targets:
                _write_once(path, value)
            print(json.dumps({
                'status': result.audit.status,
                'catalog': str(args.catalog) if result.catalog is not None else None,
                'cases': len(result.catalog.cases) if result.catalog is not None else 0,
                'audit': str(args.audit),
                'blocked_abilities': [
                    row.ability for row in result.audit.abilities if row.blockers],
            }, ensure_ascii=False, sort_keys=True))
            return 0 if result.catalog is not None else 2

        if args.command == 'inspect-subject':
            relative = _relative_path(args.artifact, 'artifact')
            path = _resolve_below(args.corpus_root, relative)
            raw = path.read_bytes()
            subject = RenderedSubject.model_validate(json.loads(raw.decode('utf-8')))
            print(json.dumps({
                'artifact': relative,
                'artifact_sha256': _sha256_bytes(raw),
                'subject_sha256': _sha256(subject),
                'ability': subject.ability,
                'panels': len(subject.panels),
            }, ensure_ascii=False, sort_keys=True))
            return 0

        if not _SHA256_RE.fullmatch(args.subject_sha256):
            raise ValueError('subject-sha256 must be a lowercase SHA-256')
        recipe = _recipe_from_uri(args.artifact, case_store=args.case_store)
        if recipe.subject_sha256 != args.subject_sha256:
            raise ValueError('blind subject hash does not match private recipe')
        rendered = render_stored_recipe(recipe, args.corpus_root)
        actual = _sha256(rendered)
        if actual != recipe.subject_sha256:
            raise ValueError(
                f'rendered subject is stale: expected {recipe.subject_sha256}, got {actual}')
        print(_rendered_json(rendered), end='')
        return 0
    except (OSError, ValueError, ValidationError, json.JSONDecodeError) as exc:
        print(json.dumps({
            'status': 'blocked', 'error': f'{type(exc).__name__}: {exc}',
        }, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
