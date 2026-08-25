#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage4.py —— 对应 `docs/schema/stage4.md`。重构计划 + 写作上下文。**两份产物，形状完全不同。**

```
plan_chapters.json    LLM 产，全书一次调用 —— 只存决策与引用，一个字内容都不经它
writer_context.json   确定性组装（非 LLM）—— 写作节点的唯一输入，可 0 成本重建
```

【五条贯穿本层的事实】

  ① **LLM 产场景设计**：`reconstruction_strategy`、逐章安全 brief、按新叙事顺序排列的
     `scenes[]` 与权重。事实真值、因果边和状态增量来自 Stage2，不由 Stage4 重写。

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
from typing import Literal

from pydantic import Field, model_validator

from contracts.invariants import (
    CACHE_POLICY,
    ADDRESS_CONTEXT, CHAPTER_WEIGHTS, DELIVERY_MODES, GOAL_INTENTS,
    NAME_DISTINCTIVENESS, NARRATIVE_PERSON, RESPONSE_CHAPTER_WEIGHTS,
    RESPONSE_DELIVERY_MODES, RESPONSE_GOAL_INTENTS, RESPONSE_STRUCTURAL_ROLES,
    RESPONSE_TEMPORAL_LINKS, RESPONSE_UNIT_WEIGHTS, STRUCTURAL_ROLES,
    TEMPORAL_LINKS, TIME_LAYERS, UNIT_WEIGHTS, WORLD_RULE_KINDS,
)
from contracts.schema.base import Fp10, StrictModel
from contracts.schema.common import FreeProvenance, LlmProvenance

from contracts.schema.stage2 import (
    BEAT_ID_PATTERN, FACT_ID_PATTERN, HINGE_ID_PATTERN, CausalEdge,
)


# ─────────────────────────────────────────────────────────────────────────────
# 一、`plan_chapters.json`
# ─────────────────────────────────────────────────────────────────────────────

class PlanScene(StrictModel):
    """Stage4 新设计的一场；引用事实契约，不引用源事件清单。"""

    slot: str = Field(pattern=r'^s\d{2,4}$')
    budget_chars: int = Field(gt=0)
    weight: Literal[UNIT_WEIGHTS]
    purpose: str = Field(min_length=1)
    role: Literal[STRUCTURAL_ROLES]
    delivery: Literal[DELIVERY_MODES]
    transition: Literal[TEMPORAL_LINKS]
    land_fact_refs: list[str] = Field(min_length=1)
    context_fact_refs: list[str]
    material_refs: list[str]
    turn_fact_ref: str = Field(pattern=FACT_ID_PATTERN)
    entry_state_refs: list[str]
    exit_state_refs: list[str]
    participant_refs: list[str]
    stage_refs: list[str] = Field(min_length=1)

    @model_validator(mode='after')
    def _scene_refs_are_coherent(self) -> PlanScene:
        for label, refs in (
            ('land_fact_refs', self.land_fact_refs),
            ('context_fact_refs', self.context_fact_refs),
            ('material_refs', self.material_refs),
            ('entry_state_refs', self.entry_state_refs),
            ('exit_state_refs', self.exit_state_refs),
            ('participant_refs', self.participant_refs),
            ('stage_refs', self.stage_refs),
        ):
            if len(refs) != len(set(refs)):
                raise ValueError(f'{self.slot}.{label} 不能重复引用')
        if self.turn_fact_ref not in self.land_fact_refs:
            raise ValueError(f'{self.slot}.turn_fact_ref 必须属于 land_fact_refs')
        if any(not re.match(FACT_ID_PATTERN, ref)
               for ref in self.land_fact_refs + self.context_fact_refs
               + self.entry_state_refs + self.exit_state_refs):
            raise ValueError(f'{self.slot} 存在非法 fact_id')
        if any(not re.match(HINGE_ID_PATTERN, ref)
               for ref in self.material_refs):
            raise ValueError(f'{self.slot} 存在非法 hinge_id')
        if any(not re.match(BEAT_ID_PATTERN, ref)
               for ref in self.stage_refs):
            raise ValueError(f'{self.slot} 存在非法 stage beat 引用')
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
    chapter: int = Field(ge=1)


