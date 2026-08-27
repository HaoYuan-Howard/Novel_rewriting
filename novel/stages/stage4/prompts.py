#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 场景规划提示与对规划者的安全投影。"""
from __future__ import annotations

from typing import Any

from contracts.invariants import (
    RESPONSE_CHAPTER_WEIGHTS, RESPONSE_DELIVERY_MODES, RESPONSE_GOAL_INTENTS,
    RESPONSE_STRUCTURAL_ROLES, RESPONSE_TEMPORAL_LINKS, RESPONSE_UNIT_WEIGHTS,
    STRUCTURE_CHANGE_KINDS, STYLE_WORDS,
)
from novel.llm.prompts import with_json_shape

BLIND_DESIGN_MAX_TOKENS = 96_000
EVIDENCE_BIND_MAX_TOKENS = 48_000


BLIND_DESIGN_SYS = """\
你是 Stage4A 盲结构设计器。你只能看到去序的事实真相图：事实与人物都用无顺序含义的 opaque
alias，数组顺序也由确定性散列打乱。不要从 alias 字面、数组位置或章内编号猜原文叙述顺序；
你没有原文、源舞台、source_beat_ref 或逐字材料。

每个输出章与同号输入章一一对应。先按事实因果、人物状态和读者信息设计场景分组与披露顺序，
再选择结构决定：
- retain：本章适合直接、线性的真相推进，不主动声明结构变化；declared_change_kinds 必须为空；
- reconstruct：本章有真实重构空间，必须声明至少两类变化。可选值只有：$CHANGE_KINDS。

每个 land alias 恰好落入一场；context alias 只作上下文；turn 必须属于本场 land；状态输入输出要
承接。三个 goal 只能引用本章 fact alias，并分别落在章目标、首场和末场。场景数组就是新披露顺序。
weight/role/delivery/transition 使用响应契约里的前缀枚举，首场 transition 必须 X_START。
只谈事实组织，不指定文风，不写纯写法词：$STYLE_WORDS。只输出 JSON。""" \
    .replace('$CHANGE_KINDS', '、'.join(STRUCTURE_CHANGE_KINDS)) \
    .replace('$STYLE_WORDS', '、'.join(f'「{word}」' for word in STYLE_WORDS))

_BLIND_GOAL_HINT = {
    'intent': '|'.join(RESPONSE_GOAL_INTENTS),
    'fact_aliases': ['a_0123456789abcdef'],
}
_BLIND_SCENE_HINT = {
    'weight': '|'.join(RESPONSE_UNIT_WEIGHTS),
    'purpose': '本场要完成的事实局面变化',
    'role': '|'.join(RESPONSE_STRUCTURAL_ROLES),
    'delivery': '|'.join(RESPONSE_DELIVERY_MODES),
    'transition': '|'.join(RESPONSE_TEMPORAL_LINKS),
    'land_fact_aliases': ['a_0123456789abcdef'],
    'context_fact_aliases': ['a_fedcba9876543210'],
    'turn_fact_alias': 'a_0123456789abcdef',
    'entry_state_aliases': [], 'exit_state_aliases': ['a_0123456789abcdef'],
}
BLIND_DESIGN_HINT: dict[str, Any] = {
    'reconstruction_strategy': '只基于真相图的全书结构意图',
    'output_chapters': [{
        'output_ordinal': 1,
        'structure_decision': {
            'mode': 'retain|reconstruct', 'reason': '本章选择理由',
            'declared_change_kinds': ['entry_anchor', 'fact_grouping'],
        },
        'chapter_goal': _BLIND_GOAL_HINT,
        'open_hook_goal': _BLIND_GOAL_HINT,
        'end_hook_goal': _BLIND_GOAL_HINT,
        'weight': '|'.join(RESPONSE_CHAPTER_WEIGHTS),
        'chapter_rewrite_brief': '入口、事实组合与收束的可执行简报',
        'scenes': [_BLIND_SCENE_HINT],
    }],
}

EVIDENCE_BIND_SYS = """\
你是 Stage4B 证据绑定器。盲设计候选已经冻结；你现在看到原文证据、源舞台与必须材料，
只能为每个 candidate_scene_id 绑定 stage_refs、material_refs。人物到场由所绑舞台的 present 唯一确定。
不得改变候选的章序、
场景顺序、事实分组、goal、retain/reconstruct、变化声明、purpose、role、delivery、transition 或 weight。

candidate_sha256 必须逐字回显。若全部候选场都能在本章证据内执行，返回 status=bound，并且每个候选场
恰好绑定一次、每条必须材料恰好落一次、stage 引用来自本章关闭式目录；此时 reason_kind、reason、
evidence_refs 三项必须全部为 null。若任何一场无法在不改
候选结构的前提下成立，返回 status=infeasible，给出关闭式 reason_kind、具体理由和非空 evidence_refs；
不得返回一份悄悄重规划或只绑定一半的候选。只输出 JSON。"""

EVIDENCE_BIND_HINT: dict[str, Any] = {
    'status': 'bound|infeasible',
    'candidate_sha256': '64位小写sha256',
    'chapters': [{
        'output_ordinal': 1,
        'scenes': [{
            'candidate_scene_id': 'c01s01',
            'material_refs': ['h01_001'],
            'stage_refs': ['b01_01'],
        }],
    }],
    # ⚠ 示例三项都写 null：旧示例给的是一条**非空** evidence 数组，模型照抄成
    #   `bound` 下的空数组，于是一次整书 B 相白重出。示例形态就是契约的一部分。
    'reason_kind': None,
    'reason': None,
    'evidence_refs': None,
}


def blind_design_system() -> str:
    return with_json_shape(BLIND_DESIGN_SYS, BLIND_DESIGN_HINT)


def evidence_bind_system() -> str:
    return with_json_shape(EVIDENCE_BIND_SYS, EVIDENCE_BIND_HINT)


BLOCK_INDEX = '── 章节索引（阅读序）──'
BLOCK_FACT_REFS = '── 可引用事实目录（goal.fact_refs 只能从这里选择）──'
BLOCK_SKELETON = '── Stage2 原子事实契约与舞台目录（只作场景重组输入）──'
BLOCK_SIZES = '── 章级源字数与原子数（预算整数由代码计算）──'
BLOCK_BODY = '── 全书原文正文（不可变原文，按阅读序章号分块）──'
BLOCK_BLIND_TRUTH = '── 去序真相图（opaque alias 无顺序含义）──'
BLOCK_CANDIDATE = '── 已冻结的 Stage4A 盲设计候选（不得修改）──'
BLOCK_CANDIDATE_HASH = '── 候选完整 SHA-256（必须回显）──'
BLOCK_EVIDENCE = '── Stage2 证据层与关闭式引用目录（只用于绑定）──'


def project_blueprints_for_planner(
    blueprints: dict[str, Any], _groups: Any = None,
) -> dict[str, Any]:
    """下发章级原子真相、因果/状态与舞台；不构造源序 events 清单。"""
    rows: list[dict[str, Any]] = []
    for bp in (blueprints.get('blueprints') or []):
        rows.append({
            'chapter_ordinal': bp.get('chapter_ordinal'),
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
                'source_beat_ref': beat.get('beat_id'),
            } for beat in (bp.get('beats') or [])
                for hinge in (beat.get('hinges') or [])],
            'stages': [{
                'stage_ref': beat.get('beat_id'), 'time_layer': beat.get('time_layer'),
                'place': beat.get('place'),
                'present_char_ids': beat.get('present_char_ids') or [],
            } for beat in (bp.get('beats') or [])],
        })
    return {'blueprints': rows}
