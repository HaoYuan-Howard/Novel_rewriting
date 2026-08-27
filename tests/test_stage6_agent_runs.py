#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage6 双盲 agent checkpoint / 提交尾窗的零成本测试。"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from contracts.schema.orchestration import AgentProfile
from novel import prov
from novel.agents import (
    AgentDriver, AgentProbe, AgentRequest, AgentTurn, AgentUsage,
)
from novel.agents.session_client import SessionAgentClient
from novel.errors import ProvenanceRefused
from novel.llm import stage6_runs
from novel.llm.cache import CachePolicy
from novel.llm.ledger import CostLedger, ledger_path
from novel.run import RunContext


class _JudgeAnswer(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    value: int


class _Driver(AgentDriver):
    driver = 'codex'
    provider = 'openai'

    def __init__(self, content: str, session_id: str) -> None:
        self.content = content
        self.session_id = session_id

    def probe(self) -> AgentProbe:
        return AgentProbe(
            driver='codex', provider='openai', executable='fake-codex',
            available=True, version='0.test')

    def _turn(self) -> AgentTurn:
        return AgentTurn(
            session_id=self.session_id, content=self.content,
            model='gpt-5.6-sol', actual_reasoning_effort='high',
            usage=AgentUsage(prompt_tokens=10, completion_tokens=2),
            turn_count=1)

    def start(self, profile: AgentProfile, request: AgentRequest) -> AgentTurn:
        return self._turn()

    def resume(self, profile: AgentProfile, session_id: str,
               request: AgentRequest) -> AgentTurn:
        assert session_id == self.session_id
        return self._turn()

    def cancel(self, session_id: str | None = None) -> bool:
        return False

    def close(self) -> None:
        return None


def _profile() -> AgentProfile:
    return AgentProfile(
        driver='codex', model='gpt-5.6-sol', reasoning_effort='high',
        execution_mode='standard')


def _ctx(base: Path, *, run_id: str | None = None,
         code: str | None = None) -> RunContext:
    novel_dir = base / '双盲测试_42'
    novel_dir.mkdir(exist_ok=True)
    ledger = (CostLedger(path=ledger_path(base)) if run_id is None else
              CostLedger(path=ledger_path(base), run_id=run_id))
    return RunContext(
        novel_dir=novel_dir, node='stage6.semantic', paid=True,
        code=code or prov.code_version(), cache=CachePolicy.default(),
        ledger=ledger, executor_profile=_profile(), run_id=run_id or '')


_SYSTEM = '稳定 Stage6 系统提示'
_USERS = {'A': '匿名材料\nA', 'B': '匿名材料\nB'}
_INPUT_SHA = hashlib.sha256(b'stage6-input-projection').hexdigest()
_MAX_TOKENS = 4096


def _ensure(ctx: RunContext) -> dict[str, object]:
    return stage6_runs.ensure_run(
        ctx, input_sha256=_INPUT_SHA, system=_SYSTEM, users=_USERS,
        response_model=_JudgeAnswer, max_tokens=_MAX_TOKENS)


def _produce(
        ctx: RunContext, role: str, value: int, *,
        provider_session_id: str | None = None,
) -> tuple[_JudgeAnswer, dict[str, object]]:
    purpose = f'stage6.semantic.{role}'
    client = SessionAgentClient(
        ctx, _profile(),
        _Driver(
            f'{{"value":{value}}}',
            provider_session_id or f'provider-session-{role}'),
        max_turns=3)
    result = client.ask_json(
        _SYSTEM, _USERS[role], model_cls=_JudgeAnswer, purpose=purpose,
        max_tokens=_MAX_TOKENS,
        recovery_policy=stage6_runs.RECOVERY_POLICY)
    acceptance = client.accepted_checkpoint(purpose)
    stage6_runs.write_checkpoint(
        ctx, role=role, input_sha256=_INPUT_SHA,
        system=_SYSTEM, user=_USERS[role], response_model=_JudgeAnswer,
        max_tokens=_MAX_TOKENS,
        response_payload=result.model_dump(by_alias=True),
        acceptance=acceptance)
    client.finish(process_state='ok', delivery_state='delivered')
    client.close()
    return result, acceptance


def test_run_freezes_two_role_policy_and_refuses_prompt_or_input_drift(tmp_path):
    ctx = _ctx(tmp_path)
    record = _ensure(ctx)

    assert record['input_sha256'] == _INPUT_SHA
    assert record['request_policy']['recovery_policy'] == 'stage6-semantic-v1'
    assert record['request_policy']['session_isolation'] == 'per-purpose'
    assert [row['phase'] for row in record['request_policy']['phases']] == ['A', 'B']
    assert [row['purpose'] for row in record['requests']] == [
        'stage6.semantic.A', 'stage6.semantic.B']

    with pytest.raises(ProvenanceRefused, match='漂移'):
        stage6_runs.ensure_run(
            ctx, input_sha256=hashlib.sha256(b'changed').hexdigest(),
            system=_SYSTEM, users=_USERS, response_model=_JudgeAnswer,
            max_tokens=_MAX_TOKENS)
    with pytest.raises(ProvenanceRefused, match='漂移'):
        stage6_runs.ensure_run(
            ctx, input_sha256=_INPUT_SHA, system=_SYSTEM,
            users={'A': _USERS['A'], 'B': _USERS['B'] + ' changed'},
            response_model=_JudgeAnswer, max_tokens=_MAX_TOKENS)


def test_each_role_checkpoint_restores_typed_payload_ledger_and_session(tmp_path):
    ctx = _ctx(tmp_path)
    _ensure(ctx)
    _produce(ctx, 'A', 11)
    _produce(ctx, 'B', 22)

    restarted = _ctx(tmp_path, run_id=ctx.run_id, code=ctx.code)
    _ensure(restarted)
    restored_a = stage6_runs.restore_checkpoint(
        restarted, role='A', input_sha256=_INPUT_SHA,
        system=_SYSTEM, user=_USERS['A'], response_model=_JudgeAnswer,
        max_tokens=_MAX_TOKENS,
        validator=lambda value, _wire: None if value.value == 11 else 'wrong A')
    restored_b = stage6_runs.restore_checkpoint(
        restarted, role='B', input_sha256=_INPUT_SHA,
        system=_SYSTEM, user=_USERS['B'], response_model=_JudgeAnswer,
        max_tokens=_MAX_TOKENS)

    assert restored_a is not None and restored_a[0].value == 11
    assert restored_b is not None and restored_b[0].value == 22
    assert restored_a[1]['acceptance_protocol'] == 'node-executor/v1'
    assert restored_a[1]['accepted_request']['chapter'] is None
    assert restarted.ledger.calls_made == 2
    assert restarted.ledger.attempt_ids_for_purpose('stage6.semantic.A') == (
        restored_a[1]['attempt_id'],)


def test_restore_reruns_dynamic_validator_without_calling_provider(tmp_path):
    ctx = _ctx(tmp_path)
    _ensure(ctx)
    _produce(ctx, 'A', 11)
    restarted = _ctx(tmp_path, run_id=ctx.run_id, code=ctx.code)
    _ensure(restarted)

    with pytest.raises(ProvenanceRefused, match='业务 validator'):
        stage6_runs.restore_checkpoint(
            restarted, role='A', input_sha256=_INPUT_SHA,
            system=_SYSTEM, user=_USERS['A'], response_model=_JudgeAnswer,
            max_tokens=_MAX_TOKENS,
            validator=lambda _value, _wire: 'current alias set changed')


def test_two_judges_cannot_share_one_provider_session(tmp_path):
    ctx = _ctx(tmp_path)
    _ensure(ctx)
    _produce(ctx, 'A', 1, provider_session_id='shared-provider-session')

    with pytest.raises(ProvenanceRefused, match='provider session.*偷接旧 session'):
        _produce(ctx, 'B', 2, provider_session_id='shared-provider-session')


def test_commit_tail_is_owned_readback_checked_and_cross_process_resumable(tmp_path):
    ctx = _ctx(tmp_path)
    _ensure(ctx)
    _a, acceptance_a = _produce(ctx, 'A', 1)
    _b, acceptance_b = _produce(ctx, 'B', 2)
    artifact = {'answers': {'A': 1, 'B': 2}}
    finalization = {
        'artifact': 'stage6/semantic_1.json', 'seq': 1,
        'input_sha256': _INPUT_SHA,
    }
    adoptions = {
        'A': str(acceptance_a['attempt_id']),
        'B': str(acceptance_b['attempt_id']),
    }

    with pytest.raises(ProvenanceRefused, match='commit_ownership'):
        stage6_runs.prepare_commit(
            ctx, artifact='stage6/semantic_1.json', artifact_payload=artifact,
            finalization_payload=finalization, adoptions=adoptions)

    with stage6_runs.commit_ownership(ctx.novel_dir):
        prepared = stage6_runs.prepare_commit(
            ctx, artifact='stage6/semantic_1.json', artifact_payload=artifact,
            finalization_payload=finalization, adoptions=adoptions)
    assert stage6_runs.prepared(ctx) == prepared
    assert stage6_runs.committed(ctx) is None

    # agent prepared 是跨进程持久 reservation；即使下一次恰好走 legacy，活锁
    # 已释放后也不能按 latest+1 抢走同一 semantic seq。
    legacy = _ctx(tmp_path, code=ctx.code)
    legacy.executor_profile = None
    with stage6_runs.commit_ownership(legacy.novel_dir):
        with pytest.raises(ProvenanceRefused, match='仍持有 prepared'):
            stage6_runs.refuse_foreign_commit_reservations(
                legacy, 'stage6/semantic_1.json')

    # 模拟进程死在正式 write/readback 之后、adoption 之前：新 RunContext 只靠盘上
    # run/checkpoint/ledger/journal 恢复，不重新请求 A/B。
    restarted = _ctx(tmp_path, run_id=ctx.run_id, code=ctx.code)
    tail = stage6_runs.prepared(restarted)
    assert tail is not None
    assert stage6_runs.verify_commit_artifact(tail, artifact)
    assert stage6_runs.verify_commit_finalization(tail, finalization)
    restored = stage6_runs.restore_commit_adoptions(restarted, tail)
    for purpose, attempt_id in restored:
        restarted.ledger.note_adoption(
            sid=restarted.sid, node=restarted.node, purpose=purpose,
            attempt_id=attempt_id, artifact=str(tail['artifact']),
            artifact_fingerprint=str(tail['artifact_fingerprint']))

    with stage6_runs.commit_ownership(restarted.novel_dir):
        path = stage6_runs.mark_committed(
            restarted, artifact_payload=artifact,
            finalization_payload=finalization)
        # complete 尾窗也必须逐字幂等，不能追加 adoption 或改 journal 身份。
        assert stage6_runs.mark_committed(
            restarted, artifact_payload=artifact,
            finalization_payload=finalization) == path

    assert stage6_runs.prepared(restarted) is None
    assert stage6_runs.committed(restarted) is not None
    with stage6_runs.commit_ownership(restarted.novel_dir):
        with pytest.raises(ProvenanceRefused, match='readback'):
            stage6_runs.mark_committed(
                restarted, artifact_payload={'answers': {}},
                finalization_payload=finalization)


def test_committed_refuses_missing_one_of_two_adoptions(tmp_path):
    ctx = _ctx(tmp_path)
    _ensure(ctx)
    _a, acceptance_a = _produce(ctx, 'A', 1)
    _b, acceptance_b = _produce(ctx, 'B', 2)
    artifact = {'answers': {'A': 1, 'B': 2}}
    finalization = {'artifact': 'stage6/semantic_1.json', 'seq': 1}
    with stage6_runs.commit_ownership(ctx.novel_dir):
        record = stage6_runs.prepare_commit(
            ctx, artifact='stage6/semantic_1.json', artifact_payload=artifact,
            finalization_payload=finalization,
            adoptions={
                'A': str(acceptance_a['attempt_id']),
                'B': str(acceptance_b['attempt_id']),
            })
        ctx.ledger.note_adoption(
            sid=ctx.sid, node=ctx.node, purpose='stage6.semantic.A',
            attempt_id=str(acceptance_a['attempt_id']),
            artifact=str(record['artifact']),
            artifact_fingerprint=str(record['artifact_fingerprint']))
        with pytest.raises(ProvenanceRefused, match='stage6.semantic.B'):
            stage6_runs.mark_committed(
                ctx, artifact_payload=artifact,
                finalization_payload=finalization)
