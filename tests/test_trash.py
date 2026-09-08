#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""项目回收站：目标绑定、整目录移动、审批状态随书归档。"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from contracts.schema.orchestration import AgentProfile, PipelineAgentProfiles
from novel.commanders.base import CommanderInvocation
from novel.errors import (
    ApprovalFrozen, BookTrashFailed, SourceConflict, UpstreamMissing,
)
from novel.orchestrator import admission, agent, approvals, jobs, trash
from novel.orchestrator.jobs import JobConflict


_PROFILE = AgentProfile(
    driver='codex', model='gpt-5.6-sol', reasoning_effort='high',
    execution_mode='standard')
_PROFILES = PipelineAgentProfiles(
    commander=_PROFILE, executor_default=_PROFILE, executor_overrides={})


class _PlanningDriver:
    """trash 只测互斥；planner 冻结本地 invocation，绝不探 CLI 或发模型请求。"""

    def build_invocation(self, profile, prompt, *, repo, base, **_kwargs):
        del base
        return CommanderInvocation(
            argv=('offline-commander', '--model', profile.model),
            stdin_text=prompt, cwd=str(repo), env_overrides={}, profile=profile,
            protocol='trash-test-v1')

    def close(self):
        return None


@pytest.fixture(autouse=True)
def _offline_commander_planning(monkeypatch):
    monkeypatch.setattr(
        agent, 'create_commander_driver',
        lambda _profile, **_kwargs: _PlanningDriver())
    monkeypatch.setattr(
        agent, '_require_reproducible_code_version', lambda _version: None)


def _book(base, *, sid='1001', title='测试书'):
    from novel.work_units import initialize_workflow
    initialize_workflow(base)
    book = base / f'{title}_{sid}'
    (book / 'stage0').mkdir(parents=True)
    (book / 'stage0' / 'clean_text.txt').write_text('正文\n', encoding='utf-8')
    approval = base / '_service' / 'approvals' / f'{sid}.json'
    approval.parent.mkdir(parents=True, exist_ok=True)
    approval.write_text('{"decision":"human"}\n', encoding='utf-8')
    return book, approval


def _stale_incomplete(base, *, trash_id='1700000000-0badf00d'):
    """造一条断电半成品：书仍 live，审批只在回收站，且已过 sweep 宽限期。"""
    book, approval = _book(base)
    item = base / '_service' / 'trash' / trash_id
    item.mkdir(parents=True)
    (item / 'metadata.json').write_text(json.dumps({
        '_schema_version': 2, 'sid': '1001', 'title': '测试书',
        'original_dirname': book.name, 'trashed_at': 1,
        'approval_archived': True, 'complete': False,
    }, ensure_ascii=False), encoding='utf-8')
    (item / 'approval.json').write_text(
        approval.read_text(encoding='utf-8'), encoding='utf-8')
    approval.unlink()
    return book, approval, item


def test_execute_moves_the_whole_project_and_approval_into_recoverable_trash(tmp_path):
    book, approval = _book(tmp_path)

    result = trash.execute(tmp_path, '1001', '测试书', '1700000000-deadbeef')

    item = tmp_path / '_service' / 'trash' / '1700000000-deadbeef'
    assert not book.exists() and not approval.exists()
    assert (item / 'book' / 'stage0' / 'clean_text.txt').read_text(encoding='utf-8') == '正文\n'
    assert (item / 'approval.json').read_text(encoding='utf-8') == '{"decision":"human"}\n'
    metadata = json.loads((item / 'metadata.json').read_text(encoding='utf-8'))
    assert metadata['sid'] == '1001' and metadata['title'] == '测试书'
    assert result.trash_rel == '_service/trash/1700000000-deadbeef/book'


