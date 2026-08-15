#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
measure.py —— 本层的上游装载、投影与**纯读数**。**逐字移植自原型，注释一起搬。**

⚠ 这里只放「拿到什么、量出什么」，判定一律在 `checks.py`。
"""
from __future__ import annotations

import re

from contracts.schema.stage5 import JudgedDraft
from contracts.schema.stage6 import Mech
from novel.core.bands import BAND_SEP, sep_band_of_text
from novel.core.normalize import (
    dominant_quote_style, normalize_paragraphs, normalize_punctuation, normalize_quotes,
    quote_pair,
)
from novel.core.text import norm
from novel.stages.stage5.measure import load_drafts
from novel.store import ArtifactStore

#: 引号内片段。⚠ 与 `core.text.QUOTED` **刻意不同**：那一份有 40 字上界
#: （stage3 取的是「举例性质的微引用」），本层要的是**整条台词**，一条 46 字的台词
#: 正是某本书的卖点。上界会把它截掉，于是「最长引语」这个读数系统性偏低。
_QUOTED = re.compile(r'「([^」]+)」|“([^”]+)”|『([^』]+)』|‘([^’]+)’|"([^"]+)"')

#: 短引语在任何一章正文里都必然命中 ＝ **保证会亮的绿灯**，所以不计。
QUOTE_MIN = 6


# ── 下发范围 ────────────────────────────────────────────────────────────────

def lead_upto(arc: dict | None) -> int:
    """文案的下发范围 = **第一个冲突阶段的末章**。返回章号（至少 1）。

    【为什么需要一个范围，而不是只给第 1 章】实测原作文案就跨了好几章
    （某本的文案里「新婚夜」「后半夜采花贼」「夫君求我」分属前几章）。
    只给第 1 章，写出来的文案必然比原作干瘪——而这是**重构**，不是压缩。

    【为什么是它，而不是一个参数】`conflict_stages[0]` 是 stage2 的结构化数据，
    语义正好是「开篇的那一段故事」，逐书自动（实测 `[1]` / `[1,2]`）。
    写成 `--reveal-upto N` 就是又一个需要人逐书拍、而且没有守卫的自由变量。

    ⚠⚠ **只读 `chapters`，绝不读 `stage` 那个名字**——实测最后一个阶段叫
    「子女满堂与安享晚年」「系统揭示与最终选择」，**阶段名本身就是结局**。
    这个函数只是在数章号，它碰不得那一列文字。
    〔这是 `R-9.2` 那一行「谁在查」标的代码落点。〕

    ⚠ 这是**连续开篇正文**的下发范围，不是全书知识上界；结构化故事地图与真相面仍全量下发。
    两件事分开：给得少 ≠ 挡得住，给得多 ≠ 允许说破。
    """
    stages = (arc or {}).get('conflict_stages') or []
    first = (stages[0] or {}) if stages else {}
    chs = [c for c in (first.get('chapters') or []) if isinstance(c, int)]
    return max(chs) if chs else 1


def hidden_secrets(secrets: list[dict], upto: int) -> tuple[list[dict], list[str]]:
    """读者读到第 `upto` 章为止**还没被揭示**的秘密。

    返回 `(还没揭示的 secret 列表, 说不出揭示时点的 secret_id 列表)`。
    口径与逐章闸门一致：**揭示章 ≤ upto 即为已揭示**。

    ⚠ 没有 `reader_reveal_events` 的那些**按未揭示处理并单独报出来**——
    「不知道什么时候揭示」不是「已经揭示了」，倒向安全的一边，**但要出声**。
    """
    hid: list[dict] = []
    no_time: list[str] = []
    for s in secrets or []:
        rev = [e.get('chapter') for e in (s.get('reader_reveal_events') or [])
               if isinstance((e or {}).get('chapter'), int)]
        if not rev:
            no_time.append(s.get('secret_id'))
            hid.append(s)
        elif min(rev) > upto:
            hid.append(s)
    return hid, no_time


# ── 成稿：走 `read_judged`，绝不走会记 `inputs` 的那条 ──────────────────────

def lead_drafts(store: ArtifactStore, upto: int) -> tuple[list[dict], list[int],
                                                          dict[str, JudgedDraft]]:
    """开篇 `upto` 章的成稿。返回 `(lead, missing, judged)`。

    ⚠ **装载走产地那一份**（`novel/stages/stage5/measure.py::load_drafts`）：
    成稿是 stage5 的产物，本层只是下游读者。这里只加本层的**口径**——
    「开篇到第几章」，以及同章双份时取排在前面的那一份（**由判据去说**，
    产地的多义性不该在装载器里被一个隐式的选择抹掉）。
    ⚠⚠ 那份装载器里那条「绝不走 `store.read`」的告诫仍然承重，去那边读。

    ⚠ `.INVALID` 由装载器排除并单列，本层不消费那个计数：文案只看正式稿。
    """
    by_ord, _invalids, judged = load_drafts(store)
    lead: list[dict] = []
    missing: list[int] = []
    for o in range(1, upto + 1):
        recs = by_ord.get(o) or []
        if recs:
            lead.append(recs[0])
        else:
            missing.append(o)
    return lead, missing, judged


# ── 落盘前那次确定性改写 ────────────────────────────────────────────────────

def normalize_hook(text: str, *, src_hook: str | None) -> tuple[str, Mech]:
    """文案的确定性后处理：标点 → 引号体 → 段落分隔。返回 `(新文本, mech)`。

    对位 stage5 的三步链，但**每一步的基准都是原作文案段而不是全书正文**：
    文案与正文原则上可以用不同引号体、不同段落分隔（盘上两处一致，那是巧合不是契约）。
    算法一律复用 `novel/core/normalize.py`，这里只决定「拿谁当基准」。

    【段落分隔这一步曾经整个没做】旧论证是「`normalize_paragraphs` 的基准是
    `measurable.body`」——**那条推理把两个维度混了**：它只改**段与段之间的分隔符**，
    不改段落数量，所以文案「一句一段」这个体裁特征它根本碰不到。
    旧代码看的是错的基准，结论却是「干脆不做」。随后把职责推给 `hook_shape`，
    而 `hook_shape` 量的是 `avg_paragraph_chars`——`measure()` 定义「段落 = 非空行」，
    于是 `\\n\\n` 与 `\\n` 对它**完全不可见**。
    实测代价：原作文案段 sep=2.0、原作正文 2.01、成稿正文 1.98，而**新文案 0.92**
    ——五本无一例外，后果在交付物上可见：`book.txt` 里开篇文案是全书唯一一段密排的。

    ⚠ 基准取**原作文案段的字节**（`sep_band_of_text`），不取 `measurable.hook` 里那个
    平均值——它有 off-by-one，短文案会被判反。

    ⚠ **基准缺失时不猜**：没有原作文案段就没有本书的文案分隔口径，原样留着。
    （今天这条分支按 `R-1.12` 走不到——无文案段的书被挡在 stage1；留着是因为
    `src_hook` 是参数，测试可以构造它。）
    """
    text, punct = normalize_punctuation(text)
    qs = quote_pair(dominant_quote_style(src_hook)) if src_hook else None
    if qs:
        text, quotes_fixed, problems = normalize_quotes(text, *qs)
    else:
        quotes_fixed, problems = 0, []
    if src_hook:
        text, changed = normalize_paragraphs(text, BAND_SEP[sep_band_of_text(src_hook)])
        paras = int(changed)
    else:
        paras = 0
    # ⚠ `problems` **不进 `mech`**：它不是记账，**是一条结论**
    # ——已升成判据 `hook_quote_unbalanced`（`Mech` 里那个 `quote_problems` 已裁撤）。
    # 调用方要拿它去排那条判据，所以这里把它挂在返回值之外由 `quote_problems_of` 重算。
    return text, Mech(punct_fixed=punct, quotes_fixed=quotes_fixed, paras_fixed=paras)


def quote_problems_of(text: str, *, src_hook: str | None) -> list[str]:
    """归一化过程中发现的引号配对问题。`hook_quote_unbalanced` 的输入。

    ⚠ 与 `normalize_hook` 跑的是**同一个** `normalize_quotes`，只是取它的第三个返回值。
    分开算是为了让 `mech` 保持「三个计数器」的形状（`quote_problems` 已从载体裁掉）。
    """
    if not src_hook:
        return []
    qs = quote_pair(dominant_quote_style(src_hook))
    if not qs:
        return []
    return normalize_quotes(normalize_punctuation(text)[0], *qs)[2]


# ── 纯读数 ──────────────────────────────────────────────────────────────────

def hook_quotes(text: str | None) -> list[str]:
    """文案里被引号包住的片段（≥ `QUOTE_MIN` 个内容字符）。"""
    out = []
    for m in _QUOTED.finditer(text or ''):
        seg = next((g for g in m.groups() if g), '')
        if len(norm(seg)) >= QUOTE_MIN:
            out.append(seg)
    return out


def quote_stats(text: str | None) -> tuple[int, int]:
    """`(引语条数, 最长一条的字数)`。口径与 `hook_quotes` 共用。"""
    qs = hook_quotes(text)
    return len(qs), max((len(q) for q in qs), default=0)
