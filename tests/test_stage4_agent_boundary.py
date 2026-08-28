#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 design/plan executor 候选边界的零 wire 集成测试。"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from contracts.artifacts import (
    STAGE4_BLIND_CANDIDATE, STAGE4_EVIDENCE_BINDING,
    STAGE4_PLAN_CHAPTERS, STAGE4_WRITER_CONTEXT,
)
from contracts.schema.orchestration import AgentProfile
from contracts.schema.stage4 import BlindCandidate, EvidenceBinding, PlanChapters
from novel import agent_session_audit, paths, prov
from novel.agents import (
    AgentDriver, AgentProbe, AgentRequest, AgentTurn, AgentUsage,
)
from novel.agents.session_client import SessionAgentClient
from novel.errors import GateBlocked, ProvenanceRefused
from novel.llm import stage4_runs
from novel.llm.cache import CachePolicy
from novel.llm.ledger import CostLedger, ledger_path
from novel.run import RunContext
from novel.node_agents import Stage4DesignSessionPool
from novel.stages.stage4 import plan
from novel.stages.stage4.blind import build_blind_projection
from novel.store import ArtifactStore
from tests.test_stage4_v3_integration import (
    _book, _bound_b_response, _infeasible_b_response, _seed_current_stage2,
    _split_a_responder, _ctx as _legacy_ctx, _retain_a_response,
    _seal_a_validator_rejection,
)


def _profile() -> AgentProfile:
    return AgentProfile(
        driver='codex', model='gpt-5.6-sol',
        reasoning_effort='high', execution_mode='standard')


class _OneTurnDriver(AgentDriver):
    driver = 'codex'
    provider = 'openai'

    def __init__(self, payload: dict, *, session_id: str) -> None:
        self.content = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        self.session_id = session_id
        self.calls = 0
        self.requests: list[AgentRequest] = []

    def probe(self) -> AgentProbe:
        return AgentProbe(
            driver='codex', provider='openai', executable='fake-codex',
            available=True, version='0.test')

    def _turn(self) -> AgentTurn:
        self.calls += 1
        return AgentTurn(
            session_id=self.session_id,
            content=self.content,
            model='gpt-5.6-sol',
            actual_reasoning_effort='high',
            usage=AgentUsage(prompt_tokens=20, completion_tokens=5),
            turn_count=1,
        )

    def start(self, profile: AgentProfile, request: AgentRequest) -> AgentTurn:
        self.requests.append(request)
        return self._turn()

    def resume(
            self, profile: AgentProfile, session_id: str,
            request: AgentRequest,
    ) -> AgentTurn:
        assert session_id == self.session_id
        self.requests.append(request)
        return self._turn()

    def cancel(self, session_id: str | None = None) -> bool:
        return False

    def close(self) -> None:
        return None


class _NoWireDriver(_OneTurnDriver):
    def __init__(self) -> None:
        super().__init__({}, session_id='must-not-start')

    def probe(self) -> AgentProbe:
        raise AssertionError('checkpoint/committed 恢复不得探测或调用 provider')

    def start(self, profile: AgentProfile, request: AgentRequest) -> AgentTurn:
        raise AssertionError('checkpoint/committed 恢复不得 start')

    def resume(
            self, profile: AgentProfile, session_id: str,
            request: AgentRequest,
    ) -> AgentTurn:
        raise AssertionError('checkpoint/committed 恢复不得 resume')


class _RequestDriver(_OneTurnDriver):
    """按每个 unit 的真实 AgentRequest 动态构造 fake structured output。"""

    def __init__(
            self, responder: Callable[[AgentRequest], dict[str, Any]], *,
            session_id: str,
    ) -> None:
        super().__init__({}, session_id=session_id)
        self._responder = responder

    def _answer(self, request: AgentRequest) -> AgentTurn:
        self.content = json.dumps(
            self._responder(request), ensure_ascii=False, sort_keys=True,
            separators=(',', ':'))
        self.requests.append(request)
        return self._turn()

    def start(self, profile: AgentProfile, request: AgentRequest) -> AgentTurn:
        return self._answer(request)

    def resume(
            self, profile: AgentProfile, session_id: str,
            request: AgentRequest,
    ) -> AgentTurn:
        assert session_id == self.session_id
        return self._answer(request)


