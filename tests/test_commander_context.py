# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from contracts.schema.orchestration import (
    AgentProfile, AgentRunRecord, AgentRunStart, PipelineAgentProfiles,
)
from novel import commander_context, skill_contract
from novel.errors import TerminalState


def _profiles() -> PipelineAgentProfiles:
    return PipelineAgentProfiles(
        commander=AgentProfile(
            driver='claude-code', model='claude-opus-5',
            reasoning_effort='high', execution_mode='standard'),
        executor_default=AgentProfile(
            driver='codex', model='gpt-5.6-sol',
            reasoning_effort='high', execution_mode='standard'),
        executor_overrides={
            'stage6.semantic': AgentProfile(
                driver='claude-code', model='claude-sonnet-5',
                reasoning_effort='xhigh', execution_mode='standard'),
        },
    )


def _start(base: Path, **changes) -> AgentRunStart:
    values = {
        'sid': '42',
        'base': str(base.resolve()),
        'argv': ['codex', 'exec', '-'],
        'prompt': '按 operator skill 完成这本书',
        'authorized': True,
        'egress_authorized': True,
        'code_version': 'code-v1',
        'skill_sha256': skill_contract.package_sha256(),
        'profiles': _profiles(),
    }
    values.update(changes)
    return AgentRunStart(**values)


def _record(start: AgentRunStart, run_id: str = 'commander-1') -> AgentRunRecord:
    return AgentRunRecord(
        run_id=run_id, start=start, started=1,
        ended=None, exit_code=None, state='running')


def test_context_env_is_only_a_pointer_and_round_trips_the_started_snapshot(
        tmp_path, monkeypatch):
    start = _start(tmp_path)
    env = commander_context.env_with_context(
        run_id='commander-1', base=tmp_path, start=start, env={'KEEP': 'yes'})
    monkeypatch.setattr(commander_context.prov, 'code_version', lambda: 'code-v1')

    loaded = commander_context.load_from_environment(
        tmp_path, '42', env=env, records=[_record(start)])

    assert loaded is not None
    assert env['KEEP'] == 'yes'
    assert loaded.start == start and loaded.profiles == _profiles()
    assert loaded.executor_profile('stage2.global').driver == 'codex'
    assert loaded.executor_profile('stage6.semantic').driver == 'claude-code'
    serialized_env = '\n'.join(f'{key}={value}' for key, value in env.items())
    assert 'claude-opus-5' not in serialized_env
    assert 'gpt-5.6-sol' not in serialized_env


@pytest.mark.parametrize('mutation,match', [
    ('half', '同时携带'),
    ('sha', '不一致'),
    ('sid', '不能执行'),
    ('base', '不能执行'),
    ('code', '代码指纹'),
    ('skill', 'operator skill'),
    ('missing_skill', '没有 canonical operator skill'),
])
def test_context_refuses_partial_or_drifted_identity(
        tmp_path, monkeypatch, mutation, match):
    start = _start(tmp_path)
    env = commander_context.env_with_context(
        run_id='commander-1', base=tmp_path, start=start, env={})
    base = tmp_path
    sid = '42'
    if mutation == 'half':
        env.pop(commander_context.COMMANDER_CONTRACT_SHA256_ENV)
    elif mutation == 'sha':
        env[commander_context.COMMANDER_CONTRACT_SHA256_ENV] = '0' * 64
    elif mutation == 'sid':
        sid = '43'
    elif mutation == 'base':
        base = tmp_path / 'other'
    elif mutation == 'skill':
        start = _start(tmp_path, skill_sha256='0' * 64)
        env = commander_context.env_with_context(
            run_id='commander-1', base=tmp_path, start=start, env={})
    elif mutation == 'missing_skill':
        start = _start(tmp_path, skill_sha256=None)
        env = commander_context.env_with_context(
            run_id='commander-1', base=tmp_path, start=start, env={})
    monkeypatch.setattr(
        commander_context.prov, 'code_version',
        lambda: 'code-v2' if mutation == 'code' else 'code-v1')

    with pytest.raises(TerminalState, match=match):
        commander_context.load_from_environment(
            base, sid, env=env, records=[_record(start)])


@pytest.mark.parametrize(
    'authorized,egress,missing',
    [(False, True, '付费授权'), (True, False, '正文外发授权'),
     (False, False, '付费授权、正文外发授权')],
)
def test_execution_requires_both_independent_authorizations(
        tmp_path, monkeypatch, authorized, egress, missing):
    start = _start(tmp_path, authorized=authorized, egress_authorized=egress)
    env = commander_context.env_with_context(
        run_id='commander-1', base=tmp_path, start=start, env={})
    monkeypatch.setattr(commander_context.prov, 'code_version', lambda: 'code-v1')
    loaded = commander_context.load_from_environment(
        tmp_path, '42', env=env, records=[_record(start)])
    assert loaded is not None

    with pytest.raises(TerminalState, match=missing):
        commander_context.require_execution_authorized(loaded)
