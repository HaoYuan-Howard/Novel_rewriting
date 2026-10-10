#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 直连（legacy executor）测试共用 RunContext 夹具（C3：无 Agent CLI）。

⚠ **F8b-3 载荷切换**：原文件是旧链 Stage2 Global／Analyze 的共用夹具——它 import 的
`novel.stages.stage2.extract`、`tests.test_stage2`、`tests.test_stage2_generation` 与
`tests.stage2_fixtures` 已随旧链整层删除，整个模块无法装载（收集错误）。本文件**不含
测试用例**（与退役前一致：它是夹具模块，用例在各自文件里）；存活的夹具职责迁到
`tests/str06_facilities.py`（候选上下文与付费边次）与 `tests/conftest.py`。

保留本文件的原因：它是对现役直连 executor 形状（LegacyExecutorBackend /
LegacyLlmProfile——稳定传输名，非旧实现）的断言，存废随被测面。
⇒ 这里只放**新链上仍成立的直连 executor 形状**断言面：legacy profile 的构造与
解析（`novel/cli/main.py::_executor_backend_of` 与 `nodes.resolve_executor_backend`
的共用形状），保证「直连 LLM 是唯一执行面」这条在夹具层也有落点。
"""
from __future__ import annotations

import pytest

from contracts.schema.orchestration import (
    ExecutorBackend,
    LegacyExecutorBackend,
    LegacyLlmProfile,
)
from novel.errors import UnsupportedRunMode
from novel.orchestrator import nodes as N


def _profile() -> LegacyLlmProfile:
    return LegacyLlmProfile(
        provider='deepseek',
        model='deepseek-v4-pro',
        reasoning_effort='high',
        thinking_mode='on',
    )


def _backend() -> LegacyExecutorBackend:
    return LegacyExecutorBackend(profile=_profile())


def test_legacy_profile_is_the_only_executor_shape():
    """直连 LLM 是现役唯一执行面：legacy profile 四件套缺一不可。"""
    with pytest.raises(ValueError):
        LegacyLlmProfile(provider='deepseek', model='deepseek-v4-pro',
                         reasoning_effort='high')
    backend = _backend()
    assert isinstance(backend, ExecutorBackend)
    assert backend.kind == 'legacy'
    assert backend.profile == _profile()


def test_nodes_resolve_legacy_backend_and_refuse_agent_kind():
    """节点表的 executor 解析口只认 legacy；agent kind 与未登记组合关闭式拒绝。"""
    resolved = N.resolve_executor_backend(
        'str06.stage5.body', (), _backend())
    assert isinstance(resolved, LegacyExecutorBackend)
    assert resolved.profile.model == 'deepseek-v4-pro'

    from novel.orchestrator.retired_formats import refuse_agent_backend
    with pytest.raises(Exception):
        refuse_agent_backend('夹具层 agent 形状', {'kind': 'agent'})
    # 未迁移的旧链节点名连显式 profile 都不接（不能静默回退另一套执行面）。
    with pytest.raises(UnsupportedRunMode, match='不再接受显式 executor profile'):
        N.resolve_executor_backend('stage4.design', (), _backend())
