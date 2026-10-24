"""单一目标 Fold：按显式地址/操作重放，仅 accepted prefix 的 planned_delta 可传播。

叠加机制用 `novel.stages.common.StateOverlay`（D16：Stage2 源折叠与 Stage4 计划
折叠共用的唯一实现）；本类只负责把目标侧 `Establishment` 适配成规范变化，并投影
成消费面视图（StateEntry／KnowledgeEntry）。

【ThreadFold（R2.4，设计原则 4）】跨章语义是拍上的转移，不走状态叠加：线程
（open→advance→close）由**独立的 ThreadFold** 按阅读序重放——状态走
`FoldedState`，线程走 `ThreadFold`，两条折叠互不掺和。ThreadFold 是生产方
（`compile_writer_book_ir` 经它把 thread_ops／open_threads 投进 VerificationIR）；
`guard.expected_thread_faces` 是 M8 的独立对账基准，两者判据同源、实现分离，
漂移由 M8 当场红。
"""
from __future__ import annotations

from typing import Iterable

from contracts.schema.design import BodyDesign, TargetScene
from contracts.schema.execution import EffectDomain, Establishment, SceneExecutionPlan
from contracts.schema.context import (
    KnowledgeEntry, OpenThreadIR, StateEntry, ThreadOpIR,
)
from novel.stages.common import OverlayChange, StateOverlay

INIT_ANCHOR = 'init'


class FoldedState:
    """可重建派生状态；scene 覆盖保留 book 底值，解除/未知/未建立分别表达。"""

    def __init__(self) -> None:
        self._overlay = StateOverlay()
        # 听到过某种说法是历史，不是对该命题的当前接受。两者从同一批正式
        # Establishment 派生；正反说法、不同建立锚均保留，撤回只作用于接受。
        self._heard = StateOverlay()

    def apply(self, scene_id: str, establishments: Iterable[Establishment]) -> 'FoldedState':
        for e in establishments:
            change = OverlayChange(address=e.address(), op=e.op, value=e.value,
                                   scope=e.scope, source=e.established_by,
                                   mode=e.knowledge_mode)
            if e.domain is EffectDomain.KNOWLEDGE and e.knowledge_mode == 'heard':
                identity = (*e.address(), e.value, e.established_by, e.scope)
                self._heard.apply(scene_id, [change._replace(address=identity)])
            else:
                self._overlay.apply(scene_id, [change])
        return self

    def expire_scene_scope(self, scene_id: str) -> 'FoldedState':
        self._overlay.expire_scene_scope(scene_id)
        self._heard.expire_scene_scope(scene_id)
        return self

    def with_scene_changes(self, scene_id: str,
                           changes: Iterable[Establishment]) -> 'FoldedState':
        """同态派生（B3 后半）：在副本上叠加场内、读取位置之前的建立，原 fold
        不变——「先写后读」的拍读取在（前文折叠＋已写拍）上解析（先读旧值、
        后写新值；`common.scene_read_views` 消费本入口）。"""
        clone = FoldedState()
        clone._overlay = self._overlay.copy()
        clone._heard = self._heard.copy()
        clone.apply(scene_id, changes)
        return clone

    def _entry_of(self, change: OverlayChange) -> Establishment:
        """规范变化还原成 Establishment（地址四段从 address 元组拆回）。"""
        domain, subjects, holder, key = change.address[:4]
        return Establishment(
            domain=EffectDomain(domain), key=key, subjects=tuple(subjects),
            holder=holder or None, op=change.op, value=change.value,
            scope=change.scope, established_by=change.source,
            knowledge_mode=change.mode)

    def entry_state_view(self, scene_start_anchor: str, domains=(
            EffectDomain.CHARACTER, EffectDomain.RELATION,
            EffectDomain.WORLD)) -> tuple[StateEntry, ...]:
        # 读者揭示是 knowledge(holder=reader)（D5 统一，rc13）——经
        # entry_knowledge_view 投影，state 视图不再有 reader 域。
        return tuple(StateEntry(
            domain=e.domain, key=e.key, subjects=e.subjects, value=e.value,
            status=e.op, established_by=e.established_by)
            for e in (self._entry_of(c) for c in self._overlay.entries())
            if e.domain in domains)

    def entry_knowledge_view(self) -> tuple[KnowledgeEntry, ...]:
        # 撤回只解除当前接受，既不伪造 known，也不删除此前发生过的认知变化。
        return tuple(KnowledgeEntry(
            holder=e.holder, subjects=e.subjects, key=e.key,
            mode=e.knowledge_mode,
            polarity=('negative' if e.value == 'false' else 'positive')
            if e.op == 'set' else None,
            proposition=f'{e.key}={e.value}', status=e.op, established_by=e.established_by)
            for e in (self._entry_of(c)
                      for c in (*self._overlay.entries(), *self._heard.entries()))
            if e.domain is EffectDomain.KNOWLEDGE)

    def value_of(self, domain: EffectDomain, key: str, *, subjects=(), holder=None) -> str | None:
        fact = self._overlay.visible().get((domain.value, tuple(subjects), holder or '', key))
        return fact.value if fact and fact.op == 'set' else None


