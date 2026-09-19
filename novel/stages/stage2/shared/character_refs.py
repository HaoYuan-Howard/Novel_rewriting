#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 逐章人物引用使用的纯称名 inventory（Characters v3）。

这份 inventory 同时供逐章 prompt / 编译校验与 ``consumed`` freshness 投影使用。
本模块不读盘、不校验 schema。freshness 会拿尚未通过当前 schema 的旧 JSON 调它，
因此畸形分支只跳过不可解释的项，不向 ``state`` 查询抛异常。
"""
from __future__ import annotations

from typing import Any, TypedDict
from collections.abc import Mapping, Sequence

from contracts.schema.characters import BOOK_LEVEL_SURFACE_KINDS, NAME_LICENSE_KINDS
from contracts.schema.stage2 import BlueprintCharacterProjection
from novel.core.text import norm


class BlueprintCharacterPromptInventory(TypedDict):
    past_attested_surfaces: dict[int, list[str]]
    visible_character_refs: dict[int, dict[str, list[str]]]
    character_bindings: dict[int, dict[str, list[dict[str, Any]]]]


def _surface_rows(character: Mapping[str, Any]) -> list[dict[str, Any]]:
    """兼容 v3 ``surfaces`` 与存量 ``aliases`` 形状。"""
    surfaces = character.get('surfaces')
    if isinstance(surfaces, list):
        return [row for row in surfaces if isinstance(row, dict)]
    aliases = character.get('aliases')
    if isinstance(aliases, list):
        return [row for row in aliases if isinstance(row, dict)]
    return []


def _first_mention(character: Mapping[str, Any]) -> int | None:
    first = character.get('first_mention_chapter')
    if isinstance(first, int) and first > 0:
        return first
    legacy = character.get('first_chapter')
    if isinstance(legacy, int) and legacy > 0:
        return legacy
    return None


def _name_usable_from(character: Mapping[str, Any]) -> int | None:
    value = character.get('name_usable_from')
    if isinstance(value, int) and value > 0:
        return value
    return None


def null_binding_conflict(names: Sequence[Mapping[str, Any]], surfaces: Sequence[str], *,
                          chapter_ordinal: int) -> bool:
    """唯一显式绑定不能吸收另一个既有公开身份。"""
    selected = set(surfaces)
    return (any(row.get('identity_revealed_from', 0) >= chapter_ordinal for row in names)
            and any(row.get('surface') not in selected for row in names))


def character_ref_inventory(
    characters: object,
) -> tuple[
    dict[str, tuple[tuple[str, int | None], ...]],
    dict[str, frozenset[str]],
]:
    """内部分析库存；时间字段描述同人映射，不是称谓的禁用时间。"""
    inventory: dict[str, tuple[tuple[str, int | None], ...]] = {}
    if not isinstance(characters, dict):
        return inventory, {}

    for cid, raw_character in characters.items():
        if not isinstance(cid, str) or not isinstance(raw_character, dict):
            continue
        surfaces: list[tuple[str, int | None]] = []
        for raw_address in _surface_rows(raw_character):
            kind = raw_address.get('kind')
            # 关系称谓不进全书 alias 投影；存量 aliases 无 kind 时保留。
            if kind == 'relational_address':
                continue
            if kind is not None and kind not in BOOK_LEVEL_SURFACE_KINDS:
                continue
            chapter = raw_address.get('identity_revealed_from')
            surfaces.append((
                str(raw_address.get('surface') or '').strip(),
                chapter if isinstance(chapter, int) and chapter > 0 else 1,
            ))

        distinct: dict[str, tuple[str, int | None]] = {}
        for surface, from_chapter in surfaces:
            key = norm(surface)
            if not key:
                continue
            if key not in distinct:
                distinct[key] = (surface, from_chapter)
        inventory[cid] = tuple(distinct.values())

    owners: dict[str, set[str]] = {}
    for cid, surfaces in inventory.items():
        for surface, _from_chapter in surfaces:
            owners.setdefault(norm(surface), set()).add(cid)
    return inventory, {key: frozenset(cids) for key, cids in owners.items()}


def visible_character_refs(
    characters: dict[str, dict[str, Any]],
    main_text: list[dict[str, Any]],
) -> dict[int, dict[str, list[str]]]:
    return visible_character_refs_with_drops(characters, main_text)[0]


def visible_character_refs_with_drops(
    characters: dict[str, dict[str, Any]],
    main_text: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, list[str]]], dict[int, list[list[str]]]]:
    """返回逐章最终候选，以及需要原文语义消歧的同名字面组。

    姓名类 surface 受 ``name_usable_from`` 约束；准入实体看 ``first_mention_chapter``。
    """
    inventory, _owners = character_ref_inventory(characters)

    out: dict[int, dict[str, list[str]]] = {}
    dropped: dict[int, list[list[str]]] = {}
    cumulative = ''
    for section in main_text:
        ordinal = int(section.get('chapter_ordinal') or 0)
        cumulative += norm(str(section.get('text') or ''))
        visible: dict[str, list[str]] = {}
        for cid, surfaces in inventory.items():
            character = characters.get(cid) or {}
            name_from = _name_usable_from(character)
            allowed: list[str] = []
            for surface, from_chapter in surfaces:
                if norm(surface) not in cumulative:
                    continue
                # 姓名类：未到 name_usable_from 前不下发该 surface
                row_kinds = {
                    str(row.get('surface')): row.get('kind')
                    for row in _surface_rows(character)
                }
                kind = row_kinds.get(surface)
                if kind in NAME_LICENSE_KINDS and name_from is not None and ordinal < name_from:
                    continue
                if from_chapter is not None and from_chapter > ordinal and kind in BOOK_LEVEL_SURFACE_KINDS:
                    # identity 未揭示仍可出现 surface；投影仍保留字面，映射时点另存
                    pass
                allowed.append(surface)
            if allowed:
                visible[cid] = allowed
        present = {
            cid: surfaces for cid, surfaces in visible.items()
            if (first := _first_mention(characters[cid])) is None or first <= ordinal
        }
        for cid, row in characters.items():
            first = _first_mention(row)
            if cid in present:
                continue
            if first is not None and first <= ordinal:
                present[cid] = []
        by_signature: dict[frozenset[str], list[str]] = {}
        for cid, surfaces in present.items():
            if not surfaces:
                continue
            by_signature.setdefault(
                frozenset(norm(surface) for surface in surfaces), []).append(cid)
        collisions = [sorted(cids) for cids in by_signature.values()
                      if len(cids) > 1]
        if collisions:
            dropped[ordinal] = sorted(collisions)
        out[ordinal] = present
    return out, dropped


def blueprint_character_prompt_inventory(
    characters: dict[str, dict[str, Any]],
    main_text: list[dict[str, Any]],
) -> BlueprintCharacterPromptInventory:
    from novel.stages.stage2.shared.normalize import past_attested_surfaces

    visible, _dropped = visible_character_refs_with_drops(characters, main_text)
    inventory, _ = character_ref_inventory(characters)
    bindings = {ordinal: {cid: [
        {'surface': surface, 'identity_revealed_from': next(
            (chapter for candidate, chapter in inventory[cid] if candidate == surface),
            1)}
        for surface in surfaces] for cid, surfaces in members.items()}
        for ordinal, members in visible.items()}
    return {
        'character_bindings': bindings,
        'past_attested_surfaces': past_attested_surfaces(characters, main_text),
        'visible_character_refs': visible,
    }


def materialize_blueprint_character_projection(
    characters: dict[str, dict[str, Any]],
    main_text: list[dict[str, Any]],
) -> BlueprintCharacterProjection:
    inventory = blueprint_character_prompt_inventory(characters, main_text)
    past = inventory['past_attested_surfaces']
    visible = inventory['visible_character_refs']
    ordinals = sorted({
        int(section.get('chapter_ordinal') or 0)
        for section in main_text
        if int(section.get('chapter_ordinal') or 0) > 0
    })
    return BlueprintCharacterProjection.model_validate({
        'chapters': [
            {
                'chapter_ordinal': ordinal,
                'character_bindings': inventory['character_bindings'].get(ordinal, {}),
                'past_attested_surfaces': list(past.get(ordinal, [])),
                'visible_character_refs': {
                    cid: list(surfaces)
                    for cid, surfaces in visible.get(ordinal, {}).items()
                },
            }
            for ordinal in ordinals
        ],
    })


def blueprint_inventory_from_projection(
    projection: BlueprintCharacterProjection | dict[str, Any],
) -> BlueprintCharacterPromptInventory:
    artifact = (
        projection
        if isinstance(projection, BlueprintCharacterProjection)
        else BlueprintCharacterProjection.model_validate(projection)
    )
    return {
        'character_bindings': {row.chapter_ordinal: {cid: [
            binding.model_dump(mode='json') for binding in bindings]
            for cid, bindings in row.character_bindings.items()}
            for row in artifact.chapters},
        'past_attested_surfaces': {
            row.chapter_ordinal: list(row.past_attested_surfaces)
            for row in artifact.chapters
        },
        'visible_character_refs': {
            row.chapter_ordinal: {
                cid: list(surfaces)
                for cid, surfaces in row.visible_character_refs.items()
            }
            for row in artifact.chapters
        },
    }


__all__ = [
    'BlueprintCharacterPromptInventory', 'blueprint_character_prompt_inventory',
    'blueprint_inventory_from_projection',
    'character_ref_inventory',
    'materialize_blueprint_character_projection',
    'visible_character_refs', 'visible_character_refs_with_drops',
    'null_binding_conflict',
]
