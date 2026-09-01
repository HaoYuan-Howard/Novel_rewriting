# -*- coding: utf-8 -*-
"""Stage4 design review v3 的纯契约回归。

本文件不读审批盘、不起 provider，只钉住新旧决定的可读边界、typed
relation feedback、累计 repair scope 与完整章外部请求的注册形状。
"""
from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from contracts.artifacts import STAGE4_BLIND_CHAPTER_EDIT
from contracts.invariants import BLIND_ALIAS_ALGORITHM, DESIGN_REVIEW_CONTRACT
from contracts.schema import ARTIFACT_SPECS
from contracts.schema.api import (
    DesignAcceptedChapterConstraint,
    DesignChapterReview,
    DesignChapterRepairCapability,
    DesignChapterSubject,
    DesignRepairChapterScope,
    DesignRepairConstraint,
    DesignRepairScope,
    DesignReviewMaterialized,
    DesignReviewRepairCapability,
    DesignStrategyReplanConstraint,
    DesignStrategyReplanFeedback,
    DesignStrategyReview,
    DesignStrategySubject,
    DesignStructureEvidence,
)
from contracts.schema.stage4 import (
    BlindCandidateChapter, BlindChapterEditRequest,
)


A = 'a_' + '1' * 16
B = 'a_' + '2' * 16
OBSERVER = 'a_' + '3' * 16
SHA = 'd' * 64


def _chapter(*, ordinal: int = 1, candidate: bool = True) -> dict[str, object]:
    row: dict[str, object] = {
        'output_ordinal': ordinal,
        'structure_decision': {
            'mode': 'retain', 'reason': '按因果线直接推进。',
            'declared_change_kinds': [],
        },
        'chapter_goal': {'intent': 'G_ADVANCE', 'fact_aliases': [B]},
        'open_hook_goal': {'intent': 'G_OPEN', 'fact_aliases': [A]},
        'end_hook_goal': {'intent': 'G_CLOSE', 'fact_aliases': [B]},
        'weight': 'CH_EVEN',
        'chapter_rewrite_brief': '先落入口事实，再完成回应。',
        'scenes': [{
            'weight': 'W_STRONG', 'purpose': '落入口事实',
            'role': 'R_COLD_OPEN', 'delivery': 'D_SCENE',
            'transition': 'X_START',
            'land_fact_aliases': [A, B], 'context_fact_aliases': [],
            'turn_fact_alias': B,
            'entry_state_aliases': [], 'exit_state_aliases': [B],
        }],
    }
    if candidate:
        row['scenes'][0]['candidate_scene_id'] = f'c{ordinal:02d}s01'  # type: ignore[index]
    return row


def _truth(*, ordinal: int = 1) -> dict[str, object]:
    return {
        'chapter_ordinal': ordinal,
        'chapter_function': '建立回应关系',
        'facts': [
            {'fact_alias': A, 'claim': '她敲门。', 'kind': 'event',
             'obligation': 'land', 'participant_aliases': [OBSERVER]},
            {'fact_alias': B, 'claim': '对方回应。', 'kind': 'event',
             'obligation': 'land', 'participant_aliases': [OBSERVER]},
        ],
        'causal_edges': [],
        'narrative_relations': [],
        'entry_state_aliases': [],
        'ending_state_aliases': [],
    }


def _evidence() -> DesignStructureEvidence:
    return DesignStructureEvidence(
        state='ok', declared_change_kinds=[], computed_change_kinds=[],
        realized_declared=[], unrealized_declared=[], undeclared_actual=[],
        readings={'mapping_state': 'ok', 'land_facts': 2},
    )


def _v3_subject() -> DesignChapterSubject:
    return DesignChapterSubject(
        strategy_epoch_sha256='1' * 64,
        chapter_strategy_sha256='2' * 64,
        blind_truth=_truth(),
        chapter=BlindCandidateChapter.model_validate(_chapter()),
        structure_evidence=_evidence(),
    )


def _relation_constraint() -> DesignRepairConstraint:
    return DesignRepairConstraint.model_validate({
        'code': 'narrative_relation',
        'effect': {
            'kind': 'response_to', 'enforcement': 'hard',
            'stimulus_alias': A, 'response_alias': B,
        },
    })


def _v3_capability() -> DesignReviewRepairCapability:
    return DesignReviewRepairCapability(
        agent_repairable=True,
        agent_reason_code='same_strategy_epoch_ready',
        full_chapter_editable=True,
        full_chapter_edit_reason_code='current_typed_decision',
        chapters=[DesignChapterRepairCapability(
            output_ordinal=1, committed_rounds=2,
            agent_repair_available=True,
            full_chapter_edit_available=True,
        )],
    )


