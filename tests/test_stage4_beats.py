"""完整剧情呈现编译的反例；稀疏状态使用显式端点，不重建旧逐事实进出状态表。"""
from __future__ import annotations

import copy
import itertools
import math

import pytest
from pydantic import ValidationError

from contracts.invariants import CHAPTER_WEIGHT_RATIO, PLAN_BUDGET_BAND
from contracts.schema.plot import (
    PlotBlueprint, PlotChapterArrangement, PlotChapterResponse,
)
from contracts.schema.stage4 import BlindSceneResponse, PlanChapter
from novel.stages.stage2.blueprint.compile import compile_chapter
from novel.stages.stage4 import checks, plot_checks
from novel.stages.stage4.beats import beat_index
from novel.stages.stage4.measure import (
    _bounded_round_shares, adopt_plan_response, identity_plan,
    semantic_contract_of, source_chars_table,
)
from novel.stages.stage4.plot_measure import structure_metrics_of
from novel.stages.stage4.plot_placements import compile_placements
from tests import plot_fixtures as fixture
from tests.test_plot_blueprint import blueprint, arrangement, chapter_response


def _compile(raw, bp=None, **kwargs):
    return compile_placements(bp or blueprint(), PlotChapterArrangement.model_validate(raw), **kwargs)


def _state_blueprint():
    response = chapter_response()
    response['states'] = [{
        'state_key': 'st1', 'content': '娘子决定离开。', 'global_ref': None,
        'modality': 'world', 'holder': None,
        'established_at': {'kind': 'at_beat', 'beat_ref': 'b2'},
        'ended_at': None, 'supersedes_state_refs': [],
        'evidence': [{'start_para': 2, 'end_para': 2}]}]
    response['carry_out_refs'] = ['st1']
    return blueprint(response=response)


def test_beat_index_keeps_unique_content_and_shared_evidence_without_atomic_claims():
    bp = fixture.blueprint()
    index = beat_index(bp, fixture.MAIN)
    assert len(index) == len(bp['blueprints'][0]['beats']) == 3
    for beat in bp['blueprints'][0]['beats']:
        row = index[beat['beat_id']]
        assert row['content'] == beat['content']
        assert set(row['evidence']) == set(beat['evidence_refs'])
        assert row['source_chapter_chars'] == len(fixture.TEXT)
        assert 'fact_atoms' not in row and 'hinges' not in row


@pytest.mark.parametrize('field', ['events', 'from_beats', 'end_beat', 'land_fact_aliases',
                                  'context_fact_aliases', 'exit_state_aliases'])
def test_plan_response_rejects_retired_atomic_and_skeleton_keys(field):
    from tests.test_plot_design import inputs, design_response
    _bp, _truth, _mapping, public, _private = inputs()
    raw = design_response(public)['scenes'][0]
    with pytest.raises(ValidationError, match=field):
        BlindSceneResponse.model_validate({**raw, field: []})


def test_scene_compiler_decodes_enums_and_strips_budget_scaffolding():
    plan = fixture.plan()
    chapter = PlanChapter.model_validate(plan['output_chapters'][0])
    assert [s.slot for s in chapter.scenes] == ['s01', 's02', 's03']
    assert sum(s.budget_chars for s in chapter.scenes) == len(fixture.TEXT)
    assert all('weight' not in s.model_dump() for s in chapter.scenes)
    contract = semantic_contract_of(fixture.blueprint()['blueprints'][0])
    assert contract['beat_refs'] == ['b01_01', 'b01_02', 'b01_03']
    assert contract['carry_out_refs'] == ['st01_01']


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'unknown', 'empty'])
def test_scene_compiler_refuses_incomplete_or_duplicate_placement(mutation):
    raw = arrangement().model_dump(mode='json')
    if mutation == 'missing':
        raw['beat_placements'].pop()
    elif mutation == 'duplicate':
        raw['beat_placements'].append(copy.deepcopy(raw['beat_placements'][0]))
    elif mutation == 'unknown':
        raw['beat_placements'][0]['beat_ref'] = 'b01_99'
    else:
        raw['beat_placements'][0]['deliveries'] = []
    with pytest.raises(ValueError):
        _compile(raw)


@pytest.mark.parametrize('mutation', ['foreign_scene', 'foreign_evidence', 'duplicate_delivery',
                                     'missing_instruction', 'reverse_delivery'])
