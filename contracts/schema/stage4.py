#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage4.py —— 对应 `docs/schema/stage4.md`。重构计划 + 写作上下文。**两份产物，形状完全不同。**

```
plan_chapters.json    LLM 产，全书一次调用 —— 只存决策与引用，一个字内容都不经它
writer_context.json   确定性组装（非 LLM）—— 写作节点的唯一输入，可 0 成本重建
```

【五条贯穿本层的事实】

  ① **LLM 只产三样东西**：`reconstruction_strategy` · 逐章三个 goal · **逐拍 `budget_chars`**。
     拍怎么切、怎么排、怎么并**不是 LLM 的决定**（`group_beats` 按源文切）。
     ⇒ 本层 schema 的绝大部分字段，取值面**不来自模型、来自代码**——
     这与 stage2「每一个字段都是模型写的」正好相反，**读表时不要套用那边的怀疑口径**。

  ② **`writer_context` 是全链的收口点**：叫法时点 / 秘密揭示章 / 规则放行章 /
     关系剧透闸门 / 弧线终点 / setting 逐章闸门——**前三层所有逐章闸门最后都汇到它一份上**。
     ⇒ 改任何一条闸门，唯一能看到效果的地方是这份产物。

  ③ **`plan ⊊ ctx`，而且是字节级原样透传**（实测 3/3 本 mismatch 0）。
     ⇒ **两处各存一份 goals，手改 plan 而不重建 ctx 就会分家**——那正是 `context_link`（HARD）在盯的事。

  ④ **先校验后落盘**：`_provenance` 是必填契约；失败响应只允许进入 `.INVALID`，
     不得晋升为正式计划。

【判据 vs 载体：本层一条判据都没有真的上移】
`budget_int` / `style_words` 守的东西一模一样可以用类型表达，但那样做会
**把一条 0 成本出路（手改一行 + `--revalidate`）换成一次付费重试**。
反过来 `extra='forbid'` 归载体，理由也在这条判准上：模型产了我们没要的键 ⇒ prompt 漂了，
整份响应本来就坏了、留着也没用，**而且要在生成下一章之前就停住**。
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from contracts.invariants import (
    ADDRESS_CONTEXT, NAME_DISTINCTIVENESS, NARRATIVE_PERSON, TIME_LAYERS, WORLD_RULE_KINDS,
)
from contracts.schema.base import StrictModel
from contracts.schema.common import FreeProvenance, LlmProvenance

#: ⚠ 从 stage2 借的**不是拷贝，是同一个类**：`facts.setting` 是 stage2 那两个中文键的
#: **字节级原样透传**（实测 31/31 章 mismatch 0）。各定一份就是第二个落点。
#: ⚠ 但**只借这一个**：`facts.address[].by` 看起来也该借，实际不能——见 `CtxAddress.by`。
from contracts.schema.stage2 import Setting


# ─────────────────────────────────────────────────────────────────────────────
# 一、`plan_chapters.json`
# ─────────────────────────────────────────────────────────────────────────────

class PlanBeat(StrictModel):
    """计划里的一拍。**2 个键。**

    模型响应用 `plan_beat` 标识骨架拍；`expand_plan_beats` 验证编号后把它确定性展开为
    正式产物的 `from_beats`。正式产物只保存下游实际消费的引用与预算。
    """

    #: **[HARD] 正整数**。**这是模型在拍这一层唯一的产出。** 盘上 15~720。
    #:
    #: ⚠ 它留在**判据层**不动：归载体等于把一次 0 成本手改（把某一拍写成 0 的那个数改回来）
    #: 换成一次付费重试。这里的 `gt=0` 是**同一条约束的载体侧镜像**，
    #: 而不是把判据搬上来——判据仍要报出「哪一拍、原值是多少」，那是类型给不了的。
    budget_chars: int = Field(gt=0)

    #: 引用 stage2 的 `beat_id`。长度 1~3。
    #: **代码写的**：`expand_plan_beats` 按模型给的 `plan_beat` 换回，模型不产它。
    #:
    #: ⚠ 它上面挂着五条 [HARD]，其中三条是**后置条件**不是叮嘱：
    #: `beat_ref`（引用存在且属本章）· `beat_coverage`（每个源 beat 都被覆盖——
    #: 压缩靠调低预算，不靠丢内容）· `beat_seq` · `beat_adjacent` · `beat_layer`。
    #: 后三条从前只写在 prompt 里、代码里一个门都没有，**实测盘上三条全被违反过**
    #: （重排 · 跳过中间那条并两头 · hearsay+present 并成一拍）。
    from_beats: list[str] = Field(min_length=1)


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
    chapter_goal: str = Field(min_length=1)
    open_hook_goal: str = Field(min_length=1)
    end_hook_goal: str = Field(min_length=1)

    #: **[HARD] 非空**。长度 3~13，条数与顺序照骨架切好的拍。
    beats: list[PlanBeat] = Field(min_length=1)

    @model_validator(mode='after')
    def _output_ordinal_equals_its_only_source(self) -> PlanChapter:
        """一章对一章。它是**代码写的**那一侧的后置条件 ⇒ 归载体，拒收＝代码 bug。"""
        if self.from_source_ordinals[0] != self.output_ordinal:
            raise ValueError(
                f'output_ordinal={self.output_ordinal} 而 from_source_ordinals='
                f'{self.from_source_ordinals}——一章对一章，两者必须相等'
            )
        return self


