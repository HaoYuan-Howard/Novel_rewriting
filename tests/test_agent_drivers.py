#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_agent_drivers.py —— provider-neutral agent runtime 的离线协议测试。

本文件不探测真实账号、不发 prompt；wire 用例都经过注入 runner，另有一条
零模型请求的真跨进程用例验证取消边界。重点钉住：模型/effort 没有下发、
resume 串错 session、Codex JSONL/失败计量误读、Windows 退回 ``cmd /c``、
超长 schema 被系统截断，以及 cancel 只杀根进程却留下孙进程。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import novel.agents.base as agent_base
import novel.agents.codex as codex_module
from contracts.schema.orchestration import AgentProfile
from novel.agents import (
    AgentCommandError,
    AgentConfigurationError,
    AgentDriver,
    AgentProtocolError,
    AgentRequest,
    ClaudeCodeDriver,
    CodexDriver,
)
from novel.agents.base import resolve_cli_entry


def _profile(driver: str, *, model: str | None = None,
             effort: str = 'high', mode: str | None = None) -> AgentProfile:
    defaults = {
        'claude-code': 'claude-opus-5',
        'codex': 'gpt-5.6-sol',
    }
    return AgentProfile(
        driver=driver,
        model=model or defaults[driver],
        reasoning_effort=effort,
        execution_mode=mode,
    )


def _request(
    cwd: Path,
    *,
    prompt: str,
    system: str | None = None,
    output_schema: dict[str, object] | None = None,
) -> AgentRequest:
    """测试也必须显式选择隔离策略，不能靠生产代码的隐藏默认。"""
    return AgentRequest(
        prompt=prompt,
        execution_policy='executor-isolated',
        system=system,
        output_schema=output_schema,
        cwd=cwd,
    )


def _completed(argv, stdout: str, *, returncode: int = 0,
               stderr: str = '') -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


def _claude_wrapper(*, session_id: str = 'claude-session-1',
                    result: object = None) -> str:
    if result is None:
        result = {'status': 'done', 'artifact': 'stage2/out.json'}
    return json.dumps({
        'type': 'result',
        'subtype': 'success',
        'is_error': False,
        'session_id': session_id,
        'structured_output': result,
        'num_turns': 3,
        'reasoning_effort': 'high',
        'modelUsage': {
            'claude-opus-5-20260801': {
                'inputTokens': 10,
                'cacheReadInputTokens': 7,
                'cacheCreationInputTokens': 2,
                'outputTokens': 5,
            },
            'claude-haiku-helper': {
                'inputTokens': 1,
                'cacheReadInputTokens': 0,
                'cacheCreationInputTokens': 0,
                'outputTokens': 2,
            },
        },
        'usage': {'output_tokens_details': {'thinking_tokens': 4}},
    }, ensure_ascii=False)


def _codex_jsonl(*, session_id: str = 'codex-session-1',
                 content: str = '{"status":"done"}') -> str:
    return '\n'.join(json.dumps(event, ensure_ascii=False) for event in (
        {
            'type': 'thread.started', 'thread_id': session_id,
            'model': 'gpt-5.6-sol', 'reasoning_effort': 'high',
        },
        {'type': 'turn.started'},
        {
            'type': 'item.completed',
            'item': {'id': 'item-1', 'type': 'command_execution',
                     'aggregated_output': '正文不该进摘要'},
        },
        {
            'type': 'item.completed',
            'item': {'id': 'item-2', 'type': 'agent_message', 'text': content},
        },
        {
            'type': 'turn.completed',
            'usage': {
                'input_tokens': 30,
                'cached_input_tokens': 11,
                'output_tokens': 8,
                'output_tokens_details': {'reasoning_tokens': 3},
            },
        },
    ))


def test_agent_driver_is_an_abstract_lifecycle_contract():
    assert AgentDriver.__abstractmethods__ == {
        'probe', 'start', 'resume', 'cancel', 'close'}


