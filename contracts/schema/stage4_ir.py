#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 目标 IR（STR-03-S4）：Presentation steps / delivery 绑定 / Writer IR。

本模块承载 Design→Grounding→Context 的正式字段形状。wire（盲 alias）仍在
``stage4.py`` 的 Blind*Response 中；compiler 落盘前映回 canonical 并写入本形状。
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, model_validator

from contracts.invariants import (
    CHAPTER_WEIGHTS, DELIVERY_MODES, GOAL_INTENTS, NARRATIVE_PERSON,
    RESPONSE_DELIVERY_MODES, RESPONSE_STRUCTURAL_ROLES, RESPONSE_TEMPORAL_LINKS,
    RESPONSE_UNIT_WEIGHTS, STRUCTURAL_ROLES, STRUCTURE_CHANGE_KINDS,
    STRUCTURE_MODES, TEMPORAL_LINKS, TIME_LAYERS, UNIT_WEIGHTS,
)
from contracts.schema.base import StrictModel
from contracts.schema.common import Sha256

DELIVERY_REF_PATTERN = r'^d\d{2,}_\d{2,}$'
BEAT_REF_PATTERN = r'^b\d{2,}_\d{2,}$'
SCENE_REF_PATTERN = r'^s\d{2,4}$'
CHAR_ID_PATTERN = r'^char_\d{3,}$'
DELIVERY_FOCUS_MAX = 80

FACT_PERMISSIONS = (
    'world_fact', 'claim_only', 'belief_only',
    'must_preserve_unresolved', 'forbidden',
)


class PresentationStep(StrictModel):
    """场景内一次呈现；数组顺序即正式呈现序。"""

    delivery_ref: str = Field(pattern=DELIVERY_REF_PATTERN)
    beat_ref: str = Field(pattern=BEAT_REF_PATTERN)
    delivery_focus: str | None = Field(default=None, max_length=DELIVERY_FOCUS_MAX)

    @model_validator(mode='after')
    def _focus_not_blank(self) -> PresentationStep:
        if self.delivery_focus is not None and not self.delivery_focus.strip():
            raise ValueError('delivery_focus 不得为空白')
        return self


class PermissionTransition(StrictModel):
    """自 delivery_ref 起粘性生效，直至后续 transition 覆盖同键。"""

    delivery_ref: str = Field(pattern=DELIVERY_REF_PATTERN)
    surface_grants: dict[str, list[str]] = Field(default_factory=dict)
    fact_permissions: dict[str, Literal[FACT_PERMISSIONS]] = Field(default_factory=dict)

    @model_validator(mode='after')
    def _non_empty_and_char_ids(self) -> PermissionTransition:
        if not self.surface_grants and not self.fact_permissions:
            raise ValueError('permission_transition 至少改一类许可')
        for char_id, surfaces in self.surface_grants.items():
            if not re.fullmatch(CHAR_ID_PATTERN, char_id):
                raise ValueError('surface_grants 键必须是 char_id')
            if not surfaces or len(surfaces) != len(set(surfaces)):
                raise ValueError('surface_grants 称谓须非空且不重复')
        for ref in self.fact_permissions:
            if not ref.strip():
                raise ValueError('fact_permissions 引用不能空白')
        return self


class StructureMoves(StrictModel):
    mode: Literal[STRUCTURE_MODES]
    moves: list[Literal[STRUCTURE_CHANGE_KINDS]] = Field(default_factory=list)

    @model_validator(mode='after')
    def _canonical(self) -> StructureMoves:
        order = {name: i for i, name in enumerate(STRUCTURE_CHANGE_KINDS)}
        if self.moves != sorted(set(self.moves), key=order.__getitem__):
            raise ValueError('structure.moves 必须去重并按契约顺序排列')
        if self.mode == 'retain' and self.moves:
            raise ValueError('retain 不得声称主动改变结构维度')
        if self.mode == 'reconstruct' and len(self.moves) < 2:
            raise ValueError('reconstruct 必须声明至少两类结构变化')
        return self


class PresentationGoal(StrictModel):
    intent: Literal[GOAL_INTENTS]
    focus_refs: list[str] = Field(min_length=1)
    anchor_beat_ref: str = Field(pattern=BEAT_REF_PATTERN)
    anchor_scene_ref: str = Field(pattern=SCENE_REF_PATTERN)

    @model_validator(mode='after')
    def _unique_focus(self) -> PresentationGoal:
        if len(self.focus_refs) != len(set(self.focus_refs)):
            raise ValueError('goal.focus_refs 不能重复')
        return self


