#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 design/plan executor 候选边界的零 wire 集成测试。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from contracts.artifacts import (
    STAGE4_BLIND_CANDIDATE, STAGE4_EVIDENCE_BINDING,
    STAGE4_PLAN_CHAPTERS, STAGE4_WRITER_CONTEXT,
)
from contracts.schema.orchestration import AgentProfile
from contracts.schema.api import DesignRepairScope
from contracts.schema.stage4 import BlindCandidate, EvidenceBinding, PlanChapters
from novel import agent_session_audit, paths, prov
from novel.agents import (
    AgentDriver, AgentProbe, AgentRequest, AgentTurn, AgentUsage,
)
from novel.agents.session_client import SessionAgentClient
from novel.errors import (
    ApprovalRequired, ArtifactUnreadable, GateBlocked, ProvenanceRefused,
)
from novel.llm import agent_runs, stage4_runs
from novel.llm.cache import CachePolicy
from novel.llm.ledger import CostLedger, ledger_path
from novel.run import RunContext
from novel.node_agents import Stage4DesignSessionPool, Stage4PlanSessionPool
from novel.stages.stage4 import plan
from novel.stages.stage4.blind import build_blind_projection
from novel.store import ArtifactStore
from tests.test_stage4_v3_integration import (
    _book, _bound_b_response, _infeasible_b_response, _seed_current_stage2,
    _split_a_responder, _ctx as _legacy_ctx, _retain_a_response,
    _seal_a_validator_rejection,
)


@pytest.fixture(autouse=True)
def _stable_code_version(monkeypatch) -> None:
    """并行编辑不应把本模块的 durable-checkpoint 用例变成 git 漂移假红。"""
    monkeypatch.setattr(
        prov, 'code_version', lambda: 'test-stage4-agent-boundary')


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
        code: str | None = None, profile: AgentProfile | None = None,
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
        executor_profile=profile or _profile(),
        run_id=run_id or '',
    )


def _client(
        ctx: RunContext, driver: AgentDriver, *, max_turns: int = 4,
) -> SessionAgentClient:
    return SessionAgentClient(
        ctx, _profile(), driver, max_semantic_turns=max_turns)


def _plan_pool(ctx: RunContext, monkeypatch, *, first_response=None,
               max_turns: int = 4, session_id: str = 'provider-plan-chapter'):
    """真实章池：只让第一章有歧义，续修沿用同章请求与物理 session。"""
    from tests.test_stage4_plan_chapters import _inject_stage_ambiguities
    monkeypatch.setattr(
        plan, 'build_mechanical_baseline',
        _inject_stage_ambiguities(plan.build_mechanical_baseline, (1,)))
    response = None
    requests = 0

    def answer(request):
        nonlocal response, requests
        requests += 1
        if response is None:
            lines = request.prompt.splitlines()
            ambiguities = json.loads(lines[
                lines.index(plan.prompts.BLOCK_CHAPTER_AMBIGUITIES) + 1])
            response = {'resolutions': [
                {'ambiguity_id': row['ambiguity_id'],
                 ('stage_ref' if row['kind'] == 'stage_signature_conflict'
                  else 'candidate_scene_id'): row['candidates'][0]}
                for row in ambiguities], 'binding_concerns': []}
        return first_response if requests == 1 and first_response is not None else response

    driver = _RequestDriver(answer, session_id=session_id)
    return Stage4PlanSessionPool(
        ctx, driver_factory=lambda _purpose, chapter: driver,
        max_turns=max_turns, concurrency=1), driver


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


def _rewrite_standard_aggregate_primary(
        ctx: RunContext, mutate: Callable[[dict[str, Any]], None],
        *, refresh_payload_links: bool = True,
        refresh_commit: bool = True,
) -> tuple[str, str]:
    """按旧 producer 形状重封 journal；正式候选文件保持不动。"""
    target = (
        paths.stage4_run_path(ctx.novel_dir, ctx.run_id, 'stage4.design')
        / stage4_runs.DESIGN_AGGREGATE_COMMIT_FILE
    )
    record = json.loads(target.read_text(encoding='utf-8'))
    primary = record['primary_payload']
    before = agent_runs.canonical_sha256(primary)
    mutate(primary)
    after = agent_runs.canonical_sha256(primary)
    assert after != before
    if refresh_payload_links:
        record['primary_payload_sha256'] = after
        record['primary_payload_fingerprint'] = prov.projection_fingerprint(primary)
        finalization = record['finalization_payload']
        assert finalization['candidate_sha256'] == before
        finalization['candidate_sha256'] = after
        record['finalization_sha256'] = agent_runs.canonical_sha256(finalization)
        record['landed_payload_sha256s'][STAGE4_BLIND_CANDIDATE] = after
    if refresh_commit:
        record['commit_sha256'] = stage4_runs._design_aggregate_hash(record)
    agent_runs._atomic_json(target, record)
    return before, after


def _repair_aggregate_path(ctx: RunContext) -> Path:
    return (
        paths.stage4_run_path(ctx.novel_dir, ctx.run_id, 'stage4.design')
        / stage4_runs.DESIGN_REPAIR_AGGREGATE_COMMIT_FILE
    )


