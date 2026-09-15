"""智谱 Claude CLI 的 executor 驱动、接收端、结构化交付和真实计量边界；零模型请求。

【角色边界】受托 Commander 已退役（2026-09-15）：``novel/commanders`` 与
``create_commander_driver`` 已删，GLM driver 只承接 executor；历史 commander
构建/解码用例随之退役，不补造占位流程。
"""
from __future__ import annotations

import json
import subprocess

import pytest

from contracts.schema.orchestration import AgentProfile, PipelineAgentProfiles
from novel.agents.capabilities import (
    AgentProfileRefused, get_agent_capabilities, reported_model_matches,
    provider_for_driver, validate_pipeline_profiles, validate_profile,
)
from novel.agents.model_identity import AgentModelIdentityRefused, resolve_agent_model_identity
from novel.agents import base as agent_base
from novel.agents.factory import create_driver
from novel.agents.glm import BIGMODEL_BASE_URL, GlmClaudeCodeDriver, bigmodel_cli_settings


PROFILE = AgentProfile(driver='claude-code-glm', model='glm-5.3-flash[1M]',
                       reasoning_effort='max', execution_mode='standard')
SCHEMA = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
          'required': ['ok'], 'additionalProperties': False}


@pytest.fixture
def glm_config(tmp_path, monkeypatch):
    config = tmp_path / 'config'
    config.mkdir()
    settings = config / 'settings.json'
    settings.write_text(json.dumps({'env': {'ANTHROPIC_BASE_URL': BIGMODEL_BASE_URL,
        'ANTHROPIC_AUTH_TOKEN': 'test-private-token', 'ANTHROPIC_MODEL': 'other-model'}}), encoding='utf-8')
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(config))
    return settings


@pytest.mark.parametrize('effort', ['low', 'high', 'max'])
def test_glm_capability_and_executor_role_are_closed(effort, monkeypatch):
    profile = PROFILE.model_copy(update={'reasoning_effort': effort})
    resolved = validate_pipeline_profiles(PipelineAgentProfiles(
        executor_default=profile, executor_overrides={}))
    assert resolved.executor_default.profile == profile
    assert provider_for_driver(profile.driver) == 'bigmodel'
    capability = next(row for row in get_agent_capabilities(which=lambda _: None).drivers
                      if row.id == profile.driver)
    assert capability.models[0].reasoning_efforts == ['low', 'high', 'max']
    assert capability.availability == 'missing'
    monkeypatch.setattr(agent_base, 'resolve_cli_entry', lambda *args: 'claude.exe')
    with create_driver(profile) as executor:
        assert executor.provider == 'bigmodel'


@pytest.mark.parametrize('updates', [
    {'reasoning_effort': 'medium'}, {'reasoning_effort': 'xhigh'},
    {'model': 'glm-5.3'}, {'model': 'claude-opus-5'}, {'driver': 'claude-code'},
])
def test_glm_rejects_unregistered_combinations(updates):
    with pytest.raises(AgentProfileRefused):
        validate_profile(PROFILE.model_copy(update=updates))


@pytest.mark.parametrize('actual', ['glm-5.3-flash', 'glm-5.3-flash[1m]', 'glm-5.3-flash[1M]'])
def test_glm_context_suffix_is_explicit_without_rewriting_actual(actual):
    identity = resolve_agent_model_identity(PROFILE, actual)
    assert identity.model == PROFILE.model
    assert identity.actual_model == actual and identity.evidence == 'provider-reported'
    assert reported_model_matches(PROFILE, actual)


@pytest.mark.parametrize('actual', [None, 'glm-5.3', 'glm-5.3-flash-other', 'claude-opus-5'])
def test_glm_cannot_infer_unreported_or_other_model(actual):
    with pytest.raises(AgentModelIdentityRefused):
        resolve_agent_model_identity(PROFILE, actual)


def test_glm_executor_pins_recipient_and_model_and_keeps_resume(glm_config, tmp_path):
    original = glm_config.read_bytes()
    cwd = tmp_path / 'empty'
    cwd.mkdir()
    invocations = []
    def runner(argv, **kwargs):
        invocations.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': 'glm-session',
            'num_turns': 1, 'structured_output': {'ok': True},
            'modelUsage': {'glm-5.3-flash': {'inputTokens': 8, 'outputTokens': 3,
                'cacheReadInputTokens': 5, 'cacheCreationInputTokens': 2}},
        }), '')
    driver = GlmClaudeCodeDriver(executable='claude.exe', runner=runner)
    request = agent_base.AgentRequest(prompt='测试结构化交付', cwd=str(cwd), output_schema=SCHEMA,
        execution_policy='executor-isolated',
        delivery_protocol=agent_base.agent_delivery_protocol(driver=PROFILE.driver, has_output_schema=True))
    turn = driver.start(PROFILE, request)
    assert turn.model == 'glm-5.3-flash' and turn.actual_reasoning_effort is None
    assert turn.usage.prompt_tokens == 15 and turn.usage.total_tokens == 18
    assert turn.usage.reasoning_tokens is None
    driver.resume(PROFILE, turn.session_id, request)
    for argv, kwargs in invocations:
        assert argv[argv.index('--model') + 1] == PROFILE.model
        assert argv[argv.index('--effort') + 1] == 'max'
        assert '--safe-mode' in argv and '--fallback-model' not in argv
        assert argv[argv.index('--tools') + 1] == ''
        settings = json.loads(argv[argv.index('--settings') + 1])
        assert settings['env']['ANTHROPIC_BASE_URL'] == BIGMODEL_BASE_URL
        assert 'test-private-token' not in str(argv)
        assert 'claude-structured-output/v1' in str(kwargs['input'])
    assert invocations[1][0][invocations[1][0].index('--resume') + 1] == 'glm-session'
    assert glm_config.read_bytes() == original and not list(cwd.iterdir())


@pytest.mark.parametrize('endpoint', [None, 'https://api.anthropic.com', 'https://open.bigmodel.cn.evil.invalid/api/anthropic'])
def test_glm_wrong_or_missing_recipient_is_rejected_before_spawn(glm_config, endpoint):
    glm_config.write_text(json.dumps({'env': {'ANTHROPIC_BASE_URL': endpoint}}), encoding='utf-8')
    with pytest.raises(agent_base.AgentConfigurationError, match='接收端'):
        bigmodel_cli_settings()
