"""真实 CLI 与直接 producer 的新旧 base 边界；全部使用合成文本、零模型。"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from novel.cli.main import main
from novel.run import RunContext
from novel.stages.stage0.removal import run_source
from novel.stages.stage1.partition import MAIN_TEXT, run_partition
from novel.store import ArtifactStore
from novel.work_units import WorkUnitRefused, workflow_manifest
from tests.fixtures_ingest import body


def _tree(root: Path) -> dict[str, bytes | None]:
    return {path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else None
            for path in root.rglob('*')}


def _current_and_frozen(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / 'FQ_准入验证_42.txt'
    source.write_text(body(), encoding='utf-8')
    current = tmp_path / 'current'
    novel_dir, _result, _report = run_source(source, current)
    # stage0 机械通过即自动接受（2026-09-15）：首跑即 approved，无人工批准步骤。
    run_partition(RunContext.open(current, '42', 'stage1.partition', paid=False))
    # 旧 base 独立构造：只携原产物，没有当前工作流 manifest 或新预算。
    frozen = tmp_path / 'frozen'
    shutil.copytree(novel_dir, frozen / novel_dir.name)
    assert workflow_manifest(current) is not None
    assert workflow_manifest(frozen) is None
    return current, frozen


def test_free_cli_refuses_frozen_base_without_writing_any_file(tmp_path):
    _current, frozen = _current_and_frozen(tmp_path)
    before = _tree(frozen)
    assert main(['run', 'stage1.partition', '42', '--base', str(frozen)]) == 2
    assert _tree(frozen) == before


@pytest.mark.parametrize('operation', ['producer', 'archive', 'text', 'transaction'])
def test_direct_free_mutator_refuses_frozen_base_before_any_change(tmp_path, operation):
    _current, frozen = _current_and_frozen(tmp_path)
    ctx = RunContext.open(frozen, '42', 'stage1.partition', paid=False)
    store = ArtifactStore(ctx)
    before = _tree(frozen)
    with pytest.raises(WorkUnitRefused, match='冻结旧版本'):
        if operation == 'producer':
            run_partition(ctx)
        elif operation == 'archive':
            store.archive_round(MAIN_TEXT)
        elif operation == 'text':
            store.write_text('stage0/clean_text.txt', '不能覆盖旧正文\n')
        else:
            with store.history_transaction(MAIN_TEXT):
                pytest.fail('旧 base 不应取得写事务')
    assert _tree(frozen) == before


def test_frozen_check_only_is_readable_and_current_free_producer_still_writes(tmp_path):
    current, frozen = _current_and_frozen(tmp_path)
    before = _tree(frozen)
    assert main(['run', 'stage1.partition', '42', '--base', str(frozen), '--check-only']) == 0
    assert _tree(frozen) == before
    assert main(['run', 'stage1.partition', '42', '--base', str(current)]) == 0
    assert workflow_manifest(current) is not None


def test_stage0_dry_run_does_not_initialize_or_materialize_workflow(tmp_path):
    source = tmp_path / 'FQ_纯读预览_43.txt'
    source.write_text(body(), encoding='utf-8')
    fresh = tmp_path / 'preview'
    assert main(['run', 'stage0.remove', '--base', str(fresh), '--source', str(source),
                 '--dry-run']) == 0
    assert not fresh.exists()


@pytest.mark.parametrize('entry', ['run', 'start'])
def test_shared_job_write_entry_refuses_before_writer_or_events(tmp_path, monkeypatch, entry):
    from novel.orchestrator import jobs

    _current, frozen = _current_and_frozen(tmp_path)
    before = _tree(frozen)
    monkeypatch.setattr(jobs, 'acquire_job_writer',
                        lambda *a, **kw: pytest.fail('旧 base 不得占 writer 或落启动事件'))
    with pytest.raises(WorkUnitRefused, match='冻结旧版本'):
        getattr(jobs, entry)(frozen, 'stage1.partition', '42')
    assert _tree(frozen) == before


def test_http_job_rejects_frozen_write_and_keeps_history_readable(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from novel.api.app import UI_HEADER, create_app
    from novel.orchestrator import jobs

    _current, frozen = _current_and_frozen(tmp_path)
    before = _tree(frozen)
    monkeypatch.setattr(jobs, 'acquire_job_writer',
                        lambda *a, **kw: pytest.fail('HTTP 不得为旧 base 占 writer'))
    with TestClient(create_app(frozen)) as client:
        response = client.post('/api/jobs', headers={UI_HEADER: '1'}, json={
            'node': 'stage1.partition', 'sid': '42', 'flags': [],
        })
        assert response.status_code == 409
        assert response.json()['kind'] == 'WorkUnitRefused'
        assert client.get('/api/jobs').status_code == 200
        assert client.get('/api/queues').status_code == 200
    assert _tree(frozen) == before


@pytest.mark.parametrize('entry', ['create', 'run', 'start', 'resume', 'resume_start'])
def test_persistent_queue_write_entries_refuse_frozen_base_without_events(tmp_path, entry):
    from novel.orchestrator import queue

    current, frozen = _current_and_frozen(tmp_path)
    original = queue.create(current, ['42'], until_stage='stage2', yes=True)
    frozen_events = queue.queue_path(frozen)
    frozen_events.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(queue.queue_path(current), frozen_events)
    before = _tree(frozen)
    assert queue.records(frozen)[0].queue_id == original.queue_id
    with pytest.raises(WorkUnitRefused, match='冻结旧版本'):
        if entry == 'create':
            queue.create(frozen, ['42'], until_stage='stage2', yes=True)
        elif entry in ('resume', 'resume_start'):
            getattr(queue, entry)(frozen, original.queue_id, yes=True)
        else:
            getattr(queue, entry)(frozen, original.queue_id)
    assert _tree(frozen) == before


def test_startup_keeps_frozen_queue_readable_without_reconciling_or_starting(tmp_path):
    from novel.orchestrator import queue

    current, frozen = _current_and_frozen(tmp_path)
    original = queue.create(current, ['42'], until_stage='stage2', yes=True)
    frozen_events = queue.queue_path(frozen)
    frozen_events.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(queue.queue_path(current), frozen_events)
    before = _tree(frozen)
    assert queue.resume_incomplete(frozen) == []
    assert queue.records(frozen)[0].queue_id == original.queue_id
    assert _tree(frozen) == before


def test_read_only_job_is_not_closed_by_frozen_write_guard(tmp_path):
    from novel.orchestrator import jobs

    _current, frozen = _current_and_frozen(tmp_path)
    book = next(path for path in frozen.iterdir() if path.is_dir())
    artifacts = _tree(book)
    calls = []

    def spawn(argv, *args, **kwargs):
        calls.append(argv)
        return 0

    result = jobs.run(frozen, 'stage1.partition', '42', ['--check-only'],
                      spawn=spawn, follow_check=False)
    assert result.state == 'ok' and len(calls) == 1
    assert _tree(book) == artifacts
    assert workflow_manifest(frozen) is None
