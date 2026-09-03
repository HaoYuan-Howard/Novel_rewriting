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
        'verified_skipped_nodes': [],
        'verified_skipped_count': 0,
    }


def test_beacon_events_fold_into_steps(client):
    c, base = client
    beacon.append(base, _ev(10, 'stage0.remove', 'start'))
    beacon.append(base, _ev(20, 'stage0.remove', 'end', exit_code=0))
    beacon.append(base, _ev(30, 'stage0.approve', 'start'))
    beacon.append(base, _ev(31, 'stage0.edit', 'end', exit_code=1))
    beacon.append(base, _ev(30, 'stage1.partition', 'start', sid='other'))
    d = c.get('/api/novels/42/run').json()
    by_node = {s['node']: s for s in d['steps']}
    assert by_node['stage0.remove']['beacon'] == 'ok'
    assert by_node['stage0.remove']['last_exit'] == 0
    assert by_node['stage0.approve']['beacon'] == 'running'
    assert by_node['stage0.approve']['elapsed'] is not None
    assert by_node['stage0.edit']['beacon'] == 'landed_red'
    assert by_node['stage0.edit']['last_exit'] == 1
    assert by_node['stage1.partition']['beacon'] == 'pending'   # 别书的打点不漏过来
    assert len(d['events_tail']) == 4


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
