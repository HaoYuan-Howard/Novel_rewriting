"""Prepare HTTP→受管免费 CLI→重启：只替换上游与 Prepare 的模型运输。"""
from __future__ import annotations

import copy
import json
import time

import pytest
from fastapi.testclient import TestClient

from contracts.artifacts import STAGE0_CLEAN_TEXT, STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT
from contracts.schema.stage1 import HookCopy
from contracts.schema.stage6_prepare import Stage6Prepare, PrepareMutationJournal
from novel.api.app import UI_HEADER, create_app
from novel.orchestrator.node_agents import Stage6PrepareAgent
from novel.orchestrator import jobs
from novel.stages.stage6 import prepare
from novel.store import ArtifactStore
from tests.test_plot_design import produce_through_first_chapter
from tests.test_plot_pipeline import complete_stage5, grounded_global
from tests.test_stage2_generation import _ctx
from tests.test_stage2_global_agent import _Driver, _free_ctx, _profile
from tests.test_stage6_prepare import ProcessExit, edit_for, response


def _business(ctx):
    return {str(path.relative_to(ctx.novel_dir)): path.read_bytes()
            for path in ctx.novel_dir.rglob('*') if path.is_file()}


def _pack(http):
    result = http.get('/api/novels/123/stage6/prepare/workpack')
    assert result.status_code == 200, result.text
    return result.json()


def _request(pack, draft):
    return {'review_token': pack['state']['review_token'],
            'actor': 'test:HTTP 准备审阅者', 'reason': '对照原文修订支持命题与证据',
            'source_blurb': draft}


def _job(result, base):
    assert result.status_code == 200, result.text
    record = result.json()
    if record['state'] != 'ok':
        pytest.fail(f'免费 CLI 未完成：{record}; {jobs.read_log(base, record["job_id"])}')
    assert record['start']['paid'] is False
    return record


def _accept(http, base):
    pack = _pack(http)
    _job(http.post('/api/novels/123/stage6/prepare/review', json={
        'review_token': pack['state']['review_token'], 'actor': 'test:HTTP 准备审阅者',
        'reason': '原文和原钩子逐项核验，支持类型与连续范围成立',
        'decision': 'accept', 'edit': None}), base)
    status = http.get('/api/novels/123/stage6/prepare')
    assert status.status_code == 200 and status.json()['status']['state'] == 'accepted', status.text
    gates = http.get('/api/novels/123/approvals')
    assert gates.status_code == 200, gates.text
    assert gates.json()['gate']['s5'] and not gates.json()['gate']['s6']


def _revise_source_hook(book, edit_path, new_text):
    """从正式 Stage0 编辑、批准再分章；MainText 字节保持原样。"""
    from novel.stages.stage0.removal import _context, run_edit, run_approve
    from novel.stages.stage1.partition import run_partition
    read = ArtifactStore(_context(book, 'stage0.edit'))
    old_hook = read.read_own(HookCopy, STAGE1_HOOK_COPY)[0].text
    before = read.full_sha256_or_none(STAGE1_MAIN_TEXT)
    clean = read.read_text(STAGE0_CLEAN_TEXT)
    assert clean.startswith(old_hook)
    edit_path.write_text(new_text + '\n\n' + clean[len(old_hook):], encoding='utf-8', newline='')
    _, edited = run_edit(_context(book, 'stage0.edit'), edit_path,
        actor='test:源钩子订正者', reason='仅修订源钩子措辞，章节正文保持不变')
    # 源文编辑会主动撤销旧批准；这里只允许预期的待批准阻断。
    assert not edited.passed
    assert [row.rule for row in edited.rules if row.sev == 'hard' and row.status == 'hit'] == ['s0_approval']
    assert not any(row.status == 'skipped' for row in edited.rules)
    _, approved = run_approve(_context(book, 'stage0.approve'),
        actor='test:源钩子审阅者', reason='已核对新钩子及未变更的全部章节')
    assert approved.passed, approved.model_dump_json()
    _, partitioned = run_partition(_context(book, 'stage1.partition'))
    assert partitioned.passed, partitioned.model_dump_json()
    assert ArtifactStore(_context(book, 'stage1.partition')).full_sha256_or_none(STAGE1_MAIN_TEXT) == before


