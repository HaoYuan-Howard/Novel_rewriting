# -*- coding: utf-8 -*-
"""审阅继承必须穿过真实 typed 审批、覆盖校验与模型传输边界。"""
from dataclasses import replace

import pytest

from contracts.schema.api import DesignReviewRequest, StructureApprovalRequest
from novel.errors import ArtifactUnreadable, HumanEditProtected
from novel.orchestrator import approvals, review_inheritance as inheritance, reviewer_tasks
from tests.test_design_review import (
    SID, _accept_all, _mixed_repair_subjects, _template, isolated as design_isolated,
)
from tests.test_structure_approval import isolated as structure_isolated, _request


def _unit(subject='a', *, decision='accept', reason='原裁决理由', source=None):
    return inheritance.DecisionUnit(subject, {'decision': decision, 'reason': reason}, source)


def _receipt(sha, *, units=None, valid=True):
    return inheritance.DecisionReceipt(sha, units or {'1': _unit()}, valid)


def test_new_and_inherited_rulings_cover_exactly_the_current_obligations():
    history = [_receipt('old')]
    inheritance.validate_submission(history, {'1': 'a', '2': 'b'}, {
        '1': _unit(source='old'), '2': _unit('b')})
    with pytest.raises(HumanEditProtected, match='完整覆盖'):
        inheritance.validate_submission(history, {'1': 'a', '2': 'b'}, {'1': _unit(source='old')})


@pytest.mark.parametrize('damage', ['evidence', 'negative', 'source', 'ruling', 'unknown'])
def test_inheritance_never_hides_changed_evidence_or_negative_history(damage):
    history = [_receipt('old')]
    current = {'1': 'changed' if damage == 'evidence' else 'a'}
    submitted = {'1': _unit(current['1'], source='missing' if damage == 'source' else 'old',
                            reason='新编的理由' if damage == 'ruling' else '原裁决理由')}
    if damage == 'negative':
        history.append(_receipt('new', units={'1': _unit(decision='request_replan')}))
    if damage == 'unknown':
        history.append(_receipt('broken', valid=False))
    with pytest.raises((HumanEditProtected, ArtifactUnreadable)):
        inheritance.validate_submission(history, current, submitted)


def test_inheritance_revalidates_the_original_source_through_multiple_closures():
    history = [_receipt('first'), _receipt('second', units={'1': _unit(source='first')}),
               _receipt('third', units={'1': _unit(source='second')})]
    assert inheritance.accepted_sources(history, {'1': 'a'})['1'].decision_sha256 == 'third'
    history[0] = replace(history[0], valid=False)
    with pytest.raises(ArtifactUnreadable, match='证据链'):
        inheritance.accepted_sources(history, {'1': 'a'})


def test_editing_a_rejected_subject_back_to_old_content_still_requires_review():
    history = [_receipt('old'), _receipt('negative', units={
        '1': _unit('changed', decision='request_replan')})]
    assert inheritance.accepted_sources(history, {'1': 'a'}) == {}
    with pytest.raises(ArtifactUnreadable, match='证据链'):
        inheritance.validate_history([*history, _receipt('forged-closure', units={
            '1': _unit(source='old')})])


def test_design_only_reviews_the_changed_chapter_and_keeps_original_sources(design_isolated):
    base, state = design_isolated
    state['subjects'] = _mixed_repair_subjects()
    first = approvals.approve_design(base, SID, _accept_all(_template(base))).design_review.current_decision
    current = state['subjects']
    second = current.chapters[1]
    changed = second.model_copy(update={'chapter': second.chapter.model_copy(update={
        'chapter_rewrite_brief': '第二章修改后的具体场景任务'})})
    state['subjects'] = current.model_copy(update={
        'candidate_sha256': '7' * 64, 'aggregate_subject_sha256': '8' * 64,
        'chapters': [current.chapters[0], changed], 'chapter_subject_sha256': {
            **current.chapter_subject_sha256, 2: approvals._decision_sha256(changed.model_dump(mode='json'))}})
    template = _template(base)
    assert template.strategy.source_decision_sha256 == first.decision_sha256
    assert template.chapters[0].source_decision_sha256 == first.decision_sha256
    assert template.chapters[1].source_decision_sha256 is None
    payload = reviewer_tasks._compact_design_payload(template)
    assert payload['chapters'][0]['decision'] == 'accept'
    payload['actor'], payload['general_reason'] = 'reviewer', '只复审变化义务'
    payload['chapters'][1].update(decision='accept', reason='已核对第二章新场景与证据')
    request = DesignReviewRequest.model_validate(payload)
    approved = approvals.approve_design(base, SID, request)
    decision = approved.design_review.current_decision
    assert decision.chapters[0].source_decision_sha256 == first.decision_sha256
    assert decision.chapters[0].reason == first.chapters[0].reason
    assert decision.chapters[1].source_decision_sha256 is None
    assert approvals.approve_design(base, SID, request) == approved
    old = state['record'].design_history[0]
    old.request.chapters[0].reason = '破坏已引用的原裁决'
    with pytest.raises(ArtifactUnreadable, match='证据链'):
        approvals._design_review_view(base, state['record'], current=state['subjects'])


