#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 blueprint 逐章 executor session pool 的零成本纵切。"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.invariants import STAGE2_BLUEPRINT_MAX_CONCURRENCY
from contracts.schema.orchestration import AgentProfile
from contracts.schema.stage1 import MainText
from contracts.schema.stage2 import ChapterBlueprints
from novel import agent_session_audit, paths, prov
from novel.agents import AgentDriver, AgentProbe, AgentRequest, AgentTurn, AgentUsage
from novel.errors import GateBlocked, ProvenanceRefused
from novel.llm.cache import CachePolicy
from novel.llm.client import LlmFailed
from novel.llm.ledger import CostLedger, ledger_path
from novel.node_agents import BlueprintSessionPool, Stage2BlueprintAgent
from novel.run import RunContext
from novel.stages.stage2 import extract
from novel.store import ArtifactStore
from tests.test_stage2 import main_text
from tests.test_stage2_generation import (
    _accept_fixture_global, _client, _ctx as _legacy_ctx, _ok,
    _response_blueprints, _response_global,
)


@pytest.fixture(autouse=True)
def _stable_contract_and_code(monkeypatch):
    monkeypatch.setenv(
        'NOVEL_STAGE2_BLUEPRINT_CONTRACT', 'plot-blueprint-v1')
    monkeypatch.setattr(
        prov, 'code_version', lambda: 'test-stage2-blueprint-node-agent')


def _profile() -> AgentProfile:
    return AgentProfile(
        driver='codex', model='gpt-5.6-sol', reasoning_effort='high',
        execution_mode='standard')


def _land_global(book: Path) -> None:
    ctx = _legacy_ctx(book, 'stage2.global')
    ArtifactStore(ctx).write(
        extract.MAIN_TEXT, MainText.model_validate(main_text()))
    client, _transport = _client(
        ctx, lambda _request, _index: _ok(_response_global()))
    extract.run_global(ctx, client)
    _accept_fixture_global(ctx)


@pytest.fixture(autouse=True)
def _workflow_epoch(tmp_path):
    from novel.work_units import initialize_workflow
    initialize_workflow(tmp_path)


def _ctx(base: Path, book: Path, *, run_id: str | None = None) -> RunContext:
    ledger = (
        CostLedger(path=ledger_path(base), run_id=run_id)
        if run_id is not None else CostLedger(path=ledger_path(base))
    )
    return RunContext(
        novel_dir=book, node='stage2.blueprint', paid=True,
        code=prov.code_version(), cache=CachePolicy.default(), ledger=ledger,
        executor_profile=_profile(), run_id=run_id or '',
        observer=lambda _event: None,
    )


def _bad_chapter(chapter: int) -> dict:
    value = copy.deepcopy(_response_blueprints()[chapter])
    value['beats'][0]['participants'] = [{'char_id': 'char_999999'}]
    return value


def test_blueprint_agent_concurrency_is_clamped_to_provider_stability_limit(
        tmp_path):
    """全局 32 不能穿透到 Claude 逐章池；显式更低值仍被保留。"""
    book = tmp_path / '蓝图Agent并发护栏_600'
    book.mkdir()
    ctx = _ctx(tmp_path, book)

    high = BlueprintSessionPool(
        ctx, driver_factory=lambda *_args: None, concurrency=32)
    low = BlueprintSessionPool(
        ctx, driver_factory=lambda *_args: None, concurrency=2)
    try:
        assert high.concurrency == STAGE2_BLUEPRINT_MAX_CONCURRENCY == 8
        assert low.concurrency == 2
    finally:
        high.close()
        low.close()


