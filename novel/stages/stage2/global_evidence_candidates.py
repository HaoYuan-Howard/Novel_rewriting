#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stage2.global 的段落证据闭集：下发与回取共用同一张表。

这张表不判断一段原文是否真的支持某条主张；它只消灭模型同时转抄章号和 locator
造成的坐标错位。候选行只由 :func:`paragraph_spans` 产生，prompt 标记与正式
``SourceSpan`` 又只从候选行产生。两侧各切一次原文会重新引入本模块要消灭的静默错位。

候选 ID 是一次请求内的不透明 wire 引用，不进入正式 ``GlobalGrounding``。模型响应
通过本模块编译成既有 ``FactEvidence`` 后，仍须走现有 normalize/check/人工语义复核。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from contracts.schema.stage2 import (
    FactEvidence,
    GlobalGroundingCandidateResponse,
    SourceSpan,
)
from novel.core.evidence import iter_global_evidence_slots
from novel.stages.stage2.paragraphs import paragraph_spans


EVIDENCE_CANDIDATE_FORMAT = '<{evidence_candidate_id}> {text}'
_MAX_CANDIDATES = 999_999


@dataclass(frozen=True)
class GlobalEvidenceCandidate:
    """候选表的一行；坐标始终是所属章内 Python 码点半开区间。"""

    evidence_candidate_id: str
    chapter_ordinal: int
    text: str
    start: int
    end: int

    def source_span(self) -> dict[str, Any]:
        """返回一份新的正式 SourceSpan 字典，绝不把 prompt 标记带进去。"""
        return SourceSpan.model_validate({
            'text': self.text,
            'start': self.start,
            'end': self.end,
        }).model_dump(mode='json')


@dataclass(frozen=True)
class GlobalEvidenceCandidateTable:
    """一次 global 请求的全书候选表。

    ``rows`` 是唯一业务源；``render`` 与 ``lookup`` 都只消费它。私有索引只是同一
    表的只读加速视图，不保存第二份可独立漂移的候选内容。
    """

    rows: tuple[GlobalEvidenceCandidate, ...]
    _by_id: Mapping[str, GlobalEvidenceCandidate] = field(
        init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        by_id: dict[str, GlobalEvidenceCandidate] = {}
        for index, row in enumerate(self.rows, start=1):
            expected = _candidate_id(index)
            if row.evidence_candidate_id != expected:
                raise ValueError(
                    '证据候选 ID 必须按全书阅读序连续编号：'
                    f'第 {index} 行是 {row.evidence_candidate_id!r}，应为 {expected!r}')
            if row.evidence_candidate_id in by_id:
                raise ValueError(f'证据候选 ID 重复：{row.evidence_candidate_id}')
            if type(row.chapter_ordinal) is not int or row.chapter_ordinal < 1:
                raise ValueError(
                    f'{row.evidence_candidate_id}: chapter_ordinal 必须是正整数')
            # 构造表本身也关闭式守正式 span；编译时会再铸成新的 dict，调用方不能
            # 通过修改返回值污染候选表。
            row.source_span()
            by_id[row.evidence_candidate_id] = row
        object.__setattr__(self, '_by_id', MappingProxyType(by_id))

    @classmethod
    def build(
        cls,
        main_text: Sequence[Mapping[str, Any]],
    ) -> GlobalEvidenceCandidateTable:
        """从 MainText 阅读序逐章建立全书唯一候选表。"""
        rows: list[GlobalEvidenceCandidate] = []
        for section in main_text:
            ordinal = section.get('chapter_ordinal')
            text = section.get('text')
            if type(ordinal) is not int or ordinal < 1:
                raise ValueError(
                    f'证据候选表遇到非法 chapter_ordinal：{ordinal!r}')
            if not isinstance(text, str):
                raise ValueError(f'ch{ordinal} 正文必须是字符串')
            for start, end in paragraph_spans(text):
                index = len(rows) + 1
                rows.append(GlobalEvidenceCandidate(
                    evidence_candidate_id=_candidate_id(index),
                    chapter_ordinal=ordinal,
                    text=text[start:end],
                    start=start,
                    end=end,
                ))
        return cls(rows=tuple(rows))

    def render(self) -> str:
        """渲染可直接放入 prompt 的带章界候选正文。"""
        parts: list[str] = []
        current_chapter: int | None = None
        for row in self.rows:
            if row.chapter_ordinal != current_chapter:
                if parts:
                    parts.append('')
                current_chapter = row.chapter_ordinal
                parts.append(f'【第{current_chapter}章】')
            parts.append(EVIDENCE_CANDIDATE_FORMAT.format(
                evidence_candidate_id=row.evidence_candidate_id,
                text=row.text,
            ))
        return '\n'.join(parts)

    def sha256(self) -> str:
        """候选表的全宽身份；render、lookup、patch 与恢复只认这一把尺子。

        文本、坐标、章号与 ID 全部参与 canonical JSON。只 hash ``render()`` 会漏掉
        坐标；只 hash ID 会让同一个 ID 在 MainText 漂移后仍被误认成旧候选。
        """
        payload = [
            {
                'evidence_candidate_id': row.evidence_candidate_id,
                'chapter_ordinal': row.chapter_ordinal,
                'text': row.text,
                'start': row.start,
                'end': row.end,
            }
            for row in self.rows
        ]
        raw = json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False,
        ).encode('utf-8')
        return hashlib.sha256(raw).hexdigest()

    def lookup(self, evidence_candidate_id: str) -> GlobalEvidenceCandidate:
        """按关闭式 ID 取候选；不解析 ID、不猜相邻行。"""
        try:
            return self._by_id[evidence_candidate_id]
        except (KeyError, TypeError):
            raise ValueError(
                f'证据候选不在本次下发闭集：{evidence_candidate_id!r}') from None

    def resolve(self, evidence_candidate_id: str) -> GlobalEvidenceCandidate:
        """``lookup`` 的语义别名，供响应编译调用。"""
        return self.lookup(evidence_candidate_id)


