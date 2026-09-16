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
    beacon.append(base, _ev(30, 'stage2.analyze', 'start'))
    beacon.append(base, _ev(31, 'stage2.characters', 'end', exit_code=1))
    beacon.append(base, _ev(32, 'stage1.partition', 'end', exit_code=2))
    beacon.append(base, _ev(33, 'stage2.global', 'end', exit_code=70))
    beacon.append(base, _ev(30, 'stage1.partition', 'start', sid='other'))
    d = c.get('/api/novels/42/run').json()
    by_node = {s['node']: s for s in d['steps']}
    assert by_node['stage0.remove']['beacon'] == 'ok'
    assert by_node['stage0.remove']['last_exit'] == 0
    assert by_node['stage2.analyze']['beacon'] == 'running'
    assert by_node['stage2.analyze']['elapsed'] is not None
    assert by_node['stage2.characters']['beacon'] == 'landed_red'
    assert by_node['stage2.characters']['last_exit'] == 1
    assert by_node['stage1.partition']['beacon'] == 'stopped'
    assert by_node['stage2.global']['beacon'] == 'failed'
    assert len(d['events_tail']) == 6
    by_event_node = {event['node']: event for event in d['events_tail']}
    assert by_event_node['stage1.partition']['beacon'] == 'stopped'
    assert by_event_node['stage2.global']['beacon'] == 'failed'


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


def test_stage2_chapter_fix_route_reaches_http_preview(client):
    c, base = client
    os.mkdir(os.path.join(base, '测试书_42'))
    response = c.post(
        '/api/jobs/preview',
        headers={UI_HEADER: '1'},
        json={'sid': '42', 'node': 'stage2.blueprint',
              'flags': ['--chapters', '11']},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['flags'] == ['--chapters', '11']
    assert any('--chapters' in step['argv'] and '11' in step['argv']
               for step in body['steps'])


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
    # stage0.approve（B1）与 stage4.design/plan 双审（B2）退役：human 审阅面 8 → 5。
    assert len(reviews) == 5
    assert {row['node'] for row in reviews} == {
        node for node, spec in NODES.items() if spec.lifecycle.review_mode == 'human'}
    # Stage2 语义审阅退役：run 面不再有 stage2 审批状态行。
    assert not [row for row in reviews if row['node'].startswith('stage2.')]
    assert operator._review_state_projection(None) is None
    def row(node, role, scope):
        return dict(unit_id=node+role+scope, node=node, role=role, scope=scope,
                    content_used=1, content_limit=4, content_remaining=3, state='delivered',
                    recovery_action='replay_response')
    monkeypatch.setattr(run.work_units, 'list_unit_views', lambda *_args: [
        row('stage7.rename', 'executor', 'book'),
        # 历史 commander 行保留只读解码，但不得进入当前恢复动作面。
        row('stage7.rename', 'commander', 'book'),
        row('stage6.hook', 'executor', 'book')])
    # 人工审批闸：next_handler=human，任何模型工作单元都不投影恢复动作。
    frontier = RunResumeFrontierView(
        state='blocked', node='stage7.approve', seq=list(NODES).index('stage7.approve') + 1,
        label=NODES['stage7.approve'].label, resume_verdict='run', why='等人名表定稿',
        waiting_on='stage7.approve', paid=False, writes='NameMap',
        next_handler='human', review_states=reviews,
        verified_skipped_nodes=list(NODES)[:list(NODES).index('stage7.approve')],
        verified_skipped_count=list(NODES).index('stage7.approve'))
    monkeypatch.setattr(operator, 'progress_read_snapshot',
                        lambda *_args: nullcontext((SimpleNamespace(stages=[]), frontier)))
    response = c.get('/api/novels/42/run')
    assert response.status_code == 200
    assert response.json()['resume_frontier']['next_handler'] == 'human'
    assert response.json()['current_recovery_actions'] == []
    # executor lane：只有与 next_handler 同角色的当前节点工作单元进入恢复动作。
    frontier = RunResumeFrontierView(
        state='blocked', node='stage7.rename', seq=list(NODES).index('stage7.rename') + 1,
        label=NODES['stage7.rename'].label, resume_verdict='run', why='人名表待重采',
        waiting_on='resume:manual-decision', paid=True, writes='NameMap',
        next_handler='executor', review_states=reviews,
        verified_skipped_nodes=list(NODES)[:list(NODES).index('stage7.rename')],
        verified_skipped_count=list(NODES).index('stage7.rename'))
    response = c.get('/api/novels/42/run')
    assert response.status_code == 200
    # 恢复动作标签 = scope: recovery_action；只有当前节点 executor 轮进入。
    assert response.json()['current_recovery_actions'] == ['book: replay_response']
