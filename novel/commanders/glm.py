"""智谱 GLM 的受托 Commander；与 Executor 固定同一 CLI 接收端。"""
from __future__ import annotations

from dataclasses import replace
from collections.abc import Mapping, Sequence
from typing import Any

from contracts.schema.orchestration import AgentProfile
from novel.agents.base import AgentConfigurationError, ensure_command_line_fits
from novel.agents.glm import bigmodel_cli_settings
from novel.agents.claude_code import _actual_model
from novel.commanders.base import (
    CommanderConfigurationError, CommanderInvocation, CommanderObservation, CommanderProtocolError,
)
from novel.commanders.claude_code import ClaudeCodeCommanderDriver, _last_metadata


class GlmClaudeCodeCommanderDriver(ClaudeCodeCommanderDriver):
    driver = 'claude-code-glm'
    provider = 'bigmodel'

    def _decode_events(self, events: Sequence[Mapping[str, Any]], *, profile: AgentProfile,
                       exit_code: int, expected_provider_session_id: str | None) -> CommanderObservation:
        observation = super()._decode_events(events, profile=profile, exit_code=exit_code,
            expected_provider_session_id=expected_provider_session_id)
        terminal = [event for event in events if event.get('type') == 'result']
        # system.init 的 model 是 CLI 请求配置；只有回答/计量的回显可证明实际模型。
        actual = _actual_model(terminal[-1], profile.model) if terminal else None
        if actual is None:
            actual = _last_metadata([event for event in events if event.get('type') == 'assistant'], 'model')
        observation = replace(observation, actual_model=actual)
        if exit_code == 0 and actual is None:
            raise CommanderProtocolError('GLM 成功回答没有实际模型回显', partial_observation=observation)
        return observation

    def build_invocation(self, profile: AgentProfile, prompt: str, **kwargs: Any) -> CommanderInvocation:
        try:
            settings = bigmodel_cli_settings()
            invocation = super().build_invocation(profile, prompt, **kwargs)
            # 放在 --allowedTools 之前；不能让设置被 variadic 参数吞进工具白名单。
            argv = (invocation.argv[0], '--safe-mode', *settings, *invocation.argv[1:])
            ensure_command_line_fits(argv)
            return replace(invocation, argv=argv)
        except AgentConfigurationError as exc:
            raise CommanderConfigurationError(str(exc)) from exc
