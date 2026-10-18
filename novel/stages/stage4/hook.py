#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook_design —— Hook Design 的确定性校验臂与唯一 publisher（F6）。

【分工】语义判断（选哪些材料、隐藏什么、如何组织）是 Hook Design 一次 LLM
调用的职责；本模块是确定性臂：引用闭合、withheld ⊆ 该拍 delta、时序与披露
自洽、人称组合、称谓释放面、隐藏人物的 presentation 纪律。全部错误聚合返回，
wire 原样进、不修改（I-24）。

【withheld 指向不存在地址必须报错（F5e 重申）】
与 design_compile 的确定性归一不同，这里不做静默剔除：withheld 指向该拍
delta 之外的地址，可能意味着设计者对「事实在哪一拍建立」的真实误解（想把
别处建立的事实藏在本材料）——静默丢弃会掩盖披露意图错误。报错后走 owner
patch（提取已购 wire、最小修订、经 --wire-dir 采用留证），不走 compiler 猜测。

【唯一 publisher】
`publish_graph(body, hook)` 是完整 PresentationGraph 的唯一发布点——不存在
「正文设计完成但 Hook 未定」的中间 canonical，也不用空 Hook 默认值冒充完成。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from pydantic import ValidationError

from contracts.schema.design import BodyDesign, PresentationGraph
from contracts.schema.hook import HookDesign
from contracts.schema.source import NameProjection, NameStructure
from contracts.schema.workflow import STAGE4_HOOK_DESIGN_PROTOCOL
from novel.stages.stage4.design_paths import design_issue
from novel.stages.stage4.hook_validation import validate_hook_payload
from novel.stages.stage4.hook_wire import (
    annotated_hook_catalog, expand_hook_wire, hook_catalog, hook_name_exposure,
    hook_wire_schema, opening_chapter_input,
)


def assemble_hook_design(
    wire: dict[str, Any], *, body: BodyDesign, known_char_ids: set[str],
    name_projection: NameProjection,
    name_structure: NameStructure | None = None,
    faces: Mapping[str, Mapping[str, str]] | None = None,
) -> tuple[HookDesign | None, list[str]]:
    """Hook Design wire（stage4-hook-design-v4）→ HookDesign
    （canonical 候选，待 publish_graph 发布）。

    事实只选目录 fact_ref，材料只给位置分量；compiler 展开 canonical 引用，
    validator 独立验证一次建立与披露边界。协议逐字对账，不隐式收 v1。"""
    errors: list[str] = []
    if wire.get('protocol_version') != STAGE4_HOOK_DESIGN_PROTOCOL:
        errors.append(design_issue(
            f'protocol_version 必须是 {STAGE4_HOOK_DESIGN_PROTOCOL}，'
            f'实得 {wire.get("protocol_version")!r}（I-13 无隐式迁移）',
            code='hook_protocol_literal', location=('protocol_version',)))
    if errors:
        return None, errors
    data, errors = expand_hook_wire(wire, body)
    if errors:
        return None, errors
    assert data is not None
    errors = validate_hook_payload(
        data, body=body, known_char_ids=known_char_ids,
        name_projection=name_projection, name_structure=name_structure, faces=faces)
    if errors:
        return None, errors
    try:
        return HookDesign.model_validate(data), []
    except ValidationError as exc:
        return None, [design_issue(
            f'hook_design 构造失败：{exc}',
            code='hook_construct', location=('hook_design',))]


def publish_graph(body: BodyDesign, hook: HookDesign) -> PresentationGraph:
    """完整 PresentationGraph 的唯一 publisher。"""
    from contracts.artifacts import STR06_ARTIFACT_RC_VERSIONS
    payload = body.model_dump()
    payload['contract_version'] = STR06_ARTIFACT_RC_VERSIONS[
        'stage4/design/presentation_graph.json']
    payload['hook'] = hook.model_dump()
    return PresentationGraph.model_validate(payload)


# ── 窄返修的依赖切片与合法取值（CS-H H2，RC-57；与 Design 的 slim 切片同型） ──

def _hook_ref_parts(raw: Any) -> tuple[str, str] | None:
    """wire 位置引用分量（{"scene","beat"}）→ (scene_id, beat_id)；否则 None。"""
    if isinstance(raw, dict) and set(raw) == {'scene', 'beat'} \
            and isinstance(raw['scene'], str) and isinstance(raw['beat'], str):
        return raw['scene'], raw['beat']
    return None


def _hook_followup_directory(index: dict, order: list[tuple[str, str]], materials: Any) -> dict:
    """期待返修的只读故事目录：按已知视界筛选，不根据问题猜承载拍。"""
    material_refs = [_hook_ref_parts(item.get('beat_ref')) if isinstance(item, dict) else None
                     for item in materials] if isinstance(materials, (list, tuple)) else []
    timing_known = bool(material_refs) and all(ref in index for ref in material_refs)
    horizon = max(order.index(ref) for ref in material_refs) if timing_known else None
    candidates = order[horizon + 1:] if horizon is not None else order
    horizon_ref = order[horizon] if horizon is not None else None
    return {
        'scope': 'after_material_horizon' if timing_known else 'all_beats_horizon_unresolved',
        'material_horizon': {'scene': horizon_ref[0], 'beat': horizon_ref[1]} if horizon_ref else None,
        'beats': [{
            'beat_ref': {'scene': scene_id, 'beat': beat_id},
            'event': index[(scene_id, beat_id)][1].event,
            'participants': list(index[(scene_id, beat_id)][1].participants)}
            for scene_id, beat_id in candidates]}


