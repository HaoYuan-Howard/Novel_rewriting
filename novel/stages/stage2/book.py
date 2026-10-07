#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""book —— `str06.stage2.book` 的观察 owner 引擎（B2；D9 书级一次调用）。

【职责】书级观察（人物候选、称谓及证据、初态、世界约束）的确定性面：
  • prompt 组装：全书原文（〔第N章〕章界标记＋章前文字块）与证据句段清单；
  • wire → `BookObservation` 的机械验收（S2-2：解析／形状问题免费修复，不重购）：
    协议版本字面量（I-13）、人物 slug 形状、证据句段闭集（Resolver）、
    `R-2.10` 称谓锚点可派生（所引段逐字包含该称呼，不猜笔误）、`R-2.8`
    canonical 闭合（必须逐字等于某个已登记 surface）、人物内 surface 重复、
    初态／世界约束的目标与 known_by 闭合、场景作用域登记键不进 initial_state
    （`R-2.9` 一族）；
  • 人物表 core view 投影（`character_core_view`）：book 观察的人物条目按
    Resolver 派生 `from_chapter`（唯一实现，S-03），供 names 的确定性输入。

【职责边界】语义判断（谁是人物、初态收什么、世界约束收什么）归付费
semantic unit；本模块只验证并报告（S2-3／I-20），不补猜、不模糊匹配。
观察落盘 `stage2/observations/book.json`（canonical 形状），compiler 归一：
初态／约束的 basis 在 wire 上只给段 id（2026-10-07 裁决：模型只选段、
代码切原文——转写文本不进模型输出面），段属条目自身 source_refs 核对后
引文按**段全文**派生（落盘永远是原文字节，构造上不存在「引文不在原文中」）。
其余字段原样（窗口坐标／编号不在 wire——`from_chapter`
只活在 core view 投影与 compile 发布的 canonical 里，`R-2.10`）。validator
不修改输入：全部检查只读，问题聚合成列表返回（I-24／I-36）。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

from pydantic import ValidationError

from contracts.schema.observation import (
    STAGE2_BOOK_PROTOCOL, STAGE2_BOOK_WIRE_PROTOCOL, BookObservation, BookObservationWire,
)
from contracts.schema.source import (
    REGISTERED_STATE_KEYS, CharactersCore, CharacterEntry,
)
from novel.stages.common import BOOK_PROMPT, group_located_errors
from novel.stages.stage2.resolver import (
    EvidenceResolver, render_evidence_block, verify_bases,
)


def book_observation_prompt(*, chapters: list[dict], hook_copy: str,
                            evidence: Mapping[str, Mapping[str, Any]]) -> str:
    """书级观察请求的 prompt 组装（run_book 的唯一组装点）。

    章前文字（引子）按既有切分以〔章前文字〕块置顶（e00 证据段）；证据清单
    逐段带 id——模型只做闭集选择，不发明 id。
    """
    full = '\n'.join(f'〔第{c["chapter_ordinal"]}章〕\n{c["text"]}' for c in chapters)
    if hook_copy.strip():
        full = f'〔章前文字〕\n{hook_copy}\n\n' + full
    return BOOK_PROMPT.format(n=len(chapters), text=full,
                              evidence=render_evidence_block(evidence))


def _iter_book_refs(wire: Mapping[str, Any]) -> list[tuple[str, tuple[str, ...], tuple]]:
    """book wire 里全部 (来源, source_refs, wire 路径) 三元组（闭集核对与锚点
    派生共用；路径供条目定位器 `book_patch_locate` 同源复用，CS-H H4）。

    只走形状合法的条目；畸形条目交给协议校验报错，这里不猜不抛。
    """
    pairs: list[tuple[str, tuple[str, ...], tuple]] = []
    raw_chars = wire.get('characters')
    if isinstance(raw_chars, Mapping):
        for slug, c in raw_chars.items():
            surfaces = (c or {}).get('surfaces') if isinstance(c, Mapping) else None
            for s_index, s in enumerate(surfaces or []):
                if not isinstance(s, Mapping):
                    continue
                pairs.append((f'{slug} surface {s.get("surface")}',
                              tuple(s.get('source_refs') or ()),
                              ('characters', slug, 'surfaces', s_index)))
    raw_initial = wire.get('initial_state')
    for atom_index, atom in enumerate(raw_initial or []):
        if not isinstance(atom, Mapping):
            continue
        pairs.append((f'initial_state {atom.get("key")}',
                      tuple(atom.get('source_refs') or ()),
                      ('initial_state', atom_index)))
    raw_wcs = wire.get('world_constraints')
    for wc_index, wc in enumerate(raw_wcs or []):
        if not isinstance(wc, Mapping):
            continue
        pairs.append((f'world_constraint {str(wc.get("statement"))[:20]}',
                      tuple(wc.get('source_refs') or ()),
                      ('world_constraints', wc_index)))
    expression = wire.get('expression')
    if isinstance(expression, Mapping):
        for index, item in enumerate([expression.get('narration'), *(expression.get('mechanisms') or [])]):
            if isinstance(item, Mapping):
                path = (('expression', 'narration') if index == 0
                        else ('expression', 'mechanisms', index - 1))
                pairs.append((f'expression[{index}]', tuple(item.get('source_refs') or ()), path))
    return pairs


