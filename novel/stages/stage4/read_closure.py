"""Design 接受与执行计划共用的确定性读取闭包；不消费 Binding、不写盘。

先读旧值再应用本拍写入，由 scene_read_views 唯一解析；背景事实来源一并
返回供执行计划建立依赖。初始 unknown 原样装载，在消费点拒绝，不洗成缺失。

错误结构化（CS-F1 局部修订收口）：违例在判据处带稳定位置（初始行号，或
场／拍下标＋该拍内第几个带 confirms 的增量）——下标按 canonical 阅读序，
与 v8 wire 行同序（expand 保序派发），design 接受面据此映射可编辑 wire 路径；
compile 诊断面保留同一规则码与稳定位置，不附 wire 编辑路径。候选预检只在
Design 已报出同码同位置的可编辑问题时合并回声，独立重编译仍完整报告。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

from contracts.schema.design import BodyDesign
from contracts.schema.execution import Establishment
from novel.stages.common import ReadViewError, participant_background, scene_read_views
from novel.stages.stage4.fold import FoldedState


def _establishment(delta, established_by: str) -> Establishment:
    # confirms 是揭示关联，不是折叠状态；事实关联保留在 TargetDelta/IR。
    return Establishment(**delta.model_dump(exclude={'confirms'}), established_by=established_by)


@dataclass(frozen=True)
class ConfirmViolation:
    """一次「读者确认早于事实实际建立」的机械定位。

    · `initial`：初始态违例的 wire `initial_state` 行号（拍内违例为 None）；
    · `scene`／`beat`：拍内违例的场／拍下标（canonical 序＝v8 wire 序）；
    · `confirm_ordinal`：该拍内第几个带 `confirms` 的增量（0-based；wire know
      行里带 confirms 的第 N 个与之同序对应——设计面映射路径时按 wire 侧再取
      实际行号，不在此硬编码两套下标）。
    """

    message: str
    initial: int | None = None
    scene: int | None = None
    beat: int | None = None
    confirm_ordinal: int | None = None

    @property
    def location(self) -> tuple:
        """身份用的稳定位置（与措辞无关）：初始行号或（场id、拍id、序）。"""
        if self.initial is not None:
            return ('initial_state', self.initial)
        return (self.scene, self.beat, self.confirm_ordinal)


class ReadClosureError(NamedTuple):
    """一条读取闭包违例：判据码＋场下标＋场内读取行号＋消息（同 ReadViewError 加场）。"""

    code: str
    scene: int
    read: int
    message: str


def confirmation_closure_errors(graph: BodyDesign) -> tuple[ConfirmViolation, ...]:
    """读者确认只能引用此刻已实际建立的实际事实；不解释文本、不改输入。"""
    book = {d.address(): d for d in graph.initial_state}
    violations: list[ConfirmViolation] = []
    for row, delta in enumerate(graph.initial_state):
        if delta.confirms is not None:
            fact = book.get(delta.confirms.address())
            if fact is None or fact.op != 'set' or fact.modality != 'actual':
                violations.append(ConfirmViolation(
                    f'初始态的读者确认地址尚未实际建立：{delta.confirms.address()}',
                    initial=row))
    for scene_i, scene in enumerate(graph.scenes):
        local: dict = {}
        for beat_i, beat in enumerate(scene.beats):
            confirm_seen = 0
            for delta in beat.target_delta:
                target = local if delta.scope == 'scene' else book
                target[delta.address()] = delta
            for delta in beat.target_delta:
                if delta.confirms is None:
                    continue
                address = delta.confirms.address()
                fact = local.get(address, book.get(address))
                if fact is None or fact.op != 'set' or fact.modality != 'actual':
                    violations.append(ConfirmViolation(
                        f'{scene.scene_id}#{beat.beat_id} 的读者确认地址尚未实际建立：'
                        f'{address}（confirms 不得早于建立，未知/已解除/计划不能确认）',
                        scene=scene_i, beat=beat_i, confirm_ordinal=confirm_seen))
                confirm_seen += 1
    return tuple(violations)


def init_establishments(graph: BodyDesign) -> tuple[Establishment, ...]:
    """T₀ 保留 set（包括空值）、unset 与 unknown；未建立才是缺席。"""
    return tuple(_establishment(d, 'init') for d in graph.initial_state)


@dataclass(frozen=True)
class ReadClosure:
    establishments: dict[str, tuple[Establishment, ...]]
    dependencies: dict[tuple[str, str], str]
    errors: tuple[ReadClosureError, ...]


def resolve_design_reads(graph: BodyDesign) -> ReadClosure:
    """逐场折叠、解析声明读取与参与者背景，完整报告定位错误。"""
    scene_ids = {s.scene_id for s in graph.scenes}
    establishments = {
        s.scene_id: tuple(_establishment(d, f'{s.scene_id}#{b.beat_id}')
                          for b in s.beats for d in b.target_delta)
        for s in graph.scenes}
    folded = FoldedState().apply('__init__', init_establishments(graph))
    edges: dict[tuple[str, str], str] = {}
    errors: list[ReadClosureError] = []
    for scene_i, scene in enumerate(graph.scenes):
        views = scene_read_views(scene=scene,
                                 establishments=establishments[scene.scene_id],
                                 folded_prefix=folded)
        errors.extend(ReadClosureError(e.code, scene_i, e.read, e.message)
                      for e in views.errors)
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
