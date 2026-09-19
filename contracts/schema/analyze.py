# -*- coding: utf-8 -*-
"""联合首抽的短寿命 wire 与不可变来源回执，不是第三份运行知识库。"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.characters import CharacterCandidateRow
from contracts.schema.common import LlmProvenance, Sha256
from contracts.schema.knowledge import KnowledgeExtract


class AnalyzeResponse(StrictModel):
    characters: list[CharacterCandidateRow] = Field(min_length=1)
    global_draft: KnowledgeExtract = Field(alias='global')

    @model_validator(mode='after')
    def _unique_handles(self):
        refs = [row.character_ref for row in self.characters]
        if len(refs) != len(set(refs)):
            raise ValueError('联合响应人物局部句柄重复')
        return self


class AnalyzeMaterialization(StrictModel):
    owner: Literal['stage2.characters', 'stage2.global']
    #: ``deferred``：Characters v3 由独立 ``stage2.characters`` producer 物化，
    #: 联合首抽不再写 ``characters.json`` / 称名投影。
    state: Literal[
        'formal', 'invalid', 'awaiting_characters', 'draft_invalid', 'deferred',
    ]
    target_rel: str | None
    core_sha256: Sha256 | None
    issues: list[str]


class AnalyzeReceipt(StrictModel):
    """只冻结来源、局部身份映射及首次物化去向；修订不改写此回执。"""

    run_id: str = Field(min_length=1)
    attempt_id: str = Field(pattern=r'^[0-9a-f]{16}$')
    source_sha256: Sha256
    input_sha256: Sha256
    request_sha256: Sha256
    response_sha256: Sha256
    schema_sha256: Sha256
    checkpoint_sha256: Sha256
    character_handles: dict[str, str]
    global_handles: dict[str, str]
    materializations: list[AnalyzeMaterialization] = Field(min_length=2, max_length=2)
    provenance: LlmProvenance = Field(alias='_provenance')

    @model_validator(mode='after')
    def _both_owners(self):
        if {row.owner for row in self.materializations} != {'stage2.characters', 'stage2.global'}:
            raise ValueError('联合回执必须恰好登记两侧物化结果')
        if len(set(self.character_handles.values())) != len(self.character_handles):
            raise ValueError('首次人物句柄不得多对一合并')
        return self
