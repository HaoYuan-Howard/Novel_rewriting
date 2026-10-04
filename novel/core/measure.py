#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
measure.py —— 一段文本的基础风格读数。**确定性，0 成本。**

【现役落点】正文机械检查由 `novel/stages/stage5/body_checks.py` 在真实消费点
即时计算（口径版本轴由 `BODY_VALIDATOR_VERSION` 承载）；本模块只保留
`measure()` 这把基础读数尺，供 `tools/calibrate_style_corpus.py` 做语料
标定复算。旧链 Stage5/6 投影构建族（`measure_body` / `body_style_metrics` /
`hook_style_metrics`）已随 2026-10-04 清理批删除。

⚠ 本模块的每一个 `round()` 都是契约的一部分。改一处小数位就是一次测量口径改版。
"""
from __future__ import annotations

from contracts.schema.measurement import MeasurableBlock
from novel.core.text import QUOTE_STYLES, SENT_END


def measure(text: str | None) -> MeasurableBlock | None:
    """一段文本的可测风格数字。**空文本返回 `None`**（调用方决定那算不算错）。

    段落 = 非空行；句子按 `。！？…!?` 切；对话比 = 引号跨度字符占比。
    """
    lines = [ln.strip() for ln in (text or '').split('\n') if ln.strip()]
    body = ''.join(lines)
    n_chars = len(body)
    if n_chars == 0:
        return None
    n_paras = len(lines)
    n_sent = max(len([s for s in SENT_END.split(body) if s.strip()]), 1)
    spans = [(name, [m.group() for m in rx.finditer(body)]) for name, rx in QUOTE_STYLES]
    styles = [name for name, ms in spans if ms]
    dialogue_chars = sum(len(x) for _, ms in spans for x in ms)
    return MeasurableBlock(
        chars=n_chars,
        paragraphs=n_paras,
        sentences=n_sent,
        avg_paragraph_chars=round(n_chars / n_paras, 1),
        # 【段落分隔符也要占字数】`chars`/`avg_paragraph_chars` 数的是**段内**字符
        # （`lines` 已 strip、换行不计），而下游的篇幅预算（stage4 的 `source_chars` /
        # `budget_chars`）是 `len(整章文本)`、**含分隔符**。两个口径差 ~14%，
        # 把 budget 直接除以 `avg_paragraph_chars` 求段数就会系统性多要一成半的段落
        # （实测成稿 704 段 vs 原文 691 段，而成稿只有原文的 0.96 长）。
        # 把这一维实测出来，让下游能在同一个口径里换算——**一个语义只留一个落点**。
        paragraph_sep_chars=round((len(text or '') - n_chars) / n_paras, 2),
        avg_sentence_chars=round(n_chars / n_sent, 1),
        # 无引号对白体（大鱼/温意那类）对话比**不可信** → 标 `None`，不进任何一致性判断。
        dialogue_ratio=(round(dialogue_chars / n_chars, 3) if styles else None),
        quote_styles=styles,
    )
