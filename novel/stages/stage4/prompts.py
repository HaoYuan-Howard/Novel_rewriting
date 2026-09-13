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

# 宏观策略与逐章设计使用独立请求及输出预算。
BLIND_BOOK_STRATEGY_MAX_TOKENS = 16_000
BLIND_CHAPTER_DESIGN_MAX_TOKENS = 24_000
EVIDENCE_BIND_MAX_TOKENS = 48_000


BLIND_BOOK_STRATEGY_SYS = """\
你是 Stage4A 的整书宏观策略器。你只能看到按阅读序排列的 chapter_function 与确定性计数，
看不到节点正文、节点或人物 alias、原文、源舞台、source beat 或逐字材料。

请给出一条全书 reconstruction_strategy，并为输入中的每章选择相对篇幅权重。章号集合必须与
输入完全一致且不得重复。权重只能使用响应契约中的 CH_ 前缀枚举；它表达章间相对轻重，
不等于最终字数，预算整数由代码统一计算。不要设计逐章场景，不回显章摘要，不添加事实、人物
或证据。只谈宏观剧情组织，不指定文风，不写纯写法词：$STYLE_WORDS。只输出 JSON。""" \
    .replace('$STYLE_WORDS', '、'.join(f'「{word}」' for word in STYLE_WORDS))

BLIND_BOOK_STRATEGY_HINT: dict[str, Any] = {
    'reconstruction_strategy': '只基于章级功能与计数的全书结构意图',
    'chapter_weights': [{
        'chapter_ordinal': 1,
        'weight': '|'.join(RESPONSE_CHAPTER_WEIGHTS),
    }],
}


BLIND_CHAPTER_DESIGN_SYS = """\
你是 Stage4A 章级剧情规划者。输入是去源序的完整剧情、稀疏状态与约束、必要知识和目标端点。
opaque alias 和数组顺序不表示原文顺序。你没有原文或证据坐标，不得猜测它们。
全书策略和本章权重已冻结，不回显章号或权重。

每个 beat 是完整剧情语义，正文是唯一真相源。beat_placements 必须不重不漏覆盖本章节点，
默认只有一条 delivery。普通换场、节奏调整、合法倒叙可用少量多场 delivery 完成，
每条必须说明该场承担的呈现任务；不另写子 claim，不新增、改变或遗漏父节点含义。
同一节点同一场景只有一条 delivery；场景不再保存第二份可编辑节点列表。
presentation_instruction 是呈现安排，不能发明动机、施受关系、客观结果或知识。

场景 scene_ref 按目标阅读序为 s01、s02……。每场 turn_beat_alias 属于本场实际呈现的节点。
只有全部主要呈现都完成之后，完整节点才可进入后续场的 context_beat_aliases；
首次出现不代表全部内容已披露。同一段意义不要在多个 delivery 中重复完整表现。

对输入 endpoints，把必要状态、知识、因果、回应、观察与字面约束落到确定场景：
分场父节点必须显式填 anchor_bindings；单场节点的唯一场景由编译器补齐。
绑定只能引用已登记 endpoint_alias，不能创建知识事件、改变知晓者、把 belief/claim 升级为 world。
same_scene 两端必须同场。故事世界先后与披露顺序分开，倒叙不能更改世界因果。
三个 goal 使用 subject_aliases 引用当前材料，并用 anchor_beat_alias + anchor_scene_ref 明确目标锚点。
开篇目标在首场，结尾目标在末场。不要用场景第一次/最后一次出现替代真实端点判断。

State 只说明持续状态性质；披露时点按蓝图章级揭示字段与既有计划表达。父节点的重要原文语义错漏、
知识窗口或独立依赖缺失才是上游问题，普通呈现分场不要求重新抽取 Stage2。
语义问题如无法忠实安排，应如实交给已有设计审阅，不伪造机械闭合证明。

retain 的 declared_change_kinds 为空；reconstruct 需声明至少两类实际结构变化。
可选类别：$CHANGE_KINDS。使用响应契约的权重、role、delivery、transition 前缀枚举，
首场 X_START。只谈剧情安排，不指定文风，不写纯写法词：$STYLE_WORDS。只输出 JSON。""" \
    .replace('$CHANGE_KINDS', '、'.join(STRUCTURE_CHANGE_KINDS)) \
    .replace('$STYLE_WORDS', '、'.join(f'「{word}」' for word in STYLE_WORDS))

