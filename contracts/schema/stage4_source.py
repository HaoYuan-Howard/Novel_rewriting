#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4SourceView —— Stage4 ingress 的最小消费投影（非正式产物、非流水线节点）。

从冻结的 Stage2 Blueprint / Characters / Global 确定性投影；供 Design wire 材料、
Grounding 证据闭包与 Context compiler 共用。Blueprint schema 本批冻结，削减只改
本投影与适配，不迫使 Stage4 再换结构。
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue

from contracts.schema.base import FrozenModel
from contracts.schema.common import Sha256


class SourceCharacter(FrozenModel):
    char_id: str = Field(pattern=r'^char_\d{3,}$')
    surfaces: list[str] = Field(default_factory=list)
    first_mention_chapter: int = Field(ge=1)
    name_usable_from: int | None = Field(default=None, ge=1)
    identity_revealed_from: int | None = Field(default=None, ge=1)


class SourceBeat(FrozenModel):
    beat_ref: str = Field(pattern=r'^b\d{2,}_\d{2,}$')
    content: str = Field(min_length=1)
    participant_ids: list[str]


class SourceState(FrozenModel):
    state_ref: str = Field(pattern=r'^st\d{2,}_\d{2,}$')
    content: str | None = None
    modality: Literal['world', 'belief', 'claim', 'unresolved']
    holder_id: str | None = None
    global_kind: Literal['relation', 'world_rule', 'secret', 'setting'] | None = None
    global_ref: str | None = None
    established_at_entry: bool | None = None


class SourceConstraint(FrozenModel):
    constraint_ref: str = Field(min_length=1)
    kind: Literal[
        'cause', 'story_before', 'response_to', 'same_scene', 'observes',
        'literal', 'value', 'object',
    ]
    before_beat_ref: str | None = None
    after_beat_ref: str | None = None
    observer_id: str | None = None
    target_beat_ref: str | None = None
    value: str | None = None


class SourceGlobalSubject(FrozenModel):
    kind: Literal['relation', 'world_rule', 'secret', 'setting']
    ref: str = Field(min_length=1)
    value: JsonValue
    revealed_from: int | None = Field(default=None, ge=1)
    effective_from: int | None = Field(default=None, ge=1)
    effective_until: int | None = Field(default=None, ge=1)


class SourceChapter(FrozenModel):
    """单章正式真相投影；identity 一律 char_id / beat_ref，无 presentation actor。"""

    chapter_ordinal: int = Field(ge=1)
    chapter_function: str = Field(min_length=1)
    characters: list[SourceCharacter]
    beats: list[SourceBeat] = Field(min_length=1)
    states: list[SourceState]
    entry_state_refs: list[str]
    carry_out_refs: list[str]
    constraints: list[SourceConstraint]
    global_subjects: list[SourceGlobalSubject]
    background_beats: list[SourceBeat] = Field(default_factory=list)


class Stage4SourceView(FrozenModel):
    """全书 ingress；`truth_sha256` 绑定投影输入身份，供 Design/Context freshness。"""

    truth_sha256: Sha256
    chapters: list[SourceChapter] = Field(min_length=1)
