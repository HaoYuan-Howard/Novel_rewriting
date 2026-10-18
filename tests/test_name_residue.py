#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""name_residue —— 普通正文的疑似姓名残留定位（rc26，命名前移批 C）。

覆盖面（对应 BACKEND_CONTRACT R-7.9）：

- 旧名残留与未释放目标名都能定位，归因到（码、人物、字面、区间）；
- 长名优先单遍：更长字面先认领，短字面的残余出现才算命中（「沈知遥」里的
  「知遥」不二次计数）；
- 报告只定位：不修改文本、不宣称语义泄漏、不授予业务状态；
- 与写手材料硬闸同源不同用：监视行字面平铺查材料泄漏（`watch_literals`），
  归因行出正文报告——两面判据不互换；
- 无命中返回空；计数只记出现次数，不记零占位。
"""
from __future__ import annotations

from contracts.schema.state import NameWatch
from novel.core.name_residue import (
    name_residues, residue_counts, residue_report, watch_literals,
)


def _watch(*rows: tuple[str, str, tuple[str, ...]]) -> tuple[NameWatch, ...]:
    return tuple(NameWatch(code=code, char_id=char_id, literals=literals)
                 for code, char_id, literals in rows)


def test_old_name_residue_is_located_with_attribution():
    watch = _watch(('old_name_residue', 'lu', ('陆之遥', '陆老爷')))
    residues = name_residues('那人自称陆之遥，转身走了。', watch=watch)
    assert [(r.code, r.char_id, r.literal, r.start, r.end) for r in residues] == [
        ('old_name_residue', 'lu', '陆之遥', 4, 7)]


def test_unreleased_target_name_is_located_separately():
    watch = _watch(
        ('old_name_residue', 'lu', ('陆之遥',)),
        ('unreleased_name', 'shen', ('沈知遥',)),
    )
    text = '沈知遥与陆之遥都在场上。'
    residues = name_residues(text, watch=watch)
    assert [(r.code, r.char_id) for r in residues] == [
        ('unreleased_name', 'shen'), ('old_name_residue', 'lu')]
    # 定位区间与文本字面一致（左闭右开，Python 切片语义）。
    for row in residues:
        assert text[row.start:row.end] == row.literal


def test_longer_literal_claims_the_region_once():
    watch = _watch(('unreleased_name', 'shen', ('沈知遥', '知遥')))
    residues = name_residues('沈知遥来了。', watch=watch)
    assert [r.literal for r in residues] == ['沈知遥']


def test_released_literal_is_not_in_the_watch():
    """已释放字面不进监视行（compile 派生时已排除）；空监视不命中。"""
    assert name_residues('沈知遥来了。', watch=()) == ()
    assert name_residues('沈知遥来了。', watch=_watch()) == ()


def test_ordinary_word_with_shared_shape_is_reported_not_judged():
    """同形普通词也会命中——报告只定位，消息明说不宣称语义泄漏。"""
    watch = _watch(('old_name_residue', 'lu', ('若',)))
    residues = name_residues('倘若如此。', watch=watch)
    assert [r.literal for r in residues] == ['若']
    assert '仅定位' in residues[0].message  # 消息是定位文案，不是判罚


def test_watch_literals_flatten_for_the_material_gate():
    watch = _watch(
        ('old_name_residue', 'lu', ('陆之遥', '陆之远')),
        ('unreleased_name', 'shen', ('沈知遥',)),
    )
    assert watch_literals(watch) == ('沈知遥', '陆之远', '陆之遥')


def test_residue_counts_and_report_shape():
    watch = _watch(
        ('old_name_residue', 'lu', ('陆之遥',)),
        ('unreleased_name', 'shen', ('沈知遥',)),
    )
    residues = name_residues('陆之遥见沈知遥，又见陆之遥。', watch=watch)
    assert residue_counts(residues) == {'old_name_residue': 2, 'unreleased_name': 1}
    report = residue_report(residues, scope='ch03')
    assert report['scope'] == 'ch03'
    assert report['counts'] == {'old_name_residue': 2, 'unreleased_name': 1}
    assert len(report['residues']) == 3
    # 报告不带正文文本（I-38：观测面不复制领域内容）。
    assert all('text' not in row for row in report['residues'])
    assert '不阻断' in report['disclosure']


def test_report_of_empty_residues_keeps_zero_absent():
    report = residue_report((), scope='hook')
    assert report['residues'] == []
    assert report['counts'] == {}
