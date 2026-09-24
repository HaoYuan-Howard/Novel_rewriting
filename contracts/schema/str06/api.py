#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""api —— STR-06 候选链只读监控面的响应模型（F7d）。

【与 `contracts/schema/api.py` 的分界】现役 API 契约管现役链；本模块管候选链的
只读监控面——F8 激活前候选路由不进现役注册表，也不伪装成已切换产品。两条纪律
与现役一致：
  ① 只组合，不手抄取值面（字面量取值面的唯一落点是 `contracts/invariants.py`）；
  ② 一个字段都不自己算判定（`R-11.4`）：没有 `badge` / `passed` / `counts`——
     候选链的判定只有现役 check 报告与机械回执的 `mechanical_checks` 名单，
     本模块给的是「盘上有什么、回执写了什么」并排呈现。

【缺席与未知分开（I-42）】候选根可能只跑到某一腿（`--through stage4` 就没有
stage5/stage7 产物）：每个面用 `present` 显式表达「盘上有没有」，没有就用
`None` 字段缺席——不拿空数组冒充合法空值，不拿旧轮产物冒充当前结论。
`semantic` 恒 `unverified`：章级机械回执不升级为语义已验，正文 Judge 已取消。

【provenance 不进监控面】盘上 `_provenance` 是来源账事实，由 views 剥掉；
监控面要回答「这本书写到哪了」，不是「这份产物怎么来的」——后者在盘上与
`--check-only` 复检里核。
"""
from __future__ import annotations

from typing import Any

from pydantic import Field

from contracts.schema.base import StrictModel

__all__ = [
    'Str06SurfaceWindowView', 'Str06CharacterView', 'Str06NameStructureView',
    'Str06CharactersView', 'Str06BlueprintBeatView', 'Str06BlueprintChapterView',
    'Str06BlueprintView', 'Str06DesignSceneView', 'Str06DesignChapterView',
    'Str06DesignHookView', 'Str06DesignBindingView', 'Str06DesignView',
    'Str06IrChapterView', 'Str06IrNavView', 'Str06ChapterReceiptView',
    'Str06ChapterView', 'Str06HookView', 'Str06NamingEntryView', 'Str06NamingView',
    'Str06DeliveryChapterView', 'Str06DeliveryView', 'Str06NodeView',
    'Str06LedgerNodeCostView', 'Str06LedgerCostView', 'Str06EpochView',
    'Str06RunView', 'Str06CandidateSummary', 'Str06CandidateList',
]


class Str06SurfaceWindowView(StrictModel):
    """一个称谓的时间窗（源事实）：从 from_chapter 起可解析到该人物。"""

    surface: str = Field(min_length=1)
    from_chapter: int = Field(ge=1)
    until_chapter: int | None = None


class Str06CharacterView(StrictModel):
    char_id: str = Field(min_length=1)
    canonical: str = Field(min_length=1)
    surfaces: tuple[Str06SurfaceWindowView, ...] = Field(min_length=1)


class Str06NameStructureView(StrictModel):
    """源名称结构（Stage2 names 窄调用产出）：家族、名、化名与称谓构词。"""

    families: dict[str, str] = Field(default_factory=dict,
                                     description='family_id → 姓字面（可复姓）')
    people: dict[str, dict[str, Any]] = Field(default_factory=dict,
                                              description='char_id → 结构条目原样投影')


class Str06CharactersView(StrictModel):
    present: bool
    contract_version: str | None = None
    characters: tuple[Str06CharacterView, ...] = ()
    name_structure: Str06NameStructureView | None = None


class Str06BlueprintBeatView(StrictModel):
    beat_id: str = Field(min_length=1)
    event: str = Field(min_length=1)
    participants: tuple[str, ...] = ()
    effects: tuple[dict[str, Any], ...] = ()
    reveals: tuple[dict[str, Any], ...] = ()


class Str06BlueprintChapterView(StrictModel):
    chapter: int = Field(ge=1)
    beats: tuple[Str06BlueprintBeatView, ...] = Field(min_length=1)
    causal_edges: tuple[dict[str, Any], ...] = ()


class Str06BlueprintView(StrictModel):
    present: bool
    world_constraints: tuple[dict[str, Any], ...] = ()
    chapters: tuple[Str06BlueprintChapterView, ...] = ()


class Str06DesignSceneView(StrictModel):
    scene_id: str = Field(min_length=1)
    driver: dict[str, str]
    realization_brief: str = Field(min_length=1, description='导演说明原样投影')
    pov: str = Field(min_length=1)
    voice: str = Field(min_length=1)
    avoid: tuple[str, ...] = ()
    beats: tuple[dict[str, Any], ...] = Field(min_length=1)


class Str06DesignChapterView(StrictModel):
    chapter_ordinal: int = Field(ge=1)
    purpose: str = Field(min_length=1)
    scene_ids: tuple[str, ...] = Field(min_length=1)


class Str06DesignHookView(StrictModel):
    """Hook 的正式选择（PresentationGraph.hook）：只投影选择本身，不重算。

    `person`／`materials` 的取值面属于产物契约（`HookPov`／`HookMaterial`）——
    视图层不抄第二份字面量，原样投影已由产物模型校验过的值。
    """

    person: str = Field(min_length=1)
    focalizers: tuple[str, ...] = Field(min_length=1)
    materials: tuple[str, ...] = Field(min_length=1,
                                       description='按设计序的 beat_ref（乱序冷开合法）')
    concealed: tuple[str, ...] = ()
    forbidden_reveals: int = Field(ge=0, description='禁止揭示条数（逐条在盘面）')
    design_brief: str = Field(min_length=1)


class Str06DesignBindingView(StrictModel):
    obligations: int = Field(ge=0)
    entries: tuple[dict[str, Any], ...] = ()
    unbound: tuple[str, ...] = ()


class Str06DesignView(StrictModel):
    present: bool
    graph_id: str | None = None
    scenes: tuple[Str06DesignSceneView, ...] = ()
    chapters: tuple[Str06DesignChapterView, ...] = ()
    hook: Str06DesignHookView | None = None
    binding: Str06DesignBindingView | None = None


class Str06IrChapterView(StrictModel):
    chapter_ordinal: int = Field(ge=1)
    purpose: str = Field(min_length=1)
    length_budget: int = Field(ge=1)
    scene_ids: tuple[str, ...] = Field(min_length=1)
    scene_lines: tuple[str, ...] = Field(min_length=1)


class Str06IrNavView(StrictModel):
    """WriterBookIR 的导航面：章预算、章目的、逐场摘要行与称谓释放位置表。"""

    present: bool
    book_title: str = ''
    target_chapter_count: int | None = Field(default=None, ge=1)
    total_length_budget: int | None = Field(default=None, ge=1)
    length_band_low: float | None = Field(default=None, gt=0)
    length_band_high: float | None = Field(default=None, gt=0)
    chapters: tuple[Str06IrChapterView, ...] = ()
    release_schedule: tuple[dict[str, Any], ...] = ()


class Str06ChapterReceiptView(StrictModel):
    """章级回执的监控投影：机械范围与语义未评分开，篇幅披露显式。

    `status`／`semantic` 原样投影产物回执（取值面属于 `ChapterReceipt`；
    语义恒 `unverified`——机械通过不升级为语义已验）。
    """

    status: str = Field(min_length=1)
    mechanical_checks: tuple[str, ...] = Field(min_length=1)
    semantic: str = Field(min_length=1, default='unverified')
    length: dict[str, Any]
    length_disclosed: bool = False
    request_identity: str = Field(min_length=1)
    protocol_version: str = Field(min_length=1)
    renderer_version: str = Field(min_length=1)
    validator_version: str = Field(min_length=1)


class Str06ChapterView(StrictModel):
    ordinal: int = Field(ge=1)
    present: bool
    prose: str | None = None
    receipt: Str06ChapterReceiptView | None = None


class Str06HookView(StrictModel):
    present: bool
    prose: str | None = None
    receipt: dict[str, Any] | None = None


class Str06NamingEntryView(StrictModel):
    char_ref: str = Field(min_length=1)
    family: str = Field(min_length=1)
    given: str | None = None
    full: str = Field(min_length=1)
    aliases: dict[str, str] = Field(default_factory=dict)
    keep_reason: str | None = None


class Str06NamingView(StrictModel):
    present: bool
    entries: tuple[Str06NamingEntryView, ...] = ()


class Str06DeliveryChapterView(StrictModel):
    chapter_ordinal: int = Field(ge=1)
    prose_sha256: str | None = None
    length: dict[str, Any] | None = None
    length_disclosed: bool = False


class Str06DeliveryView(StrictModel):
    """成书交付的监控投影：manifest 是就绪核对记录，字节 hash 逐字转出。"""

    present: bool
    delivery_identity: str | None = None
    final_text_sha256: str | None = None
    chapters: tuple[Str06DeliveryChapterView, ...] = ()
    length_reading: dict[str, Any] | None = None
    disclosure: str | None = None


class Str06NodeView(StrictModel):
    """候选节点表的盘面事实：产物在不在是 I/O 事实，不是业务状态（I-32）。"""

    node: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    paid: bool
    produced: bool


class Str06LedgerNodeCostView(StrictModel):
    node: str = Field(min_length=1)
    rows: int = Field(ge=0)
    new_paid_rows: int = Field(ge=0)
    cached_rows: int = Field(ge=0)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None


class Str06LedgerCostView(StrictModel):
    """ledger 成本账的只读投影：缓存采用与实付分开，未知不作零（Q-11／I-43）。"""

    present: bool
    rows: int = Field(default=0, ge=0)
    new_paid_rows: int = Field(default=0, ge=0)
    cached_rows: int = Field(default=0, ge=0)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    unknown_cost_rows: int = Field(default=0, ge=0,
                                   description='usage 缺失或部分提交的行数；不并入合计')
    per_node: tuple[Str06LedgerNodeCostView, ...] = ()


class Str06EpochView(StrictModel):
    epoch_id: str = Field(min_length=1)
    ordinal: int = Field(ge=1)
    reason: str = Field(min_length=1)
    session_run_id: str | None = None


class Str06RunView(StrictModel):
    """候选根的一次运行总览：workflow 状态、epoch、节点盘面事实与成本账。"""

    candidate: str = Field(min_length=1)
    present: bool
    status: str | None = None
    failed_node: str | None = None
    accepted_body: bool | None = None
    epoch: Str06EpochView | None = None
    nodes: tuple[Str06NodeView, ...] = ()
    cost: Str06LedgerCostView | None = None


class Str06CandidateSummary(StrictModel):
    candidate: str = Field(min_length=1)
    present: bool
    status: str | None = None
    updated_at: int | None = None


class Str06CandidateList(StrictModel):
    candidates: tuple[Str06CandidateSummary, ...] = ()
