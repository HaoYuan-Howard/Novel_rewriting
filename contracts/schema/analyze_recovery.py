"""原 GlobalDraft 的独立免费物化 WAL；不改变联合首抽回执。"""
from __future__ import annotations

from typing import Literal
from pydantic import Field, model_validator

from contracts.schema.base import StrictModel
from contracts.schema.common import Sha256
from contracts.schema.knowledge import KnowledgeGrounding


class AnalyzeRecoveryJournal(StrictModel):
    state: Literal['prepared', 'committed']
    run_id: str = Field(min_length=1)
    code: str = Field(min_length=1)
    source_run_id: str = Field(min_length=1)
    source_receipt_sha256: Sha256
    source_checkpoint_sha256: Sha256
    source_run_sha256: Sha256
    characters_sha256: Sha256
    target_rel: Literal['stage2/global_grounding.json', 'stage2/global_grounding.INVALID.json']
    target_sha256: Sha256
    candidate: KnowledgeGrounding
    hard_hits: list[str]

    @model_validator(mode='after')
    def _target_state(self):
        if ('.INVALID.' in self.target_rel) != bool(self.hard_hits):
            raise ValueError('恢复候选 HARD 与正式/INVALID 目标必须一致')
        return self
