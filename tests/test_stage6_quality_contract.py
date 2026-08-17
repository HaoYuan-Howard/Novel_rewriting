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


def test_interpolated_prompt_names_every_approach_and_has_no_placeholders():
    from contracts.invariants import HOOK_APPROACH_NOTES

    rendered = prompts.hook_system()
    for approach in HOOK_APPROACHES:
        assert approach in rendered
        assert HOOK_APPROACH_NOTES[approach] in rendered
    assert '$' not in rendered


def test_invented_number_compares_values_and_ignores_success_false_positive():
    mat = prompts.Material(blocks=('人物已经三十岁，做这件事有八成胜算。',))
    text = ('好感度一百成功。这位人物已经30岁，做这件事仍有八成胜算。'
            '所有事实都来自下发材料，这一句只是把文案补到正常长度。')
    findings = checks.hook_findings(
        text, mat=mat, secrets=[], arc=None, characters={}, src_hook=None,
        style_hook=None, quote_problems=[])
    assert not [finding for finding in findings
                if finding.rule == 'hook_invented_number']


def test_hook_secret_literal_uses_the_shared_number_ruler_and_own_bytes():
    fact = '她其实一直都知道他是三皇子'
    text = fact + '。这条文案继续写到足够长，并以完整句子收束。'
    findings = checks.hook_findings(
        text,
        mat=prompts.Material(blocks=('完整故事真相。',), upto=1),
        secrets=[{
            'secret_id': 'secret_x',
            'fact': fact,
            'reader_reveal_events': [{'chapter': 9}],
        }],
        arc=None,
        characters={},
        src_hook=None,
        style_hook=None,
        quote_problems=[],
    )
    literal = next(item for item in findings if item.rule == 'hook_secret_literal')
    assert '重合 1/1 条' in literal.detail
    assert literal.evidence
    assert all(piece in text for piece in literal.evidence)


def test_story_map_consumes_segments_keeps_slots_and_strips_private_rationale():
    goal = {
        'intent': 'advance',
        # Stage6 consumes the writer-facing CtxGoal.  Typed PlanGoal IDs have
        # already been resolved by Stage4 and must not be reintroduced here.
        'facts': ['她在门口收到一封匿名信。'],
        'rationale': '这是只供审计的自由理由，不得下发',
    }
    rows = prompts.story_map({'chapters': [{
        'output_ordinal': 1,
        'from_source_ordinals': [1],
        'chapter_goal': goal,
        'open_hook_goal': goal,
        'end_hook_goal': goal,
        'segments': [
            {'slot': 's01', 'events': ['事件一']},
            {'slot': 's02', 'events': ['事件二']},
        ],
    }]})
    assert rows[0]['events'] == ['事件一', '事件二']
    assert rows[0]['segments'] == [
        {'slot': 's01', 'events': ['事件一']},
        {'slot': 's02', 'events': ['事件二']},
    ]
    for key in ('chapter_goal', 'open_hook_goal', 'end_hook_goal'):
        assert rows[0][key] == {
            'intent': 'advance',
            'facts': goal['facts'],
        }
        assert 'rationale' not in rows[0][key]


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


def test_weak_fact_attribution_metric_compares_gift_phrase_to_story_map():
    material = prompts.Material(
        blocks=('完整创作包。',),
        story_truth={'entities': {'characters': {
            'c1': {'canonical': '阿宁', 'address': []},
        }}},
        story_map=({'chapter': 1, 'events': ['阿宁送给小禾玉佩。']},),
    )
    outcomes = checks.check(
        texts=[('候选', '阿宁送给小禾玉佩。', [])],
        mat=material, secrets=[], arc=None, lead_upto_ch=1, hook_chars=None,
        approved=False, stale=[], missing=[])
    metric = next(outcome for outcome in outcomes
                  if outcome.rule == 'hook_fact_attribution')
    assert metric.findings and '1 条' in metric.findings[0].detail
    assert '故事地图有同字面' in metric.findings[0].detail
    assert metric.findings[0].evidence == ['阿宁送给小禾玉佩']


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
    assert '最长公共子串 norm=' in reuse[0].detail
    assert '候选 raw[' in reuse[0].detail and '原作文案 raw[' in reuse[0].detail
    assert reuse[0].evidence and all(fragment in text for fragment in reuse[0].evidence)


def test_body_transcription_guard_reports_normalized_lcs_and_both_raw_ranges():
    lead = '甲，乙。丙！丁？戊；己：庚、辛壬癸子丑寅卯辰巳午未申酉戌亥。'
    text = lead + '这条候选继续补足篇幅并完整收束。'
    findings = checks.hook_findings(
        text,
        mat=prompts.Material(blocks=('完整故事真相。',), lead=({'text': lead},)),
        secrets=[],
        arc=None,
        characters={},
        src_hook=None,
        style_hook=None,
        quote_problems=[],
    )

    transcribed = [item for item in findings if item.rule == 'hook_transcribes_body']
    assert transcribed
    assert '最长公共子串 norm=' in transcribed[0].detail
    assert '候选 raw[' in transcribed[0].detail
    assert '开篇正文 raw[' in transcribed[0].detail
    assert transcribed[0].evidence
    assert all(fragment in text and fragment in lead for fragment in transcribed[0].evidence)


def test_lcs_ties_choose_the_earliest_candidate_then_reference_raw_span():
    detail, evidence = checks._lcs_detail(
        '甲，乙丙X甲乙丙', '甲、乙丙Y甲乙丙', reference_name='对侧')

    assert 'norm=3' in detail
    assert "候选 raw[0:4]='甲，乙丙'" in detail
    assert "对侧 raw[0:4]='甲、乙丙'" in detail
    assert evidence == ['甲，乙丙']
