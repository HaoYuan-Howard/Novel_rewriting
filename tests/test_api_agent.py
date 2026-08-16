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
        # 同时第二个 agent 也 409（全局一把锁）
        r3 = c.post('/api/agent/runs', json={
            'sid': '43', 'authorized': True, 'egress_authorized': True,
        },
                     headers=UI)
        assert r3.status_code == 409
    finally:
        gate.set()
        agent.wait(r.json()['run_id'], 10)
