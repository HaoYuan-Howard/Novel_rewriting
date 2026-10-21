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
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, BinaryIO, TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel

from contracts.schema.source_story import STORY_VERSION
from contracts.invariants import (
    BOOK_CONTENT_CALL_CEILING_DEFAULT, NODES, narrow_unit_scopes,
)
from contracts.llm_versions import llm_node_versions
from contracts.schema.workflow import (
    BookBudgetGrantV1, BookContentBudgetV1,
    WorkflowManifest, WorkUnitAttempt, WorkUnitBlobV1,
    WorkUnitContentTurn, WorkUnitGroupBlobV1, WorkUnitLedgerV3,
    WorkUnitRegistryV1, WorkUnitProcessing, CommitBundleV1, commit_bundle_seal,
    LlmNodeVersionReset, work_unit_ledger_of_payload,
)
from contracts.schema.work_unit_recovery import (
    CommanderFailureRecoveryReceipt, ExecutorWireRefusalReceipt,
    WorkUnitFailureReceipt,
)
from novel import paths
from novel.appendlog import path_transaction_lock
from novel.errors import TerminalState

if TYPE_CHECKING:
    from novel.run import RunContext


def sealed_commit_bundles(root: Path) -> list[tuple[Path, CommitBundleV1]]:
    """候选根 ``runtime/commit/`` 的全部已封存提交记录（损坏即关闭式拒绝）。

    **采用状态从 seal 派生**（B1）：恢复路径据此判断哪些内容轮已被正式提交
    采用；记录损坏不是「当它不存在」，而是显式停止。形状不合约与 seal 不符
    同走 `ProvenanceRefused`——此前形状错以裸 `ValidationError` 冒出，被
    `chain._committed_set` 的逐章 except 吞成「本章无提交」，把全局记录损坏
    扩大成全书待补集合（2026-10-10 审阅实证）。要么整份记录可信，要么整个
    恢复面停下，不存在「跳过坏记录继续」的中间档。
    """
    from pydantic import ValidationError

    from novel.errors import ProvenanceRefused

    commit_dir = Path(root) / 'runtime' / 'commit'
    if not commit_dir.is_dir():
        return []
    found: list[tuple[Path, CommitBundleV1]] = []
    for path in sorted(commit_dir.rglob('*.json')):
        payload = _read_json_dict(path)
        if commit_bundle_seal(payload) != payload.get('seal'):
            raise ProvenanceRefused(f'CommitBundle 记录 seal 不符：{path}')
        try:
            found.append((path, CommitBundleV1.model_validate(payload)))
        except ValidationError as exc:
            raise ProvenanceRefused(
                f'CommitBundle 记录形状不合约：{path}'
                f'（{exc.errors(include_url=False)}）') from exc
    return found


def _read_json_dict(path: Path) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'JSON 重复键：{key}')
            result[key] = value
        return result

    data = json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=unique)
    if not isinstance(data, dict):
        raise ValueError(f'CommitBundle 记录不是 JSON object：{path}')
    return data


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
    _write_bytes(target, _bytes(model.model_dump(mode='json')))


def _write_bytes(target: Path, data: bytes) -> None:
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
            raw = stream.read()
            if model is WorkUnitLedgerV3:
                return work_unit_ledger_of_payload(json.loads(raw))
            return model.model_validate_json(raw)
    except (OSError, ValueError) as exc:
        raise WorkUnitRefused(f'工作单元账缺失或不符合当前契约：{target.name}') from exc


def workflow_manifest(base: str | os.PathLike[str]) -> WorkflowManifest | None:
    path = _safe(base, paths.workflow_manifest_path(base))
    if not path.exists():
        return None
    try:
        return _read(base, path, WorkflowManifest)
    except (ValueError, WorkUnitRefused) as exc:
        raise WorkUnitRefused('输出根不是当前story-v3契约；旧根只读归档，请在新根重新生成') from exc


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
        # STR-06 关闭批：离线迁移工具（workflow transition）已删；其 pending
        # 围栏与记录目录是旧 base 痕迹，存在即拒绝初始化，不当作干净 base。
        if ((service / 'workflow_transition.json').exists()
                or (service / 'workflow_transitions').exists()):
            raise WorkUnitRefused('此 base 含已退役工作流迁移痕迹，禁止初始化新工作流')


