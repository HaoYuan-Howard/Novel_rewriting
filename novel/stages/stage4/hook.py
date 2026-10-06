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

from copy import deepcopy
from typing import Any, Iterable, Mapping, Sequence

from pydantic import ValidationError

from contracts.schema.design import BodyDesign, PresentationGraph
from contracts.schema.hook import HookDesign
from contracts.schema.source import NameProjection, NameStructure
from contracts.schema.workflow import STAGE4_HOOK_DESIGN_PROTOCOL
from novel.stages.stage4.design_paths import design_issue
from novel.stages.stage4.hook_validation import validate_hook_payload

_WIRE_KEYS = frozenset({
    'protocol_version', 'pov', 'opening_facts', 'materials', 'concealed',
    'surface_releases', 'forbidden_reveals', 'design_brief', 'delivery_brief'})

Init = 'init'


def _resolve_ref(raw: Any, where: str, errors: list[str],
                 path: tuple = ()) -> str | None:
    """wire 位置引用 → canonical 复合引用（B3 前半：模型不写复合引用，I-21）。

    `'init'` 原样保留；否则必须是 {"scene": scene_id, "beat": beat_id} 分量
    （闭集内单 ID），compiler 组成 `场景#拍`。形状非法的问题带该条目的 wire
    路径（CS-F1：条目返修只发该条，不退整份）。"""
    if raw == Init:
        return Init
    if isinstance(raw, dict) and set(raw) == {'scene', 'beat'}:
        scene, beat = raw['scene'], raw['beat']
        if isinstance(scene, str) and scene and isinstance(beat, str) and beat:
            return f'{scene}#{beat}'
    errors.append(design_issue(
        f'{where} 的位置引用形状非法：{raw!r}'
        f'（应为 "init" 或 {{"scene": 场景id, "beat": 拍id}}）',
        code='hook_ref_shape', location=(*path, 'ref'),
        paths=(path,) if path else ()))
    return None


def assemble_hook_design(
    wire: dict[str, Any], *, body: BodyDesign, known_char_ids: set[str],
    name_projection: NameProjection,
    name_structure: NameStructure | None = None,
) -> tuple[HookDesign | None, list[str]]:
    """Hook Design wire（stage4-hook-design-v1）→ HookDesign
    （canonical 候选，待 publish_graph 发布）。

    B3 前半：wire 顶层 `protocol_version` 逐字对账；位置引用只给
    {"scene","beat"} 分量（或 "init"），复合引用由本 compiler 组成——
    wire 先解析成 canonical 形状再走既有校验与 schema 构造。"""
    errors: list[str] = []
    if wire.get('protocol_version') != STAGE4_HOOK_DESIGN_PROTOCOL:
        errors.append(design_issue(
            f'protocol_version 必须是 {STAGE4_HOOK_DESIGN_PROTOCOL}，'
            f'实得 {wire.get("protocol_version")!r}（I-13 无隐式迁移）',
            code='hook_protocol_literal', location=('protocol_version',)))
    extra = set(wire) - _WIRE_KEYS
    if extra:
        errors.append(design_issue(
            f'Hook Design wire 携带未知字段 {sorted(extra)}'
            '（源 Hook 证据、义务字段与自由 state0 文本不收）',
            code='hook_wire_key_whitelist', location=('wire_keys', tuple(sorted(extra)))))
    if errors:
        return None, errors
    data = deepcopy(wire)
    data.pop('protocol_version', None)  # wire 协议版本不进 canonical
    for k, item in enumerate(data.get('materials') or []):
        ref = _resolve_ref(item.get('beat_ref'), f'materials[{k}]', errors,
                           path=('materials', k))
        if ref is not None:
            item['beat_ref'] = ref
    for k, item in enumerate(data.get('opening_facts') or []):
        ref = _resolve_ref(item.get('ref'), f'opening_facts[{k}]', errors,
                           path=('opening_facts', k))
        if ref is not None:
            item['ref'] = ref
    for k, item in enumerate(data.get('forbidden_reveals') or []):
        ref = _resolve_ref(item.get('ref'), f'forbidden_reveals[{k}]', errors,
                           path=('forbidden_reveals', k))
        if ref is not None:
            item['ref'] = ref
    if errors:
        return None, errors
    errors = validate_hook_payload(
        data, body=body, known_char_ids=known_char_ids,
        name_projection=name_projection, name_structure=name_structure)
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
    payload = body.model_dump()
    payload['hook'] = hook.model_dump()
    return PresentationGraph.model_validate(payload)


# ── 窄返修的依赖切片与合法取值（CS-H H2，RC-57；与 Design 的 slim 切片同型） ──

def _hook_ref_parts(raw: Any) -> tuple[str, str] | None:
    """wire 位置引用分量（{"scene","beat"}）→ (scene_id, beat_id)；否则 None。"""
    if isinstance(raw, dict) and set(raw) == {'scene', 'beat'} \
            and isinstance(raw['scene'], str) and isinstance(raw['beat'], str):
        return raw['scene'], raw['beat']
    return None


def hook_patch_slice(body: BodyDesign, wire: dict, targets: Sequence[tuple]) -> dict:
    """Hook 窄返修的依赖切片：只带失败条目引用的拍与释放事实。

    · **被引用的拍**：目标材料／起笔事实／禁揭条目引用到的拍（event、参与人、
      目标增量与释放），模型据此判断 withheld 与 presentation；
    · **到该拍为止的释放前缀**：initial_surfaces 与阅读序上不晚于该拍的释放；
    · **secret 侧约束**：已发布正文里隐藏人物的呈现约束由 HookContext 编译时
      校验，切片不预判语义，只给事实。
    """
    index = {(scene.scene_id, beat.beat_id): (scene, beat)
             for scene in body.scenes for beat in scene.beats}
    order = [(scene.scene_id, beat.beat_id)
             for scene in body.scenes for beat in scene.beats]
    wanted: list[tuple[str, str]] = []
    for path in targets:
        entry = wire.get(path[0]) if path and isinstance(path[0], str) else None
        rows = entry if isinstance(entry, list) else ()
        for row in rows:
            if not isinstance(row, dict):
                continue
            for key in ('beat_ref', 'ref'):
                parts = _hook_ref_parts(row.get(key))
                if parts is not None and parts in index:
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
    return {'slice_version': 'stage4-hook-patch-slice-v1', 'beats': beats}


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
        'char_ids': sorted(known_char_ids),
        'surfaces': {char_id: sorted(faces)
                     for char_id, faces in sorted((registered_surfaces or {}).items())},
    }
