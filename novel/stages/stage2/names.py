#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""name_structure —— Stage2 names 窄调用的确定性 compile（F6；CS-S 1c 升 v6）。

【职责】compiler：names wire → Characters 的 `name_structure` 段。语义判断
（谁是这个人的姓、名、化名）归抽取 LLM；本模块只做确定性转换与闭合校验：
键白名单、引用闭合、模板渲染等式、字面必须出现在源文中。

【CS-S 1c 的三件事（S2-10）】
  • C（构词派生）：wire 不再携带 `surface_forms`——每个 surface 的构词模板
    由 `derive_surface_forms` 按 family／given／aliases 字面对源部件做**占位符
    最大替换**派生（最长字面优先，逐位贪心）；无身份字面＝纯称谓 None；
    同位同长多解＝歧义，模型只在 `form_overrides` 写例外（今日两本 51/51
    逐条一致，I-20/I-21）。
  • B（候选先行）：`name_variant_candidates` 由代码生成变体候选——锚＝派生
    模板非空且 ≥3 字的登记 surface，候选＝源文里同首字、编辑距离 1（替换／
    插入／缺字）、未登记、非任一登记 surface 子串的字面；无候选＝零调用。
    判定走节点内新 owner `name_variants` 的窄调用（wire
    `stage2-name-variants-v1`，逐条 verdict，每候选恰答一次）。
  • A（守卫）：names 不得增删或拆合人物（people 键集合闸）；same 只在证据
    段内局部替换；已登记 surface 不得作异写；未决阻断发布；构词多解未决。

【未登记姓氏构词探针（只报告，不阻断，不猜）】
多字姓（复姓）严格：源文中任何未被登记 surface／family form 覆盖的完整姓字面
出现都列为条目；单字姓只列上下文供人工复核。探针**永不**改写 wire 或拒绝产物
——漏标构词是 Stage2 语义面的事，compiler 不补猜（I-20/I-21）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, NamedTuple

from contracts.schema.source import (
    CharactersCore, NameStructure, render_form,
)
from contracts.schema.observation import (
    STAGE2_NAME_VARIANTS_WIRE_PROTOCOL, STAGE2_NAMES_WIRE_PROTOCOL,
)
from novel.stages.common import group_located_errors
from novel.stages.stage2.resolver import EvidenceResolver

_PEOPLE_KEYS = frozenset({'family_id', 'given', 'aliases', 'form_overrides'})
_FAMILY_KEYS = frozenset({'literal', 'forms'})
#: wire 允许对象内冗余自报名（与 dict 键一致时归一，不一致报错）
_FAMILY_OPTIONAL = frozenset({'family_id'})


#: names 请求的输入版本（进请求身份）。v2（2026-10-04，RC-26 追加）：证据从以段号
#: 为键的对象改为按阅读序排列的数组——请求 JSON 按键排序，段号只补齐两位，一章
#: 超过 99 段时 `e03_100` 会排到 `e03_11` 之前，模型看到的原文顺序被打乱。
NAMES_INPUT_VERSION = 'stage2-names-input-v2'


def names_evidence_view(evidence) -> list[dict[str, Any]]:
    """证据闭集 → names 输入的阅读序数组（纯投影，S2-4；不改输入）。

    每项 ``{segment, chapter, text[, pre_chapter]}``：数组保序不受键排序影响；
    项内键名与 basis 的 ``segment`` 同名，模型照抄所选段号即可。
    """
    view: list[dict[str, Any]] = []
    for ref, segment in evidence.items():
        item: dict[str, Any] = {'segment': ref, 'chapter': segment['chapter'],
                                'text': segment['text']}
        if segment.get('pre_chapter'):
            item['pre_chapter'] = True
        view.append(item)
    return view


class ProbeFinding(NamedTuple):
    """一条未登记构词探针发现：只报告。"""

    literal: str
    context: str
    strict: bool


def compile_name_structure(
    wire: dict[str, Any], characters: CharactersCore, source_text: str,
    *, evidence=None,
) -> tuple[NameStructure | None, list[str]]:
    """names wire（{'families': ..., 'people': ...}）→ NameStructure。

    全部错误聚合返回；wire 原样进、不修改（I-24）。
    """
    structure, located = _compile_name_structure_located(
        wire, characters, source_text, evidence=evidence)
    return structure, [message for _path, message in located]


