from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import stage5_ab_evidence as E


def _quality_stats() -> dict:
    old_dimensions = {
        'fact_fidelity': 3.0,
        'narrative_restructuring': 2.0,
        'causal_continuity': 3.0,
        'character_person_dialogue_style': 3.0,
        'end_delivery': 3.0,
        'readability': 3.0,
    }
    new_dimensions = {
        'fact_fidelity': 3.0,
        'narrative_restructuring': 2.5,
        'causal_continuity': 3.4,
        'character_person_dialogue_style': 3.4,
        'end_delivery': 3.4,
        'readability': 3.3,
    }
    return {
        'chapter_count': 23,
        'arms': {
            'old': {
                'dimensions': old_dimensions,
                'nonfact_composite': sum(
                    old_dimensions[key] for key in E.NONFACT_DIMENSIONS) / 5,
                'major_fact_errors': 2,
                'minor_fact_issues': 5,
            },
            'new': {
                'dimensions': new_dimensions,
                # 旧 2.8；新 3.4，远高于精确边界，单测另把它钉到 +0.30。
                'nonfact_composite': sum(
                    new_dimensions[key] for key in E.NONFACT_DIMENSIONS) / 5,
                'major_fact_errors': 0,
                'minor_fact_issues': 5,
            },
        },
        'chapter_preference': {
            'ties': 8, 'non_ties': 15, 'new_wins': 9, 'old_wins': 6,
        },
        'whole_book_winner': 'new',
    }


def test_quality_gate_accepts_exact_thresholds_and_rejects_each_side():
    stats = _quality_stats()
    # 精确钉住两个连续阈值；其余单项仍不低于旧臂 0.10。
    stats['arms']['new']['dimensions']['narrative_restructuring'] = 2.5
    stats['arms']['new']['nonfact_composite'] = (
        stats['arms']['old']['nonfact_composite'] + .30)
    gate = E.quality_gate_for_book(stats)
    assert gate['pass']
    assert all(gate['checks'].values())

    below_restructure = json.loads(json.dumps(stats))
    below_restructure['arms']['new']['dimensions']['narrative_restructuring'] = 2.499
    assert not E.quality_gate_for_book(below_restructure)['checks'][
        'restructuring_gain_at_least_0_50']

    more_minor = json.loads(json.dumps(stats))
    more_minor['arms']['new']['minor_fact_issues'] = 5.001
    assert not E.quality_gate_for_book(more_minor)['checks'][
        'new_minor_fact_issues_not_more']

    low_win_rate = json.loads(json.dumps(stats))
    low_win_rate['chapter_preference'].update(new_wins=8, old_wins=7)
    assert not E.quality_gate_for_book(low_win_rate)['checks'][
        'new_win_rate_at_least_60pct']

    too_many_ties = json.loads(json.dumps(stats))
    too_many_ties['chapter_preference'].update(
        ties=12, non_ties=11, new_wins=7, old_wins=4)
    assert not E.quality_gate_for_book(too_many_ties)['checks'][
        'at_least_half_chapters_non_tie']


def test_cost_and_speed_bonus_accept_exact_five_and_ten_percent_boundaries():
    books = {
        'a': {
            'old': {'calls': 10, 'prompt_tokens': 1000, 'completion_tokens': 500,
                    'span_s': 100, 'latency_p90_ms': 1000,
                    'latency_ms_samples': [900, 1000]},
            'new': {'calls': 10, 'prompt_tokens': 950, 'completion_tokens': 500,
                    'span_s': 90, 'latency_p90_ms': 1000,
                    'latency_ms_samples': [900, 1000]},
        },
        'b': {
            'old': {'calls': 20, 'prompt_tokens': 1000, 'completion_tokens': 500,
                    'span_s': 100, 'latency_p90_ms': 1200,
                    'latency_ms_samples': [1100, 1200]},
            'new': {'calls': 20, 'prompt_tokens': 950, 'completion_tokens': 500,
                    'span_s': 90, 'latency_p90_ms': 1200,
                    'latency_ms_samples': [1100, 1200]},
        },
    }
    verdict = E.cost_speed_bonus(books)
    assert verdict['cost']['qualifies']
    assert verdict['cost']['total_reduction_ratio']['prompt_tokens'] == pytest.approx(.05)
    assert verdict['speed']['qualifies']
    assert verdict['speed']['total_span_reduction_ratio'] == pytest.approx(.10)

    books['b']['new']['latency_p90_ms'] = 1201
    books['b']['new']['latency_ms_samples'] = [1100, 1201]
    assert not E.cost_speed_bonus(books)['speed']['qualifies']


