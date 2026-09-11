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
    AgentExecutorBackend, AgentProfile, NovelOverview, OverviewSummary,
    PipelineAgentProfiles,
)
from contracts.schema.report import finding
from novel.errors import UpstreamMissing
from novel.prov import EdgeVerdict, FreshnessReport
from novel.report import build, render
from novel.report.findings import ran, unverifiable
from novel.orchestrator.operator import canonical_sha256
from novel.orchestrator.nodes import is_paid, write_effect

REPO_ROOT = Path(__file__).resolve().parents[1]


def _operator_script(name: str):
    import importlib
    modules = {
        'run_step': 'novel.orchestrator.run_step',
        'session_preflight': 'novel.orchestrator.preflight',
        'verify_delivery': 'novel.orchestrator.delivery',
        'stage0_review_workpack': 'novel.stages.stage0.review_workpack',
    }
    return importlib.import_module(modules[name])


def test_run_step_closes_execution_with_crash_when_node_spawn_raises(
        monkeypatch, tmp_path):
    runner = _operator_script('run_step')
    step = SimpleNamespace(
        label='stage2 全局事实',
        argv=['python', '-m', 'novel.cli', 'run', 'stage2.global', '42'],
        cwd=str(REPO_ROOT), paid=True, writes='stage2/global_grounding.json',
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
        '--operator-permit-file', str(permit_file), 'stage2.global',
    ]) == runner.X.CRASH

    start, _node, end = calls
    assert start[start.index('--execution-id') + 1] == (
        end[end.index('--execution-id') + 1])
    assert end[end.index('--phase') + 1] == 'end'
    assert end[end.index('--exit') + 1] == str(runner.X.CRASH)



def _commander_profiles() -> PipelineAgentProfiles:
    return PipelineAgentProfiles(
        commander=AgentProfile(
            driver='claude-code', model='claude-opus-5',
            reasoning_effort='high', execution_mode='standard'),
        executor_default=AgentExecutorBackend(profile=AgentProfile(
            driver='codex', model='gpt-5.6-sol',
            reasoning_effort='high', execution_mode='standard')),
        executor_overrides={},
    )



def _execution_permit(
        *, base: str = 'E:/outputs', sid: str = '42',
        node: str = 'stage2.global', flags: list[str] | None = None,
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
    """完整 token 已冻结 preview；渲染步骤不能再跑七层 planner/CAS。"""
    runner = _operator_script('run_step')
    permit = _execution_permit(base=str(tmp_path.resolve()))
    token = _write_ready_next(tmp_path / 'operator-next.json', permit)
    step = SimpleNamespace(
        label='stage2 全局事实',
        argv=['python', '-m', 'novel.cli', 'run', 'stage2.global', '42'],
        cwd=str(REPO_ROOT), paid=True,
        writes=write_effect('stage2.global', []), note=None,
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
        '--operator-permit-file', str(token), 'stage2.global',
    ])

    assert rc == 0
    assert built == [(('stage2.global', '42', []), {
        'base': str(tmp_path.resolve()),
        'executor_backend': None,
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
        '--operator-permit-file', str(token), 'stage2.global',
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
        '--operator-permit-file', str(token), 'stage2.global',
    ])

    assert rc == 2
    assert 'exact sid/node/flags' in capsys.readouterr().err



def test_run_step_projects_frozen_executor_and_egress_into_production_steps(
        monkeypatch, tmp_path):
    runner = _operator_script('run_step')
    profiles = _commander_profiles()
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
    step = SimpleNamespace(
        label='stage2 全局事实',
        argv=['python', '-m', 'novel.cli', 'run', 'stage2.global', '42'],
        cwd=str(REPO_ROOT), paid=True,
        writes='stage2/global_grounding.json', note=None,
        executor_backend=profile, egress_authorized=True)
    monkeypatch.setenv(runner.commander_context.COMMANDER_RUN_ID_ENV, 'outer-1')
    monkeypatch.setenv(
        runner.commander_context.COMMANDER_CONTRACT_SHA256_ENV, 'a' * 64)
    monkeypatch.setattr(
        runner.commander_context, 'load_from_environment',
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
        'stage2.global',
    ])

    assert rc == 0
    assert built[0][1]['executor_backend'] == profile
    assert built[0][1]['egress_authorized'] is True
    payload = json.loads(evidence.read_bytes())
    assert payload['schema'] == 'novel-operator-step-evidence-v4'
    assert payload['commander']['run_id'] == 'outer-1'
    assert payload['execution_permit'] == permit.model_dump(mode='json')
    assert payload['steps'][0]['executor_backend'] == profile.model_dump(mode='json')
    assert payload['steps'][0]['egress_authorized'] is True



