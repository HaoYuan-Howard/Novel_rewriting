#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small, deterministic builders for tests that do not need the local corpus."""
from __future__ import annotations

from contracts.invariants import STAGE3_MEASUREMENT_CONTRACT
from contracts.schema.stage1 import HookCopy, MainText
from novel.consumption import stage5_style
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
        {'chapter_ordinal': 1, 'text': source_one},
        {'chapter_ordinal': 2, 'text': source_two},
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
            '本章事实：沈青发现账册末页的墨迹被人换过。']},
        'open_hook_goal': {'intent': 'open', 'facts': ['第 1 章开篇']},
        'end_hook_goal': {'intent': 'close', 'facts': ['第 1 章结尾']},
        'semantic_contract': {
            'chapter_function': '发现账册异常并决定秘密追查',
            'information_delta': ['沈青发现账册末页的墨迹被人换过。'],
            'character_state_delta': [], 'relationship_delta': [],
            'reader_knowledge_delta': [],
            'must_land_facts': ['沈青发现账册末页的墨迹被人换过。'],
            'context_facts': [], 'causal_edges': [], 'entry_state': [],
            'ending_state': ['沈青决定暂不惊动旁人。'],
        },
        'chapter_rewrite_brief': '从雨夜核账切入，让墨迹异常成为本场转折，章尾落到秘密追查。',
        'budget_chars': len(source_one),
        'source_chars': len(source_one),
        'scenes': [{
            'slot': 's01',
            'purpose': '让墨迹异常在核账过程中暴露',
            'role': 'setup', 'delivery': 'scene', 'transition': 'start',
            'budget_chars': len(source_one),
            'source_chars': len(source_one),
            'required_facts': ['沈青发现账册末页的墨迹被人换过。'],
            'context_facts': [],
            'turn_fact': '沈青发现账册末页的墨迹被人换过。',
            'entry_state': [], 'exit_state': ['沈青决定暂不惊动旁人。'],
            'participants': ['沈青', '周衡'],
            'stages': [{'time_layer': 'present', 'place': '茶馆',
                        'present': ['沈青', '周衡']}],
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
    projected = stage5_style({'measurable': measurable})
    assert isinstance(projected, dict) and isinstance(projected.get('body'), dict)
    return Book(
        title='雨夜账册',
        ctx={
            'world_facts': {'characters': {'char_001': character}},
            'chapters': [entry],
        },
        measurable=projected['body'],
        source_text={1: source_one, 2: source_two},
        secrets=[],
    )


def stage3_artifact() -> dict:
    """Return a current Stage3 v2 mechanical payload."""
    book = stage5_book()
    main = MainText.model_validate([
        {'chapter_ordinal': ordinal, 'text': text}
        for ordinal, text in sorted(book.source_text.items())
    ])
    measurable = measure_all(
        main,
        HookCopy(text='一把银匙，一本被调换的账册，足以让旧日秘密重新开口。'),
    ).model_dump()
    return {
        'measurable': measurable,
        '_provenance': {
            'code': 'fixture',
            'root_clean_sha1': '0' * 40,
            'inputs': {},
            'measurement_contract': STAGE3_MEASUREMENT_CONTRACT,
        },
    }
