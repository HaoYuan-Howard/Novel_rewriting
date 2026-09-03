#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stage2.global 唯一闭集 evidence wire 的真实 executor 纵切。"""
from __future__ import annotations

import copy
import json

import pytest

import novel.config as novel_config
from contracts.invariants import FATE_WORDS, GLOBAL_EVIDENCE_CONTRACT
from contracts.schema.stage2 import (
    GlobalCandidateAgentTurnResponse, GlobalCandidateFullTurn, GlobalGrounding,
    GlobalGroundingCandidateResponse, GlobalRoleFatePatchTurn,
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
    _legacy_locator_response_global, _response_global,
)
from tests.test_stage2_global_agent import (
    _Driver, _ctx, _profile, _resume_ctx, _rows,
)


_ENV_KEY = 'NOVEL_STAGE2_GLOBAL_EVIDENCE_CONTRACT'


def _candidate_response() -> dict:
    """返回原生 candidate fixture；构造过程从未经过 locator wire。"""
    return copy.deepcopy(_response_global())


def _full(candidate: dict) -> str:
    return json.dumps({
        'payload': {'kind': 'full_candidate', 'candidate': candidate},
    }, ensure_ascii=False)


def _session_client(ctx, driver, *, max_turns: int = 2) -> SessionAgentClient:
    return SessionAgentClient(
        ctx, _profile(), driver,
        parent_run_id=ctx.run_id, max_semantic_turns=max_turns)


def _role_leak_candidate() -> dict:
    candidate = _candidate_response()
    character = candidate['entities']['characters']['char_001']
    character['role'] += '，最终仍独自承担家中账目'
    character['fate'] = None
    character['fate_evidence'] = None
    return candidate


def _role_patch_text(
        prepared: extract.GlobalCandidateContext,
        evaluation: extract.GlobalCandidateEvaluation,
        *, bad_sha: bool = False, role: str | None = None,
) -> str:
    scope = extract.global_role_fate_patch_scope(prepared, evaluation)
    assert scope is not None
    base = evaluation._response.model_dump(by_alias=True, mode='json')
    character = base['entities']['characters']['char_001']
    payload = {
        'payload': {
            'kind': 'role_fate_patch',
            'candidate_sha256': ('f' * 64 if bad_sha else scope.candidate_sha256),
            'evidence_table_sha256': scope.evidence_table_sha256,
            'changes': [{
                'char_id': 'char_001',
                'role': role or character['role'].split('，最终', 1)[0],
                'role_evidence': character['role_evidence'],
                'fate': '最终仍独自承担家中账目',
                'fate_evidence': character['role_evidence'],
            }],
        },
    }
    return json.dumps(payload, ensure_ascii=False)


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


