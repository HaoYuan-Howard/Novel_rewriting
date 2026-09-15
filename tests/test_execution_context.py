# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from contracts.schema.orchestration import (
    AgentProfile, AgentRunRecord, AgentRunStart, ExecutionEpoch,
    PipelineAgentProfiles, RunContractV2,
)
from novel.orchestrator import execution_context
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
            'stage7.rename': AgentProfile(
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
        'identity_version': 2,
        'profiles': _profiles(),
    }
    values.update(changes)
    return AgentRunStart(**values)


def _record(start: AgentRunStart, run_id: str = 'commander-1') -> AgentRunRecord:
    return AgentRunRecord(
        run_id=run_id, start=start, started=1,
        ended=None, exit_code=None, state='running',
        allowed_controls=['stop'])


def test_context_env_is_only_a_pointer_and_round_trips_the_started_snapshot(
        tmp_path, monkeypatch):
    start = _start(tmp_path)
    env = execution_context.env_with_context(
        run_id='commander-1', base=tmp_path, start=start, env={'KEEP': 'yes'})
    monkeypatch.setattr(execution_context.prov, 'code_version', lambda: 'code-v1')

    loaded = execution_context.load_from_environment(
        tmp_path, '42', env=env, records=[_record(start)])

    assert loaded is not None
    assert loaded.producer_run_id == loaded.run_id == 'commander-1'
    assert env['KEEP'] == 'yes'
    assert loaded.start == start and loaded.profiles == _profiles()
    assert loaded.executor_profile('stage2.global').driver == 'codex'
    assert loaded.executor_profile('stage7.rename').driver == 'claude-code'
    serialized_env = '\n'.join(f'{key}={value}' for key, value in env.items())
    assert 'claude-opus-5' not in serialized_env
    assert 'gpt-5.6-sol' not in serialized_env


def test_v2_parent_compatibility_snapshots_do_not_override_epoch_identity(
        tmp_path, monkeypatch):
    """parent hash 固定 started 字节；执行 currentness 只认当前 epoch。"""
    initial = _start(
        tmp_path, argv=['old-commander', '-'], prompt='首次启动提示',
        code_version='code-v1', identity_version=1, skill_sha256='0' * 64)
    contract = RunContractV2(**initial.model_dump(mode='json'))
    assert contract.model_dump(mode='json') == {
        'wire_version': 2,
        'sid': initial.sid,
        'base': initial.base,
        'authorized': True,
        'egress_authorized': True,
        'profiles': initial.profiles.model_dump(mode='json'),
    }
    epoch = ExecutionEpoch(
        epoch_id='epoch-v2', ordinal=2, reason='code_change',
        argv=['new-commander', '-'], prompt_sha256='1' * 64,
        protocol_version='commander-v2', cli_version='cli-v2',
        code_version='code-v2', identity_version=2,
        session_run_id='session-v2')
    record = AgentRunRecord(
        run_id='commander-v2', start=initial, contract=contract,
        epochs=[epoch], active_epoch_id=epoch.epoch_id,
        started=1, ended=None, exit_code=None, state='running',
        allowed_controls=['pause', 'stop'])
    env = execution_context.env_with_context(
        run_id=record.run_id, base=tmp_path, start=initial,
        contract=contract, epoch=epoch, admission_id=epoch.epoch_id, env={})
    parent_sha = env[execution_context.COMMANDER_CONTRACT_SHA256_ENV]
    monkeypatch.setattr(execution_context.prov, 'code_version', lambda: 'code-v2')

    loaded = execution_context.load_from_environment(
        tmp_path, '42', env=env, records=[record])

    assert loaded is not None and loaded.epoch == epoch
    assert loaded.producer_run_id == epoch.session_run_id
    assert loaded.start.code_version == 'code-v1'
    assert loaded.start.skill_sha256 == '0' * 64
    assert loaded.contract_sha256 == parent_sha
    assert execution_context.contract_v2_sha256(
        run_id=record.run_id, base=tmp_path, contract=contract) == parent_sha


def test_retired_stage2_prereview_context_cannot_open_or_load_admission(
        tmp_path, monkeypatch):
    """旧预审哨兵与近似前缀都不再拥有上下文入口。"""
    start = _start(tmp_path)
    contract = RunContractV2(**start.model_dump(mode='json'))
    epoch = ExecutionEpoch(
        epoch_id='epoch-prereview', ordinal=1, reason='initial',
        argv=['commander', '-'], prompt_sha256='1' * 64,
        protocol_version='commander-v2', cli_version='cli-v2',
        code_version='code-v1', identity_version=2,
        session_run_id='session-prereview')
    record = AgentRunRecord(
        run_id='commander-prereview', start=start, contract=contract,
        epochs=[epoch], active_epoch_id=epoch.epoch_id,
        started=1, ended=None, exit_code=None, state='running',
        allowed_controls=['pause', 'stop'])
    prereview_id = epoch.epoch_id + '-stage2-prereview'
    env = execution_context.env_with_context(
        run_id=record.run_id, base=tmp_path, start=start,
        contract=contract, epoch=epoch, admission_id=epoch.epoch_id, env={})
    monkeypatch.setattr(execution_context.prov, 'code_version', lambda: 'code-v1')

    for retired_id in (prereview_id, prereview_id + '-extra'):
        with pytest.raises(ValueError, match='属于当前 epoch'):
            execution_context.env_with_context(
                run_id=record.run_id, base=tmp_path, start=start,
                contract=contract, epoch=epoch, admission_id=retired_id, env={})
        tampered = dict(env)
        tampered[execution_context.COMMANDER_ADMISSION_ID_ENV] = retired_id
        with pytest.raises(TerminalState, match='不属于当前 epoch'):
            execution_context.load_from_environment(
                tmp_path, '42', env=tampered, records=[record])


