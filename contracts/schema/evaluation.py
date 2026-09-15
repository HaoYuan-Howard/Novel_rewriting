#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""语义评测与 Stage2 全局复核的契约。

⚠⚠ **本仓没有自动放行门。** 这里曾有 `GroundTruthCase` / `GroundTruthManifest` /
`CapabilityMetrics` / `EvaluatorProfile` 与八类能力 `EVALUATOR_ABILITIES`，服务的是
「双人独立标注 + 第三方仲裁 + 三轮 A/B 校准 ⇒ 允许 judge 自动 PASS/FAIL」那条路。
它从未被标定过，那条分支一次都没走通过，现已整批下线（人读版见
`docs/schema/evaluation.md` 顶部那段「删了什么 · 怎么恢复」）。

A/B 双盲判官（`EvaluatorFingerprint` / `JudgeDecision` / `SemanticDecision` /
`Ability` / `SemanticVerdict`）已随 stage6.semantic 节点整批退役（2026-09-15）：
唯一消费者 novel/quality/evaluator.py 与判官产物 schema 同批删除，本仓不再有
语义合并决议。保留的是仍被 Stage2 事实引用消费的 `EvidenceRef`、Stage2 全局
复核与 trim 账。
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from contracts.invariants import (
    GLOBAL_REVIEW_GENERATION_DISPOSITIONS, GLOBAL_REVIEW_KINDS,
)
from contracts.schema.base import StrictModel
from contracts.schema.common import FreeProvenance


class EvidenceRef(StrictModel):
    """A locator, never a copied source excerpt."""

    artifact: str = Field(min_length=1)
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)

    @model_validator(mode='after')
    def _ordered_span(self) -> 'EvidenceRef':
        if (self.start is None) != (self.end is None):
            raise ValueError('evidence span must provide start and end together')
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError('evidence end must be greater than start')
        return self


GlobalReviewState = Literal['pending', 'PASS', 'FAIL', 'UNVER']


class GlobalReviewGenerationDisposition(StrictModel):
    """证据仍不足时，对写作消费面的显式、可审计处置。

    这不是把 ``UNVER`` 洗成 ``PASS``。当前唯一动作只允许下游确定性移除该
    item 所属的完整事实组；哪些组可安全移除由运行时闭集判断，Commander 不能
    在载荷里自报一个任意 group 来扩大权限。
    """

    action: Literal[GLOBAL_REVIEW_GENERATION_DISPOSITIONS]
    reason: str = Field(min_length=1)

    @model_validator(mode='after')
    def _reason_has_visible_text(self) -> 'GlobalReviewGenerationDisposition':
        if not self.reason.strip():
            raise ValueError('generation disposition reason must not be blank')
        return self


class GlobalReviewItem(StrictModel):
    item_id: str = Field(min_length=1)
    #: v3 同时覆盖独立人物登记与四类长期约束；取值及阻断效果由 invariants 唯一定义。
    #: alias_identity 只审称谓与实体映射，role 只审首次公开身份。
    kind: Literal[GLOBAL_REVIEW_KINDS]
    subject_path: str = Field(min_length=1)
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    state: GlobalReviewState
    resolution_required: bool = False
    evidence: list[EvidenceRef]
    actor: str | None = None
    decided_at: str | None = None
    reason: str | None = None
    generation_disposition: GlobalReviewGenerationDisposition | None = None

    @model_validator(mode='after')
    def _decision_has_audit_fields(self) -> 'GlobalReviewItem':
        if self.resolution_required and self.state == 'PASS':
            raise ValueError('缺必要材料的主体必须先由对应 owner typed 修订，不能直接 PASS')
        decided = self.state != 'pending'
        values = (self.actor, self.decided_at, self.reason)
        if decided and any(not value for value in values):
            raise ValueError('decided global-review item requires actor, decided_at, and reason')
        if not decided and any(value is not None for value in values):
            raise ValueError('pending global-review item cannot claim decision audit fields')
        if self.state != 'UNVER' and self.generation_disposition is not None:
            raise ValueError(
                'generation disposition 只属于 UNVER；PASS/FAIL/pending 不能携带')
        return self


