#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把两本 Stage3 v1 实验书安全移出 live discovery，并留下人工恢复证据。

默认只读预检：核对两本书的 sid/书名/最终成书 SHA、整书 inventory、service
approval 与实验 decision。只有显式 ``--execute`` 才逐本调用
``novel.orchestrator.trash.run``；本工具不直接调用 ``novel.cli.trash``，也不实现
第二套移动逻辑。

终态 manifest 固定落在 ``<base>/_service/archive_manifests/``（随 Output 一起被
Git 忽略）。它记录 trash recovery id/path，但**不提供一键恢复**：恢复前必须验证
原书目录与 live approval 目标都不存在，再在同一卷上人工移回。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from novel import paths, prov  # noqa: E402
from novel.errors import NovelError  # noqa: E402
from novel.orchestrator import approvals, trash  # noqa: E402


BOOK_COUNT = 2
SCHEMA_VERSION = 1
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST_NAME = 'stage3-v1-books.json'
EXPECTED_EXPERIMENT_ID = 'stage3-stage6-style-ab-20260824-v1'
RUN_MANIFEST_NAME = 'run-manifest.json'
COMPLETE_NAME = 'COMPLETE.json'
_SHA256 = re.compile(r'[0-9a-f]{64}')
_TRASH_ID = re.compile(r'[0-9]{10}-[0-9a-f]{8}')
_EXTERNAL_PREFIXES = ('\\\\?\\', '\\\\.\\')


class ArchiveRefused(RuntimeError):
    """预检、冻结输入或归档后验不成立。"""


def _resolved(path: str | os.PathLike[str]) -> Path:
    raw = os.fspath(path)
    if raw.replace('/', '\\').startswith(_EXTERNAL_PREFIXES):
        raise ArchiveRefused('外部路径不接受 Windows extended/device prefix')
    return Path(raw).expanduser().resolve(strict=False)


def _io_path(path: str | os.PathLike[str]) -> str:
    raw = os.path.abspath(os.fspath(path))
    if os.name != 'nt' or raw.startswith('\\\\?\\'):
        return raw
    if raw.startswith('\\\\'):
        return '\\\\?\\UNC\\' + raw[2:]
    return '\\\\?\\' + raw


def _sha256_io(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256_io(_io_path(path))


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        with open(_io_path(path), 'r', encoding='utf-8') as handle:
            value = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ArchiveRefused(f'{label} 不是合法 UTF-8 JSON：{path}') from exc
    if not isinstance(value, dict):
        raise ArchiveRefused(f'{label} 顶层必须是 object')
    return value


def _is_linklike(path: str | os.PathLike[str]) -> bool:
    raw = _io_path(path)
    if os.path.islink(raw):
        return True
    # Python 3.11 on Windows has no ``os.path.isjunction``.  ``lstat`` exposes
    # the reparse-point file attribute there, and rejecting every reparse point
    # is deliberately stricter than trying to distinguish junction flavours:
    # an inventory must never walk through a filesystem redirect.
    try:
        attrs = getattr(os.lstat(raw), 'st_file_attributes', 0)
    except FileNotFoundError:
        return False
    reparse = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)
    if reparse and attrs & reparse:
        return True
    isjunction = getattr(os.path, 'isjunction', None)
    return bool(isjunction is not None and isjunction(raw))


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _check_self_hash(value: Mapping[str, Any], *, field: str, label: str) -> str:
    output_sha = value.get(field)
    unsigned = {key: item for key, item in value.items() if key != field}
    if not isinstance(output_sha, str) or output_sha != _canonical_sha256(unsigned):
        raise ArchiveRefused(f'{label} {field} 自校验失败')
    return output_sha


