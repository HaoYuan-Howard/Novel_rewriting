#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 完整剧情呈现的确定性编译、结构读数与篇幅分配。"""
from __future__ import annotations

import hashlib
import heapq
import json
import math
from typing import Any

from contracts.invariants import (
    CHAPTER_WEIGHT_RATIO, PLAN_BUDGET_BAND, RESPONSE_CHAPTER_WEIGHTS,
    RESPONSE_DELIVERY_MODES, RESPONSE_GOAL_INTENTS, RESPONSE_STRUCTURAL_ROLES,
    RESPONSE_TEMPORAL_LINKS, RESPONSE_UNIT_WEIGHTS, STRUCTURE_CHANGE_KINDS,
    STRUCTURE_REVIEW_CONTRACT, UNIT_WEIGHT_FACTOR,
)
from novel.errors import RefusedByStructure
from novel.stages.stage4.beats import beat_index

IDENTITY_GOAL = '（恒等基线·未做任何重构决策）'
_GOAL_KEYS = ('chapter_goal', 'open_hook_goal', 'end_hook_goal')
_SCENE_ENUMS = {
    'role': RESPONSE_STRUCTURAL_ROLES,
    'delivery': RESPONSE_DELIVERY_MODES,
    'transition': RESPONSE_TEMPORAL_LINKS,
    'weight': RESPONSE_UNIT_WEIGHTS,
}


def semantic_contract_of(blueprint: dict[str, Any]) -> dict[str, Any]:
    return {
        'chapter_function': blueprint['chapter_function'],
        'beat_refs': [row['beat_id'] for row in blueprint['beats']],
        'state_refs': [row['state_id'] for row in blueprint['states']],
        'constraint_refs': [row['constraint_id'] for row in blueprint['constraints']],
        'entry_state_refs': list(blueprint['entry_state_refs']),
        'carry_out_refs': list(blueprint['carry_out_refs']),
    }


