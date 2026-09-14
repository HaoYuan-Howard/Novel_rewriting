#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent adopted-model 两路证据的关闭式单元测试。"""
from __future__ import annotations

import pytest

from contracts.schema.orchestration import AgentProfile
from novel.agents.model_identity import (
    AgentModelIdentityRefused, resolve_agent_model_identity,
    resolve_ledger_model_identity, validate_checkpoint_model_identity,
)


def _profile(*, driver: str, model: str) -> AgentProfile:
    return AgentProfile(
        driver=driver,
        model=model,
        reasoning_effort='high',
        execution_mode='standard',
    )


@pytest.mark.parametrize('model', ['gpt-5.6-sol', 'gpt-5.6-terra'])
def test_registered_codex_models_use_driver_evidence_without_faking_actual(model):
    profile = _profile(driver='codex', model=model)

    session = resolve_agent_model_identity(
        profile, None, cli_version='codex-cli 0.148.0',
        require_driver_version=True)
    ledger = resolve_ledger_model_identity(
        profile, 'unknown', cli_version='codex-cli 0.148.0',
        require_driver_version=True)

    assert session.model == ledger.model == model
    assert session.evidence == ledger.evidence == 'driver-enforced'
    assert session.actual_model is ledger.actual_model is None


@pytest.mark.parametrize(
    ('driver', 'model'),
    [
        ('claude-code', 'claude-opus-5'),
        ('claude-code', 'claude-sonnet-5'),
        ('codex', 'gpt-5.6-sol'),
    ],
)
def test_nonempty_exact_actual_keeps_provider_reported_path(driver, model):
    resolved = resolve_agent_model_identity(
        _profile(driver=driver, model=model), model,
        cli_version='provider-cli/test', require_driver_version=True)

    assert resolved.model == model
    assert resolved.evidence == 'provider-reported'
    assert resolved.actual_model == model


def test_codex_conflicting_actual_cannot_fall_back_to_driver_evidence():
    with pytest.raises(AgentModelIdentityRefused) as raised:
        resolve_agent_model_identity(
            _profile(driver='codex', model='gpt-5.6-sol'),
            'gpt-5.6-terra', cli_version='codex-cli 0.148.0',
            require_driver_version=True)

    assert raised.value.kind == 'provider_metadata_mismatch'
    assert raised.value.loc == 'actual_model'


def test_unregistered_missing_actual_and_unversioned_driver_evidence_fail_closed():
    with pytest.raises(AgentModelIdentityRefused) as claude:
        resolve_agent_model_identity(
            _profile(driver='claude-code', model='claude-opus-5'), None,
            cli_version='claude/test', require_driver_version=True)
    assert claude.value.kind == 'missing_actual_model'

    with pytest.raises(AgentModelIdentityRefused) as codex:
        validate_checkpoint_model_identity(
            _profile(driver='codex', model='gpt-5.6-sol'),
            checkpoint_model='gpt-5.6-sol', actual_model=None,
            cli_version=None, require_driver_version=True)
    assert codex.value.kind == 'missing_cli_version'
