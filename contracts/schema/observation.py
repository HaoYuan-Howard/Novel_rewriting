#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""observation —— Stage2 五节点观察与关联协议（B2；D3/D8/D9，显式版本化 I-13）。

【协议是什么】`str06.stage2.book`／`chapters`／`associate` 三个付费 semantic unit
的输出面（观察与关联决定，归已购恢复证据，不是普通缓存），以及 `names` 观察的
落盘载体。它们是 wire 协议：由 compiler 解析、执行关联并发布 canonical
（Characters＋Blueprint，唯一 writer `str06.stage2.compile`）。协议升级 = 改
版本字面量并同步本模块模型——旧协议盘面按版本字面量关闭式拒读，不做运行时
转换层（I-13：本仓无隐式通用迁移；旧根只读，新协议下新根全量重购）。

【associate 独立版本（R1，2026-09-28）】关联协议与三类观察协议分别管理：
`STAGE2_ASSOCIATE_PROTOCOL`（CS-H H4 起 stage2-associate-v3）只升 associate——
typed 作用对象显式序列化（人物／关系方向／world）、状态句柄位置编码
（``ch{nn}.b{拍}.e{效应序}.{key}``／``book.e{条目序}.{key}``）、维度句柄
（``dim{nn}``，按（作用对象，键）地址聚合；模型只配键不同但同维的维度）。
三类观察的请求与字面量逐字不变（首版 PLAN.md R1，Git 2fc03832；A1.5 出自
被它吸收的 A0–A6 提案）。

【模型输出面去机械化（I-20/I-21）】协议里**没有**这些字段，模型想给也没处给：
拍号（事件序即列表序，拍号由 compiler 按序派发）、状态/约束编号
（`state_id`／`constraint_id` 由 compiler 生成）、命题/loop/场景正式 ID
（compiler 生成）、章序（compile 按请求作用域绑定）、offset 与 hash（compiler
计算）。人物 slug 仍由模型给出——它是可读语义句柄，无法机械推导。

【章观察互不依赖（D3）】章观察只含**章内本地句柄**：没有前章快照、命题/loop
登记表与 beat 表，没有跨章正式 ID 引用，也没有因果边（v13 探针实证模型在这
一族上系统性失败：双 owner 级联、因果边前向引用）。跨章同一性——同一命题、
同一状态维度、悬念开合配对、跨章因果、跨章续场——由付费关联节点
`str06.stage2.associate` 建立：输入是 compiler 投影的**已给句柄闭集及最小文本**，
输出只选已给句柄（D8）。缺项、歧义、冲突保持未决，不默认合并或默认不同。

