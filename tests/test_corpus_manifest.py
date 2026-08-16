from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from contracts.schema import (
    CorpusArtifactEntry, CorpusLedgerSummary, CorpusManifest,
    CorpusSkipExpectation, LedgerTokenSummary, encode_artifact,
)
from contracts.schema.stage1 import MainText
from tools import corpus_manifest


def test_documented_script_entrypoint_bootstraps_repo_imports(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, '-X', 'utf8', str(root / 'tools' / 'corpus_manifest.py'), '--help'],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding='utf-8',
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert 'Build or verify the exact, text-free current-schema corpus manifest.' in result.stdout


def _ledger_row(*, cached: bool, prompt, hit, miss, completion, reasoning):
    return {
        'sid': '123', 'cached': cached, 'outcome': 'ok', 'model': 'model-v1',
        'reasoning_effort': 'high', 'prompt_tokens': prompt,
        'prompt_cache_hit_tokens': hit, 'prompt_cache_miss_tokens': miss,
        'completion_tokens': completion, 'reasoning_tokens': reasoning,
    }


def test_ledger_summary_preserves_zero_vs_null_and_closes_provider_cache():
    rows = [
        _ledger_row(
            cached=False, prompt=10, hit=0, miss=10, completion=5, reasoning=0),
        _ledger_row(
            cached=True, prompt=None, hit=None, miss=None, completion=None, reasoning=None),
    ]
    got = corpus_manifest._ledger_summary(rows, '123')
    assert (got.rows, got.wire_attempts, got.local_cache_hits) == (2, 1, 1)
    assert got.prompt_cache_hit_tokens.model_dump() == {
        'total': 0, 'reported_rows': 1, 'null_rows': 1}
    assert got.reasoning_tokens.model_dump() == {
        'total': 0, 'reported_rows': 1, 'null_rows': 1}

    rows[0]['prompt_cache_miss_tokens'] = 9
    with pytest.raises(ValueError, match=r'hit\(0\) \+ miss\(9\) != prompt\(10\)'):
        corpus_manifest._ledger_summary(rows, '123')

    rows[0]['prompt_cache_miss_tokens'] = '10'
    with pytest.raises(ValueError, match='非非负整数/null'):
        corpus_manifest._ledger_summary(rows, '123')


def test_ledger_manifest_partitions_cannot_claim_impossible_counts():
    token = LedgerTokenSummary(total=0, reported_rows=0, null_rows=1)
    with pytest.raises(ValidationError, match='outcomes 计数之和'):
        CorpusLedgerSummary(
            rows=1, wire_attempts=1, local_cache_hits=0,
            outcomes={}, models={'m': 1}, reasoning_efforts={'high': 1},
            **{key: token for key in corpus_manifest._TOKEN_COLUMNS})
    with pytest.raises(ValidationError, match='token total'):
        LedgerTokenSummary(total=1, reported_rows=0, null_rows=1)


def test_manifest_artifact_versions_and_stable_order_are_strict():
    with pytest.raises(ValidationError, match='JSON 产物必须钉'):
        CorpusArtifactEntry(rel='stage1/main_text.json', schema_version=None)
    with pytest.raises(ValidationError, match='字节产物必须'):
        CorpusArtifactEntry(rel='stage7/book.txt', schema_version=2)

    raw = {
        'manifest_version': 1,
        'generated_at': '2026-08-16T00:00:00Z',
        'code_identity': 'code',
        'artifact_inventory_sha256': 'a' * 64,
        'commands': ['rebuild'],
        'books': [{
            'sid': '123', 'source_sha1': 'b' * 40, 'chapters': 1,
            'artifacts': [
                {'rel': 'stage1/main_text.json', 'schema_version': 2},
                {'rel': 'stage0/removal_report.json', 'schema_version': 2},
            ],
            'ledger': {
                'rows': 0, 'wire_attempts': 0, 'local_cache_hits': 0,
                'outcomes': {}, 'models': {}, 'reasoning_efforts': {},
                **{key: {'total': 0, 'reported_rows': 0, 'null_rows': 0}
                   for key in corpus_manifest._TOKEN_COLUMNS},
            },
        }],
        'skip_allowlist': [],
    }
    with pytest.raises(ValidationError, match='artifacts 必须按 rel 排序'):
        CorpusManifest.model_validate(raw)


