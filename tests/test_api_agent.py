#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_api_agent.py —— `/api/agent/runs` 各端点真的走一遍 HTTP（零成本子进程注入）。

钉六件事：preview 与真跑同构（`R-11.8`）、202 拉起、列表与字节续读日志、
熄火闸、暂停/恢复/停止的异步控制面，以及**两个方向的互斥**
（agent 活 ⇒ 起作业 409；作业活 ⇒ 拉 agent 409）。

【角色边界】受托 Commander 已退役（2026-09-15）：新 run 不再有顶层进程
argv/prompt，也没有 driver fake 可注入；本文件改用与 test_agent.py 同一套
Supervisor harness——``operator.next_action_at_code`` 假读面 +
``agent._run_step_argv`` 零成本 ``python -c`` 子进程。历史 exit origin
'commander' 仍是合法 Literal，只在「历史账解码」语境断言。

本批不需要语料、不需要模型配额。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

fastapi = pytest.importorskip(
    'fastapi', reason='没装 fastapi ⇒ 这一批**没在查**，不是「查过没事」')
from fastapi.testclient import TestClient               # noqa: E402

from contracts.schema.orchestration import (           # noqa: E402
    AgentDeliveryOutcome, AgentRunEvent, AgentRunStart,
)
from novel.api.app import UI_HEADER, create_app         # noqa: E402
from novel.orchestrator import agent                    # noqa: E402

UI = {UI_HEADER: '1'}

_PROFILE = {
    'driver': 'codex',
    'model': 'gpt-5.6-sol',
    'reasoning_effort': 'high',
    'execution_mode': 'standard',
}
#: schema v3：只有 executor backend，没有 commander 字段。
_PIPELINE_PROFILES = {
    'schema_version': 3,
    'executor_default': {'kind': 'agent', 'profile': _PROFILE},
    'executor_overrides': {},
}


@pytest.fixture(autouse=True)
def stable_code_version(monkeypatch):
    """HTTP 用例测路由、锁与审计，不把宿主 Git 状态混进来。

    ``R-11.23`` 让付费 start 拒绝 dirty/unknown 代码身份；若这里照实读仓库指纹，
    开发者工作树一有未提交改动，本文件的 202 用例就整批变成 400。代码指纹与
    dirty 拒绝各由 ``tests/test_prov.py`` / ``tests/test_agent.py`` 独立覆盖，
    这里固定一个干净的非 unknown 身份；个别用例再按需覆盖成 unknown 或 dirty。
    """
    from novel import prov

    monkeypatch.setattr(prov, 'code_version', lambda: 'test-api-code-version')


#: 这一族用例真去拉 agent，而 `agent.start` / `enqueue` 现在**拒绝一本不在盘上的书**
#: （`UpstreamMissing` → 404）。那道闸补的是删除竞态的下游：书删完之后队列里可能还
#: 压着它的旧 pending 项。用例关心的是 HTTP 契约与互斥，不是摄入 ⇒ 这里一次落地。
_SIDS = ('41', '42', '43', '44')


@pytest.fixture
def client(tmp_path):
    from novel.work_units import initialize_workflow
    initialize_workflow(tmp_path)
    base = str(tmp_path)
    for sid in _SIDS:
        (tmp_path / f'FQ_接口测试书_{sid}').mkdir(exist_ok=True)
    with TestClient(create_app(base)) as c:
        yield c, base


# ── 派发 harness：HTTP 拉起的 agent 走真实 Supervisor ──────────────────

