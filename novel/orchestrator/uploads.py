#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''HTTP 入口使用的非产物文件：原始上传与一次性人工输入。

这些文件都住在 Output/_service，不进来源账，也不冒充小说产物。上传原文要长期
保留，因为 stage0 审校屏必须能按 `source_ref` 重新取回被剔除的文本；普通编辑
请求在作业结束即删。Stage0 v3 收成单次机械 transform 后，本模块不再承载
stage0 编辑/候选计划——上传后直接由 stage0.remove 重建 transform。
'''
from __future__ import annotations

NL = chr(10)  # 换行字面量：避免在字符串里写转义

import hashlib
import json
import os
import uuid
import warnings
from pathlib import Path

from pydantic import ValidationError

from contracts.schema.orchestration import (
    ReviewerActionRecord,
)
from contracts.schema import encode_artifact
from contracts.artifacts import (
    STAGE2_CHARACTERS_EDIT,
    STAGE2_BLUEPRINT_EDIT,
    STAGE2_GLOBAL_EDIT,
    STAGE2_GLOBAL_REVIEW_DECISIONS,
)
from contracts.schema.api import BatchTrimResolutionRequest
from contracts.schema.plot_edit import PlotEdit
from contracts.schema.stage2 import GlobalEdit
from contracts.schema.characters import CharactersEdit
from contracts.schema.analyze_draft import AnalyzeOwnerDraftEdit
from contracts.schema.stage5 import ChapterResponse
from novel import paths
from novel.errors import (
    ArtifactUnreadable,
    PathRefused,
    SourceConflict,
    SourcePathRejected,
)
from novel.paths import service_dir

EDITS_DIRNAME = 'edits'
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


def save_draft_edit(
    base: str | os.PathLike[str],
    sid: str,
    ordinal: int,
    edit: ChapterResponse,
) -> Path:
    '''把一份人工改稿原子落进服务目录，供 `stage5.write --apply-edit` 子进程读取。

    ⚠ **文件名必须带 `ch{o}`**：`write._ordinal_of_edit` 从文件名认章、认不出当场炸
    （不从正文猜——猜错会把一章的人工修改落到另一章头上，而两边都不出声）。
    一次性文件，作业结束即由调用方清理。
    '''
    try:
        paths.check_sid(sid)
    except PathRefused as exc:
        raise SourcePathRejected(f'人工改稿的书 id 不合法：{exc}') from exc
    if ordinal < 1:
        raise SourcePathRejected(f'章号必须 ≥1，拿到 {ordinal}')
    root = service_dir(base) / EDITS_DIRNAME / sid
    root.mkdir(parents=True, exist_ok=True)
    target = root / f'ch{ordinal:02d}-{uuid.uuid4().hex}.json'
    tmp = target.with_suffix('.tmp')
    try:
        tmp.write_bytes(edit.model_dump_json().encode('utf-8'))
        os.replace(tmp, target)
    except OSError as exc:
        raise ArtifactUnreadable(f'保存人工改稿失败：{exc!r}') from exc
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return target.resolve()


def save_characters_edit(base: str | os.PathLike[str], sid: str, edit: CharactersEdit) -> Path:
    """HTTP 只保存同 SID 的一次性人物输入；正式写回属于 CLI producer。"""
    paths.check_sid(sid)
    root = service_dir(base) / EDITS_DIRNAME / sid
    root.mkdir(parents=True, exist_ok=True)
    target = root / f'characters-edit-{uuid.uuid4().hex}.json'
    tmp = target.with_suffix('.tmp')
    payload = encode_artifact(STAGE2_CHARACTERS_EDIT, edit.model_dump(mode='json'))
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + NL, encoding='utf-8')
        os.replace(tmp, target)
    except OSError as exc:
        raise ArtifactUnreadable(f'保存人物编辑请求失败：{exc!r}') from exc
    finally:
        tmp.unlink(missing_ok=True)
    return target.resolve()


def save_analyze_draft_edit(base: str | os.PathLike[str], sid: str,
                            edit: AnalyzeOwnerDraftEdit) -> Path:
    """浏览器候选只暂存在本书服务目录，版本与正式草稿修复入口一致。"""
    from contracts.artifacts import STAGE2_ANALYZE_OWNER_DRAFT_EDIT
    paths.check_sid(sid)
    root = service_dir(base) / EDITS_DIRNAME / sid
    root.mkdir(parents=True, exist_ok=True)
    target = root / f'analyze-draft-edit-{uuid.uuid4().hex}.json'
    tmp = target.with_suffix('.tmp')
    payload = encode_artifact(STAGE2_ANALYZE_OWNER_DRAFT_EDIT, edit.model_dump(mode='json'))
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + NL, encoding='utf-8')
        os.replace(tmp, target)
    except OSError as exc:
        raise ArtifactUnreadable(f'保存联合草稿订正请求失败：{exc!r}') from exc
    finally:
        tmp.unlink(missing_ok=True)
    return target.resolve()


def save_global_edit(
    base: str | os.PathLike[str],
    sid: str,
    edit: GlobalEdit,
) -> Path:
    """把 Commander 的一次性 Stage2 typed 修订原子落进 SID 专属服务根。"""
    try:
        paths.check_sid(sid)
    except PathRefused as exc:
        raise SourcePathRejected(f'全局修订的书 id 不合法：{exc}') from exc
    root = service_dir(base) / EDITS_DIRNAME / sid
    root.mkdir(parents=True, exist_ok=True)
    target = root / f'global-edit-{uuid.uuid4().hex}.json'
    tmp = target.with_suffix('.tmp')
    payload = encode_artifact(
        STAGE2_GLOBAL_EDIT,
        # GlobalEdit 的「字段是否出现」就是 patch 语义；补出的显式 null 会被
        # nested patch 校验器正确拒绝，不能在运输层把 absent 改写成 null。
        edit.model_dump(
            mode='json', by_alias=True, exclude_unset=True),
    )
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + NL,
            encoding='utf-8')
        os.replace(tmp, target)
    except OSError as exc:
        raise ArtifactUnreadable(f'保存 Stage2 全局修订失败：{exc!r}') from exc
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return target.resolve()


def save_blueprint_edit(
    base: str | os.PathLike[str],
    sid: str,
    request: PlotEdit,
) -> Path:
    """把 Stage2 完整剧情编辑请求 落到 SID 专属的一次性目录。

    HTTP 进程只写 ``_service/edits``；正式 Blueprint 仍只能由
    ``stage2.blueprint --apply-edit`` 子进程经 schema/HARD/归档口写回。
    """
    try:
        paths.check_sid(sid)
    except PathRefused as exc:
        raise SourcePathRejected(f'蓝图编辑的书 id 不合法：{exc}') from exc
    root = service_dir(base) / EDITS_DIRNAME / sid
    root.mkdir(parents=True, exist_ok=True)
    target = root / f'blueprint-edit-{uuid.uuid4().hex}.json'
    tmp = target.with_suffix('.tmp')
    payload = encode_artifact(
        STAGE2_BLUEPRINT_EDIT,
        request.model_dump(mode='json', by_alias=True),
    )
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + NL,
            encoding='utf-8')
        os.replace(tmp, target)
    except OSError as exc:
        raise ArtifactUnreadable(f'保存 Stage2 蓝图编辑请求失败：{exc!r}') from exc
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return target.resolve()


def save_stage5_trim_batch(
    base: str | os.PathLike[str],
    sid: str,
    request: 'BatchTrimResolutionRequest',
) -> Path:
    """已随批次 B3 trim 三选一退役；保留形状仅为历史导入兼容，不得新调用。"""
    raise NotImplementedError(
        'stage5 trim 批量销账人工提交面已退役（2026-09-15 全自动裁决）：'
        '超限章由 stage5.review producer 机械处置')


def load_reviewer_actions(
    base: str | os.PathLike[str], sid: str,
) -> list[tuple[Path, ReviewerActionRecord]]:
    """关闭式列出同 SID 的可重放 Commander 决定，按文件名稳定排序。

    记录是一次已付费席位的唯一恢复凭据；坏文件、符号链接、sid 串书或内容地址
    不匹配都不能装成“没有记录”后再买一席。业务是否过期仍由拿着 current
    workpack 的调用方判，这里只验证持久载体完整性。
    """
    try:
        paths.check_sid(sid)
    except PathRefused as exc:
        raise SourcePathRejected(f'reviewer 决定记录的书 id 不合法：{exc}') from exc
    root = service_dir(base) / EDITS_DIRNAME / sid
    if not root.is_dir():
        return []
    out: list[tuple[Path, ReviewerActionRecord]] = []
    for path in sorted(root.glob('reviewer-action-*.json')):
        if path.is_symlink() or not path.is_file():
            raise ArtifactUnreadable(
                f'reviewer 决定记录不是普通文件：{path.name}')
        try:
            raw = path.read_bytes()
            record = ReviewerActionRecord.model_validate_json(raw)
        except (OSError, ValueError, ValidationError) as exc:
            raise ArtifactUnreadable(
                f'reviewer 决定记录无法解析：{path.name}：'
                f'{type(exc).__name__}') from exc
        expected_name = f'reviewer-action-{hashlib.sha256(raw).hexdigest()}.json'
        if path.name != expected_name:
            raise ArtifactUnreadable(
                f'reviewer 决定记录内容地址不匹配：{path.name}')
        if record.sid != sid:
            raise ArtifactUnreadable(
                f'reviewer 决定记录 {path.name} 的 sid 不匹配')
        out.append((path.resolve(), record))
    return out


def discard_edit_plan(base: str | os.PathLike[str], plan_path: str | os.PathLike[str]) -> None:
    '''只删除 _service/edits 内的一次性文件（编辑计划 / 人工改稿）；路径逃逸会显式拒绝。'''
    root = (service_dir(base) / EDITS_DIRNAME).resolve()
    candidate = Path(plan_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PathRefused(f'拒绝删除 edits 目录之外的路径：{candidate}') from exc
    try:
        candidate.unlink(missing_ok=True)
    except OSError as exc:
        raise ArtifactUnreadable(f'删除一次性编辑计划失败：{exc!r}') from exc
