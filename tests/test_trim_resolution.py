#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Typed, hash-bound human closure for Stage5 trim verification."""
from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from contracts.artifacts import artifact_rel
from contracts.schema.evaluation import TrimAudit, TrimResolution
from contracts.schema.common import RoundFreeProvenance
from contracts.schema.stage5 import Chapter, ChapterSegment
from novel import paths, prov
from novel.errors import ApprovalRequired
from novel.cli import exit_codes as X
from novel.cli.main import main
from novel.orchestrator.state import readonly_ctx
from novel.stages.stage5.trim_resolution import retain_pretrim
from novel.store import ArtifactStore
from novel.views.inbox import inbox_view


PRETRIM_TEXT = '原稿正文。'
PRETRIM_SHA256 = hashlib.sha256(PRETRIM_TEXT.encode('utf-8')).hexdigest()


def _book(tmp_path):
    base = tmp_path / 'Output'
    book = base / paths.novel_dirname('收缩测试', '42')
    (book / 'stage0').mkdir(parents=True)
    (book / 'stage0' / 'clean_text.txt').write_text(
        '第一章\n原稿正文。\n', encoding='utf-8', newline='\n')
    ctx = readonly_ctx(book, 'stage5.review', prov.code_version())
    ArtifactStore(ctx).write(
        artifact_rel('stage5.chapter', mode='deep', nn=3),
        Chapter(
            output_ordinal=3, from_source_ordinals=[1], mode='deep',
            source_chars=len(PRETRIM_TEXT), budget_chars=len(PRETRIM_TEXT),
            chars=len(PRETRIM_TEXT), overlap_excl_verbatim=None,
            segments=[ChapterSegment(slot='s01', text=PRETRIM_TEXT)],
            warns=[], errs=None,
            _provenance=ctx.free_provenance(
                RoundFreeProvenance, round=1, origin='human'),
        ),
    )
    return base, book


def _store(book, node='stage5.review'):
    return ArtifactStore(readonly_ctx(book, node, prov.code_version()))


def _audit(*, reason='profile 未启用', before=PRETRIM_SHA256, after='b' * 64):
    return TrimAudit(
        output_ordinal=3,
        mode='deep',
        before_sha256=before,
        after_sha256=after,
        before_claims=[],
        after_claims=[],
        deterministic_passed=True,
        semantic=None,
        adopted=False,
        reason=reason,
    )


def _write_audit(book, audit):
    _store(book).write(artifact_rel('stage5.trim_audit', nn=3), audit)


def _trim_rows(base):
    return [
        row for row in inbox_view(base, '42').items
        if row.kind == 'verification' and row.stage == 'stage5'
    ]


def test_trim_resolution_schema_has_no_candidate_adoption_action(tmp_path):
    _base, book = _book(tmp_path)
    _write_audit(book, _audit())
    ctx = readonly_ctx(book, 'stage5.review', prov.code_version())
    resolution = retain_pretrim(ctx, 3, actor='reviewer', reason='保留原稿更安全')
    payload = resolution.model_dump(by_alias=True, mode='json')
    payload['action'] = 'adopt_candidate'
    with pytest.raises(ValidationError, match='retain_pretrim'):
        TrimResolution.model_validate(payload)


