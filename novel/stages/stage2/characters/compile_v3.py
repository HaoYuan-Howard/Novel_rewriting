# -*- coding: utf-8 -*-
"""Characters v3 compiler：lexicon Resolve 展开到 occurrence + 派生投影。

流程：候选闭集 + 嵌套预排除 + surface_bindings → mentions → RegisteredCharacter。
``name_distinctiveness`` 由 surface kind 派生，不由模型填写。
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from contracts.schema.characters import (
    BOOK_LEVEL_SURFACE_KINDS, NAME_LICENSE_KINDS, BookResolveResponse,
    CharacterRegistry, MentionCandidate, MentionDisposition, MentionId,
    MentionKind, MentionRecord, RegisteredCharacter, SurfaceBinding,
    SurfaceBindingScope, SurfaceRecord,
)
from novel.stages.stage2.characters.candidates import (
    NESTED_EXCLUDE_REASON, NestedExclusion,
)
from novel.stages.stage2.shared.core_evidence import evidence_id

NameDistinctiveness = Literal['high', 'medium', 'common']


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _anchor(chapter: int, start: int, end: int, text: str) -> dict[str, Any]:
    return {
        'chapter_ordinal': chapter,
        'start': start,
        'end': end,
        'text_sha256': _sha(text),
    }


def _assign_character_ids(refs: Sequence[str]) -> dict[str, str]:
    ordered = sorted(refs, key=lambda ref: int(ref[1:]))
    return {ref: f'char_{index:03d}' for index, ref in enumerate(ordered, start=1)}


def _chapter_text(
    main_text: Sequence[Mapping[str, Any]],
) -> dict[int, str]:
    texts: dict[int, str] = {}
    for section in main_text:
        ordinal = section.get('chapter_ordinal')
        text = section.get('text')
        if type(ordinal) is not int or ordinal < 1 or not isinstance(text, str):
            raise ValueError('MainText 章号与正文非法')
        if ordinal in texts:
            raise ValueError(f'章号重复：{ordinal}')
        texts[ordinal] = text
    return texts


def derive_name_distinctiveness(kinds: Sequence[MentionKind]) -> NameDistinctiveness:
    """proper_name→high / short_name→medium / 其余 common。"""
    kind_set = set(kinds)
    if 'proper_name' in kind_set:
        return 'high'
    if 'short_name' in kind_set:
        return 'medium'
    return 'common'


def _scope_matches(scope: SurfaceBindingScope, candidate: MentionCandidate) -> bool:
    if scope.mention_id is not None:
        return scope.mention_id == candidate.mention_id
    if scope.chapter_ordinal != candidate.chapter_ordinal:
        return False
    if scope.start is None:
        return True
    assert scope.end is not None
    return scope.start == candidate.start and scope.end == candidate.end


def _binding_applies(binding: SurfaceBinding, candidate: MentionCandidate) -> bool:
    if binding.surface != candidate.surface:
        return False
    if not binding.scopes:
        return True
    return any(_scope_matches(scope, candidate) for scope in binding.scopes)


def _select_binding(
    bindings: Sequence[SurfaceBinding],
    candidate: MentionCandidate,
) -> SurfaceBinding | None:
    matches = [row for row in bindings if _binding_applies(row, candidate)]
    if not matches:
        return None
    # scoped 优先于全书默认；同级多条 → fail-closed
    scoped = [row for row in matches if row.scopes]
    pool = scoped or matches
    if len(pool) > 1:
        # 同 surface 多实体未分区
        refs = {row.character_ref for row in pool if row.character_ref}
        if len(refs) > 1 or len(pool) > 1:
            raise ValueError(
                f'{candidate.mention_id}: surface {candidate.surface!r} '
                f'存在未分区的多重 binding（{len(pool)} 条）')
    return pool[0]


def _ensure_surface_coverage(
    *,
    resolve_surfaces: Sequence[str],
    bindings: Sequence[SurfaceBinding],
) -> None:
    """每个未预排除的 distinct surface 必须至少有一条 binding。"""
    covered = {row.surface for row in bindings}
    missing = sorted(set(resolve_surfaces) - covered)
    if missing:
        raise ValueError(f'Resolve 缺少 surface binding：{missing}')


def compile_registry_v3(
    *,
    main_text: Sequence[Mapping[str, Any]],
    candidates: Sequence[MentionCandidate],
    resolve: BookResolveResponse,
    nested_exclusions: Sequence[NestedExclusion] = (),
) -> dict[str, Any]:
    """将 lexicon Resolve 展开为正式 CharacterRegistry 的业务载荷（无 provenance）。

    返回可 ``CharacterRegistry.model_validate({**payload, '_provenance': ...})`` 的 dict。
    """
    texts = _chapter_text(main_text)
    by_id = {row.mention_id: row for row in candidates}
    if len(by_id) != len(candidates):
        raise ValueError('mention_id 在候选集中必须唯一')

    excluded_nested = {row.mention_id: row for row in nested_exclusions}
    unknown_nested = sorted(set(excluded_nested) - set(by_id))
    if unknown_nested:
        raise ValueError(f'预排除指向未知 mention：{unknown_nested}')

    resolve_candidates = [
        row for row in candidates if row.mention_id not in excluded_nested
    ]
    resolve_surfaces = sorted({row.surface for row in resolve_candidates})
    _ensure_surface_coverage(
        resolve_surfaces=resolve_surfaces,
        bindings=resolve.surface_bindings,
    )

    ref_to_id = _assign_character_ids(
        [row.character_ref for row in resolve.characters])
    seed_by_ref = {row.character_ref: row for row in resolve.characters}

    mentions: dict[str, MentionRecord] = {}
    anchors: dict[str, dict[str, Any]] = {}
    bound_by_char: dict[str, list[MentionRecord]] = defaultdict(list)

    for candidate in candidates:
        body = texts.get(candidate.chapter_ordinal)
        if body is None:
            raise ValueError(f'缺少 ch{candidate.chapter_ordinal} 正文')
        snippet = body[candidate.start:candidate.end]
        if snippet != candidate.surface:
            raise ValueError(
                f'{candidate.mention_id}: 原文切片与 surface 不一致')
        if _sha(snippet) != candidate.text_sha256:
            raise ValueError(f'{candidate.mention_id}: text_sha256 与原文不符')
        anchor = _anchor(
            candidate.chapter_ordinal, candidate.start, candidate.end, snippet)
        eid = evidence_id(anchor)
        anchors[eid] = anchor

        if candidate.mention_id in excluded_nested:
            record = MentionRecord(
                mention_id=candidate.mention_id,
                chapter_ordinal=candidate.chapter_ordinal,
                start=candidate.start,
                end=candidate.end,
                surface=candidate.surface,
                kind='descriptive',
                disposition='excluded',
                char_id=None,
                exclude_reason=NESTED_EXCLUDE_REASON,
                reveals_identity=False,
                evidence_refs=[eid],
            )
            mentions[record.mention_id] = record
            continue

        binding = _select_binding(resolve.surface_bindings, candidate)
        if binding is None:
            raise ValueError(
                f'{candidate.mention_id}: surface {candidate.surface!r} '
                f'无适用 binding')

        char_id = None
        if binding.disposition == 'bound':
            assert binding.character_ref is not None
            if binding.character_ref not in ref_to_id:
                raise ValueError(
                    f'{candidate.mention_id}: 未知 character_ref '
                    f'{binding.character_ref}')
            char_id = ref_to_id[binding.character_ref]

        reveals = False
        if (
            binding.disposition == 'bound'
            and binding.identity_revealed_from is not None
            and binding.kind in BOOK_LEVEL_SURFACE_KINDS
        ):
            prior = [
                row for row in mentions.values()
                if row.char_id == char_id
                and row.surface == candidate.surface
                and row.disposition == 'bound'
                and row.reveals_identity
            ]
            if not prior and candidate.chapter_ordinal >= binding.identity_revealed_from:
                reveals = True

        record = MentionRecord(
            mention_id=candidate.mention_id,
            chapter_ordinal=candidate.chapter_ordinal,
            start=candidate.start,
            end=candidate.end,
            surface=candidate.surface,
            kind=binding.kind,
            disposition=binding.disposition,
            char_id=char_id,
            exclude_reason=binding.exclude_reason,
            reveals_identity=reveals,
            evidence_refs=[eid],
        )
        mentions[record.mention_id] = record
        if char_id is not None:
            bound_by_char[char_id].append(record)

    characters: dict[str, RegisteredCharacter] = {}
    for ref, char_id in ref_to_id.items():
        seed = seed_by_ref[ref]
        rows = bound_by_char.get(char_id, [])
        if seed.status == 'confirmed' and not rows:
            raise ValueError(f'{char_id}: 已确认人物至少需要一条 bound mention')
        first_mention = min((row.chapter_ordinal for row in rows), default=None)
        if first_mention is None:
            raise ValueError(f'{char_id}: 无法派生 first_mention_chapter')
        name_chapters = [
            row.chapter_ordinal for row in rows if row.kind in NAME_LICENSE_KINDS
        ]
        name_usable = min(name_chapters) if name_chapters else None
        surface_map: dict[str, SurfaceRecord] = {}
        for row in rows:
            if row.kind not in BOOK_LEVEL_SURFACE_KINDS:
                continue
            revealed = None
            if row.reveals_identity:
                revealed = row.chapter_ordinal
            else:
                for binding in resolve.surface_bindings:
                    if (
                        binding.surface == row.surface
                        and binding.character_ref == ref
                        and binding.identity_revealed_from is not None
                    ):
                        revealed = binding.identity_revealed_from
                        break
            existing = surface_map.get(row.surface)
            if existing is None:
                surface_map[row.surface] = SurfaceRecord(
                    surface=row.surface,
                    kind=row.kind,
                    identity_revealed_from=revealed,
                )
                continue
            if existing.kind != row.kind:
                raise ValueError(
                    f'{char_id}: surface {row.surface!r} 的 kind 冲突')
            if revealed is not None:
                prior = existing.identity_revealed_from
                surface_map[row.surface] = SurfaceRecord(
                    surface=row.surface,
                    kind=row.kind,
                    identity_revealed_from=(
                        revealed if prior is None else min(prior, revealed)),
                )
        if not any(row.surface == seed.canonical_surface for row in rows):
            raise ValueError(
                f'{char_id}: canonical_surface 必须至少有一条同字面 bound mention')
        distinctiveness = derive_name_distinctiveness([row.kind for row in rows])
        characters[char_id] = RegisteredCharacter(
            canonical_surface=seed.canonical_surface,
            status=seed.status,
            first_mention_chapter=first_mention,
            name_usable_from=name_usable,
            surfaces=sorted(surface_map.values(), key=lambda row: row.surface),
            name_distinctiveness=distinctiveness,
        )

    return {
        'characters': {
            cid: row.model_dump(mode='json') for cid, row in characters.items()
        },
        'mentions': {
            mid: row.model_dump(mode='json') for mid, row in mentions.items()
        },
        'evidence_anchors': anchors,
    }


# 兼容旧名
compile_registry = compile_registry_v3


def recompute_character_projections(
    characters: dict[str, Any],
    mentions: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """编辑后由 mentions 重算 first_mention / name_usable / surfaces / distinctiveness。

    不改动 mentions；不猜测缺失绑定。无 bound mention 的实体保留空 surfaces，
    由后续 checks 拦截。
    """
    bound_by_char: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in mentions.values():
        if row.get('disposition') != 'bound':
            continue
        cid = row.get('char_id')
        if isinstance(cid, str):
            bound_by_char[cid].append(row)

    out: dict[str, Any] = {}
    for cid, raw in characters.items():
        rows = bound_by_char.get(cid, [])
        first_mention = min(
            (int(row['chapter_ordinal']) for row in rows
             if isinstance(row.get('chapter_ordinal'), int)),
            default=None,
        )
        name_chapters = [
            int(row['chapter_ordinal']) for row in rows
            if row.get('kind') in NAME_LICENSE_KINDS
            and isinstance(row.get('chapter_ordinal'), int)
        ]
        name_usable = min(name_chapters) if name_chapters else None
        surface_map: dict[str, SurfaceRecord] = {}
        for row in rows:
            kind = row.get('kind')
            surface = row.get('surface')
            if kind not in BOOK_LEVEL_SURFACE_KINDS or not isinstance(surface, str):
                continue
            revealed = row['chapter_ordinal'] if row.get('reveals_identity') else None
            if not isinstance(revealed, int):
                revealed = None
            existing = surface_map.get(surface)
            if existing is None:
                surface_map[surface] = SurfaceRecord(
                    surface=surface, kind=kind, identity_revealed_from=revealed)
                continue
            if existing.kind != kind:
                raise ValueError(f'{cid}: surface {surface!r} 的 kind 冲突')
            if revealed is not None:
                prior = existing.identity_revealed_from
                surface_map[surface] = SurfaceRecord(
                    surface=surface,
                    kind=kind,
                    identity_revealed_from=(
                        revealed if prior is None else min(prior, revealed)),
                )
        kinds = [str(row.get('kind')) for row in rows if row.get('kind')]
        distinctiveness = derive_name_distinctiveness(kinds)  # type: ignore[arg-type]
        if first_mention is None:
            first_mention = int(raw.get('first_mention_chapter') or 1)
        out[cid] = {
            **raw,
            'first_mention_chapter': first_mention,
            'name_usable_from': name_usable,
            'surfaces': [
                row.model_dump(mode='json')
                for row in sorted(surface_map.values(), key=lambda item: item.surface)
            ],
            'name_distinctiveness': distinctiveness,
        }
    return out


def name_usable_in_chapter(character: RegisteredCharacter, chapter: int) -> bool:
    if character.name_usable_from is None:
        return False
    return character.name_usable_from <= chapter


def mentioned_in_chapter(
    registry: CharacterRegistry, char_id: str, chapter: int,
) -> bool:
    return any(
        row.char_id == char_id
        and row.disposition == 'bound'
        and row.chapter_ordinal == chapter
        for row in registry.mentions.values()
    )


__all__ = [
    'compile_registry',
    'compile_registry_v3',
    'derive_name_distinctiveness',
    'mentioned_in_chapter',
    'name_usable_in_chapter',
    'recompute_character_projections',
]
