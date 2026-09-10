"""真实 Hook producer 的完整候选组、正式落盘和多来源采纳断点。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from contracts.invariants import HOOK_RECIPE, HOOK_SLOTS
from contracts.schema.report import finding
from contracts.schema.stage1 import HookCopy
from contracts.schema.stage6 import Mech
from novel.errors import ProvenanceRefused
from novel.llm.cache import CachePolicy, ResponseCache, cache_key
from novel.llm.client import LlmClient
from novel.llm.ledger import CostLedger, audit_path, ledger_path, read_jsonl_rows
from novel.llm.transport import ChatResponse, FakeTransport, TransportError
from novel.stages.stage6 import hook, prompts
from novel.store import ArtifactStore
from novel.work_units import WorkUnitRefused, budget_for
from tests.test_stage2_generation import _ctx


def _response(payload):
    return ChatResponse(
        content=json.dumps(payload, ensure_ascii=False), finish_reason='stop',
        model='test-model', usage={'prompt_tokens': 10, 'completion_tokens': 20})


def _rig(tmp_path, monkeypatch, *, cached=False, fail_last=False):
    book = tmp_path / 'hook_961'
    ctx = _ctx(book, 'stage6.hook')
    ArtifactStore(ctx).write(hook.HOOK_COPY, HookCopy(text='合成原始开篇文案。'))
    up = SimpleNamespace(
        mat=prompts.Material(blocks=('完整合成创作包。',), upto=1),
        src_hook='', secrets=[],  characters={},
        hook_mechanical={}, upto=1, judged={})
    def upstreams(_current, store):
        # 真实消费边由 ArtifactStore 读取建立；不手填 provenance.inputs。
        store.read(HookCopy, hook.HOOK_COPY)
        return up

    monkeypatch.setattr(hook, '_Upstreams', upstreams)

    def judge(text, _up):
        findings = ([finding('hook_truncated', detail='候选未完整', evidence=[text])]
                    if text.startswith('坏候选') else [])
        return text, Mech(punct_fixed=0, quotes_fixed=0, paras_fixed=0), findings

    monkeypatch.setattr(hook, '_normalize_and_judge', judge)
    initial = {
        'candidates': [{'approach': approach, 'text': f'坏候选{index}。'}
                       for index, approach in enumerate(HOOK_RECIPE)],
        'rationale': '初始整组说明。',
    }

    def respond(request, _index):
        marker = '── 单候选定向修复 ──\n'
        if marker not in request.user:
            return _response(initial)
        repair = json.loads(request.user.split(marker, 1)[1])
        if fail_last and repair['target_slot'] == HOOK_SLOTS[-1]:
            raise TransportError('最后一槽未取得回答', retryable=False)
        return _response({'approach': repair['target_approach'],
                          'text': f'完成定向修复{repair["target_slot"]}。',
                          'rationale': f'采用{repair["target_slot"]}的整组说明。'})

    def client(current, *, model='test-model', no_wire=False):
        def forbidden(*_args):
            raise AssertionError('完整组恢复不得发起 wire')
        transport = FakeTransport(forbidden if no_wire else respond)
        source = LlmClient(
            current, transport, model=model, max_retries=0,
            cache=ResponseCache(tmp_path / 'cache'),
            budget_checker=lambda *_a, **_kw: None)
        return source, transport

    first, wire = client(ctx)
    if cached:
        prepared = hook.prepare_hook_candidate(ctx)
        request = first._chat_request(
            purpose=ctx.node, model=first.model, system=prepared.system,
            user=prepared.user, reasoning_effort=first._rungs[0][0],
            thinking=first._rungs[0][1], json_mode=True, max_tokens=hook.MAX_TOKENS)
        first.cache.put(cache_key(request), _response(initial))
    return ctx, first, wire, client, up


def _crash_after_first_adoption(monkeypatch, ctx):
    original = CostLedger.note_adoption
    fired = False

    def interrupted(self, **kwargs):
        nonlocal fired
        original(self, **kwargs)
        if self is ctx.ledger and not fired:
            fired = True
            raise OSError('首条采纳落账后中断')

    monkeypatch.setattr(CostLedger, 'note_adoption', interrupted)


def test_full_book_budget_recovers_free_with_exact_readonly_projection(tmp_path, monkeypatch):
    ctx, first, _wire, _make_client, _up = _rig(tmp_path, monkeypatch)
    _crash_after_first_adoption(monkeypatch, ctx)
    with pytest.raises(OSError, match='首条采纳'):
        hook.run_hook(ctx, first)
    frozen = ledger_path(tmp_path).read_bytes()
    resumed = _ctx(ctx.novel_dir, ctx.node, paid=False)
    assert budget_for(resumed, scope='book').remaining == 0
    monkeypatch.setattr(LlmClient, '__init__', lambda *_a, **_k: pytest.fail('免费恢复不得构造 client'))
    from novel.orchestrator.state import readonly_ctx
    projection = hook.inspect_prepared_recovery(readonly_ctx(ctx.novel_dir, ctx.node, ctx.code))
    assert projection.state == 'recoverable' and projection.source_run_id == ctx.run_id, projection.reason
    assert ledger_path(tmp_path).read_bytes() == frozen
    with pytest.raises(ProvenanceRefused, match='证明已变化'):
        hook.recover_prepared_hook(resumed, proof_sha256='0' * 64)
    hook.recover_prepared_hook(
        resumed, source_run_id=projection.source_run_id, proof_sha256=projection.proof_sha256)
    assert hook.inspect_prepared_recovery(resumed).state == 'complete'
    assert ledger_path(tmp_path).read_bytes() == frozen
    assert len([row for row in read_jsonl_rows(audit_path(tmp_path))
                if row.get('kind') == 'artifact_adoption']) == 3


def test_completed_hook_approval_keeps_history_and_does_not_reopen_recovery(tmp_path, monkeypatch):
    from novel.orchestrator.state import readonly_ctx
    ctx, source, _wire, _make_client, _up = _rig(tmp_path, monkeypatch)
    original = hook.run_hook(ctx, source)
    monkeypatch.setattr(hook, '_judge_artifact', lambda *_a, **_k: [])
    approval = _ctx(ctx.novel_dir, 'stage6.approve', paid=False)
    pack, _report = hook._approval_workpack_snapshot(ArtifactStore(approval), original)
    approved, _report = hook.run_approve(
        approval, 1, actor='合成编辑', reason='已逐条核验候选', review_token=pack.review_token)
    assert approved.approved is not None
    query = readonly_ctx(ctx.novel_dir, ctx.node, ctx.code)
    assert hook.inspect_prepared_recovery(query).state == 'complete'
    assert ArtifactStore(query).seen_rounds(hook.REL)


@pytest.mark.parametrize('window', ['before_write', 'after_adoption'])
def test_pure_cache_group_has_zero_budget_free_submission_recovery(tmp_path, monkeypatch, window):
    from novel.orchestrator.state import readonly_ctx
    ctx, source, wire, make_client, _up = _rig(tmp_path, monkeypatch)
    prepared = hook.prepare_hook_candidate(ctx)
    clean = {
        'candidates': [{'approach': approach, 'text': f'缓存完整候选{index}。'}
                       for index, approach in enumerate(HOOK_RECIPE)],
        'rationale': '缓存中的完整候选组。',
    }
    request = source._chat_request(
        purpose=ctx.node, model=source.model, system=prepared.system, user=prepared.user,
        reasoning_effort=source._rungs[0][0], thinking=source._rungs[0][1],
        json_mode=True, max_tokens=hook.MAX_TOKENS)
    source.cache.put(cache_key(request), _response(clean))
    original_write = ArtifactStore.write
    if window == 'before_write':
        def interrupted(self, rel, artifact):
            if rel == hook.REL:
                raise OSError('纯缓存组正式首写前中断')
            return original_write(self, rel, artifact)
        monkeypatch.setattr(ArtifactStore, 'write', interrupted)
    else:
        _crash_after_first_adoption(monkeypatch, ctx)
    with pytest.raises(OSError):
        hook.run_hook(ctx, source)
    assert wire.calls == [] and budget_for(ctx, scope='book').used == 0
    proof = budget_for(ctx, scope='book').group_producer_recovery()
    assert proof is not None
    monkeypatch.setattr(ArtifactStore, 'write', original_write)
    for path in (tmp_path / 'cache').rglob('*.json'):
        path.unlink()
    frozen = ledger_path(tmp_path).read_bytes()
    resumed = _ctx(ctx.novel_dir, ctx.node, paid=False)
    projection = hook.inspect_prepared_recovery(readonly_ctx(ctx.novel_dir, ctx.node, ctx.code))
    assert projection.state == 'recoverable'
    result = hook.recover_prepared_hook(
        resumed, source_run_id=projection.source_run_id, proof_sha256=projection.proof_sha256)
    assert result.rationale == clean['rationale']
    assert budget_for(resumed, scope='book').group_producer_completed
    assert ledger_path(tmp_path).read_bytes() == frozen and budget_for(resumed, scope='book').used == 0
    adoptions = [row for row in read_jsonl_rows(audit_path(tmp_path))
                 if row.get('kind') == 'artifact_adoption']
    assert len(adoptions) == 1 and adoptions[0]['run_id'] == ctx.run_id
    monkeypatch.setattr(hook, '_judge_artifact', lambda *_a, **_k: [])
    approval = _ctx(ctx.novel_dir, 'stage6.approve', paid=False)
    pack, _report = hook._approval_workpack_snapshot(ArtifactStore(approval), result)
    hook.run_approve(approval, 1, actor='合成编辑', reason='逐字核验缓存候选',
                     review_token=pack.review_token)
    assert hook.inspect_prepared_recovery(readonly_ctx(ctx.novel_dir, ctx.node, ctx.code)).state == 'complete'
    # 完整旧 cache 锚先退休，下一次真实生成不能被它遮住。
    fresh = _ctx(ctx.novel_dir, ctx.node)
    fresh.cache = CachePolicy.refresh()
    refreshed, _unused = make_client(fresh)
    new_wire = FakeTransport.always(_response({**clean, 'rationale': '新的真实候选组。'}))
    refreshed.transport = new_wire
    hook.run_hook(fresh, refreshed)
    assert len(new_wire.calls) == 1 and budget_for(fresh, scope='book').used == 1
    assert budget_for(fresh, scope='book').group_producer_recovery() is None
    assert hook.inspect_prepared_recovery(readonly_ctx(ctx.novel_dir, ctx.node, ctx.code)).state == 'complete'


@pytest.mark.parametrize('cached,fail_last,refresh', [
    (False, False, False), (True, False, False), (False, True, False),
    (True, False, True),
])
def test_partial_group_adoption_restores_original_sources_without_wire(
        tmp_path, monkeypatch, cached, fail_last, refresh):
    ctx, first, wire, make_client, _up = _rig(
        tmp_path, monkeypatch, cached=cached, fail_last=fail_last)
    _crash_after_first_adoption(monkeypatch, ctx)
    with pytest.raises(OSError, match='首条采纳'):
        hook.run_hook(ctx, first)
    formal = ctx.novel_dir / hook.REL
    frozen = formal.read_bytes()
    costs = ledger_path(tmp_path).read_bytes()
    assert len(wire.calls) == 4 - int(cached)
    assert len([row for row in read_jsonl_rows(audit_path(tmp_path))
                if row.get('kind') == 'artifact_adoption']) == 1
    # 完整 producer 证明自足；恢复不能依赖原缓存尚未清理。
    for cache_file in (tmp_path / 'cache').rglob('*.json'):
        cache_file.unlink()
    resumed = _ctx(ctx.novel_dir, ctx.node)
    if refresh:
        resumed.cache = CachePolicy.refresh()
    second, resumed_wire = make_client(resumed, no_wire=True)
    recovered = hook.run_hook(resumed, second)
    assert len(recovered.candidates) == 3 and resumed_wire.calls == []
    assert formal.read_bytes() == frozen
    assert not (ctx.novel_dir / hook._INVALID).exists()
    assert ledger_path(tmp_path).read_bytes() == costs
    adoptions = [row for row in read_jsonl_rows(audit_path(tmp_path))
                 if row.get('kind') == 'artifact_adoption']
    assert len(adoptions) == 3
    assert len({row['attempt_id'] for row in adoptions}) == 3
    assert {row['run_id'] for row in adoptions} == {ctx.run_id}
    budget = budget_for(resumed, scope='book')
    assert budget.used == 4 - int(cached) and budget.remaining == int(cached)
    assert not budget.unfinished_content_ids()
    # 下一次显式 refresh 才能新生产；普通重启仍只是幂等恢复。
    resumed.cache = CachePolicy.default()
    hook.run_hook(resumed, second)
    assert len([row for row in read_jsonl_rows(audit_path(tmp_path))
                if row.get('kind') == 'artifact_adoption']) == 3


@pytest.mark.parametrize('drift', ['formal', 'proof', 'model', 'input', 'cost'])
def test_complete_group_drift_refuses_without_new_wire(tmp_path, monkeypatch, drift):
    ctx, first, _wire, make_client, up = _rig(tmp_path, monkeypatch)
    _crash_after_first_adoption(monkeypatch, ctx)
    with pytest.raises(OSError, match='首条采纳'):
        hook.run_hook(ctx, first)
    if drift == 'formal':
        formal = ctx.novel_dir / hook.REL
        formal.write_bytes(formal.read_bytes() + b'\n')
    elif drift == 'proof':
        budget = budget_for(ctx, scope='book')
        turn = budget.latest_turn()
        assert turn is not None and turn.producer_sha256
        path = budget.directory / f'{turn.producer_sha256}.json'
        payload = json.loads(path.read_text(encoding='utf-8'))
        payload['payload']['target_payload']['rationale'] = '篡改终态'
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    elif drift == 'input':
        up.mat = prompts.Material(blocks=('上游已改。',), upto=1)
    elif drift == 'cost':
        path = ledger_path(tmp_path)
        rows = read_jsonl_rows(path)
        rows[0]['completion_tokens'] += 1
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    resumed = _ctx(ctx.novel_dir, ctx.node)
    source, wire = make_client(
        resumed, model='other-model' if drift == 'model' else 'test-model', no_wire=True)
    with pytest.raises((ProvenanceRefused, WorkUnitRefused)):
        hook.run_hook(resumed, source)
    assert wire.calls == [] and budget_for(resumed, scope='book').used == 4


def test_complete_group_refresh_consumes_only_remaining_book_budget(tmp_path, monkeypatch):
    ctx, first, _wire, make_client, _up = _rig(tmp_path, monkeypatch, cached=True)
    original = hook.run_hook(ctx, first)
    assert budget_for(ctx, scope='book').used == 3
    resumed = _ctx(ctx.novel_dir, ctx.node)
    resumed.cache = CachePolicy.refresh()
    source, _unused_wire = make_client(resumed)
    wire = FakeTransport.always(_response({
        'candidates': [{'approach': approach, 'text': f'明确重新采样候选{index}。'}
                       for index, approach in enumerate(HOOK_RECIPE)],
        'rationale': '第二组已完成。',
    }))
    source.transport = wire
    refreshed = hook.run_hook(resumed, source)
    assert refreshed.rationale != original.rationale and len(wire.calls) == 1
    budget = budget_for(resumed, scope='book')
    assert budget.used == 4 and budget.remaining == 0
    costs = read_jsonl_rows(ledger_path(tmp_path))
    assert sum(row.get('cached') is False for row in costs) == 4
    assert sum(row.get('cached') is True for row in costs) == 1


def test_complete_proof_before_formal_write_restores_original_target_bytes(tmp_path, monkeypatch):
    ctx, first, _wire, make_client, _up = _rig(tmp_path, monkeypatch)
    original = ArtifactStore.write

    def interrupted(self, rel, artifact):
        if rel == hook.REL:
            raise OSError('目标已封存但正式首写尚未发生')
        return original(self, rel, artifact)

    monkeypatch.setattr(ArtifactStore, 'write', interrupted)
    with pytest.raises(OSError, match='正式首写'):
        hook.run_hook(ctx, first)
    budget = budget_for(ctx, scope='book')
    _turn_id, proof = budget.producer_recoveries()[0]
    assert not (ctx.novel_dir / hook.REL).exists() and budget.used == 4
    frozen_costs = ledger_path(tmp_path).read_bytes()
    monkeypatch.setattr(ArtifactStore, 'write', original)
    resumed = _ctx(ctx.novel_dir, ctx.node)
    source, wire = make_client(resumed, no_wire=True)
    hook.run_hook(resumed, source)
    assert wire.calls == [] and ledger_path(tmp_path).read_bytes() == frozen_costs
    assert ArtifactStore(resumed).full_sha256_or_none(hook.REL) == proof['target_sha256']
    adoptions = [row for row in read_jsonl_rows(audit_path(tmp_path))
                 if row.get('kind') == 'artifact_adoption']
    assert len(adoptions) == 3 and {row['run_id'] for row in adoptions} == {ctx.run_id}


@pytest.mark.parametrize('drift', ['parent', 'identity', 'duplicate'])
def test_partial_adoption_audit_drift_refuses_even_explicit_refresh(
        tmp_path, monkeypatch, drift):
    ctx, first, _wire, make_client, _up = _rig(tmp_path, monkeypatch, cached=True)
    _crash_after_first_adoption(monkeypatch, ctx)
    with pytest.raises(OSError, match='首条采纳'):
        hook.run_hook(ctx, first)
    path = audit_path(tmp_path)
    rows = read_jsonl_rows(path)
    adopted = next(row for row in rows if row.get('kind') == 'artifact_adoption')
    if drift == 'parent':
        adopted['parent_run_id'] = 'other-parent'
    elif drift == 'identity':
        adopted['adoption_id'] = 'f' * 64
    else:
        rows.append(dict(adopted))
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    frozen = path.read_bytes()
    resumed = _ctx(ctx.novel_dir, ctx.node)
    resumed.cache = CachePolicy.refresh()
    source, wire = make_client(resumed, no_wire=True)
    with pytest.raises(ProvenanceRefused, match='采纳审计'):
        hook.run_hook(resumed, source)
    assert wire.calls == [] and path.read_bytes() == frozen
    assert budget_for(resumed, scope='book').used == 3
