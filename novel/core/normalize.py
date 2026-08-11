#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
normalize.py —— 落盘前那条**确定性改写链**：标点 → 引号体 → 段落分隔。
**逐字移植自原型 `Stage5/stage5_common.py`，注释一起搬。**

【为什么算法在 core、口径在各层】三步的**算法**是同一套，而**拿谁当基准**逐层不同：
  · stage5 的成稿：基准是全书正文（引号体按源文频次、段落按 `measurable.body`）；
  · stage6 的文案：基准是**原作文案段**——文案与正文原则上可以用不同引号体、
    不同段落分隔（盘上两处一致，但那是巧合不是契约）。
各层自己抄一份算法必然漂，而漂的表现是**其中一处悄悄不再归一**（判据 25）。

⚠ 三步都必须在**校验之前**跑：校验与落盘看同一份最终字节，不留两套。
原型为此付过学费——`--recheck` 从前把定稿原样搬过去，于是候选归一了、
而真正拼进 `book.txt` 的那一份没有，报告全绿而交付的书里版式是坏的。

⚠ 对保真是安全的，**而且是由构造保证的**：四套保真校验与重合率全部工作在
【内容字符空间】（`text.norm()` 把标点整体剥掉），所以动标点不可能影响其中任何一条。
"""
from __future__ import annotations

import re

#: 标点归一表。**顺序是承重的**：`......` 那条必须先于半角逗号那条。
_PUNCT_FIX: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'\.{2,}'), '……'),                    # 原文的 `......` → 规范省略号（须先于下一条）
    (re.compile(r'(?<![0-9]),(?![0-9])'), '，'),        # 数字千分位不动
    (re.compile(r'!'), '！'),
    (re.compile(r'\?'), '？'),
    (re.compile(r';'), '；'),
    (re.compile(r'(?<![0-9]):(?![0-9])'), '：'),        # 时刻 3:00 不动
    (re.compile(r'(?<=[一-鿿])\.(?![0-9])'), '。'),   # 汉字后的句点才是句号，小数点不动
]

_QUOTE_OPEN = {'“': '”', '「': '」', '『': '』', '‘': '’', '"': '"'}
_QUOTE_CLOSE = frozenset('”」』’"')


def normalize_punctuation(text: str | None) -> tuple[str, int]:
    """标点归一到规范全角。返回 `(归一后的文本, 改动字符数)`。"""
    out = text or ''
    n = 0
    for rx, rep in _PUNCT_FIX:
        out, k = rx.subn(rep, out)
        n += k
    return out, n


def normalize_quotes(text: str | None, primary: str = '「」',
                     secondary: str = '『』') -> tuple[str, int, list[str]]:
    """引号体归一到本书的目标样式。返回 `(新文本, 改动数, problems)`。

    **配对失败（栈未空 / 闭无开 / 奇偶判不了）⇒ 整段原样返回、问题进 `problems`。**
    引号错乱多半是台词归属错位，那不猜（判据 26），交人工。

    ⚠ **不能盲互换**：某本书的目标样式是直角 `「」`，而它成稿里翻转来的恰是弯引号 `“”`
    ——一个「把所有 `“` 换成 `「`」的正则会先把本就正确的 `「」` 当成没翻、再把翻转的
    `“”` 换对，看起来是对的；但一旦遇到 `「“引语『嵌套』”」` 或直引号兜底，
    盲换就把嵌套层压成了主引号。
    ⇒ 先**用栈配对切分文本角色**（哪段在引号里、嵌套多深），再按目标样式替换
    ——主引号只落在**奇数深度**。
    """
    if not text:
        return text or '', 0, []
    p_open, p_close = primary[0], primary[1]
    s_open, s_close = secondary[0], secondary[1]
    stack: list[str] = []
    out: list[str] = []
    problems: list[str] = []
    n_straight = 0                      # 直引号按出现奇偶交替判开闭
    for ch in text:
        if ch in _QUOTE_OPEN:
            if ch == '"':
                n_straight += 1
                if n_straight % 2 == 1:
                    stack.append('"')
                    out.append(p_open if len(stack) % 2 == 1 else s_open)
                    continue
                if stack and stack[-1] == '"':
                    stack.pop()
                    out.append(p_close if len(stack) % 2 == 0 else s_close)
                else:
                    problems.append('直引号闭无开')
                    out.append(ch)
                continue
            stack.append(ch)
            out.append(p_open if len(stack) % 2 == 1 else s_open)
        elif ch in _QUOTE_CLOSE:
            if not stack:
                problems.append('闭无开')
                out.append(ch)
                continue
            op = stack.pop()
            if _QUOTE_OPEN[op] != ch and not (op == '"' and ch == '"'):
                problems.append(f'配对错位 {op}…{ch}')
            out.append(p_close if len(stack) % 2 == 0 else s_close)
        else:
            out.append(ch)
    if stack:
        problems.append(f'引号未闭合 ×{len(stack)}')
    new = ''.join(out)
    n = sum(1 for a, b in zip(text, new) if a != b) if new != text else 0
    return (text if problems else new), (0 if problems else n), problems


def normalize_paragraphs(text: str | None, sep_chars: float | None) -> tuple[str, bool]:
    """段落分隔归一到本书的实测分隔。返回 `(新文本, 改动与否)`。

    **阈值 `1.5` 只有这一个落点**：`>=1.5` 走空行分段，否则单换行。
    `bands.BAND_SEP` 那张「档位 → 代表值」的翻译表配了 import 期断言钉住它，
    阈值一改当场炸，而不是静默按错的一侧归一（判据 25）。
    """
    lines = [ln.strip() for ln in (text or '').split('\n') if ln.strip()]
    sep = '\n\n' if (sep_chars or 0) >= 1.5 else '\n'
    new = sep.join(lines)
    return new, new != (text or '').strip()


def quote_pair(primary: str | None) -> tuple[str, str] | None:
    """主引号 → `(主, 副)`。副引号取同族的嵌套对（`「」`→`『』`，`“”`→`‘’`）。**唯一落点**。

    两个消费方：stage5 的成稿（基准是全书正文）与 stage6 的文案（基准是原作文案段）。
    **基准不同、配对规则相同**——各写一份映射就会漂（判据 25）。
    """
    return {'「」': ('「」', '『』'), '“”': ('“”', '‘’')}.get(primary or '') \
        or ((primary, primary) if primary else None)


def dominant_quote_style(text: str | None) -> str | None:
    """一段文本里**频次最高**的引号体（都没有则 `None`）。**唯一落点**。

    ⚠ **不取 `measurable.quote_styles[0]`**——那是 `QUOTE_STYLES` 的**表序**，不是频次序。
    """
    from novel.core.text import QUOTE_STYLES
    freq = {name: len(rx.findall(text or '')) for name, rx in QUOTE_STYLES}
    return max(freq, key=lambda k: freq[k]) if any(freq.values()) else None
