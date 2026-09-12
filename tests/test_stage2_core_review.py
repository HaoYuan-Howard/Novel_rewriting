"""Core v2/v7 的统一审阅边界；合成反例不代替真书语义验收。"""
from __future__ import annotations

import copy
import hashlib
from types import SimpleNamespace

import pytest

from contracts.invariants import GLOBAL_REVIEW_KINDS
from contracts.schema.evaluation import GlobalReviewDecisionSet
from novel.errors import HumanEditProtected
from novel.quality.global_review import (
    alias_review_key, apply_decision_set, build_review, decide, generation_safety,
    review_evidence_slots, review_group_key, review_source, review_status,
)
from novel.stages.stage2.core_evidence import evidence_id
from novel.stages.stage2.global_review import _formal_materials


def test_core_batch_sparse_patch_roundtrip_does_not_invent_null_edits():
    from contracts.schema.stage2_review import Stage2CoreEditBatch
    batch = Stage2CoreEditBatch.model_validate({
        'expected_characters_sha256': 'a' * 64, 'expected_global_sha256': 'b' * 64,
        'global': {'setting': {'social_context': '老宅家事',
                              'social_context_evidence_refs': [
                                  {'chapter_ordinal': 1, 'locator': '老宅'}]}}})
    raw = batch.model_dump(mode='json', by_alias=True)
    assert raw['global']['setting'].keys() == {'social_context', 'social_context_evidence_refs'}
    assert Stage2CoreEditBatch.model_validate_json(batch.model_dump_json()) == batch


def test_core_reviewer_binding_keeps_frozen_sources_and_explicit_nonedit_path():
    from contracts.schema.api import Stage2NodeReviewRequest
    from novel.orchestrator.reviewer_tasks import ReviewerTaskError, _validate_approval_binding
    plain = Stage2NodeReviewRequest(node='stage2.global', review_token='s2gr2_' + 'a' * 64,
        decision='uncertain', actor='test:commander', reason='需要核对原文',
        review={'scope_sha256': 'c' * 64, 'default_decision': 'uncertain', 'exceptions': []}
        ).model_dump(mode='json')
    expected = copy.deepcopy(plain)
    expected['core_edits'] = {'expected_characters_sha256': 'a' * 64,
                              'expected_global_sha256': 'b' * 64,
                              'characters': None, 'global': None}
    _validate_approval_binding('stage2_global_review', plain, {'payload': expected})
    submitted = copy.deepcopy(plain)
    submitted['core_edits'] = copy.deepcopy(expected['core_edits'])
    submitted['core_edits']['expected_global_sha256'] = 'd' * 64
    with pytest.raises(ReviewerTaskError, match='immutable binding'):
        _validate_approval_binding('stage2_global_review', submitted, {'payload': expected})


def core_pair():
    def anchor(text, start=0):
        row = {'chapter_ordinal': 1, 'start': start, 'end': start + len(text),
               'text_sha256': hashlib.sha256(text.encode()).hexdigest()}
        return evidence_id(row), row
    person_ref, person_evidence = anchor('甲是邻居。')
    relation_ref, relation_evidence = anchor('甲乙同窗。', 5)
    characters = {'characters': {'char_001': {
        'canonical': '甲',
        'aliases': [{'surface': '甲', 'identity_revealed_from': 1,
                     'evidence_refs': [person_ref]},
                    {'surface': '新邻居', 'identity_revealed_from': 3,
                     'evidence_refs': [person_ref]}],
        'first_chapter': 1, 'first_appearance_evidence_refs': [person_ref],
        'role': '搬到隔壁的邻居', 'role_evidence_refs': [person_ref],
        'name_distinctiveness': 'high'}},
        'evidence_anchors': {person_ref: person_evidence}}
    global_ = {'characters_reference_sha256': 'a' * 64,
        'setting': {'era': None, 'social_context': None,
                    'era_evidence_refs': [], 'social_context_evidence_refs': []},
        'world_rules': [], 'relations': [{
            'relation_id': 'rel_01', 'subject_id': 'char_001', 'object_id': 'char_002',
            'predicate': '同窗', 'effective_from': 1, 'effective_until': 2,
            'revealed_from': 3, 'evidence_refs': [relation_ref]}],
        'arc_secrets': [{'secret_id': 'secret_01', 'fact': None,
                         'fact_ref': {'kind': 'relation', 'relation_id': 'rel_01'},
                         'setup_chapters': [], 'revealed_from': 3, 'evidence_refs': []}],
        'evidence_anchors': {relation_ref: relation_evidence}}
    return characters, global_