def _ctx(
        book: Path, node: str, *, run_id: str | None = None,
        code: str | None = None,
) -> RunContext:
    base = book.parent
    ledger = (CostLedger(path=ledger_path(base)) if run_id is None else
              CostLedger(path=ledger_path(base), run_id=run_id))
    return RunContext(
        novel_dir=book,
        node=node,
        paid=True,
        code=code or prov.code_version(),
        cache=CachePolicy.default(),
        ledger=ledger,
        executor_profile=_profile(),
        run_id=run_id or '',
    )


def _client(
        ctx: RunContext, driver: AgentDriver, *, max_turns: int = 4,
) -> SessionAgentClient:
    return SessionAgentClient(
        ctx, _profile(), driver, max_turns=max_turns)


def _design_pool(
        ctx: RunContext, blueprint_raw: dict,
) -> tuple[Stage4DesignSessionPool, list[_RequestDriver]]:
    split = _split_a_responder(blueprint_raw)
    drivers: list[_RequestDriver] = []

    def factory(purpose: str, chapter: int | None) -> _RequestDriver:
        def respond(request: AgentRequest) -> dict[str, Any]:
            return split(SimpleNamespace(
                system=request.system, user=request.prompt))

        driver = _RequestDriver(
            respond,
            session_id=f'provider-design-{"strategy" if chapter is None else chapter}',
        )
        drivers.append(driver)
        return driver

    return Stage4DesignSessionPool(ctx, driver_factory=factory), drivers


def _land_design(
        book: Path, blueprint_raw: dict, *, finish: bool = True,
) -> tuple[RunContext, BlindCandidate]:
    ctx = _ctx(book, 'stage4.design')
    pool, drivers = _design_pool(ctx, blueprint_raw)
    artifact = plan.run_design(ctx, pool)
    if finish:
        pool.finish_all(delivered=True)
    pool.close()
    assert len(drivers) == 1 + len(blueprint_raw['blueprints'])
    assert all(driver.calls == 1 for driver in drivers)
    return ctx, artifact


def _session_record(book: Path, session_run_id: str):
    matches = [
        record for record in agent_session_audit.fold(
            agent_session_audit.read_events(book.parent))
        if record.session_run_id == session_run_id
    ]
    assert len(matches) == 1
    return matches[0]


def test_design_checkpoint_and_committed_restart_are_zero_wire(tmp_path: Path) -> None:
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    first_ctx, artifact = _land_design(
        book, blueprints.model_dump(by_alias=True), finish=False)

    assert ArtifactStore(first_ctx).read(
        BlindCandidate, STAGE4_BLIND_CANDIDATE) == artifact
    ordinals = tuple(row.chapter_ordinal for row in blueprints.blueprints)
    checkpoint_paths = [
        paths.stage4_design_unit_checkpoint_dir(
            book, first_ctx.run_id, chapter) / 'rev-0001.json'
        for chapter in (None, *ordinals)
    ]
    assert all(path.is_file() for path in checkpoint_paths)
    assert stage4_runs.design_aggregate_committed(first_ctx) is not None
    checkpoints = [
        json.loads(path.read_text(encoding='utf-8'))
        for path in checkpoint_paths
    ]
    assert all(
        _session_record(book, row['session_run_id']).end is None
        for row in checkpoints)
    assert artifact.provenance.usage is not None
    assert artifact.provenance.usage.calls_made == 1 + len(ordinals)

    restarted = _ctx(
        book, 'stage4.design', run_id=first_ctx.run_id, code=first_ctx.code)
    pool = Stage4DesignSessionPool(
        restarted,
        driver_factory=lambda _purpose, _chapter: (_ for _ in ()).throw(
            AssertionError('committed 恢复不得构造 driver')),
    )
    recovered = plan.run_design(restarted, pool)
    pool.finish_all(delivered=True)
    pool.close()
    assert recovered == artifact
    for checkpoint in checkpoints:
        ended = _session_record(book, checkpoint['session_run_id']).end
        assert ended is not None
        assert (ended.process_state, ended.delivery_state, ended.error_kind) == (
            'ok', 'delivered', None)