def hook_patch_slice(body: BodyDesign, wire: dict, targets: Sequence[tuple],
                     *, faces: Mapping[str, Iterable[str]] | None = None,
                     kp_statements: Mapping[str, str] = (),
                     world_statements: Mapping[str, str] = ()) -> dict:
    """Hook 窄返修的依赖切片：只带失败条目引用的拍与释放事实。

    · **被引用的拍**：目标材料／起笔事实／禁揭条目引用到的拍（event、参与人、
      目标增量与释放），模型据此判断 withheld 与 presentation；
    · **到该拍为止的释放前缀**：initial_surfaces 与阅读序上不晚于该拍的释放；
    · **阅读期待的候选目录**：仅在修订期待时列视界之后的真实拍及事件，包含没有
      delta 的行动／关系拍；材料位置非法则明确给全拍目录，不猜时序界。
    · **secret 侧约束**：已发布正文里隐藏人物的呈现约束由 HookContext 编译时
      校验，切片不预判语义，只给事实。
    · `projection` 在场时目录与拍附派生标注（later_writes／name_exposure），
      与整份请求同一把尺——返修轮同样按查表修复机械层，不重放状态机。
    """
    index = {(scene.scene_id, beat.beat_id): (scene, beat)
             for scene in body.scenes for beat in scene.beats}
    order = [(scene.scene_id, beat.beat_id)
             for scene in body.scenes for beat in scene.beats]
    wanted: list[tuple[str, str]] = []
    catalog = hook_catalog(body)
    for path in targets:
        entry = wire.get(path[0]) if path and isinstance(path[0], str) else None
        rows = ([entry[path[1]]] if isinstance(entry, list) and len(path) > 1
                and isinstance(path[1], int) and path[1] < len(entry)
                else entry if isinstance(entry, list) else [entry] if isinstance(entry, dict) else ())
        for row in rows:
            if not isinstance(row, dict):
                continue
            for key in ('beat_ref', 'ref'):
                parts = _hook_ref_parts(row.get(key))
                if parts is not None and parts in index:
                    wanted.append(parts)
            followups = row.get('followup_refs')
            for followup in followups if isinstance(followups, (list, tuple)) else ():
                parts = _hook_ref_parts(followup)
                if parts is not None and parts in index:
                    wanted.append(parts)
            fact_ref = row.get('fact_ref')
            fact = catalog.get(fact_ref) if isinstance(fact_ref, str) else None
            if fact and isinstance(fact['ref'], dict):
                wanted.append((fact['ref']['scene'], fact['ref']['beat']))
    # 呈现起点与视界是修正起笔事实所需的只读边界，不能只给失败条目的来源拍。
    raw_materials = wire.get('materials')
    for material in raw_materials if isinstance(raw_materials, (list, tuple)) else ():
        if not isinstance(material, dict):
            continue
        parts = _hook_ref_parts(material.get('beat_ref'))
        if parts in index:
            wanted.append(parts)
    beats: list[dict] = []
    for scene_id, beat_id in dict.fromkeys(wanted):
        scene, beat = index[(scene_id, beat_id)]
        limit = order.index((scene_id, beat_id))
        releases = [{'at': 'initial_surfaces', **s.model_dump(mode='json')}
                    for s in body.initial_surfaces]
        for other_scene in body.scenes:
            for other in other_scene.beats:
                if order.index((other_scene.scene_id, other.beat_id)) > limit:
                    continue
                releases.extend({'at': f'{other_scene.scene_id}#{other.beat_id}',
                                 **s.model_dump(mode='json')}
                                for s in other.surface_introductions)
        beats.append({
            'beat_ref': {'scene': scene_id, 'beat': beat_id},
            'event': beat.event, 'participants': list(beat.participants),
            'target_delta': [d.model_dump(mode='json') for d in beat.target_delta],
            'release_prefix': releases})
    if faces is not None:
        for row in beats:
            exposure = hook_name_exposure(row['event'], faces=faces)
            if exposure:
                row['name_exposure'] = exposure
    sliced = {'slice_version': 'stage4-hook-patch-slice-v4',
            'facts': annotated_hook_catalog(body, faces=faces,
                                            kp_statements=kp_statements,
                                            world_statements=world_statements),
            'initial_surfaces': [s.model_dump(mode='json') for s in body.initial_surfaces],
            'materials': wire.get('materials', []),
            'reader_expectation': wire.get('reader_expectation'),
            'local_focus': wire.get('local_focus'),
            'opening_chapter': opening_chapter_input(body),
            'book_style': body.book_style,
            'expression_decisions': [d.model_dump(mode='json') for d in body.expression_decisions],
            'character_voices': [v.model_dump(mode='json') for v in body.character_voices],
            'forbidden_reveals': wire.get('forbidden_reveals', []), 'pov': wire.get('pov'),
            'beats': beats}
    if any(path and path[0] == 'reader_expectation' for path in targets):
        sliced['followup_candidates'] = _hook_followup_directory(index, order, raw_materials)
    return sliced


def hook_patch_legal_values(*, body: BodyDesign, known_char_ids: Iterable[str] = (),
                            registered_surfaces: Mapping[str, set[str]] | None = None,
                            ) -> dict:
    """Hook 窄返修的合法取值：位置引用分量与称谓释放闭集（与整份请求同一把尺）。"""
    beats: dict[str, list[str]] = {}
    for scene in body.scenes:
        beats.setdefault(scene.scene_id, []).extend(
            beat.beat_id for beat in scene.beats)
    return {
        'beat_refs': beats,
        'init': 'init',
        'fact_refs': list(hook_catalog(body)),
        'wire_schema': hook_wire_schema(body),
        'char_ids': sorted(known_char_ids),
        'surfaces': {char_id: sorted(faces)
                     for char_id, faces in sorted((registered_surfaces or {}).items())},
    }
