#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""legacy executor capability registry 与动态只读 API 的关闭式守卫（C3）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.invariants import LLM_EXECUTOR_NODES, NODES
from contracts.schema.orchestration import (
    LegacyExecutorBackend, LegacyLlmProfile, PipelineAgentProfiles,
)
from novel.orchestrator import executor_capabilities as capabilities


def _legacy_profile(*, model: str = 'deepseek-v4-pro',
                    thinking: str = 'on') -> LegacyLlmProfile:
    return LegacyLlmProfile(
        provider='deepseek', model=model,
        reasoning_effort='high', thinking_mode=thinking,
    )


def test_executor_nodes_cover_all_paid_llm_nodes():
    # 10 个付费节点（B2 五节点重建；CS-3 bind 退役）：三个确定性 compile／assemble
    # 结构性禁付（executor_content_limit=0）。
    assert len(LLM_EXECUTOR_NODES) == 10
    assert set(LLM_EXECUTOR_NODES) == {
        node for node, spec in NODES.items() if spec.paid
    }


def test_pipeline_profiles_are_legacy_only():
    """C3：新写入只认 legacy；agent wire 构造期拒绝。"""
    with pytest.raises(ValidationError):
        PipelineAgentProfiles(
            executor_default={
                'kind': 'agent',
                'profile': {
                    'driver': 'codex', 'model': 'gpt-5.6-sol',
                    'reasoning_effort': 'high',
                },
            },
        )
    flash_default = PipelineAgentProfiles(
        executor_default=LegacyExecutorBackend(
            profile=_legacy_profile(model='deepseek-flash')),
        executor_overrides={},
    )
    resolved = capabilities.validate_pipeline_profiles(flash_default)
    assert resolved.schema_version == 4
    assert resolved.executor_default.kind == 'legacy'
    assert resolved.executor_overrides == {}


def test_pipeline_profiles_discard_agent_overrides_on_load():
    """加载丢弃 agent 覆盖；default 仍须为 legacy。"""
    profiles = PipelineAgentProfiles.model_validate({
        'schema_version': 3,
        'executor_default': {
            'kind': 'legacy',
            'profile': _legacy_profile().model_dump(mode='json'),
        },
        'executor_overrides': {
            'stage5.write': {
                'kind': 'agent',
                'profile': {
                    'driver': 'codex', 'model': 'gpt-5.6-sol',
                    'reasoning_effort': 'high',
                },
            },
        },
    })
    assert profiles.schema_version == 4
    assert 'stage5.write' not in profiles.executor_overrides


def test_get_agent_capabilities_is_legacy_only():
    response = capabilities.get_agent_capabilities()
    assert not hasattr(response, 'drivers') or getattr(response, 'drivers', None) is None
    assert response.model_dump().get('drivers') is None
    assert response.executor_nodes == list(LLM_EXECUTOR_NODES)
    assert response.legacy_providers
    assert all(p.id for p in response.legacy_providers)


_WEB_CAPABILITIES = Path(__file__).resolve().parents[1] / 'web' / 'src' / 'fixtures' / 'backend_v1' / 'agent_capabilities.json'


def test_the_web_capabilities_fixture_is_the_same_payload():
    """⭐ `web/src/fixtures/backend_v1/agent_capabilities.json` == `get_agent_capabilities()`。

    与 `tests/test_api_enums.py` 的 enums 夹具同款唯一对账落点：mock 模式下前端
    读到的能力面（含并发值域）是这份夹具，不是 registry。没有这条对账，夹具会
    停在旧契约——2026-10-05 合并复审实证过 total_max=32／node_choices=[4,6,8]
    对真实 1~200／[] 的静默分叉（mock 校验 1~32、真实模式 1~200）。
    比解析后的对象不比字节（Windows 检出的 CRLF 会红在换行上）。
    """
    fixture = json.loads(_WEB_CAPABILITIES.read_text(encoding='utf-8'))
    live = json.loads(json.dumps(
        capabilities.get_agent_capabilities().model_dump(mode='json')))

    missing = sorted(set(live) - set(fixture))
    stale = sorted(set(fixture) - set(live))
    changed = sorted(key for key in set(live) & set(fixture)
                     if live[key] != fixture[key])
    assert (missing, stale, changed) == ([], [], []), (
        f'capability 夹具与 get_agent_capabilities() 分家了：\n'
        f'  夹具缺少 {missing}；夹具多出 {stale}；内容不同 {changed}\n'
        f'  并发面——夹具={fixture.get("concurrency")}，现役={live.get("concurrency")}\n'
        f'→ 按当前 registry 输出重录夹具（web/src/fixtures/backend_v1/README.md）'
    )


def test_direct_llm_executor_resolves_legacy_wire():
    backend = LegacyExecutorBackend(profile=_legacy_profile())
    executor = capabilities.resolve_direct_llm_executor(backend)
    assert executor.profile.provider == 'deepseek'
    assert executor.profile.model == 'deepseek-v4-pro'


def test_validate_executor_backend_refuses_agent():
    with pytest.raises(Exception):
        capabilities.validate_executor_backend(
            {'kind': 'agent', 'profile': {'driver': 'codex', 'model': 'x',
                                         'reasoning_effort': 'high'}})