def accepted(global_, characters):
    review = build_review(global_, characters=characters)
    for row in review.items:
        review = decide(review, item_id=row.item_id,
                        expected_subject_sha256=row.subject_sha256,
                        state='PASS', actor='test:Commander', reason='逐条核验合成原文',
                        now='2026-09-12T00:00:00Z')
    return review


def test_core_review_uses_two_independent_owners_and_retired_fields_have_no_subjects():
    characters, global_ = core_pair()
    before = copy.deepcopy((characters, global_))
    review = build_review(global_, characters=characters)
    assert (characters, global_) == before
    assert {row.kind for row in review.items} <= set(GLOBAL_REVIEW_KINDS)
    assert {'character_identity', 'alias_identity', 'role', 'secret', 'reveal'} <= {
        row.kind for row in review.items}
    assert all(row.subject_path.startswith(('characters.', 'global.')) for row in review.items)
    assert not any('source_blurb' in row.subject_path or 'fate' in row.subject_path
                   or 'knowledge_events' in row.subject_path for row in review.items)
    assert {review_group_key(row.subject_path) for row in review.items} >= {
        'character:char_001', 'relation:rel_01', 'secret:secret_01', 'setting'}


def test_missing_registry_is_missing_material_and_cannot_be_accepted():
    _, global_ = core_pair()
    review = build_review(global_)
    missing = next(row for row in review.items if row.kind == 'character_set_complete')
    assert missing.resolution_required
    with pytest.raises(HumanEditProtected, match='缺必要材料'):
        decide(review, item_id=missing.item_id, expected_subject_sha256=missing.subject_sha256,
               state='PASS', actor='test', reason='不能把缺文件当空人物')
    assert not generation_safety(global_, review).safe


def test_alias_evidence_correction_keeps_global_semantics_and_completeness_accepted():
    characters, global_ = core_pair()
    previous = accepted(global_, characters)
    changed = copy.deepcopy(characters)
    old_ref = changed['characters']['char_001']['aliases'][1]['evidence_refs'][0]
    evidence = {**changed['evidence_anchors'][old_ref], 'start': 1,
                'text_sha256': hashlib.sha256('是邻居。'.encode()).hexdigest()}
    ref = evidence_id(evidence)
    changed['evidence_anchors'][ref] = evidence
    changed['characters']['char_001']['aliases'][1]['evidence_refs'] = [ref]
    review = build_review(global_, previous, characters=changed)
    assert not review_status(global_, previous, characters=changed)[1]
    assert any(row.state == 'pending' for row in review.items if row.kind == 'alias_identity')
    assert all(row.state == 'PASS' for row in review.items if row.subject_path.startswith('global.'))
    assert next(row for row in review.items if row.kind == 'character_set_complete').state == 'PASS'


def test_changed_alias_mapping_reopens_character_review_without_rebuying_global_subjects():
    characters, global_ = core_pair()
    previous = accepted(global_, characters)
    changed = copy.deepcopy(characters)
    changed['characters']['char_001']['aliases'][1]['identity_revealed_from'] = 2
    review = build_review(global_, previous, characters=changed)
    assert next(row for row in review.items if row.subject_path.endswith(
        'aliases.' + alias_review_key('新邻居'))).state == 'pending'
    assert all(row.state == 'PASS' for row in review.items if row.subject_path.startswith('global.'))


def test_reordering_unique_aliases_retains_semantic_reviews_and_original_paths():
    from novel.stages.stage2.global_review import _subject_at
    characters, global_ = core_pair()
    previous = accepted(global_, characters)
    changed = copy.deepcopy(characters)
    changed['characters']['char_001']['aliases'].reverse()
    review = build_review(global_, previous, characters=changed)
    assert all(row.state == 'PASS' for row in review.items)
    assert {row.subject_path for row in review.items} == {row.subject_path for row in previous.items}
    path = 'characters.characters.char_001.aliases.' + alias_review_key('新邻居')
    assert _subject_at(review_source(global_, changed), path)['surface'] == '新邻居'


