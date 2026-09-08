"""真实 legacy 双 judge 的并发、回答恢复与原序号/双采纳断点。"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from contracts.schema.stage6 import HookSemanticResponse
from novel.errors import ProvenanceRefused, TerminalState
from novel.llm.cache import CachePolicy, ResponseCache, cache_key
from novel.llm.client import LlmClient
from novel.llm.ledger import CostLedger, audit_path, ledger_path, read_jsonl_rows
from novel.llm.transport import FakeTransport
from novel.stages.stage6 import hook, semantic
from novel.store import ArtifactStore
from novel.work_units import WorkUnitRefused, budget_for
from tests.test_stage2_generation import _ctx
from tests.test_stage6_legacy_delivery_recovery import (
    _crash_after_first_adoption, _response, _rig as hook_rig,
)
from tests.test_stage6_semantic_agent_boundary import _response as judge_response


def _rig(tmp_path, monkeypatch, *, cached_roles=()):
    hook_ctx, hook_source, _wire, _factory, up = hook_rig(tmp_path, monkeypatch)
    hook.run_hook(hook_ctx, hook_source)
    ctx = _ctx(hook_ctx.novel_dir, 'stage6.semantic')
    # 上游仍由真实 build_prompt/ArtifactStore 读取，隔离无关报告展示判据。
    monkeypatch.setattr(hook, '_judge_artifact', lambda *_a, **_k: [])
    monkeypatch.setattr(hook, '_report', lambda *_a, **_k: SimpleNamespace(passed=True))
    prepared = semantic.prepare_semantic_candidate(ctx)
    review = judge_response().model_dump(mode='json')['reviews'][0]
    payload = {
        'reviews': [{**review, 'label': neutral} for neutral, _real, _text in prepared.aliases],
        'ranking': [neutral for neutral, _real, _text in prepared.aliases],
    }

    def client(current, *, responder=None, no_wire=False, model='test-model'):
        def forbidden(*_args):
            raise AssertionError('双判官恢复不得购买任何新回答')
        transport = FakeTransport(forbidden if no_wire else responder or (lambda *_a: _response(payload)))
        source = LlmClient(
            current, transport, model=model, concurrency=2, max_retries=0,
            cache=ResponseCache(tmp_path / 'semantic-cache'),
            budget_checker=lambda *_a, **_k: None)
        return source, transport

    source, wire = client(ctx)
    for role in cached_roles:
        request = source._chat_request(
            purpose=semantic._judge_purpose(ctx, role), model=source.model,
            system=prepared.system, user=prepared.user_for(role),
            reasoning_effort=source._rungs[0][0], thinking=source._rungs[0][1],
            json_mode=True, max_tokens=semantic.SEMANTIC_MAX_TOKENS)
        source.cache.put(cache_key(request), _response(payload))
    return ctx, source, wire, client, prepared, payload, up


def _adoptions(ctx):
    return [row for row in read_jsonl_rows(audit_path(ctx.novel_dir.parent))
            if row.get('kind') == 'artifact_adoption' and row.get('node') == ctx.node]


def _snapshot(base):
    return {str(path.relative_to(base)): path.read_bytes() for path in base.rglob('*')
            if path.is_file() and not path.name.endswith('.lock')}


def test_default_warmup_reaches_both_judge_wires_without_extra_request(tmp_path, monkeypatch):
    ctx, _source, _wire, make_client, prepared, payload, _up = _rig(tmp_path, monkeypatch)
    barrier = threading.Barrier(2)
    def respond(request, _index):
        assert request.history == ()
        barrier.wait(timeout=10)
        return _response(payload)
    source, wire = make_client(ctx, responder=respond)
    assert source.warmup is True
    artifact, report = semantic.run_semantic(ctx, source)
    assert report.passed and len(wire.calls) == 2
    assert {call.user for call in wire.calls} == {prepared.user_for('A'), prepared.user_for('B')}
    assert all(budget_for(ctx, scope=f'judge:{role}').used == 1 for role in ('a', 'b'))
    assert len(_adoptions(ctx)) == 2
    assert ArtifactStore(ctx).read_own(type(artifact), semantic.SEMANTIC_TPL.format(n=1))[0] == artifact


def test_failed_a_waits_for_b_then_restart_restores_both_original_answers(tmp_path, monkeypatch):
    ctx, source, wire, make_client, prepared, payload, _up = _rig(tmp_path, monkeypatch)
    entered_b, release_b, failed_a = threading.Event(), threading.Event(), threading.Event()
    original = source.ask_json
    def interrupted(system, user, **kwargs):
        if kwargs['purpose'].endswith('.B'):
            entered_b.set()
            assert release_b.wait(10)
        result = original(system, user, **kwargs)
        if kwargs['purpose'].endswith('.A'):
            failed_a.set()
            raise OSError('A 回答验真后中断')
        return result
    monkeypatch.setattr(source, 'ask_json', interrupted)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(semantic.request_semantic_candidates, ctx, source, prepared)
        assert entered_b.wait(10) and failed_a.wait(10)
        assert not future.done()
        release_b.set()
        with pytest.raises(OSError, match='A 回答'):
            future.result(timeout=15)
    assert len(wire.calls) == 2
    frozen = ledger_path(tmp_path).read_bytes()
    resumed = _ctx(ctx.novel_dir, ctx.node)
    restored, no_wire = make_client(resumed, no_wire=True)
    artifact, _report = semantic.run_semantic(resumed, restored)
    assert no_wire.calls == [] and ledger_path(tmp_path).read_bytes() == frozen
    assert len(artifact.reviews) == len(prepared.aliases)
    assert all(budget_for(resumed, scope=f'judge:{role}').used == 1 for role in ('a', 'b'))


def test_b_saved_evidence_is_verified_before_missing_a_wire(tmp_path, monkeypatch):
    ctx, source, _wire, make_client, prepared, _payload, _up = _rig(tmp_path, monkeypatch)
    source.ask_json(
        prepared.system, prepared.user_for('B'), model_cls=HookSemanticResponse,
        purpose=semantic._judge_purpose(ctx, 'B'), max_tokens=semantic.SEMANTIC_MAX_TOKENS,
        validator=semantic.response_validator(list(prepared.aliases)))
    budget = budget_for(ctx, scope='judge:b')
    path = budget.directory / f'{budget.latest_turn().response_sha256}.json'
    path.write_text('{}', encoding='utf-8')
    resumed = _ctx(ctx.novel_dir, ctx.node)
    restored, wire = make_client(resumed, no_wire=True)
    with pytest.raises((WorkUnitRefused, ProvenanceRefused)):
        semantic.run_semantic(resumed, restored)
    assert wire.calls == [] and budget_for(resumed, scope='judge:a').used == 0


@pytest.mark.parametrize('cached_roles', [(), ('A',), ('A', 'B')])
def test_first_adoption_crash_free_recovery_keeps_original_sequence_and_sources(
        tmp_path, monkeypatch, cached_roles):
    ctx, source, wire, _make_client, _prepared, _payload, _up = _rig(
        tmp_path, monkeypatch, cached_roles=cached_roles)
    _crash_after_first_adoption(monkeypatch, ctx)
    with pytest.raises(OSError, match='首条采纳'):
        semantic.run_semantic(ctx, source)
    assert len(wire.calls) == 2 - len(cached_roles) and len(_adoptions(ctx)) == 1
    rel = semantic.SEMANTIC_TPL.format(n=1)
    formal = (ctx.novel_dir / rel).read_bytes()
    frozen_costs = ledger_path(tmp_path).read_bytes()
    for path in (tmp_path / 'semantic-cache').rglob('*.json'):
        path.unlink()
    resumed = _ctx(ctx.novel_dir, ctx.node, paid=False)
    monkeypatch.setattr(LlmClient, '__init__', lambda *_a, **_k: pytest.fail('免费恢复不得构造 client'))
    before = _snapshot(tmp_path)
    from novel.orchestrator.state import readonly_ctx
    state = semantic.inspect_prepared_recovery(readonly_ctx(ctx.novel_dir, ctx.node, ctx.code))
    assert state.state == 'recoverable' and state.source_run_id == ctx.run_id, state.reason
    assert _snapshot(tmp_path) == before
    artifact, report = semantic.recover_prepared_semantic(
        resumed, source_run_id=state.source_run_id, proof_sha256=state.proof_sha256)
    assert report.passed and (ctx.novel_dir / rel).read_bytes() == formal
    assert semantic.semantic_seqs(ArtifactStore(resumed)) == [1]
    assert ledger_path(tmp_path).read_bytes() == frozen_costs
    assert len(_adoptions(ctx)) == 2 and {r['run_id'] for r in _adoptions(ctx)} == {ctx.run_id}
    assert {r['purpose'] for r in _adoptions(ctx)} == {f'{ctx.node}.A', f'{ctx.node}.B'}
    for role in ('A', 'B'):
        assert budget_for(resumed, scope=f'judge:{role.lower()}').used == int(role not in cached_roles)
    assert semantic.inspect_prepared_recovery(resumed).state == 'complete'
    semantic.recover_prepared_semantic(resumed)
    assert len(_adoptions(ctx)) == 2


def test_group_before_first_formal_write_recovers_exact_original_bytes(tmp_path, monkeypatch):
    ctx, source, _wire, _make_client, _prepared, _payload, _up = _rig(tmp_path, monkeypatch)
    original = ArtifactStore.write
    rel = semantic.SEMANTIC_TPL.format(n=1)
    def interrupted(self, target, artifact):
        if target == rel:
            raise OSError('组已封存，正式写前中断')
        return original(self, target, artifact)
    monkeypatch.setattr(ArtifactStore, 'write', interrupted)
    with pytest.raises(OSError, match='正式写前'):
        semantic.run_semantic(ctx, source)
    proof = budget_for(ctx, scope='judge:a').group_producer_recovery()
    assert not (ctx.novel_dir / rel).exists()
    monkeypatch.setattr(ArtifactStore, 'write', original)
    resumed = _ctx(ctx.novel_dir, ctx.node, paid=False)
    semantic.recover_prepared_semantic(resumed)
    assert ArtifactStore(resumed).full_sha256_or_none(rel) == proof['target_sha256']
    assert len(_adoptions(ctx)) == 2


@pytest.mark.parametrize('drift', ['formal', 'proof', 'cost', 'adoption', 'input', 'token'])
def test_free_group_recovery_refuses_drift_without_writes(tmp_path, monkeypatch, drift):
    ctx, source, _wire, _make_client, _prepared, _payload, up = _rig(tmp_path, monkeypatch)
    _crash_after_first_adoption(monkeypatch, ctx)
    with pytest.raises(OSError, match='首条采纳'):
        semantic.run_semantic(ctx, source)
    rel = semantic.SEMANTIC_TPL.format(n=1)
    if drift == 'formal':
        path = ctx.novel_dir / rel
        path.write_bytes(path.read_bytes() + b'\n')
    elif drift == 'proof':
        budget = budget_for(ctx, scope='judge:a')
        (budget.directory / f'{budget.group_producer_recovery_sha256}.json').write_text('{}', encoding='utf-8')
    elif drift in ('cost', 'adoption'):
        path = ledger_path(tmp_path) if drift == 'cost' else audit_path(tmp_path)
        rows = read_jsonl_rows(path)
        row = next(row for row in rows if row.get('node') == ctx.node
                   and (drift == 'cost' or row.get('kind') == 'artifact_adoption'))
        if drift == 'cost':
            row['completion_tokens'] += 1
        else:
            row['parent_run_id'] = 'different-parent'
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    elif drift == 'input':
        up.src_hook = '上游参考文本已变化。'
    resumed = _ctx(ctx.novel_dir, ctx.node, paid=False)
    frozen = _snapshot(tmp_path)
    state = semantic.inspect_prepared_recovery(resumed)
    assert _snapshot(tmp_path) == frozen
    assert state.state == ('recoverable' if drift == 'token' else 'unknown'), state.reason
    with pytest.raises((TerminalState, ProvenanceRefused, ValueError)):
        semantic.recover_prepared_semantic(resumed, proof_sha256='0' * 64 if drift == 'token' else None)
    assert _snapshot(tmp_path) == frozen


def test_refresh_uses_remaining_judge_budget_and_fourth_group_recovers_free(tmp_path, monkeypatch):
    ctx, source, _wire, make_client, _prepared, _payload, _up = _rig(tmp_path, monkeypatch)
    semantic.run_semantic(ctx, source)
    for ordinal in (2, 3, 4):
        ctx = _ctx(ctx.novel_dir, ctx.node)
        ctx.cache = CachePolicy.refresh()
        source, wire = make_client(ctx)
        if ordinal == 4:
            _crash_after_first_adoption(monkeypatch, ctx)
            with pytest.raises(OSError, match='首条采纳'):
                semantic.run_semantic(ctx, source)
        else:
            semantic.run_semantic(ctx, source)
        assert len(wire.calls) == 2
    resumed = _ctx(ctx.novel_dir, ctx.node, paid=False)
    assert all(budget_for(resumed, scope=f'judge:{role}').remaining == 0 for role in ('a', 'b'))
    frozen = ledger_path(tmp_path).read_bytes()
    semantic.recover_prepared_semantic(resumed)
    assert semantic.semantic_seqs(ArtifactStore(resumed)) == [1, 2, 3, 4]
    assert len(_adoptions(ctx)) == 8 and ledger_path(tmp_path).read_bytes() == frozen


def test_empty_base_recovery_inspection_has_no_manifest_side_effect(tmp_path):
    from novel.run import RunContext
    from novel.llm.ledger import CostLedger
    ctx = RunContext(novel_dir=tmp_path / 'book_962', node='stage6.semantic', paid=False,
                     code='fixture-code', cache=CachePolicy.default(),
                     ledger=CostLedger(path=ledger_path(tmp_path)))
    assert semantic.inspect_prepared_recovery(ctx) is None
    ctx.node = 'stage6.hook'
    assert hook.inspect_prepared_recovery(ctx) is None
    assert not any(tmp_path.iterdir())


def test_completed_semantic_history_allows_new_inputs_and_new_judge_group(tmp_path, monkeypatch):
    from novel.orchestrator.state import readonly_ctx
    ctx, source, _wire, make_client, _prepared, _payload, up = _rig(tmp_path, monkeypatch)
    semantic.run_semantic(ctx, source)
    first = (ctx.novel_dir / semantic.SEMANTIC_TPL.format(n=1)).read_bytes()
    up.src_hook = '已经修改的合成参考内容。'
    query = readonly_ctx(ctx.novel_dir, ctx.node, ctx.code)
    assert semantic.inspect_prepared_recovery(query).state == 'complete'
    resumed = _ctx(ctx.novel_dir, ctx.node)
    second, wire = make_client(resumed)
    semantic.run_semantic(resumed, second)
    assert len(wire.calls) == 2
    assert semantic.semantic_seqs(ArtifactStore(resumed)) == [1, 2]
    assert (ctx.novel_dir / semantic.SEMANTIC_TPL.format(n=1)).read_bytes() == first
    assert semantic.inspect_prepared_recovery(query).state == 'complete'
    assert all(budget_for(resumed, scope=f'judge:{role}').used == 2 for role in ('a', 'b'))


def test_all_adoptions_before_completion_marker_remains_free_recoverable(tmp_path, monkeypatch):
    from novel.orchestrator.state import readonly_ctx
    from novel.work_units import WorkUnitBudget
    ctx, source, _wire, _make_client, _prepared, _payload, _up = _rig(tmp_path, monkeypatch)
    original = WorkUnitBudget.complete_group_producer_recovery
    def interrupted(self, proof):
        raise OSError('原双采纳已齐，组完成标记写前中断')
    monkeypatch.setattr(WorkUnitBudget, 'complete_group_producer_recovery', interrupted)
    with pytest.raises(OSError, match='标记写前'):
        semantic.run_semantic(ctx, source)
    assert len(_adoptions(ctx)) == 2
    anchor = budget_for(ctx, scope='judge:a')
    assert not anchor.group_producer_completed
    query = readonly_ctx(ctx.novel_dir, ctx.node, ctx.code)
    assert semantic.inspect_prepared_recovery(query).state == 'recoverable'
    frozen = ledger_path(tmp_path).read_bytes()
    monkeypatch.setattr(WorkUnitBudget, 'complete_group_producer_recovery', original)
    semantic.recover_prepared_semantic(_ctx(ctx.novel_dir, ctx.node, paid=False))
    assert anchor.group_producer_completed and semantic.inspect_prepared_recovery(query).state == 'complete'
    assert len(_adoptions(ctx)) == 2 and ledger_path(tmp_path).read_bytes() == frozen
