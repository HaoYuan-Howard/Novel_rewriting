#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook_context —— HookContext 的确定性投影（F6）。

【compiler 只解析引用、投影与格式化】
不解释 design_brief／delivery_brief，不补猜缺失选择：POV、按 audience 分组的
起笔事实、按设计序（呈现序）排列的材料投影、禁止揭示、可用称谓与 withheld
专名字面全部从（完整 graph, 名称投影, 长度边界）确定性解析。同一 graph +
同一投影 + 同一边界 ⇒ 同一 Context 字节（免费重建一致）。

【withheld_literals】
未在 Hook 中释放的专名字面：名称结构 ∩（全部人物的专名字面与带名 surface）
− Hook 已释放的称谓。不写死任何人名；hook validator 据此查成文泄漏。
"""
from __future__ import annotations

from contracts.schema.context import SurfaceRelease
from contracts.schema.design import PresentationGraph
from contracts.schema.hook import (
    HookConstraints, HookContext, HookMaterialView, HookOpeningFact,
)
from contracts.schema.source import NameProjection


def _display(projection: NameProjection, char_id: str) -> str:
    entry = projection.person(char_id)
    return entry.display if entry and entry.display else char_id


def _render_subjects(projection: NameProjection, subjects: tuple[str, ...]) -> str:
    return '与'.join(_display(projection, s) for s in subjects)


def _render_fact(projection: NameProjection, key: tuple, value) -> str:
    """地址元组 → 人读事实文本：主体（display）＋ key=value。"""
    _, subjects, _holder, name = key
    head = f'{_render_subjects(projection, subjects)}：' if subjects else ''
    return f'{head}{name}={value}'


def compile_hook_context(
    graph: PresentationGraph, projection: NameProjection, constraints: HookConstraints,
) -> tuple[HookContext | None, list[str]]:
    """（完整 graph, 名称投影, 长度边界）→ HookContext。纯函数，不落盘。"""
    hook = graph.hook
    errors: list[str] = []

    # 起笔事实：解析 ref 处该地址的建立值。
    facts: list[HookOpeningFact] = []
    for item in hook.opening_facts:
        key = item.address.address()
        if item.ref == 'init':
            established = [d for d in graph.initial_state
                           if d.address() == key and d.op == 'set']
        else:
            beat = graph.beat(*item.ref.split('#', 1))
            established = [d for d in (beat.target_delta if beat else ())
                           if d.address() == key and d.op == 'set']
        if not established:
            errors.append(f'起笔事实在 {item.ref} 未建立：{key}')
            continue
        facts.append(HookOpeningFact(
            audience=item.audience, established_by=item.ref,
            text=_render_fact(projection, key, established[0].value)))

    # 材料投影：presentation 原样；否则 event + 未 withheld 的 set delta 渲染。
    materials: list[HookMaterialView] = []
    for item in hook.materials:
        if item.presentation is not None:
            materials.append(HookMaterialView(
                beat_ref=item.beat_ref, presentation=item.presentation))
            continue
        beat = graph.beat(*item.beat_ref.split('#', 1))
        if beat is None:
            errors.append(f'材料引用不存在的拍：{item.beat_ref}')
            continue
        withheld = {d.address() for d in item.withheld}
        rendered = tuple(
            _render_fact(projection, d.address(), d.value)
            for d in beat.target_delta
            if d.address() not in withheld and d.op == 'set')
        materials.append(HookMaterialView(
            beat_ref=item.beat_ref, event=beat.event, deltas=rendered))

    # 禁止揭示渲染：address 给定则渲染该事实，否则渲染该拍事件内核。
    forbidden: list[str] = []
    for item in hook.forbidden_reveals:
        if item.address is not None:
            key = item.address.address()
            if item.ref == 'init':
                established = [d for d in graph.initial_state
                               if d.address() == key and d.op == 'set']
            else:
                beat = graph.beat(*item.ref.split('#', 1))
                established = [d for d in (beat.target_delta if beat else ())
                               if d.address() == key and d.op == 'set']
            if established:
                forbidden.append(_render_fact(projection, key, established[0].value))
            else:
                errors.append(f'禁止揭示的 address 在 {item.ref} 未建立：{key}')
        else:
            beat = graph.beat(*item.ref.split('#', 1)) if item.ref != 'init' else None
            if beat is None:
                errors.append(f'禁止揭示引用不存在的拍：{item.ref}')
            else:
                forbidden.append(f'{item.ref}：{beat.event}')

    # 可用称谓与 withheld 专名字面。
    usable = tuple(sorted(
        (SurfaceRelease(char_id=r.char_id, surfaces=tuple(sorted(set(r.surfaces))))
         for r in hook.surface_releases), key=lambda r: r.char_id))
    released = {s for r in hook.surface_releases for s in r.surfaces}
    withheld: set[str] = set()
    for entry in projection.by_person:
        withheld.update(entry.proper_literals)
        withheld.update(entry.name_bearing_surfaces)
    withheld -= released
    withheld.discard('')

    if errors:
        return None, errors
    return HookContext(
        pov=hook.pov, opening_facts=tuple(facts), materials=tuple(materials),
        forbidden_reveals=tuple(forbidden), design_brief=hook.design_brief,
        delivery_brief=hook.delivery_brief, target_chars=constraints.target_chars,
        hard_max_chars=constraints.hard_max_chars, usable_surfaces=usable,
        withheld_literals=tuple(sorted(withheld))), []
