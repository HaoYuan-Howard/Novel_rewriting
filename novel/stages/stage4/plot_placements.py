"""完整剧情到场景的确定性编译；不拆写或裁剪剧情正文。

唯一 placement 决定场景成员；显式语义端点决定知识、状态和约束位置。
缺失分场端点拒绝，不能用父节点的首次或末次出现猜。证据重叠只供审阅，
不代表重复剧情；本模块不产生语义 PASS。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from contracts.schema.plot import (
    PlotAnchorBinding, PlotBlueprint, PlotChapterArrangement, PlotGoalAnchor,
    PlotKnowledgeTrigger, PlotRelationConstraint,
)

EndpointKey = tuple[str, str, str]


@dataclass(frozen=True)
class PlacementCompilation:
    bindings: tuple[PlotAnchorBinding, ...]
    scene_beats: Mapping[str, tuple[str, ...]]
    completed_at: Mapping[str, str]
    # 只是证据定位读数，不能据此判定语义重复。
    shared_evidence: tuple[tuple[str, str, tuple[str, ...]], ...]


def required_endpoints(
    blueprint: PlotBlueprint, knowledge: Sequence[PlotKnowledgeTrigger],
    goals: Sequence[PlotGoalAnchor],
) -> dict[EndpointKey, str]:
    result: dict[EndpointKey, str] = {}

    def put(key: EndpointKey, beat: str) -> None:
        if key in result:
            raise ValueError(f'重复的语义端点主体：{key}')
        result[key] = beat

    for state in blueprint.states:
        for name, moment in (('established', state.established_at), ('ended', state.ended_at)):
            if moment is not None and moment.beat_ref is not None:
                put(('state', state.state_id, name), moment.beat_ref)
        if state.established_at.beat_ref is not None:
            for old_ref in state.supersedes_state_refs:
                put(('state', old_ref, 'ended'), state.established_at.beat_ref)
    for ending in blueprint.inherited_state_endings:
        put(('state', ending.state_ref, 'ended'), ending.at_beat_ref)
    for constraint in blueprint.constraints:
        if isinstance(constraint, PlotRelationConstraint):
            put(('constraint', constraint.constraint_id, 'before'), constraint.before_beat_ref)
            put(('constraint', constraint.constraint_id, 'after'), constraint.after_beat_ref)
        else:
            put(('constraint', constraint.constraint_id, 'target'), constraint.target_beat_ref)
    for row in knowledge:
        put(('knowledge', row.knowledge_ref, 'trigger'), row.trigger_beat_ref)
    for row in goals:
        put(('goal', row.goal_ref, row.kind), row.beat_ref)
    known = {beat.beat_id for beat in blueprint.beats}
    if not set(result.values()) <= known:
        raise ValueError('语义端点引用不存在的本章节点')
    return result


def compile_placements(
    blueprint: PlotBlueprint, arrangement: PlotChapterArrangement, *,
    knowledge: Sequence[PlotKnowledgeTrigger] = (),
    goals: Sequence[PlotGoalAnchor] = (),
) -> PlacementCompilation:
    if blueprint.chapter_ordinal != arrangement.chapter_ordinal:
        raise ValueError('呈现安排必须与蓝图同章')
    beats = {row.beat_id: row for row in blueprint.beats}
    placements = {row.beat_ref: row for row in arrangement.beat_placements}
    if placements.keys() != beats.keys():
        raise ValueError('placement 必须不重不漏覆盖本章剧情节点')
    indices = {scene.slot: index for index, scene in enumerate(arrangement.scenes)}
    by_scene: dict[str, list[str]] = {scene.slot: [] for scene in arrangement.scenes}
    completion: dict[str, str] = {}
    shared = []
    for ref, placement in placements.items():
        slots = [delivery.scene_ref for delivery in placement.deliveries]
        if slots != sorted(slots, key=indices.__getitem__):
            raise ValueError('呈现安排必须按目标场景阅读序排列')
        completion[ref] = slots[-1]
        evidence = set(beats[ref].evidence_refs)
        for delivery in placement.deliveries:
            if not set(delivery.evidence_refs) <= evidence:
                raise ValueError('呈现证据必须属于父剧情节点')
            by_scene[delivery.scene_ref].append(ref)
        for i, left in enumerate(placement.deliveries):
            for right in placement.deliveries[i + 1:]:
                overlap = tuple(sorted(set(left.evidence_refs) & set(right.evidence_refs)))
                if overlap:
                    shared.append((ref, f'{left.scene_ref}/{right.scene_ref}', overlap))
    if any(not values for values in by_scene.values()):
        raise ValueError('每个正式场景必须承载剧情呈现')
    for scene in arrangement.scenes:
        if len(scene.context_beat_refs) != len(set(scene.context_beat_refs)):
            raise ValueError('背景节点引用不得重复')
        for ref in scene.context_beat_refs:
            if ref not in completion:
                raise ValueError('背景引用必须属于本章')
            if indices[completion[ref]] >= indices[scene.slot]:
                raise ValueError('全部主要呈现完成之前不能把完整节点作为已知背景')

    requirements = required_endpoints(blueprint, knowledge, goals)
    explicit = {row.key(): row for row in arrangement.anchor_bindings}
    if explicit.keys() - requirements.keys():
        raise ValueError('端点映射不能创造知识、状态或约束主体')
    resolved: dict[EndpointKey, PlotAnchorBinding] = {}
    for key, beat in requirements.items():
        destinations = {row.scene_ref for row in placements[beat].deliveries}
        binding = explicit.get(key)
        if binding is not None:
            if binding.scene_ref not in destinations:
                raise ValueError('语义端点必须落在其父节点实际呈现的场景')
        elif len(destinations) == 1:
            binding = PlotAnchorBinding(
                kind=key[0], ref=key[1], endpoint=key[2], scene_ref=next(iter(destinations)))
        else:
            raise ValueError(f'分场节点的语义端点必须明确绑定：{key}')
        resolved[key] = binding
    # 替代是同一个持续状态变化，不能把旧状态的解除另移到别场。
    for state in blueprint.states:
        if state.established_at.beat_ref is not None:
            established = resolved[('state', state.state_id, 'established')].scene_ref
            if any(resolved[('state', ref, 'ended')].scene_ref != established
                   for ref in state.supersedes_state_refs):
                raise ValueError('取代状态的成立与旧状态解除必须绑定同一场')
    for constraint in blueprint.constraints:
        if constraint.kind == 'same_scene':
            left = resolved[('constraint', constraint.constraint_id, 'before')].scene_ref
            right = resolved[('constraint', constraint.constraint_id, 'after')].scene_ref
            if left != right:
                raise ValueError('明确的必须同场约束没有闭合')
    for goal in goals:
        slot = resolved[('goal', goal.goal_ref, goal.kind)].scene_ref
        if goal.kind == 'open' and slot != arrangement.scenes[0].slot:
            raise ValueError('开篇目标必须落在首场')
        if goal.kind == 'close' and slot != arrangement.scenes[-1].slot:
            raise ValueError('结尾目标必须落在末场')
    # 不按披露序判 cause/story_before 的真伪，合法倒叙不能被自动阻断。
    return PlacementCompilation(
        bindings=tuple(resolved[key] for key in sorted(resolved)),
        scene_beats={key: tuple(value) for key, value in by_scene.items()},
        completed_at=completion,
        shared_evidence=tuple(shared),
    )


def compile_plan_placements(blueprint: PlotBlueprint, chapter: dict, *,
                            knowledge: Sequence[PlotKnowledgeTrigger] = ()) -> PlacementCompilation:
    """正式 Plan 的目标/turn 自带端点，编译视图合入同一端点校验，不复制可编辑映射。"""
    goals = []
    bindings = list(chapter['anchor_bindings'])
    for name, kind in (('chapter_goal', 'chapter'), ('open_hook_goal', 'open'),
                       ('end_hook_goal', 'close')):
        goal = chapter[name]
        goals.append(PlotGoalAnchor(goal_ref=name, kind=kind, beat_ref=goal['anchor_beat_ref']))
        bindings.append({'kind': 'goal', 'ref': name, 'endpoint': kind,
                         'scene_ref': goal['anchor_scene_ref']})
    for scene in chapter['scenes']:
        ref = 'turn:' + scene['slot']
        goals.append(PlotGoalAnchor(goal_ref=ref, kind='turn', beat_ref=scene['turn_beat_ref']))
        bindings.append({'kind': 'goal', 'ref': ref, 'endpoint': 'turn', 'scene_ref': scene['slot']})
    arrangement = PlotChapterArrangement.model_validate({
        'chapter_ordinal': chapter['output_ordinal'],
        'scenes': [{key: scene[key] for key in
                   ('slot', 'purpose', 'role', 'delivery', 'transition', 'context_beat_refs')}
                   for scene in chapter['scenes']],
        'beat_placements': chapter['beat_placements'], 'anchor_bindings': bindings,
    })
    return compile_placements(blueprint, arrangement, knowledge=knowledge, goals=goals)


def knowledge_triggers_for_chapter(knowledge_links: dict, ordinal: int) -> tuple[PlotKnowledgeTrigger, ...]:
    """只取已闭合知识事件的真实触发节点，不从父节点首次出现推导许可。"""
    from contracts.schema.knowledge_links import KnowledgeLinks
    artifact = KnowledgeLinks.model_validate(knowledge_links)
    records = {r.obligation_id: r for r in artifact.links}
    result = []
    for obligation in artifact.scope.obligations:
        if obligation.chapter_ordinal != ordinal:
            continue
        resolution = records[obligation.obligation_id].resolution
        if resolution.kind == 'at_beats':
            result.append(PlotKnowledgeTrigger(
                knowledge_ref=obligation.obligation_id, trigger_beat_ref=resolution.trigger_beat_ref))
    return tuple(result)
