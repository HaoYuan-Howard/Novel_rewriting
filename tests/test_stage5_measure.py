#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage5 当前成稿的归一幂等、机械读数与严格篇幅契约。"""
from __future__ import annotations

import pytest

from contracts.invariants import DEEP_OVERLAP_MAX
from novel.core.text import quote_count
from novel.stages.stage5 import measure as M
from tests import corpus


def _by_rel(rel: str) -> dict[str, object]:
    return {a.source_id: corpus.load_raw(a) for a in corpus.iter_artifacts() if a.rel == rel}


MAIN = _by_rel('stage1/main_text.json')
STYLE = _by_rel('stage3/style_grounding.json')
CTX = _by_rel('stage4/writer_context.json')

_READY = set(MAIN) & set(STYLE) & set(CTX)
#: 正式成稿；归档轮次不属于当前消费面。
DRAFTS = [a for a in corpus.iter_artifacts()
          if a.pattern == 'stage5/{mode}/ch{nn}.json' and '_rounds/' not in a.rel
          and a.source_id in _READY]

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
def test_the_current_corpus_has_formal_drafts():
    assert DRAFTS
    assert {a.source_id for a in DRAFTS} <= _READY


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
    assert unchanged == len(DRAFTS)


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
    """`chars` = `len(text.strip())`，**含空白**，可逐份复算。

    ⚠ 它与 stage3 `measurable.body.chars`（**去空白**）不是同一个口径，
    而本层的预算 `budget_chars` 也是含空白那一支——两边对得上才谈得上篇幅带。
    """
    for a in DRAFTS:
        rec = corpus.load_raw(a)
        assert M.chars_of(rec['text']) == rec['chars'], a.rel


@needs_corpus
def test_overlap_reproduces_every_stored_value():
    """20 字窗口重合率（扣掉被要求逐字复现的部分）可逐份复算。

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
    assert checked == len(DRAFTS)


@needs_corpus
def test_every_formal_draft_is_inside_the_current_length_band():
    """当前正式稿已经过同一把严格篇幅尺。"""
    out = []
    for a in DRAFTS:
        rec = corpus.load_raw(a)
        entry = _ctx_of(a.source_id, rec['output_ordinal'])
        rng = M.target_range(entry or {})
        if rng and not (rng[0] <= rec['chars'] <= rng[1]):
            out.append(round(rec['chars'] / entry['budget_chars'], 3))
    assert not out, f'当前正式成稿仍有篇幅出带：{sorted(out)}'


@needs_corpus
def test_budget_is_required_instead_of_guessed():
    """当前契约要求正整数预算；缺失、空值或非正数都显式失败。"""
    with pytest.raises(TypeError):
        M.target_range({'budget_chars': None})
    with pytest.raises(KeyError):
        M.target_range({})
    with pytest.raises(ValueError):
        M.target_range({'budget_chars': 0})
    assert M.target_range({'budget_chars': 1000}) == (900, 1100), '带就是 0.90~1.10，没有第二层'
    assert M.book_range(1000) == (900, 1100), '全书与逐章是同一个带，不是两个数'


# ── ⭐ 三、对白比：本项目效应量最大的一条跨书结论 ──────────────────────────

@needs_corpus
def test_dialogue_ratios_are_defined_per_current_mode():
    """对白比逐章计算，模式只允许当前 deep/shallow。"""
    ratios: dict[str, list[float]] = {'deep': [], 'shallow': []}
    for a in DRAFTS:
        rec = corpus.load_raw(a)
        entry = _ctx_of(a.source_id, rec['output_ordinal'])
        src_n = M.src_quote_count(entry or {}, _source_of(a.source_id))
        if src_n:
            ratios[rec['mode']].append(quote_count(rec['text']) / src_n)
    assert all(value >= 0 for values in ratios.values() for value in values)


@needs_corpus
def test_overlap_is_bounded_per_chapter():
    """重合率是逐章的 0..1 数；正式闭卷稿不得越过当前上限。"""
    per: dict[str, list[float]] = {'deep': [], 'shallow': []}
    for a in DRAFTS:
        rec = corpus.load_raw(a)
        v = rec.get('overlap_excl_verbatim')
        if v is not None:
            per[rec['mode']].append(v)
    assert all(0 <= value <= 1 for values in per.values() for value in values)
    assert all(value <= DEEP_OVERLAP_MAX for value in per['deep'])
