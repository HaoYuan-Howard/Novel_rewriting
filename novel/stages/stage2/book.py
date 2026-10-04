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
观察落盘 `stage2/observations/book.json`（canonical 形状），compiler 归一两处：
初态／约束 basis 的所在段由 Resolver 按引文在条目自身 source_refs 内定位
（RC-27，2026-10-04——wire 只给引文，段号不让模型抄）；引文按 `norm()` 尺
回取段原文切片（D2 标点宽度——源文半角、模型全角的真摘抄不再误报，落盘
永远是原文字节）。其余字段原样（窗口坐标／编号不在 wire——`from_chapter`
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
from novel.stages.common import BOOK_PROMPT
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


def _iter_book_refs(wire: Mapping[str, Any]) -> list[tuple[str, tuple[str, ...]]]:
    """book wire 里全部 (来源, source_refs) 对（闭集核对与锚点派生共用）。

    只走形状合法的条目；畸形条目交给协议校验报错，这里不猜不抛。
    """
    pairs: list[tuple[str, tuple[str, ...]]] = []
    raw_chars = wire.get('characters')
    if isinstance(raw_chars, Mapping):
        for slug, c in raw_chars.items():
            surfaces = (c or {}).get('surfaces') if isinstance(c, Mapping) else None
            for s in surfaces or []:
                if not isinstance(s, Mapping):
                    continue
                pairs.append((f'{slug} surface {s.get("surface")}',
                              tuple(s.get('source_refs') or ())))
    raw_initial = wire.get('initial_state')
    for atom in raw_initial or []:
        if not isinstance(atom, Mapping):
            continue
        pairs.append((f'initial_state {atom.get("key")}',
                      tuple(atom.get('source_refs') or ())))
    raw_wcs = wire.get('world_constraints')
    for wc in raw_wcs or []:
        if not isinstance(wc, Mapping):
            continue
        pairs.append((f'world_constraint {str(wc.get("statement"))[:20]}',
                      tuple(wc.get('source_refs') or ())))
    expression = wire.get('expression')
    if isinstance(expression, Mapping):
        for index, item in enumerate([expression.get('narration'), *(expression.get('mechanisms') or [])]):
            if isinstance(item, Mapping):
                pairs.append((f'expression[{index}]', tuple(item.get('source_refs') or ())))
    return pairs


def _canonicalize_basis_quotes(wire: Any, resolver: EvidenceResolver) -> None:
    """basis 引文回取原文切片（D2 标点宽度，2026-10-03；形状与 chapters 侧
    同一规则、各自的 wire 形状）：逐字已在段内不动；仅标点／空白不同且唯一
    命中 → 段原文切片；其余不改写、不放宽判定。就地改写 compiler 副本，
    原 wire 不动（I-24）。
    """
    groups: list[tuple[Any, Any]] = [(wire.get('initial_state') or ()),
                                     (wire.get('world_constraints') or ())]
    for atoms in groups:
        for atom in atoms:
            if not isinstance(atom, Mapping):
                continue
            for basis in atom.get('basis') or ():
                if isinstance(basis, Mapping):
                    segment = str(basis.get('segment') or '')
                    quote = str(basis.get('quote') or '')
                    if segment and quote:
                        basis['quote'] = resolver.canonical_quote(segment, quote)


def _basis_label(group: str, atom: Mapping[str, Any]) -> str:
    """初态／约束条目的人读定位（引文定位与逐字判定共用同一措辞）。"""
    if group == 'initial_state':
        return f'initial_state {atom.get("key")}'
    return f'world_constraint {str(atom.get("statement"))[:16]}'


def _expand_wire_basis(wire: dict) -> dict:
    """RC-27：book wire（`STAGE2_BOOK_WIRE_PROTOCOL`）→ canonical 形状副本。

    初态／约束的 basis 引文串变成 ``{'segment': None, 'quote': 引文}``，段号
    留给 `_locate_basis_quotes`；字面量换成 canonical 版本。非字符串条目原样
    保留，交 wire 模型校验报告（不宽松接纳坏输入）。不改调用方对象（I-24）。
    """
    expanded = deepcopy(wire)
    expanded['protocol_version'] = STAGE2_BOOK_PROTOCOL
    for group in ('initial_state', 'world_constraints'):
        for atom in expanded.get(group) or ():
            if isinstance(atom, dict) and isinstance(atom.get('basis'), list):
                atom['basis'] = [{'segment': None, 'quote': item} if isinstance(item, str)
                                 else item for item in atom['basis']]
    return expanded


