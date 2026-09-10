"""章级 B 必须能把 opaque 场景语义联结到本章逐字证据，且仍由响应选场。"""
from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from contracts.schema.stage4 import BindingConcern
from novel.errors import ProvenanceRefused
from novel.stages.stage4 import plan
from tests.test_stage4_v3_integration import _retain_a_response


def _inputs():
    # 材料显式涉及两个事实，且 alias 大小与事实/场景顺序相反。
    # 多事实分属两场，不能靠源拍或 alias 顺序替模型选择。
    mapping = {
        'fact_aliases': {
            'a_ffffffffffffffff': 'f02_001',
            'a_0000000000000001': 'f02_002',
            'a_1111111111111111': 'f01_001',
        },
        'participant_aliases': {
            'a_2222222222222222': 'char_002',
            'a_3333333333333333': 'char_001',
        },
    }
    bp = {
        'chapter_ordinal': 2, 'causal_edges': [],
        'entry_state_refs': [], 'ending_state_refs': [],
        'fact_atoms': [
            {'fact_id': 'f02_001', 'claim': '来人宣告出发。',
             'kind': 'event', 'obligation': 'land',
             'source_beat_ref': 'b02_01', 'source': {'text': '出发。'},
             'participant_char_ids': ['char_002']},
            {'fact_id': 'f02_002', 'claim': '来人追问期限。',
             'kind': 'event', 'obligation': 'land',
             'source_beat_ref': 'b02_01', 'source': {'text': '几天？'},
             'participant_char_ids': ['char_002']},
        ],
        'beats': [{
            'beat_id': 'b02_01', 'time_layer': 'present', 'place': '山门',
            'present_char_ids': ['char_002'],
            'hinges': [{'hinge_id': 'h02_001', 'kind': '要害台词',
                        'source_fact_refs': ['f02_001', 'f02_002'],
                        'dialogue': {'speaker': {'kind': 'character', 'char_id': 'char_002'},
                                     'addressees': None, 'meaning': '来人在出发前追问期限',
                                     'speech_act': '询问'}, 'literal': None,
                        'source': {'text': '几天？'}}],
        }],
    }
    blueprints = {'blueprints': [bp]}
    chapter = _retain_a_response(blueprints, mapping)['output_chapters'][0]
    for index, scene in enumerate(chapter['scenes'], 1):
        scene['candidate_scene_id'] = f'c02s{index:02}'
    chapter['scenes'][0]['purpose'] = '发出行动指令。'
    chapter['scenes'][1]['purpose'] = '接续追问行动期限。'
    candidate = {
        **mapping,
        'output_chapters': [
            {'output_ordinal': 1, 'scenes': [{
                'candidate_scene_id': 'c01s01', 'land_fact_aliases': [],
                'context_fact_aliases': [],
                'exit_state_aliases': ['a_1111111111111111']}]},
            chapter,
        ],
    }
    up = SimpleNamespace(
        main_text=[{'chapter_ordinal': 1, 'text': '临章私密正文'},
                   {'chapter_ordinal': 2, 'text': '出发。几天？'}],
        blueprints={'blueprints': [{
            'chapter_ordinal': 1, 'ending_state_refs': ['f01_001']}, bp]},
    )
    baseline = plan.build_mechanical_baseline(candidate, blueprints).chapter(2)
    return SimpleNamespace(title='映射反例'), up, candidate, baseline


def _payload(issue, marker):
    lines = issue.user.splitlines()
    return json.loads(lines[lines.index(marker) + 1])


def test_binding_prompt_resolves_fact_material_join_without_choosing_for_model():
    ctx, up, candidate, baseline = _inputs()
    frozen = deepcopy(candidate)
    issue = plan._chapter_binding_issue(ctx, up, candidate, baseline, 2)
    chapter = _payload(issue, plan.prompts.BLOCK_CHAPTER_CANDIDATE)
    evidence = _payload(issue, plan.prompts.BLOCK_CHAPTER_EVIDENCE)
    ambiguities = _payload(issue, plan.prompts.BLOCK_CHAPTER_AMBIGUITIES)
    assert chapter == candidate['output_chapters'][1]
    assert candidate == frozen
    assert len(ambiguities) == 1
    ambiguity = ambiguities[0]
    assert ambiguity['candidates'] == ['c02s01', 'c02s02']
    material = evidence['stages'][0]['materials'][0]
    # 一个仅消费最终 prompt 的测试响应者：从逐字材料找事实，再找该场。
    fact = next(row for row in evidence['fact_atoms']
                if row['source_text'] == material['text'])
    alias = next(alias for alias, formal in evidence['fact_aliases'].items()
                 if formal == fact['fact_id'])
    chosen = next(scene['candidate_scene_id'] for scene in chapter['scenes']
                  if alias in scene['land_fact_aliases'])
    assert chosen == 'c02s02'
    rows = plan.apply_chapter_resolutions(baseline, [{
        'ambiguity_id': ambiguity['ambiguity_id'], 'candidate_scene_id': chosen,
    }])
    assert rows[0]['material_refs'] == []
    assert rows[1]['material_refs'] == ['h02_001']
    assert evidence['participant_aliases'] == {'a_2222222222222222': 'char_002'}
    assert set(issue.formal_to_alias) == {'f02_001', 'f02_002', 'char_002'}
    assert '临章私密正文' not in issue.user
    assert 'f01_001' not in json.dumps(evidence)
    assert 'a_1111111111111111' not in json.dumps(evidence)
    for neighbor_ref in ('f01_001', 'a_1111111111111111', 'char_001'):
        concern = BindingConcern(
            constraint_code='future_leak', scenes=['c02s01'],
            reason='边界阅读不能成为本章写引用。', closed_refs=[neighbor_ref])
        assert issue.concern_error(concern) is not None