def test_observe_is_text_free_and_verification_detects_any_inventory_or_ledger_drift(
        tmp_path, monkeypatch):
    book_dir = tmp_path / 'book_123'
    book_dir.mkdir()
    artifacts = [
        CorpusArtifactEntry(rel='stage0/removal_report.json', schema_version=2),
        CorpusArtifactEntry(rel='stage1/main_text.json', schema_version=2),
        CorpusArtifactEntry(rel='stage7/book.txt', schema_version=None),
    ]
    monkeypatch.setattr(corpus_manifest, '_book_dirs', lambda _base: [book_dir])
    state = {
        'artifacts': artifacts,
        'payloads': {
            'stage0/removal_report.json': {'source_sha1': 'b' * 40},
            'stage1/main_text.json': [{'chapter_ordinal': 1}],
        },
        'codes': {'frozen-code'},
    }
    monkeypatch.setattr(
        corpus_manifest, '_artifact_inventory',
        lambda _book: (
            state['artifacts'], state['payloads'], state['codes']),
    )
    rows = [_ledger_row(
        cached=False, prompt=10, hit=0, miss=10, completion=5, reasoning=0)]
    monkeypatch.setattr(corpus_manifest, '_ledger_rows', lambda _base: rows)

    manifest = corpus_manifest.build_manifest(
        tmp_path, commands=['python -m novel.cli ...'],
        skips=[CorpusSkipExpectation(nodeid='tests/test_x.py::test_y', reason='declared')])
    corpus_manifest.verify(tmp_path, manifest)
    encoded = json.dumps(manifest.model_dump(mode='json'), ensure_ascii=False)
    assert 'chapter_ordinal' not in encoded
    assert 'book_123' not in encoded
    assert 'frozen-code' in encoded and 'bbbbbbbb' in encoded

    rows.append(_ledger_row(
        cached=True, prompt=None, hit=None, miss=None, completion=None, reasoning=None))
    with pytest.raises(ValueError, match='精确内容不一致'):
        corpus_manifest.verify(tmp_path, manifest)
    rows.pop()

    state['artifacts'] = artifacts[:-1]  # byte artifact reduction
    with pytest.raises(ValueError, match='inventory hash 漂移'):
        corpus_manifest.verify(tmp_path, manifest)
    state['artifacts'] = artifacts

    state['artifacts'] = [
        item.model_copy(update={'schema_version': 1})
        if item.rel == 'stage1/main_text.json' else item
        for item in artifacts
    ]
    with pytest.raises(ValueError, match='inventory hash 漂移'):
        corpus_manifest.verify(tmp_path, manifest)
    state['artifacts'] = artifacts

    state['payloads']['stage0/removal_report.json']['source_sha1'] = 'c' * 40
    with pytest.raises(ValueError, match='inventory hash 漂移'):
        corpus_manifest.verify(tmp_path, manifest)
    state['payloads']['stage0/removal_report.json']['source_sha1'] = 'b' * 40

    state['payloads']['stage1/main_text.json'].append({'chapter_ordinal': 2})
    with pytest.raises(ValueError, match='inventory hash 漂移'):
        corpus_manifest.verify(tmp_path, manifest)
    state['payloads']['stage1/main_text.json'].pop()

    state['codes'] = {'other-code'}
    with pytest.raises(ValueError, match='code identity 漂移'):
        corpus_manifest.verify(tmp_path, manifest)

    monkeypatch.setattr(corpus_manifest, '_book_dirs', lambda _base: [])
    with pytest.raises(ValueError, match='没有书目录'):
        corpus_manifest.verify(tmp_path, manifest)


