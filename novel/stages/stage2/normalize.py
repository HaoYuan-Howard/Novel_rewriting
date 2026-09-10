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

from contracts.schema.stage2 import Hinge

from novel.core.evidence import iter_global_evidence_slots
from novel.core.facts import first_reveal_chapter, setup_chapters_of
from novel.core.naming import strip_distinguisher
from novel.core.spans import (
    content_index, content_only, exact_span, extend_terminal_punct, locate_unique_span,
)
from novel.core.text import norm
from novel.stages.stage2.paragraphs import (
    ParagraphIndexError, paragraph_range_of, paragraph_slice, paragraph_spans, span_of,
)


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


class FactAtomRehome(NamedTuple):
    """一次由原文唯一性推出的事实证据改绑；成功态不重复写进 artifact。"""

    chapter_ordinal: int
    fact_id: str
    original_beat_ref: str
    resolved_beat_ref: str
    recovery: Literal['exact', 'normalized']


class BlueprintNormalizationAccounting(dict[str, Any]):
    """Blueprint 正式 provenance 的失败账，以及本次成功 rehome 的瞬时观察面。"""

    fact_atom_rehomes: tuple[FactAtomRehome, ...]

    def __init__(
        self,
        *,
        verbatim_failures: list[tuple[str, str, str]],
        fact_atom_failures: list[tuple[str, str, str]],
        fact_atom_rehomes: list[FactAtomRehome],
    ) -> None:
        super().__init__(
            verbatim_failures=verbatim_failures,
            fact_atom_failures=fact_atom_failures,
        )
        self.fact_atom_rehomes = tuple(fact_atom_rehomes)


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


def normalize_beat_ids(blueprints: list[dict[str, Any]]) -> None:
    for blueprint in blueprints:
        ordinal = int(blueprint.get('chapter_ordinal') or 0)
        for index, beat in enumerate(blueprint.get('beats') or [], 1):
            beat['beat_id'] = f'b{ordinal:02d}_{index:02d}'


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


def _locate_phrase(para_text: str, phrase: str) -> tuple[int, int, str] | None:
    """在**一段之内**定位 hinge 的短语，返回 `(起, 止, recovery)`；找不到返回 `None`。

    坐标是**段内**相对偏移，调用方负责加上段的起点。

    【为什么原样字节优先】标点可能正是重复字面的唯一消歧信息
    （`温夏白:[……]` 在内容空间里会坍缩成 `温夏白`）。模型已经抄对时按字节命中更精确；
    抄不对时才退回内容空间，那一条负责容忍换行、半角/全角标点。
    ——这条次序是从已删除的 `novel/core/spans.py::_find_exact_raw` 原样搬过来的，
    它压着一次实测故障，别为了少一个分支合并掉。

    ⚠⚠ **没有 LCS 兜底，而那是刻意的**：实测「传家玉镯」→「家传玉镯」的最长公共子串
    只剩「玉镯」2/4，救回来的是一个**语义已经变了**的片段。hinge 要的正是
    「位置对、字节错」这件事被报出来（`hinge_drift` / `hinge_anchor_void`）。
    ⚠ 搜索面只有这一段（本仓语料均段约 21 字）——**这正是本次改造省下的那笔钱**：
    模型不必再逐字转写一段能在整章里唯一定位的原文。
    """
    raw = phrase.strip()
    if raw:
        hit = para_text.find(raw)
        if hit >= 0:
            return hit, extend_terminal_punct(para_text, hit + len(raw)), 'exact'
    content, cmap = content_index(para_text)
    needle = content_only(phrase)
    if not needle or not cmap:
        return None
    at = content.find(needle)
    if at < 0:
        return None
    return cmap[at], extend_terminal_punct(para_text, cmap[at + len(needle) - 1] + 1), \
        'normalized'


def _source(text: str, start: int, end: int) -> dict[str, Any]:
    raw = text[start:end]
    return {
        'text': raw,
        'start': start,
        'end': end,
    }


