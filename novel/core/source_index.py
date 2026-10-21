"""story-v1 原文索引：代码生成证据段，纯只读定位，不解释小说。

查询只接受已冻结的 SourceIndex，不访问磁盘、网络、环境变量或最新观察。
结果保留命中分母与截断事实；空命中不证明某项前因不存在。邻段是上下文，
只有显式引用的段才是语义条目的证据。索引是 Stage1 原文的派生物。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Mapping, Sequence

from contracts.schema.source_story import SourceEvidence, STORY_VERSION


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class SourceQueryResult:
    source_identity: str
    index_version: str
    query: Mapping[str, Any]
    segments: Mapping[str, SourceEvidence]
    total_hits: int
    status: Literal['complete', 'truncated', 'empty']
    next_cursor: int | None
    manifest_sha256: str

    def material(self) -> dict[str, Any]:
        return {
            'source_identity': self.source_identity, 'index_version': self.index_version,
            'query': dict(self.query),
            'segments': {key: value.model_dump(mode='json') for key, value in self.segments.items()},
            'total_hits': self.total_hits, 'status': self.status,
            'next_cursor': self.next_cursor, 'manifest_sha256': self.manifest_sha256,
        }


@dataclass(frozen=True)
class SourceIndex:
    source_identity: str
    content_sha256: str
    chapter_ordinals: tuple[int, ...]
    evidence: Mapping[str, SourceEvidence]
    index_version: str = STORY_VERSION

    def chapter(self, chapter_ordinal: int, *, include_prelude: bool = True) -> dict[str, SourceEvidence]:
        if chapter_ordinal not in self.chapter_ordinals:
            raise ValueError(f'源章不存在：{chapter_ordinal}')
        return {key: value for key, value in self.evidence.items()
                if value.chapter_ordinal == chapter_ordinal
                or (include_prelude and chapter_ordinal == 1 and value.chapter_ordinal == 0)}


def build_source_index(
    chapters: Sequence[Mapping[str, Any]], hook_copy: str = '', *, source_identity: str | None = None,
) -> SourceIndex:
    """按源阅读序派发自然段 ID；不排序补章、不吞空章，不改原文段内字节。"""
    if not isinstance(hook_copy, str):
        raise ValueError('源引子必须是字符串，合法无引子使用空字符串')
    if (not isinstance(chapters, Sequence) or isinstance(chapters, (str, bytes))
            or any(not isinstance(chapter, Mapping)
                   or not {'chapter_ordinal', 'text'} <= chapter.keys() for chapter in chapters)):
        raise ValueError('源章必须是含chapter_ordinal/text的数组')
    if any(isinstance(chapter['chapter_ordinal'], bool)
           or not isinstance(chapter['chapter_ordinal'], int) for chapter in chapters):
        raise ValueError('源章序必须为整数，不能用布尔值或字符串代替')
    if source_identity is not None and (not isinstance(source_identity, str) or not source_identity.strip()):
        raise ValueError('显式源身份必须为非空字符串，不能用缺值推导规则掩盖坏身份')
    ordinals = tuple(chapter['chapter_ordinal'] for chapter in chapters)
    if ordinals != tuple(range(1, len(chapters) + 1)) or not chapters:
        raise ValueError('源章必须按阅读序为非空的 1..N 密集数组')
    records = []
    if hook_copy.strip():
        records.append((0, hook_copy))
    records.extend((chapter['chapter_ordinal'], chapter['text']) for chapter in chapters)
    evidence: dict[str, SourceEvidence] = {}
    for chapter, text in records:
        if not isinstance(text, str):
            raise ValueError(f'第{chapter}章正文必须是字符串')
        paragraphs = [line for line in text.splitlines() if line.strip()]
        if not paragraphs:
            raise ValueError(f'第{chapter}章没有非空自然段')
        for number, paragraph in enumerate(paragraphs, 1):
            evidence[f'e{chapter:03d}_{number:05d}'] = SourceEvidence(
                chapter_ordinal=chapter, paragraph_ordinal=number, text=paragraph)
    content_sha = _digest({'chapters': [
        {'chapter_ordinal': chapter['chapter_ordinal'], 'text': chapter['text']} for chapter in chapters
    ], 'hook_copy': hook_copy})
    return SourceIndex(source_identity if source_identity is not None else content_sha, content_sha, ordinals,
                       MappingProxyType(evidence))


def _result(index: SourceIndex, query: dict[str, Any], refs: Sequence[str], *,
            total: int, next_cursor: int | None) -> SourceQueryResult:
    segments = {ref: index.evidence[ref] for ref in refs}
    status = 'empty' if not total else ('truncated' if next_cursor is not None else 'complete')
    payload = {
        'source_identity': index.source_identity, 'index_version': index.index_version,
        'content_sha256': index.content_sha256, 'query': query,
        'segments': {ref: seg.model_dump(mode='json') for ref, seg in segments.items()},
        'total_hits': total, 'status': status, 'next_cursor': next_cursor,
    }
    return SourceQueryResult(index.source_identity, index.index_version, MappingProxyType(query),
                             MappingProxyType(segments), total, status, next_cursor, _digest(payload))


def read_segments(index: SourceIndex, segment_ids: Sequence[str], *, adjacent_paragraphs: int = 0,
                  max_chapter: int | None = None) -> SourceQueryResult:
    """精确闭集读取；任何越权或未知段整体拒绝，不返回貌似完整的部分结果。"""
    if adjacent_paragraphs < 0:
        raise ValueError('邻段数不得为负')
    for ref in segment_ids:
        if ref not in index.evidence:
            raise ValueError(f'证据段不存在：{ref}')
        if max_chapter is not None and index.evidence[ref].chapter_ordinal > max_chapter:
            raise ValueError(f'证据段超出查询章范围：{ref}')
    requested = [index.evidence[ref] for ref in dict.fromkeys(segment_ids)]
    refs = [ref for ref, segment in index.evidence.items()
            if any(segment.chapter_ordinal == seed.chapter_ordinal
                   and abs(segment.paragraph_ordinal - seed.paragraph_ordinal) <= adjacent_paragraphs
                   for seed in requested)]
    query = {'operation': 'read_segments', 'segment_ids': list(segment_ids),
             'adjacent_paragraphs': adjacent_paragraphs, 'max_chapter': max_chapter}
    return _result(index, query, refs, total=len(refs), next_cursor=None)


def search_literal(index: SourceIndex, terms: Sequence[str], *, mode: Literal['all', 'any'] = 'all',
                   chapter_range: tuple[int, int] | None = None, cursor: int = 0,
                   limit: int = 20) -> SourceQueryResult:
    """按原文阅读序进行逐字查询；无模糊匹配，无语义同一性推断。"""
    if not terms or any(not isinstance(term, str) or not term for term in terms):
        raise ValueError('查询词必须是非空字符串数组')
    if mode not in ('all', 'any') or cursor < 0 or limit < 1:
        raise ValueError('查询 mode/cursor/limit 非法')
    if chapter_range is not None and not (0 <= chapter_range[0] <= chapter_range[1]):
        raise ValueError('查询章范围非法')
    hits = []
    for ref, segment in index.evidence.items():
        if chapter_range is not None and not chapter_range[0] <= segment.chapter_ordinal <= chapter_range[1]:
            continue
        matches = [term in segment.text for term in terms]
        if (all(matches) if mode == 'all' else any(matches)):
            hits.append(ref)
    if cursor > len(hits):
        raise ValueError('cursor 超出查询命中分母')
    selected = hits[cursor:cursor + limit]
    following = cursor + len(selected)
    query = {'operation': 'search_literal', 'terms': list(terms), 'mode': mode,
             'chapter_range': list(chapter_range) if chapter_range is not None else None,
             'cursor': cursor, 'limit': limit}
    return _result(index, query, selected, total=len(hits),
                   next_cursor=following if following < len(hits) else None)


__all__ = ['SourceIndex', 'SourceQueryResult', 'build_source_index', 'read_segments', 'search_literal']
