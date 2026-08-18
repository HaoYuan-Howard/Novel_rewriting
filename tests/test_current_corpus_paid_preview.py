from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel.stages.stage0.removal import _context, run_approve, run_source
from novel.stages.stage1.partition import run_partition
from tests.fixtures_ingest import body
from tools import current_corpus_paid_preview as P


ROOT = Path(__file__).resolve().parents[1]
REAL_SOURCES = (
    ROOT / 'Test_data' / '收养三只反派幼崽后_2019402867061048656.txt',
    ROOT / 'Test_data' / '常春杜若_1668963766200045568.txt',
)


def _synthetic_sources(tmp_path: Path) -> tuple[Path, Path]:
    source_dir = tmp_path / 'sources'
    source_dir.mkdir(exist_ok=True)
    sources = (
        source_dir / 'FQ_合成甲_2019402867061048656.txt',
        source_dir / 'FQ_合成乙_1668963766200045568.txt',
    )
    for source, chapters in zip(sources, (17, 25), strict=True):
        source.write_text(body(chapters), encoding='utf-8')
    return sources


def _spec(
    tmp_path: Path,
    *,
    ledger: Path | None = None,
    sources: tuple[Path, Path] | None = None,
) -> P.PreviewSpec:
    sources = sources or _synthetic_sources(tmp_path)
    return P.PreviewSpec.model_validate({
        'historical_ledger': ledger,
        'books': [
            {
                'sid': P.paths.derive_source_meta(source.name)[0],
                'base': tmp_path / f'isolated-{index}',
                'input_kind': 'source',
                'source': source,
            }
            for index, source in enumerate(sources, 1)
        ],
    })


def _shared_spec(
    tmp_path: Path,
    *,
    input_kind: str = 'source',
    sources: tuple[Path, Path] | None = None,
) -> P.PreviewSpec:
    sources = sources or _synthetic_sources(tmp_path)
    shared = tmp_path / 'NOVEL_OUTPUT'
    return P.PreviewSpec.model_validate({
        'books': [
            {
                'sid': P.paths.derive_source_meta(source.name)[0],
                'base': shared,
                'input_kind': input_kind,
                **({'source': source} if input_kind == 'source' else {}),
            }
            for source in sources
        ],
    })


@pytest.mark.corpus
@pytest.mark.skipif(
    not all(source.is_file() for source in REAL_SOURCES),
    reason='缺少现役双书 Test_data 语料',
)
def test_real_current_corpus_has_the_frozen_17_and_25_chapter_shape(tmp_path):
    plan = P.build_plan(
        _spec(tmp_path, sources=REAL_SOURCES),
        token_counter=lambda _request: 1,
    )
    assert [(book['sid'], book['chapters']) for book in plan['books']] == [
        ('2019402867061048656', 17),
        ('1668963766200045568', 25),
    ]


def test_final_topology_lists_16_dispatches_and_real_conditional_limits(tmp_path):
    plan = P.build_plan(_spec(tmp_path), token_counter=lambda _request: 123)
    aggregate = plan['aggregate']
    assert aggregate == {
        'production_cli_dispatches': 16,
        'base_nonsemantic_logical_requests': 136,
        'base_nonsemantic_ladder_wire_hard_limit': 544,
        'stage3_conditional_habit_quote_logical': 2,
        'stage3_conditional_habit_quote_wire_hard_limit': 8,
        'semantic_logical': 4,
        'semantic_wire_hard_limit': 16,
        'stage6_conditional_candidate_retry_logical': 6,
        'stage6_conditional_candidate_retry_wire_hard_limit': 6,
        'full_conditional_logical_hard_limit': 148,
        'full_conditional_wire_hard_limit': 574,
        'full_conditional_context_token_hard_ceiling': 574_000_000,
    }
    assert sorted(book['chapters'] for book in plan['books']) == [17, 25]

    stage3 = [row for row in plan['dispatches'] if row['node'] == 'stage3.style']
    assert all(row['conditional_reason'].startswith('首轮唯一 HARD=habit_quote')
               for row in stage3)
    assert [(row['base_logical_requests'], row['logical_request_hard_limit'])
            for row in stage3] == [(18, 19), (26, 27)]

    hooks = [row for row in plan['dispatches'] if row['node'] == 'stage6.hook']
    assert all('low/no-thinking' in row['conditional_reason'] for row in hooks)
    assert [(
        row['base_logical_requests'],
        row['conditional_logical_requests'],
        row['logical_request_hard_limit'],
        row['base_ladder_wire_hard_limit'],
        row['conditional_wire_hard_limit'],
        row['wire_attempt_hard_limit'],
    ) for row in hooks] == [(1, 3, 4, 4, 3, 7), (1, 3, 4, 4, 3, 7)]
    for row in hooks:
        retry = next(
            item for item in row['request_envelopes']
            if item['kind'] == 'conditional_candidate_retry')
        assert retry == {
            'kind': 'conditional_candidate_retry',
            'max_tokens': 24_000,
            'rungs': [['low', False]],
        }


