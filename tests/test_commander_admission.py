#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Commander admission 的原子顺序、坏尾与重启关闭式边界。"""
from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

from contracts.schema.api import ExecutionPermit
from contracts.schema.orchestration import ProgressEvent
from novel.errors import TerminalState
from novel.orchestrator import (
    admission, agent, beacon, execution_permit, jobs, nodes, operator,
)


def _ready_permit(base: str, *, surface: str = 'a' * 64) -> ExecutionPermit:
    """构造 canonical、可跨 root/follow 复用的最小许可。"""
    flags = [
        '--revalidate', '--actor', 'agent:test', '--reason', '测试复验',
    ]
    payload = {
        'base': str(Path(base).resolve()),
        'sid': '42',
        'code_version': 'code-v1',
        'surface_seal': surface,
        'next_state': 'ready',
        'waiting_on': None,
        'kind': 'ready',
        'node': 'stage4.plan',
        'flags': flags,
        'paid': nodes.is_paid('stage4.plan', flags),
        'writes': nodes.write_effect('stage4.plan', flags),
        'operator_action_sha256': 'b' * 64,
        'external_inputs': [],
    }
    return ExecutionPermit(
        **payload, permit_sha256=operator.canonical_sha256(payload))


def _install_child_envelope(
    monkeypatch: pytest.MonkeyPatch,
    permit: ExecutionPermit,
    claim: admission.AdmissionClaim,
    step_index: int,
) -> None:
    env = execution_permit.env_with_permit(permit, step_index, {})
    env = admission.env_with_claim(claim, env)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def test_claim_before_close_remains_visible_until_exact_release(tmp_path):
    base = str(tmp_path)
    admission.open_gate(base, 'parent-1', '42')
    claim = admission.claim(
        base, 'parent-1', '42', node='stage2.global',
        claim_sha256='a' * 64, claim_id='claim-1')

    closed = admission.close_gate(base, 'parent-1')
    assert closed.closed is True
    assert closed.active_claims == (claim,)
    with pytest.raises(TerminalState, match='迟到'):
        admission.claim(
            base, 'parent-1', '42', node='stage2.global',
            claim_sha256='b' * 64, claim_id='claim-late')

    admission.release(base, claim)
    done = admission.snapshot(base, 'parent-1')
    assert done.closed is True and done.active_claims == ()
    admission.mark_drained(base, 'parent-1')
    assert admission.unresolved(base, '42') == ()


def test_close_before_claim_atomically_rejects_without_writing_claim(tmp_path):
    base = str(tmp_path)
    admission.open_gate(base, 'parent-2', '42')
    admission.close_gate(base, 'parent-2')

    with pytest.raises(TerminalState, match='迟到'):
        admission.claim(
            base, 'parent-2', '42', node='stage4.design',
            claim_sha256='c' * 64, claim_id='claim-after-close')
    assert [event.kind for event in admission.read_events(base)] == [
        'opened', 'closed']


