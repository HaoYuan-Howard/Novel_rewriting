#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Codex ``exec --json`` Commander driver。"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from typing import Any

from contracts.schema.orchestration import AgentProfile
from novel.agents.base import AgentUsage, integer
from novel.commanders.base import (
    CliCommanderDriver,
    CommanderConfigurationError,
    CommanderInvocation,
    CommanderObservation,
    CommanderProtocolError,
    jsonl_events,
    optional_usage,
    sum_optional,
)


def _thread_id(events: Sequence[Mapping[str, Any]]) -> str | None:
    ids = {
        event.get('thread_id')
        for event in events
        if event.get('type') == 'thread.started'
        and isinstance(event.get('thread_id'), str)
        and str(event.get('thread_id')).strip()
    }
    if len(ids) > 1:
        raise CommanderProtocolError(
            'Codex --json 返回了多个 thread.started.thread_id')
    return str(ids.pop()) if ids else None


def _nested_string(event: Mapping[str, Any], key: str) -> str | None:
    direct = event.get(key)
    if isinstance(direct, str) and direct.strip():
        return direct
    for container_name in ('thread', 'turn', 'response', 'item'):
        container = event.get(container_name)
        if isinstance(container, Mapping):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return None


def _last_metadata(
    events: Sequence[Mapping[str, Any]],
    *keys: str,
) -> str | None:
    found: str | None = None
    for event in events:
        for key in keys:
            value = _nested_string(event, key)
            if value is not None:
                found = value
                break
    return found


def _usage_row(raw: Any) -> AgentUsage | None:
    if not isinstance(raw, Mapping):
        return None
    prompt = integer(raw.get('input_tokens'))
    cache_hit = integer(raw.get('cached_input_tokens'))
    completion = integer(raw.get('output_tokens'))
    detail = raw.get('output_tokens_details')
    reasoning = integer(raw.get('reasoning_tokens'))
    if reasoning is None and isinstance(detail, Mapping):
        reasoning = integer(detail.get('reasoning_tokens'))
        if reasoning is None:
            reasoning = integer(detail.get('reasoning_output_tokens'))
    cache_miss = (max(0, prompt - cache_hit)
                  if prompt is not None and cache_hit is not None else None)
    total = ((prompt or 0) + (completion or 0)
             if prompt is not None or completion is not None else None)
    return optional_usage(
        prompt_tokens=prompt,
        prompt_cache_hit_tokens=cache_hit,
        prompt_cache_miss_tokens=cache_miss,
        completion_tokens=completion,
        reasoning_tokens=reasoning,
        total_tokens=total,
    )


def _usage(events: Sequence[Mapping[str, Any]]) -> AgentUsage | None:
    # error 常是 turn.failed 的补充，已有 turn 终态时不能再加一遍。
    terminal = [
        event for event in events
        if event.get('type') in ('turn.completed', 'turn.failed')
    ]
    if not terminal:
        terminal = [
            event for event in events
            if event.get('type') == 'error'
            and isinstance(event.get('usage'), Mapping)
        ]
    rows = [row for row in (
        _usage_row(event.get('usage')) for event in terminal) if row is not None]
    if not rows:
        return None
    return optional_usage(
        prompt_tokens=sum_optional([row.prompt_tokens for row in rows]),
        prompt_cache_hit_tokens=sum_optional(
            [row.prompt_cache_hit_tokens for row in rows]),
        prompt_cache_miss_tokens=sum_optional(
            [row.prompt_cache_miss_tokens for row in rows]),
        completion_tokens=sum_optional(
            [row.completion_tokens for row in rows]),
        reasoning_tokens=sum_optional(
            [row.reasoning_tokens for row in rows]),
        total_tokens=sum_optional([row.total_tokens for row in rows]),
    )


