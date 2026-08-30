#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage5 trim 批量销账（P1-2）：整批预检、按章幂等、漂移即整批不开始。"""
from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path

import pytest

from contracts.schema.api import BatchTrimResolutionRequest
from contracts.schema.stage5 import Chapter
from novel.llm.cache import CachePolicy
from novel.llm.ledger import CostLedger
from novel.run import RunContext
from novel.stages.stage5 import trim_resolution
from novel.store import ArtifactStore

_GATES = 2


def _chapter(ordinal: int, text: str, root_fp: str, root_sha1: str) -> Chapter:
    return Chapter.model_validate({
        'output_ordinal': ordinal,
        'from_source_ordinals': [ordinal],
        'source_chars': 100, 'budget_chars': 90,
        'chars': len(text), 'overlap_excl_verbatim': 0.01,
        'segments': [{'slot': 's01', 'text': text}],
        'warns': [],
        '_provenance': {
            'code': 'test-trim-batch', 'root_clean_sha1': root_sha1,
            'inputs': {'stage0/clean_text.txt': {
                'file': root_fp, 'consumed': None}},
            'origin': 'human',
            'round': 1, 'prompts': {'SYS': 'a' * 10}, 'model': 'test-model',
            'adopted_models': ['test-model'],
        },
    })


@pytest.fixture
def rig(tmp_path):
    book_dir = tmp_path / '批量销账_001'
    (book_dir / 'stage5').mkdir(parents=True)
    (book_dir / 'stage0').mkdir(parents=True)
    (book_dir / 'stage0' / 'clean_text.txt').write_text(
        '固定测试根。\n', encoding='utf-8', newline='')
    from novel import prov
    root_fp = prov.fingerprint_bytes(
        (book_dir / 'stage0' / 'clean_text.txt').read_bytes())
    root_sha1 = prov.content_sha1(
        (book_dir / 'stage0' / 'clean_text.txt').read_bytes())
    ctx = RunContext(
        novel_dir=book_dir, node='stage5.review', paid=False,
        code='test-trim-batch',
        cache=CachePolicy.default(), ledger=CostLedger(),
        observer=lambda _event: None,
    )
    store = ArtifactStore(ctx)
    sha_by_ordinal: dict[int, str] = {}
    audit_sha: dict[int, str] = {}
    for ordinal, text in ((1, '第一章超限正文。' * 20), (2, '第二章超限正文。' * 20)):
        chapter = _chapter(ordinal, text, root_fp, root_sha1)
        raw = chapter.model_dump(by_alias=True, exclude_none=True)
        raw['_provenance'] = chapter.provenance.to_disk()
        from contracts.schema import encode_artifact
        rel = f'stage5/ch{ordinal:02d}.json'
        store.path(rel).write_bytes((
            json.dumps(encode_artifact(rel, raw), ensure_ascii=False, indent=2)
            + chr(10)).encode('utf-8'))
        sha_by_ordinal[ordinal] = hashlib.sha256(
            chapter.text.encode('utf-8')).hexdigest()
    for ordinal in sha_by_ordinal:
        from contracts.schema.evaluation import TrimAudit
        audit = TrimAudit(
            output_ordinal=ordinal,
            before_sha256=sha_by_ordinal[ordinal],
            reason='超上限，零调用落 identity audit')
        store.write(f'stage5/trim_audit_ch{ordinal:02d}.json', audit)
        audit_sha[ordinal] = store.read_with_sha256(
            TrimAudit, f'stage5/trim_audit_ch{ordinal:02d}.json')[1]
    return ctx, store, sha_by_ordinal, audit_sha


def _request(sha_by_ordinal, audit_sha, *, chapter_override=None):
    items = []
    for ordinal in sorted(sha_by_ordinal):
        items.append({
            'output_ordinal': ordinal,
            'audit_sha256': audit_sha[ordinal],
            'chapter_sha256': (chapter_override or {}).get(
                ordinal, sha_by_ordinal[ordinal]),
        })
    return BatchTrimResolutionRequest.model_validate({
        'actor': 'commander', 'reason': '两章一并确认保留 pretrim',
        'items': items,
    })


