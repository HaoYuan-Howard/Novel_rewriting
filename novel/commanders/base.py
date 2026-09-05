#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
base.py —— 长驻整书 Commander 的供应商中立进程边界。

Commander 与逐节点 executor 是两种不同生命周期：前者只读不可变
review workpack 并交付一份 typed 判断，后者在隔离目录中交付候选。
节点调度、permit 与正式写入属于确定性 Supervisor，不属于 Commander。
因此这里不复用 ``AgentRequest``，也不把 commander 权限偷偷塞进
executor driver。

提示词永远走 stdin。尤其在 Windows 上，多行正文进 argv 不只会撞 32767 字符上限，
还可能被 ``cmd /c`` 截在第一行后仍然 exit 0。默认 runner 直接 ``Popen(list)``，
并发泵入 stdin、读取 stdout 与 stderr；两条输出既分标签进入运行日志，也保留原始
stdout 给协议解析，不能用「进程退出了」代替「协议可审计」。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, BinaryIO, Protocol

from contracts.schema.orchestration import AgentProfile
from contracts.invariants import (
    COMMANDER_FIRST_EVENT_TIMEOUT_SECONDS, COMMANDER_IDLE_TIMEOUT_SECONDS,
)
from novel.agents.base import (
    AgentConfigurationError,
    AgentUsage,
    classify_agent_command_failure,
    compact_schema,
    integer,
    resolve_cli_entry,
    validate_agent_output_schema,
)
from novel.agents.jsonl_runner import (
    JsonlProcessRunner, ProcessTimeout, RunnerBusyError,
    RunnerCancelledBeforeStart, RunnerClosedError, RunnerError,
    decode_wire_json,
)


class CommanderError(RuntimeError):
    """Commander driver 的共同父类。"""


class CommanderConfigurationError(CommanderError):
    """本地配置不成立；在任何模型请求前关闭式拒绝。"""


class CommanderBusyError(CommanderError):
    """同一 driver 实例已经拥有一棵运行中的 commander 进程树。"""


class CommanderProtocolError(CommanderError):
    """CLI 已返回，但 stdout 不满足声明的机读协议。"""

    def __init__(
        self, message: str, *,
        partial_observation: CommanderObservation | None = None,
    ) -> None:
        super().__init__(message)
        # 协议矛盾可能发生在 thread/usage 已经到达之后。它们仍是
        # 已发生的 provider 事实，不能因为最终业务结论不可信就从成本/
        # session 审计中消失。
        self.partial_observation = partial_observation
        self.failure_kind = 'protocol'
        self.retryable = False
        self.wire_evidence = (
            partial_observation is not None
            and (partial_observation.provider_session_id is not None
                 or partial_observation.usage is not None))


