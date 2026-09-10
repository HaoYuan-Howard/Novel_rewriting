# -*- coding: utf-8 -*-
"""必要知识与 Blueprint 事实的独立关联契约。

关联是 Blueprint Commander 的 typed 决定，不是抽取模型的补充输出。
人物知情、世界内成立、读者揭示和伏笔表现分别保存，不能互相推导。
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from contracts.schema.base import FrozenModel, StrictModel
from contracts.schema.common import FreeProvenance, Sha256
from contracts.schema.stage2 import FactEvidence


class KnowledgeEvidenceRefs(FrozenModel):
    fact_refs: list[str] = Field(min_length=1)

    @model_validator(mode='after')
    def _unique(self) -> KnowledgeEvidenceRefs:
        if len(set(self.fact_refs)) != len(self.fact_refs) or not all(self.fact_refs):
            raise ValueError('关联事实引用须非空且无重复')
        return self


class KnowledgeAtFacts(KnowledgeEvidenceRefs):
    """支持事实可有多条，触发时点只由明确选中的一条 land 事实决定。"""

    kind: Literal['at_facts']
    trigger_fact_ref: str = Field(min_length=1)

    @model_validator(mode='after')
    def _trigger_is_supported(self) -> KnowledgeAtFacts:
        if self.trigger_fact_ref not in self.fact_refs:
            raise ValueError('关联触发事实必须属于本次支持事实')
        return self


class KnowledgeAtEntry(KnowledgeEvidenceRefs):
    """世界内早已成立的状态；fact_refs 仍须指向能证明它的源事实。"""

    kind: Literal['at_entry']


class KnowledgeLinkUnresolved(FrozenModel):
    kind: Literal['unresolved']
    reason: str = Field(min_length=1)


KnowledgeLinkResolution = Annotated[
    KnowledgeAtFacts | KnowledgeAtEntry | KnowledgeLinkUnresolved,
    Field(discriminator='kind'),
]


class KnowledgeLinkObligation(FrozenModel):
    obligation_id: str = Field(min_length=1)
    kind: Literal['secret_reveal', 'secret_setup', 'knowledge_event',
                  'world_rule', 'world_rule_reveal', 'relation',
                  'relation_visibility', 'character_state', 'setting']
    knowledge_ref: str = Field(min_length=1)
    chapter_ordinal: int = Field(ge=1)
    subject_sha256: Sha256
    claim: str = Field(min_length=1)
    evidence: list[FactEvidence]
    # 所有候选都有业务主张与原文证据；出现多义候选会改变主体摘要。
    candidate_fact_refs: list[str]
    entry_fact_refs: list[str]


class KnowledgeLinkDecision(FrozenModel):
    obligation_id: str = Field(min_length=1)
    expected_subject_sha256: Sha256
    resolution: KnowledgeLinkResolution


class KnowledgeLinkSubmission(StrictModel):
    scope_sha256: Sha256
    actor: str = Field(min_length=1, max_length=256)
    reason: str = Field(min_length=1, max_length=4096)
    decisions: list[KnowledgeLinkDecision]

    @model_validator(mode='after')
    def _closed_submission(self) -> KnowledgeLinkSubmission:
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError('关联决定必须有明确 actor/reason')
        ids = [row.obligation_id for row in self.decisions]
        if len(ids) != len(set(ids)):
            raise ValueError('关联决定的义务不能重复')
        return self


class KnowledgeLinkScope(FrozenModel):
    contract: Literal['stage2-knowledge-links-v1'] = 'stage2-knowledge-links-v1'
    scope_sha256: Sha256
    # full SHA 用于本次读写竞态，逐项 subject 才决定哪些历史关联可继承。
    main_text_sha256: Sha256
    knowledge_sha256: Sha256
    blueprint_sha256: Sha256
    obligations: list[KnowledgeLinkObligation]


class KnowledgeLinkRecord(KnowledgeLinkDecision):
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    source_decision_sha256: Sha256


class KnowledgeLinkHistory(FrozenModel):
    """从已落盘 typed 审阅决定投影；不得把无裁决的建议模板塞入历史。"""

    decision_sha256: Sha256
    accepted: bool
    submission: KnowledgeLinkSubmission


class KnowledgeLinkReview(FrozenModel):
    scope: KnowledgeLinkScope
    inherited: list[KnowledgeLinkRecord]
    pending_obligation_ids: list[str]


class KnowledgeLinksProvenance(FreeProvenance):
    decision_sha256: Sha256


class KnowledgeLinks(StrictModel):
    """只由 stage2.blueprint 物化；不能回写 Global 或章抽取主体。"""

    scope: KnowledgeLinkScope
    links: list[KnowledgeLinkRecord]
    provenance: KnowledgeLinksProvenance = Field(alias='_provenance')

    @model_validator(mode='after')
    def _complete(self) -> KnowledgeLinks:
        obligations = {row.obligation_id: row for row in self.scope.obligations}
        records = {row.obligation_id: row for row in self.links}
        if (len(obligations) != len(self.scope.obligations)
                or len(records) != len(self.links) or records.keys() != obligations.keys()):
            raise ValueError('关联产物必须不重不漏覆盖当前义务')
        for key, record in records.items():
            subject = obligations[key]
            if record.expected_subject_sha256 != subject.subject_sha256:
                raise ValueError('关联决定必须绑定当前义务主体')
            if record.resolution.kind == 'unresolved':
                raise ValueError('未解决的关联不能成为正式知识消费授权')
            allowed = (subject.entry_fact_refs if record.resolution.kind == 'at_entry'
                       else subject.candidate_fact_refs)
            if not set(record.resolution.fact_refs) <= set(allowed):
                raise ValueError('关联不能引用当前义务未开放的事实或时点')
        return self
