#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 issues 协议的有界回答恢复：状态机、内容保全与 job 级持久计数器。

原始回答、失败原因与调用身份先持久化（现役 work unit 账），再按以下顺序
分类处理；新协议不复用重新附带完整 ``task.prompt`` 的旧格式纠错路径：

===== ============================================================
层级   行为
===== ============================================================
确定性 BOM、空白、完整单层代码围栏、字符串外尾逗号及登记过的无语义
恢复    包装；只删除可证明无语义的运输内容，不发起调用。
格式    仅发送原回答、issue schema 和解析错误；不附原文、业务产物或
模型修复 额外历史材料，使用新 session；**最多一次**。必须通过独立内容
        保全检查，无法建立证明时停止消费该回答。
语义重试 截断、缺失 issues、矛盾候选或内容保全不可证明时触发；复用
        同一 prepared 包，不附旧推理；**最多一次**。
Provider 仅处理 provider／传输故障；原配置新 session；**最多一次**。
恢复
免费提交 已有合法回答、checkpoint 或 prepared 时，优先重放原决定补尾。
恢复
===== ============================================================

所有可能付费的调用都计入 job 上限、原工作单元额度及书级授权；格式修复只是
统计分类不同，不视为免费。恢复计数器以 work unit 账的 ``recovery_kind`` 为
唯一来源，跨重启、换 session、换 epoch 与范围修订保持累计。
"""
from __future__ import annotations

import json
from typing import Any, Final

#: 每个恢复分支的 job 级上限；所有分支共用 work unit 的内容额度。
RECOVERY_CAPS: Final[dict[str, int]] = {
    'initial_semantic': 1,
    'format_repair': 1,
    'semantic_retry': 1,
    'provider_recovery': 1,
}

#: 登记过的无语义包装键：单键 object 且值为 object 时可展开一层。
#: 额外问题描述、多份候选或未知包装不能通过裁剪变成合法回答。
_REGISTERED_WRAPPERS: Final = ('answer', 'response', 'result', 'issues_review')


class RecoveryExhausted(RuntimeError):
    """恢复槽位或内容额度耗尽；调用方进入明确失败终态，不继续购买结果。"""


def _strip_bom_and_whitespace(text: str) -> str:
    return text.lstrip('﻿ \t\r\n').rstrip()


def _strip_single_code_fence(text: str) -> str | None:
    """完整单层代码围栏：一对 ``` 包裹且正文其余位置没有第三个围栏标记。"""
    stripped = text.strip()
    if not stripped.startswith('```'):
        return None
    first_newline = stripped.find('\n')
    if first_newline < 0:
        return None
    opener = stripped[:first_newline].strip()
    if opener != '```' and not opener.startswith('```'):
        return None
    tail = stripped[first_newline + 1:]
    end = tail.rfind('```')
    if end < 0:
        return None
    body = tail[:end]
    if '```' in body:
        return None
    return body.strip('\r\n') or None


def _strip_outer_trailing_commas(text: str) -> str | None:
    """只删除字符串字面量之外、紧邻 ``]``/``}`` 前的逗号；其余一概不动。"""
    out: list[str] = []
    in_string = False
    escaped = False
    changed = False
    for char in text:
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            out.append(char)
            continue
        if char in ']}':
            while out and out[-1] in ' \t\r\n':
                out.pop()
            if out and out[-1] == ',':
                out.pop()
                changed = True
                while out and out[-1] in ' \t\r\n':
                    out.pop()
            out.append(char)
            continue
        out.append(char)
    # 顶部对象之后的孤立尾逗号同样是可证明无语义的运输尾巴。
    while out and out[-1] in ' \t\r\n':
        out.pop()
    if out and out[-1] == ',':
        out.pop()
        changed = True
        while out and out[-1] in ' \t\r\n':
            out.pop()
    return ''.join(out) if changed else None


def _unwrap_registered(value: Any) -> tuple[Any, str] | None:
    if isinstance(value, dict) and len(value) == 1:
        key, inner = next(iter(value.items()))
        if key in _REGISTERED_WRAPPERS and isinstance(inner, dict):
            return inner, f'unwrap:{key}'
    return None


def deterministic_recovery(raw: str) -> tuple[str, list[str]] | None:
    """把可证明无语义的运输内容剥掉，返回（清理后文本, 步骤）或 ``None``。"""
    steps: list[str] = []
    text = raw
    if text != (stripped := _strip_bom_and_whitespace(text)):
        text = stripped
        steps.append('strip_bom_whitespace')
    fence = _strip_single_code_fence(text)
    if fence is not None:
        text = fence
        steps.append('strip_code_fence')
    commas = _strip_outer_trailing_commas(text)
    if commas is not None:
        text = commas
        steps.append('strip_trailing_commas')
    if not steps:
        return None
    return text, steps


