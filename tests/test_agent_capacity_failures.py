"""容量拒绝与错误权威的离线驱动矩阵；不运行 CLI、不访问账号或模型。"""
from __future__ import annotations

import io
import json
import subprocess

import pytest

from contracts.schema.orchestration import AgentProfile
from novel.agents import AgentCommandError, AgentRequest, ClaudeCodeDriver, CodexDriver
from novel.agents.base import classify_agent_command_failure
from novel.commanders import ClaudeCodeCommanderDriver, CodexCommanderDriver


CAPACITY = 'Selected model is at capacity. Please try a different model.'
WARNING = 'WARN codex_core::models_manager: models refresh timed out'


def _profile(vendor):
    return AgentProfile(
        driver=vendor,
        model='gpt-5.6-sol' if vendor == 'codex' else 'claude-opus-5',
        reasoning_effort='max', execution_mode='standard')


def _failed_driver(tmp_path, role, vendor, events, stderr=WARNING):
    """真实 driver 的命令/解析消费链；只替换外部进程回包。"""
    cwd = tmp_path / 'scratch'
    cwd.mkdir()
    profile = _profile(vendor)
    calls = []
    stdout = ('\n'.join(json.dumps(row) for row in events)
              if role == 'commander' or vendor == 'codex'
              else json.dumps(events[-1]))

    def runner(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, stdout, stderr)

    if role == 'commander':
        driver_class = CodexCommanderDriver if vendor == 'codex' else ClaudeCodeCommanderDriver
        driver = driver_class(executable='offline-probe.exe', runner=runner)
        invocation = driver.build_invocation(
            profile, '离线错误边界测试', repo=tmp_path, base=cwd,
            structured_response=True)
        result = driver.run(invocation, log=io.BytesIO(), run_id='offline-failure')
        assert result.exit_code == 1
        assert result.final_response is None
        observation = result.observation
        values = (result.failure_kind, result.status, result.retryable)
    else:
        driver_class = CodexDriver if vendor == 'codex' else ClaudeCodeDriver
        driver = driver_class(executable='offline-probe.exe', runner=runner)
        with pytest.raises(AgentCommandError) as caught:
            driver.start(profile, AgentRequest(
                prompt='离线错误边界测试', execution_policy='executor-isolated', cwd=cwd))
        assert caught.value.returncode == 1
        observation = caught.value.partial_turn
        assert observation is not None and observation.content == ''
        values = (caught.value.error_kind, caught.value.status, caught.value.retryable)
        assert CAPACITY not in str(caught.value) and WARNING not in str(caught.value)
    assert len(calls) == 1
    assert profile.model in calls[0]
    assert all('fallback-model' not in word for word in calls[0])
    return values, observation


def _codex_stream(error):
    return [
        {'type': 'thread.started', 'thread_id': 'failed-provider'},
        {'type': 'turn.started'},
        {'type': 'item.completed', 'item': {
            'type': 'agent_message', 'text': '不可采纳的半截回答'}},
        {'type': 'turn.failed', 'error': error},
    ]


@pytest.mark.parametrize('status', [None, 400, 401, 403, 429, 503])
def test_capacity_reuses_existing_kind_without_overriding_http_authority(status):
    expected = {
        None: ('provider_transient', None, True),
        400: ('agent_command', 400, False),
        401: ('authentication', 401, False),
        403: ('permission', 403, False),
        429: ('rate_limit', 429, True),
        503: ('server_error', 503, True),
    }
    assert classify_agent_command_failure(CAPACITY, status=status) == expected[status]


@pytest.mark.parametrize('detail', [
    'model capacity',
    'Unknown refusal: ' + CAPACITY,
    'content_policy_violation ' + CAPACITY,
    'at capacity; please switch model',
])
def test_capacity_words_or_refusal_context_do_not_create_retry_permission(detail):
    assert classify_agent_command_failure(detail) == ('agent_command', None, False)


@pytest.mark.parametrize('role', ['executor', 'commander'])
@pytest.mark.parametrize('stderr', ['', WARNING, 'background connection reset'])
def test_capacity_terminal_is_independent_of_background_diagnostics(tmp_path, role, stderr):
    events = _codex_stream({'message': CAPACITY})
    events.insert(-1, {'type': 'error', 'message': CAPACITY})
    result, observation = _failed_driver(tmp_path, role, 'codex', events, stderr)
    assert result == ('provider_transient', None, True)
    usage = observation.usage
    assert usage is None or all(value is None for value in vars(usage).values())


