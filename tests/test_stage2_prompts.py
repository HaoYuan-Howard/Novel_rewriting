#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 2 当前模型提示与响应契约。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contracts.invariants import (
    BARE_PRONOUNS,
    KNOWLEDGE_SUBJECT_KINDS, NARRATIVE_PERSON, RELATION_VISIBILITY_KINDS,
    TIME_LAYERS, WORLD_RULE_KINDS,
)
from contracts.schema.knowledge import KnowledgeAgentTurnResponse, KnowledgeResponse
from novel.stages.stage2 import characters, knowledge_prompts, plot_prompts as prompts
from contracts.schema.plot import PlotBlueprintResponse
from tests.test_plot_blueprint import chapter_response


def test_all_current_prompt_arms_are_rendered():
    assert prompts.response_system().startswith(prompts.BLUEPRINT_SYSTEM)
    assert '静态人物登记' in characters.SYSTEM and '不写经历、结局' in characters.SYSTEM
    assert '不得新增人物 ID' in knowledge_prompts.SYSTEM
    # JSON Schema 的 $defs/$ref 是正式引用，不是未渲染模板。
    assert '$' not in knowledge_prompts.SYSTEM + characters.SYSTEM + prompts.BLUEPRINT_SYSTEM


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
    for surface in (prompts.BLUEPRINT_SYSTEM, str(PlotBlueprintResponse.model_json_schema())):
        for gone in ('last_beat_id', 'narrator_name', 'focalizer_name',
                     'third_limited', 'omniscient', 'fact_atoms', 'hinges'):
            assert gone not in surface


def test_person_values_match_the_current_contract():
    assert NARRATIVE_PERSON == ('first', 'second', 'third')
    definitions = PlotBlueprintResponse.model_json_schema()['$defs']
    assert definitions['PlotNarration']['properties']['grammatical_person']['enum'] == list(NARRATIVE_PERSON)


def test_source_boundaries_are_semantic_not_forced_evidence_partition():
    system = prompts.BLUEPRINT_SYSTEM
    assert '独立重要事件、时间或揭示边界' in system
    assert '不设节点数或字数配额' in system
    assert 'Stage4 会处理呈现安排' in system
    assert '同一段可支持多个节点' in system
    assert '只读全部 content' in system
    assert 'modality 不授予他人知情或读者披露' in system


def test_the_prompt_asks_for_paragraph_numbers_not_transcribed_source():
    system = prompts.BLUEPRINT_SYSTEM
    for gone in ('starts_with', 'ends_with', '原样照抄 8~30', '各 4~8 字'):
        assert gone not in system
    assert '证据只选编号的段范围' in system
    assert '不能抄写正文或编造坐标' in system
    assert '正式 ID 与证据锚点由程序分配' in system


def test_response_schema_derives_enums_from_the_contract_layer():
    definitions = PlotBlueprintResponse.model_json_schema()['$defs']
    properties = definitions['SourceContextResponse']['properties']
    time_layer = properties['time_layer']['anyOf'][0]['enum']
    assert time_layer == list(TIME_LAYERS)
    assert knowledge_prompts.HINT['canon']['world_rules'][0]['kind'] == WORLD_RULE_KINDS[0]
    assert set(knowledge_prompts.HINT['source_blurb']['facts'][0]) == {
        'kind', 'claim_id', 'claim', 'evidence'}


def test_response_schema_only_requests_model_written_fields():
    response = PlotBlueprintResponse.model_validate({'blueprints': [chapter_response()]})
    row = response.blueprints[0].model_dump()
    assert {'narrator_char_id', 'focalizer_char_id'} <= row['narration'][0].keys()
    assert {'content', 'participant_char_ids', 'evidence', 'source_contexts'} <= row['beats'][0].keys()
    surface = str(PlotBlueprintResponse.model_json_schema())
    for field in ('narrator_name', 'focalizer_name', 'present_names', 'evidence_id',
                  'source_sha256', 'source_text', 'fact_atoms'):
        assert field not in surface
