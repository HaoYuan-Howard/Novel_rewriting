#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage6 semantic 双判官 executor pool 的零成本隔离/恢复纵切。"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict

from contracts.schema.orchestration import AgentProfile
from novel import agent_session_audit, paths, prov
from novel.agents import (
    AgentDriver, AgentProbe, AgentRequest, AgentTurn, AgentUsage,
)
from novel.errors import ProvenanceRefused
from novel.llm import stage6_runs
from novel.llm.cache import CachePolicy
from novel.llm.client import LlmFailed
from novel.llm.ledger import CostLedger, ledger_path
from novel.node_agents import (
    SemanticSessionPool, Stage6SemanticAgent, build_node_agent,
)
from novel.run import RunContext
from novel.stages.stage6 import semantic


class _Answer(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    value: int


_SYSTEM = '稳定的双盲系统提示'
_USERS = {'A': '匿名材料\n判官 A', 'B': '匿名材料\n判官 B'}
_INPUT_SHA256 = 'a' * 64
_MAX_TOKENS = 4096


@pytest.fixture(autouse=True)
def _stable_code(monkeypatch):
    monkeypatch.setattr(prov, 'code_version', lambda: 'test-stage6-semantic-agent')


def _profile() -> AgentProfile:
    return AgentProfile(
        driver='codex', model='gpt-5.6-sol', reasoning_effort='high',
        execution_mode='standard')


def _ctx(base: Path, *, run_id: str | None = None) -> RunContext:
    book = base / '双盲执行者_811'
    book.mkdir(exist_ok=True)
    ledger = (
        CostLedger(path=ledger_path(base), run_id=run_id)
        if run_id is not None else CostLedger(path=ledger_path(base))
    )
    return RunContext(
        novel_dir=book, node='stage6.semantic', paid=True,
        code=prov.code_version(), cache=CachePolicy.default(), ledger=ledger,
        executor_profile=_profile(), run_id=run_id or '',
        observer=lambda _event: None,
    )


def _ensure(ctx: RunContext) -> None:
    stage6_runs.ensure_run(
        ctx, input_sha256=_INPUT_SHA256, system=_SYSTEM, users=_USERS,
        response_model=_Answer, max_tokens=_MAX_TOKENS)


class _Overlap:
    def __init__(self, parties: int = 2) -> None:
        self.barrier = threading.Barrier(parties, timeout=3)
        self.lock = threading.Lock()
        self.active = 0
        self.maximum = 0
        self.release = threading.Event()

    def enter(self) -> None:
        self.barrier.wait()
        with self.lock:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            if self.active == self.barrier.parties:
                self.release.set()
        assert self.release.wait(timeout=3)
        with self.lock:
            self.active -= 1


class _Driver(AgentDriver):
    driver = 'codex'
    provider = 'openai'

    def __init__(
        self, role: str, responses: list[str], *,
        provider_session_id: str | None = None,
        overlap: _Overlap | None = None,
        close_failure: bool = False,
    ) -> None:
        self.role = role
        self.responses = list(responses)
        self.session_id = provider_session_id or f'provider-{role}'
        self.overlap = overlap
        self.close_failure = close_failure
        self.probe_calls = 0
        self.calls: list[tuple[str, str | None, AgentRequest]] = []
        self.close_calls = 0

    def probe(self) -> AgentProbe:
        self.probe_calls += 1
        return AgentProbe(
            driver=self.driver, provider=self.provider,
            executable='fake-codex', available=True, version='0.semantic-test')

    def _turn(self) -> AgentTurn:
        return AgentTurn(
            session_id=self.session_id,
            content=self.responses.pop(0),
            model=None, actual_reasoning_effort=None,
            usage=AgentUsage(prompt_tokens=20, completion_tokens=3),
            turn_count=1,
        )

    def start(self, profile: AgentProfile, request: AgentRequest) -> AgentTurn:
        assert profile == _profile()
        self.calls.append(('start', None, request))
        if self.overlap is not None:
            self.overlap.enter()
        return self._turn()

    def resume(
        self, profile: AgentProfile, session_id: str, request: AgentRequest,
    ) -> AgentTurn:
        assert profile == _profile()
        assert session_id == self.session_id
        self.calls.append(('resume', session_id, request))
        return self._turn()

    def cancel(self, session_id: str | None = None) -> bool:
        return False

    def close(self) -> None:
        self.close_calls += 1
        if self.close_failure:
            raise RuntimeError(f'close {self.role} failed')


class _Factory:
    def __init__(
        self, plans: dict[str, list[str]], *, overlap: _Overlap | None = None,
        shared_provider_session: bool = False,
        provider_session_prefix: str = 'provider',
        close_failure: str | None = None,
    ) -> None:
        self.plans = plans
        self.overlap = overlap
        self.shared_provider_session = shared_provider_session
        self.provider_session_prefix = provider_session_prefix
        self.close_failure = close_failure
        self.created: dict[str, _Driver] = {}
        self.calls: list[str] = []

    def __call__(self, role: str) -> AgentDriver:
        self.calls.append(role)
        driver = _Driver(
            role,
            self.plans[role],
            provider_session_id=(
                'shared-provider' if self.shared_provider_session
                else f'{self.provider_session_prefix}-{role}'),
            overlap=self.overlap,
            close_failure=self.close_failure == role,
        )
        self.created[role] = driver
        return driver


def _wire_and_checkpoint(
    ctx: RunContext, pool: SemanticSessionPool, role: str,
) -> _Answer:
    purpose = f'stage6.semantic.{role}'
    response = pool.ask_json(
        _SYSTEM, _USERS[role], model_cls=_Answer, purpose=purpose,
        max_tokens=_MAX_TOKENS,
        recovery_policy=stage6_runs.RECOVERY_POLICY)
    stage6_runs.write_checkpoint(
        ctx, role=role, input_sha256=_INPUT_SHA256,
        system=_SYSTEM, user=_USERS[role], response_model=_Answer,
        max_tokens=_MAX_TOKENS,
        response_payload=response.model_dump(by_alias=True),
        acceptance=pool.accepted_checkpoint(purpose))
    return response


def _request_both(
    ctx: RunContext, pool: SemanticSessionPool,
) -> tuple[dict[str, _Answer], dict[str, BaseException]]:
    values: dict[str, _Answer] = {}
    failures: dict[str, BaseException] = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            role: executor.submit(_wire_and_checkpoint, ctx, pool, role)
            for role in ('A', 'B')
        }
        for role in ('A', 'B'):
            try:
                values[role] = futures[role].result()
            except BaseException as exc:
                failures[role] = exc
    return values, failures