@pytest.mark.parametrize('corruption', [
    'missing_fact_table', 'missing_participant_table', 'missing_fact',
    'missing_participant', 'duplicate_fact', 'duplicate_participant',
    'cross_table_alias', 'cross_table_same_pair',
    'foreign_land', 'foreign_context', 'foreign_entry', 'foreign_exit',
    'foreign_goal',
])
def test_binding_prompt_refuses_unjoinable_frozen_evidence(corruption):
    ctx, up, candidate, baseline = _inputs()
    scene = candidate['output_chapters'][1]['scenes'][0]
    if corruption == 'missing_fact_table':
        del candidate['fact_aliases']
    elif corruption == 'missing_participant_table':
        del candidate['participant_aliases']
    elif corruption == 'missing_fact':
        del candidate['fact_aliases']['a_ffffffffffffffff']
    elif corruption == 'missing_participant':
        del candidate['participant_aliases']['a_2222222222222222']
    elif corruption == 'duplicate_fact':
        candidate['fact_aliases']['a_4444444444444444'] = 'f02_001'
    elif corruption == 'duplicate_participant':
        candidate['participant_aliases']['a_4444444444444444'] = 'char_002'
    elif corruption == 'cross_table_alias':
        candidate['participant_aliases']['a_ffffffffffffffff'] = 'char_004'
    elif corruption == 'cross_table_same_pair':
        candidate['participant_aliases']['a_ffffffffffffffff'] = 'f02_001'
    elif corruption == 'foreign_goal':
        candidate['output_chapters'][1]['chapter_goal']['fact_aliases'] = [
            'a_1111111111111111']
    else:
        field = {
            'foreign_land': 'land_fact_aliases',
            'foreign_context': 'context_fact_aliases',
            'foreign_entry': 'entry_state_aliases',
            'foreign_exit': 'exit_state_aliases',
        }[corruption]
        scene[field].append('a_1111111111111111')
    with pytest.raises(ProvenanceRefused):
        plan._chapter_binding_issue(ctx, up, candidate, baseline, 2)


def test_stage_only_participant_does_not_require_invented_opaque_alias():
    ctx, up, candidate, baseline = _inputs()
    up.blueprints['blueprints'][1]['beats'][0]['present_char_ids'].append('char_009')
    issue = plan._chapter_binding_issue(ctx, up, candidate, baseline, 2)
    evidence = _payload(issue, plan.prompts.BLOCK_CHAPTER_EVIDENCE)
    assert 'char_009' in evidence['stages'][0]['present_char_ids']
    assert 'char_009' not in evidence['participant_aliases'].values()
    assert 'char_009' in issue.issued_refs


def test_binding_request_identity_does_not_reuse_old_input_contract(monkeypatch):
    ctx, up, candidate, baseline = _inputs()
    prepared = SimpleNamespace(subject_sha256='a' * 64, input_sha256='b' * 64)
    issue = plan._chapter_binding_issue(ctx, up, candidate, baseline, 2)
    current = plan._plan_chapter_request(ctx, prepared, baseline, 2, issue)
    monkeypatch.setattr(plan, 'STAGE4_BINDING_INPUT_CONTRACT', 'old-input-contract')
    previous = plan._plan_chapter_request(ctx, prepared, baseline, 2, issue)
    assert current.input_sha256 != previous.input_sha256
    assert current.user == previous.user == issue.user
    assert current.system == previous.system


