#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_canary_chapters —— B6-A canary 选章工具（R5b/W1；只读、零成本）。

【验证边界】机械选章规则（分段、计分、平分、区段为空即拒）用合成计数钉住；
计数函数跑在 fake 链**真实编译**出的 WriterBookIR 上（不是手捏字典——计数器
读的是契约模型，口径漂移必须在这里红）；2 章的真实 IR 报「章数不足」而不猜；
工具入口在没有 IR 时说「没查」并退出码 2，不拿半个盘面当选章依据。
"""
from __future__ import annotations

import sys

import pytest

from tools.canary_chapters import (
    ChapterCount, chapter_counts, select_canary_chapters,
)


def _count(ordinal: int, *, thread_ops=0, cross_chapter_reads=0,
           reader_confirmations=0, thread_closes=0) -> ChapterCount:
    return ChapterCount(chapter_ordinal=ordinal, thread_ops=thread_ops,
                        cross_chapter_reads=cross_chapter_reads,
                        reader_confirmations=reader_confirmations,
                        thread_closes=thread_closes)


def _book(total: int) -> tuple:
    return tuple(_count(n) for n in range(1, total + 1))


# ───────────────────────── 机械选章规则（合成计数） ─────────────────────────

def test_selection_segments_and_scores_for_eleven_chapters():
    """N=11：中段 4..8、后段 9..11；全零平分取各区最小章序，开篇固定第 1 章。"""
    assert select_canary_chapters(_book(11)) == (1, 4, 9)


def test_selection_takes_the_maximum_of_each_segment():
    counts = tuple(
        _count(n, thread_ops=(9 if n == 7 else 1),
               reader_confirmations=(5 if n == 10 else 0))
        for n in range(1, 12))
    assert select_canary_chapters(counts) == (1, 7, 10)


def test_selection_ties_take_the_smaller_ordinal():
    counts = tuple(
        _count(n, thread_ops=(3 if 4 <= n <= 8 else 0),
               reader_confirmations=(2 if 9 <= n <= 11 else 0))
        for n in range(1, 12))
    assert select_canary_chapters(counts) == (1, 4, 9)


def test_selection_refuses_when_a_segment_is_empty():
    """区段为空即报错（不猜、不静默截断）；N=3 是能选满三章的下限。"""
    for total in (1, 2):
        with pytest.raises(ValueError, match='为空'):
            select_canary_chapters(_book(total))
    assert select_canary_chapters(_book(3)) == (1, 2, 3)


# ───────────────────────── 计数函数跑在真实编译的 IR 上 ─────────────────────────

def _real_writer_ir():
    """fake 链真实编译出的 WriterBookIR（2 章设计）。"""
    from tests.test_str06_body import _green_book_ir
    _body, _binding, _plan, writer_ir = _green_book_ir()
    return writer_ir


def test_counts_run_on_the_real_compiled_ir():
    """计数口径读契约模型（ChapterIR/VerificationIR），不是手捏字典。"""
    counts = chapter_counts(_real_writer_ir())
    assert [c.chapter_ordinal for c in counts] == [1, 2]
    for count in counts:
        assert count.thread_ops >= 0 and count.cross_chapter_reads == 0
        assert count.reader_confirmations >= 0 and count.thread_closes == 0


def test_counts_read_the_threads_actually_projected_into_the_ir():
    """线程操作与收束按 IR 投影面计数（与 M8 判据面同一数据面）。"""
    from contracts.schema.context import ThreadOpIR

    writer_ir = _real_writer_ir()
    scene = writer_ir.chapters[1].scenes[0]
    ops = (
        ThreadOpIR(ref='th01', op='open', kind='goal',
                   subject='fu_jun', text='查明旧案'),
        ThreadOpIR(ref='th01', op='close', kind='goal',
                   subject='fu_jun', text='查明旧案', ending='resolved',
                   resolution='真相大白'),
    )
    patched_chapter = writer_ir.chapters[1].model_copy(update={'scenes': (
        scene.model_copy(update={'verification_ir':
                                 scene.verification_ir.model_copy(
                                     update={'thread_ops': ops})}),)})
    patched = writer_ir.model_copy(update={
        'chapters': writer_ir.chapters[:1] + (patched_chapter,)})
    counts = {c.chapter_ordinal: c for c in chapter_counts(patched)}
    assert counts[2].thread_ops == 2 and counts[2].thread_closes == 1


def test_two_chapter_real_ir_refuses_selection():
    """2 章的真实 IR：后段区间为空 ⇒ 报「章数不足」，不猜选章。"""
    counts = chapter_counts(_real_writer_ir())
    with pytest.raises(ValueError, match='为空'):
        select_canary_chapters(counts)


# ───────────────────────── 工具入口：没有 IR 就说「没查」 ─────────────────────────

def test_tool_main_reports_missing_ir_instead_of_guessing(tmp_path, capsys):
    """没有 IR 明说「没查」：退出码 2，不把半个盘面当选章依据。"""
    from tools import canary_chapters

    empty = tmp_path / 'empty'
    empty.mkdir()
    old_argv = sys.argv
    sys.argv = ['canary_chapters.py', '--base', str(tmp_path), '--sid', 'empty']
    try:
        code = canary_chapters.main()
    finally:
        sys.argv = old_argv
    assert code == 2
    assert '没查' in capsys.readouterr().err
