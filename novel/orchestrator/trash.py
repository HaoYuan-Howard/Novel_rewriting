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
import threading
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

#: 本进程正在进行的删除。`sweep_incomplete` 据此绕开它们——**「还没写完」与
#: 「写到一半死了」在盘上长得一模一样**，只有还活着的那一方知道区别。
_IN_FLIGHT_LOCK = threading.Lock()
_IN_FLIGHT: set[str] = set()

#: 别的进程留下的条目要多久才算「死了」。删除是一次同盘目录移动（毫秒级），
#: 五分钟还没完成的只可能是被硬中断的。⚠ 这个宽限期不是为了保守——
#: 没有它，一次直接敲 CLI 的删除会被并发的服务端 sweep 把归档审批抢走。
STALE_AFTER_SECONDS = 300

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


def _metadata_json(sid: str, title: str, original_dirname: str, *,
                   approval_archived: bool, complete: bool) -> str:
    """回收站条目的元数据。`complete` 是硬中断恢复唯一的判据落点。"""
    return json.dumps({
        '_schema_version': 2,
        'sid': sid,
        'title': title,
        'original_dirname': original_dirname,
        'trashed_at': int(time.time()),
        'approval_archived': approval_archived,
        'complete': complete,
    }, ensure_ascii=False, indent=2) + '\n'


