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

import re

from contracts.schema.design import BodyDesign
from contracts.schema.execution import (
    ConstraintSpec, DependencyEdge, ExecutionPlan, SceneExecutionPlan,
)
from contracts.schema.grounding import PreserveKind, SemanticBinding
from novel.stages.stage4.read_closure import init_establishments, resolve_design_reads
from novel.stages.stage4.guard import check_binding_beat_refs

#: 锚点句柄的源拍位置：``b{章}_{拍}``（s0_*／wc_* 是故事开始前锚点，无拍位置）。
_BEAT_ANCHOR = re.compile(r'^b(\d+)_(\d+)')


def _anchor_max_position(obligation) -> tuple[int, int] | None:
    """义务锚点的**最大**源拍位置（(章, 拍)；无可解析锚点返回 None）。

    D3（2026-10-03 CS-1）：按最大位置排——跨度事件的接受拍是最后一拍，
    揭示可合法发生在尝试过程内部（衣柜揭晓在借种当夜中间）；按最小位置排
    会把这类合法结构误报成倒置（r10b 实证 ob17/ob16）。
    """
    best: tuple[int, int] | None = None
    for anchor in obligation.anchors:
        m = _BEAT_ANCHOR.match(anchor.handle or '')
        if m is None:
            continue
        position = (int(m.group(1)), int(m.group(2)))
        if best is None or position > best:
            best = position
    return best



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
    # D3（2026-10-03 CS-1）：event/reveal 义务的承载序与锚点最大源拍位置不得
    # 倒置——按锚点最大位置排序后接受拍非递减（允许并列）。跨度复合义务的
    # 揭示可发生在过程内部（R10c c1），故不按 requires 前置拍的拍序硬卡，
    # 改按源文位置单调性判结构剧透；hook 不经 binding，天然不在本判定内。
    ordered = []
    for oid, entry in sorted(entries.items()):
        obligation = obligations.get(oid)
        if obligation is None or obligation.kind not in (
                PreserveKind.EVENT_CORE, PreserveKind.REVEAL):
            continue
        own = positions.get(entry.beat_ref)
        anchor_max = _anchor_max_position(obligation)
        if own is None or anchor_max is None:
            continue
        ordered.append((anchor_max, own, oid))
    ordered.sort()
    for (anchor_a, pos_a, oid_a), (anchor_b, pos_b, oid_b) in zip(ordered, ordered[1:]):
        if anchor_a < anchor_b and pos_a > pos_b:
            errors.append(
                f'锚点序与承载序倒置：{oid_a}（锚点最大源拍 {anchor_a[0]}_{anchor_a[1]}）'
                f'的接受拍晚于 {oid_b}（锚点最大源拍 {anchor_b[0]}_{anchor_b[1]}）'
                '——event/reveal 义务按源文顺序承载，允许并列、不得倒置（结构剧透）')
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
