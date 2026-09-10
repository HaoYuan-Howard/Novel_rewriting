#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 2 当前模型提示与响应契约。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contracts.invariants import (
    BARE_PRONOUNS, HINGE_KINDS,
    KNOWLEDGE_SUBJECT_KINDS, NARRATIVE_PERSON, RELATION_VISIBILITY_KINDS,
    RESPONSE_TIME_LAYERS, WORLD_RULE_KINDS,
)
from contracts.schema.knowledge import KnowledgeAgentTurnResponse, KnowledgeResponse
from novel.stages.stage2 import characters, knowledge_prompts, prompts


def test_all_current_prompt_arms_are_rendered():
    assert prompts.BLUEPRINT_SYS_V2_REFS.startswith(prompts.BLUEPRINT_RULES)
    assert prompts.BLUEPRINT_SYS_V31.startswith(prompts.BLUEPRINT_RULES)
    assert prompts.BLUEPRINT_SYS_V32.startswith(prompts.BLUEPRINT_RULES)
    assert '静态人物登记' in characters.SYSTEM and '不写经历、结局' in characters.SYSTEM
    assert '不得新增人物 ID' in knowledge_prompts.SYSTEM
    rendered = (knowledge_prompts.SYSTEM + characters.SYSTEM + prompts.BOUNDARY_SYS_V2
                + prompts.BLUEPRINT_SYS_V2_REFS + prompts.BLUEPRINT_SYS_V31
                + prompts.BLUEPRINT_SYS_V32)
    assert '$' not in rendered


def test_character_prompt_uses_the_complete_pronoun_contract_and_retired_arms_are_absent():
    assert all(f'「{word}」' in characters.SYSTEM for word in BARE_PRONOUNS)
    for retired in ('GLOBAL_CANDIDATE_SYS', 'GLOBAL_CANDIDATE_HINT',
                    'ROLE_SPLIT_SYS', 'ROLE_SPLIT_HINT', 'ROLE_FATE_RULE'):
        assert not hasattr(prompts, retired)


def test_global_prompt_requires_evidence_bound_opening_without_default_chapter():
    assert 'source_blurb' in knowledge_prompts.SYSTEM
    assert 'boundary_evidence' in knowledge_prompts.SYSTEM
    assert 'unresolved' in knowledge_prompts.SYSTEM
    assert '不能默认第 1 章' in knowledge_prompts.SYSTEM
    assert 'conflict_stages' not in knowledge_prompts.SYSTEM


def test_global_prompt_separates_opening_premises_from_later_hidden_truths():
    assert '第一章直接明牌的普通背景不列秘密' in knowledge_prompts.SYSTEM
    assert '最早知情' in knowledge_prompts.SYSTEM
    assert '世界内知情、读者揭示、事实发生三者分开' in knowledge_prompts.SYSTEM
    assert '范围不是秘密披露授权' in knowledge_prompts.SYSTEM


def test_global_prompt_requests_typed_visibility_and_candidate_ids_only():
    assert '读者知情单独用 visibility' in knowledge_prompts.SYSTEM
    assert 'knowledge_events' in knowledge_prompts.SYSTEM
    assert 'character_known_by' not in knowledge_prompts.SYSTEM
    assert '本次表内 evidence_candidate_id' in knowledge_prompts.SYSTEM
    assert '不抄 locator、章号或 SourceSpan' in knowledge_prompts.SYSTEM

    hint = knowledge_prompts.HINT
    KnowledgeResponse.model_validate(hint)
    assert set(hint['entities']) == {'character_states', 'relations'}
    relations = hint['entities']['relations']
    assert [row['visibility']['kind'] for row in relations] == list(
        RELATION_VISIBILITY_KINDS)
    knowledge = hint['canon']['secrets'][0]['knowledge_events']
    assert [row['subject']['kind'] for row in knowledge] == list(KNOWLEDGE_SUBJECT_KINDS)

    candidate_ref_keys = {'evidence_candidate_id'}
    seven_families = [
        hint['entities']['character_states'][0]['fate_evidence'],
        relations[0]['predicate_evidence'],
        hint['canon']['setting']['era_evidence'],
        hint['canon']['world_rules'][0]['versions'][0]['desc_evidence'],
        hint['canon']['secrets'][0]['fact_evidence'],
        knowledge[0]['evidence'],
        hint['source_blurb']['opening']['boundary_evidence'],
    ]
    assert all(set(row) == candidate_ref_keys for row in seven_families)


