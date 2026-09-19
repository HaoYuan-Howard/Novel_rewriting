"""书级 narration_default 确定性上提与章级有效叙述合并。

书级默认只存可跨章复用的人称与 char_id；不得携带章级 presentation_ref。
章级 narration 列表保留拍级 beat_refs；表面身份按使用章解析。
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from contracts.schema.plot import (
    ActorRef, NarrationDefault, PlotBlueprint, PlotNarration,
)


def _char_id(actor: ActorRef | None) -> str | None:
    return None if actor is None else actor.char_id


def narration_fingerprint(row: PlotNarration) -> tuple[Any, ...]:
    return (
        row.grammatical_person,
        _char_id(row.narrator),
        _char_id(row.focalizer),
    )


def derive_narration_default(
    chapters: Sequence[PlotBlueprint],
) -> NarrationDefault | None:
    """从已编译各章叙述确定性聚合；无一致配置则 None（不加 LLM）。"""
    fingerprints: list[tuple[Any, ...]] = []
    for chapter in chapters:
        if not chapter.narration:
            continue
        chapter_fps = [narration_fingerprint(row) for row in chapter.narration]
        if len(set(chapter_fps)) != 1:
            return None
        fingerprints.append(chapter_fps[0])
    if not fingerprints or len(set(fingerprints)) != 1:
        return None
    person, narrator, focalizer = fingerprints[0]
    return NarrationDefault(
        grammatical_person=person,
        narrator_char_id=narrator,
        focalizer_char_id=focalizer,
    )


def synthesize_default_narration(
    default: NarrationDefault,
    *,
    evidence_refs: Sequence[str],
    pronoun_surfaces: Sequence[str] | None = None,
) -> PlotNarration:
    """书级 default → 单条全章 narration（无 presentation）。"""
    narrator = (
        None if default.narrator_char_id is None
        else ActorRef(char_id=default.narrator_char_id, presentation_ref=None))
    focalizer = (
        None if default.focalizer_char_id is None
        else ActorRef(char_id=default.focalizer_char_id, presentation_ref=None))
    pronouns = list(pronoun_surfaces) if pronoun_surfaces else (
        ['我', '我们'] if default.grammatical_person == 'first' else [])
    return PlotNarration(
        grammatical_person=default.grammatical_person,
        narrator=narrator,
        focalizer=focalizer,
        pronoun_surfaces=pronouns,
        beat_refs=[],
        evidence_refs=list(evidence_refs),
    )


def effective_narration(
    chapter: PlotBlueprint,
    *,
    narration_default: NarrationDefault | None = None,
) -> list[PlotNarration]:
    """章级列表优先；空列表且有书级 default 时合成全章一条。"""
    if chapter.narration:
        return list(chapter.narration)
    if narration_default is None:
        raise ValueError('章叙述为空且缺少书级 narration_default')
    refs: list[str] = []
    seen: set[str] = set()
    for beat in chapter.beats:
        for ref in beat.evidence_refs:
            if ref not in seen:
                seen.add(ref)
                refs.append(ref)
    if not refs:
        raise ValueError('无法为 narration_default 合成证据引用')
    return [synthesize_default_narration(narration_default, evidence_refs=refs)]
