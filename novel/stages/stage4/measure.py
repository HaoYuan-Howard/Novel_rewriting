#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 原子事实 → 场景计划的确定性编译与篇幅分配。"""
from __future__ import annotations

from collections import Counter
import hashlib
import heapq
import json
import math
from typing import Any

from contracts.invariants import (
    CHAPTER_WEIGHT_RATIO, PLAN_BUDGET_BAND, RESPONSE_CHAPTER_WEIGHTS,
    RESPONSE_DELIVERY_MODES, RESPONSE_GOAL_INTENTS, RESPONSE_STRUCTURAL_ROLES,
    RESPONSE_TEMPORAL_LINKS, RESPONSE_UNIT_WEIGHTS, STRUCTURE_CHANGE_KINDS,
    STRUCTURE_METRIC_CONTRACT, STRUCTURE_REVIEW_CONTRACT,
    UNIT_WEIGHT_FACTOR, VERBATIM_BUDGET_FACTOR,
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
        'kind': 'chapter_boundary', 'ref': f'chapter:{chapter}',
    }]}


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


def structure_metrics_of(blueprint: dict[str, Any],
                         chapter: dict[str, Any]) -> dict[str, Any]:
    """逐章复算 Stage2 原子事实到 Stage4 场景的结构变化。

    这一个函数是结构读数的唯一数学落点。源顺序只认 ``SourceSpan`` 的半开
    区间：两段不重叠才建立先后，重叠证据合成同一个 source locus，不拿
    ``fact_id`` 或数组位置伪造顺序。源分区则只认 ``source_beat_ref``；计划
    分区只认 fact 最终落到的 scene。

    任何 land fact 缺失、重复落场、越界落场或空场都会让全部结构读数进入
    ``unver``。这是刻意的关闭式前件：残缺映射上的漂亮比例没有解释力。
    返回值只含 JSON 可序列化的标量、列表与字典，不修改任一输入。
    """

    ordinal = int(chapter.get('output_ordinal') or 0)
    source_ordinal = int(blueprint.get('chapter_ordinal') or 0)
    atoms = list(blueprint.get('fact_atoms') or [])
    land_atoms = [atom for atom in atoms if atom.get('obligation') == 'land']
    land_ids = [str(atom.get('fact_id') or '') for atom in land_atoms]
    scenes = list(chapter.get('scenes') or [])

    mapping_problems: list[str] = []
    if ordinal and source_ordinal and ordinal != source_ordinal:
        mapping_problems.append(f'章号不一致：plan={ordinal},source={source_ordinal}')
    if not land_ids:
        mapping_problems.append('没有 land fact')
    if any(not fact_id for fact_id in land_ids):
        mapping_problems.append('land fact_id 为空')
    duplicate_source_ids = sorted(
        fact_id for fact_id, count in Counter(land_ids).items() if count != 1)
    if duplicate_source_ids:
        mapping_problems.append(f'Stage2 land fact_id 重复：{duplicate_source_ids}')
    if not scenes:
        mapping_problems.append('计划没有场景')

    landed: list[str] = []
    empty_scenes: list[str] = []
    scene_of: dict[str, int] = {}
    for index, scene in enumerate(scenes):
        refs = [str(ref) for ref in (scene.get('land_fact_refs') or [])]
        if not refs:
            empty_scenes.append(str(scene.get('slot') or index + 1))
        landed.extend(refs)
        for ref in refs:
            scene_of[ref] = index
    if empty_scenes:
        mapping_problems.append(f'空场景：{empty_scenes}')

    landed_counts = Counter(landed)
    land_set = set(land_ids)
    missing = sorted(land_set - set(landed_counts))
    duplicate_landing = sorted(
        ref for ref in land_set if landed_counts.get(ref, 0) != 1)
    unknown = sorted(set(landed_counts) - land_set)
    if missing:
        mapping_problems.append(f'land fact 缺失：{missing}')
    if duplicate_landing:
        mapping_problems.append(f'land fact 非恰落一次：{duplicate_landing}')
    if unknown:
        mapping_problems.append(f'场景含越界 land ref：{unknown}')

    section_names = (
        'partition', 'order', 'adjacency', 'boundary',
        'noncontiguous', 'anchors', 'eligibility',
    )
    if mapping_problems:
        reason = '；'.join(mapping_problems)
        result: dict[str, Any] = {
            'state': 'unver',
            'chapter_ordinal': ordinal or source_ordinal,
            'mapping': {
                'state': 'unver', 'reason': reason,
                'land_facts': len(land_ids), 'mapped_refs': len(landed),
                'plan_scenes': len(scenes),
            },
        }
        result.update({name: _structure_unver(reason) for name in section_names})
        return result

    # 映射闭合后才允许进入任何比例计算。上面的 Counter 已经证明每个 land id
    # 恰好出现一次，因此此处的 scene_of 不会被 update 的最后一次命中掩盖重复。
    mapping = {
        'state': 'ok', 'land_facts': len(land_ids),
        'mapped_facts': len(scene_of), 'plan_scenes': len(scenes),
    }

    partition = _partition_metrics(land_atoms, scene_of)
    loci, locus_problem = _source_loci(land_atoms)
    if loci is None:
        source_reason = locus_problem or 'land fact 的 SourceSpan 不可用'
        source_unver = _structure_unver(source_reason)
        return {
            'state': 'ok', 'chapter_ordinal': ordinal or source_ordinal,
            'mapping': mapping,
            'partition': partition,
            'order': dict(source_unver),
            'adjacency': dict(source_unver),
            'boundary': _boundary_metrics(partition, land_atoms, scene_of),
            'noncontiguous': dict(source_unver),
            'anchors': dict(source_unver),
            'eligibility': dict(source_unver),
        }

    locus_of = {
        fact_id: index for index, locus in enumerate(loci)
        for fact_id in locus['fact_ids']
    }
    order = _order_metrics(land_atoms, scene_of)
    adjacency = _adjacency_metrics(loci, scene_of)
    boundary = _boundary_metrics(partition, land_atoms, scene_of)
    noncontiguous = _noncontiguous_metrics(scenes, locus_of)
    anchors = _anchor_metrics(chapter, scenes, loci, locus_of)
    eligibility = _eligibility_metrics(
        atoms, land_atoms, blueprint.get('causal_edges') or [], locus_of,
    )
    return {
        'state': 'ok', 'chapter_ordinal': ordinal or source_ordinal,
        'mapping': mapping,
        'partition': partition,
        'order': order,
        'adjacency': adjacency,
        'boundary': boundary,
        'noncontiguous': noncontiguous,
        'anchors': anchors,
        'eligibility': eligibility,
    }