def _install_dispatch(monkeypatch, tmp_root, *, blocking=True, ready_reads=1):
    """前 ``ready_reads`` 次读面各派发一个零成本子进程，之后停在 stage0.approve
    人工闸（exit 2 stopped）。

    * ``blocking=True``：子进程等到 ``<stem>.go`` 出现才退出（暂停/停止类用例）。
    返回 box：reads / argvs(epoch, permit, stem) / marker_root。
    """
    marker_root = Path(tmp_root) / '_dispatch'
    marker_root.mkdir(parents=True, exist_ok=True)
    box = {'reads': 0, 'argvs': [], 'marker_root': marker_root}

    def next_action(_base, _sid, *, code):
        box['reads'] += 1
        if box['reads'] <= ready_reads:
            return SimpleNamespace(
                state='ready', code_version=code,
                action=SimpleNamespace(
                    node='stage0.remove', flags=[], waiting_on=None,
                    action_sha256='a' * 64),
                execution_token=SimpleNamespace(node='stage0.remove', flags=[]))
        return SimpleNamespace(
            state='blocked', code_version=code,
            action=SimpleNamespace(
                waiting_on='stage0.approve', action_sha256='b' * 64),
            execution_token=None)

    def step_argv(_base, _sid, epoch, permit, *, stem, **_kwargs):
        box['argvs'].append((epoch, permit, stem))
        if not blocking:
            return [sys.executable, '-c', 'raise SystemExit(0)']
        go = marker_root / f'{stem}.go'
        ready = marker_root / f'{stem}.ready'
        script = (
            'import pathlib,sys,time\n'
            'go=pathlib.Path(sys.argv[1]); ready=pathlib.Path(sys.argv[2])\n'
            'ready.parent.mkdir(parents=True,exist_ok=True)\n'
            'ready.write_text("1",encoding="utf-8")\n'
            'while not go.exists():\n'
            '    time.sleep(0.05)\n'
            'raise SystemExit(0)\n')
        return [sys.executable, '-c', script, str(go), str(ready)]

    monkeypatch.setattr(agent.operator, 'next_action_at_code', next_action)
    monkeypatch.setattr(agent, '_run_step_argv', step_argv)
    monkeypatch.setattr(
        agent, '_delivery_pause_note', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        agent, '_assess_delivery',
        lambda *_args, **_kwargs: AgentDeliveryOutcome(
            state='stopped', reason='HTTP 派发 harness 测试停点', checked_at=1))
    return box