【转移语义延续 v13】effect／cognition／loop 三类转移、可见性、认知模式闭集、
登记键作用域与悬念开闭形状**逐字复用** `contracts/schema/source.py` 的同一批
模型（I-11：同一领域模型不复制第二份）；本模块只新增观察外壳与关联面。
"""
from __future__ import annotations

from typing import Annotated, Any, Final, Literal

from pydantic import Field, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.source import (
    Cognition, Effect, EvidenceQuote, KnowledgeSpec, LoopAction, NameStructure,
    StateTarget,
)

#: 观察协议的版本字面量（进请求身份与盘面信封；I-13 显式版本化）。升版 =
#: 换字面量＋改模型＋登记（REQUIREMENTS／DECISIONS），不做转换层。
#: R10a a1（2026-10-01）拆分：book 升 v3（输出带 basis 引文与初态/约束证据）、
#: chapters 升独立字面 v2（转移带 basis／Effect.modality）；names 输入投影
#: 未变、保持 observation-v1；associate 仍 v2。
#: RC-26/27（2026-10-04 证据寻址改版，rc15）：证据段改为**一个自然段一段**
#: （`novel/stages/chain.py::build_evidence`），段号含义随之改变——凡携带段号
#: 的观察一律换字面量（book v4／chapters v4／names v3，形状不变），旧盘面按
#: 版本关闭式拒读；同一族字面量单调递增、每个号只标一种形状（wire 与
#: canonical 共用编号序列，号不复用）。
#: 2026-10-07 basis 指针化（用户裁决「模型只选段、代码切原文」）：wire 的
#: basis 从逐字引文改为段 id——book wire 升 v6、chapters wire 升 v6、names
#: 新增独立 wire 面 v4；canonical 三者不动（EvidenceQuote 仍是段号＋引文，
#: 引文＝段全文，整段天然满足「quote 逐字在段内」），旧 canonical 盘面照读。
STAGE2_OBSERVATION_PROTOCOL: Final = 'stage2-observation-v1'
STAGE2_NAMES_PROTOCOL: Final = 'stage2-names-v3'
STAGE2_BOOK_PROTOCOL: Final = 'stage2-book-v4'
STAGE2_CHAPTERS_PROTOCOL: Final = 'stage2-chapters-v4'

#: 书级观察的 **wire 面**（2026-10-07 裁决：模型只选段、代码切原文；同日二次
#: 收口：wire/canonical 词汇分层——wire 用 `evidence_refs`，canonical 用 basis）：
#: 初态／世界约束的 evidence_refs 只给**段 id**（闭集成员），逐字引文由 compiler
#: 按段全文派生（I-20/I-21：转写文本不进模型输出面）；compile 把它派生成
#: `STAGE2_BOOK_PROTOCOL` 的 canonical。
#: CS-S 1a（2026-10-07）升 v8：`InitialFactWire.knowledge` 删——初始知情不再由
#: 模型声明（使用率 0–8/13，语义含混：引子已披露的算不算 reader_knows 指不清），
#: Phase 2 以 facts 的 known_at_start 补回该语义；canonical 的 `InitialFact.knowledge`
#: 本批保留、1b 删。
STAGE2_BOOK_WIRE_PROTOCOL: Final = 'stage2-book-v8'

#: 章观察的**瘦 wire 面**（CS-2 起；2026-10-07 basis 指针化；同日二次收口升 v7：
#: evidence_refs 词汇＋scene_location 上移拍级元数据；CS-S 1a 升 v8：`src`→`start`
#: ＋`who`→`on_stage`＋SlimEffect 删 `scope`/`hidden`/`prop`）：
#: `str06.stage2.chapters` 的 LLM 输出协议——可机械推导的字段不让模型抄写
#: （I-15/I-21：拍号、场景边界默认、时态默认、op／modality／scope／disclosure
#: 默认、target 四元组压成地址串、命题句柄按文本归并、evidence_refs 只给段 id、
#: 引文＝段全文由 compiler 派生、scene_location 是拍级字段不进 state）。
#: v8 起**证据区间由代码派生**：模型每拍只写 `start`（单段 id），拍覆盖段＝
#: `[start, 下一拍 start−1]`、末拍到章末——区间纪律从提示词纪律变成构造保证
#: （今日两本 383 拍实证同章连续、仅 1 处 owner patch 造成的重叠），`hidden`／
#: `prop`／`scope` 为死字段（5 观察集 404 效应 concealed 0、effect 命题 0、
#: 表外 scene 作用域 0）。落盘经
#: `novel/stages/stage2/slim.py::expand_chapter_wire` 派生回 canonical
#: （`STAGE2_CHAPTERS_PROTOCOL`）形状，全部下游消费者不变（S-03：适配集中在
#: slim 模块，删除条件＝下游消费迁移完成、canonical 自身瘦身时）。
STAGE2_CHAPTERS_WIRE_PROTOCOL: Final = 'stage2-chapters-v8'

#: names 的 **wire 面**（2026-10-07 basis 指针化新增；同日二次收口升 v5：
#: evidence_refs 词汇）：resolutions 的 evidence_refs 只给段 id，引文由 compiler
#: 按段全文派生。canonical（`STAGE2_NAMES_PROTOCOL`
#: v3，resolutions basis 仍是 `EvidenceQuote`）不变——旧 names.json 照常可读；
#: 旧 wire 回答（模型手抄段号＋引文／v4 basis 词汇）按版本拒收，不做推断采用。
STAGE2_NAMES_WIRE_PROTOCOL: Final = 'stage2-names-v5'

#: 关联协议（associate 独立）的版本字面量（R1，2026-09-28 升 v2；CS-H H4，
#: 2026-10-06 升 v3，RC-65）：v2＝typed 作用对象显式序列化＋状态句柄位置编码；
#: v3＝`same_state_dimension` 改配**维度句柄**（compiler 按（作用对象，键）地址
#: 聚合，``dim{nn}``）——同键写入已由编译器合并，模型只为键不同但同维的维度
#: 配对，规范键由编译器按地址传播到整类；章内已自开自闭的 close 不进可配对
#: 闭集。句柄含义与输入材料已变，旧关联回答不做句柄替换后继续采用
#: （A1.5：恢复边界）。
STAGE2_ASSOCIATE_PROTOCOL: Final = 'stage2-associate-v3'

#: 观察句柄（associate 输入/输出）：compiler 投影的闭集成员标识。
#: 形状只锁字符集与长度；成员资格（只选已给句柄）归 compile 对闭集核对。
#: 章观察内部的命题/loop 本地句柄沿用 v13 转移原子的 `ref` 字段（形状与其
#: 逐字一致，I-11），正式 ID 由 compiler 生成、本地句柄不进 canonical（I-21）。
OBSERVATION_HANDLE_PATTERN: Final = r'^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,63}$'

#: 世界约束的 kind 闭集（与 v13 `WorldConstraint` 同源；这里不复制模型是因为
#: 正式 ID 归 compiler，wire 不带编号——kind 面保持同一张表）。
WORLD_RULE_KINDS: Final = (
    'hard_rule', 'social_norm', 'institutional_fact', 'setting_constant')


def _slug_ok(slug: str) -> bool:
    import re
    return bool(re.fullmatch(r'[a-z0-9_]+', slug))


# ───────────────────────── book 观察（str06.stage2.book） ─────────────────────────

class SurfaceMention(StrictModel):
    """称谓及证据：模型只给 surface 字面与证据句段（闭集选择）。

    **没有** `from_chapter`／`until_chapter`：窗口坐标由 compiler 按锚点派生
    （I-15；派生规则沿用 `R-2.10`，落在 `novel/stages/stage2/resolver.py`）。
    """

    surface: str = Field(min_length=1)
    source_refs: tuple[str, ...] = Field(min_length=1)


class CharacterCandidate(StrictModel):
    """人物候选：可读 slug（dict 键）＋最稳定称呼＋称谓面。

    canonical 必须逐字等于某个已登记 surface 的闭合闸由 compile 承接
    （`R-2.8` wire 闸延续，不静默改写）。
    """

    canonical: str = Field(min_length=1)
    surfaces: tuple[SurfaceMention, ...] = Field(min_length=1)


class InitialFact(StrictModel):
    """初态事实（第 1 章第一拍之前已经成立的地基）。

    **没有** `state_id`：compiler 生成 `s0_*`（I-21）。target/knowledge 形状
    与 v13 `InitialStateAtom` 同源（`StateTarget`／`KnowledgeSpec` 复用）。
    """

    target: StateTarget
    key: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_.-]*$')
    value: Any
    persistence: str = Field(pattern=r'^(stable|mutable)$')
    knowledge: KnowledgeSpec | None = None
    source_refs: tuple[str, ...] = Field(min_length=1)
    #: R10a a1：逐字证据（必填 ≥1，段属自身 source_refs；wire 与 canonical 同形）。
    basis: tuple[EvidenceQuote, ...] = Field(min_length=1)


class WorldRule(StrictModel):
    """世界约束（写手若不知道就可能写错世界观的规则）。

    **没有** `constraint_id`：compiler 生成 `wc_*`（I-21）。
    """

    kind: str = Field(pattern=r'^(' + '|'.join(WORLD_RULE_KINDS) + r')$')
    statement: str = Field(min_length=1)
    source_refs: tuple[str, ...] = Field(min_length=1)
    #: R10a a1：逐字证据（必填 ≥1，段属自身 source_refs）。
    basis: tuple[EvidenceQuote, ...] = Field(min_length=1)


from contracts.schema.expression import ExpressionObservation


class BookObservation(StrictModel):
    """`str06.stage2.book` 的 canonical 观察：人物候选（可读 slug、称谓及证据）、
    初态、世界约束。作用域＝书；输入是全书原文（含 e00 引子句段）与证据句段
    清单。basis 的段号由 compile 按引文定位后写入（RC-27）。"""

    protocol_version: Literal['stage2-book-v4']
    expression: ExpressionObservation
    characters: dict[str, CharacterCandidate] = Field(min_length=1)
    initial_state: tuple[InitialFact, ...] = Field(default=(), min_length=0)
    world_constraints: tuple[WorldRule, ...] = Field(default=(), min_length=0)

    @model_validator(mode='after')
    def _slugs_are_readable_handles(self) -> 'BookObservation':
        bad = [slug for slug in self.characters if not _slug_ok(slug)]
        if bad:
            raise ValueError(f'人物 slug 非法（小写蛇形）：{bad}')
        return self


#: 证据指针（wire 面的条目）：模型只给证据段 id（闭集成员，2026-10-07 裁决：
#: 模型只选段、逐字文本由代码切）；成员资格归 compile 对闭集核对。wire 词汇用
#: `evidence_refs` 与 canonical 的 basis 分层；canonical 的 `EvidenceQuote` 不变。
SegmentRef = Annotated[str, Field(pattern=r'^e\d+_\d+$')]


class InitialFactWire(StrictModel):
    """初态事实的 wire 面：与 `InitialFact` 同形（除 knowledge，1a 删），
    evidence_refs 只给段 id。"""

    target: StateTarget
    key: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_.-]*$')
    value: Any
    persistence: str = Field(pattern=r'^(stable|mutable)$')
    source_refs: tuple[str, ...] = Field(min_length=1)
    evidence_refs: tuple[SegmentRef, ...] = Field(min_length=1)


class WorldRuleWire(StrictModel):
    """世界约束的 wire 面：与 `WorldRule` 同形，evidence_refs 只给段 id。"""

    kind: str = Field(pattern=r'^(' + '|'.join(WORLD_RULE_KINDS) + r')$')
    statement: str = Field(min_length=1)
    source_refs: tuple[str, ...] = Field(min_length=1)
    evidence_refs: tuple[SegmentRef, ...] = Field(min_length=1)


class BookObservationWire(StrictModel):
    """`str06.stage2.book` 的 LLM 输出面（传输面，不落盘）：compile 按
    evidence_refs 段 id 派生逐字引文（段全文）后生成 `BookObservation`。
    人物、称谓证据与表达观察的形状与 canonical 相同（称谓的 source_refs 是
    语义选择——选哪一处出现作锚点）。"""

    protocol_version: Literal['stage2-book-v8']
    expression: ExpressionObservation
    characters: dict[str, CharacterCandidate] = Field(min_length=1)
    initial_state: tuple[InitialFactWire, ...] = Field(default=(), min_length=0)
    world_constraints: tuple[WorldRuleWire, ...] = Field(default=(), min_length=0)


# ───────────────────────── chapters 观察（str06.stage2.chapters） ─────────────────────────

class ObsBeat(StrictModel):
    """一拍＝一次状态转移（v13 拍模型的观察面）。

    **没有** `beat_id`：事件序即列表序，拍号由 compiler 按序派发（I-21）。
    三类转移、场景边界与时间模式的语义与 v13 `Beat` 一致；转移条目直接复用
    `Effect`／`Cognition`／`LoopAction`（I-11），命题与 loop 用章内本地句柄。
    """

    event: str = Field(min_length=1)
    participants: tuple[str, ...] = Field(default=(), min_length=0)
    source_refs: tuple[str, ...] = Field(min_length=1)
    scene_boundary: str = Field(pattern=r'^(new|continue)$')
    temporal_mode: str = Field(pattern=r'^(single|iterative)$')
    effects: tuple[Effect, ...] = Field(default=(), min_length=0)
    cognition: tuple[Cognition, ...] = Field(default=(), min_length=0)
    loops: tuple[LoopAction, ...] = Field(default=(), min_length=0)


class ChapterObservation(StrictModel):
    """`str06.stage2.chapters` 的输出（逐章并行，作用域＝章）。

    只含章内本地句柄（D3）：没有章序（compile 按请求作用域绑定）、没有因果边、
    没有跨章命题/loop 引用（跨章悬念 close 以本地句柄落账，配对归 associate，
    配不上即未决——不默认合并）。participants／holder 闭集与证据闭合归 compile。
    """

    protocol_version: Literal['stage2-chapters-v4']
    beats: tuple[ObsBeat, ...] = Field(min_length=1)


# ──────────── chapters 瘦 wire（CS-2 第一步；expand 后即上形，不落盘） ────────────

class SlimEffect(StrictModel):
    """事实转移的瘦面：`at` 地址串＋例外字段。

    机械默认（expand 派生，模型只写例外）：op＝set、modality＝actual、
    scope＝登记键按表／表外一律 book（v8 起不进 wire；R-2.14 派生）、
    disclosure 恒 on_page、effect 不带命题锚（v8 删 `hidden`／`prop`——
    5 观察集 404 效应实证 concealed 0、effect 命题 0，死字段不再让模型抄）。
    `val` 只在 op 需要值时给（set／add／remove；unset／unknown 不带）。
    evidence_refs 必填（R10a a1 语义不变）：wire 只给段 id，引文＝段
    全文由 compiler 派生（2026-10-07 裁决）。**不得用 state 表达场景位置**：
    `scene_location` 是 `SlimBeat` 的拍级字段（compiler 合成登记键效应）。at
    只锁字符集，结构（单个 +、两端非空、不自环）由 expand 逐条校验——可条目
    级返修，不退整份。
    """

    at: str = Field(pattern=r'^(world|[a-z0-9_+]+)$')
    key: str = Field(min_length=1)
    val: Any = None
    op: str | None = Field(default=None, pattern=r'^(set|unset|add|remove|unknown)$')
    modality: str | None = Field(default=None,
                                 pattern=r'^(actual|planned|expected|conditional)$')
    evidence_refs: tuple[SegmentRef, ...] = Field(min_length=1)


class SlimCognition(StrictModel):
    """认知转移的瘦面：`what` 命题文本直接承载语义，句柄由代码按文本归并。

    同一命题再次出现时逐字重写同一句；norm() 后不同即另一条命题（不模糊
    合并——与 CS-1 的 D1 配对判定同一把尺）。evidence_refs 只给段 id（引文＝
    段全文由 compiler 派生，2026-10-07 裁决）。
    """

    who: str = Field(min_length=1)
    how: str = Field(pattern=r'^(learns|suspects|confirms|hears_claim|believes|asserts)$')
    what: str = Field(min_length=1)
    evidence_refs: tuple[SegmentRef, ...] = Field(min_length=1)


class SlimBeat(StrictModel):
    """一拍的瘦面（v8）：`start` 只写**单段 id**，拍覆盖区间由代码派生——
    `[start, 下一拍 start−1]`，末拍到章末（今日两本 383 拍实证同章连续，
    区间纪律从提示词纪律变成构造保证）。start 按段号数值严格递增（≥100 段
    的章不按字典序）；首拍必须是本章第一段（第 1 章允许跳过前导 e00 引子段
    ——被跳过的引子段视为弃权）；start 之间的每一段（含「......」分隔段）
    按构造归属其前最近一个 start 所在的拍，不再有未覆盖段或两拍共引一段。

    `new_scene`／`iterative` 只写例外（缺省 continue／single）；`scene_location`
    是拍级场景位置元数据（换景或首次交代位置时给，compiler 合成登记键
    `scene_location` 的 world 效应，证据面＝本拍派生区间全体——覆盖性证据
    不猜具体句）；loops 保持原形状（悬念句柄仍由模型给——跨章开合配对靠
    它，D8）。
    """

    ev: str = Field(min_length=1)
    on_stage: tuple[str, ...] = Field(default=(), min_length=0)
    start: SegmentRef
    scene_location: str | None = Field(default=None, min_length=1)
    new_scene: bool = False
    iterative: bool = False
    state: tuple[SlimEffect, ...] = Field(default=(), min_length=0)
    know: tuple[SlimCognition, ...] = Field(default=(), min_length=0)
    loops: tuple[LoopAction, ...] = Field(default=(), min_length=0)


class ChapterObservationWire(StrictModel):
    """`str06.stage2.chapters` 的瘦输出面（传输面，不落盘；expand 成
    `ChapterObservation` 后进既有管道与盘面）。"""

    protocol_version: Literal['stage2-chapters-v8']
    beats: tuple[SlimBeat, ...] = Field(min_length=1)


# ───────────────────────── associate 观察（str06.stage2.associate） ─────────────────────────

class PropositionPairing(StrictModel):
    """同一命题：把不同章观察里的同义命题句柄配成一对（D8）。

    措辞不同的同义表述也算同一，语义判断归 associate；compiler 不以文本
    全等推翻配对，按阅读序采用类内最早建立的文本作为正式代表。

    关联只建立同一性与引用：合并后由 compiler 授予唯一正式 ID，不移动事实
    成立、角色知情或读者揭示时点。配对缺项保持未决（compile 拒绝发布并按
    item 报告），不默认合并也不默认不同。
    """

    left: str = Field(pattern=OBSERVATION_HANDLE_PATTERN)
    right: str = Field(pattern=OBSERVATION_HANDLE_PATTERN)


class StateDimensionPairing(StrictModel):
    """同一状态维度（v3，RC-65）：两个**维度句柄**（``dim{nn}``，compiler 按
    （作用对象，键）地址聚合）的近义键配对（如 trust／trust_level）。

    同键写入（含书级初态与章内效应同键）已由编译器合并为同一维度句柄，不需要
    也不可再配；作用对象不同的维度不是同一维度。compiler 据此把规范键按地址
    传播到两类全部写入。"""

    left: str = Field(pattern=OBSERVATION_HANDLE_PATTERN)
    right: str = Field(pattern=OBSERVATION_HANDLE_PATTERN)


class LoopPairing(StrictModel):
    """悬念开合配对：某章 open 与另一章 close 的跨章配对（D8）。

    章内自开自闭不需要配对；跨章 close 在章观察里以本地句柄落账，由本配对
    连接到 open 句柄。配不上即未决。
    """

    open: str = Field(pattern=OBSERVATION_HANDLE_PATTERN)
    close: str = Field(pattern=OBSERVATION_HANDLE_PATTERN)


class CrossChapterCause(StrictModel):
    """跨章因果：from 拍 → to 拍（跨章拍句柄；章内因果不在此——拍序与转移
    结构已经承载章内连续性，v13 探针实证模型自报因果边系统性不可靠）。"""

    from_beat: str = Field(pattern=OBSERVATION_HANDLE_PATTERN)
    to_beat: str = Field(pattern=OBSERVATION_HANDLE_PATTERN)
    kind: str = Field(pattern=r'^(causes|motivates|enables|setup_payoff)$')

    @model_validator(mode='after')
    def _not_self(self) -> 'CrossChapterCause':
        if self.from_beat == self.to_beat:
            raise ValueError('因果边自指')
        return self


class SceneContinuation(StrictModel):
    """跨章续场：上一章的开放场景延续到下一章（场景句柄配对）。"""

    left: str = Field(pattern=OBSERVATION_HANDLE_PATTERN)
    right: str = Field(pattern=OBSERVATION_HANDLE_PATTERN)


class AssociateObservation(StrictModel):
    """`str06.stage2.associate` 的输出：只选已给句柄（闭集成员资格归 compile
    对投影句柄核对），只建立同一性与引用（D8）。句柄为 compiler 位置编码
    （R1 v2；见 `novel/stages/stage2/associate.py` 的登记表）。"""

    protocol_version: Literal['stage2-associate-v3']
    same_proposition: tuple[PropositionPairing, ...] = Field(default=(), min_length=0)
    same_state_dimension: tuple[StateDimensionPairing, ...] = Field(default=(), min_length=0)
    loop_pairing: tuple[LoopPairing, ...] = Field(default=(), min_length=0)
    cross_chapter_cause: tuple[CrossChapterCause, ...] = Field(default=(), min_length=0)
    scene_continuation: tuple[SceneContinuation, ...] = Field(default=(), min_length=0)


# ───────────────────────── names 观察（str06.stage2.names） ─────────────────────────

class NamesObservation(StrictModel):
    """`str06.stage2.names` 的落盘观察：源名称结构（家族、名、化名、称谓构词）。

    名称结构形状与 `NameStructure` 同源（I-11）；compile 是 Characters 的
    唯一 writer。v2 增加有证据的名称同一性判断，未决时禁止下游归一。
    """

    protocol_version: Literal['stage2-names-v3']
    structure: NameStructure