def test_claude_start_uses_json_stdin_and_explicit_model_effort(tmp_path):
    seen: dict[str, object] = {}

    def runner(argv, **kwargs):
        seen['argv'] = argv
        seen['kwargs'] = kwargs
        return _completed(argv, _claude_wrapper())

    driver = ClaudeCodeDriver(executable='claude.exe', runner=runner)
    request = _request(
        tmp_path, prompt='写入最终产物', system='只按证据行动',
        output_schema={
            'type': 'object',
            'properties': {'status': {'type': 'string'}},
            'required': ['status'],
        })
    turn = driver.start(_profile('claude-code'), request)

    argv = seen['argv']
    kwargs = seen['kwargs']
    schema_text = argv[argv.index('--json-schema') + 1]
    assert argv == [
        driver.executable, '-p', '--output-format', 'json',
        '--model', 'claude-opus-5', '--effort', 'high',
        '--safe-mode', '--tools', '',
        '--mcp-config', '{"mcpServers":{}}', '--strict-mcp-config',
        '--json-schema', schema_text,
    ]
    assert json.loads(schema_text)['type'] == 'object'
    assert '--fallback-model' not in argv and '--resume' not in argv
    assert '--no-session-persistence' not in argv
    assert not any('deepseek' in value.casefold() for value in argv)
    assert kwargs['input'] == '只按证据行动\n\n--- 本轮任务 ---\n\n写入最终产物'
    assert kwargs['cwd'] == str(tmp_path.resolve())
    assert kwargs['shell'] is False

    assert turn.session_id == 'claude-session-1'
    assert json.loads(turn.content)['status'] == 'done'
    assert turn.model == 'claude-opus-5-20260801'
    assert turn.actual_reasoning_effort == 'high'
    assert turn.turn_count == 3
    assert turn.usage.prompt_tokens == 20       # 两个模型的 direct + cache 两类
    assert turn.usage.prompt_cache_hit_tokens == 7
    assert turn.usage.prompt_cache_miss_tokens == 13
    assert turn.usage.completion_tokens == 7
    assert turn.usage.reasoning_tokens == 4
    assert turn.usage.total_tokens == 27
    assert 'artifact' not in json.dumps(
        turn.raw_event_summary, ensure_ascii=False)


def test_claude_resume_uses_the_exact_session_id_same_cwd_and_rejects_a_fork(
        tmp_path):
    calls: list[list[str]] = []
    call_cwds: list[str] = []

    def ok_runner(argv, **kwargs):
        calls.append(argv)
        call_cwds.append(kwargs['cwd'])
        return _completed(argv, _claude_wrapper(session_id='kept-session'))

    isolated = tmp_path / 'same-empty-cwd'
    isolated.mkdir()
    driver = ClaudeCodeDriver(executable='claude.exe', runner=ok_runner)
    driver.start(
        _profile('claude-code'),
        _request(isolated, prompt='建立会话', output_schema={'type': 'object'}))
    turn = driver.resume(
        _profile('claude-code'), 'kept-session',
        _request(
            isolated, prompt='这是代码校验反馈',
            output_schema={'type': 'object'}))
    assert turn.session_id == 'kept-session'
    assert turn.turn_count == 3, 'turn_count 只表示本次 invocation 的 provider 读数'
    resume_argv = calls[1]
    schema_text = resume_argv[resume_argv.index('--json-schema') + 1]
    assert resume_argv == [
        driver.executable, '-p', '--output-format', 'json',
        '--model', 'claude-opus-5', '--effort', 'high',
        '--safe-mode', '--tools', '',
        '--mcp-config', '{"mcpServers":{}}', '--strict-mcp-config',
        '--resume', 'kept-session', '--json-schema', schema_text,
    ]
    assert call_cwds == [str(isolated.resolve()), str(isolated.resolve())]
    assert '--session-id' not in resume_argv and '--fork-session' not in resume_argv
    assert '--no-session-persistence' not in resume_argv

    other = tmp_path / 'other-empty-cwd'
    other.mkdir()
    with pytest.raises(AgentConfigurationError, match='同一空 cwd'):
        driver.resume(
            _profile('claude-code'), 'kept-session',
            _request(other, prompt='不得换 cwd'))
    assert len(calls) == 2, '换 cwd 必须在拉起 CLI 前拒绝'

    (isolated / 'provider-session-leak').write_text('unexpected', encoding='utf-8')
    with pytest.raises(AgentConfigurationError, match='必须为空'):
        driver.resume(
            _profile('claude-code'), 'kept-session',
            _request(isolated, prompt='目录已被污染也不得续跑'))
    assert len(calls) == 2, 'resume 前必须重新检查 cwd 仍为空'

    bad = ClaudeCodeDriver(
        executable='claude.exe',
        runner=lambda argv, **_kwargs: _completed(
            argv, _claude_wrapper(session_id='different-session')))
    with pytest.raises(AgentProtocolError, match='不同 session_id'):
        bad.resume(
            _profile('claude-code'), 'kept-session',
            _request(other, prompt='继续'))


