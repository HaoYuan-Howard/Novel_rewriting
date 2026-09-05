#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''HTTP 入口使用的非产物文件：原始上传与一次性 stage0 编辑计划。

两类文件都住在 Output/_service，不进来源账，也不冒充小说产物。上传原文要长期
保留，因为 stage0 审校屏必须能按 `source_ref` 重新取回被剔除的文本；普通编辑计划
在作业结束即删。候选裁决是例外：按内容寻址，失败时保留供同一决定重放，只有
正式动作已落地（ok / landed_red）后才删。
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

from contracts.schema.stage0 import Stage0CandidatePlan, Stage0EditPlan
from contracts.schema.evaluation import GlobalReviewDecisionSet
from contracts.schema.orchestration import (
    ReviewerActionRecord, Stage2PrereviewRecord,
)
from contracts.schema import encode_artifact
from contracts.artifacts import (
    STAGE2_BLUEPRINT_RELATION_EDIT,
    STAGE2_GLOBAL_EDIT,
    STAGE2_GLOBAL_REVIEW_DECISIONS,
)
from contracts.schema.api import BatchTrimResolutionRequest
from contracts.schema.stage2 import BlueprintRelationEdit, GlobalEdit
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


def save_blueprint_relation_edit(
    base: str | os.PathLike[str],
    sid: str,
    request: BlueprintRelationEdit,
) -> Path:
    """把 Stage2 v7 typed relation request 落到 SID 专属的一次性目录。

    HTTP 进程只写 ``_service/edits``；正式 Blueprint 仍只能由
    ``stage2.blueprint --apply-edit`` 子进程经 schema/HARD/归档口写回。
    """
    try:
        paths.check_sid(sid)
    except PathRefused as exc:
        raise SourcePathRejected(f'关系编辑的书 id 不合法：{exc}') from exc
    root = service_dir(base) / EDITS_DIRNAME / sid
    root.mkdir(parents=True, exist_ok=True)
    target = root / f'blueprint-relations-{uuid.uuid4().hex}.json'
    tmp = target.with_suffix('.tmp')
    payload = encode_artifact(
        STAGE2_BLUEPRINT_RELATION_EDIT,
        request.model_dump(mode='json', by_alias=True),
    )
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + NL,
            encoding='utf-8')
        os.replace(tmp, target)
    except OSError as exc:
        raise ArtifactUnreadable(f'保存 Stage2 关系编辑请求失败：{exc!r}') from exc
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
    """Persist a one-shot Stage5 batch trim manifest outside the artifact tree."""
    try:
        paths.check_sid(sid)
    except PathRefused as exc:
        raise SourcePathRejected(f'批量销账 manifest 的书 id 不合法：{exc}') from exc
    root = service_dir(base) / EDITS_DIRNAME / sid
    root.mkdir(parents=True, exist_ok=True)
    target = root / f'stage5-trim-batch-{uuid.uuid4().hex}.json'
    tmp = target.with_suffix('.tmp')
    try:
        payload = request.model_dump(mode='json', by_alias=True)
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + NL,
            encoding='utf-8')
        os.replace(tmp, target)
    except OSError as exc:
        raise ArtifactUnreadable(f'写不了批量销账 manifest：{exc!r}') from exc
    return target


def save_reviewer_action(
    base: str | os.PathLike[str], sid: str, record: ReviewerActionRecord,
) -> Path:
    """按内容身份持久化一份已通过 typed loader 的 Commander 决定。

    与 ``save_candidate_plan`` 同款：同字节幂等复用，不同字节拒绝覆盖。它是
    一次已完成模型审阅的可重放半边，必须活过 epoch 关闭与 scratch 清理，只在
    canonical approval/node action 已 landed 或得到显式 exit 2 拒因后由
    调用方删除；exit 70 结论未知时必须保留。Stage2 node 记录额外保存
    workpack、原 logical session 与 provider 原始回答 digest，其中不写回答正文。
    """
    try:
        paths.check_sid(sid)
    except PathRefused as exc:
        raise SourcePathRejected(f'reviewer 决定记录的书 id 不合法：{exc}') from exc
    if record.sid != sid:
        raise SourcePathRejected(
            f'reviewer 决定记录属于 sid={record.sid!r}，不能落到 sid={sid!r}')
    raw = (record.model_dump_json() + '\n').encode('utf-8')
    digest = hashlib.sha256(raw).hexdigest()
    root = service_dir(base) / EDITS_DIRNAME / sid
    root.mkdir(parents=True, exist_ok=True)
    target = root / f'reviewer-action-{digest}.json'
    if target.is_file():
        try:
            existing = target.read_bytes()
        except OSError as exc:
            raise ArtifactUnreadable(f'读不了已有 reviewer 决定记录：{exc!r}') from exc
        if existing != raw:
            raise SourceConflict(
                f'reviewer 决定记录内容地址 {target} 已存在不同字节；拒绝覆盖')
        return target.resolve()
    tmp = target.with_name(f'.{target.name}.{uuid.uuid4().hex}.tmp')
    try:
        tmp.write_bytes(raw)
        os.replace(tmp, target)
    except OSError as exc:
        raise ArtifactUnreadable(f'保存 reviewer 决定记录失败：{exc!r}') from exc
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return target.resolve()