def test_concurrent_claim_and_close_have_one_replayable_transaction_order(tmp_path):
    base = str(tmp_path)
    admission.open_gate(base, 'parent-race', '42')
    barrier = threading.Barrier(3)
    outcome = {}

    def do_claim():
        barrier.wait()
        try:
            outcome['claim'] = admission.claim(
                base, 'parent-race', '42', node='stage2.global',
                claim_sha256='1' * 64, claim_id='claim-race')
        except BaseException as exc:  # 测试线程必须把异常送回主线程
            outcome['claim_error'] = exc

    def do_close():
        barrier.wait()
        try:
            outcome['close'] = admission.close_gate(base, 'parent-race')
        except BaseException as exc:
            outcome['close_error'] = exc

    threads = [threading.Thread(target=do_claim), threading.Thread(target=do_close)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()

    assert 'close_error' not in outcome
    kinds = [event.kind for event in admission.read_events(base)]
    if 'claim' in outcome:
        assert kinds == ['opened', 'claimed', 'closed']
        assert admission.snapshot(
            base, 'parent-race').active_claims == (outcome['claim'],)
        admission.release(base, outcome['claim'])
    else:
        assert isinstance(outcome.get('claim_error'), TerminalState)
        assert kinds == ['opened', 'closed']


def test_full_json_without_lf_is_uncommitted_and_refuses_any_append(tmp_path):
    base = str(tmp_path)
    admission.open_gate(base, 'parent-no-lf', '42')
    path = admission.admission_path(base)
    original = path.read_bytes()
    assert original.endswith(b'\n')
    path.write_bytes(original[:-1])

    assert admission.read_events(base) == []
    with pytest.raises(ValueError, match='未以 LF 提交'):
        admission.close_gate(base, 'parent-no-lf')
    with pytest.raises(ValueError, match='未以 LF 提交'):
        admission.claim(
            base, 'parent-no-lf', '42', node='stage2.global',
            claim_sha256='d' * 64)
    assert path.read_bytes() == original[:-1]


def test_truncated_tail_is_ignored_for_read_but_blocks_close_and_claim(tmp_path):
    base = str(tmp_path)
    admission.open_gate(base, 'parent-bad-tail', '42')
    path = admission.admission_path(base)
    truncated = path.read_bytes() + b'{"wire_version":1'
    path.write_bytes(truncated)

    assert [event.kind for event in admission.read_events(base)] == ['opened']
    with pytest.raises(ValueError, match='未以 LF 提交'):
        admission.close_gate(base, 'parent-bad-tail')
    with pytest.raises(ValueError, match='未以 LF 提交'):
        admission.claim(
            base, 'parent-bad-tail', '42', node='stage2.global',
            claim_sha256='e' * 64)
    assert path.read_bytes() == truncated


def test_active_claim_is_reloaded_from_disk_and_bound_to_sid_and_node(tmp_path):
    base = str(tmp_path)
    admission.open_gate(base, 'parent-active', '42')
    claim = admission.claim(
        base, 'parent-active', '42', node='stage5.write',
        claim_sha256='f' * 64, claim_id='claim-active')
    env = {admission.CLAIM_ID_ENV: claim.claim_id}

    assert admission.require_active_claim(
        base, 'parent-active', '42', 'stage5.write', env=env) == claim
    with pytest.raises(TerminalState, match='不能执行'):
        admission.require_active_claim(
            base, 'parent-active', '42', 'stage4.design', env=env)
    admission.close_gate(base, 'parent-active')
    # close 只拒绝新 claim；close 前取得的 active claim 仍供已经启动的子 CLI 验证。
    assert admission.require_active_claim(
        base, 'parent-active', '42', 'stage5.write', env=env) == claim
    admission.release(base, claim)
    with pytest.raises(TerminalState, match='已释放'):
        admission.require_active_claim(
            base, 'parent-active', '42', 'stage5.write', env=env)


def test_jobs_keep_blocking_a_durable_unresolved_gate_after_memory_restart(
        tmp_path):
    base = str(tmp_path)
    admission.open_gate(base, 'parent-orphan', '42')

    with pytest.raises(jobs.JobConflict, match='未闭合 Commander admission'):
        jobs._refuse_if_agent_running(base, '42')
    with pytest.raises(jobs.JobConflict, match='未闭合 Commander admission'):
        jobs._refuse_if_agent_running(base, None)

    admission.close_gate(base, 'parent-orphan')
    admission.mark_drained(base, 'parent-orphan')
    jobs._refuse_if_agent_running(base, '42')


def test_commander_context_direct_node_cli_without_claim_is_refused_before_run(
        tmp_path, monkeypatch):
    cli = importlib.import_module('novel.cli.main')
    monkeypatch.setenv(
        cli.commander_context.COMMANDER_RUN_ID_ENV, 'parent-direct')
    monkeypatch.setenv(
        cli.commander_context.COMMANDER_CONTRACT_SHA256_ENV, 'a' * 64)
    monkeypatch.setattr(
        cli.commander_context, 'load_from_environment',
        lambda *_args, **_kwargs: SimpleNamespace(run_id='parent-direct'))
    monkeypatch.setattr(
        cli.RunContext, 'open',
        lambda *_args, **_kwargs: pytest.fail('无 claim 不得进入 RunContext/producer'))
    args = cli._parser().parse_args([
        'run', 'stage2.global', '42', '--base', str(tmp_path), '--yes'])

    with pytest.raises(TerminalState, match='缺 active admission claim'):
        cli._cmd_run(args)


def test_v2_child_run_uses_epoch_producer_id_and_keeps_parent_link(
        tmp_path, monkeypatch):
    """真实 CLI 边界不能再让 NOVEL_RUN_ID 覆盖 epoch producer 身份。"""
    cli = importlib.import_module('novel.cli.main')
    base = str(tmp_path.resolve())
    permit = _ready_permit(base)
    parent = SimpleNamespace(
        run_id='agent-parent-run',
        producer_run_id='epoch-producer-run',
        execution_id='epoch-admission-run',
        start=SimpleNamespace(authorized=True, egress_authorized=True),
    )
    admission.open_gate(base, parent.execution_id, permit.sid)
    claim = admission.claim(
        base, parent.execution_id, permit.sid, node=permit.node,
        claim_sha256=permit.permit_sha256, claim_id='epoch-producer-claim')
    _install_child_envelope(monkeypatch, permit, claim, 0)
    steps = nodes.build_steps(
        permit.node, permit.sid, permit.flags, base=permit.base,
        executor_backend=None, egress_authorized=None, placeholders=False)
    monkeypatch.setattr(sys, 'orig_argv', list(steps[0].argv))
    monkeypatch.setenv(
        cli.commander_context.COMMANDER_RUN_ID_ENV, parent.run_id)
    monkeypatch.setenv(
        cli.commander_context.COMMANDER_CONTRACT_SHA256_ENV, 'a' * 64)
    monkeypatch.setenv(cli.run_identity.RUN_ID_ENV, parent.run_id)
    monkeypatch.setattr(
        cli.commander_context, 'load_from_environment',
        lambda *_args, **_kwargs: parent)
    monkeypatch.setattr(cli, 'stage2_review_required', lambda *_args, **_kwargs: False)
    captured: dict[str, object] = {}

    def open_context(*_args, **kwargs):
        captured.update(kwargs)
        from novel.run import RunContext
        ctx = SimpleNamespace(
            novel_dir=tmp_path / '测试书_42', readonly=False,
            run_id=kwargs['run_id'], parent_run_id=kwargs['parent_run_id'])
        ctx.require_current_workflow_write = MethodType(
            RunContext.require_current_workflow_write, ctx)
        return ctx

    monkeypatch.setattr(cli.RunContext, 'open', open_context)
    monkeypatch.setattr(cli, '_dispatch', lambda *_args, **_kwargs: 0)
    args = cli._parser().parse_args([
        'run', permit.node, permit.sid, '--base', base,
        *permit.flags,
    ])

    assert cli._cmd_run(args) == 0
    assert captured['run_id'] == parent.producer_run_id
    assert captured['parent_run_id'] == parent.run_id


def test_one_claim_and_permit_cover_root_and_follow_without_planner_remint(
        tmp_path, monkeypatch):
    """child 只验冻结 envelope/claim/index/argv，不再重跑全链 planner。"""
    cli = importlib.import_module('novel.cli.main')
    base = str(tmp_path.resolve())
    permit = _ready_permit(base)
    parent = SimpleNamespace(
        run_id='parent-permit-follow',
        executor_profile=lambda _node: pytest.fail('免费恢复不应解析 executor'))
    admission.open_gate(base, parent.run_id, permit.sid)
    claim = admission.claim(
        base, parent.run_id, permit.sid, node=permit.node,
        claim_sha256=permit.permit_sha256, claim_id='permit-follow-claim')
    steps = nodes.build_steps(
        permit.node, permit.sid, permit.flags, base=permit.base,
        placeholders=False)
    assert len(steps) == 2 and '--check-only' in steps[1].argv
    args = SimpleNamespace(base=base, sid=permit.sid, node=permit.node)

    # active-surface CAS 已由 runner 在 claim 后、首个副作用前完成；root child
    # 只能核同一 envelope，不能再拍一次完整 operator snapshot。
    _install_child_envelope(monkeypatch, permit, claim, 0)
    monkeypatch.setattr(sys, 'orig_argv', list(steps[0].argv))
    monkeypatch.setattr(
        operator, 'permit',
        lambda *_args, **_kwargs: pytest.fail('root child 不得重铸 operator permit'))
    cli._require_commander_execution_permit(args, permit.flags, parent)

    # root 落盘后 surface 已变；follow 仍只复验同一 claim/envelope 与
    # canonical build_steps[1]，不会拿旧 surface 再做一次错误 CAS。
    _install_child_envelope(monkeypatch, permit, claim, 1)
    monkeypatch.setattr(sys, 'orig_argv', list(steps[1].argv))
    monkeypatch.setattr(
        operator, 'permit',
        lambda *_args, **_kwargs: pytest.fail('follow-check 不得重铸已 stale 的 root permit'))
    cli._require_commander_execution_permit(
        args, ['--check-only'], parent)


@pytest.mark.parametrize('step_index,argv_kind,match', [
    (1, 'root', 'step_index/argv'),
    (2, 'follow', '越过 canonical'),
])
def test_child_rejects_forged_step_index_or_argv(
        step_index, argv_kind, match, tmp_path, monkeypatch):
    cli = importlib.import_module('novel.cli.main')
    base = str(tmp_path.resolve())
    permit = _ready_permit(base)
    parent = SimpleNamespace(
        run_id='parent-forged-index', executor_profile=lambda _node: None)
    admission.open_gate(base, parent.run_id, permit.sid)
    claim = admission.claim(
        base, parent.run_id, permit.sid, node=permit.node,
        claim_sha256=permit.permit_sha256, claim_id='forged-index-claim')
    steps = nodes.build_steps(
        permit.node, permit.sid, permit.flags, base=permit.base,
        placeholders=False)
    _install_child_envelope(monkeypatch, permit, claim, step_index)
    chosen = steps[0] if argv_kind == 'root' else steps[1]
    monkeypatch.setattr(sys, 'orig_argv', list(chosen.argv))

    with pytest.raises(TerminalState, match=match):
        cli._require_commander_execution_permit(
            SimpleNamespace(base=base, sid=permit.sid, node=permit.node),
            permit.flags, parent)


def test_child_rejects_permit_whose_hash_differs_from_active_claim(
        tmp_path, monkeypatch):
    cli = importlib.import_module('novel.cli.main')
    base = str(tmp_path.resolve())
    claimed = _ready_permit(base)
    forged = _ready_permit(base, surface='f' * 64)
    parent = SimpleNamespace(
        run_id='parent-hash-drift', executor_profile=lambda _node: None)
    admission.open_gate(base, parent.run_id, claimed.sid)
    claim = admission.claim(
        base, parent.run_id, claimed.sid, node=claimed.node,
        claim_sha256=claimed.permit_sha256, claim_id='hash-drift-claim')
    _install_child_envelope(monkeypatch, forged, claim, 0)

    with pytest.raises(TerminalState, match='claim SHA-256'):
        cli._require_commander_execution_permit(
            SimpleNamespace(base=base, sid=forged.sid, node=forged.node),
            forged.flags, parent)


def test_plain_direct_run_refuses_durable_unresolved_before_run_context(
        tmp_path, monkeypatch):
    cli = importlib.import_module('novel.cli.main')
    base = str(tmp_path.resolve())
    admission.open_gate(base, 'parent-orphan-direct', '42')
    for name in (
        cli.commander_context.COMMANDER_RUN_ID_ENV,
        cli.commander_context.COMMANDER_CONTRACT_SHA256_ENV,
        admission.CLAIM_ID_ENV,
        execution_permit.PERMIT_JSON_ENV,
        execution_permit.STEP_INDEX_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        cli.RunContext, 'open',
        lambda *_args, **_kwargs: pytest.fail('unresolved gate 前不得进入 RunContext'))
    args = cli._parser().parse_args([
        'run', 'stage1.partition', '42', '--base', base])

    with pytest.raises(TerminalState, match='durable unresolved Commander admission'):
        cli._cmd_run(args)



def test_plain_check_only_can_observe_unresolved_admission_without_writing(tmp_path, monkeypatch):
    cli = importlib.import_module('novel.cli.main')
    base = str(tmp_path.resolve())
    admission.open_gate(base, 'parent-read-only', '42')
    for name in (cli.commander_context.COMMANDER_RUN_ID_ENV,
                 cli.commander_context.COMMANDER_CONTRACT_SHA256_ENV,
                 admission.CLAIM_ID_ENV, execution_permit.PERMIT_JSON_ENV,
                 execution_permit.STEP_INDEX_ENV):
        monkeypatch.delenv(name, raising=False)
    before = {p.relative_to(tmp_path): p.read_bytes()
              for p in tmp_path.rglob('*') if p.is_file()}
    monkeypatch.setattr(cli.RunContext, 'open', lambda *_a, **_k: SimpleNamespace())
    calls = []
    monkeypatch.setattr(cli, '_dispatch', lambda node, _ctx, flags:
                        calls.append((node, flags)) or 0)
    args = cli._parser().parse_args([
        'run', 'stage1.partition', '42', '--check-only', '--base', base])
    assert cli._cmd_run(args) == 0
    assert calls == [('stage1.partition', ['--check-only'])]
    assert {p.relative_to(tmp_path): p.read_bytes()
            for p in tmp_path.rglob('*') if p.is_file()} == before

def test_permit_envelope_without_commander_context_is_an_orphan(
        tmp_path, monkeypatch):
    cli = importlib.import_module('novel.cli.main')
    permit = _ready_permit(str(tmp_path.resolve()))
    env = execution_permit.env_with_permit(permit, 0, {})
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(
        cli.commander_context.COMMANDER_RUN_ID_ENV, raising=False)
    monkeypatch.delenv(
        cli.commander_context.COMMANDER_CONTRACT_SHA256_ENV, raising=False)
    monkeypatch.delenv(admission.CLAIM_ID_ENV, raising=False)

    with pytest.raises(TerminalState, match='脱离 Commander contract'):
        cli._require_commander_execution_permit(
            SimpleNamespace(
                base=permit.base, sid=permit.sid, node=permit.node),
            permit.flags, None)


def test_plain_direct_run_without_unresolved_keeps_legacy_path_reachable(
        tmp_path, monkeypatch):
    cli = importlib.import_module('novel.cli.main')
    base = str(tmp_path.resolve())
    for name in (
        cli.commander_context.COMMANDER_RUN_ID_ENV,
        cli.commander_context.COMMANDER_CONTRACT_SHA256_ENV,
        admission.CLAIM_ID_ENV,
        execution_permit.PERMIT_JSON_ENV,
        execution_permit.STEP_INDEX_ENV,
    ):
        monkeypatch.delenv(name, raising=False)

    class ReachedRunContext(RuntimeError):
        pass

    monkeypatch.setattr(
        cli.RunContext, 'open',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ReachedRunContext()))
    args = cli._parser().parse_args([
        'run', 'stage1.partition', '42', '--check-only', '--base', base])

    with pytest.raises(ReachedRunContext):
        cli._cmd_run(args)


