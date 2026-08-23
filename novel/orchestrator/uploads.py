#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''HTTP 入口使用的非产物文件：原始上传与一次性 stage0 编辑计划。

两类文件都住在 Output/_service，不进来源账，也不冒充小说产物。上传原文要长期
保留，因为 stage0 审校屏必须能按 `source_ref` 重新取回被剔除的文本；普通编辑计划
在作业结束即删。候选裁决是例外：按内容寻址，失败时保留供同一决定重放，只有
正式动作已落地（ok / landed_red）后才删。
'''
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

from contracts.schema.stage0 import Stage0CandidatePlan, Stage0EditPlan
from contracts.schema.evaluation import GlobalReviewDecisionSet
from contracts.schema import encode_artifact
from contracts.artifacts import STAGE2_GLOBAL_REVIEW_DECISIONS
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


def save_edit_plan(
    base: str | os.PathLike[str],
    sid: str,
    plan: Stage0EditPlan | Stage0CandidatePlan,
) -> Path:
    '''把一次编辑计划原子落进服务目录，供随后启动的 CLI 子进程读取。'''
    try:
        paths.check_sid(sid)
    except PathRefused as exc:
        raise SourcePathRejected(f'编辑计划的书 id 不合法：{exc}') from exc
    root = service_dir(base) / EDITS_DIRNAME / sid
    root.mkdir(parents=True, exist_ok=True)
    target = root / f'{uuid.uuid4().hex}.json'
    tmp = target.with_suffix('.tmp')
    raw = plan.model_dump_json(exclude_none=True).encode('utf-8')
    try:
        tmp.write_bytes(raw)
        os.replace(tmp, target)
    except OSError as exc:
        raise ArtifactUnreadable(f'保存 stage0 编辑计划失败：{exc!r}') from exc
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return target.resolve()


def save_candidate_plan(
    base: str | os.PathLike[str], sid: str, plan: Stage0CandidatePlan,
) -> Path:
    """Durably save a replayable candidate decision under its content identity.

    Unlike an ordinary edit plan, a candidate decision is the human half of a
    replayable transaction.  A failed job must leave the exact same plan
    available, and an API retry must address that same file rather than create
    an unrelated one-shot UUID.
    """
    try:
        paths.check_sid(sid)
    except PathRefused as exc:
        raise SourcePathRejected(f'候选裁决的书 id 不合法：{exc}') from exc
    raw = plan.model_dump_json(exclude_none=True).encode('utf-8')
    digest = hashlib.sha256(raw).hexdigest()
    root = service_dir(base) / EDITS_DIRNAME / sid
    root.mkdir(parents=True, exist_ok=True)
    target = root / f'candidate-{digest}.json'
    if target.is_file():
        try:
            existing = target.read_bytes()
        except OSError as exc:
            raise ArtifactUnreadable(f'读不了已有 Stage0 合并决定：{exc!r}') from exc
        if existing != raw:
            raise SourceConflict(f'合并决定内容地址 {target} 已存在不同字节；拒绝覆盖')
        return target.resolve()
    tmp = target.with_name(f'.{target.name}.{uuid.uuid4().hex}.tmp')
    try:
        tmp.write_bytes(raw)
        os.replace(tmp, target)
    except OSError as exc:
        raise ArtifactUnreadable(f'保存 Stage0 合并决定失败：{exc!r}') from exc
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return target.resolve()


def save_draft_edit(
    base: str | os.PathLike[str],
    sid: str,
    ordinal: int,
    edit: ChapterResponse,
) -> Path:
    '''把一份人工改稿原子落进服务目录，供 `stage5.write --apply-edit` 子进程读取。

    ⚠ **文件名必须带 `ch{o}`**：`write._ordinal_of_edit` 从文件名认章、认不出当场炸
    （不从正文猜——猜错会把一章的人工修改落到另一章头上，而两边都不出声）。
    与 `save_edit_plan` 同族：一次性文件，作业结束即由调用方清理。
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


def save_global_review_decisions(
    base: str | os.PathLike[str],
    sid: str,
    decisions: GlobalReviewDecisionSet,
) -> Path:
    """Persist a one-shot Stage2 decision set outside the artifact tree."""
    try:
        paths.check_sid(sid)
    except PathRefused as exc:
        raise SourcePathRejected(f'复核决策的书 id 不合法：{exc}') from exc
    root = service_dir(base) / EDITS_DIRNAME / sid
    root.mkdir(parents=True, exist_ok=True)
    target = root / f'global-review-{uuid.uuid4().hex}.json'
    tmp = target.with_suffix('.tmp')
    try:
        payload = decisions.model_dump(mode='json', by_alias=True)
        data = encode_artifact(STAGE2_GLOBAL_REVIEW_DECISIONS, payload)
        tmp.write_bytes((json.dumps(
            data, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8'))
        os.replace(tmp, target)
    except OSError as exc:
        raise ArtifactUnreadable(f'保存 stage2 复核决策失败：{exc!r}') from exc
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return target.resolve()


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
