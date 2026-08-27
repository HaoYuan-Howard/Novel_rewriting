#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Commander driver 的零成本协议测试。

这里的 CLI stdout 全由 fake 或 Python sentinel 产生；绝不拉 Claude/Codex、绝不
读取账号、绝不发送模型请求。fake 能看见 invocation 组装，真实 sentinel 则专门
跨过 Popen/三条 pipe 那道缝，防止超长 stdin 与双输出互锁。
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from contracts.schema.orchestration import AgentProfile
from novel.commanders import (
    ClaudeCodeCommanderDriver,
    CodexCommanderDriver,
    CommanderInvocation,
    CommanderProtocolError,
    create_commander_driver,
)
from novel.commanders import base as commander_base
from novel.commanders.claude_code import _posix_anchor


def _profile(driver: str) -> AgentProfile:
    if driver == 'claude-code':
        return AgentProfile(
            driver='claude-code', model='claude-opus-5',
            reasoning_effort='high', execution_mode='standard')
    return AgentProfile(
        driver='codex', model='gpt-5.6-sol',
        reasoning_effort='max', execution_mode='standard')


def _completed(argv, stdout: str, stderr: str = '', returncode: int = 0):
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


def _claude_stream() -> str:
    events = (
        {
            'type': 'system', 'subtype': 'init',
            'session_id': 'claude-session-1',
            'model': 'claude-opus-5', 'effort': 'high',
        },
        {
            'type': 'assistant', 'session_id': 'claude-session-1',
            'message': {'model': 'claude-opus-5'},
        },
        {
            'type': 'result', 'subtype': 'success', 'is_error': False,
            'session_id': 'claude-session-1',
            'modelUsage': {
                'claude-opus-5': {
                    'inputTokens': 10,
                    'cacheReadInputTokens': 2,
                    'cacheCreationInputTokens': 3,
                    'outputTokens': 7,
                },
            },
            'usage': {'output_tokens_details': {'thinking_tokens': 4}},
        },
    )
    return ''.join(json.dumps(event) + '\n' for event in events)


def _codex_stream() -> str:
    events = (
        {
            'type': 'thread.started', 'thread_id': 'codex-thread-1',
            'model': 'gpt-5.6-sol', 'reasoning_effort': 'max',
        },
        {'type': 'turn.started'},
        {
            'type': 'turn.completed',
            'usage': {
                'input_tokens': 20, 'cached_input_tokens': 5,
                'output_tokens': 8,
                'output_tokens_details': {'reasoning_tokens': 6},
            },
        },
    )
    return ''.join(json.dumps(event) + '\n' for event in events)


def test_claude_invocation_is_exact_and_prompt_only_uses_stdin(tmp_path):
    repo = tmp_path / 'repo'
    base = tmp_path / 'outputs'
    repo.mkdir()
    base.mkdir()
    prompt = ('第一行\n第二行：' + '甲' * 40_000)
    executable = str(tmp_path / 'claude-sentinel.exe')
    driver = ClaudeCodeCommanderDriver(executable=executable, runner=lambda *a, **k: None)

    invocation = driver.build_invocation(
        _profile('claude-code'), prompt, repo=repo, base=base,
        env_overrides={'NOVEL_COMMANDER_PROFILE': 'frozen'},
        cli_version='2.1.test')

    expected_tools = (
        f'Bash({Path(sys.executable).as_posix()} *)',
        f'Edit({_posix_anchor(base)}/**)',
    )
    assert invocation.argv == (
        executable,
        '-p',
        '--input-format', 'text',
        '--output-format', 'stream-json',
        '--verbose',
        '--model', 'claude-opus-5',
        '--effort', 'high',
        '--permission-mode', 'manual',
        '--no-chrome',
        '--mcp-config', '{"mcpServers":{}}',
        '--strict-mcp-config',
        '--add-dir', str(base.resolve()),
        '--allowedTools',
        *expected_tools,
    )
    assert invocation.stdin_text == prompt
    assert prompt not in invocation.argv
    assert '--fallback-model' not in invocation.argv
    assert len(prompt) > 32_767
    assert invocation.cwd == str(repo.resolve())
    assert invocation.protocol == 'claude-stream-json-v1'
    assert invocation.cli_version == '2.1.test'


