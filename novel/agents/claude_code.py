#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
claude_code.py —— Claude Code CLI 的可恢复会话驱动。

新会话使用 ``--output-format json``，并从结果信封读取 ``session_id``；续轮只用
这一个 id 传 ``--resume``，不使用「最近一次会话」之类依赖进程全局状态的捷径。
模型与 effort 每轮都显式传递，且刻意不提供 ``--fallback-model``：主模型不可用时
应当响亮失败，由上层决定下一步，而不是把另一个模型的产物冒充原配置结果。
"""
from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from contracts.schema.orchestration import AgentProfile
from novel.agents.base import (
    CLAUDE_STRUCTURED_DELIVERY_V1,
    AgentCommandError,
    AgentConfigurationError,
    AgentProtocolError,
    AgentRequest,
    AgentTurn,
    AgentUsage,
    CliAgentDriver,
    Runner,
    RunnerCallable,
    compact_schema,
    classify_agent_command_failure,
    decode_wire_json,
    integer,
)


_EMPTY_MCP_CONFIG = '{"mcpServers":{}}'


def _claude_usage(raw: Mapping[str, Any]) -> AgentUsage:
    """把 Claude 的多模型明细聚合一次；辅助模型已经包含在总数里。"""
    model_usage = raw.get('modelUsage')
    prompt = cache_hit = cache_miss = completion = 0
    seen_prompt = seen_hit = seen_miss = seen_completion = False
    if isinstance(model_usage, Mapping):
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

    top_usage = raw.get('usage')
    usage = top_usage if isinstance(top_usage, Mapping) else {}
    # 某些 CLI 版本不带 modelUsage；这时才读取顶层 usage，不能与明细相加两遍。
    if not isinstance(model_usage, Mapping) or not model_usage:
        direct = integer(usage.get('input_tokens'))
        read = integer(usage.get('cache_read_input_tokens'))
        created = integer(usage.get('cache_creation_input_tokens'))
        output = integer(usage.get('output_tokens'))
        if any(value is not None for value in (direct, read, created)):
            prompt = (direct or 0) + (read or 0) + (created or 0)
            seen_prompt = True
        if read is not None:
            cache_hit, seen_hit = read, True
        if direct is not None or created is not None:
            cache_miss, seen_miss = (direct or 0) + (created or 0), True
        if output is not None:
            completion, seen_completion = output, True

    output_detail = usage.get('output_tokens_details')
    reasoning = (integer(output_detail.get('thinking_tokens'))
                 if isinstance(output_detail, Mapping) else None)
    prompt_value = prompt if seen_prompt else None
    completion_value = completion if seen_completion else None
    total = ((prompt_value or 0) + (completion_value or 0)
             if prompt_value is not None or completion_value is not None else None)
    return AgentUsage(
        prompt_tokens=prompt_value,
        prompt_cache_hit_tokens=cache_hit if seen_hit else None,
        prompt_cache_miss_tokens=cache_miss if seen_miss else None,
        completion_tokens=completion_value,
        reasoning_tokens=reasoning,
        total_tokens=total,
    )


def _actual_model(raw: Mapping[str, Any], requested: str) -> str | None:
    """优先读显式 model；多模型明细中只选择能辨认出的主模型。"""
    top = raw.get('model')
    if isinstance(top, str) and top.strip():
        return top
    detail = raw.get('modelUsage')
    if not isinstance(detail, Mapping):
        return None
    names = [name for name in detail if isinstance(name, str) and name.strip()]
    needle = requested.casefold().removeprefix('claude-')
    matching = [name for name in names if needle in name.casefold()]
    if len(matching) == 1:
        return matching[0]
    return names[0] if len(names) == 1 else None


def _actual_effort(raw: Mapping[str, Any]) -> str | None:
    for key in ('reasoning_effort', 'effort'):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _summary(raw: Mapping[str, Any], usage: AgentUsage,
             model: str | None) -> tuple[dict[str, Any], ...]:
    """只留信封身份与计量摘要；result/structured_output 不重复进入 raw 面。"""
    return ({
        'type': raw.get('type'),
        'subtype': raw.get('subtype'),
        'is_error': raw.get('is_error'),
        'session_id': raw.get('session_id'),
        'model': model,
        'num_turns': integer(raw.get('num_turns')),
        'usage': {
            'prompt_tokens': usage.prompt_tokens,
            'prompt_cache_hit_tokens': usage.prompt_cache_hit_tokens,
            'prompt_cache_miss_tokens': usage.prompt_cache_miss_tokens,
            'completion_tokens': usage.completion_tokens,
            'reasoning_tokens': usage.reasoning_tokens,
            'total_tokens': usage.total_tokens,
        },
    },)


def _failure_detail(raw: Mapping[str, Any]) -> str:
    """只拼供应商信封的已知错误格；不把 structured output 当诊断。"""
    values: list[str] = []
    for key in ('error', 'error_type', 'code', 'result'):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value)
        elif isinstance(value, Mapping):
            for nested in ('type', 'code', 'message', 'status'):
                item = value.get(nested)
                if isinstance(item, (str, int)) and not isinstance(item, bool):
                    values.append(str(item))
    return ' '.join(values)


def _failure_status(raw: Mapping[str, Any]) -> int | None:
    for key in ('status', 'error_status', 'status_code'):
        value = integer(raw.get(key))
        if value is not None and 100 <= value <= 599:
            return value
    error = raw.get('error')
    if isinstance(error, Mapping):
        for key in ('status', 'status_code'):
            value = integer(error.get(key))
            if value is not None and 100 <= value <= 599:
                return value
    return None


def claude_failure_evidence(
    raw: Mapping[str, Any], stderr: str = '',
) -> tuple[str, int | None]:
    """完整失败 result 是唯一错误来源；缺格保持未知，不混后台 stderr 警告。"""
    if raw.get('subtype') == 'success' and raw.get('is_error') is False:
        # CLI 在成功回答之后另行失败时，result 仍是回答，不取得错误行动权。
        return stderr, None
    return _failure_detail(raw), _failure_status(raw)


class ClaudeCodeDriver(CliAgentDriver):
    """Claude Code 的一次实例只拥有当前一只 CLI 子进程。"""

    driver = 'claude-code'
    provider = 'anthropic'
    command = 'claude'
    # 取值来自当前受测 Claude CLI 的 ``--help``；``ultra`` 尚未宣称可用。
    supported_reasoning_efforts = frozenset(
        {'low', 'medium', 'high', 'xhigh', 'max'})

    def __init__(self, *, executable: str | None = None,
                 timeout: float = 1800,
                 runner: Runner | RunnerCallable | None = None) -> None:
        super().__init__(
            executable=executable, timeout=timeout, runner=runner)

    def _argv(self, profile: AgentProfile, request: AgentRequest, *,
               session_id: str | None) -> list[str]:
        argv = [
            self.executable, '-p', '--output-format', 'json',
            '--model', profile.model,
            '--effort', profile.reasoning_effort,
            # executor 只经 stdin/structured output 交付候选，不能借内置工具读写
            # 宿主，也不能从用户/项目配置恢复 MCP。safe mode 仍保留 CLI 自己的
            # provider session persistence；刻意不传 ``--no-session-persistence``。
            '--safe-mode',
            '--tools', '',
            '--mcp-config', _EMPTY_MCP_CONFIG,
            '--strict-mcp-config',
        ]
        if session_id is not None:
            argv.extend(('--resume', session_id))
        schema = compact_schema(request.output_schema)
        if schema is not None:
            if request.delivery_protocol != CLAUDE_STRUCTURED_DELIVERY_V1:
                raise AgentConfigurationError(
                    'Claude --json-schema 请求必须使用 '
                    'claude-structured-output/v1 delivery footer')
            argv.extend(('--json-schema', schema))
        elif request.delivery_protocol == CLAUDE_STRUCTURED_DELIVERY_V1:
            raise AgentConfigurationError(
                'Claude schema-less 请求不能冒充 structured delivery')
        return argv

    def _decode(self, completed: subprocess.CompletedProcess[str], *,
                profile: AgentProfile,
        expected_session_id: str | None,
        expects_structured_output: bool,
    ) -> AgentTurn:
        try:
            raw = decode_wire_json(completed.stdout or '')
        except (json.JSONDecodeError, TypeError) as exc:
            if completed.returncode != 0:
                self._raise_for_exit(completed)
            raise AgentProtocolError(
                f'Claude Code JSON 信封无法解析：{type(exc).__name__}') from exc
        if not isinstance(raw, Mapping):
            raise AgentProtocolError('Claude Code JSON 顶层必须是 object')
        if raw.get('type') != 'result':
            raise AgentProtocolError('Claude Code JSON type 必须是 result')

        session_id = raw.get('session_id')
        if not isinstance(session_id, str) or not session_id.strip():
            raise AgentProtocolError('Claude Code 没有返回可恢复的 session_id')
        if expected_session_id is not None and session_id != expected_session_id:
            raise AgentProtocolError(
                'Claude Code resume 返回了不同 session_id；拒绝把两个会话串成一条')

        usage = _claude_usage(raw)
        model = _actual_model(raw, profile.model)
        structured = raw.get('structured_output')
        result = raw.get('result')
        if structured is not None:
            content = json.dumps(
                structured, ensure_ascii=False, separators=(',', ':'))
        elif not expects_structured_output and isinstance(result, str):
            content = result
        else:
            content = ''
        turn = AgentTurn(
            session_id=session_id,
            content=content,
            model=model,
            usage=usage,
            turn_count=integer(raw.get('num_turns')),
            actual_reasoning_effort=_actual_effort(raw),
            raw_event_summary=_summary(raw, usage, model),
        )
        if (completed.returncode != 0 or raw.get('subtype') != 'success'
                or raw.get('is_error') is not False):
            failure_detail, failure_status = claude_failure_evidence(
                raw, completed.stderr or '')
            error_kind, status, retryable = classify_agent_command_failure(
                failure_detail, usage=usage, status=failure_status)
            raise AgentCommandError(
                f'Claude Code 返回失败信封（exit={completed.returncode}，'
                f'subtype={raw.get("subtype")!r}，is_error={raw.get("is_error")!r}，'
                f'kind={error_kind!r}，status={status!r}）',
                returncode=completed.returncode,
                # 失败信封可以带看似完整的 result；只留结算证据，绝不让它成为候选。
                partial_turn=replace(turn, content=''),
                error_kind=error_kind, status=status, retryable=retryable)
        if expects_structured_output and structured is None:
            raise AgentProtocolError(
                'Claude Code --json-schema 成功信封缺 structured_output；'
                '拒绝把普通 result 冒充结构化交付')
        if not content:
            raise AgentProtocolError('Claude Code 成功信封没有最终回答')
        return turn

    def start(self, profile: AgentProfile, request: AgentRequest) -> AgentTurn:
        self._check_profile(profile)
        cwd = self._prepare_request_cwd(request, session_id=None)
        completed = self._run(
            self._argv(profile, request, session_id=None), request,
            session_id=None)
        turn = self._decode(
            completed, profile=profile, expected_session_id=None,
            expects_structured_output=request.output_schema is not None)
        self._bind_session_cwd(turn.session_id, cwd)
        return turn

    def resume(self, profile: AgentProfile, session_id: str,
               request: AgentRequest) -> AgentTurn:
        self._check_profile(profile)
        if not session_id.strip():
            raise AgentProtocolError('resume session_id 不得为空')
        cwd = self._prepare_request_cwd(request, session_id=session_id)
        completed = self._run(
            self._argv(profile, request, session_id=session_id), request,
            session_id=session_id)
        turn = self._decode(
            completed, profile=profile, expected_session_id=session_id,
            expects_structured_output=request.output_schema is not None)
        self._bind_session_cwd(turn.session_id, cwd)
        return turn
