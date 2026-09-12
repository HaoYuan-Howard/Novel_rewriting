#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 Core 当前语义全部阻断；隔离不洗白 UNVER、不吞审计。

保留集合完整性、逐项覆盖、原文身份、工作包绑定、HTTP 展示和下游内存隔离反例。
旧 fate/address_context/knowledge_event 披露层已退役，身份、关系、规则均必须明确接受。
"""
from __future__ import annotations

from novel.stages.stage2 import grounding as grounding_io

import json
import copy

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
from novel.quality import global_review as review_domain
from novel.quality.global_review import (
    apply_decision_set, blocking_scope_sha256, decide,
    review_effect_counts, review_group_key,
    subject_sha256, UNBOUND_WORKPACK_SHA256,
)
from novel.stages.stage2 import global_review
from novel.store import ArtifactStore
from tests.test_stage2_core_review import core_pair
from contracts.schema.characters import CharacterRegistry
from tests.test_stage2_generation import _ctx
from tests.test_stage2_global_core_edits import seed



def _characters():
    registry, _ = core_pair()
    registry['characters']['char_002'] = copy.deepcopy(registry['characters']['char_001'])
    registry['characters']['char_002']['canonical'] = '乙'
    registry['characters']['char_002']['aliases'] = [dict(
        registry['characters']['char_001']['aliases'][0], surface='乙')]
    return registry


def _global():
    _registry, core = core_pair()
    secret = core['arc_secrets'][0]
    secret.update(fact='隐瞒的身世', fact_ref=None,
                  evidence_refs=list(core['relations'][0]['evidence_refs']))
    core['world_rules'] = [{'rule_id': 'wr_01', 'kind': 'social', 'versions': [{
        'version_id': 'v01', 'desc': '本地家族的继承规矩', 'effective_from': 1,
        'effective_until': None, 'revealed_from': 1,
        'evidence_refs': list(core['relations'][0]['evidence_refs'])}]}]
    return core


def build_review(core, previous=None, *, characters=None):
    """纯判据样例显式提供固定独立人物登记，盘面用例仍读取真实 registry。"""
    return review_domain.build_review(core, previous, characters=_characters() if characters is None else characters)


def review_status(core, review):
    return review_domain.review_status(core, review, characters=_characters())


def generation_safety(core, review):
    return review_domain.generation_safety(core, review, characters=_characters())


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
    assert review_generation_impact_of('alias_identity') == 'must_fix'
    assert review_generation_impact_of('relation') == 'must_fix'
    assert review_generation_impact_of('world_rule_version') == 'must_fix'
    with pytest.raises(ValueError, match='未登记'):
        review_generation_impact_of('new_semantic_guess')


def test_all_current_core_kinds_block_and_retired_kinds_are_refused():
    assert GLOBAL_REVIEW_DISCLOSURE_KINDS == ()
    assert all(review_effect_of(kind) == 'blocking' for kind in GLOBAL_REVIEW_KINDS)
    for retired in ('fate', 'address_context', 'knowledge_event', 'known_by'):
        with pytest.raises(ValueError, match='未登记'):
            review_effect_of(retired)


def test_the_producer_never_emits_a_kind_the_classifier_cannot_place():
    """扫描面从生产枚举派生（判据 3），不在这里手抄第二份清单。"""
    review = build_review(_global())
    assert review.items
    for item in review.items:
        assert review_effect_of(item.kind)
        assert review_group_key(item.subject_path)


def test_a_group_key_has_no_fallback_bucket():
    with pytest.raises(ValueError, match='未登记'):
        review_group_key('some.unregistered.subject.path')


def test_a_card_collects_every_item_of_the_same_subject():
    review = build_review(_global())
    keys = {item.subject_path: review_group_key(item.subject_path) for item in review.items}
    assert keys['characters.characters.char_001.role'] == 'character:char_001'
    assert keys['characters.characters.char_001.name_distinctiveness'] == 'character:char_001'
    assert keys['global.arc_secrets.secret_01'] == 'secret:secret_01'
    assert keys['global.arc_secrets.secret_01.revealed_from'] == 'secret:secret_01'
    assert keys['global.arc_secrets.secret_01.setup_chapters'] == 'secret:secret_01'
    assert keys['characters.characters'] == 'set:characters'
    assert keys['global.setting.era'] == 'setting'


def test_dependency_hash_reopens_only_the_changed_subject_card():
    grounding = _global()
    approved = _decide_all(build_review(grounding), set(GLOBAL_REVIEW_KINDS))
    changed = copy.deepcopy(grounding)
    changed['arc_secrets'][0]['fact'] = '修正后的真相'

    refreshed = build_review(changed, approved)
    secret_rows = [
        item for item in refreshed.items
        if review_group_key(item.subject_path) == 'secret:secret_01'
    ]
    assert secret_rows and all(item.state == 'pending' for item in secret_rows)
    assert next(item for item in refreshed.items
                if item.kind == 'secret_set_complete').state == 'pending'
    assert next(item for item in refreshed.items
                if item.kind == 'role').state == 'PASS'


@pytest.mark.parametrize('change', ['predicate', 'evidence_locator'])
def test_relation_completeness_tracks_business_fields_but_not_evidence_location(change):
    """同一 ID 的业务修改重开完整性；合法证据订正只重开引用该证据的判断。"""
    from novel.stages.stage2.core_evidence import (
        compile_manual_evidence_refs, validate_evidence_anchors,
    )

    grounding = _global()
    main_text = [{'chapter_ordinal': 1, 'text': '甲是邻居。甲乙同窗。甲乙曾一同求学。'}]
    assert not validate_evidence_anchors(
        grounding, grounding['evidence_anchors'], main_text)
    approved = _decide_all(build_review(grounding), set(GLOBAL_REVIEW_KINDS))
    changed = copy.deepcopy(grounding)
    if change == 'predicate':
        changed['relations'][0]['predicate'] = '旧友'
    else:
        changed['relations'][0]['evidence_refs'] = [
            {'chapter_ordinal': 1, 'locator': '甲乙曾一同求学。'}]
        changed, anchors = compile_manual_evidence_refs(
            changed, changed['evidence_anchors'], main_text)
        changed['evidence_anchors'] = anchors
        assert changed['relations'][0]['evidence_refs'] != grounding['relations'][0]['evidence_refs']
    assert not validate_evidence_anchors(changed, changed['evidence_anchors'], main_text)
    assert [row['relation_id'] for row in changed['relations']] == [
        row['relation_id'] for row in grounding['relations']]
    assert (review_domain.without_review_evidence(changed['relations']) ==
            review_domain.without_review_evidence(grounding['relations'])) == (
                change == 'evidence_locator')

    refreshed = build_review(changed, approved)
    old_relation = next(row for row in approved.items if row.kind == 'relation')
    new_relation = next(row for row in refreshed.items if row.kind == 'relation')
    assert new_relation.subject_path == old_relation.subject_path
    assert new_relation.item_id != old_relation.item_id
    assert new_relation.subject_sha256 != old_relation.subject_sha256
    assert new_relation.state == 'pending'
    old_complete = next(row for row in approved.items if row.kind == 'relation_set_complete')
    new_complete = next(row for row in refreshed.items if row.kind == 'relation_set_complete')
    assert new_complete.subject_path == old_complete.subject_path
    if change == 'predicate':
        assert new_complete.item_id != old_complete.item_id
        assert new_complete.subject_sha256 != old_complete.subject_sha256
        assert new_complete.state == 'pending'
    else:
        assert new_complete == old_complete
        assert new_complete.state == 'PASS'


@pytest.mark.parametrize('negative_state', ['FAIL', 'UNVER'])
def test_free_evidence_reaudit_restores_generation_consumed_but_new_denial_blocks(
        tmp_path, negative_state):
    """真实编辑和决定落盘后比较 Store 的 consumed，不把旧证据审批沿用到新主体。"""
    from contracts.artifacts import STAGE2_CHARACTERS
    from contracts.schema import encode_artifact
    from novel.consumption import stage4_grounding
    from novel.errors import ApprovalRequired
    from novel.stages.stage2 import extract
    from tests.test_stage2_global_core_edits import write_edit
    from tests.test_stage2_global_agent import _free_ctx

    ctx, original = seed(tmp_path)
    ledger = ctx.ledger.path.read_bytes()
    characters_bytes = (ctx.novel_dir / STAGE2_CHARACTERS).read_bytes()
    registry = ArtifactStore(_free_ctx(ctx)).read_own(
        CharacterRegistry, STAGE2_CHARACTERS)[0].model_dump(mode='json', by_alias=True)

    def submit(review, *, state='PASS', kind=None):
        payload = {'actor': 'test:free-reviewer', 'decisions': [{
            'item_id': row.item_id, 'expected_subject_sha256': row.subject_sha256,
            'state': state, 'reason': '按当前证据逐项重审的合成决定', 'evidence': [],
        } for row in review.items if kind is None or row.kind == kind]}
        path = ctx.novel_dir / global_review.DEFAULT_DECISIONS
        path.write_text(json.dumps(encode_artifact(global_review.DEFAULT_DECISIONS, payload),
                                   ensure_ascii=False), encoding='utf-8')
        return global_review.apply_file(_free_ctx(ctx))

    def consume():
        consumer = _ctx(ctx.novel_dir, 'stage4.design', paid=False)
        value = global_review.generation_grounding(ArtifactStore(consumer))
        return value, consumer.edges[STAGE2_GLOBAL_REVIEW]

    pending = ArtifactStore(_free_ctx(ctx)).read_own(GlobalReview, STAGE2_GLOBAL_REVIEW)[0]
    accepted = submit(pending)
    before_global, before_edge = consume()
    assert before_edge.consumed is not None
    old_relation = next(row for row in accepted.items if row.kind == 'relation')

    write_edit(ctx, {'relations': [{'relation_id': original.relations[0].relation_id,
        'evidence_refs': [{'chapter_ordinal': 3, 'locator': '母亲进来给她添了一盏茶。'}]}]})
    changed, report = extract.run_apply_edit(_free_ctx(ctx))
    assert report.passed
    assert stage4_grounding(changed.model_dump(mode='json', by_alias=True)) == stage4_grounding(
        before_global.model_dump(mode='json', by_alias=True))
    with pytest.raises(HumanEditProtected, match='stale'):
        review_domain.require_current(changed.model_dump(mode='json', by_alias=True),
                                      accepted, characters=registry)
    reopened = ArtifactStore(_free_ctx(ctx)).read_own(GlobalReview, STAGE2_GLOBAL_REVIEW)[0]
    new_relation = next(row for row in reopened.items if row.kind == 'relation')
    assert new_relation.subject_path == old_relation.subject_path
    assert new_relation.item_id != old_relation.item_id
    assert new_relation.subject_sha256 != old_relation.subject_sha256
    assert new_relation.state == 'pending'
    with pytest.raises(ApprovalRequired, match='未收口'):
        consume()

    reaccepted = submit(reopened)
    after_global, after_edge = consume()
    assert all(row.state == 'PASS' for row in reaccepted.items)
    assert after_edge.file != before_edge.file
    assert after_edge.consumed == before_edge.consumed
    assert after_global == changed
    global_bytes = (ctx.novel_dir / STAGE2_GLOBAL_GROUNDING).read_bytes()

    denied = submit(reaccepted, state=negative_state, kind='relation')
    assert next(row for row in denied.items if row.kind == 'relation').state == negative_state
    with pytest.raises(ApprovalRequired, match='未收口'):
        consume()
    from novel.consumption import stage2_generation_review
    assert stage2_generation_review(denied.model_dump(mode='json')) != stage2_generation_review(
        reaccepted.model_dump(mode='json'))
    assert (ctx.novel_dir / STAGE2_GLOBAL_GROUNDING).read_bytes() == global_bytes
    assert (ctx.novel_dir / STAGE2_CHARACTERS).read_bytes() == characters_bytes
    assert ctx.ledger.path.read_bytes() == ledger


def test_generation_permission_keeps_subject_membership_and_quarantine_changes():
    from novel.consumption import stage2_generation_review

    core = _global()
    accepted = _decide_all(build_review(core), set(GLOBAL_REVIEW_KINDS))
    expected = stage2_generation_review(accepted.model_dump(mode='json'))
    reversed_items = accepted.model_copy(update={'items': list(reversed(accepted.items))})
    assert stage2_generation_review(reversed_items.model_dump(mode='json')) == expected
    added = copy.deepcopy(core)
    added['relations'].append(dict(added['relations'][0], relation_id='rel_02'))
    expanded = _decide_all(build_review(added), set(GLOBAL_REVIEW_KINDS))
    assert stage2_generation_review(expanded.model_dump(mode='json')) != expected
    removed = copy.deepcopy(core)
    removed['relations'] = []
    reduced = _decide_all(build_review(removed), set(GLOBAL_REVIEW_KINDS))
    assert stage2_generation_review(reduced.model_dump(mode='json')) != expected

    quarantined = _quarantine_group(accepted, 'secret:secret_01')
    # 同为 UNVER，是否隔离仍改变写作许可；不能只保留 state。
    unquarantined = quarantined.model_copy(update={'items': [
        row.model_copy(update={'generation_disposition': None}) for row in quarantined.items]})
    assert stage2_generation_review(quarantined.model_dump(mode='json')) != stage2_generation_review(
        unquarantined.model_dump(mode='json'))


# ── ② 闸 ────────────────────────────────────────────────────────────────────

def test_alias_identity_pending_cannot_be_treated_as_nonblocking_disclosure():
    grounding = _global()
    review = _decide_all(build_review(grounding), set(GLOBAL_REVIEW_KINDS) - {'alias_identity'})
    _subject, current, approved = review_status(grounding, review)
    assert current and not approved
    pending = [row for row in review.items if row.state == 'pending']
    assert pending and all(row.kind == 'alias_identity' for row in pending)
    counts = review_effect_counts(review)
    assert counts['blocking']['pending'] == len(pending)
    assert counts['disclosure']['pending'] == 0


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


@pytest.mark.parametrize('subject_path', [
    'global.setting.era', 'global.relations.rel_01',
    'global.world_rules.wr_01.versions.v01', 'characters.characters.char_001.role',
])
def test_generation_safety_never_quarantines_core_structure_or_fail(subject_path):
    grounding = _global()
    all_pass = _decide_all(
        build_review(grounding), set(GLOBAL_REVIEW_KINDS))
    subject = next(
        item for item in all_pass.items
        if item.subject_path == subject_path)
    disposition = GlobalReviewGenerationDisposition(
        action='quarantine_group', reason='请求隔离当前主体')
    unsafe = decide(
        all_pass, item_id=subject.item_id,
        expected_subject_sha256=subject.subject_sha256,
        state='UNVER', actor='tester', reason='证据不足',
        generation_disposition=disposition,
        now='2026-09-02T00:00:00+00:00')
    safety = generation_safety(grounding, unsafe)
    assert safety.safe is False
    assert safety.quarantined_groups == ()
    assert subject.item_id in safety.blocking_item_ids

    failed = decide(
        all_pass, item_id=subject.item_id,
        expected_subject_sha256=subject.subject_sha256,
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


def test_failed_identity_mapping_blocks_and_preserves_audit():
    grounding = _global()
    review = _decide_all(build_review(grounding), set(GLOBAL_REVIEW_KINDS) - {'alias_identity'})
    review = _decide_all(review, {'alias_identity'}, state='FAIL')
    assert review_status(grounding, review)[2] is False
    counts = review_effect_counts(review)
    assert counts['blocking']['FAIL'] == len([row for row in review.items if row.kind == 'alias_identity'])
    failed = next(row for row in review.items if row.state == 'FAIL')
    assert failed.actor and failed.reason and failed.decided_at


# ── ④ 评测面完整性一格没松 ──────────────────────────────────────────────────

def test_the_required_surface_still_demands_every_alias_identity_item():
    grounding = _global()
    full = build_review(grounding)
    trimmed = full.model_copy(update={'items': [row for row in full.items if row.kind != 'alias_identity']})
    assert len(trimmed.items) < len(full.items)
    _subject, current, approved = review_status(grounding, trimmed)
    assert not current and not approved


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

def _book_with_review(tmp_path):
    ctx, _artifact = seed(tmp_path)
    return ctx.novel_dir


def _bind_scope_to_workpack(template, workpack_sha256: str):
    assert template.blocking_scope is not None
    return template.model_copy(update={
        'blocking_scope': template.blocking_scope.model_copy(update={
            'expected_workpack_sha256': workpack_sha256,
        }),
    })


def test_the_review_template_defaults_to_blocking_and_all_is_opt_in(tmp_path):
    book = _book_with_review(tmp_path)
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
    assert len(full.decisions) == len(blocking_ids), '当前 Core 全部语义项均阻断'


def test_a_scoped_template_never_decides_anything_on_its_own(tmp_path):
    """裁剪的是阅读范围，不是评测面：没进模板的项在盘上仍是 pending。"""
    book = _book_with_review(tmp_path)
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


def test_a_compact_scope_pass_expands_to_every_current_core_item(tmp_path):
    book = _book_with_review(tmp_path)
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


def test_a_compact_scope_rejects_a_stale_surface_or_incomplete_chapter_claim(tmp_path):
    book = _book_with_review(tmp_path)
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


def test_a_compact_scope_rejects_a_stale_workpack_or_template_placeholders(tmp_path):
    book = _book_with_review(tmp_path)
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


def test_a_new_template_cannot_refresh_an_old_workpack_reading_claim(monkeypatch, tmp_path):
    """读 A 后生成 B 模板，不能只改三格就把“读过 B”凭证一起刷新。"""
    book = _book_with_review(tmp_path)
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


def test_an_explicit_item_decision_overrides_the_compact_scope_default(tmp_path):
    book = _book_with_review(tmp_path)
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


def test_an_unknown_review_scope_is_refused_instead_of_silently_widening(tmp_path):
    from novel.errors import TerminalState

    book = _book_with_review(tmp_path)
    ctx = _ctx(book, 'stage2.global', paid=False)
    with pytest.raises(TerminalState, match='review-scope'):
        global_review.review_template(ctx, scope='blocking_only')


def test_the_workpack_follows_the_same_scope_rule(tmp_path):
    book = _book_with_review(tmp_path)
    ctx = _ctx(book, 'stage2.global', paid=False)
    default = json.loads(global_review.review_workpack_json(ctx))
    full = json.loads(global_review.review_workpack_json(ctx, scope='all'))
    assert len(full['items']) == len(default['items']) > 0
    assert all(review_effect_of(row['kind']) == 'blocking'
               for row in default['items'])
    assert default['main_text_subject_sha256'] == \
        default['binding']['main_text_subject_sha256']
    assert len(default['main_text_subject_sha256']) == 64


def test_workpack_v6_preserves_items_and_includes_whole_book_text_once(tmp_path):
    book = _book_with_review(tmp_path)
    ctx = _ctx(book, 'stage2.global', paid=False)
    review = ArtifactStore(ctx).read(GlobalReview, global_review.REL)

    first = global_review.review_workpack(ctx, scope='all')
    second = global_review.review_workpack(ctx, scope='all')
    assert first == second
    assert first['workpack_sha256'] == second['workpack_sha256']
    assert first['kind'] == 'stage2-global-review-workpack-v6'
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
    assert all('fate_companion' not in row for row in blocking['items'])
    assert any(row['kind'] == 'alias_identity' for row in blocking['items'])


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


def test_the_api_row_carries_the_server_derived_effect_and_group_key(tmp_path):
    from novel.views.global_review import global_review_view

    book = _book_with_review(tmp_path)
    view = global_review_view(str(book.parent), book.name.split('_')[-1])

    assert view.approved is False
    assert view.generation_safe is False
    assert view.quarantined_groups == []
    assert sorted(view.effect_counts) == ['blocking', 'disclosure']
    assert view.effect_counts['disclosure']['pending'] == 0
    assert view.effect_counts['blocking']['pending'] == len(view.items)
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


def test_current_core_pending_items_stay_in_inbox_and_block(tmp_path):
    from novel.views.inbox import inbox_view
    book = _book_with_review(tmp_path)
    view = inbox_view(str(book.parent), book.name.split('_')[-1])
    rows = [row for row in view.items if row.kind == 'verification']
    assert rows
    review = ArtifactStore(_ctx(book, 'stage2.global', paid=False)).read(GlobalReview, global_review.REL)
    assert {row.rule for row in rows} >= {item.item_id for item in review.items}
    assert all(row.delivery_effect == 'blocking' for row in rows)


def test_quarantined_secret_is_removed_from_generation_but_retained_in_inbox(tmp_path):
    """隔离秘密不抹去同义且已接受的权威关系，也不改写正式事实或 UNVER。"""
    book = _book_with_review(tmp_path)
    stage2_ctx = _ctx(book, 'stage2.global', paid=False)
    store = ArtifactStore(stage2_ctx)
    grounding = grounding_io.read_own(store, STAGE2_GLOBAL_GROUNDING)[0]
    registry = store.read(CharacterRegistry, 'stage2/characters.json').model_dump(mode='json', by_alias=True)
    payload = grounding.model_dump(mode='json', by_alias=True)
    secret = payload['arc_secrets'][0]
    secret.update(fact=None, fact_ref={'kind': 'relation', 'relation_id': payload['relations'][0]['relation_id']},
                  revealed_from=payload['relations'][0]['revealed_from'], evidence_refs=[])
    grounding = GlobalGrounding.model_validate(payload)
    store.write(STAGE2_GLOBAL_GROUNDING, grounding_io.for_write(store, grounding))
    before = store.path(STAGE2_GLOBAL_GROUNDING).read_bytes()
    review = _quarantine_group(_decide_all(build_review(payload, characters=registry), set(GLOBAL_REVIEW_KINDS)),
                               'secret:secret_01')
    store.write(STAGE2_GLOBAL_REVIEW, review)
    from novel.views.global_review import global_review_view
    public = global_review_view(book.parent, stage2_ctx.sid)
    assert not public.approved and public.generation_safe
    assert public.quarantined_groups == ['secret:secret_01']
    downstream_ctx = _ctx(book, 'stage4.plan', paid=False)
    generated = global_review.generation_grounding(ArtifactStore(downstream_ctx))
    assert generated.arc_secrets == []
    assert generated.relations == grounding.relations
    assert generated.world_rules == grounding.world_rules
    assert generated.setting == grounding.setting
    assert set(downstream_ctx.edges) >= {STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW}
    assert store.path(STAGE2_GLOBAL_GROUNDING).read_bytes() == before
    from novel.views.inbox import inbox_view
    inbox = inbox_view(book.parent, stage2_ctx.sid)
    quarantined_ids = {row.item_id for row in review.items if row.state == 'UNVER'
                       and review_group_key(row.subject_path) == 'secret:secret_01'}
    rows = [row for row in inbox.items if row.rule in quarantined_ids]
    assert {row.rule for row in rows} == quarantined_ids
    assert all(row.delivery_effect == 'disclosure' and row.attention == 'reviewed_retained' for row in rows)
    assert all(row.decision is not None and row.decision.state == 'UNVER'
               and row.decision.generation_disposition == 'quarantine_group' for row in rows)
