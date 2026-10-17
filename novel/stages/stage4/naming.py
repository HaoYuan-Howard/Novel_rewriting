#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""naming —— NameMap 的投影、编译与已绑定人物提及的确定性改名。

【Naming 只读投影，Apply 唯一应用】
`naming_projection` 给出应改名集合、需要的名称位、须回避的旧字面、稳定身份与
风格——不读正文、Hook 或 Binding。`compile_name_map` 核对完整覆盖与冲突
（同 family_id 新姓一致；不同源姓不得合并同姓；新旧字面冲突；keep 带理由）。
`compile_bound_replacements` 从正式名称结构及完整 NameMap 按人物身份派生
称谓变换（包含明确保留），不新增 replacement-table 产物。

【具体提及唯一应用】
`apply_bound_names` 只消费 Stage5 回执的具体人物区间；坐标、字面和身份映射
不闭合即拒绝。普通词不因包含旧姓名部件而变化；同形称谓可由不同人物各自改名。
`diff_proof` 验证交付变换之外零改动。语义声明正确性依然 unverified。
源身份/名称结构缺失回 Stage2；目标名称缺失或冲突回 Naming；机械错误归 compiler。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from contracts.schema.naming import NAMING_VALIDATOR_VERSION, NameAssignment, NameMapV6, TargetName
from contracts.schema.source import (
    BlueprintsArtifactV17, CharactersArtifactV6, render_form,
)
from contracts.schema.name_text import NameMention
from novel.core.name_text import validate_name_mentions
from novel.stages.common import group_located_errors


# ───────────────────────── 投影（Naming 的唯一输入面） ─────────────────────────

def naming_projection(
    characters: CharactersArtifactV6,
    blueprint: BlueprintsArtifactV17 | None = None,
) -> dict[str, Any]:
    """Naming 的只读投影（通常不落盘）：应改名集合、名称位、回避字面、身份与风格。

    风格取 Blueprint 的 setting_constant；身份取 core 稳定称呼与 surface 窗口。
    纯称谓人物（无姓名位、无化名）不进入应改名集合。
    """
    if characters.name_structure is None:
        raise ValueError('Characters 未登记名称结构；先完成 Stage2 names 窄调用')
    structure = characters.name_structure
    persons: list[dict[str, Any]] = []
    avoided: set[str] = set()
    for slug in sorted(structure.people):
        person = structure.people[slug]
        entry = structure.families.get(person.family_id) if person.family_id else None
        family = entry.literal if entry else None
        bearing = sorted(s for s, form in person.surface_forms.items() if form is not None)
        aliases = {a.alias_id: a.literal for a in person.aliases}
        core_entry = characters.characters[slug]
        if not (bearing or aliases or person.given):
            # 纯称谓（无姓名位、无化名）不进入应改名集；其字面是保留面。
            avoided.update(s for s, form in person.surface_forms.items()
                           if form is None)
            continue
        persons.append({
            'char_ref': slug,
            'display': core_entry.canonical,
            'surfaces': sorted(s.surface for s in core_entry.surfaces),
            'positions': {
                'family': family,
                'given': person.given,
                'aliases': aliases,
            },
            'name_bearing_surfaces': bearing,
        })
        avoided.update(bearing)
        avoided.update(x for x in (family, person.given, *aliases.values()) if x)
    style = [wc.statement for wc in (blueprint.world_constraints if blueprint else ())
             if wc.kind == 'setting_constant']
    return {
        'rename_set': [p['char_ref'] for p in persons],
        'persons': persons,
        'avoided_old_literals': sorted(avoided),
        'style': style,
    }


# ───────────────────────── NameMap compile ─────────────────────────

def compile_name_map(
    wire: dict[str, Any], characters: CharactersArtifactV6,
) -> tuple[NameMapV6 | None, list[str]]:
    """Naming wire（{'entries': [...]}）→ NameMap v5（full 由 compiler 拼接）。"""
    _map, located = _compile_name_map_located(wire, characters)
    return _map, [message for _paths, message in located]


def naming_patch_locate(
    wire: Any, errors: list, characters: CharactersArtifactV6,
) -> list[tuple[tuple[str | int, ...], list]] | None:
    """CS-H H4（RC-57 覆盖）：naming 条目定位（`ask_json` 的 `patch` 协议）。

    与 `compile_name_map` 同一条管道（不新增判据），把每条错误映射到 wire 的
    `entries[i]` 条目路径；冲突类错误挂到牵连的**每个**条目（改任一侧都能解）。
    wire 级问题（未知字段／entries 缺失）或 `errors` 与复跑定位集不完全一致时
    返回 None——本轮退整份返修，不静默漏项。分组与对账见
    `common.group_located_errors`；裸元组返回，不 import novel.llm
    （分层规则禁 stage7 依赖 LLM 面）。
    """
    if not isinstance(wire, dict) or not errors:
        return None
    _map, located = _compile_name_map_located(wire, characters)
    return group_located_errors(errors, located)


