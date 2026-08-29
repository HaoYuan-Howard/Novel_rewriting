#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""provider-neutral agent run revision / commit core 的零成本合同测试。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from contracts.artifacts import STAGE4_EVIDENCE_BINDING, STAGE4_PLAN_CHAPTERS
from contracts.schema.orchestration import AgentProfile
from novel import paths, prov
from novel.agents import (
    AgentDriver, AgentProbe, AgentRequest, AgentTurn, AgentUsage,
    render_agent_stdin, render_structured_agent_stdin,
)
from novel.agents.session_client import SessionAgentClient
from novel.errors import ArtifactUnreadable, ProvenanceRefused
from novel.llm import agent_runs, stage4_runs
from novel.llm.cache import CachePolicy
from novel.llm.client import LlmFailed
from novel.llm.ledger import CostLedger, ledger_path
from novel.run import RunContext


class _Answer(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    value: int


class _OtherAnswer(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    other: int


class _Driver(AgentDriver):
    driver = 'codex'
    provider = 'openai'

    def __init__(self, contents: list[str]) -> None:
        self.contents = list(contents)
        self.index = 0

    def probe(self) -> AgentProbe:
        return AgentProbe(
            driver='codex', provider='openai', executable='fake-codex',
            available=True, version='0.test')

    def _next(self) -> AgentTurn:
        content = self.contents[self.index]
        self.index += 1
        return AgentTurn(
            session_id='provider-stage4-design',
            content=content,
            model='gpt-5.6-sol',
            actual_reasoning_effort='high',
            usage=AgentUsage(prompt_tokens=7, completion_tokens=2),
            # executor-isolated 每次 CLI 调用只允许返回一个 turn。
            turn_count=1,
        )

    def start(self, profile: AgentProfile, request: AgentRequest) -> AgentTurn:
        return self._next()

    def resume(
            self, profile: AgentProfile, session_id: str,
            request: AgentRequest,
    ) -> AgentTurn:
        assert session_id == 'provider-stage4-design'
        return self._next()

    def cancel(self, session_id: str | None = None) -> bool:
        return False

    def close(self) -> None:
        return None


def _profile(*, effort: str = 'high') -> AgentProfile:
    return AgentProfile(
        driver='codex', model='gpt-5.6-sol',
        reasoning_effort=effort, execution_mode='standard')


def _claude_profile() -> AgentProfile:
    return AgentProfile(
        driver='claude-code', model='claude-opus-5',
        reasoning_effort='high', execution_mode='standard')


def _ctx(
        base: Path, *, run_id: str | None = None,
        code: str | None = None, profile: AgentProfile | None = None,
) -> RunContext:
    novel_dir = base / '通用Agent恢复核_4242'
    novel_dir.mkdir(exist_ok=True)
    ledger = (CostLedger(path=ledger_path(base)) if run_id is None else
              CostLedger(path=ledger_path(base), run_id=run_id))
    return RunContext(
        novel_dir=novel_dir,
        node='stage4.design',
        paid=True,
        code=code or prov.code_version(),
        cache=CachePolicy.default(),
        ledger=ledger,
        executor_profile=profile or _profile(),
        run_id=run_id or '',
    )


def _spec() -> agent_runs.SingleSessionRunSpec:
    return agent_runs.SingleSessionRunSpec(
        label='test stage4 design',
        node='stage4.design',
        session_owner='stage4.design',
        purpose='stage4.design',
        chapter=None,
        phase='design',
        recovery_policy='stage4-design-v1',
        session_isolation='per-node',
        run_path=lambda ctx: (
            ctx.novel_dir / '_agent_runs_test' / ctx.run_id / 'design'),
        checkpoint_dir=lambda ctx: (
            ctx.novel_dir / '_agent_runs_test' / ctx.run_id
            / 'design' / 'checkpoints'),
    )


_SYSTEM = '稳定 system'
_USER = '稳定 user'
_INPUT_SHA = hashlib.sha256(b'agent-run-input').hexdigest()
_MAX_TOKENS = 2048


def _ensure(ctx: RunContext) -> dict[str, object]:
    return agent_runs.ensure_run(
        ctx, _spec(), input_sha256=_INPUT_SHA,
        system=_SYSTEM, user=_USER,
        response_model=_Answer, max_tokens=_MAX_TOKENS)


def _accept(
        ctx: RunContext, client: SessionAgentClient,
) -> tuple[_Answer, dict[str, object]]:
    value = client.ask_json(
        _SYSTEM, _USER, model_cls=_Answer,
        purpose='stage4.design', max_tokens=_MAX_TOKENS,
        recovery_policy='stage4-design-v1')
    return value, client.accepted_checkpoint('stage4.design')


def _write(
        ctx: RunContext, value: _Answer, acceptance: dict[str, object], *,
        state_value: str,
) -> Path:
    return agent_runs.write_checkpoint(
        ctx, _spec(), input_sha256=_INPUT_SHA,
        system=_SYSTEM, user=_USER,
        response_model=_Answer, max_tokens=_MAX_TOKENS,
        response_payload=value.model_dump(mode='json'),
        acceptance=acceptance,
        revision_key='candidate',
        state_payload={'progress_state': state_value},
    )


def test_revision_chain_restores_latest_and_old_corruption_cannot_hide(
        tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path)
    _ensure(ctx)
    client = SessionAgentClient(
        ctx, _profile(), _Driver(['{"value":1}', '{"value":2}']),
        max_turns=4)
    first, first_acceptance = _accept(ctx, client)
    first_path = _write(ctx, first, first_acceptance, state_value='first')
    second, second_acceptance = _accept(ctx, client)
    second_path = _write(ctx, second, second_acceptance, state_value='second')
    client.finish(process_state='ok', delivery_state='delivered')
    client.close()

    assert first_path.name == 'rev-0001.json'
    assert second_path.name == 'rev-0002.json'
    first_record = json.loads(first_path.read_text(encoding='utf-8'))
    second_record = json.loads(second_path.read_text(encoding='utf-8'))
    assert second_record['previous_checkpoint_sha256'] == \
        first_record['checkpoint_sha256']
    assert second_record['accepted_turn_index'] > \
        first_record['accepted_turn_index']
    assert {
        row['attempt_id'] for row in first_record['attempt_links']
    }.issubset({
        row['attempt_id'] for row in second_record['attempt_links']
    })

    restarted = _ctx(tmp_path, run_id=ctx.run_id, code=ctx.code)
    _ensure(restarted)
    restored = agent_runs.restore_checkpoint(
        restarted, _spec(), input_sha256=_INPUT_SHA,
        system=_SYSTEM, user=_USER,
        response_model=_Answer, max_tokens=_MAX_TOKENS)
    assert restored is not None
    assert restored.value.value == 2
    assert restored.revision == 2
    assert restored.state_payload == {'progress_state': 'second'}

    # latest 仍是完整的，但旧 revision 的任意一个字节损坏也必须
    # 关闭式失败，不能跳过它只读 latest。
    first_record['state_payload']['progress_state'] = 'tampered'
    first_path.write_text(
        json.dumps(first_record, ensure_ascii=False), encoding='utf-8')
    with pytest.raises(ArtifactUnreadable, match='checkpoint SHA'):
        agent_runs.restore_checkpoint(
            restarted, _spec(), input_sha256=_INPUT_SHA,
            system=_SYSTEM, user=_USER,
            response_model=_Answer, max_tokens=_MAX_TOKENS)


def test_revision_is_idempotent_per_attempt_and_refuses_state_or_gap(
        tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path)
    _ensure(ctx)
    client = SessionAgentClient(
        ctx, _profile(), _Driver(['{"value":1}']), max_turns=3)
    value, acceptance = _accept(ctx, client)
    path = _write(ctx, value, acceptance, state_value='stable')
    assert _write(ctx, value, acceptance, state_value='stable') == path
    with pytest.raises(ProvenanceRefused, match='response/state'):
        _write(ctx, value, acceptance, state_value='different')
    client.finish(process_state='ok', delivery_state='delivered')
    client.close()

    path.unlink()
    orphan = path.with_name('rev-0002.json')
    orphan.write_text('{}', encoding='utf-8')
    restarted = _ctx(tmp_path, run_id=ctx.run_id, code=ctx.code)
    _ensure(restarted)
    with pytest.raises(ArtifactUnreadable, match='revision 链有缺口'):
        agent_runs.restore_checkpoint(
            restarted, _spec(), input_sha256=_INPUT_SHA,
            system=_SYSTEM, user=_USER,
            response_model=_Answer, max_tokens=_MAX_TOKENS)


def test_new_revision_requires_monotonic_turn_and_all_prior_attempts(
        tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path)
    _ensure(ctx)
    client = SessionAgentClient(
        ctx, _profile(), _Driver(['{"value":1}', '{"value":2}']),
        max_turns=4)
    first, first_acceptance = _accept(ctx, client)
    _write(ctx, first, first_acceptance, state_value='first')
    second, second_acceptance = _accept(ctx, client)

    non_monotonic = dict(second_acceptance)
    non_monotonic['accepted_turn_index'] = 1
    with pytest.raises(ProvenanceRefused, match='session turn'):
        _write(ctx, second, non_monotonic, state_value='bad-turn')

    missing_prior = dict(second_acceptance)
    missing_prior['attempt_links'] = [
        dict(row) for row in second_acceptance['attempt_links']
        if row['attempt_id'] == second_acceptance['attempt_id']
    ]
    missing_prior['recovery_from_attempts'] = []
    with pytest.raises(ProvenanceRefused, match='turn/recovery/attempt_links'):
        _write(ctx, second, missing_prior, state_value='missing-prior')

    # 失败候选没有污染 append-only 链，完整 acceptance 仍能追加。
    path = _write(ctx, second, second_acceptance, state_value='second')
    assert path.name == 'rev-0002.json'
    client.finish(process_state='ok', delivery_state='delivered')
    client.close()


def test_clean_session_cannot_self_consistently_replace_original_prompt_hash(
        tmp_path: Path, monkeypatch) -> None:
    ctx = _ctx(tmp_path)
    _ensure(ctx)
    client = SessionAgentClient(
        ctx, _profile(), _Driver(['{"value":1}']), max_turns=2)
    value, acceptance = _accept(ctx, client)
    wrong_sha = 'f' * 64
    events = agent_runs.agent_session_audit.read_events(ctx.novel_dir.parent)
    tampered = [
        replace(event, prompt_sha256=wrong_sha)
        if (event.session_run_id == acceptance['session_run_id']
            and event.kind in ('started', 'turn'))
        else event
        for event in events
    ]
    monkeypatch.setattr(
        agent_runs.agent_session_audit, 'read_events', lambda _base: tampered)
    changed_acceptance = {**acceptance, 'accepted_prompt_sha256': wrong_sha}

    with pytest.raises(ProvenanceRefused, match='正常首发 stdin'):
        _write(ctx, value, changed_acceptance, state_value='tampered')
    client.close()


def test_trailing_rejected_turn_does_not_replace_last_accepted_checkpoint(
        tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _ensure(ctx)
    client = SessionAgentClient(
        ctx, _profile(), _Driver(['{"value":1}', '{"other":2}']),
        max_turns=2)
    value, acceptance = _accept(ctx, client)
    with pytest.raises(LlmFailed, match='已用尽 2 轮'):
        client.ask_json(
            _SYSTEM, _USER, model_cls=_Answer,
            purpose='stage4.design', max_tokens=_MAX_TOKENS,
            recovery_policy='stage4-design-v1')

    path = _write(ctx, value, acceptance, state_value='last-valid')
    assert path.name == 'rev-0001.json'
    records = agent_runs.agent_session_audit.fold(
        agent_runs.agent_session_audit.read_events(ctx.novel_dir.parent))
    assert [turn.turn_index for turn in records[0].turns] == [1, 2]
    assert acceptance['accepted_turn_index'] == 1
    client.finish(process_state='ok', delivery_state='delivered')
    client.close()


def test_run_refuses_profile_prompt_schema_and_code_drift(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _ensure(ctx)
    with pytest.raises(ProvenanceRefused, match='漂移'):
        agent_runs.ensure_run(
            ctx, _spec(), input_sha256=_INPUT_SHA,
            system=_SYSTEM, user=_USER + ' changed',
            response_model=_Answer, max_tokens=_MAX_TOKENS)
    with pytest.raises(ProvenanceRefused, match='漂移'):
        agent_runs.ensure_run(
            ctx, _spec(), input_sha256=_INPUT_SHA,
            system=_SYSTEM, user=_USER,
            response_model=_OtherAnswer, max_tokens=_MAX_TOKENS)

    changed_code = _ctx(
        tmp_path, run_id=ctx.run_id, code=ctx.code + '-changed')
    with pytest.raises(ProvenanceRefused, match='run/sid/node/code'):
        _ensure(changed_code)
    changed_profile = _ctx(
        tmp_path, run_id=ctx.run_id, code=ctx.code,
        profile=_profile(effort='max'))
    with pytest.raises(ProvenanceRefused, match='profile'):
        _ensure(changed_profile)


def test_claude_run_identity_hashes_the_final_structured_stdin(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, profile=_claude_profile())
    record = _ensure(ctx)
    expected_stdin = render_structured_agent_stdin(
        _SYSTEM, _USER, driver='claude-code')

    assert record['request']['initial_prompt_sha256'] == hashlib.sha256(
        expected_stdin.encode('utf-8')).hexdigest()
    assert record['request']['initial_prompt_sha256'] != hashlib.sha256(
        render_agent_stdin(_SYSTEM, _USER).encode('utf-8')
    ).hexdigest()


def test_commit_target_states_and_formal_adoption_are_exact(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _ensure(ctx)
    client = SessionAgentClient(
        ctx, _profile(), _Driver(['{"value":7}']), max_turns=3)
    value, acceptance = _accept(ctx, client)
    _write(ctx, value, acceptance, state_value='terminal')
    client.finish(process_state='ok', delivery_state='delivered')
    client.close()

    run_spec = _spec()
    formal = 'stage9/formal.json'
    invalid = 'stage9/formal.INVALID.json'
    commit_spec = agent_runs.CommitBundleSpec(
        run=run_spec,
        commit_path=lambda current: run_spec.run_path(current) / 'committed.json',
        primary_artifact=formal,
        allowed_touched=((formal, invalid),),
        nonformal_paths=(invalid,),
    )
    invalid_path = ctx.novel_dir / invalid
    invalid_path.parent.mkdir(parents=True, exist_ok=True)
    invalid_path.write_bytes(b'old-invalid')
    primary = {'value': 7}
    finalization = {'delivery_kind': 'clean'}
    record = agent_runs.prepare_commit(
        ctx, commit_spec,
        primary_payload=primary,
        finalization_payload=finalization,
        touched_paths=(formal, invalid),
        attempt_id=str(acceptance['attempt_id']),
    )
    assert agent_runs.target_state(ctx, commit_spec, record, formal) == 'baseline'
    assert agent_runs.target_state(ctx, commit_spec, record, invalid) == 'baseline'

    formal_path = ctx.novel_dir / formal
    formal_path.parent.mkdir(parents=True, exist_ok=True)
    expected_bytes = b'formal-v1'
    formal_path.write_bytes(expected_bytes)
    invalid_path.unlink()
    assert agent_runs.target_state(
        ctx, commit_spec, record, formal,
        expected_file_sha256=hashlib.sha256(expected_bytes).hexdigest()) == 'expected'
    assert agent_runs.target_state(
        ctx, commit_spec, record, invalid,
        expected_absent=True) == 'expected'

    fingerprint = prov.projection_fingerprint(primary)
    ctx.ledger.note_adoption(
        sid=ctx.sid, node=ctx.node, purpose=run_spec.purpose,
        attempt_id=str(acceptance['attempt_id']), artifact=formal,
        artifact_fingerprint=fingerprint, chapter=None)
    committed_path = agent_runs.mark_committed(
        ctx, commit_spec,
        primary_payload=primary,
        finalization_payload=finalization,
        landed_payloads={formal: primary},
    )
    committed = agent_runs.commit_state(ctx, commit_spec)
    assert committed_path.is_file()
    assert committed is not None and committed['state'] == 'committed'
    agent_runs.verify_committed_delivery(
        ctx, commit_spec, committed,
        primary_payload=primary,
        finalization_payload=finalization,
        landed_payloads={formal: primary},
    )

    formal_path.write_bytes(b'third-party')
    with pytest.raises(ProvenanceRefused, match='既不是写前基线'):
        agent_runs.target_state(
            ctx, commit_spec, committed, formal,
            expected_file_sha256=hashlib.sha256(expected_bytes).hexdigest())


def test_stage4_plan_commit_declares_three_exact_terminal_bundles() -> None:
    spec = stage4_runs._commit_spec('stage4.plan')
    invalid = paths.invalid_of(STAGE4_PLAN_CHAPTERS)
    assert {
        frozenset(group) for group in spec.allowed_touched
    } == {
        frozenset((STAGE4_EVIDENCE_BINDING,)),
        frozenset((STAGE4_EVIDENCE_BINDING, invalid)),
        frozenset((
            STAGE4_EVIDENCE_BINDING,
            STAGE4_PLAN_CHAPTERS,
            invalid,
        )),
    }
    assert spec.nonformal_paths == (invalid,)
