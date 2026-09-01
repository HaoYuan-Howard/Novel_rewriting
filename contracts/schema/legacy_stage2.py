#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 ``chapter_blueprints.json`` schema v6 的冻结只读解码器。

这不是迁移器，也不在 :mod:`contracts.schema` 的活动注册表中登记。v7 唯一的
shape break 是新增 ``narrative_relations``；两本已经完成过 Stage4A 人审的旧书
仍需按**当时真正存在的字节**复核其来源与盲真相绑定，不能为了让当前模型接收而
在盘上手补字段。这个模块只回答“它是不是一份精确的 v6”，永远不编码或写回。
"""
from __future__ import annotations

from typing import Any

from pydantic import Field, ValidationError, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.stage2 import (
    Beat, BlueprintProvenance, CausalEdge, FactAtom, Narration,
)


FROZEN_STAGE2_BLUEPRINT_VERSION = 6


class FrozenBlueprintV6(StrictModel):
    """v6 章蓝图；刻意没有 ``narrative_relations`` 槽位。"""

    chapter_ordinal: int = Field(ge=1)
    chapter_function: str = Field(min_length=1)
    beats: list[Beat] = Field(min_length=1)
    fact_atoms: list[FactAtom] = Field(min_length=1)
    causal_edges: list[CausalEdge]
    entry_state_refs: list[str]
    ending_state_refs: list[str]
    narration: list[Narration] = Field(min_length=1)

    @model_validator(mode='after')
    def _semantic_refs_are_closed(self) -> FrozenBlueprintV6:
        fact_ids = [fact.fact_id for fact in self.fact_atoms]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError('fact_atom.fact_id 必须在章内唯一')
        beat_ids = {beat.beat_id for beat in self.beats}
        bad_beats = [
            fact.fact_id for fact in self.fact_atoms
            if fact.source_beat_ref not in beat_ids
        ]
        if bad_beats:
            raise ValueError(f'fact_atom 引用了本章外或不存在的 beat：{bad_beats}')
        refs = set(fact_ids)
        used = [
            ref for edge in self.causal_edges for ref in (edge.before, edge.after)
        ] + list(self.entry_state_refs) + list(self.ending_state_refs)
        missing = sorted(set(used) - refs)
        if missing:
            raise ValueError(f'章级语义引用不存在：{missing}')
        if any(edge.before == edge.after for edge in self.causal_edges):
            raise ValueError('causal_edge 不能自环')
        entry = set(self.entry_state_refs)
        ending = set(self.ending_state_refs)
        obligation = {fact.fact_id: fact.obligation for fact in self.fact_atoms}
        if any(obligation[ref] != 'context' for ref in entry):
            raise ValueError('entry_state_refs 只能引用 context 原子')
        if any(obligation[ref] != 'land' for ref in ending):
            raise ValueError('ending_state_refs 只能引用 land 原子')
        return self


class FrozenChapterBlueprintsV6(StrictModel):
    """冻结 v6 业务 payload；版本信封由 decoder 单独核。"""

    blueprints: list[FrozenBlueprintV6] = Field(min_length=1)
    provenance: BlueprintProvenance = Field(alias='_provenance')

    @model_validator(mode='after')
    def _chapter_ordinals_are_unique(self) -> FrozenChapterBlueprintsV6:
        ordinals = [row.chapter_ordinal for row in self.blueprints]
        if len(ordinals) != len(set(ordinals)):
            duplicates = sorted({value for value in ordinals if ordinals.count(value) > 1})
            raise ValueError(f'chapter_ordinal 重号：{duplicates}')
        return self


def decode_frozen_stage2_blueprints_v6(
    data: Any,
) -> tuple[FrozenChapterBlueprintsV6, dict[str, Any]]:
    """关闭式解码一份 v6 信封，返回模型与未注入字段的原业务 payload。"""
    if not isinstance(data, dict):
        raise ValueError('chapter_blueprints v6 顶层必须是 object')
    if data.get('_schema_version') != FROZEN_STAGE2_BLUEPRINT_VERSION:
        raise ValueError(
            '冻结 reader 只接受 chapter_blueprints schema v6，'
            f'实得 {data.get("_schema_version")!r}')
    if set(data) != {'_schema_version', 'blueprints', '_provenance'}:
        raise ValueError(
            'chapter_blueprints v6 顶层字段必须精确等于 '
            '[_schema_version, blueprints, _provenance]')
    payload = {key: value for key, value in data.items() if key != '_schema_version'}
    try:
        model = FrozenChapterBlueprintsV6.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f'chapter_blueprints 不是可读的冻结 v6：\n{exc}') from exc
    return model, payload


__all__ = [
    'FROZEN_STAGE2_BLUEPRINT_VERSION',
    'FrozenBlueprintV6', 'FrozenChapterBlueprintsV6',
    'decode_frozen_stage2_blueprints_v6',
]