def _compile_name_map_located(
    wire: dict[str, Any], characters: CharactersArtifactV6,
) -> tuple[NameMapV6 | None,
           list[tuple[tuple[tuple[str | int, ...], ...] | None, str]]]:
    """`compile_name_map` 的定位视图：每条错误一项 ``(挂到的条目路径元组, 消息)``
    （None＝wire 级，退整份）。冲突类错误一条消息挂多个条目，**错误列表仍只有一条**
    ——定位面的扇出不改变 `compile_name_map` 返回的错误。"""
    issues: list[tuple[tuple[tuple[str | int, ...], ...] | None, str]] = []

    def fail(path, message: str) -> None:
        issues.append(((path,) if path is not None else None, message))

    errors: list[str] = []
    if set(wire) - {'entries'}:
        fail(None, 'naming wire 携带未知字段')
        errors.append('naming wire 携带未知字段')
    raw_entries = wire.get('entries')
    if not isinstance(raw_entries, list) or not raw_entries:
        # 早退分支只补报自己的问题：此前的「未知字段」已经记入 issues，不再重报。
        fail(None, 'entries 缺失或为空')
        return None, issues
    expected = naming_projection(characters)
    expected_refs = set(expected['rename_set'])
    structure = characters.name_structure
    seen: dict[str, str] = {}
    entries: list[NameAssignment] = []
    entry_index: dict[str, int] = {}
    for index, raw in enumerate(raw_entries):
        if not isinstance(raw, dict) or set(raw) - {'char_ref', 'name', 'aliases', 'keep_reason'}:
            fail(('entries', index), f'naming 条目形状非法：{raw}')
            errors.append(f'naming 条目形状非法：{raw}')
            continue
        ref = raw.get('char_ref')
        if ref not in expected_refs:
            if ref in characters.characters:
                fail(('entries', index), f'{ref} 不在应改名集合（纯称谓人物无需决策）')
                errors.append(f'{ref} 不在应改名集合（纯称谓人物无需决策）')
            else:
                fail(('entries', index), f'未知 char_ref：{ref}')
                errors.append(f'未知 char_ref：{ref}')
            continue
        if ref in seen:
            fail(('entries', index), f'char_ref 重复：{ref}')
            errors.append(f'char_ref 重复：{ref}')
            continue
        seen[ref] = ref
        entry_index[ref] = index
        person = structure.people[ref]
        source_family = (structure.families[person.family_id].literal
                         if person.family_id else None)
        keep_reason = raw.get('keep_reason')
        name_raw = raw.get('name')
        aliases_raw = raw.get('aliases') or {}
        if keep_reason is not None and not str(keep_reason).strip():
            fail(('entries', index), f'{ref} 保留必须带理由')
            errors.append(f'{ref} 保留必须带理由')
            continue
        if name_raw is None:
            if aliases_raw:
                fail(('entries', index), f'{ref} 保留时不得再改化名')
                errors.append(f'{ref} 保留时不得再改化名')
            entries.append(NameAssignment(
                char_ref=ref, name=None, aliases={},
                keep_reason=str(keep_reason)))
            continue
        if not isinstance(name_raw, dict):
            fail(('entries', index), f'{ref} 的 name 形状非法')
            errors.append(f'{ref} 的 name 形状非法')
            continue
        family = name_raw.get('family')
        given = name_raw.get('given')
        aliases_raw = aliases_raw if isinstance(aliases_raw, dict) else {}
        if not family and not given and not aliases_raw:
            fail(('entries', index),
                 f'{ref} 的目标名称全空：改名需给名称位或化名位，保留请给 keep_reason')
            errors.append(
                f'{ref} 的目标名称全空：改名需给名称位或化名位，保留请给 keep_reason')
            continue
        if keep_reason is not None:
            fail(('entries', index), f'{ref} 的 name 与 keep_reason 只能给出一个')
            errors.append(f'{ref} 的 name 与 keep_reason 只能给出一个')
            continue
        for message in (
                f'{ref} 缺少姓氏位（源结构登记了姓 {source_family}）'
                if source_family is not None and not family else None,
                f'{ref} 源结构无姓氏位，不得给 family'
                if source_family is None and family else None,
                f'{ref} 缺少名字位（源结构登记了名 {person.given}）'
                if person.given is not None and not given else None,
                f'{ref} 源结构无名字位，不得给 given'
                if person.given is None and given else None):
            if message:
                fail(('entries', index), message)
                errors.append(message)
        unknown_alias = set(aliases_raw) - {a.alias_id for a in person.aliases}
        if unknown_alias:
            fail(('entries', index), f'{ref} 的化名位未登记：{sorted(unknown_alias)}')
            errors.append(f'{ref} 的化名位未登记：{sorted(unknown_alias)}')
        entries.append(NameAssignment(
            char_ref=ref,
            name=TargetName(family=family, given=given),
            aliases={k: v for k, v in aliases_raw.items()},
            keep_reason=None))
    by_ref = {e.char_ref: e for e in entries}
    missing = expected_refs - set(by_ref)
    if missing:
        # 漏项＝需要新增条目，不是替换既有条目——定位不了，退整份返修。
        fail(None, f'应改名集合漏项：{sorted(missing)}')
        errors.append(f'应改名集合漏项：{sorted(missing)}')

    if not errors:
        for message, refs in _check_name_conflicts(entries, characters, structure):
            paths = tuple(dict.fromkeys(
                ('entries', entry_index[ref]) for ref in refs if ref in entry_index))
            issues.append((paths or None, message))
            errors.append(message)
    if errors:
        return None, issues
    # full 由 compiler 拼接（wire 不携带）；化名位改名人物可无姓名位。
    filled: list[NameAssignment] = []
    for entry in entries:
        if entry.name is None:
            filled.append(entry)
            continue
        full = ((entry.name.family or '') + (entry.name.given or '')) or None
        filled.append(entry.model_copy(update={
            'name': TargetName(family=entry.name.family, given=entry.name.given,
                               full=full)}))
    return NameMapV6(entries=tuple(filled)), issues


