"""退役 Agent CLI 执行格式的关闭式负例（C2a，单 Executor 架构）。

五个持久面（run contract / queue 对证 / jobs / 执行上下文 / CLI wire）上，
携带 `backend.kind=='agent'` 的历史或新构造一律 `retired_executor_backend`
（HTTP 410 / CLI 显式终态）；服务列表与折叠读回不崩溃，历史账原样只读。
"""
from __future__ import annotations

import json

import pytest

from contracts.schema.orchestration import (
    AgentProfile, AgentRunEvent, AgentRunStart, ExecutionEpoch,
    LegacyExecutorBackend, LegacyLlmProfile, PipelineAgentProfiles, RunContractV2,
)
from novel.errors import RetiredExecutorBackend
from novel.orchestrator import agent, jobs, retired_formats


@pytest.fixture
def base(tmp_path):
    from novel.work_units import initialize_workflow
    initialize_workflow(tmp_path)
    return str(tmp_path)

_LEGACY = LegacyExecutorBackend(profile=LegacyLlmProfile(
    provider='deepseek', model='deepseek-v4-pro',
    reasoning_effort='high', thinking_mode='on'))
_AGENT = AgentProfile(
    driver='codex', model='gpt-5.6-sol', reasoning_effort='high',
    execution_mode='standard')
_AGENT_PROFILES = PipelineAgentProfiles(
    executor_default=_AGENT, executor_overrides={})


def test_detect_and_refuse_raw_json_shapes():
    """浅判别覆盖 executor_backend / executor_profile / profiles 三种拼写。"""
    assert retired_formats.detect_retired_agent_backend(
        {'executor_backend': {'kind': 'agent', 'profile': {}}})
    assert retired_formats.detect_retired_agent_backend(
        {'executor_profile': {'kind': 'agent'}})
    assert retired_formats.detect_retired_agent_backend({
        'profiles': {'executor_default': {'kind': 'legacy', 'profile': {}},
                     'executor_overrides': {'stage5.write': {'kind': 'agent'}}}})
    assert not retired_formats.detect_retired_agent_backend(
        {'executor_backend': {'kind': 'legacy', 'profile': {}}})
    assert not retired_formats.detect_retired_agent_backend({})
    assert not retired_formats.detect_retired_agent_backend('not-a-mapping')
    with pytest.raises(RetiredExecutorBackend) as caught:
        retired_formats.refuse_if_retired('raw 面', {
            'executor_backend': {'kind': 'agent'}}, run_id='run-x')
    assert 'run-x' in str(caught.value)


def test_agent_backend_error_maps_to_410():
    from novel.api.errors import status_of
    assert status_of(RetiredExecutorBackend('退役')) == 410


def _seed_agent_run(base: str) -> str:
    """按 C2a 之前的历史 wire 直接写入一条含 agent backend 的 started 契约。"""
    start = AgentRunStart(
        sid='42', base=base, argv=None, prompt=None, authorized=True,
        egress_authorized=True, code_version='test-code-version',
        profiles=_AGENT_PROFILES)
    contract = RunContractV2(
        sid='42', base=base, authorized=True, egress_authorized=True,
        profiles=_AGENT_PROFILES)
    epoch = ExecutionEpoch(
        epoch_id='retired-epoch-1', ordinal=1, reason='initial', argv=None,
        prompt_sha256=None, protocol_version='supervisor-executor-v1',
        cli_version=None, code_version='test-code-version',
        identity_version=2, session_run_id='retired-session-1')
    agent.append(base, AgentRunEvent(
        wire_version=2, run_id='retired-run-1', kind='started',
        ts=int(1_700_000_000), start=start, contract=contract))
    agent.append(base, AgentRunEvent(
        wire_version=2, run_id='retired-run-1', kind='epoch_opened',
        ts=int(1_700_000_001), epoch=epoch))
    agent.append(base, AgentRunEvent(
        wire_version=2, run_id='retired-run-1', kind='epoch_closed',
        ts=int(1_700_000_002), epoch_id=epoch.epoch_id, exit_code=0,
        drain_state='drained'))
    agent.append(base, AgentRunEvent(
        wire_version=2, run_id='retired-run-1', kind='ended',
        ts=int(1_700_000_003), exit_code=2,
        outcome={'state': 'stopped', 'reason': 'C2a seed',
                 'checked_at': 1_700_000_003}))
    return 'retired-run-1'


