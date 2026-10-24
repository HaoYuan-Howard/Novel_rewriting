#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""name_structure —— Stage2 names 窄调用的确定性 compile（现役 wire v7）。

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
    插入／缺字）、未登记、与任一 ≥3 字登记 surface 互不为子串、只含字母数字类
    字符的字面（多锚全部保留）；无候选＝零调用。
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
from typing import Any, Mapping, NamedTuple

from contracts.schema.source import (
    CharactersCore, NameStructure, SurfaceWindow, render_form,
)
from contracts.schema.observation import (
    STAGE2_NAME_VARIANTS_WIRE_PROTOCOL, STAGE2_NAMES_PROTOCOL, STAGE2_NAMES_WIRE_PROTOCOL,
    AdditionalNameSurfaceWire, NamesObservation, SurfaceMention,
)
from novel.stages.common import group_located_errors
from novel.stages.stage2.resolver import EvidenceResolver

_PEOPLE_KEYS = frozenset({'family_id', 'given', 'aliases', 'form_overrides', 'additional_surfaces'})
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


def extend_name_surfaces(
    characters: CharactersCore, names: NamesObservation, *, evidence,
) -> tuple[CharactersCore | None, list[str]]:
    """唯一补面投影：Names 的有证据词面声明并入 Book 既有人物，不改 Book。

    v4 只读兼容明确没有观察过补充面；空映射不是称谓完整性证明。v5 则逐项
    复验证据、人物闭集和既有面冲突，窗口复用 Resolver 派生，供所有真实消费者。
    """
    if names.protocol_version == 'stage2-names-v4':
        return (characters, []) if not names.additional_surfaces else (
            None, ['names v4 不得暗含补充称谓声明'])
    if names.protocol_version != STAGE2_NAMES_PROTOCOL:
        return None, [f'不支持的 names 观察协议：{names.protocol_version}']
    extended, located = _extend_surface_mentions(
        characters, names.additional_surfaces, evidence=evidence)
    return extended, [message for _path, message in located]


def _extend_surface_mentions(
    characters: CharactersCore, additions: Mapping[str, tuple[SurfaceMention, ...]], *, evidence,
) -> tuple[CharactersCore | None, list[tuple[tuple | None, str]]]:
    """证据寻址与不可变 core 投影；不判断自然语言中的人物同一性。"""
    resolver = EvidenceResolver(evidence or {})
    issues: list[tuple[tuple | None, str]] = []
    people = dict(characters.characters)
    for slug, mentions in additions.items():
        path = ('people', slug)
        if slug not in people:
            issues.append((path, f'补充称谓引用人物闭集之外的 {slug!r}；不得增删或拆合人物'))
            continue
        entry = people[slug]
        seen = {surface.surface for surface in entry.surfaces}
        windows = list(entry.surfaces)
        for mention in mentions:
            literal, refs = mention.surface, tuple(mention.source_refs)
            if literal in seen:
                issues.append((path, f'{slug} 的补充称谓 {literal!r} 重复或已在 Book 登记'))
                continue
            seen.add(literal)
            invalid = resolver.check_closed_set(refs)
            if len(set(refs)) != len(refs):
                issues.append((path, f'{slug} 补充称谓 {literal!r} 的 evidence_refs 重复'))
            for issue in invalid:
                issues.append((path, f'{slug} 补充称谓 {literal!r}：{issue.message}'))
            if not isinstance(evidence, Mapping):
                issues.append((path, f'{slug} 补充称谓 {literal!r} 缺少可逐字核对的源证据'))
                continue
            missing = [ref for ref in refs if ref in evidence
                       and literal not in (evidence[ref].get('text') or '')]
            if missing:
                issues.append((path, f'{slug} 补充称谓 {literal!r} 在所引段 {missing} 未逐字出现'))
            if invalid or missing:
                continue
            chapter, issue = resolver.surface_chapter(literal, refs)
            if issue is not None:
                issues.append((path, f'{slug} 补充称谓 {literal!r}：{issue.message}'))
                continue
            assert chapter is not None
            windows.append(SurfaceWindow(surface=literal, from_chapter=chapter,
                                         until_chapter=None, source_refs=refs))
        people[slug] = entry.model_copy(update={'surfaces': tuple(windows)})
    return (None if issues else characters.model_copy(update={'characters': people})), issues


