#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""终审事实收集器/实例校验器的纯函数守卫；不吃语料、不写盘。"""
from __future__ import annotations

import json
import hashlib
import re
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel.llm.ledger import read_jsonl_rows
from novel import skill_contract


SCRIPT_DIR = (Path(__file__).resolve().parent.parent
              / skill_contract.SKILL_REL / 'scripts')
sys.path.insert(0, str(SCRIPT_DIR))
import validate_review as validator  # noqa: E402
import collect_final_evidence as collector  # noqa: E402
import sync_review_evidence as synchronizer  # noqa: E402
import finalize_review as finalizer  # noqa: E402
from novel.orchestrator import inspection  # noqa: E402


_COMMIT_WORKER = r"""
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from sync_review_evidence import commit_review, ReviewConflictError
review = Path(sys.argv[2])
expected = Path(sys.argv[3]).read_bytes()
updated = sys.argv[4]
entered_arg, release_arg = sys.argv[5], sys.argv[6]
result = Path(sys.argv[7])
def hold_lock():
    if entered_arg == '-':
        return
    entered = Path(entered_arg)
    release = Path(release_arg)
    entered.write_bytes(b'')
    while not release.exists():
        time.sleep(0.001)
try:
    commit_review(
        review, updated, expected_original=expected,
        precommit_check=hold_lock if entered_arg != '-' else None)
except ReviewConflictError:
    outcome = 'conflict'
else:
    outcome = 'committed'
result.write_text(outcome, encoding='utf-8')
"""


CURRENT_QUALITY_STAGES = (
    'stage0', 'stage1', 'stage2', 'stage4', 'stage5', 'stage6', 'stage7',
)