def names_patch_locate(
    wire: Any, errors: list, characters: CharactersCore, source_text: str,
    *, evidence=None,
) -> list[tuple[tuple[str | int, ...], list]] | None:
    """CS-H H4（RC-57 覆盖）：names 条目定位（`ask_json` 的 `patch` 协议）。

    与 `compile_name_structure` 同一条管道（不新增判据）：家族条目错误映射到
    `families.{fid}`、人物条目到 `people.{slug}`（两级路径互不嵌套，分组见
    `common.group_located_errors`）。wire 级问题（协议字面量／覆盖缺失＝需增删
    条目／构造期复检）或 `errors` 与复跑定位集不完全一致时返回 None——本轮退整份
    返修，不静默漏项。
    """
    if not isinstance(wire, dict) or not errors:
        return None
    _structure, located = _compile_name_structure_located(
        wire, characters, source_text, evidence=evidence)
    return group_located_errors(
        errors, [((path,) if path is not None else None, message)
                 for path, message in located])


# ───────────────────────── C：构词模板机械派生（CS-S 1c） ─────────────────────────

def derive_surface_forms(
    surfaces, parts: dict[str, str],
) -> tuple[str | None, bool]:
    """一个 surface 的构词模板：占位符最大替换（最长身份字面优先，逐位贪心）。

    `parts` 是变量名→身份字面（family／given／alias:<id>）。返回 (模板或
    None, 是否歧义)：无任何身份字面 → (None, False)＝纯称谓；同位同长的
    最大字面属于多个变量 → 歧义（须 `form_overrides` 例外，S2-10 的
    「构词多解未决」）。最长优先使「沈知言」在 family=沈／given=知言 时派生
    为 {given}，不误拆成 {family}言。
    """
    if not parts:
        return (None, False)
    ordered = sorted(parts.items(), key=lambda kv: -len(kv[1]))
    out: list[str] = []
    ambiguous = False
    i = 0
    while i < len(surfaces):
        best_len = 0
        best_vars: list[str] = []
        for var, literal in ordered:
            if literal and surfaces.startswith(literal, i):
                if len(literal) > best_len:
                    best_len, best_vars = len(literal), [var]
                elif len(literal) == best_len and var not in best_vars:
                    best_vars.append(var)
        if best_len == 0:
            out.append(surfaces[i])
            i += 1
            continue
        if len(best_vars) > 1:
            ambiguous = True
        var = best_vars[0]
        out.append('{' + var + '}')
        i += best_len
    template = ''.join(out)
    if '{' not in template:
        return (None, False)
    return (template, ambiguous)


# ───────────────────────── B：变体候选（代码给出，无候选＝零调用） ─────────────────────────

@dataclass(frozen=True)
class NameVariantCandidate:
    """一条变体候选：字面、生成它的锚面（含人物 slug）与出现段。

    召回范围如实登记（S2-10）：只收「≥3 字含姓名部件的登记 surface 的同首字
    编辑距离 1 邻域」这一确定性可判的类——锚取全部登记 surface 会把
    「太子妃→太子的」一类称谓构词当噪声收进（夫君不行实测 16 条）。
    """

    literal: str
    char_id: str
    anchor: str
    segments: tuple[str, ...]


def _edit_distance_one(candidate: str, anchor: str) -> bool:
    """同首字、编辑距离恰为 1（替换／插入／缺字）；首字符必须相同。"""
    if not candidate or candidate[0] != anchor[0]:
        return False
    m, n = len(candidate), len(anchor)
    if abs(m - n) > 1:
        return False
    if m == n:
        diff = sum(a != b for a, b in zip(candidate, anchor))
        return diff == 1
    if m > n:  # 插入：candidate 比 anchor 多一字
        i = 0
        while i < n and candidate[i] == anchor[i]:
            i += 1
        return candidate[i + 1:] == anchor[i:]
    # 缺字：candidate 比 anchor 少一字
    i = 0
    while i < m and candidate[i] == anchor[i]:
        i += 1
    return candidate[i:] == anchor[i + 1:]