def test_random_swap_is_reproducible_per_scope_and_deblinds_exactly():
    seed = 'fixed-seed'
    whole = E._candidate_map(seed, 'book', 'whole', ('X', 'Y'))
    assert whole == E._candidate_map(seed, 'book', 'whole', ('X', 'Y'))
    assert set(whole.values()) == {'old', 'new'}
    assert E.deblind_choice('X', whole) == whole['X']
    assert E.deblind_choice('tie', whole) == 'tie'

    # 作用域参与哈希：寻找一个确定存在的章节换位差，证明不是全书只掷一次硬币。
    chapter_maps = [
        E._candidate_map(seed, 'book', f'chapter:{ordinal}', ('P', 'Q'))
        for ordinal in range(1, 50)
    ]
    assert len({tuple(sorted(mapping.items())) for mapping in chapter_maps}) == 2


def test_quality_aggregation_deblinds_independent_chapter_swaps():
    config = {'books': [{'label': 'book', 'expected_chapters': 2}]}
    blind_map = {
        'book': {
            'whole': {'X': 'new', 'Y': 'old'},
            'chapters': {
                '1': {'P': 'new', 'Q': 'old'},
                '2': {'P': 'old', 'Q': 'new'},
            },
        },
    }

    def candidate(score: int) -> dict:
        return {
            'scores': {dimension: score for dimension in E.DIMENSIONS},
            'major_fact_errors': [], 'minor_fact_issues': [],
        }

    score = {
        'reviewer': 'r1',
        'books': {'book': {
            'whole_book': {'choice': 'X'},
            'chapters': {
                '1': {'preference': 'P', 'candidates': {
                    'P': candidate(3), 'Q': candidate(2)}},
                '2': {'preference': 'Q', 'candidates': {
                    'P': candidate(2), 'Q': candidate(3)}},
            },
        }},
    }
    second = json.loads(json.dumps(score))
    second['reviewer'] = 'r2'
    result = E.aggregate_quality(config, blind_map, [score, second], None)
    assert result['pass']
    assert result['books']['book']['arms']['new']['dimensions']['readability'] == 3
    assert result['books']['book']['arms']['old']['dimensions']['readability'] == 2
    assert result['books']['book']['chapter_preference']['new_wins'] == 2
    assert result['books']['book']['whole_book_winner'] == 'new'


def test_nearest_rank_p90_and_concurrent_span_use_wire_response_window(tmp_path: Path):
    service = tmp_path / '_service'
    service.mkdir()
    rows = []
    for index, latency in enumerate(range(100, 1100, 100), 1):
        rows.append({
            'at': f'2026-08-24T00:00:{index:02d}+00:00',
            'sid': '42', 'node': 'stage5.write', 'model': 'm',
            'cached': False, 'prompt_tokens': 10, 'completion_tokens': 20,
            'reasoning_tokens': 5, 'prompt_cache_hit_tokens': 0,
            'prompt_cache_miss_tokens': 10, 'outcome': 'ok', 'attempt': 1,
            'reasoning_effort': 'high', 'thinking': True, 'latency_ms': latency,
        })
    (service / 'ledger.jsonl').write_text(
        ''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    summary = E.ledger_summary(tmp_path, '42', accepted_chapters=10)
    assert summary['latency_p90_ms'] == 900
    # 最早请求约 00:00:00.9，最后响应 00:00:10；不是 Σ latency=5.5s。
    assert summary['span_s'] == pytest.approx(9.1)
    assert summary['completion_tokens'] == 200
    assert summary['reasoning_tokens_diagnostic_subset'] == 50


def test_request_identity_is_bound_to_accepted_wire_attempt(tmp_path: Path):
    service = tmp_path / '_service'
    service.mkdir()
    accepted_fp = 'a' * 64
    ledger = {
        'sid': '42', 'node': 'stage5.write', 'chapter': 1,
        'attempt_id': 'a1', 'request_fingerprint': accepted_fp,
    }
    (service / 'ledger.jsonl').write_text(
        json.dumps(ledger) + '\n', encoding='utf-8')
    audit = {
        'sid': '42', 'node': 'stage5.write', 'chapter': 1,
        'kind': 'gate_decision', 'decision': 'accepted', 'attempt_id': 'a1',
    }
    (service / 'llm_audit.jsonl').write_text(
        json.dumps(audit) + '\n', encoding='utf-8')
    chapter = SimpleNamespace(provenance=SimpleNamespace(
        request_fingerprint=accepted_fp))
    assert E.request_identity_mismatches(tmp_path, '42', {1: chapter}) == []

    chapter.provenance.request_fingerprint = 'b' * 64
    assert E.request_identity_mismatches(tmp_path, '42', {1: chapter}) == [1]


def test_blind_output_refuses_unignored_git_directory_and_foreign_nonempty_dir(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    git_root = tmp_path / 'repo'
    git_root.mkdir()
    (git_root / '.git').mkdir()
    target = git_root / 'evidence'

    class Result:
        returncode = 1

    monkeypatch.setattr(E.subprocess, 'run', lambda *args, **kwargs: Result())
    with pytest.raises(E.EvidenceError, match='没有被 ignore'):
        E.ensure_blind_output_safe(target)

    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'foreign.txt').write_text('x', encoding='utf-8')
    with pytest.raises(E.EvidenceError, match='不是本工具创建'):
        E.ensure_blind_output_safe(outside)
