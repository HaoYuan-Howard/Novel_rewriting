#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage5.py —— 对应 `docs/schema/stage5.md`。写作 + 全书验收。
**全链唯一产出「书」的层**，也是**唯一带 LLM 返工回路**的层。

```
stage5/chNN.json                LLM 产，逐章一次调用 —— 唯一承载正文的产物
stage5/chNN.INVALID.json        被落盘闸门拦下的问题稿（同一份章契约 + errs）
stage5/_rounds/…                三种归档：上一轮 · 被提升掉的问题稿 · 被护栏驳回的自救
stage5/review_<n>.json           确定性验收报告（**0 成本，不调 LLM**）
stage5/wontfix.json              人的「这条我不修」
stage5/draft.md                  canonical 章产物的纯投影（字节形态，不在本模块）
```
⚠ 这张图上曾列着 `stage5/semantic_<n>.json`（书级语义审判）。`stage5.semantic`
已于 2026-08-17 **整节点下线**（裁决与实跑证据见 `docs/BACKEND_CONTRACT.md` §十），
产物、路径登记与两条判据一并删除——本层现在**没有任何付费的语义审判**。

【五条贯穿本层的事实】

  ① **单臂 + 两相。** 写作一律闭卷；付费写作与零成本验收共用同一份章契约。

  ② **判据 vs 载体在本层几乎全部倒向判据**：拒收 = 整章响应作废 = **再花一次高推理调用**
     （本层最贵的一次重试），而判据拦下 = 落 `.INVALID` + 人手改 / 重判 = **0 成本**。
     ⇒ 现役 49 条判据**一条都不该上移**；少数够得着的（`from_source_ordinals` 长度恒 1）
     **本来就没有判据**，归载体是原样承认现状，不是收权。
     Stage5 的 `ask_json` 只在响应侧校验 segment 形状与 slot 闭合；保真等内容判据仍在
     落盘前完整执行，失败落 `.INVALID`，不把一次可恢复的付费正文当场丢掉。

  ③ **现役 49 条：6 hard、27 info、11 metric、5 unver。**
     每条 HARD 都必须有最小反例走生产判据入口；新 canonical corpus 尚未跑完前，
     不拿旧双臂的命中数冒充当前质量基线。**「见过它通过」不是证据，
     「见过它红一次」才是。**

  ④ **逐章带与书级带是两个数，各自都没有第二层宽限**：`DRAFT_BAND` 0.80~1.20（逐章
     成稿 / 本章预算）、`BOOK_BAND` 0.90~1.10（全书成稿 / 全书源字数）。
     实测依据：31 章里 10 章（32%）落在 ±10% 之外，两端极值 **0.814 / 1.206**
     ——那是这一层的自然方差，不是写坏了，逐章带按它定在 0.80~1.20 把两端包住。
     书级带不跟着放宽：全书总量是产品承诺，且逐章的正负偏差在书级相互抵消
     （实测三本 0.949 / 1.022 / 0.980）。
     ⚠ 两个数不同**不是漂移**；把它们改回同一个数之前先看那两个极值。

  ⑤ **落盘前有一次确定性改写正文本身，而本层不为它记账。**
     `normalize_chapter` 跑在落盘之前，`text` 存的是**归一之后**的字节，
     模型原样吐出来的那一份**不在任何产物里**。
     ⇒ 判准是**「有没有判据依赖这次改写的结果」**——本层**没有** ⇒ 记账没有服务对象；
     而 stage6 **有两条** ⇒ 那一层留着 `mech`。**同一条判准的两个答案，不是不一致。**
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from contracts.schema.base import Fp10, FrozenModel, StrictModel
from contracts.schema.common import (
    Finding, FreeProvenance, LlmProvenance, RoundFreeProvenance, RoundLlmProvenance,
)
from contracts.schema.evaluation import SemanticDecision

# ─────────────────────────────────────────────────────────────────────────────
# 一、`stage5/chNN.json`
# ─────────────────────────────────────────────────────────────────────────────

class GeneratedSegment(StrictModel):
    """One writer-returned slot.  The producer, not the model, owns ordering."""

    slot: str = Field(pattern=r'^s\d{2,4}$')
    text: str = Field(min_length=1)


class ChapterResponse(StrictModel):
    """The only accepted Stage5 writer response shape (schema v2)."""

    segments: list[GeneratedSegment] = Field(min_length=1)

    @model_validator(mode='after')
    def _unique_slots(self) -> 'ChapterResponse':
        slots = [segment.slot for segment in self.segments]
        if len(slots) != len(set(slots)):
            raise ValueError('writer response contains duplicate segment slots')
        return self


class ChapterSegment(StrictModel):
    """The sole content authority inside a formal chapter artifact."""

    slot: str = Field(pattern=r'^s\d{2,4}$')
    text: str = Field(min_length=1)


