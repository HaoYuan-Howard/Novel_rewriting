"""离线推进工作流契约，保留整个原任务及来源，不迁移小说产物。

只有显式登记的相邻 manifest 可以推进。prepared 冻结精确字节、全盘保留清单、
配对来源代码的接受和排空证明；manifest CAS 后由 completed 闭合消费者入口。
工作单元仍使用原 task_id，旧预算、profiles、决定和付费回答一个字节也不改。
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from contracts.artifacts import ARTIFACT_PATHS, STAGE1_MAIN_TEXT, STAGE2_BLUEPRINT_CHARACTER_PROJECTION, STAGE2_CHARACTERS
from contracts.schema.workflow import (
    WorkflowManifestV4, WorkflowManifestV5, WorkflowTransitionCompletedV1,
    WorkflowTransitionPendingV1, WorkflowTransitionPreparedV1, WorkflowTransitionProofV1,
    WorkflowTransitionProjectionV1, WorkflowTransitionRefV1,
)
from novel import paths, work_units
from novel.appendlog import path_transaction_lock
from novel.errors import TerminalState


class WorkflowTransitionRefused(TerminalState):
    """来源、离线证明或 CAS 不成立；保留原文件并等待同一事务恢复。"""


_SOURCE_MODEL = WorkflowManifestV4
_TARGET_MODEL = WorkflowManifestV5
_CORE_KEYS = frozenset({
    'stage2.analyze_receipt', 'stage2.analyze_recovery', 'stage2.analyze_owner_draft_edit',
    'stage2.analyze_characters_draft_repair', 'stage2.analyze_global_draft_repair',
    'stage2.characters', 'stage2.characters_edit', 'stage2.global_grounding',
    'stage2.global_review', 'stage2.global_review_decisions', 'stage2.global_edit',
    'stage2.global_edit_failure', 'stage2.global_edit_input_commit', 'stage2.global_edit_commit',
    'stage2.blueprint_character_projection',
})
_CORE_NODES = frozenset({'stage2.analyze', 'stage2.characters', 'stage2.global'})
_ACTIVE_TRANSITION: ContextVar[tuple[str, str, str] | None] = ContextVar('workflow_transition_owner', default=None)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _plain(path: Path) -> Path:
    """先拒绝字面路径每层的链接，再解析；不能让 junction 隐藏清单外写口。"""
    if str(path).startswith(('\\\\?\\', '\\\\.\\')):
        raise WorkflowTransitionRefused('迁移路径不得使用 Windows 设备命名空间')
    absolute = Path(os.path.abspath(path))
    for part in (absolute, *absolute.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise WorkflowTransitionRefused('工作流迁移路径不得经过链接或 reparse point')
    return absolute.resolve()


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError('重复 JSON 键')
        result[key] = value
    return result


def _decode(raw: bytes, model):
    try:
        payload = json.loads(raw, object_pairs_hook=_pairs)
        value = model.model_validate(payload)
        # 普通默认值不能补造旧 manifest 缺失的契约字段，也不接受 bool 伪整数。
        if work_units._bytes(payload) != work_units._bytes(value.model_dump(mode='json')):
            raise ValueError('记录缺少正式字段或依赖隐式类型转换')
        return value
    except (ValueError, TypeError) as exc:
        raise WorkflowTransitionRefused('迁移记录不符合明确登记的完整版本') from exc


def _raw(path: Path) -> bytes:
    try:
        with work_units._open_snapshot(_plain(path)) as stream:
            return stream.read()
    except OSError as exc:
        raise WorkflowTransitionRefused(f'迁移记录不可读：{path.name}') from exc


def _read(path: Path, model):
    return _decode(_raw(path), model)


def _immutable(base: Path, path: Path, model) -> None:
    """完整临时文件经 no-replace 发布；已存在的另一份字节永不覆盖。"""
    target = work_units._safe(base, _plain(path))
    data = work_units._bytes(model.model_dump(mode='json'))
    if target.exists():
        if target.read_bytes() != data:
            raise WorkflowTransitionRefused('已有不可变迁移记录与本次请求不一致')
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.transition-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.read_bytes() != data:
                raise WorkflowTransitionRefused('并发迁移记录发生冲突')
    finally:
        Path(temporary).unlink(missing_ok=True)


def _code_identity(root: Path) -> str:
    def git(*args):
        result = subprocess.run(['git', '-c', f'safe.directory={root.as_posix()}', *args],
            cwd=root, capture_output=True, text=True, encoding='utf-8', check=False)
        if result.returncode:
            raise WorkflowTransitionRefused('迁移代码必须是可核验的 Git checkout')
        return result.stdout.strip()
    if Path(git('rev-parse', '--show-toplevel')).resolve() != root:
        raise WorkflowTransitionRefused('source-code 必须精确指向冻结 checkout 根')
    if git('status', '--porcelain', '--untracked-files=normal'):
        raise WorkflowTransitionRefused('来源和目标代码必须干净并已冻结')
    return git('rev-parse', 'HEAD')


def _require_pid_stopped(pid: int) -> None:
    if os.name != 'nt':
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        raise WorkflowTransitionRefused('声明停止的服务 PID 仍存活或无法核验')
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return
        raise WorkflowTransitionRefused('无法核验服务 PID 是否退出')
    try:
        if kernel.WaitForSingleObject(handle, 0) != 0:
            raise WorkflowTransitionRefused('声明停止的服务 PID 仍存活')
    finally:
        kernel.CloseHandle(handle)


@contextmanager
def _offline(ports: tuple[int, ...], stopped_pids: tuple[int, ...]):
    with ExitStack() as stack:
        for pid in stopped_pids:
            _require_pid_stopped(pid)
        for port in ports:
            sock = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
            if os.name == 'nt':
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                sock.bind(('127.0.0.1', port))
                sock.listen(1)
            except OSError as exc:
                raise WorkflowTransitionRefused('服务端口仍占用或无法冻结离线窗口') from exc
        yield


@contextmanager
def _lock_existing(path: Path):
    """不创建、补写锁文件；非阻塞持有原 owner/writer 的同一 OS 字节锁。"""
    with _plain(path).open('r+b') as stream:
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise WorkflowTransitionRefused('owner/writer 锁仍被持有或无法核验') from exc
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def _owners(base: Path):
    # transition 自己的控制锁在外层持有，其余现有锁全部保留直到 manifest/receipt CAS 闭合。
    control = paths.workflow_transition_pending_path(base)
    excluded = control.with_name(control.name + '.lock')
    with ExitStack() as stack:
        locks = sorted(path for path in base.rglob('*.lock') if path != excluded)
        for path in locks:
            stack.enter_context(_lock_existing(path))
        def unchanged():
            if locks != sorted(path for path in base.rglob('*.lock') if path != excluded):
                raise WorkflowTransitionRefused('离线窗口中出现新的 owner/writer 锁')
        yield unchanged
        unchanged()


def _inventory(base: Path) -> dict[str, str]:
    """全盘原字节是 CAS 保存义务，不是下游业务失效依赖。"""
    manifest = paths.workflow_manifest_path(base)
    pending = paths.workflow_transition_pending_path(base)
    records = paths.workflow_transition_dir(base, '0' * 64).parent
    result = {}
    def failed(error):
        raise WorkflowTransitionRefused('无法完整列举保留清单') from error
    for current, dirs, files in os.walk(base, followlinks=False, onerror=failed):
        for name in dirs + files:
            _plain(Path(current) / name)
        dirs[:] = [name for name in dirs if Path(current) / name != records]
        for name in files:
            path = Path(current) / name
            if path in (manifest, pending) or path.name.endswith('.lock'):
                continue
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise WorkflowTransitionRefused('保留清单含非常规文件或硬链接别名')
            raw = path.read_bytes()
            after = path.stat()
            if (info.st_size, info.st_mtime_ns, info.st_ino) != (
                    after.st_size, after.st_mtime_ns, after.st_ino):
                raise WorkflowTransitionRefused('读取保留文件期间发生写入')
            result[path.relative_to(base).as_posix()] = _sha(raw)
    return dict(sorted(result.items()))


def _projection_target(base: Path, sid: str, code: str) -> tuple[bytes, str, str]:
    """调用 Characters 的同一纯编译器；不补空绑定，不发布/重写 Core。"""
    from contracts.schema.characters import CharacterRegistry
    from contracts.schema.stage1 import MainText
    from novel.orchestrator.state import readonly_ctx
    from novel.stages.stage2.character_refs import materialize_blueprint_character_projection
    from novel.store import ArtifactStore
    store = ArtifactStore(readonly_ctx(paths.resolve_novel_dir(base, sid), 'stage2.characters', code))
    registry, char_sha = store.read_own_with_sha256(CharacterRegistry, STAGE2_CHARACTERS)
    main, main_sha = store.read_own_with_sha256(MainText, STAGE1_MAIN_TEXT)
    target = materialize_blueprint_character_projection(
        registry.model_dump(mode='json')['characters'], main.model_dump(mode='json'))
    return store._render(STAGE2_BLUEPRINT_CHARACTER_PROJECTION, target), char_sha, main_sha


def _projection_plans(base: Path, proof: WorkflowTransitionProofV1,
                      code: str) -> dict[str, WorkflowTransitionProjectionV1]:
    result = {}
    for sid in proof.accepted_books:
        path = paths.resolve_novel_dir(base, sid) / STAGE2_BLUEPRINT_CHARACTER_PROJECTION
        source = _raw(path) if path.exists() else None
        target, char_sha, main_sha = _projection_target(base, sid, code)
        result[sid] = WorkflowTransitionProjectionV1(
            relative_path=path.relative_to(base).as_posix(),
            source_json=source.decode('utf-8') if source is not None else None,
            source_sha256=_sha(source) if source is not None else None,
            target_json=target.decode('utf-8'), target_sha256=_sha(target),
            characters_sha256=char_sha, main_text_sha256=main_sha)
    return result


def _preserved_unchanged(base: Path, prepared: WorkflowTransitionPreparedV1) -> None:
    current = _inventory(base)
    expected = dict(prepared.preserved_files)
    for value in prepared.projections.values():
        actual = current.pop(value.relative_path, None)
        if actual not in (value.source_sha256, value.target_sha256):
            raise WorkflowTransitionRefused('人物投影不是冻结 source/target；拒绝第三方修改')
        expected.pop(value.relative_path, None)
    if current != expected:
        raise WorkflowTransitionRefused('保留文件变化；不得丢弃 profiles、ledger、审批或原回答')


def projection_commit_material(store, transition_id: str, prepared_sha256: str):
    """Store 的专用窄授权：只能采用 prepared 中该书的确定性人物投影。"""
    base = _plain(store.ctx.novel_dir.parent)
    if _ACTIVE_TRANSITION.get() != (str(base), transition_id, prepared_sha256):
        raise WorkflowTransitionRefused('人物投影写口只允许已持离线 owner 锁的当前迁移')
    prepared, digest = _prepared(base, transition_id)
    if digest != prepared_sha256 or _completed(base, prepared, digest) is not None:
        raise WorkflowTransitionRefused('投影写入不属于当前待提交迁移')
    expected_pointer = WorkflowTransitionPendingV1(transition_id=transition_id, prepared_sha256=digest)
    if _read(paths.workflow_transition_pending_path(base), WorkflowTransitionPendingV1) != expected_pointer:
        raise WorkflowTransitionRefused('投影提交缺当前迁移围栏')
    if _raw(paths.workflow_manifest_path(base)) not in (
            prepared.source_manifest_json.encode('utf-8'), prepared.target_manifest_json.encode('utf-8')):
        raise WorkflowTransitionRefused('投影提交时 manifest 偏离冻结 source/target')
    value = prepared.projections.get(store.ctx.sid)
    path = store.path(STAGE2_BLUEPRINT_CHARACTER_PROJECTION)
    if value is None or _plain(path).relative_to(base).as_posix() != value.relative_path:
        raise WorkflowTransitionRefused('此路径不在当前迁移的投影授权内')
    _preserved_unchanged(base, prepared)
    target, char_sha, main_sha = _projection_target(base, store.ctx.sid, prepared.target_code_version)
    if (target != value.target_json.encode('utf-8') or _sha(target) != value.target_sha256
            or (char_sha, main_sha) != (value.characters_sha256, value.main_text_sha256)):
        raise WorkflowTransitionRefused('人物投影与冻结输入或现役编译结果不一致')
    return path, value.source_json.encode('utf-8') if value.source_json is not None else None, target


def _assert_downstream_empty(base: Path, files: dict[str, str]) -> None:
    """文件、历史及运行身份一起扫描；没有 formal 不证明尚未购买下游回答。"""
    allowed = {Path(ARTIFACT_PATHS[key]).stem for key in _CORE_KEYS}
    forbidden = {'stage3', 'stage4', 'stage5', 'stage6', 'stage7', '_stage4_runs',
                 '_stage5_runs', '_stage6_runs', '_stage7_runs', 'blueprint', 'blueprints',
                 'knowledge_links'}
    for path in base.rglob('*'):
        _plain(path)
        parts = path.relative_to(base).parts
        if parts and parts[0] == '_service' and (len(parts) < 2 or parts[1] != 'runs'):
            continue
        if any(part in forbidden for part in parts[1:]):
            raise WorkflowTransitionRefused('目标契约的下游已有产物、运行目录或历史；不能自动迁移')
    for rel in files:
        parts = Path(rel).parts
        if not parts or parts[0] == '_service':
            continue
        for stage in parts[1:]:
            if stage in {'stage3', 'stage4', 'stage5', 'stage6', 'stage7',
                         '_stage4_runs', '_stage5_runs', '_stage6_runs', '_stage7_runs',
                         'blueprint', 'blueprints', 'knowledge_links'}:
                raise WorkflowTransitionRefused('目标契约的下游已有产物或历史；不能自动迁移')
        if 'stage2' in parts:
            name = Path(rel).name
            if not any(name == stem + '.json' or name.startswith(stem + '.')
                       or stem == 'global_review_decisions' and name.startswith(stem + '_')
                       for stem in allowed):
                raise WorkflowTransitionRefused('Stage2 下游已有 formal/INVALID/history 或未知材料')
    service = paths.service_runs_dir(base)
    if service.exists():
        for path in service.rglob('*'):
            _plain(path)
            if not path.is_file() or path.name.endswith('.lock'):
                continue
            relative = path.relative_to(service).parts
            if any(part in {'stage3', 'stage4', 'stage5', 'stage6', 'stage7',
                            'blueprint', 'blueprints', 'knowledge_links'} for part in relative):
                raise WorkflowTransitionRefused('下游存在 checkpoint/prepared/已购回答来源')
    # 只检查真实执行身份，不扫描 JSON schema、下一步计划或模型正文里的同名字段。
    # blob 的 node 归属由来源代码验证 work-unit 注册与完整账，不能从任意字符串猜。
    for rel in files:
        path = base / rel
        if path.suffix not in {'.json', '.jsonl'}:
            continue
        try:
            raw = path.read_bytes()
            if path.suffix == '.jsonl' and raw and not raw.endswith(b'\n'):
                raise ValueError('未提交 JSONL 尾')
            values = ([json.loads(line, object_pairs_hook=_pairs) for line in raw.splitlines() if line.strip()]
                      if path.suffix == '.jsonl' else [json.loads(raw, object_pairs_hook=_pairs)])
        except (ValueError, UnicodeError) as exc:
            raise WorkflowTransitionRefused('服务来源中有不可解析 JSON；无法证明下游为空') from exc
        identities = []
        for value in values:
            if not isinstance(value, dict):
                continue
            identities.append(value)
            if isinstance(value.get('start'), dict):
                identities.append(value['start'])
            if isinstance(value.get('identity'), dict):
                identities.append(value['identity'])
        stack = list(identities)
        while stack:
            value = stack.pop()
            if isinstance(value, list):
                stack.extend(value)
            elif isinstance(value, dict):
                for key, item in value.items():
                    if key in {'node', 'producer_node', 'purpose', 'activity'} and isinstance(item, str):
                        if item.startswith('stage') and not (item.startswith(('stage0.', 'stage1.'))
                                or any(item == node or item.startswith(node + '.') for node in _CORE_NODES)):
                            raise WorkflowTransitionRefused('下游已有运行、工作单元或已购回答')


# 外部来源代码只读自己的生命周期和 Core 接受证明。不会加载新代码的 artifact
# decoder，不把 unknown、孤儿事件、缺 epoch drain 或未提交 JSONL 尾当成“已停机”。
_SOURCE_PROOF = r'''
import hashlib, json, sys
from pathlib import Path
from novel import paths, agent_session_audit, work_units
from contracts.schema.workflow import WorkflowManifestV4, WorkUnitRegistryV1, WorkUnitLedgerV2
from novel.orchestrator import admission, agent, approvals, beacon, jobs, queue
from novel.orchestrator.state import readonly_ctx
from novel.stages.stage2 import global_review
base, code = Path(sys.argv[1]), sys.argv[2]
loaded, hashes = [], {}
streams = [(admission.admission_path, admission.read_events),
           (beacon.beacon_path, beacon.read_events), (agent.runs_path, agent.read_events),
           (jobs.jobs_path, jobs.read_events), (queue.queue_path, queue.read_events),
           (agent_session_audit.audit_path, agent_session_audit.read_events)]
for locate, read in streams:
    path = locate(base)
    raw = path.read_bytes() if path.is_file() else b''
    if raw and not raw.endswith(b'\n'): raise ValueError('uncommitted lifecycle tail')
    lines = [json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
    events = read(base)
    if len(events) != len(lines): raise ValueError('lifecycle denominator mismatch')
    loaded.append(events)
    hashes[path.name] = hashlib.sha256(raw).hexdigest() if path.is_file() else None
adm, progress, agents, job_events, queues, sessions = loaded
parents = {e.parent_run_id for e in adm}
for parent in tuple(parents):
    gate = admission._fold(adm, parent)
    gate = admission._validate_reconcile_seal(gate, adm,
        admission._parent_beacon_events(progress, gate.lifecycle_run_id))
    if not gate.opened or not gate.closed or gate.terminal_kind not in ('drained', 'reconciled'):
        raise ValueError('admission not drained')
    if gate.active_claims: raise ValueError('unresolved claim')
    parents.add(gate.lifecycle_run_id)
for run_id in {e.run_id for e in progress}:
    if run_id is None or beacon._open_executions_from_events(progress, run_id):
        raise ValueError('execution not ended')
    parents.add(run_id)
for events, fold, key in [(agents, agent.fold, 'run_id'), (job_events, jobs.fold, 'job_id')]:
    records = fold(events)
    if {getattr(e, key) for e in events} != {getattr(r, key) for r in records}:
        raise ValueError('orphan lifecycle event')
    for row in records:
        if row.ended is None or row.state in ('pending', 'running', 'paused', 'unknown'):
            raise ValueError('owner not terminal')
        if key == 'run_id':
            parents.add(row.run_id)
            for epoch in row.epochs:
                closed = [e for e in agents if e.run_id == row.run_id and e.kind == 'epoch_closed'
                          and e.epoch_id == epoch.epoch_id]
                if len(closed) != 1 or closed[0].drain_state != 'drained':
                    raise ValueError('epoch drain missing')
        else: parents.add(row.start.run_id)
for session in agent_session_audit.fold(sessions):
    if session.end is None or session.process_state not in ('ok', 'failed', 'stopped'):
        raise ValueError('provider owner not ended')
queue_rows = queue.fold(queues)
if {e.queue_id for e in queues} != {row.queue_id for row in queue_rows}:
    raise ValueError('orphan queue event')
if any(row.state in ('pending','running','paused','unknown') for row in queue_rows):
    raise ValueError('queue not terminal')
books = {}
if sys.argv[3] == 'accept':
    source_manifest = WorkflowManifestV4.model_validate_json(paths.workflow_manifest_path(base).read_bytes())
    for book in paths.iter_novel_dirs(base):
        ctx = readonly_ctx(book, 'stage2.blueprint', code)
        with approvals._read_projection(overview=None, code=code):
            global_review.require_global_accepted(ctx)
        books[ctx.sid] = {key:hashlib.sha256((book/rel).read_bytes()).hexdigest() for key,rel in (
            ('characters_sha256','stage2/characters.json'),
            ('global_sha256','stage2/global_grounding.json'),
            ('review_sha256','stage2/global_review.json'))}
    if not books: raise ValueError('no accepted Stage2 source')
    for sid_root in paths.service_runs_dir(base).iterdir():
        registry_path = sid_root/'work_units.json'
        unit_root = sid_root/'work_units'
        if not registry_path.exists():
            if unit_root.exists() and any(unit_root.iterdir()): raise ValueError('orphan paid response')
            continue
        registry = WorkUnitRegistryV1.model_validate_json(registry_path.read_bytes())
        if (registry.task_id != source_manifest.task_id or registry.sid != sid_root.name
                or len(set(registry.unit_ids)) != len(registry.unit_ids)):
            raise ValueError('work unit registry identity mismatch')
        actual = {p.name for p in unit_root.iterdir() if p.is_dir()} if unit_root.exists() else set()
        if actual != set(registry.unit_ids): raise ValueError('work unit denominator mismatch')
        for unit_id in registry.unit_ids:
            unit = WorkUnitLedgerV2.model_validate_json((unit_root/unit_id/'ledger.json').read_bytes())
            if unit.task_id != registry.task_id or unit.sid != registry.sid or unit.unit_id != unit_id:
                raise ValueError('work unit identity mismatch')
            if unit.node not in ('stage2.analyze', 'stage2.characters', 'stage2.global'):
                raise ValueError('downstream paid response')
            if any(turn.state in ('reserved', 'unknown') for turn in unit.turns):
                raise ValueError('work unit response unknown')
            # 此只读对象只绑定已严格解码的 source task，不经当前 manifest writer。
            # 不能在迁移中调用普通构造器初始化额度，也不能为测试/新版本放宽该入口。
            budget = object.__new__(work_units.WorkUnitBudget)
            budget.base = base
            budget.identity = {key:getattr(unit,key) for key in ('task_id','sid','node','role','scope')}
            budget.unit_id = work_units._digest(budget.identity)
            if budget.unit_id != unit_id: raise ValueError('work unit persistent identity mismatch')
            budget.directory = unit_root/unit_id
            budget.path = budget.directory/'ledger.json'
            budget.registry_path = registry_path
            budget._read()
            budget.group_producer_recovery()
            for turn in unit.turns:
                for field, read in [('request_payload_sha256',budget.request_payload),
                        ('response_sha256',budget.response_payload),('recovery_sha256',budget.recovery_proof),
                        ('producer_sha256',budget.producer_recovery)]:
                    if getattr(turn,field) is not None: read(turn.turn_id)
                for field in ('request_payload_sha256','response_sha256','recovery_sha256',
                              'producer_sha256','feedback_sha256'):
                    digest = getattr(turn,field)
                    if digest is None: continue
                    raw = (budget.directory/(digest+'.json')).read_bytes()
                    if hashlib.sha256(raw).hexdigest() != digest:
                        raise ValueError('work unit blob raw digest mismatch')
print(json.dumps({'logs':hashes,'owner_ids':sorted(parents),
    'counts':{'admissions':len({e.parent_run_id for e in adm}),'executions':sum(e.phase=='start' for e in progress),
              'agents':len(agent.fold(agents)),'jobs':len(jobs.fold(job_events)),
              'sessions':len(agent_session_audit.fold(sessions)),'queues':len(queue_rows)},
    'accepted_books':books}))
'''


def _source_proof(base: Path, source_code: Path, code: str, *, accepted: bool) -> WorkflowTransitionProofV1:
    environment = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(source_code)}
    response = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', _SOURCE_PROOF,
        str(base), code, 'accept' if accepted else 'lifecycle'], cwd=source_code,
        env=environment, capture_output=True, timeout=60)
    if response.returncode:
        raise WorkflowTransitionRefused('来源代码不能证明全 owner 排空及 Stage2 语义接受；先按原代码恢复')
    try:
        return WorkflowTransitionProofV1.model_validate_json(response.stdout)
    except ValueError as exc:
        raise WorkflowTransitionRefused('来源代码的离线证明不可读取') from exc


def _prepared(base: Path, transition_id: str) -> tuple[WorkflowTransitionPreparedV1, str]:
    path = paths.workflow_transition_dir(base, transition_id) / 'prepared.json'
    raw = _raw(path)
    value = _decode(raw, WorkflowTransitionPreparedV1)
    if value.transition_id != transition_id:
        raise WorkflowTransitionRefused('迁移记录与目录身份不一致')
    source = _decode(value.source_manifest_json.encode('utf-8'), _SOURCE_MODEL)
    target = _decode(value.target_manifest_json.encode('utf-8'), _TARGET_MODEL)
    if (source.task_id != target.task_id or source.created_at != target.created_at
            or _sha(value.source_manifest_json.encode('utf-8')) != value.source_manifest_sha256
            or _sha(value.target_manifest_json.encode('utf-8')) != value.target_manifest_sha256
            or target.transition_ref != WorkflowTransitionRefV1(
                transition_id=transition_id, source_manifest_sha256=value.source_manifest_sha256)):
        raise WorkflowTransitionRefused('源目标身份、精确字节或迁移引用不闭合')
    if set(value.projections) != set(value.source_proof.accepted_books):
        raise WorkflowTransitionRefused('人物投影集合与来源接受集合不一致')
    for sid, projection in value.projections.items():
        parts = Path(projection.relative_path).parts
        if (len(parts) != 3 or Path(*parts[1:]).as_posix() != STAGE2_BLUEPRINT_CHARACTER_PROJECTION
                or paths.derive_source_meta(parts[0])[0] != sid
                or projection.relative_path != Path(*parts).as_posix()
                or value.preserved_files.get(projection.relative_path) != projection.source_sha256
                or (projection.source_json is not None
                    and _sha(projection.source_json.encode('utf-8')) != projection.source_sha256)
                or _sha(projection.target_json.encode('utf-8')) != projection.target_sha256
                or projection.characters_sha256 != value.source_proof.accepted_books[sid].characters_sha256):
            raise WorkflowTransitionRefused('人物投影原目标路径、字节或输入身份不闭合')
    return value, _sha(raw)


def _completed(base: Path, prepared: WorkflowTransitionPreparedV1, digest: str):
    path = paths.workflow_transition_dir(base, prepared.transition_id) / 'completed.json'
    if not path.exists():
        return None
    value = _read(path, WorkflowTransitionCompletedV1)
    if (value.transition_id != prepared.transition_id or value.prepared_sha256 != digest
            or value.source_manifest_sha256 != prepared.source_manifest_sha256
            or value.target_manifest_sha256 != prepared.target_manifest_sha256):
        raise WorkflowTransitionRefused('completed 与不可变 prepared 不闭合')
    return value


def require_transition_completed(base: Path, manifest: WorkflowManifestV5, raw: bytes) -> None:
    """普通现役入口只消费闭合回执；不恢复、不创建任何迁移文件。"""
    pending_path = paths.workflow_transition_pending_path(base)
    pending = _read(pending_path, WorkflowTransitionPendingV1) if pending_path.exists() else None
    ref = manifest.transition_ref
    if ref is None:
        if pending is not None:
            raise WorkflowTransitionRefused('工作流迁移 prepared 尚待免费提交')
        return
    prepared, digest = _prepared(base, ref.transition_id)
    if (ref.source_manifest_sha256 != prepared.source_manifest_sha256
            or raw != prepared.target_manifest_json.encode('utf-8')
            or _completed(base, prepared, digest) is None
            or pending is not None and pending != WorkflowTransitionPendingV1(
                transition_id=ref.transition_id, prepared_sha256=digest)):
        raise WorkflowTransitionRefused('工作流迁移尚未完成或当前 manifest 已变；请免费 recover')


def preview(*, base: Path, source_code: Path, service_ports: tuple[int, ...],
            stopped_pids: tuple[int, ...], actor: str, reason: str,
            offline_ack: bool) -> dict[str, Any]:
    base, source_code = _plain(base), _plain(source_code)
    if not offline_ack or not base.is_dir() or not paths.service_dir(base).is_dir():
        raise WorkflowTransitionRefused('必须确认服务、调度器及独立 CLI 已停止；仅接收现有工作流根')
    source_raw = _raw(paths.workflow_manifest_path(base))
    source = _decode(source_raw, _SOURCE_MODEL)
    source_commit = _code_identity(source_code)
    target_commit = _code_identity(Path(__file__).resolve().parents[2])
    identity = _sha(uuid4().bytes)
    target = _TARGET_MODEL(task_id=source.task_id, created_at=source.created_at,
        transition_ref=WorkflowTransitionRefV1(transition_id=identity,
                                             source_manifest_sha256=_sha(source_raw)))
    # 先验证请求的完整 typed 形状；不因坏参数创建控制锁和 prepared。
    controls = dict(service_ports=service_ports, stopped_pids=stopped_pids,
                    actor=actor, reason=reason)
    if (not actor.strip() or not reason.strip() or not service_ports
            or len(set(service_ports)) != len(service_ports)
            or any(type(port) is not int or not 1 <= port <= 65535 for port in service_ports)
            or len(set(stopped_pids)) != len(stopped_pids)
            or any(type(pid) is not int or pid <= 0 for pid in stopped_pids)):
        raise WorkflowTransitionRefused('迁移需要合法唯一端口/PID和非空 actor/reason')
    pointer = paths.workflow_transition_pending_path(base)
    with _offline(service_ports, stopped_pids), path_transaction_lock(pointer), _owners(base) as owners_unchanged:
        if pointer.exists():
            raise WorkflowTransitionRefused('已有迁移 prepared；请使用原 transition-id 免费恢复')
        journal = paths.workflow_transition_dir(base, '0' * 64).parent
        for directory in sorted(journal.iterdir()) if journal.exists() else ():
            if not directory.is_dir():
                raise WorkflowTransitionRefused('迁移记录目录有未知文件；不能开新事务')
            prior, prior_sha = _prepared(base, directory.name)
            if _completed(base, prior, prior_sha) is None:
                raise WorkflowTransitionRefused(
                    f'已有不可变 prepared，恢复 {prior.transition_id} / {prior_sha}，不能另建迁移')
        if _raw(paths.workflow_manifest_path(base)) != source_raw:
            raise WorkflowTransitionRefused('来源 manifest 在 preview 前已变化')
        inventory = _inventory(base)
        _assert_downstream_empty(base, inventory)
        proof = _source_proof(base, source_code, source_commit, accepted=True)
        projections = _projection_plans(base, proof, target_commit)
        if (inventory != _inventory(base) or source_commit != _code_identity(source_code)
                or target_commit != _code_identity(Path(__file__).resolve().parents[2])):
            raise WorkflowTransitionRefused('preview 期间来源文件或代码变化')
        prepared = WorkflowTransitionPreparedV1(transition_id=identity,
            source_manifest_json=source_raw.decode('utf-8'), source_manifest_sha256=_sha(source_raw),
            target_manifest_json=work_units._bytes(target.model_dump(mode='json')).decode('utf-8'),
            target_manifest_sha256=_sha(work_units._bytes(target.model_dump(mode='json'))),
            source_code_directory=str(source_code), source_code_version=source_commit,
            target_code_version=target_commit, preserved_files=inventory, projections=projections, source_proof=proof,
            prepared_at=int(time.time()), **controls)
        owners_unchanged()
        _immutable(base, paths.workflow_transition_dir(base, identity) / 'prepared.json', prepared)
        digest = _sha(work_units._bytes(prepared.model_dump(mode='json')))
        _immutable(base, pointer, WorkflowTransitionPendingV1(
            transition_id=identity, prepared_sha256=digest))
    return {'state': 'prepared', 'transition_id': identity, 'prepared_sha256': digest,
            'source_manifest_sha256': prepared.source_manifest_sha256,
            'target_manifest_sha256': prepared.target_manifest_sha256}


def _cas_manifest(base: Path, prepared: WorkflowTransitionPreparedV1) -> None:
    path = paths.workflow_manifest_path(base)
    raw = _raw(path)
    if raw == prepared.target_manifest_json.encode('utf-8'):
        return
    if raw != prepared.source_manifest_json.encode('utf-8'):
        raise WorkflowTransitionRefused('manifest 已不是冻结 source/target；拒绝覆盖第三方修改')
    work_units._write(base, path, _decode(prepared.target_manifest_json.encode('utf-8'), _TARGET_MODEL))
    if path.read_bytes() != prepared.target_manifest_json.encode('utf-8'):
        raise WorkflowTransitionRefused('manifest 原子发布后的目标字节不一致')


def apply(*, base: Path, transition_id: str, expected_prepared_sha256: str,
          yes: bool) -> dict[str, Any]:
    if not yes:
        raise WorkflowTransitionRefused('推进 manifest 需显式 --yes；此操作不含模型调用')
    base = _plain(base)
    prepared, digest = _prepared(base, transition_id)
    if digest != expected_prepared_sha256:
        raise WorkflowTransitionRefused('prepared SHA 与明确批准的迁移不一致')
    done = _completed(base, prepared, digest)
    if done is not None:
        # 旧回执重放只确认历史闭合，不再检查下游空、不改新产物、不重开旧事务。
        return {'state': 'completed', **done.model_dump(mode='json')}
    pointer = paths.workflow_transition_pending_path(base)
    expected_pointer = WorkflowTransitionPendingV1(transition_id=transition_id, prepared_sha256=digest)
    source_code = _plain(Path(prepared.source_code_directory))
    with _offline(prepared.service_ports, prepared.stopped_pids), path_transaction_lock(pointer), _owners(base) as owners_unchanged:
        if _prepared(base, transition_id) != (prepared, digest):
            raise WorkflowTransitionRefused('提交前 prepared 变化')
        if pointer.exists() and _read(pointer, WorkflowTransitionPendingV1) != expected_pointer:
            raise WorkflowTransitionRefused('另一迁移拥有当前 prepared 围栏')
        if (prepared.source_code_version != _code_identity(source_code)
                or prepared.target_code_version != _code_identity(Path(__file__).resolve().parents[2])):
            raise WorkflowTransitionRefused('来源或目标代码偏离冻结版本')
        raw = _raw(paths.workflow_manifest_path(base))
        if raw not in (prepared.source_manifest_json.encode('utf-8'), prepared.target_manifest_json.encode('utf-8')):
            raise WorkflowTransitionRefused('manifest 已不是冻结 source/target；拒绝覆盖第三方修改')
        _preserved_unchanged(base, prepared)
        _assert_downstream_empty(base, prepared.preserved_files)
        proof = _source_proof(base, source_code, prepared.source_code_version, accepted=False)
        expected_proof = prepared.source_proof.model_copy(update={'accepted_books': {}})
        if proof != expected_proof:
            raise WorkflowTransitionRefused('离线生命周期证明变化；不得追认迟到 owner')
        _immutable(base, pointer, expected_pointer)
        owners_unchanged()
        from novel.orchestrator.state import readonly_ctx
        from novel.store import ArtifactStore
        token = _ACTIVE_TRANSITION.set((str(base), transition_id, digest))
        try:
            for sid in prepared.projections:
                store = ArtifactStore(readonly_ctx(paths.resolve_novel_dir(base, sid),
                                                  'stage2.characters', prepared.target_code_version))
                store.apply_workflow_projection_transition(transition_id, digest)
        finally:
            _ACTIVE_TRANSITION.reset(token)
        _cas_manifest(base, prepared)
        _preserved_unchanged(base, prepared)
        done = WorkflowTransitionCompletedV1(transition_id=transition_id,
            prepared_sha256=digest, source_manifest_sha256=prepared.source_manifest_sha256,
            target_manifest_sha256=prepared.target_manifest_sha256, completed_at=int(time.time()))
        owners_unchanged()
        _immutable(base, paths.workflow_transition_dir(base, transition_id) / 'completed.json', done)
        if _read(pointer, WorkflowTransitionPendingV1) != expected_pointer:
            raise WorkflowTransitionRefused('迁移围栏被第三方更改；保留已完成回执')
        pointer.unlink()
    return {'state': 'completed', **done.model_dump(mode='json')}


def recover(**kwargs) -> dict[str, Any]:
    """相同 prepared 的免费补尾；与 apply 共用全部身份和 owner 守卫。"""
    return apply(**kwargs)
