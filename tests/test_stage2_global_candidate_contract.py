#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stage2.global 唯一闭集 evidence wire 的真实 executor 纵切。"""
from __future__ import annotations

from novel.stages.stage2 import grounding as grounding_io

import copy
import hashlib
import json

import pytest

import novel.config as novel_config
from contracts.invariants import GLOBAL_EVIDENCE_CONTRACT
from contracts.schema.knowledge import (
    KnowledgeAgentTurnResponse as GlobalCandidateAgentTurnResponse,
    KnowledgeResponse as GlobalGroundingCandidateResponse,
)
from novel import paths
from novel.agents.session_client import SessionAgentClient, agent_json_schema
from novel.errors import ProvenanceRefused, TerminalState
from novel.node_agents import Stage2GlobalAgent
from novel.llm import response_repair, stage2_global_runs
from novel.llm.client import LlmFailed
from novel.llm.ledger import AUDIT_REL
from novel.config import ENV_KEYS, load_config
from novel.stages.stage2 import extract
from novel.stages.stage2.global_contract import CONTRACT, active_contract
from novel.store import ArtifactStore
from tests.test_stage2 import main_text
from tests.test_stage2_generation import (
    _response_global,
)
from tests.test_stage2_global_agent import (
    _Driver, _ctx, _profile, _resume_ctx, _rows, _full,
)


_ENV_KEY = 'NOVEL_STAGE2_GLOBAL_EVIDENCE_CONTRACT'


def _candidate_response() -> dict:
    """返回原生 candidate fixture；构造过程从未经过 locator wire。"""
    return copy.deepcopy(_response_global())


def _session_client(ctx, driver, *, max_turns: int = 2) -> SessionAgentClient:
    return SessionAgentClient(
        ctx, _profile(), driver,
        parent_run_id=ctx.run_id, max_semantic_turns=max_turns)


def _unknown_candidate():
    value = _candidate_response()
    value['relations'][0]['evidence_refs'] = ['e999999']
    return value


def _offer_knowledge_custody(ctx, *, prior_count):
    candidate = _candidate_response()
    candidate['arc_secrets'][0]['revealed_from'] = 99
    driver = _Driver([_full(candidate)] * prior_count + [_full(_unknown_candidate())], ctx.novel_dir)
    with pytest.raises(LlmFailed, match=f'已用尽 {prior_count + 1} 轮'):
        Stage2GlobalAgent(ctx, driver=driver, max_turns=prior_count + 1).run()
    assert len(driver.calls) == prior_count + 1
    store = ArtifactStore(ctx)
    assert not store.exists(extract.GLOBAL_REL) and not store.exists(extract._GLOBAL_INVALID)
    return driver


def _seal_replacement(ctx, replacement: str) -> str:
    rejections = list((ctx.novel_dir / '_llm_rejected').glob(
        '*/*/rejection.json'))
    assert len(rejections) == 1
    rejection = json.loads(rejections[0].read_text(encoding='utf-8'))
    attempt_id = rejection['attempt_id']
    record = response_repair.find_attempt(ctx.novel_dir, attempt_id)
    response_repair.candidate_path(record).write_text(
        replacement + '\n', encoding='utf-8')
    response_repair.submit(
        ctx.novel_dir, attempt_id,
        actor='human-reviewer', reason='按当轮 dynamic contract 修复 typed patch')
    return attempt_id


def _terminal_repair_rows(base, attempt_id: str) -> list[dict]:
    return [
        row for row in _rows(base.joinpath(*AUDIT_REL))
        if row.get('kind') == 'response_repair'
        and row.get('attempt_id') == attempt_id
        and row.get('decision') in ('accepted', 'rejected', 'declined')
    ]


def test_global_provider_has_one_candidate_only_contract_and_schema():
    assert GLOBAL_EVIDENCE_CONTRACT == 'knowledge-paragraph-candidate-v2'
    assert active_contract() is CONTRACT
    assert CONTRACT.name == GLOBAL_EVIDENCE_CONTRACT
    assert CONTRACT.response_model is GlobalGroundingCandidateResponse
    assert CONTRACT.agent_response_model is GlobalCandidateAgentTurnResponse
    assert 'e000001' in CONTRACT.system
    serialized = json.dumps(CONTRACT.hint, ensure_ascii=False)
    assert '"locator"' not in serialized
    assert 'chapter_ordinal' not in serialized


