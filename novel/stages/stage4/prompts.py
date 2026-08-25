#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 场景规划提示与对规划者的安全投影。"""
from __future__ import annotations

from typing import Any

from contracts.invariants import (
    RESPONSE_CHAPTER_WEIGHTS, RESPONSE_DELIVERY_MODES, RESPONSE_GOAL_INTENTS,
    RESPONSE_STRUCTURAL_ROLES, RESPONSE_TEMPORAL_LINKS, RESPONSE_UNIT_WEIGHTS,
    STYLE_WORDS,
)
from novel.llm.prompts import with_json_shape

PLAN_MAX_TOKENS = 96_000

PLAN_SYS = """\
你是全书结构规划器。每个输出章必须与同号源章一一对应；不可合章、拆章、删章或跨章取材。

这次不是按源事件清单改写。Stage2 已给出本章不可改变的原子事实契约；你要重新设计场景，
`scenes[]` 的数组顺序就是新章的写作顺序。可以把不相邻事实放进同一场、把同一源舞台的事实
拆到不同场，或从后果/冲突切入再披露前因，但必须满足：

- 每个 obligation=land 的 fact_id 恰好进入一个 `land_fact_refs`；context 原子只进
  `context_fact_refs`，可按需复用；不产 `coverage`，覆盖由代码复算；
- `causal_edges` 是故事因果，不等于披露顺序。场景顺序可打破源相邻关系，但不能让结果在其
  因果前提尚未成立时发生；entry/exit state 必须闭合到章级 ending state；
- 每条保真 material_id 有明确场景落点；turn_fact_ref 必须是本场 land_fact_refs 中真正改变局面的
  一条；stage_refs 只选本章舞台，participant_refs 只选本章人物；
- `chapter_function` 已由 Stage2 冻结，不在响应里重写；`purpose`、`chapter_rewrite_brief`
  只能谈本章已经给出的事实，不泄露后文。brief 必须具体说明入口、场景重组与章尾状态，
  不能只写“增强张力/优化节奏”。

三个 goal 使用 typed fact_refs；章首/章尾 goal 应指向新 scenes 首尾真正承载的事实。
weight/role/delivery/transition 使用结构提示中的前缀枚举。第一场 transition 必须 T_START。
这里只做结构，不指定文风；目标文本中不得写这些纯写法词：$STYLE_WORDS。
只输出 JSON。""".replace('$STYLE_WORDS', '、'.join(f'「{word}」' for word in STYLE_WORDS))

_FACT_REF_HINT = {'kind': 'fact_atom', 'ref': 'f01_001', 'chapter': 1}
_GOAL_HINT = {'intent': '|'.join(RESPONSE_GOAL_INTENTS),
              'fact_refs': [_FACT_REF_HINT], 'rationale': 'str|null'}
_SCENE_HINT = {
    'weight': '|'.join(RESPONSE_UNIT_WEIGHTS),
    'purpose': '本场要完成的局面变化',
    'role': '|'.join(RESPONSE_STRUCTURAL_ROLES),
    'delivery': '|'.join(RESPONSE_DELIVERY_MODES),
    'transition': '|'.join(RESPONSE_TEMPORAL_LINKS),
    'land_fact_refs': ['f01_001'], 'context_fact_refs': ['f01_002'],
    'material_refs': ['h01_001'], 'turn_fact_ref': 'f01_001',
    'entry_state_refs': ['f01_002'], 'exit_state_refs': ['f01_003'],
    'participant_refs': ['char_001'], 'stage_refs': ['b01_01'],
}
PLAN_HINT: dict[str, Any] = {
    'reconstruction_strategy': '全书重构意图',
    'output_chapters': [{
        'output_ordinal': 1, 'from_source_ordinals': [1],
        'chapter_goal': _GOAL_HINT, 'open_hook_goal': _GOAL_HINT,
        'end_hook_goal': _GOAL_HINT,
        'weight': '|'.join(RESPONSE_CHAPTER_WEIGHTS),
        'chapter_rewrite_brief': '只含本章安全信息的场景重构简报',
        'scenes': [_SCENE_HINT],
    }],
}
PLAN_REPAIR_OVERRIDE = """\
本次只返回指定章的完整新对象，不返回 patch，也不修改全书 reconstruction_strategy。
修复仍须重新核对全部 land 事实、材料、因果与状态闭合。"""
PLAN_REPAIR_HINT = dict(PLAN_HINT['output_chapters'][0])


def plan_system() -> str:
    return with_json_shape(PLAN_SYS, PLAN_HINT)


def plan_repair_system() -> str:
    return with_json_shape(PLAN_SYS + '\n' + PLAN_REPAIR_OVERRIDE, PLAN_REPAIR_HINT)


BLOCK_INDEX = '── 章节索引（阅读序）──'
BLOCK_FACT_REFS = '── 可引用事实目录（goal.fact_refs 只能从这里选择）──'
BLOCK_SKELETON = '── Stage2 原子事实契约与舞台目录（只作场景重组输入）──'
BLOCK_SIZES = '── 章级源字数与原子数（预算整数由代码计算）──'
BLOCK_BODY = '── 全书原文正文（不可变原文，按阅读序章号分块）──'
BLOCK_REPAIR_SCOPE = '── 本次定向修复范围（只能返回这一章）──'
BLOCK_REPAIR_FAILURES = '── 当前确定性 HARD 失败（逐条修复）──'
BLOCK_REPAIR_BASELINE = '── 目标章现有场景计划（仅作修复基线）──'
BLOCK_REPAIR_STRATEGY = '── 全书重构意图（不可修改，不要在响应中返回）──'


def project_blueprints_for_planner(
    blueprints: dict[str, Any], _groups: Any = None,
) -> dict[str, Any]:
    """下发章级原子真相、因果/状态与舞台；不构造源序 events 清单。"""
    rows: list[dict[str, Any]] = []
    for bp in (blueprints.get('blueprints') or []):
        rows.append({
            'chapter_ordinal': bp.get('chapter_ordinal'),
            'summary': bp.get('summary'),
            'chapter_function': bp.get('chapter_function'),
            'fact_atoms': [{
                'fact_id': atom.get('fact_id'), 'claim': atom.get('claim'),
                'kind': atom.get('kind'), 'obligation': atom.get('obligation'),
                'source_beat_ref': atom.get('source_beat_ref'),
                'participant_char_ids': atom.get('participant_char_ids') or [],
            } for atom in (bp.get('fact_atoms') or [])],
            'causal_edges': bp.get('causal_edges') or [],
            'entry_state_refs': bp.get('entry_state_refs') or [],
            'ending_state_refs': bp.get('ending_state_refs') or [],
            'materials': [{
                'material_id': hinge.get('hinge_id'), 'kind': hinge.get('kind'),
                'source_beat_ref': hinge.get('source_beat_ref'),
            } for beat in (bp.get('beats') or [])
                for hinge in (beat.get('hinges') or [])],
            'stages': [{
                'stage_ref': beat.get('beat_id'), 'time_layer': beat.get('time_layer'),
                'place': beat.get('place'),
                'present_char_ids': beat.get('present_char_ids') or [],
            } for beat in (bp.get('beats') or [])],
        })
    return {'blueprints': rows}