def test_run_step_with_frozen_profile_refuses_execute_without_both_authorizations(
        monkeypatch, tmp_path):
    runner = _operator_script('run_step')
    profiles = _commander_profiles()
    context = SimpleNamespace(
        run_id='outer-1', base=str(tmp_path.resolve()),
        contract_sha256='b' * 64,
        start=SimpleNamespace(
            authorized=True, egress_authorized=False, code_version='code-v1'),
        profiles=profiles,
        executor_backend=lambda _node: profiles.executor_default,
    )
    step = SimpleNamespace(
        label='stage2 全局事实', argv=['python'], cwd=str(REPO_ROOT),
        paid=True, writes='stage2/global_grounding.json', note=None,
        executor_backend=profiles.executor_default, egress_authorized=False)
    monkeypatch.setenv(runner.commander_context.COMMANDER_RUN_ID_ENV, 'outer-1')
    monkeypatch.setenv(
        runner.commander_context.COMMANDER_CONTRACT_SHA256_ENV, 'b' * 64)
    monkeypatch.setattr(
        runner.commander_context, 'load_from_environment',
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
        '--operator-permit-sha256', permit.permit_sha256, 'stage2.global',
    ])

    assert rc == 2



def test_headless_execute_claims_before_evidence_and_releases_after_steps(
        monkeypatch, tmp_path):
    runner = _operator_script('run_step')
    profiles = _commander_profiles()
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
    step = SimpleNamespace(
        label='stage2 全局事实',
        argv=['python', '-m', 'novel.cli', 'run', 'stage2.global', '42'],
        cwd=str(REPO_ROOT), paid=True,
        writes='stage2/global_grounding.json', note=None,
        executor_backend=profiles.executor_default, egress_authorized=True)
    runner.admission.open_gate(str(tmp_path), context.run_id, '42')
    monkeypatch.setenv(runner.commander_context.COMMANDER_RUN_ID_ENV, context.run_id)
    monkeypatch.setenv(
        runner.commander_context.COMMANDER_CONTRACT_SHA256_ENV, 'a' * 64)
    monkeypatch.setattr(
        runner.commander_context, 'load_from_environment',
        lambda *_args, **_kwargs: context)
    monkeypatch.setattr(
        runner.commander_context, 'env_with_context',
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
            'node': 'stage2.global',
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
        '--operator-permit-file', str(token), 'stage2.global'])

    assert rc == 0 and evidence.is_file() and len(calls) == 3
    assert order[:2] == ['revalidate', 'evidence']
    assert order.count('revalidate') == 1
    assert runner.admission.snapshot(
        str(tmp_path), context.run_id).active_claims == ()



def test_headless_execute_releases_claim_when_node_spawn_raises(
        monkeypatch, tmp_path):
    runner = _operator_script('run_step')
    profiles = _commander_profiles()
    context = SimpleNamespace(
        run_id='outer-exception', base=str(tmp_path.resolve()),
        contract_sha256='b' * 64,
        start=SimpleNamespace(
            authorized=True, egress_authorized=True, code_version='code-v1'),
        profiles=profiles,
        executor_backend=lambda _node: profiles.executor_default,
    )
    permit = _execution_permit(base=str(tmp_path.resolve()))
    step = SimpleNamespace(
        label='stage2 全局事实',
        argv=['python', '-m', 'novel.cli', 'run', 'stage2.global', '42'],
        cwd=str(REPO_ROOT), paid=True,
        writes='stage2/global_grounding.json', note=None,
        executor_backend=profiles.executor_default, egress_authorized=True)
    runner.admission.open_gate(str(tmp_path), context.run_id, '42')
    monkeypatch.setenv(runner.commander_context.COMMANDER_RUN_ID_ENV, context.run_id)
    monkeypatch.setenv(
        runner.commander_context.COMMANDER_CONTRACT_SHA256_ENV, 'b' * 64)
    monkeypatch.setattr(
        runner.commander_context, 'load_from_environment',
        lambda *_args, **_kwargs: context)
    monkeypatch.setattr(
        runner.commander_context, 'env_with_context', lambda **_kwargs: {})
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
        '--operator-permit-file', str(permit_file), 'stage2.global']) == runner.X.CRASH
    assert runner.admission.snapshot(
        str(tmp_path), context.run_id).active_claims == ()



