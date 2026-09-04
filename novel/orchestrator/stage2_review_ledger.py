#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 Global Commander 的跨进程预算与单调进展账。

这份账只落无正文 identity：Global/MainText/workpack SHA、未闭合项数量与调用结果。
模型进程一旦获得调用槽就先写 ``started``，所以宿主崩溃也不会重新发放同一格预算。
每个 Global + MainText 主体最多三次 Commander 真调用，且每次必须是新的 logical
session；相同 progress fingerprint 不允许靠重启重演。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

from contracts.schema.orchestration import (
    Stage2ReviewLedger, Stage2ReviewLedgerTurn,
)
from novel import paths
from novel.appendlog import path_transaction_lock
from novel.errors import PathRefused


MAX_COMMANDER_CALLS = 3


class Stage2ReviewBudgetRefused(RuntimeError):
    """预算耗尽、进展倒退或持久账不闭合。"""


def ledger_path(
    base: str | os.PathLike[str], sid: str,
    *, source_sha256: str, main_text_sha256: str,
) -> Path:
    checked_sid = paths.check_sid(sid)
    for label, value in (
            ('source_sha256', source_sha256),
            ('main_text_sha256', main_text_sha256)):
        if (len(value) != 64
                or any(ch not in '0123456789abcdef' for ch in value)):
            raise ValueError(f'{label} 不是 64 位小写 SHA-256')
    # Windows 的锁文件还会追加 ``.lock``；直接把两个 64 位 SHA 拼进文件名会让
    # 深书目录越过传统 MAX_PATH。文件名使用二者的内容地址，账内仍保留完整双 SHA
    # 并在读取后逐字核对，因此没有缩短业务 identity。
    identity_sha256 = hashlib.sha256(
        f'{source_sha256}:{main_text_sha256}'.encode('ascii')).hexdigest()
    target = (
        paths.service_runs_dir(base) / checked_sid / 'stage2'
        / 'global_review_ledgers'
        / f'{identity_sha256}.json'
    )
    service_root = paths.service_dir(base).absolute()
    if service_root.is_symlink():
        raise PathRefused('_service 根不能是符号链接')
    resolved_root = service_root.resolve()
    resolved = target.absolute().resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise PathRefused('Stage2 review ledger 越出 _service') from exc
    return resolved


def _atomic_write(path: Path, ledger: Stage2ReviewLedger) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(
        ledger.model_dump(mode='json'), ensure_ascii=False,
        sort_keys=True, indent=2, allow_nan=False,
    ).encode('utf-8') + b'\n'
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _read(path: Path) -> Stage2ReviewLedger | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise Stage2ReviewBudgetRefused(
            f'Stage2 review ledger 不是普通文件：{path}')
    try:
        return Stage2ReviewLedger.model_validate_json(path.read_bytes())
    except Exception as exc:  # noqa: BLE001 —— 坏账不能静默重置预算
        raise Stage2ReviewBudgetRefused(
            f'Stage2 review ledger 无法按当前 typed 契约读取：{path}') from exc


def progress_with_feedback(
    progress_sha256: str, *, reason: str, request_sha256: str,
) -> str:
    """把确定性拒因与被拒请求 identity 并入下一次纠错进展。"""
    return hashlib.sha256(json.dumps({
        'prior_progress_sha256': progress_sha256,
        'rejection_reason': reason,
        'request_sha256': request_sha256,
    }, ensure_ascii=False, sort_keys=True,
       separators=(',', ':')).encode('utf-8')).hexdigest()