def test_design_prepared_tail_recovers_same_candidate_and_usage_zero_wire(
        tmp_path: Path, monkeypatch,
) -> None:
    """崩在 formal write 后、adoption 前时，封存候选可跨进程零 wire 收尾。"""
    import pytest

    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    blueprint_raw = blueprints.model_dump(by_alias=True)
    first = _ctx(book, 'stage4.design')
    pool, drivers = _design_pool(first, blueprint_raw)
    original_restore = stage4_runs.restore_design_aggregate_adoptions

    def crash_after_formal_write(*args, **kwargs):
        assert (book / STAGE4_BLIND_CANDIDATE).is_file()
        raise RuntimeError('模拟 aggregate prepared 正式写盘尾窗中断')

    monkeypatch.setattr(
        stage4_runs, 'restore_design_aggregate_adoptions',
        crash_after_formal_write)
    with pytest.raises(RuntimeError, match='prepared'):
        plan.run_design(first, pool)
    pool.close()  # 模拟进程直接退出：不写 ended，不伪造业务终态。
    monkeypatch.setattr(
        stage4_runs, 'restore_design_aggregate_adoptions', original_restore)

    assert len(drivers) == 1 + len(blueprints.blueprints)
    assert stage4_runs.design_aggregate_prepared(first) is not None
    assert stage4_runs.design_aggregate_committed(first) is None
    sealed = ArtifactStore(first).read(
        BlindCandidate, STAGE4_BLIND_CANDIDATE)

    restarted = _ctx(
        book, 'stage4.design', run_id=first.run_id, code=first.code)
    recovered_pool = Stage4DesignSessionPool(
        restarted,
        driver_factory=lambda _purpose, _chapter: (_ for _ in ()).throw(
            AssertionError('prepared 尾窗恢复不得构造 driver')),
    )
    recovered = plan.run_design(restarted, recovered_pool)
    recovered_pool.finish_all(delivered=True)
    recovered_pool.close()

    assert recovered == sealed
    assert recovered.provenance.usage == sealed.provenance.usage
    assert stage4_runs.design_aggregate_committed(restarted) is not None


def test_legacy_adopter_refuses_foreign_prepared_design_aggregate(
        tmp_path: Path, monkeypatch,
) -> None:
    """旧 whole-book 恢复口不能踩过另一 run 尚未收完的 split 提交。"""
    import pytest
    from novel.llm import response_repair

    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    blueprint_raw = blueprints.model_dump(by_alias=True)
    split_ctx = _ctx(book, 'stage4.design')
    pool, _drivers = _design_pool(split_ctx, blueprint_raw)
    original_restore = stage4_runs.restore_design_aggregate_adoptions

    def crash_after_formal_write(*args, **kwargs):
        assert (book / STAGE4_BLIND_CANDIDATE).is_file()
        raise RuntimeError('模拟 foreign aggregate prepared 尾窗')

    monkeypatch.setattr(
        stage4_runs, 'restore_design_aggregate_adoptions',
        crash_after_formal_write)
    with pytest.raises(RuntimeError, match='foreign aggregate prepared'):
        plan.run_design(split_ctx, pool)
    pool.close()
    monkeypatch.setattr(
        stage4_runs, 'restore_design_aggregate_adoptions', original_restore)
    assert stage4_runs.design_aggregate_prepared(split_ctx) is not None
    formal_before = (book / STAGE4_BLIND_CANDIDATE).read_bytes()

    legacy_ctx = _legacy_ctx(book, 'stage4.design', paid=False)
    _truth, mapping = build_blind_projection(
        blueprints.model_dump(by_alias=True, mode='json'),
        root_clean_sha1=legacy_ctx.root_clean_sha1,
    )
    response = _retain_a_response(
        blueprints.model_dump(by_alias=True, mode='json'), mapping)
    attempt = _seal_a_validator_rejection(
        legacy_ctx, book, response=response,
        why='合成的旧整书 validator 拒稿')

    with pytest.raises(ProvenanceRefused, match='仍持有 prepared'):
        plan.run_adopt_rejected(
            legacy_ctx, attempt_id=attempt,
            actor='tester', reason='不得覆盖 split 尾窗')

    assert (book / STAGE4_BLIND_CANDIDATE).read_bytes() == formal_before
    assert response_repair.find_attempt(book, attempt).outcome is None
    assert not any(
        row.get('kind') == 'artifact_adoption'
        and row.get('attempt_id') == attempt
        for row in legacy_ctx.ledger.audit_events)


