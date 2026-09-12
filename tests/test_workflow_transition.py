"""离线 manifest 推进走真实 Core、来源子进程、CLI 与文件系统恢复；不调用真实模型。"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from contracts.schema.api import Stage2NodeReviewRequest
from contracts.schema.workflow import WorkflowManifestV4, WorkflowManifestV5
from novel import paths, prov, work_units
from novel.cli import main as cli
from novel.errors import TerminalState
from novel.orchestrator import admission, approvals
from novel.orchestrator import workflow_transition as transition
from novel.orchestrator.state import readonly_ctx
from novel.stages.stage2 import global_review
from tests.test_stage2_core_edits import seed


REPO = Path(__file__).resolve().parents[1]
CODE = 'a' * 40


@pytest.fixture(scope='module')
def frozen_core(tmp_path_factory):
    """只复用不可变上游；每例复制字节到独立输出根，迁移步骤从不共享结果。"""
    base = tmp_path_factory.mktemp('transition-core')
    with pytest.MonkeyPatch.context() as identity:
        identity.setattr(prov, 'code_version', lambda: CODE)
        ctx = seed(base)
        from tests.test_stage2_generation import _accept_fixture_global
        _accept_fixture_global(ctx)
        pack = global_review.review_workpack(ctx)
        approvals.approve_stage2_review(base, ctx.sid, Stage2NodeReviewRequest(
            node='stage2.global', review_token=pack['review_token'], decision='accept',
            actor='codex', reason='工程核对合成源文后接受 Core',
            review={'scope_sha256': pack['review_coverage']['scope']['scope_sha256'],
                    'default_decision': 'accept', 'exceptions': []}))
        global_review.require_global_accepted(ctx)
        original = work_units.require_workflow(base)
        source = WorkflowManifestV4(task_id=original.task_id, created_at=original.created_at)
        work_units._write(base, paths.workflow_manifest_path(base), source)
        projection_path = ctx.novel_dir / 'stage2/blueprint_character_projection.json'
        projection = json.loads(projection_path.read_bytes())
        for chapter in projection['chapters']:
            chapter.pop('character_bindings')
        projection_path.write_bytes(work_units._bytes(projection))
    # 实际 owner open → close → drained，而不是伪造 stopped 快照。
    with admission.owner_lease(base, 'old-parent'):
        admission.open_gate(base, 'old-parent', ctx.sid)
        admission.close_gate(base, 'old-parent')
        admission.mark_drained(base, 'old-parent')
    # profiles 位于服务运行资料；迁移必须逐字保留其嵌套内容和费用账。
    profile = paths.service_dir(base) / 'original_profiles.json'
    profile.write_text(json.dumps({'executor': {'driver': 'claude-code-glm',
        'model': 'glm-5.3-flash[1M]', 'reasoning_effort': 'high'},
        'commander': {'driver': 'claude-code-glm', 'model': 'glm-5.3-flash[1M]',
                      'reasoning_effort': 'high'}}, ensure_ascii=False), encoding='utf-8')
    return base, ctx.sid, source


@pytest.fixture
def setup(tmp_path, monkeypatch, frozen_core):
    original, sid, source = frozen_core
    base = tmp_path / '输出'
    shutil.copytree(original, base)
    monkeypatch.setattr(prov, 'code_version', lambda: CODE)
    # 工作树有本批并行改动；仅代码 clean checkout 证明替换。来源 Core/生命周期
    # 证明仍由真实子进程读取完整原字节，不伪造接受或停止结论。
    monkeypatch.setattr(transition, '_code_identity', lambda root: CODE)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    return dict(base=base, source_code=REPO, service_ports=(port,), stopped_pids=(),
                actor='codex', reason='Gate A 完成后进入 Actor 契约', offline_ack=True)


def _apply(setup, plan, *, recovery=False):
    return (transition.recover if recovery else transition.apply)(base=setup['base'],
        transition_id=plan['transition_id'], expected_prepared_sha256=plan['prepared_sha256'], yes=True)


def _record(setup, plan, name):
    return paths.workflow_transition_dir(setup['base'], plan['transition_id']) / name


@pytest.mark.parametrize('window', ['none', 'before_projection', 'after_projection',
                                  'before_cas', 'after_cas', 'before_completed', 'after_completed'])
def test_exact_manifest_transition_and_crash_recovery_preserve_task_budget_and_all_sources(
        setup, frozen_core, monkeypatch, window):
    base = setup['base']
    before = transition._inventory(base)
    source_raw = paths.workflow_manifest_path(base).read_bytes()
    plan = transition.preview(**setup)
    assert transition._inventory(base) == before
    assert paths.workflow_manifest_path(base).read_bytes() == source_raw
    prepared, digest = transition._prepared(base, plan['transition_id'])
    assert digest == plan['prepared_sha256']
    assert prepared.source_proof.counts['admissions'] == 1
    assert set(prepared.source_proof.accepted_books) == {frozen_core[1]}
    with pytest.raises(TerminalState):
        work_units.require_workflow(base)
    if window != 'none':
        with monkeypatch.context() as crash:
            if window in {'before_projection', 'after_projection'}:
                from novel.store import ArtifactStore
                original = ArtifactStore.apply_workflow_projection_transition
                def interrupted_projection(store, *args):
                    if window == 'after_projection':
                        original(store, *args)
                    raise OSError('模拟投影提交中断')
                crash.setattr(ArtifactStore, 'apply_workflow_projection_transition', interrupted_projection)
            elif window in {'before_cas', 'after_cas'}:
                original = transition._cas_manifest
                def interrupted_cas(base, value):
                    if window == 'after_cas':
                        original(base, value)
                    raise OSError('模拟 manifest 提交中断')
                crash.setattr(transition, '_cas_manifest', interrupted_cas)
            else:
                original = transition._immutable
                def interrupted_completed(base, path, value):
                    if path.name != 'completed.json' or window == 'after_completed':
                        original(base, path, value)
                    if path.name == 'completed.json':
                        raise OSError('模拟回执提交中断')
                crash.setattr(transition, '_immutable', interrupted_completed)
            with pytest.raises(OSError, match='模拟'):
                _apply(setup, plan)
        if window != 'after_completed':
            with pytest.raises(TerminalState):
                work_units.require_workflow(base)
    result = _apply(setup, plan, recovery=True)
    assert result['state'] == 'completed'
    target = work_units.require_workflow(base)
    assert isinstance(target, WorkflowManifestV5)
    assert (target.task_id, target.created_at) == (frozen_core[2].task_id, frozen_core[2].created_at)
    assert paths.workflow_manifest_path(base).read_bytes() == prepared.target_manifest_json.encode('utf-8')
    assert target.transition_ref.source_manifest_sha256 == transition._sha(source_raw)
    transition._preserved_unchanged(base, prepared)
    after = transition._inventory(base)
    assert {key for key in before if before[key] != after[key]} == {
        row.relative_path for row in prepared.projections.values()}
    for row in prepared.projections.values():
        assert (base / row.relative_path).read_bytes() == row.target_json.encode('utf-8')
        assert all(chapter['character_bindings'] for chapter in json.loads(row.target_json)['chapters'])
        assert row.source_json is not None
        assert all('character_bindings' not in chapter for chapter in json.loads(row.source_json)['chapters'])
    registry = work_units._read(base, paths.work_unit_registry_path(base, frozen_core[1]),
                               work_units.WorkUnitRegistryV1)
    assert registry.unit_ids
    for unit_id in registry.unit_ids:
        budget = work_units.readonly_budget(base, frozen_core[1], unit_id)
        ledger = budget._read()
        assert budget.identity['task_id'] == frozen_core[2].task_id
        assert ledger.base_content_limit == 4 and len(ledger.turns) > 0
        assert ledger.content_limit - len(ledger.turns) < 4
    assert _apply(setup, plan) == result


def test_completed_replay_does_not_reopen_or_delete_new_downstream(setup):
    plan = transition.preview(**setup)
    result = _apply(setup, plan)
    book = next(iter(paths.iter_novel_dirs(setup['base'])))
    target = book / 'stage4' / 'blind_candidate.json'
    target.parent.mkdir()
    target.write_bytes(b'new downstream after completed')
    after = transition._inventory(setup['base'])
    assert _apply(setup, plan, recovery=True) == result
    assert transition._inventory(setup['base']) == after


@pytest.mark.parametrize('drift', ['manifest', 'projection', 'ledger', 'profile', 'prepared', 'wrong_sha', 'pending', 'missing_manifest'])
def test_apply_refuses_any_third_state_without_overwriting_sources(setup, drift):
    base = setup['base']
    plan = transition.preview(**setup)
    if drift in {'manifest', 'projection'}:
        path = (paths.workflow_manifest_path(base) if drift == 'manifest' else
                next(iter(paths.iter_novel_dirs(base))) / 'stage2/blueprint_character_projection.json')
        path.write_bytes(path.read_bytes() + b' ')
    elif drift == 'missing_manifest':
        paths.workflow_manifest_path(base).unlink()
    elif drift in {'ledger', 'profile'}:
        path = paths.service_dir(base) / ('ledger.jsonl' if drift == 'ledger' else 'original_profiles.json')
        path.write_bytes(path.read_bytes() + b' ')
    elif drift == 'prepared':
        _record(setup, plan, 'prepared.json').write_bytes(b'{}')
    elif drift == 'pending':
        path = paths.workflow_transition_pending_path(base)
        raw = json.loads(path.read_bytes())
        raw['transition_id'] = 'f' * 64
        path.write_bytes(work_units._bytes(raw))
    else:
        plan['prepared_sha256'] = 'f' * 64
    before = {str(p.relative_to(base)): p.read_bytes() for p in base.rglob('*') if p.is_file()}
    with pytest.raises(TerminalState):
        _apply(setup, plan)
    assert {str(p.relative_to(base)): p.read_bytes() for p in base.rglob('*') if p.is_file()} == before
    assert not _record(setup, plan, 'completed.json').exists()


@pytest.mark.parametrize('relative', [
    'stage2/chapter_blueprints.json', 'stage2/chapter_blueprints.INVALID.json',
    'stage2/_rounds/chapter_blueprints.r1.json', 'stage2/knowledge_links.json',
    'stage4/empty/', '_stage4_runs/old/checkpoints/1.json',
    '_service/runs/2042/stage2/old/blueprint/checkpoints/1.json',
    '_service/runs/2042/stage2/old/blueprint/prepared.json',
    '_service/runs/2042/stage2/old/blueprint/response.txt',
    '_service/runs/2042/stage4/old/response.txt',
])
def test_downstream_formal_invalid_history_checkpoint_and_paid_response_all_block(setup, relative):
    base = setup['base']
    book = next(iter(paths.iter_novel_dirs(base)))
    path = (base if relative.startswith('_service/') else book) / relative
    if relative.endswith('/'):
        path.mkdir(parents=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'{}')
    before = transition._inventory(base)
    with pytest.raises(TerminalState, match='下游'):
        transition.preview(**setup)
    assert transition._inventory(base) == before
    assert not paths.workflow_transition_pending_path(base).exists()


def test_source_reader_rejects_missing_paid_registry_even_without_formal_downstream(setup):
    base = setup['base']
    orphan = paths.service_runs_dir(base) / 'unknown' / 'work_units' / ('f' * 64) / 'response.txt'
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b'paid response without owner registry')
    with pytest.raises(TerminalState, match='来源代码不能证明'):
        transition.preview(**setup)


def test_downstream_work_unit_node_blocks_without_any_downstream_artifact(setup):
    base = setup['base']
    path = next(paths.work_units_dir(base, '2042').glob('*/ledger.json'))
    raw = json.loads(path.read_bytes())
    raw['node'] = 'stage2.blueprint'
    path.write_bytes(work_units._bytes(raw))
    with pytest.raises(TerminalState, match='下游'):
        transition.preview(**setup)


def test_preview_interrupted_before_pointer_recovers_same_immutable_prepared(setup, monkeypatch):
    original = transition._immutable
    with monkeypatch.context() as crash:
        def fail(base, path, value):
            if path == paths.workflow_transition_pending_path(base):
                raise OSError('模拟 pointer 尚未发布')
            original(base, path, value)
        crash.setattr(transition, '_immutable', fail)
        with pytest.raises(OSError, match='模拟'):
            transition.preview(**setup)
    path = next(paths.workflow_transition_dir(setup['base'], '0' * 64).parent.glob('*/prepared.json'))
    value, digest = transition._prepared(setup['base'], path.parent.name)
    assert not paths.workflow_transition_pending_path(setup['base']).exists()
    with pytest.raises(TerminalState, match='不能另建迁移'):
        transition.preview(**setup)
    plan = {'transition_id': value.transition_id, 'prepared_sha256': digest}
    assert _apply(setup, plan, recovery=True)['state'] == 'completed'


def test_missing_derived_projection_is_built_from_actual_characters(setup):
    path = next(iter(paths.iter_novel_dirs(setup['base']))) / 'stage2/blueprint_character_projection.json'
    path.unlink()
    plan = transition.preview(**setup)
    value, _ = transition._prepared(setup['base'], plan['transition_id'])
    assert value.projections['2042'].source_json is None
    _apply(setup, plan)
    assert path.read_bytes() == value.projections['2042'].target_json.encode('utf-8')


def test_projection_store_exception_cannot_bypass_offline_apply(setup):
    from novel.store import ArtifactStore
    plan = transition.preview(**setup)
    store = ArtifactStore(readonly_ctx(next(iter(paths.iter_novel_dirs(setup['base']))),
                                      'stage2.characters', CODE))
    with pytest.raises(TerminalState, match='只允许已持离线 owner 锁'):
        store.apply_workflow_projection_transition(plan['transition_id'], plan['prepared_sha256'])
    assert _apply(setup, plan)['state'] == 'completed'


@pytest.mark.parametrize('fault', ['uncommitted_tail', 'unclosed_admission', 'orphan_event', 'pending_review', 'bool_only'])
def test_source_proof_never_treats_unknown_or_old_gate_bool_as_accepted_and_drained(setup, fault):
    base = setup['base']
    if fault == 'uncommitted_tail':
        with admission.admission_path(base).open('ab') as stream:
            stream.write(b'{}')
    elif fault == 'unclosed_admission':
        admission.open_gate(base, 'unclosed-owner', '2042')
    elif fault == 'orphan_event':
        (paths.service_dir(base) / 'jobs.jsonl').write_bytes(b'{}\n')
    else:
        path = approvals._path(base, '2042')
        raw = json.loads(path.read_bytes())
        if fault == 'pending_review':
            raw['stage2_reviews']['stage2.global']['decision'] = 'uncertain'
            raw['stage2_review_history'][-1]['decision'] = 'uncertain'
        else:
            raw['stage2_reviews'] = {}
            raw['stage2_review_history'] = []
            raw['gate']['s2'] = True
        path.write_bytes(work_units._bytes(raw))
    with pytest.raises(TerminalState):
        transition.preview(**setup)
    assert not paths.workflow_transition_pending_path(base).exists()


def test_live_owner_is_rejected_by_real_cross_process_lock(setup):
    script = ('from novel.orchestrator.admission import owner_lease\nimport sys\n'
              'with owner_lease(sys.argv[1], "old-parent"):\n'
              ' print("locked", flush=True)\n sys.stdin.readline()\n')
    process = subprocess.Popen([sys.executable, '-B', '-c', script, str(setup['base'])],
        cwd=REPO, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'locked'
        with pytest.raises(TerminalState, match='锁仍被持有'):
            transition.preview(**setup)
    finally:
        process.communicate('\n', timeout=15)
    assert process.returncode == 0
    assert transition.preview(**setup)['state'] == 'prepared'


def test_live_service_pid_and_port_refuse_before_prepared(setup):
    with pytest.raises(TerminalState, match='仍存活'):
        transition.preview(**{**setup, 'stopped_pids': (os.getpid(),)})
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', setup['service_ports'][0]))
        sock.listen()
        with pytest.raises(TerminalState, match='端口'):
            transition.preview(**setup)
    assert not paths.workflow_transition_pending_path(setup['base']).exists()


def test_prepared_or_target_without_completed_fences_all_ordinary_writes(setup, monkeypatch):
    base = setup['base']
    plan = transition.preview(**setup)
    prepared, _ = transition._prepared(base, plan['transition_id'])
    transition._cas_manifest(base, prepared)
    for operation in (work_units.require_workflow, work_units.check_workflow_write, work_units.initialize_workflow):
        with pytest.raises(TerminalState, match='尚未完成'):
            operation(base)
    _record(setup, plan, 'completed.json').write_bytes(b'{"schema_version":99}')
    with pytest.raises(TerminalState):
        work_units.workflow_manifest(base)


def test_cli_preview_apply_and_completed_recover_are_reachable(setup, capsys):
    base = setup['base']
    assert cli.main(['workflow', 'transition', 'preview', '--base', str(base),
        '--source-code', str(REPO), '--port', str(setup['service_ports'][0]), '--offline-ack',
        '--actor', 'codex', '--reason', '明确推进合成 Gate B']) == 0
    plan = json.loads(capsys.readouterr().out)
    args = ['--base', str(base), '--transition-id', plan['transition_id'],
            '--prepared-sha256', plan['prepared_sha256']]
    assert cli.main(['workflow', 'transition', 'apply', *args]) == 2
    capsys.readouterr()
    assert cli.main(['workflow', 'transition', 'apply', *args, '--yes']) == 0
    applied = json.loads(capsys.readouterr().out)
    assert cli.main(['workflow', 'transition', 'recover', *args, '--yes']) == 0
    assert json.loads(capsys.readouterr().out) == applied


def test_ordinary_workflow_rejects_v4_and_initializes_only_clean_v5(setup, tmp_path):
    raw = paths.workflow_manifest_path(setup['base']).read_bytes()
    with pytest.raises(TerminalState):
        work_units.require_workflow(setup['base'])
    assert paths.workflow_manifest_path(setup['base']).read_bytes() == raw
    current = work_units.initialize_workflow(tmp_path / 'clean')
    assert current.schema_version == 5 and current.transition_ref is None
    assert current.artifact_versions.chapter_blueprints == 10
    assert current.artifact_versions.writer_context == 9


def test_unreadable_subtree_and_hardlink_never_become_complete_inventory(setup, monkeypatch):
    original = os.scandir
    blocked = paths.service_dir(setup['base'])
    with monkeypatch.context() as denied:
        def fail(path):
            if Path(path) == blocked:
                raise PermissionError('模拟子目录读取失败')
            return original(path)
        denied.setattr(transition.os, 'scandir', fail)
        with pytest.raises(TerminalState, match='无法完整列举'):
            transition.preview(**setup)
    source = paths.service_dir(setup['base']) / 'ledger.jsonl'
    target = setup['base'] / 'ledger-alias'
    os.link(source, target)
    with pytest.raises(TerminalState, match='硬链接'):
        transition.preview(**setup)
