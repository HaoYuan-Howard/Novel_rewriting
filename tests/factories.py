#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small, deterministic builders for tests that do not need the local corpus."""
from __future__ import annotations

from typing import Any

from contracts.schema.evaluation import GlobalReview
from contracts.schema.stage1 import MainText
from novel.core.measure import body_style_metrics, measure_body
from novel.quality.global_review import build_review
from novel.stages.stage5.measure import Book


def approved_global_review(grounding: Any) -> GlobalReview:
    """用生产 review builder 铸造测试下游所需的 current 全 PASS 前置事实。"""
    payload = (
        grounding.model_dump(by_alias=True, mode='json')
        if hasattr(grounding, 'model_dump') else grounding
    )
    expected = build_review(payload)
    return expected.model_copy(update={'items': [
        item.model_copy(update={
            'state': 'PASS',
            'actor': 'test-reviewer',
            'decided_at': '2026-09-02T00:00:00+00:00',
            'reason': '测试下游的显式 current Stage2 前置事实',
        })
        for item in expected.items
    ]})


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
    character = {
        'display_name': '沈青',
        'role': '茶馆掌柜',
        'name_distinctiveness': 'high',
    }
    def goal(intent):
        return {'intent': intent, 'subject_refs': [{'kind': 'beat', 'ref': 'b01_01'}],
                'anchor_beat_ref': 'b01_01', 'anchor_scene_ref': 's01'}

    entry = {
        'output_ordinal': 1,
        'chapter_goal': goal('advance'), 'open_hook_goal': goal('open'), 'end_hook_goal': goal('close'),
        'semantic_contract': {
            'chapter_function': '发现账册异常并决定秘密追查',
            'beat_refs': ['b01_01'], 'state_refs': ['st01_01'], 'constraint_refs': [],
            'entry_state_refs': [], 'carry_out_refs': ['st01_01'],
        },
        'chapter_rewrite_brief': '从雨夜核账切入，让墨迹异常成为本场转折，章尾落到秘密追查。',
        'beats': [{
            'beat_ref': 'b01_01',
            'content': '雨夜核账时，沈青发现账册末页的墨迹被换成陌生数字。她让周衡关门，暂不惊动旁人；周衡应下，挂好湿斗篷并拨亮灯芯。',
            'participant_char_ids': ['char_001', 'char_002'],
        }],
        'states': [{'state_ref': 'st01_01', 'content': '沈青与周衡暂不对外声张账册异常。',
                    'global_ref': None, 'modality': 'world', 'holder_char_id': None}],
        'constraints': [], 'restricted_subject_refs': [], 'knowledge_events': [],
        'global_subjects': [
            {'subject_ref': {'kind': 'setting', 'ref': 'setting:era'}, 'value': '架空旧城',
             'allowed_slots': ['s01'], 'state_permissions': []},
            {'subject_ref': {'kind': 'setting', 'ref': 'setting:worldview'},
             'value': '故事发生在临河茶馆及其旧城商路。',
             'allowed_slots': ['s01'], 'state_permissions': []},
        ],
        'scenes': [{
            'slot': 's01', 'purpose': '让墨迹异常在核账过程中暴露',
            'role': 'setup', 'delivery': 'scene', 'transition': 'start',
            'budget_chars': len(source_one),
            'deliveries': [{'beat_ref': 'b01_01', 'presentation_instruction': None,
                            'stages': [{'time_layer': 'present', 'place': '茶馆',
                                        'present': ['沈青', '周衡']}]}],
            'context_beat_refs': [], 'turn_beat_ref': 'b01_01',
            'state_events': [{'state_ref': 'st01_01', 'endpoint': 'established'}],
            'knowledge_event_refs': [], 'constraint_refs': [],
        }],
        'characters': [{'char_id': 'char_001', 'display_name': character['display_name'],
                        'role': character['role']},
                       {'char_id': 'char_002', 'display_name': '周衡', 'role': '与沈青一同核账的人'}],
        'narration': [{'segment': '全章', 'grammatical_person': 'third', 'narrator': '沈青'}],
        'address': [],
    }
    projected = body_style_metrics(measure_body(main))
    return Book(
        title='雨夜账册',
        ctx={'chapters': [entry]},
        measurable=projected,
        source_text={1: source_one, 2: source_two},
        secrets=[],
        characters={'char_001': character,
                    'char_002': {'display_name': '周衡', 'role': '与沈青一同核账的人',
                                 'name_distinctiveness': 'high'}},
    )