def test_claude_requires_cli_session_id_and_keeps_failure_usage(tmp_path):
    missing = json.loads(_claude_wrapper())
    missing.pop('session_id')
    driver = ClaudeCodeDriver(
        executable='claude.exe',
        runner=lambda argv, **_kwargs: _completed(argv, json.dumps(missing)))
    with pytest.raises(AgentProtocolError, match='session_id'):
        driver.start(
            _profile('claude-code'), _request(tmp_path, prompt='开始'))

    failed = json.loads(_claude_wrapper(session_id='paid-but-failed'))
    failed.update({'subtype': 'error_during_execution', 'is_error': True})
    errored = ClaudeCodeDriver(
        executable='claude.exe',
        runner=lambda argv, **_kwargs: _completed(
            argv, json.dumps(failed), returncode=1, stderr='private detail'))
    with pytest.raises(AgentCommandError) as caught:
        errored.start(
            _profile('claude-code'), _request(tmp_path, prompt='开始'))
    assert caught.value.partial_turn is not None
    assert caught.value.partial_turn.session_id == 'paid-but-failed'
    assert caught.value.partial_turn.usage.total_tokens == 27
    assert caught.value.partial_turn.content == ''
    assert 'private detail' not in str(caught.value)


def test_codex_start_uses_jsonl_output_schema_and_explicit_profile(tmp_path):
    seen: dict[str, object] = {}
    schema = {
        'type': 'object',
        'properties': {'status': {'const': 'done'}},
        'required': ['status'],
        'additionalProperties': False,
    }

    def runner(argv, **kwargs):
        seen['argv'] = argv
        seen['kwargs'] = kwargs
        schema_path = Path(argv[argv.index('--output-schema') + 1])
        assert schema_path.is_file()
        assert json.loads(schema_path.read_text(encoding='utf-8')) == schema
        return _completed(argv, _codex_jsonl())

    driver = CodexDriver(executable='codex.exe', runner=runner)
    turn = driver.start(
        _profile('codex'),
        _request(tmp_path, prompt='完成节点并落盘', output_schema=schema))

    argv = seen['argv']
    kwargs = seen['kwargs']
    schema_path = argv[argv.index('--output-schema') + 1]
    assert argv == [
        driver.executable, 'exec', '--json',
        '--model', 'gpt-5.6-sol',
        '--config', 'model_reasoning_effort="high"',
        '--config', 'sandbox_mode="read-only"',
        '--ignore-user-config', '--ignore-rules', '--strict-config',
        '--skip-git-repo-check',
        '--output-schema', schema_path, '-',
    ]
    assert '--last' not in argv and '--ephemeral' not in argv
    assert not any('deepseek' in value.casefold() for value in argv)
    assert kwargs['input'] == '完成节点并落盘'
    assert kwargs['cwd'] == str(tmp_path.resolve())
    assert kwargs['shell'] is False

    assert turn.session_id == 'codex-session-1'
    assert turn.content == '{"status":"done"}'
    assert turn.model == 'gpt-5.6-sol'
    assert turn.actual_reasoning_effort == 'high'
    assert turn.turn_count == 1
    assert turn.usage.prompt_tokens == 30
    assert turn.usage.prompt_cache_hit_tokens == 11
    assert turn.usage.prompt_cache_miss_tokens == 19
    assert turn.usage.completion_tokens == 8
    assert turn.usage.reasoning_tokens == 3
    assert turn.usage.total_tokens == 38
    summary = json.dumps(turn.raw_event_summary, ensure_ascii=False)
    assert '正文不该进摘要' not in summary and '{"status":"done"}' not in summary


