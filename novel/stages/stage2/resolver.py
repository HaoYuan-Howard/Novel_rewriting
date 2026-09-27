#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""resolver —— Stage2 证据 Resolver（B2；句段闭集选择与逐字锚定位）。

【职责边界】Resolver 只**定位与查找**，不做任何语义判断（S2-3／I-20）：
  • 句段 id 闭集选择——引用的段 id 必须在给定闭集内，不猜、不模糊匹配；
  • 逐字锚唯一定位——称谓、原话等需要字面锚的材料在**所引句段内**逐字
    定位；重复出现、不存在、跨段冲突都产生精确 issue（带 code 与上下文），
    不模糊匹配、不猜笔误（B2 验收：重复引文、不存在证据、错误嵌套都有
    精确报告，修复走 owner patch 或定向修订）；
  • 称谓 `from_chapter` 锚点派生——沿用 `R-2.10`：锚点段＝所引证据段中逐字
    包含该称呼的最早一段，章前文字（e00）锚点映射到第 1 章（D12/I-15：
    可推导坐标归 compiler，模型不给窗口）。

理解原文、判断同一性与关联归 semantic unit（book／chapters／associate）；
本模块只回答「这个字面在哪、这个 id 在不在」。validator 不修改输入：全部
API 只读，问题聚合成结构化 issue 返回（I-24/I-36）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

import re

#: 证据段 id 的章序：`e00_*`＝章前文字（0），`eNN_*`＝第 NN 章（形状非法即抛，
#: 与 `build_evidence` 的切分约定同一张表）。
_EVIDENCE_REF = re.compile(r'e(\d+)_\d+')


def chapter_of_evidence(ref: str) -> int:
    """证据段 id → 章序；形状非法即抛（不猜）。"""
    m = _EVIDENCE_REF.fullmatch(ref)
    if m is None:
        raise ValueError(f'证据段 id 形状非法：{ref!r}')
    return int(m.group(1))


#: 精确 issue 的 code 闭集。
ISSUE_CODES: tuple[str, ...] = (
    'unknown_segment',    # 引用的句段 id 不在给定闭集内
    'missing_literal',    # 逐字锚在所引句段内一处都没有
    'ambiguous_literal',  # 逐字锚在所引句段内出现多次（重复引文/无法唯一定位）
    'anchor_conflict',    # 逐字锚命中分散在所引的多个句段（定位冲突）
)


@dataclass(frozen=True)
class ResolverIssue:
    """精确定位 issue：code 可分派、message 可读、context 逐项给证据。

    Resolver 不模糊匹配、不猜笔误：每一条 issue 都指名是哪个字面、查过哪些
    句段、命中（或冲突）在哪里，修复者不用再猜一次。
    """

    code: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AnchorHit:
    """一次唯一定位：句段 id、所属章序（0＝章前文字）与字内偏移（compiler
    计算的机械坐标，I-21 允许且仅允许 compiler 产生）。"""

    segment_id: str
    chapter: int
    offset: int


