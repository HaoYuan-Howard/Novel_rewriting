#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""创建/恢复可校验快照。

``code`` profile 收 Git 已跟踪与未忽略源码，但硬排除密钥、本地设置、版权语料、付费输出、
缓存和构建物；``private`` profile 只接受显式数据根，并要求调用者确认目标是受控的加密离线介质。
两种归档都逐文件 SHA-256，restore 只落到空目录并在落盘后复核。
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path, PurePosixPath
from typing import Iterable

MANIFEST_NAME = 'BACKUP_MANIFEST.json'
FORMAT_VERSION = 1

_CODE_EXCLUDED_TOP = {
    '.git', '.llm_cache', '.pytest_cache', '.mypy_cache', '.ruff_cache',
    '.venv', 'venv', 'node_modules', 'dist', 'Output', 'outputs', 'Novel_data', 'Test_data',
}
_PUBLIC_ENV_FILES = {'.env.example', 'web/.env'}
_EXCLUDED_NAMES = {'settings.local.json'}
_PRIVATE_REGENERABLE = {'.llm_cache', 'node_modules', 'dist', '__pycache__'}


class BackupRefused(ValueError):
    """备份边界、归档形状或恢复目标不安全。"""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_rel(value: str) -> str:
    rel = PurePosixPath(value.replace('\\', '/'))
    if rel.is_absolute() or not rel.parts or '..' in rel.parts:
        raise BackupRefused(f'归档路径越界：{value!r}')
    return rel.as_posix()


def _code_allowed(rel: str) -> bool:
    path = PurePosixPath(rel)
    if path.parts[0] in _CODE_EXCLUDED_TOP:
        return False
    if any(part in _CODE_EXCLUDED_TOP for part in path.parts[1:]):
        return False
    if path.name in _EXCLUDED_NAMES:
        return False
    if (path.name == '.env' or path.name.startswith('.env.')) \
            and path.as_posix() not in _PUBLIC_ENV_FILES:
        return False
    if path.name.endswith(('.pyc', '.pyo')) or '__pycache__' in path.parts:
        return False
    return not any(part.startswith('.test_tmp_') for part in path.parts)


def code_files(repo: Path) -> list[tuple[str, Path]]:
    """Git 的 tracked + unignored untracked 是代码候选；敏感/数据面再做硬排除。"""
    command = [
        'git', '-c', f'safe.directory={repo.resolve().as_posix()}',
        'ls-files', '--cached', '--others', '--exclude-standard', '-z',
    ]
    result = subprocess.run(command, cwd=repo, check=True, capture_output=True)
    rels = [item.decode('utf-8') for item in result.stdout.split(b'\0') if item]
    out = []
    for rel in sorted(set(rels)):
        safe = _safe_rel(rel)
        path = repo / Path(*PurePosixPath(safe).parts)
        if not _code_allowed(safe):
            continue
        if path.is_symlink():
            raise BackupRefused(f'代码快照拒绝跟随符号链接：{safe}')
        if path.is_file():
            resolved = path.resolve()
            root = repo.resolve()
            if resolved != root and root not in resolved.parents:
                raise BackupRefused(f'代码快照源路径越界：{safe}')
            out.append((safe, path))
    return out


def private_files(source: Path) -> list[tuple[str, Path]]:
    """私密数据快照：保留语料、付费产物、账本与轮次；仅排除密钥和可再生缓存/构建物。"""
    out = []
    root = source.resolve()
    for path in sorted(source.rglob('*'), key=lambda p: p.as_posix()):
        if path.is_symlink():
            raise BackupRefused(
                f'私密快照拒绝跟随符号链接：{path.relative_to(source)}')
        if not path.is_file():
            continue
        resolved = path.resolve()
        if resolved != root and root not in resolved.parents:
            raise BackupRefused(
                f'私密快照源路径越界：{path.relative_to(source)}')
        rel = _safe_rel(path.relative_to(source).as_posix())
        parts = PurePosixPath(rel).parts
        if (path.name in _EXCLUDED_NAMES
                or path.name == '.env' or path.name.startswith('.env.')
                or any(part in _PRIVATE_REGENERABLE for part in parts)):
            continue
        if path.suffix.lower() in {'.pyc', '.pyo'}:
            continue
        out.append((rel, path))
    return out


def _manifest(profile: str, files: Iterable[tuple[str, Path]]) -> tuple[dict, list[tuple[str, bytes]]]:
    payloads: list[tuple[str, bytes]] = []
    rows = []
    for rel, path in files:
        data = path.read_bytes()
        payloads.append((rel, data))
        rows.append({'path': rel, 'bytes': len(data), 'sha256': _sha256(data)})
    return ({'format_version': FORMAT_VERSION, 'profile': profile, 'files': rows}, payloads)


