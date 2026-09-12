#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage4.py —— 对应 `docs/schema/stage4.md`。重构计划 + 写作上下文。**两份产物，形状完全不同。**

```
blind_candidate.json  Stage4A 盲设计 —— 只见去序真相图与 opaque alias
evidence_binding.json Stage4B 证据绑定 —— 只可给候选场安放舞台、人物与材料
plan_chapters.json    代码编译 A+B —— 只存决策与引用，一个字内容都不经它
writer_context.json   确定性组装（非 LLM）—— 写作节点的唯一输入，可 0 成本重建
```

【五条贯穿本层的事实】

  ① **LLM 产场景设计**：A 相的 `reconstruction_strategy`、逐章安全 brief、按新叙事顺序排列的
     `scenes[]` 与预算权重。策略和权重只作规划/编译脚手架，不进入正式计划；事实真值、
     因果边和状态增量来自 Stage2，不由 Stage4 重写。

  ② **`writer_context` 是全链的收口点**：叫法时点 / 秘密揭示章 / 规则放行章 /
     关系剧透闸门 / 弧线终点 / setting 逐章闸门——**前三层所有逐章闸门最后都汇到它一份上**。
     ⇒ 改任何一条闸门，唯一能看到效果的地方是这份产物。

  ③ **`plan ⊊ ctx`，但 brief 是安全投影，不是全文策略原样透传**：计划保留可复算 id 与审计理由，
     context 只保留本章可知的事实原子。手改 plan 而不重建 ctx 仍会分家——那正是
     `context_link`（HARD）在盯的事。

  ④ **先校验后落盘**：`_provenance` 是必填契约；失败响应只允许进入 `.INVALID`，
     不得晋升为正式计划。

【判据 vs 载体：本层一条判据都没有真的上移】
`budget_int` / `style_words` 守的东西一模一样可以用类型表达，但那样做会
**把一条 0 成本出路（手改一行 + `--revalidate`）换成一次付费重试**。
反过来 `extra='forbid'` 归载体，理由也在这条判准上：模型产了我们没要的键 ⇒ prompt 漂了，
整份响应本来就坏了、留着也没用，**而且要在生成下一章之前就停住**。
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from contracts.invariants import (
    BLIND_ALIAS_ALGORITHM,
    ADDRESS_CONTEXT, BINDING_CONCERN_CODES, BINDING_INFEASIBILITY_CODES,
    DELIVERY_MODES, GOAL_INTENTS,
    NARRATIVE_PERSON, RESPONSE_CHAPTER_WEIGHTS,
    RESPONSE_DELIVERY_MODES, RESPONSE_GOAL_INTENTS, RESPONSE_STRUCTURAL_ROLES,
    RESPONSE_TEMPORAL_LINKS, RESPONSE_UNIT_WEIGHTS, STRUCTURAL_ROLES,
    STRUCTURE_CHANGE_KINDS, STRUCTURE_MODES,
    TEMPORAL_LINKS, TIME_LAYERS, WORLD_RULE_KINDS,
)
from contracts.schema.base import FrozenModel, StrictModel
from contracts.schema.common import (
    FreeProvenance, LlmProvenance, Sha256,
)

from contracts.schema.stage2 import (
    BEAT_ID_PATTERN, FACT_ID_PATTERN, HINGE_ID_PATTERN, CausalEdge,
)
from contracts.schema.plot import PlotEndpoint, PlotSubjectRef, BeatPlacement, PlotAnchorBinding


# ─────────────────────────────────────────────────────────────────────────────
# 一、`plan_chapters.json`
# ─────────────────────────────────────────────────────────────────────────────

_BLIND_ALIAS_PATTERN = r'^a_[0-9a-f]{16,64}$'
_BLIND_LOCAL_FACT_ALIAS_PATTERN = r'^f_[0-9a-f]{6,64}$'
_CANDIDATE_SCENE_PATTERN = r'^c\d{2,}s\d{2,}$'


class StructureDecision(StrictModel):
    """章级人工结构选择；实际变化由当前计划现场复算，不重复落盘。"""

    mode: Literal[STRUCTURE_MODES]
    reason: str = Field(min_length=1)
    declared_change_kinds: list[Literal[STRUCTURE_CHANGE_KINDS]]

    @model_validator(mode='after')
    def _decision_is_canonical(self) -> StructureDecision:
        order = {name: index for index, name in enumerate(STRUCTURE_CHANGE_KINDS)}
        values = self.declared_change_kinds
        if values != sorted(set(values), key=order.__getitem__):
            raise ValueError('declared_change_kinds 必须去重并按契约顺序排列')
        if self.mode == 'retain' and self.declared_change_kinds:
            raise ValueError('retain 不得声称主动改变结构维度')
        if self.mode == 'reconstruct' and len(self.declared_change_kinds) < 2:
            raise ValueError('reconstruct 必须声明至少两类结构变化')
        return self