def fold_accepted_prefix(plans: Iterable[SceneExecutionPlan], *,
                         initial: Iterable[Establishment] = ()) -> FoldedState:
    state = FoldedState().apply('__init__', initial)
    for plan in plans:
        state.apply(plan.scene_id, plan.planned_delta).expire_scene_scope(plan.scene_id)
    return state


class ThreadFold:
    """独立线程折叠（R2.4）：按阅读序重放拍上的线程操作，产出每场双面投影。

    `apply(scene)` 一趟完成两件事（先快照后重放，与
    `guard.expected_thread_faces` 同判据）：

    · **本场操作**（must_realize 数据面）：本场各拍上的 open／advance／close
      按拍阅读序投影成 `ThreadOpIR`；advance／close 的声明内容（kind／subject／
      text）取自 open——文本只写一次，折叠不改写声明；
    · **入场快照**（continuity／guard_only 数据面）：`apply` 前仍开放、主体与
      本场参与者相关（participants∪focal_actor∪人物 pov）、**本场没有操作**的
      线程投影成 `OpenThreadIR`——E-4 分支：先判本场操作，再按 kind 分节
      （question→guard_only，其余→continuity），不相关的开放线程不投影
      （全场景保护不等于每场提醒）。

    生命周期非法（操作早于 open 等）由 canonical schema 与 M8 生命周期段拒绝；
    本折叠对未知 ref 的操作按 M8 纪律跳过（不在此重复报错）。结尾仍开放的
    线程合法（留线是设计自由）。
    """

    def __init__(self) -> None:
        self._opened: dict[str, list] = {}   # ref -> [kind, subject, text, anchor, closed]

    def apply(self, scene: TargetScene) -> tuple[
            tuple[ThreadOpIR, ...], tuple[OpenThreadIR, ...]]:
        participants = {p for b in scene.beats for p in b.participants}
        participants.add(scene.driver.focal_actor)
        if scene.delivery.pov != 'narrator':
            participants.add(scene.delivery.pov)
        operated = {t.ref for b in scene.beats for t in b.threads}
        snapshot = tuple(
            OpenThreadIR(ref=ref, kind=state[0], subject=state[1],
                         text=state[2], opened_at=state[3])
            for ref, state in sorted(self._opened.items())
            if not state[4] and ref not in operated and state[1] in participants)
        ops: list[ThreadOpIR] = []
        for beat in scene.beats:
            for thread in beat.threads:
                if thread.op == 'open':
                    self._opened[thread.ref] = [
                        thread.kind, thread.subject, thread.text,
                        f'{scene.scene_id}#{beat.beat_id}', False]
                    ops.append(ThreadOpIR(
                        ref=thread.ref, op='open', kind=thread.kind,
                        subject=thread.subject, text=thread.text))
                    continue
                state = self._opened.get(thread.ref)
                if state is None:
                    continue          # 生命周期非法归 M8 生命周期段报告
                payload = ({'progress': thread.progress} if thread.op == 'advance'
                           else {'ending': thread.ending,
                                 'resolution': thread.resolution})
                ops.append(ThreadOpIR(
                    ref=thread.ref, op=thread.op, kind=state[0], subject=state[1],
                    text=state[2], **payload))
                if thread.op == 'close':
                    state[4] = True
        return tuple(ops), snapshot


def fold_thread_faces(
    graph: BodyDesign,
) -> dict[str, tuple[tuple[ThreadOpIR, ...], tuple[OpenThreadIR, ...]]]:
    """全书线程折叠（ThreadFold 的驱动面）：按 `guard.scene_order` 的阅读序
    逐场 `apply`，返回 scene_id →（本场操作, 入场快照）。

    阅读序与 `expected_thread_faces` 的迭代序同一来源（scene_order），生产方与
    对账基准不会在「哪一场先来」上漂移。纯函数。
    """
    from novel.stages.stage4.guard import scene_order
    order = scene_order(graph)
    by_id = {scene.scene_id: scene for scene in graph.scenes}
    fold = ThreadFold()
    return {scene_id: fold.apply(by_id[scene_id])
            for scene_id in sorted(order, key=order.get)}