class Chapter(StrictModel):
    """一章成稿。顶层 **10 个键**。

    **正式稿与 `.INVALID` 共用同一份章契约**——状态由**文件名**区分，不由字段贫富区分。

    ⚠ **前五个字段是 `writer_context` 的副本，看起来违反「能 0 成本现算的不落盘」。**
    它们**不是抄件，是冻结下来的任务书**：`writer_context` 可以被 `stage4.context`
    0 成本重建并覆盖，而这一份成稿是**付过钱**的。
    实测发生过一次：23 章成稿与盘上任何一版 ctx 都配不上（匹配 1/23），配对关系**永久丢失**
    ——那时留在成稿里的这几个数是唯一还说得出「写手当时被要求写多少字」的东西。
    ⇒ 与被裁掉的那些**不同类**：那些的源头（目录名、原文）不会被覆盖，这个会。

    ⚠ **已裁撤 3 个**：`op`（随 stage4）· `mech` · `dialogue_count`——
    **信息一条都没丢，只是换了落点**：
      · `mech` 的三个计数器 → **校验报告的一行**（`Q-1`：空也要报）；
        `mech.quote_problems` → **升成一条 INFO 判据 `quote_unbalanced`**。
        它根本不是记账，**它是一条结论**（「引号配对异常、未归一、留着交人工」）——
        把一条结论塞进记账块，就是它既没有出口、也没有守卫的原因。
      · `dialogue_count` → 可 0 成本现算，但**补一条无条件的度量 `dialogue_report`**：
        本层原本只有三条**告警**，「这本书的对白比是多少」**只有在漂了的时候才说得出来**。
    """

    #: 1..N。**文件名里的 NN 就是它。**
    output_ordinal: int = Field(ge=1)

    #: **长度恒为 1**（一章对一章）。透传自 ctx。
    from_source_ordinals: list[int] = Field(min_length=1, max_length=1)

    #: 源章对应部分字数（计划算好的）。盘上 408~1963。
    source_chars: int = Field(ge=0)

    #: 本章各拍预算之和。
    budget_chars: int = Field(gt=0)

    #: `len(text.strip())`，**含空白**。盘上 399~1967。
    #: ⚠ 与 stage3 `measurable.body.chars`（**去空白**）不是同一个口径——跨口径运算要先对齐。
    chars: int = Field(ge=0)

    #: 20 字窗口重合率，**已扣掉按契约必须逐字保留的部分**。
    #: `null` = 正文短于一个窗口，**那是「量不了」不是「重合 0」**。
    #:
    #: 闭卷的现役上界是 10%（`source_overlap_high`），必须按**逐章**口径读；
    #: 书级均值会把少数高重合章平掉。
    overlap_excl_verbatim: float | None

    #: **唯一内容权威**。整章正文与 `draft.md` 都由代码按 producer slot 顺序拼出；
    #: schema v2 不再保存第二份可独立漂移的 `text`。
    segments: list[ChapterSegment] = Field(min_length=1)

    #: 提示级判定的**字符串投影**。盘上长度 4~9，共 187 条。
    #:
    #: ⚠ **恒非空**：4 条 metric（篇幅 / 重合度 / 破折号密度 / 连续短段）无条件产出
    #: ⇒ **「有 warns」不携带任何信息**，只有读它是哪几条才有。
    warns: list[str]

    #: 阻断级判定。⚠ **一个键承担两种语义**：
    #:
    #: | 出现在 | 含义 | 盘上 |
    #: |---|---|---|
    #: | `chNN.INVALID.json` | **它为什么被拦下**（非空） | 2 份（都在 `_rounds/`） |
    #: | `chNN.json`（`--apply-edit` 落的） | **人工稿仍然违反了什么，但人决定照落**（可能为空） | 1 份，值是 `[]` |
    #: | `chNN.json`（其余） | 键不存在 | 28 份 |
    #:
    #: ⇒ 「状态只由文件名与 `errs` 区分」这句话**不再成立**：一份正式稿也可以带 `errs`，
    #: 甚至带非空的。今天靠 `_provenance.origin=human` 还分得开，**但那是第二个落点**。
    #: ⚠ **非空 `errs` 的正式稿会一路进成书，没有一行报告会说**——
    #: stage7 只排除 `.INVALID.json`，不看正式稿里的 `errs`。
    #: 盘上零样本。**这是裁决的后果，不是 bug——但要写进契约。**
    errs: list[str] | None = None

    #: ⚠ **全链唯一带轮次账的产物。** 两种形状：模型产的那一份（`RoundLlmProvenance`）·
    #: `--apply-edit` 落的人工稿（`RoundFreeProvenance`：**没有 `prompts`/`model`，
    #: 却仍带着 `round`/`revision_of`**，盘上 1/31）。
    #:
    #: ⚠⚠ **`--revalidate` 那条路伪造来源账，方向与 stage4 恰好相反**：stage4 是**丢掉**
    #: `_provenance`，本层是**盖上一份当前的**——一份上一轮付费产出的问题稿被提升为正式稿后，
    #: 来源账写着这一轮的 SYS 指纹 · `model` · 这一轮读到的上游指纹，`round` 还 +1，
    #: 而**没有任何一次新的落笔发生过**。
    #: 正确形状由 `LlmProvenance.promoted()` 在结构上保证（签名里没有 prompts/model/inputs）。
    #: ⚠ `--apply-edit` 在**同一个文件里、相隔 30 行**把这三件事全做对了——移植时连注释一起搬。
    #:
    #: ⚠ **已裁撤**：`state`（原 `'invalid'`，只在 `.INVALID` 上）——0 读者，
    #: 且**它恒等于文件名**。一个叫 `chNN.INVALID.json` 的文件里再写一句「我是 invalid」
    #: 就是第二落点。
    provenance: RoundLlmProvenance | RoundFreeProvenance = Field(
        alias='_provenance', union_mode='left_to_right')

    @model_validator(mode='after')
    def _segments_are_unique(self) -> 'Chapter':
        slots = [segment.slot for segment in self.segments]
        if len(slots) != len(set(slots)):
            raise ValueError('formal chapter contains duplicate segment slots')
        return self

    @property
    def text(self) -> str:
        """Deterministic whole-chapter projection; never serialized as a second authority."""
        return '\n\n'.join(segment.text.strip() for segment in self.segments).strip()