def _restore_role(
    ctx: RunContext, pool: SemanticSessionPool, role: str,
) -> _Answer | None:
    restored = stage6_runs.restore_checkpoint(
        ctx, role=role, input_sha256=_INPUT_SHA256,
        system=_SYSTEM, user=_USERS[role], response_model=_Answer,
        max_tokens=_MAX_TOKENS)
    if restored is None:
        return None
    response, acceptance = restored
    pool.restore_checkpoint_acceptance(
        f'stage6.semantic.{role}', acceptance,
        system=_SYSTEM, user=_USERS[role], max_tokens=_MAX_TOKENS,
        recovery_policy=stage6_runs.RECOVERY_POLICY)
    return response


def _ended(base: Path) -> list:
    return [
        event for event in agent_session_audit.read_events(base)
        if event.kind == 'ended'
    ]


def test_two_judges_precreate_then_overlap_with_private_cwd_and_session(tmp_path):
    ctx = _ctx(tmp_path)
    _ensure(ctx)
    overlap = _Overlap()
    factory = _Factory(
        {'A': ['{"value":1}'], 'B': ['{"value":2}']}, overlap=overlap)
    pool = SemanticSessionPool(ctx, driver_factory=factory, max_turns=2)

    pool.prepare_initial_sessions()
    values, failures = _request_both(ctx, pool)

    assert failures == {}
    assert {role: value.value for role, value in values.items()} == {'A': 1, 'B': 2}
    assert factory.calls == ['A', 'B'], '两个 driver 必须在任一 ask 前全部造完'
    assert sum(driver.probe_calls for driver in factory.created.values()) == 1
    assert overlap.maximum == 2, 'A/B 必须真实重叠，而不是顺序伪并发'
    requests = [driver.calls[0][2] for driver in factory.created.values()]
    assert len({request.cwd for request in requests}) == 2
    assert {driver.session_id for driver in factory.created.values()} == {
        'provider-A', 'provider-B'}
    pool.finish_all(delivered=True)
    pool.close()
    assert all(driver.close_calls == 1 for driver in factory.created.values())
    assert {event.provider_session_id for event in _ended(tmp_path)} == {
        'provider-A', 'provider-B'}
    assert all(event.delivery_state == 'delivered' for event in _ended(tmp_path))


