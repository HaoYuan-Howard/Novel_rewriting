#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公共可恢复 JSONL 子进程调用层。

executor 与 Commander 两种 CLI 驱动此前各自 Popen：一边整块 ``communicate``、
无实时日志、只有总超时；另一边三线程泵、实时日志、24 小时总超时——而进程组
创建、进程树终止、按 owner 匹配的 cancel/close 逐字重复。这里只定义一次：

- 三线程泵（stdin / stdout / stderr）；stdout 逐行流式解析，能识别的 JSON 事件
  即时回调 ``on_event``（供应商 session/thread id 由此在线落盘）；
- 三档超时：首事件（Popen 后迟迟没有协议事件）、空闲（两次输出之间的静默）、
  总时长；超时都先 SIGTERM/taskkill 整棵树，再强杀，最后把已收到的部分输出
  与事件随异常交回，调用方据此判断能否同席 resume；
- 进程组/进程树终止、按 owner 匹配的 cancel、幂等 close、Popen 后 launch 回调
  （Supervisor 的 launch ticket）。

它不知道 Stage、席位、账本，也不判断成功：退出码、stdout、stderr、事件与超时
种类原样交给驱动；错误分类留在驱动层（``classify_agent_command_failure``）。
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, BinaryIO, Literal

from novel.orchestrator.process_tree import signal_process_tree


TimeoutKind = Literal['first_event', 'idle', 'total']


class RunnerError(RuntimeError):
    """公共 runner 的共同父类；驱动层再映射成各自的 typed 异常。"""


class RunnerClosedError(RunnerError):
    """runner 已 close，不能再启动进程。"""


class RunnerBusyError(RunnerError):
    """同一 runner 实例任何时刻最多拥有一棵进程树。"""


class RunnerCancelledBeforeStart(RunnerError):
    """cancel 恰落在 Popen 前的窄缝；下一次 run 关闭式拒绝启动。"""


class ProcessTimeout(RunnerError):
    """三档超时之一；携带已收到的部分输出与事件。"""

    def __init__(
        self, kind: TimeoutKind, *, limit: float, elapsed: float,
        argv: Sequence[str], stdout: str, stderr: str,
        events: Sequence[Mapping[str, Any]],
    ) -> None:
        super().__init__(
            f'子进程 {kind} 超时：limit={limit:g}s elapsed={elapsed:.1f}s')
        self.kind: TimeoutKind = kind
        self.limit = limit
        self.elapsed = elapsed
        self.argv = tuple(argv)
        self.stdout = stdout
        self.stderr = stderr
        self.events = tuple(events)


@dataclass(frozen=True)
class StreamedProcess:
    """一次子进程运行的原始事实；不声称协议合法或交付成功。"""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    events: tuple[Mapping[str, Any], ...]
    first_event_seconds: float | None
    unparsed_lines: int



def _tagged_log(log: BinaryIO, label: str, payload: bytes) -> None:
    """把两条流明确分栏并即时 flush；空块不伪造一段日志。"""
    if not payload:
        return
    log.write(f'[{label}]\n'.encode('ascii'))
    log.write(payload)
    if not payload.endswith(b'\n'):
        log.write(b'\n')
    log.flush()


def _positive_or_none(value: float | None, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f'{label} 必须是正数或 None')
    return float(value)