def _inventory(book: Path) -> dict[str, Any]:
    """全书文件的路径、大小与内容共同入摘要；普通 manifest 不承载正文/文件表。"""
    io_root = _io_path(book)
    entries: list[tuple[str, int, str]] = []
    for current, directories, files in os.walk(io_root):
        directories.sort()
        files.sort()
        for name in directories:
            full = os.path.join(current, name)
            if _is_linklike(full):
                raise ArchiveRefused(f'书目录含符号链接，拒绝跨边界归档：{full}')
        for name in files:
            full = os.path.join(current, name)
            if _is_linklike(full):
                raise ArchiveRefused(f'书目录含符号链接，拒绝跨边界归档：{full}')
            rel = os.path.relpath(full, io_root).replace(os.sep, '/')
            entries.append((rel, os.path.getsize(full), _sha256_io(full)))
    entries.sort(key=lambda row: row[0])
    digest = hashlib.sha256()
    total_bytes = 0
    for rel, size, file_sha in entries:
        rel_bytes = rel.encode('utf-8')
        digest.update(len(rel_bytes).to_bytes(8, 'big'))
        digest.update(rel_bytes)
        digest.update(size.to_bytes(8, 'big'))
        digest.update(bytes.fromhex(file_sha))
        total_bytes += size
    return {
        'algorithm': 'sha256(rel+size+file_sha256)-v1',
        'sha256': digest.hexdigest(),
        'files': len(entries),
        'bytes': total_bytes,
    }