def check_workflow_write(base: str | os.PathLike[str]) -> None:
    """纯读预检；在 admission 等服务写副作用之前拒绝冻结旧 base。

    本仓没有通用迁移机制：v7 起删除 Stage2 语义审阅与 KnowledgeLinks，
    旧 v4~v6 manifest 不迁移，旧输出与成本账原样保留，由冻结旧代码读取。
    """
    if workflow_manifest(base) is None:
        _require_clean_base(base)


def initialize_workflow(base: str | os.PathLike[str]) -> WorkflowManifest:
    """仅空 base 可初始化；旧书不能靠删账或换代码获得新额度。"""
    target = _safe(base, paths.workflow_manifest_path(base))
    # 拒绝存量 base 必须零写；锁本身会创建父目录，因此先作只读预检。
    if workflow_manifest(base) is None:
        _require_clean_base(base)
    with path_transaction_lock(target):
        existing = workflow_manifest(base)
        if existing is not None:
            return existing
        _require_clean_base(base)
        manifest = WorkflowManifest(schema_version=1, contract_version=STORY_VERSION,
            pipeline_contract_epoch=STORY_VERSION, task_id=hashlib.sha256(uuid4().bytes).hexdigest(),
                                      created_at=int(time.time()))
        _write(base, target, manifest)
        return manifest


def require_workflow(
        base: str | os.PathLike[str],
) -> WorkflowManifest:
    manifest = workflow_manifest(base)
    if manifest is None:
        return initialize_workflow(base)
    return manifest


# ─── 整书级付费内容调用总闸 ────────────────────────────────────────────────

def _book_budget_path(base: str | os.PathLike[str], sid: str) -> Path:
    return _safe(base, paths.book_budget_path(base, sid))


def node_lifecycle_spec(node: str):
    """节点生命周期解析：现役 `NODES` 唯一来源，未登记再查只读退役表。

    【为什么不再回退候选表】STR-06 候选节点（`str06.*`）已在 F8a 激活进
    `NODES`，付费内容调用始终落在**同一套**持久工作单元与整书总闸上——不建
    第二份预算账（I-17）。原 F7a 的「现役表优先、未登记回退候选表」分支随
    F8c 删除条件（STR-06-F8c）一并删除：候选表已不存在，未登记节点一律
    `WorkUnitRefused`。

    只返回现役 `NodeLifecycleSpec`（带 `unit_kind` 与 `executor_content_limit`，
    调用方只消费这两个字段）。

    ⚠ STR-06 关闭批：退役节点只读解码桩（`_RetiredNodeLifecycle`）与
    `RETIRED_NODES` 名册已随存量解码面删除——未登记节点一律关闭式拒绝。
    """
    if node in NODES:
        spec = NODES[node].lifecycle
        if spec is None:
            raise WorkUnitRefused(f'现役节点未登记执行生命周期：{node}')
        return spec
    raise WorkUnitRefused(f'未登记的工作单元节点：{node}')


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


def reserve_book_call(base: str | os.PathLike[str], sid: str, *, task_id: str) -> None:
    """整书付费内容调用总闸 +1（跨全部工作单元合账的唯一持久入口，Q-10）。

    正式 `WorkUnitBudget.reserve` 在单元账锁内调用它；隔离候选链的付费边界
    经同一工作单元路径间接调用——两条路同一份账
    （`_service/runs/<sid>/book_budget.json`），不建第二本预算账（I-17）。
    账在首次付费调用时创建；重启、恢复、重建 job 读同一份持久账，已消耗额度
    不重置。坏账关闭式拒绝新调用；自动恢复路径不得追加额度。
    """
    path = _book_budget_path(base, sid)
    with path_transaction_lock(path):
        budget = read_book_budget(base, sid, task_id=task_id)
        if budget is None:
            budget = BookContentBudgetV1(
                task_id=task_id, sid=sid,
                ceiling=BOOK_CONTENT_CALL_CEILING_DEFAULT)
        if budget.remaining <= 0:
            raise WorkUnitBudgetExhausted(
                f'整书付费内容调用总闸已耗尽（{budget.used}/'
                f'{budget.ceiling + sum(g.additional for g in budget.grants)}）——'
                '这是跨全部工作单元的合计上限；请显式追加整书预算后再续跑，'
                '自动恢复路径不得追加')
        _write(base, path, budget.model_copy(update={'used': budget.used + 1}))


