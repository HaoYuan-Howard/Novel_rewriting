#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C5：Agent CLI 时代 ledger.jsonl 必须永久可读（reader ≠ executor decoder）。"""
from __future__ import annotations

import json
from pathlib import Path

from novel.llm.ledger import read_jsonl_rows

_FIXTURE = (
    Path(__file__).resolve().parent
    / 'fixtures' / 'ledger' / 'agent_era_ledger.jsonl'
)

# 历史 Agent CLI error_kind 词表样本（ledger 解码层保留，不重建运行时对象）。
_AGENT_ERA_ERROR_KINDS = frozenset({
    'provider_transient', 'agent_command', 'authentication',
})


def test_agent_era_ledger_fixture_is_never_deleted() -> None:
    assert _FIXTURE.is_file(), (
        'tests/fixtures/ledger/agent_era_ledger.jsonl 是 C5 永久分母，不得删除')


def test_agent_era_ledger_all_rows_readable_and_cost_totals() -> None:
    rows = read_jsonl_rows(_FIXTURE)
    assert len(rows) == 5
    assert all(isinstance(row, dict) for row in rows)

    # 全行可 JSON 往返，且历史 error_kind 原样保留。
    raw_lines = _FIXTURE.read_text(encoding='utf-8').splitlines()
    assert len(raw_lines) == 5
    for line, row in zip(raw_lines, rows, strict=True):
        assert json.loads(line) == row

    seen_errors = {
        row['error_kind'] for row in rows
        if row.get('error_kind') is not None
    }
    assert 'provider_transient' in seen_errors
    assert 'agent_command' in seen_errors
    assert 'authentication' in seen_errors
    assert seen_errors <= {
        'provider_transient', 'authentication', 'usage_limit',
        'permission', 'agent_command', 'agent_protocol',
    }

    # 历史成本合计：token 只加已知整数；reasoning 不重复计入 completion。
    prompt = sum(int(row['prompt_tokens']) for row in rows
                 if isinstance(row.get('prompt_tokens'), int))
    completion = sum(int(row['completion_tokens']) for row in rows
                     if isinstance(row.get('completion_tokens'), int))
    reasoning = sum(int(row['reasoning_tokens']) for row in rows
                    if isinstance(row.get('reasoning_tokens'), int))
    assert prompt == 150
    assert completion == 60
    assert reasoning == 10
    # 失败行保持 null，不得被当成 0 参与合计分母洗白。
    null_prompt_rows = [
        row for row in rows if row.get('outcome') == 'error'
        and row.get('prompt_tokens') is None
    ]
    assert len(null_prompt_rows) == 3
