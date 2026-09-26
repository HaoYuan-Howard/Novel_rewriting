#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""body_writer —— 按章串行 Writer：生成、机械检查、有限重写与候选选择（F5，Stage5）。

【串行与恢复】
正文严格按章串行：每次读取完整 WriterBookIR 与全部有效前文，一次输出当前整
章自然正文。重写使用相同 IR、相同前文、上一稿与 typed 机械错误（attempt 进请
求身份，可免费恢复）。候选必须通过全部硬检查；额度耗尽后按相对目标篇幅偏差
最小优先、平局取较晚者；没有合法候选则不提交、停止后续章。

【边界】
本模块不落盘（正文/回执/attempt 记录的写入归调用方的 repository 编排）；
不从正文反抽 semantic delta；上游 IR 错误回 Stage4，付费状态未知保持阻断。
"""
from __future__ import annotations

import hashlib
import json
from typing import Mapping, NamedTuple

from contracts.schema.book_ir import WriterBookIR
from contracts.schema.design import BodyDesign
from contracts.schema.receipt import ChapterLengthDisclosure, ChapterReceipt
from contracts.schema.source import NameProjection, NameStructure
from novel.stages.stage5.body_checks import (
    BODY_MECHANICAL_CHECKS, BODY_VALIDATOR_VERSION, MAX_BODY_REWRITES,
    chapter_surface_releases, chapter_withheld_literals, count_body_chars,
    validate_chapter_prose,
)
from novel.stages.common import (
    RENDERER_VERSION, WRITER_PROTOCOL_VERSION, render_chapter_packet,
)


class AnsweredText(NamedTuple):
    """一次文本请求的已回答行：调用方（工具层）从 PaidRequestSession 取得。"""

    text: str | None
    finish_reason: str | None
    identity: str
    cached: bool


class ChapterAttempt(NamedTuple):
    """一次章 attempt 的留证：typed 错误与请求身份（拒绝候选单独留证用）。"""

    attempt: int
    identity: str
    finish_reason: str | None
    chars: int
    errors: tuple[str, ...]


class ChapterOutcome(NamedTuple):
    """一章的写作结果：提交候选 + 回执字段；prose=None 表示无合法候选。"""

    prose: str | None
    receipt: ChapterReceipt | None
    attempts: tuple[ChapterAttempt, ...]
    request_identity: str
    length_disclosed: bool


def prior_body_text(committed: Mapping[int, str]) -> str:
    """有效前文的规范拼接（按章序，空行分隔）；回执 prior_text_sha256 绑定它。"""
    return '\n\n'.join(committed[ordinal] for ordinal in sorted(committed))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def book_ir_sha256(book_ir: WriterBookIR) -> str:
    """全书 IR 内容身份（规范化 JSON；章级回执与恢复核对共用）。"""
    payload = json.dumps(book_ir.model_dump(mode='json'), ensure_ascii=False,
                         sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _rewrite_prompt(packet: str, previous: str, errors: list[str]) -> str:
    return (packet
            + '\n\n【重写要求】\n上一次稿未通过机械校验，原因：' + '；'.join(errors)
            + '。\n上一稿全文如下，供对照修正；仍只依据同一份写作输入，'
              '不得新增或改换事实，修正后重写整章：\n〔上一稿〕\n' + previous)


def write_chapter(
    *, book_ir: WriterBookIR, graph: BodyDesign, projection: NameProjection,
    chapter_ordinal: int, committed: Mapping[int, str],
    ask, structure: NameStructure | None = None,
) -> ChapterOutcome:
    """写一章：生成 → 机械检查 → 有限重写 → 确定性候选选择。

    `ask(user_prompt) -> AnsweredText` 由工具层提供（付费边界、缓存与留证都在
    那一层）。返回的 outcome 不落盘。
    """
    chapter = book_ir.chapter(chapter_ordinal)
    brief = book_ir.book_brief
    prior = prior_body_text(committed)
    packet = render_chapter_packet(book_ir, chapter_ordinal, {
        ordinal: prose for ordinal, prose in sorted(committed.items())})
    released = tuple(sorted(chapter_surface_releases(graph, chapter_ordinal)))
    withheld = chapter_withheld_literals(projection, graph, chapter_ordinal, structure)
    scene_markers = tuple(context.scene_id for context in chapter.scenes)
    low = round(chapter.length_budget * brief.length_band_low)
    high = round(chapter.length_budget * brief.length_band_high)
    target = chapter.length_budget

    attempts: list[ChapterAttempt] = []
    candidates: list[tuple[str, str, list[str]]] = []   # (identity, prose, errors)
    errors: list[str] = []
    identity = ''
    for attempt in range(1 + MAX_BODY_REWRITES):
        prompt = (packet if attempt == 0
                  else _rewrite_prompt(packet, candidates[-1][1], errors))
        answered = ask(prompt)
        identity = answered.identity
        if answered.text is None:
            errors = ['[non_empty] 响应为空']
            attempts.append(ChapterAttempt(attempt + 1, identity, answered.finish_reason,
                                           0, tuple(errors)))
            continue
        prose = answered.text
        errors = validate_chapter_prose(
            prose, withheld_literals=withheld, released_surfaces=released,
            target_chars=target, band_low=low, band_high=high,
            scene_markers=scene_markers)
        if answered.finish_reason == 'length':
            errors.append('[truncated] 响应被截断（finish_reason=length），不得作为成文')
        attempts.append(ChapterAttempt(attempt + 1, identity, answered.finish_reason,
                                       count_body_chars(prose), tuple(errors)))
        candidates.append((identity, prose, errors))
        if not errors:
            return _outcome(prose, identity, attempts, book_ir, chapter_ordinal,
                            prior, low, high, target, disclosed=False)

    # 额度耗尽：通过全部硬检查的候选里按相对偏差最小、平局取较晚者。
    legal = [(identity, prose) for identity, prose, errs in candidates
             if not any(e.startswith(('[non_empty]', '[encoding]', '[no_wrapper]',
                                      '[withheld_literals]', '[truncated]'))
                        for e in errs)]
    if not legal:
        return ChapterOutcome(prose=None, receipt=None,
                              attempts=tuple(attempts), request_identity=identity,
                              length_disclosed=False)
    chosen_identity, chosen_prose = min(
        enumerate(legal),
        key=lambda item: (abs(count_body_chars(item[1][1]) - target) / target,
                          -item[0]))[1]
    disclosed = not (low <= count_body_chars(chosen_prose) <= high)
    return _outcome(chosen_prose, chosen_identity, attempts, book_ir,
                    chapter_ordinal, prior, low, high, target, disclosed=disclosed)


def _outcome(prose: str, identity: str, attempts: list[ChapterAttempt],
             book_ir: WriterBookIR, chapter_ordinal: int, prior: str,
             low: int, high: int, target: int, *, disclosed: bool) -> ChapterOutcome:
    count = count_body_chars(prose)
    receipt = ChapterReceipt(
        chapter_ordinal=chapter_ordinal, book_ir_sha256=book_ir_sha256(book_ir),
        prior_text_sha256=sha256_text(prior), prose_sha256=sha256_text(prose),
        request_identity=identity or 'unidentified',
        protocol_version=WRITER_PROTOCOL_VERSION,
        renderer_version=RENDERER_VERSION, validator_version=BODY_VALIDATOR_VERSION,
        status='accepted', mechanical_checks=BODY_MECHANICAL_CHECKS,
        length=ChapterLengthDisclosure(
            char_count=count, target_chars=target, band_low=low, band_high=high,
            within_band=low <= count <= high,
            deviation_ratio=round((count - target) / target, 4)),
        length_disclosed=disclosed)
    return ChapterOutcome(prose=prose, receipt=receipt, attempts=tuple(attempts),
                          request_identity=identity, length_disclosed=disclosed)