def _basis_label(group: str, atom: Mapping[str, Any]) -> str:
    """初态／约束条目的人读定位（段派生与逐字判定共用同一措辞）。"""
    if group == 'initial_state':
        return f'initial_state {atom.get("key")}'
    return f'world_constraint {str(atom.get("statement"))[:16]}'


def _expand_wire_basis(wire: dict) -> dict:
    """wire（`STAGE2_BOOK_WIRE_PROTOCOL`，evidence_refs 只给段 id）→ canonical
    形状副本（2026-10-07 裁决：模型只选段、代码切原文）。

    初态／约束的 evidence_refs 段 id 串变成 canonical 的
    ``basis: [{'segment': id, 'quote': None}]``，引文由 `_derive_basis_quotes`
    按段全文填入。非列表形状原样保留，交 wire 模型校验报告（不宽松接纳坏
    输入）。不改调用方对象（I-24）。
    """
    expanded = deepcopy(wire)
    expanded['protocol_version'] = STAGE2_BOOK_PROTOCOL
    for group in ('initial_state', 'world_constraints'):
        for atom in expanded.get(group) or ():
            if isinstance(atom, dict) and isinstance(atom.get('evidence_refs'), list):
                atom['basis'] = [{'segment': item, 'quote': None}
                                 if isinstance(item, str) else item
                                 for item in atom['evidence_refs']]
                del atom['evidence_refs']
    return expanded


def _derive_basis_quotes(
    wire: dict, resolver: EvidenceResolver,
) -> list[tuple[tuple, str]]:
    """basis 段 id 在条目自身 source_refs 内派生段全文引文（2026-10-07
    裁决：模型只选段、代码切原文）；段号与原文写回 compiler 副本。派生不了
    逐条报告（不改 source_refs、不换段——issue 指明「计入证据范围或改选」）。
    返回 (wire 路径, 消息)。"""
    issues: list[tuple[tuple, str]] = []
    for group in ('initial_state', 'world_constraints'):
        for atom_index, atom in enumerate(wire.get(group) or ()):
            if not isinstance(atom, dict):
                continue
            refs = tuple(atom.get('source_refs') or ())
            for basis_index, basis in enumerate(atom.get('basis') or ()):
                if not isinstance(basis, dict) or basis.get('segment') is None:
                    continue
                quote, issue = resolver.basis_quote(str(basis.get('segment') or ''), refs)
                if issue is None:
                    basis['quote'] = quote
                else:
                    issues.append((
                        (group, atom_index, 'basis', basis_index),
                        f'{_basis_label(group, atom)}：{issue.message}'))
    return issues


#: 派生失败的 basis 在**形状复验副本**里的占位（段号与引文各一）：与 chapters
#: 侧同一规则——仅在本轮已带错、必不发布时使用，占位不进任何输出。
_UNLOCATED_SEGMENT = 'e00_00'
_UNDERIVED_QUOTE = '（未派生）'


def _shape_view(wire: dict) -> dict:
    view = deepcopy(wire)
    for group in ('initial_state', 'world_constraints'):
        for atom in view.get(group) or ():
            for basis in (atom.get('basis') or ()) if isinstance(atom, dict) else ():
                if isinstance(basis, dict):
                    if basis.get('segment') is None:
                        basis['segment'] = _UNLOCATED_SEGMENT
                    if basis.get('quote') is None:
                        basis['quote'] = _UNDERIVED_QUOTE
            # wire 形状失败（如漏 evidence_refs）时 basis 可能整键缺失——补占位
            # 让其余字段照常过形状复验；该错误已由 ⑤ 的 wire 闸按路径报告，
            # 这里不重复整份化（2026-10-07 二次收口④）。
            if isinstance(atom, dict) and not atom.get('basis'):
                atom['basis'] = [{'segment': _UNLOCATED_SEGMENT,
                                  'quote': _UNDERIVED_QUOTE}]
    return view