class EvidenceResolver:
    """一个证据句段闭集上的定位器。

    `segments` 是 `novel/stages/chain.py::build_evidence` 的切分形状：
    ``{段id: {'chapter': int, 'text': str, 'pre_chapter'?: bool}}``。只给
    id 不给段文本的合成证据面（单元测试）支持降级派生（见 `surface_chapter`），
    但逐字锚定位在该形态下明确不可用——没有文本就报 `unknown_segment`，
    不假装定位过。
    """

    def __init__(self, segments: Mapping[str, Mapping[str, Any]] | Iterable[str]):
        if not isinstance(segments, Mapping):
            segments = {ref: {} for ref in segments}
        self._segments: dict[str, dict[str, Any]] = {
            str(k): dict(v) for k, v in segments.items()}

    @property
    def segment_ids(self) -> frozenset[str]:
        """句段 id 闭集（唯一权威；引用面只认它）。"""
        return frozenset(self._segments)

    def _text(self, ref: str) -> str:
        return self._segments.get(ref, {}).get('text') or ''

    # ── 闭集选择 ────────────────────────────────────────────────────────────

    def check_closed_set(self, refs: Iterable[str]) -> list[ResolverIssue]:
        """所引句段 id 逐个对闭集核对；逐条精确 issue，不做集合模糊差。"""
        issues: list[ResolverIssue] = []
        for ref in refs:
            if ref not in self._segments:
                issues.append(ResolverIssue(
                    code='unknown_segment',
                    message=f'引用了闭集之外的句段 id：{ref!r}',
                    context={'segment_id': ref}))
        return issues

    # ── 逐字锚唯一定位 ──────────────────────────────────────────────────────

    def locate(self, literal: str, refs: Iterable[str]) -> tuple[AnchorHit | None,
                                                               list[ResolverIssue]]:
        """在**所引句段内**逐字定位一个字面锚（称谓、原话）。

        只做逐字子串匹配：不做归一化、不做模糊匹配、不猜笔误。命中 0 处 ⇒
        `missing_literal`；同一字面在所引句段内出现多于一次 ⇒
        `ambiguous_literal`（重复引文，context 列出全部命中）——唯一定位
        要求恰好一处。issues 非空时首返回值为 None。
        """
        problems = self.check_closed_set(refs)
        if problems:
            return None, problems
        hits: list[AnchorHit] = []
        for ref in refs:
            text = self._text(ref)
            start = 0
            while True:
                offset = text.find(literal, start)
                if offset < 0:
                    break
                hits.append(AnchorHit(
                    segment_id=ref, chapter=int(self._segments[ref].get('chapter', 0)),
                    offset=offset))
                start = offset + max(len(literal), 1)
        if not hits:
            return None, [ResolverIssue(
                code='missing_literal',
                message=f'字面锚 {literal!r} 在所引句段内不存在'
                        f'（source_refs={list(refs)}）',
                context={'literal': literal, 'source_refs': list(refs)})]
        if len(hits) > 1:
            return None, [ResolverIssue(
                code='ambiguous_literal',
                message=f'字面锚 {literal!r} 在所引句段内出现 {len(hits)} 次，'
                        '无法唯一定位（重复引文须并入同一处或收窄证据）',
                context={'literal': literal, 'source_refs': list(refs),
                         'hits': [{'segment_id': h.segment_id, 'chapter': h.chapter,
                                   'offset': h.offset} for h in hits]})]
        return hits[0], []

    # ── 称谓窗口锚点（R-2.10） ───────────────────────────────────────────────

    def surface_chapter(self, surface: str,
                        refs: Iterable[str]) -> tuple[int | None, ResolverIssue | None]:
        """称谓 `from_chapter` 的锚点派生（`R-2.10`；D12/I-15）。

        锚点段＝所引证据段中**逐字包含该 surface** 的最早一段；章前锚点
        （e00，chapter=0）映射到第 1 章（阅读序从 1 开始）。没有所引段包含
        该字面即 issue——那是抽取把锚点指错了地方（QP-04 根因）。容忍同一
        章内的多次出现（只取章序，不做唯一定位）。

        调用方只给 id、不给段文本时（合成证据面）按 id 前缀派生，字面闸
        无从核对——该形态仅存于既有单元测试，新协议不走这条分支。
        """
        refs = list(refs)
        known = [(ref, self._segments[ref]) for ref in refs if ref in self._segments]
        if known:
            hits = [(ref, seg) for ref, seg in known
                    if surface in (seg.get('text') or '')]
            if not hits:
                return None, ResolverIssue(
                    code='missing_literal',
                    message=(f'surface {surface!r} 的证据锚点不含该字面'
                             f'（source_refs={refs}）'),
                    context={'surface': surface, 'source_refs': refs})
            chapter = min(int((seg or {}).get('chapter', 0)) for _ref, seg in hits)
        else:
            chapter = min((chapter_of_evidence(ref) for ref in refs), default=0)
        return max(chapter, 1), None
