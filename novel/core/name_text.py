"""身份引用文本的纯 compiler/validator；不猜人物、不读写文件、不调用模型。"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from contracts.schema.name_text import NameMention, NameTextSpan


@dataclass(frozen=True)
class NameTextIssue:
    code: str
    message: str
    start: int
    end: int
    literal: str = ''
    coordinate_space: str = 'prose'

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class ResolvedNameText:
    text: str
    mentions: tuple[NameMention, ...]
    non_name_spans: tuple[NameTextSpan, ...]
    errors: tuple[NameTextIssue, ...]
    origin_spans: tuple[tuple[int, int], ...] = ()


def validate_name_text_spans(text: str, spans: Iterable[NameTextSpan]) -> tuple[NameTextIssue, ...]:
    """只核对已声明区间的边界、顺序和非重叠，不判断片段的自然语言含义。"""
    errors: list[NameTextIssue] = []
    previous = 0
    for span in spans:
        if not 0 <= span.start < span.end <= len(text) or span.start < previous:
            errors.append(NameTextIssue('name_text_span', '身份文本区间越界、重叠或未排序',
                                        span.start, span.end))
        previous = max(previous, span.end)
    return tuple(errors)


def validate_name_mentions(text: str, mentions: Iterable[NameMention]) -> tuple[NameTextIssue, ...]:
    mentions = tuple(mentions)
    errors = list(validate_name_text_spans(text, mentions))
    for mention in mentions:
        if text[mention.start:mention.end] != mention.surface:
            errors.append(NameTextIssue('name_text_surface', '人物提及区间与正文称谓不一致',
                                        mention.start, mention.end, mention.surface))
    return tuple(errors)


def unclassified_name_occurrences(
    text: str, candidate_literals: Iterable[str], *, mentions: Iterable[NameMention] = (),
    non_name_spans: Iterable[NameTextSpan] = (),
) -> tuple[NameTextIssue, ...]:
    """词法只召回候选；无语义声明时报未决，绝不把普通词宣布为姓名泄漏。"""
    candidates = sorted(set(candidate_literals) - {''}, key=lambda s: (-len(s), s))
    if not candidates:
        return ()
    intervals: list[tuple[int, int]] = []
    for span in sorted((*mentions, *non_name_spans), key=lambda s: (s.start, s.end)):
        if intervals and span.start <= intervals[-1][1]:
            intervals[-1] = (intervals[-1][0], max(intervals[-1][1], span.end))
        else:
            intervals.append((span.start, span.end))
    pattern = re.compile('|'.join(re.escape(s) for s in candidates))
    return tuple(NameTextIssue('name_text_unclassified',
                               f'姓名候选 {m.group()!r} 的本次出现尚未声明人物或普通义',
                               m.start(), m.end(), m.group())
                 for m in pattern.finditer(text)
                 if not any(start <= m.start() and m.end() <= end for start, end in intervals))


def validate_resolved_name_text(
    text: str, *, name_mentions: Iterable[NameMention], non_name_spans: Iterable[NameTextSpan],
    surfaces: Mapping[str, Iterable[str]], candidate_literals: Iterable[str] = (),
) -> tuple[NameTextIssue, ...]:
    """纯正文及 canonical 声明复验；表、权限和候选覆盖必须同时闭合。"""
    mentions, non_name_spans = tuple(name_mentions), tuple(non_name_spans)
    errors = list(validate_name_mentions(text, mentions))
    errors.extend(validate_name_text_spans(text, non_name_spans))
    for mention in mentions:
        if mention.char_id not in surfaces or mention.surface not in surfaces[mention.char_id]:
            errors.append(NameTextIssue('name_text_reference',
                                       f'人物引用未登记或未授权：{mention.char_id}/{mention.surface}',
                                       mention.start, mention.end, mention.surface))
    all_spans = sorted((*mentions, *non_name_spans), key=lambda s: (s.start, s.end))
    errors.extend(validate_name_text_spans(text, all_spans))
    errors.extend(unclassified_name_occurrences(text, candidate_literals,
                                               mentions=mentions, non_name_spans=non_name_spans))
    if '[[' in text or ']]' in text:
        errors.append(NameTextIssue('name_text_syntax', '正式纯正文仍含身份协议定界符', 0, len(text)))
    return tuple(errors)


def resolve_name_text(
    text: str, surfaces: Mapping[str, Iterable[str]] | None = None,
    replacements: Mapping[str, str] | None = None, *, candidate_literals: Iterable[str] = (),
) -> ResolvedNameText:
    """解析标记并派生原始可见正文坐标；None 目录只用于结构读取，不授予业务采用。

    replacements 按明确 char_id 投影显示面；它不按称谓子串选择人物。
    """
    output: list[str] = []
    origins: list[tuple[int, int]] = []
    mentions: list[NameMention] = []
    ordinary: list[NameTextSpan] = []
    errors: list[NameTextIssue] = []
    cursor, length = 0, 0
    while cursor < len(text):
        start = text.find('[[', cursor)
        if start < 0:
            output.append(text[cursor:])
            origins.extend((i, i + 1) for i in range(cursor, len(text)))
            break
        plain = text[cursor:start]
        output.append(plain)
        origins.extend((i, i + 1) for i in range(cursor, start))
        length += len(plain)
        end = text.find(']]', start + 2)
        if end < 0:
            errors.append(NameTextIssue('name_text_syntax', '身份文本标记缺少结束符', start, len(text)))
            output.append(text[start:])
            origins.extend((i, i + 1) for i in range(start, len(text)))
            break
        token = text[start + 2:end]
        kind, separator, value = token.partition('|')
        if not separator or not value or '[[' in token or '|' in value:
            errors.append(NameTextIssue('name_text_syntax', '身份文本标记为空、嵌套或有非法分隔符',
                                        start, end + 2))
            output.append(text[start:end + 2])
            origins.extend((i, i + 1) for i in range(start, end + 2))
            length += end + 2 - start
        elif kind == 'text':
            output.append(value)
            origins.extend((start, end + 2) for _ in value)
            ordinary.append(NameTextSpan(start=length, end=length + len(value)))
            length += len(value)
        elif (kind.startswith('name:') and kind[5:]
              and not any(c in kind[5:] for c in ('[', ']', '\r', '\n'))):
            char_id = kind[5:]
            if surfaces is not None and (char_id not in surfaces or value not in surfaces[char_id]):
                errors.append(NameTextIssue('name_text_reference',
                                            f'人物引用未登记或未授权：{char_id}/{value}',
                                            start, end + 2, value))
            rendered = replacements.get(char_id, value) if replacements is not None else value
            output.append(rendered)
            origins.extend((start, end + 2) for _ in rendered)
            mentions.append(NameMention(start=length, end=length + len(rendered),
                                         char_id=char_id, surface=rendered))
            length += len(rendered)
        else:
            errors.append(NameTextIssue('name_text_syntax', '未知身份文本标记', start, end + 2))
            output.append(text[start:end + 2])
            origins.extend((i, i + 1) for i in range(start, end + 2))
            length += end + 2 - start
        cursor = end + 2
    resolved = ''.join(output)
    if ']]' in resolved or '[[' in resolved:
        errors.append(NameTextIssue('name_text_syntax', '文本含未解析的身份协议定界符', 0, len(text)))
    errors = [replace(error, coordinate_space='wire') for error in errors]
    for error in unclassified_name_occurrences(resolved, candidate_literals,
                                               mentions=mentions, non_name_spans=ordinary):
        spans = origins[error.start:error.end]
        errors.append(replace(error, start=min(s[0] for s in spans), end=max(s[1] for s in spans),
                              coordinate_space='wire'))
    return ResolvedNameText(resolved, tuple(mentions), tuple(ordinary), tuple(errors), tuple(origins))
