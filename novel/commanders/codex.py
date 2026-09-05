#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Codex ``exec --json`` Commander driver。"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, Iterator

from contracts.schema.orchestration import AgentProfile
from novel.agents.base import AgentUsage, integer
from novel.commanders.base import (
    CliCommanderDriver,
    CommanderConfigurationError,
    CommanderInvocation,
    CommanderObservation,
    CommanderProcessResult,
    CommanderProtocolError,
    _prepare_output_schema,
    jsonl_events,
    optional_usage,
    sum_optional,
)
from novel.commanders.output_schema_file import (
    materialized_codex_output_schema,
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
    reasoning = integer(raw.get('reasoning_output_tokens'))
    if reasoning is None:
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


def _agent_message(events: Sequence[Mapping[str, Any]]) -> str | None:
    """只取最后一个已完成 agent_message；tool 输出和失败正文不是交付。"""
    found: str | None = None
    for event in events:
        if event.get('type') != 'item.completed':
            continue
        item = event.get('item')
        if not isinstance(item, Mapping) or item.get('type') != 'agent_message':
            continue
        value = item.get('text')
        if isinstance(value, str) and value.strip():
            found = value
    return found


class CodexCommanderDriver(CliCommanderDriver):
    """Codex 非交互 Commander；reviewer 只读并从最终回答交付 JSON。"""

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
        structured_response: bool = False,
        output_schema: Mapping[str, Any] | None = None,
        expected_output_schema_sha256: str | None = None,
    ) -> CommanderInvocation:
        if allowed_tools:
            raise CommanderConfigurationError(
                'Codex 不接受 Claude --allowedTools 名称；'
                '其 commander 权限由 approval/sandbox 配置关闭式约束')
        resolved = self._resolved_profile(profile)
        if not isinstance(prompt, str) or not prompt.strip():
            raise CommanderConfigurationError('commander prompt 不得为空')
        repo_abs, base_abs = self._paths(repo, base)
        command_cwd = base_abs if structured_response else repo_abs
        schema_snapshot, schema_bytes, schema_sha256 = _prepare_output_schema(
            output_schema)
        argv = [
            self.executable,
            'exec',
        ]
        if resume_provider_session_id is not None:
            if not resume_provider_session_id.strip():
                raise CommanderConfigurationError(
                    'Codex resume provider session id 不得为空')
        # approval/sandbox / ``--cd`` / ``--add-dir`` 是 ``exec`` 父命令
        # 选项，不属于 ``exec resume`` 子命令；必须全部放在 ``resume`` 前。
        # 放反后真实 CLI 会在第二 turn 报 unknown option，单看 argv 含 session id
        # 的测试抓不到这类假续会。
        argv.extend((
            '--json',
            '--model', resolved.model,
            '--config',
            f'model_reasoning_effort={json.dumps(resolved.reasoning_effort)}',
        ))
        if structured_response:
            # Reviewer 的材料已全部内联在 prompt，也不再由模型写
            # scratch。Codex CLI 没有 Claude ``--tools ''`` 对等开关，
            # 所以这里只能把 cwd 收窄到 scratch、文件系统冻结为 read-only、
            # 关闭审批与默认网络外发；最终文本仍必须经过 Supervisor 的
            # typed loader，不能把 prompt 自觉冒充结构性撤权。
            argv.extend((
                '--config', 'approval_policy="never"',
                '--config', 'sandbox_mode="read-only"',
            ))
        else:
            # 保留旧的非 reviewer Commander invocation；它只用于冻结/
            # 兼容面，现役确定性 AgentRun 的节点执行不经这里。
            argv.extend((
                '--approve-for-me',
                '--config', 'sandbox_workspace_write.network_access=true',
            ))
        argv.extend((
            '--ignore-user-config',
            # user/project execpolicy 是宿主状态，不能让同一冻结 run 因机器上
            # 历史 ``.rules`` 不同而一台直跑、一台在自动审查前提前拒绝。
            '--ignore-rules',
            '--strict-config',
        ))
        if structured_response:
            argv.append('--skip-git-repo-check')
        argv.extend(('--cd', command_cwd))
        if not structured_response:
            argv.extend(('--add-dir', base_abs))
        if schema_sha256 is not None:
            # 固定 hash 文件名使 preview/start/resume 的 invocation 身份稳定；
            # 真文件只在 ``run`` 包围期独占创建，不成为 durable artifact。
            schema_path = Path(command_cwd) / (
                f'.commander-output-schema-{schema_sha256}.json')
            argv.extend(('--output-schema', str(schema_path)))
        if resume_provider_session_id is not None:
            argv.extend(('resume', resume_provider_session_id))
        # 官方 ``PROMPT=-``：多行任务只从 stdin 进入，绝不受 argv 长度影响。
        argv.append('-')
        return CommanderInvocation(
            argv=tuple(argv),
            stdin_text=prompt,
            cwd=command_cwd,
            env_overrides={} if env_overrides is None else env_overrides,
            profile=resolved,
            protocol=self.protocol,
            cli_version=cli_version,
            resume_provider_session_id=resume_provider_session_id,
            structured_response=structured_response,
            output_schema=schema_snapshot,
            expected_output_schema_sha256=expected_output_schema_sha256,
        )

    @contextmanager
    def _materialized_output_schema(
        self, invocation: CommanderInvocation,
    ) -> Iterator[None]:
        """把冻结 schema 限定在实际 runner 生命周期，且只允许 scratch 直属文件。"""
        with materialized_codex_output_schema(
                argv=invocation.argv,
                cwd=invocation.cwd,
                payload=invocation.output_schema_bytes,
                digest=invocation.output_schema_sha256):
            yield

    def run(
        self,
        invocation: CommanderInvocation,
        *,
        log: BinaryIO,
        run_id: str | None = None,
    ) -> CommanderProcessResult:
        with self._materialized_output_schema(invocation):
            return super().run(invocation, log=log, run_id=run_id)

    provider_label = 'Codex --json'

    @staticmethod
    def session_id_of_event(event: Mapping[str, Any]) -> str | None:
        if event.get('type') != 'thread.started':
            return None
        value = event.get('thread_id')
        return value if isinstance(value, str) and value.strip() else None

    def _final_response(
        self, events: Sequence[Mapping[str, Any]],
    ) -> str | None:
        return _agent_message(events)

    def _validate_structured_events(
        self,
        events: Sequence[Mapping[str, Any]],
        observation: CommanderObservation,
    ) -> None:
        """Codex 没有空工具集开关；任何实际 tool item 都使回答不可交付。

        Reviewer 的 cwd 已收窄到空 scratch、sandbox 为 read-only 且网络关闭。
        这里再从 provider 协议验证本轮只发生了推理与最终回答；即便不可信
        正文诱导了命令、文件、MCP 或搜索，Supervisor 也不会消费其 typed 结果。
        未识别的新 item 类型同样关闭式拒绝，不能默认当成无工具。
        """
        allowed = frozenset({'agent_message', 'reasoning'})
        used: set[str] = set()
        for event in events:
            if event.get('type') not in {'item.started', 'item.completed'}:
                continue
            item = event.get('item')
            if not isinstance(item, Mapping):
                used.add('<malformed>')
                continue
            item_type = item.get('type')
            if not isinstance(item_type, str) or item_type not in allowed:
                used.add(str(item_type) if item_type is not None else '<missing>')
        if used:
            raise CommanderProtocolError(
                'Codex structured reviewer 触发了禁止的工具/未知 item：'
                + '、'.join(sorted(used)),
                partial_observation=observation)

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
        thread_id = _thread_id(events)
        observation = CommanderObservation(
            provider_session_id=thread_id,
            actual_model=_last_metadata(events, 'model'),
            actual_effort=_last_metadata(
                events, 'reasoning_effort', 'effort'),
            usage=_usage(events),
        )
        if (expected_provider_session_id is not None
                and thread_id is not None
                and thread_id != expected_provider_session_id):
            raise CommanderProtocolError(
                'Codex Commander resume 返回了不同 thread id；拒绝串错会话',
                partial_observation=observation)
        completed_turns = sum(
            event.get('type') == 'turn.completed' for event in events)
        last_completed = max(
            (index for index, event in enumerate(events)
             if event.get('type') == 'turn.completed'),
            default=-1)
        # Codex 0.148 会在 websocket/TLS 瞬断时先发顶层
        # ``error: Reconnecting...``，内部重连成功后再发 turn.completed。
        # 只有最后成功终态之后的 error 才是未恢复错误；
        # turn.failed 则无论顺序都与 exit 0 矛盾。
        failed_turn = next(
            (event for event in events if event.get('type') == 'turn.failed'),
            None)
        terminal_error = next(
            (event for index, event in enumerate(events)
             if event.get('type') == 'error' and index > last_completed),
            None)
        if exit_code == 0:
            if thread_id is None:
                raise CommanderProtocolError(
                    'Codex 成功事件流没有 thread.started.thread_id',
                    partial_observation=observation)
            if completed_turns < 1:
                raise CommanderProtocolError(
                    'Codex 成功事件流没有 turn.completed',
                    partial_observation=observation)
            if failed_turn is not None or terminal_error is not None:
                raise CommanderProtocolError(
                    'Codex exit 0 的最后终态仍是失败，拒绝折成成功',
                    partial_observation=observation)
        return observation


__all__ = ['CodexCommanderDriver']
