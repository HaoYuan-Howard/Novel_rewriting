"""Hook选择的纯结构判据；接受与canonical复验共用，不编译或发布产物。

只验证引用闭合、披露时序、隐藏人物与称谓释放授权，返回错误、不修改输入。
assembler先确定性展开wire目录引用，随后校验规范payload；恢复预览与正式Context
compiler直接复验canonical Hook。判据不依赖producer、compiler、LLM或repository。

rc26（命名前移批 C）：identity-text 协议退役——Hook 自有措辞（presentation、
reader_expectation.focus、local_focus）一律普通目标文本，不再有标记语法与
候选覆盖判据；隐藏人物泄漏判据改按目标字面表（`faces`）直接字面检查。

CS-F1（Hook 接入局部修订）：错误在判定处带 wire 路径——wire 与 canonical
payload 的条目容器同形同序（assembler 展开引用、不改列表序），路径两用：wire 面
供条目返修定位（`locate_design_patches`），canonical 复验面留诊断。涉及两侧的
冲突（禁止揭示 vs 材料）同时带两侧路径，模型可改任一侧。
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from contracts.schema.design import BodyDesign
from contracts.schema.hook import HookDesign
from contracts.schema.source import NameProjection, NameStructure
from novel.core.name_materials import expand_surface_releases
from novel.stages.stage4.design_paths import design_issue
from novel.stages.stage4.hook_facts import established_fact, fact_is_current, hook_facts

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


def _established_at(body: BodyDesign, ref: str, key: tuple, fact_ref: str | None) -> bool:
    """具体事实原子是否确实在 ref 处建立（init 或该拍 delta）。"""
    return established_fact(hook_facts(body), ref, key, fact_ref=fact_ref) is not None


def _validate_transitions(materials: list[dict], positions: dict) -> list[str]:
    """只核对显式跳时声明与拍序；合法声明不证明因果或阅读质量。"""
    errors: list[str] = []
    presented_scenes: set[str] = set()
    flashed_back = False
    previous = None
    for index, material in enumerate(materials):
        ref = material.get('beat_ref')
        position = positions.get(ref)
        transition = material.get('transition')
        valid = transition == 'start' if index == 0 else transition != 'start'
        if index and position is not None and previous is not None:
            if transition == 'continue':
                valid = position == (previous[0], previous[1] + 1)
            elif transition == 'time_skip':
                valid = position > previous
            elif transition == 'flashback':
                valid = position < previous
            elif transition == 'return':
                valid = (flashed_back and position > previous
                         and ref.split('#', 1)[0] in presented_scenes)
            elif transition == 'scene_cut':
                valid = position[0] != previous[0]
        if not valid:
            errors.append(_issue(
                f'材料 {ref} 的 transition={transition} 与呈现位置不符；'
                '首材料须 start，承接须同场相邻后拍，跳时向后、倒叙向前，'
                '返回须在倒叙后回到已呈现场，切场须换场',
                'hook_transition', ('materials', index, 'transition'), ('materials', index)))
        if (index and transition == 'flashback' and valid
                and previous is not None and position is not None):
            flashed_back = True
        if isinstance(ref, str) and ref in positions:
            presented_scenes.add(ref.split('#', 1)[0])
        previous = position
    return errors


def _validate_reader_expectation(expectation: dict, *, facts, positions: dict,
                                 horizon: tuple | None) -> list[str]:
    """期待依据必须真实且在材料之后；不把依据存在误报为回答了 focus。"""
    errors: list[str] = []
    path = ('reader_expectation',)
    if expectation.get('kind') == 'fact':
        answer = expectation.get('answer') or {}
        ref = answer.get('ref')
        key = _address_key(answer.get('address'))
        fact = (established_fact(facts, ref, key, fact_ref=answer.get('fact_ref'))
                if key is not None else None)
        if fact is None:
            errors.append(_issue('事实期待的答案必须引用一次真实 set 建立，且带具体地址',
                                 'hook_expectation_fact', path, path))
        elif (fact.delta.domain.value == 'knowledge'
              and fact.delta.knowledge_mode != 'known'):
            errors.append(_issue('事实期待不能把怀疑、听说或相信作为确定答案',
                                 'hook_expectation_knowledge', path, path))
        refs = [ref]
    else:
        refs = list(expectation.get('followup_refs') or ())
        if not refs or len(set(refs)) != len(refs):
            errors.append(_issue('行动／关系期待须有非空且不重复的后续承载拍',
                                 'hook_expectation_followup', path, path))
    for ref in refs:
        if ref not in positions:
            errors.append(_issue(f'期待依据引用不存在的后续拍：{ref}',
                                 'hook_expectation_ref', path, path))
        elif horizon is not None and positions[ref] <= horizon:
            errors.append(_issue(f'期待依据 {ref} 必须晚于全部材料的最大故事视界',
                                 'hook_expectation_horizon', path, path))
    return errors


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
    faces: Mapping[str, Mapping[str, str]] | None = None,
) -> list[str]:
    """复验canonical Hook与当前正文的选择结构；不归一、修复或改写输入。

    正式恢复与编译读回的Hook已经通过schema，但正文候选可能改变了它依赖的
    状态、拍或称谓授权；与wire接受共用同一判据，不能只凭schema可读便采用。
    rc26：`faces`（目标字面表）是隐藏人物泄漏判据的字面宇宙（普通正文，无声明
    区间可排除——隐藏人物的目标字面出现即泄漏）。
    """
    return validate_hook_payload(
        hook.model_dump(mode='json'), body=body, known_char_ids=known_char_ids,
        name_projection=name_projection, name_structure=name_structure, faces=faces)


def validate_hook_payload(
    wire: dict[str, Any], *, body: BodyDesign, known_char_ids: set[str],
    name_projection: NameProjection, name_structure: NameStructure | None = None,
    faces: Mapping[str, Mapping[str, str]] | None = None,
) -> list[str]:
    """位置已确定性解析后的同一套结构判据；wire与canonical复验共用。"""
    errors: list[str] = []

    positions = _position(body)
    facts = hook_facts(body)
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
            elif not any(fact.fact_ref == raw.get('fact_ref') and fact.ref == ref
                         and fact.delta.address() == key for fact in facts):
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
            # rc26：presentation 是普通目标文本——隐藏人物的目标字面（faces 表
            # 全部渲染面）出现即泄漏；没有声明区间可排除，不做词法猜测。
            text = item['presentation']
            for char_id in sorted(hidden_here):
                literals = sorted(set((faces or {}).get(char_id, {}).values()) - {''})
                for literal in literals:
                    if literal in text:
                        errors.append(_issue(
                            f'材料 {ref} 的 presentation 泄漏隐藏人物 {char_id} 的字面 {literal!r}',
                            'hook_presentation_leak', ('materials', k, char_id),
                            ('materials', k)))

    # 禁止揭示：引用真实；无 address 是整拍禁区，同拍不得作为材料。
    # 具体事实只禁止该原子；presentation 替代全拍投影，或 withheld 隐藏该原子
    # 后仍可使用同拍其余材料，未隐藏的事实才与材料冲突。
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
            forbidden_addresses.append((ref, key, item.get('fact_ref')))
            if not _established_at(body, ref, key, item.get('fact_ref')):
                errors.append(_issue(
                    f'禁止揭示的事实在 {ref} 未建立：{key}', 'hook_forbidden_unestablished',
                    ('forbidden_reveals', k), ('forbidden_reveals', k)))
        elif ref not in positions:
            errors.append(_issue(
                f'禁止揭示引用不存在的拍：{ref}', 'hook_forbidden_ref',
                ('forbidden_reveals', k), ('forbidden_reveals', k)))
        if key is None and ref in material_refs:
            errors.append(_issue(
                f'禁止揭示与材料冲突：同一拍 {ref} 既是材料又禁止揭示',
                'hook_forbidden_conflict', ('forbidden_reveals', k, ref),
                ('forbidden_reveals', k), *material_paths.get(ref, ())))
            continue
        if key is None or key not in (deltas.get(ref) or ()):
            continue
        leaked_paths = [('materials', j) for j, other in enumerate(materials)
                        if other.get('beat_ref') == ref and not other.get('presentation')
                        and item.get('fact_ref') not in {
                            w.get('fact_ref') for w in other.get('withheld') or ()
                            if isinstance(w, dict)}]
        if leaked_paths:
            errors.append(_issue(
                f'禁止揭示与材料冲突：{ref} 的 {key} 被材料未隐藏地呈现',
                'hook_forbidden_leak', ('forbidden_reveals', k, key),
                ('forbidden_reveals', k), *leaked_paths))

    # 一次建立由（位置、原子序号、地址）的 fact_ref 定位；同拍同址不吞掉原子。
    # 冷开起点按呈现序第一份材料，视界按最大故事位置。
    # 后续同地址更新不等于同一次揭示；起点前已失效的旧值仍须拒绝。
    material_positions = [positions.get(r) for r in material_refs]
    timing_known = all(p is not None for p in material_positions) if material_positions else False
    errors.extend(_validate_transitions(materials, positions))
    errors.extend(_validate_reader_expectation(
        wire.get('reader_expectation') or {}, facts=facts, positions=positions,
        horizon=max(material_positions) if timing_known else None))
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
        fact = established_fact(facts, ref, key, fact_ref=item.get('fact_ref'))
        if fact is None:
            errors.append(_issue(
                f'起笔事实在 {ref} 未建立：{key}（只能引用已存在的目标事实）',
                'hook_opening_unestablished', ('opening_facts', k, key),
                ('opening_facts', k)))
        if timing_known:
            pos = (-1, -1) if ref == Init else positions[ref]
            audience = item.get('audience')
            opening = material_positions[0]
            if audience in ('world', 'pov') and pos >= opening:
                errors.append(_issue(
                    f'起笔事实（{audience}）必须早于首个材料：{ref}',
                    'hook_opening_timing', ('opening_facts', k, audience),
                    ('opening_facts', k)))
            if (fact is not None and audience in ('world', 'pov') and pos < opening
                    and not fact_is_current(fact, facts, opening)):
                errors.append(_issue(
                    f'起笔事实在冷开起点之前已变化、解除或失效：{ref} 的 {key}',
                    'hook_opening_stale', ('opening_facts', k), ('opening_facts', k)))
            if fact is not None and audience == 'world' and fact.delta.modality not in (None, 'actual'):
                errors.append(_issue(
                    f'起笔世界事实不能把 {fact.delta.modality} 当成已经发生：{ref} 的 {key}',
                    'hook_opening_modality', ('opening_facts', k), ('opening_facts', k)))
            if (fact is not None and audience == 'pov' and fact.delta.domain.value == 'knowledge'
                    and fact.delta.holder not in (pov.get('focalizers') or ())):
                errors.append(_issue(
                    f'起笔 POV 知情持有人不属于视角人物：{fact.delta.holder}',
                    'hook_opening_holder', ('opening_facts', k), ('opening_facts', k)))
            if audience == 'reader' and pos > max(material_positions):
                errors.append(_issue(
                    f'起笔事实（reader）不得晚于材料视界：{ref}',
                    'hook_opening_horizon', ('opening_facts', k), ('opening_facts', k)))
        if (ref, key, item.get('fact_ref')) in forbidden_addresses or (ref in forbidden_refs and
                any(row.get('ref') == ref and row.get('address') is None
                    for row in wire.get('forbidden_reveals') or ())):
            errors.append(_issue(
                f'起笔事实落在禁止揭示集内：{key}', 'hook_opening_forbidden',
                ('opening_facts', k, key), ('opening_facts', k)))
        for other_ref in dict.fromkeys(material_refs):
            if ref == other_ref and key in deltas.get(other_ref, ()):
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