class _Driver(AgentDriver):
    driver = 'codex'
    provider = 'openai'

    def __init__(
        self, *, chapter: int, responses: list[dict], session_id: str,
        book: Path,
    ) -> None:
        self.chapter = chapter
        self.responses = [copy.deepcopy(row) for row in responses]
        self.session_id = session_id
        self.book = book
        self.calls: list[tuple[str, AgentRequest]] = []
        self.probe_calls = 0
        self.close_calls = 0

    def probe(self) -> AgentProbe:
        self.probe_calls += 1
        return AgentProbe(
            driver=self.driver, provider=self.provider,
            executable='fake-codex', available=True, version='0.blueprint-test')

    def _turn(self, request: AgentRequest) -> AgentTurn:
        response = self.responses.pop(0)
        if callable(response):
            response = response(request)
        return AgentTurn(
            session_id=self.session_id,
            content=json.dumps(
                {'blueprints': [response]}, ensure_ascii=False),
            model=None, actual_reasoning_effort='high',
            usage=AgentUsage(
                prompt_tokens=100, completion_tokens=20, reasoning_tokens=10,
                total_tokens=120),
            turn_count=1,
        )

    def start(self, profile: AgentProfile, request: AgentRequest) -> AgentTurn:
        assert profile == _profile()
        self.calls.append(('start', request))
        return self._turn(request)

    def resume(
        self, profile: AgentProfile, session_id: str, request: AgentRequest,
    ) -> AgentTurn:
        assert profile == _profile()
        assert session_id == self.session_id
        # 联合 HARD 的中间轮只在内存；修订调用前不能有正式或
        # INVALID 蓝图，也不能伪造一轮归档。
        assert not (self.book / extract.BLUEPRINT_REL).exists()
        assert not (self.book / extract._INVALID).exists()
        assert not list((self.book / 'stage2/_rounds').glob(
            'chapter_blueprints*.json'))
        self.calls.append(('resume', request))
        return self._turn(request)

    def cancel(self, session_id: str | None = None) -> bool:
        return False

    def close(self) -> None:
        self.close_calls += 1


class _Factory:
    def __init__(
        self, book: Path, plans: dict[tuple[int, int], list[dict]], *,
        shared_provider_session: bool = False,
        session_namespace: str = '',
    ) -> None:
        self.book = book
        self.plans = plans
        self.shared_provider_session = shared_provider_session
        self.session_namespace = session_namespace
        self.created: list[tuple[str, str, int, int, _Driver]] = []
        self._counts: dict[int, int] = {}
        self._lock = threading.Lock()

    def __call__(self, phase: str, purpose: str, chapter: int) -> AgentDriver:
        with self._lock:
            ordinal = self._counts.get(chapter, 0) + 1
            self._counts[chapter] = ordinal
            plan = self.plans[(chapter, ordinal)]
            session_id = (
                'provider-session-reused'
                if self.shared_provider_session
                else (
                    f'{self.session_namespace}{phase}-ch{chapter}-'
                    f'session-{ordinal}')
            )
            driver = _Driver(
                chapter=chapter, responses=plan, session_id=session_id,
                book=self.book)
            self.created.append((phase, purpose, chapter, ordinal, driver))
            return driver

    def chapter(self, chapter: int) -> list[_Driver]:
        return [
            driver for _phase, _purpose, value, _ordinal, driver in self.created
            if value == chapter
        ]


def _records(base: Path):
    return agent_session_audit.fold(agent_session_audit.read_events(base))


def test_live_hard_resumes_only_target_chapter_then_delivers(tmp_path):
    book = tmp_path / '蓝图逐章会话_601'
    book.mkdir()
    _land_global(book)
    good = _response_blueprints()
    factory = _Factory(book, {
        (1, 1): [_bad_chapter(1), good[1]],
        (2, 1): [good[2]],
        (3, 1): [good[3]],
    })
    ctx = _ctx(tmp_path, book)

    result = Stage2BlueprintAgent(
        ctx, driver_factory=factory, max_turns=3).run()

    assert result.turns == 4
    assert len(factory.created) == len(main_text())
    assert sum(driver.probe_calls for *_head, driver in factory.created) == 1
    ch1, = factory.chapter(1)
    assert [kind for kind, _request in ch1.calls] == ['start', 'resume']
    assert all(len(factory.chapter(chapter)[0].calls) == 1 for chapter in (2, 3))
    first, resumed = (request for _kind, request in ch1.calls)
    assert first.system is not None and resumed.system is None
    feedback = json.loads(resumed.prompt)
    assert feedback['kind'] == 'response_validation_failed'
    assert feedback['gate'] == 'validator'
    assert '请求未提供的人物' in json.dumps(feedback, ensure_ascii=False)

    store = ArtifactStore(ctx)
    landed = store.read(ChapterBlueprints, extract.BLUEPRINT_REL)
    assert landed == result.artifact
    assert landed.provenance.usage is not None
    assert landed.provenance.usage.calls_made == 4
    assert not store.exists(extract._INVALID)
    assert all(driver.close_calls == 1 for *_head, driver in factory.created)
    records = _records(tmp_path)
    assert len(records) == 3
    assert {record.start.chapter for record in records} == {1, 2, 3}
    assert all(record.process_state == 'ok' for record in records)
    assert all(record.delivery_state == 'delivered' for record in records)


