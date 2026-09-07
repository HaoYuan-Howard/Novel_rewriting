#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage6 双盲 agent checkpoint / 提交尾窗的零成本测试。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from contracts.schema.orchestration import AgentProfile
from contracts.schema.stage6 import HookSemanticResponse
from novel import paths, prov
from novel.agents import (
    AgentDriver, AgentProbe, AgentRequest, AgentTurn, AgentUsage,
    render_structured_agent_stdin,
)
from novel.agents.session_client import SessionAgentClient, agent_json_schema
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

    def __init__(
            self, content: str, session_id: str, *, cache_hit_tokens: int = 0,
    ) -> None:
        self.content = content
        self.session_id = session_id
        self.cache_hit_tokens = cache_hit_tokens

    def probe(self) -> AgentProbe:
        return AgentProbe(
            driver='codex', provider='openai', executable='fake-codex',
            available=True, version='0.test')

    def _turn(self) -> AgentTurn:
        return AgentTurn(
            session_id=self.session_id, content=self.content,
            model=None, actual_reasoning_effort='high',
            usage=AgentUsage(
                prompt_tokens=10,
                prompt_cache_hit_tokens=self.cache_hit_tokens,
                prompt_cache_miss_tokens=10 - self.cache_hit_tokens,
                completion_tokens=2),
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


def _claude_profile() -> AgentProfile:
    return AgentProfile(
        driver='claude-code', model='claude-opus-5',
        reasoning_effort='high', execution_mode='standard')


def _ctx(base: Path, *, run_id: str | None = None,
         code: str | None = None,
         profile: AgentProfile | None = None) -> RunContext:
    novel_dir = base / '双盲测试_42'
    novel_dir.mkdir(exist_ok=True)
    ledger = (CostLedger(path=ledger_path(base)) if run_id is None else
              CostLedger(path=ledger_path(base), run_id=run_id))
    return RunContext(
        novel_dir=novel_dir, node='stage6.semantic', paid=True,
        code=code or prov.code_version(), cache=CachePolicy.default(),
        ledger=ledger, executor_profile=profile or _profile(), run_id=run_id or '')


_SYSTEM = '稳定 Stage6 系统提示'
_USERS = {'A': '匿名材料\nA', 'B': '匿名材料\nB'}
_INPUT_SHA = hashlib.sha256(b'stage6-input-projection').hexdigest()
_MAX_TOKENS = 4096


def _ensure(
        ctx: RunContext, *, judge_stagger_ms: int = 0,
) -> dict[str, object]:
    return stage6_runs.ensure_run(
        ctx, input_sha256=_INPUT_SHA, system=_SYSTEM, users=_USERS,
        response_model=_JudgeAnswer, max_tokens=_MAX_TOKENS,
        experiment_arm=stage6_runs.semantic_experiment_arm(
            judge_stagger_ms=judge_stagger_ms))


def _produce(
        ctx: RunContext, role: str, value: int, *,
        provider_session_id: str | None = None,
        cache_hit_tokens: int = 0,
) -> tuple[_JudgeAnswer, dict[str, object]]:
    purpose = f'stage6.semantic.{role}'
    client = SessionAgentClient(
        ctx, _profile(),
        _Driver(
            f'{{"value":{value}}}',
            provider_session_id or f'provider-session-{role}',
            cache_hit_tokens=cache_hit_tokens),
        max_semantic_turns=3)
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
    assert record['experiment_arm'] == {
        'session_policy': 'independent-parallel',
        'judge_stagger_ms': 0,
    }
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


def test_default_checkpoint_cannot_be_relabelled_as_a_stagger_arm(tmp_path):
    """0ms 已付费 checkpoint 不能在同 run 下冒充 25ms 实验观测。"""
    ctx = _ctx(tmp_path)
    _ensure(ctx, judge_stagger_ms=0)
    _produce(ctx, 'A', 11)

    restarted = _ctx(tmp_path, run_id=ctx.run_id, code=ctx.code)
    with pytest.raises(ProvenanceRefused, match='experiment arm.*漂移'):
        _ensure(restarted, judge_stagger_ms=25)

    # 身份拒绝发生在 restore 之前；旧 checkpoint 原样保留，换新 run 仍可审计。
    checkpoint = paths.stage6_semantic_checkpoint_path(
        ctx.novel_dir, ctx.run_id, 'A')
    assert checkpoint.is_file()


def test_legacy_run_without_an_arm_remains_the_default_zero_ms_arm(tmp_path):
    """优化前的 run.json 没有 arm 字段；只兼容成默认臂，不作通配符。"""
    ctx = _ctx(tmp_path)
    _ensure(ctx)
    run_path = stage6_runs._run_root(ctx) / stage6_runs.RUN_FILE
    historical = json.loads(run_path.read_text(encoding='utf-8'))
    historical.pop('experiment_arm')
    identity = {
        key: value for key, value in historical.items()
        if key not in ('identity_sha256', 'created_at')
    }
    historical['identity_sha256'] = stage6_runs._sha(identity)
    run_path.write_text(
        json.dumps(historical, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
        encoding='utf-8')

    restarted = _ctx(tmp_path, run_id=ctx.run_id, code=ctx.code)
    restored = _ensure(restarted)
    assert restored['experiment_arm']['judge_stagger_ms'] == 0
    with pytest.raises(ProvenanceRefused, match='experiment arm.*漂移'):
        _ensure(restarted, judge_stagger_ms=25)


def test_same_stagger_arm_restores_checkpoint_without_a_new_wire(tmp_path):
    """同一非默认臂重启仍走原 checkpoint，并保留 per-purpose session 隔离。"""
    ctx = _ctx(tmp_path)
    record = _ensure(ctx, judge_stagger_ms=25)
    _produce(ctx, 'A', 11)
    assert record['request_policy']['session_isolation'] == 'per-purpose'
    assert record['experiment_arm'] == {
        'session_policy': 'independent-concurrent-stagger/v1',
        'judge_stagger_ms': 25,
    }

    restarted = _ctx(tmp_path, run_id=ctx.run_id, code=ctx.code)
    _ensure(restarted, judge_stagger_ms=25)
    restored = stage6_runs.restore_checkpoint(
        restarted, role='A', input_sha256=_INPUT_SHA,
        system=_SYSTEM, user=_USERS['A'], response_model=_JudgeAnswer,
        max_tokens=_MAX_TOKENS)

    assert restored is not None and restored[0].value == 11
    assert restarted.ledger.calls_made == 1


def test_claude_run_hashes_each_role_final_structured_stdin(tmp_path):
    ctx = _ctx(tmp_path, profile=_claude_profile())
    record = _ensure(ctx)

    for row in record['requests']:
        role = str(row['role'])
        expected_stdin = render_structured_agent_stdin(
            _SYSTEM, _USERS[role], driver='claude-code')
        assert row['initial_prompt_sha256'] == hashlib.sha256(
            expected_stdin.encode('utf-8')).hexdigest()


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


def test_real_semantic_schema_accepts_and_restores_both_judge_responses(tmp_path):
    """真实响应含默认值与联合类型，必须以实际发送的 schema 贯穿接收和恢复。"""
    answer = HookSemanticResponse.model_validate({
        'reviews': [{
            'label': 'T1', 'coverage': 'complete',
            'uncertainty_reason': None, 'contradictions': [],
            'quality': {
                'read_on_desire': 8, 'body_voice_match': 8,
                'concrete_scene_and_specificity': 8,
                'curiosity_and_payoff_promise': 8,
                'hook_craft': 8, 'synopsis_penalty': 1,
            },
            'concise_reason': '具体场面与最后一击都成立。',
        }],
        'ranking': ['T1'],
    })
    wire_schema_sha = stage6_runs._sha(agent_json_schema(HookSemanticResponse))
    assert wire_schema_sha != stage6_runs._sha(
        HookSemanticResponse.model_json_schema(by_alias=True))
    ctx = _ctx(tmp_path)
    record = stage6_runs.ensure_run(
        ctx, input_sha256=_INPUT_SHA, system=_SYSTEM, users=_USERS,
        response_model=HookSemanticResponse, max_tokens=_MAX_TOKENS)
    for role in ('A', 'B'):
        purpose = f'stage6.semantic.{role}'
        client = SessionAgentClient(
            ctx, _profile(),
            _Driver(answer.model_dump_json(), f'real-schema-session-{role}'),
            max_semantic_turns=3)
        result = client.ask_json(
            _SYSTEM, _USERS[role], model_cls=HookSemanticResponse,
            purpose=purpose, max_tokens=_MAX_TOKENS,
            recovery_policy=stage6_runs.RECOVERY_POLICY)
        acceptance = client.accepted_checkpoint(purpose)
        stage6_runs.write_checkpoint(
            ctx, role=role, input_sha256=_INPUT_SHA,
            system=_SYSTEM, user=_USERS[role],
            response_model=HookSemanticResponse, max_tokens=_MAX_TOKENS,
            response_payload=result.model_dump(by_alias=True),
            acceptance=acceptance)
        client.finish(process_state='ok', delivery_state='delivered')
        client.close()
    assert all(row['response_schema_sha256'] == wire_schema_sha
               for row in record['requests'])
    ledger_before = ctx.ledger.path.read_bytes()
    restarted = _ctx(tmp_path, run_id=ctx.run_id, code=ctx.code)
    stage6_runs.ensure_run(
        restarted, input_sha256=_INPUT_SHA, system=_SYSTEM, users=_USERS,
        response_model=HookSemanticResponse, max_tokens=_MAX_TOKENS)
    for role in ('A', 'B'):
        restored = stage6_runs.restore_checkpoint(
            restarted, role=role, input_sha256=_INPUT_SHA,
            system=_SYSTEM, user=_USERS[role],
            response_model=HookSemanticResponse, max_tokens=_MAX_TOKENS)
        assert restored is not None and restored[0] == answer
        assert restored[1]['accepted_schema_sha256'] == wire_schema_sha
    assert restarted.ledger.calls_made == 2
    assert ctx.ledger.path.read_bytes() == ledger_before


def test_independent_judge_ledger_keeps_provider_cache_hit_observation(tmp_path):
    """静态公共前缀不等于命中；实验只认每个独立 wire 的 provider usage。"""
    ctx = _ctx(tmp_path)
    _ensure(ctx)
    _a, acceptance_a = _produce(ctx, 'A', 11, cache_hit_tokens=0)
    _b, acceptance_b = _produce(ctx, 'B', 22, cache_hit_tokens=7)

    rows = [json.loads(line) for line in ctx.ledger.path.read_text(
        encoding='utf-8').splitlines()]
    by_purpose = {row['purpose']: row for row in rows}
    assert by_purpose['stage6.semantic.A']['prompt_cache_hit_tokens'] == 0
    assert by_purpose['stage6.semantic.A']['prompt_cache_miss_tokens'] == 10
    assert by_purpose['stage6.semantic.B']['prompt_cache_hit_tokens'] == 7
    assert by_purpose['stage6.semantic.B']['prompt_cache_miss_tokens'] == 3
    assert acceptance_a['session_run_id'] != acceptance_b['session_run_id']
    assert acceptance_a['provider_session_id'] != acceptance_b['provider_session_id']


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
