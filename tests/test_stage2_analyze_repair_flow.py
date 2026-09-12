"""已购原响应经真实恢复/修复入口进入 planner；终态与封存来源不能被下一动作掩盖。"""
from __future__ import annotations

import json

import pytest

from novel.errors import GateBlocked, NovelError, ResponseRepairRequired, TerminalState
from novel import paths
from novel.llm import agent_runs, response_repair
from novel.orchestrator import execution_permit, inspection, operator, queue
from novel.stages.stage2 import analyze, analyze_recovery
from tests.test_stage2_analyze import _ctx, _exhausted_raw, _response
from tests.test_stage2_global_agent import _free_ctx


def _files(base):
    return {path.relative_to(base).as_posix(): path.read_bytes()
            for path in base.rglob('*') if path.is_file()}


def _pending(base):
    ctx = _ctx(base)
    _exhausted_raw(ctx, '{broken JSON')
    with pytest.raises(ResponseRepairRequired) as caught:
        analyze_recovery.recover_initial(_free_ctx(ctx), ctx.run_id)
    return ctx, response_repair.find_attempt(ctx.novel_dir, caught.value.attempt_id)


def _assert_blocked(base, ctx, waiting_on, kind):
    flags = ['--recover-analyze-run', ctx.run_id]
    before = _files(base)
    todo, blocked = queue.plan_book(base, ctx.sid, until_stage='stage2')
    assert todo == [] and len(blocked) == 1
    assert blocked[0].node == analyze.NODE and blocked[0].flags == flags
    assert blocked[0].waiting_on == waiting_on and blocked[0].steps == []
    current = operator.next_action(base, ctx.sid)
    assert current.state == 'blocked' and current.execution_token is None
    assert current.action.node == analyze.NODE and current.action.flags == flags
    assert current.action.paid is False and current.action.waiting_on == waiting_on
    assert current.inbox.total == 1 and current.inbox.items[0].kind == kind
    assert _files(base) == before
    with pytest.raises(NovelError):
        operator.permit(base, ctx.sid, analyze.NODE, flags)
    # permit 进行执行许可复验，会触及既有 Stage4 ownership 锁；queue/next
    # 的严格纯读断言在上方。除此单一锁路径，正式产物/费用/workunit 全字节不变。
    lock_target = paths.stage4_commit_lock_path(ctx.novel_dir)
    lock = lock_target.with_name(lock_target.name + '.lock').relative_to(base).as_posix()
    assert {name: raw for name, raw in _files(base).items() if name != lock} == {
        name: raw for name, raw in before.items() if name != lock}


def test_unresolved_original_response_blocks_planner_without_any_write(tmp_path):
    ctx, record = _pending(tmp_path)
    _assert_blocked(tmp_path, ctx, queue.WAITING_ON_ANALYZE_RESPONSE_REPAIR, 'repairable')
    before = inspection.read_surface_seal(tmp_path, ctx.sid)
    (record.root / response_repair.CANDIDATE_FILE).write_text(
        json.dumps(_response()), encoding='utf-8')
    assert inspection.read_surface_seal(tmp_path, ctx.sid) != before
    _assert_blocked(tmp_path, ctx, queue.WAITING_ON_ANALYZE_RESPONSE_REPAIR, 'repairable')


