#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 验证后 checkpoint、一次性 batch workpack 与 run_id 闭环。"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from contracts.schema.orchestration import AgentProfile
from novel import agent_session_audit
from novel.agents.base import render_structured_agent_stdin
from novel.agents.session_client import agent_request_fingerprint
from novel.errors import (
    ArtifactUnreadable, ProvenanceRefused, ResponseRepairBatchRequired,
    ResponseRepairRequired, TerminalState,
)
from novel.llm import response_repair, stage2_runs
from novel.llm.cache import CachePolicy, request_fingerprint
from novel.llm.client import (
    FLASH_MODEL, JSON_OBJECT_MODE, PRO_FALLBACK_MODEL, LlmClient, LlmFailed,
)
from novel.llm.ledger import CostLedger
from novel.llm.transport import ChatRequest, ChatResponse, FakeTransport, TransportError
from novel.run import RunContext
from novel.run_identity import new_run_id
from novel.stages.stage2 import extract


class _ChapterRows(BaseModel):
    blueprints: list[dict] = Field(min_length=1)


class _CoercingBlueprint(BaseModel):
    chapter_ordinal: int
    normalized_default: str = 'filled-by-schema'


class _CoercingChapterRows(BaseModel):
    blueprints: list[_CoercingBlueprint] = Field(min_length=1)


def _request_binding(request: ChatRequest) -> dict[str, object]:
    return {
        'request_model': request.model,
        'reasoning_effort': request.reasoning_effort,
        'thinking': request.thinking,
        'json_mode': request.json_mode,
        'max_tokens': request.max_tokens,
        'temperature': request.temperature,
        'system_sha256': hashlib.sha256(request.system.encode()).hexdigest(),
        'user_sha256': hashlib.sha256(request.user.encode()).hexdigest(),
        'request_fingerprint': request_fingerprint(request),
    }