def sweep_incomplete(base: str | os.PathLike[str]) -> list[str]:
    """把硬中断留下的半成品回收站条目收干净，返回处理过的 trash_id。

    ⚠⚠ **半成品只有一种形状**：没有 `book/` **且** `metadata.complete` 不为真。
    此时 `execute` 死在了「审批已归档」与「书目录已移走」之间——书还在语料库里
    活着，而它的审批只存在于一个没人会去读的回收站目录。放着不管的后果不是丢数据，
    是**读数说不清**：那本书看起来「从来没批过」，而批准记录明明还在盘上。
    ⇒ 把审批搬回 live（live 位置已有新的一份时不覆盖，改名留证），再删掉空条目。

    ⚠ **有 `book/` 的条目一律不碰**，哪怕 `complete` 缺失：`complete` 是本次
    新加的字段，旧条目没有它，而它们的书目录确实已经移进来了——那是删除**成功**。
    判据取「书在不在」这个盘上事实，不取一个可能还没写过的标记。
    """
    root = paths.trash_dir(base)
    if not root.is_dir():
        return []
    with _IN_FLIGHT_LOCK:
        in_flight = set(_IN_FLIGHT)
    now = int(time.time())
    swept: list[str] = []
    for item in sorted(root.iterdir()):
        if not item.is_dir() or not _TRASH_ID.fullmatch(item.name):
            continue
        if (item / 'book').exists():
            continue
        # ⚠⚠ **「还没写完」与「写到一半死了」在盘上长得一模一样。** 一次正在进行的
        #   删除，在 `os.replace(approval)` 与 `os.replace(novel_dir)` 之间的那一瞬
        #   恰好就是半成品的形状；这时把它的归档审批搬回 live，子进程随后的回滚
        #   条件（`not approval.exists()`）就被破坏了。⇒ 本进程发起的那些直接跳过；
        #   别的进程（人手敲 CLI）留下的靠宽限期区分。
        if item.name in in_flight:
            continue
        meta_path = item / 'metadata.json'
        try:
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            meta = {}
        if meta.get('complete') is True:
            continue
        trashed_at = meta.get('trashed_at')
        if isinstance(trashed_at, int) and now - trashed_at < STALE_AFTER_SECONDS:
            continue
        archived_approval = item / 'approval.json'
        sid = str(meta.get('sid') or '')
        if archived_approval.is_file() and sid:
            live = _approval_path(base, sid)
            if live.exists():
                archived_approval.replace(item / 'approval.superseded.json')
            else:
                live.parent.mkdir(parents=True, exist_ok=True)
                os.replace(archived_approval, live)
        try:
            if meta_path.exists():
                meta_path.unlink()
            if not any(item.iterdir()):
                item.rmdir()
        except OSError:
            # 别人正好也在收同一条。审批已经归位是唯一要紧的事，空壳留着无害。
            continue
        swept.append(item.name)
    return swept


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
        # ⚠ **`complete` 先写 false，成功后才改 true。** 这两次 `os.replace` 之间
        #   若进程被硬中断（断电 / taskkill），盘上会留下「审批已归档、书还在原位」
        #   的半成品——而它对外看起来与「删成功了」一模一样。
        #   哨兵让 `sweep_incomplete` 事后分得出这两种，从而把审批搬回去。
        metadata.write_text(_metadata_json(
            sid, title, novel_dir.name,
            approval_archived=approval.is_file(), complete=False),
            encoding='utf-8')
        if approval.is_file():
            os.replace(approval, archived_approval)
            approval_moved = True
        # 最后一步才让项目从语料库消失；此前任何失败都不碰正式书目录。
        os.replace(novel_dir, archived_book)
        metadata.write_text(_metadata_json(
            sid, title, novel_dir.name,
            approval_archived=approval_moved, complete=True),
            encoding='utf-8')
    except Exception:
        if approval_moved and archived_approval.is_file() and not approval.exists():
            approval.parent.mkdir(parents=True, exist_ok=True)
            os.replace(archived_approval, approval)
            approval_moved = False
        if not archived_book.exists():
            if archived_approval.is_file():
                # 回滚没动它只有一种可能：live 位置在归档之后又被写了出来。
                # 那两份**不是同一件事**，静默留在半成品里等于让下一次 sweep
                # 拿旧账盖掉新账 ⇒ 就地改名留证，人可读、机器不会误当恢复源。
                archived_approval.replace(item / 'approval.superseded.json')
            if metadata.exists():
                metadata.unlink()
            if item.exists() and not any(item.iterdir()):
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
    # 上一次删除若被硬中断，盘上会留下半成品条目（审批已归档、书还在原位）。
    # 在开新的一次之前先收干净：否则那本书的审批一直躺在回收站里，读数说不清。
    sweep_incomplete(base)
    trash_id = new_trash_id()
    key = jobs.active_key(sid, 'book.trash')
    registry.acquire(key, 'trash-' + trash_id)
    with _IN_FLIGHT_LOCK:
        _IN_FLIGHT.add(trash_id)
    try:
        # 必须先占书级作业锁，再检查 agent。agent.start 的顺序正好相反：
        # 先占 agent 锁，再查这把作业锁。两边的 acquire 都不等待，谁先拿到，
        # 另一边都明确 409；若先查后占，agent 会在两步之间钻进来写盘。
        queued = {item.sid for item in agent.queue_view(base).pending}
        if sid in queued:
            raise jobs.JobConflict('这本书还在 agent 排队中——先取消/等队列处理完再删除')
        jobs._refuse_if_agent_running(sid)
        novel_dir = paths.resolve_novel_dir(base, sid)
        title = paths.title_of(novel_dir)
        if title != expected_title:
            raise SourceConflict(
                f'删除确认绑定的是《{expected_title}》，当前项目是《{title}》，请重新确认')
        argv = build_argv(base, sid, expected_title, trash_id)
        # ⚠⚠ **删除的原子边界是这个 `with`，不是子进程那一次 `os.replace`。**
        #   两条缝各由一半守着，缺一条都能复现实测过的坏状态：
        #     · 审批写入住在**父进程**，子进程移走 `<sid>.json` 之后它还能把同名文件
        #       重新写出来 ⇒「书已删除、审批复活」。`frozen_for_trash` 堵这条。
        #     · agent 待跑队列也住在父进程，上面那次 `queue_view` 只是开跑前的一张
        #       快照；spawn 期间新入队的这本书会在锁一还回去就被 `_drain` 启动
        #       ⇒「书删完了 agent 才起来」。`drop_pending` **在锁内**堵这条
        #       （`agent.start` 那道 `conflicting_holder` 覆盖这中间的每一刻）。
        with approvals.frozen_for_trash(sid):
            rc, output = spawn(argv, str(REPO_ROOT), child_env())
            if rc != 0:
                detail = output.decode('utf-8', errors='replace').strip()
                raise BookTrashFailed(
                    f'项目没有安全移入回收站（exit {rc}）'
                    + (f'：{detail[-1200:]}' if detail else ''))
            agent.drop_pending(base, sid)
        rel = (paths.trash_dir(base) / trash_id / 'book').relative_to(
            Path(os.fspath(base))).as_posix()
        return TrashNovelResult(
            sid=sid, title=title, trash_id=trash_id, trash_rel=rel)
    finally:
        with _IN_FLIGHT_LOCK:
            _IN_FLIGHT.discard(trash_id)
        registry.release(key, 'trash-' + trash_id)
