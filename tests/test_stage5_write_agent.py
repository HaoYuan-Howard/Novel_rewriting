#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage5 逐章 executor pool 的零真实模型隔离与 crash 恢复测试。"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from contracts.schema.orchestration import AgentProfile
from contracts.schema.stage5 import ChapterResponse
from novel import prov
from novel.agents import session_audit as agent_session_audit
from novel.agents import AgentDriver, AgentProbe, AgentRequest, AgentTurn, AgentUsage
from novel.errors import ProvenanceRefused
from novel.llm import stage5_runs
from novel.llm.cache import CachePolicy
from novel.llm.ledger import CostLedger, audit_path, ledger_path
from novel.orchestrator.node_agents import Stage5SessionPool, build_node_agent
from novel.observability import RequestProgress
from novel.run import RunContext
from novel.stages.stage5 import write


_INPUT_SHA256 = 'a' * 64
_SYSTEM = '稳定的闭卷写作系统提示'
_USERS = {
    1: '第一章的闭卷任务书',
    2: '第二章的闭卷任务书',
}


@pytest.fixture(autouse=True)
def _stable_code(monkeypatch):
    monkeypatch.setattr(prov, 'code_version', lambda: 'test-stage5-write-agent')


def _profile() -> AgentProfile:
    return AgentProfile(
        driver='codex', model='gpt-5.6-sol', reasoning_effort='high',
        execution_mode='standard')


def _ctx(base: Path, *, run_id: str | None = None) -> RunContext:
    from novel.work_units import require_workflow
    require_workflow(base)
    book = base / '逐章执行者_851'
    book.mkdir(exist_ok=True)
    ledger = (
        CostLedger(path=ledger_path(base), run_id=run_id)
        if run_id is not None else CostLedger(path=ledger_path(base))
    )
    return RunContext(
        novel_dir=book,
        node='stage5.write',
        paid=True,
        code=prov.code_version(),
        cache=CachePolicy.default(),
        ledger=ledger,
        executor_profile=_profile(),
        run_id=run_id or '',
        observer=lambda _event: None,
    )


def _payload(text: str) -> dict[str, object]:
    return {'segments': [{'slot': 's01', 'text': text}]}


class _Driver(AgentDriver):
    driver = 'codex'
    provider = 'openai'

    def __init__(self, responses: list[dict[str, object]], *, session_id: str):
        self.responses = [dict(item) for item in responses]
        self.session_id = session_id
        self.calls: list[tuple[str, AgentRequest]] = []
        self.probe_calls = 0
        self.close_calls = 0

    def probe(self) -> AgentProbe:
        self.probe_calls += 1
        return AgentProbe(
            driver=self.driver,
            provider=self.provider,
            executable='fake-codex',
            available=True,
            version='0.stage5-test',
        )

    def _turn(self) -> AgentTurn:
        return AgentTurn(
            session_id=self.session_id,
            content=json.dumps(self.responses.pop(0), ensure_ascii=False),
            model=None,
            actual_reasoning_effort=None,
            usage=AgentUsage(
                prompt_tokens=10,
                completion_tokens=5,
                reasoning_tokens=2,
                total_tokens=15,
            ),
            turn_count=1,
        )

    def start(self, profile: AgentProfile, request: AgentRequest) -> AgentTurn:
        assert profile == _profile()
        self.calls.append(('start', request))
        return self._turn()

    def resume(
        self,
        profile: AgentProfile,
        session_id: str,
        request: AgentRequest,
    ) -> AgentTurn:
        assert profile == _profile()
        assert session_id == self.session_id
        self.calls.append(('resume', request))
        return self._turn()

    def cancel(self, session_id: str | None = None) -> bool:
        return False

    def close(self) -> None:
        self.close_calls += 1


class _Factory:
    def __init__(
        self,
        plans: dict[tuple[int, int], tuple[list[dict[str, object]], str]],
    ) -> None:
        self.plans = plans
        self.created: list[tuple[int, int, _Driver]] = []
        self._counts: dict[int, int] = {}
        self._lock = threading.Lock()

    def __call__(self, chapter: int) -> AgentDriver:
        with self._lock:
            ordinal = self._counts.get(chapter, 0) + 1
            self._counts[chapter] = ordinal
            responses, session_id = self.plans[(chapter, ordinal)]
            driver = _Driver(responses, session_id=session_id)
            self.created.append((chapter, ordinal, driver))
            return driver

    def chapter(self, chapter: int) -> list[_Driver]:
        return [
            driver for value, _ordinal, driver in self.created
            if value == chapter
        ]


