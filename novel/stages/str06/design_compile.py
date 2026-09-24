"""Body Design wire 的确定性组装（F6 改版；F5 增补确定性归一）。

RC2 时代本模块直接组装 PresentationGraph；RC3 起 Hook 与正文同一份 graph 但
分开设计：这里只组装 **BodyDesign**（无 hook 字段），完整 graph 由
`hook_design.publish_graph(body, hook)` 唯一发布——从类型上保证 Bind／Compile／
Context／Guard 消费的 body 视图不含 Hook，Hook 不影响正文。

【确定性归一（F5e 真书实证；不补猜语义）】
  ① 非 knowledge 域的 `knowledge_mode` 一律剥离——schema 只允许 knowledge 带
     模式；模型会在 reader 揭示上装饰 `'known'`，该值在其它域无语义可保留。
  ② `chapter_weights` 若是单键 dict 的列表（如 `[{"S01":0.04}, ...]`），按键
     合并为 dict；重复键或非单键形状不归一，交给 schema 报错。
两类归一都是无损形状转换：不改任何事件、状态、数值与 brief 语义。
  ③ `delivery.pov` 若不是 char_id/narrator，而是以某人物 canonical 开头的字符串
     （F5e 实证：`苏芸娘第三人称限知`），按 canonical→char_id 唯一前缀匹配解析为
     该 char_id；无匹配或多义匹配不归一，交由 char_id 校验报错。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from pydantic import ValidationError
from contracts.schema.str06.design import BodyDesign
from contracts.schema.str06.grounding import OutputConstraints


def _normalize(data: dict[str, Any]) -> None:
    for item in data.get('initial_state') or []:
        if isinstance(item, dict) and item.get('domain') != 'knowledge':
            item['knowledge_mode'] = None
    for scene in data.get('scenes') or []:
        for beat in scene.get('beats') or []:
            for item in beat.get('target_delta') or []:
                if isinstance(item, dict) and item.get('domain') != 'knowledge':
                    item['knowledge_mode'] = None
    weights = data.get('chapter_weights')
    if isinstance(weights, list):
        merged: dict[str, Any] = {}
        for entry in weights:
            if not isinstance(entry, dict) or len(entry) != 1:
                return  # 形状不在可归一面内：保持原样，由 schema 报错
            key, value = next(iter(entry.items()))
            if key in merged:
                return
            merged[key] = value
        data['chapter_weights'] = merged


def _normalize_povs(data: dict[str, Any], char_canonicals: Mapping[str, str] | None) -> None:
    """canonical 前缀唯一匹配 → char_id；匹配不到保持原样让校验报错。"""
    if not char_canonicals:
        return
    for scene in data.get('scenes') or []:
        delivery = scene.get('delivery') or {}
        pov = delivery.get('pov')
        if not isinstance(pov, str) or pov == 'narrator':
            continue
        matches = {cid for canonical, cid in char_canonicals.items()
                   if canonical and pov.startswith(canonical)}
        if len(matches) == 1:
            delivery['pov'] = next(iter(matches))


def assemble_body_design(
    wire: dict[str, Any], *, known_char_ids: set[str],
    constraints: OutputConstraints | None = None,
    char_canonicals: Mapping[str, str] | None = None,
) -> tuple[BodyDesign | None, list[str]]:
    # 仅剥离已明确给出的场景前缀与上述确定性归一，不补猜缺失语义。
    data = deepcopy(wire)
    data.setdefault('graph_id', 'pg_main')
    _normalize(data)
    _normalize_povs(data, char_canonicals)
    for scene in data.get('scenes', []):
        prefix = scene.get('scene_id', '') + '#'
        for beat in scene.get('beats', []):
            if isinstance(beat.get('beat_id'), str):
                beat['beat_id'] = beat['beat_id'].removeprefix(prefix)
    try:
        body = BodyDesign.model_validate(data)
    except (ValidationError, TypeError) as exc:
        return None, [f'body_design 构造失败：{exc}']
    errors = []
    if constraints and len(body.chapters) != constraints.target_chapter_count:
        errors.append('目标章数与 OutputConstraints 不一致')
    addressed = list(body.initial_state)
    surfaces = list(body.initial_surfaces)
    for scene in body.scenes:
        for cid in (scene.driver.focal_actor, scene.delivery.pov):
            if cid not in known_char_ids and cid != 'narrator':
                errors.append(f'{scene.scene_id} 引用未知 char_id {cid}')
        addressed.extend(scene.state_reads)
        for beat in scene.beats:
            for cid in beat.participants:
                if cid not in known_char_ids:
                    errors.append(f'{scene.scene_id}#{beat.beat_id} participants 引用未知 char_id {cid}')
            addressed.extend(beat.target_delta)
            surfaces.extend(beat.surface_introductions)
    for item in addressed:
        for cid in (*item.subjects, *((item.holder,) if item.holder else ())):
            if cid not in known_char_ids:
                errors.append(f'状态 {item.key} 引用未知 char_id {cid}')
    for item in surfaces:
        if item.char_id not in known_char_ids:
            errors.append(f'称谓释放引用未知 char_id {item.char_id}')
    return (None, errors) if errors else (body, [])