def _reserve_book_call(base: str | os.PathLike[str], identity: dict) -> None:
    """在已持有的单元账锁内对整书总闸 +1；锁序恒为 单元账 → 整书账。"""
    reserve_book_call(base, identity['sid'], task_id=identity['task_id'])


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
        if role != 'executor':
            # Commander 受托审阅已退役（2026-09-15）。STR-06 关闭批删除其
            # 历史账只读解码面：commander 角色一律关闭式拒绝（含 read_only）。
            raise WorkUnitRefused('Commander 工作单元已退役；历史账随关闭批冻结')
        try:
            # 现役 NODES 与 STR-06 候选表（F7a）同一道解析；候选节点复用本类
            # 全部正式语义，不为隔离链建第二套预算账（删除条件见函数 docstring）。
            spec = node_lifecycle_spec(node)
        except WorkUnitRefused:
            raise WorkUnitRefused('未登记的工作单元节点或角色')
        allowed_role = spec.executor_content_limit == 4
        chapter_scope = bool(re.fullmatch(r'chapter:[1-9][0-9]*', scope))
        # 节点内窄调用 owner 的独立单元（`NARROW_OWNER_UNITS`）：只放行该节点登记过的
        # scope；其余形状照旧关闭式拒绝。
        allowed_scope = (
            spec.unit_kind in ('book', 'candidate_batch') and scope == 'book'
            or spec.unit_kind == 'book' and scope in narrow_unit_scopes(node)
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

    def digest_of(self, blob: WorkUnitBlobV1) -> str:
        """blob 内容摘要的同一把尺（外部只读校验用，R8a-6 采用证据）。"""
        return _digest(blob.model_dump(mode='json'))

    def _read(self) -> WorkUnitLedgerV3:
        if not self.path.exists():
            if self.unit_id in self._registry().unit_ids:
                raise WorkUnitRefused('已登记的工作单元预算账缺失，禁止重新发放额度')
            # 非账文件意味着丢账，不能以新对象覆盖已保存的回答。
            if self.directory.exists() and any(
                    p.name != 'ledger.json.lock' for p in self.directory.iterdir()):
                raise WorkUnitRefused('工作单元已有内容但预算账缺失')
            return WorkUnitLedgerV3(schema_version=3, unit_id=self.unit_id, **self.identity)
        ledger = _read(self.base, self.path, WorkUnitLedgerV3)
        if ledger.unit_id != self.unit_id or any(
                getattr(ledger, key) != val for key, val in self.identity.items()):
            raise WorkUnitRefused('工作单元账身份不一致')
        if any(row.upgrade_id != _digest({'node': ledger.node, 'versions': row.versions})
               for row in ledger.version_resets):
            raise WorkUnitRefused('LLM 节点版本重置身份损坏')
        for row in ledger.processing:
            if self._processing(**row.model_dump(exclude={'processing_id'}, exclude_none=True)) != row:
                raise WorkUnitRefused('工作单元处理来源回执损坏，禁止新调用')
        for turn in ledger.turns:
            if turn.reason_code == COMMANDER_FAILURE_RECONCILED:
                self.failure_recovery_receipt(ledger, turn)
        return ledger

    def failure_recovery_receipt(
            self, ledger: WorkUnitLedgerV3,
            turn: WorkUnitContentTurn) -> WorkUnitFailureReceipt:
        """历史失败一旦已收口，其原失败和工程裁决证明仍必须可验证。

        Commander 收口证明只配 commander 账、executor wire 拒收收口证明
        （R10c 2026-10-02）只配 executor 账；role 与证明种类错配即拒绝。
        """
        found = [row for row in ledger.processing if row.kind == 'mechanical'
                 and row.input_sha256 == failure_recovery_input(turn.turn_id)]
        if len(found) != 1 or found[0].run_id != turn.run_id:
            raise WorkUnitRefused('失败收口缺唯一工程证明')
        row = found[0]
        try:
            target = _safe(self.base, self.directory / f'{row.evidence_sha256}.json')
            with _open_snapshot(target) as stream:
                raw = stream.read()
            if hashlib.sha256(raw).hexdigest() != row.evidence_sha256:
                raise ValueError('失败收口证明字节身份漂移')
            blob = WorkUnitBlobV1.model_validate_json(raw)
            if blob.payload.get('kind') == 'executor-wire-refusal-reconciliation-v1':
                receipt: WorkUnitFailureReceipt = (
                    ExecutorWireRefusalReceipt.model_validate(blob.payload))
                expected_role = 'executor'
            else:
                receipt = CommanderFailureRecoveryReceipt.model_validate(blob.payload)
                expected_role = 'commander'
            expected = receipt.before.model_copy(update={
                'state': 'failed', 'reason_code': COMMANDER_FAILURE_RECONCILED})
            original = work_unit_ledger_of_payload(receipt.evidence['ledger'])
            if (_digest(blob.model_dump(mode='json')) != row.evidence_sha256
                    or blob.unit_id != self.unit_id or blob.turn_id != turn.turn_id
                    or receipt.unit_id != ledger.unit_id or receipt.sid != ledger.sid
                    or ledger.role != expected_role or expected != turn
                    or original.unit_id != ledger.unit_id
                    or self._turn(original, turn.turn_id) != receipt.before
                    or _digest(receipt.evidence) != receipt.request.preview_token):
                raise ValueError('失败收口证明主体不一致')
        except (OSError, ValueError, KeyError) as exc:
            raise WorkUnitRefused('失败收口证明损坏，禁止新调用') from exc
        return receipt

    def reconcile_failure_under_lock(self, receipt: WorkUnitFailureReceipt) -> None:
        """调用方已持有本账事务锁及 owner lease；不重入不支持重入的文件锁。

        只翻轮级终态，不改写 attempt 历史：executor 确定性拒收在旧 wire 下
        attempt 记 ``unknown``（R10c 2026-10-02），作为历史记录原样保留；
        消费面（如 `work_unit_evidence.cost_evidence`）按轮级收口标记＋
        receipt 复验容忍该形态。
        """
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
        updated = WorkUnitLedgerV3.model_validate({**ledger.model_dump(mode='json'),
            'turns': [failed if row.turn_id == turn.turn_id else row for row in ledger.turns],
            'processing': [*ledger.processing, processing]})
        self.failure_recovery_receipt(updated, failed)
        _write(self.base, self.path, updated)

    def _replace(self, ledger: WorkUnitLedgerV3, turn: WorkUnitContentTurn) -> None:
        rows = [row.model_dump(mode='json') if row.turn_id != turn.turn_id
                else turn.model_dump(mode='json') for row in ledger.turns]
        updated = WorkUnitLedgerV3.model_validate({
            **ledger.model_dump(mode='json'), 'turns': rows})
        _write(self.base, self.path, updated)

    def _turn(self, ledger: WorkUnitLedgerV3, turn_id: str) -> WorkUnitContentTurn:
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

    def _synchronize_version(self, ledger: WorkUnitLedgerV3) -> WorkUnitLedgerV3:
        """已持有单元锁：版本升版恢复基础额度；历史、状态与请求身份不变。"""
        versions = llm_node_versions(self.identity['node'])
        previous = ledger.version_resets[-1].versions if ledger.version_resets else {}
        if versions == previous:
            return ledger
        if (not set(previous) <= set(versions)
                or any(versions[key] < value for key, value in previous.items())):
            raise WorkUnitRefused('LLM 节点版本降级，不能恢复或消耗当前版本额度')
        reset = LlmNodeVersionReset(
            upgrade_id=_digest({'node': ledger.node, 'versions': versions}),
            versions=versions,
            reason=('LLM 节点升版恢复基础额度' if previous else
                    '首次建立 LLM 节点版本基线并恢复基础额度'),
            created_at=int(time.time()),
            before_ledger_sha256=_digest(ledger.model_dump(mode='json')),
            refunded_after_ordinal=len(ledger.turns),
            additional=max(0, ledger.base_content_limit - (
                ledger.content_limit - len(ledger.turns))))
        return WorkUnitLedgerV3.model_validate({**ledger.model_dump(mode='json'),
            'version_resets': [*ledger.version_resets, reset]})

    def synchronize_version(self) -> dict[str, Any]:
        """免费执行入口；同版本幂等，读面不通过本入口偷偷写账。"""
        with path_transaction_lock(self.path):
            ledger = self._read()
            updated = self._synchronize_version(ledger)
            if updated == ledger:
                return {'status': 'already_refunded', 'additional': 0,
                        'remaining': ledger.content_limit - len(ledger.turns)}
            if not self.path.is_file():
                raise WorkUnitRefused('版本恢复只处理已登记工作单元，不初始化空账')
            _write(self.base, self.path, updated)
            return {'status': 'refunded', 'additional': updated.content_limit - ledger.content_limit,
                    'remaining': updated.content_limit - len(updated.turns),
                    'versions': updated.version_resets[-1].versions}

    def reserve(self, request_sha256: str, *, run_id: str,
                session_run_id: str = '',
                agent_run_id: str = '', agent_epoch_id: str = '',
                candidate_session_run_id: str = '',
                request_payload: dict[str, Any] | None = None) -> ContentReservation:
        with path_transaction_lock(self.path):
            ledger = self._read()
            if ledger.group_producer_sha256 is not None and not ledger.group_producer_completed:
                raise WorkUnitRefused('已有整组候选待补提交；须先免费恢复，不能重新调用模型')
            if any(t.state in ('reserved', 'unknown', 'delivered') for t in ledger.turns):
                raise WorkUnitRefused('已有未闭合内容请求；须先对账，不能重新调用模型')
            updated_version = self._synchronize_version(ledger)
            if self.path.is_file() and updated_version != ledger:
                # 先独立保存免费版本恢复；整书总闸或后续调用失败不丢掉该基线。
                _write(self.base, self.path, updated_version)
            ledger = updated_version
            if len(ledger.turns) >= ledger.content_limit:
                raise WorkUnitBudgetExhausted(
                    f'本工作单元内容轮额度已耗尽（{len(ledger.turns)}/'
                    f'{ledger.content_limit}），请免费恢复、人工编辑或明确申请追加额度')
            # 整书总闸在单元账写入之前判：被拒时单元账不留下未闭合的
            # reserved 轮；反向（总闸已 +1 而单元账写失败）只是保守多计
            # 一次，不构成付费风险。
            _reserve_book_call(self.base, self.identity)
            turn = WorkUnitContentTurn(
                turn_id=uuid4().hex, ordinal=len(ledger.turns) + 1,
                request_sha256=request_sha256, run_id=run_id,
                session_run_id=session_run_id,
                # R8a-5：执行身份三格随轮入账——追加额度的 owner 证明按记录的
                # 关系闭合，不再事后从成本行反推（parent_run_id 是候选根 epoch
                # 会话，不是 AgentRun）。
                agent_run_id=agent_run_id, agent_epoch_id=agent_epoch_id,
                candidate_session_run_id=candidate_session_run_id,
                created_at=int(time.time()))
            if request_payload is not None:
                blob = WorkUnitBlobV1(unit_id=self.unit_id, turn_id=turn.turn_id,
                                      payload=request_payload)
                digest = _digest(blob.model_dump(mode='json'))
                # 账先持久，避免工作包落盘后崩溃把未知预算洗成空账。
                turn = turn.model_copy(update={'request_payload_sha256': digest})
            updated = WorkUnitLedgerV3.model_validate({
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
            updated_version = self._synchronize_version(ledger)
            if proposed in ledger.processing:
                if updated_version != ledger:
                    _write(self.base, self.path, updated_version)
                return
            ledger = updated_version
            with path_transaction_lock(self.registry_path):
                registry = self._registry()
                if self.unit_id not in registry.unit_ids:
                    _write(self.base, self.registry_path, registry.model_copy(update={
                        'unit_ids': (*registry.unit_ids, self.unit_id)}))
            _write(self.base, self.path, WorkUnitLedgerV3.model_validate({
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
                                      (previous.request_fingerprint if previous else None)),
                                  response_sha256=(previous.response_sha256
                                                   if previous else None))
            if previous is not None:
                if previous == row:
                    return
                if previous.outcome != 'started':
                    raise WorkUnitRefused('网络尝试终态不能覆盖')
            attempts = tuple(a for a in turn.attempts if a.attempt_id != attempt_id) + (row,)
            self._replace(ledger, turn.model_copy(update={'attempts': attempts}))

    def record_attempt_response(self, turn_id: str, attempt_id: str,
                                payload: dict[str, Any]) -> str:
        """原响应按 attempt 耐久存储并被 attempt 引用（ProviderAttempt 层，B1）。

        blob 与既有响应证据同目录同形状（``WorkUnitBlobV1``，内容寻址）；引用
        一经写入不可覆盖为另一份回答。空回答同样留证（failed attempt 的证据），
        但不置轮级响应——空回答不占据请求身份（D5）。
        """
        with path_transaction_lock(self.path):
            ledger = self._read()
            turn = self._turn(ledger, turn_id)
            found = [a for a in turn.attempts if a.attempt_id == attempt_id]
            if len(found) != 1:
                raise WorkUnitRefused('attempt 不属于当前内容轮')
            blob = WorkUnitBlobV1(unit_id=self.unit_id, turn_id=turn_id, payload=payload)
            digest = _digest(blob.model_dump(mode='json'))
            if found[0].response_sha256 not in (None, digest):
                raise WorkUnitRefused('同一 attempt 不能覆盖为另一个回答')
            self._replace(ledger, turn.model_copy(update={'attempts': tuple(
                a.model_copy(update={'response_sha256': digest})
                if a.attempt_id == attempt_id else a for a in turn.attempts)}))
            _write(self.base, self.directory / f'{digest}.json', blob)
            return digest

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

    # ── B1 执行设施：按请求身份的可用响应与同轮续发 ────────────────────────

    def usable_response(self, request_sha256: str
                        ) -> tuple[ContentReservation, dict[str, Any], str, str] | None:
        """该请求身份最近一次**可用**的已交付回答（免费采用）。

        认 ``outcome='ok'`` 且非空的 attempt 响应，或经过同一 compiler
        接受的完整 reasoning 救回。其余空回答以 failed attempt 留证，
        **不占据请求身份**（D5）——重跑同一请求身份必须能拿到新回答，
        而不是免费读回同一空壳。返回 (reservation, payload, attempt_id,
        response_sha256)。blob 缺失或身份漂移是坏账，关闭式拒绝。
        """
        with path_transaction_lock(self.path):
            ledger = self._read()
            for turn in reversed(ledger.turns):
                if turn.request_sha256 != request_sha256:
                    continue
                for attempt in reversed(turn.attempts):
                    if attempt.outcome not in ('ok', 'failed') or not attempt.response_sha256:
                        continue
                    salvaged = False
                    if attempt.outcome != 'ok':
                        path = self.directory / f'accept_{attempt.attempt_id}.json'
                        if not path.is_file():
                            continue
                        acceptance = _read(self.base, path, WorkUnitBlobV1)
                        record = acceptance.payload
                        salvaged = (
                            acceptance.unit_id == self.unit_id
                            and acceptance.turn_id == turn.turn_id
                            and record.get('attempt_id') == attempt.attempt_id
                            and record.get('request_sha256') == request_sha256
                            and record.get('accepted') is True
                            and record.get('parse') == 'ok'
                            and record.get('salvage') == 'reasoning')
                        if not salvaged:
                            continue
                    blob = _read(self.base,
                                 self.directory / f'{attempt.response_sha256}.json',
                                 WorkUnitBlobV1)
                    if (_digest(blob.model_dump(mode='json')) != attempt.response_sha256
                            or blob.unit_id != self.unit_id
                            or blob.turn_id != turn.turn_id):
                        raise WorkUnitRefused('attempt 级响应证据身份漂移')
                    payload = blob.payload
                    response = payload.get('response') if isinstance(payload, dict) else None
                    if (not isinstance(response, dict)
                            or not ((response.get('reasoning') if salvaged
                                     else response.get('content')) or '').strip()):
                        raise WorkUnitRefused('ok attempt 的响应证据为空或损坏')
                    return (ContentReservation(self.unit_id, turn.turn_id, turn.ordinal),
                            payload, attempt.attempt_id, attempt.response_sha256)
            return None

    def open_turn(self, request_sha256: str) -> WorkUnitContentTurn | None:
        """该请求身份仍未闭合的内容轮（崩溃后同轮续发；无则 None）。"""
        ledger = self._read()
        for turn in reversed(ledger.turns):
            if turn.request_sha256 == request_sha256 and turn.state == 'reserved':
                return turn
        return None

    def unknown_turn(self, request_sha256: str) -> WorkUnitContentTurn | None:
        """该请求身份仍处于 unknown 的内容轮（先恢复对账，不自动重购）。"""
        ledger = self._read()
        for turn in reversed(ledger.turns):
            if turn.request_sha256 == request_sha256 and turn.state == 'unknown':
                return turn
        return None

    def attempts_of(self, turn_id: str) -> int:
        return len(self._turn(self._read(), turn_id).attempts)

    def attempt_request_payload(self, turn_id: str, attempt_id: str) -> dict[str, Any]:
        """读取一次 attempt 的耐久原响应证据（含响应与请求工作包）。"""
        turn = self._turn(self._read(), turn_id)
        found = [a for a in turn.attempts if a.attempt_id == attempt_id]
        if len(found) != 1 or not found[0].response_sha256:
            raise WorkUnitRefused('attempt 缺耐久响应证据')
        digest = found[0].response_sha256
        blob = _read(self.base, self.directory / f'{digest}.json', WorkUnitBlobV1)
        if (_digest(blob.model_dump(mode='json')) != digest
                or blob.unit_id != self.unit_id or blob.turn_id != turn_id):
            raise WorkUnitRefused('attempt 级响应证据身份漂移')
        return blob.payload

    def save_acceptance(self, turn_id: str, attempt_id: str,
                        payload: dict[str, Any]) -> str:
        """AcceptanceRecord 落为 attempt 级证据 blob（``accept_<attempt_id>.json``）。

        取代候选根逐请求 ``validation_*.json``；证据按 attempt 寻址，幂等重写
        同一 attempt 的同一份记录，不覆盖其它 attempt 的记录。
        """
        with path_transaction_lock(self.path):
            ledger = self._read()
            self._turn(ledger, turn_id)
            blob = WorkUnitBlobV1(unit_id=self.unit_id, turn_id=turn_id, payload=payload)
            digest = _digest(blob.model_dump(mode='json'))
            latest = self.directory / f'accept_{attempt_id}.json'
            first = self.directory / 'first_acceptance' / f'{attempt_id}.json'
            # 首次接受结论冻结；免费重验只更新latest，不改首答分母或历史结论。
            if not latest.exists() and not first.exists():
                _write(self.base, first, blob)
            _write(self.base, latest, blob)
            return digest

    def acceptance_records(self) -> tuple[tuple[str, dict[str, Any]], ...]:
        """读取本单元全部接受分项记录（按盘上文件名，损坏即拒绝）。"""
        result: list[tuple[str, dict[str, Any]]] = []
        if not self.directory.exists():
            return ()
        for path in sorted(self.directory.glob('accept_*.json')):
            blob = _read(self.base, path, WorkUnitBlobV1)
            if blob.unit_id != self.unit_id:
                raise WorkUnitRefused('接受记录不属于当前工作单元')
            attempt_id = path.stem.removeprefix('accept_')
            result.append((attempt_id, blob.payload))
        return tuple(result)

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

    def _group_producer_recovery(self, ledger: WorkUnitLedgerV3) -> dict[str, Any] | None:
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
        """本账恢复只认截至目标回答的实际尝试，不从其他根补采用来源。"""
        ledger = self._read()
        target = self._turn(ledger, turn_id)
        if not target.response_sha256:
            raise WorkUnitRefused('本账恢复必须绑定完整保存的回答')
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

    def latest_feedback(self) -> dict[str, Any] | None:
        """只读最近一次模型拒因（typed 纠错的恢复输入之一）；adopted 即清零。

        旧审阅指纹（business_sha256）重绑与 rejected_responses 恢复上下文
        随 STR-06 关闭批的 reviewer legacy 解码面删除；本读面只回答当前账内
        最近的拒因 payload，不做历史业务身份投影。
        """
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
            return blob.payload
        return None

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


def registered_unit_ids(base: str | os.PathLike[str], sid: str) -> tuple[str, ...]:
    """登记账里的全部工作单元（只读）；任务或书身份不符一律拒绝。

    工作单元的分母是登记账，不是目录扫描：采用等路径取事务锁时会在
    ``work_units/`` 下建出只有 ``ledger.json.lock`` 的空壳目录，它们不是单元。
    登记账不存在＝本书还没有单元（返回空）；登记了却缺预算账由 `readonly_budget`
    逐个关闭式拒绝，这里不替它判。
    """
    registry_path = paths.work_unit_registry_path(base, sid)
    if not registry_path.exists():
        return ()
    registry = _read(base, registry_path, WorkUnitRegistryV1)
    manifest = workflow_manifest(base)
    if manifest is None or registry.sid != sid or registry.task_id != manifest.task_id:
        raise WorkUnitRefused('工作单元登记账属于另一工作流或书，运行状态未知')
    return registry.unit_ids


def readonly_budget(base: str | os.PathLike[str], sid: str, unit_id: str) -> WorkUnitBudget:
    """观察既有工作单元；缺登记、缺账或身份漂移一律拒绝，不初始化额度。"""
    registry = _read(base, paths.work_unit_registry_path(base, sid), WorkUnitRegistryV1)
    ledger = _read(base, paths.work_unit_dir(base, sid, unit_id) / 'ledger.json', WorkUnitLedgerV3)
    budget = WorkUnitBudget(base, sid, ledger.node, ledger.role, ledger.scope, read_only=True)
    if (registry.sid != sid or registry.task_id != budget.identity['task_id']
            or unit_id not in registry.unit_ids or budget.unit_id != unit_id):
        raise WorkUnitRefused('只读工作单元登记身份不一致')
    budget._read()
    return budget


def executor_scope(node: str, purpose: str, progress: Any = None) -> str:
    """只从登记的业务拓扑取 scope，修复 purpose 不铸新单元。

    ⚠ F8b-3：本表按现役 `NODES` 的 unit_kind 登记付费节点；旧链节点键已随
    F8a 退役（不再接受任何模型工作单元）。B2（2026-09-27）五节点重建（D9）：
    章观察（`str06.stage2.chapters`）与按章成文（body）的 purpose／progress
    带显式章号；联合抽取的 `strategy` 整书单元随 `str06.stage2.source` 退役。
    """
    spec = NODES.get(node)
    if spec is None or not spec.paid:
        raise WorkUnitRefused('该节点没有登记 Executor 模型工作单元')
    if spec.lifecycle.unit_kind == 'book':
        return 'book'
    if spec.lifecycle.unit_kind == 'chapter':
        chapter = getattr(progress, 'chapter_ordinal', None)
        if chapter is None:
            chapter = getattr(progress, 'chapter', None)
        if isinstance(progress, dict):
            chapter = progress.get('chapter_ordinal', progress.get('chapter', chapter))
        if chapter is None:
            match = re.search(r'(?:chapter|ch)[.:_-]?(\d+)', purpose)
            if match is None:
                match = re.match(re.escape(node) + r':(\d+)(?:\b|:)', purpose)
            if match:
                chapter = int(match.group(1))
        if not isinstance(chapter, int) or isinstance(chapter, bool) or chapter < 1:
            raise WorkUnitRefused(f'{node} 请求缺显式章节工作单元')
        return f'chapter:{chapter}'
    raise WorkUnitRefused('该节点没有登记 Executor 模型工作单元')


def unit_view(ledger: WorkUnitLedgerV3) -> dict[str, Any]:
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
                content_refunded=sum(row.additional for row in ledger.version_resets),
                grant_count=len(ledger.grants), refund_count=sum(
                    row.additional > 0 for row in ledger.version_resets),
                content_remaining=ledger.content_limit - len(ledger.turns),
                state=state, latest_request_sha256=latest.request_sha256 if latest else None,
                latest_response_sha256=latest.response_sha256 if latest else None,
                recovery_action=recovery, processing=processing_view(ledger))


def processing_view(ledger: WorkUnitLedgerV3) -> dict[str, Any] | None:
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
        'pending', 'generated', 'mechanical', 'cached', 'failed', 'unknown')}
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
        ledger = _read(base, path, WorkUnitLedgerV3)
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
