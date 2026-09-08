#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Claude Code ``stream-json`` Commander driver。"""
from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from contracts.schema.orchestration import AgentProfile
from novel.agents.base import (
    AgentConfigurationError,
    AgentUsage,
    ensure_command_line_fits,
    integer,
)
from novel.agents.claude_code import claude_failure_evidence
from novel.commanders.base import (
    CliCommanderDriver,
    CommanderConfigurationError,
    CommanderInvocation,
    CommanderObservation,
    CommanderProtocolError,
    _prepare_output_schema,
    optional_usage,
)


_EMPTY_MCP_CONFIG = '{"mcpServers":{}}'

# Claude Code 的后台 Bash 会随顶层 CLI 退出而被清理；整书 Commander 若把
# ``run_step.py`` 丢进后台，外层却会先收到一个成功 result，随后对仍在写盘的
# 子进程做终验。这里把「前台执行」冻结成 launcher 事实，不依赖 prompt 自觉。
# 默认单条 Bash 允许 6 小时，显式 timeout 最多 23 小时；后者刻意短于
# ``CliCommanderDriver`` 的 24 小时总 timeout，给协议终态与日志泵留出收尾窗口。
_FROZEN_CLAUDE_ENV = {
    'CLAUDE_CODE_DISABLE_BACKGROUND_TASKS': '1',
    'BASH_DEFAULT_TIMEOUT_MS': str(6 * 60 * 60 * 1000),
    'BASH_MAX_TIMEOUT_MS': str(23 * 60 * 60 * 1000),
}


def _python_rule() -> str:
    """与 commander 实际运行解释器同源，不硬编码 conda 路径。"""
    return f'Bash({Path(sys.executable).as_posix()} *)'


def _posix_anchor(path: str | os.PathLike[str]) -> str:
    """Claude Edit 规则使用 gitignore 风格 ``//`` 绝对锚。"""
    posix = Path(path).resolve().as_posix()
    match = re.match(r'^([A-Za-z]):/(.*)$', posix)
    if match is not None:
        return f'//{match.group(1).lower()}/{match.group(2)}'
    return f'//{posix}'


def _default_allowed_tools(base: str) -> tuple[str, ...]:
    return (_python_rule(), f'Edit({_posix_anchor(base)}/**)')


def _session_id(events: Sequence[Mapping[str, Any]]) -> str | None:
    ids = {
        value
        for event in events
        for value in (event.get('session_id'),)
        if isinstance(value, str) and value.strip()
    }
    if len(ids) > 1:
        raise CommanderProtocolError(
            'Claude stream-json 返回了多个 session_id；拒绝串接会话')
    return str(ids.pop()) if ids else None


def _last_metadata(
    events: Sequence[Mapping[str, Any]],
    *keys: str,
) -> str | None:
    found: str | None = None
    for event in events:
        containers = [event]
        for container_name in ('message', 'response', 'result'):
            container = event.get(container_name)
            if isinstance(container, Mapping):
                containers.append(container)
        for container in containers:
            matched = False
            for key in keys:
                value = container.get(key)
                if isinstance(value, str) and value.strip():
                    found = value
                    matched = True
                    break
            if matched:
                break
    return found


