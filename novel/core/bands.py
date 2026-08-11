#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bands.py —— 实测数字 → **定性档位词**。喂给抽取器的是档位词，不是数值。

【根因 F-013·也是 `R-4.3`】既给「权威数字」又禁止复述数字，是**一对自相冲突的指令**
——模型只能靠复述数字来证明自己遵守了自洽要求。
⇒ 档位词本身就是我们要它写进描述的形态词，数字则只留 `measurable` 一个落点。
⚠ 那一条在需求表里标的是 `结构` 而不是 `HARD`：抽取侧的插值表里**只有档位词键**，
实测数值**不在参数里**——发不出去不是因为有人在查，是因为没有那个变量。

档位阈值是网文短篇的通用启发式，**不针对任何一本书**。
"""
from __future__ import annotations

from contracts.schema.stage3 import Measurable, MeasurableBlock

#: 句子/段落的档位阈值：**唯一落点**。
#:
#: ⚠ `bands()` 与 stage6 的文案 prompt 都按这两张表分档——同一个
#: 「极短段（多为一句一段）」在两处各写一份字面量，就是判据 25 的形状：
#: 改一处、另一处静默说旧话。
#: （stage6 需要的是**文案自己的**段落档位，而 `bands()['段落形态']` 量的是正文，
#:   两者用同一张尺、不同的输入。）
SENT_BANDS: list[tuple[int | None, str]] = [
    (16, '极短句'), (22, '短句'), (30, '中等长度句'), (None, '长句')]
PARA_BANDS: list[tuple[int | None, str]] = [
    (20, '极短段（多为一句一段）'), (40, '短段'), (80, '中等段落'), (None, '长段')]


def band(value: float, cuts: list[tuple[int | None, str]]) -> str:
    """数值 → 档位词。`cuts=[(上界, 档位词)…]`，最后一项上界用 `None` 表示兜底。"""
    return next(word for hi, word in cuts if hi is None or value < hi)


def bands(meas: Measurable) -> dict[str, str]:
    """实测数字 → 发给抽取器的那张档位表。

    ⚠⚠ **这份输出的每一个字都进 prompt**，所以它的措辞是契约的一部分：
    改一个字，全部存量 `style_grounding.json` 的 `_provenance.prompts` 指纹当场对不上、
    被报成 stale——那是一次**假回归**（判据 28）。
    """
    b, h = meas.body, meas.hook
    out = {
        '句子形态': band(b.avg_sentence_chars, SENT_BANDS),
        '段落形态': band(b.avg_paragraph_chars, PARA_BANDS),
        '对白形态': (
            '无引号对白体（全书不用引号，对白比无法按引号统计）'
            if not b.dialogue_ratio_applicable
            else band(b.dialogue_ratio, [(0.15, '对白稀少'), (0.3, '对白中等'), (None, '对白密集')])
            + f"（引号体：{'/'.join(b.quote_styles)}）"),
        '章篇幅': band(b.chapter_chars.avg, [(600, '短章'), (1500, '中等篇幅章'), (None, '长章')]),
        '章间形态差异': (
            '章间句长形态差异明显'
            if b.per_chapter_avg_sentence.max >= 1.5 * max(b.per_chapter_avg_sentence.min, 1)
            else '各章句长形态接近'),
    }
    if h:
        ratio = h.avg_sentence_chars / max(b.avg_sentence_chars, 1)
        out['文案段形态'] = (band(h.avg_sentence_chars, SENT_BANDS) + '；相对正文'
                             + ('更长' if ratio >= 1.15 else '更短' if ratio <= 0.87 else '接近'))
    return out


def hook_shape_band(hook_meas: MeasurableBlock | None) -> dict[str, str] | None:
    """文案段自己的【句子 + 段落】档位。给 stage6 下发用。

    【为什么不并进 `bands()['文案段形态']`】那一项的消费方是 **stage3 自己的 prompt**
    （抽取器收的是 `bands()` 整份）。往里加一项会改动 stage3 的 prompt 文本，
    于是已跑过的 `style_grounding.json` 的 `_provenance.prompts` 指纹当场对不上、
    被报成 stale——**那是一次假回归**（判据 28）。这里另起一个函数，
    `bands()` 的输出逐字节不变，而阈值仍然共用 `SENT_BANDS`/`PARA_BANDS`（判据 25）。

    实测动机：三个候选文案全被模型写成**一整段**（162/178/185 字/段），
    而本书文案段是 16.4 字/段（一句一段）。`hook_shape` 判据抓到了，
    但 prompt 里只有一句含糊的「多为短句短段」——**判据在查一件 prompt 没说清的事**。
    """
    if not hook_meas:
        return None
    return {'句子形态': band(hook_meas.avg_sentence_chars or 0, SENT_BANDS),
            '段落形态': band(hook_meas.avg_paragraph_chars or 0, PARA_BANDS)}
