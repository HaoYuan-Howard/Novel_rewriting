"""已购草稿只读 API；人工 POST 写面已随批次 C 退役（内部 CLI 仍可恢复）。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from novel.api.app import UI_HEADER, create_app
from tests.test_stage2_analyze_draft_recovery import seed, original_bytes


@pytest.mark.real_code_version
@pytest.mark.parametrize('owner', ['stage2.characters', 'stage2.global'])
def test_analyze_draft_http_read_only_after_batch_c(tmp_path, owner):
    ctx, _free = seed(tmp_path, owner)
    saved = original_bytes(ctx)
    endpoint = f'/api/novels/{ctx.sid}/stage2/analyze-draft'
    with TestClient(create_app(str(tmp_path))) as client:
        client.headers[UI_HEADER] = '1'
        read = client.get(endpoint, params={'owner': owner})
        assert read.status_code == 200, read.text
        view = read.json()
        assert view['owner'] == owner and view['source_state'] == 'draft_invalid'
        assert view['diagnostics'] and view['evidence_candidates']
        assert view['evidence_candidates'][0]['excerpt']
        assert all(path.read_bytes() == raw for path, raw in saved.items())
        # 批次 C：人工 POST 写面退役；OpenAPI 仅登记 GET。
        posted = client.post(endpoint, json={
            'actor': '人工验收员', 'reason': '据原文纠正闭集引用',
            'edit': view['template'],
        })
        assert posted.status_code == 405, posted.text
    assert all(path.read_bytes() == raw for path, raw in saved.items())


def test_analyze_draft_get_keeps_missing_book_and_closed_owner_explicit(tmp_path):
    ctx, _free = seed(tmp_path, 'stage2.characters')
    with TestClient(create_app(str(tmp_path))) as client:
        assert client.get('/api/novels/404/stage2/analyze-draft',
                          params={'owner': 'stage2.characters'}).status_code == 404
        assert client.get(f'/api/novels/{ctx.sid}/stage2/analyze-draft',
                          params={'owner': 'stage2.blueprint'}).status_code == 422
