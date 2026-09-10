#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4A 去序投影与 Stage4B 窄绑定编译器。"""
from __future__ import annotations

import copy
import json
import re

import pytest
from pydantic import ValidationError

from contracts.invariants import BLIND_ALIAS_ALGORITHM
from contracts.schema.stage4 import (
    BlindBookStrategyResponse, BlindCandidateChapter, BlindCandidateProvenance,
    BlindChapterDesignResponse, BlindGoalResponse, BlindPlanResponse,
    EvidenceBindingResponse, EvidenceSceneBinding,
)
from novel.stages.stage4 import blind as blind_core
from novel.stages.stage4 import prompts
from novel.stages.stage4.blind import (
    BlindPlanError,
    assemble_blind_candidate,
    build_blind_chapter_projection,
    build_blind_executor_projection,
    build_blind_projection,
    build_blind_strategy_projection,
    build_evidence_catalog,
    candidate_subject_sha256,
    compile_blind_chapter_design,
    compile_blind_candidate,
    construct_feasible_binding_witness,
    merge_evidence_bindings,
    prove_binding_infeasible,
    validate_infeasible_binding,
)
from novel.stages.stage4.measure import adopt_plan_response


ROOT = '1f' * 20
ALIAS_RE = re.compile(r'^a_[0-9a-f]{16,64}$')


def test_phase_v2_schemas_reject_retired_display_and_participant_fields():
    alias = 'a_0123456789abcdef'
    with pytest.raises(ValidationError, match='rationale'):
        BlindGoalResponse.model_validate({
            'intent': 'G_ADVANCE', 'fact_aliases': [alias],
            'rationale': '旧展示理由',
        })
    with pytest.raises(ValidationError, match='participant_refs'):
        EvidenceSceneBinding.model_validate({
            'candidate_scene_id': 'c01s01',
            'material_refs': [], 'stage_refs': ['b01_01'],
            'participant_refs': ['char_001'],
        })


def _atom(chapter: int, index: int, claim: str, *, participants: list[str]) -> dict:
    return {
        'fact_id': f'f{chapter:02d}_{index:03d}',
        'claim': claim,
        'kind': 'event' if index % 2 else 'character_state',
        'obligation': 'land',
        'source_beat_ref': f'b{chapter:02d}_{index:02d}',
        'source': {
            'text': f'SOURCE-ONLY-{chapter}-{index}', 'start': index,
            'end': index + 1,
        },
        'participant_char_ids': participants,
    }


def _beat(chapter: int, index: int, participants: list[str], *, hinge: bool) -> dict:
    return {
        'beat_id': f'b{chapter:02d}_{index:02d}',
        'span': {
            'text': f'BODY-ONLY-{chapter}-{index}', 'start': index,
            'end': index + 1,
        },
        'time_layer': 'present',
        'place': f'地点{index}',
        'present_char_ids': participants,
        'hinges': ([{
            'hinge_id': f'h{chapter:02d}_{index:03d}',
            'kind': '要害台词',
            'source': {
                'text': f'MATERIAL-ONLY-{chapter}-{index}', 'start': index,
                'end': index + 1,
            },
        }] if hinge else []),
    }


def _blueprints() -> dict:
    return {
        'blueprints': [
            {
                'chapter_ordinal': 1,
                'chapter_function': '建立入住冲突并完成主角入局',
                'fact_atoms': [
                    _atom(1, 1, '甲收到入住邀请', participants=['char_001']),
                    _atom(1, 2, '乙拒绝甲入住', participants=['char_002', 'char_001']),
                    _atom(1, 3, '甲决定留下', participants=['char_001']),
                ],
                'causal_edges': [
                    {'before': 'f01_002', 'after': 'f01_003'},
                    {'before': 'f01_001', 'after': 'f01_003'},
                ],
                'narrative_relations': [],
                'entry_state_refs': ['f01_001'],
                'ending_state_refs': ['f01_003'],
                'beats': [
                    _beat(1, 1, ['char_001'], hinge=False),
                    _beat(1, 2, ['char_001', 'char_002'], hinge=True),
                    _beat(1, 3, ['char_001'], hinge=False),
                ],
                'narration': [],
            },
            {
                'chapter_ordinal': 2,
                'chapter_function': '引入访客并扩大冲突',
                'fact_atoms': [
                    _atom(2, 1, '丙突然来访', participants=['char_003']),
                    _atom(2, 2, '甲决定隐瞒邀请', participants=['char_001', 'char_003']),
                ],
                'causal_edges': [{'before': 'f02_001', 'after': 'f02_002'}],
                'narrative_relations': [],
                'entry_state_refs': [],
                'ending_state_refs': ['f02_002'],
                'beats': [
                    _beat(2, 1, ['char_003'], hinge=True),
                    _beat(2, 2, ['char_001', 'char_003'], hinge=False),
                ],
                'narration': [],
            },
        ],
        '_provenance': {'inputs': {'stage1/main_text.json': 'not-for-A'}},
    }


def _permuted_blueprints() -> dict:
    value = copy.deepcopy(_blueprints())
    value['blueprints'].reverse()
    for chapter in value['blueprints']:
        chapter['fact_atoms'].reverse()
        chapter['causal_edges'].reverse()
        chapter['entry_state_refs'].reverse()
        chapter['ending_state_refs'].reverse()
        chapter['beats'].reverse()
        for atom in chapter['fact_atoms']:
            atom['participant_char_ids'].reverse()
        for beat in chapter['beats']:
            beat['present_char_ids'].reverse()
            beat['hinges'].reverse()
    return value


def _walk_keys(value: object) -> list[str]:
    if isinstance(value, dict):
        return list(value) + [key for child in value.values() for key in _walk_keys(child)]
    if isinstance(value, list):
        return [key for child in value for key in _walk_keys(child)]
    return []


