#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Typed, hash-bound human closure for Stage5 trim verification."""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from contracts.artifacts import artifact_rel
from contracts.schema import artifact_spec, encode_artifact
from contracts.schema.evaluation import TrimAudit, TrimResolution
from contracts.schema.common import RoundFreeProvenance
from contracts.schema.stage5 import Chapter, ChapterSegment
from contracts.schema.stage1 import MainText
from novel import paths, prov
from novel.errors import ApprovalRequired
from novel import exit_codes as X
from novel.cli.main import main
from novel.orchestrator.state import readonly_ctx
from novel.run import RunContext
from novel.stages.stage5.trim_resolution import (
    current_trim_audits, current_unresolved_audits, retain_pretrim,
    verification_context,
)
from novel.store import ArtifactStore
from tests import factories
from novel.orchestrator.inbox_projection import inbox_view
from novel.views.stage5 import stage5_view


PRETRIM_TEXT = '原稿正文。'
PRETRIM_SHA256 = hashlib.sha256(PRETRIM_TEXT.encode('utf-8')).hexdigest()


def _book(tmp_path):
    base = tmp_path / 'Output'
    from novel.work_units import initialize_workflow
    initialize_workflow(base)
    book = base / paths.novel_dirname('收缩测试', '42')
    (book / 'stage0').mkdir(parents=True)
    (book / 'stage0' / 'clean_text.txt').write_text(
        '第一章\n原稿正文。\n', encoding='utf-8', newline='\n')
    main_rel = 'stage1/main_text.json'
    main = MainText.model_validate([
        {'chapter_ordinal': 1, 'text': '前章。'},
        {'chapter_ordinal': 2, 'text': '次章。'},
        {'chapter_ordinal': 3, 'text': PRETRIM_TEXT},
    ])
    (book / 'stage1').mkdir()
    (book / main_rel).write_text(json.dumps(encode_artifact(
        main_rel, main.model_dump(mode='json')),
        ensure_ascii=False), encoding='utf-8', newline='\n')
    ctx = readonly_ctx(book, 'stage5.review', prov.code_version())
    rel = artifact_rel('stage5.chapter', nn=3)
    chapter = Chapter(output_ordinal=3, segments=[ChapterSegment(slot='s01', text=PRETRIM_TEXT)], _provenance=ctx.free_provenance(RoundFreeProvenance, round=1, origin='human'))
    target = book / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(encode_artifact(
        rel, chapter.model_dump(by_alias=True, mode='json')),
        ensure_ascii=False), encoding='utf-8', newline='\n')
    return base, book


def _store(book, node='stage5.review'):
    return ArtifactStore(readonly_ctx(book, node, prov.code_version()))


def _audit(*, reason='超上限，本仓没有自动收缩路径', before=PRETRIM_SHA256):
    return TrimAudit(before_sha256=before, reason=reason)


def _writer_ctx(book):
    from novel.llm.cache import CachePolicy
    from novel.llm.ledger import CostLedger
    return RunContext(
        novel_dir=book, node='stage5.review', paid=False,
        code=prov.code_version(), cache=CachePolicy.default(), ledger=CostLedger())


def _write_audit(book, audit):
    ArtifactStore(_writer_ctx(book)).write(artifact_rel('stage5.trim_audit', nn=3), audit)


def _trim_rows(base):
    return [
        row for row in inbox_view(base, '42').items
        if row.kind == 'verification' and row.stage == 'stage5'
    ]


def test_trim_verification_context_carries_the_limit_and_the_audit_identity():
    from contracts.schema.stage4 import WriterContext

    text = '甲' * 20
    audit = _audit(before=hashlib.sha256(text.encode('utf-8')).hexdigest())
    provenance = {'code': 'test', 'root_clean_sha1': 'a' * 40, 'inputs': {}}
    chapter = Chapter.model_validate({
        'output_ordinal': 3, 'segments': [{'slot': 's01', 'text': text}],
        '_provenance': {**provenance, 'round': 1, 'origin': 'human'},
    })
    entry = factories.stage5_book().chapters[1]
    entry['output_ordinal'] = 3
    entry['scenes'][0]['budget_chars'] = 10
    context = WriterContext.model_validate({
        'chapters': [entry], '_provenance': provenance,
    })
    store = SimpleNamespace(
        read_own=lambda *_args, **_kwargs: (chapter, {}),
        read=lambda *_args, **_kwargs: context,
    )
    detail = verification_context(store, audit, ordinal=3)
    assert '当前 pretrim 20 字' in detail and '目标 ' in detail
    assert '超上限' in detail
    assert f'before={audit.before_sha256}' in detail
    changed = chapter.model_copy(update={
        'segments': [ChapterSegment(slot='s01', text=text + '乙')],
    })
    store.read_own = lambda *_args, **_kwargs: (changed, {})
    assert '绑定不一致' in verification_context(store, audit, ordinal=3)


