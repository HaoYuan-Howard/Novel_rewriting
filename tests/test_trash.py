#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""项目回收站：目标绑定、整目录移动、审批状态随书归档。"""
from __future__ import annotations

import json
import threading

import pytest

from novel.errors import ApprovalFrozen, SourceConflict, UpstreamMissing
from novel.orchestrator import agent, approvals, jobs, trash
from novel.orchestrator.jobs import JobConflict


def _book(base, *, sid='1001', title='测试书'):
    book = base / f'{title}_{sid}'
    (book / 'stage0').mkdir(parents=True)
    (book / 'stage0' / 'clean_text.txt').write_text('正文\n', encoding='utf-8')
    approval = base / '_service' / 'approvals' / f'{sid}.json'
    approval.parent.mkdir(parents=True, exist_ok=True)
    approval.write_text('{"decision":"human"}\n', encoding='utf-8')
    return book, approval


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

    def agent_spawn(argv, cwd, env, log):
        spawn_called.set()
        return 0

    def interleaved_queue_view(base):
        try:
            agent.start(base, '1001', authorized=True, egress_authorized=True,
                        spawn=agent_spawn)
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

    def never_reached(argv, cwd, env, log):
        started.append('起来了')
        return 0

    def interleaved_queue_view(base):
        snapshot = original_queue_view(base)          # 快照：此刻队列还是空的
        agent.enqueue(base, ['1001'], authorized=True, egress_authorized=True,
                      spawn=never_reached)
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
                  spawn=lambda *a: 0)
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
                         egress_authorized=True, spawn=lambda *a: 0)
    try:
        assert [item.sid for item in view.rejected] == ['1001']
        assert view.running == [] and view.pending == []
    finally:
        agent.drop_pending(tmp_path, '1001')


def test_start_refuses_a_book_that_is_not_in_the_corpus(tmp_path):
    with pytest.raises(UpstreamMissing):
        agent.start(tmp_path, '1001', authorized=True, egress_authorized=True,
                    spawn=lambda *a: 0)


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
