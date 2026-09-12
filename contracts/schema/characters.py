"""Characters v2：实体身份和唯一称谓归属；不保存 mention 或披露图。"""
from __future__ import annotations

from typing import Annotated, Generic, Literal, TypeVar
from pydantic import Field, StringConstraints, field_validator, model_validator
from contracts.invariants import NAME_DISTINCTIVENESS
from contracts.schema.base import StrictModel
from contracts.schema.common import HumanApproval, LlmProvenance, Sha256
from contracts.schema.core_evidence import (
    CoreEvidenceAnchor, CoreEvidenceCandidateRef, CoreEvidenceEditRef, EvidenceId,
)

CharacterId = Annotated[str, StringConstraints(pattern=r'^char_\d{3,}$')]
LocalCharacterRef = Annotated[str, StringConstraints(pattern=r'^c[1-9]\d*$')]
EvidenceT = TypeVar('EvidenceT')


class CharacterNameBinding(StrictModel, Generic[EvidenceT]):
    surface: str = Field(min_length=1)
    identity_revealed_from: int = Field(ge=1, strict=True)
    evidence_refs: list[EvidenceT] = Field(min_length=1)


class RegisteredCharacter(StrictModel, Generic[EvidenceT]):
    canonical: str = Field(min_length=1)
    aliases: list[CharacterNameBinding[EvidenceT]] = Field(min_length=1)
    first_chapter: int = Field(ge=1, strict=True)
    first_appearance_evidence_refs: list[EvidenceT] = Field(min_length=1)
    role: str = Field(min_length=1)
    role_evidence_refs: list[EvidenceT] = Field(min_length=1)
    name_distinctiveness: Literal[NAME_DISTINCTIVENESS]

    @model_validator(mode='after')
    def _unique_names(self):
        names = [row.surface for row in self.aliases]
        if len(names) != len(set(names)):
            raise ValueError('同一实体的每个唯一称谓只能登记一次')
        if names.count(self.canonical) != 1:
            raise ValueError('aliases 必须包含 canonical surface 恰好一次')
        return self


class CharactersEditReceipt(HumanApproval):
    at: str = Field(min_length=1)
    source_sha256: Sha256
    edit_sha256: Sha256
    deleted_character_ids: list[CharacterId]


class CharactersProvenance(LlmProvenance):
    characters_edit: CharactersEditReceipt | None = None
    retired_character_ids: list[CharacterId] = Field(default_factory=list)


class CharacterRegistry(StrictModel):
    characters: dict[CharacterId, RegisteredCharacter[EvidenceId]] = Field(min_length=1)
    evidence_anchors: dict[EvidenceId, CoreEvidenceAnchor] = Field(min_length=1)
    provenance: CharactersProvenance = Field(alias='_provenance')

    @model_validator(mode='after')
    def _retired_ids_stay_retired(self):
        retired = self.provenance.retired_character_ids
        if len(retired) != len(set(retired)) or set(retired) & set(self.characters):
            raise ValueError('已退休人物 ID 不得重复或重新成为当前实体')
        return self


class CharacterCandidate(RegisteredCharacter[CoreEvidenceCandidateRef]):
    pass


class CharacterCandidateRow(CharacterCandidate):
    character_ref: LocalCharacterRef


class CharactersResponse(StrictModel):
    characters: list[CharacterCandidateRow] = Field(min_length=1)

    @model_validator(mode='after')
    def _unique_refs(self):
        refs = [row.character_ref for row in self.characters]
        if len(refs) != len(set(refs)):
            raise ValueError('人物候选局部引用不得重复')
        return self


class CharacterAdd(StrictModel):
    kind: Literal['add']
    char_id: CharacterId
    character: RegisteredCharacter[CoreEvidenceEditRef]


class CharacterReplace(StrictModel):
    kind: Literal['replace']
    char_id: CharacterId
    character: RegisteredCharacter[CoreEvidenceEditRef]


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


CharacterOperation = Annotated[CharacterAdd | CharacterReplace | CharacterDelete,
                               Field(discriminator='kind')]


class CharactersEdit(StrictModel):
    source_sha256: Sha256
    operations: list[CharacterOperation] = Field(min_length=1)

    @model_validator(mode='after')
    def _unique_targets(self):
        ids = [row.char_id for row in self.operations]
        if len(ids) != len(set(ids)):
            raise ValueError('同一人物不能在一次编辑中重复操作')
        return self