def test_trim_resolution_schema_has_no_candidate_adoption_action(tmp_path):
    _base, book = _book(tmp_path)
    _write_audit(book, _audit())
    ctx = _writer_ctx(book)
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
        _writer_ctx(book),
        3, actor='甲', reason='核对后决定保留原稿')
    assert _trim_rows(str(base)) == []
    resolution_rel = artifact_rel('stage5.trim_resolution', nn=3)
    assert _store(book).freshness(resolution_rel).state == 'fresh'
    store = _store(book)
    assert set(current_trim_audits(store)) == {3}, (
        '有效 retain 决定关闭 verification，但 exact-current trim 根因必须继续抑制 length_over')
    assert current_unresolved_audits(store) == {}

    resolution_path = book / resolution_rel
    raw = json.loads(resolution_path.read_text(encoding='utf-8'))
    # ⚠ 版本号**从注册表现算**，不写字面量：这一条要查的是「人工写回落的是当前版本」，
    #   与那个数字具体是几无关。曾硬编码成 2，全表归零到 v1 时它就是一次假红。
    # ⚠ 走 `artifact_spec(rel)` 这一个落点，不手拼「id → 模板 → spec」两跳：那条路绕开了
    #   `artifact_key` 那套路由，注册表键的约定一变，它给的不是假红就是**静默指到另一份 spec**。
    assert raw['_schema_version'] == artifact_spec(resolution_rel).current_version
    assert raw['action'] == 'retain_pretrim'
    assert 'mode' not in raw
    assert raw['output_ordinal'] == 3
    assert not {'before_sha256', 'chapter_rel', 'chapter_sha256'} & raw.keys()
    assert len(raw['audit_sha256']) == 64
    assert 'model' not in raw['_provenance']

    # Even a valid audit rewrite with an unchanged before identity changes
    # the complete artifact digest and therefore reopens verification.
    _write_audit(book, audit.model_copy(update={'reason': '同一正文，审计说明已更新'}))
    assert [row.chapter for row in _trim_rows(str(base))] == [3]

    retain_pretrim(
        _writer_ctx(book),
        3, actor='乙', reason='按更新后的审计继续保留原稿')
    assert _trim_rows(str(base)) == []
    history = sorted((book / 'stage5' / '_rounds').glob('trim_resolution_ch03.r*.json'))
    assert len(history) == 1
    assert json.loads(history[0].read_text(encoding='utf-8'))['actor'] == '甲'

    (book / 'stage0' / 'clean_text.txt').write_text(
        '第一章\n根正文已经变化。\n', encoding='utf-8', newline='\n')
    assert _trim_rows(str(base)) == []
    assert current_trim_audits(_store(book)) == {}, (
        '根身份已变 ⇒ 旧 audit 连「exact-current 根因」都不再是，不只是离开 verification')


def test_stage5_view_exposes_and_closes_the_current_trim_decision_surface(tmp_path):
    base, book = _book(tmp_path)
    _write_audit(book, _audit(reason='超出目标上限，当前保留完整原稿'))

    before = stage5_view(str(base), '42', 3)
    assert before.trim_audit is not None
    assert before.trim_audit.output_ordinal == 3
    assert before.trim_audit.reason == '超出目标上限，当前保留完整原稿'
    assert 'before=' in before.trim_audit.context

    retain_pretrim(
        _writer_ctx(book),
        3, actor='reviewer', reason='逐段核对后接受完整原稿')
    assert stage5_view(str(base), '42', 3).trim_audit is None


def test_stage5_view_reports_an_old_trim_schema_as_legacy_incompatible(tmp_path):
    base, book = _book(tmp_path)
    _write_audit(book, _audit())
    audit_path = book / artifact_rel('stage5.trim_audit', nn=3)
    raw = json.loads(audit_path.read_text(encoding='utf-8'))
    raw['_schema_version'] = 1
    audit_path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')

    view = stage5_view(str(base), '42', 3)
    assert view.legacy_incompatible is True
    assert any('trim_audit_ch03.json:v1->v3' in path for path in view.legacy_paths)
    assert view.trim_audit is None


