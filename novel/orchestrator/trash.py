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
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from contracts.schema.api import TrashNovelResult
from novel import paths
from novel.errors import BookTrashFailed, SourceConflict
from novel.orchestrator import agent, approvals, jobs
from novel.orchestrator.nodes import child_env

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_MODULE = 'novel.cli.trash'
_TRASH_ID = re.compile(r'[0-9]{10}-[0-9a-f]{8}')

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


def _approval_path(base: str | os.PathLike[str], sid: str) -> Path:
    return paths.service_dir(base) / approvals.APPROVALS_DIRNAME / f'{sid}.json'


def execute(base: str | os.PathLike[str], sid: str, expected_title: str,
            trash_id: str) -> TrashNovelResult:
    """CLI 子进程里的原子边界：书目录最后移动，前置失败则回滚审批文件。"""
    if not _TRASH_ID.fullmatch(trash_id):
        raise BookTrashFailed(f'非法 trash_id={trash_id!r}')
    novel_dir = paths.resolve_novel_dir(base, sid)
    title = paths.title_of(novel_dir)
    if title != expected_title:
        raise SourceConflict(
            f'删除确认绑定的是《{expected_title}》，当前 sid={sid} 实际是《{title}》。'
            '书名或项目状态已经变化，请重新打开确认框。')

    root = paths.trash_dir(base)
    item = root / trash_id
    if item.exists():
        raise BookTrashFailed(f'回收站目标已经存在：{item}')

    approval = _approval_path(base, sid)
    archived_approval = item / 'approval.json'
    archived_book = item / 'book'
    metadata = item / 'metadata.json'
    approval_moved = False
    try:
        item.mkdir(parents=True, exist_ok=False)
        metadata.write_text(json.dumps({
            '_schema_version': 1,
            'sid': sid,
            'title': title,
            'original_dirname': novel_dir.name,
            'trashed_at': int(time.time()),
            'approval_archived': approval.is_file(),
        }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        if approval.is_file():
            os.replace(approval, archived_approval)
            approval_moved = True
        # 最后一步才让项目从语料库消失；此前任何失败都不碰正式书目录。
        os.replace(novel_dir, archived_book)
    except Exception:
        if approval_moved and archived_approval.is_file() and not approval.exists():
            approval.parent.mkdir(parents=True, exist_ok=True)
            os.replace(archived_approval, approval)
        if not archived_book.exists():
            if metadata.exists():
                metadata.unlink()
            if item.exists():
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
    queued = {item.sid for item in agent.queue_view(base).pending}
    if sid in queued:
        raise jobs.JobConflict('这本书还在 agent 排队中——先取消/等队列处理完再删除')
    jobs._refuse_if_agent_running(sid)

    trash_id = new_trash_id()
    key = jobs.active_key(sid, 'book.trash')
    registry.acquire(key, 'trash-' + trash_id)
    try:
        novel_dir = paths.resolve_novel_dir(base, sid)
        title = paths.title_of(novel_dir)
        if title != expected_title:
            raise SourceConflict(
                f'删除确认绑定的是《{expected_title}》，当前项目是《{title}》，请重新确认')
        argv = build_argv(base, sid, expected_title, trash_id)
        rc, output = spawn(argv, str(REPO_ROOT), child_env())
        if rc != 0:
            detail = output.decode('utf-8', errors='replace').strip()
            raise BookTrashFailed(
                f'项目没有安全移入回收站（exit {rc}）'
                + (f'：{detail[-1200:]}' if detail else ''))
        rel = (paths.trash_dir(base) / trash_id / 'book').relative_to(
            Path(os.fspath(base))).as_posix()
        return TrashNovelResult(
            sid=sid, title=title, trash_id=trash_id, trash_rel=rel)
    finally:
        registry.release(key, 'trash-' + trash_id)
