#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook_writer —— Hook 成文的机械 validator 与请求身份（F6，Stage5）。

【Writer 是 realizer，不是 designer】
请求只从 HookContext 构建（沿用原型 realizer 的系统契约，去掉强制第一人称）；
结构已由 Hook Design 完成。机械 validator 只查：非空、编码、包装行、显式身份
引用许可与候选逐次声明，以及篇幅硬上限（rc24：包络由
compiler 按书级章预算派生进 Context，超 cap 拒收；目标区间 low/high 只随请求
送达、不作拒稿条件）。机械通过**不**产生「语义已验」字段——回执的 semantic
恒为 unverified，普通义与人物声明的语义准确性也未经机械证明。

【失败归属】
格式失败 = realization failure：同一 Context 下有限重写（最多再 2 次），
重写请求 = 同一 Context + 上一稿的机械错误，身份确定、可免费恢复；上游设计
错误回 Stage4；付费状态未知阻断。超篇幅硬闸的人工恢复入口是 hook_writer 的
wire 免费采用（`--wire-dir`，同一 validator 验收），不重购。
"""
from __future__ import annotations

import hashlib
import json
import re

from contracts.schema.hook import HookContext
from contracts.schema.receipt import HOOK_MECHANICAL_CHECKS, HOOK_VALIDATOR_VERSION
from contracts.schema.name_text import NameMention, NameTextSpan
from novel.core.name_text import ResolvedNameText, resolve_name_text, validate_resolved_name_text
from novel.core.text import count_non_whitespace_chars
from novel.stages.common import render_hook_context

#: 已通过的机械检查项名单（回执 mechanical_checks 的取值面）。
MECHANICAL_CHECKS = HOOK_MECHANICAL_CHECKS

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
    """实际请求与 Stage4 终检共用同一 renderer。"""
    return render_hook_context(context)


def build_rewrite_user_prompt(context: HookContext, errors: list[str]) -> str:
    """重写请求 = 同一 Context + 上一稿的机械错误。身份确定，可免费恢复。"""
    return (build_hook_user_prompt(context)
            + '\n\n上一次稿未通过机械校验，原因：' + '；'.join(errors)
            + '。请修正后重写，仍只依据同一 HookContext，不得新增或改换事实。')


def resolve_hook_prose(prose: str, context: HookContext) -> ResolvedNameText:
    """身份声明 → 纯楔子与提及表；只做协议与身份许可的确定性解析。"""
    return resolve_name_text(
        prose, surfaces={row.char_id: row.surfaces for row in context.usable_surfaces},
        candidate_literals=context.name_candidates)


def validate_hook_prose(
    prose: str, context: HookContext, *,
    name_mentions: tuple[NameMention, ...] | None = None,
    non_name_spans: tuple[NameTextSpan, ...] | None = None,
) -> list[str]:
    """机械校验：只报告，不修改；错误信息带检查项名（I-36）。"""
    errors: list[str] = []
    if name_mentions is None and non_name_spans is None:
        resolved = resolve_hook_prose(prose or '', context)
        raw, name_issues = resolved.text, resolved.errors
    elif name_mentions is not None and non_name_spans is not None:
        raw = prose or ''
        name_issues = validate_resolved_name_text(
            raw, name_mentions=name_mentions, non_name_spans=non_name_spans,
            surfaces={row.char_id: row.surfaces for row in context.usable_surfaces},
            candidate_literals=context.name_candidates)
    else:
        return ['[name_text] 人物提及与普通义声明须同时提供，缺失不是合法空表']
    errors.extend(f'[{issue.code}] {issue.message}' for issue in name_issues)
    text = raw.strip()
    if not text:
        return ['[non_empty] 楔子为空']
    if '\ufffd' in text or _CONTROL.search(text):
        errors.append('[encoding] 存在 U+FFFD 或控制字符')
    if '```' in text or text.lstrip().startswith('#') or _TITLE_LINE.match(
            text.splitlines()[0].strip()):
        errors.append('[no_wrapper] 存在 markdown 围栏、标题或包装行')
    chars = count_non_whitespace_chars(text)
    if chars > context.length_cap:
        errors.append(
            f'[length_cap] 楔子 {chars} 字超过硬上限 {context.length_cap} 字'
            f'（目标区间 {context.length_low}–{context.length_high}，非空白字符）')
    return errors
