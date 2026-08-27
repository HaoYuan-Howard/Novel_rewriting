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
from novel.agents.base import AgentUsage                # noqa: E402
from novel.commanders import (                          # noqa: E402
    CommanderInvocation, CommanderObservation, CommanderProcessResult,
)
from novel.orchestrator import agent                    # noqa: E402

UI = {UI_HEADER: '1'}

_PROFILE = {
    'driver': 'codex',
    'model': 'gpt-5.6-sol',
    'reasoning_effort': 'high',
    'execution_mode': 'standard',
}
_PIPELINE_PROFILES = {
    'commander': _PROFILE,
    'executor_default': _PROFILE,
    'executor_overrides': {},
}


class _PlanningDriver:
    def __init__(self, profile):
        self.profile = profile

    def build_invocation(self, profile, prompt, *, repo, base, **_kwargs):
        del base
        return CommanderInvocation(
            argv=(profile.driver, 'headless', '--model', profile.model),
            stdin_text=prompt, cwd=str(repo), env_overrides={},
            profile=profile, protocol=f'{profile.driver}-test-v1')

    def close(self):
        return None


@pytest.fixture(autouse=True)
def no_real_commander_cli(monkeypatch):
    monkeypatch.setattr(
        agent, 'create_commander_driver',
        lambda profile, **_kwargs: _PlanningDriver(profile))


#: 这一族用例真去拉 agent，而 `agent.start` / `enqueue` 现在**拒绝一本不在盘上的书**
#: （`UpstreamMissing` → 404）。那道闸补的是删除竞态的下游：书删完之后队列里可能还
#: 压着它的旧 pending 项。用例关心的是 HTTP 契约与互斥，不是摄入 ⇒ 这里一次落地。
_SIDS = ('41', '42', '43', '44')


@pytest.fixture
def client(tmp_path):
    base = str(tmp_path)
    for sid in _SIDS:
        (tmp_path / f'FQ_接口测试书_{sid}').mkdir(exist_ok=True)
    with TestClient(create_app(base)) as c:
        yield c, base


def _result(invocation, env, *, exit_code=0):
    return CommanderProcessResult(
        exit_code=exit_code,
        observation=CommanderObservation(
            provider_session_id=f"provider-{env['NOVEL_COMMANDER_RUN_ID']}",
            actual_model=invocation.profile.model,
            actual_effort=invocation.profile.reasoning_effort,
            usage=AgentUsage(prompt_tokens=5, completion_tokens=1,
                             total_tokens=6)))


def _ok_spawn(invocation, env, log):
    log.write(b'fake agent output\n')
    return _result(invocation, env)


def test_preview_shows_the_exact_argv_and_prompt(client):
    c, base = client
    r = c.post('/api/agent/runs/preview', json={
        'sid': '42', 'authorized': True, 'egress_authorized': True,
        'profiles': _PIPELINE_PROFILES,
    },
               headers=UI)
    assert r.status_code == 200
    d = r.json()
    assert d['authorized'] is True
    assert d['egress_authorized'] is True
    assert '付费授权已授予' in d['prompt']
    assert '正文外发授权已授予' in d['prompt']
    assert d['profiles'] == _PIPELINE_PROFILES
    assert d['prompt'] not in d['argv']
    # preview 仍可在未授权状态 0 成本查看；profiles 不能省略，因为当前默认是 null。
    d2 = c.post('/api/agent/runs/preview', json={
        'sid': '42', 'profiles': _PIPELINE_PROFILES,
    }, headers=UI).json()
    assert d2['authorized'] is False and d2['egress_authorized'] is True
    assert '付费授权未授予' in d2['prompt']
    assert '正文外发授权已授予' in d2['prompt']

    # 外发授权可独立取消，不能被已勾选的付费授权覆盖。
    d3 = c.post('/api/agent/runs/preview', json={
        'sid': '42', 'authorized': True, 'egress_authorized': False,
        'profiles': _PIPELINE_PROFILES,
    }, headers=UI).json()
    assert d3['authorized'] is True and d3['egress_authorized'] is False
    assert '付费授权已授予' in d3['prompt']
    assert '正文外发授权未授予' in d3['prompt']


@pytest.mark.parametrize('path,payload', [
    ('/api/agent/runs/preview', {'sid': '42'}),
    ('/api/agent/runs', {
        'sid': '42', 'authorized': True, 'egress_authorized': True}),
    ('/api/agent/runs/batch', {
        'sids': ['42'], 'authorized': True, 'egress_authorized': True}),
])
def test_missing_pipeline_profiles_are_closed_while_default_is_null(
        client, path, payload):
    c, _ = client
    response = c.post(path, json=payload, headers=UI)
    assert response.status_code == 400
    assert response.json()['kind'] == 'UnsupportedRunMode'
    assert '显式选择' in response.json()['message']


