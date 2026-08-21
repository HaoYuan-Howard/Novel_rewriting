#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage2_replay_v2.py —— 拿盘上已付费 v3 蓝图重放 `extraction-ir-v1` 的不变量。

【分工】读数与留档归 `tools/replay_stage2_contract.py`（JSON/MD 报告）；
本文件只断言**付费前必须成立的不变量**：
  · 唯一命中处 hinge source 与旧产物逐字节相同（回收 100% 的零成本预演）；
  · 新分割把旧 span 全部包进去、拍间只吸收空白、章首章尾不漏内容；
  · HARD 差集 ⊆ 白名单（`hinge_ambiguous` 是预期产物，其余新增即失败）；
  · 歧义率 ≤ 2% 且每条都进了候选选择的 pending 账（先验：189 条里 2 条 ≈1.06%）。

⚠ 只对**相对上游当前**的书跑（stale 的 blueprints 可能是拿旧 main_text 产的，
段落表对不上量出来的不是契约差异）；过滤后必须留分母断言。
"""
from __future__ import annotations

import pytest

from tests import corpus

pytestmark = [
    pytest.mark.corpus,
    pytest.mark.skipif(
        corpus.corpus_root() is None,
        reason=f'未设置 {corpus.CORPUS_ENV}；契约重放未运行',
    ),
]

_BP_REL = 'stage2/chapter_blueprints.json'

BOOKS: list[str] = sorted({
    artifact.book_dir for artifact in corpus.iter_artifacts()
    if artifact.rel == _BP_REL
})


def _current_books() -> list[str]:
    return [book for book in BOOKS
            if corpus.is_current(book, _BP_REL, 'stage2/global_grounding.json')]


def test_at_least_one_book_is_replayable():
    """分母断言：过滤后一本都不剩 ⇒ 「没东西可查」不许伪装成「查过、都对」。"""
    assert BOOKS, '语料里没有任何 stage2/chapter_blueprints.json'
    books = _current_books()
    corpus.declared_dependency(
        bool(books),
        needs=f'至少一本 stage2 两份产物相对上游当前的书（候选 {BOOKS}）',
        produced_by='stage2.global + stage2.blueprint 重跑该书',
        cost='付费')


@pytest.mark.parametrize('book', BOOKS)
def test_replaying_the_paid_v3_artifact_through_ir1_holds_the_invariants(book):
    from tools.replay_stage2_contract import replay_book

    corpus.requires_current(
        book, _BP_REL, 'stage2/global_grounding.json',
        produced_by='stage2.global + stage2.blueprint 重跑该书', cost='付费')
    root = corpus.corpus_root()
    assert root is not None
    report = replay_book(root / book)

    assert report['violations'] == [], (
        '重放违约（旧 span 不被包住 / 拍间吸收了非空白 / 回收字节漂了）：\n- '
        + '\n- '.join(report['violations'][:10]))
    assert report['hinge_rewritten'] == 0, \
        '唯一命中处的 source 必须与旧产物逐字节相同'
    assert report['unexpected_new_hard'] == [], (
        f'HARD 差集越出白名单：{report["unexpected_new_hard"]}\n'
        f'→ 要么是 compiler 缺陷（回步骤 2 修），要么把它加进白名单并在报告里晒出量')
    assert set(report['failure_causes']) <= {'短语在本拍区间内多处命中'}, (
        f'重放输入来自已通过的产物，除歧义外不该有任何定位失败：'
        f'{report["failure_causes"]}')
    assert report['ambiguous_rate'] <= 0.02, (
        f'歧义率 {report["ambiguous_rate"]:.2%} 超过 2% 首轮阈值——'
        f'先收紧「不唯一就多带几个字」的 prompt 规则再谈付费')
    assert len(report['hinge_ambiguous_pending']) == report['hinge_ambiguous'], \
        '每条歧义都必须进候选选择的 pending 账（那是字段级修复唯一的稳定输入）'
