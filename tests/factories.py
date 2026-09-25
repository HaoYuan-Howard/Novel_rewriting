#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small, deterministic builders for tests that do not need the local corpus."""
from __future__ import annotations

from contracts.schema.report import finding  # noqa: F401  （stage5_book 的判据样本共用）
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


# ── stage0 链助手（2026-09-15 全自动裁决后的人工闸退役测试夹具）──────────────


def stage0_machine_revise(book, new_clean: str):
    """现役语义的换稿：直接替换当前 transform 并保持机械 admission。

    `removed` 账本沿用当前稿（正文级换稿不动剔除账）。
    """
    from novel import prov
    from novel.stages.stage0 import removal, revisions as rev
    from novel.store import ArtifactStore

    store = ArtifactStore(removal._context(book, 'stage0.remove'))
    current = rev.read(store)
    if current is None:
        raise RuntimeError(f'{book}: 没有 stage0 transform，无法换稿')
    transform = rev.make_transform(
        source=current.source,
        code=prov.code_version(),
        clean_text=new_clean,
        removed=list(current.removed),
    )
    return rev.commit(store, transform, clean_text=new_clean)
