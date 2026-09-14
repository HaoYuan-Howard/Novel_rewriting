# -*- coding: utf-8 -*-
"""Stage4 design review v4 与 v3 历史兼容的纯契约回归。

本文件不读审批盘、不起 provider，只钉住新旧决定的可读边界、typed
relation feedback、累计 repair scope 与完整章外部请求的注册形状。
"""
from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from contracts.artifacts import STAGE4_BLIND_CHAPTER_EDIT
from contracts.invariants import (
    BLIND_ALIAS_ALGORITHM, DESIGN_REVIEW_CONTRACT,
    LEGACY_V3_DESIGN_REVIEW_CONTRACT,
)
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
CONSTRAINT = 'a_' + '4' * 16


def _chapter(*, ordinal: int = 1, candidate: bool = True) -> dict[str, object]:
    row: dict[str, object] = {
        'output_ordinal': ordinal,
        'structure_decision': {
            'mode': 'retain', 'reason': '按因果线直接推进。',
            'declared_change_kinds': [],
        },
        'chapter_goal': {'intent': 'G_ADVANCE', 'subject_aliases': [B], 'anchor_beat_alias': B, 'anchor_scene_ref': 's01'},
        'open_hook_goal': {'intent': 'G_OPEN', 'subject_aliases': [A], 'anchor_beat_alias': A, 'anchor_scene_ref': 's01'},
        'end_hook_goal': {'intent': 'G_CLOSE', 'subject_aliases': [B], 'anchor_beat_alias': B, 'anchor_scene_ref': 's01'},
        'weight': 'CH_EVEN',
        'chapter_rewrite_brief': '先落入口事实，再完成回应。',
        'scenes': [{
            'scene_ref': 's01', 'weight': 'W_STRONG', 'purpose': '呈现敲门和回应',
            'role': 'R_COLD_OPEN', 'delivery': 'D_SCENE',
            'transition': 'X_START',
            'context_beat_aliases': [], 'turn_beat_alias': B,
        }],
        'beat_placements': [{'beat_alias': alias, 'deliveries': [
            {'scene_ref': 's01', 'presentation_instruction': None}]} for alias in (A, B)],
        'anchor_bindings': [],
    }
    if candidate:
        row['scenes'][0]['candidate_scene_id'] = f'c{ordinal:02d}s01'  # type: ignore[index]
    return row


def _truth(*, ordinal: int = 1) -> dict[str, object]:
    return {
        'chapter_ordinal': ordinal,
        'chapter_function': '建立回应关系',
        'characters': [{'alias': OBSERVER,
            'surfaces': ['来访者'], 'role': '敲门求见的人'}],
        'beats': [
            {'alias': A, 'content': '她敲门。', 'participant_aliases': [OBSERVER]},
            {'alias': B, 'content': '对方回应。', 'participant_aliases': [OBSERVER]},
        ],
        'states': [], 'inherited_states': [], 'background_beats': [],
        'constraints': [], 'endpoints': [],
        'entry_state_aliases': [], 'carry_out_aliases': [], 'global_subjects': [],
    }


def _evidence() -> DesignStructureEvidence:
    return DesignStructureEvidence(
        state='ok', declared_change_kinds=[], computed_change_kinds=[],
        realized_declared=[], unrealized_declared=[], undeclared_actual=[],
        readings={'mapping_state': 'ok', 'beats': 2},
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
    return DesignRepairConstraint(code='semantic_constraint', opaque_aliases=[CONSTRAINT])


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


def test_design_review_contract_is_v6_and_chapter_subject_identity_is_exclusive():
    assert DESIGN_REVIEW_CONTRACT == 'stage4-design-review-v6'
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


def test_v2_and_v3_decisions_remain_readable_but_v4_uses_split_identities():
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
        actor='reviewer', reviewer_kind='human', review_run_id='current-v4',
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

    # v4 只改变 currentness / 同主体批准语义；已经持久化的 v3 使用的是
    # 同一 split-capability + strategy-epoch 家族，不能被误送进 v1/v2 decoder。
    old_v3 = current.model_dump(mode='json')
    old_v3['strategy']['subject']['basis']['review_contract'] = \
        LEGACY_V3_DESIGN_REVIEW_CONTRACT
    restored_v3 = DesignReviewMaterialized.model_validate(old_v3)
    assert restored_v3.repair_capability.is_v3
    assert restored_v3.chapters[0].subject.strategy_epoch_sha256 == '1' * 64


def test_semantic_constraint_references_existing_subject_without_creating_relation():
    constraint = _relation_constraint()
    assert 'effect' not in constraint.model_dump()
    assert constraint.opaque_aliases == [CONSTRAINT]
    # 旧边式指令会在 Stage4 制造新的语义主体，当前请求关闭式拒绝。
    for enforcement in ('hard', 'review'):
        with pytest.raises(ValidationError):
            DesignRepairConstraint.model_validate({
                'code': 'narrative_relation', 'effect': {
                    'kind': 'response_to', 'enforcement': enforcement,
                    'stimulus_alias': A, 'response_alias': B}})
    with pytest.raises(ValidationError):
        DesignRepairConstraint(code='semantic_constraint')
    with pytest.raises(ValidationError):
        DesignRepairConstraint.model_validate({**constraint.model_dump(mode='json'),
            'effect': {'kind': 'response_to', 'stimulus_alias': A, 'response_alias': B}})


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


def test_complete_chapter_edit_request_is_registered_v3_and_ordinal_bound():
    payload = {
        'actor': 'reviewer', 'reason': '修正动作与台词回应关系。',
        'review_token': 's4er1_' + '1' * 64,
        'output_ordinal': 1,
        'chapter': _chapter(candidate=False),
    }
    request = BlindChapterEditRequest.model_validate(payload)
    spec = ARTIFACT_SPECS[STAGE4_BLIND_CHAPTER_EDIT]
    assert spec.model is BlindChapterEditRequest
    assert spec.current_version == 3
    assert request.chapter.output_ordinal == 1
    assert set(request.model_dump()) == {
        'actor', 'reason', 'review_token', 'output_ordinal', 'chapter'}

    drifted = copy.deepcopy(payload)
    drifted['output_ordinal'] = 2
    with pytest.raises(ValidationError, match='output_ordinal'):
        BlindChapterEditRequest.model_validate(drifted)