class BlindStructureDecision(StrictModel):
    """Stage4A 只声明意图；实际变化由最终计划现场复算。

    ⚠ **`declared_change_kinds` 是集合，不是序列。** 去重与排序是代码本来就精确
    知道的事（判据：代码知道的别去问模型），把它判成响应违约，代价是**一次全书
    重出**——Stage4A 是本仓最贵、最容易空正文的那一次调用。因此这里在 `before`
    阶段先做确定性归一，`after` 的 canonical 断言随即变成一条**永远不该红的
    后置条件**：它仍留着，因为人工/历史字节仍走同一个模型，而归一只接受
    「全部取值都合法」的输入，非法枚举照旧原样交给 pydantic 报错。
    """

    mode: Literal[STRUCTURE_MODES]
    reason: str = Field(min_length=1)
    declared_change_kinds: list[Literal[STRUCTURE_CHANGE_KINDS]]

    @model_validator(mode='before')
    @classmethod
    def _canonicalize_declared_change_kinds(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        raw = value.get('declared_change_kinds')
        if not isinstance(raw, list) or any(
                item not in STRUCTURE_CHANGE_KINDS for item in raw):
            return value
        order = {name: index for index, name in enumerate(STRUCTURE_CHANGE_KINDS)}
        return {**value,
                'declared_change_kinds': sorted(set(raw), key=order.__getitem__)}

    @model_validator(mode='after')
    def _declared_decision_is_canonical(self) -> BlindStructureDecision:
        order = {name: index for index, name in enumerate(STRUCTURE_CHANGE_KINDS)}
        if self.declared_change_kinds != sorted(
            set(self.declared_change_kinds), key=order.__getitem__,
        ):
            raise ValueError('declared_change_kinds 必须去重并按契约顺序排列')
        if self.mode == 'retain' and self.declared_change_kinds:
            raise ValueError('retain 不得声称主动改变结构维度')
        if self.mode == 'reconstruct' and len(self.declared_change_kinds) < 2:
            raise ValueError('reconstruct 必须声明至少两类结构变化')
        return self

class PlanScene(StrictModel):
    """场景只保存组织决策；实际节点清单始终从章级 placement 派生。"""

    slot: str = Field(pattern=r'^s\d{2,4}$')
    budget_chars: int = Field(gt=0)
    purpose: str = Field(min_length=1)
    role: Literal[STRUCTURAL_ROLES]
    delivery: Literal[DELIVERY_MODES]
    transition: Literal[TEMPORAL_LINKS]
    context_beat_refs: list[str]
    turn_beat_ref: str = Field(pattern=r'^b\d{2,}_\d{2,}$')

    @model_validator(mode='after')
    def _unique_background(self):
        if len(self.context_beat_refs) != len(set(self.context_beat_refs)):
            raise ValueError('场景背景节点引用不能重复')
        return self


class FactRef(StrictModel):
    """A recomputable Stage1/2 fact reference, not a free-text justification."""

    kind: Literal[
        'character', 'fact_atom', 'world_rule', 'secret', 'relation',
        'setting', 'chapter_boundary',
    ]
    # Internal joins are valid in the planning artifact, but they are never a
    # writer-facing language.  Marking the value private makes the final-message
    # guard catch any future shortcut that sends PlanGoal itself to Stage5/6.
    ref: str = Field(min_length=1, json_schema_extra={'prompt_private': True})


class PlanGoal(StrictModel):
    """已接受目标与显式场景锚点；不保存第二份剧情文字。"""

    intent: Literal[GOAL_INTENTS]
    subject_refs: list[PlotSubjectRef] = Field(min_length=1)
    anchor_beat_ref: str = Field(pattern=r'^b\d{2,}_\d{2,}$')
    anchor_scene_ref: str = Field(pattern=r'^s\d{2,4}$')


class CtxGoal(PlanGoal):
    """目标引用章级唯一主体，不再复制剧情正文。"""


class SemanticContract(StrictModel):
    """由 Blueprint 投影的引用契约；完整正文只在 beat.content。"""

    chapter_function: str = Field(min_length=1)
    beat_refs: list[str] = Field(min_length=1)
    state_refs: list[str]
    constraint_refs: list[str]
    entry_state_refs: list[str]
    carry_out_refs: list[str]


class PlanChapter(StrictModel):
    """一个输出章。"""

    #: 1..N 连续。**[HARD]** 且 **[HARD] 恒等于 `from_source_ordinals[0]`**。
    output_ordinal: int = Field(ge=1)

    #: **[HARD] 长度恒为 1**——这是「禁合并/重排/删章/拆分」四条禁令的**合取式**（`R-5.2`）。
    from_source_ordinals: list[int] = Field(min_length=1, max_length=1)

    #: 三个 goal 现只含 enum + typed refs，**没有可扫描的自由文本**。
    #: `style_words` **[HARD]** 扫 A 策略 / `chapter_rewrite_brief` / 场景 `purpose`；
    #: 但与该字段显式 land/context claim 连续对齐的**足够长中文引文**是事实词义，
    #: 不是写法指令——无引号、短引号、跨场跨章与同字段第二次无据出现均不豁免。
    #: A 与正式复检共用 `novel/stages/stage4/checks.py::style_word_findings`，
    #: 不做全章/全书词面豁免。判 `HARD` 而不是 `INFO` 的理由仍是「挡下来不需要再付费」：
    #: 正式计划红后有手改 + `--revalidate` 的 0 成本路。
    #: ⚠ 放行的代价是实测的——旧口径下两本共 5 处命中（「张力」×4、「意象」×1）
    #: 全部 INFO 放行、一路进了写手 prompt。
    chapter_goal: PlanGoal
    open_hook_goal: PlanGoal
    end_hook_goal: PlanGoal
    structure_decision: StructureDecision
    chapter_rewrite_brief: str = Field(min_length=1)
    scenes: list[PlanScene] = Field(min_length=1)
    beat_placements: list[BeatPlacement] = Field(min_length=1)
    anchor_bindings: list[PlotAnchorBinding]

    @model_validator(mode='after')
    def _output_ordinal_equals_its_only_source(self) -> PlanChapter:
        """一章对一章。它是**代码写的**那一侧的后置条件 ⇒ 归载体，拒收＝代码 bug。"""
        if self.from_source_ordinals[0] != self.output_ordinal:
            raise ValueError(
                f'output_ordinal={self.output_ordinal} 而 from_source_ordinals='
                f'{self.from_source_ordinals}——一章对一章，两者必须相等'
            )
        return self

    @model_validator(mode='after')
    def _scene_slots_are_unique(self) -> PlanChapter:
        slots = [scene.slot for scene in self.scenes]
        if len(slots) != len(set(slots)):
            raise ValueError('scene slots must be unique')
        return self


class PlanProvenance(FreeProvenance):
    """`plan_chapters._provenance`：A+B 的确定性编译账，不复制任何一相的 LLM usage。

    `inputs` 精确绑定 blind candidate、evidence binding 与编译器实际读取的 Stage1/2
    证据；两次模型调用各自只在自己的中间产物上记账。

    `revalidated` 继承自 `Provenance`（`_common.md` §五），不在这里重定义。
    它是 `bool`：**`False` 与「键不在」不是一回事**。
    """

    origin: Literal['compiled', 'human'] = 'compiled'
    blind_candidate_sha256: Sha256 | None = None
    evidence_binding_sha256: Sha256 | None = None

    @model_validator(mode='after')
    def _phase_links_match_origin(self) -> PlanProvenance:
        if self.origin == 'compiled':
            if self.blind_candidate_sha256 is None or self.evidence_binding_sha256 is None:
                raise ValueError('compiled 计划必须绑定 A/B 两份完整 SHA-256')
        elif self.blind_candidate_sha256 is not None or self.evidence_binding_sha256 is not None:
            raise ValueError('human 计划不得继续冒充旧 A/B 候选的确定性编译结果')
        return self


class PlanChapters(StrictModel):
    """`stage4/plan_chapters.json` —— A+B 整书编译物。

    两个付费响应分别由 ``BlindPlanResponse`` 与
    ``EvidenceBindingResponse`` 收口；本模型只承载代码编译后的业务形状与
    0 成本 provenance，不再存在单次规划或按章响应形。
    """

    #: 长度 = 源章数（8~14）。
    #: ⚠ **产地的一条后置条件**：它为空时**先单独说一句**「这份计划里一个输出章都没有」，
    #: **不摊成 N 条「这一场没有给预算」**——旧计划实测那一次 74 条派生症状
    #: 盖住了一句话就能说清的
    #: 事实，而症状的文案指向的是**另一个根因**。
    output_chapters: list[PlanChapter] = Field(min_length=1)

    provenance: PlanProvenance = Field(alias='_provenance')










# ── Stage4A：盲设计响应与中间产物 ────────────────────────────────────────────

class BlindChapterWeightResponse(StrictModel):
    """整书策略相只负责跨章可比较的相对轻重。"""

    chapter_ordinal: int = Field(ge=1)
    weight: Literal[tuple(RESPONSE_CHAPTER_WEIGHTS)]


class BlindBookStrategyResponse(StrictModel):
    """Stage4A 的小型整书策略响应；不携带任何事实 alias 或场景设计。"""

    reconstruction_strategy: str = Field(min_length=1)
    chapter_weights: list[BlindChapterWeightResponse] = Field(min_length=1)

    @model_validator(mode='after')
    def _chapter_weights_are_unique(self) -> BlindBookStrategyResponse:
        ordinals = [row.chapter_ordinal for row in self.chapter_weights]
        if len(ordinals) != len(set(ordinals)):
            raise ValueError('chapter_weights.chapter_ordinal 不能重复')
        return self


class BlindGoalResponse(StrictModel):
    """目标引用完整主体；锚点是规划选择，不能借目标正文另造事实。"""

    intent: Literal[tuple(RESPONSE_GOAL_INTENTS)]
    subject_aliases: list[str] = Field(min_length=1)
    anchor_beat_alias: str = Field(pattern=r'^[au]_[0-9a-f]{6,64}$')
    anchor_scene_ref: str = Field(pattern=r'^s\d{2,4}$')

    @model_validator(mode='after')
    def _unique_subjects(self) -> BlindGoalResponse:
        if len(self.subject_aliases) != len(set(self.subject_aliases)):
            raise ValueError('目标主体引用不能重复')
        if any(not re.fullmatch(r'[au]_[0-9a-f]{6,64}', v) for v in self.subject_aliases):
            raise ValueError('目标只能引用当前规划投影中的 opaque 主体')
        return self


class BlindLocalGoalResponse(BlindGoalResponse):
    """局部 alias 的章级目标；编译器核对本次局部映射。"""


class BlindPresentationDelivery(StrictModel):
    """A 只决定呈现任务；证据由 B 绑定，不在盲请求里伪造证据。"""

    scene_ref: str = Field(pattern=r'^s\d{2,4}$')
    presentation_instruction: str | None


class BlindBeatPlacement(StrictModel):
    beat_alias: str = Field(pattern=r'^[au]_[0-9a-f]{6,64}$')
    deliveries: list[BlindPresentationDelivery] = Field(min_length=1)

    @model_validator(mode='after')
    def _one_delivery_per_scene(self) -> BlindBeatPlacement:
        refs = [row.scene_ref for row in self.deliveries]
        if len(refs) != len(set(refs)):
            raise ValueError('同一节点在同一场景只能有一条呈现安排')
        if len(refs) > 1 and any(
            not row.presentation_instruction or not row.presentation_instruction.strip()
            for row in self.deliveries
        ):
            raise ValueError('分场节点的每条 delivery 必须说明呈现任务')
        return self


class BlindAnchorBinding(StrictModel):
    endpoint_alias: str = Field(pattern=r'^[au]_[0-9a-f]{6,64}$')
    scene_ref: str = Field(pattern=r'^s\d{2,4}$')


class BlindSceneResponse(StrictModel):
    scene_ref: str = Field(pattern=r'^s\d{2,4}$')
    weight: Literal[tuple(RESPONSE_UNIT_WEIGHTS)]
    purpose: str = Field(min_length=1)
    role: Literal[tuple(RESPONSE_STRUCTURAL_ROLES)]
    delivery: Literal[tuple(RESPONSE_DELIVERY_MODES)]
    transition: Literal[tuple(RESPONSE_TEMPORAL_LINKS)]
    context_beat_aliases: list[str]
    turn_beat_alias: str = Field(pattern=r'^[au]_[0-9a-f]{6,64}$')

    @model_validator(mode='after')
    def _context_unique(self) -> BlindSceneResponse:
        if len(self.context_beat_aliases) != len(set(self.context_beat_aliases)):
            raise ValueError('背景节点引用不能重复')
        if any(not re.fullmatch(r'[au]_[0-9a-f]{6,64}', v) for v in self.context_beat_aliases):
            raise ValueError('背景只能引用当前投影的 opaque 节点')
        return self


class BlindLocalSceneResponse(BlindSceneResponse):
    """局部响应与正式候选共享形状，alias 权限由本次请求映射核验。"""


class BlindChapterDesignResponse(StrictModel):
    """章级设计响应只存场景与唯一 placement，不重复保存场景节点清单。"""

    structure_decision: BlindStructureDecision
    chapter_goal: BlindLocalGoalResponse
    open_hook_goal: BlindLocalGoalResponse
    end_hook_goal: BlindLocalGoalResponse
    chapter_rewrite_brief: str = Field(min_length=1)
    scenes: list[BlindLocalSceneResponse] = Field(min_length=1)
    beat_placements: list[BlindBeatPlacement] = Field(min_length=1)
    anchor_bindings: list[BlindAnchorBinding] = Field(min_length=0)

    @model_validator(mode='after')
    def _unique_planning_identity(self) -> BlindChapterDesignResponse:
        for values in (
            [s.scene_ref for s in self.scenes],
            [p.beat_alias for p in self.beat_placements],
            [b.endpoint_alias for b in self.anchor_bindings],
        ):
            if len(values) != len(set(values)):
                raise ValueError('场景、placement 或端点身份重复')
        return self


class BlindChapterResponse(BlindChapterDesignResponse):
    output_ordinal: int = Field(ge=1)
    weight: Literal[tuple(RESPONSE_CHAPTER_WEIGHTS)]

class BlindChapterEditRequest(StrictModel):
    """Stage4A 零模型完整章替换请求（外部 artifact v1）。

    这不是字段 patch：``chapter`` 必须是一份完整
    :class:`BlindChapterResponse`。运行时仍要用与 agent 路相同的编译器、
    schema、dynamic validator 与整书 HARD；本载体只冻结人审/候选的
    TOCTOU 身份，不铸造 prompt、model 或 usage。外部只搬一枚 opaque
    ``review_token``；candidate / 决定 / 目标章 subject / strategy / 累计约束
    的多重摘要由服务端现场重建，不进入人工请求。
    """

    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    review_token: str = Field(pattern=r'^s4er1_[0-9a-f]{64}$')
    output_ordinal: int = Field(ge=1)
    chapter: BlindChapterResponse

    @model_validator(mode='after')
    def _request_is_explicit_and_single_chapter(self) -> BlindChapterEditRequest:
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError('Stage4A 完整章 edit 的 actor/reason 不能只含空白')
        if self.chapter.output_ordinal != self.output_ordinal:
            raise ValueError('Stage4A edit 目标章必须与完整 chapter.output_ordinal 一致')
        return self


class BlindPlanResponse(StrictModel):
    reconstruction_strategy: str = Field(min_length=1)
    output_chapters: list[BlindChapterResponse] = Field(min_length=1)


class BlindCandidateScene(BlindSceneResponse):
    candidate_scene_id: str = Field(pattern=_CANDIDATE_SCENE_PATTERN)


class BlindCandidateChapter(BlindChapterResponse):
    scenes: list[BlindCandidateScene] = Field(min_length=1)

    @model_validator(mode='after')
    def _candidate_scene_ids_are_unique(self) -> BlindCandidateChapter:
        ids = [scene.candidate_scene_id for scene in self.scenes]
        if len(ids) != len(set(ids)):
            raise ValueError('candidate_scene_id 不能重复')
        return self


class DesignUnitLineage(StrictModel):
    """Stage4A 聚合候选中每个实际被采纳 unit 的跨 run 来源。"""

    chapter_ordinal: int | None = Field(default=None, ge=1)
    source_run_id: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    attempt_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{16}$')
    source_kind: Literal['inherited', 'repair', 'human']
    origin: Literal[
        'agent', 'historical_checkpoint', 'human',
    ] = 'agent'
    source_ref: Sha256 | None = None

    @model_validator(mode='after')
    def _origin_is_honest(self) -> DesignUnitLineage:
        if self.origin == 'agent':
            if self.attempt_id is None or self.source_ref is not None:
                raise ValueError('agent lineage 必须有 attempt_id 且不得伪造 source_ref')
        elif self.origin == 'historical_checkpoint':
            if self.attempt_id is None or self.source_ref is None:
                raise ValueError(
                    'historical checkpoint lineage 必须同时绑定 attempt_id 与 adoption manifest')
        elif self.attempt_id is not None or self.source_ref is None:
            raise ValueError('deterministic lineage 只用 source_ref，不得伪造 attempt_id')
        if self.source_kind == 'human' and self.origin != 'human':
            raise ValueError('source_kind=human 必须对应 human origin')
        if self.source_kind == 'repair' and self.origin not in {
            'agent', 'historical_checkpoint',
        }:
            raise ValueError('本轮 repair source 必须来自可核验 agent checkpoint')
        return self


class DesignChapterDependency(FrozenModel):
    chapter_ordinal: int = Field(ge=1)
    request_sha256: Sha256


class DesignDependencies(FrozenModel):
    """已提交候选的普通实际请求投影；修复及人工来源由提交链另外绑定。"""

    contract: Literal['stage4-design-dependencies-v1'] = 'stage4-design-dependencies-v1'
    strategy: Sha256
    chapters: list[DesignChapterDependency] = Field(min_length=1)

    @model_validator(mode='after')
    def _complete_chapters(self) -> DesignDependencies:
        if [row.chapter_ordinal for row in self.chapters] != list(range(1, len(self.chapters) + 1)):
            raise ValueError('设计实际依赖必须完整、有序覆盖 1..N')
        return self


class BlindCandidateProvenance(LlmProvenance):
    alias_algorithm: Literal[BLIND_ALIAS_ALGORITHM]
    truth_sha256: Sha256
    design_dependencies: DesignDependencies | None = None

    #: 当前 producer 必须显式提供策略与章拓扑；退役整书来源不能解码成当前候选。
    architecture: Literal[
        'book_strategy_chapters_v1',
        'book_strategy_targeted_repair_v1',
        'book_strategy_replan_v1',
    ]

    parent_candidate_sha256: Sha256 | None = None
    repair_decision_sha256: Sha256 | None = None
    unit_lineage: list[DesignUnitLineage] | None = None

    @model_validator(mode='after')
    def _repair_lineage_is_closed(self) -> BlindCandidateProvenance:
        repair = self.architecture in {
            'book_strategy_targeted_repair_v1', 'book_strategy_replan_v1',
        }
        fields = (
            self.parent_candidate_sha256,
            self.repair_decision_sha256,
            self.unit_lineage,
        )
        if repair:
            if any(value is None for value in fields) or not self.unit_lineage:
                raise ValueError('design evolution provenance 缺 parent/decision/unit lineage')
            keys = [row.chapter_ordinal for row in self.unit_lineage]
            chapters = sorted(value for value in keys if value is not None)
            if keys.count(None) != 1 or chapters != list(range(1, len(chapters) + 1)):
                raise ValueError('design evolution unit_lineage 必须恰覆盖 strategy + 1..N')
        elif any(value is not None for value in fields):
            raise ValueError('非 targeted repair 候选不得夹带 repair lineage')
        return self


class BlindCandidateHumanProvenance(FreeProvenance):
    """零 wire 完整章替换的真实来源账；结构上没有 model/prompt/usage 假身份。"""

    origin: Literal['human'] = 'human'
    alias_algorithm: Literal[BLIND_ALIAS_ALGORITHM]
    truth_sha256: Sha256
    design_dependencies: DesignDependencies | None = None
    architecture: Literal['human_chapter_edit_v1'] = 'human_chapter_edit_v1'
    parent_candidate_sha256: Sha256
    review_token: str = Field(pattern=r'^s4er1_[0-9a-f]{64}$')
    review_decision_sha256: Sha256
    output_ordinal: int = Field(ge=1)
    chapter_subject_sha256: Sha256
    strategy_epoch_sha256: Sha256
    chapter_strategy_sha256: Sha256
    constraint_set_sha256: Sha256
    edit_request_sha256: Sha256
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)

    @model_validator(mode='after')
    def _human_identity_is_nonblank(self) -> BlindCandidateHumanProvenance:
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError('Stage4A human edit actor/reason 不能只含空白')
        return self


