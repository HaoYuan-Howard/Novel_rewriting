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
    """请求 user 段：只从 HookContext 构建，不掺源文或盘上材料。"""
    return ('HookContext:\n```json\n'
            + json.dumps(context.model_dump(mode='json'), ensure_ascii=False, indent=1)
            + '\n```\n请写出楔子。')


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
