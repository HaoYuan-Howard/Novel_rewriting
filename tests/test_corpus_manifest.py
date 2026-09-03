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

from contracts.artifacts import ARTIFACT_PATHS
from contracts.invariants import NODES
from contracts.schema import (
    CorpusArtifactEntry, CorpusLedgerSummary, CorpusManifest,
    CorpusSkipExpectation, LedgerTokenSummary, artifact_spec, encode_artifact,
)
from contracts.schema.corpus import (
    CURRENT_CORPUS_BOOKS, CURRENT_CORPUS_PAID_NODES,
    required_current_corpus_artifacts,
)
from contracts.schema.stage1 import MainText
from tests import corpus_manifest


def test_documented_script_entrypoint_bootstraps_repo_imports(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, '-X', 'utf8', str(root / 'tests' / 'corpus_manifest.py'), '--help'],
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
            commands.append(
                f'python -X utf8 -u -m novel.cli run {node} {sid}'
                f' --base E:/acceptance --yes')
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
        'normalized_source_sha1': 'b' * 40,
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
        'normalized_source_sha1': book['normalized_source_sha1'],
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


def test_current_corpus_tracks_both_paid_stage4_phases_and_their_outputs():
    paid_nodes = tuple(node for node, spec in NODES.items() if spec.paid)
    assert CURRENT_CORPUS_PAID_NODES == paid_nodes
    assert tuple(
        node for node in CURRENT_CORPUS_PAID_NODES if NODES[node].stage == 'stage4'
    ) == ('stage4.design', 'stage4.plan')
    assert len(CURRENT_CORPUS_BOOKS) * len(CURRENT_CORPUS_PAID_NODES) == 16

    stage4_paid_outputs = {
        rel
        for node in ('stage4.design', 'stage4.plan')
        for rel in NODES[node].produces
    }
    assert stage4_paid_outputs == {
        ARTIFACT_PATHS['stage4.blind_candidate'],
        ARTIFACT_PATHS['stage4.evidence_binding'],
        ARTIFACT_PATHS['stage4.plan_chapters'],
    }
    for _sid, chapters in CURRENT_CORPUS_BOOKS:
        assert stage4_paid_outputs <= required_current_corpus_artifacts(chapters)


@pytest.mark.parametrize('rel', [
    ARTIFACT_PATHS['stage4.blind_candidate'],
    ARTIFACT_PATHS['stage4.evidence_binding'],
])
def test_manifest_rejects_a_missing_stage4_intermediate(rel):
    raw = _manifest_raw()
    artifacts = raw['books'][0]['artifacts']
    raw['books'][0]['artifacts'] = [item for item in artifacts if item['rel'] != rel]
    assert len(raw['books'][0]['artifacts']) == len(artifacts) - 1
    with pytest.raises(ValidationError, match='required artifacts'):
        CorpusManifest.model_validate(raw)


def test_manifest_does_not_let_stage4_plan_ledger_rows_replace_design():
    raw = _manifest_raw()
    nodes = raw['books'][0]['ledger']['nodes']
    nodes['stage4.plan'] += nodes.pop('stage4.design')
    with pytest.raises(ValidationError, match='ledger.nodes'):
        CorpusManifest.model_validate(raw)


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

    # ⚠ 这里曾断言「`stage6.semantic` 的命令缺 `--advisory` 必须被拒」。那个 flag 随
    #   自动放行门一并删除后，这条断的就是**一道产品明说不存在的闸**（`docs/LESSONS.md`
    #   第 21 条第二形状）——删掉它，而不是给冻结命令把 flag 补回去让它变绿。
    #   反向由下面那条「多一个 flag 就拒」覆盖：现在 semantic 只许带 `--base --yes`。
    raw = _manifest_raw()
    index = next(i for i, command in enumerate(raw['commands'])
                 if 'stage6.semantic' in command)
    raw['commands'][index] += ' --advisory'
    with pytest.raises(ValidationError, match='flags 必须恰为'):
        CorpusManifest.model_validate(raw)

    raw = _manifest_raw()
    index = next(i for i, command in enumerate(raw['commands'])
                 if 'stage5.write' in command)
    raw['commands'][index] += ' --deep 1,2,3'
    with pytest.raises(ValidationError, match='flags 必须恰为'):
        CorpusManifest.model_validate(raw)


