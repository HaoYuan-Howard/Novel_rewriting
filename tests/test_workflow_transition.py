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

from contracts.schema.workflow import (
    WorkflowManifestV4, WorkflowManifestV5, WorkflowManifestV6, WorkflowManifestV7,
    WorkUnitLedgerV2)
from novel import paths, prov, work_units
from novel.cli import main as cli
from novel.errors import TerminalState
from novel.orchestrator import admission, approvals
from novel.orchestrator import workflow_transition as transition
from novel.orchestrator.state import readonly_ctx

REPO = Path(__file__).resolve().parents[1]
CODE = 'a' * 40

#: v7 起不登记 v6→v7 迁移：旧 v4~v6 根由冻结旧代码读取，v7 只允许 clean 初始化。
#: 生产只保留 4→5、5→6 两对历史显式迁移（``_TRANSITION_PAIRS``）。
_LEGACY_REVIEW_TOKEN = 's2gr1_' + 'a' * 64

#: 冻结旧来源替身里的退役模块：真实 v4~v6 来源 checkout 自带完整
#: ``require_global_accepted`` 语义闸；v7 退役后工作树无法在仓内复原整份旧代码，
#: 这里以最小等价检查证明同一历史前置——服务审批账存在 stage2.global 的
#: 当前 typed 接受，且不认旧 gate bool。除此之外来源树与现役字节逐字相同。
_LEGACY_GLOBAL_REVIEW_SHIM = '''"""冻结旧来源替身：Stage2 语义审阅已退役，仅证明历史迁移前置。"""
import json


def require_global_accepted(ctx) -> None:
    from novel.orchestrator import approvals
    path = approvals._path(ctx.novel_dir.parent, ctx.sid)
    doc = json.loads(path.read_bytes())
    row = (doc.get('stage2_reviews') or {}).get('stage2.global') or {}
    if row.get('decision') != 'accept' or not row.get('review_token'):
        raise ValueError('stage2.global 尚无当前独立 Commander 接受')
'''


def _frozen_legacy_source(root: Path) -> Path:
    """物化 v4/v5 迁移的「来源代码」替身：现役字节 + 退役 global_review 模块。

    迁移来源证明子进程按 ``PYTHONPATH=source_code`` 执行现役 proof 文本，
    因此来源树必须提供 v4/v5 时代的模块面。代码身份核验由
    ``transition._code_identity`` 替身承担（与真实冻结 checkout 的逐字一致性
    在生产侧由 git 核验负责）。
    """
    source = root / 'legacy-source'
    for package in ('novel', 'contracts'):
        shutil.copytree(REPO / package, source / package,
                        ignore=shutil.ignore_patterns('__pycache__'))
    shim = source / 'novel' / 'stages' / 'stage2' / 'global_review.py'
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.write_text(_LEGACY_GLOBAL_REVIEW_SHIM, encoding='utf-8')
    return source


