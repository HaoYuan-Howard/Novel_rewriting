#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
style.py —— 写法密度：破折号与「短段成串」。**确定性，0 成本，只量不判。**

【为什么在 `core` 而不在某一层】它有**两个**消费方，方向还不同：
  · stage5 的验收相**逐章**判（本书基线 + 体裁上界双线，属 F4）；
  · stage7 的 `rn_style_report` 给**成书**一页总账（正文／文案／原作三列）。
两处各写一份，就是同一把尺子的第二个落点——而这类算子一旦分家，
两边的数字会在报告上并排出现，谁也说不清哪个对。

⚠ **stage7 那一列是全链唯一量到【文案】的地方**：逐章判定够不到它，
因为文案不属于任何一章。
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
