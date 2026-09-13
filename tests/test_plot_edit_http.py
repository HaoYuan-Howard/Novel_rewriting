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
        progress_shape = progress.json()['blueprint_progress']
        assert progress_shape['artifact_state'] == 'formal'
        assert 'review' not in progress_shape
    landed = ArtifactStore(_ctx(book, 'stage2.blueprint', paid=False)).read_own(
        ChapterBlueprints, extract.BLUEPRINT_REL, require_current=True)[0]
    assert len(landed.provenance.plot_revisions) == 1
    assert landed.blueprints[1:] == baseline.blueprints[1:]
    assert landed.provenance.usage == baseline.provenance.usage
    assert len(transport.calls) == 3


@pytest.mark.real_code_version
@pytest.mark.parametrize('damage', ['missing', 'wrong_owner', 'ambiguous'])
def test_unusable_actor_binding_is_409_and_free_cli_edit_retains_original_subject(tmp_path, damage):
    from contracts.schema.plot import ActorRef, ActorBinding
    from novel.stages.stage2.plot_diagnostics import read_repair_diagnostics
    from novel.orchestrator import approvals
    book = prepare_book(tmp_path)
    producer, transport = generate_plot(book)
    store = ArtifactStore(producer)
    original, _ = store.read_own(ChapterBlueprints, extract.BLUEPRINT_REL)
    candidate = original.model_copy(deep=True)
    chapter = candidate.blueprints[0]
    chapter.beats[0].participants[0] = ActorRef(char_id='char_001', presentation_ref='ap01_01')
    anchor = next(row for row in chapter.evidence_anchors if row.start_para == 1)
    if damage != 'missing':
        chapter.actor_bindings = [ActorBinding(presentation_id='ap01_01',
            char_id='char_002' if damage == 'wrong_owner' else 'char_001',
            surfaces=['李宝珠'], evidence_refs=[anchor.evidence_id])]
    if damage == 'ambiguous':
        chapter.actor_bindings.append(chapter.actor_bindings[0].model_copy(update={'presentation_id': 'ap01_02'}))
        chapter.beats[1].participants[0] = ActorRef(char_id='char_001', presentation_ref='ap01_02')
        # narrator/focalizer 仍为默认引用，在两个呈现中没有唯一解。
    store.write_invalid(extract.BLUEPRINT_REL, candidate, gates_skipped=[])
    formal_before = store.full_sha256_or_none(extract.BLUEPRINT_REL)
    invalid_before = store.full_sha256_or_none(extract._INVALID)
    diagnostic = read_repair_diagnostics(book.parent, '123', code=producer.code)
    assert diagnostic['diagnostics']
    assert all(row['owner'] == 'stage2.blueprint' for row in diagnostic['diagnostics'])

    with TestClient(create_app(str(book.parent))) as http:
        http.headers.update({UI_HEADER: '1'})
        # 审阅端点已退役；阅读端点仍可对照 INVALID 主体，但不把它冒充成 formal。
        reading = http.get('/api/novels/123/stage2/blueprint-reading-workpack?scope=chapter%3A1')
        assert reading.status_code == 200, reading.text
        assert reading.json()['binding']['subject_kind'] == 'invalid'
        response = http.get('/api/novels/123/stage2/blueprint/edit/template?chapter_ordinal=1')
        assert response.status_code == 200, response.text
        request = response.json()
        assert request['chapters'][0]['chapter']['beats'][0]['content'] == chapter.beats[0].content
        assert store.full_sha256_or_none(extract.BLUEPRINT_REL) == formal_before
        assert store.full_sha256_or_none(extract._INVALID) == invalid_before
        request.update(actor='test:HTTP 免费订正者', reason='恢复当前原文支持的明确人物引用')
        edited = request['chapters'][0]
        edited['chapter']['actor_bindings'] = []
        for row in edited['chapter']['beats']:
            for actor in row['participants']:
                actor['presentation_ref'] = None
        for operation in edited['identities']:
            if operation['collection'] == 'actor_bindings':
                operation.update(kind='replace', new_keys=[])
        preview = http.post('/api/novels/123/stage2/blueprint/edit/preview', json=request)
        assert preview.status_code == 200 and preview.json()['would_apply'], preview.text
        result = http.post('/api/novels/123/stage2/blueprint/edit', json=request)
        assert result.status_code == 200, result.text
        assert result.json()['state'] == 'ok', result.text
        assert result.json()['start']['paid'] is False
    with TestClient(create_app(str(book.parent))) as restarted:
        response = restarted.get('/api/novels/123/stage2/blueprint/edit/template?chapter_ordinal=1')
        assert response.status_code == 200, response.text
        assert response.json()['chapters'][0]['chapter']['actor_bindings'] == []
    assert not store.exists(extract._INVALID)
    assert len(transport.calls) == 3