def test_delivery_reference_order_and_instruction_are_closed(mutation):
    raw = arrangement(distributed=True).model_dump(mode='json')
    delivery = raw['beat_placements'][1]['deliveries']
    if mutation == 'foreign_scene':
        delivery[0]['scene_ref'] = 's99'
    elif mutation == 'foreign_evidence':
        delivery[0]['evidence_refs'] = ['e02_01']
    elif mutation == 'duplicate_delivery':
        delivery.append(copy.deepcopy(delivery[0]))
    elif mutation == 'missing_instruction':
        delivery[0]['presentation_instruction'] = None
    else:
        delivery.reverse()
    with pytest.raises(ValueError):
        _compile(raw)


def test_background_is_available_only_after_all_parent_deliveries_complete():
    raw = arrangement(distributed=True).model_dump(mode='json')
    raw['scenes'][1]['context_beat_refs'] = ['b01_01']
    good = _compile(raw)
    assert good.completed_at['b01_01'] == 's01'
    assert good.completed_at['b01_02'] == 's02'
    assert good.shared_evidence == (('b01_02', 's01/s02', ('e01_02',)),)


@pytest.mark.parametrize('slot,ref', [('s01', 'b01_01'), ('s01', 'b01_02'),
                                    ('s02', 'b01_02'), ('s02', 'b99_01')])
def test_early_same_scene_or_foreign_background_is_refused(slot, ref):
    raw = arrangement(distributed=True).model_dump(mode='json')
    next(s for s in raw['scenes'] if s['slot'] == slot)['context_beat_refs'] = [ref]
    with pytest.raises(ValueError):
        _compile(raw)


def test_single_scene_state_endpoint_is_derived_without_changing_blueprint():
    bp = _state_blueprint()
    before = bp.model_dump_json()
    compiled = compile_placements(bp, arrangement())
    state = next(b for b in compiled.bindings if b.kind == 'state')
    assert state.ref == 'st01_01' and state.endpoint == 'established'
    assert state.scene_ref == 's02'
    assert bp.model_dump_json() == before


def test_split_state_requires_specific_binding_and_does_not_default_to_first_scene():
    bp = _state_blueprint()
    raw = arrangement(distributed=True).model_dump(mode='json')
    with pytest.raises(ValueError, match='明确绑定'):
        _compile(raw, bp)
    raw['anchor_bindings'] = [{'kind': 'state', 'ref': 'st01_01',
                              'endpoint': 'established', 'scene_ref': 's02'}]
    compiled = _compile(raw, bp)
    assert next(b for b in compiled.bindings if b.kind == 'state').scene_ref == 's02'


@pytest.mark.parametrize('kind,endpoint', [('state', 'established'), ('constraint', 'target'),
                                          ('goal', 'open')])
def test_unregistered_endpoint_cannot_create_new_truth_or_permission(kind, endpoint):
    raw = arrangement().model_dump(mode='json')
    raw['anchor_bindings'] = [{'kind': kind, 'ref': 'unregistered',
                              'endpoint': endpoint, 'scene_ref': 's01'}]
    with pytest.raises(ValueError, match='不能创造'):
        _compile(raw)


@pytest.mark.parametrize('kind', ['cause', 'story_before', 'response_to', 'observes'])
def test_reverse_disclosure_keeps_story_constraints_for_semantic_review(kind):
    response = chapter_response()
    response['constraints'] = [{
        'constraint_key': 'c1', 'kind': kind,
        'before_beat_ref': 'b1', 'after_beat_ref': 'b2',
        'observer': {'char_id': 'char_001'} if kind == 'observes' else None,
        'evidence': [{'start_para': 1, 'end_para': 2}]}]
    bp = blueprint(response=response)
    raw = arrangement().model_dump(mode='json')
    raw['beat_placements'][0]['deliveries'][0]['scene_ref'] = 's02'
    raw['beat_placements'][1]['deliveries'][0]['scene_ref'] = 's01'
    raw['scenes'][1]['transition'] = 'earlier'
    compiled = _compile(raw, bp)
    ends = {b.endpoint: b.scene_ref for b in compiled.bindings if b.kind == 'constraint'}
    assert ends == {'before': 's02', 'after': 's01'}
    assert bp.constraints[0].kind == kind


