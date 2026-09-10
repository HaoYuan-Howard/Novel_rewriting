# -*- coding: utf-8 -*-
"""Stage4A repair/replan/human edit 的纯语义回归。

这些用例不造审批账、run journal 或 CLI；它们只钉住后续各入口必须共用的数学身份、
累计关系判官与完整章替换边界。
"""
from __future__ import annotations

import copy

import pytest

from contracts.invariants import BLIND_ALIAS_ALGORITHM
from novel.stages.stage4 import design_evolution
from novel.stages.stage4.blind import BlindPlanError, compile_blind_chapter_edit
from novel.stages.stage4.design_evolution import (
    DesignEvolutionError,
    accumulate_relation_constraints,
    canonical_relation_constraint,
    chapter_strategy_sha256,
    evaluate_relation_constraints,
    merge_relation_constraints,
    non_target_chapter_drift,
    relation_constraint_fingerprint,
    relation_constraint_set_sha256,
    replace_candidate_chapters,
    replan_affected_chapters,
    strategy_epoch_sha256,
)


A = 'a_' + '1' * 16
B = 'a_' + '2' * 16
C = 'a_' + '3' * 16
OBSERVER = 'a_' + '4' * 16


def _basis() -> dict[str, str]:
    return {
        'review_contract': 'stage4-design-review-v3',
        'root_clean_sha1': 'a' * 40,
        'truth_sha256': 'b' * 64,
        'alias_algorithm': BLIND_ALIAS_ALGORITHM,
    }


def _strategy(
    text: str = '按因果闭合重新组织全书。',
    *, first: str = 'CH_EVEN', second: str = 'CH_EVEN',
) -> dict[str, object]:
    return {
        'reconstruction_strategy': text,
        'chapter_weights': [
            {'chapter_ordinal': 1, 'weight': first},
            {'chapter_ordinal': 2, 'weight': second},
        ],
    }


def _relation(kind: str, *aliases: str) -> dict[str, str]:
    fields = {
        'story_before': ('before_alias', 'after_alias'),
        'response_to': ('stimulus_alias', 'response_alias'),
        'same_scene': ('left_alias', 'right_alias'),
        'observes': ('observer_alias', 'observed_alias', 'observation_alias'),
    }[kind]
    return {'kind': kind, **dict(zip(fields, aliases, strict=True))}


def _scene(
    *land: str,
    context: tuple[str, ...] = (),
    transition: str = 'X_CONTINUE',
) -> dict[str, object]:
    return {
        'land_fact_aliases': list(land),
        'context_fact_aliases': list(context),
        'transition': transition,
    }


def test_strategy_epoch_excludes_other_chapter_weights_but_chapter_digest_does_not():
    epoch = strategy_epoch_sha256(_basis(), '按因果闭合重新组织全书。')
    same = strategy_epoch_sha256(copy.deepcopy(_basis()), '按因果闭合重新组织全书。')
    changed = strategy_epoch_sha256(_basis(), '改用另一条宏观策略。')

    assert epoch == same
    assert changed != epoch
    assert chapter_strategy_sha256(epoch, 1, 'CH_EVEN') == \
        chapter_strategy_sha256(epoch, 1, 'CH_EVEN')
    assert chapter_strategy_sha256(epoch, 1, 'CH_MAJOR') != \
        chapter_strategy_sha256(epoch, 1, 'CH_EVEN')
    assert chapter_strategy_sha256(epoch, 2, 'CH_EVEN') != \
        chapter_strategy_sha256(epoch, 1, 'CH_EVEN')



def test_strategy_epoch_keeps_local_truth_changes_out_but_preserves_global_changes():
    basis = _basis()
    current = strategy_epoch_sha256(basis, '冻结宏观策略')
    assert current == strategy_epoch_sha256(
        {**basis, 'truth_sha256': 'c' * 64}, '冻结宏观策略')
    for key, changed in (
        ('review_contract', 'changed-review-contract'),
        ('root_clean_sha1', 'd' * 40), ('alias_algorithm', 'changed-alias'),
    ):
        assert current != strategy_epoch_sha256({**basis, key: changed}, '冻结宏观策略')
    with pytest.raises(DesignEvolutionError, match='SHA-256'):
        strategy_epoch_sha256({**basis, 'truth_sha256': 'broken'}, '冻结宏观策略')