def _fact_aliases(mapping: dict) -> dict[str, str]:
    return {formal: alias for alias, formal in mapping['fact_aliases'].items()}


def _goal(alias: str, intent: str) -> dict:
    return {
        'intent': intent,
        'fact_aliases': [alias],
    }


def _scene(
    land: list[str], turn: str, *, purpose: str, role: str, transition: str,
    entry: list[str] | None = None, exit_refs: list[str] | None = None,
) -> dict:
    return {
        'weight': 'W_STRONG',
        'purpose': purpose,
        'role': role,
        'delivery': 'D_SCENE',
        'transition': transition,
        'land_fact_aliases': land,
        'context_fact_aliases': [],
        'turn_fact_alias': turn,
        'entry_state_aliases': entry or [],
        'exit_state_aliases': exit_refs or [],
    }


def _candidate(mapping: dict) -> dict:
    aliases = _fact_aliases(mapping)
    ch1 = {
        'output_ordinal': 1,
        'structure_decision': {
            'mode': 'reconstruct',
            'reason': '用拒绝作入口，并把邀请与最终选择重新组合。',
            'declared_change_kinds': ['entry_anchor', 'fact_grouping'],
        },
        'chapter_goal': _goal(aliases['f01_003'], 'G_ADVANCE'),
        'open_hook_goal': _goal(aliases['f01_002'], 'G_OPEN'),
        'end_hook_goal': _goal(aliases['f01_003'], 'G_CLOSE'),
        'weight': 'CH_EVEN',
        'chapter_rewrite_brief': '先让拒绝发生，再补足邀请，最后完成留下的选择。',
        'scenes': [
            _scene(
                [aliases['f01_002']], aliases['f01_002'],
                purpose='让拒绝立即发生', role='R_COLD_OPEN',
                transition='X_START', exit_refs=[aliases['f01_002']]),
            _scene(
                [aliases['f01_001'], aliases['f01_003']], aliases['f01_003'],
                purpose='补出邀请并完成选择', role='R_PAYOFF',
                transition='X_EARLIER', entry=[aliases['f01_002']],
                exit_refs=[aliases['f01_003']]),
        ],
    }
    ch2 = {
        'output_ordinal': 2,
        'structure_decision': {
            'mode': 'retain',
            'reason': '本章只有一条强因果链，没有安全的重排空间。',
            'declared_change_kinds': [],
        },
        'chapter_goal': _goal(aliases['f02_002'], 'G_ADVANCE'),
        'open_hook_goal': _goal(aliases['f02_001'], 'G_OPEN'),
        'end_hook_goal': _goal(aliases['f02_002'], 'G_CLOSE'),
        'weight': 'CH_EVEN',
        'chapter_rewrite_brief': '访客到场后形成隐瞒决定。',
        'scenes': [
            _scene(
                [aliases['f02_001'], aliases['f02_002']], aliases['f02_002'],
                purpose='让来访逼出隐瞒决定', role='R_PAYOFF',
                transition='X_START', exit_refs=[aliases['f02_002']]),
        ],
    }
    return {
        'reconstruction_strategy': '按因果可行性重新组合事实，而不是复述源 beat。',
        'output_chapters': [ch2, ch1],  # A 可乱序返回；编译器规范化章序。
    }


def _typed_candidate(mapping: dict) -> dict:
    return BlindPlanResponse.model_validate(_candidate(mapping)).model_dump(mode='json')


def _split_strategy(mapping: dict) -> dict:
    candidate = _candidate(mapping)
    return {
        'reconstruction_strategy': candidate['reconstruction_strategy'],
        'chapter_weights': [{
            'chapter_ordinal': chapter['output_ordinal'],
            'weight': chapter['weight'],
        } for chapter in candidate['output_chapters']],
    }


def _local_chapter_response(chapter: dict, local_mapping: dict) -> dict:
    local_by_global = {
        global_alias: local
        for local, global_alias in local_mapping['fact_aliases'].items()
    }

    def goal(value: dict) -> dict:
        return {
            'intent': value['intent'],
            'fact_aliases': [local_by_global[alias] for alias in value['fact_aliases']],
        }

    def scene(value: dict) -> dict:
        return {
            'weight': value['weight'],
            'purpose': value['purpose'],
            'role': value['role'],
            'delivery': value['delivery'],
            'transition': value['transition'],
            'land_fact_aliases': [
                local_by_global[alias] for alias in value['land_fact_aliases']
            ],
            'context_fact_aliases': [
                local_by_global[alias] for alias in value['context_fact_aliases']
            ],
            'turn_fact_alias': local_by_global[value['turn_fact_alias']],
            'entry_state_aliases': [
                local_by_global[alias] for alias in value['entry_state_aliases']
            ],
            'exit_state_aliases': [
                local_by_global[alias] for alias in value['exit_state_aliases']
            ],
        }

    return {
        'structure_decision': copy.deepcopy(chapter['structure_decision']),
        'chapter_goal': goal(chapter['chapter_goal']),
        'open_hook_goal': goal(chapter['open_hook_goal']),
        'end_hook_goal': goal(chapter['end_hook_goal']),
        'chapter_rewrite_brief': chapter['chapter_rewrite_brief'],
        'scenes': [scene(value) for value in chapter['scenes']],
    }


