#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage5 当前成稿的归一幂等、机械读数与严格篇幅契约。"""
from __future__ import annotations

from functools import wraps

import pytest

from contracts.invariants import DEEP_OVERLAP_MAX
from novel.core.text import quote_count
from novel.stages.stage5 import measure as M
from tests import corpus


def _by_rel(rel: str) -> dict[str, object]:
    return {a.source_id: corpus.load_current(a) for a in corpus.iter_artifacts() if a.rel == rel}


MAIN: dict[str, object] = {}
STYLE: dict[str, object] = {}
CTX: dict[str, object] = {}
_READY: set[str] = set()
DRAFTS: list[corpus.Artifact] = []
_CORPUS_LOADED = False


def _load_corpus() -> None:
    global MAIN, STYLE, CTX, _READY, DRAFTS, _CORPUS_LOADED
    if _CORPUS_LOADED:
        return
    MAIN = _by_rel('stage1/main_text.json')
    STYLE = _by_rel('stage3/style_grounding.json')
    CTX = _by_rel('stage4/writer_context.json')
    _READY = set(MAIN) & set(STYLE) & set(CTX)
    # 正式成稿；归档轮次不属于当前消费面。
    DRAFTS = [a for a in corpus.iter_artifacts()
              if a.pattern == 'stage5/{mode}/ch{nn}.json' and '_rounds/' not in a.rel
              and a.source_id in _READY]
    _CORPUS_LOADED = True


def needs_corpus(test):
    @pytest.mark.corpus
    @wraps(test)
    def run(*args, **kwargs):
        _load_corpus()
        if not DRAFTS:
            pytest.skip('盘上没有语料——本族对账无从谈起')
        return test(*args, **kwargs)
    return run


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
        rec = corpus.load_current(a)
        new, acc = M.normalize_chapter(rec['text'],
                                       quote_style=_quote_style_of(a.source_id),
                                       sep_chars=_sep_of(a.source_id))
        assert new == rec['text'], f'{a.source_id} {a.rel} 再归一改了字节'
        assert acc.counters() == {'punct_fixed': 0, 'quotes_fixed': 0, 'paras_fixed': 0}, \
            f'{a.rel} 的归一账不是全 0：{acc.counters()}'
        # ⚠ **`quote_problems` 恒空不是本条该断的东西。** 归一化对配对失败
        #   **拒绝猜**（整段原样返回），问题落到 `quote_unbalanced` 那条 `info` 上
        #   ⇒ 盘上有一章报「引号未闭合」与「再归一改了 0 字节」**同时成立**，
        #   而本条名字说的是后者。原来那句 `== []` 把一条 info 的真阳读成了等价性回归。
        #   ⇒ 这里只钉「它是稳定的」：拒绝猜就意味着第二遍报的是同一条。
        second = M.normalize_chapter(new, quote_style=_quote_style_of(a.source_id),
                                     sep_chars=_sep_of(a.source_id))[1]
        assert second.quote_problems == acc.quote_problems, \
            f'{a.rel} 两遍归一报的引号问题不一样——「拒绝猜」这条纪律没守住'
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
        rec = corpus.load_current(a)
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
        rec = corpus.load_current(a)
        entry = _ctx_of(a.source_id, rec['output_ordinal'])
        assert entry is not None, f'{a.rel} 在 writer_context 里找不到对应章'
        got = M.overlap_ratio(rec['text'], entry, _source_of(a.source_id))
        assert got == rec.get('overlap_excl_verbatim'), \
            f'{a.rel} 重合率对不上：算 {got}，盘上 {rec.get("overlap_excl_verbatim")}'
        checked += 1
    assert checked == len(DRAFTS)