def create_archive(profile: str, files: Iterable[tuple[str, Path]], archive: Path) -> dict:
    manifest, payloads = _manifest(profile, files)
    if not payloads:
        raise BackupRefused('备份集合为空；拒绝生成一份看似成功的空归档')
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        raise BackupRefused(f'归档已存在，拒绝覆盖：{archive}')
    manifest_bytes = (json.dumps(
        manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')
    with open(archive, 'xb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode='w', format=tarfile.USTAR_FORMAT) as tar:
                for rel, data in [(MANIFEST_NAME, manifest_bytes), *payloads]:
                    info = tarfile.TarInfo(_safe_rel(rel))
                    info.size = len(data)
                    info.mode = 0o644
                    info.mtime = info.uid = info.gid = 0
                    info.uname = info.gname = ''
                    tar.addfile(info, io.BytesIO(data))
    return manifest


def _read_archive(archive: Path) -> tuple[dict, dict[str, bytes]]:
    payloads: dict[str, bytes] = {}
    with tarfile.open(archive, mode='r:gz') as tar:
        for member in tar:
            rel = _safe_rel(member.name)
            if not member.isfile() or rel in payloads:
                raise BackupRefused(f'归档含非普通文件或重复路径：{rel}')
            source = tar.extractfile(member)
            if source is None:
                raise BackupRefused(f'归档成员读不出：{rel}')
            payloads[rel] = source.read()
    raw_manifest = payloads.pop(MANIFEST_NAME, None)
    if raw_manifest is None:
        raise BackupRefused(f'归档缺 {MANIFEST_NAME}')
    manifest = json.loads(raw_manifest.decode('utf-8'))
    if manifest.get('format_version') != FORMAT_VERSION:
        raise BackupRefused(f'不支持的备份格式版本：{manifest.get("format_version")!r}')
    expected = {row['path']: row for row in manifest.get('files', [])}
    if set(expected) != set(payloads):
        raise BackupRefused('manifest 与归档文件集合不一致')
    for rel, data in payloads.items():
        row = expected[rel]
        if row['bytes'] != len(data) or row['sha256'] != _sha256(data):
            raise BackupRefused(f'归档校验失败：{rel}')
    return manifest, payloads


def restore_archive(archive: Path, destination: Path) -> dict:
    manifest, payloads = _read_archive(archive)
    if destination.exists() and any(destination.iterdir()):
        raise BackupRefused(f'恢复目标不是空目录，拒绝覆盖：{destination}')
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    for rel, data in payloads.items():
        target = destination / Path(*PurePosixPath(rel).parts)
        resolved = target.resolve()
        if resolved != root and root not in resolved.parents:
            raise BackupRefused(f'恢复路径越界：{rel}')
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, 'xb') as output:
            output.write(data)
    # 恢复后再从盘上复核，不拿内存里的待写字节冒充恢复成功。
    for row in manifest['files']:
        target = destination / Path(*PurePosixPath(row['path']).parts)
        data = target.read_bytes()
        if len(data) != row['bytes'] or _sha256(data) != row['sha256']:
            raise BackupRefused(f'恢复后校验失败：{row["path"]}')
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    code = sub.add_parser('create-code')
    code.add_argument('--repo', type=Path, required=True)
    code.add_argument('--archive', type=Path, required=True)
    private = sub.add_parser('create-private')
    private.add_argument('--source', type=Path, required=True)
    private.add_argument('--archive', type=Path, required=True)
    private.add_argument('--acknowledge-encrypted-offline-target', action='store_true')
    restore = sub.add_parser('restore')
    restore.add_argument('--archive', type=Path, required=True)
    restore.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()

    if args.command == 'create-code':
        manifest = create_archive('code', code_files(args.repo.resolve()), args.archive)
    elif args.command == 'create-private':
        if not args.acknowledge_encrypted_offline_target:
            raise BackupRefused(
                'private 快照含版权语料/付费输出；只有目标位于受控加密离线介质时才可确认')
        manifest = create_archive(
            'private-data', private_files(args.source.resolve()), args.archive)
    else:
        manifest = restore_archive(args.archive, args.destination)
    print(json.dumps({
        'profile': manifest['profile'],
        'files': len(manifest['files']),
        'bytes': sum(row['bytes'] for row in manifest['files']),
    }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