def test_codex_resume_uses_exec_resume_same_id_schema_and_cwd(tmp_path):
    seen: list[list[str]] = []
    seen_cwds: list[str] = []

    def runner(argv, **kwargs):
        seen.append(argv)
        seen_cwds.append(kwargs['cwd'])
        return _completed(argv, _codex_jsonl(session_id='thread-42'))

    isolated = tmp_path / 'same-empty-cwd'
    isolated.mkdir()
    driver = CodexDriver(executable='codex.exe', runner=runner)
    driver.start(
        _profile('codex', effort='max'),
        _request(isolated, prompt='建立会话', output_schema={'type': 'object'}))
    turn = driver.resume(
        _profile('codex', effort='max'), 'thread-42',
        _request(
            isolated, prompt='机械校验仍有两项失败',
            output_schema={'type': 'object'}))

    argv = seen[1]
    schema_path = argv[argv.index('--output-schema') + 1]
    assert argv == [
        driver.executable, 'exec', 'resume', '--json',
        '--model', 'gpt-5.6-sol',
        '--config', 'model_reasoning_effort="max"',
        '--config', 'sandbox_mode="read-only"',
        '--ignore-user-config', '--ignore-rules', '--strict-config',
        '--skip-git-repo-check',
        '--output-schema', schema_path, 'thread-42', '-',
    ]
    assert '--last' not in argv and '--ephemeral' not in argv
    assert seen_cwds == [str(isolated.resolve()), str(isolated.resolve())]
    assert turn.session_id == 'thread-42'
    assert turn.turn_count == 1, 'resume 不得把 logical session 累计轮号塞进 turn_count'

    other = tmp_path / 'other-empty-cwd'
    other.mkdir()
    with pytest.raises(AgentConfigurationError, match='同一空 cwd'):
        driver.resume(
            _profile('codex', effort='max'), 'thread-42',
            _request(other, prompt='不得换 cwd'))
    assert len(seen) == 2, '换 cwd 必须在创建 schema/拉起 CLI 前拒绝'


def test_codex_rejects_mismatched_thread_and_malformed_jsonl(tmp_path):
    mismatched = CodexDriver(
        executable='codex.exe',
        runner=lambda argv, **_kwargs: _completed(
            argv, _codex_jsonl(session_id='other-thread')))
    with pytest.raises(AgentProtocolError, match='不同 id'):
        mismatched.resume(
            _profile('codex'), 'expected-thread',
            _request(tmp_path, prompt='继续'))

    malformed = CodexDriver(
        executable='codex.exe',
        runner=lambda argv, **_kwargs: _completed(
            argv, '{"type":"thread.started"}\nnot-json'))
    with pytest.raises(AgentProtocolError, match='不是合法 JSON'):
        malformed.start(_profile('codex'), _request(tmp_path, prompt='开始'))

    paid_then_failed = CodexDriver(
        executable='codex.exe',
        runner=lambda argv, **_kwargs: _completed(
            argv, _codex_jsonl(content='看似完整但不可采纳')
            + '\n' + json.dumps({'type': 'turn.failed', 'error': 'secret'}),
            returncode=1))
    with pytest.raises(AgentCommandError) as caught:
        paid_then_failed.start(
            _profile('codex'), _request(tmp_path, prompt='开始'))
    assert caught.value.partial_turn is not None
    assert caught.value.usage is caught.value.partial_turn.usage
    assert caught.value.partial_turn.usage.total_tokens == 38
    assert caught.value.partial_turn.content == ''
    assert '看似完整' not in str(caught.value)