class GlobalReview(StrictModel):
    source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    items: list[GlobalReviewItem]

    @model_validator(mode='after')
    def _unique_items(self) -> 'GlobalReview':
        ids = [item.item_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError('global review item_id must be unique')
        return self

    @property
    def all_pass(self) -> bool:
        """Row-only fact; gate approval additionally requires current grounding."""
        return bool(self.items) and all(item.state == 'PASS' for item in self.items)


class GlobalReviewDecision(StrictModel):
    item_id: str = Field(min_length=1)
    expected_subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    state: Literal['PASS', 'FAIL', 'UNVER']
    reason: str = Field(min_length=1)
    evidence: list[EvidenceRef]
    generation_disposition: GlobalReviewGenerationDisposition | None = None

    @model_validator(mode='after')
    def _disposition_belongs_to_unver(self) -> 'GlobalReviewDecision':
        if self.state != 'UNVER' and self.generation_disposition is not None:
            raise ValueError('只有 UNVER 决定可以携带 generation disposition')
        return self


class GlobalReviewBlockingScopeDecision(StrictModel):
    """对当前完整 blocking 面的一次显式默认裁决。

    逐项 ``subject_sha256`` 仍由 ``expected_blocking_scope_sha256`` 整体绑定，
    ``expected_workpack_sha256`` 再绑定执行者实际读的只读材料；
    ``decisions`` 只需列例外，服务端会把默认裁决展开回每一条持久 item。这样压缩的是
    Commander 重复输出，不是人审范围，也不会让 disclosure 被自动 PASS。

    模板默认 ``UNVER``：原样提交仍保持闸门关闭。审阅者只有在确实通读列出的全部章、
    核完当前 workpack 后，才把它显式改成 ``PASS``。
    """

    expected_source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    expected_blocking_scope_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    expected_workpack_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    expected_item_count: int = Field(ge=1)
    reviewed_chapter_ordinals: list[int] = Field(min_length=1)
    state: Literal['PASS', 'UNVER']
    reason: str = Field(min_length=1)
    evidence: list[EvidenceRef]

    @model_validator(mode='after')
    def _closed_audit_surface(self) -> 'GlobalReviewBlockingScopeDecision':
        if not self.reason.strip():
            raise ValueError('blocking scope reason must not be blank')
        chapters = self.reviewed_chapter_ordinals
        if chapters != sorted(set(chapters)) or any(chapter < 1 for chapter in chapters):
            raise ValueError('reviewed_chapter_ordinals 必须是去重升序的正整数全集')
        return self


class GlobalReviewDecisionSet(StrictModel):
    """A human-produced optimistic-lock decision file/API payload."""

    actor: str = Field(min_length=1)
    decisions: list[GlobalReviewDecision] = Field(default_factory=list)
    blocking_scope: GlobalReviewBlockingScopeDecision | None = None

    @model_validator(mode='after')
    def _exactly_once(self) -> 'GlobalReviewDecisionSet':
        if not self.actor.strip() or any(not row.reason.strip() for row in self.decisions):
            raise ValueError('global-review actor/reason must not be blank')
        if not self.decisions and self.blocking_scope is None:
            raise ValueError('global-review decision set 至少要有逐项决定或 blocking_scope')
        ids = [row.item_id for row in self.decisions]
        if len(ids) != len(set(ids)):
            raise ValueError('each global-review item may be decided at most once per request')
        return self


class TrimAudit(StrictModel):
    """One over-length chapter's durable fact: **nothing was trimmed**.

    ⚠⚠ 这里曾经有六个字段（`after_sha256` / `before_claims` / `after_claims` /
    `deterministic_passed` / `evaluator_fingerprint` / `semantic` / `adopted`）与一条
    `_adoption_is_fail_closed` 校验器，服务的是「同槽结构化候选 → 确定性对账 → 校准双盲
    → 采用」那条收缩臂。收缩臂随 evaluator profile 整臂下线后，那六格恒为
    「等于 before / 空 / True / None / None / False」——**六个恒定值不是六个状态**。

    v3 只留 before_sha256 与当时 reason。章号只从当前登记路径解析；before SHA
    决定 audit 是否仍代表当前正文，reason 保留产生当时的超限事实，不能用后改的
    任务书重写历史原因。人审 Resolution 保留自身章主体并引用 exact audit SHA，
    不再复制 audit 的 before SHA 或由章主体确定的正文路径。
    ⚠ **不要**加 `over_by_chars` / `target_hi`：`trim_resolution.verification_context`
    已经 0 成本现算这两个数，落盘等于给自己造第二个会漂的落点。
    """

    before_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    reason: str = Field(min_length=1)


class TrimResolution(StrictModel):
    """A hash-bound human decision to keep the already-retained pretrim text.

    ``actor`` is a self-reported audit label, not an authenticated identity.
    There is deliberately no action that can adopt a trim candidate — and now
    there is no automated one either: the calibrated gate that used to own
    adoption was removed together with the evaluator profile, so
    ``retain_pretrim`` is the **only** way an over-length chapter is ever
    closed (the other exit is rewriting it via ``stage5.write --apply-edit``,
    which produces a new chapter and therefore a new audit).
    """

    output_ordinal: int = Field(ge=1)
    action: Literal['retain_pretrim']
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    audit_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    provenance: FreeProvenance = Field(alias='_provenance')

    @model_validator(mode='after')
    def _audit_text_is_not_blank(self) -> 'TrimResolution':
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError('actor/reason must not be blank')
        return self