def _wait_marker(path: Path, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return
        time.sleep(0.05)
    raise AssertionError(f'子进程未落启动标记：{path}')


def _start_body(sid='42'):
    return {
        'sid': sid, 'authorized': True, 'egress_authorized': True,
        'profiles': _PIPELINE_PROFILES,
    }


# ── preview：与真跑同一 planner（R-11.8） ───────────────────────────────

def test_preview_shows_the_frozen_boundary_without_commander_columns(client):
    c, _ = client
    r = c.post('/api/agent/runs/preview', json={
        'sid': '42', 'authorized': True, 'egress_authorized': True,
        'profiles': _PIPELINE_PROFILES,
    },
               headers=UI)
    assert r.status_code == 200
    d = r.json()
    assert d['authorized'] is True
    assert d['egress_authorized'] is True
    assert d['profiles'] == _PIPELINE_PROFILES
    # Commander 退役：preview 不再摊开顶层进程命令行/提示词两格。
    assert d.get('argv') is None and d.get('prompt') is None
    # preview 仍可在未授权状态 0 成本查看；profiles 不能省略，因为当前默认是 null。
    d2 = c.post('/api/agent/runs/preview', json={
        'sid': '42', 'profiles': _PIPELINE_PROFILES,
    }, headers=UI).json()
    assert d2['authorized'] is False and d2['egress_authorized'] is True
    assert d2['profiles'] == _PIPELINE_PROFILES

    # 外发授权可独立取消，不能被已勾选的付费授权覆盖。
    d3 = c.post('/api/agent/runs/preview', json={
        'sid': '42', 'authorized': True, 'egress_authorized': False,
        'profiles': _PIPELINE_PROFILES,
    }, headers=UI).json()
    assert d3['authorized'] is True and d3['egress_authorized'] is False


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


def test_invalid_executor_profile_combination_is_refused(client):
    """legacy 默认不再配 agent 覆盖 ⇒ 构造期就 400，不能等真跑才炸。"""
    c, _ = client
    bad = {
        'schema_version': 3,
        'executor_default': {'kind': 'legacy', 'profile': {
            'provider': 'deepseek', 'model': 'deepseek-v4-pro',
            'reasoning_effort': 'high', 'thinking_mode': 'on'}},
        'executor_overrides': {},
    }
    response = c.post('/api/agent/runs/preview', json={
        'sid': '42', 'profiles': bad,
    }, headers=UI)
    assert response.status_code == 400
    assert response.json()['kind'] == 'UnsupportedRunMode'


# ── 启动 / 列表 / 日志 / 熄火闸 ────────────────────────────────────────

def test_start_list_log_and_stop_gate(client, monkeypatch, tmp_path):
    c, base = client
    box = _install_dispatch(monkeypatch, tmp_path, blocking=False)

    r = c.post('/api/agent/runs', json=_start_body(), headers=UI)
    assert r.status_code == 202
    started = r.json()
    assert started['start']['authorized'] is True
    assert started['start']['egress_authorized'] is True
    assert started['start']['profiles'] == _PIPELINE_PROFILES
    # 新 run 的 started 载荷不再写入 Commander 时代的 argv/prompt 两格。
    assert started['start'].get('argv') is None
    assert started['start'].get('prompt') is None
    run_id = started['run_id']
    assert agent.wait(run_id, 15)

    # 单个 ready 动作跑完后，盘面停在人工审批闸 ⇒ exit 2 stopped。
    runs = c.get('/api/agent/runs?sid=42').json()['runs']
    assert [row['state'] for row in runs] == ['stopped']
    assert runs[0]['exit_code'] == 2
    assert '停在人工审批闸' in runs[0]['outcome']['reason']
    assert c.get('/api/agent/runs?sid=other').json()['runs'] == []

    log = c.get(f'/api/agent/runs/{run_id}/log?offset=0').json()
    assert 'ready-stage0.remove' in log['text']
    again = c.get(f"/api/agent/runs/{run_id}/log?offset={log['next_byte']}").json()
    assert again['text'] == ''

    # 已结束 ⇒ 没东西可熄 ⇒ 409（显式终态，不是 500）
    assert c.post(f'/api/agent/runs/{run_id}/stop', headers=UI).status_code == 409


def test_run_list_exposes_exit_two_with_stopped_outcome_as_stopped(client):
    """HTTP 消费真实 ended 组合，不让页面从 exit_code 自己猜状态。"""
    c, base = client
    agent.append(base, AgentRunEvent(
        run_id='explicit-stop', kind='started', ts=10,
        start=AgentRunStart(
            sid='42', base=base, argv=['codex', 'exec'], prompt='test',
            authorized=True, egress_authorized=True,
            profiles=_PIPELINE_PROFILES)))
    agent.append(base, AgentRunEvent(
        run_id='explicit-stop', kind='ended', ts=20, exit_code=2,
        outcome=AgentDeliveryOutcome(
            state='stopped', reason='Stage0 reviewer 明确停在安全边界',
            checked_at=20)))

    [record] = c.get('/api/agent/runs?sid=42').json()['runs']

    assert record['exit_code'] == 2
    assert record['state'] == 'stopped'
    assert record['outcome']['state'] == 'stopped'
    assert record['allowed_controls'] == []


@pytest.mark.parametrize('origin', [None, 'novel_cli', 'commander', 'supervisor'])
def test_run_list_preserves_exit_origin_and_only_projects_novel_cli_one_as_landed(client, origin):
    """历史账解码：'commander' 是已退役角色的历史 origin，仍是合法 Literal，
    HTTP 读面必须原样投影而不重判；只有 novel_cli 的 1 投影成 landed_red。"""
    c, base = client
    agent.append(base, AgentRunEvent(
        run_id='origin', kind='started', ts=10,
        start=AgentRunStart(
            sid='42', base=base, argv=['codex', 'exec'], prompt='test',
            authorized=True, egress_authorized=True, profiles=_PIPELINE_PROFILES)))
    agent.append(base, AgentRunEvent(
        run_id='origin', kind='ended', ts=20, exit_code=1, exit_origin=origin,
        outcome=AgentDeliveryOutcome(state='unknown', checked_at=20, reason='独立终验未知')))

    response = c.get('/api/agent/runs?sid=42')

    assert response.status_code == 200
    [record] = response.json()['runs']
    assert record['exit_code'] == 1
    assert record['state'] == ('landed_red' if origin == 'novel_cli' else 'failed')
    assert record['outcome']['state'] == 'unknown'
    assert record['allowed_controls'] == []
    if origin is None:
        assert 'exit_origin' not in record
    else:
        assert record['exit_origin'] == origin
    if origin != 'novel_cli':
        assert record['note'] is None


# ── 暂停 / 恢复 / 停止：HTTP 只受理，ended 由 worker 独占 ──────────────

def test_pause_resume_are_async_202_and_worker_writes_the_final_states(
        client, monkeypatch, tmp_path):
    """pause/resume 只受理（202，不带 ended）；最终 stopped 事实由 worker 写。"""
    c, base = client
    box = _install_dispatch(monkeypatch, tmp_path, blocking=True, ready_reads=2)
    started = c.post('/api/agent/runs', json=_start_body(), headers=UI)
    assert started.status_code == 202
    run_id = started.json()['run_id']
    try:
        _wait_marker(box['marker_root'] / 'ready-stage0.remove.ready')
        paused_request = c.post(f'/api/agent/runs/{run_id}/pause', headers=UI)
        assert paused_request.status_code == 202
        assert paused_request.json()['ended'] is None
        (box['marker_root'] / 'ready-stage0.remove.go').touch()
        assert agent.pause(base, run_id, timeout=5).state == 'paused'

        resumed_request = c.post(f'/api/agent/runs/{run_id}/resume', headers=UI)
        assert resumed_request.status_code == 202
        assert resumed_request.json()['ended'] is None
        assert agent.wait(run_id, 15)
    finally:
        (box['marker_root'] / 'ready-stage0.remove.go').touch()
        agent.wait(run_id, 15)

    # 暂停前后的两个 ready 动作都在同一 execution epoch 内派发；新世界没有
    # provider 会话可续接，epoch 的 argv/prompt_sha256 两格保持 None。
    assert len(box['argvs']) == 2
    first_epoch, _permit, _stem = box['argvs'][0]
    second_epoch, _permit, _stem = box['argvs'][1]
    assert first_epoch.session_run_id == second_epoch.session_run_id
    assert first_epoch.argv is None and first_epoch.prompt_sha256 is None
    events = [row for row in agent.read_events(base) if row.run_id == run_id]
    kinds = [row.kind for row in events]
    assert 'paused' in kinds and 'resumed' in kinds
    assert kinds.count('epoch_opened') == 1
    [done] = [row for row in agent.records(base) if row.run_id == run_id]
    assert done.ended is not None and done.exit_code == 2
    assert done.state == 'stopped' and done.allowed_controls == []


def test_stop_is_async_202_and_ends_only_after_the_process_tree_drains(
        client, monkeypatch, tmp_path):
    """stop 202 不得同步等待并伪装 execution 已经 drain；cancel 后仍由 worker 收口。"""
    c, base = client
    box = _install_dispatch(monkeypatch, tmp_path, blocking=True)
    started = c.post('/api/agent/runs', json=_start_body(), headers=UI)
    assert started.status_code == 202
    run_id = started.json()['run_id']
    try:
        _wait_marker(box['marker_root'] / 'ready-stage0.remove.ready')

        stopped_request = c.post(f'/api/agent/runs/{run_id}/stop', headers=UI)
        assert stopped_request.status_code == 202
        # 受理响应只能携带请求时刻的记录：ended 必须还是空，路由层不同步等待。
        assert stopped_request.json()['ended'] is None
    finally:
        (box['marker_root'] / 'ready-stage0.remove.go').touch()
        agent.wait(run_id, 15)

    [done] = [row for row in agent.records(base) if row.run_id == run_id]
    assert done.ended is not None and done.exit_code != 0
    assert done.outcome is not None and done.outcome.state == 'stopped'
    assert done.allowed_controls == []


def test_csrf_guard_covers_the_mutation(client):
    c, _ = client
    assert c.post('/api/agent/runs', json={'sid': '42'}).status_code == 403


@pytest.mark.parametrize('authorized,egress_authorized', [
    (False, True), (True, False), (False, False),
])
def test_http_start_requires_both_authorizations_before_any_dispatch(
        client, monkeypatch, authorized, egress_authorized):
    c, base = client
    dispatched = []
    monkeypatch.setattr(
        agent, '_run_step_argv',
        lambda *_args, **_kwargs: dispatched.append(True))
    response = c.post('/api/agent/runs', json={
        'sid': '42', 'authorized': authorized,
        'egress_authorized': egress_authorized,
        'profiles': _PIPELINE_PROFILES,
    }, headers=UI)
    assert response.status_code == 409
    assert response.json()['kind'] == 'ApprovalRequired'
    assert dispatched == [], '缺授权时不得构造任何 canonical 子进程'
    assert not agent.runs_path(base).exists()


def test_http_start_refuses_unknown_code_before_spawn(client, monkeypatch):
    from novel import prov

    c, base = client
    dispatched = []
    monkeypatch.setattr(prov, 'code_version', lambda: prov.CODE_UNKNOWN)
    monkeypatch.setattr(
        agent, '_run_step_argv',
        lambda *_args, **_kwargs: dispatched.append(True))
    response = c.post('/api/agent/runs', json=_start_body(), headers=UI)
    assert response.status_code == 400
    assert response.json()['kind'] == 'UnsupportedRunMode'
    assert '代码指纹不可用' in response.json()['message']
    assert dispatched == []
    assert not agent.runs_path(base).exists()


def test_http_start_refuses_dirty_code_but_preview_shows_it(client, monkeypatch):
    """`R-11.23`：dirty digest 可 0 成本预览，付费 start 在派发前 400。"""
    from novel import prov

    c, base = client
    dirty = 'a' * 40 + '-dirty.' + 'b' * 16
    monkeypatch.setattr(prov, 'code_version', lambda: dirty)
    dispatched = []
    monkeypatch.setattr(
        agent, '_run_step_argv',
        lambda *_args, **_kwargs: dispatched.append(True))
    preview = c.post('/api/agent/runs/preview', json=_start_body(), headers=UI)
    assert preview.status_code == 200
    assert preview.json()['profiles'] == _PIPELINE_PROFILES
    response = c.post('/api/agent/runs', json=_start_body(), headers=UI)
    assert response.status_code == 400
    assert response.json()['kind'] == 'UnsupportedRunMode'
    assert '未提交改动' in response.json()['message']
    assert dispatched == []


def test_both_directions_of_the_mutex(client, monkeypatch, tmp_path):
    c, base = client
    box42 = _install_dispatch(monkeypatch, tmp_path / 'b42', blocking=True)
    r = c.post('/api/agent/runs', json=_start_body(), headers=UI)
    assert r.status_code == 202
    other = None
    boxes = [box42]
    try:
        # agent 活着 ⇒ 起 API 作业 409（反向互斥在 jobs.start 里，路由层白拿）
        # ⚠ stage0.approve 已退役（2026-09-15）：互斥改用现役免费节点
        #   `stage1.partition`——它无必填凭据，保证这条测试测的是「一条本来
        #   能起的作业被锁挡住」，而不是在 validate_run 处先 400。
        r2 = c.post('/api/jobs', json={
            'sid': '42', 'node': 'stage1.partition', 'flags': [],
        }, headers=UI)
        assert r2.status_code == 409
        # 同一本书的第二个 agent 仍 409（一本书一把锁）
        r3 = c.post('/api/agent/runs', json=_start_body(),
                    headers=UI)
        assert r3.status_code == 409
        # ⭐ 但**另一本书**起得来：锁按书分，异书两个 agent 谁也碰不到谁的目录。
        boxes.append(_install_dispatch(
            monkeypatch, tmp_path / 'b43', blocking=True))
        r4 = c.post('/api/agent/runs', json=_start_body('43'), headers=UI)
        assert r4.status_code == 202, '另一本书被挡住了——锁又变回全局一把了'
        other = r4.json()['run_id']
    finally:
        # ⚠ 收尾走 `wait` 不是 `stop`：子进程是 harness 造的零成本 python 子进程，
        #   逐个放行 go 标记后等 worker 自己收口。
        for box in boxes:
            (box['marker_root'] / 'ready-stage0.remove.go').touch()
        agent.wait(r.json()['run_id'], 15)
        if other:
            agent.wait(other, 15)


# ───────────────────── 批量入口与队列（并发放开之后的新面） ─────────────────────

def test_batch_starts_what_it_can_and_queues_the_rest(client, monkeypatch, tmp_path):
    """⭐ 一次交 N 本：闸内的立刻跑，超出的排队。**202 = 已受理，不是已跑完。**"""
    c, base = client
    monkeypatch.setattr(agent, 'MAX_CONCURRENT', 2)
    box = _install_dispatch(monkeypatch, tmp_path, blocking=True)
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
        (box['marker_root'] / 'ready-stage0.remove.go').touch()
        for run_id in started:
            agent.wait(run_id, 15)
        for rec in agent.records(base):
            agent.wait(rec.run_id, 15)


def test_the_queue_endpoint_publishes_the_gate(client):
    """前端要显示「3 个都在跑」就得从服务端拿这个数——**不许自己写死**。"""
    c, _ = client
    r = c.get('/api/agent/runs/queue')
    assert r.status_code == 200
    d = r.json()
    assert d['max_concurrent'] == agent.MAX_CONCURRENT
    assert d['running'] == [] and d['pending'] == []


def test_batch_rejects_a_book_that_already_has_an_agent(client, monkeypatch, tmp_path):
    """**部分失败不回滚**：重复那本进 `rejected`，其余照常受理。"""
    c, base = client
    box = _install_dispatch(monkeypatch, tmp_path, blocking=True)
    first = c.post('/api/agent/runs', json=_start_body(), headers=UI)
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
        (box['marker_root'] / 'ready-stage0.remove.go').touch()
        for rec in agent.records(base):
            agent.wait(rec.run_id, 15)


def test_batch_still_needs_the_csrf_header(client):
    """变更类请求一律过 `R-11.2` 那道中间件闸——新端点不例外。"""
    c, _ = client
    assert c.post('/api/agent/runs/batch',
                  json={'sids': ['42']}).status_code == 403