def test_codex_invocation_is_exact_and_prompt_only_uses_stdin(tmp_path):
    repo = tmp_path / 'repo'
    base = tmp_path / 'outputs'
    repo.mkdir()
    base.mkdir()
    prompt = '任务\n' + '乙' * 40_000
    executable = str(tmp_path / 'codex-sentinel.exe')
    driver = CodexCommanderDriver(executable=executable, runner=lambda *a, **k: None)

    invocation = driver.build_invocation(
        _profile('codex'), prompt, repo=repo, base=base,
        cli_version='0.148.test')

    assert invocation.argv == (
        executable,
        'exec',
        '--json',
        '--model', 'gpt-5.6-sol',
        '--config', 'model_reasoning_effort="max"',
        '--config', 'approval_policy="never"',
        '--sandbox', 'workspace-write',
        '--config', 'sandbox_workspace_write.network_access=false',
        '--ignore-user-config',
        '--strict-config',
        '--cd', str(repo.resolve()),
        '--add-dir', str(base.resolve()),
        '-',
    )
    assert invocation.stdin_text == prompt
    assert prompt not in invocation.argv
    assert invocation.argv[-1] == '-'
    assert len(prompt) > 32_767
    assert invocation.cwd == str(repo.resolve())
    assert invocation.protocol == 'codex-jsonl-v1'
    assert invocation.cli_version == '0.148.test'


def test_invocation_copies_environment_overrides_into_an_immutable_snapshot(tmp_path):
    repo = tmp_path / 'repo'
    base = tmp_path / 'base'
    repo.mkdir()
    base.mkdir()
    supplied = {'NOVEL_FROZEN_PROFILE': 'before'}
    invocation = CodexCommanderDriver(
        executable='codex.exe', runner=lambda *a, **k: None,
    ).build_invocation(
        _profile('codex'), '任务', repo=repo, base=base,
        env_overrides=supplied)

    supplied['NOVEL_FROZEN_PROFILE'] = 'after'
    assert invocation.env_overrides['NOVEL_FROZEN_PROFILE'] == 'before'
    with pytest.raises(TypeError):
        invocation.env_overrides['NOVEL_FROZEN_PROFILE'] = 'mutated'  # type: ignore[index]


@pytest.mark.parametrize(
    ('driver_cls', 'driver_name', 'stdout', 'expected'),
    [
        (
            ClaudeCodeCommanderDriver, 'claude-code', _claude_stream(),
            ('claude-session-1', 'claude-opus-5', 'high', 15, 7, 4, 22),
        ),
        (
            CodexCommanderDriver, 'codex', _codex_stream(),
            ('codex-thread-1', 'gpt-5.6-sol', 'max', 20, 8, 6, 28),
        ),
    ],
)
def test_run_decodes_provider_observation_and_keeps_logs_split(
        tmp_path, driver_cls, driver_name, stdout, expected):
    repo = tmp_path / 'repo'
    base = tmp_path / 'base'
    repo.mkdir()
    base.mkdir()
    seen: dict[str, object] = {}

    def runner(argv, **kwargs):
        seen['argv'] = argv
        seen.update(kwargs)
        return _completed(argv, stdout, 'diagnostic only\n')

    driver = driver_cls(
        executable=('claude.exe' if driver_name == 'claude-code' else 'codex.exe'),
        runner=runner)
    invocation = driver.build_invocation(
        _profile(driver_name), '多行\n任务', repo=repo, base=base)
    log = io.BytesIO()
    result = driver.run(invocation, log=log, run_id='run-1')

    observation = result.observation
    assert result.exit_code == 0
    assert (
        observation.provider_session_id,
        observation.actual_model,
        observation.actual_effort,
    ) == expected[:3]
    assert observation.usage is not None
    assert (
        observation.usage.prompt_tokens,
        observation.usage.completion_tokens,
        observation.usage.reasoning_tokens,
        observation.usage.total_tokens,
    ) == expected[3:]
    assert seen['input'] == '多行\n任务'
    assert seen['cwd'] == str(repo.resolve())
    assert seen['shell'] is False
    rendered_log = log.getvalue()
    assert b'[stdout protocol]\n' in rendered_log
    assert b'[stderr]\n' in rendered_log


