# -*- coding: utf-8 -*-
"""Global v7：四类长期知识、共享证据与独立人物身份引用。

模型只交请求局部句柄和证据候选；代码编译正式 ID、来源与外键。人物身份、局部
状态、人物知情和文案不属于本载体。生效窗口与读者揭示时点始终分开。
"""
from __future__ import annotations

from typing import Annotated, Generic, Literal, TypeVar
from pydantic import Field, model_validator

from contracts.invariants import WORLD_RULE_KINDS
from contracts.schema.base import StrictModel
from contracts.schema.common import HumanApproval, LlmProvenance, Sha256
from contracts.schema.core_evidence import CoreEvidenceAnchor, CoreEvidenceCandidateRef, EvidenceId

Chapter = Annotated[int, Field(strict=True, ge=1)]
NonEmpty = Annotated[str, Field(min_length=1)]
RefT = TypeVar('RefT')


class CharacterKnowledgeReference(StrictModel):
    canonical: NonEmpty
    surfaces: list[NonEmpty] = Field(min_length=1)
    role: NonEmpty


class GlobalSetting(StrictModel, Generic[RefT]):
    era: NonEmpty | None
    social_context: NonEmpty | None
    era_evidence_refs: list[RefT]
    social_context_evidence_refs: list[RefT]

    @model_validator(mode='after')
    def _established(self):
        for field in ('era', 'social_context'):
            if bool(getattr(self, field + '_evidence_refs')) != (getattr(self, field) is not None):
                raise ValueError('背景未建立须为 null 且无证据；有值须有证据')
        return self


class GlobalTimeWindow(StrictModel):
    effective_from: Chapter
    effective_until: Chapter | None
    revealed_from: Chapter

    @model_validator(mode='after')
    def _ordered(self):
        if self.effective_until is not None and self.effective_until < self.effective_from:
            raise ValueError('effective_until 为含末章，不得早于 effective_from')
        return self


class GlobalRelation(GlobalTimeWindow, Generic[RefT]):
    relation_id: Annotated[str, Field(pattern=r'^rel_\d{2,}$')]
    subject_id: Annotated[str, Field(pattern=r'^char_\d{3,}$')]
    predicate: NonEmpty
    object_id: Annotated[str, Field(pattern=r'^char_\d{3,}$')]
    evidence_refs: list[RefT] = Field(min_length=1)


class GlobalWorldRuleVersion(GlobalTimeWindow, Generic[RefT]):
    version_id: Annotated[str, Field(pattern=r'^v\d{2,}$')]
    desc: NonEmpty
    evidence_refs: list[RefT] = Field(min_length=1)


class GlobalWorldRule(StrictModel, Generic[RefT]):
    rule_id: Annotated[str, Field(pattern=r'^wr_\d{2,}$')]
    kind: Literal[WORLD_RULE_KINDS]
    versions: list[GlobalWorldRuleVersion[RefT]] = Field(min_length=1)

    @model_validator(mode='after')
    def _unique_versions(self):
        values = [row.version_id for row in self.versions]
        if len(values) != len(set(values)):
            raise ValueError('同一规则的 version_id 不得重复')
        return self


class RelationFactRef(StrictModel):
    kind: Literal['relation']
    relation_id: Annotated[str, Field(pattern=r'^rel_\d{2,}$')]


class WorldRuleVersionFactRef(StrictModel):
    kind: Literal['world_rule_version']
    rule_id: Annotated[str, Field(pattern=r'^wr_\d{2,}$')]
    version_id: Annotated[str, Field(pattern=r'^v\d{2,}$')]


GlobalFactRef = Annotated[RelationFactRef | WorldRuleVersionFactRef, Field(discriminator='kind')]


class GlobalArcSecret(StrictModel, Generic[RefT]):
    secret_id: Annotated[str, Field(pattern=r'^secret_\d{2,}$')]
    fact: NonEmpty | None
    fact_ref: GlobalFactRef | None
    setup_chapters: list[Chapter]
    revealed_from: Chapter
    evidence_refs: list[RefT]

    @model_validator(mode='after')
    def _one_fact(self):
        if (self.fact is None) == (self.fact_ref is None):
            raise ValueError('arc secret 必须恰好选择 fact 或 fact_ref')
        if bool(self.evidence_refs) != (self.fact is not None):
            raise ValueError('独立秘密必须举证；引用秘密复用目标证据')
        if len(self.setup_chapters) != len(set(self.setup_chapters)):
            raise ValueError('setup_chapters 不得重复')
        if any(chapter > self.revealed_from for chapter in self.setup_chapters):
            raise ValueError('铺垫章不得晚于读者揭示章；同章先后由语义审阅裁决')
        return self


class GlobalCoreEditReceipt(HumanApproval):
    source_sha256: Sha256
    request_sha256: Sha256
    characters_business_sha256: Sha256


class GlobalProvenance(LlmProvenance):
    evidence_locator_failures: list[tuple[str, int, str, str]]
    core_edit: GlobalCoreEditReceipt | None = None
    retired_global_ids: list[Annotated[str, Field(pattern=(
        r'^(relation:rel_\d{2,}|rule:wr_\d{2,}|secret:secret_\d{2,}|'
        r'version:wr_\d{2,}:v\d{2,})$'))]] = Field(default_factory=list)


