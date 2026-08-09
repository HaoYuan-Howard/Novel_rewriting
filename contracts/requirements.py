#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
requirements.py —— 把 `docs/REQUIREMENTS.md` 读成数据。

【为什么要解析 markdown 而不是另存一份结构化清单】那就是第二个落点。
需求文档是人写人读的那一份，也必须是**唯一**的那一份；一旦另存一份 YAML，
两边就会开始分家，而分家不会报错。解析器坏了会当场炸，那是可接受的失败方式。

【它让「谁在查」那一列变成可执行的】需求文档 §6 立了双向覆盖率，
本模块 + `tests/test_coverage.py` 就是它的执行落点。没有执行落点的验收方式，
与「只写在 prompt 里的约束」是同一个形状。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache

DOC_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'docs', 'REQUIREMENTS.md')

# 【定义】与【引用】必须分得开，否则一句「见 `B-20`」会被当成 B-20 的定义。
# 判据是**行首形态**：定义一律在表格行首或标题行首，引用一律在行内（多半带反引号）。
_R_DEF = re.compile(r'^\|\s*(R-\d+\.\d+[a-z]?)\s*\|')
_R_SECTION = re.compile(r'^###\s+(R-\d+)\s')
_Q_DEF = re.compile(r'^###\s+(Q-\d+)\s')
_B_DEF = re.compile(r'^\|\s*\*\*(B-\d+)\*\*')

# 「谁在查」那一列里出现的守卫记号
_GUARD_TOKENS = (('HARD', 'HARD'), ('INFO', 'INFO'), ('闸门', 'GATE'))


@dataclass(frozen=True)
class Who:
    """一条需求的「谁在查」列，解析成结构。"""
    raw: str
    guards: tuple[str, ...] = ()        # HARD / INFO / GATE 的子集
    human: bool = False                 # 按设计就由人判
    pending: bool = False               # 待建（R-13/R-14 这类新增能力）
    unguarded: bool = False             # 字面写了「无守卫」
    b_refs: tuple[str, ...] = ()        # 这一格里引用到的 B 编号

    @property
    def claims_guard(self) -> bool:
        """它声称自己有确定性守卫。正向覆盖率就查这些。"""
        return bool(self.guards)


@dataclass(frozen=True)
class Requirement:
    rid: str            # R-1.1 / Q-1 / B-01
    kind: str           # 'R' | 'Q' | 'B'
    text: str
    who: Who | None = None      # 只有 R 有这一列
    section: str = ''           # R-1.1 属于 R-1


@dataclass
class Doc:
    requirements: dict[str, Requirement] = field(default_factory=dict)

    def of_kind(self, kind: str) -> dict[str, Requirement]:
        return {k: v for k, v in self.requirements.items() if v.kind == kind}

    @property
    def r_ids(self) -> set[str]:
        return set(self.of_kind('R'))

    @property
    def b_ids(self) -> set[str]:
        return set(self.of_kind('B'))


def _parse_who(cell: str) -> Who:
    raw = cell.strip()
    guards = tuple(tag for lit, tag in _GUARD_TOKENS if lit in raw)
    return Who(
        raw=raw,
        guards=guards,
        human='人' in raw,
        pending='待建' in raw,
        unguarded='无守卫' in raw,
        b_refs=tuple(sorted(set(re.findall(r'B-\d+', raw)))),
    )


def _cells(line: str) -> list[str]:
    """markdown 表格行 → 单元格。首尾的空串丢掉。"""
    parts = line.split('|')
    if parts and not parts[0].strip():
        parts = parts[1:]
    if parts and not parts[-1].strip():
        parts = parts[:-1]
    return [p.strip() for p in parts]


@lru_cache(maxsize=1)
def load(path: str = DOC_PATH) -> Doc:
    """解析需求文档。**找不到文件直接抛**——静默返回空 Doc 会让双向覆盖率全绿。"""
    with open(path, encoding='utf-8') as f:
        lines = f.read().splitlines()

    doc = Doc()
    section = ''

    def put(rid: str, kind: str, text: str, who: Who | None = None, sec: str = '') -> None:
        if rid in doc.requirements:
            raise ValueError(f'需求编号重复：{rid}（第二次出现在 {text[:40]!r}）')
        doc.requirements[rid] = Requirement(rid=rid, kind=kind, text=text, who=who, section=sec)

    for line in lines:
        if m := _R_SECTION.match(line):
            section = m.group(1)
            put(section, 'R', line.split('·', 1)[-1].strip(), sec=section)
            continue
        if m := _Q_DEF.match(line):
            put(m.group(1), 'Q', line.split('·', 1)[-1].strip())
            continue
        if m := _B_DEF.match(line):
            cs = _cells(line)
            put(m.group(1), 'B', ' | '.join(cs[1:]))
            continue
        if m := _R_DEF.match(line):
            cs = _cells(line)
            # R 行恒为三列：编号 | 要求 | 谁在查
            if len(cs) < 3:
                raise ValueError(f'需求行少于三列，「谁在查」那一列是必填的：{line!r}')
            put(m.group(1), 'R', cs[1], who=_parse_who(cs[-1]), sec=section)

    if not doc.requirements:
        raise ValueError(f'{path} 里一条需求都没解析出来——多半是表格格式变了，解析器要跟着改')
    return doc