def test_same_scene_is_explicitly_enforced_instead_of_assumed_from_shared_evidence():
    response = chapter_response()
    response['constraints'] = [{
        'constraint_key': 'c1', 'kind': 'same_scene', 'observer': None,
        'before_beat_ref': 'b1', 'after_beat_ref': 'b2',
        'evidence': [{'start_para': 1, 'end_para': 2}]}]
    bp = blueprint(response=response)
    with pytest.raises(ValueError, match='必须同场'):
        compile_placements(bp, arrangement())
    raw = arrangement().model_dump(mode='json')
    raw['scenes'] = raw['scenes'][:1]
    raw['beat_placements'][1]['deliveries'][0]['scene_ref'] = 's01'
    assert _compile(raw, bp).scene_beats == {'s01': ('b01_01', 'b01_02')}


@pytest.mark.parametrize('transition', ['earlier', 'parallel', 'return', 'later'])
def test_nonlinear_presentations_do_not_fabricate_a_second_state_history(transition):
    bp = _state_blueprint()
    raw = arrangement().model_dump(mode='json')
    raw['scenes'][1]['transition'] = transition
    before = bp.model_dump_json()
    compiled = _compile(raw, bp)
    assert compiled.completed_at['b01_02'] == 's02'
    assert bp.model_dump_json() == before
    assert all('entry_state' not in s and 'exit_state' not in s for s in raw['scenes'])


@pytest.mark.parametrize('scene,transition', [(0, 'continue'), (1, 'start')])
def test_transition_start_syntax_is_closed(scene, transition):
    raw = arrangement().model_dump(mode='json')
    raw['scenes'][scene]['transition'] = transition
    with pytest.raises(ValueError, match='start'):
        _compile(raw)


def _budget_case(sizes, weights):
    chapters, main = [], []
    for ordinal, size in enumerate(sizes, 1):
        text = '甲' * size
        response = {
            'chapter_ordinal': ordinal, 'chapter_function': '测试源章预算',
            'beats': [{'beat_key': 'b1', 'content': '甲完成本章行动。',
                       'participants': [], 'evidence': [{'start_para': 1, 'end_para': 1}],
                       'source_contexts': []}],
            'states': [], 'constraints': [], 'inherited_state_endings': [],
            'entry_state_refs': [], 'carry_out_refs': [],
            'narration': [{'grammatical_person': 'third', 'narrator': None,
                           'focalizer': None, 'pronoun_surfaces': [], 'beat_refs': [],
                           'evidence': [{'start_para': 1, 'end_para': 1}]}]}
        chapters.append(compile_chapter(PlotChapterResponse.model_validate(response), text,
                                        allowed_char_ids=frozenset()).model_dump(mode='json'))
        main.append({'chapter_ordinal': ordinal, 'text': text})
    bp = {'blueprints': chapters}
    index = beat_index(bp, main)
    plan = identity_plan(bp, main, index)
    for chapter, weight in zip(plan['output_chapters'], weights):
        chapter['weight'] = weight
        for scene in chapter['scenes']:
            scene['weight'] = 'mid'
    return bp, plan, index, main


def test_chapter_budget_allocator_clamps_and_redistributes_the_real_failure_shape():
    bp, plan, index, _ = _budget_case([288, 65], ['major', 'minor'])
    adopt_plan_response(plan, bp, index)
    totals = [sum(s['budget_chars'] for s in c['scenes']) for c in plan['output_chapters']]
    assert totals == [301, 52]


def test_bounded_rounding_preserves_total_band_and_determinism_for_all_weight_pairs():
    sizes = [65, 288]
    lo, hi = PLAN_BUDGET_BAND
    lower, upper = [math.ceil(n * lo) for n in sizes], [math.floor(n * hi) for n in sizes]
    for weights in itertools.product(CHAPTER_WEIGHT_RATIO, repeat=2):
        raw = [n * CHAPTER_WEIGHT_RATIO[w] for n, w in zip(sizes, weights)]
        first = _bounded_round_shares(raw, sum(sizes), lower, upper)
        assert first == _bounded_round_shares(raw, sum(sizes), lower, upper)
        assert sum(first) == sum(sizes)
        assert all(a <= b <= c for a, b, c in zip(lower, first, upper))


def test_budget_allocator_postconditions_are_green_at_the_production_checker():
    bp, plan, index, main = _budget_case([37, 65, 288, 411, 73],
                                       ['minor', 'below', 'even', 'above', 'major'])
    adopt_plan_response(plan, bp, index)
    outcomes = {o.rule: o for o in plot_checks.check(plan, blueprints=bp, main_text=main)}
    assert not outcomes['budget_band'].findings
    assert not outcomes['plan_book_band'].findings
    assert sum(s['budget_chars'] for c in plan['output_chapters'] for s in c['scenes']) == 874