class PlanParams(StrictModel):
    """这份计划的拍是照哪一套阈值切的。盘上恒 `{absorb-small-v1, 80, 57~73}`。

    ⚠⚠ **它记了，但全仓没有一个读者**——而 `beat_seq` / `beat_adjacent` / `beat_layer`
    三条 HARD 是拿**当前**的 `group_beats` 去量的，从不读这三个值。
    也就是说这三条判据**每一次都在「拿新尺子量旧布」**，
    而这个字段本来就是为了让人发现这件事才存在的。
    ⇒ `R-5.11`：三条后置条件判据在开跑前先比一次 `params`；对不上就先说
    「这是跨版本比较」，再报判定。**参数缺失记作「不知道」，不是「匹配」。**
    """

    grouping: str = Field(min_length=1)
    beat_min_chars: int = Field(ge=1)
    plan_beats: int = Field(ge=1)


class PlanProvenance(LlmProvenance):
    """`plan_chapters._provenance`。

    `inputs` 恒三条：`stage1/main_text.json` · `stage2/global_grounding.json` ·
    `stage2/chapter_blueprints.json`。

    `revalidated` 继承自 `Provenance`（`_common.md` §五），不在这里重定义。
    它是 `bool`：**`False` 与「键不在」不是一回事**。
    """

    params: PlanParams


class PlanChapters(StrictModel):
    """`stage4/plan_chapters.json` —— 顶层 3 个键。**全书一次调用。**

    结构化调用点声明的必需顶层键 = `reconstruction_strategy` · `output_chapters`
    ——**这是完整的**（`_provenance` 是代码写的，不归调用点管）。
    """

    #: 全书重构意图，一段话。盘上长度 80~238。
    reconstruction_strategy: str = Field(min_length=1)

    #: 长度 = 源章数（8~14）。
    #: ⚠ **产地的一条后置条件**：它为空时**先单独说一句**「这份计划里一个输出章都没有」，
    #: **不摊成 N 条「这一拍没有给预算」**——实测那一次 74 条派生症状盖住了一句话就能说清的
    #: 事实，而症状的文案指向的是**另一个根因**。
    output_chapters: list[PlanChapter] = Field(min_length=1)

    provenance: PlanProvenance = Field(alias='_provenance')


class PlanBeatResponse(StrictModel):
    """**模型响应里**的一拍。⚠ 与落盘那份（`PlanBeat`）**不是同一个形状**。

    模型给的是 `plan_beat`（照抄我们发过去的编号）+ 预算；
    `from_beats` 由 `expand_plan_beats` **按编号换回来**，模型不产它。
    """

    #: `pNN_NN`。**它是模型给这一拍分预算时唯一的抓手。**
    #: ⚠ 三种违约（编号不存在 / 少给一拍 / 重复引用）一律**显式终态，无兜底**。
    plan_beat: str = Field(min_length=1)

    budget_chars: int = Field(gt=0)


