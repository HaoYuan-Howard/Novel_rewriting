from __future__ import annotations

import io
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.invariants import NODES
from novel.config import Config
from novel.stages.stage6 import hook, prompts
from tools import stage6_style_ab as A


def _source_base(tmp_path: Path) -> tuple[Path, tuple[str, str]]:
    base = tmp_path / 'live-output'
    sids = ('101', '202')
    required = {
        'stage0/clean_text.txt': '正文',
        'stage1/hook_copy.json': '{}',
        'stage2/global_grounding.json': '{}',
        'stage2/global_review.json': '{}',
        'stage3/style_grounding.json': '{}',
        'stage4/writer_context.json': '{}',
        'stage5/deep/ch01.json': '{}',
    }
    for sid in sids:
        book = base / f'book_{sid}'
        for stage in range(6):
            (book / f'stage{stage}').mkdir(parents=True, exist_ok=True)
        for rel, text in required.items():
            (book / rel).parent.mkdir(parents=True, exist_ok=True)
            (book / rel).write_text(text, encoding='utf-8')
    return base, sids


@pytest.fixture
def protocol_files(tmp_path: Path, monkeypatch):
    markdown = tmp_path / 'protocol.md'
    machine = tmp_path / 'protocol.json'
    markdown.write_text('# frozen\n', encoding='utf-8')
    machine.write_text('{"schema_version":1}\n', encoding='utf-8')
    monkeypatch.setattr(A, 'PROTOCOL_FILE', markdown)
    monkeypatch.setattr(A, 'MACHINE_PROTOCOL_FILE', machine)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'fixture-secret-from-environment')
    return markdown, machine


def test_preview_uses_production_argv_and_writes_nothing(
    tmp_path, protocol_files, capsys,
):
    source, sids = _source_base(tmp_path)
    root = tmp_path / 'experiment'
    before = sorted(p.relative_to(source).as_posix() for p in source.rglob('*'))

    code = A.main([
        '--source-base', str(source), '--experiment-root', str(root),
        '--sid', sids[0], '--sid', sids[1],
    ])

    assert code == 0 and not root.exists()
    assert before == sorted(p.relative_to(source).as_posix() for p in source.rglob('*'))
    text = capsys.readouterr().out
    assert 'cells=20' in text and '--yes' in text
    assert '未加 --execute' in text


def test_plan_balances_order_and_isolates_every_output_and_cache(
    tmp_path, protocol_files,
):
    source, sids = _source_base(tmp_path)
    plan = A.build_plan(
        source_base=source, experiment_root=tmp_path / 'experiment', sids=sids)
    cells = plan['_cells']

    assert len(cells) == 20
    assert sum(c.order == 1 and c.arm == A.ARM_FULL for c in cells) == 5
    assert sum(c.order == 1 and c.arm == A.ARM_NO_QUAL for c in cells) == 5
    assert len({c.output_base for c in cells}) == 20
    assert len({c.cache_dir for c in cells}) == 20
    for index, cell in enumerate(cells, 1):
        physical = (tmp_path / 'experiment' / 'c' / f'{index:02d}')
        assert cell.output_base == physical / 'o'
        assert cell.cache_dir == physical / 'k'
        for argv in (cell.argv, cell.check_argv):
            assert argv[argv.index('--base') + 1] == str(cell.output_base)
    assert all('--yes' in c.argv for c in cells)
    assert all('--check-only' in c.check_argv and '--yes' not in c.check_argv
               for c in cells)
    assert plan['protocol_sha256'] == A._sha256(protocol_files[0])
    assert plan['machine_protocol_sha256'] == A._sha256(protocol_files[1])
    assert len(plan['git_head']) == 40 and plan['code_version'] != 'unknown'
    assert plan['path_budget']['max_clone_target_chars'] < 240


