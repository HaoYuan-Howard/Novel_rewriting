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
# 新架构把宏观策略与逐章设计拆成独立 provider request；这两枚是请求身份里的
# 输出预算边界。旧 ``BLIND_DESIGN_MAX_TOKENS`` 只供整书拒稿的零 wire 重放身份。
BLIND_BOOK_STRATEGY_MAX_TOKENS = 16_000
BLIND_CHAPTER_DESIGN_MAX_TOKENS = 24_000
EVIDENCE_BIND_MAX_TOKENS = 48_000


BLIND_BOOK_STRATEGY_SYS = """\
你是 Stage4A 的整书宏观策略器。你只能看到按阅读序排列的 chapter_function 与确定性计数，
看不到事实 claim、事实或人物 alias、原文、源舞台、source beat 或逐字材料。

请给出一条全书 reconstruction_strategy，并为输入中的每章选择相对篇幅权重。章号集合必须与
输入完全一致且不得重复。权重只能使用响应契约中的 CH_ 前缀枚举；它表达章间相对轻重，
不等于最终字数，预算整数由代码统一计算。不要设计逐章场景，不回显章摘要，不添加事实、人物
或证据。只谈宏观事实组织，不指定文风，不写纯写法词：$STYLE_WORDS。只输出 JSON。""" \
    .replace('$STYLE_WORDS', '、'.join(f'「{word}」' for word in STYLE_WORDS))

BLIND_BOOK_STRATEGY_HINT: dict[str, Any] = {
    'reconstruction_strategy': '只基于章级功能与计数的全书结构意图',
    'chapter_weights': [{
        'chapter_ordinal': 1,
        'weight': '|'.join(RESPONSE_CHAPTER_WEIGHTS),
    }],
}


BLIND_CHAPTER_DESIGN_SYS = """\
你是 Stage4A 单章盲结构设计器。你只看到本章去序的事实真相图：事实与人物都用仅在本请求
有效的短 local alias，数组顺序也由确定性散列打乱。不要从 alias 字面或数组位置猜原文
叙述顺序；你没有原文、源舞台、source_beat_ref 或逐字材料。输入中的全书策略与本章权重
已经冻结；不要在响应中回显或修改它们，章号也由请求身份确定，不在响应中回显。

先按事实因果、人物状态和读者信息设计场景分组与披露顺序，再选择结构决定：
- retain：本章适合直接、线性的真相推进，不主动声明结构变化；declared_change_kinds 必须为空；
- reconstruct：本章有真实重构空间，必须声明至少两类变化。可选值及唯一合法排列顺序是：
  $CHANGE_KINDS；只选需要的项，去重后仍须保持这个契约顺序。

每个 land alias 恰好落入一场；context_fact_aliases 只能引用「此刻读者已经知道」的事实：
输入中 obligation=context 的 alias，或**已经在更早场落地**的 land alias。**不得引用本场或
后面才落地的 land alias**。turn 必须属于本场 land。entry_state_aliases / exit_state_aliases
只填**本场新增**的状态，不要把前面已经成立的状态再抄一遍：
- X_CONTINUE 表示同一时间线紧邻续接，代码自动继承上一场的有效 exit；
- X_RETURN 回到离开主线前的锚点，代码自动恢复该锚点的有效状态；
- 首场自动承接本章章首状态；
- X_EARLIER 打开更早时点，X_PARALLEL 打开并行时层，二者不继承紧邻上一场 exit；
- 一条 X_EARLIER/X_PARALLEL 支路用一次 X_RETURN 收口；当前输出没有 branch id，
  不要嵌套支路，也不要在支路内再用 X_LATER；
- X_LATER 跳到更晚时点，有时间空档，不承诺线性继承紧邻上一场 exit。

机器无法从 alias 图完整判断非线性时序是否合理；必须如实标注边界留给人工判断，
不得为了让状态看似连续而把未来事实带入过去。章尾必须真的把本章 ending 状态确立起来。
三个 goal 只能引用本章 local fact alias，并分别落在章目标、首场和末场。场景数组就是新披露顺序。
scene weight/role/delivery/transition 使用响应契约里的前缀枚举，首场 transition 必须 X_START。
只谈事实组织，不指定文风，不写纯写法词：$STYLE_WORDS。只输出 JSON。""" \
    .replace('$CHANGE_KINDS', '、'.join(STRUCTURE_CHANGE_KINDS)) \
    .replace('$STYLE_WORDS', '、'.join(f'「{word}」' for word in STYLE_WORDS))