def name_variant_candidates(
    structure: NameStructure, characters: CharactersCore,
    evidence: dict,
) -> tuple[NameVariantCandidate, ...]:
    """代码生成变体候选（纯函数；S2-10 的 B）。

    锚＝该人物派生模板非空且 ≥3 字的登记 surface；候选＝证据段文本里与锚
    同首字、编辑距离 1 的字面，且未登记（不等于任何登记 surface／家族级
    form 字面）、与任何登记 surface 互不为子串——变体不会完整包含正确字面
    （插入邻域「anchor＋后继字」因此天然滤除，标点跟随同滤）。同字面多锚
    时保留生成锚的首个（char_id 随锚走，语义判断归窄调用）。
    """
    registered = {
        s.surface for entry in characters.characters.values() for s in entry.surfaces
    } | {form.literal for family in structure.families.values()
         for form in family.forms}
    by_literal: dict[str, NameVariantCandidate] = {}
    for slug, entry in characters.characters.items():
        person = structure.people.get(slug)
        if person is None:
            continue
        for window in entry.surfaces:
            surface = window.surface
            form = person.surface_forms.get(surface)
            if form is None or len(surface) < 3:
                continue
            for ref, segment in evidence.items():
                text = segment.get('text') or ''
                start = text.find(surface[0])
                while start >= 0:
                    for length in (len(surface) - 1, len(surface), len(surface) + 1):
                        cand = text[start:start + length]
                        if (len(cand) == length and cand != surface
                                and _edit_distance_one(cand, surface)
                                and cand not in registered
                                and not any(cand in s or s in cand
                                            for s in registered)):
                            existing = by_literal.get(cand)
                            if existing is None:
                                by_literal[cand] = NameVariantCandidate(
                                    literal=cand, char_id=slug, anchor=surface,
                                    segments=(ref,))
                            elif ref not in existing.segments:
                                by_literal[cand] = NameVariantCandidate(
                                    literal=cand, char_id=existing.char_id,
                                    anchor=existing.anchor,
                                    segments=(*existing.segments, ref))
                    start = text.find(surface[0], start + 1)
    return tuple(by_literal[literal] for literal in sorted(by_literal))


# ───────────────────────── 结构 wire v6 的编译 ─────────────────────────

