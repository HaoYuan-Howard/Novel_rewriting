#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
codex.py —— Codex CLI JSONL 会话驱动。

Codex 的 stdout 在 ``--json`` 下是一条事件流，不是一份最终 JSON。驱动只从
``thread.started`` 取得 session id，从 ``turn.completed`` 归一 usage/轮次，再从
最后一个 ``agent_message`` 取得最终回答。resume 必须显式携带同一个 id；不能使用
``--last``，因为并行节点下「最近一次」是进程全局竞态。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

from contracts.schema.orchestration import AgentProfile
from novel.agents.base import (
    AgentCommandError,
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


def _events(stdout: str) -> list[Mapping[str, Any]]:
    """严格读取 JSONL；夹杂一行人读日志也不能静默跳过。"""
    events: list[Mapping[str, Any]] = []
    for line_number, line in enumerate(stdout.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = decode_wire_json(line)
        except json.JSONDecodeError as exc:
            raise AgentProtocolError(
                f'Codex --json 第 {line_number} 行不是合法 JSON') from exc
        if not isinstance(raw, Mapping):
            raise AgentProtocolError(
                f'Codex --json 第 {line_number} 行顶层不是 object')
        if not isinstance(raw.get('type'), str):
            raise AgentProtocolError(
                f'Codex --json 第 {line_number} 行缺少事件 type')
        events.append(raw)
    if not events:
        raise AgentProtocolError('Codex --json 没有返回任何事件')
    return events


def _optional_thread_id(events: Sequence[Mapping[str, Any]]) -> str | None:
    ids = {
        event.get('thread_id')
        for event in events
        if event.get('type') == 'thread.started'
        and isinstance(event.get('thread_id'), str)
        and str(event.get('thread_id')).strip()
    }
    if len(ids) > 1:
        raise AgentProtocolError(
            'Codex 事件流必须且只能标识一个 thread.started.thread_id')
    return str(ids.pop()) if ids else None


def _usage_row(raw: Any) -> AgentUsage:
    if not isinstance(raw, Mapping):
        return AgentUsage()
    prompt = integer(raw.get('input_tokens'))
    cache_hit = integer(raw.get('cached_input_tokens'))
    completion = integer(raw.get('output_tokens'))
    detail = raw.get('output_tokens_details')
    # Codex CLI 0.148.0 的公开 ``turn.completed.usage`` 把这格命名为
    # ``reasoning_output_tokens``；保留旧/嵌套名字只为兼容已见协议版本。
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
    return AgentUsage(
        prompt_tokens=prompt,
        prompt_cache_hit_tokens=cache_hit,
        prompt_cache_miss_tokens=cache_miss,
        completion_tokens=completion,
        reasoning_tokens=reasoning,
        total_tokens=total,
    )


def _sum_optional(values: Sequence[int | None]) -> int | None:
    return sum(value or 0 for value in values) if any(
        value is not None for value in values) else None


def _usage(events: Sequence[Mapping[str, Any]]) -> AgentUsage:
    """按终态 turn 事件相加；失败轮的已发生计量也不能丢。

    ``error`` 可能是 ``turn.failed`` 的补充诊断：有明确 turn 终态时
    不再加它，避免同一份 usage 重复结算；只有启动前 error 时才读它。
    """
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
    rows = [
        _usage_row(event.get('usage'))
        for event in terminal
    ]
    if not rows:
        return AgentUsage()
    return AgentUsage(
        prompt_tokens=_sum_optional([row.prompt_tokens for row in rows]),
        prompt_cache_hit_tokens=_sum_optional(
            [row.prompt_cache_hit_tokens for row in rows]),
        prompt_cache_miss_tokens=_sum_optional(
            [row.prompt_cache_miss_tokens for row in rows]),
        completion_tokens=_sum_optional(
            [row.completion_tokens for row in rows]),
        reasoning_tokens=_sum_optional([row.reasoning_tokens for row in rows]),
        total_tokens=_sum_optional([row.total_tokens for row in rows]),
    )


def _nested_string(event: Mapping[str, Any], key: str) -> str | None:
    """只查已知信封层，避免从 tool 输出里的同名业务字段误认供应商元数据。"""
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


def _last_metadata(events: Sequence[Mapping[str, Any]],
                   *keys: str) -> str | None:
    found: str | None = None
    for event in events:
        for key in keys:
            value = _nested_string(event, key)
            if value is not None:
                found = value
                break
    return found


def _agent_message(events: Sequence[Mapping[str, Any]]) -> str:
    messages: list[str] = []
    for event in events:
        if event.get('type') != 'item.completed':
            continue
        item = event.get('item')
        if not isinstance(item, Mapping) or item.get('type') != 'agent_message':
            continue
        text = item.get('text')
        if isinstance(text, str):
            messages.append(text)
    return messages[-1] if messages else ''


def _failure_detail(event: Mapping[str, Any] | None) -> str:
    """只读失败事件登记的错误格；完整内容仅在内存中分类，不回显正文。

    provider 常把多层错误序列化进 message，根因可能在长前缀之后。不能先按
    日志显示长度截断再分类，否则明确的连接/限额错误会退化为未知命令失败。
    """
    if event is None:
        return ''
    values: list[str] = []
    for key in ('error', 'message', 'code', 'status'):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value)
        elif isinstance(value, int) and not isinstance(value, bool):
            values.append(str(value))
        elif isinstance(value, Mapping):
            for nested in ('type', 'code', 'message', 'status'):
                item = value.get(nested)
                if isinstance(item, (str, int)) and not isinstance(item, bool):
                    values.append(str(item))
    return ' '.join(values)


def _failure_status(event: Mapping[str, Any] | None) -> int | None:
    if event is None:
        return None
    for source in (event, event.get('error')):
        if not isinstance(source, Mapping):
            continue
        for key in ('status', 'status_code'):
            value = integer(source.get(key))
            if value is not None and 100 <= value <= 599:
                return value
    return None


def codex_failure_event(
    events: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """Executor 与 Commander 共用的当前失败权威，不读回答或重连旧诊断。"""
    # Codex 的 WebSocket/TLS 重连会先发 error 再发 turn.completed；
    # 已被成功终态覆盖的过程诊断不能成为后来 CLI 失败的重试理由。
    last_error: Mapping[str, Any] | None = None
    for event in reversed(events):
        if event.get('type') == 'turn.completed':
            break
        if event.get('type') == 'turn.failed':
            return event
        if event.get('type') == 'error' and last_error is None:
            last_error = event
    return last_error


def codex_failure_evidence(
    events: Sequence[Mapping[str, Any]], stderr: str,
) -> tuple[str, int | None]:
    """显式终态即使错误格为空也有权威；stderr 只补完全没有终态的进程失败。"""
    if (any(event.get('type') == 'turn.failed' for event in events)
            and any(event.get('type') == 'turn.completed' for event in events)):
        # 同一 invocation 的失败/成功终态矛盾。失败事实仍阻止采纳回答，
        # 但不能借成功后的 stderr 警告重新取得网络重试权。
        return '', None
    event = codex_failure_event(events)
    if event is not None:
        return _failure_detail(event), _failure_status(event)
    return stderr, None


def _summary(events: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    """保留事件骨架，不复制 agent_message、command 输出或错误正文。"""
    rows: list[dict[str, Any]] = []
    for event in events:
        row: dict[str, Any] = {'type': event.get('type')}
        if event.get('type') == 'thread.started':
            row['thread_id'] = event.get('thread_id')
        item = event.get('item')
        if isinstance(item, Mapping):
            row['item_type'] = item.get('type')
            if isinstance(item.get('id'), str):
                row['item_id'] = item.get('id')
        if event.get('type') in ('turn.completed', 'turn.failed', 'error'):
            usage = _usage_row(event.get('usage'))
            row['usage'] = {
                'prompt_tokens': usage.prompt_tokens,
                'prompt_cache_hit_tokens': usage.prompt_cache_hit_tokens,
                'prompt_cache_miss_tokens': usage.prompt_cache_miss_tokens,
                'completion_tokens': usage.completion_tokens,
                'reasoning_tokens': usage.reasoning_tokens,
                'total_tokens': usage.total_tokens,
            }
        model = _nested_string(event, 'model')
        effort = (_nested_string(event, 'reasoning_effort')
                  or _nested_string(event, 'effort'))
        if model is not None:
            row['model'] = model
        if effort is not None:
            row['reasoning_effort'] = effort
        rows.append(row)
    return tuple(rows)


def _write_all(
        descriptor: int,
        payload: bytes,
        *,
        writer: Callable[[int, bytes | memoryview], int] = os.write,
) -> None:
    """``os.write`` 允许部分写；循环到 schema 的每个字节都在盘上。"""
    remaining = memoryview(payload)
    while remaining:
        written = writer(descriptor, remaining)
        if written <= 0 or written > len(remaining):
            raise OSError('Codex output schema 临时文件未完整写入')
        remaining = remaining[written:]


class CodexDriver(CliAgentDriver):
    """Codex ``exec`` / ``exec resume`` 的供应商中立适配器。"""

    driver = 'codex'
    provider = 'openai'
    command = 'codex'
    # ``ultra`` 留在 AgentProfile 的未来取值面，本驱动尚无受测 CLI 映射，故不宣称。
    supported_reasoning_efforts = frozenset(
        {'none', 'low', 'medium', 'high', 'xhigh', 'max'})

    def __init__(self, *, executable: str | None = None,
                 timeout: float = 1800,
                 runner: Runner | RunnerCallable | None = None) -> None:
        super().__init__(
            executable=executable, timeout=timeout, runner=runner)

    @contextmanager
    def _schema_path(self, request: AgentRequest) -> Iterator[str | None]:
        encoded = compact_schema(request.output_schema)
        if encoded is None:
            yield None
            return
        # ``--output-schema`` 只接收文件。临时目录在 runner 返回后立即擦除，
        # 不把逐节点 schema 另落一份会漂移的长期副本。
        with tempfile.TemporaryDirectory(prefix='novel-codex-schema-') as root:
            path = Path(root) / 'output.schema.json'
            # 这是只活到本次子进程返回的 wire 临时文件，不是工作流产物；用低层
            # 独占创建把它与 ArtifactStore 的正式写入口在代码形状上也分开。
            descriptor = os.open(
                path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                _write_all(descriptor, encoded.encode('utf-8'))
            finally:
                os.close(descriptor)
            yield str(path)

    def _argv(self, profile: AgentProfile, *, session_id: str | None,
              schema_path: str | None) -> list[str]:
        if session_id is None:
            argv = [self.executable, 'exec']
        else:
            argv = [self.executable, 'exec', 'resume']
        argv.extend((
            '--json',
            '--model', profile.model,
            '--config',
            f'model_reasoning_effort={json.dumps(profile.reasoning_effort)}',
            # resume 子命令没有独立 ``--sandbox`` flag；用两边都支持的严格
            # config 覆盖，保证首轮/续轮均为 read-only。用户配置与 rules 全部
            # 忽略，cwd 由 base 关闭式验证为空，skip-git 只允许这个空目录运行。
            '--config', 'sandbox_mode="read-only"',
            '--ignore-user-config',
            '--ignore-rules',
            '--strict-config',
            '--skip-git-repo-check',
        ))
        if schema_path is not None:
            argv.extend(('--output-schema', schema_path))
        if session_id is not None:
            argv.append(session_id)
        argv.append('-')
        return argv

    def _decode(self, completed: subprocess.CompletedProcess[str], *,
        expected_session_id: str | None) -> AgentTurn:
        try:
            events = _events(completed.stdout or '')
        except AgentProtocolError:
            if completed.returncode != 0:
                self._raise_for_exit(completed)
            raise
        failed_event = codex_failure_event(events)
        failed = (completed.returncode != 0 or failed_event is not None
                  or any(event.get('type') == 'turn.failed' for event in events))
        # 启动前的 auth/config error 可以合法地没有 thread.started；先识别
        # 失败，才不会把一次命令失败误分成 wire 协议损坏。成功仍必须有 id。
        session_id = _optional_thread_id(events)
        if (expected_session_id is not None and session_id is not None
                and session_id != expected_session_id):
            raise AgentProtocolError(
                'Codex resume 的 thread.started 返回了不同 id；拒绝串错会话')
        usage = _usage(events)
        content = _agent_message(events)
        # 只数本次 invocation 已完成的 provider turn；失败尝试不冒充
        # completed，logical session 轮号由上层审计账另行严格递增。
        completed_turns = sum(
            event.get('type') == 'turn.completed' for event in events)
        turn = (None if session_id is None else AgentTurn(
            session_id=session_id, content=content,
            model=_last_metadata(events, 'model'), usage=usage,
            turn_count=completed_turns,
            actual_reasoning_effort=_last_metadata(
                events, 'reasoning_effort', 'effort'),
            raw_event_summary=_summary(events)))
        if failed:
            kind = None if failed_event is None else failed_event.get('type')
            failure_detail, failure_status = codex_failure_evidence(
                events, completed.stderr or '')
            error_kind, status, retryable = classify_agent_command_failure(
                failure_detail, usage=usage, status=failure_status)
            raise AgentCommandError(
                f'Codex CLI 失败（exit={completed.returncode}，event={kind!r}，'
                f'kind={error_kind!r}，status={status!r}，'
                f'detail_chars={len(failure_detail)}，'
                f'detail_sha256={hashlib.sha256(failure_detail.encode("utf-8")).hexdigest()}）',
                returncode=completed.returncode,
                # 失败事件前可能已经吐过 agent_message；只保留 usage/session 供结算。
                partial_turn=(None if turn is None else replace(turn, content='')),
                usage=usage, error_kind=error_kind, status=status,
                retryable=retryable)
        if session_id is None:
            raise AgentProtocolError(
                'Codex 成功事件流没有 thread.started.thread_id')
        assert turn is not None
        if completed_turns < 1:
            raise AgentProtocolError('Codex 事件流没有 turn.completed')
        if not content:
            raise AgentProtocolError('Codex 成功事件流没有最终 agent_message')
        return turn

    def _invoke(self, profile: AgentProfile, request: AgentRequest, *,
                session_id: str | None) -> AgentTurn:
        self._check_profile(profile)
        cwd = self._prepare_request_cwd(
            request, session_id=session_id)
        with self._schema_path(request) as schema_path:
            completed = self._run(
                self._argv(
                    profile, session_id=session_id, schema_path=schema_path),
                request, session_id=session_id)
        turn = self._decode(
            completed, expected_session_id=session_id)
        self._bind_session_cwd(turn.session_id, cwd)
        return turn

    def start(self, profile: AgentProfile, request: AgentRequest) -> AgentTurn:
        return self._invoke(profile, request, session_id=None)

    def resume(self, profile: AgentProfile, session_id: str,
               request: AgentRequest) -> AgentTurn:
        if not session_id.strip():
            raise AgentProtocolError('resume session_id 不得为空')
        return self._invoke(profile, request, session_id=session_id)
