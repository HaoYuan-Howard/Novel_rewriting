#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
base.py —— 长驻整书 Commander 的供应商中立进程边界。

Commander 与逐节点 executor 是两种不同生命周期：前者在仓根读取 skill、调用
``novel.cli`` 并持续维护终审包，后者在空目录中只交付一个候选。因此这里不复用
``AgentRequest``，也不把 commander 权限偷偷塞进 executor driver。

提示词永远走 stdin。尤其在 Windows 上，多行正文进 argv 不只会撞 32767 字符上限，
还可能被 ``cmd /c`` 截在第一行后仍然 exit 0。默认 runner 直接 ``Popen(list)``，
并发泵入 stdin、读取 stdout 与 stderr；两条输出既分标签进入运行日志，也保留原始
stdout 给协议解析，不能用「进程退出了」代替「协议可审计」。
"""
from __future__ import annotations

import io
import os
import subprocess
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, BinaryIO, Protocol

from contracts.schema.orchestration import AgentProfile
from novel.agents.base import (
    AgentUsage,
    _signal_process_tree,
    decode_wire_json,
    integer,
    resolve_cli_entry,
)


class CommanderError(RuntimeError):
    """Commander driver 的共同父类。"""


class CommanderConfigurationError(CommanderError):
    """本地配置不成立；在任何模型请求前关闭式拒绝。"""


class CommanderBusyError(CommanderError):
    """同一 driver 实例已经拥有一棵运行中的 commander 进程树。"""


class CommanderProtocolError(CommanderError):
    """CLI 已返回，但 stdout 不满足声明的机读协议。"""


class CommanderCommandError(CommanderError):
    """CLI 无法启动或超时；不把完整 prompt 放进异常文本。"""

    def __init__(self, message: str, *, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


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

    def __post_init__(self) -> None:
        argv = tuple(self.argv)
        if not argv or any(not isinstance(item, str) or not item
                           for item in argv):
            raise CommanderConfigurationError(
                'CommanderInvocation.argv 必须是非空字符串 tuple')
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
    """外层进程事实；它不声称工作流产物已经可交付。"""

    exit_code: int
    observation: CommanderObservation

    def __post_init__(self) -> None:
        if isinstance(self.exit_code, bool) or not isinstance(
                self.exit_code, int):
            raise CommanderProtocolError('commander exit_code 必须是整数')


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


class _SubprocessCommanderRunner:
    """默认 runner：一实例只持有一棵可按 run_id 熄火的进程树。

    三条 pipe 各有独立泵线程，避免“大 prompt 填满 stdin，而 CLI 同时把 stdout
    或 stderr 填满”形成互等。协议 stdout 另存原始字节，日志则带流标签；解析
    永远不消费被标签污染的日志副本。
    """

    _CHUNK_SIZE = 64 * 1024

    def __init__(self) -> None:
        self._state_lock = threading.Lock()
        self._log_lock = threading.Lock()
        self._active: tuple[subprocess.Popen[bytes], str | None] | None = None
        self._cancel_before_start: set[str | None] = set()
        self._launch_callback: Callable[[], None] | None = None
        self._closed = False

    def set_launch_callback(self, callback: Callable[[], None]) -> None:
        with self._state_lock:
            if self._active is not None or self._launch_callback is not None:
                raise CommanderBusyError('Commander launch callback 已占用')
            self._launch_callback = callback

    def run(
        self,
        invocation: CommanderInvocation,
        *,
        timeout: float,
        run_id: str | None,
        log: BinaryIO,
    ) -> subprocess.CompletedProcess[str]:
        with self._state_lock:
            if self._closed:
                raise CommanderConfigurationError('commander runner 已关闭')
            if self._active is not None:
                raise CommanderBusyError(
                    '同一 CommanderDriver 实例不能并发启动两棵进程树')
            if run_id in self._cancel_before_start:
                self._cancel_before_start.discard(run_id)
                raise CommanderCommandError(
                    'commander CLI 在 Popen 前已收到 stop，拒绝启动进程树',
                    returncode=130)
            options: dict[str, Any] = {}
            if os.name == 'nt':
                options['creationflags'] = (
                    subprocess.CREATE_NO_WINDOW
                    | subprocess.CREATE_NEW_PROCESS_GROUP)
            else:
                options['start_new_session'] = True
            env = os.environ.copy()
            env.update(invocation.env_overrides)
            process = subprocess.Popen(
                list(invocation.argv), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=invocation.cwd, env=env, shell=False, **options)
            self._active = (process, run_id)
            callback, self._launch_callback = self._launch_callback, None

        if callback is not None:
            try:
                callback()
            except BaseException:
                _signal_process_tree(process, force=True)
                with self._state_lock:
                    if self._active is not None and self._active[0] is process:
                        self._active = None
                raise

        stdout = bytearray()
        stderr = bytearray()
        pump_errors: list[BaseException] = []

        def pump_stdin() -> None:
            assert process.stdin is not None
            try:
                process.stdin.write(invocation.stdin_text.encode('utf-8'))
                process.stdin.flush()
            except BrokenPipeError:
                # CLI 在读完/拒绝配置后可先退出；退出码与协议会给出真正结论。
                pass
            except BaseException as exc:  # noqa: BLE001 —— 回传 owner 线程
                pump_errors.append(exc)
            finally:
                try:
                    process.stdin.close()
                except OSError:
                    pass

        def pump_output(
            stream: io.BufferedReader,
            target: bytearray,
            label: str,
        ) -> None:
            try:
                while True:
                    block = stream.read(self._CHUNK_SIZE)
                    if not block:
                        return
                    target.extend(block)
                    with self._log_lock:
                        _tagged_log(log, label, block)
            except BaseException as exc:  # noqa: BLE001 —— 回传 owner 线程
                pump_errors.append(exc)

        assert process.stdout is not None and process.stderr is not None
        threads = (
            threading.Thread(target=pump_stdin, name='commander-stdin'),
            threading.Thread(
                target=pump_output,
                args=(process.stdout, stdout, 'stdout protocol'),
                name='commander-stdout'),
            threading.Thread(
                target=pump_output,
                args=(process.stderr, stderr, 'stderr'),
                name='commander-stderr'),
        )
        for thread in threads:
            thread.start()
        try:
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                _signal_process_tree(process, force=False)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _signal_process_tree(process, force=True)
                    process.wait()
                raise CommanderCommandError(
                    f'commander CLI 超过 {timeout:g} 秒，已终止本轮进程树') from exc
            for thread in threads:
                thread.join()
            if pump_errors:
                raise CommanderCommandError(
                    'commander stdio 泵失败：'
                    f'{type(pump_errors[0]).__name__}') from pump_errors[0]
            return subprocess.CompletedProcess(
                list(invocation.argv), int(process.returncode),
                bytes(stdout).decode('utf-8', errors='replace'),
                bytes(stderr).decode('utf-8', errors='replace'))
        except BaseException:
            if self._owns(process) and process.poll() is None:
                _signal_process_tree(process, force=True)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
            for thread in threads:
                thread.join(timeout=5)
            raise
        finally:
            with self._state_lock:
                if self._active is not None and self._active[0] is process:
                    self._active = None

    def cancel(self, run_id: str | None = None) -> bool:
        with self._state_lock:
            active = self._active
            if active is None:
                # driver owner 先于 Popen 登记，stop 可能恰落在这条窄缝。
                # 记住精确 run_id，让随后的 run 在同一 state lock 内关闭式拒绝；
                # 否则 cancel 返回时看不见进程，下一行却能把它启动出来。
                self._cancel_before_start.add(run_id)
                return True
            process, active_run_id = active
            if run_id is not None and run_id != active_run_id:
                return False
        return _signal_process_tree(process, force=True)

    def close(self) -> None:
        with self._state_lock:
            self._closed = True
            self._cancel_before_start.clear()
            self._launch_callback = None
            active = self._active
        if active is not None:
            _signal_process_tree(active[0], force=True)

    def _owns(self, process: subprocess.Popen[bytes]) -> bool:
        with self._state_lock:
            return self._active is not None and self._active[0] is process


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
    ) -> None:
        if timeout <= 0:
            raise CommanderConfigurationError('timeout 必须大于 0')
        try:
            self.executable = resolve_cli_entry(self.command, executable)
        except Exception as exc:
            raise CommanderConfigurationError(str(exc)) from exc
        self.timeout = float(timeout)
        if runner is None:
            self._runner: CommanderRunner = _SubprocessCommanderRunner()
        elif callable(runner) and not hasattr(runner, 'run'):
            self._runner = _CallableRunner(runner)
        else:
            self._runner = runner  # type: ignore[assignment]

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
    def _decode(
        self,
        stdout: str,
        *,
        profile: AgentProfile,
        exit_code: int,
        expected_provider_session_id: str | None,
    ) -> CommanderObservation:
        """把供应商协议归一；不读取 stderr 猜身份。"""

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
        except CommanderError:
            raise
        except subprocess.TimeoutExpired as exc:
            raise CommanderCommandError(
                f'commander CLI 超过 {self.timeout:g} 秒') from exc
        except OSError as exc:
            raise CommanderCommandError(
                f'commander CLI 无法启动：{type(exc).__name__}') from exc
        exit_code = int(completed.returncode)
        observation = self._decode(
            _text(completed.stdout), profile=profile, exit_code=exit_code,
            expected_provider_session_id=(
                invocation.resume_provider_session_id))
        return CommanderProcessResult(
            exit_code=exit_code, observation=observation)

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