def test_replan_only_marks_own_weight_changes_unless_global_strategy_changes():
    old = _strategy()
    weight_only = _strategy(second='CH_MAJOR')
    global_change = _strategy('换一条宏观事实组织策略。', second='CH_MAJOR')

    assert replan_affected_chapters(old, old) == ()
    assert replan_affected_chapters(old, weight_only) == (2,)
    assert replan_affected_chapters(old, global_change) == (1, 2)

    missing_chapter = copy.deepcopy(weight_only)
    missing_chapter['chapter_weights'] = missing_chapter['chapter_weights'][:1]
    with pytest.raises(DesignEvolutionError, match='不得改变章集合'):
        replan_affected_chapters(old, missing_chapter)


def test_relation_merge_is_canonical_monotonic_and_content_addressed():
    canonical_same_scene = _relation('same_scene', A, B)
    before = _relation('story_before', A, C)
    committed = [canonical_same_scene]
    incoming = [canonical_same_scene, before, copy.deepcopy(before)]

    merged = merge_relation_constraints(committed, incoming)

    assert len(merged) == 2
    assert canonical_relation_constraint(canonical_same_scene) == {
        'kind': 'same_scene', 'enforcement': 'hard',
        'left_alias': A, 'right_alias': B,
    }
    review_same_scene = {**canonical_same_scene, 'enforcement': 'review'}
    assert relation_constraint_fingerprint(review_same_scene) == \
        relation_constraint_fingerprint(canonical_same_scene)
    assert relation_constraint_set_sha256([*committed, *incoming]) == \
        relation_constraint_set_sha256(list(reversed([*incoming, *committed])))
    # 调用者的输入没有被规范化过程原地改写。
    assert 'enforcement' not in canonical_same_scene

    # blind truth schema 已把无向对规范成升序；repair 路不替坏输入偷换身份。
    with pytest.raises(DesignEvolutionError, match='same_scene alias 必须升序'):
        canonical_relation_constraint(_relation('same_scene', B, A))


def test_relation_constraints_accumulate_only_within_epoch_and_blind_truth():
    epoch = '1' * 64
    next_epoch = '2' * 64
    before = _relation('story_before', A, B)
    same_scene = _relation('same_scene', B, C)
    truth = [
        {**before, 'enforcement': 'review'},
        {**same_scene, 'enforcement': 'review'},
    ]

    accumulated = accumulate_relation_constraints(
        parent_epoch_sha256=epoch,
        current_epoch_sha256=epoch,
        inherited=[before],
        requested=[same_scene, before],
        truth_relations=truth,
    )
    assert {
        relation_constraint_fingerprint(row) for row in accumulated
    } == {
        relation_constraint_fingerprint(before),
        relation_constraint_fingerprint(same_scene),
    }

    # 宏观策略换 epoch 后旧承诺不得继续绑定新策略。
    reset = accumulate_relation_constraints(
        parent_epoch_sha256=epoch,
        current_epoch_sha256=next_epoch,
        inherited=[before],
        requested=[same_scene],
        truth_relations=truth,
    )
    assert reset == (canonical_relation_constraint(same_scene),)

    invented = _relation('story_before', A, C)
    with pytest.raises(DesignEvolutionError, match='blind truth'):
        accumulate_relation_constraints(
            parent_epoch_sha256=epoch,
            current_epoch_sha256=epoch,
            inherited=[], requested=[invented], truth_relations=truth)

    hard_truth = _relation('response_to', A, C)
    with pytest.raises(DesignEvolutionError, match='review relation'):
        accumulate_relation_constraints(
            parent_epoch_sha256=epoch,
            current_epoch_sha256=epoch,
            inherited=[], requested=[hard_truth],
            truth_relations=[*truth, {**hard_truth, 'enforcement': 'hard'}])


@pytest.mark.parametrize('relations', [
    [
        _relation('story_before', A, B),
        _relation('story_before', B, C),
        _relation('story_before', C, A),
    ],
    [
        _relation('response_to', A, B),  # A <= B
        _relation('story_before', B, A),  # B < A
    ],
    [
        _relation('observes', OBSERVER, A, B),  # A <= B
        _relation('story_before', B, A),
    ],
])
def test_relation_conflicts_close_before_wire(relations):
    with pytest.raises(DesignEvolutionError, match='严格顺序环'):
        merge_relation_constraints((), relations)


