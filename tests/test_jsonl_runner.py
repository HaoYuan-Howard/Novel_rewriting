#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公共 JSONL 子进程 runner：流式事件、三档超时、owner 取消。只起 Python，不碰模型 CLI。"""
from __future__ import annotations

import io
import sys
import threading
import time

import pytest

from novel.agents import jsonl_runner as R


def _run(runner: R.JsonlProcessRunner, source: str, **kwargs):
    return runner.run(
        [sys.executable, '-I', '-u', '-c', source],
        stdin_bytes=kwargs.pop('stdin_bytes', b''), cwd=None, env=None,
        timeout=kwargs.pop('timeout', 20), **kwargs)


def test_streams_events_as_they_arrive_and_keeps_raw_stdout():
    source = (
        'import sys, time\n'
        'print(\'{"type":"thread.started","thread_id":"t-1"}\', flush=True)\n'
        'time.sleep(0.2)\n'
        'print("human log line", flush=True)\n'
        'print(\'{"type":"turn.completed"}\', flush=True)\n'
        'sys.stderr.write("diag\\n")\n'
        'payload = sys.stdin.buffer.read()\n'
        'print(\'{"type":"stdin","n":\' + str(len(payload)) + \'}\', flush=True)\n'
    )
    seen: list[tuple[str, float]] = []
    started = time.monotonic()
    runner = R.JsonlProcessRunner()
    log = io.BytesIO()
    try:
        result = _run(
            runner, source, stdin_bytes=b'x' * 3,
            on_event=lambda ev: seen.append((ev['type'], time.monotonic() - started)),
            log=log)
    finally:
        runner.close()

    assert result.returncode == 0
    assert [ev['type'] for ev in result.events] == [
        'thread.started', 'turn.completed', 'stdin']
    assert result.events[-1]['n'] == 3
    assert result.unparsed_lines == 1, '人读日志行不是协议事件，但也不炸'
    assert result.first_event_seconds is not None
    assert [name for name, _ in seen] == ['thread.started', 'turn.completed', 'stdin']
    assert seen[1][1] - seen[0][1] >= 0.15, '事件必须在到达时回调，而不是进程结束后一次性回放'
    assert 'human log line' in result.stdout
    assert result.stderr.replace('\r\n', '\n') == 'diag\n', '原始字节照收，不做换行归一'
    rendered = log.getvalue()
    assert b'[stdout protocol]\n' in rendered and b'[stderr]\n' in rendered


def test_first_event_timeout_kills_a_silent_process_and_reports_no_events():
    runner = R.JsonlProcessRunner(first_event_timeout=0.6)
    source = 'import time\ntime.sleep(30)\n'
    started = time.monotonic()
    try:
        with pytest.raises(R.ProcessTimeout) as info:
            _run(runner, source)
    finally:
        runner.close()
    assert info.value.kind == 'first_event'
    assert info.value.events == ()
    assert time.monotonic() - started < 15


def test_idle_timeout_keeps_already_received_events_for_resume():
    runner = R.JsonlProcessRunner(idle_timeout=0.8)
    source = (
        'import time\n'
        'print(\'{"type":"system","subtype":"init","session_id":"s-1"}\', flush=True)\n'
        'time.sleep(30)\n'
    )
    try:
        with pytest.raises(R.ProcessTimeout) as info:
            _run(runner, source)
    finally:
        runner.close()
    assert info.value.kind == 'idle'
    assert [ev['session_id'] for ev in info.value.events] == ['s-1']
    assert 's-1' in info.value.stdout


def test_total_timeout_and_parse_events_off_never_parse_stdout():
    runner = R.JsonlProcessRunner(first_event_timeout=0.3)
    source = 'print(\'{"type":"x"}\', flush=True)\nimport time\ntime.sleep(30)\n'
    try:
        with pytest.raises(R.ProcessTimeout) as info:
            _run(runner, source, timeout=1.0, parse_events=False)
    finally:
        runner.close()
    # parse_events=False 时首事件超时不生效，只剩总超时。
    assert info.value.kind == 'total'
    assert info.value.events == ()


def test_cancel_before_start_and_owner_scoped_cancel():
    runner = R.JsonlProcessRunner()
    assert runner.cancel('owner-a') is True, '无活动进程时记住窄缝取消'
    with pytest.raises(R.RunnerCancelledBeforeStart):
        _run(runner, 'print(1)', owner='owner-a')
    result = _run(runner, 'print(1)', owner='owner-a')
    assert result.returncode == 0

    source = 'import time\nprint(\'{"type":"ready"}\', flush=True)\ntime.sleep(30)\n'
    ready = threading.Event()
    results: list[object] = []

    def go() -> None:
        try:
            results.append(_run(
                runner, source, owner='owner-b',
                on_event=lambda _ev: ready.set()))
        except BaseException as exc:  # noqa: BLE001
            results.append(exc)

    thread = threading.Thread(target=go)
    thread.start()
    try:
        assert ready.wait(10)
        assert runner.cancel('someone-else') is False
        assert runner.cancel('owner-b') is True
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert isinstance(results[0], R.StreamedProcess)
        assert results[0].returncode != 0
    finally:
        runner.close()


def test_busy_and_closed_are_refused_closed_form():
    runner = R.JsonlProcessRunner()
    source = 'import time\nprint(\'{"type":"ready"}\', flush=True)\ntime.sleep(5)\n'
    ready = threading.Event()
    thread = threading.Thread(
        target=lambda: _run(runner, source, on_event=lambda _ev: ready.set()))
    thread.start()
    try:
        assert ready.wait(10)
        with pytest.raises(R.RunnerBusyError):
            _run(runner, 'print(1)')
    finally:
        runner.close()
        thread.join(timeout=10)
    with pytest.raises(R.RunnerClosedError):
        _run(runner, 'print(1)')


def test_on_event_callback_failure_is_reported_after_the_process_ends():
    runner = R.JsonlProcessRunner()
    source = 'print(\'{"type":"a"}\', flush=True)\nprint(\'{"type":"b"}\', flush=True)\n'

    def boom(_ev):
        raise RuntimeError('pointer write failed')

    try:
        with pytest.raises(R.RunnerError, match='on_event 回调失败'):
            _run(runner, source, on_event=boom)
    finally:
        runner.close()
