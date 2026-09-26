"""单一目标 Fold：按显式地址/操作重放，仅 accepted prefix 的 planned_delta 可传播。

叠加机制用 `novel.stages.common.StateOverlay`（D16：Stage2 源折叠与 Stage4 计划
折叠共用的唯一实现）；本类只负责把目标侧 `Establishment` 适配成规范变化，并投影
成消费面视图（StateEntry／KnowledgeEntry）。
"""
from __future__ import annotations

from typing import Iterable

from contracts.schema.execution import EffectDomain, Establishment, SceneExecutionPlan
from contracts.schema.context import KnowledgeEntry, StateEntry
from novel.stages.common import OverlayChange, StateOverlay

INIT_ANCHOR = 'init'


class FoldedState:
    """可重建派生状态；scene 覆盖保留 book 底值，解除/未知/未建立分别表达。"""

    def __init__(self) -> None:
        self._overlay = StateOverlay()

    def apply(self, scene_id: str, establishments: Iterable[Establishment]) -> 'FoldedState':
        self._overlay.apply(scene_id, [
            OverlayChange(address=e.address(), op=e.op, value=e.value,
                          scope=e.scope, source=e.established_by,
                          mode=e.knowledge_mode)
            for e in establishments])
        return self

    def expire_scene_scope(self, scene_id: str) -> 'FoldedState':
        self._overlay.expire_scene_scope(scene_id)
        return self

    def _entry_of(self, change: OverlayChange) -> Establishment:
        """规范变化还原成 Establishment（地址四段从 address 元组拆回）。"""
        domain, subjects, holder, key = change.address
        return Establishment(
            domain=EffectDomain(domain), key=key, subjects=tuple(subjects),
            holder=holder or None, op=change.op, value=change.value,
            scope=change.scope, established_by=change.source,
            knowledge_mode=change.mode)

    def entry_state_view(self, scene_start_anchor: str, domains=(
            EffectDomain.CHARACTER, EffectDomain.RELATION,
            EffectDomain.WORLD, EffectDomain.READER)) -> tuple[StateEntry, ...]:
        return tuple(StateEntry(
            domain=e.domain, key=e.key, subjects=e.subjects, value=e.value,
            status=e.op, established_by=e.established_by)
            for e in (self._entry_of(c) for c in self._overlay.entries())
            if e.domain in domains)

    def entry_knowledge_view(self) -> tuple[KnowledgeEntry, ...]:
        # 非 set（解除/未知）的 knowledge 项无知情模式可消费；mode 只在 set 上有
        # 语义，这里按 inert 'known' 兜底，防 None 炸 KnowledgeEntry 构造。
        return tuple(KnowledgeEntry(
            holder=e.holder, subjects=e.subjects, key=e.key,
            mode=e.knowledge_mode or 'known',
            proposition=f'{e.key}={e.value}', status=e.op, established_by=e.established_by)
            for e in (self._entry_of(c) for c in self._overlay.entries())
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
