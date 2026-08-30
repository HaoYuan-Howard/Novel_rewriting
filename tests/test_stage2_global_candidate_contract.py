#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stage2.global 闭集 evidence 实验臂的真实 executor 纵切。"""
from __future__ import annotations

import copy
import json

import pytest

from contracts.schema.stage2 import (
    GlobalCandidateAgentTurnResponse, GlobalGrounding,
    GlobalGroundingCandidateResponse,
)
from novel.agents.session_client import agent_json_schema
from novel.core.evidence import iter_global_evidence_slots
from novel.node_agents import Stage2GlobalAgent
from novel.config import load_config
from novel.stages.stage2 import extract
from novel.stages.stage2.global_contract import CONTRACTS
from novel.stages.stage2.global_evidence_candidates import (
    GlobalEvidenceCandidateTable,
)
from novel.store import ArtifactStore
from tests.test_stage2 import main_text
from tests.test_stage2_generation import _response_global
from tests.test_stage2_global_agent import _Driver, _ctx


_ENV_KEY = 'NOVEL_STAGE2_GLOBAL_EVIDENCE_CONTRACT'


def _candidate_response() -> dict:
    """把既有 locator fixture 投影成同一正文候选表里的关闭式 ID。"""
    candidate = copy.deepcopy(_response_global())
    table = GlobalEvidenceCandidateTable.build(main_text())
    for claim_path, owner, evidence_field in iter_global_evidence_slots(candidate):
        evidence = owner[evidence_field]
        chapter = evidence['chapter_ordinal']
        locator = evidence['locator'].strip()
        matches = [
            row for row in table.rows
            if row.chapter_ordinal == chapter and locator in row.text
        ]
        assert matches, f'{claim_path}: fixture locator 不在同章候选段落中'
        owner[evidence_field] = {
            'evidence_candidate_id': matches[0].evidence_candidate_id,
        }
    return GlobalGroundingCandidateResponse.model_validate(candidate).model_dump(
        by_alias=True, mode='json')


def _full(candidate: dict) -> str:
    return json.dumps({
        'payload': {'kind': 'full_candidate', 'candidate': candidate},
    }, ensure_ascii=False)


def test_candidate_contract_is_not_default_and_has_separate_wire_schema():
    assert tuple(CONTRACTS) == ('locator-v1', 'paragraph-candidate-v1')
    assert CONTRACTS['locator-v1'].candidate_only is False
    candidate = CONTRACTS['paragraph-candidate-v1']
    assert candidate.candidate_only is True
    assert candidate.response_model is GlobalGroundingCandidateResponse
    assert candidate.agent_response_model is GlobalCandidateAgentTurnResponse
    assert 'evidence_candidate_id' in candidate.system
    assert '"locator"' not in json.dumps(candidate.hint, ensure_ascii=False)


def test_candidate_agent_has_only_full_candidate_while_locator_keeps_patch():
    candidate_system = extract.build_global_agent_system(
        CONTRACTS['paragraph-candidate-v1'])
    assert '每一次回答都必须使用 full_candidate 并完整重交 candidate' in \
        candidate_system
    assert '同一 full_candidate 形状完整重交' in candidate_system
    assert '"kind": "evidence_patch"' not in candidate_system
    assert 'candidate_sha256' not in candidate_system
    assert 'changes' not in candidate_system

    # locator-v1 的既有局部修订出口仍在；candidate 收窄不能改掉控制臂行为。
    locator_system = extract.build_global_agent_system(CONTRACTS['locator-v1'])
    assert '"kind": "evidence_patch"' in locator_system
    assert 'candidate_sha256' in locator_system
    assert '代码指定局部证据修订时的形状' in locator_system


def test_candidate_contract_config_is_closed_and_locator_stays_default(monkeypatch):
    monkeypatch.delenv(_ENV_KEY, raising=False)
    assert load_config().stage2_global_evidence_contract == 'locator-v1'
    monkeypatch.setenv(_ENV_KEY, 'not-registered')
    with pytest.raises(ValueError, match='NOVEL_STAGE2_GLOBAL_EVIDENCE_CONTRACT'):
        load_config()