def test_codex_startup_error_without_thread_keeps_failure_usage(tmp_path):
    """认证/配置可在 thread.started 前失败；这是命令失败而非会话协议损坏。"""
    stdout = json.dumps({
        'type': 'error',
        'message': '不得进入诊断摘要的私密内容',
        'usage': {
            'input_tokens': 9,
            'cached_input_tokens': 4,
            'output_tokens': 2,
        },
    }, ensure_ascii=False)
    driver = CodexDriver(
        executable='codex.exe',
        runner=lambda argv, **_kwargs: _completed(
            argv, stdout, returncode=1, stderr='private stderr'))

    with pytest.raises(AgentCommandError) as caught:
        driver.start(_profile('codex'), _request(tmp_path, prompt='开始'))

    assert caught.value.partial_turn is None, 'CLI 没给 session id 时不能本地伪造 AgentTurn'
    assert caught.value.usage is not None
    assert caught.value.usage.prompt_tokens == 9
    assert caught.value.usage.prompt_cache_hit_tokens == 4
    assert caught.value.usage.total_tokens == 11
    assert 'private' not in str(caught.value) and '私密内容' not in str(caught.value)


def test_codex_failed_turn_usage_is_counted_without_claiming_completion(tmp_path):
    stdout = '\n'.join((
        json.dumps({'type': 'thread.started', 'thread_id': 'failed-thread'}),
        json.dumps({
            'type': 'turn.failed',
            'usage': {'input_tokens': 12, 'cached_input_tokens': 5,
                      'output_tokens': 3},
            'error': {'message': 'secret'},
        }),
    ))
    driver = CodexDriver(
        executable='codex.exe',
        runner=lambda argv, **_kwargs: _completed(argv, stdout, returncode=1))

    with pytest.raises(AgentCommandError) as caught:
        driver.start(_profile('codex'), _request(tmp_path, prompt='开始'))

    assert caught.value.partial_turn is not None
    assert caught.value.partial_turn.turn_count == 0
    assert caught.value.partial_turn.usage.total_tokens == 15


def test_profiles_are_closed_failures_not_cross_vendor_fallbacks(tmp_path):
    never_called = False

    def runner(argv, **_kwargs):
        nonlocal never_called
        never_called = True
        return _completed(argv, '')

    claude = ClaudeCodeDriver(executable='claude.exe', runner=runner)
    with pytest.raises(AgentConfigurationError, match='不能交给'):
        claude.start(
            _profile('codex'), _request(tmp_path, prompt='不能错投'))
    with pytest.raises(AgentConfigurationError, match='execution_mode'):
        claude.start(
            _profile('claude-code', mode='ultra'),
            _request(tmp_path, prompt='不能忽略'))
    assert never_called is False


def test_executor_policy_is_required_and_nonempty_cwd_is_closed_before_cli(
        tmp_path):
    with pytest.raises(TypeError, match='execution_policy'):
        AgentRequest(prompt='不能缺 policy', cwd=tmp_path)  # type: ignore[call-arg]
    with pytest.raises(AgentConfigurationError, match='execution_policy'):
        AgentRequest(
            prompt='不能冒充未来 commander policy',
            execution_policy='commander-workspace',  # type: ignore[arg-type]
            cwd=tmp_path,
        )

    occupied = tmp_path / 'occupied'
    occupied.mkdir()
    (occupied / 'AGENTS.md').write_text('不应被 executor 看到', encoding='utf-8')
    called = False

    def runner(argv, **_kwargs):
        nonlocal called
        called = True
        return _completed(argv, _claude_wrapper())

    driver = ClaudeCodeDriver(executable='claude.exe', runner=runner)
    with pytest.raises(AgentConfigurationError, match='必须为空'):
        driver.start(
            _profile('claude-code'), _request(occupied, prompt='不得启动'))
    assert called is False


def test_windows_shim_is_resolved_to_exe_without_cmd(tmp_path):
    native = tmp_path / 'node_modules' / 'vendor' / 'tool.exe'
    native.parent.mkdir(parents=True)
    native.write_bytes(b'not executed')
    shim = tmp_path / 'tool.cmd'
    shim.write_text(
        '@ECHO off\n"%dp0%\\node_modules\\vendor\\tool.exe" %*\n',
        encoding='utf-8')

    resolved = resolve_cli_entry('tool', str(shim))
    assert Path(resolved) == native.resolve()
    assert Path(resolved).suffix.lower() == '.exe'
    assert 'cmd' not in Path(resolved).name.casefold()

    bad = tmp_path / 'bad.cmd'
    bad.write_text('@node script.js %*\n', encoding='utf-8')
    with pytest.raises(AgentConfigurationError, match='原生可执行入口'):
        resolve_cli_entry('bad', str(bad))


