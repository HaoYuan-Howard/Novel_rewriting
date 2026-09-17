#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Legacy LLM accepted-model 证据的关闭式单元测试（C3：无 Agent CLI driver 路径）。"""
from __future__ import annotations

import pytest

from contracts.schema.orchestration import LegacyLlmProfile
from novel.errors import AgentModelIdentityRefused
from novel.llm.model_identity import (
    resolve_agent_model_identity,
    resolve_ledger_model_identity,
    validate_checkpoint_model_identity,
)


def _profile(*, model: str = 'deepseek-v4-pro') -> LegacyLlmProfile:
    return LegacyLlmProfile(
        provider='deepseek', model=model,
        reasoning_effort='high', thinking_mode='on',
    )


def test_provider_reported_actual_matches_requested():
    profile = _profile()
    resolved = resolve_agent_model_identity(profile, 'deepseek-v4-pro')
    assert resolved.model == 'deepseek-v4-pro'
    assert resolved.evidence == 'provider-reported'
    assert resolved.actual_model == 'deepseek-v4-pro'


def test_missing_actual_fails_closed():
    with pytest.raises(AgentModelIdentityRefused) as raised:
        resolve_agent_model_identity(_profile(), None)
    assert raised.value.kind == 'missing_actual_model'


def test_mismatched_actual_fails_closed():
    with pytest.raises(AgentModelIdentityRefused) as raised:
        resolve_agent_model_identity(_profile(), 'deepseek-flash')
    assert raised.value.kind == 'provider_metadata_mismatch'


def test_ledger_unknown_is_not_provider_reported():
    with pytest.raises(AgentModelIdentityRefused) as raised:
        resolve_ledger_model_identity(_profile(), 'unknown')
    assert raised.value.kind == 'missing_actual_model'


def test_checkpoint_requires_matching_actual():
    with pytest.raises(AgentModelIdentityRefused):
        validate_checkpoint_model_identity(
            _profile(),
            checkpoint_model='deepseek-v4-pro',
            actual_model=None,
        )
    ok = validate_checkpoint_model_identity(
        _profile(),
        checkpoint_model='deepseek-v4-pro',
        actual_model='deepseek-v4-pro',
    )
    assert ok.model == 'deepseek-v4-pro'
