#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
report.py —— 对应 `docs/schema/_report.md`。校验报告。

【它与 `common.py` 的分界线】`common.py` 管**每份产物里都有的那几个键**
（`_provenance` / `Finding` / `usage`）；本模块管**产物之外的一份东西**——
校验报告不落在任何一份 artifact 里，它是 `--check-only` 打印的、API 返回的那一份。

【本模块存在的那一句话】**权威从「结论的集合」翻转成「判据的集合」。**
旧形状（盘上两份真实抓包）的 `oks` 是一串**没有 rule id 的散文字符串**，
于是「一条判据跑了没命中」与「它压根没被调用」在响应里**无法区分**——
补一个 `skipped` 桶也救不了，因为桶是由**产出**填的，而没跑的那条判据本来就没有产出。
⇒ 报告按 `REGISTRY.of_stage(stage)` 编排，每条注册判据**恰好一行**。

⚠ 本模块 import `contracts.rules`（工厂要从注册表取 `sev`/`item`/`owner`）。
无环：`rules.py` 只 import `contracts.invariants`。
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from contracts.invariants import (
    BADGE, CHECK_KIND, FRESHNESS, FRESHNESS_REASON, REMEDIATION, STATUS,
)
from contracts.rules import REGISTRY, Registry
from contracts.schema.base import FrozenModel, StrictModel
from contracts.schema.common import Finding, WontFix

#: 枚举一律从 `contracts.invariants` 取，不在本文件重抄（同 `common.py` 的写法）。
Status = Literal[STATUS]
Badge = Literal[BADGE]
Freshness = Literal[FRESHNESS]
FreshnessReason = Literal[FRESHNESS_REASON]
CheckKind = Literal[CHECK_KIND]
Remediation = Literal[REMEDIATION]


# ─────────────────────────────────────────────────────────────────────────────
# 一、`Finding` 的工厂 —— `_common.md` §七 设计要点 1 的唯一实现
# ─────────────────────────────────────────────────────────────────────────────

