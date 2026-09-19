"""Characters v3：身份、lexicon 归属与出现绑定；无自由文本 role。

生产链：稀疏 Extract → 确定性字面回填 → lexicon Resolve → compiler 展开 occurrence。
派生字段（first_mention / name_usable / name_distinctiveness / book-level surfaces）
由 compiler 计算，不得由模型直填。

``CharactersResponse`` / ``CharacterCandidateRow`` 仅服务存量 analyze 草稿解码，
不是正式 CharacterRegistry 的生产 wire；新书人物唯一 writer 是 stage2.characters。
"""
from __future__ import annotations

from typing import Annotated, Generic, Literal, TypeVar
from pydantic import Field, StringConstraints, field_validator, model_validator

from contracts.invariants import NAME_DISTINCTIVENESS
from contracts.schema.base import StrictModel
from contracts.schema.common import HumanApproval, LlmProvenance, Sha256
from contracts.schema.core_evidence import (
    CoreEvidenceAnchor, CoreEvidenceCandidateRef, EvidenceId,
)

CharacterId = Annotated[str, StringConstraints(pattern=r'^char_\d{3,}$')]
LocalCharacterRef = Annotated[str, StringConstraints(pattern=r'^c[1-9]\d*$')]
MentionId = Annotated[str, StringConstraints(pattern=r'^m_[0-9a-f]{16}$')]

MentionKind = Literal[
    'proper_name',
    'short_name',
    'name_bearing_address',
    'relational_address',
    'social_address',
    'descriptive',
    'pronoun',
    'source_typo',
]

MentionDisposition = Literal['bound', 'excluded', 'unresolved']
CharacterIdentityStatus = Literal['confirmed', 'unresolved_surface']
CandidateSource = Literal['extract', 'literal_backfill']

BOOK_LEVEL_SURFACE_KINDS: frozenset[str] = frozenset({
    'proper_name', 'short_name', 'name_bearing_address', 'social_address', 'descriptive',
})
NAME_LICENSE_KINDS: frozenset[str] = frozenset({'proper_name', 'short_name'})


# ─── Extract / 候选 wire ─────────────────────────────────────────────────────


class ChapterSurfaceDiscover(StrictModel):
    chapter_ordinal: int = Field(ge=1, strict=True)
    surfaces: list[str] = Field(default_factory=list)


class BookExtractResponse(StrictModel):
    chapters: list[ChapterSurfaceDiscover] = Field(min_length=1)

    @model_validator(mode='after')
    def _unique_chapters(self):
        ordinals = [row.chapter_ordinal for row in self.chapters]
        if len(ordinals) != len(set(ordinals)):
            raise ValueError('BookExtractResponse 章号不得重复')
        return self


class MentionCandidate(StrictModel):
    mention_id: MentionId
    chapter_ordinal: int = Field(ge=1, strict=True)
    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)
    surface: str = Field(min_length=1)
    text_sha256: Sha256
    source: CandidateSource

    @model_validator(mode='after')
    def _span(self):
        if self.end <= self.start:
            raise ValueError('mention 区间必须非空且左闭右开')
        return self


# ─── Lexicon Resolve wire ────────────────────────────────────────────────────


class CharacterSeed(StrictModel):
    character_ref: LocalCharacterRef
    canonical_surface: str = Field(min_length=1)
    status: CharacterIdentityStatus = 'confirmed'


class SurfaceBindingScope(StrictModel):
    """将同 surface 限制到具体出现；缺省表示全书该 surface 一律适用。"""

    chapter_ordinal: int = Field(ge=1, strict=True)
    start: int | None = Field(default=None, ge=0, strict=True)
    end: int | None = Field(default=None, gt=0, strict=True)
    mention_id: MentionId | None = None

    @model_validator(mode='after')
    def _coords(self):
        if (self.start is None) ^ (self.end is None):
            raise ValueError('start/end 必须同时给出或同时省略')
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError('scope 区间必须非空')
        return self


