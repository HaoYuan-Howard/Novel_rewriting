#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small, deterministic builders for tests that do not need the local corpus."""
from __future__ import annotations

from typing import Any

from contracts.invariants import HOOK_RECIPE, HOOK_SLOTS
from contracts.schema.report import finding
from contracts.schema.stage6 import Hook
from contracts.schema.stage6_prepare import PrepareState
from contracts.schema.stage1 import MainText
from novel.core.measure import body_style_metrics, measure_body
from novel.stages.stage5.measure import Book


def missing_prepare_state() -> PrepareState:
    """审批接口最小样例明确登记未运行准备，不把缺字段当成未运行。"""
    return PrepareState(state='missing', reason='测试样例尚未运行文案准备',
        subject_sha256=None, review_token=None, prepare=None, review=None,
        pending_commit=False)


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
            'participants': ['actor_' + '1' * 24, 'actor_' + '2' * 24],
        }],
        'states': [{'state_ref': 'st01_01', 'content': '沈青与周衡暂不对外声张账册异常。',
                    'global_ref': None, 'modality': 'world', 'holder': None}],
        'constraints': [], 'restricted_subject_refs': [],
        'global_subjects': [
            {'subject_ref': {'kind': 'setting', 'ref': 'subject_' + '1' * 24}, 'value': {'content': '架空旧城'},
             'allowed_slots': ['s01'], 'state_permissions': []},
            {'subject_ref': {'kind': 'setting', 'ref': 'subject_' + '2' * 24},
             'value': {'content': '故事发生在临河茶馆及其旧城商路。'},
             'allowed_slots': ['s01'], 'state_permissions': []},
        ],
        'scenes': [{
            'slot': 's01', 'purpose': '让墨迹异常在核账过程中暴露',
            'role': 'setup', 'delivery': 'scene', 'transition': 'start',
            'budget_chars': len(source_one),
            'deliveries': [{'beat_ref': 'b01_01', 'presentation_instruction': None,
                            'stages': [{'time_layer': 'present', 'place': '茶馆',
                                        'present': ['actor_' + '1' * 24, 'actor_' + '2' * 24]}]}],
            'context_beat_refs': [], 'turn_beat_ref': 'b01_01',
            'state_events': [{'state_ref': 'st01_01', 'endpoint': 'established'}],
            'constraint_refs': [],
        }],
        'characters': [{'actor_ref': 'actor_' + '1' * 24, 'display_name': character['display_name'],
                        'surfaces': ['沈青'], 'role': character['role'],
                        'surface_allowed_slots': {'沈青': ['s01']},
                        'allowed_slots': ['s01'], 'role_allowed_slots': ['s01']},
                       {'actor_ref': 'actor_' + '2' * 24, 'display_name': '周衡', 'surfaces': ['周衡'],
                        'surface_allowed_slots': {'周衡': ['s01']},
                        'role': '与沈青一同核账的人', 'allowed_slots': ['s01'], 'role_allowed_slots': ['s01']}],
        'narration': [{'segment': '全章', 'grammatical_person': 'third',
                       'narrator': 'actor_' + '1' * 24, 'focalizer': None,
                       'pronoun_surfaces': [], 'beat_refs': [], 'allowed_slots': ['s01']}],
    }
    from contracts.schema.stage4 import CtxChapter
    entry = CtxChapter.model_validate(entry).model_dump(mode='json')
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


# 现役 Stage6 的纯载体夹具；不证明真实生成、判据或成书质量。
def stage6_hook_retry(slot: str, index: int) -> dict:
    return {
        'slot': slot,
        'attempt': index // len(HOOK_SLOTS) + 1,
        'failure_items': ['hook_truncated'],
        'failure_evidence': f'{index:010x}'[-10:],
        'user_prompt': f'{index + 1:010x}'[-10:],
        'output': f'{index + 2:010x}'[-10:],
        'model': 'model-x',
        'cache_policy': 'refresh-write',
        'before_hard': 1,
        'after_hard': 0,
        'status': 'accepted',
        'reason': None,
        'accepted': True,
    }


def stage6_hook(
    texts: list[str],
    *,
    hard_slots: set[int] = frozenset(),
    reuse_slots: set[int] = frozenset(),
    retries: int = 0,
) -> Hook:
    candidates = []
    for index, (approach, text) in enumerate(zip(HOOK_RECIPE, texts), 1):
        findings = []
        if index in hard_slots:
            findings.append(finding(
                'hook_truncated', detail='测试：句末不完整', evidence=['截断']))
        if index in reuse_slots:
            findings.append(finding(
                'hook_reuses_source', detail='测试：复用原作文案', evidence=['二十字复用']))
        candidates.append({
            'approach': approach,
            'text': text,
            'mech': {'punct_fixed': 0, 'quotes_fixed': 0, 'paras_fixed': 0},
            'findings': [row.model_dump(mode='json') for row in findings],
        })
    retry_rows = [stage6_hook_retry(HOOK_SLOTS[index % 3], index) for index in range(retries)]
    return Hook.model_validate({
        'lead_upto': 1,
        'rationale': '确定性合成候选，供现役载体与审批边界测试。',
        'candidates': candidates,
        'approved': None,
        'sources': {'writer_context': {'state': 'fresh', 'detail': [], 'code': 'test'}},
        'judged': {},
        '_provenance': {
            'code': 'test-code',
            'root_clean_sha1': 'a' * 40,
            'inputs': {},
            'usage': {
                'calls_made': 1, 'calls_cached': 0,
                'prompt_tokens': 10, 'completion_tokens': 10, 'reasoning_tokens': 5,
            },
            'prompts': {'HOOK_SYS': 'b' * 10, 'RULES': 'c' * 10},
            'model': 'model-x',
            'params': {
                'max_tokens': 64000,
                'candidate_retry_max_tokens': 24000,
                'candidate_retry_limit': 4,
                'candidate_retry_effort': 'low',
                'candidate_retry_thinking': False,
            },
            'candidate_retries': retry_rows,
        },
    })
