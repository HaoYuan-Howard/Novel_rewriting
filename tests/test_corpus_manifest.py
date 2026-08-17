from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from contracts.schema import (
    CorpusArtifactEntry, CorpusLedgerSummary, CorpusManifest,
    CorpusSkipExpectation, LedgerTokenSummary, artifact_spec, encode_artifact,
)
from contracts.schema.corpus import (
    CURRENT_CORPUS_BOOKS, CURRENT_CORPUS_PAID_NODES,
    required_current_corpus_artifacts,
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


def _ledger_row(
    *, cached: bool, prompt, hit, miss, completion, reasoning,
    sid: str = '123', node: str = 'stage2.global',
):
    return {
        'sid': sid, 'node': node, 'cached': cached, 'outcome': 'ok', 'model': 'model-v1',
        'code': 'frozen-code',
        'reasoning_effort': 'high', 'prompt_tokens': prompt,
        'prompt_cache_hit_tokens': hit, 'prompt_cache_miss_tokens': miss,
        'completion_tokens': completion, 'reasoning_tokens': reasoning,
    }


def _commands() -> list[str]:
    commands = []
    for sid, _chapters in CURRENT_CORPUS_BOOKS:
        for node in CURRENT_CORPUS_PAID_NODES:
            flags = ''
            if node == 'stage5.write':
                flags = ' --deep 1,2,3 --shallow-rest'
            elif node in {'stage5.semantic', 'stage6.semantic'}:
                flags = ' --advisory'
            commands.append(
                f'python -X utf8 -u -m novel.cli run {node} {sid}'
                f'{flags} --base E:/acceptance --yes')
    return commands


def _artifact(rel: str, *, digest: str | None = None) -> CorpusArtifactEntry:
    spec = artifact_spec(rel)
    return CorpusArtifactEntry(
        rel=rel,
        schema_version=spec.current_version if spec is not None else None,
        sha256=digest or hashlib.sha256(rel.encode('utf-8')).hexdigest(),
    )


def _artifacts(chapters: int) -> list[CorpusArtifactEntry]:
    return sorted(
        (_artifact(rel) for rel in required_current_corpus_artifacts(chapters)),
        key=lambda item: item.rel,
    )


def _rows() -> list[dict]:
    return [
        _ledger_row(
            sid=sid, node=node, cached=False,
            prompt=10, hit=0, miss=10, completion=5, reasoning=0,
        )
        for sid, _chapters in CURRENT_CORPUS_BOOKS
        for node in CURRENT_CORPUS_PAID_NODES
    ]


def _book_raw(sid: str, chapters: int) -> dict:
    return {
        'sid': sid,
        'source_sha1': 'b' * 40,
        'chapters': chapters,
        'artifacts': [item.model_dump(mode='json') for item in _artifacts(chapters)],
        'ledger': corpus_manifest._ledger_summary(_rows(), sid).model_dump(mode='json'),
    }


def _manifest_raw() -> dict:
    books = sorted(
        (_book_raw(sid, chapters) for sid, chapters in CURRENT_CORPUS_BOOKS),
        key=lambda item: item['sid'],
    )
    inventory = [{
        'sid': book['sid'],
        'source_sha1': book['source_sha1'],
        'chapters': book['chapters'],
        'artifacts': book['artifacts'],
    } for book in books]
    return {
        'manifest_version': 1,
        'generated_at': '2026-08-16T00:00:00Z',
        'code_identity': 'code',
        'artifact_inventory_sha256': corpus_manifest._canonical_sha256(inventory),
        'commands': _commands(),
        'books': books,
        'skip_allowlist': [],
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
            nodes={'stage2.global': 1}, outcomes={}, models={'m': 1},
            reasoning_efforts={'high': 1},
            **{key: token for key in corpus_manifest._TOKEN_COLUMNS})
    with pytest.raises(ValidationError, match='token total'):
        LedgerTokenSummary(total=1, reported_rows=0, null_rows=1)


def test_manifest_refuses_any_local_response_cache_hit():
    raw = _manifest_raw()
    ledger = raw['books'][0]['ledger']
    ledger['wire_attempts'] -= 1
    ledger['local_cache_hits'] += 1

    with pytest.raises(ValidationError, match='独立空本地缓存'):
        CorpusManifest.model_validate(raw)


def test_manifest_artifact_versions_and_stable_order_are_strict():
    with pytest.raises(ValidationError, match='JSON 产物必须钉'):
        CorpusArtifactEntry(
            rel='stage1/main_text.json', schema_version=None, sha256='a' * 64)
    with pytest.raises(ValidationError, match='字节产物必须'):
        CorpusArtifactEntry(
            rel='stage7/book.txt', schema_version=2, sha256='a' * 64)

    raw = _manifest_raw()
    raw['books'][0]['artifacts'] = list(reversed(raw['books'][0]['artifacts']))
    with pytest.raises(ValidationError, match='artifacts 必须按 rel 排序'):
        CorpusManifest.model_validate(raw)


def test_manifest_pins_the_real_run_dispatch_topology_and_required_flags():
    raw = _manifest_raw()
    CorpusManifest.model_validate(raw)

    raw['commands'][0] = raw['commands'][0].replace('novel.cli run', 'novel.cli preview')
    with pytest.raises(ValidationError, match='novel.cli run'):
        CorpusManifest.model_validate(raw)

    raw = _manifest_raw()
    raw['commands'][0] = raw['commands'][0].replace('novel.cli run ', 'novel.cli ')
    with pytest.raises(ValidationError, match='novel.cli run'):
        CorpusManifest.model_validate(raw)

    raw = _manifest_raw()
    index = next(i for i, command in enumerate(raw['commands'])
                 if 'stage5.semantic' in command)
    raw['commands'][index] = raw['commands'][index].replace(' --advisory', '')
    with pytest.raises(ValidationError, match='advisory'):
        CorpusManifest.model_validate(raw)

    raw = _manifest_raw()
    index = next(i for i, command in enumerate(raw['commands'])
                 if 'stage5.write' in command)
    raw['commands'][index] = raw['commands'][index].replace('1,2,3', '1,2')
    with pytest.raises(ValidationError, match='deep 1,2,3'):
        CorpusManifest.model_validate(raw)


def test_manifest_accepts_commands_from_the_production_argv_builder(tmp_path):
    from novel.orchestrator.nodes import build_argv

    commands = []
    for sid, _chapters in CURRENT_CORPUS_BOOKS:
        for node in CURRENT_CORPUS_PAID_NODES:
            flags: tuple[str, ...] = ()
            if node == 'stage5.write':
                flags = ('--deep', '1,2,3', '--shallow-rest')
            elif node in {'stage5.semantic', 'stage6.semantic'}:
                flags = ('--advisory',)
            commands.append(subprocess.list2cmdline(build_argv(
                node, sid, flags, base=tmp_path)))
    raw = _manifest_raw()
    raw['commands'] = commands
    got = CorpusManifest.model_validate(raw)
    assert got.commands == commands


def test_manifest_rejects_incomplete_books_ledger_and_artifact_hashes():
    raw = _manifest_raw()
    raw['books'][0]['artifacts'] = [
        item for item in raw['books'][0]['artifacts']
        if item['rel'] != 'stage7/book.txt'
    ]
    with pytest.raises(ValidationError, match='required artifacts'):
        CorpusManifest.model_validate(raw)

    raw = _manifest_raw()
    raw['books'][0]['ledger']['nodes'].pop('stage7.rename')
    with pytest.raises(ValidationError, match='nodes'):
        CorpusManifest.model_validate(raw)

    raw = _manifest_raw()
    raw['books'][0]['artifacts'][0].pop('sha256')
    with pytest.raises(ValidationError, match='sha256'):
        CorpusManifest.model_validate(raw)

    raw = _manifest_raw()
    raw['books'][0]['chapters'] = 24
    with pytest.raises(ValidationError, match='17/25'):
        CorpusManifest.model_validate(raw)


@pytest.mark.parametrize('reason', [
    'missing corpus',
    '未设置包含当前产物的 NOVEL_OUTPUT',
    '0 tests collected',
    '缺少现役双书 Test_data 语料',
    '盘上没有 stage7 语料——这些集成用例没在查',
    '语料缺 current v3 artifact；补法：重跑',
    '未设置 NOVEL_OUTPUT；当前产物语料体检未运行',
])
def test_skip_allowlist_cannot_hide_missing_corpus_or_zero_collection(reason):
    with pytest.raises(ValidationError, match='missing corpus / 0 collected'):
        CorpusSkipExpectation(nodeid='tests/test_x.py::test_x', reason=reason)


def test_observe_is_text_free_and_verification_detects_any_inventory_or_ledger_drift(
        tmp_path, monkeypatch):
    book_dirs = {
        sid: tmp_path / f'book_{sid}' for sid, _chapters in CURRENT_CORPUS_BOOKS
    }
    for book_dir in book_dirs.values():
        book_dir.mkdir()
    monkeypatch.setattr(
        corpus_manifest, '_book_dirs',
        lambda _base: [book_dirs[sid] for sid, _chapters in CURRENT_CORPUS_BOOKS],
    )
    state = {
        sid: {
            'artifacts': _artifacts(chapters),
            'payloads': {
                'stage0/removal_report.json': {'source_sha1': 'b' * 40},
                'stage1/main_text.json': [
                    {'chapter_ordinal': ordinal}
                    for ordinal in range(1, chapters + 1)
                ],
            },
            'codes': {'frozen-code'},
        }
        for sid, chapters in CURRENT_CORPUS_BOOKS
    }
    monkeypatch.setattr(
        corpus_manifest, '_artifact_inventory',
        lambda book: (
            state[book.name.rsplit('_', 1)[-1]]['artifacts'],
            state[book.name.rsplit('_', 1)[-1]]['payloads'],
            state[book.name.rsplit('_', 1)[-1]]['codes'],
        ),
    )
    rows = _rows()
    monkeypatch.setattr(corpus_manifest, '_ledger_rows', lambda _base: rows)

    manifest = corpus_manifest.build_manifest(
        tmp_path, commands=list(reversed(_commands())),
        skips=[CorpusSkipExpectation(nodeid='tests/test_x.py::test_y', reason='declared')])
    corpus_manifest.verify(tmp_path, manifest)
    encoded = json.dumps(manifest.model_dump(mode='json'), ensure_ascii=False)
    assert 'chapter_ordinal' not in encoded
    assert 'book_2019402867061048656' not in encoded
    assert 'frozen-code' in encoded and 'bbbbbbbb' in encoded
    assert [
            (command.split('novel.cli run ', 1)[1].split()[1],
             command.split('novel.cli run ', 1)[1].split()[0])
        for command in manifest.commands
    ] == [
        (sid, node)
        for sid, _chapters in CURRENT_CORPUS_BOOKS
        for node in CURRENT_CORPUS_PAID_NODES
    ]

    rows.append(_ledger_row(
        sid=CURRENT_CORPUS_BOOKS[0][0], node='stage2.global',
        cached=True, prompt=None, hit=None, miss=None, completion=None, reasoning=None))
    with pytest.raises(ValueError, match='独立空本地缓存'):
        corpus_manifest.verify(tmp_path, manifest)
    rows.pop()

    sid = CURRENT_CORPUS_BOOKS[0][0]
    artifacts = state[sid]['artifacts']
    state[sid]['artifacts'] = [
        item for item in artifacts if item.rel != 'stage7/book.txt'
    ]
    with pytest.raises(ValidationError, match='required artifacts'):
        corpus_manifest.verify(tmp_path, manifest)
    state[sid]['artifacts'] = artifacts

    state[sid]['artifacts'] = [
        item.model_copy(update={'schema_version': 1})
        if item.rel == 'stage0/removal_report.json' else item
        for item in artifacts
    ]
    with pytest.raises(ValueError, match='inventory hash 漂移'):
        corpus_manifest.verify(tmp_path, manifest)
    state[sid]['artifacts'] = artifacts

    state[sid]['artifacts'] = [
        item.model_copy(update={'sha256': 'f' * 64})
        if item.rel == 'stage7/book.txt' else item
        for item in artifacts
    ]
    with pytest.raises(ValueError, match='inventory hash 漂移'):
        corpus_manifest.verify(tmp_path, manifest)
    state[sid]['artifacts'] = artifacts

    state[sid]['payloads']['stage0/removal_report.json']['source_sha1'] = 'c' * 40
    with pytest.raises(ValueError, match='inventory hash 漂移'):
        corpus_manifest.verify(tmp_path, manifest)
    state[sid]['payloads']['stage0/removal_report.json']['source_sha1'] = 'b' * 40

    state[sid]['payloads']['stage1/main_text.json'].append({'chapter_ordinal': 18})
    with pytest.raises(ValidationError, match='17/25'):
        corpus_manifest.verify(tmp_path, manifest)
    state[sid]['payloads']['stage1/main_text.json'].pop()

    for one in state.values():
        one['codes'] = {'other-code'}
    with pytest.raises(ValueError, match='code identity'):
        corpus_manifest.verify(tmp_path, manifest)
    for one in state.values():
        one['codes'] = {'frozen-code'}

    rows.append(_ledger_row(
        sid='999', node='stage2.global', cached=False,
        prompt=1, hit=0, miss=1, completion=1, reasoning=0))
    with pytest.raises(ValueError, match='ledger SID'):
        corpus_manifest.verify(tmp_path, manifest)
    rows.pop()

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
        rel='stage1/main_text.json', schema_version=1,
        sha256=hashlib.sha256(target.read_bytes()).hexdigest())]
    assert isinstance(payloads['stage1/main_text.json'], list)

    first_sha = entries[0].sha256
    target.write_text(json.dumps(
        encode_artifact('stage1/main_text.json', main.model_dump(mode='json')),
        ensure_ascii=False, indent=2,
    ), encoding='utf-8')
    changed, _payloads, _codes = corpus_manifest._artifact_inventory(book)
    assert changed[0].sha256 == hashlib.sha256(target.read_bytes()).hexdigest()
    assert changed[0].sha256 != first_sha


