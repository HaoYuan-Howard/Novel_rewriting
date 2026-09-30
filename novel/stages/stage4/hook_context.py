#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook_context —— HookContext 的确定性投影（F6；R2 引擎批补线程禁区）。

【compiler 只解析引用、投影与格式化】
不解释 design_brief／delivery_brief，不补猜缺失选择：POV、按 audience 分组的
起笔事实、按设计序（呈现序）排列的材料投影、禁止揭示、可用称谓与 withheld
专名字面全部从（完整 graph, 名称投影, 长度边界）确定性解析。同一 graph +
同一投影 + 同一边界 ⇒ 同一 Context 字节（免费重建一致）。

【事实渲染保留认知维度】起笔事实与材料 delta 渲染时写明 domain、holder 与
knowledge_mode：「谁知道、怀疑还是听说」与「读者视角成立」是 Hook 写手判断
呈现边界的依据，压成「主体：key=value」会把怀疑洗成知道（F7e 评估实证）。
renderer 仍只做格式化——值、模式与地址原样来自 delta，不重写语义。

【线程收束并入禁区（R2.7，E-3 读法 b）】楔子不得提前兑现线索的收束：
compiler 把「材料视界内开启、视界外收束」的线程 close（结局＋收束文本）与
「视界内开启、全书未收的问题线」机械并入 `forbidden_reveals`（不动 Hook
Design wire；主体须与楔子人物相关——全场景保护不等于全列）。视界＝材料
引用拍的最大（场序, 拍序）。

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
from contracts.schema.source import NameProjection, NameStructure
from novel.core.name_materials import withheld_name_literals, expand_surface_releases


def _display(projection: NameProjection, char_id: str) -> str:
    entry = projection.person(char_id)
    return entry.display if entry and entry.display else ''


def _render_subjects(projection: NameProjection, subjects: tuple[str, ...]) -> str:
    return '与'.join(_display(projection, s) for s in subjects)


#: knowledge 认知模式 → 人读措辞（区分「知道、怀疑、听说、以为为真」）。
_KNOWLEDGE_MODES = {'known': '确知', 'suspected': '怀疑', 'heard': '听说',
                    'believes': '以为为真（不代表世界为真）'}

#: 效果域 → 人读措辞（「读者揭示」与人物知情分开）。
_DOMAIN_LABELS = {'character': '人物', 'relation': '关系', 'knowledge': '知情',
                  'world': '世界', 'reader': '读者'}


def _render_fact(projection: NameProjection, key: tuple, delta) -> str:
    """地址元组 + 建立该项的 delta → 人读事实文本。

    认知维度必须保留：domain（这条事实归谁）、holder（谁知道）与 knowledge_mode
    （知道／怀疑／听说）是 Hook 写手判断「能否呈现、呈现到什么程度」的唯一依据；
    只渲染「主体：key=value」会把「夫君怀疑」压成「夫君知道」（F7e 评估实证：
    渲染丢掉 holder 与 knowledge_mode 后，presentation 与引用拍脱钩）。
    """
    domain, subjects, holder, name = key
    head = f'{_render_subjects(projection, subjects)}的' if subjects else ''
    if domain == 'knowledge':
        mode = _KNOWLEDGE_MODES.get(delta.knowledge_mode, delta.knowledge_mode or '未知模式')
        who = _display(projection, holder) if holder else '未登记持有人'
        return f'{who}{mode}：{delta.value}'
    if domain == 'reader':
        return f'读者视角成立：{delta.value}（人物未必知晓）'
    return f'{head}{_DOMAIN_LABELS.get(domain, domain)}层面：{delta.value}'


#: 线程 kind → 人读措辞（禁区条目用；slug 不进阅读面）。
_THREAD_KINDS = {'goal': '在途目标', 'action': '进行中行动',
                 'question': '未决问题', 'promise': '待兑现承诺'}


