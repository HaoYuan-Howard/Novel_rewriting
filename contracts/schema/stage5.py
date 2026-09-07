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
```
⚠ 这张图上曾列着 `stage5/semantic_<n>.json`（书级语义审判）。`stage5.semantic`
已于 2026-08-17 **整节点下线**（裁决与实跑证据见 `docs/DECISIONS.md`），
产物、路径登记与两条判据一并删除——本层现在**没有任何付费的语义审判**。

【五条贯穿本层的事实】

  ① **单臂 + 两相。** 写作一律闭卷；付费写作与零成本验收共用同一份章契约。

  ② **判据 vs 载体在本层几乎全部倒向判据**：拒收 = 整章响应作废 = **再花一次高推理调用**
     （本层最贵的一次重试），而判据拦下 = 落 `.INVALID` + 人手改 / 重判 = **0 成本**。
     ⇒ 现役 50 条判据**一条都不该上移**；少数够得着的（`from_source_ordinals` 长度恒 1）
     **本来就没有判据**，归载体是原样承认现状，不是收权。
     Stage5 的 `ask_json` 只在响应侧校验 segment 形状与 slot 闭合；保真等内容判据仍在
     落盘前完整执行，失败落 `.INVALID`，不把一次可恢复的付费正文当场丢掉。

  ③ **现役 50 条：7 hard、26 info、12 metric、5 unver。**
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

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from contracts.invariants import STAGE5_REVIEWER_REWRITE_DIRECTIVE_CODES
from contracts.schema.base import Fp10, FrozenModel, StrictModel
from contracts.schema.common import (
    Finding, FreeProvenance, HashBoundHumanApproval, LlmProvenance,
    RoundFreeProvenance, RoundLlmProvenance, Sha256,
)
from contracts.schema.orchestration import AgentProfile

# ─────────────────────────────────────────────────────────────────────────────
# 一、`stage5/chNN.json`
# ─────────────────────────────────────────────────────────────────────────────

class ChapterSegment(StrictModel):
    """Writer response 与正式章共用的唯一正文分段。"""

    slot: str = Field(pattern=r'^s\d{2,4}$')
    text: str = Field(min_length=1)


class ChapterResponse(StrictModel):
    """The only accepted Stage5 writer response shape (schema v2)."""

    segments: list[ChapterSegment] = Field(min_length=1)

    @model_validator(mode='after')
    def _unique_slots(self) -> 'ChapterResponse':
        slots = [segment.slot for segment in self.segments]
        if len(slots) != len(set(slots)):
            raise ValueError('writer response contains duplicate segment slots')
        return self

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

    #: **唯一内容权威**。整章正文由消费者按 producer slot 顺序拼出；
    #: schema v2 不再保存第二份可独立漂移的 `text`。
    segments: list[ChapterSegment] = Field(min_length=1)

    #: 提示级判定的**字符串投影**。盘上长度 4~9，共 187 条。
    #:
    #: ⚠ **恒非空**：5 条 metric（篇幅 / 段落数 / 重合度 / 破折号密度 / 连续短段）无条件产出
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
    #: 非空 `errs` 的正式稿会进入下一轮 Review，供人工修复 / wontfix 收口；
    #: `review.require_accepted_review` 会在审批、Stage6、Stage7 前拒绝当前 HARD，
    #: 不再允许它静默进入成书。盘上零样本。
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
# 一a、Commander 单章 formal reviewer rewrite request
# ─────────────────────────────────────────────────────────────────────────────

class ReviewerRewriteDirective(FrozenModel):
    """一条关闭式语义改写指令；正文证据必须逐字存在于 current 目标章。"""

    code: Literal[STAGE5_REVIEWER_REWRITE_DIRECTIVE_CODES]
    current_text_evidence: str = Field(min_length=1, max_length=240)

    @field_validator('current_text_evidence')
    @classmethod
    def _evidence_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('reviewer rewrite evidence 不能只含空白')
        return value


class ReviewerRewriteSubject(FrozenModel):
    """Commander 所见 current unresolved trim frontier 的一项双 SHA。"""

    output_ordinal: int = Field(ge=1)
    audit_sha256: Sha256
    chapter_sha256: Sha256


class ReviewerRewriteTarget(FrozenModel):
    """一批人审请求中由 Commander 选择的一个独立写作单元。"""

    target_ordinal: int = Field(ge=1)
    target_chapter_file_sha256: Sha256
    directives: tuple[ReviewerRewriteDirective, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def _unique_directives(self) -> 'ReviewerRewriteTarget':
        keys = [(row.code, row.current_text_evidence) for row in self.directives]
        if len(keys) != len(set(keys)):
            raise ValueError('reviewer rewrite directive code/evidence 不得重复')
        return self


class ReviewerRewriteTrustedInput(FrozenModel):
    """free reviewer action 写给 producer 的受信 envelope。

    前六项来自外部 typed 决定；末四项只能由已验证的 durable ReviewerActionRecord、
    logical session、seat turn 与 provider response 注入，不能让 HTTP 客户端自报。
    """

    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    review_sha256: Sha256
    unresolved_trim_items: tuple[ReviewerRewriteSubject, ...] = Field(min_length=1)
    target_ordinal: int = Field(ge=1)
    # ``ReviewerRewriteSubject.chapter_sha256`` 沿用 trim closure 的正文 SHA；
    # paid currentness 还必须另绑完整 Chapter JSON 原字节，不能混用两种口径。
    target_chapter_file_sha256: Sha256
    directives: tuple[ReviewerRewriteDirective, ...] = Field(min_length=1)
    reviewer_action_record_sha256: Sha256
    business_sha256: Sha256
    reviewer_contract_sha256: Sha256
    reviewer_session_run_id: str = Field(min_length=1, max_length=128)
    reviewer_seat_turn_id: str = Field(min_length=1, max_length=256)
    provider_response_sha256: Sha256

    @model_validator(mode='after')
    def _closed_input(self) -> 'ReviewerRewriteTrustedInput':
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError('reviewer rewrite actor/reason 不能只含空白')
        ordinals = [row.output_ordinal for row in self.unresolved_trim_items]
        if ordinals != sorted(set(ordinals)):
            raise ValueError('reviewer rewrite frontier 必须按章升序、去重')
        if self.target_ordinal not in ordinals:
            raise ValueError('reviewer rewrite target 必须属于完整 unresolved frontier')
        keys = [(row.code, row.current_text_evidence) for row in self.directives]
        if len(keys) != len(set(keys)):
            raise ValueError('reviewer rewrite directive code/evidence 不得重复')
        return self


class ReviewerRewriteBatchTrustedInput(FrozenModel):
    """同一 Commander 决定的完整批次运输 envelope。"""

    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    review_sha256: Sha256
    unresolved_trim_items: tuple[ReviewerRewriteSubject, ...] = Field(min_length=1)
    targets: tuple[ReviewerRewriteTarget, ...] = Field(min_length=1)
    reviewer_action_record_sha256: Sha256
    business_sha256: Sha256
    reviewer_contract_sha256: Sha256
    reviewer_session_run_id: str = Field(min_length=1, max_length=128)
    reviewer_seat_turn_id: str = Field(min_length=1, max_length=256)
    provider_response_sha256: Sha256

    @model_validator(mode='after')
    def _closed_batch(self) -> 'ReviewerRewriteBatchTrustedInput':
        ordinals = [row.target_ordinal for row in self.targets]
        if ordinals != sorted(set(ordinals)):
            raise ValueError('reviewer rewrite targets 必须按章升序、去重')
        for target in self.targets:
            self.for_target(target.target_ordinal)
        return self

    def for_target(self, ordinal: int) -> ReviewerRewriteTrustedInput:
        target = next((row for row in self.targets if row.target_ordinal == ordinal), None)
        if target is None:
            raise ValueError('reviewer rewrite 目标未被本批授权')
        return ReviewerRewriteTrustedInput.model_validate({
            **self.model_dump(mode='json', exclude={'targets'}),
            **target.model_dump(mode='json'),
        })


def reviewer_rewrite_request_sha256(payload: Mapping[str, object]) -> str:
    """正式 request 自摘要的契约层唯一算法。"""
    canonical = {
        key: value for key, value in dict(payload).items()
        if key != 'request_sha256'
    }
    try:
        raw = json.dumps(
            canonical, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False,
        ).encode('utf-8')
    except (TypeError, ValueError) as exc:
        raise ValueError(f'reviewer rewrite request 不能 canonical JSON 化：{exc}') from exc
    return hashlib.sha256(raw).hexdigest()


class ReviewerRewriteRequest(FrozenModel):
    """批次内独立自哈希的单章执行单元；不再独立登记为 canonical artifact。"""

    version: Literal[1] = 1
    kind: Literal['stage5-reviewer-rewrite-request-v1'] = \
        'stage5-reviewer-rewrite-request-v1'
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    review_rel: str = Field(pattern=r'^stage5/review_[1-9][0-9]*\.json$')
    review_sha256: Sha256
    unresolved_trim_items: tuple[ReviewerRewriteSubject, ...] = Field(min_length=1)
    target_ordinal: int = Field(ge=1)
    target_chapter_file_sha256: Sha256
    directives: tuple[ReviewerRewriteDirective, ...] = Field(min_length=1)
    reviewer_action_record_sha256: Sha256
    business_sha256: Sha256
    reviewer_contract_sha256: Sha256
    reviewer_session_run_id: str = Field(min_length=1, max_length=128)
    reviewer_seat_turn_id: str = Field(min_length=1, max_length=256)
    provider_response_sha256: Sha256
    request_sha256: Sha256

    @model_validator(mode='after')
    def _closed_request(self) -> 'ReviewerRewriteRequest':
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError('reviewer rewrite actor/reason 不能只含空白')
        ordinals = [row.output_ordinal for row in self.unresolved_trim_items]
        if ordinals != sorted(set(ordinals)):
            raise ValueError('reviewer rewrite frontier 必须按章升序、去重')
        if self.target_ordinal not in ordinals:
            raise ValueError('reviewer rewrite target 必须属于完整 unresolved frontier')
        keys = [(row.code, row.current_text_evidence) for row in self.directives]
        if len(keys) != len(set(keys)):
            raise ValueError('reviewer rewrite directive code/evidence 不得重复')
        payload = self.model_dump(mode='json')
        if self.request_sha256 != reviewer_rewrite_request_sha256(payload):
            raise ValueError('reviewer rewrite request SHA 不闭合')
        return self


class ReviewerRewriteBatchRequest(FrozenModel):
    """canonical 改写批次 v2：单章恢复身份独立，整批审阅授权一起封存。"""

    version: Literal[2] = 2
    kind: Literal['stage5-reviewer-rewrite-batch-v2'] = 'stage5-reviewer-rewrite-batch-v2'
    requests: tuple[ReviewerRewriteRequest, ...] = Field(min_length=1)
    request_sha256: Sha256

    @model_validator(mode='after')
    def _closed_batch(self) -> 'ReviewerRewriteBatchRequest':
        ordinals = [row.target_ordinal for row in self.requests]
        if ordinals != sorted(set(ordinals)):
            raise ValueError('reviewer rewrite batch 必须按章升序、去重')
        variable = {'target_ordinal', 'target_chapter_file_sha256', 'directives', 'request_sha256'}
        common = self.requests[0].model_dump(mode='json', exclude=variable)
        if any(row.model_dump(mode='json', exclude=variable) != common for row in self.requests):
            raise ValueError('reviewer rewrite batch 所有单元必须属于同一审阅授权与 frontier')
        if self.request_sha256 != reviewer_rewrite_request_sha256(self.model_dump(mode='json')):
            raise ValueError('reviewer rewrite batch SHA 不闭合')
        return self

    @property
    def target_ordinals(self) -> tuple[int, ...]:
        return tuple(row.target_ordinal for row in self.requests)

    def by_sha256(self, digest: str) -> ReviewerRewriteRequest:
        matches = [row for row in self.requests if row.request_sha256 == digest]
        if len(matches) != 1:
            raise ValueError('reviewer rewrite 单元 SHA 未唯一属于 current 批次')
        return matches[0]

    def trusted(self) -> ReviewerRewriteBatchTrustedInput:
        shared = self.requests[0].model_dump(mode='json', exclude={
            'version', 'kind', 'review_rel', 'target_ordinal',
            'target_chapter_file_sha256', 'directives', 'request_sha256'})
        return ReviewerRewriteBatchTrustedInput.model_validate({
            **shared,
            'targets': [{
                'target_ordinal': row.target_ordinal,
                'target_chapter_file_sha256': row.target_chapter_file_sha256,
                'directives': [item.model_dump(mode='json') for item in row.directives],
            } for row in self.requests],
        })


def reviewer_rewrite_execution_journal_sha256(payload: Mapping[str, object]) -> str:
    """append-only execution revision 自摘要的唯一算法。"""
    canonical = {
        key: value for key, value in dict(payload).items()
        if key != 'journal_sha256'
    }
    try:
        raw = json.dumps(
            canonical, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False,
        ).encode('utf-8')
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f'reviewer rewrite execution journal 不能 canonical JSON 化：{exc}') from exc
    return hashlib.sha256(raw).hexdigest()


class ReviewerRewriteExecutionJournal(FrozenModel):
    """一次 formal request 的 append-only paid/0-wire 执行状态 revision。"""

    version: Literal[1] = 1
    kind: Literal['stage5-reviewer-rewrite-execution-v1'] = \
        'stage5-reviewer-rewrite-execution-v1'
    sequence: int = Field(ge=1)
    recorded_at: str = Field(min_length=1)
    previous_journal_sha256: Sha256 | None
    # 不同 producer run 的 claim 也必须是一条单链。首个 claim 为 null；
    # reclaim 后的下一 claim 指向上一条 ``reclaimed_unstarted`` revision。
    claim_predecessor_journal_sha256: Sha256 | None = None
    sid: str = Field(min_length=1)
    node: Literal['stage5.write'] = 'stage5.write'
    run_id: str = Field(min_length=1, max_length=128)
    parent_run_id: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=1)
    output_ordinal: int = Field(ge=1)
    mode: Literal['reviewer_rewrite'] = 'reviewer_rewrite'
    purpose: str = Field(pattern=r'^stage5\.reviewer_rewrite·ch[1-9][0-9]*$')
    request_rel: str = Field(pattern=r'^stage5/reviewer_rewrite_request\.json$')
    request_file_sha256: Sha256
    request_sha256: Sha256
    input_sha256: Sha256
    event: Literal[
        'started', 'checkpointed', 'retry_started', 'delivery_prepared',
        'landed_formal', 'landed_invalid', 'landed_rejected',
        'reclaimed_unstarted',
    ]
    checkpoint_sha256: Sha256 | None
    commit_sha256: Sha256 | None
    outcome: Literal['formal', 'invalid', 'rejected'] | None
    landed_rel: str | None
    landed_file_sha256: Sha256 | None
    landed_payload_sha256: Sha256 | None
    reclaimed_by_run_id: str | None = Field(default=None, max_length=128)
    reclaimed_by_parent_run_id: str | None = Field(default=None, max_length=128)
    reclaim_evidence: dict[str, Any] | None = None
    reclaim_evidence_sha256: Sha256 | None = None
    journal_sha256: Sha256

    @model_validator(mode='after')
    def _closed_revision(self) -> 'ReviewerRewriteExecutionJournal':
        if (self.sequence == 1) != (self.previous_journal_sha256 is None):
            raise ValueError('reviewer rewrite execution 首 revision/previous SHA 不闭合')
        terminal = self.event.startswith('landed_')
        reclaimed = self.event == 'reclaimed_unstarted'
        if self.event == 'started':
            if any(value is not None for value in (
                    self.checkpoint_sha256, self.commit_sha256, self.outcome,
                    self.landed_rel, self.landed_file_sha256,
                    self.landed_payload_sha256,
                    self.reclaimed_by_run_id,
                    self.reclaimed_by_parent_run_id,
                    self.reclaim_evidence,
                    self.reclaim_evidence_sha256)):
                raise ValueError('reviewer rewrite started 不得预填后续事实')
        elif not reclaimed and self.checkpoint_sha256 is None:
            raise ValueError('reviewer rewrite started 之后必须绑定 accepted checkpoint')
        if reclaimed:
            if (self.checkpoint_sha256 is not None
                    or self.commit_sha256 is not None
                    or any(value is not None for value in (
                        self.outcome, self.landed_rel, self.landed_file_sha256,
                        self.landed_payload_sha256))
                    or not self.reclaimed_by_run_id
                    or not self.reclaimed_by_parent_run_id
                    or self.reclaim_evidence is None
                    or self.reclaim_evidence_sha256 is None):
                raise ValueError('reviewer rewrite reclaimed_unstarted 字段不闭合')
            if self.reclaim_evidence_sha256 != _reviewer_rewrite_payload_sha256(
                    self.reclaim_evidence):
                raise ValueError('reviewer rewrite reclaim evidence SHA 不闭合')
        elif any(value is not None for value in (
                self.reclaimed_by_run_id, self.reclaimed_by_parent_run_id,
                self.reclaim_evidence, self.reclaim_evidence_sha256)):
            raise ValueError('非 reclaimed reviewer rewrite 不得声明接管证据')
        if terminal:
            expected = {
                'landed_formal': 'formal',
                'landed_invalid': 'invalid',
                'landed_rejected': 'rejected',
            }[self.event]
            if (self.outcome != expected or not self.landed_rel
                    or self.landed_file_sha256 is None
                    or self.landed_payload_sha256 is None):
                raise ValueError('reviewer rewrite terminal 与落盘证据不闭合')
            if ((self.event == 'landed_formal') != (self.commit_sha256 is not None)):
                raise ValueError(
                    'reviewer rewrite formal terminal 必须绑定 commit；负终态不得冒充 commit')
        elif any(value is not None for value in (
                self.outcome, self.landed_rel, self.landed_file_sha256,
                self.landed_payload_sha256)):
            raise ValueError('reviewer rewrite 非 terminal 不得声明 landed')
        if self.event in {'checkpointed', 'retry_started'} \
                and self.commit_sha256 is not None:
            raise ValueError('reviewer rewrite checkpoint/retry 尚未产生 commit')
        payload = self.model_dump(mode='json')
        if self.journal_sha256 != reviewer_rewrite_execution_journal_sha256(payload):
            raise ValueError('reviewer rewrite execution journal SHA 不闭合')
        return self


ReviewerRewriteExecutionStatus = Literal[
    'ready_paid', 'in_flight_or_unknown', 'recoverable_checkpoint',
    'recoverable_prepared', 'landed_formal', 'landed_invalid',
    'landed_rejected', 'expired_unstarted', 'consumed_resolved',
]


class ReviewerRewriteExecutionState(FrozenModel):
    """供 queue 使用的关闭式九态投影；canonical request 不存在由 ``None`` 表示。"""

    request_rel: str
    request_file_sha256: Sha256
    request_sha256: Sha256
    target_ordinal: int = Field(ge=1)
    state: ReviewerRewriteExecutionStatus
    source_run_id: str | None
    checkpoint_sha256: Sha256 | None
    journal_sha256: Sha256 | None
    recovery_target_run_id: str | None = None
    recovery_journal_sha256: Sha256 | None = None
    # terminal artifact 仍在 canonical 路径 = 人工尚未处置；已进历史轮次 =
    # request 已消费且被合法后继 supersede。非 terminal 为 None。
    terminal_current: bool | None = None

    @model_validator(mode='after')
    def _terminal_axis_is_closed(self) -> 'ReviewerRewriteExecutionState':
        terminal = self.state.startswith('landed_') or self.state == 'consumed_resolved'
        if terminal != (self.terminal_current is not None):
            raise ValueError('reviewer rewrite execution terminal_current 轴不闭合')
        if self.state == 'consumed_resolved' and self.terminal_current is not False:
            raise ValueError('consumed_resolved 必须来自已归档 terminal 或 disposition')
        has_recovery = self.recovery_target_run_id is not None
        if has_recovery != (self.recovery_journal_sha256 is not None):
            raise ValueError('reviewer rewrite recovery target/journal 身份必须同有同无')
        if has_recovery != (self.state == 'recoverable_prepared'):
            raise ValueError('只有 recoverable_prepared 才能携带 target recovery WAL')
        return self


class ReviewerRewriteProbe(FrozenModel):
    """canonical request 与 queue-visible execution state 的一次同锁快照。"""

    request: ReviewerRewriteRequest
    execution: ReviewerRewriteExecutionState


def reviewer_rewrite_recovery_journal_sha256(
        payload: Mapping[str, object],
) -> str:
    """reviewer rewrite 自动 0-wire recovery WAL 的唯一自摘要算法。"""
    canonical = {
        key: value for key, value in dict(payload).items()
        if key != 'journal_sha256'
    }
    try:
        raw = json.dumps(
            canonical, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False,
        ).encode('utf-8')
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f'reviewer rewrite recovery journal 不能 canonical JSON 化：{exc}') from exc
    return hashlib.sha256(raw).hexdigest()


def _reviewer_rewrite_payload_sha256(payload: Mapping[str, object]) -> str:
    """WAL 内冻结业务 payload 的契约层 canonical 摘要。"""
    raw = json.dumps(
        dict(payload), ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False,
    ).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


class ReviewerRewriteRecoveryJournal(FrozenModel):
    """跨 run 自动采用 exact reviewer rewrite checkpoint 的两相 WAL。"""

    version: Literal[1] = 1
    kind: Literal['stage5-reviewer-rewrite-recovery-v1'] = \
        'stage5-reviewer-rewrite-recovery-v1'
    state: Literal['prepared', 'committed']
    prepared_at: str = Field(min_length=1)
    committed_at: str | None
    sid: str = Field(min_length=1)
    node: Literal['stage5.write'] = 'stage5.write'
    source_run_id: str = Field(min_length=1, max_length=128)
    target_run_id: str = Field(min_length=1, max_length=128)
    source_code: str = Field(min_length=1)
    target_code: str = Field(min_length=1)
    output_ordinal: int = Field(ge=1)
    request_rel: str = Field(pattern=r'^stage5/reviewer_rewrite_request\.json$')
    request_file_sha256: Sha256
    request_sha256: Sha256
    reviewer_action_record_sha256: Sha256
    business_sha256: Sha256
    reviewer_contract_sha256: Sha256
    input_sha256: Sha256
    source_run_identity_sha256: Sha256
    source_run_file_sha256: Sha256
    source_checkpoint_sha256: Sha256
    source_checkpoint_file_sha256: Sha256
    source_checkpoint_revision: int = Field(ge=1)
    source_commit_sha256: Sha256 | None
    source_commit_file_sha256: Sha256 | None
    source_attempt_id: str = Field(pattern=r'^[0-9a-f]{16}$')
    source_response_sha256: Sha256
    source_request_fingerprint: Sha256
    source_session_run_id: str = Field(min_length=1)
    source_provider_session_id: str = Field(min_length=1)
    source_adoption_id: Sha256
    delivery_outcome: Literal['formal', 'invalid', 'rejected']
    delivery_rel: str = Field(min_length=1)
    delivery_payload: dict[str, Any]
    delivery_payload_sha256: Sha256
    delivery_payload_fingerprint: str = Field(pattern=r'^[0-9a-f]{10}$')
    finalization_payload: dict[str, Any]
    finalization_sha256: Sha256
    touched_paths: tuple[str, ...] = Field(min_length=1)
    baselines: dict[str, Sha256 | None]
    landed_file_sha256: Sha256 | None
    post_file_sha256s: dict[str, Sha256 | None] | None
    journal_sha256: Sha256

    @model_validator(mode='after')
    def _closed_recovery(self) -> 'ReviewerRewriteRecoveryJournal':
        if self.source_run_id == self.target_run_id:
            raise ValueError('reviewer rewrite recovery source/target run 必须不同')
        if ((self.source_commit_sha256 is None)
                != (self.source_commit_file_sha256 is None)):
            raise ValueError('reviewer rewrite recovery source commit 双 SHA 必须同有同无')
        if (tuple(sorted(self.touched_paths)) != self.touched_paths
                or len(set(self.touched_paths)) != len(self.touched_paths)
                or sorted(self.baselines) != list(self.touched_paths)):
            raise ValueError('reviewer rewrite recovery touched/baselines 不闭合')
        expected_rel = {
            'formal': f'stage5/ch{self.output_ordinal:02d}.json',
            'invalid': f'stage5/ch{self.output_ordinal:02d}.INVALID.json',
            'rejected': f'stage5/ch{self.output_ordinal:02d}.rejected.json',
        }[self.delivery_outcome]
        if self.delivery_rel != expected_rel:
            raise ValueError('reviewer rewrite recovery outcome/delivery rel 不闭合')
        delivery_sha256 = _reviewer_rewrite_payload_sha256(
            self.delivery_payload)
        delivery_raw = json.dumps(
            self.delivery_payload, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False,
        ).encode('utf-8')
        if (self.delivery_payload_sha256 != delivery_sha256
                or self.delivery_payload_fingerprint
                != hashlib.sha1(delivery_raw).hexdigest()[:10]
                or self.finalization_sha256
                != _reviewer_rewrite_payload_sha256(
                    self.finalization_payload)):
            raise ValueError('reviewer rewrite recovery 冻结 payload/finalization 不闭合')
        if self.state == 'prepared':
            if (self.committed_at is not None
                    or self.landed_file_sha256 is not None
                    or self.post_file_sha256s is not None):
                raise ValueError('reviewer rewrite recovery prepared 字段不闭合')
        elif (not self.committed_at
              or self.landed_file_sha256 is None
              or self.post_file_sha256s is None
              or sorted(self.post_file_sha256s) != list(self.touched_paths)
              or self.post_file_sha256s.get(self.delivery_rel)
                 != self.landed_file_sha256):
            raise ValueError('reviewer rewrite recovery committed 字段不闭合')
        if self.journal_sha256 != reviewer_rewrite_recovery_journal_sha256(
                self.model_dump(mode='json')):
            raise ValueError('reviewer rewrite recovery journal SHA 不闭合')
        return self


def reviewer_rewrite_disposition_sha256(
        payload: Mapping[str, object],
) -> str:
    canonical = {
        key: value for key, value in dict(payload).items()
        if key != 'disposition_sha256'
    }
    try:
        raw = json.dumps(
            canonical, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False,
        ).encode('utf-8')
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f'reviewer rewrite disposition 不能 canonical JSON 化：{exc}') from exc
    return hashlib.sha256(raw).hexdigest()


class ReviewerRewriteDisposition(FrozenModel):
    """request 的单写 immutable retirement / typed successor receipt。"""

    version: Literal[1] = 1
    kind: Literal['stage5-reviewer-rewrite-disposition-v1'] = \
        'stage5-reviewer-rewrite-disposition-v1'
    disposition: Literal['expired_unstarted', 'consumed_resolved']
    recorded_at: str = Field(min_length=1)
    sid: str = Field(min_length=1)
    node: Literal['stage5.write'] = 'stage5.write'
    retire_run_id: str = Field(min_length=1, max_length=128)
    retire_parent_run_id: str = Field(min_length=1, max_length=128)
    retire_code: str = Field(min_length=1)
    request_rel: str = Field(pattern=r'^stage5/reviewer_rewrite_request\.json$')
    request_file_sha256: Sha256
    request_sha256: Sha256
    reviewer_action_record_sha256: Sha256
    target_ordinal: int = Field(ge=1)
    unstarted_evidence: dict[str, Any] | None = None
    unstarted_evidence_sha256: Sha256 | None = None
    terminal_event: Literal[
        'landed_formal', 'landed_invalid', 'landed_rejected'] | None = None
    terminal_rel: str | None = None
    terminal_file_sha256: Sha256 | None = None
    terminal_payload_sha256: Sha256 | None = None
    successor_kind: Literal['recheck', 'revalidate', 'apply_edit'] | None = None
    successor_rel: str | None = None
    successor_file_sha256: Sha256 | None = None
    successor_payload_sha256: Sha256 | None = None
    successor_evidence: dict[str, Any] | None = None
    successor_evidence_sha256: Sha256 | None = None
    disposition_sha256: Sha256

    @model_validator(mode='after')
    def _self_hash_is_closed(self) -> 'ReviewerRewriteDisposition':
        successor_fields = (
            self.terminal_event, self.terminal_rel,
            self.terminal_file_sha256, self.terminal_payload_sha256,
            self.successor_kind, self.successor_rel,
            self.successor_file_sha256, self.successor_payload_sha256,
            self.successor_evidence, self.successor_evidence_sha256,
        )
        if self.disposition == 'expired_unstarted':
            if (self.unstarted_evidence is None
                    or self.unstarted_evidence_sha256 is None
                    or any(value is not None for value in successor_fields)):
                raise ValueError('expired reviewer rewrite disposition 字段不闭合')
            if self.unstarted_evidence_sha256 != _reviewer_rewrite_payload_sha256(
                    self.unstarted_evidence):
                raise ValueError('reviewer rewrite disposition evidence SHA 不闭合')
        else:
            if (self.unstarted_evidence is not None
                    or self.unstarted_evidence_sha256 is not None
                    or any(value is None for value in successor_fields)):
                raise ValueError('consumed reviewer rewrite disposition 字段不闭合')
            assert self.successor_evidence is not None
            if (self.successor_evidence_sha256
                    != _reviewer_rewrite_payload_sha256(
                        self.successor_evidence)):
                raise ValueError('reviewer rewrite successor evidence SHA 不闭合')
        if self.disposition_sha256 != reviewer_rewrite_disposition_sha256(
                self.model_dump(mode='json')):
            raise ValueError('reviewer rewrite disposition SHA 不闭合')
        return self


def reviewer_rewrite_successor_journal_sha256(
        payload: Mapping[str, object],
) -> str:
    canonical = {
        key: value for key, value in dict(payload).items()
        if key != 'journal_sha256'
    }
    return _reviewer_rewrite_payload_sha256(canonical)


class ReviewerRewriteSuccessorJournal(FrozenModel):
    """terminal 之后 0-wire recheck/revalidate/apply-edit 的 request 专属 WAL。"""

    version: Literal[1] = 1
    kind: Literal['stage5-reviewer-rewrite-successor-v1'] = \
        'stage5-reviewer-rewrite-successor-v1'
    state: Literal['prepared', 'committed']
    prepared_at: str = Field(min_length=1)
    committed_at: str | None
    sid: str = Field(min_length=1)
    node: Literal['stage5.write'] = 'stage5.write'
    prepare_run_id: str = Field(min_length=1, max_length=128)
    prepare_parent_run_id: str = Field(min_length=1, max_length=128)
    prepare_code: str = Field(min_length=1)
    commit_run_id: str | None = Field(default=None, max_length=128)
    commit_parent_run_id: str | None = Field(default=None, max_length=128)
    commit_code: str | None = None
    request_rel: str = Field(pattern=r'^stage5/reviewer_rewrite_request\.json$')
    request_file_sha256: Sha256
    request_sha256: Sha256
    reviewer_action_record_sha256: Sha256
    target_ordinal: int = Field(ge=1)
    terminal_event: Literal[
        'landed_formal', 'landed_invalid', 'landed_rejected']
    terminal_rel: str = Field(min_length=1)
    terminal_file_sha256: Sha256
    terminal_payload: dict[str, Any]
    terminal_payload_sha256: Sha256
    successor_kind: Literal['recheck', 'revalidate', 'apply_edit']
    successor_rel: str = Field(pattern=r'^stage5/ch\d+\.json$')
    successor_file_sha256: Sha256
    successor_payload: dict[str, Any]
    successor_payload_sha256: Sha256
    action_evidence: dict[str, Any]
    action_evidence_sha256: Sha256
    touched_paths: tuple[str, ...] = Field(min_length=1)
    baselines: dict[str, Sha256 | None]
    post_file_sha256s: dict[str, Sha256 | None] | None
    journal_sha256: Sha256

    @model_validator(mode='after')
    def _closed_successor(self) -> 'ReviewerRewriteSuccessorJournal':
        terminal_rel = {
            'landed_formal': f'stage5/ch{self.target_ordinal:02d}.json',
            'landed_invalid': f'stage5/ch{self.target_ordinal:02d}.INVALID.json',
            'landed_rejected': f'stage5/ch{self.target_ordinal:02d}.rejected.json',
        }[self.terminal_event]
        if (self.terminal_rel != terminal_rel
                or self.successor_rel != f'stage5/ch{self.target_ordinal:02d}.json'):
            raise ValueError('reviewer rewrite successor terminal/target rel 不闭合')
        if (self.terminal_payload_sha256
                != _reviewer_rewrite_payload_sha256(self.terminal_payload)
                or self.successor_payload_sha256
                != _reviewer_rewrite_payload_sha256(self.successor_payload)
                or self.action_evidence_sha256
                != _reviewer_rewrite_payload_sha256(self.action_evidence)):
            raise ValueError('reviewer rewrite successor payload/evidence SHA 不闭合')
        if (tuple(sorted(self.touched_paths)) != self.touched_paths
                or len(set(self.touched_paths)) != len(self.touched_paths)
                or sorted(self.baselines) != list(self.touched_paths)):
            raise ValueError('reviewer rewrite successor touched/baselines 不闭合')
        commit_fields = (
            self.committed_at, self.commit_run_id,
            self.commit_parent_run_id, self.commit_code,
            self.post_file_sha256s,
        )
        if self.state == 'prepared':
            if any(value is not None for value in commit_fields):
                raise ValueError('prepared reviewer rewrite successor 不得预填 committed')
        elif (any(value is None for value in commit_fields)
              or self.post_file_sha256s is None
              or sorted(self.post_file_sha256s) != list(self.touched_paths)
              or self.post_file_sha256s.get(self.successor_rel)
                 != self.successor_file_sha256):
            raise ValueError('committed reviewer rewrite successor 字段不闭合')
        if self.journal_sha256 != reviewer_rewrite_successor_journal_sha256(
                self.model_dump(mode='json')):
            raise ValueError('reviewer rewrite successor journal SHA 不闭合')
        return self


# ─────────────────────────────────────────────────────────────────────────────
# 一a、Stage5 prepared checkpoint 的显式 0-wire 恢复合同（内部 sidecar）
# ─────────────────────────────────────────────────────────────────────────────

class PreparedRecoveryChapter(FrozenModel):
    """工作包里一章历史响应与当前重判面的完整 hash 绑定。"""

    output_ordinal: int = Field(ge=1)
    mode: Literal['write', 'revise']
    purpose: str = Field(min_length=1)
    source_code: str = Field(min_length=1)
    source_run_identity_sha256: Sha256
    source_run_file_sha256: Sha256
    source_commit_sha256: Sha256
    source_commit_file_sha256: Sha256
    source_checkpoint_sha256: Sha256
    source_checkpoint_file_sha256: Sha256
    source_checkpoint_revision: int = Field(ge=1)
    source_attempt_id: str = Field(pattern=r'^[0-9a-f]{16}$')
    source_response_sha256: Sha256
    source_request_fingerprint: Sha256
    source_session_run_id: str = Field(min_length=1)
    source_provider_session_id: str = Field(min_length=1)
    source_adoption_id: Sha256
    input_sha256: Sha256
    system_sha256: Sha256
    user_sha256: Sha256
    response_schema_sha256: Sha256
    response_model: str = Field(min_length=1)
    requested_output_token_ceiling: int = Field(gt=0)
    profile: AgentProfile
    candidate_sha256: Sha256
    touched_paths: tuple[str, ...] = Field(min_length=1)
    source_baselines: dict[str, Sha256 | None]
    current_baselines: dict[str, Sha256 | None]
    hard_rules: tuple[str, ...]
    regressions: tuple[str, ...]

    @model_validator(mode='after')
    def _closed_chapter_binding(self) -> 'PreparedRecoveryChapter':
        if self.mode != 'write':
            raise ValueError('prepared recovery 只接 initial write；revision source 关闭')
        if self.hard_rules or self.regressions:
            raise ValueError('prepared recovery 工作包只能收当前重判已通过的候选')
        if (tuple(sorted(self.touched_paths)) != self.touched_paths
                or len(set(self.touched_paths)) != len(self.touched_paths)):
            raise ValueError('prepared recovery touched_paths 必须去重并排序')
        if (sorted(self.source_baselines) != list(self.touched_paths)
                or sorted(self.current_baselines) != list(self.touched_paths)):
            raise ValueError('prepared recovery baseline 键必须恰好覆盖 touched_paths')
        return self


def prepared_recovery_workpack_subject(payload: Mapping[str, object]) -> str:
    """Stage5 prepared recovery 工作包 subject 的契约层唯一算法。"""
    canonical = {
        key: value for key, value in dict(payload).items()
        if key != 'subject_sha256'
    }
    try:
        raw = json.dumps(
            canonical, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False,
        ).encode('utf-8')
    except (TypeError, ValueError) as exc:
        raise ValueError(f'prepared recovery 工作包不能 canonical JSON 化：{exc}') from exc
    return hashlib.sha256(raw).hexdigest()


class PreparedRecoveryWorkpack(FrozenModel):
    """stdout 与 target manifest 共用的只读工作包。"""

    version: Literal[1] = 1
    kind: Literal['stage5-prepared-recovery-workpack-v1'] = \
        'stage5-prepared-recovery-workpack-v1'
    read_only: Literal[True] = True
    sid: str = Field(min_length=1)
    node: Literal['stage5.write'] = 'stage5.write'
    source_run_id: str = Field(
        min_length=1, max_length=128,
        pattern=r'^[a-z0-9][a-z0-9_-]{0,127}$')
    target_run_id: str = Field(
        min_length=1, max_length=128,
        pattern=r'^[a-z0-9][a-z0-9_-]{0,127}$')
    source_code: str = Field(min_length=1)
    target_code: str = Field(min_length=1)
    chapters: tuple[PreparedRecoveryChapter, ...] = Field(min_length=1)
    subject_sha256: Sha256

    @model_validator(mode='after')
    def _closed_scope(self) -> 'PreparedRecoveryWorkpack':
        if self.source_run_id == self.target_run_id:
            raise ValueError('prepared recovery 必须使用不同于 source 的新 target run')
        ordinals = [row.output_ordinal for row in self.chapters]
        if ordinals != sorted(set(ordinals)):
            raise ValueError('prepared recovery chapters 必须按章序去重排序')
        if {row.source_code for row in self.chapters} != {self.source_code}:
            raise ValueError('prepared recovery source code 必须全书一致')
        payload = self.model_dump(mode='json')
        if self.subject_sha256 != prepared_recovery_workpack_subject(payload):
            raise ValueError('prepared recovery workpack subject SHA 不闭合')
        return self


class PreparedRecoveryAction(HashBoundHumanApproval):
    """操作员对精确工作包的显式 apply 决定。"""

    source_run_id: str = Field(
        min_length=1, max_length=128,
        pattern=r'^[a-z0-9][a-z0-9_-]{0,127}$')
    target_run_id: str = Field(
        min_length=1, max_length=128,
        pattern=r'^[a-z0-9][a-z0-9_-]{0,127}$')


class PreparedRecoveryManifest(FrozenModel):
    """target run 的不可变整书恢复声明；先于任何逐章写盘落下。"""

    version: Literal[1] = 1
    kind: Literal['stage5-prepared-recovery-manifest-v1'] = \
        'stage5-prepared-recovery-manifest-v1'
    workpack: PreparedRecoveryWorkpack
    action: PreparedRecoveryAction
    manifest_sha256: Sha256

    @model_validator(mode='after')
    def _action_matches_workpack(self) -> 'PreparedRecoveryManifest':
        if (self.action.source_run_id != self.workpack.source_run_id
                or self.action.target_run_id != self.workpack.target_run_id
                or self.action.subject_sha256 != self.workpack.subject_sha256):
            raise ValueError('prepared recovery action 与 workpack 身份漂移')
        return self


class PreparedRecoveryJournal(FrozenModel):
    """一章 replacement 的 ``prepared -> committed`` 两相 journal。"""

    version: Literal[1] = 1
    kind: Literal['stage5-prepared-recovery-journal-v1'] = \
        'stage5-prepared-recovery-journal-v1'
    state: Literal['prepared', 'committed']
    prepared_at: str = Field(min_length=1)
    committed_at: str | None
    sid: str = Field(min_length=1)
    node: Literal['stage5.write'] = 'stage5.write'
    source_run_id: str = Field(min_length=1)
    target_run_id: str = Field(min_length=1)
    code: str = Field(min_length=1)
    source_code: str = Field(min_length=1)
    output_ordinal: int = Field(ge=1)
    subject_sha256: Sha256
    source_adoption_id: Sha256
    source_run_identity_sha256: Sha256
    source_run_file_sha256: Sha256
    source_commit_sha256: Sha256
    source_commit_file_sha256: Sha256
    source_checkpoint_sha256: Sha256
    source_checkpoint_file_sha256: Sha256
    source_attempt_id: str = Field(pattern=r'^[0-9a-f]{16}$')
    source_response_sha256: Sha256
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    primary_artifact: str = Field(min_length=1)
    primary_payload_sha256: Sha256
    primary_payload_fingerprint: str = Field(pattern=r'^[0-9a-f]{10}$')
    finalization_sha256: Sha256
    touched_paths: tuple[str, ...] = Field(min_length=1)
    baselines: dict[str, Sha256 | None]
    landed_payload_sha256: Sha256 | None
    post_file_sha256s: dict[str, Sha256 | None] | None
    journal_sha256: Sha256

    @field_validator('actor', 'reason')
    @classmethod
    def _human_fields_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('prepared recovery 人工凭据不能只含空白')
        return value

    @model_validator(mode='after')
    def _state_is_closed(self) -> 'PreparedRecoveryJournal':
        if (tuple(sorted(self.touched_paths)) != self.touched_paths
                or sorted(self.baselines) != list(self.touched_paths)):
            raise ValueError('prepared recovery journal touched/baselines 不闭合')
        if self.state == 'prepared':
            if (self.committed_at is not None
                    or self.landed_payload_sha256 is not None
                    or self.post_file_sha256s is not None):
                raise ValueError('prepared recovery journal prepared 字段不闭合')
        elif (not self.committed_at
              or self.landed_payload_sha256 is None
              or self.post_file_sha256s is None
              or sorted(self.post_file_sha256s) != list(self.touched_paths)):
            raise ValueError('prepared recovery journal committed 字段不闭合')
        return self


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

    #: 仅 formal reviewer rewrite 拒稿存在；普通 ``--revise`` 与历史 v1 拒稿缺席。
    #: request execution journal 以此证明负终态已消费，旧 canonical 章不变也不会再买席。
    reviewer_rewrite_request_sha256: Sha256 | None = None

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
    #: 等于不知道自己在说什么**。含一条 `_fact_landing`。
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