class SurfaceBinding(StrictModel):
    """lexicon 级归属：一条 surface 规则，由 compiler 展开到字面出现。"""

    surface: str = Field(min_length=1)
    disposition: MentionDisposition
    kind: MentionKind
    character_ref: LocalCharacterRef | None = None
    identity_revealed_from: int | None = Field(default=None, ge=1, strict=True)
    exclude_reason: str | None = None
    scopes: list[SurfaceBindingScope] = Field(default_factory=list)

    @model_validator(mode='after')
    def _disposition_fields(self):
        if self.disposition == 'bound':
            if self.character_ref is None:
                raise ValueError('bound 必须指定 character_ref')
            if self.exclude_reason:
                raise ValueError('bound 不得带 exclude_reason')
        elif self.disposition == 'excluded':
            if self.character_ref is not None:
                raise ValueError('excluded 不得带 character_ref')
            if not (self.exclude_reason or '').strip():
                raise ValueError('excluded 必须说明 exclude_reason')
        else:
            if self.exclude_reason:
                raise ValueError('unresolved 不得带 exclude_reason')
        if self.kind == 'source_typo' and self.disposition == 'bound' and not self.scopes:
            raise ValueError('source_typo 绑定必须带 scopes，不得升为全书别名')
        return self


class BookResolveResponse(StrictModel):
    characters: list[CharacterSeed] = Field(min_length=1)
    surface_bindings: list[SurfaceBinding] = Field(min_length=1)

    @model_validator(mode='after')
    def _unique_refs(self):
        refs = [row.character_ref for row in self.characters]
        if len(refs) != len(set(refs)):
            raise ValueError('character_ref 不得重复')
        return self


# ─── 正式产物 ────────────────────────────────────────────────────────────────


class SurfaceRecord(StrictModel):
    surface: str = Field(min_length=1)
    kind: MentionKind
    identity_revealed_from: int | None = Field(default=None, ge=1, strict=True)

    @field_validator('kind')
    @classmethod
    def _book_level_kind(cls, value: MentionKind) -> MentionKind:
        if value not in BOOK_LEVEL_SURFACE_KINDS:
            raise ValueError(f'{value} 不得进入人物级 surfaces')
        return value


class RegisteredCharacter(StrictModel):
    canonical_surface: str = Field(min_length=1)
    status: CharacterIdentityStatus
    first_mention_chapter: int = Field(ge=1, strict=True)
    name_usable_from: int | None = Field(default=None, ge=1, strict=True)
    surfaces: list[SurfaceRecord] = Field(default_factory=list)
    #: compiler 由 kind 派生，供 Stage7 残留探针；非 LLM 填写。
    name_distinctiveness: Literal[NAME_DISTINCTIVENESS]

    @model_validator(mode='after')
    def _surfaces_unique(self):
        names = [row.surface for row in self.surfaces]
        if len(names) != len(set(names)):
            raise ValueError('同一实体的 book-level surface 不得重复')
        return self


class MentionRecord(StrictModel):
    mention_id: MentionId
    chapter_ordinal: int = Field(ge=1, strict=True)
    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)
    surface: str = Field(min_length=1)
    kind: MentionKind
    disposition: MentionDisposition
    char_id: CharacterId | None = None
    exclude_reason: str | None = None
    reveals_identity: bool = False
    evidence_refs: list[EvidenceId] = Field(min_length=1)


class CharactersEditReceipt(HumanApproval):
    at: str = Field(min_length=1)
    source_sha256: Sha256
    edit_sha256: Sha256
    deleted_character_ids: list[CharacterId]
    added_character_ids: dict[LocalCharacterRef, CharacterId]

    @model_validator(mode='after')
    def _unique_added_ids(self):
        ids = list(self.added_character_ids.values())
        if len(ids) != len(set(ids)) or set(ids) & set(self.deleted_character_ids):
            raise ValueError('新增人物映射必须一对一且不得复用已删除身份')
        return self


class CharactersProvenance(LlmProvenance):
    characters_edit: CharactersEditReceipt | None = None
    retired_character_ids: list[CharacterId] = Field(default_factory=list)


class CharacterRegistry(StrictModel):
    characters: dict[CharacterId, RegisteredCharacter] = Field(min_length=1)
    mentions: dict[MentionId, MentionRecord] = Field(min_length=1)
    evidence_anchors: dict[EvidenceId, CoreEvidenceAnchor] = Field(min_length=1)
    provenance: CharactersProvenance = Field(alias='_provenance')

    @model_validator(mode='after')
    def _closed(self):
        retired = self.provenance.retired_character_ids
        if len(retired) != len(set(retired)) or set(retired) & set(self.characters):
            raise ValueError('已退休人物 ID 不得重复或重新成为当前实体')
        for mid, mention in self.mentions.items():
            if mention.mention_id != mid:
                raise ValueError('mentions 键必须等于 mention_id')
            if mention.disposition == 'bound':
                if mention.char_id is None or mention.char_id not in self.characters:
                    raise ValueError(f'{mid}: bound 必须指向已登记人物')
            elif mention.char_id is not None:
                raise ValueError(f'{mid}: 非 bound 不得带 char_id')
            for ref in mention.evidence_refs:
                if ref not in self.evidence_anchors:
                    raise ValueError(f'{mid}: 悬空 evidence_refs {ref}')
        return self


