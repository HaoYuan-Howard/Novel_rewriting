"""Stage2 Global 直连候选契约。"""
from __future__ import annotations

import json

from contracts.invariants import GLOBAL_EVIDENCE_CONTRACT
from contracts.schema.knowledge import (
    KnowledgeResponse as GlobalGroundingCandidateResponse,
)
from novel.stages.stage2 import extract
from novel.stages.stage2.global_contract import CONTRACT, active_contract


def test_global_provider_has_one_direct_candidate_contract_and_schema():
    assert GLOBAL_EVIDENCE_CONTRACT == 'knowledge-paragraph-candidate-v2'
    assert active_contract() is CONTRACT
    assert CONTRACT.name == GLOBAL_EVIDENCE_CONTRACT
    assert CONTRACT.response_model is GlobalGroundingCandidateResponse
    assert 'e000001' in CONTRACT.system
    serialized = json.dumps(CONTRACT.hint, ensure_ascii=False)
    assert '"locator"' not in serialized
    assert 'chapter_ordinal' not in serialized
    schema = json.dumps(
        GlobalGroundingCandidateResponse.model_json_schema(by_alias=True),
        ensure_ascii=False,
    )
    assert '"evidence_refs":' in schema
    assert '"characters":' not in schema
    assert not hasattr(extract, '_finalize_agent_global_formal')