def finding(rule: str, *, detail: str, evidence: list[str],
            identity_hit: bool = False, identity_dead: bool | None = None,
            converge: str = 'new', unresolved_rounds: int = 0,
            wontfix: WontFix | None = None,
            registry: Registry = REGISTRY) -> Finding:
    """造一条结论。**调用方只给 `detail` + `evidence`。**

    `sev` / `item` / `owner` / `actionable` **一律从判据注册表取**
    ⇒ 一条 finding 不可能与判据表说的不一致（`_common.md` §七 设计要点 1）。

    ⚠ **`actionable` 从 `repair != 'NONE'` 派生，`Rule` 上不加第二个字段。**
    两条已有的不变量正好同构：`rules.py::_validate` ③ 是
    `repair != 'NONE' ⇒ owner == 'stage5'`，而 `Finding._postconditions` ③ 是
    `actionable ⇒ owner == 'stage5'`。加一个字段等于给同一件事开第二个落点，
    而「同一件事的第 N 个落点必然分家」正是本项目要消灭的病。
    ⚠ stage6 / stage7 会推翻这条派生（那两层的 `actionable` 意思是「人能 0 成本改掉它」），
    **那是一次语义裁决不是笔误**，登记在 `docs/schema/_report.md` §2.1，移植那两层时一起裁。

    `evidence_key` 不收参数：它是 `evidence` 的函数，由载体的后置条件断言
    （设计要点 2——免费，且不可能忘）。
    """
    r = registry.get(rule)
    return Finding(
        rule=r.rule,
        sev=r.sev,
        item=r.item,
        detail=detail,
        evidence=list(evidence),
        evidence_key='|'.join(evidence),
        owner=r.owner,
        actionable=r.repair != 'NONE',
        identity_hit=identity_hit,
        identity_dead=identity_dead,
        converge=converge,
        unresolved_rounds=unresolved_rounds,
        wontfix=wontfix,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 二、一条注册判据 = 恰好一行（`_report.md` §二）
# ─────────────────────────────────────────────────────────────────────────────

class FixRoute(FrozenModel):
    """前端可直接执行的修复路由；贵贱仍只由服务端计算。"""

    node: str = Field(min_length=1)
    flags: list[str] = Field(min_length=1)
    paid: bool


class RuleLine(FrozenModel):
    """报告的**权威单位**。注意它由 `Rule` 生成，不由 findings 生成。"""

    rule: str = Field(min_length=1)
    sev: str
    status: Status
    item: str = Field(min_length=1)
    requirement: str = Field(min_length=1)
    check_kind: CheckKind = 'mechanical'
    remediation: Remediation = 'human'
    fix: FixRoute | None = None

    #: 这条判据这一次产出的结论。
    #:
    #: ⚠ **`sev='metric'` 的行也用这个字段装它的读数**——一条度量「无条件产出」，
    #: 而读数就是它的产出。给度量另开一个 `readings` 字段等于同一件事两个落点，
    #: 且 `Finding` 已经是全链唯一那一份（原型有 5 份拷贝、3 种字段签名）。
    #: ⇒ 区别不在字段上，在 `status` 上：**度量永不 `hit`**。
    findings: list[Finding] = Field(default_factory=list)

    #: `status ∈ {skipped, unver}` 时**必填**：为什么这一次够不着。
    #: 「实现根本不存在」与「实现在，但这条路径没走到」分不开就没法判断该不该修。
    why: str | None = None

    #: 这一次**扫了多少**（`8 个文本字段` / `拿 22 个专名扫过 20 个字段` / `body 14 项`）。
    #:
    #: ⚠ **判据 31：一个不知道自己扫了多少的绿灯没有判别力。** 一条判据打 `[OK]`
    #: 而没人知道它的分母，与它根本没跑在报告上长得一样近。
    #: ⇒ 它对**任何** `status` 都允许（`skipped` 那一行天然没有，因为它没扫）。
    scanned: str | None = None

    #: 词表类判据的基线命中率。`wordlist=True` 时**必填**（`Q-9`：永远打印基线）。
    baseline: str | None = None

    @model_validator(mode='after')
    def _postconditions(self) -> RuleLine:
        # ① 度量永不「命中」。写成 `hit` 会让「现状读数」重新并回「告警」那一支（判据 21）。
        if self.sev == 'metric' and self.status == 'hit':
            raise ValueError(
                f'{self.rule}: sev=metric 却 status=hit。度量是**无条件产出的读数，不是结论**，'
                f'它永远不命中——让它命中就是把度量与告警重新合流'
            )
        # ② 结论类判据：有结论 ⇔ 命中。度量不在此列（它有读数但不命中）。
        if self.sev != 'metric':
            if bool(self.findings) != (self.status == 'hit'):
                raise ValueError(
                    f'{self.rule}: status={self.status!r} 与 {len(self.findings)} 条结论对不上。'
                    f'结论类判据「有结论」与「命中」是同一件事'
                )
        # ③ 没跑成的那一行不许携带结论——它这一次一条代码都没执行过。
        if self.status == 'skipped' and self.findings:
            raise ValueError(f'{self.rule}: status=skipped 却带着 {len(self.findings)} 条结论')
        # ④ 「够不着」与「没跑成」都必须说出原因；跑过的两态不许有原因（那是噪声）。
        needs_why = self.status in ('skipped', 'unver')
        if needs_why and not self.why:
            raise ValueError(
                f'{self.rule}: status={self.status!r} 必须写出原因。'
                f'“没被调用”有两种（实现不存在 / 这条路径没走到），分不开就没法判断该不该修'
            )
        if not needs_why and self.why:
            raise ValueError(f'{self.rule}: status={self.status!r} 不该带 why={self.why!r}')
        # ④b 没跑成的那一行不可能扫过任何东西。
        if self.status == 'skipped' and self.scanned:
            raise ValueError(
                f'{self.rule}: status=skipped 却报了扫描量 {self.scanned!r}——它这一次没有执行过')
        # ⑤ 每条 finding 都得是这一行自己的。串行归并时错位过一次，代价是结论指错判据。
        bad = sorted({f.rule for f in self.findings} - {self.rule})
        if bad:
            raise ValueError(f'{self.rule}: 这一行里混进了别的判据的结论：{bad}')
        return self


class RetiredLine(FrozenModel):
    """退役判据。**退役而不删**，带着理由常驻一行（`stage3.md` §六 设计要点③）。

    ⚠ **退役的是判据，不是要求**：stage3 的 `cliche` 撤了，而 `STYLE_SYS` 里
    那句「不要用『语言流畅／节奏明快』这类放之四海皆准的话填格」**仍在下发**——
    那张九词表因此没有任何现役消费方，是判据 35 的教科书实例。
    常驻列出，是为了让「量过、量不准，所以撤了」与「忘了」读起来不一样。
    """

    rule: str = Field(min_length=1)
    why: str = Field(min_length=1)


# ─────────────────────────────────────────────────────────────────────────────
# 三、来源在前（`_report.md` §五）
# ─────────────────────────────────────────────────────────────────────────────

class EdgeLine(FrozenModel):
    """一条来源边的判定。字段与 `novel/prov.py::EdgeVerdict` 一一对应。

    ⚠⚠ **指纹这四格是 `str | None` 而不是 `Fp10 | None`，这是挑过的。**
    盘上存量产物的 `inputs` 值是扁平字符串（297 条），而本报告最该跑得通的时刻
    恰恰是「这份产物已经不合当前 schema」的时候。给它一个受约束类型，
    一条畸形的旧指纹就会让**整份报告构造失败**——而 `store.freshness` 的注释
    亲自写过这条：「别让一条坏键把『有一条边指向已删文件』变成『整份报告出不来』」。
    """

    key: str = Field(min_length=1)
    state: Freshness
    reason: FreshnessReason
    recorded_file: str | None
    now_file: str | None
    recorded_consumed: str | None
    now_consumed: str | None


class ProvenanceSection(StrictModel):
    """报告的第一节。**来源在前，判定在后**（`Q-4`）。

    ⚠ **空也要报。** 对不上时报出来的一堆违规是「拿新尺子量旧布」，
    不说破就会被当成一次回归（`R-5.10` 就是补这一行的）。
    """

    freshness: Freshness
    reason: FreshnessReason

    #: ⭐ 指向已经不在盘上的文件的那些边，**按名字点名**。
    #: `Q-4` 亲自点过的 bug：原型对「取不到指纹」的条目**静默跳过**，
    #: 而盘上真有一条（0 成本提升路读完 `.INVALID` 就把它删了）。
    ghosts: list[str] = Field(default_factory=list)

    edges: list[EdgeLine] = Field(default_factory=list)

    #: 判的这一份是不是 `.INVALID` 稿。
    invalid: bool = False

    #: 三态照 `_provenance` 原样带下来：`None` = 键不在（这份产物太老，没记过这件事）。
    revalidated: bool | None = None
    origin: str | None = None

    @model_validator(mode='after')
    def _ghosts_match_the_edges(self) -> ProvenanceSection:
        """点名的那几条必须真的是幽灵边。**手抄一份名单必然与判定分家。**"""
        expect = sorted(e.key for e in self.edges if e.reason == 'ghost')
        if self.edges and sorted(self.ghosts) != expect:
            raise ValueError(
                f'ghosts={sorted(self.ghosts)} 与逐条边算出来的 {expect} 对不上。'
                f'点名清单只能从 edges 派生'
            )
        return self


# ─────────────────────────────────────────────────────────────────────────────
# 四、记账键的出口（`_report.md` §六 · `_common.md` §九）
# ─────────────────────────────────────────────────────────────────────────────

class AccountingLine(StrictModel):
    """一个记账键的出口。**一行常驻，非空才展开。**"""

    key: str = Field(min_length=1)
    item: str = Field(min_length=1)

    #: 产地这次记了没有。`False` ⇒ 渲染成 `[????] 未记录`
    #: ——「记了 0 次」与「压根没记」必须分开。
    recorded: bool

    #: 值为空时是 `0`，渲染成 `… 0 处`——「没改过」与「没在报」必须分开。
    count: int = Field(ge=0)

    #: 非空才展开的那部分。**空也要有这个键。**
    detail: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def _unrecorded_carries_nothing(self) -> AccountingLine:
        if not self.recorded and (self.count or self.detail):
            raise ValueError(
                f'{self.key}: recorded=False 却带着 count={self.count} / {len(self.detail)} 条明细。'
                f'「没记」就是没有任何数——有数说明它其实记了'
            )
        return self


# ─────────────────────────────────────────────────────────────────────────────
# 五、徽章 —— 服务端**一个函数**（`R-11.7`：通过与否只有一处算）
# ─────────────────────────────────────────────────────────────────────────────

def badge_of(rules: list[RuleLine]) -> str:
    """三态。**`skipped` 挂在 HARD 上时进 `fail`，不是 amber。**

    一条阻断级判据这次没被执行，与它执行了并且命中，对调用方是同一件事：
    **你不能拿这份产物往下走**。把它降成 amber 等于说「大概没事」，而我们恰恰不知道。

    ⚠ `amber` 不是绿色（`invariants.BADGE` 已写死：`passed and unver_count > 0`）
    ⇒ **「pass + N 项未验证」在类型上不可能**。
    ⚠ `sev='metric'` 的行**永不影响徽章**：度量无条件产出、永不命中，
    让它参与判定就是把「现状读数」重新并回「告警」那一支（判据 21）。
    """
    if any(r.sev == 'hard' and r.status in ('hit', 'skipped') for r in rules):
        return 'fail'
    if any(r.status in ('unver', 'skipped') for r in rules):
        return 'amber'
    return 'pass'


class StatusCounts(StrictModel):
    """四态各几条。**做成模型不做成 dict**：`extra='forbid'` 管不住 dict 的键，
    而这四个数是要发给前端画计数的——多一个键少一个键都得当场炸。"""

    ok: int = Field(ge=0)
    hit: int = Field(ge=0)
    unver: int = Field(ge=0)
    skipped: int = Field(ge=0)

    @classmethod
    def of(cls, rules: list[RuleLine]) -> StatusCounts:
        return cls(**{s: sum(1 for r in rules if r.status == s) for s in STATUS})


# ─────────────────────────────────────────────────────────────────────────────
# 六、顶层
# ─────────────────────────────────────────────────────────────────────────────

class CheckReport(StrictModel):
    """一次校验的全部输出。

    ⚠ **四个 sev 桶（`oks`/`unver`/`info`/`hard`）不在这里，那是刻意的。**
    前端要分桶就拿 `rules` 按 `sev` groupby——同一份数据只有一个落点。
    留一份派生的桶当兼容层，等于把已经翻转过来的权威又翻回去一半
    （旧形状的 `hard_count` 与 `hard[]` 的长度已经是两个字段了）。
    """

    sid: str = Field(min_length=1)

    #: `<stage>.<动词>`。⚠ `n1..n9` 整套已退役（A0 的命名裁决）。
    node: str = Field(min_length=1)
    stage: str = Field(min_length=1)

    #: 第一节恒是它。**空也要报。**
    provenance: ProvenanceSection

    #: ⭐ **权威。** 每条注册判据恰好一行。
    rules: list[RuleLine] = Field(default_factory=list)

    #: 记账键的出口。**空也要报。**
    accounting: list[AccountingLine] = Field(default_factory=list)

    retired: list[RetiredLine] = Field(default_factory=list)

    badge: Badge
    passed: bool
    counts: StatusCounts

    @model_validator(mode='after')
    def _postconditions(self) -> CheckReport:
        """三条。**服务端算的那几个数不许与 `rules` 分家**（`R-11.7`）。"""
        dup = sorted({r.rule for r in self.rules
                      if sum(1 for x in self.rules if x.rule == r.rule) > 1})
        if dup:
            raise ValueError(
                f'同一条判据在报告里出现了不止一行：{dup}。'
                f'「一条注册判据恰好一行」是本结构的全部意义'
            )
        expect_badge = badge_of(self.rules)
        if self.badge != expect_badge:
            raise ValueError(
                f'badge={self.badge!r} 与逐行算出来的 {expect_badge!r} 对不上。'
                f'徽章只有一处算（`R-11.7`），第二处必然分家'
            )
        if self.passed != (self.badge != 'fail'):
            raise ValueError(f'passed={self.passed} 与 badge={self.badge!r} 对不上')
        expect_counts = StatusCounts.of(self.rules)
        if self.counts != expect_counts:
            raise ValueError(
                f'counts={self.counts!r} 与逐行数出来的 {expect_counts!r} 对不上'
            )
        return self
