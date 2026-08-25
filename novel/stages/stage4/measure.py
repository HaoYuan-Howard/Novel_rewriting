#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 原子事实 → 场景计划的确定性编译与篇幅分配。"""
from __future__ import annotations

import math
from typing import Any

from contracts.invariants import (
    CHAPTER_WEIGHT_RATIO, RESPONSE_CHAPTER_WEIGHTS, RESPONSE_DELIVERY_MODES,
    RESPONSE_GOAL_INTENTS, RESPONSE_STRUCTURAL_ROLES, RESPONSE_TEMPORAL_LINKS,
    RESPONSE_UNIT_WEIGHTS, UNIT_WEIGHT_FACTOR, VERBATIM_BUDGET_FACTOR,
)
from novel.errors import RefusedByStructure
from novel.stages.stage4.beats import beat_index, verbatim_pieces

IDENTITY_GOAL = '（恒等基线·未做任何重构决策）'
_GOAL_KEYS = ('chapter_goal', 'open_hook_goal', 'end_hook_goal')
_SCENE_ENUMS = {
    'role': RESPONSE_STRUCTURAL_ROLES,
    'delivery': RESPONSE_DELIVERY_MODES,
    'transition': RESPONSE_TEMPORAL_LINKS,
    'weight': RESPONSE_UNIT_WEIGHTS,
}


def identity_goal(chapter: int, intent: str = 'advance') -> dict[str, Any]:
    return {'intent': intent, 'fact_refs': [{
        'kind': 'chapter_boundary', 'ref': f'chapter:{chapter}', 'chapter': chapter,
    }], 'rationale': IDENTITY_GOAL}


def semantic_contract_of(blueprint: dict[str, Any]) -> dict[str, Any]:
    atoms = list(blueprint.get('fact_atoms') or [])
    land = [a for a in atoms if a.get('obligation') == 'land']
    hinges = [hinge for beat in (blueprint.get('beats') or [])
              for hinge in (beat.get('hinges') or [])]
    return {
        'chapter_function': blueprint.get('chapter_function'),
        'information_delta': [a.get('fact_id') for a in land
                              if a.get('kind') == 'information'],
        'character_state_delta': [a.get('fact_id') for a in land
                                  if a.get('kind') == 'character_state'],
        'relationship_delta': [a.get('fact_id') for a in land
                               if a.get('kind') == 'relationship'],
        'reader_knowledge_delta': [a.get('fact_id') for a in land
                                   if a.get('kind') == 'reader_knowledge'],
        'must_land_fact_refs': [a.get('fact_id') for a in land],
        'context_fact_refs': [a.get('fact_id') for a in atoms
                              if a.get('obligation') == 'context'],
        'causal_edges': list(blueprint.get('causal_edges') or []),
        'entry_state_refs': list(blueprint.get('entry_state_refs') or []),
        'ending_state_refs': list(blueprint.get('ending_state_refs') or []),
        'material_refs': [h.get('hinge_id') for h in hinges if h.get('hinge_id')],
    }


def decode_plan_response(plan: dict[str, Any]) -> int:
    hit = 0
    for chapter in (plan.get('output_chapters') or []):
        if chapter.get('weight') in RESPONSE_CHAPTER_WEIGHTS:
            chapter['weight'] = RESPONSE_CHAPTER_WEIGHTS[chapter['weight']]
            hit += 1
        for key in _GOAL_KEYS:
            goal = chapter.get(key)
            if isinstance(goal, dict) and goal.get('intent') in RESPONSE_GOAL_INTENTS:
                goal['intent'] = RESPONSE_GOAL_INTENTS[goal['intent']]
                hit += 1
        for scene in (chapter.get('scenes') or []):
            for field, table in _SCENE_ENUMS.items():
                if scene.get(field) in table:
                    scene[field] = table[scene[field]]
                    hit += 1
    return hit


