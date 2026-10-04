#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""项目回收站：父进程只做互斥与拉子进程，真正移动目录发生在 CLI 子进程。

删除项目不是删除一张前端卡片。一本书目录里既有原作派生物，也可能有已经付费的产物；
因此这里采用同盘原子移动，把书目录与该书审批记录放进
``<base>/_service/trash/<trash_id>/``。上传原文、ledger、作业与 agent 事件继续留在
``_service`` 作为审计账，也避免误删可能被别的书共用的内容地址上传。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

from contracts.schema.api import TrashNovelResult
from novel import paths
from novel.errors import BookTrashFailed, PathRefused, SourceConflict
from novel.orchestrator import agent, jobs, service_lifecycle
from novel.orchestrator.nodes import child_env

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_MODULE = 'novel.cli.trash'
RESTORE_CLI_MODULE = 'novel.cli.trash_restore'
_TRASH_ID = re.compile(r'[0-9]{10}-[0-9a-f]{8}')

#: 本进程正在进行的删除。`sweep_incomplete` 据此绕开它们——**「还没写完」与
#: 「写到一半死了」在盘上长得一模一样**，只有还活着的那一方知道区别。
_IN_FLIGHT_LOCK = threading.Lock()
_IN_FLIGHT: set[str] = set()

#: 别的进程留下的条目要多久才算「死了」。删除是一次同盘目录移动（毫秒级），
#: 五分钟还没完成的只可能是被硬中断的。⚠ 这个宽限期不是为了保守——
#: 没有它，一次直接敲 CLI 的删除会被并发的服务端 sweep 把归档审批抢走。
STALE_AFTER_SECONDS = 300

Spawn = Callable[[Sequence[str], str, Mapping[str, str]], tuple[int, bytes]]


def new_trash_id(now: int | None = None) -> str:
    return f'{int(time.time() if now is None else now):010d}-{uuid.uuid4().hex[:8]}'


def build_argv(base: str | os.PathLike[str], sid: str, expected_title: str,
               trash_id: str) -> list[str]:
    """父进程显示/执行共用的唯一命令构造口。"""
    return [
        sys.executable, '-X', 'utf8', '-u', '-m', CLI_MODULE,
        '--base', os.path.abspath(os.fspath(base)),
        '--sid', paths.check_sid(sid),
        '--expected-title', expected_title,
        '--trash-id', trash_id,
    ]


def _default_spawn(argv: Sequence[str], cwd: str,
                   env: Mapping[str, str]) -> tuple[int, bytes]:
    kw: dict[str, int] = {}
    if os.name == 'nt':
        kw['creationflags'] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.run(
        list(argv), cwd=cwd, env=dict(env), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False, **kw,
    )
    return proc.returncode, proc.stdout


APPROVALS_DIRNAME = 'approvals'


_APPROVAL_LOCK = threading.RLock()


@contextmanager
def approval_exclusion_lock(
    base: str | os.PathLike[str], sid: str,
) -> Iterator[None]:
    """串行化历史审批文件的移动与回收站操作（跨进程 OS 文件锁）。

    ⚠ 2026-10-04 裁决：审批账写入与解码已整体删除，这把锁只剩「同一本书的
    回收站移动互斥」这一个职责；锁文件不是审批事实，长期保留无害。
    """
    lock_path = (
        Path(os.fspath(base)).resolve()
        / '_service' / APPROVALS_DIRNAME / '.locks' / f'{paths.check_sid(sid)}.lock'
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _APPROVAL_LOCK:
        with lock_path.open('a+b') as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b'\0')
                handle.flush()
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _approval_path(base: str | os.PathLike[str], sid: str) -> Path:
    return paths.service_dir(base) / APPROVALS_DIRNAME / f'{sid}.json'


def _exact_item(base: str | os.PathLike[str], trash_id: str) -> Path:
    """把 trash id 收到精确 `_service/trash/`，并拒绝路径链上的 symlink。"""
    if not _TRASH_ID.fullmatch(trash_id):
        raise PathRefused(f'非法 trash_id={trash_id!r}')
    declared = paths.trash_dir(base) / trash_id
    rel = declared.relative_to(Path(os.fspath(base))).as_posix()
    return service_lifecycle._exact_service_target(base, rel)


def _metadata_json(sid: str, title: str, original_dirname: str, *,
                   approval_archived: bool, complete: bool,
                   service_runs_archived: bool = False,
                   operation: str = 'trash') -> str:
    """回收站条目的元数据。`complete` 是硬中断恢复唯一的判据落点。"""
    return json.dumps({
        '_schema_version': 3,
        'sid': sid,
        'title': title,
        'original_dirname': original_dirname,
        'trashed_at': int(time.time()),
        'approval_archived': approval_archived,
        'service_runs_archived': service_runs_archived,
        'operation': operation,
        'complete': complete,
    }, ensure_ascii=False, indent=2) + '\n'


def _sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False).encode('utf-8')


def _sealed_receipt(payload: Mapping[str, object]) -> dict[str, object]:
    """给永久小回执加自哈希；状态变化必须重新封印。"""
    sealed = dict(payload)
    sealed['receipt_sha256'] = '0' * 64
    sealed['receipt_sha256'] = _sha256(_canonical({
        key: value for key, value in sealed.items()
        if key != 'receipt_sha256'}))
    return sealed