def _wire_surface_additions(
    raw_people: dict[str, Any], characters: CharactersCore, *, evidence,
) -> tuple[dict[str, tuple[SurfaceMention, ...]], CharactersCore | None,
           list[tuple[tuple | None, str]]]:
    """新 wire 的补充声明只在这里转换成 canonical SurfaceMention。"""
    from pydantic import ValidationError
    additions: dict[str, tuple[SurfaceMention, ...]] = {}
    issues: list[tuple[tuple | None, str]] = []
    for slug, raw in raw_people.items():
        if not isinstance(raw, dict):
            continue  # 人物条目的形状由主结构判据报告。
        path = ('people', slug)
        if 'additional_surfaces' not in raw or not isinstance(raw['additional_surfaces'], list):
            issues.append((path, f'{slug} 必须显式给 additional_surfaces 数组（没有则给 []）'))
            continue
        mentions = []
        for value in raw['additional_surfaces']:
            try:
                declaration = AdditionalNameSurfaceWire.model_validate(value)
            except ValidationError as exc:
                issues.append((path, f'{slug} 补充称谓形状非法：{exc}'))
                continue
            mentions.append(SurfaceMention(surface=declaration.surface,
                                           source_refs=declaration.evidence_refs))
        if mentions:
            additions[slug] = tuple(mentions)
    extended, surface_issues = _extend_surface_mentions(characters, additions, evidence=evidence)
    return additions, extended, issues + surface_issues


