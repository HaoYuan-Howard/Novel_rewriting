"""正式运行入口的授权、信标、材料隔离与真实子进程反例。"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.invariants import INBOX_KIND
from contracts.rules import REGISTRY
from contracts.schema.api import (
    ExecutionPermit, InboxView, OperatorAction, OperatorNext,
)
from contracts.schema.orchestration import (
    LegacyExecutorBackend, LegacyLlmProfile, NovelOverview, OverviewSummary,
    PipelineAgentProfiles,
)
from novel.orchestrator.operator import canonical_sha256
from novel.orchestrator.nodes import is_paid, write_effect

REPO_ROOT = Path(__file__).resolve().parents[1]

def _step_stub(**values):
    # runner副作用测试的Step替身仍遵循现役配置字段形状。
    backend = values.get('executor_backend')
    values.setdefault('request_profile', backend.profile if backend is not None else None)
    return SimpleNamespace(**values)


def _operator_script(name: str):
    import importlib
    modules = {
        'run_step': 'novel.orchestrator.run_step',
        'session_preflight': 'novel.orchestrator.preflight',
        'verify_delivery': 'novel.orchestrator.delivery',
    }
    return importlib.import_module(modules[name])


def test_run_step_closes_execution_with_crash_when_node_spawn_raises(
        monkeypatch, tmp_path):
    runner = _operator_script('run_step')
    step = _step_stub(
        label='stage2 全局事实',
        argv=['python', '-m', 'novel.cli', 'run', 'str06.stage2.book', '42'],
        cwd=str(REPO_ROOT), paid=True, writes='stage2/observations/book.json',
        note=None)
    calls: list[list[str]] = []
    monkeypatch.setattr(runner, 'build_steps', lambda *_args, **_kwargs: [step])
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')
    permit = _execution_permit()
    permit_file = tmp_path / 'execution-permit.json'
    permit_file.write_text(permit.model_dump_json(indent=2), encoding='utf-8')
    monkeypatch.setattr(
        runner.permit_layer, 'revalidate_active_execution_surface',
        lambda *_args, **_kwargs: None)

    def fake_run(argv, **_kwargs):
        calls.append(list(argv))
        if 'mark' in argv:
            return SimpleNamespace(returncode=0)
        raise OSError('synthetic spawn failure')

    monkeypatch.setattr(runner.subprocess, 'run', fake_run)

    assert runner.main([
        '--base', 'E:/outputs', '--sid', '42',
        '--expect-code-version', 'code-v1', '--execute',
        '--operator-permit-file', str(permit_file), 'str06.stage2.book',
    ]) == runner.X.CRASH

    start, _node, end = calls
    assert start[start.index('--execution-id') + 1] == (
        end[end.index('--execution-id') + 1])
    assert end[end.index('--phase') + 1] == 'end'
    assert end[end.index('--exit') + 1] == str(runner.X.CRASH)



def _executor_profiles() -> PipelineAgentProfiles:
    """schema v3 executor-only；C2a 起执行面 legacy-only。"""
    return PipelineAgentProfiles(
        executor_default=LegacyExecutorBackend(profile=LegacyLlmProfile(
            provider='deepseek', model='deepseek-v4-pro',
            reasoning_effort='high', thinking_mode='on')),
        executor_overrides={},
    )



def _execution_permit(
        *, base: str = 'E:/outputs', sid: str = '42',
        node: str = 'str06.stage2.book', flags: list[str] | None = None,
        code_version: str = 'code-v1', surface_seal: str = 'a' * 64,
        operator_action_sha256: str = 'b' * 64) -> ExecutionPermit:
    """生成可过 canonical envelope 校验的最小 ready permit。"""
    payload = {
        'base': base,
        'sid': sid,
        'code_version': code_version,
        'surface_seal': surface_seal,
        'next_state': 'ready',
        'waiting_on': None,
        'kind': 'ready',
        'node': node,
        'flags': list(flags or []),
        'paid': is_paid(node, flags or []),
        'writes': write_effect(node, flags or []),
        'operator_action_sha256': operator_action_sha256,
        'external_inputs': [],
    }
    return ExecutionPermit(
        **payload, permit_sha256=canonical_sha256(payload))



def _write_execution_token(path: Path, permit: ExecutionPermit) -> Path:
    path.write_text(permit.model_dump_json(), encoding='utf-8')
    return path



def _write_ready_next(path: Path, permit: ExecutionPermit) -> Path:
    """模拟 CLI 的原样 ready next JSON；调用方不拆 execution token。"""
    assert permit.operator_action_sha256 is not None
    overview = NovelOverview(
        sid=permit.sid, title='测试书', dirname=f'平台_测试书_{permit.sid}',
        stages=[], summary=OverviewSummary(
            verdict='pending', text='待继续', detail='下一步待判',
            artifact_count=0, latest_mtime=None),
    )
    inbox = InboxView(
        items=[], counts={kind: 0 for kind in INBOX_KIND}, total=0, wontfix=0)
    action = OperatorAction(
        node=permit.node, flags=permit.flags, resume_verdict='run', why='测试',
        waiting_on=None, paid=permit.paid, writes=permit.writes,
        action_sha256=permit.operator_action_sha256,
    )
    wrapper = OperatorNext(
        base=permit.base, sid=permit.sid, code_version=permit.code_version,
        surface_seal=permit.surface_seal, state='ready', overview=overview,
        inbox=inbox, action=action, execution_token=permit,
    )
    path.write_text(wrapper.model_dump_json(), encoding='utf-8')
    return path



def test_run_step_permit_file_preview_never_remints_or_revalidates(
        monkeypatch, tmp_path) -> None:
    """完整 token 已冻结 preview；渲染步骤不能再跑六层 planner/CAS。"""
    runner = _operator_script('run_step')
    permit = _execution_permit(base=str(tmp_path.resolve()))
    token = _write_ready_next(tmp_path / 'operator-next.json', permit)
    step = _step_stub(
        label='stage2 全局事实',
        argv=['python', '-m', 'novel.cli', 'run', 'str06.stage2.book', '42'],
        cwd=str(REPO_ROOT), paid=True,
        writes=write_effect('str06.stage2.book', []), note=None,
    )
    built: list[tuple] = []
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')
    monkeypatch.setattr(
        runner.operator_planner, 'permit',
        lambda *_args, **_kwargs: pytest.fail('完整 token preview 不得重新铸 permit'))
    monkeypatch.setattr(
        runner.permit_layer, 'revalidate_active_execution_surface',
        lambda *_args, **_kwargs: pytest.fail('preview 不做 execute-side CAS'))
    monkeypatch.setattr(
        runner, 'build_steps',
        lambda *args, **kwargs: built.append((args, kwargs)) or [step])

    rc = runner.main([
        '--base', str(tmp_path), '--sid', '42',
        '--expect-code-version', 'code-v1',
        '--operator-permit-file', str(token), 'str06.stage2.book',
    ])

    assert rc == 0
    assert built == [(('str06.stage2.book', '42', []), {
        'base': str(tmp_path.resolve()),
        'executor_backend': None,
        'request_profile': None,
        'egress_authorized': None,
        'placeholders': True,
    })]



def test_run_step_permit_file_refuses_tampering_before_building(
        monkeypatch, tmp_path, capsys) -> None:
    runner = _operator_script('run_step')
    permit = _execution_permit(base=str(tmp_path.resolve()))
    tampered = permit.model_dump(mode='json')
    tampered['surface_seal'] = 'f' * 64
    token = tmp_path / 'tampered-token.json'
    token.write_text(json.dumps(tampered), encoding='utf-8')
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')
    monkeypatch.setattr(
        runner, 'build_steps',
        lambda *_args, **_kwargs: pytest.fail('坏 token 不得构造生产步骤'))
    monkeypatch.setattr(
        runner.operator_planner, 'permit',
        lambda *_args, **_kwargs: pytest.fail('坏完整 token 也不得退回 remint'))

    rc = runner.main([
        '--base', str(tmp_path), '--sid', '42',
        '--expect-code-version', 'code-v1',
        '--operator-permit-file', str(token), 'str06.stage2.book',
    ])

    assert rc == 2
    assert 'canonical SHA-256' in capsys.readouterr().err



def test_run_step_permit_file_refuses_exact_coordinate_drift_before_building(
        monkeypatch, tmp_path, capsys) -> None:
    runner = _operator_script('run_step')
    permit = _execution_permit(base=str(tmp_path.resolve()))
    token = _write_execution_token(tmp_path / 'execution-token.json', permit)
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')
    monkeypatch.setattr(
        runner, 'build_steps',
        lambda *_args, **_kwargs: pytest.fail('坐标不等不得构造生产步骤'))
    monkeypatch.setattr(
        runner.operator_planner, 'permit',
        lambda *_args, **_kwargs: pytest.fail('坐标错不得退回 remint'))

    rc = runner.main([
        '--base', str(tmp_path), '--sid', 'other-book',
        '--expect-code-version', 'code-v1',
        '--operator-permit-file', str(token), 'str06.stage2.book',
    ])

    assert rc == 2
    assert 'exact sid/node/flags' in capsys.readouterr().err



def test_run_step_projects_frozen_executor_and_egress_into_production_steps(
        monkeypatch, tmp_path):
    runner = _operator_script('run_step')
    profiles = _executor_profiles()
    start = SimpleNamespace(
        authorized=True, egress_authorized=True, code_version='code-v1')
    context = SimpleNamespace(
        run_id='outer-1', base=str(tmp_path.resolve()),
        contract_sha256='a' * 64, start=start, profiles=profiles,
        executor_backend=lambda node: profiles.executor_overrides.get(
            node, profiles.executor_default),
    )
    built: list[tuple] = []
    profile = profiles.executor_default
    step = _step_stub(
        label='stage2 全局事实',
        argv=['python', '-m', 'novel.cli', 'run', 'str06.stage2.book', '42'],
        cwd=str(REPO_ROOT), paid=True,
        writes='stage2/observations/book.json', note=None,
        executor_backend=profile, egress_authorized=True)
    monkeypatch.setenv(runner.execution_context.COMMANDER_RUN_ID_ENV, 'outer-1')
    monkeypatch.setenv(
        runner.execution_context.COMMANDER_CONTRACT_SHA256_ENV, 'a' * 64)
    monkeypatch.setattr(
        runner.execution_context, 'load_from_environment',
        lambda *_args, **_kwargs: context)
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')
    permit = _execution_permit(base=str(tmp_path.resolve()))
    monkeypatch.setattr(
        runner.operator_planner, 'permit', lambda *_args, **_kwargs: permit)
    monkeypatch.setattr(
        runner, 'build_steps',
        lambda *args, **kwargs: built.append((args, kwargs)) or [step])

    evidence = tmp_path / 'evidence.json'
    rc = runner.main([
        '--base', str(tmp_path), '--sid', '42',
        '--expect-code-version', 'code-v1', '--evidence-json', str(evidence),
        '--operator-permit-sha256', permit.permit_sha256,
        'str06.stage2.book',
    ])

    assert rc == 0
    assert built[0][1]['executor_backend'] == profile
    assert built[0][1]['request_profile'] == profile.profile
    assert built[0][1]['egress_authorized'] is True
    payload = json.loads(evidence.read_bytes())
    assert payload['schema'] == 'novel-operator-step-evidence-v5'
    assert payload['commander']['run_id'] == 'outer-1'
    assert payload['execution_permit'] == permit.model_dump(mode='json')
    assert payload['steps'][0]['executor_backend'] == profile.model_dump(mode='json')
    assert payload['steps'][0]['request_profile'] == profile.profile.model_dump(mode='json')
    assert payload['steps'][0]['egress_authorized'] is True



def test_run_step_with_frozen_profile_refuses_execute_without_both_authorizations(
        monkeypatch, tmp_path):
    runner = _operator_script('run_step')
    profiles = _executor_profiles()
    context = SimpleNamespace(
        run_id='outer-1', base=str(tmp_path.resolve()),
        contract_sha256='b' * 64,
        start=SimpleNamespace(
            authorized=True, egress_authorized=False, code_version='code-v1'),
        profiles=profiles,
        executor_backend=lambda _node: profiles.executor_default,
    )
    step = _step_stub(
        label='stage2 全局事实', argv=['python'], cwd=str(REPO_ROOT),
        paid=True, writes='stage2/observations/book.json', note=None,
        executor_backend=profiles.executor_default, egress_authorized=False)
    monkeypatch.setenv(runner.execution_context.COMMANDER_RUN_ID_ENV, 'outer-1')
    monkeypatch.setenv(
        runner.execution_context.COMMANDER_CONTRACT_SHA256_ENV, 'b' * 64)
    monkeypatch.setattr(
        runner.execution_context, 'load_from_environment',
        lambda *_args, **_kwargs: context)
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')
    permit = _execution_permit(base=str(tmp_path.resolve()))
    monkeypatch.setattr(
        runner.operator_planner, 'permit', lambda *_args, **_kwargs: permit)
    monkeypatch.setattr(runner, 'build_steps', lambda *_args, **_kwargs: [step])
    monkeypatch.setattr(
        runner.subprocess, 'run',
        lambda *_args, **_kwargs: pytest.fail('授权不足不得打信标或启动节点'))

    rc = runner.main([
        '--base', str(tmp_path), '--sid', '42',
        '--expect-code-version', 'code-v1', '--execute',
        '--operator-permit-sha256', permit.permit_sha256, 'str06.stage2.book',
    ])

    assert rc == 2



def test_headless_execute_claims_before_evidence_and_releases_after_steps(
        monkeypatch, tmp_path):
    runner = _operator_script('run_step')
    profiles = _executor_profiles()
    context = SimpleNamespace(
        run_id='outer-admission', base=str(tmp_path.resolve()),
        contract_sha256='a' * 64,
        start=SimpleNamespace(
            authorized=True, egress_authorized=True, code_version='code-v1'),
        profiles=profiles,
        executor_backend=lambda _node: profiles.executor_default,
    )
    permit = _execution_permit(base=str(tmp_path.resolve()))
    token = _write_execution_token(tmp_path / 'execution-token.json', permit)
    step = _step_stub(
        label='stage2 全局事实',
        argv=['python', '-m', 'novel.cli', 'run', 'str06.stage2.book', '42'],
        cwd=str(REPO_ROOT), paid=True,
        writes='stage2/observations/book.json', note=None,
        executor_backend=profiles.executor_default, egress_authorized=True)
    runner.admission.open_gate(str(tmp_path), context.run_id, '42')
    monkeypatch.setenv(runner.execution_context.COMMANDER_RUN_ID_ENV, context.run_id)
    monkeypatch.setenv(
        runner.execution_context.COMMANDER_CONTRACT_SHA256_ENV, 'a' * 64)
    monkeypatch.setattr(
        runner.execution_context, 'load_from_environment',
        lambda *_args, **_kwargs: context)
    monkeypatch.setattr(
        runner.execution_context, 'env_with_context',
        lambda **_kwargs: {'PARENT': 'commander'})
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')
    monkeypatch.setattr(
        runner.operator_planner, 'permit',
        lambda *_args, **_kwargs: pytest.fail(
            '完整 token execute 不得重新运行 operator planner'))
    monkeypatch.setattr(runner, 'build_steps', lambda *_args, **_kwargs: [step])
    original_write = runner._write_evidence
    order: list[str] = []

    def observed_revalidate(got, **coordinates):
        current = runner.admission.snapshot(str(tmp_path), context.run_id)
        assert len(current.active_claims) == 1, 'active-surface CAS 必须发生在 claim 之后'
        assert got == permit
        assert coordinates == {
            'base': str(tmp_path),
            'sid': '42',
            'node': 'str06.stage2.book',
            'flags': [],
        }
        order.append('revalidate')

    monkeypatch.setattr(
        runner.permit_layer, 'revalidate_active_execution_surface',
        observed_revalidate)

    def observed_write(path, data):
        current = runner.admission.snapshot(str(tmp_path), context.run_id)
        assert len(current.active_claims) == 1, 'evidence 落盘前必须已经 claim'
        order.append('evidence')
        original_write(path, data)

    monkeypatch.setattr(runner, '_write_evidence', observed_write)
    calls = []

    def fake_run(argv, **kwargs):
        order.append('subprocess')
        calls.append((list(argv), kwargs))
        claim_id = kwargs['env'][runner.admission.CLAIM_ID_ENV]
        current = runner.admission.snapshot(str(tmp_path), context.run_id)
        assert [item.claim_id for item in current.active_claims] == [claim_id]
        assert kwargs['env'][runner.permit_layer.STEP_INDEX_ENV] == '0'
        assert json.loads(
            kwargs['env'][runner.permit_layer.PERMIT_JSON_ENV]
        ) == permit.model_dump(mode='json')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner.subprocess, 'run', fake_run)
    evidence = tmp_path / 'claimed-evidence.json'
    rc = runner.main([
        '--base', str(tmp_path), '--sid', '42',
        '--expect-code-version', 'code-v1', '--execute',
        '--evidence-json', str(evidence),
        '--operator-permit-file', str(token), 'str06.stage2.book'])

    assert rc == 0 and evidence.is_file() and len(calls) == 3
    assert order[:2] == ['revalidate', 'evidence']
    assert order.count('revalidate') == 1
    assert runner.admission.snapshot(
        str(tmp_path), context.run_id).active_claims == ()



def test_headless_execute_releases_claim_when_node_spawn_raises(
        monkeypatch, tmp_path):
    runner = _operator_script('run_step')
    profiles = _executor_profiles()
    context = SimpleNamespace(
        run_id='outer-exception', base=str(tmp_path.resolve()),
        contract_sha256='b' * 64,
        start=SimpleNamespace(
            authorized=True, egress_authorized=True, code_version='code-v1'),
        profiles=profiles,
        executor_backend=lambda _node: profiles.executor_default,
    )
    permit = _execution_permit(base=str(tmp_path.resolve()))
    step = _step_stub(
        label='stage2 全局事实',
        argv=['python', '-m', 'novel.cli', 'run', 'str06.stage2.book', '42'],
        cwd=str(REPO_ROOT), paid=True,
        writes='stage2/observations/book.json', note=None,
        executor_backend=profiles.executor_default, egress_authorized=True)
    runner.admission.open_gate(str(tmp_path), context.run_id, '42')
    monkeypatch.setenv(runner.execution_context.COMMANDER_RUN_ID_ENV, context.run_id)
    monkeypatch.setenv(
        runner.execution_context.COMMANDER_CONTRACT_SHA256_ENV, 'b' * 64)
    monkeypatch.setattr(
        runner.execution_context, 'load_from_environment',
        lambda *_args, **_kwargs: context)
    monkeypatch.setattr(
        runner.execution_context, 'env_with_context', lambda **_kwargs: {})
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')
    permit_file = tmp_path / 'execution-permit.json'
    permit_file.write_text(permit.model_dump_json(indent=2), encoding='utf-8')
    monkeypatch.setattr(
        runner.permit_layer, 'revalidate_active_execution_surface',
        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, 'build_steps', lambda *_args, **_kwargs: [step])

    def fake_run(argv, **_kwargs):
        if 'mark' in argv:
            return SimpleNamespace(returncode=0)
        raise OSError('synthetic admitted spawn failure')

    monkeypatch.setattr(runner.subprocess, 'run', fake_run)
    assert runner.main([
        '--base', str(tmp_path), '--sid', '42',
        '--expect-code-version', 'code-v1', '--execute',
        '--operator-permit-file', str(permit_file), 'str06.stage2.book']) == runner.X.CRASH
    assert runner.admission.snapshot(
        str(tmp_path), context.run_id).active_claims == ()



def test_delayed_headless_run_step_after_parent_close_never_writes_or_spawns(
        monkeypatch, tmp_path):
    runner = _operator_script('run_step')
    profiles = _executor_profiles()
    context = SimpleNamespace(
        run_id='outer-closed', base=str(tmp_path.resolve()),
        contract_sha256='c' * 64,
        start=SimpleNamespace(
            authorized=True, egress_authorized=True, code_version='code-v1'),
        profiles=profiles,
        executor_backend=lambda _node: profiles.executor_default,
    )
    permit = _execution_permit(base=str(tmp_path.resolve()))
    runner.admission.open_gate(str(tmp_path), context.run_id, '42')
    runner.admission.close_gate(str(tmp_path), context.run_id)
    monkeypatch.setenv(runner.execution_context.COMMANDER_RUN_ID_ENV, context.run_id)
    monkeypatch.setenv(
        runner.execution_context.COMMANDER_CONTRACT_SHA256_ENV, 'c' * 64)
    monkeypatch.setattr(
        runner.execution_context, 'load_from_environment',
        lambda *_args, **_kwargs: context)
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')
    monkeypatch.setattr(
        runner.operator_planner, 'permit', lambda *_args, **_kwargs: permit)
    monkeypatch.setattr(
        runner, 'build_steps',
        lambda *_args, **_kwargs: [_step_stub(
            label='stage2 全局事实', argv=['python'], cwd=str(REPO_ROOT),
            paid=True, writes='stage2/observations/book.json', note=None,
            executor_backend=profiles.executor_default,
            egress_authorized=True)])
    monkeypatch.setattr(
        runner, '_write_evidence',
        lambda *_args, **_kwargs: pytest.fail('post-close 不得写 evidence'))
    monkeypatch.setattr(
        runner.subprocess, 'run',
        lambda *_args, **_kwargs: pytest.fail('post-close 不得打 beacon/启动节点'))

    rc = runner.main([
        '--base', str(tmp_path), '--sid', '42',
        '--expect-code-version', 'code-v1', '--execute',
        '--evidence-json', str(tmp_path / 'never.json'),
        '--operator-permit-sha256', permit.permit_sha256, 'str06.stage2.book'])

    assert rc == 2



def test_headless_run_step_requires_a_machine_selected_execution_permit(
        monkeypatch, tmp_path):
    runner = _operator_script('run_step')
    context = SimpleNamespace(
        run_id='outer-1', base=str(tmp_path.resolve()),
        contract_sha256='d' * 64,
        start=SimpleNamespace(
            authorized=True, egress_authorized=True, code_version='code-v1'),
        profiles=_executor_profiles(),
        executor_backend=lambda _node: _executor_profiles().executor_default,
    )
    monkeypatch.setenv(runner.execution_context.COMMANDER_RUN_ID_ENV, 'outer-1')
    monkeypatch.setenv(
        runner.execution_context.COMMANDER_CONTRACT_SHA256_ENV, 'd' * 64)
    monkeypatch.setattr(
        runner.execution_context, 'load_from_environment',
        lambda *_args, **_kwargs: context)
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')
    monkeypatch.setattr(
        runner, 'build_steps',
        lambda *_args, **_kwargs: pytest.fail('缺 execution permit 不得构造节点命令'))

    rc = runner.main([
        '--base', str(tmp_path), '--sid', '42',
        '--expect-code-version', 'code-v1', 'str06.stage2.book',
    ])

    assert rc == 2



@pytest.mark.parametrize('permit_error', [
    'exact node/flags 与当前 ready action 不一致',
    'blocked 只允许登记的 recovery 组合',
])
def test_run_step_refuses_an_unmintable_execution_permit_before_building(
        permit_error, monkeypatch, tmp_path, capsys):
    runner = _operator_script('run_step')
    built: list[bool] = []
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')

    def reject(*_args, **_kwargs):
        raise runner.TerminalState(permit_error)

    monkeypatch.setattr(runner.operator_planner, 'permit', reject)
    monkeypatch.setattr(
        runner, 'build_steps',
        lambda *_args, **_kwargs: built.append(True) or [])

    rc = runner.main([
        '--base', str(tmp_path), '--sid', '42',
        '--expect-code-version', 'code-v1',
        '--operator-permit-sha256', 'c' * 64, 'str06.stage2.book',
    ])

    assert rc == 2
    assert built == []
    assert permit_error in capsys.readouterr().err



def test_run_step_rejects_a_stale_permit_hash_before_building(
        monkeypatch, tmp_path, capsys):
    runner = _operator_script('run_step')
    built: list[bool] = []
    current = _execution_permit(base=str(tmp_path.resolve()))
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')
    monkeypatch.setattr(
        runner.operator_planner, 'permit', lambda *_args, **_kwargs: current)
    monkeypatch.setattr(
        runner, 'build_steps',
        lambda *_args, **_kwargs: built.append(True) or [])

    rc = runner.main([
        '--base', str(tmp_path), '--sid', '42',
        '--expect-code-version', 'code-v1',
        '--operator-permit-sha256', 'c' * 64, 'str06.stage2.book',
    ])

    assert rc == 2
    assert built == []
    assert '已过期' in capsys.readouterr().err



def test_run_step_rejects_an_invalid_run_id_at_the_argument_boundary(capsys):
    """run_id 拼错不能先 build/写 evidence，更不能 traceback 成未知崩溃。"""
    runner = _operator_script('run_step')

    with pytest.raises(SystemExit) as exc:
        runner.main([
            '--base', 'E:/outputs', '--expect-code-version', 'code-v1',
            '--run-id', '../cross-run', 'stage2.blueprint',
        ])

    assert exc.value.code == 2
    assert 'run_id' in capsys.readouterr().err



def test_run_step_stops_before_building_when_the_session_code_changed(monkeypatch):
    runner = _operator_script('run_step')
    built: list[bool] = []
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v2')
    monkeypatch.setattr(
        runner, 'build_steps', lambda *args, **kwargs: built.append(True) or [])

    rc = runner.main([
        '--base', 'E:/outputs', '--sid', '42',
        '--expect-code-version', 'code-v1', 'str06.stage2.book',
    ])

    assert rc == 2
    assert built == []



def test_run_step_evidence_serializes_the_same_steps_for_preview_and_execute(
        tmp_path, monkeypatch):
    runner = _operator_script('run_step')
    evidence = tmp_path / 'nested' / 'preview.json'
    step_argv = [
        'python', '-B', '-m', 'novel.cli', 'run', 'str06.stage5.body', '42',
        '--base', 'E:/outputs', '--yes',
    ]
    step = _step_stub(
        label='stage5 全书闭卷写作', argv=step_argv, cwd=str(REPO_ROOT),
        paid=True, writes='stage5/ch{nn}.json',
        note='随后免费复检')
    built: list[tuple] = []
    calls: list[list[str]] = []
    monkeypatch.setattr(
        runner, 'build_steps',
        lambda *args, **kwargs: built.append((args, kwargs)) or [step])
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')
    permit = _execution_permit(node='str06.stage5.body')
    permit_file = tmp_path / 'execution-permit.json'
    permit_file.write_text(permit.model_dump_json(indent=2), encoding='utf-8')
    monkeypatch.setattr(
        runner.permit_layer, 'revalidate_active_execution_surface',
        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runner.subprocess, 'run',
        lambda argv, **_kwargs: calls.append(list(argv)) or SimpleNamespace(returncode=0))

    common = [
        '--base', 'E:/outputs', '--sid', '42', '--expect-code-version', 'code-v1',
        '--operator-permit-file', str(permit_file),
        'str06.stage5.body', '--evidence-json', str(evidence),
    ]
    assert runner.main(common) == 0
    preview_bytes = evidence.read_bytes()
    assert json.loads(preview_bytes) == {
        'schema': 'novel-operator-step-evidence-v5',
        'execution_permit': permit.model_dump(mode='json'),
        'steps': [{
            'argv': step_argv,
            'paid': True,
            'writes': 'stage5/ch{nn}.json',
            'label': 'stage5 全书闭卷写作',
            'note': '随后免费复检',
            'request_profile': None,
        }],
    }
    assert built[0][0] == ('str06.stage5.body', '42', [])
    assert built[0][1]['placeholders'] is True
    assert calls == []

    assert runner.main([*common, '--execute']) == 0
    assert evidence.read_bytes() == preview_bytes
    assert built[1][0] == ('str06.stage5.body', '42', [])
    assert built[1][1]['placeholders'] is False
    assert step_argv in calls
    expected_note = 'operator_evidence_sha256=' + runner.hashlib.sha256(
        preview_bytes).hexdigest()
    mark_calls = [argv for argv in calls if 'mark' in argv]
    assert len(mark_calls) == 2
    assert all(argv[argv.index('--note') + 1] == expected_note for argv in mark_calls)



def test_run_step_execute_partial_cannot_reuse_full_book_preview_evidence(
        tmp_path, monkeypatch):
    runner = _operator_script('run_step')
    evidence = tmp_path / 'preview.json'

    def step(flags):
        argv = ['python', '-m', 'novel.cli', 'run', 'str06.stage5.body', '42', *flags,
                '--base', 'E:/outputs', '--yes']
        return _step_stub(
            label='stage5 写作', argv=argv, cwd=str(REPO_ROOT), paid=True,
            writes='stage5/ch{nn}.json', note=None)

    def fake_build(_node, _sid, flags, **_kwargs):
        return [step(list(flags))]

    calls = []
    monkeypatch.setattr(runner, 'build_steps', fake_build)
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')
    full_permit = _execution_permit(node='str06.stage5.body')
    partial_permit = _execution_permit(
        node='str06.stage5.body', flags=['--chapters', '1'])

    def mint(_base, _sid, _node, flags):
        return partial_permit if flags else full_permit

    monkeypatch.setattr(runner.operator_planner, 'permit', mint)
    monkeypatch.setattr(
        runner.subprocess, 'run',
        lambda *args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(returncode=0))
    common = [
        '--base', 'E:/outputs', '--sid', '42', '--expect-code-version', 'code-v1',
        '--evidence-json', str(evidence),
    ]
    assert runner.main([
        *common, '--operator-permit-sha256', full_permit.permit_sha256,
        'str06.stage5.body']) == 0
    assert runner.main([
        *common, '--execute',
        '--operator-permit-sha256', partial_permit.permit_sha256,
        'str06.stage5.body', '--', '--chapters', '1']) == 2
    assert calls == [], 'evidence hash 不同必须在打信标/执行前停止'



def test_run_step_evidence_refuses_different_existing_content_before_execution(
        tmp_path, monkeypatch):
    runner = _operator_script('run_step')
    evidence = tmp_path / 'preview.json'
    original = b'{"different":true}\n'
    evidence.write_bytes(original)
    step = _step_stub(
        label='stage5 写作',
        argv=['python', '-m', 'novel.cli', 'run', 'str06.stage5.body', '42', '--yes'],
        cwd=str(REPO_ROOT), paid=True, writes='stage5/ch{nn}.json', note=None)
    monkeypatch.setattr(runner, 'build_steps', lambda *_args, **_kwargs: [step])
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')
    permit = _execution_permit(node='str06.stage5.body')
    monkeypatch.setattr(
        runner.operator_planner, 'permit', lambda *_args, **_kwargs: permit)
    monkeypatch.setattr(
        runner.subprocess, 'run',
        lambda *_args, **_kwargs: pytest.fail('不同证据不得打信标或执行节点'))

    rc = runner.main([
        '--base', 'E:/outputs', '--sid', '42', '--expect-code-version', 'code-v1',
        '--evidence-json', str(evidence), '--execute',
        '--operator-permit-sha256', permit.permit_sha256, 'str06.stage5.body',
    ])

    assert rc == 2
    assert evidence.read_bytes() == original



def test_run_step_evidence_refuses_a_suspected_key_without_writing_or_execution(
        tmp_path, monkeypatch):
    runner = _operator_script('run_step')
    evidence = tmp_path / 'preview.json'
    step = _step_stub(
        label='synthetic',
        argv=['python', '-m', 'novel.cli', '--api-key=must-not-land'],
        cwd=str(REPO_ROOT), paid=False, writes='', note=None)
    monkeypatch.setattr(runner, 'build_steps', lambda *_args, **_kwargs: [step])
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')
    permit = _execution_permit(node='str06.stage5.body')
    monkeypatch.setattr(
        runner.operator_planner, 'permit', lambda *_args, **_kwargs: permit)
    monkeypatch.setattr(
        runner.subprocess, 'run',
        lambda *_args, **_kwargs: pytest.fail('疑似密钥不得进入执行路径'))

    rc = runner.main([
        '--base', 'E:/outputs', '--sid', '42', '--expect-code-version', 'code-v1',
        '--evidence-json', str(evidence), '--execute',
        '--operator-permit-sha256', permit.permit_sha256, 'str06.stage5.body',
    ])

    assert rc == 2
    assert not evidence.exists()



def test_session_preflight_materializes_one_reusable_run_coordinate(
        tmp_path, monkeypatch):
    preflight = _operator_script('session_preflight')
    book = tmp_path / '测试书_42'
    book.mkdir()
    monkeypatch.setattr(preflight.prov, 'code_version', lambda: 'code-v1')
    monkeypatch.setattr(preflight.time, 'time', lambda: 1234567890)

    facts = preflight.capture(tmp_path, sid='42', session_id='session-1')

    assert facts['started_at'] == 1234567890
    assert facts['code_version'] == 'code-v1'
    assert facts['sid'] == '42'
    assert Path(str(facts['novel_dir'])) == book.resolve()
    assert facts['session_id'] == 'session-1'
    assert Path(str(facts['scratch_dir'])) == (
        tmp_path / '_service' / 'scratch' / '42' / 'session-1').resolve()
    assert Path(str(facts['scratch_dir'])).is_dir()



def test_session_preflight_finish_removes_only_the_named_scratch(
        tmp_path, monkeypatch, capsys):
    preflight = _operator_script('session_preflight')
    book = tmp_path / 'book_42'
    book.mkdir()
    monkeypatch.setattr(preflight.prov, 'code_version', lambda: 'code-v1')
    first = preflight.capture(tmp_path, sid='42', session_id='session-1')
    second = preflight.capture(tmp_path, sid='42', session_id='session-2')

    rc = preflight.main([
        '--base', str(tmp_path), '--sid', '42',
        '--finish-session', 'session-1',
    ])

    assert rc == 0
    assert not Path(str(first['scratch_dir'])).exists()
    assert Path(str(second['scratch_dir'])).is_dir()
    result = json.loads(capsys.readouterr().out)
    assert result['state'] == 'finished'
    assert result['scratch_removed'] is True



def test_delivery_verifier_requires_the_start_code_version():
    verifier = _operator_script('verify_delivery')
    with pytest.raises(SystemExit) as exc:
        verifier.main(['--base', 'E:/outputs', '--sid', '42'])
    assert exc.value.code == 2



def test_runner_keeps_keyboard_interrupt_control_semantics(monkeypatch):
    runner = _operator_script('run_step')

    def interrupt(_argv):
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, '_main', interrupt)
    with pytest.raises(KeyboardInterrupt):
        runner.main([])



@pytest.mark.parametrize(('fault', 'exit_code'), [
    ('RuntimeError("runner failed")', 70),
    ('SystemExit(1)', 1),
    ('TerminalState("explicit stop")', 2),
])
def test_runner_process_distinguishes_own_exception_from_explicit_exit(
        tmp_path, fault, exit_code):
    """通过真实 Python 子进程验证 runner 最外层退出域，不由 pytest 截住异常。"""
    import subprocess

    script = tmp_path / 'runner-boundary.py'
    script.write_text(
        "import importlib.util, sys\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from novel.errors import TerminalState\n"
        f"spec = importlib.util.spec_from_file_location('runner', "
        f"{str(REPO_ROOT / 'novel' / 'orchestrator' / 'run_step.py')!r})\n"
        "runner = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(runner)\n"
        "def fail(_argv):\n"
        f"    raise {fault}\n"
        "runner._main = fail\n"
        "raise SystemExit(runner.main([]))\n", encoding='utf-8')
    result = subprocess.run(
        [sys.executable, '-B', str(script)], cwd=REPO_ROOT,
        capture_output=True, check=False, text=True, encoding='utf-8')
    assert result.returncode == exit_code, result.stderr
    if exit_code == 70:
        assert 'RuntimeError: runner failed' in result.stderr
        assert 'exit 70' in result.stderr


def test_follow_check_purchase_required_discriminator():
    """R10c 硬化：追跑 check-only 的 purchase_required 判别（修订收敛节点
    基础身份无可用答案→免费复检恒报未授权；权威归下游复验，降级为警告）。
    其余显式终态（上游过期等）不误伤。"""
    from novel.orchestrator.run_step import _follow_check_purchase_required
    from novel import exit_codes as X
    hit = '✗ 显式终态（缺少付费/外发授权）：preserve 无同请求身份结果…'
    assert _follow_check_purchase_required(X.TERMINAL_STATE, hit)
    assert not _follow_check_purchase_required(X.OK, hit)
    assert not _follow_check_purchase_required(X.TERMINAL_STATE, None)
    assert not _follow_check_purchase_required(X.TERMINAL_STATE, '')
    # 其他显式终态（如上游过期）不降级。
    assert not _follow_check_purchase_required(
        X.TERMINAL_STATE, '✗ 显式终态（勿静默）：候选产物的上游已过期…')
    assert not _follow_check_purchase_required(
        X.CHECK_FAILED, hit)
