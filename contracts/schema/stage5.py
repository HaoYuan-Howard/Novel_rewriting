#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage5.py —— 对应 `docs/schema/stage5.md`。写作 + 全书验收。
**全链唯一产出「书」的层**，也是**唯一带 LLM 返工回路**的层。

```
stage5/<mode>/chNN.json          LLM 产，逐章一次调用 —— 唯一承载正文的产物
stage5/<mode>/chNN.INVALID.json  被落盘闸门拦下的问题稿（同一份章契约 + errs）
stage5/<mode>/_rounds/…          三种归档：上一轮 · 被提升掉的问题稿 · 被护栏驳回的自救
stage5/review_<n>.json           确定性验收报告（**0 成本，不调 LLM**）
stage5/semantic_<n>.json         书级语义审判（付费；claims → reveal 对齐）
stage5/wontfix.json              人的「这条我不修」
stage5/draft.md                  两条臂合成的全书拼稿（字节形态，不在本模块）
```

【五条贯穿本层的事实】

  ① **两条臂 + 两相，而 schema 只有一份。** 臂只差 `(system prompt, 给不给源章原文)`，
     产物形状逐字节相同；`mode` 同时是目录名。
     ⚠ **臂不是 tag**：`--tag _v2` 给**目录**加后缀做 A/B，两者曾被拼成同一个串传下去，
     于是 `dialogue_halved`（判 `mode == 'shallow'`）在每一条 tag 臂上**恒假**——
     闸门没有报错，它是**静默地不再存在**。

  ② **判据 vs 载体在本层几乎全部倒向判据**：拒收 = 整章响应作废 = **再花一次高推理调用**
     （本层最贵的一次重试），而判据拦下 = 落 `.INVALID` + 人手改 / 重判 = **0 成本**。
     ⇒ 47 条判据**一条都不该上移**；少数够得着的（`mode` / `from_source_ordinals` 长度恒 1）
     **本来就没有判据**，归载体是原样承认现状，不是收权。
     ⚠ **还有一处不对称**：stage4 那条「响应侧 `extra='forbid'` 当场挡回」的出路，
     **在本层的默认路径上不存在**——默认逐章调用走 `ask_text`，**响应是纯文本，没有 JSON 可校验**。

  ③ **7 条 HARD，盘上 31 章 0 命中；47 条判据里 29 条从未命中。**
     这不是「干净」，要连基线一起读：`secret_leak` 报 0，而 **21 条 secret 里 19 条
     「连它自己的揭示章都命中不了」**——那盏灯基本不会亮。
     **「见过它通过」不是证据，「见过它红一次」才是。**

  ④ **篇幅带只有一个数**：`DRAFT_BAND` / `BOOK_BAND` 都是 0.90~1.10，**没有第二层宽限**。
     实测代价可量：31 章里 **10 章（32%）落在 ±10% 之外**，两端极值 0.814 / 1.206
     ——旧口径下它们恰好卡在 0.81 / 1.21 的内侧、各差不到 1%，两条判据一次都没响。

  ⑤ **落盘前有一次确定性改写正文本身，而本层不为它记账。**
     `normalize_chapter` 跑在落盘之前，`text` 存的是**归一之后**的字节，
     模型原样吐出来的那一份**不在任何产物里**。
     ⇒ 判准是**「有没有判据依赖这次改写的结果」**——本层**没有** ⇒ 记账没有服务对象；
     而 stage6 **有两条** ⇒ 那一层留着 `mech`。**同一条判准的两个答案，不是不一致。**
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, RootModel, field_validator, model_validator

from contracts.invariants import DEPTH_MODES
from contracts.schema.base import Fp10, FrozenModel, StrictModel
from contracts.schema.common import (
    Finding, FreeProvenance, LlmProvenance, RoundFreeProvenance, RoundLlmProvenance,
)

Mode = Literal[DEPTH_MODES]


# ─────────────────────────────────────────────────────────────────────────────
# 一、`stage5/<mode>/chNN.json`
# ─────────────────────────────────────────────────────────────────────────────

