from __future__ import annotations

from contracts.invariants import INBOX_KIND, STAGES
from tools import ab_compare as A


def _row(node: str, **usage):
    return {
        'node': node, 'model': 'model-x', 'cached': False,
        'attempt': 1, 'outcome': 'ok',
        'purpose': usage.pop('purpose', node),
        'reasoning_effort': usage.pop('reasoning_effort', 'high'),
        'thinking': usage.pop('thinking', True),
        'prompt_tokens': usage.pop('prompt_tokens', 0),
        'completion_tokens': usage.pop('completion_tokens', 0),
        'reasoning_tokens': usage.pop('reasoning_tokens', 0),
        **usage,
    }


def test_commander_cost_is_a_role_group_without_a_fake_node():
    commander = _row('placeholder', prompt_tokens=12, completion_tokens=3)
    commander.update({
        'role': 'commander',
        'node': None,
        'purpose': 'commander',
        'model': 'gpt-5.6-sol',
    })

    costs = A.cost_table([commander, _row('stage2.global')])
    wall = A.wall_table([commander, _row('stage2.global')], 600)

    assert sorted(costs) == ['role:commander', 'stage2.global']
    assert costs['role:commander']['calls_made'] == 1
    assert costs['role:commander']['models'] == ['gpt-5.6-sol']
    assert sorted(wall) == ['role:commander', 'stage2.global']

    arm = {
        'base': 'synthetic', 'cost': costs, 'wall': wall,
        'quality': {stage: {'unreachable': 'synthetic'} for stage in STAGES},
        'inbox': {'counts': {kind: 0 for kind in INBOX_KIND},
                  'total': 0, 'wontfix': 0},
        'semantic': {}, 'book': {'book': None},
    }
    rendered = A.render('sid', {'arm': arm}, None)
    assert 'role:commander·指挥' in rendered
    assert '| **指挥合计** | calls_made | 1 |' in rendered


def test_provider_cache_usage_preserves_null_and_explicit_zero():
    costs = A.cost_table([
        _row('null-node', prompt_tokens=12,
             prompt_cache_hit_tokens=None, prompt_cache_miss_tokens=None),
        _row('zero-node', prompt_tokens=0,
             prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=0),
        _row('measured-node', prompt_tokens=100,
             prompt_cache_hit_tokens=75, prompt_cache_miss_tokens=25),
    ])

    assert costs['null-node']['prompt_cache_hit_null_rows'] == 1
    assert costs['null-node']['prompt_cache_hit_rate'] is None
    assert A._cache_token_cell(
        costs['null-node'], 'prompt_cache_hit_tokens') == '—（全部 null）'
    assert costs['zero-node']['prompt_cache_hit_null_rows'] == 0
    assert A._cache_token_cell(
        costs['zero-node'], 'prompt_cache_hit_tokens') == '0'
    assert A._cache_rate_cell(costs['zero-node']).startswith('n/a')
    assert costs['measured-node']['prompt_cache_hit_rate'] == 0.75


def test_money_uses_cache_rates_only_when_usage_is_complete():
    measured = A.cost_table([
        _row('stage3.style', prompt_tokens=1_000_000,
             completion_tokens=100_000,
             prompt_cache_hit_tokens=750_000,
             prompt_cache_miss_tokens=250_000),
    ])
    rates = {'per_mtok': {'model-x': {
        'input': 2.0, 'input_cache_hit': 0.2,
        'input_cache_miss': 2.0, 'output': 3.0,
    }}}
    assert A.money_of(measured, rates)['stage3.style']['model-x'] == 0.95

    incomplete = A.cost_table([
        _row('stage3.style', prompt_tokens=1_000_000,
             completion_tokens=100_000,
             prompt_cache_hit_tokens=None,
             prompt_cache_miss_tokens=None),
    ])
    assert A.money_of(incomplete, rates)['stage3.style']['model-x'] == 2.3


def test_effort_fallback_and_errors_are_exposed_without_double_counting_reasoning():
    costs = A.cost_table([
        _row('stage3.style', purpose='chapter.1', reasoning_effort='high',
             completion_tokens=100, reasoning_tokens=80),
        _row('stage3.style', purpose='chapter.1', reasoning_effort='medium',
             completion_tokens=50, reasoning_tokens=30),
        _row('stage3.style', purpose='chapter.2', reasoning_effort='high',
             outcome='error', error_kind='timeout'),
    ])
    cost = costs['stage3.style']
    assert cost['efforts'] == {'high': 2, 'medium': 1}
    assert cost['fallback_purposes'] == 1 and cost['fallback_steps'] == 1
    assert cost['errors'] == {'timeout': 1}
    assert cost['completion_tokens'] == 150
    assert cost['reasoning_tokens'] == 110
    assert cost['completion_includes_reasoning'] is True
    assert cost['conditional_surface_repair_calls'] == 0

    rates = {'per_mtok': {'model-x': {'input': 0, 'output': 1}}}
    # output price is charged on completion=150, not completion+reasoning=260.
    assert A.money_of(costs, rates)['stage3.style']['model-x'] == 0.0001


def test_stage3_conditional_repair_is_visible_as_reachable_nonbase_work():
    cost = A.cost_table([
        _row('stage3.style', purpose='stage3.style.surface_habits_targeted_repair'),
        _row('stage3.style', purpose='stage3.style.surface_habits_targeted_repair',
             reasoning_effort='medium'),
    ])['stage3.style']
    assert cost['conditional_surface_repair_calls'] == 2


def test_provider_cache_partition_mismatch_is_not_silently_accepted():
    cost = A.cost_table([
        _row('stage3.style', prompt_tokens=1_000_000,
             prompt_cache_hit_tokens=600_000, prompt_cache_miss_tokens=300_000),
    ])['stage3.style']
    assert cost['prompt_cache_mismatch_rows'] == 1
    rates = {'per_mtok': {'model-x': {
        'input': 2, 'input_cache_hit': 0.2, 'input_cache_miss': 2,
        'output': 0,
    }}}
    assert A.money_of({'stage3.style': cost}, rates)['stage3.style']['model-x'] == 2


def test_render_exposes_cache_rows_and_every_inbox_kind():
    cost = A.cost_table([
        _row('stage3.style', prompt_tokens=100,
             prompt_cache_hit_tokens=75, prompt_cache_miss_tokens=25),
    ])
    arm = {
        'base': 'synthetic',
        'cost': cost,
        'wall': {'stage3.style': {
            'segments': 1, 'span_s': 0.1, 'sum_latency_s': 0.1}},
        'quality': {stage: {'unreachable': 'synthetic'} for stage in STAGES},
        'inbox': {'counts': {kind: 0 for kind in INBOX_KIND},
                  'total': 0, 'wontfix': 0},
        'semantic': {}, 'book': {'book': None},
    }
    text = A.render('sid', {'arm': arm}, None)
    assert '供应商 prompt cache 命中率' in text and '75.0%' in text
    assert '| repairable | 0 |' in text
    assert '| verification | 0 |' in text
    assert 'effort（wire 行）' in text
    assert 'completion_tokens` 已包含 `reasoning_tokens`' in text
