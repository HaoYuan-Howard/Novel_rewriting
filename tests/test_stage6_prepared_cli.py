"""原回答已交付但采纳未齐：真实 CLI、HTTP preview、队列与 Supervisor 免费补尾。"""
from __future__ import annotations

import os

import pytest

from novel import config
from novel.cli import main as cli
from novel import exit_codes as X
from novel.llm.client import LlmClient
from novel.llm.ledger import audit_path, ledger_path, read_jsonl_rows
from novel.orchestrator import execution_permit, operator, queue
from novel.orchestrator.agent_supervisor import RunSupervisor, SupervisorActionResult
from novel.stages.stage6 import hook
from novel.stages.stage7 import rename
from novel.store import ArtifactStore
from novel.work_units import budget_for
from tests.test_agent_supervisor import _epoch, _outcome
from tests.test_stage2_generation import _ctx
from tests.test_stage6_legacy_delivery_recovery import _crash_after_first_adoption, _rig as hook_rig
from tests.test_stage7_legacy_delivery_recovery import _rig as rename_rig, _interrupt as rename_interrupt


# stage6.semantic 的恢复面已随判官节点退役删除（2026-09-15）。
NODES = ['stage6.hook', 'stage7.rename']


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.setattr(config, 'load_config', lambda *, env=None, dotenv_path=None: config.Config(
        api_key=None, stage2_blueprint_contract=(env if env is not None else os.environ).get(
            config.ENV_KEYS['stage2_blueprint_contract'], config.DEFAULT_STAGE2_BLUEPRINT_CONTRACT)))


def _prepared(tmp_path, monkeypatch, node, *, before_adoption=False):
    if node == 'stage6.hook':
        ctx, source, _wire, _factory, _up = hook_rig(tmp_path, monkeypatch)
        producer, run = hook, hook.run_hook
    else:
        ctx, source, _wire, _factory, _up = rename_rig(tmp_path, monkeypatch)
        producer, run = rename, rename.run_rename
    if node == 'stage7.rename':
        with monkeypatch.context() as fault:
            rename_interrupt(fault, 'adoption' if before_adoption else 'after_adoption')
            with pytest.raises(OSError, match='原人物表采纳'):
                run(ctx, source)
    else:
        _crash_after_first_adoption(monkeypatch, ctx)
        with pytest.raises(OSError, match='首条采纳'):
            run(ctx, source)
    monkeypatch.setattr(LlmClient, '__init__', lambda *_a, **_k: pytest.fail('恢复不得构造 LLM client'))
    inspected = producer.inspect_prepared_recovery(ctx)
    assert inspected.state == 'recoverable', inspected.reason
    flags = ['--recover-prepared', inspected.source_run_id, '--subject-sha256', inspected.proof_sha256]
    return ctx, producer, flags


@pytest.mark.parametrize('node', NODES)
def test_exact_free_recovery_is_real_queue_supervisor_cli_and_http_preview(tmp_path, monkeypatch, node):
    ctx, producer, flags = _prepared(tmp_path, monkeypatch, node)
    scopes = ['book']
    before_budget = {scope: budget_for(ctx, scope=scope).used for scope in scopes}
    if node == 'stage6.hook':
        assert budget_for(ctx, scope='book').remaining == 0
    costs = ledger_path(tmp_path).read_bytes()
    rel = producer.REL
    formal = (ctx.novel_dir / rel).read_bytes()
    todo, blocked = queue.plan_book(tmp_path, ctx.sid, until_stage='stage7')
    assert not todo and len(blocked) == 1
    assert blocked[0].node == node and blocked[0].flags == flags
    assert blocked[0].waiting_on == queue.WAITING_ON_PREPARED_DELIVERY
    assert len(blocked[0].steps) == 1 and '--yes' not in blocked[0].steps[0].argv
    current = operator.next_action(tmp_path, ctx.sid)
    assert current.action.flags == flags and current.action.paid is False
    permit = execution_permit.permit_from_next(current, node, flags)
    assert permit.kind == 'recovery' and permit.paid is False

    from fastapi.testclient import TestClient
    from novel.api.app import UI_HEADER, create_app
    with TestClient(create_app(tmp_path), headers={UI_HEADER: '1'}) as client:
        response = client.post('/api/jobs/preview', json={'sid': ctx.sid, 'node': node, 'flags': flags})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body['paid'] is False and body['writes'] is True
        assert all('--yes' not in step['argv'] for step in body['steps'])

    # rig 省略了机械判据执行，真实报告为 exit 1；保存原产物不授予校验/语义通过。
    expected_rc = X.OK
    called = []
    def recover(_epoch, actual, ticket):
        assert actual.action.node == node and actual.action.flags == flags
        execution_permit.permit_from_next(actual, node, flags)
        ticket.commit()
        called.append(node)
        rc = cli.main(['run', node, ctx.sid, '--base', str(tmp_path), *flags])
        assert X.landed(rc) and rc == expected_rc
        return SupervisorActionResult(rc, _outcome('ready' if rc == X.OK else 'stopped'), False)
    forbidden = lambda *_a: pytest.fail('免费提交不能进入默认生成或已退役审阅通道')
    supervisor = RunSupervisor(
        run_id='test-free-stage6', append_event=lambda _event: None,
        make_epoch=forbidden, open_epoch=lambda _epoch: None,
        close_epoch=lambda _epoch, _turn: 'drained',
        epoch_identity_current=lambda _epoch: True, cancel_active=lambda: True,
        initial_epoch=_epoch('free-stage6-epoch', 1, 'initial').model_copy(update={
            'code_version': current.code_version}),
        read_operator_next=lambda _epoch: operator.next_action(tmp_path, ctx.sid),
        run_ready_action=forbidden, finalize_run=forbidden,
        run_blocked_action=recover, deterministic_blockers=queue.DETERMINISTIC_RECOVERY_WAITING_ON)
    result = supervisor.run()
    assert X.landed(result.turn.exit_code) and result.turn.exit_code == expected_rc
    assert result.turn.outcome.state == ('ready' if expected_rc == X.OK else 'stopped')
    assert called == [node]
    assert producer.inspect_prepared_recovery(ctx).state == 'complete'
    assert (ctx.novel_dir / rel).read_bytes() == formal
    assert ledger_path(tmp_path).read_bytes() == costs
    assert {scope: budget_for(ctx, scope=scope).used for scope in scopes} == before_budget
    adoptions = [row for row in read_jsonl_rows(audit_path(tmp_path))
                 if row.get('kind') == 'artifact_adoption' and row.get('node') == node]
    assert len(adoptions) == {'stage6.hook': 3, 'stage7.rename': 1}[node]
    assert {row['run_id'] for row in adoptions} == {ctx.run_id}
    assert queue.prepared_delivery_recovery_item(tmp_path, ctx.sid, code=ctx.code) is None

    # 正常批准合法改变正式文件；历史完成组不得重新成为待恢复 blocker。
    if node == 'stage7.rename':
        approve_ctx = _ctx(ctx.novel_dir, 'stage7.approve', paid=False)
        pack = rename.approval_workpack(ArtifactStore(approve_ctx))
        rename.run_approve(approve_ctx, actor='零模型测试', reason='已审阅合成人物表',
                           review_token=pack.review_token)
    else:
        monkeypatch.setattr(hook, '_judge_artifact', lambda *_a, **_k: [])
        approve_ctx = _ctx(ctx.novel_dir, 'stage6.approve', paid=False)
        pack = hook.approval_workpack(ArtifactStore(approve_ctx))
        hook.run_approve(approve_ctx, 1, actor='零模型测试', reason='已审阅合成候选', review_token=pack.review_token)
    assert queue.prepared_delivery_recovery_item(tmp_path, ctx.sid, code=ctx.code) is None