class PresentationScene(StrictModel):
    scene_ref: str = Field(pattern=SCENE_REF_PATTERN)
    role: Literal[STRUCTURAL_ROLES]
    delivery: Literal[DELIVERY_MODES]
    transition: Literal[TEMPORAL_LINKS]
    weight: Literal[UNIT_WEIGHTS]
    purpose: str = Field(min_length=1, max_length=120)
    turn_beat_ref: str = Field(pattern=BEAT_REF_PATTERN)
    steps: list[PresentationStep] = Field(min_length=1)

    @model_validator(mode='after')
    def _steps_closed(self) -> PresentationScene:
        refs = [s.delivery_ref for s in self.steps]
        if len(refs) != len(set(refs)):
            raise ValueError('同场 delivery_ref 不能重复')
        beats = [s.beat_ref for s in self.steps]
        if self.turn_beat_ref not in beats:
            raise ValueError('turn_beat_ref 必须属于本场 steps')
        multi = {b for b in beats if beats.count(b) > 1}
        for step in self.steps:
            if step.beat_ref in multi and (
                    step.delivery_focus is None or not step.delivery_focus.strip()):
                raise ValueError('同一 beat 多 delivery 时 delivery_focus 必填')
        return self


class PresentationChapter(StrictModel):
    output_ordinal: int = Field(ge=1)
    weight: Literal[CHAPTER_WEIGHTS]
    structure: StructureMoves
    goal: PresentationGoal
    open_hook: PresentationGoal
    end_hook: PresentationGoal
    scenes: list[PresentationScene] = Field(min_length=1)
    permission_transitions: list[PermissionTransition] = Field(default_factory=list)

    @model_validator(mode='after')
    def _chapter_closed(self) -> PresentationChapter:
        scene_refs = [s.scene_ref for s in self.scenes]
        if len(scene_refs) != len(set(scene_refs)):
            raise ValueError('同章 scene_ref 不能重复')
        delivery_refs = [st.delivery_ref for sc in self.scenes for st in sc.steps]
        if len(delivery_refs) != len(set(delivery_refs)):
            raise ValueError('同章 delivery_ref 必须唯一')
        known = set(delivery_refs)
        seen_at: set[str] = set()
        for row in self.permission_transitions:
            if row.delivery_ref not in known:
                raise ValueError('permission_transition 必须绑本章 delivery_ref')
            if row.delivery_ref in seen_at:
                raise ValueError('同一 delivery_ref 至多一条 permission_transition')
            seen_at.add(row.delivery_ref)
        for goal in (self.goal, self.open_hook, self.end_hook):
            if goal.anchor_scene_ref not in scene_refs:
                raise ValueError('goal 锚点场景必须属于本章')
        return self


class BookStrategy(StrictModel):
    """仅解释性摘要；章权重权威在 chapters[].weight。"""

    summary: str = Field(min_length=1, max_length=400)


class PresentationGraph(StrictModel):
    """Design 正式产物（presentation graph）；无 alias 字典。"""

    truth_sha256: Sha256
    book_strategy: BookStrategy
    chapters: list[PresentationChapter] = Field(min_length=1)

    @model_validator(mode='after')
    def _ordinals(self) -> PresentationGraph:
        ordinals = [c.output_ordinal for c in self.chapters]
        if ordinals != list(range(1, len(ordinals) + 1)):
            raise ValueError('chapters.output_ordinal 必须 1..N 连续')
        return self


# ── Grounding ───────────────────────────────────────────────────────────────

class DeliveryEvidenceBinding(StrictModel):
    delivery_ref: str = Field(pattern=DELIVERY_REF_PATTERN)
    evidence_refs: list[str] = Field(min_length=1)
    source_context_refs: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def _unique(self) -> DeliveryEvidenceBinding:
        for label, refs in (
            ('evidence_refs', self.evidence_refs),
            ('source_context_refs', self.source_context_refs),
        ):
            if len(refs) != len(set(refs)):
                raise ValueError(f'{label} 不能重复')
        return self


class EvidenceChapterBindings(StrictModel):
    output_ordinal: int = Field(ge=1)
    bindings: list[DeliveryEvidenceBinding] = Field(min_length=1)

    @model_validator(mode='after')
    def _unique_deliveries(self) -> EvidenceChapterBindings:
        refs = [b.delivery_ref for b in self.bindings]
        if len(refs) != len(set(refs)):
            raise ValueError('同章 delivery_ref 绑定不能重复')
        return self


class BindingIssue(StrictModel):
    issue_id: str = Field(min_length=1)
    delivery_ref: str = Field(pattern=DELIVERY_REF_PATTERN)
    candidates: list[str] = Field(min_length=2)


class BindingResolution(StrictModel):
    issue_id: str = Field(min_length=1)
    selected_evidence_refs: list[str] = Field(min_length=1)
    resolver: Literal['llm', 'human', 'mechanical'] = 'llm'


class BindingInfeasibility(StrictModel):
    reason_code: str = Field(min_length=1)
    delivery_refs: list[str] = Field(min_length=1)


class InputInsufficient(StrictModel):
    reason_code: str = Field(min_length=1)
    missing: list[str] = Field(min_length=1)


# ── Writer IR ───────────────────────────────────────────────────────────────

class WriterFact(StrictModel):
    ref: str = Field(min_length=1)
    content: str = Field(min_length=1)
    permission: Literal[FACT_PERMISSIONS]
    holder: str | None = Field(default=None, pattern=CHAR_ID_PATTERN)


class WriterHardRequirement(StrictModel):
    kind: Literal['literal', 'value', 'object']
    value: str = Field(min_length=1)
    beat_ref: str | None = Field(default=None, pattern=BEAT_REF_PATTERN)


