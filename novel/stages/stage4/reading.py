#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reading.py —— Stage4 A/B 审阅工作包的逐章可读裁决材料（0 LLM、纯函数）。

Commander/人审要判的是「这一章的场景入口、事实组合、揭示顺序、锚点与 concern 是否成立」，
而工作包过去只给 id 与整书 JSON：审阅者只能自己去盘上读 plan_chapters / chapter_blueprints /
main_text 取证——那正是 headless Commander 每轮多花的十几次往返。这里把同一份材料按章渲染成
typed ``reading``，挂在 coverage 行随包下发（`R-5.25`）。

体积纪律：claim 文本在一章里只出现一次——落在本章 ``facts`` 表；场景、目标、因果边、状态与
concern 只引用 id/alias。实测把 claim 内联进每场 entry/exit 状态会让 B 面工作包翻倍，而那些状态
是编译期累加出来的，同一 claim 会重复十几次。

它不是第二个判官：结构读数直接调 ``measure.structure_metrics_of``（与 stage4.plan 复检同一函数）；
claim 只从当前 Stage2 蓝图取；hinge 材料只经 ``beats.verbatim_pieces`` 拆分。A 面输入本身就是
opaque alias 的盲主体，这里不解 alias、不碰 formal id；B 面 ref 是正式 fact/beat/hinge id，
与计划产物同口径。找不到的引用如实标 unknown / 缺失，不猜。
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from contracts.schema.api import (
    DesignChapterReading,
    DesignChapterSubject,
    StructureChapterReading,
    WorkpackCausalEdgeReading,
    WorkpackClosedRefReading,
    WorkpackConcernReading,
    WorkpackFactReading,
    WorkpackGoalReading,
    WorkpackMaterialReading,
    WorkpackSceneReading,
    WorkpackStageReading,
)
from contracts.schema.stage4 import BindingConcern, PlanChapter, PlanGoal
from novel.stages.stage4.beats import verbatim_pieces
from novel.stages.stage4.measure import (
    computed_structure_change_kinds, structure_metrics_of,
)

DESIGN_READING_KIND = 'stage4_design_chapter_reading_v1'
STRUCTURE_READING_KIND = 'stage4_structure_chapter_reading_v1'
WORKPACK_READING_KIND = 'stage4_workpack_reading_v1'

_FACT_ID = re.compile(r'^f\d{2,}_\d{3,}$')
_BEAT_ID = re.compile(r'^b\d{2}_\d{2}$')
_HINGE_ID = re.compile(r'^h\d{2,}_\d{3,}$')
#: 材料摘录只示范「这场引用了什么」，不是逐字全文；全文仍只在 Stage2 产物里。
_EXCERPT_CHARS = 60
_MISSING_CLAIM = '（当前蓝图无此事实）'
#: 与 stage4.plan 生产 metric 规则发布的同一批读数面 + 映射状态；adjacency/boundary
#: 从未被生产判据发布，这里也不发，免得工作包再长出一份没人裁的读数。
_METRIC_SECTIONS = (
    'state', 'mapping', 'partition', 'order', 'noncontiguous', 'anchors',
    'eligibility',
)


