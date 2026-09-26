#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""name_structure —— Stage2 names 窄调用的确定性 compile（F6）。

【职责】compiler：names wire → Characters 的 `name_structure` 段。语义判断
（谁是这个人的姓、哪个称谓是纯称谓）归抽取 LLM；本模块只做确定性转换与闭合
校验：键白名单、引用闭合、模板渲染等式、字面必须出现在源文中。

【未登记姓氏构词探针（只报告，不阻断，不猜）】
多字姓（复姓）严格：源文中任何未被登记 surface／family form 覆盖的完整姓字面
出现都列为条目；单字姓只列上下文供人工复核。探针**永不**改写 wire 或拒绝产物
——漏标构词是 Stage2 语义面的事，compiler 不补猜（I-20/I-21）。
"""
from __future__ import annotations

import re
from typing import Any, NamedTuple

from contracts.schema.source import (
    CharactersCore, NameStructure, render_form,
)

_PEOPLE_KEYS = frozenset({'family_id', 'given', 'aliases', 'surface_forms'})
_FAMILY_KEYS = frozenset({'literal', 'forms'})
#: wire 允许对象内冗余自报名（与 dict 键一致时归一，不一致报错）
_FAMILY_OPTIONAL = frozenset({'family_id'})


class ProbeFinding(NamedTuple):
    """一条未登记构词探针发现：只报告。"""

    literal: str
    context: str
    strict: bool


def compile_name_structure(
    wire: dict[str, Any], characters: CharactersCore, source_text: str,
) -> tuple[NameStructure | None, list[str]]:
    """names wire（{'families': ..., 'people': ...}）→ NameStructure。

    全部错误聚合返回；wire 原样进、不修改（I-24）。
    """
    errors: list[str] = []
    if set(wire) - {'families', 'people'}:
        errors.append('names wire 携带未知字段')
    raw_families = wire.get('families') or {}
    raw_people = wire.get('people') or {}
    if not isinstance(raw_families, dict) or not raw_families:
        return None, (errors + ['families 缺失或为空'])
    if not isinstance(raw_people, dict):
        return None, (errors + ['people 必须是对象'])
    slugs = set(characters.characters)
    unknown_people = set(raw_people) - slugs
    if unknown_people:
        errors.append(f'people 引用了 Characters 之外的人物：{sorted(unknown_people)}')
    missing_people = slugs - set(raw_people)
    if missing_people:
        errors.append(f'people 缺少人物条目：{sorted(missing_people)}')

    # 家族：键白名单、字面非空、源文出现、form 只用 {family}。
    for family_id, raw in raw_families.items():
        where = f'家族 {family_id}'
        if not isinstance(raw, dict) or set(raw) - _FAMILY_KEYS - _FAMILY_OPTIONAL:
            errors.append(f'{where} 携带未知字段或形状非法')
            continue
        if 'family_id' in raw and raw['family_id'] != family_id:
            errors.append(f'{where} 对象内 family_id 与键不一致：{raw["family_id"]!r}')
            continue
        literal = raw.get('literal')
        if not isinstance(literal, str) or not literal:
            errors.append(f'{where} 的 literal 缺失或为空')
            continue
        if literal not in source_text:
            errors.append(f'{where} 的字面 {literal!r} 未在源文中出现')
        for form in raw.get('forms') or []:
            if not isinstance(form, dict) or set(form) - {'literal', 'form', 'source_refs'}:
                errors.append(f'{where} 称谓条目形状非法')
                continue
            try:
                rendered = render_form(form['form'], family=literal, given=None, aliases={})
            except ValueError as exc:
                errors.append(f'{where} 称谓 {form.get("literal")!r}：{exc}')
                continue
            if rendered != form.get('literal'):
                errors.append(
                    f'{where} 称谓 {form.get("literal")!r} 与构词渲染不一致')
            if form.get('literal') and form['literal'] not in source_text:
                errors.append(f'{where} 称谓字面 {form["literal"]!r} 未在源文中出现')

    # 人物：键白名单、引用闭合、surface 覆盖、渲染等式、字面源文出现。
    for slug, raw in raw_people.items():
        if slug not in slugs:
            continue
        where = f'人物 {slug}'
        if not isinstance(raw, dict) or set(raw) - _PEOPLE_KEYS:
            errors.append(f'{where} 携带未知字段或形状非法')
            continue
        surfaces = {s.surface for s in characters.characters[slug].surfaces}
        surface_forms = raw.get('surface_forms')
        if not isinstance(surface_forms, dict) or set(surface_forms) != surfaces:
            errors.append(f'{where} 的 surface_forms 未恰好覆盖全部 surface')
            surface_forms = surface_forms if isinstance(surface_forms, dict) else {}
        aliases_raw = raw.get('aliases') or []
        aliases: dict[str, str] = {}
        for alias in aliases_raw:
            if not isinstance(alias, dict) or set(alias) - {'alias_id', 'literal'}:
                errors.append(f'{where} 化名条目形状非法')
                continue
            if alias['alias_id'] in aliases:
                errors.append(f'{where} 化名 id 重复：{alias["alias_id"]}')
            aliases[alias['alias_id']] = alias.get('literal') or ''
            if alias.get('literal') and alias['literal'] not in source_text:
                errors.append(f'{where} 化名字面 {alias["literal"]!r} 未在源文中出现')
        family_id = raw.get('family_id')
        family_literal = None
        if family_id is not None:
            entry = raw_families.get(family_id)
            if not isinstance(entry, dict):
                errors.append(f'{where} 的 family_id {family_id!r} 未登记家族')
            else:
                family_literal = entry.get('literal')
        given = raw.get('given')
        if given is not None and not isinstance(given, str):
            errors.append(f'{where} 的 given 必须是字符串或 null')
            given = None
        if given and given not in source_text:
            errors.append(f'{where} 的名字面 {given!r} 未在源文中出现')
        for alias_literal in aliases.values():
            if alias_literal and alias_literal not in source_text:
                errors.append(f'{where} 的化名字面 {alias_literal!r} 未在源文中出现')
        for surface, form in surface_forms.items():
            if form is None:
                for literal in (family_literal, given, *aliases.values()):
                    if literal and literal in surface:
                        errors.append(
                            f'{where} 的纯称谓 {surface!r} 含本人姓名字面 {literal!r}'
                            '（防漏标：应登记构词）')
                continue
            try:
                rendered = render_form(form, family=family_literal, given=given,
                                       aliases=aliases)
            except ValueError as exc:
                errors.append(f'{where} 的 surface {surface!r}：{exc}')
                continue
            if rendered != surface:
                errors.append(
                    f'{where} 的 surface {surface!r} 与构词渲染不一致：'
                    f'{form!r} 渲染为 {rendered!r}')
    if errors:
        return None, errors
    # schema 构造期复检全部跨段规则（渲染等式、覆盖、闭合）；先剥家族对象内
    # 与键一致的冗余 family_id（wire 允许携带，canonical 不收）。
    normalized = {
        'families': {fid: {k: v for k, v in raw.items() if k != 'family_id'}
                     for fid, raw in raw_families.items()},
        'people': raw_people,
    }
    from pydantic import ValidationError
    try:
        return NameStructure.model_validate(normalized), []
    except ValidationError as exc:
        return None, [f'name_structure 构造失败：{exc}']


def merge_name_structure(
    core: CharactersCore, structure: NameStructure,
) -> tuple[Any | None, list[str]]:
    """core + name_structure → 完整 CharactersArtifactV4（发布前唯一合并点）。"""
    from pydantic import ValidationError
    from contracts.schema.source import CharactersArtifactV4
    payload = core.model_dump()
    payload['name_structure'] = structure.model_dump()
    try:
        return CharactersArtifactV4.model_validate(payload), []
    except ValidationError as exc:
        return None, [f'characters v4 合并名称结构失败：{exc}']


def surname_construction_probe(
    structure: NameStructure, source_text: str,
) -> tuple[ProbeFinding, ...]:
    """未登记姓氏构词探针（只报告）：姓字面在源文中的出现位置。复姓逐处
    strict=True 列出（严格复核面）；单字姓 strict=False 只列上下文。不阻断，
    不推断归属——漏标构词归 Stage2 语义面修订。"""
    findings: list[ProbeFinding] = []
    for entry in structure.families.values():
        for match in re.finditer(re.escape(entry.literal), source_text):
            snippet = source_text[max(0, match.start() - 6):match.end() + 6]
            findings.append(ProbeFinding(
                literal=entry.literal, context=snippet.replace('\n', ' '),
                strict=len(entry.literal) > 1))
    return tuple(findings)
