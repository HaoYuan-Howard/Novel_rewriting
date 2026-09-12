#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 逐章人物引用使用的纯称名 inventory。

这份 inventory 同时供逐章 prompt / 编译校验与 ``consumed`` freshness 投影使用。
前者若自己算一份、后者只抄字段名，最危险的结果不是报错，而是 global 改动已经
改变本章可选 ``char_id``，旧 blueprint 仍被判成 fresh。

本模块不读盘、不校验 schema。freshness 会拿尚未通过当前 schema 的旧 JSON 调它，
因此畸形分支只跳过不可解释的项，不向 ``state`` 查询抛异常。
"""
from __future__ import annotations

from typing import Any, TypedDict

from contracts.schema.stage2 import BlueprintCharacterProjection
from novel.core.text import norm


class BlueprintCharacterPromptInventory(TypedDict):
    past_attested_surfaces: dict[int, list[str]]
    visible_character_refs: dict[int, dict[str, list[str]]]


def character_ref_inventory(
    characters: object,
) -> tuple[
    dict[str, tuple[tuple[str, int | None], ...]],
    dict[str, frozenset[str]],
]:
    """内部分析库存；时间字段描述同人映射，不是称谓的禁用时间。

    Writer 的 ActorRef 隔离由 Gate B 负责，不能将本内部 char_id 映射直接交给 Writer。
    """
    inventory: dict[str, tuple[tuple[str, int | None], ...]] = {}
    if not isinstance(characters, dict):
        return inventory, {}

    for cid, raw_character in characters.items():
        if not isinstance(cid, str) or not isinstance(raw_character, dict):
            continue
        surfaces: list[tuple[str, int | None]] = []
        addresses = raw_character.get('aliases')
        if isinstance(addresses, list):
            for raw_address in addresses:
                if not isinstance(raw_address, dict):
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
    """每章真正会下发的 ``char_id -> surface`` 候选。"""
    return visible_character_refs_with_drops(characters, main_text)[0]


def visible_character_refs_with_drops(
    characters: dict[str, dict[str, Any]],
    main_text: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, list[str]]], dict[int, list[list[str]]]]:
    """返回逐章最终候选，以及因无法区分而整组剔除的 ``char_id``。

    这是分析侧内部实体候选，仅保留截止本章出现的字面；identity_revealed_from
    不能拿来禁用已经出现的称谓。隐秘同人关系的展示与映射由 Gate B 处理。

    最后再按可见字面签名剔除仍无法区分的整组 id。这个「剔除后」结果
    同时是 prompt、编译 validator 与 freshness 的权威；只投影中间 inventory
    会把「时点改了，但最终仍整组 dropped」误报成 stale。
    """
    inventory, _owners = character_ref_inventory(characters)

    out: dict[int, dict[str, list[str]]] = {}
    dropped: dict[int, list[list[str]]] = {}
    cumulative = ''
    for section in main_text:
        ordinal = int(section.get('chapter_ordinal') or 0)
        cumulative += norm(str(section.get('text') or ''))
        visible = {
            cid: [surface for surface, from_chapter in surfaces
                  if norm(surface) in cumulative]
            for cid, surfaces in inventory.items()
        }
        present = {
            cid: surfaces for cid, surfaces in visible.items() if surfaces
        }
        by_signature: dict[frozenset[str], list[str]] = {}
        for cid, surfaces in present.items():
            by_signature.setdefault(
                frozenset(norm(surface) for surface in surfaces), []).append(cid)
        collisions = [sorted(cids) for cids in by_signature.values()
                      if len(cids) > 1]
        if collisions:
            dropped[ordinal] = sorted(collisions)
            indistinguishable = {cid for group in collisions for cid in group}
            present = {cid: surfaces for cid, surfaces in present.items()
                       if cid not in indistinguishable}
        out[ordinal] = present
    return out, dropped


def blueprint_character_prompt_inventory(
    characters: dict[str, dict[str, Any]],
    main_text: list[dict[str, Any]],
) -> BlueprintCharacterPromptInventory:
    """global 中真正改变逐章请求字节的人物称名投影。"""
    # 局部导入避免让 normalize 的其余实现变成本纯函数模块的
    # import-time 依赖；这里仍调用 prompt 真实使用的唯一函数。
    from novel.stages.stage2.normalize import past_attested_surfaces

    visible, _dropped = visible_character_refs_with_drops(characters, main_text)
    return {
        'past_attested_surfaces': past_attested_surfaces(characters, main_text),
        'visible_character_refs': visible,
    }


def materialize_blueprint_character_projection(
    characters: dict[str, dict[str, Any]],
    main_text: list[dict[str, Any]],
) -> BlueprintCharacterProjection:
    """把真实 prompt inventory 物化成注册产物；不增加第三种中间形态。"""
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
    """版本化投影 → prompt/validator/compile 共用的最终下发形态。"""
    artifact = (
        projection
        if isinstance(projection, BlueprintCharacterProjection)
        else BlueprintCharacterProjection.model_validate(projection)
    )
    return {
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
]
