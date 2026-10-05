"""Hook选择的纯结构判据；接受与canonical复验共用，不编译或发布产物。

只验证引用闭合、披露时序、隐藏人物与称谓释放授权，返回错误、不修改输入。
assembler先确定性解析wire位置，随后校验规范payload；恢复预览与正式Context
compiler直接复验canonical Hook。判据不依赖producer、compiler、LLM或repository。

CS-F1（Hook 接入局部修订）：错误在判定处带 wire 路径——wire 与 canonical
payload 同形同序（assembler 只解析位置引用、不改列表序），路径两用：wire 面
供条目返修定位（`locate_design_patches`），canonical 复验面留诊断。涉及两侧的
冲突（禁止揭示 vs 材料）同时带两侧路径，模型可改任一侧。
"""
from __future__ import annotations

from typing import Any

from contracts.schema.design import BodyDesign
from contracts.schema.hook import HookDesign
from contracts.schema.source import NameProjection, NameStructure
from novel.core.name_materials import expand_surface_releases
from novel.stages.stage4.design_paths import design_issue

Init = 'init'


def _issue(message: str, code: str, location: tuple, *paths: tuple):
    """带规则码与可编辑 wire 路径的判据问题；路径为空＝该问题不可条目定位。"""
    return design_issue(message, code=code, location=location, paths=paths)


def _address_key(raw: dict[str, Any] | None) -> tuple | None:
    """wire 的 StateAddress → address() 元组；形状非法返回 None。"""
    if not isinstance(raw, dict) or 'domain' not in raw or 'key' not in raw:
        return None
    return (raw['domain'], tuple(raw.get('subjects') or ()),
            raw.get('holder') or '', raw['key'])


def _position(body: BodyDesign) -> dict[str, tuple[int, int]]:
    return {f'{s.scene_id}#{b.beat_id}': (i, j)
            for i, s in enumerate(body.scenes) for j, b in enumerate(s.beats)}


def _beat(body: BodyDesign, ref: str):
    if ref == Init:
        return None
    scene_id, beat_id = ref.split('#', 1)
    return body.beat(scene_id, beat_id)


def _beat_deltas(body: BodyDesign) -> dict[str, tuple[tuple, ...]]:
    """beat_ref → 该拍全部 delta 地址（address() 元组）。"""
    out: dict[str, tuple[tuple, ...]] = {}
    for s in body.scenes:
        for b in s.beats:
            out[f'{s.scene_id}#{b.beat_id}'] = tuple(d.address() for d in b.target_delta)
    return out


def _established_at(body: BodyDesign, ref: str, key: tuple) -> bool:
    """地址为 key 的事实是否确实在 ref 处建立（init 或该拍 delta）。"""
    if ref == Init:
        return any(d.address() == key for d in body.initial_state)
    return key in (_beat_deltas(body).get(ref) or ())


def _delta_subjects(body: BodyDesign, ref: str) -> set[str]:
    beat = _beat(body, ref)
    subjects: set[str] = set()
    if beat is None:
        return subjects
    for d in beat.target_delta:
        subjects.update(d.subjects)
        if d.holder:
            subjects.add(d.holder)
    return subjects


def validate_hook_design(
    hook: HookDesign, *, body: BodyDesign, known_char_ids: set[str],
    name_projection: NameProjection, name_structure: NameStructure | None = None,
) -> list[str]:
    """复验canonical Hook与当前正文的选择结构；不归一、修复或改写输入。

    正式恢复与编译读回的Hook已经通过schema，但正文候选可能改变了它依赖的
    状态、拍或称谓授权；与wire接受共用同一判据，不能只凭schema可读便采用。
    """
    return validate_hook_payload(
        hook.model_dump(mode='json'), body=body, known_char_ids=known_char_ids,
        name_projection=name_projection, name_structure=name_structure)