def test_v3_trash_and_restore_keep_book_approval_and_service_runs_together(
        tmp_path):
    book, approval = _book(tmp_path)
    service_run = (
        tmp_path / '_service' / 'runs' / '1001' / 'stage5' / 'run-a')
    service_run.mkdir(parents=True)
    (service_run / 'run.json').write_text(
        '{"state":"committed"}\n', encoding='utf-8')
    trash_id = '1700000000-f00dbabe'

    trash.execute(tmp_path, '1001', '测试书', trash_id)

    item = tmp_path / '_service' / 'trash' / trash_id
    assert not book.exists() and not approval.exists() and not service_run.exists()
    assert (item / 'service_runs' / 'stage5' / 'run-a' / 'run.json').is_file()
    proof = trash.complete_item_proof(tmp_path, trash_id, max_entries=64)
    assert proof is not None and proof['sid'] == '1001'

    restored = trash.restore(tmp_path, trash_id, '测试书')

    assert restored['sid'] == '1001'
    assert book.is_dir() and approval.is_file() and service_run.is_dir()
    assert not item.exists()
    receipt = (
        tmp_path / '_service' / 'trash_receipts'
        / f'{trash_id}.restore.json')
    restored_receipt = json.loads(receipt.read_text(encoding='utf-8'))
    assert restored_receipt['state'] == 'committed'


class _SimulatedRestoreHardKill(BaseException):
    """Bypass ordinary compensation exactly like process termination."""


def _trashed_with_service_run(tmp_path, trash_id):
    book, approval = _book(tmp_path)
    service_run = (
        tmp_path / '_service' / 'runs' / '1001' / 'stage5' / 'run-a')
    service_run.mkdir(parents=True)
    (service_run / 'run.json').write_text(
        '{"state":"committed"}\n', encoding='utf-8')
    trash.execute(tmp_path, '1001', '测试书', trash_id)
    return book, approval, service_run, tmp_path / '_service' / 'trash' / trash_id


def test_restore_prepared_receipt_reconciles_a_hard_kill_per_surface(
        tmp_path, monkeypatch):
    trash_id = '1700000000-f00dba01'
    book, approval, service_run, item = _trashed_with_service_run(
        tmp_path, trash_id)
    plan = trash.preview_restore(tmp_path, trash_id, '测试书', now=123)

    def kill_after_runs(point):
        if point == 'after_service_runs':
            raise _SimulatedRestoreHardKill('kill after first surface')

    monkeypatch.setattr(trash, '_restore_fault', kill_after_runs)
    with pytest.raises(_SimulatedRestoreHardKill, match='first surface'):
        trash.apply_restore(tmp_path, plan)

    receipt_path = (
        tmp_path / '_service' / 'trash_receipts'
        / f'{trash_id}.restore.json')
    prepared = json.loads(receipt_path.read_text(encoding='utf-8'))
    assert prepared['state'] == 'prepared'
    assert [surface['name'] for surface in prepared['surfaces']] == [
        'service_runs', 'approval', 'book']
    assert service_run.is_dir() and not (item / 'service_runs').exists()
    assert not book.exists() and not approval.exists()
    assert (item / 'book').is_dir() and (item / 'approval.json').is_file()

    monkeypatch.setattr(trash, '_restore_fault', lambda _point: None)
    restored = trash.reconcile_restore(
        tmp_path, trash_id, expected_title='测试书')
    assert restored['sid'] == '1001'
    assert book.is_dir() and approval.is_file() and service_run.is_dir()
    assert not item.exists()
    assert json.loads(receipt_path.read_text(encoding='utf-8'))[
        'state'] == 'committed'
    assert trash.reconcile_restore(
        tmp_path, trash_id, expected_title='测试书') == restored


def test_sweep_automatically_finishes_a_proved_restore_hard_kill(
        tmp_path, monkeypatch):
    trash_id = '1700000000-f00dba02'
    book, approval, service_run, item = _trashed_with_service_run(
        tmp_path, trash_id)
    plan = trash.preview_restore(tmp_path, trash_id, '测试书', now=123)

    def kill_after_approval(point):
        if point == 'after_approval':
            raise _SimulatedRestoreHardKill('kill after second surface')

    monkeypatch.setattr(trash, '_restore_fault', kill_after_approval)
    with pytest.raises(_SimulatedRestoreHardKill):
        trash.apply_restore(tmp_path, plan)

    monkeypatch.setattr(trash, '_restore_fault', lambda _point: None)
    assert trash.sweep_incomplete(tmp_path) == [trash_id]
    assert book.is_dir() and approval.is_file() and service_run.is_dir()
    assert not item.exists()