def test_design_review_contract_is_v3_and_chapter_subject_identity_is_exclusive():
    assert DESIGN_REVIEW_CONTRACT == 'stage4-design-review-v3'
    assert _v3_subject().strategy_subject_sha256 is None

    legacy = DesignChapterSubject(
        strategy_subject_sha256='3' * 64,
        blind_truth=_truth(), chapter=_chapter(), structure_evidence=_evidence())
    assert legacy.strategy_epoch_sha256 is None

    with pytest.raises(ValidationError, match='必须且只能'):
        DesignChapterSubject(
            strategy_subject_sha256='3' * 64,
            strategy_epoch_sha256='1' * 64,
            chapter_strategy_sha256='2' * 64,
            blind_truth=_truth(), chapter=_chapter(), structure_evidence=_evidence())


def test_v2_complete_decision_shape_remains_readable_but_v3_uses_new_identities():
    legacy_strategy = DesignStrategySubject.model_validate({
        'basis': {
            'review_contract': 'stage4-design-review-v2',
            'root_clean_sha1': 'c' * 40,
            'truth_sha256': 'd' * 64,
            'alias_algorithm': BLIND_ALIAS_ALGORITHM,
        },
        'reconstruction_strategy': '历史策略。',
        'chapter_weights': [{'chapter_ordinal': 1, 'weight': 'CH_EVEN'}],
    })
    legacy_subject = DesignChapterSubject(
        strategy_subject_sha256='3' * 64,
        blind_truth=_truth(), chapter=_chapter(), structure_evidence=_evidence())
    legacy_capability = DesignReviewRepairCapability.model_validate({
        'repairable': True, 'reason_code': 'ready',
        'chapters': [{
            'output_ordinal': 1, 'committed_rounds': 0,
            'request_repair_available': True,
        }],
    })
    legacy = DesignReviewMaterialized(
        actor='reviewer', reviewer_kind='human', review_run_id='legacy-v2',
        general_reason='历史决定保留可读。',
        candidate_sha256='4' * 64, aggregate_subject_sha256='5' * 64,
        repair_capability=legacy_capability,
        strategy={
            'subject': legacy_strategy, 'subject_sha256': '3' * 64,
            'placeholder': False, 'decision': 'accept', 'reason': '接受。',
        },
        chapters=[DesignChapterReview(
            output_ordinal=1, subject=legacy_subject,
            subject_sha256='6' * 64, placeholder=False,
            decision='accept', reason='接受。', constraints=[],
        )],
    )
    assert legacy.strategy.subject.basis.review_contract.endswith('-v2')

    current_strategy = legacy_strategy.model_copy(update={
        'basis': legacy_strategy.basis.model_copy(update={
            'review_contract': DESIGN_REVIEW_CONTRACT,
        }),
    })
    current = DesignReviewMaterialized(
        actor='reviewer', reviewer_kind='human', review_run_id='current-v3',
        general_reason='当前决定使用章级策略身份。',
        candidate_sha256='4' * 64, aggregate_subject_sha256='5' * 64,
        repair_capability=_v3_capability(),
        strategy={
            'subject': current_strategy, 'subject_sha256': '3' * 64,
            'placeholder': False, 'decision': 'accept', 'reason': '接受。',
        },
        chapters=[DesignChapterReview(
            output_ordinal=1, subject=_v3_subject(),
            subject_sha256='6' * 64, placeholder=False,
            decision='accept', reason='接受。', constraints=[],
        )],
    )
    assert current.chapters[0].subject.chapter_strategy_sha256 == '2' * 64


def test_narrative_relation_repair_effect_is_typed_and_always_hard():
    constraint = _relation_constraint()
    assert constraint.effect is not None
    assert constraint.effect.enforcement == 'hard'

    bad = constraint.model_dump(mode='json')
    bad['effect']['enforcement'] = 'review'  # type: ignore[index]
    with pytest.raises(ValidationError):
        DesignRepairConstraint.model_validate(bad)
    with pytest.raises(ValidationError, match='typed HARD effect'):
        DesignRepairConstraint(code='narrative_relation')
    with pytest.raises(ValidationError, match='只存 effect'):
        DesignRepairConstraint.model_validate({
            **constraint.model_dump(mode='json'), 'opaque_aliases': [A],
        })