def computed_structure_change_kinds(metrics: dict[str, Any]) -> list[str]:
    """把逐章读数收成关闭式变化向量；顺序由契约常量决定。"""

    changed: set[str] = set()
    anchors = metrics.get('anchors') or {}
    opening = (anchors.get('opening') or {}).get('scene_payload') or {}
    ending = (anchors.get('ending') or {}).get('scene_payload') or {}
    if opening.get('state') == 'ok' and opening.get('excludes_source_edge') is True:
        changed.add('entry_anchor')
    if ending.get('state') == 'ok' and ending.get('excludes_source_edge') is True:
        changed.add('ending_anchor')
    boundary = metrics.get('boundary') or {}
    if boundary.get('state') == 'ok' and boundary.get('changed') is True:
        changed.add('fact_grouping')
    order = metrics.get('order') or {}
    if order.get('state') == 'ok' and int(order.get('inverted_pairs') or 0) > 0:
        changed.add('disclosure_order')
    noncontiguous = metrics.get('noncontiguous') or {}
    if (noncontiguous.get('state') == 'ok'
            and int(noncontiguous.get('noncontiguous_scenes') or 0) > 0):
        changed.add('noncontiguous_regroup')
    return [name for name in STRUCTURE_CHANGE_KINDS if name in changed]


