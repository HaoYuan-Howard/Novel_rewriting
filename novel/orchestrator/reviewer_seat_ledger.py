#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""非 Stage2 reviewer blocker 的跨进程席位账。

Stage2 的席位账按 Global/MainText 主体记四席；其它 reviewer blocker（Stage0/4/5/6/7
审批与 Stage4 A/B 裁决）此前只有一个进程内的纠错计数：重启、pause/resume 或换
epoch 都会把它清零。这里把同一 ``(sid, blocker, business_sha256)`` 上的每一轮
Commander turn（首次完整审阅 + 纠错）与同轮内的进程 attempts 持久记账：

- 一份账对应一个 reviewer **席位**：同一 binding 内的首次审阅与纠错共用同一
  logical/provider session（纠错是紧凑提示，依赖该席已读过的工作包）；
- 纠错次数上限 ``COMMANDER_REVIEWER_MAX_CORRECTIONS`` 跨重启持久，不返还；
- 账在 Popen 后、stdin 发出前写 ``started``，与 Stage2 席位账同一纪律。

surface（如返修落盘后的新候选）变化会改变 business_sha256 → 新账、新席，因此
“请求返修的那个会话自己复审”在账本层不可表达。
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from contracts.invariants import LEGACY_COMMANDER_REVIEWER_MAX_CORRECTIONS
from contracts.schema.orchestration import (
    ReviewerSchemaRecoveryProof, ReviewerSeatLedger, ReviewerSeatPendingCorrection, ReviewerSeatTurn,
    Stage2ReviewAttempt,
)
from novel import paths, run_identity
from novel.appendlog import path_transaction_lock
from novel.errors import PathRefused


MAX_CORRECTIONS = LEGACY_COMMANDER_REVIEWER_MAX_CORRECTIONS
_RAW_LEDGER = TypeAdapter(dict[str, Any])


class ReviewerSeatRefused(RuntimeError):
    """纠错席位耗尽、账不闭合或身份冲突。"""


def _blocker_segment(waiting_on: str) -> str:
    text = str(waiting_on).strip()
    if not text or len(text) > 96 or any(
            ch in text for ch in '/\\') or text in ('.', '..'):
        raise PathRefused(f'reviewer blocker 不是合法目录段：{waiting_on!r}')
    return text.replace(':', '__')


def ledger_path(
    base: str | os.PathLike[str], sid: str, *,
    waiting_on: str, business_sha256: str,
) -> Path:
    checked_sid = paths.check_sid(sid)
    if (len(business_sha256) != 64
            or any(ch not in '0123456789abcdef' for ch in business_sha256)):
        raise ValueError('business_sha256 不是 64 位小写 SHA-256')
    target = (
        paths.service_runs_dir(base) / checked_sid / 'reviewer_seats'
        / _blocker_segment(waiting_on) / f'{business_sha256}.json')
    service_root = paths.service_dir(base).absolute()
    if service_root.is_symlink():
        raise PathRefused('_service 根不能是符号链接')
    resolved = target.absolute().resolve()
    try:
        resolved.relative_to(service_root.resolve())
    except ValueError as exc:
        raise PathRefused('reviewer seat ledger 越出 _service') from exc
    return resolved


def _atomic_write(path: Path, ledger: ReviewerSeatLedger) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (ledger.model_dump_json(indent=2) + '\n').encode('utf-8')
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


def _read(path: Path) -> ReviewerSeatLedger | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ReviewerSeatRefused(f'reviewer seat ledger 不是普通文件：{path}')
    try:
        raw = _RAW_LEDGER.validate_json(path.read_bytes())
        return ReviewerSeatLedger.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 —— 坏账不能静默重置席位
        raise ReviewerSeatRefused(
            f'reviewer seat ledger 无法按当前 typed 契约读取：{path}') from exc


def _locate(
    ledger: ReviewerSeatLedger, turn_id: str,
) -> tuple[int, ReviewerSeatTurn]:
    matches = [
        (index, row) for index, row in enumerate(ledger.turns)
        if row.turn_id == turn_id]
    if len(matches) != 1:
        raise ReviewerSeatRefused('reviewer seat turn 在账中不唯一')
    return matches[0]


def _replace(
    path: Path, ledger: ReviewerSeatLedger, index: int, turn: ReviewerSeatTurn,
) -> ReviewerSeatTurn:
    turns = [row.model_dump(mode='json') for row in ledger.turns]
    turns[index] = turn.model_dump(mode='json')
    updated = ReviewerSeatLedger.model_validate({
        **ledger.model_dump(mode='json'), 'turns': turns})
    _atomic_write(path, updated)
    return updated.turns[index]


def corrections_used(ledger: ReviewerSeatLedger | None) -> int:
    if ledger is None:
        return 0
    return max(0, len(ledger.turns) - 1)