def test_invalid_profile_combination_is_refused(client):
    c, _ = client
    bad = {
        **_PIPELINE_PROFILES,
        'commander': {**_PROFILE, 'model': 'claude-opus-5'},
    }
    response = c.post('/api/agent/runs/preview', json={
        'sid': '42', 'profiles': bad,
    }, headers=UI)
    assert response.status_code == 400
    assert response.json()['kind'] == 'UnsupportedRunMode'


def test_start_list_log_and_stop_gate(client, monkeypatch):
    c, base = client
    monkeypatch.setattr(agent, '_default_spawn', lambda run_id: _ok_spawn)

    r = c.post('/api/agent/runs', json={
        'sid': '42', 'authorized': True, 'egress_authorized': True,
        'profiles': _PIPELINE_PROFILES,
    }, headers=UI)
    assert r.status_code == 202
    assert r.json()['start']['authorized'] is True
    assert r.json()['start']['egress_authorized'] is True
    assert r.json()['start']['profiles'] == _PIPELINE_PROFILES
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


@pytest.mark.parametrize('authorized,egress_authorized', [
    (False, True), (True, False), (False, False),
])
def test_http_start_requires_both_authorizations_before_driver(
        client, monkeypatch, authorized, egress_authorized):
    c, _ = client
    called = []
    monkeypatch.setattr(
        agent, 'create_commander_driver',
        lambda *_args, **_kwargs: called.append(True))
    response = c.post('/api/agent/runs', json={
        'sid': '42', 'authorized': authorized,
        'egress_authorized': egress_authorized,
        'profiles': _PIPELINE_PROFILES,
    }, headers=UI)
    assert response.status_code == 409
    assert response.json()['kind'] == 'ApprovalRequired'
    assert called == []


def test_http_start_refuses_unknown_code_before_spawn(client, monkeypatch):
    from novel import prov

    c, _ = client
    spawned = []
    monkeypatch.setattr(prov, 'code_version', lambda: prov.CODE_UNKNOWN)
    monkeypatch.setattr(
        agent, '_default_spawn', lambda _run_id: spawned.append(True))
    response = c.post('/api/agent/runs', json={
        'sid': '42', 'authorized': True, 'egress_authorized': True,
        'profiles': _PIPELINE_PROFILES,
    }, headers=UI)
    assert response.status_code == 400
    assert response.json()['kind'] == 'UnsupportedRunMode'
    assert '代码指纹不可用' in response.json()['message']
    assert spawned == []


def test_both_directions_of_the_mutex(client, monkeypatch):
    c, base = client
    gate = threading.Event()

    def slow(invocation, env, log):
        gate.wait(10)
        return _result(invocation, env)

    monkeypatch.setattr(agent, '_default_spawn', lambda run_id: slow)
    r = c.post('/api/agent/runs', json={
        'sid': '42', 'authorized': True, 'egress_authorized': True,
        'profiles': _PIPELINE_PROFILES,
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
            'profiles': _PIPELINE_PROFILES,
        },
                     headers=UI)
        assert r3.status_code == 409
        # ⭐ 但**另一本书**起得来：锁按书分，异书两个 agent 谁也碰不到谁的目录。
        r4 = c.post('/api/agent/runs', json={
            'sid': '43', 'authorized': True, 'egress_authorized': True,
            'profiles': _PIPELINE_PROFILES,
        }, headers=UI)
        assert r4.status_code == 202, '另一本书被挡住了——锁又变回全局一把了'
        other = r4.json()['run_id']
    finally:
        # ⚠ 收尾走 `wait` 不是 `stop`：这一批用的是 monkeypatch 进去的假 spawn，
        # Commander owner 表里根本没有句柄，`stop` 会以「没有在跑的子进程」当场炸掉。
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

    def slow(invocation, env, log):
        gate.wait(10)
        return _result(invocation, env)

    monkeypatch.setattr(agent, '_default_spawn', lambda run_id: slow)
    started = []
    try:
        r = c.post('/api/agent/runs/batch', json={
            'sids': ['42', '43', '44'], 'authorized': True,
            'egress_authorized': True,
            'profiles': _PIPELINE_PROFILES,
        }, headers=UI)
        assert r.status_code == 202
        d = r.json()
        assert {rec['start']['sid'] for rec in d['running']} == {'42', '43'}
        assert [p['sid'] for p in d['pending']] == ['44']
        assert d['pending'][0]['position'] == 1
        assert d['pending'][0]['profiles'] == _PIPELINE_PROFILES
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

    def slow(invocation, env, log):
        gate.wait(10)
        return _result(invocation, env)

    monkeypatch.setattr(agent, '_default_spawn', lambda run_id: slow)
    first = c.post('/api/agent/runs', json={
        'sid': '42', 'authorized': True, 'egress_authorized': True,
        'profiles': _PIPELINE_PROFILES,
    }, headers=UI)
    assert first.status_code == 202
    try:
        r = c.post('/api/agent/runs/batch', json={
            'sids': ['42', '43'], 'authorized': True, 'egress_authorized': True,
            'profiles': _PIPELINE_PROFILES,
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