def test_each_judge_only_resumes_its_own_provider_session(tmp_path):
    ctx = _ctx(tmp_path)
    _ensure(ctx)
    factory = _Factory({
        'A': ['{}', '{"value":11}'],
        'B': ['{}', '{"value":22}'],
    })
    pool = SemanticSessionPool(ctx, driver_factory=factory, max_turns=2)
    pool.prepare_initial_sessions()

    values, failures = _request_both(ctx, pool)

    assert failures == {}
    assert {role: value.value for role, value in values.items()} == {'A': 11, 'B': 22}
    for role, driver in factory.created.items():
        assert [kind for kind, _session, _request in driver.calls] == [
            'start', 'resume']
        assert driver.calls[1][1] == f'provider-{role}'
        assert driver.calls[0][2].cwd == driver.calls[1][2].cwd
    pool.finish_all(delivered=True)
    pool.close()


def test_one_success_one_validation_failure_keeps_checkpoint_and_restarts_only_missing(
    tmp_path,
):
    ctx = _ctx(tmp_path)
    _ensure(ctx)
    first_factory = _Factory({'A': ['{"value":7}'], 'B': ['{}']})
    first = SemanticSessionPool(
        ctx, driver_factory=first_factory, max_turns=1)
    first.prepare_initial_sessions()

    values, failures = _request_both(ctx, first)

    assert values['A'].value == 7
    assert isinstance(failures['B'], LlmFailed)
    assert paths.stage6_semantic_checkpoint_path(
        ctx.novel_dir, ctx.run_id, 'A').is_file()
    assert not paths.stage6_semantic_checkpoint_path(
        ctx.novel_dir, ctx.run_id, 'B').exists()
    assert not list((ctx.novel_dir / 'stage6').glob('semantic_*.json'))
    first.finish_all(delivered=False, failure=failures['B'])
    first.close()
    ended = {
        event.provider_session_id: event for event in _ended(tmp_path)
    }
    assert (ended['provider-A'].process_state,
            ended['provider-A'].delivery_state,
            ended['provider-A'].error_kind) == (
                'ok', 'blocked', 'peer_result_missing')
    assert (ended['provider-B'].process_state,
            ended['provider-B'].delivery_state,
            ended['provider-B'].error_kind) == (
                'ok', 'failed', 'response_validation')

    restarted = _ctx(tmp_path, run_id=ctx.run_id)
    _ensure(restarted)
    second_factory = _Factory(
        {'B': ['{"value":8}']}, provider_session_prefix='provider-restarted')
    second = SemanticSessionPool(
        restarted, driver_factory=second_factory, max_turns=2)
    second.prepare_initial_sessions()
    restored_a = _restore_role(restarted, second, 'A')
    assert restored_a is not None and restored_a.value == 7
    assert second_factory.calls == ['B'], 'A checkpoint 恢复不得触发 factory/probe/start'

    response_b = _wire_and_checkpoint(restarted, second, 'B')
    assert response_b.value == 8
    assert second.accepted_attempt('stage6.semantic.A') is not None
    assert [kind for kind, _session, _request
            in first_factory.created['A'].calls] == ['start'], \
        '恢复不能 resume 历史 A provider session'
    second.finish_all(delivered=True)
    second.close()


def test_full_restore_constructs_no_driver_and_no_probe_or_start(tmp_path):
    ctx = _ctx(tmp_path)
    _ensure(ctx)
    factory = _Factory({'A': ['{"value":1}'], 'B': ['{"value":2}']})
    live = SemanticSessionPool(ctx, driver_factory=factory)
    live.prepare_initial_sessions()
    _values, failures = _request_both(ctx, live)
    assert failures == {}
    # 模拟 checkpoint 已稳、进程却在逐 session ended 之前退出。历史 driver
    # 可以关闭，但不能借此伪造交付结论。
    live.close()
    assert _ended(tmp_path) == []

    restarted = _ctx(tmp_path, run_id=ctx.run_id)
    _ensure(restarted)

    def forbidden_factory(role: str) -> AgentDriver:
        raise AssertionError(f'完整恢复不应构造 judge {role}')

    restored = SemanticSessionPool(
        restarted, driver_factory=forbidden_factory)
    restored.prepare_initial_sessions()
    assert _restore_role(restarted, restored, 'A') is not None
    assert _restore_role(restarted, restored, 'B') is not None
    assert restored.session_count == 0
    assert restored.turns_used == 0
    restored.finish_all(delivered=True)
    restored.close()
    ended = _ended(tmp_path)
    assert {event.provider_session_id for event in ended} == {
        'provider-A', 'provider-B'}
    assert all(
        (event.process_state, event.delivery_state, event.error_kind)
        == ('ok', 'delivered', None)
        for event in ended)

    # 同一 zero-wire 尾窗再次执行只命中公共幂等桥，不重复 ended。
    restored.finish_all(delivered=True)
    assert len(_ended(tmp_path)) == 2


