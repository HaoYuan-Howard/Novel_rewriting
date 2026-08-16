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

import re

from contracts.invariants import SEP_BANDS
from contracts.schema.stage3 import Measurable, MeasurableBlock
from novel.core.normalize import normalize_paragraphs as _normalize_paragraphs

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


# ─────────────────────────────────────────────────────────────────────────────
# 段间分隔与收尾形态 —— 两个**离散**性质，都按字节判，不经任何平均值
# ─────────────────────────────────────────────────────────────────────────────

_BLANK_LINE = re.compile(r'\n[ \t]*\n')


def sep_band_of_text(text: str | None) -> str:
    """一段文本的段间分隔档位（`空行分段` / `单换行`），**直接看字节**。

    ⚠⚠ **不走 `measurable.*.paragraph_sep_chars` 那个平均值。** `measure` 把分隔字符
    总数除以**段数**，而分隔符只有「段数 − 1」个，于是**段数越少偏得越低**。
    原型实测七本原作文案**全部**是空行分段（真值一律 2.00），而那个平均值给出
    11 段→1.82 · 10 段→1.80 · 8 段→1.75 · **3 段→1.33**——最后一个掉到 `1.5` 阈值以下，
    于是那本自己的文案段被判成了「单换行」，归一化会照着把它拆散。

    「段间有没有空行」是一个**离散、精确**的性质；把它平均成一个浮点数再卡阈值，
    那一步才是引入 bug 的地方。

    ⚠ 上游那个 off-by-one **本轮不修**：`paragraph_sep_chars` 被 stage3/4/5 一路消费，
    改分母会动到**所有书所有产物的指纹**，远超本轮范围。
    它的触发条件很具体：**段数 ≤ 3 的文本会被判成单换行**。
    正文章节段数都在几十以上所以现在不咬人，而**文案本来就短**——所以本层必须绕开它。
    """
    return '空行分段' if _BLANK_LINE.search((text or '').strip()) else '单换行'


#: 档位 → 喂给 `normalize.normalize_paragraphs` 的代表值。
#: **阈值仍然只有一个落点**（那个函数里的 `1.5`），这里只是把「档位」翻译成它认得的输入。
#: 下面那个 import 期断言把这层翻译钉住：**阈值一改当场炸**，
#: 而不是静默按错的一侧归一（判据 25）。
#: ⚠ 键与 `invariants.SEP_BANDS` 逐值相等，同样由 import 期断言钉着。
BAND_SEP: dict[str, float] = {'空行分段': 2.0, '单换行': 1.0}

_CLOSE_QUOTES = '」”』’"'


def ending_shape(text: str | None) -> str | None:
    """文本最后一行的收尾形态 → `ENDING_SHAPES` 之一（没有内容行时 `None`）。

    这是 `hook_style.end_question`（一条**自由文本**描述，逐书下发给模型）
    **唯一可确定性检测的投影**：不去解析那段自由文本，而是从原作文案段里量出
    同一个量，两边比**形态**（判据 11：验收判据用结构不变量，不要用关键词表）。

    ⚠ **判定顺序是有意的**，`引号台词` 优先于 `问句`：原作里就有既是台词又以问号收尾的
    （`“不然我怎么遇到你就变坏了呢？”`），而「用一句角色台词收尾」与「用叙述者的反问收尾」
    是两种不同的最后一击——前者是原作最常用的那一种，后者是新文案最常落到的那一种。
    原型实测七本原作：问句 1 / 陈述 3 / 引号台词 2 / 省略号 1；五本新文案定稿：问句 3。

    ⚠ 已知边界：`引号台词` **只看行尾是不是收引号**，不要求整行都在引号里
    （`他说：“走。”` 也算）。实测七本原作无假阳；但行内引号恰好收在行尾的情形会被算进来
    ——**这一条是 `info`，别把它当判定**（判据 7）。
    """
    lines = [ln.strip() for ln in (text or '').split('\n') if ln.strip()]
    if not lines:
        return None
    last = lines[-1]
    if last[-1] in _CLOSE_QUOTES:
        return '引号台词'
    if last.endswith(('？', '?')):
        return '问句'
    if last.endswith(('…', '...')):
        return '省略号'
    return '陈述'


# import 期断言：把「档位 → 代表值」这层翻译与 `normalize_paragraphs` 的 1.5 阈值钉在一起。
# 阈值一改就当场炸，而不是静默按错的一侧归一。
assert tuple(BAND_SEP) == SEP_BANDS, 'BAND_SEP 的键与 invariants.SEP_BANDS 分家了'
for _band, _val in BAND_SEP.items():
    assert ('\n\n' in _normalize_paragraphs('甲。\n乙。', _val)[0]) == (_band == '空行分段'), \
        f'BAND_SEP[{_band}]={_val} 与 normalize.normalize_paragraphs 的阈值不再对应'