def test_plan_infeasible_lands_only_binding_and_recovers_without_wire(
        tmp_path: Path,
) -> None:
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    design_ctx, candidate = _land_design(
        book, blueprints.model_dump(by_alias=True))
    plan_ctx = _ctx(book, 'stage4.plan')
    driver = _OneTurnDriver(
        _infeasible_b_response(candidate), session_id='provider-plan-only')
    client = _client(plan_ctx, driver)
    landed = plan.run_plan(plan_ctx, client)
    client.close()

    assert isinstance(landed, EvidenceBinding)
    assert landed.status == 'infeasible'
    store = ArtifactStore(plan_ctx)
    assert store.exists(STAGE4_EVIDENCE_BINDING)
    assert not store.exists(STAGE4_PLAN_CHAPTERS)
    assert not store.exists(STAGE4_WRITER_CONTEXT)
    assert stage4_runs.committed(plan_ctx) is not None
    assert driver.calls == 1

    restarted = _ctx(
        book, 'stage4.plan', run_id=plan_ctx.run_id, code=plan_ctx.code)
    plan_checkpoint_path = (
        book / '_stage4_runs' / plan_ctx.run_id / 'plan'
        / 'checkpoints' / 'rev-0001.json')
    plan_checkpoint = json.loads(plan_checkpoint_path.read_text(encoding='utf-8'))
    assert _session_record(book, plan_checkpoint['session_run_id']).end is None
    no_wire = _client(restarted, _NoWireDriver())
    recovered = plan.run_plan(restarted, no_wire)
    no_wire.close()
    assert isinstance(recovered, EvidenceBinding)
    assert recovered == landed
    ended = _session_record(book, plan_checkpoint['session_run_id']).end
    assert ended is not None
    assert (ended.process_state, ended.delivery_state, ended.error_kind) == (
        'ok', 'delivered', None)

    plan_checkpoint = json.loads((
        book / '_stage4_runs' / plan_ctx.run_id / 'plan'
        / 'checkpoints' / 'rev-0001.json').read_text(encoding='utf-8'))
    design_checkpoints = [
        json.loads(path.read_text(encoding='utf-8'))
        for path in (
            paths.stage4_design_unit_checkpoint_dir(
                book, design_ctx.run_id, None) / 'rev-0001.json',
            *(paths.stage4_design_unit_checkpoint_dir(
                book, design_ctx.run_id, row.chapter_ordinal) / 'rev-0001.json'
              for row in blueprints.blueprints),
        )
    ]
    assert plan_checkpoint['provider_session_id'] == 'provider-plan-only'
    assert all(
        row['provider_session_id'] != plan_checkpoint['provider_session_id']
        for row in design_checkpoints)


def _hard_bound_response(
        candidate: BlindCandidate, blueprints_raw: dict,
) -> dict:
    """构造一份可编译、但材料覆盖必然 HARD 的 B 响应。"""
    payload = _bound_b_response(candidate, blueprints_raw)
    for chapter in payload['chapters']:
        for scene in chapter['scenes']:
            scene['material_refs'] = []
    return payload