def test_fact_ref_secret_inherits_only_its_authoritative_local_evidence():
    characters, global_ = core_pair()
    slots = review_evidence_slots(review_source(global_, characters))
    secret = [anchor for path, anchor in slots if path == 'global.arc_secrets.secret_01']
    relation = [anchor for path, anchor in slots if path == 'global.relations.rel_01']
    assert secret == relation and len(secret) == 1
    previous = accepted(global_, characters)
    changed = copy.deepcopy(global_)
    ref = changed['relations'][0]['evidence_refs'][0]
    changed['evidence_anchors'][ref]['end'] += 1
    review = build_review(changed, previous, characters=characters)
    assert next(row for row in review.items if row.kind == 'secret').state == 'pending'
    assert all(row.state == 'PASS' for row in review.items if row.subject_path.startswith('characters.'))


def test_changed_relation_endpoint_identity_reopens_only_related_global_judgments():
    characters, global_ = core_pair()
    previous = accepted(global_, characters)
    changed = copy.deepcopy(characters)
    changed['characters']['char_001']['aliases'][1]['surface'] = '旧邻居'
    review = build_review(global_, previous, characters=changed)
    assert next(row for row in review.items if row.kind == 'relation').state == 'pending'
    assert next(row for row in review.items if row.kind == 'secret').state == 'pending'
    assert all(row.state == 'PASS' for row in review.items if row.kind == 'setting_fact')


@pytest.mark.parametrize('effective_from,effective_until,revealed_from', [(3, None, 1), (1, 2, 3)])
def test_truth_and_disclosure_timelines_remain_independent(effective_from, effective_until, revealed_from):
    characters, global_ = core_pair()
    global_['relations'][0].update(effective_from=effective_from,
                                  effective_until=effective_until, revealed_from=revealed_from)
    global_['arc_secrets'][0]['revealed_from'] = revealed_from
    review = accepted(global_, characters)
    assert generation_safety(global_, review, characters=characters).safe


def test_formal_materials_deduplicate_same_anchor_across_claims_and_refuse_damage():
    characters, _ = core_pair()
    anchor = next(iter(characters['evidence_anchors'].values()))
    source = {1: SimpleNamespace(text='甲是邻居。甲乙同窗。')}
    materials, unresolved = _formal_materials([('one', anchor), ('two', anchor)], source)
    assert len(materials) == 1 and unresolved == []
    assert materials[0]['literal'] == '甲是邻居。'
    broken = {**anchor, 'text_sha256': 'f' * 64}
    materials, unresolved = _formal_materials([('one', broken)], source)
    assert materials == [] and len(unresolved) == 1


def test_new_negative_on_alias_identity_survives_old_positive_review():
    characters, global_ = core_pair()
    original = accepted(global_, characters)
    alias = next(row for row in original.items if row.kind == 'alias_identity')
    decisions = GlobalReviewDecisionSet(actor='test:Commander', decisions=[{
        'item_id': alias.item_id, 'expected_subject_sha256': alias.subject_sha256,
        'state': 'FAIL', 'reason': '原文并未把这两个身份揭示为同一人', 'evidence': []}])
    rejected = apply_decision_set(original, decisions)
    assert not generation_safety(global_, rejected, characters=characters).safe
    same = build_review(global_, rejected, characters=characters)
    assert next(row for row in same.items if row.item_id == alias.item_id).state == 'FAIL'


def test_new_subject_is_pending_even_with_an_old_fully_accepted_review():
    characters, global_ = core_pair()
    previous = accepted(global_, characters)
    changed = copy.deepcopy(global_)
    changed['arc_secrets'].append({**changed['arc_secrets'][0], 'secret_id': 'secret_02'})
    review = build_review(changed, previous, characters=characters)
    assert any(row.state == 'pending' for row in review.items if 'secret_02' in row.subject_path)
    assert not generation_safety(changed, review, characters=characters).safe