def structure_subject(plan: Any) -> dict[str, Any]:
    """结构人工门的 canonical 主体；不含来源账，也不把文件格式当决定语义。"""

    if hasattr(plan, 'model_dump'):
        raw = plan.model_dump(by_alias=True, mode='json')
    elif isinstance(plan, dict):
        raw = plan
    else:
        raise TypeError(f'expected PlanChapters or dict, got {type(plan).__name__}')

    def goal(value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        return {
            'intent': value.get('intent'),
            'fact_refs': value.get('fact_refs'),
        }

    chapters: list[dict[str, Any]] = []
    for chapter in (raw.get('output_chapters') or []):
        chapters.append({
            'output_ordinal': chapter.get('output_ordinal'),
            'from_source_ordinals': chapter.get('from_source_ordinals'),
            'structure_decision': chapter.get('structure_decision'),
            'chapter_goal': goal(chapter.get('chapter_goal')),
            'open_hook_goal': goal(chapter.get('open_hook_goal')),
            'end_hook_goal': goal(chapter.get('end_hook_goal')),
            'chapter_rewrite_brief': chapter.get('chapter_rewrite_brief'),
            'scenes': [
                {key: scene.get(key) for key in (
                    'slot', 'budget_chars', 'purpose', 'role', 'delivery',
                    'transition', 'land_fact_refs', 'context_fact_refs', 'material_refs',
                    'turn_fact_ref', 'entry_state_refs', 'exit_state_refs',
                    'stage_refs',
                )}
                for scene in (chapter.get('scenes') or [])
            ],
        })
    return {'output_chapters': chapters}


def structure_subject_sha256(plan: Any) -> str:
    payload = json.dumps(
        structure_subject(plan), ensure_ascii=False, sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def structure_review_subject_sha256(
    plan: Any, binding_concerns: list[Any], *,
    binding_input_contract: str | None = None,
) -> str:
    """最终结构主体：Plan、B 担忧与实际章级输入契约；旧非章模型保留原 hash。"""
    concerns = [
        value.model_dump(mode='json')
        if hasattr(value, 'model_dump') else value
        for value in binding_concerns
    ]
    payload = {
        'kind': 'stage4_structure_review_subject',
        'review_contract': STRUCTURE_REVIEW_CONTRACT,
        'plan_subject_sha256': structure_subject_sha256(plan),
        'binding_concerns': concerns,
        **({'binding_input_contract': binding_input_contract}
           if binding_input_contract is not None else {}),
    }
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')).hexdigest()


def _structure_unver(reason: str) -> dict[str, Any]:
    return {'state': 'unver', 'reason': reason}


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _partition_metrics(land_atoms: list[dict[str, Any]],
                       scene_of: dict[str, int]) -> dict[str, Any]:
    all_pairs = len(land_atoms) * (len(land_atoms) - 1) // 2
    if not all_pairs:
        return _structure_unver('land fact 不足 2 条，事实分区没有扫描分母')
    if any(not str(atom.get('source_beat_ref') or '') for atom in land_atoms):
        return _structure_unver('land fact 缺 source_beat_ref，源分区未知')

    preserved_together = split = merged = preserved_apart = 0
    for index, left in enumerate(land_atoms):
        left_id = str(left.get('fact_id'))
        for right in land_atoms[index + 1:]:
            right_id = str(right.get('fact_id'))
            source_same = left.get('source_beat_ref') == right.get('source_beat_ref')
            plan_same = scene_of[left_id] == scene_of[right_id]
            if source_same and plan_same:
                preserved_together += 1
            elif source_same:
                split += 1
            elif plan_same:
                merged += 1
            else:
                preserved_apart += 1
    source_together = preserved_together + split
    source_apart = merged + preserved_apart
    changed = split + merged
    return {
        'state': 'ok',
        'all_fact_pairs': all_pairs,
        'source_together_pairs': source_together,
        'source_apart_pairs': source_apart,
        'preserved_together_pairs': preserved_together,
        'split_pairs': split,
        'merged_pairs': merged,
        'preserved_apart_pairs': preserved_apart,
        'changed_pairs': changed,
        'distance': _ratio(changed, all_pairs),
        'split_rate': _ratio(split, source_together),
        'merge_rate': _ratio(merged, source_apart),
    }


def _source_interval(atom: dict[str, Any]) -> tuple[int, int] | None:
    source = atom.get('source')
    if not isinstance(source, dict):
        return None
    start, end = source.get('start'), source.get('end')
    if type(start) is not int or type(end) is not int or end <= start:
        return None
    return start, end


def _source_loci(land_atoms: list[dict[str, Any]]) \
        -> tuple[list[dict[str, Any]] | None, str | None]:
    rows: list[tuple[int, int, str]] = []
    for atom in land_atoms:
        fact_id = str(atom.get('fact_id') or '')
        interval = _source_interval(atom)
        if interval is None:
            return None, f'{fact_id} 没有合法 SourceSpan'
        rows.append((interval[0], interval[1], fact_id))
    rows.sort()
    loci: list[dict[str, Any]] = []
    for start, end, fact_id in rows:
        # SourceSpan 是半开区间；端点相接不重叠，应是两个可排序位置。
        if not loci or start >= int(loci[-1]['end']):
            loci.append({'start': start, 'end': end, 'fact_ids': [fact_id]})
        else:
            loci[-1]['end'] = max(int(loci[-1]['end']), end)
            loci[-1]['fact_ids'].append(fact_id)
    return loci, None


def _ordered_pair(left: dict[str, Any], right: dict[str, Any]) \
        -> tuple[str, str] | None:
    left_span, right_span = _source_interval(left), _source_interval(right)
    if left_span is None or right_span is None:
        return None
    if left_span[1] <= right_span[0]:
        return str(left.get('fact_id')), str(right.get('fact_id'))
    if right_span[1] <= left_span[0]:
        return str(right.get('fact_id')), str(left.get('fact_id'))
    return None


def _order_metrics(land_atoms: list[dict[str, Any]],
                   scene_of: dict[str, int]) -> dict[str, Any]:
    concordant = inverted = same_scene = source_incomparable = 0
    for index, left in enumerate(land_atoms):
        for right in land_atoms[index + 1:]:
            ordered = _ordered_pair(left, right)
            if ordered is None:
                source_incomparable += 1
                continue
            before, after = ordered
            delta = scene_of[after] - scene_of[before]
            if delta > 0:
                concordant += 1
            elif delta < 0:
                inverted += 1
            else:
                same_scene += 1
    comparable = concordant + inverted + same_scene
    if not comparable:
        return {
            **_structure_unver('没有 SourceSpan 严格可排序的 fact pair'),
            'source_incomparable_pairs': source_incomparable,
        }
    cross_scene = concordant + inverted
    tau_denom = math.sqrt(cross_scene * comparable) if cross_scene else 0.0
    return {
        'state': 'ok',
        'source_comparable_pairs': comparable,
        'source_incomparable_pairs': source_incomparable,
        'concordant_pairs': concordant,
        'inverted_pairs': inverted,
        'same_scene_ties': same_scene,
        'cross_scene_pairs': cross_scene,
        'cross_scene_inversion_rate': _ratio(inverted, cross_scene),
        'same_scene_tie_rate': _ratio(same_scene, comparable),
        'kendall_tau_b': ((concordant - inverted) / tau_denom
                          if tau_denom else None),
    }


def _adjacency_metrics(loci: list[dict[str, Any]],
                       scene_of: dict[str, int]) -> dict[str, Any]:
    edges = len(loci) - 1
    if edges <= 0:
        return _structure_unver('source locus 不足 2 个，相邻关系没有扫描分母')
    counts = Counter({
        'ambiguous_locus': 0,
        'same_scene': 0,
        'forward_adjacent': 0,
        'forward_separated': 0,
        'reversed_adjacent': 0,
        'reversed_separated': 0,
    })
    for left, right in zip(loci, loci[1:]):
        left_scenes = {scene_of[fact_id] for fact_id in left['fact_ids']}
        right_scenes = {scene_of[fact_id] for fact_id in right['fact_ids']}
        if len(left_scenes) != 1 or len(right_scenes) != 1:
            counts['ambiguous_locus'] += 1
            continue
        delta = next(iter(right_scenes)) - next(iter(left_scenes))
        if delta == 0:
            counts['same_scene'] += 1
        elif delta == 1:
            counts['forward_adjacent'] += 1
        elif delta > 1:
            counts['forward_separated'] += 1
        elif delta == -1:
            counts['reversed_adjacent'] += 1
        else:
            counts['reversed_separated'] += 1
    comparable = edges - counts['ambiguous_locus']
    return {
        'state': 'ok' if comparable else 'unver',
        'reason': None if comparable else '相邻 source locus 全部跨多个计划场，方向不可比较',
        'source_loci': len(loci),
        'source_adjacency_edges': edges,
        'comparable_edges': comparable,
        **dict(counts),
        # “保留”只允许同场或顺向相邻；绝不再用 abs(delta)。
        'directionally_retained': counts['same_scene'] + counts['forward_adjacent'],
        'directional_retention_rate': _ratio(
            counts['same_scene'] + counts['forward_adjacent'], comparable),
    }


def _boundary_metrics(partition: dict[str, Any],
                      land_atoms: list[dict[str, Any]],
                      scene_of: dict[str, int]) -> dict[str, Any]:
    if partition.get('state') != 'ok':
        return _structure_unver(str(partition.get('reason') or '事实分区不可比较'))
    source_groups = len({str(atom.get('source_beat_ref')) for atom in land_atoms})
    plan_groups = len(set(scene_of.values()))
    return {
        'state': 'ok',
        'changed': bool(partition['changed_pairs']),
        'source_groups': source_groups,
        'plan_groups': plan_groups,
        'group_count_changed': source_groups != plan_groups,
        'changed_pairs': partition['changed_pairs'],
        'all_fact_pairs': partition['all_fact_pairs'],
        'partition_distance': partition['distance'],
    }


def _noncontiguous_metrics(scenes: list[dict[str, Any]],
                           locus_of: dict[str, int]) -> dict[str, Any]:
    testable = noncontiguous = extra_runs = 0
    witnesses: list[dict[str, Any]] = []
    for index, scene in enumerate(scenes):
        ranks = sorted({locus_of[str(ref)] for ref in (scene.get('land_fact_refs') or [])})
        if len(ranks) < 2:
            continue
        testable += 1
        runs: list[list[int]] = [[ranks[0]]]
        for rank in ranks[1:]:
            if rank == runs[-1][-1] + 1:
                runs[-1].append(rank)
            else:
                runs.append([rank])
        if len(runs) > 1:
            noncontiguous += 1
            extra_runs += len(runs) - 1
            witnesses.append({
                'scene': str(scene.get('slot') or index + 1),
                'source_runs': [[run[0], run[-1]] for run in runs],
            })
    if not testable:
        return _structure_unver('没有含两个以上 source locus 的场景')
    return {
        'state': 'ok', 'testable_scenes': testable,
        'noncontiguous_scenes': noncontiguous,
        'noncontiguous_rate': _ratio(noncontiguous, testable),
        'extra_source_runs': extra_runs,
        'witnesses': witnesses,
    }


def _goal_fact_refs(chapter: dict[str, Any], key: str) -> list[str]:
    goal = chapter.get(key)
    if not isinstance(goal, dict):
        return []
    return [str(ref.get('ref')) for ref in (goal.get('fact_refs') or [])
            if isinstance(ref, dict) and ref.get('kind') == 'fact_atom']


def _declared_anchor(refs: list[str], locus_of: dict[str, int], *,
                     opening: bool, loci_count: int) -> dict[str, Any]:
    if loci_count < 2:
        return _structure_unver('source locus 不足 2 个，锚点迁移没有分母')
    if not refs:
        return _structure_unver('typed goal 没有 fact_atom 锚点')
    unknown = sorted(set(refs) - set(locus_of))
    if unknown:
        return _structure_unver(f'goal fact refs 不是 land fact：{unknown}')
    ranks = sorted({locus_of[ref] for ref in refs})
    if opening:
        offset = ranks[0]
    else:
        offset = loci_count - 1 - ranks[-1]
    return {
        'state': 'ok', 'fact_refs': refs, 'source_locus_ranks': ranks,
        'offset': offset, 'normalized_offset': offset / (loci_count - 1),
        'migrated': offset > 0,
    }


def _payload_anchor(scene: dict[str, Any], locus_of: dict[str, int], *,
                    opening: bool, loci_count: int) -> dict[str, Any]:
    if loci_count < 2:
        return _structure_unver('source locus 不足 2 个，首尾场迁移没有分母')
    ranks = sorted({locus_of[str(ref)] for ref in (scene.get('land_fact_refs') or [])})
    edge = 0 if opening else loci_count - 1
    return {
        'state': 'ok', 'source_locus_min': ranks[0],
        'source_locus_max': ranks[-1],
        'excludes_source_edge': edge not in ranks,
    }


def _anchor_metrics(chapter: dict[str, Any], scenes: list[dict[str, Any]],
                    loci: list[dict[str, Any]],
                    locus_of: dict[str, int]) -> dict[str, Any]:
    count = len(loci)
    return {
        'state': 'ok' if count >= 2 else 'unver',
        'reason': None if count >= 2 else 'source locus 不足 2 个',
        # goal 是“声明的入口/收束”；首尾 scene payload 是“结构容器”。两者都不
        # 冒充 Stage5 正文实际从哪一句起笔，后者仍需独立验收。
        'opening': {
            'declared_goal': _declared_anchor(
                _goal_fact_refs(chapter, 'open_hook_goal'), locus_of,
                opening=True, loci_count=count),
            'scene_payload': _payload_anchor(
                scenes[0], locus_of, opening=True, loci_count=count),
        },
        'ending': {
            'declared_goal': _declared_anchor(
                _goal_fact_refs(chapter, 'end_hook_goal'), locus_of,
                opening=False, loci_count=count),
            'scene_payload': _payload_anchor(
                scenes[-1], locus_of, opening=False, loci_count=count),
        },
    }


def _eligibility_metrics(atoms: list[dict[str, Any]],
                         land_atoms: list[dict[str, Any]],
                         causal_edges: list[dict[str, Any]],
                         locus_of: dict[str, int]) -> dict[str, Any]:
    all_ids = [str(atom.get('fact_id') or '') for atom in atoms]
    if any(not fact_id for fact_id in all_ids) or len(all_ids) != len(set(all_ids)):
        return _structure_unver('全章 fact id 不闭合，因果可重排分母未知')
    known = set(all_ids)
    reach = {fact_id: set() for fact_id in all_ids}
    for edge in causal_edges:
        before, after = str(edge.get('before') or ''), str(edge.get('after') or '')
        if before not in known or after not in known:
            return _structure_unver(f'因果边引用越界：{before}>{after}')
        reach[before].add(after)
    changed = True
    while changed:
        changed = False
        for fact_id in all_ids:
            indirect: set[str] = set()
            for after in reach[fact_id]:
                indirect.update(reach[after])
            if not indirect.issubset(reach[fact_id]):
                reach[fact_id].update(indirect)
                changed = True
    cyclic = sorted(fact_id for fact_id in all_ids if fact_id in reach[fact_id])
    if cyclic:
        return _structure_unver(f'因果图成环：{cyclic}')

    comparable = constrained = incomparable = noncontiguous_opportunities = 0
    for index, left in enumerate(land_atoms):
        left_id = str(left.get('fact_id'))
        for right in land_atoms[index + 1:]:
            ordered = _ordered_pair(left, right)
            if ordered is None:
                continue
            before, after = ordered
            # 同一源 beat 内的 atom 拆分是分区自由度，不把它冒充跨源场景重排。
            if left.get('source_beat_ref') == right.get('source_beat_ref'):
                continue
            comparable += 1
            if after in reach[before] or before in reach[after]:
                constrained += 1
                continue
            incomparable += 1
            if abs(locus_of[after] - locus_of[before]) >= 2:
                noncontiguous_opportunities += 1
    loci_count = len(set(locus_of.values()))
    source_groups = len({str(atom.get('source_beat_ref')) for atom in land_atoms})
    return {
        'state': 'ok',
        'definition': '不同源 beat、SourceSpan 可排序且因果传递闭包互不可达的保守句法下界',
        'source_loci': loci_count,
        'source_groups': source_groups,
        'cross_group_comparable_pairs': comparable,
        'causally_constrained_pairs': constrained,
        'causally_incomparable_pairs': incomparable,
        'reorderable': incomparable > 0,
        'noncontiguous_opportunity_pairs': noncontiguous_opportunities,
        'noncontiguous_eligible': noncontiguous_opportunities > 0,
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


def _bounded_round_shares(
    raw: list[float], total: int, lower: list[int], upper: list[int],
) -> list[int]:
    """按 ``raw`` 的相对权重分整数，并严格守住逐格上下界与总和。

    先把归一化后的精确份额钳进整数带，再用最大余量法逐字重分残差；堆中的
    第二排序键是原数组位置，因此同一输入逐字稳定。上下界来自产品契约，不是
    舍入建议：不可行时必须关闭式失败，不能像旧实现那样悄悄扩大 ``total``。
    """
    if not raw:
        if total == 0 and not lower and not upper:
            return []
        raise RefusedByStructure('预算分配没有单元，却收到非零总量或边界')
    if not (len(raw) == len(lower) == len(upper)):
        raise RefusedByStructure('预算权重与上下界数量不一致')
    if any(lo < 1 or hi < lo for lo, hi in zip(lower, upper)):
        raise RefusedByStructure('预算上下界不可行')
    if not sum(lower) <= total <= sum(upper):
        raise RefusedByStructure(
            f'预算总量 {total} 不在可分区间 [{sum(lower)}, {sum(upper)}]')

    denom = sum(raw)
    basis = raw if denom > 0 else [1.0] * len(raw)
    denom = sum(basis)
    exact = [value * total / denom for value in basis]
    out = [min(hi, max(lo, math.floor(value)))
           for value, lo, hi in zip(exact, lower, upper)]

    rest = total - sum(out)
    if rest > 0:
        # 优先把字给「精确份额 - 当前整数」最大的格；每加一字重新入堆。
        candidates = [(-(exact[i] - out[i]), i) for i in range(len(out))
                      if out[i] < upper[i]]
        heapq.heapify(candidates)
        while rest:
            if not candidates:               # 可行性前件守住后构造上到不了
                raise RefusedByStructure('预算余量没有可接收的章节/场景')
            _priority, index = heapq.heappop(candidates)
            out[index] += 1
            rest -= 1
            if out[index] < upper[index]:
                heapq.heappush(candidates, (-(exact[index] - out[index]), index))
    elif rest < 0:
        # 优先从「精确份额 - 当前整数」最小（超额最多）的格取回一字。
        candidates = [(exact[i] - out[i], i) for i in range(len(out))
                      if out[i] > lower[i]]
        heapq.heapify(candidates)
        while rest:
            if not candidates:               # 可行性前件守住后构造上到不了
                raise RefusedByStructure('预算超额没有可扣减的章节/场景')
            _priority, index = heapq.heappop(candidates)
            out[index] -= 1
            rest += 1
            if out[index] > lower[index]:
                heapq.heappush(candidates, (exact[index] - out[index], index))
    return out


def _round_shares(raw: list[float], total: int) -> list[int]:
    """无额外业务带的正整数分配；总和仍必须逐字等于 ``total``。"""
    return _bounded_round_shares(
        raw, total, [1] * len(raw), [total] * len(raw))


def _validate_scene_refs(chapter: dict[str, Any], blueprint: dict[str, Any]) -> None:
    # `checks` 本身复用本模块的结构读数，故在调用时导入以避开模块初始化环；
    # context 的产品口径仍只落在 checks.context_ref_findings 一处。
    from novel.stages.stage4 import checks

    ordinal = chapter.get('output_ordinal')
    atoms = {str(a.get('fact_id')): a for a in (blueprint.get('fact_atoms') or [])}
    hinges = {str(h.get('hinge_id')) for beat in (blueprint.get('beats') or [])
              for h in (beat.get('hinges') or [])}
    beats = {str(b.get('beat_id')) for b in (blueprint.get('beats') or [])}
    land = {fid for fid, atom in atoms.items() if atom.get('obligation') == 'land'}
    context = {fid for fid, atom in atoms.items() if atom.get('obligation') == 'context'}
    landed: list[str] = []
    bad: list[str] = []
    for index, scene in enumerate(chapter.get('scenes') or [], 1):
        prefix = f'ch{ordinal}.scene{index}'
        lrefs = [str(ref) for ref in (scene.get('land_fact_refs') or [])]
        crefs = [str(ref) for ref in (scene.get('context_fact_refs') or [])]
        landed.extend(lrefs)
        if set(lrefs) - land:
            bad.append(f'{prefix}.land_fact_refs 越界 {sorted(set(lrefs) - land)}')
        if set(scene.get('material_refs') or []) - hinges:
            bad.append(f'{prefix}.material_refs 越界')
        if set(scene.get('stage_refs') or []) - beats:
            bad.append(f'{prefix}.stage_refs 越界')
        if scene.get('turn_fact_ref') not in lrefs:
            bad.append(f'{prefix}.turn_fact_ref 不在本场落地事实中')
    bad.extend(
        f'ch{ordinal}.{row}.context_fact_refs 越界'
        for row in checks.context_ref_findings(
            list(chapter.get('scenes') or []),
            context_obligation_refs=context,
            land_key='land_fact_refs', context_key='context_fact_refs')
    )
    duplicates = sorted({ref for ref in landed if landed.count(ref) > 1})
    missing = sorted(land - set(landed))
    if missing or duplicates or bad:
        raise RefusedByStructure(
            f'ch{ordinal} 场景事实契约无法编译：缺失={missing}，重复={duplicates}，'
            f'引用错误={bad[:8]}')


def allocate_budgets(plan: dict[str, Any], blueprints: dict[str, Any],
                     bidx: dict[str, dict[str, Any]], *,
                     chapter_totals: dict[Any, int] | None = None) -> None:
    """把章/场档位编译成正整数预算，并守住两条篇幅 HARD 的后置条件。

    整书路径先按 ``源章字数 × 章档位`` 求相对份额，再把每章夹在
    ``PLAN_BUDGET_BAND`` 内，将夹出的余量在仍有容量的章之间确定性重分；最终
    全书总量逐字等于源 span 合计。给定 ``chapter_totals`` 是定向修复路径：章
    总量沿用基线，只重新分配场景，但仍拒绝一个带外或装不下正整数场景的基线。
    """
    bp_of = {bp.get('chapter_ordinal'): bp for bp in (blueprints.get('blueprints') or [])}
    source, fact_sizes, gaps = budget_basis(blueprints, bidx)
    if gaps:
        raise RefusedByStructure(f'Stage2 有 {len(gaps)} 章没有可用 span，无法分配场景预算：{gaps}')
    chapters = list(plan.get('output_chapters') or [])
    ordinals = [chapter.get('output_ordinal') for chapter in chapters]
    if len(ordinals) != len(set(ordinals)):
        raise RefusedByStructure(f'Stage4 输出章号重复，无法分配预算：{ordinals}')
    if chapter_totals is None and set(ordinals) != set(source):
        raise RefusedByStructure(
            f'Stage4 章集与预算基准不闭合：计划={sorted(ordinals)}，'
            f'基准={sorted(source)}')

    lo_ratio, hi_ratio = PLAN_BUDGET_BAND
    lower: list[int] = []
    upper: list[int] = []
    for chapter in chapters:
        ordinal = chapter.get('output_ordinal')
        base = source.get(ordinal)
        if base is None:
            raise RefusedByStructure(f'第 {ordinal} 章没有预算基准')
        # 每场预算还须为正整数；场数因此也是章总量的构造性下界。
        lower.append(max(math.ceil(base * lo_ratio), len(chapter.get('scenes') or []), 1))
        upper.append(math.floor(base * hi_ratio))

    if chapter_totals is None:
        raw = [source.get(ch.get('output_ordinal'), 1)
               * CHAPTER_WEIGHT_RATIO.get(ch.get('weight'), 1.0) for ch in chapters]
        totals = _bounded_round_shares(raw, sum(source.values()), lower, upper)
        chapter_totals = {ch.get('output_ordinal'): total
                          for ch, total in zip(chapters, totals)}
    else:
        normalized: dict[Any, int] = {}
        for chapter, lo, hi in zip(chapters, lower, upper):
            ordinal = chapter.get('output_ordinal')
            value = chapter_totals.get(ordinal, source[ordinal])
            if type(value) is not int or not lo <= value <= hi:
                raise RefusedByStructure(
                    f'第 {ordinal} 章钉定预算 {value!r} 不在可分整数带 [{lo}, {hi}]')
            normalized[ordinal] = value
        chapter_totals = normalized
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
            scene.pop('weight', None)
        chapter.pop('weight', None)


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
            scenes.append({
                'weight': 'mid', 'purpose': IDENTITY_GOAL, 'role': 'bridge',
                'delivery': 'scene', 'transition': 'start' if not scenes else 'continue',
                'land_fact_refs': land, 'context_fact_refs': list(dict.fromkeys(pending_context)),
                'material_refs': list(dict.fromkeys(pending_materials)),
                'turn_fact_ref': land[-1], 'entry_state_refs': [],
                'exit_state_refs': [],
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
            'structure_decision': {
                'mode': 'retain', 'reason': IDENTITY_GOAL,
                'declared_change_kinds': [],
            },
            'weight': 'even', 'chapter_rewrite_brief': IDENTITY_GOAL, 'scenes': scenes,
        })
    plan = {'output_chapters': output}
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