class PlanChapterResponse(StrictModel):
    """**模型响应里**的一个输出章。

    ⚠ **这里刻意没有那条「`output_ordinal` 恒等于源章号」的 `model_validator`**：
    落盘那一侧（`PlanChapter`）有它，因为那时它是**代码写的**那一侧的后置条件；
    而这一侧是**模型写的**，违约的代价是「人手改一行 + 0 成本重判」——
    归载体就等于把它换成一次付费重试。判据 `one_to_one` 在盘上那份 dict 上判它。
    """

    output_ordinal: int = Field(ge=1)
    from_source_ordinals: list[int] = Field(min_length=1)
    chapter_goal: str = Field(min_length=1)
    open_hook_goal: str = Field(min_length=1)
    end_hook_goal: str = Field(min_length=1)
    beats: list[PlanBeatResponse] = Field(min_length=1)


class PlanResponse(StrictModel):
    """规划那一次调用**收回来的东西**。`ask_json(model_cls=…)` 用它。

    结构化调用的 `require` 从本模型字段派生：`reconstruction_strategy` 与
    `output_chapters` 都必须返回。继承的 `extra='forbid'` 拒收任何未声明字段。
    """

    reconstruction_strategy: str = Field(min_length=1)
    output_chapters: list[PlanChapterResponse] = Field(min_length=1)


