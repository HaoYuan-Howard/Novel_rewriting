"""Body Design wire 的确定性组装（F6 改版）。

RC2 时代本模块直接组装 PresentationGraph；RC3 起 Hook 与正文同一份 graph 但
分开设计：这里只组装 **BodyDesign**（无 hook 字段），完整 graph 由
`hook_design.publish_graph(body, hook)` 唯一发布——从类型上保证 Bind／Compile／
Context／Guard 消费的 body 视图不含 Hook，Hook 不影响正文。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError
from contracts.schema.str06.design import BodyDesign
from contracts.schema.str06.grounding import OutputConstraints


def assemble_body_design(
    wire: dict[str, Any], *, known_char_ids: set[str],
    constraints: OutputConstraints | None = None,
) -> tuple[BodyDesign | None, list[str]]:
    # 仅剥离已明确给出的场景前缀，不补猜缺失语义。
    data = deepcopy(wire)
    data.setdefault('graph_id', 'pg_main')
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