def test_initial_checkpoint_is_immutable_across_live_revision(tmp_path):
    book = tmp_path / '蓝图检查点不可变_602'
    book.mkdir()
    _land_global(book)
    ctx = _ctx(tmp_path, book)
    good = _response_blueprints()
    factory = _Factory(book, {
        (1, 1): [_bad_chapter(1), good[1]],
        (2, 1): [good[2]],
        (3, 1): [good[3]],
    })

    Stage2BlueprintAgent(
        ctx, driver_factory=factory, max_turns=3).run()

    checkpoint = json.loads(paths.stage2_blueprint_checkpoint_path(
        book, ctx.run_id, 'blueprint', 1).read_text(encoding='utf-8'))
    first_blueprint = checkpoint['response_payload']['blueprints'][0]
    from contracts.schema.plot import PlotChapterResponse
    assert first_blueprint == PlotChapterResponse.model_validate(good[1]).model_dump(mode='json')
    assert checkpoint['accepted_turn_index'] == 2
    landed = ArtifactStore(ctx).read(ChapterBlueprints, extract.BLUEPRINT_REL)
    assert [actor.char_id for actor in landed.blueprints[0].beats[0].participants] == [
        actor['char_id'] for actor in good[1]['beats'][0]['participants']]


def test_crash_restore_opens_one_new_subject_and_never_builds_restored_drivers(
        tmp_path, monkeypatch):
    """两章响应已购并验证后中断，重启只购买尚未完成的第三章。"""
    from novel import work_units
    book = tmp_path / '蓝图恢复新会话_603'
    book.mkdir()
    _land_global(book)
    first_ctx = _ctx(tmp_path, book)
    good = _response_blueprints()
    first_factory = _Factory(book, {(ch, 1): [good[ch]] for ch in (1, 2, 3)})
    first_pool = BlueprintSessionPool(first_ctx, driver_factory=first_factory, max_turns=3)
    first_pool.prepare_initial_sessions()
    original = extract._run_chapter_requests
    def interrupted(ctx, pool, requests, **kwargs):
        result = original(ctx, pool, requests, **kwargs)
        if requests[0][0] == 2:
            raise RuntimeError('第二章检查点封存后中断')
        return result
    with monkeypatch.context() as patch:
        patch.setattr(extract, '_run_chapter_requests', interrupted)
        with pytest.raises(RuntimeError, match='封存后中断'):
            extract.request_blueprints(first_ctx, first_pool)
    saved = {ch: paths.stage2_blueprint_checkpoint_path(
        book, first_ctx.run_id, 'blueprint', ch).read_bytes() for ch in (1, 2)}
    first_pool.close()
    resumed_ctx = _ctx(tmp_path, book, run_id=first_ctx.run_id)
    resumed_factory = _Factory(book, {(3, 1): [good[3]]}, session_namespace='restored-')
    result = Stage2BlueprintAgent(resumed_ctx, driver_factory=resumed_factory, max_turns=3).run()
    assert result.turns == 3
    assert [row[2] for row in resumed_factory.created] == [3]
    assert [kind for kind, _ in resumed_factory.chapter(3)[0].calls] == ['start']
    assert all(paths.stage2_blueprint_checkpoint_path(
        book, first_ctx.run_id, 'blueprint', ch).read_bytes() == raw for ch, raw in saved.items())
    assert result.artifact.provenance.usage.calls_made == 3
    assert [work_units.budget_for(resumed_ctx, scope=f'chapter:{ch}').used for ch in (1, 2, 3)] == [1, 1, 1]
    records = _records(tmp_path)
    assert len(records) == 3
    assert all(row.delivery_state == 'delivered' for row in records)


