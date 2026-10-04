#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''HTTP 入口使用的非产物文件：原始上传。

这些文件都住在 Output/_service，不进来源账，也不冒充小说产物。上传原文要长期
保留，因为 stage0 审校屏必须能按 `source_ref` 重新取回被剔除的文本。
旧链的一次性人工编辑输入（draft/characters/global/blueprint 编辑与 trim 批量
销账）随 F8b 全自动单模式退役；本模块只剩上传原文一个写入口。
'''
from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

from novel import paths
from novel.errors import (
    ArtifactUnreadable,
    PathRefused,
    SourceConflict,
    SourcePathRejected,
)

MAX_UPLOAD_BYTES = 16 * 1024 * 1024


def _source_name(filename: str) -> tuple[str, str]:
    '''校验浏览器给的 basename，并提前证明它能安全、可逆地推导书目录。'''
    name = str(filename)
    if (
        not name
        or name != name.strip()
        or '/' in name
        or '\\' in name
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
        or Path(name).name != name
    ):
        raise SourcePathRejected(f'上传文件名必须是单个安全 basename，拿到 {filename!r}')
    if Path(name).suffix.lower() not in ('.txt', '.md'):
        raise SourcePathRejected('只接受 .txt / .md 小说原文')
    sid, title, _platform = paths.derive_source_meta(name)
    try:
        paths.check_sid(sid)
    except PathRefused as exc:
        raise SourcePathRejected(f'上传文件名推导出的书 id 不合法：{exc}') from exc
    if not title.strip():
        raise SourcePathRejected(f'上传文件名 {name!r} 推导不出非空书名')
    errors = paths.dirname_roundtrip_errors(title, sid)
    if errors:
        raise SourcePathRejected('；'.join(errors))
    return name, sid


def save_upload(
    base: str | os.PathLike[str],
    filename: str,
    raw: bytes,
) -> tuple[Path, str]:
    '''按内容身份持久保存原始字节；同名不同内容互不覆盖，摄入层再裁决书身份冲突。'''
    name, sid = _source_name(filename)
    if not raw:
        raise SourcePathRejected('上传文件为空')
    if len(raw) > MAX_UPLOAD_BYTES:
        raise SourcePathRejected(
            f'上传文件 {len(raw)} 字节，超过上限 {MAX_UPLOAD_BYTES} 字节')
    # ⚠ 目录名**就是内容地址**，这不只是防同名覆盖：`novel/core/source.py` 的重锚
    #   靠它自证「重新锚到的这份确实是当初那份」（Output 根搬家后，记在产物里的
    #   绝对路径会断，而这一段跟着 base 走）。换成随机 id 会让那条回落失去唯一依据。
    digest = hashlib.sha256(raw).hexdigest()
    target = paths.uploads_dir(base) / digest / name
    if target.is_file():
        try:
            existing = target.read_bytes()
        except OSError as exc:
            raise ArtifactUnreadable(f'读不了已有上传文件 {target}：{exc!r}') from exc
        if existing != raw:
            raise SourceConflict(f'上传缓存路径 {target} 已存在不同字节；拒绝覆盖')
        return target.resolve(), sid

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f'.{target.name}.{uuid.uuid4().hex}.tmp')
    try:
        tmp.write_bytes(raw)
        os.replace(tmp, target)
    except OSError as exc:
        raise ArtifactUnreadable(f'保存上传文件 {target} 失败：{exc!r}') from exc
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return target.resolve(), sid