class JsonlProcessRunner:
    """默认可取消 runner；一个实例任何时刻最多拥有一棵进程树。"""

    _CHUNK_SIZE = 64 * 1024
    _POLL_SECONDS = 0.25
    _GRACE_SECONDS = 5.0

    def __init__(
        self, *, first_event_timeout: float | None = None,
        idle_timeout: float | None = None,
    ) -> None:
        self.first_event_timeout = _positive_or_none(
            first_event_timeout, 'first_event_timeout')
        self.idle_timeout = _positive_or_none(idle_timeout, 'idle_timeout')
        self._state_lock = threading.Lock()
        self._log_lock = threading.Lock()
        self._active: tuple[subprocess.Popen[bytes], str | None] | None = None
        self._closed = False
        self._cancel_before_start: set[str | None] = set()
        self._launch_callback: Callable[[], None] | None = None

    # ── owner / ticket ────────────────────────────────────────────────
    def set_launch_callback(self, callback: Callable[[], None]) -> None:
        with self._state_lock:
            if self._active is not None or self._launch_callback is not None:
                raise RunnerBusyError('launch callback 已占用')
            self._launch_callback = callback

    def _owns(self, process: subprocess.Popen[Any]) -> bool:
        with self._state_lock:
            return self._active is not None and self._active[0] is process

    def cancel(self, owner: str | None = None) -> bool:
        with self._state_lock:
            active = self._active
            if active is None:
                # driver owner 先于 Popen 登记，stop 可能恰落在这条窄缝。
                # 记住精确 owner，让随后的 run 在同一 state lock 内关闭式拒绝。
                self._cancel_before_start.add(owner)
                return True
            process, active_owner = active
            if owner is not None and owner != active_owner:
                return False
        return signal_process_tree(process, force=True)

    def close(self) -> None:
        with self._state_lock:
            self._closed = True
            self._cancel_before_start.clear()
            self._launch_callback = None
            active = self._active
        if active is not None:
            signal_process_tree(active[0], force=True)

    # ── run ───────────────────────────────────────────────────────────
    def run(
        self,
        argv: Sequence[str],
        *,
        stdin_bytes: bytes | None,
        cwd: str | None,
        env: Mapping[str, str] | None,
        timeout: float,
        owner: str | None = None,
        log: BinaryIO | None = None,
        on_event: Callable[[Mapping[str, Any]], None] | None = None,
        parse_events: bool = True,
    ) -> StreamedProcess:
        if timeout <= 0:
            raise ValueError('timeout 必须大于 0')
        with self._state_lock:
            if self._closed:
                raise RunnerClosedError('runner 已关闭')
            if self._active is not None:
                raise RunnerBusyError('同一 runner 实例不能并发启动两棵进程树')
            if owner in self._cancel_before_start:
                self._cancel_before_start.discard(owner)
                raise RunnerCancelledBeforeStart(
                    '在 Popen 前已收到 cancel，拒绝启动进程树')
            options: dict[str, Any] = {}
            if os.name == 'nt':
                # 子 CLI 会继续拉工具/novel.cli 孙进程；独立进程组是按 PID 用
                # ``taskkill /T`` 收整棵树的前提，不能退回只杀 CLI 根进程。
                options['creationflags'] = (
                    subprocess.CREATE_NO_WINDOW
                    | subprocess.CREATE_NEW_PROCESS_GROUP)
            else:
                # 新 session 同时建立 pgid=pid；取消时只向这一组发信号，不碰本服务
                # 或并行节点所在的进程组。
                options['start_new_session'] = True
            process = subprocess.Popen(
                list(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd=cwd,
                env=(None if env is None else dict(env)),
                text=False, shell=False, **options)
            self._active = (process, owner)
            callback, self._launch_callback = self._launch_callback, None

        started = time.monotonic()
        if callback is not None:
            try:
                callback()
            except BaseException:
                signal_process_tree(process, force=True)
                with self._state_lock:
                    if self._active is not None and self._active[0] is process:
                        self._active = None
                raise

        stdout = bytearray()
        stderr = bytearray()
        events: list[Mapping[str, Any]] = []
        activity = {
            'last': started, 'first_event': None, 'unparsed': 0}
        activity_lock = threading.Lock()
        pump_errors: list[BaseException] = []
        callback_errors: list[BaseException] = []

        def touch() -> None:
            with activity_lock:
                activity['last'] = time.monotonic()

        def consume_line(raw_line: bytes) -> None:
            text = raw_line.decode('utf-8', errors='replace').strip()
            if not text:
                return
            try:
                decoded = decode_wire_json(text)
            except Exception:  # noqa: BLE001 —— 人读日志行不是协议事件
                with activity_lock:
                    activity['unparsed'] += 1
                return
            if not isinstance(decoded, Mapping) or not isinstance(
                    decoded.get('type'), str):
                with activity_lock:
                    activity['unparsed'] += 1
                return
            with activity_lock:
                if activity['first_event'] is None:
                    activity['first_event'] = time.monotonic() - started
                events.append(decoded)
            if on_event is not None:
                try:
                    on_event(decoded)
                except BaseException as exc:  # noqa: BLE001 —— 回调不能杀进程
                    callback_errors.append(exc)

        def pump_stdin() -> None:
            assert process.stdin is not None
            try:
                if stdin_bytes:
                    process.stdin.write(stdin_bytes)
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

        def pump_stdout() -> None:
            assert process.stdout is not None
            pending = bytearray()
            try:
                while True:
                    # ``read(n)`` 在管道上会阻塞到凑满 n 字节；``read1`` 只做一次
                    # 底层读，字节一到就返回——首事件/空闲超时与在线回调都靠它。
                    block = process.stdout.read1(self._CHUNK_SIZE)
                    if not block:
                        break
                    stdout.extend(block)
                    touch()
                    if log is not None:
                        with self._log_lock:
                            _tagged_log(log, 'stdout protocol', block)
                    if not parse_events:
                        continue
                    pending.extend(block)
                    while True:
                        cut = pending.find(b'\n')
                        if cut < 0:
                            break
                        line = bytes(pending[:cut])
                        del pending[:cut + 1]
                        consume_line(line)
                if parse_events and pending:
                    consume_line(bytes(pending))
            except BaseException as exc:  # noqa: BLE001
                pump_errors.append(exc)

        def pump_stderr() -> None:
            assert process.stderr is not None
            try:
                while True:
                    block = process.stderr.read1(self._CHUNK_SIZE)
                    if not block:
                        return
                    stderr.extend(block)
                    touch()
                    if log is not None:
                        with self._log_lock:
                            _tagged_log(log, 'stderr', block)
            except BaseException as exc:  # noqa: BLE001
                pump_errors.append(exc)

        threads = (
            threading.Thread(target=pump_stdin, name='jsonl-runner-stdin'),
            threading.Thread(target=pump_stdout, name='jsonl-runner-stdout'),
            threading.Thread(target=pump_stderr, name='jsonl-runner-stderr'),
        )
        for thread in threads:
            thread.daemon = True
            thread.start()

        def terminate_and_join() -> None:
            signal_process_tree(process, force=False)
            try:
                process.wait(timeout=self._GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                signal_process_tree(process, force=True)
                process.wait()
            for thread in threads:
                thread.join(timeout=self._GRACE_SECONDS)

        try:
            deadline = started + float(timeout)
            while True:
                try:
                    process.wait(timeout=self._POLL_SECONDS)
                    break
                except subprocess.TimeoutExpired:
                    pass
                now = time.monotonic()
                with activity_lock:
                    last = activity['last']
                    first_event = activity['first_event']
                kind: TimeoutKind | None = None
                limit = float(timeout)
                if now >= deadline:
                    kind = 'total'
                elif (self.first_event_timeout is not None and parse_events
                        and first_event is None
                        and now - started >= self.first_event_timeout):
                    kind, limit = 'first_event', self.first_event_timeout
                elif (self.idle_timeout is not None
                        and now - last >= self.idle_timeout):
                    kind, limit = 'idle', self.idle_timeout
                if kind is None:
                    continue
                terminate_and_join()
                raise ProcessTimeout(
                    kind, limit=limit, elapsed=now - started, argv=argv,
                    stdout=bytes(stdout).decode('utf-8', errors='replace'),
                    stderr=bytes(stderr).decode('utf-8', errors='replace'),
                    events=tuple(events))
            for thread in threads:
                thread.join()
            if pump_errors:
                raise RunnerError(
                    f'子进程 stdio 泵失败：{type(pump_errors[0]).__name__}'
                ) from pump_errors[0]
            if callback_errors:
                raise RunnerError(
                    f'on_event 回调失败：{type(callback_errors[0]).__name__}'
                ) from callback_errors[0]
            with activity_lock:
                first_event = activity['first_event']
                unparsed = activity['unparsed']
            return StreamedProcess(
                argv=tuple(argv), returncode=int(process.returncode),
                stdout=bytes(stdout).decode('utf-8', errors='replace'),
                stderr=bytes(stderr).decode('utf-8', errors='replace'),
                events=tuple(events), first_event_seconds=first_event,
                unparsed_lines=unparsed)
        except BaseException:
            # KeyboardInterrupt / 调用线程取消也不能把一只失去所有权的 CLI 留在后台。
            if self._owns(process) and process.poll() is None:
                signal_process_tree(process, force=True)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
            for thread in threads:
                thread.join(timeout=self._GRACE_SECONDS)
            raise
        finally:
            with self._state_lock:
                if self._active is not None and self._active[0] is process:
                    self._active = None


__all__ = [
    'JsonlProcessRunner', 'ProcessTimeout', 'RunnerBusyError',
    'RunnerCancelledBeforeStart', 'RunnerClosedError', 'RunnerError',
    'StreamedProcess', 'TimeoutKind', 'decode_wire_json',
    'signal_process_tree',
]
