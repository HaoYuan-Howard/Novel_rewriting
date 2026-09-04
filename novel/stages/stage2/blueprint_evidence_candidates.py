#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 blueprint 的章内段落证据闭集。

当前章正文原本就按段下发；本模块只把同一行的段号同时铸成短 candidate id，
不复制第二份候选文本。模型只返回 ID，compiler 再从同一张表回取精确 SourceSpan。
候选只解决转抄与坐标问题，不判断该段是否真的支持 claim；既有事实语义复核不变。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from contracts.schema.stage2 import SourceSpan
from novel.stages.stage2.paragraphs import paragraph_spans


_MAX_CANDIDATES = 999


@dataclass(frozen=True)
class BlueprintEvidenceCandidate:
    candidate_id: str
    para: int
    text: str
    start: int
    end: int

    def source_span(self) -> dict[str, Any]:
        return SourceSpan.model_validate({
            'text': self.text,
            'start': self.start,
            'end': self.end,
        }).model_dump(mode='json')


@dataclass(frozen=True)
class BlueprintEvidenceCandidateTable:
    """一章的一张不可变候选表；渲染与 lookup 共用 ``rows``。"""

    rows: tuple[BlueprintEvidenceCandidate, ...]
    _by_id: Mapping[str, BlueprintEvidenceCandidate] = field(
        init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        by_id: dict[str, BlueprintEvidenceCandidate] = {}
        for index, row in enumerate(self.rows, start=1):
            expected = _candidate_id(index)
            if row.candidate_id != expected or row.para != index:
                raise ValueError(
                    'blueprint 证据候选必须与章内段号一一对应且连续')
            row.source_span()
            by_id[row.candidate_id] = row
        object.__setattr__(self, '_by_id', MappingProxyType(by_id))

    @classmethod
    def build(cls, text: str) -> 'BlueprintEvidenceCandidateTable':
        spans = paragraph_spans(text)
        if len(spans) > _MAX_CANDIDATES:
            raise ValueError(
                f'单章段落超过 {_MAX_CANDIDATES}，三位证据候选无法无歧义表示')
        return cls(rows=tuple(
            BlueprintEvidenceCandidate(
                candidate_id=_candidate_id(index), para=index,
                text=text[start:end], start=start, end=end,
            )
            for index, (start, end) in enumerate(spans, start=1)
        ))

    def render(self) -> str:
        """正文只出现一次；``<段号|候选ID>`` 标记不属于原文。"""
        return '\n'.join(
            f'<{row.para}|{row.candidate_id}> {row.text}' for row in self.rows)

    def lookup(self, candidate_id: str) -> BlueprintEvidenceCandidate:
        try:
            return self._by_id[candidate_id]
        except (KeyError, TypeError):
            raise ValueError(
                f'blueprint 证据候选不在本章关闭式集合：{candidate_id!r}') from None

    def candidate_for_span(
        self, start: object, end: object,
    ) -> BlueprintEvidenceCandidate | None:
        if (type(start) is not int or type(end) is not int
                or start < 0 or end <= start):
            return None
        matches = [row for row in self.rows
                   if row.start <= start and end <= row.end]
        return matches[0] if len(matches) == 1 else None


def tables_by_chapter(
    main_text: list[dict[str, Any]],
) -> dict[int, BlueprintEvidenceCandidateTable]:
    tables: dict[int, BlueprintEvidenceCandidateTable] = {}
    for section in main_text:
        ordinal = section.get('chapter_ordinal')
        text = section.get('text')
        if type(ordinal) is not int or ordinal < 1 or not isinstance(text, str):
            raise ValueError('blueprint 证据候选遇到非法 MainText 章节')
        tables[ordinal] = BlueprintEvidenceCandidateTable.build(text)
    return tables


def _candidate_id(index: int) -> str:
    if not 1 <= index <= _MAX_CANDIDATES:
        raise ValueError('blueprint 证据候选序号越界')
    return f'e{index:03d}'


__all__ = [
    'BlueprintEvidenceCandidate', 'BlueprintEvidenceCandidateTable',
    'tables_by_chapter',
]
