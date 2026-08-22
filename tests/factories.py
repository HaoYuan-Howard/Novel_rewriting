#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small, deterministic builders for tests that do not need the local corpus."""
from __future__ import annotations

from copy import deepcopy

from contracts.invariants import HOOK_KEYS, VOICE_KEYS
from contracts.schema.stage1 import HookCopy, MainText
from novel.stages.stage3.measure import measure_all
from novel.stages.stage5.measure import Book


def stage5_book() -> Book:
    """Return a current-shape Stage5 book with enough prose for style checks."""
    scene = (
        '沈青把银匙放在木桌上，听见窗外的雨一阵紧过一阵。'
        '她翻到账册最后一页，发现熟悉的墨迹被人换成了陌生的数字。'
        '「先关门。」她抬眼看向门边的周衡，「这件事别惊动旁人。」'
        '周衡点头，把湿透的斗篷挂好，又将柜台后的灯芯拨亮。'
    )
    source_one = '\n\n'.join(scene + f'这是第{i}次核对，她仍没有仓促下结论。' for i in range(1, 7))
    source_two = '\n\n'.join(scene + f'天色又暗了一层，线索在第{i}次比对后逐渐清楚。' for i in range(1, 5))
    main = MainText.model_validate([
        {'chapter_ordinal': 1, 'source_chapter_no': 1, 'text': source_one},
        {'chapter_ordinal': 2, 'source_chapter_no': 2, 'text': source_two},
    ])
    measurable = measure_all(
        main,
        HookCopy(text='一把银匙，一本被调换的账册，足以让旧日秘密重新开口。'),
    ).model_dump()
    character = {
        'display_name': '沈青',
        'role': '茶馆掌柜',
        'name_distinctiveness': 'high',
    }
    entry = {
        'output_ordinal': 1,
        'from_source_ordinals': [1],
        'chapter_goal': {'intent': 'advance', 'facts': [
            '事件：沈青发现账册末页的墨迹被人换过。']},
        'open_hook_goal': {'intent': 'open', 'facts': ['第 1 章开篇']},
        'end_hook_goal': {'intent': 'close', 'facts': ['第 1 章结尾']},
        'budget_chars': len(source_one),
        'source_chars': len(source_one),
        'segments': [{
            'slot': 's01',
            'events': ['沈青发现账册末页的墨迹被人换过。'],
            'budget_chars': len(source_one),
            'source_chars': len(source_one),
            'time_layer': 'present',
            'place': '茶馆',
            'present': ['沈青', '周衡'],
            'verbatim': [],
            'keep_value': [],
            'must_appear': [],
            'must_hold': [],
        }],
        'characters': {'char_001': {
            'display_name': character['display_name'], 'role': character['role']}},
        'narration': [{
            'segment': '全章',
            'grammatical_person': 'third',
            'narrator': '沈青',
        }],
        'facts': {
            'setting': {'时代背景': '架空旧城', '世界观': '故事发生在临河茶馆及其旧城商路。'},
            'relations': [],
            'secrets': [],
            'world_rules': [],
            'arc': {'current_stage': {'stage': '查账', 'chapters': [1]}},
            'address': [],
        },
        'must_not_reveal': [],
    }
    voice = {key: f'{key} 的合成风格描述' for key in VOICE_KEYS}
    return Book(
        title='雨夜账册',
        ctx={
            'world_facts': {'characters': {'char_001': character}},
            'chapters': [entry],
        },
        style={
            'voice': voice,
            'surface_habits': ['以动作推进判断，避免解释性总结。'],
            'measurable': measurable,
        },
        source_text={1: source_one, 2: source_two},
        secrets=[],
    )


def stage3_artifact() -> dict:
    """Return a model-authored Stage3 payload suitable for rule counterexamples."""
    book = stage5_book()
    data = deepcopy(book.style)
    data['hook_style'] = {key: f'{key} 的合成文案风格' for key in HOOK_KEYS}
    data['chapter_styles'] = [
        {
            'source_ordinal': ordinal,
            'voice': deepcopy(data['voice']),
            'surface_habits': list(data['surface_habits']),
        }
        for ordinal in sorted(book.source_text)
    ]
    data['_provenance'] = {}
    return data