def test_manifest_accepts_commands_from_the_production_argv_builder(tmp_path):
    from novel.orchestrator.nodes import build_argv

    commands = []
    for sid, _chapters in CURRENT_CORPUS_BOOKS:
        for node in CURRENT_CORPUS_PAID_NODES:
            flags: tuple[str, ...] = ()
            commands.append(subprocess.list2cmdline(build_argv(
                node, sid, flags, base=tmp_path)))
    raw = _manifest_raw()
    raw['commands'] = commands
    got = CorpusManifest.model_validate(raw)
    assert got.commands == commands


def test_manifest_rejects_incomplete_books_ledger_and_artifact_hashes():
    raw = _manifest_raw()
    # ⚠ 取注册表符号而不是字面量：清单里成书那一条记的是**模板 rel**（不含书名，
    #   见 `contracts/schema/corpus.py`）。写死 `stage7/book.txt` 的后果不是红——
    #   过滤器一条都匹配不上，于是这里什么都没删，用例安静地不再制造「缺必备产物」。
    delivery = ARTIFACT_PATHS['stage7.book']
    raw['books'][0]['artifacts'] = [
        item for item in raw['books'][0]['artifacts']
        if item['rel'] != delivery
    ]
    assert len(raw['books'][0]['artifacts']) < len(_manifest_raw()['books'][0]['artifacts']), \
        '过滤器没删掉任何东西——它匹配的 rel 已经不存在了'
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
    '语料缺 current v1 artifact；补法：重跑',
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
                'stage0/revisions.json': {
                    'source': {'normalized_source_sha1': 'b' * 40},
                },
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
        item for item in artifacts if item.rel != ARTIFACT_PATHS['stage7.book']
    ]
    assert len(state[sid]['artifacts']) < len(artifacts), \
        '过滤器没删掉任何东西——它匹配的 rel 已经不存在了'
    with pytest.raises(ValidationError, match='required artifacts'):
        corpus_manifest.verify(tmp_path, manifest)
    state[sid]['artifacts'] = artifacts

    # ⚠ 写成 `item.schema_version + 1` 而不是一个字面量：这条要表达的是「**版本变了**
    #   就该被 inventory hash 抓到」，与那个数字具体是几无关。曾经硬编码成 `1`，
    #   而全表归零到 v1 之后「改成 1」等于没改 ⇒ 用例安静地不再制造漂移。
    state[sid]['artifacts'] = [
        item.model_copy(update={'schema_version': item.schema_version + 1})
        if item.rel == 'stage0/revisions.json' else item
        for item in artifacts
    ]
    with pytest.raises(ValueError, match='inventory hash 漂移'):
        corpus_manifest.verify(tmp_path, manifest)
    state[sid]['artifacts'] = artifacts

    # ⚠ 同上一格那条纪律：取注册表符号，别写死 `stage7/book.txt`。写死的后果不是红——
    #   条件一条都不命中 ⇒ 这份 artifacts 与原件逐字相同 ⇒ 用例安静地不再制造漂移。
    state[sid]['artifacts'] = [
        item.model_copy(update={'sha256': 'f' * 64})
        if item.rel == ARTIFACT_PATHS['stage7.book'] else item
        for item in artifacts
    ]
    assert state[sid]['artifacts'] != artifacts, '没有制造出任何漂移，这一格失去了判别力'
    with pytest.raises(ValueError, match='inventory hash 漂移'):
        corpus_manifest.verify(tmp_path, manifest)
    state[sid]['artifacts'] = artifacts

    state[sid]['payloads']['stage0/revisions.json']['source']['normalized_source_sha1'] = 'c' * 40
    with pytest.raises(ValueError, match='inventory hash 漂移'):
        corpus_manifest.verify(tmp_path, manifest)
    state[sid]['payloads']['stage0/revisions.json']['source']['normalized_source_sha1'] = 'b' * 40

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
        'chapter_ordinal': 1, 'text': '正文。',
    }, {
        'chapter_ordinal': 2, 'text': '续文。',
    }])
    target.write_text(json.dumps(encode_artifact(
        'stage1/main_text.json', main.model_dump(mode='json'))), encoding='utf-8')
    entries, payloads, _codes = corpus_manifest._artifact_inventory(book)
    # ⚠ 版本号现算，不写死：本条测的是「root 信封里的版本被解出来了」，
    #   而不是「这份产物恰好是 v1」——写死会让任何一次合规升版连带炸掉它。
    assert entries == [CorpusArtifactEntry(
        rel='stage1/main_text.json',
        schema_version=artifact_spec('stage1/main_text.json').current_version,
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


def test_inventory_refuses_a_raw_non_current_version(tmp_path):
    """⚠ **这条不能因为「迁移机制已经删了」就跟着删。**

    `_artifact_inventory` 在 decode 之前先比 **raw marker**，是一道独立于
    `decode_artifact` 的闸——它防的是「迁移之后冒充 current」。迁移梯子拆掉之后
    这条的语义变成「raw marker 必须直接等于 current」，仍然值钱：
    盘面清点要的是**盘上那个数字**，不是解码器愿意接受的那个数字。
    """
    from tests.test_stage2 import global_grounding

    book = tmp_path / 'book_123'
    target = book / 'stage2' / 'global_grounding.json'
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({
        '_schema_version': 2,
        **global_grounding(),
    }, ensure_ascii=False), encoding='utf-8')

    spec = artifact_spec('stage2/global_grounding.json')
    assert spec is not None
    with pytest.raises(
            ValueError,
            match=rf'raw _schema_version=2.*current v{spec.current_version}.*从 producer 重跑'):
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
    config.option.numprocesses = 2
    with pytest.raises(pytest.UsageError, match='must run serially'):
        hooks.pytest_configure(config)

    config.option.numprocesses = 0
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


#: 这两条预检用例的**假仓根**。相对 basetemp 的绝对长度取决于仓库住在哪儿，
#: 拿真 cwd 当基准就是把宿主环境当被测对象（实测：`Novel_rewriting_stage01`
#: 这个 worktree 名比主仓长 8 字符，`.test_runtime` 于是从 252 变成 260 而假红）。
_FAKE_ROOT = 'C:/repo'
#: 「仓内已放不下任何 basetemp」那一支的假仓根。长到必然触发仓外建议。
_FAKE_DEEP_ROOT = 'C:/' + '/'.join(['verylongsegment'] * 12)


def test_windows_basetemp_preflight_rejects_deep_paths_before_stage0_runs():
    """MAX_PATH 未解除的机器上，深 basetemp 必须在 Stage0 炸之前被拦下。

    ⚠ 一律显式传 `long_paths=False`。不传就等于把这条断言的成立与否交给**宿主机器的
    注册表**——在一台已解除限制的机器上，预检本该放行，于是这个测试会假红。
    ⚠ 一律显式传 `root=_FAKE_ROOT`，理由同构：不传就等于把断言交给**仓库自己的
    路径长度**。docstring 此前只挡住了注册表那一维，`.test_runtime` 必须被放行
    这一条于是在任何路径较长的 clone / worktree 里假红。
    """
    from tests import conftest as hooks

    assert hooks._windows_basetemp_problem(
        '.test_runtime', os_name='nt', long_paths=False, root=_FAKE_ROOT) is None
    assert hooks._windows_basetemp_problem(
        'x' * 400, os_name='posix', long_paths=False, root=_FAKE_ROOT) is None
    assert tuple(hooks._STAGE0_TMP_SUFFIX.parts[-3:-1]) == ('stage0', '_rounds')
    assert 'Output' in hooks._STAGE0_TMP_SUFFIX.parts

    # Pin the actual 259/260 boundary instead of testing only an obviously
    # enormous path that would not catch an underestimated relative suffix.
    first_refused = next(
        size for size in range(1, 500)
        if hooks._windows_basetemp_problem(
            'x' * size, os_name='nt', long_paths=False, root=_FAKE_ROOT) is not None
    )
    assert hooks._windows_basetemp_problem(
        'x' * (first_refused - 1), os_name='nt', long_paths=False,
        root=_FAKE_ROOT) is None
    problem = hooks._windows_basetemp_problem(
        'x' * first_refused, os_name='nt', long_paths=False, root=_FAKE_ROOT)
    assert problem is not None
    assert '约 260 字符' in problem
    assert 'MAX_PATH(260)' in problem and '--basetemp=.tr' in problem
    # 报错必须同时给出两条出路，否则人只会去缩路径，永远不知道限制可以一次性解除。
    assert 'LongPathsEnabled' in problem


def test_windows_basetemp_preflight_stands_down_once_the_limit_is_lifted():
    """限制解除之后，同一个深 basetemp 必须放行。

    这是 `docs/LESSONS.md` 第 21 条那一格：守卫在问题已经消失之后继续拦，就是假红。
    先前这里硬编码 260、不问机器状态，于是开启系统长路径也换不来一次能跑的深 basetemp。
    """
    from tests import conftest as hooks

    for size in (1, 200, 400, 4000):
        assert hooks._windows_basetemp_problem(
            'x' * size, os_name='nt', long_paths=True, root=_FAKE_ROOT) is None


def test_xdist_worker_suffix_is_reserved_before_workers_start():
    """controller 必须把 ``popen-gwN`` 算进预算，不能放行后让 worker 才炸。"""
    from tests import conftest as hooks

    config = SimpleNamespace(option=SimpleNamespace(numprocesses=4))
    suffix = hooks._xdist_worker_suffix(config)
    assert suffix == 'popen-gw999'
    boundary_root = next(
        f'C:/{"r" * size}' for size in range(1, 240)
        if hooks._windows_basetemp_problem(
            '.test_runtime', os_name='nt', long_paths=False,
            root=f'C:/{"r" * size}') is None
        and hooks._windows_basetemp_problem(
            '.test_runtime', os_name='nt', long_paths=False,
            root=f'C:/{"r" * size}', xdist_suffix=suffix) is not None
    )
    problem = hooks._windows_basetemp_problem(
        '.test_runtime', os_name='nt', long_paths=False,
        root=boundary_root, xdist_suffix=suffix)
    assert problem is not None and f'xdist 子目录 {suffix}' in problem
    assert '--basetemp=.tr' in problem
    assert hooks._windows_basetemp_problem(
        '.tr', os_name='nt', long_paths=False,
        root=boundary_root, xdist_suffix=suffix) is None


def test_the_preflight_never_recommends_a_basetemp_it_would_itself_refuse():
    """⭐ 预检给的出路必须是**算过的**，不是写死的一句话。

    实测踩过：无条件推荐 `--basetemp=.test_runtime`，而在
    `Novel_rewriting_stage01` 这个 worktree 里那个值自己恰好 260 ⇒ 照着做的人
    原地打转，一次都跑不起来。⇒ 仓内还放得下才报仓内短名，放不下就明说仓内无解。
    """
    from tests import conftest as hooks

    shallow = hooks._windows_basetemp_problem(
        'x' * 400, os_name='nt', long_paths=False, root=_FAKE_ROOT)
    assert shallow is not None and '--basetemp=.tr' in shallow
    # 推荐的那个值，预检自己必须放行——否则这句建议是空头支票。
    assert hooks._windows_basetemp_problem(
        '.tr', os_name='nt', long_paths=False, root=_FAKE_ROOT) is None

    deep = hooks._windows_basetemp_problem(
        '.test_runtime', os_name='nt', long_paths=False, root=_FAKE_DEEP_ROOT)
    assert deep is not None, '仓根长到这个程度，仓内任何 basetemp 都该被拦'
    assert '--basetemp=.tr' not in deep,         '仓内已放不下，还推荐仓内短名 = 推荐一个自己会拒绝的值'
    assert '仓外' in deep and '--basetemp=C:/_tr' in deep


def test_long_path_probe_measures_the_host_and_leaves_nothing_behind():
    """探测本身：给出 bool、可缓存、且不在系统 temp 里留残留目录。

    ⚠ 它是**实测**（真去建一个 >260 的目录），所以「不留垃圾」是它的正确性的一部分——
    未解除的机器上多层探测会留下一串连清理自己都删不掉的中间目录。
    """
    from tests import conftest as hooks

    before = set(Path(tempfile.gettempdir()).glob('lp-*'))
    first = hooks._long_paths_usable()
    assert isinstance(first, bool)
    assert hooks._long_paths_usable() is first          # lru_cache，不重复摸盘
    assert set(Path(tempfile.gettempdir()).glob('lp-*')) == before


def test_pytest_temp_paths_restore_only_this_run_and_the_controller_cache(tmp_path):
    """不扫描陈旧 basetemp；worker 只修自己的根，cache 只由 controller 修。"""
    from tests import conftest as hooks

    class Config:
        rootpath = tmp_path
        option = SimpleNamespace(basetemp='.test_runtime')

        @staticmethod
        def getini(name):
            assert name == 'cache_dir'
            return '.test_cache'

    runtime, cache = hooks._configured_pytest_temp_dirs(Config())
    assert (runtime, cache) == (
        tmp_path / '.test_runtime', tmp_path / '.test_cache')
    worker = runtime / 'popen-gw0'
    worker.mkdir(parents=True)
    cache.mkdir()

    selected = hooks._pytest_temp_dirs_to_restore(Config(), {worker})
    assert selected == (worker, cache)
    assert runtime not in selected, '不得递归扫描整个陈旧 .test_runtime'

    worker_config = Config()
    worker_config.workerinput = {'workerid': 'gw0'}
    assert hooks._pytest_temp_dirs_to_restore(
        worker_config, {worker}) == (worker,)
    assert hooks._pytest_temp_dirs_to_restore(Config(), set()) == (cache,)

    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0)

    assert hooks._restore_windows_test_acls(
        selected,
        os_name='nt',
        env={'USERDOMAIN': 'DOMAIN', 'USERNAME': 'reader'},
        runner=runner,
    ) == []
    assert [call[0][1] for call in calls] == [str(worker), str(cache)]
    for argv, kwargs in calls:
        assert argv[0] == 'icacls.exe'
        assert argv[2:] == [
            '/inheritance:e', '/grant:r', 'DOMAIN\\reader:(OI)(CI)F',
            '/T', '/C', '/Q',
        ]
        assert kwargs['check'] is False and kwargs['cwd'] == hooks._ROOT

    calls.clear()
    assert hooks._restore_windows_test_acls(
        (runtime,), os_name='posix', runner=runner) == []
    assert calls == []