def compile_book_observation(
    wire: Any, *, evidence: Mapping[str, Mapping[str, Any]] | Iterable[str],
) -> tuple[BookObservation | None, list[str]]:
    """book wire → (BookObservation, errors)：书级观察的全部机械闸。

    证据面与旧 compile_book 同形：传 `build_evidence` 的切分字典（闭集核对与
    `R-2.10` 锚点派生都有段文本可查），或只传 id 的合成闭集（锚点闸在该形态下
    按 id 前缀派生，字面无从核对——仅存于既有单元测试）。wire 不被修改
    （I-24）；错误逐条聚合，含 Resolver 的精确 issue 消息。LLM 输出面
    （`STAGE2_BOOK_WIRE_PROTOCOL`）的 basis 只给引文，所在段由 Resolver 在条目
    自身 source_refs 内定位（RC-27）；canonical 形状直通（wire 领养与测试读面）。
    """
    observation, located = _compile_book_view(wire, evidence=evidence)
    return observation, [message for _path, message in located]


def book_patch_locate(
    wire: Any, errors: list, *,
    evidence: Mapping[str, Mapping[str, Any]] | Iterable[str],
) -> list[tuple[tuple[str | int, ...], list]] | None:
    """CS-H H4（RC-57 覆盖）：book 条目定位（`ask_json` 的 `patch` 协议）。

    与 `compile_book_observation` 同一条管道（不新增判据），把每条错误映射到
    wire 条目路径（`characters.{slug}`［`.surfaces.i`］／`initial_state[i]`［`.basis.j`］／
    `world_constraints[i]`／`expression.narration`）；同一条目的父、子错误折叠到父条目
    （请求路径不重叠，见 `common.group_located_errors`）。协议形状类问题
    （wire 级）或 `errors` 与复跑定位集不完全一致时返回 None——本轮退整份返修，
    不静默漏项。裸元组返回，不 import novel.llm（分层规则禁 stage2 依赖 LLM 面）。
    """
    if not isinstance(wire, dict) or not errors:
        return None
    _observation, located = _compile_book_view(wire, evidence=evidence)
    return group_located_errors(
        errors, [((path,) if path is not None else None, message)
                 for path, message in located])


