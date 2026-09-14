#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent capability registry 与动态只读 API 的关闭式守卫。"""
from __future__ import annotations

import subprocess
from typing import get_args

import pytest
from pydantic import ValidationError

from contracts.invariants import (
    AGENT_EXECUTOR_NODES, NODES, AgentAvailability, AgentDriver,
    AgentModelEvidence, AgentRole,
)
from contracts.schema.orchestration import (
    AgentExecutorBackend, AgentProfile, LegacyExecutorBackend, LegacyLlmProfile,
    PipelineAgentProfiles, PipelineConcurrency,
)
from novel.agents import capabilities as capabilities


def _profile(*, driver: str = 'codex', model: str = 'gpt-5.6-sol',
             effort: str = 'high', mode: str | None = None) -> AgentProfile:
    return AgentProfile(
        driver=driver, model=model, reasoning_effort=effort,
        execution_mode=mode,
    )


def test_executor_nodes_cover_all_paid_llm_nodes():
    """付费节点新增/退役时，executor 配置面不能静默漏接或误接代码节点。"""
    assert len(AGENT_EXECUTOR_NODES) == 9
    assert set(AGENT_EXECUTOR_NODES) == {
        node for node, spec in NODES.items() if spec.paid
    }


def test_concurrency_capability_and_full_profiles_roundtrip_preserve_exact_trial():
    from novel.config import load_config, ENV_KEYS

    policy = PipelineConcurrency(node_overrides={'stage4.plan': 8}, per_book=8, per_provider=12)
    profiles = PipelineAgentProfiles(commander=_profile(), executor_default=_profile(), concurrency=policy)
    normalized = capabilities.validate_pipeline_profiles(profiles)
    restored = PipelineAgentProfiles.model_validate_json(normalized.model_dump_json())
    assert restored.concurrency == policy
    cfg = load_config(env={ENV_KEYS['concurrency_policy']: policy.model_dump_json()})
    assert cfg.concurrency_for('stage4.plan') == 8
    assert cfg.concurrency_for('stage2.blueprint') == 4
    assert cfg.concurrency_policy.per_provider == 12
    cap = capabilities.get_agent_capabilities(which=lambda _name: None).concurrency
    assert cap.default_node == cap.defaults.per_book == 4
    assert cap.node_choices == [4, 6, 8]
    assert set(cap.nodes) == {'stage2.blueprint', 'stage4.design', 'stage4.plan', 'stage5.write'}


@pytest.mark.parametrize('policy', [
    {'node_overrides': {'stage2.global': 8}},
    {'node_overrides': {'stage5.write': 7}},
    {'per_book': True}, {'per_provider': 0}, {'per_provider': 33},
])
def test_concurrency_policy_rejects_unregistered_or_invalid_values(policy):
    with pytest.raises(ValidationError):
        PipelineConcurrency.model_validate(policy)


def test_missing_concurrency_keeps_original_profile_bytes_and_empty_env_is_invalid():
    from novel.config import load_config, ENV_KEYS, Config

    profiles = PipelineAgentProfiles(commander=_profile(), executor_default=_profile())
    assert 'concurrency' not in profiles.model_dump(mode='json')
    assert 'concurrency' not in capabilities.validate_pipeline_profiles(profiles).model_dump(mode='json')
    with pytest.raises(ValueError):
        load_config(env={ENV_KEYS['concurrency_policy']: ''})
    with pytest.raises(ValueError, match='PipelineConcurrency'):
        Config(concurrency_policy={})