def encode_plan_baseline(chapter: dict[str, Any]) -> dict[str, Any]:
    """正式场景计划 → 定向重规划响应形；删除 producer-owned 字段。"""
    chapter_back = {v: k for k, v in RESPONSE_CHAPTER_WEIGHTS.items()}
    goal_back = {v: k for k, v in RESPONSE_GOAL_INTENTS.items()}
    scene_back = {field: {v: k for k, v in table.items()}
                  for field, table in _SCENE_ENUMS.items()}
    if chapter.get('weight') in chapter_back:
        chapter['weight'] = chapter_back[chapter['weight']]
    else:
        chapter.pop('weight', None)
    chapter.pop('semantic_contract', None)
    for key in _GOAL_KEYS:
        goal = chapter.get(key)
        if isinstance(goal, dict) and goal.get('intent') in goal_back:
            goal['intent'] = goal_back[goal['intent']]
    for scene in (chapter.get('scenes') or []):
        scene.pop('slot', None)
        scene.pop('budget_chars', None)
        for field, table in scene_back.items():
            if scene.get(field) in table:
                scene[field] = table[scene[field]]
    return chapter


def _source_chars(beat: dict[str, Any]) -> int | None:
    span = beat.get('span')
    if not isinstance(span, dict):
        return None
    start, end = span.get('start'), span.get('end')
    return end - start if type(start) is int and type(end) is int and end > start else None


def chapter_source_chars(main_text: list[dict[str, Any]],
                         bidx: dict[str, dict[str, Any]]) \
        -> tuple[dict[Any, int], dict[Any, tuple[int, int]]]:
    per: dict[Any, int] = {}
    cov: dict[Any, tuple[int, int]] = {}
    for row in bidx.values():
        ordinal = row['ord']
        got, total = cov.get(ordinal, (0, 0))
        known = row.get('source_chars') is not None
        cov[ordinal] = (got + int(known), total + 1)
        if known:
            per[ordinal] = per.get(ordinal, 0) + int(row['source_chars'])
    chars = {row.get('chapter_ordinal'): per.get(
        row.get('chapter_ordinal'), len(row.get('text') or '')) for row in main_text}
    return chars, cov


def budget_basis(blueprints: dict[str, Any], _bidx: dict[str, dict[str, Any]] | None = None
                 ) -> tuple[dict[Any, int], dict[str, int], list[Any]]:
    chapters: dict[Any, int] = {}
    facts: dict[str, int] = {}
    gaps: list[Any] = []
    for bp in (blueprints.get('blueprints') or []):
        ordinal = bp.get('chapter_ordinal')
        sizes = [_source_chars(beat) for beat in (bp.get('beats') or [])]
        known = [size for size in sizes if size is not None]
        if not known:
            gaps.append(ordinal)
        else:
            chapters[ordinal] = sum(known)
        for atom in (bp.get('fact_atoms') or []):
            if atom.get('fact_id'):
                facts[str(atom['fact_id'])] = max(1, len(str(atom.get('claim') or '')))
    return chapters, facts, gaps


def source_chars_table(blueprints: dict[str, Any], _bidx: dict[str, dict[str, Any]] | None = None,
                       *, book_total: bool = False) -> str | None:
    chapter_sizes, _facts, gaps = budget_basis(blueprints)
    if not chapter_sizes and not gaps:
        return None
    rows = [f'阅读序第{o}章：原子事实 {len(bp.get("fact_atoms") or [])} 条，'
            f'可定位源区间 {chapter_sizes.get(o, "?")} 字'
            for bp in (blueprints.get('blueprints') or [])
            for o in [bp.get('chapter_ordinal')]]
    if book_total:
        rows.append(f'全书可定位源区间合计 {sum(chapter_sizes.values())} 字；'
                    f'{len(gaps)} 章基准未知')
    return '\n'.join(rows)


def _round_shares(raw: list[float], total: int) -> list[int]:
    if not raw:
        return []
    total = max(total, len(raw))
    denom = sum(raw) or len(raw)
    exact = [value * total / denom for value in raw]
    out = [max(1, math.floor(value)) for value in exact]
    while sum(out) < total:
        index = max(range(len(out)), key=lambda i: exact[i] - out[i])
        out[index] += 1
    while sum(out) > total:
        choices = [i for i, value in enumerate(out) if value > 1]
        if not choices:
            break
        index = min(choices, key=lambda i: exact[i] - out[i])
        out[index] -= 1
    return out


