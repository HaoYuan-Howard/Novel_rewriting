#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Commander driver 的零成本协议测试。

这里的 CLI stdout 全由 fake 或 Python sentinel 产生；绝不拉 Claude/Codex、绝不
读取账号、绝不发送模型请求。fake 能看见 invocation 组装，真实 sentinel 则专门
跨过 Popen/三条 pipe 那道缝，防止超长 stdin 与双输出互锁。
"""
from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from contracts.schema.orchestration import AgentProfile
from novel.agents import base as agent_base
from novel.commanders import (
    ClaudeCodeCommanderDriver,
    CodexCommanderDriver,
    CommanderConfigurationError,
    CommanderInvocation,
    CommanderProtocolError,
    create_commander_driver,
)
from novel.commanders import base as commander_base
from novel.commanders import claude_code as claude_commander
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


_OUTPUT_SCHEMA = {
    'type': 'object',
    'properties': {
        'schema': {'type': 'string', 'enum': ['review-v1']},
    },
    'required': ['schema'],
    'additionalProperties': False,
}


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


def _codex_structured_stream(
        response: str = '{"schema":"review-v1"}', *,
        transient_error: bool = False,
        terminal_error: bool = False,
        tool_item_type: str | None = None) -> str:
    events: list[dict[str, object]] = [
        {
            'type': 'thread.started', 'thread_id': 'codex-thread-structured',
            'model': 'gpt-5.6-sol', 'reasoning_effort': 'max',
        },
        {'type': 'turn.started'},
    ]
    if transient_error:
        events.append({
            'type': 'error',
            'message': 'Reconnecting... 2/5 (tls handshake eof)',
        })
    if tool_item_type is not None:
        events.append({
            'type': 'item.completed',
            'item': {'type': tool_item_type, 'command': 'Get-Content secret'},
        })
    events.extend((
        {
            'type': 'item.completed',
            'item': {'type': 'agent_message', 'text': response},
        },
        {
            'type': 'turn.completed',
            'usage': {
                'input_tokens': 43_944,
                'cached_input_tokens': 32_640,
                'output_tokens': 1_549,
                'reasoning_output_tokens': 849,
            },
        },
    ))
    if terminal_error:
        events.append({'type': 'error', 'message': 'terminal failure'})
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
    assert dict(invocation.env_overrides) == {
        'NOVEL_COMMANDER_PROFILE': 'frozen',
        'CLAUDE_CODE_DISABLE_BACKGROUND_TASKS': '1',
        'BASH_DEFAULT_TIMEOUT_MS': str(6 * 60 * 60 * 1000),
        'BASH_MAX_TIMEOUT_MS': str(23 * 60 * 60 * 1000),
    }


@pytest.mark.parametrize('name', [
    'CLAUDE_CODE_DISABLE_BACKGROUND_TASKS',
    'BASH_DEFAULT_TIMEOUT_MS',
    'BASH_MAX_TIMEOUT_MS',
])
def test_claude_lifecycle_environment_cannot_be_overridden(tmp_path, name):
    repo = tmp_path / 'repo'
    base = tmp_path / 'base'
    repo.mkdir()
    base.mkdir()

    with pytest.raises(CommanderConfigurationError, match='生命周期环境不可覆盖'):
        ClaudeCodeCommanderDriver(
            executable='claude.exe', runner=lambda *a, **k: None,
        ).build_invocation(
            _profile('claude-code'), '任务', repo=repo, base=base,
            env_overrides={name: 'caller-controlled'})


def test_codex_invocation_freezes_auto_review_and_executor_egress(tmp_path):
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
        '--approve-for-me',
        '--config', 'sandbox_workspace_write.network_access=true',
        '--ignore-user-config',
        '--ignore-rules',
        '--strict-config',
        '--cd', str(repo.resolve()),
        '--add-dir', str(base.resolve()),
        '-',
    )
    assert invocation.stdin_text == prompt
    assert prompt not in invocation.argv
    assert invocation.argv[-1] == '-'
    assert 'approval_policy="never"' not in invocation.argv
    assert '--sandbox' not in invocation.argv, (
        '--approve-for-me 已固定 workspace-write，Codex 0.148 拒绝再传 --sandbox')
    assert '--dangerously-bypass-approvals-and-sandbox' not in invocation.argv
    assert len(prompt) > 32_767
    assert invocation.cwd == str(repo.resolve())
    assert invocation.protocol == 'codex-jsonl-v1'
    assert invocation.cli_version == '0.148.test'


def test_codex_reviewer_invocation_is_read_only_and_has_no_egress(tmp_path):
    repo = tmp_path / 'repo'
    scratch = tmp_path / 'scratch'
    repo.mkdir()
    scratch.mkdir()
    driver = CodexCommanderDriver(
        executable='codex.exe', runner=lambda *a, **k: None)

    invocation = driver.build_invocation(
        _profile('codex'), 'review JSON', repo=repo, base=scratch,
        structured_response=True, cli_version='0.148.test')

    assert invocation.argv == (
        driver.executable, 'exec', '--json',
        '--model', 'gpt-5.6-sol',
        '--config', 'model_reasoning_effort="max"',
        '--config', 'approval_policy="never"',
        '--config', 'sandbox_mode="read-only"',
        '--ignore-user-config', '--ignore-rules', '--strict-config',
        '--skip-git-repo-check', '--cd', str(scratch.resolve()), '-')
    assert invocation.cwd == str(scratch.resolve())
    assert invocation.structured_response is True
    assert '--approve-for-me' not in invocation.argv
    assert '--add-dir' not in invocation.argv
    assert 'sandbox_workspace_write.network_access=true' not in invocation.argv
    assert '--dangerously-bypass-approvals-and-sandbox' not in invocation.argv


def test_claude_reviewer_invocation_disables_all_builtin_tools(tmp_path):
    repo = tmp_path / 'repo'
    scratch = tmp_path / 'scratch'
    repo.mkdir()
    scratch.mkdir()
    driver = ClaudeCodeCommanderDriver(
        executable='claude.exe', runner=lambda *a, **k: None)

    invocation = driver.build_invocation(
        _profile('claude-code'), 'review JSON', repo=repo, base=scratch,
        structured_response=True)

    assert invocation.cwd == str(scratch.resolve())
    assert invocation.structured_response is True
    assert ('--tools', '') == invocation.argv[
        invocation.argv.index('--tools'):invocation.argv.index('--tools') + 2]
    assert ('--permission-prompts', 'none') == invocation.argv[
        invocation.argv.index('--permission-prompts'):
        invocation.argv.index('--permission-prompts') + 2]
    assert '--add-dir' not in invocation.argv
    assert '--allowedTools' not in invocation.argv


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


def test_codex_reconnecting_error_before_completed_keeps_final_response(tmp_path):
    repo = tmp_path / 'repo'
    scratch = tmp_path / 'scratch'
    repo.mkdir()
    scratch.mkdir()
    response = '{"schema":"novel-reviewer-action-v1","action":{}}'
    driver = CodexCommanderDriver(
        executable='codex.exe',
        runner=lambda argv, **_kwargs: _completed(
            argv, _codex_structured_stream(
                response, transient_error=True)))
    invocation = driver.build_invocation(
        _profile('codex'), 'review', repo=repo, base=scratch,
        structured_response=True)

    result = driver.run(invocation, log=io.BytesIO(), run_id='reconnected')

    assert result.exit_code == 0
    assert result.final_response == response
    assert result.observation.provider_session_id == 'codex-thread-structured'
    assert result.observation.usage is not None
    assert result.observation.usage.prompt_tokens == 43_944
    assert result.observation.usage.completion_tokens == 1_549
    assert result.observation.usage.reasoning_tokens == 849
    assert result.wire_evidence is True


@pytest.mark.parametrize('item_type', [
    'command_execution', 'file_change', 'mcp_tool_call', 'web_search',
    'future_unknown_item',
])
def test_codex_structured_reviewer_rejects_every_tool_or_unknown_item(
        tmp_path, item_type):
    """Codex 无空工具 argv；协议亲见任何 tool item 时 typed 回答不得交付。"""
    repo = tmp_path / 'repo'
    scratch = tmp_path / 'scratch'
    repo.mkdir()
    scratch.mkdir()
    driver = CodexCommanderDriver(
        executable='codex.exe',
        runner=lambda argv, **_kwargs: _completed(
            argv, _codex_structured_stream(tool_item_type=item_type)))
    invocation = driver.build_invocation(
        _profile('codex'), 'review', repo=repo, base=scratch,
        structured_response=True)

    with pytest.raises(CommanderProtocolError, match=item_type) as caught:
        driver.run(invocation, log=io.BytesIO(), run_id='tool-refused')

    assert caught.value.partial_observation is not None
    assert caught.value.partial_observation.provider_session_id == (
        'codex-thread-structured')


def test_codex_terminal_error_after_completed_keeps_partial_observation(tmp_path):
    repo = tmp_path / 'repo'
    scratch = tmp_path / 'scratch'
    repo.mkdir()
    scratch.mkdir()
    driver = CodexCommanderDriver(
        executable='codex.exe',
        runner=lambda argv, **_kwargs: _completed(
            argv, _codex_structured_stream(terminal_error=True)))
    invocation = driver.build_invocation(
        _profile('codex'), 'review', repo=repo, base=scratch,
        structured_response=True)

    with pytest.raises(CommanderProtocolError, match='最后终态') as info:
        driver.run(invocation, log=io.BytesIO(), run_id='terminal-error')

    exc = info.value
    assert exc.partial_observation is not None
    assert exc.partial_observation.provider_session_id == 'codex-thread-structured'
    assert exc.partial_observation.usage is not None
    assert exc.partial_observation.usage.prompt_tokens == 43_944
    assert exc.wire_evidence is True


def test_structured_success_without_final_message_is_protocol_error(tmp_path):
    repo = tmp_path / 'repo'
    scratch = tmp_path / 'scratch'
    repo.mkdir()
    scratch.mkdir()
    driver = CodexCommanderDriver(
        executable='codex.exe',
        runner=lambda argv, **_kwargs: _completed(argv, _codex_stream()))
    invocation = driver.build_invocation(
        _profile('codex'), 'review', repo=repo, base=scratch,
        structured_response=True)

    with pytest.raises(CommanderProtocolError, match='没有最终结构化回答') as info:
        driver.run(invocation, log=io.BytesIO(), run_id='missing-response')
    assert info.value.partial_observation is not None
    assert info.value.partial_observation.provider_session_id == 'codex-thread-1'


def test_nonzero_structured_turn_never_delivers_partial_agent_message(tmp_path):
    repo = tmp_path / 'repo'
    scratch = tmp_path / 'scratch'
    repo.mkdir()
    scratch.mkdir()
    driver = CodexCommanderDriver(
        executable='codex.exe',
        runner=lambda argv, **_kwargs: _completed(
            argv, _codex_structured_stream('{"must":"not-deliver"}'),
            'connection reset by peer', returncode=1))
    invocation = driver.build_invocation(
        _profile('codex'), 'review', repo=repo, base=scratch,
        structured_response=True)

    result = driver.run(invocation, log=io.BytesIO(), run_id='failed-response')
    assert result.exit_code == 1
    assert result.final_response is None
    assert result.failure_kind == 'connection'

    with pytest.raises(CommanderProtocolError, match='非 0 commander'):
        commander_base.CommanderProcessResult(
            exit_code=1, observation=result.observation,
            final_response='{"invalid":"delivery"}')


def test_claude_structured_result_delivers_final_response(tmp_path):
    repo = tmp_path / 'repo'
    scratch = tmp_path / 'scratch'
    repo.mkdir()
    scratch.mkdir()
    events = [json.loads(line) for line in _claude_stream().splitlines()]
    events[-1]['result'] = '{"schema":"novel-reviewer-action-v1"}'
    stdout = ''.join(json.dumps(event) + '\n' for event in events)
    driver = ClaudeCodeCommanderDriver(
        executable='claude.exe',
        runner=lambda argv, **_kwargs: _completed(argv, stdout))
    invocation = driver.build_invocation(
        _profile('claude-code'), 'review', repo=repo, base=scratch,
        structured_response=True)

    result = driver.run(invocation, log=io.BytesIO(), run_id='claude-review')
    assert result.final_response == '{"schema":"novel-reviewer-action-v1"}'


def test_output_schema_is_frozen_as_canonical_bytes_and_hash(tmp_path):
    repo = tmp_path / 'repo'
    scratch = tmp_path / 'scratch'
    repo.mkdir()
    scratch.mkdir()
    mutable = json.loads(json.dumps(_OUTPUT_SCHEMA))
    invocation = ClaudeCodeCommanderDriver(
        executable='claude.exe', runner=lambda *_a, **_k: None,
    ).build_invocation(
        _profile('claude-code'), 'review', repo=repo, base=scratch,
        structured_response=True, output_schema=mutable)
    expected = json.dumps(
        _OUTPUT_SCHEMA, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')

    mutable['properties']['schema']['enum'].append('drift')

    assert invocation.output_schema_bytes == expected
    assert invocation.output_schema_sha256 == hashlib.sha256(expected).hexdigest()
    assert invocation.output_schema['properties']['schema']['enum'] == ['review-v1']


@pytest.mark.parametrize(
    ('driver_cls', 'driver_name', 'resume_id'),
    [
        (CodexCommanderDriver, 'codex', None),
        (CodexCommanderDriver, 'codex', 'codex-thread-structured'),
    ],
)
def test_codex_schema_file_covers_start_and_resume_runner_lifecycle(
        tmp_path, driver_cls, driver_name, resume_id):
    repo = tmp_path / 'repo'
    scratch = tmp_path / 'scratch'
    repo.mkdir()
    scratch.mkdir()
    seen: dict[str, object] = {}

    def runner(argv, **_kwargs):
        index = argv.index('--output-schema')
        path = Path(argv[index + 1])
        seen['path'] = path
        seen['payload'] = path.read_bytes()
        seen['exists_during_run'] = path.is_file()
        return _completed(argv, _codex_structured_stream())

    driver = driver_cls(executable='codex.exe', runner=runner)
    first = driver.build_invocation(
        _profile(driver_name), 'first', repo=repo, base=scratch,
        structured_response=True, output_schema=_OUTPUT_SCHEMA)
    invocation = driver.build_invocation(
        _profile(driver_name), 'review', repo=repo, base=scratch,
        structured_response=True, output_schema=_OUTPUT_SCHEMA,
        resume_provider_session_id=resume_id,
        expected_output_schema_sha256=(
            first.output_schema_sha256 if resume_id is not None else None))

    schema_index = invocation.argv.index('--output-schema')
    if resume_id is not None:
        assert schema_index < invocation.argv.index('resume')
        assert invocation.argv[invocation.argv.index('resume') + 1] == resume_id
    result = driver.run(invocation, log=io.BytesIO(), run_id='schema-wire')

    assert result.final_response == '{"schema":"review-v1"}'
    assert seen['exists_during_run'] is True
    assert seen['payload'] == invocation.output_schema_bytes
    assert isinstance(seen['path'], Path)
    assert seen['path'].parent == scratch.resolve()
    assert not seen['path'].exists()


def test_codex_schema_file_is_cleaned_when_runner_fails(tmp_path):
    repo = tmp_path / 'repo'
    scratch = tmp_path / 'scratch'
    repo.mkdir()
    scratch.mkdir()
    seen: dict[str, Path] = {}

    def runner(argv, **_kwargs):
        path = Path(argv[argv.index('--output-schema') + 1])
        seen['path'] = path
        assert path.is_file()
        raise OSError('sentinel launch failure')

    driver = CodexCommanderDriver(executable='codex.exe', runner=runner)
    invocation = driver.build_invocation(
        _profile('codex'), 'review', repo=repo, base=scratch,
        structured_response=True, output_schema=_OUTPUT_SCHEMA)

    with pytest.raises(commander_base.CommanderCommandError):
        driver.run(invocation, log=io.BytesIO(), run_id='schema-failure')
    assert not seen['path'].exists()


def test_codex_schema_file_recovers_only_matching_regular_orphan(tmp_path):
    repo = tmp_path / 'repo'
    scratch = tmp_path / 'scratch'
    repo.mkdir()
    scratch.mkdir()
    launched = False

    def runner(argv, **_kwargs):
        nonlocal launched
        launched = True
        path = Path(argv[argv.index('--output-schema') + 1])
        assert path.read_bytes() == invocation.output_schema_bytes
        return _completed(argv, _codex_structured_stream())

    driver = CodexCommanderDriver(executable='codex.exe', runner=runner)
    invocation = driver.build_invocation(
        _profile('codex'), 'review', repo=repo, base=scratch,
        structured_response=True, output_schema=_OUTPUT_SCHEMA)
    schema_path = Path(
        invocation.argv[invocation.argv.index('--output-schema') + 1])
    schema_path.write_bytes(invocation.output_schema_bytes)

    result = driver.run(invocation, log=io.BytesIO(), run_id='schema-orphan')

    assert launched is True
    assert result.final_response == '{"schema":"review-v1"}'
    assert not schema_path.exists()

    schema_path.write_text('{"different":true}', encoding='utf-8')
    launched = False
    with pytest.raises(CommanderConfigurationError, match='残留字节'):
        driver.run(invocation, log=io.BytesIO(), run_id='schema-conflict')
    assert launched is False


def test_codex_schema_cleanup_failure_does_not_erase_provider_result(
        tmp_path):
    repo = tmp_path / 'repo'
    scratch = tmp_path / 'scratch'
    repo.mkdir()
    scratch.mkdir()

    def runner(argv, **_kwargs):
        path = Path(argv[argv.index('--output-schema') + 1])
        assert path.is_file()
        path.unlink()
        path.mkdir()
        return _completed(argv, _codex_structured_stream())

    driver = CodexCommanderDriver(executable='codex.exe', runner=runner)
    invocation = driver.build_invocation(
        _profile('codex'), 'review', repo=repo, base=scratch,
        structured_response=True, output_schema=_OUTPUT_SCHEMA)

    with pytest.warns(RuntimeWarning, match='等待下次安全回收'):
        result = driver.run(
            invocation, log=io.BytesIO(), run_id='schema-cleanup-failure')

    assert result.final_response == '{"schema":"review-v1"}'


@pytest.mark.parametrize('resume_id', [None, 'claude-session-1'])
def test_claude_schema_argv_and_structured_output_cover_start_and_resume(
        tmp_path, resume_id):
    repo = tmp_path / 'repo'
    scratch = tmp_path / 'scratch'
    repo.mkdir()
    scratch.mkdir()
    events = [json.loads(line) for line in _claude_stream().splitlines()]
    events[-1]['structured_output'] = {'schema': 'review-v1'}
    events[-1]['result'] = '{"wrong":"fallback"}'
    stdout = ''.join(json.dumps(event) + '\n' for event in events)
    seen: dict[str, object] = {}

    def runner(argv, **_kwargs):
        seen['argv'] = argv
        return _completed(argv, stdout)

    driver = ClaudeCodeCommanderDriver(executable='claude.exe', runner=runner)
    first = driver.build_invocation(
        _profile('claude-code'), 'first', repo=repo, base=scratch,
        structured_response=True, output_schema=_OUTPUT_SCHEMA)
    invocation = driver.build_invocation(
        _profile('claude-code'), 'review', repo=repo, base=scratch,
        structured_response=True, output_schema=_OUTPUT_SCHEMA,
        resume_provider_session_id=resume_id,
        expected_output_schema_sha256=(
            first.output_schema_sha256 if resume_id is not None else None))

    schema_index = invocation.argv.index('--json-schema')
    assert invocation.argv[schema_index + 1] == (
        invocation.output_schema_bytes.decode('utf-8'))
    if resume_id is not None:
        assert invocation.argv[invocation.argv.index('--resume') + 1] == resume_id
    result = driver.run(invocation, log=io.BytesIO(), run_id='claude-schema')

    assert seen['argv'] == list(invocation.argv)
    assert result.final_response == '{"schema":"review-v1"}'


@pytest.mark.parametrize(
    ('driver_cls', 'driver_name'),
    [
        (ClaudeCodeCommanderDriver, 'claude-code'),
        (CodexCommanderDriver, 'codex'),
    ],
)
def test_invalid_or_drifting_schema_fails_before_runner(
        tmp_path, driver_cls, driver_name):
    repo = tmp_path / 'repo'
    scratch = tmp_path / 'scratch'
    repo.mkdir()
    scratch.mkdir()
    launched = False

    def runner(*_args, **_kwargs):
        nonlocal launched
        launched = True
        raise AssertionError('非法 schema 不应到达 runner')

    driver = driver_cls(
        executable=('claude.exe' if driver_name == 'claude-code' else 'codex.exe'),
        runner=runner)
    invalid = {
        'type': 'object', 'properties': {}, 'required': [],
        'additionalProperties': True,
    }
    with pytest.raises(CommanderConfigurationError, match='additionalProperties'):
        driver.build_invocation(
            _profile(driver_name), 'review', repo=repo, base=scratch,
            structured_response=True, output_schema=invalid)

    first = driver.build_invocation(
        _profile(driver_name), 'first', repo=repo, base=scratch,
        structured_response=True, output_schema=_OUTPUT_SCHEMA)
    drifted = json.loads(json.dumps(_OUTPUT_SCHEMA))
    drifted['properties']['schema']['enum'] = ['review-v2']
    with pytest.raises(CommanderConfigurationError, match='schema 已漂移'):
        driver.build_invocation(
            _profile(driver_name), 'resume', repo=repo, base=scratch,
            structured_response=True, output_schema=drifted,
            resume_provider_session_id='same-seat',
            expected_output_schema_sha256=first.output_schema_sha256)
    with pytest.raises(CommanderConfigurationError, match='必须提供上一轮'):
        driver.build_invocation(
            _profile(driver_name), 'resume', repo=repo, base=scratch,
            structured_response=True, output_schema=_OUTPUT_SCHEMA,
            resume_provider_session_id='same-seat')
    assert launched is False


def test_claude_oversized_inline_schema_is_zero_wire_on_windows(
        tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    scratch = tmp_path / 'scratch'
    repo.mkdir()
    scratch.mkdir()
    launched = False

    def runner(*_args, **_kwargs):
        nonlocal launched
        launched = True
        raise AssertionError('超长 argv 不应到达 runner')

    monkeypatch.setattr(
        claude_commander, 'ensure_command_line_fits',
        lambda argv: agent_base.ensure_command_line_fits(
            argv, platform_name='nt'))
    schema = json.loads(json.dumps(_OUTPUT_SCHEMA))
    schema['description'] = '甲' * 40_000
    driver = ClaudeCodeCommanderDriver(
        executable='claude.exe', runner=runner)

    with pytest.raises(CommanderConfigurationError, match='32767'):
        driver.build_invocation(
            _profile('claude-code'), 'review', repo=repo, base=scratch,
            structured_response=True, output_schema=schema)
    assert launched is False


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
    # 0-wire 本地失败：没有任何协议事件，分类为普通命令失败、不可热重试。
    assert result.failure_kind == 'agent_command'
    assert result.retryable is False
    assert result.wire_evidence is False


def test_nonzero_exit_after_wire_is_classified_for_same_seat_resume(tmp_path):
    """收到 thread.started 后被 connection reset 打断：可重试且有 wire 证据。"""
    repo = tmp_path / 'repo'
    base = tmp_path / 'base'
    repo.mkdir()
    base.mkdir()

    def runner(argv, **kwargs):
        del kwargs
        return _completed(
            argv, '{"type":"thread.started","thread_id":"codex-thread-9"}\n',
            'stream connection failed: connection reset by peer\n',
            returncode=1)

    driver = CodexCommanderDriver(executable='codex.exe', runner=runner)
    invocation = driver.build_invocation(
        _profile('codex'), '任务', repo=repo, base=base)
    result = driver.run(invocation, log=io.BytesIO(), run_id='reset-mid-turn')
    assert result.exit_code == 1
    assert result.observation.provider_session_id == 'codex-thread-9'
    assert result.failure_kind == 'connection'
    assert result.retryable is True
    assert result.wire_evidence is True
    with pytest.raises(CommanderProtocolError, match='exit 0 不能携带 failure_kind'):
        commander_base.CommanderProcessResult(
            exit_code=0, observation=result.observation, failure_kind='connection')


class _TimeoutRunner:
    """注入 runner：模拟公共 runner 的空闲超时，携带已收到的部分事件。"""

    def __init__(self, events):
        self._events = events

    def run(self, invocation, *, timeout, run_id, log):
        del invocation, timeout, run_id, log
        raise commander_base.CommanderTimeoutError(
            'commander CLI idle 超过 1 秒，已终止本轮进程树',
            timeout_kind='idle',
            partial_stdout='\n'.join(json.dumps(e) for e in self._events) + '\n',
            partial_stderr='', events=self._events)

    def cancel(self, run_id=None):
        del run_id
        return False

    def close(self):
        return None


@pytest.mark.parametrize(
    ('driver_cls', 'driver_name', 'events', 'session_id'),
    [
        (CodexCommanderDriver, 'codex',
         [{'type': 'thread.started', 'thread_id': 'codex-thread-t'}],
         'codex-thread-t'),
        (ClaudeCodeCommanderDriver, 'claude-code',
         [{'type': 'system', 'subtype': 'init', 'session_id': 'claude-session-t'}],
         'claude-session-t'),
        (CodexCommanderDriver, 'codex', [], None),
    ],
)
def test_timeout_surfaces_partial_observation_for_resume(
        tmp_path, driver_cls, driver_name, events, session_id):
    repo = tmp_path / 'repo'
    base = tmp_path / 'base'
    repo.mkdir()
    base.mkdir()
    driver = driver_cls(
        executable=('claude.exe' if driver_name == 'claude-code' else 'codex.exe'),
        runner=_TimeoutRunner(events))
    invocation = driver.build_invocation(
        _profile(driver_name), '任务', repo=repo, base=base)
    with pytest.raises(commander_base.CommanderTimeoutError) as info:
        driver.run(invocation, log=io.BytesIO(), run_id='idle-timeout')
    exc = info.value
    assert exc.timeout_kind == 'idle'
    assert exc.failure_kind == 'timeout' and exc.retryable is True
    assert exc.wire_evidence is bool(events)
    assert exc.partial_observation is not None
    assert exc.partial_observation.provider_session_id == session_id
    for event in events:
        assert driver.session_id_of_event(event) == session_id
    assert driver.session_id_of_event({'type': 'turn.started'}) is None


@pytest.mark.parametrize(
    ('driver_cls', 'driver_name', 'events', 'actual_session_id'),
    [
        (CodexCommanderDriver, 'codex',
         [{'type': 'thread.started', 'thread_id': 'forked-codex-thread'}],
         'forked-codex-thread'),
        (ClaudeCodeCommanderDriver, 'claude-code',
         [{'type': 'system', 'subtype': 'init',
           'session_id': 'forked-claude-session'}],
         'forked-claude-session'),
    ],
)
def test_timeout_resume_session_mismatch_remains_nonretryable_protocol_error(
        tmp_path, driver_cls, driver_name, events, actual_session_id):
    """进程超时不能洗掉 wire 已证明的 exact-resume 串会话。"""
    repo = tmp_path / 'repo'
    base = tmp_path / 'base'
    repo.mkdir()
    base.mkdir()
    driver = driver_cls(
        executable=('claude.exe' if driver_name == 'claude-code'
                    else 'codex.exe'),
        runner=_TimeoutRunner(events))
    invocation = driver.build_invocation(
        _profile(driver_name), '任务', repo=repo, base=base,
        resume_provider_session_id='expected-session')

    with pytest.raises(CommanderProtocolError, match='拒绝串错会话') as info:
        driver.run(invocation, log=io.BytesIO(), run_id='mismatched-timeout')

    exc = info.value
    assert exc.failure_kind == 'protocol'
    assert exc.retryable is False
    assert exc.wire_evidence is True
    assert exc.partial_observation is not None
    assert exc.partial_observation.provider_session_id == actual_session_id


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