def test_root_model_artifact_version_is_decoded_from_envelope(tmp_path):
    book = tmp_path / 'book_123'
    target = book / 'stage1' / 'main_text.json'
    target.parent.mkdir(parents=True)
    main = MainText.model_validate([{
        'section_id': 'sec_0001', 'chapter_ordinal': 1,
        'source_chapter_label': '第一章', 'text': '正文。',
    }, {
        'section_id': 'sec_0002', 'chapter_ordinal': 2,
        'source_chapter_label': '第二章', 'text': '续文。',
    }])
    target.write_text(json.dumps(encode_artifact(
        'stage1/main_text.json', main.model_dump(mode='json'))), encoding='utf-8')
    entries, payloads, _codes = corpus_manifest._artifact_inventory(book)
    assert entries == [CorpusArtifactEntry(
        rel='stage1/main_text.json', schema_version=1)]
    assert isinstance(payloads['stage1/main_text.json'], list)


def test_require_corpus_overrides_default_marker_and_fails_closed(
        tmp_path, monkeypatch):
    from tests import conftest as hooks
    monkeypatch.setattr(hooks, '_REQUIRE', False)

    class Config:
        option = SimpleNamespace(markexpr='not corpus')

        @staticmethod
        def getoption(name):
            return name == '--require-corpus'

    config = Config()
    monkeypatch.delenv('NOVEL_OUTPUT', raising=False)
    with pytest.raises(pytest.UsageError, match='requires NOVEL_OUTPUT'):
        hooks.pytest_configure(config)

    manifest_path = tmp_path / 'manifest.json'
    monkeypatch.setenv('NOVEL_OUTPUT', str(tmp_path / 'corpus'))
    monkeypatch.setenv('NOVEL_CORPUS_MANIFEST', str(manifest_path))
    with pytest.raises(pytest.UsageError, match='manifest missing'):
        hooks.pytest_configure(config)

    manifest_path.write_text('{}', encoding='utf-8')
    sentinel = SimpleNamespace(skip_allowlist=[])
    monkeypatch.setattr(hooks.CorpusManifest, 'model_validate', lambda _raw: sentinel)
    monkeypatch.setattr(hooks, 'verify_corpus_manifest', lambda _base, _manifest: None)
    hooks.pytest_configure(config)
    assert config.option.markexpr == 'corpus or not corpus'
    assert getattr(config, '_current_corpus_manifest') is sentinel


def test_skip_recorder_excludes_xfail_and_keeps_exact_reason():
    from tests import conftest as hooks

    hooks._SEEN_SKIPS.clear()
    hooks._remember_skip(SimpleNamespace(
        skipped=True, wasxfail='known issue', nodeid='tests/test_x.py::test_x',
        longrepr=('x.py', 1, 'Skipped: xfail')))
    assert hooks._SEEN_SKIPS == []
    hooks._remember_skip(SimpleNamespace(
        skipped=True, nodeid='tests/test_x.py::test_y',
        longrepr=('x.py', 2, 'Skipped: exact reason')))
    assert hooks._SEEN_SKIPS == [{
        'nodeid': 'tests/test_x.py::test_y', 'reason': 'exact reason'}]


def test_require_corpus_skip_allowlist_is_exact(monkeypatch):
    from tests import conftest as hooks

    expected = CorpusSkipExpectation(
        nodeid='tests/test_x.py::test_y', reason='declared')

    class Plugins:
        @staticmethod
        def get_plugin(_name):
            return None

    class Config:
        _current_corpus_manifest = SimpleNamespace(skip_allowlist=[expected])
        pluginmanager = Plugins()

        @staticmethod
        def getoption(_name):
            return None

    monkeypatch.setattr(hooks, '_REQUIRE', True)
    hooks._SEEN_SKIPS[:] = [expected.model_dump(mode='json')]
    session = SimpleNamespace(config=Config(), exitstatus=pytest.ExitCode.OK)
    hooks.pytest_sessionfinish(session, int(pytest.ExitCode.OK))
    assert session.exitstatus == pytest.ExitCode.OK

    hooks._SEEN_SKIPS.append({
        'nodeid': 'tests/test_z.py::test_new', 'reason': 'new skip'})
    hooks.pytest_sessionfinish(session, int(pytest.ExitCode.OK))
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