def locate_fact_atom_source(
    chapter_text: str,
    phrase: str,
    declared_beat_ref: str,
    beats: list[dict[str, Any]],
) -> tuple[str | None, dict[str, Any] | None, str | None, str]:
    """按「声明拍优先、全章唯一才改绑」定位一条事实原子证据。

    返回 ``(beat_ref, source, recovery, cause)``。成功时 ``cause`` 为空；失败时
    前三格为空并给稳定根因。声明拍内已经唯一命中时维持模型分拍，即使同一句在
    别拍重复也不擅自改绑；只有声明拍内 0 命中，才扩大到全章，并要求命中区间
    完整落入**恰好一个**有效 beat。这样跨拍的内容空间命中、重叠坏区间和没有
    可用 span 的目标拍都不会被 ``find`` 首命中洗成成功。
    """
    ranges: list[tuple[str, int, int]] = []
    declared: tuple[int, int] | None = None
    for beat in beats:
        beat_ref = str(beat.get('beat_id') or '')
        span = beat.get('span')
        start = span.get('start') if isinstance(span, dict) else None
        end = span.get('end') if isinstance(span, dict) else None
        if type(start) is not int or type(end) is not int:
            continue
        if start < 0 or end <= start or end > len(chapter_text):
            continue
        ranges.append((beat_ref, start, end))
        if beat_ref == declared_beat_ref:
            declared = (start, end)

    if declared is None:
        return None, None, None, '所属拍没有可用 span'

    declared_start, declared_end = declared
    local = locate_unique_span(
        chapter_text[declared_start:declared_end], phrase)
    if isinstance(local, tuple):
        offset, tail, recovery = local
        start, end = declared_start + offset, declared_start + tail
        return declared_beat_ref, _source(chapter_text, start, end), recovery, ''
    if isinstance(local, int):
        return None, None, None, '证据短语在所属拍内多处命中'

    chapter_hit = locate_unique_span(chapter_text, phrase)
    if isinstance(chapter_hit, int):
        return None, None, None, '证据短语在本章多处命中'
    if chapter_hit is None:
        return None, None, None, '证据短语不在本章原文'

    start, end, recovery = chapter_hit
    owners = [
        beat_ref for beat_ref, beat_start, beat_end in ranges
        if beat_start <= start and end <= beat_end
    ]
    if len(owners) != 1:
        return None, None, None, '证据短语不能唯一归属一个 beat'
    return owners[0], _source(chapter_text, start, end), recovery, ''


def _blank_hinge(hinge: dict[str, Any]) -> None:
    """保留材料身份、事实关联与语义，把 source 打回明确失败态。

    ⚠ 用 `clear()` 而不是逐键 `pop`：响应形状的键（`para` / `phrase`）与产物形状的键
    （`source`）**不能同时留在盘上**——`StrictModel` 的 `extra='forbid'` 会拒收整份产物。
    """
    retained = {key: hinge[key] for key in Hinge.model_fields if key != 'source' and key in hinge}
    hinge.clear()
    hinge.update({**retained, 'source': None})


def _beat_span(text: str, span: dict[str, Any], cursor: int,
               table: list[tuple[int, int]]) -> tuple[int, int, int, str | None]:
    """一条 beat 的 span → `(起, 止, 新游标段号, 失败根因)`。

    两种入形，**同一个出口**：
      · 响应形 `{'start_para', 'end_para'}` —— 按段落表回取
      · 产物形 `SourceSpan`               —— `--renormalize` / INVALID 提升时已经是精确区间，
        照旧交给 `novel/core/spans.py::exact_span` 验（那是它唯一的校验落点）

    ⚠ **游标单调是在段号上判的**：第 n 条的起段必须**越过**第 n-1 条的止段。
    这一条同时表达了「区间重叠」与「顺序倒置」两件事——与旧实现「`starts_with` 只在上一条
    终点之后找」是同一个机制，只是现在它是一次整数比较而不是一次字符串搜索。
    """
    if any(key in span for key in ('start', 'end', 'text')):
        start, end, problem = exact_span(span, text, 0 if cursor <= 0 else table[cursor - 1][1])
        if problem:
            return 0, 0, cursor, '已落盘的精确区间与原文对不上'
        try:
            _a, b = paragraph_range_of(text, start, end)
        except ParagraphIndexError as exc:
            return 0, 0, cursor, str(exc)
        return start, end, b, None

    if 'start_para' not in span and 'end_para' not in span:
        return 0, 0, cursor, 'span 里既没有段号也不是正式区间'
    if not table:
        return 0, 0, cursor, '本章没有可编号的段落'

    start_para, end_para = span.get('start_para'), span.get('end_para')
    if start_para is not None and type(start_para) is int and start_para <= cursor:
        return 0, 0, cursor, '起段没有越过上一拍的止段（区间重叠或顺序倒置）'
    try:
        start, end = span_of(text, start_para, end_para)
    except ParagraphIndexError as exc:
        return 0, 0, cursor, str(exc)
    return start, end, int(end_para), None