def _checkpoint_hash(payload: dict[str, object]) -> str:
    projected = {key: value for key, value in payload.items()
                 if key not in ('created_at', 'checkpoint_sha256')}
    raw = json.dumps(
        projected, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _canonical_sha(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _checkpoint_acceptance(
        ctx: RunContext, *, attempt_id: str, purpose: str,
        system: str, user: str, raw_text: str, normalized_text: str,
        channel: str = 'content', effort: str = 'high', thinking: bool = True,
        max_tokens: int = 64_000,
) -> dict[str, object]:
    request = ChatRequest(
        model='test-model', system=system, user=user,
        reasoning_effort=effort, thinking=thinking,
        json_mode=JSON_OBJECT_MODE, max_tokens=max_tokens)
    provider_content = raw_text if channel == 'content' else ''
    ctx.ledger.record(
        node=ctx.node, sid=ctx.sid, model='test-model', request_model='test-model',
        code=ctx.code, cached=False, cache_policy='default', purpose=purpose,
        outcome='ok', attempt_id=attempt_id,
        request_fingerprint=request_fingerprint(request),
        response_sha256=hashlib.sha256(provider_content.encode()).hexdigest(),
        reasoning_effort=effort, thinking=thinking,
        json_mode=JSON_OBJECT_MODE, max_tokens=max_tokens,
        prompt_tokens=10, completion_tokens=20, reasoning_tokens=15,
    )
    identity = ctx.ledger.request_identity_for_attempt(attempt_id)
    return {
        'attempt_id': attempt_id,
        'request_fingerprint': request_fingerprint(request),
        'model': 'test-model',
        'ledger_response_sha256': identity['response_sha256'],
        'accepted_channel': channel,
        'accepted_text': raw_text,
        'normalized_text': normalized_text,
        'accepted_request': _request_binding(request),
        'model_chain': ['test-model'],
        'rungs': [[effort, thinking]],
        'attempt_links': [identity],
        'recovery_mode': 'original',
        'request_mode': 'original',
        'recovery_from_attempts': [],
        'salvaged': channel == 'reasoning',
    }


class _SyntheticChapterClient:
    concurrency = 3

    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx
        self._acceptance: dict[str, dict[str, object]] = {}

    def ask_json(self, system, user, *, model_cls, purpose, **kwargs):
        ordinal = int(purpose.rsplit('ch', 1)[1])
        attempt_id = f'{ordinal:016x}'
        text = json.dumps({'blueprints': [{'chapter_ordinal': ordinal}]})
        self._acceptance[purpose] = _checkpoint_acceptance(
            self.ctx, attempt_id=attempt_id, purpose=purpose,
            system=system, user=user, raw_text=text, normalized_text=text,
            max_tokens=int(kwargs['max_tokens']))
        return model_cls.model_validate({
            'blueprints': [{'chapter_ordinal': ordinal}],
        })

    def accepted_checkpoint(self, purpose):
        return self._acceptance[purpose]

    def restore_checkpoint_acceptance(self, _purpose, _record, **_kwargs):
        raise AssertionError('本测试不走 restore')


def _ctx(
        tmp_path: Path, run_id: str, *, observer=None, repair: bool = False,
        executor_profile: AgentProfile | None = None,
) -> RunContext:
    book = tmp_path / '恢复测试_123'
    clean = book / 'stage0' / 'clean_text.txt'
    clean.parent.mkdir(parents=True, exist_ok=True)
    clean.write_text('synthetic root\n', encoding='utf-8', newline='')
    return RunContext(
        novel_dir=book,
        node='stage2.blueprint',
        paid=True,
        code='test-stage2-runs',
        cache=CachePolicy.default(),
        ledger=CostLedger(
            path=tmp_path / '_service' / 'ledger.jsonl', run_id=run_id),
        run_id=run_id,
        executor_profile=executor_profile,
        repair_rejected_responses=repair,
        observer=observer or (lambda _payload: None),
    )


def _test_request_policy(
        *, model_chain: tuple[str, ...] = ('test-model',), effort: str = 'high',
        thinking: bool = True, max_tokens: int = 64_000,
) -> dict[str, object]:
    schema_sha = hashlib.sha256(json.dumps(
        _ChapterRows.model_json_schema(by_alias=True), ensure_ascii=False,
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {
        'model_chain': list(model_chain),
        'rungs': [[effort, thinking]],
        'json_mode': True,
        'temperature': None,
        'recovery_policy': 'stage2-blueprint-v1',
        'phases': [{
            'phase': 'blueprint',
            'response_model': _ChapterRows.__name__,
            'response_schema_sha256': schema_sha,
            'max_tokens': max_tokens,
            'prefer_content_over_reasoning_salvage': False,
        }],
    }


_AGENT_PROFILE = AgentProfile(
    driver='codex', model='gpt-5.6-sol', reasoning_effort='high',
    execution_mode='standard')
_CLAUDE_AGENT_PROFILE = AgentProfile(
    driver='claude-code', model='claude-opus-5', reasoning_effort='high',
    execution_mode='standard')


def _agent_request_policy(
        *, profile: AgentProfile = _AGENT_PROFILE,
        max_tokens: int = 64_000,
) -> dict[str, object]:
    return {
        'protocol_version': 'node-executor/v1',
        'profile': profile.model_dump(mode='json'),
        'session_isolation': 'per-purpose-chapter',
        'recovery_policy': 'stage2-blueprint-v1',
        'phases': [{
            'phase': 'blueprint',
            'response_model': _ChapterRows.__name__,
            'response_schema_sha256': _canonical_sha(
                _ChapterRows.model_json_schema(by_alias=True)),
            'requested_output_token_ceiling': max_tokens,
            'prefer_content_over_reasoning_salvage': False,
        }],
    }


def _agent_checkpoint_acceptance(
        ctx: RunContext, *, purpose: str, system: str, user: str,
        texts: list[tuple[str, str]], max_tokens: int = 64_000,
) -> dict[str, object]:
    """登记若干 agent turns，并返回最后一轮的 SessionClient 同形 acceptance。"""
    assert ctx.executor_profile is not None and texts
    profile = ctx.executor_profile.model_dump(mode='json')
    schema_sha = _canonical_sha(_ChapterRows.model_json_schema(by_alias=True))
    binding = {
        'node': ctx.node,
        'chapter': 1,
        'purpose': purpose,
        'system_sha256': hashlib.sha256(system.encode()).hexdigest(),
        'user_sha256': hashlib.sha256(user.encode()).hexdigest(),
        'schema_sha256': schema_sha,
        'requested_model': profile['model'],
        'requested_effort': profile['reasoning_effort'],
        'requested_output_token_ceiling': max_tokens,
        'protocol_version': 'node-executor/v1',
    }
    subject_sha = _canonical_sha(binding)
    session_run_id = f'session-{texts[-1][0]}'
    provider_session_id = f'provider-{texts[-1][0]}'
    initial_prompt_sha = hashlib.sha256(
        render_structured_agent_stdin(
            system, user, driver=str(profile['driver']),
        ).encode()).hexdigest()
    # synthetic audit 也必须遵守生产不变式：started 与该 session
    # 第一条 turn 绑定同一份真实 stdin，后续轮才是反馈 prompt。
    prompt_shas = [initial_prompt_sha, *[
        hashlib.sha256(f'agent stdin {index}'.encode()).hexdigest()
        for index in range(2, len(texts) + 1)
    ]]
    agent_session_audit.append_event(
        ctx.novel_dir.parent,
        agent_session_audit.AgentSessionEvent(
            session_run_id=session_run_id, parent_run_id=ctx.run_id,
            role='executor', node=ctx.node, chapter=1,
            subject_sha256=subject_sha, profile=ctx.executor_profile,
            requested_model=ctx.executor_profile.model,
            requested_effort=ctx.executor_profile.reasoning_effort,
            cli_version='synthetic-cli', protocol_version='node-executor/v1',
            start_code_version=ctx.code, prompt_sha256=initial_prompt_sha,
            schema_sha256=schema_sha, kind='started', ts=1)),
    links: list[dict[str, object]] = []
    for index, (attempt_id, text) in enumerate(texts, 1):
        fingerprint = agent_request_fingerprint(
            profile=profile,
            subject_sha256=subject_sha,
            provider_session_id=(None if index == 1 else provider_session_id),
            system_sha256=(binding['system_sha256'] if index == 1 else None),
            prompt_sha256=prompt_shas[index - 1],
            schema_sha256=schema_sha,
            purpose=purpose,
            turn_index=index,
        )
        ctx.ledger.record(
            node=ctx.node, sid=ctx.sid, model=ctx.executor_profile.model,
            request_model=ctx.executor_profile.model, code=ctx.code,
            cached=False, cache_policy='agent-session', purpose=purpose,
            outcome='ok', attempt_id=attempt_id,
            request_fingerprint=fingerprint,
            response_sha256=hashlib.sha256(text.encode()).hexdigest(),
            reasoning_effort=ctx.executor_profile.reasoning_effort,
            thinking=None, json_mode=True, max_tokens=None,
            prompt_tokens=10, completion_tokens=20, reasoning_tokens=15,
        )
        links.append(ctx.ledger.request_identity_for_attempt(attempt_id))
        agent_session_audit.append_event(
            ctx.novel_dir.parent,
            agent_session_audit.AgentSessionEvent(
                session_run_id=session_run_id, parent_run_id=ctx.run_id,
                role='executor', node=ctx.node, chapter=1,
                subject_sha256=subject_sha, profile=ctx.executor_profile,
                provider_session_id=provider_session_id,
                turn_index=index, attempt_id=attempt_id,
                requested_model=ctx.executor_profile.model,
                actual_model=ctx.executor_profile.model,
                requested_effort=ctx.executor_profile.reasoning_effort,
                actual_effort=ctx.executor_profile.reasoning_effort,
                cli_version='synthetic-cli',
                protocol_version='node-executor/v1',
                start_code_version=ctx.code, prompt_sha256=prompt_shas[index - 1],
                schema_sha256=schema_sha, kind='turn', ts=index + 1))
    links.sort(key=lambda row: (
        str(row['attempt_id']), str(row['request_fingerprint'])))
    accepted = links[-1]
    accepted_text = texts[-1][1]
    return {
        'acceptance_protocol': 'node-executor/v1',
        'attempt_id': accepted['attempt_id'],
        'request_fingerprint': accepted['request_fingerprint'],
        'model': accepted['model'],
        'ledger_response_sha256': accepted['response_sha256'],
        'accepted_channel': 'content',
        'accepted_text': accepted_text,
        'normalized_text': accepted_text,
        'attempt_links': links,
        'profile': profile,
        'subject_sha256': subject_sha,
        'session_run_id': session_run_id,
        'provider_session_id': provider_session_id,
        'accepted_turn_index': len(links),
        'accepted_prompt_sha256': prompt_shas[-1],
        'accepted_schema_sha256': schema_sha,
        'accepted_request': binding,
        'request_mode': (
            'original' if len(links) == 1 else 'agent_session_resume'),
        'recovery_from_attempts': [
            row['attempt_id'] for row in links[:-1]],
        'salvaged': False,
    }


def _ensure_run(
        ctx: RunContext, *, architecture: str, topology: str,
        requests: list[tuple[int | None, str, str]],
        request_policy: dict[str, object] | None = None,
        refresh: bool = False,
) -> dict[str, object]:
    return stage2_runs.ensure_run(
        ctx, architecture=architecture, topology=topology, requests=requests,
        request_policy=request_policy or _test_request_policy(), refresh=refresh)


def test_agent_request_policy_is_discriminated_and_legacy_stays_exact(tmp_path):
    requests = [(1, 'SYSTEM', 'USER')]
    agent = _ctx(
        tmp_path, new_run_id(), executor_profile=_AGENT_PROFILE)
    agent_policy = _agent_request_policy(max_tokens=2048)
    agent_run = _ensure_run(
        agent, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=requests,
        request_policy=agent_policy)

    assert agent_run['request_policy'] == agent_policy
    assert set(agent_run['request_policy']) == {
        'protocol_version', 'profile', 'session_isolation',
        'recovery_policy', 'phases'}
    assert not ({'model_chain', 'rungs', 'thinking', 'max_tokens'}
                & set(agent_run['request_policy']))
    phase = agent_run['request_policy']['phases'][0]
    assert phase['requested_output_token_ceiling'] == 2048
    assert 'max_tokens' not in phase

    legacy = _ctx(tmp_path, new_run_id())
    legacy_policy = _test_request_policy(max_tokens=2048)
    legacy_run = _ensure_run(
        legacy, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=requests,
        request_policy=legacy_policy)
    assert legacy_run['request_policy'] == legacy_policy
    assert 'protocol_version' not in legacy_run['request_policy']


@pytest.mark.parametrize('case', [
    'unknown_protocol', 'legacy_field', 'unresolved_profile',
    'wrong_isolation', 'provider_max_tokens',
])
def test_agent_request_policy_rejects_ambiguous_or_unresolved_shape(
        tmp_path, case):
    run_id = new_run_id()
    ctx = _ctx(tmp_path, run_id, executor_profile=_AGENT_PROFILE)
    policy = _agent_request_policy(max_tokens=2048)
    if case == 'unknown_protocol':
        policy['protocol_version'] = 'node-executor/v2'
    elif case == 'legacy_field':
        policy['model_chain'] = ['gpt-5.6-sol']
    elif case == 'unresolved_profile':
        policy['profile'] = {
            **dict(policy['profile']), 'execution_mode': None}
    elif case == 'wrong_isolation':
        policy['session_isolation'] = 'shared-book'
    else:
        phase = dict(policy['phases'][0])
        phase['max_tokens'] = phase.pop('requested_output_token_ceiling')
        policy['phases'] = [phase]

    with pytest.raises(ProvenanceRefused, match='agent|profile|policy'):
        _ensure_run(
            ctx, architecture='extraction-ir-v3.1',
            topology='scaffolded_single',
            requests=[(1, 'SYSTEM', 'USER')], request_policy=policy)
    assert not (stage2_runs.paths.stage2_blueprint_run_path(
        ctx.novel_dir, run_id) / stage2_runs.RUN_FILE).exists()


def test_agent_checkpoint_restores_full_session_and_ledger_with_zero_wire(
        tmp_path):
    run_id = new_run_id()
    purpose = 'stage2.blueprint:ch1'
    requests = [(1, 'SYSTEM', 'USER')]
    accepted_text = '{"blueprints":[{"chapter_ordinal":1}]}'
    first = _ctx(
        tmp_path, run_id, executor_profile=_AGENT_PROFILE)
    policy = _agent_request_policy(max_tokens=2048)
    _ensure_run(
        first, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=requests,
        request_policy=policy)
    acceptance = _agent_checkpoint_acceptance(
        first, purpose=purpose, system='SYSTEM', user='USER',
        texts=[('1' * 16, '{"blueprints":[]}'),
               ('2' * 16, accepted_text)], max_tokens=2048)
    checkpoint = stage2_runs.write_checkpoint(
        first, phase='blueprint', chapter=1, purpose=purpose,
        architecture='extraction-ir-v3.1', response_model=_ChapterRows,
        system='SYSTEM', user='USER',
        response_payload={'blueprints': [{'chapter_ordinal': 1}]},
        acceptance=acceptance, scope_violation=None)
    stored = json.loads(checkpoint.read_text(encoding='utf-8'))
    assert stored['acceptance_protocol'] == 'node-executor/v1'
    assert stored['accepted_text'] == accepted_text
    assert stored['accepted_turn_index'] == 2
    assert stored['recovery_from_attempts'] == ['1' * 16]
    assert not ({'model_chain', 'rungs', 'repair_resolution', 'recovery_mode'}
                & set(stored))

    resumed = _ctx(
        tmp_path, run_id, executor_profile=_AGENT_PROFILE)
    _ensure_run(
        resumed, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=requests,
        request_policy=policy)
    restored = stage2_runs.restore_checkpoint(
        resumed, phase='blueprint', chapter=1, purpose=purpose,
        architecture='extraction-ir-v3.1', response_model=_ChapterRows,
        system='SYSTEM', user='USER')

    assert restored == stored
    assert resumed.ledger.calls_made == 2
    assert resumed.ledger.attempt_ids_for_purpose(purpose) == (
        '1' * 16, '2' * 16)
    assert resumed.ledger.request_identity_for_attempt('2' * 16) \
        == stored['attempt_links'][1]


def test_claude_checkpoint_audit_hashes_the_final_structured_stdin(tmp_path):
    run_id = new_run_id()
    purpose = 'stage2.blueprint:ch1'
    ctx = _ctx(
        tmp_path, run_id, executor_profile=_CLAUDE_AGENT_PROFILE)
    policy = _agent_request_policy(
        profile=_CLAUDE_AGENT_PROFILE, max_tokens=2048)
    _ensure_run(
        ctx, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=[(1, 'SYSTEM', 'USER')],
        request_policy=policy)
    text = '{"blueprints":[{"chapter_ordinal":1}]}'
    acceptance = _agent_checkpoint_acceptance(
        ctx, purpose=purpose, system='SYSTEM', user='USER',
        texts=[('c' * 16, text)], max_tokens=2048)

    [started] = [
        event for event in agent_session_audit.read_events(tmp_path)
        if event.kind == 'started']
    expected_stdin = render_structured_agent_stdin(
        'SYSTEM', 'USER', driver='claude-code')
    assert started.prompt_sha256 == hashlib.sha256(
        expected_stdin.encode('utf-8')).hexdigest()

    checkpoint = stage2_runs.write_checkpoint(
        ctx, phase='blueprint', chapter=1, purpose=purpose,
        architecture='extraction-ir-v3.1', response_model=_ChapterRows,
        system='SYSTEM', user='USER',
        response_payload={'blueprints': [{'chapter_ordinal': 1}]},
        acceptance=acceptance, scope_violation=None)
    assert checkpoint.is_file()


@pytest.mark.parametrize('case', [
    'profile', 'actual_model', 'subject', 'schema', 'ceiling', 'stdout', 'session_turn',
    'provider_max_tokens',
])
def test_agent_checkpoint_write_rejects_each_unclosed_identity(tmp_path, case):
    run_id = new_run_id()
    purpose = 'stage2.blueprint:ch1'
    ctx = _ctx(tmp_path, run_id, executor_profile=_AGENT_PROFILE)
    _ensure_run(
        ctx, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=[(1, 'SYSTEM', 'USER')],
        request_policy=_agent_request_policy(max_tokens=2048))
    text = '{"blueprints":[{"chapter_ordinal":1}]}'
    acceptance = _agent_checkpoint_acceptance(
        ctx, purpose=purpose, system='SYSTEM', user='USER',
        texts=[('3' * 16, text)], max_tokens=2048)
    if case == 'profile':
        acceptance['profile'] = AgentProfile(
            driver='codex', model='gpt-5.6-terra', reasoning_effort='high',
            execution_mode='standard').model_dump(mode='json')
    elif case == 'actual_model':
        acceptance['model'] = 'gpt-5.6-terra'
    elif case == 'subject':
        acceptance['subject_sha256'] = '0' * 64
    elif case == 'schema':
        acceptance['accepted_schema_sha256'] = '0' * 64
    elif case == 'ceiling':
        acceptance['accepted_request'] = {
            **dict(acceptance['accepted_request']),
            'requested_output_token_ceiling': 4096,
        }
    elif case == 'stdout':
        acceptance['accepted_text'] = text + ' '
    elif case == 'session_turn':
        acceptance['session_run_id'] = 'session-missing'
    else:
        acceptance['attempt_links'][0]['max_tokens'] = 2048

    with pytest.raises(ProvenanceRefused):
        stage2_runs.write_checkpoint(
            ctx, phase='blueprint', chapter=1, purpose=purpose,
            architecture='extraction-ir-v3.1', response_model=_ChapterRows,
            system='SYSTEM', user='USER',
            response_payload={'blueprints': [{'chapter_ordinal': 1}]},
            acceptance=acceptance, scope_violation=None)
    assert not stage2_runs.paths.stage2_blueprint_checkpoint_path(
        ctx.novel_dir, run_id, 'blueprint', 1).exists()


def test_agent_checkpoint_restore_rechecks_session_and_never_overwrites_first(
        tmp_path):
    run_id = new_run_id()
    purpose = 'stage2.blueprint:ch1'
    ctx = _ctx(tmp_path, run_id, executor_profile=_AGENT_PROFILE)
    _ensure_run(
        ctx, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=[(1, 'SYSTEM', 'USER')],
        request_policy=_agent_request_policy(max_tokens=2048))
    text = '{"blueprints":[{"chapter_ordinal":1}]}'
    first_acceptance = _agent_checkpoint_acceptance(
        ctx, purpose=purpose, system='SYSTEM', user='USER',
        texts=[('4' * 16, text)], max_tokens=2048)
    checkpoint = stage2_runs.write_checkpoint(
        ctx, phase='blueprint', chapter=1, purpose=purpose,
        architecture='extraction-ir-v3.1', response_model=_ChapterRows,
        system='SYSTEM', user='USER',
        response_payload={'blueprints': [{'chapter_ordinal': 1}]},
        acceptance=first_acceptance, scope_violation=None)
    before = checkpoint.read_bytes()

    replacement = _agent_checkpoint_acceptance(
        ctx, purpose=purpose, system='SYSTEM', user='USER',
        texts=[('5' * 16, text)], max_tokens=2048)
    with pytest.raises(ProvenanceRefused, match='拒绝覆盖'):
        stage2_runs.write_checkpoint(
            ctx, phase='blueprint', chapter=1, purpose=purpose,
            architecture='extraction-ir-v3.1', response_model=_ChapterRows,
            system='SYSTEM', user='USER',
            response_payload={'blueprints': [{'chapter_ordinal': 1}]},
            acceptance=replacement, scope_violation=None)
    assert checkpoint.read_bytes() == before

    tampered = json.loads(before.decode('utf-8'))
    # 非空但错误，确保 restore 失败来自 checkpoint ↔ audit turn 漂移，
    # 而不是先被普通字符串形状校验挡住。
    tampered['provider_session_id'] = 'provider-tampered'
    tampered['checkpoint_sha256'] = _checkpoint_hash(tampered)
    checkpoint.write_text(json.dumps(tampered, ensure_ascii=False), encoding='utf-8')
    resumed = _ctx(
        tmp_path, run_id, executor_profile=_AGENT_PROFILE)
    with pytest.raises(ProvenanceRefused, match='session'):
        stage2_runs.restore_checkpoint(
            resumed, phase='blueprint', chapter=1, purpose=purpose,
            architecture='extraction-ir-v3.1', response_model=_ChapterRows,
            system='SYSTEM', user='USER')


def test_agent_checkpoint_restore_refuses_legacy_prior_ledger_actual_model_drift(
        tmp_path):
    """旧写入器可能将前一轮的实际模型同时写入 checkpoint/ledger；
    两份相互自洽仍不能替代 resolved profile 这第三把尺。
    """
    run_id = new_run_id()
    purpose = 'stage2.blueprint:ch1'
    first_attempt = '6' * 16
    accepted_attempt = '7' * 16
    ctx = _ctx(tmp_path, run_id, executor_profile=_AGENT_PROFILE)
    policy = _agent_request_policy(max_tokens=2048)
    _ensure_run(
        ctx, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=[(1, 'SYSTEM', 'USER')],
        request_policy=policy)
    text = '{"blueprints":[{"chapter_ordinal":1}]}'
    acceptance = _agent_checkpoint_acceptance(
        ctx, purpose=purpose, system='SYSTEM', user='USER',
        texts=[(first_attempt, '{"blueprints":[]}'),
               (accepted_attempt, text)], max_tokens=2048)
    checkpoint = stage2_runs.write_checkpoint(
        ctx, phase='blueprint', chapter=1, purpose=purpose,
        architecture='extraction-ir-v3.1', response_model=_ChapterRows,
        system='SYSTEM', user='USER',
        response_payload={'blueprints': [{'chapter_ordinal': 1}]},
        acceptance=acceptance, scope_violation=None)

    # 模拟旧 checkpoint：前一轮 checkpoint link 与盘上 ledger 都声称
    # 实际返回自另一个已知模型，而最终 accepted turn 仍是 profile 模型。
    stored = json.loads(checkpoint.read_text(encoding='utf-8'))
    prior_link = next(
        row for row in stored['attempt_links']
        if row['attempt_id'] == first_attempt)
    prior_link['model'] = 'gpt-5.6-terra'
    stored['checkpoint_sha256'] = _checkpoint_hash(stored)
    checkpoint.write_text(
        json.dumps(stored, ensure_ascii=False), encoding='utf-8')

    assert ctx.ledger.path is not None
    ledger_rows = [
        json.loads(line) for line in ctx.ledger.path.read_text(
            encoding='utf-8').splitlines() if line.strip()
    ]
    prior_ledger = next(
        row for row in ledger_rows if row.get('attempt_id') == first_attempt)
    prior_ledger['model'] = 'gpt-5.6-terra'
    ctx.ledger.path.write_text(
        ''.join(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n'
                for row in ledger_rows),
        encoding='utf-8', newline='')

    # 旧的失败 turn 可能没有 provider actual_model；保持 audit 事件流
    # 合法，使失败精确落在 ledger actual model 与 profile 的漂移。
    audit_path = agent_session_audit.audit_path(ctx.novel_dir.parent)
    audit_rows = [
        json.loads(line) for line in audit_path.read_text(
            encoding='utf-8').splitlines() if line.strip()
    ]
    prior_turn = next(
        row for row in audit_rows
        if row.get('kind') == 'turn'
        and row.get('attempt_id') == first_attempt)
    prior_turn['actual_model'] = None
    audit_path.write_text(
        ''.join(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n'
                for row in audit_rows),
        encoding='utf-8', newline='')

    resumed = _ctx(
        tmp_path, run_id, executor_profile=_AGENT_PROFILE)
    _ensure_run(
        resumed, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=[(1, 'SYSTEM', 'USER')],
        request_policy=policy)
    with pytest.raises(ProvenanceRefused, match='resolved profile'):
        stage2_runs.restore_checkpoint(
            resumed, phase='blueprint', chapter=1, purpose=purpose,
            architecture='extraction-ir-v3.1', response_model=_ChapterRows,
            system='SYSTEM', user='USER')


@pytest.mark.parametrize(('field', 'value', 'message'), [
    ('actual_model', None, 'actual model'),
    ('actual_model', 'gpt-5.6-terra', 'actual model'),
    ('actual_effort', 'max', 'actual effort'),
])
def test_agent_checkpoint_restore_refuses_legacy_audit_actual_profile_drift(
        tmp_path, field, value, message):
    """accepted turn 的供应商实际值必须与 resolved profile 精确闭合。"""
    run_id = new_run_id()
    purpose = 'stage2.blueprint:ch1'
    attempt_id = '8' * 16
    ctx = _ctx(tmp_path, run_id, executor_profile=_AGENT_PROFILE)
    policy = _agent_request_policy(max_tokens=2048)
    _ensure_run(
        ctx, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=[(1, 'SYSTEM', 'USER')],
        request_policy=policy)
    text = '{"blueprints":[{"chapter_ordinal":1}]}'
    acceptance = _agent_checkpoint_acceptance(
        ctx, purpose=purpose, system='SYSTEM', user='USER',
        texts=[(attempt_id, text)], max_tokens=2048)
    stage2_runs.write_checkpoint(
        ctx, phase='blueprint', chapter=1, purpose=purpose,
        architecture='extraction-ir-v3.1', response_model=_ChapterRows,
        system='SYSTEM', user='USER',
        response_payload={'blueprints': [{'chapter_ordinal': 1}]},
        acceptance=acceptance, scope_violation=None)

    audit_path = agent_session_audit.audit_path(ctx.novel_dir.parent)
    audit_rows = [
        json.loads(line) for line in audit_path.read_text(
            encoding='utf-8').splitlines() if line.strip()
    ]
    accepted_turn = next(
        row for row in audit_rows
        if row.get('kind') == 'turn'
        and row.get('attempt_id') == attempt_id)
    accepted_turn[field] = value
    audit_path.write_text(
        ''.join(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n'
                for row in audit_rows),
        encoding='utf-8', newline='')

    resumed = _ctx(
        tmp_path, run_id, executor_profile=_AGENT_PROFILE)
    _ensure_run(
        resumed, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=[(1, 'SYSTEM', 'USER')],
        request_policy=policy)
    with pytest.raises(ProvenanceRefused, match=message):
        stage2_runs.restore_checkpoint(
            resumed, phase='blueprint', chapter=1, purpose=purpose,
            architecture='extraction-ir-v3.1', response_model=_ChapterRows,
            system='SYSTEM', user='USER')


def test_validated_checkpoint_restores_cost_and_acceptance_with_zero_wire(tmp_path):
    run_id = new_run_id()
    purpose = 'stage2.blueprint:ch1'
    attempt_id = 'a' * 16
    requests = [(1, 'SYSTEM', 'USER')]

    first = _ctx(tmp_path, run_id)
    _ensure_run(
        first, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=requests)
    normalized = '{"blueprints":[{"chapter_ordinal":1}]}'
    raw_reasoning = f'```json\n{normalized}\n```'
    acceptance = _checkpoint_acceptance(
        first, attempt_id=attempt_id, purpose=purpose,
        system='SYSTEM', user='USER', raw_text=raw_reasoning,
        normalized_text=normalized, channel='reasoning')
    stage2_runs.write_checkpoint(
        first, phase='blueprint', chapter=1, purpose=purpose,
        architecture='extraction-ir-v3.1', response_model=_ChapterRows,
        system='SYSTEM', user='USER',
        response_payload={'blueprints': [{'chapter_ordinal': 1}]},
        acceptance=acceptance,
        scope_violation=None,
    )

    events: list[dict[str, object]] = []
    resumed = _ctx(tmp_path, run_id, observer=events.append)
    _ensure_run(
        resumed, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=requests)
    record = stage2_runs.restore_checkpoint(
        resumed, phase='blueprint', chapter=1, purpose=purpose,
        architecture='extraction-ir-v3.1', response_model=_ChapterRows,
        system='SYSTEM', user='USER')
    transport = FakeTransport(lambda _req, _index: AssertionError('checkpoint 不应上线'))
    client = LlmClient(
        resumed, transport, model='test-model', enable_model_fallback=False,
        sleep=lambda _seconds: None)
    assert record is not None
    client.restore_checkpoint_acceptance(
        purpose, record, system='SYSTEM', user='USER', max_tokens=64_000,
        recovery_policy='stage2-blueprint-v1')

    assert record is not None and record['attempt_id'] == attempt_id
    assert record['accepted_text'] == raw_reasoning
    assert record['normalized_text'] == normalized
    assert resumed.ledger.calls_made == 1 and resumed.ledger.calls_cached == 0
    assert transport.calls == [] and events == []


def test_checkpoint_acceptance_missing_or_invalid_identity_fails_closed(tmp_path):
    ctx = _ctx(tmp_path, new_run_id())
    client = LlmClient(
        ctx, FakeTransport.always(ChatResponse(content='{}')),
        cache=None, model='test-model', enable_model_fallback=False,
        sleep=lambda _seconds: None,
    )
    with pytest.raises(ProvenanceRefused, match='没有 checkpoint'):
        client.accepted_checkpoint('stage2.blueprint:ch1')
    with pytest.raises(ProvenanceRefused, match='channel/raw-text'):
        client.restore_checkpoint_acceptance('stage2.blueprint:ch1', {
            'attempt_id': 'a' * 16,
            'attempt_links': [{'attempt_id': 'a' * 16}],
            'accepted_text': '{}',
            'normalized_text': '{}',
            'accepted_channel': 'unknown',
            'model': 'test-model',
        })
    with pytest.raises(ProvenanceRefused, match='不在当前模型链'):
        client.restore_checkpoint_acceptance('stage2.blueprint:ch1', {
            'attempt_id': 'a' * 16,
            'attempt_links': [{'attempt_id': 'a' * 16}],
            'accepted_text': '{}',
            'normalized_text': '{}',
            'accepted_channel': 'content',
            'model': 'test-model-202608',
            'model_chain': ['test-model'],
            'rungs': [['high', True]],
            'accepted_request': {'request_model': 'other-model'},
        }, system='SYSTEM', user='USER', max_tokens=64_000)


def test_checkpoint_drift_corruption_and_cross_run_copy_fail_closed(tmp_path):
    run_id = new_run_id()
    purpose = 'stage2.blueprint:ch1'
    attempt_id = 'c' * 16
    ctx = _ctx(tmp_path, run_id)
    requests = [(1, 'SYSTEM', 'USER')]
    _ensure_run(
        ctx, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=requests)
    normalized = '{"blueprints":[{"chapter_ordinal":1}]}'
    acceptance = _checkpoint_acceptance(
        ctx, attempt_id=attempt_id, purpose=purpose,
        system='SYSTEM', user='USER', raw_text=normalized,
        normalized_text=normalized)
    checkpoint = stage2_runs.write_checkpoint(
        ctx, phase='blueprint', chapter=1, purpose=purpose,
        architecture='extraction-ir-v3.1', response_model=_ChapterRows,
        system='SYSTEM', user='USER',
        response_payload={'blueprints': [{'chapter_ordinal': 1}]},
        acceptance=acceptance,
        scope_violation=None,
    )

    with pytest.raises(ProvenanceRefused, match='输入绑定'):
        stage2_runs.restore_checkpoint(
            ctx, phase='blueprint', chapter=1, purpose=purpose,
            architecture='extraction-ir-v3.1', response_model=_ChapterRows,
            system='SYSTEM', user='CHANGED')

    other_run = new_run_id()
    copied = stage2_runs.paths.stage2_blueprint_checkpoint_path(
        ctx.novel_dir, other_run, 'blueprint', 1)
    copied.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(checkpoint, copied)
    other = _ctx(tmp_path, other_run)
    with pytest.raises(ProvenanceRefused, match='run/代码/契约/输入绑定'):
        stage2_runs.restore_checkpoint(
            other, phase='blueprint', chapter=1, purpose=purpose,
            architecture='extraction-ir-v3.1', response_model=_ChapterRows,
            system='SYSTEM', user='USER')

    raw = json.loads(checkpoint.read_text(encoding='utf-8'))
    raw['accepted_text'] += ' '
    checkpoint.write_text(
        json.dumps(raw, ensure_ascii=False), encoding='utf-8', newline='')
    with pytest.raises(ArtifactUnreadable, match='checkpoint_sha256|SHA-256'):
        stage2_runs.restore_checkpoint(
            ctx, phase='blueprint', chapter=1, purpose=purpose,
            architecture='extraction-ir-v3.1', response_model=_ChapterRows,
            system='SYSTEM', user='USER')


def test_checkpoint_rejects_normalized_text_that_points_to_another_payload(tmp_path):
    run_id = new_run_id()
    purpose = 'stage2.blueprint:ch1'
    ctx = _ctx(tmp_path, run_id)
    requests = [(1, 'SYSTEM', 'USER')]
    _ensure_run(
        ctx, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=requests)
    normalized = '{"blueprints":[{"chapter_ordinal":1}]}'
    acceptance = _checkpoint_acceptance(
        ctx, attempt_id='e' * 16, purpose=purpose,
        system='SYSTEM', user='USER', raw_text=normalized,
        normalized_text=normalized)
    checkpoint = stage2_runs.write_checkpoint(
        ctx, phase='blueprint', chapter=1, purpose=purpose,
        architecture='extraction-ir-v3.1', response_model=_ChapterRows,
        system='SYSTEM', user='USER',
        response_payload={'blueprints': [{'chapter_ordinal': 1}]},
        acceptance=acceptance, scope_violation=None)
    raw = json.loads(checkpoint.read_text(encoding='utf-8'))
    changed = '{"blueprints":[{"chapter_ordinal":2}]}'
    raw['normalized_text'] = changed
    raw['normalized_text_sha256'] = hashlib.sha256(changed.encode()).hexdigest()
    raw['checkpoint_sha256'] = _checkpoint_hash(raw)
    checkpoint.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    with pytest.raises(ProvenanceRefused, match='唯一归一结果|指向不同内容'):
        stage2_runs.restore_checkpoint(
            ctx, phase='blueprint', chapter=1, purpose=purpose,
            architecture='extraction-ir-v3.1', response_model=_ChapterRows,
            system='SYSTEM', user='USER')


@pytest.mark.parametrize('channel', ['content', 'reasoning'])
def test_checkpoint_write_rejects_normalized_text_not_derived_from_raw_channel(
        tmp_path, channel):
    ctx = _ctx(tmp_path, new_run_id())
    purpose = 'stage2.blueprint:ch1'
    _ensure_run(
        ctx, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=[(1, 'SYSTEM', 'USER')])
    normalized = '{"blueprints":[{"chapter_ordinal":1}]}'
    acceptance = _checkpoint_acceptance(
        ctx, attempt_id='7' * 16, purpose=purpose,
        system='SYSTEM', user='USER', raw_text='{}',
        normalized_text=normalized, channel=channel)

    with pytest.raises(ProvenanceRefused, match='唯一归一结果'):
        stage2_runs.write_checkpoint(
            ctx, phase='blueprint', chapter=1, purpose=purpose,
            architecture='extraction-ir-v3.1', response_model=_ChapterRows,
            system='SYSTEM', user='USER',
            response_payload={'blueprints': [{'chapter_ordinal': 1}]},
            acceptance=acceptance, scope_violation=None)


@pytest.mark.parametrize(
    ('channel', 'salvaged'), [('content', True), ('reasoning', False)])
def test_checkpoint_write_requires_reasoning_iff_salvaged(
        tmp_path, channel, salvaged):
    ctx = _ctx(tmp_path, new_run_id())
    purpose = 'stage2.blueprint:ch1'
    _ensure_run(
        ctx, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=[(1, 'SYSTEM', 'USER')])
    normalized = '{"blueprints":[{"chapter_ordinal":1}]}'
    acceptance = _checkpoint_acceptance(
        ctx, attempt_id='6' * 16, purpose=purpose,
        system='SYSTEM', user='USER', raw_text=normalized,
        normalized_text=normalized, channel=channel)
    acceptance['salvaged'] = salvaged

    with pytest.raises(ProvenanceRefused, match='salvaged'):
        stage2_runs.write_checkpoint(
            ctx, phase='blueprint', chapter=1, purpose=purpose,
            architecture='extraction-ir-v3.1', response_model=_ChapterRows,
            system='SYSTEM', user='USER',
            response_payload={'blueprints': [{'chapter_ordinal': 1}]},
            acceptance=acceptance, scope_violation=None)


def test_checkpoint_restore_rejects_coherent_payload_split_from_raw_channel(tmp_path):
    run_id = new_run_id()
    purpose = 'stage2.blueprint:ch1'
    ctx = _ctx(tmp_path, run_id)
    _ensure_run(
        ctx, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=[(1, 'SYSTEM', 'USER')])
    original = '{"blueprints":[{"chapter_ordinal":1}]}'
    acceptance = _checkpoint_acceptance(
        ctx, attempt_id='5' * 16, purpose=purpose,
        system='SYSTEM', user='USER', raw_text=original,
        normalized_text=original)
    checkpoint = stage2_runs.write_checkpoint(
        ctx, phase='blueprint', chapter=1, purpose=purpose,
        architecture='extraction-ir-v3.1', response_model=_ChapterRows,
        system='SYSTEM', user='USER',
        response_payload={'blueprints': [{'chapter_ordinal': 1}]},
        acceptance=acceptance, scope_violation=None)

    raw = json.loads(checkpoint.read_text(encoding='utf-8'))
    changed_text = '{"blueprints":[{"chapter_ordinal":2}]}'
    changed_payload = {'blueprints': [{'chapter_ordinal': 2}]}
    raw['normalized_text'] = changed_text
    raw['normalized_text_sha256'] = hashlib.sha256(changed_text.encode()).hexdigest()
    raw['response_payload'] = changed_payload
    raw['response_payload_sha256'] = hashlib.sha256(json.dumps(
        changed_payload, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()
    raw['checkpoint_sha256'] = _checkpoint_hash(raw)
    checkpoint.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')

    resumed = _ctx(tmp_path, run_id)
    with pytest.raises(ProvenanceRefused, match='唯一归一结果'):
        stage2_runs.restore_checkpoint(
            resumed, phase='blueprint', chapter=1, purpose=purpose,
            architecture='extraction-ir-v3.1', response_model=_ChapterRows,
            system='SYSTEM', user='USER')


def test_checkpoint_metadata_corruption_is_closed_by_whole_record_hash(tmp_path):
    run_id = new_run_id()
    purpose = 'stage2.blueprint:ch1'
    ctx = _ctx(tmp_path, run_id)
    requests = [(1, 'SYSTEM', 'USER')]
    _ensure_run(
        ctx, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=requests)
    normalized = '{"blueprints":[{"chapter_ordinal":1}]}'
    acceptance = _checkpoint_acceptance(
        ctx, attempt_id='9' * 16, purpose=purpose,
        system='SYSTEM', user='USER', raw_text=normalized,
        normalized_text=normalized)
    checkpoint = stage2_runs.write_checkpoint(
        ctx, phase='blueprint', chapter=1, purpose=purpose,
        architecture='extraction-ir-v3.1', response_model=_ChapterRows,
        system='SYSTEM', user='USER',
        response_payload={'blueprints': [{'chapter_ordinal': 1}]},
        acceptance=acceptance, scope_violation=None)
    raw = json.loads(checkpoint.read_text(encoding='utf-8'))
    raw['salvaged'] = True
    checkpoint.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    with pytest.raises(ArtifactUnreadable, match='checkpoint_sha256'):
        stage2_runs.restore_checkpoint(
            ctx, phase='blueprint', chapter=1, purpose=purpose,
            architecture='extraction-ir-v3.1', response_model=_ChapterRows,
            system='SYSTEM', user='USER')


def test_transport_retry_checkpoint_claims_every_attempt_and_real_resume_is_zero_wire(
        tmp_path):
    run_id = new_run_id()
    requests = [(1, 'SYSTEM', 'USER')]
    raw_text = '```json\n{"blueprints":[{"chapter_ordinal":1}]}\n```'
    normalized = '{"blueprints":[{"chapter_ordinal":1}]}'
    first = _ctx(tmp_path, run_id)
    _ensure_run(
        first, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=requests)

    def retry_then_succeed(_request, index):
        if index == 0:
            return TransportError('synthetic 429', retryable=True, status=429)
        return ChatResponse(
            content=raw_text, finish_reason='stop', model='test-model',
            usage={'prompt_tokens': 11, 'completion_tokens': 7,
                   'reasoning_tokens': 5})

    first_transport = FakeTransport(retry_then_succeed)
    first_client = LlmClient(
        first, first_transport, model='test-model', enable_model_fallback=False,
        max_retries=1, backoff_base=0, sleep=lambda _seconds: None,
        budget_checker=lambda *_args, **_kwargs: None)
    rows, violations, errors = extract._run_chapter_requests(
        first, first_client, requests, model_cls=_ChapterRows,
        max_tokens=2048, checkpoints=True, architecture='extraction-ir-v3.1')
    assert errors == [] and violations == []
    assert rows == [{'chapter_ordinal': 1}]
    assert len(first_transport.calls) == 2 and first.ledger.calls_made == 2

    checkpoint_path = stage2_runs.paths.stage2_blueprint_checkpoint_path(
        first.novel_dir, run_id, 'blueprint', 1)
    checkpoint = json.loads(checkpoint_path.read_text(encoding='utf-8'))
    assert len(checkpoint['attempt_links']) == 2
    assert checkpoint['accepted_text'] == raw_text
    assert checkpoint['normalized_text'] == normalized
    assert {link['response_sha256'] for link in checkpoint['attempt_links']} == {
        None, hashlib.sha256(raw_text.encode()).hexdigest(),
    }

    events: list[dict[str, object]] = []
    resumed = _ctx(tmp_path, run_id, observer=events.append)
    _ensure_run(
        resumed, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=requests)
    resumed_transport = FakeTransport(
        lambda _request, _index: AssertionError('checkpoint restore 不应新增 wire'))
    resumed_client = LlmClient(
        resumed, resumed_transport, model='test-model', enable_model_fallback=False,
        max_retries=1, backoff_base=0, sleep=lambda _seconds: None,
        budget_checker=lambda *_args, **_kwargs: None)
    restored_rows, restored_violations, restored_errors = extract._run_chapter_requests(
        resumed, resumed_client, requests, model_cls=_ChapterRows,
        max_tokens=2048, checkpoints=True, architecture='extraction-ir-v3.1')
    assert restored_errors == [] and restored_violations == []
    assert restored_rows == rows and resumed_transport.calls == []
    assert resumed.ledger.calls_made == 2
    restored = [event for event in events
                if event.get('event') == 'checkpoint_restored']
    assert len(restored) == 1 and restored[0]['wire_calls'] == 0


def test_fenced_reasoning_checkpoint_keeps_raw_and_normalized_channels(tmp_path):
    run_id = new_run_id()
    requests = [(1, 'SYSTEM', 'USER')]
    normalized = '{"blueprints":[{"chapter_ordinal":1}]}'
    raw_reasoning = f'```json\n{normalized}\n```'
    ctx = _ctx(tmp_path, run_id)
    _ensure_run(
        ctx, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=requests)
    transport = FakeTransport.always(ChatResponse(
        content='', reasoning=raw_reasoning, finish_reason='stop',
        model='test-model', usage={'completion_tokens': 9, 'reasoning_tokens': 9}))
    client = LlmClient(
        ctx, transport, model='test-model', enable_model_fallback=False,
        sleep=lambda _seconds: None,
        budget_checker=lambda *_args, **_kwargs: None)
    rows, _violations, errors = extract._run_chapter_requests(
        ctx, client, requests, model_cls=_ChapterRows,
        max_tokens=2048, checkpoints=True, architecture='extraction-ir-v3.1')
    assert errors == [] and rows == [{'chapter_ordinal': 1}]
    assert len(transport.calls) == 1, '合法 reasoning 已过完整闸，不得再发一采争取 content'
    checkpoint = json.loads(stage2_runs.paths.stage2_blueprint_checkpoint_path(
        ctx.novel_dir, run_id, 'blueprint', 1).read_text(encoding='utf-8'))
    assert checkpoint['accepted_channel'] == 'reasoning'
    assert checkpoint['salvaged'] is True
    assert checkpoint['accepted_text'] == raw_reasoning
    assert checkpoint['normalized_text'] == normalized
    assert checkpoint['ledger_response_sha256'] == hashlib.sha256(b'').hexdigest()
    gate, = [event for event in ctx.ledger.audit_events
             if event['kind'] == 'gate_decision']
    lost, = [event for event in ctx.ledger.audit_events
             if event['kind'] == 'answer_lost']
    assert (gate['decision'], gate['salvaged']) == ('accepted', True)
    assert lost['salvaged'] is True


@pytest.mark.parametrize(
    ('effort', 'max_tokens', 'message'),
    [('low', 2048, '模型链/effort/thinking'),
     ('high', 2049, 'model/effort/thinking/json_mode/max_tokens')],
)
def test_checkpoint_request_config_drift_fails_closed_before_wire(
        tmp_path, effort, max_tokens, message):
    run_id = new_run_id()
    requests = [(1, 'SYSTEM', 'USER')]
    first = _ctx(tmp_path, run_id)
    _ensure_run(
        first, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=requests)
    first_client = LlmClient(
        first, FakeTransport.always(ChatResponse(
            content='{"blueprints":[{"chapter_ordinal":1}]}',
            model='test-model')), model='test-model', enable_model_fallback=False,
        effort='high', sleep=lambda _seconds: None,
        budget_checker=lambda *_args, **_kwargs: None)
    _rows, _violations, errors = extract._run_chapter_requests(
        first, first_client, requests, model_cls=_ChapterRows,
        max_tokens=2048, checkpoints=True, architecture='extraction-ir-v3.1')
    assert errors == []

    resumed = _ctx(tmp_path, run_id)
    transport = FakeTransport(
        lambda _request, _index: AssertionError('配置漂移必须在 wire 前关闭'))
    client = LlmClient(
        resumed, transport, model='test-model', enable_model_fallback=False,
        effort=effort, sleep=lambda _seconds: None,
        budget_checker=lambda *_args, **_kwargs: None)
    _rows, _violations, errors = extract._run_chapter_requests(
        resumed, client, requests, model_cls=_ChapterRows,
        max_tokens=max_tokens, checkpoints=True, architecture='extraction-ir-v3.1')
    assert len(errors) == 1 and isinstance(errors[0], ProvenanceRefused)
    assert message in str(errors[0]) and transport.calls == []


def test_response_model_version_suffix_restores_by_request_alias_with_zero_wire(tmp_path):
    run_id = new_run_id()
    requests = [(1, 'SYSTEM', 'USER')]
    first = _ctx(tmp_path, run_id)
    _ensure_run(
        first, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=requests)
    first_client = LlmClient(
        first, FakeTransport.always(ChatResponse(
            content='{"blueprints":[{"chapter_ordinal":1}]}',
            model='test-model-202608')),
        model='test-model', enable_model_fallback=False,
        sleep=lambda _seconds: None,
        budget_checker=lambda *_args, **_kwargs: None)
    rows, _violations, errors = extract._run_chapter_requests(
        first, first_client, requests, model_cls=_ChapterRows,
        max_tokens=2048, checkpoints=True, architecture='extraction-ir-v3.1')
    assert errors == [] and rows == [{'chapter_ordinal': 1}]
    checkpoint = json.loads(stage2_runs.paths.stage2_blueprint_checkpoint_path(
        first.novel_dir, run_id, 'blueprint', 1).read_text(encoding='utf-8'))
    assert checkpoint['model'] == 'test-model-202608'
    assert checkpoint['accepted_request']['request_model'] == 'test-model'

    resumed = _ctx(tmp_path, run_id)
    transport = FakeTransport(
        lambda _request, _index: AssertionError('request alias 未漂移，不应新增 wire'))
    resumed_client = LlmClient(
        resumed, transport, model='test-model', enable_model_fallback=False,
        sleep=lambda _seconds: None,
        budget_checker=lambda *_args, **_kwargs: None)
    restored, _violations, restored_errors = extract._run_chapter_requests(
        resumed, resumed_client, requests, model_cls=_ChapterRows,
        max_tokens=2048, checkpoints=True, architecture='extraction-ir-v3.1')
    assert restored_errors == [] and restored == rows and transport.calls == []


def test_checkpoint_preserves_untyped_parse_then_revalidates_schema_coercion(tmp_path):
    run_id = new_run_id()
    requests = [(1, 'SYSTEM', 'USER')]
    first = _ctx(tmp_path, run_id)
    raw_text = '{"blueprints":[{"chapter_ordinal":"1"}]}'
    first_client = LlmClient(
        first, FakeTransport.always(ChatResponse(
            content=raw_text, model='test-model')),
        model='test-model', enable_model_fallback=False,
        sleep=lambda _seconds: None,
        budget_checker=lambda *_args, **_kwargs: None)
    policy = first_client.json_request_policy(
        [('blueprint', _CoercingChapterRows, 2048, False)],
        recovery_policy='stage2-blueprint-v1')
    _ensure_run(
        first, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=requests,
        request_policy=policy)
    rows, _violations, errors = extract._run_chapter_requests(
        first, first_client, requests, model_cls=_CoercingChapterRows,
        max_tokens=2048, checkpoints=True, architecture='extraction-ir-v3.1')
    assert errors == []
    assert rows == [{
        'chapter_ordinal': 1, 'normalized_default': 'filled-by-schema'}]
    checkpoint = json.loads(stage2_runs.paths.stage2_blueprint_checkpoint_path(
        first.novel_dir, run_id, 'blueprint', 1).read_text(encoding='utf-8'))
    assert checkpoint['parsed_payload'] == {
        'blueprints': [{'chapter_ordinal': '1'}]}
    assert checkpoint['response_payload'] == {'blueprints': [{
        'chapter_ordinal': 1, 'normalized_default': 'filled-by-schema'}]}

    resumed = _ctx(tmp_path, run_id)
    transport = FakeTransport(
        lambda _request, _index: AssertionError('schema coercion checkpoint 不应新增 wire'))
    resumed_client = LlmClient(
        resumed, transport, model='test-model', enable_model_fallback=False,
        sleep=lambda _seconds: None,
        budget_checker=lambda *_args, **_kwargs: None)
    restored, _violations, restored_errors = extract._run_chapter_requests(
        resumed, resumed_client, requests, model_cls=_CoercingChapterRows,
        max_tokens=2048, checkpoints=True, architecture='extraction-ir-v3.1')
    assert restored_errors == [] and restored == rows and transport.calls == []


@pytest.mark.parametrize('scenario', ['all_pending', 'mixed_checkpoint'])
def test_run_request_policy_drift_stops_pending_resume_before_any_wire(
        tmp_path, scenario):
    run_id = new_run_id()
    requests = [(1, 'SYSTEM', 'USER 1'), (2, 'SYSTEM', 'USER 2')]
    first = _ctx(tmp_path, run_id, repair=True)

    def first_response(request, _index):
        if scenario == 'mixed_checkpoint' and request.user.endswith('1'):
            return ChatResponse(
                content='{"blueprints":[{"chapter_ordinal":1}]}',
                model='test-model')
        return ChatResponse(content='{"blueprints":[}', model='test-model')

    first_client = LlmClient(
        first, FakeTransport(first_response), model='test-model',
        enable_model_fallback=False, effort='high', max_retries=0,
        sleep=lambda _seconds: None,
        budget_checker=lambda *_args, **_kwargs: None)
    high_policy = first_client.json_request_policy(
        [('blueprint', _ChapterRows, 2048, False)],
        recovery_policy='stage2-blueprint-v1')
    _ensure_run(
        first, architecture='extraction-ir-v3.1', topology='scaffolded_single',
        requests=requests, request_policy=high_policy)
    _rows, _violations, pending = extract._run_chapter_requests(
        first, first_client, requests, model_cls=_ChapterRows,
        max_tokens=2048, checkpoints=True, architecture='extraction-ir-v3.1')
    expected_pending = 2 if scenario == 'all_pending' else 1
    assert len(pending) == expected_pending
    assert all(isinstance(error, ResponseRepairRequired) for error in pending)
    manifest = stage2_runs.create_repair_manifest(
        first, pending, architecture='extraction-ir-v3.1')
    decisions = tmp_path / f'{scenario}-decisions.json'
    decisions.write_text(json.dumps({'decisions': [
        {'attempt_id': error.attempt_id, 'action': 'decline',
         'reason': '材料不足，进入登记恢复动作'}
        for error in pending
    ]}, ensure_ascii=False), encoding='utf-8')
    stage2_runs.submit_batch(
        first.novel_dir, manifest, decisions, actor='operator')

    resumed = _ctx(tmp_path, run_id, repair=True)
    transport = FakeTransport(
        lambda _request, _index: AssertionError('run policy 漂移必须先于任何 wire 失败'))
    changed_client = LlmClient(
        resumed, transport, model='test-model', enable_model_fallback=False,
        effort='low', max_retries=0, sleep=lambda _seconds: None,
        budget_checker=lambda *_args, **_kwargs: None)
    changed_policy = changed_client.json_request_policy(
        [('blueprint', _ChapterRows, 2048, False)],
        recovery_policy='stage2-blueprint-v1')
    with pytest.raises(ProvenanceRefused, match='绑定已漂移'):
        _ensure_run(
            resumed, architecture='extraction-ir-v3.1',
            topology='scaffolded_single', requests=requests,
            request_policy=changed_policy)
    assert transport.calls == [] and resumed.ledger.calls_made == 0


def test_checkpoint_preflight_blocks_missing_chapter_before_any_wire(tmp_path):
    run_id = new_run_id()
    requests = [(1, 'SYSTEM', 'USER 1'), (2, 'SYSTEM', 'USER 2')]
    first = _ctx(tmp_path, run_id)
    policy = _test_request_policy(max_tokens=2048)
    _ensure_run(
        first, architecture='extraction-ir-v3.1', topology='scaffolded_single',
        requests=requests, request_policy=policy)
    purpose = 'stage2.blueprint:ch1'
    original = '{"blueprints":[{"chapter_ordinal":1}]}'
    acceptance = _checkpoint_acceptance(
        first, attempt_id='4' * 16, purpose=purpose,
        system='SYSTEM', user='USER 1', raw_text=original,
        normalized_text=original, max_tokens=2048)
    checkpoint = stage2_runs.write_checkpoint(
        first, phase='blueprint', chapter=1, purpose=purpose,
        architecture='extraction-ir-v3.1', response_model=_ChapterRows,
        system='SYSTEM', user='USER 1',
        response_payload={'blueprints': [{'chapter_ordinal': 1}]},
        acceptance=acceptance, scope_violation=None)
    raw = json.loads(checkpoint.read_text(encoding='utf-8'))
    changed_text = '{"blueprints":[{"chapter_ordinal":99}]}'
    changed_payload = {'blueprints': [{'chapter_ordinal': 99}]}
    raw['normalized_text'] = changed_text
    raw['normalized_text_sha256'] = hashlib.sha256(changed_text.encode()).hexdigest()
    raw['response_payload'] = changed_payload
    raw['response_payload_sha256'] = hashlib.sha256(json.dumps(
        changed_payload, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()
    raw['checkpoint_sha256'] = _checkpoint_hash(raw)
    checkpoint.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')

    resumed = _ctx(tmp_path, run_id)
    transport = FakeTransport(
        lambda _request, _index: AssertionError('坏 checkpoint 必须阻止缺章上线'))
    client = LlmClient(
        resumed, transport, model='test-model', enable_model_fallback=False,
        max_retries=0, sleep=lambda _seconds: None,
        budget_checker=lambda *_args, **_kwargs: None)
    rows, violations, errors = extract._run_chapter_requests(
        resumed, client, requests, model_cls=_ChapterRows,
        max_tokens=2048, checkpoints=True, architecture='extraction-ir-v3.1')
    assert rows == [] and violations == []
    assert len(errors) == 1 and isinstance(errors[0], ProvenanceRefused)
    assert transport.calls == [] and resumed.ledger.calls_made == 0


def test_checkpoint_write_remains_enabled_when_old_restore_is_disabled(tmp_path):
    ctx = _ctx(tmp_path, new_run_id())
    requests = [(1, 'SYSTEM', 'USER')]
    _ensure_run(
        ctx, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=requests)
    rows, _violations, errors = extract._run_chapter_requests(
        ctx, _SyntheticChapterClient(ctx), requests,
        model_cls=_ChapterRows, checkpoints=True, restore_checkpoints=False,
        architecture='extraction-ir-v3.1')
    assert errors == [] and rows == [{'chapter_ordinal': 1}]
    assert stage2_runs.paths.stage2_blueprint_checkpoint_path(
        ctx.novel_dir, ctx.run_id, 'blueprint', 1).is_file()


def test_refresh_cannot_reuse_run_with_checkpoint_before_any_wire(tmp_path):
    run_id = new_run_id()
    requests = [(1, 'SYSTEM', 'USER')]
    first = _ctx(tmp_path, run_id)
    _ensure_run(
        first, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=requests)
    rows, _violations, errors = extract._run_chapter_requests(
        first, _SyntheticChapterClient(first), requests,
        model_cls=_ChapterRows, checkpoints=True,
        architecture='extraction-ir-v3.1')
    assert errors == [] and rows == [{'chapter_ordinal': 1}]
    checkpoint = stage2_runs.paths.stage2_blueprint_checkpoint_path(
        first.novel_dir, run_id, 'blueprint', 1)
    before = checkpoint.read_bytes()

    resumed = _ctx(tmp_path, run_id)
    resumed.cache = CachePolicy.refresh()
    transport = FakeTransport(
        lambda _request, _index: AssertionError('同 run 主动重采必须先于 wire 拒绝'))
    _client = LlmClient(
        resumed, transport, model='test-model', enable_model_fallback=False,
        sleep=lambda _seconds: None,
        budget_checker=lambda *_args, **_kwargs: None)
    with pytest.raises(ProvenanceRefused, match='主动重采.*换新.*--run-id'):
        _ensure_run(
            resumed, architecture='extraction-ir-v3.1',
            topology='scaffolded_single', requests=requests, refresh=True)

    assert checkpoint.read_bytes() == before
    assert transport.calls == [] and resumed.ledger.calls_made == 0


def test_refresh_cannot_reuse_run_with_pending_manifest_before_any_wire(tmp_path):
    run_id = new_run_id()
    requests = [(1, 'SYSTEM', 'USER')]
    first = _ctx(tmp_path, run_id, repair=True)
    first_client = LlmClient(
        first, FakeTransport.always(ChatResponse(
            content='{"blueprints":[}', model='test-model')),
        model='test-model', enable_model_fallback=False, max_retries=0,
        sleep=lambda _seconds: None,
        budget_checker=lambda *_args, **_kwargs: None)
    policy = first_client.json_request_policy(
        [('blueprint', _ChapterRows, 2048, False)],
        recovery_policy='stage2-blueprint-v1')
    _ensure_run(
        first, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=requests,
        request_policy=policy)
    _rows, _violations, pending = extract._run_chapter_requests(
        first, first_client, requests, model_cls=_ChapterRows,
        max_tokens=2048, checkpoints=True, architecture='extraction-ir-v3.1')
    assert len(pending) == 1 and isinstance(pending[0], ResponseRepairRequired)
    manifest = stage2_runs.create_repair_manifest(
        first, pending, architecture='extraction-ir-v3.1')
    before = manifest.read_bytes()

    resumed = _ctx(tmp_path, run_id, repair=True)
    resumed.cache = CachePolicy.refresh()
    transport = FakeTransport(
        lambda _request, _index: AssertionError('pending manifest 必须先于 wire 拒绝'))
    changed_client = LlmClient(
        resumed, transport, model='test-model', enable_model_fallback=False,
        max_retries=0, sleep=lambda _seconds: None,
        budget_checker=lambda *_args, **_kwargs: None)
    with pytest.raises(ProvenanceRefused, match='主动重采.*换新.*--run-id'):
        _ensure_run(
            resumed, architecture='extraction-ir-v3.1',
            topology='scaffolded_single', requests=requests,
            request_policy=changed_client.json_request_policy(
                [('blueprint', _ChapterRows, 2048, False)],
                recovery_policy='stage2-blueprint-v1'),
            refresh=True)

    assert manifest.read_bytes() == before
    assert not manifest.with_name(stage2_runs.RESOLUTION_FILE).exists()
    assert transport.calls == [] and resumed.ledger.calls_made == 0


def test_targeted_refresh_reusing_stage2_run_is_rejected_before_input_or_wire(tmp_path):
    run_id = new_run_id()
    first = _ctx(tmp_path, run_id)
    _ensure_run(
        first, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=[(1, 'SYSTEM', 'USER')])

    resumed = _ctx(tmp_path, run_id)
    resumed.cache = CachePolicy.refresh()
    transport = FakeTransport(
        lambda _request, _index: AssertionError('--chapters 同 run 不应上线'))
    client = LlmClient(
        resumed, transport, model='test-model', enable_model_fallback=False,
        sleep=lambda _seconds: None,
        budget_checker=lambda *_args, **_kwargs: None)
    with pytest.raises(ProvenanceRefused, match='--chapters.*换新.*--run-id'):
        extract.run_blueprint(resumed, client, chapters=(1,))
    assert transport.calls == [] and resumed.ledger.calls_made == 0


def test_checkpoint_path_does_not_invent_a_9999_chapter_cap(tmp_path):
    run_id = new_run_id()
    ctx = _ctx(tmp_path, run_id)
    requests = [(10_000, 'SYSTEM', 'USER')]
    _ensure_run(
        ctx, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=requests)
    rows, _violations, errors = extract._run_chapter_requests(
        ctx, _SyntheticChapterClient(ctx), requests,
        model_cls=_ChapterRows, checkpoints=True,
        architecture='extraction-ir-v3.1')
    assert errors == [] and rows == [{'chapter_ordinal': 10_000}]
    assert stage2_runs.paths.stage2_blueprint_checkpoint_path(
        ctx.novel_dir, run_id, 'blueprint', 10_000).name \
        == 'blueprint-ch10000.json'


def test_recovery_prompt_checkpoint_is_reconstructed_before_zero_wire_restore(tmp_path):
    run_id = new_run_id()
    requests = [(1, 'SYSTEM', 'USER')]
    first = _ctx(tmp_path, run_id, repair=True)
    _ensure_run(
        first, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=requests)
    bad_transport = FakeTransport.always(ChatResponse(
        content='{"blueprints":[}', finish_reason='stop', model='test-model'))
    first_client = LlmClient(
        first, bad_transport, model='test-model', enable_model_fallback=False,
        max_retries=0, sleep=lambda _seconds: None,
        budget_checker=lambda *_args, **_kwargs: None)
    _rows, _violations, pending = extract._run_chapter_requests(
        first, first_client, requests, model_cls=_ChapterRows,
        max_tokens=2048, checkpoints=True, architecture='extraction-ir-v3.1')
    assert len(pending) == 1 and isinstance(pending[0], ResponseRepairRequired)
    manifest = stage2_runs.create_repair_manifest(
        first, pending, architecture='extraction-ir-v3.1')
    decisions = tmp_path / 'recovery-decisions.json'
    decisions.write_text(json.dumps({'decisions': [{
        'attempt_id': pending[0].attempt_id,
        'action': 'decline',
        'reason': '材料不完整，交给定向恢复',
    }]}, ensure_ascii=False), encoding='utf-8')
    stage2_runs.submit_batch(
        first.novel_dir, manifest, decisions, actor='operator')

    resumed = _ctx(tmp_path, run_id, repair=True)
    good = ChatResponse(
        content='{"blueprints":[{"chapter_ordinal":1}]}',
        finish_reason='stop', model='test-model')
    recovery_transport = FakeTransport.always(good)
    recovery_client = LlmClient(
        resumed, recovery_transport, model='test-model', enable_model_fallback=False,
        max_retries=0, sleep=lambda _seconds: None,
        budget_checker=lambda *_args, **_kwargs: None)
    rows, _violations, errors = extract._run_chapter_requests(
        resumed, recovery_client, requests, model_cls=_ChapterRows,
        max_tokens=2048, checkpoints=True, architecture='extraction-ir-v3.1')
    assert errors == [] and rows == [{'chapter_ordinal': 1}]
    assert len(recovery_transport.calls) == 1
    assert recovery_transport.calls[0].user != 'USER'
    checkpoint = json.loads(stage2_runs.paths.stage2_blueprint_checkpoint_path(
        resumed.novel_dir, run_id, 'blueprint', 1).read_text(encoding='utf-8'))
    assert checkpoint['request_mode'].startswith('targeted_repair:')
    assert checkpoint['recovery_from_attempts'] == [pending[0].attempt_id]

    final_ctx = _ctx(tmp_path, run_id, repair=True)
    final_transport = FakeTransport(
        lambda _request, _index: AssertionError('恢复 prompt checkpoint 不应上线'))
    final_client = LlmClient(
        final_ctx, final_transport, model='test-model', enable_model_fallback=False,
        max_retries=0, sleep=lambda _seconds: None,
        budget_checker=lambda *_args, **_kwargs: None)
    final_rows, _violations, final_errors = extract._run_chapter_requests(
        final_ctx, final_client, requests, model_cls=_ChapterRows,
        max_tokens=2048, checkpoints=True, architecture='extraction-ir-v3.1')
    assert final_errors == [] and final_rows == rows and final_transport.calls == []


def _seed_pending_attempts(
        ctx: RunContext, count: int,
) -> tuple[list[ResponseRepairRequired], list[str]]:
    errors: list[ResponseRepairRequired] = []
    attempts: list[str] = []
    for index in range(1, count + 1):
        attempt_id = f'{index:016x}'
        chain_id = hashlib.sha256(f'chain-{index}'.encode()).hexdigest()
        _rel, dump_error = response_repair.keep_rejection(
            ctx.novel_dir, chain_id=chain_id, attempt_id=attempt_id,
            original='{"blueprints":[]}', candidate={'blueprints': []},
            payload={
                'sid': ctx.sid, 'node': ctx.node, 'run_id': ctx.run_id,
                'purpose': f'{ctx.node}:ch{index}', 'code': ctx.code,
                'repair_eligible': True, 'gate': 'schema',
                'chapter': index, 'model': 'test-model',
            })
        assert dump_error is None
        errors.append(ResponseRepairRequired(
            f'ch{index} pending', attempt_id=attempt_id,
            candidate_path='candidate.json', original_path='original.txt',
            hint='repair'))
        attempts.append(attempt_id)
    return errors, attempts


def _write_repaired_checkpoint(
        tmp_path: Path, *, authority: str, channel: str,
) -> tuple[RunContext, Path, Path]:
    run_id = new_run_id()
    ctx = _ctx(tmp_path, run_id)
    purpose = 'stage2.blueprint:ch1'
    _ensure_run(
        ctx, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=[(1, 'SYSTEM', 'USER')])
    errors, attempts = _seed_pending_attempts(ctx, 1)
    attempt_id = attempts[0]
    record = response_repair.find_attempt(ctx.novel_dir, attempt_id)
    payload = {'blueprints': [{'chapter_ordinal': 1}]}

    if authority == 'batch':
        assert channel == 'agent'
        response_repair.candidate_path(record).write_text(
            json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        manifest = stage2_runs.create_repair_manifest(
            ctx, errors, architecture='extraction-ir-v3.1')
        decisions = tmp_path / 'repair-checkpoint-decisions.json'
        decisions.write_text(json.dumps({'decisions': [{
            'attempt_id': attempt_id, 'action': 'submit',
            'reason': '封存合格候选',
        }]}, ensure_ascii=False), encoding='utf-8')
        source_path = stage2_runs.submit_batch(
            ctx.novel_dir, manifest, decisions, actor='operator')
    elif channel == 'agent':
        response_repair.candidate_path(record).write_text(
            json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        sealed = response_repair.submit(
            ctx.novel_dir, attempt_id, actor='operator', reason='封存合格候选')
        source_path = sealed.root / response_repair.RESOLUTION_FILE
    else:
        assert channel == 'deterministic'
        sealed = response_repair.submit_deterministic(
            record, payload, rule='json-bytes-v1')
        source_path = sealed.root / response_repair.RESOLUTION_FILE

    text = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    acceptance = _checkpoint_acceptance(
        ctx, attempt_id=attempt_id, purpose=purpose,
        system='SYSTEM', user='USER', raw_text=text,
        normalized_text=text, channel=channel)
    acceptance['recovery_mode'] = (
        'deterministic' if channel == 'deterministic' else 'agent_submit')
    checkpoint = stage2_runs.write_checkpoint(
        ctx, phase='blueprint', chapter=1, purpose=purpose,
        architecture='extraction-ir-v3.1', response_model=_ChapterRows,
        system='SYSTEM', user='USER', response_payload=payload,
        acceptance=acceptance, scope_violation=None)
    return ctx, checkpoint, source_path


@pytest.mark.parametrize('channel', ['agent', 'deterministic'])
@pytest.mark.parametrize('mutation', ['candidate', 'strip_integrity'])
def test_single_resolution_tamper_before_checkpoint_is_rejected(
        tmp_path, channel, mutation):
    run_id = new_run_id()
    ctx = _ctx(tmp_path, run_id)
    purpose = 'stage2.blueprint:ch1'
    _ensure_run(
        ctx, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=[(1, 'SYSTEM', 'USER')])
    _errors, attempts = _seed_pending_attempts(ctx, 1)
    attempt_id = attempts[0]
    record = response_repair.find_attempt(ctx.novel_dir, attempt_id)
    payload = {'blueprints': [{'chapter_ordinal': 1}]}
    if channel == 'agent':
        response_repair.candidate_path(record).write_text(
            json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        sealed = response_repair.submit(
            ctx.novel_dir, attempt_id, actor='operator', reason='封存合格候选')
    else:
        sealed = response_repair.submit_deterministic(
            record, payload, rule='json-bytes-v1')
    resolution_path = sealed.root / response_repair.RESOLUTION_FILE
    resolution = json.loads(resolution_path.read_text(encoding='utf-8'))
    if mutation == 'candidate':
        changed = {'blueprints': [{'chapter_ordinal': 99}]}
        resolution['candidate'] = changed
        resolution['candidate_payload_sha256'] = hashlib.sha256(json.dumps(
            changed, ensure_ascii=False, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()
    else:
        resolution.pop('resolution_sha256')
        resolution.pop('candidate_payload_sha256')
    resolution_path.write_text(
        json.dumps(resolution, ensure_ascii=False), encoding='utf-8')

    text = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    acceptance = _checkpoint_acceptance(
        ctx, attempt_id=attempt_id, purpose=purpose,
        system='SYSTEM', user='USER', raw_text=text,
        normalized_text=text, channel=channel)
    with pytest.raises(ArtifactUnreadable, match='resolution_sha256'):
        stage2_runs.write_checkpoint(
            ctx, phase='blueprint', chapter=1, purpose=purpose,
            architecture='extraction-ir-v3.1', response_model=_ChapterRows,
            system='SYSTEM', user='USER', response_payload=payload,
            acceptance=acceptance, scope_violation=None)


def test_legacy_single_resolution_without_integrity_marker_remains_readable(tmp_path):
    ctx = _ctx(tmp_path, new_run_id())
    errors, attempts = _seed_pending_attempts(ctx, 1)
    assert errors
    record = response_repair.find_attempt(ctx.novel_dir, attempts[0])
    rejection_path = record.root / response_repair.REJECTION_FILE
    rejection = json.loads(rejection_path.read_text(encoding='utf-8'))
    rejection.pop(response_repair.RESOLUTION_INTEGRITY_REQUIRED)
    rejection_path.write_text(json.dumps(rejection, ensure_ascii=False), encoding='utf-8')
    legacy = {
        'version': response_repair.VERSION,
        'submitted_at': '2026-01-01T00:00:00+00:00',
        'attempt_id': attempts[0],
        'actor': 'legacy-operator',
        'reason': '历史记录',
        'action': 'submit',
        'candidate': {'blueprints': [{'chapter_ordinal': 1}]},
        'candidate_sha256': 'a' * 64,
    }
    (record.root / response_repair.RESOLUTION_FILE).write_text(
        json.dumps(legacy, ensure_ascii=False), encoding='utf-8')
    restored = response_repair.find_attempt(ctx.novel_dir, attempts[0])
    assert restored.resolution == legacy


@pytest.mark.parametrize(
    ('authority', 'channel'),
    [('single', 'agent'), ('single', 'deterministic'), ('batch', 'agent')],
)
@pytest.mark.parametrize('mutation', ['delete', 'tamper'])
def test_repaired_checkpoint_requires_immutable_resolution_on_restore(
        tmp_path, authority, channel, mutation):
    ctx, checkpoint, resolution_path = _write_repaired_checkpoint(
        tmp_path, authority=authority, channel=channel)
    stored = json.loads(checkpoint.read_text(encoding='utf-8'))
    assert stored['repair_resolution']['source'] == authority
    assert stored['repair_resolution']['action'] == (
        'submit' if channel == 'agent' else 'deterministic')

    valid = _ctx(tmp_path, ctx.run_id)
    assert stage2_runs.restore_checkpoint(
        valid, phase='blueprint', chapter=1, purpose='stage2.blueprint:ch1',
        architecture='extraction-ir-v3.1', response_model=_ChapterRows,
        system='SYSTEM', user='USER') is not None

    if mutation == 'delete':
        resolution_path.unlink()
    else:
        resolution_path.write_bytes(resolution_path.read_bytes() + b' ')
    resumed = _ctx(tmp_path, ctx.run_id)
    with pytest.raises(ProvenanceRefused, match='resolution'):
        stage2_runs.restore_checkpoint(
            resumed, phase='blueprint', chapter=1,
            purpose='stage2.blueprint:ch1',
            architecture='extraction-ir-v3.1', response_model=_ChapterRows,
            system='SYSTEM', user='USER')


def test_eight_pending_make_one_manifest_and_one_atomic_resolution(tmp_path):
    ctx = _ctx(tmp_path, new_run_id())
    requests = [(index, 'SYSTEM', f'USER {index}') for index in range(1, 9)]
    _ensure_run(
        ctx, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=requests)
    errors, attempts = _seed_pending_attempts(ctx, 8)

    with pytest.raises(ResponseRepairBatchRequired) as paused:
        extract._raise_response_repair(
            ctx, errors, architecture='extraction-ir-v3.1')
    manifest = Path(paused.value.manifest_path)
    assert paused.value.run_id == ctx.run_id
    assert paused.value.attempts == tuple(attempts)
    assert len(list(manifest.parent.glob(stage2_runs.MANIFEST_FILE))) == 1

    with pytest.raises(TerminalState, match='禁止再走单项 submit'):
        response_repair.submit(
            ctx.novel_dir, attempts[0], actor='operator', reason='不能拆单')

    decisions = tmp_path / 'decisions.json'
    decisions.write_text(json.dumps({
        'decisions': [
            {'attempt_id': attempt_id, 'action': 'decline',
             'reason': '材料不足，交给登记的模型恢复动作'}
            for attempt_id in reversed(attempts)
        ],
    }, ensure_ascii=False), encoding='utf-8', newline='')
    resolution = stage2_runs.submit_batch(
        ctx.novel_dir, manifest, decisions, actor='operator')

    assert resolution.name == stage2_runs.RESOLUTION_FILE
    assert len(list(manifest.parent.glob(stage2_runs.RESOLUTION_FILE))) == 1
    assert not list((ctx.novel_dir / response_repair.paths.REJECTED_RESPONSE_DIRNAME).glob(
        '*/*/resolution.json'))
    for attempt_id in attempts:
        record = response_repair.find_attempt(ctx.novel_dir, attempt_id)
        assert record.resolution is not None
        assert record.resolution['action'] == 'decline'
        assert record.resolution['batch_resolution'] == str(resolution)

    with pytest.raises(TerminalState, match='最多人工 resume 一次'):
        stage2_runs.submit_batch(
            ctx.novel_dir, manifest, decisions, actor='operator')


def test_eight_pending_resume_once_then_all_checkpoints_replay_with_zero_wire(tmp_path):
    run_id = new_run_id()
    requests = [
        (ordinal, 'SYSTEM', f'USER {ordinal}')
        for ordinal in range(1, 9)
    ]
    first = _ctx(tmp_path, run_id, repair=True)
    first_transport = FakeTransport.always(ChatResponse(
        content='{"blueprints":[]}', finish_reason='stop', model='test-model'))
    first_client = LlmClient(
        first, first_transport, model='test-model', enable_model_fallback=False,
        max_retries=0, sleep=lambda _seconds: None,
        budget_checker=lambda *_args, **_kwargs: None)
    policy = first_client.json_request_policy(
        [('blueprint', _ChapterRows, 2048, False)],
        recovery_policy='stage2-blueprint-v1')
    _ensure_run(
        first, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=requests,
        request_policy=policy)

    first_rows, first_violations, pending = extract._run_chapter_requests(
        first, first_client, requests, model_cls=_ChapterRows,
        max_tokens=2048, checkpoints=True, architecture='extraction-ir-v3.1')
    assert first_rows == [] and first_violations == []
    assert len(pending) == 8
    assert all(isinstance(error, ResponseRepairRequired) for error in pending)
    assert len(first_transport.calls) == 8
    with pytest.raises(ResponseRepairBatchRequired) as paused:
        extract._raise_response_repair(
            first, pending, architecture='extraction-ir-v3.1')
    manifest = Path(paused.value.manifest_path)
    assert paused.value.run_id == run_id
    assert len(paused.value.attempts) == 8

    decisions_rows = []
    expected_wire_chapters: set[int] = set()
    for attempt_id in paused.value.attempts:
        record = response_repair.find_attempt(first.novel_dir, attempt_id)
        ordinal = int(record.rejection['chapter'])
        if ordinal <= 4:
            response_repair.candidate_path(record).write_text(
                json.dumps({'blueprints': [{'chapter_ordinal': ordinal}]},
                           ensure_ascii=False),
                encoding='utf-8')
            action = 'submit'
            reason = '候选只补齐已知目标章外层形状'
        else:
            action = 'decline'
            reason = '现有材料不足，进入登记的定向恢复动作'
            expected_wire_chapters.add(ordinal)
        decisions_rows.append({
            'attempt_id': attempt_id, 'action': action, 'reason': reason,
        })
    decisions = tmp_path / 'eight-real-resume-decisions.json'
    decisions.write_text(json.dumps(
        {'decisions': decisions_rows}, ensure_ascii=False), encoding='utf-8')
    resolution = stage2_runs.submit_batch(
        first.novel_dir, manifest, decisions, actor='operator')
    assert resolution.is_file()

    resumed = _ctx(tmp_path, run_id, repair=True)

    def recovered(request, _index):
        match = re.search(r'USER (\d+)', request.user)
        assert match is not None
        ordinal = int(match.group(1))
        assert ordinal in expected_wire_chapters
        return ChatResponse(
            content=json.dumps({
                'blueprints': [{'chapter_ordinal': ordinal}],
            }, ensure_ascii=False),
            finish_reason='stop', model='test-model')

    resumed_transport = FakeTransport(recovered)
    resumed_client = LlmClient(
        resumed, resumed_transport, model='test-model',
        enable_model_fallback=False, max_retries=0,
        sleep=lambda _seconds: None,
        budget_checker=lambda *_args, **_kwargs: None)
    _ensure_run(
        resumed, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=requests,
        request_policy=resumed_client.json_request_policy(
            [('blueprint', _ChapterRows, 2048, False)],
            recovery_policy='stage2-blueprint-v1'))
    resumed_rows, resumed_violations, resumed_errors = \
        extract._run_chapter_requests(
            resumed, resumed_client, requests, model_cls=_ChapterRows,
            max_tokens=2048, checkpoints=True,
            architecture='extraction-ir-v3.1')
    assert resumed_errors == [] and resumed_violations == []
    assert [row['chapter_ordinal'] for row in resumed_rows] == list(range(1, 9))
    assert len(resumed_transport.calls) == len(expected_wire_chapters) == 4
    extract._raise_response_repair(
        resumed, resumed_errors, architecture='extraction-ir-v3.1')
    assert list(manifest.parent.glob(stage2_runs.MANIFEST_FILE)) == [manifest]
    assert list(manifest.parent.glob(stage2_runs.RESOLUTION_FILE)) == [resolution]

    events: list[dict[str, object]] = []
    replay = _ctx(tmp_path, run_id, repair=True, observer=events.append)
    replay_transport = FakeTransport(
        lambda _request, _index: AssertionError('八章 checkpoint replay 不应新增 wire'))
    replay_client = LlmClient(
        replay, replay_transport, model='test-model', enable_model_fallback=False,
        max_retries=0, sleep=lambda _seconds: None,
        budget_checker=lambda *_args, **_kwargs: None)
    _ensure_run(
        replay, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=requests,
        request_policy=replay_client.json_request_policy(
            [('blueprint', _ChapterRows, 2048, False)],
            recovery_policy='stage2-blueprint-v1'))
    replay_rows, replay_violations, replay_errors = \
        extract._run_chapter_requests(
            replay, replay_client, requests, model_cls=_ChapterRows,
            max_tokens=2048, checkpoints=True,
            architecture='extraction-ir-v3.1')
    assert replay_errors == [] and replay_violations == []
    assert replay_rows == resumed_rows
    assert replay_transport.calls == []
    assert len([event for event in events
                if event.get('event') == 'checkpoint_restored']) == 8
    # 8 次首波失败 + 4 次 decline 后定向恢复；replay 只 claim，不新增账行/wire。
    assert replay.ledger.calls_made == 12


def test_v2_detail_failure_after_boundary_batch_cannot_pause_a_second_time(
        tmp_path, monkeypatch):
    """v2 两相共享一次人工额度；第二相失败只能自动升级或明确终态。"""
    run_id = new_run_id()
    first = _ctx(tmp_path, run_id, repair=True)
    main_text = [{'chapter_ordinal': 1, 'text': '第一段正文'}]
    boundary_system = 'BOUNDARY SYSTEM'
    detail_system = 'DETAIL SYSTEM'
    requests = [(1, boundary_system, 'BOUNDARY USER 1')]
    spec = SimpleNamespace(
        architecture='extraction-ir-v2',
        topology='boundary_then_detail',
        boundary_system=boundary_system,
        boundary_response_model=_ChapterRows,
        response_model=_ChapterRows,
        prefers_content_over_reasoning_salvage=False,
        system=detail_system,
    )
    character_projection = extract.materialize_blueprint_character_projection(
        {}, main_text)
    character_projection_raw = character_projection.model_dump(
        by_alias=True, mode='json')

    monkeypatch.setattr(extract, '_active_spec', lambda: spec)
    monkeypatch.setattr(
        extract, 'build_prompts',
        lambda _ctx, chapters=(), **_kwargs: requests)
    monkeypatch.setattr(extract, '_read_main_text', lambda _store: main_text)
    monkeypatch.setattr(
        extract, '_require_blueprint_character_projection',
        lambda _store: (character_projection, character_projection_raw))
    monkeypatch.setattr(
        extract, 'build_blueprint_user',
        lambda *_args, **_kwargs: 'DETAIL USER 1')
    monkeypatch.setattr(
        extract, '_boundary_validator',
        lambda *_args, **_kwargs: (lambda _obj, _resp: None))
    monkeypatch.setattr(
        extract, '_detail_validator',
        lambda *_args, **_kwargs: (lambda _obj, _resp: None))

    bad = ChatResponse(
        content='{"blueprints":[]}', finish_reason='stop', model=FLASH_MODEL)
    first_transport = FakeTransport.always(bad)
    first_client = LlmClient(
        first, first_transport, model=FLASH_MODEL, max_retries=0,
        concurrency=1, sleep=lambda _seconds: None,
        budget_checker=lambda *_args, **_kwargs: None)
    with pytest.raises(ResponseRepairBatchRequired) as paused:
        extract.request_blueprints(first, first_client)
    assert len(first_transport.calls) == 1
    manifest = Path(paused.value.manifest_path)
    assert paused.value.run_id == run_id
    assert len(paused.value.attempts) == 1

    decisions = tmp_path / 'v2-boundary-decisions.json'
    decisions.write_text(json.dumps({'decisions': [{
        'attempt_id': paused.value.attempts[0],
        'action': 'decline',
        'reason': '边界材料不足，进入登记的定向恢复动作',
    }]}, ensure_ascii=False), encoding='utf-8')
    resolution = stage2_runs.submit_batch(
        first.novel_dir, manifest, decisions, actor='operator')
    manifest_before = manifest.read_bytes()

    resumed = _ctx(tmp_path, run_id, repair=True)
    boundary_ok = {
        'blueprints': [{
            'chapter_ordinal': 1,
            'micro_events': [{'end_para': 1, 'event': '边界'}],
            'beats': [{'end_para': 1, 'event': '边界'}],
        }],
    }

    def respond(request, _index):
        if request.system == boundary_system:
            return ChatResponse(
                content=json.dumps(boundary_ok, ensure_ascii=False),
                finish_reason='stop', model=request.model)
        return ChatResponse(
            content='{"blueprints":[]}', finish_reason='stop',
            model=request.model)

    resumed_transport = FakeTransport(respond)
    resumed_client = LlmClient(
        resumed, resumed_transport, model=FLASH_MODEL, max_retries=0,
        concurrency=1, sleep=lambda _seconds: None,
        budget_checker=lambda *_args, **_kwargs: None)
    with pytest.raises(LlmFailed):
        extract.request_blueprints(resumed, resumed_client)

    assert [request.model for request in resumed_transport.calls] == [
        FLASH_MODEL, FLASH_MODEL, FLASH_MODEL, PRO_FALLBACK_MODEL,
    ]
    assert stage2_runs.paths.stage2_blueprint_checkpoint_path(
        resumed.novel_dir, run_id, 'boundaries', 1).is_file()
    assert not stage2_runs.paths.stage2_blueprint_checkpoint_path(
        resumed.novel_dir, run_id, 'detail', 1).exists()
    assert manifest.read_bytes() == manifest_before
    assert list(manifest.parent.glob(stage2_runs.MANIFEST_FILE)) == [manifest]
    assert list(manifest.parent.glob(stage2_runs.RESOLUTION_FILE)) == [resolution]
    detail_rejections = [
        json.loads(path.read_text(encoding='utf-8'))
        for path in resumed.novel_dir.glob(
            f'{response_repair.paths.REJECTED_RESPONSE_DIRNAME}/*/*/'
            f'{response_repair.REJECTION_FILE}')
        if json.loads(path.read_text(encoding='utf-8')).get('purpose')
        == 'stage2.blueprint:ch1'
    ]
    assert len(detail_rejections) == 3
    assert all(row.get('repair_eligible') is False for row in detail_rejections)


@pytest.mark.parametrize('tamper', ['run', 'rejection'])
def test_batch_consumption_rechecks_manifest_source_bytes(tmp_path, tamper):
    ctx = _ctx(tmp_path, new_run_id())
    _ensure_run(
        ctx, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=[(1, 'SYSTEM', 'USER')])
    errors, attempts = _seed_pending_attempts(ctx, 1)
    record_root = response_repair.find_attempt(ctx.novel_dir, attempts[0]).root
    manifest = stage2_runs.create_repair_manifest(
        ctx, errors, architecture='extraction-ir-v3.1')
    decisions = tmp_path / 'tamper-decisions.json'
    decisions.write_text(json.dumps({'decisions': [{
        'attempt_id': attempts[0], 'action': 'decline', 'reason': '材料不足',
    }]}, ensure_ascii=False), encoding='utf-8')
    stage2_runs.submit_batch(ctx.novel_dir, manifest, decisions, actor='operator')
    target = (manifest.with_name(stage2_runs.RUN_FILE) if tamper == 'run'
              else record_root / response_repair.REJECTION_FILE)
    target.write_bytes(target.read_bytes() + b' ')
    with pytest.raises(ProvenanceRefused, match='已漂移'):
        response_repair.find_attempt(ctx.novel_dir, attempts[0])


def test_manifest_refuses_attempt_with_existing_single_resolution(tmp_path):
    ctx = _ctx(tmp_path, new_run_id())
    _ensure_run(
        ctx, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=[(1, 'S', 'U')])
    errors, attempts = _seed_pending_attempts(ctx, 1)
    response_repair.submit(
        ctx.novel_dir, attempts[0], actor='legacy-operator',
        reason='旧单项路径已先完成')

    with pytest.raises(TerminalState, match='已有单项或 batch resolution'):
        stage2_runs.create_repair_manifest(
            ctx, errors, architecture='extraction-ir-v3.1')
    assert not (stage2_runs.paths.stage2_blueprint_run_path(
        ctx.novel_dir, ctx.run_id) / stage2_runs.MANIFEST_FILE).exists()


@pytest.mark.parametrize('single_kind', ['submit', 'deterministic'])
@pytest.mark.parametrize('winner', ['manifest', 'single'])
def test_manifest_and_single_resolution_race_has_exactly_one_authority(
        tmp_path, monkeypatch, single_kind, winner):
    """两个写手经 barrier 同时到达；ownership 胜者落盘，败者必须明确关闭。"""
    ctx = _ctx(tmp_path, new_run_id())
    _ensure_run(
        ctx, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=[(1, 'S', 'U')])
    errors, attempts = _seed_pending_attempts(ctx, 1)
    attempt_id = attempts[0]
    record = response_repair.find_attempt(ctx.novel_dir, attempt_id)
    manifest_path = stage2_runs.paths.stage2_blueprint_run_path(
        ctx.novel_dir, ctx.run_id) / stage2_runs.MANIFEST_FILE
    single_path = record.root / response_repair.RESOLUTION_FILE

    rendezvous = threading.Barrier(2)
    winner_has_lock = threading.Event()
    role = threading.local()
    real_ownership_lock = stage2_runs.repair_ownership_lock

    @contextmanager
    def ordered_ownership_lock(novel_dir):
        current = role.value
        if current != winner and not winner_has_lock.wait(timeout=5):
            raise AssertionError('预定胜者未进入 ownership 临界区')
        with real_ownership_lock(novel_dir):
            if current == winner:
                winner_has_lock.set()
            yield

    monkeypatch.setattr(
        stage2_runs, 'repair_ownership_lock', ordered_ownership_lock)

    def compete(current: str):
        role.value = current
        rendezvous.wait(timeout=5)
        try:
            if current == 'manifest':
                result = stage2_runs.create_repair_manifest(
                    ctx, errors, architecture='extraction-ir-v3.1')
            elif single_kind == 'submit':
                result = response_repair.submit(
                    ctx.novel_dir, attempt_id, actor='operator',
                    reason='并发单项决定', decline=True)
            else:
                result = response_repair.submit_deterministic(
                    record, {'blueprints': []}, rule='test-only')
            return 'ok', result
        except BaseException as exc:  # 交给主测试线程断言异常类型与文案。
            return 'error', exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            current: pool.submit(compete, current)
            for current in ('manifest', 'single')
        }
        outcomes = {
            current: future.result(timeout=10)
            for current, future in futures.items()
        }

    loser = 'single' if winner == 'manifest' else 'manifest'
    assert outcomes[winner][0] == 'ok'
    assert outcomes[loser][0] == 'error'
    assert isinstance(outcomes[loser][1], TerminalState)
    if winner == 'manifest':
        assert ('Stage2 batch manifest' in str(outcomes[loser][1])
                or 'batch 收编后' in str(outcomes[loser][1]))
    else:
        assert '已有单项或 batch resolution' in str(outcomes[loser][1])

    assert manifest_path.is_file() is (winner == 'manifest')
    assert single_path.is_file() is (winner == 'single')
    assert not (manifest_path.is_file() and single_path.is_file())
    assert (ctx.novel_dir / stage2_runs.paths.STAGE2_RUNS_DIRNAME
            / f'{stage2_runs.OWNERSHIP_LOCK_TARGET}.lock').is_file()


def test_checkpoint_or_terminal_error_precedes_batch_manifest(tmp_path):
    ctx = _ctx(tmp_path, new_run_id())
    _ensure_run(
        ctx, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=[(1, 'S', 'U')])
    pending, _attempts = _seed_pending_attempts(ctx, 1)

    with pytest.raises(ArtifactUnreadable, match='checkpoint broken'):
        extract._raise_response_repair(
            ctx, [pending[0], ArtifactUnreadable('checkpoint broken')],
            architecture='extraction-ir-v3.1')
    assert not (stage2_runs.paths.stage2_blueprint_run_path(
        ctx.novel_dir, ctx.run_id) / stage2_runs.MANIFEST_FILE).exists()


def test_checkpoint_write_failure_still_drains_and_saves_other_successes(
        tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, new_run_id())
    requests = [(chapter, 'SYSTEM', f'USER {chapter}') for chapter in (1, 2, 3)]
    _ensure_run(
        ctx, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=requests)
    original_write = stage2_runs.write_checkpoint
    attempted: list[int] = []

    def write_with_one_failure(*args, **kwargs):
        chapter = int(kwargs['chapter'])
        attempted.append(chapter)
        if chapter == 1:
            raise ArtifactUnreadable('synthetic checkpoint IO failure')
        return original_write(*args, **kwargs)

    monkeypatch.setattr(stage2_runs, 'write_checkpoint', write_with_one_failure)
    raw, _violations, errors = extract._run_chapter_requests(
        ctx, _SyntheticChapterClient(ctx), requests,
        model_cls=_ChapterRows, checkpoints=True,
        architecture='extraction-ir-v3.1')

    assert sorted(attempted) == [1, 2, 3]
    assert [row['chapter_ordinal'] for row in raw] == [2, 3]
    assert len(errors) == 1 and isinstance(errors[0], ArtifactUnreadable)
    for chapter in (2, 3):
        assert stage2_runs.paths.stage2_blueprint_checkpoint_path(
            ctx.novel_dir, ctx.run_id, 'blueprint', chapter).is_file()


def test_batch_decisions_are_exact_and_candidate_is_sealed(tmp_path):
    ctx = _ctx(tmp_path, new_run_id())
    _ensure_run(
        ctx, architecture='extraction-ir-v3.1',
        topology='scaffolded_single', requests=[(1, 'S', 'U')])
    errors, attempts = _seed_pending_attempts(ctx, 2)
    manifest = stage2_runs.create_repair_manifest(
        ctx, errors, architecture='extraction-ir-v3.1')

    incomplete = tmp_path / 'incomplete.json'
    incomplete.write_text(json.dumps({'decisions': [{
        'attempt_id': attempts[0], 'action': 'decline', 'reason': 'no',
    }]}), encoding='utf-8')
    with pytest.raises(TerminalState, match='完全一致'):
        stage2_runs.submit_batch(
            ctx.novel_dir, manifest, incomplete, actor='operator')

    candidate = response_repair.candidate_path(
        response_repair.find_attempt(ctx.novel_dir, attempts[0]))
    decisions = tmp_path / 'all.json'
    decisions.write_text(json.dumps({'decisions': [
        {'attempt_id': attempts[0], 'action': 'submit', 'reason': '只修封装'},
        {'attempt_id': attempts[1], 'action': 'decline', 'reason': '材料不足'},
    ]}, ensure_ascii=False), encoding='utf-8')
    candidate.write_text('1', encoding='utf-8')
    with pytest.raises(TerminalState, match='顶层 object'):
        stage2_runs.submit_batch(
            ctx.novel_dir, manifest, decisions, actor='operator')
    assert not manifest.with_name(stage2_runs.RESOLUTION_FILE).exists()

    candidate.write_text('{"blueprints":[{"chapter_ordinal":1}]}', encoding='utf-8')
    resolution = stage2_runs.submit_batch(
        ctx.novel_dir, manifest, decisions, actor='operator')
    sealed = json.loads(resolution.read_text(encoding='utf-8'))
    submitted = next(row for row in sealed['decisions']
                     if row['attempt_id'] == attempts[0])
    before = submitted['candidate_sha256']
    candidate.write_text('{}', encoding='utf-8')
    replay = response_repair.find_attempt(ctx.novel_dir, attempts[0]).resolution
    assert replay is not None and replay['candidate_sha256'] == before
    assert replay['candidate'] == {'blueprints': [{'chapter_ordinal': 1}]}

    resolution_raw = json.loads(resolution.read_text(encoding='utf-8'))
    resolution_raw['decisions'][0]['candidate'] = {
        'blueprints': [{'chapter_ordinal': 999}],
    }
    resolution.write_text(
        json.dumps(resolution_raw, ensure_ascii=False), encoding='utf-8')
    with pytest.raises(ArtifactUnreadable, match='resolution_sha256'):
        response_repair.find_attempt(ctx.novel_dir, attempts[0])


def test_failure_signature_is_stable_and_distinguishes_recovery_action_inputs():
    common = dict(
        request_fingerprint='a' * 64, model='flash', reasoning_effort='high',
        thinking=True, request_mode='original', finish_reason='stop',
        gate='schema', reject_detail=[{'loc': 'blueprints[]', 'type': 'missing'}],
        why='ignored when detail exists',
    )
    first = response_repair.failure_signature(**common)
    reordered = response_repair.failure_signature(**{
        **common,
        'reject_detail': [dict(type='missing', loc='blueprints[]')],
    })
    changed = response_repair.failure_signature(**{
        **common, 'request_mode': 'constraint_rebuild:full',
    })
    assert first == reordered and first != changed