def test_sealed_repair_becomes_free_action_and_resolution_bytes_enter_cas(tmp_path):
    ctx, record = _pending(tmp_path)
    ledger = ctx.ledger.path.read_bytes()
    (record.root / response_repair.CANDIDATE_FILE).write_text(
        json.dumps(_response()), encoding='utf-8')
    unresolved_seal = inspection.read_surface_seal(tmp_path, ctx.sid)
    response_repair.submit(ctx.novel_dir, record.attempt_id, actor='test', reason='核对原文后封存形状修复')
    before = _files(tmp_path)
    current = operator.next_action(tmp_path, ctx.sid)
    assert current.state == 'ready' and current.action.paid is False
    assert current.action.flags == ['--recover-analyze-run', ctx.run_id]
    assert current.execution_token is not None and current.inbox.total == 0
    assert current.surface_seal != unresolved_seal
    assert _files(tmp_path) == before
    # 提交后唯一权威是 resolution 内的候选；可变草稿不再是执行输入。
    (record.root / response_repair.CANDIDATE_FILE).write_text('{}', encoding='utf-8')
    execution_permit.revalidate_active_execution_surface(current.execution_token)
    resolution = record.root / response_repair.RESOLUTION_FILE
    resolution.write_bytes(resolution.read_bytes() + b' ')
    # JSON 内容等价也改变了原始字节。旧 token 不得跨过封存来源变动。
    with pytest.raises(NovelError):
        execution_permit.revalidate_active_execution_surface(current.execution_token)
    renewed = operator.next_action(tmp_path, ctx.sid)
    assert renewed.state == 'ready' and renewed.execution_token.paid is False
    assert renewed.surface_seal != current.surface_seal
    analyze_recovery.recover_initial(_free_ctx(ctx), ctx.run_id)
    assert queue.stage2_core_recovery_item(tmp_path, ctx.sid) is None
    assert ctx.ledger.path.read_bytes() == ledger


@pytest.mark.parametrize('decision', ['decline', 'rejected'])
def test_closed_original_response_repair_stays_blocked_without_rebuy(tmp_path, decision):
    ctx, record = _pending(tmp_path)
    ledger = ctx.ledger.path.read_bytes()
    (record.root / response_repair.CANDIDATE_FILE).write_text('{}', encoding='utf-8')
    response_repair.submit(ctx.novel_dir, record.attempt_id, actor='test', reason='提交唯一响应决定',
                           decline=decision == 'decline')
    if decision == 'rejected':
        with pytest.raises(TerminalState, match='未通过原 schema'):
            analyze_recovery.recover_initial(_free_ctx(ctx), ctx.run_id)
    for _ in range(2):
        _assert_blocked(tmp_path, ctx, queue.WAITING_ON_ANALYZE_RESPONSE_TERMINAL, 'human_only')
    with pytest.raises(TerminalState, match='不可变'):
        response_repair.submit(ctx.novel_dir, record.attempt_id, actor='test', reason='不能重置修复额度')
    assert analyze.pending_initial_run(ctx.novel_dir) == ctx.run_id
    assert ctx.ledger.path.read_bytes() == ledger


def test_missing_original_raw_blocks_instead_of_minting_paid_fallback(tmp_path):
    ctx = _ctx(tmp_path)
    agent_runs.ensure_run(ctx, analyze.run_spec(), **analyze.prepare(ctx).request())
    _assert_blocked(tmp_path, ctx, queue.WAITING_ON_ANALYZE_SOURCE, 'human_only')


@pytest.mark.parametrize('owner', ['stage2.characters', 'stage2.global'])
def test_uncompilable_owner_draft_blocks_paid_dispatch_and_offers_free_template(tmp_path, owner):
    from novel.node_agents import Stage2AnalyzeAgent
    from tests.test_stage2_analyze import _driver
    ctx = _ctx(tmp_path)
    response = _response()
    if owner == 'stage2.characters':
        response['characters'][0]['aliases'][0]['evidence_refs'] = ['e999999']
    else:
        response['global']['setting'].update(era='旧时', era_evidence_refs=['e999999'])
    with pytest.raises(GateBlocked):
        Stage2AnalyzeAgent(ctx, driver=_driver(ctx, response)).run()
    before = _files(tmp_path)
    todo, blocked = queue.plan_book(tmp_path, ctx.sid, until_stage='stage2')
    assert todo == [] and len(blocked) == 1
    assert blocked[0].node == owner and blocked[0].waiting_on == queue.WAITING_ON_ANALYZE_DRAFT_REPAIR
    current = operator.next_action(tmp_path, ctx.sid)
    assert current.state == 'blocked' and current.execution_token is None
    assert current.action.node == owner and current.action.paid is False
    assert current.action.flags == ['--emit-analyze-draft-template']
    assert current.inbox.items[0].kind == 'repairable'
    assert '--repair-analyze-draft' in current.inbox.items[0].how
    assert _files(tmp_path) == before


