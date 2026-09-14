"""已购草稿由只读 API 展示，typed 人工输入经真实免费 CLI 物化并重启读回。"""
from __future__ import annotations

import copy
import pytest
from fastapi.testclient import TestClient

from novel.api.app import UI_HEADER, create_app
from novel.stages.stage2 import analyze
from novel.store import ArtifactStore
from tests.test_stage2_analyze_draft_recovery import seed, original_bytes


@pytest.mark.real_code_version
@pytest.mark.parametrize('owner', ['stage2.characters', 'stage2.global'])
def test_analyze_draft_http_free_cli_restart_preserves_paid_source(tmp_path, owner):
    ctx, free = seed(tmp_path, owner)
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
        edit = copy.deepcopy(view['template'])
        assert edit['source_kind'] == 'analyze_owner_draft' and '_schema_version' not in edit
        if owner == 'stage2.characters':
            assert edit['characters']['characters'][0]['role_evidence_refs'] == ['e999999']
            from tests.test_stage2_analyze import _response
            edit['characters']['characters'][0]['role_evidence_refs'] = _response()['characters'][0]['role_evidence_refs']
        else:
            assert edit['global_draft']['relations'][0]['object_ref'] == 'c999'
            edit['global_draft']['relations'][0]['object_ref'] = 'c2'
        body = {'actor': '人工验收员', 'reason': '据原文纠正闭集引用', 'edit': edit}
        posted = client.post(endpoint, json=body)
        assert posted.status_code == 200, posted.text
        record = posted.json()
        assert record['start']['node'] == owner and record['start']['paid'] is False
        assert record['exit_code'] == 0, client.get(f'/api/jobs/{record["job_id"]}/log').json()
    with TestClient(create_app(str(tmp_path))) as restarted:
        restarted.headers[UI_HEADER] = '1'
        obsolete = restarted.get(endpoint, params={'owner': owner})
        assert obsolete.status_code == 404 and obsolete.json()['kind'] == 'UpstreamMissing'
        formal = restarted.get(f'/api/novels/{ctx.sid}/stage2/' + (
            'characters' if owner == 'stage2.characters' else 'global'))
        assert formal.status_code == 200, formal.text
        payload = formal.json()
        source = (payload['registry'] if owner == 'stage2.characters' else payload)['_provenance']
        assert source['shared_analysis_source']['receipt_sha256'] == edit['source_receipt_sha256']
        assert restarted.post(endpoint, json=body).json()['exit_code'] == 0
    assert all(path.read_bytes() == raw for path, raw in saved.items())
    assert not ArtifactStore(free).exists('stage2/chapter_blueprints.json')


def test_analyze_draft_get_keeps_missing_book_and_closed_owner_explicit(tmp_path):
    ctx, _free = seed(tmp_path, 'stage2.characters')
    with TestClient(create_app(str(tmp_path))) as client:
        assert client.get('/api/novels/404/stage2/analyze-draft',
                          params={'owner': 'stage2.characters'}).status_code == 404
        assert client.get(f'/api/novels/{ctx.sid}/stage2/analyze-draft',
                          params={'owner': 'stage2.blueprint'}).status_code == 422
