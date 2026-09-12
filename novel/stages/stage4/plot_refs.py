"""剧情主体的关闭目录与正式计划检查；不再重建逐谓词事实表。"""
from __future__ import annotations

from typing import Any

from contracts.schema.plot import PlotBlueprint
from contracts.schema.stage4 import PlanChapter
from novel.core.evidence import without_global_evidence
from novel.errors import RefusedByStructure
from novel.stages.stage4.plot_binding import source_context_catalog
from novel.stages.stage4.plot_placements import (
    compile_plan_placements, knowledge_triggers_for_chapter,
)


def global_subjects(grounding: dict[str, Any]) -> dict[tuple[str, str], Any]:
    """原始 canonical 主体，仅编译器内部使用；可见性由独立消费视图处理。"""
    value = without_global_evidence(grounding)
    entities, canon = value['entities'], value['canon']
    # generation 使用独立 character_states；GlobalGrounding 消费视图把 fate 合入人物。
    states = (entities['character_states'] if 'character_states' in entities else {
        ref: {'fate': row['fate']} for ref, row in entities['characters'].items()
        if row.get('fate') is not None})
    return {
        **{('character', ref): row for ref, row in entities['characters'].items()},
        **{('character_state', ref): row for ref, row in states.items()},
        **{('relation', row['relation_id']): row for row in entities['relations']},
        **{('secret', row['secret_id']): row for row in canon['secrets']},
        **{('world_rule', row['rule_id']): row for row in canon['world_rules']},
        **{('setting', ref): row for ref, row in canon['setting'].items()},
    }


def validate_plan(plan: dict[str, Any], blueprints: dict[str, Any],
                  grounding: dict[str, Any], knowledge_links: dict[str, Any]) -> None:
    """同一检查用于 B、人工编辑、恢复和 Context；引用齐全不表示语义已审。"""
    bps = {row['chapter_ordinal']: PlotBlueprint.model_validate(row)
           for row in blueprints['blueprints']}
    chapters = [PlanChapter.model_validate(row) for row in plan['output_chapters']]
    if [row.output_ordinal for row in chapters] != sorted(bps):
        raise RefusedByStructure('计划须完整覆盖源书，同号同序，不拆合章节')
    globals_ = global_subjects(grounding)
    all_states = {row.state_id for bp in bps.values() for row in bp.states}
    for chapter in chapters:
        ordinal = chapter.output_ordinal
        bp = bps[ordinal]
        allowed = (set(globals_) | {('beat', row.beat_id) for row in bp.beats}
                   | {('state', ref) for ref in all_states
                      if ref in bp.entry_state_refs or any(s.state_id == ref for s in bp.states)}
                   | {('constraint', row.constraint_id) for row in bp.constraints}
                   | {('chapter_boundary', f'chapter:{ordinal}')})
        for goal in (chapter.chapter_goal, chapter.open_hook_goal, chapter.end_hook_goal):
            if any((row.kind, row.ref) not in allowed for row in goal.subject_refs):
                raise RefusedByStructure(f'第 {ordinal} 章目标引用不在当前关闭主体目录')
        contexts = source_context_catalog(bp)
        parents = {row.beat_id: row for row in bp.beats}
        for placement in chapter.beat_placements:
            parent = parents.get(placement.beat_ref)
            if parent is None:
                raise RefusedByStructure('呈现引用了不存在的本章节点')
            parent_contexts = {key for key, row in contexts.items()
                               if row['beat_ref'] == parent.beat_id}
            for delivery in placement.deliveries:
                if not delivery.evidence_refs:
                    raise RefusedByStructure('正式呈现必须绑定父节点证据')
                refs = delivery.source_context_refs
                if (len(refs) != len(set(refs)) or not set(refs) <= parent_contexts
                        or parent_contexts and not refs):
                    raise RefusedByStructure('呈现舞台引用必须来自父节点，不能丢弃已知舞台')
        try:
            compile_plan_placements(bp, chapter.model_dump(mode='json'), knowledge=
                                    knowledge_triggers_for_chapter(knowledge_links, ordinal))
        except ValueError as exc:
            raise RefusedByStructure(str(exc)) from exc