def workpack_legend(kind: str) -> dict[str, str]:
    """顶层只出现一次的字段说明；逐行不再重复。"""
    common = {
        'reading': (
            '每个 coverage 行的 reading 是服务端确定性渲染的本章裁决材料；'
            '逐章裁决只读它，不需要也不得读取盘上产物或源码。'),
        'facts': (
            'reading.facts 是本章 id/alias→claim 表，每个 claim 只在这里出现一次；'
            '场景、目标、因果边、状态与 concern 只引用 id/alias，按表解读。'),
        'metric_only': (
            'metric_only 组只保留一行摘要；完整逐章结构读数在 '
            'coverage[].reading.structure_metrics，与 stage4.plan 复检同一函数现场复算。'),
        'actions': (
            'INFO/metric/UNVER 只披露，不制造付费动作；裁决只提交 coverage 行 '
            'allowed_actions 列出的动作。'),
    }
    if kind == 'design':
        return {
            **common,
            'refs': 'A 面所有 ref 都是本章 opaque alias（a_…），不解 formal id。',
            'scenes': (
                'scenes[] 是候选场景：purpose/role/delivery/transition/weight 与 '
                'land/context/turn/entry_state/exit_state 的 alias。'),
            'strategy': (
                '顶层 reading.reconstruction_strategy / chapter_weights 是本次整书策略主体。'),
        }
    return {
        **common,
        'refs': (
            'B 面 ref 是正式 fact/beat/hinge id；stages 的 time_layer/place/present 来自'
            '蓝图 beat（present 为 char_id）。'),
        'materials': (
            'scenes[].materials 是本场引用 hinge 材料的四组计数与首句摘录，不是逐字全文；'
            'missing_material_refs 是蓝图里找不到的 hinge 引用。'),
        'concerns': (
            'binding_concerns 的 closed_refs 已解成 claim/舞台/材料文本；'
            'kind=unknown 表示当前蓝图找不到该引用。全局（无章）concern 在顶层 '
            'reading.global_binding_concerns。'),
        'alignment': (
            'structure_metrics.alignment 把声明的 retain/reconstruct 与代码复算的变化并排；'
            'unrealized_declared / undeclared_actual 非空即声明与实算不一致。'),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 蓝图索引（只从 typed 产物的 JSON 投影读，不读盘）
# ─────────────────────────────────────────────────────────────────────────────

def _atoms_of(blueprint: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(atom.get('fact_id')): atom
        for atom in (blueprint.get('fact_atoms') or [])
        if isinstance(atom, Mapping) and atom.get('fact_id')
    }


def _beats_of(blueprint: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(beat.get('beat_id')): beat
        for beat in (blueprint.get('beats') or [])
        if isinstance(beat, Mapping) and beat.get('beat_id')
    }


def _hinges_of(beats: Mapping[str, Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {
        str(hinge.get('hinge_id')): hinge
        for beat in beats.values()
        for hinge in (beat.get('hinges') or [])
        if isinstance(hinge, Mapping) and hinge.get('hinge_id')
    }


def _excerpt(text: str) -> str:
    stripped = text.strip()
    if len(stripped) <= _EXCERPT_CHARS:
        return stripped or '（空）'
    return stripped[:_EXCERPT_CHARS] + '…'


def _fact_row(ref: str, atoms: Mapping[str, Mapping[str, Any]]) -> WorkpackFactReading:
    atom = atoms.get(ref)
    if atom is None:
        return WorkpackFactReading(ref=ref, claim=_MISSING_CLAIM)
    claim = str(atom.get('claim') or '').strip() or _MISSING_CLAIM
    return WorkpackFactReading(
        ref=ref, claim=claim,
        kind=(str(atom['kind']) if atom.get('kind') else None),
        obligation=(str(atom['obligation']) if atom.get('obligation') else None),
    )


def _unique(refs: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            out.append(ref)
    return out


def _stage(ref: str, beats: Mapping[str, Mapping[str, Any]]) -> WorkpackStageReading:
    beat = beats.get(ref)
    if beat is None:
        return WorkpackStageReading(ref=ref, found=False)
    return WorkpackStageReading(
        ref=ref, found=True,
        time_layer=(str(beat['time_layer']) if beat.get('time_layer') else None),
        place=(str(beat['place']) if beat.get('place') else None),
        present=[str(value) for value in (beat.get('present_char_ids') or [])],
    )


def _materials(
    refs: Sequence[str], hinges: Mapping[str, Mapping[str, Any]],
) -> tuple[list[WorkpackMaterialReading], list[str]]:
    selected = [dict(hinges[ref]) for ref in refs if ref in hinges]
    missing = [ref for ref in refs if ref not in hinges]
    groups, _unknown = verbatim_pieces(selected)
    out: list[WorkpackMaterialReading] = []
    for group, pieces in groups.items():
        if not pieces:
            continue
        out.append(WorkpackMaterialReading(
            group=group, count=len(pieces),
            chars=sum(len(piece) for piece in pieces),
            excerpt=_excerpt(pieces[0])))
    return out, missing


def _plan_goal(goal: PlanGoal) -> WorkpackGoalReading:
    return WorkpackGoalReading(
        intent=goal.intent,
        refs=[
            ref.ref if ref.kind == 'fact_atom' else f'{ref.kind}:{ref.ref}'
            for ref in goal.fact_refs
        ])


def _closed_ref(
    ref: str, *, atoms: Mapping[str, Mapping[str, Any]],
    beats: Mapping[str, Mapping[str, Any]],
    hinges: Mapping[str, Mapping[str, Any]],
) -> WorkpackClosedRefReading:
    if _FACT_ID.match(ref) and ref in atoms:
        return WorkpackClosedRefReading(
            ref=ref, kind='fact', text=_fact_row(ref, atoms).claim)
    if _BEAT_ID.match(ref) and ref in beats:
        stage = _stage(ref, beats)
        return WorkpackClosedRefReading(
            ref=ref, kind='beat',
            text=(
                f'{stage.time_layer or "（无时层）"} / {stage.place or "（无地点）"} / '
                f'在场 {"、".join(stage.present) or "（无）"}'))
    if _HINGE_ID.match(ref) and ref in hinges:
        groups, _unknown = verbatim_pieces([dict(hinges[ref])])
        pieces = [piece for values in groups.values() for piece in values]
        return WorkpackClosedRefReading(
            ref=ref, kind='hinge',
            text=_excerpt(pieces[0]) if pieces else '（材料无原文字节）')
    return WorkpackClosedRefReading(ref=ref, kind='unknown', text=None)


def concern_reading(
    concern: BindingConcern, *,
    blueprints: Mapping[int, Mapping[str, Any]],
) -> WorkpackConcernReading:
    """把一条 B concern 的 closed_refs 解成人话；找不到的引用如实标 unknown。"""
    ordinals = (
        [concern.chapter_ordinal] if concern.chapter_ordinal is not None
        else sorted(blueprints))
    atoms: dict[str, Mapping[str, Any]] = {}
    beats: dict[str, Mapping[str, Any]] = {}
    for ordinal in ordinals:
        blueprint = blueprints.get(ordinal) or {}
        atoms.update(_atoms_of(blueprint))
        beats.update(_beats_of(blueprint))
    hinges = _hinges_of(beats)
    return WorkpackConcernReading(
        constraint_code=concern.constraint_code,
        chapter_ordinal=concern.chapter_ordinal,
        scenes=list(concern.scenes),
        reason=concern.reason,
        closed_refs=[
            _closed_ref(ref, atoms=atoms, beats=beats, hinges=hinges)
            for ref in concern.closed_refs
        ],
    )


# ─────────────────────────────────────────────────────────────────────────────
# B 面：已绑定计划的逐章材料
# ─────────────────────────────────────────────────────────────────────────────

def structure_chapter_reading(
    chapter: PlanChapter, *,
    blueprint: Mapping[str, Any],
    concerns: Sequence[BindingConcern],
    signature_conflict_codes: Sequence[str] = (),
) -> StructureChapterReading:
    """一章 Stage4B 裁决材料：facts 表 + 逐场 id/舞台/材料 + 本章结构读数 + 本章 concerns。"""
    atoms = _atoms_of(blueprint)
    beats = _beats_of(blueprint)
    hinges = _hinges_of(beats)
    goals = {
        'chapter': _plan_goal(chapter.chapter_goal),
        'open_hook': _plan_goal(chapter.open_hook_goal),
        'end_hook': _plan_goal(chapter.end_hook_goal),
    }
    scenes: list[WorkpackSceneReading] = []
    used: list[str] = []
    for scene in chapter.scenes:
        materials, missing = _materials(scene.material_refs, hinges)
        scenes.append(WorkpackSceneReading(
            scene_ref=scene.slot,
            budget_chars=scene.budget_chars,
            purpose=scene.purpose, role=scene.role,
            delivery=scene.delivery, transition=scene.transition,
            land=list(scene.land_fact_refs),
            context=list(scene.context_fact_refs),
            turn=scene.turn_fact_ref,
            entry_state=list(scene.entry_state_refs),
            exit_state=list(scene.exit_state_refs),
            stages=[_stage(ref, beats) for ref in scene.stage_refs],
            materials=materials,
            missing_material_refs=missing,
        ))
        used.extend(scene.land_fact_refs)
        used.extend(scene.context_fact_refs)
        used.append(scene.turn_fact_ref)
        used.extend(scene.entry_state_refs)
        used.extend(scene.exit_state_refs)
    for goal in goals.values():
        used.extend(ref for ref in goal.refs if _FACT_ID.match(ref))
    facts = [_fact_row(ref, atoms) for ref in _unique(used)]

    metrics = structure_metrics_of(
        dict(blueprint), chapter.model_dump(mode='json'))
    computed = computed_structure_change_kinds(metrics)
    declared = list(chapter.structure_decision.declared_change_kinds)
    structure_metrics = {
        key: metrics[key] for key in _METRIC_SECTIONS if key in metrics
    }
    structure_metrics['alignment'] = {
        'mode': chapter.structure_decision.mode,
        'declared_change_kinds': declared,
        'computed_change_kinds': computed,
        'unrealized_declared': [value for value in declared if value not in computed],
        'undeclared_actual': [value for value in computed if value not in declared],
    }
    chapter_function = str(blueprint.get('chapter_function') or '').strip()
    return StructureChapterReading(
        kind=STRUCTURE_READING_KIND,
        chapter_function=chapter_function or '（当前蓝图缺章功能）',
        chapter_rewrite_brief=chapter.chapter_rewrite_brief,
        structure_decision=chapter.structure_decision.model_dump(mode='json'),
        facts=facts,
        goals=goals,
        scenes=scenes,
        structure_metrics=structure_metrics,
        binding_concerns=[
            concern_reading(
                concern, blueprints={chapter.output_ordinal: blueprint})
            for concern in concerns
            if concern.chapter_ordinal == chapter.output_ordinal
        ],
        signature_conflict_codes=list(signature_conflict_codes),
    )


# ─────────────────────────────────────────────────────────────────────────────
# A 面：盲候选的逐章材料（只用 opaque alias）
# ─────────────────────────────────────────────────────────────────────────────

def design_chapter_reading(subject: DesignChapterSubject) -> DesignChapterReading:
    """一章 Stage4A 裁决材料：盲真相 facts 表 + goal/边/关系/状态/候选场景的 alias。"""
    truth = subject.blind_truth
    chapter = subject.chapter

    def goal(value: Any) -> WorkpackGoalReading:
        return WorkpackGoalReading(
            intent=value.intent, refs=list(value.fact_aliases))

    return DesignChapterReading(
        kind=DESIGN_READING_KIND,
        chapter_function=truth.chapter_function,
        weight=chapter.weight,
        chapter_rewrite_brief=chapter.chapter_rewrite_brief,
        structure_decision=chapter.structure_decision.model_dump(mode='json'),
        facts=list(truth.facts),
        goals={
            'chapter': goal(chapter.chapter_goal),
            'open_hook': goal(chapter.open_hook_goal),
            'end_hook': goal(chapter.end_hook_goal),
        },
        causal_edges=[
            WorkpackCausalEdgeReading(
                before_alias=edge.before_alias, after_alias=edge.after_alias)
            for edge in truth.causal_edges
        ],
        narrative_relations=[
            relation.model_dump(mode='json')
            for relation in truth.narrative_relations
        ],
        entry_state=list(truth.entry_state_aliases),
        ending_state=list(truth.ending_state_aliases),
        scenes=[
            WorkpackSceneReading(
                scene_ref=scene.candidate_scene_id,
                weight=scene.weight,
                purpose=scene.purpose, role=scene.role,
                delivery=scene.delivery, transition=scene.transition,
                land=list(scene.land_fact_aliases),
                context=list(scene.context_fact_aliases),
                turn=scene.turn_fact_alias,
                entry_state=list(scene.entry_state_aliases),
                exit_state=list(scene.exit_state_aliases),
            )
            for scene in chapter.scenes
        ],
    )


__all__ = [
    'DESIGN_READING_KIND', 'STRUCTURE_READING_KIND', 'WORKPACK_READING_KIND',
    'concern_reading', 'design_chapter_reading', 'structure_chapter_reading',
    'workpack_legend',
]
