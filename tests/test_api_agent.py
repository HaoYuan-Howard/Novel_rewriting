#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_api_agent.py —— `/api/agent/runs` 各端点真的走一遍 HTTP（fake spawn 注入）。

钉五件事：preview 与真跑同构（`R-11.8`）、202 拉起、列表与字节续读日志、
熄火闸、以及**两个方向的互斥**（agent 活 ⇒ 起作业 409；作业活 ⇒ 拉 agent 409）。

本批不需要语料、不需要模型配额。
"""
from __future__ import annotations

import threading

import pytest

fastapi = pytest.importorskip(
    'fastapi', reason='没装 fastapi ⇒ 这一批**没在查**，不是「查过没事」')
from fastapi.testclient import TestClient               # noqa: E402

from novel.api.app import UI_HEADER, create_app         # noqa: E402
from novel.orchestrator import agent                    # noqa: E402

UI = {UI_HEADER: '1'}


@pytest.fixture
def client(tmp_path):
    base = str(tmp_path)
    with TestClient(create_app(base)) as c:
        yield c, base


def _ok_spawn(argv, cwd, env, log):
    log.write(b'fake agent output\n')
    return 0


def test_preview_shows_the_exact_argv_and_prompt(client):
    c, base = client
    r = c.post('/api/agent/runs/preview', json={
        'sid': '42', 'authorized': True, 'egress_authorized': True,
    },
               headers=UI)
    assert r.status_code == 200
    d = r.json()
    assert d['authorized'] is True
    assert d['egress_authorized'] is True
    assert '付费授权已授予' in d['prompt']
    assert '正文外发授权已授予' in d['prompt']
    assert any('python.exe *' in rule for rule in d['argv'])
    # 请求入口的产品默认：不付费，但默认允许正文外发；prompt 仍把两项都显式写出。
    d2 = c.post('/api/agent/runs/preview', json={'sid': '42'}, headers=UI).json()
    assert d2['authorized'] is False and d2['egress_authorized'] is True
    assert '付费授权未授予' in d2['prompt']
    assert '正文外发授权已授予' in d2['prompt']

    # 外发授权可独立取消，不能被已勾选的付费授权覆盖。
    d3 = c.post('/api/agent/runs/preview', json={
        'sid': '42', 'authorized': True, 'egress_authorized': False,
    }, headers=UI).json()
    assert d3['authorized'] is True and d3['egress_authorized'] is False
    assert '付费授权已授予' in d3['prompt']
    assert '正文外发授权未授予' in d3['prompt']


def test_start_list_log_and_stop_gate(client, monkeypatch):
    c, base = client
    monkeypatch.setattr(agent, '_default_spawn', lambda run_id: _ok_spawn)

    r = c.post('/api/agent/runs', json={
        'sid': '42', 'authorized': True, 'egress_authorized': False,
    }, headers=UI)
    assert r.status_code == 202
    assert r.json()['start']['authorized'] is True
    assert r.json()['start']['egress_authorized'] is False
    run_id = r.json()['run_id']
    assert agent.wait(run_id, 10)

    runs = c.get('/api/agent/runs?sid=42').json()['runs']
    assert [r['state'] for r in runs] == ['ok']
    assert c.get('/api/agent/runs?sid=other').json()['runs'] == []

    log = c.get(f'/api/agent/runs/{run_id}/log?offset=0').json()
    assert 'fake agent output' in log['text']
    again = c.get(f"/api/agent/runs/{run_id}/log?offset={log['next_byte']}").json()
    assert again['text'] == ''

    # 已结束 ⇒ 没东西可熄 ⇒ 409（显式终态，不是 500）
    assert c.post(f'/api/agent/runs/{run_id}/stop', headers=UI).status_code == 409


def test_csrf_guard_covers_the_mutation(client):
    c, _ = client
    assert c.post('/api/agent/runs', json={'sid': '42'}).status_code == 403


def test_both_directions_of_the_mutex(client, monkeypatch):
    c, base = client
    gate = threading.Event()

    def slow(argv, cwd, env, log):
        gate.wait(10)
        return 0

    monkeypatch.setattr(agent, '_default_spawn', lambda run_id: slow)
    r = c.post('/api/agent/runs', json={
        'sid': '42', 'authorized': True, 'egress_authorized': True,
    }, headers=UI)
    assert r.status_code == 202
    other = None
    try:
        # agent 活着 ⇒ 起 API 作业 409（反向互斥在 jobs.start 里，路由层白拿）
        # ⚠ 必填的人工凭据要带全：`stage0.approve` 的 `required_flags` 缺一个就是 400
        #   （`validate_run` 在拼 argv 那一步拒），而那会让这条测试在**够不到互斥**的
        #   地方变绿/变红——它要测的是「一条本来能起的作业被锁挡住」。
        r2 = c.post('/api/jobs', json={
            'sid': '42', 'node': 'stage0.approve',
            'flags': ['--actor', 'tester', '--reason', '互斥用例'],
        }, headers=UI)
        assert r2.status_code == 409
        # 同一本书的第二个 agent 仍 409（一本书一把锁）
        r3 = c.post('/api/agent/runs', json={
            'sid': '42', 'authorized': True, 'egress_authorized': True,
        },
                     headers=UI)
        assert r3.status_code == 409
        # ⭐ 但**另一本书**起得来：锁按书分，异书两个 agent 谁也碰不到谁的目录。
        r4 = c.post('/api/agent/runs', json={
            'sid': '43', 'authorized': True, 'egress_authorized': True,
        }, headers=UI)
        assert r4.status_code == 202, '另一本书被挡住了——锁又变回全局一把了'
        other = r4.json()['run_id']
    finally:
        # ⚠ 收尾走 `wait` 不是 `stop`：这一批用的是 monkeypatch 进去的假 spawn，
        # `_PROCS` 里根本没有句柄，`stop` 会以「没有在跑的子进程」当场炸掉。
        gate.set()
        agent.wait(r.json()['run_id'], 10)
        if other:
            agent.wait(other, 10)


# ───────────────────── 批量入口与队列（并发放开之后的新面） ─────────────────────

def test_batch_starts_what_it_can_and_queues_the_rest(client, monkeypatch):
    """⭐ 一次交 N 本：闸内的立刻跑，超出的排队。**202 = 已受理，不是已跑完。**"""
    c, base = client
    monkeypatch.setattr(agent, 'MAX_CONCURRENT', 2)
    gate = threading.Event()

    def slow(argv, cwd, env, log):
        gate.wait(10)
        return 0

    monkeypatch.setattr(agent, '_default_spawn', lambda run_id: slow)
    started = []
    try:
        r = c.post('/api/agent/runs/batch', json={
            'sids': ['42', '43', '44'], 'authorized': True,
            'egress_authorized': True,
        }, headers=UI)
        assert r.status_code == 202
        d = r.json()
        assert {rec['start']['sid'] for rec in d['running']} == {'42', '43'}
        assert [p['sid'] for p in d['pending']] == ['44']
        assert d['pending'][0]['position'] == 1
        assert d['max_concurrent'] == 2
        assert d['rejected'] == []
        started = [rec['run_id'] for rec in d['running']]
    finally:
        gate.set()
        for run_id in started:
            agent.wait(run_id, 10)
        for rec in agent.records(base):
            agent.wait(rec.run_id, 10)


def test_the_queue_endpoint_publishes_the_gate(client):
    """前端要显示「3 个都在跑」就得从服务端拿这个数——**不许自己写死**。"""
    c, _ = client
    r = c.get('/api/agent/runs/queue')
    assert r.status_code == 200
    d = r.json()
    assert d['max_concurrent'] == agent.MAX_CONCURRENT
    assert d['running'] == [] and d['pending'] == []


def test_batch_rejects_a_book_that_already_has_an_agent(client, monkeypatch):
    """**部分失败不回滚**：重复那本进 `rejected`，其余照常受理。"""
    c, base = client
    gate = threading.Event()

    def slow(argv, cwd, env, log):
        gate.wait(10)
        return 0

    monkeypatch.setattr(agent, '_default_spawn', lambda run_id: slow)
    first = c.post('/api/agent/runs', json={
        'sid': '42', 'authorized': True, 'egress_authorized': True,
    }, headers=UI)
    assert first.status_code == 202
    try:
        r = c.post('/api/agent/runs/batch', json={
            'sids': ['42', '43'], 'authorized': True, 'egress_authorized': True,
        }, headers=UI)
        assert r.status_code == 202
        d = r.json()
        assert [x['sid'] for x in d['rejected']] == ['42']
        assert '43' in {rec['start']['sid'] for rec in d['running']}
    finally:
        gate.set()
        for rec in agent.records(base):
            agent.wait(rec.run_id, 10)


def test_batch_still_needs_the_csrf_header(client):
    """变更类请求一律过 `R-11.2` 那道中间件闸——新端点不例外。"""
    c, _ = client
    assert c.post('/api/agent/runs/batch',
                  json={'sids': ['42']}).status_code == 403
