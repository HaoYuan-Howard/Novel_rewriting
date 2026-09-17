#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 确定性编译原语：delivery_ref 发号、permission 传播、Writer IR。

不发 LLM；不落盘。严禁从 evidence/prose 推断 permission 或 narrator。
"""
from __future__ import annotations

from typing import Any, Mapping

from contracts.invariants import (
    CHAPTER_WEIGHTS, RESPONSE_CHAPTER_WEIGHTS, RESPONSE_UNIT_WEIGHTS, UNIT_WEIGHTS,
)
from contracts.schema.stage4_ir import (
    FACT_PERMISSIONS, BookStrategy, DeliveryEvidenceBinding, PresentationChapter,
    PresentationGoal, PresentationGraph, PresentationScene, PresentationStep,
    PermissionTransition, StructureMoves, WriterChapterIR, WriterCharacterIR,
    WriterFact, WriterHardRequirement, WriterHookIR, WriterNarrationIR,
    WriterSceneIR, WriterStageIR, WriterStateDelta, WriterStep,
)
from contracts.schema.stage4_source import SourceChapter, Stage4SourceView


COMPILER_REVISION = 'stage4-writer-ir-v1'
BUDGET_CONFIG_ID = 'allocate_budgets-v1'


def mint_delivery_ref(chapter_ordinal: int, seq: int) -> str:
    """Design 快照内确定性发号；跨快照不承诺同串同义。"""
    return f'd{chapter_ordinal:02d}_{seq:02d}'


def decode_chapter_weight(raw: str) -> str:
    if raw in CHAPTER_WEIGHTS:
        return raw
    return RESPONSE_CHAPTER_WEIGHTS.get(raw, raw)


def decode_unit_weight(raw: str) -> str:
    if raw in UNIT_WEIGHTS:
        return raw
    return RESPONSE_UNIT_WEIGHTS.get(raw, raw)


def compile_presentation_chapter(
    *,
    output_ordinal: int,
    weight: str,
    structure: Mapping[str, Any],
    goal: Mapping[str, Any],
    open_hook: Mapping[str, Any],
    end_hook: Mapping[str, Any],
    scenes: list[Mapping[str, Any]],
    permission_transitions: list[Mapping[str, Any]] | None = None,
    alias_to_ref: Mapping[str, str] | None = None,
) -> PresentationChapter:
    """将章级设计（可含 wire alias / 无 delivery_ref 的 steps）编译为正式章。"""
    table = dict(alias_to_ref or {})
    seq = 1
    formal_scenes: list[PresentationScene] = []
    step_index_to_delivery: dict[tuple[str, int], str] = {}

    for scene in scenes:
        scene_ref = scene['scene_ref']
        steps_in = scene.get('steps') or _steps_from_legacy_placements(scene, scenes)
        formal_steps: list[PresentationStep] = []
        for i, step in enumerate(steps_in):
            beat = step.get('beat_ref') or table.get(step.get('beat_alias', ''), step.get('beat_alias'))
            if not beat or not str(beat).startswith('b'):
                beat = table.get(str(step.get('beat_alias') or ''), '')
            if not beat:
                raise ValueError(f'无法解析 beat：{step!r}')
            delivery_ref = step.get('delivery_ref') or mint_delivery_ref(output_ordinal, seq)
            seq += 1
            step_index_to_delivery[(scene_ref, i)] = delivery_ref
            formal_steps.append(PresentationStep(
                delivery_ref=delivery_ref,
                beat_ref=beat,
                delivery_focus=step.get('delivery_focus'),
            ))
        turn = scene.get('turn_beat_ref') or table.get(
            scene.get('turn_beat_alias', ''), scene.get('turn_beat_alias'))
        formal_scenes.append(PresentationScene(
            scene_ref=scene_ref,
            role=_map_enum(scene['role']),
            delivery=_map_enum(scene['delivery']),
            transition=_map_enum(scene['transition']),
            weight=decode_unit_weight(scene['weight']),
            purpose=str(scene['purpose']).strip(),
            turn_beat_ref=turn,
            steps=formal_steps,
        ))

    transitions: list[PermissionTransition] = []
    for row in permission_transitions or []:
        if 'delivery_ref' in row:
            delivery_ref = row['delivery_ref']
        else:
            key = (row['scene_ref'], int(row['step_index']))
            delivery_ref = step_index_to_delivery[key]
        surfaces = {
            table.get(k, k): v for k, v in (row.get('surface_grants') or {}).items()
        }
        facts = {
            table.get(k, k): v for k, v in (row.get('fact_permissions') or {}).items()
        }
        transitions.append(PermissionTransition(
            delivery_ref=delivery_ref,
            surface_grants=surfaces,
            fact_permissions=facts,
        ))

    def goal_of(raw: Mapping[str, Any]) -> PresentationGoal:
        refs = raw.get('focus_refs') or [
            table.get(a, a) for a in raw.get('subject_aliases') or []
        ]
        return PresentationGoal(
            intent=_map_enum(raw['intent']),
            focus_refs=refs,
            anchor_beat_ref=table.get(
                raw.get('anchor_beat_alias', ''), raw.get('anchor_beat_ref')),
            anchor_scene_ref=raw['anchor_scene_ref'],
        )

    moves = structure.get('moves') or structure.get('declared_change_kinds') or []
    return PresentationChapter(
        output_ordinal=output_ordinal,
        weight=decode_chapter_weight(weight),
        structure=StructureMoves(mode=structure['mode'], moves=list(moves)),
        goal=goal_of(goal),
        open_hook=goal_of(open_hook),
        end_hook=goal_of(end_hook),
        scenes=formal_scenes,
        permission_transitions=transitions,
    )


def assemble_presentation_graph(
    *,
    truth_sha256: str,
    strategy_summary: str,
    chapters: list[PresentationChapter],
) -> PresentationGraph:
    return PresentationGraph(
        truth_sha256=truth_sha256,
        book_strategy=BookStrategy(summary=strategy_summary.strip()),
        chapters=sorted(chapters, key=lambda c: c.output_ordinal),
    )


def validate_transitions_against_source(
    chapter: PresentationChapter,
    source: SourceChapter,
) -> None:
    """章级边界校验：transition 不得早于 Stage2 revealed_from / identity_revealed_from 放开。"""
    revealed_chars = {
        c.char_id: c.identity_revealed_from for c in source.characters
    }
    revealed_facts = {
        f'{g.kind}:{g.ref}': g.revealed_from for g in source.global_subjects
    }
    ordinal = chapter.output_ordinal
    for tr in chapter.permission_transitions:
        for char_id, surfaces in tr.surface_grants.items():
            clock = revealed_chars.get(char_id)
            if clock is not None and clock > ordinal:
                raise ValueError(
                    f'permission_transition 不得早于 identity_revealed_from={clock} '
                    f'放开 {char_id}')
            # 真名类：若 identity 未揭示，不得 grant 非代词表面以外的「正式名」——
            # 此处只做时钟校验；具体表面集合由 SourceView.surfaces 提供。
            _ = surfaces
        for fact_ref, permission in tr.fact_permissions.items():
            if permission == 'forbidden':
                continue
            clock = revealed_facts.get(fact_ref)
            if clock is None:
                for g in source.global_subjects:
                    if g.ref == fact_ref or fact_ref.endswith(g.ref):
                        clock = g.revealed_from
                        break
            if clock is not None and clock > ordinal and permission == 'world_fact':
                raise ValueError(
                    f'不得在 revealed_from={clock} 之前以 world_fact 放开 {fact_ref}')


def propagate_permissions(
    chapter: PresentationChapter,
    source: SourceChapter,
) -> dict[str, dict[str, Any]]:
    """按 steps 序传播粘性 permission；返回 delivery_ref → 有效许可快照。"""
    managed_surfaces = {
        cid for t in chapter.permission_transitions for cid in t.surface_grants
    }
    base_surfaces: dict[str, list[str]] = {}
    for char in source.characters:
        surfaces = list(char.surfaces) or []
        if char.char_id in managed_surfaces:
            # Design 用 transition 管理该人物表面：章级合法，但开场默认空，粘性传播
            base_surfaces[char.char_id] = []
        elif char.identity_revealed_from is not None and char.identity_revealed_from > chapter.output_ordinal:
            base_surfaces[char.char_id] = []
        else:
            base_surfaces[char.char_id] = surfaces

    base_facts: dict[str, str] = {}
    for state in source.states:
        perm = {
            'world': 'world_fact',
            'belief': 'belief_only',
            'claim': 'claim_only',
            'unresolved': 'must_preserve_unresolved',
        }.get(state.modality, 'forbidden')
        if state.global_kind and state.global_ref:
            for g in source.global_subjects:
                if g.ref == state.global_ref and g.revealed_from and g.revealed_from > chapter.output_ordinal:
                    if state.modality == 'world':
                        perm = 'forbidden'
                    break
        base_facts[state.state_ref] = perm

    by_delivery = {t.delivery_ref: t for t in chapter.permission_transitions}
    current_surfaces = {k: list(v) for k, v in base_surfaces.items()}
    current_facts = dict(base_facts)
    out: dict[str, dict[str, Any]] = {}
    for scene in chapter.scenes:
        for step in scene.steps:
            tr = by_delivery.get(step.delivery_ref)
            if tr:
                for cid, surfaces in tr.surface_grants.items():
                    current_surfaces[cid] = list(surfaces)
                for ref, perm in tr.fact_permissions.items():
                    if perm not in FACT_PERMISSIONS:
                        raise ValueError(f'非法 permission: {perm}')
                    current_facts[ref] = perm
            out[step.delivery_ref] = {
                'surface_permissions': {k: list(v) for k, v in current_surfaces.items() if v},
                'fact_permissions': dict(current_facts),
            }
    return out


def build_writer_chapter_ir(
    *,
    design: PresentationChapter,
    source: SourceChapter,
    bindings: list[DeliveryEvidenceBinding],
    target_chars_by_scene: Mapping[str, int],
    narration: WriterNarrationIR | None = None,
) -> WriterChapterIR:
    """确定性展开 Writer IR；顺序严格等于 Design.steps。"""
    validate_transitions_against_source(design, source)
    perm = propagate_permissions(design, source)
    beat_content = {b.beat_ref: b.content for b in source.beats}
    beat_content.update({b.beat_ref: b.content for b in source.background_beats})
    binding_by = {b.delivery_ref: b for b in bindings}

    characters = {
        c.char_id: WriterCharacterIR(
            default_surface=(c.surfaces[0] if c.surfaces else c.char_id),
            role=c.role,
        )
        for c in source.characters
    }

    hard = [
        WriterHardRequirement(kind=c.kind, value=c.value or '', beat_ref=c.target_beat_ref)
        for c in source.constraints
        if c.kind in ('literal', 'value', 'object') and c.value
    ]

    scenes_ir: list[WriterSceneIR] = []
    for scene in design.scenes:
        steps_ir: list[WriterStep] = []
        for step in scene.steps:
            if step.delivery_ref not in binding_by and bindings:
                # 绑定缺失由调用方以 input_insufficient / infeasible 处理；此处允许空绑定测单测
                pass
            snap = perm[step.delivery_ref]
            steps_ir.append(WriterStep(
                delivery_ref=step.delivery_ref,
                beat_ref=step.beat_ref,
                content=beat_content.get(step.beat_ref, ''),
                delivery_focus=step.delivery_focus,
                surface_permissions=snap['surface_permissions'],
            ))
        entry_facts = []
        for state in source.states:
            if state.established_at_entry or state.state_ref in source.entry_state_refs:
                p = perm[scene.steps[0].delivery_ref]['fact_permissions'].get(
                    state.state_ref, 'forbidden')
                content = state.content or state.state_ref
                entry_facts.append(WriterFact(
                    ref=state.state_ref, content=content, permission=p,
                    holder=state.holder_id,
                ))
        # scene 默认 surface = 首 step 快照
        first = perm[scene.steps[0].delivery_ref]['surface_permissions']
        present_ids: list[str] = []
        seen_p: set[str] = set()
        for b in source.beats:
            if b.beat_ref not in {s.beat_ref for s in scene.steps}:
                continue
            for p in b.participant_ids:
                if p not in seen_p:
                    seen_p.add(p)
                    present_ids.append(p)
        scenes_ir.append(WriterSceneIR(
            scene_ref=scene.scene_ref,
            role=scene.role,
            delivery=scene.delivery,
            transition=scene.transition,
            purpose=scene.purpose,
            target_chars=int(target_chars_by_scene.get(scene.scene_ref) or 1),
            stage=WriterStageIR(
                location_mode='unspecified',
                present=present_ids,
            ),
            surface_permissions=first,
            steps=steps_ir,
            facts=entry_facts,
            state_delta=WriterStateDelta(establish=[], end=[]),
            hard_requirements=hard,
        ))

    narr = narration or WriterNarrationIR(person='third', narrator=None, focalizer=None, pronouns=[])
    return WriterChapterIR(
        output_ordinal=design.output_ordinal,
        function=source.chapter_function,
        goal=design.goal.intent,
        open_hook=WriterHookIR(intent=design.open_hook.intent, beat_ref=design.open_hook.anchor_beat_ref),
        end_hook=WriterHookIR(intent=design.end_hook.intent, beat_ref=design.end_hook.anchor_beat_ref),
        narration=narr,
        characters=characters,
        scenes=scenes_ir,
    )


def _steps_from_legacy_placements(
    scene: Mapping[str, Any], all_scenes: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """兼容旧 beat_placements → steps（仅 compiler 内部迁移辅助）。"""
    placements = scene.get('beat_placements')
    if placements:
        return placements
    return list(scene.get('steps') or [])


def _map_enum(value: str) -> str:
    from contracts.invariants import (
        RESPONSE_DELIVERY_MODES, RESPONSE_GOAL_INTENTS,
        RESPONSE_STRUCTURAL_ROLES, RESPONSE_TEMPORAL_LINKS,
    )
    for table in (
        RESPONSE_GOAL_INTENTS, RESPONSE_STRUCTURAL_ROLES,
        RESPONSE_DELIVERY_MODES, RESPONSE_TEMPORAL_LINKS,
    ):
        if value in table:
            return table[value]
    return value


def chapter_delivery_refs(chapter: PresentationChapter) -> list[str]:
    return [st.delivery_ref for sc in chapter.scenes for st in sc.steps]


def assert_binding_matches_design(
    design: PresentationChapter,
    bindings: list[DeliveryEvidenceBinding],
) -> None:
    expected = set(chapter_delivery_refs(design))
    got = {b.delivery_ref for b in bindings}
    if expected != got:
        raise ValueError(
            f'Grounding delivery 集合必须与 Design 快照一致：'
            f'missing={sorted(expected - got)} extra={sorted(got - expected)}')
