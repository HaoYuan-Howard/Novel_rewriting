"""直连联合首抽的问题 owner 经真实免费修复入口进入 planner。"""
from __future__ import annotations

import re

import pytest

from novel import paths
from novel.errors import NovelError
from novel.orchestrator import execution_permit, operator, queue
from novel.stages.stage2 import analyze
from tests.test_stage2_analyze import _ctx, _generate, _response
from tests.test_stage2_global_agent import _free_ctx


def _files(base, *, active_slot_groups=()):
    result = {}
    for path in base.rglob('*'):
        if not path.is_file():
            continue
        key = path.relative_to(base).as_posix()
        if path.parent in active_slot_groups and re.fullmatch(r'slot-[0-9]{2}\.lock', path.name):
            stat = path.stat()
            result[key] = (
                'active-model-slot',
                stat.st_mode,
                stat.st_ino,
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
            )
        else:
            result[key] = path.read_bytes()
    return result


def _assert_run_frontier_http(base, ctx, *, node, state, waiting_on, handler):
    """真实路由穿过 operator/DTO；读取不得改写联合首抽来源。"""
    from fastapi.testclient import TestClient
    from contracts.invariants import NODES
    from novel.api.app import create_app

    before = _files(base)
    with TestClient(create_app(str(base))) as client:
        response = client.get(f'/api/novels/{ctx.sid}/run')
        assert response.status_code == 200, response.text
        result = response.json()
    frontier = result['resume_frontier']
    assert frontier['state'] == state and frontier['node'] == node
    assert frontier['seq'] == list(NODES).index(node) + 1
    assert frontier['label'] == NODES[node].label
    assert frontier['waiting_on'] == waiting_on and frontier['next_handler'] == handler
    assert frontier['paid'] is False
    assert frontier['verified_skipped_nodes'] == []
    assert frontier['verified_skipped_count'] == 0
    assert frontier['review_states'] is None
    assert [step['node'] for step in result['steps']] == list(NODES)
    assert _files(base) == before


@pytest.mark.parametrize('owner', ['stage2.characters', 'stage2.global'])
def test_uncompilable_owner_draft_blocks_paid_dispatch_and_offers_free_template(
        tmp_path, owner):
    ctx = _ctx(tmp_path)
    response = _response()
    if owner == 'stage2.characters':
        response['characters'][0]['aliases'][0]['evidence_refs'] = ['e999999']
    else:
        response['global']['setting'].update(era='旧时', era_evidence_refs=['e999999'])
    _generate(ctx, response, expect_blocked=True)

    before = _files(tmp_path)
    todo, blocked = queue.plan_book(tmp_path, ctx.sid, until_stage='stage2')
    assert todo == [] and len(blocked) == 1
    assert blocked[0].node == owner
    assert blocked[0].waiting_on == queue.WAITING_ON_ANALYZE_DRAFT_REPAIR
    current = operator.next_action(tmp_path, ctx.sid)
    assert current.state == 'blocked' and current.execution_token is None
    assert current.action.node == owner and current.action.paid is False
    assert current.action.flags == ['--emit-analyze-draft-template']
    assert current.inbox.items[0].kind == 'repairable'
    assert '--repair-analyze-draft' in current.inbox.items[0].how
    assert _files(tmp_path) == before
    _assert_run_frontier_http(
        tmp_path,
        ctx,
        node=owner,
        state='blocked',
        waiting_on=queue.WAITING_ON_ANALYZE_DRAFT_REPAIR,
        handler='human',
    )


def test_prepared_owner_draft_dispatch_binds_exact_journal_and_never_buys_again(
        tmp_path, monkeypatch):
    from contracts.schema.analyze_draft import AnalyzeOwnerDraftEdit
    from novel.stages.stage2 import analyze_draft_recovery
    from novel.store import ArtifactStore

    ctx = _ctx(tmp_path)
    response = _response()
    response['global']['setting'].update(era='旧时', era_evidence_refs=['e999999'])
    _generate(ctx, response, expect_blocked=True)
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
        analyze_draft_recovery.repair(
            free,
            AnalyzeOwnerDraftEdit.model_validate(request),
            actor='test:reviewer',
            reason='未建立背景应保留合法空值',
        )
    monkeypatch.setattr(ArtifactStore, 'write_round', real_write)
    before = _files(tmp_path)
    current = operator.next_action(tmp_path, ctx.sid)
    assert current.state == 'ready' and current.action.node == 'stage2.global'
    assert current.action.flags == ['--recover-analyze-draft']
    assert current.execution_token is not None and current.execution_token.paid is False
    assert _files(tmp_path) == before
    _assert_run_frontier_http(
        tmp_path,
        ctx,
        node='stage2.global',
        state='ready',
        waiting_on=None,
        handler='code',
    )
    journal = ArtifactStore(free).path(paths.analyze_draft_repair_rel('stage2.global'))
    journal.write_bytes(journal.read_bytes() + b' ')
    with pytest.raises(NovelError):
        execution_permit.revalidate_active_execution_surface(current.execution_token)
    ledger = ctx.ledger.path.read_bytes()
    analyze_draft_recovery.recover(_free_ctx(free))
    assert ctx.ledger.path.read_bytes() == ledger
    assert queue.stage2_core_recovery_item(tmp_path, ctx.sid) is None


@pytest.mark.parametrize('problem', ['deleted_identity', 'unknown_evidence'])
def test_repaired_characters_leave_uncompilable_global_for_explicit_free_edit(
        tmp_path, problem):
    from contracts.schema.characters import CharactersEdit
    from novel.stages.stage2 import characters

    ctx = _ctx(tmp_path)
    response = _response()
    original_role = response['characters'][0]['role']
    response['characters'][0]['role'] = '最终遇害'
    if problem == 'unknown_evidence':
        response['global']['setting'].update(era='旧时', era_evidence_refs=['e999999'])
    _generate(ctx, response, expect_blocked=True)
    free = _free_ctx(ctx)
    free.node = 'stage2.characters'
    template = characters.edit_template(free)
    template['operations'] = template['operations'][:1]
    template['operations'][0]['character']['role'] = original_role
    if problem == 'deleted_identity':
        template['operations'].append({
            'kind': 'delete',
            'char_id': 'char_002',
            'reason': '订正误登记',
        })
    characters.apply_edit(
        free,
        CharactersEdit.model_validate(template),
        actor='test:reviewer',
        reason='核对原人物',
    )
    before = _files(tmp_path)
    current = operator.next_action(tmp_path, ctx.sid)
    assert current.state == 'blocked' and current.execution_token is None
    assert current.action.node == 'stage2.global' and current.action.paid is False
    assert current.action.flags == ['--emit-analyze-draft-template']
    assert current.action.waiting_on == queue.WAITING_ON_ANALYZE_DRAFT_REPAIR
    assert _files(tmp_path) == before