class CommanderCommandError(CommanderError):
    """CLI 无法启动或超时；不把完整 prompt 放进异常文本。"""

    def __init__(self, message: str, *, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


def _reject_json_constant(value: str) -> None:
    """标准 JSON 没有 NaN/Infinity；不能把 Python 扩展值送给不同 provider。"""
    raise ValueError(f'非法 JSON 常量 {value}')


def _prepare_output_schema(
    schema: Mapping[str, Any] | None,
) -> tuple[dict[str, Any] | None, bytes | None, str | None]:
    """复用 executor 的 portable 子集，并冻结 Commander 的规范字节身份。"""
    if schema is None:
        return None, None, None
    if not isinstance(schema, Mapping):
        raise CommanderConfigurationError('output_schema 必须是 JSON object')
    try:
        validate_agent_output_schema(schema)
        encoded = compact_schema(schema)
        assert encoded is not None
        # ``json.dumps`` 默认容许 NaN/Infinity；两家 CLI 并不共享这个 Python
        # 扩展，故在任何 runner/Popen 前重新按标准 JSON 关闭式解析。
        snapshot = json.loads(encoded, parse_constant=_reject_json_constant)
        if not isinstance(snapshot, dict):
            raise ValueError('schema 顶层不是 object')
        canonical = json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False)
    except (AgentConfigurationError, TypeError, ValueError) as exc:
        raise CommanderConfigurationError(str(exc)) from exc
    payload = canonical.encode('utf-8')
    return snapshot, payload, hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class CommanderInvocation:
    """一次 commander 启动的完整、不可变快照。

    ``env_overrides`` 只保存本轮明确覆盖的键，不复制整份宿主环境；构造时仍会
    深复制并包成 ``MappingProxyType``，排队后修改调用方 dict 不会让运行漂移。
    ``cli_version`` 来自同一次 capability/preview 的零成本版本探针；driver 不在
    真跑前暗中重探，也不拿今天的版本倒推历史。
    """

    argv: tuple[str, ...]
    stdin_text: str
    cwd: str
    env_overrides: Mapping[str, str]
    profile: AgentProfile
    protocol: str
    cli_version: str | None = None
    #: ``None`` 表示创建新会话；非空时 driver 必须使用供应商的 exact resume
    #: 参数，并校验 wire 返回的 id 逐字相同。
    resume_provider_session_id: str | None = None
    #: reviewer 不再让模型写 scratch；成功时必须从供应商最终回答
    #: 交付一份裸 JSON，由 Supervisor 严格验证后原子物化。
    structured_response: bool = False
    #: 两家 provider 共用、已归一为严格公共子集的 response schema。调用方
    #: 传入的可变对象只用于构造；真正进入 wire/身份的是下方规范字节与 hash。
    output_schema: Mapping[str, Any] | None = None
    #: exact resume 必须回带上一轮冻结的 schema hash；缺失或漂移都在 runner 前
    #: 关闭，不能只靠同一个 provider session id 猜它仍是同一响应契约。
    expected_output_schema_sha256: str | None = None
    output_schema_bytes: bytes | None = field(init=False, repr=False)
    output_schema_sha256: str | None = field(init=False)

    def __post_init__(self) -> None:
        argv = tuple(self.argv)
        if not argv or any(not isinstance(item, str) for item in argv):
            raise CommanderConfigurationError(
                'CommanderInvocation.argv 必须是字符串 tuple')
        empty_items = [index for index, item in enumerate(argv) if not item]
        if any(
                self.structured_response is not True
                or index == 0 or argv[index - 1] != '--tools'
                for index in empty_items):
            # Claude Code 的唯一结构性“禁用全部 built-ins”语法就是
            # ``--tools ''``；除此以外空 argv token 仍关闭式拒绝。
            raise CommanderConfigurationError(
                'CommanderInvocation.argv 仅允许 structured reviewer 的 '
                '--tools 后出现一个空字符串')
        if not isinstance(self.stdin_text, str) or not self.stdin_text.strip():
            raise CommanderConfigurationError('commander prompt 不得为空')
        if not isinstance(self.cwd, str) or not self.cwd.strip():
            raise CommanderConfigurationError('commander cwd 不得为空')
        if not isinstance(self.protocol, str) or not self.protocol.strip():
            raise CommanderConfigurationError('commander protocol 不得为空')
        if self.cli_version is not None and (
                not isinstance(self.cli_version, str)
                or not self.cli_version.strip()):
            raise CommanderConfigurationError(
                'cli_version 若出现就必须是非空字符串')
        if self.resume_provider_session_id is not None and (
                not isinstance(self.resume_provider_session_id, str)
                or not self.resume_provider_session_id.strip()):
            raise CommanderConfigurationError(
                'resume_provider_session_id 若出现就必须是非空字符串')
        if not isinstance(self.structured_response, bool):
            raise CommanderConfigurationError(
                'structured_response 必须是 bool')
        schema, schema_bytes, schema_sha256 = _prepare_output_schema(
            self.output_schema)
        if schema is not None and not self.structured_response:
            raise CommanderConfigurationError(
                'output_schema 只允许用于 structured_response invocation')
        expected_schema_sha256 = self.expected_output_schema_sha256
        if expected_schema_sha256 is not None and (
                not isinstance(expected_schema_sha256, str)
                or re.fullmatch(r'[0-9a-f]{64}', expected_schema_sha256) is None):
            raise CommanderConfigurationError(
                'expected_output_schema_sha256 必须是小写 SHA-256')
        if expected_schema_sha256 is not None and (
                self.resume_provider_session_id is None or schema_sha256 is None):
            raise CommanderConfigurationError(
                'expected_output_schema_sha256 只允许绑定带 schema 的 exact resume')
        if (self.resume_provider_session_id is not None
                and schema_sha256 is not None
                and expected_schema_sha256 is None):
            raise CommanderConfigurationError(
                '带 output_schema 的 exact resume 必须提供上一轮 schema SHA-256')
        if (expected_schema_sha256 is not None
                and expected_schema_sha256 != schema_sha256):
            raise CommanderConfigurationError(
                'Commander exact resume 的 output_schema 已漂移')
        try:
            snapshot = dict(self.env_overrides)
        except (TypeError, ValueError) as exc:
            raise CommanderConfigurationError(
                'env_overrides 必须是字符串映射') from exc
        if any(not isinstance(key, str) or not key
               or not isinstance(value, str)
               for key, value in snapshot.items()):
            raise CommanderConfigurationError(
                'env_overrides 的键和值都必须是字符串，键不得为空')
        object.__setattr__(self, 'argv', argv)
        object.__setattr__(
            self, 'env_overrides', MappingProxyType(snapshot))
        object.__setattr__(
            self, 'output_schema',
            None if schema is None else MappingProxyType(schema))
        object.__setattr__(self, 'output_schema_bytes', schema_bytes)
        object.__setattr__(self, 'output_schema_sha256', schema_sha256)