def test_repair_capability_separates_agent_from_full_chapter_edit_and_reads_v2():
    current = _v3_capability()
    assert current.is_v3
    assert current.chapters[0].committed_rounds == 2
    assert current.chapters[0].agent_repair_available is True

    legacy = DesignReviewRepairCapability.model_validate({
        'repairable': True, 'reason_code': 'ready',
        'chapters': [{
            'output_ordinal': 1, 'committed_rounds': 1,
            'request_repair_available': True,
        }],
    })
    assert not legacy.is_v3
    with pytest.raises(ValidationError, match='必须且只能'):
        DesignReviewRepairCapability.model_validate({
            **legacy.model_dump(mode='json'),
            'agent_repairable': True,
            'agent_reason_code': 'ready',
            'full_chapter_editable': True,
            'full_chapter_edit_reason_code': 'ready',
        })


def test_targeted_repair_scope_carries_new_accumulated_set_and_strategy_epoch():
    relation = _relation_constraint()
    row = DesignRepairChapterScope(
        output_ordinal=1, subject_sha256='4' * 64,
        new_constraints=[relation], accumulated_constraints=[relation],
        constraint_set_sha256='5' * 64,
    )
    scope = DesignRepairScope(
        candidate_sha256='6' * 64,
        aggregate_subject_sha256='7' * 64,
        decision_sha256='8' * 64,
        repair_capability=_v3_capability(),
        strategy_epoch_sha256='1' * 64,
        strategy_replan=False,
        target_chapters=[1], chapters=[row],
    )
    assert scope.chapters[0].accumulated_constraints == [relation]

    missing_history = row.model_dump(mode='json')
    missing_history['accumulated_constraints'] = []
    with pytest.raises(ValidationError, match='new/accumulated/constraint_set'):
        DesignRepairChapterScope.model_validate(missing_history)


def test_strategy_replan_has_typed_cumulative_feedback_and_accepted_constraints():
    constraint = DesignStrategyReplanConstraint(
        kind='replace_reconstruction_strategy',
        subject_sha256='9' * 64,
        chapter_ordinals=[],
    )
    feedback = DesignStrategyReplanFeedback(
        new_constraints=[constraint], accumulated_constraints=[constraint],
        constraint_set_sha256='a' * 64,
        accepted_chapters=[DesignAcceptedChapterConstraint(
            output_ordinal=1, subject_sha256='b' * 64,
            chapter_strategy_sha256='2' * 64,
        )],
        chapter_failures=[],
    )
    scope = DesignRepairScope(
        candidate_sha256='6' * 64,
        aggregate_subject_sha256='7' * 64,
        decision_sha256='8' * 64,
        repair_capability=_v3_capability(),
        strategy_epoch_sha256='1' * 64,
        strategy_replan=True,
        replan_feedback=feedback,
        target_chapters=[], chapters=[],
    )
    assert scope.replan_feedback == feedback

    strategy = DesignStrategySubject.model_validate({
        'basis': {
            'review_contract': DESIGN_REVIEW_CONTRACT,
            'root_clean_sha1': 'c' * 40,
            'truth_sha256': 'd' * 64,
            'alias_algorithm': BLIND_ALIAS_ALGORITHM,
        },
        'reconstruction_strategy': '按因果闭合重组。',
        'chapter_weights': [{'chapter_ordinal': 1, 'weight': 'CH_EVEN'}],
    })
    with pytest.raises(ValidationError, match='typed strategy constraint'):
        DesignStrategyReview(
            subject=strategy, subject_sha256='9' * 64,
            placeholder=False, decision='request_replan', reason='策略需重做。')


def test_complete_chapter_edit_request_is_registered_v1_and_ordinal_bound():
    payload = {
        'actor': 'reviewer', 'reason': '修正动作与台词回应关系。',
        'review_token': 's4er1_' + '1' * 64,
        'output_ordinal': 1,
        'chapter': _chapter(candidate=False),
    }
    request = BlindChapterEditRequest.model_validate(payload)
    spec = ARTIFACT_SPECS[STAGE4_BLIND_CHAPTER_EDIT]
    assert spec.model is BlindChapterEditRequest
    assert spec.current_version == 1
    assert request.chapter.output_ordinal == 1
    assert set(request.model_dump()) == {
        'actor', 'reason', 'review_token', 'output_ordinal', 'chapter'}

    drifted = copy.deepcopy(payload)
    drifted['output_ordinal'] = 2
    with pytest.raises(ValidationError, match='output_ordinal'):
        BlindChapterEditRequest.model_validate(drifted)
