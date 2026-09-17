#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上传幂等自动开跑（R-11.28）合成反例：无真实模型。"""
from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path

import pytest

fastapi = pytest.importorskip(
    'fastapi', reason='没装 fastapi ⇒ 这一批没在查，不是查过没事')
from fastapi.testclient import TestClient  # noqa: E402

from contracts.schema.orchestration import (  # noqa: E402
    AgentRunRecord, AgentRunStart, LegacyExecutorBackend, LegacyLlmProfile,
    JobRecord, JobStart, PipelineAgentProfiles, Step,
)
from novel.api.app import UI_HEADER, create_app  # noqa: E402
from novel.orchestrator import agent_settings, upload_autostart, uploads  # noqa: E402

UI = {UI_HEADER: '1'}
FILENAME = 'FQ_联调样本_9001.txt'
FILENAME_ALT = 'FQ_联调样本副本_9002.txt'
TEXT = (
    '开篇钩子。\n\n'
    '1\n\n'
    '第一章正文。她走进门里，没有回头。\n'
)
KEY = 'upload-key-9001'


def _profiles() -> PipelineAgentProfiles:
    return PipelineAgentProfiles(
        executor_default=LegacyExecutorBackend(
            profile=LegacyLlmProfile(
                provider='deepseek', model='deepseek-v4-pro',
                reasoning_effort='high', thinking_mode='on')),
        executor_overrides={},
    )


def _run_record(*, run_id: str = 'run-once', sid: str = '9001',
                base: str = 'E:/tmp') -> AgentRunRecord:
    start = AgentRunStart(
        sid=sid, base=base, argv=None, prompt=None,
        authorized=True, egress_authorized=True,
        code_version='test-code', identity_version=2,
        profiles=_profiles())
    return AgentRunRecord(
        run_id=run_id, start=start, started=1,
        ended=None, exit_code=None, state='running',
        allowed_controls=['stop'])


def _failed_job() -> JobRecord:
    step = Step(
        argv=['python', '-m', 'novel.cli', 'stage0.remove'],
        cwd='.', label='stage0.remove', paid=False, writes='stage0')
    return JobRecord(
        job_id='job-fail',
        start=JobStart(
            sid=None, node='stage0.remove', flags=[],
            steps=[step], python='3.11', code='test',
            paid=False, writes=True),
        started=1, ended=2, exit_code=2, state='failed',
        steps_started=1, note=None)


@pytest.fixture
def root():
    value = Path(tempfile.mkdtemp(prefix='upload-autostart-'))
    try:
        yield value
    finally:
        shutil.rmtree(value, ignore_errors=True)


@pytest.fixture
def client(root):
    settings = agent_settings.read(root)
    agent_settings.update(
        root, profiles=_profiles(), expected_revision=settings.revision)
    with TestClient(create_app(root)) as value:
        yield value


def test_same_idempotency_key_replays_same_sid_and_run(client, root, monkeypatch):
    starts: list[str] = []

    def fake_start(base, sid, *, authorized, egress_authorized, profiles):
        from contracts.schema.orchestration import AgentRunEvent, RunContractV2
        from novel.orchestrator import agent as agent_mod
        starts.append(sid)
        record = _run_record(run_id='run-once', sid=sid, base=str(base))
        contract = RunContractV2(
            sid=sid, base=str(base), authorized=True, egress_authorized=True,
            profiles=profiles)
        agent_mod.append(base, AgentRunEvent(
            wire_version=2, run_id=record.run_id, kind='started',
            ts=1, contract=contract, start=record.start))
        return record

    monkeypatch.setattr('novel.api.routes.ingest.agent.start', fake_start)

    headers = {
        **UI,
        'Content-Type': 'application/octet-stream',
        'X-Novel-Idempotency-Key': KEY,
    }
    params = {
        'filename': FILENAME,
        'authorized': 'true',
        'egress_authorized': 'true',
    }
    first = client.post(
        '/api/upload', params=params, content=TEXT.encode('utf-8'), headers=headers)
    assert first.status_code == 200, first.text
    body1 = first.json()
    assert body1['sid'] == '9001'
    assert body1['autostart'] == 'started'
    assert body1['run']['run_id'] == 'run-once'
    assert body1['idempotency_key'] == KEY
    assert starts == ['9001']

    second = client.post(
        '/api/upload', params=params, content=TEXT.encode('utf-8'), headers=headers)
    assert second.status_code == 200, second.text
    body2 = second.json()
    assert body2['sid'] == body1['sid']
    assert body2['run']['run_id'] == body1['run']['run_id']
    assert body2['autostart'] == 'replay'
    assert body2['idempotency_key'] == KEY
    assert starts == ['9001'], '重放不得再次 agent.start'


