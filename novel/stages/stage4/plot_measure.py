"""完整剧情的结构读数。证据定位可比较源披露序，不能据此推断原文场景或语义覆盖。"""
from __future__ import annotations

from itertools import combinations
from typing import Any


def constraint_and_stage_readings(blueprint: dict, chapter: dict, *, knowledge_links: dict):
    """按已登记约束和 B 选择计算读数，不从源坐标发明场景或语义覆盖。"""
    from contracts.schema.plot import PlotBlueprint
    from novel.stages.stage4.plot_binding import source_context_catalog
    from novel.stages.stage4.plot_placements import compile_plan_placements, knowledge_triggers_for_chapter
    typed = PlotBlueprint.model_validate(blueprint)
    compiled = compile_plan_placements(typed, chapter, knowledge=
                                      knowledge_triggers_for_chapter(knowledge_links, typed.chapter_ordinal))
    endpoints = {row.key(): row.scene_ref for row in compiled.bindings}
    contexts = source_context_catalog(typed)
    slots = [row['slot'] for row in chapter['scenes']]
    causal, literal, stage = [], [], []
    for constraint in typed.constraints:
        if constraint.kind in ('cause', 'story_before', 'response_to', 'observes'):
            left = endpoints[('constraint', constraint.constraint_id, 'before')]
            right = endpoints[('constraint', constraint.constraint_id, 'after')]
            causal.append({'constraint_ref': constraint.constraint_id, 'kind': constraint.kind,
                'before_slot': left, 'after_slot': right,
                'disclosure_order': ('same_scene' if left == right else
                                    'forward' if slots.index(left) < slots.index(right) else 'reverse')})
    for scene in chapter['scenes']:
        slot = scene['slot']
        budget = scene['budget_chars']
        if isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0:
            raise ValueError(f'{slot} 没有正整数预算，不能计算字面占比')
        values = sorted({c.value for c in typed.constraints if c.kind == 'literal'
                         and endpoints[('constraint', c.constraint_id, 'target')] == slot})
        maximum = max(map(len, values), default=0)
        literal.append({'slot': slot, 'constraints': len(values),
            'distinct_literal_chars': sum(map(len, values)), 'largest_literal_chars': maximum,
            'budget_chars': budget,
            'nominal_share': sum(map(len, values)) / budget,
            'single_literal_exceeds_budget': maximum > budget})
        selected = {ref for p in chapter['beat_placements'] for d in p['deliveries']
                    if d['scene_ref'] == slot for ref in d['source_context_refs']}
        signatures = {(
            contexts[ref]['time_layer'], contexts[ref]['place'],
            None if contexts[ref]['present_char_ids'] is None else tuple(sorted(contexts[ref]['present_char_ids'])),
        ) for ref in selected}
        stage.append({'slot': slot, 'context_refs': sorted(selected),
                      'signature_count': len(signatures), 'multiple_signatures': len(signatures) > 1,
                      'unknown_context': not selected or any(None in sig for sig in signatures)})
    return {'chapter_ordinal': typed.chapter_ordinal, 'causal': causal,
            'literal': literal, 'stage': stage}


def structure_metrics_of(blueprint: dict[str, Any], chapter: dict[str, Any]) -> dict[str, Any]:
    ordinal = chapter.get('output_ordinal')
    beats = {b['beat_id']: b for b in blueprint['beats']}
    slots = [s.get('slot', s.get('scene_ref')) for s in chapter['scenes']]
    placements = chapter['beat_placements']
    selected = {p['beat_ref']: [d['scene_ref'] for d in p['deliveries']] for p in placements}
    error = None
    if ordinal != blueprint['chapter_ordinal']:
        error = '章号不一致'
    elif (len(selected) != len(placements) or set(selected) != set(beats)
          or len(slots) != len(set(slots)) or not slots):
        error = '节点或场景身份不闭合'
    elif any(not targets or len(targets) != len(set(targets))
             or not set(targets) <= set(slots) for targets in selected.values()):
        error = '呈现引用缺失、重复或越界'
    elif set(slots) != set(t for targets in selected.values() for t in targets):
        error = '存在没有剧情呈现的场景'
    def unknown(reason):
        return {'state': 'unver', 'reason': reason}
    if error:
        result = {'state': 'unver', 'chapter_ordinal': ordinal, 'mapping': unknown(error)}
        result.update({k: unknown(error) for k in ('order', 'partition', 'boundary',
                                                  'noncontiguous', 'anchors', 'eligibility')})
        return result
    positions = {ref: sorted(slots.index(slot) for slot in targets)
                 for ref, targets in selected.items()}
    anchors = {a['evidence_id']: a for a in blueprint['evidence_anchors']}
    spans = {ref: (min(anchors[e]['start'] for e in b['evidence_refs']),
                   max(anchors[e]['end'] for e in b['evidence_refs'])) for ref, b in beats.items()}
    comparable = inverted = interleaved = 0
    for a, b in combinations(beats, 2):
        if spans[a][1] <= spans[b][0]:
            before, after = a, b
        elif spans[b][1] <= spans[a][0]:
            before, after = b, a
        else:
            continue
        comparable += 1
        if max(positions[after]) < min(positions[before]):
            inverted += 1
        elif max(positions[before]) >= min(positions[after]):
            interleaved += 1
    source_first = min(s for s, e in spans.values())
    source_last = max(e for s, e in spans.values())
    first = {ref for ref, (s, e) in spans.items() if s == source_first}
    last = {ref for ref, (s, e) in spans.items() if e == source_last}
    def edge(refs, slot):
        members = {ref for ref, values in positions.items() if slot in values}
        return {'scene_payload': {'state': 'ok',
                                  'excludes_source_edge': not bool(refs & members)}}
    grouping_reason = 'Blueprint 节点不是源场景，证据相邻或重叠不证明原文同场'
    return {
        'state': 'ok', 'chapter_ordinal': ordinal,
        'mapping': {'state': 'ok', 'beats': len(beats), 'mapped_beats': len(selected),
                    'deliveries': sum(len(v) for v in positions.values()),
                    'distributed_beats': sum(len(v) > 1 for v in positions.values()),
                    'plan_scenes': len(slots)},
        'order': {'state': 'ok', 'source_comparable_pairs': comparable,
                  'inverted_pairs': inverted, 'interleaved_pairs': interleaved,
                  'cross_scene_inversion_rate': inverted / comparable if comparable else None,
                  'definition': '全部证据区间互不重叠且父节点全部呈现可整体比较；交错另列'},
        'partition': unknown(grouping_reason),
        'boundary': unknown(grouping_reason),
        'noncontiguous': unknown('分场指令不含独立语义子节点，不能以父节点整段证据猜各次呈现的源位置'),
        'anchors': {'opening': edge(first, 0), 'ending': edge(last, len(slots) - 1)},
        'eligibility': unknown('已登记因果边不是所有不可移动语义的全集；改编自由度由语义审阅判断'),
    }
