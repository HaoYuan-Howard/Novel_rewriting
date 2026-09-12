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

State 只说明持续状态性质；传播权限由 KnowledgeLinks 管理。父节点的重要原文语义错漏、
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

EVIDENCE_BIND_SYS = """\
你是 Stage4B 证据绑定器。盲设计候选已经冻结；你现在看到原文证据、源舞台与必须材料，
只能为每个 candidate_scene_id 绑定 stage_refs、material_refs。人物到场由所绑舞台的 present 唯一确定。
不得改变候选的章序、
场景顺序、事实分组、goal、retain/reconstruct、变化声明、purpose、role、delivery、transition 或 weight。

candidate_sha256 必须逐字回显。若全部候选场都能在本章证据内执行，返回 status=bound，并且每个候选场
恰好绑定一次、每条必须材料恰好落一次、stage 引用来自本章关闭式目录。更早场或
更早章已经落地的事实可作后续场的 context，这不是 future leak。

若你对因果、舞台、材料或未来泄漏有语义担忧，把它写入 binding_concerns。每条 concern 只能
引用请求中下发的章号、candidate_scene_id、opaque alias、stage_ref 或 material_ref。
binding_concerns 是非阻断证据：只要能交付关闭式完整绑定，仍必须返回 status=bound。只有你认为连完整
绑定都无法给出时才可声称 status=infeasible；该声称不会自行成为正式终态，必须由编译器在同一
关闭约束集上独立证明。不得返回一份悄悄重规划或只绑定一半的候选。只输出 JSON。"""

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
    'binding_concerns': [],
}


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


def evidence_bind_system() -> str:
    return with_json_shape(EVIDENCE_BIND_SYS, EVIDENCE_BIND_HINT)


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


BLOCK_INDEX = '── 章节索引（阅读序）──'
BLOCK_FACT_REFS = '── 可引用事实目录（goal.fact_refs 只能从这里选择）──'
BLOCK_SKELETON = '── Stage2 原子事实契约与舞台目录（只作场景重组输入）──'
BLOCK_SIZES = '── 章级源字数与原子数（预算整数由代码计算）──'
BLOCK_BODY = '── 全书原文正文（不可变原文，按阅读序章号分块）──'
BLOCK_BLIND_TRUTH = '── 去序真相图（opaque alias 无顺序含义）──'
BLOCK_BLIND_BOOK_STRATEGY = '── 章级功能与确定性计数（整书宏观策略输入）──'
BLOCK_BLIND_CHAPTER_TRUTH = '── 单章去序真相图（local alias 仅在本请求有效）──'
BLOCK_ACCEPTED_BOOK_STRATEGY = '── 已接受的整书策略与本章权重（不得修改）──'
BLOCK_PREVIOUS_CHAPTER_RESPONSE = '── 上一份已审本章响应（只修目标约束）──'
BLOCK_REPAIR_CONSTRAINTS = '── 代码渲染的闭合返修约束（无自由文本理由）──'
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
