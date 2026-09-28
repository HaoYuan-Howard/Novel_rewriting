#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook_writer —— Hook 成文的机械 validator 与请求身份（F6，Stage5）。

【Writer 是 realizer，不是 designer】
请求只从 HookContext 构建（沿用原型 realizer 的系统契约，去掉强制第一人称）；
结构已由 Hook Design 完成。机械 validator 只查：非空、长度、编码、包装行与
withheld 专名字面泄漏（先认领已释放称谓，再查未覆盖出现）。机械通过**不**
产生「语义已验」字段——回执的 semantic 恒为 unverified。

【失败归属】
超长或格式失败 = realization failure：同一 Context 下有限重写（最多再 2 次），
重写请求 = 同一 Context + 上一稿的机械错误，身份确定、可免费恢复；上游设计
错误回 Stage4；付费状态未知阻断。
"""
from __future__ import annotations

import hashlib
import json
import re

from contracts.schema.hook import HookContext
from novel.stages.common import claim_spans, uncovered_occurrences

#: 机械 validator 的版本：回执 validator_version 记这一位，规则变化必须换版。
HOOK_VALIDATOR_VERSION = 'str06-hook-mechanical-v1'

#: 已通过的机械检查项名单（回执 mechanical_checks 的取值面）。
MECHANICAL_CHECKS = (
    'non_empty', 'length', 'encoding', 'no_wrapper', 'withheld_literals',
)

#: realization failure 的同 Context 重写上限（首次 + 再 2 次）。
MAX_REWRITES = 2

_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
_TITLE_LINE = re.compile(r'^(楔子|引子|序章|prologue)[:：]?\s*$')


def context_sha256(context: HookContext) -> str:
    """HookContext 内容身份（规范化 JSON；回执绑定与免费重建核对用）。"""
    payload = json.dumps(context.model_dump(mode='json'), ensure_ascii=False,
                         sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def build_hook_user_prompt(context: HookContext) -> str:
    """请求 user 段（R2 分层渲染）：只从 HookContext 构建，不掺源文或盘上材料。

    材料是必须写出；禁止揭示与受保护线程是禁区；起笔事实以延续/背景呈现；
    withheld_literals 是 validator 材料，不进阅读面（与正文章包同一纪律）。
    """
    names = {r.char_id: r.surfaces for r in context.usable_surfaces}

    def _name(char_id: str) -> str:
        faces = names.get(char_id)
        return faces[0] if faces else char_id

    person = '第一人称' if context.pov.person == 'first' else '第三人称'
    focalizers = '、'.join(_name(f) for f in context.pov.focalizers)
    parts = ['【起笔已成立的事实】（开场前已成立；自然带过，不作为任务交代）']
    parts.extend(f'- {fact.text}' for fact in context.opening_facts)
    parts.append('【必须写出的材料】（按给定顺序组织；不得重选或换序）')
    for index, material in enumerate(context.materials, start=1):
        if material.presentation is not None:
            parts.append(f'- 材料{index}：{material.presentation}')
        else:
            parts.append(f'- 材料{index}：{material.event}')
            for delta in material.deltas:
                parts.append(f'  随材料成立：{delta}')
    parts.append('【禁止提前兑现】（禁区：不得出现、不得暗示）')
    parts.extend(f'- {line}' for line in context.forbidden_reveals)
    parts.append('【叙述与组织】')
    parts.append(f'- 视角：{person}，聚焦 {focalizers}')
    parts.append(f'- 组织方式：{context.design_brief}')
    parts.append(f'- 文风与口吻：{context.delivery_brief}')
    parts.append('【可用称谓】（只能使用这些字面）')
    surfaces = '、'.join(sorted({s for r in context.usable_surfaces
                                 for s in r.surfaces}))
    parts.append(f'- {surfaces}' if surfaces else '- （无）')
    parts.append('【交付要求】')
    parts.append(f'- 篇幅目标约 {context.target_chars} 字，'
                 f'绝对不得超过 {context.hard_max_chars} 字。')
    parts.append('请写出楔子。')
    return '\n'.join(parts) + '\n'


def build_rewrite_user_prompt(context: HookContext, errors: list[str]) -> str:
    """重写请求 = 同一 Context + 上一稿的机械错误。身份确定，可免费恢复。"""
    return (build_hook_user_prompt(context)
            + '\n\n上一次稿未通过机械校验，原因：' + '；'.join(errors)
            + '。请修正后重写，仍只依据同一 HookContext，不得新增或改换事实。')


def validate_hook_prose(prose: str, context: HookContext) -> list[str]:
    """机械校验：只报告，不修改；错误信息带检查项名（I-36）。"""
    errors: list[str] = []
    text = (prose or '').strip()
    if not text:
        return ['[non_empty] 楔子为空']
    if len(text) > context.hard_max_chars:
        errors.append(
            f'[length] 长度 {len(text)} 超过 hard_max_chars={context.hard_max_chars}')
    if '\ufffd' in text or _CONTROL.search(text):
        errors.append('[encoding] 存在 U+FFFD 或控制字符')
    if '```' in text or text.lstrip().startswith('#') or _TITLE_LINE.match(
            text.splitlines()[0].strip()):
        errors.append('[no_wrapper] 存在 markdown 围栏、标题或包装行')
    released = {s for r in context.usable_surfaces for s in r.surfaces}
    claimed = claim_spans(text, released)
    for claim in uncovered_occurrences(text, context.withheld_literals, claimed):
        errors.append(
            f'[withheld_literals] 出现未释放的专名字面 {claim.literal!r}'
            f'（位置 {claim.start}）')
    return errors
