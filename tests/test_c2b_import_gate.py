#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C3：agent 执行包已删除；生产不得再 import 已删路径。"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_c3_agent_packages_deleted() -> None:
    assert not (ROOT / 'novel' / 'agents').exists()
    assert not (ROOT / 'novel' / 'orchestrator' / 'node_agents.py').exists()
    assert not (ROOT / 'novel' / 'llm' / 'agent_runs.py').exists()
    assert not (ROOT / 'tools' / 'verify_agent_e2e.py').exists()


def _production_py_files() -> list[Path]:
    return list((ROOT / 'novel').rglob('*.py'))


def _hits(pattern: str) -> list[str]:
    rx = re.compile(pattern)
    found: list[str] = []
    for path in _production_py_files():
        text = path.read_text(encoding='utf-8')
        if rx.search(text):
            found.append(path.relative_to(ROOT).as_posix())
    return found


@pytest.mark.parametrize(
    'label,pattern',
    [
        ('novel.agents', r'from novel\.agents|import novel\.agents'),
        ('node_agents', r'novel\.orchestrator\.node_agents'),
        ('agent_runs', r'novel\.llm\.agent_runs|from novel\.llm import agent_runs'),
    ],
)
def test_c3_production_import_gate(label: str, pattern: str) -> None:
    found = _hits(pattern)
    assert found == [], f'C3 生产仍引用 {label}: {found}'