@pytest.mark.parametrize(
    ('driver_cls', 'driver_name'),
    [
        (ClaudeCodeCommanderDriver, 'claude-code'),
        (CodexCommanderDriver, 'codex'),
    ],
)
def test_success_with_malformed_protocol_is_never_accepted(
        tmp_path, driver_cls, driver_name):
    repo = tmp_path / 'repo'
    base = tmp_path / 'base'
    repo.mkdir()
    base.mkdir()

    def runner(argv, **kwargs):
        del kwargs
        return _completed(argv, 'human log leaked into protocol\n')

    driver = driver_cls(
        executable=('claude.exe' if driver_name == 'claude-code' else 'codex.exe'),
        runner=runner)
    invocation = driver.build_invocation(
        _profile(driver_name), '任务', repo=repo, base=base)
    with pytest.raises(CommanderProtocolError, match='不是合法 JSON'):
        driver.run(invocation, log=io.BytesIO(), run_id='bad-wire')


def test_nonzero_exit_without_a_session_keeps_observation_unknown(tmp_path):
    repo = tmp_path / 'repo'
    base = tmp_path / 'base'
    repo.mkdir()
    base.mkdir()

    def runner(argv, **kwargs):
        del kwargs
        return _completed(argv, '', 'auth refused', returncode=9)

    driver = CodexCommanderDriver(executable='codex.exe', runner=runner)
    invocation = driver.build_invocation(
        _profile('codex'), '任务', repo=repo, base=base)
    result = driver.run(invocation, log=io.BytesIO(), run_id='failed-start')
    assert result.exit_code == 9
    assert result.observation.provider_session_id is None
    assert result.observation.actual_model is None
    assert result.observation.actual_effort is None
    assert result.observation.usage is None


class _OwnedRunner:
    """可注入 runner 证明 cancel 只按本次 run_id，不扫描其他进程。"""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.released = threading.Event()
        self.active_run_id: str | None = None
        self.closed = False

    def run(self, invocation, *, timeout, run_id, log):
        del timeout, log
        self.active_run_id = run_id
        self.started.set()
        assert self.released.wait(5)
        self.active_run_id = None
        return _completed(invocation.argv, _codex_stream())

    def cancel(self, run_id=None):
        if self.active_run_id is None:
            return False
        if run_id is not None and run_id != self.active_run_id:
            return False
        self.released.set()
        return True

    def close(self):
        self.closed = True
        self.cancel()


def test_cancel_is_scoped_to_this_commander_run(tmp_path):
    repo = tmp_path / 'repo'
    base = tmp_path / 'base'
    repo.mkdir()
    base.mkdir()
    runner = _OwnedRunner()
    driver = CodexCommanderDriver(executable='codex.exe', runner=runner)
    invocation = driver.build_invocation(
        _profile('codex'), '任务', repo=repo, base=base)
    results: list[object] = []
    thread = threading.Thread(
        target=lambda: results.append(driver.run(
            invocation, log=io.BytesIO(), run_id='owned-run')))
    thread.start()
    assert runner.started.wait(2)
    assert driver.cancel('somebody-elses-run') is False
    assert runner.released.is_set() is False
    assert driver.cancel('owned-run') is True
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert results and results[0].observation.provider_session_id == 'codex-thread-1'
    driver.close()
    assert runner.closed is True