def parse_issue_answer(raw: str) -> tuple[dict[str, Any], list[str]]:
    """严格解析 + 确定性恢复；返回（document, 恢复步骤）。

    重复 JSON 键、未知包装、多份候选或截断迹象不会被裁剪成合法回答。
    """
    steps: list[str] = []

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        document: dict[str, Any] = {}
        for key, value in pairs:
            if key in document:
                raise AnswerParseError('duplicate_json_key')
            document[key] = value
        return document

    def loads(text: str) -> Any:
        return json.loads(text, object_pairs_hook=unique_object)

    text = raw
    cleaned = deterministic_recovery(text)
    if cleaned is not None:
        text, steps = cleaned
    try:
        value = loads(text)
    except AnswerParseError:
        raise
    except (ValueError, TypeError):
        # 尾逗号只清了最外层的情形；这里不扩大裁剪面，交给格式修复。
        raise AnswerParseError('invalid_json') from None
    unwrapped = _unwrap_registered(value)
    if unwrapped is not None:
        value, step = unwrapped
        steps.append(step)
    if not isinstance(value, dict):
        raise AnswerParseError('top_level_not_object')
    if 'issues' not in value and 'schema' not in value:
        raise AnswerParseError('missing_issues')
    return value, steps


class AnswerParseError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def content_preserved(original: dict[str, Any], repaired: dict[str, Any]) -> bool:
    """格式修复的独立内容保全检查；语义内容逐项相等，顺序不敏感。

    问题数量、目标、FAIL／UNVER、问题文字、证据与候选 patch 都不得删改。
    """
    def semantic(document: dict[str, Any]) -> Any:
        issues = document.get('issues')
        if not isinstance(issues, list):
            return None
        rows = []
        for issue in issues:
            if not isinstance(issue, dict):
                return None
            rows.append(json.dumps(issue, ensure_ascii=False, sort_keys=True,
                                   separators=(',', ':')))
        return sorted(rows)

    before, after = semantic(original), semantic(repaired)
    if before is None or after is None:
        return False
    return before == after


def looks_truncated(raw: str, document: dict[str, Any] | None) -> bool:
    """截断迹象：schema 已声明但 answer 缺失，或 JSON 在字符串/括号中截断。"""
    if document is None:
        return True
    if 'schema' in document and 'answer' not in document:
        return True
    text = raw.strip()
    return bool(text) and not text.rstrip().endswith('}')


def format_repair_prompt(
    original_raw: str, output_schema: dict[str, Any] | None, error: str,
) -> str:
    """格式模型修复的唯一 prompt：原回答 + issue schema + 解析错误。

    不附原文、业务产物或额外历史材料；模型只做运输归一，不得增删问题。
    """
    schema_text = json.dumps(
        output_schema, ensure_ascii=False, sort_keys=True) if output_schema else '（本轮无原生 schema）'
    return f"""你是一次纯格式修复的执行者。下面提供三样东西：上一轮模型的原始回答、
本轮必须满足的输出 JSON schema、以及确定性解析器给出的错误类别。

规则：
1. 只把原始回答修整成符合 schema 的裸 JSON object；不使用 Markdown、代码围栏或解释文字。
2. 不得增删或改写任何问题语义：issues 的数量、target、state、issue_type、problem 文字、
   evidence_refs 与 candidate_patch 必须逐项保留；没有 issues 就输出空数组。
3. 不得引入原文、业务产物或任何新事实；你没有任何审阅材料。
4. 原始回答不可修复成合法 schema 时，原样输出最接近的合法 JSON object，不要发明内容。

<deterministic-rejection>
错误类别：{error}
</deterministic-rejection>

<original-answer>
{json.dumps(original_raw, ensure_ascii=False)}
</original-answer>

<output-schema>
{schema_text}
</output-schema>
"""


def counters(budget: Any) -> dict[str, int]:
    """从 work unit 账恢复 job 级计数器；跨重启/session/epoch 保持累计。"""
    used = budget.recovery_kinds_used() if hasattr(budget, 'recovery_kinds_used') else {}
    result = {}
    for kind, cap in RECOVERY_CAPS.items():
        result[kind] = {'used': int(used.get(kind, 0)), 'cap': cap,
                        'remaining': max(0, cap - int(used.get(kind, 0)))}
    return result


def require_slot(budget: Any, kind: str) -> None:
    """调度前核验并预留调用额度；耗尽时进入明确失败终态。"""
    state = counters(budget).get(kind)
    if state is None:
        raise RecoveryExhausted(f'未登记的恢复类别：{kind}')
    if state['remaining'] <= 0:
        raise RecoveryExhausted(
            f'{kind} 恢复槽位已用尽（{state["used"]}/{state["cap"]}）；'
            '进入明确失败终态，接受闸保持关闭')