def test_delayed_headless_run_step_after_parent_close_never_writes_or_spawns(
        monkeypatch, tmp_path):
    runner = _operator_script('run_step')
    profiles = _commander_profiles()
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
    monkeypatch.setenv(runner.commander_context.COMMANDER_RUN_ID_ENV, context.run_id)
    monkeypatch.setenv(
        runner.commander_context.COMMANDER_CONTRACT_SHA256_ENV, 'c' * 64)
    monkeypatch.setattr(
        runner.commander_context, 'load_from_environment',
        lambda *_args, **_kwargs: context)
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')
    monkeypatch.setattr(
        runner.operator_planner, 'permit', lambda *_args, **_kwargs: permit)
    monkeypatch.setattr(
        runner, 'build_steps',
        lambda *_args, **_kwargs: [SimpleNamespace(
            label='stage2 全局事实', argv=['python'], cwd=str(REPO_ROOT),
            paid=True, writes='stage2/global_grounding.json', note=None,
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
        '--operator-permit-sha256', permit.permit_sha256, 'stage2.global'])

    assert rc == 2



def test_headless_run_step_requires_a_machine_selected_execution_permit(
        monkeypatch, tmp_path):
    runner = _operator_script('run_step')
    context = SimpleNamespace(
        run_id='outer-1', base=str(tmp_path.resolve()),
        contract_sha256='d' * 64,
        start=SimpleNamespace(
            authorized=True, egress_authorized=True, code_version='code-v1'),
        profiles=_commander_profiles(),
        executor_backend=lambda _node: _commander_profiles().executor_default,
    )
    monkeypatch.setenv(runner.commander_context.COMMANDER_RUN_ID_ENV, 'outer-1')
    monkeypatch.setenv(
        runner.commander_context.COMMANDER_CONTRACT_SHA256_ENV, 'd' * 64)
    monkeypatch.setattr(
        runner.commander_context, 'load_from_environment',
        lambda *_args, **_kwargs: context)
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')
    monkeypatch.setattr(
        runner, 'build_steps',
        lambda *_args, **_kwargs: pytest.fail('缺 execution permit 不得构造节点命令'))

    rc = runner.main([
        '--base', str(tmp_path), '--sid', '42',
        '--expect-code-version', 'code-v1', 'stage2.global',
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
        '--operator-permit-sha256', 'c' * 64, 'stage2.global',
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
        '--operator-permit-sha256', 'c' * 64, 'stage2.global',
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
        '--expect-code-version', 'code-v1', 'stage2.global',
    ])

    assert rc == 2
    assert built == []



def test_run_step_evidence_serializes_the_same_steps_for_preview_and_execute(
        tmp_path, monkeypatch):
    runner = _operator_script('run_step')
    evidence = tmp_path / 'nested' / 'preview.json'
    step_argv = [
        'python', '-B', '-m', 'novel.cli', 'run', 'stage5.write', '42',
        '--base', 'E:/outputs', '--yes',
    ]
    step = SimpleNamespace(
        label='stage5 全书闭卷写作', argv=step_argv, cwd=str(REPO_ROOT),
        paid=True, writes='stage5/ch{nn}.json',
        note='随后免费复检')
    built: list[tuple] = []
    calls: list[list[str]] = []
    monkeypatch.setattr(
        runner, 'build_steps',
        lambda *args, **kwargs: built.append((args, kwargs)) or [step])
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')
    permit = _execution_permit(node='stage5.write')
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
        'stage5.write', '--evidence-json', str(evidence),
    ]
    assert runner.main(common) == 0
    preview_bytes = evidence.read_bytes()
    assert json.loads(preview_bytes) == {
        'schema': 'novel-operator-step-evidence-v2',
        'execution_permit': permit.model_dump(mode='json'),
        'steps': [{
            'argv': step_argv,
            'paid': True,
            'writes': 'stage5/ch{nn}.json',
            'label': 'stage5 全书闭卷写作',
            'note': '随后免费复检',
        }],
    }
    assert built[0][0] == ('stage5.write', '42', [])
    assert built[0][1]['placeholders'] is True
    assert calls == []

    assert runner.main([*common, '--execute']) == 0
    assert evidence.read_bytes() == preview_bytes
    assert built[1][0] == ('stage5.write', '42', [])
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
        argv = ['python', '-m', 'novel.cli', 'run', 'stage5.write', '42', *flags,
                '--base', 'E:/outputs', '--yes']
        return SimpleNamespace(
            label='stage5 写作', argv=argv, cwd=str(REPO_ROOT), paid=True,
            writes='stage5/ch{nn}.json', note=None)

    def fake_build(_node, _sid, flags, **_kwargs):
        return [step(list(flags))]

    calls = []
    monkeypatch.setattr(runner, 'build_steps', fake_build)
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')
    full_permit = _execution_permit(node='stage5.write')
    partial_permit = _execution_permit(
        node='stage5.write', flags=['--chapters', '1'])

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
        'stage5.write']) == 0
    assert runner.main([
        *common, '--execute',
        '--operator-permit-sha256', partial_permit.permit_sha256,
        'stage5.write', '--', '--chapters', '1']) == 2
    assert calls == [], 'evidence hash 不同必须在打信标/执行前停止'