class PlanGoal(StrictModel):
    """Typed goal; rationale is retained for audit but never sent to the writer.

    ⚠ **模型不直接产这个形状**：响应侧是 `PlanGoalResponse`（`intent` 带 `G_` 前缀），
    `decode_plan_response` 是唯一的映射落点。理由见
    `contracts/invariants.py::RESPONSE_GOAL_INTENTS`——`intent='turn'` 那次
    6 次 wire 全灭是拿真钱买的。
    """

    intent: Literal[GOAL_INTENTS]
    fact_refs: list[FactRef] = Field(min_length=1)
    rationale: str | None = Field(
        default=None, min_length=1, json_schema_extra={'prompt_private': True})


class CtxGoal(StrictModel):
    """Writer-facing goal with every internal fact reference already resolved.

    ``PlanGoal`` is the durable audit decision and therefore keeps typed IDs.
    ``CtxGoal`` is a deterministic projection: it carries only human-readable,
    chapter-safe facts, so neither IDs nor the planner's free rationale can leak
    into a writer or hook prompt.
    """

    intent: Literal[GOAL_INTENTS]
    facts: list[str] = Field(min_length=1)


class SemanticContract(StrictModel):
    """由 Stage2 确定性投影的不可改章级真相；规划模型不能重写它。"""

    chapter_function: str = Field(min_length=1)
    information_delta: list[str]
    character_state_delta: list[str]
    relationship_delta: list[str]
    reader_knowledge_delta: list[str]
    must_land_fact_refs: list[str] = Field(min_length=1)
    context_fact_refs: list[str]
    causal_edges: list[CausalEdge]
    entry_state_refs: list[str]
    ending_state_refs: list[str]
    material_refs: list[str]


class PlanChapter(StrictModel):
    """一个输出章。"""

    #: 1..N 连续。**[HARD]** 且 **[HARD] 恒等于 `from_source_ordinals[0]`**。
    output_ordinal: int = Field(ge=1)

    #: **[HARD] 长度恒为 1**——这是「禁合并/重排/删章/拆分」四条禁令的**合取式**（`R-5.2`）。
    from_source_ordinals: list[int] = Field(min_length=1, max_length=1)

    #: 三个 goal **[HARD] 不含 `STYLE_WORDS`**（15 个纯写法词）。
    #: 判 `HARD` 而不是 `INFO` 的理由是「挡下来不需要再付费」：落 `.INVALID` 后手改那一行、
    #: `--revalidate` 提升即可。
    #: ⚠ 放行的代价是实测的——旧口径下两本共 5 处命中（「张力」×4、「意象」×1）**全在 goal 里**，
    #: 全部 INFO 放行、一路进了写手 prompt。
    chapter_goal: PlanGoal
    open_hook_goal: PlanGoal
    end_hook_goal: PlanGoal

    #: **本章在全书里的分量档**（`None` = 这份计划没记档位）。
    #:
    #: 它是本章预算总量的**来源**：`allocate_budgets` 先按 `CHAPTER_WEIGHT_RATIO`
    #: 排出章间轻重，再归一化到全书目标并把冲出 `PLAN_BUDGET_BAND` 的章钳回带内。
    #: ⚠ **它与 `PlanScene.weight` 是两层，不是同一个决策的粗细两版**：
    #: 前者答「哪一章重要」（唯一表达处是章级倍率），后者答「本章里哪一场重要」。
    #: `PLAN_SYS` 的篇幅段里这两条一直是并列的两句话。
    weight: Literal[CHAPTER_WEIGHTS] | None = None
    semantic_contract: SemanticContract
    chapter_rewrite_brief: str = Field(min_length=1)
    scenes: list[PlanScene] = Field(min_length=1)

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


class PlanParams(StrictModel):
    """场景编译器版本与输入规模；不再登记源拍吸收阈值。"""

    compiler: Literal['atomic-scenes-v1']
    fact_atoms: int = Field(ge=1)
    source_stages: int = Field(ge=1)