def _check_name_conflicts(entries, characters, structure) -> list[tuple[str, tuple[str, ...]]]:
    """同 family_id 新姓一致；不同源姓不得合并同姓；新字面与跨人旧字面冲突。

    返回 (message, 牵连 char_ref 序)：定位器把冲突挂到牵连条目（改任一侧都能
    解）。本人保留未改的名称位（如只换姓、名不变）不算冲突；新姓必须与本人旧姓
    不同（换名就要真换）。
    """
    problems: list[tuple[str, tuple[str, ...]]] = []
    renames = {e.char_ref: e for e in entries if e.name is not None}
    # 家族一致性：同 family_id → 同新姓；不同 literal 的 family → 不得同新姓。
    family_new: dict[str, str] = {}
    family_new_ref: dict[str, str] = {}
    new_family_owner: dict[str, tuple[str, str]] = {}
    for ref, entry in sorted(renames.items()):
        if not entry.name.family:
            continue
        person = structure.people[ref]
        family_id = person.family_id
        if family_id:
            registered = family_new.get(family_id)
            if registered is not None and registered != entry.name.family:
                problems.append((
                    f'同一家族 {family_id} 的新姓不一致：{registered} 与 {entry.name.family}'
                    f'（{family_new_ref[family_id]} 与 {ref}）',
                    (family_new_ref[family_id], ref)))
            else:
                family_new.setdefault(family_id, entry.name.family)
                family_new_ref.setdefault(family_id, ref)
            literal = structure.families[family_id].literal
            if entry.name.family == literal:
                problems.append((f'{ref} 的新姓与本人旧姓相同：{literal!r}', (ref,)))
        owner = new_family_owner.get(entry.name.family)
        if owner is None:
            source = structure.families[family_id].literal if family_id else None
            new_family_owner[entry.name.family] = (source or '', ref)
        else:
            source = structure.families[family_id].literal if family_id else None
            if owner[0] != (source or ''):
                problems.append((
                    f'不同源姓 {owner[0]!r} 与 {source!r} 不得合并到同一新姓 '
                    f'{entry.name.family!r}（{owner[1]} 与 {ref}）', (owner[1], ref)))
    # 跨人旧字面集：新字面不得与**他人**的专名字面/带名 surface 相撞；
    # 本人保留未改的名称位（同名传入）不算冲突。
    for ref, entry in sorted(renames.items()):
        person = structure.people[ref]
        own = set(person.identity_literals(structure.families))
        others: set[str] = set()
        for other_slug, other_person in structure.people.items():
            if other_slug == ref:
                continue
            others.update(other_person.identity_literals(structure.families))
            others.update(s for s, form in other_person.surface_forms.items()
                          if form is not None)
        news = [x for x in (entry.name.family, entry.name.given,
                            *entry.aliases.values()) if x]
        for literal in news:
            if literal in others:
                problems.append((f'{ref} 的新字面 {literal!r} 与他人旧字面冲突', (ref,)))
            if literal in own and literal != person.given and literal not in {
                    a.literal for a in person.aliases}:
                problems.append((f'{ref} 的新字面 {literal!r} 与本人旧字面冲突', (ref,)))
    new_literals: dict[str, str] = {}
    for ref, entry in sorted(renames.items()):
        for literal in [x for x in (entry.name.given, *entry.aliases.values()) if x]:
            if literal in new_literals and new_literals[literal] != ref:
                problems.append((
                    f'新字面 {literal!r} 归属多人：{new_literals[literal]} 与 {ref}',
                    (new_literals[literal], ref)))
            new_literals.setdefault(literal, ref)
    return problems


