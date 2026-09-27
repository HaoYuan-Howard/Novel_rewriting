#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_api_run.py —— `GET /api/novels/:sid/run` 真的走一遍 HTTP。

钉三件事：信标折叠进响应、别书的打点不漏过来、
**书目录不存在也是 200**（stage0.remove 打点先于书目录落地，
进度屏那时就得开得开——`stage_done` 全 `None`）。

本批不需要语料（信标只进 `_service/`）。
"""
from __future__ import annotations

import os

import pytest

fastapi = pytest.importorskip(
    'fastapi', reason='没装 fastapi ⇒ 这一批**没在查**，不是「查过没事」')
from fastapi.testclient import TestClient               # noqa: E402

from contracts.invariants import NODES, STAGES          # noqa: E402
from contracts.schema.orchestration import ProgressEvent  # noqa: E402
from novel.api.app import UI_HEADER, create_app         # noqa: E402
from novel.orchestrator import beacon                   # noqa: E402


@pytest.fixture
def client(tmp_path):
    base = str(tmp_path)
    with TestClient(create_app(base)) as c:
        yield c, base


def _ev(ts, node, phase, exit_code=None, sid='42'):
    return ProgressEvent(ts=ts, sid=sid, node=node, phase=phase, exit_code=exit_code)


def test_empty_base_is_all_pending(client):
    c, _ = client
    r = c.get('/api/novels/42/run')
    assert r.status_code == 200
    d = r.json()
    assert d['sid'] == '42'
    assert [step['node'] for step in d['steps']] == list(NODES)
    assert {s['beacon'] for s in d['steps']} == {'pending'}
    assert set(d['stage_done']) == set(STAGES)
    assert all(v is None for v in d['stage_done'].values())
    assert d['events_tail'] == []
    assert d['resume_frontier'] == {
        'state': 'unknown',
        'node': None,
        'seq': None,
        'label': None,
        'resume_verdict': None,
        'why': '书目录尚未形成，当前无法判定续跑点',
        'waiting_on': None,
        'paid': None,
        'writes': None,
        'next_handler': 'unknown',
        'review_states': None,
        'verified_skipped_nodes': [],
        'verified_skipped_count': 0,
    }


def test_beacon_events_fold_into_steps(client):
    c, base = client
    # stage0.approve / stage0.edit 已退役（2026-09-15）：fold 节点面 = NODES，
    # 改用现役节点名钉同一组状态。
    beacon.append(base, _ev(10, 'stage0.remove', 'start'))
    beacon.append(base, _ev(20, 'stage0.remove', 'end', exit_code=0))
    beacon.append(base, _ev(30, 'str06.stage2.book', 'start'))
    beacon.append(base, _ev(31, 'str06.stage2.names', 'end', exit_code=1))
    beacon.append(base, _ev(32, 'stage1.partition', 'end', exit_code=2))
    beacon.append(base, _ev(33, 'str06.stage4.preserve', 'end', exit_code=70))
    beacon.append(base, _ev(30, 'stage1.partition', 'start', sid='other'))
    d = c.get('/api/novels/42/run').json()
    by_node = {s['node']: s for s in d['steps']}
    assert by_node['stage0.remove']['beacon'] == 'ok'
    assert by_node['stage0.remove']['last_exit'] == 0
    assert by_node['str06.stage2.book']['beacon'] == 'running'
    assert by_node['str06.stage2.book']['elapsed'] is not None
    assert by_node['str06.stage2.names']['beacon'] == 'landed_red'
    assert by_node['str06.stage2.names']['last_exit'] == 1
    assert by_node['stage1.partition']['beacon'] == 'stopped'
    assert by_node['str06.stage4.preserve']['beacon'] == 'failed'
    assert len(d['events_tail']) == 6
    by_event_node = {event['node']: event for event in d['events_tail']}
    assert by_event_node['stage1.partition']['beacon'] == 'stopped'
    assert by_event_node['str06.stage4.preserve']['beacon'] == 'failed'


def test_existing_book_dir_gives_real_stage_done(client):
    """书目录在盘上时，`stage_done` 来自产物完成度而不是全 `None`。"""
    c, base = client
    os.mkdir(os.path.join(base, '测试书_42'))
    d = c.get('/api/novels/42/run').json()
    assert set(d['stage_done']) == set(STAGES)
    # 一份产物都没有：非逐章层 done=False；stage5 读不回计划 ⇒ None（判不了≠False）
    assert d['stage_done']['stage0'] is False
    assert d['stage_done']['stage5'] is None
    assert d['resume_frontier']['state'] == 'ready'
    assert d['resume_frontier']['node'] == 'stage0.remove'
    assert d['resume_frontier']['next_handler'] == 'code'
    assert d['resume_frontier']['verified_skipped_count'] == 0


def test_retired_valued_flag_is_refused_at_http_preview(client):
    """F8b-1 后节点旗标面收敛为 --check-only：旧链带值旗标（--chapters）
    在预检口关闭式拒绝，不再拼出一条看似可跑的命令。"""
    c, base = client
    os.mkdir(os.path.join(base, '测试书_42'))
    response = c.post(
        '/api/jobs/preview',
        headers={UI_HEADER: '1'},
        json={'sid': '42', 'node': 'str06.stage2.book',
              'flags': ['--chapters', '11']},
    )
    assert response.status_code == 400, response.text


def test_run_projects_current_review_states_and_only_current_role_recovery(client, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from contracts.schema.orchestration import RunResumeFrontierView
    from novel.api.routes import run
    from novel.orchestrator import operator

    c, base = client
    os.mkdir(os.path.join(base, '测试书_42'))
    actual = c.get('/api/novels/42/run').json()
    reviews = actual['resume_frontier']['review_states']
    # F8a/F8b-2/F8c：prepare/s6/s7 闸与 Stage2 语义审阅面随 Stage6 整层与人工闸
    # 退役，`RunResumeFrontierView.review_states` 恒为 None——run 面不再投影任何
    # 审批状态行（字段保留只为 wire 兼容，读到的就是「没有这个面」）。
    assert reviews is None
    # 投影函数本身也随人工闸整层退役：operator 不再有 review state 投影落点。
    assert not hasattr(operator, '_review_state_projection')
    def row(node, role, scope):
        return dict(unit_id=node+role+scope, node=node, role=role, scope=scope,
                    content_used=1, content_limit=4, content_remaining=3, state='delivered',
                    recovery_action='replay_response')
    monkeypatch.setattr(run.work_units, 'list_unit_views', lambda *_args: [
        row('str06.stage7.naming', 'executor', 'book'),
        # 历史 commander 行保留只读解码，但不得进入当前恢复动作面。
        row('str06.stage7.naming', 'commander', 'book'),
        row('stage6.hook', 'executor', 'book')])
    # 阻断面：next_handler=human，任何模型工作单元都不投影恢复动作。
    frontier = RunResumeFrontierView(
        state='blocked', node='str06.stage7.naming', seq=list(NODES).index('str06.stage7.naming') + 1,
        label=NODES['str06.stage7.naming'].label, resume_verdict='run', why='等人名表恢复',
        waiting_on='inbox:invalid', paid=False, writes='stage7/naming/name_map.json',
        next_handler='human', review_states=reviews,
        verified_skipped_nodes=list(NODES)[:list(NODES).index('str06.stage7.naming')],
        verified_skipped_count=list(NODES).index('str06.stage7.naming'))
    monkeypatch.setattr(operator, 'progress_read_snapshot',
                        lambda *_args: nullcontext((SimpleNamespace(stages=[]), frontier)))
    response = c.get('/api/novels/42/run')
    assert response.status_code == 200
    assert response.json()['resume_frontier']['next_handler'] == 'human'
    assert response.json()['current_recovery_actions'] == []
    # executor lane：只有与 next_handler 同角色的当前节点工作单元进入恢复动作。
    frontier = RunResumeFrontierView(
        state='blocked', node='str06.stage7.naming', seq=list(NODES).index('str06.stage7.naming') + 1,
        label=NODES['str06.stage7.naming'].label, resume_verdict='run', why='人名表待重采',
        waiting_on='resume:manual-decision', paid=True, writes='stage7/naming/name_map.json',
        next_handler='executor', review_states=reviews,
        verified_skipped_nodes=list(NODES)[:list(NODES).index('str06.stage7.naming')],
        verified_skipped_count=list(NODES).index('str06.stage7.naming'))
    response = c.get('/api/novels/42/run')
    assert response.status_code == 200
    # 恢复动作标签 = scope: recovery_action；只有当前节点 executor 轮进入。
    assert response.json()['current_recovery_actions'] == ['book: replay_response']
