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
from typing import Any

from pydantic import ValidationError

from contracts.schema.design import BodyDesign, PresentationGraph
from contracts.schema.hook import HookDesign
from contracts.schema.source import NameProjection, NameStructure
from contracts.schema.workflow import STAGE4_HOOK_DESIGN_PROTOCOL
from novel.stages.stage4.hook_validation import validate_hook_payload

_WIRE_KEYS = frozenset({
    'protocol_version', 'pov', 'opening_facts', 'materials', 'concealed',
    'surface_releases', 'forbidden_reveals', 'design_brief', 'delivery_brief'})

Init = 'init'


def _resolve_ref(raw: Any, where: str, errors: list[str]) -> str | None:
    """wire 位置引用 → canonical 复合引用（B3 前半：模型不写复合引用，I-21）。

    `'init'` 原样保留；否则必须是 {"scene": scene_id, "beat": beat_id} 分量
    （闭集内单 ID），compiler 组成 `场景#拍`。"""
    if raw == Init:
        return Init
    if isinstance(raw, dict) and set(raw) == {'scene', 'beat'}:
        scene, beat = raw['scene'], raw['beat']
        if isinstance(scene, str) and scene and isinstance(beat, str) and beat:
            return f'{scene}#{beat}'
    errors.append(f'{where} 的位置引用形状非法：{raw!r}'
                  f'（应为 "init" 或 {{"scene": 场景id, "beat": 拍id}}）')
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
        errors.append(f'protocol_version 必须是 {STAGE4_HOOK_DESIGN_PROTOCOL}，'
                      f'实得 {wire.get("protocol_version")!r}（I-13 无隐式迁移）')
    extra = set(wire) - _WIRE_KEYS
    if extra:
        errors.append(f'Hook Design wire 携带未知字段 {sorted(extra)}'
                      '（源 Hook 证据、义务字段与自由 state0 文本不收）')
    if errors:
        return None, errors
    data = deepcopy(wire)
    data.pop('protocol_version', None)  # wire 协议版本不进 canonical
    for item in data.get('materials') or []:
        ref = _resolve_ref(item.get('beat_ref'), 'materials', errors)
        if ref is not None:
            item['beat_ref'] = ref
    for item in data.get('opening_facts') or []:
        ref = _resolve_ref(item.get('ref'), 'opening_facts', errors)
        if ref is not None:
            item['ref'] = ref
    for item in data.get('forbidden_reveals') or []:
        ref = _resolve_ref(item.get('ref'), 'forbidden_reveals', errors)
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
        return None, [f'hook_design 构造失败：{exc}']


def publish_graph(body: BodyDesign, hook: HookDesign) -> PresentationGraph:
    """完整 PresentationGraph 的唯一 publisher。"""
    payload = body.model_dump()
    payload['hook'] = hook.model_dump()
    return PresentationGraph.model_validate(payload)