def _usage(raw: Mapping[str, Any]) -> AgentUsage | None:
    """Claude result 的 modelUsage 优先；顶层 usage 只作无明细时的后备。"""
    model_usage = raw.get('modelUsage')
    prompt = cache_hit = cache_miss = completion = 0
    seen_prompt = seen_hit = seen_miss = seen_completion = False
    if isinstance(model_usage, Mapping) and model_usage:
        for detail in model_usage.values():
            if not isinstance(detail, Mapping):
                continue
            direct = integer(detail.get('inputTokens'))
            read = integer(detail.get('cacheReadInputTokens'))
            created = integer(detail.get('cacheCreationInputTokens'))
            output = integer(detail.get('outputTokens'))
            if any(value is not None for value in (direct, read, created)):
                prompt += (direct or 0) + (read or 0) + (created or 0)
                seen_prompt = True
            if read is not None:
                cache_hit += read
                seen_hit = True
            if direct is not None or created is not None:
                cache_miss += (direct or 0) + (created or 0)
                seen_miss = True
            if output is not None:
                completion += output
                seen_completion = True
    else:
        usage = raw.get('usage')
        detail = usage if isinstance(usage, Mapping) else {}
        direct = integer(detail.get('input_tokens'))
        read = integer(detail.get('cache_read_input_tokens'))
        created = integer(detail.get('cache_creation_input_tokens'))
        output = integer(detail.get('output_tokens'))
        if any(value is not None for value in (direct, read, created)):
            prompt = (direct or 0) + (read or 0) + (created or 0)
            seen_prompt = True
        if read is not None:
            cache_hit, seen_hit = read, True
        if direct is not None or created is not None:
            cache_miss, seen_miss = (direct or 0) + (created or 0), True
        if output is not None:
            completion, seen_completion = output, True

    usage = raw.get('usage')
    output_detail = (usage.get('output_tokens_details')
                     if isinstance(usage, Mapping) else None)
    reasoning = (integer(output_detail.get('thinking_tokens'))
                 if isinstance(output_detail, Mapping) else None)
    prompt_value = prompt if seen_prompt else None
    completion_value = completion if seen_completion else None
    total = ((prompt_value or 0) + (completion_value or 0)
             if prompt_value is not None or completion_value is not None
             else None)
    return optional_usage(
        prompt_tokens=prompt_value,
        prompt_cache_hit_tokens=cache_hit if seen_hit else None,
        prompt_cache_miss_tokens=cache_miss if seen_miss else None,
        completion_tokens=completion_value,
        reasoning_tokens=reasoning,
        total_tokens=total,
    )