def mark_pending_correction(
    base: str | os.PathLike[str], sid: str, *,
    waiting_on: str, business_sha256: str,
    session_run_id: str, provider_session_id: str,
    request_sha256: str, reason: str, now: int | None = None,
) -> ReviewerSeatPendingCorrection:
    """把 loader/canonical 的精确拒因绑到刚完成的本席 turn。"""
    path = ledger_path(
        base, sid, waiting_on=waiting_on, business_sha256=business_sha256)
    with path_transaction_lock(path):
        ledger = _read(path)
        if ledger is None or not ledger.turns:
            raise ReviewerSeatRefused('pending correction 没有对应 reviewer 席位')
        source = ledger.turns[-1]
        if source.outcome != 'ok':
            raise ReviewerSeatRefused(
                '只有成功交付 provider 回答的 reviewer turn 才能登记纠错')
        checked_session = run_identity.check_run_id(session_run_id)
        if source.session_run_id != checked_session:
            raise ReviewerSeatRefused('pending correction 的 logical session 漂移')
        pending = ReviewerSeatPendingCorrection(
            source_turn_id=source.turn_id,
            session_run_id=checked_session,
            provider_session_id=provider_session_id,
            request_sha256=request_sha256,
            reason=reason,
            recorded_at=int(time.time()) if now is None else now,
        )
        updated = ReviewerSeatLedger.model_validate({
            **ledger.model_dump(mode='json'),
            'pending_correction': pending.model_dump(mode='json'),
        })
        _atomic_write(path, updated)
        assert updated.pending_correction is not None
        return updated.pending_correction


def clear_pending_correction(
    base: str | os.PathLike[str], sid: str, *,
    waiting_on: str, business_sha256: str,
    expected_request_sha256: str | None = None,
) -> ReviewerSeatLedger:
    """typed 动作已验证/重放后 CAS 清掉旧拒因；无 marker 时幂等。"""
    path = ledger_path(
        base, sid, waiting_on=waiting_on, business_sha256=business_sha256)
    with path_transaction_lock(path):
        ledger = _read(path)
        if ledger is None:
            raise ReviewerSeatRefused('pending correction 没有对应 reviewer 席位')
        pending = ledger.pending_correction
        if pending is None:
            return ledger
        if (expected_request_sha256 is not None
                and pending.request_sha256 != expected_request_sha256):
            raise ReviewerSeatRefused('pending correction identity 已漂移，拒绝清除')
        updated = ReviewerSeatLedger.model_validate({
            **ledger.model_dump(mode='json'), 'pending_correction': None})
        _atomic_write(path, updated)
        return updated


def reserve(
    base: str | os.PathLike[str], sid: str, *,
    waiting_on: str,
    business_sha256: str,
    purpose: str,
    session_run_id: str,
    turn_id: str,
    parent_run_id: str,
    epoch_id: str,
    schema_recovery: ReviewerSchemaRecoveryProof | None = None,
    now: int | None = None,
) -> ReviewerSeatTurn:
    """原子预留一轮 reviewer turn；纠错超过上限或账不闭合时关闭式拒绝。"""
    path = ledger_path(
        base, sid, waiting_on=waiting_on, business_sha256=business_sha256)
    checked_session = run_identity.check_run_id(session_run_id)
    with path_transaction_lock(path):
        ledger = _read(path) or ReviewerSeatLedger(
            sid=sid, waiting_on=waiting_on, business_sha256=business_sha256)
        if (ledger.sid != sid or ledger.waiting_on != waiting_on
                or ledger.business_sha256 != business_sha256):
            raise ReviewerSeatRefused('reviewer seat ledger 身份冲突')
        if any(row.turn_id == turn_id for row in ledger.turns):
            raise ReviewerSeatRefused('reviewer seat turn_id 重复')
        if purpose not in ('review', 'correction', 'schema_recovery'):
            raise ReviewerSeatRefused(
                f'未登记的 reviewer seat purpose：{purpose!r}')
        if any(row.outcome == 'started' for row in ledger.turns):
            raise ReviewerSeatRefused('reviewer seat 尚有未闭合 turn，拒绝再开一轮')
        if (purpose == 'schema_recovery') != (schema_recovery is not None):
            raise ReviewerSeatRefused('schema recovery 必须携带独立已验来源 proof')
        if purpose in ('correction', 'schema_recovery'):
            used = corrections_used(ledger)
            if used >= ledger.max_corrections:
                raise ReviewerSeatRefused(
                    f'{waiting_on} 在同一业务主体上的纠错席位已用尽'
                    f'（{used}/{ledger.max_corrections}），重启不返还')
            if not ledger.turns:
                raise ReviewerSeatRefused('没有首次审阅席位就不能预留纠错')
            if purpose == 'schema_recovery':
                from novel.orchestrator.reviewer_schema_recovery import canonical_sha256

                if (ledger.pending_correction is not None
                        or schema_recovery is None
                        or canonical_sha256(ledger.model_dump(mode='json'))
                        != schema_recovery.source_ledger_sha256):
                    raise ReviewerSeatRefused('schema recovery 来源账已漂移或存在真实待纠错回答')
            else:
                pending = ledger.pending_correction
                if pending is None:
                    raise ReviewerSeatRefused(
                        '没有持久化的 typed 拒因就不能预留纠错')
                if pending.session_run_id != checked_session:
                    raise ReviewerSeatRefused(
                        '纠错必须续用最后成功审阅轮的 logical session')
        elif purpose == 'review':
            if ledger.turns:
                raise ReviewerSeatRefused(
                    f'{waiting_on} 在同一业务主体上的首次审阅已经发生；'
                    '重启或换 epoch 不返还 review 席位')
        turn = ReviewerSeatTurn(
            turn_id=turn_id,
            ordinal=len(ledger.turns) + 1,
            purpose=purpose,  # type: ignore[arg-type]
            session_run_id=checked_session,
            parent_run_id=run_identity.check_run_id(parent_run_id),
            epoch_id=epoch_id,
            started_at=int(time.time()) if now is None else now,
            schema_recovery=schema_recovery,
        )
        updated = ReviewerSeatLedger.model_validate({
            **ledger.model_dump(mode='json'),
            'turns': [
                *[row.model_dump(mode='json') for row in ledger.turns],
                turn.model_dump(mode='json')],
        })
        _atomic_write(path, updated)
        return turn