def _rewrite_repair_aggregate_primary(
        ctx: RunContext, mutate: Callable[[dict[str, Any]], None],
) -> tuple[str, str]:
    target = _repair_aggregate_path(ctx)
    record = json.loads(target.read_text(encoding='utf-8'))
    primary = record['primary_payload']
    before = agent_runs.canonical_sha256(primary)
    mutate(primary)
    after = agent_runs.canonical_sha256(primary)
    assert after != before
    record['primary_payload_sha256'] = after
    record['primary_payload_fingerprint'] = prov.projection_fingerprint(primary)
    finalization = record['finalization_payload']
    assert finalization['candidate_sha256'] == before
    finalization['candidate_sha256'] = after
    record['finalization_sha256'] = agent_runs.canonical_sha256(finalization)
    record['landed_payload_sha256s'][STAGE4_BLIND_CANDIDATE] = after
    record['commit_sha256'] = stage4_runs._design_repair_aggregate_hash(record)
    agent_runs._atomic_json(target, record)
    return before, after


def _drop_legacy_standard_repair_nulls(primary: dict[str, Any]) -> None:
    provenance = primary['_provenance']
    for field in (
            'parent_candidate_sha256', 'repair_decision_sha256',
            'unit_lineage'):
        assert provenance.pop(field) is None


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


def test_bare_initial_design_cannot_overwrite_current_candidate_or_start_wire(
        tmp_path: Path,
) -> None:
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    _parent_ctx, parent = _land_design(
        book, blueprints.model_dump(by_alias=True, mode='json'))
    before = (book / STAGE4_BLIND_CANDIDATE).read_bytes()
    retry_ctx = _ctx(book, 'stage4.design')
    pool = Stage4DesignSessionPool(
        retry_ctx,
        driver_factory=lambda _purpose, _chapter: (_ for _ in ()).throw(
            AssertionError('current A 的裸 initial 必须在构造 driver 前关闭')),
    )

    with pytest.raises(ApprovalRequired, match='裸 stage4.design 不得覆盖'):
        plan.run_design(retry_ctx, pool)
    pool.close()

    assert (book / STAGE4_BLIND_CANDIDATE).read_bytes() == before
    assert ArtifactStore(retry_ctx).read(
        BlindCandidate, STAGE4_BLIND_CANDIDATE) == parent
    assert not paths.stage4_run_path(
        book, retry_ctx.run_id, 'stage4.design').exists()


def test_design_repair_identity_rejects_coercion_and_nonwhitelist_defaults(
        tmp_path: Path,
) -> None:
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    parent_ctx, parent = _land_design(
        book, blueprints.model_dump(by_alias=True, mode='json'))
    target = (
        paths.stage4_run_path(
            book, parent_ctx.run_id, 'stage4.design')
        / stage4_runs.DESIGN_AGGREGATE_COMMIT_FILE
    )
    original = json.loads(target.read_text(encoding='utf-8'))
    candidate_file_sha = hashlib.sha256(
        (book / STAGE4_BLIND_CANDIDATE).read_bytes()).hexdigest()

    for coerced in ('1', True, 1.0):
        agent_runs._atomic_json(target, original)

        def drift_type(primary: dict[str, Any]) -> None:
            primary['output_chapters'][0]['output_ordinal'] = coerced

        _rewrite_standard_aggregate_primary(parent_ctx, drift_type)
        record = json.loads(target.read_text(encoding='utf-8'))
        with pytest.raises(ArtifactUnreadable, match='BlindCandidate'):
            stage4_runs._normalized_design_candidate_identity(
                record['primary_payload'], label='test standard journal',
                aggregate_kind='standard')
        capability = stage4_runs.inspect_design_repair_capability(
            parent_ctx,
            candidate_payload=parent.model_dump(by_alias=True, mode='json'),
            candidate_file_sha256=candidate_file_sha,
        )
        assert capability.repairable is False
        assert capability.reason_code == 'checkpoint_lineage_unavailable'

    agent_runs._atomic_json(target, original)

    def delete_nonwhitelist_default(primary: dict[str, Any]) -> None:
        assert primary['_provenance'].pop(
            'rejected_response_adoption') is None

    _rewrite_standard_aggregate_primary(
        parent_ctx, delete_nonwhitelist_default)
    record = json.loads(target.read_text(encoding='utf-8'))
    with pytest.raises(ArtifactUnreadable, match='白名单之外'):
        stage4_runs._normalized_design_candidate_identity(
            record['primary_payload'], label='test standard journal',
            aggregate_kind='standard')
    capability = stage4_runs.inspect_design_repair_capability(
        parent_ctx,
        candidate_payload=parent.model_dump(by_alias=True, mode='json'),
        candidate_file_sha256=candidate_file_sha,
    )
    assert capability.repairable is False
    assert capability.reason_code == 'checkpoint_lineage_unavailable'


