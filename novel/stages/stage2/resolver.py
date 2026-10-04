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
    可推导坐标归 compiler，模型不给窗口）；
  • basis 引文所在段定位（RC-27，2026-10-04）——wire 只给逐字引文，段号
    由本模块在条目自身证据范围内查出（I-21：可由引文机械推导的 id 不让
    模型抄）；范围外命中或全无命中给精确 issue，不替模型改范围或换引文；
  • 证据清单的唯一渲染（RC-26）——一段一行，段号与该段全文同行。

理解原文、判断同一性与关联归 semantic unit（book／chapters／associate）；
本模块只回答「这个字面在哪、这个 id 在不在」。validator 不修改输入：全部
API 只读，问题聚合成结构化 issue 返回（I-24/I-36）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

import re

from novel.core.text import content_equivalent_slice

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
    'basis_segment_outside_refs',  # R10a a1：basis 引的段不在条目自身 source_refs 内
    'quote_not_in_segment',        # R10a a1：basis 引文在所引句段内不逐字存在
    'quote_outside_refs',          # RC-27：引文只出现在条目证据范围之外的段
    'quote_not_found',             # RC-27：引文在证据闭集内无命中
)


def render_evidence_block(segments: Mapping[str, Mapping[str, Any]]) -> str:
    """证据清单的唯一渲染（RC-26，book／chapters 请求共用）：一段一行，段号
    与该段全文同行。

    段文本含换行即违反切分契约——段号会悬在两段文字之间，模型无从判断它
    属于哪一段（2026-10-04 三书停书的根因形态）。这里直接拒绝（I-34），
    不在渲染时偷偷压平换行掩盖切分缺陷。
    """
    lines: list[str] = []
    for ref, segment in segments.items():
        text = str(segment.get('text') or '')
        if '\n' in text or '\r' in text:
            raise ValueError(f'证据段 {ref} 的文本含换行：证据段必须是单个自然段（RC-26）')
        lines.append(f'[{ref}] {text}')
    return '\n'.join(lines)


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

    # ── R10a a1：basis 引文判定（C4 引文错位） ─────────────────────────────

    def verify_quote(self, segment: str, quote: str,
                     refs: Iterable[str]) -> list[ResolverIssue]:
        """一条 basis 的机械判定：段在闭集、段属条目自身 source_refs、
        引文逐字存在于段文本。refs 是该条目自身的 source_refs（Effect／
        Cognition 在所属拍；书级条目在自身）。逐字子串判定，不归一化、
        不模糊匹配；不要求唯一出现（basis 是证据存在性，不是定位）。
        """
        refs = list(refs)
        problems: list[ResolverIssue] = []
        if segment not in self._segments:
            return [ResolverIssue(
                code='unknown_segment',
                message=f'basis 引用的句段 id 不在闭集内：{segment!r}',
                context={'segment_id': segment})]
        if segment not in refs:
            problems.append(ResolverIssue(
                code='basis_segment_outside_refs',
                message=(f'basis 引文段 {segment!r} 不在条目自身 source_refs '
                         f'{refs} 内——引你所引，不得引用未声明的段'),
                context={'segment_id': segment, 'source_refs': refs}))
        if quote not in self._text(segment):
            # 反馈指认（R10c 2026-10-02）：模型常引对原文却挂错句段号（对话
            # 分句的收尾「」孤儿段使 id↔句子对齐反直觉）。命中段扫描仍是纯
            # 逐字判定——只改反馈、不放宽匹配；修订轮据此直接改段号。
            homes = [ref for ref, seg in self._segments.items()
                     if quote in (seg.get('text') or '')]
            hint = (f'该引文逐字命中于句段 {homes[:6]}——请改引这些句段号'
                    '并将其列入该条目 source_refs'
                    if homes else
                    '该引文未在闭集任何句段逐字命中——请逐字摘抄原句，'
                    '不转写、不缩写')
            problems.append(ResolverIssue(
                code='quote_not_in_segment',
                message=(f'basis 引文在句段 {segment!r} 内不逐字存在（引文须'
                         f'逐字摘自原句，不转写、不缩写；{hint}）：{quote[:30]!r}…'),
                context={'segment_id': segment, 'quote': quote[:60],
                         'verbatim_home_segments': homes[:12]}))
        return problems

    def _holds(self, ref: str, quote: str) -> bool:
        text = self._text(ref)
        return bool(text) and (quote in text
                               or content_equivalent_slice(quote, text) is not None)

    def locate_quote(self, quote: str, refs: Iterable[str]
                     ) -> tuple[str | None, str, ResolverIssue | None]:
        """RC-27：逐字引文 → ``(所在段, 原文切片, issue)``。

        只在条目自身的证据范围 `refs` 内取段（basis 必须出自本条目所述的
        原文，防止拿后文证据给前面的拍定结论）：按 refs 顺序取第一处逐字
        命中；没有逐字命中时取第一处「仅标点／空白宽度不同」的唯一命中，
        回取段原文切片（D2 同一把尺）。basis 是证据存在性，同一引文出现
        在多个所引段时取 refs 中的第一段，不要求唯一。

        范围内找不到即报 issue、不改范围：闭集里别处有命中 →
        `quote_outside_refs`（context 列出实际所在段，修正可能是扩范围也
        可能是换引文，由语义单元选）；全无命中 → `quote_not_found`。
        """
        refs = list(refs)
        for ref in refs:
            if ref in self._segments and quote in self._text(ref):
                return ref, quote, None
        for ref in refs:
            text = self._text(ref)
            if text:
                slice_ = content_equivalent_slice(quote, text)
                if slice_ is not None:
                    return ref, slice_, None
        homes = [ref for ref in self._segments if self._holds(ref, quote)]
        if homes:
            return None, quote, ResolverIssue(
                code='quote_outside_refs',
                message=(f'basis 引文不在本条目证据范围 {refs} 的任何一段内'
                         f'（原文位于 {homes[:6]}）：{quote[:30]!r}…——改用范围内的'
                         '原句作引文，或把引文所在段计入证据范围'),
                context={'quote': quote[:60], 'source_refs': refs,
                         'verbatim_home_segments': homes[:12]})
        return None, quote, ResolverIssue(
            code='quote_not_found',
            message=(f'basis 引文未在原文中命中：{quote[:30]!r}…——逐字摘抄原句，'
                     '不转写、不缩写、不跨段拼接'),
            context={'quote': quote[:60], 'source_refs': refs})

    def canonical_quote(self, segment: str, quote: str) -> str:
        """basis 引文的规范化回取（D2 标点宽度）：逐字已在段内 → 原样返回；
        仅标点／空白不同且**唯一**命中 → 段原文切片；其余（0 处、多处、段
        不在闭集）原样返回——判定不放宽，交 `verify_quote` 按现状报告。
        纯查找，不改任何输入（I-24）；调用方在 compiler 副本上采用返回值。
        """
        text = self._text(segment)
        if not text or quote in text:
            return quote
        slice_ = content_equivalent_slice(quote, text)
        return slice_ if slice_ is not None else quote

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


def verify_bases(
    resolver: EvidenceResolver,
    entries: Iterable[tuple[str, str, str, Iterable[str]]],
) -> list[tuple[str, ResolverIssue]]:
    """R10a a1：批量 basis 判定。`entries` 每项＝(where, segment, quote, refs)，
    where 是调用方的人读定位（如「第3章拍2 effects[1]」）；返回 (where, issue)
    对，由调用方聚合成自己的错误清单。纯定位查找，不做语义判断。
    """
    results: list[tuple[str, ResolverIssue]] = []
    for where, segment, quote, refs in entries:
        for issue in resolver.verify_quote(segment, quote, refs):
            results.append((where, issue))
    return results