def _raw_bindings(compiled: dict) -> dict:
    scene_ids = {
        chapter['output_ordinal']: [scene['candidate_scene_id'] for scene in chapter['scenes']]
        for chapter in compiled['output_chapters']
    }
    return {
        'status': 'bound',
        'candidate_sha256': candidate_subject_sha256(compiled),
        # B 的章、场数组都故意反排；合并结果仍必须服从 A。
        'chapters': [
            {
                'output_ordinal': 2,
                'scenes': [{
                    'candidate_scene_id': scene_ids[2][0],
                    'material_refs': ['h02_001'],
                    'stage_refs': ['b02_01', 'b02_02'],
                }],
            },
            {
                'output_ordinal': 1,
                'scenes': [
                    {
                        'candidate_scene_id': scene_ids[1][1],
                        'material_refs': [],
                        'stage_refs': ['b01_01', 'b01_03'],
                    },
                    {
                        'candidate_scene_id': scene_ids[1][0],
                        'material_refs': ['h01_002'],
                        'stage_refs': ['b01_02'],
                    },
                ],
            },
        ],
        'binding_concerns': [],
    }


def _typed_bindings(compiled: dict) -> dict:
    return EvidenceBindingResponse.model_validate(
        _raw_bindings(compiled)).model_dump(mode='json')


def _compiled() -> tuple[dict, dict, dict]:
    truth, mapping = build_blind_projection(_blueprints(), root_clean_sha1=ROOT)
    compiled = compile_blind_candidate(
        _typed_candidate(mapping), mapping, truth=truth)
    return compiled, mapping, build_evidence_catalog(_blueprints())


def _chapter_binding(response: dict, chapter: int) -> dict:
    return next(row for row in response['chapters'] if row['output_ordinal'] == chapter)


def test_blind_projection_is_order_invariant_and_hides_formal_ids_from_a():
    prompt, mapping = build_blind_projection(_blueprints(), root_clean_sha1=ROOT)
    permuted_prompt, permuted_mapping = build_blind_projection(
        _permuted_blueprints(), root_clean_sha1=ROOT)

    assert prompt == permuted_prompt
    assert mapping == permuted_mapping
    assert mapping['alias_algorithm'] == BLIND_ALIAS_ALGORITHM
    assert [chapter['chapter_ordinal'] for chapter in prompt['chapters']] == [1, 2]

    forbidden_keys = {
        'fact_id', 'source_beat_ref', 'beat_id', 'beats', 'hinges',
        'material_refs', 'materials', 'body', 'text', 'source', 'summary', 'span',
        'participant_char_ids', 'present_char_ids',
    }
    assert forbidden_keys.isdisjoint(_walk_keys(prompt))
    serialized = json.dumps(prompt, ensure_ascii=False, sort_keys=True)
    for formal in (
        'f01_001', 'f01_002', 'f01_003', 'f02_001', 'f02_002',
        'char_001', 'char_002', 'char_003',
        'b01_01', 'b01_02', 'h01_002', 'SOURCE-SUMMARY-ONE',
        'SOURCE-ONLY-1-1', 'BODY-ONLY-1-1', 'MATERIAL-ONLY-1-2',
    ):
        assert formal not in serialized
    assert all(ALIAS_RE.fullmatch(alias) for alias in mapping['fact_aliases'])
    assert all(ALIAS_RE.fullmatch(alias) for alias in mapping['participant_aliases'])
    assert set(mapping['fact_aliases'].values()) == {
        'f01_001', 'f01_002', 'f01_003', 'f02_001', 'f02_002',
    }
    assert set(mapping['participant_aliases'].values()) == {
        'char_001', 'char_002', 'char_003',
    }
    assert re.fullmatch(r'[0-9a-f]{64}', mapping['truth_sha256'])


def test_executor_projection_keeps_hard_and_hides_review_relations():
    """含糊关系只进 Commander workpack，不能静默升级成 Executor 约束。"""
    blueprints = _blueprints()
    blueprints['blueprints'][0]['narrative_relations'] = [
        {
            'kind': 'story_before', 'enforcement': 'hard',
            'before_fact_ref': 'f01_001', 'after_fact_ref': 'f01_002',
        },
        {
            'kind': 'response_to', 'enforcement': 'review',
            'stimulus_fact_ref': 'f01_002', 'response_fact_ref': 'f01_003',
        },
    ]
    truth, mapping = build_blind_projection(
        blueprints, root_clean_sha1=ROOT)
    commander_relations = truth['chapters'][0]['narrative_relations']
    assert {row['enforcement'] for row in commander_relations} == {
        'hard', 'review'}

    executor_truth = build_blind_executor_projection(truth)
    assert [
        row['enforcement']
        for row in executor_truth['chapters'][0]['narrative_relations']
    ] == ['hard']

    chapter_truth, _local = build_blind_chapter_projection(
        truth, mapping, chapter_ordinal=1, root_clean_sha1=ROOT)
    relations = chapter_truth['chapter']['narrative_relations']
    assert len(relations) == 1
    assert relations[0]['kind'] == 'story_before'
    assert relations[0]['enforcement'] == 'hard'
    serialized = json.dumps(chapter_truth, ensure_ascii=False, sort_keys=True)
    assert 'response_to' not in serialized
    # 完整 review truth 仍绑定 mapping；Executor 降维投影不得冒充新的 truth 身份。
    assert mapping['truth_sha256'] != blind_core._sha256(executor_truth)


@pytest.mark.parametrize(
    ('field', 'leaked_ref'),
    (
        ('claim', 'f02_001'),
        ('claim', 'char_003'),
        ('claim', 'b02_01'),
        ('claim', 'h02_001'),
        ('chapter_function', 'f01_003'),
        ('chapter_function', 'char_002'),
        ('chapter_function', 'b01_02'),
        ('chapter_function', 'h01_002'),
    ),
)
def test_blind_projection_refuses_book_formal_ids_hidden_in_free_text(
        field: str, leaked_ref: str):
    blueprints = _blueprints()
    chapter = blueprints['blueprints'][0]
    if field == 'claim':
        chapter['fact_atoms'][0]['claim'] = f'甲在说明中回显 {leaked_ref}'
    else:
        chapter['chapter_function'] = f'本章功能误写内部引用 {leaked_ref}'

    with pytest.raises(BlindPlanError, match='泄漏正式内部引用'):
        build_blind_projection(blueprints, root_clean_sha1=ROOT)