def _git_head() -> str:
    completed = subprocess.run(
        ['git', '-c', f'safe.directory={REPO_ROOT.as_posix()}',
         'rev-parse', 'HEAD'],
        cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise ArchiveRefused('无法冻结 git HEAD，拒绝归档')
    head = completed.stdout.decode('ascii', errors='strict').strip()
    if not re.fullmatch(r'[0-9a-f]{40}', head):
        raise ArchiveRefused(f'git HEAD 不是完整 40 位：{head!r}')
    return head


def _expect_sha256(value: str, *, field: str) -> str:
    normalized = value.strip().lower()
    if not _SHA256.fullmatch(normalized):
        raise ArchiveRefused(f'{field} 必须是 64 位 SHA-256')
    return normalized


def _decision_evidence(path: Path, expected_sha256: str) -> dict[str, Any]:
    if not path.is_file():
        raise ArchiveRefused(f'实验 decision 不存在：{path}')
    raw_sha = _file_sha256(path)
    if raw_sha != expected_sha256:
        raise ArchiveRefused(
            f'实验 decision SHA 漂移：expected={expected_sha256}, actual={raw_sha}')
    value = _read_json(path, label='实验 decision')
    output_sha = _check_self_hash(
        value, field='output_sha256', label='实验 decision')
    if value.get('schema_version') != 1:
        raise ArchiveRefused('实验 decision schema_version 必须为 1')
    experiment_id = value.get('experiment_id')
    if experiment_id != EXPECTED_EXPERIMENT_ID:
        raise ArchiveRefused(
            f'实验 decision 不属于本次消融：{experiment_id!r}')
    decision = value.get('decision')
    if decision not in ('keep_and_localize', 'delete_qualitative', 'inconclusive'):
        raise ArchiveRefused(f'实验 decision 取值越界：{decision!r}')

    run_sha = value.get('run_manifest_sha256')
    if not isinstance(run_sha, str) or not _SHA256.fullmatch(run_sha):
        raise ArchiveRefused('实验 decision 缺合法 run_manifest_sha256')
    run_path = path.parent / RUN_MANIFEST_NAME
    if not run_path.is_file() or _file_sha256(run_path) != run_sha:
        raise ArchiveRefused('实验 decision 绑定的 run-manifest 原始字节不匹配')
    run = _read_json(run_path, label='实验 run-manifest')
    experiment = run.get('experiment')
    if not isinstance(experiment, dict):
        raise ArchiveRefused('实验 run-manifest.experiment 必须是 object')
    if (experiment.get('experiment_id') != experiment_id
            or experiment.get('status') != 'complete'):
        raise ArchiveRefused('实验 run-manifest 未以同一 experiment_id 完整结束')
    source_base = experiment.get('source_base')
    if not isinstance(source_base, str):
        raise ArchiveRefused('实验 run-manifest 缺 source_base')
    run_books = run.get('books')
    if not isinstance(run_books, list) or len(run_books) != BOOK_COUNT:
        raise ArchiveRefused('实验 run-manifest 必须恰有两本 books')
    book_refs: list[dict[str, str]] = []
    for index, item in enumerate(run_books, 1):
        if not isinstance(item, dict):
            raise ArchiveRefused('实验 run-manifest books 项必须是 object')
        expected_key = f'B{index:02d}'
        book_key = item.get('book_key')
        sid = item.get('sid')
        source_book = item.get('source_book')
        if (book_key != expected_key or not isinstance(sid, str)
                or not isinstance(source_book, str)):
            raise ArchiveRefused(
                f'实验 run-manifest 第 {index} 本身份不完整或 book_key 失序')
        book_refs.append({
            'book_key': book_key,
            'sid': paths.check_sid(sid),
            'source_book': str(_resolved(source_book)),
        })

    complete_path = path.parent / COMPLETE_NAME
    if not complete_path.is_file():
        raise ArchiveRefused('实验 COMPLETE.json 不存在')
    complete = _read_json(complete_path, label='实验 COMPLETE')
    complete_sha = _check_self_hash(
        complete, field='output_sha256', label='实验 COMPLETE')
    if (complete.get('experiment_id') != experiment_id
            or complete.get('run_manifest_sha256') != run_sha
            or str(_resolved(str(complete.get('run_manifest')))) != str(run_path.resolve())):
        raise ArchiveRefused('实验 COMPLETE 与 decision/run-manifest 绑定不闭合')
    return {
        'path': str(path),
        'sha256': raw_sha,
        'output_sha256': output_sha,
        'experiment_id': experiment_id,
        'decision': decision,
        'run_manifest_path': str(run_path.resolve()),
        'run_manifest_sha256': run_sha,
        'complete_path': str(complete_path.resolve()),
        'complete_output_sha256': complete_sha,
        'source_base': str(_resolved(source_base)),
        'books': book_refs,
    }


def _approval_path(base: Path, sid: str) -> Path:
    return paths.service_dir(base) / approvals.APPROVALS_DIRNAME / f'{sid}.json'


def _book_snapshot(
    base: Path, *, sid: str, expected_title: str,
    expected_final_sha256: str,
) -> dict[str, Any]:
    sid = paths.check_sid(sid)
    located = paths.resolve_novel_dir(base, sid)
    if _is_linklike(located):
        raise ArchiveRefused(f'sid={sid} 书目录是链接/联接点，拒绝跨边界归档')
    book = located.resolve()
    try:
        book.relative_to(base.resolve())
    except ValueError as exc:
        raise ArchiveRefused(f'sid={sid} 书目录 resolve 后越出 output base') from exc
    title = paths.title_of(book)
    if title != expected_title:
        raise ArchiveRefused(
            f'sid={sid} 书名漂移：expected={expected_title!r}, actual={title!r}')
    final_rel = paths.book_txt_rel(book)
    final_path = book / final_rel
    if not final_path.is_file():
        raise ArchiveRefused(f'sid={sid} 最终成书不存在：{final_rel}')
    final_sha = _file_sha256(final_path)
    if final_sha != expected_final_sha256:
        raise ArchiveRefused(
            f'sid={sid} final SHA 漂移：expected={expected_final_sha256}, '
            f'actual={final_sha}')
    approval = _approval_path(base, sid)
    if approval.exists() and not approval.is_file():
        raise ArchiveRefused(f'sid={sid} service approval 落点存在但不是文件')
    if _is_linklike(approval):
        raise ArchiveRefused(f'sid={sid} service approval 是链接/联接点，拒绝归档')
    approval_exists = approval.is_file()
    return {
        'sid': sid,
        'expected_title': expected_title,
        'original_dirname': book.name,
        'original_path': str(book),
        'inventory': _inventory(book),
        'final': {
            'rel': final_rel,
            'expected_sha256': expected_final_sha256,
            'sha256': final_sha,
        },
        'approval': {
            'path': str(approval),
            'exists': approval_exists,
            'sha256': _file_sha256(approval) if approval_exists else None,
        },
    }


def _manifest_path(base: Path, requested: str | os.PathLike[str] | None) -> Path:
    directory = paths.service_dir(base) / 'archive_manifests'
    candidate = (directory / DEFAULT_MANIFEST_NAME
                 if requested is None else _resolved(requested))
    if candidate.parent.resolve(strict=False) != directory.resolve(strict=False):
        raise ArchiveRefused(
            f'archive manifest 必须直接位于 Git-ignored service 目录：{directory}')
    return candidate


def _bind_decision_books(
    evidence: Mapping[str, Any], base: Path,
    snapshots: Sequence[Mapping[str, Any]],
) -> None:
    if Path(evidence['source_base']) != base:
        raise ArchiveRefused('实验 run-manifest.source_base 不是本次 live output base')
    refs = evidence['books']
    if len(refs) != len(snapshots):
        raise ArchiveRefused('实验 run-manifest 的书数与归档书数不同')
    for index, (ref, snapshot) in enumerate(zip(refs, snapshots), 1):
        if (ref['book_key'] != f'B{index:02d}'
                or ref['sid'] != snapshot['sid']
                or Path(ref['source_book']) != Path(snapshot['original_path'])):
            raise ArchiveRefused(
                f'实验 run-manifest 的 B{index:02d} 没有绑定本次同一本 live 书')


def build_plan(
    *, base: str | os.PathLike[str], books: Sequence[Sequence[str]],
    decision: str | os.PathLike[str], expected_decision_sha256: str,
    manifest: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """只读冻结两书归档计划；不会创建 manifest/service/trash 目录。"""
    root = _resolved(base)
    if not root.is_dir():
        raise ArchiveRefused(f'live output base 不存在：{root}')
    if len(books) != BOOK_COUNT:
        raise ArchiveRefused(f'必须显式给恰好 {BOOK_COUNT} 本书，实得 {len(books)}')
    normalized: list[tuple[str, str, str]] = []
    for index, row in enumerate(books, 1):
        if len(row) != 3:
            raise ArchiveRefused(f'第 {index} 个 --book 必须是 SID TITLE FINAL_SHA256')
        sid, title, final_sha = row
        normalized.append((
            paths.check_sid(sid), title,
            _expect_sha256(final_sha, field=f'book {index} final SHA'),
        ))
    if len({sid for sid, _title, _sha in normalized}) != BOOK_COUNT:
        raise ArchiveRefused('两本书的 sid 必须不同')
    decision_path = _resolved(decision)
    decision_sha = _expect_sha256(
        expected_decision_sha256, field='expected decision SHA')
    evidence = _decision_evidence(decision_path, decision_sha)
    snapshots = [
        _book_snapshot(
            root, sid=sid, expected_title=title,
            expected_final_sha256=final_sha,
        )
        for sid, title, final_sha in normalized
    ]
    _bind_decision_books(evidence, root, snapshots)
    payload: dict[str, Any] = {
        '_schema_version': SCHEMA_VERSION,
        'operation': 'archive-stage3-v1-experiment-books',
        'base': str(root),
        'manifest_path': str(_manifest_path(root, manifest)),
        'git_head': _git_head(),
        'code_version': prov.code_version(),
        'decision': evidence,
        'books': [
            {'book_key': f'B{index:02d}', **snapshot}
            for index, snapshot in enumerate(snapshots, 1)
        ],
        'recovery_contract': {
            'automatic_restore': False,
            'same_volume_manual_move': True,
            'target_must_not_exist': True,
            'fact': (
                '恢复前必须验证原书目录与 live approval 目标不存在；随后在同一卷上'
                '人工移回 archived book，并仅在原先存在 approval 时人工移回 approval。'
            ),
        },
    }
    payload['plan_sha256'] = _canonical_sha256(payload)
    return payload


def render(plan: Mapping[str, Any], *, execute: bool) -> str:
    lines = [
        '# Stage3 v1 两书归档预检',
        '',
        f'plan_sha256={plan["plan_sha256"]}',
        f'git_head={plan["git_head"]}',
        f'decision={plan["decision"]["decision"]} '
        f'sha256={plan["decision"]["sha256"]}',
        f'execute={"是" if execute else "否（零写预检）"}',
        '',
    ]
    for book in plan['books']:
        lines.append(
            f'{book["book_key"]} sid={book["sid"]} title={book["expected_title"]!r} '
            f'files={book["inventory"]["files"]} '
            f'inventory={book["inventory"]["sha256"]} '
            f'approval={"present" if book["approval"]["exists"] else "absent"}')
        lines.append(
            f'  final {book["final"]["rel"]} sha256={book["final"]["sha256"]}')
    if not execute:
        lines += [
            '',
            '未加 --execute：没有调用 trash.run，没有创建或移动任何文件。',
            f'执行后 manifest 将写入：{plan["manifest_path"]}',
        ]
    return '\n'.join(lines)


def _verify_globals(plan: Mapping[str, Any]) -> None:
    if _git_head() != plan['git_head']:
        raise ArchiveRefused('git HEAD 在计划冻结后漂移')
    if prov.code_version() != plan['code_version']:
        raise ArchiveRefused('code_version 在计划冻结后漂移')
    decision = plan['decision']
    current = _decision_evidence(Path(decision['path']), decision['sha256'])
    if current != decision:
        raise ArchiveRefused('实验 decision 在计划冻结后漂移')


def _verify_live_book(base: Path, expected: Mapping[str, Any]) -> None:
    current = _book_snapshot(
        base,
        sid=expected['sid'],
        expected_title=expected['expected_title'],
        expected_final_sha256=expected['final']['expected_sha256'],
    )
    comparable = {
        key: value for key, value in expected.items() if key != 'book_key'}
    if current != comparable:
        raise ArchiveRefused(f'sid={expected["sid"]} 整书或 approval 在冻结后漂移')


def _postcheck(
    base: Path, expected: Mapping[str, Any], result: Any,
) -> dict[str, Any]:
    sid = expected['sid']
    if result.sid != sid or result.title != expected['expected_title']:
        raise ArchiveRefused(f'sid={sid} trash.run 返回了别的书身份')
    archive_book = (base / result.trash_rel).resolve(strict=False)
    wanted = (paths.trash_dir(base) / result.trash_id / 'book').resolve(strict=False)
    if archive_book != wanted or not archive_book.is_dir():
        raise ArchiveRefused(f'sid={sid} trash recovery path 不在正式 trash 落点')
    metadata_path = archive_book.parent / 'metadata.json'
    try:
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ArchiveRefused(f'sid={sid} trash metadata 不可读') from exc
    if (metadata.get('complete') is not True
            or metadata.get('original_dirname') != expected['original_dirname']
            or metadata.get('sid') != sid
            or metadata.get('title') != expected['expected_title']
            or metadata.get('approval_archived') is not expected['approval']['exists']):
        raise ArchiveRefused(f'sid={sid} trash metadata 后验不闭合')
    archived_inventory = _inventory(archive_book)
    if archived_inventory != expected['inventory']:
        raise ArchiveRefused(f'sid={sid} archive book inventory 与 live 前像不同')
    archived_final = archive_book / expected['final']['rel']
    if _file_sha256(archived_final) != expected['final']['sha256']:
        raise ArchiveRefused(f'sid={sid} archive final SHA 与 live 前像不同')
    if Path(expected['original_path']).exists() or paths.find_novel_dir(base, sid) is not None:
        raise ArchiveRefused(f'sid={sid} 归档后仍在 live discovery')
    live_approval = Path(expected['approval']['path'])
    if live_approval.exists():
        raise ArchiveRefused(f'sid={sid} 归档后 live approval 仍存在')
    archived_approval = archive_book.parent / 'approval.json'
    if expected['approval']['exists']:
        if (not archived_approval.is_file()
                or _file_sha256(archived_approval) != expected['approval']['sha256']):
            raise ArchiveRefused(f'sid={sid} archived approval 缺失或 SHA 漂移')
    elif archived_approval.exists():
        raise ArchiveRefused(f'sid={sid} 原先无 approval，trash 却凭空出现一份')
    same_volume = (os.path.splitdrive(str(base))[0].casefold()
                   == os.path.splitdrive(str(archive_book))[0].casefold())
    if not same_volume:
        raise ArchiveRefused(f'sid={sid} archive 不在同一卷')
    return {
        'status': 'archived',
        'trash_id': result.trash_id,
        'trash_rel': result.trash_rel,
        'archive_book_path': str(archive_book),
        'metadata_path': str(metadata_path),
        'metadata_complete': True,
        'metadata_original_dirname': metadata['original_dirname'],
        'archive_inventory': archived_inventory,
        'archive_final_sha256': _file_sha256(archived_final),
        'archived_approval_path': str(archived_approval),
        'archived_approval_sha256': (
            _file_sha256(archived_approval) if archived_approval.is_file() else None),
        'same_volume': True,
        'recovery': {
            'automatic_restore': False,
            'original_book_target': expected['original_path'],
            'original_approval_target': expected['approval']['path'],
            'target_must_not_exist': True,
            'method': 'verify targets absent, then manually move back on the same volume',
        },
    }


def _trash_ids(base: Path) -> set[str]:
    """只列正式 trash 的直接子项；不跟随任何重解析点。"""
    root = paths.trash_dir(base)
    if _is_linklike(root):
        raise ArchiveRefused('正式 trash 根不是普通目录，拒绝归档')
    if not root.exists():
        return set()
    if not root.is_dir():
        raise ArchiveRefused('正式 trash 根不是普通目录，拒绝归档')
    with os.scandir(_io_path(root)) as entries:
        return {
            entry.name for entry in entries
            if _TRASH_ID.fullmatch(entry.name)
        }


def _active_call(book: Mapping[str, Any], before: set[str]) -> dict[str, Any]:
    return {
        'book_key': book['book_key'],
        'sid': book['sid'],
        'expected_title': book['expected_title'],
        'original_dirname': book['original_dirname'],
        'original_path': book['original_path'],
        'original_approval_target': book['approval']['path'],
        'approval_was_present': book['approval']['exists'],
        'trash_entries_before': sorted(before),
    }


def _trash_candidate_evidence(
    base: Path, trash_id: str, active: Mapping[str, Any],
) -> dict[str, Any]:
    """对一次调用之后新出现的直接 trash 子项做不跟链的身份留证。"""
    if not _TRASH_ID.fullmatch(trash_id):
        raise ArchiveRefused(f'非法 trash candidate id={trash_id!r}')
    item = paths.trash_dir(base) / trash_id
    book = item / 'book'
    metadata_path = item / 'metadata.json'
    approval = item / 'approval.json'
    item_linklike = _is_linklike(item)
    book_linklike = False if item_linklike else _is_linklike(book)
    metadata_linklike = False if item_linklike else _is_linklike(metadata_path)
    approval_linklike = False if item_linklike else _is_linklike(approval)
    book_present = (not item_linklike and not book_linklike
                    and os.path.isdir(_io_path(book)))
    metadata: dict[str, Any] | None = None
    metadata_error: str | None = None
    if not item_linklike and not metadata_linklike:
        try:
            metadata = _read_json(metadata_path, label='trash metadata')
        except (ArchiveRefused, OSError) as exc:
            metadata_error = f'{type(exc).__name__}: {exc}'
    else:
        metadata_error = 'trash item/metadata 是重解析点，未跟随读取'
    identity_match = bool(
        book_present
        and metadata is not None
        and metadata.get('sid') == active['sid']
        and metadata.get('title') == active['expected_title']
        and metadata.get('original_dirname') == active['original_dirname']
    )
    archived_approval_sha: str | None = None
    if (not item_linklike and not approval_linklike
            and os.path.isfile(_io_path(approval))):
        archived_approval_sha = _file_sha256(approval)
    return {
        'trash_id': trash_id,
        'trash_rel': (item / 'book').relative_to(base).as_posix(),
        'item_path': str(item),
        'archive_book_path': str(book),
        'metadata_path': str(metadata_path),
        'archived_approval_path': str(approval),
        'item_linklike': item_linklike,
        'book_linklike': book_linklike,
        'metadata_linklike': metadata_linklike,
        'approval_linklike': approval_linklike,
        'book_present': book_present,
        'metadata_readable': metadata is not None,
        'metadata_error': metadata_error,
        'metadata_sid': metadata.get('sid') if metadata is not None else None,
        'metadata_title': metadata.get('title') if metadata is not None else None,
        'metadata_original_dirname': (
            metadata.get('original_dirname') if metadata is not None else None),
        'metadata_complete': (
            metadata.get('complete') if metadata is not None else None),
        'metadata_approval_archived': (
            metadata.get('approval_archived') if metadata is not None else None),
        'archived_approval_sha256': archived_approval_sha,
        'identity_match': identity_match,
    }


def _discover_recovery_candidates(
    base: Path, active: Mapping[str, Any],
) -> list[dict[str, Any]]:
    before = set(active['trash_entries_before'])
    return [
        _trash_candidate_evidence(base, trash_id, active)
        for trash_id in sorted(_trash_ids(base) - before)
    ]


def _recovery_archive(
    base: Path, active: Mapping[str, Any], candidate: Mapping[str, Any],
) -> dict[str, Any]:
    archive_book = Path(candidate['archive_book_path'])
    same_volume = (os.path.splitdrive(str(base))[0].casefold()
                   == os.path.splitdrive(str(archive_book))[0].casefold())
    return {
        'book_key': active['book_key'],
        'sid': active['sid'],
        'status': 'trash-moved-run-failed',
        'trash_id': candidate['trash_id'],
        'trash_rel': candidate['trash_rel'],
        'archive_book_path': candidate['archive_book_path'],
        'metadata_path': candidate['metadata_path'],
        'metadata_complete': candidate['metadata_complete'] is True,
        'metadata_original_dirname': candidate['metadata_original_dirname'],
        'archive_inventory': None,
        'archive_final_sha256': None,
        'archived_approval_path': candidate['archived_approval_path'],
        'archived_approval_sha256': candidate['archived_approval_sha256'],
        'same_volume': same_volume,
        'postcheck_complete': False,
        'recovery': {
            'automatic_restore': False,
            'original_book_target': active['original_path'],
            'original_approval_target': active['original_approval_target'],
            'target_must_not_exist': True,
            'method': (
                'trash.run 未正常交付结果；先人工复核 candidate/metadata，验证 live '
                'targets absent，再在同一卷上人工移回'),
        },
    }


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    data = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
            + '\n').encode('utf-8')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _terminal_manifest(
    plan: Mapping[str, Any], archived: Sequence[Mapping[str, Any]], *,
    status: str, failure: Mapping[str, Any] | None,
    recovery_candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        '_schema_version': SCHEMA_VERSION,
        'operation': plan['operation'],
        'status': status,
        'plan_sha256': plan['plan_sha256'],
        'base': plan['base'],
        'git_head': plan['git_head'],
        'code_version': plan['code_version'],
        'decision': plan['decision'],
        'books_before': plan['books'],
        'archives': list(archived),
        'recovery_candidates': list(recovery_candidates),
        'recovery_contract': plan['recovery_contract'],
        'failure': dict(failure) if failure is not None else None,
    }
    payload['output_sha256'] = _canonical_sha256(payload)
    return payload


def execute(plan: Mapping[str, Any]) -> dict[str, Any]:
    """逐本走 ``trash.run``；首个失败即停止，终态清单保留已发生的 recovery。"""
    base = Path(plan['base'])
    manifest_path = Path(plan['manifest_path'])
    progress_path = manifest_path.with_name(manifest_path.name + '.progress.json')
    if manifest_path.exists() or progress_path.exists():
        raise ArchiveRefused(
            f'归档 manifest/progress 已存在，拒绝覆盖或猜测续跑：{manifest_path}')
    archived: list[dict[str, Any]] = []
    progress: dict[str, Any] = {
        '_schema_version': SCHEMA_VERSION,
        'status': 'preflight',
        'plan_sha256': plan['plan_sha256'],
        'base': plan['base'],
        'manifest_path': plan['manifest_path'],
        'git_head': plan['git_head'],
        'code_version': plan['code_version'],
        'decision': plan['decision'],
        'books_before': plan['books'],
        'archives': archived,
        'active_call': None,
        'recovery_candidates': [],
        'recovery_contract': plan['recovery_contract'],
    }
    _atomic_json(progress_path, progress)
    try:
        _verify_globals(plan)
        # 两本一起通过冻结前置，才允许第一本离开 live。
        for book in plan['books']:
            _verify_live_book(base, book)
        progress['status'] = 'running'
        _atomic_json(progress_path, progress)
        for book in plan['books']:
            _verify_globals(plan)
            _verify_live_book(base, book)
            progress['active_call'] = _active_call(book, _trash_ids(base))
            progress['recovery_candidates'] = []
            # 先把调用身份与 trash 前像落盘；即使进程随后被硬杀，人仍能按差分
            # 找到正式入口可能已经移走、却来不及返回 recovery id 的那一项。
            _atomic_json(progress_path, progress)
            result = trash.run(base, book['sid'], book['expected_title'])
            pending = {
                'book_key': book['book_key'],
                'sid': book['sid'],
                'status': 'trash-returned-awaiting-postcheck',
                'trash_id': result.trash_id,
                'trash_rel': result.trash_rel,
            }
            archived.append(pending)
            _atomic_json(progress_path, progress)
            verified = _postcheck(base, book, result)
            archived[-1] = {**pending, **verified}
            progress['active_call'] = None
            _atomic_json(progress_path, progress)
        _verify_globals(plan)
        progress['status'] = 'complete'
        _atomic_json(progress_path, progress)
        terminal = _terminal_manifest(
            plan, archived, status='complete', failure=None,
            recovery_candidates=[])
        _atomic_json(manifest_path, terminal)
        return terminal
    except BaseException as exc:
        # ``KeyboardInterrupt`` / ``SystemExit`` 也可能落在书已经移走之后；尽最大
        # 努力封存，但最后必须原样抛出，不能把中断洗成普通 ArchiveRefused。
        failure: dict[str, Any] = {
            'type': type(exc).__name__, 'error': str(exc),
        }
        candidates: list[dict[str, Any]] = []
        active = progress.get('active_call')
        try:
            if isinstance(active, Mapping):
                candidates = _discover_recovery_candidates(base, active)
                matching = [row for row in candidates if row['identity_match']]
                known_ids = {row.get('trash_id') for row in archived}
                if (len(matching) == 1
                        and matching[0]['trash_id'] not in known_ids):
                    archived.append(_recovery_archive(base, active, matching[0]))
        except BaseException as audit_exc:
            failure['recovery_scan_failure'] = {
                'type': type(audit_exc).__name__, 'error': str(audit_exc),
            }
        progress['status'] = 'failed'
        progress['failure'] = failure
        progress['recovery_candidates'] = candidates
        # 写盘本身也可能是失败根因；任何二次失败都不得覆盖原始异常。
        try:
            _atomic_json(progress_path, progress)
            terminal = _terminal_manifest(
                plan, archived, status='failed', failure=failure,
                recovery_candidates=candidates)
            _atomic_json(manifest_path, terminal)
        except BaseException:
            pass
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, help='含两本 live 书的 Output 根')
    parser.add_argument(
        '--book', action='append', nargs=3, required=True,
        metavar=('SID', 'EXPECTED_TITLE', 'FINAL_SHA256'),
        help='恰好写两次；逐本绑定 sid、书名和最终 txt 原始字节 SHA-256',
    )
    parser.add_argument('--decision', required=True,
                        help='封存的 Stage3/Stage6 实验 decision.json')
    parser.add_argument('--expected-decision-sha256', required=True,
                        help='decision.json 原始文件字节 SHA-256')
    parser.add_argument('--manifest', default=None,
                        help='可选；只能位于 <base>/_service/archive_manifests/')
    parser.add_argument('--execute', action='store_true',
                        help='二次确认：真实调用 trash.run 移出两本 live 书')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        plan = build_plan(
            base=args.base,
            books=args.book,
            decision=args.decision,
            expected_decision_sha256=args.expected_decision_sha256,
            manifest=args.manifest,
        )
        print(render(plan, execute=args.execute))
        if not args.execute:
            return 0
        result = execute(plan)
        print(json.dumps({
            'status': result['status'],
            'manifest': plan['manifest_path'],
            'output_sha256': result['output_sha256'],
        }, ensure_ascii=False))
        return 0
    except (OSError, ValueError, ArchiveRefused, NovelError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