def test_design_empty_pending_closes_without_new_reviewer(design_isolated):
    base, state = design_isolated
    first = approvals.approve_design(base, SID, _accept_all(_template(base))).design_review.current_decision
    state['subjects'] = state['subjects'].model_copy(update={
        'candidate_sha256': '7' * 64, 'aggregate_subject_sha256': '8' * 64})
    assert approvals.stage4_review_closure_available(base, SID, node='stage4.design')
    result = approvals.close_stage4_review(base, SID, node='stage4.design')
    assert result.design_review.current_decision.reviewer_kind == 'inherited'
    assert result.design_review.current_decision.strategy.source_decision_sha256 == first.decision_sha256
    assert not approvals.stage4_review_closure_available(base, SID, node='stage4.design')
    assert approvals.close_stage4_review(base, SID, node='stage4.design') == result


def test_structure_empty_pending_closes_and_preserves_typed_rulings(structure_isolated):
    base, _book, state = structure_isolated
    approvals.approve_structure(base, SID, _request('accept', 'accept'))
    source = state['record'].structure_decision.proof.decision_sha256
    state['subject'] = 'e' * 64
    assert approvals.stage4_review_closure_available(base, SID, node='stage4.plan')
    result = approvals.close_stage4_review(base, SID, node='stage4.plan')
    assert result.gate['s4']
    assert {row.source_decision_sha256 for row in result.structure.current_decision.chapters} == {source}
    assert approvals.close_stage4_review(base, SID, node='stage4.plan') == result


def test_structure_changed_blueprint_reopens_only_its_own_review(structure_isolated):
    base, _book, state = structure_isolated
    approvals.approve_structure(base, SID, _request('accept', 'accept'))
    state['subject'], state['chapter_subjects'][2] = 'e' * 64, 'f' * 64
    template = approvals.structure_template(base, SID, actor='reviewer', reason='复核新证据')
    assert template.chapters[0].source_decision_sha256 is not None
    assert template.chapters[1].source_decision_sha256 is None
    assert not approvals.stage4_review_closure_available(base, SID, node='stage4.plan')
    payload = template.model_dump(mode='json')
    payload['chapters'][1].update(decision='accept', reason='第二章新证据已核验')
    request = StructureApprovalRequest.model_validate(payload)
    result = approvals.approve_structure(base, SID, request)
    assert result.structure.current_decision.chapters[1].source_decision_sha256 is None


def test_transport_omits_inherited_material_but_binds_the_complete_receipt():
    inherited = {'chapter_ordinal': 1, 'historical_accept_reusable': True,
                 'source_decision_sha256': 'a' * 64, 'reading': {'text': '不应重发的整章'}}
    pending = {'chapter_ordinal': 2, 'historical_accept_reusable': False,
               'source_decision_sha256': None, 'reading': {'text': '必须完整阅读的新证据'}}
    pack = {'design': {'groups': [{'group': 'coverage', 'items': [inherited, pending]}]}}
    projected = reviewer_tasks._compact_stage4_review_materials(pack)['design']
    assert projected['groups'][0]['items'] == [pending]
    assert projected['review_projection']['inherited'] == [{
        'chapter_ordinal': 1, 'source_decision_sha256': 'a' * 64}]
    assert pack['design']['groups'][0]['items'] == [inherited, pending]
    frozen = {'actor': 'a', 'review_token': 'token', 'reason': '本轮', 'chapters': [
        {'output_ordinal': 1, 'decision': 'accept', 'reason': '原理由', 'source_decision_sha256': 'a' * 64}]}
    changed = {**frozen, 'chapters': [{**frozen['chapters'][0], 'reason': '被模型改写'}]}
    with pytest.raises(reviewer_tasks.ReviewerTaskError, match='继承行'):
        reviewer_tasks._validate_approval_binding('structure', changed, {'payload': frozen})