_BLIND_LOCAL_GOAL_HINT = {
    'intent': '|'.join(RESPONSE_GOAL_INTENTS),
    'fact_aliases': ['f_012345'],
}
_BLIND_LOCAL_SCENE_HINT = {
    'weight': '|'.join(RESPONSE_UNIT_WEIGHTS),
    'purpose': '本场要完成的事实局面变化',
    'role': '|'.join(RESPONSE_STRUCTURAL_ROLES),
    'delivery': '|'.join(RESPONSE_DELIVERY_MODES),
    'transition': '|'.join(RESPONSE_TEMPORAL_LINKS),
    'land_fact_aliases': ['f_012345'],
    'context_fact_aliases': [],
    'turn_fact_alias': 'f_012345',
    'entry_state_aliases': [],
    'exit_state_aliases': ['f_012345'],
}
BLIND_CHAPTER_DESIGN_HINT: dict[str, Any] = {
    'structure_decision': {
        'mode': 'retain|reconstruct', 'reason': '本章选择理由',
        'declared_change_kinds': ['entry_anchor', 'fact_grouping'],
    },
    'chapter_goal': _BLIND_LOCAL_GOAL_HINT,
    'open_hook_goal': _BLIND_LOCAL_GOAL_HINT,
    'end_hook_goal': _BLIND_LOCAL_GOAL_HINT,
    'chapter_rewrite_brief': '入口、事实组合与收束的可执行简报',
    'scenes': [_BLIND_LOCAL_SCENE_HINT],
}


BLIND_DESIGN_SYS = """\
你是 Stage4A 盲结构设计器。你只能看到去序的事实真相图：事实与人物都用无顺序含义的 opaque
alias，数组顺序也由确定性散列打乱。不要从 alias 字面、数组位置或章内编号猜原文叙述顺序；
你没有原文、源舞台、source_beat_ref 或逐字材料。

每个输出章与同号输入章一一对应。先按事实因果、人物状态和读者信息设计场景分组与披露顺序，
再选择结构决定：
- retain：本章适合直接、线性的真相推进，不主动声明结构变化；declared_change_kinds 必须为空；
- reconstruct：本章有真实重构空间，必须声明至少两类变化。可选值及唯一合法排列顺序是：
  $CHANGE_KINDS；只选需要的项，去重后仍须保持这个契约顺序。

每个 land alias 恰好落入一场；context_fact_aliases 只能引用「此刻读者已经知道」的事实：
输入中 obligation=context 的 alias，或**已经在更早场落地**的 land alias。**不得引用本场或
后面才落地的 land alias**——那是把还没披露的事实提前当成已成立。turn 必须属于本场 land。
entry_state_aliases / exit_state_aliases 只填**本场新增**的状态：entry 填本场开场时必须
额外成立、而上一时点尚未给出的状态，exit 填本场结束时新确立的状态。**不要把前面已经
成立的状态再抄一遍**——同一时间线上的承接由代码按 transition 的定义自动补全：
- X_CONTINUE 表示同一时间线紧邻续接，代码自动继承上一场的有效 exit；
- X_RETURN 回到离开主线前的锚点，代码自动恢复该锚点的有效状态；
- 首场自动承接本章章首状态。
真正需要你判断的是**时点本身**：
- X_EARLIER 打开更早时点，X_PARALLEL 打开并行时层，二者都不继承紧邻上一场 exit；
- 一条 X_EARLIER/X_PARALLEL 支路用一次 X_RETURN 收口；当前输出没有 branch id，
  不要嵌套支路，也不要在支路内再用 X_LATER；
- X_LATER 跳到更晚时点，有时间空档，不承诺线性继承紧邻上一场 exit。
机器无法从 alias 图完整判断非线性时序是否合理；必须如实标注边界留给人工判断，
不得为了让状态看似连续而把未来事实带入过去。章尾必须真的把本章 ending 状态确立起来。
三个 goal 只能引用本章 fact alias，并分别落在章目标、首场和末场。场景数组就是新披露顺序。
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
    # 空数组是常态：多数场不需要额外上下文事实，示例给非空会诱导硬凑。
    'context_fact_aliases': [],
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


def blind_book_strategy_system() -> str:
    return with_json_shape(BLIND_BOOK_STRATEGY_SYS, BLIND_BOOK_STRATEGY_HINT)


def blind_chapter_design_system() -> str:
    return with_json_shape(BLIND_CHAPTER_DESIGN_SYS, BLIND_CHAPTER_DESIGN_HINT)


def evidence_bind_system() -> str:
    return with_json_shape(EVIDENCE_BIND_SYS, EVIDENCE_BIND_HINT)


BLOCK_INDEX = '── 章节索引（阅读序）──'
BLOCK_FACT_REFS = '── 可引用事实目录（goal.fact_refs 只能从这里选择）──'
BLOCK_SKELETON = '── Stage2 原子事实契约与舞台目录（只作场景重组输入）──'
BLOCK_SIZES = '── 章级源字数与原子数（预算整数由代码计算）──'
BLOCK_BODY = '── 全书原文正文（不可变原文，按阅读序章号分块）──'
BLOCK_BLIND_TRUTH = '── 去序真相图（opaque alias 无顺序含义）──'
BLOCK_BLIND_BOOK_STRATEGY = '── 章级功能与确定性计数（整书宏观策略输入）──'
BLOCK_BLIND_CHAPTER_TRUTH = '── 单章去序真相图（local alias 仅在本请求有效）──'
BLOCK_ACCEPTED_BOOK_STRATEGY = '── 已接受的整书策略与本章权重（不得修改）──'
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