def test_design_repair_identity_keeps_raw_sha_and_commit_strict(
        tmp_path: Path,
) -> None:
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    parent_ctx, parent = _land_design(
        book, blueprints.model_dump(by_alias=True, mode='json'))
    target = (
        paths.stage4_run_path(
            book, parent_ctx.run_id, 'stage4.design')
        / stage4_runs.DESIGN_AGGREGATE_COMMIT_FILE
    )
    original = json.loads(target.read_text(encoding='utf-8'))
    candidate_file_sha = hashlib.sha256(
        (book / STAGE4_BLIND_CANDIDATE).read_bytes()).hexdigest()

    for refresh_payload_links, refresh_commit in ((False, True), (True, False)):
        agent_runs._atomic_json(target, original)
        _rewrite_standard_aggregate_primary(
            parent_ctx, _drop_legacy_standard_repair_nulls,
            refresh_payload_links=refresh_payload_links,
            refresh_commit=refresh_commit,
        )
        with pytest.raises((ArtifactUnreadable, ProvenanceRefused)):
            stage4_runs._committed_design_aggregate_for_run(
                parent_ctx, parent_ctx.run_id)
        capability = stage4_runs.inspect_design_repair_capability(
            parent_ctx,
            candidate_payload=parent.model_dump(by_alias=True, mode='json'),
            candidate_file_sha256=candidate_file_sha,
        )
        assert capability.repairable is False
        assert capability.reason_code == 'checkpoint_lineage_unavailable'


def _scene_goal_constraint(candidate: Any) -> dict[str, Any]:
    """批次 2 后唯一可默认下发的具体修复脚手架（scene_goal, arity 1,1）。"""
    chapter = candidate.output_chapters[0]
    return {
        'code': 'scene_goal',
        'scene_refs': [chapter.scenes[0].candidate_scene_id],
        'opaque_aliases': [next(iter(candidate.fact_aliases))],
    }


def _single_chapter_repair_scope(
        book: Path, candidate: BlindCandidate,
) -> DesignRepairScope:
    return DesignRepairScope.model_validate({
        'candidate_sha256': hashlib.sha256(
            (book / STAGE4_BLIND_CANDIDATE).read_bytes()).hexdigest(),
        'aggregate_subject_sha256': 'a' * 64,
        'decision_sha256': 'b' * 64,
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
            'subject_sha256': 'c' * 64,
            'constraints': [_scene_goal_constraint(candidate)],
        }],
    })


def _leave_interrupted_design_repair_checkpoint(
        book: Path, scope: DesignRepairScope,
) -> tuple[RunContext, _RequestDriver]:
    """造一份只有合法章 checkpoint、没有 aggregate 的旧 epoch。"""
    source_ctx = _ctx(book, 'stage4.design')
    source_drivers: list[_RequestDriver] = []

    def source_factory(_purpose: str, chapter: int | None) -> _RequestDriver:
        assert chapter == 1

        def respond(request: AgentRequest) -> dict[str, Any]:
            lines = request.prompt.splitlines()
            marker = lines.index(plan.prompts.BLOCK_PREVIOUS_CHAPTER_RESPONSE)
            return json.loads(lines[marker + 1])['response']

        driver = _RequestDriver(
            respond, session_id='provider-old-epoch-repair-ch1')
        source_drivers.append(driver)
        return driver

    source_pool = Stage4DesignSessionPool(
        source_ctx, driver_factory=source_factory, max_turns=2)
    prepared, _responses, targets, _inherited, _parent, _parent_run, _input = \
        plan._prepare_design_repair(source_ctx, scope)
    landed_units = plan._request_design_chapters(
        source_ctx, source_pool, prepared, targets)
    assert [chapter for chapter, _response in landed_units] == [1]
    source_pool.finish_all(
        delivered=False, failure=RuntimeError('模拟 epoch rollover 前中断'))
    source_pool.close()
    assert source_drivers[0].calls == 1
    assert not (paths.stage4_run_path(
        book, source_ctx.run_id, 'stage4.design')
        / stage4_runs.DESIGN_REPAIR_AGGREGATE_COMMIT_FILE).exists()
    return source_ctx, source_drivers[0]


def _candidate_at(book: Path, ctx: RunContext) -> Any:
    """读盘上当前正式候选；返修循环里每轮候选都会换。"""
    return ArtifactStore(ctx).read(BlindCandidate, STAGE4_BLIND_CANDIDATE)


def test_strategy_replan_refuses_source_equal_target_before_any_wire(
        tmp_path: Path, monkeypatch) -> None:
    """旧 producer run 不能承接自己的 replan；拒绝发生在 strategy wire 之前。"""
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    parent_ctx, _parent = _land_design(
        book, blueprints.model_dump(by_alias=True, mode='json'))
    candidate_path = book / STAGE4_BLIND_CANDIDATE
    before = candidate_path.read_bytes()
    scope = DesignRepairScope.model_validate({
        'candidate_sha256': hashlib.sha256(before).hexdigest(),
        'aggregate_subject_sha256': 'a' * 64,
        'decision_sha256': 'b' * 64,
        'repair_capability': {
            'repairable': True, 'reason_code': 'ready',
            'chapters': [{
                'output_ordinal': 1, 'committed_rounds': 0,
                'request_repair_available': True,
            }],
        },
        'strategy_replan': True,
        'target_chapters': [],
        'chapters': [],
    })
    same_run = _ctx(
        book, 'stage4.design', run_id=parent_ctx.run_id,
        code=parent_ctx.code)
    wire_calls: list[object] = []

    def must_not_wire(*args, **kwargs):
        wire_calls.append((args, kwargs))
        raise AssertionError('source==target replan 不得进入 strategy wire')

    monkeypatch.setattr(plan, '_request_design_strategy', must_not_wire)

    with pytest.raises(
            ProvenanceRefused,
            match='strategy replan 必须创建新 run，不得原 run 续写'):
        plan.run_design(same_run, SimpleNamespace(), repair_scope=scope)

    assert wire_calls == []
    assert candidate_path.read_bytes() == before


