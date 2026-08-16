#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage6 质量升级的生成契约与完整材料面。"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from contracts.invariants import HOOK_APPROACHES
from contracts.schema.stage6 import HookResponse
from novel.stages.stage6 import checks, prompts


def _candidate_payload(approaches=HOOK_APPROACHES, texts=None):
    texts = texts or ['第一条具体文案。', '第二条具体文案。', '第三条具体文案。']
    return {
        'candidates': [
            {'approach': approach, 'text': text}
            for approach, text in zip(approaches, texts)
        ],
        'rationale': '三条分别保结构、预告兑现和用场面收尾。',
    }


def test_current_generation_approaches_are_the_ab_tested_three():
    assert HOOK_APPROACHES == (
        'source_architecture', 'payoff_preview', 'scene_punchline')


def test_model_response_requires_each_current_approach_once_and_unique_texts():
    response = HookResponse.model_validate(_candidate_payload())
    assert tuple(candidate.approach for candidate in response.candidates) == HOOK_APPROACHES

    duplicate_approach = _candidate_payload(
        ('source_architecture', 'source_architecture', 'scene_punchline'))
    with pytest.raises(ValidationError, match='各覆盖一次'):
        HookResponse.model_validate(duplicate_approach)

    duplicate_text = _candidate_payload(texts=['同一条。', '同一条。', '第三条。'])
    with pytest.raises(ValidationError, match='不得重复'):
        HookResponse.model_validate(duplicate_text)

    unsupported_response = _candidate_payload(('first', 'second', 'third'))
    with pytest.raises(ValidationError):
        HookResponse.model_validate(unsupported_response)


def test_generation_system_prioritizes_retention_and_removes_spoiler_gate():
    system = prompts.hook_system()
    assert '第一目标只有一个：让目标读者看完文案后立刻继续读第一章' in system
    assert '原始文案是创作金标准' in system
    assert '剧透不是违规' in system
    assert 'surface_habits' in system
    assert '不得取后期高潮或结局' not in system
    assert '故事最终如何收场' not in system
    for approach in HOOK_APPROACHES:
        assert approach in system


def test_material_carries_source_full_truth_story_map_and_body_habits():
    ctx = {
        'chapters': [
            {
                'output_ordinal': 1,
                'from_source_ordinals': [2],
                'chapter_goal': {'intent': 'advance', 'facts': ['事件：收到信。']},
                'open_hook_goal': {'intent': 'open', 'facts': ['事件：从匿名信开场。']},
                'end_hook_goal': {'intent': 'close', 'facts': ['事件：发现落款。']},
                'segments': [{
                    'slot': 's01',
                    'events': ['她在枕下发现匿名信。', '信的落款是仇人的名字。'],
                }],
            },
            {
                'output_ordinal': 2,
                'from_source_ordinals': [3],
                'chapter_goal': {'intent': 'advance', 'facts': ['事件：追查写信者。']},
                'open_hook_goal': {'intent': 'open', 'facts': ['事件：当面对质。']},
                'end_hook_goal': {'intent': 'close', 'facts': ['事件：对方承认一半。']},
                'segments': [{'slot': 's01', 'events': ['她去城门找人。']}],
            },
        ],
    }
    style = {
        'hook_style': {'relation_to_body': '更高浓度'},
        'voice': {'pace': '快', 'mode_variation': {'danger': '句子更短'}},
        'surface_habits': ['一句一段', '用人物反应收尾'],
        'chapter_styles': [
            {'source_ordinal': 2, 'voice': {'stance': '冷'}},
            {'source_ordinal': 3, 'voice': {'stance': '急'}},
            {'source_ordinal': 9, 'voice': {'stance': '远期'}},
        ],
        'measurable': {
            'hook': {
                'chars': 100,
                'paragraphs': 8,
                'sentences': 10,
                'avg_sentence_chars': 10,
                'avg_paragraph_chars': 12,
                'paragraph_sep_chars': 2,
                'dialogue_ratio': 0.2,
                'dialogue_ratio_applicable': True,
                'quote_styles': ['“”'],
            },
        },
    }
    grounding = {
        'entities': {'characters': {'c1': {'canonical': '阿宁'}}},
        'canon': {'secrets': [{'secret_id': 's1', 'fact': '写信者是仇人。'}]},
        'arc': {'climax': '她当众烧信。', 'ending': '两人联手破局。'},
    }
    material = prompts.build_material(
        title='匿名信',
        ctx=ctx,
        style=style,
        grounding=grounding,
        source_hook='原作文案先抛匿名信，再用仇人落款收尾。',
        lead=[{'output_ordinal': 1, 'text': '重写后的第一章正文。'}],
        missing=[],
        upto=1,
    )
    user = prompts.build_user(material)
    packet = json.loads(user.split('\n\n', 1)[1])
    assert packet['source_hook'].startswith('原作文案')
    assert packet['story_truth'] == grounding
    assert packet['whole_story_map'][1]['events'] == ['她去城门找人。']
    assert packet['whole_story_map'][0]['segments'][0]['slot'] == 's01'
    assert packet['whole_story_map'][0]['chapter_goal'] == {
        'intent': 'advance',
        'facts': ['事件：收到信。'],
    }
    assert 'fact_refs' not in packet['whole_story_map'][0]['chapter_goal']
    assert packet['story_truth']['arc']['ending'] in user
    assert packet['style']['book_surface_habits'] == style['surface_habits']
    assert packet['style']['book_voice']['mode_variation'] == {'danger': '句子更短'}
    assert [item['source_ordinal'] for item in packet['style']['opening_chapter_styles']] == [2]
    assert packet['rewritten_opening'][0]['text'] == '重写后的第一章正文。'


def test_source_copy_guard_now_rejects_any_twenty_content_character_window():
    source = '甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥天地玄黄。'
    text = source + '这条文案继续写到足够长并以完整句子收束。'
    findings = checks.hook_findings(
        text,
        mat=prompts.Material(blocks=('完整故事真相。',)),
        secrets=[],
        arc=None,
        characters={},
        src_hook=source,
        style_hook=None,
        quote_problems=[],
    )
    reuse = [item for item in findings if item.rule == 'hook_reuses_source']
    assert reuse and checks.REUSE_MAX == 0.0