def _ask(pool: Stage5SessionPool, chapter: int) -> ChapterResponse:
    return pool.ask_json(
        _SYSTEM,
        _USERS[chapter],
        model_cls=ChapterResponse,
        purpose=stage5_runs.purpose_of(chapter),
        max_tokens=write.MAX_TOKENS,
        progress=RequestProgress(chapter=chapter, index=chapter, total=2),
        recovery_policy=stage5_runs.RECOVERY_POLICY,
    )


def _land_checkpoint(
    ctx: RunContext,
    pool: Stage5SessionPool,
    *,
    chapter: int = 1,
    passed: bool = False,
) -> dict[str, object]:
    stage5_runs.ensure_run(
        ctx,
        chapter=chapter,
        input_sha256=_INPUT_SHA256,
        system=_SYSTEM,
        user=_USERS[chapter],
        response_model=ChapterResponse,
        max_tokens=write.MAX_TOKENS,
    )
    response = _ask(pool, chapter)
    purpose = stage5_runs.purpose_of(chapter)
    acceptance = pool.accepted_checkpoint(purpose)
    stage5_runs.write_checkpoint(
        ctx,
        chapter=chapter,
        input_sha256=_INPUT_SHA256,
        system=_SYSTEM,
        user=_USERS[chapter],
        response_model=ChapterResponse,
        max_tokens=write.MAX_TOKENS,
        response_payload=response.model_dump(by_alias=True, mode='json'),
        acceptance=acceptance,
        state_payload={
            'version': 2,
            'mode': 'write',
            'outcome': 'passed' if passed else 'hard',
            'input_sha256': _INPUT_SHA256,
            'candidate_sha256': 'b' * 64,
            'accepted_turn_index': 1,
            'prior_attempts': [],
            'hard_rules': [] if passed else ['verbatim_missing'],
            'regressions': [],
        },
    )
    return acceptance


def _restore(ctx: RunContext, pool: Stage5SessionPool):
    restored = stage5_runs.restore_checkpoint(
        ctx,
        chapter=1,
        input_sha256=_INPUT_SHA256,
        system=_SYSTEM,
        user=_USERS[1],
        response_model=ChapterResponse,
        max_tokens=write.MAX_TOKENS,
    )
    assert restored is not None
    pool.restore_checkpoint_acceptance(
        stage5_runs.purpose_of(1),
        restored.acceptance,
        system=_SYSTEM,
        user=_USERS[1],
        max_tokens=write.MAX_TOKENS,
        recovery_policy=stage5_runs.RECOVERY_POLICY,
    )
    return restored


def test_live_feedback_resumes_only_its_chapter_and_pool_probes_once(tmp_path):
    factory = _Factory({
        (1, 1): ([_payload('第一版。'), _payload('第二版。')], 'stage5-ch1'),
        (2, 1): ([_payload('第二章。')], 'stage5-ch2'),
    })
    pool = Stage5SessionPool(
        _ctx(tmp_path), driver_factory=factory, max_turns=3, concurrency=2)
    pool.prepare_initial_sessions((1, 2))

    assert len(factory.created) == 2
    assert sum(driver.probe_calls for *_head, driver in factory.created) == 1
    assert _ask(pool, 1).segments[0].text == '第一版。'
    assert pool.queue_code_validation_feedback(
        [{'rule': 'verbatim_missing', 'count': 1}],
        purpose=stage5_runs.purpose_of(1),
    )
    assert _ask(pool, 1).segments[0].text == '第二版。'
    ch1, = factory.chapter(1)
    ch2, = factory.chapter(2)
    assert [kind for kind, _request in ch1.calls] == ['start', 'resume']
    assert ch2.calls == []

    pool.finish_chapter_session(1, outcome='delivered')
    pool.finish_all(failure=RuntimeError('本测试不启动第二章'))
    pool.close()
    assert ch1.close_calls == ch2.close_calls == 1