def test_registry_publishes_exact_ids_labels_and_model_specific_options():
    def must_not_run(*_args, **_kwargs):
        raise AssertionError('PATH 缺 CLI 时不应启动任何子进程')

    response = capabilities.get_agent_capabilities(
        which=lambda _name: None, runner=must_not_run)
    drivers = {driver.id: driver for driver in response.drivers}

    assert response.executor_nodes == list(AGENT_EXECUTOR_NODES)
    assert response.default_profiles is None, (
        'Phase7 前没有 provider-neutral 默认；前端不得拿数组第一项猜')
    assert set(drivers) == set(get_args(AgentDriver)) == {'claude-code', 'claude-code-glm', 'codex'}
    assert {driver_id: driver.provider for driver_id, driver in drivers.items()} == {
        'claude-code': 'anthropic', 'claude-code-glm': 'bigmodel', 'codex': 'openai',
    }
    assert capabilities.provider_for_driver('claude-code') == 'anthropic'
    assert capabilities.provider_for_driver('codex') == 'openai'
    assert set(get_args(AgentModelEvidence)) == {
        'provider-reported', 'driver-enforced',
    }
    assert capabilities.unreported_model_evidence_for_driver('codex') \
        == 'driver-enforced'
    assert capabilities.unreported_model_evidence_for_driver('claude-code') is None
    assert all(set(driver.roles) == set(get_args(AgentRole))
               for driver in drivers.values())
    assert set(get_args(AgentAvailability)) == {
        'ready', 'missing', 'unknown', 'unauthenticated',
    }
    assert all(driver.availability == 'missing' for driver in drivers.values())

    claude = {model.id: model for model in drivers['claude-code'].models}
    assert {model_id: model.label for model_id, model in claude.items()} == {
        'claude-opus-5': 'Claude Opus 5',
        'claude-sonnet-5': 'Claude Sonnet 5',
    }
    codex = {model.id: model for model in drivers['codex'].models}
    assert {model_id: model.label for model_id, model in codex.items()} == {
        'gpt-5.6-sol': 'GPT-5.6 Sol',
        'gpt-5.6-terra': 'GPT-5.6 Terra',
    }
    for model in codex.values():
        assert model.reasoning_efforts == [
            'none', 'low', 'medium', 'high', 'xhigh', 'max',
        ]
    assert all(model.execution_modes == ['standard']
               for driver in drivers.values() for model in driver.models)
    assert all('ultra' not in model.reasoning_efforts
               and 'ultra' not in model.execution_modes
               for driver in drivers.values() for model in driver.models)
    assert [provider.id for provider in response.legacy_providers] == ['deepseek']
    deepseek = response.legacy_providers[0]
    assert [model.id for model in deepseek.models] == [
        'deepseek-v4-flash', 'deepseek-v4-pro',
    ]
    assert set(deepseek.models[1].executor_nodes) == set(AGENT_EXECUTOR_NODES)


def test_profile_validation_is_closed_over_driver_model_effort_and_mode():
    unresolved = _profile()
    resolved = capabilities.validate_profile(unresolved, role='executor')
    assert resolved.model == 'gpt-5.6-sol'
    assert resolved.execution_mode == 'standard'
    assert unresolved.execution_mode is None and resolved is not unresolved
    assert capabilities.validate_profile(
        _profile(mode='standard'), role='commander').execution_mode == 'standard'

    with pytest.raises(capabilities.AgentProfileRefused, match='不支持 model'):
        capabilities.validate_profile(
            _profile(model='claude-opus-5'), role='executor')
    with pytest.raises(capabilities.AgentProfileRefused, match='reasoning_effort'):
        capabilities.validate_profile(
            _profile(driver='claude-code', model='claude-sonnet-5', effort='none'),
            role='executor',
        )
    with pytest.raises(capabilities.AgentProfileRefused, match='execution_mode'):
        capabilities.validate_profile(_profile(mode='ultra'), role='executor')


def test_executor_overrides_reject_code_and_approval_nodes():
    with pytest.raises(ValidationError, match='非 executor 节点'):
        PipelineAgentProfiles(
            commander=_profile(), executor_default=_profile(),
            executor_overrides={'stage4.context': _profile()},
        )
    profiles = PipelineAgentProfiles(
        commander=_profile(), executor_default=_profile(),
        executor_overrides={'stage6.semantic': _profile(model='gpt-5.6-terra')},
    )
    resolved = capabilities.validate_pipeline_profiles(profiles)
    assert resolved is not profiles
    assert resolved.commander.execution_mode == 'standard'
    assert isinstance(resolved.executor_default, AgentExecutorBackend)
    assert resolved.executor_default.profile.execution_mode == 'standard'
    override = resolved.executor_overrides['stage6.semantic']
    assert isinstance(override, AgentExecutorBackend)
    assert override.profile.execution_mode == 'standard'
    assert profiles.commander.execution_mode is None, '请求对象不应被原地改写'


def test_legacy_backend_is_closed_over_provider_model_thinking_effort_and_node():
    pro = LegacyExecutorBackend(profile=LegacyLlmProfile(
        provider='deepseek', model='deepseek-v4-pro',
        reasoning_effort='high', thinking_mode='on'))
    assert capabilities.validate_executor_backend(
        pro, node='stage6.semantic') == pro

    flash = LegacyExecutorBackend(profile=LegacyLlmProfile(
        provider='deepseek', model='deepseek-v4-flash',
        reasoning_effort='high', thinking_mode='on'))
    with pytest.raises(capabilities.AgentProfileRefused, match='钉死 deepseek-v4-pro'):
        capabilities.validate_executor_backend(flash, node='stage6.semantic')
    with pytest.raises(capabilities.AgentProfileRefused, match='reasoning_effort'):
        capabilities.validate_executor_backend(
            LegacyExecutorBackend(profile=LegacyLlmProfile(
                provider='deepseek', model='deepseek-v4-pro',
                reasoning_effort='high', thinking_mode='off')),
            node='stage6.semantic',
        )


