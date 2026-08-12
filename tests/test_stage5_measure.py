#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage5_measure.py —— stage5 的确定性核心，**逐章拿盘上 31 份成稿对账**。

⭐ **本文件最值钱的一条是 `test_normalizing_a_stored_draft_changes_nothing`**：
产物里存的是**归一之后**的字节（`stage5.md` 事实⑤），所以再归一一遍必须**一个字不动**。
它一次钉住三步归一链的全部口径（标点表的顺序、引号栈的深度规则、段落阈值 1.5、
以及「拿谁当基准」那三处选择）——**搬错一个字符类不会报错，只会让某类输入静默地不再归一**。

另外三条同族：`chars` / `overlap_excl_verbatim` 逐值相同，篇幅带与对白比的分布
与 `docs/schema/stage5.md` 记的实测数**逐个相同**。

⚠ 一律走 `corpus.load_raw`：**确定性核心吃 dict 不吃模型**（同 `test_stage4_beats.py`）。
盘上那本 `成年人不做选择` 的 stage4 计划载体拒收，而 `writer_context` 三本都读得回来
——本文件用的正是后者。
"""
from __future__ import annotations

import statistics

import pytest

from novel.core.text import quote_count
from novel.stages.stage5 import measure as M
from tests import corpus


def _by_rel(rel: str) -> dict[str, object]:
    return {a.source_id: corpus.load_raw(a) for a in corpus.iter_artifacts() if a.rel == rel}


MAIN = _by_rel('stage1/main_text.json')
STYLE = _by_rel('stage3/style_grounding.json')
CTX = _by_rel('stage4/writer_context.json')

#: 正式成稿（归档的 `_rounds/chNN.rN.json` 不算——那是上一轮，不是盘上现役的字节）。
DRAFTS = [a for a in corpus.iter_artifacts()
          if a.pattern == 'stage5/{mode}/ch{nn}.json' and '_rounds/' not in a.rel]

needs_corpus = pytest.mark.skipif(not DRAFTS, reason='盘上没有语料——本族对账无从谈起')


def _ctx_of(sid: str, ordi: int) -> dict | None:
    return next((c for c in CTX[sid]['chapters'] if c['output_ordinal'] == ordi), None)


def _source_of(sid: str) -> dict[int, str]:
    return {m['chapter_ordinal']: (m.get('text') or '') for m in MAIN[sid]}


def _quote_style_of(sid: str):
    meas = STYLE[sid].get('measurable') or {}
    return M.target_quote_style(meas, ''.join(_source_of(sid).values()))


def _sep_of(sid: str):
    return ((STYLE[sid].get('measurable') or {}).get('body') or {}).get('paragraph_sep_chars')


# ── ⭐ 一、归一幂等 ────────────────────────────────────────────────────────

@needs_corpus
def test_the_corpus_has_the_three_books_worth_of_drafts():
    """扫描面的守卫：**31 份成稿 / 3 本**。少了就是语料没抽到位，
    而那种情况下下面每一条都会「全部通过」——一条什么都没查的绿灯。"""
    assert len(DRAFTS) == 31, f'盘上应有 31 份正式成稿，这次扫到 {len(DRAFTS)}'
    assert len({a.source_id for a in DRAFTS}) == 3


@needs_corpus
def test_normalizing_a_stored_draft_changes_nothing():
    """⭐⭐ **本层最值钱的一条免费等价测试：31/31 一个字不动。**

    产物里存的 `text` 是**归一之后**的字节（模型原样吐出来的那一份不在任何产物里）
    ⇒ 拿同一条链子再跑一遍，**必须**得到同样的字节、三个计数器全 0。

    它抓的是「逐字移植时错一个字符类、少一个前瞻断言」那种错——那种错不报错，
    只会让某类输入**静默地不再归一**，而落盘闸门与验收相看的是同一份字节，
    两边会一起错成一样，**谁也不会出声**。
    """
    unchanged = 0
    for a in DRAFTS:
        rec = corpus.load_raw(a)
        new, acc = M.normalize_chapter(rec['text'],
                                       quote_style=_quote_style_of(a.source_id),
                                       sep_chars=_sep_of(a.source_id))
        assert new == rec['text'], f'{a.source_id} {a.rel} 再归一改了字节'
        assert acc.counters() == {'punct_fixed': 0, 'quotes_fixed': 0, 'paras_fixed': 0}, \
            f'{a.rel} 的归一账不是全 0：{acc.counters()}'
        assert acc.quote_problems == [], f'{a.rel} 报了引号配对异常：{acc.quote_problems}'
        unchanged += 1
    assert unchanged == 31


@needs_corpus
def test_the_three_counters_add_up_instead_of_overwriting():
    """两遍归一的账**相加不覆盖**。

    ⚠ 链子是「归一 → 收缩 → 再归一 → 校验」，只留后一份会让「首稿修了 9 处引号」
    凭空消失——而那一份账正是「模型这一轮吐出来的东西有多脏」的唯一读数。
    """
    first = M.NormalizeAccount(punct_fixed=9, quotes_fixed=2, paras_fixed=1,
                               quote_problems=['闭无开'])
    second = M.NormalizeAccount(punct_fixed=1, paras_fixed=1)
    total = first + second
    assert total.counters() == {'punct_fixed': 10, 'quotes_fixed': 2, 'paras_fixed': 2}
    assert total.quote_problems == ['闭无开'], '结论也要留着，它是判据的输入'


@needs_corpus
def test_a_book_whose_quotes_were_stripped_gets_no_quote_normalization():
    """⚠ `dialogue_ratio_applicable=False` 的书**不归引号**（返回 `None`，不猜）。

    那几本的引号是**入库前被人工删掉的**（语料缺陷，不是作者选择）⇒ 没有「本书的
    引号体」可言，硬归一等于按一个不存在的基准去改写正文。
    """
    assert M.target_quote_style({'body': {'dialogue_ratio_applicable': False}}, '「甲」') is None
    assert M.target_quote_style({'body': {'dialogue_ratio_applicable': True}},
                                '「甲」「乙」') == ('「」', '『』')
    assert M.target_quote_style(None, '「甲」') is None, '没有实测就没有基准'


# ── 二、读数逐值相同 ──────────────────────────────────────────────────────

@needs_corpus
def test_chars_is_len_of_the_stripped_text():
    """`chars` = `len(text.strip())`，**含空白**。31/31 逐值相同。

    ⚠ 它与 stage3 `measurable.body.chars`（**去空白**）不是同一个口径，
    而本层的预算 `budget_chars` 也是含空白那一支——两边对得上才谈得上篇幅带。
    """
    for a in DRAFTS:
        rec = corpus.load_raw(a)
        assert M.chars_of(rec['text']) == rec['chars'], a.rel


@needs_corpus
def test_overlap_reproduces_every_stored_value():
    """⭐ 20 字窗口重合率（**扣掉被要求逐字复现的部分**）与盘上 **31/31 逐值相同**。

    它同时钉住三样：窗口宽度 20、`norm` 空间、以及**扣除口径**
    ——不扣的话「写手在抄原文」（坏）与「写手正确复现了指定台词」（好）就分不开，
    实测某章重合 19.9% 而 52 个重合窗口里非 `verbatim` 的是 **0** 个。
    """
    checked = 0
    for a in DRAFTS:
        rec = corpus.load_raw(a)
        entry = _ctx_of(a.source_id, rec['output_ordinal'])
        assert entry is not None, f'{a.rel} 在 writer_context 里找不到对应章'
        got = M.overlap_ratio(rec['text'], entry, _source_of(a.source_id))
        assert got == rec.get('overlap_excl_verbatim'), \
            f'{a.rel} 重合率对不上：算 {got}，盘上 {rec.get("overlap_excl_verbatim")}'
        checked += 1
    assert checked == 31


@needs_corpus
def test_the_narrowed_length_band_costs_exactly_ten_chapters():
    """⭐ **收窄篇幅带的代价是可量的，而它就是这个数。**

    原型在 `DRAFT_BAND` 之外又乘了一次 ±10%（实际执行 0.81~1.21×）——
    与 stage4 的 `OP_BAND` 是同一个形状：**写下来的带和执行的带是两个数**。
    实测：31 章里 **10 章（32%）落在 ±10% 之外**，两端极值 **0.814 / 1.206**
    ——恰好卡在旧口径 0.81 / 1.21 的内侧、各差不到 1%，**两条判据一次都没响**。
    """
    out = []
    for a in DRAFTS:
        rec = corpus.load_raw(a)
        entry = _ctx_of(a.source_id, rec['output_ordinal'])
        rng = M.target_range(entry or {})
        if rng and not (rng[0] <= rec['chars'] <= rng[1]):
            out.append(round(rec['chars'] / entry['budget_chars'], 3))
    assert len(out) == 10, f'应有 10 章出带，这次 {len(out)} 章：{sorted(out)}'
    assert (min(out), max(out)) == (0.814, 1.206)


@needs_corpus
def test_no_budget_means_no_band_instead_of_a_guessed_one():
    """没有 `budget_chars` ⇒ 返回 `None`，**不拿猜的数挡人**。"""
    assert M.target_range({'budget_chars': None}) is None
    assert M.target_range({}) is None
    assert M.target_range({'budget_chars': 1000}) == (900, 1100), '带就是 0.90~1.10，没有第二层'
    assert M.book_range(1000) == (900, 1100), '全书与逐章是同一个带，不是两个数'


# ── ⭐ 三、对白比：本项目效应量最大的一条跨书结论 ──────────────────────────

@needs_corpus
def test_the_closed_book_arm_systematically_shortens_dialogue():
    """⭐ **闭卷写手会系统性把台词压成转述**，而开卷不会——盘上逐章重算。

    逐章 `got/src`：**deep 中位 0.80（0.53~1.07）· shallow 中位 1.00（0.62~1.75）**。
    ⚠ 这是**有跨书方差的效应，不是一个系数**：更早那一批 deep 是 0.74/0.67/0.68，
    这一批弱了约一半。别把 0.7× 当常数引用——**那正是 `R-7.8b` 要求这条比值
    无条件报出的理由**（只在漂了的时候才说得出来，就永远看不见这件事）。
    """
    ratios: dict[str, list[float]] = {'deep': [], 'shallow': []}
    for a in DRAFTS:
        rec = corpus.load_raw(a)
        entry = _ctx_of(a.source_id, rec['output_ordinal'])
        src_n = M.src_quote_count(entry or {}, _source_of(a.source_id))
        if src_n:
            ratios[rec['mode']].append(quote_count(rec['text']) / src_n)
    assert (len(ratios['deep']), len(ratios['shallow'])) == (11, 20)
    assert round(statistics.median(ratios['deep']), 2) == 0.80
    assert round(statistics.median(ratios['shallow']), 2) == 1.00
    assert round(min(ratios['deep']), 2) == 0.53 and round(max(ratios['deep']), 2) == 1.07
    assert round(min(ratios['shallow']), 2) == 0.62 and round(max(ratios['shallow']), 2) == 1.75


@needs_corpus
def test_overlap_must_be_read_per_chapter_not_as_a_book_average():
    """⚠ **书级均值会把闭卷的上界平掉**，也会藏起「开卷有低到 0.9% 的章」这件事。

    逐章：deep **0.0%~2.4%**（中位 0.1%）· shallow **0.9%~96.8%**（中位 49.8%）。
    `DEEP_OVERLAP_MAX`（10%）落在两者之间且离两边都远——**它守的是臂的身份，
    不是文风**，所以阈值要按逐章口径选，不是按书级均值。
    """
    per: dict[str, list[float]] = {'deep': [], 'shallow': []}
    for a in DRAFTS:
        rec = corpus.load_raw(a)
        v = rec.get('overlap_excl_verbatim')
        if v is not None:
            per[rec['mode']].append(v)
    assert (round(min(per['deep']), 3), round(max(per['deep']), 3)) == (0.0, 0.024)
    assert (round(min(per['shallow']), 3), round(max(per['shallow']), 3)) == (0.009, 0.968)
    assert round(statistics.median(per['shallow']), 3) == 0.498
