#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C2b 硬出口：生产代码不得再 import agent 包 / node_agents / agent_runs。"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / 'novel'


def _production_py_files() -> list[Path]:
    out: list[Path] = []
    for path in ROOT.rglob('*.py'):
        rel = path.relative_to(ROOT.parent).as_posix()
        if rel.startswith('novel/agents/'):
            continue
        if rel.endswith('novel/orchestrator/node_agents.py'):
            continue
        if rel.endswith('novel/llm/agent_runs.py'):
            continue
        out.append(path)
    return out


def _hits(pattern: str) -> list[str]:
    rx = re.compile(pattern)
    found: list[str] = []
    for path in _production_py_files():
        text = path.read_text(encoding='utf-8')
        if rx.search(text):
            found.append(path.relative_to(ROOT.parent).as_posix())
    return found


@pytest.mark.parametrize(
    'label,pattern',
    [
        ('novel.agents', r'from novel\.agents|import novel\.agents'),
        ('node_agents', r'novel\.orchestrator\.node_agents'),
        ('agent_runs', r'novel\.llm\.agent_runs|from novel\.llm import agent_runs'),
    ],
)
def test_c2b_production_import_gate(label: str, pattern: str) -> None:
    found = _hits(pattern)
    assert found == [], f'C2b 生产仍引用 {label}: {found}'