@pytest.mark.parametrize('field', ['weight', 'semantic_contract', 'reconstruction_strategy',
                                  'land_fact_refs', 'entry_state_refs', 'ending_state_refs'])
def test_plan_rejects_retired_persistent_chapter_fields(field):
    raw = fixture.plan()['output_chapters'][0]
    with pytest.raises(ValidationError, match=field):
        PlanChapter.model_validate({**raw, field: []})


def test_source_size_table_uses_source_once_and_counts_complete_beats():
    bp = fixture.blueprint()
    table = source_chars_table(bp, beat_index(bp, fixture.MAIN), book_total=True)
    assert '完整剧情 3 个' in table and f'源文 {len(fixture.TEXT)} 字' in table
    assert '原子' not in table


def test_structure_measurement_never_mutates_presentation():
    chapter = fixture.plan()['output_chapters'][0]
    before = copy.deepcopy(chapter)
    metrics = structure_metrics_of(fixture.blueprint()['blueprints'][0], chapter)
    assert chapter == before and metrics['mapping']['beats'] == 3
    assert metrics['partition']['state'] == 'unver'


def _style_sites(brief: str, purpose: str, claim: str) -> list[str]:
    """把一句 brief / purpose 与一条本章 claim 送进生产判定，返回命中。"""
    return checks.style_word_findings(
        [{
            'output_ordinal': 1,
            'chapter_rewrite_brief': brief,
            'scenes': [{'purpose': purpose,
                        'beat_refs': ['b01_01'],
                        'context_beat_refs': []}],
        }],
        source_chapters=[{
            'chapter_ordinal': 1,
            'beats': [{'beat_id': 'b01_01', 'content': claim}],
        }],
        source_facts_key='beats', source_ref_key='beat_id', source_content_key='content',
        land_key='beat_refs', context_key='context_beat_refs')


def test_a_style_word_used_as_a_writing_instruction_is_still_hard():
    hits = _style_sites('用白描手法渲染氛围。', '把节奏放慢。', '甲收到入住邀请')
    assert any('渲染氛围' in row for row in hits)
    assert any('白描' in row for row in hits)


def test_a_style_word_quoted_from_a_typed_fact_is_not_a_writing_instruction():
    """事实可以逐字含有写法词；复述它不该被判成「用写法词代替结构决策」。"""
    claim = '乙评价甲的画只有意象没有骨架'
    hits = _style_sites(
        f'先落「{claim}」这条判断，再补出它的由来。',
        f'让「{claim}」当场说出口。', claim)
    assert hits == [], hits


def test_the_exemption_never_leaks_past_the_quoted_span_or_the_field():
    """豁免只覆盖被引用的那一段；同字段第二次无据出现照旧红。"""
    claim = '乙评价甲的画只有意象没有骨架'
    hits = _style_sites(
        f'先落「{claim}」，然后整章都用意象堆出来。', '正常组场。', claim)
    assert len(hits) == 1 and '意象' in hits[0]

    # 短引号不算证据：引的是那个词本身，不是一段事实。
    assert _style_sites('本章要有「意象」。', '正常组场。', claim)

    # 另一章的 claim 不能替本章开豁免口。
    other = checks.style_word_findings(
        [{'output_ordinal': 1, 'chapter_rewrite_brief': f'先落「{claim}」。',
          'scenes': []}],
        source_chapters=[{'chapter_ordinal': 2,
                          'beats': [{'beat_id': 'b02_01', 'content': claim}]}],
        source_facts_key='beats', source_ref_key='beat_id', source_content_key='content',
        land_key='beat_refs', context_key='context_beat_refs')
    assert other, '跨章不共享事实证据'


def test_the_book_level_strategy_has_no_typed_ref_and_therefore_no_exemption():
    hits = checks.style_word_findings(
        [], source_chapters=[], source_facts_key='beats',
        source_ref_key='beat_id', source_content_key='content', land_key='beat_refs',
        context_key='context_beat_refs',
        reconstruction_strategy='全书用「乙评价甲的画只有意象没有骨架」的意象串起来。')
    assert [row for row in hits if row.startswith('reconstruction_strategy:')]


def test_style_word_scan_survives_punctuation_changes_in_the_claim():
    """标点不是事实词义：claim 从逗号改成顿号不该让守卫变红。"""
    assert _style_sites('先落「甲收到邀请，随后察觉画里的意象」。', '正常组场。',
                        '甲收到邀请、随后察觉画里的意象') == []