def _compile_book_view(
    wire: Any, *, evidence: Mapping[str, Mapping[str, Any]] | Iterable[str],
) -> tuple[BookObservation | None, list[tuple[tuple | None, str]]]:
    """`compile_book_observation` 的定位视图：错误携带 wire 路径（None＝wire 级，
    退整份返修；CS-H H4，与 `chapter_patch_locate` 的 `_compile_view` 同一模式）。"""
    issues: list[tuple[tuple | None, str]] = []

    def fail(path: tuple | None, message: str) -> None:
        issues.append((path, message))

    errors: list[str] = []
    if not isinstance(wire, dict):
        return None, [(None, 'book wire 必须是 JSON object')]
    segments = dict(evidence) if isinstance(evidence, Mapping) else {}
    resolver = EvidenceResolver(segments)
    original = wire
    is_wire = wire.get('protocol_version') == STAGE2_BOOK_WIRE_PROTOCOL
    # 引文定位与 D2 回取先于全部判定（与 chapters 侧同一规则）：后续闭集
    # 核对、锚点闸与协议校验都在副本上施行；调用方 wire 对象不被修改。
    canonical = _expand_wire_basis(wire) if is_wire else deepcopy(wire)
    if is_wire:
        # 只有 wire 面的 basis 不带引文；canonical 直通缺引文按协议拒收，不代补。
        issues.extend(_derive_basis_quotes(canonical, resolver))
        errors.extend(message for _path, message in issues)
    wire = canonical

    # ① 证据句段闭集（逐条精确 issue，不模糊差）
    for where, refs, path in _iter_book_refs(wire):
        for issue in resolver.check_closed_set(refs):
            fail(path, f'{where}：{issue.message}')
            errors.append(f'{where}：{issue.message}')

    # ② R-2.10 锚点可派生：每个 surface 的所引段至少一段逐字包含该称呼
    raw_chars = wire.get('characters')
    char_items = list(raw_chars.items()) if isinstance(raw_chars, Mapping) else []
    for slug, c in char_items:
        if not isinstance(c, Mapping):
            continue
        for s_index, s in enumerate(c.get('surfaces') or []):
            if not isinstance(s, Mapping):
                continue
            _chapter, issue = resolver.surface_chapter(
                s.get('surface'), tuple(s.get('source_refs') or ()))
            if issue is not None:
                path = ('characters', slug, 'surfaces', s_index)
                fail(path, f'{slug}：{issue.message}')
                errors.append(f'{slug}：{issue.message}')

    # ②b from_chapter 源保真（R10c 硬化，2026-10-02）：surface 的首现章必须
    # 等于证据闭集中**最早逐字包含该字面**的章。②只查所引段含字面，防不住
    # 「引了第 4 章的段而源文第 1 章早就出现」（表弟@4 三次实证——错到
    # compile4 才被 positive_material_leak 拦下，整条级联重购）。机械判定与
    # leak 归因同一尺（逐字扫描，I-20）；错误直接给出真实首现章与命中段。
    for slug, c in char_items:
        if not isinstance(c, Mapping):
            continue
        for s_index, s in enumerate(c.get('surfaces') or []):
            if not isinstance(s, Mapping):
                continue
            literal = s.get('surface')
            declared = s.get('from_chapter')
            if not isinstance(literal, str) or not literal.strip() \
                    or not isinstance(declared, int):
                continue
            hits = sorted(
                ((int(seg.get('chapter', 0)), ref)
                 for ref, seg in segments.items()
                 if isinstance(seg, Mapping) and literal in (seg.get('text') or '')),
                key=lambda item: (item[0], item[1]))
            if not hits:
                continue  # 全闭集无命中：锚点闸（②）与 leak 归因另行报告
            first_chapter, first_ref = hits[0]
            expected = max(1, first_chapter)  # 章前（e00，chapter=0）映射第 1 章
            if declared != expected:
                message = (
                    f'{slug} surface {literal!r} 的 from_chapter={declared} 记晚了'
                    f'——源文最早逐字出现于第 {expected} 章（证据段 {first_ref}），'
                    f'请把 from_chapter 改为 {expected} 并将 source_refs 指向'
                    '该章逐字命中的段')
                fail(('characters', slug, 'surfaces', s_index), message)
                errors.append(message)

    # ③ R-2.8 canonical 闭合 + surface 重复（canonical 未登记字面进不了替换表）
    slugs = set(raw_chars) if isinstance(raw_chars, Mapping) else set()
    for slug, c in char_items:
        if not isinstance(c, Mapping):
            continue
        surfaces = [s.get('surface') for s in (c.get('surfaces') or [])
                    if isinstance(s, Mapping)]
        canonical = c.get('canonical')
        if canonical is not None and canonical not in surfaces:
            message = (f'{slug} 的 canonical {canonical!r} 不是其 surface 之一'
                       '（canonical 必须逐字等于某个已登记 surface）')
            fail(('characters', slug), message)
            errors.append(message)
        if len(set(surfaces)) != len(surfaces):
            message = f'{slug} 的 surface 重复登记（复现应并入同一证据条）'
            fail(('characters', slug), message)
            errors.append(message)

    # ④ 初态与约束的引用闭合：目标人物、场景作用域键（R-2.9 一族）。
    # CS-S 1b：knowledge／known_by 闭合检查删（字段已删——初始知情由
    # Phase 2 的 facts known_at_start 承载）。
    for atom_index, atom in enumerate(wire.get('initial_state') or []):
        if not isinstance(atom, Mapping):
            continue
        path = ('initial_state', atom_index)
        target = atom.get('target') or {}
        refs = ([target.get('char_id')] if target.get('kind') == 'character'
                else [target.get('subject'), target.get('object')])
        for ref in refs:
            if ref is not None and ref not in slugs:
                message = f'initial_state {atom.get("key")} 引用未知人物 {ref}'
                fail(path, message)
                errors.append(message)
        if REGISTERED_STATE_KEYS.get(atom.get('key')) == 'scene':
            message = (f'initial_state {atom.get("key")} 的键 {atom.get("key")} 是场景'
                       '作用域：开场所在交给第 1 章第一个 new 拍；故事开始前成立的长期'
                       '居所登记 residence')
            fail(path, message)
            errors.append(message)

    # ④b R10a a1：初态/约束的 basis 判定（段属自身 source_refs ＋ 引文逐字
    # 在段内——canonical 直通面的防线）；wire 派生成功的条目引文＝段全文，
    # 天然通过；派生失败的条目（quote 仍为 None）已在派生步报告，不重复。
    basis_entries: list[tuple[str, str, str, Iterable[str]]] = []
    basis_paths: list[tuple] = []
    for group in ('initial_state', 'world_constraints'):
        for atom_index, atom in enumerate(wire.get(group) or []):
            if not isinstance(atom, Mapping):
                continue
            for basis_index, basis in enumerate(atom.get('basis') or ()):
                if isinstance(basis, Mapping) and basis.get('segment') is not None \
                        and basis.get('quote') is not None:
                    basis_entries.append((
                        _basis_label(group, atom),
                        str(basis.get('segment') or ''),
                        str(basis.get('quote') or ''),
                        tuple(atom.get('source_refs') or ())))
                    basis_paths.append((group, atom_index, 'basis', basis_index))
    # 标签可能重复（同键初态、前 16 字相同的约束）：按条目序消费路径，与
    # verify_bases 的逐条产出序对齐。
    by_label: dict[str, list[tuple]] = {}
    for (where, _seg, _quote, _refs), path in zip(basis_entries, basis_paths):
        by_label.setdefault(where, []).append(path)
    for where, issue in verify_bases(resolver, basis_entries):
        message = f'{where}：{issue.message}'
        paths = by_label.get(where) or []
        path = paths.pop(0) if paths else None
        fail(path, message)
        errors.append(message)

    # ⑤ 协议形状（版本字面量、slug 形状、target 形状、转移原子闭集）；LLM
    # 输出面另按 wire 模型复验（evidence_refs 只收段 id 串，不宽松接纳 canonical
    # 形状）。形状失败按 pydantic 路径定位条目（2026-10-07 二次收口④：
    # initial_state[i]／characters.{slug} 一类窄修该条目，顶层失败退整份）。
    if is_wire:
        try:
            BookObservationWire.model_validate(original)
        except ValidationError as exc:
            from novel.stages.common import wire_shape_issues
            for paths, message in wire_shape_issues(exc, kind='book'):
                fail(paths[0] if paths else None, f'book 观察 {message}')
                errors.append(f'book 观察 {message}')
    try:
        # 占位段号只在本轮已带错（必不发布）时用于形状复验，见 `_UNLOCATED_SEGMENT`。
        observation = BookObservation.model_validate(_shape_view(wire) if errors else wire)
    except ValidationError as exc:
        return None, issues + [(None, f'book 观察协议校验失败：{exc}')]
    if errors:
        return None, issues
    for cid in observation.expression.narration.focalizers:
        if cid != 'narrator' and cid not in observation.characters:
            fail(('expression', 'narration', 'focalizers'),
                 f'expression.narration 引用未知人物 {cid}')
            errors.append(f'expression.narration 引用未知人物 {cid}')
    if errors:
        return None, issues
    return observation, issues