def build_global_evidence_candidates(
    main_text: Sequence[Mapping[str, Any]],
) -> GlobalEvidenceCandidateTable:
    """函数式入口；调用方无需知道表的构造细节。"""
    return GlobalEvidenceCandidateTable.build(main_text)


def compile_candidate_evidence(
    response: dict[str, Any],
    table: GlobalEvidenceCandidateTable,
) -> dict[str, Any]:
    """candidate-only 响应 → 全部 evidence 槽均为正式 FactEvidence 的字典。

    输入先过独立 candidate-only schema；因此缺字段、locator 混入、fate/evidence
    不一致都会在回取前关闭式失败。格式合法但未下发的 ID 再由 ``table.lookup``
    关闭式失败。函数深拷贝且不修改模型响应或候选表。
    """
    try:
        validated = GlobalGroundingCandidateResponse.model_validate(response)
    except ValueError as exc:
        raise ValueError(f'candidate-only global 响应不完整或不合法：{exc}') from exc

    compiled = validated.model_dump(by_alias=True, mode='json')
    for claim_path, owner, evidence_field in iter_global_evidence_slots(compiled):
        evidence = owner.get(evidence_field)
        if not isinstance(evidence, dict):
            raise ValueError(f'{claim_path}: 缺少 evidence_candidate_id')
        candidate_id = evidence.get('evidence_candidate_id')
        if not isinstance(candidate_id, str):
            raise ValueError(f'{claim_path}: 缺少 evidence_candidate_id')
        candidate = table.lookup(candidate_id)
        owner[evidence_field] = FactEvidence.model_validate({
            'chapter_ordinal': candidate.chapter_ordinal,
            'source': candidate.source_span(),
        }).model_dump(mode='json')

    # 新增 evidence 槽却忘记登记进统一遍历器时，不许把 wire ID 静默带进正式面。
    if _contains_candidate_ref(compiled):
        raise ValueError('candidate-only 响应仍有未登记的 evidence_candidate_id 槽')
    return compiled


def _candidate_id(index: int) -> str:
    if not 1 <= index <= _MAX_CANDIDATES:
        raise ValueError(
            f'全书证据候选超过 {_MAX_CANDIDATES} 条，六位 ID 无法无歧义表示')
    return f'e{index:06d}'


def _contains_candidate_ref(value: object) -> bool:
    if isinstance(value, dict):
        if 'evidence_candidate_id' in value:
            return True
        return any(_contains_candidate_ref(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_candidate_ref(item) for item in value)
    return False