def _locate_basis_quotes(wire: dict, resolver: EvidenceResolver) -> list[str]:
    """RC-27：初态／约束只给引文的 basis 在条目自身 source_refs 内定位所在段，
    段号与原文切片写回 compiler 副本；定位不了逐条报告（不改 source_refs、
    不换引文——Resolver 的 issue 指明引文实际所在段）。"""
    errors: list[str] = []
    for group in ('initial_state', 'world_constraints'):
        for atom in wire.get(group) or ():
            if not isinstance(atom, dict):
                continue
            refs = tuple(atom.get('source_refs') or ())
            for basis in atom.get('basis') or ():
                if not isinstance(basis, dict) or basis.get('segment') is not None:
                    continue
                segment, quote, issue = resolver.locate_quote(
                    str(basis.get('quote') or ''), refs)
                if issue is None:
                    basis['segment'], basis['quote'] = segment, quote
                else:
                    errors.append(f'{_basis_label(group, atom)}：{issue.message}')
    return errors


#: 定位失败的 basis 在**形状复验副本**里的占位段号（与 chapters 侧同一规则：
#: 仅在本轮已带错、必不发布时使用，占位不进任何输出）。
_UNLOCATED_SEGMENT = 'e00_00'


def _shape_view(wire: dict) -> dict:
    view = deepcopy(wire)
    for group in ('initial_state', 'world_constraints'):
        for atom in view.get(group) or ():
            for basis in (atom.get('basis') or ()) if isinstance(atom, dict) else ():
                if isinstance(basis, dict) and basis.get('segment') is None:
                    basis['segment'] = _UNLOCATED_SEGMENT
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
    errors: list[str] = []
    if not isinstance(wire, dict):
        return None, ['book wire 必须是 JSON object']
    segments = dict(evidence) if isinstance(evidence, Mapping) else {}
    resolver = EvidenceResolver(segments)
    original = wire
    is_wire = wire.get('protocol_version') == STAGE2_BOOK_WIRE_PROTOCOL
    # 引文定位与 D2 回取先于全部判定（与 chapters 侧同一规则）：后续闭集
    # 核对、锚点闸与协议校验都在副本上施行；调用方 wire 对象不被修改。
    canonical = _expand_wire_basis(wire) if is_wire else deepcopy(wire)
    if is_wire:
        # 只有 wire 面的 basis 不带段号；canonical 直通缺段号按协议拒收，不代补。
        errors.extend(_locate_basis_quotes(canonical, resolver))
    _canonicalize_basis_quotes(canonical, resolver)
    wire = canonical

    # ① 证据句段闭集（逐条精确 issue，不模糊差）
    for where, refs in _iter_book_refs(wire):
        for issue in resolver.check_closed_set(refs):
            errors.append(f'{where}：{issue.message}')

    # ② R-2.10 锚点可派生：每个 surface 的所引段至少一段逐字包含该称呼
    raw_chars = wire.get('characters')
    char_items = list(raw_chars.items()) if isinstance(raw_chars, Mapping) else []
    for slug, c in char_items:
        if not isinstance(c, Mapping):
            continue
        for s in (c.get('surfaces') or []):
            if not isinstance(s, Mapping):
                continue
            _chapter, issue = resolver.surface_chapter(
                s.get('surface'), tuple(s.get('source_refs') or ()))
            if issue is not None:
                errors.append(f'{slug}：{issue.message}')

    # ②b from_chapter 源保真（R10c 硬化，2026-10-02）：surface 的首现章必须
    # 等于证据闭集中**最早逐字包含该字面**的章。②只查所引段含字面，防不住
    # 「引了第 4 章的段而源文第 1 章早就出现」（表弟@4 三次实证——错到
    # compile4 才被 positive_material_leak 拦下，整条级联重购）。机械判定与
    # leak 归因同一尺（逐字扫描，I-20）；错误直接给出真实首现章与命中段。
    for slug, c in char_items:
        if not isinstance(c, Mapping):
            continue
        for s in (c.get('surfaces') or []):
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
                errors.append(
                    f'{slug} surface {literal!r} 的 from_chapter={declared} 记晚了'
                    f'——源文最早逐字出现于第 {expected} 章（证据段 {first_ref}），'
                    f'请把 from_chapter 改为 {expected} 并将 source_refs 指向'
                    '该章逐字命中的段')

    # ③ R-2.8 canonical 闭合 + surface 重复（canonical 未登记字面进不了替换表）
    slugs = set(raw_chars) if isinstance(raw_chars, Mapping) else set()
    for slug, c in char_items:
        if not isinstance(c, Mapping):
            continue
        surfaces = [s.get('surface') for s in (c.get('surfaces') or [])
                    if isinstance(s, Mapping)]
        canonical = c.get('canonical')
        if canonical is not None and canonical not in surfaces:
            errors.append(
                f'{slug} 的 canonical {canonical!r} 不是其 surface 之一'
                '（canonical 必须逐字等于某个已登记 surface）')
        if len(set(surfaces)) != len(surfaces):
            errors.append(f'{slug} 的 surface 重复登记（复现应并入同一证据条）')

    # ④ 初态与约束的引用闭合：目标人物、known_by、场景作用域键（R-2.9 一族）
    for atom in wire.get('initial_state') or []:
        if not isinstance(atom, Mapping):
            continue
        target = atom.get('target') or {}
        refs = ([target.get('char_id')] if target.get('kind') == 'character'
                else [target.get('subject'), target.get('object')])
        for ref in refs:
            if ref is not None and ref not in slugs:
                errors.append(f'initial_state {atom.get("key")} 引用未知人物 {ref}')
        knowledge = atom.get('knowledge')
        for who in (knowledge.get('known_by') or []) if isinstance(knowledge, Mapping) else []:
            if who not in slugs:
                errors.append(
                    f'initial_state {atom.get("key")} 的 known_by 引用未知人物 {who}'
                    '（读者知情用 reader_knows 表达）')
        if REGISTERED_STATE_KEYS.get(atom.get('key')) == 'scene':
            errors.append(
                f'initial_state {atom.get("key")} 的键 {atom.get("key")} 是场景'
                '作用域：开场所在交给第 1 章第一个 new 拍；故事开始前成立的长期'
                '居所登记 residence')

    # ④b R10a a1：初态/约束的 basis 引文判定（C4——逐字命中＋段属自身
    # source_refs）；定位失败的 basis（段号仍为 None）已在定位步报告，不重复。
    basis_entries: list[tuple[str, str, str, Iterable[str]]] = []
    for group in ('initial_state', 'world_constraints'):
        for atom in wire.get(group) or []:
            if not isinstance(atom, Mapping):
                continue
            for basis in atom.get('basis') or ():
                if isinstance(basis, Mapping) and basis.get('segment') is not None:
                    basis_entries.append((
                        _basis_label(group, atom),
                        str(basis.get('segment') or ''),
                        str(basis.get('quote') or ''),
                        tuple(atom.get('source_refs') or ())))
    for where, issue in verify_bases(resolver, basis_entries):
        errors.append(f'{where}：{issue.message}')

    # ⑤ 协议形状（版本字面量、slug 形状、target 形状、转移原子闭集）；LLM
    # 输出面另按 wire 模型复验（basis 只收引文串，不宽松接纳 canonical 形状）。
    if is_wire:
        try:
            BookObservationWire.model_validate(original)
        except ValidationError as exc:
            errors.append(f'book 观察 wire 协议校验失败：{exc}')
    try:
        # 占位段号只在本轮已带错（必不发布）时用于形状复验，见 `_UNLOCATED_SEGMENT`。
        observation = BookObservation.model_validate(_shape_view(wire) if errors else wire)
    except ValidationError as exc:
        return None, errors + [f'book 观察协议校验失败：{exc}']
    if errors:
        return None, errors
    for cid in observation.expression.narration.focalizers:
        if cid != 'narrator' and cid not in observation.characters:
            errors.append(f'expression.narration 引用未知人物 {cid}')
    if errors:
        return None, errors
    return observation, []


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
