#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 v3.1 的确定性 hinge 候选表。

候选表不是抽取器，也不判断某句话是不是 hinge。它只枚举三类最容易机械定位、又最常
让模型在回抄时浪费推理的原文字面：引号内容、带单位数字、含显式因果词的短句。模型
仍决定要不要保留以及 `kind`；一旦选择 `candidate_id`，原文字节与坐标完全由代码回取。

为什么保留 `phrase` 兜底：物件与隐式因果没有可靠的确定性枚举器。候选覆盖不足时强迫
模型只能选 id，会把“少输出 token”悄悄换成“少抓事实”。因此 v3.1 是关闭候选优先、
自由短语兜底；候选覆盖率要经付费 A/B 标定后，才有资格继续收窄。
"""
from __future__ import annotations

import re
from typing import Any

from novel.core.spans import content_only
from novel.stages.stage2.paragraphs import paragraph_spans

MAX_HINGE_CANDIDATES = 20

#: 候选不是语义真值，但也不能让“前 32 个易定位字面”成为一种隐形上下文裁剪。
#: 对话密集章曾让前半章引号填满整张表，后半章揭示/关系答复只能退回自由 phrase；
#: 反过来给满 32 项又实测诱发普通对白批量入选。这里按定位族保底、族内沿章节位置
#: 均匀抽样，并把引语保底收成 8 项；某族用不满的额度仍回流，普通短章不受影响。
#: ⚠ 20 是**曝光预算**不是 hinge 数量上限：模型没在表里找到真材料时仍可交 phrase。
_FAMILY_QUOTA = {'quoted': 8, 'numbered': 4, 'causal': 8}
assert sum(_FAMILY_QUOTA.values()) == MAX_HINGE_CANDIDATES

_QUOTED = re.compile(
    r'“[^”\n]{1,80}”|「[^」\n]{1,80}」|『[^』\n]{1,80}』|"[^"\n]{1,80}"')
_NUMBERED = re.compile(
    # 「第二天」要整项列出，不能从第二个字起截成「二天」；「下一秒」
    # 「上一回」则只是相对叙述过渡，不能从中间挖出「一秒 / 一回」冒充
    # 硬数字。若不在候选产地关掉，模型会把这些看似已预筛的短串大量
    # 选回，正式 hinge 数随之虚高。
    r'(?<![\d零一二三四五六七八九十百千万两半第上下每])'
    r'(?:第)?(?:\d+(?:\.\d+)?|[零一二三四五六七八九十百千万两半]+)'
    r'(?:个月|小时|分钟|公里|时辰|岁|年|月|日|天|时|点|刻|秒|文|两|钱|'
    r'人|名|个|次|回|件|只|匹|里|斤|担|成)')
_CAUSAL_MARKERS = (
    '因为', '由于', '只要', '除非', '否则', '以免', '为了', '既然', '所以',
    '因此', '若是', '如果', '得以', '才会', '方可',
)
_SENTENCE = re.compile(r'[^。！？；\n]{1,120}[。！？；]?')


def _candidate_source(text: str, start: int, end: int) -> dict[str, Any]:
    return {
        'start': start,
        'end': end,
        'text': text[start:end],
    }


def _spread(rows: list[tuple[str, int, int, int]],
            limit: int) -> list[tuple[str, int, int, int]]:
    """从有序行里按位置均匀取样；不把额度永远留给章首。"""
    if limit <= 0:
        return []
    if len(rows) <= limit:
        return list(rows)
    if limit == 1:
        return [rows[len(rows) // 2]]
    # 首尾都保留，中间等距。limit <= len(rows)，索引严格递增。
    return [rows[(index * (len(rows) - 1)) // (limit - 1)]
            for index in range(limit)]


def hinge_candidates(text: str) -> list[dict[str, Any]]:
    """单章正文 → 稳定候选表；id 只由正文位置顺序决定。"""
    ranked: list[tuple[str, int, int, int]] = []
    for para, (start, end) in enumerate(paragraph_spans(text), start=1):
        body = text[start:end]
        for family, pattern in (('quoted', _QUOTED), ('numbered', _NUMBERED)):
            for match in pattern.finditer(body):
                ranked.append((family, start + match.start(),
                               start + match.end(), para))
        for match in _SENTENCE.finditer(body):
            sentence = match.group(0)
            if any(marker in sentence for marker in _CAUSAL_MARKERS):
                ranked.append(('causal', start + match.start(),
                               start + match.end(), para))

    # 同一字节区间只列一次；若两族恰好得到相同区间，保留先枚举的族。
    #
    # ⚠ 引号正则允许「?」「......」这类只含标点的原文字面。它们的 ``text`` 非空，
    # 但 ``content_only`` 为空，不能构造正式 ``SourceSpan.normalized``。必须在 20 项
    # 截断之前剔除：若在 compiler 末端才发现，不仅会让一次已付费整跑 exit 70，
    # 还会让无效项白占候选额度、挤掉后面的有效材料。
    selected: dict[tuple[int, int], tuple[str, int, int, int]] = {}
    for row in sorted(ranked, key=lambda item: (item[1], item[2], item[0])):
        if not content_only(text[row[1]:row[2]]):
            continue
        selected.setdefault((row[1], row[2]), row)

    by_family = {
        family: sorted(
            (row for row in selected.values() if row[0] == family),
            key=lambda item: (item[1], item[2]),
        )
        for family in _FAMILY_QUOTA
    }
    reserved = [
        row
        for family, quota in _FAMILY_QUOTA.items()
        for row in _spread(by_family[family], quota)
    ]
    kept_keys = {(row[1], row[2]) for row in reserved}
    remaining = sorted(
        (row for row in selected.values() if (row[1], row[2]) not in kept_keys),
        key=lambda item: (item[1], item[2], item[0]),
    )
    room = MAX_HINGE_CANDIDATES - len(reserved)
    kept = reserved + _spread(remaining, room)
    kept.sort(key=lambda row: (row[1], row[2], row[0]))
    return [
        {
            'candidate_id': f'h{index:03d}',
            'para': para,
            'source': _candidate_source(text, start, end),
        }
        for index, (_family, start, end, para) in enumerate(kept, start=1)
    ]


def hinge_candidates_by_chapter(
    main_text: list[dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    return {
        int(section.get('chapter_ordinal') or 0):
        hinge_candidates(str(section.get('text') or ''))
        for section in main_text
    }


def prompt_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """只下发选择所需字段；绝对偏移仍留在代码侧。"""
    return [
        {
            'candidate_id': row['candidate_id'],
            'para': row['para'],
            'text': row['source']['text'],
        }
        for row in rows
    ]
