"""只消费 D/G 编译执行计划；目标初始态来自 Design，义务来自完整 Binding。

B3 后半：本模块同时承载**章闭包证明**——每个已声明状态读取在其定位点（入场
或具体拍）解析到已建立事实（先读旧值、后写新值，与写手投影共用
`novel/stages/common.py::scene_read_views` 同一实现）；已声明依赖的引用闭合由
compile 机械证明，语义依赖是否遗漏归质量验收，机械检查不冒充证明。
"""
from __future__ import annotations

from contracts.schema.design import BodyDesign
from contracts.schema.execution import (
    ConstraintSpec, DependencyEdge, Establishment, ExecutionPlan, SceneExecutionPlan,
)
from contracts.schema.grounding import SemanticBinding
from novel.stages.common import scene_read_views
from novel.stages.stage4.fold import FoldedState
from novel.stages.stage4.guard import check_binding_beat_refs


def _establishment(delta, established_by: str) -> Establishment:
    """TargetDelta → Establishment：剥读者确认元数据（confirms 是 D4 的投影面，
    不是状态建立事实——Establishment 只承载可折叠的状态语义）。"""
    payload = {k: v for k, v in delta.model_dump().items() if k != 'confirms'}
    return Establishment(**payload, established_by=established_by)


def init_establishments(graph: BodyDesign) -> tuple[Establishment, ...]:
    """T₀ = Init(initial_state)：全部初始项原样装载，不按 op 过滤（B3 后半）。

    set（含合法空值 ''）、unset（显式解除）与 unknown（显式值未知）在 T₀ 是
    三种不同的事实状态，「未建立」才是缺席（I-42 四态分开）。F5e 实证的
    「unknown 初态垫底」不再是 compiler 静默洗掉的形状：垫底的 unknown 以其
    本面到达消费点、被「消费 unknown」闸拦下（毒化 fold 的失败保留为回归
    反例，见 tests/test_str06_chapter_closure.py），不由 T₀ 装载处洗白。"""
    return tuple(_establishment(d, 'init') for d in graph.initial_state)


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
    # 章闭包证明（B3 后半）：按阅读序折叠，逐场解析已声明读取——任何读取在其
    # 定位点（入场或具体拍）没有已建立事实（未建立／scene 作用域已失效）或解析到
    # unknown 都在这里 fail fast（str06.stage4.compile 免费重验），不留给写手投影。
    scene_establishments = {
        scene.scene_id: tuple(
            _establishment(d, f'{scene.scene_id}#{b.beat_id}')
            for b in scene.beats for d in b.target_delta)
        for scene in graph.scenes}
    folded = FoldedState().apply('__init__', init_establishments(graph))
    for scene in graph.scenes:
        _, _, read_errors = scene_read_views(
            scene=scene, establishments=scene_establishments[scene.scene_id],
            folded_prefix=folded)
        errors.extend(read_errors)
        folded.apply(scene.scene_id, scene_establishments[scene.scene_id])
        folded.expire_scene_scope(scene.scene_id)
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
