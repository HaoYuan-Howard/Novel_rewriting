"""真实 Rename legacy 回答、提交证明、正式字节与原采纳的免费恢复。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from contracts.schema.stage1 import MainText
from contracts.schema.stage7 import HookConsumed
from novel import prov
from novel.core.naming import family_groups, surface_owners
from novel.errors import ArtifactUnreadable, ProvenanceRefused
from novel.llm.cache import ResponseCache, cache_key
from novel.llm.client import LlmClient
from novel.llm.ledger import CostLedger, audit_path, ledger_path, read_jsonl_rows
from novel.llm.transport import ChatResponse, FakeTransport
from novel.orchestrator.state import readonly_ctx
from novel.stages.stage6 import semantic
from novel.stages.stage7 import prompts, rename
from novel.store import ArtifactStore
from novel.work_units import WorkUnitBudget, WorkUnitRefused, budget_for
from tests.test_stage2_generation import _ctx


def _rig(tmp_path, monkeypatch, *, cached=False):
    ctx = _ctx(tmp_path / '改名恢复_972', 'stage7.rename')
    ArtifactStore(ctx).write(rename.MAIN_TEXT, MainText.model_validate([
        {'chapter_ordinal': 1, 'text': '张明走到门前。'},
        {'chapter_ordinal': 2, 'text': '张明带着信回来了。'},
    ]))
    registry = {'char_001': {'canonical': '张明', 'aliases': [{'surface': '张明'}, *[]], 'role': '主角'}}
    source_text = '张明走到门前。张明带着信回来了。'
    owners = surface_owners(registry)
    groups, surnames, _how = family_groups(registry, source_text, owners)
    hook_text = '张明收到了一封信。'
    recs = [{'output_ordinal': 1, 'text': '张明推开了门。'},
            {'output_ordinal': 2, 'text': '张明把信藏进衣袖。'}]
    up = SimpleNamespace(
        title=ctx.title, registry=registry, owners=owners, sur=surnames,
        family_members={surnames[cids[0]]: tuple(cids) for cids in groups.values()
                        if cids and surnames.get(cids[0])},
        source_text=source_text, recs=recs, hook_text=hook_text,
        hook_art=SimpleNamespace(approved=SimpleNamespace(text=hook_text)),
        hook_sha1=None, hook_consumed=HookConsumed(
            present=True, approved_text_sha1=prov.fingerprint_bytes(hook_text.encode())),
        judged={}, missing=[], invalids=[], planned_chapters=2, ctx_code=ctx.code,
        pre_target='\n'.join([*(row['text'] for row in recs), hook_text, ctx.title]))
    up.mat = prompts.build_material(
        title=up.title, registry=registry, relations=[], era='古代', groups=groups,
        sur=surnames, book_text=source_text,
        targets=[*(row['text'] for row in recs), hook_text, up.title])

    def upstreams(_current, store):
        # 本纵切替换庞大的上游组装；保留真正的读取边、归一、HARD、落盘及审批。
        store.read(MainText, rename.MAIN_TEXT)
        return up

    monkeypatch.setattr(rename, '_Upstreams', upstreams)
    monkeypatch.setattr(semantic, 'require_approved_clear', lambda _store, art: art.approved)
    payload = {'surname_map': [{'old_surname': '张', 'new_surname': '李'}],
               'characters': [{'char_id': 'char_001', 'new_canonical': '李青',
                               'renamed': True, 'not_renamed_reason': None,
                               'aliases': [], 'reason': '姓氏和名均已更换。'}]}
    answer = ChatResponse(content=json.dumps(payload, ensure_ascii=False),
                          finish_reason='stop', model='test-model',
                          usage={'prompt_tokens': 11, 'completion_tokens': 23})

    def client(current, *, model='test-model', no_wire=False):
        def respond(*_a):
            if no_wire:
                pytest.fail('原人物表恢复不能新增模型请求')
            return answer
        wire = FakeTransport(respond)
        return LlmClient(current, wire, model=model, max_retries=0,
                         cache=ResponseCache(tmp_path / 'cache'),
                         budget_checker=lambda *_a, **_k: None), wire

    first, wire = client(ctx)
    if cached:
        prepared = rename.prepare_rename_candidate(ctx)
        request = first._chat_request(
            purpose=ctx.node, model=first.model, system=prepared.system, user=prepared.user,
            reasoning_effort=first._rungs[0][0], thinking=first._rungs[0][1],
            json_mode=True, max_tokens=rename.MAX_TOKENS)
        first.cache.put(cache_key(request), answer)
    return ctx, first, wire, client, up


def _interrupt(monkeypatch, boundary):
    if boundary == 'write':
        original = ArtifactStore.write
        def write(self, rel, artifact):
            if rel == rename.REL:
                raise OSError('原人物表提交中断')
            return original(self, rel, artifact)
        monkeypatch.setattr(ArtifactStore, 'write', write)
    elif boundary in ('adoption', 'after_adoption'):
        original = CostLedger.note_adoption
        def adoption(self, **kwargs):
            if kwargs['node'] != 'stage7.rename':
                return original(self, **kwargs)
            if boundary == 'after_adoption':
                original(self, **kwargs)
            raise OSError('原人物表采纳中断')
        monkeypatch.setattr(CostLedger, 'note_adoption', adoption)
    else:
        def complete(*_a, **_k):
            raise OSError('原人物表完成标记中断')
        monkeypatch.setattr(WorkUnitBudget, 'complete_group_producer_recovery', complete)


@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('boundary', ['write', 'adoption', 'after_adoption', 'complete'])
def test_prepared_recovers_original_bytes_cost_and_adoption_without_client(
        tmp_path, monkeypatch, cached, boundary):
    ctx, first, wire, _factory, _up = _rig(tmp_path, monkeypatch, cached=cached)
    with monkeypatch.context() as fault:
        _interrupt(fault, boundary)
        with pytest.raises(OSError, match='原人物表'):
            rename.run_rename(ctx, first)
    assert len(wire.calls) == int(not cached)
    costs = ledger_path(tmp_path).read_bytes()
    budget = budget_for(ctx, scope='book')
    proof = budget.group_producer_recovery()
    assert proof is not None and budget.used == int(not cached)
    for path in (tmp_path / 'cache').rglob('*.json'):
        path.unlink()
    monkeypatch.setattr(LlmClient, '__init__', lambda *_a, **_k: pytest.fail('免费恢复不构造 client'))
    observed = readonly_ctx(ctx.novel_dir, ctx.node, ctx.code)
    before = {path.relative_to(tmp_path): path.read_bytes()
              for path in tmp_path.rglob('*') if path.is_file()}
    projection = rename.inspect_prepared_recovery(observed)
    assert projection.state == 'recoverable' and projection.source_run_id == ctx.run_id
    assert {path.relative_to(tmp_path): path.read_bytes()
            for path in tmp_path.rglob('*') if path.is_file()} == before
    resumed = _ctx(ctx.novel_dir, ctx.node, paid=False)
    with pytest.raises(ProvenanceRefused, match='证明已变化'):
        rename.recover_prepared_rename(resumed, proof_sha256='0' * 64)
    landed = rename.recover_prepared_rename(
        resumed, source_run_id=projection.source_run_id, proof_sha256=projection.proof_sha256)
    assert landed.model_dump(by_alias=True, mode='json') == proof['target_payload']
    assert ArtifactStore(resumed).full_sha256_or_none(rename.REL) == proof['target_sha256']
    assert ledger_path(tmp_path).read_bytes() == costs
    assert budget.used == int(not cached) and not budget.unfinished_content_ids()
    assert rename.inspect_prepared_recovery(observed).state == 'complete'
    rename.recover_prepared_rename(resumed)
    adoptions = [row for row in read_jsonl_rows(audit_path(tmp_path))
                 if row.get('kind') == 'artifact_adoption' and row.get('node') == ctx.node]
    assert len(adoptions) == 1 and adoptions[0]['run_id'] == ctx.run_id
    assert adoptions[0]['attempt_id'] == proof['adoptions'][0][1]


@pytest.mark.parametrize('damage', ['formal', 'invalid', 'book', 'input', 'code', 'cost', 'adoption', 'blob'])
def test_incomplete_original_proof_damage_stays_unknown_without_replacement(
        tmp_path, monkeypatch, damage):
    ctx, first, _wire, factory, up = _rig(tmp_path, monkeypatch)
    with monkeypatch.context() as fault:
        _interrupt(fault, 'after_adoption' if damage == 'adoption' else 'adoption')
        with pytest.raises(OSError):
            rename.run_rename(ctx, first)
    budget = budget_for(ctx, scope='book')
    proof = budget.group_producer_recovery()
    if damage in ('formal', 'invalid', 'book'):
        rel = {'formal': rename.REL, 'invalid': rename._INVALID, 'book': proof['book_rel']}[damage]
        (ctx.novel_dir / rel).write_text('第三方改变了原目标', encoding='utf-8')
    elif damage == 'input':
        up.mat.blocks.append('新的上游事实')
    elif damage == 'code':
        monkeypatch.setattr(prov, 'code_version', lambda: 'drifted-code')
    elif damage == 'cost':
        ledger_path(tmp_path).write_text('', encoding='utf-8')
    elif damage == 'adoption':
        rows = read_jsonl_rows(audit_path(tmp_path))
        for row in rows:
            if row.get('kind') == 'artifact_adoption':
                row['artifact_fingerprint'] = 'f' * 10
        audit_path(tmp_path).write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    else:
        (budget.directory / f'{budget.group_producer_recovery_sha256}.json').write_text('{}', encoding='utf-8')
    before_costs = ledger_path(tmp_path).read_bytes()
    projection = rename.inspect_prepared_recovery(readonly_ctx(ctx.novel_dir, ctx.node, ctx.code))
    assert projection.state == 'unknown' and projection.source_run_id is None
    resumed = _ctx(ctx.novel_dir, ctx.node)
    second, resumed_wire = factory(resumed, no_wire=True)
    with pytest.raises((ProvenanceRefused, WorkUnitRefused, ValueError)):
        rename.run_rename(resumed, second)
    assert not resumed_wire.calls and ledger_path(tmp_path).read_bytes() == before_costs


@pytest.mark.parametrize('damage', ['cost', 'raw'])
def test_completed_history_still_requires_original_paid_evidence(tmp_path, monkeypatch, damage):
    ctx, first, _wire, _factory, _up = _rig(tmp_path, monkeypatch)
    rename.run_rename(ctx, first)
    budget = budget_for(ctx, scope='book')
    proof = budget.group_producer_recovery()
    assert budget.group_producer_completed
    if damage == 'cost':
        ledger_path(tmp_path).write_text('', encoding='utf-8')
    else:
        receipt = proof['receipts'][0]
        (budget.directory / f'{receipt["response_sha256"]}.json').write_text('{}', encoding='utf-8')
    assert rename.inspect_prepared_recovery(readonly_ctx(ctx.novel_dir, ctx.node, ctx.code)).state == 'unknown'


def test_readonly_foreign_reservation_scan_shares_the_real_commit_guard(tmp_path, monkeypatch):
    from novel.llm import stage7_runs
    from novel.node_agents import Stage7RenameAgent
    from tests.test_stage7_rename_agent import (
        _Driver, _SimulatedProcessExit, _ctx as agent_ctx, _patch_business, _response,
    )

    _patch_business(monkeypatch)
    ctx = agent_ctx(tmp_path)
    with monkeypatch.context() as fault:
        def interrupt(*_a, **_k):
            raise _SimulatedProcessExit('原 Agent prepared 尚未完成')
        fault.setattr(stage7_runs, 'mark_committed', interrupt)
        with pytest.raises(_SimulatedProcessExit):
            Stage7RenameAgent(ctx, driver=_Driver([_response('完整合格映射')]), max_turns=2).run()
    other = agent_ctx(tmp_path)
    before = {path.relative_to(tmp_path): path.read_bytes()
              for path in tmp_path.rglob('*') if path.is_file()}
    with pytest.raises(ProvenanceRefused, match='prepared'):
        stage7_runs.inspect_foreign_commit_reservations(other)
    assert {path.relative_to(tmp_path): path.read_bytes()
            for path in tmp_path.rglob('*') if path.is_file()} == before
    # 只读通过/拒绝都不能授予写所有权，真正 apply 仍必须在书锁内重验。
    with pytest.raises(ProvenanceRefused, match='commit_ownership'):
        stage7_runs.refuse_foreign_commit_reservations(other)
    commit = stage7_runs._commit_spec(ctx).commit_path(ctx)
    raw = commit.read_bytes()
    commit.write_text('{broken', encoding='utf-8')
    with pytest.raises((ArtifactUnreadable, ProvenanceRefused)):
        stage7_runs.inspect_foreign_commit_reservations(other)
    commit.write_bytes(raw)
    restarted = agent_ctx(tmp_path, run_id=ctx.run_id)
    Stage7RenameAgent(restarted, driver=_Driver([], forbid_wire=True), max_turns=2).run()
    stage7_runs.inspect_foreign_commit_reservations(other)
    with stage7_runs.commit_ownership(other.novel_dir):
        stage7_runs.refuse_foreign_commit_reservations(other)


@pytest.mark.parametrize('incomplete_tail', [False, True])
def test_completed_original_acceptance_survives_real_approval_and_changed_inputs(
        tmp_path, monkeypatch, incomplete_tail):
    ctx, first, _wire, _factory, up = _rig(tmp_path, monkeypatch)
    if incomplete_tail:
        with monkeypatch.context() as fault:
            _interrupt(fault, 'complete')
            with pytest.raises(OSError):
                rename.run_rename(ctx, first)
    else:
        rename.run_rename(ctx, first)
    original = ArtifactStore(ctx).full_sha256_or_none(rename.REL)
    approve_ctx = _ctx(ctx.novel_dir, 'stage7.approve', paid=False)
    pack = rename.approval_workpack(ArtifactStore(approve_ctx))
    approved, _report = rename.run_approve(
        approve_ctx, actor='测试审阅者', reason='已核对实际合成改名工作包', review_token=pack.review_token)
    assert approved.approved is not None
    assert ArtifactStore(ctx).full_sha256_or_none(rename.REL) != original
    up.mat.blocks.append('完成提交以后上游合法改变')
    monkeypatch.setattr(prov, 'code_version', lambda: 'later-code')
    before = (ctx.novel_dir / rename.REL).read_bytes()
    assert rename.inspect_prepared_recovery(readonly_ctx(ctx.novel_dir, ctx.node, 'later-code')).state \
        == ('recoverable' if incomplete_tail else 'complete')
    rename.recover_prepared_rename(_ctx(ctx.novel_dir, ctx.node, paid=False))
    assert (ctx.novel_dir / rename.REL).read_bytes() == before
    assert rename.inspect_prepared_recovery(readonly_ctx(ctx.novel_dir, ctx.node, 'later-code')).state == 'complete'


def test_empty_recovery_read_does_not_create_workflow_or_ledger(tmp_path):
    book = tmp_path / '空书_972'
    book.mkdir()
    assert rename.inspect_prepared_recovery(readonly_ctx(book, 'stage7.rename', 'test')) is None
    assert list(tmp_path.iterdir()) == [book]
