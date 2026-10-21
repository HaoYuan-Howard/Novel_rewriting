#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""body_writer —— 章包写手：生成、机械检查、有限重写与候选选择（F5/D2，Stage5）。

【章包与独立成章（D2，B4）】
Writer 与 Validator 只收当前章包（`ChapterWriterPacket` 三件套：PacketBrief＋
ChapterIR＋RealizationContract）：不读前文正文、Design、Characters 或其他章
IR。验证材料（身份称谓许可、待声明名称候选）已在 compile 时算好并随章包携带；Stage5
只做语言实现。各章因此相互独立：可乱序完成、可并发、可按有效提交集合补缺
（I-31：单章失败不污染其他章的合法结果）。

【有限重写与截断（D6）】
每章首次＋最多两次内容重写（上一稿＋typed 机械错误进请求身份，可免费恢复）；
候选必须通过全部硬检查；额度耗尽后按相对目标篇幅偏差最小优先、平局取较晚
者；没有合法候选则该章无提交。截断（finish_reason=length）是节点设计缺陷：
即停该章，不重写、不进候选。

【回执绑定（rc6 身份拆分）】
章回执分别绑定写手视图摘要（`writer_view_sha256`，材料含实际正文 system、
renderer 与写手协议版本）、验证输入摘要（`validation_sha256`：禁用字面、称谓许可、篇幅带）与
Machine 审计摘要（`machine_view_sha256`，仅审计不触发重购）；不再绑定全书 IR
与前文链（`R-7.4` 改写）。

【边界】
rc26（命名前移批次 C）：普通正文主链——写手直接输出目标姓名成文，不再有身份文本标记、
坐标表与末端改名；回执 prose_sha256 绑定最终正文字节。疑似旧名残留/提前揭名只形成定位报告
（调用方落 runtime 审计面），不阻断、不购修。
本模块不落盘（正文/回执/attempt 记录的写入归调用方的 repository 编排）；
不从正文反抽 semantic delta；章包错误回 Stage4 重编译，付费状态未知保持阻断。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, NamedTuple

from contracts.schema.receipt import ChapterLengthDisclosure, ChapterReceipt
from novel.core.name_residue import NameResidue, name_residues, residue_report
from novel.stages.common import (
    BODY_PARAGRAPH_REPAIR_SYSTEM, RENDERER_VERSION, WRITER_PROTOCOL_VERSION,
    ChapterWriterPacket, chapter_identities, render_chapter_packet,
)
from novel.stages.stage5.body_checks import (
    BODY_MECHANICAL_CHECKS, BODY_VALIDATOR_VERSION, MAX_BODY_REWRITES,
    chapter_prose_validator, count_body_chars,
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


def _wire_chars(packet: ChapterWriterPacket, text: str) -> int:
    """篇幅计数（rc26：普通正文无协议标记，直接按非空白字符计）。"""
    return count_body_chars(text)


def _repair_identity(packet: ChapterWriterPacket, previous: str) -> str:
    """窄请求绑定完整基底与章包身份，省略的正文或材料变化仍须换请求。"""
    identities = chapter_identities(packet)
    return json.dumps({
        'chapter_ordinal': packet.chapter.chapter_ordinal,
        'base_text_sha256': sha256_text(previous),
        'writer_view_sha256': identities.writer_view_sha256,
        'validation_sha256': identities.validation_sha256,
    }, ensure_ascii=False, sort_keys=True)


def _paragraph_repair_prompt(previous: str, errors: list[str], *,
                             packet: ChapterWriterPacket, patch_error: str = '') -> str:
    """段落级修句（CS-H H3，RC-61）：只差必保揭名字面时，不重发整份章包与整稿——
    按行编号送达当前稿，只要求回需要替换的段落（窄输入窄输出，M3 返修宽度）。"""
    numbered = '\n'.join(f'{index}: {line}'
                         for index, line in enumerate(previous.split('\n')))
    return ('【段落修句身份】' + _repair_identity(packet, previous)
            + '\n【段落修句】上一稿仅以下必保揭名问题未过，其余机械检查全部通过：'
            + '；'.join(errors)
            + ('\n上次修句回答失败：' + patch_error if patch_error else '')
            + '\n只修订需要改动的段落：在相关段落把该称谓字面自然写进情节'
            '（人物的首次正式点名），不得新增或改变事实、不得改动无关段落。'
            '\n当前稿按行编号列出；输出 JSON（不要围栏、不要解释）：'
            '{"paragraphs":[{"index":行号,"text":"该行替换后的全文"}]}——'
            '只输出需要替换的行，未列出的行保持原样。人物称谓仍须使用'
            '本章已释放的目标姓名字面（普通文本，不加任何标记）。'
            '\n【可用称谓】\n' + '、'.join(sorted(
                {s for row in packet.chapter.released_names for s in row.surfaces}))
            + '\n〔当前稿〕\n' + numbered)


def _merge_paragraph_patch(previous: str, answer: str, *,
                           editable_lines: tuple[int, ...] | None = None,
                           ) -> tuple[str | None, str]:
    """段落修句回答的确定性合并：按行号替换，范围外/重复/坏形状拒绝（返回错误）。"""
    def unique_fields(pairs):
        values = {}
        for key, value in pairs:
            if key in values:
                raise ValueError(f'段落修句回答含重复 JSON 键：{key}')
            values[key] = value
        return values

    try:
        data = json.loads(answer, object_pairs_hook=unique_fields)
    except ValueError as exc:
        return None, f'[paragraph_patch] 段落修句回答不是合法 JSON：{exc}'
    lines = previous.split('\n')
    entries = data.get('paragraphs') if isinstance(data, dict) and set(data) == {'paragraphs'} else None
    if not isinstance(entries, list) or not entries:
        return None, '[paragraph_patch] 段落修句回答缺 paragraphs 数组（至少修订一行）'
    seen: set[int] = set()
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != {'index', 'text'}
                or not isinstance(entry.get('index'), int)
                or isinstance(entry.get('index'), bool)
                or not isinstance(entry.get('text'), str)):
            return None, '[paragraph_patch] 段落修句条目形状失败（须含 index 与 text）'
        index = entry['index']
        if (not 0 <= index < len(lines) or index in seen
                or (editable_lines is not None and index not in editable_lines)):
            return None, f'[paragraph_patch] 段落行号不可编辑、越界或重复：{index}'
        if '\n' in entry['text'] or '\r' in entry['text']:
            return None, '[paragraph_patch] 单行替换不得插入换行或新段落'
        seen.add(index)
        lines[index] = entry['text']
    return '\n'.join(lines), ''