# ─────────────────────────────────────────────────────────────────────────────
# 一b、`stage5/chNN.rejected.json`
# ─────────────────────────────────────────────────────────────────────────────

class RejectedAttempt(StrictModel):
    """一次已完整返回、但相对上一轮成稿发生确定性退化的候选。"""

    chars: int = Field(ge=1)
    text: str = Field(min_length=1)


class RejectedRevision(StrictModel):
    """修订稿与唯一一次绕缓存重采样都退化时留下的人工证据。

    它不是正式成稿，也不是落盘闸门产生的 `.INVALID`：旧正式稿保持原位，
    本文件原样保存失败候选，避免一次付费自救把较好的旧稿覆盖掉。
    """

    output_ordinal: int = Field(ge=1)
    why: list[str] = Field(min_length=1)
    fixes: list[list[str]]
    attempts: list[RejectedAttempt] = Field(min_length=1, max_length=2)

    @field_validator('fixes')
    @classmethod
    def _fixes_are_rule_evidence_pairs(cls, value: list[list[str]]) -> list[list[str]]:
        for item in value:
            if len(item) != 2 or not item[0]:
                raise ValueError('fixes 每项必须是 [非空 rule, evidence_key]')
        return value


# ─────────────────────────────────────────────────────────────────────────────
# 二、`stage5/review_<n>.json`
# ─────────────────────────────────────────────────────────────────────────────

class ContextSourceDetail(StrictModel):
    """一条对不上的来源边。盘上全空（`state != 'stale'` 时为空）。"""

    key: str = Field(min_length=1)
    was: str | None
    now: str | None


class ContextSource(StrictModel):
    """`writer_context` 这一条上游的新鲜度。"""

    #: **三态**：没有 `inputs` 就是「不知道」，**不是「匹配」**。盘上 12/12 `fresh`
    #: ⇒ `stale` / `unknown` 两支**零样本**。
    state: Literal['fresh', 'stale', 'unknown']

    detail: list[ContextSourceDetail]
    code: str | None


class DraftSources(StrictModel):
    """成稿这一侧的来源账。

    ⚠ 这一半今天**只在非空时才报**，而 `Q-1` 要求**空也要报**。
    """

    #: 照的是另一份上游的成稿。盘上全空。
    stale: list[str]

    #: 没有来源账的成稿。盘上 3 份 review 里各 1 条。
    no_prov: list[str]


class ReviewSources(StrictModel):
    """**先报来源再报判定**（`Q-4`）。

    对不上时报出来的一堆违规是「拿新尺子量旧布」，不说破就会被当成一次回归。
    """

    writer_context: ContextSource
    drafts: DraftSources


class DraftConsumed(FrozenModel):
    """成稿的消费面投影。"""

    output_ordinal: int = Field(ge=1)

    #: **只管正文**——判「写手有没有再落笔」。
    text_sha1: Fp10