def test_plan_agent_exhaustion_commits_binding_and_invalid_then_recovers_zero_wire(
        tmp_path: Path, monkeypatch,
) -> None:
    """业务 HARD 先续同 session；耗尽才落一次 INVALID，旧正式 bundle 不动。"""
    import pytest

    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    _design_ctx, candidate = _land_design(
        book, blueprints.model_dump(by_alias=True))

    # 先落一份下游正在消费的 clean plan/context，随后 HARD run 必须逐字保留它们。
    clean_ctx = _ctx(book, 'stage4.plan')
    clean_driver = _OneTurnDriver(
        _bound_b_response(candidate, blueprints.model_dump(by_alias=True)),
        session_id='provider-plan-clean')
    clean_client = _client(clean_ctx, clean_driver)
    assert plan.run_plan(clean_ctx, clean_client).passed
    clean_client.finish(process_state='ok', delivery_state='delivered')
    clean_client.close()
    plan_before = (book / STAGE4_PLAN_CHAPTERS).read_bytes()
    context_before = (book / STAGE4_WRITER_CONTEXT).read_bytes()
    # 先留一份旧 live INVALID，专门覆盖“替换问题稿”的
    # crash 窗口；正式 plan/context 仍是下游正在消费的 clean bundle。
    clean_store = ArtifactStore(clean_ctx)
    prior_plan = clean_store.read(PlanChapters, STAGE4_PLAN_CHAPTERS)
    clean_store.write_invalid(
        STAGE4_PLAN_CHAPTERS, prior_plan, gates_skipped=[])
    prior_invalid = (book / plan._INVALID).read_bytes()

    hard_ctx = _ctx(book, 'stage4.plan')
    hard_driver = _OneTurnDriver(
        _hard_bound_response(candidate, blueprints.model_dump(by_alias=True)),
        session_id='provider-plan-hard')
    hard_client = _client(hard_ctx, hard_driver, max_turns=2)
    original_write_immutable = ArtifactStore._write_immutable
    crashed = False

    def _crash_after_round_copy(self, destination, data):
        nonlocal crashed
        if not crashed and destination.name.startswith('b.'):
            crashed = True
            raise RuntimeError('模拟 INVALID round copy 后进程中断')
        return original_write_immutable(self, destination, data)

    monkeypatch.setattr(
        ArtifactStore, '_write_immutable', _crash_after_round_copy)
    with pytest.raises(RuntimeError, match='round copy'):
        plan.run_plan(hard_ctx, hard_client)
    hard_client.close()
    monkeypatch.setattr(
        ArtifactStore, '_write_immutable', original_write_immutable)

    assert hard_driver.calls == 2
    assert len(hard_driver.requests) == 2
    assert hard_driver.requests[1].system is None
    assert 'code_validation_failed' in hard_driver.requests[1].prompt
    checkpoint_dir = (
        book / '_stage4_runs' / hard_ctx.run_id / 'plan' / 'checkpoints')
    assert [path.name for path in checkpoint_dir.glob('*.json')] == [
        'rev-0001.json', 'rev-0002.json']
    latest_checkpoint = json.loads((checkpoint_dir / 'rev-0002.json').read_text(
        encoding='utf-8'))
    assert _session_record(
        book, latest_checkpoint['session_run_id']).end is None
    assert (book / plan._INVALID).read_bytes() == prior_invalid
    assert stage4_runs.committed(hard_ctx) is None

    store = ArtifactStore(hard_ctx)
    binding = store.read_own(EvidenceBinding, STAGE4_EVIDENCE_BINDING)[0]
    assert binding.status == 'bound'
    assert (book / STAGE4_PLAN_CHAPTERS).read_bytes() == plan_before
    assert (book / STAGE4_WRITER_CONTEXT).read_bytes() == context_before

    restarted = _ctx(
        book, 'stage4.plan', run_id=hard_ctx.run_id, code=hard_ctx.code)
    no_wire = _client(restarted, _NoWireDriver(), max_turns=2)
    with pytest.raises(GateBlocked, match='预算耗尽'):
        plan.run_plan(restarted, no_wire)
    no_wire.close()
    invalid = ArtifactStore(restarted).read_own(
        PlanChapters, plan._INVALID)[0]
    assert invalid.output_chapters
    committed = stage4_runs.committed(restarted)
    assert committed is not None
    assert committed['touched_paths'] == sorted((
        STAGE4_EVIDENCE_BINDING, plan._INVALID))
    assert committed['landed_payload_sha256s'].keys() == {
        STAGE4_EVIDENCE_BINDING}
    assert (book / STAGE4_PLAN_CHAPTERS).read_bytes() == plan_before
    assert (book / STAGE4_WRITER_CONTEXT).read_bytes() == context_before
    ended = _session_record(book, latest_checkpoint['session_run_id']).end
    assert ended is not None
    assert (ended.process_state, ended.delivery_state, ended.error_kind) == (
        'ok', 'blocked', 'checkpoint_business_rejected')
    archived_invalids = list((book / 'stage4' / '_rounds').glob(
        'plan_chapters.INVALID.r*.json'))
    assert any(path.read_bytes() == prior_invalid for path in archived_invalids)