def test_targeted_design_repair_reruns_only_target_and_reuses_parent_checkpoints(
        tmp_path: Path,
) -> None:
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    blueprint_raw = blueprints.model_dump(by_alias=True, mode='json')
    parent_ctx, parent = _land_design(book, blueprint_raw)
    normalized_parent_sha, legacy_raw_parent_sha = \
        _rewrite_standard_aggregate_primary(
            parent_ctx, _drop_legacy_standard_repair_nulls)
    assert normalized_parent_sha == agent_runs.canonical_sha256(
        parent.model_dump(by_alias=True, mode='json'))
    assert legacy_raw_parent_sha != normalized_parent_sha
    ordinals = tuple(row.chapter_ordinal for row in blueprints.blueprints)
    parent_checkpoint_bytes = {
        chapter: (
            paths.stage4_design_unit_checkpoint_dir(
                book, parent_ctx.run_id, chapter) / 'rev-0001.json'
        ).read_bytes()
        for chapter in (None, *ordinals)
    }
    candidate_file_sha = hashlib.sha256(
        (book / STAGE4_BLIND_CANDIDATE).read_bytes()).hexdigest()
    capability = stage4_runs.inspect_design_repair_capability(
        parent_ctx,
        candidate_payload=parent.model_dump(by_alias=True, mode='json'),
        candidate_file_sha256=candidate_file_sha,
    )
    assert capability.repairable is True
    assert capability.reason_code == 'ready'
    assert capability.parent_run_id == parent_ctx.run_id
    assert capability.chapter_repair_rounds == ()
    scope = DesignRepairScope.model_validate({
        'candidate_sha256': candidate_file_sha,
        'aggregate_subject_sha256': 'a' * 64,
        'decision_sha256': 'b' * 64,
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
            'subject_sha256': 'c' * 64,
            'constraints': [_scene_goal_constraint(parent)],
        }],
    })
    repair_ctx = _ctx(book, 'stage4.design')
    drivers: list[_RequestDriver] = []

    def factory(purpose: str, chapter: int | None) -> _RequestDriver:
        assert purpose == stage4_runs.design_unit_purpose(1)
        assert chapter == 1

        def respond(request: AgentRequest) -> dict[str, Any]:
            lines = request.prompt.splitlines()
            marker = lines.index(plan.prompts.BLOCK_PREVIOUS_CHAPTER_RESPONSE)
            return json.loads(lines[marker + 1])['response']

        driver = _RequestDriver(
            respond, session_id='provider-targeted-repair-ch1')
        drivers.append(driver)
        return driver

    pool = Stage4DesignSessionPool(
        repair_ctx, driver_factory=factory, max_turns=2)
    repaired = plan.run_design(
        repair_ctx,
        pool,
        repair_decision_sha256=scope.decision_sha256,
        repair_scope=scope,
    )
    pool.finish_all(delivered=True)
    pool.close()

    assert len(drivers) == 1 and drivers[0].calls == 1
    request = drivers[0].requests[0]
    assert request.system == plan.prompts.blind_chapter_repair_system()
    assert plan.prompts.BLOCK_REPAIR_CONSTRAINTS in request.prompt
    assert 'scene_goal' in request.prompt
    assert repaired.provenance.architecture == 'book_strategy_targeted_repair_v1'
    assert repaired.provenance.repair_decision_sha256 == scope.decision_sha256
    assert repaired.provenance.parent_candidate_sha256 == \
        agent_runs.canonical_sha256(
            parent.model_dump(by_alias=True, mode='json'))
    lineage = {row.chapter_ordinal: row for row in repaired.provenance.unit_lineage or []}
    assert set(lineage) == {None, *ordinals}
    assert lineage[1].source_kind == 'repair'
    assert lineage[1].source_run_id == repair_ctx.run_id
    assert all(
        row.source_kind == 'inherited' and row.source_run_id == parent_ctx.run_id
        for chapter, row in lineage.items() if chapter != 1)
    parent_by = {row.output_ordinal: row for row in parent.output_chapters}
    repaired_by = {row.output_ordinal: row for row in repaired.output_chapters}
    assert all(
        repaired_by[chapter] == parent_by[chapter]
        for chapter in ordinals if chapter != 1)
    assert all((
        paths.stage4_design_unit_checkpoint_dir(
            book, parent_ctx.run_id, chapter) / 'rev-0001.json'
    ).read_bytes() == raw for chapter, raw in parent_checkpoint_bytes.items())
    assert not paths.stage4_design_unit_run_path(
        book, repair_ctx.run_id, None).exists()
    assert all(
        not paths.stage4_design_unit_run_path(
            book, repair_ctx.run_id, chapter).exists()
        for chapter in ordinals if chapter != 1)
    journal = stage4_runs.design_repair_aggregate_state(repair_ctx)
    assert journal is not None and journal['state'] == 'committed'
    assert journal['target_chapters'] == [1]
    assert journal['parent_candidate_sha256'] == normalized_parent_sha
    repaired_capability = stage4_runs.inspect_design_repair_capability(
        repair_ctx,
        candidate_payload=repaired.model_dump(by_alias=True, mode='json'),
        candidate_file_sha256=hashlib.sha256(
            (book / STAGE4_BLIND_CANDIDATE).read_bytes()).hexdigest(),
    )
    assert repaired_capability.repairable is True
    assert dict(repaired_capability.chapter_repair_rounds) == {1: 1}

    # repair primary 内的 lineage 漂移本身仍是合法 BlindCandidate；完整候选
    # 身份比较必须拒绝，不能只靠 schema 冲突碰巧挡住。
    repair_target = _repair_aggregate_path(repair_ctx)
    original_repair = json.loads(repair_target.read_text(encoding='utf-8'))

    def drift_valid_lineage(primary: dict[str, Any]) -> None:
        row = primary['_provenance']['unit_lineage'][0]
        row['source_run_id'] += '-schema-valid-drift'

    _rewrite_repair_aggregate_primary(repair_ctx, drift_valid_lineage)
    tampered = json.loads(repair_target.read_text(encoding='utf-8'))
    BlindCandidate.model_validate(tampered['primary_payload'], strict=True)
    repaired_payload_sha = agent_runs.canonical_sha256(
        repaired.model_dump(by_alias=True, mode='json'))
    with pytest.raises(ArtifactUnreadable, match='unit_lineage'):
        stage4_runs.load_committed_design_sources(
            repair_ctx,
            parent_run_id=repair_ctx.run_id,
            parent_candidate_sha256=repaired_payload_sha,
        )
    with pytest.raises(ArtifactUnreadable, match='unit_lineage'):
        stage4_runs._repair_ancestry_rounds(repair_ctx, repair_ctx.run_id)
    drifted_capability = stage4_runs.inspect_design_repair_capability(
        repair_ctx,
        candidate_payload=repaired.model_dump(by_alias=True, mode='json'),
        candidate_file_sha256=hashlib.sha256(
            (book / STAGE4_BLIND_CANDIDATE).read_bytes()).hexdigest(),
    )
    assert drifted_capability.repairable is False
    assert drifted_capability.reason_code == 'checkpoint_lineage_unavailable'

    for field, drifted_sha in (
            ('parent_candidate_sha256', 'e' * 64),
            ('repair_decision_sha256', 'f' * 64)):
        agent_runs._atomic_json(repair_target, original_repair)

        def drift_top_level_link(primary: dict[str, Any]) -> None:
            primary['_provenance'][field] = drifted_sha

        _rewrite_repair_aggregate_primary(repair_ctx, drift_top_level_link)
        with pytest.raises(ArtifactUnreadable, match='parent/decision'):
            stage4_runs.load_committed_design_sources(
                repair_ctx,
                parent_run_id=repair_ctx.run_id,
                parent_candidate_sha256=repaired_payload_sha,
            )

    # foreign committed repair 必须复验 target↔source_kind/source_run↔attempts，
    # 不能只凭一份重封过 commit 的 target_chapters 给 ancestry 计轮。
    agent_runs._atomic_json(repair_target, original_repair)
    target_mismatch = json.loads(repair_target.read_text(encoding='utf-8'))
    assert len(ordinals) >= 2
    target_mismatch['target_chapters'] = [ordinals[1]]
    target_mismatch['commit_sha256'] = \
        stage4_runs._design_repair_aggregate_hash(target_mismatch)
    agent_runs._atomic_json(repair_target, target_mismatch)
    with pytest.raises(ProvenanceRefused, match='source_kind/source_run'):
        stage4_runs.load_committed_design_sources(
            repair_ctx,
            parent_run_id=repair_ctx.run_id,
            parent_candidate_sha256=repaired_payload_sha,
        )
    with pytest.raises(ProvenanceRefused, match='source_kind/source_run'):
        stage4_runs._repair_ancestry_rounds(repair_ctx, repair_ctx.run_id)
    mismatched_capability = stage4_runs.inspect_design_repair_capability(
        repair_ctx,
        candidate_payload=repaired.model_dump(by_alias=True, mode='json'),
        candidate_file_sha256=hashlib.sha256(
            (book / STAGE4_BLIND_CANDIDATE).read_bytes()).hexdigest(),
    )
    assert mismatched_capability.repairable is False
    assert mismatched_capability.reason_code == 'checkpoint_lineage_unavailable'