def normalize_verbatim(blueprints: list[dict[str, Any]],
                       text_of: dict[int, str]) -> dict[str, list[tuple[Any, ...]]]:
    """把模型交的**段号**回取成正式 `SourceSpan`，只记录会阻断的失败。

    ⚠⚠ **这里曾经是「在原文里搜模型逐字抄回来的锚点」**（`bounded-fidelity-v2`）。
    换成段号之后，beat 的区间是一次整数查表 ⇒ `recovery` 恒 `exact`、
    「锚点不是原文字节」这件事在 beat 这一侧**结构上不存在**了。
    模型仍要转写的只剩 `hinge.phrase`，而它只需要在**一段之内**可定位。
    """
    failures: list[tuple[str, str, str]] = []
    #: `ordinal -> [(beat, 起, 止, 起段, 止段)]`，只装定位成功的。
    located: dict[int, list[tuple[dict[str, Any], int, int, int, int]]] = {}

    for blueprint in blueprints:
        ordinal = int(blueprint.get('chapter_ordinal') or 0)
        text = text_of.get(ordinal, '')
        table = paragraph_spans(text)
        cursor = 0
        for beat in (blueprint.get('beats') or []):
            beat_id = str(beat.get('beat_id') or '')
            span = beat.get('span') or {}
            start, end, cursor, problem = _beat_span(text, span, cursor, table)
            if problem:
                failures.append((beat_id, 'span', problem))
                beat['span'] = None
                for hinge in (beat.get('hinges') or []):
                    _blank_hinge(hinge)
                continue
            first, last = paragraph_range_of(text, start, end)
            beat['span'] = _source(text, start, end)
            located.setdefault(ordinal, []).append((beat, start, end, first, last))

    for ordinal, items in located.items():
        chapter_text = text_of.get(ordinal, '')
        #: 段号 → 拥有它的 beat_id。段区间由游标单调保证**互不重叠** ⇒ 归属唯一，
        #: 旧实现那条「在多个 beat 里都命中、归属不唯一」的失败态因此不复存在。
        owner_of: dict[int, str] = {}
        for beat, _s, _e, first, last in items:
            for para in range(first, last + 1):
                owner_of[para] = str(beat.get('beat_id') or '')
        fresh: dict[str, list[dict[str, Any]]] = {
            str(beat.get('beat_id') or ''): [] for beat, *_rest in items}

        for beat, start, end, _first, _last in items:
            beat_id = str(beat.get('beat_id') or '')
            for hinge in (beat.get('hinges') or []):
                if 'source' in hinge:
                    # `--renormalize` / INVALID 提升：已经是正式区间，只验不重定位。
                    h_start, h_end, problem = exact_span(hinge.get('source'), chapter_text)
                    if problem or h_start < start or h_end > end:
                        _blank_hinge(hinge)
                        fresh[beat_id].append(hinge)
                        failures.append((
                            beat_id, 'hinge', '已落盘的 source 与它所属 beat 的区间对不上'))
                    else:
                        fresh[beat_id].append(hinge)
                    continue

                para, phrase = hinge.get('para'), str(hinge.get('phrase') or '')
                try:
                    p_start, p_end = paragraph_slice(chapter_text, para)
                except ParagraphIndexError:
                    _blank_hinge(hinge)
                    fresh[beat_id].append(hinge)
                    failures.append((beat_id, 'hinge', '段号越界，本章没有那一段'))
                    continue

                owner = owner_of.get(int(para))
                if owner is None:
                    _blank_hinge(hinge)
                    fresh[beat_id].append(hinge)
                    failures.append((
                        beat_id, 'hinge', '段号落在没有被任何 beat 覆盖的空档'))
                    continue

                hit = _locate_phrase(chapter_text[p_start:p_end], phrase)
                if hit is None:
                    # 【分清「编的」与「段号指错」】两者的修法不同，说错了会让人去查一句
                    # 原文里明明有的话。整章都找不到才是「转述或编的」。
                    elsewhere = content_only(phrase) and \
                        content_only(phrase) in content_only(chapter_text)
                    _blank_hinge(hinge)
                    fresh[beat_id].append(hinge)
                    failures.append((beat_id, 'hinge',
                                     '短语不在它指定的那一段里，而出现在别处' if elsewhere
                                     else '短语不在本章原文里'))
                    continue

                offset, tail, _method = hit
                h_start, h_end = p_start + offset, p_start + tail
                _blank_hinge(hinge)
                hinge['source'] = _source(chapter_text, h_start, h_end)
                fresh[owner].append(hinge)

        for beat, *_rest in items:
            beat['hinges'] = fresh[str(beat.get('beat_id') or '')]

    return {'verbatim_failures': failures}


