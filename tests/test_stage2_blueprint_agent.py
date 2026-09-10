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
        'NOVEL_STAGE2_BLUEPRINT_CONTRACT', 'extraction-ir-v3.1')
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
    for beat in value['beats']:
        beat['hinges'] = []
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

    def _turn(self) -> AgentTurn:
        response = self.responses.pop(0)
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
        return self._turn()

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
        return self._turn()

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


def test_commander_rework_only_revises_requested_chapter_and_preserves_others(tmp_path):
    from contracts.schema.api import Stage2NodeReviewRequest
    from novel import work_units
    from novel.node_agents import build_node_agent
    from novel.orchestrator import approvals
    from tests.test_stage2_review_decisions import _approve_all_review

    book = tmp_path / '蓝图定向返工_690'
    book.mkdir()
    _land_global(book)
    _approve_all_review(book)
    global_token = approvals._stage2_review_identity(book, 'stage2.global')[0]
    approvals.approve_stage2_review(tmp_path, '690', Stage2NodeReviewRequest(
        node='stage2.global', review_token=global_token, decision='accept',
        actor='test-commander', reason='全局必需事实已核验'))
    good = _response_blueprints()
    initial = _ctx(tmp_path, book)
    first = Stage2BlueprintAgent(initial, driver_factory=_Factory(
        book, {(chapter, 1): [good[chapter]] for chapter in (1, 2, 3)})).run()
    token = approvals._stage2_review_identity(book, initial.node)[0]
    approvals.approve_stage2_review(tmp_path, initial.sid, Stage2NodeReviewRequest(
        node=initial.node, review_token=token, decision='rework', owner=initial.node,
        actor='test-commander', reason='仅核对第 2 章因果依据', chapter_ordinals=[2]))
    repaired = _ctx(tmp_path, book)
    factory = _Factory(book, {(2, 1): [good[2]]}, session_namespace='commander-')
    result = build_node_agent(repaired, driver_factory=factory, review_token=token).run()
    assert result.passed
    assert [row[2] for row in factory.created] == [2]
    assert '仅核对第 2 章因果依据' in factory.created[0][-1].calls[0][1].prompt
    before = {row.chapter_ordinal: row for row in first.artifact.blueprints}
    after = {row.chapter_ordinal: row for row in result.artifact.blueprints}
    assert before[1] == after[1] and before[3] == after[3]
    assert [work_units.budget_for(repaired, scope=f'chapter:{chapter}').used
            for chapter in (1, 2, 3)] == [1, 2, 1]
    assert repaired.cache == CachePolicy.default()
    assert approvals.pending_stage2_rework(tmp_path, initial.sid, initial.node) is None


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
    assert feedback['kind'] == 'code_validation_failed'
    assert feedback['failures']
    assert set(feedback['failures'][0]) == {
        'rule', 'item', 'requirement', 'count', 'chapter'}
    assert all(
        forbidden not in json.dumps(feedback['failures'], ensure_ascii=False)
        for forbidden in ('findings', 'detail', 'evidence')
    )

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
    assert all(not beat['hinges'] for beat in first_blueprint['beats'])
    landed = ArtifactStore(ctx).read(
        ChapterBlueprints, extract.BLUEPRINT_REL)
    final_ch1 = next(
        row for row in landed.blueprints if row.chapter_ordinal == 1)
    assert any(beat.hinges for beat in final_ch1.beats)