def test_design_repair_identity_normalization_keeps_real_drift_closed(
        tmp_path: Path,
) -> None:
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    parent_ctx, parent = _land_design(
        book, blueprints.model_dump(by_alias=True, mode='json'))

    def drift_business(primary: dict[str, Any]) -> None:
        _drop_legacy_standard_repair_nulls(primary)
        primary['reconstruction_strategy'] += '（journal 业务漂移）'

    _rewrite_standard_aggregate_primary(parent_ctx, drift_business)
    capability = stage4_runs.inspect_design_repair_capability(
        parent_ctx,
        candidate_payload=parent.model_dump(by_alias=True, mode='json'),
        candidate_file_sha256=hashlib.sha256(
            (book / STAGE4_BLIND_CANDIDATE).read_bytes()).hexdigest(),
    )
    assert capability.repairable is False
    assert capability.reason_code == 'legacy_or_untracked_candidate'


def test_design_repair_identity_normalization_does_not_erase_lineage_drift(
        tmp_path: Path,
) -> None:
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    parent_ctx, parent = _land_design(
        book, blueprints.model_dump(by_alias=True, mode='json'))

    def inject_nonempty_lineage(primary: dict[str, Any]) -> None:
        primary['_provenance']['parent_candidate_sha256'] = 'd' * 64

    _rewrite_standard_aggregate_primary(parent_ctx, inject_nonempty_lineage)
    capability = stage4_runs.inspect_design_repair_capability(
        parent_ctx,
        candidate_payload=parent.model_dump(by_alias=True, mode='json'),
        candidate_file_sha256=hashlib.sha256(
            (book / STAGE4_BLIND_CANDIDATE).read_bytes()).hexdigest(),
    )
    assert capability.repairable is False
    assert capability.reason_code == 'checkpoint_lineage_unavailable'