@pytest.mark.parametrize('has_alias', [False, True])
def test_dialogue_only_participant_is_issued_without_inventing_alias(has_alias):
    ctx, up, candidate, baseline = _inputs()
    dialogue = up.blueprints['blueprints'][1]['beats'][0]['hinges'][0]['dialogue']
    dialogue['speaker'] = {'kind': 'character', 'char_id': 'char_009'}
    dialogue['addressees'] = [{'kind': 'group', 'label': '同行人'},
                              {'kind': 'character', 'char_id': 'char_008'}]
    if has_alias:
        candidate['participant_aliases']['a_9999999999999999'] = 'char_009'
    issue = plan._chapter_binding_issue(ctx, up, candidate, baseline, 2)
    evidence = _payload(issue, plan.prompts.BLOCK_CHAPTER_EVIDENCE)
    assert {'char_008', 'char_009'} <= issue.issued_refs
    assert '同行人' not in issue.issued_refs
    assert ('char_009' in evidence['participant_aliases'].values()) is has_alias
    assert 'char_008' not in evidence['participant_aliases'].values()


@pytest.mark.parametrize('change', ['facts', 'meaning', 'speaker', 'addressees', 'source_start', 'source_end'])
def test_binding_request_invalidates_changed_material_evidence(change):
    ctx, up, candidate, baseline = _inputs()
    prepared = SimpleNamespace(subject_sha256='a' * 64, input_sha256='b' * 64)
    before = plan._plan_chapter_request(ctx, prepared, baseline, 2,
        plan._chapter_binding_issue(ctx, up, candidate, baseline, 2))
    hinge = up.blueprints['blueprints'][1]['beats'][0]['hinges'][0]
    if change == 'facts':
        hinge['source_fact_refs'] = ['f02_002']
    elif change == 'meaning':
        hinge['dialogue']['meaning'] = '质疑能否及时出发'
    elif change == 'speaker':
        hinge['dialogue']['speaker'] = None
    elif change == 'addressees':
        hinge['dialogue']['addressees'] = [{'kind': 'group', 'label': '同伴'}]
    else:
        hinge['source'][change.removeprefix('source_')] = 4
    updated = plan.build_mechanical_baseline(candidate, up.blueprints).chapter(2)
    after = plan._plan_chapter_request(ctx, prepared, updated, 2,
        plan._chapter_binding_issue(ctx, up, candidate, updated, 2))
    assert after.input_sha256 != before.input_sha256


@pytest.mark.parametrize('changed', [
    'foreign_text', 'foreign_fact', 'foreign_candidate', 'book_identity',
    'own_source', 'own_source_range', 'own_fact', 'own_candidate', 'previous_boundary', 'previous_state_fact',
    'candidates',
])
def test_binding_request_changes_only_for_actual_materials_and_boundary(changed):
    ctx, up, candidate, baseline = _inputs()
    prepared = SimpleNamespace(subject_sha256='a' * 64, input_sha256='b' * 64)
    before = plan._plan_chapter_request(
        ctx, prepared, baseline, 2,
        plan._chapter_binding_issue(ctx, up, candidate, baseline, 2))
    if changed == 'foreign_text':
        up.main_text[0]['text'] = '改变上一章正文但边界不变。'
    elif changed == 'foreign_fact':
        up.blueprints['blueprints'][0]['fact_atoms'] = [{'claim': '改变未下发的事实。'}]
    elif changed == 'foreign_candidate':
        candidate['output_chapters'][0]['chapter_rewrite_brief'] = '别章重写说明'
    elif changed == 'book_identity':
        prepared.subject_sha256 = 'c' * 64
        prepared.input_sha256 = 'd' * 64
    elif changed == 'own_source':
        up.main_text[1]['text'] += '第三日动身。'
    elif changed == 'own_source_range':
        up.blueprints['blueprints'][1]['fact_atoms'][0]['source']['start'] = 3
    elif changed == 'previous_state_fact':
        up.blueprints['blueprints'][0]['fact_atoms'] = [{
            'fact_id': 'f01_001', 'claim': '上一章结束时角色已经离开。', 'kind': 'character_state',
            'obligation': 'land'}]
    elif changed == 'own_fact':
        up.blueprints['blueprints'][1]['fact_atoms'][0]['source']['text'] = '明日出发。'
    elif changed == 'own_candidate':
        candidate['output_chapters'][1]['scenes'][0]['purpose'] = '拒绝行动指令。'
    elif changed == 'previous_boundary':
        up.blueprints['blueprints'][0]['ending_state_refs'] = []
    else:
        from dataclasses import replace
        ambiguity = baseline.ambiguities[0]
        baseline = replace(baseline, ambiguities=(replace(
            ambiguity, candidates=ambiguity.candidates[:1]),))
    after = plan._plan_chapter_request(
        ctx, prepared, baseline, 2,
        plan._chapter_binding_issue(ctx, up, candidate, baseline, 2))
    should_change = changed in {'own_source', 'own_source_range', 'own_fact', 'own_candidate',
                                'previous_boundary', 'previous_state_fact', 'candidates'}
    assert (after.input_sha256 != before.input_sha256) is should_change
    if not should_change:
        assert (after.user, after.system) == (before.user, before.system)