def test_dotenv_is_never_copied_hashed_or_rendered(
    tmp_path, protocol_files,
):
    source, sids = _source_base(tmp_path)
    dotenv = tmp_path / 'private.env'
    secret = 'top-secret-never-print'
    dotenv.write_text(
        f'DEEPSEEK_API_KEY={secret}\nNOVEL_CACHE_DIR={tmp_path / "production-cache"}\n',
        encoding='utf-8')
    plan = A.build_plan(
        source_base=source, experiment_root=tmp_path / 'experiment',
        sids=sids, dotenv=dotenv)
    public = json.dumps(A._without_private(plan), ensure_ascii=False)
    rendered = A.render(plan, execute=False)
    assert secret not in public + rendered
    assert 'dotenv_sha256' not in public
    assert plan['config_source'] == {
        'dotenv_path': str(dotenv.resolve()),
        'precedence': 'process-environment-over-dotenv',
    }
    assert all(c.cache_dir != tmp_path / 'production-cache' for c in plan['_cells'])


def test_plan_fails_closed_when_explicit_dotenv_has_no_api_key(
    tmp_path, protocol_files, monkeypatch,
):
    source, sids = _source_base(tmp_path)
    dotenv = tmp_path / 'empty.env'
    dotenv.write_text('NOVEL_MODEL=deepseek-reasoner\n', encoding='utf-8')
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)

    with pytest.raises(ValueError, match='DEEPSEEK_API_KEY 未配置') as caught:
        A.build_plan(
            source_base=source,
            experiment_root=tmp_path / 'experiment',
            sids=sids,
            dotenv=dotenv,
        )

    assert 'Config' not in str(caught.value)


@pytest.mark.skipif(os.name != 'nt', reason='Windows 路径别名语义')
def test_external_extended_path_aliases_are_rejected_for_source_root_and_cache(
    tmp_path, protocol_files,
):
    source, sids = _source_base(tmp_path)
    experiment = (tmp_path / 'experiment').resolve()

    with pytest.raises(ValueError, match='extended/device prefix'):
        A.build_plan(
            source_base='\\\\?\\' + str(source.resolve()),
            experiment_root=experiment,
            sids=sids,
        )
    with pytest.raises(ValueError, match='extended/device prefix'):
        A._resolved('\\\\?\\UNC\\server\\share\\outputs')
    with pytest.raises(ValueError, match='extended/device prefix'):
        A.build_plan(
            source_base=source,
            experiment_root='\\\\?\\' + str(experiment),
            sids=sids,
        )

    dotenv = tmp_path / 'extended-cache.env'
    dotenv.write_text(
        'DEEPSEEK_API_KEY=fixture-secret\n'
        f'NOVEL_CACHE_DIR=\\\\?\\{experiment}\n',
        encoding='utf-8',
    )
    with pytest.raises(ValueError, match='extended/device prefix'):
        A.build_plan(
            source_base=source,
            experiment_root=experiment,
            sids=sids,
            dotenv=dotenv,
        )

    # 外部输入拒绝别名，不影响内部为长路径 I/O 生成同一 namespace。
    assert A._io_path(experiment).startswith('\\\\?\\')


def test_frozen_request_config_and_upstream_drift_are_rejected(
    tmp_path, protocol_files,
):
    source, sids = _source_base(tmp_path)
    plan = A.build_plan(
        source_base=source, experiment_root=tmp_path / 'experiment',
        sids=sids, repeats=1)
    good = Config(
        api_key='secret', model=plan['model'],
        thinking_mode=plan['thinking_mode'],
        max_retries=plan['budgets']['http_retries_per_rung'],
    )
    A._assert_config_frozen(plan, good, where='test')
    with pytest.raises(RuntimeError, match='请求配置漂移'):
        A._assert_config_frozen(
            plan, Config(api_key='secret', model='drifted-model'), where='test')

    cell = plan['_cells'][0]
    (cell.source_book / 'stage0/clean_text.txt').write_text('漂移', encoding='utf-8')
    with pytest.raises(ValueError, match='plan 冻结 SHA'):
        A._prepare_or_validate_cell(
            cell,
            expected_upstream_sha256=plan['books'][0]['stage0_to_stage5_sha256'],
        )
    assert not cell.output_base.exists()