def test_knowledge_agent_schema_only_accepts_complete_knowledge_candidates():
    system = extract.build_global_agent_system(CONTRACT)
    assert 'full_candidate' in system
    structured = agent_json_schema(GlobalCandidateAgentTurnResponse)
    schema = json.dumps(structured, ensure_ascii=False)
    assert 'full_candidate' in schema
    assert 'role_fate_patch' not in schema and 'evidence_patch' not in schema
    # Global 已无 Hook 定位域；所有证据只接受段落候选引用。
    locator_models = {name for name, definition in structured['$defs'].items()
                      if 'locator' in definition.get('properties', {})}
    assert locator_models == set()
    assert '"evidence_refs":' in schema
    assert '"characters":' not in schema
    assert '"character_states":' not in schema and 'HookEvidenceLocator' not in schema


def test_retired_global_selector_is_not_configurable_and_fails_closed(
    tmp_path, monkeypatch,
):
    missing_dotenv = tmp_path / 'missing.env'
    monkeypatch.delenv(_ENV_KEY, raising=False)
    config = load_config(env={}, dotenv_path=missing_dotenv)
    assert not hasattr(config, 'stage2_global_evidence_contract')
    assert _ENV_KEY not in ENV_KEYS.values()
    assert active_contract() is CONTRACT

    monkeypatch.setenv(_ENV_KEY, 'locator-v1')
    with pytest.raises(ValueError, match='选臂已退役|请移除'):
        active_contract()
    with pytest.raises(ValueError, match='选臂已退役|请移除'):
        load_config(dotenv_path=missing_dotenv)

    ctx = _ctx(tmp_path)
    driver = _Driver([_full(_candidate_response())], ctx.novel_dir)
    with pytest.raises(ValueError, match='选臂已退役|请移除'):
        Stage2GlobalAgent(ctx, driver=driver, max_turns=1).run()
    assert driver.calls == []

    monkeypatch.delenv(_ENV_KEY, raising=False)
    repo_root = tmp_path / 'repo-root'
    repo_root.mkdir()
    repo_root.joinpath('.env').write_text(
        f'{_ENV_KEY}=locator-v1\n', encoding='utf-8')
    monkeypatch.setattr(novel_config, '_REPO_ROOT', repo_root)
    dotenv_base = tmp_path / 'dotenv-agent'
    dotenv_base.mkdir()
    dotenv_ctx = _ctx(dotenv_base)
    dotenv_driver = _Driver(
        [_full(_candidate_response())], dotenv_ctx.novel_dir)
    with pytest.raises(ValueError, match='选臂已退役|请移除'):
        Stage2GlobalAgent(
            dotenv_ctx, driver=dotenv_driver, max_turns=1).run()
    assert dotenv_driver.calls == []


def test_candidate_agent_compiles_ids_to_formal_source_spans(
    tmp_path, monkeypatch,
):
    ctx = _ctx(tmp_path)
    driver = _Driver([_full(_candidate_response())], ctx.novel_dir)

    result = Stage2GlobalAgent(ctx, driver=driver, max_turns=2).run()

    assert result.turns == 1
    request = driver.calls[0][1]
    assert request.output_schema == agent_json_schema(
        GlobalCandidateAgentTurnResponse)
    assert '<e000001>' in request.prompt
    assert 'e000001' in request.system
    landed = grounding_io.read(ArtifactStore(ctx))
    dumped = landed.model_dump(by_alias=True, mode='json')
    assert 'evidence_candidate_id' not in json.dumps(dumped, ensure_ascii=False)
    assert not any(
        key == 'locator'
        for value in _walk(dumped) if isinstance(value, dict)
        for key in value
    )
    relation = landed.relations[0]
    source = landed.evidence_anchors[relation.evidence_refs[0]]
    chapter = next(
        row for row in main_text()
        if row['chapter_ordinal'] == source.chapter_ordinal)
    assert hashlib.sha256(chapter['text'][source.start:source.end].encode('utf-8')).hexdigest() == source.text_sha256


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
    ctx = _ctx(tmp_path)
    invalid = _candidate_response()
    invalid['relations'][0]['evidence_refs'] = ['e999999']
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