def _compile_name_structure_located(
    wire: dict[str, Any], characters: CharactersCore, source_text: str,
    *, evidence=None,
) -> tuple[NameStructure | None, list[tuple[tuple | None, str]]]:
    """`compile_name_structure` 的定位视图（CS-H H4；None＝wire 级，退整份）。

    CS-S 1c（v6）：wire 只有 families／people（部件＋form_overrides 例外）；
    surface_forms 由 `derive_surface_forms` 派生；同一性判定不在此面（窄调用
    归 `compile_name_variants`）。
    """
    issues: list[tuple[tuple | None, str]] = []

    def fail(path: tuple | None, message: str) -> None:
        issues.append((path, message))

    errors: list[str] = []
    if not isinstance(wire, dict):
        return None, [(None, 'names wire必须是对象')]
    if wire.get('protocol_version') != STAGE2_NAMES_WIRE_PROTOCOL:
        return None, [(None, f'names必须使用{STAGE2_NAMES_WIRE_PROTOCOL}；'
                       '旧回答（含 surface_forms／resolutions 的 v5 及更早）按版本拒收，'
                       '不做推断采用')]
    if set(wire) - {'protocol_version', 'families', 'people'}:
        fail(None, 'names wire 携带未知字段')
        errors.append('names wire 携带未知字段')
    raw_families = wire.get('families') or {}
    raw_people = wire.get('people') or {}
    # 早退分支只补报自己的问题：此前的「未知字段」已经记入 issues，不再重报。
    if not isinstance(raw_families, dict) or not raw_families:
        fail(None, 'families 缺失或为空')
        return None, issues
    if not isinstance(raw_people, dict):
        fail(None, 'people 必须是对象')
        return None, issues
    slugs = set(characters.characters)
    unknown_people = set(raw_people) - slugs
    if unknown_people:
        # A（S2-10）：names 不得增删或拆合人物——键集合闸。
        fail(None, f'people 引用了 Characters 之外的人物：{sorted(unknown_people)}')
        errors.append(f'people 引用了 Characters 之外的人物：{sorted(unknown_people)}')
    missing_people = slugs - set(raw_people)
    if missing_people:
        fail(None, f'people 缺少人物条目：{sorted(missing_people)}')
        errors.append(f'people 缺少人物条目：{sorted(missing_people)}')

    # 家族：键白名单、字面非空、源文出现、form 只用 {family}。
    for family_id, raw in raw_families.items():
        path = ('families', family_id)
        where = f'家族 {family_id}'
        if not isinstance(raw, dict) or set(raw) - _FAMILY_KEYS - _FAMILY_OPTIONAL:
            fail(path, f'{where} 携带未知字段或形状非法')
            errors.append(f'{where} 携带未知字段或形状非法')
            continue
        if 'family_id' in raw and raw['family_id'] != family_id:
            fail(path, f'{where} 对象内 family_id 与键不一致：{raw["family_id"]!r}')
            errors.append(f'{where} 对象内 family_id 与键不一致：{raw["family_id"]!r}')
            continue
        literal = raw.get('literal')
        if not isinstance(literal, str) or not literal:
            fail(path, f'{where} 的 literal 缺失或为空')
            errors.append(f'{where} 的 literal 缺失或为空')
            continue
        if literal not in source_text:
            fail(path, f'{where} 的字面 {literal!r} 未在源文中出现')
            errors.append(f'{where} 的字面 {literal!r} 未在源文中出现')
        for form in raw.get('forms') or []:
            if not isinstance(form, dict) or set(form) - {'literal', 'form', 'source_refs'}:
                fail(path, f'{where} 称谓条目形状非法')
                errors.append(f'{where} 称谓条目形状非法')
                continue
            try:
                rendered = render_form(form['form'], family=literal, given=None, aliases={})
            except ValueError as exc:
                fail(path, f'{where} 称谓 {form.get("literal")!r}：{exc}')
                errors.append(f'{where} 称谓 {form.get("literal")!r}：{exc}')
                continue
            if rendered != form.get('literal'):
                fail(path, f'{where} 称谓 {form.get("literal")!r} 与构词渲染不一致')
                errors.append(f'{where} 称谓 {form.get("literal")!r} 与构词渲染不一致')
            if form.get('literal') and form['literal'] not in source_text:
                fail(path, f'{where} 称谓字面 {form["literal"]!r} 未在源文中出现')
                errors.append(f'{where} 称谓字面 {form["literal"]!r} 未在源文中出现')

    # 人物：键白名单、引用闭合、部件字面源文出现；构词模板派生（C）。
    derived_people: dict[str, dict[str, Any]] = {}
    for slug, raw in raw_people.items():
        if slug not in slugs:
            continue
        path = ('people', slug)
        where = f'人物 {slug}'
        if not isinstance(raw, dict) or set(raw) - _PEOPLE_KEYS:
            fail(path, f'{where} 携带未知字段或形状非法（v6 无 surface_forms——'
                       '构词模板由程序派生）')
            errors.append(f'{where} 携带未知字段或形状非法')
            continue
        aliases_raw = raw.get('aliases') or []
        aliases: dict[str, str] = {}
        for alias in aliases_raw:
            if not isinstance(alias, dict) or set(alias) - {'alias_id', 'literal'}:
                fail(path, f'{where} 化名条目形状非法')
                errors.append(f'{where} 化名条目形状非法')
                continue
            if alias['alias_id'] in aliases:
                fail(path, f'{where} 化名 id 重复：{alias["alias_id"]}')
                errors.append(f'{where} 化名 id 重复：{alias["alias_id"]}')
            aliases[alias['alias_id']] = alias.get('literal') or ''
            if alias.get('literal') and alias['literal'] not in source_text:
                fail(path, f'{where} 化名字面 {alias["literal"]!r} 未在源文中出现')
                errors.append(f'{where} 化名字面 {alias["literal"]!r} 未在源文中出现')
        family_id = raw.get('family_id')
        family_literal = None
        if family_id is not None:
            entry = raw_families.get(family_id)
            if not isinstance(entry, dict):
                fail(path, f'{where} 的 family_id {family_id!r} 未登记家族')
                errors.append(f'{where} 的 family_id {family_id!r} 未登记家族')
            else:
                family_literal = entry.get('literal')
        given = raw.get('given')
        if given is not None and not isinstance(given, str):
            fail(path, f'{where} 的 given 必须是字符串或 null')
            errors.append(f'{where} 的 given 必须是字符串或 null')
            given = None
        if given and given not in source_text:
            fail(path, f'{where} 的名字面 {given!r} 未在源文中出现')
            errors.append(f'{where} 的名字面 {given!r} 未在源文中出现')
        parts: dict[str, str] = {}
        if family_literal:
            parts['family'] = family_literal
        if given:
            parts['given'] = given
        for alias_id, alias_literal in aliases.items():
            if alias_literal:
                parts[f'alias:{alias_id}'] = alias_literal
        overrides = raw.get('form_overrides') or {}
        if not isinstance(overrides, dict):
            fail(path, f'{where} 的 form_overrides 必须是对象')
            errors.append(f'{where} 的 form_overrides 必须是对象')
            overrides = {}
        surfaces = {s.surface for s in characters.characters[slug].surfaces}
        unknown_override = set(overrides) - surfaces
        if unknown_override:
            fail(path, f'{where} 的 form_overrides 指向未登记 surface：'
                       f'{sorted(unknown_override)}')
            errors.append(f'{where} 的 form_overrides 指向未登记 surface：'
                          f'{sorted(unknown_override)}')
        surface_forms: dict[str, str | None] = {}
        for surface in sorted(surfaces):
            if surface in overrides:
                surface_forms[surface] = overrides[surface]
                continue
            template, ambiguous = derive_surface_forms(surface, parts)
            if ambiguous:
                # A（S2-10）：构词多解未决——同位同长的最大字面属于多个部件，
                # 须模型在 form_overrides 写例外。
                fail(path, f'{where} 的 surface {surface!r} 构词多解'
                           f'（部件字面 {parts}），须在 form_overrides 给出例外')
                errors.append(f'{where} 的 surface {surface!r} 构词多解'
                              f'（部件字面 {parts}），须在 form_overrides 给出例外')
            surface_forms[surface] = template
        # 渲染等式与防漏标（派生模板按构造满足；例外 override 在此复检）。
        for surface, form in surface_forms.items():
            if form is None:
                for literal in (family_literal, given, *aliases.values()):
                    if literal and literal in surface:
                        message = (f'{where} 的纯称谓 {surface!r} 含本人姓名字面'
                                   f' {literal!r}（防漏标：应登记构词）')
                        fail(path, message)
                        errors.append(message)
                continue
            try:
                rendered = render_form(form, family=family_literal, given=given,
                                       aliases=aliases)
            except ValueError as exc:
                fail(path, f'{where} 的 surface {surface!r}：{exc}')
                errors.append(f'{where} 的 surface {surface!r}：{exc}')
                continue
            if rendered != surface:
                message = (f'{where} 的 surface {surface!r} 与构词渲染不一致：'
                           f'{form!r} 渲染为 {rendered!r}')
                fail(path, message)
                errors.append(message)
        derived_people[slug] = {
            'family_id': family_id, 'given': given,
            'aliases': aliases_raw, 'surface_forms': surface_forms}
    if errors:
        return None, issues
    # schema 构造期复检全部跨段规则（渲染等式、覆盖、闭合）；先剥家族对象内
    # 与键一致的冗余 family_id（wire 允许携带，canonical 不收）。同一性判定
    # 不在结构面（CS-S 1c）：resolutions 由窄调用经 run_names 合入。
    normalized = {
        'families': {fid: {k: v for k, v in raw.items() if k != 'family_id'}
                     for fid, raw in raw_families.items()},
        'people': derived_people,
        'resolutions': [],
    }
    from pydantic import ValidationError
    try:
        return NameStructure.model_validate(normalized), issues
    except ValidationError as exc:
        return None, issues + [(None, f'name_structure 构造失败：{exc}')]


