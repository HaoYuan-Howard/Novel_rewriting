"""范围审阅和依赖反例；不替代 Commander 的真实语义质量验收。"""
from __future__ import annotations

import copy
import json

import pytest

from contracts.artifacts import STAGE1_MAIN_TEXT, STAGE2_CHAPTER_BLUEPRINTS
from contracts.schema.dependencies import ScopeSelection
from contracts.schema.plot_review import PlotReviewResponse
from novel.consumption import compile_scope_inputs, invalidated_scopes
from novel.stages.stage2.plot_review import (
    CONTRACT, acceptance, apply_review, build_workpack,
)
from tests.test_plot_blueprint import SOURCE, blueprint

DIMENSIONS = ('completeness', 'attribution', 'causal_continuity',
              'epistemic_reveal', 'carry_state')


def inputs():
    return {
        STAGE1_MAIN_TEXT: [
            {'chapter_ordinal': n, 'text': SOURCE} for n in (1, 2)],
        STAGE2_CHAPTER_BLUEPRINTS: {
            'blueprints': [blueprint(n).model_dump(mode='json') for n in (1, 2)]},
    }


def response():
    return {
        'decision': 'accept', 'reason': '已对照原文完成本轮范围审阅',
        'coverage': {key: {'state': 'pass', 'issue_refs': []} for key in DIMENSIONS},
        'new_issues': [], 'dispositions': [],
    }


def submit(pack, value):
    return apply_review(
        pack, PlotReviewResponse.model_validate(value),
        expected_request_sha256=pack.request_sha256, actor='测试审阅者')


def issue_response():
    value = response()
    value['decision'] = 'revise'
    value['coverage']['attribution'] = {'state': 'issue', 'issue_refs': ['new:1']}
    value['new_issues'] = [{
        'issue_key': 'new:1', 'dimension': 'attribution',
        'problem': '必须保留施救的原因，不能倒推当时已经结婚。',
        'subjects': [{'kind': 'beat', 'ref': 'b01_01'}],
        'evidence_refs': [{'chapter_ordinal': 1, 'evidence_ref': 'e01_01'}],
    }]
    return value


def test_chapter_review_has_source_once_and_five_dimensions():
    pack = build_workpack('chapter:1', inputs())
    assert pack.material['sources'] == [{'chapter_ordinal': 1, 'text': SOURCE}]
    assert len(pack.material['blueprints']) == 1
    accepted = submit(pack, response())
    assert acceptance(accepted, pack) is not None
    value = response()
    value['coverage'].pop('carry_state')
    with pytest.raises(ValueError):
        PlotReviewResponse.model_validate(value)


def test_cross_review_requires_each_chapter_and_explicit_acceptance():
    data = inputs()
    with pytest.raises(ValueError, match='所有章节'):
        build_workpack('cross_chapter', data)
    records = []
    accepts = []
    for number in (1, 2):
        pack = build_workpack(f'chapter:{number}', data)
        record = submit(pack, response())
        records.append(record)
        accepts.append(acceptance(record, pack))
    cross = build_workpack('cross_chapter', data, chapter_acceptances=accepts)
    assert len(cross.material['sources']) == 2
    assert len(cross.material['blueprints']) == 2
    assert len(cross.material['chapter_acceptances']) == 2
    assert all('response' not in row for row in cross.material['chapter_acceptances'])
    assert acceptance(submit(cross, response()), cross) is not None


@pytest.mark.parametrize('state', ['unver', 'not_applicable'])
def test_not_applicable_and_unver_require_separate_reasons(state):
    value = response()
    value['coverage']['carry_state']['state'] = state
    with pytest.raises(ValueError, match='说明原因'):
        PlotReviewResponse.model_validate(value)
    value['coverage']['carry_state']['reason'] = '本章没有需要继续携带的状态'
    if state == 'unver':
        value['decision'] = 'unver'
    record = submit(build_workpack('chapter:1', inputs()), value)
    assert (record.response.decision == 'accept') == (state == 'not_applicable')


