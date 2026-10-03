"""Design 接受与执行计划共用的确定性读取闭包；不消费 Binding、不写盘。

先读旧值再应用本拍写入，由 scene_read_views 唯一解析；背景事实来源一并
返回供执行计划建立依赖。初始 unknown 原样装载，在消费点拒绝，不洗成缺失。
"""
from __future__ import annotations

from dataclasses import dataclass

from contracts.schema.design import BodyDesign
from contracts.schema.execution import Establishment
from novel.stages.common import participant_background, scene_read_views
from novel.stages.stage4.fold import FoldedState


def _establishment(delta, established_by: str) -> Establishment:
    # confirms 已随 D5 统一删除（rc13）：Establishment 与 TargetDelta 字段同构。
    return Establishment(**delta.model_dump(), established_by=established_by)


def init_establishments(graph: BodyDesign) -> tuple[Establishment, ...]:
    """T₀ 保留 set（包括空值）、unset 与 unknown；未建立才是缺席。"""
    return tuple(_establishment(d, 'init') for d in graph.initial_state)


@dataclass(frozen=True)
class ReadClosure:
    establishments: dict[str, tuple[Establishment, ...]]
    dependencies: dict[tuple[str, str], str]
    errors: tuple[str, ...]


def resolve_design_reads(graph: BodyDesign) -> ReadClosure:
    """逐场折叠、解析声明读取与参与者背景，完整报告定位错误。"""
    scene_ids = {s.scene_id for s in graph.scenes}
    establishments = {
        s.scene_id: tuple(_establishment(d, f'{s.scene_id}#{b.beat_id}')
                          for b in s.beats for d in b.target_delta)
        for s in graph.scenes}
    folded = FoldedState().apply('__init__', init_establishments(graph))
    edges: dict[tuple[str, str], str] = {}
    errors: list[str] = []
    for scene in graph.scenes:
        views = scene_read_views(scene=scene,
                                 establishments=establishments[scene.scene_id],
                                 folded_prefix=folded)
        errors.extend(views.errors)
        state, knowledge = participant_background(scene=scene, folded_prefix=folded)
        for anchors, kind in ((views.read_anchors, 'resolved_read'),
                              ({e.established_by for e in (*state, *knowledge)},
                               'supplemented_baseline')):
            for anchor in sorted(anchors):
                source = anchor.split('#', 1)[0]
                if source in scene_ids and source != scene.scene_id:
                    edges.setdefault((source, scene.scene_id), kind)
        folded.apply(scene.scene_id, establishments[scene.scene_id])
        folded.expire_scene_scope(scene.scene_id)
    return ReadClosure(establishments, edges, tuple(errors))