class BlindCandidateCompiledProvenance(FreeProvenance):
    """全部响应由已验证历史采用；聚合本身没有模型调用或新模型身份。"""

    origin: Literal['compiled'] = 'compiled'
    alias_algorithm: Literal[BLIND_ALIAS_ALGORITHM]
    truth_sha256: Sha256
    design_dependencies: DesignDependencies | None = None
    architecture: Literal['book_strategy_adopted_v1'] = 'book_strategy_adopted_v1'
    sources_sha256: Sha256


class BlindReferenceMaps(StrictModel):
    """正式主体与端点的关闭映射；人物表是主体表的确定性投影。"""
    subject_aliases: dict[str, PlotSubjectRef] = Field(min_length=1)
    endpoint_aliases: dict[str, PlotEndpoint]
    participant_aliases: dict[str, str]

    @model_validator(mode='after')
    def _alias_maps_are_bijective(self) -> BlindReferenceMaps:
        for label, table in (
            ('subject_aliases', self.subject_aliases),
            ('endpoint_aliases', self.endpoint_aliases),
            ('participant_aliases', self.participant_aliases),
        ):
            if any(not re.match(_BLIND_ALIAS_PATTERN, alias) for alias in table):
                raise ValueError(f'{label} 的键必须是 opaque alias')
            identities = [value if isinstance(value, str) else value.model_dump_json()
                          for value in table.values()]
            if len(identities) != len(set(identities)):
                raise ValueError(f'{label} 的正式引用必须一一对应')
        if set(self.subject_aliases) & set(self.endpoint_aliases):
            raise ValueError('主体与端点 alias 不得占用同一身份')
        if self.participant_aliases != {token: row.ref for token, row in self.subject_aliases.items()
                                        if row.kind == 'character'}:
            raise ValueError('人物映射必须完整对应主体表中的人物')
        return self