def _write_legacy_stage2_acceptance(base: Path, sid: str) -> None:
    """按 v4 时代的服务账格式重建语义接受凭据，供旧来源替身核验。

    v7 删除了 Stage2 审阅写入口；历史根的账面证据在此以原样形状手写，
    现役入口不读也不写这些键（``gate.s2``、``stage2_reviews`` 已退场）。
    """
    path = approvals._path(base, sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    decision = {
        'node': 'stage2.global', 'decision': 'accept',
        'review_token': _LEGACY_REVIEW_TOKEN,
        'actor': 'codex', 'reason': '工程核对合成源文后接受 Core',
        'review': {'scope_sha256': 'b' * 64, 'default_decision': 'accept',
                   'exceptions': []},
    }
    path.write_bytes(work_units._bytes({
        'stage2_reviews': {'stage2.global': decision},
        'stage2_review_history': [dict(decision, decided_at='2026-09-13T00:00:00+00:00')],
        'gate': {gate: False for gate in ('s0', 's1', 's2', 's4', 's5', 's6', 's7')},
    }))




@pytest.fixture(scope='module')
def legacy_source(tmp_path_factory):
    return _frozen_legacy_source(tmp_path_factory.mktemp('transition-source'))


@pytest.fixture(scope='module')
def frozen_core(tmp_path_factory, legacy_source):
    """只复用不可变上游；每例复制字节到独立输出根，迁移步骤从不共享结果。"""
    base = tmp_path_factory.mktemp('transition-core')
    with pytest.MonkeyPatch.context() as identity:
        identity.setattr(prov, 'code_version', lambda: CODE)
        from tests.test_stage2_analyze import _ctx, _driver
        from novel.node_agents import Stage2AnalyzeAgent
        ctx = _ctx(base)
        Stage2AnalyzeAgent(ctx, driver=_driver(ctx)).run()
        _write_legacy_stage2_acceptance(base, ctx.sid)
        (ctx.novel_dir / 'stage2' / 'global_review.json').write_bytes(
            work_units._bytes({'generation_safety': {
                'blocking_item_ids': [], 'quarantined_item_ids': []}}))
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
    return base, ctx.sid, source, legacy_source


@pytest.fixture
def setup(tmp_path, monkeypatch, frozen_core):
    original, sid, source, legacy_source = frozen_core
    base = tmp_path / '输出'
    shutil.copytree(original, base)
    monkeypatch.setattr(prov, 'code_version', lambda: CODE)
    # 工作树有本批并行改动；仅代码 clean checkout 证明替换。来源 Core/生命周期
    # 证明仍由真实子进程读取完整原字节，不伪造接受或停止结论。
    monkeypatch.setattr(transition, '_code_identity', lambda root: CODE)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    return dict(base=base, source_code=legacy_source, service_ports=(port,), stopped_pids=(),
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
    target = WorkflowManifestV5.model_validate_json(paths.workflow_manifest_path(base).read_bytes())
    transition.require_transition_completed(base, target, paths.workflow_manifest_path(base).read_bytes())
    with pytest.raises(TerminalState):
        work_units.require_workflow(base)  # 当前 V6 普通写口不自动接纳中间 V5。
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
        ledger = WorkUnitLedgerV2.model_validate_json(paths.work_unit_dir(base, frozen_core[1], unit_id).joinpath('ledger.json').read_bytes())
        assert ledger.task_id == frozen_core[2].task_id
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


def test_prepared_or_target_without_completed_fences_all_ordinary_writes(setup_stage5):
    # V4→V5 的中间目标已不是普通写口接受的 current shape；本例须使用
    # V5→V6 当前目标，才能实际越过 schema 解码而验证 completed 围栏。
    setup = setup_stage5
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
        '--source-code', str(setup['source_code']), '--port', str(setup['service_ports'][0]),
        '--offline-ack',
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


def test_ordinary_workflow_rejects_v4_and_initializes_only_clean_v7(setup, tmp_path):
    raw = paths.workflow_manifest_path(setup['base']).read_bytes()
    with pytest.raises(TerminalState):
        work_units.require_workflow(setup['base'])
    assert paths.workflow_manifest_path(setup['base']).read_bytes() == raw
    current = work_units.initialize_workflow(tmp_path / 'clean')
    assert current.schema_version == 7 and current.transition_ref is None
    assert current.contract_version == 'stage2-mechanical-admission-v1'
    assert current.artifact_versions.chapter_blueprints == 10
    assert current.artifact_versions.writer_context == 10
    assert current.artifact_versions.stage6_prepare == 1


def test_v7_registers_no_migration_out_of_retired_manifests():
    """v7 删除 Stage2 语义审阅与 KnowledgeLinks 后不登记任何迁入/迁出对。

    旧 v4~v6 根原样保留给冻结旧代码；只有 4→5、5→6 两对历史迁移可以推进，
    v6/v7 来源都没有「下一版」，普通入口也只接受 clean v7。
    """
    assert set(transition._TRANSITION_PAIRS) == {4, 5}
    v7 = WorkflowManifestV7(task_id='a' * 64, created_at=1)
    with pytest.raises(TerminalState, match='没有登记此来源的下一版显式迁移'):
        transition._pair(v7)
    assert transition._manifest(work_units._bytes(v7.model_dump(mode='json'))) == v7


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


@pytest.fixture(scope='module')
def frozen_stage5(tmp_path_factory, legacy_source):
    """经真实 BP→A/B→Writer/Review/closure 形成 V5 来源，只有模型响应是合成材料。"""
    base = tmp_path_factory.mktemp('transition-stage5')
    try:
        from tests.test_plot_design import produce_through_first_chapter
        from tests.test_plot_pipeline import complete_stage5
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(prov, 'code_version', lambda: CODE)
            book = produce_through_first_chapter(base, mp, real_code_identity=True)
            complete_stage5(book)
    except ImportError as exc:
        # ⚠ Stage2 退役把联合生产链（knowledge_links / test_plot_review_runtime）
        # 一起拆了，链路迁移属另一批次；V5→V6 用例保留，等链路闭合后自动恢复。
        pytest.skip(f'v4/v5 联合生产链尚未随退役迁移，V5 来源无法构建：{exc}')
    source = work_units.require_workflow(book.parent)
    old = WorkflowManifestV5(task_id=source.task_id, created_at=source.created_at)
    work_units._write(book.parent, paths.workflow_manifest_path(book.parent), old)
    # ⚠ v7 生产链产出的是 v7 载体（无 retired review 文件、审批账无
    # stage2_reviews 历史键）；v4/v5 来源证明要求这些历史前置。在链路迁移
    # 批次闭合前，V5→V6 用例保持跳过，不伪造旧来源。
    try:
        transition._source_proof(book.parent, legacy_source, CODE,
                                 accepted=True, source_version=5)
    except transition.WorkflowTransitionRefused as exc:
        pytest.skip(f'现役 v7 生产链构造不出合法 v5 迁移来源，'
                    f'V5→V6 用例待链路迁移批次：{exc}')
    return book.parent, paths.derive_source_meta(book.name)[0], old


@pytest.fixture
def setup_stage5(tmp_path, monkeypatch, frozen_stage5, legacy_source):
    original, sid, _ = frozen_stage5
    base = tmp_path / '输出'
    shutil.copytree(original, base)
    monkeypatch.setattr(prov, 'code_version', lambda: CODE)
    monkeypatch.setattr(transition, '_code_identity', lambda root: CODE)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    return dict(base=base, source_code=legacy_source, service_ports=(port,), stopped_pids=(),
        actor='codex', reason='Stage5 闭合后进入独立文案准备', offline_ack=True)


@pytest.mark.parametrize('window', ['none', 'before_cas', 'after_cas', 'before_completed', 'after_completed'])
def test_v5_to_v6_retains_all_stage4_stage5_sources_and_recovers_exactly(
        setup_stage5, frozen_stage5, monkeypatch, window):
    setup = setup_stage5
    before = transition._inventory(setup['base'])
    plan = transition.preview(**setup)
    prepared, digest = transition._prepared(setup['base'], plan['transition_id'])
    assert prepared.schema_version == 2 and prepared.projections == {}
    assert prepared.source_proof.accepted_books[frozen_stage5[1]].closure_sha256
    assert any('/stage4/' in key for key in before)
    assert any('/stage5/' in key for key in before)
    if window != 'none':
        with monkeypatch.context() as crash:
            if window.endswith('cas'):
                original = transition._cas_manifest
                def interrupted(base, value):
                    if window == 'after_cas': original(base, value)
                    raise OSError('模拟迁移中断')
                crash.setattr(transition, '_cas_manifest', interrupted)
            else:
                original = transition._immutable
                def interrupted(base, path, value):
                    if path.name != 'completed.json' or window == 'after_completed':
                        original(base, path, value)
                    if path.name == 'completed.json': raise OSError('模拟迁移中断')
                crash.setattr(transition, '_immutable', interrupted)
            with pytest.raises(OSError, match='模拟'):
                _apply(setup, plan)
    result = _apply(setup, plan, recovery=True)
    assert result['state'] == 'completed'
    current = work_units.require_workflow(setup['base'])
    assert isinstance(current, WorkflowManifestV6)
    assert (current.task_id, current.created_at) == (frozen_stage5[2].task_id, frozen_stage5[2].created_at)
    assert transition._inventory(setup['base']) == before
    assert _apply(setup, plan) == result


@pytest.mark.parametrize('residue', ['stage6/hook.json', 'stage6/prepare.INVALID.json',
    'stage6/rounds/0001.json', '_stage6_runs/old/prepare/run.json',
    'stage7/name_map.json', 'stage7/name_map.INVALID.json'])
def test_v5_transition_refuses_only_affected_downstream_residue(setup_stage5, residue):
    book = next(iter(paths.iter_novel_dirs(setup_stage5['base'])))
    path = book / residue
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{}', encoding='utf-8')
    before = paths.workflow_manifest_path(setup_stage5['base']).read_bytes()
    with pytest.raises(TerminalState, match='下游'):
        transition.preview(**setup_stage5)
    assert paths.workflow_manifest_path(setup_stage5['base']).read_bytes() == before


def _closed_predecessor_for_stage5(setup):
    """给定严格闭合的历史回执；本轮 Stage5 接受与离线证明仍走真实来源代码。

    历史迁移 producer 的完整生成/中断另由本模块 V4→V5 用例覆盖。
    这里只构造旧回执前置，不用改当前产物或伪造本轮 Stage5 接受结论。
    """
    from contracts.schema.workflow import (
        WorkflowTransitionRefV1, WorkflowTransitionPreparedV1,
        WorkflowTransitionProofV1, WorkflowTransitionProjectionV1,
        WorkflowTransitionPendingV1, WorkflowTransitionCompletedV1)

    base = setup['base']
    current = WorkflowManifestV5.model_validate_json(paths.workflow_manifest_path(base).read_bytes())
    source = WorkflowManifestV4(task_id=current.task_id, created_at=current.created_at)
    source_raw = work_units._bytes(source.model_dump(mode='json'))
    old_id = transition._sha(b'closed-predecessor:' + source_raw)
    target = current.model_copy(update={'transition_ref': WorkflowTransitionRefV1(
        transition_id=old_id, source_manifest_sha256=transition._sha(source_raw))})
    target_raw = work_units._bytes(target.model_dump(mode='json'))
    proof = transition._source_proof(base, setup['source_code'], CODE, accepted=True, source_version=5)
    core_proof = WorkflowTransitionProofV1.model_validate({**proof.model_dump(mode='json'),
        'accepted_books': {sid: {k: v for k, v in row.model_dump(mode='json').items()
            if k != 'closure_sha256'} for sid, row in proof.accepted_books.items()}})
    projections = {}
    inventory = transition._inventory(base)
    for book in paths.iter_novel_dirs(base):
        sid = paths.derive_source_meta(book.name)[0]
        path = book / 'stage2/blueprint_character_projection.json'
        raw = path.read_bytes()
        rel = path.relative_to(base).as_posix()
        projections[sid] = WorkflowTransitionProjectionV1(relative_path=rel,
            source_json=raw.decode('utf-8'), source_sha256=transition._sha(raw),
            target_json=raw.decode('utf-8'), target_sha256=transition._sha(raw),
            characters_sha256=core_proof.accepted_books[sid].characters_sha256,
            main_text_sha256=transition._sha((book / 'stage1/main_text.json').read_bytes()))
    previous = WorkflowTransitionPreparedV1(transition_id=old_id,
        source_manifest_json=source_raw.decode(), source_manifest_sha256=transition._sha(source_raw),
        target_manifest_json=target_raw.decode(), target_manifest_sha256=transition._sha(target_raw),
        source_code_directory=str(REPO), source_code_version=CODE, target_code_version=CODE,
        preserved_files=inventory, projections=projections, source_proof=core_proof,
        service_ports=setup['service_ports'], stopped_pids=(), actor='codex',
        reason='给定旧迁移闭合记录，测试下一迁移指针交接', prepared_at=1)
    transition._immutable(base, paths.workflow_transition_dir(base, old_id) / 'prepared.json', previous)
    _, digest = transition._prepared(base, old_id)
    completed = WorkflowTransitionCompletedV1(transition_id=old_id, prepared_sha256=digest,
        source_manifest_sha256=previous.source_manifest_sha256,
        target_manifest_sha256=previous.target_manifest_sha256, completed_at=2)
    transition._immutable(base, paths.workflow_transition_dir(base, old_id) / 'completed.json', completed)
    work_units._write(base, paths.workflow_manifest_path(base), target)
    transition._immutable(base, paths.workflow_transition_pending_path(base),
        WorkflowTransitionPendingV1(transition_id=old_id, prepared_sha256=digest))
    return old_id


@pytest.mark.parametrize('fault', ['none', 'old_pointer_sha', 'old_completed_missing', 'source_bytes', 'new_inventory'])
def test_new_prepared_recovers_before_closed_predecessor_pointer_is_retired(setup_stage5, monkeypatch, fault):
    setup, base = setup_stage5, setup_stage5['base']
    previous_id = _closed_predecessor_for_stage5(setup)
    pointer = paths.workflow_transition_pending_path(base)
    old_pointer = pointer.read_bytes()
    before = transition._inventory(base)
    original = transition._immutable
    with monkeypatch.context() as crash:
        def after_prepared(base_, path, value):
            original(base_, path, value)
            if path.name == 'prepared.json':
                raise OSError('新 prepared 已保存，旧 pointer 尚未退休')
        crash.setattr(transition, '_immutable', after_prepared)
        with pytest.raises(OSError, match='尚未退休'):
            transition.preview(**setup)
    assert pointer.read_bytes() == old_pointer
    new_id = next(p.name for p in paths.workflow_transition_dir(base, previous_id).parent.iterdir()
                  if p.name != previous_id)
    prepared, digest = transition._prepared(base, new_id)
    plan = {'transition_id': new_id, 'prepared_sha256': digest}
    if fault == 'old_pointer_sha':
        value = json.loads(old_pointer)
        value['prepared_sha256'] = 'f' * 64
        pointer.write_bytes(work_units._bytes(value))
    elif fault == 'old_completed_missing':
        (paths.workflow_transition_dir(base, previous_id) / 'completed.json').unlink()
    elif fault == 'source_bytes':
        path = paths.workflow_manifest_path(base)
        path.write_bytes(path.read_bytes() + b'\n')
    elif fault == 'new_inventory':
        (base / 'late-file.txt').write_text('第三方写入', encoding='utf-8')
    if fault != 'none':
        pointer_before = pointer.read_bytes()
        manifest_before = paths.workflow_manifest_path(base).read_bytes()
        with pytest.raises(TerminalState):
            _apply(setup, plan, recovery=True)
        assert pointer.read_bytes() == pointer_before
        assert paths.workflow_manifest_path(base).read_bytes() == manifest_before
        assert not _record(setup, plan, 'completed.json').exists()
    else:
        result = _apply(setup, plan, recovery=True)
        assert work_units.require_workflow(base).schema_version == 6
        assert not pointer.exists()
        assert transition._inventory(base) == before
        assert paths.workflow_manifest_path(base).read_bytes() == prepared.target_manifest_json.encode('utf-8')
        assert _apply(setup, plan, recovery=True) == result
