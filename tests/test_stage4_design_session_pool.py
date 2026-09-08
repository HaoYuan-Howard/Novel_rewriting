#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4A strategy/逐章私有 session pool 的隔离、并发与恢复纵切。"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from contracts.invariants import STAGE4_DESIGN_MAX_CONCURRENCY
from contracts.schema.api import DesignRepairScope
from contracts.schema.orchestration import AgentProfile
from novel import agent_session_audit, prov
from novel.agents import (
    AgentDriver, AgentProbe, AgentRequest, AgentTurn, AgentUsage,
)
from novel.errors import ProvenanceRefused
from novel.llm import stage4_runs
from novel.llm.cache import CachePolicy
from novel.llm.client import LlmFailed
from novel.llm.ledger import CostLedger, ledger_path
from novel.node_agents import (
    Stage4DesignAgent, Stage4DesignSessionPool, build_node_agent,
)
from novel.run import RunContext


class _Answer(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    value: int


_MAX_TOKENS = 4096


@pytest.fixture(autouse=True)
def _stable_code(monkeypatch):
    monkeypatch.setattr(prov, 'code_version', lambda: 'test-stage4-design-pool')


def _profile() -> AgentProfile:
    return AgentProfile(
        driver='codex', model='gpt-5.6-sol', reasoning_effort='high',
        execution_mode='standard')


def _ctx(base: Path, *, run_id: str | None = None) -> RunContext:
    from novel.work_units import initialize_workflow
    initialize_workflow(base)
    book = base / 'Stage4多会话_901'
    book.mkdir(exist_ok=True)
    ledger = (
        CostLedger(path=ledger_path(base), run_id=run_id)
        if run_id is not None else CostLedger(path=ledger_path(base))
    )
    return RunContext(
        novel_dir=book, node='stage4.design', paid=True,
        code=prov.code_version(), cache=CachePolicy.default(), ledger=ledger,
        executor_profile=_profile(), run_id=run_id or '',
        observer=lambda _event: None,
    )


def _request(chapter: int | None) -> stage4_runs.DesignUnitRequest:
    key = stage4_runs.design_unit_key(chapter)
    return stage4_runs.DesignUnitRequest(
        chapter=chapter,
        input_sha256=(
            ('a' if chapter is None else format(chapter, 'x')) * 64)[:64],
        system=f'系统-{key}',
        user=f'输入-{key}',
        response_model=_Answer,
        max_tokens=_MAX_TOKENS,
    )


def _ensure(ctx: RunContext, chapters=(None, 1, 2)) -> None:
    for chapter in chapters:
        stage4_runs.ensure_design_unit_run(ctx, _request(chapter))


class _Overlap:
    def __init__(self, parties: int = 2) -> None:
        self.barrier = threading.Barrier(parties, timeout=3)
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.active = 0
        self.maximum = 0

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
        self, purpose: str, responses: list[str], *,
        provider_session_id: str | None = None,
        overlap: _Overlap | None = None,
    ) -> None:
        self.purpose = purpose
        self.responses = list(responses)
        self.session_id = provider_session_id or f'provider-{purpose}'
        self.overlap = overlap
        self.probe_calls = 0
        self.calls: list[tuple[str, str | None, AgentRequest]] = []
        self.close_calls = 0

    def probe(self) -> AgentProbe:
        self.probe_calls += 1
        return AgentProbe(
            driver=self.driver, provider=self.provider,
            executable='fake-codex', available=True, version='0.stage4-test')

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