class BlindCandidate(BlindReferenceMaps):
    alias_algorithm: Literal[BLIND_ALIAS_ALGORITHM]
    truth_sha256: Sha256
    reconstruction_strategy: str = Field(min_length=1)
    output_chapters: list[BlindCandidateChapter] = Field(min_length=1)
    provenance: BlindCandidateProvenance | BlindCandidateHumanProvenance | BlindCandidateCompiledProvenance = Field(
        alias='_provenance')


# ── Stage4B：证据绑定响应与中间产物 ──────────────────────────────────────────

class EvidenceDeliveryBinding(StrictModel):
    """B 只为 A 已登记的呈现补证据与有据舞台，不修改呈现任务。"""

    beat_ref: str = Field(pattern=r'^b\d{2,}_\d{2,}$')
    evidence_refs: list[str] = Field(min_length=1)
    source_context_refs: list[str]

    @model_validator(mode='after')
    def _unique_refs(self):
        for refs in (self.evidence_refs, self.source_context_refs):
            if len(refs) != len(set(refs)):
                raise ValueError('呈现的证据/舞台引用不能重复')
        return self


class EvidenceSceneBinding(StrictModel):
    candidate_scene_id: str = Field(pattern=_CANDIDATE_SCENE_PATTERN)
    deliveries: list[EvidenceDeliveryBinding] = Field(min_length=1)

    @model_validator(mode='after')
    def _unique_parents(self):
        refs = [r.beat_ref for r in self.deliveries]
        if len(refs) != len(set(refs)):
            raise ValueError('同场同节点只能绑定一条呈现')
        return self