def test_shared_driver_is_refused_before_first_model_start(tmp_path):
    shared = _Driver([_payload('不会发出。')], session_id='shared-driver')
    pool = Stage5SessionPool(
        _ctx(tmp_path), driver_factory=lambda _chapter: shared)

    with pytest.raises(ProvenanceRefused, match='同一对象'):
        pool.prepare_initial_sessions((1, 2))

    assert shared.probe_calls == 1
    assert shared.calls == []
    assert shared.close_calls == 1
    assert agent_session_audit.read_events(tmp_path) == []


def test_provider_session_id_cannot_be_shared_across_chapters(tmp_path):
    factory = _Factory({
        (1, 1): ([_payload('第一章。')], 'provider-reused'),
        (2, 1): ([_payload('第二章。')], 'provider-reused'),
    })
    pool = Stage5SessionPool(
        _ctx(tmp_path), driver_factory=factory, concurrency=2)
    pool.prepare_initial_sessions((1, 2))
    _ask(pool, 1)

    with pytest.raises(ProvenanceRefused, match='provider session') as caught:
        _ask(pool, 2)

    pool.finish_all(failure=caught.value)
    pool.close()
    ended = [
        event for event in agent_session_audit.read_events(tmp_path)
        if event.kind == 'ended'
    ]
    assert len(ended) == 2
    assert all(event.process_state == 'failed' for event in ended)
    assert all(event.delivery_state == 'unknown' for event in ended)
    assert all(event.error_kind == 'session_isolation' for event in ended)


def test_dirty_checkpoint_starts_new_provider_session_with_cumulative_budget(
        tmp_path):
    first_ctx = _ctx(tmp_path)
    first_factory = _Factory({
        (1, 1): ([_payload('业务判据不合格的旧候选。')], 'old-provider'),
    })
    first_pool = Stage5SessionPool(
        first_ctx, driver_factory=first_factory, max_turns=3)
    first_pool.prepare_initial_sessions((1,))
    old_acceptance = _land_checkpoint(first_ctx, first_pool)
    first_pool.close()  # 模拟 checkpoint 后、终态前进程退出。

    rows = [{'rule': 'verbatim_missing', 'count': 1}]
    # 第一次恢复停在反馈排队后；第二次恢复必须幂等补同一条业务拒稿账。
    paused_ctx = _ctx(tmp_path, run_id=first_ctx.run_id)
    paused_factory = _Factory({
        (1, 1): ([_payload('不会调用。')], 'paused-provider'),
    })
    paused_pool = Stage5SessionPool(
        paused_ctx, driver_factory=paused_factory, max_turns=3)
    paused_pool.prepare_initial_sessions((1,))
    assert paused_factory.created == []
    _restore(paused_ctx, paused_pool)
    assert paused_factory.created == []
    assert paused_pool.queue_restored_code_validation_feedback(
        rows, purpose=stage5_runs.purpose_of(1))
    old_record, = [
        record for record in agent_session_audit.fold(
            agent_session_audit.read_events(tmp_path))
        if record.session_run_id == old_acceptance['session_run_id']
    ]
    assert old_record.end is not None
    assert (old_record.end.process_state, old_record.end.delivery_state,
            old_record.end.error_kind) == (
                'ok', 'blocked', 'checkpoint_business_rejected')
    paused_driver, = paused_factory.chapter(1)
    assert paused_driver.probe_calls == 1 and paused_driver.calls == []
    paused_pool.close()

    resumed_ctx = _ctx(tmp_path, run_id=first_ctx.run_id)
    resumed_factory = _Factory({
        (1, 1): ([_payload('恢复后的合格候选。')], 'new-provider'),
    })
    resumed_pool = Stage5SessionPool(
        resumed_ctx, driver_factory=resumed_factory, max_turns=3)
    resumed_pool.prepare_initial_sessions((1,))
    assert resumed_factory.created == []
    _restore(resumed_ctx, resumed_pool)
    assert resumed_pool.queue_restored_code_validation_feedback(
        rows, purpose=stage5_runs.purpose_of(1))
    old_ended = [
        event for event in agent_session_audit.read_events(tmp_path)
        if (event.kind == 'ended'
            and event.session_run_id == old_acceptance['session_run_id'])
    ]
    assert len(old_ended) == 1
    response = _ask(resumed_pool, 1)

    assert response.segments[0].text == '恢复后的合格候选。'
    resumed_driver, = resumed_factory.chapter(1)
    assert [kind for kind, _request in resumed_driver.calls] == ['start']
    feedback = json.loads(resumed_driver.calls[0][1].prompt)
    assert feedback['kind'] == 'checkpoint_recovery_task'
    assert feedback['original_task'] == _USERS[1]
    assert feedback['recovery']['kind'] \
        == 'checkpoint_recovery_validation_failed'
    assert '业务判据不合格的旧候选' \
        in feedback['recovery']['previous_candidate']
    current = resumed_pool.accepted_checkpoint(stage5_runs.purpose_of(1))
    assert old_acceptance['session_run_id'] != current['session_run_id']
    assert old_acceptance['provider_session_id'] != current['provider_session_id']
    assert current['accepted_turn_index'] == 2
    assert len(current['attempt_links']) == 2
    assert resumed_pool.turns_used == 2
    events = agent_session_audit.read_events(tmp_path)
    old_end_index = next(
        index for index, event in enumerate(events)
        if (event.kind == 'ended'
            and event.session_run_id == old_acceptance['session_run_id']))
    new_start_index = next(
        index for index, event in enumerate(events)
        if (event.kind == 'started'
            and event.session_run_id == current['session_run_id']))
    assert old_end_index < new_start_index

    rejected = [
        json.loads(line)
        for line in audit_path(tmp_path).read_text(encoding='utf-8').splitlines()
        if line.strip()
    ]
    rejected = [
        row for row in rejected
        if row.get('kind') == 'gate_decision'
        and row.get('decision') == 'rejected'
        and row.get('gate') == 'validator'
        and row.get('attempt_id') == old_acceptance['attempt_id']
    ]
    assert len(rejected) == 1 and rejected[0]['gate_decision_id']
    resumed_pool.finish_chapter_session(1, outcome='blocked')
    resumed_pool.finish_all(failure=ProvenanceRefused('测试只到候选终态'))
    resumed_pool.close()