def test_unknown_id_typed_replacement_recovers_same_run_without_wire(
    tmp_path, monkeypatch,
):
    ctx = _ctx(tmp_path)
    invalid = _candidate_response()
    invalid['relations'][0]['evidence_refs'] = ['e999999']
    with pytest.raises(LlmFailed, match='已用尽 1 轮'):
        Stage2GlobalAgent(
            ctx, driver=_Driver([_full(invalid)], ctx.novel_dir),
            max_turns=1,
        ).run()
    store = ArtifactStore(ctx)
    assert not store.exists(extract.GLOBAL_REL)
    assert not store.exists(extract._GLOBAL_INVALID)
    checkpoint_dir = paths.stage2_global_checkpoint_dir(
        ctx.novel_dir, ctx.run_id)
    assert not list(checkpoint_dir.glob('rev-*.json'))
    assert stage2_global_runs.prepared(ctx) is None
    assert stage2_global_runs.committed(ctx) is None
    assert not [
        row for row in _rows(tmp_path.joinpath(*AUDIT_REL))
        if row.get('kind') == 'artifact_adoption'
    ]

    rejections = list((ctx.novel_dir / '_llm_rejected').glob(
        '*/*/rejection.json'))
    assert len(rejections) == 1
    rejection = json.loads(rejections[0].read_text(encoding='utf-8'))
    attempt_id = rejection['attempt_id']
    record = response_repair.find_attempt(ctx.novel_dir, attempt_id)
    response_repair.candidate_path(record).write_text(
        _full(_candidate_response()) + '\n', encoding='utf-8')
    response_repair.submit(
        ctx.novel_dir, attempt_id,
        actor='human-reviewer', reason='把未知候选 ID 改回本次下发闭集')
    # submit 后可变草稿即使被碰，也不能越过 resolution 里的 sealed candidate。
    response_repair.candidate_path(record).write_text(
        '{}\n', encoding='utf-8')

    resumed_ctx = _resume_ctx(ctx)
    no_wire = _Driver([], resumed_ctx.novel_dir)
    result = Stage2GlobalAgent(
        resumed_ctx, driver=no_wire, max_turns=1).run()

    assert result.passed and result.turns == 1
    assert no_wire.calls == []
    assert ArtifactStore(resumed_ctx).exists(extract.GLOBAL_REL)
    final_record = response_repair.find_attempt(ctx.novel_dir, attempt_id)
    assert final_record.outcome is not None
    assert final_record.outcome['decision'] == 'accepted'
    assert final_record.resolution is not None
    terminal = _terminal_repair_rows(tmp_path, attempt_id)
    assert len(terminal) == 1
    assert terminal[0]['action'] == 'agent'
    assert terminal[0]['decision'] == 'accepted'
    assert terminal[0]['actor'] == 'human-reviewer'
    assert terminal[0]['gate'] is None
    assert terminal[0]['candidate_sha256'] == final_record.resolution[
        'candidate_payload_sha256']
    assert not (
        {'candidate', 'reason', 'why', 'reject_detail'} & terminal[0].keys())


@pytest.mark.parametrize('prior_count', [1, 2, 3])
def test_knowledge_custody_preserves_prior_paid_turns_and_recovers_without_wire(
        tmp_path, prior_count):
    ctx = _ctx(tmp_path)
    _offer_knowledge_custody(ctx, prior_count=prior_count)
    attempt = _seal_replacement(ctx, _full(_candidate_response()))
    calls_before = ctx.ledger.path.read_bytes()
    resumed = _resume_ctx(ctx)
    driver = _Driver([], ctx.novel_dir)
    result = Stage2GlobalAgent(resumed, driver=driver, max_turns=prior_count + 1).run()
    assert result.passed and not driver.calls
    assert result.turns == prior_count + 1
    assert result.artifact.provenance.usage.calls_made == prior_count + 1
    rows = _terminal_repair_rows(tmp_path, attempt)
    assert len(rows) == 1 and rows[0]['decision'] == 'accepted'
    ledger_after = ctx.ledger.path.read_bytes()
    assert ledger_after == calls_before, '恢复可追加采用审计，但不得重写或新增调用账'
    again = _resume_ctx(ctx)
    driver_again = _Driver([], ctx.novel_dir)
    repeated = Stage2GlobalAgent(again, driver=driver_again, max_turns=prior_count + 1).run()
    assert repeated.passed and not driver_again.calls
    assert len(_terminal_repair_rows(tmp_path, attempt)) == 1