@dataclass(frozen=True)
class CommanderObservation:
    """CLI wire 中能证明的实际身份与计量；缺失就是 ``None``，不猜请求值。"""

    provider_session_id: str | None
    actual_model: str | None
    actual_effort: str | None
    usage: AgentUsage | None

    def __post_init__(self) -> None:
        for field_name in (
                'provider_session_id', 'actual_model', 'actual_effort'):
            value = getattr(self, field_name)
            if value is not None and (
                    not isinstance(value, str) or not value.strip()):
                raise CommanderProtocolError(
                    f'{field_name} 若出现就必须是非空字符串')
@dataclass(frozen=True)
class CommanderProcessResult:
    """外层进程事实；它不声称工作流产物已经可交付。

    ``failure_kind`` / ``status`` / ``retryable`` 只在非 0 退出时由驱动按 stderr
    与 usage 归一（与 executor 侧同一分类器）；``wire_evidence`` 表示协议流里
    出现过供应商 session/usage——没有它的失败是 0-wire 本地失败。
    """

    exit_code: int
    observation: CommanderObservation
    failure_kind: str | None = None
    status: int | None = None
    retryable: bool = False
    wire_evidence: bool = False
    #: reviewer 成功 turn 的 provider 最终文本。它不进入身份/计量
    #: observation，也不进入 ledger；Supervisor 会严格验证后才物化。
    final_response: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.exit_code, bool) or not isinstance(
                self.exit_code, int):
            raise CommanderProtocolError('commander exit_code 必须是整数')
        if self.exit_code == 0 and self.failure_kind is not None:
            raise CommanderProtocolError('exit 0 不能携带 failure_kind')
        if self.final_response is not None and (
                not isinstance(self.final_response, str)
                or not self.final_response.strip()):
            raise CommanderProtocolError(
                'final_response 若出现就必须是非空字符串')
        if self.exit_code != 0 and self.final_response is not None:
            raise CommanderProtocolError(
                '非 0 commander 进程不得交付 final_response')


class CommanderRunner(Protocol):
    """可注入进程缝；默认实现与测试 fake 共享这一张面。"""

    def run(
        self,
        invocation: CommanderInvocation,
        *,
        timeout: float,
        run_id: str | None,
        log: BinaryIO,
    ) -> subprocess.CompletedProcess[str]: ...

    def cancel(self, run_id: str | None = None) -> bool: ...

    def close(self) -> None: ...


