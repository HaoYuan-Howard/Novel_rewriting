from __future__ import annotations

import hashlib

import pytest

from contracts.schema.evaluation import (
    CapabilityMetrics,
    EvaluatorFingerprint,
    EvaluatorProfile,
    EvidenceRef,
    JudgeDecision,
)
from novel.quality.evaluator import double_blind_decision, wilson_lower
from novel.quality.global_review import build_review, decide, review_status
from novel.errors import HumanEditProtected


_H = hashlib.sha256(b'x').hexdigest()


def _fingerprint(**changes):
    raw = dict(
        dataset_sha256=_H, model='judge-v1', prompt_sha256=_H,
        schema_sha256=_H, decoder_sha256=_H,
    )
    raw.update(changes)
    return EvaluatorFingerprint(**raw)


def _profile():
    names = (
        'event_order', 'verbatim_fidelity', 'hard_number_fidelity',
        'reference_boundary', 'secret_timeline', 'world_rule_consistency',
        'trim_equivalence', 'style_transfer',
    )
    return EvaluatorProfile(
        profile_version=1, profile_id='profile-v1',
        calibrated_at='2026-08-16T00:00:00Z', runs=3,
        thresholds_locked_before_evaluation=True,
        capabilities=[CapabilityMetrics(
            ability=name, fingerprint=_fingerprint(), n=100,
            pass_precision_wilson95_lower=.91,
            fail_precision_wilson95_lower=.91,
            violation_recall_wilson95_lower=.91,
            evidence_closure=.96, rerun_consistency=.96, enabled=True,
        ) for name in names],
    )


def _judge(name='a', verdict='PASS', evidence=True):
    refs = [EvidenceRef(artifact='stage5/deep/ch01.json', subject_sha256=_H,
                        start=0, end=2)] if evidence else []
    return JudgeDecision(judge=name, verdict=verdict, evidence=refs, reason='fixed rubric')


def test_wilson_lower_is_conservative():
    assert wilson_lower(40, 40) < 1
    assert wilson_lower(0, 0) == 0
    assert wilson_lower(90, 100) < .90


def test_double_blind_requires_profile_identity_agreement_and_evidence():
    profile = _profile()
    good = double_blind_decision(
        ability='event_order', subject_key='case-1', first=_judge(), second=_judge('b'),
        profile=profile, current=_fingerprint())
    assert (good.verdict, good.automated) == ('PASS', True)

    drift = double_blind_decision(
        ability='event_order', subject_key='case-1', first=_judge(), second=_judge('b'),
        profile=profile, current=_fingerprint(model='judge-v2'))
    assert (drift.verdict, drift.automated) == ('UNVER', False)

    split = double_blind_decision(
        ability='event_order', subject_key='case-1', first=_judge(), second=_judge('b', 'FAIL'),
        profile=profile, current=_fingerprint())
    assert split.verdict == 'UNVER'

    no_evidence = double_blind_decision(
        ability='event_order', subject_key='case-1',
        first=_judge(evidence=False), second=_judge('b'),
        profile=profile, current=_fingerprint())
    assert no_evidence.verdict == 'UNVER'


def _global(role='静态身份'):
    return {
        'entities': {'characters': {
            'char_001': {
                'name_distinctiveness': 'HIGH', 'role': role,
                'address': [{'context': 'address', 'surface': '阿甲'}],
            },
        }},
        'canon': {
            'world_rules': [{'rule_id': 'wr_01', 'kind': 'social',
                             'versions': [{'effective_from_chapter': 1,
                                           'desc': '规矩'}]}],
            'secrets': [{'secret_id': 'secret_01', 'fact': '真相',
                         'character_known_by': ['char_001'],
                         'reader_reveal_events': [{'chapter': 2}]}],
        },
    }


def test_global_review_enumerates_and_only_inherits_unchanged_subjects():
    first = build_review(_global())
    assert first.items
    assert {'canon.world_rules', 'canon.secrets'} <= {
        row.subject_path for row in first.items}
    target = first.items[0]
    approved = decide(
        first, item_id=target.item_id,
        expected_subject_sha256=target.subject_sha256,
        state='PASS', actor='reviewer-1', reason='按口径核对',
        now='2026-08-16T00:00:00Z')
    same = build_review(_global(), approved)
    assert next(row for row in same.items if row.item_id == target.item_id).state == 'PASS'

    changed = _global()
    changed['entities']['characters']['char_001']['name_distinctiveness'] = 'LOW'
    refreshed = build_review(changed, approved)
    path = 'entities.characters.char_001.name_distinctiveness'
    assert next(row for row in refreshed.items if row.subject_path == path).state == 'pending'


def test_empty_stage2_collections_still_require_explicit_completeness_decisions():
    grounding = _global()
    grounding['canon']['world_rules'] = []
    grounding['canon']['secrets'] = []
    review = build_review(grounding)
    collection_items = [
        (row.kind, row.subject_path) for row in review.items
        if row.kind.endswith('_set_complete')
    ]
    assert collection_items == [
        ('world_rule_set_complete', 'canon.world_rules'),
        ('secret_set_complete', 'canon.secrets'),
    ]
    assert all(
        row.state == 'pending' for row in review.items
        if row.kind.endswith('_set_complete'))


def test_review_approval_recomputes_complete_current_subject_and_required_surface():
    grounding = _global()
    review = build_review(grounding)
    for item in list(review.items):
        review = decide(
            review, item_id=item.item_id,
            expected_subject_sha256=item.subject_sha256,
            state='PASS', actor='reviewer-1', reason='逐项核对',
            now='2026-08-16T00:00:00Z')
    current_sha, current, approved = review_status(grounding, review)
    assert current_sha == review.source_sha256
    assert current and approved

    # Copying the full-subject hash cannot hide a newly required or omitted row.
    forged = review.model_copy(update={'items': review.items[1:]})
    assert review_status(grounding, forged)[1:] == (False, False)

    # An unaudited field edit changes the complete GlobalGrounding subject even
    # when every individually enumerated item happens to be byte-identical.
    edited = _global(role='变更后的身份')
    assert review_status(edited, review)[1:] == (False, False)

    # Refreshing from that edit may inherit only exact item identities and then
    # binds those decisions to the new complete subject hash.
    refreshed = build_review(edited, review)
    assert all(item.state == 'PASS' for item in refreshed.items)
    assert review_status(edited, refreshed)[1:] == (True, True)


def test_global_review_rejects_stale_and_unaudited_decisions():
    review = build_review(_global())
    target = review.items[0]
    with pytest.raises(HumanEditProtected, match='stale'):
        decide(
            review, item_id=target.item_id, expected_subject_sha256='0' * 64,
            state='PASS', actor='reviewer', reason='ok')
    with pytest.raises(ValueError, match='actor'):
        decide(
            review, item_id=target.item_id,
            expected_subject_sha256=target.subject_sha256,
            state='PASS', actor=' ', reason='ok')
