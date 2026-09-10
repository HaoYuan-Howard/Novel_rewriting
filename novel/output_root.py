# -*- coding: utf-8 -*-
"""离线整根归档的消费边界，不读取或迁移旧 artifact。

围栏放在根外，移动整个旧根不会撤销原地址的禁入。新根只有同一事务的
committed 回执落盘后才可启用；损坏、缺失的提交证据均拒绝。旧代码回滚须
使用配对的旧代码与归档根，不由当前代码删除围栏或兼容读取。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from novel import paths
from novel.errors import OutputRootUnavailable


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def seal(value: dict) -> dict:
    return {**value, 'sha256': hashlib.sha256(canonical(value)).hexdigest()}


def read_sealed(path: Path) -> dict:
    raw = json.loads(path.read_bytes())
    if not isinstance(raw, dict):
        raise ValueError('根切换记录必须为对象')
    unsigned = {key: value for key, value in raw.items() if key != 'sha256'}
    if raw != seal(unsigned):
        raise ValueError('根切换记录摘要不匹配')
    return raw


def require_active(base: str | os.PathLike[str]) -> None:
    """CLI、服务与 ArtifactStore 共用；不为普通根创建任何文件。"""
    root = Path(base).resolve()
    marker = paths.output_root_fence_path(root)
    try:
        marker.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise OutputRootUnavailable('输出根围栏不可读，不能判定是否可运行') from exc
    try:
        if marker.is_symlink():
            raise ValueError('根围栏不接受链接')
        fence = read_sealed(marker)
        if (set(fence) != {'version', 'root', 'role', 'journal', 'plan_sha256', 'sha256'}
                or type(fence['version']) is not int or fence['version'] != 1
                or Path(fence['root']) != root
                or fence['role'] not in {'source', 'archive', 'new_root'}):
            raise ValueError('根围栏身份或字段不匹配')
        if fence['role'] != 'new_root':
            raise OutputRootUnavailable('该输出根已封存或正在归档；当前代码只接受本次切换的独立新根')
        journal = Path(fence['journal'])
        plan = read_sealed(journal / 'preview.json')
        commit = read_sealed(journal / 'committed.json')
        if (type(plan['version']) is not int or plan['version'] != 1
                or plan['sha256'] != fence['plan_sha256'] or Path(plan['new_root']) != root
                or commit != seal({'version': 1, 'state': 'committed',
                                   'plan_sha256': plan['sha256']})):
            raise ValueError('新根缺少同一事务的完整提交回执')
    except OutputRootUnavailable:
        raise
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise OutputRootUnavailable('输出根切换证据未知；先用原 preview/apply 免费补尾') from exc