def test_stdlib_temp_is_redirected_under_the_managed_root_and_removed(tmp_path):
    """遗留 mkdtemp 不再污染系统 temp，连同管理根在退出时自动清理。"""
    from tests import conftest as hooks

    managed = tmp_path / 'managed-stdlib-temp'
    previous = tempfile.tempdir
    with hooks._redirect_stdlib_temp(managed):
        created = Path(tempfile.mkdtemp())
        assert created.parent == managed
        assert tempfile.gettempdir() == str(managed)
    assert tempfile.tempdir == previous
    assert not managed.exists()


def test_only_corpus_and_provenance_tests_use_the_live_code_version():
    """普通测试身份是确定值，但真实来源账的边界不能被替身覆盖。"""
    from tests import conftest as hooks

    class Node:
        def __init__(self, *markers):
            self.markers = set(markers)

        def get_closest_marker(self, name):
            return object() if name in self.markers else None

    assert not hooks._uses_real_code_version(Node(), require=False)
    assert hooks._uses_real_code_version(Node('corpus'), require=False)
    assert hooks._uses_real_code_version(Node('real_code_version'), require=False)
    assert hooks._uses_real_code_version(Node(), require=True)


def test_default_collection_keeps_schema_gates_and_carrier_regressions(
        tmp_path_factory):
    """防止以后再用整文件 ``corpus`` 把可合成验证的契约静默排除。"""
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.pop('NOVEL_OUTPUT', None)
    env.pop('NOVEL_CORPUS_MANIFEST', None)
    # 外层 pytest 的 basetemp 已经过同一套 Windows MAX_PATH 预检；嵌套收集应复用
    # 那个短根，而不是回到可能很长的 worktree。必须再分一个唯一子目录：pytest
    # 启动时会清空它收到的 --basetemp，直接传外层根会把仍在运行的外层临时区删掉。
    short_base = tmp_path_factory.mktemp('c')
    result = subprocess.run(
        [
            sys.executable, '-m', 'pytest', '-n', '0', '--collect-only', '-vv',
            '-m', 'not corpus', '--basetemp', str(short_base),
                'tests/test_stage4.py',
            'tests/test_stage4_beats.py',
            'tests/test_stage4_repair.py',
            'tests/test_stage6.py',
            'tests/test_wontfix_semantic.py',
            'tests/test_stage7.py',
            'tests/test_api_edits.py',
            'tests/test_api_screens.py',
            'tests/test_views_stage2.py',
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
        'test_system_prompt_contains_every_current_style_word',
        'test_clean_compiled_plan_closes_all_new_scene_hard_rules',
        'test_revalidate_promotes_only_plan_without_llm_or_context',
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