def character_core_view(
    book: BookObservation, *, evidence: Mapping[str, Mapping[str, Any]] | Iterable[str],
) -> tuple[CharactersCore | None, list[str]]:
    """book 观察 → 人物表 core view（names 的确定性输入投影；S2-4 只读派生）。

    `from_chapter` 由 Resolver 按证据锚点派生（`R-2.10` 唯一实现；D12/I-15）。
    本投影**不含初态**：初态编号（s0_*）由 compile 生成后并入 canonical，这里
    提前编号等于第二个真相源（I-16/I-21）。compile_book_observation 已闸过
    锚点可派生，此处派生失败说明证据面与观察不再一致——如实报错，不猜。
    """
    segments = dict(evidence) if isinstance(evidence, Mapping) else {}
    resolver = EvidenceResolver(segments)
    errors: list[str] = []
    characters: dict[str, CharacterEntry] = {}
    for slug, candidate in book.characters.items():
        surfaces = []
        for mention in candidate.surfaces:
            chapter, issue = resolver.surface_chapter(mention.surface, mention.source_refs)
            if issue is not None:
                errors.append(f'{slug}：{issue.message}')
                continue
            surfaces.append({'surface': mention.surface, 'from_chapter': chapter,
                             'until_chapter': None,
                             'source_refs': list(mention.source_refs)})
        if not surfaces:
            continue
        characters[slug] = CharacterEntry(canonical=candidate.canonical,
                                          surfaces=surfaces)  # type: ignore[arg-type]
    if errors or not characters:
        return None, errors or ['book 观察没有可投影的人物条目']
    return CharactersCore(characters=characters, initial_state=()), []