class ClaudeCodeCommanderDriver(CliCommanderDriver):
    """Claude Code 的整书 Commander；模型不可用时绝不指定 fallback。"""

    driver = 'claude-code'
    provider = 'anthropic'
    command = 'claude'
    protocol = 'claude-stream-json-v1'

    def build_invocation(
        self,
        profile: AgentProfile,
        prompt: str,
        *,
        repo: str | os.PathLike[str],
        base: str | os.PathLike[str],
        allowed_tools: Sequence[str] = (),
        env_overrides: Mapping[str, str] | None = None,
        cli_version: str | None = None,
        resume_provider_session_id: str | None = None,
        structured_response: bool = False,
        output_schema: Mapping[str, Any] | None = None,
        expected_output_schema_sha256: str | None = None,
    ) -> CommanderInvocation:
        resolved = self._resolved_profile(profile)
        if not isinstance(prompt, str) or not prompt.strip():
            raise CommanderConfigurationError('commander prompt 不得为空')
        repo_abs, base_abs = self._paths(repo, base)
        schema_snapshot, schema_bytes, _schema_sha256 = _prepare_output_schema(
            output_schema)
        tools = (() if structured_response else
                 (tuple(allowed_tools) or _default_allowed_tools(base_abs)))
        if any(not isinstance(item, str) or not item.strip() for item in tools):
            raise CommanderConfigurationError(
                'Claude --allowedTools 每一项都必须是非空字符串')
        argv = [
            self.executable,
            '-p',
            '--input-format', 'text',
            '--output-format', 'stream-json',
            '--verbose',
            '--model', resolved.model,
            '--effort', resolved.reasoning_effort,
            '--permission-mode', 'manual',
            '--no-chrome',
            '--mcp-config', _EMPTY_MCP_CONFIG,
            '--strict-mcp-config',
        ]
        if structured_response:
            # 省略 --allowedTools 不是禁用 built-ins；Claude 只有
            # ``--tools ''`` 才是空工具集。headless reviewer 也不取得
            # permission prompt 通道，保证“只回答最终 JSON”是 argv 事实。
            argv.extend(('--tools', '', '--permission-prompts', 'none'))
        else:
            argv.extend(('--add-dir', base_abs))
        if schema_bytes is not None:
            argv.extend(('--json-schema', schema_bytes.decode('utf-8')))
        if resume_provider_session_id is not None:
            if not resume_provider_session_id.strip():
                raise CommanderConfigurationError(
                    'Claude resume provider session id 不得为空')
            argv.extend(('--resume', resume_provider_session_id))
        # 必须留在末尾：Claude 将其后各项都解释为白名单，不再追加 prompt。
        # structured reviewer 的材料已全部内联，不下发任何工具白名单。
        if tools:
            argv.extend(('--allowedTools', *tools))
        if '--fallback-model' in argv:
            raise AssertionError('Commander 禁止隐式 fallback model')
        try:
            # Claude 只提供内联 schema；沿用 executor 与 Popen(list) 同口径的
            # UTF-16 预算，保证超限在任何模型 wire 之前明确失败。
            ensure_command_line_fits(argv)
        except AgentConfigurationError as exc:
            raise CommanderConfigurationError(str(exc)) from exc
        frozen_env = dict(env_overrides or {})
        conflicts = {
            key: frozen_env[key]
            for key, value in _FROZEN_CLAUDE_ENV.items()
            if key in frozen_env and frozen_env[key] != value
        }
        if conflicts:
            names = '、'.join(sorted(conflicts))
            raise CommanderConfigurationError(
                f'Claude Commander 的生命周期环境不可覆盖：{names}')
        frozen_env.update(_FROZEN_CLAUDE_ENV)
        return CommanderInvocation(
            argv=tuple(argv),
            stdin_text=prompt,
            cwd=(base_abs if structured_response else repo_abs),
            env_overrides=frozen_env,
            profile=resolved,
            protocol=self.protocol,
            cli_version=cli_version,
            resume_provider_session_id=resume_provider_session_id,
            structured_response=structured_response,
            output_schema=schema_snapshot,
            expected_output_schema_sha256=expected_output_schema_sha256,
        )

    provider_label = 'Claude stream-json'

    @staticmethod
    def session_id_of_event(event: Mapping[str, Any]) -> str | None:
        value = event.get('session_id')
        return value if isinstance(value, str) and value.strip() else None

    def _failure_evidence(
        self, events: Sequence[Mapping[str, Any]], stderr: str,
    ) -> tuple[str, int | None]:
        terminals = [event for event in events if event.get('type') == 'result']
        succeeded = [event.get('subtype') == 'success' and event.get('is_error') is False
                     for event in terminals]
        if any(succeeded) and not all(succeeded):
            # 与 Codex 相同：同一进程同时报告失败和成功，不能借后台 warning
            # 把矛盾状态解释成一次普通网络超时。
            return '', None
        if terminals:
            return claude_failure_evidence(terminals[-1], stderr)
        return stderr, None

    def _final_response(
        self, events: Sequence[Mapping[str, Any]],
    ) -> str | None:
        for event in reversed(events):
            if (event.get('type') != 'result'
                    or event.get('subtype') != 'success'
                    or event.get('is_error') is not False):
                continue
            for key in ('structured_output', 'result'):
                value = event.get(key)
                if isinstance(value, str) and value.strip():
                    return value
                if isinstance(value, Mapping):
                    return json.dumps(
                        value, ensure_ascii=False, sort_keys=True,
                        separators=(',', ':'), allow_nan=False)
        return None

    def _decode_events(
        self,
        events: Sequence[Mapping[str, Any]],
        *,
        profile: AgentProfile,
        exit_code: int,
        expected_provider_session_id: str | None,
    ) -> CommanderObservation:
        del profile
        if not events:
            return CommanderObservation(
                provider_session_id=None,
                actual_model=None,
                actual_effort=None,
                usage=None,
            )
        terminal = [event for event in events if event.get('type') == 'result']
        session_id = _session_id(events)
        usage = _usage(terminal[-1]) if terminal else None
        observation = CommanderObservation(
            provider_session_id=session_id,
            actual_model=_last_metadata(events, 'model'),
            actual_effort=_last_metadata(
                events, 'reasoning_effort', 'effort'),
            usage=usage,
        )
        if (expected_provider_session_id is not None
                and session_id is not None
                and session_id != expected_provider_session_id):
            raise CommanderProtocolError(
                'Claude Commander resume 返回了不同 session_id；拒绝串错会话',
                partial_observation=observation)
        failed_event = next(
            (event for event in events
             if event.get('type') == 'result'
             and (event.get('subtype') != 'success'
                  or event.get('is_error') is not False)),
            None,
        )
        if exit_code == 0:
            if not terminal:
                raise CommanderProtocolError(
                    'Claude 成功退出但 stream-json 没有 result 终态',
                    partial_observation=observation)
            if session_id is None:
                raise CommanderProtocolError(
                    'Claude 成功事件流没有 provider session_id',
                    partial_observation=observation)
            if failed_event is not None:
                raise CommanderProtocolError(
                    'Claude exit 0 却返回失败 result，拒绝把矛盾状态折成成功',
                    partial_observation=observation)
        return observation


__all__ = ['ClaudeCodeCommanderDriver']