class EvidenceChapterBinding(StrictModel):
    output_ordinal: int = Field(ge=1)
    scenes: list[EvidenceSceneBinding] = Field(min_length=1)

    @model_validator(mode='after')
    def _scene_bindings_are_unique(self):
        ids = [scene.candidate_scene_id for scene in self.scenes]
        if len(ids) != len(set(ids)):
            raise ValueError('同章 candidate_scene_id 不能重复绑定')
        return self


class ChapterBindingResolution(StrictModel):
    """仅选择本章编译器登记的证据/舞台歧义；不能新增呈现或改 A。"""

    ambiguity_id: str = Field(pattern=r'^[0-9a-f]{16}$')
    selected_refs: list[str] = Field(min_length=1)

    @model_validator(mode='after')
    def _unique(self):
        if len(self.selected_refs) != len(set(self.selected_refs)):
            raise ValueError('歧义选择不能重复')
        return self


class ChapterBindingResponse(StrictModel):
    """章级 B session 的唯一响应形状：只解析本章歧义 + 非阻断担忧。

    刻意**没有**章号、候选 hash 与完整绑定面——章号由 session 隐含，
    机械基线由代码持有，模型只回答代码真正问的那些自由度。
    """

    resolutions: list[ChapterBindingResolution] = Field(min_length=1)
    binding_concerns: list[BindingConcern] = Field(default_factory=list)

    @model_validator(mode='after')
    def _resolutions_are_unique(self) -> ChapterBindingResponse:
        ids = [row.ambiguity_id for row in self.resolutions]
        if len(ids) != len(set(ids)):
            raise ValueError('章级绑定解析的 ambiguity_id 不得重复')
        return self


class BindingConcern(StrictModel):
    """模型对绑定的语义担忧，只是非阻断证据，不是不可行证明。"""

    constraint_code: Literal[BINDING_CONCERN_CODES]
    chapter_ordinal: int | None = Field(default=None, ge=1)
    scenes: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)
    closed_refs: list[str] = Field(min_length=1)

    @model_validator(mode='after')
    def _closed_surface_is_unique(self) -> BindingConcern:
        for label, values in (
            ('scenes', self.scenes), ('closed_refs', self.closed_refs),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f'binding_concerns.{label} 不能重复')
        if any(not re.match(_CANDIDATE_SCENE_PATTERN, scene) for scene in self.scenes):
            raise ValueError('binding_concerns.scenes 存在非法 candidate_scene_id')
        return self


class BindingInfeasibilityWitness(StrictModel):
    """由编译器铸造的关闭式不可行证明；模型响应不接受此字段。"""

    chapter_ordinal: int = Field(ge=1)
    scenes: list[str] = Field(min_length=1)
    constraint_code: Literal[BINDING_INFEASIBILITY_CODES]
    closed_refs: list[str] = Field(min_length=1)
    proof_basis: Sha256

    @model_validator(mode='after')
    def _proof_sets_are_canonical(self) -> BindingInfeasibilityWitness:
        if self.scenes != sorted(set(self.scenes)):
            raise ValueError('infeasibility_proof.scenes 必须去重并按字典序排列')
        if self.closed_refs != sorted(set(self.closed_refs)):
            raise ValueError(
                'infeasibility_proof.closed_refs 必须去重并按字典序排列')
        return self


class EvidenceBindingResponse(StrictModel):
    status: Literal['bound', 'infeasible']
    candidate_sha256: Sha256
    chapters: list[EvidenceChapterBinding] | None = None
    binding_concerns: list[BindingConcern] = Field(default_factory=list)

    @model_validator(mode='after')
    def _status_has_exact_payload(self) -> EvidenceBindingResponse:
        if self.status == 'bound':
            if not self.chapters:
                raise ValueError('bound 必须返回非空 chapters')
        else:
            if self.chapters is not None:
                raise ValueError('infeasible 不得返回部分绑定冒充可编译结果')
            if not self.binding_concerns:
                raise ValueError(
                    'infeasible 模型声称必须给非空 binding_concerns；'
                    '它仍须由代码独立证明')
        return self


