#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook_design —— Hook Design 的确定性校验臂与唯一 publisher（F6）。

【分工】语义判断（选哪些材料、隐藏什么、如何组织）是 Hook Design 一次 LLM
调用的职责；本模块是确定性臂：引用闭合、withheld ⊆ 该拍 delta、时序与披露
自洽、人称组合、称谓释放面、隐藏人物的 presentation 纪律。全部错误聚合返回，
wire 原样进、不修改（I-24）。

【唯一 publisher】
`publish_graph(body, hook)` 是完整 PresentationGraph 的唯一发布点——不存在
「正文设计完成但 Hook 未定」的中间 canonical，也不用空 Hook 默认值冒充完成。
"""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from contracts.schema.str06.design import BodyDesign, PresentationGraph
from contracts.schema.str06.hook import HookDesign
from contracts.schema.str06.source import NameProjection

_WIRE_KEYS = frozenset({
    'pov', 'opening_facts', 'materials', 'concealed', 'surface_releases',
    'forbidden_reveals', 'design_brief', 'delivery_brief'})

Init = 'init'


def _address_key(raw: dict[str, Any] | None) -> tuple | None:
    """wire 的 StateAddress → address() 元组；形状非法返回 None。"""
    if not isinstance(raw, dict) or 'domain' not in raw or 'key' not in raw:
        return None
    return (raw['domain'], tuple(raw.get('subjects') or ()),
            raw.get('holder') or '', raw['key'])


def _position(body: BodyDesign) -> dict[str, tuple[int, int]]:
    return {f'{s.scene_id}#{b.beat_id}': (i, j)
            for i, s in enumerate(body.scenes) for j, b in enumerate(s.beats)}


def _beat(body: BodyDesign, ref: str):
    if ref == Init:
        return None
    scene_id, beat_id = ref.split('#', 1)
    return body.beat(scene_id, beat_id)


def _beat_deltas(body: BodyDesign) -> dict[str, tuple[tuple, ...]]:
    """beat_ref → 该拍全部 delta 地址（address() 元组）。"""
    out: dict[str, tuple[tuple, ...]] = {}
    for s in body.scenes:
        for b in s.beats:
            out[f'{s.scene_id}#{b.beat_id}'] = tuple(d.address() for d in b.target_delta)
    return out


def _established_at(body: BodyDesign, ref: str, key: tuple) -> bool:
    """地址为 key 的事实是否确实在 ref 处建立（init 或该拍 delta）。"""
    if ref == Init:
        return any(d.address() == key for d in body.initial_state)
    return key in (_beat_deltas(body).get(ref) or ())


def _established_value(body: BodyDesign, ref: str, key: tuple) -> str | None:
    """地址在 ref 处建立的 set 值（无则 None）；hook_context 投影用。"""
    deltas: tuple
    if ref == Init:
        deltas = body.initial_state
    else:
        beat = _beat(body, ref)
        deltas = beat.target_delta if beat is not None else ()
    for d in deltas:
        if d.address() == key and d.op == 'set':
            return d.value
    return None


def _delta_subjects(body: BodyDesign, ref: str) -> set[str]:
    beat = _beat(body, ref)
    subjects: set[str] = set()
    if beat is None:
        return subjects
    for d in beat.target_delta:
        subjects.update(d.subjects)
        if d.holder:
            subjects.add(d.holder)
    return subjects


def assemble_hook_design(
    wire: dict[str, Any], *, body: BodyDesign, known_char_ids: set[str],
    name_projection: NameProjection,
) -> tuple[HookDesign | None, list[str]]:
    """Hook Design wire → HookDesign（canonical 候选，待 publish_graph 发布）。"""
    errors: list[str] = []
    extra = set(wire) - _WIRE_KEYS
    if extra:
        errors.append(f'Hook Design wire 携带未知字段 {sorted(extra)}'
                      '（源 Hook 证据、义务字段与自由 state0 文本不收）')
    positions = _position(body)
    deltas = _beat_deltas(body)
    concealed = set(wire.get('concealed') or [])
    for char_id in sorted(concealed - known_char_ids):
        errors.append(f'concealed 引用未知人物：{char_id}')
    concealed &= known_char_ids

    # POV：first 恰一人且非 narrator 由 schema 保；这里保人物真实。
    pov = wire.get('pov') or {}
    for focal in pov.get('focalizers') or ():
        if focal != 'narrator' and focal not in known_char_ids:
            errors.append(f'pov 引用未知人物：{focal}')

    # 材料：withheld ⊆ 该拍 delta；隐藏人物的材料必须 presentation 且不含其字面。
    material_refs: list[str] = []
    for item in wire.get('materials') or []:
        ref = item.get('beat_ref')
        material_refs.append(ref)
        beat_keys = deltas.get(ref)
        if beat_keys is None:
            errors.append(f'材料引用不存在的拍：{ref}')
            continue
        for raw in item.get('withheld') or []:
            key = _address_key(raw)
            if key is None:
                errors.append(f'材料 {ref} 的 withheld 形状非法：{raw}')
            elif key not in beat_keys:
                errors.append(f'材料 {ref} 的 withheld 不属于该拍 delta：{key}')
        hidden_here = ({p for p in (_beat(body, ref).participants if _beat(body, ref) else ())
                        } | _delta_subjects(body, ref)) & concealed
        if hidden_here and not item.get('presentation'):
            errors.append(
                f'材料 {ref} 涉及隐藏人物 {sorted(hidden_here)}，必须给出 presentation')
        if item.get('presentation'):
            for char_id in sorted(hidden_here):
                entry = name_projection.person(char_id)
                literals = (set(entry.proper_literals) | set(entry.name_bearing_surfaces)
                            if entry else set())
                for literal in sorted(literals):
                    if literal and literal in item['presentation']:
                        errors.append(
                            f'材料 {ref} 的 presentation 泄漏隐藏人物 {char_id} 的字面 {literal!r}')

    # 禁止揭示：引用真实；与材料不冲突——同一拍不得既是材料又是禁 reveal；
    # 材料若未以 presentation 或 withheld 隐藏被禁地址，即视为冲突。
    forbidden_refs: list[str] = []
    forbidden_addresses: list[tuple] = []
    for item in wire.get('forbidden_reveals') or []:
        ref = item.get('ref')
        forbidden_refs.append(ref)
        key = _address_key(item.get('address'))
        if item.get('address') is not None:
            if key is None:
                errors.append(f'forbidden_reveals 的 address 形状非法：{item.get("address")}')
                continue
            forbidden_addresses.append(key)
        if ref in material_refs:
            errors.append(f'禁止揭示与材料冲突：同一拍 {ref} 既是材料又禁止揭示')
            continue
        if key is None or key not in (deltas.get(ref) or ()):
            continue
        leaked = False
        for other in wire.get('materials') or []:
            if other.get('beat_ref') != ref:
                continue
            if other.get('presentation'):
                continue
            withheld_keys = {k for k in (_address_key(w) for w in other.get('withheld') or ())
                             if k is not None}
            if key not in withheld_keys:
                leaked = True
        if leaked:
            errors.append(f'禁止揭示与材料冲突：{ref} 的 {key} 被材料未隐藏地呈现')

    # 起笔事实：必须真实建立、时序合法、不在禁止集内、不是任何材料拍的 delta。
    material_positions = [positions.get(r) for r in material_refs]
    timing_known = all(p is not None for p in material_positions) if material_positions else False
    for item in wire.get('opening_facts') or []:
        ref = item.get('ref')
        key = _address_key(item.get('address'))
        if key is None:
            errors.append(f'起笔事实的 address 形状非法：{item.get("address")}')
            continue
        if ref != Init and ref not in positions:
            errors.append(f'起笔事实引用不存在的拍：{ref}')
            continue
        if not _established_at(body, ref, key):
            errors.append(f'起笔事实在 {ref} 未建立：{key}（只能引用已存在的目标事实）')
        if timing_known:
            pos = (-1, -1) if ref == Init else positions[ref]
            audience = item.get('audience')
            if audience in ('world', 'pov') and pos >= min(material_positions):
                errors.append(f'起笔事实（{audience}）必须早于首个材料：{ref}')
            if audience == 'reader' and pos > max(material_positions):
                errors.append(f'起笔事实（reader）不得晚于材料视界：{ref}')
        if key in forbidden_addresses:
            errors.append(f'起笔事实落在禁止揭示集内：{key}')
        for other_ref in dict.fromkeys(material_refs):
            if other_ref in deltas and key in deltas[other_ref]:
                errors.append(f'起笔事实是材料拍 {other_ref} 的 delta：{key}')

    # 称谓释放：必须是 Design 已声明的面，且不属于隐藏人物。
    declared = body.declared_surfaces()
    for item in wire.get('surface_releases') or []:
        char_id = item.get('char_id')
        faces = declared.get(char_id, set())
        for surface in item.get('surfaces') or ():
            if surface not in faces:
                errors.append(f'释放的称谓 {surface!r} 不是 {char_id} 已声明的面')
        if char_id in concealed:
            errors.append(f'称谓释放针对隐藏人物 {char_id}，拒绝')

    if errors:
        return None, errors
    try:
        return HookDesign.model_validate(wire), []
    except ValidationError as exc:
        return None, [f'hook_design 构造失败：{exc}']


def publish_graph(body: BodyDesign, hook: HookDesign) -> PresentationGraph:
    """完整 PresentationGraph 的唯一 publisher。"""
    payload = body.model_dump()
    payload['hook'] = hook.model_dump()
    return PresentationGraph.model_validate(payload)
