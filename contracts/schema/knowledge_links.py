"""必要知识 V2：只引用已有剧情/状态/Global 主体，不保存第二份剧情正文。"""
from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from contracts.schema.base import FrozenModel, StrictModel
from contracts.schema.common import FreeProvenance, Sha256
from contracts.schema.plot import PlotSubjectRef, BeatId
from contracts.schema.stage2 import KnowledgeSubject


def ref_keys(refs) -> set[tuple[str, str]]:
    return {(row.kind, row.ref) for row in refs}


class KnowledgeEvidenceRefs(FrozenModel):
    subject_refs: list[PlotSubjectRef] = Field(min_length=1)

    @model_validator(mode='after')
    def _unique(self) -> Self:
        if len(ref_keys(self.subject_refs)) != len(self.subject_refs):
            raise ValueError('知识支持主体不能重复')
        return self


class KnowledgeAtBeats(KnowledgeEvidenceRefs):
    kind: Literal['at_beats']
    trigger_beat_ref: BeatId

    @model_validator(mode='after')
    def _trigger_is_supported(self) -> Self:
        if ('beat', self.trigger_beat_ref) not in ref_keys(self.subject_refs):
            raise ValueError('知识触发节点必须属于本次支持主体')
        return self


class KnowledgeAtEntry(KnowledgeEvidenceRefs):
    kind: Literal['at_entry']


class KnowledgeLinkUnresolved(FrozenModel):
    kind: Literal['unresolved']
    reason: str = Field(min_length=1)


KnowledgeLinkResolution = Annotated[
    KnowledgeAtBeats | KnowledgeAtEntry | KnowledgeLinkUnresolved,
    Field(discriminator='kind'),
]


KnowledgeLinkKind = Literal[
    'secret_reveal', 'secret_setup', 'knowledge_event', 'world_rule', 'world_rule_reveal',
    'relation', 'relation_visibility', 'character_state', 'setting',
    'reader_reveal', 'character_knowledge',
]


class PlotKnowledgeEvidence(FrozenModel):
    chapter_ordinal: int = Field(ge=1)
    evidence_ref: str = Field(min_length=1)


class PlotKnowledgeEvent(FrozenModel):
    """仅声明必要的传播许可，命题正文继续属于引用的 Beat/State。"""
    kind: Literal['reader_reveal', 'character_knowledge']
    subject_ref: PlotSubjectRef
    audience: KnowledgeSubject | None
    chapter_ordinal: int = Field(ge=1)
    evidence_refs: list[PlotKnowledgeEvidence] = Field(min_length=1)

    @model_validator(mode='after')
    def _subject_and_audience(self) -> Self:
        if self.subject_ref.kind not in ('beat', 'state'):
            raise ValueError('稀疏剧情知识事件只引用既有 Beat/State；Global 使用既有知识通路')
        if (self.kind == 'character_knowledge') != (self.audience is not None):
            raise ValueError('人物传播必须有受众；读者披露的 audience 为 null')
        keys = [(row.chapter_ordinal, row.evidence_ref) for row in self.evidence_refs]
        if len(keys) != len(set(keys)):
            raise ValueError('知识事件证据不得重复')
        return self


EventId = Annotated[str, Field(pattern=r'^ke_[a-zA-Z0-9_-]{1,80}$')]


class PutPlotKnowledgeEvent(FrozenModel):
    operation: Literal['put']
    event_id: EventId
    expected_event_sha256: Sha256 | None
    event: PlotKnowledgeEvent
    resolution: Annotated[KnowledgeAtBeats | KnowledgeLinkUnresolved, Field(discriminator='kind')]


class RetirePlotKnowledgeEvent(FrozenModel):
    operation: Literal['retire']
    event_id: EventId
    expected_event_sha256: Sha256
    reason: str = Field(min_length=1)


PlotKnowledgeEventOperation = Annotated[
    PutPlotKnowledgeEvent | RetirePlotKnowledgeEvent, Field(discriminator='operation')]


class RegisteredPlotKnowledgeEvent(FrozenModel):
    event_id: EventId
    event_sha256: Sha256
    event: PlotKnowledgeEvent
    retired: bool
    reason: str | None

    @model_validator(mode='after')
    def _retirement(self) -> Self:
        if self.retired != bool(self.reason and self.reason.strip()):
            raise ValueError('退役事件必须保留明确理由；活动事件不保存退役理由')
        return self


class KnowledgeLinkObligation(FrozenModel):
    obligation_id: str = Field(min_length=1)
    kind: KnowledgeLinkKind
    knowledge_ref: str = Field(min_length=1)
    subject_ref: PlotSubjectRef
    audience: KnowledgeSubject | None = None
    chapter_ordinal: int = Field(ge=1)
    subject_sha256: Sha256
    candidate_subject_refs: list[PlotSubjectRef]
    entry_subject_refs: list[PlotSubjectRef]


