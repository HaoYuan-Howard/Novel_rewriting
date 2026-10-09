#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Preserve/Brief/Bind RC2：Preserve 隔离于设计反馈，Brief 全语义去源坐标，
Binding 发布完整义务与单一验收位置。paid checkpoint 不是下游读取接口。
跨场复合义务通过较早支撑拍表达，不能把义务整体提前当成已成立事实。
"""
from __future__ import annotations

from pydantic import Field, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.common import FreeProvenance, LlmProvenance
from contracts.schema.policy import PreserveKind
from typing import Literal
from contracts.schema.expression import ExpressionRequirement, ExpressionReadings
from contracts.schema.hook import HookPov
from contracts.schema.state import StateAddress


class SourceLineage(StrictModel):
    """Obligation 的源主体引用：可综合多个源主体。

    计划原文：Obligation 是有 source lineage 的语义投影。蓝图拍、人物条目都可
    引用；两项都空 = 没有来源的断言，构造期即拒（lineage 为必填非空集合）。
    R10a a2 起 lineage 不再由 Preserve wire 输出——compiler 从义务 anchors
    （投影句柄）确定性派生（I-15/I-21），本模型保留为 canonical 侧的类型化结果。
    """

    blueprint_refs: tuple[str, ...] = Field(min_length=0)
    character_ids: tuple[str, ...] = Field(min_length=0)

    @model_validator(mode='after')
    def _not_empty(self) -> 'SourceLineage':
        if not (self.blueprint_refs or self.character_ids):
            raise ValueError('SourceLineage 必须至少引用一个源主体（blueprint 或 characters）')
        return self


#: 投影句柄的原子类别闭集（R10a a2）：拍内效应／认知／悬念、书级初态、世界约束。
ANCHOR_SOURCE_KINDS: tuple[str, ...] = (
    'effect', 'cognition', 'loop', 'initial_state', 'world_constraint')


class ObligationAnchor(StrictModel):
    """R10a a2：义务锚点——compiler 从投影句柄解析并**类型化拷贝**的源原子。

    Preserve 只给句柄（如 ``b06_21.e1``／``s0_01``／``wc_01``）；本模型的内容
    全部由 compiler 从 canonical 拷贝（持有者、mode、命题、效应目标/值/
    modality、逐字引文），不由模型复述（I-15/I-21）。a4 的知情/时态锁直接
    消费 holder／mode／proposition_ref／modality。CS-S 1b：`visibility` 删
    （effect.disclosure 已删——单写者、零逻辑读者的死字段）。
    """

    handle: str = Field(min_length=1)
    source_kind: str = Field(pattern=r'^(' + '|'.join(ANCHOR_SOURCE_KINDS) + r')$')
    #: 认知持有者（cognition.holder；其余 None）
    holder: str | None = None
    #: 认知模式（cognition.mode；其余 None）
    mode: str | None = None
    #: 效应操作（effect.op；其余 None）。a4 的 unknown 值锁输入（op=unknown
    #: ＝维度已确立但源文未给值——目标不得补值，与 modality 锁同一消费面）。
    op: str | None = None
    #: 命题正式 ID（cognition.proposition.ref；无则 None——effect 命题锚已删）
    proposition_ref: str | None = None
    proposition_statement: str | None = None
    #: 效应渲染地址（如 char:fu_jun.scene_location；初态/约束同形）
    address: str | None = None
    value: object = None
    #: 效应时态（effect.modality；其余 None）。a4 时态锁（kf）的输入。
    modality: str | None = None
    #: 悬念问题或收束（loop.question／resolution）
    question: str | None = None
    #: 逐字引文（basis quotes 原样拷贝；段归属已在 Stage2 判定，不重验证）
    quotes: tuple[str, ...] = Field(default=(), min_length=0)


class ObligationDetail(StrictModel):
    """Preserve wire 的可改写细节：语义转述与正式源原子的投影句柄。

    细节是可用材料；必要动机、铺垫与后果仍归 satisfaction。编号和完整源锚
    不由模型输出，compiler 解析后发布 SourceDetail。
    """

    anchor: str = Field(min_length=1)
    text: str = Field(min_length=1)

    @model_validator(mode='after')
    def _text_not_blank(self) -> 'ObligationDetail':
        if not self.text.strip():
            raise ValueError('细节 text 必须是非空的可改写转述')
        return self


class SourceDetail(StrictModel):
    """有源证据的可改写细节；身份与锚点均由 compiler 派生。"""

    detail_id: str = Field(pattern=r'^dt_[0-9a-f]{16}$')
    anchor: ObligationAnchor
    text: str = Field(min_length=1)

    @model_validator(mode='after')
    def _text_not_blank(self) -> 'SourceDetail':
        if not self.text.strip():
            raise ValueError('细节 text 必须是非空的可改写转述')
        return self


class BriefDetail(StrictModel):
    """源盲细节材料：不携带源拍、章号、引文或锚点。"""

    detail_id: str = Field(pattern=r'^dt_[0-9a-f]{16}$')
    text: str = Field(min_length=1)


class SourceObligation(StrictModel):
    """一条保存义务（Preserve 的输出原子）。

    `must_preserve=True` 时 `satisfaction` 必填非空——「必保要求必须有实质满足
    条件」的构造期表达；可综合类（must_preserve=False）允许留空 satisfaction。
    `requires` 是义务间必要因果（F3 起存在；DAG 校验归 compile/Guard，schema
    只保无自指）。R10a a2：`anchors` 必填 ≥1——义务锚定其依赖的源原子（投影
    句柄），`lineage` 由 compiler 从 anchors 派生（wire 不再输出 lineage）。
    """

    obligation_id: str = Field(min_length=1)
    kind: PreserveKind
    statement: str = Field(min_length=1)
    lineage: SourceLineage
    anchors: tuple[ObligationAnchor, ...] = Field(min_length=1)
    must_preserve: bool
    satisfaction: str = ''
    requires: tuple[str, ...] = Field(default=(), min_length=0)
    details: tuple[SourceDetail, ...] = Field(default=(), min_length=0)

    @model_validator(mode='after')
    def _must_preserve_needs_satisfaction(self) -> 'SourceObligation':
        if self.must_preserve and not self.satisfaction.strip():
            raise ValueError(
                f'必保义务 {self.obligation_id!r} 必须给出实质满足条件（satisfaction）')
        if self.obligation_id in self.requires:
            raise ValueError(f'义务 {self.obligation_id!r} 的 requires 不得包含自身')
        ids = [detail.detail_id for detail in self.details]
        if len(ids) != len(set(ids)):
            raise ValueError(f'义务 {self.obligation_id!r} 的细节不得重复')
        return self


class SeriesSummary(StrictModel):
    """系列内一次显式回顾及其覆盖的实例；不等同于全组的最后成员。"""

    member: str = Field(min_length=1)
    covers: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def _coverage_unique(self) -> 'SeriesSummary':
        if len(set(self.covers)) != len(self.covers):
            raise ValueError('summary covers 不得重复')
        if self.member in self.covers:
            raise ValueError('summary 不得覆盖自身')
        return self


class ObligationSeries(StrictModel):
    """R10a a2：同一机制的多次发生（离散兑现时点分别登记、义务各自独立）。

    members 是正式 obligation_id（compiler 从 wire 的 1-based 序号派发后重写）；
    mechanism 属源表达（blueprint.source_expression.items 的 ex_id）或 None——
    compiler 只查闭集与互斥，不判断「是否同一机制」（语义归 Preserve）。
    """

    members: tuple[str, ...] = Field(min_length=2)
    mechanism: str | None = None
    summaries: tuple[SeriesSummary, ...] = Field(default=(), min_length=0)

    @model_validator(mode='after')
    def _members_unique(self) -> 'ObligationSeries':
        if len(set(self.members)) != len(self.members):
            raise ValueError(f'series 成员不得重复：{self.members}')
        summaries = [summary.member for summary in self.summaries]
        if len(set(summaries)) != len(summaries):
            raise ValueError('series summary 成员不得重复登记')
        summary_ids = set(summaries)
        members = set(self.members)
        for summary in self.summaries:
            if summary.member not in members or set(summary.covers) - members:
                raise ValueError('summary 与 covers 必须属于同一个 series 闭集')
            if set(summary.covers) & summary_ids:
                raise ValueError('summary covers 只能引用实例，不得覆盖 summary')
        return self


class PreserveCheckpoint(StrictModel):
    """已购 Preserve 结果的身份与内容（paid checkpoint）。

    经过校验、hash-bound、可恢复；**不是下游读取接口**——下游消费
    ReconstructionBrief / SemanticBinding。执行身份记录 provider/model/请求
    hash，供「同请求身份下历史采用」与成本对账（I-37/I-43）。
    R10a a2：义务带类型化 anchors；顶层增 series（同一机制的多次发生）。
    """

    contract_version: Literal['str06-rc21'] = 'str06-rc21'
    provenance: FreeProvenance | LlmProvenance | None = Field(
        default=None, alias='_provenance', exclude=True)
    checkpoint_id: str = Field(min_length=1)
    policy_id: str = Field(min_length=1)
    policy_version: int = Field(ge=1)
    characters_sha256: str = Field(min_length=64, max_length=64)
    blueprints_sha256: str = Field(min_length=64, max_length=64)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    request_sha256: str = Field(min_length=64, max_length=64)
    obligations: tuple[SourceObligation, ...] = Field(min_length=1)
    series: tuple[ObligationSeries, ...] = Field(default=(), min_length=0)
    checkpoint_sha256: str = Field(min_length=64, max_length=64)

    @model_validator(mode='after')
    def _unique_obligation_ids(self) -> 'PreserveCheckpoint':
        ids = [o.obligation_id for o in self.obligations]
        if len(set(ids)) != len(ids):
            raise ValueError('obligation_id 在 checkpoint 内必须唯一（稳定 ID，I-31）')
        detail_ids = [detail.detail_id for obligation in self.obligations
                      for detail in obligation.details]
        if len(set(detail_ids)) != len(detail_ids):
            raise ValueError('checkpoint 内细节身份必须唯一')
        known = set(ids)
        seen: set[str] = set()
        for group in self.series:
            outside = [m for m in group.members if m not in known]
            if outside:
                raise ValueError(f'series 成员不在义务闭集内：{outside}')
            overlap = sorted(set(group.members) & seen)
            if overlap:
                raise ValueError(f'同一义务进了多个 series（互斥）：{overlap}')
            seen.update(group.members)
        return self


class IdentityProjection(StrictModel):
    """Design 的输入之一：稳定 char_id → 身份声明。

    来源 Characters（公共投影）；只给身份与稳定约束，不给源坐标。Design 由此
    在 source-blind 前提下仍能引用「同一个人」。
    """

    char_id: str = Field(min_length=1)
    identity_statement: str = Field(min_length=1)
    stable_constraints: tuple[str, ...] = Field(default=(), min_length=0)


class BriefRequirement(StrictModel):
    """Brief 中的保存要求：所有类别与满足条件，无源章坐标。"""

    core_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    kind: PreserveKind
    must_preserve: bool
    satisfaction: str
    participants: tuple[str, ...] = Field(default=(), min_length=0)
    # 必要因果的显式边（RC：起止都是 core_id；DAG 校验归 F4 Guard）
    requires_cores: tuple[str, ...] = Field(default=(), min_length=0)
    #: 本义务 anchors 派生的锁句柄（kp_*/kf_*，a4；信息面——哪些命题/事实
    #: 进了本义务的锁视野）。
    locks: tuple[str, ...] = Field(default=(), min_length=0)
    #: 本义务的每次认知变化，保留持有者、命题与模式的对应关系。compiler
    #: 从正式 anchor 铸盲身份；Design 只标注短引用的拍位，不重写这三个值。
    knowledge_changes: tuple[BriefKnowledgeChange, ...] = Field(default=(), min_length=0)
    #: 必保义务须兑现的事实锁（kf_*）：Design 在**已选的目标状态**上以 `lock`
    #: 标注承载（时态锁须同 modality 的 set、值未知锁须 op=unknown；CS-F2，
    #: 不再另写以 kf 为键的重复状态）；非必保义务为空。
    realize_locks: tuple[str, ...] = Field(default=(), min_length=0)
    #: series 盲标记（a4）：同一机制的离散兑现义务共享同一盲组 token；
    #: None＝不属任何 series。source-blind——token 由 compiler 铸，不泄源序号。
    series_group: str | None = None
    #: series 的机制（源表达 ex_id 或 None；与 PreserveCheckpoint.series 的
    #: mechanism 同值，供 Design/Bind 理解「同一机制的多次发生」）。
    series_mechanism: str | None = None
    details: tuple[BriefDetail, ...] = Field(default=(), min_length=0)
    #: 显式回顾覆盖的实例；从唯一的 PreserveCheckpoint.series.summaries 派生。
    #: 引用改为盲 core_id，不携带源顺序或坐标。
    summarizes_cores: tuple[str, ...] = Field(default=(), min_length=0)


#: 知情锁的目标侧模式闭集（a4）：源认知模式的唯一映射——learns/confirms→known、
#: suspects→suspected、hears_claim→heard、believes→believes；asserts 不铸知情锁
#: （公开声称是关于世界的事实，不是持有者的知识——转命题登记的公开声称标记）。
KNOWLEDGE_LOCK_MODES: tuple[str, ...] = (
    'known', 'suspected', 'heard', 'believes')

#: 事实锁的类别闭集（a4）：planned/expected/conditional＝时态锁（modality≠actual
#: 的效应——目标只能按同 modality 承载，不得升格为 actual）；unknown_value＝
#: 值未知锁（op=unknown 的效应——目标不得补值）；unanswered＝原文未回答锁
#: （canonical 里 open 而从未 close 的悬念——目标不得作答）。
FACT_LOCK_KINDS: tuple[str, ...] = (
    'planned', 'expected', 'conditional', 'unknown_value', 'unanswered')


def _lock_id(prefix: str, seed: str) -> str:
    """锁/命题盲句柄：prefix + sha256(seed)[:16]（同一 seed 恒同一句柄）。

    与 `obligation_core_id` 同一铸造纪律（R8a-3）：源盲——Design 看得到稳定
    句柄，看不到源命题/效应/悬念的原始编号与顺序。
    """
    import hashlib
    return prefix + hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]


class BriefProposition(StrictModel):
    """kp 命题登记（a4）：Brief 里可被知情/读者确认引用的命题，盲句柄 kp_<hash>。

    `publicly_claimed_by` 是 asserts 认知的转登记（「某人公开声称」事实锁）：
    命题以「被谁公开声称」的面目出现，**不产生任何持有者的知情锁**——Design
    不能据此写 known/suspected（说辞与确信是两回事，D5）。
    """

    kp_id: str = Field(pattern=r'^kp_[0-9a-f]{16}$')
    statement: str = Field(min_length=1)
    publicly_claimed_by: tuple[str, ...] = Field(default=(), min_length=0)


class BriefKnowledgeLock(StrictModel):
    """知情锁（a4）：{持有者|reader, kp, mode}——谁对该命题持什么模式。

    Design 的 knowledge 域（及 reader 域确认）用 kp 键承载该命题时，持有者
    必须有锁且模式被授权（known 只能来自 known 锁——C1 的机械面）。
    """

    holder: str = Field(min_length=1)
    kp: str = Field(pattern=r'^kp_[0-9a-f]{16}$')
    mode: str = Field(pattern=r'^(' + '|'.join(KNOWLEDGE_LOCK_MODES) + r')$')


class BriefKnowledgeChange(BriefKnowledgeLock):
    """单次认知变化的源盲身份；同一命题反复变化不按 (holder,kp) 合并。

    `requires_released`（CS-H H3，RC-61 必保揭名）是 compiler 从三项**类型化**
    信号合取派生的结果：所属命题被 identity／reveal 类必保义务引用、该命题的
    源证据句段正是某带名称谓在源文首次出现的句段、命题文本含该称谓——三项
    都来自已有数据（义务类别、Blueprint 证据句段、Characters 的 source_refs），
    不用文本共现作判定；句段号不进 Brief（保持源盲），只给称谓字面。该 know
    所在拍必须已释放所列称谓（同拍释放合法），承载章正文必现该字面；这类
    称谓不投影。漏检（三项未同时满足的揭名）退化为普通投影，不比现状差。
    """

    change_id: str = Field(pattern=r'^kc_[0-9a-f]{16}$')
    requires_released: tuple[str, ...] = Field(default=(), min_length=0)


class BriefFactLock(StrictModel):
    """事实锁（a4）：kf_<hash>——时态/未知/未回答的源事实约束。

    时态锁（planned/expected/conditional）：Design 只能按同 modality 承载
    （写成 actual 即「预计被写成已发生」，C2 的机械面）；unknown_value：源文
    值未知，目标不得补值（C9）；unanswered：原文未回答的悬念，目标不得作答。
    CS-F2（rc16）：承载方式是在 Design 已选的状态上标注 `lock` 引用（锁句柄只
    是引用，不再作状态键，也不要求复述 statement）；unanswered 是禁区，不承载
    状态、也不进 wire 的 `lock` 闭集。`statement` 是源侧语义，只供 Design 理解
    约束，不是写手阅读材料。
    """

    kf_id: str = Field(pattern=r'^kf_[0-9a-f]{16}$')
    statement: str = Field(min_length=1)
    kind: str = Field(pattern=r'^(' + '|'.join(FACT_LOCK_KINDS) + r')$')
    #: 锁主体（CS-H H3，RC-63 主体包含检查的输入）：compiler 从 effect 锚点的
    #: 作用对象派生（char:x→(x,)；rel:a×b→(a,b)；world→()）。承载该锁的状态
    #: 主体必须包含锁主体（此前丈夫的锁可标到别人的状态上而通过）。
    subjects: tuple[str, ...] = Field(default=(), min_length=0)
    #: 时态锁的源 modality（planned/expected/conditional）；其余类别 None。
    modality: str | None = None

    @model_validator(mode='after')
    def _kind_matches_modality(self) -> 'BriefFactLock':
        expected = self.kind if self.kind in ('planned', 'expected',
                                              'conditional') else None
        if self.modality != expected:
            raise ValueError(
                f'事实锁 {self.kf_id} 的 modality {self.modality!r} 与类别'
                f' {self.kind!r} 不一致')
        return self


class BookConstraint(StrictModel):
    """世界约束的稳定登记（rc13，CS-3）：源 blueprint 世界约束的 id＋命题。

    `constraint_id` 沿用源 `wc_{nn}` 句柄（蓝图既有稳定编号，不新造序列，
    I-21）——design wire 的 world 域规则断言（value 恒真的布尔键）以该编号
    引用，渲染面经本表把 key 映射回 statement 文本（r10b 审计：`women_cannot_
    stand_alone=true` 这类布尔键无机械 statement 映射，渲染零信息）。
    """

    constraint_id: str = Field(pattern=r'^wc_[A-Za-z0-9_-]+$')
    statement: str = Field(min_length=1)


class ReconstructionBrief(StrictModel):
    """确定性发布的 Derived Brief——Design 的唯一语义输入（与 IdentityProjection、
    OutputConstraints 并列）。

    ⚠ 本模型刻意**没有**以下字段，且 `extra='forbid'` 会拒绝任何试图混入的同义
    键：源章号、源拍 id、源叙述顺序、evidence 定位、binding refs。屏蔽源叙述
    拓扑是 Preserve→Design 的冻结边界，字段缺失就是它的类型层表达。

    【a4 锁定项】`propositions`／`knowledge_locks`／`fact_locks` 由 compiler 从
    义务 anchors 与 canonical loops 确定性派生（`project_brief`）：命题、知情
    与时态以**盲句柄**（kp_*/kf_*）进 Design——锁的内容是语义（命题文本、
    模式、modality），坐标（拍号/句段/引文）仍然屏蔽。锁定的知情/时态/未知
    不得经状态或说明改写；兑现与越权校验归 assemble_body_design。
    """

    contract_version: Literal['str06-rc21'] = 'str06-rc21'
    brief_id: str = Field(min_length=1)
    preserve_checkpoint_sha256: str = Field(min_length=64, max_length=64)
    requirements: tuple[BriefRequirement, ...] = Field(min_length=1)
    #: kp 命题登记（a4）：义务 anchors 引用到的全部命题，盲句柄。
    propositions: tuple[BriefProposition, ...] = Field(default=(), min_length=0)
    #: 知情锁（a4）：{持有者|reader, kp, mode}；known 只能来自 known 锁。
    knowledge_locks: tuple[BriefKnowledgeLock, ...] = Field(
        default=(), min_length=0)
    #: 事实锁（a4）：时态（modality≠actual）／值未知（op=unknown）／
    #: 原文未回答（canonical 悬念 open 未 close）。
    fact_locks: tuple[BriefFactLock, ...] = Field(default=(), min_length=0)
    book_constraints: tuple[BookConstraint, ...] = Field(
        default=(), min_length=0)
    source_narration: HookPov
    expression: tuple[ExpressionRequirement, ...] = Field(min_length=1)
    expression_readings: ExpressionReadings
    provenance: FreeProvenance | LlmProvenance | None = Field(
        default=None, alias='_provenance', exclude=True)

    @model_validator(mode='after')
    def _unique_core_ids(self) -> 'ReconstructionBrief':
        ids = [c.core_id for c in self.requirements]
        if len(set(ids)) != len(ids):
            raise ValueError('core_id 在 Brief 内必须唯一')
        if any(set(c.requires_cores) - set(ids) for c in self.requirements):
            raise ValueError('Brief 依赖必须闭合，非事件义务也必须投影')
        requirements = {c.core_id: c for c in self.requirements}
        detail_ids = [detail.detail_id for c in self.requirements for detail in c.details]
        if len(detail_ids) != len(set(detail_ids)):
            raise ValueError('Brief 细节身份必须唯一')
        for requirement in self.requirements:
            covers = requirement.summarizes_cores
            if len(set(covers)) != len(covers) or requirement.core_id in covers:
                raise ValueError('Brief 回顾覆盖不得重复或自指')
            for core in covers:
                instance = requirements.get(core)
                if instance is None:
                    raise ValueError('Brief 回顾覆盖引用必须闭合')
                if (requirement.series_group is None
                        or instance.series_group != requirement.series_group):
                    raise ValueError('Brief 回顾与覆盖实例必须属于同一 series')
                if instance.summarizes_cores:
                    raise ValueError('Brief 回顾不得覆盖另一个回顾')
        # 锁引用闭合（a4）：知情锁引用已登记命题；义务的 locks 引用已登记锁；
        # 每个认知变化的 holder/kp/mode 必须引用已登记的授权锁行。
        kp_ids = {p.kp_id for p in self.propositions}
        kf_ids = {f.kf_id for f in self.fact_locks}
        lock_triples = {(lock.holder, lock.kp, lock.mode) for lock in self.knowledge_locks}
        for lock in self.knowledge_locks:
            if lock.kp not in kp_ids:
                raise ValueError(f'知情锁引用未登记命题：{lock.kp}')
        for req in self.requirements:
            unknown = [x for x in req.locks if x not in kp_ids | kf_ids]
            if unknown:
                raise ValueError(f'义务 {req.core_id} 的 locks 引用未登记锁：{unknown}')
            outside = [x for x in req.realize_locks if x not in kf_ids]
            if outside:
                raise ValueError(f'义务 {req.core_id} 的 realize_locks 引用未登记事实锁：{outside}')
            change_ids = [c.change_id for c in req.knowledge_changes]
            if len(change_ids) != len(set(change_ids)):
                raise ValueError(f'义务 {req.core_id} 的认知变化身份重复')
            for change in req.knowledge_changes:
                if (change.holder, change.kp, change.mode) not in lock_triples:
                    raise ValueError(
                        f'义务 {req.core_id} 的 knowledge_changes 引用无锁行：{change!r}')
        return self


class OutputConstraints(StrictModel):
    """Design 的输出边界约束：目标形状的机械可验证部分。

    章数 N 与篇幅边界来自书级配置（不来自源章对照——「保留全书篇幅边界，不对照
    同号源章长度」）；目标预算按 Design 的章/场权重分配（Design 自己的权重字段）。
    """

    target_chapter_count: int = Field(ge=1)
    total_length_budget: int = Field(ge=1, description='全书正文总字数预算上限')


class BindingEntry(StrictModel):
    """一条义务到目标位置的承载声明（Bind 输出原子）。"""

    obligation_id: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    beat_ref: str = Field(min_length=1)
    supporting_beat_refs: tuple[str, ...] = ()

    @model_validator(mode='after')
    def _beat_ref_scene_consistent(self) -> 'BindingEntry':
        # beat_ref 若给出，必须指 scene 内的拍（`<scene_id>#<beat_id>`）。
        if not self.beat_ref.startswith(f'{self.scene_id}#'):
            raise ValueError(f'beat_ref {self.beat_ref!r} 必须落在所属场景 {self.scene_id!r} 内')
        if len(set(self.supporting_beat_refs)) != len(self.supporting_beat_refs):
            raise ValueError('支撑拍不得重复')
        return self


class UnboundEntry(StrictModel):
    """未承载的义务：保留原义务并返回正确 owner——Bind 不得删义务迁就 Design。"""

    obligation_id: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class FactLockBinding(StrictModel):
    """事实锁的目标承载映射（CS-F2，rc16）：锁句柄 → 承载它的**已有**目标状态。

    只存引用——锁句柄、承载拍与状态地址，**不复制目标 value**：BodyDesign 是
    目标事实的唯一持有者（I-16）。映射由 body_design 与 BodyDesign 同次发布，
    compiler 从 wire 在已选状态上的 `lock` 标注派生——模型只选已有状态，不生成
    拍引用或地址（I-21）。一个锁至多一个承载状态，承载拍内该地址必须恰好对应
    一条状态；不存在或有多条同址状态均拒绝。锁的内容是否被该状态语义满足
    不由本映射证明（结构检查与语义核验分开，未标定的语义保持 unver，I-42）。
    """

    kf_id: str = Field(pattern=r'^kf_[0-9a-f]{16}$')
    beat_ref: str = Field(pattern=r'^[^#]+#[^#]+$', description='承载拍 `场景#拍`')
    address: StateAddress


class SemanticBinding(StrictModel):
    """完整 Canonical snapshot（一次发布，无半份）。

    自洽校验：entries 与 unbound 的 obligation_id 并集内无重复（每条义务至多出现
    一次）；跨产物「全分母 = 采用的 checkpoint obligations」归 F4 机械 Guard。
    rc16（CS-F2）：`fact_bindings` 承载事实锁到已有目标状态的映射；空表是合法值
    （＝没有锁被兑现，由 body_design 的接受校验保证必保锁必有映射）。旧 rc12
    产物没有该映射，由 `contract_version` 字面量拒读——缺省空表不会冒充旧产物。
    """

    contract_version: Literal['str06-rc21'] = 'str06-rc21'
    binding_id: str = Field(min_length=1)
    preserve_checkpoint_sha256: str = Field(min_length=64, max_length=64)
    obligations: tuple[SourceObligation, ...] = Field(min_length=1)
    entries: tuple[BindingEntry, ...] = Field(min_length=0)
    unbound: tuple[UnboundEntry, ...] = Field(min_length=0)
    fact_bindings: tuple[FactLockBinding, ...] = Field(default=(), min_length=0)
    #: 采用的 checkpoint 系列原样投影，供写手消费回顾覆盖关系；不另建系列真源。
    series: tuple[ObligationSeries, ...] = Field(default=(), min_length=0)
    provenance: FreeProvenance | LlmProvenance | None = Field(
        default=None, alias='_provenance', exclude=True)

    @model_validator(mode='after')
    def _no_double_bookkeeping(self) -> 'SemanticBinding':
        locked = [f.kf_id for f in self.fact_bindings]
        if len(locked) != len(set(locked)):
            raise ValueError(f'同一事实锁在 fact_bindings 内重复承载：{sorted(locked)}')
        ids = [o.obligation_id for o in self.obligations]
        if len(ids) != len(set(ids)):
            raise ValueError('Binding 完整义务内 ID 不得重复')
        detail_ids = [detail.detail_id for obligation in self.obligations
                      for detail in obligation.details]
        if len(detail_ids) != len(set(detail_ids)):
            raise ValueError('Binding 完整义务内细节身份必须唯一')
        known = set(ids)
        seen: set[str] = set()
        for group in self.series:
            if set(group.members) - known:
                raise ValueError('Binding series 成员必须在完整义务闭集内')
            if set(group.members) & seen:
                raise ValueError('Binding series 成员互斥')
            seen.update(group.members)
        bound = [e.obligation_id for e in self.entries]
        returned = [u.obligation_id for u in self.unbound]
        if len(set(bound)) != len(bound):
            raise ValueError(f'同一义务在 entries 内重复承载：{sorted(bound)}')
        if len(set(returned)) != len(returned):
            raise ValueError(f'同一义务在 unbound 内重复登记：{sorted(returned)}')
        overlap = set(bound) & set(returned)
        if overlap:
            raise ValueError(f'义务同时出现在 entries 与 unbound：{sorted(overlap)}')
        return self