def test_crash_restore_opens_one_new_subject_and_never_builds_restored_drivers(
        tmp_path):
    book = tmp_path / '蓝图恢复新会话_603'
    book.mkdir()
    _land_global(book)
    first_ctx = _ctx(tmp_path, book)
    good = _response_blueprints()
    first_factory = _Factory(book, {
        (1, 1): [_bad_chapter(1)],
        (2, 1): [good[2]],
        (3, 1): [good[3]],
    })
    first_pool = BlueprintSessionPool(
        first_ctx, driver_factory=first_factory, max_turns=3)
    first_pool.prepare_initial_sessions()
    batch = extract.request_blueprints(first_ctx, first_pool)
    evaluation = extract.evaluate_blueprint_candidate(
        first_ctx, first_pool, batch)
    assert not evaluation.passed
    checkpoint_path = paths.stage2_blueprint_checkpoint_path(
        book, first_ctx.run_id, 'blueprint', 1)
    checkpoint_before = checkpoint_path.read_bytes()
    # 不写终态来模拟修订前进程中断；只释放本进程 driver。
    first_pool.close()

    resumed_ctx = _ctx(
        tmp_path, book, run_id=first_ctx.run_id)
    resumed_factory = _Factory(
        book, {(1, 1): [good[1]]}, session_namespace='restored-')
    result = Stage2BlueprintAgent(
        resumed_ctx, driver_factory=resumed_factory, max_turns=3).run()

    # 三章 checkpoint 的历史 turns 与第一章恢复新 session 的新 turn 一起累计，
    # crash 不会把逐章预算/计数清零。
    assert result.turns == 4
    assert [(phase, chapter) for phase, _purpose, chapter, _n, _driver
            in resumed_factory.created] == [('blueprint', 1)]
    replacement, = resumed_factory.chapter(1)
    assert replacement.probe_calls == 1
    assert [kind for kind, _request in replacement.calls] == ['start']
    request = replacement.calls[0][1]
    assert request.system is not None
    feedback = json.loads(request.prompt)
    assert feedback['kind'] == 'checkpoint_recovery_task'
    assert '<1> 春风吹过老宅的院墙。' in feedback['original_task']
    assert feedback['recovery']['kind'] \
        == 'checkpoint_recovery_validation_failed'
    assert 'previous_candidate' in feedback['recovery']
    assert set(feedback['recovery']['failures'][0]) == {
        'rule', 'item', 'requirement', 'count', 'chapter'}
    assert checkpoint_path.read_bytes() == checkpoint_before
    landed = ArtifactStore(resumed_ctx).read(
        ChapterBlueprints, extract.BLUEPRINT_REL)
    assert landed.provenance.usage is not None
    assert landed.provenance.usage.calls_made == 4
    records = _records(tmp_path)
    assert len(records) == 4
    old = [
        record for record in records
        if (record.start.chapter == 1
            and record.end is not None
            and record.end.error_kind == 'checkpoint_business_rejected')
    ]
    assert len(old) == 1
    assert old[0].delivery_state == 'blocked'
    assert old[0].end is not None
    newer = [
        record for record in records
        if record.start.chapter == 1 and record.session_run_id != old[0].session_run_id
    ]
    assert len(newer) == 1 and newer[0].delivery_state == 'delivered'
    original_request = AgentRequest(
        prompt=feedback['original_task'], system=request.system,
        output_schema=request.output_schema,
        delivery_protocol=request.delivery_protocol,
        execution_policy=request.execution_policy, cwd=request.cwd,
    )
    assert newer[0].turns
    assert newer[0].start.prompt_sha256 == hashlib.sha256(
        request.stdin_bytes()).hexdigest()
    assert newer[0].start.prompt_sha256 == newer[0].turns[0].prompt_sha256
    assert newer[0].start.prompt_sha256 != hashlib.sha256(
        original_request.stdin_bytes()).hexdigest()


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


