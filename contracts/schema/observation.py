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
`STAGE2_ASSOCIATE_PROTOCOL`（现役 stage2-associate-v5；Phase 2 起 v4 改为条目级
窄 resolver，Phase 3 起 v5 删维度候选面）：输入＝代码给出的未决候选＋最小文本
（S2-9 正常路径落地；无候选＝零调用），输出逐候选 verdict；跨章因果是独立窄调用 wire
（`STAGE2_CROSS_CAUSE_WIRE_PROTOCOL`，D-2 A：候选＝loop 配对＋facts 流拍对，
模型只选 kind 或否决）。三类观察的请求与字面量逐字不变（首版 PLAN.md R1，
Git 2fc03832；A1.5 出自被它吸收的 A0–A6 提案）。

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

import re
from typing import Annotated, Any, Final, Literal

from pydantic import Field, field_validator, model_serializer, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.source import (
    ACCESS_PATTERN, CHANGE_KEY_PATTERN, AliasEntry, CharacterKey, Cognition, Effect,
    EvidenceQuote, EvidenceSupport, FamilyEntry, IdentityBinding, LoopAction,
    NameStructure, Polarity, StateTarget,
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
#: CS-S 1c（2026-10-07）：names canonical 升 v4——resolutions 增 `not_name`
#: （变体候选的完整性证明）、构词 surface_forms 改由 compiler 派生（wire 不
#: 再携带）；同名判定走新窄调用 wire `stage2-name-variants-v1`。
STAGE2_NAMES_PROTOCOL: Final = 'stage2-names-v5'
#: CS-S Phase 2（2026-10-07）：book canonical 升 v6——增 `facts`（书级命题
#: 词表：statement＋basis＋known_at_start，F 编号 f{nn} 由 compile 按列表序
#: 派发）；初始知情由 facts 的 known_at_start 承载（1b 删的 InitialFact.
#: knowledge 语义在此补回，持有者闭集＝人物 slug——D-5 删 public、reader
#: 知情 Phase 3 由 compile 派生）。旧 v5 观察按版本字面量关闭式拒读。
#: CS-S Phase 3（2026-10-08）：book canonical 升 v7——initial_state 键入
#: `CHANGE_KEYS` 闭集（schema 层兜底表外键）。
#: 2026-10-08 口径统一（用户裁决，形状不变不升版）：known_at_start 的
#: 「故事开始」精确为**第 1 章第一拍开始的时刻**（与 `InitialFact` 同一判定
#: 时刻）——引子（e00）内已完成的告知算开场已知（过程归第 1 章开场拍、结果
#: 归词表起步）；后置高光前置／全书概括类引子披露不算开场已知，信息由正文
#: 对应章观察承载。判据变化经请求身份（prompt 哈希）隔离旧采用。
STAGE2_BOOK_PROTOCOL: Final = 'stage2-book-v8'
#: CS-S Phase 2（2026-10-07）：chapters canonical 升 v6——cognition 的命题
#: 可引用书级 facts（proposition.ref=f{nn}、statement=None——文本由 compile
#: 从词表回填，跨章同一性按引用构造成立，不再靠转写配对）。
#: CS-S Phase 3（2026-10-08）：chapters canonical 升 v7——ObsBeat 增 `access`
#: （获知通道闭集 witnessed/narrated）；know 的 holder 禁 reader（读者知情由
#: compile 派生，不进观察面）。
STAGE2_CHAPTERS_PROTOCOL: Final = 'stage2-chapters-v8'

#: 书级观察的 **wire 面**（2026-10-07 裁决：模型只选段、代码切原文；同日二次
#: 收口：wire/canonical 词汇分层——wire 用 `evidence_refs`，canonical 用 basis）：
#: 初态／世界约束的 evidence_refs 只给**段 id**（闭集成员），逐字引文由 compiler
#: 按段全文派生（I-20/I-21：转写文本不进模型输出面）；compile 把它派生成
#: `STAGE2_BOOK_PROTOCOL` 的 canonical。
#: CS-S 1a（2026-10-07）升 v8：`InitialFactWire.knowledge` 删——初始知情不再由
#: 模型声明（使用率 0–8/13，语义含混：引子已披露的算不算 reader_knows 指不清），
#: Phase 2 以 facts 的 known_at_start 补回该语义；canonical 的 `InitialFact.knowledge`
#: 本批保留、1b 删。
#: CS-S Phase 2（2026-10-07）升 v9：增 `facts`（书级命题词表）——statement＋
#: evidence_refs（段 id）＋known_at_start（人物 slug）；编号不进 wire（F 编号
#: 代码派发，I-21）。
#: CS-S Phase 3（2026-10-08）升 v10：initial_state 键入 `CHANGE_KEYS` 闭集
#: （提示词渲染清单，表外键即拒）。
STAGE2_BOOK_WIRE_PROTOCOL: Final = 'stage2-book-v11'

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
#: slim 模块，删除条件＝下游消费迁移完成、canonical 自身瘦身升版时）。
#: CS-S Phase 2（2026-10-07）升 v9：know 增 `fact` 引用——命题已在书级词表
#: 登记时引用编号（f{nn}），不重写文本；`fact` 与 `what` 恰写其一（形状保证，
#: 引用不携带文本＝跨章同一性按引用构造成立）。
#: CS-S Phase 3（2026-10-08）升 v10：字段改名 `ev`→`event`、`state`→`change`
#: （与语义对齐：state 条目是变化，ev 是 event 的缩写遗风）；增 `access`
#: （获知通道：witnessed 默认不写、narrated 变体直陈时显式标注）；change 键入
#: `CHANGE_KEYS` 闭集；know 的 `who` 禁 reader（读者知情由 compile 派生）。
STAGE2_CHAPTERS_WIRE_PROTOCOL: Final = 'stage2-chapters-v11'

#: names 的 **结构 wire 面**（CS-S 1c，2026-10-07 升 v6）：模型只登记姓氏家族
#: （含家族级 forms）、名、化名与**歧义例外** `form_overrides`——每个 surface
#: 的构词模板由 compiler 按 family／given／aliases 字面对源部件做**占位符最大
#: 替换**机械派生（今日两本 51/51 逐条一致；无身份字面＝纯称谓 None，同位
#: 同长多解＝须 form_overrides，S2-10/I-20/I-21）。同一性判定不再混在结构答案
#: 里：候选由代码给出（无候选＝零调用），判定走独立窄调用 wire
#: `stage2-name-variants-v1`。旧 v5 结构回答（带 surface_forms／resolutions）
#: 按版本字面量拒收，不做推断采用。
STAGE2_NAMES_WIRE_PROTOCOL: Final = 'stage2-names-v7'

#: names 的**变体窄调用 wire 面**（CS-S 1c 新增）：输入＝代码生成的候选清单
#: （锚＝≥3 字含姓名部件的登记 surface、同首字编辑距离 1、未登记、非任一登记
#: surface 子串），输出逐条 verdict——same（带 char_id＋surface）／unresolved／
#: not_name（不是人名，只作完整性证明）；每候选恰答一次，漏答／多答＝机械
#: 缺陷按条目窄修。evidence_refs 只给段 id（闭集＝该候选的出现段），引文由
#: compiler 按段全文派生。
STAGE2_NAME_VARIANTS_WIRE_PROTOCOL: Final = 'stage2-name-variants-v1'

#: 关联协议（associate 独立）的版本字面量（R1，2026-09-28 升 v2；CS-H H4，
#: 2026-10-06 升 v3，RC-65；CS-S Phase 2，2026-10-07 升 v4；CS-S Phase 3，
#: 2026-10-08 升 v5）：v2＝typed 作用对象显式序列化＋状态句柄位置编码；
#: v3＝`same_state_dimension` 改配**维度句柄**（compiler 按（作用对象，键）地址
#: 聚合，``dim{nn}``）；v4＝条目级窄 resolver（S2-9 落地）——输入不再是整书
#: 投影，而是**代码给出的未决候选＋最小文本**，每候选恰答一次 verdict；无候选
#: ＝零调用。v5＝`same_state_dimension` **消失**——change 键入 `CHANGE_KEYS`
#: 闭集后「键不同但同维」构造上不可能（键即 canonical），维度句柄与整类合并
#: 面一并删除；剩余四类候选（命题 same／悬念 pair／跨章因果 kind／续场
#: continue）不变。旧 v4 回答按版本字面量关闭式拒读。
STAGE2_ASSOCIATE_PROTOCOL: Final = 'stage2-associate-v5'

#: 跨章因果窄调用 wire（CS-S Phase 2，D-2 A）：独立于身份判定的第二窄任务
#: （同节点 owner `cross_cause`，不增节点）。候选＝代码给出（悬念开合配对的
#: 开/收拍对＋书级 facts 流经的拍对），模型只选 kind（causes／motivates／
#: enables／setup_payoff）或否决（none）；不新增候选外的边。
STAGE2_CROSS_CAUSE_WIRE_PROTOCOL: Final = 'stage2-cross-cause-v1'

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
    identity_bindings: tuple[IdentityBinding, ...]


class InitialFact(StrictModel):
    """初态事实（第 1 章第一拍之前已经成立的地基）。

    判定时刻＝第 1 章第一拍开始（与 `Fact.known_at_start` 的「故事开始」
    同一时刻，2026-10-08 统一口径）：引子（e00）内先行成立的状态照收；
    叙事时间在其后的预告／全书概括不收。

    **没有** `state_id`：compiler 生成 `s0_*`（I-21）。target 形状与 v13
    `InitialStateAtom` 同源（`StateTarget` 复用）。CS-S 1b：`knowledge` 删
    （Phase 2 由 facts `known_at_start` 承载初始知情）。CS-S Phase 3：键入
    `CHANGE_KEYS` 闭集（表外键 schema 层即拒）。
    """

    target: StateTarget
    key: str = Field(pattern=CHANGE_KEY_PATTERN)
    value: Any
    persistence: str = Field(pattern=r'^(stable|mutable)$')
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


#: 书级命题编号（F 编号，CS-S Phase 2）：compile 按列表序派发 ``f{nn}``，
#: 不进 wire（I-21）。章观察的 cognition 以该编号引用命题（跨章同一性按
#: 引用构造成立）；与章内代码派发句柄 ``k{nn}``（slim 模块）分属两个命名
#: 空间，冲突在观察闸按闭集拒绝。
FACT_ID_PATTERN: Final = r'^f\d{2,}$'
_FACT_ID_RE: Final = re.compile(FACT_ID_PATTERN)


def is_fact_ref(ref: object) -> bool:
    """命题引用是否书级词表编号（``f{nn}``）——全仓唯一判定（章验收与 slim 逆映射
    共用）；章内代码派发的句柄是 ``k{nn}``，两个命名空间不冲突。"""
    return isinstance(ref, str) and _FACT_ID_RE.fullmatch(ref) is not None


class InitialKnowledge(StrictModel):
    """开场已确认的正／负命题知识；空数组表示开场尚无确认。"""

    holder: CharacterKey
    polarity: Polarity


class FactEvidence(StrictModel):
    """源文对命题的一次支持、说法或推测；不声明永久世界真值。"""

    kind: EvidenceSupport
    polarity: Polarity
    holder: CharacterKey | None
    basis: tuple[EvidenceQuote, ...] = Field(min_length=1)


class Fact(StrictModel):
    """共享原子命题词表，不是永久世界真相表。

    一项只表达可独立确认或否定的主张；evidence 保留原文的确认、说法、推测
    以及极性和相关人物。原子性与语义同一性由语义单元判断，compiler 不拆句。
    fact_id 由代码按序派发。known_at_start 仅记录第 1 章第一拍开始时已确认的
    正／负知识；引子中的后置预告不视为开场已知，reader 认知仍由 compiler 派生。
    """

    fact_id: str = Field(pattern=FACT_ID_PATTERN)
    statement: str = Field(min_length=1)
    evidence: tuple[FactEvidence, ...] = Field(min_length=1)
    known_at_start: tuple[InitialKnowledge, ...]


from contracts.schema.expression import ExpressionObservation


class BookObservation(StrictModel):
    """`str06.stage2.book` 的 canonical 观察：人物候选（可读 slug、称谓及证据）、
    初态、世界约束、书级命题词表。作用域＝书；输入是全书原文（含 e00 引子
    句段）与证据句段清单。basis 的段号由 compile 按引文定位后写入（RC-27）。
    facts 的 F 编号由 compile 按列表序派发（Phase 2）——词表不是真源
    （S2-8），下游经 cognition 引用与 fold 知情时间线读数消费。"""

    protocol_version: Literal['stage2-book-v8']
    expression: ExpressionObservation
    characters: dict[CharacterKey, CharacterCandidate] = Field(min_length=1)
    initial_state: tuple[InitialFact, ...] = Field(default=(), min_length=0)
    world_constraints: tuple[WorldRule, ...] = Field(default=(), min_length=0)
    facts: tuple[Fact, ...] = Field(default=(), min_length=0)

    @model_validator(mode='after')
    def _slugs_are_readable_handles(self) -> 'BookObservation':
        bad = [slug for slug in self.characters if not _slug_ok(slug)]
        if bad:
            raise ValueError(f'人物 slug 非法（小写蛇形）：{bad}')
        return self

    @model_validator(mode='after')
    def _facts_are_a_dense_sequence(self) -> 'BookObservation':
        """F 编号唯一且按列表序连续（f01、f02…）：编号由 compile 的 expand 按序
        派发（I-21），本不变量把「唯一、连续」从派发实现的偶然变成模型保证——
        直通面与手改的 book.json 若重复或跳号，下游以 fact_id 为键的索引会静默
        覆盖前一条命题。"""
        expected = [f'f{index:02d}' for index in range(1, len(self.facts) + 1)]
        actual = [fact.fact_id for fact in self.facts]
        if actual != expected:
            raise ValueError(
                f'facts 的 fact_id 须唯一且按列表序连续（期望 {expected}，实际 {actual}）')
        return self


#: 证据指针（wire 面的条目）：模型只给证据段 id（闭集成员，2026-10-07 裁决：
#: 模型只选段、逐字文本由代码切）；成员资格归 compile 对闭集核对。wire 词汇用
#: `evidence_refs` 与 canonical 的 basis 分层；canonical 的 `EvidenceQuote` 不变。
SegmentRef = Annotated[str, Field(pattern=r'^e\d+_\d+$')]


class InitialFactWire(StrictModel):
    """初态事实的 wire 面：与 `InitialFact` 同形（除 knowledge，1a 删），
    evidence_refs 只给段 id。"""

    target: StateTarget
    key: str = Field(pattern=CHANGE_KEY_PATTERN)
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


class FactEvidenceWire(StrictModel):
    """命题证据的 wire：只选择证据段，逐字引文由代码展开。"""

    kind: EvidenceSupport
    polarity: Polarity
    holder: CharacterKey | None
    evidence_refs: tuple[SegmentRef, ...] = Field(min_length=1)


class FactWire(StrictModel):
    """命题 wire 不带编号，逐项证据只选段；开场知识显式给出人物与极性。"""

    statement: str = Field(min_length=1)
    evidence: tuple[FactEvidenceWire, ...] = Field(min_length=1)
    known_at_start: tuple[InitialKnowledge, ...]


class BookObservationWire(StrictModel):
    """`str06.stage2.book` 的 LLM 输出面（传输面，不落盘）：compile 按
    evidence_refs 段 id 派生逐字引文（段全文）后生成 `BookObservation`。
    人物、称谓证据与表达观察的形状与 canonical 相同（称谓的 source_refs 是
    语义选择——选哪一处出现作锚点）。"""

    protocol_version: Literal['stage2-book-v11']
    expression: ExpressionObservation
    characters: dict[CharacterKey, CharacterCandidate] = Field(min_length=1)
    initial_state: tuple[InitialFactWire, ...] = Field(default=(), min_length=0)
    world_constraints: tuple[WorldRuleWire, ...] = Field(default=(), min_length=0)
    facts: tuple[FactWire, ...] = Field(default=(), min_length=0)


# ───────────────────────── chapters 观察（str06.stage2.chapters） ─────────────────────────

class ObsBeat(StrictModel):
    """一拍＝一次状态转移（v13 拍模型的观察面）。

    **没有** `beat_id`：事件序即列表序，拍号由 compiler 按序派发（I-21）。
    三类转移、场景边界与时间模式的语义与 v13 `Beat` 一致；转移条目直接复用
    `Effect`／`Cognition`／`LoopAction`（I-11），命题与 loop 用章内本地句柄。
    CS-S Phase 3：增 `access`（获知通道——reader 派生的输入；wire 缺省
    witnessed 由 expand 补默认）。
    """

    event: str = Field(min_length=1)
    participants: tuple[str, ...] = Field(default=(), min_length=0)
    source_refs: tuple[str, ...] = Field(min_length=1)
    scene_boundary: str = Field(pattern=r'^(new|continue)$')
    temporal_mode: str = Field(pattern=r'^(single|iterative)$')
    access: str = Field(default='witnessed', pattern=ACCESS_PATTERN)
    effects: tuple[Effect, ...] = Field(default=(), min_length=0)
    cognition: tuple[Cognition, ...] = Field(default=(), min_length=0)
    loops: tuple[LoopAction, ...] = Field(default=(), min_length=0)


class ChapterObservation(StrictModel):
    """`str06.stage2.chapters` 的输出（逐章并行，作用域＝章）。

    只含章内本地句柄（D3）：没有章序（compile 按请求作用域绑定）、没有因果边、
    没有跨章命题/loop 引用（跨章悬念 close 以本地句柄落账，配对归 associate，
    配不上即未决——不默认合并）。participants／holder 闭集与证据闭合归 compile。
    CS-S Phase 2：cognition 的 proposition.ref 可以是书级 facts 编号
    （``f{nn}``，此时 statement=None——文本由 compile 从词表回填）；词表
    成员资格与「引用不带文本」在章观察验收，fact 引用按构造建立跨章同一性。
    CS-S Phase 3：holder 禁 reader（观察面无读者知情——reader 由 compile
    按拍级 access 派生注入蓝图）。
    """

    protocol_version: Literal['stage2-chapters-v8']
    beats: tuple[ObsBeat, ...] = Field(min_length=1)


# ──────────── chapters 瘦 wire（CS-2 第一步；expand 后即上形，不落盘） ────────────

class SlimEffect(StrictModel):
    """事实转移的瘦面（v10 改名 `state`→`change` 后的条目形状）：`at` 地址串
    ＋例外字段。

    机械默认（expand 派生，模型只写例外）：op＝set、modality＝actual、
    disclosure 恒 on_page、effect 不带命题锚（v8 删 `hidden`／`prop`——
    5 观察集 404 效应实证 concealed 0、effect 命题 0，死字段不再让模型抄）。
    `val` 只在 op 需要值时给（set／add／remove；unset／unknown 不带）。
    evidence_refs 必填（R10a a1 语义不变）：wire 只给段 id，引文＝段
    全文由 compiler 派生（2026-10-07 裁决）。**不得用 change 表达场景位置**：
    `scene_location` 是 `SlimBeat` 的拍级字段（compiler 合成登记键效应）。
    CS-S Phase 3：`key` 入 `CHANGE_KEYS` 闭集（表外键即拒；语义细节由 val
    承载，不为细分自造新键）。at 只锁字符集，结构（单个 +、两端非空、不自环）
    由 expand 逐条校验——可条目级返修，不退整份。
    """

    at: str = Field(pattern=r'^(world|[a-z0-9_+]+)$')
    key: str = Field(pattern=CHANGE_KEY_PATTERN)
    val: Any = None
    op: str | None = Field(default=None, pattern=r'^(set|unset|add|remove|unknown)$')
    modality: str | None = Field(default=None,
                                 pattern=r'^(actual|planned|expected|conditional)$')
    reader_visible: bool
    evidence_refs: tuple[SegmentRef, ...] = Field(min_length=1)


class SlimCognition(StrictModel):
    """认知转移的瘦面（v9）：命题优先引用书级词表——`fact`＝词表编号
    （``f{nn}``，恰一书级 facts 成员，闭集核对归章观察验收）；词表外的命题
    才写 `what` 文本，句柄由代码按文本归并。`fact` 与 `what` 恰写其一
    （形状保证：引用不携带文本——转写漂移在构造上消失，跨章同一性按引用
    成立）。`what` 同一命题再次出现时逐字重写同一句；norm() 后不同即另一条
    命题（不模糊合并——与 CS-1 的 D1 配对判定同一把尺）。evidence_refs 只给
    段 id（引文＝段全文由 compiler 派生，2026-10-07 裁决）。
    """

    who: str = Field(min_length=1)
    op: Literal['set', 'unset']
    how: str | None = Field(pattern=r'^(learns|suspects|confirms|hears_claim|believes|asserts)$')
    polarity: Polarity | None
    support: EvidenceSupport
    reader_visible: bool
    what: str | None = Field(default=None, min_length=1)
    fact: str | None = Field(default=None, pattern=FACT_ID_PATTERN)
    evidence_refs: tuple[SegmentRef, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def _exactly_one_proposition(self) -> 'SlimCognition':
        if (self.what is None) == (self.fact is None):
            raise ValueError(
                'know 条目须恰写其一：fact（书级词表编号）或 what（词表外命题文本）')
        return self

    @model_validator(mode='after')
    def _no_reader(self) -> 'SlimCognition':
        if self.who == 'reader':
            raise ValueError(
                'know.who 禁 reader——读者知情由 compile 按拍级 access 派生，'
                '不进观察面')
        return self

    @model_validator(mode='after')
    def _op_shape(self) -> 'SlimCognition':
        if self.op == 'unset':
            if self.how is not None or self.polarity is not None:
                raise ValueError('know unset 的 how／polarity 必须为 null')
        elif self.how is None or self.polarity is None:
            raise ValueError('know set 必须显式给出 how／polarity')
        if self.how in ('learns', 'confirms') and self.support != 'confirmed':
            raise ValueError('learns／confirms 只接受 confirmed 证据')
        return self


class SlimAnswerRef(StrictModel):
    """本拍答案选择器；数组保持 1-based，拍级位置用独立选择器指向合成 Effect。"""

    model_config = {'extra': 'forbid', 'json_schema_extra': {'allOf': [{
        'if': {'properties': {'kind': {'const': 'scene_location'}}, 'required': ['kind']},
        'then': {'properties': {'index': {'const': 1}}},
    }]}}

    kind: Literal['change', 'know', 'scene_location']
    index: int = Field(ge=1)

    @field_validator('index', mode='before')
    @classmethod
    def _json_integer_index(cls, value: Any) -> Any:
        # JSON Schema 的 integer 不接受布尔或数字字符串；Pydantic 默认会强转。
        # 数值 1.0 属 JSON Schema 的整数，交 int 字段按同一数学整数规则验证。
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError('答案 index 必须是 JSON 整数，不接受布尔或字符串')
        return value

    @model_validator(mode='after')
    def _location_index(self) -> 'SlimAnswerRef':
        if self.kind == 'scene_location' and self.index != 1:
            raise ValueError('scene_location 答案的 index 只能为 1')
        return self


class SlimLoopAction(StrictModel):
    """模型只选答案原子，不自由填写可能把猜测固化的收束文本。"""

    ref: str = Field(min_length=1)
    op: Literal['open', 'close']
    question: str | None
    answer_refs: tuple[SlimAnswerRef, ...]

    @model_validator(mode='after')
    def _op_shape(self) -> 'SlimLoopAction':
        if self.op == 'open':
            if not self.question or not self.question.strip():
                raise ValueError('open 必须带非空 question')
            if self.answer_refs:
                raise ValueError('open 不得带答案引用')
        else:
            if self.question is not None:
                raise ValueError('close 不得改写问题文本')
            if not self.answer_refs:
                raise ValueError('close 必须引用本拍答案原子')
            refs = [(item.kind, item.index) for item in self.answer_refs]
            if len(set(refs)) != len(refs):
                raise ValueError('close 答案原子引用重复')
        return self


class SlimBeat(StrictModel):
    """一拍的瘦面（v8 起 start 单段；v10 改名 `ev`→`event`、`state`→`change`
    ＋增 `access`）：`start` 只写**单段 id**，拍覆盖区间由代码派生——
    `[start, 下一拍 start−1]`，末拍到章末（今日两本 383 拍实证同章连续，
    区间纪律从提示词纪律变成构造保证）。start 按段号数值严格递增（≥100 段
    的章不按字典序）；首拍必须是本章第一段（第 1 章允许跳过前导 e00 引子段
    ——被跳过的引子段视为弃权）；start 之间的每一段（含「......」分隔段）
    按构造归属其前最近一个 start 所在的拍，不再有未覆盖段或两拍共引一段。

    `new_scene`／`iterative` 只写例外（缺省 continue／single）；`access` 只写
    变体直陈的例外（缺省 witnessed——narrated＝叙述者直陈：回溯、背景交代、
    概括、「叙述者点明」，不经角色展演；不据此推定读者确知）；
    `scene_location` 是拍级场景位置元数据（换景或首次交代位置时给，compiler
    合成登记键 `scene_location` 的 world 效应，证据面＝本拍派生区间全体——
    覆盖性证据不猜具体句）；`location_reader_visible` 必填，明确位置内容是否
    向读者展示，无位置时必须为 false。悬念收束引用本拍已确认的可见原子。
    """

    model_config = {'extra': 'forbid', 'json_schema_extra': {'allOf': [{
        'if': {
            'properties': {'loops': {'contains': {
                'properties': {'answer_refs': {'contains': {
                    'properties': {'kind': {'const': 'scene_location'}},
                    'required': ['kind'],
                }}},
                'required': ['answer_refs'],
            }}},
            'required': ['loops'],
        },
        'then': {'required': ['scene_location'],
                 'properties': {'scene_location': {'type': 'string', 'minLength': 1}}},
    }]}}

    event: str = Field(min_length=1)
    on_stage: tuple[str, ...] = Field(default=(), min_length=0)
    start: SegmentRef
    scene_location: str | None = Field(default=None, min_length=1)
    location_reader_visible: bool
    new_scene: bool = False
    iterative: bool = False
    access: str = Field(default='witnessed', pattern=ACCESS_PATTERN)
    change: tuple[SlimEffect, ...] = Field(default=(), min_length=0)
    know: tuple[SlimCognition, ...] = Field(default=(), min_length=0)
    loops: tuple[SlimLoopAction, ...] = Field(default=(), min_length=0)

    @model_validator(mode='after')
    def _location_visibility_requires_location(self) -> 'SlimBeat':
        if self.scene_location is None and self.location_reader_visible:
            raise ValueError('无 scene_location 时 location_reader_visible 必须为 false')
        if self.scene_location is None and any(
                answer.kind == 'scene_location' for loop in self.loops
                for answer in loop.answer_refs):
            raise ValueError('scene_location 答案必须引用本拍实际存在的拍级位置')
        return self


class ChapterObservationWire(StrictModel):
    """`str06.stage2.chapters` 的瘦输出面（传输面，不落盘；expand 成
    `ChapterObservation` 后进既有管道与盘面）。"""

    protocol_version: Literal['stage2-chapters-v11']
    beats: tuple[SlimBeat, ...] = Field(min_length=1)


# ───────────────────────── associate 观察（str06.stage2.associate） ─────────────────────────
#
# 【v5＝维度候选消失（CS-S Phase 3）】`same_state_dimension` 删除：change 键入
# `CHANGE_KEYS` 闭集后「键不同但同维」构造上不可能（键即 canonical），维度句柄
# （``dim{nn}``）与 canonical_key 整类合并面一并退役。v4 的条目级窄 resolver
# 语义不变：输入面＝**代码给出的未决候选＋最小文本**，模型逐候选恰答一次
# verdict，不重新理解整书、不发明候选外的配对（每类候选＝确定性相似度或
# 结构投影筛出，闭集核对归 compile；漏答／多答／重复答＝机械缺陷按条目窄修）。
# 候选对句柄可以跨命名空间（章命题句柄 ``ch{nn}.{ref}``×书级 facts 编号
# ``f{nn}``）——verdict=same 即声明该章命题就是词表命题，compiler 据此并入
# fact 等价类（fact 编号是正式 ID，早于任何章内句柄）。

class PropositionPairVerdict(StrictModel):
    """同一命题候选的判定：same＝同一命题（并入同一等价类，fact 侧为
    canonical）；distinct＝不同命题；unresolved＝拿不准（保持分离，不默认
    合并也不默认不同）。"""

    left: str = Field(pattern=OBSERVATION_HANDLE_PATTERN)
    right: str = Field(pattern=OBSERVATION_HANDLE_PATTERN)
    verdict: str = Field(pattern=r'^(same|distinct|unresolved)$')


class LoopPairVerdict(StrictModel):
    """悬念开合候选的判定：pair＝该 open 与该 close 是同一悬念；distinct＝
    不是（close 保持未配对——compile 按未决拒发，恢复走 owner patch）；
    unresolved＝拿不准。"""

    open: str = Field(pattern=OBSERVATION_HANDLE_PATTERN)
    close: str = Field(pattern=OBSERVATION_HANDLE_PATTERN)
    verdict: str = Field(pattern=r'^(pair|distinct|unresolved)$')


class SceneContinuationVerdict(StrictModel):
    """跨章续场候选（相邻章 seam）的判定：continue＝右场景是左场景的延续
    （compiler 并回同一场）；distinct＝确是新场；unresolved＝拿不准。"""

    left: str = Field(pattern=OBSERVATION_HANDLE_PATTERN)
    right: str = Field(pattern=OBSERVATION_HANDLE_PATTERN)
    verdict: str = Field(pattern=r'^(continue|distinct|unresolved)$')


class CauseVerdict(StrictModel):
    """跨章因果候选的判定（D-2 A）：模型只选 kind 或否决（none）——不新增
    候选外的边（候选＝loop 配对拍对＋facts 流拍对，代码给出）。"""

    from_beat: str = Field(pattern=OBSERVATION_HANDLE_PATTERN)
    to_beat: str = Field(pattern=OBSERVATION_HANDLE_PATTERN)
    kind: str = Field(pattern=r'^(causes|motivates|enables|setup_payoff|none)$')

    @model_validator(mode='after')
    def _not_self(self) -> 'CauseVerdict':
        if self.from_beat == self.to_beat:
            raise ValueError('因果边自指')
        return self


class AssociateObservation(StrictModel):
    """`str06.stage2.associate` 的输出（v5）：逐候选 verdict（含否决与
    unresolved——已购判定的完整性证明；compile 只执行 same／pair／continue
    与 kind≠none）。句柄闭集＝代码给出的候选集合（成员资格归 compile 核对），
    只建立同一性与引用（D8）。`same_state_dimension` 已随 change 键闭集
    删除（键即 canonical，无「键不同但同维」）。"""

    protocol_version: Literal['stage2-associate-v5']
    same_proposition: tuple[PropositionPairVerdict, ...] = Field(default=(), min_length=0)
    loop_pairing: tuple[LoopPairVerdict, ...] = Field(default=(), min_length=0)
    cross_chapter_cause: tuple[CauseVerdict, ...] = Field(default=(), min_length=0)
    scene_continuation: tuple[SceneContinuationVerdict, ...] = Field(default=(), min_length=0)


class CrossCauseWire(StrictModel):
    """跨章因果窄调用的 wire 面（D-2 A）：逐候选恰答一次（候选由代码给出，
    闭集核对归 compile，漏答／多答按条目窄修）。kind=none 是合法结论
    （否决该候选的因果关系）。"""

    protocol_version: Literal['stage2-cross-cause-v1']
    causes: tuple[CauseVerdict, ...] = Field(min_length=1)


# ───────────────────────── names 观察（str06.stage2.names） ─────────────────────────

class AdditionalNameSurfaceWire(StrictModel):
    """既有人物的补充源称谓；人物归属由语义声明，证据仅选段，不抄窗口。"""

    surface: str = Field(min_length=1)
    evidence_refs: tuple[SegmentRef, ...] = Field(min_length=1)


class PersonNameStructureWire(StrictModel):
    """结构答案里一个人物的 wire 面（CS-S 1c）：无 `surface_forms`（构词模板
    由 compiler 派生，I-20/I-21）、无 resolutions 字段（模型无处凭空造同一性
    判定——候选由代码给出，判定走窄调用）；`form_overrides` 只在派生歧义
    （同位同长多解）时写例外。"""

    family_id: str | None = None
    given: str | None = None
    aliases: tuple[AliasEntry, ...] = ()
    form_overrides: dict[str, str | None] = Field(default_factory=dict)
    additional_surfaces: tuple[AdditionalNameSurfaceWire, ...]


class NamesStructureWire(StrictModel):
    """`str06.stage2.names` 的结构 wire 面（v7）：部件、补充源称谓及歧义例外。
    人物闭集不变；新增称谓必须选源证据段，构词模板由 compiler 派生。"""

    protocol_version: Literal['stage2-names-v7']
    families: dict[str, FamilyEntry] = Field(min_length=1)
    people: dict[str, PersonNameStructureWire]


class NameVariantVerdict(StrictModel):
    """一个候选字面的窄调用判定（CS-S 1c）：same／unresolved／not_name。"""

    literal: str = Field(min_length=1)
    verdict: str = Field(pattern=r'^(same|unresolved|not_name)$')
    char_id: str | None = None
    surface: str | None = None
    evidence_refs: tuple[SegmentRef, ...] = Field(min_length=1)
    reason: str = Field(min_length=1)

    @model_validator(mode='after')
    def _verdict_shape(self) -> 'NameVariantVerdict':
        if self.verdict == 'same' and (not self.char_id or not self.surface):
            raise ValueError('same 必须指向人物与已登记称谓')
        if self.verdict != 'same' and (self.char_id is not None
                                       or self.surface is not None):
            raise ValueError('非 same 不得暗含已接受的名称映射')
        return self


class NameVariantsWire(StrictModel):
    """`name_variants` 窄调用的 wire 面：逐候选恰答一次（闭集核对归
    compile，漏答／多答按条目窄修）。"""

    protocol_version: Literal['stage2-name-variants-v1']
    variants: tuple[NameVariantVerdict, ...] = Field(min_length=1)


class NamesObservation(StrictModel):
    """`str06.stage2.names` 的落盘观察：源名称结构（家族、名、化名、称谓构词）
    ＋同一性判定。

    名称结构形状与 `NameStructure` 同源（I-11）；compile 是 Characters 的
    唯一 writer。v2 增加有证据的名称同一性判断，未决时禁止下游归一；v4
    （CS-S 1c）：构词模板由 compiler 派生进 structure、同一性判定来自变体
    窄调用（resolutions 增 not_name——完整性证明，不参与归一）。
    v5 保存 Book 既有人物的有证据补充称谓；源称谓只在这里观察，免费 compile
    并入 Characters。v4 集中只读兼容保留原身份，不宣称源称谓已完整观察。
    """

    protocol_version: Literal['stage2-names-v4', 'stage2-names-v5']
    structure: NameStructure
    additional_surfaces: dict[str, tuple[SurfaceMention, ...]]

    @model_validator(mode='before')
    @classmethod
    def _explicit_surface_observation_version(cls, value):
        """集中只读 v4：保留历史协议身份，不把旧观察冒充已查过的空补充面。"""
        if isinstance(value, dict):
            protocol = value.get('protocol_version')
            if protocol == 'stage2-names-v4':
                if 'additional_surfaces' in value:
                    raise ValueError('names v4 不得携带 v5 的补充称谓声明')
                return {**value, 'additional_surfaces': {}}
            if protocol == 'stage2-names-v5' and 'additional_surfaces' not in value:
                raise ValueError('names v5 必须显式声明 additional_surfaces（没有则给空对象）')
        return value

    @model_serializer(mode='wrap')
    def _keep_historical_identity(self, handler):
        if self.protocol_version == 'stage2-names-v4' and self.additional_surfaces:
            raise ValueError('names v4 内部对象含补充称谓声明，拒绝丢弃证据或伪签历史身份')
        data = handler(self)
        if self.protocol_version == 'stage2-names-v4':
            data.pop('additional_surfaces', None)
        return data

    @model_validator(mode='after')
    def _historical_surface_scope(self):
        if self.protocol_version == 'stage2-names-v4' and self.additional_surfaces:
            raise ValueError('names v4 不得暗含补充称谓声明')
        return self