def test_candidate_agent_compiles_ids_to_formal_source_spans(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv(_ENV_KEY, 'paragraph-candidate-v1')
    ctx = _ctx(tmp_path)
    driver = _Driver([_full(_candidate_response())], ctx.novel_dir)

    result = Stage2GlobalAgent(ctx, driver=driver, max_turns=2).run()

    assert result.turns == 1
    request = driver.calls[0][1]
    assert request.output_schema == agent_json_schema(
        GlobalCandidateAgentTurnResponse)
    assert '<e000001>' in request.prompt
    assert 'evidence_candidate_id' in request.system
    landed = ArtifactStore(ctx).read(GlobalGrounding, extract.GLOBAL_REL)
    dumped = landed.model_dump(by_alias=True, mode='json')
    assert 'evidence_candidate_id' not in json.dumps(dumped, ensure_ascii=False)
    assert not any(
        key == 'locator'
        for value in _walk(dumped) if isinstance(value, dict)
        for key in value
    )
    relation = landed.entities.relations[0]
    assert relation.predicate_evidence.source is not None
    source = relation.predicate_evidence.source
    chapter = next(
        row for row in main_text()
        if row['chapter_ordinal'] == relation.predicate_evidence.chapter_ordinal)
    assert chapter['text'][source.start:source.end] == source.text


def _walk(value):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def test_unknown_candidate_id_is_rejected_before_acceptance_or_invalid_write(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv(_ENV_KEY, 'paragraph-candidate-v1')
    ctx = _ctx(tmp_path)
    invalid = _candidate_response()
    invalid['entities']['relations'][0]['predicate_evidence'] = {
        'evidence_candidate_id': 'e999999',
    }
    driver = _Driver([
        _full(invalid),
        _full(_candidate_response()),
    ], ctx.novel_dir)

    result = Stage2GlobalAgent(ctx, driver=driver, max_turns=2).run()

    assert result.turns == 2
    assert [kind for kind, _request in driver.calls] == ['start', 'resume']
    assert '证据候选不在本次下发闭集' in driver.calls[1][1].prompt
    assert all(
        request.output_schema == agent_json_schema(
            GlobalCandidateAgentTurnResponse)
        for _kind, request in driver.calls
    )
    feedback = json.loads(driver.calls[1][1].prompt)
    assert '证据候选不在本次下发闭集' in feedback['validator_feedback']
    assert 'repair_contract' not in feedback
    store = ArtifactStore(ctx)
    assert store.exists(extract.GLOBAL_REL)
    assert not store.exists(extract._GLOBAL_INVALID)


def test_candidate_agent_rejects_fake_patch_then_requires_full_same_schema(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv(_ENV_KEY, 'paragraph-candidate-v1')
    ctx = _ctx(tmp_path)
    fake_patch = json.dumps({
        'payload': {
            'kind': 'evidence_patch',
            'candidate_sha256': 'a' * 64,
            'changes': [{
                'claim_path': 'entities.relations.rel_01.predicate',
                'evidence': {'evidence_candidate_id': 'e000001'},
            }],
        },
    }, ensure_ascii=False)
    driver = _Driver([
        fake_patch,
        _full(_candidate_response()),
    ], ctx.novel_dir)

    result = Stage2GlobalAgent(ctx, driver=driver, max_turns=2).run()

    assert result.turns == 2
    assert [kind for kind, _request in driver.calls] == ['start', 'resume']
    expected_schema = agent_json_schema(GlobalCandidateAgentTurnResponse)
    assert all(request.output_schema == expected_schema
               for _kind, request in driver.calls)
    assert ArtifactStore(ctx).exists(extract.GLOBAL_REL)
    assert not ArtifactStore(ctx).exists(extract._GLOBAL_INVALID)