def save_stage2_prereview(
    base: str | os.PathLike[str], sid: str, record: Stage2PrereviewRecord,
) -> Path:
    """内容寻址保存 typed-valid、尚未取得 live 写权的 Stage2 预审决定。"""
    try:
        paths.check_sid(sid)
    except PathRefused as exc:
        raise SourcePathRejected(f'Stage2 预审记录的书 id 不合法：{exc}') from exc
    if record.sid != sid:
        raise SourcePathRejected(
            f'Stage2 预审记录属于 sid={record.sid!r}，不能落到 sid={sid!r}')
    raw = (record.model_dump_json() + '\n').encode('utf-8')
    digest = hashlib.sha256(raw).hexdigest()
    root = service_dir(base) / EDITS_DIRNAME / sid
    root.mkdir(parents=True, exist_ok=True)
    target = root / f'stage2-prereview-{digest}.json'
    if target.is_file():
        try:
            existing = target.read_bytes()
        except OSError as exc:
            raise ArtifactUnreadable(f'读不了已有 Stage2 预审记录：{exc!r}') from exc
        if existing != raw:
            raise SourceConflict(
                f'Stage2 预审记录内容地址 {target} 已存在不同字节；拒绝覆盖')
        return target.resolve()
    tmp = target.with_name(f'.{target.name}.{uuid.uuid4().hex}.tmp')
    try:
        tmp.write_bytes(raw)
        os.replace(tmp, target)
    except OSError as exc:
        raise ArtifactUnreadable(f'保存 Stage2 预审记录失败：{exc!r}') from exc
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return target.resolve()


def load_stage2_prereviews(
    base: str | os.PathLike[str], sid: str,
) -> list[tuple[Path, Stage2PrereviewRecord]]:
    """关闭式读取尚未 live bind 的 Stage2 预审记录。

    记录本身就是一次已付费席位的恢复凭据；若文件损坏、内容地址不符或 sid
    串书，调用方不能把它当成“不存在”后再买一席。
    """
    try:
        paths.check_sid(sid)
    except PathRefused as exc:
        raise SourcePathRejected(f'Stage2 预审记录的书 id 不合法：{exc}') from exc
    root = service_dir(base) / EDITS_DIRNAME / sid
    if not root.is_dir():
        return []
    out: list[tuple[Path, Stage2PrereviewRecord]] = []
    for path in sorted(root.glob('stage2-prereview-*.json')):
        if path.is_symlink() or not path.is_file():
            raise ArtifactUnreadable(
                f'Stage2 预审记录不是普通文件：{path.name}')
        try:
            raw = path.read_bytes()
            record = Stage2PrereviewRecord.model_validate_json(raw)
        except (OSError, ValueError, ValidationError) as exc:
            raise ArtifactUnreadable(
                f'Stage2 预审记录无法解析：{path.name}：'
                f'{type(exc).__name__}') from exc
        expected_name = f'stage2-prereview-{hashlib.sha256(raw).hexdigest()}.json'
        if path.name != expected_name:
            raise ArtifactUnreadable(
                f'Stage2 预审记录内容地址不匹配：{path.name}')
        if record.sid != sid:
            raise ArtifactUnreadable(
                f'Stage2 预审记录 {path.name} 的 sid 不匹配')
        out.append((path.resolve(), record))
    return out


def load_reviewer_actions(
    base: str | os.PathLike[str], sid: str,
) -> list[tuple[Path, ReviewerActionRecord]]:
    """只读列出同 SID 的可重放 Commander 决定，按文件名稳定排序。

    坏文件、符号链接与 sid 不符的记录一律跳过并告警，不猜内容；这里不删任何
    东西——失效与否只有拿着当前 workpack 的调用方能判。
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
            continue
        try:
            record = ReviewerActionRecord.model_validate_json(path.read_bytes())
        except (OSError, ValueError, ValidationError) as exc:
            warnings.warn(
                f'跳过无法解析的 reviewer 决定记录 {path.name}：{type(exc).__name__}',
                RuntimeWarning, stacklevel=2)
            continue
        if record.sid != sid:
            warnings.warn(
                f'跳过 sid 不符的 reviewer 决定记录 {path.name}',
                RuntimeWarning, stacklevel=2)
            continue
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