def test_stage0_source_guards_the_derived_sid_and_rejects_explicit_mismatch(
        tmp_path, monkeypatch):
    cli = importlib.import_module('novel.cli.main')
    base = str(tmp_path.resolve())
    source = tmp_path / '平台_测试书_42.txt'
    source.write_text('正文', encoding='utf-8')
    admission.open_gate(base, 'parent-source-orphan', '42')
    for name in (
        cli.commander_context.COMMANDER_RUN_ID_ENV,
        cli.commander_context.COMMANDER_CONTRACT_SHA256_ENV,
        admission.CLAIM_ID_ENV,
        execution_permit.PERMIT_JSON_ENV,
        execution_permit.STEP_INDEX_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        cli, '_run_stage0_source',
        lambda *_args, **_kwargs: pytest.fail('派生 sid unresolved 前不得进入 producer'))

    mismatched = cli._parser().parse_args([
        'run', 'stage0.remove', 'old-sid', '--source', str(source),
        '--base', base])
    with pytest.raises(TerminalState, match='与显式 sid=.*不一致'):
        cli._cmd_run(mismatched)

    derived = cli._parser().parse_args([
        'run', 'stage0.remove', '--source', str(source), '--base', base])
    with pytest.raises(TerminalState, match='sid=.42.*durable unresolved'):
        cli._cmd_run(derived)


