"""稳定业务单元与首 wire 前预算账。

候选、会话和执行 epoch 都是一次工作的证据，不能作为新预算账户。正文工作包与
完整响应按内容寻址单独保存；账中只留身份。此服务账不写书内产物、不制造接受，
费用仍只认 CostLedger。缺失或坏账必须停止，不能把没有 checkpoint 当成零调用。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, BinaryIO, TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel

from contracts.invariants import (
    BOOK_CONTENT_CALL_CEILING_DEFAULT, NODES, RETIRED_APPROVAL_BINDINGS,
    approval_work_unit, approval_scope_allowed,
)
from contracts.schema.workflow import (
    BookBudgetGrantV1, BookContentBudgetV1, WorkflowManifestV6,
    WorkflowManifestV7, WorkflowManifestV8, WorkflowManifestV9, WorkUnitAttempt, WorkUnitBlobV1,
    WorkUnitContentTurn, WorkUnitGroupBlobV1, WorkUnitLedgerV2,
    WorkUnitRegistryV1, WorkUnitProcessing,
)
from contracts.schema.work_unit_recovery import CommanderFailureRecoveryReceipt
from novel import paths
from novel.appendlog import path_transaction_lock
from novel.errors import TerminalState

if TYPE_CHECKING:
    from novel.run import RunContext


class WorkUnitRefused(TerminalState):
    """明确停点；不得吞掉后改走另一个预算账户。"""


class WorkUnitBudgetExhausted(WorkUnitRefused):
    pass


COMMANDER_FAILURE_RECONCILED = 'commander_transport_failure_reconciled'


def failure_recovery_input(turn_id: str) -> str:
    return _digest({'kind': 'commander-failure-reconciliation-v1', 'turn_id': turn_id})


def _bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':')) + '\n').encode('utf-8')


def _digest(value: Any) -> str:
    return hashlib.sha256(_bytes(value)).hexdigest()


def _safe(base: str | os.PathLike[str], path: Path) -> Path:
    root = Path(base).absolute().resolve()
    target = path.absolute()
    current = target
    while current != root and current != current.parent:
        if current.is_symlink():
            raise WorkUnitRefused('工作单元路径不能经过符号链接')
        current = current.parent
    try:
        # Windows 同一并发建目录窗口可能混合返回普通/扩展长度前缀。
        # 原子替换期间 Windows 可把旧文件句柄解析为 $Extend/$Deleted。
        # 我们随后按路径打开当前快照，边界应核稳定父目录；符号链接仍由上面拒绝。
        resolved_target = (os.path.realpath(target) if target.is_dir()
                           else os.path.join(os.path.realpath(target.parent), target.name))
        resolved = os.path.normcase(resolved_target)
        service = os.path.normcase(os.path.realpath(root / '_service'))
        if os.name == 'nt':
            resolved = resolved.removeprefix('\\\\?\\')
            service = service.removeprefix('\\\\?\\')
        Path(resolved).relative_to(Path(service))
    except ValueError as exc:
        raise WorkUnitRefused('工作单元路径越出服务根') from exc
    return target


def _write(base: str | os.PathLike[str], path: Path, model: BaseModel) -> None:
    target = _safe(base, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = _bytes(model.model_dump(mode='json'))
    fd, tmp = tempfile.mkstemp(prefix='.wu-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _replace_snapshot(Path(tmp), target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _replace_snapshot(source: Path, target: Path) -> None:
    """原子发布账；Windows 明确允许替换仍被共享读取的旧版本。"""
    if os.name != 'nt':
        os.replace(source, target)
        return

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    rename = kernel32.SetFileInformationByHandle
    rename.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    rename.restype = wintypes.BOOL

    class RenameInfo(ctypes.Structure):
        _fields_ = [('flags', wintypes.DWORD), ('root_directory', wintypes.HANDLE),
                    ('filename_length', wintypes.DWORD), ('filename', wintypes.WCHAR * 1)]

    def _extended(path: str) -> str:
        if path.startswith('\\\\?\\'):
            return path
        if path.startswith('\\\\'):
            return '\\\\?\\UNC\\' + path[2:]
        return '\\\\?\\' + path

    # 目标也必须走扩展长度前缀：源句柄有 `\\?\` 而 FileName 仍是普通路径时，
    # 绝对路径一过 MAX_PATH 就会 WinError 206，把「账已 reserved、blob 未落」的孤儿轮留下来。
    filename = _extended(str(target.absolute())).encode('utf-16-le')
    # Win32 FILE_RENAME_INFO.FileName 仍要求 NUL 结尾；长度字段不计终止符。
    buffer = ctypes.create_string_buffer(RenameInfo.filename.offset + len(filename) + 2)
    info = ctypes.cast(buffer, ctypes.POINTER(RenameInfo)).contents
    # FileRenameInfoEx 的 flags：REPLACE_IF_EXISTS | POSIX_SEMANTICS。
    # MoveFileEx（os.replace）即使目标共享 DELETE 仍会拒绝覆盖打开的文件。
    info.flags = 0x1 | 0x2
    info.root_directory = None
    info.filename_length = len(filename)
    ctypes.memmove(ctypes.addressof(buffer) + RenameInfo.filename.offset,
                   filename, len(filename))
    source_name = _extended(str(source.absolute()))
    handle = create_file(source_name, 0x10000, 0x1 | 0x2 | 0x4, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not rename(handle, 22, buffer, len(buffer)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        close_handle(handle)


def _open_snapshot(path: Path) -> BinaryIO:
    """读取已经打开的完整版本，允许另一进程同时原子替换路径。

    Windows 默认文件打开方式不共享删除权限，会让并章写入的 os.replace 偶发失败。
    账只以原子替换发布，因此读句柄保留旧文件即可；不需要读锁或重试，也不阻塞新版本。
    """
    if os.name != 'nt':
        return path.open('rb')

    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    filename = str(path.absolute())
    if not filename.startswith('\\\\?\\'):
        filename = ('\\\\?\\UNC\\' + filename[2:] if filename.startswith('\\\\')
                    else '\\\\?\\' + filename)
    handle = create_file(
        filename, 0x80000000, 0x1 | 0x2 | 0x4, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        # open_osfhandle 成功后由 fd 接管句柄；失败时仍由本函数负责关闭。
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close_handle(handle)
        raise
    try:
        return os.fdopen(fd, 'rb')
    except BaseException:
        os.close(fd)
        raise


def _read(base: str | os.PathLike[str], path: Path, model: type[BaseModel]) -> Any:
    target = _safe(base, path)
    try:
        with _open_snapshot(target) as stream:
            return model.model_validate_json(stream.read())
    except (OSError, ValueError) as exc:
        raise WorkUnitRefused(f'工作单元账缺失或不符合当前契约：{target.name}') from exc


def workflow_manifest(base: str | os.PathLike[str]) -> (
        WorkflowManifestV7 | WorkflowManifestV8 | WorkflowManifestV9 | None):
    path = _safe(base, paths.workflow_manifest_path(base))
    if not path.exists():
        if paths.workflow_transition_pending_path(base).exists():
            raise WorkUnitRefused('已有迁移 prepared 但 manifest 缺失；禁止初始化新任务')
        return None
    with _open_snapshot(path) as stream:
        raw = stream.read()
    try:
        version = json.loads(raw)['schema_version']
    except (TypeError, ValueError, KeyError) as exc:
        raise WorkUnitRefused('工作流 manifest 版本不可读') from exc
    model = {
        9: WorkflowManifestV9,
        8: WorkflowManifestV8,
        7: WorkflowManifestV7,
    }.get(version)
    if model is None:
        raise WorkUnitRefused(f'不支持的工作流 manifest schema_version={version!r}')
    manifest = _read(base, path, model)
    from novel.orchestrator.workflow_transition import require_transition_completed
    if model.model_validate_json(raw) != manifest:
        raise WorkUnitRefused('读取工作流 manifest 时身份变化')
    require_transition_completed(Path(base), manifest, raw)
    return manifest


def _require_clean_base(base: str | os.PathLike[str]) -> None:
    root = Path(base)
    if root.exists():
        for book in root.iterdir():
            if not book.is_dir() or book.name.startswith('.') or book.name == '_service':
                continue
            if any(child.is_dir() and re.fullmatch(r'stage[0-7]', child.name)
                   and any(child.iterdir()) for child in book.iterdir()):
                raise WorkUnitRefused(
                    '此 base 已有旧工作流产物；请用冻结旧版本恢复，'
                    '新工作流必须使用新的 clean base')
        service = paths.service_dir(base)
        for name in ('ledger.jsonl', 'agent_sessions.jsonl'):
            old = service / name
            if old.exists() and old.stat().st_size:
                raise WorkUnitRefused('此 base 已有旧调用账，禁止初始化空内容预算')
        runs = service / 'runs'
        if runs.exists() and any(runs.iterdir()):
            raise WorkUnitRefused('此 base 已有旧运行记录，禁止初始化新工作流')


def check_workflow_write(base: str | os.PathLike[str]) -> None:
    """纯读预检；在 admission 等服务写副作用之前拒绝冻结旧 base。

    本仓没有通用迁移机制：v7 起删除 Stage2 语义审阅与 KnowledgeLinks，
    旧 v4~v6 manifest 不迁移，旧输出与成本账原样保留，由冻结旧代码读取。
    """
    if workflow_manifest(base) is None:
        _require_clean_base(base)


def initialize_workflow(base: str | os.PathLike[str]) -> WorkflowManifestV8:
    """仅空 base 可初始化；旧书不能靠删账或换代码获得新额度。"""
    target = _safe(base, paths.workflow_manifest_path(base))
    # 拒绝存量 base 必须零写；锁本身会创建父目录，因此先作只读预检。
    if workflow_manifest(base) is None:
        _require_clean_base(base)
    with path_transaction_lock(target):
        existing = workflow_manifest(base)
        if existing is not None:
            if not isinstance(existing, WorkflowManifestV8):
                raise WorkUnitRefused(
                    '存量 workflow manifest 不是 v8；STR-03-S4 不迁移，请使用 clean base')
            return existing
        _require_clean_base(base)
        manifest = WorkflowManifestV8(task_id=hashlib.sha256(uuid4().bytes).hexdigest(),
                                      created_at=int(time.time()))
        _write(base, target, manifest)
        return manifest


def require_workflow(base: str | os.PathLike[str]) -> WorkflowManifestV7 | WorkflowManifestV8:
    manifest = workflow_manifest(base)
    if manifest is None:
        return initialize_workflow(base)
    return manifest


# ─── 整书级付费内容调用总闸 ────────────────────────────────────────────────

def _book_budget_path(base: str | os.PathLike[str], sid: str) -> Path:
    return _safe(base, paths.book_budget_path(base, sid))


def read_book_budget(
    base: str | os.PathLike[str], sid: str, *, task_id: str | None = None,
) -> BookContentBudgetV1 | None:
    """只读整书总闸账；缺账返回 None，坏账关闭式上抛，不把坏账当零。"""
    path = _book_budget_path(base, sid)
    if not path.is_file():
        return None
    budget = _read(base, path, BookContentBudgetV1)
    if task_id is not None and budget.task_id != task_id:
        raise WorkUnitRefused('整书预算账任务身份漂移')
    return budget


def _reserve_book_call(base: str | os.PathLike[str], identity: dict) -> None:
    """在已持有的单元账锁内对整书总闸 +1；锁序恒为 单元账 → 整书账。

    账在首次付费调用时创建；重启/恢复/重建 job 读的是同一份持久账，
    已消耗额度不重置。坏账关闭式拒绝新调用（Q-13：付费副作用状态未知
    不得绕过）。
    """
    path = _book_budget_path(base, identity['sid'])
    with path_transaction_lock(path):
        budget = read_book_budget(
            base, identity['sid'], task_id=identity['task_id'])
        if budget is None:
            budget = BookContentBudgetV1(
                task_id=identity['task_id'], sid=identity['sid'],
                ceiling=BOOK_CONTENT_CALL_CEILING_DEFAULT)
        if budget.remaining <= 0:
            raise WorkUnitBudgetExhausted(
                f'整书付费内容调用总闸已耗尽（{budget.used}/'
                f'{budget.ceiling + sum(g.additional for g in budget.grants)}）——'
                '这是跨全部工作单元的合计上限；请显式追加整书预算后再续跑，'
                '自动恢复路径不得追加')
        _write(base, path, budget.model_copy(update={'used': budget.used + 1}))


def grant_book_budget(
    base: str | os.PathLike[str], sid: str, *,
    actor: str, reason: str, additional: int,
    task_id: str | None = None,
) -> BookContentBudgetV1:
    """整书总闸的显式人工追加；唯一合法调用方是人工 HTTP/CLI 入口。"""
    if not actor.strip() or not reason.strip():
        raise WorkUnitRefused('整书预算追加必须显式给非空 actor 与 reason')
    if not 1 <= additional <= 64:
        raise WorkUnitRefused('整书预算单次追加必须在 1~64 之间')
    manifest = require_workflow(base)
    path = _book_budget_path(base, sid)
    with path_transaction_lock(path):
        budget = read_book_budget(base, sid, task_id=task_id)
        if budget is None:
            budget = BookContentBudgetV1(
                task_id=manifest.task_id, sid=sid,
                ceiling=BOOK_CONTENT_CALL_CEILING_DEFAULT)
        grant = BookBudgetGrantV1(
            actor=actor, reason=reason, additional=additional,
            created_at=int(time.time()), before_used=budget.used)
        updated = budget.model_copy(update={'grants': (*budget.grants, grant)})
        _write(base, path, updated)
        return updated


@dataclass(frozen=True)
class ContentReservation:
    unit_id: str
    turn_id: str
    ordinal: int


class WorkUnitBudget:
    def __init__(self, base: str | os.PathLike[str], sid: str, node: str,
                 role: str = 'executor', scope: str = 'book', *, read_only: bool = False) -> None:
        if node not in NODES or role not in ('executor', 'commander'):
            raise WorkUnitRefused('未登记的工作单元节点或角色')
        if role == 'commander' and not read_only:
            # Commander 受托审阅已退役（2026-09-15）：新工作单元不再接受
            # commander 角色；``read_only=True`` 仅服务存量书历史账的解码与
            # owner 停止证明，不参与任何新预算。
            raise WorkUnitRefused('Commander 工作单元已退役；历史账只读解码仍可用')
        spec = NODES[node].lifecycle
        if role == 'commander':
            # 原 ``spec.commander_content_limit == 4`` 的准入面随字段退役；
            # 历史解码以现存的审批登记（approval_bindings）与已退役闸的
            # 历史只读表（RETIRED_APPROVAL_BINDINGS）为等价准入证据。
            allowed_scope = approval_scope_allowed(node, scope)
            allowed_role = (
                bool(spec.approval_bindings) or node in RETIRED_APPROVAL_BINDINGS)
        else:
            allowed_role = spec.executor_content_limit == 4
            chapter_scope = bool(re.fullmatch(r'chapter:[1-9][0-9]*', scope))
            allowed_scope = (
                spec.unit_kind in ('book', 'candidate_batch') and scope == 'book'
                or spec.unit_kind in ('chapter', 'ambiguous_chapter') and chapter_scope
                or spec.unit_kind == 'strategy_and_chapter' and (scope == 'strategy' or chapter_scope)
                )
        if not allowed_role or not allowed_scope:
            raise WorkUnitRefused('模型角色或工作单元范围未在节点生命周期登记')
        if not scope or len(scope) > 160 or any(ord(c) < 32 for c in scope):
            raise WorkUnitRefused('工作单元范围非法')
        self.base = Path(base)
        manifest = workflow_manifest(base) if read_only else require_workflow(base)
        if manifest is None:
            raise WorkUnitRefused('只读工作单元缺现役 workflow，不能初始化新账')
        self.identity = dict(task_id=manifest.task_id, sid=paths.check_sid(sid),
                             node=node, role=role, scope=scope)
        self.unit_id = _digest(self.identity)
        self.directory = _safe(base, paths.work_unit_dir(base, sid, self.unit_id))
        self.path = self.directory / 'ledger.json'
        self.registry_path = _safe(base, paths.work_unit_registry_path(base, sid))

    def _registry(self) -> WorkUnitRegistryV1:
        if not self.registry_path.exists():
            return WorkUnitRegistryV1(task_id=self.identity['task_id'], sid=self.identity['sid'])
        registry = _read(self.base, self.registry_path, WorkUnitRegistryV1)
        if registry.task_id != self.identity['task_id'] or registry.sid != self.identity['sid']:
            raise WorkUnitRefused('工作单元登记账身份漂移')
        return registry

    def _read(self) -> WorkUnitLedgerV2:
        if not self.path.exists():
            if self.unit_id in self._registry().unit_ids:
                raise WorkUnitRefused('已登记的工作单元预算账缺失，禁止重新发放额度')
            # 非账文件意味着丢账，不能以新对象覆盖已保存的回答。
            if self.directory.exists() and any(
                    p.name != 'ledger.json.lock' for p in self.directory.iterdir()):
                raise WorkUnitRefused('工作单元已有内容但预算账缺失')
            return WorkUnitLedgerV2(unit_id=self.unit_id, **self.identity)
        ledger = _read(self.base, self.path, WorkUnitLedgerV2)
        if ledger.unit_id != self.unit_id or any(
                getattr(ledger, key) != val for key, val in self.identity.items()):
            raise WorkUnitRefused('工作单元账身份不一致')
        for row in ledger.processing:
            if self._processing(**row.model_dump(exclude={'processing_id'}, exclude_none=True)) != row:
                raise WorkUnitRefused('工作单元处理来源回执损坏，禁止新调用')
        for turn in ledger.turns:
            if turn.reason_code == COMMANDER_FAILURE_RECONCILED:
                self.failure_recovery_receipt(ledger, turn)
        return ledger

    def failure_recovery_receipt(
            self, ledger: WorkUnitLedgerV2,
            turn: WorkUnitContentTurn) -> CommanderFailureRecoveryReceipt:
        """历史失败一旦已收口，其原失败和工程裁决证明仍必须可验证。"""
        found = [row for row in ledger.processing if row.kind == 'mechanical'
                 and row.input_sha256 == failure_recovery_input(turn.turn_id)]
        if len(found) != 1 or found[0].run_id != turn.run_id:
            raise WorkUnitRefused('Commander 失败收口缺唯一工程证明')
        row = found[0]
        try:
            target = _safe(self.base, self.directory / f'{row.evidence_sha256}.json')
            with _open_snapshot(target) as stream:
                raw = stream.read()
            if hashlib.sha256(raw).hexdigest() != row.evidence_sha256:
                raise ValueError('失败收口证明字节身份漂移')
            blob = WorkUnitBlobV1.model_validate_json(raw)
            receipt = CommanderFailureRecoveryReceipt.model_validate(blob.payload)
            expected = receipt.before.model_copy(update={
                'state': 'failed', 'reason_code': COMMANDER_FAILURE_RECONCILED})
            original = WorkUnitLedgerV2.model_validate(receipt.evidence['ledger'])
            if (_digest(blob.model_dump(mode='json')) != row.evidence_sha256
                    or blob.unit_id != self.unit_id or blob.turn_id != turn.turn_id
                    or receipt.unit_id != ledger.unit_id or receipt.sid != ledger.sid
                    or ledger.role != 'commander' or expected != turn
                    or original.unit_id != ledger.unit_id
                    or self._turn(original, turn.turn_id) != receipt.before
                    or _digest(receipt.evidence) != receipt.request.preview_token):
                raise ValueError('失败收口证明主体不一致')
        except (OSError, ValueError, KeyError) as exc:
            raise WorkUnitRefused('Commander 失败收口证明损坏，禁止新调用') from exc
        return receipt

    def reconcile_failure_under_lock(self, receipt: CommanderFailureRecoveryReceipt) -> None:
        """调用方已持有本账事务锁及 owner lease；不重入不支持重入的文件锁。"""
        ledger = self._read()
        turn = self._turn(ledger, receipt.before.turn_id)
        if receipt.unit_id != self.unit_id or receipt.sid != ledger.sid or turn != receipt.before:
            raise WorkUnitRefused('失败收口写入前主体已漂移')
        blob = WorkUnitBlobV1(unit_id=self.unit_id, turn_id=turn.turn_id,
                              payload=receipt.model_dump(mode='json'))
        digest = _digest(blob.model_dump(mode='json'))
        _write(self.base, self.directory / f'{digest}.json', blob)
        processing = self._processing(run_id=turn.run_id, kind='mechanical',
            input_sha256=failure_recovery_input(turn.turn_id), evidence_sha256=digest)
        failed = turn.model_copy(update={'state': 'failed', 'reason_code': COMMANDER_FAILURE_RECONCILED})
        updated = WorkUnitLedgerV2.model_validate({**ledger.model_dump(mode='json'),
            'turns': [failed if row.turn_id == turn.turn_id else row for row in ledger.turns],
            'processing': [*ledger.processing, processing]})
        self.failure_recovery_receipt(updated, failed)
        _write(self.base, self.path, updated)

    def _replace(self, ledger: WorkUnitLedgerV2, turn: WorkUnitContentTurn) -> None:
        rows = [row.model_dump(mode='json') if row.turn_id != turn.turn_id
                else turn.model_dump(mode='json') for row in ledger.turns]
        updated = WorkUnitLedgerV2.model_validate({
            **ledger.model_dump(mode='json'), 'turns': rows})
        _write(self.base, self.path, updated)

    def _turn(self, ledger: WorkUnitLedgerV2, turn_id: str) -> WorkUnitContentTurn:
        found = [t for t in ledger.turns if t.turn_id == turn_id]
        if len(found) != 1:
            raise WorkUnitRefused('内容轮不属于当前工作单元')
        return found[0]

    @property
    def used(self) -> int:
        return len(self._read().turns)

    @property
    def remaining(self) -> int:
        ledger = self._read()
        return ledger.content_limit - len(ledger.turns)

    @property
    def granted(self) -> int:
        ledger = self._read()
        return ledger.content_limit - ledger.base_content_limit

    def reserve(self, request_sha256: str, *, run_id: str,
                session_run_id: str = '',
                request_payload: dict[str, Any] | None = None) -> ContentReservation:
        with path_transaction_lock(self.path):
            ledger = self._read()
            if ledger.group_producer_sha256 is not None and not ledger.group_producer_completed:
                raise WorkUnitRefused('已有整组候选待补提交；须先免费恢复，不能重新调用模型')
            if any(t.state in ('reserved', 'unknown', 'delivered') for t in ledger.turns):
                raise WorkUnitRefused('已有未闭合内容请求；须先对账，不能重新调用模型')
            if len(ledger.turns) >= ledger.content_limit:
                raise WorkUnitBudgetExhausted(
                    f'本工作单元内容预算已耗尽（{len(ledger.turns)}/{ledger.content_limit}），'
                    '请免费恢复、人工编辑或明确申请追加额度')
            # 整书总闸在单元账写入之前判：被拒时单元账不留下未闭合的
            # reserved 轮；反向（总闸已 +1 而单元账写失败）只是保守多计
            # 一次，不构成付费风险。
            _reserve_book_call(self.base, self.identity)
            turn = WorkUnitContentTurn(
                turn_id=uuid4().hex, ordinal=len(ledger.turns) + 1,
                request_sha256=request_sha256, run_id=run_id,
                session_run_id=session_run_id, created_at=int(time.time()))
            if request_payload is not None:
                blob = WorkUnitBlobV1(unit_id=self.unit_id, turn_id=turn.turn_id,
                                      payload=request_payload)
                digest = _digest(blob.model_dump(mode='json'))
                # 账先持久，避免工作包落盘后崩溃把未知预算洗成空账。
                turn = turn.model_copy(update={'request_payload_sha256': digest})
            updated = WorkUnitLedgerV2.model_validate({
                **ledger.model_dump(mode='json'),
                'turns': [*ledger.turns, turn],
                'processing': [*ledger.processing, self._processing(
                    run_id=run_id, input_sha256=request_sha256, kind='model',
                    turn_id=turn.turn_id)]})
            with path_transaction_lock(self.registry_path):
                registry = self._registry()
                if self.unit_id not in registry.unit_ids:
                    _write(self.base, self.registry_path, registry.model_copy(update={
                        'unit_ids': (*registry.unit_ids, self.unit_id)}))
            _write(self.base, self.path, updated)
            if request_payload is not None:
                try:
                    _write(self.base, self.directory / f'{digest}.json', blob)
                except BaseException:
                    # 账已含 reserved；blob 落盘失败必须立刻关闭该轮，否则永久挡住新调用。
                    self._replace(
                        updated,
                        turn.model_copy(update={
                            'state': 'failed',
                            'reason_code': 'request_payload_write_failed',
                        }),
                    )
                    raise
            return ContentReservation(self.unit_id, turn.turn_id, turn.ordinal)

    def _processing(self, **values: Any) -> WorkUnitProcessing:
        material = {'unit_id': self.unit_id, **values}
        return WorkUnitProcessing(processing_id=_digest(material), **values)

    def note_processing(self, *, run_id: str, input_sha256: str,
                        kind: str, evidence_sha256: str) -> None:
        """producer 完成来源复验后记录免费处理，重放不重复计数或重发额度。"""
        if kind == 'model':
            raise WorkUnitRefused('模型处理只能由首 wire 前 reserve 登记')
        proposed = self._processing(run_id=run_id, input_sha256=input_sha256,
                                    kind=kind, evidence_sha256=evidence_sha256)
        with path_transaction_lock(self.path):
            ledger = self._read()
            if proposed in ledger.processing:
                return
            with path_transaction_lock(self.registry_path):
                registry = self._registry()
                if self.unit_id not in registry.unit_ids:
                    _write(self.base, self.registry_path, registry.model_copy(update={
                        'unit_ids': (*registry.unit_ids, self.unit_id)}))
            _write(self.base, self.path, WorkUnitLedgerV2.model_validate({
                **ledger.model_dump(mode='json'),
                'processing': [*ledger.processing, proposed]}))

    def record_attempt(self, turn_id: str, *, attempt_id: str, outcome: str,
                       provider_session_id: str | None = None,
                       request_fingerprint: str | None = None) -> None:
        with path_transaction_lock(self.path):
            ledger = self._read()
            turn = self._turn(ledger, turn_id)
            previous = next((a for a in turn.attempts if a.attempt_id == attempt_id), None)
            row = WorkUnitAttempt(attempt_id=attempt_id, outcome=outcome,
                                  provider_session_id=provider_session_id,
                                  request_fingerprint=(request_fingerprint or
                                      (previous.request_fingerprint if previous else None)))
            if previous is not None:
                if previous == row:
                    return
                if previous.outcome != 'started':
                    raise WorkUnitRefused('网络尝试终态不能覆盖')
            attempts = tuple(a for a in turn.attempts if a.attempt_id != attempt_id) + (row,)
            self._replace(ledger, turn.model_copy(update={'attempts': attempts}))

    def save_response(self, turn_id: str, payload: dict[str, Any]) -> str:
        with path_transaction_lock(self.path):
            ledger = self._read()
            turn = self._turn(ledger, turn_id)
            blob = WorkUnitBlobV1(unit_id=self.unit_id, turn_id=turn_id, payload=payload)
            digest = _digest(blob.model_dump(mode='json'))
            if turn.response_sha256 not in (None, digest):
                raise WorkUnitRefused('同一次内容请求不能覆盖为另一个回答')
            # 先记不可变目标再写完整回答；回答已写、状态未提交的窗口可精确补尾。
            if turn.response_sha256 is None:
                self._replace(ledger, turn.model_copy(update={'response_sha256': digest}))
                ledger = self._read()
                turn = self._turn(ledger, turn_id)
            _write(self.base, self.directory / f'{digest}.json', blob)
            self._replace(ledger, turn.model_copy(update={
                'response_sha256': digest, 'state': 'delivered'}))
            return digest

    def finish(self, turn_id: str, *, outcome: str = 'delivered', reason_code: str = '') -> None:
        if outcome not in ('delivered', 'failed', 'unknown'):
            raise WorkUnitRefused('未登记的内容请求终态')
        with path_transaction_lock(self.path):
            ledger = self._read()
            turn = self._turn(ledger, turn_id)
            if turn.state == 'adopted':
                return
            # 完整回答已经落盘后，即使后继账面异常也不能丢掉恢复入口。
            response_exists = (turn.response_sha256 is not None
                               and (self.directory / f'{turn.response_sha256}.json').is_file())
            state = 'delivered' if response_exists else outcome
            self._replace(ledger, turn.model_copy(update={'state': state, 'reason_code': reason_code}))

    def pending_response(self, request_sha256: str) -> tuple[ContentReservation, dict[str, Any]] | None:
        with path_transaction_lock(self.path):
            ledger = self._read()
            turns = [t for t in ledger.turns if t.request_sha256 == request_sha256
                     and t.state in ('delivered', 'reserved', 'unknown') and t.response_sha256]
            if not turns:
                return None
            turn = turns[-1]
            blob = _read(self.base, self.directory / f'{turn.response_sha256}.json', WorkUnitBlobV1)
            if (_digest(blob.model_dump(mode='json')) != turn.response_sha256
                    or blob.unit_id != self.unit_id or blob.turn_id != turn.turn_id):
                raise WorkUnitRefused('已保存模型回答的内容或身份漂移')
            if turn.state != 'delivered':
                self._replace(ledger, turn.model_copy(update={'state': 'delivered'}))
            return ContentReservation(self.unit_id, turn.turn_id, turn.ordinal), blob.payload

    def latest_pending_response(self) -> tuple[ContentReservation, dict[str, Any]] | None:
        """只返回尚未提交的完整回答；候选变化不允许把它默默当作不存在。"""
        ledger = self._read()
        rows = [turn for turn in ledger.turns if turn.state in ('delivered', 'reserved', 'unknown')
                and turn.response_sha256]
        if not rows:
            return None
        return self.pending_response(rows[-1].request_sha256)

    def recovery_proof(self, turn_id: str) -> dict[str, Any]:
        ledger = self._read()
        turn = self._turn(ledger, turn_id)
        if not turn.recovery_sha256 or not turn.response_sha256:
            raise WorkUnitRefused('工作单元缺原始回答或 typed 决定证明')
        blob = _read(self.base, self.directory / f'{turn.recovery_sha256}.json', WorkUnitBlobV1)
        if (_digest(blob.model_dump(mode='json')) != turn.recovery_sha256
                or blob.unit_id != self.unit_id or blob.turn_id != turn_id):
            raise WorkUnitRefused('工作单元 typed 决定证明身份漂移')
        return blob.payload

    def turn(self, turn_id: str) -> WorkUnitContentTurn:
        return self._turn(self._read(), turn_id)

    def latest_turn(self) -> WorkUnitContentTurn | None:
        ledger = self._read()
        return ledger.turns[-1] if ledger.turns else None

    def response_payload(self, turn_id: str) -> dict[str, Any]:
        turn = self.turn(turn_id)
        if turn.response_sha256 is None:
            raise WorkUnitRefused('工作单元尚无完整响应')
        blob = _read(self.base, self.directory / f'{turn.response_sha256}.json', WorkUnitBlobV1)
        if (_digest(blob.model_dump(mode='json')) != turn.response_sha256
                or blob.unit_id != self.unit_id or blob.turn_id != turn_id):
            raise WorkUnitRefused('工作单元响应身份不一致')
        return blob.payload

    def request_payload(self, turn_id: str) -> dict[str, Any]:
        turn = self.turn(turn_id)
        if turn.request_payload_sha256 is None:
            raise WorkUnitRefused('工作单元缺原始请求工作包')
        blob = _read(self.base, self.directory / f'{turn.request_payload_sha256}.json', WorkUnitBlobV1)
        if (_digest(blob.model_dump(mode='json')) != turn.request_payload_sha256
                or blob.unit_id != self.unit_id or blob.turn_id != turn_id):
            raise WorkUnitRefused('工作单元请求身份不一致')
        return blob.payload

    def recovery_kinds_used(self) -> dict[str, int]:
        """按请求工作包的 ``recovery_kind`` 统计已用恢复槽位。

        这是 issues 协议 job 级持久计数器的唯一来源：跨重启、换 session、
        换 epoch 与范围修订保持累计，不能在任何一层重新获得恢复次数。
        """
        counts: dict[str, int] = {}
        for turn in self._read().turns:
            if turn.request_payload_sha256 is None:
                continue
            try:
                payload = self.request_payload(turn.turn_id)
            except WorkUnitRefused:
                continue
            kind = payload.get('recovery_kind')
            if isinstance(kind, str) and kind:
                counts[kind] = counts.get(kind, 0) + 1
        return counts

    def mark_adopted(self, turn_id: str) -> None:
        with path_transaction_lock(self.path):
            ledger = self._read()
            turn = self._turn(ledger, turn_id)
            if not turn.response_sha256:
                raise WorkUnitRefused('没有完整响应不能标记采纳')
            self._replace(ledger, turn.model_copy(update={'state': 'adopted'}))

    def mark_artifact_committed(self, artifact: str, fingerprint: str) -> None:
        """producer 正式落盘后的组级进度，和模型候选 checkpoint 分开。"""
        with path_transaction_lock(self.path):
            ledger = self._read()
            _write(self.base, self.path, ledger.model_copy(update={
                'committed_through': len(ledger.turns), 'committed_artifact': artifact,
                'committed_fingerprint': fingerprint}))

    def unfinished_content_ids(self) -> frozenset[str]:
        ledger = self._read()
        if ledger.committed_through >= len(ledger.turns):
            return frozenset()
        return frozenset(turn.turn_id for turn in ledger.turns
                         if turn.ordinal > ledger.committed_through)

    def save_recovery(self, turn_id: str, payload: dict[str, Any]) -> str:
        """响应校验后的恢复证明独立保存，不覆盖原始模型回答。"""
        with path_transaction_lock(self.path):
            ledger = self._read()
            turn = self._turn(ledger, turn_id)
            if not turn.response_sha256:
                raise WorkUnitRefused('保存恢复证明前必须有原始回答')
            blob = WorkUnitBlobV1(unit_id=self.unit_id, turn_id=turn_id, payload=payload)
            digest = _digest(blob.model_dump(mode='json'))
            if turn.recovery_sha256 not in (None, digest):
                raise WorkUnitRefused('不可覆盖已有恢复证明')
            _write(self.base, self.directory / f'{digest}.json', blob)
            self._replace(ledger, turn.model_copy(update={'recovery_sha256': digest}))
            return digest

    def save_producer_recovery(self, turn_id: str, payload: dict[str, Any]) -> str:
        """正式提交前保存 producer 的精确目标，独立于模型响应和 typed 解析。"""
        with path_transaction_lock(self.path):
            ledger = self._read()
            turn = self._turn(ledger, turn_id)
            if not turn.response_sha256:
                raise WorkUnitRefused('保存 producer 提交证明前必须有完整回答')
            blob = WorkUnitBlobV1(unit_id=self.unit_id, turn_id=turn_id, payload=payload)
            digest = _digest(blob.model_dump(mode='json'))
            if turn.producer_sha256 not in (None, digest):
                raise WorkUnitRefused('不可覆盖已有 producer 提交目标')
            _write(self.base, self.directory / f'{digest}.json', blob)
            self._replace(ledger, turn.model_copy(update={'producer_sha256': digest}))
            return digest

    def producer_recovery(self, turn_id: str) -> dict[str, Any] | None:
        return self._producer_recovery(self.turn(turn_id))

    def _producer_recovery(self, turn: WorkUnitContentTurn) -> dict[str, Any] | None:
        if turn.producer_sha256 is None:
            return None
        blob = _read(self.base, self.directory / f'{turn.producer_sha256}.json', WorkUnitBlobV1)
        if (_digest(blob.model_dump(mode='json')) != turn.producer_sha256
                or blob.unit_id != self.unit_id or blob.turn_id != turn.turn_id
                or not turn.response_sha256):
            raise WorkUnitRefused('producer 提交证明身份漂移')
        return blob.payload

    def producer_recoveries(self) -> tuple[tuple[str, dict[str, Any]], ...]:
        """按轮次倒序读取已保存提交目标；最新失败不能遮蔽此前完整候选。

        每份目标仍经同一摘要与单元身份校验，损坏时停止，不跳过坏证明。
        """
        ledger = self._read()
        result = []
        for turn in reversed(ledger.turns):
            if turn.producer_sha256 is None:
                continue
            payload = self._producer_recovery(turn)
            if payload is None:
                raise WorkUnitRefused('producer 提交证明在读取期间缺失')
            result.append((turn.turn_id, payload))
        return tuple(result)

    def save_group_producer_recovery(self, payload: dict[str, Any]) -> str:
        """整组已完成候选的提交锚；纯缓存结果不创建内容轮、不消耗模型额度。"""
        with path_transaction_lock(self.path):
            ledger = self._read()
            blob = WorkUnitGroupBlobV1(unit_id=self.unit_id, payload=payload)
            digest = _digest(blob.model_dump(mode='json'))
            if ledger.group_producer_sha256 not in (None, digest):
                raise WorkUnitRefused('不可覆盖尚未退休的整组提交证明')
            if ledger.group_producer_sha256 == digest:
                # 重试只能验证原证明；不能用内存里的副本洗白丢失或损坏的恢复盘面。
                self._group_producer_recovery(ledger)
                return digest
            with path_transaction_lock(self.registry_path):
                registry = self._registry()
                if self.unit_id not in registry.unit_ids:
                    _write(self.base, self.registry_path, registry.model_copy(update={
                        'unit_ids': (*registry.unit_ids, self.unit_id)}))
            # 先记待提交身份；若 blob 尚未完整保存，恢复必须明确停止，不能购买替代组。
            _write(self.base, self.path, ledger.model_copy(update={'group_producer_sha256': digest}))
            _write(self.base, self.directory / f'{digest}.json', blob)
            return digest

    def group_producer_recovery(self) -> dict[str, Any] | None:
        """只读原整组提交证明；缺失、串单元或损坏均不得当成没执行。"""
        return self._group_producer_recovery(self._read())

    def _group_producer_recovery(self, ledger: WorkUnitLedgerV2) -> dict[str, Any] | None:
        # 读取原子账快照及不可变 blob 不需要写锁；写侧可在已持有的事务锁内复用，
        # 避免不可重入 OS 锁死锁，也不让 API 查询创建不存在的单元或 .lock 文件。
        digest = ledger.group_producer_sha256
        if digest is None:
            return None
        blob = _read(self.base, self.directory / f'{digest}.json', WorkUnitGroupBlobV1)
        if blob.unit_id != self.unit_id or _digest(blob.model_dump(mode='json')) != digest:
            raise WorkUnitRefused('整组提交证明身份漂移')
        return blob.payload

    @property
    def group_producer_recovery_sha256(self) -> str | None:
        """仅返回已经通过完整身份校验的提交锚，供确定性恢复 action 绑定。"""
        ledger = self._read()
        self._group_producer_recovery(ledger)
        return ledger.group_producer_sha256

    @property
    def group_producer_completed(self) -> bool:
        """读取已验证原证明的完成标记；生产者仍须核对原始采纳全集。"""
        ledger = self._read()
        self._group_producer_recovery(ledger)
        return ledger.group_producer_completed

    def retire_group_producer_recovery(self, expected_payload: dict[str, Any]) -> None:
        """producer 证明原目标及全部采纳完成后 CAS 退休；保留原 blob 与模型额度。"""
        with path_transaction_lock(self.path):
            ledger = self._read()
            blob = WorkUnitGroupBlobV1(unit_id=self.unit_id, payload=expected_payload)
            expected = _digest(blob.model_dump(mode='json'))
            if ledger.group_producer_sha256 != expected:
                raise WorkUnitRefused('整组提交证明在退休前变化')
            if not ledger.group_producer_completed:
                raise WorkUnitRefused('未完成的整组提交证明不能退休或被新组覆盖')
            self._group_producer_recovery(ledger)
            _write(self.base, self.path, ledger.model_copy(update={
                'group_producer_sha256': None, 'group_producer_completed': False}))

    def complete_group_producer_recovery(self, expected_payload: dict[str, Any]) -> None:
        """producer 核实正式目标及原采纳全集后标记完成，保留可恢复候选。"""
        with path_transaction_lock(self.path):
            ledger = self._read()
            blob = WorkUnitGroupBlobV1(unit_id=self.unit_id, payload=expected_payload)
            if ledger.group_producer_sha256 != _digest(blob.model_dump(mode='json')):
                raise WorkUnitRefused('整组提交证明在完成前变化')
            self._group_producer_recovery(ledger)
            if not ledger.group_producer_completed:
                _write(self.base, self.path, ledger.model_copy(update={'group_producer_completed': True}))

    def pending_recovery(self, request_sha256: str) -> tuple[ContentReservation, dict[str, Any]] | None:
        with path_transaction_lock(self.path):
            ledger = self._read()
            rows = [t for t in ledger.turns if t.request_sha256 == request_sha256
                    and t.recovery_sha256 and t.state == 'delivered']
            if not rows:
                return None
            turn = rows[-1]
            blob = _read(self.base, self.directory / f'{turn.recovery_sha256}.json', WorkUnitBlobV1)
            if (_digest(blob.model_dump(mode='json')) != turn.recovery_sha256
                    or blob.unit_id != self.unit_id or blob.turn_id != turn.turn_id):
                raise WorkUnitRefused('模型恢复证明身份不闭合')
            return ContentReservation(self.unit_id, turn.turn_id, turn.ordinal), blob.payload

    def mark_rejected(self, turn_id: str, *, reason_code: str = 'content_rejected') -> None:
        """内容拒绝保留原回答，只关闭自动重放，不返还轮次。"""
        with path_transaction_lock(self.path):
            ledger = self._read()
            turn = self._turn(ledger, turn_id)
            self._replace(ledger, turn.model_copy(update={'state': 'failed', 'reason_code': reason_code}))

    def conversation_history(self, *, system: str, logical_request_sha256: str) -> tuple[tuple[str, str], ...]:
        """只读完整已保存的同协议历史；恢复同一个请求不把其回答追加到输入。"""
        ledger = self._read()
        history: list[tuple[str, str]] = []
        for turn in ledger.turns:
            if not turn.request_payload_sha256 or not turn.response_sha256:
                continue
            request = _read(self.base, self.directory / f'{turn.request_payload_sha256}.json', WorkUnitBlobV1)
            response = _read(self.base, self.directory / f'{turn.response_sha256}.json', WorkUnitBlobV1)
            if (_digest(request.model_dump(mode='json')) != turn.request_payload_sha256
                    or _digest(response.model_dump(mode='json')) != turn.response_sha256):
                raise WorkUnitRefused('会话历史字节身份漂移')
            req = request.payload
            if req.get('system') != system or req.get('logical_request_sha256') == logical_request_sha256:
                continue
            reply = response.payload.get('response', {})
            content = reply.get('content') if isinstance(reply, dict) else None
            if isinstance(req.get('user'), str) and isinstance(content, str) and content:
                history.extend((('user', req['user']), ('assistant', content)))
        return tuple(history)

    def recovery_attempts(self, turn_id: str) -> tuple[dict[str, Any], ...]:
        """历史采用只授权本账户内、截至目标回答的实际尝试。"""
        ledger = self._read()
        target = self._turn(ledger, turn_id)
        if not target.response_sha256:
            raise WorkUnitRefused('历史采用必须绑定完整保存的回答')
        return tuple(dict(attempt_id=attempt.attempt_id, run_id=turn.run_id,
                          request_fingerprint=attempt.request_fingerprint or turn.request_sha256,
                          sid=ledger.sid, node=ledger.node)
                     for turn in ledger.turns if turn.ordinal <= target.ordinal
                     for attempt in turn.attempts if attempt.outcome not in ('started', 'not_started'))

    def mark_adopted_for_attempt(self, attempt_id: str) -> None:
        ledger = self._read()
        rows = [turn for turn in ledger.turns
                if any(a.attempt_id == attempt_id for a in turn.attempts)]
        if len(rows) != 1:
            raise WorkUnitRefused('checkpoint 采纳缺唯一工作单元尝试')
        self.mark_adopted(rows[0].turn_id)

    def save_feedback(self, turn_id: str, payload: dict[str, Any]) -> None:
        with path_transaction_lock(self.path):
            ledger = self._read()
            turn = self._turn(ledger, turn_id)
            blob = WorkUnitBlobV1(unit_id=self.unit_id, turn_id=turn_id, payload=payload)
            digest = _digest(blob.model_dump(mode='json'))
            if turn.feedback_sha256 not in (None, digest):
                raise WorkUnitRefused('不得覆盖已保存的模型拒因')
            _write(self.base, self.directory / f'{digest}.json', blob)
            self._replace(ledger, turn.model_copy(update={
                'feedback_sha256': digest, 'state': 'failed', 'reason_code': 'typed_rejected'}))

    def _reviewer_business_matches(
            self, turn_id: str, recorded: Any, current: str, waiting_on: str) -> bool:
        """旧审阅指纹只经原冻结包投影为当前业务身份；不改写账或拒因。"""
        if recorded == current:
            return True
        from novel.orchestrator import reviewer_legacy

        if (waiting_on not in reviewer_legacy.REVALIDATION_WAITING_ON
                or self.turn(turn_id).request_payload_sha256 is None):
            return False
        request = self.request_payload(turn_id)
        if (request.get('business_sha256') != recorded
                or request.get('waiting_on') != waiting_on):
            return False
        try:
            return reviewer_legacy.request_business_sha256(request) == current
        except reviewer_legacy.RevalidationRefused as exc:
            raise WorkUnitRefused(str(exc)) from exc

    def latest_feedback(self, *, business_sha256: str | None = None) -> dict[str, Any] | None:
        ledger = self._read()
        for turn in reversed(ledger.turns):
            if turn.state == 'adopted':
                return None
            if not turn.feedback_sha256:
                continue
            blob = _read(self.base, self.directory / f'{turn.feedback_sha256}.json', WorkUnitBlobV1)
            if (_digest(blob.model_dump(mode='json')) != turn.feedback_sha256
                    or blob.unit_id != self.unit_id or blob.turn_id != turn.turn_id):
                raise WorkUnitRefused('模型拒因字节身份漂移')
            payload = blob.payload
            if business_sha256 is not None and self._reviewer_business_matches(
                    turn.turn_id, payload.get('business_sha256'), business_sha256,
                    payload.get('waiting_on', '')):
                return {**payload, 'business_sha256': business_sha256}
            return payload
        return None

    def rejected_responses(
            self, *, business_sha256: str, waiting_on: str,
            limit: int = 2) -> tuple[dict[str, Any], ...]:
        """恢复纠错上下文，只读最近同主体的拒因与原回答，不把它们提升为决定。

        每份回答和拒因继续核验原 blob；遇到接受或主体变更就停止回溯，损坏
        则关闭式拒绝。调用方还须按其运输协议核对 feedback 的原回答摘要。
        """
        rows = []
        for turn in reversed(self._read().turns):
            if turn.state == 'adopted' or len(rows) >= limit:
                break
            if not turn.feedback_sha256:
                continue
            blob = _read(self.base, self.directory / f'{turn.feedback_sha256}.json', WorkUnitBlobV1)
            if (_digest(blob.model_dump(mode='json')) != turn.feedback_sha256
                    or blob.unit_id != self.unit_id or blob.turn_id != turn.turn_id):
                raise WorkUnitRefused('纠错拒因的原始身份漂移')
            feedback = blob.payload
            if (feedback.get('waiting_on') != waiting_on
                    or not self._reviewer_business_matches(
                        turn.turn_id, feedback.get('business_sha256'), business_sha256, waiting_on)):
                break
            response = self.response_payload(turn.turn_id)
            if (response.get('waiting_on') != waiting_on
                    or not self._reviewer_business_matches(
                        turn.turn_id, response.get('business_sha256'), business_sha256, waiting_on)
                    or not isinstance(response.get('final_response'), str)
                    or not isinstance(feedback.get('reason'), str)
                    or not isinstance(feedback.get('response_sha256'), str)):
                raise WorkUnitRefused('纠错回答与原拒因的业务身份不一致')
            rows.append({
                'ordinal': turn.ordinal,
                'response_sha256': feedback['response_sha256'],
                'final_response': response['final_response'],
                'reason': feedback['reason'],
            })
        return tuple(reversed(rows))

    def response_for_logical_request(self, seed: str, *, include_completed: bool = False
                                     ) -> tuple[ContentReservation, dict[str, Any]] | None:
        """冻结的请求种子不含后来追加的历史，恢复仍保留原完整请求指纹。"""
        for turn in reversed(self._read().turns):
            if not turn.response_sha256 or not turn.request_payload_sha256:
                continue
            if turn.state not in ('delivered', 'reserved', 'unknown') and not include_completed:
                continue
            blob = _read(self.base, self.directory / f'{turn.request_payload_sha256}.json', WorkUnitBlobV1)
            if (_digest(blob.model_dump(mode='json')) != turn.request_payload_sha256
                    or blob.unit_id != self.unit_id or blob.turn_id != turn.turn_id):
                raise WorkUnitRefused('原始工作包身份漂移')
            if blob.payload.get('logical_request_sha256') == seed:
                if turn.state in ('reserved', 'unknown'):
                    return self.pending_response(turn.request_sha256)
                return ContentReservation(self.unit_id, turn.turn_id, turn.ordinal), self.response_payload(turn.turn_id)
        return None


def budget_for(ctx: RunContext, *, scope: str, role: str = 'executor',
               node: str | None = None) -> WorkUnitBudget:
    return WorkUnitBudget(ctx.novel_dir.parent, ctx.sid, node or ctx.node, role, scope)


def readonly_budget(base: str | os.PathLike[str], sid: str, unit_id: str) -> WorkUnitBudget:
    """观察既有工作单元；缺登记、缺账或身份漂移一律拒绝，不初始化额度。"""
    registry = _read(base, paths.work_unit_registry_path(base, sid), WorkUnitRegistryV1)
    ledger = _read(base, paths.work_unit_dir(base, sid, unit_id) / 'ledger.json', WorkUnitLedgerV2)
    budget = WorkUnitBudget(base, sid, ledger.node, ledger.role, ledger.scope, read_only=True)
    if (registry.sid != sid or registry.task_id != budget.identity['task_id']
            or unit_id not in registry.unit_ids or budget.unit_id != unit_id):
        raise WorkUnitRefused('只读工作单元登记身份不一致')
    budget._read()
    return budget


def reconcile_commander_response(
        budget: WorkUnitBudget, turn_id: str, *, read_only: bool = False) -> None:
    """原始回答的成本/会话尾账必须闭合后才能消费 typed 决定。"""
    from novel.llm import session_audit as agent_session_audit
    from novel.llm.ledger import reconcile_commander_call

    row = budget.turn(turn_id)
    saved = budget.response_payload(turn_id)
    request = budget.request_payload(turn_id)
    receipt = saved.get('prepared_receipt')
    if not isinstance(receipt, dict) or receipt.get('kind') != 'commander-result-receipt-v1':
        raise WorkUnitRefused('Commander 原回答缺持久成本与会话提交证明')
    kwargs = receipt.get('cost_kwargs')
    if not isinstance(kwargs, dict):
        raise WorkUnitRefused('Commander 成本恢复证明缺参数')
    attempt = agent_session_audit.AgentSessionEvent.from_dict(receipt['attempt_event'])
    turn = agent_session_audit.AgentSessionEvent.from_dict(receipt['turn_event'])
    response = saved.get('final_response')
    if not isinstance(response, str):
        raise WorkUnitRefused('Commander 原回答缺文本')
    # 沿用 Commander 唯一严格 JSON 摘要口；格式错误也有有界纠错身份。
    from novel.orchestrator.reviewer_legacy import reviewer_response_sha256
    response_sha = reviewer_response_sha256(response)
    expected = dict(run_id=row.run_id, session_run_id=row.session_run_id, sid=budget.identity['sid'],
                    attempt_id=attempt.attempt_id, response_sha256=response_sha,
                    outcome='ok', exit_code=0, error_kind=None,
                    code=attempt.start_code_version, protocol_version=attempt.protocol_version,
                    cli_version=attempt.cli_version)
    raw_usage = kwargs.get('usage')
    cost_usage = (None if raw_usage is None or (isinstance(raw_usage, dict)
                  and all(value is None for value in raw_usage.values())) else raw_usage)
    if (any(kwargs.get(k) != v for k, v in expected.items())
            or saved.get('session_run_id') != row.session_run_id
            or attempt.kind != 'attempt' or turn.kind != 'turn'
            or turn != replace(attempt, kind='turn', delivery_attempts=None, turn_index=turn.turn_index)
            or cost_usage != (None if attempt.usage is None else attempt.usage.to_dict())
            or not attempt.attempt_id or attempt.attempt_id != turn.attempt_id
            or not any(item.attempt_id == attempt.attempt_id for item in row.attempts)
            or attempt.role != 'commander' or turn.role != 'commander'
            or attempt.session_run_id != row.session_run_id
            or turn.session_run_id != row.session_run_id
            or attempt.parent_run_id != row.run_id or turn.parent_run_id != row.run_id
            or attempt.error_kind is not None or turn.error_kind is not None
            or attempt.profile is None or attempt.profile != turn.profile
            or attempt.profile.model != kwargs.get('request_model')
            or attempt.profile.reasoning_effort != kwargs.get('requested_effort')
            or (attempt.actual_model is not None
                and attempt.profile is not None
                and attempt.actual_model != attempt.profile.model)
            or attempt.actual_effort not in (None, attempt.profile.reasoning_effort)
            or not attempt.provider_session_id
            or attempt.provider_session_id != turn.provider_session_id
            or attempt.provider_session_id != kwargs.get('provider_session_id')
            or attempt.actual_model != kwargs.get('actual_model')
            or attempt.actual_effort != kwargs.get('actual_effort')
            or attempt.schema_sha256 != request.get('schema_sha256')
            or receipt.get('logical_prompt_sha256') != hashlib.sha256(request['prompt'].encode('utf-8')).hexdigest()
            or attempt.prompt_sha256 != hashlib.sha256(receipt['request_prompt'].encode('utf-8')).hexdigest()):
        raise WorkUnitRefused('Commander 原回答、请求、成本与会话身份不一致')
    agent_session_audit.reconcile_commander_event(budget.base, attempt, read_only=read_only)
    reconcile_commander_call(budget.base, read_only=read_only, **kwargs)
    agent_session_audit.reconcile_commander_event(budget.base, turn, read_only=read_only)
    if not read_only:
        budget.record_attempt(turn_id, attempt_id=attempt.attempt_id, outcome='ok',
                              provider_session_id=attempt.provider_session_id)


def commander_budget_for_record(base: str | os.PathLike[str], sid: str, record: Any) -> WorkUnitBudget:
    """从原不可变账恢复章级/整书属主，不按当前下一批次重造预算账户。"""
    if not record.work_unit_id or record.sid != sid:
        raise WorkUnitRefused('审阅记录缺当前书的原工作单元身份')
    ledger = _read(base, paths.work_unit_dir(base, sid, record.work_unit_id) / 'ledger.json', WorkUnitLedgerV2)
    identity = approval_work_unit(record.waiting_on)
    budget = WorkUnitBudget(base, sid, ledger.node, 'commander', ledger.scope, read_only=True)
    if (identity is None or identity[0] != ledger.node or ledger.role != 'commander'
            or budget.unit_id != record.work_unit_id or not approval_scope_allowed(ledger.node, ledger.scope)):
        raise WorkUnitRefused('审阅记录与原 Commander 属主或范围不一致')
    return budget


def validate_reviewer_record(base: str | os.PathLike[str], sid: str, record: Any) -> WorkUnitBudget:
    """所有决定消费者共用原回答、工作单元及 typed 动作的精确证明。"""
    from novel.orchestrator import reviewer_legacy

    unit_id = record.work_unit_id
    turn_id = record.work_unit_turn_id
    if not unit_id or not turn_id or record.sid != sid:
        raise WorkUnitRefused('审阅决定缺工作单元身份')
    ledger = _read(base, paths.work_unit_dir(base, sid, unit_id) / 'ledger.json', WorkUnitLedgerV2)
    budget = commander_budget_for_record(base, sid, record)
    if budget.unit_id != unit_id or ledger.role != 'commander':
        raise WorkUnitRefused('审阅决定串用了其他角色或工作单元')
    turn = budget._turn(ledger, turn_id)
    if turn.state not in ('delivered', 'adopted'):
        raise WorkUnitRefused('审阅决定不属于登记 owner 或回答已经拒绝')
    if ledger.scope != approval_work_unit(record.waiting_on)[1]:
        if budget.request_payload(turn_id).get('work_unit_scope') != ledger.scope:
            raise WorkUnitRefused('分章审阅原请求缺精确批次绑定')
    reconcile_commander_response(budget, turn_id, read_only=True)
    from novel.orchestrator.reviewer_legacy import validate_work_unit_recovery
    validate_work_unit_recovery(str(base), sid, budget=budget, turn_id=turn_id)
    proof = budget.recovery_proof(turn_id)
    if record.node is not None:
        action = reviewer_legacy.ReviewerNodeAction(
            kind='node', node=record.node, flags=tuple(record.flags), input_payload=record.input_payload)
    else:
        action = reviewer_legacy.ReviewerApprovalAction(kind='approval', surface=record.surface, payload=record.payload)
    expected = dict(business_sha256=record.business_sha256, waiting_on=record.waiting_on,
                    action_sha256=reviewer_legacy.action_business_sha256(action),
                    provider_response_sha256=record.provider_response_sha256)
    if any(proof.get(key) != value for key, value in expected.items()):
        from novel.orchestrator import reviewer_legacy
        try:
            rebound = reviewer_legacy.proof_matches(
                budget.request_payload(turn_id), proof,
                business_sha256=record.business_sha256, waiting_on=record.waiting_on,
                action=asdict(action), provider_response_sha256=record.provider_response_sha256)
        except reviewer_legacy.RevalidationRefused as exc:
            raise WorkUnitRefused(str(exc)) from exc
        if not rebound:
            raise WorkUnitRefused('typed 动作与原工作单元回答不一致')
    if turn.session_run_id != record.reviewer_session_run_id:
        raise WorkUnitRefused('typed 决定与原物理会话不一致')
    raw = _read(base, budget.directory / f'{turn.response_sha256}.json', WorkUnitBlobV1)
    if (_digest(raw.model_dump(mode='json')) != turn.response_sha256
            or raw.unit_id != unit_id or raw.turn_id != turn_id
            or not isinstance(raw.payload.get('final_response'), str)
            or _digest(json.loads(raw.payload['final_response'])) != record.provider_response_sha256):
        raise WorkUnitRefused('原始 Commander 回答字节不闭合')
    return budget


def executor_scope(node: str, purpose: str, progress: Any = None) -> str:
    """只从登记的业务拓扑取 scope，修复 purpose 不铸新单元。"""
    if node in ('stage2.analyze', 'stage2.characters', 'stage2.global', 'stage6.prepare', 'stage6.hook', 'stage7.rename'):
        return 'book'
    chapter = getattr(progress, 'chapter_ordinal', None)
    if chapter is None:
        chapter = getattr(progress, 'chapter', None)
    if isinstance(progress, dict):
        chapter = progress.get('chapter_ordinal', progress.get('chapter', chapter))
    if node == 'stage4.design' and 'strategy' in purpose:
        return 'strategy'
    if chapter is None:
        match = re.search(r'(?:chapter|ch)[.:_-]?(\d+)', purpose)
        if match is None:
            match = re.match(re.escape(node) + r':(\d+)(?:\b|:)', purpose)
        if match:
            chapter = int(match.group(1))
    if node in ('stage2.blueprint', 'stage4.design', 'stage4.plan', 'stage5.write'):
        if not isinstance(chapter, int) or isinstance(chapter, bool) or chapter < 1:
            raise WorkUnitRefused(f'{node} 请求缺显式章节工作单元')
        return f'chapter:{chapter}'
    raise WorkUnitRefused('该节点没有登记 Executor 模型工作单元')


def unit_view(ledger: WorkUnitLedgerV2) -> dict[str, Any]:
    """运行页与额度预览共享同一投影，不从剩余次数重新猜恢复路径。"""
    latest = ledger.turns[-1] if ledger.turns else None
    state = latest.state if latest else 'not_run'
    recovery = ('replay_response' if (state == 'delivered' or (
                    ledger.group_producer_sha256 is not None and not ledger.group_producer_completed)
                    or any(turn.producer_sha256 and turn.ordinal > ledger.committed_through for turn in ledger.turns)) else
                'reconcile' if state in ('reserved', 'unknown') else
                'manual_edit' if len(ledger.turns) >= ledger.content_limit else None)
    return dict(unit_id=ledger.unit_id, node=ledger.node, role=ledger.role,
                scope=ledger.scope, content_used=len(ledger.turns),
                content_limit=ledger.content_limit, content_base_limit=ledger.base_content_limit,
                content_granted=ledger.content_limit - ledger.base_content_limit,
                grant_count=len(ledger.grants), content_remaining=ledger.content_limit - len(ledger.turns),
                state=state, latest_request_sha256=latest.request_sha256 if latest else None,
                latest_response_sha256=latest.response_sha256 if latest else None,
                recovery_action=recovery, processing=processing_view(ledger))


def processing_view(ledger: WorkUnitLedgerV2) -> dict[str, Any] | None:
    """最近一次实际处理与累计内容账分别展示；回答存在不代表质量验收通过。"""
    last = ledger.processing[-1] if ledger.processing else None
    turn = (next((row for row in ledger.turns if row.turn_id == last.turn_id), None)
            if last is not None and last.kind == 'model' else
            ledger.turns[-1] if last is None and ledger.turns else None)
    if last is None and turn is None:
        return None
    kind = last.kind if last is not None else 'model'
    reason = None
    if kind == 'model':
        assert turn is not None
        kind = {'adopted': 'generated', 'delivered': 'generated', 'reserved': 'pending',
                'failed': 'failed', 'unknown': 'unknown'}[turn.state]
        reason = turn.reason_code or None
    counts = {name: int(name == kind) for name in (
        'pending', 'generated', 'mechanical', 'history_adopted', 'cached', 'failed', 'unknown')}
    return dict(run_id=last.run_id if last is not None else turn.run_id,
                input_sha256=last.input_sha256 if last is not None else turn.request_sha256,
                total=1, **counts, reason=reason)


def list_unit_views(base: str | os.PathLike[str], sid: str) -> list[dict[str, Any]] | None:
    manifest = workflow_manifest(base)
    if manifest is None:
        return None
    root = paths.work_units_dir(base, sid)
    registry_path = paths.work_unit_registry_path(base, sid)
    if registry_path.exists():
        registry = _read(base, registry_path, WorkUnitRegistryV1)
        if registry.task_id != manifest.task_id or registry.sid != sid:
            raise WorkUnitRefused('工作单元登记账属于另一工作流或书，运行状态未知')
        if any(not (root / unit_id / 'ledger.json').is_file() for unit_id in registry.unit_ids):
            raise WorkUnitRefused('已登记工作单元缺预算账，运行状态未知')
    if not root.exists():
        return []
    result: list[dict[str, Any]] = []
    for path in sorted(root.glob('*/ledger.json')):
        ledger = _read(base, path, WorkUnitLedgerV2)
        identity = {key: getattr(ledger, key) for key in ('task_id', 'sid', 'node', 'role', 'scope')}
        if (ledger.task_id != manifest.task_id or ledger.sid != sid
                or ledger.unit_id != path.parent.name or ledger.unit_id != _digest(identity)):
            raise WorkUnitRefused('工作单元路径、任务、书或内容身份漂移，运行状态未知')
        for row in ledger.processing:
            material = row.model_dump(exclude={'processing_id'}, exclude_none=True)
            if row.processing_id != _digest({'unit_id': ledger.unit_id, **material}):
                raise WorkUnitRefused('工作单元处理来源回执损坏，运行状态未知')
        result.append(unit_view(ledger))
    return result
