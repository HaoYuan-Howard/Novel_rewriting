#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage2_review_decisions.py —— `stage2.global --review-decisions` 的决策文件路径闸。

【它守的是什么】报错文案承诺「复核决策文件必须在**当前书**或 `_service/edits/<sid>` 内」，
但 `apply_file` 此前只卡第二个根：落在书目录内的绝对路径被误判 `PathRefused`（exit 3），
与文案、与 `paths.edit_rel` 的既有口径（「绝对路径仍然收，只要落在书目录内」）都矛盾。
这里钉死两个根都收、两个根之外都拒。

⚠ 绝对路径分支吃的是**裸决策集**（API `save_global_review_decisions` 的落盘形状），
不带 `_schema_version` 标记；带标记的书内文件请走相对路径（`store.read` 那一支）。
"""
from __future__ import annotations

import json

import pytest

from novel.errors import PathRefused
from novel.stages.stage2 import global_review
from tests.test_stage2_generation import _ctx
from tests.test_stage2_secret_edit import _sandbox, _seed


def _current_items(book):
    raw = json.loads((book / 'stage2/global_review.json').read_text(encoding='utf-8'))
    return raw['payload']['items'] if 'payload' in raw else raw['items']


def _bare_decisions(items) -> str:
    """API 落盘形状：裸 `GlobalReviewDecisionSet`，无 `_schema_version`。"""
    return json.dumps({
        'actor': 'tester',
        'decisions': [{
            'item_id': items[0]['item_id'],
            'expected_subject_sha256': items[0]['subject_sha256'],
            'state': 'PASS',
            'reason': '逐字核对原文后人工 PASS',
            'evidence': [],
        }],
    }, ensure_ascii=False)


def test_a_book_dir_absolute_decision_path_is_accepted():
    _root, book = _sandbox('书_123')
    _seed(book)
    items = _current_items(book)
    path = book / 'stage2/global_review_decisions.json'
    path.write_text(_bare_decisions(items), encoding='utf-8', newline='\n')

    updated = global_review.apply_file(
        _ctx(book, 'stage2.global', paid=False), str(path.resolve()))

    decided = {item.item_id: item.state for item in updated.items}
    assert decided[items[0]['item_id']] == 'PASS'


def test_an_outside_absolute_decision_path_is_still_refused(tmp_path):
    _root, book = _sandbox('书_123')
    _seed(book)
    outside = tmp_path / 'elsewhere_decisions.json'
    outside.write_text(
        _bare_decisions(_current_items(book)), encoding='utf-8', newline='\n')

    with pytest.raises(PathRefused, match='复核决策文件必须在当前书或'):
        global_review.apply_file(
            _ctx(book, 'stage2.global', paid=False), str(outside.resolve()))