def test_clean_checkpoint_closes_old_session_only_after_terminal_delivery(
        tmp_path):
    first_ctx = _ctx(tmp_path)
    first_factory = _Factory({
        (1, 1): ([_payload('已经通过业务校验的候选。')], 'clean-provider'),
    })
    first_pool = Stage5SessionPool(
        first_ctx, driver_factory=first_factory, max_turns=2)
    first_pool.prepare_initial_sessions((1,))
    acceptance = _land_checkpoint(first_ctx, first_pool, passed=True)
    first_pool.close()
    assert not any(
        event.kind == 'ended'
        and event.session_run_id == acceptance['session_run_id']
        for event in agent_session_audit.read_events(tmp_path)
    )

    resumed_ctx = _ctx(tmp_path, run_id=first_ctx.run_id)
    resumed_factory = _Factory({})
    resumed_pool = Stage5SessionPool(
        resumed_ctx, driver_factory=resumed_factory, max_turns=2)
    resumed_pool.prepare_initial_sessions((1,))
    restored = _restore(resumed_ctx, resumed_pool)
    assert restored.state_payload['outcome'] == 'passed'
    assert resumed_factory.created == []
    assert not any(
        event.kind == 'ended'
        and event.session_run_id == acceptance['session_run_id']
        for event in agent_session_audit.read_events(tmp_path)
    )

    resumed_pool.finish_chapter_session(1, outcome='delivered')
    resumed_pool.finish_chapter_session(1, outcome='delivered')
    resumed_pool.finish_all(failure=None)
    resumed_pool.close()
    ended = [
        event for event in agent_session_audit.read_events(tmp_path)
        if (event.kind == 'ended'
            and event.session_run_id == acceptance['session_run_id'])
    ]
    assert len(ended) == 1
    assert (ended[0].process_state, ended[0].delivery_state,
            ended[0].error_kind, ended[0].end_code_version) == (
                'ok', 'delivered', None, 'test-stage5-write-agent')