def test_empty_issues_cannot_erase_unresolved_issue_even_after_node_deleted():
    data = inputs()
    pack = build_workpack('chapter:1', data)
    first = submit(pack, issue_response())
    changed = copy.deepcopy(data)
    chapter = changed[STAGE2_CHAPTER_BLUEPRINTS]['blueprints'][0]
    chapter['beats'].pop(0)
    chapter['evidence_anchors'].pop(0)
    current = build_workpack('chapter:1', changed, issues=first.issues)
    assert len(current.material['issues']) == 1
    with pytest.raises(ValueError, match='所有未解决问题'):
        submit(current, response())
    assert current.issues[0].subjects[0].ref == 'b01_01'


def test_issue_resolution_does_not_invalidate_its_own_acceptance():
    data = inputs()
    first = submit(build_workpack('chapter:1', data), issue_response())
    pending = build_workpack('chapter:1', data, issues=first.issues)
    value = response()
    value['dispositions'] = [{
        'issue_id': first.issues[0].issue_id, 'state': 'resolved',
        'reason': '原文与完整节点均明确保留原因，已复核。'}]
    accepted = submit(pending, value)
    after = build_workpack('chapter:1', data, issues=accepted.issues)
    assert after.subject_sha256 == pending.subject_sha256
    assert after.request_sha256 != pending.request_sha256
    assert acceptance(accepted, after) is not None
    assert len(accepted.issues[0].history) == 2
    with pytest.raises(ValueError, match='材料已变化'):
        apply_review(after, PlotReviewResponse.model_validate(value),
                     expected_request_sha256=pending.request_sha256, actor='审阅者')


def test_new_negative_overrides_previous_acceptance_and_dimension_mismatch_rejected():
    pack = build_workpack('chapter:1', inputs())
    positive = submit(pack, response())
    negative = submit(pack, issue_response())
    assert acceptance(positive, pack) is not None
    assert acceptance(negative, pack) is None
    value = issue_response()
    value['coverage']['attribution'] = {'state': 'pass', 'issue_refs': []}
    value['coverage']['completeness'] = {'state': 'issue', 'issue_refs': ['new:1']}
    with pytest.raises(ValueError, match='同维度'):
        submit(pack, value)


def test_omission_can_cite_source_paragraph_without_existing_anchor():
    pack = build_workpack('chapter:1', inputs())
    value = issue_response()
    value['new_issues'][0]['subjects'] = [{'kind': 'chapter_boundary', 'ref': 'chapter:1'}]
    value['new_issues'][0]['evidence_refs'][0]['evidence_ref'] = 'p:2:2'
    assert submit(pack, value).issues[0].state == 'open'
    value['new_issues'][0]['evidence_refs'][0]['evidence_ref'] = 'p:99:99'
    with pytest.raises(ValueError):
        submit(pack, value)


def test_changed_second_chapter_does_not_expire_first_but_propagates_to_consumers():
    data = inputs()
    one = build_workpack('chapter:1', data)
    two = build_workpack('chapter:2', data)
    _, consumer = compile_scope_inputs(
        data, (), scope='stage4:chapter:2', contract='design-v4',
        depends_on_scopes=('chapter:2',))
    changed = copy.deepcopy(data)
    changed[STAGE2_CHAPTER_BLUEPRINTS]['blueprints'][1]['beats'][0]['content'] += '动机明确。'
    stale = invalidated_scopes(
        (one.manifest, two.manifest, consumer), changed,
        contracts={'chapter:1': CONTRACT, 'chapter:2': CONTRACT,
                   'stage4:chapter:2': 'design-v4'})
    assert stale == frozenset({'chapter:2', 'stage4:chapter:2'})


def test_membership_and_evidence_changes_are_real_dependencies():
    data = {'knowledge': {'items': [{'id': 'a', 'content': '已知'}]}}
    all_items = ScopeSelection(
        input_rel='knowledge', projection='collection_members',
        path=('items',), key_field='id')
    just_a = all_items.model_copy(update={'selected_ids': ('a',)})
    _, whole = compile_scope_inputs(data, (all_items,), scope='whole', contract='v1')
    _, partial = compile_scope_inputs(data, (just_a,), scope='partial', contract='v1')
    data['knowledge']['items'].append({'id': 'b', 'content': '新知识'})
    assert invalidated_scopes((whole, partial), data, contracts={
        'whole': 'v1', 'partial': 'v1'}) == frozenset({'whole'})
    data['knowledge']['items'][0]['content'] = '原有知识的内容变化'
    assert invalidated_scopes((whole, partial), data, contracts={
        'whole': 'v1', 'partial': 'v1'}) == frozenset({'whole', 'partial'})