def test_claude_large_inline_schema_is_refused_before_windows_createprocess(
        monkeypatch, tmp_path):
    called = False

    def runner(argv, **_kwargs):
        nonlocal called
        called = True
        return _completed(argv, _claude_wrapper())

    original = agent_base.ensure_command_line_fits
    monkeypatch.setattr(
        agent_base, 'ensure_command_line_fits',
        lambda argv: original(argv, platform_name='nt'))
    driver = ClaudeCodeDriver(executable='claude.exe', runner=runner)
    request = _request(
        tmp_path, prompt='开始',
        output_schema={'type': 'string', 'description': '甲' * 33_000},
    )

    with pytest.raises(AgentConfigurationError, match='CreateProcess'):
        driver.start(_profile('claude-code'), request)
    assert called is False, '超长 schema 必须在任何模型请求前关闭式拒绝'


def test_codex_schema_file_writer_retries_partial_os_writes(tmp_path):
    path = tmp_path / 'schema.json'
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    original = os.write
    calls = 0

    def partial(fd: int, payload: bytes | memoryview) -> int:
        nonlocal calls
        calls += 1
        return original(fd, payload[:3])

    expected = b'{"type":"object","additionalProperties":false}'
    try:
        codex_module._write_all(descriptor, expected, writer=partial)
    finally:
        os.close(descriptor)

    assert path.read_bytes() == expected
    assert calls > 1, '用例必须真的走过 partial write 分支'


def test_probe_is_version_only_and_close_is_idempotent():
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return _completed(argv, 'codex-cli 1.2.3\n')

    driver = CodexDriver(executable='codex.exe', runner=runner)
    probe = driver.probe()
    assert probe.available is True and probe.version == 'codex-cli 1.2.3'
    assert Path(calls[0][0][0]).name == 'codex.exe'
    assert calls[0][0][1:] == ['--version']
    assert calls[0][1] == {
        'input': None,
        'cwd': None,
        'capture_output': True,
        'text': True,
        'encoding': 'utf-8',
        'errors': 'replace',
        'timeout': 15,
        'check': False,
        'shell': False,
    }
    driver.close()
    driver.close()
    assert driver.probe().available is False


def test_executor_empty_cwd_reaches_a_real_zero_cost_sentinel_process(
        monkeypatch, tmp_path):
    """真实 Popen 只观察 cwd；不拉 Claude/Codex，也不接触任何账号。"""
    caller = tmp_path / 'poisoned-caller'
    caller.mkdir()
    (caller / 'AGENTS.md').write_text('caller sentinel', encoding='utf-8')
    (caller / '.mcp.json').write_text('{}', encoding='utf-8')
    isolated = tmp_path / 'executor-empty'
    isolated.mkdir()
    monkeypatch.chdir(caller)

    observed: dict[str, object] = {}
    real_runner = agent_base._SubprocessRunner()
    sentinel_source = (
        'import json, os\n'
        'print(json.dumps({"cwd": os.getcwd(), "entries": os.listdir(".")}))\n'
    )

    def runner(argv, **kwargs):
        completed = real_runner.run(
            [sys.executable, '-I', '-c', sentinel_source],
            input=None,
            cwd=kwargs['cwd'],
            timeout=10,
            session_id=None,
        )
        assert completed.returncode == 0
        observed.update(json.loads(completed.stdout))
        return _completed(
            argv, _claude_wrapper(
                session_id='zero-cost-sentinel', result={'status': 'done'}))

    try:
        driver = ClaudeCodeDriver(executable='claude.exe', runner=runner)
        turn = driver.start(
            _profile('claude-code'), _request(isolated, prompt='只跑 sentinel'))
    finally:
        real_runner.close()

    assert turn.session_id == 'zero-cost-sentinel'
    assert Path(str(observed['cwd'])).resolve() == isolated.resolve()
    assert observed['entries'] == []
    assert set(caller.iterdir()) != set(isolated.iterdir())