def test_capability_probe_and_runtime_share_the_same_safe_windows_entry(tmp_path):
    native = tmp_path / 'node_modules' / 'vendor' / 'claude.exe'
    native.parent.mkdir(parents=True)
    native.write_bytes(b'not executed')
    shim = tmp_path / 'claude.cmd'
    shim.write_text(
        '@ECHO off\n"%dp0%\\node_modules\\vendor\\claude.exe" %*\n',
        encoding='utf-8')
    seen: list[list[str]] = []

    def runner(argv, **_kwargs):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, 'Claude 9.9\n', '')

    spec = capabilities._DRIVER_BY_ID['claude-code']
    probed = capabilities._probe_driver(
        spec, which=lambda _name: str(shim), runner=runner)
    assert probed.availability == 'ready'
    assert seen == [[str(native.resolve()), '--version']]
    assert all('cmd' not in part.casefold() for part in seen[0])

    bad = tmp_path / 'bad.cmd'
    bad.write_text('@node script.js %*\n', encoding='utf-8')
    seen.clear()
    refused = capabilities._probe_driver(
        spec, which=lambda _name: str(bad), runner=runner)
    assert refused.availability == 'unknown'
    assert '不可安全执行' in refused.reason
    assert seen == [], '真实 runtime 会拒绝的 shim 不得先经 cmd 探测成 ready'


def test_probe_only_executes_the_fixed_version_command():
    calls: list[tuple[list[str], dict[str, object]]] = []

    def which(name: str) -> str:
        return f'C:/agent-bin/{name}.exe'

    def runner(argv, **kwargs):
        calls.append((list(argv), dict(kwargs)))
        name = 'Claude Code' if 'claude' in argv[0] else 'Codex CLI'
        return subprocess.CompletedProcess(argv, 0, f'{name} 1.2.3\n', '')

    response = capabilities.get_agent_capabilities(which=which, runner=runner)
    assert [driver.availability for driver in response.drivers] == ['ready', 'ready', 'ready']
    assert [driver.version for driver in response.drivers] == [
        'Claude Code 1.2.3', 'Claude Code 1.2.3', 'Codex CLI 1.2.3',
    ]
    assert len(calls) == 3
    for argv, kwargs in calls:
        assert argv[-1] == '--version' and len(argv) == 2
        assert kwargs['check'] is False and kwargs['capture_output'] is True
        assert kwargs['timeout'] == capabilities._PROBE_TIMEOUT_SECONDS
        assert not any('prompt' in part.casefold() for part in argv)


def test_probe_distinguishes_unauthenticated_from_unknown_without_a_model_call():
    def which(name: str) -> str:
        return f'/opt/{name}'

    def unauthenticated(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 1, '', 'Please login first')

    response = capabilities.get_agent_capabilities(
        which=which, runner=unauthenticated)
    assert {driver.availability for driver in response.drivers} == {'unauthenticated'}
    assert all(driver.version is None for driver in response.drivers)


def test_http_capabilities_are_reprobed_instead_of_cached(monkeypatch, tmp_path):
    pytest.importorskip('fastapi')
    from fastapi.testclient import TestClient
    from novel.api.app import create_app

    call = 0

    def changing_probe(spec, **_kwargs):
        nonlocal call
        request_index = call // len(capabilities._DRIVERS)
        call += 1
        if request_index == 0:
            return capabilities._ProbeResult(
                availability='missing', reason='测试：尚未安装')
        return capabilities._ProbeResult(
            availability='ready', reason='测试：版本探针成功', version='test 9.9')

    monkeypatch.setattr(capabilities, '_probe_driver', changing_probe)
    with TestClient(create_app(str(tmp_path))) as client:
        first = client.get('/api/agent/capabilities')
        second = client.get('/api/agent/capabilities')

    assert first.status_code == second.status_code == 200
    assert first.headers['cache-control'] == second.headers['cache-control'] == 'no-store'
    assert {item['availability'] for item in first.json()['drivers']} == {'missing'}
    assert {item['availability'] for item in second.json()['drivers']} == {'ready'}
    assert call == 2 * len(capabilities._DRIVERS), '动态机器状态被缓存了，前端会一直看到旧安装/登录状态'
