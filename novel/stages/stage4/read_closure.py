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
    # confirms 是揭示关联，不是折叠状态；事实关联保留在 TargetDelta/IR。
    return Establishment(**delta.model_dump(exclude={'confirms'}), established_by=established_by)


def confirmation_closure_errors(graph: BodyDesign) -> list[str]:
    """读者确认只能引用此刻已建立的实际事实；不解释文本、不改输入。"""
    book = {d.address(): d for d in graph.initial_state}
    errors: list[str] = []
    for delta in graph.initial_state:
        if delta.confirms is not None:
            fact = book.get(delta.confirms.address())
            if fact is None or fact.op != 'set' or fact.modality != 'actual':
                errors.append(f'初始态的读者确认地址尚未实际建立：{delta.confirms.address()}')
    for scene in graph.scenes:
        local: dict = {}
        for beat in scene.beats:
            for delta in beat.target_delta:
                target = local if delta.scope == 'scene' else book
                target[delta.address()] = delta
            for delta in beat.target_delta:
                if delta.confirms is None:
                    continue
                address = delta.confirms.address()
                fact = local.get(address, book.get(address))
                if fact is None or fact.op != 'set' or fact.modality != 'actual':
                    errors.append(f'{scene.scene_id}#{beat.beat_id} 的读者确认地址尚未实际建立：'
                                  f'{address}（confirms 不得早于建立，未知/已解除/计划不能确认）')
    return errors


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