def test_exhausted_restored_budget_adds_no_probe_or_provider_start(tmp_path):
    book = tmp_path / '蓝图恢复预算耗尽_608'
    book.mkdir()
    _land_global(book)
    good = _response_blueprints()
    first_ctx = _ctx(tmp_path, book)
    # 第一轮给错章，触发同一 provider session 的 scope validator resume；第二轮
    # wire/schema/scope 合格但仍有整书业务 HARD，因此 checkpoint 已累计两轮。
    first_factory = _Factory(book, {
        (1, 1): [good[2], _bad_chapter(1)],
        (2, 1): [good[2]],
        (3, 1): [good[3]],
    })
    first_pool = BlueprintSessionPool(
        first_ctx, driver_factory=first_factory, max_turns=2)
    first_pool.prepare_initial_sessions()
    batch = extract.request_blueprints(first_ctx, first_pool)
    assert not extract.evaluate_blueprint_candidate(
        first_ctx, first_pool, batch).passed
    checkpoint = json.loads(paths.stage2_blueprint_checkpoint_path(
        book, first_ctx.run_id, 'blueprint', 1).read_text(encoding='utf-8'))
    assert checkpoint['accepted_turn_index'] == 2
    old_session = checkpoint['session_run_id']
    first_pool.close()

    resumed_ctx = _ctx(tmp_path, book, run_id=first_ctx.run_id)
    resumed_factory = _Factory(book, {(1, 1): []})
    with pytest.raises(GateBlocked):
        Stage2BlueprintAgent(
            resumed_ctx, driver_factory=resumed_factory, max_turns=2).run()

    replacement, = resumed_factory.chapter(1)
    assert replacement.probe_calls == 0
    assert replacement.calls == []
    assert replacement.close_calls == 1
    assert not any(
        event.kind == 'started' and event.session_run_id != old_session
        and event.chapter == 1
        for event in agent_session_audit.read_events(tmp_path)
    )
    old_record, = [
        record for record in _records(tmp_path)
        if record.session_run_id == old_session
    ]
    assert old_record.end is not None
    assert (old_record.end.process_state, old_record.end.delivery_state,
            old_record.end.error_kind) == (
                'ok', 'blocked', 'checkpoint_business_rejected')


def test_turn_limit_writes_one_invalid_and_ends_every_live_session(tmp_path):
    book = tmp_path / '蓝图终态拒稿_604'
    book.mkdir()
    _land_global(book)
    good = _response_blueprints()
    factory = _Factory(book, {
        (1, 1): [_bad_chapter(1), _bad_chapter(1)],
        (2, 1): [good[2]],
        (3, 1): [good[3]],
    })
    ctx = _ctx(tmp_path, book)

    with pytest.raises(GateBlocked):
        Stage2BlueprintAgent(
            ctx, driver_factory=factory, max_turns=2).run()

    store = ArtifactStore(ctx)
    assert store.exists(extract._INVALID)
    assert not store.exists(extract.BLUEPRINT_REL)
    invalid = store.read(ChapterBlueprints, extract._INVALID)
    assert invalid.provenance.usage is not None
    assert invalid.provenance.usage.calls_made == 4
    assert not list((book / 'stage2/_rounds').glob(
        'chapter_blueprints.INVALID.r*.json'))
    records = _records(tmp_path)
    assert len(records) == 3
    assert all(record.process_state == 'ok' for record in records)
    assert all(record.delivery_state == 'blocked' for record in records)
    assert all(driver.close_calls == 1 for *_head, driver in factory.created)


def test_static_book_hard_is_broadcast_only_to_detail_targets():
    row = SimpleNamespace(
        rule='R-STATIC', item='整书规则', requirement='满足静态规则',
        chapters=(), findings=(object(), object()))
    evaluation = SimpleNamespace(
        purposes=(
            ('stage2.blueprint:boundaries:ch1', 1),
            ('stage2.blueprint:boundaries:ch2', 2),
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

    assert shared.probe_calls == 1
    assert shared.calls == []
    assert shared.close_calls == 1
    assert not (book / extract.BLUEPRINT_REL).exists()
    assert agent_session_audit.read_events(tmp_path) == []


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

    assert len({id(driver) for *_head, driver in factory.created}) == 3
    assert sum(driver.probe_calls for *_head, driver in factory.created) == 1
    assert not (book / extract.BLUEPRINT_REL).exists()
    # 冲突 turn 留作协议违规证据但不取得 ownership，因此整份审计账仍必须可折叠。
    records = _records(tmp_path)
    assert len(records) == 3
    assert sum(bool(record.provider_session_ids) for record in records) == 1
    ended = [
        event for event in agent_session_audit.read_events(tmp_path)
        if event.kind == 'ended'
    ]
    assert len(ended) == 3
    assert all(event.process_state == 'failed' for event in ended)
    assert all(event.delivery_state == 'unknown' for event in ended)
    assert all(event.error_kind == 'session_isolation' for event in ended)
    assert all(driver.close_calls == 1 for *_head, driver in factory.created)
