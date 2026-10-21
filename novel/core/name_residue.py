#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""name_residue —— 普通正文的姓名残留定位（rc26，命名前移批 C）。

identity-text-v1 协议退役后，Stage5 正文直接以目标姓名成文：不再有人物提及／
普通义区间声明，也没有候选覆盖硬闸。产品规则（用户裁决）：正文里的**疑似**
旧名残留与疑似提前揭名只形成定位报告——不阻断流水线、不自动触发付费返修；
确定的结构错误仍走 fail fast（I-34）。

本模块是那个定位器的唯一实现（纯函数：不读写文件、不调模型、不判定语义）：

- 输入是 compile 期派生的监视行（`contracts/schema/state.py::NameWatch`，
  行内字面平铺同时是写手材料硬闸的面，见 `common.positive_material_issues`）；
- 扫描沿用字面扫描的长名优先单遍语义（更长的字面先认领，短字面的残余出现
  才算命中——「沈知遥」里的「知遥」不二次计数）；
- 输出只定位：哪个人物、哪类字面、在哪个字符区间出现。命中**不**宣称语义
  泄漏（普通同形词可能出现），也不授予任何业务状态；报告落 runtime 审计面，
  由监控页与交付 manifest 披露（I-38：不进回执、不进领域 schema）。

写手材料侧的同类命中是硬闸（`positive_material_leak`），与本文本报告是两面
判据，不互换。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from contracts.schema.state import NameWatch


@dataclass(frozen=True)
class NameResidue:
    """一次疑似姓名残留的定位：归因到监视行（码＋人物＋字面）与字符区间。"""

    code: str          # NameWatch.code：'old_name_residue' | 'unreleased_name'
    char_id: str
    literal: str
    start: int
    end: int

    def __str__(self) -> str:
        return self.message

    @property
    def message(self) -> str:
        kind = ('改名人物的源旧字面' if self.code == 'old_name_residue'
                else '本章尚未释放的人物目标字面')
        return (f'{kind} {self.literal!r}（人物 {self.char_id}）在正文第 '
                f'{self.start}–{self.end} 字符处出现——疑似残留/提前揭名，仅定位不阻断')


def _watch_rows(watch: Iterable[NameWatch]) -> tuple[tuple[str, str, str], ...]:
    """监视行 → (code, char_id, literal) 三元组；空字面与空行显式排除。"""
    return tuple((row.code, row.char_id, literal)
                 for row in watch for literal in row.literals if literal)


def name_residues(
    text: str, *, watch: Iterable[NameWatch] = (),
) -> tuple[NameResidue, ...]:
    """定位正文中的疑似姓名残留；不阻断、不修改文本、不宣称语义泄漏。

    同一区间被多行命中时只报一次（先匹配的行，按字面长度降序保证确定性）；
    报告按出现位置排序，位置相同按（code, char_id, literal）稳定排序。
    """
    rows = _watch_rows(watch)
    if not text or not rows:
        return ()
    ordered = sorted(set(rows), key=lambda row: (-len(row[2]), row[0], row[1], row[2]))
    pattern = re.compile('|'.join(re.escape(row[2]) for row in ordered))
    residues: list[NameResidue] = []
    cursor = 0
    for match in pattern.finditer(text):
        if match.start() < cursor:
            continue  # 已被更长字面认领的区间内部不二次计数
        row = next(row for row in ordered if row[2] == match.group())
        residues.append(NameResidue(code=row[0], char_id=row[1],
                                    literal=match.group(),
                                    start=match.start(), end=match.end()))
        cursor = match.end()
    return tuple(residues)


def residue_counts(residues: Iterable[NameResidue]) -> dict[str, int]:
    """按规则码汇总出现次数（披露读数；没有命中不记零占位）。"""
    counts: dict[str, int] = {}
    for residue in residues:
        counts[residue.code] = counts.get(residue.code, 0) + 1
    return counts


def residue_report(
    residues: Iterable[NameResidue], *, scope: str,
) -> dict:
    """runtime 审计落的定位报告（不含正文文本，I-38）。

    `scope` 是报告主体（如 'ch03'／'hook'）；章节报告逐章一份，hook 一份。
    """
    rows = tuple(residues)
    return {
        'scope': scope,
        'residues': [{'code': row.code, 'char_id': row.char_id,
                      'literal': row.literal, 'start': row.start, 'end': row.end,
                      'message': row.message} for row in rows],
        'counts': residue_counts(rows),
        'disclosure': ('疑似姓名残留定位报告：只定位归因，不阻断流水线、不触发'
                       '付费返修；命中不宣称语义泄漏（普通同形词可能出现）'),
    }


def watch_literals(watch: Iterable[NameWatch]) -> tuple[str, ...]:
    """监视行字面平铺（写手材料硬闸的检查面，与正文报告同源不同用）。"""
    return tuple(sorted({row[2] for row in _watch_rows(watch)}))