def test_formal_chapter_rewrite_retires_and_blocks_the_old_trim_resolution(tmp_path):
    base, book = _book(tmp_path)
    _write_audit(book, _audit())
    ctx = _writer_ctx(book)
    retain_pretrim(ctx, 3, actor='甲', reason='确认保留当前 pretrim')
    assert _trim_rows(str(base)) == []

    store = ArtifactStore(ctx)
    rel = artifact_rel('stage5.chapter', nn=3)
    chapter, _raw = store.read_own(Chapter, rel)
    changed = chapter.model_copy(update={
        'segments': [ChapterSegment(slot='s01', text='人工改过的另一版正文。')],
        'chars': len('人工改过的另一版正文。'),
    })
    # This test needs a changed on-disk subject, not a producer write.  Seed
    # the versioned fixture directly so it does not pretend stage5.review is
    # the chapter producer (the real post-write freshness guard rejects that).
    (book / rel).write_text(json.dumps(encode_artifact(
        rel, changed.model_dump(by_alias=True, mode='json')),
        ensure_ascii=False), encoding='utf-8', newline='\n')

    assert _trim_rows(str(base)) == [], '正式稿 hash 不再等于 audit.before，旧 audit 必须退出清单'
    with pytest.raises(ApprovalRequired, match='audit 之后变化'):
        retain_pretrim(ctx, 3, actor='乙', reason='不能沿用旧审计')


def test_revalidate_failure_restores_chapter_trim_and_all_history(tmp_path, monkeypatch):
    """Failure after promotion and one trim retirement leaves no half-commit."""
    from novel.stages.stage5 import write

    base, book = _book(tmp_path)
    _write_audit(book, _audit())
    retain_pretrim(
        _writer_ctx(book),
        3, actor='reviewer', reason='确认保留当前 pretrim',
    )
    ctx = RunContext.open(base, '42', 'stage5.write', paid=False)
    store = ArtifactStore(ctx)
    rel = artifact_rel('stage5.chapter', nn=3)
    chapter, _raw = store.read_own(Chapter, rel)
    store.write_invalid(
        rel, chapter,
        gates_skipped=[],
    )

    synthetic = factories.stage5_book()
    synthetic.ctx['chapters'][0]['output_ordinal'] = 3
    synthetic.source_text[3] = synthetic.source_text[1]
    monkeypatch.setattr(write.review, 'load_book', lambda *_args, **_kwargs: synthetic)
    monkeypatch.setattr(
        write, '_judge', lambda *_args, **_kwargs: ([], [], []))
    resolution_rel = artifact_rel('stage5.trim_resolution', nn=3)
    original_archive = ArtifactStore.archive_round

    def fail_resolution(self, target, **kwargs):
        if target == resolution_rel:
            raise OSError('模拟 trim resolution 退休失败')
        return original_archive(self, target, **kwargs)

    stage = book / 'stage5'
    before = {
        path.relative_to(stage).as_posix(): path.read_bytes()
        for path in stage.rglob('*') if path.is_file()
    }
    # This fixture intentionally has no real producer input ledger.  Freshness
    # postcondition behavior has dedicated store tests; this case must reach the
    # later trim-retirement failure to exercise the widened transaction.
    monkeypatch.setattr(ArtifactStore, '_assert_self_fresh', lambda *_args: None)
    monkeypatch.setattr(ArtifactStore, 'archive_round', fail_resolution)
    with pytest.raises(OSError, match='trim resolution 退休失败'):
        write.run_revalidate(ctx, actor='reviewer', reason='重判问题稿')

    after = {
        path.relative_to(stage).as_posix(): path.read_bytes()
        for path in stage.rglob('*') if path.is_file()
    }
    assert after == before


def test_cli_and_api_paths_are_zero_cost_and_only_retain_pretrim(tmp_path, monkeypatch):
    base, book = _book(tmp_path)
    _write_audit(book, _audit())
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    refused = main([
        'run', 'stage5.review', '42', '--retain-pretrim', '--chapters', '3',
        '--actor', '   ', '--reason', '不能留空白身份',
        '--base', str(base),
    ])
    assert refused == X.TERMINAL_STATE
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