@pytest.mark.real_code_version
def test_prepare_http_free_edits_material_change_acceptance_and_pending_recovery(tmp_path, monkeypatch):
    # CLI 的真实写入口要求 Stage5 已闭合；不伪造 gate 或跳过前置检查。
    book = produce_through_first_chapter(tmp_path, monkeypatch, distributed=True,
        grounding=grounded_global(), real_code_identity=True)
    complete_stage5(book)
    ctx = _ctx(book, 'stage6.prepare')
    ctx.executor_profile = _profile()
    store = ArtifactStore(ctx)
    source_hook = store.read_own(HookCopy, STAGE1_HOOK_COPY)[0].text.strip()
    initial = response()
    initial['source_blurb']['facts'][1]['claim'] = source_hook
    driver = _Driver([json.dumps(initial, ensure_ascii=False)], book, session_id=ctx.run_id)
    original = Stage6PrepareAgent(ctx, driver=driver).run().artifact
    assert len(driver.calls) == 1
    ledger_before = ctx.ledger.path.read_bytes()

    with TestClient(create_app(str(book.parent))) as http:
        http.headers.update({UI_HEADER: '1'})
        before = _business(ctx)
        pack = _pack(http)
        assert pack['state']['state'] == 'pending'
        draft = copy.deepcopy(pack['edit_template']['source_blurb'])
        draft['facts'][0]['claim'] = '李宝珠把那封信折好，收在衣袖里。'
        request = _request(pack, draft)
        preview = http.post('/api/novels/123/stage6/prepare/edit/preview', json=request)
        assert preview.status_code == 200 and preview.json()['would_apply'], preview.text
        assert _business(ctx) == before, '读工作包与预检不得写候选、决定或历史'
        edited = _job(http.post('/api/novels/123/stage6/prepare/edit', json=request), book.parent)
        assert '--apply-edit' in edited['start']['steps'][0]['argv']
        landed = ArtifactStore(_free_ctx(ctx)).read_own(Stage6Prepare, prepare.REL)[0]
        assert landed.source_blurb.facts[0].claim == draft['facts'][0]['claim']
        assert landed.provenance.usage == original.provenance.usage
        old = http.post('/api/novels/123/stage6/prepare/edit', json=request)
        assert old.status_code == 409, old.text
        _accept(http, book.parent)

    # 真实材料变更使原 Hook evidence 不再匹配。新服务仍能给出未决模板与免费修订口。
    changed_hook = '父亲已回城的消息，随着来信送到李宝珠手中。'
    body_before = {name: raw for name, raw in _business(ctx).items()
                   if name.split('/')[0] in {'stage2', 'stage4', 'stage5'}}
    _revise_source_hook(book, tmp_path / 'prepare_source_edit.txt', changed_hook)
    with TestClient(create_app(str(book.parent))) as restarted:
        restarted.headers.update({UI_HEADER: '1'})
        body_gates = restarted.get('/api/novels/123/approvals')
        assert body_gates.status_code == 200, body_gates.text
        assert body_gates.json()['gate']['s5'], '仅钩子修订不得重签或失效已闭合正文'
        assert {name: raw for name, raw in _business(ctx).items()
                if name.split('/')[0] in {'stage2', 'stage4', 'stage5'}} == body_before
        pack = _pack(restarted)
        assert pack['state']['state'] == 'stale'
        by_id = {fact['claim_id']: fact for fact in pack['edit_template']['source_blurb']['facts']}
        assert by_id['hook_002']['kind'] == 'unresolved'
        fixed = copy.deepcopy(pack['edit_template']['source_blurb'])
        fixed['facts'][1] = {'kind': 'hook_supported', 'claim_id': 'hook_002',
            'claim': changed_hook, 'hook_evidence': {'start_para': 1, 'end_para': 1}}
        fixed_request = _request(pack, fixed)
        preview = restarted.post('/api/novels/123/stage6/prepare/edit/preview', json=fixed_request)
        assert preview.status_code == 200 and preview.json()['would_apply'], preview.text
        _job(restarted.post('/api/novels/123/stage6/prepare/edit', json=fixed_request), book.parent)
        _accept(restarted, book.parent)

    # 模拟原请求已保存、业务提交尚未开始；网页必须给受管免费恢复动作。
    free = _free_ctx(ctx)
    pending_draft = copy.deepcopy(fixed)
    pending_draft['facts'][2]['reason'] = '重新核验完整正文和原钩子，均未建立地契命题'
    edit = edit_for(free, {'source_blurb': pending_draft})
    with monkeypatch.context() as interrupted:
        def stop_before_commit(*_args, **_kwargs):
            raise ProcessExit()
        interrupted.setattr(prepare, '_finish_journal', stop_before_commit)
        with pytest.raises(ProcessExit):
            prepare.apply_edit(free, edit)
    with TestClient(create_app(str(book.parent))) as reopened:
        reopened.headers.update({UI_HEADER: '1'})
        status = reopened.get('/api/novels/123/stage6/prepare')
        assert status.status_code == 200 and status.json()['status']['pending_commit'], status.text
        blocked = reopened.post('/api/novels/123/stage6/prepare/edit/preview',
                                json=_request(_pack(reopened), fixed))
        assert blocked.status_code == 409, blocked.text
        actions = reopened.get('/api/novels/123/approvals')
        assert actions.status_code == 200, actions.text
        action = next(row for row in actions.json()['spawns']['s5']
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
        _accept(reopened, book.parent)

    final = ArtifactStore(_free_ctx(ctx)).read_own(Stage6Prepare, prepare.REL)[0]
    assert final.provenance.usage == original.provenance.usage
    assert final.provenance.source_rebuild.generation_root_clean_sha1 == original.provenance.root_clean_sha1
    assert final.provenance.root_clean_sha1 != original.provenance.root_clean_sha1
    assert {name: raw for name, raw in _business(ctx).items()
            if name.split('/')[0] in {'stage2', 'stage4', 'stage5'}} == body_before
    assert len(driver.calls) == 1 and ctx.ledger.path.read_bytes() == ledger_before