def test_zero_cost_hash_bound_reconcile_closes_a_restart_orphan(tmp_path):
    base = str(tmp_path)
    admission.open_gate(base, 'parent-reconcile', '42')
    subject = admission.reconcile_subject(base, 'parent-reconcile')

    done = admission.reconcile_gate(
        base, 'parent-reconcile', actor='恢复值守员',
        reason='已核实服务崩溃且不存在残留 Commander/节点进程',
        subject_sha256=subject)

    assert done.closed is True and done.terminal_kind == 'reconciled'
    assert admission.unresolved(base, '42') == ()
    [event] = [item for item in admission.read_events(base)
               if item.kind == 'reconciled']
    assert event.actor == '恢复值守员'
    assert event.reason == '已核实服务崩溃且不存在残留 Commander/节点进程'
    assert event.subject_sha256 == subject and event.force is False


def test_owner_lease_blocks_reconcile_across_process_and_crash_auto_releases(
        tmp_path):
    base = str(tmp_path)
    parent_run_id = 'parent-owner-crash'
    admission.open_gate(base, parent_run_id, '42')
    subject = admission.reconcile_subject(base, parent_run_id)
    code = (
        'import sys\n'
        'from novel.orchestrator import admission\n'
        'with admission.owner_lease(sys.argv[1], sys.argv[2]):\n'
        '    print("ready", flush=True)\n'
        '    sys.stdin.read(1)\n'
    )
    process = subprocess.Popen(
        [sys.executable, '-c', code, base, parent_run_id],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True)
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == 'ready'
        for force in (False, True):
            with pytest.raises(TerminalState, match='owner lease'):
                admission.reconcile_gate(
                    base, parent_run_id, actor='恢复值守员',
                    reason='live owner 不得被人工覆盖',
                    subject_sha256=subject, force=force)
        assert admission.snapshot(base, parent_run_id).closed is False
    finally:
        process.kill()
        process.wait(timeout=5)

    lock_path = admission.owner_lease_path(base, parent_run_id).with_name(
        admission.owner_lease_path(base, parent_run_id).name + '.lock')
    assert lock_path.exists(), '残留 lock 文件允许存在，但不得被当作 live truth'
    done = admission.reconcile_gate(
        base, parent_run_id, actor='恢复值守员',
        reason='owner 进程已退出，OS lease 已自动释放',
        subject_sha256=subject)
    assert done.terminal_kind == 'reconciled'


