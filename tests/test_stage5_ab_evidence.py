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


def test_request_identity_is_bound_to_accepted_wire_attempt_and_request_preimage():
    accepted_fp = 'a' * 64
    chapter = SimpleNamespace(provenance=SimpleNamespace(
        request_fingerprint=accepted_fp))
    requests = {'1': {'request_fingerprints': {'high:true': accepted_fp}}}
    assert E.request_identity_mismatches(
        {1: chapter}, {'1': {
            'request_fingerprint': accepted_fp,
            'reasoning_effort': 'high', 'thinking': True}}, requests) == []

    chapter.provenance.request_fingerprint = 'b' * 64
    assert E.request_identity_mismatches(
        {1: chapter}, {'1': {
            'request_fingerprint': accepted_fp,
            'reasoning_effort': 'high', 'thinking': True}}, requests) == [1]


def test_request_identity_cannot_set_match_a_fingerprint_from_the_wrong_rung():
    high_fp, low_fp = 'a' * 64, 'b' * 64
    chapter = SimpleNamespace(provenance=SimpleNamespace(request_fingerprint=low_fp))
    expected = {'1': {'request_fingerprints': {
        'high:true': high_fp, 'low:true': low_fp, 'low:false': 'c' * 64}}}
    accepted = {'1': {
        'request_fingerprint': low_fp,
        'reasoning_effort': 'high', 'thinking': True,
    }}
    assert E.request_identity_mismatches({1: chapter}, accepted, expected) == [1]


def test_blind_output_refuses_unignored_git_directory_and_foreign_nonempty_dir(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    git_root = tmp_path / 'repo'
    git_root.mkdir()
    (git_root / '.git').mkdir()
    target = git_root / 'evidence'

    class Result:
        returncode = 1
        stdout = ''

    monkeypatch.setattr(E.subprocess, 'run', lambda *args, **kwargs: Result())
    with pytest.raises(E.EvidenceError, match='没有被 ignore'):
        E.ensure_blind_output_safe(target)

    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'foreign.txt').write_text('x', encoding='utf-8')
    with pytest.raises(E.EvidenceError, match='不是本工具创建'):
        E.ensure_blind_output_safe(outside)