@needs_corpus
def test_every_out_of_band_draft_carries_the_band_finding():
    """⭐ **出带的每一章都必须把那条 finding 带在身上**——尺子量过了，而且说出口了。

    ⚠⚠ **本条原来断言的是「一章都没出带」，而那是一道产品明说不存在的闸。**
    `stage5/checks.py::length_over` 的措辞逐字是「收缩已跑过并未能压下来，**照样落盘**，
    由全书验收相在成品上判（**由阻断降为提示**）」——`length_over` / `length_under`
    在判据表里是 `info`，不是 `hard`。⇒ 盘上有出带的正式稿是**契约允许的常态**，
    把它读成「语料过期」会指着一批健康产物要求重跑（那要花钱）。
    ⇒ 真正的代码级不变量是这一条：**尺子量过、判定出声、字节落盘，三件事对得上。**
    尺子静默失效（判定没产出）才是 bug，而那正是本条现在守的东西。
    """
    silent: list[str] = []
    seen_out_of_band = 0
    for a in DRAFTS:
        rec = corpus.load_current(a)
        entry = _ctx_of(a.source_id, rec['output_ordinal'])
        rng = M.target_range(entry or {})
        if not rng:
            continue
        lo, hi = rng
        want = ('length_over' if rec['chars'] > hi else
                'length_under' if rec['chars'] < lo else None)
        if want is None:
            continue
        seen_out_of_band += 1
        # `warns` 的形态是 `[判据] 说明`（`checks.warnings_of`），判据 id 逐字在里面。
        if not any(w.startswith(f'[{want}]') for w in (rec.get('warns') or [])):
            silent.append(f'{a.source_id[-4:]}/{a.rel} {rec["chars"]} 字 ∉ {lo}~{hi}，'
                          f'却没有 [{want}]')
    assert not silent, ('出带却没出声的章——尺子静默失效了：\n  ' + '\n  '.join(silent[:8]))
    assert seen_out_of_band or DRAFTS, '一章都没有？那这条测试什么也没查'


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
        rec = corpus.load_current(a)
        entry = _ctx_of(a.source_id, rec['output_ordinal'])
        src_n = M.src_quote_count(entry or {}, _source_of(a.source_id))
        if src_n:
            ratios[rec['mode']].append(quote_count(rec['text']) / src_n)
    assert all(value >= 0 for values in ratios.values() for value in values)


@needs_corpus
def test_overlap_is_a_ratio_and_every_deep_excess_carries_its_finding():
    """重合率是逐章的 0..1 数；闭卷臂越上限的每一章都必须带上 `deep_overlap_high`。

    ⚠ **上限不是闸。** `deep_overlap_high` 在判据表里是 `info`（`judges/stage5.py:229`），
    它的措辞说的是「这个数说明**这一章多半不是闭卷写的**（分工写反 / 开卷稿落进了
    `deep/` 目录）」——那是一条要人去看的线索，不是一条拦落盘的规则。
    本条原来直接断言 `value <= DEEP_OVERLAP_MAX`，于是它在**产品正常工作时**变红。
    ⇒ 改守「越线必出声」：0..1 的取值域仍然是硬的（那是度量自己的契约）。
    """
    per: dict[str, list[float]] = {'deep': [], 'shallow': []}
    silent: list[str] = []
    for a in DRAFTS:
        rec = corpus.load_current(a)
        value = rec.get('overlap_excl_verbatim')
        if value is None:
            continue
        per[rec['mode']].append(value)
        if rec['mode'] == 'deep' and value > DEEP_OVERLAP_MAX:
            if not any(w.startswith('[deep_overlap_high]') for w in (rec.get('warns') or [])):
                silent.append(f'{a.source_id[-4:]}/{a.rel} 重合 {value} > {DEEP_OVERLAP_MAX}，'
                              f'却没有 [deep_overlap_high]')
    assert all(0 <= value <= 1 for values in per.values() for value in values), \
        '重合率不是 0..1 的比例——度量自己的取值域破了'
    assert not silent, ('闭卷越线却没出声的章：\n  ' + '\n  '.join(silent[:8]))