class JudgedDraft(FrozenModel):
    """「我判了哪一份稿」。键是**完整相对路径**（`stage5/ch01.json`）。

    canonical 路径只有两级，可以被 `read_judged` 和下游消费方无损地反查。
    """

    output_ordinal: int = Field(ge=1)

    #: 那份成稿当时是第几轮。**不可再生**（成稿被覆盖后就没了）。
    round: int = Field(ge=1)

    #: **整文件**指纹，**只作审计**。
    sha1: Fp10

    #: 本节点**真正消费**的投影。⚠ **它才是判过期的那把尺子**（`consumed` 判过期、
    #: 整文件 `sha1` 只作审计）——整文件指纹会因对方任何一次补 `_provenance` 或
    #: `--recheck` 而变，而那些改动**本节点一个字都没消费**。
    #: 盘上两个消费方各用一把尺子，**分歧零样本，但那只是还没发生**。
    consumed: DraftConsumed


class ReviewChapter(StrictModel):
    """逐章。长度 = 计划章数，**没有成稿的章也在**（带一条 `draft_missing`）。"""

    #: 以下四项取自 **ctx**（不取成稿）。
    output_ordinal: int = Field(ge=1)
    from_source_ordinals: list[int] = Field(min_length=1, max_length=1)
    budget_chars: int = Field(gt=0)
    source_chars: int = Field(ge=0)

    #: 以下六项**无成稿的章没有**。
    relpath: str | None = None
    draft_round: int | None = None

    #: **整份文件**指纹——管来源与过期。
    draft_sha1: Fp10 | None = None

    #: **只管正文**——判「写手有没有再落笔」。⚠ 盘上只有 10/124（后加的）。
    #:
    #: ⚠ **两把指纹各管一件事，这是被一次误判逼出来的**：来源账、`warns`、归一这类
    #: 0 成本元数据修复会改整份文件的指纹，但写手**一个字都没再写**——
    #: 拿整份指纹去推进收敛计数，等于**把一次重判记成「写手又试了一次」**。
    draft_text_sha1: Fp10 | None = None

    chars: int | None = None

    #: 盘上长度 4~11。
    findings: list[Finding]


class ReviewProvenance(FreeProvenance):
    """`review_<n>.json._provenance` —— **没有 `model`**（0 成本）。

    `prompts` 只有一项：`RULES`（**判据表自身的指纹**）。它是这份报告的**尺子版本**：
    判据表改了而报告没重跑，这个指纹是唯一说得出来的地方。
    ⚠ `inputs` 恒四条，**成稿不在里面**——它们记在 `judged`（理由见 `JudgedDraft`）。
    """


class Review(StrictModel):
    """`stage5/review_<n>.json` —— 顶层 **8 个键**。
    **0 成本、不调 LLM。判成品，不判计划。**

    `<n>` 由盘上已有的最大序号 +1。

    ⚠ **已裁撤 4 个**：`tag`（全仓 0 读者，盘上 12/12 恒 `''`）· `review_seq` ·
    `title` · `source_id`——**四个全在文件名或目录名里**，消费方都握着路径。
    ⚠ `source_id` 那条**不改不行**：它取自 ctx 的同名字段，而那个已随 stage4 裁撤
    ⇒ 不改就会**静默变 `null`**，报告抬头打印 `=== stage5·全书验收: 书名 (None) ===`。
    """

    sources: ReviewSources

    #: `{完整相对路径: 引用}` —— **判了哪几份稿。**
    judged: dict[str, JudgedDraft]

    chapters: list[ReviewChapter]

    #: 全书级结论。盘上长度 6~13。
    book: list[Finding]

    #: `{rule: 'k/N'}`，**每条 hard/info 判据在多少章上命中**。
    #: ⚠ 它是「一条判据有没有判别力」唯一的读数——**没有基线数字的判据，
    #: 等于不知道自己在说什么**。含一条 `_beat_event_landing`。
    baselines: dict[str, str]

    #: 同一把尺子量**源章原文**的命中率。**「判据把合法情况排除在外了吗」的可执行形式。**
    #:
    #: ⚠ **逐章豁免与书级闸门问的是两个不同的问题，都要**：
    #: 逐章（`identity_hit`）问「这一条被本章源文的同一现象混淆了吗」；
    #: 书级（`identity_dead`）问「这条判据在这本书上比它自己的基础发生率强吗」。
    #: 只做逐章会漏掉这种情形：源文 3/8 章命中、成稿也 3/8 章命中，**但是不同的章**——
    #: 逐章看每条都「干净」，而整体上这条判据什么也没测出来。
    #: ⚠ **两道闸都只降 `actionable`，不删结论**：「查过、但这一章原文自己也这样」
    #: 与「没查」必须分得开。
    baselines_identity: dict[str, str]

    #: `{hard, info, unver, metric}` 计数。
    #: ⚠ **稀疏**：盘上 12/12 份都没有 `hard` 这个键（一条都没有）——
    #: 「没有这个键」与「值是 0」在这里混掉了，所以四个键**都要能缺席**，
    #: 而消费方必须按 `.get(k, 0)` 之外的方式处理（报告现算，别读这个字段）。
    counts: dict[str, int]

    provenance: ReviewProvenance = Field(alias='_provenance')