def test_service_startup_reconcile_uses_the_cli_boundary_and_is_bounded(
        tmp_path, monkeypatch):
    trash_id = '1700000000-f00dba04'
    book, approval, service_run, _item = _trashed_with_service_run(
        tmp_path, trash_id)
    plan = trash.preview_restore(tmp_path, trash_id, '测试书', now=123)

    def kill_after_runs(point):
        if point == 'after_service_runs':
            raise _SimulatedRestoreHardKill('kill')

    monkeypatch.setattr(trash, '_restore_fault', kill_after_runs)
    with pytest.raises(_SimulatedRestoreHardKill):
        trash.apply_restore(tmp_path, plan)
    monkeypatch.setattr(trash, '_restore_fault', lambda _point: None)
    resumed_plan = trash.preview_restore(
        tmp_path, trash_id, '测试书', now=456)
    assert resumed_plan.metadata_sha256 == plan.metadata_sha256
    assert resumed_plan.manifest_file_sha256 == plan.manifest_file_sha256
    seen_argv = []

    def child(argv, _cwd, _env):
        seen_argv.append(list(argv))
        restored = trash.restore(
            tmp_path, argv[argv.index('--trash-id') + 1],
            argv[argv.index('--expected-title') + 1])
        return 0, json.dumps(restored, ensure_ascii=False).encode('utf-8')

    result = trash.reconcile_pending_restores(
        tmp_path, spawn=child, max_receipts=8)
    assert result.examined == 1
    assert result.reconciled == (trash_id,) and result.blocked == ()
    assert seen_argv and 'novel.cli.trash_restore' in seen_argv[0]
    assert book.is_dir() and approval.is_file() and service_run.is_dir()
    receipts = tmp_path / '_service' / 'trash_receipts'
    (receipts / 'unrelated.json').write_text('{}\n', encoding='utf-8')
    bounded = trash.reconcile_pending_restores(
        tmp_path, spawn=lambda *_args: pytest.fail('超限扫描不得启动 child'),
        max_receipts=1)
    assert bounded.reconciled == ()
    assert bounded.blocked == ('scan_limit_exceeded',)


def test_restore_reconcile_refuses_a_duplicate_surface_without_moving_more(
        tmp_path, monkeypatch):
    trash_id = '1700000000-f00dba03'
    book, approval, service_run, item = _trashed_with_service_run(
        tmp_path, trash_id)
    plan = trash.preview_restore(tmp_path, trash_id, '测试书', now=123)

    def kill_after_runs(point):
        if point == 'after_service_runs':
            raise _SimulatedRestoreHardKill('kill')

    monkeypatch.setattr(trash, '_restore_fault', kill_after_runs)
    with pytest.raises(_SimulatedRestoreHardKill):
        trash.apply_restore(tmp_path, plan)

    duplicate = item / 'service_runs' / 'stage5' / 'run-a'
    duplicate.mkdir(parents=True)
    (duplicate / 'run.json').write_text(
        '{"state":"committed"}\n', encoding='utf-8')
    monkeypatch.setattr(trash, '_restore_fault', lambda _point: None)

    with pytest.raises(BookTrashFailed, match='两侧同时存在'):
        trash.reconcile_restore(
            tmp_path, trash_id, expected_title='测试书')
    assert not book.exists() and not approval.exists()
    assert (item / 'book').is_dir() and (item / 'approval.json').is_file()
    assert service_run.is_dir() and duplicate.is_dir()
    assert trash.sweep_incomplete(tmp_path) == [], \
        '自动 reconcile 遇到歧义必须留盘，不能转交 legacy delete sweep 猜测'


def test_purge_resumes_when_process_dies_after_rmtree_before_commit(
        tmp_path, monkeypatch):
    _book(tmp_path)
    trash_id = '1700000000-a11ce123'
    trash.execute(tmp_path, '1001', '测试书', trash_id)
    proof = trash.complete_item_proof(tmp_path, trash_id, max_entries=64)
    assert proof is not None
    item = tmp_path / '_service' / 'trash' / trash_id
    receipt = tmp_path / '_service' / 'trash_receipts' / f'{trash_id}.json'

    original_rmtree = trash.shutil.rmtree
    interrupted = False

    def rmtree_then_interrupt(path, *args, **kwargs):
        nonlocal interrupted
        result = original_rmtree(path, *args, **kwargs)
        if Path(path) == item and not interrupted:
            interrupted = True
            raise RuntimeError('fault after rmtree')
        return result

    monkeypatch.setattr(trash.shutil, 'rmtree', rmtree_then_interrupt)
    with pytest.raises(RuntimeError, match='fault after rmtree'):
        trash.purge_complete_item(
            tmp_path, trash_id,
            expected_manifest_sha256=str(proof['manifest_file_sha256']),
            max_entries=64)

    assert not item.exists()
    assert json.loads(receipt.read_text(encoding='utf-8'))['state'] == 'prepared'

    monkeypatch.setattr(trash.shutil, 'rmtree', original_rmtree)
    completed = trash.purge_complete_item(
        tmp_path, trash_id,
        expected_manifest_sha256=str(proof['manifest_file_sha256']),
        max_entries=64)
    assert completed == receipt
    assert json.loads(receipt.read_text(encoding='utf-8'))['state'] == 'committed'
    assert trash.purge_complete_item(
        tmp_path, trash_id,
        expected_manifest_sha256=str(proof['manifest_file_sha256']),
        max_entries=64) == receipt