def test_same_scene_does_not_invent_first_visibility_equality_conflict():
    # 生产判官只要求场景集合有交集：A 可以先出现，之后与 B 同场。
    relations = merge_relation_constraints((), [
        _relation('story_before', A, B),
        _relation('same_scene', A, B),
    ])
    chapter = {'scenes': [
        _scene(A),
        _scene(B, context=(A,)),
    ]}

    evaluation = evaluate_relation_constraints(relations, chapter)

    assert evaluation.unclosed == ()
    assert evaluation.locally_closed
    assert evaluation.fully_verified


def test_story_before_uses_story_time_instead_of_disclosure_array_order():
    relation = _relation('story_before', A, B)
    fingerprint = relation_constraint_fingerprint(relation)

    # 先披露 B，再以 X_EARLIER 回到 A：披露倒序，但故事世界 A < B。
    valid_flashback = evaluate_relation_constraints(
        [relation], {'scenes': [
            _scene(B, transition='X_START'),
            _scene(A, transition='X_EARLIER'),
        ]})
    assert valid_flashback.unclosed == ()
    assert valid_flashback.needs_story_time_review == ()
    assert valid_flashback.fully_verified

    # 数组看似 A、B，B 却显式打开 earlier：故事世界实际 B < A。
    reversed_story_time = evaluate_relation_constraints(
        [relation], {'scenes': [
            _scene(A, transition='X_START'),
            _scene(B, transition='X_EARLIER'),
        ]})
    assert reversed_story_time.unclosed[0].findings == (
        f'story_before:{A}!<{B}:故事时序反向',)

    # 同场没有事实内序字段；不能把“机器够不着”洗成 fully verified。
    same_scene = evaluate_relation_constraints(
        [relation], {'scenes': [_scene(A, B, transition='X_START')]})
    assert same_scene.unclosed == ()
    assert same_scene.needs_story_time_review == (fingerprint,)
    assert not same_scene.fully_verified


def test_evaluator_returns_each_accumulated_unclosed_relation_with_stable_identity():
    satisfied = _relation('story_before', A, B)
    unclosed = _relation('same_scene', A, C)
    chapter = {'scenes': [_scene(A), _scene(B), _scene(C)]}

    evaluation = evaluate_relation_constraints([unclosed, satisfied], chapter)

    assert len(evaluation.unclosed) == 1
    finding = evaluation.unclosed[0]
    assert finding.kind == 'same_scene'
    assert finding.constraint_sha256 == relation_constraint_fingerprint(unclosed)
    assert finding.findings == (f'same_scene:{A}!={C}',)
    assert evaluation.needs_binding_preflight == ()


def test_observes_is_locally_checked_but_never_claimed_fully_verified_in_a():
    observes = _relation('observes', OBSERVER, A, B)
    fingerprint = relation_constraint_fingerprint(observes)

    ordered = evaluate_relation_constraints(
        [observes], {'scenes': [_scene(A), _scene(B)]})
    assert ordered.unclosed == ()
    assert ordered.needs_binding_preflight == (fingerprint,)
    assert ordered.locally_closed
    assert not ordered.fully_verified

    reversed_order = evaluate_relation_constraints(
        [observes], {'scenes': [_scene(B), _scene(A)]})
    assert reversed_order.unclosed[0].findings == (f'observes:{B}<{A}',)
    assert reversed_order.needs_binding_preflight == (fingerprint,)


def test_relation_evaluator_delegates_to_production_judge(monkeypatch):
    calls: list[tuple[list[dict[str, object]], str]] = []

    def fake_findings(relations, _positions, *, suffix=''):
        calls.append((copy.deepcopy(relations), suffix))
        return []

    monkeypatch.setattr(
        design_evolution.checks,
        'narrative_relation_scene_findings',
        fake_findings,
    )
    evaluation = evaluate_relation_constraints(
        [_relation('response_to', A, B), _relation('same_scene', A, C)],
        {'scenes': [_scene(A, B, C)]},
    )

    assert evaluation.fully_verified
    assert len(calls) == 2
    assert all(suffix == '_alias' for _relations, suffix in calls)
    assert all(
        relation['enforcement'] == 'hard'
        for relations, _suffix in calls
        for relation in relations
    )


