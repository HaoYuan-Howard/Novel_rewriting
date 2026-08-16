#!/usr/bin/env python3
"""Repository-wide pytest acceptance options."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from contracts.schema import CorpusManifest
from tools.corpus_manifest import verify as verify_corpus_manifest


_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_MANIFEST = _ROOT / 'docs' / 'corpus' / 'current_schema_manifest.json'
_SEEN_SKIPS: list[dict[str, str]] = []
_REQUIRE = False


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup('current corpus')
    group.addoption(
        '--require-corpus', action='store_true', default=False,
        help=(
            'fail closed unless NOVEL_OUTPUT and the exact current-schema manifest '
            'are present; include both corpus and non-corpus tests'),
    )
    group.addoption(
        '--record-corpus-skips', metavar='PATH', default=None,
        help='write the exact observed skip list as JSON for manifest finalisation',
    )


def _manifest_path() -> Path:
    configured = os.environ.get('NOVEL_CORPUS_MANIFEST')
    return Path(configured).expanduser() if configured else _DEFAULT_MANIFEST


def pytest_configure(config: pytest.Config) -> None:
    global _REQUIRE
    _SEEN_SKIPS.clear()
    _REQUIRE = bool(config.getoption('--require-corpus'))
    if not _REQUIRE:
        return
    # `pyproject.toml` deliberately keeps ordinary local runs cheap.  This
    # explicit mode is the opposite contract: it must exercise both halves.
    config.option.markexpr = 'corpus or not corpus'
    configured = os.environ.get('NOVEL_OUTPUT')
    if not configured:
        raise pytest.UsageError('--require-corpus requires NOVEL_OUTPUT')
    base = Path(configured).expanduser()
    manifest_path = _manifest_path()
    if not manifest_path.is_file():
        raise pytest.UsageError(
            f'--require-corpus manifest missing: {manifest_path}')
    try:
        manifest = CorpusManifest.model_validate(
            json.loads(manifest_path.read_text(encoding='utf-8')))
        verify_corpus_manifest(base, manifest)
    except Exception as exc:
        raise pytest.UsageError(f'current corpus refused: {exc}') from exc
    setattr(config, '_current_corpus_manifest', manifest)


def _reason(longrepr: Any) -> str:
    raw = longrepr[2] if isinstance(longrepr, tuple) and len(longrepr) >= 3 else str(longrepr)
    raw = str(raw).strip()
    return raw.removeprefix('Skipped: ').strip()


def _remember_skip(report: pytest.TestReport | pytest.CollectReport) -> None:
    # pytest implements xfail as an internal skipped report, but exposes it as
    # a distinct terminal category.  The manifest's allowlist is for actual
    # skips only; otherwise adding/removing an xfail silently changes it.
    if report.skipped and not getattr(report, 'wasxfail', False):
        row = {'nodeid': report.nodeid, 'reason': _reason(report.longrepr)}
        if row not in _SEEN_SKIPS:
            _SEEN_SKIPS.append(row)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    _remember_skip(report)


def pytest_collectreport(report: pytest.CollectReport) -> None:
    _remember_skip(report)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    actual = sorted(_SEEN_SKIPS, key=lambda item: item['nodeid'])
    record = session.config.getoption('--record-corpus-skips')
    if record:
        output = Path(record)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(actual, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8', newline='\n')

    if not _REQUIRE:
        return
    manifest: CorpusManifest = getattr(session.config, '_current_corpus_manifest')
    expected = [item.model_dump(mode='json') for item in manifest.skip_allowlist]
    if actual == expected:
        return
    terminal = session.config.pluginmanager.get_plugin('terminalreporter')
    message = (
        'current corpus skip allowlist mismatch\n'
        f'expected={json.dumps(expected, ensure_ascii=False, sort_keys=True)}\n'
        f'actual={json.dumps(actual, ensure_ascii=False, sort_keys=True)}'
    )
    if terminal is not None:
        terminal.write_sep('=', message, red=True)
    session.exitstatus = pytest.ExitCode.TESTS_FAILED