class KnowledgeCore(StrictModel):
    characters_reference_sha256: Sha256
    setting: GlobalSetting[EvidenceId]
    world_rules: list[GlobalWorldRule[EvidenceId]]
    relations: list[GlobalRelation[EvidenceId]]
    arc_secrets: list[GlobalArcSecret[EvidenceId]]
    evidence_anchors: dict[EvidenceId, CoreEvidenceAnchor]

    def identity_keys(self) -> set[str]:
        """持久主体身份；规则版本仅在所属正式规则内唯一。"""
        return {
            *(f'relation:{row.relation_id}' for row in self.relations),
            *(f'rule:{row.rule_id}' for row in self.world_rules),
            *(f'secret:{row.secret_id}' for row in self.arc_secrets),
            *(f'version:{rule.rule_id}:{row.version_id}'
              for rule in self.world_rules for row in rule.versions),
        }

    @model_validator(mode='after')
    def _closed_core(self):
        for label, values in (
            ('relation', [row.relation_id for row in self.relations]),
            ('world_rule', [row.rule_id for row in self.world_rules]),
            ('arc_secret', [row.secret_id for row in self.arc_secrets])):
            if len(values) != len(set(values)):
                raise ValueError(f'{label} 正式 ID 不得重复')
        relations = {row.relation_id: row for row in self.relations}
        versions = {(rule.rule_id, version.version_id): version
                    for rule in self.world_rules for version in rule.versions}
        for secret in self.arc_secrets:
            ref = secret.fact_ref
            if ref is None:
                continue
            target = (relations.get(ref.relation_id) if isinstance(ref, RelationFactRef)
                      else versions.get((ref.rule_id, ref.version_id)))
            if target is None:
                raise ValueError(f'{secret.secret_id} 的 fact_ref 悬空')
            if secret.revealed_from != target.revealed_from:
                raise ValueError('引用秘密与权威事实的 revealed_from 必须一致')
        return self


class KnowledgeGrounding(KnowledgeCore):
    provenance: GlobalProvenance = Field(alias='_provenance')

    @model_validator(mode='after')
    def _retired_ids_stay_retired(self):
        retired = self.provenance.retired_global_ids
        if len(retired) != len(set(retired)) or set(retired) & self.identity_keys():
            raise ValueError('已退休 Global ID 不得重复或重新成为当前主体')
        return self

class RelationDraft(GlobalTimeWindow):
    relation_ref: Annotated[str, Field(pattern=r'^r\d+$')]
    subject_ref: Annotated[str, Field(pattern=r'^c[1-9]\d*$')]
    predicate: NonEmpty
    object_ref: Annotated[str, Field(pattern=r'^c[1-9]\d*$')]
    evidence_refs: list[CoreEvidenceCandidateRef] = Field(min_length=1)


class WorldRuleVersionDraft(GlobalTimeWindow):
    version_ref: Annotated[str, Field(pattern=r'^v\d+$')]
    desc: NonEmpty
    evidence_refs: list[CoreEvidenceCandidateRef] = Field(min_length=1)


class WorldRuleDraft(StrictModel):
    rule_ref: Annotated[str, Field(pattern=r'^w\d+$')]
    kind: Literal[WORLD_RULE_KINDS]
    versions: list[WorldRuleVersionDraft] = Field(min_length=1)

    @model_validator(mode='after')
    def _unique_version_refs(self):
        refs = [row.version_ref for row in self.versions]
        if len(refs) != len(set(refs)):
            raise ValueError('同一规则的版本局部句柄不得重复')
        return self


class RelationFactDraftRef(StrictModel):
    kind: Literal['relation']
    relation_ref: Annotated[str, Field(pattern=r'^r\d+$')]


class WorldRuleVersionFactDraftRef(StrictModel):
    kind: Literal['world_rule_version']
    rule_ref: Annotated[str, Field(pattern=r'^w\d+$')]
    version_ref: Annotated[str, Field(pattern=r'^v\d+$')]


DraftFactRef = Annotated[RelationFactDraftRef | WorldRuleVersionFactDraftRef,
                         Field(discriminator='kind')]


class ArcSecretDraft(StrictModel):
    secret_ref: Annotated[str, Field(pattern=r'^s\d+$')]
    fact: NonEmpty | None
    fact_ref: DraftFactRef | None
    setup_chapters: list[Chapter]
    revealed_from: Chapter
    evidence_refs: list[CoreEvidenceCandidateRef]

    @model_validator(mode='after')
    def _fact_choice(self):
        if (self.fact is None) == (self.fact_ref is None):
            raise ValueError('arc secret draft 必须恰好选择 fact 或 fact_ref')
        if bool(self.evidence_refs) != (self.fact is not None):
            raise ValueError('独立秘密必须举证；fact_ref 复用目标证据')
        return self


class KnowledgeDraft(StrictModel):
    setting: GlobalSetting[CoreEvidenceCandidateRef]
    world_rules: list[WorldRuleDraft] = Field(min_length=0)
    relations: list[RelationDraft] = Field(min_length=0)
    arc_secrets: list[ArcSecretDraft] = Field(min_length=0)

    @model_validator(mode='after')
    def _unique_handles(self):
        for rows, field in ((self.relations, 'relation_ref'),
                            (self.world_rules, 'rule_ref'), (self.arc_secrets, 'secret_ref')):
            refs = [getattr(row, field) for row in rows]
            if len(refs) != len(set(refs)):
                raise ValueError(f'{field} 局部句柄不得重复')
        return self


KnowledgeResponse = KnowledgeDraft
GlobalGroundingView = KnowledgeGrounding


class KnowledgeFullTurn(StrictModel):
    kind: Literal['full_candidate']
    candidate: KnowledgeDraft


class KnowledgeAgentTurnResponse(StrictModel):
    payload: KnowledgeFullTurn
