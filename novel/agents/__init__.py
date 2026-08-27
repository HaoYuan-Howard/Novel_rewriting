#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""供应商中立的可恢复 agent CLI 驱动。

业务层只依赖本模块导出的契约；不得越过它去判断 Claude/Codex 的原始 JSON 形状，
也不得在某个驱动失败时静默回退到 DeepSeek 或另一个供应商。
"""
from novel.agents.base import (
    AgentBusyError,
    AgentCommandError,
    AgentConfigurationError,
    AgentDriver,
    AgentError,
    AgentProbe,
    AgentProtocolError,
    AgentRequest,
    AgentTurn,
    AgentUsage,
)
from novel.agents.claude_code import ClaudeCodeDriver
from novel.agents.codex import CodexDriver

__all__ = [
    'AgentBusyError',
    'AgentCommandError',
    'AgentConfigurationError',
    'AgentDriver',
    'AgentError',
    'AgentProbe',
    'AgentProtocolError',
    'AgentRequest',
    'AgentTurn',
    'AgentUsage',
    'ClaudeCodeDriver',
    'CodexDriver',
]
