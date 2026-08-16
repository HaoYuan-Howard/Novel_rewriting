from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import current_corpus_paid_preview as P


ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    ROOT / 'Test_data' / '收养三只反派幼崽后_2019402867061048656.txt',
    ROOT / 'Test_data' / '常春杜若_1668963766200045568.txt',
)


def _spec(tmp_path: Path, *, ledger: Path | None = None) -> P.PreviewSpec:
    return P.PreviewSpec.model_validate({
        'historical_ledger': ledger,
        'books': [
            {
                'sid': P.paths.derive_source_meta(source.name)[0],
                'base': tmp_path / f'isolated-{index}',
                'input_kind': 'source',
                'source': source,
            }
            for index, source in enumerate(SOURCES, 1)
        ],
    })


def test_final_topology_lists_18_dispatches_and_real_conditional_limits(tmp_path):
    plan = P.build_plan(_spec(tmp_path), token_counter=lambda _request: 123)
    aggregate = plan['aggregate']
    assert aggregate == {
        'production_cli_dispatches': 18,
        'mandatory_base_logical_requests': 136,
        'mandatory_base_ladder_wire_hard_limit': 544,
        'stage3_conditional_habit_quote_logical': 2,
        'stage3_conditional_habit_quote_wire_hard_limit': 8,
        'profile_gated_semantic_logical': 50,
        'profile_gated_semantic_wire_hard_limit': 200,
        'conditional_trim_logical': 168,
        'conditional_trim_wire_hard_limit': 420,
        'full_conditional_logical_hard_limit': 356,
        'full_conditional_wire_hard_limit': 1172,
        'full_conditional_context_token_hard_ceiling': 1_172_000_000,
    }
    assert sorted(book['chapters'] for book in plan['books']) == [17, 25]

    stage3 = [row for row in plan['dispatches'] if row['node'] == 'stage3.style']
    assert all(row['conditional_reason'].startswith('首轮唯一 HARD=habit_quote')
               for row in stage3)
    assert [(row['base_logical_requests'], row['logical_request_hard_limit'])
            for row in stage3] == [(18, 19), (26, 27)]


def test_commands_use_production_builder_and_freeze_env_without_execution(tmp_path):
    plan = P.build_plan(_spec(tmp_path), token_counter=lambda _request: 1)
    assert plan['read_only'] is True and plan['has_execution_entry'] is False
    assert plan['authorization'].startswith('not_granted')
    assert len(plan['dispatches']) == 18
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
    with pytest.raises(SystemExit):
        P._parser().parse_args(['--execute'])


def test_profiles_and_human_gates_are_explicit_before_semantic_calls(tmp_path):
    plan = P.build_plan(_spec(tmp_path), token_counter=lambda _request: 1)
    by_node = {row['node']: row for row in plan['dispatches'][:9]}
    assert by_node['stage5.semantic']['profile_dependency'] == 'secret_timeline'
    assert by_node['stage6.semantic']['profile_dependency'] == 'reference_boundary'
    assert '0 wire' in by_node['stage5.semantic']['profile_gate_behavior']
    assert any('全项 PASS' in line for line in by_node['stage4.plan']['human_gate_dependencies'])
    assert any('actor/reason' in line for line in by_node['stage2.global']['human_gate_dependencies'])
    assert any('approve' in line for line in by_node['stage7.rename']['human_gate_dependencies'])
    trim = by_node['stage5.write']
    assert trim['conditional_logical_requests'] == 17 * 4
    assert trim['conditional_wire_hard_limit'] == 17 * 10


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
    assert old['combined_wire_hard_ceiling_once_each'] == 588 + 1172
    calibration = plan['calibration_evaluation']
    assert calibration['production_cli_dispatch'] is None
    assert calibration['status'] == 'blocked_missing_final_private_inputs'
    assert calibration['minimum_cases'] == 8 * 100
    assert calibration['count_scope'] == 'frozen_minimum_800_case_campaign'
    assert calibration['exact_calls'] == 4800
    assert calibration['logical_requests'] == 4800
    assert calibration['wire_hard_cap'] == 4800
    assert calibration['wire_attempts'] == 4800
    assert calibration['minimum_campaign_exact_logical_requests'] == 800 * 2 * 3
    assert calibration['minimum_campaign_wire_hard_cap'] == 4800
    assert calibration['batch_size'] == 1
    assert calibration['fallback_rungs'] == 1
    assert calibration['transport_retries'] == 0
    assert calibration['tokens']['input_tokens'].startswith('unknown_until')
    assert calibration['included_in_current_corpus_totals'] is False
    assert calibration['included_in_corpus_totals'] is False
    assert calibration['overlaps_current_corpus_scope'] is False
    assert calibration['overlaps_existing_fc08_fc13_scope'] is False
    argv = calibration['preview_argv_template']
    assert argv[:4] == [P.sys.executable, '-X', 'utf8', '-u']
    assert argv[4] == 'tools/restricted_evaluator.py'
    assert argv.count('--run') == 3
    assert '--execute' not in argv and '--yes' not in argv

    accounting = plan['scope_accounting']
    assert accounting['existing_fc08_fc13_overlaps_current_corpus'] is False
    assert accounting['existing_plus_current_once_each'] == 588 + 1172
    assert accounting['all_three_minimum_campaign_once_each_if_later_unblocked'] == (
        588 + 1172 + 4800)


def test_dirty_source_base_or_wrong_input_scope_is_rejected(tmp_path):
    spec = _spec(tmp_path)
    dirty = spec.books[0].base
    dirty.mkdir(parents=True)
    (dirty / 'old').write_text('x', encoding='utf-8')
    with pytest.raises(ValueError, match='必须不存在或为空'):
        P.build_plan(spec, token_counter=lambda _request: 1)

    bad = spec.model_dump()
    bad['books'][1]['source'] = SOURCES[0]
    bad['books'][0]['base'] = tmp_path / 'clean-a'
    bad['books'][1]['base'] = tmp_path / 'clean-b'
    with pytest.raises(ValueError, match='派生 sid'):
        P.build_plan(
            P.PreviewSpec.model_validate(bad), token_counter=lambda _request: 1)


def test_render_says_upper_bound_unknown_price_and_no_execution(tmp_path):
    text = P.render(P.build_plan(_spec(tmp_path), token_counter=lambda _request: 1))
    assert '[18/18]' in text
    assert '全条件硬上限：logical=356 / wire=1172' in text
    assert '可达硬上限，不是预计实付' in text
    assert '已有 FC08/12/13：10 CLI run' in text
    assert 'blocked_missing_final_private_inputs' in text
    assert '8 abilities × 100 cases × 2 judges × 3 runs = 4800' in text
    assert '三块各一次=6560' in text
    assert '若最终 manifest 超过 800 case' in text
    assert 'input token=unknown' in text
    assert 'price：unknown' in text
    assert 'completion 已含 reasoning，不重复相加' in text
    assert '本工具没有执行入口' in text
