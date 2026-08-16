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

from contracts.invariants import STAGES                 # noqa: E402
from contracts.schema.orchestration import ProgressEvent  # noqa: E402
from novel.api.app import create_app                    # noqa: E402
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
    assert len(d['steps']) == 14
    assert {s['beacon'] for s in d['steps']} == {'pending'}
    assert set(d['stage_done']) == set(STAGES)
    assert all(v is None for v in d['stage_done'].values())
    assert d['events_tail'] == []


def test_beacon_events_fold_into_steps(client):
    c, base = client
    beacon.append(base, _ev(10, 'stage0.remove', 'start'))
    beacon.append(base, _ev(20, 'stage0.remove', 'end', exit_code=0))
    beacon.append(base, _ev(30, 'stage0.approve', 'start'))
    beacon.append(base, _ev(30, 'stage1.partition', 'start', sid='other'))
    d = c.get('/api/novels/42/run').json()
    by_node = {s['node']: s for s in d['steps']}
    assert by_node['stage0.remove']['beacon'] == 'ok'
    assert by_node['stage0.remove']['last_exit'] == 0
    assert by_node['stage0.approve']['beacon'] == 'running'
    assert by_node['stage0.approve']['elapsed'] is not None
    assert by_node['stage1.partition']['beacon'] == 'pending'   # 别书的打点不漏过来
    assert len(d['events_tail']) == 3


def test_existing_book_dir_gives_real_stage_done(client):
    """书目录在盘上时，`stage_done` 来自产物完成度而不是全 `None`。"""
    c, base = client
    os.mkdir(os.path.join(base, '测试书_42'))
    d = c.get('/api/novels/42/run').json()
    assert set(d['stage_done']) == set(STAGES)
    # 一份产物都没有：非逐章层 done=False；stage5 读不回计划 ⇒ None（判不了≠False）
    assert d['stage_done']['stage0'] is False
    assert d['stage_done']['stage5'] is None
