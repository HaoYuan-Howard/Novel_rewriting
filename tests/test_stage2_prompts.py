#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 2 当前模型提示与响应契约。"""
from __future__ import annotations

from contracts.invariants import (
    ADDRESS_BY, ADDRESS_CONTEXT, CHANGE_TYPES_ENUM, HINGE_KINDS, NAME_DISTINCTIVENESS,
    NARRATIVE_PERSON, TIME_LAYERS, WORLD_RULE_KINDS,
)
from contracts.schema.stage2 import AnchorLocatorResponse, HingeResponse
from novel.stages.stage2 import prompts


def test_all_three_current_prompt_arms_are_rendered():
    assert prompts.GLOBAL_SYS.startswith(prompts.RULES_V2)
    assert prompts.BLUEPRINT_SYS.startswith(prompts.RULES_V2)
    assert prompts.ROLE_FATE_RULE in prompts.GLOBAL_SYS
    assert prompts.ROLE_FATE_RULE in prompts.ROLE_SPLIT_SYS
    assert '$' not in prompts.GLOBAL_SYS + prompts.BLUEPRINT_SYS + prompts.ROLE_SPLIT_SYS


def test_global_prompt_requires_an_explicit_exact_chapter_partition():
    assert '每一章逐个显式列全' in prompts.GLOBAL_SYS
    assert '每章恰好出现一次' in prompts.GLOBAL_SYS


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
    """章首章尾必须被第一/最后一拍盖住 —— 这条 prompt 规矩压着 5 处实测的章首漏抓。

    ⚠ 措辞随 `bounded-fidelity-v3` 从「照抄第一句话」改成「段号必须是 1」，
    **但要求本身一个字没松**；`edge_hole` 那条 HARD 仍然是它的真正守卫
    （prompt 写了不等于遵守率有保证，见 `contracts/judges/stage2.py` 那条 note）。
    """
    rule = '【但空隙只能在中间，两头不许留】'
    head = '第一条的 start_para 必须是 1'
    tail = '最后一条的 end_para 必须是本章最后一段的段号'
    for probe in (rule, head, tail):
        assert probe in prompts.BLUEPRINT_SYS
    assert prompts.BLUEPRINT_SYS.index('两条之间允许有空隙') < prompts.BLUEPRINT_SYS.index(rule)


def test_the_prompt_asks_for_paragraph_numbers_not_transcribed_source():
    """⭐⭐ 本次改造的**全部要点**：模型不再转写原文，它交段号。

    ⚠ 反向断言那几个词是承重的：只要 `starts_with` / 「原样照抄 N 字」这类措辞还在，
    就说明有人把逐字转写那条路补回来了一半——而那正是这次要砍掉的 reasoning 大头。
    """
    sys_prompt = prompts.BLUEPRINT_SYS
    for gone in ('starts_with', 'ends_with', '原样照抄 8~30', '各 4~8 字', '凑长度'):
        assert gone not in sys_prompt, f'{gone!r} 还在 —— 逐字转写那条路被补回来了'
    for wanted in ('<数字>', 'start_para', 'end_para', '"para"', '"phrase"'):
        assert wanted in sys_prompt or wanted.strip('"') in sys_prompt, wanted
    # 编号只在当前章：过去窗口是节选，指它的段号一定是错的
    assert '段号只在当前章全文里有' in sys_prompt


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
    # ⚠ 形状**从响应模型现算**，不在这里手抄一份键名：手抄的那份与
    #   `contracts/schema/stage2.py` 分家时，本条会继续绿着，而模型会收到一份
    #   `extra='forbid'` 当场拒收的提示（`docs/LESSONS.md` 第 21 条）。
    assert set(beat['span']) == set(AnchorLocatorResponse.model_fields)
    assert set(beat['hinges'][0]) == set(HingeResponse.model_fields)
