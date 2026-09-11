"""智谱的 Claude CLI 接入：共用 CLI 协议，独立供应商身份与固定接收端。

认证仍由用户的 Claude CLI 管理。本层只核对非敏感接收端并将其固定到本次 argv；
不复制密钥、不更改用户配置、不把 Anthropic 登录态默认为智谱凭据。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from contracts.schema.orchestration import AgentProfile
from novel.agents.base import AgentConfigurationError, AgentRequest
from novel.agents.claude_code import ClaudeCodeDriver


BIGMODEL_BASE_URL = 'https://open.bigmodel.cn/api/anthropic'


def bigmodel_cli_settings() -> list[str]:
    """每次发 wire 前检查现有接收端；密钥仅由 CLI 自己读取，不进入 argv/审计。"""
    config_dir = Path(os.environ.get('CLAUDE_CONFIG_DIR', str(Path.home() / '.claude')))
    path = config_dir / 'settings.json'
    try:
        config = json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}
        configured_env = config.get('env', {})
        if not isinstance(configured_env, dict):
            raise ValueError('env 必须是对象')
        # CLI settings.env 优先于继承的 shell env；argv 再固定唯一接收端。
        endpoint = configured_env.get('ANTHROPIC_BASE_URL', os.environ.get('ANTHROPIC_BASE_URL'))
    except (OSError, ValueError, AttributeError) as exc:
        raise AgentConfigurationError('无法读取 Claude CLI 的智谱接收端配置') from exc
    if not isinstance(endpoint, str) or endpoint.rstrip('/') != BIGMODEL_BASE_URL:
        raise AgentConfigurationError(
            'claude-code-glm 要求 Claude CLI 已配置智谱接收端 '
            'https://open.bigmodel.cn/api/anthropic；本次未发请求')
    return ['--setting-sources', 'user', '--settings', json.dumps({'env': {
        'ANTHROPIC_BASE_URL': BIGMODEL_BASE_URL,
        'CLAUDE_CODE_AUTO_COMPACT_WINDOW': '1000000',
        'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
    }}, separators=(',', ':'))]


class GlmClaudeCodeDriver(ClaudeCodeDriver):
    """GLM Executor 不复用 Claude 模型映射或 Anthropic 供应商额度。"""
    driver = 'claude-code-glm'
    provider = 'bigmodel'
    supported_reasoning_efforts = frozenset({'low', 'high', 'max'})

    def _argv(self, profile: AgentProfile, request: AgentRequest, *,
              session_id: str | None) -> list[str]:
        return [*super()._argv(profile, request, session_id=session_id), *bigmodel_cli_settings()]
