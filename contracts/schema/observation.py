#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""observation —— Stage2 五节点观察与关联协议（B2；D3/D8/D9，显式版本化 I-13）。

【协议是什么】`str06.stage2.book`／`chapters`／`associate` 三个付费 semantic unit
的输出面（观察与关联决定，归已购恢复证据，不是普通缓存），以及 `names` 观察的
落盘载体。它们是 wire 协议：由 compiler 解析、执行关联并发布 canonical
（Characters＋Blueprint，唯一 writer `str06.stage2.compile`）。协议升级 = 改
`STAGE2_OBSERVATION_PROTOCOL` 字面量并同步本模块模型——旧协议盘面按版本字面量
关闭式拒读，不做运行时转换层（I-13：本仓无隐式通用迁移；旧根只读，新协议下
新根全量重购）。

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

from typing import Any, Final, Literal

from pydantic import Field, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.source import (
    Cognition, Effect, KnowledgeSpec, LoopAction, NameStructure, StateTarget,
)

#: 观察与关联协议的版本字面量（进请求身份与盘面信封；I-13 显式版本化）。
#: 升版 = 换字面量＋改模型＋登记（REQUIREMENTS／DECISIONS），不做转换层。
STAGE2_OBSERVATION_PROTOCOL: Final = 'stage2-observation-v1'

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


class WorldRule(StrictModel):
    """世界约束（写手若不知道就可能写错世界观的规则）。

    **没有** `constraint_id`：compiler 生成 `wc_*`（I-21）。
    """

    kind: str = Field(pattern=r'^(' + '|'.join(WORLD_RULE_KINDS) + r')$')
    statement: str = Field(min_length=1)
    source_refs: tuple[str, ...] = Field(min_length=1)


class BookObservation(StrictModel):
    """`str06.stage2.book` 的输出：人物候选（可读 slug、称谓及证据）、初态、
    世界约束。作用域＝书；输入是全书原文（含 e00 引子句段）与证据句段清单。"""

    protocol_version: Literal['stage2-observation-v1']
    characters: dict[str, CharacterCandidate] = Field(min_length=1)
    initial_state: tuple[InitialFact, ...] = Field(default=(), min_length=0)
    world_constraints: tuple[WorldRule, ...] = Field(default=(), min_length=0)

    @model_validator(mode='after')
    def _slugs_are_readable_handles(self) -> 'BookObservation':
        bad = [slug for slug in self.characters if not _slug_ok(slug)]
        if bad:
            raise ValueError(f'人物 slug 非法（小写蛇形）：{bad}')
        return self


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

    protocol_version: Literal['stage2-observation-v1']
    beats: tuple[ObsBeat, ...] = Field(min_length=1)


# ───────────────────────── associate 观察（str06.stage2.associate） ─────────────────────────

class PropositionPairing(StrictModel):
    """同一命题：把不同章观察里的同义命题句柄配成一对（D8）。

    关联只建立同一性与引用：合并后由 compiler 授予唯一正式 ID，不移动事实
    成立、角色知情或读者揭示时点。配对缺项保持未决（compile 拒绝发布并按
    item 报告），不默认合并也不默认不同。
    """

    left: str = Field(pattern=OBSERVATION_HANDLE_PATTERN)
    right: str = Field(pattern=OBSERVATION_HANDLE_PATTERN)


class StateDimensionPairing(StrictModel):
    """同一状态维度：近义状态键（如 trust／trust_level）或初态与章内效应的
    同维合并。compiler 据此归一到同一地址。"""

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
    对投影句柄核对），只建立同一性与引用（D8）。"""

    protocol_version: Literal['stage2-observation-v1']
    same_proposition: tuple[PropositionPairing, ...] = Field(default=(), min_length=0)
    same_state_dimension: tuple[StateDimensionPairing, ...] = Field(default=(), min_length=0)
    loop_pairing: tuple[LoopPairing, ...] = Field(default=(), min_length=0)
    cross_chapter_cause: tuple[CrossChapterCause, ...] = Field(default=(), min_length=0)
    scene_continuation: tuple[SceneContinuation, ...] = Field(default=(), min_length=0)


# ───────────────────────── names 观察（str06.stage2.names） ─────────────────────────

class NamesObservation(StrictModel):
    """`str06.stage2.names` 的落盘观察：源名称结构（家族、名、化名、称谓构词）。

    名称结构形状与 v4 `NameStructure` 同源（I-11）；compile 是 Characters 的
    唯一 writer（D9），names 观察是它的输入之一。语义协议（NAMES_RULES）不变
    （D9「names 不变」）。
    """

    protocol_version: Literal['stage2-observation-v1']
    structure: NameStructure