def test_blind_projection_does_not_reject_unregistered_id_shaped_text():
    blueprints = _blueprints()
    blueprints['blueprints'][0]['chapter_function'] = \
        '本章讨论 char_999 与 b99_99 这两个未登记字面。'
    blueprints['blueprints'][0]['fact_atoms'][0]['claim'] = \
        '甲把 f99_999 与 h99_999 当作普通字面记在纸上'

    prompt, _mapping = build_blind_projection(
        blueprints, root_clean_sha1=ROOT)
    serialized = json.dumps(prompt, ensure_ascii=False)
    assert 'char_999' in serialized
    assert 'f99_999' in serialized
    assert 'b99_99' in serialized
    assert 'h99_999' in serialized


def test_split_wire_schemas_keep_book_accounting_out_of_chapter_response():
    strategy = BlindBookStrategyResponse.model_validate({
        'reconstruction_strategy': '按章级功能协调全书事实组织。',
        'chapter_weights': [
            {'chapter_ordinal': 1, 'weight': 'CH_MAJOR'},
            {'chapter_ordinal': 2, 'weight': 'CH_BELOW'},
        ],
    })
    assert [row.chapter_ordinal for row in strategy.chapter_weights] == [1, 2]
    with pytest.raises(ValidationError, match='不能重复'):
        BlindBookStrategyResponse.model_validate({
            'reconstruction_strategy': '重复章号必须关闭式失败。',
            'chapter_weights': [
                {'chapter_ordinal': 1, 'weight': 'CH_MAJOR'},
                {'chapter_ordinal': 1, 'weight': 'CH_BELOW'},
            ],
        })

    truth, mapping = build_blind_projection(_blueprints(), root_clean_sha1=ROOT)
    _public, local_mapping = build_blind_chapter_projection(
        truth, mapping, chapter_ordinal=1, root_clean_sha1=ROOT)
    global_chapter = next(
        row for row in _candidate(mapping)['output_chapters']
        if row['output_ordinal'] == 1)
    local = _local_chapter_response(global_chapter, local_mapping)
    parsed = BlindChapterDesignResponse.model_validate(local)
    assert not hasattr(parsed, 'output_ordinal') and not hasattr(parsed, 'weight')

    with pytest.raises(ValidationError, match='output_ordinal'):
        BlindChapterDesignResponse.model_validate({**local, 'output_ordinal': 1})
    with pytest.raises(ValidationError, match='weight'):
        BlindChapterDesignResponse.model_validate({**local, 'weight': 'CH_EVEN'})
    leaked = copy.deepcopy(local)
    leaked['chapter_goal']['fact_aliases'] = [
        global_chapter['chapter_goal']['fact_aliases'][0]
    ]
    with pytest.raises(ValidationError, match='local fact alias'):
        BlindChapterDesignResponse.model_validate(leaked)


def test_split_prompt_shapes_and_provenance_architecture_are_explicit():
    strategy_hint = prompts.BLIND_BOOK_STRATEGY_HINT
    assert set(strategy_hint) == {'reconstruction_strategy', 'chapter_weights'}
    assert set(strategy_hint['chapter_weights'][0]) == {'chapter_ordinal', 'weight'}
    chapter_hint = prompts.BLIND_CHAPTER_DESIGN_HINT
    assert {'output_ordinal', 'weight'}.isdisjoint(chapter_hint)
    assert chapter_hint['scenes'][0]['turn_fact_alias'].startswith('f_')
    rendered = prompts.blind_chapter_design_system()
    for clause in (
        'local alias', 'X_CONTINUE', 'X_EARLIER', 'X_PARALLEL',
        'X_RETURN', 'X_LATER', '代码自动继承上一场的有效 exit',
    ):
        assert clause in rendered

    common = {
        'code': 'test-code', 'root_clean_sha1': ROOT, 'inputs': {},
        'prompts': {'BLIND_BOOK_STRATEGY_SYS': 'a' * 10},
        'model': 'test-model', 'adopted_models': ['test-model'],
        'alias_algorithm': BLIND_ALIAS_ALGORITHM,
        'truth_sha256': 'b' * 64,
    }
    with pytest.raises(ValidationError, match='architecture'):
        BlindCandidateProvenance.model_validate(common)
    for old in (None, 'whole_book_v1'):
        with pytest.raises(ValidationError, match='architecture'):
            BlindCandidateProvenance.model_validate({**common, 'architecture': old})
    split = BlindCandidateProvenance.model_validate({
        **common, 'architecture': 'book_strategy_chapters_v1',
    })
    assert split.architecture == 'book_strategy_chapters_v1'
    with pytest.raises(ValidationError, match='architecture'):
        BlindCandidateProvenance.model_validate({
            **common, 'architecture': 'implicit-current-default',
        })


