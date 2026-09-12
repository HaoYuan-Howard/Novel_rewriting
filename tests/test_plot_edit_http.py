"""真实 HTTP → CLI 子进程 → 重启读取；所有模型回答使用合成传输。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from contracts.schema.stage2 import ChapterBlueprints
from novel.api.app import create_app, UI_HEADER
from novel.store import ArtifactStore
from novel.stages.stage2 import extract
from tests.test_plot_producer import prepare_book, generate_plot, plot_contract
from tests.test_stage2_generation import _ctx


@pytest.mark.real_code_version
def test_blueprint_edit_reaches_cli_and_survives_service_restart(tmp_path):
    book = prepare_book(tmp_path)
    producer, transport = generate_plot(book)
    baseline = ArtifactStore(producer).read_own(ChapterBlueprints, extract.BLUEPRINT_REL)[0]
    with TestClient(create_app(str(book.parent))) as http:
        http.headers.update({UI_HEADER: '1'})
        response = http.get('/api/novels/123/stage2/blueprint/edit/template?chapter_ordinal=1')
        assert response.status_code == 200, response.text
        request = response.json()
        request.update(actor='test:HTTP 编辑者', reason='合成原文支持的措辞订正')
        request['chapters'][0]['chapter']['beats'][0]['content'] = '李宝珠在昨夜的来信中得知父亲已经回城。'
        preview = http.post('/api/novels/123/stage2/blueprint/edit/preview', json=request)
        assert preview.status_code == 200 and preview.json()['would_apply'], preview.text
        response = http.post('/api/novels/123/stage2/blueprint/edit', json=request)
        assert response.status_code == 200, response.text
        job = response.json()
        if job['state'] != 'ok':
            from novel.orchestrator import jobs
            logs = jobs.read_log(book.parent, job['job_id'])
            pytest.fail(f'CLI 编辑未完成：{job}; {logs}')
        assert job['start']['paid'] is False
        assert '--apply-edit' in job['start']['steps'][0]['argv']
    # 新服务对象不复用上次查询缓存；章级修改和未接受状态来自盘面。
    with TestClient(create_app(str(book.parent))) as restarted:
        response = restarted.get('/api/novels/123/reading/1')
        assert response.status_code == 200, response.text
        assert response.json()['blueprint']['beats'][0]['content'] == request['chapters'][0]['chapter']['beats'][0]['content']
        progress = restarted.get('/api/novels/123/run')
        assert progress.status_code == 200, progress.text
        assert progress.json()['blueprint_progress']['review']['accepted_chapters'] == 0
    landed = ArtifactStore(_ctx(book, 'stage2.blueprint', paid=False)).read_own(
        ChapterBlueprints, extract.BLUEPRINT_REL, require_current=True)[0]
    assert len(landed.provenance.plot_revisions) == 1
    assert landed.blueprints[1:] == baseline.blueprints[1:]
    assert landed.provenance.usage == baseline.provenance.usage
    assert len(transport.calls) == 3