class _Factory:
    def __init__(
        self, plans: dict[str, list[str]], *, overlap: _Overlap | None = None,
        shared_provider_session: bool = False,
        provider_session_prefix: str = 'provider',
    ) -> None:
        self.plans = plans
        self.overlap = overlap
        self.shared_provider_session = shared_provider_session
        self.provider_session_prefix = provider_session_prefix
        self.calls: list[tuple[str, int | None]] = []
        self.created: dict[str, _Driver] = {}

    def __call__(self, purpose: str, chapter: int | None) -> AgentDriver:
        self.calls.append((purpose, chapter))
        driver = _Driver(
            purpose, self.plans[purpose],
            provider_session_id=(
                'shared-provider' if self.shared_provider_session
                else f'{self.provider_session_prefix}-'
                     f'{stage4_runs.design_unit_key(chapter)}'),
            overlap=self.overlap if chapter is not None else None,
        )
        self.created[purpose] = driver
        return driver


def _unit_tuple(chapter: int | None) -> tuple[str, int | None]:
    return stage4_runs.design_unit_purpose(chapter), chapter


def _wire_and_checkpoint(
    ctx: RunContext, pool: Stage4DesignSessionPool, chapter: int | None,
) -> _Answer:
    request = _request(chapter)
    purpose = stage4_runs.design_unit_purpose(chapter)
    response = pool.ask_json(
        request.system, request.user,
        model_cls=_Answer, purpose=purpose,
        max_tokens=request.max_tokens,
        recovery_policy=stage4_runs.DESIGN_UNIT_RECOVERY_POLICY)
    stage4_runs.write_design_unit_checkpoint(
        ctx, request,
        response_payload=response.model_dump(by_alias=True),
        acceptance=pool.accepted_checkpoint(purpose),
        state_payload={'kind': 'test-terminal', 'value': response.value})
    return response


def _restore(
    ctx: RunContext, pool: Stage4DesignSessionPool, chapter: int | None,
) -> _Answer | None:
    request = _request(chapter)
    restored = stage4_runs.restore_design_unit_checkpoint(ctx, request)
    if restored is None:
        return None
    purpose = stage4_runs.design_unit_purpose(chapter)
    pool.restore_checkpoint_acceptance(
        purpose, restored.acceptance,
        system=request.system, user=request.user,
        max_tokens=request.max_tokens,
        recovery_policy=stage4_runs.DESIGN_UNIT_RECOVERY_POLICY)
    return _Answer.model_validate(restored.value)


def _ended(base: Path) -> list:
    return [
        event for event in agent_session_audit.read_events(base)
        if event.kind == 'ended'
    ]


def test_strategy_then_chapters_precreate_and_overlap_with_private_sessions(
    tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path)
    _ensure(ctx)
    overlap = _Overlap()
    plans = {
        stage4_runs.design_unit_purpose(None): ['{"value":10}'],
        stage4_runs.design_unit_purpose(1): ['{"value":11}'],
        stage4_runs.design_unit_purpose(2): ['{"value":12}'],
    }
    factory = _Factory(plans, overlap=overlap)
    pool = Stage4DesignSessionPool(
        ctx, driver_factory=factory, concurrency=99)
    assert pool.concurrency == STAGE4_DESIGN_MAX_CONCURRENCY == 4

    pool.prepare_initial_sessions((_unit_tuple(None),))
    assert _wire_and_checkpoint(ctx, pool, None).value == 10
    pool.prepare_initial_sessions((_unit_tuple(1), _unit_tuple(2)))
    assert [chapter for _purpose, chapter in factory.calls] == [None, 1, 2]
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            chapter: executor.submit(_wire_and_checkpoint, ctx, pool, chapter)
            for chapter in (1, 2)
        }
        values = {chapter: future.result().value
                  for chapter, future in futures.items()}

    assert values == {1: 11, 2: 12}
    assert overlap.maximum == 2
    assert sum(driver.probe_calls for driver in factory.created.values()) == 1
    requests = [driver.calls[0][2] for driver in factory.created.values()]
    assert len({request.cwd for request in requests}) == 3
    assert len({driver.session_id for driver in factory.created.values()}) == 3
    pool.finish_all(delivered=True)
    pool.close()
    assert all(driver.close_calls == 1 for driver in factory.created.values())
    assert all(event.delivery_state == 'delivered' for event in _ended(tmp_path))