def test_run_step_evidence_refuses_different_existing_content_before_execution(
        tmp_path, monkeypatch):
    runner = _operator_script('run_step')
    evidence = tmp_path / 'preview.json'
    original = b'{"different":true}\n'
    evidence.write_bytes(original)
    step = SimpleNamespace(
        label='stage5 写作',
        argv=['python', '-m', 'novel.cli', 'run', 'stage5.write', '42', '--yes'],
        cwd=str(REPO_ROOT), paid=True, writes='stage5/ch{nn}.json', note=None)
    monkeypatch.setattr(runner, 'build_steps', lambda *_args, **_kwargs: [step])
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')
    permit = _execution_permit(node='stage5.write')
    monkeypatch.setattr(
        runner.operator_planner, 'permit', lambda *_args, **_kwargs: permit)
    monkeypatch.setattr(
        runner.subprocess, 'run',
        lambda *_args, **_kwargs: pytest.fail('不同证据不得打信标或执行节点'))

    rc = runner.main([
        '--base', 'E:/outputs', '--sid', '42', '--expect-code-version', 'code-v1',
        '--evidence-json', str(evidence), '--execute',
        '--operator-permit-sha256', permit.permit_sha256, 'stage5.write',
    ])

    assert rc == 2
    assert evidence.read_bytes() == original



