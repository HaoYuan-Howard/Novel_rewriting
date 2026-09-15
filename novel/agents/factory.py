#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AgentProfile → vendor driver 的唯一工厂；未知配置绝不 fallback。"""
from __future__ import annotations

from contracts.schema.orchestration import AgentProfile
from novel.agents.capabilities import validate_profile
from novel.agents import AgentDriver, ClaudeCodeDriver, CodexDriver
from novel.agents.glm import GlmClaudeCodeDriver


def create_driver(profile: AgentProfile) -> AgentDriver:
    resolved = validate_profile(profile)
    if resolved.driver == 'claude-code':
        return ClaudeCodeDriver()
    if resolved.driver == 'claude-code-glm':
        return GlmClaudeCodeDriver()
    if resolved.driver == 'codex':
        return CodexDriver()
    # Pydantic + registry 正常入口已挡住；保留关闭式穷尽检查。
    raise AssertionError(f'未实现 agent driver {resolved.driver!r}')


__all__ = ['create_driver']
