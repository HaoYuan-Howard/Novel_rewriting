"""实际 producer 中断后，运行页仍能免费读到检查点，且不产生审阅工作包或审批待办。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from novel import paths

from novel.stages.stage2 import run_state as stage2_run_state
from novel.api.app import create_app
from novel.llm import stage2_runs
from novel.stages.stage2 import extract
from novel.stages.stage2.plot_inputs import read_input_digests
from novel.store import ArtifactStore
from tests.test_plot_producer import prepare_book, responses, plot_contract  # noqa: F401
from tests.test_stage2_generation import _ctx, _client, _ok


def test_progress_survives_before_formal_commit_and_rejects_damaged_checkpoint(tmp_path, monkeypatch):
    book = prepare_book(tmp_path)
    ctx = _ctx(book, 'stage2.blueprint')
    events = []
    ctx.observer = events.append
    by_chapter = responses()
    client, transport = _client(ctx, lambda req, _index: _ok({
        'blueprints': [by_chapter[json.loads(req.user.split('\n', 1)[0])['chapter_ordinal']]],
    }))
    monkeypatch.setattr(stage2_runs, 'prepare_commit',
                        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError('提交前中断')))
    with pytest.raises(RuntimeError, match='提交前中断'):
        extract.run_blueprint(ctx, client)
    completed = [row for row in events if row['event'] == 'chapter_progress']
    assert [(row['chapter'], row['chapter_index'], row['chapter_total'],
             row['completed'], row['succeeded']) for row in completed] == [
        (1, 1, 3, 1, 1), (2, 2, 3, 2, 2), (3, 3, 3, 3, 3)]
    started = [row for row in events
               if row['event'] == 'llm_attempt_heartbeat' and row['state'] == 'started']
    assert [(row['chapter_index'], row['chapter_total']) for row in started] == [
        (1, 3), (2, 3), (3, 3)]
    assert not ArtifactStore(ctx).exists(extract.BLUEPRINT_REL)
    # 进度读面只回答检查点进度；不构建任何 Commander 工作包，也不产生审批待办。
    # 两个应用实例模拟刷新/服务重启；都从持久记录重建相同的进度。
    snapshots = []
    for _ in range(2):
        with TestClient(create_app(str(book.parent))) as http:
            reply = http.get(f'/api/novels/{ctx.sid}/run')
            assert reply.status_code == 200, reply.text
            progress = reply.json()['blueprint_progress']
            assert progress['total_chapters'] == progress['generated_chapters'] == 3
            assert progress['checkpoint_chapters'] == 3
            assert progress['submitted_chapters'] == 0
            assert progress['artifact_state'] == 'missing' and progress['review'] is None
            snapshots.append(progress)
    assert snapshots[0] == snapshots[1]
    assert len(transport.calls) == 3
    inputs = read_input_digests(ArtifactStore(ctx))
    changed = stage2_run_state.plot_checkpoint_progress(ctx, run_id=ctx.run_id,
                                                   chapter=1, input_sha256='0' * 64)
    assert changed['state'] == 'expired'
    target = paths.stage2_blueprint_checkpoint_path(book, ctx.run_id, 'blueprint', 1)
    raw = json.loads(target.read_text(encoding='utf-8'))
    raw['response_payload']['blueprints'][0]['beats'][0]['content'] = '伪造剧情'
    target.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    damaged = stage2_run_state.plot_checkpoint_progress(ctx, run_id=ctx.run_id, chapter=1,
                                                  input_sha256=inputs['1'])
    assert damaged['state'] == 'unver'
    with TestClient(create_app(str(book.parent))) as http:
        reply = http.get(f'/api/novels/{ctx.sid}/run')
        assert reply.status_code == 200, reply.text
        progress = reply.json()['blueprint_progress']
        assert progress['generated_chapters'] is None
        assert progress['checkpoint_chapters'] is None
        assert progress['chapters'][0]['checkpoint_state'] == 'unver'
    assert len(transport.calls) == 3