@pytest.mark.skipif(os.name != 'nt', reason='只验证 Windows extended-length 路径')
def test_prepare_cell_copies_and_hashes_files_beyond_windows_max_path(
    tmp_path, protocol_files,
):
    source, sids = _source_base(tmp_path)
    rel = (Path('stage2/_revalidation') / ('nested-' + 'x' * 48)
           / ('b.' + 'a' * 64 + '.snapshot'))
    source_file = source / f'book_{sids[0]}' / rel
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b'long-path-bytes\x00\xff')
    experiment = tmp_path / ('experiment-' + 'e' * 96)
    output = experiment / 'c' / '01' / 'o'
    steps = A.production_nodes.build_steps(
        'stage6.hook', sids[0], base=output, follow_check=True)
    cell = A.Cell(
        sid=sids[0], book_index=0, pair=1, order=1, arm=A.ARM_FULL,
        source_book=source / f'book_{sids[0]}', output_base=output,
        cache_dir=experiment / 'c' / '01' / 'k',
        argv=tuple(steps[0].argv), check_argv=tuple(steps[1].argv),
    )
    target = cell.novel_dir / rel
    assert len(str(target)) > 260

    A._prepare_cell(cell)

    assert A._sha256(target) == A._sha256(source_file)
    assert A._tree_sha256(cell.novel_dir) == A._tree_sha256(cell.source_book)
    inventory = {row['rel']: row['sha256']
                 for row in A._upstream_inventory(cell.novel_dir)}
    assert inventory[rel.as_posix()] == A._sha256(source_file)


def test_compact_layout_enforces_review_rglob_path_budget(
    tmp_path, protocol_files,
):
    source, sids = _source_base(tmp_path)
    rel = (Path('stage5/deep/_revalidation') / ('nested-' + 'x' * 10)
           / ('b.' + 'f' * 64 + '.snapshot'))
    for sid in sids:
        item = source / f'book_{sid}' / rel
        item.parent.mkdir(parents=True)
        item.write_bytes(b'review-needs-ordinary-rglob')
    padding = max(1, 100 - len(str(tmp_path)) - 1)
    experiment = tmp_path / ('e' * padding)
    assert len(str(experiment)) == 100

    plan = A.build_plan(
        source_base=source, experiment_root=experiment, sids=sids, repeats=5)

    cells = plan['_cells']
    assert len(cells) == 20
    peaks = []
    for cell in cells:
        book = plan['books'][cell.book_index]
        peak = max(len(str(cell.novel_dir / row['rel']))
                   for row in book['upstream_inventory'])
        peaks.append(peak)
        assert peak < A.REVIEW_PATH_LIMIT_EXCLUSIVE
    assert max(peaks) == plan['path_budget']['max_clone_target_chars']
    assert max(peaks) >= 225  # 真正逼近预算，不是拿很短的 tmp 路径做假绿。


def test_partial_copy_resume_is_safe_only_without_paid_or_runtime_evidence(
    tmp_path, protocol_files,
):
    source, sids = _source_base(tmp_path)
    plan = A.build_plan(
        source_base=source, experiment_root=tmp_path / 'experiment',
        sids=sids, repeats=1)
    expected = {book['sid']: book['stage0_to_stage5_sha256']
                for book in plan['books']}
    safe, unsafe = plan['_cells'][:2]

    safe.novel_dir.mkdir(parents=True)
    shutil.copytree(safe.source_book / 'stage0', safe.novel_dir / 'stage0')
    A._prepare_or_validate_cell(
        safe, expected_upstream_sha256=expected[safe.sid])
    assert safe.cache_dir.is_dir()
    assert A._tree_sha256(safe.novel_dir) == expected[safe.sid]

    unsafe.novel_dir.mkdir(parents=True)
    shutil.copytree(unsafe.source_book / 'stage0', unsafe.novel_dir / 'stage0')
    service = unsafe.output_base / '_service'
    service.mkdir(parents=True)
    (service / 'ledger.jsonl').write_text('{"outcome":"unknown"}\n', encoding='utf-8')
    with pytest.raises(ValueError, match='已有运行/付费证据'):
        A._prepare_or_validate_cell(
            unsafe, expected_upstream_sha256=expected[unsafe.sid])
    assert not (unsafe.novel_dir / 'stage1').exists()


