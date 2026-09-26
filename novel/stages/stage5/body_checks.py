#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""body_checks —— 章级正文机械 validator 与专名禁用面推导（F5，Stage5）。

【能查什么、不能证明什么】
整章自由正文不提供可靠的场景边界，机械检查只覆盖章级明确事项：非空、编码、
包装（围栏/章号标题/场景标记）、未释放专名字面与篇幅带。机械通过**不证明**
章内揭示位置、逐场义务兑现或成文质量——限制如实进入交付披露，回执 semantic
恒 unverified。

【失败分级】
硬失败（non_empty/encoding/no_wrapper/withheld_literals/truncated）：有额度则
重写，不能披露放行；只有篇幅带（length_band）允许在额度耗尽后按偏差最小提交
并披露。截断由调用方按 finish_reason 折算为 typed 错误进入同一通道。
"""
from __future__ import annotations

import re

from contracts.schema.design import BodyDesign
from contracts.schema.source import NameProjection, NameStructure, render_form
from novel.stages.common import claim_spans, uncovered_occurrences

#: 机械 validator 的版本：章级回执 validator_version 记这一位，规则变化必须换版。
BODY_VALIDATOR_VERSION = 'str06-body-mechanical-v1'

#: 已通过的机械检查项名单（回执 mechanical_checks 的取值面）。
BODY_MECHANICAL_CHECKS = (
    'non_empty', 'encoding', 'no_wrapper', 'withheld_literals', 'length_band',
)

#: realization failure 的同输入重写上限（首次 + 再 2 次）。
MAX_BODY_REWRITES = 2

_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
_CHAPTER_TITLE = re.compile(
    r'^(第[0-9一二三四五六七八九十百千零两]+章|楔子|引子|序章|尾声|prologue|epilogue|chapter)\s*[:：、.．]?\s*',
    re.IGNORECASE)


def count_body_chars(text: str) -> int:
    """篇幅计数口径：非空白字符（空白、程序章号与 Hook 不在章文本内）。"""
    return sum(1 for c in text if not c.isspace())


def chapter_surface_releases(graph: BodyDesign, chapter_ordinal: int) -> set[str]:
    """第 k 章（含）之前已释放的称谓字面：initial + 前 k 章内引入位置。"""
    released = {s for intro in graph.initial_surfaces for s in intro.surfaces}
    chapter_of = {sid: ch.chapter_ordinal for ch in graph.chapters for sid in ch.scene_ids}
    for scene in graph.scenes:
        if chapter_of[scene.scene_id] > chapter_ordinal:
            continue
        for beat in scene.beats:
            for intro in beat.surface_introductions:
                released.update(intro.surfaces)
    return released


def _family_form_literals(projection: NameProjection,
                          structure: NameStructure | None) -> set[str]:
    """家族级构词（families[].forms）渲染出的字面（同字面家族的构词取并集）。"""
    if structure is None:
        return set()
    by_literal: dict[str, set[str]] = {}
    for family in structure.families.values():
        for item in family.forms:
            try:
                by_literal.setdefault(family.literal, set()).add(
                    render_form(item.form, family=family.literal, given=None, aliases={}))
            except ValueError:
                continue
    literals: set[str] = set()
    for entry in projection.by_person:
        literals |= by_literal.get(entry.family_literal or '', set())
    return literals


def chapter_withheld_literals(
    projection: NameProjection, graph: BodyDesign, chapter_ordinal: int,
    structure: NameStructure | None = None,
) -> tuple[str, ...]:
    """第 k 章不得出现的专名字面 = 全部人物专名/带名 surface（含家族级构词渲染）
    − 第 k 章前已释放的称谓。与 Hook withheld 同一推导纪律：不写死人名。"""
    all_names: set[str] = set(projection.proper_literals)
    for entry in projection.by_person:
        all_names.update(entry.name_bearing_surfaces)
    all_names |= _family_form_literals(projection, structure)
    withheld = all_names - chapter_surface_releases(graph, chapter_ordinal)
    withheld.discard('')
    return tuple(sorted(withheld))


def validate_chapter_prose(
    text: str, *, withheld_literals: tuple[str, ...], released_surfaces,
    target_chars: int, band_low: int, band_high: int,
    scene_markers: tuple[str, ...] = (),
) -> list[str]:
    """章级机械校验：只报告，不修改；错误带检查项名（I-36）。"""
    errors: list[str] = []
    body = (text or '').strip()
    if not body:
        return ['[non_empty] 章正文为空']
    if '\ufffd' in body or _CONTROL.search(body):
        errors.append('[encoding] 存在 U+FFFD 或控制字符')
    first_line = body.splitlines()[0].strip()
    if ('```' in body or body.lstrip().startswith('#') or _CHAPTER_TITLE.match(first_line)
            or any(marker in body for marker in scene_markers)):
        errors.append('[no_wrapper] 存在围栏、章号标题或场景标记（章号由系统添加）')
    claimed = claim_spans(body, tuple(released_surfaces))
    for claim in uncovered_occurrences(body, withheld_literals, claimed):
        errors.append(f'[withheld_literals] 出现未释放的专名字面 {claim.literal!r}'
                      f'（位置 {claim.start}）')
    count = count_body_chars(body)
    if not (band_low <= count <= band_high):
        errors.append(f'[length_band] 字数 {count} 超出篇幅带 [{band_low}, {band_high}]'
                      f'（目标 {target_chars}）')
    return errors