def test_purge_does_not_treat_unproved_missing_item_as_success(tmp_path):
    trash_id = '1700000000-a11ce124'

    with pytest.raises(BookTrashFailed, match='没有 prepared proof'):
        trash.purge_complete_item(
            tmp_path, trash_id,
            expected_manifest_sha256='a' * 64,
            max_entries=64)


def test_execute_holds_the_same_approval_file_lock_for_both_moves(
        tmp_path, monkeypatch):
    book, approval = _book(tmp_path)
    events = []

    @contextmanager
    def observed_lock(base, sid):
        events.append(('enter', base, sid, approval.exists(), book.exists()))
        yield
        events.append(('exit', base, sid, approval.exists(), book.exists()))

    monkeypatch.setattr(approvals, 'approval_exclusion_lock', observed_lock)
    trash.execute(tmp_path, '1001', '测试书', '1700000000-c001d00d')

    assert events == [
        ('enter', tmp_path, '1001', True, True),
        ('exit', tmp_path, '1001', False, False),
    ]


def test_title_bound_confirmation_refuses_without_moving_anything(tmp_path):
    book, approval = _book(tmp_path)

    with pytest.raises(SourceConflict, match='当前 sid=1001 实际是《测试书》'):
        trash.execute(tmp_path, '1001', '另一本文', '1700000000-deadbeef')

    assert book.is_dir() and approval.is_file()
    assert not (tmp_path / '_service' / 'trash').exists()