def test_blind_output_refuses_tracked_descendant_even_when_directory_is_ignored(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    git_root = tmp_path / 'repo'
    git_root.mkdir()
    (git_root / '.git').mkdir()
    target = git_root / 'ignored-evidence'

    def fake_run(argv, **_kwargs):
        if 'check-ignore' in argv:
            return SimpleNamespace(returncode=0, stdout='')
        assert 'ls-files' in argv
        return SimpleNamespace(
            returncode=0, stdout='ignored-evidence/already-tracked.json\n')

    monkeypatch.setattr(E.subprocess, 'run', fake_run)
    with pytest.raises(E.EvidenceError, match='已被 Git tracked'):
        E.ensure_blind_output_safe(target)


def test_runtime_stage5_registry_is_exact_and_closes_on_drift(monkeypatch):
    assert E.require_stage5_registry() == E.EXPECTED_STAGE5_REGISTRY
    real = E.REGISTRY.of_stage('stage5')
    monkeypatch.setattr(E.REGISTRY, 'of_stage', lambda _stage: real[:-1])
    with pytest.raises(E.EvidenceError, match='运行时注册表漂移'):
        E.require_stage5_registry()


def test_ab_capture_requires_a_clean_full_commit_identity():
    assert E._require_clean_commit('a' * 40, 'code') == 'a' * 40
    for invalid in ('unknown', 'a' * 40 + '-dirty.deadbeefdeadbeef', 'a' * 39):
        with pytest.raises(E.EvidenceError, match='clean worktree'):
            E._require_clean_commit(invalid, 'code')


def test_stage0_4_hashes_cover_every_regular_file_and_detect_extra_history(tmp_path: Path):
    novel = tmp_path / 'book'
    for number in range(5):
        (novel / f'stage{number}').mkdir(parents=True)
        (novel / f'stage{number}' / 'live.json').write_text(
            f'{number}', encoding='utf-8')
    extra = novel / 'stage4' / '_rounds' / 'plan_edit.r1.json'
    extra.parent.mkdir()
    extra.write_text('history', encoding='utf-8')
    hashes = E._stage0_4_tree_hashes(novel)
    assert 'stage4/_rounds/plan_edit.r1.json' in hashes
    before = E._hash_set(hashes)
    extra.write_text('changed', encoding='utf-8')
    assert E._hash_set(E._stage0_4_tree_hashes(novel)) != before


def _capture_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                    *, missing_rel: str | None = None):
    sid = E.EXPECTED_BOOKS['idol']['sid']
    base = tmp_path / 'base'
    novel = base / f'book_{sid}'
    for number in range(5):
        (novel / f'stage{number}').mkdir(parents=True)
    for rel in E.FROZEN_UPSTREAM_RELS:
        if rel == missing_rel:
            continue
        destination = novel / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text('{}', encoding='utf-8')
    cache = tmp_path / 'cache'
    preview = tmp_path / 'preview.json'
    argv = E.orchestrator_nodes.build_argv(
        'stage5.write', sid, ['--write-contract', 'event-list-v1'],
        base=base.resolve())
    preview.write_bytes(E._json_bytes({
        'schema': 'novel-operator-step-evidence-v1',
        'steps': [{
            'argv': argv, 'paid': True, 'writes': 'stage5/chNN.json',
            'label': 'stage5 全书闭卷写作', 'note': None,
        }],
    }, pretty=True))
    monkeypatch.setattr(E.prov, 'code_version', lambda: 'a' * 40)
    monkeypatch.setattr(E.runtime_config, 'load_config', lambda: SimpleNamespace(
        model=E.EXPERIMENT_RUN['model'], thinking_mode='on',
        max_retries=E.EXPERIMENT_RUN['max_retries'],
        backoff_base=E.EXPERIMENT_RUN['backoff_base'],
        concurrency=E.EXPERIMENT_RUN['concurrency'],
        timeout=E.EXPERIMENT_RUN['timeout'],
        base_url=E.runtime_config.DEFAULT_BASE_URL, cache_dir=cache,
    ))
    return sid, base, novel, cache, preview, tmp_path / 'run.json'


@pytest.mark.parametrize('missing_rel', E.FROZEN_UPSTREAM_RELS)
def test_capture_refuses_each_missing_required_upstream_before_any_ledger_row(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing_rel: str):
    sid, base, _novel, cache, preview, manifest = _capture_inputs(
        tmp_path, monkeypatch, missing_rel=missing_rel)
    with pytest.raises(E.EvidenceError, match='缺少产物'):
        E.capture_run_manifest(
            destination=manifest, book_label='idol', sid=sid, arm='old',
            base=base, contract='event-list-v1', cache_dir=cache,
            preview_argv_path=preview, experiment_round=1,
            previous_manifest_sha256=None, previous_manifest_path=None)
    assert not E.ledger_path(base).exists()


