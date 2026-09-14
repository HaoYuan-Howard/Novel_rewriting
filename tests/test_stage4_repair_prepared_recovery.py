#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage4 repair/replan aggregate prepared 跨 run 0-wire 补尾。"""
from __future__ import annotations

import hashlib
import json
import copy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from contracts.artifacts import STAGE4_BLIND_CANDIDATE
from contracts.schema.orchestration import (
    AgentDeliveryOutcome, AgentRunEvent, AgentRunStart, ExecutionEpoch,
    PipelineAgentProfiles, RunContractV2,
)
from contracts.schema.stage4 import BlindCandidate
from novel import paths, prov
from novel.agents import session_audit as agent_session_audit
from novel.agents.session_client import SESSION_PROTOCOL_VERSION
from novel.errors import ArtifactUnreadable, ProvenanceRefused
from novel.llm import agent_runs, stage4_runs
from novel.llm.cache import CachePolicy
from novel.llm.ledger import CostLedger, ledger_path
from novel.run import RunContext
from novel.node_agents import Stage4DesignSessionPool
from novel.orchestrator import agent
from novel.stages.stage4 import plan
from novel.store import ArtifactStore
from tests.test_stage4_agent_boundary import (
    _NoWireDriver, _RequestDriver, _book, _ctx, _land_design,
    _leave_interrupted_design_repair_checkpoint,
    _scene_goal_constraint, _seed_current_stage2,
    _single_chapter_repair_scope,
)
from tests.test_stage4_runtime_v3 import (
    _current_design_contexts, _v3_capability,
)
from tests.test_stage4_v3_integration import _split_a_responder


@pytest.fixture(autouse=True)
def _stable_code_version(monkeypatch) -> None:
    monkeypatch.setattr(
        prov, 'code_version', lambda: 'test-stage4-repair-tail')


def _free_ctx(
        book: Path, run_id: str, *, parent_run_id: str | None = None,
) -> RunContext:
    ledger = CostLedger(
        path=ledger_path(book.parent), run_id=run_id,
        parent_run_id=parent_run_id or run_id)
    return RunContext(
        novel_dir=book, node='stage4.design', paid=False,
        code=prov.code_version(), cache=CachePolicy.default(), ledger=ledger,
        run_id=run_id, parent_run_id=parent_run_id or run_id)


