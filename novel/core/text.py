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

_NON_CONTENT = re.compile(r'[^一-鿿A-Za-z0-9]')


def count_non_whitespace_chars(text: str) -> int:
    """正文、Hook 与请求视图的统一字数：标点计入，Unicode 空白不计。"""
    return sum(not char.isspace() for char in text)


def norm(s: str | None) -> str:
    """内容字符归一：去标点/空白/表情，只留中日韩汉字与字母数字。

    用于与原文比对——**LLM 常改写标点**（半角逗号→全角），不归一会漏掉真摘抄。
    """
    return _NON_CONTENT.sub('', s or '')


#: kp 句柄形状（Brief.propositions 登记的 16 位哈希；R10b「锁以内容送达」）。
_KP_HANDLE = re.compile(r'kp_[0-9a-f]{16}')


def kp_statement(statements, key: str) -> str | None:
    """kp 句柄 → 命题 statement（R10b「锁以内容送达」的机械面；D6 渲染修复
    2026-10-03）。

    knowledge/reader 的 set 用 `value` 为 "true"／"false" 表达命题极性，unset
    的值为空；命题内容只在
    句柄；`Brief.propositions` 是唯一登记表（design 验收已按它闭集核对句柄）。
    kp 形状的句柄缺表＝悬空引用，fail fast——渲染面不得以句柄或 "true"
    顶替命题内容（I-34）。非 kp 形状的键返回 None：调用方保持既有行为
    （合成测试面的自述键，value 本就是内容）。放在 text 层供
    `project_context`（正文 IR）与 `hook_context`（楔子）共用同一把尺
    （判据 25：两处各抄一份就会漂）。
    """
    if not _KP_HANDLE.fullmatch(key):
        return None
    statement = dict(statements or {}).get(key)
    if not statement:
        raise ValueError(f'悬空 kp 引用：{key} 不在 Brief.propositions——'
                         '命题内容不可用句柄或 "true" 顶替（渲染/IR 回填 fail fast）')
    return statement


def window_overlap(text: str | None, source: str | None, window: int = 20,
                   exclude: str = '') -> float | None:
    """两段文本的【N 字窗口重合率】，扣除 `exclude` 里允许原样出现的那部分。

    在 `norm` 空间里算（去标点/空白），防标点改写绕过。
    ⚠ `text` 短于一个窗口时返回 **`None`**——那不是「重合 0」，是**量不了**，
    两者必须分得开（判据 7）。

    **这里只有算法，没有口径**：跟谁比、什么算「允许重合」由调用方给。
    现役消费方是 `tools/book_overlap.py` 的成书 vs 源文逐章重合读数。
    """
    src, t, ex = norm(source), norm(text), norm(exclude)
    if len(t) < window or not src:
        return None
    n = len(t) - window + 1
    hits = sum(1 for i in range(n)
               if t[i:i + window] in src and t[i:i + window] not in ex)
    return round(hits / n, 3)


def quote_count(s: str | None) -> int:
    """直接引语的**条数**（四种引号体的成对跨度合计）。

    ⚠ 与 `measure().dialogue_ratio` 不是一回事，两者都要：那一个量的是**占多少字**，
    这一个量的是**说了几句**。台词被压成转述时前者可以纹丝不动
    （叙述里复述一遍同样长），后者掉一半——原型正是靠它抓到「闭卷把台词压成转述」。
    ⚠ 归一之后全书只剩目标引号体，但探针**对归一前后都要成立**（存量稿是归一后的，
    模型刚吐出来那一份不是）⇒ 四种体全表都数。
    """
    return sum(len(rx.findall(s or '')) for _, rx in QUOTE_STYLES)