def test_partial_failure_keeps_passed_checkpoint_and_marks_peer_blocked(
    tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path)
    _ensure(ctx, chapters=(1, 2))
    p1, p2 = (stage4_runs.design_unit_purpose(value) for value in (1, 2))
    factory = _Factory({p1: ['{"value":1}'], p2: ['{}']})
    pool = Stage4DesignSessionPool(
        ctx, driver_factory=factory, max_turns=1, concurrency=2)
    pool.prepare_initial_sessions((_unit_tuple(1), _unit_tuple(2)))

    values: dict[int, _Answer] = {}
    failures: dict[int, BaseException] = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            chapter: executor.submit(_wire_and_checkpoint, ctx, pool, chapter)
            for chapter in (1, 2)
        }
        for chapter, future in futures.items():
            try:
                values[chapter] = future.result()
            except BaseException as exc:
                failures[chapter] = exc

    assert values[1].value == 1
    assert isinstance(failures[2], LlmFailed)
    assert stage4_runs.restore_design_unit_checkpoint(
        ctx, _request(1)) is not None
    assert stage4_runs.restore_design_unit_checkpoint(
        ctx, _request(2)) is None
    pool.finish_all(delivered=False, failure=failures[2])
    pool.close()
    ended = {event.provider_session_id: event for event in _ended(tmp_path)}
    assert (ended['provider-ch0001'].process_state,
            ended['provider-ch0001'].delivery_state,
            ended['provider-ch0001'].error_kind) == (
                'ok', 'blocked', 'peer_result_missing')
    assert (ended['provider-ch0002'].process_state,
            ended['provider-ch0002'].delivery_state,
            ended['provider-ch0002'].error_kind) == (
                'ok', 'failed', 'response_validation')

    restarted = _ctx(tmp_path, run_id=ctx.run_id)
    _ensure(restarted, chapters=(1, 2))
    retry_factory = _Factory(
        {p2: ['{"value":2}']}, provider_session_prefix='retry')
    retry = Stage4DesignSessionPool(
        restarted, driver_factory=retry_factory, concurrency=2)
    assert _restore(restarted, retry, 1) == _Answer(value=1)
    retry.prepare_initial_sessions((_unit_tuple(1), _unit_tuple(2)))
    assert retry_factory.calls == [(p2, 2)], \
        '已通过 ch1 必须零 factory；只为缺失 ch2 新建私有 session'
    assert _wire_and_checkpoint(restarted, retry, 2).value == 2
    retry.finish_all(delivered=True)
    retry.close()


