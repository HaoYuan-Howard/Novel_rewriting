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
from collections.abc import Mapping, Sequence

from contracts.schema.stage2 import BlueprintCharacterProjection
from novel.core.text import norm


class BlueprintCharacterPromptInventory(TypedDict):
    past_attested_surfaces: dict[int, list[str]]
    visible_character_refs: dict[int, dict[str, list[str]]]
    character_bindings: dict[int, dict[str, list[dict[str, Any]]]]


def null_binding_conflict(names: Sequence[Mapping[str, Any]], surfaces: Sequence[str], *,
                          chapter_ordinal: int) -> bool:
    """唯一显式绑定不能吸收另一个既有公开身份。

    names 必须来自截至使用章已出现的登记称谓候选。若存在绑定外称谓，且候选中的身份
    映射本章或未来才揭示，null 仍有两种解释；新旧绑定两个方向使用同一判据。这里只拒绝歧义，
    不猜测对应关系、铸造历史绑定或代拟同章披露安排。
    """
    selected = set(surfaces)
    return (any(row['identity_revealed_from'] >= chapter_ordinal for row in names)
            and any(row['surface'] not in selected for row in names))


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
    """返回逐章最终候选，以及需要原文语义消歧的同名字面组。

    这是分析侧内部实体候选，仅保留截止本章出现的字面；identity_revealed_from
    不能拿来禁用已经出现的称谓。隐秘同人关系的展示与映射由 Gate B 处理。

    同名字面仍保留全部实体候选；歧义交给 Blueprint 归属审阅，不能以删除人物消除。
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
            and (not isinstance(characters[cid].get('first_chapter'), int)
                 or characters[cid]['first_chapter'] <= ordinal)
        }
        # 已到首次出场章、但登记名尚未字面出现（常见于第一人称「我」）：
        # 仍进入 ActorRef 闭集，surfaces 为空——可引用 char_id，不可造未证实的 binding surface。
        for cid, row in characters.items():
            first = row.get('first_chapter')
            if cid in present:
                continue
            if isinstance(first, int) and first <= ordinal:
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
    """global 中真正改变逐章请求字节的人物称名投影。"""
    # 局部导入避免让 normalize 的其余实现变成本纯函数模块的
    # import-time 依赖；这里仍调用 prompt 真实使用的唯一函数。
    from novel.stages.stage2.shared.normalize import past_attested_surfaces

    visible, _dropped = visible_character_refs_with_drops(characters, main_text)
    inventory, _ = character_ref_inventory(characters)
    bindings = {ordinal: {cid: [
        {'surface': surface, 'identity_revealed_from': next(
            chapter for candidate, chapter in inventory[cid] if candidate == surface)}
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
    """版本化投影 → prompt/validator/compile 共用的最终下发形态。"""
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
