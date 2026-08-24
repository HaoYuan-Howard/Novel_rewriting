#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""语义评测与 Stage2 全局复核的契约。

⚠⚠ **本仓没有自动放行门。** 这里曾有 `GroundTruthCase` / `GroundTruthManifest` /
`CapabilityMetrics` / `EvaluatorProfile` 与八类能力 `EVALUATOR_ABILITIES`，服务的是
「双人独立标注 + 第三方仲裁 + 三轮 A/B 校准 ⇒ 允许 judge 自动 PASS/FAIL」那条路。
它从未被标定过，那条分支一次都没走通过，现已整批下线（人读版见
`docs/schema/evaluation.md` 顶部那段「删了什么 · 怎么恢复」）。

留下来的是判官不是闸门：`JudgeDecision` 保留三态（那正是要留的证据），而合并的
`SemanticDecision.verdict` 在**类型上**收成 `Literal['UNVER']`。选类型不选校验器，是因为
本仓偏好让非法态不可表达而不是校验掉；一条永远不可能被触发的校验器会绿一辈子，
而没人知道它到底还在不在。
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.common import FreeProvenance


#: 现役唯一一类语义能力。这里曾是八元组（`event_order` / `verbatim_fidelity` /
#: `hard_number_fidelity` / `secret_timeline` / `world_rule_consistency` /
#: `trim_equivalence` / `style_transfer`），随标定装置一起下线——留 7 个没有生产者的取值，
#: 就是让退役的描述以现役身份被引用。
#:
#: ⚠ 字段本身不能删：`ability` 是产物里**唯一**说得出「这两名判官被问的是什么问题」的一格。
#: 单值 `Literal` 是**取值面**不是**入口**，本仓已有同形先例——`TrimResolution.action`。
Ability = Literal['reference_boundary']

#: 逐判官的三态。⚠ 它**不**服务合并决议：`SemanticDecision.verdict` 恒 `UNVER`。
SemanticVerdict = Literal['PASS', 'FAIL', 'UNVER']


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


class EvaluatorFingerprint(StrictModel):
    """这一次判官调用的确切请求面身份。

    ⚠ 曾有第五格 `dataset_sha256`。ground truth 没有了之后它只能是一个恒定哨兵，而一个
    哨兵**看起来像一个 dataset 身份、其实不是**——那正是「把不同状态压成同一个值」。
    """

    model: str = Field(min_length=1)
    prompt_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    schema_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    decoder_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class JudgeDecision(StrictModel):
    judge: str = Field(min_length=1)
    verdict: SemanticVerdict
    evidence: list[EvidenceRef]
    reason: str = Field(min_length=1)


class SemanticDecision(StrictModel):
    """两名盲评判官的合并决议。**恒是带证据的 `UNVER`。**

    ⚠ 曾有 `automated: bool` 与 `profile_id: str | None` 两格，以及三态 `verdict`。
    自动放行门下线后它们恒为 `False` / `None` / `UNVER`——恒定值不是状态。
    逐判官的 `judges[].verdict` 仍是三态，读的人要的正是那两个数。
    """

    ability: Ability
    subject_key: str = Field(min_length=1)
    verdict: Literal['UNVER']
    evidence: list[EvidenceRef]
    reason: str = Field(min_length=1)
    judges: list[JudgeDecision] = Field(min_length=2, max_length=2)


GlobalReviewState = Literal['pending', 'PASS', 'FAIL', 'UNVER']


class GlobalReviewItem(StrictModel):
    item_id: str = Field(min_length=1)
    kind: Literal[
        'world_rule_version', 'secret', 'reveal', 'known_by',
        'name_distinctiveness', 'address_context', 'world_rule_kind',
        'world_rule_set_complete', 'secret_set_complete',
    ]
    subject_path: str = Field(min_length=1)
    subject_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    state: GlobalReviewState
    evidence: list[EvidenceRef]
    actor: str | None = None
    decided_at: str | None = None
    reason: str | None = None

    @model_validator(mode='after')
    def _decision_has_audit_fields(self) -> 'GlobalReviewItem':
        decided = self.state != 'pending'
        values = (self.actor, self.decided_at, self.reason)
        if decided and any(not value for value in values):
            raise ValueError('decided global-review item requires actor, decided_at, and reason')
        if not decided and any(value is not None for value in values):
            raise ValueError('pending global-review item cannot claim decision audit fields')
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


class GlobalReviewDecisionSet(StrictModel):
    """A human-produced optimistic-lock decision file/API payload."""

    actor: str = Field(min_length=1)
    decisions: list[GlobalReviewDecision] = Field(min_length=1)

    @model_validator(mode='after')
    def _exactly_once(self) -> 'GlobalReviewDecisionSet':
        if not self.actor.strip() or any(not row.reason.strip() for row in self.decisions):
            raise ValueError('global-review actor/reason must not be blank')
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

    留下的三格各有不可替代的作用：`before_sha256` 是过期机制本体（`trim_resolution`
    靠「当前正文 sha == 它」判这条 audit 还代不代表当前稿），`output_ordinal`
    是唯一键，`reason` 是唯一携带「这一章为什么在这里」的人读格。
    ⚠ **不要**加 `over_by_chars` / `target_hi`：`trim_resolution.verification_context`
    已经 0 成本现算这两个数，落盘等于给自己造第二个会漂的落点。
    """

    output_ordinal: int = Field(ge=1)
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
    before_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    chapter_rel: str = Field(min_length=1)
    chapter_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    provenance: FreeProvenance = Field(alias='_provenance')

    @model_validator(mode='after')
    def _audit_text_is_not_blank(self) -> 'TrimResolution':
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError('actor/reason must not be blank')
        expected = f'stage5/ch{self.output_ordinal:02d}.json'
        if self.chapter_rel != expected:
            raise ValueError(
                f'chapter_rel 必须精确绑定 ordinal：期望 {expected}，实得 {self.chapter_rel}')
        if self.chapter_sha256 != self.before_sha256:
            raise ValueError('retain_pretrim 的 chapter_sha256 必须等于 audit.before_sha256')
        return self