class PlanRepair(StrictModel):
    """一次按阅读序章号执行的付费定向重规划。"""

    chapter_ordinal: int = Field(ge=1)
    failure_items: list[str] = Field(min_length=1)
    failure_evidence: Fp10
    user_prompt: Fp10
    output: Fp10
    before: Fp10
    after: Fp10
    before_hard: int = Field(ge=0)
    after_hard: int = Field(ge=0)
    model: str = Field(min_length=1)
    cache_policy: Literal[CACHE_POLICY]


class PlanProvenance(LlmProvenance):
    """`plan_chapters._provenance`。

    `inputs` 恒三条：`stage1/main_text.json` · `stage2/global_grounding.json` ·
    `stage2/chapter_blueprints.json`。

    `revalidated` 继承自 `Provenance`（`_common.md` §五），不在这里重定义。
    它是 `bool`：**`False` 与「键不在」不是一回事**。
    """

    params: PlanParams

    #: 定向重规划过哪些章（累积）。默认空 = **这份计划没有定向重规划过**，
    #: 是产地不写这一格时的中性值，不是「不知道」。（曾写作「兼容存量产物」，
    #: 而迁移机制已整批删除，那个理由不再成立。）
    partial_regen_chapters: list[int] = Field(default_factory=list)

    #: 每一次定向重规划的输入、响应与前后章指纹。
    repairs: list[PlanRepair] = Field(default_factory=list)


class PlanChapters(StrictModel):
    """`stage4/plan_chapters.json` —— 顶层 3 个键；整跑或按章付费修复后合并。

    结构化调用点声明的必需顶层键 = `reconstruction_strategy` · `output_chapters`
    ——**这是完整的**（`_provenance` 是代码写的，不归调用点管）。
    """

    #: 全书重构意图，一段话。盘上长度 80~238。
    reconstruction_strategy: str = Field(min_length=1)

    #: 长度 = 源章数（8~14）。
    #: ⚠ **产地的一条后置条件**：它为空时**先单独说一句**「这份计划里一个输出章都没有」，
    #: **不摊成 N 条「这一场没有给预算」**——旧计划实测那一次 74 条派生症状
    #: 盖住了一句话就能说清的
    #: 事实，而症状的文案指向的是**另一个根因**。
    output_chapters: list[PlanChapter] = Field(min_length=1)

    provenance: PlanProvenance = Field(alias='_provenance')


class PlanSceneResponse(StrictModel):
    """模型响应里的一场；slot 与整数预算由编译器生成。"""

    weight: Literal[tuple(RESPONSE_UNIT_WEIGHTS)]
    purpose: str = Field(min_length=1)
    role: Literal[tuple(RESPONSE_STRUCTURAL_ROLES)]
    delivery: Literal[tuple(RESPONSE_DELIVERY_MODES)]
    transition: Literal[tuple(RESPONSE_TEMPORAL_LINKS)]
    land_fact_refs: list[str] = Field(min_length=1)
    context_fact_refs: list[str]
    material_refs: list[str]
    turn_fact_ref: str = Field(pattern=FACT_ID_PATTERN)
    entry_state_refs: list[str]
    exit_state_refs: list[str]
    participant_refs: list[str]
    stage_refs: list[str] = Field(min_length=1)


class PlanGoalResponse(StrictModel):
    """**模型响应里**的 typed goal。与 `PlanGoal` 只差 `intent` 的取值面（带 `G_` 前缀）。

    ⚠ 其余两个字段刻意**共用** `PlanGoal` 的定义（`FactRef` 原样复用）：
    响应侧与产物侧只在「哪些字段被前缀化」这一点上分家，多分一寸就是第二份形状。
    """

    intent: Literal[tuple(RESPONSE_GOAL_INTENTS)]
    fact_refs: list[FactRef] = Field(min_length=1)
    rationale: str | None = Field(
        default=None, min_length=1, json_schema_extra={'prompt_private': True})