def test_inventory_refuses_raw_old_version_even_when_registry_has_migration(tmp_path):
    from tests.test_stage2 import global_grounding

    book = tmp_path / 'book_123'
    target = book / 'stage2' / 'global_grounding.json'
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({
        '_schema_version': 2,
        **global_grounding(),
    }, ensure_ascii=False), encoding='utf-8')

    with pytest.raises(ValueError, match=r'raw _schema_version=2.*current v3.*禁止迁移'):
        corpus_manifest._artifact_inventory(book)


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


def test_windows_basetemp_preflight_rejects_deep_paths_before_stage0_runs():
    from tests import conftest as hooks

    assert hooks._windows_basetemp_problem('.test_runtime', os_name='nt') is None
    assert hooks._windows_basetemp_problem('x' * 400, os_name='posix') is None
    assert tuple(hooks._STAGE0_TMP_SUFFIX.parts[-3:-1]) == ('stage0', '_rounds')
    assert 'Output' in hooks._STAGE0_TMP_SUFFIX.parts

    # Pin the actual 259/260 boundary instead of testing only an obviously
    # enormous path that would not catch an underestimated relative suffix.
    first_refused = next(
        size for size in range(1, 500)
        if hooks._windows_basetemp_problem('x' * size, os_name='nt') is not None
    )
    assert hooks._windows_basetemp_problem(
        'x' * (first_refused - 1), os_name='nt') is None
    problem = hooks._windows_basetemp_problem('x' * first_refused, os_name='nt')
    assert problem is not None
    assert '约 260 字符' in problem
    assert 'MAX_PATH(260)' in problem and '--basetemp=.test_runtime' in problem