def _patch_answer_validator(previous: str, validate, *,
                            editable_lines: tuple[int, ...] | None = None):
    """段落修句回答的接受校验：先确定性合并进上一稿，再跑本章同一把机械检查。

    付费边界（`ask_text_once`）把返回的错误写进接受记录；回答是行替换 JSON，
    不能拿整章校验器直接验它（篇幅带必然报错 ⇒ 成功的修句被记成 accepted=false）。
    """
    def check(answer: str) -> list[str]:
        merged, merge_error = _merge_paragraph_patch(
            previous, answer, editable_lines=editable_lines)
        return [merge_error] if merge_error else validate(merged)
    return check


def _rewrite_prompt(packet: str, previous: str, errors: list[str]) -> str:
    return (packet
            + '\n\n【重写要求】\n上一次稿未通过机械校验，原因：' + '；'.join(errors)
            + '。\n上一稿全文如下，供对照修正；仍只依据同一份写作输入，'
              '不得新增或改换事实，修正后重写整章：\n〔上一稿〕\n' + previous)


def write_chapter(
    *, packet: ChapterWriterPacket, ask,
) -> ChapterOutcome:
    """写一章：生成 → 机械检查 → 有限重写 → 确定性候选选择。

    `packet` 是当前章包（唯一输入面，D2）；`ask(user_prompt, validate) -> AnsweredText`
    由工具层提供（付费边界、缓存与留证都在那一层）。`validate` 是本章的机械校验
    （纯函数，与下面控制流用的是同一把）：付费边界据此把被拒首稿的接受记录如实记成
    accepted=false（RC-54）。返回的 outcome 不落盘。
    """
    contract = packet.realization_contract
    packet_text = render_chapter_packet(packet)
    low, high, target = contract.band_low, contract.band_high, contract.target_chars
    validate = chapter_prose_validator(packet)

    attempts: list[ChapterAttempt] = []
    candidates: list[tuple[str, str, list[str], Any]] = []   # (identity, prose, errors, ref)
    errors: list[str] = []
    identity = ''
    previous: str | None = None   # 重写基线：最近一份非空回答（空回答不进基线）
    patch_error = ''
    for attempt in range(1 + MAX_BODY_REWRITES):
        # 仅必保揭名缺失走段落修句；身份声明与混合问题经同一章包完整返修。
        required_patch = (previous is not None and bool(errors)
                          and all(error.startswith('[required_literals]') for error in errors))
        patch_round = required_patch
        editable_lines = None
        if previous is None:
            answered = ask(packet_text, validate)
        elif patch_round:
            # 回答是行替换 JSON，不是整章正文：付费边界收到的校验器必须校验**合并后**
            # 的稿（接受记录 accepted 才如实，RC-54），请求用专用 system 提示
            # （写手 system 要求「只输出完整正文」，与本轮的 JSON 输出相冲突）。
            prompt = _paragraph_repair_prompt(
                previous, errors, packet=packet, patch_error=patch_error)
            answered = ask(prompt,
                           _patch_answer_validator(previous, validate,
                                                   editable_lines=editable_lines),
                           system=BODY_PARAGRAPH_REPAIR_SYSTEM)
        else:
            answered = ask(_rewrite_prompt(packet_text, previous, errors), validate)
        identity = answered.identity
        if answered.text is None:
            # B1/D5：空回答计一次失败留证；重发同一份章包（同请求身份 → Runtime
            # 在同一内容轮内续发下一 attempt），不再取上一稿。
            empty_error = '[non_empty] 响应为空'
            attempts.append(ChapterAttempt(attempt + 1, identity, answered.finish_reason,
                                           0, (empty_error,)))
            errors = [empty_error]
            patch_error = ''
            continue
        prose = answered.text
        if answered.finish_reason == 'length':
            # D6：截断是节点设计缺陷——即停该章，不重写、不进候选；截断稿
            # 不得作为成文（响应到不了这里的路径已由 Runtime 的 truncated
            # 错误拦下，本分支只服务不经 Runtime 的合成/测试 ask）。
            attempts.append(ChapterAttempt(
                attempt + 1, identity, answered.finish_reason,
                _wire_chars(packet, prose),
                ('[truncated] 响应被截断（finish_reason=length），即停该章（D6）',)))
            break
        if patch_round:
            merged, merge_error = _merge_paragraph_patch(
                previous, prose, editable_lines=editable_lines)
            if merge_error:
                # 解析失败保留完整基底和原诊断，下一轮恢复整章，不假造已合并稿。
                attempts.append(ChapterAttempt(attempt + 1, identity,
                                               answered.finish_reason,
                                               _wire_chars(packet, previous), (merge_error,)))
                errors = [merge_error, *errors]
                patch_error = ''
                continue
            prose = merged
        patch_error = ''
        errors = validate(prose)
        attempts.append(ChapterAttempt(attempt + 1, identity, answered.finish_reason,
                                       _wire_chars(packet, prose), tuple(errors)))
        candidates.append((identity, prose, errors, answered.ref))
        previous = prose
        if not errors:
            return _outcome(packet, prose, identity, attempts, low, high, target,
                            disclosed=False, ref=answered.ref)

    # 额度耗尽：通过全部硬检查的候选里按相对偏差最小、平局取较晚者。
    # （截断稿自 D6 起即停、不进候选，不在过滤集内；段落修句轮的合并稿与其
    # 解析失败留证同走 attempt 账，缺字面稿不得披露放行。）
    legal = [(identity, prose, ref) for identity, prose, errs, ref in candidates
             if all(error.startswith('[length_band]') for error in errs)]
    if not legal:
        return ChapterOutcome(prose=None, receipt=None,
                              attempts=tuple(attempts), request_identity=identity,
                              length_disclosed=False)
    chosen_identity, chosen_prose, chosen_ref = min(
        enumerate(legal),
        key=lambda item: (abs(_wire_chars(packet, item[1][1]) - target) / target,
                          -item[0]))[1]
    disclosed = not (low <= _wire_chars(packet, chosen_prose) <= high)
    return _outcome(packet, chosen_prose, chosen_identity, attempts, low, high, target,
                    disclosed=disclosed, ref=chosen_ref)


