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
import copy
import shutil

import pytest
from pydantic import ValidationError

from contracts.invariants import (
    GLOBAL_REVIEW_DISCLOSURE_KINDS, GLOBAL_REVIEW_GENERATION_IMPACT_BY_KIND,
    GLOBAL_REVIEW_GENERATION_IMPACTS, GLOBAL_REVIEW_KINDS, review_effect_of,
    review_generation_impact_of,
)
from contracts.artifacts import (
    STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW,
)
from contracts.schema.evaluation import (
    GlobalReview, GlobalReviewDecision, GlobalReviewGenerationDisposition,
)
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import GlobalGrounding
from novel.errors import HumanEditProtected
from novel.quality.global_review import (
    apply_decision_set, blocking_scope_sha256, build_review, decide,
    generation_safety, review_effect_counts, review_group_key, review_status,
    subject_sha256, UNBOUND_WORKPACK_SHA256,
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


def _quarantine_group(review: GlobalReview, group_key: str) -> GlobalReview:
    disposition = GlobalReviewGenerationDisposition(
        action='quarantine_group', reason='证据不足；从写作消费面移除完整事实组')
    for item in tuple(review.items):
        if (review_effect_of(item.kind) == 'blocking'
                and review_group_key(item.subject_path) == group_key):
            review = decide(
                review, item_id=item.item_id,
                expected_subject_sha256=item.subject_sha256,
                state='UNVER', actor='tester', reason='当前原文不足以确认该事实',
                generation_disposition=disposition,
                now='2026-09-02T00:00:00+00:00')
    return review


# ── ① 分类表 ────────────────────────────────────────────────────────────────

def test_every_kind_is_classified_and_an_unknown_one_is_refused():
    assert set(GLOBAL_REVIEW_DISCLOSURE_KINDS) < set(GLOBAL_REVIEW_KINDS)
    for kind in GLOBAL_REVIEW_KINDS:
        assert review_effect_of(kind) in ('blocking', 'disclosure')
    with pytest.raises(ValueError, match='未登记'):
        review_effect_of('a_brand_new_high_impact_kind')


def test_every_kind_has_an_explicit_generation_impact_and_no_default_route():
    assert set(GLOBAL_REVIEW_GENERATION_IMPACT_BY_KIND) == set(GLOBAL_REVIEW_KINDS)
    assert set(GLOBAL_REVIEW_GENERATION_IMPACT_BY_KIND.values()) <= set(
        GLOBAL_REVIEW_GENERATION_IMPACTS)
    assert review_generation_impact_of('role') == 'must_fix'
    assert review_generation_impact_of('secret') == 'quarantine_or_fix'
    assert review_generation_impact_of('fate') == 'disclose'
    with pytest.raises(ValueError, match='未登记'):
        review_generation_impact_of('new_semantic_guess')


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


def test_dependency_hash_reopens_only_the_changed_subject_card():
    grounding = _global()
    approved = _decide_all(build_review(grounding), set(GLOBAL_REVIEW_KINDS))
    changed = copy.deepcopy(grounding)
    changed['canon']['secrets'][0]['fact'] = '修正后的真相'

    refreshed = build_review(changed, approved)
    secret_rows = [
        item for item in refreshed.items
        if review_group_key(item.subject_path) == 'secret:secret_01'
    ]
    assert secret_rows and all(item.state == 'pending' for item in secret_rows)
    assert next(item for item in refreshed.items
                if item.kind == 'secret_set_complete').state == 'PASS'
    assert next(item for item in refreshed.items
                if item.kind == 'role').state == 'PASS'


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


def test_generation_safety_keeps_unver_but_allows_only_closed_group_quarantine():
    grounding = _global()
    all_pass = _decide_all(
        build_review(grounding), set(GLOBAL_REVIEW_KINDS))
    safe = generation_safety(grounding, all_pass)
    assert safe.safe and safe.all_pass and safe.quarantined_groups == ()

    quarantined = _quarantine_group(all_pass, 'secret:secret_01')
    safe = generation_safety(grounding, quarantined)
    assert safe.safe is True
    assert safe.all_pass is False, 'UNVER 必须继续叫 UNVER，不能洗成 PASS'
    assert safe.quarantined_groups == ('secret:secret_01',)
    assert safe.blocking_item_ids == ()

    undecided = decide(
        all_pass,
        item_id=next(item.item_id for item in all_pass.items
                     if review_group_key(item.subject_path) == 'secret:secret_01'
                     and review_effect_of(item.kind) == 'blocking'),
        expected_subject_sha256=next(
            item.subject_sha256 for item in all_pass.items
            if review_group_key(item.subject_path) == 'secret:secret_01'
            and review_effect_of(item.kind) == 'blocking'),
        state='UNVER', actor='tester', reason='证据不足',
        now='2026-09-02T00:00:00+00:00')
    assert generation_safety(grounding, undecided).safe is False


def test_generation_safety_never_quarantines_core_structure_or_fail():
    grounding = _global()
    all_pass = _decide_all(
        build_review(grounding), set(GLOBAL_REVIEW_KINDS))
    setting = next(
        item for item in all_pass.items
        if item.subject_path == 'canon.setting.时代背景')
    disposition = GlobalReviewGenerationDisposition(
        action='quarantine_group', reason='请求隔离 setting')
    unsafe = decide(
        all_pass, item_id=setting.item_id,
        expected_subject_sha256=setting.subject_sha256,
        state='UNVER', actor='tester', reason='证据不足',
        generation_disposition=disposition,
        now='2026-09-02T00:00:00+00:00')
    assert generation_safety(grounding, unsafe).safe is False

    failed = decide(
        all_pass, item_id=setting.item_id,
        expected_subject_sha256=setting.subject_sha256,
        state='FAIL', actor='tester', reason='与原文冲突',
        now='2026-09-02T00:00:00+00:00')
    assert generation_safety(grounding, failed).safe is False


def test_generation_disposition_is_structurally_unavailable_to_pass_or_fail():
    with pytest.raises(ValidationError, match='只有 UNVER'):
        GlobalReviewDecision(
            item_id='item-1', expected_subject_sha256='a' * 64,
            state='PASS', reason='核验通过', evidence=[],
            generation_disposition={
                'action': 'quarantine_group', 'reason': '非法处置'},
        )


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


def test_a_new_main_text_identity_reopens_only_whole_book_review_decisions():
    """正文别处改字即使抽出同形 Global，也不能继承“全书没有漏项”的旧 PASS。"""
    grounding_a = _global()
    grounding_a['_provenance'] = {'inputs': {
        'stage1/main_text.json': {'file': 'main-a', 'consumed': 'main-a'},
    }}
    approved = _decide_all(build_review(grounding_a), set(GLOBAL_REVIEW_KINDS))
    grounding_b = copy.deepcopy(grounding_a)
    grounding_b['_provenance']['inputs']['stage1/main_text.json'] = {
        'file': 'main-b', 'consumed': 'main-b',
    }

    refreshed = build_review(grounding_b, approved)
    whole_book = {
        'character_set_complete', 'relation_set_complete',
        'world_rule_set_complete', 'secret_set_complete',
        'conflict_partition', 'arc_analysis',
    }

    assert all(item.state == 'pending' for item in refreshed.items
               if item.kind in whole_book)
    assert all(item.state == 'PASS' for item in refreshed.items
               if item.kind not in whole_book)


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


def _bind_scope_to_workpack(template, workpack_sha256: str):
    assert template.blocking_scope is not None
    return template.model_copy(update={
        'blocking_scope': template.blocking_scope.model_copy(update={
            'expected_workpack_sha256': workpack_sha256,
        }),
    })


def test_the_review_template_defaults_to_blocking_and_all_is_opt_in():
    book = _book_with_review()
    ctx = _ctx(book, 'stage2.global', paid=False)
    everything = ArtifactStore(ctx).read(GlobalReview, global_review.REL).items

    default = global_review.review_template(ctx)
    full = global_review.review_template(ctx, scope='all')

    blocking_ids = {item.item_id for item in everything
                    if review_effect_of(item.kind) == 'blocking'}
    assert default.decisions == []
    assert default.blocking_scope is not None
    assert default.blocking_scope.expected_item_count == len(blocking_ids)
    assert default.blocking_scope.expected_blocking_scope_sha256 == \
        blocking_scope_sha256(ArtifactStore(ctx).read(GlobalReview, global_review.REL))
    workpack = global_review.review_workpack(ctx)
    assert default.blocking_scope.expected_workpack_sha256 == \
        UNBOUND_WORKPACK_SHA256
    assert workpack['binding']['reviewed_chapter_ordinals'] == \
        default.blocking_scope.reviewed_chapter_ordinals
    assert {row.item_id for row in full.decisions} == {
        item.item_id for item in everything}
    assert full.blocking_scope is None
    assert len(full.decisions) > len(blocking_ids), '这本书必须真有披露项'


def test_a_scoped_template_never_decides_anything_on_its_own():
    """裁剪的是阅读范围，不是评测面：没进模板的项在盘上仍是 pending。"""
    book = _book_with_review()
    ctx = _ctx(book, 'stage2.global', paid=False)
    global_review.review_template_json(ctx)
    on_disk = ArtifactStore(ctx).read(GlobalReview, global_review.REL)
    assert all(item.state == 'pending' for item in on_disk.items)
    # 模板的完整 scope 是 UNVER —— 原样应用不会开闸。
    template = _bind_scope_to_workpack(
        global_review.review_template(ctx),
        str(global_review.review_workpack(ctx)['workpack_sha256']),
    )
    assert template.blocking_scope is not None
    assert template.blocking_scope.state == 'UNVER'


def test_a_compact_scope_pass_expands_to_every_blocking_item_but_not_disclosure():
    book = _book_with_review()
    ctx = _ctx(book, 'stage2.global', paid=False)
    current = ArtifactStore(ctx).read(GlobalReview, global_review.REL)
    template = _bind_scope_to_workpack(
        global_review.review_template(ctx),
        str(global_review.review_workpack(ctx)['workpack_sha256']),
    )
    assert template.blocking_scope is not None
    supplied = template.model_copy(update={
        'actor': 'scope-reviewer',
        'blocking_scope': template.blocking_scope.model_copy(update={
            'state': 'PASS',
            'reason': '已通读所列全部章节并逐卡核对 blocking workpack',
        }),
    })

    updated = apply_decision_set(
        current, supplied,
        expected_chapter_ordinals=tuple(
            supplied.blocking_scope.reviewed_chapter_ordinals),
        expected_workpack_sha256=supplied.blocking_scope.expected_workpack_sha256,
        now='2026-08-28T00:00:00+00:00',
    )

    assert all(item.state == 'PASS' for item in updated.items
               if review_effect_of(item.kind) == 'blocking')
    assert all(item.state == 'pending' for item in updated.items
               if review_effect_of(item.kind) == 'disclosure')
    assert global_review.is_approved(ctx, updated) is True


def test_a_compact_scope_rejects_a_stale_surface_or_incomplete_chapter_claim():
    book = _book_with_review()
    ctx = _ctx(book, 'stage2.global', paid=False)
    current = ArtifactStore(ctx).read(GlobalReview, global_review.REL)
    template = _bind_scope_to_workpack(
        global_review.review_template(ctx),
        str(global_review.review_workpack(ctx)['workpack_sha256']),
    )
    assert template.blocking_scope is not None
    actual_chapters = tuple(template.blocking_scope.reviewed_chapter_ordinals)

    stale = template.model_copy(update={
        'actor': 'scope-reviewer',
        'blocking_scope': template.blocking_scope.model_copy(update={
            'expected_blocking_scope_sha256': '0' * 64,
        }),
    })
    with pytest.raises(HumanEditProtected, match='surface changed'):
        apply_decision_set(
            current, stale, expected_chapter_ordinals=actual_chapters,
            expected_workpack_sha256=(
                stale.blocking_scope.expected_workpack_sha256))

    incomplete = template.model_copy(update={
        'actor': 'scope-reviewer',
        'blocking_scope': template.blocking_scope.model_copy(update={
            'reviewed_chapter_ordinals': list(actual_chapters[:-1]),
        }),
    })
    with pytest.raises(HumanEditProtected, match='未精确覆盖'):
        apply_decision_set(
            current, incomplete, expected_chapter_ordinals=actual_chapters,
            expected_workpack_sha256=(
                incomplete.blocking_scope.expected_workpack_sha256))
    assert all(item.state == 'pending' for item in current.items)


def test_a_compact_scope_rejects_a_stale_workpack_or_template_placeholders():
    book = _book_with_review()
    ctx = _ctx(book, 'stage2.global', paid=False)
    current = ArtifactStore(ctx).read(GlobalReview, global_review.REL)
    template = global_review.review_template(ctx)
    assert template.blocking_scope is not None
    chapters = tuple(template.blocking_scope.reviewed_chapter_ordinals)

    unbound = template.model_copy(update={
        'actor': 'scope-reviewer',
        'blocking_scope': template.blocking_scope.model_copy(update={
            'state': 'PASS',
            'reason': '已逐项核对当前工作包',
        }),
    })
    with pytest.raises(HumanEditProtected, match='未绑定实际阅读'):
        apply_decision_set(
            current, unbound,
            expected_chapter_ordinals=chapters,
            expected_workpack_sha256=UNBOUND_WORKPACK_SHA256,
        )

    current_workpack_sha256 = str(
        global_review.review_workpack(ctx)['workpack_sha256'])
    template = _bind_scope_to_workpack(template, current_workpack_sha256)

    stale_workpack = template.model_copy(update={
        'actor': 'scope-reviewer',
        'blocking_scope': template.blocking_scope.model_copy(update={
            'state': 'PASS',
            'reason': '已逐项核对当前工作包',
        }),
    })
    with pytest.raises(HumanEditProtected, match='workpack changed'):
        apply_decision_set(
            current, stale_workpack,
            expected_chapter_ordinals=chapters,
            expected_workpack_sha256='f' * 64,
        )

    flip_only = template.model_copy(update={
        'actor': 'scope-reviewer',
        'blocking_scope': template.blocking_scope.model_copy(update={
            'state': 'PASS',
        }),
    })
    with pytest.raises(HumanEditProtected, match='占位理由'):
        apply_decision_set(
            current, flip_only,
            expected_chapter_ordinals=chapters,
            expected_workpack_sha256=(
                flip_only.blocking_scope.expected_workpack_sha256),
        )

    with pytest.raises(HumanEditProtected, match='actor.*占位'):
        apply_decision_set(
            current, template,
            expected_chapter_ordinals=chapters,
            expected_workpack_sha256=(
                template.blocking_scope.expected_workpack_sha256),
        )


def test_a_new_template_cannot_refresh_an_old_workpack_reading_claim(monkeypatch):
    """读 A 后生成 B 模板，不能只改三格就把“读过 B”凭证一起刷新。"""
    book = _book_with_review()
    ctx = _ctx(book, 'stage2.global', paid=False)
    current = ArtifactStore(ctx).read(GlobalReview, global_review.REL)
    workpack_a = global_review.review_workpack(ctx)
    workpack_b = copy.deepcopy(workpack_a)
    workpack_b['main_text_subject_sha256'] = 'b' * 64
    assert isinstance(workpack_b['binding'], dict)
    workpack_b['binding']['main_text_subject_sha256'] = 'b' * 64
    workpack_b.pop('workpack_sha256')
    workpack_b['workpack_sha256'] = subject_sha256(workpack_b)
    assert workpack_a['workpack_sha256'] != workpack_b['workpack_sha256']

    monkeypatch.setattr(
        global_review, 'review_workpack',
        lambda _ctx, *, scope='blocking': copy.deepcopy(workpack_b),
    )
    template_b = global_review.review_template(ctx)
    assert template_b.blocking_scope is not None
    assert template_b.blocking_scope.expected_workpack_sha256 == \
        UNBOUND_WORKPACK_SHA256
    chapters = tuple(template_b.blocking_scope.reviewed_chapter_ordinals)
    edited_three_fields = template_b.model_copy(update={
        'actor': 'scope-reviewer',
        'blocking_scope': template_b.blocking_scope.model_copy(update={
            'state': 'PASS',
            'reason': '已通读并逐项核完当前工作包',
        }),
    })

    with pytest.raises(HumanEditProtected, match='未绑定实际阅读'):
        apply_decision_set(
            current, edited_three_fields,
            expected_chapter_ordinals=chapters,
            expected_workpack_sha256=str(workpack_b['workpack_sha256']),
        )

    bound_to_a = _bind_scope_to_workpack(
        edited_three_fields, str(workpack_a['workpack_sha256']))
    with pytest.raises(HumanEditProtected, match='workpack changed'):
        apply_decision_set(
            current, bound_to_a,
            expected_chapter_ordinals=chapters,
            expected_workpack_sha256=str(workpack_b['workpack_sha256']),
        )

    bound_to_b = _bind_scope_to_workpack(
        edited_three_fields, str(workpack_b['workpack_sha256']))
    updated = apply_decision_set(
        current, bound_to_b,
        expected_chapter_ordinals=chapters,
        expected_workpack_sha256=str(workpack_b['workpack_sha256']),
        now='2026-08-28T00:00:00+00:00',
    )
    assert all(item.state == 'PASS' for item in updated.items
               if review_effect_of(item.kind) == 'blocking')


def test_an_explicit_item_decision_overrides_the_compact_scope_default():
    book = _book_with_review()
    ctx = _ctx(book, 'stage2.global', paid=False)
    current = ArtifactStore(ctx).read(GlobalReview, global_review.REL)
    template = _bind_scope_to_workpack(
        global_review.review_template(ctx),
        str(global_review.review_workpack(ctx)['workpack_sha256']),
    )
    assert template.blocking_scope is not None
    exception = next(
        item for item in current.items
        if review_effect_of(item.kind) == 'blocking')
    supplied = template.model_copy(update={
        'actor': 'scope-reviewer',
        'blocking_scope': template.blocking_scope.model_copy(update={
            'state': 'PASS',
            'reason': '已逐项核完，例外另列',
        }),
        'decisions': [GlobalReviewDecision(
            item_id=exception.item_id,
            expected_subject_sha256=exception.subject_sha256,
            state='FAIL',
            reason='该项与原文不符，需 typed edit',
            evidence=exception.evidence,
        )],
    })

    updated = apply_decision_set(
        current, supplied,
        expected_chapter_ordinals=tuple(
            supplied.blocking_scope.reviewed_chapter_ordinals),
        expected_workpack_sha256=supplied.blocking_scope.expected_workpack_sha256,
        now='2026-08-28T00:00:00+00:00',
    )

    assert next(item for item in updated.items
                if item.item_id == exception.item_id).state == 'FAIL'
    assert all(item.state == 'PASS' for item in updated.items
               if review_effect_of(item.kind) == 'blocking'
               and item.item_id != exception.item_id)


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
    assert default['main_text_subject_sha256'] == \
        default['binding']['main_text_subject_sha256']
    assert len(default['main_text_subject_sha256']) == 64


def test_workpack_v4_preserves_items_and_includes_whole_book_text_once():
    book = _book_with_review()
    ctx = _ctx(book, 'stage2.global', paid=False)
    review = ArtifactStore(ctx).read(GlobalReview, global_review.REL)

    first = global_review.review_workpack(ctx, scope='all')
    second = global_review.review_workpack(ctx, scope='all')
    assert first == second
    assert first['workpack_sha256'] == second['workpack_sha256']
    assert first['kind'] == 'stage2-global-review-workpack-v4'
    main = ArtifactStore(ctx).read(MainText, 'stage1/main_text.json')
    assert first['main_text_chapters'] == [
        {'chapter_ordinal': chapter.chapter_ordinal, 'text': chapter.text}
        for chapter in main.root
    ]
    assert len({row['chapter_ordinal']
                for row in first['main_text_chapters']}) == len(main.root)
    first['legend']['decision'] = '调用方本地改写不应污染下一次生成'
    assert global_review.review_workpack(ctx, scope='all') == second

    rows = first['items']
    assert len(rows) == len(review.items)
    assert {row['item_id'] for row in rows} == {
        item.item_id for item in review.items}
    original_groups = list(dict.fromkeys(
        review_group_key(item.subject_path) for item in review.items))
    has_blocking = {
        key: any(review_group_key(item.subject_path) == key
                 and review_effect_of(item.kind) == 'blocking'
                 for item in review.items)
        for key in original_groups
    }
    expected_groups = [key for key in original_groups if has_blocking[key]]
    expected_groups.extend(
        key for key in original_groups if not has_blocking[key])
    actual_groups = list(dict.fromkeys(str(row['group_key']) for row in rows))
    assert actual_groups == expected_groups
    for key in actual_groups:
        assert [row['item_id'] for row in rows if row['group_key'] == key] == [
            item.item_id for item in review.items
            if review_group_key(item.subject_path) == key
        ], '组内必须保持 GlobalReview.items 原序'
    assert all(row['effect'] == review_effect_of(row['kind']) for row in rows)
    assert all(row['group_key'] == review_group_key(row['subject_path'])
               for row in rows)

    blocking = global_review.review_workpack(ctx)
    blocking_ids = {
        item.item_id for item in review.items
        if review_effect_of(item.kind) == 'blocking'}
    assert {row['item_id'] for row in blocking['items']} == blocking_ids
    for role in (row for row in blocking['items'] if row['kind'] == 'role'):
        companion = role['fate_companion']
        assert companion['read_only'] is True
        assert companion['kind'] == 'fate'
        assert companion['item_id'] not in blocking_ids
        assert review_group_key(companion['subject_path']) == role['group_key']


def test_workpack_group_priority_is_stable_by_first_appearance():
    rows = [
        {'item_id': 'd1', 'group_key': 'disclosure-first', 'effect': 'disclosure'},
        {'item_id': 'b1', 'group_key': 'blocking-second', 'effect': 'blocking'},
        {'item_id': 'd2', 'group_key': 'disclosure-first', 'effect': 'disclosure'},
        {'item_id': 'm1', 'group_key': 'mixed-third', 'effect': 'disclosure'},
        {'item_id': 'm2', 'group_key': 'mixed-third', 'effect': 'blocking'},
    ]

    ordered = global_review._ordered_workpack_rows(rows)

    assert [row['item_id'] for row in ordered] == [
        'b1', 'm1', 'm2', 'd1', 'd2']


def test_the_api_row_carries_the_server_derived_effect_and_group_key():
    from novel.views.global_review import global_review_view

    book = _book_with_review()
    view = global_review_view(str(book.parent), book.name.split('_')[-1])

    assert view.approved is False
    assert view.generation_safe is False
    assert view.quarantined_groups == []
    assert sorted(view.effect_counts) == ['blocking', 'disclosure']
    assert view.effect_counts['disclosure']['pending'] > 0
    for row in view.items:
        assert row.effect == review_effect_of(row.kind)
        assert row.group_key == review_group_key(row.subject_path)
    # 分类表不随 `/api/enums` 出门：那会造出第二个判官（判据 13）。
    from novel.api.enums import payload

    published = payload()
    assert 'global_review_disclosure_kinds' not in published
    assert 'global_review_generation_impacts' not in published
    assert 'global_review_generation_impact_by_kind' not in published
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


def test_quarantined_secret_is_removed_from_generation_but_retained_in_inbox():
    """隔离只改下游内存视图；正式事实、UNVER 与审计卡片都必须留下。"""
    root, book = _sandbox('安全隔离_901')
    try:
        _seed(book)
        stage2_ctx = _ctx(book, 'stage2.global', paid=False)
        store = ArtifactStore(stage2_ctx)
        grounding = store.read_own(
            GlobalGrounding, STAGE2_GLOBAL_GROUNDING)[0]
        payload = grounding.model_dump(mode='json', by_alias=True)
        payload['entities']['relations'][0]['visibility'] = {
            'kind': 'via_secret', 'secret_id': 'secret_01'}
        grounding = GlobalGrounding.model_validate(payload)
        store.write(STAGE2_GLOBAL_GROUNDING, grounding)

        review = _decide_all(
            build_review(payload), set(GLOBAL_REVIEW_KINDS))
        review = _quarantine_group(review, 'secret:secret_01')
        store.write(STAGE2_GLOBAL_REVIEW, review)

        from novel.views.global_review import global_review_view
        public = global_review_view(root, '901')
        assert public.approved is False, 'UNVER 事实不能被 UI/API 洗成 PASS'
        assert public.generation_safe is True
        assert public.quarantined_groups == ['secret:secret_01']

        downstream_ctx = _ctx(book, 'stage4.plan', paid=False)
        generated = global_review.generation_grounding(
            ArtifactStore(downstream_ctx))
        assert generated.canon.secrets == []
        assert generated.entities.relations == [], \
            '引用已隔离 secret 的 via_secret relation 也必须级联移除'
        assert set(downstream_ctx.edges) >= {
            STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW}

        persisted = ArtifactStore(stage2_ctx).read_own(
            GlobalGrounding, STAGE2_GLOBAL_GROUNDING)[0]
        assert [row.secret_id for row in persisted.canon.secrets] == ['secret_01']

        from novel.views.inbox import inbox_view
        inbox = inbox_view(root, '901')
        quarantined_ids = {
            item.item_id for item in review.items
            if item.state == 'UNVER'
            and review_group_key(item.subject_path) == 'secret:secret_01'
        }
        rows = [row for row in inbox.items if row.rule in quarantined_ids]
        assert {row.rule for row in rows} == quarantined_ids
        assert all(row.delivery_effect == 'disclosure' for row in rows)
        assert all(row.attention == 'reviewed_retained' for row in rows)
        assert all(
            row.decision is not None
            and row.decision.state == 'UNVER'
            and row.decision.generation_disposition == 'quarantine_group'
            for row in rows)
    finally:
        shutil.rmtree(root)