class PlanEdit(StrictModel):
    """`stage4/plan_edit.json` —— 人手改好的计划（`--apply-edit` 的决策集）。

    **v1 收整份替换**：章与拍互相引用（`beat_coverage` / `beat_seq` 都是全书性质），
    补丁式局部替换会造出半坏计划还说不清坏在哪。
    ⚠ 形状是**落盘产物**的两个业务键（`PlanChapter`，`from_beats` 已展开）——
    人编辑的就是盘上那份 `plan_chapters.json`；`plan_beat` 编号是发给模型的抓手，
    落盘时已 pop，人产不出它，所以本路**不过** `expand_plan_beats`。
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


class BeatSegment(StrictModel):
    """跨地时的子结构。**不取并集。** 盘上 28/197 拍有，长度 2~3。"""

    time_layer: Literal[TIME_LAYERS]
    place: str | None
    present: list[str]


class CtxBeat(StrictModel):
    """写手真正落笔的单位。盘上 197 拍。

    ⚠ **四组保真材料守的是不同的维度，不是「守多少」的四档。**
    全塞进一个叫 `verbatim` 的数组，写手会把「因果前提」也当成必须逐字出现的句子，
    硬塞背景交代。映射表 `HINGE_GROUPS` 的 key **必须与 stage2 的 `HINGE_KINDS`
    逐字一致**（模块加载时断言）。
    ⚠ **未知 kind 丢弃 + 告警，不许兜底**：曾经是 `HINGE_GROUP.get(kind, 'verbatim')`，
    于是模型自造的 kind（「行动描述」）被升级成「原样写入、一个字不能改」——
    而那是一句叙述文。

    ⚠ **跨段落的 hinge 必须按段落边界拆成独立的点**：一条 55 字、跨 `\\n\\n` 捆着两三句的
    「要害台词」**就是一个段**——写手要么连原文段落结构一起复现、要么整组放弃，
    实测它选后者（某章 4 条跨段 verbatim 一条不剩地全丢）。
    """

    #: **按 `from_beats` 逐条取 stage2 的 `event` 原件**，顺序同 `from_beats`，
    #: **不合并成散文**。长度 1~3，单条 13~175 字。
    #: ⚠ **一条 event 都解析不出 ⇒ 显式终态、不落盘。**
    events: list[str] = Field(min_length=1)

    #: 透传自 plan（`budget_int` HARD 保证是正整数）。盘上 15~720。
    budget_chars: int = Field(gt=0)

    #: 所引源 beat 的 span 字数之和。**算不出保持 `null`，不估算、不拿邻居补**（键恒在）。
    #: 盘上 197/197 有值。
    source_chars: int | None

    # ── 以下八个是可选的（**同层同地时才平铺，否则走 `segments`**） ──────────

    time_layer: Literal[TIME_LAYERS] | None = None
    place: str | None = None

    #: 在场者**人名**（不是 char_id）。本章还叫不出名字的**剔除而不兜底**。
    present: list[str] | None = None

    segments: list[BeatSegment] | None = None

    #: 原文原话，**逐字写入**。盘上 137/197 拍，共 177 条，单条 3~65 字。
    verbatim: list[str] | None = None

    #: 硬数字，**值必须保住、措辞可动**（下发「30岁」、成稿「三十岁」= 满足）。
    keep_value: list[str] | None = None

    #: 物件，**必须在场并起作用、描写可变**。
    must_appear: list[str] | None = None

    #: 因果前提，**命题必须成立、不必出现在文面**——按定义查不了文面，恒 `unver`。
    must_hold: list[str] | None = None


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

    secret_id: str = Field(min_length=1)
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

    rule_id: str = Field(min_length=1)
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


class CtxFacts(StrictModel):
    """六块，全部逐章投影。

    ⚠⚠ **它是【整份 dict 铺进 prompt】的**（`facts_block` 做 `compact(f)`）：
    `current_stage` / `relations` / `world_rules` / `address` / `arc` 全部逐条进了
    模型的注意力，**键名只是从不字面出现**。
    **拿 grep 审这一块，默认结论是错的。**
    """

    #: 两个**中文键**，原样透传自 stage2（31/31 章）。逐章闸门只挡**转抄式**泄底。
    setting: Setting

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

    #: 以下五项**原样透传自 plan**（实测 mismatch 0）。
    output_ordinal: int = Field(ge=1)
    from_source_ordinals: list[int] = Field(min_length=1, max_length=1)
    chapter_goal: str = Field(min_length=1)
    open_hook_goal: str = Field(min_length=1)
    end_hook_goal: str = Field(min_length=1)

    #: 本章各拍预算之和（写手不必自己加）。
    budget_chars: int = Field(gt=0)

    #: 源章真实字数（`len(整章文本)`，**含空白**）。盘上 408~1963。
    #:
    #: ⚠⚠ **同一份产物里有两个口径，不许拿它们做相等断言**：
    #: 本字段是 `len(整章文本)`，而 `budget_band` 判据的基准是 **span 合计**（约占正文 97%），
    #: `beats[].source_chars` 也是 span 合计。
    #: **两者都是对的，但它们不是同一个数**——`source_chars` 与 `sum(beats[].source_chars)`
    #: **不相等是正常的**。产地已经踩过这个坑一次：预算区间校验曾用整章字数作基准，
    #: 而规划器拿到的是 span 合计，于是一份合格的计划被判 `INVALID`。
    source_chars: int = Field(ge=0)

    beats: list[CtxBeat] = Field(min_length=1)

    #: **章级收窄**。
    characters: dict[str, ChapterCharacter]

    narration: list[CtxNarration] = Field(min_length=1)
    facts: CtxFacts

    #: 本章不许泄的 `secret_id`。**只给 id 不给内容**——写手看不到内容才不会写出来；
    #: 它服务的是**回写校验**，组 prompt 时不下发。盘上 0~8。
    must_not_reveal: list[str]


class CtxProvenance(FreeProvenance):
    """`writer_context._provenance` —— **没有 `prompts`、没有 `model`**（确定性组装）。

    `inputs` 四条：stage1 正文 · stage2 两份 · **`stage4/plan_chapters.json`**。

    ⚠ 最后那条边是全仓唯一的一处「显式追踪」，它存在的理由就是 `context_link`（HARD）
    那个问题：「写手手里这份 ctx，是不是照盘上这份计划组的」。
    **代价已经付过一次**：某轮 23 章成稿与盘上任何一版 `writer_context` 都配不上
    （匹配 1/23），产出它的那份已被 `--context-only` 覆盖，**配对关系永久丢失**。
    """


class WriterContext(StrictModel):
    """`stage4/writer_context.json`：确定性组装的当前写作输入。

    顶层只保存下游实际消费的全书人物投影、逐章上下文和来源账。组装结果由
    `ctx_events_verbatim`、`ctx_name_attested`、`ctx_secret_gate` 与 `ctx_layer_flat`
    四条后置条件规则验证；未知字段由 `StrictModel.extra='forbid'` 拒收。
    """

    world_facts: WorldFacts
    chapters: list[CtxChapter] = Field(min_length=1)
    provenance: CtxProvenance = Field(alias='_provenance')