class Chapter(StrictModel):
    """一章成稿。顶层 **10 个键**。

    **正式稿与 `.INVALID` 共用同一份章契约**——状态由**文件名**区分，不由字段贫富区分。

    ⚠ **前五个字段是 `writer_context` 的副本，看起来违反「能 0 成本现算的不落盘」。**
    它们**不是抄件，是冻结下来的任务书**：`writer_context` 可以被 `--context-only`
    0 成本原地重建并覆盖，而这一份成稿是**付过钱**的。
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

    #: **同时是目录名。** ⚠ 归载体是原样承认现状——本层没有任何判据在查它。
    mode: Mode

    #: 源章对应部分字数（计划算好的）。盘上 408~1963。
    source_chars: int = Field(ge=0)

    #: 本章各拍预算之和。盘上 380~1960（键恒在，可为 `null`）。
    #: ⚠ **兼任契约版次的结构标记**：旧契约成稿没有这一项，`_write_draft` 据此认出**混契约**的拼稿
    #: ——判别用结构特征，**不用时间戳**。
    budget_chars: int | None

    #: `len(text.strip())`，**含空白**。盘上 399~1967。
    #: ⚠ 与 stage3 `measurable.body.chars`（**去空白**）不是同一个口径——跨口径运算要先对齐。
    chars: int = Field(ge=0)

    #: 20 字窗口重合率，**已扣掉按契约必须逐字保留的部分**。
    #: `null` = 正文短于一个窗口，**那是「量不了」不是「重合 0」**。
    #:
    #: ⚠ **闭卷有上界 `DEEP_OVERLAP_MAX`(10%)，开卷无上界、只报分布**（`B-03`）。
    #: 必须按**逐章**口径读，不是书级均值：deep 逐章 0.0%~**2.4%**（中位 0.1%）·
    #: shallow 逐章 **0.9%**~96.8%（中位 49.8%）——书级均值会把闭卷的上界平掉，
    #: 也会藏起「开卷有低到 0.9% 的章」这件事（**它是上界问题不是分布问题**）。
    overlap_excl_verbatim: float | None

    #: 正文本身，**唯一承载内容的字段**，`draft.md` 由它拼。
    #: ⚠ 存的是**归一之后**的字节（事实 ⑤）。
    #: ⚠ **归一必须跑在收缩【之前】**，这是实测出来的：`normalize_paragraphs` 会把单换行
    #: 改成空行分段，**它往文本里加字符**，而 `budget_chars` 的口径含空白。
    #: 曾有一章归一前 1408 字（未触发收缩，正确），段落归一 +101 个换行 → 1509 字 →
    #: 被落盘闸门阻断——**它是被「修版式」这个动作推过线的**，而唯一能救它的收缩已经跑完
    #: 并正确地拒绝了。写手无从改，重跑也白搭。
    text: str = Field(min_length=1)

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
    #: ⚠ **非空 `errs` 的正式稿会一路进 `book.txt`，没有一行报告会说**——
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

    #: stage7 那一侧多记一格（旧契约成稿没有 `budget_chars`，它是混契约的判别特征）。
    has_budget_chars: bool | None = None


class JudgedDraft(FrozenModel):
    """「我判了哪一份稿」。键是**完整相对路径**（`stage5/deep/ch01.json`）。

    ⚠⚠ **存完整相对路径是有来历的**：成稿一律不走那条会记账的读路——
    它的 `input_key` 只取两级，`stage5/deep/ch01.json` 会被记成 `deep/ch01.json`，
    反查到一个**不存在的路径** ⇒ 指纹 `None` ⇒ 那条边**永远显示对得上**。
    **一个假的匹配比没有记录更坏。**

    ⚠ **本类 stage5 / stage6 / stage7 共用一份**（2026-08-11 用户裁决）：
    `mode` 已裁——它是**路径中间那一段**，从键现算，落盘就是第二个落点。
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
    consumed: DraftConsumed | None = None


