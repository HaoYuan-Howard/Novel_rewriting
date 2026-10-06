#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""naming —— NameMap v5 的投影、编译、单遍应用与残留判定（F6，Stage7）。

【Naming 只读投影，Apply 唯一应用】
`naming_projection` 给出应改名集合、需要的名称位、须回避的旧字面、稳定身份与
风格——不读正文、Hook 或 Binding。`compile_name_map` 核对完整覆盖与冲突
（同 family_id 新姓一致；不同源姓不得合并同姓；新旧字面冲突；keep 带理由）。
`compile_surface_replacements` 是纯函数，不新增 replacement-table 产物；同一源
字面对应多个不同值时报错并列出 owner，不按替换顺序选 owner。

【单遍与残留】
`apply_names` 单遍长名优先（kept 项作为恒等项认领）；`check_residual` 只查应
替换的字面、跳过认领区间——目标名含旧字面、kept 含映射键时不误报。
源身份/名称结构缺失回 Stage2；目标名称缺失或冲突回 Naming；机械错误归 compiler。
"""
from __future__ import annotations

from typing import Any

from contracts.schema.naming import NameAssignment, NameMapV5, TargetName
from contracts.schema.source import (
    BlueprintsArtifactV15, CharactersArtifactV4, render_form,
)
from novel.stages.common import (
    apply_replacements, group_located_errors, uncovered_occurrences,
)

NAMING_VALIDATOR_VERSION = 'str06-naming-v1'


# ───────────────────────── 投影（Naming 的唯一输入面） ─────────────────────────

def naming_projection(
    characters: CharactersArtifactV4,
    blueprint: BlueprintsArtifactV15 | None = None,
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
    wire: dict[str, Any], characters: CharactersArtifactV4,
) -> tuple[NameMapV5 | None, list[str]]:
    """Naming wire（{'entries': [...]}）→ NameMap v5（full 由 compiler 拼接）。"""
    _map, located = _compile_name_map_located(wire, characters)
    return _map, [message for _paths, message in located]


def naming_patch_locate(
    wire: Any, errors: list, characters: CharactersArtifactV4,
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
    wire: dict[str, Any], characters: CharactersArtifactV4,
) -> tuple[NameMapV5 | None,
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
    return NameMapV5(entries=tuple(filled)), issues


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

def compile_surface_replacements(
    characters: CharactersArtifactV4, name_map: NameMapV5,
) -> tuple[dict[str, str], list[str]]:
    """Characters + NameMap → 确定性替换表（不落盘）。

    同一源字面对应多个不同值时报错并列出 owner；替换值包含另一键（链式替换
    风险）时报错。kept 条目与纯称谓（form=None）不产生键。
    """
    errors: list[str] = []
    structure = characters.name_structure
    table: dict[str, str] = {}
    owner_of: dict[str, str] = {}
    for entry in name_map.entries:
        person = structure.people[entry.char_ref]
        if entry.name is None:
            continue
        source_family = (structure.families[person.family_id].literal
                         if person.family_id else None)
        alias_targets = {a.alias_id: a.literal for a in person.aliases}
        for alias_id, new_literal in entry.aliases.items():
            alias_targets[alias_id] = new_literal
        parts = {
            'family': entry.name.family or source_family,
            'given': entry.name.given or person.given,
        }
        parts.update(alias_targets)
        for surface, form in person.surface_forms.items():
            if form is None:
                continue
            try:
                value = render_form(form, family=parts.get('family'),
                                    given=parts.get('given'), aliases=alias_targets)
            except ValueError as exc:
                errors.append(f'{entry.char_ref} 的 surface {surface!r} 渲染失败：{exc}')
                continue
            if value != surface:
                _record(table, owner_of, surface, value, entry.char_ref, errors)
        # 家族级称谓：该家族的 forms 按新姓展开（一次）。
        if person.family_id and entry.name.family:
            for item in structure.families[person.family_id].forms:
                value = render_form(item.form, family=entry.name.family,
                                    given=None, aliases={})
                _record(table, owner_of, item.literal, value,
                        f'family:{person.family_id}', errors)
        # 化名键：旧字面 → 新字面（未改的化名保持恒等，不产生键）。
        for alias in person.aliases:
            target = entry.aliases.get(alias.alias_id)
            if target is not None and target != alias.literal:
                _record(table, owner_of, alias.literal, target, entry.char_ref, errors)
    for key, value in table.items():
        for other in table:
            if other != key and other in value:
                errors.append(f'替换值包含另一键（链式替换风险）：{key}->{value} 含 {other}')
    return table, errors


def _record(table: dict[str, str], owner_of: dict[str, str], key: str, value: str,
            owner: str, errors: list[str]) -> None:
    registered = table.get(key)
    if registered is None:
        table[key] = value
        owner_of[key] = owner
    elif registered != value:
        errors.append(
            f'同一源字面 {key!r} 对应多个不同值：{registered!r}（{owner_of[key]}）与 '
            f'{value!r}（{owner}）；不按替换顺序选 owner')


def apply_names(text: str, replacements: dict[str, str]) -> tuple[str, tuple]:
    """单遍替换：长名优先；kept 不在 replacements 里，天然恒等。"""
    return apply_replacements(text, replacements)


def check_residual(
    text: str, replacements: dict[str, str], claims: tuple,
    kept_literals: tuple[str, ...] = (),
) -> list[str]:
    """残留判定：只查应替换的字面，跳过认领区间与合法保留字面。

    - 目标名含旧字面：替换值里的旧字面落在 apply 的认领区间内 → 不误报；
    - kept 包含映射键：该字面是合法保留面 → 从查禁集剔除。
    """
    scan = set(replacements) - set(kept_literals)
    problems: list[str] = []
    for claim in uncovered_occurrences(text, scan, claims):
        problems.append(
            f'应替换的源名 {claim.literal!r} 在交付文本中残留且未认领（位置 {claim.start}）')
    return problems


def diff_proof(
    original: str, renamed: str, replacements: dict[str, str], claims: tuple,
) -> list[str]:
    """机械证明：renamed 与 original 的全部差异恰为替换表，别处零改动。

    原文按命中序列挖空、改名稿按替换值序列挖空，两个骨架必须逐字相等。
    """
    def _skeleton(text: str, fragments: list[str]) -> str:
        out: list[str] = []
        pos = 0
        for i, frag in enumerate(fragments):
            j = text.find(frag, pos)
            if j < 0:
                raise ValueError(f'第 {i} 个片段未按序出现：{frag!r}')
            out.append(text[pos:j])
            out.append(f'\x00{i}\x00')
            pos = j + len(frag)
        out.append(text[pos:])
        return ''.join(out)

    try:
        originals = [c.literal for c in claims]
        replaced = [replacements[c.literal] for c in claims]
        if _skeleton(original, originals) != _skeleton(renamed, replaced):
            return ['diff 差异与替换表不符：存在表外改动']
    except ValueError as exc:
        return [f'diff proof 异常：{exc}']
    return []