def test_preflight_only_plan_rebase_preserves_failed_plan_and_records_zero_paid(
    tmp_path, protocol_files,
):
    source, sids = _source_base(tmp_path)
    plan = A.build_plan(
        source_base=source, experiment_root=tmp_path / 'experiment',
        sids=sids, repeats=1)
    root = Path(plan['experiment_root'])
    root.mkdir(parents=True)
    current = A._without_private(plan)
    stored = dict(current)
    stored.update({
        'plan_sha256': '1' * 64,
        'runner_sha256': '2' * 64,
        'code_version': 'pre-copy-failure-version',
    })
    A._atomic_json(root / 'plan.json', stored)
    cell = plan['_cells'][0]
    cell.novel_dir.mkdir(parents=True)
    shutil.copytree(cell.source_book / 'stage0', cell.novel_dir / 'stage0')

    assert A._resume_preflight_rebase_allowed(
        root, plan['_cells'], stored, current)
    A._rebase_preflight_plan(root, stored, current)

    assert json.loads((root / 'plan.json').read_text(encoding='utf-8')) == current
    archived = root / 'plan.preflight-failed.111111111111.json'
    assert json.loads(archived.read_text(encoding='utf-8')) == stored
    audit = json.loads(
        (root / 'PLAN_PREFLIGHT_REBASE.json').read_text(encoding='utf-8'))
    assert audit['paid_attempts'] == 0


def test_retired_qualitative_ablation_owns_legacy_packet_and_restores_production_patch():
    style = {
        'hook_style': {'person': 'third'},
        'voice': {'register_tone': 'x'},
        'surface_habits': [{'description': 'x'}],
        'chapter_styles': [{'source_ordinal': 1, 'voice': {'stance': '冷'}}],
        'measurable': {'hook': {
            'chars': 123,
            'avg_sentence_chars': 10,
            'avg_paragraph_chars': 12,
            'quote_styles': ['“”'],
            'dialogue_ratio': 0.2,
        }},
    }
    ctx = {'chapters': [{'output_ordinal': 1, 'from_source_ordinals': [1]}]}
    full = A._legacy_style_packet(style, ctx, 1)
    no_qual = A.no_qual_style_packet(style, ctx, 1)
    assert set(A.QUALITATIVE_KEYS) <= set(full)
    assert not set(A.QUALITATIVE_KEYS) & set(no_qual)
    assert no_qual['measurable'] == full['measurable']
    assert no_qual['source_hook_shape'] == full['source_hook_shape']

    original_material = prompts.build_material
    original_system = prompts.hook_system

    with A._arm_patch(A.ARM_NO_QUAL):
        material = prompts.build_material(
            title='书', ctx=ctx, style=style, grounding={}, source_hook='原文。',
            lead=[], missing=[], upto=1)
        packet = json.loads(material.blocks[1])['style']
        system = prompts.hook_system()
        assert not set(A.QUALITATIVE_KEYS) & set(packet)
        assert packet['measurable'] == full['measurable']
        assert packet['source_hook_shape'] == full['source_hook_shape']
        assert all(old not in system and new in system
                   for old, new in A._SYSTEM_REPLACEMENTS)

    assert prompts.build_material is original_material
    assert prompts.hook_system is original_system
    assert not hasattr(prompts, 'style_packet'), '生产模块不得重新暴露定性 packet 注入口'