class ReviewChapter(StrictModel):
    """逐章。长度 = 计划章数，**没有成稿的章也在**（带一条 `draft_missing`）。"""

    #: 以下四项取自 **ctx**（不取成稿）。
    output_ordinal: int = Field(ge=1)
    from_source_ordinals: list[int] = Field(min_length=1, max_length=1)
    budget_chars: int | None
    source_chars: int = Field(ge=0)

    #: 以下五项**无成稿的章没有**。
    mode: Mode | None = None
    mode_dir: str | None = None
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


# ─────────────────────────────────────────────────────────────────────────────
# 三、`stage5/semantic_<n>.json`
# ─────────────────────────────────────────────────────────────────────────────

class SemanticClaim(StrictModel):
    """模型从某章成稿抽出的一个可判真假的命题，以及正文中的逐字证据。"""

    claim_id: str = Field(min_length=1)
    assertion: str = Field(min_length=1)
    evidence: str = Field(min_length=1)

    @field_validator('claim_id', mode='before')
    @classmethod
    def _normalise_claim_id(cls, value):
        # DeepSeek 实跑会稳定地把章内序号写成 JSON number。claim_id 只是本次
        # 审判内部的引用键，不承载语义；在不放宽 evidence 逐字闸的前提下统一成字符串。
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        return value


class SemanticChapterClaims(StrictModel):
    output_ordinal: int = Field(ge=1)
    coverage: Literal['complete', 'uncertain']
    uncertainty_reason: str | None = None
    claims: list[SemanticClaim]

    @model_validator(mode='before')
    @classmethod
    def _normalise_uncertainty_reason_alias(cls, value):
        """收敛 DeepSeek 实跑出现的解释键；不确定原因的冲突值仍然拒绝。"""
        if not isinstance(value, dict):
            return value
        got = dict(value)
        alias_keys = ('coverage_reason', 'coverage_note', 'reason', 'reasoning')
        if got.get('coverage') == 'uncertain':
            present = {
                key: got[key] for key in ('uncertainty_reason', *alias_keys)
                if key in got and got[key] not in (None, '')
            }
            if len({str(item) for item in present.values()}) > 1:
                raise ValueError(f'不确定原因别名互相冲突：{list(present)}')
            if not got.get('uncertainty_reason') and present:
                got['uncertainty_reason'] = next(iter(present.values()))
        # complete 时这些是模型擅自附带的过程说明，不把它偷换成“不确定”；真正写在
        # uncertainty_reason 的矛盾仍交给 after validator 拒绝。
        for alias in alias_keys:
            got.pop(alias, None)
        return got

    @model_validator(mode='after')
    def _uncertainty_is_explicit(self) -> SemanticChapterClaims:
        if self.coverage == 'uncertain' and not self.uncertainty_reason:
            raise ValueError('coverage=uncertain 必须说明 uncertainty_reason')
        if self.coverage == 'complete' and self.uncertainty_reason:
            raise ValueError('coverage=complete 不应携带 uncertainty_reason')
        return self


class SemanticEvidenceRef(StrictModel):
    output_ordinal: int = Field(ge=1)
    claim_id: str = Field(min_length=1)

    @field_validator('claim_id', mode='before')
    @classmethod
    def _normalise_claim_id(cls, value):
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        return value


class SemanticSecretDecision(StrictModel):
    """第二相模型只负责语义判断；时间线字段由代码从 Stage 2/4 派生。"""

    secret_id: str = Field(min_length=1)
    status: Literal['leak', 'clear', 'unver']
    evidence_refs: list[SemanticEvidenceRef] = Field(default_factory=list)
    reason: str = Field(min_length=1)

    @field_validator('evidence_refs', mode='before')
    @classmethod
    def _normalise_refs(cls, value):
        if value is None:
            return []
        if not isinstance(value, list):
            return value
        out = []
        for item in value:
            if isinstance(item, str):
                match = re.fullmatch(r'\(\s*(\d+)\s*,\s*([^,()\s]+)\s*\)', item)
                if match is not None:
                    item = {
                        'output_ordinal': int(match.group(1)),
                        'claim_id': match.group(2),
                    }
            out.append(item)
        return out

    @model_validator(mode='after')
    def _status_shape(self) -> SemanticSecretDecision:
        if self.status == 'leak' and not self.evidence_refs:
            raise ValueError('status=leak 必须给 evidence_refs')
        if self.status != 'leak' and self.evidence_refs:
            raise ValueError(f'status={self.status} 不得携带 evidence_refs')
        return self


