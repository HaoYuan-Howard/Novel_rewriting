#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""机械测量 → 人读档位词。

原始数字由消费者即时测量；本模块提供纯派生 prompt 档位，不生成、保存或恢复
任何定性文风画像。档位阈值是网文短篇的通用启发式，
**不针对任何一本书**。
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from contracts.invariants import SEP_BANDS
from contracts.schema.measurement import MeasurableBlock, MeasurableBody
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


def _field(value: MeasurableBody | Mapping[str, Any] | object, key: str) -> Any:
    """同一把档位尺同时接即时测量对象与 Stage5 的最小 dict 投影。"""
    return value.get(key) if isinstance(value, Mapping) else getattr(value, key)


def cut_distance(value: float, cuts: list[tuple[int | None, str]]) -> float | None:
    """这个值离**最近的档边**有多远；一条档边都没有时给 `None`。

    ⚠⚠ **它存在的理由是「翻档」与「真漂移」在报告上分不开。** 档位是粗粒度的
    （句子切在 16/22/30、段落切在 20/40/80），基线贴着档边时成稿只降百分之几就翻档，
    而「短句 → 极短句」看起来和一次真正的形态崩塌**一模一样**。
    实测旁证：同一份验收里 `para_len_drift`（比值制，<0.6 或 >1.6 才响）只命中 2/19，
    而 `band_drift` 的段落维命中 10/19 ⇒ 多数是「比值没怎么变但跨过了档边」。
    ⇒ 判据的 `detail` 必须把这个距离说出来，**否则拿现有产物分辨不了这两种解释**。

    ⚠ **只给读数，不做判定**：本函数不回答「多近算贴边」——那个数没有标定过
    （同 `docs/LESSONS.md` 第 16 条与 `TODO-30` 的处置：测量确定、区间没标定 ⇒ 只报不挡）。
    """
    edges = [hi for hi, _ in cuts if hi is not None]
    if not edges:
        return None
    return round(min(abs(value - hi) for hi in edges), 2)


def body_bands(body: MeasurableBody | Mapping[str, Any]) -> dict[str, str]:
    """正文九叶机械投影 → Stage5 使用的五项档位。

    ⚠ 这里只读 ``stage5_style`` 保留的九个叶子；尤其不接 ``measurable.hook``。
    键的顺序与原 ``bands()`` 前五项逐字相同，后者复用本函数，避免抽取侧与写作侧
    各自维护一份阈值或措辞。
    """
    chapter_chars = _field(body, 'chapter_chars')
    per_chapter = _field(body, 'per_chapter_avg_sentence')
    dialogue_ratio = _field(body, 'dialogue_ratio')
    quote_styles = _field(body, 'quote_styles')
    return {
        '句子形态': band(_field(body, 'avg_sentence_chars'), SENT_BANDS),
        '段落形态': band(_field(body, 'avg_paragraph_chars'), PARA_BANDS),
        '对白形态': (
            '无引号对白体（全书不用引号，对白比无法按引号统计）'
            if dialogue_ratio is None
            else band(dialogue_ratio, [
                (0.15, '对白稀少'), (0.3, '对白中等'), (None, '对白密集')])
            + f"（引号体：{'/'.join(quote_styles)}）"),
        '章篇幅': band(_field(chapter_chars, 'avg'), [
            (600, '短章'), (1500, '中等篇幅章'), (None, '长章')]),
        '章间形态差异': (
            '章间句长形态差异明显'
            if _field(per_chapter, 'max') >= 1.5 * max(_field(per_chapter, 'min'), 1)
            else '各章句长形态接近'),
    }


def hook_shape_band(
    hook_meas: MeasurableBlock | Mapping[str, Any] | None,
) -> dict[str, str] | None:
    """文案段自己的【句子 + 段落】档位。给 stage6 下发用。

    它与 ``bands()['文案段形态']`` 分开，是因为 Stage6 需要文案自身的句/段两维，
    而完整展示项还包含相对正文的比较。两者共用 ``SENT_BANDS`` / ``PARA_BANDS``，
    阈值仍只有一个落点。

    实测动机：三个候选文案全被模型写成**一整段**（162/178/185 字/段），
    而本书文案段是 16.4 字/段（一句一段）。`hook_shape` 判据抓到了，
    但 prompt 里只有一句含糊的「多为短句短段」——**判据在查一件 prompt 没说清的事**。
    """
    if not hook_meas:
        return None
    return {'句子形态': band(_field(hook_meas, 'avg_sentence_chars') or 0, SENT_BANDS),
            '段落形态': band(_field(hook_meas, 'avg_paragraph_chars') or 0, PARA_BANDS)}


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

    ⚠ 上游那个 off-by-one **本轮不修**：正文 `paragraph_sep_chars` 仍被 Stage5 消费，
    改分母属于测量契约变更，会让既有 Stage5/6 消费边诚实地判 stale。
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

    生成提示和验收都从原作文案段字节量出同一个量，两边比**形态**；不经过已经
    任何已退役的定性画像（判据 11：验收判据用结构不变量，不要用关键词表）。

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