def test_knowledge_custody_rejects_a_replacement_with_unknown_evidence(
    tmp_path, monkeypatch,
):
    ctx = _ctx(tmp_path)
    _offer_knowledge_custody(ctx, prior_count=0)
    attempt_id = _seal_replacement(ctx, _full(_unknown_candidate()))

    resumed_ctx = _resume_ctx(ctx)
    prepared = extract.prepare_global_candidate(
        resumed_ctx, agent_envelope=True)
    extract.ensure_global_agent_run(resumed_ctx, prepared)
    with pytest.raises(TerminalState, match='不通过当前 validator'):
        extract.adopt_global_validator_resolution_checkpoint(
            resumed_ctx, prepared)
    record = response_repair.find_attempt(ctx.novel_dir, attempt_id)
    assert record.outcome is not None
    assert record.outcome['decision'] == 'rejected'
    terminal = _terminal_repair_rows(tmp_path, attempt_id)
    assert len(terminal) == 1
    assert terminal[0]['action'] == 'agent'
    assert terminal[0]['decision'] == 'rejected'
    assert terminal[0]['gate'] == 'validator'

    replay_ctx = _resume_ctx(ctx)
    replay_prepared = extract.prepare_global_candidate(
        replay_ctx, agent_envelope=True)
    extract.ensure_global_agent_run(replay_ctx, replay_prepared)
    with pytest.raises(TerminalState, match='已有不可变 rejected outcome'):
        extract.adopt_global_validator_resolution_checkpoint(
            replay_ctx, replay_prepared)
    assert len(_terminal_repair_rows(tmp_path, attempt_id)) == 1


def test_global_validator_custody_decline_audit_is_terminal_and_idempotent(
    tmp_path, monkeypatch,
):
    ctx = _ctx(tmp_path)
    _offer_knowledge_custody(ctx, prior_count=0)
    rejections = list((ctx.novel_dir / '_llm_rejected').glob(
        '*/*/rejection.json'))
    assert len(rejections) == 1
    attempt_id = json.loads(
        rejections[0].read_text(encoding='utf-8'))['attempt_id']
    response_repair.submit(
        ctx.novel_dir, attempt_id,
        actor='human-reviewer', reason='无法在不发明事实的前提下修复',
        decline=True)

    resumed_ctx = _resume_ctx(ctx)
    prepared = extract.prepare_global_candidate(
        resumed_ctx, agent_envelope=True)
    extract.ensure_global_agent_run(resumed_ctx, prepared)
    with pytest.raises(TerminalState, match='已被人工放弃'):
        extract.adopt_global_validator_resolution_checkpoint(
            resumed_ctx, prepared)
    record = response_repair.find_attempt(ctx.novel_dir, attempt_id)
    assert record.outcome is not None
    assert record.outcome['decision'] == 'declined'
    terminal = _terminal_repair_rows(tmp_path, attempt_id)
    assert len(terminal) == 1
    assert terminal[0]['action'] == 'declined'
    assert terminal[0]['decision'] == 'declined'
    assert terminal[0]['actor'] == 'human-reviewer'
    assert terminal[0]['gate'] == 'validator'
    assert terminal[0]['candidate_sha256'] is None

    replay_ctx = _resume_ctx(ctx)
    replay_prepared = extract.prepare_global_candidate(
        replay_ctx, agent_envelope=True)
    extract.ensure_global_agent_run(replay_ctx, replay_prepared)
    with pytest.raises(TerminalState, match='已有不可变 declined outcome'):
        extract.adopt_global_validator_resolution_checkpoint(
            replay_ctx, replay_prepared)
    assert len(_terminal_repair_rows(tmp_path, attempt_id)) == 1


