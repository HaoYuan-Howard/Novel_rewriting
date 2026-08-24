#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_stage2_review_decisions.py —— `stage2.global --review-decisions` 的决策文件路径闸。

【它守的是什么】报错文案承诺「复核决策文件必须在**当前书**或 `_service/edits/<sid>` 内」，
但 `apply_file` 此前只卡第二个根：落在书目录内的绝对路径被误判 `PathRefused`（exit 3），
与文案、与 `paths.edit_rel` 的既有口径（「绝对路径仍然收，只要落在书目录内」）都矛盾。
这里钉死两个根都收、两个根之外都拒。

绝对路径、书内相对路径与 API 一次性文件都吃注册表版本化形状；路径写法不再改变契约。
"""
from __future__ import annotations

import json

import pytest

from novel.errors import PathRefused
from contracts.artifacts import STAGE2_GLOBAL_REVIEW_DECISIONS
from contracts.schema import encode_artifact
from novel.stages.stage2 import global_review
from tests.test_stage2_generation import _ctx
from tests.test_stage2_secret_edit import _sandbox, _seed


def _current_items(book):
    raw = json.loads((book / 'stage2/global_review.json').read_text(encoding='utf-8'))
    return raw['payload']['items'] if 'payload' in raw else raw['items']


def _versioned_decisions(items) -> str:
    payload = {
        'actor': 'tester',
        'decisions': [{
            'item_id': items[0]['item_id'],
            'expected_subject_sha256': items[0]['subject_sha256'],
            'state': 'PASS',
            'reason': '逐字核对原文后人工 PASS',
            'evidence': [],
        }],
    }
    return json.dumps(
        encode_artifact(STAGE2_GLOBAL_REVIEW_DECISIONS, payload), ensure_ascii=False)


def test_a_book_dir_absolute_decision_path_is_accepted():
    _root, book = _sandbox('书_123')
    _seed(book)
    items = _current_items(book)
    path = book / 'stage2/global_review_decisions.json'
    path.write_text(_versioned_decisions(items), encoding='utf-8', newline='\n')

    updated = global_review.apply_file(
        _ctx(book, 'stage2.global', paid=False), str(path.resolve()))

    decided = {item.item_id: item.state for item in updated.items}
    assert decided[items[0]['item_id']] == 'PASS'


def test_an_outside_absolute_decision_path_is_still_refused(tmp_path):
    _root, book = _sandbox('书_123')
    _seed(book)
    outside = tmp_path / 'elsewhere_decisions.json'
    outside.write_text(
        _versioned_decisions(_current_items(book)), encoding='utf-8', newline='\n')

    with pytest.raises(PathRefused, match='复核决策文件必须在当前书或'):
        global_review.apply_file(
            _ctx(book, 'stage2.global', paid=False), str(outside.resolve()))


def test_a_custom_book_relative_decision_path_is_readable():
    """⭐ **`--emit-review-template` 存到自定义书内相对路径后读不回来。**

    模板是本仓自己生成的、**带版本信封**的（`review_template_json` 调
    `encode_artifact`）。而 `store.read(model, rel)` 按 rel 自己查注册表：
    自定义相对路径未注册 ⇒ 信封不解码 ⇒ `_schema_version` 原样留在 payload 里
    ⇒ StrictModel 报「多了一个字段」。**换个文件名就读不回来**，
    而报错说的是 schema 不对，看不出真正的原因是路径没登记。
    """
    _root, book = _sandbox('书_123')
    _seed(book)
    items = _current_items(book)
    path = book / 'stage2/my_review_decisions.json'
    path.write_text(_versioned_decisions(items), encoding='utf-8', newline='\n')

    updated = global_review.apply_file(
        _ctx(book, 'stage2.global', paid=False), 'stage2/my_review_decisions.json')

    decided = {item.item_id: item.state for item in updated.items}
    assert decided[items[0]['item_id']] == 'PASS'


def test_the_emitted_template_round_trips_through_a_custom_relative_path():
    """产地与读回口用的是同一把尺子：emit 出来的字节原样存下就该能提交。"""
    _root, book = _sandbox('书_123')
    _seed(book)
    ctx = _ctx(book, 'stage2.global', paid=False)
    emitted = global_review.review_template_json(ctx)
    (book / 'stage2/emitted_template.json').write_text(
        emitted, encoding='utf-8', newline='\n')

    # 模板自己就是一份合法（全 pending）的决策集，读得回来即证明信封解得开。
    updated = global_review.apply_file(ctx, 'stage2/emitted_template.json')
    assert updated.items