def test_stage0_failure_does_not_start_paid_run(client, monkeypatch):
    starts: list[str] = []

    monkeypatch.setattr(
        'novel.api.routes.ingest.jobs.run', lambda *a, **k: _failed_job())
    monkeypatch.setattr(
        'novel.api.routes.ingest.agent.start',
        lambda *a, **k: starts.append('hit') or (_ for _ in ()).throw(
            AssertionError('stage0 失败后不得 agent.start')))

    response = client.post(
        '/api/upload',
        params={
            'filename': FILENAME,
            'authorized': 'true',
            'egress_authorized': 'true',
            'idempotency_key': 'stage0-fail-key',
        },
        content=TEXT.encode('utf-8'),
        headers={**UI, 'Content-Type': 'application/octet-stream'},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['autostart'] == 'skipped_stage0_failed'
    assert body.get('run') is None
    assert starts == []
    folded = upload_autostart.find_by_key(client.app.state.base, 'stage0-fail-key')
    assert folded is not None and folded.terminal is not None
    assert folded.terminal.event == 'stage0_failed'


def test_drain_starts_once_after_crash_with_stage0_ok(root, monkeypatch):
    profiles = _profiles()
    digest = hashlib.sha256(TEXT.encode('utf-8')).hexdigest()
    uploads.save_upload(root, FILENAME, TEXT.encode('utf-8'))
    (root / 'FQ_联调样本_9001').mkdir()
    upload_autostart.record_requested(
        root, idempotency_key='crash-key-01', sid='9001',
        source_sha256=digest, filename=FILENAME,
        authorized=True, egress_authorized=True, profiles=profiles)
    assert upload_autostart.pending_autostarts(root)
    starts: list[str] = []

    def fake_start(base, sid, *, authorized, egress_authorized, profiles):
        starts.append(sid)
        return _run_record(run_id='run-drain', sid=sid, base=str(base))

    outcome = upload_autostart.drain(root, agent_start=fake_start)
    assert outcome == 'done'
    assert starts == ['9001']
    folded = upload_autostart.find_by_key(root, 'crash-key-01')
    assert folded.terminal is not None
    assert folded.terminal.event == 'run_started'
    assert folded.terminal.run_id == 'run-drain'

    assert upload_autostart.drain(root, agent_start=fake_start) == 'idle'
    assert starts == ['9001']


def test_content_addressed_dedup_by_filename(root):
    path1, sid1 = uploads.save_upload(root, FILENAME, TEXT.encode('utf-8'))
    path2, sid2 = uploads.save_upload(root, FILENAME, TEXT.encode('utf-8'))
    assert sid1 == sid2 == '9001'
    assert path1 == path2

    path3, sid3 = uploads.save_upload(root, FILENAME_ALT, TEXT.encode('utf-8'))
    assert sid3 == '9002'
    assert path3 != path1
    assert path3.parent == path1.parent


def test_authorized_false_skips_paid_start(client, monkeypatch):
    starts: list[str] = []
    monkeypatch.setattr(
        'novel.api.routes.ingest.agent.start',
        lambda *a, **k: starts.append('hit') or _run_record())

    response = client.post(
        '/api/upload',
        params={'filename': FILENAME, 'authorized': 'false'},
        content=TEXT.encode('utf-8'),
        headers={**UI, 'Content-Type': 'application/octet-stream'},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['sid'] == '9001'
    assert body['job']['state'] == 'ok'
    assert body.get('autostart') is None
    assert starts == []
    assert not upload_autostart.pending_autostarts(client.app.state.base)
