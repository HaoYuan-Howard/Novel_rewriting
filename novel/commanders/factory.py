#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Commander profile → vendor driver 的唯一工厂；未知配置绝不 fallback。"""
from __future__ import annotations

from contracts.schema.orchestration import AgentProfile
from novel.agents.capabilities import validate_profile
from novel.commanders.base import (
    CommanderDriver,
    CommanderRunner,
    RunnerCallable,
)
from novel.commanders.claude_code import ClaudeCodeCommanderDriver
from novel.commanders.codex import CodexCommanderDriver
from novel.commanders.glm import GlmClaudeCodeCommanderDriver


def create_commander_driver(
    profile: AgentProfile,
    *,
    executable: str | None = None,
    timeout: float = 86_400,
    runner: CommanderRunner | RunnerCallable | None = None,
) -> CommanderDriver:
    """按已登记 commander 能力选择 driver；失败不改模型、不换厂商。"""
    resolved = validate_profile(profile, role='commander')
    kwargs = {
        'executable': executable,
        'timeout': timeout,
        'runner': runner,
    }
    if resolved.driver == 'claude-code':
        return ClaudeCodeCommanderDriver(**kwargs)
    if resolved.driver == 'claude-code-glm':
        return GlmClaudeCodeCommanderDriver(**kwargs)
    if resolved.driver == 'codex':
        return CodexCommanderDriver(**kwargs)
    raise AssertionError(f'未实现 commander driver {resolved.driver!r}')


__all__ = ['create_commander_driver']