def _validate_scene_refs(chapter: dict[str, Any], blueprint: dict[str, Any]) -> None:
    ordinal = chapter.get('output_ordinal')
    atoms = {str(a.get('fact_id')): a for a in (blueprint.get('fact_atoms') or [])}
    hinges = {str(h.get('hinge_id')) for beat in (blueprint.get('beats') or [])
              for h in (beat.get('hinges') or [])}
    beats = {str(b.get('beat_id')) for b in (blueprint.get('beats') or [])}
    land = {fid for fid, atom in atoms.items() if atom.get('obligation') == 'land'}
    context = set(atoms) - land
    landed: list[str] = []
    bad: list[str] = []
    for index, scene in enumerate(chapter.get('scenes') or [], 1):
        prefix = f'ch{ordinal}.scene{index}'
        lrefs = [str(ref) for ref in (scene.get('land_fact_refs') or [])]
        crefs = [str(ref) for ref in (scene.get('context_fact_refs') or [])]
        landed.extend(lrefs)
        if set(lrefs) - land:
            bad.append(f'{prefix}.land_fact_refs 越界 {sorted(set(lrefs) - land)}')
        if set(crefs) - context:
            bad.append(f'{prefix}.context_fact_refs 越界 {sorted(set(crefs) - context)}')
        if set(scene.get('material_refs') or []) - hinges:
            bad.append(f'{prefix}.material_refs 越界')
        if set(scene.get('stage_refs') or []) - beats:
            bad.append(f'{prefix}.stage_refs 越界')
        if scene.get('turn_fact_ref') not in lrefs:
            bad.append(f'{prefix}.turn_fact_ref 不在本场落地事实中')
    duplicates = sorted({ref for ref in landed if landed.count(ref) > 1})
    missing = sorted(land - set(landed))
    if missing or duplicates or bad:
        raise RefusedByStructure(
            f'ch{ordinal} 场景事实契约无法编译：缺失={missing}，重复={duplicates}，'
            f'引用错误={bad[:8]}')


def allocate_budgets(plan: dict[str, Any], blueprints: dict[str, Any],
                     bidx: dict[str, dict[str, Any]], *,
                     chapter_totals: dict[Any, int] | None = None) -> None:
    bp_of = {bp.get('chapter_ordinal'): bp for bp in (blueprints.get('blueprints') or [])}
    source, fact_sizes, gaps = budget_basis(blueprints, bidx)
    if gaps:
        raise RefusedByStructure(f'Stage2 有 {len(gaps)} 章没有可用 span，无法分配场景预算：{gaps}')
    chapters = list(plan.get('output_chapters') or [])
    if chapter_totals is None:
        raw = [source.get(ch.get('output_ordinal'), 1)
               * CHAPTER_WEIGHT_RATIO.get(ch.get('weight'), 1.0) for ch in chapters]
        totals = _round_shares(raw, sum(source.values()))
        chapter_totals = {ch.get('output_ordinal'): total
                          for ch, total in zip(chapters, totals)}
    for chapter in chapters:
        ordinal = chapter.get('output_ordinal')
        blueprint = bp_of.get(ordinal) or {}
        _validate_scene_refs(chapter, blueprint)
        scenes = list(chapter.get('scenes') or [])
        raw = [sum(fact_sizes.get(str(ref), 1)
                   for ref in (scene.get('land_fact_refs') or []))
               * UNIT_WEIGHT_FACTOR.get(scene.get('weight'), 1.0)
               for scene in scenes]
        budgets = _round_shares(raw, int(chapter_totals.get(ordinal, source.get(ordinal, 1))))
        for index, (scene, budget) in enumerate(zip(scenes, budgets), 1):
            scene['slot'] = f's{index:02d}'
            scene['budget_chars'] = budget
        chapter['semantic_contract'] = semantic_contract_of(blueprint)


def adopt_plan_response(plan: dict[str, Any], blueprints: dict[str, Any],
                        bidx: dict[str, dict[str, Any]], *,
                        chapter_totals: dict[Any, int] | None = None) -> int:
    decode_plan_response(plan)
    allocate_budgets(plan, blueprints, bidx, chapter_totals=chapter_totals)
    return sum(len(ch.get('scenes') or []) for ch in (plan.get('output_chapters') or []))