def test_run_step_evidence_refuses_a_suspected_key_without_writing_or_execution(
        tmp_path, monkeypatch):
    runner = _operator_script('run_step')
    evidence = tmp_path / 'preview.json'
    step = SimpleNamespace(
        label='synthetic',
        argv=['python', '-m', 'novel.cli', '--api-key=must-not-land'],
        cwd=str(REPO_ROOT), paid=False, writes='', note=None)
    monkeypatch.setattr(runner, 'build_steps', lambda *_args, **_kwargs: [step])
    monkeypatch.setattr(runner.prov, 'code_version', lambda: 'code-v1')
    permit = _execution_permit(node='stage5.write')
    monkeypatch.setattr(
        runner.operator_planner, 'permit', lambda *_args, **_kwargs: permit)
    monkeypatch.setattr(
        runner.subprocess, 'run',
        lambda *_args, **_kwargs: pytest.fail('疑似密钥不得进入执行路径'))

    rc = runner.main([
        '--base', 'E:/outputs', '--sid', '42', '--expect-code-version', 'code-v1',
        '--evidence-json', str(evidence), '--execute',
        '--operator-permit-sha256', permit.permit_sha256, 'stage5.write',
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



def _stage0_check_report(*, status: str = 'unver'):
    line = SimpleNamespace(
        rule='s0_correctness', sev='unver', status=status,
        scanned='0 个剔除段待人工语义确认',
        why='结构规则不能代替人工语义判断' if status == 'unver' else None,
        findings=[],
    )
    return SimpleNamespace(
        badge='amber' if status == 'unver' else 'pass',
        provenance=SimpleNamespace(freshness='fresh'),
        counts=SimpleNamespace(model_dump=lambda **_kwargs: {
            'ok': 12 if status == 'unver' else 13,
            'hit': 0, 'unver': 1 if status == 'unver' else 0, 'skipped': 0,
        }),
        rules=[line],
    )



def _stage0_segment(text: str, start: int, *, kind: str = 'keep',
                    flagged: bool = False, removed_kind=None,
                    origin=None, marker_line=None):
    return SimpleNamespace(
        kind=kind, start=start, end=start + len(text), chars=len(text),
        text=text, flagged=flagged, removed_kind=removed_kind,
        origin=origin, marker_line=marker_line,
    )



def _wire_stage0_workpack(monkeypatch, tmp_path, module, *, text: str,
                          segments, available: bool = True,
                          unavailable_reason: str | None = None,
                          revision_origin: str = 'machine'):
    calls: list[str] = []
    monkeypatch.setattr(
        module.paths, 'resolve_novel_dir',
        lambda _base, _sid: calls.append('resolve_novel_dir') or tmp_path / 'book')
    monkeypatch.setattr(module.prov, 'code_version', lambda: 'code-v1')
    monkeypatch.setattr(
        module, 'readonly_ctx',
        lambda *_args: calls.append('readonly_ctx') or object())
    monkeypatch.setattr(
        module, 'stage0_report',
        lambda *_args: calls.append('stage0_report') or SimpleNamespace(
            revision_id='a' * 64, revision_origin=revision_origin, executable=False,
            total_code_points=len(text), warnings=[]))
    monkeypatch.setattr(
        module, 'stage0_segments',
        lambda *_args: calls.append('stage0_segments') or SimpleNamespace(
            available=available, reason=unavailable_reason, segments=segments))
    monkeypatch.setattr(
        module, 'run_check_only',
        lambda *_args: calls.append('run_check_only') or _stage0_check_report())
    return calls



def test_stage0_workpack_zero_removal_uses_fixed_review_patterns_and_keeps_decision_human(
        monkeypatch, tmp_path):
    workpack = _operator_script('stage0_review_workpack')
    text = '文案首句\n\n1\n红袖添香在这里是店名。\n2\n完整收束句。\n'
    segment = _stage0_segment(text, 0)
    calls = _wire_stage0_workpack(
        monkeypatch, tmp_path, workpack, text=text, segments=[segment])

    payload = workpack.build_workpack(tmp_path, '42')

    assert payload['schema'] == 'novel-operator-stage0-review-workpack-v3'
    assert payload['route'] == 'zero_removal'
    assert payload['automated_decision'] is None
    assert payload['counts']['removed_segments'] == 0
    assert workpack.REVIEW_PATTERN_VERSION == 'stage0-review-v1'
    assert len(workpack.REVIEW_PATTERNS) == 13
    assert len({item.key for item in workpack.REVIEW_PATTERNS}) == 13
    assert payload['reverse_scan']['patterns_scanned'] == len(workpack.REVIEW_PATTERNS)
    platform = next(
        item for item in payload['reverse_scan']['patterns']
        if item['key'] == 'platform_name')
    assert platform['hits'] == 1
    assert platform['contexts'][0]['matched'] == '红袖添香'
    assert 'chapters' not in payload, '不得在 Stage0 另造章节裁决结果'
    denominator = payload['structure_denominator']
    assert denominator['source'] == 'novel.core.ingest.scan_text'
    assert denominator['chapter_numbers_in_source_order'] == [1, 2]
    assert denominator['chapter_markers'] == [
        {'chapter_no': 1, 'line': 3, 'source_line': '1'},
        {'chapter_no': 2, 'line': 5, 'source_line': '2'},
    ]
    assert denominator['before_first_chapter_marker'] == {
        'state': 'available',
        'nonempty_line_count': 1,
        'nonempty_lines': [{'line': 1, 'text': '文案首句'}],
        'human_classification': None,
        'expected_role': 'required_opening_copy',
        'review_rule': (
            '叙事性概述、提前呈现后文情节或对白、与正文形成重复钩子均是合法'
            '开篇文案体裁；不得仅因不是第一章正文或与后文重复而剔除。只把'
            '书名/作者署名、平台网址、站内导航、更新订阅/票赏推广、作者站务元话语等'
            '不属于作品叙事文案的站务或元数据残留视为问题。'),
    }
    assert denominator['order_or_continuity_decision'] is None
    assert isinstance(payload['production_nonbody_markers'], list)
    assert isinstance(payload['production_end_marker_present'], bool)
    assert payload['boundaries']['first_nonempty_lines'][0] == '文案首句'
    assert payload['boundaries']['last_nonempty_lines'][-1] == '完整收束句。'
    assert payload['retained_review_material']['scope'] == 'complete_current_clean_text'
    assert payload['retained_review_material']['text'] == text
    assert payload['retained_review_material']['code_points'] == len(text)
    assert payload['retained_review_material']['text_sha256'] == hashlib.sha256(
        text.encode('utf-8')).hexdigest()
    assert '首章号前的叙事性文案按' in \
        payload['retained_review_material']['meaning']
    assert '不属于这组三类漏剔' in payload['retained_review_material']['meaning']
    assert '待确认' in payload['approval_reason_draft']
    assert '按原序=[1, 2]' in payload['approval_reason_draft']
    assert '首个章号前非空行=1' in payload['approval_reason_draft']
    assert '预期角色=R-1.12 必填开篇文案' in payload['approval_reason_draft']
    assert '叙事概述或与后文重复本身合法' in payload['approval_reason_draft']
    assert {'stage0_report', 'stage0_segments', 'run_check_only', 'readonly_ctx'} <= set(calls)



def test_stage0_workpack_protects_repeated_narrative_hook_required_by_stage1(
        monkeypatch, tmp_path):
    """本次真书形状：章前概述复现后文对白，仍是 Stage1 必填 hook。"""
    from novel.errors import HookCopyMissing
    from novel.stages.stage1.partition import partition_text

    workpack = _operator_script('stage0_review_workpack')
    hook = '他解开黑袍，长老惊叹：「我们有救了。」\n\n'
    chapters = (
        '1\n弟子央求师尊再讲一次那个传说。\n'
        '2\n他解开黑袍，长老惊叹：「我们有救了。」\n')
    text = hook + chapters
    _wire_stage0_workpack(
        monkeypatch, tmp_path, workpack, text=text,
        segments=[_stage0_segment(text, 0)])

    payload = workpack.build_workpack(tmp_path, '42')
    before = payload['structure_denominator']['before_first_chapter_marker']

    assert before['expected_role'] == 'required_opening_copy'
    assert before['nonempty_lines'] == [
        {'line': 1, 'text': '他解开黑袍，长老惊叹：「我们有救了。」'}]
    assert '与正文形成重复钩子均是合法' in before['review_rule']
    assert partition_text(text).hook_copy.text == hook
    with pytest.raises(HookCopyMissing):
        partition_text(chapters)



def test_stage0_workpack_mixed_hook_reports_station_text_without_deleting_prefix(
        monkeypatch, tmp_path):
    """文案夹站务行时只给人逐条定性，不把整个必填 hook 自动判成删除。"""
    workpack = _operator_script('stage0_review_workpack')
    hook = '她推开门，看见失踪多年的人。\n求订阅收藏。\n这一次，她决定追上去。\n'
    text = hook + '1\n第一章正文。\n2\n第二章正文。\n'
    _wire_stage0_workpack(
        monkeypatch, tmp_path, workpack, text=text,
        segments=[_stage0_segment(text, 0)])

    payload = workpack.build_workpack(tmp_path, '42')
    before = payload['structure_denominator']['before_first_chapter_marker']
    subscribe = next(
        row for row in payload['reverse_scan']['patterns']
        if row['key'] == 'subscribe_prompt')

    assert before['expected_role'] == 'required_opening_copy'
    assert [row['text'] for row in before['nonempty_lines']] == [
        '她推开门，看见失踪多年的人。', '求订阅收藏。', '这一次，她决定追上去。']
    assert subscribe['hits'] == 1
    assert payload['counts']['removed_segments'] == 0
    assert payload['retained_review_material']['text'].startswith(hook)
    assert payload['automated_decision'] is None



def test_stage0_workpack_removed_route_exposes_every_span_and_scans_only_retained_text(
        monkeypatch, tmp_path):
    workpack = _operator_script('stage0_review_workpack')
    before = '1\n正文。\n'
    removed_text = '作者的话：' + ('求收藏与支持。' * 220) + '\n'
    after = '2\n结尾。\n'
    original = before + removed_text + after
    segments = [
        _stage0_segment(before, 0),
        _stage0_segment(
            removed_text, len(before), kind='remove', flagged=True,
            removed_kind='作者有话说', origin='machine', marker_line='作者有话说'),
        _stage0_segment(after, len(before) + len(removed_text)),
    ]
    _wire_stage0_workpack(
        monkeypatch, tmp_path, workpack, text=original, segments=segments)

    payload = workpack.build_workpack(tmp_path, '42')

    assert payload['route'] == 'removed_segments'
    assert payload['counts']['removed_segments'] == 1
    assert payload['removed'][0]['text'] == removed_text
    assert payload['removed'][0]['span'] == [len(before), len(before + removed_text)]
    assert payload['removed'][0]['before'].endswith(before)
    assert payload['removed'][0]['after'].startswith(after)
    assert payload['reverse_scan']['total_hits'] == 0, \
        '反向模式必须扫描 clean_text，不能把已经剔除的“求收藏”报成残留'
    assert payload['automated_decision'] is None
    assert '逐段语义类别与边界=待确认' in payload['approval_reason_draft']



def test_stage0_workpack_leaves_out_of_order_chapter_markers_to_stage1(
        monkeypatch, tmp_path):
    workpack = _operator_script('stage0_review_workpack')
    text = '1\n第一章。\n3\n第三章。\n2\n第二章。\n'
    _wire_stage0_workpack(
        monkeypatch, tmp_path, workpack, text=text,
        segments=[_stage0_segment(text, 0)])
    payload = workpack.build_workpack(tmp_path, '42')

    assert 'chapters' not in payload
    assert payload['structure_denominator']['chapter_numbers_in_source_order'] == [1, 3, 2]
    assert payload['structure_denominator']['order_or_continuity_decision'] is None
    assert '章序' not in payload['approval_reason_draft']
    assert payload['boundaries']['first_nonempty_lines'][0] == '1'
    assert payload['boundaries']['last_nonempty_lines'][-1] == '第二章。'



def test_stage0_workpack_carries_numeric_chapter_body_that_regex_cannot_classify(
        monkeypatch, tmp_path):
    """纯数字章号下的番外语义没有结构或词表信号；只有全文可供人读。"""
    workpack = _operator_script('stage0_review_workpack')
    text = (
        '1\n她们在大雨里等到了彼此。\n'
        '2\n多年后，另一个人讲起两人离开主线后的生活。\n')
    _wire_stage0_workpack(
        monkeypatch, tmp_path, workpack, text=text,
        segments=[_stage0_segment(text, 0)])

    payload = workpack.build_workpack(tmp_path, '42')

    assert payload['reverse_scan']['total_hits'] == 0
    assert payload['production_candidates_not_selected'] == []
    assert payload['retained_review_material']['text'] == text
    assert '另一个人讲起两人离开主线后的生活' in \
        payload['retained_review_material']['text']
    assert payload['automated_decision'] is None



def test_stage0_workpack_reports_no_chapter_marker_without_treating_the_book_as_preface(
        monkeypatch, tmp_path):
    workpack = _operator_script('stage0_review_workpack')
    text = '正文第一句。\n\n正文收束句。\n'
    _wire_stage0_workpack(
        monkeypatch, tmp_path, workpack, text=text,
        segments=[_stage0_segment(text, 0)])

    payload = workpack.build_workpack(tmp_path, '42')

    denominator = payload['structure_denominator']
    assert denominator['chapter_marker_count'] == 0
    assert denominator['chapter_numbers_in_source_order'] == []
    assert denominator['before_first_chapter_marker'] == {
        'state': 'no_chapter_marker',
        'nonempty_line_count': None,
        'nonempty_lines': [],
        'human_classification': None,
        'expected_role': None,
        'review_rule': (
            '生产词法未识别到章号行，不能把整本书自动解释成开篇文案；'
            '正式章结构仍交 Stage1 裁决。'),
    }
    assert '首章前文案区=不适用（生产词法未识别到章号行）' in \
        payload['approval_reason_draft']
    assert '文案区定性=待确认' not in payload['approval_reason_draft']



def test_stage0_workpack_reports_empty_prefix_as_missing_required_hook(
        monkeypatch, tmp_path):
    workpack = _operator_script('stage0_review_workpack')
    text = '1\n第一章。\n'
    _wire_stage0_workpack(
        monkeypatch, tmp_path, workpack, text=text,
        segments=[_stage0_segment(text, 0)])

    payload = workpack.build_workpack(tmp_path, '42')

    assert payload['structure_denominator']['before_first_chapter_marker'] == {
        'state': 'available',
        'nonempty_line_count': 0,
        'nonempty_lines': [],
        'human_classification': None,
        'expected_role': 'required_opening_copy',
        'review_rule': (
            '叙事性概述、提前呈现后文情节或对白、与正文形成重复钩子均是合法'
            '开篇文案体裁；不得仅因不是第一章正文或与后文重复而剔除。只把'
            '书名/作者署名、平台网址、站内导航、更新订阅/票赏推广、作者站务元话语等'
            '不属于作品叙事文案的站务或元数据残留视为问题。'),
    }
    assert '首个章号前非空行=0（缺少 R-1.12 必填开篇文案' in \
        payload['approval_reason_draft']
    assert '不得把空分母当作可批准' in payload['approval_reason_draft']
    assert '文案区定性=待确认' not in payload['approval_reason_draft']



def test_stage0_workpack_source_unavailable_stops_instead_of_faking_an_empty_review(
        monkeypatch, tmp_path):
    workpack = _operator_script('stage0_review_workpack')
    _wire_stage0_workpack(
        monkeypatch, tmp_path, workpack, text='正文', segments=[],
        available=False, unavailable_reason='原始文件已移走')

    payload = workpack.build_workpack(tmp_path, '42')

    assert payload['state'] == 'stopped'
    assert payload['route'] == 'source_unavailable'
    assert payload['source_unavailable_reason'] == '原始文件已移走'
    assert payload['automated_decision'] is None
    assert '不得批准' in payload['approval_reason_draft']



def test_stage0_workpack_human_edit_all_keep_cannot_masquerade_as_zero_removal(
        monkeypatch, tmp_path):
    workpack = _operator_script('stage0_review_workpack')
    original = '1\n第一段正文。\n2\n第二段正文。\n'
    edited_clean = '1\n第一段正文。\n'
    _wire_stage0_workpack(
        monkeypatch, tmp_path, workpack, text=original,
        segments=[_stage0_segment(edited_clean, 0)],
        revision_origin='human_edit',
    )

    payload = workpack.build_workpack(tmp_path, '42')

    assert payload['state'] == 'stopped'
    assert payload['route'] == 'human_edit_partition_unproven'
    assert payload['current_clean_code_points'] == len(edited_clean)
    assert payload['source_code_points'] == len(original)
    assert payload['automated_decision'] is None
    assert '不得按零剔除批准' in payload['approval_reason_draft']



def test_runner_keeps_keyboard_interrupt_control_semantics(monkeypatch):
    runner = _operator_script('run_step')

    def interrupt(_argv):
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, '_main', interrupt)
    with pytest.raises(KeyboardInterrupt):
        runner.main([])



def test_stage0_workpack_main_returns_stopped_json_for_an_expected_pipeline_terminal(
        monkeypatch, capsys, tmp_path):
    workpack = _operator_script('stage0_review_workpack')
    monkeypatch.setattr(
        workpack.paths, 'resolve_novel_dir',
        lambda _base, _sid: tmp_path / 'book',
    )
    monkeypatch.setattr(
        workpack, 'stage0_report',
        lambda *_args: (_ for _ in ()).throw(UpstreamMissing(
            'stage0 还没跑', hint='先运行 stage0.remove')),
    )

    rc = workpack.main(['--base', str(tmp_path), '--sid', '42'])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload['state'] == 'stopped'
    assert payload['error'] == 'UpstreamMissing: stage0 还没跑'
    assert payload['hint'] == '先运行 stage0.remove'
    assert payload['automated_decision'] is None



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
