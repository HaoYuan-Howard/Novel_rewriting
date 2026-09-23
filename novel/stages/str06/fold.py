"""单一目标 Fold：按显式地址/操作重放，仅 accepted prefix 的 planned_delta 可传播。"""
from __future__ import annotations

from typing import Iterable
from contracts.schema.str06.execution import EffectDomain, Establishment, SceneExecutionPlan
from contracts.schema.str06.context import KnowledgeEntry, StateEntry

INIT_ANCHOR = 'init'


class FoldedState:
    """可重建派生状态；scene 覆盖保留 book 底值，解除/未知/未建立分别表达。"""
    def __init__(self) -> None:
        self._book: dict[tuple, Establishment] = {}
        self._local: dict[tuple, tuple[str, Establishment]] = {}

    def apply(self, scene_id: str, establishments: Iterable[Establishment]) -> 'FoldedState':
        for est in establishments:
            if est.scope == 'book':
                self._book[est.address()] = est
            else:
                self._local[est.address()] = (scene_id, est)
        return self

    def expire_scene_scope(self, scene_id: str) -> 'FoldedState':
        self._local = {k: v for k, v in self._local.items() if v[0] != scene_id}
        return self

    def _visible(self) -> dict[tuple, Establishment]:
        return {**self._book, **{k: v[1] for k, v in self._local.items()}}

    def entry_state_view(self, scene_start_anchor: str, domains=(
            EffectDomain.CHARACTER, EffectDomain.RELATION,
            EffectDomain.WORLD, EffectDomain.READER)) -> tuple[StateEntry, ...]:
        return tuple(StateEntry(
            domain=e.domain, key=e.key, subjects=e.subjects, value=e.value,
            status=e.op, established_by=e.established_by)
            for _, e in sorted(self._visible().items()) if e.domain in domains)

    def entry_knowledge_view(self) -> tuple[KnowledgeEntry, ...]:
        return tuple(KnowledgeEntry(
            holder=e.holder, subjects=e.subjects, key=e.key, mode=e.knowledge_mode,
            proposition=f'{e.key}={e.value}', status=e.op, established_by=e.established_by)
            for _, e in sorted(self._visible().items()) if e.domain is EffectDomain.KNOWLEDGE)

    def value_of(self, domain: EffectDomain, key: str, *, subjects=(), holder=None) -> str | None:
        fact = self._visible().get((domain.value, tuple(subjects), holder or '', key))
        return fact.value if fact and fact.op == 'set' else None


def fold_accepted_prefix(plans: Iterable[SceneExecutionPlan], *,
                         initial: Iterable[Establishment] = ()) -> FoldedState:
    state = FoldedState().apply('__init__', initial)
    for plan in plans:
        state.apply(plan.scene_id, plan.planned_delta).expire_scene_scope(plan.scene_id)
    return state
