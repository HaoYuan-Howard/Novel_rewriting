#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""service retention / trash restore 的 HTTP preview→确认→apply 竖切。"""
from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip(
    'fastapi', reason='没装 fastapi ⇒ 维护 API 没在查，不是查过没事')
from fastapi.testclient import TestClient  # noqa: E402

from novel.api.app import UI_HEADER, create_app  # noqa: E402
from novel.api.routes import maintenance  # noqa: E402
from novel.errors import BookTrashFailed  # noqa: E402
from novel.orchestrator import trash  # noqa: E402

UI = {UI_HEADER: '1'}


def _book(base):
    book = base / '测试书_1001'
    (book / 'stage0').mkdir(parents=True)
    (book / 'stage0' / 'clean_text.txt').write_text('正文\n', encoding='utf-8')
    approval = base / '_service' / 'approvals' / '1001.json'
    approval.parent.mkdir(parents=True)
    approval.write_text('{"decision":"human"}\n', encoding='utf-8')
    return book


def test_retention_api_requires_same_preview_plan_and_explicit_confirmation(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(
            '/api/maintenance/retention/preview', headers=UI,
            json={
                'log_ttl_seconds': None,
                'telemetry_ttl_seconds': None,
                'trash_ttl_seconds': None,
                'checkpoint_ttl_seconds': None,
                'max_entries': 32,
            })
        assert response.status_code == 200
        plan = response.json()
        assert plan['candidates'] == [] and len(plan['plan_token']) == 64

        refused = client.post(
            '/api/maintenance/retention/apply', headers=UI,
            json={'plan': plan, 'confirmed': False})
        assert refused.status_code == 409
        applied = client.post(
            '/api/maintenance/retention/apply', headers=UI,
            json={'plan': plan, 'confirmed': True})
        assert applied.status_code == 200
        assert applied.json() == {
            'plan_token': plan['plan_token'], 'removed': [], 'receipts': []}


def test_restore_api_previews_real_cas_and_apply_calls_controlled_cli_boundary(
        tmp_path, monkeypatch):
    _book(tmp_path)
    trash_id = '1700000000-deadbeef'
    trash.execute(tmp_path, '1001', '测试书', trash_id)
    captured = []

    def fake_run_restore(base, plan):
        captured.append((base, plan))
        return {
            'sid': plan.sid, 'title': plan.title,
            'trash_id': plan.trash_id, 'book_rel': plan.original_dirname}

    monkeypatch.setattr(maintenance.trash, 'run_restore', fake_run_restore)
    with TestClient(create_app(tmp_path)) as client:
        preview = client.post(
            '/api/maintenance/trash/restore/preview', headers=UI,
            json={'trash_id': trash_id, 'expected_title': '测试书'})
        assert preview.status_code == 200
        plan = preview.json()
        assert plan['sid'] == '1001' and len(plan['plan_token']) == 64

        refused = client.post(
            '/api/maintenance/trash/restore/apply', headers=UI,
            json={'plan': plan, 'confirmed': False})
        assert refused.status_code == 409 and captured == []
        applied = client.post(
            '/api/maintenance/trash/restore/apply', headers=UI,
            json={'plan': plan, 'confirmed': True})
        assert applied.status_code == 200
        assert captured and captured[0][1].plan_token == plan['plan_token']


def test_restore_core_rejects_manifest_drift_after_preview(tmp_path):
    _book(tmp_path)
    trash_id = '1700000000-f00dbabe'
    trash.execute(tmp_path, '1001', '测试书', trash_id)
    plan = trash.preview_restore(tmp_path, trash_id, '测试书', now=123)
    manifest_path = tmp_path / '_service' / 'trash' / trash_id / 'manifest.json'
    value = json.loads(manifest_path.read_text(encoding='utf-8'))
    value['unexpected'] = True
    manifest_path.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(BookTrashFailed):
        trash.apply_restore(tmp_path, plan)
