#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``stage2.global`` evidence wire 契约注册表。

正式 ``GlobalGrounding`` 始终只保存 ``SourceSpan``；本表只决定模型在落盘前交
``chapter_ordinal + locator``，还是交关闭的 ``evidence_candidate_id``。把 system、
hint、直接响应与 agent 信封绑成一条 spec，避免实验臂只换 prompt、真实 executor
仍按 locator schema 验收这种静默混搭。

``paragraph-candidate-v1`` 仍是 TODO-45 P2a 的独立冷缓存 A/B，不是生产默认。
切默认只允许改 ``contracts.invariants.GLOBAL_EVIDENCE_CONTRACT``，且必须先满足
质量无回退与总时间/成本改善的预登记门。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

from pydantic import BaseModel

from contracts.invariants import GLOBAL_EVIDENCE_CONTRACTS
from contracts.schema.stage2 import (
    GlobalAgentTurnResponse, GlobalCandidateAgentTurnResponse,
    GlobalGroundingCandidateResponse, GlobalGroundingResponse,
)
from novel.llm.prompts import with_json_shape
from novel.stages.stage2 import prompts


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
    evidence_mode: Literal['locator', 'candidate']
    schema_mode: Literal['prompt_plus_local_validation'] = \
        'prompt_plus_local_validation'

    @property
    def candidate_only(self) -> bool:
        return self.evidence_mode == 'candidate'


CONTRACTS: dict[str, GlobalEvidenceContractSpec] = {
    'locator-v1': GlobalEvidenceContractSpec(
        name='locator-v1',
        instruction=prompts.GLOBAL_SYS,
        system=with_json_shape(prompts.GLOBAL_SYS, prompts.GLOBAL_HINT),
        sys_name='GLOBAL_SYS',
        hint=prompts.GLOBAL_HINT,
        response_model=GlobalGroundingResponse,
        agent_response_model=GlobalAgentTurnResponse,
        evidence_mode='locator',
    ),
    'paragraph-candidate-v1': GlobalEvidenceContractSpec(
        name='paragraph-candidate-v1',
        instruction=prompts.GLOBAL_CANDIDATE_SYS,
        system=with_json_shape(
            prompts.GLOBAL_CANDIDATE_SYS,
            prompts.GLOBAL_CANDIDATE_HINT,
        ),
        sys_name='GLOBAL_CANDIDATE_SYS',
        hint=prompts.GLOBAL_CANDIDATE_HINT,
        response_model=GlobalGroundingCandidateResponse,
        agent_response_model=GlobalCandidateAgentTurnResponse,
        evidence_mode='candidate',
    ),
}

assert set(CONTRACTS) == set(GLOBAL_EVIDENCE_CONTRACTS), \
    'global evidence 契约注册表与 invariants 取值面分家'
assert all(spec.name == name for name, spec in CONTRACTS.items())


def active_contract(
    env: Mapping[str, str] | None = None,
) -> GlobalEvidenceContractSpec:
    """按运行期配置选择整套 wire 契约；配置层已关闭式校验取值。"""
    from novel.config import load_config
    selected = load_config(env=env).stage2_global_evidence_contract
    return CONTRACTS[selected]


def contract_for(name: str) -> GlobalEvidenceContractSpec:
    if name not in CONTRACTS:
        raise KeyError(
            f'未登记的 stage2.global evidence 契约 {name!r}；'
            f'取值面 {GLOBAL_EVIDENCE_CONTRACTS}')
    return CONTRACTS[name]


__all__ = [
    'CONTRACTS', 'GlobalEvidenceContractSpec', 'active_contract', 'contract_for',
]