def test_pair_contract_requires_exact_user_bytes_and_reports_missing_keys(monkeypatch):
    system_a = '\n'.join(old for old, _new in A._SYSTEM_REPLACEMENTS)
    system_b = system_a
    for old, new in A._SYSTEM_REPLACEMENTS:
        system_b = system_b.replace(old, new)
    style = {
        **{key: {'evidence': key} for key in A.QUALITATIVE_KEYS},
        'source_hook_shape': {'chars': 'short'},
        'measurable': {'hook': {'chars': 100}},
    }
    packet_a = {'title': '书', 'style': style, 'truth': {'fact': 1}}
    packet_b = json.loads(json.dumps(packet_a, ensure_ascii=False))
    for key in A.QUALITATIVE_KEYS:
        packet_b['style'].pop(key)
    user_a = '说明\n\n' + json.dumps(packet_a, ensure_ascii=False, indent=2)
    user_b = '说明\n\n' + json.dumps(packet_b, ensure_ascii=False, indent=2)

    monkeypatch.setattr(
        A, '_prompt', lambda _cell, arm: (
            (system_a, user_a) if arm == A.ARM_FULL else (system_b, user_b)))
    A._assert_pair_prompt_contract(object(), object())

    compact_b = '说明\n\n' + json.dumps(packet_b, ensure_ascii=False)
    monkeypatch.setattr(
        A, '_prompt', lambda _cell, arm: (
            (system_a, user_a) if arm == A.ARM_FULL else (system_b, compact_b)))
    with pytest.raises(AssertionError, match='逐字契约'):
        A._assert_pair_prompt_contract(object(), object())

    missing_a = json.loads(json.dumps(packet_a, ensure_ascii=False))
    missing_a['style'].pop(A.QUALITATIVE_KEYS[0])
    missing_user = '说明\n\n' + json.dumps(missing_a, ensure_ascii=False, indent=2)
    monkeypatch.setattr(
        A, '_prompt', lambda _cell, arm: (
            (system_a, missing_user) if arm == A.ARM_FULL else (system_b, user_b)))
    with pytest.raises(AssertionError, match='没有完整携带'):
        A._assert_pair_prompt_contract(object(), object())


def _ledger_row(*, purpose='stage6.hook', effort='high', thinking=True,
                max_tokens=hook.MAX_TOKENS, fingerprint='main-fp'):
    retry = purpose.startswith('stage6.hook:retry:')
    return {
        'sid': '101', 'node': 'stage6.hook', 'model': 'model-x',
        'cached': False, 'cache_policy': 'refresh-write' if retry else 'default',
        'prompt_tokens': 100, 'completion_tokens': 20, 'reasoning_tokens': 10,
        'prompt_cache_hit_tokens': 40, 'prompt_cache_miss_tokens': 60,
        'purpose': purpose, 'outcome': 'ok', 'reasoning_effort': effort,
        'thinking': thinking, 'json_mode': True, 'max_tokens': max_tokens,
        'request_fingerprint': fingerprint,
    }


def test_ledger_contract_records_unique_retry_purpose_and_request_fingerprints():
    rows = [
        _ledger_row(),
        _ledger_row(
            purpose='stage6.hook:retry:c1', effort='low', thinking=False,
            max_tokens=hook.CANDIDATE_RETRY_MAX_TOKENS, fingerprint='retry-fp'),
    ]
    got = A.ledger_summary(rows, sid='101', model='model-x')
    assert got['initial_ok'] == 1 and got['retry_logical'] == 1
    assert got['prompt_tokens'] == 200 and got['completion_tokens'] == 40
    assert got['initial_request_fingerprints'] == ['main-fp']
    assert got['retry_request_fingerprints'] == ['retry-fp']

    bad = [dict(rows[0], reasoning_effort='low')]
    with pytest.raises(ValueError, match='降级档'):
        A.ledger_summary(bad, sid='101', model='model-x')
    bad = [dict(rows[0], cached=True)]
    with pytest.raises(ValueError, match='cached'):
        A.ledger_summary(bad, sid='101', model='model-x')