def _facts() -> dict:
    counts = {kind: 0 for kind in (
        'invalid', 'rejected', 'stalled', 'repairable', 'human_only',
        'verification', 'upstream', 'approval')}
    exits = [{'node': 'stage7.assemble', 'exit_code': 0, 'run_id': 'run-final'}]
    exit_digest = hashlib.sha256(json.dumps(
        exits, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')).hexdigest()
    anchor = {
        'schema': 'novel-final-evidence-v4', 'sid': 'book-1', 'title': '测试书',
        'session_started_at': 100, 'start_code_version': 'code-v1',
        'session_run_ids': ['run-final'],
        'novel_dir': 'E:\\outputs\\测试书_book-1',
        'book_path': 'E:\\outputs\\测试书_book-1\\stage7\\测试书_rewriting.txt',
        'book_sha1': 'a' * 40, 'chapters': 8, 'inbox_counts': counts,
        'inbox_attention_counts': {'pending': 0, 'reviewed_retained': 0},
        'inbox_delivery_counts': {'blocking': 0, 'disclosure': 0},
        'active_terminal_files': [], 'exit_sequence_count': 1,
        'exit_sequence_sha256': exit_digest,
        'stage2_review': {
            'current': True, 'approved': True,
            'counts': {'pending': 0, 'PASS': 58, 'FAIL': 0, 'UNVER': 0},
            'error': None,
        },
    }
    rows = [
        f'| {stage}.check | fresh/match | 0/1 | 0/1 | 1 | 1 | 0 | '
        'badge=amber; passed=true |'
        for stage in CURRENT_QUALITY_STAGES
    ]
    empty_ledger = {
        'path': 'E:\\outputs\\_service\\ledger.jsonl',
        'scope': 'run_ids', 'run_ids': ['run-final'],
        'legacy_ungrouped_rows': 0, 'by_node': {}, 'by_run_node': [],
        'total': {
            'calls_paid': 0, 'calls_cached': 0, 'prompt_tokens': 0,
            'completion_tokens': 0, 'reasoning_tokens': 0, 'latency_ms': 0,
        },
    }
    return {
        'quality_table_rows': rows, 'review_anchor': anchor,
        'review_anchor_line': '<!-- novel-final-evidence:' + json.dumps(
            anchor, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + ' -->',
        'inbox_counts_text': (
            '八类：' + ' · '.join(f'{key}=0' for key in counts)
            + '；关注：pending=0 · reviewed_retained=0'
            + '；交付：blocking=0 · disclosure=0'),
        'exit_sequence_text': 'stage7.assemble:0',
        'exit_sequence_compact_text': 'stage7.assemble:0',
        'active_terminal_files': [],
        'ledger': empty_ledger,
        'ledger_cumulative': {**empty_ledger, 'scope': 'sid_all_history',
                              'run_ids': None},
        'book': {'sha1': 'a' * 40, 'path': anchor['book_path'],
                 'current_delivery_readable': True, 'exact_rebuild_match': True},
    }


def _review(facts: dict, status: str = 'ready') -> str:
    return '\n'.join([
        '# 终审包 · 测试书（book-1）',
        f'> 交付状态：`{status}`',
        *validator.REQUIRED_HEADINGS[:4],
        validator.QUALITY_HEADER,
        '|---|---|---:|---:|---:|---:|---:|---|',
        *facts['quality_table_rows'],
        *validator.REQUIRED_HEADINGS[4:],
        facts['inbox_counts_text'], facts['exit_sequence_text'],
        facts['book']['sha1'], facts['book']['path'], facts['review_anchor_line'],
    ])


def test_review_validator_accepts_an_exact_current_instance():
    facts = _facts()
    assert validator.validate(_review(facts), facts) == []


def test_review_validator_rejects_a_legacy_draft_placeholder_not_in_template():
    facts = _facts()
    errors = validator.validate(_review(facts) + '\n<待填>\n', facts)
    assert any('<待填>' in error for error in errors)


def test_review_validator_uses_seven_current_stages_and_keeps_stage3_retired():
    assert tuple(collector.STAGES) == CURRENT_QUALITY_STAGES
    assert tuple(validator.QUALITY_STAGES) == CURRENT_QUALITY_STAGES
    assert 'stage3' not in validator.QUALITY_STAGES

    facts = _facts()
    assert len(facts['quality_table_rows']) == 7
    assert all('stage3' not in row for row in facts['quality_table_rows'])

    without_stage4 = _review(facts).replace(facts['quality_table_rows'][3], '')
    errors = validator.validate(without_stage4, facts)
    assert any('质量表缺 stage4 当前报告行' in error for error in errors)
    assert not any('质量表缺 stage3 当前报告行' in error for error in errors)


def test_review_validator_rejects_a_stale_anchor_and_missing_metric_column():
    facts = _facts()
    text = _review(facts).replace(validator.QUALITY_HEADER, '| 节点/报告 | freshness | HARD |')
    text = text.replace('"chapters":8', '"chapters":7')
    errors = validator.validate(text, facts)
    assert any('metric/skipped' in error for error in errors)
    assert any('机器锚点与当前盘面不一致' in error for error in errors)


def test_review_validator_refuses_readyish_when_stage2_is_not_all_pass():
    facts = _facts()
    facts['review_anchor']['stage2_review']['approved'] = False
    facts['review_anchor']['stage2_review']['counts']['FAIL'] = 1
    facts['review_anchor_line'] = '<!-- novel-final-evidence:' + json.dumps(
        facts['review_anchor'], ensure_ascii=False, sort_keys=True,
        separators=(',', ':')) + ' -->'
    errors = validator.validate(_review(facts), facts)
    assert any('Stage2 全局复核不是当前逐项 PASS' in error for error in errors)


def test_review_validator_distinguishes_disclosures_from_delivery_blockers():
    facts = _facts()
    facts['review_anchor']['inbox_attention_counts']['pending'] = 2
    facts['review_anchor']['inbox_delivery_counts'] = {
        'blocking': 0, 'disclosure': 2,
    }
    facts['inbox_counts_text'] = facts['inbox_counts_text'].replace(
        'pending=0', 'pending=2', 1).replace('disclosure=0', 'disclosure=2')
    facts['review_anchor_line'] = '<!-- novel-final-evidence:' + json.dumps(
        facts['review_anchor'], ensure_ascii=False, sort_keys=True,
        separators=(',', ':')) + ' -->'

    assert validator.validate(
        _review(facts, 'ready_with_disclosures'), facts) == []

    facts['review_anchor']['inbox_delivery_counts']['blocking'] = 1
    facts['review_anchor_line'] = '<!-- novel-final-evidence:' + json.dumps(
        facts['review_anchor'], ensure_ascii=False, sort_keys=True,
        separators=(',', ':')) + ' -->'
    errors = validator.validate(
        _review(facts, 'ready_with_disclosures'), facts)
    assert any('交付阻塞余额' in error for error in errors)


def test_final_evidence_jsonl_only_tolerates_an_uncommitted_tail(tmp_path):
    path = tmp_path / 'ledger.jsonl'
    path.write_bytes(b'{"sid":"book-1"}\n{"partial":')
    assert read_jsonl_rows(path) == [{'sid': 'book-1'}]

    path.write_bytes(b'{"sid":"book-1"}\n{"broken":true\n')
    with pytest.raises(json.JSONDecodeError):
        read_jsonl_rows(path)

    path.write_bytes(b'{"sid":"book-1"}\n[1,2,3]\n')
    with pytest.raises(ValueError, match='JSON object'):
        read_jsonl_rows(path)

    path.write_bytes(b'{"sid":"book-1"}\n{"bad":"\xe4\n')
    with pytest.raises(UnicodeDecodeError):
        read_jsonl_rows(path)

    path.write_bytes(b'{"sid":"book-1"}\r{"sid":"book-2"}\n')
    with pytest.raises(ValueError, match='不是 LF 提交'):
        read_jsonl_rows(path)


def test_ledger_uses_beacon_run_ids_and_leaves_legacy_ungrouped(monkeypatch, tmp_path):
    rows = [
        {'sid': 'book-1', 'outcome': 'ok', 'node': 'stage2.global',
         'run_id': 'run-old',
         'at': '1970-01-01T00:01:30+00:00', 'cached': False,
         'prompt_tokens': 90, 'completion_tokens': 9, 'reasoning_tokens': 1,
         'latency_ms': 900},
        {'sid': 'book-1', 'outcome': 'ok', 'node': 'stage2.global',
         'run_id': 'run-current',
         'at': '1970-01-01T00:01:50+00:00', 'cached': False,
         'prompt_tokens': 110, 'completion_tokens': 11, 'reasoning_tokens': 2,
         'latency_ms': 1100},
        {'sid': 'book-1', 'outcome': 'ok', 'node': 'stage5.write',
         'run_id': 'run-current',
         'at': '1970-01-01T00:02:00+00:00', 'cached': True},
        {'sid': 'book-1', 'outcome': 'error', 'node': 'stage2.blueprint',
         'run_id': 'run-current',
         'at': '1970-01-01T00:02:05+00:00', 'cached': False,
         'latency_ms': 250},
        {'sid': 'book-1', 'outcome': 'ok', 'role': 'commander', 'node': None,
         'run_id': 'run-current',
         'at': '1970-01-01T00:02:07+00:00', 'cached': False,
         'prompt_tokens': 7, 'completion_tokens': 3, 'reasoning_tokens': 2,
         'latency_ms': 125},
        {'sid': 'book-1', 'outcome': 'ok', 'node': 'stage4.plan',
         'at': '1970-01-01T00:02:10+00:00', 'cached': False,
         'prompt_tokens': 50, 'completion_tokens': 5,
         'reasoning_tokens': 3, 'latency_ms': 500},
        {'sid': 'other', 'outcome': 'ok', 'node': 'stage2.global',
         'run_id': 'run-current',
         'at': '1970-01-01T00:02:00+00:00', 'cached': False},
    ]
    monkeypatch.setattr(collector, '_jsonl', lambda _path: rows)
    monkeypatch.setattr(collector, 'ledger_path', lambda _base: tmp_path / 'ledger.jsonl')

    session = collector._ledger(
        tmp_path, 'book-1', run_ids={'run-current'})
    cumulative = collector._ledger(tmp_path, 'book-1')
    events = [
        SimpleNamespace(sid='book-1', phase='end', ts=99,
                        node='stage2.global', exit_code=0, run_id='run-old'),
        SimpleNamespace(sid='book-1', phase='end', ts=100,
                        node='stage5.write', exit_code=1, run_id='run-current'),
        SimpleNamespace(sid='other', phase='end', ts=101,
                        node='stage7.assemble', exit_code=0, run_id='run-other'),
    ]

    assert session['total']['calls_paid'] == 2
    assert session['total']['calls_cached'] == 1
    assert session['total']['wire_attempts'] == 3
    assert session['total']['wire_errors_billing_unknown'] == 1
    assert session['total']['wire_outcomes'] == {'error': 1, 'ok': 2}
    assert session['total']['prompt_tokens'] == 117
    assert cumulative['total']['calls_paid'] == 4
    assert session['legacy_ungrouped_rows'] == 1
    assert session['by_node']['role:commander']['calls_paid'] == 1
    assert 'unknown' not in session['by_node']
    assert any(
        row['node'] == 'role:commander'
        for row in session['by_run_node']
    )
    assert session['by_run_node'][0]['run_id'] == 'run-current'
    assert collector._session_exits(events, 'book-1', 100) == [
        {'node': 'stage5.write', 'exit_code': 1, 'run_id': 'run-current'}]
    assert collector._session_run_ids(events, 'book-1', 100) == {'run-current'}


def test_ledger_maps_epoch_producer_rows_back_to_agent_parent(
        monkeypatch, tmp_path):
    rows = [
        {
            'sid': 'book-1', 'outcome': 'ok', 'node': 'stage2.blueprint',
            'run_id': 'epoch-producer-current', 'parent_run_id': 'run-current',
            'cached': False, 'prompt_tokens': 21, 'completion_tokens': 8,
            'reasoning_tokens': 3, 'latency_ms': 100,
        },
        {
            'sid': 'book-1', 'outcome': 'ok', 'node': 'stage2.blueprint',
            'run_id': 'epoch-producer-old', 'parent_run_id': 'run-old',
            'cached': False, 'prompt_tokens': 999, 'completion_tokens': 999,
            'reasoning_tokens': 999, 'latency_ms': 999,
        },
    ]
    monkeypatch.setattr(collector, '_jsonl', lambda _path: rows)
    monkeypatch.setattr(
        collector, 'ledger_path', lambda _base: tmp_path / 'ledger.jsonl')

    selected = collector._ledger(
        tmp_path, 'book-1', run_ids={'run-current'})

    assert selected['total']['calls_paid'] == 1
    assert selected['total']['prompt_tokens'] == 21
    assert selected['by_run_node'] == [{
        'run_id': 'epoch-producer-current',
        'node': 'stage2.blueprint',
        'calls_paid': 1,
        'calls_cached': 0,
        'wire_attempts': 1,
        'wire_errors_billing_unknown': 0,
        'wire_unknown_outcome': 0,
        'wire_outcomes': {'ok': 1},
        'prompt_tokens': 21,
        'completion_tokens': 8,
        'reasoning_tokens': 3,
        'latency_ms': 100,
    }]


def test_review_evidence_sync_replaces_only_the_machine_blocks():
    facts = _facts()
    text = '\n'.join([
        'agent judgment stays',
        *synchronizer.QUALITY, *synchronizer.LEDGER,
        *synchronizer.EXITS, *synchronizer.ANCHOR,
        'agent recovery stays',
    ])

    updated = synchronizer.render(text, facts)

    assert 'agent judgment stays' in updated
    assert 'agent recovery stays' in updated
    assert validator.QUALITY_HEADER in updated
    assert facts['quality_table_rows'][0] in updated
    assert facts['inbox_counts_text'] in updated
    assert facts['exit_sequence_compact_text'] in updated
    assert facts['review_anchor_line'] in updated


def test_compat_sync_holds_generic_claim_from_collect_through_cas_commit(
        tmp_path, monkeypatch, capsys):
    novel_dir = tmp_path / 'FQ_测试书_book-1'
    novel_dir.mkdir()
    review = novel_dir / '测试书_review.md'
    review.write_bytes(b'original review')
    events: list[str] = []

    @contextmanager
    def guarded(base, sid, *, operation, details):
        assert Path(base) == tmp_path and sid == 'book-1'
        assert operation == 'sync-review-evidence'
        assert details == {
            'started_at': 100,
            'start_code_version': 'code-v1',
            'review_path': str(review.resolve()),
        }
        events.append('enter')
        try:
            yield SimpleNamespace(parent_run_id='commander-sync-test')
        finally:
            events.append('exit')

    def collect_guarded(*_args, **_kwargs):
        assert events == ['enter']
        events.append('collect')
        return {'facts': True}

    def render_guarded(original, facts):
        assert events == ['enter', 'collect']
        assert original == 'original review' and facts == {'facts': True}
        events.append('render')
        return 'updated review'

    def commit_guarded(path, updated, *, expected_original):
        assert events == ['enter', 'collect', 'render']
        assert path == review.resolve()
        assert updated == 'updated review'
        assert expected_original == b'original review'
        events.append('commit')
        return updated.encode('utf-8')

    monkeypatch.setattr(synchronizer, 'operation_scope', guarded)
    monkeypatch.setattr(synchronizer, 'collect', collect_guarded)
    monkeypatch.setattr(synchronizer, 'render', render_guarded)
    monkeypatch.setattr(synchronizer, 'commit_review', commit_guarded)

    code = synchronizer.main([
        '--base', str(tmp_path), '--sid', 'book-1',
        '--started-at', '100', '--start-code-version', 'code-v1',
        '--review', str(review),
    ])

    assert code == 0
    assert events == ['enter', 'collect', 'render', 'commit', 'exit']
    assert 'PASS 已同步机器事实' in capsys.readouterr().out


def test_review_evidence_sync_bootstraps_a_legacy_review_without_losing_narrative():
    facts = _facts()
    legacy = '\n'.join([
        'agent judgment stays', validator.QUALITY_HEADER,
        '|---|---|---:|---:|---:|---:|---:|---|',
        *facts['quality_table_rows'], '',
        '| 节点 | 实付调用 | 缓存命中 | prompt tokens | completion tokens | reasoning tokens | 可证实耗时 |',
        '|---|---:|---:|---:|---:|---:|---:|',
        '| stage2.global | 9 | 0 | 1 | 2 | 1 | 3s |', '',
        '### 退出码序列', '', '`historical:0`', '',
        '### 机器事实锚点', '', facts['review_anchor_line'], '',
        'agent recovery stays',
    ])

    updated = synchronizer.render(legacy, facts)

    assert all(updated.count(marker) == 1 for marker in synchronizer._ALL_MARKERS)
    assert 'agent judgment stays' in updated and 'agent recovery stays' in updated
    assert 'historical:0' not in updated
    assert facts['exit_sequence_compact_text'] in updated


def test_review_commit_first_lock_does_not_invalidate_the_surface_seal(
        monkeypatch, tmp_path):
    """首次提交前尚无 review.lock；拿锁本身不得制造假漂移。"""
    novel_dir = tmp_path / 'FQ_测试书_book-1'
    (novel_dir / 'stage7').mkdir(parents=True)
    artifact = novel_dir / 'stage7' / 'name_map.json'
    artifact.write_text('formal artifact', encoding='utf-8')
    review = novel_dir / '测试书_review.md'
    original = b'agent narrative\n'
    review.write_bytes(original)
    monkeypatch.setattr(
        inspection.paths, 'resolve_novel_dir', lambda _base, _sid: novel_dir)
    monkeypatch.setattr(
        inspection.paths, 'review_package_path', lambda _dir: review)
    monkeypatch.setattr(inspection.prov, 'code_version', lambda: 'code-v1')

    seal = inspection.read_surface_seal(tmp_path, 'book-1')
    lock = review.with_name(review.name + '.lock')
    assert not lock.exists()

    landed = synchronizer.commit_review(
        review,
        'validated candidate\n',
        expected_original=original,
        precommit_check=lambda: (
            None if inspection.read_surface_seal(tmp_path, 'book-1') == seal
            else pytest.fail('拿 review 锁本身不得改变 surface seal')
        ),
    )

    assert landed == b'validated candidate\n'
    assert review.read_bytes() == landed
    assert lock.is_file()
    artifact.write_text('changed formal artifact', encoding='utf-8')
    assert inspection.read_surface_seal(tmp_path, 'book-1') != seal


def test_review_commit_serializes_threads_and_loser_cannot_overwrite(tmp_path):
    review = tmp_path / '测试书_review.md'
    original = b'agent narrative\n'
    review.write_bytes(original)
    legacy_shared_temp = review.with_name(review.name + '.operator-sync.tmp')
    legacy_shared_temp.write_bytes(b'do not touch')
    first_inside = threading.Event()
    release_first = threading.Event()
    outcomes: list[tuple[str, str]] = []

    def first_check() -> None:
        first_inside.set()
        assert release_first.wait(timeout=10)

    def writer(label: str, check=None) -> None:
        try:
            synchronizer.commit_review(
                review,
                f'candidate {label}\n',
                expected_original=original,
                precommit_check=check,
            )
        except synchronizer.ReviewConflictError:
            outcomes.append((label, 'conflict'))
        else:
            outcomes.append((label, 'committed'))

    first = threading.Thread(target=writer, args=('first', first_check))
    second = threading.Thread(target=writer, args=('second',))
    first.start()
    assert first_inside.wait(timeout=10)
    second.start()
    time.sleep(0.05)
    second_was_blocked = second.is_alive()
    release_first.set()
    first.join(timeout=10)
    second.join(timeout=10)

    assert not first.is_alive() and not second.is_alive()
    assert second_was_blocked, '第二个线程应阻塞在同 review 锁上'
    assert sorted(outcomes) == [('first', 'committed'), ('second', 'conflict')]
    assert review.read_bytes() == b'candidate first\n'
    assert legacy_shared_temp.read_bytes() == b'do not touch'
    assert not list(tmp_path.glob(f'.{review.name}.operator-sync.*.tmp'))


def test_review_commit_serializes_processes_and_only_one_candidate_lands(tmp_path):
    review = tmp_path / '测试书_review.md'
    original = b'agent narrative\n'
    review.write_bytes(original)
    expected = tmp_path / 'expected.bin'
    expected.write_bytes(original)
    entered = tmp_path / 'first-inside-lock.flag'
    release = tmp_path / 'release-first.flag'
    results = [tmp_path / 'result-a.txt', tmp_path / 'result-b.txt']
    candidates = ('candidate a\n', 'candidate b\n')
    first = subprocess.Popen(
        [
            sys.executable, '-c', _COMMIT_WORKER, str(SCRIPT_DIR),
            str(review), str(expected), candidates[0], str(entered),
            str(release), str(results[0]),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 10
    while not entered.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not entered.exists():
        release.write_bytes(b'')
        _out, err = first.communicate(timeout=10)
        pytest.fail(
            '第一个进程未进入 review 锁临界区：'
            + err.decode('utf-8', 'replace'))
    second = subprocess.Popen(
        [
            sys.executable, '-c', _COMMIT_WORKER, str(SCRIPT_DIR),
            str(review), str(expected), candidates[1], '-', '-',
            str(results[1]),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(0.25)
    second_was_blocked = not results[1].exists()
    review_was_unchanged = review.read_bytes() == original
    release.write_bytes(b'')
    procs = [first, second]
    for proc in procs:
        _out, err = proc.communicate(timeout=60)
        assert proc.returncode == 0, err.decode('utf-8', 'replace')

    assert second_was_blocked, '第二个进程应阻塞在同 review 锁上'
    assert review_was_unchanged
    assert sorted(path.read_text(encoding='utf-8') for path in results) == [
        'committed', 'conflict']
    assert review.read_text(encoding='utf-8') in candidates
    assert not list(tmp_path.glob(f'.{review.name}.operator-sync.*.tmp'))


def _delivery(state: str = 'ready'):
    payload = {'state': state, 'reason': 'fresh delivery check', 'checked_at': 123}
    return SimpleNamespace(
        state=state,
        reason=payload['reason'],
        model_dump=lambda **_kwargs: payload,
    )


def _install_finalizer_review(monkeypatch, novel_dir: Path, review: Path) -> None:
    """让 finalizer 用真实的“书目录 → 唯一终审包”生产关系。"""
    monkeypatch.setattr(
        finalizer.paths, 'resolve_novel_dir', lambda *_args: novel_dir)
    monkeypatch.setattr(
        finalizer.paths, 'review_package_path', lambda _novel_dir: review)


def test_finalizer_collects_once_validates_before_commit_and_then_assesses_fresh(
        monkeypatch, tmp_path):
    review = tmp_path / '测试书_review.md'
    review.write_text('agent narrative', encoding='utf-8')
    facts = _facts()
    events: list[str] = []
    collects: list[tuple] = []
    seals: list[dict] = []

    monkeypatch.setattr(
        finalizer, 'collect',
        lambda *args, **kwargs: collects.append((args, kwargs)) or facts)
    _install_finalizer_review(monkeypatch, tmp_path, review)
    monkeypatch.setattr(
        finalizer, 'read_surface_seal',
        lambda *_args, **kwargs: seals.append(kwargs) or 'seal-v1')
    monkeypatch.setattr(
        finalizer, 'render',
        lambda text, got: events.append('render') or f'{text}\nmachine facts')
    monkeypatch.setattr(
        finalizer, 'validate',
        lambda text, got, **_kwargs: events.append('validate') or [])

    def commit(path, text, **kwargs):
        events.append('commit')
        return synchronizer.commit_review(path, text, **kwargs)

    def assess(*_args, **kwargs):
        events.append('assess')
        assert review.read_text(encoding='utf-8').endswith('machine facts')
        assert kwargs['start_code_version'] == 'code-v1'
        return _delivery()

    monkeypatch.setattr(finalizer, 'commit_review', commit)
    monkeypatch.setattr(finalizer, 'assess', assess)

    result = finalizer.finalize(
        tmp_path, 'book-1', started_at=100,
        start_code_version='code-v1', review_path=review)

    assert len(collects) == 1
    assert len(seals) == 4
    assert all(row == {
        'include_final_evidence_service': True,
        'excluded_review': review.resolve(),
    } for row in seals)
    assert events == ['render', 'validate', 'commit', 'assess']
    assert result['state'] == 'ready'
    assert result['phases']['delivery']['state'] == 'passed'


def test_finalizer_deterministically_creates_a_missing_canonical_review(
        monkeypatch, tmp_path):
    """缺 review 不再召回 Commander；从权威事实创建，叙述空白明示 unknown。"""
    review = tmp_path / '测试书_review.md'
    facts = _facts()
    facts.update({'sid': 'book-1', 'title': '测试书'})
    _install_finalizer_review(monkeypatch, tmp_path, review)
    monkeypatch.setattr(finalizer, 'collect', lambda *_args, **_kwargs: facts)
    monkeypatch.setattr(
        finalizer, 'read_surface_seal', lambda *_args, **_kwargs: 'seal-v1')
    monkeypatch.setattr(finalizer, 'render', lambda text, _facts: text)
    monkeypatch.setattr(finalizer, 'validate', lambda *_args, **_kwargs: [])
    commits: list[bytes | None] = []

    def commit(path, text, **kwargs):
        commits.append(kwargs['expected_original'])
        return synchronizer.commit_review(path, text, **kwargs)

    monkeypatch.setattr(finalizer, 'commit_review', commit)
    monkeypatch.setattr(finalizer, 'assess', lambda *_args, **_kwargs: _delivery())

    result = finalizer.finalize(
        tmp_path, 'book-1', started_at=100,
        start_code_version='code-v1', review_path=review)

    landed = review.read_text(encoding='utf-8')
    assert result['state'] == 'ready'
    assert commits == [None]
    assert finalizer._UNKNOWN in landed
    assert not re.findall(r'<[^>]+>', landed, flags=re.DOTALL)


def test_finalizer_normalizes_an_existing_draft_before_render(
        monkeypatch, tmp_path):
    review = tmp_path / '测试书_review.md'
    facts = _facts()
    facts.update({
        'sid': 'book-1', 'title': '测试书',
        'base': str(tmp_path.resolve()),
        'novel_dir': str(tmp_path.resolve()),
    })
    draft = '\n'.join([
        '# 终审包 · 测试书（book-1）',
        '> 交付状态：`草稿（运行中）`',
        '> 建议结论：运行进行中，尚未形成结论。',
        '> 成书：`未生成`',
        '> 本包对应成书 SHA1：`不适用`',
        '> 本次运行坐标：`started_at=1`；'
        '`start_code_version=old`；`run_ids=<待填>`',
        '人工已填叙述必须保留。',
        '账本：`<base>/_service/ledger.jsonl`',
        '其他：<待填>',
    ])
    review.write_text(draft, encoding='utf-8')
    _install_finalizer_review(monkeypatch, tmp_path, review)
    monkeypatch.setattr(finalizer, 'collect', lambda *_args, **_kwargs: facts)
    monkeypatch.setattr(
        finalizer, 'read_surface_seal', lambda *_args, **_kwargs: 'seal-v1')
    monkeypatch.setattr(finalizer, 'render', lambda text, _facts: text)
    monkeypatch.setattr(finalizer, 'validate', lambda *_args, **_kwargs: [])
    monkeypatch.setattr(finalizer, 'assess', lambda *_args, **_kwargs: _delivery())

    result = finalizer.finalize(
        tmp_path, 'book-1', started_at=100,
        start_code_version='code-v1', review_path=review)

    landed = review.read_text(encoding='utf-8')
    assert result['state'] == 'ready'
    assert '> 交付状态：`ready`' in landed
    assert '> 建议结论：建议接收并阅读成书。' in landed
    assert f"> 成书：`{facts['book']['path']}`" in landed
    assert f"> 本包对应成书 SHA1：`{facts['book']['sha1']}`" in landed
    assert "`started_at=100`；`start_code_version=code-v1`；" in landed
    assert str(tmp_path.resolve()) + '/_service/ledger.jsonl' in landed
    assert '人工已填叙述必须保留。' in landed
    assert '<待填>' not in landed and '<base>' not in landed
    assert finalizer._UNKNOWN in landed


def test_finalizer_validation_failure_keeps_the_original_review_and_skips_delivery(
        monkeypatch, tmp_path):
    review = tmp_path / '测试书_review.md'
    original = b'agent narrative\n'
    review.write_bytes(original)
    monkeypatch.setattr(finalizer, 'collect', lambda *_args, **_kwargs: _facts())
    _install_finalizer_review(monkeypatch, tmp_path, review)
    monkeypatch.setattr(
        finalizer, 'read_surface_seal', lambda *_args, **_kwargs: 'seal-v1')
    monkeypatch.setattr(finalizer, 'render', lambda *_args: 'invalid candidate')
    monkeypatch.setattr(
        finalizer, 'validate', lambda *_args, **_kwargs: ['仍有模板占位符'])
    monkeypatch.setattr(
        finalizer, 'commit_review',
        lambda *_args: pytest.fail('校验失败时不得写盘'))
    monkeypatch.setattr(
        finalizer, 'assess',
        lambda *_args, **_kwargs: pytest.fail('校验失败时不得终验'))

    result = finalizer.finalize(
        tmp_path, 'book-1', started_at=100,
        start_code_version='code-v1', review_path=review)

    assert result['state'] == 'stopped'
    assert result['phases']['validate']['state'] == 'failed'
    assert result['phases']['commit']['state'] == 'skipped'
    assert review.read_bytes() == original


def test_finalizer_never_falls_back_to_a_committable_stopped_review(
        monkeypatch, tmp_path):
    review = tmp_path / '测试书_review.md'
    original = b'# review\n> delivery draft\n'
    review.write_bytes(original)
    monkeypatch.setattr(finalizer, 'collect', lambda *_args, **_kwargs: _facts())
    _install_finalizer_review(monkeypatch, tmp_path, review)
    monkeypatch.setattr(
        finalizer, 'read_surface_seal', lambda *_args, **_kwargs: 'seal-v1')
    monkeypatch.setattr(finalizer, 'render', lambda text, _facts: text)

    def validate(text, *_args, **_kwargs):
        return ['ready candidate failed'] if '`ready`' in text else []

    monkeypatch.setattr(finalizer, 'validate', validate)
    monkeypatch.setattr(
        finalizer, 'commit_review',
        lambda *_args, **_kwargs: pytest.fail('不得提交 stopped fallback'))
    monkeypatch.setattr(
        finalizer, 'assess',
        lambda *_args, **_kwargs: pytest.fail('候选校验失败不得终验'))

    result = finalizer.finalize(
        tmp_path, 'book-1', started_at=100,
        start_code_version='code-v1', review_path=review)

    assert result['state'] == 'stopped' and result['delivery'] is None
    assert result['phases']['validate']['state'] == 'failed'
    assert 'ready candidate failed' in result['phases']['validate']['detail']
    assert review.read_bytes() == original


def test_finalizer_refuses_to_expose_a_delivery_state_different_from_review(
        monkeypatch, tmp_path):
    review = tmp_path / '测试书_review.md'
    review.write_text('# review\n', encoding='utf-8')
    monkeypatch.setattr(finalizer, 'collect', lambda *_args, **_kwargs: _facts())
    _install_finalizer_review(monkeypatch, tmp_path, review)
    monkeypatch.setattr(
        finalizer, 'read_surface_seal', lambda *_args, **_kwargs: 'seal-v1')
    monkeypatch.setattr(finalizer, 'render', lambda text, _facts: text)
    monkeypatch.setattr(finalizer, 'validate', lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        finalizer, 'assess',
        lambda *_args, **_kwargs: _delivery('ready_with_disclosures'))

    result = finalizer.finalize(
        tmp_path, 'book-1', started_at=100,
        start_code_version='code-v1', review_path=review)

    assert result['state'] == 'stopped' and result['delivery'] is None
    assert result['phases']['delivery']['state'] == 'failed'
    assert 'review=ready, delivery=ready_with_disclosures' in (
        result['phases']['delivery']['detail'])


def test_finalizer_refuses_a_surface_that_drifts_between_collect_and_commit(
        monkeypatch, tmp_path):
    review = tmp_path / '测试书_review.md'
    original = b'agent narrative\n'
    review.write_bytes(original)
    seals = iter(('seal-stable', 'seal-stable', 'seal-before-commit'))
    monkeypatch.setattr(finalizer, 'collect', lambda *_args, **_kwargs: _facts())
    _install_finalizer_review(monkeypatch, tmp_path, review)
    monkeypatch.setattr(
        finalizer, 'read_surface_seal',
        lambda *_args, **_kwargs: next(seals))
    monkeypatch.setattr(
        finalizer, 'render',
        lambda *_args: '> 交付状态：`ready`\nvalid candidate')
    monkeypatch.setattr(finalizer, 'validate', lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        finalizer, 'assess',
        lambda *_args, **_kwargs: pytest.fail('盘面漂移后不得终验'))

    result = finalizer.finalize(
        tmp_path, 'book-1', started_at=100,
        start_code_version='code-v1', review_path=review)

    assert result['state'] == 'stopped'
    assert result['phases']['commit']['state'] == 'skipped'
    assert '发生变化' in result['phases']['commit']['detail']
    assert review.read_bytes() == original


def test_finalizer_user_edit_after_render_wins_and_is_never_overwritten(
        monkeypatch, tmp_path):
    review = tmp_path / '测试书_review.md'
    original = b'agent narrative\n'
    user_edit = b'user edit must win\n'
    review.write_bytes(original)
    monkeypatch.setattr(finalizer, 'collect', lambda *_args, **_kwargs: _facts())
    _install_finalizer_review(monkeypatch, tmp_path, review)
    monkeypatch.setattr(
        finalizer, 'render',
        lambda *_args: '> 交付状态：`ready`\nvalid candidate')
    monkeypatch.setattr(finalizer, 'validate', lambda *_args, **_kwargs: [])
    seal_calls: list[None] = []

    def surface_seal(*_args, **_kwargs):
        seal_calls.append(None)
        if len(seal_calls) == 3:
            # 模拟不认 sidecar 锁的用户编辑器：它在 finalizer 已经
            # 进入锁内 precommit seal 时写 review。replace 前的第二次
            # 字节 CAS 必须仍让这份用户内容获胜。
            review.write_bytes(user_edit)
        return 'seal-v1'

    monkeypatch.setattr(finalizer, 'read_surface_seal', surface_seal)
    monkeypatch.setattr(
        finalizer, 'assess',
        lambda *_args, **_kwargs: pytest.fail('用户编辑竞争后不得终验'))

    result = finalizer.finalize(
        tmp_path, 'book-1', started_at=100,
        start_code_version='code-v1', review_path=review)

    assert result['state'] == 'stopped'
    assert result['phases']['commit']['state'] == 'skipped'
    assert '其他写者修改' in result['phases']['commit']['detail']
    assert review.read_bytes() == user_edit
    assert len(seal_calls) == 3


def test_finalizer_refuses_a_change_that_happens_inside_collect(
        monkeypatch, tmp_path):
    review = tmp_path / '测试书_review.md'
    original = b'agent narrative\n'
    review.write_bytes(original)
    seals = iter(('seal-before-collect', 'seal-after-collect'))
    collects: list[None] = []
    monkeypatch.setattr(
        finalizer, 'collect',
        lambda *_args, **_kwargs: collects.append(None) or _facts())
    _install_finalizer_review(monkeypatch, tmp_path, review)
    monkeypatch.setattr(
        finalizer, 'read_surface_seal',
        lambda *_args, **_kwargs: next(seals))
    monkeypatch.setattr(
        finalizer, 'render',
        lambda *_args: pytest.fail('collect 内漂移后不得继续 render'))
    monkeypatch.setattr(
        finalizer, 'commit_review',
        lambda *_args: pytest.fail('collect 内漂移后不得写盘'))
    monkeypatch.setattr(
        finalizer, 'assess',
        lambda *_args, **_kwargs: pytest.fail('collect 内漂移后不得终验'))

    result = finalizer.finalize(
        tmp_path, 'book-1', started_at=100,
        start_code_version='code-v1', review_path=review)

    assert collects == [None]
    assert result['state'] == 'stopped'
    assert result['phases']['seal']['state'] == 'failed'
    assert result['phases']['commit']['state'] == 'skipped'
    assert review.read_bytes() == original


def test_finalizer_refuses_a_noncanonical_review_before_collecting_or_writing(
        monkeypatch, tmp_path):
    canonical = tmp_path / '测试书_review.md'
    requested = tmp_path / '旁路_review.md'
    canonical.write_bytes(b'canonical review\n')
    requested.write_bytes(b'other review\n')
    _install_finalizer_review(monkeypatch, tmp_path, canonical)
    monkeypatch.setattr(
        finalizer, 'read_surface_seal',
        lambda *_args, **_kwargs: pytest.fail('非 canonical 路径不得开始 seal'))
    monkeypatch.setattr(
        finalizer, 'collect',
        lambda *_args, **_kwargs: pytest.fail('非 canonical 路径不得收集事实'))
    monkeypatch.setattr(
        finalizer, 'commit_review',
        lambda *_args, **_kwargs: pytest.fail('非 canonical 路径不得写盘'))
    monkeypatch.setattr(
        finalizer, 'assess',
        lambda *_args, **_kwargs: pytest.fail('非 canonical 路径不得终验'))

    result = finalizer.finalize(
        tmp_path, 'book-1', started_at=100,
        start_code_version='code-v1', review_path=requested)

    assert result['state'] == 'stopped' and result['delivery'] is None
    assert result['phases']['review']['state'] == 'failed'
    assert result['phases']['collect']['state'] == 'skipped'
    assert result['phases']['commit']['state'] == 'skipped'
    assert str(canonical.resolve()) in result['phases']['review']['detail']
    assert canonical.read_bytes() == b'canonical review\n'
    assert requested.read_bytes() == b'other review\n'


def test_finalizer_discards_ready_when_surface_drifts_during_fresh_assess(
        monkeypatch, tmp_path):
    review = tmp_path / '测试书_review.md'
    review.write_bytes(b'agent narrative\n')
    _install_finalizer_review(monkeypatch, tmp_path, review)
    seals = iter(('seal-v1', 'seal-v1', 'seal-v1', 'seal-after-assess'))
    seal_calls: list[dict] = []
    events: list[str] = []
    monkeypatch.setattr(finalizer, 'collect', lambda *_args, **_kwargs: _facts())

    def surface_seal(*_args, **kwargs):
        seal_calls.append(kwargs)
        return next(seals)

    monkeypatch.setattr(finalizer, 'read_surface_seal', surface_seal)
    monkeypatch.setattr(
        finalizer, 'render',
        lambda *_args: '> 交付状态：`ready`\nvalid candidate')
    monkeypatch.setattr(finalizer, 'validate', lambda *_args, **_kwargs: [])

    def commit(path, text, **kwargs):
        events.append('commit')
        return synchronizer.commit_review(path, text, **kwargs)

    monkeypatch.setattr(finalizer, 'commit_review', commit)
    monkeypatch.setattr(
        finalizer, 'assess',
        lambda *_args, **_kwargs: events.append('assess') or _delivery('ready'))

    result = finalizer.finalize(
        tmp_path, 'book-1', started_at=100,
        start_code_version='code-v1', review_path=review)

    assert events == ['commit', 'assess']
    assert len(seal_calls) == 4
    assert all(row == {
        'include_final_evidence_service': True,
        'excluded_review': review.resolve(),
    } for row in seal_calls)
    assert review.read_text(encoding='utf-8') == (
        '> 交付状态：`ready`\nvalid candidate')
    assert result['state'] == 'stopped' and result['delivery'] is None
    assert result['phases']['commit']['state'] == 'passed'
    assert result['phases']['seal']['state'] == 'failed'
    assert result['phases']['delivery']['state'] == 'failed'
    assert '必须重跑 canonical finalizer' in result['phases']['seal']['detail']


def test_finalizer_main_only_returns_zero_for_a_fresh_readyish_delivery(
        monkeypatch, capsys):
    monkeypatch.setattr(
        finalizer, 'finalize',
        lambda *_args, **_kwargs: {
            'state': 'ready_with_disclosures', 'review': 'review.md',
            'phases': {}, 'delivery': {},
        })
    assert finalizer.main([
        '--base', 'outputs', '--sid', 'book-1', '--started-at', '100',
        '--start-code-version', 'code-v1',
    ]) == 0
    assert json.loads(capsys.readouterr().out)['state'] == 'ready_with_disclosures'

    monkeypatch.setattr(
        finalizer, 'finalize',
        lambda *_args, **_kwargs: {
            'state': 'unknown', 'review': 'review.md',
            'phases': {}, 'delivery': {},
        })
    assert finalizer.main([
        '--base', 'outputs', '--sid', 'book-1', '--started-at', '100',
        '--start-code-version', 'code-v1',
    ]) == 1
