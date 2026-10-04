"""供应商usage的单一读面：保留原证据，解析值只用于记账和诊断。

reasoning是completion的子口径，不增加输出；0与未知分开。坏读数不使已购
回答丢失或触发重购，合法的其他读数仍可记录。解析纯函数与日志边界分开。
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


TOKEN_FIELDS = ('prompt_tokens', 'completion_tokens', 'reasoning_tokens',
                'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens')


@dataclass(frozen=True)
class ParsedUsage:
    values: Mapping[str, int | None]
    issues: tuple[str, ...] = ()

    @property
    def answer_tokens(self) -> int | None:
        completion = self.values['completion_tokens']
        reasoning = self.values['reasoning_tokens']
        return None if completion is None or reasoning is None else completion - reasoning


def parse_usage(raw: Any) -> ParsedUsage:
    """读取同一次响应的usage，不改原对象、不猜缺失值、不抛计量形状异常。"""
    if raw is None:
        return ParsedUsage(dict.fromkeys(TOKEN_FIELDS))
    if not isinstance(raw, Mapping):
        return ParsedUsage(dict.fromkeys(TOKEN_FIELDS), ('usage_not_object',))
    issues: list[str] = []

    def count(value: Any, path: str) -> int | None:
        if value is None:
            return None
        if type(value) is not int or value < 0:
            issues.append(f'{path}:invalid_count')
            return None
        return value

    values = {field: count(raw.get(field), field) for field in TOKEN_FIELDS}
    reasoning_invalid = any(issue.startswith('reasoning_tokens:') for issue in issues)
    details = raw.get('completion_tokens_details')
    nested = None
    if details is not None:
        if not isinstance(details, Mapping):
            issues.append('completion_tokens_details:not_object')
            reasoning_invalid = True
        else:
            before = len(issues)
            nested = count(details.get('reasoning_tokens'),
                           'completion_tokens_details.reasoning_tokens')
            reasoning_invalid |= len(issues) != before
    top = values['reasoning_tokens']
    if top is not None and nested is not None and top != nested:
        issues.append('reasoning_tokens:conflicting_values')
        reasoning_invalid = True
    reasoning = top if top is not None else nested
    completion = values['completion_tokens']
    if reasoning is not None and completion is not None and reasoning > completion:
        issues.append('reasoning_tokens:exceeds_completion')
        reasoning_invalid = True
    values['reasoning_tokens'] = None if reasoning_invalid else reasoning
    return ParsedUsage(values, tuple(issues))


def ledger_usage(raw: Any) -> dict[str, int | None]:
    """成本写入边界共用的投影；诊断不改变成功／失败、内容轮或请求身份。"""
    parsed = parse_usage(raw)
    if parsed.issues:
        logging.getLogger(__name__).warning('供应商用量读数未决：%s', ', '.join(parsed.issues))
    return dict(parsed.values)