def finish(
    base: str | os.PathLike[str], sid: str, *,
    waiting_on: str, business_sha256: str, turn_id: str, exit_code: int,
    now: int | None = None,
) -> ReviewerSeatTurn:
    """给已预留 turn 落终态；幂等同值，冲突关闭式拒绝。"""
    path = ledger_path(
        base, sid, waiting_on=waiting_on, business_sha256=business_sha256)
    with path_transaction_lock(path):
        ledger = _read(path)
        if ledger is None:
            raise ReviewerSeatRefused('reviewer seat turn 没有对应 ledger')
        index, prior = _locate(ledger, turn_id)
        outcome = 'ok' if exit_code == 0 else 'failed'
        if prior.outcome != 'started':
            if prior.exit_code == exit_code and prior.outcome == outcome:
                return prior
            raise ReviewerSeatRefused('reviewer seat turn 已有冲突终态')
        finished = ReviewerSeatTurn.model_validate({
            **prior.model_dump(mode='json'),
            'finished_at': int(time.time()) if now is None else now,
            'exit_code': exit_code, 'outcome': outcome,
        })
        return _replace(path, ledger, index, finished)


def bind_response(
    base: str | os.PathLike[str], sid: str, *,
    waiting_on: str, business_sha256: str, turn_id: str,
    provider_response_sha256: str,
) -> ReviewerSeatTurn:
    """在 action record 或 typed 拒因前把 provider 回答绑定到原 turn。

    这是 durable-first 的第一笔原子写：同 digest 幂等，异 digest 或已闭席后
    才尝试首次绑定一律拒绝。这样 record 不能只靠自身字段伪造来源回答。
    """
    if (
        len(provider_response_sha256) != 64
        or any(ch not in '0123456789abcdef'
               for ch in provider_response_sha256)
    ):
        raise ReviewerSeatRefused(
            'reviewer provider_response_sha256 不是 64 位小写 SHA-256')
    path = ledger_path(
        base, sid, waiting_on=waiting_on, business_sha256=business_sha256)
    with path_transaction_lock(path):
        ledger = _read(path)
        if ledger is None:
            raise ReviewerSeatRefused('reviewer response 没有对应 seat ledger')
        index, prior = _locate(ledger, turn_id)
        if prior.provider_response_sha256 is not None:
            if prior.provider_response_sha256 == provider_response_sha256:
                return prior
            raise ReviewerSeatRefused('reviewer seat turn 已绑定不同 provider 回答')
        if prior.outcome != 'started':
            raise ReviewerSeatRefused(
                'reviewer seat turn 已闭账，不能首次补绑 provider 回答')
        bound = ReviewerSeatTurn.model_validate({
            **prior.model_dump(mode='json'),
            'provider_response_sha256': provider_response_sha256,
        })
        return _replace(path, ledger, index, bound)


