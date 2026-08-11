#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
measure.py —— 机械层：一段文本的可测风格数字。**确定性，0 成本。**

【`R-4.1`：形态数字由代码算，不是 LLM 产】这些数字随后被 `bands()` 折成**档位词**
发给抽取器——**实测数字一个都不下发**（`R-4.3`）。理由见 `bands.py`。

⚠ 本模块的每一个 `round()` 都是契约的一部分：`--remeasure` 拿「照盘上 stage1 重算」
与产物里存的逐项比，两边走同一个 `round()` 所以**直接判等，不需要容差**。
改一处小数位就是一次契约改版，会让全部存量产物报 `measurable_drift`。
"""
from __future__ import annotations

from contracts.schema.stage1 import MainText
from contracts.schema.stage3 import ChapterCharStats, MeasurableBlock, MeasurableBody, PerChapterSentence
from novel.core.text import QUOTE_STYLES, SENT_END
from novel.errors import ArtifactUnreadable


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
        dialogue_ratio_applicable=bool(styles),
        quote_styles=styles,
    )


def measure_body(main_text: MainText) -> MeasurableBody:
    """全书正文聚合 + 章长分布 + 逐章句长跨度（`mode_variation` 的证据）。

    **逐章明细不落盘**（无消费方）——只有 min/max/avg 三个汇总进产物。
    """
    sections = list(main_text.root)
    agg = measure('\n'.join(s.text for s in sections))
    if agg is None:
        # ⚠ **不用 `sys.exit`**：`SystemExit` 是 `BaseException`，穿透 `except Exception`
        # （原型正因如此才不得不把每个节点跑成子进程）。
        # ⚠ 归到 `ArtifactUnreadable` 而不是 `UpstreamMissing`：那份上游**在盘上、也合 schema**
        # （`MainText` 只要求 ≥2 章，没要求每章正文非空），只是它说不出这本书写了什么
        # ——「产物坏了，去看它」才是对的动作。盘上零样本，它纯属防御。
        raise ArtifactUnreadable(
            'stage1/main_text.json 的正文合起来是空的，无法实测风格数字。'
            '⇒ 这不是「stage1 还没跑」（那份产物在，而且合 schema），是那份产物本身坏了')
    per = [m for m in (measure(s.text) for s in sections) if m is not None]
    chars = [p.chars for p in per]
    sents = [p.avg_sentence_chars for p in per]
    return MeasurableBody(
        **agg.model_dump(),
        chapter_chars=ChapterCharStats(min=min(chars), max=max(chars),
                                       avg=round(sum(chars) / len(chars), 1)),
        per_chapter_avg_sentence=PerChapterSentence(min=min(sents), max=max(sents)),
    )


def flatten(block: MeasurableBlock | MeasurableBody | None) -> dict[str, object]:
    """一个 scope 摊平成 `{键: 值}`，好逐项比。

    ⚠ `quote_styles` 是 list，**转成 tuple 才比得稳**（list 不可 hash、且两次读盘
    可能给出不同的对象）。
    """
    out: dict[str, object] = {}

    def walk(d: dict, prefix: str = '') -> None:
        for k, v in (d or {}).items():
            if isinstance(v, dict):
                walk(v, f'{prefix}{k}.')
            else:
                out[prefix + k] = tuple(v) if isinstance(v, list) else v

    walk(block.model_dump() if block is not None else {})
    return out


def drift(stored: MeasurableBlock | MeasurableBody | None,
          now: MeasurableBlock | MeasurableBody | None) -> list[tuple[str, object, object]]:
    """`(产物里存的, 照现在的 stage1 重算的)` → `[(键, 存的, 现算的)]`。

    ⚠ 两边摊平后取**并集**比：**少一个键也算漂移**——老产物缺 `paragraph_sep_chars`
    正是 F-054 那一例（下游拿 budget 直接除以 `avg_paragraph_chars`，段落目标系统性高 14%）。
    两边的数都走同一个 `round()`，所以直接判等即可，**不需要容差**。
    """
    a, b = flatten(stored), flatten(now)
    missing = '（缺）'
    return [(k, a.get(k, missing), b.get(k, missing))
            for k in sorted(set(a) | set(b)) if a.get(k, missing) != b.get(k, missing)]
