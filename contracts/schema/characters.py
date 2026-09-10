# -*- coding: utf-8 -*-
"""Stage2 静态人物登记的独立载体；不持久化命运、关系或知识正文。

人物称谓由 address 承载，不能另建 aliases 副本。人物编辑只触及显式列出的
稳定 ID；删除不是审阅接受，相关审阅义务仍由 Global Commander 复核。
"""
from __future__ import annotations

from typing import Annotated, Generic, Literal, TypeVar

from pydantic import Field, StringConstraints, field_validator, model_validator

from contracts.invariants import NAME_DISTINCTIVENESS
from contracts.schema.agent_wire import keyed_rows_to_mapping
from contracts.schema.base import StrictModel
from contracts.schema.common import HumanApproval, LlmProvenance, Sha256
from contracts.schema.stage2 import (
    Address, AddressCandidateResponse, EvidenceCandidateRef, EvidenceLocator,
    FactEvidence,
)


CharacterId = Annotated[str, StringConstraints(pattern=r'^char_\d{3}$')]
EvidenceT = TypeVar('EvidenceT', FactEvidence, EvidenceLocator, EvidenceCandidateRef)


class RegisteredCharacter(StrictModel, Generic[EvidenceT]):
    canonical: str = Field(min_length=1)
    address: list[Address] = Field(min_length=1)
    name_distinctiveness: Literal[NAME_DISTINCTIVENESS]
    role: str = Field(min_length=1)
    role_evidence: EvidenceT


class CharactersEditReceipt(HumanApproval):
    at: str = Field(min_length=1)
    source_sha256: Sha256
    edit_sha256: Sha256
    # 删除目标保留在回执中，Global Commander 仍需确认删除对事实覆盖的影响。
    deleted_character_ids: list[CharacterId]


class CharactersProvenance(LlmProvenance):
    evidence_locator_failures: list[tuple[str, int, str, str]]
    characters_edit: CharactersEditReceipt | None = None


class CharacterRegistry(StrictModel):
    characters: dict[CharacterId, RegisteredCharacter[FactEvidence]] = Field(min_length=1)
    provenance: CharactersProvenance = Field(alias='_provenance')


class CharacterCandidate(StrictModel):
    canonical: str = Field(min_length=1)
    address: list[AddressCandidateResponse] = Field(min_length=1)
    name_distinctiveness: Literal[NAME_DISTINCTIVENESS]
    role: str = Field(min_length=1)
    role_evidence: EvidenceCandidateRef


class CharacterCandidateRow(CharacterCandidate):
    char_id: CharacterId


class CharactersResponse(StrictModel):
    characters: dict[CharacterId, CharacterCandidate] = Field(min_length=1)

    @field_validator(
        'characters', mode='before',
        json_schema_input_type=list[CharacterCandidateRow],
    )
    @classmethod
    def _characters_from_rows(cls, value: object) -> object:
        return keyed_rows_to_mapping(
            value, row_model=CharacterCandidateRow, key_field='char_id')


class CharacterAdd(StrictModel):
    kind: Literal['add']
    char_id: CharacterId
    character: RegisteredCharacter[EvidenceLocator]


class CharacterReplace(StrictModel):
    kind: Literal['replace']
    char_id: CharacterId
    character: RegisteredCharacter[EvidenceLocator]


class CharacterDelete(StrictModel):
    kind: Literal['delete']
    char_id: CharacterId
    reason: str = Field(min_length=1)

    @field_validator('reason')
    @classmethod
    def _visible_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('删除人物必须说明原因')
        return value


CharacterOperation = Annotated[
    CharacterAdd | CharacterReplace | CharacterDelete,
    Field(discriminator='kind'),
]


class CharactersEdit(StrictModel):
    # 正式文件或 INVALID 的精确字节身份；不能只绑定某个展示字段。
    source_sha256: Sha256
    operations: list[CharacterOperation] = Field(min_length=1)

    @model_validator(mode='after')
    def _unique_targets(self) -> CharactersEdit:
        ids = [row.char_id for row in self.operations]
        if len(ids) != len(set(ids)):
            raise ValueError('同一人物不能在一次编辑中重复操作')
        return self