def _protected_thread_entries(graph: PresentationGraph, horizon, cast: set[str],
                              ) -> list[str]:
    """未到时点的线程收束 → 禁区条目（R2.7，E-3 读法 b：机械并入，不动 wire）。

    视界＝材料引用拍的最大（场序, 拍序）。规则（纯机械，按阅读序扫描）：

    · 线程在视界内开启（open 位置 ≤ 视界）且主体与楔子人物相关：
      - close 位置 > 视界 → 收束（resolution）不得提前兑现或暗示；
      - 全书未收且 kind=question → 未决问题不得给出答案或暗示。
    · 视界后才开启的线不进禁区（楔子根本不该引入它；材料面另有纪律）；
      视界内已收束的线不进禁区（材料可见范围内已成事实）。
    """
    opened: dict[str, list] = {}   # ref -> [kind, subject, text, open_pos, close_pos, resolution]
    for si, scene in enumerate(graph.scenes):
        for bi, beat in enumerate(scene.beats):
            for thread in beat.threads:
                position = (si, bi)
                if thread.op == 'open':
                    opened[thread.ref] = [thread.kind, thread.subject, thread.text,
                                          position, None, None]
                elif thread.ref in opened and thread.op == 'close':
                    opened[thread.ref][4] = position
                    opened[thread.ref][5] = thread.resolution
    entries: list[str] = []
    for _ref, (kind, subject, text, open_pos, close_pos, resolution) in \
            sorted(opened.items()):
        if open_pos > horizon or subject not in cast:
            continue
        if close_pos is None:
            if kind == 'question':
                entries.append(f'未决问题「{text}」不得在楔子中给出答案或暗示，'
                               '也不主动向读者提起')
        elif close_pos > horizon:
            entries.append(f'{_THREAD_KINDS[kind]}「{text}」的收束（{resolution}）'
                           '不得在楔子中提前兑现或暗示')
    return entries


def compile_hook_context(
    graph: PresentationGraph, projection: NameProjection, constraints: HookConstraints,
    structure: NameStructure | None = None,
) -> tuple[HookContext | None, list[str]]:
    """（完整 graph, 名称投影, 长度边界）→ HookContext。纯函数，不落盘。"""
    hook = graph.hook
    errors: list[str] = []
    display_names = expand_surface_releases({r.char_id: r.surfaces for r in hook.surface_releases}, structure)
    display_projection = projection.model_copy(update={'by_person': tuple(
        entry.model_copy(update={'display': (display_names.get(entry.char_id) or ('',))[0]})
        for entry in projection.by_person)})

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
            text=_render_fact(display_projection, key, established[0])))

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
            _render_fact(display_projection, d.address(), d)
            for d in beat.target_delta
            if d.address() not in withheld and d.op == 'set')
        materials.append(HookMaterialView(
            beat_ref=item.beat_ref, event=beat.event, deltas=rendered))

    # 禁止揭示渲染：address 给定则渲染该事实，否则渲染该拍事件内核；
    # 随后把未到时点的线程收束机械并入禁区（E-3 读法 b，见模块头）。
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
                forbidden.append(_render_fact(projection, key, established[0]))
            else:
                errors.append(f'禁止揭示的 address 在 {item.ref} 未建立：{key}')
        else:
            beat = graph.beat(*item.ref.split('#', 1)) if item.ref != 'init' else None
            if beat is None:
                errors.append(f'禁止揭示引用不存在的拍：{item.ref}')
            else:
                forbidden.append(f'事件不得提前揭示：{beat.event}')

    # 可用称谓与 withheld 专名字面。
    usable = tuple(sorted(
        (SurfaceRelease(char_id=r.char_id, surfaces=display_names[r.char_id])
         for r in hook.surface_releases), key=lambda r: r.char_id))
    released = {s for r in usable for s in r.surfaces}
    withheld = withheld_name_literals(projection, released, structure)

    # 线程收束并入禁区（R2.7）：视界＝材料引用拍的最大（场序, 拍序）；
    # 楔子人物＝focalizers∪材料拍参与者∪起笔事实主语/持有者∪释放与隐藏人物。
    scene_index = {scene.scene_id: si for si, scene in enumerate(graph.scenes)}
    beat_index = {(scene.scene_id, beat.beat_id): bi
                  for si, scene in enumerate(graph.scenes)
                  for bi, beat in enumerate(scene.beats)}
    horizon = max((scene_index.get(item.beat_ref.split('#', 1)[0], -1),
                   beat_index.get(tuple(item.beat_ref.split('#', 1)), -1))
                  for item in hook.materials) if hook.materials else (-1, -1)
    cast = set(hook.pov.focalizers) | set(hook.concealed)
    cast |= {r.char_id for r in hook.surface_releases}
    for item in hook.materials:
        beat = graph.beat(*item.beat_ref.split('#', 1))
        if beat is not None:
            cast |= set(beat.participants)
    for fact in hook.opening_facts:
        cast |= set(fact.address.subjects)
        if fact.address.holder:
            cast.add(fact.address.holder)
    forbidden.extend(_protected_thread_entries(graph, horizon, cast))

    if errors:
        return None, errors
    return HookContext(
        pov=hook.pov, opening_facts=tuple(facts), materials=tuple(materials),
        forbidden_reveals=tuple(forbidden), design_brief=hook.design_brief,
        delivery_brief=hook.delivery_brief, target_chars=constraints.target_chars,
        hard_max_chars=constraints.hard_max_chars, usable_surfaces=usable,
        withheld_literals=tuple(sorted(withheld))), []
