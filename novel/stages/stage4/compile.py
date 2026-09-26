"""只消费 D/G 编译执行计划；目标初始态来自 Design，义务来自完整 Binding。"""
from __future__ import annotations

from contracts.schema.design import BodyDesign
from contracts.schema.execution import (
    ConstraintSpec, DependencyEdge, Establishment, ExecutionPlan, SceneExecutionPlan,
)
from contracts.schema.grounding import SemanticBinding
from novel.stages.stage4.guard import check_binding_beat_refs


def init_establishments(graph: BodyDesign) -> tuple[Establishment, ...]:
    """T₀ = Init(initial_state)；op != 'set' 的初始项在 T₀ 无语义（没有可解除、
    也没有可标记为 unknown 的既有事实——「未建立」本身就是缺省），等价归一跳过。
    F5e 真书实证：模型用 unknown 初始项表达「尚未知晓」，与缺席等价且会毒化
    entry fold（unknown 消费阻断），故在 T₀ 装载处统一跳过。"""
    return tuple(Establishment(**d.model_dump(), established_by='init')
                 for d in graph.initial_state if d.op == 'set')


def compile_execution_plan(
    graph: BodyDesign, binding: SemanticBinding,
) -> tuple[ExecutionPlan | None, list[str]]:
    errors = check_binding_beat_refs(binding, graph)
    obligations = {o.obligation_id: o for o in binding.obligations}
    entries = {e.obligation_id: e for e in binding.entries}
    returned = {e.obligation_id for e in binding.unbound}
    if set(entries) | returned != set(obligations):
        errors.append('Binding 义务分母不闭合')
    for oid in sorted(returned & set(obligations)):
        if obligations[oid].must_preserve:
            errors.append(f'必保义务 {oid} 尚未承载，返回 owner 修复')
    for start in sorted(obligations):
        frontier, seen = list(obligations[start].requires), set()
        while frontier:
            oid = frontier.pop()
            if oid == start:
                errors.append(f'义务依赖成环：{start}')
                break
            if oid in seen:
                continue
            seen.add(oid)
            if oid not in obligations:
                errors.append(f'义务依赖悬空：{oid}')
                continue
            frontier.extend(obligations[oid].requires)
    positions = {f'{s.scene_id}#{b.beat_id}': (si, bi)
                 for si, s in enumerate(graph.scenes) for bi, b in enumerate(s.beats)}
    edges: set[tuple[str, str]] = set()
    for oid, entry in sorted(entries.items()):
        own = positions.get(entry.beat_ref)
        if own is None:
            continue
        for ref in entry.supporting_beat_refs:
            if ref in positions and positions[ref] >= own:
                errors.append(f'{oid} 支撑拍 {ref} 必须早于最终验收拍 {entry.beat_ref}')
            sid = ref.split('#', 1)[0]
            if sid != entry.scene_id:
                edges.add((sid, entry.scene_id))
        obligation = obligations.get(oid)
        for req in obligation.requires if obligation is not None else ():
            predecessor = entries.get(req)
            if predecessor is None:
                errors.append(f'{oid} 的前置义务 {req} 未承载')
                continue
            before = positions.get(predecessor.beat_ref)
            if before is not None and before > own:
                errors.append(f'义务依赖逆序/成环：{req} 必须不晚于 {oid}')
            if predecessor.scene_id != entry.scene_id:
                edges.add((predecessor.scene_id, entry.scene_id))
    # 实际状态读取也产生前置边，否则变更后的重投影闭包会漏掉状态消费者。
    last_writer: dict[tuple, str] = {}
    for scene in graph.scenes:
        for read in scene.state_reads:
            before = last_writer.get(read.address())
            if before and before != scene.scene_id:
                edges.add((before, scene.scene_id))
        for beat in scene.beats:
            for delta in beat.target_delta:
                if delta.scope == 'book':
                    last_writer[delta.address()] = scene.scene_id
    if errors:
        return None, errors
    scenes = []
    for scene in graph.scenes:
        establishments = tuple(
            Establishment(**d.model_dump(), established_by=f'{scene.scene_id}#{b.beat_id}')
            for b in scene.beats for d in b.target_delta)
        # scene 覆盖与 book 真值分别保存；临时覆盖不抹掉长期底值。
        net = {}
        for est in establishments:
            net[(*est.address(), est.scope)] = est
        owned = sorted(oid for oid, e in entries.items() if e.scene_id == scene.scene_id)
        constraints = tuple(ConstraintSpec(
            domain='reader', key=oid,
            expected=obligations[oid].satisfaction, scope='scene')
            for oid in owned if obligations[oid].must_preserve)
        scenes.append(SceneExecutionPlan(
            scene_id=scene.scene_id, establishments=establishments,
            constraints=constraints,
            dependencies=tuple(DependencyEdge(before=a, after=b, kind='obligation_or_support')
                               for a, b in sorted(edges) if b == scene.scene_id),
            planned_delta=tuple(net[k] for k in sorted(net))))
    return ExecutionPlan(initial_establishments=init_establishments(graph), scenes=tuple(scenes)), []