def test_commands_use_production_builder_and_freeze_env_without_execution(tmp_path):
    plan = P.build_plan(_spec(tmp_path), token_counter=lambda _request: 1)
    assert plan['read_only'] is True and plan['has_execution_entry'] is False
    assert plan['authorization'].startswith('not_granted')
    assert len(plan['dispatches']) == 16
    caches = set()
    for row in plan['dispatches']:
        argv = row['production_cli_argv']
        assert argv[:4] == [P.sys.executable, '-X', 'utf8', '-u']
        assert argv[-1] == '--yes'
        assert '--execute' not in argv and '--effort' not in argv
        env = row['frozen_environment']
        assert env['DEEPSEEK_MODEL'] == 'deepseek-v4-pro'
        assert env['DEEPSEEK_THINKING'] == 'on'
        assert env['DEEPSEEK_MAX_RETRIES'] == '0'
        caches.add(env['NOVEL_CACHE_DIR'])
    assert len(caches) == 2
    write = next(row for row in plan['dispatches'] if row['node'] == 'stage5.write')
    assert write['production_cli_argv'][write['production_cli_argv'].index('--deep') + 1] == '1,2,3'
    assert '--shallow-rest' in write['production_cli_argv']
    semantic = [row for row in plan['dispatches']
                if row['node'] == 'stage6.semantic']
    assert len(semantic) == 2
    # ⚠ 断的是**没有**：`--advisory` 随自动放行门一并删除，恒真的 flag 是误导。
    assert all('--advisory' not in row['production_cli_argv'] for row in semantic)
    with pytest.raises(SystemExit):
        P._parser().parse_args(['--execute'])


def test_source_preview_allows_one_canonical_output_root_with_per_sid_isolation(tmp_path):
    spec = _shared_spec(tmp_path)
    plan = P.build_plan(spec, token_counter=lambda _request: 1)
    shared = spec.books[0].base.resolve()

    assert {Path(book['base']) for book in plan['books']} == {shared}
    assert len({Path(book['novel_dir']) for book in plan['books']}) == 2
    assert all(Path(book['novel_dir']).parent == shared for book in plan['books'])
    assert len({Path(book['cache']) for book in plan['books']}) == 2
    assert all(Path(book['cache']).parent.parent == shared / '_service'
               for book in plan['books'])
    assert all(
        Path(row['production_cli_argv'][
            row['production_cli_argv'].index('--base') + 1]).resolve() == shared
        for row in plan['dispatches']
    )
    assert not shared.exists(), '只读 source 预览不得创建共享 output 根'


def test_stage1_preview_reads_two_books_from_one_canonical_output_root(tmp_path):
    source_spec = _shared_spec(tmp_path)
    shared = source_spec.books[0].base
    for book in source_spec.books:
        assert book.source is not None
        novel_dir, removed, _ = run_source(book.source, shared)
        if removed.report.approved != 'approved':
            run_approve(
                _context(novel_dir, 'stage0.approve'),
                actor='current-corpus-preview-test',
                reason='为共享根 Stage1 只读预览构造已批准 fixture',
            )
        run_partition(_context(novel_dir, 'stage1.partition'))

    plan = P.build_plan(
        _shared_spec(tmp_path, input_kind='stage1'),
        token_counter=lambda _request: 1,
    )

    assert {book['input_kind'] for book in plan['books']} == {'stage1'}
    assert {Path(book['base']) for book in plan['books']} == {shared.resolve()}
    assert len({Path(book['novel_dir']) for book in plan['books']}) == 2
    assert len({Path(book['cache']) for book in plan['books']}) == 2


