#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Commander 所消费的 canonical operator skill 字节契约。

Skill 的语义不只在 ``SKILL.md``：主循环会按需读 references，稳定
机械操作在 scripts，终审形状在 assets。只哈希入口文件会让这三类
资源在一本书运行中漂移，却仍声称是同一份冻结操作契约。

本模块只读 neutral ``.workflow`` canonical tree；``.agents`` / ``.claude``
两份逐字相同的发现 shim 不承载业务指令，故不进 hash。Commander prompt
直接点名 neutral 入口，不依赖运行中可能被宿主重新物化的 shim。canonical
文件名和原始字节都进契约，mtime、创建顺序和换行形式都不会被擅自归一。
"""
from __future__ import annotations

import hashlib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_REL = Path('.workflow/skills/novel-pipeline-operator')
RESOURCE_DIRS = ('references', 'scripts', 'assets')


class SkillContractError(RuntimeError):
    """Canonical skill 不完整或不再是封闭文件树。"""


def contract_files(repo_root: str | Path = REPO_ROOT) -> tuple[Path, ...]:
    """返回按相对 POSIX 路径排序的 canonical 契约成员。"""
    root = Path(repo_root).resolve() / SKILL_REL
    skill = root / 'SKILL.md'
    required = [skill, *(root / name for name in RESOURCE_DIRS)]
    missing = [path.relative_to(root).as_posix() for path in required
               if not path.exists()]
    if missing:
        raise SkillContractError(
            f'canonical operator skill 缺必需成员：{missing}')
    if not skill.is_file() or skill.is_symlink():
        raise SkillContractError('canonical SKILL.md 必须是普通文件')

    members = [skill]
    resource_member_counts = {name: 0 for name in RESOURCE_DIRS}
    for dirname in RESOURCE_DIRS:
        directory = root / dirname
        if not directory.is_dir() or directory.is_symlink():
            raise SkillContractError(
                f'canonical {dirname}/ 必须是普通目录')
        for path in directory.rglob('*'):
            relative = path.relative_to(root)
            if '__pycache__' in relative.parts or path.suffix in ('.pyc', '.pyo'):
                continue
            if path.is_symlink():
                raise SkillContractError(
                    f'canonical skill 不允许外指链接：{relative.as_posix()}')
            if path.is_file():
                members.append(path)
                resource_member_counts[dirname] += 1
    if any(count == 0 for count in resource_member_counts.values()):
        raise SkillContractError('canonical references/scripts/assets 均不得为空')
    return tuple(sorted(
        members, key=lambda path: path.relative_to(root).as_posix()))


def package_sha256(repo_root: str | Path = REPO_ROOT) -> str:
    """哈希 path + byte length + raw bytes；framing 无歧义。"""
    root = Path(repo_root).resolve() / SKILL_REL
    digest = hashlib.sha256()
    for path in contract_files(repo_root):
        relative = path.relative_to(root).as_posix().encode('utf-8')
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise SkillContractError(
                f'读不了 canonical skill 成员 {relative.decode("utf-8")!r}') from exc
        digest.update(relative)
        digest.update(b'\0')
        digest.update(str(len(raw)).encode('ascii'))
        digest.update(b'\0')
        digest.update(raw)
    return digest.hexdigest()


__all__ = [
    'RESOURCE_DIRS', 'REPO_ROOT', 'SKILL_REL', 'SkillContractError',
    'contract_files', 'package_sha256',
]