def test_default_collection_keeps_schema_gates_and_carrier_regressions(tmp_path):
    """防止以后再用整文件 ``corpus`` 把可合成验证的契约静默排除。"""
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.pop('NOVEL_OUTPUT', None)
    env.pop('NOVEL_CORPUS_MANIFEST', None)
    # A nested tmp_path would deliberately trip the Windows path preflight;
    # give the collection subprocess its own short repository-local base.
    with tempfile.TemporaryDirectory(dir=root, prefix='.c') as short_base:
        result = subprocess.run(
            [
                sys.executable, '-m', 'pytest', '--collect-only', '-vv',
                '-m', 'not corpus', '--basetemp', short_base,
                'tests/test_stage3_free_paths.py',
                'tests/test_stage4.py',
                'tests/test_stage4_repair.py',
                'tests/test_stage6.py',
                'tests/test_wontfix_semantic.py',
                'tests/test_stage7.py',
                'tests/test_api_edits.py',
                'tests/test_api_screens.py',
                'tests/test_views_stage2.py',
                'tests/test_views_stage3.py',
                'tests/test_views_stage4.py',
                'tests/test_views_stage5.py',
                'tests/test_views_stage6.py',
                'tests/test_views_stage7.py',
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            encoding='utf-8',
            check=False,
        )
    assert result.returncode == 0, result.stdout + result.stderr
    default_nodes = (
        'test_synthetic_check_only_runs_in_the_default_suite',
        'test_system_prompt_contains_every_current_style_word',
        'test_synthetic_stage4_response_satisfies_every_reference_hard_rule',
        'test_targeted_plan_repair_failure_writes_invalid_without_overwriting_formal',
        'test_synthetic_stage6_gate_blocks_only_when_every_candidate_is_dirty',
        'test_synthetic_semantic_finding_can_be_marked_without_current_corpus',
        'test_the_current_system_prompt_is_the_rendered_prompt',
        'test_an_illegal_edit_body_is_422_from_the_carrier',
        'test_a_zero_pick_is_422_from_the_carrier',
        'test_approving_names_requires_all_hash_bound_credentials',
        'test_an_unknown_book_is_404_on_every_screen',
        'test_an_illegal_sid_is_400_because_the_client_sent_it',
        'test_a_non_integer_ordinal_is_422_from_the_carrier',
        'test_stage2_view_rejects_an_unknown_synthetic_book',
        'test_stage3_view_rejects_an_unknown_synthetic_book',
        'test_stage4_view_rejects_an_unknown_synthetic_book',
        'test_stage5_views_reject_an_unknown_synthetic_book',
        'test_stage6_view_rejects_an_unknown_synthetic_book',
        'test_stage7_view_rejects_an_unknown_synthetic_book',
    )
    missing = [node for node in default_nodes if node not in result.stdout]
    assert not missing, (
        f'默认套件静默漏收了关键契约：{missing}\n{result.stdout}\n{result.stderr}')


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
    corpus_item = SimpleNamespace(
        get_closest_marker=lambda name: object() if name == 'corpus' else None)
    session = SimpleNamespace(
        config=Config(), exitstatus=pytest.ExitCode.OK, items=[corpus_item])
    hooks.pytest_sessionfinish(session, int(pytest.ExitCode.OK))
    assert session.exitstatus == pytest.ExitCode.OK

    hooks._SEEN_SKIPS.append({
        'nodeid': 'tests/test_z.py::test_new', 'reason': 'new skip'})
    hooks.pytest_sessionfinish(session, int(pytest.ExitCode.OK))
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED


def test_require_corpus_rejects_zero_corpus_tests_even_if_skip_list_matches(monkeypatch):
    from tests import conftest as hooks

    class Plugins:
        @staticmethod
        def get_plugin(_name):
            return None

    class Config:
        _current_corpus_manifest = SimpleNamespace(skip_allowlist=[])
        pluginmanager = Plugins()

        @staticmethod
        def getoption(_name):
            return None

    monkeypatch.setattr(hooks, '_REQUIRE', True)
    hooks._SEEN_SKIPS.clear()
    session = SimpleNamespace(
        config=Config(), exitstatus=pytest.ExitCode.OK, items=[])
    hooks.pytest_sessionfinish(session, int(pytest.ExitCode.OK))
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