class PlanChapterResponse(StrictModel):
    """**模型响应里**的一个输出章。

    ⚠ **这里刻意没有那条「`output_ordinal` 恒等于源章号」的 `model_validator`**：
    落盘那一侧（`PlanChapter`）有它，因为那时它是**代码写的**那一侧的后置条件；
    而这一侧是**模型写的**，违约的代价是「人手改一行 + 0 成本重判」——
    归载体就等于把它换成一次付费重试。判据 `one_to_one` 在盘上那份 dict 上判它。
    """

    output_ordinal: int = Field(ge=1)
    from_source_ordinals: list[int] = Field(min_length=1)
    chapter_goal: PlanGoalResponse
    open_hook_goal: PlanGoalResponse
    end_hook_goal: PlanGoalResponse

    #: **本章在全书里的分量档**。本章预算总量由它算出来（见 `PlanChapter.weight`）。
    #: ⚠ 它刻意排在三个 goal 之后、`segments` 之前：与单元级那一格**隔着整个 segments 数组**，
    #: 两张表在响应 JSON 里不相邻，串表的形状本身就成立不了。
    weight: Literal[tuple(RESPONSE_CHAPTER_WEIGHTS)]
    chapter_rewrite_brief: str = Field(min_length=1)
    scenes: list[PlanSceneResponse] = Field(min_length=1)


class PlanResponse(StrictModel):
    """规划那一次调用**收回来的东西**。`ask_json(model_cls=…)` 用它。

    结构化调用的 `require` 从本模型字段派生：`reconstruction_strategy` 与
    `output_chapters` 都必须返回。继承的 `extra='forbid'` 拒收任何未声明字段。
    """

    reconstruction_strategy: str = Field(min_length=1)
    output_chapters: list[PlanChapterResponse] = Field(min_length=1)


class PlanEdit(StrictModel):
    """`stage4/plan_edit.json` —— 人手改好的计划（`--apply-edit` 的决策集）。

    收整份替换：章级语义契约、事实覆盖与场景状态互相引用；补丁式局部替换会造出
    半坏计划还说不清坏在哪。人工编辑正式 `PlanChapter`，同样经过完整场景校验。
    """

    reconstruction_strategy: str = Field(min_length=1)
    output_chapters: list[PlanChapter] = Field(min_length=1)


# ─────────────────────────────────────────────────────────────────────────────
# 二、`writer_context.json`
# ─────────────────────────────────────────────────────────────────────────────

class BookCharacter(StrictModel):
    """`world_facts.characters` 的一个人 —— **书级名册，给确定性代码用**。
    盘上 75 个（= stage2 registry 全量，不做章级收窄）。

    ⚠ **它与 `chapters[].characters` 的分工是「同一批人物，书级这份给代码、章级那份给模型」。**
    「避免逐章复制 20+ 人的名册」这条老理由在章级投影之后**不成立了**——
    实测某本 ch1 投影后只剩 2 人，**比全书 12 人更小**。
    它现在存在的理由只剩「代码要全量」。
    """

    #: **原文里有据**的叫法。`canonical` 查不到就退回出现最多的 `address.surface`；
    #: 一个都没有则**显式终态**——不许把 registry 的记账标签当人名发。盘上长度 1~8。
    display_name: str = Field(min_length=1)

    #: 静态身份。⚠ **`fate` 一概不下发**（写手提前知道谁最后会死，第 1 章的伏笔就写过明）。
    role: str = Field(min_length=1)

    #: **只给确定性代码**（成稿人名检查的左锚 / stage3 专名过滤），
    #: 由组 prompt 时投影掉。
    name_distinctiveness: Literal[NAME_DISTINCTIVENESS]


class WorldFacts(StrictModel):
    """**只剩这一个键**——`setting` 已收编进逐章、`license` 已归位 prompt 层。"""

    characters: dict[str, BookCharacter]


class CtxStage(StrictModel):
    """一个可执行舞台；多个源区间可以合成同一新场，也可以被拆到不同新场。"""

    time_layer: Literal[TIME_LAYERS]
    place: str | None
    present: list[str]