def test_strategy_and_local_chapter_projections_are_deterministic_and_closed():
    truth, mapping = build_blind_projection(_blueprints(), root_clean_sha1=ROOT)
    permuted_truth, permuted_mapping = build_blind_projection(
        _permuted_blueprints(), root_clean_sha1=ROOT)

    strategy = build_blind_strategy_projection(truth)
    assert strategy == build_blind_strategy_projection(permuted_truth)
    assert strategy['blind_contract'] == blind_core.BLIND_BOOK_STRATEGY_CONTRACT
    assert [row['chapter_ordinal'] for row in strategy['chapters']] == [1, 2]
    serialized_strategy = json.dumps(strategy, ensure_ascii=False, sort_keys=True)
    for forbidden in (
        'fact_alias', 'participant_alias', 'claim', 'f01_001', 'char_001',
        'a_', 'source_beat_ref', 'summary', 'SOURCE-ONLY',
    ):
        assert forbidden not in serialized_strategy

    public, local_mapping = build_blind_chapter_projection(
        truth, mapping, chapter_ordinal=1, root_clean_sha1=ROOT)
    repeated_public, repeated_mapping = build_blind_chapter_projection(
        permuted_truth, permuted_mapping, chapter_ordinal=1, root_clean_sha1=ROOT)
    assert public == repeated_public
    assert local_mapping == repeated_mapping
    assert public['blind_contract'] == blind_core.BLIND_CHAPTER_CONTRACT
    assert public['chapter']['chapter_ordinal'] == 1
    assert local_mapping['chapter_ordinal'] == 1
    assert local_mapping['truth_sha256'] == mapping['truth_sha256']
    assert local_mapping['fact_aliases']
    assert all(re.fullmatch(r'f_[0-9a-f]{6,64}', alias)
               for alias in local_mapping['fact_aliases'])
    assert all(re.fullmatch(r'p_[0-9a-f]{6,64}', alias)
               for alias in local_mapping['participant_aliases'])
    assert set(local_mapping['fact_aliases'].values()) == {
        alias for alias, formal in mapping['fact_aliases'].items()
        if formal.startswith('f01_')
    }

    serialized_public = json.dumps(public, ensure_ascii=False, sort_keys=True)
    assert not any(alias in serialized_public for alias in mapping['fact_aliases'])
    assert not any(alias in serialized_public for alias in mapping['participant_aliases'])
    for formal in (*mapping['fact_aliases'].values(),
                   *mapping['participant_aliases'].values()):
        assert formal not in serialized_public
    assert 'SOURCE-ONLY' not in serialized_public

    local_facts = set(local_mapping['fact_aliases'])
    chapter = public['chapter']
    assert {fact['fact_alias'] for fact in chapter['facts']} == local_facts
    assert [fact['fact_alias'] for fact in chapter['facts']] == sorted(local_facts)
    for edge in chapter['causal_edges']:
        assert {edge['before_alias'], edge['after_alias']} <= local_facts
    assert set(chapter['entry_state_aliases']) <= local_facts
    assert set(chapter['ending_state_aliases']) <= local_facts


def test_local_alias_collision_extends_the_whole_namespace_deterministically(
        monkeypatch):
    truth, mapping = build_blind_projection(_blueprints(), root_clean_sha1=ROOT)
    real_digest = blind_core._digest
    assigned: dict[str, str] = {}

    def colliding_digest(root: str, domain: str, owner: str) -> str:
        if domain == 'chapter-local-fact-v1':
            suffix = assigned.setdefault(owner, f'{len(assigned):02x}')
            return 'abcdef' + suffix + '0' * 56
        return real_digest(root, domain, owner)

    monkeypatch.setattr(blind_core, '_digest', colliding_digest)
    public, local_mapping = build_blind_chapter_projection(
        truth, mapping, chapter_ordinal=1, root_clean_sha1=ROOT)
    aliases = list(local_mapping['fact_aliases'])
    assert aliases and all(len(alias) == len('f_') + 8 for alias in aliases)
    assert len(aliases) == len(set(aliases))
    assert {fact['fact_alias'] for fact in public['chapter']['facts']} == set(aliases)


def test_split_chapter_compilation_and_assembly_equal_legacy_whole_book():
    truth, mapping = build_blind_projection(_blueprints(), root_clean_sha1=ROOT)
    legacy_response = _typed_candidate(mapping)
    legacy = compile_blind_candidate(legacy_response, mapping, truth=truth)
    strategy = BlindBookStrategyResponse.model_validate(
        _split_strategy(mapping)).model_dump(mode='json')
    truth_by_chapter = {
        row['chapter_ordinal']: row for row in truth['chapters']
    }
    source_by_chapter = {
        row['output_ordinal']: row for row in legacy_response['output_chapters']
    }
    weight_by_chapter = {
        row['chapter_ordinal']: row['weight'] for row in strategy['chapter_weights']
    }

    compiled = []
    for chapter in sorted(source_by_chapter):
        _public, local_mapping = build_blind_chapter_projection(
            truth, mapping, chapter_ordinal=chapter, root_clean_sha1=ROOT)
        local = BlindChapterDesignResponse.model_validate(
            _local_chapter_response(source_by_chapter[chapter], local_mapping)
        ).model_dump(mode='json')
        compiled.append(compile_blind_chapter_design(
            local, local_mapping, mapping,
            truth_chapter=truth_by_chapter[chapter],
            chapter_weight=weight_by_chapter[chapter],
        ))

    split = assemble_blind_candidate(
        strategy, list(reversed(compiled)), mapping, truth=truth)
    assert split == legacy
    assert candidate_subject_sha256(split) == candidate_subject_sha256(legacy)
    assert [row['output_ordinal'] for row in split['output_chapters']] == [1, 2]
    for chapter in split['output_chapters']:
        BlindCandidateChapter.model_validate(chapter)


