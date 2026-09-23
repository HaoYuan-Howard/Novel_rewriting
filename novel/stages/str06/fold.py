#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fold —— 确定性状态折叠引擎（F2 免费前置；F4 Compile 的地基）。

【语义】`State_t = 此刻仍成立`。establishment 按场景序应用；`scene` 作用域的
establishment 在离开所属场景时失效（试点「场景局部状态无衰减」缺陷的对策）；
`book` 作用域持续到被同键新值替换。前史／倒叙不按叙述顺序误改当前状态——
引擎只消费执行位置的先后，不解释自然语言时间。

【谁用它】Compile／Context／Continuity 的 Init/Apply、Guard 的 M2/M3 查询、
Judge 的 continuity_fact_view 输入，全部查同一个引擎——**无第二套 Fold**
（试点 B0 的裁决：Guard/Judge 查询同一引擎，分家就会漂）。

【RC 边界】输入输出都是 `contracts.schema.str06.execution` 的 typed 对象；
`established_by` 锚 = `<scene_id>#<beat_id>`（与 BindingEntry 一致）或
`init`（书级初始）。
"""
from __future__ import annotations

from typing import Iterable, NamedTuple

from contracts.schema.str06.execution import (
    EffectDomain, Establishment, SceneExecutionPlan,
)
from contracts.schema.str06.context import KnowledgeEntry, StateEntry

INIT_ANCHOR = 'init'


class _Fact(NamedTuple):
    value: str
    established_by: str
    scope: str          # 'book' | 'scene'
    owner_scene: str    # scene 作用域的归属场景；book 级为建立时的场景


class FoldedState:
    """按序应用 establishment 后的全书状态（可变工作对象，属 runtime 派生）。

    不是 canonical truth：删除 runtime 后由（plan + 已接受序）确定性重建。
    """

    def __init__(self) -> None:
        self._character: dict[str, _Fact] = {}
        self._relation: dict[str, _Fact] = {}
        self._world: dict[str, _Fact] = {}
        self._reader: dict[str, _Fact] = {}
        self._knowledge: dict[str, dict[str, _Fact]] = {}  # holder -> key -> fact

    # ── 应用 ──

    def apply(self, scene_id: str, establishments: Iterable[Establishment]) -> 'FoldedState':
        for est in establishments:
            self._apply_one(scene_id, est)
        return self

    def _apply_one(self, scene_id: str, est: Establishment) -> None:
        anchor = f'{scene_id}#{_beat_of(est) or "scene"}'
        fact = _Fact(value=est.value, established_by=anchor,
                     scope=est.scope, owner_scene=scene_id)
        if est.domain is EffectDomain.KNOWLEDGE:
            holder, _, key = est.key.partition('::')
            self._knowledge.setdefault(holder or '?', {})[key or est.key] = fact
        else:
            self._bucket(est.domain)[est.key] = fact

    def expire_scene_scope(self, scene_id: str) -> 'FoldedState':
        """场景边界：本场景 scene 作用域的事实全部失效。"""
        for bucket in (self._character, self._relation, self._world, self._reader):
            for key in [k for k, f in bucket.items()
                        if f.scope == 'scene' and f.owner_scene == scene_id]:
                del bucket[key]
        for holder_map in self._knowledge.values():
            for key in [k for k, f in holder_map.items()
                        if f.scope == 'scene' and f.owner_scene == scene_id]:
                del holder_map[key]
        return self

    # ── 查询 ──

    def _bucket(self, domain: EffectDomain) -> dict[str, _Fact]:
        return {
            EffectDomain.CHARACTER: self._character,
            EffectDomain.RELATION: self._relation,
            EffectDomain.WORLD: self._world,
            EffectDomain.READER: self._reader,
        }[domain]

    def entry_state_view(
        self, scene_start_anchor: str,
        domains: Iterable[EffectDomain] = (
            EffectDomain.CHARACTER, EffectDomain.RELATION,
            EffectDomain.WORLD, EffectDomain.READER),
    ) -> tuple[StateEntry, ...]:
        """本场起点的 state 视图：established_by 晚于本场起点的事实不该出现。

        本方法**不做**时序判断（那是 Guard M3 的职责，判定与视图分离）；
        `scene_start_anchor` 仅用于调用方对齐坐标。
        """
        entries: list[StateEntry] = []
        for domain in domains:
            for key, fact in sorted(self._bucket(domain).items()):
                entries.append(StateEntry(
                    domain=domain.value, key=key, value=fact.value,
                    established_by=fact.established_by))
        return tuple(entries)

    def entry_knowledge_view(self) -> tuple[KnowledgeEntry, ...]:
        entries = [
            KnowledgeEntry(holder=holder, proposition=f'{key}={fact.value}',
                           established_by=fact.established_by)
            for holder, mapping in sorted(self._knowledge.items())
            for key, fact in sorted(mapping.items())
        ]
        return tuple(entries)

    def value_of(self, domain: EffectDomain, key: str) -> str | None:
        fact = self._bucket(domain).get(key)
        return fact.value if fact else None


def _beat_of(est: Establishment) -> str:
    # establishment 的锚在 RC 期取场景级；F4 编译时若有拍级来源，扩展为
    # Establishment 上的显式 anchor 字段（升版），不在此猜。
    return ''


def fold_accepted_prefix(
    plans: Iterable[SceneExecutionPlan],
    *,
    initial: Iterable[Establishment] = (),
) -> FoldedState:
    """从连续有效 accepted prefix 重建状态：T₀=Init(initial)，逐场 Apply。

    顺序由迭代序给出（调用方保证=目标场景序）。每场结束时 scene 作用域过期。
    恢复语义：删除 runtime 后由（plan + 已接受序）重放本函数即得相同状态。
    """
    state = FoldedState().apply('__init__', initial)
    for plan in plans:
        state.apply(plan.scene_id, plan.establishments)
        state.expire_scene_scope(plan.scene_id)
    return state