def identity_plan(blueprints: dict[str, Any], main_text: list[dict[str, Any]],
                  bidx: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """一章一组源序场景的显式基线；用于免费预览，不冒充重构结果。"""
    if bidx is None:
        bidx = beat_index(blueprints, main_text)
    output: list[dict[str, Any]] = []
    for bp in (blueprints.get('blueprints') or []):
        ordinal = int(bp.get('chapter_ordinal') or 0)
        atoms = list(bp.get('fact_atoms') or [])
        by_beat: dict[str, list[dict[str, Any]]] = {}
        for atom in atoms:
            by_beat.setdefault(str(atom.get('source_beat_ref') or ''), []).append(atom)
        scenes: list[dict[str, Any]] = []
        pending_context: list[str] = []
        pending_stages: list[str] = []
        pending_materials: list[str] = []
        for beat in (bp.get('beats') or []):
            bid = str(beat.get('beat_id') or '')
            beat_atoms = by_beat.get(bid, [])
            land = [str(a.get('fact_id')) for a in beat_atoms
                    if a.get('obligation') == 'land']
            pending_context.extend(str(a.get('fact_id')) for a in beat_atoms
                                   if a.get('obligation') == 'context')
            pending_stages.append(bid)
            pending_materials.extend(str(h.get('hinge_id'))
                                     for h in (beat.get('hinges') or []))
            if not land:
                continue
            participants = list(dict.fromkeys(
                list(beat.get('present_char_ids') or [])
                + [str(cid) for atom in beat_atoms
                   for cid in (atom.get('participant_char_ids') or [])]))
            scenes.append({
                'weight': 'mid', 'purpose': IDENTITY_GOAL, 'role': 'bridge',
                'delivery': 'scene', 'transition': 'start' if not scenes else 'continue',
                'land_fact_refs': land, 'context_fact_refs': list(dict.fromkeys(pending_context)),
                'material_refs': list(dict.fromkeys(pending_materials)),
                'turn_fact_ref': land[-1], 'entry_state_refs': [],
                'exit_state_refs': [],
                'participant_refs': participants,
                'stage_refs': list(dict.fromkeys(pending_stages)),
            })
            pending_context, pending_stages, pending_materials = [], [], []
        if not scenes:
            raise RefusedByStructure(f'ch{ordinal} 没有 land 事实，无法构造 Stage4 场景基线')
        scenes[0]['entry_state_refs'] = list(bp.get('entry_state_refs') or [])
        scenes[-1]['exit_state_refs'] = list(bp.get('ending_state_refs') or [])
        if pending_context or pending_stages or pending_materials:
            scenes[-1]['context_fact_refs'] = list(dict.fromkeys(
                scenes[-1]['context_fact_refs'] + pending_context))
            scenes[-1]['stage_refs'] = list(dict.fromkeys(
                scenes[-1]['stage_refs'] + pending_stages))
            scenes[-1]['material_refs'] = list(dict.fromkeys(
                scenes[-1]['material_refs'] + pending_materials))
        output.append({
            'output_ordinal': ordinal, 'from_source_ordinals': [ordinal],
            'chapter_goal': identity_goal(ordinal),
            'open_hook_goal': identity_goal(ordinal, 'open'),
            'end_hook_goal': identity_goal(ordinal, 'close'),
            'weight': 'even', 'chapter_rewrite_brief': IDENTITY_GOAL, 'scenes': scenes,
        })
    plan = {'reconstruction_strategy': IDENTITY_GOAL, 'output_chapters': output}
    adopt_plan_response(plan, blueprints, bidx)
    return plan


def verbatim_chars(hinges: list[dict[str, Any]]) -> int:
    return sum(len(piece) for piece in (verbatim_pieces(hinges)[0].get('verbatim') or []))


def verbatim_share_stats(plan: dict[str, Any], bidx: dict[str, dict[str, Any]]
                         ) -> tuple[dict[str, float] | None,
                                    list[tuple[Any, int, int, int]]]:
    hinges = {str(h.get('hinge_id')): h for row in bidx.values()
              for h in (row.get('hinges') or [])}
    shares: list[float] = []
    over: list[tuple[Any, int, int, int]] = []
    for chapter in (plan.get('output_chapters') or []):
        for index, scene in enumerate(chapter.get('scenes') or [], 1):
            vb = verbatim_chars([hinges[ref] for ref in scene.get('material_refs') or []
                                 if ref in hinges])
            budget = int(scene.get('budget_chars') or 0)
            if not vb or not budget:
                continue
            shares.append(vb / budget)
            if budget < VERBATIM_BUDGET_FACTOR * vb:
                over.append((chapter.get('output_ordinal'), index, vb, budget))
    if not shares:
        return None, over
    shares.sort()
    at = lambda q: shares[min(int(len(shares) * q), len(shares) - 1)]
    return {'n': len(shares), 'median': round(at(.5), 3), 'p90': round(at(.9), 3),
            'max': round(shares[-1], 3),
            'threshold': round(1 / VERBATIM_BUDGET_FACTOR, 3)}, over