def test_clean_checkpoint_restore_is_zero_driver_and_closes_old_sessions(
        tmp_path):
    book = tmp_path / '蓝图干净恢复_607'
    book.mkdir()
    _land_global(book)
    good = _response_blueprints()
    first_ctx = _ctx(tmp_path, book)
    first_factory = _Factory(book, {
        (1, 1): [good[1]],
        (2, 1): [good[2]],
        (3, 1): [good[3]],
    })
    first_pool = BlueprintSessionPool(
        first_ctx, driver_factory=first_factory, max_turns=3)
    first_pool.prepare_initial_sessions()
    batch = extract.request_blueprints(first_ctx, first_pool)
    assert extract.evaluate_blueprint_candidate(
        first_ctx, first_pool, batch).passed
    first_pool.close()
    assert not any(
        event.kind == 'ended'
        for event in agent_session_audit.read_events(tmp_path)
    )

    resumed_ctx = _ctx(tmp_path, book, run_id=first_ctx.run_id)
    resumed_factory = _Factory(book, {})
    result = Stage2BlueprintAgent(
        resumed_ctx, driver_factory=resumed_factory, max_turns=3).run()

    assert result.turns == 3
    assert resumed_factory.created == []
    assert result.artifact == ArtifactStore(resumed_ctx).read(
        ChapterBlueprints, extract.BLUEPRINT_REL)
    records = _records(tmp_path)
    assert len(records) == 3
    assert all(record.end is not None for record in records)
    assert all(record.process_state == 'ok' for record in records)
    assert all(record.delivery_state == 'delivered' for record in records)
    assert all(
        record.end is not None
        and record.end.end_code_version == 'test-stage2-blueprint-node-agent'
        for record in records)


def test_exhausted_restored_budget_adds_no_provider_start(tmp_path):
    """第四次坏响应耗尽持久额度；执行 epoch 与新 driver 都不能重置额度。"""
    from novel import work_units
    book = tmp_path / '蓝图恢复预算耗尽_608'
    book.mkdir()
    _land_global(book)
    good = _response_blueprints()
    first_ctx = _ctx(tmp_path, book)
    first_factory = _Factory(book, {(1, 1): [good[1]], (2, 1): [good[2]],
                                    (3, 1): [_bad_chapter(3)] * 4})
    with pytest.raises(LlmFailed, match="已用尽 4 轮"):
        Stage2BlueprintAgent(first_ctx, driver_factory=first_factory, max_turns=4).run()
    assert work_units.budget_for(first_ctx, scope='chapter:3').used == 4
    first_calls = len(agent_session_audit.read_events(tmp_path))
    resumed_ctx = _ctx(tmp_path, book, run_id=first_ctx.run_id)
    resumed_factory = _Factory(book, {(3, 1): []}, session_namespace='exhausted-')
    with pytest.raises((GateBlocked, ProvenanceRefused, LlmFailed)):
        Stage2BlueprintAgent(resumed_ctx, driver_factory=resumed_factory, max_turns=4).run()
    assert all(not driver.calls for *_head, driver in resumed_factory.created)
    assert work_units.budget_for(resumed_ctx, scope='chapter:3').used == 4
    recovered_events = agent_session_audit.read_events(tmp_path)[first_calls:]
    assert all(row.kind != 'turn' for row in recovered_events)
    assert all(not row.provider_session_ids for row in _records(tmp_path)
               if row.start.chapter == 3
               and row.session_run_id not in {event.session_run_id for event in agent_session_audit.read_events(tmp_path)[:first_calls]})