# ───────────────────────── 变体窄调用的编译（CS-S 1c） ─────────────────────────

def name_variants_prompt(candidates, evidence) -> str:
    """`name_variants` 窄调用 prompt（候选闭集＋每候选的出现段与文本）。"""
    from novel.stages.common import NAME_VARIANTS_RULES
    lines = ['【候选清单】（每条恰答一次；判定与证据只能从中选）']
    for index, cand in enumerate(candidates, 1):
        lines.append(f'{index}. 字面「{cand.literal}」（近似锚：{cand.anchor}，'
                     f'人物 {cand.char_id}）出现段：')
        for ref in cand.segments:
            text = (evidence.get(ref) or {}).get('text') or ''
            lines.append(f'   [{ref}] {text}')
    body = '\n'.join(lines)
    return NAME_VARIANTS_RULES.format(candidates=body,
                                      protocol=STAGE2_NAME_VARIANTS_WIRE_PROTOCOL)


def compile_name_variants(
    wire: Any, candidates, *, evidence=None,
    characters: CharactersCore | None = None,
) -> tuple[list[dict[str, Any]] | None, list[tuple[tuple | None, str]]]:
    """变体 wire → canonical `SourceNameResolution` dict 列表＋条目级 issue。

    闭集核对：每候选恰答一次（漏答／多答＝机械缺陷，按条目窄修）；
    `same` 指向已登记人物与称谓；evidence_refs 只给段 id 且限于该候选的
    出现段（引文＝段全文由 compiler 派生，2026-10-07 裁决）。返回的 dict
    形如 canonical `SourceNameResolution`（basis 已派生引文）。
    """
    from pydantic import ValidationError
    from contracts.schema.observation import NameVariantsWire
    issues: list[tuple[tuple | None, str]] = []

    def fail(path, message):
        issues.append((path, message))

    by_literal = {cand.literal: cand for cand in candidates}
    if not isinstance(wire, dict) or wire.get('protocol_version') != (
            STAGE2_NAME_VARIANTS_WIRE_PROTOCOL):
        return None, [(None, f'name_variants 必须使用{STAGE2_NAME_VARIANTS_WIRE_PROTOCOL}')]
    try:
        model = NameVariantsWire.model_validate(wire)
    except ValidationError as exc:
        from novel.stages.common import wire_shape_issues
        located = wire_shape_issues(exc, kind='names')
        if not located or any(paths is None for paths, _message in located):
            return None, [(None, f'变体 wire 协议校验失败：{exc}')]
        return None, [(paths[0], f'变体 {message}') for paths, message in located]
    resolver = EvidenceResolver(evidence or {})
    answered: dict[str, int] = {}
    resolutions: list[dict[str, Any]] = []
    errors = 0
    for index, verdict in enumerate(model.variants):
        path = ('variants', index)
        cand = by_literal.get(verdict.literal)
        if cand is None:
            fail(path, f'变体[{index + 1}] 字面「{verdict.literal}」不在候选清单内'
                       '（多答＝机械缺陷；候选由程序给出，不新增）')
            errors += 1
            continue
        if verdict.literal in answered:
            fail(path, f'变体[{index + 1}] 字面「{verdict.literal}」重复作答'
                       f'（首次见第 {answered[verdict.literal] + 1} 条）')
            errors += 1
            continue
        answered[verdict.literal] = index
        outside = [ref for ref in verdict.evidence_refs
                   if ref not in cand.segments]
        if outside:
            fail(path, f'变体[{index + 1}]「{verdict.literal}」的 evidence_refs 引用了'
                       f'该候选出现段之外的段 {outside}（出现段＝{list(cand.segments)}）')
            errors += 1
            continue
        if verdict.verdict == 'same' and characters is not None:
            entry = characters.characters.get(verdict.char_id)
            if entry is None or verdict.surface not in (
                    {s.surface for s in entry.surfaces}):
                fail(path, f'变体[{index + 1}]「{verdict.literal}」的 same 指向'
                           '未登记人物或称谓')
                errors += 1
                continue
        basis = []
        bad = False
        for ref in verdict.evidence_refs:
            quote, issue = resolver.basis_quote(ref, (ref,))
            if issue is not None:
                fail(path, f'变体[{index + 1}]「{verdict.literal}」：{issue.message}')
                errors += 1
                bad = True
                break
            basis.append({'segment': ref, 'quote': quote})
        if bad:
            continue
        resolutions.append({
            'literal': verdict.literal, 'status': verdict.verdict,
            'char_id': verdict.char_id, 'surface': verdict.surface,
            'basis': basis, 'reason': verdict.reason})
    for literal in sorted(set(by_literal) - set(answered)):
        fail(None, f'候选「{literal}」漏答（每候选恰答一次——漏答＝机械缺陷）')
        errors += 1
    if errors:
        return None, issues
    return resolutions, issues