def test_reconcile_refuses_live_claim_by_default_and_force_invalidates_it(
        tmp_path):
    base = str(tmp_path)
    admission.open_gate(base, 'parent-force', '42')
    claim = admission.claim(
        base, 'parent-force', '42', node='stage4.design',
        claim_sha256='2' * 64, claim_id='orphan-claim')
    subject = admission.reconcile_subject(base, 'parent-force')

    with pytest.raises(TerminalState, match='默认拒绝 reconcile'):
        admission.reconcile_gate(
            base, 'parent-force', actor='恢复值守员',
            reason='尚未确认孤儿执行停止', subject_sha256=subject)
    assert admission.snapshot(base, 'parent-force').closed is True

    forced = admission.reconcile_gate(
        base, 'parent-force', actor='恢复值守员',
        reason='已从操作系统核实相关进程树不存在，接受强制销账风险',
        subject_sha256=subject, force=True)
    assert forced.terminal_kind == 'reconciled'
    with pytest.raises(TerminalState, match='已由 reconciled 收口'):
        admission.require_active_claim(
            base, 'parent-force', '42', 'stage4.design',
            env={admission.CLAIM_ID_ENV: claim.claim_id})
    # 强制销账后还有 release，正是“旧进程其实活过”的反证；旧 seal 必须失效，
    # 但 closed gate 也不能因此恢复旧 claim 的执行权。
    admission.release(base, claim)
    invalidated = admission.snapshot(base, 'parent-force')
    assert invalidated.terminal_kind is None
    assert invalidated.reconcile_invalidated is True
    assert admission.unresolved(base, '42') == (invalidated,)
    with pytest.raises(TerminalState, match='反证'):
        admission.require_active_claim(
            base, 'parent-force', '42', 'stage4.design',
            env={admission.CLAIM_ID_ENV: claim.claim_id})

    rebound = admission.reconcile_subject(base, 'parent-force')
    repaired = admission.reconcile_gate(
        base, 'parent-force', actor='恢复值守员',
        reason='已复核迟到 release，确认当前已无残留进程',
        subject_sha256=rebound)
    assert repaired.terminal_kind == 'reconciled'
    assert repaired.reconcile_invalidated is False
    assert admission.unresolved(base, '42') == ()


def test_reconcile_joint_cas_serializes_concurrent_beacon_end_and_release(
        tmp_path, monkeypatch):
    base = str(tmp_path)
    parent_run_id = 'parent-reconcile-race'
    admission.open_gate(base, parent_run_id, '42')
    claim = admission.claim(
        base, parent_run_id, '42', node='stage4.design',
        claim_sha256='3' * 64, claim_id='reconcile-race-claim')
    beacon.append(base, ProgressEvent(
        ts=1, sid='42', run_id=parent_run_id,
        execution_id='reconcile-race-execution', node='stage4.design',
        phase='start'))
    subject = admission.reconcile_subject(base, parent_run_id)

    # 记录 reconcile 当前线程实际持有的事务路径；联合 CAS 必须严格按
    # admission -> beacon 顺序覆盖“最终重算 subject -> append reconciled”。
    real_transaction = admission.append_transaction
    held = threading.local()

    @contextmanager
    def tracked_transaction(path):
        with real_transaction(path):
            paths = getattr(held, 'paths', [])
            paths.append(Path(path).resolve())
            held.paths = paths
            try:
                yield
            finally:
                paths.pop()

    monkeypatch.setattr(admission, 'append_transaction', tracked_transaction)
    real_open_executions = beacon.open_executions
    open_reads = 0
    drift = threading.Event()
    drift_attempted = threading.Event()
    drift_finished = threading.Event()
    failures: list[BaseException] = []

    def finish_old_execution():
        try:
            assert drift.wait(5)
            drift_attempted.set()
            beacon.append(base, ProgressEvent(
                ts=2, sid='42', run_id=parent_run_id,
                execution_id='reconcile-race-execution',
                node='stage4.design', phase='end', exit_code=0))
            admission.release(base, claim)
        except BaseException as exc:  # 测试线程异常必须送回主线程
            failures.append(exc)
        finally:
            drift_finished.set()

    thread = threading.Thread(target=finish_old_execution)
    thread.start()

    def observing_open_executions(base_arg, parent_arg):
        nonlocal open_reads
        result = real_open_executions(base_arg, parent_arg)
        open_reads += 1
        if open_reads == 2:
            assert held.paths == [
                admission.admission_path(base).resolve(),
                beacon.beacon_path(base).resolve(),
            ]
            drift.set()
            assert drift_attempted.wait(5)
            assert not drift_finished.is_set()
        return result

    monkeypatch.setattr(beacon, 'open_executions', observing_open_executions)
    done = admission.reconcile_gate(
        base, parent_run_id, actor='恢复值守员',
        reason='已核实旧进程树不存在，接受强制销账风险',
        subject_sha256=subject, force=True)
    thread.join(5)

    assert not thread.is_alive()
    assert failures == []
    assert beacon.open_executions(base, parent_run_id) == ()
    after_drift = admission.snapshot(base, parent_run_id)
    assert after_drift.active_claims == ()
    assert after_drift.terminal_kind is None
    assert after_drift.reconcile_invalidated is True
    assert admission.unresolved(base, '42') == (after_drift,)

    rebound = admission.reconcile_subject(base, parent_run_id)
    done = admission.reconcile_gate(
        base, parent_run_id, actor='恢复值守员',
        reason='已复核并发迟到的 end/release', subject_sha256=rebound)
    assert done.terminal_kind == 'reconciled'