def test_matching_resolution_closes_inbox_but_any_audit_change_makes_it_stale(tmp_path):
    base, book = _book(tmp_path)
    audit = _audit()
    _write_audit(book, audit)
    assert [row.chapter for row in _trim_rows(str(base))] == [3]

    retain_pretrim(
        readonly_ctx(book, 'stage5.review', prov.code_version()),
        3, actor='甲', reason='核对后决定保留原稿')
    assert _trim_rows(str(base)) == []

    resolution_path = book / artifact_rel('stage5.trim_resolution', nn=3)
    raw = json.loads(resolution_path.read_text(encoding='utf-8'))
    assert raw['_schema_version'] == 1
    assert raw['action'] == 'retain_pretrim'
    assert raw['mode'] == 'deep'
    assert raw['chapter_rel'] == 'stage5/deep/ch03.json'
    assert raw['chapter_sha256'] == PRETRIM_SHA256
    assert len(raw['audit_sha256']) == 64
    assert 'model' not in raw['_provenance']

    # Even a valid audit rewrite with unchanged before/after identities changes
    # the complete artifact digest and therefore reopens verification.
    _write_audit(book, audit.model_copy(update={'reason': '同一正文，审计说明已更新'}))
    assert [row.chapter for row in _trim_rows(str(base))] == [3]

    retain_pretrim(
        readonly_ctx(book, 'stage5.review', prov.code_version()),
        3, actor='乙', reason='按更新后的审计继续保留原稿')
    assert _trim_rows(str(base)) == []
    history = sorted((book / 'stage5' / '_rounds').glob('trim_resolution_ch03.r*.json'))
    assert len(history) == 1
    assert json.loads(history[0].read_text(encoding='utf-8'))['actor'] == '甲'

    _write_audit(book, _audit(reason='候选正文变化', after='c' * 64))
    assert [row.chapter for row in _trim_rows(str(base))] == [3]

    retain_pretrim(
        readonly_ctx(book, 'stage5.review', prov.code_version()),
        3, actor='丙', reason='按新候选审计仍保留原稿')
    assert _trim_rows(str(base)) == []
    (book / 'stage0' / 'clean_text.txt').write_text(
        '第一章\n根正文已经变化。\n', encoding='utf-8', newline='\n')
    assert [row.chapter for row in _trim_rows(str(base))] == [3]


def test_formal_chapter_rewrite_reopens_and_blocks_the_old_trim_resolution(tmp_path):
    base, book = _book(tmp_path)
    _write_audit(book, _audit())
    ctx = readonly_ctx(book, 'stage5.review', prov.code_version())
    retain_pretrim(ctx, 3, actor='甲', reason='确认保留当前 pretrim')
    assert _trim_rows(str(base)) == []

    store = ArtifactStore(ctx)
    rel = artifact_rel('stage5.chapter', mode='deep', nn=3)
    chapter, _raw = store.read_own(Chapter, rel)
    changed = chapter.model_copy(update={
        'segments': [ChapterSegment(slot='s01', text='人工改过的另一版正文。')],
        'chars': len('人工改过的另一版正文。'),
    })
    store.write(rel, changed)

    assert [row.chapter for row in _trim_rows(str(base))] == [3]
    with pytest.raises(ApprovalRequired, match='audit 之后变化'):
        retain_pretrim(ctx, 3, actor='乙', reason='不能沿用旧审计')


def test_cli_and_api_paths_are_zero_cost_and_only_retain_pretrim(tmp_path, monkeypatch):
    base, book = _book(tmp_path)
    _write_audit(book, _audit())
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    rc = main([
        'run', 'stage5.review', '42', '--retain-pretrim', '--chapters', '3',
        '--actor', 'cli-reviewer', '--reason', 'CLI 核对后保留原稿',
        '--base', str(base),
    ])
    assert rc == X.OK
    assert _trim_rows(str(base)) == []

    # A new audit makes the CLI decision stale; the API must invoke the same
    # zero-cost CLI boundary rather than writing an artifact in the web process.
    _write_audit(book, _audit(reason='API 前审计更新'))
    fastapi = pytest.importorskip('fastapi')
    from fastapi.testclient import TestClient
    from novel.api.app import UI_HEADER, create_app

    with TestClient(create_app(base)) as client:
        response = client.post(
            '/api/novels/42/stage5/trim/3/retain-pretrim',
            headers={UI_HEADER: '1'},
            json={'actor': 'api-reviewer', 'reason': 'API 核对后保留原稿'},
        )
        assert response.status_code == 200, response.text
        assert response.json()['state'] == 'ok', response.text
        invalid = client.post(
            '/api/novels/42/stage5/trim/3/retain-pretrim',
            headers={UI_HEADER: '1'},
            json={'actor': '   ', 'reason': '不能是空白身份'},
        )
        assert invalid.status_code == 422
    assert _trim_rows(str(base)) == []