def test_book_specs_still_reject_same_sid_and_ancestor_bases(tmp_path):
    payload = _spec(tmp_path).model_dump(mode='python')
    payload['books'][1]['sid'] = payload['books'][0]['sid']
    with pytest.raises(ValueError, match='不同 sid'):
        P.PreviewSpec.model_validate(payload)

    payload = _spec(tmp_path).model_dump(mode='python')
    payload['books'][0]['base'] = tmp_path / 'NOVEL_OUTPUT'
    payload['books'][1]['base'] = tmp_path / 'NOVEL_OUTPUT' / 'nested'
    with pytest.raises(ValueError, match='祖先/子孙'):
        P.PreviewSpec.model_validate(payload)


def test_profiles_and_human_gates_are_explicit_before_semantic_calls(tmp_path):
    plan = P.build_plan(_spec(tmp_path), token_counter=lambda _request: 1)
    by_node = {row['node']: row for row in plan['dispatches'][:8]}
    assert by_node['stage6.semantic']['semantic_ability'] == 'reference_boundary'
    assert 'UNVER' in by_node['stage6.semantic']['semantic_gate_behavior']
    assert '没有自动放行门' in by_node['stage6.semantic']['semantic_gate_behavior']
    # `stage5.semantic` 于 2026-08-17 整节点下线 ⇒ 付费面上只剩 stage6 一条语义 dispatch。
    assert 'stage5.semantic' not in by_node
    assert any('全项 PASS' in line for line in by_node['stage4.plan']['human_gate_dependencies'])
    assert any('actor/reason' in line for line in by_node['stage2.global']['human_gate_dependencies'])
    assert any('approve' in line for line in by_node['stage7.rename']['human_gate_dependencies'])
    global_version = P.artifact_spec(P.STAGE2_GLOBAL_GROUNDING).current_version
    assert any(
        f'v{global_version} 产物' in line
        for line in by_node['stage2.blueprint']['human_gate_dependencies']
    )
    # ⚠ 收缩臂整臂下线后 stage5.write **没有条件回路**了。这一条断的是 0，不是「忘了写」：
    #   非零就说明有人把第二条付费回路接了回来而预览没跟上。
    write_row = by_node['stage5.write']
    assert write_row['conditional_logical_requests'] == 0
    assert write_row['conditional_wire_hard_limit'] == 0
    assert write_row['conditional_reason'] is None


def test_only_global_prompt_is_exact_before_upstream_generation(tmp_path):
    plan = P.build_plan(_spec(tmp_path), token_counter=lambda _request: 321)
    estimates = plan['token_estimate']['stage2_global_exact_before_generation']
    assert set(estimates) == {
        '2019402867061048656', '1668963766200045568'}
    for estimate in estimates.values():
        assert estimate['status'] == 'exact_for_current_source_or_stage1'
        assert estimate['first_success_input_tokens'] == 321
        assert estimate['exhausted_ladder_input_tokens'] == 4 * 321
        assert estimate['completion_tokens_ladder_upper_bound'] == 4 * 96_000
        assert estimate['completion_includes_reasoning'] is True
    assert plan['token_estimate']['downstream_prompt_tokens'] == 'unknown'
    assert '不含费率' in plan['token_estimate']['price']