def normalize_fact_atom_sources(
    blueprints: list[dict[str, Any]],
    text_of: dict[int, str],
    *,
    previous_failures: list[tuple[str, str, str]] = (),
    evidence_phrases: dict[str, str] | None = None,
) -> tuple[list[tuple[str, str, str]], list[FactAtomRehome]]:
    """重验正式 fact atom 证据，并用已对证的短语作确定性 rehome。

    ``evidence_phrases`` 不是人工自由输入：生产调用方只从当前 compiler 的响应，
    或与 INVALID 业务章逐字段闭合的已验证 checkpoint 取得。失败 artifact 本身只留
    ``source=None``，所以这里显式接收恢复材料；拿 claim 猜 locator 会把转述当证据，
    是本函数刻意不做的事。
    """
    locators = evidence_phrases or {}
    prior_by_fact: dict[str, list[tuple[str, str, str]]] = {}
    for row in previous_failures:
        if len(row) == 3:
            prior_by_fact.setdefault(str(row[0]), []).append(tuple(row))

    failures: list[tuple[str, str, str]] = []
    rehomes: list[FactAtomRehome] = []
    seen_facts: set[str] = set()
    for blueprint in blueprints:
        ordinal = int(blueprint.get('chapter_ordinal') or 0)
        chapter_text = text_of.get(ordinal, '')
        beats = list(blueprint.get('beats') or [])
        ranges = {
            str(beat.get('beat_id') or ''): (
                (beat.get('span') or {}).get('start'),
                (beat.get('span') or {}).get('end'),
            )
            for beat in beats
        }
        for atom in (blueprint.get('fact_atoms') or []):
            fact_id = str(atom.get('fact_id') or '')
            beat_ref = str(atom.get('source_beat_ref') or '')
            seen_facts.add(fact_id)
            source = atom.get('source')
            if source is not None:
                start, end, problem = exact_span(source, chapter_text)
                beat_range = ranges.get(beat_ref)
                in_declared = (
                    isinstance(beat_range, tuple)
                    and type(beat_range[0]) is int
                    and type(beat_range[1]) is int
                    and beat_range[0] <= start
                    and end <= beat_range[1]
                )
                if problem is None and in_declared:
                    # 重新落一次 `_source`，确保正式字节只来自当前 main_text。
                    atom['source'] = _source(chapter_text, start, end)
                    continue
                atom['source'] = None
                failures.append((
                    fact_id, beat_ref,
                    '已落盘的 source 与它声明的 beat 区间对不上',
                ))
                continue

            phrase = locators.get(fact_id)
            if not isinstance(phrase, str) or not phrase.strip():
                old = prior_by_fact.get(fact_id)
                failures.extend(old or [(
                    fact_id, beat_ref, '事实原子 source 为空且没有可恢复证据短语',
                )])
                continue
            resolved_ref, resolved_source, recovery, cause = locate_fact_atom_source(
                chapter_text, phrase, beat_ref, beats)
            if resolved_ref is None or resolved_source is None or recovery is None:
                failures.append((fact_id, beat_ref, cause))
                continue
            atom['source_beat_ref'] = resolved_ref
            atom['source'] = resolved_source
            if resolved_ref != beat_ref:
                rehomes.append(FactAtomRehome(
                    chapter_ordinal=ordinal,
                    fact_id=fact_id,
                    original_beat_ref=beat_ref,
                    resolved_beat_ref=resolved_ref,
                    recovery=recovery,
                ))

    # 删除失败 atom 不能顺手洗掉不可变失败账；人工业务修改应走有审计的 typed 路。
    for fact_id, rows in prior_by_fact.items():
        if fact_id not in seen_facts:
            failures.extend(rows)
    return failures, rehomes


def normalize_blueprints(
    data: dict[str, Any],
    main_text: list[dict[str, Any]],
    *,
    previous_fact_atom_failures: list[tuple[str, str, str]] = (),
    evidence_phrases: dict[str, str] | None = None,
) -> tuple[dict[str, Any], BlueprintNormalizationAccounting]:
    """重验正式蓝图中的证据区间；人物 id 不做第二次名字反解。"""
    blueprints = list(data.get('blueprints') or [])
    data['blueprints'] = blueprints
    normalize_beat_ids(blueprints)
    text_of = {int(section.get('chapter_ordinal') or 0): str(section.get('text') or '')
               for section in main_text}
    verbatim = normalize_verbatim(blueprints, text_of)
    fact_failures, rehomes = normalize_fact_atom_sources(
        blueprints,
        text_of,
        previous_failures=previous_fact_atom_failures,
        evidence_phrases=evidence_phrases,
    )
    return data, BlueprintNormalizationAccounting(
        verbatim_failures=list(verbatim['verbatim_failures']),
        fact_atom_failures=fact_failures,
        fact_atom_rehomes=rehomes,
    )
