"""章级 B 机械基线：等价舞台、真实歧义、完整绑定与关闭式解答。"""
from __future__ import annotations

import copy

import pytest

from novel.stages.stage4.plot_binding import (
    BlindPlanError, apply_chapter_resolutions, build_evidence_catalog,
    build_mechanical_baseline, construct_feasible_binding_witness,
)
from tests.test_plot_binding import candidate_book
from tests.test_plot_blueprint import chapter_response


def _ambiguous():
    response = chapter_response()
    _blueprints, candidate = candidate_book(response, split=True)
    placement = next(p for p in candidate['output_chapters'][0]['beat_placements']
                     if len(p['deliveries']) > 1)
    ref = candidate['subject_aliases'][placement['beat_alias']]['ref']
    index = int(ref.rsplit('_', 1)[1]) - 1
    # 分场节点有两段证据和不等价舞台，分别形成呈现证据与舞台选择。
    beat = response['beats'][index]
    other = 2 if index == 0 else 1
    beat['evidence'].append({'start_para': other, 'end_para': other})
    second = copy.deepcopy(beat['source_contexts'][0])
    second['place'] = '门外'
    beat['source_contexts'].append(second)
    return candidate_book(response, split=True)


def test_identical_signature_stages_are_canonically_resolved():
    response = chapter_response()
    context = copy.deepcopy(response['beats'][0]['source_contexts'][0])
    context['evidence'] = [{'start_para': 2, 'end_para': 2}]
    response['beats'][0]['evidence'].append({'start_para': 2, 'end_para': 2})
    response['beats'][0]['source_contexts'].append(context)
    blueprints, candidate = candidate_book(response)
    baseline = build_mechanical_baseline(candidate, blueprints)
    assert baseline.fully_mechanical
    rows = baseline.chapter(1).binding_rows()
    refs = [delivery['source_context_refs'] for scene in rows
            for delivery in scene['deliveries'] if delivery['beat_ref'] == 'b01_01']
    # 证据来源不同、舞台语义相同：两条来源都保留，无需模型重复选择。
    assert len(refs) == 1 and len(refs[0]) == 2


def test_signature_conflict_and_material_ambiguities_are_stable():
    blueprints, candidate = _ambiguous()
    baseline = build_mechanical_baseline(candidate, blueprints)
    other = build_mechanical_baseline(copy.deepcopy(candidate), copy.deepcopy(blueprints))
    assert baseline == other
    ambiguities = baseline.chapter(1).ambiguities
    assert sorted(a.kind for a in ambiguities) == [
        'delivery_evidence', 'delivery_evidence',
        'source_context_selection', 'source_context_selection',
    ]
    assert len({a.ambiguity_id for a in ambiguities}) == 4
    assert all(len(a.candidates) == 2 for a in ambiguities)
    assert baseline.chapter(1).binding_rows() is None


def test_clean_book_baseline_matches_the_canonical_bound_response():
    blueprints, candidate = candidate_book(split=True)
    baseline = build_mechanical_baseline(candidate, blueprints)
    bound = construct_feasible_binding_witness(candidate, build_evidence_catalog(blueprints))
    assert baseline.fully_mechanical
    assert baseline.binding_chapters() == bound['chapters']


def test_apply_chapter_resolutions_closes_ambiguities():
    blueprints, candidate = _ambiguous()
    chapter = build_mechanical_baseline(candidate, blueprints).chapter(1)
    resolutions = [{'ambiguity_id': a.ambiguity_id, 'selected_refs': [a.candidates[0]]}
                   for a in chapter.ambiguities]
    result = apply_chapter_resolutions(chapter, resolutions)
    assert len(result) == len(chapter.scenes)
    assert all(d['evidence_refs'] for s in result for d in s['deliveries'])
    with pytest.raises(BlindPlanError):
        apply_chapter_resolutions(chapter, resolutions[:-1])
    with pytest.raises(BlindPlanError):
        apply_chapter_resolutions(chapter, resolutions + resolutions[:1])
    wrong = copy.deepcopy(resolutions)
    wrong[0]['selected_refs'] = ['foreign_reference']
    with pytest.raises(BlindPlanError):
        apply_chapter_resolutions(chapter, wrong)
