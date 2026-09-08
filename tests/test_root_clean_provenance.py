#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import Field

from contracts.schema import encode_artifact
from contracts.schema.base import StrictModel
from contracts.schema.common import FreeProvenance
from novel import prov
from novel.errors import ProvenanceRefused, UpstreamStale
from novel.llm.cache import CachePolicy
from novel.llm.client import LlmClient
from novel.llm.ledger import CostLedger
from novel.llm.transport import ChatResponse, FakeTransport
from novel.run import RunContext
from novel.store import ArtifactStore


class _Artifact(StrictModel):
    text: str
    provenance: FreeProvenance = Field(alias='_provenance')


def _ctx(novel_dir: Path, node: str = 'stage4.context') -> RunContext:
    return RunContext(
        novel_dir=novel_dir,
        node=node,
        paid=False,
        code='test-code',
        cache=CachePolicy.default(),
        ledger=CostLedger(path=None),
    )


def _book(tmp_path: Path) -> Path:
    from novel.work_units import initialize_workflow
    initialize_workflow(tmp_path)
    novel_dir = tmp_path / '根哈希_123'
    clean = novel_dir / 'stage0/clean_text.txt'
    clean.parent.mkdir(parents=True)
    clean.write_text('第一版正文\n', encoding='utf-8', newline='')
    return novel_dir


def _seed_historical_artifact(
    novel_dir: Path, rel: str, artifact: _Artifact,
) -> Path:
    """Seed a pre-postcondition artifact so stale-root behavior can be exercised."""
    target = novel_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = artifact.model_dump(mode='json', by_alias=True, exclude_none=True)
    target.write_text(
        json.dumps(encode_artifact(rel, payload), ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8', newline='',
    )
    return target


def test_new_provenance_always_carries_current_stage0_clean_hash(tmp_path):
    novel_dir = _book(tmp_path)
    ctx = _ctx(novel_dir)
    source = (novel_dir / 'stage0/clean_text.txt').read_bytes()

    minted = ctx.free_provenance()

    assert minted.root_clean_sha1 == prov.content_sha1(source)


def test_write_refuses_an_artifact_after_root_changes(tmp_path):
    novel_dir = _book(tmp_path)
    ctx = _ctx(novel_dir)
    artifact = _Artifact(text='旧根内容', _provenance=ctx.free_provenance())
    (novel_dir / 'stage0/clean_text.txt').write_text(
        '第二版正文\n', encoding='utf-8', newline='')

    with pytest.raises(ProvenanceRefused, match='当前 stage0/clean_text'):
        ArtifactStore(ctx).write('stage3/root_test.json', artifact)


def test_stale_upstream_is_refused_at_read_before_a_new_provenance_can_be_minted(tmp_path):
    novel_dir = _book(tmp_path)
    old_ctx = _ctx(novel_dir)
    old = _Artifact(text='旧根产物', _provenance=old_ctx.free_provenance())
    _seed_historical_artifact(novel_dir, 'stage2/up.json', old)
    (novel_dir / 'stage0/clean_text.txt').write_text(
        '第二版正文\n', encoding='utf-8', newline='')
    new_ctx = _ctx(novel_dir)
    with pytest.raises(UpstreamStale, match='生成或付费请求前拒绝消费'):
        ArtifactStore(new_ctx).read(_Artifact, 'stage2/up.json')


def test_readonly_view_can_still_load_a_stale_artifact_to_explain_it(tmp_path):
    novel_dir = _book(tmp_path)
    old_ctx = _ctx(novel_dir)
    _seed_historical_artifact(
        novel_dir,
        'stage2/up.json',
        _Artifact(text='旧根产物', _provenance=old_ctx.free_provenance()),
    )
    (novel_dir / 'stage0/clean_text.txt').write_text(
        '第二版正文\n', encoding='utf-8', newline='')
    view_ctx = RunContext(
        novel_dir=novel_dir,
        node='stage4.context',
        paid=False,
        code='test-code',
        cache=CachePolicy.default(),
        ledger=CostLedger(path=None),
        readonly=True,
    )

    artifact = ArtifactStore(view_ctx).read(_Artifact, 'stage2/up.json')

    assert artifact.text == '旧根产物'
    assert ArtifactStore(view_ctx).freshness('stage2/up.json').state == 'stale'


def test_llm_preflight_rechecks_roots_even_for_an_explicit_read_own(tmp_path):
    novel_dir = _book(tmp_path)
    old_ctx = _ctx(novel_dir)
    _seed_historical_artifact(
        novel_dir,
        'stage2/up.json',
        _Artifact(text='旧根产物', _provenance=old_ctx.free_provenance()),
    )
    (novel_dir / 'stage0/clean_text.txt').write_text(
        '第二版正文\n', encoding='utf-8', newline='')
    ctx = _ctx(novel_dir)
    ArtifactStore(ctx).read_own(_Artifact, 'stage2/up.json')
    transport = FakeTransport.always(ChatResponse(
        content='不应触达', finish_reason='stop',
        usage={'prompt_tokens': 1, 'completion_tokens': 1}, model='test-model'))
    client = LlmClient(
        ctx, transport, model='test-model',
        budget_checker=lambda *_args, **_kwargs: pytest.fail('不应进入预算闸'))

    with pytest.raises(ProvenanceRefused, match='不同 root_clean_sha1'):
        client.ask_text('system', 'user', purpose='root-preflight')

    assert transport.calls == []


def test_freshness_turns_stale_immediately_when_stage0_clean_changes(tmp_path):
    novel_dir = _book(tmp_path)
    ctx = _ctx(novel_dir)
    _seed_historical_artifact(
        novel_dir,
        'stage3/root_test.json',
        _Artifact(text='旧根产物', _provenance=ctx.free_provenance()),
    )
    (novel_dir / 'stage0/clean_text.txt').write_text(
        '第二版正文\n', encoding='utf-8', newline='')

    report = ArtifactStore(_ctx(novel_dir)).freshness('stage3/root_test.json')

    assert report.state == 'stale'
    assert report.edges[0].key == 'stage0/clean_text.txt'