def test_split_compiler_and_assembler_close_unknown_alias_and_chapter_sets():
    truth, mapping = build_blind_projection(_blueprints(), root_clean_sha1=ROOT)
    legacy_response = _typed_candidate(mapping)
    truth_by_chapter = {
        row['chapter_ordinal']: row for row in truth['chapters']
    }
    source = next(
        row for row in legacy_response['output_chapters']
        if row['output_ordinal'] == 1)
    _public, local_mapping = build_blind_chapter_projection(
        truth, mapping, chapter_ordinal=1, root_clean_sha1=ROOT)
    local = _local_chapter_response(source, local_mapping)
    local['chapter_goal']['fact_aliases'] = ['f_000000']
    with pytest.raises(BlindPlanError, match='未知 local fact alias'):
        compile_blind_chapter_design(
            local, local_mapping, mapping,
            truth_chapter=truth_by_chapter[1], chapter_weight='CH_EVEN')

    good_local = _local_chapter_response(source, local_mapping)
    compiled = compile_blind_chapter_design(
        good_local, local_mapping, mapping,
        truth_chapter=truth_by_chapter[1], chapter_weight='CH_EVEN')
    missing_weight = {
        'reconstruction_strategy': '按章级功能协调全书事实组织。',
        'chapter_weights': [{'chapter_ordinal': 1, 'weight': 'CH_EVEN'}],
    }
    with pytest.raises(BlindPlanError, match='chapter_weights 章集不闭合'):
        assemble_blind_candidate(
            missing_weight, [compiled], mapping, truth=truth)

    complete_weights = {
        'reconstruction_strategy': '按章级功能协调全书事实组织。',
        'chapter_weights': [
            {'chapter_ordinal': 1, 'weight': 'CH_MAJOR'},
            {'chapter_ordinal': 2, 'weight': 'CH_EVEN'},
        ],
    }
    with pytest.raises(BlindPlanError, match='weight 与整书策略不一致'):
        assemble_blind_candidate(
            complete_weights, [compiled], mapping, truth=truth)


def test_blind_candidate_accepts_stage2_fact_ids_beyond_two_digit_chapters():
    """Blind join 复用 Stage2 正式 ID 契约，不私造两位章号上限。"""
    blueprint = {
        'blueprints': [{
            'chapter_ordinal': 100,
            'summary': '不得进入 A 的源摘要。',
            'chapter_function': '验证长章号的去序绑定',
            'fact_atoms': [
                _atom(100, 1, '甲在第一百章做出决定', participants=['char_100']),
            ],
            'causal_edges': [],
            'entry_state_refs': ['f100_001'],
            'ending_state_refs': ['f100_001'],
            'beats': [_beat(100, 1, ['char_100'], hinge=False)],
            'narration': [],
        }],
    }
    truth, mapping = build_blind_projection(blueprint, root_clean_sha1=ROOT)
    alias = _fact_aliases(mapping)['f100_001']
    response = {
        'reconstruction_strategy': '线性短章保留因果顺序。',
        'output_chapters': [{
            'output_ordinal': 100,
            'structure_decision': {
                'mode': 'retain',
                'reason': '单一事实没有可安全重排的事件对。',
                'declared_change_kinds': [],
            },
            'chapter_goal': _goal(alias, 'G_ADVANCE'),
            'open_hook_goal': _goal(alias, 'G_OPEN'),
            'end_hook_goal': _goal(alias, 'G_CLOSE'),
            'weight': 'CH_EVEN',
            'chapter_rewrite_brief': '直接执行该决定。',
            'scenes': [_scene(
                [alias], alias, purpose='落地决定', role='R_PAYOFF',
                transition='X_START', exit_refs=[alias],
            )],
        }],
    }

    compiled = compile_blind_candidate(
        BlindPlanResponse.model_validate(response).model_dump(mode='json'),
        mapping, truth=truth)

    chapter = compiled['output_chapters'][0]
    assert chapter['scenes'][0]['candidate_scene_id'] == 'c100s01'
    assert chapter['scenes'][0]['land_fact_aliases'] == [alias]
    BlindCandidateChapter.model_validate(chapter)


def test_candidate_compiler_accepts_formal_a_shape_and_injects_scene_ids():
    compiled, mapping, _catalog = _compiled()

    assert [row['output_ordinal'] for row in compiled['output_chapters']] == [1, 2]
    assert compiled['fact_aliases'] == mapping['fact_aliases']
    assert compiled['participant_aliases'] == mapping['participant_aliases']
    ch1 = compiled['output_chapters'][0]
    assert [scene['candidate_scene_id'] for scene in ch1['scenes']] == [
        'c01s01', 'c01s02',
    ]
    assert ch1['scenes'][0]['land_fact_aliases'] == [
        _fact_aliases(mapping)['f01_002'],
    ]
    assert ch1['structure_decision'] == {
        'mode': 'reconstruct',
        'reason': '用拒绝作入口，并把邀请与最终选择重新组合。',
        'declared_change_kinds': ['entry_anchor', 'fact_grouping'],
    }
    for chapter in compiled['output_chapters']:
        BlindCandidateChapter.model_validate(chapter)
    assert re.fullmatch(r'[0-9a-f]{64}', candidate_subject_sha256(compiled))


def test_binding_merge_accepts_formal_b_shape_and_preserves_a_order():
    compiled, _mapping, catalog = _compiled()
    response = _typed_bindings(compiled)
    merged = merge_evidence_bindings(compiled, response, catalog)

    ch1 = merged['output_chapters'][0]
    assert [scene['turn_fact_ref'] for scene in ch1['scenes']] == [
        'f01_002', 'f01_003',
    ]
    assert ch1['scenes'][0]['stage_refs'] == ['b01_02']
    assert ch1['scenes'][1]['stage_refs'] == ['b01_01', 'b01_03']
    assert all('candidate_scene_id' not in scene
               for chapter in merged['output_chapters'] for scene in chapter['scenes'])
    assert ch1['structure_decision'] == compiled['output_chapters'][0]['structure_decision']

    assert ch1['structure_decision']['mode'] == 'reconstruct'
    assert 'computed_change_kinds' not in ch1['structure_decision'], (
        'B 只绑证据；代码场景编译器才能复算最终结构读数')