def test_only_retry_count_is_a_hard_pause_line():
    def result(retries, prompt, completion):
        return A.CellResult(
            key='k', sid='101', pair=1, arm=A.ARM_FULL,
            artifact=Path('hook.json'), hook_rel='stage6/hook.json',
            artifact_sha256='a' * 64, ledger_sha256='b' * 64,
            retries=retries, prompt_tokens=prompt, completion_tokens=completion,
            reasoning_tokens=0, wire_rows=1, model='m', efforts=('high',),
            thinking=(True,), system_sha256='c' * 64, user_sha256='d' * 64,
            prompt_sha256='e' * 64, generation_exit=0, check_exit=0,
            generation_job_id='j1', check_job_id='j2',
            initial_request_fingerprints=('fp',), retry_request_fingerprints=(),
        )

    assert A.budget_problem([result(6, 9_000_000, 9_000_000)]) is None
    assert '7 > 6' in (A.budget_problem([result(7, 1, 1)]) or '')


def test_job_step_goes_through_jobs_run_and_exact_preview_step(tmp_path, monkeypatch):
    source = tmp_path / 'book_101'
    output = tmp_path / 'cell' / 'output'
    steps = A.production_nodes.build_steps('stage6.hook', '101', base=output)
    cell = A.Cell(
        sid='101', book_index=0, pair=1, order=1, arm=A.ARM_FULL,
        source_book=source, output_base=output, cache_dir=tmp_path / 'cache',
        argv=tuple(steps[0].argv), check_argv=tuple(steps[1].argv))
    seen = []

    monkeypatch.setattr(A, '_invoke_cli', lambda argv, **_kwargs: seen.append(tuple(argv)) or 0)

    def fake_run(base, node, sid, flags, **kwargs):
        step = kwargs['steps'][0]
        log = io.BytesIO()
        rc = kwargs['spawn'](step.argv, step.cwd, {}, log)
        return SimpleNamespace(exit_code=rc, job_id='job-1')

    monkeypatch.setattr(A.jobs, 'run', fake_run)
    record = A._run_job_step(cell, check=False, config=Config(api_key='secret'))
    assert record.exit_code == 0 and seen == [cell.argv]


def test_git_head_uses_worktree_scoped_safe_directory(monkeypatch):
    captured = {}

    def run(argv, **kwargs):
        captured['argv'] = argv
        return SimpleNamespace(returncode=0, stdout=b'a' * 40 + b'\n', stderr=b'')

    monkeypatch.setattr(A.subprocess, 'run', run)
    assert A._git_head() == 'a' * 40
    assert captured['argv'][1:3] == ['-c', f'safe.directory={A.REPO_ROOT.as_posix()}']


def test_run_manifest_exposes_strict_arm_evidence_without_candidate_text(
    tmp_path, protocol_files,
):
    source, sids = _source_base(tmp_path)
    plan = A.build_plan(
        source_base=source, experiment_root=tmp_path / 'experiment', sids=sids)
    cell = plan['_cells'][0]
    row = A.CellResult(
        key=cell.key, sid=cell.sid, pair=cell.pair, arm=cell.arm,
        artifact=cell.novel_dir / 'stage6/hook.INVALID.json',
        hook_rel='stage6/hook.INVALID.json', artifact_sha256='a' * 64,
        ledger_sha256='b' * 64, retries=1, prompt_tokens=100,
        completion_tokens=20, reasoning_tokens=10, wire_rows=2,
        model='model-x', efforts=('high', 'low'), thinking=(False, True),
        system_sha256='c' * 64, user_sha256='d' * 64,
        prompt_sha256='e' * 64, generation_exit=2, check_exit=None,
        generation_job_id='job-g', check_job_id=None,
        initial_request_fingerprints=('main-fp',),
        retry_request_fingerprints=('retry-fp',),
    )
    manifest = A._run_manifest(plan, [row], status='running')
    arm = manifest['books'][0]['rounds'][0]['arms'][cell.arm]
    assert arm['hook_rel'] == 'stage6/hook.INVALID.json'
    assert arm['prompt']['system_sha256'] == 'c' * 64
    assert arm['upstream']['inventory']
    assert arm['ledger']['initial_request_fingerprints'] == ['main-fp']
    assert manifest['protocol']['machine_sha256'] == A._sha256(protocol_files[1])
    assert 'candidate text' not in json.dumps(manifest, ensure_ascii=False)


