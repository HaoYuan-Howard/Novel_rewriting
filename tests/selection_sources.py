"""只读 Git 基线，供选测恢复删除前的依赖；不切换工作区或执行历史代码。"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from typing import Any


class BaselineUnavailable(ValueError):
    """基线不可读时由选择器显式扩大范围，不能当成没有旧消费者。"""


@dataclass(frozen=True)
class Baseline:
    revision: str
    sources: dict[str, str]
    catalog: dict[str, Any]


def read_baseline(root: Path, ref: str) -> Baseline:
    def git(*args: str, input: bytes | None = None) -> bytes:
        try:
            return subprocess.run(
                ['git', '-c', f'safe.directory={root.resolve().as_posix()}', *args],
                cwd=root, input=input, capture_output=True, check=True, timeout=60,
            ).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise BaselineUnavailable(f'无法读取 Git 基线 {ref}') from exc

    revision = git('rev-parse', '--verify', '--end-of-options', f'{ref}^{{commit}}').decode().strip()
    tree = git('ls-tree', '-rz', revision, '--', 'novel', 'contracts', 'tests', 'tools')
    entries = []
    for entry in tree.split(b'\0'):
        if not entry:
            continue
        meta, raw_path = entry.split(b'\t', 1)
        mode, kind, oid = meta.split()
        path = raw_path.decode('utf-8')
        if path.endswith('.py') or path == 'tests/suite_catalog.json':
            if kind != b'blob' or mode not in {b'100644', b'100755'}:
                raise BaselineUnavailable(f'Git 基线包含不可分析文件：{path}')
            entries.append((path, oid))
    payload = git('cat-file', '--batch', input=b''.join(oid + b'\n' for _, oid in entries))
    sources: dict[str, str] = {}
    catalog = None
    offset = 0
    try:
        for path, oid in entries:
            end = payload.index(b'\n', offset)
            actual_oid, kind, raw_size = payload[offset:end].split()
            if actual_oid != oid or kind != b'blob':
                raise ValueError('Git blob 身份不匹配')
            offset = end + 1
            size = int(raw_size)
            content = payload[offset:offset + size].decode('utf-8-sig')
            offset += size + 1
            if path == 'tests/suite_catalog.json':
                catalog = json.loads(content)
            else:
                sources[path] = content
        if not isinstance(catalog, dict) or not isinstance(catalog.get('modules'), dict):
            raise ValueError('基线缺少测试目录表')
    except (ValueError, UnicodeError) as exc:
        raise BaselineUnavailable(f'Git 基线 {ref} 的源码或目录表不可分析') from exc
    return Baseline(revision, sources, catalog)
