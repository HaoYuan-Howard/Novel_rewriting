#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
style.py —— 写法密度：破折号与「短段成串」。**确定性，0 成本，只量不判。**

【为什么在 `core` 而不在某一层】它是**只量不判**的纯读数算子，不绑定任何一层的
验收口径；现役消费方是语料标定工具 `tools/calibrate_style_corpus.py`
（下面两个 P95 即由它标定得出）。量子落在 `core`、判定留给各层判据，
标定与验收才不会各拿一把尺子。
"""
from __future__ import annotations

import re

from novel.core.text import norm

#: 破折号：按**串**计——`————` 是一个破折号写长了，不是两个。
#: ⚠ 标点归一**不碰破折号**，所以归一前后同值 ⇒ 这个探针对存量稿与新稿是同一把尺子。
_DASH_RX = re.compile(r'—{2,}')

#: 电报体的两个**定义参数**（什么叫「短」、几段算「成串」），**不是判定阈值**。
#: 判定阈值由语料标定给出（见下面那两个 P95）。
#: 段的口径 = 非空行即一段，与段落归一一致。
SHORT_PARA_MAX = 12
SHORT_RUN_MIN = 3

#: 语料标定的体裁上界（p95）。**它们是读数不是门**——stage7 只把它们并排印出来
#: 让人有个参照；真拿它们当告警线是 stage5 验收相的事（属 F4）。
#: ⚠ 语料是原始全书（含文案），成稿是分章正文，**两者口径不完全同**：
#: 这里要的是「体裁常态」的**数量级**，不是一个可以直接比大小的阈值。
DASH_CORPUS_P95 = 2.25
SHORT_RUN_CORPUS_P95 = 7.68


def paragraphs(body: str | None) -> list[str]:
    """成稿 → 段列表（非空行）。与段落归一**同一口径**。"""
    return [ln.strip() for ln in (body or '').split('\n') if ln.strip()]


def dash_per_1k(body: str | None) -> float:
    """破折号每千**内容**字。分母口径 = `len(core.text.norm())`。"""
    n = len(norm(body)) or 1
    return len(_DASH_RX.findall(body or '')) / n * 1000


def short_run_per_1k(body: str | None) -> float:
    """「连续 ≥`SHORT_RUN_MIN` 个短段」这样的**串**每千内容字出现多少次。

    **计串不计段**：一串 8 个短段与一串 3 个短段都记 1 次「这里转成了电报体」。
    计段会让同一处的长短决定读数，那量的是别的东西。

    ⚠ 它与「全章段均字数」不是一回事：后者看的是均值，而一章里
    「几段极短 + 几段很长」可以让均值纹丝不动，这条专门抓那种**成串**的碎段。
    """
    n = len(norm(body)) or 1
    runs = cur = 0
    for p in paragraphs(body):
        if len(norm(p)) <= SHORT_PARA_MAX:
            cur += 1
        else:
            if cur >= SHORT_RUN_MIN:
                runs += 1
            cur = 0
    if cur >= SHORT_RUN_MIN:
        runs += 1
    return runs / n * 1000