class SemanticSecretAdjudication(StrictModel):
    secret_id: str = Field(min_length=1)
    reveal_chapter: int = Field(ge=1)
    status: Literal['leak', 'clear', 'unver']
    evidence_refs: list[SemanticEvidenceRef]
    earliest_chapter: int | None = Field(default=None, ge=1)
    reason: str = Field(min_length=1)

    @model_validator(mode='after')
    def _status_shape(self) -> SemanticSecretAdjudication:
        if self.status == 'leak':
            if not self.evidence_refs or self.earliest_chapter is None:
                raise ValueError('status=leak 必须给 evidence_refs 与 earliest_chapter')
            if self.earliest_chapter >= self.reveal_chapter:
                raise ValueError('泄漏证据必须早于 reveal_chapter')
        else:
            if self.evidence_refs or self.earliest_chapter is not None:
                raise ValueError(
                    f'status={self.status} 不得携带命中证据或 earliest_chapter')
        return self


class SemanticClaimsResponse(StrictModel):
    """第一相模型响应：只看正文逐章抽命题，不能接触任何 secret。"""

    chapters: list[SemanticChapterClaims] = Field(min_length=1)


class SemanticAlignmentResponse(StrictModel):
    """第二相模型响应；确定性的 reveal/earliest 不让模型复述。"""

    secrets: list[SemanticSecretDecision] = Field(min_length=1)


class SemanticReview(StrictModel):
    """付费语义审判的可审计落盘形态。"""

    mechanical_review_seq: int = Field(ge=1)
    judged: dict[str, JudgedDraft]
    chapters: list[SemanticChapterClaims] = Field(min_length=1)
    secrets: list[SemanticSecretAdjudication] = Field(min_length=1)
    provenance: LlmProvenance = Field(alias='_provenance')


# ─────────────────────────────────────────────────────────────────────────────
# 四、`stage5/wontfix.json`
# ─────────────────────────────────────────────────────────────────────────────

class WontFixEntry(StrictModel):
    """人的「这条我不修」。**盘上零样本**（没有一本有 `wontfix.json`）。

    键与**收敛键同构**（`<章|book>|<rule>|<evidence_key>`）——
    只按 `rule` 记会把「换了个位置的同类问题」一并豁免掉，**那不是人做的决定**。

    三条约束：
      · **`why` 必填**——写不出理由的豁免，与「没看过」在盘上没有区别；
      · **不给不存在的结论发豁免**——最近一份 review 里没有那条 ⇒ 显式终态；
      · **豁免不删结论**——照常出现在报告与界面上，只是不进反哺、不占「待人工」，并单独计数。
    """

    output_ordinal: int | None
    rule: str = Field(min_length=1)
    evidence_key: str
    why: str = Field(min_length=1)
    detail: str
    review_seq: int = Field(ge=1)


class WontFix(RootModel[dict[str, WontFixEntry]]):
    """`stage5/wontfix.json` —— **顶层就是那张映射**（`{收敛键: 条目}`），没有包一层。"""

    @model_validator(mode='after')
    def _key_agrees_with_the_entry(self) -> WontFix:
        """键是收敛键 `<章|book>|<rule>|<evidence_key>` —— 它与条目里的字段必须一致。

        ⚠ 不一致时豁免会挂在另一条结论上，而**豁免不删结论**、报告照常打印它，
        于是「人豁免过」与「人没看过」在界面上同形。
        """
        for key, e in self.root.items():
            head = 'book' if e.output_ordinal is None else str(e.output_ordinal)
            want = f'{head}|{e.rule}|{e.evidence_key}'
            if key != want:
                raise ValueError(f'wontfix 键 {key!r} 与条目算出来的收敛键 {want!r} 对不上')
        return self