def test_full_b_compile_accepts_a_context_backref_to_a_prior_landed_fact():
    """A 候选 → B join → allocator 的真实编译链共用 reader-knowledge 口径。"""
    truth, mapping = build_blind_projection(_blueprints(), root_clean_sha1=ROOT)
    candidate = _candidate(mapping)
    aliases = _fact_aliases(mapping)
    chapter = next(row for row in candidate['output_chapters']
                   if row['output_ordinal'] == 1)
    chapter['scenes'][1]['context_fact_aliases'] = [aliases['f01_002']]
    compiled = compile_blind_candidate(
        BlindPlanResponse.model_validate(candidate).model_dump(mode='json'),
        mapping, truth=truth)

    binding = _typed_bindings(compiled)
    merged = merge_evidence_bindings(
        compiled, binding, build_evidence_catalog(_blueprints()))
    adopt_plan_response(merged, _blueprints(), {})

    compiled_chapter = next(row for row in merged['output_chapters']
                            if row['output_ordinal'] == 1)
    assert compiled_chapter['scenes'][1]['context_fact_refs'] == ['f01_002']
    assert all(scene['budget_chars'] > 0 for row in merged['output_chapters']
               for scene in row['scenes'])


def test_candidate_hash_mismatch_is_closed_before_any_merge():
    compiled, _mapping, catalog = _compiled()
    response = _typed_bindings(compiled)
    response['candidate_sha256'] = '0' * 64
    with pytest.raises(BlindPlanError, match='候选 hash 不符'):
        merge_evidence_bindings(compiled, response, catalog)


@pytest.mark.parametrize('mutation, message', [
    ('missing', '漏绑候选场景'),
    ('duplicate', '重复绑定'),
    ('unknown_scene', '未知 candidate_scene_id'),
])
def test_binding_scene_coverage_is_closed(mutation: str, message: str):
    compiled, _mapping, catalog = _compiled()
    response = _typed_bindings(compiled)
    ch1 = _chapter_binding(response, 1)
    if mutation == 'missing':
        ch1['scenes'].pop()
    elif mutation == 'duplicate':
        ch1['scenes'].append(copy.deepcopy(ch1['scenes'][0]))
    else:
        ch1['scenes'][0]['candidate_scene_id'] = 'c99s99'
    with pytest.raises(BlindPlanError, match=message):
        merge_evidence_bindings(compiled, response, catalog)


def test_b_cannot_echo_or_change_any_structural_field():
    compiled, _mapping, catalog = _compiled()
    response = _typed_bindings(compiled)
    _chapter_binding(response, 1)['scenes'][0]['purpose'] = 'B 试图改写场景目的'
    with pytest.raises(BlindPlanError, match='协议外字段.*purpose'):
        merge_evidence_bindings(compiled, response, catalog)


def test_b_cannot_move_a_scene_to_another_chapter():
    compiled, _mapping, catalog = _compiled()
    response = _typed_bindings(compiled)
    ch1 = _chapter_binding(response, 1)
    ch2 = _chapter_binding(response, 2)
    ch2['scenes'].append(ch1['scenes'].pop())
    with pytest.raises(BlindPlanError, match='放进了第 2 章绑定'):
        merge_evidence_bindings(compiled, response, catalog)


@pytest.mark.parametrize('field, unknown', [
    # b02_01 是存在的正式引用，但属于另一章；不能只做全书集合 membership。
    ('stage_refs', 'b02_01'),
    ('material_refs', 'h01_999'),
])
def test_b_unknown_or_cross_chapter_evidence_refs_are_closed(field: str, unknown: str):
    compiled, _mapping, catalog = _compiled()
    response = _typed_bindings(compiled)
    _chapter_binding(response, 1)['scenes'][0][field] = [unknown]
    with pytest.raises(BlindPlanError, match=f'{field} 含未知引用'):
        merge_evidence_bindings(compiled, response, catalog)


def test_unknown_alias_and_model_owned_scene_id_are_closed_in_a_compiler():
    truth, mapping = build_blind_projection(_blueprints(), root_clean_sha1=ROOT)
    candidate = _typed_candidate(mapping)
    candidate['output_chapters'][1]['scenes'][0]['context_fact_aliases'] = [
        'a_' + '0' * 16,
    ]
    with pytest.raises(BlindPlanError, match='未知事实别名'):
        compile_blind_candidate(candidate, mapping, truth=truth)

    candidate = _typed_candidate(mapping)
    candidate['output_chapters'][1]['scenes'][0]['candidate_scene_id'] = 'c01s99'
    with pytest.raises(BlindPlanError, match='协议外字段.*candidate_scene_id'):
        compile_blind_candidate(candidate, mapping, truth=truth)
    with pytest.raises(ValidationError, match='candidate_scene_id'):
        BlindPlanResponse.model_validate(candidate)


def test_infeasible_b_is_a_typed_terminal_and_never_partially_compiles():
    compiled, _mapping, catalog = _compiled()
    response = EvidenceBindingResponse.model_validate({
        'status': 'infeasible',
        'candidate_sha256': candidate_subject_sha256(compiled),
        'chapters': None,
        'binding_concerns': [{
            'constraint_code': 'future_leak',
            'chapter_ordinal': 2,
            'scenes': ['c02s01'],
            'reason': '候选场景只能靠未来章证据成立。',
            'closed_refs': ['c02s01', 'b02_01'],
        }],
    }).model_dump(mode='json')
    with pytest.raises(BlindPlanError, match='只有 status=bound'):
        merge_evidence_bindings(compiled, response, catalog)
    with pytest.raises(BlindPlanError, match='构造出了完整 bound'):
        validate_infeasible_binding(compiled, response, catalog)
    assert prove_binding_infeasible(compiled, catalog) == []