def validate_name_resolutions(
    structure: NameStructure, characters: CharactersCore, *, evidence,
) -> list[str]:
    """只验证语义声明的引用与原句证明，不判断两个姓名是否同一人物。

    CS-S 1c：`not_name` 与 unresolved 同形校验（不暗含映射、不参与归一），
    但**不阻断发布**——阻断只由调用方对 unresolved 施加（未决＝需要人／
    窄修，not_name＝假设已否决的完整性证明）。
    """
    from novel.stages.stage2.resolver import EvidenceResolver, verify_bases
    errors = []
    occupied = {}
    known_surfaces = {s.surface for c in characters.characters.values() for s in c.surfaces}
    resolver = EvidenceResolver(evidence or {})
    for index, item in enumerate(structure.resolutions):
        label = f'名称同一性[{index}]'
        if not evidence:
            errors.append(f'{label}缺少逐句证据，不能接受')
            continue
        errors.extend(f'{where}：{issue.code} {issue.message}' for where, issue in verify_bases(
            resolver, [(label, b.segment, b.quote, tuple(q.segment for q in item.basis)) for b in item.basis]))
        if not any(item.literal in b.quote for b in item.basis):
            errors.append(f'{label}原字面未出现在所引原句中')
        if item.status == 'same':
            if item.literal in known_surfaces:
                errors.append(f'{label}原字面已在人物表登记，不能作为异写覆盖已有身份')
            char = characters.characters.get(item.char_id)
            if char is None or item.surface not in {s.surface for s in char.surfaces}:
                errors.append(f'{label}指向未登记人物或称谓')
            for b in item.basis:
                if item.literal not in b.quote:
                    continue
                key = (b.segment, item.literal)
                decision = (item.char_id, item.surface)
                if key in occupied and occupied[key] != decision:
                    errors.append(f'{label}同一局部字面存在冲突指向')
                occupied[key] = decision
        else:
            for b in item.basis:
                if item.literal in b.quote:
                    key = (b.segment, item.literal)
                    if key in occupied:
                        errors.append(f'{label}同一局部字面已有另一判定占据')
                    occupied[key] = (None, None)
    return errors