@pytest.mark.parametrize('node', NODES)
def test_bad_group_proof_has_no_executable_queue_or_paid_fallback(tmp_path, monkeypatch, node):
    # 原采纳已经齐全时，历史提交不依赖当前字节；此处必须制造未完成提交的 CAS 漂移。
    ctx, _producer, flags = _prepared(tmp_path, monkeypatch, node, before_adoption=True)
    rel = _producer.REL
    target = ctx.novel_dir / rel
    target.write_bytes(target.read_bytes() + b'\n')
    costs, adoptions = ledger_path(tmp_path).read_bytes(), audit_path(tmp_path).read_bytes()
    todo, blocked = queue.plan_book(tmp_path, ctx.sid, until_stage='stage7')
    assert not todo and len(blocked) == 1
    assert blocked[0].waiting_on == queue.WAITING_ON_PREPARED_DELIVERY_UNKNOWN
    assert blocked[0].steps == [] and blocked[0].flags == []
    current = operator.next_action(tmp_path, ctx.sid)
    assert current.state == 'blocked' and current.execution_token is None
    assert current.action.waiting_on not in queue.DETERMINISTIC_RECOVERY_WAITING_ON
    assert cli.main(['run', node, ctx.sid, '--base', str(tmp_path), *flags]) != 0
    assert ledger_path(tmp_path).read_bytes() == costs and audit_path(tmp_path).read_bytes() == adoptions


def test_recovery_query_of_empty_base_is_readonly(tmp_path):
    (tmp_path / 'empty_961').mkdir()
    assert queue.prepared_delivery_recovery_item(tmp_path, '961', code='test') is None
    assert not (tmp_path / '_service').exists()


@pytest.mark.parametrize('node', NODES)
@pytest.mark.parametrize('flags', [
    ['--recover-prepared', 'source-run'],
    ['--subject-sha256', 'a' * 64],
    ['--recover-prepared', 'source-run', '--subject-sha256', 'a' * 64, '--response-repair', 'agent'],
    ['--recover-prepared', 'source-run', '--subject-sha256', 'not-a-sha256'],
])
def test_cli_recovery_coordinates_are_required_and_closed_before_producer(tmp_path, monkeypatch, node, flags):
    """真实参数入口不能把漏坐标或混合命令降成默认生成；拒绝先于上下文/产物写入。"""
    from novel import exit_codes
    monkeypatch.setattr(cli, '_dispatch', lambda *_a, **_k: pytest.fail('非法恢复参数不能进入 producer'))
    monkeypatch.setattr(LlmClient, '__init__', lambda *_a, **_k: pytest.fail('非法恢复参数不能构造 client'))
    assert cli.main(['run', node, '961', '--base', str(tmp_path), *flags]) == exit_codes.TERMINAL_STATE
    assert list(tmp_path.iterdir()) == []