def test_batch_writes_every_resolution_and_is_idempotent(rig):
    ctx, store, sha_by_ordinal, audit_sha = rig
    request = _request(sha_by_ordinal, audit_sha)
    written = trim_resolution.retain_pretrim_batch(ctx, request)
    assert [row.output_ordinal for row in written] == [1, 2]
    for ordinal in (1, 2):
        rel = trim_resolution.resolution_rel(ordinal)
        assert store.exists(rel)
        resolution, _raw = store.read_own(
            __import__('contracts.schema.evaluation', fromlist=['TrimResolution']).TrimResolution,
            rel)
        assert resolution.actor == 'commander'
        assert resolution.reason == '两章一并确认保留 pretrim'
    # 重交同一 manifest：已销账章幂等跳过，不写新轮。
    rounds_before = {
        ordinal: store.next_round(trim_resolution.resolution_rel(ordinal))
        for ordinal in (1, 2)}
    again = trim_resolution.retain_pretrim_batch(ctx, request)
    assert [row.output_ordinal for row in again] == [1, 2]
    for ordinal in (1, 2):
        rel = trim_resolution.resolution_rel(ordinal)
        assert store.next_round(rel) == rounds_before[ordinal]  # 轮数不变


def test_any_drift_refuses_the_whole_batch(rig):
    ctx, store, sha_by_ordinal, audit_sha = rig
    request = _request(
        sha_by_ordinal, audit_sha,
        chapter_override={2: 'f' * 64})
    with pytest.raises(Exception, match='整批不开始'):
        trim_resolution.retain_pretrim_batch(ctx, request)
    for ordinal in (1, 2):
        assert not store.exists(trim_resolution.resolution_rel(ordinal)), (
            '整批预检失败后不得留下任何一章的 resolution')


def test_drift_between_preflight_and_first_write_leaves_zero_resolutions(
        rig, monkeypatch):
    """首轮采样与首写前复采之间漂移时，整批一个 resolution 都不能落。"""
    ctx, store, sha_by_ordinal, audit_sha = rig
    request = _request(sha_by_ordinal, audit_sha)
    original = trim_resolution._current_pretrim
    calls = 0

    def drift_on_second_collection(current_store, audit):
        nonlocal calls
        calls += 1
        current = original(current_store, audit)
        if calls == 3 and current is not None:
            return current[0], 'f' * 64
        return current

    monkeypatch.setattr(
        trim_resolution, '_current_pretrim', drift_on_second_collection)

    with pytest.raises(Exception, match='首写前复核失败.*整批不开始'):
        trim_resolution.retain_pretrim_batch(ctx, request)
    assert calls == 3
    assert all(
        not store.exists(trim_resolution.resolution_rel(ordinal))
        for ordinal in (1, 2)
    )


def test_single_item_override_reason(rig):
    ctx, store, sha_by_ordinal, audit_sha = rig
    from contracts.schema.api import BatchTrimResolutionRequest
    request = BatchTrimResolutionRequest.model_validate({
        'actor': 'commander', 'reason': '共享理由',
        'items': [
            {'output_ordinal': 1, 'audit_sha256': audit_sha[1],
             'chapter_sha256': sha_by_ordinal[1]},
            {'output_ordinal': 2, 'audit_sha256': audit_sha[2],
             'chapter_sha256': sha_by_ordinal[2],
             'reason': '第二章单独的理由'},
        ],
    })
    trim_resolution.retain_pretrim_batch(ctx, request)
    from contracts.schema.evaluation import TrimResolution
    first, _ = store.read_own(TrimResolution, 'stage5/trim_resolution_ch01.json')
    second, _ = store.read_own(TrimResolution, 'stage5/trim_resolution_ch02.json')
    assert first.reason == '共享理由'
    assert second.reason == '第二章单独的理由'


def test_cli_trim_batch_resolves_relative_to_book_and_keeps_absolute_path(
        rig, monkeypatch):
    """direct CLI 保持现役语义；Commander 的绝对路径边界另由 permit 收窄。"""
    ctx, _store, sha_by_ordinal, audit_sha = rig
    cli = importlib.import_module('novel.cli.main')
    relative_request = _request(sha_by_ordinal, audit_sha)
    relative = ctx.novel_dir / 'relative-trim-batch.json'
    relative.write_text(relative_request.model_dump_json(), encoding='utf-8')
    absolute_request = relative_request.model_copy(
        update={'reason': '绝对路径 manifest'})
    absolute = ctx.novel_dir.parent / 'absolute-trim-batch.json'
    absolute.write_text(absolute_request.model_dump_json(), encoding='utf-8')
    seen = []
    monkeypatch.setattr(
        trim_resolution, 'retain_pretrim_batch',
        lambda _ctx, request: seen.append(request.reason) or [])

    assert cli._run_stage5(
        ctx, ['--retain-pretrim-batch', relative.name]) == 0
    assert cli._run_stage5(
        ctx, ['--retain-pretrim-batch', str(absolute.resolve())]) == 0
    assert seen == ['两章一并确认保留 pretrim', '绝对路径 manifest']