def test_resume_skips_completed_paid_cells_and_only_runs_pending_cells(
    tmp_path, protocol_files, monkeypatch,
):
    source, sids = _source_base(tmp_path)
    plan = A.build_plan(
        source_base=source, experiment_root=tmp_path / 'experiment',
        sids=sids, repeats=1)
    root = Path(plan['experiment_root'])
    root.mkdir(parents=True)
    A._atomic_json(root / 'plan.json', A._without_private(plan))
    cells = plan['_cells']
    completed = {cells[0].key, cells[1].key}
    artifact_rel = A.STAGE6_HOOK
    for cell in cells:
        cell.novel_dir.mkdir(parents=True)
        cell.cache_dir.mkdir(parents=True)
        if cell.key in completed:
            artifact = cell.novel_dir / artifact_rel
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text('{}', encoding='utf-8')

    hashes = {
        'system_sha256': 'a' * 64,
        'user_sha256': 'b' * 64,
        'combined_sha256': 'c' * 64,
    }
    monkeypatch.setattr(A, '_assert_code_frozen', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(A, '_assert_config_frozen', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        A, '_prepare_or_validate_cell', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        A, '_assert_pair_prompt_contract', lambda *_args: (hashes, hashes))
    def existing_metadata(cell):
        prefix = 'existing' if cell.key in completed else 'new'
        return {
            'generation_exit': A.X.OK,
            'generation_job_id': f'{prefix}-gen-{cell.key}',
            'check_exit': A.X.CHECK_FAILED,
            'check_job_id': f'{prefix}-check-{cell.key}',
        }

    monkeypatch.setattr(A, '_existing_job_metadata', existing_metadata)
    doctor_calls = []
    monkeypatch.setattr(A, '_run_doctor', lambda _config: doctor_calls.append(True))
    paid_calls = []
    check_calls = []

    def run_pending(cell, *, check, config):
        assert cell.key not in completed
        if check:
            check_calls.append(cell.key)
            return SimpleNamespace(
                exit_code=A.X.CHECK_FAILED, job_id=f'new-check-{cell.key}')
        paid_calls.append(cell.key)
        artifact = cell.novel_dir / artifact_rel
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text('{}', encoding='utf-8')
        return SimpleNamespace(exit_code=A.X.OK, job_id=f'new-gen-{cell.key}')

    def finish(cell, *, model, prompt_hashes, **metadata):
        return A.CellResult(
            key=cell.key, sid=cell.sid, pair=cell.pair, arm=cell.arm,
            artifact=cell.novel_dir / artifact_rel, hook_rel=artifact_rel,
            artifact_sha256='d' * 64, ledger_sha256='e' * 64,
            retries=0, prompt_tokens=1, completion_tokens=1,
            reasoning_tokens=0, wire_rows=1, model=model,
            efforts=('high',), thinking=(True,),
            system_sha256=prompt_hashes['system_sha256'],
            user_sha256=prompt_hashes['user_sha256'],
            prompt_sha256=prompt_hashes['combined_sha256'],
            generation_exit=metadata['generation_exit'],
            check_exit=metadata['check_exit'],
            generation_job_id=metadata['generation_job_id'],
            check_job_id=metadata['check_job_id'],
            initial_request_fingerprints=('fp',),
            retry_request_fingerprints=(),
        )

    monkeypatch.setattr(A, '_run_job_step', run_pending)
    monkeypatch.setattr(A, '_finish_cell', finish)

    results = A.execute(plan, resume=True)

    assert len(results) == 4
    assert paid_calls == [cell.key for cell in cells if cell.key not in completed]
    assert check_calls == paid_calls
    assert doctor_calls == [True]
    manifest = json.loads((root / 'run-manifest.json').read_text(encoding='utf-8'))
    assert manifest['experiment']['status'] == 'complete'
    seal = json.loads((root / 'COMPLETE.json').read_text(encoding='utf-8'))
    unsigned = {key: value for key, value in seal.items() if key != 'output_sha256'}
    assert seal['output_sha256'] == A._canonical_sha256(unsigned)
    assert A._write_complete_seal(plan) == seal
    seal_bytes = (root / 'COMPLETE.json').read_bytes()
    second = A.execute(plan, resume=True)
    assert len(second) == 4
    assert paid_calls == [cell.key for cell in cells if cell.key not in completed]
    assert check_calls == paid_calls and doctor_calls == [True]
    assert (root / 'COMPLETE.json').read_bytes() == seal_bytes
    manifest = json.loads((root / 'run-manifest.json').read_text(encoding='utf-8'))
    for book in manifest['books']:
        for arm in book['rounds'][0]['arms'].values():
            ledger = arm['ledger']
            assert ledger['generation_job_id'] and ledger['check_job_id']
            assert ledger['generation_exit'] == A.X.OK
            assert ledger['check_exit'] == A.X.CHECK_FAILED
    (root / 'COMPLETE.json').write_text(
        json.dumps({**seal, 'plan_sha256': 'f' * 64}), encoding='utf-8')
    with pytest.raises(RuntimeError, match='write-once'):
        A._write_complete_seal(plan)


def test_legacy_stage6_projection_is_exactly_the_five_hook_mechanical_leaves():
    source = {
        'voice': {'register_tone': '定性哨兵'},
        'surface_habits': ['定性哨兵'],
        'chapter_styles': [{'source_ordinal': 1, 'voice': {'x': '定性哨兵'}}],
        'hook_style': {'end_question': '定性哨兵'},
        'measurable': {
            'body': {'avg_sentence_chars': 99},
            'hook': {
                'chars': 100,
                'paragraphs': 8,
                'sentences': 10,
                'avg_sentence_chars': 10,
                'avg_paragraph_chars': 12,
                'paragraph_sep_chars': 2,
                'dialogue_ratio': 0.2,
                'dialogue_ratio_applicable': True,
                'quote_styles': ['“”'],
            },
        },
        '_provenance': {'code': '不消费'},
    }
    expected = {
        'chars': 100,
        'avg_sentence_chars': 10,
        'avg_paragraph_chars': 12,
        'quote_styles': ['“”'],
        'dialogue_ratio': 0.2,
    }
    assert A._historical_hook_measurement(source) == expected

    for key in ('voice', 'surface_habits', 'chapter_styles', 'hook_style',
                '_provenance'):
        edited = json.loads(json.dumps(source, ensure_ascii=False))
        edited[key] = {'任意': '变化'}
        assert A._historical_hook_measurement(edited) == expected, key
    for key in expected:
        edited = json.loads(json.dumps(source, ensure_ascii=False))
        edited['measurable']['hook'][key] = 'changed'
        assert A._historical_hook_measurement(edited) != expected, key

    assert A._historical_hook_measurement({'measurable': '旧坏形状'}) == {
        'chars': None,
        'avg_sentence_chars': None,
        'avg_paragraph_chars': None,
        'quote_styles': None,
        'dialogue_ratio': None,
    }
