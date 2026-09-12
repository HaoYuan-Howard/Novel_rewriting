#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 2 落盘前的确定性归一。

模型只负责语义判断；能由原文或 registry 唯一推出的字段都在这里生成。只有会驱动
闸门或恢复的失败态才进入 ``_provenance``，成功改写不重复留展示账。
"""
from __future__ import annotations

import copy
import re
from typing import Any, Literal, NamedTuple

from novel.core.evidence import iter_global_evidence_slots
from novel.core.facts import first_reveal_chapter, setup_chapters_of
from novel.core.naming import strip_distinguisher
from novel.core.spans import (
    content_only, exact_span, locate_unique_span,
)
from novel.core.text import norm
from novel.stages.stage2.paragraphs import (
    paragraph_range_of,
)


def _source(text: str, start: int, end: int) -> dict[str, Any]:
    """Global 有据属性的正式字节区间；不为 Blueprint 重新保存原文。"""
    return {'text': text[start:end], 'start': start, 'end': end}


class EvidenceLocatorCorrection(NamedTuple):
    """一次成功的跨章 locator 纠正；只供本次调用观察，不进入正式 provenance。"""

    claim_path: str
    original_chapter: int
    resolved_chapter: int
    recovery: Literal['exact', 'normalized']


class GlobalNormalizationAccounting(dict[str, Any]):
    """可直接展开给 ``GlobalProvenance`` 的账，以及不落盘的成功纠正元数据。

    映射部分故意仍只有当前 v4 provenance 的两个键；既有调用点继续
    ``**accounting`` 时不会把成功纠正写进严格 schema。需要观察本次纠正的调用方
    读取 ``accounting.evidence_locator_corrections``。
    """

    evidence_locator_corrections: tuple[EvidenceLocatorCorrection, ...]

    def __init__(
        self,
        *,
        setup_chapters_pruned: list[tuple[str, list[int], list[int]]],
        evidence_locator_failures: list[tuple[str, int, str, str]],
        evidence_locator_corrections: list[EvidenceLocatorCorrection],
    ) -> None:
        super().__init__(
            setup_chapters_pruned=setup_chapters_pruned,
            evidence_locator_failures=evidence_locator_failures,
        )
        self.evidence_locator_corrections = tuple(evidence_locator_corrections)


def restore_pruned_secret_setups(
        data: dict[str, Any], pruned: list[tuple[str, list[int], list[int]]],
) -> None:
    """复验保留原裁剪输入；仅显式归一或 typed 修订可以确认改变埋点意图。

    当前埋点仍等于旧归一结果时，从可信失败账恢复原列表再判。人工已明确改过
    埋点或删除该 secret 时，不把历史字段强行补回；揭示章修正后原埋点可重新通过。
    """
    secrets = {row['secret_id']: row for row in data['canon']['secrets']}
    for secret_id, original, normalized in pruned:
        row = secrets.get(secret_id)
        if row is not None and setup_chapters_of(row) == normalized:
            row['setup'] = {'kind': 'foreshadowed', 'chapters': list(original)}


def restore_failed_global_evidence_locators(
    data: dict[str, Any],
    failures: list[tuple[str, int, str, str]],
) -> int:
    """从当前 ``.INVALID`` 的正向失败账恢复未落地 locator。

    正式 evidence 槽只能保存 ``source=None``，locator 必须留在
    ``GlobalProvenance.evidence_locator_failures`` 才能支持后续 0 LLM
    ``--revalidate``。这里只恢复「同路径、同声明章、source 仍为空」且
    reason 明确表示原输入是 locator 的槽；已有 SourceSpan、人工改过章号、
    路径漂移，尤其是 formal SourceSpan 验证失败都不会被旧 provenance
    反向洗成 locator，否则第二次 revalidate 会违反 R-2.20 自动搬家。
    """
    slots: dict[str, tuple[dict[str, Any], str]] = {}
    for path, owner, field in iter_global_evidence_slots(data):
        if path in slots:
            raise ValueError(f'global evidence canonical claim_path 重复：{path}')
        slots[path] = (owner, field)

    restored = 0
    seen: set[str] = set()
    for row in failures:
        if len(row) != 4:
            raise ValueError('evidence_locator_failures 行必须是四元组')
        path, chapter, locator, reason = row
        if path in seen:
            raise ValueError(f'evidence_locator_failures 路径重复：{path}')
        seen.add(path)
        slot = slots.get(path)
        if slot is None:
            raise ValueError(
                f'evidence_locator_failures 路径不在当前候选：{path}')
        owner, field = slot
        evidence = owner.get(field)
        if not isinstance(evidence, dict):
            continue
        if evidence.get('source') is not None or 'locator' in evidence:
            continue
        # 新旧版 locator 失败的稳定开头都是这一句。formal
        # ``exact_span`` 错误不带此前缀，因而永不反向恢复。
        if not isinstance(reason, str) or not reason.startswith(
                'locator 在指定章内'):
            continue
        if evidence.get('chapter_ordinal') != chapter:
            continue
        owner[field] = {
            'chapter_ordinal': chapter,
            'locator': locator,
        }
        restored += 1
    return restored


def _normalize_distinguishers(characters: dict[str, dict[str, Any]]) -> None:
    canons = [(cid, str((char or {}).get('canonical') or ''))
              for cid, char in characters.items()]
    counts: dict[str, int] = {}
    for _cid, canonical in canons:
        base = strip_distinguisher(canonical)
        if base:
            counts[base] = counts.get(base, 0) + 1

    for cid, canonical in canons:
        base = strip_distinguisher(canonical)
        if canonical and base and base != canonical and counts.get(base, 0) < 2:
            characters[cid]['canonical'] = base


def _normalize_address_surfaces(characters: dict[str, dict[str, Any]], whole: str) -> None:
    """回取原文中出现的最长连续子串；同长时取靠后的中文中心语。"""
    for char in characters.values():
        for address in (char.get('address') or []):
            raw = str(address.get('surface') or '')
            if not raw or raw in whole:
                continue
            best = ''
            for start in range(len(raw)):
                # 最短两字；一字通名会过度吞掉模型自造的人物标签。
                for end in range(len(raw), start + 1, -1):
                    fragment = raw[start:end]
                    if len(fragment) >= len(best) and fragment in whole:
                        best = fragment
                        break
            if best:
                address['surface'] = best


def _dedupe_addresses(characters: dict[str, dict[str, Any]]) -> None:
    for char in characters.values():
        merged: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
        for address in (char.get('address') or []):
            if not address.get('surface'):
                continue
            key = (address.get('surface'), address.get('by'), address.get('context'))
            current = merged.get(key)
            if current is None:
                merged[key] = dict(address)
                continue
            chapters = [x for x in (current.get('from_chapter'), address.get('from_chapter'))
                        if isinstance(x, int)]
            if chapters:
                current['from_chapter'] = min(chapters)
        char['address'] = list(merged.values())


def _normalize_from_chapter(characters: dict[str, dict[str, Any]],
                            chapters: list[tuple[int, str]]) -> None:
    first_seen: dict[str, int | None] = {}
    for char in characters.values():
        for address in (char.get('address') or []):
            surface = str(address.get('surface') or '').strip()
            old = address.get('from_chapter')
            if not surface or not isinstance(old, int):
                continue
            if surface not in first_seen:
                first_seen[surface] = next((o for o, text in chapters if surface in text), None)
            first = first_seen[surface]
            if first is not None and old < first:
                address['from_chapter'] = first


def _resolve_address_by(characters: dict[str, dict[str, Any]]) -> None:
    by_canonical: dict[str, list[str]] = {}
    by_surface: dict[str, set[str]] = {}
    for cid, char in characters.items():
        canonical = str(char.get('canonical') or '')
        if canonical:
            by_canonical.setdefault(canonical, []).append(cid)
        for address in (char.get('address') or []):
            surface = str(address.get('surface') or '').strip()
            if surface:
                by_surface.setdefault(surface, set()).add(cid)
    unique_canonical = {name: ids[0] for name, ids in by_canonical.items() if len(ids) == 1}
    unique_surface = {name: next(iter(ids)) for name, ids in by_surface.items()
                      if len(ids) == 1}

    for char in characters.values():
        for address in (char.get('address') or []):
            raw = address.get('by')
            if not raw or raw in characters or raw in ('narration', 'public'):
                continue
            # char_id 是显式外键，未知 ID 交给 HARD；不能伪装成无名群体 public。
            if isinstance(raw, str) and re.fullmatch(r'char_\d+', raw):
                continue
            hit = unique_canonical.get(str(raw)) or unique_surface.get(str(raw))
            if hit:
                address['by'] = hit
            else:
                address['by'] = 'public'


def _prune_setups(data: dict[str, Any]) -> list[tuple[str, list[int], list[int]]]:
    """把不早于**首次揭示章**的埋点裁掉；全裁光的那条降级成 `{"kind": "cold"}`。

    ⚠ 基准是 `first_reveal_chapter`，不是 `min(所有揭示章)`（v2 起）。下发闸门用
    的是同一个函数——两处必须同基准，否则「这里没裁掉的埋点」会被那边当成未来章。
    ⚠ 降级成 `cold` 之后，字段值上与真·冷揭示同形；分得开它们的只有本函数
    返回的这份账（`_provenance.setup_chapters_pruned`）。判据断言的正是它。
    """
    changed: list[tuple[str, list[int], list[int]]] = []
    for secret in (((data.get('canon') or {}).get('secrets')) or []):
        if not isinstance(secret, dict):
            continue
        old = setup_chapters_of(secret)
        first = first_reveal_chapter(secret)
        if not old or first is None:
            continue
        keep = [chapter for chapter in old if chapter < first]
        if keep == old:
            continue
        secret['setup'] = ({'kind': 'foreshadowed', 'chapters': keep} if keep
                           else {'kind': 'cold'})
        changed.append((str(secret.get('secret_id') or ''), old, keep))
    return changed


def _normalize_global_evidence(
    data: dict[str, Any], main_text: list[dict[str, Any]],
    *, character_only: bool = False,
) -> tuple[
    list[tuple[str, int, str, str]],
    list[EvidenceLocatorCorrection],
]:
    """把所有响应/人工 locator 回取成正式 ``FactEvidence``。

    指定合法章唯一命中时直接采用；只有该章零命中才逐章复用同一把
    ``locate_unique_span`` 尺子，全书恰一候选且没有任何章内多命中时才纠正章号。
    指定章多命中、非法章、全书零/多命中与既有正式 span 漂移都写正向失败账，
    并把该槽收成 ``source=None``。失败态仍符合最终 schema，随后由同一 HARD 判据
    拦进 `.INVALID`，因此 typed edit 与 `--revalidate` 有一份可读问题稿可恢复。

    返回的第二项只含成功跨章纠正的坐标元数据，不含 locator 或正文；调用方可以观察，
    但它不属于正式 ``GlobalProvenance``。
    """
    text_of = {
        int(section.get('chapter_ordinal') or 0): str(section.get('text') or '')
        for section in main_text
    }
    fallback_chapter = min(text_of) if text_of else 1
    failures: list[tuple[str, int, str, str]] = []
    corrections: list[EvidenceLocatorCorrection] = []

    for path, owner, field in iter_global_evidence_slots(data):
        if character_only and not path.startswith('entities.characters.'):
            continue
        evidence = owner.get(field)
        if not isinstance(evidence, dict):
            owner[field] = {'chapter_ordinal': fallback_chapter, 'source': None}
            failures.append((path, fallback_chapter, '', '缺少 evidence 对象'))
            continue
        chapter_raw = evidence.get('chapter_ordinal')
        chapter = chapter_raw if type(chapter_raw) is int else fallback_chapter
        locator = str(evidence.get('locator') or '')
        chapter_text = text_of.get(chapter)
        if chapter_text is None:
            owner[field] = {'chapter_ordinal': chapter, 'source': None}
            failures.append((path, chapter, locator, '证据章不在 Stage1 正文章集合内'))
            continue

        if 'locator' in evidence:
            got = locate_unique_span(chapter_text, locator)
            if isinstance(got, tuple):
                start, end, _recovery = got
                source = _source(chapter_text, start, end)
                _start, _end, problem = exact_span(source, chapter_text)
                if problem:
                    owner[field] = {'chapter_ordinal': chapter, 'source': None}
                    failures.append((path, chapter, locator, problem))
                    continue
                owner[field] = {'chapter_ordinal': chapter, 'source': source}
                continue
            if isinstance(got, int):
                owner[field] = {'chapter_ordinal': chapter, 'source': None}
                failures.append((
                    path, chapter, locator,
                    f'locator 在指定章内多处命中（{got} 处）',
                ))
                continue

            # 指定章确为零命中时，章号只是可安全纠正的定位提示。逐章调用同一
            # locator 尺子，不能拼接全文（否则会产生跨章边界假命中），也不能
            # 在章内多命中时挑第一处。
            cross_candidates: list[tuple[int, int, int, str]] = []
            cross_count = 0
            has_chapter_ambiguity = False
            for candidate_chapter, candidate_text in sorted(text_of.items()):
                cross_got = locate_unique_span(candidate_text, locator)
                if isinstance(cross_got, tuple):
                    start, end, recovery = cross_got
                    cross_candidates.append(
                        (candidate_chapter, start, end, recovery))
                    cross_count += 1
                elif isinstance(cross_got, int):
                    has_chapter_ambiguity = True
                    cross_count += cross_got

            if len(cross_candidates) == 1 and not has_chapter_ambiguity:
                resolved_chapter, start, end, recovery = cross_candidates[0]
                resolved_text = text_of[resolved_chapter]
                source = _source(resolved_text, start, end)
                _start, _end, problem = exact_span(source, resolved_text)
                if problem:
                    owner[field] = {'chapter_ordinal': chapter, 'source': None}
                    failures.append((path, chapter, locator, problem))
                    continue
                owner[field] = {
                    'chapter_ordinal': resolved_chapter,
                    'source': source,
                }
                corrections.append(EvidenceLocatorCorrection(
                    claim_path=path,
                    original_chapter=chapter,
                    resolved_chapter=resolved_chapter,
                    recovery=recovery,
                ))
                continue

            owner[field] = {'chapter_ordinal': chapter, 'source': None}
            reason = (
                'locator 在指定章内找不到，且在全书各章均找不到'
                if cross_count == 0 else
                f'locator 在指定章内找不到，跨章回取不唯一（全书 {cross_count} 处）'
            )
            failures.append((path, chapter, locator, reason))
            continue

        source = evidence.get('source')
        _start, _end, problem = exact_span(source, chapter_text)
        if problem:
            owner[field] = {'chapter_ordinal': chapter, 'source': None}
            failures.append((
                path, chapter,
                str((source or {}).get('text') or '') if isinstance(source, dict) else '',
                problem,
            ))
            continue
        # 已是正式产物形：只验证，不按字面重搜，不把重复原文误判成漂移。
        owner[field] = {'chapter_ordinal': chapter, 'source': copy.deepcopy(source)}

    return failures, corrections


def normalize_characters(
        characters: dict[str, dict[str, Any]], main_text: list[dict[str, Any]],
) -> tuple[dict[str, Any], GlobalNormalizationAccounting]:
    """人物 producer 与人工编辑共用的称谓及静态证据归一，不创建知识占位。"""
    data = {'entities': {'characters': copy.deepcopy(characters)}}
    current = data['entities']['characters']
    whole = ''.join(str(section.get('text') or '') for section in main_text)
    chapters = [(int(section.get('chapter_ordinal') or 0), str(section.get('text') or ''))
                for section in main_text]
    _normalize_distinguishers(current)
    _normalize_address_surfaces(current, whole)
    _dedupe_addresses(current)
    _normalize_from_chapter(current, chapters)
    _resolve_address_by(current)
    failures, corrections = _normalize_global_evidence(
        data, main_text, character_only=True)
    return current, GlobalNormalizationAccounting(
        setup_chapters_pruned=[], evidence_locator_failures=failures,
        evidence_locator_corrections=corrections)


def normalize_global(data: dict[str, Any], main_text: list[dict[str, Any]], *,
                     source_hook: str | None = None) \
        -> tuple[dict[str, Any], GlobalNormalizationAccounting]:
    """把已由编译边界铸成正式 evidence 形状的 dict 归一为最终产物字段。"""
    characters = (((data.get('entities') or {}).get('characters')) or {})
    whole = ''.join(str(section.get('text') or '') for section in main_text)
    chapters = [(int(section.get('chapter_ordinal') or 0), str(section.get('text') or ''))
                for section in main_text]

    _normalize_distinguishers(characters)
    _normalize_address_surfaces(characters, whole)
    _dedupe_addresses(characters)
    _normalize_from_chapter(characters, chapters)
    _resolve_address_by(characters)
    pruned = _prune_setups(data)
    evidence_failures, evidence_corrections = _normalize_global_evidence(
        data, main_text)
    from novel.stages.stage2.source_blurb import normalize_hook_evidence
    normalize_hook_evidence(data, source_hook)
    return data, GlobalNormalizationAccounting(
        setup_chapters_pruned=pruned,
        evidence_locator_failures=evidence_failures,
        evidence_locator_corrections=evidence_corrections,
    )


def past_attested_surfaces(characters: dict[str, dict[str, Any]] | None,
                           main_text: list[dict[str, Any]]) -> dict[int, list[str]]:
    """`{阅读序章号: 此前章节正文里已出现过的名册称名}` —— **只含过去，无未来**。

    有界上下文架构（`bounded-fidelity-*`）把模型可见的前文收窄到窗口与尾摘之后，prompt 里
    「到目前为止的正文里出现过没有」这条判据模型就自证不了了（名字可能首现在
    窗口外）。这份清单把「已现与否」的判定收回代码侧：字面取自 registry
    （canonical 剥区分词 + 全部 `address[].surface`，与 `surface_owners` 同一套
    取值面），**出现性按第 1..N-1 章原文的 `norm` 子串命中判**——首现在本章
    及以后的字面一律不进清单，所以它不带任何未来（对比 `extract.build_blueprint_user`
    docstring 里 global 整份不下发的理由）。

    ⚠ 与 `checks._Ctx.first_seen` 是同一把 `norm` 子串尺子，但形状不同不合并：
    那边问「这些字面最早出现在第几章」（逐章扫描找最早），这边问「到上一章为止
    出现过哪些字面」（逐章给清单）。
    ⚠ `norm` 是纯逐字符过滤 ⇒ `norm(a + '\\n' + b) == norm(a) + norm(b)`，
    这里按章增量拼接与 checks 侧「先 '\\n'.join 再 norm」逐字节等价。
    """
    inventory: dict[str, str] = {}
    for char in (characters or {}).values():
        names = [strip_distinguisher(str((char or {}).get('canonical') or ''))]
        names += [str((a or {}).get('surface') or '').strip()
                  for a in ((char or {}).get('address') or [])]
        for name in names:
            key = norm(name)
            if key and key not in inventory:
                inventory[key] = name.strip()
    out: dict[int, list[str]] = {}
    past = ''
    for section in main_text:
        hits = sorted((past.find(key), key) for key in inventory if key in past)
        out[int(section.get('chapter_ordinal') or 0)] = \
            [inventory[key] for _pos, key in hits]
        past += norm(str(section.get('text') or ''))
    return out
