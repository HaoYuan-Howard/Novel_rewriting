#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""body_checks —— 章级正文机械 validator（F5/D2，Stage5）。

【能查什么、不能证明什么】
整章自由正文不提供可靠的场景边界，机械检查只覆盖章级明确事项：非空、编码、
包装（围栏/章号标题/场景标记/身份协议定界符）、必保揭名字面与篇幅带。机械通过**不证明**
章内揭示位置、逐场义务兑现、疑似旧名残留与提前揭名（只定位报告，不阻断）或成文质量
——限制如实进入交付披露，回执 semantic 恒 unverified。

【验证材料随章包（D2，B4）】
禁用字面与称谓许可在 compile 时算好并随 ChapterIR 携带（推导唯一产地
`novel/stages/stage4/book_ir.py`）；本模块只消费章包材料做检查，不再接触
Design／Characters。

【失败分级】
硬失败（non_empty/encoding/no_wrapper/name_text*/required_literals/
truncated）：有额度则
重写，不能披露放行；只有篇幅带（length_band）允许在额度耗尽后按偏差最小提交
并披露。截断由调用方按 finish_reason 折算为 typed 错误进入同一通道。
"""
from __future__ import annotations

import re
from typing import Callable, Iterable, Mapping

from contracts.schema.receipt import BODY_MECHANICAL_CHECKS, BODY_VALIDATOR_VERSION
from novel.core.text import count_non_whitespace_chars
from novel.stages.common import ChapterWriterPacket

#: realization failure 的同输入重写上限（首次 + 再 2 次）。
MAX_BODY_REWRITES = 2

_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
_CHAPTER_TITLE = re.compile(
    r'^(?:第[0-9一二三四五六七八九十百千零两]+章|楔子|引子|序章|尾声|prologue|epilogue|chapter)'
    r'(?:$|[ \t:：、.．])',
    re.IGNORECASE)


def count_body_chars(text: str) -> int:
    """篇幅计数口径：非空白字符（空白、程序章号与 Hook 不在章文本内）。"""
    return count_non_whitespace_chars(text)


#: 身份协议定界符（rc26：identity-text 退役后正文不得再带标记）。
_PROTOCOL_DELIMITERS = ('[[', ']]')


def validate_chapter_prose(
    text: str, *,
    target_chars: int, band_low: int, band_high: int,
    scene_markers: tuple[str, ...] = (),
    required_literals: tuple[str, ...] = (),
) -> list[str]:
    """章级机械校验：只报告，不修改；错误带检查项名（I-36）。

    rc26（命名前移批次 C）：普通正文主链——不再解析身份文本标记、不再做候选覆盖硬闸。
    疑似旧名残留与疑似提前揭名只进定位报告（`novel.core.name_residue`，不阻断、不购修）；
    本函数只保留硬机械项：非空、编码、包装（含身份协议定界符）、必保揭名字面与篇幅带。
    必保揭名改按纯正文字面判定（目标姓名成文，无提及块可抹）。
    """
    errors: list[str] = []
    body = (text or '').strip()
    if not body:
        return ['[non_empty] 章正文为空']
    if '�' in body or _CONTROL.search(body):
        errors.append('[encoding] 存在 U+FFFD 或控制字符')
    first_line = body.splitlines()[0].strip()
    if ('```' in body or body.lstrip().startswith('#') or _CHAPTER_TITLE.match(first_line)
            or any(marker in body for marker in scene_markers)
            or any(delimiter in body for delimiter in _PROTOCOL_DELIMITERS)):
        errors.append('[no_wrapper] 存在围栅、章号标题、场景标记或身份协议定界符'
                      '（章号由系统添加，正文不带标记）')
    for literal in required_literals:
        if literal not in body:
            errors.append(f'[required_literals] 必保揭名的称谓字面 {literal!r} 未在正文出现'
                          '（人物的首次正式点名不得只以别称带过）')
    count = count_body_chars(body)
    if not (band_low <= count <= band_high):
        errors.append(f'[length_band] 字数 {count} 超出篇幅带 [{band_low}, {band_high}]'
                      f'（目标 {target_chars}）')
    return errors


def chapter_prose_validator(
    packet: ChapterWriterPacket,
) -> Callable[[str], list[str]]:
    """本章机械校验的**唯一构造点**（纯函数，只读章包）。

    写章控制流、付费边界的接受记录（RC-54）、wire 显式采用与 owner 补丁预检共用
    这一把尺：规则只在 `validate_chapter_prose` 里定义，各处只经此取得绑定了本章
    验证材料的校验器——不再各自拼参数（此前补丁？采用面漏带 `required_literals`
    与 `scene_markers`，缺必保揭名字面的 wire 能被采用、随后在写章循环里被拒）。
    """
    chapter = packet.chapter
    contract = packet.realization_contract
    scene_markers = tuple(context.scene_id for context in chapter.scenes)

    def validate(prose: str) -> list[str]:
        return validate_chapter_prose(
            prose,
            target_chars=contract.target_chars, band_low=contract.band_low,
            band_high=contract.band_high, scene_markers=scene_markers,
            required_literals=chapter.requires_released)
    return validate