def test_declared_change_kinds_are_canonicalized_instead_of_rejected():
    """集合语义由代码归一，不拿一次全书重出去换一个排序。

    ⚠ 反方向同样要成立：**非法枚举不许被归一顺手洗掉**。归一只处理
    「取值都合法、只是重复或乱序」这一种输入，其余原样交给 pydantic。
    """
    _truth, mapping = build_blind_projection(_blueprints(), root_clean_sha1=ROOT)
    candidate = _typed_candidate(mapping)
    index, decision = next(
        (position, chapter['structure_decision'])
        for position, chapter in enumerate(candidate['output_chapters'])
        if chapter['structure_decision']['mode'] == 'reconstruct')
    decision['declared_change_kinds'] = [
        'fact_grouping', 'entry_anchor', 'fact_grouping']

    parsed = BlindPlanResponse.model_validate(candidate)
    assert parsed.output_chapters[index].structure_decision.declared_change_kinds         == ['entry_anchor', 'fact_grouping']

    decision['declared_change_kinds'] = ['entry_anchor', 'not_a_change_kind']
    with pytest.raises(ValidationError):
        BlindPlanResponse.model_validate(candidate)

    # 归一之后 retain / reconstruct 那两条业务闸一格没松。
    decision['declared_change_kinds'] = ['fact_grouping', 'fact_grouping']
    with pytest.raises(ValidationError, match='至少两类'):
        BlindPlanResponse.model_validate(candidate)


def test_constructive_binding_witness_refutes_false_infeasible_and_concerns_do_not_block():
    compiled, _mapping, catalog = _compiled()
    witness = construct_feasible_binding_witness(compiled, catalog)
    parsed = EvidenceBindingResponse.model_validate(witness)
    assert parsed.status == 'bound'
    assert merge_evidence_bindings(compiled, witness, catalog)['output_chapters']

    concerned = copy.deepcopy(witness)
    concerned['binding_concerns'] = [{
        'constraint_code': 'semantic_ambiguity',
        'chapter_ordinal': 1,
        'scenes': ['c01s01'],
        'reason': '这一场的语义键定仍需人工注意。',
        'closed_refs': ['c01s01', 'b01_01'],
    }]
    assert merge_evidence_bindings(compiled, concerned, catalog) \
        == merge_evidence_bindings(compiled, witness, catalog)

    unknown = copy.deepcopy(concerned)
    unknown['binding_concerns'][0]['closed_refs'] = ['b01_999']
    with pytest.raises(BlindPlanError, match='未下发引用'):
        merge_evidence_bindings(compiled, unknown, catalog)

    # 整书 legacy 闭集仍只接受 opaque alias；章级路径必须在聚合前规范化，
    # 不能把正式 fact id 偷加进这把全书尺。
    formal_ref = copy.deepcopy(concerned)
    formal_ref['binding_concerns'][0]['closed_refs'] = [
        next(iter(_mapping['fact_aliases'].values()))]
    with pytest.raises(BlindPlanError, match='未下发引用'):
        merge_evidence_bindings(compiled, formal_ref, catalog)


def test_deterministic_infeasibility_witness_only_covers_closed_code_constraints():
    compiled, _mapping, catalog = _compiled()
    broken_catalog = copy.deepcopy(catalog)
    broken_catalog[1]['stage_refs'] = []
    proof = prove_binding_infeasible(compiled, broken_catalog)
    assert len(proof) == 1
    assert proof[0]['constraint_code'] == 'empty_stage_allowlist'
    assert proof[0]['chapter_ordinal'] == 1
    assert proof[0]['scenes'] == ['c01s01', 'c01s02']
    assert proof[0]['closed_refs'] == ['c01s01', 'c01s02']


def test_nineteen_chapter_requests_only_invalidate_changed_fact_chapter():
    from types import SimpleNamespace
    from contracts.schema.stage4 import BlindBookStrategyResponse, BlindChapterDesignResponse
    from novel.stages.stage4 import plan

    blueprints = {'blueprints': [{
        'chapter_ordinal': ordinal, 'chapter_function': '闭合本章因果。',
        'fact_atoms': [_atom(ordinal, 1, '甲抵达门口。', participants=['char_001'])],
        'causal_edges': [], 'narrative_relations': [],
        'entry_state_refs': [], 'ending_state_refs': [],
        'beats': [_beat(ordinal, 1, ['char_001'], hinge=False)], 'narration': [],
    } for ordinal in range(1, 20)]}
    strategy = BlindBookStrategyResponse.model_validate({
        'reconstruction_strategy': '逐章闭合因果。',
        'chapter_weights': [{'chapter_ordinal': n, 'weight': 'CH_EVEN'} for n in range(1, 20)],
    })
    ctx = SimpleNamespace(root_clean_sha1=ROOT)

    def requests():
        truth, mapping = build_blind_projection(blueprints, root_clean_sha1=ROOT)
        prepared = SimpleNamespace(
            truth=truth, mapping=mapping,
            rebuild_parent=None,
            input_sha256=plan._canonical_sha256(truth),
            validator=plan._BlindStrategyValidator(tuple(range(1, 20))))
        return plan._design_chapter_contexts(ctx, prepared, strategy, ensure_units=False)

    before = requests()
    blueprints['blueprints'][16]['fact_atoms'][0]['claim'] = '甲拒绝进入门口。'
    after = requests()
    changed = [new.chapter_ordinal for old, new in zip(before, after, strict=True)
               if old.input_sha256 != new.input_sha256]
    assert changed == [17]
    assert before[0].local_mapping['truth_sha256'] != after[0].local_mapping['truth_sha256']
    assert before[0].strategy_epoch_sha256 == after[0].strategy_epoch_sha256
    for old, new in zip(before, after, strict=True):
        if old.chapter_ordinal != 17:
            assert (old.system, old.user) == (new.system, new.user)
        # 相同历史回答必须仍可由当前编译器校验，不能仅凭输入摘要相同即信任。
        from tests.test_stage4_v3_integration import _split_a_responder
        response = _split_a_responder(blueprints)(SimpleNamespace(system=new.system, user=new.user))
        typed = BlindChapterDesignResponse.model_validate(response)
        assert new.validator(typed, None) is None