def _file_identity(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise BookTrashFailed(f'回收站只接受普通文件：{path}')
    data = path.read_bytes()
    return {'present': True, 'bytes': len(data), 'sha256': _sha256(data)}


def _optional_tree_identity(path: Path) -> dict[str, object]:
    if not path.exists():
        return {'present': False, 'files': 0, 'bytes': 0, 'tree_sha256': None}
    identity = service_lifecycle.tree_identity(path)
    return {'present': True, **{
        key: identity[key] for key in ('files', 'bytes', 'tree_sha256')}}


def _manifest_payload(
        *, sid: str, title: str, original_dirname: str, book: Path,
        approval: Path, service_runs: Path,
) -> dict[str, object]:
    book_identity = service_lifecycle.tree_identity(book)
    payload: dict[str, object] = {
        '_schema_version': 1, 'sid': paths.check_sid(sid), 'title': title,
        'original_dirname': original_dirname,
        'book': {'present': True, **{
            key: book_identity[key]
            for key in ('files', 'bytes', 'tree_sha256')}},
        'approval': (
            _file_identity(approval) if approval.is_file()
            else {'present': False, 'bytes': 0, 'sha256': None}),
        'service_runs': _optional_tree_identity(service_runs),
        'manifest_sha256': '0' * 64,
    }
    payload['manifest_sha256'] = _sha256(_canonical({
        key: value for key, value in payload.items()
        if key != 'manifest_sha256'}))
    return payload


def _read_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BookTrashFailed(f'回收站记录不可读：{path}: {exc}') from exc
    if not isinstance(value, dict):
        raise BookTrashFailed(f'回收站记录不是 JSON object：{path}')
    return value


def _validate_manifest(
        item: Path, manifest: dict[str, object], *,
        max_entries: int | None = None,
) -> None:
    expected_hash = _sha256(_canonical({
        key: value for key, value in manifest.items()
        if key != 'manifest_sha256'}))
    if (manifest.get('_schema_version') != 1
            or manifest.get('manifest_sha256') != expected_hash):
        raise BookTrashFailed(f'{item}/manifest.json 的版本或自哈希不闭合')
    book = manifest.get('book')
    service_runs = manifest.get('service_runs')
    approval = manifest.get('approval')
    if not all(isinstance(value, dict) for value in (book, service_runs, approval)):
        raise BookTrashFailed(f'{item}/manifest.json 缺三面身份')
    assert isinstance(book, dict) and isinstance(service_runs, dict)
    assert isinstance(approval, dict)
    if book.get('present') is not True or not (item / 'book').is_dir():
        raise BookTrashFailed(f'{item} 缺 manifest 声明的 book/')
    actual_book = service_lifecycle.tree_identity(
        item / 'book', max_entries=max_entries)
    for key in ('files', 'bytes', 'tree_sha256'):
        if book.get(key) != actual_book.get(key):
            raise BookTrashFailed(f'{item}/book 与 manifest 的 {key} 漂移')
    archived_runs = item / 'service_runs'
    if service_runs.get('present') is True:
        if not archived_runs.is_dir():
            raise BookTrashFailed(f'{item} 缺 manifest 声明的 service_runs/')
        actual_runs = service_lifecycle.tree_identity(
            archived_runs, max_entries=max_entries)
        for key in ('files', 'bytes', 'tree_sha256'):
            if service_runs.get(key) != actual_runs.get(key):
                raise BookTrashFailed(f'{item}/service_runs 与 manifest 的 {key} 漂移')
    elif archived_runs.exists():
        raise BookTrashFailed(f'{item} 出现 manifest 未声明的 service_runs/')
    archived_approval = item / 'approval.json'
    if approval.get('present') is True:
        if not archived_approval.is_file():
            raise BookTrashFailed(f'{item} 缺 manifest 声明的 approval.json')
        actual = _file_identity(archived_approval)
        if actual != approval:
            raise BookTrashFailed(f'{item}/approval.json 与 manifest 漂移')
    elif archived_approval.exists():
        raise BookTrashFailed(f'{item} 出现 manifest 未声明的 approval.json')


def complete_item_proof(
        base: str | os.PathLike[str], trash_id: str, *,
        max_entries: int | None = None,
) -> dict[str, object] | None:
    """返回可 TTL/恢复的完整 v3 条目证明；legacy/半成品一律 ``None``。"""
    if not _TRASH_ID.fullmatch(trash_id):
        return None
    try:
        item = _exact_item(base, trash_id)
    except PathRefused:
        return None
    if item.is_symlink() or not item.is_dir():
        return None
    try:
        metadata = _read_object(item / 'metadata.json')
        manifest_path = item / 'manifest.json'
        manifest = _read_object(manifest_path)
        sid = paths.check_sid(str(metadata.get('sid') or ''))
        if (metadata.get('_schema_version') != 3
                or metadata.get('operation') != 'trash'
                or metadata.get('complete') is not True
                or manifest.get('sid') != sid
                or manifest.get('title') != metadata.get('title')
                or manifest.get('original_dirname')
                   != metadata.get('original_dirname')):
            return None
        _validate_manifest(item, manifest, max_entries=max_entries)
        trashed_at = metadata.get('trashed_at')
        if not isinstance(trashed_at, int) or isinstance(trashed_at, bool):
            return None
        return {
            'sid': sid, 'trashed_at': trashed_at,
            'manifest_sha256': manifest['manifest_sha256'],
            'manifest_file_sha256': _sha256(manifest_path.read_bytes()),
        }
    except (BookTrashFailed, PathRefused, OSError, ValueError):
        return None


@dataclass(frozen=True)
class TrashRestorePlan:
    """一次恢复 preview 的完整 CAS；apply 只消费这份不可变计划。"""

    generated_at: int
    trash_id: str
    sid: str
    title: str
    original_dirname: str
    metadata_sha256: str
    manifest_file_sha256: str
    manifest_sha256: str
    plan_token: str

    def _unsigned(self) -> dict[str, object]:
        return {
            'wire_version': 1,
            'generated_at': self.generated_at,
            'trash_id': self.trash_id,
            'sid': self.sid,
            'title': self.title,
            'original_dirname': self.original_dirname,
            'metadata_sha256': self.metadata_sha256,
            'manifest_file_sha256': self.manifest_file_sha256,
            'manifest_sha256': self.manifest_sha256,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._unsigned(), 'plan_token': self.plan_token}

    @classmethod
    def create(cls, **values: object) -> 'TrashRestorePlan':
        provisional = cls(**values, plan_token='0' * 64)  # type: ignore[arg-type]
        return cls(
            **values,  # type: ignore[arg-type]
            plan_token=_sha256(_canonical(provisional._unsigned())))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> 'TrashRestorePlan':
        expected = {
            'wire_version', 'generated_at', 'trash_id', 'sid', 'title',
            'original_dirname', 'metadata_sha256', 'manifest_file_sha256',
            'manifest_sha256', 'plan_token',
        }
        if set(raw) != expected or raw.get('wire_version') != 1:
            raise BookTrashFailed('trash restore plan 版本或字段不闭合')
        generated_at = raw['generated_at']
        if (not isinstance(generated_at, int) or isinstance(generated_at, bool)
                or generated_at < 0):
            raise BookTrashFailed('trash restore plan generated_at 不合法')
        hashes = (
            raw['metadata_sha256'], raw['manifest_file_sha256'],
            raw['manifest_sha256'], raw['plan_token'])
        if any(
                not isinstance(value, str) or len(value) != 64
                or any(ch not in '0123456789abcdef' for ch in value)
                for value in hashes):
            raise BookTrashFailed('trash restore plan SHA-256 不合法')
        plan = cls(
            generated_at=generated_at,
            trash_id=str(raw['trash_id']),
            sid=paths.check_sid(str(raw['sid'])),
            title=str(raw['title']),
            original_dirname=str(raw['original_dirname']),
            metadata_sha256=str(raw['metadata_sha256']),
            manifest_file_sha256=str(raw['manifest_file_sha256']),
            manifest_sha256=str(raw['manifest_sha256']),
            plan_token=str(raw['plan_token']))
        if not _TRASH_ID.fullmatch(plan.trash_id) or not plan.title:
            raise BookTrashFailed('trash restore plan 的 trash_id/title 不合法')
        if plan.plan_token != _sha256(_canonical(plan._unsigned())):
            raise BookTrashFailed('trash restore plan token 不闭合')
        return plan


@dataclass(frozen=True)
class TrashRestoreReconcileResult:
    """Bounded startup reconcile summary; blocked receipts remain untouched."""

    examined: int
    reconciled: tuple[str, ...]
    blocked: tuple[str, ...]


def preview_restore(
        base: str | os.PathLike[str], trash_id: str, expected_title: str, *,
        now: int | None = None,
) -> TrashRestorePlan:
    """只读生成恢复计划；树身份、元数据与 manifest 全部进入 token。"""
    if not _TRASH_ID.fullmatch(trash_id):
        raise BookTrashFailed(f'非法 trash_id={trash_id!r}')
    receipt_path = _restore_receipt_path(base, trash_id)
    if receipt_path.exists():
        receipt = _load_restore_receipt(base, trash_id)
        if receipt.get('state') in ('prepared', 'committed'):
            if receipt.get('title') != expected_title:
                raise SourceConflict(
                    f'恢复确认绑定《{expected_title}》，receipt 实际是'
                    f'《{receipt.get("title")}》')
            manifest = receipt['manifest']
            assert isinstance(manifest, dict)
            return TrashRestorePlan.create(
                generated_at=int(time.time()) if now is None else now,
                trash_id=trash_id, sid=str(receipt['sid']),
                title=expected_title,
                original_dirname=str(receipt['original_dirname']),
                metadata_sha256=str(receipt['metadata_file_sha256']),
                manifest_file_sha256=str(receipt['manifest_file_sha256']),
                manifest_sha256=str(manifest['manifest_sha256']))
    item = _exact_item(base, trash_id)
    metadata_path = item / 'metadata.json'
    manifest_path = item / 'manifest.json'
    metadata = _read_object(metadata_path)
    manifest = _read_object(manifest_path)
    metadata_bytes = metadata_path.read_bytes()
    manifest_bytes = manifest_path.read_bytes()
    sid = paths.check_sid(str(metadata.get('sid') or ''))
    if (metadata.get('_schema_version') != 3
            or metadata.get('operation') != 'trash'
            or metadata.get('complete') is not True):
        raise BookTrashFailed('只允许恢复完整 v3 trash；半成品/legacy 保持关闭式')
    if metadata.get('title') != expected_title:
        raise SourceConflict(
            f'恢复确认绑定《{expected_title}》，回收站实际是《{metadata.get("title")}》')
    _validate_manifest(item, manifest)
    if manifest.get('sid') != sid or manifest.get('title') != expected_title:
        raise BookTrashFailed('trash metadata 与 manifest 的 sid/title 不一致')
    original_dirname = str(metadata.get('original_dirname') or '')
    try:
        actual_sid, actual_title, _platform = paths.derive_source_meta(
            original_dirname)
    except Exception as exc:  # noqa: BLE001 —— 外部 trash 元数据只能报关闭式损坏
        raise BookTrashFailed('manifest 的 original_dirname 不再是合法书目录') from exc
    if actual_sid != sid or actual_title != expected_title:
        raise BookTrashFailed('manifest 原目录名与 sid/title 不闭合')
    live_book = Path(base) / original_dirname
    if (live_book.exists() or _approval_path(base, sid).exists()
            or (paths.service_runs_dir(base) / sid).exists()):
        raise BookTrashFailed('live 位置已有同书 book/approval/service run，拒绝覆盖')
    return TrashRestorePlan.create(
        generated_at=int(time.time()) if now is None else now,
        trash_id=trash_id, sid=sid, title=expected_title,
        original_dirname=original_dirname,
        metadata_sha256=_sha256(metadata_bytes),
        manifest_file_sha256=_sha256(manifest_bytes),
        manifest_sha256=str(manifest['manifest_sha256']))


def _restore_plan_matches(
        plan: TrashRestorePlan, metadata_path: Path, manifest_path: Path,
        metadata: Mapping[str, object], manifest: Mapping[str, object],
) -> bool:
    return (
        _sha256(metadata_path.read_bytes()) == plan.metadata_sha256
        and _sha256(manifest_path.read_bytes()) == plan.manifest_file_sha256
        and manifest.get('manifest_sha256') == plan.manifest_sha256
        and metadata.get('sid') == plan.sid
        and metadata.get('title') == plan.title
        and metadata.get('original_dirname') == plan.original_dirname)


def purge_complete_item(
        base: str | os.PathLike[str], trash_id: str, *,
        expected_manifest_sha256: str, max_entries: int | None = None,
) -> Path:
    """永久清除已过显式 TTL 的条目；prepared 是删后补尾的唯一凭据。

    ``rmtree`` 与 committed receipt 之间无法成为一个文件系统事务。因而重试时
    若 item 已不存在，只在盘上存在自哈希闭合、且精确绑定本次 manifest 文件
    SHA 的 v2 prepared receipt 时补 committed；普通的“目录没了”仍关闭式失败。
    """
    item = _exact_item(base, trash_id)
    target = (
        paths.service_dir(base) / service_lifecycle.TRASH_RECEIPTS_DIRNAME
        / f'{trash_id}.json')

    def valid_receipt(record: Mapping[str, object]) -> bool:
        metadata = record.get('metadata')
        manifest = record.get('manifest')
        prepared_fields = {
            '_schema_version', 'kind', 'trash_id', 'sid', 'metadata',
            'manifest', 'manifest_file_sha256', 'state', 'prepared_at',
            'receipt_sha256',
        }
        expected_fields = (
            prepared_fields | {'purged_at'}
            if record.get('state') == 'committed' else prepared_fields)
        return (
            set(record) == expected_fields
            and record.get('_schema_version') == 2
            and record.get('kind') == 'trash_purged'
            and record.get('trash_id') == trash_id
            and record.get('state') in ('prepared', 'committed')
            and record.get('manifest_file_sha256')
            == expected_manifest_sha256
            and isinstance(metadata, dict)
            and isinstance(manifest, dict)
            and metadata.get('_schema_version') == 3
            and metadata.get('operation') == 'trash'
            and metadata.get('complete') is True
            and record.get('sid') == metadata.get('sid') == manifest.get('sid')
            and metadata.get('title') == manifest.get('title')
            and metadata.get('original_dirname')
            == manifest.get('original_dirname')
            and manifest.get('manifest_sha256') == _sha256(_canonical({
                key: value for key, value in manifest.items()
                if key != 'manifest_sha256'}))
            and record.get('receipt_sha256') == _sha256(_canonical({
                key: value for key, value in record.items()
                if key != 'receipt_sha256'})))

    existing = _read_object(target) if target.exists() else None
    if existing is not None and not valid_receipt(existing):
        raise BookTrashFailed(f'永久 trash receipt 已存在且身份冲突：{target}')

    if not item.exists():
        if existing is None:
            raise BookTrashFailed(
                '回收站条目已不存在，且没有 prepared proof；拒绝把未知缺失当成功')
        if existing.get('state') == 'committed':
            return target
        receipt = existing
    else:
        if existing is not None and existing.get('state') == 'committed':
            raise BookTrashFailed(
                f'已 committed 的回收站条目重新出现，拒绝删除新目标：{item}')
        proof = complete_item_proof(
            base, trash_id, max_entries=max_entries)
        if (proof is None
                or proof.get('manifest_file_sha256')
                != expected_manifest_sha256):
            raise BookTrashFailed('回收站条目不再匹配 preview，拒绝永久清除')
        metadata = _read_object(item / 'metadata.json')
        manifest = _read_object(item / 'manifest.json')
        if existing is None:
            receipt = _sealed_receipt({
                '_schema_version': 2, 'kind': 'trash_purged',
                'trash_id': trash_id, 'sid': proof['sid'],
                'metadata': metadata, 'manifest': manifest,
                'manifest_file_sha256': expected_manifest_sha256,
                'state': 'prepared', 'prepared_at': int(time.time()),
            })
            service_lifecycle._atomic_json(target, receipt)
        else:
            if (existing.get('sid') != proof['sid']
                    or existing.get('manifest') != manifest
                    or existing.get('metadata') != metadata):
                raise BookTrashFailed(
                    f'永久 trash receipt 与仍在盘的条目身份冲突：{target}')
            receipt = existing

    if item.exists():
        shutil.rmtree(item)
    committed = _sealed_receipt({
        **receipt, 'state': 'committed', 'purged_at': int(time.time()),
    })
    service_lifecycle._atomic_json(target, committed)
    return target


def _tree_matches(path: Path, descriptor: Mapping[str, object]) -> bool:
    if descriptor.get('present') is not True or not path.is_dir():
        return False
    actual = service_lifecycle.tree_identity(path)
    return all(
        actual.get(key) == descriptor.get(key)
        for key in ('files', 'bytes', 'tree_sha256'))


def _file_matches(path: Path, descriptor: Mapping[str, object]) -> bool:
    if descriptor.get('present') is not True:
        return not path.exists()
    return path.is_file() and not path.is_symlink() \
        and _file_identity(path) == dict(descriptor)


def _restore_receipt_path(
        base: str | os.PathLike[str], trash_id: str,
) -> Path:
    rel = (
        paths.service_dir(base) / service_lifecycle.TRASH_RECEIPTS_DIRNAME
        / f'{trash_id}.restore.json').relative_to(Path(base)).as_posix()
    return service_lifecycle._exact_service_target(base, rel)


def _restore_surfaces(
        base: str | os.PathLike[str], item: Path, *, sid: str,
        original_dirname: str, manifest: Mapping[str, object],
) -> tuple[dict[str, object], ...]:
    """Return the three exact restore moves in deterministic dependency order."""
    definitions = (
        ('service_runs', 'tree', item / 'service_runs',
         paths.service_runs_dir(base) / sid),
        ('approval', 'file', item / 'approval.json',
         _approval_path(base, sid)),
        ('book', 'tree', item / 'book', Path(base) / original_dirname),
    )
    surfaces: list[dict[str, object]] = []
    for name, kind, archived, live in definitions:
        descriptor = manifest.get(name)
        if not isinstance(descriptor, dict):
            raise BookTrashFailed(f'trash manifest 缺 {name} 身份')
        surfaces.append({
            'name': name,
            'kind': kind,
            'archived_rel': archived.relative_to(Path(base)).as_posix(),
            'live_rel': live.relative_to(Path(base)).as_posix(),
            'descriptor': dict(descriptor),
        })
    return tuple(surfaces)


def _prepared_restore_receipt(
        base: str | os.PathLike[str], item: Path, *, trash_id: str,
        metadata: Mapping[str, object], manifest: Mapping[str, object],
        metadata_file_sha256: str, manifest_file_sha256: str,
) -> dict[str, object]:
    sid = paths.check_sid(str(metadata.get('sid') or ''))
    title = str(metadata.get('title') or '')
    original_dirname = str(metadata.get('original_dirname') or '')
    surfaces = _restore_surfaces(
        base, item, sid=sid, original_dirname=original_dirname,
        manifest=manifest)
    return _sealed_receipt({
        '_schema_version': 2, 'kind': 'trash_restored',
        'state': 'prepared', 'trash_id': trash_id, 'sid': sid,
        'title': title, 'original_dirname': original_dirname,
        # Both objects contain only identities/counts; no novel text is copied
        # into the permanent recovery receipt.
        'metadata': dict(metadata), 'manifest': dict(manifest),
        'metadata_file_sha256': metadata_file_sha256,
        'manifest_file_sha256': manifest_file_sha256,
        'surfaces': list(surfaces), 'prepared_at': int(time.time()),
    })


def _load_restore_receipt(
        base: str | os.PathLike[str], trash_id: str,
) -> dict[str, object]:
    path = _restore_receipt_path(base, trash_id)
    record = _read_object(path)
    prepared_fields = {
        '_schema_version', 'kind', 'state', 'trash_id', 'sid', 'title',
        'original_dirname', 'metadata', 'manifest',
        'metadata_file_sha256', 'manifest_file_sha256', 'surfaces',
        'prepared_at', 'receipt_sha256',
    }
    by_state = {
        'prepared': prepared_fields,
        'committed': prepared_fields | {'committed_at'},
        'rolled_back': prepared_fields | {'rolled_back_at'},
    }
    state = record.get('state')
    metadata = record.get('metadata')
    manifest = record.get('manifest')
    surfaces = record.get('surfaces')
    if (state not in by_state
            or set(record) != by_state[state]  # type: ignore[index]
            or record.get('_schema_version') != 2
            or record.get('kind') != 'trash_restored'
            or record.get('trash_id') != trash_id
            or not isinstance(metadata, dict)
            or not isinstance(manifest, dict)
            or not isinstance(surfaces, list)
            or record.get('receipt_sha256') != _sealed_receipt(record).get(
                'receipt_sha256')):
        raise BookTrashFailed(f'restore receipt 版本、字段或自哈希不闭合：{path}')
    sid = paths.check_sid(str(record.get('sid') or ''))
    title = str(record.get('title') or '')
    original_dirname = str(record.get('original_dirname') or '')
    try:
        derived_sid, derived_title, _platform = paths.derive_source_meta(
            original_dirname)
    except Exception as exc:  # noqa: BLE001 —— receipt 是不可信盘上输入
        raise BookTrashFailed(
            f'restore receipt 的 original_dirname 不合法：{path}') from exc
    if (not title or metadata.get('_schema_version') != 3
            or derived_sid != sid or derived_title != title
            or metadata.get('sid') != sid or metadata.get('title') != title
            or metadata.get('original_dirname') != original_dirname
            or manifest.get('sid') != sid or manifest.get('title') != title
            or manifest.get('original_dirname') != original_dirname
            or manifest.get('manifest_sha256') != _sha256(_canonical({
                key: value for key, value in manifest.items()
                if key != 'manifest_sha256'}))):
        raise BookTrashFailed(f'restore receipt 的 metadata/manifest 身份不闭合：{path}')
    for field in ('metadata_file_sha256', 'manifest_file_sha256'):
        value = record.get(field)
        if (not isinstance(value, str) or len(value) != 64
                or any(ch not in '0123456789abcdef' for ch in value)):
            raise BookTrashFailed(f'restore receipt 的 {field} 不合法：{path}')
    item = _exact_item(base, trash_id)
    expected_surfaces = list(_restore_surfaces(
        base, item, sid=sid, original_dirname=original_dirname,
        manifest=manifest))
    if surfaces != expected_surfaces:
        raise BookTrashFailed(f'restore receipt 的逐面路径/身份不闭合：{path}')
    return record


def _surface_paths(
        base: str | os.PathLike[str], surface: Mapping[str, object],
) -> tuple[Path, Path]:
    archived = service_lifecycle._exact_service_target(
        base, str(surface['archived_rel']))
    live_rel = str(surface['live_rel'])
    live = (Path(base) / live_rel).absolute()
    root = Path(base).absolute()
    try:
        live.relative_to(root)
    except ValueError as exc:
        raise BookTrashFailed(f'restore live 目标越出 base：{live_rel}') from exc
    probe = live
    while probe != root:
        if probe.exists() and probe.is_symlink():
            raise BookTrashFailed(f'restore live 路径含符号链接：{probe}')
        probe = probe.parent
    return archived, live


def _matches_restore_surface(
        path: Path, *, kind: str, descriptor: Mapping[str, object],
) -> bool:
    if path.is_symlink():
        raise BookTrashFailed(f'restore 面不允许符号链接：{path}')
    if kind == 'tree':
        return _tree_matches(path, descriptor)
    if kind == 'file':
        return _file_matches(path, descriptor)
    raise BookTrashFailed(f'restore receipt 含未知面类型：{kind!r}')


def _restore_surface_location(
        base: str | os.PathLike[str], surface: Mapping[str, object],
) -> tuple[str, Path, Path]:
    descriptor = surface.get('descriptor')
    kind = str(surface.get('kind') or '')
    name = str(surface.get('name') or '')
    if not isinstance(descriptor, dict):
        raise BookTrashFailed(f'restore {name} 面缺 descriptor')
    archived, live = _surface_paths(base, surface)
    archived_exists = archived.exists() or archived.is_symlink()
    live_exists = live.exists() or live.is_symlink()
    if descriptor.get('present') is not True:
        if archived_exists or live_exists:
            raise BookTrashFailed(
                f'restore {name} 声明 absent，但 archive/live 出现了实体')
        return 'absent', archived, live
    if archived_exists == live_exists:
        reason = '两侧同时存在' if archived_exists else '两侧都缺失'
        raise BookTrashFailed(f'restore {name} {reason}；拒绝猜测权威副本')
    located = archived if archived_exists else live
    if not _matches_restore_surface(
            located, kind=kind, descriptor=descriptor):
        raise BookTrashFailed(
            f'restore {name} 位于 {located}，但 SHA/tree 身份与 prepared receipt 漂移')
    return ('archived' if archived_exists else 'live'), archived, live


def _restore_fault(_point: str) -> None:
    """Fault-injection seam; production deliberately does nothing."""


def _cleanup_restored_item(
        base: str | os.PathLike[str], trash_id: str,
        receipt: Mapping[str, object],
) -> None:
    item = _exact_item(base, trash_id)
    if not item.exists():
        return
    if item.is_symlink() or not item.is_dir():
        raise BookTrashFailed(f'restore 清壳目标不是普通目录：{item}')
    allowed = {'metadata.json', 'manifest.json'}
    unexpected = sorted(child.name for child in item.iterdir()
                        if child.name not in allowed)
    if unexpected:
        raise BookTrashFailed(
            f'restore 已补齐 live，但 trash 壳含陌生条目，拒绝删除：{unexpected}')
    manifest_path = item / 'manifest.json'
    if manifest_path.exists():
        if (manifest_path.is_symlink() or not manifest_path.is_file()
                or _sha256(manifest_path.read_bytes())
                   != receipt.get('manifest_file_sha256')):
            raise BookTrashFailed('restore 清壳前 manifest 文件身份漂移')
    metadata_path = item / 'metadata.json'
    if metadata_path.exists():
        if metadata_path.is_symlink() or not metadata_path.is_file():
            raise BookTrashFailed('restore 清壳前 metadata 不是普通文件')
        metadata = _read_object(metadata_path)
        if (metadata.get('sid') != receipt.get('sid')
                or metadata.get('title') != receipt.get('title')
                or metadata.get('original_dirname')
                   != receipt.get('original_dirname')
                or metadata.get('operation') not in ('trash', 'restore')):
            raise BookTrashFailed('restore 清壳前 metadata 身份漂移')
    # Validation of every known shell file is complete before the first unlink.
    if manifest_path.exists():
        manifest_path.unlink()
    if metadata_path.exists():
        metadata_path.unlink()
    item.rmdir()


def _finish_restore_locked(
        base: str | os.PathLike[str], trash_id: str,
        receipt: Mapping[str, object],
) -> dict[str, str]:
    state = receipt.get('state')
    if state not in ('prepared', 'committed'):
        raise BookTrashFailed(f'restore receipt state={state!r} 不能补尾')
    surfaces = receipt.get('surfaces')
    assert isinstance(surfaces, list)
    locations = [
        _restore_surface_location(base, surface)
        for surface in surfaces if isinstance(surface, dict)]
    if len(locations) != len(surfaces):
        raise BookTrashFailed('restore receipt surfaces 含非对象')
    if state == 'prepared':
        for surface, (location, archived, live) in zip(surfaces, locations):
            if location != 'archived':
                continue
            live.parent.mkdir(parents=True, exist_ok=True)
            os.replace(archived, live)
            _restore_fault(f"after_{surface['name']}")
        # Every move is re-proved from disk before the irreversible state flip.
        for surface in surfaces:
            location, _archived, _live = _restore_surface_location(base, surface)
            if location not in ('live', 'absent'):
                raise BookTrashFailed(
                    f"restore {surface['name']} 未落到 live，拒绝提交")
        item = _exact_item(base, trash_id)
        if item.is_symlink() or not item.is_dir():
            raise BookTrashFailed('restore prepared 的 trash 壳缺失或不是普通目录')
        unexpected = sorted(
            child.name for child in item.iterdir()
            if child.name not in {'metadata.json', 'manifest.json'})
        if unexpected:
            raise BookTrashFailed(
                f'restore 提交前 trash 壳仍有未闭合面：{unexpected}')
        receipt = _sealed_receipt({
            **receipt, 'state': 'committed', 'committed_at': int(time.time()),
        })
        service_lifecycle._atomic_json(
            _restore_receipt_path(base, trash_id), receipt)
        _restore_fault('after_commit')
    else:
        for surface in surfaces:
            location, _archived, _live = _restore_surface_location(base, surface)
            if location not in ('live', 'absent'):
                raise BookTrashFailed(
                    f"已 committed 的 restore {surface['name']} 不在 live")
    _cleanup_restored_item(base, trash_id, receipt)
    return {
        'sid': str(receipt['sid']), 'title': str(receipt['title']),
        'trash_id': trash_id,
        'book_rel': str(receipt['original_dirname']),
    }


def reconcile_restore(
        base: str | os.PathLike[str], trash_id: str, *,
        expected_title: str | None = None,
        registry: jobs.JobRegistry = jobs.REGISTRY,
) -> dict[str, str]:
    """Deterministically finish one prepared/committed restore after a kill."""
    if not _TRASH_ID.fullmatch(trash_id):
        raise BookTrashFailed(f'非法 trash_id={trash_id!r}')
    receipt = _load_restore_receipt(base, trash_id)
    sid = paths.check_sid(str(receipt['sid']))
    if expected_title is not None and receipt.get('title') != expected_title:
        raise SourceConflict(
            f'恢复确认绑定《{expected_title}》，receipt 实际是《{receipt.get("title")}》')
    writer_id = f'trash-restore-reconcile-{trash_id}'
    key = jobs.acquire_job_writer(
        base, sid, 'book.restore-reconcile', writer_id, registry=registry)
    try:
        with approval_exclusion_lock(base, sid):
            current = _load_restore_receipt(base, trash_id)
            if current != receipt:
                raise BookTrashFailed('restore receipt 在 reconcile 占锁前发生变化')
            return _finish_restore_locked(base, trash_id, current)
    finally:
        registry.release(key, writer_id)


def _rollback_restore_locked(
        base: str | os.PathLike[str], trash_id: str,
        prepared: Mapping[str, object],
) -> None:
    """Compensate an ordinary exception; ambiguity leaves prepared for reconcile."""
    surfaces = prepared.get('surfaces')
    metadata = prepared.get('metadata')
    if not isinstance(surfaces, list) or not isinstance(metadata, dict):
        raise BookTrashFailed('restore prepared receipt 缺回滚材料')
    for surface in reversed(surfaces):
        if not isinstance(surface, dict):
            raise BookTrashFailed('restore prepared surfaces 含非对象')
        location, archived, live = _restore_surface_location(base, surface)
        if location == 'live':
            archived.parent.mkdir(parents=True, exist_ok=True)
            os.replace(live, archived)
    # Re-prove every declared present surface is back in trash before saying
    # rolled_back.  A failed compensation retains the prepared receipt.
    for surface in surfaces:
        location, _archived, _live = _restore_surface_location(base, surface)
        if location not in ('archived', 'absent'):
            raise BookTrashFailed(
                f"restore {surface['name']} 未完整回到 trash，保留 prepared")
    item = _exact_item(base, trash_id)
    service_lifecycle._atomic_json(item / 'metadata.json', metadata)
    manifest_path = item / 'manifest.json'
    if (not manifest_path.is_file() or manifest_path.is_symlink()
            or _sha256(manifest_path.read_bytes())
               != prepared.get('manifest_file_sha256')):
        raise BookTrashFailed('restore 回滚后的 manifest 身份不闭合')
    rolled_back = _sealed_receipt({
        **prepared, 'state': 'rolled_back', 'rolled_back_at': int(time.time()),
    })
    service_lifecycle._atomic_json(
        _restore_receipt_path(base, trash_id), rolled_back)


def restore(
        base: str | os.PathLike[str], trash_id: str, expected_title: str, *,
        plan: TrashRestorePlan | None = None,
        registry: jobs.JobRegistry = jobs.REGISTRY,
) -> dict[str, str]:
    """显式恢复 v3 回收站条目；book/approval/service run 同一 writer 边界。

    正常异常会把三面原样回滚到 trash。进程被强杀时，v2 prepared receipt
    逐面证明 archive/live 目标；同一显式调用可幂等补尾，启动 reconcile 也只在
    三面各自唯一且 SHA/tree 闭合时自动 reconcile。任何歧义都原样留盘。
    """
    if not _TRASH_ID.fullmatch(trash_id):
        raise BookTrashFailed(f'非法 trash_id={trash_id!r}')
    if plan is not None:
        plan = TrashRestorePlan.from_mapping(plan.to_dict())
        if plan.trash_id != trash_id or plan.title != expected_title:
            raise BookTrashFailed('trash restore plan 与 apply 目标不一致')
    receipt_path = _restore_receipt_path(base, trash_id)
    if receipt_path.exists():
        existing_receipt = _load_restore_receipt(base, trash_id)
        if existing_receipt.get('state') in ('prepared', 'committed'):
            existing_manifest = existing_receipt['manifest']
            assert isinstance(existing_manifest, dict)
            if plan is not None and (
                    existing_receipt.get('sid') != plan.sid
                    or existing_receipt.get('title') != plan.title
                    or existing_receipt.get('original_dirname')
                       != plan.original_dirname
                    or existing_receipt.get('metadata_file_sha256')
                       != plan.metadata_sha256
                    or existing_receipt.get('manifest_file_sha256')
                       != plan.manifest_file_sha256
                    or existing_manifest.get('manifest_sha256')
                       != plan.manifest_sha256):
                raise BookTrashFailed(
                    'prepared restore receipt 与冻结 preview plan 不一致')
            return reconcile_restore(
                base, trash_id, expected_title=expected_title,
                registry=registry)
    item = _exact_item(base, trash_id)
    metadata_path = item / 'metadata.json'
    manifest_path = item / 'manifest.json'
    metadata = _read_object(metadata_path)
    sid = paths.check_sid(str(metadata.get('sid') or ''))
    if (metadata.get('_schema_version') != 3
            or metadata.get('operation') != 'trash'
            or metadata.get('complete') is not True):
        raise BookTrashFailed('只允许恢复完整 v3 trash；半成品/legacy 保持关闭式')
    if metadata.get('title') != expected_title:
        raise SourceConflict(
            f'恢复确认绑定《{expected_title}》，回收站实际是《{metadata.get("title")}》')
    manifest = _read_object(manifest_path)
    _validate_manifest(item, manifest)
    if manifest.get('sid') != sid:
        raise BookTrashFailed('trash metadata 与 manifest 的 sid 不一致')
    if plan is not None and not _restore_plan_matches(
            plan, metadata_path, manifest_path, metadata, manifest):
        raise BookTrashFailed('trash restore preview 后盘面已变化，请重新 preview')

    writer_id = f'trash-restore-{trash_id}'
    key = jobs.acquire_job_writer(
        base, sid, 'book.restore', writer_id, registry=registry)
    try:
        with approval_exclusion_lock(base, sid):
            # 拿到跨 writer/file 两把锁后重读；preview 前的对象不能继续当依据。
            current_meta = _read_object(metadata_path)
            current_manifest = _read_object(manifest_path)
            if current_meta != metadata or current_manifest != manifest:
                raise BookTrashFailed('回收站条目在恢复占锁前发生变化，请重新确认')
            _validate_manifest(item, current_manifest)
            if plan is not None and not _restore_plan_matches(
                    plan, metadata_path, manifest_path,
                    current_meta, current_manifest):
                raise BookTrashFailed('trash restore preview token 已失效，请重新 preview')
            live_book = Path(base) / str(metadata['original_dirname'])
            try:
                actual_sid, actual_title, _platform = paths.derive_source_meta(
                    live_book.name)
            except Exception as exc:  # noqa: BLE001
                raise BookTrashFailed('manifest 的 original_dirname 不再是合法书目录') from exc
            if actual_sid != sid or actual_title != expected_title:
                raise BookTrashFailed('manifest 原目录名与 sid/title 不闭合')
            live_approval = _approval_path(base, sid)
            live_runs = paths.service_runs_dir(base) / sid
            if live_book.exists() or live_approval.exists() or live_runs.exists():
                raise BookTrashFailed('live 位置已有同书 book/approval/service run，拒绝覆盖')

            prepared = _prepared_restore_receipt(
                base, item, trash_id=trash_id,
                metadata=current_meta, manifest=current_manifest,
                metadata_file_sha256=_sha256(metadata_path.read_bytes()),
                manifest_file_sha256=_sha256(manifest_path.read_bytes()))
            if receipt_path.exists():
                old_receipt = _read_object(receipt_path)
                old_manifest = old_receipt.get('manifest')
                if (old_receipt.get('state') != 'rolled_back'
                        or not isinstance(old_manifest, dict)
                        or old_manifest.get('manifest_sha256')
                           != manifest.get('manifest_sha256')
                        or old_receipt.get('receipt_sha256')
                           != _sealed_receipt(old_receipt).get(
                               'receipt_sha256')):
                    raise BookTrashFailed(f'restore receipt 已存在且不可重试：{receipt_path}')
            service_lifecycle._atomic_json(receipt_path, prepared)
            service_lifecycle._atomic_json(metadata_path, {
                **current_meta, 'operation': 'restore', 'complete': False,
            })
            try:
                return _finish_restore_locked(base, trash_id, prepared)
            except Exception:
                current_receipt = _load_restore_receipt(base, trash_id)
                if current_receipt.get('state') == 'prepared':
                    _rollback_restore_locked(
                        base, trash_id, current_receipt)
                raise
    finally:
        registry.release(key, writer_id)


def apply_restore(
        base: str | os.PathLike[str], plan: TrashRestorePlan, *,
        registry: jobs.JobRegistry = jobs.REGISTRY,
) -> dict[str, str]:
    """显式 apply：完整重验 token，并让 restore 在持锁窗口内做最终 CAS。"""
    checked = TrashRestorePlan.from_mapping(plan.to_dict())
    return restore(
        base, checked.trash_id, checked.title,
        plan=checked, registry=registry)


def load_restore_plan(path: str | os.PathLike[str]) -> TrashRestorePlan:
    value = _read_object(Path(path))
    return TrashRestorePlan.from_mapping(value)


def build_restore_argv(
        base: str | os.PathLike[str], plan_path: str | os.PathLike[str],
) -> list[str]:
    """API 与人工维护共用的受控恢复 child argv；服务进程不移动产物。"""
    return [
        sys.executable, '-X', 'utf8', '-m', RESTORE_CLI_MODULE,
        '--base', os.fspath(base), '--plan', os.fspath(plan_path), '--yes',
    ]


def build_restore_reconcile_argv(
        base: str | os.PathLike[str], trash_id: str, expected_title: str,
) -> list[str]:
    """Startup recovery uses the same CLI boundary without inventing a plan file."""
    if not _TRASH_ID.fullmatch(trash_id):
        raise BookTrashFailed(f'非法 trash_id={trash_id!r}')
    return [
        sys.executable, '-X', 'utf8', '-m', RESTORE_CLI_MODULE,
        '--base', os.fspath(base), '--trash-id', trash_id,
        '--expected-title', expected_title, '--yes',
    ]


def reconcile_pending_restores(
        base: str | os.PathLike[str], *, spawn: Spawn | None = None,
        max_receipts: int = 128,
) -> TrashRestoreReconcileResult:
    """Bounded service-start scan; all book moves remain inside the CLI child.

    Invalid or ambiguous receipts are reported as blocked and left byte-for-byte
    for maintenance preview/apply.  They do not prevent the local service from
    starting, because that service is the explicit recovery UI.
    """
    if (not isinstance(max_receipts, int) or isinstance(max_receipts, bool)
            or max_receipts < 1):
        raise ValueError('max_receipts 必须是正整数')
    root = paths.service_dir(base) / service_lifecycle.TRASH_RECEIPTS_DIRNAME
    if not root.is_dir():
        return TrashRestoreReconcileResult(0, (), ())
    if root.is_symlink():
        raise PathRefused(f'trash receipts 根不能是符号链接：{root}')
    entries: list[Path] = []
    for path in root.iterdir():
        entries.append(path)
        if len(entries) > max_receipts:
            # Do not partially reconcile a directory whose complete workset
            # was not observed within the configured startup bound.
            return TrashRestoreReconcileResult(
                len(entries), (), ('scan_limit_exceeded',))
    candidates: list[tuple[str, Path]] = []
    for path in sorted(entries, key=lambda item: item.name):
        match = re.fullmatch(r'([0-9]{10}-[0-9a-f]{8})\.restore\.json', path.name)
        if match is None:
            continue
        candidates.append((match.group(1), path))
    reconciled: list[str] = []
    blocked: list[str] = []
    runner = spawn or _default_spawn
    for trash_id, _receipt_path in candidates:
        try:
            receipt = _load_restore_receipt(base, trash_id)
        except (BookTrashFailed, PathRefused, OSError, ValueError):
            blocked.append(trash_id)
            continue
        if receipt.get('state') not in ('prepared', 'committed'):
            continue
        if (receipt.get('state') == 'committed'
                and not _exact_item(base, trash_id).exists()):
            # Permanent committed receipts are retained for audit; once their
            # exact trash shell is gone they are not startup work forever.
            continue
        argv = build_restore_reconcile_argv(
            base, trash_id, str(receipt['title']))
        try:
            exit_code, _output = runner(
                argv, os.fspath(REPO_ROOT), child_env())
        except OSError:
            blocked.append(trash_id)
            continue
        if exit_code == 0:
            reconciled.append(trash_id)
        else:
            blocked.append(trash_id)
    return TrashRestoreReconcileResult(
        examined=len(candidates), reconciled=tuple(reconciled),
        blocked=tuple(blocked))


def run_restore(
        base: str | os.PathLike[str], plan: TrashRestorePlan, *,
        spawn: Spawn | None = None,
) -> dict[str, str]:
    """服务端恢复入口：只在 ``_service`` 写临时计划，真实移动由 CLI child 完成。"""
    checked = TrashRestorePlan.from_mapping(plan.to_dict())
    session_id = f'trash-restore-{checked.trash_id}-{uuid.uuid4().hex[:8]}'
    scratch = service_lifecycle.open_scratch_session(
        base, checked.sid, session_id)
    plan_path = scratch / 'restore-plan.json'
    service_lifecycle._atomic_json(plan_path, checked.to_dict())
    argv = build_restore_argv(base, plan_path)
    runner = spawn or _default_spawn
    try:
        exit_code, output = runner(
            argv, os.fspath(REPO_ROOT), child_env())
        if exit_code != 0:
            detail = output.decode('utf-8', errors='replace').strip()
            raise BookTrashFailed(
                f'trash restore CLI 退出码 {exit_code}'
                + (f'：{detail}' if detail else ''))
        try:
            value = json.loads(output.decode('utf-8'))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise BookTrashFailed('trash restore CLI 没有返回合法 JSON') from exc
        if not isinstance(value, dict):
            raise BookTrashFailed('trash restore CLI 响应不是 JSON object')
        result = {key: str(value.get(key) or '') for key in (
            'sid', 'title', 'trash_id', 'book_rel')}
        if (not all(result.values()) or result['sid'] != checked.sid
                or result['trash_id'] != checked.trash_id
                or result['title'] != checked.title):
            raise BookTrashFailed('trash restore CLI 响应与冻结计划不闭合')
        return result
    finally:
        service_lifecycle.finish_scratch_session(
            base, checked.sid, session_id)


def sweep_incomplete(
        base: str | os.PathLike[str], *,
        registry: jobs.JobRegistry = jobs.REGISTRY) -> list[str]:
    """把硬中断留下的半成品回收站条目收干净，返回处理过的 trash_id。

    ⚠⚠ **半成品只有一种形状**：没有 `book/` **且** `metadata.complete` 不为真。
    此时 `execute` 死在了「审批已归档」与「书目录已移走」之间——书还在语料库里
    活着，而它的审批只存在于一个没人会去读的回收站目录。放着不管的后果不是丢数据，
    是**读数说不清**：那本书看起来「从来没批过」，而批准记录明明还在盘上。
    ⇒ 把审批搬回 live（live 位置已有新的一份时不覆盖，改名留证），再删掉空条目。

    ⚠ **有 `book/` 的条目一律不碰**，哪怕 `complete` 缺失：`complete` 是本次
    新加的字段，旧条目没有它，而它们的书目录确实已经移进来了——那是删除**成功**。
    判据取「书在不在」这个盘上事实，不取一个可能还没写过的标记。
    """
    root = paths.trash_dir(base)
    if root.is_symlink():
        raise PathRefused(f'回收站根不能是符号链接：{root}')
    if not root.is_dir():
        return []
    now = int(time.time())
    swept: list[str] = []
    for item in sorted(root.iterdir()):
        if not item.is_dir() or not _TRASH_ID.fullmatch(item.name):
            continue
        # ⚠⚠ **「还没写完」与「写到一半死了」在盘上长得一模一样。** 一次正在进行的
        #   删除，在 `os.replace(approval)` 与 `os.replace(novel_dir)` 之间的那一瞬
        #   恰好就是半成品的形状；这时把它的归档审批搬回 live，子进程随后的回滚
        #   条件（`not approval.exists()`）就被破坏了。⇒ 本进程发起的那些直接跳过；
        #   别的进程（人手敲 CLI）留下的靠宽限期区分。
        with _IN_FLIGHT_LOCK:
            in_flight = item.name in _IN_FLIGHT
        if in_flight:
            continue
        restore_receipt = _restore_receipt_path(base, item.name)
        if restore_receipt.exists():
            try:
                receipt = _load_restore_receipt(base, item.name)
                if receipt.get('state') in ('prepared', 'committed'):
                    reconcile_restore(base, item.name, registry=registry)
                    swept.append(item.name)
                    continue
            except (BookTrashFailed, PathRefused, jobs.JobConflict, OSError):
                # Automatic reconcile is deliberately conservative.  A bad
                # receipt, live owner, duplicate surface, missing surface, or
                # changed SHA/tree remains on disk for the explicit command to
                # report in full; the legacy delete-sweep must not reinterpret it.
                continue
        meta_path = item / 'metadata.json'
        try:
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            meta = {}
        # A restore-shaped item without a valid prepared receipt remains
        # closed.  The branch above is the only automatic restore reconciler.
        if meta.get('operation') == 'restore':
            continue
        if (item / 'book').exists():
            continue
        if meta.get('complete') is True:
            continue
        trashed_at = meta.get('trashed_at')
        if isinstance(trashed_at, int) and now - trashed_at < STALE_AFTER_SECONDS:
            continue
        archived_approval = item / 'approval.json'
        raw_sid = str(meta.get('sid') or '')
        try:
            sid = paths.check_sid(raw_sid) if raw_sid else ''
        except PathRefused:
            # metadata 是回收站里的不可信盘上输入；坏 sid 只能留下人工检查，
            # 不能借恢复路径越出 approvals 目录。
            sid = ''
        if not sid:
            # 没有可信 sid 就没有可证明正确的书级 writer key；完整保留给人工检查，
            # 尤其不能先移动 approval 再试图补锁。
            continue

        writer_id = f'trash-sweep-{item.name}'
        try:
            key = jobs.acquire_job_writer(
                base, sid, 'book.trash', writer_id, registry=registry)
        except jobs.JobConflict:
            # 同书 writer / durable Commander admission 仍活着：这一轮什么都不动。
            # sweep 是恢复器，不值得为抢锁破坏一个真实运行；下一次会重新扫描。
            continue
        try:
            # 上面的筛选发生在拿锁前，只能用来决定「值不值得尝试」。拿到书级
            # writer 后必须从盘上重读：另一个 sweep/删除可能已经完成，metadata
            # 也可能换了 sid。未经这次复核不得移动任何审批字节。
            if not item.is_dir() or (item / 'book').exists():
                continue
            with _IN_FLIGHT_LOCK:
                if item.name in _IN_FLIGHT:
                    continue
            try:
                current_meta = json.loads(
                    meta_path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if (current_meta.get('operation') == 'restore'
                    or current_meta.get('complete') is True):
                continue
            current_trashed_at = current_meta.get('trashed_at')
            if (isinstance(current_trashed_at, int)
                    and now - current_trashed_at < STALE_AFTER_SECONDS):
                continue
            try:
                current_sid = paths.check_sid(
                    str(current_meta.get('sid') or ''))
            except PathRefused:
                continue
            if current_sid != sid:
                continue

            archived_service_runs = item / 'service_runs'
            if archived_service_runs.is_dir():
                live_runs = paths.service_runs_dir(base) / sid
                if live_runs.exists():
                    archived_service_runs.replace(
                        item / 'service_runs.superseded')
                else:
                    live_runs.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(archived_service_runs, live_runs)

            archived_approval = item / 'approval.json'
            if archived_approval.is_file():
                # 与 HTTP/CLI apply 共用审批文件锁。live 是否存在、选择恢复还是
                # supersede、以及移动本身必须是同一段临界区，不能只锁 os.replace。
                # 外层书级 writer 已经挡住 agent/job；这把只解决审批文件本身。
                with approval_exclusion_lock(base, sid):
                    if not archived_approval.is_file():
                        # 另一 sweep 已在我们等待锁时收完；后面的幂等清壳继续即可。
                        continue
                    live = _approval_path(base, sid)
                    if live.exists():
                        archived_approval.replace(
                            item / 'approval.superseded.json')
                    else:
                        live.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(archived_approval, live)
            try:
                # v3 半成品的 manifest 只在 approval/service run 都已经归位、
                # 没留下 superseded 证据时随壳清掉；完整条目的 manifest 永久跟书。
                leftovers = any(
                    path.exists() for path in (
                        item / 'approval.json', item / 'approval.superseded.json',
                        item / 'service_runs', item / 'service_runs.superseded'))
                if not leftovers and meta_path.exists():
                    meta_path.unlink()
                manifest_path = item / 'manifest.json'
                if not leftovers and manifest_path.exists():
                    manifest_path.unlink()
                if not any(item.iterdir()):
                    item.rmdir()
            except OSError:
                # 别人正好也在收同一条。审批已经归位是唯一要紧的事，空壳留着无害。
                continue
            swept.append(item.name)
        finally:
            registry.release(key, writer_id)
    return swept


def execute(base: str | os.PathLike[str], sid: str, expected_title: str,
            trash_id: str) -> TrashNovelResult:
    """CLI 子进程里的原子边界；整段占用与 approval apply 相同的文件锁。"""
    if not _TRASH_ID.fullmatch(trash_id):
        raise BookTrashFailed(f'非法 trash_id={trash_id!r}')
    with approval_exclusion_lock(base, sid):
        return _execute_locked(base, sid, expected_title, trash_id)


def _execute_locked(base: str | os.PathLike[str], sid: str, expected_title: str,
                    trash_id: str) -> TrashNovelResult:
    """书目录最后移动；任何前置失败都把审批文件恢复到原位。"""
    novel_dir = paths.resolve_novel_dir(base, sid)
    title = paths.title_of(novel_dir)
    if title != expected_title:
        raise SourceConflict(
            f'删除确认绑定的是《{expected_title}》，当前 sid={sid} 实际是《{title}》。'
            '书名或项目状态已经变化，请重新打开确认框。')

    item = _exact_item(base, trash_id)
    if item.exists():
        raise BookTrashFailed(f'回收站目标已经存在：{item}')

    approval = _approval_path(base, sid)
    service_runs = paths.service_runs_dir(base) / paths.check_sid(sid)
    archived_approval = item / 'approval.json'
    archived_service_runs = item / 'service_runs'
    archived_book = item / 'book'
    metadata = item / 'metadata.json'
    manifest_path = item / 'manifest.json'
    approval_moved = False
    service_runs_moved = False
    manifest = _manifest_payload(
        sid=sid, title=title, original_dirname=novel_dir.name,
        book=novel_dir, approval=approval, service_runs=service_runs)
    try:
        item.mkdir(parents=True, exist_ok=False)
        # ⚠ **`complete` 先写 false，成功后才改 true。** 这两次 `os.replace` 之间
        #   若进程被硬中断（断电 / taskkill），盘上会留下「审批已归档、书还在原位」
        #   的半成品——而它对外看起来与「删成功了」一模一样。
        #   哨兵让 `sweep_incomplete` 事后分得出这两种，从而把审批搬回去。
        metadata.write_text(_metadata_json(
            sid, title, novel_dir.name,
            approval_archived=approval.is_file(),
            service_runs_archived=service_runs.is_dir(), complete=False),
            encoding='utf-8')
        service_lifecycle._atomic_json(manifest_path, manifest)
        if approval.is_file():
            os.replace(approval, archived_approval)
            approval_moved = True
        if service_runs.is_dir():
            os.replace(service_runs, archived_service_runs)
            service_runs_moved = True
        # 最后一步才让项目从语料库消失；此前任何失败都不碰正式书目录。
        os.replace(novel_dir, archived_book)
        metadata.write_text(_metadata_json(
            sid, title, novel_dir.name,
            approval_archived=approval_moved,
            service_runs_archived=service_runs_moved, complete=True),
            encoding='utf-8')
    except Exception:
        if (service_runs_moved and archived_service_runs.is_dir()
                and not service_runs.exists()):
            service_runs.parent.mkdir(parents=True, exist_ok=True)
            os.replace(archived_service_runs, service_runs)
            service_runs_moved = False
        if approval_moved and archived_approval.is_file() and not approval.exists():
            approval.parent.mkdir(parents=True, exist_ok=True)
            os.replace(archived_approval, approval)
            approval_moved = False
        if not archived_book.exists():
            if archived_approval.is_file():
                # 回滚没动它只有一种可能：live 位置在归档之后又被写了出来。
                # 那两份**不是同一件事**，静默留在半成品里等于让下一次 sweep
                # 拿旧账盖掉新账 ⇒ 就地改名留证，人可读、机器不会误当恢复源。
                archived_approval.replace(item / 'approval.superseded.json')
            if not archived_service_runs.exists() and not archived_approval.exists():
                if metadata.exists():
                    metadata.unlink()
                if manifest_path.exists():
                    manifest_path.unlink()
            if item.exists() and not any(item.iterdir()):
                item.rmdir()
        raise

    rel = archived_book.relative_to(Path(os.fspath(base))).as_posix()
    return TrashNovelResult(
        sid=sid, title=title, trash_id=trash_id, trash_rel=rel)


def run(base: str | os.PathLike[str], sid: str, expected_title: str, *,
        spawn: Spawn = _default_spawn,
        registry: jobs.JobRegistry = jobs.REGISTRY) -> TrashNovelResult:
    """服务侧同步入口：拒绝队列/运行冲突，占书锁后拉零成本 CLI 子进程。"""
    sid = paths.check_sid(sid)
    # 上一次删除若被硬中断，盘上会留下半成品条目（审批已归档、书还在原位）。
    # 在开新的一次之前先收干净：否则那本书的审批一直躺在回收站里，读数说不清。
    sweep_incomplete(base, registry=registry)
    trash_id = new_trash_id()
    writer_id = 'trash-' + trash_id
    key = jobs.acquire_job_writer(
        base, sid, 'book.trash', writer_id, registry=registry)
    with _IN_FLIGHT_LOCK:
        _IN_FLIGHT.add(trash_id)
    try:
        # 上面的共享协调口已经在同一个临界区完成「查 Commander + 占书级 writer」。
        # 两边的 acquire 都不等待，谁先拿到，另一边都明确 409；不能退回各自
        # 先查后占，否则 agent/trash 可以同时看见空表再各拿一把。
        queued = {item.sid for item in agent.queue_view(base).pending}
        if sid in queued:
            raise jobs.JobConflict('这本书还在 agent 排队中——先取消/等队列处理完再删除')
        novel_dir = paths.resolve_novel_dir(base, sid)
        title = paths.title_of(novel_dir)
        if title != expected_title:
            raise SourceConflict(
                f'删除确认绑定的是《{expected_title}》，当前项目是《{title}》，请重新确认')
        argv = build_argv(base, sid, expected_title, trash_id)
        # ⚠⚠ **删除的原子边界是这个 `with`，不是子进程那一次 `os.replace`。**
        #   （历史）旧「父进程审批写入」那一半随审批写面退役消失
        #   （frozen_for_trash 已删）；现存的一半是 agent 待跑队列：上面那次
        #   `queue_view` 只是开跑前的一张快照，spawn 期间新入队的这本书会在
        #   锁一还回去就被 `_drain` 启动 ⇒「书删完了 agent 才起来」。
        #   `drop_pending` **在锁内**堵这条（`agent.start` 那道
        #   `conflicting_holder` 覆盖这中间的每一刻）。
        rc, output = spawn(argv, str(REPO_ROOT), child_env())
        if rc != 0:
            detail = output.decode('utf-8', errors='replace').strip()
            raise BookTrashFailed(
                f'项目没有安全移入回收站（exit {rc}）'
                + (f'：{detail[-1200:]}' if detail else ''))
        agent.drop_pending(base, sid)
        rel = (paths.trash_dir(base) / trash_id / 'book').relative_to(
            Path(os.fspath(base))).as_posix()
        return TrashNovelResult(
            sid=sid, title=title, trash_id=trash_id, trash_rel=rel)
    finally:
        with _IN_FLIGHT_LOCK:
            _IN_FLIGHT.discard(trash_id)
        registry.release(key, writer_id)