class KnowledgeLinkDecision(FrozenModel):
    obligation_id: str = Field(min_length=1)
    expected_subject_sha256: Sha256
    resolution: KnowledgeLinkResolution


class KnowledgeLinkSubmission(StrictModel):
    scope_sha256: Sha256
    actor: str = Field(min_length=1, max_length=256)
    reason: str = Field(min_length=1, max_length=4096)
    decisions: list[KnowledgeLinkDecision]
    event_operations: list[PlotKnowledgeEventOperation] = Field(default_factory=list)

    @model_validator(mode='after')
    def _closed_submission(self) -> Self:
        if not self.actor.strip() or not self.reason.strip():
            raise ValueError('关联决定必须有明确 actor/reason')
        ids = [row.obligation_id for row in self.decisions]
        if len(ids) != len(set(ids)):
            raise ValueError('关联决定的事件不能重复')
        event_ids = [row.event_id for row in self.event_operations]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError('同轮不能重复修改或撤销同一知识事件')
        return self


class KnowledgeLinkScope(FrozenModel):
    contract: Literal['stage2-knowledge-links-v2'] = 'stage2-knowledge-links-v2'
    chapter_ordinal: int | None = None
    scope_sha256: Sha256
    # 文件 SHA 仅供本次读写竞态；继承依据各事件的真实消费摘要。
    main_text_sha256: Sha256
    knowledge_sha256: Sha256
    blueprint_sha256: Sha256
    obligations: list[KnowledgeLinkObligation]
    registered_events: list[RegisteredPlotKnowledgeEvent] = Field(default_factory=list)

    @model_validator(mode='after')
    def _event_catalog(self) -> Self:
        ids = [row.event_id for row in self.registered_events]
        if len(ids) != len(set(ids)):
            raise ValueError('知识登记身份不能重复')
        custom = {row.knowledge_ref: row for row in self.obligations
                  if row.kind in ('reader_reveal', 'character_knowledge')}
        expected = {row.event_id: row.event for row in self.registered_events
                    if not row.retired and (self.chapter_ordinal is None
                        or row.event.chapter_ordinal == self.chapter_ordinal)}
        if custom.keys() != expected.keys():
            raise ValueError('必要知识义务必须与本范围活动事件登记闭合')
        for ref, event in expected.items():
            row = custom[ref]
            if (row.kind, row.subject_ref, row.audience, row.chapter_ordinal) != (
                    event.kind, event.subject_ref, event.audience, event.chapter_ordinal):
                raise ValueError('知识义务不能改写事件主体、性质、受众或目标章')
        return self


class KnowledgeLinkRecord(KnowledgeLinkDecision):
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    source_decision_sha256: Sha256


class KnowledgeLinkHistory(FrozenModel):
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
    """只由 Blueprint owner 物化；普通呈现分场不修改本产物。"""
    scope: KnowledgeLinkScope
    links: list[KnowledgeLinkRecord]
    provenance: KnowledgeLinksProvenance = Field(alias='_provenance')

    @model_validator(mode='after')
    def _complete(self) -> Self:
        if self.scope.chapter_ordinal is not None:
            raise ValueError('正式知识关联必须覆盖全书，不能把章级决定当作整书闭合')
        obligations = {row.obligation_id: row for row in self.scope.obligations}
        records = {row.obligation_id: row for row in self.links}
        if (len(obligations) != len(self.scope.obligations)
                or len(records) != len(self.links) or records.keys() != obligations.keys()):
            raise ValueError('关联产物必须不重不漏覆盖当前必要事件')
        for key, record in records.items():
            subject = obligations[key]
            if record.expected_subject_sha256 != subject.subject_sha256:
                raise ValueError('关联决定必须绑定当前事件主体')
            if record.resolution.kind == 'unresolved':
                raise ValueError('未解决的关联不能成为正式知识消费授权')
            allowed = (subject.entry_subject_refs if record.resolution.kind == 'at_entry'
                       else subject.candidate_subject_refs)
            if not ref_keys(record.resolution.subject_refs) <= ref_keys(allowed):
                raise ValueError('关联不能引用当前事件未开放的主体或时点')
            if record.resolution.kind == 'at_beats' and not record.resolution.trigger_beat_ref.startswith(
                    f'b{subject.chapter_ordinal:02d}_'):
                raise ValueError('知识触发节点必须属于登记章')
            if (subject.kind in ('reader_reveal', 'character_knowledge')
                    and (subject.subject_ref.kind, subject.subject_ref.ref)
                    not in ref_keys(record.resolution.subject_refs)):
                raise ValueError('必要剧情许可必须引用其完整主体')
        return self
