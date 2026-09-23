#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""design_compile —— Design wire → PresentationGraph v1 的确定性组装（F3）。

【职责】compiler：LLM 只产语义内容（scenes 的驱动/拍/交付、归章、权重）；
本模块做形状闭合：beat_id 场内唯一且 `<scene>#<beat>` 可寻址、participants
引用 IdentityProjection 的 char_id、target_delta 域闭集（schema 层），
章号连续/全覆盖/非空（schema 层）。Source-blind 由 schema 的 extra=forbid
与「prompt 只给 Brief/Identity/Constraints」双重保证。
"""
from __future__ import annotations

from typing import Any

from contracts.schema.str06.design import (
    ChapterAssignment, PresentationGraph, SceneDelivery, SceneDriver,
    TargetBeat, TargetDelta, TargetScene,
)
from pydantic import ValidationError

#: TargetDelta.domain 的闭集（与 EffectDomain 同名异层：Design 层用字符串，
#: compile 到 execution 时才进 EffectDomain 枚举）
DESIGN_DELTA_DOMAINS = frozenset({'character', 'relation', 'knowledge', 'world', 'reader'})

#: wire 键白名单（Q-1：上游多写的键在这里报错，而不是被静默丢弃——
#: source_ref 类混入正是靠这一层在 compile 入口被拒）
_SCENE_KEYS = frozenset({'scene_id', 'driver', 'beats', 'delivery'})
_BEAT_KEYS = frozenset({'beat_id', 'event', 'participants', 'dramatic_intent',
                        'target_delta', 'delivery_hint'})
_DRIVER_KEYS = frozenset({'focal_actor', 'immediate_want', 'opposition', 'turn'})
_DELIVERY_KEYS = frozenset({'pov', 'voice', 'avoid'})
_DELTA_KEYS = frozenset({'domain', 'key', 'value'})
_CHAPTER_KEYS = frozenset({'chapter_ordinal', 'scene_ids'})


def _reject_unknown_keys(errors: list[str], obj: dict[str, Any], allowed: frozenset[str],
                         where: str) -> None:
    extra = set(obj) - allowed
    if extra:
        errors.append(f'{where} 携带未知字段 {sorted(extra)}（wire 键白名单外，拒绝以防空义混入）')


def assemble_presentation_graph(
    wire: dict[str, Any], *, known_char_ids: set[str],
) -> tuple[PresentationGraph | None, list[str]]:
    """Design wire → PresentationGraph。wire 形状：
    {'scenes': [{scene_id, driver{...}, beats[{beat_id,event,participants,
    dramatic_intent,target_delta[],delivery_hint}], delivery{pov,voice,avoid[]}}],
     'chapters': [{chapter_ordinal, scene_ids[]}], 'chapter_weights': {scene: w}}"""
    errors: list[str] = []
    for scene in wire.get('scenes') or []:
        sid = scene.get('scene_id')
        _reject_unknown_keys(errors, scene, _SCENE_KEYS, f'scene {sid}')
        _reject_unknown_keys(errors, scene.get('driver') or {}, _DRIVER_KEYS, f'scene {sid}.driver')
        _reject_unknown_keys(errors, scene.get('delivery') or {}, _DELIVERY_KEYS,
                             f'scene {sid}.delivery')
        beat_ids = [b.get('beat_id') for b in scene.get('beats') or []]
        if len(set(beat_ids)) != len(beat_ids):
            errors.append(f'scene {sid} 内 beat_id 重复')
        for beat in scene.get('beats') or []:
            _reject_unknown_keys(errors, beat, _BEAT_KEYS, f'beat {beat.get("beat_id")}')
            for p in beat.get('participants') or []:
                if p not in known_char_ids:
                    errors.append(f'beat {beat.get("beat_id")} participants 引用未知 char_id {p}')
            for delta in beat.get('target_delta') or []:
                _reject_unknown_keys(errors, delta, _DELTA_KEYS,
                                     f'beat {beat.get("beat_id")}.target_delta')
                if delta.get('domain') not in DESIGN_DELTA_DOMAINS:
                    errors.append(f'target_delta.domain 非法：{delta.get("domain")}')
        pov = (scene.get('delivery') or {}).get('pov')
        if pov and pov not in known_char_ids and pov != 'narrator':
            errors.append(f'scene {sid} pov 引用未知 char_id {pov}')
    scene_ids = {s.get('scene_id') for s in wire.get('scenes') or []}
    for chapter in wire.get('chapters') or []:
        _reject_unknown_keys(errors, chapter, _CHAPTER_KEYS,
                             f'chapter {chapter.get("chapter_ordinal")}')
        for sid in chapter.get('scene_ids') or []:
            if sid not in scene_ids:
                errors.append(f'归章引用未知场景 {sid}')
    if errors:
        return None, errors
    try:
        graph = PresentationGraph(
            graph_id='pg_main',
            scenes=tuple(
                TargetScene(
                    scene_id=s['scene_id'],
                    driver=SceneDriver(**s['driver']),
                    beats=tuple(
                        TargetBeat(
                            beat_id=b['beat_id'], event=b['event'],
                            participants=tuple(b.get('participants') or ()),
                            dramatic_intent=b['dramatic_intent'],
                            target_delta=tuple(
                                TargetDelta(**d) for d in b.get('target_delta') or ()),
                            delivery_hint=b.get('delivery_hint') or '')
                        for b in s['beats']),
                    delivery=SceneDelivery(**s['delivery']))
                for s in wire['scenes']),
            chapters=tuple(
                ChapterAssignment(
                    chapter_ordinal=c['chapter_ordinal'],
                    scene_ids=tuple(c['scene_ids']))
                for c in wire['chapters']),
            chapter_weights={
                k: float(v) for k, v in (wire.get('chapter_weights') or {}).items()})
    except (ValidationError, KeyError, TypeError) as exc:
        return None, [f'presentation_graph 构造失败：{exc}']
    return graph, []