def reserve(
    base: str | os.PathLike[str], sid: str, *,
    source_sha256: str,
    main_text_sha256: str,
    business_sha256: str,
    workpack_sha256: str,
    progress_sha256: str,
    blocker_count: int,
    purpose: str,
    parent_run_id: str,
    epoch_id: str,
    session_run_id: str,
    now: int | None = None,
) -> Stage2ReviewLedgerTurn:
    """原子预留一格真实调用；返回 turn 即表示预算已消费。"""
    path = ledger_path(
        base, sid, source_sha256=source_sha256,
        main_text_sha256=main_text_sha256)
    with path_transaction_lock(path):
        ledger = _read(path) or Stage2ReviewLedger(
            sid=sid, source_sha256=source_sha256,
            main_text_sha256=main_text_sha256)
        if (ledger.sid != sid or ledger.source_sha256 != source_sha256
                or ledger.main_text_sha256 != main_text_sha256):
            raise Stage2ReviewBudgetRefused('Stage2 review ledger 主体 identity 冲突')
        if ledger.state == 'complete':
            raise Stage2ReviewBudgetRefused(
                '同一 Stage2 Global/MainText 审阅账已标 complete，拒绝重新调用')
        if len(ledger.turns) >= ledger.max_calls:
            raise Stage2ReviewBudgetRefused(
                f'Stage2 Global 已用满 {ledger.max_calls} 次 Commander 调用')
        if any(row.progress_sha256 == progress_sha256 for row in ledger.turns):
            raise Stage2ReviewBudgetRefused(
                'Stage2 Global 未闭合项与拒因 fingerprint 没有变化，拒绝重复调用')
        if ledger.turns and blocker_count > ledger.turns[-1].blocker_count:
            raise Stage2ReviewBudgetRefused(
                'Stage2 Global 未闭合项数量倒退增加，拒绝在未知扩张上继续消耗模型')
        turn = Stage2ReviewLedgerTurn(
            ordinal=len(ledger.turns) + 1,
            purpose=purpose,
            parent_run_id=parent_run_id,
            epoch_id=epoch_id,
            session_run_id=session_run_id,
            business_sha256=business_sha256,
            workpack_sha256=workpack_sha256,
            progress_sha256=progress_sha256,
            blocker_count=blocker_count,
            started_at=int(time.time()) if now is None else now,
        )
        updated = ledger.model_copy(update={
            'turns': [*ledger.turns, turn],
            'state': (
                'exhausted'
                if len(ledger.turns) + 1 == ledger.max_calls else 'active'),
        })
        # model_copy 不重跑 validator；重新铸一次确保盘前闭合。
        updated = Stage2ReviewLedger.model_validate(
            updated.model_dump(mode='json'))
        _atomic_write(path, updated)
        return turn


def finish(
    base: str | os.PathLike[str], sid: str, *,
    source_sha256: str,
    main_text_sha256: str,
    session_run_id: str,
    exit_code: int,
    now: int | None = None,
) -> Stage2ReviewLedgerTurn:
    """给已预留 turn 落终态；幂等同值，冲突关闭式拒绝。"""
    path = ledger_path(
        base, sid, source_sha256=source_sha256,
        main_text_sha256=main_text_sha256)
    with path_transaction_lock(path):
        ledger = _read(path)
        if ledger is None:
            raise Stage2ReviewBudgetRefused('Stage2 review turn 没有对应 ledger')
        matches = [
            (index, row) for index, row in enumerate(ledger.turns)
            if row.session_run_id == session_run_id
        ]
        if len(matches) != 1:
            raise Stage2ReviewBudgetRefused('Stage2 review session 在 ledger 中不唯一')
        index, prior = matches[0]
        outcome = 'ok' if exit_code == 0 else 'failed'
        if prior.outcome != 'started':
            if prior.exit_code == exit_code and prior.outcome == outcome:
                return prior
            raise Stage2ReviewBudgetRefused('Stage2 review turn 已有冲突终态')
        finished = prior.model_copy(update={
            'finished_at': int(time.time()) if now is None else now,
            'exit_code': exit_code,
            'outcome': outcome,
        })
        turns = list(ledger.turns)
        turns[index] = Stage2ReviewLedgerTurn.model_validate(
            finished.model_dump(mode='json'))
        updated = Stage2ReviewLedger.model_validate({
            **ledger.model_dump(mode='json'),
            'turns': [row.model_dump(mode='json') for row in turns],
        })
        _atomic_write(path, updated)
        return turns[index]


def mark_complete(
    base: str | os.PathLike[str], sid: str, *,
    source_sha256: str, main_text_sha256: str,
) -> None:
    """当前写作面确认安全后封账；不存在不伪造一份零调用账。"""
    path = ledger_path(
        base, sid, source_sha256=source_sha256,
        main_text_sha256=main_text_sha256)
    with path_transaction_lock(path):
        ledger = _read(path)
        if ledger is None or ledger.state == 'complete':
            return
        updated = Stage2ReviewLedger.model_validate({
            **ledger.model_dump(mode='json'), 'state': 'complete',
        })
        _atomic_write(path, updated)


__all__ = [
    'MAX_COMMANDER_CALLS', 'Stage2ReviewBudgetRefused', 'finish',
    'ledger_path', 'mark_complete', 'progress_with_feedback', 'reserve',
]