def test_historical_ledger_is_hashed_and_never_double_adds_reasoning(tmp_path):
    ledger = tmp_path / 'old-ledger.jsonl'
    rows = [
        {
            'sid': '2019402867061048656', 'node': 'stage3.style',
            'cached': True, 'outcome': 'ok',
            'prompt_tokens': 10, 'completion_tokens': 100, 'reasoning_tokens': 80,
        },
        {
            'sid': '2019402867061048656', 'node': 'stage3.style',
            'cached': False, 'outcome': 'error',
            'prompt_tokens': None, 'completion_tokens': None, 'reasoning_tokens': None,
        },
    ]
    ledger.write_text(
        '\n'.join([json.dumps(rows[0]), '{half', json.dumps(rows[1])]) + '\n',
        encoding='utf-8')
    observed = P.historical_observations(
        ledger, {'2019402867061048656', '1668963766200045568'})
    assert observed['available'] is True
    assert len(observed['sha256']) == 64
    assert observed['malformed_rows_skipped'] == 1
    group = observed['groups'][0]
    assert group['prompt_tokens']['sum'] == 10
    assert group['completion_tokens']['sum'] == 100
    assert group['reasoning_tokens_diagnostic_subset']['sum'] == 80
    assert group['prompt_plus_completion_observed_sum'] == 110
    assert group['completion_includes_reasoning'] is True


def test_existing_experiments_and_minimum_evaluator_are_separate_scopes(tmp_path):
    plan = P.build_plan(_spec(tmp_path), token_counter=lambda _request: 1)
    old = plan['existing_authorized_boundary']
    assert old['cli_runs'] == 10
    assert old['fc13_base_ladder_limit'] == 322
    assert old['fc13_conditional_habit_quote_limit'] == 14
    assert old['fc13_hard_limit'] == 336
    assert old['external_hard_limit'] == 588
    assert old['included_in_new_corpus_totals'] is False
    assert old['overlaps_current_corpus_scope'] is False
    assert old['combined_wire_hard_ceiling_once_each'] == 588 + 574

    accounting = plan['scope_accounting']
    assert accounting['existing_fc08_fc13_overlaps_current_corpus'] is False
    assert accounting['existing_plus_current_once_each'] == 588 + 574
    # ⚠ 这里曾有第三块：仍 blocked 的「最低 evaluator 标定 campaign（4800 次）」。
    #   evaluator profile 装置已被裁决删除，那场战役不会发生 ⇒ 预览不再为它报预算。
    #   断言写成「这两个键不许回来」，而不是删掉了事：一个不存在的计划出现在预算里，
    #   会被读成「还差这一步」。
    assert 'calibration_evaluation' not in plan
    assert 'all_three_minimum_campaign_once_each_if_later_unblocked' not in accounting


def test_dirty_source_base_or_wrong_input_scope_is_rejected(tmp_path):
    spec = _spec(tmp_path)
    dirty = spec.books[0].base
    dirty.mkdir(parents=True)
    (dirty / 'old').write_text('x', encoding='utf-8')
    with pytest.raises(ValueError, match='必须不存在或为空'):
        P.build_plan(spec, token_counter=lambda _request: 1)

    bad = spec.model_dump()
    bad['books'][1]['source'] = spec.books[0].source
    bad['books'][0]['base'] = tmp_path / 'clean-a'
    bad['books'][1]['base'] = tmp_path / 'clean-b'
    with pytest.raises(ValueError, match='派生 sid'):
        P.build_plan(
            P.PreviewSpec.model_validate(bad), token_counter=lambda _request: 1)


def test_render_says_upper_bound_unknown_price_and_no_execution(tmp_path):
    text = P.render(P.build_plan(_spec(tmp_path), token_counter=lambda _request: 1))
    assert '[16/16]' in text
    assert 'Stage6 条件候选定向重试：logical=6 / wire 上限=6' in text
    assert '全条件硬上限：logical=148 / wire=574' in text
    assert '可达硬上限，不是预计实付' in text
    assert '已有 FC08/12/13：10 CLI run' in text
    assert '那场战役不会发生' in text
    assert '4800' not in text
    # ⚠ 「不知道」必须一直写在渲染里：`unknown` 消失就等于把「没算过」读成「不需要算」。
    assert '其余 prompt token：unknown' in text
    assert 'price：unknown' in text
    assert 'completion 已含 reasoning，不重复相加' in text
    assert '最终 schema v2 上游' not in text
    assert '本工具没有执行入口' in text