def test_targeted_repair_adopts_exact_checkpoint_across_code_epoch_without_wire(
        tmp_path: Path, monkeypatch) -> None:
    """换 epoch 后复用旧章响应，但不复制 checkpoint、不续旧 provider session。"""
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    _parent_ctx, parent = _land_design(
        book, blueprints.model_dump(by_alias=True, mode='json'))
    scope = _single_chapter_repair_scope(book, parent)
    source_ctx, source_driver = _leave_interrupted_design_repair_checkpoint(
        book, scope)
    assert source_driver.calls == 1

    monkeypatch.setattr(
        prov, 'code_version', lambda: 'test-stage4-agent-boundary-next-commit')
    target_ctx = _ctx(book, 'stage4.design')
    target_factory_calls: list[tuple[str, int | None]] = []

    def target_factory(purpose: str, chapter: int | None) -> _NoWireDriver:
        target_factory_calls.append((purpose, chapter))
        raise AssertionError('合法 historical checkpoint 不得新建 provider driver')

    target_pool = Stage4DesignSessionPool(
        target_ctx, driver_factory=target_factory, max_turns=2)
    repaired = plan.run_design(
        target_ctx, target_pool,
        repair_decision_sha256=scope.decision_sha256,
        repair_scope=scope,
    )
    target_pool.finish_all(delivered=True)
    target_pool.close()

    assert target_factory_calls == []
    lineage = {row.chapter_ordinal: row
               for row in repaired.provenance.unit_lineage or []}
    assert lineage[1].origin == 'historical_checkpoint'
    assert lineage[1].source_run_id == source_ctx.run_id
    assert lineage[1].source_ref is not None
    assert repaired.provenance.usage is not None
    assert repaired.provenance.usage.calls_made == 1
    manifest = (paths.stage4_run_path(
        book, target_ctx.run_id, 'stage4.design')
        / stage4_runs.DESIGN_HISTORICAL_ADOPTION_FILE)
    assert manifest.is_file()
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == lineage[1].source_ref
    target_checkpoint_dir = paths.stage4_design_unit_checkpoint_dir(
        book, target_ctx.run_id, 1)
    assert not list(target_checkpoint_dir.glob('rev-*.json')), \
        '历史 checkpoint 不得复制进 target run；仅允许本 run 的锁文件'
    assert paths.stage4_design_unit_checkpoint_dir(
        book, source_ctx.run_id, 1).joinpath('rev-0001.json').is_file()
    _parent, inherited = stage4_runs.load_committed_design_sources(
        target_ctx, parent_run_id=target_ctx.run_id,
        parent_candidate_sha256=agent_runs.canonical_sha256(
            repaired.model_dump(by_alias=True, mode='json')))
    inherited_history = next(row for row in inherited if row.chapter == 1)
    assert inherited_history.source_kind == 'inherited'
    assert inherited_history.origin == 'historical_checkpoint'
    assert stage4_runs._load_design_source(
        target_ctx, stage4_runs._design_source_row(inherited_history),
        default_run_id=inherited_history.source_run_id,
        default_source_kind='inherited') == inherited_history


def test_targeted_repair_refuses_historical_checkpoint_when_profile_changed(
        tmp_path: Path, monkeypatch) -> None:
    """prompt 相同也不够：resolved executor profile 漂移时必须走新 wire。"""
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    _parent_ctx, parent = _land_design(
        book, blueprints.model_dump(by_alias=True, mode='json'))
    scope = _single_chapter_repair_scope(book, parent)
    source_ctx, _driver = _leave_interrupted_design_repair_checkpoint(book, scope)

    # 新 epoch 的 live code guard 比对的是 prov.code_version() 现值，显式
    # code= 标签只对 legacy LlmClient 生效；这里与前一条测试同样换 git 现值。
    monkeypatch.setattr(
        prov, 'code_version', lambda: 'next-code-with-new-profile')
    target_profile = AgentProfile(
        driver='codex', model='gpt-5.6-sol',
        reasoning_effort='medium', execution_mode='standard')
    target_ctx = _ctx(book, 'stage4.design', profile=target_profile)
    prepared, _responses, targets, _inherited, _parent, _parent_run, repair_sha = \
        plan._prepare_design_repair(target_ctx, scope)
    target_by = {row.chapter_ordinal: row for row in targets}
    units = tuple(plan._design_chapter_unit(row) for row in targets)

    def state_payload(response: Any) -> dict[str, Any]:
        chapter = target_by[1]
        compiled = chapter.validator.compiled
        if compiled is None:
            why = chapter.validator(response, None)
            compiled = chapter.validator.compiled
            assert why is None and compiled is not None
        return plan._design_chapter_checkpoint_state(response, compiled)

    adopted = stage4_runs.restore_or_select_design_historical_adoption(
        target_ctx, requests=units, repair_input_sha256=repair_sha,
        eligible_chapters=[1], validators={1: target_by[1].validator},
        state_payloads={1: state_payload})

    assert adopted is None
    assert not (paths.stage4_run_path(
        book, target_ctx.run_id, 'stage4.design')
        / stage4_runs.DESIGN_HISTORICAL_ADOPTION_FILE).exists()
    assert paths.stage4_design_unit_checkpoint_dir(
        book, source_ctx.run_id, 1).joinpath('rev-0001.json').is_file()