def test_knowledge_response_cannot_restore_static_character_patch_protocol():
    with pytest.raises(ValidationError):
        KnowledgeAgentTurnResponse.model_validate({'kind': 'role_fate_patch',
            'characters': [{'char_id': 'char_001', 'role': '新身份', 'fate': None}]})


def test_removed_narration_fields_are_absent_from_model_surfaces():
    for surface in (prompts.BLUEPRINT_SYS_V2_REFS, prompts.BLUEPRINT_SYS_V31,
                    str(prompts.BLUEPRINT_HINT_V2_REFS),
                    str(prompts.BLUEPRINT_HINT_V31)):
        for gone in ('last_beat_id', 'narrator_name', 'focalizer_name',
                     'third_limited', 'omniscient'):
            assert gone not in surface


def test_person_values_match_the_current_contract():
    assert NARRATIVE_PERSON == ('first', 'second', 'third')
    narration = prompts.BLUEPRINT_HINT_V31['blueprints'][0]['narration'][0]
    assert narration['grammatical_person'] == NARRATIVE_PERSON[0]


def test_chapter_edges_are_required_by_the_current_prompt():
    """边界相必须从章首起步并到达章尾。"""
    assert '从第 1 段自动起步' in prompts.BOUNDARY_SYS_V2
    assert '到达本章最后一段' in prompts.BOUNDARY_SYS_V2


def test_the_prompt_asks_for_paragraph_numbers_not_transcribed_source():
    """⭐⭐ 本次改造的**全部要点**：模型不再转写原文，它交段号。

    ⚠ 反向断言那几个词是承重的：只要 `starts_with` / 「原样照抄 N 字」这类措辞还在，
    就说明有人把逐字转写那条路补回来了一半——而那正是这次要砍掉的 reasoning 大头。
    """
    sys_prompt = prompts.BOUNDARY_SYS_V2
    for gone in ('starts_with', 'ends_with', '原样照抄 8~30', '各 4~8 字', '凑长度'):
        assert gone not in sys_prompt, f'{gone!r} 还在 —— 逐字转写那条路被补回来了'
    assert 'end_para' in sys_prompt
    assert '本章最后一段' in sys_prompt


def test_response_hints_derive_enums_from_the_contract_layer():
    beat = prompts.BLUEPRINT_HINT_V31['blueprints'][0]['beats'][0]
    assert beat['time_layer'] == '|'.join(RESPONSE_TIME_LAYERS)
    assert beat['hinges'][0]['kind'] in HINGE_KINDS
    assert all(kind in prompts.BLUEPRINT_SYS_V31 for kind in HINGE_KINDS)
    assert 'dialogue' in beat['hinges'][0] and 'source_fact_refs' in beat['hinges'][0]
    assert knowledge_prompts.HINT[
        'canon']['world_rules'][0]['kind'] == WORLD_RULE_KINDS[0]
    assert set(knowledge_prompts.HINT['source_blurb']['facts'][0]) == {
        'kind', 'claim_id', 'claim', 'evidence'}


def test_response_hints_only_request_model_written_fields():
    def keys(value):
        if isinstance(value, dict):
            return set(value).union(*(keys(child) for child in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(child) for child in value))
        return set()

    surface = keys(prompts.BLUEPRINT_HINT_V31)
    for field in ('narrator_name', 'focalizer_name', 'present_names', 'source',
                  'summary'):
        assert field not in surface
    narration = prompts.BLUEPRINT_HINT_V31['blueprints'][0]['narration'][0]
    assert {'narrator_char_id', 'focalizer_char_id'} <= set(narration)
    beat = prompts.BLUEPRINT_HINT_V31['blueprints'][0]['beats'][0]
    assert 'present_char_ids' in beat and 'fact_atoms' in beat
