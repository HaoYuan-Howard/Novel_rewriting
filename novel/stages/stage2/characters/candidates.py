# -*- coding: utf-8 -*-
"""Characters v3 出现位置候选：确定性字面回填与嵌套预排除（0-LLM）。

Extract 只发现新 surface；本模块对已发现 surface 在原文中精确定位全部字面命中，
生成稳定 mention_id 与坐标。更长 surface 覆盖处的短 surface 候选标为
``nested_in_compound``，不进入 Resolve；残余歧义才交 lexicon Resolve。
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from contracts.schema.characters import (
    BookExtractResponse, CandidateSource, MentionCandidate,
)

NESTED_EXCLUDE_REASON = 'nested_in_compound'


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def mention_id_for(*, chapter_ordinal: int, start: int, end: int, surface: str) -> str:
    raw = f'{chapter_ordinal}:{start}:{end}:{surface}'
    return 'm_' + hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def find_literal_spans(text: str, surface: str) -> list[tuple[int, int]]:
    """在原文中找出 surface 的全部精确子串区间（码点半开区间）。"""
    if not surface or not text:
        return []
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        index = text.find(surface, start)
        if index < 0:
            break
        end = index + len(surface)
        spans.append((index, end))
        start = index + 1
    return spans


def surfaces_from_book_extract(extract: BookExtractResponse) -> list[str]:
    """保序去重：先出现的 surface 优先保留原字形。"""
    seen: set[str] = set()
    out: list[str] = []
    for chapter in extract.chapters:
        for raw in chapter.surfaces:
            surface = raw.strip()
            if not surface or surface in seen:
                continue
            seen.add(surface)
            out.append(surface)
    return out


def merge_surface_lexicon(*groups: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for raw in group:
            surface = (raw or '').strip()
            if not surface or surface in seen:
                continue
            seen.add(surface)
            out.append(surface)
    return out


def backfill_chapter_candidates(
    *,
    chapter_ordinal: int,
    text: str,
    surfaces: Sequence[str],
    source: CandidateSource = 'literal_backfill',
) -> list[MentionCandidate]:
    """对给定 surface 词表做本章字面回填。"""
    if type(chapter_ordinal) is not int or chapter_ordinal < 1:
        raise ValueError(f'非法 chapter_ordinal: {chapter_ordinal!r}')
    if not isinstance(text, str):
        raise ValueError(f'ch{chapter_ordinal} 正文必须是字符串')
    rows: list[MentionCandidate] = []
    for surface in surfaces:
        needle = surface.strip()
        if not needle:
            continue
        for start, end in find_literal_spans(text, needle):
            snippet = text[start:end]
            if snippet != needle:
                raise ValueError('字面回填切片必须逐字等于 surface')
            rows.append(MentionCandidate(
                mention_id=mention_id_for(
                    chapter_ordinal=chapter_ordinal, start=start, end=end,
                    surface=needle),
                chapter_ordinal=chapter_ordinal,
                start=start,
                end=end,
                surface=needle,
                text_sha256=_sha(snippet),
                source=source,
            ))
    rows.sort(key=lambda row: (row.start, row.end, row.surface))
    return rows


@dataclass(frozen=True)
class NestedExclusion:
    mention_id: str
    covered_by_mention_id: str
    reason: str = NESTED_EXCLUDE_REASON


def mark_nested_compound_exclusions(
    candidates: Sequence[MentionCandidate],
) -> tuple[list[MentionCandidate], list[NestedExclusion]]:
    """确定性预排除：被更长同章字面子串完全覆盖的短 surface。

    返回 (仍需 Resolve 的候选, 预排除记录)。预排除不进 Resolve wire。
    """
    by_chapter: dict[int, list[MentionCandidate]] = {}
    for row in candidates:
        by_chapter.setdefault(row.chapter_ordinal, []).append(row)

    resolve_rows: list[MentionCandidate] = []
    exclusions: list[NestedExclusion] = []
    excluded_ids: set[str] = set()

    for ordinal in sorted(by_chapter):
        rows = sorted(
            by_chapter[ordinal],
            key=lambda row: (-(row.end - row.start), row.start, row.end, row.surface),
        )
        for index, short in enumerate(rows):
            covered_by: MentionCandidate | None = None
            for longer in rows[:index]:
                if longer.end - longer.start <= short.end - short.start:
                    continue
                if longer.start <= short.start and short.end <= longer.end:
                    if longer.surface == short.surface:
                        continue
                    covered_by = longer
                    break
            if covered_by is None:
                resolve_rows.append(short)
                continue
            excluded_ids.add(short.mention_id)
            exclusions.append(NestedExclusion(
                mention_id=short.mention_id,
                covered_by_mention_id=covered_by.mention_id,
            ))

    resolve_rows.sort(key=lambda row: (
        row.chapter_ordinal, row.start, row.end, row.surface))
    exclusions.sort(key=lambda row: row.mention_id)
    return resolve_rows, exclusions


def compile_book_candidates(
    main_text: Sequence[Mapping[str, Any]],
    extract: BookExtractResponse,
    *,
    extra_surfaces: Sequence[str] = (),
) -> tuple[list[MentionCandidate], list[str], list[NestedExclusion]]:
    """全书：汇总 Extract surface → 字面回填 → 嵌套预排除。

    返回 (全部候选含预排除, lexicon, 预排除记录)。
    Resolve 只应对 ``mark_nested_compound_exclusions`` 后的残余 surface 词表。
    """
    by_chapter = {row.chapter_ordinal: row for row in extract.chapters}
    if len(by_chapter) != len(extract.chapters):
        raise ValueError('Extract 章号不得重复')
    texts: dict[int, str] = {}
    for section in main_text:
        ordinal = section.get('chapter_ordinal')
        text = section.get('text')
        if type(ordinal) is not int or ordinal < 1:
            raise ValueError(f'非法 chapter_ordinal: {ordinal!r}')
        if not isinstance(text, str):
            raise ValueError(f'ch{ordinal} 正文必须是字符串')
        if ordinal in texts:
            raise ValueError(f'章号重复：{ordinal}')
        texts[ordinal] = text
        if ordinal not in by_chapter:
            raise ValueError(f'缺少 ch{ordinal} 的 Extract')

    lexicon = merge_surface_lexicon(extra_surfaces, surfaces_from_book_extract(extract))
    candidates: list[MentionCandidate] = []
    seen_ids: set[str] = set()
    for ordinal in sorted(texts):
        for row in backfill_chapter_candidates(
                chapter_ordinal=ordinal, text=texts[ordinal], surfaces=lexicon):
            if row.mention_id in seen_ids:
                continue
            seen_ids.add(row.mention_id)
            candidates.append(row)
    candidates.sort(key=lambda row: (
        row.chapter_ordinal, row.start, row.end, row.surface))
    _resolve_rows, exclusions = mark_nested_compound_exclusions(candidates)
    return candidates, lexicon, exclusions


def distinct_surfaces_for_resolve(
    candidates: Sequence[MentionCandidate],
    exclusions: Sequence[NestedExclusion],
) -> list[str]:
    """未预排除候选的 distinct surface（保序）。"""
    excluded = {row.mention_id for row in exclusions}
    seen: set[str] = set()
    out: list[str] = []
    for row in sorted(
            candidates,
            key=lambda item: (item.chapter_ordinal, item.start, item.surface)):
        if row.mention_id in excluded:
            continue
        if row.surface in seen:
            continue
        seen.add(row.surface)
        out.append(row.surface)
    return out


__all__ = [
    'NESTED_EXCLUDE_REASON',
    'NestedExclusion',
    'backfill_chapter_candidates',
    'compile_book_candidates',
    'distinct_surfaces_for_resolve',
    'find_literal_spans',
    'mark_nested_compound_exclusions',
    'mention_id_for',
    'merge_surface_lexicon',
    'surfaces_from_book_extract',
]
