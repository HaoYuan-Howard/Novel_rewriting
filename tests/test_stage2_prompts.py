#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 2 当前模型提示与响应契约。"""
from __future__ import annotations

from contracts.invariants import (
    ADDRESS_BY, ADDRESS_CONTEXT, CHANGE_TYPES_ENUM, HINGE_KINDS, NAME_DISTINCTIVENESS,
    NARRATIVE_PERSON, TIME_LAYERS, WORLD_RULE_KINDS,
)
from novel.stages.stage2 import prompts


def test_all_three_current_prompt_arms_are_rendered():
    assert prompts.GLOBAL_SYS.startswith(prompts.RULES_V2)
    assert prompts.BLUEPRINT_SYS.startswith(prompts.RULES_V2)
    assert prompts.ROLE_FATE_RULE in prompts.GLOBAL_SYS
    assert prompts.ROLE_FATE_RULE in prompts.ROLE_SPLIT_SYS
    assert '$' not in prompts.GLOBAL_SYS + prompts.BLUEPRINT_SYS + prompts.ROLE_SPLIT_SYS


def test_removed_narration_fields_are_absent_from_model_surfaces():
    for surface in (prompts.BLUEPRINT_SYS, str(prompts.BLUEPRINT_HINT)):
        assert 'last_beat_id' not in surface
    assert 'third_limited' not in prompts.BLUEPRINT_SYS
    assert 'omniscient' not in prompts.BLUEPRINT_SYS


def test_person_values_match_the_current_contract():
    assert NARRATIVE_PERSON == ('first', 'second', 'third')
    assert '(first|second|third)' in prompts.BLUEPRINT_SYS
    assert prompts.BLUEPRINT_HINT['blueprints'][0]['narration'][0][
        'grammatical_person'] == NARRATIVE_PERSON[0]


def test_chapter_edges_are_required_by_the_current_prompt():
    rule = '【但空隙只能在中间，两头不许留】'
    head = '第一条的 starts_with 就是本章的第一句话'
    tail = '最后一条的 ends_with 就是本章的最后一句话'
    for probe in (rule, head, tail):
        assert probe in prompts.BLUEPRINT_SYS
    assert prompts.BLUEPRINT_SYS.index('两条之间允许有空隙') < prompts.BLUEPRINT_SYS.index(rule)


def test_response_hints_derive_enums_from_the_contract_layer():
    beat = prompts.BLUEPRINT_HINT['blueprints'][0]['beats'][0]
    assert beat['time_layer'] == '|'.join(TIME_LAYERS)
    assert beat['hinges'][0]['kind'] == '|'.join(HINGE_KINDS)
    character = prompts.GLOBAL_HINT['entities']['characters']['char_001']
    assert character['name_distinctiveness'] == '|'.join(NAME_DISTINCTIVENESS)
    assert character['address'][0]['context'] == '|'.join(ADDRESS_CONTEXT)
    assert character['address'][0]['by'] == 'char_002|' + '|'.join(
        value for value in ADDRESS_BY if value != 'char_id')
    assert prompts.GLOBAL_HINT['canon']['world_rules'][0]['kind'] == WORLD_RULE_KINDS[0]
    assert prompts.GLOBAL_HINT['arc']['arc_tracks'][0]['change_type'] == CHANGE_TYPES_ENUM


def test_response_hints_only_request_model_written_fields():
    surface = str(prompts.BLUEPRINT_HINT)
    for field in ('narrator_char_id', 'focalizer_char_id', 'present_char_ids', 'source'):
        assert field not in surface
    beat = prompts.BLUEPRINT_HINT['blueprints'][0]['beats'][0]
    assert set(beat['span']) == {'starts_with', 'ends_with'}
    assert set(beat['hinges'][0]) == {'kind', 'starts_with', 'ends_with'}
