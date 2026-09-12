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


class PlotReferenceRefused(RefusedByStructure):
    """当前章的结构引用失败，保留归属供进度与 typed 修复路由使用。"""

    def __init__(self, chapter_ordinal: int | None, message: str) -> None:
        super().__init__(message)
        self.chapter_ordinal = chapter_ordinal


def global_subjects(grounding: dict[str, Any], *, characters: dict) -> dict[tuple[str, str], Any]:
    """仅供私有编译/校验的底稿目录；事实时间不是写手披露许可。"""
    from novel.consumption import stage4_grounding
    value = stage4_grounding(grounding)
    return {
        **{('character', ref): row for ref, row in (characters or {}).get('characters', {}).items()},
        **{('relation', row['relation_id']): row for row in value['relations']},
        **{('secret', row['secret_id']): row for row in value['arc_secrets']},
        **{('world_rule', row['rule_id'] + '/' + v['version_id']): {'kind': row['kind'], **v}
           for row in value['world_rules'] for v in row['versions']},
        **{('setting', ref): value['setting'][ref] for ref in ('era', 'social_context')
           if value['setting'].get(ref) is not None},
    }


def validate_plan(plan: dict[str, Any], blueprints: dict[str, Any],
                  grounding: dict[str, Any], knowledge_links: dict[str, Any], *,
                  characters: dict, main_text: list[dict[str, Any]]) -> None:
    """同一检查用于 B、人工编辑、恢复和 Context；引用齐全不表示语义已审。"""
    bps = {row['chapter_ordinal']: PlotBlueprint.model_validate(row)
           for row in blueprints['blueprints']}
    texts = {row['chapter_ordinal']: row['text'] for row in main_text}
    if not bps.keys() <= texts.keys():
        raise RefusedByStructure('人物呈现候选缺少源章，不能把未知当作空候选')
    chapters = []
    for raw in plan['output_chapters']:
        try:
            chapters.append(PlanChapter.model_validate(raw))
        except ValueError as exc:
            ordinal = raw.get('output_ordinal')
            raise PlotReferenceRefused(ordinal if type(ordinal) is int and ordinal > 0 else None,
                                       str(exc)) from exc
    if [row.output_ordinal for row in chapters] != sorted(bps):
        raise RefusedByStructure('计划须完整覆盖源书，同号同序，不拆合章节')
    globals_ = global_subjects(grounding, characters=characters)
    from contracts.schema.knowledge_links import KnowledgeLinks
    knowledge = KnowledgeLinks.model_validate(knowledge_links)
    all_states = {row.state_id for bp in bps.values() for row in bp.states}
    for chapter in chapters:
        ordinal = chapter.output_ordinal
        try:
            bp = bps[ordinal]
            allowed = (set(globals_) | {('beat', row.beat_id) for row in bp.beats}
                       | {('state', ref) for ref in all_states
                          if ref in bp.entry_state_refs or any(s.state_id == ref for s in bp.states)}
                       | {('constraint', row.constraint_id) for row in bp.constraints}
                       | {('chapter_boundary', f'chapter:{ordinal}')})
            allowed.update((event.subject_ref.kind, event.subject_ref.ref)
                           for event in knowledge.scope.obligations if event.chapter_ordinal == ordinal)
            for goal in (chapter.chapter_goal, chapter.open_hook_goal, chapter.end_hook_goal):
                if any((row.kind, row.ref) not in allowed for row in goal.subject_refs):
                    raise RefusedByStructure(f'第 {ordinal} 章目标引用不在当前关闭主体目录')
            from novel.stages.stage4.actors import ActorCatalog
            actors = ActorCatalog(characters or {'characters': {}}, blueprints,
                                  chapter_ordinal=ordinal, source_text='\n'.join(
                                      texts[number] for number in sorted(texts) if number <= ordinal),
                                  seed='private-validation')
            for goal in (chapter.chapter_goal, chapter.open_hook_goal, chapter.end_hook_goal):
                for ref in goal.subject_refs:
                    if ref.actor:
                        actors.resolve(ref.actor)
                    if ref.relation_actors:
                        relation = globals_[ref.raw_subject_key()]
                        for actor, field in ((ref.relation_actors.subject, 'subject_id'),
                                             (ref.relation_actors.object, 'object_id')):
                            if actors.resolve(actor).char_id != relation[field]:
                                raise RefusedByStructure('关系目标端点与底稿人物不一致')
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
        except (RefusedByStructure, ValueError, KeyError) as exc:
            raise PlotReferenceRefused(ordinal, str(exc)) from exc