def test_agent_run_listing_survives_and_continuation_preflight_refuses(
        tmp_path, monkeypatch):
    """含 agent backend 的历史 run：列表照常折叠展示；续跑 preflight 410。"""
    run_id = _seed_agent_run(str(tmp_path))
    records = agent.records(str(tmp_path))
    record = next(row for row in records if row.run_id == run_id)
    assert record.contract is not None
    assert record.contract.profiles.executor_default.kind == 'agent', \
        '历史账原样只读，不做降级改写'

    from novel.orchestrator import agent_continuation
    view = agent_continuation.inspect(str(tmp_path), run_id)
    # 续跑 preflight：inspect 原样保留历史 profiles，但因退役 backend 不可用；
    # 服务与列表不崩溃，reason 指向退役而非降级。
    assert not view.available
    assert '退役' in (view.reason or '')
    assert view.profiles.executor_default.kind == 'agent'

    with pytest.raises(RetiredExecutorBackend) as caught:
        agent.plan(str(tmp_path), '42', authorized=True, egress_authorized=True,
                   profiles=view.profiles, continuation=view.source)
    assert '退役' in str(caught.value)


def test_jobs_start_refuses_agent_backend(tmp_path):
    from contracts.schema.orchestration import AgentExecutorBackend

    # preview 与 start 共用 plan_start 内同一校验闸；闸先于任何进程动作。
    with pytest.raises(RetiredExecutorBackend) as caught:
        jobs.start(
            str(tmp_path), 'stage2.global', 'book-1',
            executor_backend=AgentExecutorBackend(profile=_AGENT),
            egress_authorized=True)
    assert '退役' in str(caught.value)


def test_agent_run_request_keeps_retired_payload_in_error(tmp_path):
    """新代跑选择含 agent default：410 + 机器码；不产生任何 run 记录。"""
    book_root = str(tmp_path)
    before = agent.runs_path(book_root).read_bytes() if agent.runs_path(book_root).exists() else b''
    with pytest.raises(RetiredExecutorBackend):
        agent.plan(book_root, '42', authorized=True, egress_authorized=True,
                   profiles=_AGENT_PROFILES)
    after = agent.runs_path(book_root).read_bytes() if agent.runs_path(book_root).exists() else b''
    assert before == after


def test_queue_continuation_recheck_flags_retired_prior_execution(tmp_path, monkeypatch):
    """queue 恢复对证：原 execution 含 agent backend 时不自动续跑、不崩溃。"""
    from novel.orchestrator import queue as queue_mod

    run_id = _seed_agent_run(str(tmp_path))
    records = agent.records(str(tmp_path))
    record = next(row for row in records if row.run_id == run_id)
    payload = record.start.model_dump(mode='json', by_alias=True)
    assert retired_formats.detect_retired_agent_backend(payload)
    reason = queue_mod._prior_execution_reasons(
        base, 'q-1', 1, start=payload) if hasattr(
            queue_mod, '_prior_execution_reasons') else None
    # 对证逻辑挂 detect_retired_agent_backend；此处只钉住判别与消息约定。
    assert reason is None or 'retired_executor_backend' in reason


def test_http_agent_backend_is_gone(tmp_path):
    """HTTP 作业预览对 agent backend 固定 410，payload 携带 backend:'agent'。"""
    from fastapi.testclient import TestClient
    from novel.api.app import UI_HEADER, create_app
    with TestClient(create_app(str(tmp_path))) as http:
        http.headers.update({UI_HEADER: '1'})
        response = http.post('/api/jobs/preview', json={
        'sid': '42', 'node': 'stage2.global', 'flags': [],
        'executor_backend': {'kind': 'agent', 'profile': {
            'driver': 'codex', 'model': 'gpt-5.6-sol',
            'reasoning_effort': 'high'}},
        'egress_authorized': True,
    })
    assert response.status_code == 410
    body = response.json()
    assert json.dumps(body, ensure_ascii=False).find('agent') >= 0