def test_turn_limit_writes_one_invalid_and_ends_every_live_session(tmp_path, monkeypatch):
    """已购合法章节在聚合中缺失，真实齐章 HARD 只能终态写一次 INVALID。"""
    book = tmp_path / '蓝图终态拒稿_604'
    book.mkdir()
    _land_global(book)
    good = _response_blueprints()
    factory = _Factory(book, {(ch, 1): [good[ch]] for ch in (1, 2, 3)})
    ctx = _ctx(tmp_path, book)
    original = extract.request_blueprints
    def lose_aggregate_chapter(*args, **kwargs):
        batch = original(*args, **kwargs)
        # 注入聚合故障，既不篡改已购 checkpoint，也不伪造通过/失败判据。
        batch['raw_blueprints'] = batch['raw_blueprints'][:2]
        return batch
    monkeypatch.setattr(extract, 'request_blueprints', lose_aggregate_chapter)
    with pytest.raises(GateBlocked):
        Stage2BlueprintAgent(ctx, driver_factory=factory, max_turns=1).run()
    store = ArtifactStore(ctx)
    assert store.exists(extract._INVALID)
    assert not store.exists(extract.BLUEPRINT_REL)
    invalid = store.read(ChapterBlueprints, extract._INVALID)
    assert invalid.provenance.usage.calls_made == 3
    assert not list((book / 'stage2/_rounds').glob('chapter_blueprints.INVALID.r*.json'))
    records = _records(tmp_path)
    assert len(records) == 3
    assert all(row.process_state == 'ok' and row.delivery_state == 'blocked' for row in records)
    assert all(driver.close_calls == 1 for *_head, driver in factory.created)


def test_static_book_hard_is_broadcast_only_to_detail_targets():
    row = SimpleNamespace(
        rule='R-STATIC', item='整书规则', requirement='满足静态规则',
        chapters=(), findings=(object(), object()))
    evaluation = SimpleNamespace(
        purposes=(
            ('stage2.blueprint:ch1', 1),
            ('stage2.blueprint:ch2', 2),
        ),
        feedback=(row,),
    )

    routed = Stage2BlueprintAgent._safe_feedback(evaluation)

    assert set(routed) == {1, 2}
    assert all(len(rows) == 1 for rows in routed.values())
    assert routed[1][0] == {
        'rule': 'R-STATIC', 'item': '整书规则',
        'requirement': '满足静态规则', 'count': 2, 'chapter': 1,
    }
    assert all(set(rows[0]) == {
        'rule', 'item', 'requirement', 'count', 'chapter'}
        for rows in routed.values())


def test_shared_driver_object_is_refused_before_first_model_start(tmp_path):
    book = tmp_path / '蓝图重复driver_605'
    book.mkdir()
    _land_global(book)
    shared = _Driver(
        chapter=1, responses=[_response_blueprints()[1]],
        session_id='shared-driver', book=book)

    with pytest.raises(ProvenanceRefused, match='同一对象'):
        Stage2BlueprintAgent(
            _ctx(tmp_path, book),
            driver_factory=lambda _phase, _purpose, _chapter: shared,
        ).run()

    # 冲突在后续会话注册时暴露；共享对象至多服务过首个合法章，且后继章
    # 不再取得同一 driver，正式产物绝不落盘。
    assert shared.probe_calls == 1
    prompt_head = json.loads(shared.calls[0][1].prompt.split('\n', 1)[0])
    assert prompt_head['chapter_ordinal'] == 1
    assert shared.close_calls >= 1
    assert not (book / extract.BLUEPRINT_REL).exists()


def test_provider_session_id_cannot_be_reused_across_chapters(tmp_path):
    book = tmp_path / '蓝图重复session_606'
    book.mkdir()
    _land_global(book)
    good = _response_blueprints()
    factory = _Factory(
        book,
        {(1, 1): [good[1]], (2, 1): [good[2]], (3, 1): [good[3]]},
        shared_provider_session=True,
    )

    with pytest.raises(
            ProvenanceRefused,
            match='provider session.*偷接旧 session|跨章/跨相位'):
        Stage2BlueprintAgent(
            _ctx(tmp_path, book), driver_factory=factory).run()

    assert len({id(driver) for *_head, driver in factory.created}) == 2
    assert not (book / extract.BLUEPRINT_REL).exists()
    # 冲突 turn 留作协议违规证据但不取得 ownership，因此整份审计账仍必须可折叠。
    records = _records(tmp_path)
    assert len(records) == 2
    assert sum(bool(record.provider_session_ids) for record in records) == 1
    ended = [
        event for event in agent_session_audit.read_events(tmp_path)
        if event.kind == 'ended'
    ]
    assert len(ended) == 2
    assert all(event.process_state == 'failed' for event in ended)
    assert all(event.delivery_state == 'unknown' for event in ended)
    assert all(event.error_kind == 'session_isolation' for event in ended)
    assert all(driver.close_calls == 1 for *_head, driver in factory.created)