def _offer_role_patch_custody(ctx, *, prior_count: int) -> str:
    prepared = extract.prepare_global_candidate(ctx, agent_envelope=True)
    extract.ensure_global_agent_run(ctx, prepared)
    base_raw = _role_leak_candidate()
    driver = _Driver([_full(base_raw)], ctx.novel_dir)
    client = _session_client(ctx, driver, max_turns=prior_count + 2)
    try:
        turn = client.ask_json(
            prepared.system, prepared.user,
            model_cls=prepared.agent_response_model,
            purpose=ctx.node, max_tokens=extract.GLOBAL_MAX_TOKENS,
            validator=extract.global_full_candidate_validator(prepared),
            recovery_policy=stage2_global_runs.RECOVERY_POLICY,
        )
        assert isinstance(turn, GlobalCandidateAgentTurnResponse)
        assert isinstance(turn.payload, GlobalCandidateFullTurn)
        response = turn.payload.candidate
        extract.write_global_agent_checkpoint(
            ctx, client, prepared, turn, response)
        evaluation = extract.evaluate_global_candidate(
            ctx, client, prepared, response)
        scope = extract.global_role_fate_patch_scope(prepared, evaluation)
        assert scope is not None
        if prior_count == 1:
            assert client.queue_code_validation_feedback(
                extract.global_role_fate_patch_feedback_rows(
                    evaluation, scope),
                purpose=ctx.node)
            driver.responses.append(_role_patch_text(
                prepared,
                evaluation,
                role='沈家账房，最终仍独自承担家中账目'))
            first_patch_turn = client.ask_json(
                prepared.system, prepared.user,
                model_cls=prepared.agent_response_model,
                purpose=ctx.node, max_tokens=extract.GLOBAL_MAX_TOKENS,
                validator=extract.global_role_fate_patch_validator(
                    prepared, evaluation),
                recovery_policy=stage2_global_runs.RECOVERY_POLICY,
                retain_validator_rejection=True,
            )
            assert isinstance(first_patch_turn, GlobalCandidateAgentTurnResponse)
            assert isinstance(
                first_patch_turn.payload, GlobalRoleFatePatchTurn)
            first_base = evaluation
            applied = extract.apply_global_role_fate_patch(
                prepared, first_base, first_patch_turn.payload)
            response = applied.response
            extract.write_global_agent_checkpoint(
                ctx,
                client,
                prepared,
                first_patch_turn,
                response,
                base_response=first_base._response,
                accepted_role_patch_count=1,
            )
            first_attempt = client.accepted_attempt(ctx.node)
            assert first_attempt is not None
            ctx.ledger.note_role_fate_candidate_patch(
                sid=ctx.sid,
                node=ctx.node,
                purpose=ctx.node,
                attempt_id=first_attempt,
                base_candidate_sha256=first_base.response_sha256,
                result_candidate_sha256=extract.global_response_sha256(response),
                evidence_table_sha256=(
                    first_patch_turn.payload.evidence_table_sha256),
                changed_field_count=applied.changed_field_count,
            )
            evaluation = extract.evaluate_global_candidate(
                ctx, client, prepared, response)
            scope = extract.global_role_fate_patch_scope(prepared, evaluation)
            assert scope is not None
        assert client.queue_code_validation_feedback(
            extract.global_role_fate_patch_feedback_rows(evaluation, scope),
            purpose=ctx.node)
        driver.responses.append(_role_patch_text(
            prepared, evaluation, bad_sha=True))
        with pytest.raises(
                LlmFailed, match=f'已用尽 {prior_count + 2} 轮'):
            client.ask_json(
                prepared.system, prepared.user,
                model_cls=prepared.agent_response_model,
                purpose=ctx.node, max_tokens=extract.GLOBAL_MAX_TOKENS,
                validator=extract.global_role_fate_patch_validator(
                    prepared, evaluation),
                recovery_policy=stage2_global_runs.RECOVERY_POLICY,
                retain_validator_rejection=True,
            )
        rel = extract.custody_global_validator_rejection(
            ctx, client, prepared,
            expected_turn_kind='role_fate_patch',
            base_response=evaluation._response,
            accepted_role_patch_count=prior_count,
        )
        assert rel is not None
        return _role_patch_text(prepared, evaluation)
    finally:
        client.finish(
            process_state='ok', delivery_state='failed',
            error_kind='response_validation')
        client.close()


def test_global_provider_has_one_candidate_only_contract_and_schema():
    assert GLOBAL_EVIDENCE_CONTRACT == 'paragraph-candidate-v1'
    assert active_contract() is CONTRACT
    assert CONTRACT.name == GLOBAL_EVIDENCE_CONTRACT
    assert CONTRACT.response_model is GlobalGroundingCandidateResponse
    assert CONTRACT.agent_response_model is GlobalCandidateAgentTurnResponse
    assert 'evidence_candidate_id' in CONTRACT.system
    serialized = json.dumps(CONTRACT.hint, ensure_ascii=False)
    assert '"locator"' not in serialized
    assert 'chapter_ordinal' not in serialized


def test_candidate_agent_union_has_only_full_and_role_fate_patch():
    candidate_system = extract.build_global_agent_system(CONTRACT)
    assert '第一次回答必须使用 full_candidate' in candidate_system
    assert 'repair_contract.kind=role_fate_patch' in candidate_system
    assert 'allowed_char_ids' in candidate_system
    assert 'evidence_table_sha256' in candidate_system
    assert '"kind":"role_fate_patch"' in candidate_system
    assert '"kind": "evidence_patch"' not in candidate_system
    assert '"kind":"evidence_patch"' not in candidate_system
    assert '$FATE_WORDS' not in candidate_system
    assert all(word in candidate_system for word in FATE_WORDS)
    schema = json.dumps(
        agent_json_schema(GlobalCandidateAgentTurnResponse),
        ensure_ascii=False,
    )
    assert 'full_candidate' in schema
    assert 'role_fate_patch' in schema
    assert '"const": "evidence_patch"' not in schema
    assert '"locator":' not in schema
    assert '"evidence_candidate_id":' in schema


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


def test_unknown_id_typed_replacement_recovers_same_run_without_wire(
    tmp_path, monkeypatch,
):
    ctx = _ctx(tmp_path)
    invalid = _candidate_response()
    invalid['entities']['relations'][0]['predicate_evidence'] = {
        'evidence_candidate_id': 'e999999',
    }
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


