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
from contracts.schema.observation import STAGE2_NAMES_WIRE_PROTOCOL
from novel.stages.common import group_located_errors
from novel.stages.stage2.resolver import EvidenceResolver

_PEOPLE_KEYS = frozenset({'family_id', 'given', 'aliases', 'surface_forms'})
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


def _compile_name_structure_located(
    wire: dict[str, Any], characters: CharactersCore, source_text: str,
    *, evidence=None,
) -> tuple[NameStructure | None, list[tuple[tuple | None, str]]]:
    """`compile_name_structure` 的定位视图（CS-H H4；None＝wire 级，退整份）。"""
    issues: list[tuple[tuple | None, str]] = []

    def fail(path: tuple | None, message: str) -> None:
        issues.append((path, message))

    errors: list[str] = []
    if not isinstance(wire, dict):
        return None, [(None, 'names wire必须是对象')]
    if wire.get('protocol_version') != STAGE2_NAMES_WIRE_PROTOCOL or 'resolutions' not in wire:
        return None, [(None, f'names必须使用{STAGE2_NAMES_WIRE_PROTOCOL}并显式提供resolutions；旧回答不能推断已核验名称异写')]
    if set(wire) - {'protocol_version', 'families', 'people', 'resolutions'}:
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
        # 删除条目不是替换——定位不了，退整份返修。
        fail(None, f'people 引用了 Characters 之外的人物：{sorted(unknown_people)}')
        errors.append(f'people 引用了 Characters 之外的人物：{sorted(unknown_people)}')
    missing_people = slugs - set(raw_people)
    if missing_people:
        # 补条目不是替换——定位不了，退整份返修。
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

    # 人物：键白名单、引用闭合、surface 覆盖、渲染等式、字面源文出现。
    for slug, raw in raw_people.items():
        if slug not in slugs:
            continue
        path = ('people', slug)
        where = f'人物 {slug}'
        if not isinstance(raw, dict) or set(raw) - _PEOPLE_KEYS:
            fail(path, f'{where} 携带未知字段或形状非法')
            errors.append(f'{where} 携带未知字段或形状非法')
            continue
        surfaces = {s.surface for s in characters.characters[slug].surfaces}
        surface_forms = raw.get('surface_forms')
        if not isinstance(surface_forms, dict) or set(surface_forms) != surfaces:
            fail(path, f'{where} 的 surface_forms 未恰好覆盖全部 surface')
            errors.append(f'{where} 的 surface_forms 未恰好覆盖全部 surface')
            surface_forms = surface_forms if isinstance(surface_forms, dict) else {}
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
        for alias_literal in aliases.values():
            if alias_literal and alias_literal not in source_text:
                fail(path, f'{where} 的化名字面 {alias_literal!r} 未在源文中出现')
                errors.append(f'{where} 的化名字面 {alias_literal!r} 未在源文中出现')
        for surface, form in surface_forms.items():
            if form is None:
                for literal in (family_literal, given, *aliases.values()):
                    if literal and literal in surface:
                        message = (f'{where} 的纯称谓 {surface!r} 含本人姓名字面 {literal!r}'
                                   '（防漏标：应登记构词）')
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
    if errors:
        return None, issues
    # 证据指针派生（2026-10-07 裁决：模型只选段、代码切原文）：resolutions 的
    # evidence_refs 段 id 在证据闭集内核对后按段全文派生 canonical 的 basis
    # 引文——转写文本不进模型输出面，原字面「literal ∈ 引文」的锚定检查保留
    # 且更强（整段可查）。派生失败逐条报 wire 级 issue（resolutions 条目不可
    # 部分替换，退整份）。
    resolver = EvidenceResolver(evidence or {})
    derived_resolutions: list[Any] = []
    for index, item in enumerate(wire.get('resolutions') or ()):
        if not isinstance(item, dict):
            derived_resolutions.append(item)
            continue
        refs = item.get('evidence_refs')
        if not isinstance(refs, list):
            message = (f'名称同一性[{index}]：evidence_refs 必须是段 id 数组'
                       '（引文由程序按段全文切出）')
            fail(None, message)
            errors.append(message)
            derived_resolutions.append(item)
            continue
        derived_basis = []
        for basis in refs:
            if isinstance(basis, str):
                quote, issue = resolver.basis_quote(basis, (basis,))
                if issue is not None:
                    fail(None, f'名称同一性[{index}]：{issue.message}')
                    errors.append(f'名称同一性[{index}]：{issue.message}')
                else:
                    derived_basis.append({'segment': basis, 'quote': quote})
            else:
                # 形状闸：wire 的 evidence_refs 只收段 id——canonical 形（自带
                # 引文字典）不宽松接纳，防模型借字典回到手抄引文（I-34）。
                message = (f'名称同一性[{index}]：evidence_refs 条目只收段 id 字符串'
                           '（引文由程序按段全文切出），不收引文对象')
                fail(None, message)
                errors.append(message)
        derived = dict(item)
        derived['basis'] = derived_basis
        del derived['evidence_refs']
        derived_resolutions.append(derived)
    if errors:
        return None, issues
    # schema 构造期复检全部跨段规则（渲染等式、覆盖、闭合）；先剥家族对象内
    # 与键一致的冗余 family_id（wire 允许携带，canonical 不收）。
    normalized = {
        'families': {fid: {k: v for k, v in raw.items() if k != 'family_id'}
                     for fid, raw in raw_families.items()},
        'people': raw_people,
        'resolutions': derived_resolutions,
    }
    from pydantic import ValidationError
    try:
        structure = NameStructure.model_validate(normalized)
        errors = validate_name_resolutions(structure, characters, evidence=evidence)
        return (None, issues + [(None, message) for message in errors]) if errors \
            else (structure, issues)
    except ValidationError as exc:
        return None, issues + [(None, f'name_structure 构造失败：{exc}')]


def validate_name_resolutions(
    structure: NameStructure, characters: CharactersCore, *, evidence,
) -> list[str]:
    """只验证语义声明的引用与原句证明，不判断两个姓名是否同一人物。"""
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
                        errors.append(f'{label}同一局部字面既已判定又未决')
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