class CodexCommanderDriver(CliCommanderDriver):
    """Codex 非交互整书 Commander；workspace-write 内由自动审查代批。"""

    driver = 'codex'
    provider = 'openai'
    command = 'codex'
    protocol = 'codex-jsonl-v1'

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
    ) -> CommanderInvocation:
        if allowed_tools:
            raise CommanderConfigurationError(
                'Codex 不接受 Claude --allowedTools 名称；'
                '其 commander 权限由 approval/sandbox 配置关闭式约束')
        resolved = self._resolved_profile(profile)
        if not isinstance(prompt, str) or not prompt.strip():
            raise CommanderConfigurationError('commander prompt 不得为空')
        repo_abs, base_abs = self._paths(repo, base)
        argv = [
            self.executable,
            'exec',
        ]
        if resume_provider_session_id is not None:
            if not resume_provider_session_id.strip():
                raise CommanderConfigurationError(
                    'Codex resume provider session id 不得为空')
        # ``--approve-for-me`` / ``--cd`` / ``--add-dir`` 是 ``exec`` 父命令
        # 选项，不属于 ``exec resume`` 子命令；必须全部放在 ``resume`` 前。
        # 放反后真实 CLI 会在第二 turn 报 unknown option，单看 argv 含 session id
        # 的测试抓不到这类假续会。
        argv.extend((
            '--json',
            '--model', resolved.model,
            '--config',
            f'model_reasoning_effort={json.dumps(resolved.reasoning_effort)}',
            # ``never`` 会把 Windows shell / 仓外解释器的边界请求直接拒绝，
            # Commander 连 canonical runner 都无法启动。CLI 自带的自动审查仍
            # 固定使用 workspace-write。network=true 会覆盖沙箱内所有命令与
            # 子进程；开启它的必要消费者是 runner 的 Claude/Codex executor，
            # 实际出站仍须继承 launcher 在双授权后注入的代理环境。
            '--approve-for-me',
            '--config', 'sandbox_workspace_write.network_access=true',
            '--ignore-user-config',
            # user/project execpolicy 是宿主状态，不能让同一冻结 run 因机器上
            # 历史 ``.rules`` 不同而一台直跑、一台在自动审查前提前拒绝。
            '--ignore-rules',
            '--strict-config',
            '--cd', repo_abs,
            '--add-dir', base_abs,
        ))
        if resume_provider_session_id is not None:
            argv.extend(('resume', resume_provider_session_id))
        # 官方 ``PROMPT=-``：多行任务只从 stdin 进入，绝不受 argv 长度影响。
        argv.append('-')
        return CommanderInvocation(
            argv=tuple(argv),
            stdin_text=prompt,
            cwd=repo_abs,
            env_overrides={} if env_overrides is None else env_overrides,
            profile=resolved,
            protocol=self.protocol,
            cli_version=cli_version,
            resume_provider_session_id=resume_provider_session_id,
        )

    def _decode(
        self,
        stdout: str,
        *,
        profile: AgentProfile,
        exit_code: int,
        expected_provider_session_id: str | None,
    ) -> CommanderObservation:
        del profile
        events = jsonl_events(
            stdout, provider='Codex --json', allow_empty=exit_code != 0)
        if not events:
            return CommanderObservation(
                provider_session_id=None,
                actual_model=None,
                actual_effort=None,
                usage=None,
            )
        thread_id = _thread_id(events)
        if (expected_provider_session_id is not None
                and thread_id is not None
                and thread_id != expected_provider_session_id):
            raise CommanderProtocolError(
                'Codex Commander resume 返回了不同 thread id；拒绝串错会话')
        completed_turns = sum(
            event.get('type') == 'turn.completed' for event in events)
        failed = next(
            (event for event in events
             if event.get('type') in ('turn.failed', 'error')),
            None,
        )
        if exit_code == 0:
            if thread_id is None:
                raise CommanderProtocolError(
                    'Codex 成功事件流没有 thread.started.thread_id')
            if completed_turns < 1:
                raise CommanderProtocolError(
                    'Codex 成功事件流没有 turn.completed')
            if failed is not None:
                raise CommanderProtocolError(
                    'Codex exit 0 却带失败事件，拒绝把矛盾状态折成成功')
        return CommanderObservation(
            provider_session_id=thread_id,
            actual_model=_last_metadata(events, 'model'),
            actual_effort=_last_metadata(
                events, 'reasoning_effort', 'effort'),
            usage=_usage(events),
        )


__all__ = ['CodexCommanderDriver']
