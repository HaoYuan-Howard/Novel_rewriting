#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
text.py —— 文本层的正则与归一。**逐字移植自原型，注释一起搬。**

这几条正则每一条都带着一次实测的账，改之前先读注释。
"""
from __future__ import annotations

import re

#: 句界：**必须含【半角 `?!`】**——stage1 原文标点不统一，半角占比高的书
#: （普女 82 处 / 清杳 90 处）若只认全角，句数被系统性少算、均句长被高估
#: （普女 22.7→19.0、清杳 17.2→14.9），而这些数字是喂给抽取器的「权威事实」，
#: 错了会直接把定性描述带偏。
SENT_END = re.compile(r'[。！？!?…]+')

#: 引号体：按**实际成对跨度**判定，不按单字符出现——孤立一个引号不算对白体。
QUOTE_STYLES = (('「」', re.compile(r'「[^」]*」')),
                ('“”', re.compile(r'“[^”]*”')),
                ('『』', re.compile(r'『[^』]*』')),
                ('""', re.compile(r'"[^"]*"')))

#: 取引号内片段（举例用的原文微引用）。上界 40 字是为了不把整段吞进来。
QUOTED = re.compile(r'「([^」]{1,40})」|“([^”]{1,40})”|‘([^’]{1,40})’|'
                    r'『([^』]{1,40})』|"([^"]{1,40})"')

#: 「结尾必须有终止标点」的那套。截断判定与删减守卫共用这一份——
#: 两处各抄一套就会漂（判据 25）。
TERMINAL_PUNCT = '。！？…”」』!?.'

_NON_CONTENT = re.compile(r'[^一-鿿A-Za-z0-9]')


def norm(s: str | None) -> str:
    """内容字符归一：去标点/空白/表情，只留中日韩汉字与字母数字。

    用于与原文比对——**LLM 常改写标点**（半角逗号→全角），不归一会漏掉真摘抄。
    """
    return _NON_CONTENT.sub('', s or '')


def locate_norm(needle: str | None, text: str | None) -> tuple[int, int] | None:
    """在 `text` 里定位一段**归一后**的串 → 原文下标区间 `(start, end)`；找不到返回 `None`。

    ⚠⚠ **这个回映是承重的，不是便利函数。** `norm()` 剥掉标点与空白，所以
    「归一后匹配得上」**不等于**「原样出现过」。凡是要把一条结论的凭据落回
    产物**自己的字节**，就必须走这条回映：

      · 剧透/秘密检测命中的片段来自 `secret.fact`（**不是产物**），
        直接拿它当凭据展示，等于把那条秘密原文抄进报告——**护栏自己成了泄漏源**。
        回映之后引用的是**产物自己写过的那句话**，一个新字节都不多给。
      · 界面要在右栏把凭据高亮出来，也只能用原文下标，**且必须服务端算**：
        JS 是 UTF-16 码元、Python 是码点，出现一个 emoji 之后全部错位。
    """
    k = norm(needle)
    if not k:
        return None
    idx = [i for i, ch in enumerate(text or '') if not _NON_CONTENT.match(ch)]
    p = ''.join((text or '')[i] for i in idx).find(k)
    if p < 0:
        return None
    return idx[p], idx[p + len(k) - 1] + 1


def window_overlap(text: str | None, source: str | None, window: int = 20,
                   exclude: str = '') -> float | None:
    """两段文本的【N 字窗口重合率】，扣除 `exclude` 里允许原样出现的那部分。

    在 `norm` 空间里算（去标点/空白），防标点改写绕过。
    ⚠ `text` 短于一个窗口时返回 **`None`**——那不是「重合 0」，是**量不了**，
    两者必须分得开（判据 7）。

    **这里只有算法，没有口径**：跟谁比、什么算「允许重合」由调用方给。
    两套口径共用它——stage5 的成稿 vs 源章（扣 verbatim）· stage6 的文案 vs
    原作文案段（**无豁免**：文案里一句逐字要求都没有）。
    各自抄一份窗口逻辑就会漂，而漂的表现是其中一处悄悄不再拦（判据 25）。
    """
    src, t, ex = norm(source), norm(text), norm(exclude)
    if len(t) < window or not src:
        return None
    n = len(t) - window + 1
    hits = sum(1 for i in range(n)
               if t[i:i + window] in src and t[i:i + window] not in ex)
    return round(hits / n, 3)


def quotes(s: str | None) -> list[str]:
    """取出字符串里被引号包住的片段（举例用的原文微引用）。"""
    return [g for m in QUOTED.finditer(s or '') for g in m.groups() if g]


def render_book(main_text: object) -> tuple[str, str]:
    """正文列表 → `(按阅读序分块的全书正文, 章节索引)`。**只含正文，绝不含文案段。**

    ⚠ 章的唯一句柄是 `chapter_ordinal`（**修正后的阅读序**）；
    `source_chapter_label` 是原文标号，**参考、不可信**——全语料 28/163 本有重号、
    12/163 本有缺号，所以它只能标成「参考」发下去，不能当句柄。
    ⚠ `section_id` 只留在 stage1，**不进语义层**。

    ⚠⚠ **这两个串逐字进 prompt**，所以它们的措辞是契约的一部分：
    改一个字，全部存量产物的 `_provenance.prompts` 指纹当场对不上、被报成 stale
    ——那是一次**假回归**（判据 28）。
    """
    parts, idx = [], []
    for m in main_text.root:            # type: ignore[attr-defined]
        ordi, lbl = m.chapter_ordinal, m.source_chapter_label
        idx.append(f'阅读序第{ordi}章  原文标号(参考,不可信)={lbl}')
        parts.append(f'===== [阅读序第{ordi}章 | 原文标号={lbl}] =====\n{m.text}')
    return '\n\n'.join(parts), '\n'.join(idx)