def _leave_targeted_prepared(
        book: Path, monkeypatch,
) -> tuple[RunContext, BlindCandidate, dict[str, Any], Path]:
    blueprints = _seed_current_stage2(book)
    _parent_ctx, parent = _land_design(
        book, blueprints.model_dump(by_alias=True, mode='json'))
    candidate = book / STAGE4_BLIND_CANDIDATE
    scope = plan.DesignRepairScope.model_validate({
        'candidate_sha256': hashlib.sha256(candidate.read_bytes()).hexdigest(),
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
    source = _ctx(book, 'stage4.design')

    def factory(_purpose: str, chapter: int | None) -> _RequestDriver:
        assert chapter == 1

        def respond(request) -> dict[str, Any]:
            lines = request.prompt.splitlines()
            marker = lines.index(plan.prompts.BLOCK_PREVIOUS_CHAPTER_RESPONSE)
            return json.loads(lines[marker + 1])['response']

        return _RequestDriver(respond, session_id='provider-repair-tail-ch1')

    pool = Stage4DesignSessionPool(
        source, driver_factory=factory, max_turns=2)
    original_write = ArtifactStore.write

    def crash_before_candidate_write(self, rel, artifact):
        if rel == STAGE4_BLIND_CANDIDATE:
            journal = stage4_runs.design_repair_aggregate_state(source)
            assert journal is not None and journal['state'] == 'prepared'
            raise RuntimeError('模拟 repair aggregate prepared 后硬杀')
        return original_write(self, rel, artifact)

    monkeypatch.setattr(ArtifactStore, 'write', crash_before_candidate_write)
    with pytest.raises(RuntimeError, match='prepared'):
        plan.run_design(
            source, pool,
            repair_decision_sha256=scope.decision_sha256,
            repair_scope=scope)
    pool.close()
    monkeypatch.setattr(ArtifactStore, 'write', original_write)

    target = (
        paths.stage4_run_path(book, source.run_id, 'stage4.design')
        / stage4_runs.DESIGN_REPAIR_AGGREGATE_COMMIT_FILE)
    journal = json.loads(target.read_text(encoding='utf-8'))
    assert journal['state'] == 'prepared'
    assert (book / STAGE4_BLIND_CANDIDATE).read_bytes() \
        != ArtifactStore(source)._render(
            STAGE4_BLIND_CANDIDATE,
            BlindCandidate.model_validate(journal['primary_payload'], strict=True))
    return source, parent, journal, target


def _recover(
        ctx: RunContext, source: RunContext, journal: dict[str, Any],
        journal_path: Path,
) -> BlindCandidate:
    return plan.run_design_repair_aggregate_recovery(
        ctx, source_run_id=source.run_id,
        source_journal_sha256=hashlib.sha256(
            journal_path.read_bytes()).hexdigest(),
        source_commit_sha256=str(journal['commit_sha256']))


def _leave_replan_prepared(
        book: Path, monkeypatch,
) -> tuple[RunContext, dict[str, Any], Path]:
    blueprints = _seed_current_stage2(book)
    blueprint_raw = blueprints.model_dump(by_alias=True, mode='json')
    _parent_ctx, parent = _land_design(book, blueprint_raw)
    source = _ctx(book, 'stage4.design')
    _prepared, contexts = _current_design_contexts(source, parent)
    epoch = contexts[0].strategy_epoch_sha256
    old_strategy_subject = plan._canonical_sha256({
        'reconstruction_strategy': parent.reconstruction_strategy,
        'chapter_weights': [{
            'chapter_ordinal': row.output_ordinal, 'weight': row.weight,
        } for row in parent.output_chapters],
    })
    constraint = {
        'kind': 'change_chapter_weights',
        'subject_sha256': old_strategy_subject,
        'chapter_ordinals': [2],
    }
    feedback_sha = plan._canonical_sha256({
        'kind': 'stage4-design-strategy-feedback-set-v1',
        'strategy_epoch_sha256': epoch,
        'constraints': [constraint],
    })
    scope = plan.DesignRepairScope.model_validate({
        'candidate_sha256': hashlib.sha256(
            (book / STAGE4_BLIND_CANDIDATE).read_bytes()).hexdigest(),
        'aggregate_subject_sha256': 'd' * 64,
        'decision_sha256': 'e' * 64,
        'repair_capability': _v3_capability(parent),
        'strategy_epoch_sha256': epoch,
        'strategy_replan': True,
        'replan_feedback': {
            'new_constraints': [constraint],
            'accumulated_constraints': [constraint],
            'constraint_set_sha256': feedback_sha,
            'accepted_chapters': [{
                'output_ordinal': row.chapter_ordinal,
                'subject_sha256': f'{row.chapter_ordinal:x}' * 64,
                'chapter_strategy_sha256': row.chapter_strategy_sha256,
            } for row in contexts if row.chapter_ordinal != 2],
            'chapter_failures': [],
        },
        'target_chapters': [],
        'chapters': [],
    })
    split = _split_a_responder(blueprint_raw)

    def factory(_purpose: str, chapter: int | None) -> _RequestDriver:
        def respond(request) -> dict[str, Any]:
            if chapter is None:
                lines = request.prompt.splitlines()
                marker = '【TYPED_REPLAN_FEEDBACK_JSON】'
                raw = json.loads(lines[lines.index(marker) + 1])
                replacement = copy.deepcopy(raw['previous_strategy'])
                row = next(item for item in replacement['chapter_weights']
                           if item['chapter_ordinal'] == 2)
                row['weight'] = (
                    'CH_MAJOR' if row['weight'] != 'CH_MAJOR' else 'CH_BELOW')
                return replacement
            return split(SimpleNamespace(
                system=request.system, user=request.prompt))

        return _RequestDriver(
            respond,
            session_id=f'provider-replan-tail-{chapter or "strategy"}')

    pool = Stage4DesignSessionPool(source, driver_factory=factory)
    original_write = ArtifactStore.write

    def crash_before_candidate_write(self, rel, artifact):
        if rel == STAGE4_BLIND_CANDIDATE:
            journal = stage4_runs.design_repair_aggregate_state(source)
            assert journal is not None and journal['state'] == 'prepared'
            raise RuntimeError('模拟 replan aggregate prepared 后硬杀')
        return original_write(self, rel, artifact)

    monkeypatch.setattr(ArtifactStore, 'write', crash_before_candidate_write)
    with pytest.raises(RuntimeError, match='prepared'):
        plan.run_design(source, pool, repair_scope=scope)
    pool.close()
    monkeypatch.setattr(ArtifactStore, 'write', original_write)
    target = (
        paths.stage4_run_path(book, source.run_id, 'stage4.design')
        / stage4_runs.DESIGN_REPAIR_AGGREGATE_COMMIT_FILE)
    journal = json.loads(target.read_text(encoding='utf-8'))
    assert journal['state'] == 'prepared'
    return source, journal, target


def _audit_rows(book: Path) -> list[dict[str, Any]]:
    target = ledger_path(book.parent).with_name('llm_audit.jsonl')
    if not target.is_file():
        return []
    return [json.loads(line) for line in target.read_text(
        encoding='utf-8').splitlines() if line.strip()]


def _append_target_node_facts(
        book: Path, target: RunContext, *, node: str,
) -> None:
    """写一组能通过现役 ledger/session wire schema 的目标 run 事实。"""
    chapter = 1 if node == 'stage4.design' else None
    purpose = (
        stage4_runs.design_unit_purpose(chapter)
        if node == 'stage4.design' else 'stage4.plan')
    attempt_id = (
        'd15ea5ed00000001' if node == 'stage4.design'
        else 'b1ad000000000001')
    target_ledger = CostLedger(
        path=ledger_path(book.parent), run_id=target.run_id,
        parent_run_id=target.parent_run_id)
    target_ledger.record(
        node=node, sid=target.sid, model='gpt-5.6-sol', code=target.code,
        cached=False, cache_policy='default', prompt_tokens=1,
        completion_tokens=1, reasoning_tokens=1, purpose=purpose,
        reasoning_effort='high', thinking=None, json_mode=True,
        max_tokens=128, attempt=1, max_attempts=1, latency_ms=1,
        attempt_id=attempt_id,
        request_fingerprint=('d' if node == 'stage4.design' else 'b') * 64,
        response_sha256=('e' if node == 'stage4.design' else 'c') * 64,
        request_model='gpt-5.6-sol')
    profile = _ctx(book, node).executor_profile
    assert profile is not None
    agent_session_audit.append_event(
        book.parent,
        agent_session_audit.AgentSessionEvent(
            session_run_id=f'{node.replace(".", "-")}-target-session',
            parent_run_id=target.run_id, role='executor', node=node,
            chapter=chapter, subject_sha256='f' * 64, profile=profile,
            kind='started', ts=10, requested_model=profile.model,
            requested_effort=profile.reasoning_effort,
            cli_version='test-cli/1',
            protocol_version=SESSION_PROTOCOL_VERSION,
            start_code_version=target.code, skill_sha256='a' * 64,
            prompt_sha256='b' * 64, schema_sha256='c' * 64))


def _reseal_prepared_journal(
        journal: dict[str, Any], target: Path,
) -> None:
    journal['primary_payload_sha256'] = agent_runs.canonical_sha256(
        journal['primary_payload'])
    journal['primary_payload_fingerprint'] = prov.projection_fingerprint(
        journal['primary_payload'])
    journal['finalization_payload']['candidate_sha256'] = \
        journal['primary_payload_sha256']
    journal['finalization_sha256'] = agent_runs.canonical_sha256(
        journal['finalization_payload'])
    journal['commit_sha256'] = \
        stage4_runs._design_repair_aggregate_hash(journal)
    agent_runs._atomic_json(target, journal)


def _mutate_repair_source_checkpoint(
        book: Path, source: RunContext, journal: dict[str, Any],
        journal_path: Path, *, mutation: str,
) -> None:
    source_row = next(row for row in journal['sources']
                      if row['source_kind'] == 'repair'
                      and row['origin'] == 'agent')
    checkpoint_path = (
        paths.stage4_design_unit_checkpoint_dir(
            book, source.run_id, source_row['chapter'])
        / f"rev-{source_row['checkpoint_revision']:04d}.json")
    checkpoint = json.loads(checkpoint_path.read_text(encoding='utf-8'))
    if mutation == 'revision_key':
        checkpoint['revision_key'] = 'forged-terminal'
    elif mutation == 'state_payload':
        checkpoint['state_payload'] = {
            **checkpoint['state_payload'], 'forged': True}
        checkpoint['state_payload_sha256'] = agent_runs.canonical_sha256(
            checkpoint['state_payload'])
    else:  # pragma: no cover -- 参数化闭集
        raise AssertionError(mutation)
    checkpoint['checkpoint_sha256'] = agent_runs._checkpoint_hash(checkpoint)
    agent_runs._atomic_json(checkpoint_path, checkpoint)
    source_row['checkpoint_sha256'] = checkpoint['checkpoint_sha256']
    source_row['checkpoint_file_sha256'] = agent_runs.canonical_sha256(
        checkpoint_path.read_bytes())
    _reseal_prepared_journal(journal, journal_path)


def _write_target_prepared_envelope(
        book: Path, target: RunContext, kind: str,
) -> Path:
    if kind in {'generic', 'plan_chapter'}:
        path = (
            (paths.stage4_plan_chapter_run_path(
                book, target.run_id, 1)
             if kind == 'plan_chapter' else paths.stage4_run_path(
                book, target.run_id, 'stage4.design'))
            / agent_runs.COMMIT_FILE)
        payload = {key: None for key in agent_runs._COMMIT_FIELDS}
        payload.update({
            'version': agent_runs.VERSION,
            'state': 'prepared', 'prepared_at': '2026-01-01T00:00:00Z',
            'run_id': target.run_id, 'sid': target.sid,
            'node': ('stage4.plan' if kind == 'plan_chapter'
                     else 'stage4.design'),
            'code': target.code,
        })
        payload['commit_sha256'] = agent_runs._commit_hash(payload)
    elif kind in {'initial', 'retired_initial'}:
        path = (
            paths.stage4_run_path(
                book, target.run_id, 'stage4.design')
            / stage4_runs.DESIGN_AGGREGATE_COMMIT_FILE)
        if kind == 'initial':
            # 占用检查必须使用当前 producer 已封存的完整字节与来源。
            # 这里只注入另一个 run 的 prepared 保留窗，不赋予提交权限。
            sources = list(paths.stage4_run_path(
                book, target.run_id, 'stage4.design').parent.parent.glob(
                    '*/design/' + stage4_runs.DESIGN_AGGREGATE_COMMIT_FILE))
            assert len(sources) == 1
            payload = json.loads(sources[0].read_text(encoding='utf-8'))
            payload.update({
                'producer_parent_run_id': target.parent_run_id,
                'recovery': None, 'committed_at': None,
                'landed_payload_sha256s': None, 'post_file_sha256s': None,
            })
        else:
            payload = {
                key: None for key in stage4_runs._DESIGN_AGGREGATE_FIELDS}
            payload.update({
                'version': 1, 'kind': 'stage4-design-aggregate/v1',
            })
        payload.update({
            'state': 'prepared', 'prepared_at': '2026-01-01T00:00:00Z',
            'run_id': target.run_id, 'sid': target.sid,
            'node': 'stage4.design', 'code': target.code,
        })
        payload['commit_sha256'] = \
            stage4_runs._design_aggregate_hash(payload)
    elif kind == 'human':
        path = (
            paths.stage4_run_path(
                book, target.run_id, 'stage4.design')
            / stage4_runs.DESIGN_HUMAN_EVOLUTION_COMMIT_FILE)
        payload = {
            key: None for key in stage4_runs._DESIGN_HUMAN_EVOLUTION_FIELDS}
        payload.update({
            'version': stage4_runs._DESIGN_HUMAN_EVOLUTION_VERSION,
            'kind': stage4_runs._DESIGN_HUMAN_EVOLUTION_KIND,
            'state': 'prepared', 'prepared_at': '2026-01-01T00:00:00Z',
            'run_id': target.run_id, 'sid': target.sid,
            'node': 'stage4.design', 'code': target.code,
        })
        payload['commit_sha256'] = \
            stage4_runs._design_human_evolution_hash(payload)
    else:  # pragma: no cover -- 参数化闭集
        raise AssertionError(kind)
    agent_runs._atomic_json(path, payload)
    return path


def test_repair_prepared_baseline_recovers_free_without_wire(
        tmp_path: Path, monkeypatch,
) -> None:
    book = _book(tmp_path)
    source, _parent, journal, journal_path = \
        _leave_targeted_prepared(book, monkeypatch)
    target = _free_ctx(book, 'repair-recovery-r1')

    landed = _recover(target, source, journal, journal_path)

    committed = json.loads(journal_path.read_text(encoding='utf-8'))
    assert committed['state'] == 'committed'
    assert committed['recovery']['target_run_id'] == target.run_id
    assert committed['recovery']['target_parent_run_id'] == target.parent_run_id
    assert ArtifactStore(target).read(
        BlindCandidate, STAGE4_BLIND_CANDIDATE) == landed
    cost = [json.loads(line) for line in ledger_path(book.parent).read_text(
        encoding='utf-8').splitlines() if line.strip()]
    assert all(row.get('run_id') != target.run_id for row in cost)
    assert all(event.parent_run_id != target.run_id for event in
               agent_session_audit.read_events(book.parent))
    kinds = [row.get('kind') for row in _audit_rows(book)]
    assert 'stage4_repair_aggregate_recovery' in kinds
    assert 'stage4_repair_aggregate_recovery_attempt' in kinds


def test_replan_prepared_baseline_recovers_through_same_zero_wire_path(
        tmp_path: Path, monkeypatch,
) -> None:
    book = _book(tmp_path)
    source, journal, journal_path = _leave_replan_prepared(book, monkeypatch)
    target = _free_ctx(book, 'replan-recovery-r1')

    landed = _recover(target, source, journal, journal_path)

    committed = json.loads(journal_path.read_text(encoding='utf-8'))
    assert committed['state'] == 'committed'
    assert committed['recovery']['target_run_id'] == target.run_id
    assert landed.provenance.architecture == 'book_strategy_replan_v1'
    assert target.ledger.calls_made == target.ledger.calls_cached == 0
    assert all(event.parent_run_id != target.run_id for event in
               agent_session_audit.read_events(book.parent))


def test_repair_prepared_r1_audit_crash_r2_records_actual_committer(
        tmp_path: Path, monkeypatch,
) -> None:
    book = _book(tmp_path)
    source, _parent, journal, journal_path = \
        _leave_targeted_prepared(book, monkeypatch)
    r1 = _free_ctx(book, 'repair-recovery-r1')
    original_mark = stage4_runs.mark_design_repair_aggregate_committed

    def crash_after_audit(*args, **kwargs):
        rows = _audit_rows(book)
        assert any(row.get('target_run_id') == r1.run_id for row in rows)
        raise RuntimeError('模拟 recovery audit 后硬杀')

    monkeypatch.setattr(
        stage4_runs, 'mark_design_repair_aggregate_committed',
        crash_after_audit)
    with pytest.raises(RuntimeError, match='audit'):
        _recover(r1, source, journal, journal_path)
    monkeypatch.setattr(
        stage4_runs, 'mark_design_repair_aggregate_committed', original_mark)

    r2 = _free_ctx(book, 'repair-recovery-r2')
    _recover(r2, source, journal, journal_path)

    committed = json.loads(journal_path.read_text(encoding='utf-8'))
    assert committed['recovery']['target_run_id'] == r2.run_id
    attempts = [row for row in _audit_rows(book)
                if row.get('kind')
                == 'stage4_repair_aggregate_recovery_attempt']
    assert {row['target_run_id'] for row in attempts} == {r1.run_id, r2.run_id}
    assert len({row['stage4_repair_recovery_id'] for row in attempts}) == 1


def test_repair_prepared_rebuild_refuses_self_rehashed_business_drift(
        tmp_path: Path, monkeypatch,
) -> None:
    book = _book(tmp_path)
    source, _parent, journal, journal_path = \
        _leave_targeted_prepared(book, monkeypatch)
    before_candidate = (book / STAGE4_BLIND_CANDIDATE).read_bytes()
    before_audit = _audit_rows(book)

    journal['primary_payload']['reconstruction_strategy'] += '（伪造漂移）'
    _reseal_prepared_journal(journal, journal_path)
    target = _free_ctx(book, 'repair-recovery-r1')

    with pytest.raises(ProvenanceRefused, match='逐字重建'):
        _recover(target, source, journal, journal_path)

    assert (book / STAGE4_BLIND_CANDIDATE).read_bytes() == before_candidate
    assert _audit_rows(book) == before_audit
    assert json.loads(journal_path.read_text(encoding='utf-8'))['state'] \
        == 'prepared'


@pytest.mark.parametrize('mode', ['targeted', 'replan'])
def test_repair_prepared_rebuild_refuses_self_rehashed_usage_drift(
        tmp_path: Path, monkeypatch, mode: str,
) -> None:
    book = _book(tmp_path)
    if mode == 'targeted':
        source, _parent, journal, journal_path = \
            _leave_targeted_prepared(book, monkeypatch)
    else:
        source, journal, journal_path = \
            _leave_replan_prepared(book, monkeypatch)
    before_candidate = (book / STAGE4_BLIND_CANDIDATE).read_bytes()
    before_audit = _audit_rows(book)
    usage = journal['primary_payload']['_provenance']['usage']
    usage['calls_made'] += 1
    _reseal_prepared_journal(journal, journal_path)
    target = _free_ctx(book, f'{mode}-provenance-recovery')

    with pytest.raises(ProvenanceRefused, match='sealed provenance'):
        _recover(target, source, journal, journal_path)

    assert (book / STAGE4_BLIND_CANDIDATE).read_bytes() == before_candidate
    assert _audit_rows(book) == before_audit
    assert json.loads(journal_path.read_text(encoding='utf-8'))['state'] \
        == 'prepared'


@pytest.mark.parametrize(
    'foreign_kind', ['generic', 'initial', 'retired_initial', 'human', 'plan_chapter'])
def test_repair_recovery_refuses_target_run_prepared_reservation_before_write(
        tmp_path: Path, monkeypatch, foreign_kind: str,
) -> None:
    book = _book(tmp_path)
    source, _parent, journal, journal_path = \
        _leave_targeted_prepared(book, monkeypatch)
    before_candidate = (book / STAGE4_BLIND_CANDIDATE).read_bytes()
    before_audit = _audit_rows(book)
    target = _free_ctx(book, f'recovery-with-{foreign_kind}-tail')
    _write_target_prepared_envelope(book, target, foreign_kind)

    error, message = (
        (ArtifactUnreadable, '身份/哈希不闭合')
        if foreign_kind == 'retired_initial'
        else (ProvenanceRefused, '另一份 prepared'))
    with pytest.raises(error, match=message):
        _recover(target, source, journal, journal_path)

    assert (book / STAGE4_BLIND_CANDIDATE).read_bytes() == before_candidate
    assert _audit_rows(book) == before_audit
    assert json.loads(journal_path.read_text(encoding='utf-8'))['state'] \
        == 'prepared'


@pytest.mark.parametrize('missing_kind', ['canonical', 'attempt', 'adoption'])
def test_recovered_candidate_real_plan_preflight_requires_exact_audit_lineage(
        tmp_path: Path, monkeypatch, missing_kind: str,
) -> None:
    book = _book(tmp_path)
    source, _parent, journal, journal_path = \
        _leave_targeted_prepared(book, monkeypatch)
    target = _free_ctx(book, 'repair-recovery-r1')
    landed = _recover(target, source, journal, journal_path)
    committed = json.loads(journal_path.read_text(encoding='utf-8'))
    audit_path = ledger_path(book.parent).with_name('llm_audit.jsonl')
    rows = _audit_rows(book)
    if missing_kind == 'canonical':
        remove = next(index for index, row in enumerate(rows)
                      if row.get('kind')
                      == 'stage4_repair_aggregate_recovery')
    elif missing_kind == 'attempt':
        remove = next(index for index, row in enumerate(rows)
                      if row.get('kind')
                      == 'stage4_repair_aggregate_recovery_attempt'
                      and row.get('target_run_id') == target.run_id)
    else:
        remove = next(index for index, row in enumerate(rows)
                      if row.get('kind') == 'artifact_adoption'
                      and row.get('run_id') == source.run_id)
    rows.pop(remove)
    audit_path.write_text(
        ''.join(json.dumps(row, ensure_ascii=False, sort_keys=True)
                + '\n' for row in rows), encoding='utf-8')

    before_candidate = (book / STAGE4_BLIND_CANDIDATE).read_bytes()
    plan_ctx = _ctx(book, 'stage4.plan', run_id=target.run_id)
    monkeypatch.setattr(
        stage4_runs, 'ensure_run',
        lambda *args, **kwargs: pytest.fail(
            'audit lineage 拒绝必须早于 stage4.plan ensure_run'))
    with pytest.raises(ProvenanceRefused, match='audit|adoption|committed'):
        plan.prepare_plan_candidate(plan_ctx)

    assert json.loads(journal_path.read_text(encoding='utf-8')) == committed
    assert (book / STAGE4_BLIND_CANDIDATE).read_bytes() == before_candidate
    assert ArtifactStore(target).read(
        BlindCandidate, STAGE4_BLIND_CANDIDATE) == landed


def test_recovered_candidate_allows_same_run_plan_facts_but_not_design_wire(
        tmp_path: Path, monkeypatch,
) -> None:
    book = _book(tmp_path)
    source, _parent, journal, journal_path = \
        _leave_targeted_prepared(book, monkeypatch)
    target = _free_ctx(book, 'repair-recovery-r1')
    _recover(target, source, journal, journal_path)
    _append_target_node_facts(book, target, node='stage4.plan')
    plan_ctx = _ctx(book, 'stage4.plan', run_id=target.run_id)
    ensured: list[str] = []
    monkeypatch.setattr(
        stage4_runs, 'ensure_run',
        lambda ctx, **_kwargs: ensured.append(ctx.node))

    plan.prepare_plan_candidate(plan_ctx)

    assert ensured == ['stage4.plan']

    _append_target_node_facts(book, target, node='stage4.design')
    ensured.clear()
    with pytest.raises(ProvenanceRefused, match='target.*ledger|0-wire'):
        plan.prepare_plan_candidate(plan_ctx)
    assert ensured == []


def test_repair_recovery_discovery_refuses_service_legacy_duplicate_journal(
        tmp_path: Path, monkeypatch,
) -> None:
    book = _book(tmp_path)
    source, _parent, journal, journal_path = \
        _leave_targeted_prepared(book, monkeypatch)
    before_candidate = (book / STAGE4_BLIND_CANDIDATE).read_bytes()
    before_audit = _audit_rows(book)
    legacy = (
        paths.legacy_stage4_run_path(
            book, source.run_id, 'stage4.design')
        / stage4_runs.DESIGN_REPAIR_AGGREGATE_COMMIT_FILE)
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(journal_path.read_bytes())
    target = _free_ctx(book, 'repair-shadow-recovery')

    with pytest.raises(ProvenanceRefused, match='旧运行目录已退役'):
        _recover(target, source, journal, journal_path)

    assert (book / STAGE4_BLIND_CANDIDATE).read_bytes() == before_candidate
    assert _audit_rows(book) == before_audit
    assert json.loads(journal_path.read_text(encoding='utf-8'))['state'] \
        == 'prepared'


def test_all_historical_repair_prepared_recovers_without_source_adoption(
        tmp_path: Path, monkeypatch,
) -> None:
    """本轮 producer 零 wire 时，旧 checkpoint 成本仍进入 provenance。"""
    book = _book(tmp_path)
    blueprints = _seed_current_stage2(book)
    _parent_ctx, parent = _land_design(
        book, blueprints.model_dump(by_alias=True, mode='json'))
    scope = _single_chapter_repair_scope(book, parent)
    historical_ctx, historical_driver = \
        _leave_interrupted_design_repair_checkpoint(book, scope)
    assert historical_driver.calls == 1

    monkeypatch.setattr(prov, 'code_version', lambda: 'historical-tail-next-code')
    source = _ctx(book, 'stage4.design')
    factory_calls: list[tuple[str, int | None]] = []

    def no_wire_factory(purpose: str, chapter: int | None) -> _NoWireDriver:
        factory_calls.append((purpose, chapter))
        raise AssertionError('all-historical repair 不得创建 provider driver')

    pool = Stage4DesignSessionPool(
        source, driver_factory=no_wire_factory, max_turns=2)
    original_write = ArtifactStore.write

    def crash_before_candidate_write(self, rel, artifact):
        if rel == STAGE4_BLIND_CANDIDATE:
            current = stage4_runs.design_repair_aggregate_state(source)
            assert current is not None and current['state'] == 'prepared'
            raise RuntimeError('模拟 all-historical prepared 后硬杀')
        return original_write(self, rel, artifact)

    monkeypatch.setattr(ArtifactStore, 'write', crash_before_candidate_write)
    with pytest.raises(RuntimeError, match='all-historical'):
        plan.run_design(
            source, pool,
            repair_decision_sha256=scope.decision_sha256,
            repair_scope=scope)
    pool.close()
    monkeypatch.setattr(ArtifactStore, 'write', original_write)
    assert factory_calls == []
    journal_path = (
        paths.stage4_run_path(book, source.run_id, 'stage4.design')
        / stage4_runs.DESIGN_REPAIR_AGGREGATE_COMMIT_FILE)
    journal = json.loads(journal_path.read_text(encoding='utf-8'))
    repair_sources = [row for row in journal['sources']
                      if row['source_kind'] == 'repair']
    assert repair_sources and all(
        row['origin'] == 'historical_checkpoint'
        and row['source_run_id'] == historical_ctx.run_id
        for row in repair_sources)

    target = _free_ctx(book, 'all-historical-recovery')
    landed = _recover(target, source, journal, journal_path)

    assert landed.provenance.usage is not None
    assert landed.provenance.usage.calls_made == 1
    assert not [row for row in _audit_rows(book)
                if row.get('kind') == 'artifact_adoption'
                and row.get('run_id') == source.run_id]


def test_repair_recovery_refuses_self_rehashed_source_code_drift_before_write(
        tmp_path: Path, monkeypatch,
) -> None:
    book = _book(tmp_path)
    source, _parent, journal, journal_path = \
        _leave_targeted_prepared(book, monkeypatch)
    before_candidate = (book / STAGE4_BLIND_CANDIDATE).read_bytes()
    before_audit = _audit_rows(book)
    source_row = next(row for row in journal['sources']
                      if row['source_kind'] == 'repair'
                      and row['origin'] == 'agent')
    chapter = source_row['chapter']
    run_path = (
        paths.stage4_design_unit_run_path(book, source.run_id, chapter)
        / agent_runs.RUN_FILE)
    run = json.loads(run_path.read_text(encoding='utf-8'))
    run['code'] = 'forged-source-code'
    identity = {
        key: run[key] for key in agent_runs._RUN_FIELDS
        if key not in ('identity_sha256', 'created_at')}
    run['identity_sha256'] = agent_runs.canonical_sha256(identity)
    agent_runs._atomic_json(run_path, run)
    run_sha = agent_runs.canonical_sha256(run_path.read_bytes())

    checkpoint_path = (
        paths.stage4_design_unit_checkpoint_dir(
            book, source.run_id, chapter)
        / f"rev-{source_row['checkpoint_revision']:04d}.json")
    checkpoint = json.loads(checkpoint_path.read_text(encoding='utf-8'))
    checkpoint['code'] = 'forged-source-code'
    checkpoint['run_sha256'] = run_sha
    checkpoint['checkpoint_sha256'] = \
        agent_runs._checkpoint_hash(checkpoint)
    agent_runs._atomic_json(checkpoint_path, checkpoint)
    source_row['run_sha256'] = run_sha
    source_row['checkpoint_sha256'] = checkpoint['checkpoint_sha256']
    source_row['checkpoint_file_sha256'] = agent_runs.canonical_sha256(
        checkpoint_path.read_bytes())
    _reseal_prepared_journal(journal, journal_path)
    target = _free_ctx(book, 'source-code-drift-recovery')

    with pytest.raises(ProvenanceRefused, match='source run code'):
        _recover(target, source, journal, journal_path)

    assert (book / STAGE4_BLIND_CANDIDATE).read_bytes() == before_candidate
    assert _audit_rows(book) == before_audit


@pytest.mark.parametrize('mutation', ['revision_key', 'state_payload'])
def test_repair_recovery_refuses_self_rehashed_checkpoint_state_before_write(
        tmp_path: Path, monkeypatch, mutation: str,
) -> None:
    book = _book(tmp_path)
    source, _parent, journal, journal_path = \
        _leave_targeted_prepared(book, monkeypatch)
    before_candidate = (book / STAGE4_BLIND_CANDIDATE).read_bytes()
    before_audit = _audit_rows(book)
    _mutate_repair_source_checkpoint(
        book, source, journal, journal_path, mutation=mutation)
    target = _free_ctx(book, f'checkpoint-{mutation}-recovery')

    with pytest.raises(
            ProvenanceRefused, match='checkpoint|terminal|state_payload'):
        _recover(target, source, journal, journal_path)

    assert (book / STAGE4_BLIND_CANDIDATE).read_bytes() == before_candidate
    assert _audit_rows(book) == before_audit
    assert json.loads(journal_path.read_text(encoding='utf-8'))['state'] \
        == 'prepared'


@pytest.mark.parametrize(
    'mutation', ['ledger_code', 'foreign_purpose', 'foreign_session'])
def test_repair_recovery_requires_closed_source_run_surface_before_write(
        tmp_path: Path, monkeypatch, mutation: str,
) -> None:
    book = _book(tmp_path)
    source, _parent, journal, journal_path = \
        _leave_targeted_prepared(book, monkeypatch)
    before_candidate = (book / STAGE4_BLIND_CANDIDATE).read_bytes()
    before_audit = _audit_rows(book)
    source_cost_path = ledger_path(book.parent)
    if mutation in {'ledger_code', 'foreign_purpose'}:
        rows = [json.loads(line) for line in source_cost_path.read_text(
            encoding='utf-8').splitlines() if line.strip()]
        source_index = next(
            index for index, row in enumerate(rows)
            if row.get('run_id') == source.run_id)
        if mutation == 'ledger_code':
            rows[source_index]['code'] = 'forged-ledger-code'
        else:
            forged = dict(rows[source_index])
            forged.update({
                'purpose': 'stage4.design:ch9999', 'chapter': 9999,
                'attempt_id': 'f0fe190000000001',
                'request_fingerprint': '9' * 64,
                'response_sha256': '8' * 64,
            })
            rows.append(forged)
        source_cost_path.write_text(
            ''.join(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n'
                    for row in rows), encoding='utf-8')
    else:
        profile = source.executor_profile
        assert profile is not None
        agent_session_audit.append_event(
            book.parent,
            agent_session_audit.AgentSessionEvent(
                session_run_id='foreign-source-plan-session',
                parent_run_id=source.run_id, role='executor',
                node='stage4.plan', chapter=None,
                subject_sha256='7' * 64, profile=profile,
                kind='started', ts=100,
                requested_model=profile.model,
                requested_effort=profile.reasoning_effort,
                cli_version='test-cli/1',
                protocol_version=SESSION_PROTOCOL_VERSION,
                start_code_version=source.code,
                skill_sha256='6' * 64, prompt_sha256='5' * 64,
                schema_sha256='4' * 64))
    target = _free_ctx(book, f'source-{mutation}-recovery')

    with pytest.raises(ProvenanceRefused, match='source|ledger|session|成本行'):
        _recover(target, source, journal, journal_path)

    assert (book / STAGE4_BLIND_CANDIDATE).read_bytes() == before_candidate
    assert _audit_rows(book) == before_audit
    assert json.loads(journal_path.read_text(encoding='utf-8'))['state'] \
        == 'prepared'


def test_repair_recovery_refuses_foreign_source_adoption_fingerprint(
        tmp_path: Path, monkeypatch,
) -> None:
    book = _book(tmp_path)
    source, _parent, journal, journal_path = \
        _leave_targeted_prepared(book, monkeypatch)
    source_row = next(row for row in journal['sources']
                      if row['source_kind'] == 'repair'
                      and row['origin'] == 'agent')
    source.ledger.note_adoption(
        sid=source.sid, node='stage4.design',
        purpose=source_row['purpose'], attempt_id=source_row['attempt_id'],
        artifact=STAGE4_BLIND_CANDIDATE,
        artifact_fingerprint='0' * 10,
        chapter=source_row['chapter'])
    before_candidate = (book / STAGE4_BLIND_CANDIDATE).read_bytes()
    before_audit = _audit_rows(book)
    target = _free_ctx(book, 'foreign-adoption-recovery')

    with pytest.raises(ProvenanceRefused, match='adoption'):
        _recover(target, source, journal, journal_path)

    assert (book / STAGE4_BLIND_CANDIDATE).read_bytes() == before_candidate
    assert _audit_rows(book) == before_audit


def test_repair_recovery_refuses_unknown_drained_agent_source_epoch(
        tmp_path: Path, monkeypatch,
) -> None:
    book = _book(tmp_path)
    source, _parent, journal, journal_path = \
        _leave_targeted_prepared(book, monkeypatch)
    before_candidate = (book / STAGE4_BLIND_CANDIDATE).read_bytes()
    before_audit = _audit_rows(book)
    parent_run = 'outer-agent-owner'
    journal['producer_parent_run_id'] = parent_run
    _reseal_prepared_journal(journal, journal_path)
    profile = source.executor_profile
    assert profile is not None
    profiles = PipelineAgentProfiles(
        commander=profile, executor_default=profile,
        executor_overrides={})
    start = AgentRunStart(
        sid=source.sid, base=str(book.parent.resolve()),
        argv=['commander', '-'], prompt='测试', authorized=True,
        egress_authorized=True, code_version=source.code,
        skill_sha256='a' * 64, profiles=profiles)
    contract = RunContractV2(
        sid=source.sid, base=str(book.parent.resolve()),
        authorized=True, egress_authorized=True, profiles=profiles)
    epoch = ExecutionEpoch(
        epoch_id='source-epoch', ordinal=1, reason='initial',
        argv=['commander', '-'], prompt_sha256='b' * 64,
        protocol_version='test-protocol', cli_version='test-cli',
        code_version=source.code, skill_sha256='a' * 64,
        session_run_id=source.run_id)
    events = (
        AgentRunEvent(
            wire_version=2, run_id=parent_run, kind='started', ts=1,
            contract=contract, start=start),
        AgentRunEvent(
            wire_version=2, run_id=parent_run, kind='epoch_opened', ts=2,
            epoch=epoch),
        AgentRunEvent(
            wire_version=2, run_id=parent_run, kind='epoch_closed', ts=3,
            epoch_id=epoch.epoch_id, exit_code=70,
            drain_state='unknown'),
        AgentRunEvent(
            wire_version=2, run_id=parent_run, kind='ended', ts=4,
            exit_code=70,
            outcome=AgentDeliveryOutcome(
                state='unknown', reason='未证明子执行清空', checked_at=4)),
    )
    for event in events:
        agent.append(book.parent, event)
    target = _free_ctx(book, 'unknown-drain-recovery')

    with pytest.raises(ProvenanceRefused, match='终态链不闭合'):
        _recover(target, source, journal, journal_path)

    assert (book / STAGE4_BLIND_CANDIDATE).read_bytes() == before_candidate
    assert _audit_rows(book) == before_audit