def normalize_name_observations(book, chapters, structure):
    """按显式same声明及其证据局部归一语义文本；观察、原文、引文全部保留。"""
    from novel.stages.common import apply_replacements
    from contracts.schema.observation import BookObservation, ChapterObservation
    semantic_fields = {'event', 'statement', 'value', 'question', 'resolution'}

    def walk(value, refs=()):
        if isinstance(value, list):
            return [walk(item, refs) for item in value]
        if not isinstance(value, dict):
            return value
        local = tuple(b['segment'] for b in value.get('basis', ())) or tuple(value.get('source_refs', refs))
        mappings = {}
        for item in structure.resolutions:
            if item.status == 'same' and set(local) & {b.segment for b in item.basis if item.literal in b.quote}:
                if item.literal in mappings and mappings[item.literal] != item.surface:
                    raise ValueError('语义字段横跨相互冲突的名称对应，不能归一')
                mappings[item.literal] = item.surface
        result = {}
        for key, item in value.items():
            if key in {'basis', 'characters', 'source_refs'}:
                result[key] = item  # 不触碰原句证据与人物候选的字面登记
            elif key in semantic_fields and isinstance(item, str):
                result[key] = apply_replacements(item, mappings)[0] if mappings else item
            else:
                result[key] = walk(item, local)
        return result

    return (BookObservation.model_validate(walk(book.model_dump(mode='json'))),
            {n: ChapterObservation.model_validate(walk(ch.model_dump(mode='json')))
             for n, ch in chapters.items()})


def merge_name_structure(
    core: CharactersCore, structure: NameStructure,
) -> tuple[Any | None, list[str]]:
    """core + name_structure → 完整 CharactersArtifactV5（发布前唯一合并点）。"""
    from pydantic import ValidationError
    from contracts.schema.source import CharactersArtifactV5
    payload = core.model_dump()
    payload['name_structure'] = structure.model_dump()
    try:
        return CharactersArtifactV5.model_validate(payload), []
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
