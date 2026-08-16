#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe_nonbody_word.py —— 往 `NONBODY_KINDS` 加词之前必须先过的那道闸。

【为什么需要一道闸】剔除是**破坏性**的：判据一旦把某一行认成非正文标记，
它之后的正文全部丢弃、盘上不留副本。一个假阳会静默吃掉半本书，
而**词表只会单向增长**——加错了的词不会有任何东西提醒你。

【它回答什么】把候选词放到全语料上，问三件事：
  ① 它在正文里以「整行」形态出现过吗（`core` 全等）→ 有就是**直接的误切**
  ② 它以该词开头的短行形态出现过吗（`core` 以它开头且 ≤ 上限）→ 有就是开头分支的误切
  ③ 它和完结标记词表撞车吗 → 撞了说明它走的是另一条路，不该进这张表

③ 不需要语料，永远会跑。①② 需要语料，没有语料时**明说「没查」而不是判「安全」**。

【为什么用位置而不是「切点之后」】一个词若只出现在全文 95% 处，它像标记；
出现在 30% 处，加了它就会把半本书砍掉。位置本身就是判据，
所以这个工具**不依赖分割器**——它不需要知道当前切点在哪，那正是它要改变的东西。

用法（语料目录**必须自己指**，本机不再有默认语料）：
    python tools/probe_nonbody_word.py 外传 --corpus D:/somewhere/Novel_data
    NOVEL_CORPUS=D:/somewhere/Novel_data python tools/probe_nonbody_word.py 外传
    python tools/probe_nonbody_word.py 外传          # 不指语料：只出 ③ 的结论，①② 报「没查」

退出码：0 = 全部候选词安全；1 = 至少一个有假阳风险或与完结标记撞车。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts.invariants import (  # noqa: E402
    END_MARKER_WORDS, NONBODY_HEAD_MAX_CP, NONBODY_KINDS,
    NONBODY_LEAD_STRIP, NONBODY_TRAIL_STRIP,
)

_LEAD = re.compile(NONBODY_LEAD_STRIP)
_TRAIL = re.compile(NONBODY_TRAIL_STRIP)


def core_of(line: str) -> str:
    """标记行 → `core`。与判据同源：两条剥壳规则都从 `contracts.invariants` 取。"""
    return _TRAIL.sub('', _LEAD.sub('', line.strip()))


def read_normalized(path: Path) -> str | None:
    """按 `R-1.2` 的口径归一化：去 BOM、换行统一。解不开就返回 None（跳过，不猜）。"""
    raw = path.read_bytes()
    for enc in ('utf-8-sig', 'utf-8', 'gb18030'):
        try:
            return raw.decode(enc).replace('\r\n', '\n').replace('\r', '\n')
        except UnicodeDecodeError:
            continue
    return None


def probe(word: str, books: list[tuple[str, str]]) -> dict:
    """在全部书上扫一个词。返回三类命中与它们的位置。"""
    exact, head, plain = [], [], 0
    for name, text in books:
        lines, pos = text.split('\n'), 0
        for ln in lines:
            start, pos = pos, pos + len(ln) + 1
            s = ln.strip()
            if word not in s:
                continue
            c = core_of(s)
            where = start / len(text) if text else 0.0
            if c == word:
                exact.append((name, s, where))
            elif c.startswith(word) and len(c) <= NONBODY_HEAD_MAX_CP:
                head.append((name, s, where))
            else:
                plain += 1
    return {'exact': exact, 'head': head, 'plain': plain}


def main() -> int:
    ap = argparse.ArgumentParser(description='往 NONBODY_KINDS 加词之前的假阳闸门')
    ap.add_argument('words', nargs='+', help='候选词，可多个')
    ap.add_argument('--corpus', default=os.environ.get('NOVEL_CORPUS'),
                    help='语料目录（递归找 *.txt）。也可用 NOVEL_CORPUS 环境变量。'
                         '**没有默认值**——版权正文不进仓，必须显式指路')
    args = ap.parse_args()

    root = Path(args.corpus) if args.corpus else None
    books: list[tuple[str, str]] = []
    if root and root.is_dir():
        for p in sorted(root.rglob('*.txt')):
            t = read_normalized(p)
            if t:
                books.append((p.stem, t))
    print(f'语料：{len(books)} 本  ←  {root if root else "（未指定）"}')
    if not books:
        print('⚠ 没读到任何语料。①② 两项**没查**——这不等于「安全」。\n'
              '  用 --corpus 指到语料目录再跑一次；只有 ③ 的结论是可信的。\n'
              '  ⚠ 本机原来那份 163 本语料（原型仓 Novel_refactoring）已于 2026-08-16 删除，\n'
              '    这道闸现在**必须自带语料**才跑得动 ①②。')

    bad = False
    for w in args.words:
        print(f'\n{"=" * 70}\n候选词：{w!r}\n{"=" * 70}')

        if w in NONBODY_KINDS:
            print(f'  · 它已经在 NONBODY_KINDS 里了')
        if w in END_MARKER_WORDS:
            print(f'  ✗ ③ 与完结标记词表撞车（END_MARKER_WORDS={END_MARKER_WORDS}）。'
                  f'\n      完结标记只定切点、不进 dropped；非正文标记进 dropped 并记账。'
                  f'\n      同一个词落进两张表，产物会同时按两种口径记它。**不要加。**')
            bad = True
            continue
        print(f'  ✓ ③ 与完结标记词表无交集')

        if not books:
            print('  ? ①② 没查（没有语料）')
            continue

        r = probe(w, books)
        n_ex, n_hd = len(r['exact']), len(r['head'])
        print(f'  {"✗" if n_ex else "✓"} ① 整行全等命中：{n_ex} 处'
              f'{"    ← 加了它，这些位置会被当成标记，其后正文全部丢弃" if n_ex else ""}')
        for name, s, where in r['exact'][:6]:
            mark = '  ← 在全文前段，加了会砍掉大半本书' if where < 0.5 else ''
            print(f'        {where:6.1%}  {name[:38]:38} {s[:32]!r}{mark}')
        print(f'  {"✗" if n_hd else "✓"} ② 以它开头的短行（core ≤ {NONBODY_HEAD_MAX_CP}）：{n_hd} 处')
        for name, s, where in r['head'][:6]:
            print(f'        {where:6.1%}  {name[:38]:38} {s[:32]!r}')
        print(f'  · 普通句子里含它：{r["plain"]} 行（**用全等匹配就碰不到它们**）')

        if n_ex or n_hd:
            bad = True
            print(f'\n  ⇒ 有假阳风险。可选：换更严的匹配（全等而非包含/开头），'
                  f'或放弃这个词。\n'
                  f'    ⚠ 别用「提高长度上限」来绕——那只会把静默假阳区扩大。')
        else:
            print(f'\n  ⇒ 安全。加词后还要做两件事：'
                  f'\n      1) 往 tests/fixtures_stage1.py 补一份会命中它的合成样本'
                  f'\n      2) 全语料重跑一遍分章，确认 dropped 的条数与切点没有变化')

    print(f'\n{"=" * 70}')
    print('结论：' + ('有候选词不安全，见上。' if bad else '全部候选词通过。'))
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
