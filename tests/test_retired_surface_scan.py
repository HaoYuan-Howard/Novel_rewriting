#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C5：退役 Agent CLI 表面的生产静态扫描。

生产 ``novel/`` 对下列符号的命中必须为 0；唯一允许按 ``kind == 'agent'``
做格式判别的代码是 ``novel/orchestrator/retired_formats.py``。
docs、历史 fixtures、ledger 词表与测试不在本闸范围内。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOVEL = ROOT / 'novel'

#: 生产代码唯一允许出现 ``kind == 'agent'`` 字面判别的模块。
_ALLOWLIST = frozenset({
    'novel/orchestrator/retired_formats.py',
})

#: (标签, 正则)。对标识符类模式在去掉注释/字符串后再匹配，避免误伤
#: stage5 legacyNotice 等无关「legacy」文案，以及历史注释里的退役符号名。
_PATTERNS: tuple[tuple[str, str, bool], ...] = (
    ('novel.agents import', r'(?:from|import)\s+novel\.agents\b', False),
    ('build_node_agent', r'\bbuild_node_agent\b', True),
    ('AgentProfile', r'\bAgentProfile\b', True),
    ('AgentExecutorBackend', r'\bAgentExecutorBackend\b', True),
    ('agent_mode', r'\bagent_mode\b', True),
    ('_uses_agent_sessions', r'\b_uses_agent_sessions\b', True),
    ('SessionAgentClient', r'\bSessionAgentClient\b', True),
    ('unit_session_pool', r'\bunit_session_pool\b', True),
    ('--executor-driver', r'--executor-driver\b', False),
    ('--executor-mode', r'--executor-mode\b', False),
    # 生产分支判别：用 executor_profile.kind / ['kind'] 选择 agent 臂。
    # LegacyLlmProfile 投影字段名本身仍可存在，不在本闸。
    ('executor_profile agent branch',
     r"executor_profile(?:\.kind|\[['\"]kind['\"]\])\s*==\s*['\"]agent['\"]",
     True),
# kind == agent：对全文件（含注释）扫描，避免注释里残留字面量。
    ('kind == agent', r"kind\s*==\s*['\"]agent['\"]", False),
)


def _strip_python_noise(source: str) -> str:
    """去掉 # 注释与普通/三引号字符串，保留换行以便报告行号近似。"""
    out: list[str] = []
    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        if ch == '#':
            while i < n and source[i] != '\n':
                i += 1
            continue
        if source.startswith('"""', i) or source.startswith("'''", i):
            quote = source[i:i + 3]
            j = source.find(quote, i + 3)
            if j < 0:
                break
            # 保留换行数，方便对照
            out.append('\n' * source[i:j].count('\n'))
            i = j + 3
            continue
        if ch in ('"', "'"):
            quote = ch
            i += 1
            while i < n:
                if source[i] == '\\':
                    i += 2
                    continue
                if source[i] == quote:
                    i += 1
                    break
                if source[i] == '\n':
                    out.append('\n')
                i += 1
            continue
        out.append(ch)
        i += 1
    return ''.join(out)


def _production_files() -> list[Path]:
    return sorted(
        path for path in NOVEL.rglob('*.py')
        if '__pycache__' not in path.parts
    )


def _hits(label: str, pattern: str, *, strip_noise: bool) -> list[str]:
    rx = re.compile(pattern)
    found: list[str] = []
    for path in _production_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel in _ALLOWLIST:
            continue
        raw = path.read_text(encoding='utf-8')
        text = _strip_python_noise(raw) if strip_noise else raw
        for index, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                found.append(f'{rel}:{index}:{line.strip()[:160]}')
    return found


@pytest.mark.parametrize(
    'label,pattern,strip_noise',
    _PATTERNS,
    ids=[row[0] for row in _PATTERNS],
)
def test_retired_surface_absent_from_production(
        label: str, pattern: str, strip_noise: bool) -> None:
    found = _hits(label, pattern, strip_noise=strip_noise)
    assert found == [], (
        f'C5 生产仍残留退役表面 {label!r}（允许名单仅 retired_formats）：\n'
        + '\n'.join(found)
    )


def test_retired_formats_is_sole_kind_agent_discriminator() -> None:
    """``kind == 'agent'`` 字面只允许出现在 retired_formats（含其模块头说明）。"""
    rx = re.compile(r"kind\s*==\s*['\"]agent['\"]")
    elsewhere: list[str] = []
    for path in _production_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel in _ALLOWLIST:
            continue
        for index, line in enumerate(
                path.read_text(encoding='utf-8').splitlines(), 1):
            if rx.search(line):
                elsewhere.append(f'{rel}:{index}:{line.strip()}')
    assert elsewhere == [], (
        'kind == \"agent\" 只能留在 retired_formats.py：\n'
        + '\n'.join(elsewhere)
    )
    allowed = (NOVEL / 'orchestrator' / 'retired_formats.py').read_text(
        encoding='utf-8')
    assert rx.search(allowed), 'retired_formats 自身必须保留 kind==agent 判别'