@pytest.mark.parametrize('drift,match', [
    ('code', '代码指纹'),
    ('skill', '旧 skill 运行身份'),
])
def test_execution_close_loader_keeps_old_epoch_pointer_but_cannot_authorize_work(
        tmp_path, monkeypatch, drift, match):
    """漂移只能放行旧 execution 收尾；严格执行 loader 仍关闭式拒绝。"""
    initial = _start(tmp_path)
    contract = RunContractV2(**initial.model_dump(mode='json'))
    epoch = ExecutionEpoch(
        epoch_id='epoch-close', ordinal=1, reason='initial',
        argv=['commander', '-'], prompt_sha256='1' * 64,
        protocol_version='commander-v2', cli_version='cli-v2',
        code_version='code-v1',
        identity_version=(1 if drift == 'skill' else 2),
        skill_sha256=('0' * 64 if drift == 'skill' else None),
        session_run_id='session-close')
    record = AgentRunRecord(
        run_id='commander-close', start=initial, contract=contract,
        epochs=[epoch], active_epoch_id=epoch.epoch_id,
        started=1, ended=None, exit_code=None, state='running',
        allowed_controls=['pause', 'stop'])
    env = execution_context.env_with_context(
        run_id=record.run_id, base=tmp_path, start=initial,
        contract=contract, epoch=epoch, admission_id=epoch.epoch_id, env={})
    monkeypatch.setattr(
        execution_context.prov, 'code_version',
        lambda: 'code-v2' if drift == 'code' else 'code-v1')

    with pytest.raises(TerminalState, match=match):
        execution_context.load_from_environment(
            tmp_path, '42', env=env, records=[record])

    closing = execution_context.load_for_execution_close_from_environment(
        tmp_path, '42', env=env, records=[record])
    assert isinstance(
        closing, execution_context.ExecutionCloseContext)
    assert closing.run_id == record.run_id
    assert closing.epoch == epoch
    assert closing.execution_id == epoch.epoch_id
    assert not hasattr(closing, 'start') and not hasattr(closing, 'profiles')


@pytest.mark.parametrize('mutation,match', [
    ('half', '同时携带'),
    ('sha', '不一致'),
    ('sid', '不能执行'),
    ('base', '不能执行'),
    ('code', '代码指纹'),
    ('skill', '旧 skill 运行身份'),
    ('missing_skill', '旧 skill 运行身份'),
])
def test_context_refuses_partial_or_drifted_identity(
        tmp_path, monkeypatch, mutation, match):
    start = _start(tmp_path)
    env = execution_context.env_with_context(
        run_id='commander-1', base=tmp_path, start=start, env={})
    base = tmp_path
    sid = '42'
    if mutation == 'half':
        env.pop(execution_context.COMMANDER_CONTRACT_SHA256_ENV)
    elif mutation == 'sha':
        env[execution_context.COMMANDER_CONTRACT_SHA256_ENV] = '0' * 64
    elif mutation == 'sid':
        sid = '43'
    elif mutation == 'base':
        base = tmp_path / 'other'
    elif mutation == 'skill':
        start = _start(tmp_path, identity_version=1, skill_sha256='0' * 64)
        env = execution_context.env_with_context(
            run_id='commander-1', base=tmp_path, start=start, env={})
    elif mutation == 'missing_skill':
        start = _start(tmp_path, identity_version=1, skill_sha256=None)
        env = execution_context.env_with_context(
            run_id='commander-1', base=tmp_path, start=start, env={})
    monkeypatch.setattr(
        execution_context.prov, 'code_version',
        lambda: 'code-v2' if mutation == 'code' else 'code-v1')

    with pytest.raises(TerminalState, match=match):
        execution_context.load_from_environment(
            base, sid, env=env, records=[_record(start)])


@pytest.mark.parametrize(
    'authorized,egress,missing',
    [(False, True, '付费授权'), (True, False, '正文外发授权'),
     (False, False, '付费授权、正文外发授权')],
)
def test_execution_requires_both_independent_authorizations(
        tmp_path, monkeypatch, authorized, egress, missing):
    start = _start(tmp_path, authorized=authorized, egress_authorized=egress)
    env = execution_context.env_with_context(
        run_id='commander-1', base=tmp_path, start=start, env={})
    monkeypatch.setattr(execution_context.prov, 'code_version', lambda: 'code-v1')
    loaded = execution_context.load_from_environment(
        tmp_path, '42', env=env, records=[_record(start)])
    assert loaded is not None

    with pytest.raises(TerminalState, match=missing):
        execution_context.require_execution_authorized(loaded)