# 直接下发正式响应 schema；提示示例不能成为与载体分叉的第二份契约。
from contracts.schema.stage4 import BlindChapterDesignResponse, BlindPlanResponse

BLIND_CHAPTER_DESIGN_HINT = BlindChapterDesignResponse.model_json_schema()
BLIND_DESIGN_SYS = BLIND_CHAPTER_DESIGN_SYS + '\n整书响应按阅读序覆盖所有章，逐章遵守相同契约。'
BLIND_DESIGN_HINT = BlindPlanResponse.model_json_schema()

BLIND_CHAPTER_REPAIR_SYS = BLIND_CHAPTER_DESIGN_SYS + """

这是一次定向单章返修。整书策略、章权重和非目标章已冻结，不得修改或重述。
你会收到上一份本章响应与代码从受审决议闭集渲染的结构化约束；只修这些约束指向的场景/
opaque alias，同时仍须返回一份完整的本章响应。不得推测或回显审阅人的自由文本理由。"""


def blind_design_system() -> str:
    return with_json_shape(BLIND_DESIGN_SYS, BLIND_DESIGN_HINT)


def blind_book_strategy_system() -> str:
    return with_json_shape(BLIND_BOOK_STRATEGY_SYS, BLIND_BOOK_STRATEGY_HINT)


def blind_chapter_design_system() -> str:
    return with_json_shape(BLIND_CHAPTER_DESIGN_SYS, BLIND_CHAPTER_DESIGN_HINT)


def blind_chapter_repair_system() -> str:
    return with_json_shape(BLIND_CHAPTER_REPAIR_SYS, BLIND_CHAPTER_DESIGN_HINT)


CHAPTER_BINDING_SYS = """\
你是 Stage4B 的章级证据与舞台绑定解析器。整章已经接受的设计骨架被冻结。
每条 ambiguity_id 恰好解析一次，selected_refs 只能选自该条 candidates：
- delivery_evidence：选择支持本次呈现任务的原文证据，可选多段；
- source_context_selection：选择有据的时层、地点和实际在场人物上下文，可选多个。
段级证据可以由不同呈现共用；不要为消除重叠而伪造细分位置。
只解析真实歧义，不改变场景顺序、父节点分配、呈现指令、目标或语义端点。
节点正文是完整剧情；delivery 是改编安排，不能当作独立新事实。
使用本章 subject_aliases / endpoint_aliases / participant_aliases 的正式映射联结材料；
opaque alias 无源序含义。必要继承状态只读，不推测其他章节。
未知时层、地点和在场人物保持未知；被提及不代表在场。
如发现联合呈现遗漏、重复、错误归因、因果变化或提前披露，记录 binding_concerns，
供既有结构审阅处理，不自行改写 A 或上游。concern 的 scenes 只用本章场景，
closed_refs 只用 issued_ref_allowlist。concern 不能代替接受决定。
只输出 JSON。"""

CHAPTER_BINDING_HINT: dict[str, Any] = {
    'resolutions': [{
        'ambiguity_id': '0123456789abcdef',
        'selected_refs': ['本条 candidates 中的引用'],
    }],
    'binding_concerns': [],
}

BLOCK_CHAPTER_BASELINE = '── 本章机械基线（代码已确定的归属，不得改动）──'
BLOCK_CHAPTER_AMBIGUITIES = '── 本章待解析歧义（每个 ambiguity_id 恰好解析一次）──'
BLOCK_CHAPTER_CANDIDATE = '── 本章冻结候选场景（只含本章）──'
BLOCK_CHAPTER_EVIDENCE = '── 本章关闭式证据目录与相关原文 ──'
BLOCK_CHAPTER_NEIGHBORS = '── 相邻章边界摘要（只作承接判断，不得引用其正文）──'


def chapter_binding_system() -> str:
    return with_json_shape(CHAPTER_BINDING_SYS, CHAPTER_BINDING_HINT)


BLOCK_BLIND_BOOK_STRATEGY = '── 章级功能与确定性计数（整书宏观策略输入）──'
BLOCK_BLIND_CHAPTER_TRUTH = '── 单章去序真相图（local alias 仅在本请求有效）──'
BLOCK_ACCEPTED_BOOK_STRATEGY = '── 已接受的整书策略与本章权重（不得修改）──'
BLOCK_PREVIOUS_CHAPTER_RESPONSE = '── 上一份已审本章响应（只修目标约束）──'
BLOCK_REPAIR_CONSTRAINTS = '── 代码渲染的闭合返修约束（无自由文本理由）──'
