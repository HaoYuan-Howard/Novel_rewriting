#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent capability registry 与动态只读 API 的关闭式守卫。"""
from __future__ import annotations

import subprocess
from typing import get_args

import pytest
from pydantic import ValidationError

from contracts.invariants import (
    AGENT_EXECUTOR_NODES, NODES, AgentAvailability, AgentDriver, AgentRole,
)
from contracts.schema.orchestration import AgentProfile, PipelineAgentProfiles
from novel import agent_capabilities as capabilities


def _profile(*, driver: str = 'codex', model: str = 'gpt-5.6-sol',
             effort: str = 'high', mode: str | None = None) -> AgentProfile:
    return AgentProfile(
        driver=driver, model=model, reasoning_effort=effort,
        execution_mode=mode,
    )


def test_executor_nodes_are_exactly_the_eight_paid_llm_nodes():
    """付费节点新增/退役时，executor 配置面不能静默漏接或误接代码节点。"""
    assert len(AGENT_EXECUTOR_NODES) == 8
    assert set(AGENT_EXECUTOR_NODES) == {
        node for node, spec in NODES.items() if spec.paid
    }


def test_registry_publishes_exact_ids_labels_and_model_specific_options():
    def must_not_run(*_args, **_kwargs):
        raise AssertionError('PATH 缺 CLI 时不应启动任何子进程')

    response = capabilities.get_agent_capabilities(
        which=lambda _name: None, runner=must_not_run)
    drivers = {driver.id: driver for driver in response.drivers}

    assert response.executor_nodes == list(AGENT_EXECUTOR_NODES)
    assert response.default_profiles is None, (
        'Phase7 前没有 provider-neutral 默认；前端不得拿数组第一项猜')
    assert set(drivers) == set(get_args(AgentDriver)) == {'claude-code', 'codex'}
    assert {driver_id: driver.provider for driver_id, driver in drivers.items()} == {
        'claude-code': 'anthropic', 'codex': 'openai',
    }
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
    assert resolved.executor_default.execution_mode == 'standard'
    assert resolved.executor_overrides['stage6.semantic'].execution_mode == 'standard'
    assert profiles.commander.execution_mode is None, '请求对象不应被原地改写'


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
    assert [driver.availability for driver in response.drivers] == ['ready', 'ready']
    assert [driver.version for driver in response.drivers] == [
        'Claude Code 1.2.3', 'Codex CLI 1.2.3',
    ]
    assert len(calls) == 2
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
        request_index = call // 2
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
    assert call == 4, '动态机器状态被缓存了，前端会一直看到旧安装/登录状态'