def test_full_checkpoint_restore_is_zero_factory_probe_start(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _ensure(ctx)
    purposes = {
        chapter: stage4_runs.design_unit_purpose(chapter)
        for chapter in (None, 1, 2)
    }
    factory = _Factory({
        purposes[None]: ['{"value":10}'],
        purposes[1]: ['{"value":11}'],
        purposes[2]: ['{"value":12}'],
    })
    live = Stage4DesignSessionPool(ctx, driver_factory=factory)
    live.prepare_initial_sessions(tuple(
        _unit_tuple(chapter) for chapter in (None, 1, 2)))
    for chapter in (None, 1, 2):
        _wire_and_checkpoint(ctx, live, chapter)
    live.close()  # 模拟 checkpoint 已稳、ended 事件尚未写入时进程退出。
    assert _ended(tmp_path) == []

    restarted = _ctx(tmp_path, run_id=ctx.run_id)
    _ensure(restarted)
    factory_calls: list[tuple[str, int | None]] = []

    def forbidden_factory(purpose: str, chapter: int | None) -> AgentDriver:
        factory_calls.append((purpose, chapter))
        raise AssertionError('完整恢复不应构造 driver')

    restored_pool = Stage4DesignSessionPool(
        restarted, driver_factory=forbidden_factory)
    for chapter in (None, 1, 2):
        assert _restore(restarted, restored_pool, chapter) is not None
    restored_pool.prepare_initial_sessions(tuple(
        _unit_tuple(chapter) for chapter in (None, 1, 2)))
    assert factory_calls == []
    assert restored_pool.session_count == 0
    assert all(restored_pool.accepted_attempt(purpose) is not None
               for purpose in purposes.values())
    restored_pool.finish_all(delivered=True)
    restored_pool.close()
    assert len(_ended(tmp_path)) == 3
    assert all(event.delivery_state == 'delivered' for event in _ended(tmp_path))


def test_reused_driver_rejected_before_any_model_start(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    shared = _Driver('shared', ['{"value":1}'])
    pool = Stage4DesignSessionPool(
        ctx, driver_factory=lambda _purpose, _chapter: shared)

    with pytest.raises(ProvenanceRefused, match='同一对象'):
        pool.prepare_initial_sessions((_unit_tuple(1), _unit_tuple(2)))

    assert shared.calls == []
    assert shared.close_calls == 1
    assert pool.isolation_failed


def test_reused_provider_session_marks_both_units_isolation_failed(
    tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path)
    _ensure(ctx, chapters=(1, 2))
    p1, p2 = (stage4_runs.design_unit_purpose(value) for value in (1, 2))
    factory = _Factory(
        {p1: ['{"value":1}'], p2: ['{"value":2}']},
        shared_provider_session=True)
    pool = Stage4DesignSessionPool(ctx, driver_factory=factory, concurrency=2)
    pool.prepare_initial_sessions((_unit_tuple(1), _unit_tuple(2)))

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_wire_and_checkpoint, ctx, pool, chapter)
            for chapter in (1, 2)
        ]
        failures = []
        for future in futures:
            try:
                future.result()
            except BaseException as exc:
                failures.append(exc)

    assert failures
    assert pool.isolation_failed
    pool.finish_all(delivered=False, failure=failures[0])
    pool.close()
    ended = _ended(tmp_path)
    assert all(
        (event.process_state, event.delivery_state, event.error_kind)
        == ('failed', 'unknown', 'session_isolation')
        for event in ended)


def test_build_node_agent_requires_stage4_factory_and_caps_concurrency(
    tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path)
    factory = _Factory({})
    agent = build_node_agent(
        ctx, stage4_design_driver_factory=factory, concurrency=99)
    assert isinstance(agent, Stage4DesignAgent)
    assert agent.pool.concurrency == STAGE4_DESIGN_MAX_CONCURRENCY
    agent.pool.close()

    with pytest.raises(ValueError, match='不能共享一个 driver'):
        build_node_agent(
            ctx, driver=_Driver('shared', ['{"value":1}']))


def test_build_node_agent_forwards_typed_repair_and_shared_four_turn_budget(
        tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path)
    scope = DesignRepairScope.model_validate({
        'candidate_sha256': 'a' * 64,
        'aggregate_subject_sha256': 'b' * 64,
        'decision_sha256': 'c' * 64,
        'repair_capability': {
            'repairable': True, 'reason_code': 'ready',
            'chapters': [{
                'output_ordinal': 1, 'committed_rounds': 0,
                'request_repair_available': True,
            }],
        },
        'strategy_replan': False,
        'target_chapters': [1],
        'chapters': [{
            'output_ordinal': 1,
            'subject_sha256': 'd' * 64,
            'constraints': [{
                'code': 'scene_goal',
                'scene_refs': ['cs0001'], 'opaque_aliases': ['fa0001'],
            }],
        }],
    })
    agent = build_node_agent(
        ctx,
        stage4_design_driver_factory=_Factory({}),
        max_turns=99,
        repair_decision_sha256=scope.decision_sha256,
        repair_scope=scope,
    )
    assert isinstance(agent, Stage4DesignAgent)
    assert agent.repair_decision_sha256 == scope.decision_sha256
    assert agent.repair_scope == scope
    assert agent.pool.max_turns == 4
    agent.pool.close()
