#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""body_writer —— 章包写手：生成、机械检查、有限重写与候选选择（F5/D2，Stage5）。

【章包与独立成章（D2，B4）】
Writer 与 Validator 只收当前章包（`ChapterWriterPacket` 三件套：PacketBrief＋
ChapterIR＋RealizationContract）：不读前文正文、Design、Characters 或其他章
IR。验证材料（称谓许可、禁用字面）已在 compile 时算好并随章包携带；Stage5
只做语言实现。各章因此相互独立：可乱序完成、可并发、可按有效提交集合补缺
（I-31：单章失败不污染其他章的合法结果）。

【有限重写与截断（D6）】
每章首次＋最多两次内容重写（上一稿＋typed 机械错误进请求身份，可免费恢复）；
候选必须通过全部硬检查；额度耗尽后按相对目标篇幅偏差最小优先、平局取较晚
者；没有合法候选则该章无提交。截断（finish_reason=length）是节点设计缺陷：
即停该章，不重写、不进候选。

【回执绑定（rc5）】
章回执绑定章包摘要（`packet_sha256`）、请求身份、验证版本与改名前正文摘要；
不再绑定全书 IR 与前文链（`R-7.4` 改写）。

【边界】
本模块不落盘（正文/回执/attempt 记录的写入归调用方的 repository 编排）；
不从正文反抽 semantic delta；章包错误回 Stage4 重编译，付费状态未知保持阻断。
"""
from __future__ import annotations

import hashlib
from typing import Any, NamedTuple

from contracts.schema.receipt import ChapterLengthDisclosure, ChapterReceipt
from novel.stages.common import (
    RENDERER_VERSION, WRITER_PROTOCOL_VERSION, ChapterWriterPacket,
    packet_sha256, render_chapter_packet,
)
from novel.stages.stage5.body_checks import (
    BODY_MECHANICAL_CHECKS, BODY_VALIDATOR_VERSION, MAX_BODY_REWRITES,
    count_body_chars, validate_chapter_prose,
)


class AnsweredText(NamedTuple):
    """一次文本请求的已回答行：调用方（工具层）从 Runtime 取得。

    ``ref`` 是 B1 的显式采用引用（`AdoptionRef`）：正式提交按它封存
    CommitBundle；无 Runtime 的测试/合成路径可为 None。
    """

    text: str | None
    finish_reason: str | None
    identity: str
    cached: bool
    ref: Any = None


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
    ref: Any = None


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _rewrite_prompt(packet: str, previous: str, errors: list[str]) -> str:
    return (packet
            + '\n\n【重写要求】\n上一次稿未通过机械校验，原因：' + '；'.join(errors)
            + '。\n上一稿全文如下，供对照修正；仍只依据同一份写作输入，'
              '不得新增或改换事实，修正后重写整章：\n〔上一稿〕\n' + previous)


def write_chapter(
    *, packet: ChapterWriterPacket, ask,
) -> ChapterOutcome:
    """写一章：生成 → 机械检查 → 有限重写 → 确定性候选选择。

    `packet` 是当前章包（唯一输入面，D2）；`ask(user_prompt) -> AnsweredText`
    由工具层提供（付费边界、缓存与留证都在那一层）。返回的 outcome 不落盘。
    """
    chapter = packet.chapter
    contract = packet.realization_contract
    packet_text = render_chapter_packet(packet)
    released = chapter.released_surfaces          # 章级称谓许可（compile 时算好，D2）
    withheld = chapter.withheld_literals          # 章级禁用字面（validator 材料）
    scene_markers = tuple(context.scene_id for context in chapter.scenes)
    low, high, target = contract.band_low, contract.band_high, contract.target_chars

    attempts: list[ChapterAttempt] = []
    candidates: list[tuple[str, str, list[str], Any]] = []   # (identity, prose, errors, ref)
    errors: list[str] = []
    identity = ''
    previous: str | None = None   # 重写基线：最近一份非空回答（空回答不进基线）
    for attempt in range(1 + MAX_BODY_REWRITES):
        prompt = (packet_text if previous is None
                  else _rewrite_prompt(packet_text, previous, errors))
        answered = ask(prompt)
        identity = answered.identity
        if answered.text is None:
            # B1/D5：空回答计一次失败留证；重发同一份章包（同请求身份 → Runtime
            # 在同一内容轮内续发下一 attempt），不再取上一稿。
            errors = ['[non_empty] 响应为空']
            attempts.append(ChapterAttempt(attempt + 1, identity, answered.finish_reason,
                                           0, tuple(errors)))
            continue
        prose = answered.text
        if answered.finish_reason == 'length':
            # D6：截断是节点设计缺陷——即停该章，不重写、不进候选；截断稿
            # 不得作为成文（响应到不了这里的路径已由 Runtime 的 truncated
            # 错误拦下，本分支只服务不经 Runtime 的合成/测试 ask）。
            attempts.append(ChapterAttempt(
                attempt + 1, identity, answered.finish_reason,
                count_body_chars(prose),
                ('[truncated] 响应被截断（finish_reason=length），即停该章（D6）',)))
            break
        errors = validate_chapter_prose(
            prose, withheld_literals=withheld, released_surfaces=released,
            target_chars=target, band_low=low, band_high=high,
            scene_markers=scene_markers)
        attempts.append(ChapterAttempt(attempt + 1, identity, answered.finish_reason,
                                       count_body_chars(prose), tuple(errors)))
        candidates.append((identity, prose, errors, answered.ref))
        previous = prose
        if not errors:
            return _outcome(packet, prose, identity, attempts, low, high, target,
                            disclosed=False, ref=answered.ref)

    # 额度耗尽：通过全部硬检查的候选里按相对偏差最小、平局取较晚者。
    # （截断稿自 D6 起即停、不进候选，不在过滤集内。）
    legal = [(identity, prose, ref) for identity, prose, errs, ref in candidates
             if not any(e.startswith(('[non_empty]', '[encoding]', '[no_wrapper]',
                                      '[withheld_literals]'))
                        for e in errs)]
    if not legal:
        return ChapterOutcome(prose=None, receipt=None,
                              attempts=tuple(attempts), request_identity=identity,
                              length_disclosed=False)
    chosen_identity, chosen_prose, chosen_ref = min(
        enumerate(legal),
        key=lambda item: (abs(count_body_chars(item[1][1]) - target) / target,
                          -item[0]))[1]
    disclosed = not (low <= count_body_chars(chosen_prose) <= high)
    return _outcome(packet, chosen_prose, chosen_identity, attempts, low, high, target,
                    disclosed=disclosed, ref=chosen_ref)


def _outcome(packet: ChapterWriterPacket, prose: str, identity: str,
             attempts: list[ChapterAttempt],
             low: int, high: int, target: int, *, disclosed: bool,
             ref=None) -> ChapterOutcome:
    count = count_body_chars(prose)
    receipt = ChapterReceipt(
        chapter_ordinal=packet.chapter.chapter_ordinal,
        packet_sha256=packet_sha256(packet),
        prose_sha256=sha256_text(prose),
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
                          request_identity=identity, length_disclosed=disclosed,
                          ref=ref)
