#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""body_checks —— 章级正文机械 validator（F5/D2，Stage5）。

【能查什么、不能证明什么】
整章自由正文不提供可靠的场景边界，机械检查只覆盖章级明确事项：非空、编码、
包装（围栏/章号标题/场景标记）、身份引用许可、候选逐次声明与篇幅带。机械通过**不证明**
身份声明语义正确、章内揭示位置、逐场义务兑现或成文质量——限制如实进入交付披露，回执 semantic
恒 unverified。

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
from contracts.schema.name_text import NameMention, NameTextSpan

from novel.core.name_text import resolve_name_text, validate_resolved_name_text
from novel.core.text import count_non_whitespace_chars
from novel.core.validation import ValidationIssue
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


def validate_chapter_prose(
    text: str, *, withheld_literals: tuple[str, ...], released_surfaces,
    target_chars: int, band_low: int, band_high: int,
    scene_markers: tuple[str, ...] = (),
    required_literals: tuple[str, ...] = (),
    name_surfaces: Mapping[str, Iterable[str]] | None = None,
    name_candidates: tuple[str, ...] = (),
    name_mentions: tuple[NameMention, ...] | None = None,
    non_name_spans: tuple[NameTextSpan, ...] | None = None,
) -> list[str]:
    """章级机械校验：只报告，不修改；错误带检查项名（I-36）。

    `withheld_literals`／`released_surfaces` 仅扩大待声明候选召回，不宣布姓名泄漏。
    `name_surfaces` 才是身份与称谓许可。wire 先解析；正式纯文复验同时提供两类
    canonical 声明表。`required_literals` 只以人物提及兑现，普通义同形词不能顶替揭名。
    """
    errors: list[str] = []
    surfaces = name_surfaces or {}
    if name_mentions is None and non_name_spans is None:
        resolved = resolve_name_text(text or '', surfaces=surfaces,
                                     candidate_literals=(*name_candidates, *withheld_literals,
                                                         *released_surfaces))
        raw, mentions, name_issues = resolved.text, resolved.mentions, resolved.errors
    elif name_mentions is not None and non_name_spans is not None:
        raw, mentions = text or '', name_mentions
        name_issues = validate_resolved_name_text(
            raw, surfaces=surfaces, candidate_literals=(*name_candidates, *withheld_literals,
                                                       *released_surfaces),
            name_mentions=name_mentions, non_name_spans=non_name_spans)
    else:
        return ['[name_text] 人物提及与普通义声明须同时提供，缺失不是合法空表']
    for issue in name_issues:
        errors.append(ValidationIssue(
            f'[{issue.code}] {issue.message}', code=issue.code,
            location=(issue.coordinate_space, issue.start, issue.end), owner='body',
            context={'literal': issue.literal, 'start': issue.start, 'end': issue.end,
                     'coordinate_space': issue.coordinate_space}))
    body = raw.strip()
    if not body:
        return ['[non_empty] 章正文为空']
    if '\ufffd' in body or _CONTROL.search(body):
        errors.append('[encoding] 存在 U+FFFD 或控制字符')
    first_line = body.splitlines()[0].strip()
    if ('```' in body or body.lstrip().startswith('#') or _CHAPTER_TITLE.match(first_line)
            or any(marker in body for marker in scene_markers)):
        errors.append('[no_wrapper] 存在围栏、章号标题或场景标记（章号由系统添加）')
    for literal in required_literals:
        if literal not in {mention.surface for mention in mentions}:
            errors.append(f'[required_literals] 必保揭名的称谓字面 {literal!r} 未在正文出现'
                          '（人物的首次正式点名不得只以别称带过）')
    count = count_body_chars(body)
    if not (band_low <= count <= band_high):
        errors.append(f'[length_band] 字数 {count} 超出篇幅带 [{band_low}, {band_high}]'
                      f'（目标 {target_chars}）')
    return errors


def chapter_prose_validator(
    packet: ChapterWriterPacket, *,
    name_mentions: tuple[NameMention, ...] | None = None,
    non_name_spans: tuple[NameTextSpan, ...] | None = None,
) -> Callable[[str], list[str]]:
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
            required_literals=chapter.requires_released,
            name_surfaces={row.char_id: row.surfaces for row in chapter.released_names},
            name_candidates=chapter.name_candidates,
            name_mentions=name_mentions, non_name_spans=non_name_spans)
    return validate
