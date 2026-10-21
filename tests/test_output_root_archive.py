# -*- coding: utf-8 -*-
"""离线整根 preview/apply 经真实文件系统、旧代码停止证明和最终消费者复验。"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from novel import paths
from novel.api.app import create_app
from novel.cli import main as cli
from novel.errors import OutputRootUnavailable
from novel.orchestrator import admission
from novel.orchestrator import beacon
from contracts.schema.orchestration import ProgressEvent
from novel.output_root import canonical, read_sealed, require_active, seal
from tools import archive_output_root as archive


REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def paired_old_code(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """只读代码快照与各例数据根隔离，停止证明仍由真实配对代码执行。"""
    root = tmp_path_factory.mktemp('archive_paired_code')
    for package in ('novel', 'contracts'):
        shutil.copytree(REPO / package, root / package,
            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'))
    return root


@pytest.fixture
def setup(tmp_path, monkeypatch, paired_old_code):
    source = tmp_path / '旧输出'
    (source / '_service' / 'uploads').mkdir(parents=True)
    (source / '_service' / 'uploads' / 'source.txt').write_text('完整源文与文案', encoding='utf-8')
    (source / '_service' / 'ledger.jsonl').write_bytes(b'{"cost":7}\n')
    (source / '书_123' / 'stage2').mkdir(parents=True)
    (source / '书_123' / 'stage2' / 'global_grounding.json').write_bytes(b'{"retired":true}')
    (source / '空目录').mkdir()
    # 真正建立、关闭、排空旧 owner，留下锁与不可变历史；不伪造 stopped 快照。
    with admission.owner_lease(source, 'old-parent'):
        admission.open_gate(source, 'old-parent', '123')
        admission.close_gate(source, 'old-parent')
        admission.mark_drained(source, 'old-parent')
    # 工程 checkout 正在修改，只有 Git 清洁证明使用独立 seam。生命周期证明
    # 在隔离代码快照中真正启动 admission/beacon/jobs/agent，不读取旧故事产物。
    # 数据根不能位于配对代码内部；仓内固定 .tr 也必须遵守生产路径保护。
    def code_identity(root):
        assert root == paired_old_code
        return 'a' * 40
    monkeypatch.setattr(archive, '_code_identity', code_identity)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    return dict(source=source, archive=tmp_path / '旧根归档', new_root=tmp_path / '新输出',
                journal=tmp_path / '切换事务', old_code=paired_old_code, ports=[port], stopped_pids=[],
                actor='test:release', reason='整根切换合成验收', offline_ack=True)


def _apply(plan):
    return archive.apply(Path(plan['journal']), expected_sha256=plan['sha256'], yes=True)


@pytest.mark.parametrize('window', ['none', 'before_move', 'after_move', 'before_commit', 'after_commit'])
def test_whole_root_transfer_and_crash_replay_keep_all_bytes(setup, monkeypatch, window):
    before = archive._inventory(setup['source'])
    plan = archive.preview(**setup)
    assert archive._inventory(setup['source']) == before
    assert plan['stopped']['admissions'] == 1
    assert not paths.output_root_fence_path(setup['source']).exists()
    if window != 'none':
        with monkeypatch.context() as crash:
            if window in {'before_move', 'after_move'}:
                original = archive._move
                def interrupted_move(source, target):
                    if window == 'after_move':
                        original(source, target)
                    raise OSError('模拟移动窗口中断')
                crash.setattr(archive, '_move', interrupted_move)
            else:
                original = archive._immutable
                def interrupted_commit(path, value):
                    if path.name != 'committed.json' or window == 'after_commit':
                        original(path, value)
                    if path.name == 'committed.json':
                        raise OSError('模拟提交窗口中断')
                crash.setattr(archive, '_immutable', interrupted_commit)
            with pytest.raises(OSError, match='模拟'):
                _apply(plan)
        if window != 'after_commit':
            with pytest.raises(OutputRootUnavailable):
                require_active(setup['new_root'])
    receipt = _apply(plan)
    assert receipt['state'] == 'committed'
    assert not setup['source'].exists()
    assert archive._inventory(setup['archive']) == before
    assert list(setup['new_root'].iterdir()) == []
    assert _apply(plan) == receipt
    for root in (setup['source'], setup['archive']):
        with pytest.raises(OutputRootUnavailable):
            require_active(root)
        assert cli.main(['state', '123', '--base', str(root)]) == 2
        # 已启动的 app 同样逐请求读取围栏；不能从旧服务对象绕过。
        client = TestClient(create_app(root))
        try:
            response = client.get('/api/novels')
            assert response.status_code == 409
        finally:
            client.close()
        with pytest.raises(OutputRootUnavailable):
            with TestClient(create_app(root)):
                pass
    with TestClient(create_app(setup['new_root'])) as restarted:
        assert restarted.get('/api/novels').status_code == 200
    # 正式提交后新生产可能已写入；重复 apply 不能清空或把它冒称当前空根。
    (setup['new_root'] / '生产已开始').write_bytes(b'new')
    assert _apply(plan) == receipt
    assert (setup['new_root'] / '生产已开始').read_bytes() == b'new'


def test_current_root_consumers_reject_missing_or_corrupt_commit(setup):
    plan = archive.preview(**setup)
    archive._fences(plan)
    client = TestClient(create_app(setup['source']))
    try:
        assert client.get('/api/novels').status_code == 409
    finally:
        client.close()
    for raw in (b'broken', b'{}'):
        (setup['journal'] / 'committed.json').write_bytes(raw)
        with pytest.raises(OutputRootUnavailable, match='未知'):
            require_active(setup['new_root'])


@pytest.mark.parametrize('change', [{'role': 'unrecognized'}, {'version': True}])
def test_invalid_root_fence_stays_unknown_after_a_valid_commit(setup, change):
    plan = archive.preview(**setup)
    _apply(plan)
    marker = paths.output_root_fence_path(setup['new_root'])
    previous = read_sealed(marker)
    fields = {key: value for key, value in previous.items() if key != 'sha256'}
    marker.write_bytes(canonical(seal({**fields, **change})))
    with pytest.raises(OutputRootUnavailable, match='未知'):
        require_active(setup['new_root'])


@pytest.mark.parametrize('version', [True, 2])
def test_unknown_preview_version_cannot_authorize_a_new_root_even_with_matching_receipts(setup, version):
    plan = archive.preview(**setup)
    _apply(plan)
    # 各文件摘要相互一致仍不能证明未知计划版本的含义；当前消费者须拒绝。
    changed = seal({**{key: value for key, value in plan.items() if key != 'sha256'},
                    'version': version})
    (setup['journal'] / 'preview.json').write_bytes(canonical(changed))
    (setup['journal'] / 'committed.json').write_bytes(canonical(seal({
        'version': 1, 'state': 'committed', 'plan_sha256': changed['sha256']})))
    marker = paths.output_root_fence_path(setup['new_root'])
    fields = {key: value for key, value in read_sealed(marker).items() if key != 'sha256'}
    marker.write_bytes(canonical(seal({**fields, 'plan_sha256': changed['sha256']})))
    with pytest.raises(OutputRootUnavailable, match='未知'):
        require_active(setup['new_root'])
    with pytest.raises(OutputRootUnavailable, match='未知'):
        with TestClient(create_app(setup['new_root'])):
            pass


@pytest.mark.parametrize('kind', ['source_changed', 'unknown_tail', 'open_admission',
                                 'destination_exists', 'new_root_exists', 'wrong_sha'])
def test_apply_refuses_drift_or_unknown_without_moving(setup, kind):
    plan = archive.preview(**setup)
    if kind == 'source_changed':
        (setup['source'] / '_service' / 'ledger.jsonl').write_bytes(b'changed\n')
    elif kind == 'unknown_tail':
        with admission.admission_path(setup['source']).open('ab') as stream:
            stream.write(b'{"kind":')
    elif kind == 'open_admission':
        admission.open_gate(setup['source'], 'new-parent', '123')
    elif kind == 'destination_exists':
        setup['archive'].mkdir()
    elif kind == 'new_root_exists':
        setup['new_root'].mkdir()
    elif kind == 'wrong_sha':
        plan['sha256'] = 'b' * 64
    before = archive._inventory(setup['source'])
    with pytest.raises(archive.ArchiveRefused):
        _apply(plan)
    assert archive._inventory(setup['source']) == before
    assert not (setup['journal'] / 'prepared.json').exists()


@pytest.mark.parametrize('kind', ['tail', 'open', 'orphan'])
def test_old_code_proof_does_not_swallow_unresolved_history(setup, kind):
    if kind == 'tail':
        with admission.admission_path(setup['source']).open('ab') as stream:
            stream.write(b'{}')
    elif kind == 'open':
        admission.open_gate(setup['source'], 'unclosed', '123')
    else:
        (setup['source'] / '_service' / 'jobs.jsonl').write_bytes(b'{}\n')
    with pytest.raises(archive.ArchiveRefused, match='旧代码无法证明'):
        archive.preview(**setup)
    assert not (setup['journal'] / 'preview.json').exists()


def test_live_owner_lease_is_probed_across_a_real_process(setup):
    script = ('from novel.orchestrator.admission import owner_lease\n'
              'import sys\n'
              'with owner_lease(sys.argv[1], "old-parent"):\n'
              ' print("locked", flush=True)\n'
              ' sys.stdin.readline()\n')
    process = subprocess.Popen([sys.executable, '-B', '-c', script, str(setup['source'])],
                               cwd=setup['old_code'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               env={**os.environ, 'PYTHONPATH': str(setup['old_code'])}, text=True)
    try:
        assert process.stdout.readline().strip() == 'locked'
        with pytest.raises(archive.ArchiveRefused, match='锁仍被持有'):
            archive.preview(**setup)
    finally:
        process.communicate('\n', timeout=15)
    assert process.returncode == 0
    assert archive.preview(**setup)['stopped']['admissions'] == 1


def test_declared_live_pid_and_port_are_not_stop_proof(setup):
    with pytest.raises(archive.ArchiveRefused, match='仍存活'):
        archive.preview(**{**setup, 'stopped_pids': [os.getpid()],
                           'journal': setup['journal'].with_name('PID预检')})
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', setup['ports'][0]))
        sock.listen()
        with pytest.raises(archive.ArchiveRefused, match='端口'):
            archive.preview(**setup)
    assert not (setup['journal'] / 'preview.json').exists()


def test_path_boundaries_and_links_fail_before_preview(setup):
    for key in ('source', 'archive', 'new_root', 'journal'):
        with pytest.raises(archive.ArchiveRefused, match='配对旧代码 checkout 之外'):
            archive.preview(**{**setup, key: setup['old_code'] / '代码内数据根'})
    for changes in ({'archive': setup['source'] / 'inside'},
                    {'source': REPO}, {'new_root': setup['source']},
                    {'journal': setup['archive'] / 'journal'}):
        with pytest.raises(archive.ArchiveRefused):
            archive.preview(**{**setup, **changes})
    os.link(setup['source'] / '_service' / 'ledger.jsonl', setup['source'] / 'alias')
    with pytest.raises(archive.ArchiveRefused, match='硬链接'):
        archive.preview(**setup)


@pytest.mark.parametrize('phase', ['preview', 'apply'])
def test_unreadable_subdirectory_never_becomes_a_complete_inventory(setup, monkeypatch, phase):
    before = archive._inventory(setup['source'])
    plan = archive.preview(**setup) if phase == 'apply' else None
    blocked = setup['source'] / '_service' / 'uploads'
    original = os.scandir
    with monkeypatch.context() as unavailable:
        def deny_subtree(path):
            if Path(path) == blocked:
                raise PermissionError(13, '模拟子目录不可列举', str(blocked))
            return original(path)
        unavailable.setattr(archive.os, 'scandir', deny_subtree)
        with pytest.raises(archive.ArchiveRefused, match='无法完整列举'):
            archive.preview(**setup) if plan is None else _apply(plan)
    assert setup['source'].is_dir() and not setup['archive'].exists()
    assert not (setup['journal'] / 'prepared.json').exists()
    assert archive._inventory(setup['source']) == before
    if plan is None:
        assert not (setup['journal'] / 'preview.json').exists()
        plan = archive.preview(**setup)
    assert _apply(plan)['state'] == 'committed'
    assert archive._inventory(setup['archive']) == before


def test_archive_keeps_reconciled_unknown_evidence_and_rejects_late_events(setup):
    base = setup['source']
    admission.open_gate(base, 'stopped-unknown', '123')
    claim = admission.claim(base, 'stopped-unknown', '123', node='stage4.design',
                            claim_sha256='c' * 64, claim_id='orphan-claim')
    beacon.append(base, ProgressEvent(ts=1, sid='123', run_id='stopped-unknown',
                                     execution_id='orphan-execution', node='stage4.design', phase='start'))
    admission.reconcile_gate(base, 'stopped-unknown', actor='test:operator',
                             reason='已核实本例无真实子进程，原结果保持未知',
                             subject_sha256=admission.reconcile_subject(base, 'stopped-unknown'), force=True)
    original = archive._inventory(base)
    plan = archive.preview(**setup)
    assert plan['stopped']['reconciled'][0]['unknown_claims'] == 1
    assert archive._inventory(base) == original
    # 已有封口后的迟到事件反证停止 seal，不能因历史曾经 reconciled 而忽略。
    admission.release(base, claim)
    with pytest.raises(archive.ArchiveRefused):
        _apply(plan)
    retry = {**setup, 'journal': setup['journal'].with_name('迟到后重新预览')}
    with pytest.raises(archive.ArchiveRefused, match='旧代码无法证明'):
        archive.preview(**retry)


def test_code_identity_requires_real_clean_checkout(tmp_path):
    repo = tmp_path / 'code'
    repo.mkdir()
    def git(*args):
        return subprocess.run(['git', '-c', f'safe.directory={repo.as_posix()}', *args],
                              cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    git('init')
    (repo / 'app.py').write_text('value = 1\n', encoding='utf-8')
    git('add', 'app.py')
    git('-c', 'user.name=工程验收', '-c', 'user.email=test@example.invalid', 'commit', '-m', '测试基线')
    assert archive._code_identity(repo) == git('rev-parse', 'HEAD')
    (repo / 'app.py').write_text('value = 2\n', encoding='utf-8')
    with pytest.raises(archive.ArchiveRefused, match='干净'):
        archive._code_identity(repo)


def test_cli_preview_and_apply_share_exact_plan(setup, capsys):
    args = ['preview']
    for key in ('source', 'archive', 'new_root', 'journal', 'old_code', 'actor', 'reason'):
        args += ['--' + key.replace('_', '-'), str(setup[key])]
    args += ['--port', str(setup['ports'][0]), '--offline-ack']
    assert archive.main(args) == 0
    plan = read_sealed(setup['journal'] / 'preview.json')
    common = ['apply', '--journal', str(setup['journal']), '--expected-sha256', plan['sha256']]
    assert archive.main(common) == 2
    assert setup['source'].exists()
    assert archive.main([*common, '--yes']) == 0
    assert not setup['source'].exists() and setup['new_root'].is_dir()


@pytest.mark.parametrize('window', ['move', 'commit'])
def test_hard_process_exit_recovers_same_inode_lock_aliases(setup, window):
    plan = archive.preview(**setup)
    script = '''
import os, sys
from pathlib import Path
from tools import archive_output_root as archive
archive._code_identity = lambda root: 'a' * 40
if sys.argv[3] == 'move':
    original = archive._move
    def stop(source, target):
        original(source, target)
        os._exit(70)
    archive._move = stop
else:
    original = archive._immutable
    def stop(path, value):
        original(path, value)
        if path.name == 'committed.json':
            os._exit(70)
    archive._immutable = stop
archive.apply(Path(sys.argv[1]), expected_sha256=sys.argv[2], yes=True)
'''
    stopped = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', script,
                              str(setup['journal']), plan['sha256'], window],
                             cwd=REPO, capture_output=True, timeout=60)
    assert stopped.returncode == 70, stopped.stderr.decode('utf-8', errors='replace')
    assert list((setup['journal'] / 'owner_handles').iterdir())
    assert not setup['source'].exists()
    if window == 'move':
        with pytest.raises(OutputRootUnavailable):
            require_active(setup['new_root'])
    assert _apply(plan)['state'] == 'committed'
    assert list((setup['journal'] / 'owner_handles').iterdir()) == []
    assert archive._inventory(setup['archive']) == plan['inventory']
