#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""终审事实收集器/实例校验器的纯函数守卫；不吃语料、不写盘。"""
from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace


SCRIPT_DIR = (Path(__file__).resolve().parent.parent / '.claude' / 'skills'
              / 'novel-pipeline-operator' / 'scripts')
sys.path.insert(0, str(SCRIPT_DIR))
import validate_review as validator  # noqa: E402
import collect_final_evidence as collector  # noqa: E402
import sync_review_evidence as synchronizer  # noqa: E402


CURRENT_QUALITY_STAGES = (
    'stage0', 'stage1', 'stage2', 'stage4', 'stage5', 'stage6', 'stage7',
)


def _facts() -> dict:
    counts = {kind: 0 for kind in (
        'invalid', 'rejected', 'stalled', 'repairable', 'human_only',
        'verification', 'upstream', 'approval')}
    exits = [{'node': 'stage7.assemble', 'exit_code': 0}]
    exit_digest = hashlib.sha256(json.dumps(
        exits, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')).hexdigest()
    anchor = {
        'schema': 'novel-final-evidence-v3', 'sid': 'book-1', 'title': '测试书',
        'session_started_at': 100, 'start_code_version': 'code-v1',
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
        'scope': 'session', 'started_at': 100, 'by_node': {},
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
                              'started_at': None},
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


def test_ledger_and_exit_evidence_are_scoped_to_this_session(monkeypatch, tmp_path):
    rows = [
        {'sid': 'book-1', 'outcome': 'ok', 'node': 'stage2.global',
         'at': '1970-01-01T00:01:30+00:00', 'cached': False,
         'prompt_tokens': 90, 'completion_tokens': 9, 'reasoning_tokens': 1,
         'latency_ms': 900},
        {'sid': 'book-1', 'outcome': 'ok', 'node': 'stage2.global',
         'at': '1970-01-01T00:01:50+00:00', 'cached': False,
         'prompt_tokens': 110, 'completion_tokens': 11, 'reasoning_tokens': 2,
         'latency_ms': 1100},
        {'sid': 'book-1', 'outcome': 'ok', 'node': 'stage5.write',
         'at': '1970-01-01T00:02:00+00:00', 'cached': True},
        {'sid': 'other', 'outcome': 'ok', 'node': 'stage2.global',
         'at': '1970-01-01T00:02:00+00:00', 'cached': False},
    ]
    monkeypatch.setattr(collector, '_jsonl', lambda _path: rows)
    monkeypatch.setattr(collector, 'ledger_path', lambda _base: tmp_path / 'ledger.jsonl')

    session = collector._ledger(tmp_path, 'book-1', started_at=100)
    cumulative = collector._ledger(tmp_path, 'book-1')
    events = [
        SimpleNamespace(sid='book-1', phase='end', ts=99,
                        node='stage2.global', exit_code=0),
        SimpleNamespace(sid='book-1', phase='end', ts=100,
                        node='stage5.write', exit_code=1),
        SimpleNamespace(sid='other', phase='end', ts=101,
                        node='stage7.assemble', exit_code=0),
    ]

    assert session['total']['calls_paid'] == 1
    assert session['total']['calls_cached'] == 1
    assert session['total']['prompt_tokens'] == 110
    assert cumulative['total']['calls_paid'] == 2
    assert collector._session_exits(events, 'book-1', 100) == [
        {'node': 'stage5.write', 'exit_code': 1}]


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