class EvidenceBindingProvenance(LlmProvenance):
    origin: Literal['llm'] = 'llm'
    candidate_sha256: Sha256

    #: 两种现役后端都由机械基线与歧义章组成，必须显式登记完整来源。
    architecture: Literal['chapter_baseline_v1']
    unit_lineage: list[dict[str, Any]] = Field(min_length=1)

    @field_validator('unit_lineage')
    @classmethod
    def validate_unit_lineage(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # 载体与 freshness 共用；缺字段的来源不能在另一读路被判为已知过时。
        if not value or any(not isinstance(row, dict) for row in value):
            raise ValueError('unit_lineage 必须是非空逐章来源表')
        ordinals = [row.get('chapter_ordinal') for row in value]
        if (any(not isinstance(n, int) or isinstance(n, bool) or n < 1 for n in ordinals)
                or len(set(ordinals)) != len(ordinals)):
            raise ValueError('unit_lineage 章号必须是正整数且不重复')
        kinds = {str(row.get('kind') or '') for row in value}
        unknown = kinds - {'baseline', 'chapter_session'}
        if unknown:
            raise ValueError(f'unit_lineage kind 越界：{sorted(unknown)}')
        for row in value:
            if row.get('kind') != 'chapter_session':
                continue
            models = row.get('models')
            revision = row.get('revision')
            ambiguities = row.get('ambiguities')
            if (not isinstance(row.get('attempt_id'), str)
                    or re.fullmatch(r'[0-9a-f]{16}', row['attempt_id']) is None
                    or not isinstance(models, list) or not models
                    or any(not isinstance(model, str) or not model for model in models)
                    or not isinstance(revision, int) or isinstance(revision, bool) or revision < 0
                    or not isinstance(ambiguities, int) or isinstance(ambiguities, bool) or ambiguities < 1):
                raise ValueError('chapter_session 缺完整响应、模型、修订或歧义来源')
            origin = row.get('origin')
            source_run_id = row.get('source_run_id')
            if (origin not in ('current_run', 'historical_checkpoint', 'work_unit_response')
                    or not isinstance(source_run_id, str)
                    or re.fullmatch(
                        r'[a-z0-9][a-z0-9_-]{0,127}', source_run_id) is None):
                raise ValueError('unit_lineage source origin/run_id 不合法')
            if origin == 'current_run':
                continue
            if origin == 'work_unit_response':
                if (not isinstance(row.get('content_turn_id'), str)
                        or re.fullmatch(r'[0-9a-f]{32}', row['content_turn_id']) is None):
                    raise ValueError('work_unit_response 缺合法 content_turn_id')
                for field in ('work_unit_id', 'source_request_fingerprint', 'source_response_sha256'):
                    if (not isinstance(row.get(field), str)
                            or re.fullmatch(r'[0-9a-f]{64}', row[field]) is None):
                        raise ValueError(f'work_unit_response 缺精确 {field}')
                if (not isinstance(row.get('attempt_id'), str)
                        or re.fullmatch(r'[0-9a-f]{16}', row['attempt_id']) is None):
                    raise ValueError('work_unit_response 缺真实 attempt_id')
                continue
            sha_fields = (
                'source_run_identity_sha256', 'source_run_file_sha256',
                'source_checkpoint_sha256', 'source_request_fingerprint',
                'source_response_sha256',
            )
            if (any(not isinstance(row.get(field), str)
                    or re.fullmatch(r'[0-9a-f]{64}', row[field]) is None
                    for field in sha_fields)
                    or not isinstance(row.get('source_code'), str)
                    or not row['source_code']
                    or not isinstance(row.get('source_model'), str)
                    or not row['source_model']
                    or not isinstance(row.get('source_checkpoint_revision'), int)
                    or isinstance(row.get('source_checkpoint_revision'), bool)
                    or row['source_checkpoint_revision'] < 1
                    or not isinstance(row.get('source_attempt_id'), str)
                    or re.fullmatch(
                        r'[0-9a-f]{16}', row['source_attempt_id']) is None
                    or row.get('attempt_id') != row['source_attempt_id']):
                raise ValueError(
                    'historical unit_lineage 缺 source-stable '
                    'run/checkpoint/attempt 身份')
        return value


class EvidenceBindingHumanProvenance(FreeProvenance):
    origin: Literal['human'] = 'human'
    candidate_sha256: Sha256


class EvidenceBindingCompiledProvenance(FreeProvenance):
    """机械绑定或全部采用的免费来源账：0 wire、无模型调用声明。

    `origin='compiled'` 如实说明这份绑定是代码从机械基线确定性编译的；
    `architecture`/`unit_lineage` 记录基线架构与逐章 unit。
    """

    origin: Literal['compiled'] = 'compiled'
    candidate_sha256: Sha256
    architecture: Literal['chapter_baseline_v1']
    unit_lineage: list[dict[str, Any]] = Field(min_length=1)
    #: 全部采用时绑定历史章实际匹配的输入契约；纯机械没有章请求回执。
    binding_input_contract: str | None = None

    @model_validator(mode='after')
    def _compiled_lineage(self):
        EvidenceBindingProvenance.validate_unit_lineage(self.unit_lineage)
        sessions = [row for row in self.unit_lineage or [] if row.get('kind') == 'chapter_session']
        if sessions and (any(row.get('origin') not in {'historical_checkpoint', 'current_run'}
                             for row in sessions)
                         or not self.binding_input_contract):
            raise ValueError('免费采用必须提供已保存来源和输入契约')
        if not sessions and self.binding_input_contract is not None:
            raise ValueError('全机械聚合不能伪造章模型输入回执')
        return self


class EvidenceBinding(EvidenceBindingResponse):
    infeasibility_proof: list[BindingInfeasibilityWitness] | None = None
    provenance: (
        EvidenceBindingProvenance | EvidenceBindingHumanProvenance
        | EvidenceBindingCompiledProvenance
    ) = Field(alias='_provenance', discriminator='origin')

    @model_validator(mode='after')
    def _formal_terminal_requires_code_proof(self) -> EvidenceBinding:
        if self.status == 'bound':
            if self.infeasibility_proof is not None:
                raise ValueError('bound 不得夹带 infeasibility_proof')
            if self.provenance.origin in {'llm', 'compiled'}:
                chapters = {chapter.output_ordinal for chapter in self.chapters}
                lineage = {row['chapter_ordinal'] for row in self.provenance.unit_lineage}
                if lineage != chapters:
                    raise ValueError('正式绑定 unit_lineage 必须完整覆盖当前章集')
        elif not self.infeasibility_proof:
            raise ValueError('infeasible 正式产物必须带代码铸造的 proof witness')
        return self


class PlanEdit(StrictModel):
    """`stage4/plan_edit.json` —— 人手改好的计划（`--apply-edit` 的决策集）。

    收整份替换：章级语义契约、事实覆盖与场景状态互相引用；补丁式局部替换会造出
    半坏计划还说不清坏在哪。人工编辑正式 `PlanChapter`，同样经过完整场景校验。
    """

    output_chapters: list[PlanChapter] = Field(min_length=1)


# ─────────────────────────────────────────────────────────────────────────────
# 二、`writer_context.json`
# ─────────────────────────────────────────────────────────────────────────────

class CtxStage(StrictModel):
    """一个可执行舞台；多个源区间可以合成同一新场，也可以被拆到不同新场。"""

    time_layer: Literal[TIME_LAYERS]
    place: str | None
    present: list[str]


class CtxCausalEdge(StrictModel):
    before: str = Field(min_length=1)
    after: str = Field(min_length=1)


class CtxSemanticContract(SemanticContract):
    """写作侧保持同一引用契约。"""


class CtxSceneKnowledge(StrictModel):
    """只在指定场景开放的知识；伏笔 content 只能是关联的表现事实。"""

    kind: Literal['secret', 'setup', 'world_rule', 'relation', 'character_state', 'setting']
    content: str = Field(min_length=1)
    usage: Literal['known', 'reveal', 'setup']
    known_by: list[str]


class CtxDisclosureWindow(StrictModel):
    """整章请求中的每条受限事实仍携带允许使用的场景；不得当作全章已知。"""

    subject_ref: str = Field(min_length=1, json_schema_extra={'prompt_private': True})
    content: str = Field(min_length=1)
    allowed_slots: list[str] = Field(min_length=1)


class CtxDialogue(StrictModel):
    """闭卷对白任务；人名按当前章解析，原句不进入写手输入。"""

    speaker: str | None
    addressees: list[str] | None
    meaning: str = Field(min_length=1)
    speech_act: str = Field(min_length=1)
    required_facts: list[str] = Field(min_length=1)


from contracts.schema.plot_context import (
    WriterBeat, WriterState, WriterConstraint, WriterGlobalSubject,
    WriterKnowledgeEvent, WriterScene,
)


class CtxScene(WriterScene):
    """由 placement 派生的逐场呈现及许可。"""


class ChapterCharacter(StrictModel):
    """章级名册的一个人，**给模型**。盘上 31 章合计 518 项。
    消费方：stage5 写手 + stage6 文案 roster。
    """

    # V8 编号连接剧情参与者、状态持有者与知识受众；是引用键，不是正文称呼。
    char_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    role: str = Field(min_length=1)


class CtxRelation(StrictModel):
    """**两端都是人名**；一端叫不出名字则整条不发。盘上共 359 条。"""

    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    object: str = Field(min_length=1)


class CtxSecret(StrictModel):
    """逐章投影后的一条秘密。盘上共 114 条。"""

    fact: str = Field(min_length=1)
    known_by: list[str]

    #: **唯一的读者时点标志**：`true` = 读者已知情、可以正面写；
    #: `false` = 事实可以作为伏笔出现，但本章绝不能点破。
    reader_knows_by_now: Literal[True]


class CtxWorldRule(StrictModel):
    """逐章投影后的一条规则。盘上共 87 条。

    ⚠ 键名是 `description` 不是 `desc`：正文的唯一落点是 stage2 的 `versions[].desc`，
    **无兜底，缺失即显式终态**（`or description` 那个兜底就是漏洞本身）。
    """

    kind: Literal[WORLD_RULE_KINDS]
    description: str = Field(min_length=1)


class CtxAddress(StrictModel):
    """逐章投影后的一条称谓，按 `from_chapter <= o` 开闸。盘上共 1007 条。"""

    #: 被称呼的那个人的**人名**（不是 `char_id`）。
    who: str = Field(min_length=1)

    surface: str = Field(min_length=1)

    #: ⚠⚠ **它与 stage2 的 `by` 不是同一个取值面，别共用一个类型。**
    #: stage2 那一份是 `char_\\d+ ∪ {narration, public}`（**派发键**）；
    #: 这一份已经被逐章投影**解析成了人名**——盘上 27 个不同值，
    #: 只有 `narration`(533) 与 `public`(110) 是原样留下的那两个字面，
    #: 其余全是「念念」「李宝珠」这样的叫法。
    #: ⇒ 拿 stage2 的模式来收它，**1007 条里有 364 条会当场被拒**。
    #: （这条是盘上语料体检抓出来的：模型第一版正是共用了那个类型。）
    #:
    #: ⚠ **可为 `null`**：本章叫不出名字的 `char_id` **不发裸编号**——
    #: 兜底成 `char_003` 会让写手把一个内部编号当成称呼。盘上 `by=null` 1 条。
    by: str | None

    context: Literal[ADDRESS_CONTEXT]


class CtxSetting(StrictModel):
    """Stage4 写手面只保留两条 claim，不携带 Stage2 的原文证据。

    两个中文 alias 仍与 Stage2 盘上字节完全同形；这里必须是独立的**消费投影**类型，
    因为 GlobalGrounding v3 的 ``Setting`` 另有 ``*_evidence`` 必填格，而证据既不属于
    写手故事材料，也已在组装入口被刻意剥掉。复用 producer 模型会让合法投影反而拒收。
    """

    era: str | None = Field(alias='时代背景', min_length=1)
    worldview: str | None = Field(alias='世界观', min_length=1)


class CtxFacts(StrictModel):
    """必要事实，全部逐章投影。

    ⚠⚠ **它是【整份 dict 铺进 prompt】的**（`facts_block` 做 `compact(f)`）：
    `relations` / `world_rules` / `address` 全部逐条进了
    模型的注意力，**键名只是从不字面出现**。
    **拿 grep 审这一块，默认结论是错的。**
    """

    #: 两个**中文键**的 claim 投影。Stage2 evidence 明确不进入 writer context。
    setting: CtxSetting

    relations: list[CtxRelation]
    secrets: list[CtxSecret]
    world_rules: list[CtxWorldRule]
    address: list[CtxAddress]


class CtxNarration(StrictModel):
    """盘上长度恒 1。"""

    #: **位置标签**，不是段落边界描述：`全章`（单段）或 `第N段（共M段，按顺序写）`。
    #: 盘上 31/31 恒为 `全章`。
    #:
    #: ⚠ **为什么不原样透传 stage2 那一份**：stage2 的界标里**常原样引用原文**当分界
    #: （「正文主体（直至"……沉重的呼吸声"）」）——那对闭卷写手就是原文泄漏，
    #: 而且泄的往往正是本章的**末句**。
    #: 写手真正需要的只是「本章分几段、各段谁在说、什么顺序」。
    segment: str = Field(min_length=1)

    grammatical_person: Literal[NARRATIVE_PERSON]

    #: 叙述者**人名**（不是 char_id）。⚠ **叫不出名字就不写这个键，不兜底成 `char_id`**：
    #: 第一人称书的第 1 章正是这种情况（主角原文通篇写「我」），
    #: 而写手要的东西 `grammatical_person` + `pronoun_surfaces` 已经给全了。盘上 27/31。
    narrator: str | None = None

    #: 视点人物，**只在与 narrator 不同时才写**。⚠ **盘上 0/31，零样本。**
    focalizer: str | None = None

    pronoun_surfaces: list[str] | None = None


class CtxChapter(StrictModel):
    """整章任务书；同次调用能看到后场材料，表达仍按逐场许可。"""

    output_ordinal: int = Field(ge=1)
    chapter_goal: CtxGoal
    open_hook_goal: CtxGoal
    end_hook_goal: CtxGoal
    semantic_contract: CtxSemanticContract
    chapter_rewrite_brief: str = Field(min_length=1)
    beats: list[WriterBeat] = Field(min_length=1)
    states: list[WriterState]
    constraints: list[WriterConstraint]
    global_subjects: list[WriterGlobalSubject]
    restricted_subject_refs: list[PlotSubjectRef]
    knowledge_events: list[WriterKnowledgeEvent]
    scenes: list[CtxScene] = Field(min_length=1)
    characters: list[ChapterCharacter]
    narration: list[CtxNarration] = Field(min_length=1)
    address: list[CtxAddress]

    @model_validator(mode='after')
    def _closed_view(self) -> CtxChapter:
        slots = [scene.slot for scene in self.scenes]
        refs = [beat.beat_ref for beat in self.beats]
        states = {row.state_ref: row for row in self.states}
        constraints = {row.constraint_ref: row for row in self.constraints}
        events = {row.event_ref: row for row in self.knowledge_events}
        globals_ = {(row.subject_ref.kind, row.subject_ref.ref): row for row in self.global_subjects}
        restricted = {(row.kind, row.ref) for row in self.restricted_subject_refs}
        for size, values in (
            (len(slots), slots), (len(refs), refs), (len(self.states), states),
            (len(self.constraints), constraints), (len(self.knowledge_events), events),
            (len(self.global_subjects), globals_), (len(self.restricted_subject_refs), restricted),
        ):
            if len(set(values)) != size:
                raise ValueError('写作目录、场景和权限的身份必须唯一')
        if set(globals_) & restricted:
            raise ValueError('同一共享主体不能同时下发正文和声明未开放')
        if (set(self.semantic_contract.beat_refs) != set(refs)
                or set(self.semantic_contract.constraint_refs) != set(constraints)
                or not set(self.semantic_contract.state_refs) <= states.keys()
                or not set(self.semantic_contract.entry_state_refs) <= states.keys()
                or not set(self.semantic_contract.carry_out_refs) <= states.keys()):
            raise ValueError('语义契约必须指向当前章级目录')
        completed = {ref: max((i for i, scene in enumerate(self.scenes)
                              if any(d.beat_ref == ref for d in scene.deliveries)), default=-1)
                     for ref in refs}
        if any(index < 0 for index in completed.values()):
            raise ValueError('剧情目录必须完整安排呈现')
        for index, scene in enumerate(self.scenes):
            current = {row.beat_ref for row in scene.deliveries}
            if not (current | set(scene.context_beat_refs)) <= set(refs):
                raise ValueError('场景引用不属于章级剧情目录')
            if any(completed[ref] >= index for ref in scene.context_beat_refs):
                raise ValueError('节点未完成全部呈现，不能作为完整背景')
            expected_events = [ref for ref, row in events.items() if scene.slot in row.allowed_slots]
            if scene.knowledge_event_refs != expected_events:
                raise ValueError('逐场知识引用必须精确等于下发事件的许可')
            if (not set(scene.constraint_refs) <= constraints.keys()
                    or any(row.state_ref not in states for row in scene.state_events)):
                raise ValueError('场景状态或约束引用不在目录')
        for row in events.values():
            expected = slots if row.timing == 'at_entry' else (
                slots[slots.index(row.slot):] if row.slot in slots else [])
            if row.allowed_slots != expected or (row.timing == 'at_entry') != (row.slot is None):
                raise ValueError('知识事件位置与许可不一致')
        for row in self.global_subjects:
            if row.allowed_slots != [slot for slot in slots if slot in row.allowed_slots]:
                raise ValueError('共享主体许可须是场景的唯一有序子集')
            for permission in row.state_permissions:
                state = states.get(permission.state_ref)
                if (state is None or state.global_ref != row.subject_ref
                        or state.modality != permission.modality
                        or permission.allowed_slots != [slot for slot in slots
                                                        if slot in permission.allowed_slots]):
                    raise ValueError('命题表达许可必须指向同一主体及认识性质的状态')
                reveals = [e for e in events.values() if e.kind == 'state_reveal'
                           and e.subject_ref.ref == state.state_ref]
                if len(reveals) != 1 or permission.allowed_slots != reveals[0].allowed_slots:
                    raise ValueError('命题表达许可必须由已登记的状态披露事件编译')
        return self


class CtxProvenance(FreeProvenance):
    """`writer_context._provenance` —— **没有 `prompts`、没有 `model`**（确定性组装）。

    `inputs` 四条：stage1 正文 · stage2 两份 · **`stage4/plan_chapters.json`**。

    ⚠ 最后那条边是全仓唯一的一处「显式追踪」，它存在的理由就是 `context_link`（HARD）
    那个问题：「写手手里这份 ctx，是不是照盘上这份计划组的」。
    **代价已经付过一次**：某轮 23 章成稿与盘上任何一版 `writer_context` 都配不上
    （匹配 1/23），产出它的那份已被 `stage4.context` 覆盖，**配对关系永久丢失**。
    """


class WriterContext(StrictModel):
    """`stage4/writer_context.json`：确定性组装的当前写作输入。

    顶层只保存逐章上下文和来源账。组装结果由 `ctx_facts_exact`、
    `ctx_name_attested`、`ctx_stage_explicit` 与 `ctx_hinge_delivered`
    四条后置条件规则验证；未知字段由
    `StrictModel.extra='forbid'` 拒收。
    """

    chapters: list[CtxChapter] = Field(min_length=1)
    provenance: CtxProvenance = Field(alias='_provenance')