class CtxCausalEdge(StrictModel):
    before: str = Field(min_length=1)
    after: str = Field(min_length=1)


class CtxSemanticContract(StrictModel):
    chapter_function: str = Field(min_length=1)
    information_delta: list[str]
    character_state_delta: list[str]
    relationship_delta: list[str]
    reader_knowledge_delta: list[str]
    must_land_facts: list[str] = Field(min_length=1)
    context_facts: list[str]
    causal_edges: list[CtxCausalEdge]
    entry_state: list[str]
    ending_state: list[str]


class CtxScene(StrictModel):
    """写手真正执行的场景任务；不携带源 beat/event 编号。"""

    slot: str = Field(pattern=r'^s\d{2,4}$')
    purpose: str = Field(min_length=1)
    role: Literal[STRUCTURAL_ROLES]
    delivery: Literal[DELIVERY_MODES]
    transition: Literal[TEMPORAL_LINKS]
    budget_chars: int = Field(gt=0)
    required_facts: list[str] = Field(min_length=1)
    context_facts: list[str]
    turn_fact: str = Field(min_length=1)
    entry_state: list[str]
    exit_state: list[str]
    participants: list[str]
    stages: list[CtxStage] = Field(min_length=1)
    source_chars: int | None
    verbatim: list[str] | None = None
    keep_value: list[str] | None = None
    must_appear: list[str] | None = None
    must_hold: list[str] | None = None

    @model_validator(mode='after')
    def _turn_is_a_required_fact(self) -> CtxScene:
        if self.turn_fact not in self.required_facts:
            raise ValueError(f'{self.slot}.turn_fact 必须属于 required_facts')
        return self


class ChapterCharacter(StrictModel):
    """章级名册的一个人，**给模型**。盘上 31 章合计 518 项。
    消费方：stage5 写手 + stage6 文案 roster。
    """

    display_name: str = Field(min_length=1)
    role: str = Field(min_length=1)


class CtxRelation(StrictModel):
    """**两端都是人名**；一端叫不出名字则整条不发。盘上共 359 条。"""

    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    object: str = Field(min_length=1)


class CtxSecret(StrictModel):
    """逐章投影后的一条秘密。盘上共 114 条。"""

    secret_id: str = Field(min_length=1, json_schema_extra={'prompt_private': True})
    fact: str = Field(min_length=1)
    known_by: list[str]

    #: **唯一的读者时点标志**：`true` = 读者已知情、可以正面写；
    #: `false` = 事实可以作为伏笔出现，但本章绝不能点破。
    reader_knows_by_now: bool


class CtxWorldRule(StrictModel):
    """逐章投影后的一条规则。盘上共 87 条。

    ⚠ 键名是 `description` 不是 `desc`：正文的唯一落点是 stage2 的 `versions[].desc`，
    **无兜底，缺失即显式终态**（`or description` 那个兜底就是漏洞本身）。
    """

    rule_id: str = Field(min_length=1, json_schema_extra={'prompt_private': True})
    kind: Literal[WORLD_RULE_KINDS]
    description: str = Field(min_length=1)


class CtxCurrentStage(StrictModel):
    """⚠ **只给阶段名与跨度，不给 `summary`**：阶段摘要描述的是整个阶段的经过，
    必然含本阶段后几章才发生的结果（实测 ch16 拿到的摘要写着 ch20 才发生的事）。
    """

    stage: str = Field(min_length=1)
    chapters: list[int] = Field(min_length=1)


class CtxArcTrack(StrictModel):
    """⚠ 这里的 `subject` 已被确定性代码**解析成人话**（stage2 那份是原始 id）。
    那条链路曾经断着：未解析的 `rel_05` 直接进了写作 prompt。
    """

    subject: str = Field(min_length=1)
    dimension: str = Field(min_length=1)
    change_type: str = Field(min_length=1)
    trajectory: str = Field(min_length=1)