@pytest.mark.parametrize('prior_count', [0, 1])
def test_role_patch_validator_custody_replays_narrow_patch_and_increments_count(
    tmp_path, monkeypatch, prior_count: int,
):
    ctx = _ctx(tmp_path)
    replacement = _offer_role_patch_custody(
        ctx, prior_count=prior_count)
    attempt_id = _seal_replacement(ctx, replacement)

    resumed_ctx = _resume_ctx(ctx)
    prepared = extract.prepare_global_candidate(
        resumed_ctx, agent_envelope=True)
    extract.ensure_global_agent_run(resumed_ctx, prepared)
    extract.adopt_global_validator_resolution_checkpoint(
        resumed_ctx, prepared)
    source = _session_client(
        resumed_ctx, _Driver([], resumed_ctx.novel_dir),
        max_turns=prior_count + 2)
    restored = extract.restore_global_agent_checkpoint(
        resumed_ctx, source, prepared)
    source.close()

    assert restored is not None
    assert restored.turn_kind == 'custody_replacement'
    assert restored.accepted_role_patch_count == prior_count + 1
    character = restored.response.entities.characters['char_001']
    assert '最终' not in character.role
    assert character.fate == '最终仍独自承担家中账目'

    # kill 在 patch audit 之后再恢复，事件身份稳定，不得重复记数。
    replay_ctx = _resume_ctx(ctx)
    replay_prepared = extract.prepare_global_candidate(
        replay_ctx, agent_envelope=True)
    extract.ensure_global_agent_run(replay_ctx, replay_prepared)
    extract.adopt_global_validator_resolution_checkpoint(
        replay_ctx, replay_prepared)
    replay_source = _session_client(
        replay_ctx, _Driver([], replay_ctx.novel_dir),
        max_turns=prior_count + 2)
    extract.restore_global_agent_checkpoint(
        replay_ctx, replay_source, replay_prepared)
    replay_source.close()
    patch_rows = [
        row for row in _rows(tmp_path.joinpath(*AUDIT_REL))
        if row.get('kind') == 'candidate_patch_applied'
        and row.get('attempt_id') == attempt_id
    ]
    assert len(patch_rows) == 1
    assert patch_rows[0]['patch_kind'] == 'role_fate'


def test_role_patch_validator_custody_rejects_full_reentry(
    tmp_path, monkeypatch,
):
    ctx = _ctx(tmp_path)
    _offer_role_patch_custody(ctx, prior_count=0)
    attempt_id = _seal_replacement(ctx, _full(_candidate_response()))

    resumed_ctx = _resume_ctx(ctx)
    prepared = extract.prepare_global_candidate(
        resumed_ctx, agent_envelope=True)
    extract.ensure_global_agent_run(resumed_ctx, prepared)
    with pytest.raises(TerminalState, match='禁止借 custody 回 full'):
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
    _offer_role_patch_custody(ctx, prior_count=0)
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
    _offer_role_patch_custody(ctx, prior_count=0)
    attempt_id = _seal_replacement(ctx, _full(_candidate_response()))

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


def test_role_patch_custody_count_must_match_latest_durable_checkpoint(
    tmp_path, monkeypatch,
):
    ctx = _ctx(tmp_path)
    prepared = extract.prepare_global_candidate(ctx, agent_envelope=True)
    extract.ensure_global_agent_run(ctx, prepared)
    base_raw = _role_leak_candidate()
    client = _session_client(
        ctx, _Driver([_full(base_raw)], ctx.novel_dir))
    try:
        turn = client.ask_json(
            prepared.system, prepared.user,
            model_cls=prepared.agent_response_model,
            purpose=ctx.node, max_tokens=extract.GLOBAL_MAX_TOKENS,
            validator=extract.global_full_candidate_validator(prepared),
            recovery_policy=stage2_global_runs.RECOVERY_POLICY,
        )
        assert isinstance(turn, GlobalCandidateAgentTurnResponse)
        assert isinstance(turn.payload, GlobalCandidateFullTurn)
        response = turn.payload.candidate
        extract.write_global_agent_checkpoint(
            ctx, client, prepared, turn, response)

        with pytest.raises(ProvenanceRefused, match='durable checkpoint 不一致'):
            extract._assert_global_custody_durable_state(
                ctx,
                prepared,
                expected_turn_kind='role_fate_patch',
                base_response=response,
                accepted_role_patch_count=1,
            )
    finally:
        client.close()


@pytest.mark.parametrize('retired_payload', ['locator_full', 'evidence_patch'])
def test_retired_locator_wire_payloads_are_rejected_without_durable_adoption(
    tmp_path, retired_payload: str,
):
    ctx = _ctx(tmp_path)
    if retired_payload == 'locator_full':
        raw = _full(_legacy_locator_response_global())
    else:
        raw = json.dumps({
            'payload': {
                'kind': 'evidence_patch',
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
