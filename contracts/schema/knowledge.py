# -*- coding: utf-8 -*-
"""人物拆分后的必要知识契约；不含可写的静态人物副本。

KnowledgeGrounding 是 global_grounding.json v6 的唯一写入载体。引用绑定的是
知识请求实际看过的人物投影；称谓使用者、语境、时点及角色证据不在该请求内。
"""
from __future__ import annotations

from typing import Generic, Literal, TypeVar

from pydantic import Field, field_validator, model_validator

from contracts.schema.agent_wire import keyed_rows_to_mapping
from contracts.schema.base import StrictModel
from contracts.schema.characters import CharacterId
from contracts.schema.common import Sha256
from contracts.schema.stage2 import (
    Canon, CanonCandidateResponse, EvidenceCandidateRef, EvidenceLocator,
    FactEvidence, GlobalGrounding, GlobalProvenance, Relation, RelationCandidateResponse,
    SourceBlurbGrounding,
)


EvidenceT = TypeVar('EvidenceT', FactEvidence, EvidenceCandidateRef, EvidenceLocator)


class CharacterKnowledgeReference(StrictModel):
    canonical: str = Field(min_length=1)
    surfaces: list[str] = Field(min_length=1)
    role: str = Field(min_length=1)


class NecessaryCharacterState(StrictModel, Generic[EvidenceT]):
    # 仅保存源文有据且影响人物动机、后续因果或交付披露的动态事实。
    # 未列入状态表不等于“该人物没有未来”，只是不为其新造动态知识。
    fate: str = Field(min_length=1)
    fate_evidence: EvidenceT


class NecessaryCharacterStateRow(NecessaryCharacterState[EvidenceCandidateRef]):
    char_id: CharacterId


class KnowledgeEntities(StrictModel):
    character_states: dict[CharacterId, NecessaryCharacterState[FactEvidence]]
    relations: list[Relation]

    @model_validator(mode='after')
    def _unique_relations(self) -> KnowledgeEntities:
        ids = [row.relation_id for row in self.relations]
        if len(ids) != len(set(ids)):
            raise ValueError('必要知识的 relation_id 不可重复')
        return self


class KnowledgeGrounding(StrictModel):
    characters_reference_sha256: Sha256
    entities: KnowledgeEntities
    canon: Canon
    source_blurb: SourceBlurbGrounding[FactEvidence]
    provenance: GlobalProvenance = Field(alias='_provenance')


# 领域视图只保留一个模型身份；此别名不是旧产物适配器，两个名称均未登记写口。
GlobalGroundingView = GlobalGrounding


class KnowledgeEntitiesResponse(StrictModel):
    character_states: dict[CharacterId, NecessaryCharacterState[EvidenceCandidateRef]]
    relations: list[RelationCandidateResponse]

    @field_validator(
        'character_states', mode='before',
        json_schema_input_type=list[NecessaryCharacterStateRow],
    )
    @classmethod
    def _states_from_rows(cls, value: object) -> object:
        return keyed_rows_to_mapping(
            value, row_model=NecessaryCharacterStateRow, key_field='char_id')

    @model_validator(mode='after')
    def _unique_relations(self) -> KnowledgeEntitiesResponse:
        ids = [row.relation_id for row in self.relations]
        if len(ids) != len(set(ids)):
            raise ValueError('必要知识响应的 relation_id 不可重复')
        return self


class KnowledgeResponse(StrictModel):
    # 人物引用由代码冻结，不由模型抄回一个声称相同的 SHA。
    entities: KnowledgeEntitiesResponse
    canon: CanonCandidateResponse
    source_blurb: SourceBlurbGrounding[EvidenceCandidateRef]


class KnowledgeFullTurn(StrictModel):
    kind: Literal['full_candidate']
    candidate: KnowledgeResponse


class KnowledgeAgentTurnResponse(StrictModel):
    """知识只修改自身完整候选；静态 role/fate 重划分已归人物派修链。"""

    payload: KnowledgeFullTurn