def _candidate() -> dict[str, object]:
    return {
        'reconstruction_strategy': '冻结策略',
        'output_chapters': [
            {'output_ordinal': 1, 'marker': 'old-1', 'scenes': [_scene(A)]},
            {'output_ordinal': 2, 'marker': 'old-2', 'scenes': [_scene(B)]},
            {'output_ordinal': 3, 'marker': 'old-3', 'scenes': [_scene(C)]},
        ],
        '_provenance': {'marker': 'old-provenance'},
    }


def test_full_chapter_replace_is_exact_and_non_targets_are_byte_stable():
    previous = _candidate()
    replacement = {
        'output_ordinal': 2, 'marker': 'human-2', 'scenes': [_scene(B, C)]}

    current = replace_candidate_chapters(
        previous, [replacement], target_chapters=[2])

    assert current['output_chapters'][1] == replacement
    assert current['output_chapters'][0] == previous['output_chapters'][0]
    assert current['output_chapters'][2] == previous['output_chapters'][2]
    assert current['_provenance'] == previous['_provenance']
    assert non_target_chapter_drift(previous, current, [2]) == ()
    assert previous['output_chapters'][1]['marker'] == 'old-2'

    drifted = copy.deepcopy(current)
    drifted['output_chapters'][0]['marker'] = 'drifted'
    assert non_target_chapter_drift(previous, drifted, [2]) == (1,)
    with pytest.raises(DesignEvolutionError, match='精确覆盖'):
        replace_candidate_chapters(previous, [replacement], target_chapters=[1, 2])


def _complete_human_chapter() -> dict[str, object]:
    return {
        'output_ordinal': 1,
        'structure_decision': {
            'mode': 'retain', 'reason': '本章按因果线推进。',
            'declared_change_kinds': [],
        },
        'chapter_goal': {'intent': 'G_ADVANCE', 'fact_aliases': [B]},
        'open_hook_goal': {'intent': 'G_OPEN', 'fact_aliases': [A]},
        'end_hook_goal': {'intent': 'G_CLOSE', 'fact_aliases': [B]},
        'weight': 'CH_EVEN',
        'chapter_rewrite_brief': '先落入口事实，再完成章末状态。',
        'scenes': [
            {
                'weight': 'W_STRONG', 'purpose': '落下入口事实',
                'role': 'R_COLD_OPEN', 'delivery': 'D_SCENE',
                'transition': 'X_START',
                'land_fact_aliases': [A], 'context_fact_aliases': [],
                'turn_fact_alias': A,
                'entry_state_aliases': [], 'exit_state_aliases': [A],
            },
            {
                'weight': 'W_STRONG', 'purpose': '完成章末事实',
                'role': 'R_PAYOFF', 'delivery': 'D_SCENE',
                'transition': 'X_CONTINUE',
                'land_fact_aliases': [B], 'context_fact_aliases': [A],
                'turn_fact_alias': B,
                'entry_state_aliases': [], 'exit_state_aliases': [B],
            },
        ],
    }


def test_human_full_chapter_uses_the_canonical_compiler_and_cannot_change_weight():
    mapping = {
        'alias_algorithm': BLIND_ALIAS_ALGORITHM,
        'truth_sha256': 'c' * 64,
        'fact_aliases': {A: 'f01_001', B: 'f01_002'},
        'participant_aliases': {},
    }
    truth_chapter = {
        'chapter_ordinal': 1,
        'entry_state_aliases': [],
    }
    response = _complete_human_chapter()

    compiled = compile_blind_chapter_edit(
        response, mapping, truth_chapter=truth_chapter,
        expected_weight='CH_EVEN')

    assert [scene['candidate_scene_id'] for scene in compiled['scenes']] == [
        'c01s01', 'c01s02']
    assert compiled['scenes'][1]['entry_state_aliases'] == [A]
    assert 'candidate_scene_id' not in response['scenes'][0]

    changed_weight = copy.deepcopy(response)
    changed_weight['weight'] = 'CH_MAJOR'
    with pytest.raises(BlindPlanError, match='weight 与当前策略不一致'):
        compile_blind_chapter_edit(
            changed_weight, mapping, truth_chapter=truth_chapter,
            expected_weight='CH_EVEN')
