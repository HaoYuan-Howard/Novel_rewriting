#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""body_checks —— 章级正文机械 validator（F5/D2，Stage5）。

【能查什么、不能证明什么】
整章自由正文不提供可靠的场景边界，机械检查只覆盖章级明确事项：非空、编码、
包装（围栏/章号标题/场景标记）、未释放专名字面与篇幅带。机械通过**不证明**
章内揭示位置、逐场义务兑现或成文质量——限制如实进入交付披露，回执 semantic
恒 unverified。

【验证材料随章包（D2，B4）】
禁用字面与称谓许可在 compile 时算好并随 ChapterIR 携带（推导唯一产地
`novel/stages/stage4/book_ir.py`）；本模块只消费章包材料做检查，不再接触
Design／Characters。

【失败分级】
硬失败（non_empty/encoding/no_wrapper/withheld_literals/required_literals/
truncated）：有额度则
重写，不能披露放行；只有篇幅带（length_band）允许在额度耗尽后按偏差最小提交
并披露。截断由调用方按 finish_reason 折算为 typed 错误进入同一通道。
"""
from __future__ import annotations

import re
from typing import Callable

from novel.core.text import count_non_whitespace_chars
from novel.core.validation import ValidationIssue
from novel.stages.common import (
    ChapterWriterPacket, claim_spans, uncovered_occurrences,
)

#: 机械 validator 的版本：章级回执 validator_version 记这一位，规则变化必须换版。
#: v2（CS-H H3，RC-61）：新增 `required_literals`——必保揭名的承载章正文必现
#: 称谓字面（缺字面即拒，走段落级修句，不披露放行）。
BODY_VALIDATOR_VERSION = 'str06-body-mechanical-v2'

#: 已通过的机械检查项名单（回执 mechanical_checks 的取值面）。
BODY_MECHANICAL_CHECKS = (
    'non_empty', 'encoding', 'no_wrapper', 'withheld_literals', 'required_literals',
    'length_band',
)

#: realization failure 的同输入重写上限（首次 + 再 2 次）。
MAX_BODY_REWRITES = 2

_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
_CHAPTER_TITLE = re.compile(
    r'^(第[0-9一二三四五六七八九十百千零两]+章|楔子|引子|序章|尾声|prologue|epilogue|chapter)\s*[:：、.．]?\s*',
    re.IGNORECASE)


def count_body_chars(text: str) -> int:
    """篇幅计数口径：非空白字符（空白、程序章号与 Hook 不在章文本内）。"""
    return count_non_whitespace_chars(text)


def validate_chapter_prose(
    text: str, *, withheld_literals: tuple[str, ...], released_surfaces,
    target_chars: int, band_low: int, band_high: int,
    scene_markers: tuple[str, ...] = (),
    required_literals: tuple[str, ...] = (),
) -> list[str]:
    """章级机械校验：只报告，不修改；错误带检查项名（I-36）。

    `withheld_literals`／`released_surfaces` 来自当前章包（compile 时算好）。
    `required_literals`（CS-H H3，RC-61）是必保揭名的承载章必现字面——缺字面
    即拒（违例走段落级修句，不是披露面）。
    """
    errors: list[str] = []
    raw = text or ''
    body = raw.strip()
    if not body:
        return ['[non_empty] 章正文为空']
    if '\ufffd' in body or _CONTROL.search(body):
        errors.append('[encoding] 存在 U+FFFD 或控制字符')
    first_line = body.splitlines()[0].strip()
    if ('```' in body or body.lstrip().startswith('#') or _CHAPTER_TITLE.match(first_line)
            or any(marker in body for marker in scene_markers)):
        errors.append('[no_wrapper] 存在围栏、章号标题或场景标记（章号由系统添加）')
    claimed = claim_spans(body, tuple(released_surfaces))
    # 诊断字符位置绑定原稿，不绑定 strip 后的临时视图；窄修据此
    # 定位段落，控制器不再从中文文案反解析字面或位置。
    offset = len(raw) - len(raw.lstrip())
    for claim in uncovered_occurrences(body, withheld_literals, claimed):
        start, end = offset + claim.start, offset + claim.end
        errors.append(ValidationIssue(
            f'[withheld_literals] 出现未释放的专名字面 {claim.literal!r}'
            f'（位置 {claim.start}）', code='withheld_literals',
            location=('prose', start, end), owner='body',
            context={'literal': claim.literal, 'start': start, 'end': end}))
    for literal in required_literals:
        if literal not in body:
            errors.append(f'[required_literals] 必保揭名的称谓字面 {literal!r} 未在正文出现'
                          '（人物的首次正式点名不得只以别称带过）')
    count = count_body_chars(body)
    if not (band_low <= count <= band_high):
        errors.append(f'[length_band] 字数 {count} 超出篇幅带 [{band_low}, {band_high}]'
                      f'（目标 {target_chars}）')
    return errors


def chapter_prose_validator(packet: ChapterWriterPacket) -> Callable[[str], list[str]]:
    """本章机械校验的**唯一构造点**（纯函数，只读章包）。

    写章控制流、付费边界的接受记录（RC-54）、wire 显式采用与 owner 补丁预检共用
    这一把尺：规则只在 `validate_chapter_prose` 里定义，各处只经此取得绑定了本章
    验证材料的校验器——不再各自拼参数（此前补丁／采用面漏带 `required_literals`
    与 `scene_markers`，缺必保揭名字面的 wire 能被采用、随后在写章循环里被拒）。
    """
    chapter = packet.chapter
    contract = packet.realization_contract
    scene_markers = tuple(context.scene_id for context in chapter.scenes)

    def validate(prose: str) -> list[str]:
        return validate_chapter_prose(
            prose, withheld_literals=chapter.withheld_literals,
            released_surfaces=chapter.released_surfaces,
            target_chars=contract.target_chars, band_low=contract.band_low,
            band_high=contract.band_high, scene_markers=scene_markers,
            required_literals=chapter.requires_released)
    return validate
