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
    BlindCandidateChapter, BlindGoalResponse,
    BlindPlanResponse,
    EvidenceBindingResponse, EvidenceSceneBinding,
)
from novel.stages.stage4.blind import (
    BlindPlanError,
    build_blind_projection,
    build_evidence_catalog,
    candidate_subject_sha256,
    compile_blind_candidate,
    merge_evidence_bindings,
)


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
        'reason_kind': None,
        'reason': None,
        'evidence_refs': None,
    }


def _typed_bindings(compiled: dict) -> dict:
    return EvidenceBindingResponse.model_validate(
        _raw_bindings(compiled)).model_dump(mode='json')


def _compiled() -> tuple[dict, dict, dict]:
    _prompt, mapping = build_blind_projection(_blueprints(), root_clean_sha1=ROOT)
    compiled = compile_blind_candidate(_typed_candidate(mapping), mapping)
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
    _prompt, mapping = build_blind_projection(blueprint, root_clean_sha1=ROOT)
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
        BlindPlanResponse.model_validate(response).model_dump(mode='json'), mapping)

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
    _prompt, mapping = build_blind_projection(_blueprints(), root_clean_sha1=ROOT)
    candidate = _typed_candidate(mapping)
    candidate['output_chapters'][1]['scenes'][0]['context_fact_aliases'] = [
        'a_' + '0' * 16,
    ]
    with pytest.raises(BlindPlanError, match='未知事实别名'):
        compile_blind_candidate(candidate, mapping)

    candidate = _typed_candidate(mapping)
    candidate['output_chapters'][1]['scenes'][0]['candidate_scene_id'] = 'c01s99'
    with pytest.raises(BlindPlanError, match='协议外字段.*candidate_scene_id'):
        compile_blind_candidate(candidate, mapping)
    with pytest.raises(ValidationError, match='candidate_scene_id'):
        BlindPlanResponse.model_validate(candidate)


def test_infeasible_b_is_a_typed_terminal_and_never_partially_compiles():
    compiled, _mapping, catalog = _compiled()
    response = EvidenceBindingResponse.model_validate({
        'status': 'infeasible',
        'candidate_sha256': candidate_subject_sha256(compiled),
        'chapters': None,
        'reason_kind': 'future_leak',
        'reason': '候选场景只能靠未来章证据成立。',
        'evidence_refs': ['f02_001'],
    }).model_dump(mode='json')
    with pytest.raises(BlindPlanError, match='infeasible 是正式终态'):
        merge_evidence_bindings(compiled, response, catalog)
