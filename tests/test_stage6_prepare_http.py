"""Prepare HTTP 只读面与退役写面；正式接受由 producer auto accept。

批次 B4：``POST .../edit`` / ``.../review`` 退役；机械通过即
``system:stage6-prepare-auto``。本文件钉住只读 GET、写面 405，以及
``--recover-edit`` 受管恢复仍经 jobs 可达。
"""
from __future__ import annotations

import copy
import json
import time

import pytest
from fastapi.testclient import TestClient

from contracts.artifacts import STAGE1_HOOK_COPY
from contracts.schema.stage1 import HookCopy
from contracts.schema.stage6_prepare import Stage6Prepare, PrepareMutationJournal
from novel.api.app import UI_HEADER, create_app
from novel.orchestrator import jobs
from novel.llm.cache import ResponseCache
from novel.llm.client import LlmClient
from novel.llm.transport import ChatResponse, FakeTransport
from novel.stages.stage6 import prepare
from novel.store import ArtifactStore
from tests.test_plot_design import produce_through_first_chapter
from tests.test_plot_pipeline import complete_stage5, grounded_global
from tests.test_stage2_generation import _ctx
from tests.test_stage2_global_agent import _free_ctx
from tests.test_stage6_prepare import ProcessExit, edit_for, response


def _pack(http):
    result = http.get('/api/novels/123/stage6/prepare/workpack')
    assert result.status_code == 200, result.text
    return result.json()


@pytest.mark.real_code_version
def test_prepare_http_readonly_auto_accept_and_retired_write_surfaces(
        tmp_path, monkeypatch):
    book = produce_through_first_chapter(
        tmp_path, monkeypatch, distributed=True,
        grounding=grounded_global(), real_code_identity=True)
    complete_stage5(book)
    ctx = _ctx(book, 'stage6.prepare')
    initial = response()
    source_hook = ArtifactStore(ctx).read_own(HookCopy, STAGE1_HOOK_COPY)[0].text.strip()
    initial['source_blurb']['facts'][1]['claim'] = source_hook
    transport = FakeTransport(responder=lambda _req, _index: ChatResponse(
        content=json.dumps(initial, ensure_ascii=False), finish_reason='stop',
        usage={'prompt_tokens': 10, 'completion_tokens': 20},
        model='test-model'))
    client = LlmClient(ctx, transport, cache=ResponseCache(),
        model='test-model', max_retries=0, concurrency=2,
        budget_checker=lambda *_args, **_kwargs: None)
    original = prepare.run_prepare(ctx, client)
    assert len(transport.calls) == 1
    ledger_before = ctx.ledger.path.read_bytes()

    with TestClient(create_app(str(book.parent))) as http:
        http.headers.update({UI_HEADER: '1'})
        status = http.get('/api/novels/123/stage6/prepare')
        assert status.status_code == 200, status.text
        body = status.json()
        assert body['status']['state'] == 'accepted'
        assert body['status']['review']['actor'] == 'system:stage6-prepare-auto'
        pack = _pack(http)
        assert pack['state']['state'] == 'accepted'
        for path in (
                '/api/novels/123/stage6/prepare/edit/preview',
                '/api/novels/123/stage6/prepare/edit',
                '/api/novels/123/stage6/prepare/review'):
            assert http.post(path, json={}).status_code == 405, path
        gates = http.get('/api/novels/123/approvals')
        assert gates.status_code == 200, gates.text
        assert gates.json()['gate']['s5'] and not gates.json()['gate']['s6']

    # 内部 apply_edit 中断后，受管 --recover-edit 仍经 jobs 可达；
    # 恢复的是 edit 提交，不会自动 accept（auto accept 只挂在正式生成路径）。
    free = _free_ctx(ctx)
    pending_draft = copy.deepcopy(pack['edit_template']['source_blurb'])
    pending_draft['facts'][2]['reason'] = '重新核验完整正文和原钩子，均未建立地契命题'
    edit = edit_for(free, {'source_blurb': pending_draft})
    with monkeypatch.context() as interrupted:
        interrupted.setattr(
            prepare, '_finish_journal',
            lambda *_a, **_k: (_ for _ in ()).throw(ProcessExit()))
        with pytest.raises(ProcessExit):
            prepare.apply_edit(free, edit)
    with TestClient(create_app(str(book.parent))) as reopened:
        reopened.headers.update({UI_HEADER: '1'})
        status = reopened.get('/api/novels/123/stage6/prepare')
        assert status.status_code == 200 and status.json()['status']['pending_commit'], status.text
        actions = reopened.get('/api/novels/123/approvals')
        assert actions.status_code == 200, actions.text
        action = next(
            row for row in actions.json()['spawns']['s5']
            if row['kind'] == 'stage6.prepare' and row['extra'] == ['--recover-edit'])
        request = {'sid': '123', 'node': action['kind'], 'flags': action['extra']}
        preview = reopened.post('/api/jobs/preview', json=request)
        assert preview.status_code == 200, preview.text
        started = reopened.post('/api/jobs', json=request)
        assert started.status_code == 202, started.text
        job = started.json()
        until = time.monotonic() + 60
        while job['state'] in {'queued', 'running'} and time.monotonic() < until:
            time.sleep(0.1)
            loaded = reopened.get('/api/jobs/' + job['job_id'])
            assert loaded.status_code == 200, loaded.text
            job = loaded.json()
        if job['state'] != 'ok':
            pytest.fail(f'恢复未完成：{job}; {jobs.read_log(book.parent, job["job_id"])}')
        assert job['start']['paid'] is False
        status = reopened.get('/api/novels/123/stage6/prepare').json()['status']
        assert status['state'] == 'pending' and not status['pending_commit']
        recovered_store = ArtifactStore(_free_ctx(ctx))
        journal = recovered_store.read_own(PrepareMutationJournal, prepare.COMMIT)[0]
        assert journal.state == 'completed' and journal.request == edit
        recovered = recovered_store.read_own(Stage6Prepare, prepare.REL)[0]
        assert recovered == journal.candidate
        assert recovered.source_blurb.facts[2].reason == pending_draft['facts'][2]['reason']

    final = ArtifactStore(_free_ctx(ctx)).read_own(Stage6Prepare, prepare.REL)[0]
    assert final.provenance.usage == original.provenance.usage
    assert len(transport.calls) == 1 and ctx.ledger.path.read_bytes() == ledger_before