@pytest.mark.parametrize('role', ['executor', 'commander'])
@pytest.mark.parametrize(('error', 'expected'), [
    ({'message': 'Unknown explicit provider failure'}, ('agent_command', None, False)),
    ({}, ('agent_command', None, False)),
    ({'message': ''}, ('agent_command', None, False)),
    ({'message': 'Content policy refusal'}, ('agent_command', None, False)),
    ({'message': 'not logged in'}, ('authentication', None, False)),
    ({'message': 'usage limit'}, ('usage_limit', None, False)),
    ({'status': 403, 'message': CAPACITY}, ('permission', 403, False)),
    ({'status_code': 400, 'code': 'invalid_json_schema'}, ('agent_schema', 400, False)),
    ({'status': 400, 'code': 'unknown_error'}, ('agent_command', 400, False)),
])
def test_failed_turn_authority_blocks_warning_and_earlier_retry_diagnostics(
        tmp_path, role, error, expected):
    events = _codex_stream(error)
    events.insert(2, {'type': 'error', 'message': 'Reconnecting: timeout'})
    events.insert(3, {'type': 'error', 'message': CAPACITY})
    result, _ = _failed_driver(tmp_path, role, 'codex', events)
    assert result == expected


@pytest.mark.parametrize('role', ['executor', 'commander'])
@pytest.mark.parametrize('scenario', ['latest_error', 'latest_failed', 'recovered_error'])
def test_only_current_terminal_diagnostic_has_error_authority(tmp_path, role, scenario):
    events = _codex_stream({'message': CAPACITY})
    if scenario == 'latest_error':
        events = events[:-1] + [
            {'type': 'error', 'message': CAPACITY},
            {'type': 'error', 'message': 'Unknown current failure'}]
    elif scenario == 'latest_failed':
        events.append({'type': 'turn.failed', 'error': {'message': 'Unknown current failure'}})
    else:
        events = events[:-1] + [
            {'type': 'error', 'message': CAPACITY}, {'type': 'turn.completed'}]
    result, _ = _failed_driver(tmp_path, role, 'codex', events, 'Unknown CLI shutdown failure')
    assert result == ('agent_command', None, False)


@pytest.mark.parametrize('role', ['executor', 'commander'])
def test_stderr_is_only_fallback_without_structured_failure(tmp_path, role):
    events = _codex_stream({})[:-1]
    result, _ = _failed_driver(tmp_path, role, 'codex', events, 'connection reset by peer')
    assert result == ('connection', None, True)


@pytest.mark.parametrize('role', ['executor', 'commander'])
@pytest.mark.parametrize(('result_text', 'expected'), [
    ('Unknown explicit failure', ('agent_command', None, False)),
    ('', ('agent_command', None, False)),
    ('Content policy refusal', ('agent_command', None, False)),
    ('not logged in', ('authentication', None, False)),
    ('usage limit', ('usage_limit', None, False)),
    ('API Error: 403 model access forbidden', ('permission', 403, False)),
    ('private-prefix' * 400 + ' connection reset', ('connection', None, True)),
])
def test_claude_failed_result_uses_complete_error_not_stderr_warning(
        tmp_path, role, result_text, expected):
    result = {'type': 'result', 'subtype': 'error_during_execution', 'is_error': True,
              'session_id': 'failed-provider', 'result': result_text,
              'structured_output': {'text': CAPACITY}}
    actual, _ = _failed_driver(tmp_path, role, 'claude-code', [result])
    assert actual == expected


@pytest.mark.parametrize('role', ['executor', 'commander'])
def test_claude_successful_answer_has_no_failure_authority(tmp_path, role):
    result = {'type': 'result', 'subtype': 'success', 'is_error': False,
              'session_id': 'failed-provider', 'result': CAPACITY}
    actual, _ = _failed_driver(tmp_path, role, 'claude-code', [result], 'unknown shutdown error')
    assert actual == ('agent_command', None, False)


@pytest.mark.parametrize('role', ['executor', 'commander'])
@pytest.mark.parametrize('completed_first', [False, True])
def test_conflicting_success_and_failure_never_gain_retry_from_stderr(
        tmp_path, role, completed_first):
    events = _codex_stream({'message': CAPACITY})
    events.insert(-1 if completed_first else len(events), {'type': 'turn.completed'})
    actual, _ = _failed_driver(tmp_path, role, 'codex', events, WARNING)
    assert actual == ('agent_command', None, False)


@pytest.mark.parametrize('success_first', [False, True])
def test_claude_commander_conflicting_results_do_not_retry_from_warning(
        tmp_path, success_first):
    failed = {'type': 'result', 'subtype': 'error_during_execution', 'is_error': True,
              'session_id': 'failed-provider', 'result': 'Unknown explicit failure'}
    success = {'type': 'result', 'subtype': 'success', 'is_error': False,
               'session_id': 'failed-provider', 'result': '不可采纳的回答'}
    events = [success, failed] if success_first else [failed, success]
    actual, _ = _failed_driver(tmp_path, 'commander', 'claude-code', events, WARNING)
    assert actual == ('agent_command', None, False)