def test_capture_does_not_require_review_decisions_and_freezes_required_bytes(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    sid, base, novel, cache, preview, manifest = _capture_inputs(tmp_path, monkeypatch)
    monkeypatch.setattr(E, 'readonly_ctx', lambda *_args, **_kwargs: object())
    monkeypatch.setattr(E, 'ArtifactStore', lambda _ctx: object())
    fake_book = SimpleNamespace(chapters={value: {} for value in range(1, 19)})
    monkeypatch.setattr(E.stage5_review, 'load_book', lambda *_args: fake_book)
    monkeypatch.setattr(
        E, 'expected_request_evidence',
        lambda *_args: {str(value): {} for value in range(1, 19)})
    result = E.capture_run_manifest(
        destination=manifest, book_label='idol', sid=sid, arm='old',
        base=base, contract='event-list-v1', cache_dir=cache,
        preview_argv_path=preview, experiment_round=1,
        previous_manifest_sha256=None, previous_manifest_path=None)
    assert result['request_chapters'] == 18
    assert not (novel / 'stage2/global_review_decisions.json').exists()
    captured = json.loads(manifest.read_text(encoding='utf-8'))
    assert set(captured['start_consumed_upstream_hashes']) == set(E.FROZEN_UPSTREAM_RELS)

    changed = novel / E.FROZEN_UPSTREAM_RELS[0]
    changed.write_text('{"changed":true}', encoding='utf-8')
    with pytest.raises(E.EvidenceError, match='pre-run'):
        E._require_start_upstream_unchanged(
            captured, E._stage0_4_tree_hashes(novel),
            E._artifact_hashes(novel, E.FROZEN_UPSTREAM_RELS),
            book='idol', arm='old')


def test_transport_unknown_usage_or_latency_only_disables_cost_speed_bonus(tmp_path: Path):
    service = tmp_path / '_service'
    service.mkdir()
    common = {
        'sid': '42', 'node': 'stage5.write', 'model': 'm', 'cached': False,
        'reasoning_effort': 'high', 'thinking': True, 'latency_ms': 10,
        'attempt_id': 'x', 'purpose': 'chapter.1', 'chapter': 1,
    }
    rows = [
        {**common, 'at': '2026-08-24T00:00:01+00:00', 'outcome': 'error',
         'latency_ms': None,
         'attempt': 1, 'prompt_tokens': None, 'completion_tokens': None,
         'reasoning_tokens': None, 'prompt_cache_hit_tokens': None,
         'prompt_cache_miss_tokens': None, 'error_kind': 'timeout', 'will_retry': True},
        {**common, 'attempt_id': 'y', 'at': '2026-08-24T00:00:02+00:00',
         'outcome': 'ok', 'attempt': 2, 'prompt_tokens': 11, 'completion_tokens': 22,
         'reasoning_tokens': 7, 'prompt_cache_hit_tokens': 0,
         'prompt_cache_miss_tokens': 11, 'will_retry': False},
    ]
    (service / 'ledger.jsonl').write_text(
        ''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    summary = E.ledger_summary(tmp_path, '42', 1)
    assert summary['calls'] == 2 and summary['failed_calls'] == 1
    assert summary['prompt_tokens'] is None and summary['completion_tokens'] is None
    assert summary['unknown_prompt_rows'] == 0
    assert summary['unreported_transport_error_prompt_rows'] == 1
    assert not summary['latency_complete']
    assert summary['span_s'] is None and summary['latency_p90_ms'] is None
    bonus = E.cost_speed_bonus({'book': {'old': summary, 'new': summary}})
    assert not bonus['cost']['qualifies'] and not bonus['speed']['qualifies']

    rows[1]['prompt_tokens'] = None
    (service / 'ledger.jsonl').write_text(
        ''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    broken = E.ledger_summary(tmp_path, '42', 1)
    assert broken['prompt_tokens'] is None and broken['unknown_prompt_rows'] == 1


def test_session_window_includes_equal_second_and_scans_wire_cache_and_audit(
        tmp_path: Path):
    service = tmp_path / '_service'
    service.mkdir()
    start_at = '1970-01-01T00:01:40+00:00'
    end_at = '1970-01-01T00:01:41+00:00'
    common = {
        'sid': '42', 'node': 'stage5.write', 'model': 'm',
        'reasoning_effort': 'high', 'thinking': True,
        'prompt_tokens': 1, 'completion_tokens': 1, 'reasoning_tokens': 0,
        'prompt_cache_hit_tokens': 0, 'prompt_cache_miss_tokens': 1,
        'outcome': 'ok', 'attempt': 1, 'latency_ms': 1,
    }
    wire = {**common, 'at': start_at, 'cached': False, 'attempt_id': 'wire'}
    cached = {**common, 'at': end_at, 'cached': True, 'attempt_id': 'cache'}
    ledger_file = service / 'ledger.jsonl'
    audit_file = service / 'llm_audit.jsonl'

    def write(rows, audits):
        ledger_file.write_text(
            ''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
        audit_file.write_text(
            ''.join(json.dumps(row) + '\n' for row in audits), encoding='utf-8')

    audits = [{
        'at': start_at, 'sid': '42', 'node': 'stage5.write',
        'kind': 'gate_decision', 'decision': 'rejected'}]
    write([wire, cached], audits)
    summary = E.ledger_summary(
        tmp_path, '42', 1, session_window=(100, 101))
    assert summary['calls'] == 1 and summary['local_cache_calls'] == 1

    for bad_rows, bad_audits, match in (
            ([wire, {**cached, 'at': '1970-01-01T00:01:42+00:00'}], audits,
             'ledger.*窗口'),
            ([{**wire, 'at': None}, cached], audits, 'ledger session row'),
            ([wire, cached], [{**audits[0], 'at': '1970-01-01T00:01:42+00:00'}],
             'llm_audit.*窗口'),
            ([wire, cached], [{**audits[0], 'at': None}], 'llm_audit session row'),
            ([{**wire, 'at': '1970-01-01T00:01:40.001+00:00'}, cached], audits,
             '整秒')):
        write(bad_rows, bad_audits)
        with pytest.raises(E.EvidenceError, match=match):
            E.ledger_summary(tmp_path, '42', 1, session_window=(100, 101))


def test_stage5_write_window_binds_both_beacons_to_preview_evidence_hash():
    digest = 'a' * 64
    note = f'operator_evidence_sha256={digest}'
    rows = [
        {'ts': 100, 'sid': '42', 'node': 'stage5.write', 'phase': 'start',
         'exit_code': None, 'note': note},
        {'ts': 101, 'sid': '42', 'node': 'stage5.write', 'phase': 'end',
         'exit_code': 1, 'note': note},
    ]
    assert E._stage5_write_window(rows, '42', digest)[0] == (100, 101)
    rows[1]['note'] = f'operator_evidence_sha256={"b" * 64}'
    with pytest.raises(E.EvidenceError, match='同一 preview evidence'):
        E._stage5_write_window(rows, '42', digest)


def _fact(issue_id: str, origin: str = 'arm_induced') -> dict:
    row = {'issue_id': issue_id, 'origin': origin, 'detail': '事实偏差'}
    for prefix in ('source', 'candidate', 'task'):
        row[f'{prefix}_ref'] = f'ch01:{prefix}:1'
        row[f'{prefix}_evidence'] = f'{prefix}短证据'
        row[f'{prefix}_unavailable_reason'] = None
    return row


def _scored_candidate(score: int = 3) -> dict:
    return {
        'scores': {dimension: score for dimension in E.DIMENSIONS},
        'major_fact_errors': [], 'minor_fact_issues': [],
        'fact_issue_adjudications': [],
    }


def test_major_fact_claim_needs_per_issue_adjudication_and_common_origin_never_exempts():
    first = {'preference': 'P', 'candidates': {
        'P': _scored_candidate(), 'Q': _scored_candidate()}}
    second = json.loads(json.dumps(first))
    first['candidates']['P']['major_fact_errors'] = [_fact('m1', 'upstream_common')]
    config = {'books': [{'label': 'book', 'expected_chapters': 1}]}
    scores = [
        {'books': {'book': {'whole_book': {'choice': 'X'}, 'chapters': {'1': first}}}},
        {'books': {'book': {'whole_book': {'choice': 'X'}, 'chapters': {'1': second}}}},
    ]
    needs = E.adjudication_needs(scores, config)
    assert needs['book']['chapters'] == [1]
    assert 'primary_1:major:m1' in needs['book']['fact_claims']['1']['P']

    third = json.loads(json.dumps(second))
    ref = 'primary_1:major:m1'
    third['candidates']['P']['fact_issue_adjudications'] = [{
        'claim_refs': [ref], 'canonical_issue_id': 'canon-m1',
        'verdict': 'confirmed', 'severity': 'major', 'origin': 'upstream_common',
        'reason': f'{ref} 对照三侧证据后确认',
    }]
    resolved = E._resolved_candidate([first, second], 'P', third)
    assert resolved['major_fact_errors'] == 1
    assert resolved['fact_issue_origins']['upstream_common'] == 1
    stats = _quality_stats()
    stats['arms']['new']['major_fact_errors'] = resolved['major_fact_errors']
    assert not E.quality_gate_for_book(stats)['checks']['new_major_fact_errors_zero']


def test_minor_fact_issues_are_deduplicated_by_issue_not_averaged():
    first = {'candidates': {'P': _scored_candidate()}}
    second = json.loads(json.dumps(first))
    first['candidates']['P']['minor_fact_issues'] = [_fact('same')]
    second['candidates']['P']['minor_fact_issues'] = [_fact('same')]
    resolved = E._resolved_candidate([first, second], 'P', None)
    assert resolved['minor_fact_issues'] == 1


def test_same_fact_id_with_different_claim_content_requires_then_dedupes_adjudication():
    first = {'preference': 'P', 'candidates': {
        'P': _scored_candidate(), 'Q': _scored_candidate()}}
    second = json.loads(json.dumps(first))
    first_issue = _fact('collision')
    second_issue = _fact('collision')
    second_issue['candidate_evidence'] = '另一段候选证据'
    first['candidates']['P']['minor_fact_issues'] = [first_issue]
    second['candidates']['P']['minor_fact_issues'] = [second_issue]
    config = {'books': [{'label': 'book', 'expected_chapters': 1}]}
    scores = [
        {'books': {'book': {'whole_book': {'choice': 'X'}, 'chapters': {'1': first}}}},
        {'books': {'book': {'whole_book': {'choice': 'X'}, 'chapters': {'1': second}}}},
    ]
    needs = E.adjudication_needs(scores, config)
    assert needs['book']['chapters'] == [1]

    third = json.loads(json.dumps(second))
    refs = ['primary_1:minor:collision', 'primary_2:minor:collision']
    third['candidates']['P']['fact_issue_adjudications'] = [{
        'claim_refs': refs, 'canonical_issue_id': 'canonical-collision',
        'verdict': 'confirmed', 'severity': 'minor', 'origin': 'arm_induced',
        'reason': '；'.join(refs) + ' 对照后确认是同一问题',
    }]
    resolved = E._resolved_candidate([first, second], 'P', third)
    assert resolved['minor_fact_issues'] == 1


def test_public_quality_needs_contains_only_scope_count_and_hash_not_issue_material():
    secret_id = '短正文-秘密-issue-id'
    first = {'preference': 'P', 'candidates': {
        'P': _scored_candidate(), 'Q': _scored_candidate()}}
    second = json.loads(json.dumps(first))
    issue = _fact(secret_id)
    issue['detail'] = '短正文-秘密-detail'
    issue['source_evidence'] = '短正文-秘密-source-evidence'
    issue['candidate_ref'] = '短正文-秘密-candidate-ref'
    first['candidates']['P']['major_fact_errors'] = [issue]
    config = {'books': [{'label': 'book', 'expected_chapters': 1}]}
    scores = [
        {'books': {'book': {'whole_book': {'choice': 'X'}, 'chapters': {'1': first}}}},
        {'books': {'book': {'whole_book': {'choice': 'X'}, 'chapters': {'1': second}}}},
    ]
    needs = E.adjudication_needs(scores, config)
    public = E._public_quality({
        'state': 'needs_adjudication', 'pass': False, 'needs': needs})
    rendered = json.dumps(public, ensure_ascii=False)
    for secret in (
            secret_id, issue['detail'], issue['source_evidence'], issue['candidate_ref'],
            f'primary_1:major:{secret_id}'):
        assert secret not in rendered
    scope = public['needs']['book']['fact_claim_scopes'][0]
    assert scope['claim_count'] == 1 and len(scope['claim_set_sha256']) == 64


def test_round2_requires_actual_failed_round1_manifest_and_identical_upstream(
        tmp_path: Path):
    upstream = {'stage0/clean.txt': 'a' * 64}
    previous = {
        'schema_version': E.SCHEMA_VERSION,
        'experiment_round': 1,
        'previous_manifest_sha256': None,
        'verdict': {
            'artifact_gate_pass': True, 'quality_gate_state': 'complete',
            'merge_gate_pass': False, 'quality_gate_pass': False},
        'books': {
            label: {'arms': {
                arm: {'upstream_hashes': upstream} for arm in E.ARM_NAMES}}
            for label in E.EXPECTED_BOOKS
        },
    }
    path = tmp_path / 'round1.json'
    path.write_bytes(E._json_bytes(previous, pretty=True))
    digest = E._sha256_file(path)
    loaded = E._load_previous_manifest(path, digest)
    E._require_previous_upstream(loaded, 'idol', 'old', upstream)
    with pytest.raises(E.EvidenceError, match='SHA-256'):
        E._load_previous_manifest(path, '0' * 64)
    with pytest.raises(E.EvidenceError, match='Stage0~4'):
        E._require_previous_upstream(
            loaded, 'idol', 'old', {'stage0/clean.txt': 'b' * 64})

    for changed in (
            {'artifact_gate_pass': False},
            {'quality_gate_state': 'pending'}):
        broken = json.loads(json.dumps(previous))
        broken['verdict'].update(changed)
        path.write_bytes(E._json_bytes(broken, pretty=True))
        with pytest.raises(E.EvidenceError, match='artifact 有效'):
            E._load_previous_manifest(path, E._sha256_file(path))


def test_operator_preview_evidence_is_strictly_one_paid_writing_step(tmp_path: Path):
    base = tmp_path / 'base'
    argv = E.orchestrator_nodes.build_argv(
        'stage5.write', '42', ['--write-contract', 'event-list-v1'],
        base=base.resolve())
    got, digest = E._validate_preview_argv(
        argv, base=base.resolve(), sid='42', contract='event-list-v1')
    assert got == argv and len(digest) == 64
    with pytest.raises(E.EvidenceError, match='全书 Stage5 首稿命令'):
        E._validate_preview_argv(
            [token for token in argv if token != '--yes'],
            base=base.resolve(), sid='42', contract='event-list-v1')

    base_index = argv.index('--base')
    for extra in (
            ['--chapters', '1'], ['--revise'], ['--recheck'], ['--rebuild-draft']):
        partial_or_recovery = [*argv[:base_index], *extra, *argv[base_index:]]
        with pytest.raises(E.EvidenceError, match='全书 Stage5 首稿命令'):
            E._validate_preview_argv(
                partial_or_recovery, base=base.resolve(), sid='42',
                contract='event-list-v1')

    envelope = {
        'schema': 'novel-operator-step-evidence-v1',
        'steps': [{'argv': argv, 'paid': True, 'writes': 'stage5/chNN.json',
                   'label': '写作', 'note': None}],
    }
    assert E._operator_preview_argv(envelope) == argv
    evidence_path = tmp_path / 'operator.json'
    canonical = E._json_bytes(envelope, pretty=True)
    evidence_path.write_bytes(canonical)
    _data, loaded_argv, evidence_sha = E._load_operator_preview(evidence_path)
    assert loaded_argv == argv and evidence_sha == E._sha256_bytes(canonical)
    evidence_path.write_text(json.dumps(envelope), encoding='utf-8')
    with pytest.raises(E.EvidenceError, match='canonical'):
        E._load_operator_preview(evidence_path)
    envelope['steps'][0]['writes'] = []
    with pytest.raises(E.EvidenceError, match='付费且会写'):
        E._operator_preview_argv(envelope)


def test_expected_requests_reuse_production_prompt_bytes_and_cover_full_ladder(monkeypatch):
    book = SimpleNamespace(chapters={1: {'output_ordinal': 1}})
    monkeypatch.setattr(E.stage5_write, '_system_for', lambda *_args, **_kw: 'SYS')
    monkeypatch.setattr(
        E.stage5_write, '_material_for',
        lambda *_args, **_kw: SimpleNamespace(blocks=['本章材料', '上一章上下文']))
    expected = {'code_version': 'c', **E.EXPERIMENT_RUN}
    rows = E.expected_request_evidence(book, 'event-list-v1', expected)
    assert rows['1']['system_sha256'] == E._sha256_bytes(b'SYS')
    assert rows['1']['user_sha256'] == E._sha256_bytes(
        '本章材料\n\n上一章上下文'.encode('utf-8'))
    assert set(rows['1']['request_fingerprints']) == {
        'high:true', 'low:true', 'low:false'}
    assert all(len(value) == 64 for value in rows['1']['request_fingerprints'].values())


def test_first_draft_guard_separates_automatic_quality_artifacts_from_human_history(
        tmp_path: Path, monkeypatch):
    class FakeRound:
        round = 1
        revision_of = None
        review = None
        fixes = None
        origin = None
        input_basis = None
        partial_rewrite = None
        request_fingerprint = 'f' * 64
        prompts = {'CONTRACT': 'a', 'SYS': 'b', 'USER': 'c'}

    monkeypatch.setattr(E, 'RoundLlmProvenance', FakeRound)
    novel = tmp_path / 'book'
    stage5 = novel / 'stage5'
    stage5.mkdir(parents=True)
    (stage5 / 'trim_audit_ch01.json').write_text('{}', encoding='utf-8')
    evidence = E.first_draft_evidence(
        novel, {1: SimpleNamespace(provenance=FakeRound())})
    assert evidence['pass'], '自动首跑 trim audit 是质量信号，不冒充人工返工'
    assert evidence['quality_artifact_paths'] == ['stage5/trim_audit_ch01.json']

    (stage5 / 'wontfix.json').write_text('{}', encoding='utf-8')
    evidence = E.first_draft_evidence(
        novel, {1: SimpleNamespace(provenance=FakeRound())})
    assert not evidence['pass']
    assert evidence['human_or_rewrite_paths'] == ['stage5/wontfix.json']


def test_session_slice_excludes_history_and_detects_prefix_tampering(tmp_path: Path):
    service = tmp_path / '_service'
    service.mkdir()
    ledger_file = service / 'ledger.jsonl'
    audit_file = service / 'llm_audit.jsonl'
    ledger_file.write_text(json.dumps({'sid': 'old', 'node': 'stage2.global'}) + '\n',
                           encoding='utf-8')
    audit_file.write_text('', encoding='utf-8')
    session = {
        'ledger_prefix': E._prefix_evidence(ledger_file),
        'audit_prefix': E._prefix_evidence(audit_file),
    }
    fp = 'f' * 64
    row = {
        'at': '2026-08-24T00:00:01+00:00', 'sid': '42', 'node': 'stage5.write',
        'model': 'm', 'code': 'c', 'cached': False, 'cache_policy': 'default',
        'prompt_tokens': 1, 'completion_tokens': 2, 'reasoning_tokens': 1,
        'prompt_cache_hit_tokens': 0, 'prompt_cache_miss_tokens': 1,
        'outcome': 'ok', 'attempt': 1, 'max_attempts': 4, 'latency_ms': 1,
        'reasoning_effort': 'high', 'thinking': True, 'json_mode': True,
        'max_tokens': 16000, 'attempt_id': 'a', 'request_fingerprint': fp,
        'purpose': 'chapter.1', 'chapter': 1,
    }
    with ledger_file.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(row) + '\n')
    audit_file.write_text(json.dumps({
        'sid': '42', 'node': 'stage5.write', 'kind': 'gate_decision',
        'decision': 'accepted', 'attempt_id': 'a', 'chapter': 1,
    }) + '\n', encoding='utf-8')
    summary = E.ledger_summary(tmp_path, '42', 1, session=session)
    assert summary['foreign_sids'] == [] and summary['accepted_gate_rows'] == 1

    raw = ledger_file.read_bytes()
    ledger_file.write_bytes(b'X' + raw[1:])
    with pytest.raises(E.EvidenceError, match='前缀被改写'):
        E.ledger_summary(tmp_path, '42', 1, session=session)
