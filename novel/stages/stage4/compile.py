"""只消费 D/G 编译执行计划；目标初始态来自 Design，义务来自完整 Binding。

B3 后半：本模块同时承载**章闭包证明**——每个已声明状态读取在其定位点（入场
或具体拍）解析到已建立事实（先读旧值、后写新值，与写手投影共用
`novel/stages/common.py::scene_read_views` 同一实现）；已声明依赖的引用闭合由
compile 机械证明，语义依赖是否遗漏归质量验收，机械检查不冒充证明。

R2（引擎批）：依赖边改由**实际解析来源**生成——声明读取解析到的建立场
（`SceneReadViews.read_anchors`）与参与者背景补齐的事实来源场
（`participant_background`，同一折叠前缀、同一实现）。`last_writer` 位置扫描
删除：它按「最近一次 book 写入」猜来源，拍级读取解析到本场自写值时会产出
跨场假边，参与者背景的事实来源又完全不在其视野内（依赖算完整，Machine IR
以完整为先）。
"""
from __future__ import annotations

from contracts.schema.design import BodyDesign
from contracts.schema.execution import (
    ConstraintSpec, DependencyEdge, ExecutionPlan, SceneExecutionPlan,
)
from contracts.schema.grounding import SemanticBinding
from novel.stages.stage4.read_closure import (
    confirmation_closure_errors, init_establishments, resolve_design_reads,
)
from novel.stages.stage4.guard import check_binding_beat_refs

def compile_execution_plan(
    graph: BodyDesign, binding: SemanticBinding,
) -> tuple[ExecutionPlan | None, list[str]]:
    errors = check_binding_beat_refs(binding, graph)
    errors.extend(confirmation_closure_errors(graph))
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
            # 顺序三分（PLAN §二.3）：requires 是故事因果前提语义，不机械映射
            # 到场内阅读序——复合尝试义务的单一验收拍在场末，其揭示确认可合法
            # 发生在尝试过程内部（R10c c1 实证：床底/衣柜/房梁三对揭示先于
            # 尝试义务末拍）。跨场逆序仍是结构剧透硬闸；场内拍序交错放行
            # （信息披露顺序由 kp/kf 锁位置 Guard 单独保证）。
            if (before is not None and before > own
                    and predecessor.scene_id != entry.scene_id):
                errors.append(f'义务依赖逆序/成环：{req} 必须不晚于 {oid}（跨场）')
            if predecessor.scene_id != entry.scene_id:
                edges.add((predecessor.scene_id, entry.scene_id))
    # R-5.19：目标故事允许重排、倒叙；源锚点顺序不充当目标阅读序硬闸。
    # 因果、状态读取与认知授权仍分别由依赖闭合和 Guard 验证。
    # 依赖边＝义务/支撑拍边＋**实际解析来源**边（R2）：
    # · 每条声明读取在其定位点解析到的建立场（read_anchors；'init' 无边）；
    # · 参与者背景补齐的事实来源场（packet 的 background 分区消费这些值，
    #   来源变化即章包变化——依赖算完整）。解析到本场自写的值不产跨场边。
    closure = resolve_design_reads(graph)
    scene_establishments = closure.establishments
    resolved_edges = closure.dependencies
    errors.extend(closure.errors)
    edges |= set(resolved_edges)
    if errors:
        return None, errors
    scenes = []
    for scene in graph.scenes:
        establishments = scene_establishments[scene.scene_id]
        # scene 覆盖与 book 真值分别保存；临时覆盖不抹掉长期底值。
        net = {}
        for est in establishments:
            net[(*est.address(), est.scope)] = est
        owned = sorted(oid for oid, e in entries.items() if e.scene_id == scene.scene_id)
        constraints = tuple(ConstraintSpec(
            domain='obligation', key=oid,
            expected=obligations[oid].satisfaction, scope='scene')
            for oid in owned if obligations[oid].must_preserve)
        scenes.append(SceneExecutionPlan(
            scene_id=scene.scene_id, establishments=establishments,
            constraints=constraints,
            dependencies=tuple(DependencyEdge(
                before=a, after=b,
                kind=resolved_edges.get((a, b), 'obligation_or_support'))
                for a, b in sorted(edges) if b == scene.scene_id),
            planned_delta=tuple(net[k] for k in sorted(net))))
    return ExecutionPlan(initial_establishments=init_establishments(graph), scenes=tuple(scenes)), []
