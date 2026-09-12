"""已购但尚不能编译的单 owner 草稿修复；不是正式人物或知识产物。"""
from __future__ import annotations

from typing import Literal
from pydantic import Field, model_validator
from contracts.schema.base import StrictModel
from contracts.schema.common import Sha256
from contracts.schema.characters import CharactersResponse, CharacterRegistry, CharacterId, LocalCharacterRef
from contracts.schema.knowledge import KnowledgeDraft, KnowledgeGrounding

DraftOwner = Literal['stage2.characters', 'stage2.global']
DraftSourceState = Literal['draft_invalid', 'awaiting_characters', 'prepared']


class AnalyzeOwnerDraftEdit(StrictModel):
    owner: DraftOwner
    source_kind: Literal['analyze_owner_draft'] = 'analyze_owner_draft'
    source_receipt_sha256: Sha256
    source_draft_sha256: Sha256
    source_mapping_sha256: Sha256
    character_bindings: dict[LocalCharacterRef, CharacterId]
    characters: CharactersResponse | None
    global_draft: KnowledgeDraft | None

    @model_validator(mode='after')
    def _one_owner(self):
        if ((self.characters is not None) != (self.owner == 'stage2.characters')
                or (self.global_draft is not None) != (self.owner == 'stage2.global')):
            raise ValueError('草稿替换必须恰好属于指定 owner')
        if self.owner == 'stage2.characters' and self.character_bindings:
            raise ValueError('人物草稿持久 ID 由代码编译，不接受人工声明映射')
        if len(set(self.character_bindings.values())) != len(self.character_bindings):
            raise ValueError('Global 人物句柄必须一对一；失配须明确 typed 订正')
        return self


class AnalyzeDraftRepairJournal(StrictModel):
    state: Literal['prepared', 'committed']
    run_id: str = Field(min_length=1)
    code: str = Field(min_length=1)
    request: AnalyzeOwnerDraftEdit
    request_sha256: Sha256
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    source_run_sha256: Sha256
    source_checkpoint_sha256: Sha256
    main_text_sha256: Sha256
    characters_sha256: Sha256 | None
    target_rel: str = Field(min_length=1)
    target_sha256: Sha256
    projection_sha256: Sha256 | None
    candidate: CharacterRegistry | KnowledgeGrounding
    hard_hits: list[str]

    @model_validator(mode='after')
    def _owned_target(self):
        character = self.request.owner == 'stage2.characters'
        formal = 'stage2/characters.json' if character else 'stage2/global_grounding.json'
        target = formal.replace('.json', '.INVALID.json') if self.hard_hits else formal
        if self.target_rel != target or isinstance(self.candidate, CharacterRegistry) != character:
            raise ValueError('草稿修复候选、owner、HARD 与目标不一致')
        if (self.projection_sha256 is not None) != (character and not self.hard_hits):
            raise ValueError('只有合格人物草稿修复需要称谓投影')
        if (self.characters_sha256 is not None) != (not character):
            raise ValueError('Global 草稿修复必须绑定已保存人物的精确字节')
        return self
