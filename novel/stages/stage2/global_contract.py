#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``stage2.global`` 唯一现役 evidence wire 契约。

正式 ``GlobalGrounding`` 始终只保存 ``SourceSpan``；provider 只交关闭的
``evidence_candidate_id``。system、hint、直接响应与 agent 信封绑在同一份 spec，
避免真实 executor 与预览/编译边界分家。人工 ``GlobalEdit`` 与存量 ``.INVALID``
仍可交 locator；那是 0 LLM 恢复面，不是本模块的 provider wire。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from contracts.invariants import GLOBAL_EVIDENCE_CONTRACT
from contracts.schema.knowledge import (
    KnowledgeAgentTurnResponse, KnowledgeResponse,
)
from novel.config import reject_retired_global_evidence_selector
from novel.llm.prompts import with_json_shape
from novel.stages.stage2 import knowledge_prompts


@dataclass(frozen=True)
class GlobalEvidenceContractSpec:
    """一套 global evidence 响应策略的全部构件。"""

    name: str
    instruction: str
    system: str
    sys_name: str
    hint: dict[str, object]
    response_model: type[BaseModel]
    agent_response_model: type[BaseModel]
    schema_mode: Literal['prompt_plus_local_validation'] = \
        'prompt_plus_local_validation'


CONTRACT = GlobalEvidenceContractSpec(
    name=GLOBAL_EVIDENCE_CONTRACT,
    instruction=knowledge_prompts.SYSTEM,
    system=with_json_shape(
        knowledge_prompts.SYSTEM,
        knowledge_prompts.HINT,
    ),
    sys_name='KNOWLEDGE_SYS',
    hint=knowledge_prompts.HINT,
    response_model=KnowledgeResponse,
    agent_response_model=KnowledgeAgentTurnResponse,
)


def active_contract() -> GlobalEvidenceContractSpec:
    """返回唯一 provider wire；不存在运行期选臂或 locator fallback。"""
    reject_retired_global_evidence_selector()
    return CONTRACT


__all__ = [
    'CONTRACT', 'GlobalEvidenceContractSpec', 'active_contract',
]