def test_reused_driver_is_rejected_before_first_model_start(tmp_path):
    ctx = _ctx(tmp_path)
    _ensure(ctx)
    shared = _Driver('shared', ['{"value":1}'])
    pool = SemanticSessionPool(
        ctx, driver_factory=lambda _role: shared)

    with pytest.raises(ProvenanceRefused, match='同一对象'):
        pool.prepare_initial_sessions()

    assert shared.calls == []
    assert shared.close_calls == 1


def test_reused_provider_session_rejects_double_blind_and_closes_both(tmp_path):
    ctx = _ctx(tmp_path)
    _ensure(ctx)
    factory = _Factory(
        {'A': ['{"value":1}'], 'B': ['{"value":2}']},
        shared_provider_session=True)
    pool = SemanticSessionPool(ctx, driver_factory=factory)
    pool.prepare_initial_sessions()

    _values, failures = _request_both(ctx, pool)

    assert any(isinstance(exc, ProvenanceRefused) for exc in failures.values())
    assert pool.isolation_failed
    pool.finish_all(delivered=False, failure=next(iter(failures.values())))
    pool.close()
    assert all(driver.close_calls == 1 for driver in factory.created.values())
    assert all(event.delivery_state == 'unknown' for event in _ended(tmp_path))


def test_finish_and_close_visit_every_client_before_raising_aggregate(tmp_path):
    ctx = _ctx(tmp_path)
    _ensure(ctx)
    factory = _Factory(
        {'A': ['{"value":1}'], 'B': ['{"value":2}']},
        close_failure='A')
    pool = SemanticSessionPool(ctx, driver_factory=factory)
    pool.prepare_initial_sessions()
    _values, failures = _request_both(ctx, pool)
    assert failures == {}

    finish_calls: list[str] = []
    original_a = pool._clients['A'].finish
    original_b = pool._clients['B'].finish

    def fail_a(**kwargs):
        finish_calls.append('A')
        raise RuntimeError('ended A failed')

    def finish_b(**kwargs):
        finish_calls.append('B')
        return original_b(**kwargs)

    pool._clients['A'].finish = fail_a
    pool._clients['B'].finish = finish_b
    with pytest.raises(ExceptionGroup, match='ended'):
        pool.finish_all(delivered=True)
    assert finish_calls == ['A', 'B']

    with pytest.raises(ExceptionGroup, match='关闭'):
        pool.close()
    assert factory.created['A'].close_calls == 1
    assert factory.created['B'].close_calls == 1
    # 防止静态分析把保存的生产实现误当未使用；A 的实现刻意未调用。
    assert callable(original_a)


def test_stage6_agent_registers_pool_and_marks_delivered_only_after_finalize(
    tmp_path, monkeypatch,
):
    ctx = _ctx(tmp_path)
    factory = _Factory({'A': ['{"value":1}'], 'B': ['{"value":2}']})
    finalized: list[bool] = []

    prepared = SimpleNamespace()

    def prepare(got: RunContext):
        _ensure(got)
        return prepared

    def request(got: RunContext, source: SemanticSessionPool, _prepared):
        values, failures = _request_both(got, source)
        assert failures == {}
        return values

    def evaluate(_ctx, _source, _prepared, responses):
        return responses

    def finalize(_ctx, _source, _prepared, evaluation):
        assert set(evaluation) == {'A', 'B'}
        finalized.append(True)
        return (
            SimpleNamespace(kind='formal-readback-verified'),
            SimpleNamespace(passed=True),
        )

    original_finish_rows = SemanticSessionPool._finish_rows

    def guarded_finish(self, *, delivered, failure):
        if delivered:
            assert finalized == [True]
        return original_finish_rows(
            self, delivered=delivered, failure=failure)

    monkeypatch.setattr(semantic, 'prepare_semantic_candidate', prepare)
    monkeypatch.setattr(semantic, 'request_semantic_candidates', request)
    monkeypatch.setattr(semantic, 'evaluate_semantic_candidate', evaluate)
    monkeypatch.setattr(semantic, 'finalize_semantic_candidate', finalize)
    monkeypatch.setattr(SemanticSessionPool, '_finish_rows', guarded_finish)

    agent = build_node_agent(ctx, semantic_driver_factory=factory)
    assert isinstance(agent, Stage6SemanticAgent)
    result = agent.run()

    assert result.artifact.kind == 'formal-readback-verified'
    assert result.turns == 2
    assert all(event.delivery_state == 'delivered' for event in _ended(tmp_path))
    with pytest.raises(ValueError, match='共用一个 driver'):
        build_node_agent(ctx, driver=factory.created['A'])
