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

_NON_CONTENT = re.compile(r'[^一-鿿A-Za-z0-9]')


def norm(s: str | None) -> str:
    """内容字符归一：去标点/空白/表情，只留中日韩汉字与字母数字。

    用于与原文比对——**LLM 常改写标点**（半角逗号→全角），不归一会漏掉真摘抄。
    """
    return _NON_CONTENT.sub('', s or '')


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
