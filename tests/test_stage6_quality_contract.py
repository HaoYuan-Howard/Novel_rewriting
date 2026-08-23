#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage6 质量升级的生成契约与完整材料面。"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from contracts.invariants import (
    HOOK_APPROACHES, HOOK_CURRENT_APPROACHES, HOOK_RECIPE, HOOK_RETIRED_APPROACHES,
    HOOK_SLOTS,)
from contracts.schema.stage6 import HookResponse
from novel.stages.stage6 import checks, prompts


def _candidate_payload(approaches=HOOK_RECIPE, texts=None):
    texts = texts or ['第一条具体文案。', '第二条具体文案。', '第三条具体文案。']
    return {
        'candidates': [
            {'approach': approach, 'text': text}
            for approach, text in zip(approaches, texts)
        ],
        'rationale': '三条分别保结构、预告兑现和用场面收尾。',
    }


def test_the_recipe_fills_every_slot_and_stays_inside_the_value_domain():
    """配方是**序列**，取值面是**集合**——2026-08-23 拆开的就是这两件事。"""
    assert HOOK_RECIPE == ('payoff_preview', 'scene_punchline', 'scene_punchline')
    assert len(HOOK_RECIPE) == len(HOOK_SLOTS), '一条配方对一个位置身份'
    assert set(HOOK_RECIPE) <= set(HOOK_APPROACHES)
    # 取值面**故意**比配方宽：盘上存量产物里有已退场的写法，收窄 `Literal` 会让它们拒读。
    assert HOOK_RETIRED_APPROACHES == ('source_architecture',)
    assert set(HOOK_RETIRED_APPROACHES) <= set(HOOK_APPROACHES)
    assert not (set(HOOK_RECIPE) & set(HOOK_RETIRED_APPROACHES))
    assert HOOK_CURRENT_APPROACHES == ('payoff_preview', 'scene_punchline')


def test_model_response_matches_the_recipe_counts_and_keeps_texts_unique():
    response = HookResponse.model_validate(_candidate_payload())
    assert tuple(candidate.approach for candidate in response.candidates) == HOOK_RECIPE

    # 同一种策略占两槽是**合法**的——这正是旧口径「每种各一次」判不出来的那一格。
    off_recipe = _candidate_payload(
        ('payoff_preview', 'payoff_preview', 'scene_punchline'))
    with pytest.raises(ValidationError, match='配方'):
        HookResponse.model_validate(off_recipe)

    # 退场的写法过得了 `Literal`（存量要读得回来），但过不了配方计数。
    retired = _candidate_payload(
        (HOOK_RETIRED_APPROACHES[0], 'scene_punchline', 'scene_punchline'))
    with pytest.raises(ValidationError, match='配方'):
        HookResponse.model_validate(retired)

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
    for approach in HOOK_CURRENT_APPROACHES:
        assert approach in system


def test_interpolated_prompt_renders_the_recipe_by_slot_and_has_no_placeholders():
    from contracts.invariants import HOOK_APPROACH_NOTES

    rendered = prompts.hook_system()
    for slot, approach in zip(HOOK_SLOTS, HOOK_RECIPE):
        assert f'{slot}. {approach}' in rendered, '配方按位置逐条下发，位置就是候选身份'
        assert HOOK_APPROACH_NOTES[approach] in rendered
    # 退场的写法一个字都不许出现在下发面上——列出来等于邀请模型产一条必被打回的候选。
    for approach in HOOK_RETIRED_APPROACHES:
        assert approach not in rendered
    assert '$' not in rendered


def test_a_repeated_approach_gets_a_computed_different_event_requirement():
    """同一种写法占两槽时，那句「锚不同事件」必须**现算**出来。

    ⚠ 写死一行会在配方改回全不重复之后留下一句不再成立的话；这里连它点到的
    槽位名一起查，防止有人把它退化成一句不指名的通用叮嘱。
    """
    from collections import Counter

    repeated = [key for key, count in Counter(HOOK_RECIPE).items() if count > 1]
    assert repeated, '当前配方本来就有重复；没有重复时这条测试要跟着改'
    block = prompts.recipe_block()
    for key in repeated:
        slots = [slot for slot, item in zip(HOOK_SLOTS, HOOK_RECIPE) if item == key]
        assert '、'.join(slots) in block
        assert '不同事件' in block


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
