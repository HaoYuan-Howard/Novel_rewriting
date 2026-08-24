#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""终审事实收集器/实例校验器的纯函数守卫；不吃语料、不写盘。"""
from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path


SCRIPT_DIR = (Path(__file__).resolve().parent.parent / '.claude' / 'skills'
              / 'novel-pipeline-operator' / 'scripts')
sys.path.insert(0, str(SCRIPT_DIR))
import validate_review as validator  # noqa: E402


def _facts() -> dict:
    counts = {kind: 0 for kind in (
        'invalid', 'rejected', 'stalled', 'repairable', 'human_only',
        'verification', 'upstream', 'approval')}
    exits = [{'node': 'stage7.assemble', 'exit_code': 0}]
    exit_digest = hashlib.sha256(json.dumps(
        exits, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')).hexdigest()
    anchor = {
        'schema': 'novel-final-evidence-v2', 'sid': 'book-1', 'title': '测试书',
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
        f'| stage{stage}.check | fresh/match | 0/1 | 0/1 | 1 | 1 | 0 | '
        'badge=amber; passed=true |'
        for stage in range(8)
    ]
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
