from __future__ import annotations

import hashlib

import pytest

from contracts.schema.evaluation import EvidenceRef, JudgeDecision
from novel.quality.evaluator import double_blind_decision
from novel.quality.global_review import build_review, decide, review_status
from novel.errors import HumanEditProtected


_H = hashlib.sha256(b'x').hexdigest()


def _judge(name='a', verdict='PASS', evidence=True):
    refs = [EvidenceRef(artifact='stage5/deep/ch01.json', subject_sha256=_H,
                        start=0, end=2)] if evidence else []
    return JudgeDecision(judge=name, verdict=verdict, evidence=refs, reason='fixed rubric')


@pytest.mark.parametrize(('first', 'second', 'why'), [
    (_judge(), _judge('b'), 'A/B 同判 PASS'),
    (_judge(verdict='FAIL'), _judge('b', 'FAIL'), 'A/B 同判 FAIL'),
    (_judge(), _judge('b', 'FAIL'), '结论不一致'),
    (_judge(verdict='UNVER', evidence=False), _judge('b'), '明确判为 UNVER'),
    (_judge(evidence=False), _judge('b'), '缺少可闭合证据'),
])
def test_the_merged_verdict_is_always_unver_and_the_reason_is_never_the_same(
        first, second, why):
    """本仓没有自动放行门 ⇒ 合并决议**恒** UNVER。

    ⚠ 这条守卫的重点在第二个断言：五种情形的 `reason` **必须逐句不同**。
    如果它们被压成一句套话，人在 inbox 里就分不出「两名判官都说没问题」与
    「判官压根没给证据」——而那两件事的处置完全相反。
    """
    decision = double_blind_decision(
        ability='reference_boundary', subject_key='case-1',
        first=first, second=second)
    assert decision.verdict == 'UNVER'
    assert why in decision.reason, decision.reason


def test_merged_evidence_is_the_deduplicated_union_of_both_judges():
    decision = double_blind_decision(
        ability='reference_boundary', subject_key='case-1',
        first=_judge(), second=_judge('b'))
    # 两名判官指的是同一段字节 ⇒ 并集去重后只剩一条，而不是重复两条。
    assert len(decision.evidence) == 1
    assert [judge.verdict for judge in decision.judges] == ['PASS', 'PASS']


def test_an_automated_pass_is_not_expressible_at_all():
    """⚠ 类型上就踩不进去：`SemanticDecision.verdict` 是 `Literal['UNVER']`。

    这比「有一条校验器拦着」强——一条永远不可能被触发的校验器会绿一辈子，
    而没人知道它到底还在不在。
    """
    from pydantic import ValidationError

    from contracts.schema.evaluation import SemanticDecision

    with pytest.raises(ValidationError):
        SemanticDecision(
            ability='reference_boundary', subject_key='case-1', verdict='PASS',
            evidence=[], reason='两名判官都说没问题',
            judges=[_judge(), _judge('b')])


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