def test_targeted_design_repair_opens_new_typed_epoch_after_two_committed_rounds(
        tmp_path: Path,
) -> None:
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    parent_ctx, current = _land_design(
        book, blueprints.model_dump(by_alias=True, mode='json'))
    assert len(current.output_chapters) >= 2

    for repair_round in (1, 2):
        candidate_sha = hashlib.sha256(
            (book / STAGE4_BLIND_CANDIDATE).read_bytes()).hexdigest()
        decision_sha = f'{repair_round + 1:x}' * 64
        scope = DesignRepairScope.model_validate({
            'candidate_sha256': candidate_sha,
            'aggregate_subject_sha256': f'{repair_round + 3:x}' * 64,
            'decision_sha256': decision_sha,
            'repair_capability': {
                'repairable': True, 'reason_code': 'ready',
                'chapters': [{
                    'output_ordinal': 1,
                    'committed_rounds': repair_round - 1,
                    'request_repair_available': True,
                }],
            },
            'strategy_replan': False,
            'target_chapters': [1],
            'chapters': [{
                'output_ordinal': 1,
                'subject_sha256': f'{repair_round + 5:x}' * 64,
                'constraints': [_scene_goal_constraint(
                    _candidate_at(book, parent_ctx))],
            }],
        })
        repair_ctx = _ctx(book, 'stage4.design')

        def factory(_purpose: str, chapter: int | None) -> _RequestDriver:
            assert chapter == 1

            def respond(request: AgentRequest) -> dict[str, Any]:
                lines = request.prompt.splitlines()
                marker = lines.index(plan.prompts.BLOCK_PREVIOUS_CHAPTER_RESPONSE)
                return json.loads(lines[marker + 1])['response']

            return _RequestDriver(
                respond,
                session_id=f'provider-targeted-round-{repair_round}',
            )

        pool = Stage4DesignSessionPool(
            repair_ctx, driver_factory=factory, max_turns=2)
        current = plan.run_design(
            repair_ctx,
            pool,
            repair_decision_sha256=decision_sha,
            repair_scope=scope,
        )
        pool.finish_all(delivered=True)
        pool.close()
        capability = stage4_runs.inspect_design_repair_capability(
            repair_ctx,
            candidate_payload=current.model_dump(by_alias=True, mode='json'),
            candidate_file_sha256=hashlib.sha256(
                (book / STAGE4_BLIND_CANDIDATE).read_bytes()).hexdigest(),
        )
        counts = dict(capability.chapter_repair_rounds)
        assert capability.repairable is True
        assert capability.parent_run_id == repair_ctx.run_id
        assert counts[1] == repair_round
        assert counts.get(2, 0) == 0

    third_sha = hashlib.sha256(
        (book / STAGE4_BLIND_CANDIDATE).read_bytes()).hexdigest()
    third_scope = DesignRepairScope.model_validate({
        'candidate_sha256': third_sha,
        'aggregate_subject_sha256': '8' * 64,
        'decision_sha256': '9' * 64,
        # v3 的 committed_rounds 只是审计读数；新的 hash-bound typed
        # decision 打开同 strategy epoch 的新 repair epoch，不得自动升级为整书 replan。
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
            'subject_sha256': 'a' * 64,
            'constraints': [_scene_goal_constraint(
                _candidate_at(book, parent_ctx))],
        }],
    })
    third_ctx = _ctx(book, 'stage4.design')
    def third_factory(_purpose: str, chapter: int | None) -> _RequestDriver:
        assert chapter == 1

        def respond(request: AgentRequest) -> dict[str, Any]:
            lines = request.prompt.splitlines()
            marker = lines.index(plan.prompts.BLOCK_PREVIOUS_CHAPTER_RESPONSE)
            return json.loads(lines[marker + 1])['response']

        return _RequestDriver(respond, session_id='provider-targeted-new-epoch')

    third_pool = Stage4DesignSessionPool(
        third_ctx, driver_factory=third_factory, max_turns=2)
    third = plan.run_design(
        third_ctx,
        third_pool,
        repair_decision_sha256=third_scope.decision_sha256,
        repair_scope=third_scope,
    )
    third_pool.finish_all(delivered=True)
    third_pool.close()
    capability = stage4_runs.inspect_design_repair_capability(
        third_ctx,
        candidate_payload=third.model_dump(by_alias=True, mode='json'),
        candidate_file_sha256=hashlib.sha256(
            (book / STAGE4_BLIND_CANDIDATE).read_bytes()).hexdigest(),
    )
    assert dict(capability.chapter_repair_rounds)[1] == 3
    assert paths.stage4_run_path(
        book, third_ctx.run_id, 'stage4.design').exists()


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