def test_global_validator_custody_rejected_outcome_replays_audit_after_crash(
    tmp_path, monkeypatch,
):
    ctx = _ctx(tmp_path)
    _offer_knowledge_custody(ctx, prior_count=0)
    attempt_id = _seal_replacement(ctx, _full(_unknown_candidate()))

    resumed_ctx = _resume_ctx(ctx)
    prepared = extract.prepare_global_candidate(
        resumed_ctx, agent_envelope=True)
    extract.ensure_global_agent_run(resumed_ctx, prepared)

    def crash_after_outcome(**_kwargs):
        raise KeyboardInterrupt('kill after custody outcome before audit')

    monkeypatch.setattr(
        resumed_ctx.ledger, 'note_response_repair', crash_after_outcome)
    with pytest.raises(KeyboardInterrupt, match='outcome before audit'):
        extract.adopt_global_validator_resolution_checkpoint(
            resumed_ctx, prepared)
    record = response_repair.find_attempt(ctx.novel_dir, attempt_id)
    assert record.outcome is not None
    assert record.outcome['decision'] == 'rejected'
    assert _terminal_repair_rows(tmp_path, attempt_id) == []

    replay_ctx = _resume_ctx(ctx)
    replay_prepared = extract.prepare_global_candidate(
        replay_ctx, agent_envelope=True)
    extract.ensure_global_agent_run(replay_ctx, replay_prepared)
    with pytest.raises(TerminalState, match='已有不可变 rejected outcome'):
        extract.adopt_global_validator_resolution_checkpoint(
            replay_ctx, replay_prepared)
    terminal = _terminal_repair_rows(tmp_path, attempt_id)
    assert len(terminal) == 1
    assert terminal[0]['decision'] == 'rejected'
    assert terminal[0]['action'] == 'agent'
    assert terminal[0]['gate'] == 'validator'


@pytest.mark.parametrize('retired_payload', ['locator_full', 'evidence_patch', 'role_fate_patch', 'static_full'])
def test_retired_locator_wire_payloads_are_rejected_without_durable_adoption(
    tmp_path, retired_payload: str,
):
    ctx = _ctx(tmp_path)
    if retired_payload == 'locator_full':
        candidate = _candidate_response()
        candidate['setting']['era_evidence_refs'] = [{
            'chapter_ordinal': 1, 'locator': '原文明示的时代背景'}]
        raw = _full(candidate)
    elif retired_payload == 'static_full':
        from tests.stage2_fixtures import candidate_view
        raw = json.dumps({'payload': {'kind': 'full_candidate', 'candidate': candidate_view()}},
                         ensure_ascii=False)
    else:
        raw = json.dumps({
            'payload': {
                'kind': retired_payload,
                'candidate_sha256': 'a' * 64,
                'changes': [{
                    'claim_path': 'entities.relations.rel_01.predicate',
                    'evidence': {
                        'chapter_ordinal': 2,
                        'locator': '回来时看见母亲在门口等她。',
                    },
                }],
            },
        }, ensure_ascii=False)
    driver = _Driver([raw], ctx.novel_dir)

    with pytest.raises(LlmFailed, match='已用尽 1 轮'):
        Stage2GlobalAgent(ctx, driver=driver, max_turns=1).run()

    request = driver.calls[0][1]
    assert request.output_schema == agent_json_schema(
        GlobalCandidateAgentTurnResponse)
    store = ArtifactStore(ctx)
    assert not store.exists(extract.GLOBAL_REL)
    assert not store.exists(extract._GLOBAL_INVALID)
    assert not store.exists(extract.GLOBAL_REVIEW_REL)
    checkpoint_dir = paths.stage2_global_checkpoint_dir(
        ctx.novel_dir, ctx.run_id)
    assert not list(checkpoint_dir.glob('rev-*.json'))
    assert stage2_global_runs.prepared(ctx) is None
    assert stage2_global_runs.committed(ctx) is None
    audit_rows = _rows(tmp_path.joinpath(*AUDIT_REL))
    assert not [
        row for row in audit_rows
        if row.get('kind') in ('artifact_adoption', 'candidate_patch_applied')
    ]