def test_any_new_parent_beacon_lifecycle_invalidates_reconciled_seal(tmp_path):
    base = str(tmp_path)
    parent_run_id = 'parent-late-beacon'
    admission.open_gate(base, parent_run_id, '42')
    subject = admission.reconcile_subject(base, parent_run_id)
    admission.reconcile_gate(
        base, parent_run_id, actor='恢复值守员', reason='首次确认无残留进程',
        subject_sha256=subject)

    # 即使迟到 execution 已经 start/end 成对闭合，“它曾在 seal 后出现”本身也
    # 反证首次人工核查；只绑定 open 集合会静默漏掉这一事实。
    beacon.append(base, ProgressEvent(
        ts=10, sid='42', run_id=parent_run_id,
        execution_id='late-complete', node='stage2.global', phase='start'))
    beacon.append(base, ProgressEvent(
        ts=11, sid='42', run_id=parent_run_id,
        execution_id='late-complete', node='stage2.global',
        phase='end', exit_code=0))

    invalidated = admission.snapshot(base, parent_run_id)
    assert invalidated.terminal_kind is None
    assert invalidated.reconcile_invalidated is True
    assert admission.unresolved(base, '42') == (invalidated,)
    rebound = admission.reconcile_subject(base, parent_run_id)
    assert rebound != subject
    assert admission.reconcile_gate(
        base, parent_run_id, actor='恢复值守员',
        reason='已复核迟到但闭合的 execution',
        subject_sha256=rebound).terminal_kind == 'reconciled'


def test_linked_gate_seal_tracks_outer_beacons_not_admission_id(tmp_path):
    base = str(tmp_path)
    admission_id = 'fresh-epoch-linked-t2'
    outer_run_id = 'agent-outer-linked'
    admission.open_gate(
        base, admission_id, '42', lifecycle_run_id=outer_run_id)
    subject = admission.reconcile_subject(base, admission_id)
    admission.reconcile_gate(
        base, admission_id, actor='恢复值守员', reason='首次确认无残留进程',
        subject_sha256=subject)

    # admission id 下的无关 beacon 不属于这条 outer lifecycle，不能污染 seal。
    for phase, ts in (('start', 10), ('end', 11)):
        beacon.append(base, ProgressEvent(
            ts=ts, sid='42', run_id=admission_id,
            execution_id='wrong-child-beacon', node='stage4.design',
            phase=phase, exit_code=0 if phase == 'end' else None))
    assert admission.snapshot(
        base, admission_id).terminal_kind == 'reconciled'

    # outer 下哪怕迟到的是完整 start/end，也会反证旧人工核查。
    for phase, ts in (('start', 12), ('end', 13)):
        beacon.append(base, ProgressEvent(
            ts=ts, sid='42', run_id=outer_run_id,
            execution_id='outer-late-beacon', node='stage4.design',
            phase=phase, exit_code=0 if phase == 'end' else None))
    invalidated = admission.snapshot(base, admission_id)
    assert invalidated.terminal_kind is None
    assert invalidated.reconcile_invalidated is True
    assert admission.unresolved(base, '42') == (invalidated,)


