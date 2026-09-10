#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage5 当前成稿的归一幂等、机械读数与严格篇幅契约。"""
from __future__ import annotations

from functools import wraps

import pytest

from contracts.invariants import SOURCE_OVERLAP_MAX
from contracts.schema.stage1 import MainText
from novel.core.measure import body_style_metrics, measure_body
from novel.core.text import norm, quote_count
from contracts.schema.stage5 import Chapter
from novel.stages.stage5 import measure as M, review, trim_resolution
from novel.views.stage5 import stage5_view
from tests import corpus


def _by_rel(rel: str) -> dict[str, object]:
    return {a.source_id: corpus.load_current(a) for a in corpus.iter_artifacts() if a.rel == rel}


MAIN: dict[str, object] = {}
MEASURABLE: dict[str, object] = {}
CTX: dict[str, object] = {}
_READY: set[str] = set()
DRAFTS: list[corpus.Artifact] = []
_CORPUS_LOADED = False


def _load_corpus() -> None:
    global MAIN, MEASURABLE, CTX, _READY, DRAFTS, _CORPUS_LOADED
    if _CORPUS_LOADED:
        return
    MAIN = _by_rel('stage1/main_text.json')
    CTX = _by_rel('stage4/writer_context.json')
    _READY = set(MAIN) & set(CTX)
    MEASURABLE = {
        sid: body_style_metrics(measure_body(MainText.model_validate(main)))
        for sid, main in MAIN.items() if sid in _READY
    }
    # 正式成稿；归档轮次不属于当前消费面。
    DRAFTS = [a for a in corpus.iter_artifacts()
              if a.pattern == 'stage5/ch{nn}.json' and '_rounds/' not in a.rel
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
    return M.target_quote_style(
        MEASURABLE[sid], ''.join(_source_of(sid).values()))


def _sep_of(sid: str):
    return MEASURABLE[sid].get('paragraph_sep_chars')


def _chapter_text(rec: dict) -> str:
    """Current schema has one content authority: ordered ``segments``."""
    return Chapter.model_validate(rec).text


def _read_view(artifact):
    base = corpus.corpus_root()
    assert base is not None
    raw = corpus.load_current(artifact)
    view = stage5_view(base, artifact.source_id, raw['output_ordinal'])
    assert len(view.drafts) == 1, f'{artifact.rel} 没有唯一当前成稿视图'
    return view, view.drafts[0]


def _review_rules(artifact):
    from novel import prov
    from novel.orchestrator.state import readonly_ctx
    from novel.store import ArtifactStore

    base = corpus.corpus_root()
    assert base is not None
    store = ArtifactStore(readonly_ctx(base / artifact.book_dir, 'stage5.review', prov.code_version()))
    current = review.review_status(store.ctx, store)
    corpus.declared_dependency(
        current.state == 'fresh',
        needs=f'{artifact.book_dir} 相对当前正文与判据为 fresh 的 Stage5 Review',
        produced_by='stage5.review', cost='免费')
    assert current.review is not None
    ordinal = corpus.load_current(artifact)['output_ordinal']
    row = next(item for item in current.review.chapters if item.output_ordinal == ordinal)
    rules = {f.rule for f in row.findings}
    return rules, ordinal in trim_resolution.current_trim_audits(store)


# ── ⭐ 一、归一幂等 ────────────────────────────────────────────────────────

@needs_corpus
def test_the_current_corpus_has_formal_drafts():
    assert DRAFTS
    assert {a.source_id for a in DRAFTS} <= _READY


@needs_corpus
def test_normalizing_a_stored_draft_changes_nothing():
    """⭐⭐ **本层最值钱的一条免费等价测试：31/31 一个字不动。**

    产物里存的 `segments[].text` 是**归一之后**的唯一权威字节；整章 `text` 由
    `Chapter.text` 确定性拼出（模型原样吐出来的那一份不在任何产物里）
    ⇒ 拿同一条链子再跑一遍，**必须**得到同样的字节、三个计数器全 0。

    它抓的是「逐字移植时错一个字符类、少一个前瞻断言」那种错——那种错不报错，
    只会让某类输入**静默地不再归一**，而落盘闸门与验收相看的是同一份字节，
    两边会一起错成一样，**谁也不会出声**。
    """
    unchanged = 0
    for a in DRAFTS:
        rec = corpus.load_current(a)
        text = _chapter_text(rec)
        new, acc = M.normalize_chapter(text,
                                       quote_style=_quote_style_of(a.source_id),
                                       sep_chars=_sep_of(a.source_id))
        assert new == text, f'{a.source_id} {a.rel} 再归一改了字节'
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


def test_a_book_whose_quotes_were_stripped_gets_no_quote_normalization():
    """⚠ `dialogue_ratio_applicable=False` 的书**不归引号**（返回 `None`，不猜）。

    那几本的引号是**入库前被人工删掉的**（语料缺陷，不是作者选择）⇒ 没有「本书的
    引号体」可言，硬归一等于按一个不存在的基准去改写正文。
    """
    assert M.target_quote_style({'dialogue_ratio_applicable': False}, '「甲」') is None
    assert M.target_quote_style({'dialogue_ratio_applicable': True},
                                '「甲」「乙」') == ('「」', '『』')
    assert M.target_quote_style(None, '「甲」') is None, '没有实测就没有基准'


def test_minimal_body_baseline_keeps_quote_and_paragraph_normalization_idempotent():
    text = '“先关门。”\n她把灯拨亮。'
    first, account = M.normalize_chapter(
        text, quote_style=('「」', '『』'), sep_chars=2.0)
    assert '「先关门。」' in first and '\n\n' in first
    assert account.quotes_fixed > 0 and account.paras_fixed == 1
    second, again = M.normalize_chapter(
        first, quote_style=('「」', '『』'), sep_chars=2.0)
    assert second == first
    assert again.counters() == {'punct_fixed': 0, 'quotes_fixed': 0, 'paras_fixed': 0}


# ── 二、读数逐值相同 ──────────────────────────────────────────────────────

@needs_corpus
def test_chars_is_len_of_the_stripped_text():
    """实际成稿视图按完整 segments 拼文计长，包含空白，不依赖盘上副本。"""
    for a in DRAFTS:
        rec = corpus.load_current(a)
        _view, draft = _read_view(a)
        assert draft.chars == len(_chapter_text(rec).strip()), a.rel
        assert 'chars' not in rec, a.rel


@needs_corpus
def test_overlap_reaches_the_current_draft_view():
    """当前成稿的窗口重合率进入实际读取视图；来源过期仍保留明确扫描分母。"""
    rels_by_book = {
        book: tuple(a.rel for a in DRAFTS if a.book_dir == book)
        for book in {a.book_dir for a in DRAFTS}
    }
    fresh_books = {
        book for book, rels in rels_by_book.items()
        if rels and corpus.is_current(book, *rels)
    }
    todo = [a for a in DRAFTS if a.book_dir in fresh_books]
    corpus.declared_dependency(
        bool(todo),
        needs=(f'至少一本成稿相对 stage4/writer_context.json 是当前的书'
               f'（{len({a.book_dir for a in DRAFTS})} 本候选里 0 本满足）'),
        produced_by='stage5.write', cost='付费')

    checked = 0
    for a in todo:
        rec = corpus.load_current(a)
        entry = _ctx_of(a.source_id, rec['output_ordinal'])
        assert entry is not None, f'{a.rel} 在 writer_context 里找不到对应章'
        got = M.overlap_ratio(_chapter_text(rec), entry, _source_of(a.source_id))
        _view, draft = _read_view(a)
        assert got == draft.overlap_excl_verbatim, a.rel
        checked += 1
    assert checked == len(todo) and checked > 0, \
        f'扫描分母是 {checked}——这一条没在查'


@needs_corpus
def test_every_out_of_band_draft_has_a_current_review_or_trim_finding():
    """出带仍须在 Review 出声；超长已由当前 trim 接管时只保留一个根因入口。"""
    checked = 0
    for a in DRAFTS:
        rec = corpus.load_current(a)
        entry = _ctx_of(a.source_id, rec['output_ordinal'])
        assert entry is not None, a.rel
        lo, hi = M.target_range(entry)
        size = len(_chapter_text(rec).strip())
        want = 'length_over' if size > hi else 'length_under' if size < lo else None
        rules, has_trim = _review_rules(a)
        assert 'length_report' in rules, f'{a.rel} 当前 Review 缺少篇幅读数'
        if want is not None:
            assert want in rules or (want == 'length_over' and has_trim), (
                f'{a.rel} {size} 字不在 {lo}~{hi}，当前 Review/trim 却没有 {want}')
        checked += 1
    assert checked == len(DRAFTS) and checked > 0


@needs_corpus
def test_budget_is_required_instead_of_guessed():
    """当前契约要求正整数预算；缺失、空值或非正数都显式失败。"""
    with pytest.raises(TypeError):
        M.target_range({'budget_chars': None})
    with pytest.raises(KeyError):
        M.target_range({})
    with pytest.raises(ValueError):
        M.target_range({'budget_chars': 0})
    assert M.target_range({'budget_chars': 1000}) == (800, 1200), '逐章带就是 0.80~1.20，没有第二层'
    assert M.book_range(1000) == (900, 1100), '书级带 0.90~1.10，刻意不跟随逐章带放宽'


# ── ⭐ 三、对白比：本项目效应量最大的一条跨书结论 ──────────────────────────

@needs_corpus
def test_dialogue_ratios_are_defined_per_canonical_chapter():
    """对白比逐章计算；单臂契约不再按 mode 分桶。"""
    ratios: list[float] = []
    for a in DRAFTS:
        rec = corpus.load_current(a)
        entry = _ctx_of(a.source_id, rec['output_ordinal'])
        src_n = M.src_quote_count(entry or {}, _source_of(a.source_id))
        if src_n:
            ratios.append(quote_count(_chapter_text(rec)) / src_n)
    assert all(value >= 0 for value in ratios)


@needs_corpus
def test_overlap_is_a_ratio_and_every_excess_has_a_current_review_finding():
    """重合率从正文现算；超标必须进入当前 Review，不能依赖已退役的 warns。"""
    checked = 0
    for a in DRAFTS:
        _view, draft = _read_view(a)
        value = draft.overlap_excl_verbatim
        if value is None:
            assert (len(norm(_chapter_text(corpus.load_current(a)))) < 20
                    or not norm(_view.source_text)), (
                f'{a.rel} 长正文的重合率未知，不能当作已检查')
        else:
            assert 0 <= value <= 1
            rules, _has_trim = _review_rules(a)
            if value > SOURCE_OVERLAP_MAX:
                assert 'source_overlap_high' in rules, a.rel
        checked += 1
    assert checked == len(DRAFTS) and checked > 0