# ───────────────────────── 替换表（纯函数）与应用 ─────────────────────────

def compile_bound_replacements(
    characters: CharactersArtifactV6, name_map: NameMapV6,
) -> tuple[dict[str, dict[str, str]], list[str]]:
    """按人物身份编译登记称谓，含合法保留的恒等项，不凭字面选择 owner。

    同一称谓归属于不同人物是合法的；只有回执已经绑定身份的具体提及会应用。
    同一源称谓对同一人物出现不同渲染才是 compiler 错误。
    """
    structure = characters.name_structure
    if structure is None:
        return {}, ['Characters 未登记名称结构']
    checked, errors = compile_name_map({'entries': [{
        'char_ref': entry.char_ref,
        'name': entry.name.model_dump(include={'family', 'given'}) if entry.name else None,
        'aliases': entry.aliases, 'keep_reason': entry.keep_reason,
    } for entry in name_map.entries]}, characters)
    if errors:
        return {}, [f'NameMap 与源名称结构不闭合：{error}' for error in errors]
    assignments = {entry.char_ref: entry for entry in checked.entries}
    result: dict[str, dict[str, str]] = {}
    errors = []
    for char_id, person in sorted(structure.people.items()):
        entry = assignments.get(char_id)
        family = structure.families.get(person.family_id) if person.family_id else None
        aliases = {alias.alias_id: alias.literal for alias in person.aliases}
        renamed = entry is not None and entry.name is not None
        if renamed:
            aliases.update(entry.aliases)
        surname = entry.name.family if renamed and entry.name.family else (
            family.literal if family else None)
        given = entry.name.given if renamed and entry.name.given else person.given
        table: dict[str, str] = {}
        for surface, form in person.surface_forms.items():
            if not renamed or form is None:
                table[surface] = surface
                continue
            try:
                table[surface] = render_form(form, family=surname, given=given, aliases=aliases)
            except ValueError as exc:
                errors.append(f'{char_id} 的 surface {surface!r} 渲染失败：{exc}')
        if family is not None:
            for item in family.forms:
                try:
                    target = render_form(item.form, family=surname, given=None, aliases={})
                except ValueError as exc:
                    errors.append(f'{char_id} 的家族称谓 {item.literal!r} 渲染失败：{exc}')
                    continue
                if item.literal in table and table[item.literal] != target:
                    errors.append(f'{char_id} 的称谓 {item.literal!r} 存在冲突渲染')
                else:
                    table[item.literal] = target
        result[char_id] = table
    return result, errors


def apply_bound_names(
    text: str, mentions: Iterable[NameMention],
    replacements: Mapping[str, Mapping[str, str]],
) -> tuple[str, list[str]]:
    """只替换回执已绑定的提及；正文其他字节逐字保留，不全局搜旧名。"""
    mentions = tuple(mentions)
    errors = [issue.message for issue in validate_name_mentions(text, mentions)]
    for mention in mentions:
        if mention.char_id not in replacements or mention.surface not in replacements[mention.char_id]:
            errors.append(f'提及没有登记的改名或保留决定：{mention.char_id}/{mention.surface}')
    if errors:
        return text, errors
    fragments: list[str] = []
    cursor = 0
    for mention in mentions:
        fragments.extend((text[cursor:mention.start],
                          replacements[mention.char_id][mention.surface]))
        cursor = mention.end
    fragments.append(text[cursor:])
    return ''.join(fragments), []


def diff_proof(
    original: str, renamed: str, replacements: Mapping[str, Mapping[str, str]],
    mentions: Iterable[NameMention],
) -> list[str]:
    """验证具体提及变换的全部差异，正文其他字节必须逐字一致。"""
    expected, errors = apply_bound_names(original, mentions, replacements)
    if errors:
        return errors
    return [] if expected == renamed else ['diff 差异与身份绑定提及不符：存在表外改动或漏改']