def record_attempt(
    base: str | os.PathLike[str], sid: str, *,
    waiting_on: str, business_sha256: str, turn_id: str,
    attempt_id: str, kind: str, provider_session_id: str | None = None,
    now: int | None = None,
) -> ReviewerSeatTurn:
    path = ledger_path(
        base, sid, waiting_on=waiting_on, business_sha256=business_sha256)
    with path_transaction_lock(path):
        ledger = _read(path)
        if ledger is None:
            raise ReviewerSeatRefused('reviewer seat attempt 没有对应 ledger')
        index, prior = _locate(ledger, turn_id)
        if prior.outcome != 'started':
            raise ReviewerSeatRefused('reviewer seat turn 已终态，不能再登记 attempt')
        if any(row.attempt_id == attempt_id for row in prior.attempts):
            raise ReviewerSeatRefused('reviewer seat attempt_id 重复')
        attempt = Stage2ReviewAttempt(
            attempt_id=attempt_id, kind=kind,  # type: ignore[arg-type]
            started_at=int(time.time()) if now is None else now,
            provider_session_id=provider_session_id)
        updated = ReviewerSeatTurn.model_validate({
            **prior.model_dump(mode='json'),
            'attempts': [
                *[row.model_dump(mode='json') for row in prior.attempts],
                attempt.model_dump(mode='json')],
        })
        return _replace(path, ledger, index, updated)


def finish_attempt(
    base: str | os.PathLike[str], sid: str, *,
    waiting_on: str, business_sha256: str, turn_id: str,
    attempt_id: str, exit_code: int, error_kind: str | None,
    provider_session_id: str | None, wire_evidence: bool,
    now: int | None = None,
) -> ReviewerSeatTurn:
    path = ledger_path(
        base, sid, waiting_on=waiting_on, business_sha256=business_sha256)
    with path_transaction_lock(path):
        ledger = _read(path)
        if ledger is None:
            raise ReviewerSeatRefused('reviewer seat attempt 没有对应 ledger')
        index, prior = _locate(ledger, turn_id)
        rows = [row.model_dump(mode='json') for row in prior.attempts]
        hits = [i for i, row in enumerate(rows) if row['attempt_id'] == attempt_id]
        if len(hits) != 1:
            raise ReviewerSeatRefused('reviewer seat attempt 在轮内不唯一')
        current = rows[hits[0]]
        if current.get('exit_code') is not None:
            if (current.get('exit_code') == exit_code
                    and current.get('error_kind') == error_kind):
                return prior
            raise ReviewerSeatRefused('reviewer seat attempt 已有冲突终态')
        rows[hits[0]] = {
            **current,
            'finished_at': int(time.time()) if now is None else now,
            'exit_code': exit_code, 'error_kind': error_kind,
            'provider_session_id': (
                provider_session_id or current.get('provider_session_id')),
            'wire_evidence': bool(current.get('wire_evidence')) or wire_evidence,
        }
        updated = ReviewerSeatTurn.model_validate({
            **prior.model_dump(mode='json'), 'attempts': rows})
        return _replace(path, ledger, index, updated)


def read(
    base: str | os.PathLike[str], sid: str, *,
    waiting_on: str, business_sha256: str,
) -> ReviewerSeatLedger | None:
    return _read(ledger_path(
        base, sid, waiting_on=waiting_on, business_sha256=business_sha256))


def list_ledgers(base: str | os.PathLike[str], sid: str) -> list[dict[str, Any]]:
    """只读列出一本书全部 reviewer 席位账的无正文摘要。"""
    root = paths.service_runs_dir(base) / paths.check_sid(sid) / 'reviewer_seats'
    if not root.is_dir():
        return []
    shown: list[dict[str, Any]] = []
    for candidate in sorted(root.glob('*/*.json')):
        if not candidate.is_file() or candidate.is_symlink():
            continue
        ledger = _read(candidate)
        if ledger is None:
            continue
        shown.append({
            'ledger_path': str(candidate),
            'waiting_on': ledger.waiting_on,
            'business_sha256': ledger.business_sha256,
            'max_corrections': ledger.max_corrections,
            'corrections_used': corrections_used(ledger),
            'pending_correction': ledger.pending_correction is not None,
            'turns': [{
                'ordinal': row.ordinal, 'purpose': row.purpose,
                'outcome': row.outcome, 'exit_code': row.exit_code,
                'session_run_id': row.session_run_id,
                'attempts': len(row.attempts),
            } for row in ledger.turns],
            'sha256': hashlib.sha256(candidate.read_bytes()).hexdigest(),
        })
    return shown


__all__ = [
    'MAX_CORRECTIONS', 'ReviewerSeatRefused', 'bind_response', 'corrections_used',
    'clear_pending_correction', 'finish', 'finish_attempt', 'ledger_path',
    'list_ledgers', 'mark_pending_correction', 'read', 'record_attempt',
    'reserve',
]
