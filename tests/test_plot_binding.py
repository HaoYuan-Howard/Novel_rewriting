"""章级 B 的呈现绑定：共享证据合法，A 的安排不可改，费用不被分场放大。"""
from __future__ import annotations

import copy
import json

import pytest

from contracts.invariants import RESPONSE_CHAPTER_WEIGHTS
from contracts.schema.stage4 import PlanChapter
from novel.stages.stage4.plot_binding import (
    apply_chapter_resolutions, build_evidence_catalog, build_mechanical_baseline,
    construct_feasible_binding_witness, merge_evidence_bindings, BlindPlanError,
)
from novel.stages.stage4.plot_design import (
    build_blind_projection, build_blind_chapter_projection,
    compile_blind_chapter_design, assemble_blind_candidate,
)
from novel.stages.stage4.measure import adopt_plan_response
from novel.stages.stage4.beats import beat_index
from tests.test_plot_blueprint import blueprint, chapter_response, SOURCE
from tests.test_plot_design import design_response, planner_characters


def candidate_book(response=None, *, split=False):
    blueprints = {'blueprints': [blueprint(response=response).model_dump(mode='json'),
                                 blueprint(2).model_dump(mode='json')]}
    truth, mapping = build_blind_projection(blueprints, root_clean_sha1='a' * 40,
                                           global_g=planner_characters())
    rows = []
    for ordinal in (1, 2):
        public, private = build_blind_chapter_projection(
            truth, mapping, chapter_ordinal=ordinal, root_clean_sha1='a' * 40)
        raw = design_response(public, split=split)
        # 此 fixture 不含需要在分场节点显式绑定的状态/知识事件。
        rows.append(compile_blind_chapter_design(raw, private, mapping,
            truth_chapter=truth['chapters'][ordinal - 1],
            chapter_weight=next(iter(RESPONSE_CHAPTER_WEIGHTS))))
    candidate = assemble_blind_candidate({
        'reconstruction_strategy': '逐章推进',
        'chapter_weights': [{'chapter_ordinal': n, 'weight': next(iter(RESPONSE_CHAPTER_WEIGHTS))}
                            for n in (1, 2)],
    }, rows, mapping, truth=truth)
    return blueprints, candidate


def test_shared_evidence_split_compiles_formal_plan_without_upstream_edit():
    blueprints, candidate = candidate_book(split=True)
    original = copy.deepcopy(blueprints)
    catalog = build_evidence_catalog(blueprints)
    baseline = build_mechanical_baseline(candidate, blueprints)
    assert baseline.fully_mechanical
    binding = construct_feasible_binding_witness(candidate, catalog)
    compiled = merge_evidence_bindings(candidate, binding, catalog)
    bidx = beat_index(blueprints, [{'chapter_ordinal': n, 'text': SOURCE} for n in (1, 2)])
    adopt_plan_response(compiled, blueprints, bidx)
    chapters = [PlanChapter.model_validate(row) for row in compiled['output_chapters']]
    assert sum(s.budget_chars for c in chapters for s in c.scenes) == 2 * len(SOURCE)
    assert all(sum(len(p.deliveries) for p in c.beat_placements) == 3 for c in chapters)
    assert blueprints == original
    assert all('land_fact_refs' not in s.model_dump() for c in chapters for s in c.scenes)
    assert all('claim' not in d.model_dump() for c in chapters
               for p in c.beat_placements for d in p.deliveries)


def test_binding_cannot_move_remove_or_forge_delivery():
    blueprints, candidate = candidate_book(split=True)
    catalog = build_evidence_catalog(blueprints)
    binding = construct_feasible_binding_witness(candidate, catalog)
    missing = copy.deepcopy(binding)
    missing['chapters'][0]['scenes'] = missing['chapters'][0]['scenes'][1:]
    with pytest.raises(BlindPlanError, match='全部呈现'):
        merge_evidence_bindings(candidate, missing, catalog)
    forged = copy.deepcopy(binding)
    forged['chapters'][0]['scenes'][0]['deliveries'][0]['evidence_refs'] = ['e02_01']
    with pytest.raises(BlindPlanError, match='证据不属于父节点'):
        merge_evidence_bindings(candidate, forged, catalog)
    altered = copy.deepcopy(binding)
    altered['chapters'][0]['scenes'][0]['deliveries'][0]['presentation_instruction'] = '改变剧情'
    with pytest.raises(ValueError, match='extra_forbidden'):
        merge_evidence_bindings(candidate, altered, catalog)


def test_unknown_and_empty_presence_are_not_equivalent_stages():
    response = chapter_response()
    first = response['beats'][0]['source_contexts'][0]
    first['present_char_ids'] = []
    unknown = copy.deepcopy(first)
    unknown['present_char_ids'] = None
    response['beats'][0]['source_contexts'].append(unknown)
    blueprints, candidate = candidate_book(response)
    baseline = build_mechanical_baseline(candidate, blueprints)
    chapter = baseline.chapter(1)
    assert not chapter.mechanical and len(chapter.ambiguities) == 1
    ambiguity = chapter.ambiguities[0]
    assert ambiguity.kind == 'source_context_selection'
    assert len(ambiguity.candidates) == 2
    selected = [{'ambiguity_id': ambiguity.ambiguity_id,
                 'selected_refs': [ambiguity.candidates[-1]]}]
    rows = apply_chapter_resolutions(chapter, selected)
    refs = [d['source_context_refs'] for s in rows for d in s['deliveries']
            if d['beat_ref'] == ambiguity.beat_ref]
    assert refs == [[ambiguity.candidates[-1]]]
    selected[0]['selected_refs'] = ['scx_unknown']
    with pytest.raises(BlindPlanError, match='超出本章'):
        apply_chapter_resolutions(chapter, selected)


def test_budget_is_not_multiplied_by_shared_or_repeated_evidence():
    totals = []
    for split in (False, True):
        blueprints, candidate = candidate_book(split=split)
        catalog = build_evidence_catalog(blueprints)
        plan = merge_evidence_bindings(candidate,
            construct_feasible_binding_witness(candidate, catalog), catalog)
        index = beat_index(blueprints, [{'chapter_ordinal': n, 'text': SOURCE} for n in (1, 2)])
        adopt_plan_response(plan, blueprints, index)
        totals.append([sum(s['budget_chars'] for s in c['scenes']) for c in plan['output_chapters']])
    assert totals[0] == totals[1] == [len(SOURCE), len(SOURCE)]
