# -*- coding: utf-8 -*-
"""Stage4 演进反例：策略身份、现役约束操作、Plot 端点与完整章编辑。

累计约束只经正式审批目录处理；原文关系只在 Blueprint 登记。披露读数不代替语义
裁决，本模块不恢复旧的关系数据库，也不把这些纯函数测试当作付费质量验收。
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from contracts.invariants import BLIND_ALIAS_ALGORITHM, DESIGN_REVIEW_CONTRACT
from contracts.schema.api import DesignActiveConstraint, DesignConstraintOperations, DesignRepairConstraint
from contracts.schema.plot import PlotBlueprint, PlotChapterArrangement
from contracts.schema.stage4 import PlanChapter
from novel.errors import HumanEditProtected
from novel.orchestrator import approvals
from novel.stages.stage4.design_evolution import (
    DesignEvolutionError, chapter_strategy_sha256, non_target_chapter_drift,
    replace_candidate_chapters, replan_affected_chapters, strategy_epoch_sha256,
)
from novel.stages.stage4.plot_design import (
    BlindPlanError, assemble_blind_candidate, build_blind_projection,
    compile_blind_chapter_edit,
)
from novel.stages.stage4.plot_placements import compile_placements, required_endpoints
from novel.stages.stage4.plot_measure import constraint_and_stage_readings
from tests.plot_fixtures import blueprint as fixture_blueprint, links
from tests.test_plot_blueprint import blueprint as two_beat_blueprint
from tests.test_plot_design import design_response, inputs, planner_characters, planner_main


A, B, C = 'b01_01', 'b01_02', 'b01_03'
X, Y = 'a_' + '1' * 16, 'a_' + '2' * 16


def _basis():
    return {'review_contract': DESIGN_REVIEW_CONTRACT, 'root_clean_sha1': 'a' * 40,
            'truth_sha256': 'b' * 64, 'alias_algorithm': BLIND_ALIAS_ALGORITHM}


def _strategy(text='按因果闭合重新组织全书。', *, first='CH_EVEN', second='CH_EVEN'):
    return {'reconstruction_strategy': text, 'chapter_weights': [
        {'chapter_ordinal': 1, 'weight': first}, {'chapter_ordinal': 2, 'weight': second}]}


def _blueprint(relations=()):
    raw = fixture_blueprint()['blueprints'][0]
    raw.update(states=[], carry_out_refs=[], constraints=[
        {'constraint_id': f'c01_{index:02d}', 'kind': kind,
         'before_beat_ref': before, 'after_beat_ref': after,
         'observer': {'char_id': 'char_002'} if kind == 'observes' else None,
         'evidence_refs': ['e01_01', 'e01_02', 'e01_03']}
        for index, (kind, before, after) in enumerate(relations, 1)])
    return PlotBlueprint.model_validate(raw)


def _arrangement(*scene_beats, bindings=(), earlier=None):
    slots = [f's{index:02d}' for index in range(1, len(scene_beats) + 1)]
    placements = []
    for beat, evidence in ((A, 'e01_01'), (B, 'e01_02'), (C, 'e01_03')):
        targets = [slot for slot, beats in zip(slots, scene_beats) if beat in beats]
        placements.append({'beat_ref': beat, 'deliveries': [
            {'scene_ref': slot, 'presentation_instruction': None if len(targets) == 1 else '承担本场呈现任务，联合完成父节点。',
             'evidence_refs': [evidence]} for slot in targets]})
    return PlotChapterArrangement.model_validate({
        'chapter_ordinal': 1,
        'scenes': [{'slot': slot, 'purpose': '完整呈现', 'role': 'setup', 'delivery': 'scene',
                    'transition': 'start' if i == 0 else 'earlier' if slot == earlier else 'continue',
                    'context_beat_refs': []} for i, slot in enumerate(slots)],
        'beat_placements': placements, 'anchor_bindings': list(bindings)})


def _binding(ref, endpoint, slot):
    return {'kind': 'constraint', 'ref': ref, 'endpoint': endpoint, 'scene_ref': slot}


def _plan(arrangement):
    raw = arrangement.model_dump(mode='json')
    def first_at(slot):
        return next(p['beat_ref'] for p in raw['beat_placements']
                    if any(d['scene_ref'] == slot for d in p['deliveries']))
    def goal(slot):
        beat = first_at(slot)
        return {'intent': 'establish', 'subject_refs': [{'kind': 'beat', 'ref': beat}],
                'anchor_beat_ref': beat, 'anchor_scene_ref': slot}
    return PlanChapter.model_validate({
        'output_ordinal': 1, 'from_source_ordinals': [1],
        'chapter_goal': goal(raw['scenes'][0]['slot']), 'open_hook_goal': goal(raw['scenes'][0]['slot']),
        'end_hook_goal': goal(raw['scenes'][-1]['slot']),
        'structure_decision': {'mode': 'retain', 'reason': '检查给定呈现安排', 'declared_change_kinds': []},
        'chapter_rewrite_brief': '完整呈现剧情',
        'scenes': [{**s, 'budget_chars': 100, 'turn_beat_ref': first_at(s['slot'])} for s in raw['scenes']],
        'beat_placements': raw['beat_placements'], 'anchor_bindings': raw['anchor_bindings'],
    }).model_dump(mode='json')


def _design_book(count=3):
    blueprints = [two_beat_blueprint(n).model_dump(mode='json') for n in range(1, count + 1)]
    truth, mapping = build_blind_projection(blueprints, root_clean_sha1='a' * 40,
        characters=planner_characters(), global_g=__import__('tests.plot_fixtures', fromlist=['GLOBAL']).GLOBAL,
        main_text=planner_main(count))
    responses, chapters = [], []
    for chapter in truth['chapters']:
        response = {**design_response({'chapter': chapter}),
                    'output_ordinal': chapter['chapter_ordinal'], 'weight': 'CH_EVEN'}
        responses.append(response)
        chapters.append(compile_blind_chapter_edit(response, mapping,
            truth_chapter=chapter, expected_weight='CH_EVEN'))
    candidate = assemble_blind_candidate({'reconstruction_strategy': '冻结策略',
        'chapter_weights': [{'chapter_ordinal': n, 'weight': 'CH_EVEN'} for n in range(1, count + 1)]},
        chapters, mapping, truth=truth)
    return candidate, truth, mapping, responses


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


def test_constraint_merge_is_canonical_and_content_addressed_without_copying_relation():
    one = DesignRepairConstraint(code='semantic_constraint', opaque_aliases=[X])
    two = DesignRepairConstraint(code='semantic_constraint', opaque_aliases=[Y])
    source = [one, two, one]
    canonical = approvals._canonical_design_constraints(source)
    assert canonical == approvals._canonical_design_constraints(list(reversed(source)))
    assert len(canonical) == 2 and source == [one, two, one]
    digest = lambda c: approvals._design_constraint_sha256('1' * 64, 1, c)
    assert len({digest(c) for c in canonical}) == 2
    assert all(set(c.model_dump()) == {'code', 'scene_refs', 'opaque_aliases'} for c in canonical)
    with pytest.raises(ValidationError, match='extra_forbidden'):
        DesignRepairConstraint.model_validate({**one.model_dump(), 'enforcement': 'hard'})


@pytest.mark.parametrize('operation', ['keep', 'withdraw', 'supersede'])
def test_constraint_operations_bind_epoch_chapter_and_exact_active_set(operation):
    epoch = '1' * 64
    old = DesignRepairConstraint(code='semantic_constraint', opaque_aliases=[X])
    new = DesignRepairConstraint(code='semantic_constraint', opaque_aliases=[Y])
    sha = approvals._design_constraint_sha256(epoch, 1, old)
    active = {sha: DesignActiveConstraint(constraint_sha256=sha, constraint=old,
                                         source_decision_sha256s=['a' * 64])}
    catalog = approvals._constraint_catalog_from_active(epoch, 1, active)
    ops = DesignConstraintOperations(expected_constraint_set_sha256=catalog.expected_constraint_set_sha256,
        items=[{'kind': operation, 'constraint_sha256': sha, 'reason': '显式处置真实历史约束',
                'replacements': [new] if operation == 'supersede' else []}])
    retained, replacement = approvals._apply_design_constraint_operations(active, ops, epoch=epoch, ordinal=1)
    assert retained == (active if operation == 'keep' else {})
    assert replacement == ([new] if operation == 'supersede' else [])
    assert active[sha].source_decision_sha256s == ['a' * 64]
    assert approvals._design_constraint_sha256('2' * 64, 1, old) != sha
    assert approvals._design_constraint_sha256(epoch, 2, old) != sha
    for wrong_epoch, wrong_chapter in [('2' * 64, 1), (epoch, 2)]:
        with pytest.raises(HumanEditProtected, match='集合已漂移'):
            approvals._apply_design_constraint_operations(active, ops,
                epoch=wrong_epoch, ordinal=wrong_chapter)
    with pytest.raises(HumanEditProtected, match='精确覆盖'):
        approvals._apply_design_constraint_operations(active, ops.model_copy(update={'items': []}),
                                                       epoch=epoch, ordinal=1)


def test_semantic_repair_can_only_reference_registered_constraint_or_endpoint():
    from contracts.schema.stage4 import BlindCandidateChapter
    from novel.stages.stage4.plot_design import compile_blind_chapter_design
    from contracts.schema.plot_planning import PlannerChapter
    from tests.test_plot_blueprint import chapter_response
    response = chapter_response()
    response['constraints'] = [{'constraint_key': 'c1', 'kind': 'response_to',
        'before_beat_ref': 'b1', 'after_beat_ref': 'b2', 'observer': None,
        'evidence': [{'start_para': 1, 'end_para': 2}]}]
    _bp, truth, mapping, public, private = inputs(response)
    raw = compile_blind_chapter_design(design_response(public), private, mapping,
        truth_chapter=truth['chapters'][0], chapter_weight='CH_EVEN')
    chapter = BlindCandidateChapter.model_validate(raw)
    planner = PlannerChapter.model_validate(truth['chapters'][0])
    def request(alias):
        constraint = DesignRepairConstraint(code='semantic_constraint', opaque_aliases=[alias])
        return SimpleNamespace(chapters=[SimpleNamespace(output_ordinal=1, constraints=[constraint],
            subject=SimpleNamespace(chapter=chapter, blind_truth=planner))])
    approvals._require_design_constraint_refs(request(planner.constraints[0].alias))
    for alias in (planner.beats[0].alias, 'a_' + 'f' * 16):
        with pytest.raises(HumanEditProtected, match='已登记约束|越界'):
            approvals._require_design_constraint_refs(request(alias))
    assert planner.constraints[0].kind == 'response_to'


@pytest.mark.parametrize('relations', [
    [('story_before', A, B), ('story_before', B, C), ('story_before', C, A)],
    [('response_to', A, B), ('story_before', B, A)],
    [('observes', A, B), ('story_before', B, A)],
    [('cause', A, B), ('response_to', B, C), ('observes', C, A)],
])
def test_registered_strict_order_cycles_are_rejected_before_planning(relations):
    with pytest.raises(ValidationError, match='严格顺序环'):
        _blueprint(relations)


@pytest.mark.parametrize('relations', [
    [('response_to', A, B), ('response_to', B, A)],
    [('response_to', A, B), ('observes', B, A)],
    [('same_scene', A, B), ('story_before', A, B)],
])
def test_non_strict_cycles_and_same_scene_do_not_invent_strict_time(relations):
    assert len(_blueprint(relations).constraints) == len(relations)


def test_same_scene_binds_actual_deliveries_without_first_visibility_equality():
    bp = _blueprint([('story_before', A, B), ('same_scene', A, B)])
    arrangement = _arrangement((A,), (A, B), (C,), bindings=[
        _binding('c01_01', 'before', 's01'), _binding('c01_02', 'before', 's02')])
    compiled = compile_placements(bp, arrangement)
    endpoints = {r.key(): r.scene_ref for r in compiled.bindings}
    assert endpoints['constraint', 'c01_02', 'before'] == endpoints['constraint', 'c01_02', 'after'] == 's02'
    assert endpoints['constraint', 'c01_01', 'before'] == 's01'
    assert compiled.completed_at[A] == 's02'
    missing = arrangement.model_copy(update={'anchor_bindings': []})
    with pytest.raises(ValueError, match='必须明确绑定'):
        compile_placements(bp, missing)


@pytest.mark.parametrize(('scenes', 'earlier', 'order'), [
    ([(B,), (A,), (C,)], 's02', 'reverse'),
    ([(A,), (B,), (C,)], 's02', 'forward'),
    ([(A, B), (C,)], None, 'same_scene'),
])
def test_story_time_is_not_certified_from_disclosure_or_earlier_transition(scenes, earlier, order):
    bp = _blueprint([('story_before', A, B)])
    arrangement = _arrangement(*scenes, earlier=earlier)
    compiled = compile_placements(bp, arrangement)
    reading = constraint_and_stage_readings(bp.model_dump(mode='json'), _plan(arrangement), knowledge_links=links())
    assert reading['causal'][0]['disclosure_order'] == order
    assert not hasattr(compiled, 'fully_verified')
    assert 'story_time_verified' not in reading and 'semantic_pass' not in reading
    assert bp.constraints[0].before_beat_ref == A, '呈现顺序和倒叙标记不得改写故事关系'


def test_unclosed_constraint_keeps_registered_identity_and_other_endpoints():
    bp = _blueprint([('story_before', A, B), ('same_scene', A, C)])
    before = bp.model_dump_json()
    endpoints = required_endpoints(bp, (), ())
    assert endpoints['constraint', 'c01_02', 'before'] == A
    assert endpoints['constraint', 'c01_02', 'after'] == C
    with pytest.raises(ValueError, match='必须同场约束没有闭合'):
        compile_placements(bp, _arrangement((A,), (B,), (C,)))
    assert bp.model_dump_json() == before
    valid = compile_placements(bp, _arrangement((A, C), (B,)))
    assert {r.ref for r in valid.bindings} == {'c01_01', 'c01_02'}


@pytest.mark.parametrize('reverse', [False, True])
def test_observer_and_knowledge_truth_are_not_certified_by_position(reverse):
    bp = _blueprint([('observes', A, B)])
    arrangement = _arrangement(*([(B,), (A,), (C,)] if reverse else [(A,), (B,), (C,)]))
    reading = constraint_and_stage_readings(bp.model_dump(mode='json'), _plan(arrangement), knowledge_links=links())
    assert reading['causal'][0]['kind'] == 'observes'
    assert reading['causal'][0]['disclosure_order'] == ('reverse' if reverse else 'forward')
    assert bp.constraints[0].observer.char_id == 'char_002'
    assert not any('verified' in key for key in reading)
    assert 'character_knowledge' not in reading, '排序不铸造人物知晓许可'


def test_current_readings_delegate_to_the_single_placement_compiler(monkeypatch):
    from novel.stages.stage4 import plot_placements
    calls = []
    real = plot_placements.compile_placements
    def capture(blueprint, arrangement, **kwargs):
        calls.append([c.constraint_id for c in blueprint.constraints])
        return real(blueprint, arrangement, **kwargs)
    monkeypatch.setattr(plot_placements, 'compile_placements', capture)
    bp = _blueprint([('response_to', A, B), ('same_scene', A, C)])
    result = constraint_and_stage_readings(bp.model_dump(mode='json'),
        _plan(_arrangement((A, C), (B,))), knowledge_links=links())
    assert calls == [['c01_01', 'c01_02']]
    assert result['causal'][0]['constraint_ref'] == 'c01_01'


def test_full_chapter_replace_is_exact_and_non_targets_are_byte_stable():
    previous, _truth, _mapping, _responses = _design_book()
    previous['_provenance'] = {'marker': '原来源账不由纯替换函数重签'}
    replacement = copy.deepcopy(previous['output_chapters'][1])
    replacement['chapter_rewrite_brief'] = '人审修订第二章的呈现组织'
    current = replace_candidate_chapters(previous, [replacement], target_chapters=[2])
    assert current['output_chapters'][1] == replacement
    assert current['output_chapters'][0] == previous['output_chapters'][0]
    assert current['output_chapters'][2] == previous['output_chapters'][2]
    assert current['_provenance'] == previous['_provenance']
    assert non_target_chapter_drift(previous, current, [2]) == ()
    assert previous['output_chapters'][1]['chapter_rewrite_brief'] != replacement['chapter_rewrite_brief']
    drifted = copy.deepcopy(current)
    drifted['output_chapters'][0]['chapter_rewrite_brief'] = '无关章节也被错误改动'
    assert non_target_chapter_drift(previous, drifted, [2]) == (1,)
    with pytest.raises(DesignEvolutionError, match='精确覆盖'):
        replace_candidate_chapters(previous, [replacement], target_chapters=[1, 2])


def test_human_full_chapter_uses_canonical_compiler_without_changing_weight():
    candidate, truth, mapping, responses = _design_book(2)
    response = responses[0]
    compiled = compile_blind_chapter_edit(response, mapping,
        truth_chapter=truth['chapters'][0], expected_weight='CH_EVEN')
    assert compiled == candidate['output_chapters'][0]
    assert [s['candidate_scene_id'] for s in compiled['scenes']] == ['c01s01', 'c01s02']
    assert 'candidate_scene_id' not in response['scenes'][0]
    changed = copy.deepcopy(response)
    changed['weight'] = 'CH_MAJOR'
    with pytest.raises(BlindPlanError, match='不能顺带改变策略权重'):
        compile_blind_chapter_edit(changed, mapping,
            truth_chapter=truth['chapters'][0], expected_weight='CH_EVEN')


@pytest.mark.parametrize('defect', ['missing_placement', 'unknown_subject', 'forged_scene_id'])
def test_human_full_chapter_cannot_bypass_closed_placement_identity(defect):
    _candidate, truth, mapping, responses = _design_book(2)
    response = copy.deepcopy(responses[0])
    if defect == 'missing_placement':
        response['beat_placements'].pop()
    elif defect == 'unknown_subject':
        response['beat_placements'][0]['beat_alias'] = 'a_' + 'f' * 16
    else:
        response['scenes'][0]['candidate_scene_id'] = 'forged'
    with pytest.raises((BlindPlanError, ValidationError)):
        compile_blind_chapter_edit(response, mapping,
            truth_chapter=truth['chapters'][0], expected_weight='CH_EVEN')