def test_old_v1_reconcile_seal_is_invalidated_by_v3_subject_and_can_reseal(
        tmp_path):
    """升级不能继续信任只看 admission id beacon 的历史 v2 subject。"""
    base = str(tmp_path)
    admission_id = 'legacy-v1-reconcile'
    common = {
        'wire_version': 1, 'parent_run_id': admission_id, 'sid': '42',
        'claim_id': None, 'node': None, 'claim_sha256': None,
        'actor': None, 'reason': None, 'subject_sha256': None, 'force': None,
    }
    opened = {**common, 'kind': 'opened', 'ts': 1}
    old_payload = {
        'protocol': 'novel-commander-admission-reconcile/v2',
        'parent_run_id': admission_id, 'sid': '42',
        'events': [opened], 'beacon_lifecycle': [],
    }
    old_subject = hashlib.sha256(json.dumps(
        old_payload, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')).hexdigest()
    closed = {**common, 'kind': 'closed', 'ts': 2}
    reconciled = {
        **common, 'kind': 'reconciled', 'ts': 3,
        'actor': '旧值守员', 'reason': '旧协议核查',
        'subject_sha256': old_subject, 'force': False,
    }
    path = admission.admission_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(
        json.dumps(row, ensure_ascii=False, sort_keys=True,
                   separators=(',', ':')) + '\n'
        for row in (opened, closed, reconciled)), encoding='utf-8')

    invalidated = admission.snapshot(base, admission_id)
    assert invalidated.terminal_kind is None
    assert invalidated.reconcile_invalidated is True
    assert admission.unresolved(base, '42') == (invalidated,)
    current_subject = admission.reconcile_subject(base, admission_id)
    assert current_subject != old_subject

    done = admission.reconcile_gate(
        base, admission_id, actor='新值守员', reason='按 v3 重新核查',
        subject_sha256=current_subject)
    assert done.terminal_kind == 'reconciled'
    assert done.lifecycle_run_id == admission_id
    assert admission.read_events(base)[-1].wire_version == 2


def test_hash_bound_tail_repair_quarantines_original_and_fsyncs_prefix(tmp_path):
    base = str(tmp_path)
    admission.open_gate(base, 'parent-tail-repair', '42')
    path = admission.admission_path(base)
    committed = path.read_bytes()
    tail = b'{"wire_version":1,"parent_run_id":"partial"'
    path.write_bytes(committed + tail)
    subject = admission.tail_repair_subject(base)

    done = admission.repair_tail(
        base, actor='恢复值守员', reason='核对 committed prefix 后隔离 kill 坏尾',
        subject_sha256=subject.subject_sha256)

    assert done == subject
    assert path.read_bytes() == committed
    assert subject.quarantine_path.read_bytes() == tail
    records = admission.read_tail_repair_records(base)
    assert [record.kind for record in records] == ['authorized', 'applied']
    assert all(record.subject_sha256 == subject.subject_sha256
               for record in records)
    # 恢复不是代签 gate：原 committed opened 仍须走正常 typed reconcile。
    assert admission.unresolved(base, '42')[0].parent_run_id == 'parent-tail-repair'


def test_tail_repair_refuses_live_owner_without_touching_tail(tmp_path):
    base = str(tmp_path)
    parent_run_id = 'parent-tail-live-owner'
    admission.open_gate(base, parent_run_id, '42')
    path = admission.admission_path(base)
    damaged = path.read_bytes() + b'{"partial":'
    path.write_bytes(damaged)
    subject = admission.tail_repair_subject(base)

    with admission.owner_lease(base, parent_run_id):
        with pytest.raises(TerminalState, match='owner lease'):
            admission.repair_tail(
                base, actor='恢复值守员', reason='live owner 时不得截账',
                subject_sha256=subject.subject_sha256)
    assert path.read_bytes() == damaged
    assert admission.read_tail_repair_records(base) == ()

    admission.repair_tail(
        base, actor='恢复值守员', reason='owner 已退出后精确恢复',
        subject_sha256=subject.subject_sha256)
    assert path.read_bytes().endswith(b'\n')


def test_tail_repair_refuses_stale_prefix_tail_subject_without_mutation(tmp_path):
    base = str(tmp_path)
    admission.open_gate(base, 'parent-tail-stale', '42')
    path = admission.admission_path(base)
    path.write_bytes(path.read_bytes() + b'{"partial":1')
    stale = admission.tail_repair_subject(base)
    changed = path.read_bytes() + b'x'
    path.write_bytes(changed)

    with pytest.raises(TerminalState, match='subject SHA 已变化'):
        admission.repair_tail(
            base, actor='恢复值守员', reason='旧模板不得截新尾',
            subject_sha256=stale.subject_sha256)
    assert path.read_bytes() == changed
    assert not stale.quarantine_path.exists()


def test_tail_repair_retry_rejects_actor_reason_drift_between_phases(
        tmp_path, monkeypatch):
    base = str(tmp_path)
    admission.open_gate(base, 'parent-tail-phase', '42')
    path = admission.admission_path(base)
    committed = path.read_bytes()
    path.write_bytes(committed + b'{"partial":')
    subject = admission.tail_repair_subject(base)
    real_write_record = admission._write_repair_record

    def fail_after_truncate(base_arg, subject_arg, *, kind, actor, reason):
        if kind == 'applied':
            raise RuntimeError('模拟 truncate fsync 后、applied 前掉电')
        return real_write_record(
            base_arg, subject_arg, kind=kind, actor=actor, reason=reason)

    monkeypatch.setattr(admission, '_write_repair_record', fail_after_truncate)
    with pytest.raises(RuntimeError, match='模拟'):
        admission.repair_tail(
            base, actor='值守员-A', reason='同一恢复决定',
            subject_sha256=subject.subject_sha256)
    assert path.read_bytes() == committed
    monkeypatch.setattr(admission, '_write_repair_record', real_write_record)

    with pytest.raises(TerminalState, match='actor/reason 漂移'):
        admission.repair_tail(
            base, actor='值守员-B', reason='另一恢复决定',
            subject_sha256=subject.subject_sha256)
    assert [record.kind for record in admission.read_tail_repair_records(base)] == [
        'authorized']

    admission.repair_tail(
        base, actor='值守员-A', reason='同一恢复决定',
        subject_sha256=subject.subject_sha256)
    assert [record.kind for record in admission.read_tail_repair_records(base)] == [
        'authorized', 'applied']


def test_reconcile_cli_has_read_only_template_then_typed_apply(
        tmp_path, capsys):
    cli = importlib.import_module('novel.cli.main')
    base = str(tmp_path)
    admission.open_gate(base, 'parent-cli-reconcile', '42')

    assert cli.main([
        'reconcile', '--base', base,
        '--admission-parent-run-id', 'parent-cli-reconcile']) == 0
    shown = __import__('json').loads(capsys.readouterr().out)
    assert shown['sid'] == '42'
    assert shown['admission_id'] == 'parent-cli-reconcile'
    assert shown['lifecycle_run_id'] == 'parent-cli-reconcile'
    assert shown['agent_run_id'] is None
    assert shown['apply_requires'] == [
        '--actor', '--reason', '--subject-sha256']

    assert cli.main([
        'reconcile', '--base', base,
        '--admission-parent-run-id', 'parent-cli-reconcile',
        '--actor', '恢复值守员', '--reason', '确认无残留进程',
        '--subject-sha256', shown['subject_sha256']]) == 0
    assert admission.snapshot(
        base, 'parent-cli-reconcile').terminal_kind == 'reconciled'


@pytest.mark.parametrize('argv', [
    ['queue', '42', '--base', 'unused', '--until', 'stage0'],
    ['queue-cancel', 'queue-1', '0', '--base', 'unused'],
    ['queue-resume', 'queue-1', '--base', 'unused'],
    ['reconcile', '--base', 'unused'],
    [
        'reconcile', '--base', 'unused',
        '--admission-parent-run-id', 'parent-control',
        '--actor', 'agent', '--reason', '不应在子上下文 apply',
        '--subject-sha256', 'a' * 64,
    ],
    [
        'reconcile', '--base', 'unused', '--admission-repair-tail',
        '--actor', 'agent', '--reason', '不应在子上下文 repair',
        '--subject-sha256', 'a' * 64,
    ],
    [
        'reconcile', '--base', 'unused',
        '--stage2-review-no-wire-session-run-id', 'stage2-no-wire-session',
        '--stage2-review-sid', '42',
        '--actor', 'agent', '--reason', '不应在子上下文恢复预算',
        '--subject-sha256', 'a' * 64,
    ],
])
def test_commander_dispatcher_refuses_control_plane_mutations_before_handler(
        monkeypatch, argv):
    cli = importlib.import_module('novel.cli.main')
    monkeypatch.setenv(
        cli.commander_context.COMMANDER_RUN_ID_ENV, 'parent-control')
    monkeypatch.setitem(
        cli._CMDS, argv[0],
        lambda _args: pytest.fail('Commander control mutation 不得进入 handler'))

    assert cli.main(argv) == 2


@pytest.mark.parametrize('argv', [
    ['queue-list', '--base', 'unused'],
    ['queue', '42', '--base', 'unused', '--until', 'stage0', '--plan-only'],
    [
        'reconcile', '--base', 'unused',
        '--admission-parent-run-id', 'parent-control',
    ],
    ['reconcile', '--base', 'unused', '--admission-repair-tail'],
    [
        'reconcile', '--base', 'unused',
        '--stage2-review-no-wire-session-run-id', 'stage2-no-wire-session',
        '--stage2-review-sid', '42',
    ],
])
def test_commander_dispatcher_keeps_control_plane_inspection_read_only(
        monkeypatch, argv):
    cli = importlib.import_module('novel.cli.main')
    monkeypatch.setenv(
        cli.commander_context.COMMANDER_RUN_ID_ENV, 'parent-control')
    called: list[str] = []
    monkeypatch.setitem(
        cli._CMDS, argv[0], lambda args: called.append(args.cmd) or 0)

    assert cli.main(argv) == 0
    assert called == [argv[0]]


def test_reconcile_cli_exposes_zero_cost_typed_tail_repair(tmp_path, capsys):
    cli = importlib.import_module('novel.cli.main')
    base = str(tmp_path)
    admission.open_gate(base, 'parent-cli-tail', '42')
    path = admission.admission_path(base)
    original = path.read_bytes()
    path.write_bytes(original + b'{"truncated":')
    assert cli.main([
        'reconcile', '--base', base, '--admission-repair-tail']) == 0
    shown = __import__('json').loads(capsys.readouterr().out)
    assert shown['committed_size'] == len(original)
    assert shown['tail_size'] == len(b'{"truncated":')
    assert shown['apply_requires'] == [
        '--actor', '--reason', '--subject-sha256']

    assert cli.main([
        'reconcile', '--base', base, '--admission-repair-tail',
        '--actor', '恢复值守员', '--reason', '隔离 non-LF 尾',
        '--subject-sha256', shown['subject_sha256']]) == 0
    assert path.read_bytes() == original
    assert Path(shown['quarantine_path']).read_bytes() == b'{"truncated":'


@pytest.mark.parametrize('force', [False, True])
def test_reconcile_cli_apply_never_overrides_same_live_agent_owner(
        tmp_path, force):
    cli = importlib.import_module('novel.cli.main')
    base = str(tmp_path)
    parent_run_id = 'parent-live-reconcile'
    admission.open_gate(base, parent_run_id, '42')
    subject = admission.reconcile_subject(base, parent_run_id)
    argv = [
        'reconcile', '--base', base,
        '--admission-parent-run-id', parent_run_id,
        '--actor', '恢复值守员', '--reason', '不应越过 live owner',
        '--subject-sha256', subject,
    ]
    if force:
        argv.append('--force')

    with admission.owner_lease(base, parent_run_id):
        with pytest.raises(TerminalState, match='owner lease'):
            cli._cmd_reconcile(cli._parser().parse_args(argv))
    current = admission.snapshot(base, parent_run_id)
    assert current.closed is False and current.terminal_kind is None


def test_execution_wait_treats_reconciled_as_external_unknown_takeover(tmp_path):
    base = str(tmp_path)
    parent_run_id = 'parent-external-takeover'
    admission.open_gate(base, parent_run_id, '42')
    subject = admission.reconcile_subject(base, parent_run_id)
    admission.reconcile_gate(
        base, parent_run_id, actor='另一服务值守员',
        reason='确认原服务进程已经失联', subject_sha256=subject)

    result = agent._wait_for_execution_closure(base, parent_run_id)

    assert result.timed_out is True
    assert result.timeout_reason is not None
    assert '外部人工 reconciled 接管' in result.timeout_reason