def test_recovery_refuses_provider_id_from_historical_logical_session(tmp_path):
    first_ctx = _ctx(tmp_path)
    first_factory = _Factory({
        (1, 1): ([_payload('旧候选。')], 'provider-must-not-resume'),
    })
    first_pool = Stage5SessionPool(
        first_ctx, driver_factory=first_factory, max_turns=2)
    first_pool.prepare_initial_sessions((1,))
    _land_checkpoint(first_ctx, first_pool)
    first_pool.close()

    resumed_ctx = _ctx(tmp_path, run_id=first_ctx.run_id)
    resumed_factory = _Factory({
        (1, 1): ([_payload('新候选。')], 'provider-must-not-resume'),
    })
    resumed_pool = Stage5SessionPool(
        resumed_ctx, driver_factory=resumed_factory, max_turns=2)
    resumed_pool.prepare_initial_sessions((1,))
    _restore(resumed_ctx, resumed_pool)
    assert resumed_pool.queue_restored_code_validation_feedback(
        [{'rule': 'verbatim_missing', 'count': 1}],
        purpose=stage5_runs.purpose_of(1),
    )

    with pytest.raises(ProvenanceRefused, match='偷接旧 session'):
        _ask(resumed_pool, 1)

    driver, = resumed_factory.chapter(1)
    assert [kind for kind, _request in driver.calls] == ['start']
    resumed_pool.finish_all(failure=ProvenanceRefused('provider 复用'))
    resumed_pool.close()


def test_exhausted_checkpoint_repairs_gate_without_probe_or_model_start(tmp_path):
    first_ctx = _ctx(tmp_path)
    first_factory = _Factory({
        (1, 1): ([_payload('预算已耗尽的坏候选。')], 'only-provider-turn'),
    })
    first_pool = Stage5SessionPool(
        first_ctx, driver_factory=first_factory, max_turns=1)
    first_pool.prepare_initial_sessions((1,))
    acceptance = _land_checkpoint(first_ctx, first_pool)
    first_pool.close()

    resumed_ctx = _ctx(tmp_path, run_id=first_ctx.run_id)
    resumed_factory = _Factory({
        (1, 1): ([], 'must-not-start'),
    })
    resumed_pool = Stage5SessionPool(
        resumed_ctx, driver_factory=resumed_factory, max_turns=1)
    resumed_pool.prepare_initial_sessions((1,))
    _restore(resumed_ctx, resumed_pool)

    assert resumed_pool.queue_restored_code_validation_feedback(
        [{'rule': 'verbatim_missing', 'count': 1}],
        purpose=stage5_runs.purpose_of(1),
    ) is False
    driver, = resumed_factory.chapter(1)
    assert driver.probe_calls == 0
    assert driver.calls == []
    rejected = [
        json.loads(line)
        for line in audit_path(tmp_path).read_text(encoding='utf-8').splitlines()
        if line.strip()
    ]
    rejected = [
        row for row in rejected
        if row.get('kind') == 'gate_decision'
        and row.get('decision') == 'rejected'
        and row.get('gate') == 'validator'
        and row.get('attempt_id') == acceptance['attempt_id']
    ]
    assert len(rejected) == 1
    old_ended = [
        event for event in agent_session_audit.read_events(tmp_path)
        if (event.kind == 'ended'
            and event.session_run_id == acceptance['session_run_id'])
    ]
    assert len(old_ended) == 1
    assert old_ended[0].error_kind == 'checkpoint_business_rejected'
    resumed_pool.finish_chapter_session(1, outcome='blocked')
    resumed_pool.finish_all(failure=ProvenanceRefused('预算终态'))
    resumed_pool.close()


def test_builder_routes_explicit_stage5_flags_and_forbids_single_driver(tmp_path):
    ctx = _ctx(tmp_path)
    factory = _Factory({
        (2, 1): ([_payload('第二章。')], 'stage5-ch2'),
    })

    agent = build_node_agent(
        ctx,
        stage5_driver_factory=factory,
        chapters='2',
        revise=True,
        max_turns=2,
    )

    assert agent.chapters == '2'
    assert agent.revise is True
    with pytest.raises(ValueError, match='不能让多个章节共享'):
        build_node_agent(ctx, driver=_Driver([], session_id='forbidden'))