# 兼容旧名
CharacterRegistryV3 = CharacterRegistry


# ─── Edit v3 ─────────────────────────────────────────────────────────────────


class CharacterAdd(StrictModel):
    kind: Literal['add']
    character_ref: LocalCharacterRef
    canonical_surface: str = Field(min_length=1)
    status: CharacterIdentityStatus = 'confirmed'
    #: 可选：显式补一条全书 bound surface（默认等于 canonical）
    seed_surface: str | None = None
    seed_kind: MentionKind = 'proper_name'


class CharacterReplace(StrictModel):
    kind: Literal['replace']
    char_id: CharacterId
    canonical_surface: str = Field(min_length=1)
    status: CharacterIdentityStatus = 'confirmed'


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


class MentionRebind(StrictModel):
    kind: Literal['rebind_mentions']
    mention_ids: list[MentionId] = Field(min_length=1)
    disposition: MentionDisposition
    char_id: CharacterId | None = None
    mention_kind: MentionKind
    exclude_reason: str | None = None

    @model_validator(mode='after')
    def _rebind_fields(self):
        if self.disposition == 'bound' and self.char_id is None:
            raise ValueError('rebind bound 必须指定 char_id')
        if self.disposition == 'excluded' and not (self.exclude_reason or '').strip():
            raise ValueError('rebind excluded 必须说明原因')
        return self


CharacterOperation = Annotated[
    CharacterAdd | CharacterReplace | CharacterDelete | MentionRebind,
    Field(discriminator='kind'),
]


class CharactersEdit(StrictModel):
    source_sha256: Sha256
    operations: list[CharacterOperation] = Field(min_length=1)

    @model_validator(mode='after')
    def _unique_targets(self):
        ids = [
            row.char_id for row in self.operations
            if isinstance(row, (CharacterReplace, CharacterDelete))
        ]
        if len(ids) != len(set(ids)):
            raise ValueError('同一人物不能在一次编辑中重复操作')
        refs = [row.character_ref for row in self.operations if isinstance(row, CharacterAdd)]
        if len(refs) != len(set(refs)):
            raise ValueError('新增人物的局部引用不得重复')
        return self


# ─── 存量 analyze 草稿 wire（非正式产物）──────────────────────────────────────

EvidenceT = TypeVar('EvidenceT')


class LegacyNameBinding(StrictModel, Generic[EvidenceT]):
    """analyze 联合草稿中的称谓行；不得写入正式 v3 registry。"""

    surface: str = Field(min_length=1)
    identity_revealed_from: int = Field(ge=1, strict=True)
    evidence_refs: list[EvidenceT] = Field(min_length=1)


class LegacyCharacterDraft(StrictModel, Generic[EvidenceT]):
    canonical: str = Field(min_length=1)
    aliases: list[LegacyNameBinding[EvidenceT]] = Field(min_length=1)
    first_chapter: int = Field(ge=1, strict=True)
    first_appearance_evidence_refs: list[EvidenceT] = Field(min_length=1)
    role: str = Field(min_length=1)
    role_evidence_refs: list[EvidenceT] = Field(min_length=1)
    name_distinctiveness: Literal[NAME_DISTINCTIVENESS]

    @field_validator('name_distinctiveness', mode='before')
    @classmethod
    def _normalize_name_distinctiveness(cls, value):
        if value == 'low':
            return 'common'
        return value


class CharacterCandidate(LegacyCharacterDraft[CoreEvidenceCandidateRef]):
    pass


class CharacterCandidateRow(CharacterCandidate):
    character_ref: LocalCharacterRef


class CharactersResponse(StrictModel):
    """存量 analyze / draft repair 解码用；新书不得经此物化 characters.json。"""

    characters: list[CharacterCandidateRow] = Field(min_length=1)

    @model_validator(mode='after')
    def _unique_refs(self):
        refs = [row.character_ref for row in self.characters]
        if len(refs) != len(set(refs)):
            raise ValueError('人物候选局部引用不得重复')
        return self