def test_prepared_owner_draft_dispatch_binds_exact_journal_and_never_buys_again(tmp_path, monkeypatch):
    from contracts.schema.analyze_draft import AnalyzeOwnerDraftEdit
    from novel.node_agents import Stage2AnalyzeAgent
    from novel.stages.stage2 import analyze_draft_recovery
    from novel.store import ArtifactStore
    from tests.test_stage2_analyze import _driver
    ctx = _ctx(tmp_path)
    response = _response()
    response['global']['setting'].update(era='旧时', era_evidence_refs=['e999999'])
    with pytest.raises(GateBlocked):
        Stage2AnalyzeAgent(ctx, driver=_driver(ctx, response)).run()
    free = _free_ctx(ctx)
    free.node = 'stage2.global'
    request = analyze_draft_recovery.edit_template(free)
    request['global_draft']['setting'].update(era=None, era_evidence_refs=[])
    real_write = ArtifactStore.write_round
    def interrupted(store, rel, artifact, *args, **kwargs):
        if rel == analyze.GLOBAL:
            raise KeyboardInterrupt('草稿独立 prepared 后中断')
        return real_write(store, rel, artifact, *args, **kwargs)
    monkeypatch.setattr(ArtifactStore, 'write_round', interrupted)
    with pytest.raises(KeyboardInterrupt):
        analyze_draft_recovery.repair(free, AnalyzeOwnerDraftEdit.model_validate(request),
                                     actor='test:reviewer', reason='未建立背景应保留合法空值')
    monkeypatch.setattr(ArtifactStore, 'write_round', real_write)
    before = _files(tmp_path)
    current = operator.next_action(tmp_path, ctx.sid)
    assert current.state == 'ready' and current.action.node == 'stage2.global'
    assert current.action.flags == ['--recover-analyze-draft']
    assert current.execution_token is not None and current.execution_token.paid is False
    assert _files(tmp_path) == before
    journal = ArtifactStore(free).path(paths.analyze_draft_repair_rel('stage2.global'))
    journal.write_bytes(journal.read_bytes() + b' ')
    with pytest.raises(NovelError):
        execution_permit.revalidate_active_execution_surface(current.execution_token)
    ledger = ctx.ledger.path.read_bytes()
    analyze_draft_recovery.recover(_free_ctx(free))
    assert ctx.ledger.path.read_bytes() == ledger
    assert queue.stage2_core_recovery_item(tmp_path, ctx.sid) is None


@pytest.mark.parametrize('problem', ['deleted_identity', 'unknown_evidence'])
def test_repaired_characters_leave_uncompilable_global_for_explicit_free_edit(tmp_path, problem):
    from contracts.schema.characters import CharactersEdit
    from novel.node_agents import Stage2AnalyzeAgent
    from novel.stages.stage2 import characters
    from tests.test_stage2_analyze import _driver
    ctx = _ctx(tmp_path)
    response = _response()
    original_role = response['characters'][0]['role']
    response['characters'][0]['role'] = '最终遇害'
    if problem == 'unknown_evidence':
        response['global']['setting'].update(era='旧时', era_evidence_refs=['e999999'])
    with pytest.raises(GateBlocked):
        Stage2AnalyzeAgent(ctx, driver=_driver(ctx, response)).run()
    free = _free_ctx(ctx)
    free.node = 'stage2.characters'
    template = characters.edit_template(free)
    template['operations'] = template['operations'][:1]
    template['operations'][0]['character']['role'] = original_role
    if problem == 'deleted_identity':
        template['operations'].append({'kind': 'delete', 'char_id': 'char_002', 'reason': '订正误登记'})
    characters.apply_edit(free, CharactersEdit.model_validate(template), actor='test:reviewer', reason='核对原人物')
    before = _files(tmp_path)
    current = operator.next_action(tmp_path, ctx.sid)
    assert current.state == 'blocked' and current.execution_token is None
    assert current.action.node == 'stage2.global' and current.action.paid is False
    assert current.action.flags == ['--emit-analyze-draft-template']
    assert current.action.waiting_on == queue.WAITING_ON_ANALYZE_DRAFT_REPAIR
    assert _files(tmp_path) == before