class _OwnedRunner:
    """证明 cancel 只按本实例当前 session 工作，不依赖系统进程扫描。"""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.released = threading.Event()
        self.active_session: str | None = None
        self.closed = False

    def run(self, argv, *, input, cwd, timeout, session_id):
        del input, cwd, timeout
        self.active_session = session_id
        self.started.set()
        assert self.released.wait(5)
        self.active_session = None
        return _completed(argv, _codex_jsonl(session_id=session_id or 'new'))

    def cancel(self, session_id=None):
        if self.active_session is None:
            return False
        if session_id is not None and session_id != self.active_session:
            return False
        self.released.set()
        return True

    def close(self):
        self.closed = True
        self.cancel()


def test_cancel_is_scoped_to_this_driver_and_current_session(tmp_path):
    runner = _OwnedRunner()
    driver = CodexDriver(executable='codex.exe', runner=runner)
    results: list[object] = []

    thread = threading.Thread(
        target=lambda: results.append(driver.resume(
            _profile('codex'), 'owned-session',
            _request(tmp_path, prompt='继续'))))
    thread.start()
    assert runner.started.wait(2)
    assert driver.cancel('somebody-elses-session') is False
    assert runner.released.is_set() is False
    assert driver.cancel('owned-session') is True
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert results and results[0].session_id == 'owned-session'
    driver.close()
    assert runner.closed is True


def test_default_runner_cancels_the_owned_process_tree_but_not_a_neighbor(tmp_path):
    """真跨进程验证 root + grandchild；不用 fake 代替进程组边界。"""
    ready = tmp_path / 'owned.ready'
    survived = tmp_path / 'owned-grandchild-survived.txt'
    neighbor_done = tmp_path / 'neighbor-finished.txt'
    grandchild_source = (
        'import time\n'
        'from pathlib import Path\n'
        'time.sleep(1.5)\n'
        f'Path({str(survived)!r}).write_text("survived", encoding="utf-8")\n'
    )
    parent_source = (
        'import subprocess, sys, time\n'
        'from pathlib import Path\n'
        f'subprocess.Popen([sys.executable, "-c", {grandchild_source!r}])\n'
        f'Path({str(ready)!r}).write_text("ready", encoding="utf-8")\n'
        'time.sleep(30)\n'
    )
    neighbor_source = (
        'import time\n'
        'from pathlib import Path\n'
        'time.sleep(0.8)\n'
        f'Path({str(neighbor_done)!r}).write_text("done", encoding="utf-8")\n'
    )
    runner = agent_base._SubprocessRunner()
    results: list[object] = []

    def run_owned() -> None:
        try:
            results.append(runner.run(
                [sys.executable, '-c', parent_source], input=None, cwd=None,
                timeout=20, session_id='owned-session'))
        except BaseException as exc:  # noqa: BLE001 —— 线程异常要回传主用例
            results.append(exc)

    neighbor = subprocess.Popen([sys.executable, '-c', neighbor_source])
    thread = threading.Thread(target=run_owned)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not ready.is_file() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.is_file(), '受控根进程没有拉起孙进程'
        assert runner.cancel('somebody-elses-session') is False
        assert thread.is_alive(), '不匹配 session 的 cancel 误杀了当前树'
        assert runner.cancel('owned-session') is True
        thread.join(timeout=8)
        assert not thread.is_alive(), '取消后根/孙进程仍占着 stdout 管道'
        neighbor.wait(timeout=5)
        time.sleep(1.0)
        assert neighbor_done.read_text(encoding='utf-8') == 'done'
        assert not survived.exists(), '只杀了 CLI 根进程，孙进程仍在后台写盘'
        assert results and isinstance(results[0], subprocess.CompletedProcess)
    finally:
        runner.close()
        if thread.is_alive():
            thread.join(timeout=2)
        if neighbor.poll() is None:
            neighbor.kill()
            neighbor.wait(timeout=5)
