#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_global_review_effects.py —— Stage2 复核项的 blocking / disclosure 分级。

【它守什么】分级的失败方向只有一个，而且很贵：**一类高影响事实被静默放行**。
所以每条守卫都成对写——放宽的那一格要真的放宽，没放宽的那一格要真的没松：

  ① 分类表是白名单 + 关闭式：未登记的 kind 当场炸，不给默认值；
  ② 闸只看 blocking：disclosure 全 pending 也要能开 Stage4；
     blocking 缺一条就必须关着；
  ③ **不阻断 ≠ 不列出**：disclosure 的未决项照旧逐条进 inbox，只是标成 disclosure；
  ④ `current` 仍要求 item/path/hash 集合完全一致——分级只改「谁能拦」，
     不改「谁必须在场」；
  ⑤ 分类与卡片键**只在服务端算**并随行下发，前端拿不到分类表。
"""
from __future__ import annotations

import json

import pytest

from contracts.invariants import (
    GLOBAL_REVIEW_DISCLOSURE_KINDS, GLOBAL_REVIEW_KINDS, review_effect_of,
)
from contracts.schema.evaluation import GlobalReview
from novel.quality.global_review import (
    build_review, decide, review_effect_counts, review_group_key, review_status,
)
from novel.stages.stage2 import global_review
from novel.store import ArtifactStore
from tests.test_quality_evaluator import _global
from tests.test_stage2_generation import _ctx
from tests.test_stage2_secret_edit import _sandbox, _seed


def _decide_all(review: GlobalReview, kinds, state: str = 'PASS') -> GlobalReview:
    for item in tuple(review.items):
        if item.kind not in kinds:
            continue
        review = decide(
            review, item_id=item.item_id,
            expected_subject_sha256=item.subject_sha256,
            state=state, actor='tester', reason='逐项对照原文后裁决',
            now='2026-08-27T00:00:00+00:00')
    return review


# ── ① 分类表 ────────────────────────────────────────────────────────────────

def test_every_kind_is_classified_and_an_unknown_one_is_refused():
    assert set(GLOBAL_REVIEW_DISCLOSURE_KINDS) < set(GLOBAL_REVIEW_KINDS)
    for kind in GLOBAL_REVIEW_KINDS:
        assert review_effect_of(kind) in ('blocking', 'disclosure')
    with pytest.raises(ValueError, match='未登记'):
        review_effect_of('a_brand_new_high_impact_kind')


def test_only_the_three_calibrated_kinds_are_disclosure():
    """名单是冻结决策，不是「看起来差不多」。改它要连同理由一起改。"""
    assert sorted(GLOBAL_REVIEW_DISCLOSURE_KINDS) == [
        'address_context', 'fate', 'name_distinctiveness']
    # legacy `known_by` 只为读存量 v1 保留 ⇒ 保守按 blocking 读，不替历史项开门。
    assert review_effect_of('known_by') == 'blocking'


def test_the_producer_never_emits_a_kind_the_classifier_cannot_place():
    """扫描面从生产枚举派生（判据 3），不在这里手抄第二份清单。"""
    review = build_review(_global())
    assert review.items
    for item in review.items:
        assert review_effect_of(item.kind)
        assert review_group_key(item.subject_path)


def test_a_group_key_has_no_fallback_bucket():
    with pytest.raises(ValueError, match='卡片收敛键'):
        review_group_key('some.unregistered.subject.path')


def test_a_card_collects_every_item_of_the_same_subject():
    review = build_review(_global())
    keys = {item.subject_path: review_group_key(item.subject_path)
            for item in review.items}
    # 同一个人物的 role / fate / 名字 / 称谓进同一张卡。
    assert keys['entities.characters.char_001.role'] == 'character:char_001'
    assert keys['entities.characters.char_001.fate'] == 'character:char_001'
    # 同一条秘密的事实 / 埋点 / 揭示 / 知情事件进同一张卡。
    assert keys['canon.secrets.secret_01.fact'] == 'secret:secret_01'
    assert keys['canon.secrets.secret_01.first_reveal_chapter'] == 'secret:secret_01'
    assert keys['canon.secrets.secret_01.knowledge_events.0'] == 'secret:secret_01'
    # 集合完整性各自成卡，不并进任何主体。
    assert keys['entities.characters'] == 'set:characters'
    assert keys['arc.conflict_stages'] == 'arc'


# ── ② 闸 ────────────────────────────────────────────────────────────────────

def test_disclosure_items_never_hold_the_gate_closed():
    grounding = _global()
    review = _decide_all(build_review(grounding), set(GLOBAL_REVIEW_KINDS)
                         - set(GLOBAL_REVIEW_DISCLOSURE_KINDS))
    _subject, current, approved = review_status(grounding, review)

    assert current and approved, '全部 blocking PASS ⇒ Stage4 可开'
    counts = review_effect_counts(review)
    assert counts['disclosure']['pending'] > 0, 'disclosure 仍然一条没被裁决'
    assert counts['disclosure']['PASS'] == 0, '不许自动 PASS'


def test_one_missing_blocking_decision_still_holds_the_gate_closed():
    grounding = _global()
    every = _decide_all(build_review(grounding), set(GLOBAL_REVIEW_KINDS))
    assert review_status(grounding, every)[2] is True

    blocking = [item for item in every.items
                if review_effect_of(item.kind) == 'blocking']
    reopened = every.model_copy(update={'items': [
        item.model_copy(update={
            'state': 'pending', 'actor': None, 'reason': None, 'decided_at': None})
        if item.item_id == blocking[0].item_id else item
        for item in every.items
    ]})
    assert review_status(grounding, reopened)[2] is False


def test_a_failed_disclosure_item_is_carried_not_swallowed():
    """`FAIL` 的披露项不挡门，但它必须仍然可数、可读、可追责。"""
    grounding = _global()
    review = _decide_all(build_review(grounding), set(GLOBAL_REVIEW_KINDS)
                         - set(GLOBAL_REVIEW_DISCLOSURE_KINDS))
    review = _decide_all(review, set(GLOBAL_REVIEW_DISCLOSURE_KINDS), state='FAIL')

    assert review_status(grounding, review)[2] is True
    counts = review_effect_counts(review)
    assert counts['disclosure']['FAIL'] == len(
        [i for i in review.items if i.kind in GLOBAL_REVIEW_DISCLOSURE_KINDS])
    failed = next(i for i in review.items if i.state == 'FAIL')
    assert failed.actor and failed.reason and failed.decided_at


# ── ④ 评测面完整性一格没松 ──────────────────────────────────────────────────

def test_the_required_surface_still_demands_every_item_including_disclosure():
    grounding = _global()
    full = build_review(grounding)
    trimmed = full.model_copy(update={'items': [
        item for item in full.items
        if review_effect_of(item.kind) == 'blocking'
    ]})
    _subject, current, approved = review_status(grounding, trimmed)
    assert not current, '少一条披露项也不是当前评测面'
    assert not approved, '面不 current 就绝不许开门'


def test_effect_counts_always_emit_every_key_even_at_zero():
    counts = review_effect_counts(build_review(_global()))
    assert sorted(counts) == ['blocking', 'disclosure']
    for states in counts.values():
        assert sorted(states) == ['FAIL', 'PASS', 'UNVER', 'pending']


# ── ③⑤ 只读输出面：CLI scope 与 API row ─────────────────────────────────────

def _book_with_review():
    _root, book = _sandbox('书_456')
    _seed(book)
    return book


def test_the_review_template_defaults_to_blocking_and_all_is_opt_in():
    book = _book_with_review()
    ctx = _ctx(book, 'stage2.global', paid=False)
    everything = ArtifactStore(ctx).read(GlobalReview, global_review.REL).items

    default = global_review.review_template(ctx)
    full = global_review.review_template(ctx, scope='all')

    blocking_ids = {item.item_id for item in everything
                    if review_effect_of(item.kind) == 'blocking'}
    assert {row.item_id for row in default.decisions} == blocking_ids
    assert {row.item_id for row in full.decisions} == {
        item.item_id for item in everything}
    assert len(full.decisions) > len(default.decisions), '这本书必须真有披露项'


def test_a_scoped_template_never_decides_anything_on_its_own():
    """裁剪的是阅读范围，不是评测面：没进模板的项在盘上仍是 pending。"""
    book = _book_with_review()
    ctx = _ctx(book, 'stage2.global', paid=False)
    global_review.review_template_json(ctx)
    on_disk = ArtifactStore(ctx).read(GlobalReview, global_review.REL)
    assert all(item.state == 'pending' for item in on_disk.items)
    # 模板本身也全是 UNVER —— 原样应用不会开闸。
    assert {row.state for row in global_review.review_template(ctx).decisions} == {'UNVER'}


def test_an_unknown_review_scope_is_refused_instead_of_silently_widening():
    from novel.errors import TerminalState

    book = _book_with_review()
    ctx = _ctx(book, 'stage2.global', paid=False)
    with pytest.raises(TerminalState, match='review-scope'):
        global_review.review_template(ctx, scope='blocking_only')


def test_the_workpack_follows_the_same_scope_rule():
    book = _book_with_review()
    ctx = _ctx(book, 'stage2.global', paid=False)
    default = json.loads(global_review.review_workpack_json(ctx))
    full = json.loads(global_review.review_workpack_json(ctx, scope='all'))
    assert len(full['items']) > len(default['items'])
    assert all(review_effect_of(row['kind']) == 'blocking'
               for row in default['items'])


def test_the_api_row_carries_the_server_derived_effect_and_group_key():
    from novel.views.global_review import global_review_view

    book = _book_with_review()
    view = global_review_view(str(book.parent), book.name.split('_')[-1])

    assert sorted(view.effect_counts) == ['blocking', 'disclosure']
    assert view.effect_counts['disclosure']['pending'] > 0
    for row in view.items:
        assert row.effect == review_effect_of(row.kind)
        assert row.group_key == review_group_key(row.subject_path)
    # 分类表不随 `/api/enums` 出门：那会造出第二个判官（判据 13）。
    from novel.api.enums import payload

    published = payload()
    assert 'global_review_disclosure_kinds' not in published
    assert 'global_review_kinds' in published


def test_disclosure_items_stay_in_the_inbox_with_a_disclosure_effect():
    """**不阻断 ≠ 不列出。** 顺手让它们消失，这次分级就成了一次静默删审计。"""
    from novel.views.inbox import inbox_view

    book = _book_with_review()
    view = inbox_view(str(book.parent), book.name.split('_')[-1])
    rows = [row for row in view.items if row.kind == 'verification']
    assert rows, '复核项必须在清单里'

    by_effect = {'blocking': 0, 'disclosure': 0}
    for row in rows:
        by_effect[row.delivery_effect] += 1
    assert by_effect['disclosure'] > 0 and by_effect['blocking'] > 0
    assert all('只披露，不挡 Stage4' in row.what
               for row in rows if row.delivery_effect == 'disclosure')
    # 阻断数只读 delivery_effect，不拿类别总数冒充。
    assert view.delivery_counts['disclosure'] >= by_effect['disclosure']