def chapter_name_residues(packet: ChapterWriterPacket, prose: str) -> tuple[NameResidue, ...]:
    """本章正文的疑似姓名残留定位（rc26 披露面；不阻断、不购返修）。

    监视行由 compile 派生并随章包携带（`ChapterIR.name_watch`）：改名人物的
    源旧字面与本章未释放的人物目标字面。命中只定位归因——普通同形词可能
    出现，报告不宣称语义泄漏。
    """
    return name_residues(prose, watch=packet.chapter.name_watch)


def _outcome(packet: ChapterWriterPacket, prose: str, identity: str,
             attempts: list[ChapterAttempt],
             low: int, high: int, target: int, *, disclosed: bool,
             ref=None) -> ChapterOutcome:
    count = count_body_chars(prose)
    identities = chapter_identities(packet)
    receipt = ChapterReceipt(
        chapter_ordinal=packet.chapter.chapter_ordinal,
        writer_view_sha256=identities.writer_view_sha256,
        validation_sha256=identities.validation_sha256,
        machine_view_sha256=identities.machine_view_sha256,
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


def recheck_chapter(packet: ChapterWriterPacket, prose: str,
                    receipt: ChapterReceipt) -> ChapterReceipt | None:
    """当前验证规则免费复验原稿；写手输入／正文字节不符不构造新接受。"""
    identities = chapter_identities(packet)
    if (receipt.status != 'accepted'
            or receipt.chapter_ordinal != packet.chapter.chapter_ordinal
            or receipt.protocol_version != WRITER_PROTOCOL_VERSION
            or receipt.renderer_version != RENDERER_VERSION
            or receipt.writer_view_sha256 != identities.writer_view_sha256
            or receipt.prose_sha256 != sha256_text(prose)):
        return None
    errors = chapter_prose_validator(packet)(prose)
    if errors and not (receipt.length_disclosed and all(str(e).startswith('[length_band]') for e in errors)):
        return None
    contract = packet.realization_contract
    return _outcome(packet, prose, receipt.request_identity, [],
                    contract.band_low, contract.band_high, contract.target_chars,
                    disclosed=bool(errors)).receipt