RunnerCallable = Callable[..., subprocess.CompletedProcess[str]]


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    return value


def _tagged_log(log: BinaryIO, label: str, payload: str | bytes | None) -> None:
    """把两条流明确分栏；空流不伪造一段日志。"""
    if payload is None or payload == '' or payload == b'':
        return
    encoded = payload if isinstance(payload, bytes) else payload.encode(
        'utf-8', errors='replace')
    log.write(f'[{label}]\n'.encode('ascii'))
    log.write(encoded)
    if not encoded.endswith(b'\n'):
        log.write(b'\n')
    log.flush()


class _CallableRunner:
    """兼容 ``subprocess.run`` 形 fake；只有默认 runner 承诺可中途取消。"""

    def __init__(self, call: RunnerCallable) -> None:
        self._call = call
        self._closed = False
        self._launch_callback: Callable[[], None] | None = None

    def set_launch_callback(self, callback: Callable[[], None]) -> None:
        self._launch_callback = callback

    def run(
        self,
        invocation: CommanderInvocation,
        *,
        timeout: float,
        run_id: str | None,
        log: BinaryIO,
    ) -> subprocess.CompletedProcess[str]:
        del run_id
        if self._closed:
            raise CommanderConfigurationError('commander runner 已关闭')
        callback, self._launch_callback = self._launch_callback, None
        if callback is not None:
            callback()
        env = os.environ.copy()
        env.update(invocation.env_overrides)
        completed = self._call(
            list(invocation.argv), input=invocation.stdin_text,
            cwd=invocation.cwd, env=env, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=timeout,
            check=False, shell=False)
        _tagged_log(log, 'stdout protocol', completed.stdout)
        _tagged_log(log, 'stderr', completed.stderr)
        return completed

    def cancel(self, run_id: str | None = None) -> bool:
        del run_id
        return False

    def close(self) -> None:
        self._closed = True


class CommanderTimeoutError(CommanderCommandError):
    """三档超时之一；携带已解析的部分事件供驱动判断能否同席 resume。"""

    def __init__(
        self, message: str, *, timeout_kind: str,
        partial_stdout: str, partial_stderr: str,
        events: Sequence[Mapping[str, Any]],
    ) -> None:
        super().__init__(message, returncode=None)
        self.timeout_kind = timeout_kind
        self.partial_stdout = partial_stdout
        self.partial_stderr = partial_stderr
        self.events = tuple(events)
        # 由 ``CliCommanderDriver.run`` 在解码部分事件后补齐。
        self.partial_observation: CommanderObservation | None = None
        self.failure_kind = 'timeout'
        self.retryable = True
        self.wire_evidence = bool(self.events)