def compile_names_observation(
    wire: dict[str, Any], characters: CharactersCore, source_text: str, *, evidence,
) -> tuple[NamesObservation | None, CharactersCore | None, list[str]]:
    """names 唯一组装入口：结构与补充证据同次验证，返回变体判定所用的同源 core。"""
    structure, errors = compile_name_structure(wire, characters, source_text, evidence=evidence)
    if errors:
        return None, None, errors
    additions, extended, located = _wire_surface_additions(wire['people'], characters, evidence=evidence)
    if located:
        return None, None, [message for _path, message in located]
    observation = NamesObservation(protocol_version=STAGE2_NAMES_PROTOCOL,
                                   structure=structure, additional_surfaces=additions)
    return observation, extended, []


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
    「构词多解未决」）。最长优先使「沈知言」在 family=沈／given=沈知言（名字段
    登记的是含姓全名）时派生为 {given}，不误拆成 {family}知言；family=沈／
    given=知言 时则逐位派生为 {family}{given}。
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
    #: 同一字面也落在别的人物锚的编辑距离 1 邻域时的其余 (char_id, anchor)，
    #: 按生成序；首个锚仍是 `char_id`／`anchor`。不静默归给先到的人物：全部
    #: 锚进窄调用请求，`same` 只能指向其中任一锚的人物（compile 同口径执行）。
    other_anchors: tuple[tuple[str, str], ...] = ()

    @property
    def anchors(self) -> tuple[tuple[str, str], ...]:
        return ((self.char_id, self.anchor), *self.other_anchors)


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
    form 字面）、与任何 ≥3 字的登记 surface 互不为子串——变体不会完整包含
    正确字面（插入邻域「anchor＋后继字」因此天然滤除）。子串过滤只对 ≥3 字
    的登记面生效：1～2 字的称谓（「远」「娘」）若也参与，会把含它的真变体
    （「裴知远」）整条吞掉。候选字面只收字母数字类字符：带标点或空白的邻域
    切片（「裴书，」）不是名字，不进请求。同字面落在多个人物锚的邻域时全部
    保留（不静默归给先到的人物），语义判断归窄调用。
    """
    registered = {
        s.surface for entry in characters.characters.values() for s in entry.surfaces
    } | {form.literal for family in structure.families.values()
         for form in family.forms}
    long_registered = tuple(sorted(s for s in registered if len(s) >= 3))
    #: 候选字面 → ([(char_id, 锚)…], [出现段…])，均按生成序、不重复
    found: dict[str, tuple[list[tuple[str, str]], list[str]]] = {}
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
                                and cand.isalnum()
                                and _edit_distance_one(cand, surface)
                                and cand not in registered
                                and not any(cand in s or s in cand
                                            for s in long_registered)):
                            anchors, segments = found.setdefault(cand, ([], []))
                            if (slug, surface) not in anchors:
                                anchors.append((slug, surface))
                            if ref not in segments:
                                segments.append(ref)
                    start = text.find(surface[0], start + 1)
    return tuple(
        NameVariantCandidate(
            literal=literal, char_id=anchors[0][0], anchor=anchors[0][1],
            segments=tuple(segments), other_anchors=tuple(anchors[1:]))
        for literal, (anchors, segments) in sorted(found.items()))


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
                       '旧回答（v6 及更早）按版本拒收，'
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
    _additions, extended, surface_issues = _wire_surface_additions(
        raw_people, characters, evidence=evidence)
    issues.extend(surface_issues)
    errors.extend(message for _path, message in surface_issues)
    if extended is not None:
        characters = extended
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
        forms = raw.get('forms') or []
        if not isinstance(forms, list):
            fail(path, f'{where} 的 forms 必须是数组')
            errors.append(f'{where} 的 forms 必须是数组')
            continue
        for form in forms:
            if (not isinstance(form, dict) or set(form) - {'literal', 'form', 'source_refs'}
                    or not isinstance(form.get('form'), str)
                    or not isinstance(form.get('literal'), str)
                    or not form['literal']):
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
            fail(path, f'{where} 携带未知字段或形状非法（v7 无 surface_forms——'
                       '构词模板由程序派生）')
            errors.append(f'{where} 携带未知字段或形状非法')
            continue
        aliases_raw = raw.get('aliases') or []
        aliases: dict[str, str] = {}
        if not isinstance(aliases_raw, list):
            fail(path, f'{where} 的 aliases 必须是数组')
            errors.append(f'{where} 的 aliases 必须是数组')
            continue
        for alias in aliases_raw:
            if (not isinstance(alias, dict) or set(alias) - {'alias_id', 'literal'}
                    or not isinstance(alias.get('alias_id'), str) or not alias['alias_id']
                    or not isinstance(alias.get('literal') or '', str)):
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
        if family_id is not None and not isinstance(family_id, str):
            fail(path, f'{where} 的 family_id 必须是字符串或 null')
            errors.append(f'{where} 的 family_id 必须是字符串或 null')
            family_id = None
        if family_id is not None:
            entry = raw_families.get(family_id)
            if not isinstance(entry, dict):
                fail(path, f'{where} 的 family_id {family_id!r} 未登记家族')
                errors.append(f'{where} 的 family_id {family_id!r} 未登记家族')
            else:
                # 家族字面的形状错误已在家族条目报告；不把 list/dict 等坏部件
                # 交给新构词派生器，否则 startswith 会绕过免费形状修复抛 TypeError。
                candidate_literal = entry.get('literal')
                family_literal = candidate_literal if isinstance(candidate_literal, str) else None
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
                override = overrides[surface]
                if override is not None and not isinstance(override, str):
                    message = (f'{where} 的 form_overrides[{surface!r}] 必须是构词模板'
                               '字符串或 null（纯称谓）')
                    fail(path, message)
                    errors.append(message)
                    continue
                if isinstance(override, str) and '{' not in override:
                    # 无占位符的“模板”恒等于 surface、不产生任何替换键，且绕开
                    # 纯称谓的防漏标——等价于纯称谓就必须写 null。
                    message = (f'{where} 的 form_overrides[{surface!r}]={override!r} '
                               '无占位符；纯称谓请写 null')
                    fail(path, message)
                    errors.append(message)
                    continue
                surface_forms[surface] = override
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
            # 防漏标对模板同样成立：占位符之外的字面片段（逐片段，不拼接）不得
            # 再含本人姓名字面——否则改名时这一段不会随占位符被替换。派生模板
            # 按最大替换构造满足；只有 form_overrides 例外会在此被拦。
            for fragment in re.split(r'\{[^{}]*\}', form):
                for literal in (family_literal, given, *aliases.values()):
                    if literal and literal in fragment:
                        message = (f'{where} 的 surface {surface!r} 构词模板 {form!r} '
                                   f'的非占位部分 {fragment!r} 含本人姓名字面 {literal!r}'
                                   '（防漏标：应以占位符表达）')
                        fail(path, message)
                        errors.append(message)
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

_REF_RE = None


def _neighbour_refs(ref: str, evidence) -> tuple[str, ...]:
    """出现段的同章前后各一段（段号 ±1，章内越界即无；e00 引子同法）。

    邻段只是判定上下文（正名复指一类证据常在邻段），不是证据段——
    `evidence_refs` 仍只能从出现段里选（compile 同口径核对）。
    """
    import re as _re
    global _REF_RE
    if _REF_RE is None:
        _REF_RE = _re.compile(r'^e(\d+)_(\d+)$')
    m = _REF_RE.match(ref)
    if m is None:
        return ()
    chapter, segment = int(m.group(1)), int(m.group(2))
    return tuple(f'e{chapter:02d}_{n:02d}' for n in (segment - 1, segment + 1)
                 if f'e{chapter:02d}_{n:02d}' in evidence)


def _literal_count(literal: str, evidence) -> int:
    """字面在全书证据段的子串出现次数（数量级事实，供判定参考）。"""
    return sum((seg.get('text') or '').count(literal)
               for seg in evidence.values())


def _anchor_fact_line(char_id: str, anchor: str, structure, evidence) -> str:
    """一个锚人物的机械事实：锚称谓出现次数＋姓名结构分解。"""
    person = structure.people.get(char_id) if structure is not None else None
    parts = [f'锚称谓「{anchor}」出现 {_literal_count(anchor, evidence)} 次']
    if person is not None and person.family_id:
        family = structure.families.get(person.family_id)
        if family is not None:
            given = f'，名「{person.given}」' if person.given else '（无名）'
            parts.append(f'人物 {char_id} 姓「{family.literal}」{given}')
    return '；'.join(parts)


def _same_family_fact(structure, char_ids) -> str:
    """同姓登记人物清单（书内唯一性事实；多锚候选覆盖全部锚人物）。"""
    if structure is None:
        return ''
    literals = {}
    for slug in char_ids:
        person = structure.people.get(slug)
        if person is None or not person.family_id:
            continue
        family = structure.families.get(person.family_id)
        if family is None:
            continue
        literals.setdefault(family.literal, set()).add(slug)
    lines = []
    for literal in sorted(literals):
        peers = sorted(literals[literal])
        only = '仅 ' + '、'.join(peers) if len(peers) == 1 \
            else '、'.join(peers) + '（不止一人，须凭上下文区分）'
        lines.append(f'书内姓「{literal}」的登记人物：{only}')
    return '；'.join(lines)


def name_variants_prompt(candidates, evidence, structure=None) -> str:
    """`name_variants` 窄调用 prompt。

    材料面（2026-10-08 修复「裴知远」死锁）：每候选给出现段全文＋**同章邻段
    上下文**（正名复指证据常在邻段）＋代码确定性派生的机械事实（字面/锚称谓
    出现次数、锚人物姓名结构、同姓登记人物清单）。事实由代码给出、判定仍归
    模型（S2-10/I-20）；邻段只是上下文，`evidence_refs` 仍限于出现段。
    """
    from novel.stages.common import NAME_VARIANTS_RULES
    lines = ['【候选清单】（每条恰答一次；判定与证据只能从中选）']
    for index, cand in enumerate(candidates, 1):
        anchors = '；'.join(f'{anchor}，人物 {char_id}'
                           for char_id, anchor in cand.anchors)
        lines.append(f'{index}. 字面「{cand.literal}」（近似锚：{anchors}）出现段：')
        for ref in cand.segments:
            text = (evidence.get(ref) or {}).get('text') or ''
            lines.append(f'   [{ref}] {text}')
            neighbours = [n for n in _neighbour_refs(ref, evidence)
                          if n not in cand.segments]
            if neighbours:
                lines.append('   （邻段上下文，非证据段：')
                lines.extend(f'     [{n}] {(evidence.get(n) or {}).get("text") or ""}'
                             for n in neighbours)
                lines.append('   ）')
        facts = [f'字面「{cand.literal}」全书出现 {_literal_count(cand.literal, evidence)} 次']
        facts.extend(_anchor_fact_line(char_id, anchor, structure, evidence)
                     for char_id, anchor in cand.anchors)
        same_family = _same_family_fact(structure, [a for a, _ in cand.anchors])
        if same_family:
            facts.append(same_family)
        lines.append(f'   事实：{"；".join(facts)}。')
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
        if verdict.verdict == 'same' and verdict.char_id not in {
                char_id for char_id, _anchor in cand.anchors}:
            # 候选由代码给出、近似锚就是 same 的合法去向（提示词同口径）：
            # 指向锚之外的人物＝凭空新增同一性，不接受（S2-10）。
            fail(path, f'变体[{index + 1}]「{verdict.literal}」的 same 只能指向近似锚'
                       f'人物 {sorted({c for c, _a in cand.anchors})}，不是 '
                       f'{verdict.char_id!r}')
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

    生成候选必须逐条有且仅有一个判定：不仅在 wire 接受时核对，消费正式 names
    观察时也复验，防止人工编辑删除判定或上游候选变化后漏过未决。候选集合由
    当前人物账、名称结构与源证据重新派生，不增加持久化真相源；人工补充的局部
    判定沿用既有引用与证据校验，不扩大拒收范围。
    CS-S 1c：`not_name` 与 unresolved 同形校验（不暗含映射、不参与归一），
    但**不阻断发布**——阻断只由调用方对 unresolved 施加（未决＝需要人／
    窄修，not_name＝假设已否决的完整性证明）。
    """
    from novel.stages.stage2.resolver import EvidenceResolver, verify_bases
    errors = []
    occupied = {}
    known_surfaces = {s.surface for c in characters.characters.values() for s in c.surfaces}
    resolver = EvidenceResolver(evidence or {})
    candidate_targets: dict[str, set[str]] = {}
    if evidence:
        candidates = name_variant_candidates(structure, characters, evidence)
        counts: dict[str, int] = {}
        for item in structure.resolutions:
            counts[item.literal] = counts.get(item.literal, 0) + 1
        for candidate in candidates:
            candidate_targets[candidate.literal] = {char_id for char_id, _anchor
                                                    in candidate.anchors}
            count = counts.get(candidate.literal, 0)
            if count != 1:
                errors.append(
                    f'名称候选「{candidate.literal}」须恰有一条判定，当前 {count} 条'
                    '（名称候选由当前人物账与源证据派生，不得漏答或重复）')
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
            if (item.literal in candidate_targets
                    and item.char_id not in candidate_targets[item.literal]):
                errors.append(
                    f'{label} same 只能指向候选近似锚人物 '
                    f'{sorted(candidate_targets[item.literal])}，不是 {item.char_id!r}')
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
    """core + name_structure → 完整 CharactersArtifactV7（发布前唯一合并点）。"""
    from pydantic import ValidationError
    from contracts.schema.source import CharactersArtifactV7
    payload = core.model_dump()
    payload['name_structure'] = structure.model_dump()
    try:
        return CharactersArtifactV7.model_validate(payload), []
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