def validate_hook_payload(
    wire: dict[str, Any], *, body: BodyDesign, known_char_ids: set[str],
    name_projection: NameProjection, name_structure: NameStructure | None = None,
) -> list[str]:
    """位置已确定性解析后的同一套结构判据；wire与canonical复验共用。"""
    errors: list[str] = []

    positions = _position(body)
    deltas = _beat_deltas(body)
    concealed_raw = list(wire.get('concealed') or [])
    concealed = set(concealed_raw)
    for char_id in sorted(concealed - known_char_ids):
        errors.append(_issue(
            f'concealed 引用未知人物：{char_id}', 'hook_concealed_char',
            ('concealed', char_id), ('concealed', concealed_raw.index(char_id))))
    concealed &= known_char_ids

    # POV：first 恰一人且非 narrator 由 schema 保；这里保人物真实。
    pov = wire.get('pov') or {}
    for i, focal in enumerate(pov.get('focalizers') or ()):
        if focal != 'narrator' and focal not in known_char_ids:
            errors.append(_issue(f'pov 引用未知人物：{focal}', 'hook_pov_char',
                                 ('pov', focal), ('pov', 'focalizers', i)))

    # 材料：withheld ⊆ 该拍 delta；隐藏人物的材料必须 presentation 且不含其字面。
    material_refs: list[str] = []
    material_paths: dict[str, list[tuple]] = {}
    materials = list(wire.get('materials') or [])
    for k, item in enumerate(materials):
        ref = item.get('beat_ref')
        material_refs.append(ref)
        material_paths.setdefault(ref, []).append(('materials', k))
        beat_keys = deltas.get(ref)
        if beat_keys is None:
            errors.append(_issue(f'材料引用不存在的拍：{ref}', 'hook_material_ref',
                                 ('materials', k, ref), ('materials', k)))
            continue
        for raw in item.get('withheld') or []:
            key = _address_key(raw)
            if key is None:
                errors.append(_issue(
                    f'材料 {ref} 的 withheld 形状非法：{raw}', 'hook_withheld_shape',
                    ('materials', k, 'withheld'), ('materials', k)))
            elif key not in beat_keys:
                errors.append(_issue(
                    f'材料 {ref} 的 withheld 不属于该拍 delta：{key}',
                    'hook_withheld_ref', ('materials', k, 'withheld', key),
                    ('materials', k)))
        hidden_here = ({p for p in (_beat(body, ref).participants if _beat(body, ref) else ())
                        } | _delta_subjects(body, ref)) & concealed
        if hidden_here and not item.get('presentation'):
            errors.append(_issue(
                f'材料 {ref} 涉及隐藏人物 {sorted(hidden_here)}，必须给出 presentation',
                'hook_hidden_presentation', ('materials', k), ('materials', k)))
        if item.get('presentation'):
            for char_id in sorted(hidden_here):
                entry = name_projection.person(char_id)
                literals = (set(entry.proper_literals) | set(entry.name_bearing_surfaces)
                            if entry else set())
                for literal in sorted(literals):
                    if literal and literal in item['presentation']:
                        errors.append(_issue(
                            f'材料 {ref} 的 presentation 泄漏隐藏人物 {char_id} 的字面 {literal!r}',
                            'hook_presentation_leak', ('materials', k, char_id),
                            ('materials', k)))

    # 禁止揭示：引用真实；与材料不冲突——同一拍不得既是材料又是禁 reveal；
    # 材料若未以 presentation 或 withheld 隐藏被禁地址，即视为冲突。
    forbidden_refs: list[str] = []
    forbidden_addresses: list[tuple] = []
    for k, item in enumerate(wire.get('forbidden_reveals') or []):
        ref = item.get('ref')
        forbidden_refs.append(ref)
        key = _address_key(item.get('address'))
        if item.get('address') is not None:
            if key is None:
                errors.append(_issue(
                    f'forbidden_reveals 的 address 形状非法：{item.get("address")}',
                    'hook_forbidden_address', ('forbidden_reveals', k),
                    ('forbidden_reveals', k)))
                continue
            forbidden_addresses.append(key)
        if ref in material_refs:
            errors.append(_issue(
                f'禁止揭示与材料冲突：同一拍 {ref} 既是材料又禁止揭示',
                'hook_forbidden_conflict', ('forbidden_reveals', k, ref),
                ('forbidden_reveals', k), *material_paths.get(ref, ())))
            continue
        if key is None or key not in (deltas.get(ref) or ()):
            continue
        leaked_paths = [('materials', j) for j, other in enumerate(materials)
                        if other.get('beat_ref') == ref and not other.get('presentation')
                        and key not in {wk for wk in (_address_key(w)
                                                      for w in other.get('withheld') or ())
                                         if wk is not None}]
        if leaked_paths:
            errors.append(_issue(
                f'禁止揭示与材料冲突：{ref} 的 {key} 被材料未隐藏地呈现',
                'hook_forbidden_leak', ('forbidden_reveals', k, key),
                ('forbidden_reveals', k), *leaked_paths))

    # 起笔事实：必须真实建立、时序合法、不在禁止集内、不是任何材料拍的 delta。
    material_positions = [positions.get(r) for r in material_refs]
    timing_known = all(p is not None for p in material_positions) if material_positions else False
    for k, item in enumerate(wire.get('opening_facts') or []):
        ref = item.get('ref')
        key = _address_key(item.get('address'))
        if key is None:
            errors.append(_issue(
                f'起笔事实的 address 形状非法：{item.get("address")}',
                'hook_opening_address', ('opening_facts', k), ('opening_facts', k)))
            continue
        if ref != Init and ref not in positions:
            errors.append(_issue(
                f'起笔事实引用不存在的拍：{ref}', 'hook_opening_ref',
                ('opening_facts', k, ref), ('opening_facts', k)))
            continue
        if not _established_at(body, ref, key):
            errors.append(_issue(
                f'起笔事实在 {ref} 未建立：{key}（只能引用已存在的目标事实）',
                'hook_opening_unestablished', ('opening_facts', k, key),
                ('opening_facts', k)))
        if timing_known:
            pos = (-1, -1) if ref == Init else positions[ref]
            audience = item.get('audience')
            if audience in ('world', 'pov') and pos >= min(material_positions):
                errors.append(_issue(
                    f'起笔事实（{audience}）必须早于首个材料：{ref}',
                    'hook_opening_timing', ('opening_facts', k, audience),
                    ('opening_facts', k)))
            if audience == 'reader' and pos > max(material_positions):
                errors.append(_issue(
                    f'起笔事实（reader）不得晚于材料视界：{ref}',
                    'hook_opening_horizon', ('opening_facts', k), ('opening_facts', k)))
        if key in forbidden_addresses:
            errors.append(_issue(
                f'起笔事实落在禁止揭示集内：{key}', 'hook_opening_forbidden',
                ('opening_facts', k, key), ('opening_facts', k)))
        for other_ref in dict.fromkeys(material_refs):
            if other_ref in deltas and key in deltas[other_ref]:
                errors.append(_issue(
                    f'起笔事实是材料拍 {other_ref} 的 delta：{key}',
                    'hook_opening_material_delta', ('opening_facts', k, key),
                    ('opening_facts', k)))

    # 称谓释放：必须是 Design 已声明的面，且不属于隐藏人物。
    declared = expand_surface_releases(body.declared_surfaces(), name_structure)
    for k, item in enumerate(wire.get('surface_releases') or []):
        char_id = item.get('char_id')
        faces = declared.get(char_id, set())
        for surface in item.get('surfaces') or ():
            if surface not in faces:
                errors.append(_issue(
                    f'释放的称谓 {surface!r} 不是 {char_id} 已声明的面',
                    'hook_release_face', ('surface_releases', k, surface),
                    ('surface_releases', k)))
        if char_id in concealed:
            errors.append(_issue(
                f'称谓释放针对隐藏人物 {char_id}，拒绝', 'hook_release_hidden',
                ('surface_releases', k, char_id), ('surface_releases', k)))

    return errors