def test_http_route_reaches_the_cli_subprocess_and_removes_the_corpus_entry(tmp_path):
    fastapi = pytest.importorskip(
        'fastapi', reason='没装 fastapi ⇒ HTTP 删除闭环没在查，不是查过没事')
    from fastapi.testclient import TestClient

    from novel.api.app import UI_HEADER, create_app

    book, _approval = _book(tmp_path)
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(
            '/api/novels/1001/trash',
            headers={UI_HEADER: '1'},
            json={'expected_title': '测试书'},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body['sid'] == '1001' and body['title'] == '测试书'
    assert not book.exists()
    assert (tmp_path / body['trash_rel']).is_dir()


def test_trash_reserves_the_book_before_an_agent_can_start(tmp_path, monkeypatch):
    """删除拿锁与检查 agent 之间插入一次启动；后到的 agent 必须明确冲突。"""
    _book(tmp_path)
    original_queue_view = agent.queue_view
    attempted: list[Exception] = []
    spawn_called = threading.Event()

    def agent_spawn(invocation, env, log):
        del invocation, env, log
        spawn_called.set()
        raise AssertionError('删除已占书锁时不应进入 Commander spawn')

    def interleaved_queue_view(base):
        try:
            agent.start(base, '1001', authorized=True, egress_authorized=True,
                        profiles=_PROFILES, spawn=agent_spawn)
        except Exception as exc:  # 断言在调用完成后做，避免 monkeypatch 缝吞细节
            attempted.append(exc)
        return original_queue_view(base)

    monkeypatch.setattr(agent, 'queue_view', interleaved_queue_view)
    result = trash.run(
        tmp_path, '1001', '测试书',
        spawn=lambda argv, cwd, env: (0, b''),
    )

    assert result.sid == '1001'
    assert len(attempted) == 1 and isinstance(attempted[0], JobConflict)
    assert not spawn_called.is_set(), 'agent 在删除持有书锁时仍进入了写盘 worker'
    assert jobs.REGISTRY.conflicting_holder('1001') is None


def _spawn_that_really_moves(base, sid='1001', title='测试书', *, before=None):
    """把 `trash.execute` 当子进程用：**书要真的从语料库消失**，竞态才是真的。

    `before` 在移动之前跑一次，用来把「删除进行中，另一个人正好在写」这一刻做出来。
    """
    def go(argv, cwd, env):
        if before is not None:
            before()
        trash.execute(base, sid, title, argv[argv.index('--trash-id') + 1])
        return 0, b''
    return go


def _keep_pending(monkeypatch):
    """让入队的书一律滞留 pending。

    ⚠ 不能改 `MAX_CONCURRENT`：取值面下界是 1（`AgentQueueView` 校验），
    调成 0 会把用例炸在响应模型上而不是我们要测的那条竞态上。
    """
    monkeypatch.setattr(
        agent, '_admit',
        lambda key, run_id, registry: (_ for _ in ()).throw(
            agent.AgentBusy('用例把闸按住了')))


def test_a_book_deleted_mid_flight_cannot_be_started_from_the_queue_afterwards(
        tmp_path, monkeypatch):
    """⭐ **实测复现过的那条：书删完了，队列却把它带起来。**

    `trash.run` 开跑前只对 agent 队列拍了**一张快照**（`queue_view`）。spawn 期间
    新入队的这本书当时挡在书级作业锁外（`start` 那道 `conflicting_holder`），
    锁一还回去就轮到它——而此时书目录已经不在了。
    ⇒ 摘队列必须发生在**锁里**（`agent.drop_pending`）。
    """
    _book(tmp_path)
    original_admit = agent._admit
    original_queue_view = agent.queue_view
    _keep_pending(monkeypatch)
    started: list[str] = []

    def never_reached(invocation, env, log):
        del invocation, env, log
        started.append('起来了')
        raise AssertionError('已删除的书不应进入 Commander spawn')

    def interleaved_queue_view(base):
        snapshot = original_queue_view(base)          # 快照：此刻队列还是空的
        agent.enqueue(base, ['1001'], authorized=True, egress_authorized=True,
                      profiles=_PROFILES, spawn=never_reached)
        return snapshot

    monkeypatch.setattr(agent, 'queue_view', interleaved_queue_view)
    try:
        trash.run(tmp_path, '1001', '测试书',
                  spawn=_spawn_that_really_moves(tmp_path))
        monkeypatch.setattr(agent, 'queue_view', original_queue_view)
        assert [item.sid for item in agent.queue_view(tmp_path).pending] == [], \
            '书已经移入回收站，队列里还留着它 —— 一份会说谎的队列'
        monkeypatch.setattr(agent, '_admit', original_admit)
        agent._drain()
        assert not started, '对一本已经不在盘上的书起了 agent'
    finally:
        agent.drop_pending(tmp_path, '1001')


def test_a_gone_book_leaves_the_queue_instead_of_being_retried_forever(
        tmp_path, monkeypatch):
    """出队丢弃而不是挪到队尾：不存在的书不会因为等一会儿就回来。"""
    _book(tmp_path)
    original_admit = agent._admit
    _keep_pending(monkeypatch)
    agent.enqueue(tmp_path, ['1001'], authorized=True, egress_authorized=True,
                  profiles=_PROFILES, spawn=lambda *a: 0)
    try:
        assert [item.sid for item in agent.queue_view(tmp_path).pending] == ['1001']
        trash.execute(tmp_path, '1001', '测试书', '1700000000-cafebabe')
        monkeypatch.setattr(agent, '_admit', original_admit)
        agent._drain()
        assert [item.sid for item in agent.queue_view(tmp_path).pending] == [], \
            '一本已经不存在的书被挪到队尾，`_drain` 会永远对它重试'
        drops = agent.queue_drops_path(tmp_path).read_text(encoding='utf-8')
        assert '1001' in drops, '出队丢弃没有留下任何可查的痕迹'
    finally:
        agent.drop_pending(tmp_path, '1001')


def test_enqueue_refuses_a_book_that_is_not_in_the_corpus(tmp_path):
    """入队这一格就该拒：批量响应里如实占一条 rejected，不静默排一本幽灵书。"""
    view = agent.enqueue(tmp_path, ['1001'], authorized=True,
                         egress_authorized=True, profiles=_PROFILES,
                         spawn=lambda *a: 0)
    try:
        assert [item.sid for item in view.rejected] == ['1001']
        assert view.running == [] and view.pending == []
    finally:
        agent.drop_pending(tmp_path, '1001')


def test_start_refuses_a_book_that_is_not_in_the_corpus(tmp_path):
    with pytest.raises(UpstreamMissing):
        agent.start(tmp_path, '1001', authorized=True, egress_authorized=True,
                    profiles=_PROFILES, spawn=lambda *a: 0)


def test_approval_writes_are_refused_while_the_book_is_being_trashed(tmp_path):
    """⭐ 「书已删除、审批复活」：审批写在**父进程**，目录移动在子进程。

    子进程刚把 `<sid>.json` 移进回收站，父进程的 `approve_gate` 就能把同名文件
    重新写出来 —— 留下一本书不在了、审批却还活着的孤儿。
    """
    _book(tmp_path)
    seen: list[Exception] = []

    def someone_approves():
        try:
            approvals.reset(tmp_path, '1001')
        except Exception as exc:                      # noqa: BLE001
            seen.append(exc)

    trash.run(tmp_path, '1001', '测试书',
              spawn=_spawn_that_really_moves(tmp_path, before=someone_approves))

    assert len(seen) == 1 and isinstance(seen[0], ApprovalFrozen), seen
    assert not (tmp_path / '_service' / 'approvals' / '1001.json').exists(), \
        '删除完成之后，这本书的审批又被写了回来'


def test_approval_writes_work_again_after_the_deletion_finishes(tmp_path):
    """冻结只覆盖删除那一段，而且只覆盖这一本书。"""
    _book(tmp_path)
    _book(tmp_path, sid='1002', title='另一本')
    inside: list[str] = []

    def another_book_is_approved():
        approvals.reset(tmp_path, '1002')                 # 别的书不受影响
        inside.append('ok')

    trash.run(tmp_path, '1001', '测试书',
              spawn=_spawn_that_really_moves(tmp_path,
                                             before=another_book_is_approved))
    assert inside == ['ok'], '冻结一本书把别的书的审批也挡住了'
    with approvals.frozen_for_trash('1002'):
        with pytest.raises(ApprovalFrozen):
            approvals.reset(tmp_path, '1002')
    approvals.reset(tmp_path, '1002')                     # 出了 with 就恢复


def test_sweep_skips_a_book_level_writer_without_moving_the_archived_approval(
        tmp_path):
    """恢复器拿不到同书 writer 就整条跳过，不能先搬审批再发现冲突。"""
    _book_dir, approval, item = _stale_incomplete(tmp_path)
    registry = jobs.JobRegistry()
    key = jobs.active_key('1001', 'stage5.write')
    registry.acquire(key, 'live-job')
    try:
        assert trash.sweep_incomplete(tmp_path, registry=registry) == []
        assert (item / 'approval.json').is_file()
        assert not approval.exists()
        assert (item / 'metadata.json').is_file()
    finally:
        registry.release(key, 'live-job')

    assert trash.sweep_incomplete(tmp_path, registry=registry) == [item.name]
    assert approval.read_text(encoding='utf-8') == '{"decision":"human"}\n'


def test_sweep_skips_in_memory_and_durable_commander_writers(tmp_path):
    """内存 agent 锁与重启后 admission 都是 sweep 的关闭式冲突面。"""
    _book_dir, approval, item = _stale_incomplete(tmp_path)
    agent_key = jobs.agent_lock_key('1001')
    jobs.AGENT_REGISTRY.acquire(agent_key, 'live-agent')
    try:
        assert trash.sweep_incomplete(tmp_path) == []
        assert (item / 'approval.json').is_file() and not approval.exists()
    finally:
        jobs.AGENT_REGISTRY.release(agent_key, 'live-agent')

    parent_run_id = 'orphan-sweep-admission'
    admission.open_gate(tmp_path, parent_run_id, '1001')
    try:
        assert trash.sweep_incomplete(tmp_path) == []
        assert (item / 'approval.json').is_file() and not approval.exists()
    finally:
        admission.close_gate(tmp_path, parent_run_id)
        admission.mark_drained(tmp_path, parent_run_id)

    assert trash.sweep_incomplete(tmp_path) == [item.name]
    assert approval.read_text(encoding='utf-8') == '{"decision":"human"}\n'


def test_a_hard_interrupt_between_the_two_moves_is_swept_back(tmp_path):
    """⭐ 断电落在「审批已归档」与「书目录已移走」之间：书还活着，审批却在回收站。

    这不丢数据，它让**读数说不清**——那本书看起来从来没批过，而批准记录
    明明还在盘上。`sweep_incomplete` 按 `complete` 哨兵认出这一种并搬回去。
    """
    book, approval = _book(tmp_path)
    item = tmp_path / '_service' / 'trash' / '1700000000-0badf00d'
    item.mkdir(parents=True)
    (item / 'metadata.json').write_text(json.dumps({
        '_schema_version': 2, 'sid': '1001', 'title': '测试书',
        'original_dirname': book.name, 'trashed_at': 1,
        'approval_archived': True, 'complete': False,
    }, ensure_ascii=False), encoding='utf-8')
    (item / 'approval.json').write_text(
        approval.read_text(encoding='utf-8'), encoding='utf-8')
    approval.unlink()

    assert trash.sweep_incomplete(tmp_path) == ['1700000000-0badf00d']
    assert approval.read_text(encoding='utf-8') == '{"decision":"human"}\n'
    assert not item.exists(), '半成品条目没有清掉'
    assert book.is_dir(), 'sweep 不该碰仍然活着的书目录'


def test_sweep_never_touches_a_completed_entry(tmp_path):
    """有 `book/` 的条目是删除**成功**，哪怕它没有 `complete` 字段（旧条目）。"""
    _book(tmp_path)
    trash.execute(tmp_path, '1001', '测试书', '1700000000-deadbeef')
    item = tmp_path / '_service' / 'trash' / '1700000000-deadbeef'
    meta = json.loads((item / 'metadata.json').read_text(encoding='utf-8'))
    assert meta['complete'] is True
    meta.pop('complete')                                  # 冒充升级前的旧条目
    (item / 'metadata.json').write_text(
        json.dumps(meta, ensure_ascii=False), encoding='utf-8')

    assert trash.sweep_incomplete(tmp_path) == []
    assert (item / 'approval.json').is_file(), '成功条目的审批被搬回了 live'
    assert not (tmp_path / '_service' / 'approvals' / '1001.json').exists()


def test_sweep_leaves_a_delete_that_is_still_in_flight_alone(tmp_path):
    """⭐ **「还没写完」与「写到一半死了」在盘上长得一模一样。**

    一次正在进行的删除，在两次 `os.replace` 之间的那一瞬恰好就是半成品的形状。
    这时若把它的归档审批搬回 live，子进程随后的回滚条件
    （`not approval.exists()`）就被破坏了 —— 于是「回滚成功」与「审批被别人重写」
    再也分不开。⇒ 本进程发起的直接跳过，别的进程留下的靠宽限期区分。
    """
    book, approval = _book(tmp_path)
    fresh_id = trash.new_trash_id()
    item = tmp_path / '_service' / 'trash' / fresh_id
    item.mkdir(parents=True)
    (item / 'metadata.json').write_text(trash._metadata_json(
        '1001', '测试书', book.name, approval_archived=True, complete=False),
        encoding='utf-8')
    (item / 'approval.json').write_text(
        approval.read_text(encoding='utf-8'), encoding='utf-8')
    approval.unlink()

    # ① 本进程正在做这一条 ⇒ 一个字都不许碰。
    with trash._IN_FLIGHT_LOCK:
        trash._IN_FLIGHT.add(fresh_id)
    try:
        assert trash.sweep_incomplete(tmp_path) == []
    finally:
        with trash._IN_FLIGHT_LOCK:
            trash._IN_FLIGHT.discard(fresh_id)
    assert (item / 'approval.json').is_file()

    # ② 不在本进程的名单里，但**还很新**（别的进程可能正在做）⇒ 仍然不碰。
    assert trash.sweep_incomplete(tmp_path) == []
    assert (item / 'approval.json').is_file()
    assert not approval.exists()

    # ③ 过了宽限期才认定它死了。
    stale = json.loads((item / 'metadata.json').read_text(encoding='utf-8'))
    stale['trashed_at'] -= trash.STALE_AFTER_SECONDS + 1
    (item / 'metadata.json').write_text(
        json.dumps(stale, ensure_ascii=False), encoding='utf-8')
    assert trash.sweep_incomplete(tmp_path) == [fresh_id]
    assert approval.read_text(encoding='utf-8') == '{"decision":"human"}' + chr(10)