class WriterStep(StrictModel):
    delivery_ref: str = Field(pattern=DELIVERY_REF_PATTERN)
    beat_ref: str = Field(pattern=BEAT_REF_PATTERN)
    content: str = Field(min_length=1)
    delivery_focus: str | None = Field(default=None, max_length=DELIVERY_FOCUS_MAX)
    surface_permissions: dict[str, list[str]] = Field(default_factory=dict)


class WriterStageIR(StrictModel):
    time_layer: Literal[TIME_LAYERS] | None = None
    location_mode: Literal['explicit', 'inherit', 'multi_location', 'unspecified'] = 'unspecified'
    location_ref: str | None = None
    location_label: str | None = None
    present: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def _present_char_ids(self) -> WriterStageIR:
        for char_id in self.present:
            if not re.fullmatch(CHAR_ID_PATTERN, char_id):
                raise ValueError('stage.present 必须是 char_id')
        if self.location_mode == 'explicit' and not (
                self.location_ref or self.location_label):
            raise ValueError('explicit 地点须有 location_ref 或 location_label')
        return self


class WriterStateDelta(StrictModel):
    establish: list[str] = Field(default_factory=list)
    end: list[str] = Field(default_factory=list)


class WriterSceneIR(StrictModel):
    scene_ref: str = Field(pattern=SCENE_REF_PATTERN)
    role: Literal[STRUCTURAL_ROLES]
    delivery: Literal[DELIVERY_MODES]
    transition: Literal[TEMPORAL_LINKS]
    purpose: str = Field(min_length=1, max_length=120)
    target_chars: int = Field(gt=0)
    stage: WriterStageIR
    surface_permissions: dict[str, list[str]] = Field(default_factory=dict)
    steps: list[WriterStep] = Field(min_length=1)
    facts: list[WriterFact] = Field(default_factory=list)
    state_delta: WriterStateDelta = Field(default_factory=WriterStateDelta)
    hard_requirements: list[WriterHardRequirement] = Field(default_factory=list)


class WriterCharacterIR(StrictModel):
    default_surface: str = Field(min_length=1)
    role: str | None = None


class WriterHookIR(StrictModel):
    intent: Literal[GOAL_INTENTS]
    beat_ref: str = Field(pattern=BEAT_REF_PATTERN)


class WriterNarrationIR(StrictModel):
    person: Literal[NARRATIVE_PERSON]
    narrator: str | None = Field(default=None, pattern=CHAR_ID_PATTERN)
    focalizer: str | None = Field(default=None, pattern=CHAR_ID_PATTERN)
    pronouns: list[str] = Field(default_factory=list)


class WriterChapterIR(StrictModel):
    output_ordinal: int = Field(ge=1)
    function: str = Field(min_length=1)
    goal: Literal[GOAL_INTENTS]
    open_hook: WriterHookIR
    end_hook: WriterHookIR
    narration: WriterNarrationIR
    characters: dict[str, WriterCharacterIR]
    scenes: list[WriterSceneIR] = Field(min_length=1)

    @model_validator(mode='after')
    def _char_keys(self) -> WriterChapterIR:
        for char_id in self.characters:
            if not re.fullmatch(CHAR_ID_PATTERN, char_id):
                raise ValueError('characters 键必须是 char_id')
        return self


# Wire 侧 steps（无 delivery_ref；compiler 发号）
class BlindStepResponse(StrictModel):
    beat_alias: str = Field(pattern=r'^[au]_[0-9a-f]{6,64}$')
    delivery_focus: str | None = Field(default=None, max_length=DELIVERY_FOCUS_MAX)


class BlindPermissionTransitionResponse(StrictModel):
    """wire：用 scene_ref + step_index（0-based）定位，落盘前换成 delivery_ref。"""

    scene_ref: str = Field(pattern=SCENE_REF_PATTERN)
    step_index: int = Field(ge=0)
    surface_grants: dict[str, list[str]] = Field(default_factory=dict)
    fact_permissions: dict[str, Literal[FACT_PERMISSIONS]] = Field(default_factory=dict)

    @model_validator(mode='after')
    def _non_empty(self) -> BlindPermissionTransitionResponse:
        if not self.surface_grants and not self.fact_permissions:
            raise ValueError('permission_transition 至少改一类许可')
        return self


class BlindSceneStepsResponse(StrictModel):
    scene_ref: str = Field(pattern=SCENE_REF_PATTERN)
    weight: Literal[tuple(RESPONSE_UNIT_WEIGHTS)]
    purpose: str = Field(min_length=1, max_length=120)
    role: Literal[tuple(RESPONSE_STRUCTURAL_ROLES)]
    delivery: Literal[tuple(RESPONSE_DELIVERY_MODES)]
    transition: Literal[tuple(RESPONSE_TEMPORAL_LINKS)]
    turn_beat_alias: str = Field(pattern=r'^[au]_[0-9a-f]{6,64}$')
    steps: list[BlindStepResponse] = Field(min_length=1)