class CtxArc(StrictModel):
    """非末章只有 `current_stage`；**末章**另加三样。盘上 3/31 章是末章。

    ⚠ **非末章一律不发 `arc_tracks`**：剥掉 `trajectory` 剩下的
    `{char_001, 道德底线, deterioration}` 对写手是零信息，不剥就是结局。
    两头不讨好，所以整块不发。
    """

    current_stage: CtxCurrentStage
    arc_tracks: list[CtxArcTrack] | None = None
    climax: str | None = None
    ending: str | None = None


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

    era: str = Field(alias='时代背景', min_length=1)
    worldview: str = Field(alias='世界观', min_length=1)


class CtxFacts(StrictModel):
    """六块，全部逐章投影。

    ⚠⚠ **它是【整份 dict 铺进 prompt】的**（`facts_block` 做 `compact(f)`）：
    `current_stage` / `relations` / `world_rules` / `address` / `arc` 全部逐条进了
    模型的注意力，**键名只是从不字面出现**。
    **拿 grep 审这一块，默认结论是错的。**
    """

    #: 两个**中文键**的 claim 投影。Stage2 evidence 明确不进入 writer context。
    setting: CtxSetting

    relations: list[CtxRelation]
    secrets: list[CtxSecret]
    world_rules: list[CtxWorldRule]
    arc: CtxArc
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
    """一个输出章的完整任务书。"""

    #: 章号与来源章原样透传；三个目标由 PlanGoal 的 typed refs 确定性解析为
    #: CtxGoal，不携带内部 id 或审计 rationale。
    output_ordinal: int = Field(ge=1)
    from_source_ordinals: list[int] = Field(min_length=1, max_length=1)
    chapter_goal: CtxGoal
    open_hook_goal: CtxGoal
    end_hook_goal: CtxGoal
    semantic_contract: CtxSemanticContract
    chapter_rewrite_brief: str = Field(min_length=1)

    #: 本章各拍预算之和（写手不必自己加）。
    budget_chars: int = Field(gt=0)

    #: 源章真实字数（`len(整章文本)`，**含空白**）。盘上 408~1963。
    #:
    #: ⚠⚠ **同一份产物里有两个口径，不许拿它们做相等断言**：
    #: 本字段是 `len(整章文本)`，而 `budget_band` 判据的基准是 **span 合计**（约占正文 97%），
    #: `segments[].source_chars` 也是 span 合计。
    #: **两者都是对的，但它们不是同一个数**——`source_chars` 与 `sum(segments[].source_chars)`
    #: **不相等是正常的**。产地已经踩过这个坑一次：预算区间校验曾用整章字数作基准，
    #: 而规划器拿到的是 span 合计，于是一份合格的计划被判 `INVALID`。
    source_chars: int = Field(ge=0)

    scenes: list[CtxScene] = Field(min_length=1)

    #: **章级收窄**。
    characters: dict[str, ChapterCharacter] = Field(
        json_schema_extra={'prompt_private_keys': True})

    narration: list[CtxNarration] = Field(min_length=1)
    facts: CtxFacts

    #: 本章不许泄的 `secret_id`。**只给 id 不给内容**——写手看不到内容才不会写出来；
    #: 它服务的是**回写校验**，组 prompt 时不下发。盘上 0~8。
    must_not_reveal: list[str] = Field(json_schema_extra={'prompt_private': True})

    @model_validator(mode='after')
    def _ctx_slots_are_unique(self) -> CtxChapter:
        slots = [scene.slot for scene in self.scenes]
        if len(slots) != len(set(slots)):
            raise ValueError('writer-context segment slots must be unique')
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

    顶层只保存下游实际消费的全书人物投影、逐章上下文和来源账。组装结果由
    `ctx_facts_exact`、`ctx_name_attested`、`ctx_secret_gate`、`ctx_stage_explicit`
    与 `ctx_hinge_delivered` 五条后置条件规则验证；未知字段由
    `StrictModel.extra='forbid'` 拒收。
    """

    world_facts: WorldFacts
    chapters: list[CtxChapter] = Field(min_length=1)
    provenance: CtxProvenance = Field(alias='_provenance')