class _SubprocessCommanderRunner:
    """默认可取消 runner；进程组、三线程泵、实时日志与取消全部来自公共 runner。

    Commander 协议是流式 JSONL：这里开启逐行解析与首事件/空闲超时，并把每个
    协议事件即时交给 ``on_event``（供应商 session id 由此在线落盘）。
    """

    def __init__(
        self, *, first_event_timeout: float | None = None,
        idle_timeout: float | None = None,
    ) -> None:
        self._runner = JsonlProcessRunner(
            first_event_timeout=first_event_timeout,
            idle_timeout=idle_timeout)
        self._on_event: Callable[[Mapping[str, Any]], None] | None = None

    def set_launch_callback(self, callback: Callable[[], None]) -> None:
        try:
            self._runner.set_launch_callback(callback)
        except RunnerBusyError as exc:
            raise CommanderBusyError('Commander launch callback 已占用') from exc

    def set_event_callback(
        self, callback: Callable[[Mapping[str, Any]], None] | None,
    ) -> None:
        self._on_event = callback

    def run(
        self,
        invocation: CommanderInvocation,
        *,
        timeout: float,
        run_id: str | None,
        log: BinaryIO,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(invocation.env_overrides)
        try:
            streamed = self._runner.run(
                list(invocation.argv),
                stdin_bytes=invocation.stdin_text.encode('utf-8'),
                cwd=invocation.cwd, env=env, timeout=timeout, owner=run_id,
                log=log, on_event=self._on_event, parse_events=True)
        except RunnerClosedError as exc:
            raise CommanderConfigurationError('commander runner 已关闭') from exc
        except RunnerBusyError as exc:
            raise CommanderBusyError(
                '同一 CommanderDriver 实例不能并发启动两棵进程树') from exc
        except RunnerCancelledBeforeStart as exc:
            raise CommanderCommandError(
                'commander CLI 在 Popen 前已收到 stop，拒绝启动进程树',
                returncode=130) from exc
        except ProcessTimeout as exc:
            raise CommanderTimeoutError(
                f'commander CLI {exc.kind} 超过 {exc.limit:g} 秒，已终止本轮进程树',
                timeout_kind=exc.kind, partial_stdout=exc.stdout,
                partial_stderr=exc.stderr, events=exc.events) from exc
        except RunnerError as exc:
            raise CommanderCommandError(
                f'commander stdio 泵失败：{type(exc.__cause__ or exc).__name__}'
            ) from exc
        return subprocess.CompletedProcess(
            list(invocation.argv), streamed.returncode,
            streamed.stdout, streamed.stderr)

    def cancel(self, run_id: str | None = None) -> bool:
        return self._runner.cancel(run_id)

    def close(self) -> None:
        self._runner.close()


def jsonl_events(stdout: str, *, provider: str,
                 allow_empty: bool = False) -> list[Mapping[str, Any]]:
    """严格解析 stdout JSONL；人读日志夹进协议流也必须当场失败。"""
    events: list[Mapping[str, Any]] = []
    for line_number, line in enumerate(stdout.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = decode_wire_json(line)
        except Exception as exc:  # JSONDecodeError 的实现细节不外泄到公共面
            raise CommanderProtocolError(
                f'{provider} 协议第 {line_number} 行不是合法 JSON') from exc
        if not isinstance(raw, Mapping):
            raise CommanderProtocolError(
                f'{provider} 协议第 {line_number} 行顶层不是 object')
        if not isinstance(raw.get('type'), str) or not raw.get('type'):
            raise CommanderProtocolError(
                f'{provider} 协议第 {line_number} 行缺少事件 type')
        events.append(raw)
    if not events and not allow_empty:
        raise CommanderProtocolError(f'{provider} 协议没有返回任何事件')
    return events


def optional_usage(**values: int | None) -> AgentUsage | None:
    """wire 未给任何 token 时保留 unknown，而不是造一排 0。"""
    if not any(value is not None for value in values.values()):
        return None
    return AgentUsage(**values)


def sum_optional(values: Sequence[int | None]) -> int | None:
    return sum(value or 0 for value in values) if any(
        value is not None for value in values) else None


class CommanderDriver(ABC):
    """供应商中立的 Commander 生命周期。"""

    driver: str
    provider: str
    command: str
    protocol: str

    @abstractmethod
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
        """冻结展示与真跑共用的唯一 invocation。"""

    @abstractmethod
    def run(
        self,
        invocation: CommanderInvocation,
        *,
        log: BinaryIO,
        run_id: str | None = None,
    ) -> CommanderProcessResult:
        """执行一次长驻 commander；exit 0 仍不等价于工作流交付成功。"""

    @abstractmethod
    def cancel(self, run_id: str | None = None) -> bool:
        """只终止本 driver 当前拥有、且 run_id 匹配的进程树。"""

    @abstractmethod
    def close(self) -> None:
        """幂等释放；仍在运行时终止本实例拥有的树。"""


class CliCommanderDriver(CommanderDriver):
    """两个 CLI driver 共享的运行/取消骨架。"""

    def __init__(
        self,
        *,
        executable: str | None = None,
        timeout: float = 86_400,
        runner: CommanderRunner | RunnerCallable | None = None,
        first_event_timeout: float | None = COMMANDER_FIRST_EVENT_TIMEOUT_SECONDS,
        idle_timeout: float | None = COMMANDER_IDLE_TIMEOUT_SECONDS,
    ) -> None:
        if timeout <= 0:
            raise CommanderConfigurationError('timeout 必须大于 0')
        try:
            self.executable = resolve_cli_entry(self.command, executable)
        except Exception as exc:
            raise CommanderConfigurationError(str(exc)) from exc
        self.timeout = float(timeout)
        if runner is None:
            self._runner: CommanderRunner = _SubprocessCommanderRunner(
                first_event_timeout=first_event_timeout,
                idle_timeout=idle_timeout)
        elif callable(runner) and not hasattr(runner, 'run'):
            self._runner = _CallableRunner(runner)
        else:
            self._runner = runner  # type: ignore[assignment]

    def set_event_callback(
        self, callback: Callable[[Mapping[str, Any]], None] | None,
    ) -> None:
        """把协议事件即时交给 Supervisor（在线保存 provider session）；fake runner 忽略。"""
        setter = getattr(self._runner, 'set_event_callback', None)
        if setter is not None:
            setter(callback)

    @staticmethod
    def session_id_of_event(event: Mapping[str, Any]) -> str | None:
        """从单个协议事件识别供应商 session id；各驱动按自己的协议覆盖。"""
        del event
        return None

    def _resolved_profile(self, profile: AgentProfile) -> AgentProfile:
        from novel.agent_capabilities import (
            AgentProfileRefused,
            validate_profile,
        )

        try:
            resolved = validate_profile(profile, role='commander')
        except AgentProfileRefused as exc:
            raise CommanderConfigurationError(str(exc)) from exc
        if resolved.driver != self.driver:
            raise CommanderConfigurationError(
                f'{self.__class__.__name__} 不能执行 driver={resolved.driver!r}')
        return resolved

    def _paths(
        self,
        repo: str | os.PathLike[str],
        base: str | os.PathLike[str],
    ) -> tuple[str, str]:
        repo_path = Path(repo).resolve()
        base_path = Path(base).resolve()
        if not repo_path.is_dir():
            raise CommanderConfigurationError(f'仓根不存在或不是目录：{repo_path}')
        if not base_path.is_dir():
            raise CommanderConfigurationError(f'base 不存在或不是目录：{base_path}')
        return str(repo_path), str(base_path)

    @abstractmethod
    def _decode_events(
        self,
        events: Sequence[Mapping[str, Any]],
        *,
        profile: AgentProfile,
        exit_code: int,
        expected_provider_session_id: str | None,
    ) -> CommanderObservation:
        """把已解析的协议事件归一；不读取 stderr 猜身份。"""

    def _final_response(
        self, events: Sequence[Mapping[str, Any]],
    ) -> str | None:
        """从成功协议流取 provider 最终文本；子类只读已知信封。"""
        del events
        return None

    def _validate_structured_events(
        self,
        events: Sequence[Mapping[str, Any]],
        observation: CommanderObservation,
    ) -> None:
        """验证只读 reviewer 的协议能力边界；driver 可按事件协议收紧。"""
        del events, observation

    def _decode(
        self,
        stdout: str,
        *,
        profile: AgentProfile,
        exit_code: int,
        expected_provider_session_id: str | None,
        structured_response: bool,
    ) -> tuple[CommanderObservation, str | None]:
        events = jsonl_events(
            stdout, provider=self.provider_label, allow_empty=exit_code != 0)
        observation = self._decode_events(
            events, profile=profile, exit_code=exit_code,
            expected_provider_session_id=expected_provider_session_id)
        if structured_response and exit_code == 0:
            self._validate_structured_events(events, observation)
        # 非 0 轮次里即使曾出现 agent message 也只是半截草稿，
        # 不得交付或覆盖真实进程失败分类。
        response = (
            self._final_response(events)
            if structured_response and exit_code == 0 else None)
        if (structured_response and exit_code == 0
                and (response is None or not response.strip())):
            raise CommanderProtocolError(
                f'{self.provider_label} 成功事件流没有最终结构化回答',
                partial_observation=observation)
        return observation, response

    provider_label: str = 'commander'

    def run(
        self,
        invocation: CommanderInvocation,
        *,
        log: BinaryIO,
        run_id: str | None = None,
    ) -> CommanderProcessResult:
        if invocation.protocol != self.protocol:
            raise CommanderConfigurationError(
                f'invocation.protocol={invocation.protocol!r} 与 '
                f'driver protocol={self.protocol!r} 不一致')
        profile = self._resolved_profile(invocation.profile)
        if profile != invocation.profile:
            raise CommanderConfigurationError(
                'invocation.profile 未物化为 capability registry 的精确快照')
        try:
            completed = self._runner.run(
                invocation, timeout=self.timeout, run_id=run_id, log=log)
        except CommanderTimeoutError as exc:
            # 超时不是协议失败：把已收到的事件归一成部分观测（session id、
            # usage），调用方据此决定同席 resume 还是 relaunch。
            try:
                exc.partial_observation = self._decode_events(
                    exc.events, profile=profile, exit_code=-1,
                    expected_provider_session_id=(
                        invocation.resume_provider_session_id))
            except CommanderProtocolError:
                # timeout 只描述进程树为何被终止，不能覆盖 wire 已经证明的
                # 会话矛盾。尤其 exact resume 返回另一个 provider session
                # 时，若吞成可重试 timeout，Supervisor 会把它误当成同席可续
                # 或无 session relaunch，反而扩大串会话风险。
                raise
            except CommanderError:
                exc.partial_observation = None
            exc.wire_evidence = bool(exc.events) or (
                exc.partial_observation is not None
                and exc.partial_observation.provider_session_id is not None)
            raise
        except CommanderError:
            raise
        except subprocess.TimeoutExpired as exc:
            raise CommanderCommandError(
                f'commander CLI 超过 {self.timeout:g} 秒') from exc
        except OSError as exc:
            raise CommanderCommandError(
                f'commander CLI 无法启动：{type(exc).__name__}') from exc
        exit_code = int(completed.returncode)
        observation, final_response = self._decode(
            _text(completed.stdout), profile=profile, exit_code=exit_code,
            expected_provider_session_id=(
                invocation.resume_provider_session_id),
            structured_response=invocation.structured_response)
        wire_evidence = (
            observation.provider_session_id is not None
            or observation.usage is not None)
        if exit_code == 0:
            return CommanderProcessResult(
                exit_code=exit_code, observation=observation,
                wire_evidence=wire_evidence,
                final_response=final_response)
        kind, status, retryable = classify_agent_command_failure(
            _text(completed.stderr), usage=observation.usage)
        return CommanderProcessResult(
            exit_code=exit_code, observation=observation,
            failure_kind=kind, status=status, retryable=retryable,
            wire_evidence=wire_evidence)

    def cancel(self, run_id: str | None = None) -> bool:
        return self._runner.cancel(run_id)

    def set_launch_callback(self, callback: Callable[[], None]) -> None:
        """只给 Supervisor launch ticket 使用；默认 runner 在 Popen 后回调。"""
        setter = getattr(self._runner, 'set_launch_callback', None)
        if setter is None:
            raise CommanderConfigurationError(
                '当前 Commander runner 不支持 launch ticket')
        setter(callback)

    def close(self) -> None:
        self._runner.close()


__all__ = [
    'CliCommanderDriver',
    'CommanderBusyError',
    'CommanderCommandError',
    'CommanderTimeoutError',
    'CommanderConfigurationError',
    'CommanderDriver',
    'CommanderError',
    'CommanderInvocation',
    'CommanderObservation',
    'CommanderProcessResult',
    'CommanderProtocolError',
    'CommanderRunner',
    'RunnerCallable',
]