def test_default_runner_pumps_large_stdin_stdout_and_stderr_concurrently(tmp_path):
    """真跨 pipe：三边都超过常见缓冲区，串行读写会在这里互锁。"""
    source = (
        'import sys\n'
        'sys.stdout.buffer.write(b"O" * 200000)\n'
        'sys.stdout.buffer.flush()\n'
        'sys.stderr.buffer.write(b"E" * 200000)\n'
        'sys.stderr.buffer.flush()\n'
        'payload = sys.stdin.buffer.read()\n'
        'sys.stdout.buffer.write(b"\\nLEN=" + str(len(payload)).encode() + b"\\n")\n'
    )
    prompt = '甲' * 100_000
    invocation = CommanderInvocation(
        argv=(sys.executable, '-I', '-c', source),
        stdin_text=prompt,
        cwd=str(tmp_path.resolve()),
        env_overrides={},
        profile=_profile('codex'),
        protocol='sentinel-jsonl-v1',
    )
    runner = commander_base._SubprocessCommanderRunner()
    log = io.BytesIO()
    try:
        completed = runner.run(
            invocation, timeout=10, run_id='pipe-sentinel', log=log)
    finally:
        runner.close()

    assert completed.returncode == 0
    assert f'LEN={len(prompt.encode("utf-8"))}' in completed.stdout
    assert len(completed.stderr) == 200_000
    assert b'[stdout protocol]\n' in log.getvalue()
    assert b'[stderr]\n' in log.getvalue()


def test_factory_selects_the_exact_profile_driver_without_fallback():
    claude = create_commander_driver(
        _profile('claude-code'), executable='claude.exe',
        runner=lambda *a, **k: None)
    codex = create_commander_driver(
        _profile('codex'), executable='codex.exe',
        runner=lambda *a, **k: None)
    assert isinstance(claude, ClaudeCodeCommanderDriver)
    assert isinstance(codex, CodexCommanderDriver)


def test_default_runner_cancels_only_its_owned_process_tree(tmp_path):
    """真跨进程验证 root + grandchild；仍然只起 Python，不碰模型 CLI。"""
    ready = tmp_path / 'owned.ready'
    survived = tmp_path / 'grandchild-survived.txt'
    child_source = (
        'import time\n'
        'from pathlib import Path\n'
        'time.sleep(1.5)\n'
        f'Path({str(survived)!r}).write_text("survived", encoding="utf-8")\n'
    )
    parent_source = (
        'import subprocess, sys, time\n'
        'from pathlib import Path\n'
        f'subprocess.Popen([sys.executable, "-c", {child_source!r}])\n'
        f'Path({str(ready)!r}).write_text("ready", encoding="utf-8")\n'
        'time.sleep(30)\n'
    )
    invocation = CommanderInvocation(
        argv=(sys.executable, '-I', '-c', parent_source),
        stdin_text='sentinel', cwd=str(tmp_path.resolve()), env_overrides={},
        profile=_profile('codex'), protocol='sentinel-jsonl-v1')
    runner = commander_base._SubprocessCommanderRunner()
    results: list[object] = []

    def run_owned() -> None:
        try:
            results.append(runner.run(
                invocation, timeout=20, run_id='owned-run', log=io.BytesIO()))
        except BaseException as exc:  # noqa: BLE001 —— 子线程异常回传主用例
            results.append(exc)

    thread = threading.Thread(target=run_owned)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not ready.is_file() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.is_file(), '受控根进程没有拉起孙进程'
        assert runner.cancel('another-run') is False
        assert thread.is_alive()
        assert runner.cancel('owned-run') is True
        thread.join(timeout=8)
        assert not thread.is_alive(), '取消后进程树仍占着 stdout/stderr 管道'
        time.sleep(1.8)
        assert not survived.exists(), '只杀了 commander 根进程，孙进程仍在后台'
        assert results and isinstance(results[0], subprocess.CompletedProcess)
    finally:
        runner.close()
        if thread.is_alive():
            thread.join(timeout=2)
