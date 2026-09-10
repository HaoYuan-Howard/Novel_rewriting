#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可提交最小 fixture 注册表：存在理由、可复算字节与消费覆盖双向守卫。"""
from __future__ import annotations

import json
import hashlib
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.schema import ArtifactVersionError, artifact_spec, decode_artifact
from contracts.schema.stage5 import RejectedRevision
from novel import paths
from novel.llm.cache import cache_key
from novel.llm.client import LlmClient
from novel.llm.transport import ChatRequest, ChatResponse, FakeTransport, TransportError
from novel.run import RunContext
from novel.stages.stage5 import checks, write

ROOT = Path(__file__).parent / 'fixtures'
MANIFEST = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
ENTRIES = {row['id']: row for row in MANIFEST['fixtures']}


def _load(fixture_id: str):
    return json.loads((ROOT / ENTRIES[fixture_id]['path']).read_text(encoding='utf-8'))


def _long_context(_monkeypatch) -> None:
    recipe = _load('long_context')
    user = recipe['unit'] * recipe['repeat']
    changed = user[:-1] + recipe['mutate_last_to']
    common = dict(model='fixture-model', system='SYS', reasoning_effort='low',
                  thinking=False, json_mode=False, max_tokens=128)
    assert len(user.encode('utf-8')) > 60_000
    assert cache_key(ChatRequest(user=user, **common)) != cache_key(
        ChatRequest(user=changed, **common))


def _secret_numbers(_monkeypatch) -> None:
    sample = _load('secret_numbers')
    keywords = checks.secret_keywords(sample['fact'])
    assert any(checks.secret_contains(keyword, sample['draft']) for keyword in keywords)
    spans = [checks.locate_secret(keyword, sample['draft']) for keyword in keywords]
    assert any(span is not None for span in spans)


def _half_draft(monkeypatch) -> None:
    sample = _load('half_draft')
    book = SimpleNamespace(chapters={n: {'output_ordinal': n} for n in sample['planned']})
    completed = set(sample['completed'])
    store = SimpleNamespace(
        exists=lambda rel: int(Path(rel).stem.removeprefix('ch')) in completed)
    got = [row['output_ordinal'] for row in write._chapters_of(store, book, None)]
    assert got == sample['expected_resume']


def _rejected_archive(_monkeypatch) -> None:
    rel = 'stage5/ch01.rejected.json'
    decoded = decode_artifact(rel, _load('rejected_archive'))
    artifact = RejectedRevision.model_validate(decoded.payload)
    assert len(artifact.attempts) == 2
    archived = paths.round_rel(rel, 1)
    assert archived == 'stage5/_rounds/ch01.rejected.r1.json'
    spec = artifact_spec(rel)
    assert spec is not None
    assert decode_artifact(archived, _load('rejected_archive')).version == spec.current_version
    assert [attempt.segments[0].slot for attempt in artifact.attempts] == ['s01', 's01']


def _retired_rejected(_monkeypatch) -> None:
    # 原 v1 字节继续作为拒绝反例，不手补字段或改版本冒充新产物。
    for rel in ('stage5/ch01.rejected.json', 'stage5/_rounds/ch01.rejected.r1.json'):
        with pytest.raises(ArtifactVersionError):
            decode_artifact(rel, _load('retired_rejected'))


def _retry_branch(_monkeypatch) -> None:
    sample = _load('retry_branch')
    responses = sample['responses']

    def respond(_request, index):
        item = responses[index]
        if 'status' in item:
            return TransportError(
                str(item['status']), retryable=item['retryable'], status=item['status'])
        return ChatResponse(content=item['content'], finish_reason=item['finish_reason'])

    base = Path(tempfile.mkdtemp(prefix='fixture-retry-'))
    (base / '合成书_1' / 'stage1').mkdir(parents=True)
    ctx = RunContext.open(base, '1', 'stage6.hook', paid=True, write_ledger=False)
    sleeps: list[float] = []
    transport = FakeTransport(responder=respond)
    client = LlmClient(
        ctx, transport, sleep=sleeps.append, max_retries=2,
        backoff_base=sample['expected_backoff_seconds'][0], concurrency=1)
    assert client.ask_text(
        'SYS', 'USER', purpose='fixture-retry', rungs=(('low', False),)
    ) == '重试后完整正文'
    assert len(transport.calls) == sample['expected_attempts']
    assert sleeps == sample['expected_backoff_seconds']


HANDLERS = {
    'long_context': _long_context,
    'secret_numbers': _secret_numbers,
    'half_draft': _half_draft,
    'rejected_archive': _rejected_archive,
    'retired_rejected': _retired_rejected,
    'retry_branch': _retry_branch,
}


def test_fixture_manifest_has_no_dangling_files_or_consumers():
    assert MANIFEST['encoding'] == 'utf-8'
    assert set(ENTRIES) == set(HANDLERS), 'fixture 与消费测试必须双向一一对应'
    assert len(ENTRIES) == len(MANIFEST['fixtures']), 'fixture id 不能重复'
    for row in MANIFEST['fixtures']:
        assert row['reason'].strip(), row['id']
        path = ROOT / row['path']
        assert path.is_file(), path
        raw = path.read_bytes()
        assert raw.startswith((b'{', b'[')) and raw.endswith(b'\n'), path
        raw.decode(MANIFEST['encoding'])
        assert hashlib.sha256(raw).hexdigest() == row['sha256'], path


def test_every_byte_pinned_fixture_is_protected_from_git_line_endings():
    root = Path(__file__).resolve().parents[1]
    for row in MANIFEST['fixtures']:
        rel = (Path('tests/fixtures') / row['path']).as_posix()
        checked = subprocess.run(
            [
                'git', '-c', f'safe.directory={root.as_posix()}',
                'check-attr', 'text', '--', rel,
            ],
            cwd=root, check=True, capture_output=True, text=True, encoding='utf-8',
        )
        assert checked.stdout.strip() == f'{rel}: text: unset', (
            f'{rel} 的 sha256 被 manifest 钉住，却没有 .gitattributes -text 保护；'
            '全新 Windows checkout 会把 LF 改成 CRLF')


@pytest.mark.parametrize('fixture_id', sorted(HANDLERS))
def test_registered_fixture_exercises_its_branch(fixture_id, monkeypatch):
    HANDLERS[fixture_id](monkeypatch)