from novel.stages.stage4.plot_measure import structure_metrics_of


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
        changed.add('presentation_grouping')
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
            'subject_refs': value.get('subject_refs'),
            'anchor_beat_ref': value.get('anchor_beat_ref'),
            'anchor_scene_ref': value.get('anchor_scene_ref'),
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
            'beat_placements': chapter.get('beat_placements'),
            'anchor_bindings': chapter.get('anchor_bindings'),
            'scenes': [
                {key: scene.get(key) for key in (
                    'slot', 'budget_chars', 'purpose', 'role', 'delivery',
                    'transition', 'context_beat_refs', 'turn_beat_ref',
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


def structure_blueprint_subject(
    blueprints: Any, *, chapter_ordinal: int | None = None,
) -> dict[str, Any]:
    """结构审阅实际依赖的蓝图与完整证据；只排除包装 provenance。

    reading 的摘录不能代替证据身份：原文后半段变化也必须使旧决定失效。
    章级主体只取本章；全局 concern 由调用者显式扩大到全书，不能遗漏其跨章引用。
    """
    raw = (blueprints.model_dump(by_alias=True, mode='json')
           if hasattr(blueprints, 'model_dump') else blueprints)
    if not isinstance(raw, dict) or not isinstance(raw.get('blueprints'), list):
        raise ValueError('结构审阅缺当前蓝图证据')
    rows = raw['blueprints']
    if chapter_ordinal is not None:
        rows = [row for row in rows if row.get('chapter_ordinal') == chapter_ordinal]
        if len(rows) != 1:
            raise ValueError('结构审阅章必须对应唯一蓝图')
    return {'blueprints': rows}


def structure_review_subject_sha256(
    plan: Any, binding_concerns: list[Any], *,
    blueprints: Any,
    binding_input_contract: str | None = None,
) -> str:
    """最终结构主体：Plan、蓝图证据、B 担忧与实际章级输入契约。"""
    concerns = [
        value.model_dump(mode='json')
        if hasattr(value, 'model_dump') else value
        for value in binding_concerns
    ]
    payload = {
        'kind': 'stage4_structure_review_subject',
        'review_contract': STRUCTURE_REVIEW_CONTRACT,
        'plan_subject_sha256': structure_subject_sha256(plan),
        'blueprints': structure_blueprint_subject(blueprints),
        'binding_concerns': concerns,
        **({'binding_input_contract': binding_input_contract}
           if binding_input_contract is not None else {}),
    }
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')).hexdigest()


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


def budget_basis(blueprints: dict[str, Any], bidx: dict[str, dict[str, Any]] | None = None
                 ) -> tuple[dict[Any, int], dict[str, int], list[Any]]:
    """篇幅以经验证的源章正文为准；节点正文长度只作章内权重。"""
    chapters, sizes, gaps = {}, {}, []
    for bp in blueprints['blueprints']:
        ordinal = bp['chapter_ordinal']
        known = {row['source_chapter_chars'] for row in (bidx or {}).values()
                 if row['ord'] == ordinal}
        if len(known) != 1:
            gaps.append(ordinal)
        else:
            chapters[ordinal] = next(iter(known))
        for beat in bp['beats']:
            sizes[beat['beat_id']] = max(1, len(beat['content']))
    return chapters, sizes, gaps


def source_chars_table(blueprints: dict[str, Any], bidx: dict[str, dict[str, Any]] | None = None,
                       *, book_total: bool = False) -> str | None:
    sizes, _, gaps = budget_basis(blueprints, bidx)
    rows = [f'阅读序第{bp["chapter_ordinal"]}章：完整剧情 {len(bp["beats"])} 个，'
            f'源文 {sizes.get(bp["chapter_ordinal"], "未核算")} 字' for bp in blueprints['blueprints']]
    if book_total:
        rows.append(f'全书已核算源文 {sum(sizes.values())} 字；{len(gaps)} 章未核算')
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
    from contracts.schema.plot import PlotBlueprint
    from novel.stages.stage4.plot_placements import compile_plan_placements
    from contracts.schema.plot import PlotKnowledgeTrigger
    # 已审知识的触发身份由 A+B 冻结；完整 KnowledgeLinks 一致性在联合正式检查再核验。
    # 预算只核验其余呈现，不把缺失知识语义伪装成已通过。
    checked = {**chapter, 'anchor_bindings': [
        r for r in chapter['anchor_bindings'] if r['kind'] != 'knowledge']}
    try:
        compile_plan_placements(PlotBlueprint.model_validate(blueprint), checked)
    except ValueError as exc:
        raise RefusedByStructure(str(exc)) from exc


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
        # 父节点权重在其 deliveries 间均分，分场不会凭空抬高全章篇幅。
        scene_weights = {scene['slot']: 0.0 for scene in scenes}
        for placement in chapter['beat_placements']:
            share = fact_sizes[placement['beat_ref']] / len(placement['deliveries'])
            for delivery in placement['deliveries']:
                scene_weights[delivery['scene_ref']] += share
        raw = [scene_weights[scene['slot']]
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
    """按节点阅读序的免费参考；不是原文场景真值或已接受重构。"""
    from novel.stages.stage4.plot_binding import source_context_catalog
    from contracts.schema.plot import PlotBlueprint
    if bidx is None:
        bidx = beat_index(blueprints, main_text)
    output = []
    for raw in blueprints['blueprints']:
        bp = PlotBlueprint.model_validate(raw)
        ids = [b.beat_id for b in bp.beats]
        positions = {ref: i for i, ref in enumerate(ids)}
        # 必须同场时收拢该闭区间，不凭证据坐标发明源场景。
        boundaries = set(range(1, len(ids)))
        for constraint in bp.constraints:
            if constraint.kind == 'same_scene':
                left, right = sorted((positions[constraint.before_beat_ref], positions[constraint.after_beat_ref]))
                boundaries.difference_update(range(left + 1, right + 1))
        groups, current = [], []
        for i, ref in enumerate(ids):
            if i in boundaries:
                groups.append(current)
                current = []
            current.append(ref)
        groups.append(current)
        contexts = source_context_catalog(bp)
        scenes, placements, scene_of = [], [], {}
        for i, refs in enumerate(groups, 1):
            slot = f's{i:02d}'
            scenes.append({
                'slot': slot, 'weight': 'mid', 'purpose': IDENTITY_GOAL, 'role': 'bridge',
                'delivery': 'scene', 'transition': 'start' if i == 1 else 'continue',
                'context_beat_refs': [], 'turn_beat_ref': refs[-1],
            })
            for ref in refs:
                scene_of[ref] = slot
                beat = bp.beats[positions[ref]]
                placements.append({'beat_ref': ref, 'deliveries': [{
                    'scene_ref': slot, 'presentation_instruction': None,
                    'evidence_refs': list(beat.evidence_refs),
                    'source_context_refs': sorted(key for key, row in contexts.items()
                                                  if row['beat_ref'] == ref),
                }]})
        def goal(intent, ref):
            return {'intent': intent, 'subject_refs': [{'kind': 'beat', 'ref': ref}],
                    'anchor_beat_ref': ref, 'anchor_scene_ref': scene_of[ref]}
        output.append({
            'output_ordinal': bp.chapter_ordinal, 'from_source_ordinals': [bp.chapter_ordinal],
            'chapter_goal': goal('advance', ids[-1]), 'open_hook_goal': goal('open', ids[0]),
            'end_hook_goal': goal('close', ids[-1]),
            'structure_decision': {'mode': 'retain', 'reason': IDENTITY_GOAL, 'declared_change_kinds': []},
            'weight': 'even', 'chapter_rewrite_brief': IDENTITY_GOAL,
            'scenes': scenes, 'beat_placements': placements, 'anchor_bindings': [],
        })
    plan = {'output_chapters': output}
    adopt_plan_response(plan, blueprints, bidx)
    return plan