def test_plan_false_infeasible_is_repaired_to_bound_in_same_session(
        tmp_path: Path, monkeypatch,
) -> None:
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    _design_ctx, candidate = _land_design(
        book, blueprints.model_dump(by_alias=True))
    plan_ctx = _ctx(book, 'stage4.plan')
    client, driver = _plan_pool(
        plan_ctx, monkeypatch, first_response=_infeasible_b_response(candidate),
        session_id='provider-plan-one-session', max_turns=2)
    report = plan.run_plan(plan_ctx, client)
    client.finish(process_state='ok', delivery_state='delivered')
    client.close()

    assert report.passed
    store = ArtifactStore(plan_ctx)
    landed = store.read_own(EvidenceBinding, STAGE4_EVIDENCE_BINDING)[0]
    assert landed.status == 'bound'
    assert store.exists(STAGE4_PLAN_CHAPTERS)
    assert not store.exists(STAGE4_WRITER_CONTEXT)
    assert stage4_runs.committed(
        plan_ctx, stage4_runs.plan_chapter_commit_spec(1)) is not None
    assert driver.calls == 2
    assert len(driver.requests) == 2
    assert driver.requests[0].system is not None
    assert driver.requests[1].system is None
    # 章级协议不接受整书 infeasible；明确 schema 反馈回同一章 Executor 自修。
    assert 'schema' in driver.requests[1].prompt
    plan_checkpoint_path = (
        paths.stage4_plan_chapter_checkpoint_dir(book, plan_ctx.run_id, 1)
        / 'rev-0001.json')
    plan_checkpoint = json.loads(plan_checkpoint_path.read_text(encoding='utf-8'))
    assert plan_checkpoint['provider_session_id'] == 'provider-plan-one-session'
    assert plan_checkpoint['accepted_turn_index'] == 2
    assert len(plan_checkpoint['attempt_links']) == 2


def test_plan_code_owned_hard_commits_binding_and_invalid_then_recovers_zero_wire(
        tmp_path: Path, monkeypatch,
) -> None:
    """code-owner HARD 立即封存 INVALID；章 checkpoint 恢复保留旧正式 bundle。"""
    import pytest

    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    _design_ctx, candidate = _land_design(
        book, blueprints.model_dump(by_alias=True))

    # Plan 落盘后由独立 context producer 组装一次；随后 HARD
    # run 必须逐字保留这两份下游当前仍消费的字节。
    clean_ctx = _ctx(book, 'stage4.plan')
    clean_client = Stage4PlanSessionPool(clean_ctx)
    assert plan.run_plan(clean_ctx, clean_client).passed
    clean_client.finish(process_state='ok', delivery_state='delivered')
    clean_client.close()
    plan.run_context(_ctx(book, 'stage4.context'))
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
    hard_client, hard_driver = _plan_pool(
        hard_ctx, monkeypatch, session_id='provider-plan-hard')
    real_evaluate = plan.evaluate_plan_candidate
    real_verdict = plan._candidate_bundle_verdict
    post_model = False

    def evaluate(*args, **kwargs):
        nonlocal post_model
        post_model = True
        return real_evaluate(*args, **kwargs)

    def code_failure(data, up, *, witness=None):
        hits, feedback = real_verdict(data, up, witness=witness)
        if not post_model:
            return hits, feedback
        if witness is not None:
            witness.add(code='phase_artifact_link', rule='phase_artifact_link')
        return (*hits, 'phase_artifact_link'), (*feedback, {'rule': 'phase_artifact_link'})

    monkeypatch.setattr(plan, 'evaluate_plan_candidate', evaluate)
    monkeypatch.setattr(plan, '_candidate_bundle_verdict', code_failure)
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

    assert hard_driver.calls == 1
    assert len(hard_driver.requests) == 1
    checkpoint_dir = paths.stage4_plan_chapter_checkpoint_dir(
        book, hard_ctx.run_id, 1)
    assert [path.name for path in checkpoint_dir.glob('*.json')] == [
        'rev-0001.json']
    latest_checkpoint = json.loads((checkpoint_dir / 'rev-0001.json').read_text(
        encoding='utf-8'))
    assert _session_record(
        book, latest_checkpoint['session_run_id']).end is None
    assert (book / plan._INVALID).read_bytes() == prior_invalid
    commit_spec = stage4_runs.plan_chapter_commit_spec(1)
    assert stage4_runs.committed(hard_ctx, commit_spec) is None

    store = ArtifactStore(hard_ctx)
    binding = store.read_own(EvidenceBinding, STAGE4_EVIDENCE_BINDING)[0]
    assert binding.status == 'bound'
    assert (book / STAGE4_PLAN_CHAPTERS).read_bytes() == plan_before
    assert (book / STAGE4_WRITER_CONTEXT).read_bytes() == context_before

    restarted = _ctx(
        book, 'stage4.plan', run_id=hard_ctx.run_id, code=hard_ctx.code)
    post_model = False
    from tests.test_stage4_plan_chapters import _DeliveryRecoveryDriver
    # 章池预建时校验本地 driver 隔离；原回答恢复不得 start/resume。
    forbidden = _DeliveryRecoveryDriver(1, outcome='forbid')
    no_wire = Stage4PlanSessionPool(
        restarted, driver_factory=lambda _purpose, _chapter: forbidden)
    with pytest.raises(GateBlocked, match='phase_artifact_link'):
        plan.run_plan(restarted, no_wire)
    no_wire.close()
    invalid = ArtifactStore(restarted).read_own(
        PlanChapters, plan._INVALID)[0]
    assert invalid.output_chapters
    assert forbidden.calls == 0
    committed = stage4_runs.committed(restarted, commit_spec)
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
