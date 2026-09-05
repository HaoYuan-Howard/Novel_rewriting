#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage2 Global Commander 的跨进程席位与单调进展账（v2）。

这份账只落无正文 identity：Global/MainText/workpack SHA、未闭合项数量与调用结果。
模型进程一旦获得席位就先写 ``started``，所以宿主崩溃也不会重新发放同一席。
每个 Global + MainText 主体最多 ``STAGE2_COMMANDER_MAX_SEATS`` 席：前三席承担
完整审阅与定向订正，第四席只保留给只读 ``final_review``；每席必须是新的 logical
session，同席内的 resume/relaunch 只追加 ``attempts``，不占新席。相同 progress
fingerprint 不允许靠重启重演；终审 reopen 之后允许未闭合数回升。

Blueprint 期间的 ``prereview`` 就是第一席完整审阅，不能因稍后才绑定 live surface
而返还预算。``post_edit_review`` 则随当席 ``correction`` 一起提交，不另占一席：
只有该 typed edit 已成功落地、下一快照的 ``blocker_count`` 为 0，后续才是
``final_review``；若请求在落盘前被拒，业务仍未修完，后续仍只能是 ``correction``，
不得借第四席冒充终审继续修改。

v1 三席账没有迁移机制：``_read`` 拒读，只能经 :func:`reopen_archive` 带
actor/reason 整体归档后开新账。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from contracts.invariants import (
    STAGE2_COMMANDER_MAX_EDIT_SEAT, STAGE2_COMMANDER_MAX_SEATS,
)
from contracts.schema.orchestration import (
    Stage2ReviewAttempt, Stage2ReviewLedger, Stage2ReviewLedgerArchive,
    Stage2ReviewLedgerReconciliation, Stage2ReviewLedgerTurn,
    Stage2ReviewRejection,
)
from novel import paths, run_identity
from novel.appendlog import path_transaction_lock
from novel.errors import PathRefused, TerminalState
from novel.llm import ledger as cost_ledger


MAX_COMMANDER_CALLS = STAGE2_COMMANDER_MAX_SEATS
#: 席位账不是 artifact，不走 store；用 typed adapter 读原始 object 供版本分流。
_RAW_LEDGER = TypeAdapter(dict[str, Any])
MAX_EDIT_SEAT = STAGE2_COMMANDER_MAX_EDIT_SEAT
_ARCHIVABLE_SCHEMA_VERSIONS = (1, 2)
_LEGACY_NO_WIRE_EXIT_CODE = 70
_LEGACY_NO_WIRE_SIGNATURE = 'ValueError: admission id 必须属于当前 epoch'
_MAX_REJECTION_REASON_CHARS = 8192


class Stage2ReviewBudgetRefused(RuntimeError):
    """预算耗尽、进展倒退或持久账不闭合。"""


@dataclass(frozen=True)
class Stage2NoWireReconcileSubject:
    """一份可供外部操作员复核并 CAS 应用的旧假预留主体。"""

    sid: str
    session_run_id: str
    parent_run_id: str
    ledger_path: Path
    original_ordinal: int
    active_calls: int
    remaining_calls: int
    subject_sha256: str
    original_ledger_sha256: str
    run_log_sha256: str
    agent_sessions_sha256: str
    cost_ledger_sha256: str
    parent_ended_at: int
    parent_exit_code: int
    already_reconciled: bool = False


@dataclass(frozen=True)
class Stage2PendingRejection:
    """最后一席、且仍绑定当前 workpack 的待纠错拒因。"""

    turn: Stage2ReviewLedgerTurn
    rejection: Stage2ReviewRejection


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


def _raw_ledger(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise Stage2ReviewBudgetRefused(
            f'Stage2 review ledger 不是普通文件：{path}')
    try:
        raw = _RAW_LEDGER.validate_json(path.read_bytes())
    except Exception as exc:  # noqa: BLE001 —— 坏账不能静默重置预算
        raise Stage2ReviewBudgetRefused(
            f'Stage2 review ledger 不是合法 JSON object：{path}') from exc
    return raw


def _read(path: Path) -> Stage2ReviewLedger | None:
    """只接受当前 v2 账；v1 三席账关闭式拒绝并指向唯一的归档入口。"""
    raw = _raw_ledger(path)
    if raw is None:
        return None
    version = raw.get('schema_version')
    if version != 2:
        raise Stage2ReviewBudgetRefused(
            f'Stage2 review ledger 是 schema_version={version!r} 的旧席位账：{path}；'
            '本仓没有迁移机制，只能经 `reconcile --stage2-review-reopen` '
            '带 actor/reason 整体归档后开 v2 新账')
    try:
        return Stage2ReviewLedger.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 —— 坏账不能静默重置预算
        raise Stage2ReviewBudgetRefused(
            f'Stage2 review ledger 无法按当前 typed 契约读取：{path}') from exc


def read_any_version(path: Path) -> tuple[dict[str, Any], Stage2ReviewLedgerArchive]:
    """只供归档：把 v1/v2 账读成一份 typed archive 记录，不产生可预留的新账。

    v1 turn 行的 ordinal/purpose 都落在 v2 的取值面内，可以逐字复读；
    ``max_calls`` / ``state`` 只作为原始事实保留在 archive 元数据里。
    """
    raw = _raw_ledger(path)
    if raw is None:
        raise TerminalState(f'Stage2 review ledger 不在盘上：{path}')
    version = raw.get('schema_version')
    if version not in _ARCHIVABLE_SCHEMA_VERSIONS:
        raise TerminalState(
            f'Stage2 review ledger schema_version={version!r} 不在可归档面内')
    try:
        turns = [
            Stage2ReviewLedgerTurn.model_validate(row)
            for row in raw.get('turns', [])]
        reconciliations = [
            Stage2ReviewLedgerReconciliation.model_validate(row)
            for row in raw.get('reconciliations', [])]
        max_calls = int(raw['max_calls'])
        state = str(raw['state'])
    except Exception as exc:  # noqa: BLE001
        raise TerminalState(
            f'Stage2 review ledger 无法按可归档形状读取：{path}') from exc
    archive = Stage2ReviewLedgerArchive(
        actor='pending', reason='pending', archived_at=0,
        original_schema_version=int(version),
        original_max_calls=max_calls,
        original_state=state,  # type: ignore[arg-type]
        original_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        turns=turns, reconciliations=reconciliations,
    )
    return raw, archive


def _ledger_with_session(
        base: str | os.PathLike[str], sid: str, session_run_id: str,
) -> tuple[Path, Stage2ReviewLedger, Stage2ReviewLedgerTurn,
           Stage2ReviewLedgerReconciliation | None]:
    """在单一 SID 的关闭目录里精确定位一个 active/已隔离 session。"""
    checked_sid = paths.check_sid(sid)
    checked_session = run_identity.check_run_id(session_run_id)
    root = (
        paths.service_runs_dir(base) / checked_sid / 'stage2'
        / 'global_review_ledgers')
    service_root = paths.service_dir(base).absolute()
    if service_root.is_symlink():
        raise PathRefused('_service 根不能是符号链接')
    try:
        root.absolute().resolve().relative_to(service_root.resolve())
    except ValueError as exc:
        raise PathRefused('Stage2 review ledger 目录越出 _service') from exc
    matches: list[
        tuple[Path, Stage2ReviewLedger, Stage2ReviewLedgerTurn,
              Stage2ReviewLedgerReconciliation | None]
    ] = []
    if root.is_dir():
        for candidate in sorted(root.glob('*.json')):
            ledger = _read(candidate)
            if ledger is None:
                continue
            active = [
                row for row in ledger.turns
                if row.session_run_id == checked_session]
            reconciled = [
                row for row in ledger.reconciliations
                if row.removed_turn.session_run_id == checked_session]
            if active and reconciled:
                raise Stage2ReviewBudgetRefused(
                    '同一 Stage2 review session 同时出现在有效与隔离预算面')
            if len(active) == 1:
                matches.append((candidate, ledger, active[0], None))
            elif len(reconciled) == 1:
                matches.append((
                    candidate, ledger, reconciled[0].removed_turn,
                    reconciled[0]))
            elif len(active) > 1 or len(reconciled) > 1:
                raise Stage2ReviewBudgetRefused(
                    'Stage2 review session 在同一 ledger 中不唯一')
    if len(matches) != 1:
        raise TerminalState(
            f'Stage2 review session {checked_session!r} '
            f'在 sid={checked_sid} 的持久账中命中 {len(matches)} 份；拒绝猜测')
    return matches[0]


def _closed_append_log_without_session(
        path: Path, *, label: str, session_run_id: str,
) -> tuple[str, int, bool]:
    """给负证据取 committed 文件 hash；半截尾或 session 命中都拒绝。"""
    if not path.exists():
        raw = b''
        exists = False
    else:
        if path.is_symlink() or not path.is_file():
            raise TerminalState(f'{label} 不是普通文件，无法证明 0-wire')
        raw = path.read_bytes()
        exists = True
        if raw and not raw.endswith(b'\n'):
            raise TerminalState(
                f'{label} 有 non-LF 未提交尾；须先修复 append-only 坏尾')
    if session_run_id.encode('ascii') in raw:
        raise TerminalState(
            f'{label} 已出现 session_run_id={session_run_id}；'
            '不能把这次调用认定为 0-wire')
    return hashlib.sha256(raw).hexdigest(), len(raw), exists


def _current_no_wire_subject(
        base: str | os.PathLike[str], path: Path,
        ledger: Stage2ReviewLedger, turn: Stage2ReviewLedgerTurn,
) -> Stage2NoWireReconcileSubject:
    """重算已知旧 admission/epoch 预审事故的完整负证据主体。"""
    if (turn.purpose != 'prereview' or turn.outcome != 'failed'
            or turn.exit_code != _LEGACY_NO_WIRE_EXIT_CODE
            or turn.finished_at is None):
        raise TerminalState(
            '目标 turn 不是 exit 70 的失败 Stage2 prereview；'
            'started/其他失败仍消耗预算')
    duration = turn.finished_at - turn.started_at
    if duration < 0 or duration > 1:
        raise TerminalState(
            '目标 prereview 不符合已知的即时 pre-spawn 失败形状；拒绝按 0-wire 恢复')

    # 延迟导入避免 agent -> stage2_review_ledger 的模块初始化环；这里是外部
    # operator recovery 路，不在 AgentRun 热路径中调用。
    from novel.orchestrator import agent as agent_runner

    parents = [
        row for row in agent_runner.records(base)
        if row.run_id == turn.parent_run_id]
    if len(parents) != 1:
        raise TerminalState('找不到唯一 parent AgentRun，无法证明预审已终止')
    parent = parents[0]
    if parent.ended is None or parent.exit_code is None or parent.state in (
            'pending', 'running', 'paused', 'unknown'):
        raise TerminalState('parent AgentRun 尚无确定终态，拒绝释放预算')

    log_path = agent_runner.run_log_path(base, turn.parent_run_id)
    if log_path.is_symlink() or not log_path.is_file():
        raise TerminalState('parent AgentRun 日志缺失或不是普通文件')
    log_bytes = log_path.read_bytes()
    if _LEGACY_NO_WIRE_SIGNATURE.encode('utf-8') not in log_bytes:
        raise TerminalState(
            'parent 日志没有已知 admission/epoch pre-spawn 失败签名；'
            '拒绝把一般 exit 70 当成 0-wire')
    run_log_sha256 = hashlib.sha256(log_bytes).hexdigest()

    from novel import agent_session_audit

    agent_sessions_sha256, agent_sessions_size, agent_sessions_exists = (
        _closed_append_log_without_session(
            agent_session_audit.audit_path(base), label='agent_sessions.jsonl',
            session_run_id=turn.session_run_id))
    cost_ledger_sha256, cost_ledger_size, cost_ledger_exists = (
        _closed_append_log_without_session(
            cost_ledger.ledger_path(base), label='ledger.jsonl',
            session_run_id=turn.session_run_id))

    ledger_bytes = path.read_bytes()
    original_ledger_sha256 = hashlib.sha256(ledger_bytes).hexdigest()
    payload = {
        'protocol': 'novel-stage2-review-no-wire-reconcile/v1',
        'sid': ledger.sid,
        'source_sha256': ledger.source_sha256,
        'main_text_sha256': ledger.main_text_sha256,
        'ledger_sha256': original_ledger_sha256,
        'turn': turn.model_dump(mode='json'),
        'parent': {
            'run_id': turn.parent_run_id,
            'ended_at': parent.ended,
            'exit_code': parent.exit_code,
        },
        'known_failure_signature': _LEGACY_NO_WIRE_SIGNATURE,
        'run_log_sha256': run_log_sha256,
        'negative_wire_evidence': {
            'agent_sessions': {
                'exists': agent_sessions_exists,
                'size': agent_sessions_size,
                'sha256': agent_sessions_sha256,
            },
            'cost_ledger': {
                'exists': cost_ledger_exists,
                'size': cost_ledger_size,
                'sha256': cost_ledger_sha256,
            },
        },
    }
    subject_sha256 = hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False,
    ).encode('utf-8')).hexdigest()
    return Stage2NoWireReconcileSubject(
        sid=ledger.sid,
        session_run_id=turn.session_run_id,
        parent_run_id=turn.parent_run_id,
        ledger_path=path,
        original_ordinal=turn.ordinal,
        active_calls=len(ledger.turns),
        remaining_calls=ledger.max_calls - len(ledger.turns) + 1,
        subject_sha256=subject_sha256,
        original_ledger_sha256=original_ledger_sha256,
        run_log_sha256=run_log_sha256,
        agent_sessions_sha256=agent_sessions_sha256,
        cost_ledger_sha256=cost_ledger_sha256,
        parent_ended_at=parent.ended,
        parent_exit_code=parent.exit_code,
    )


def inspect_no_wire_reconciliation(
        base: str | os.PathLike[str], sid: str, *, session_run_id: str,
) -> Stage2NoWireReconcileSubject:
    """只读展示旧 pre-spawn 假预留的 current hash-bound 主体。"""
    path, ledger, turn, existing = _ledger_with_session(
        base, sid, session_run_id)
    if existing is not None:
        return Stage2NoWireReconcileSubject(
            sid=ledger.sid,
            session_run_id=turn.session_run_id,
            parent_run_id=turn.parent_run_id,
            ledger_path=path,
            original_ordinal=turn.ordinal,
            active_calls=len(ledger.turns),
            remaining_calls=ledger.max_calls - len(ledger.turns),
            subject_sha256=existing.subject_sha256,
            original_ledger_sha256=existing.original_ledger_sha256,
            run_log_sha256=existing.run_log_sha256,
            agent_sessions_sha256=existing.agent_sessions_sha256,
            cost_ledger_sha256=existing.cost_ledger_sha256,
            parent_ended_at=existing.parent_ended_at,
            parent_exit_code=existing.parent_exit_code,
            already_reconciled=True,
        )
    return _current_no_wire_subject(base, path, ledger, turn)


def reconcile_no_wire_reservation(
        base: str | os.PathLike[str], sid: str, *, session_run_id: str,
        actor: str, reason: str, subject_sha256: str,
        now: int | None = None,
) -> Stage2ReviewLedgerReconciliation:
    """隔离一格经完整负证据确认的旧假预留；幂等同决定，漂移拒绝。"""
    actor = actor.strip()
    reason = reason.strip()
    if not actor or not reason:
        raise TerminalState('Stage2 no-wire reconcile 的 actor/reason 不得为空')
    path, initial, turn, existing = _ledger_with_session(
        base, sid, session_run_id)
    if existing is not None:
        if (existing.actor == actor and existing.reason == reason
                and existing.subject_sha256 == subject_sha256):
            return existing
        raise TerminalState('Stage2 no-wire reconcile 已由另一份 actor/reason/subject 收口')
    if initial.state == 'complete':
        raise TerminalState('已 complete 的 Stage2 review ledger 不得重新打开')

    from novel.orchestrator import admission

    # 锁住 parent owner 后再在 ledger 事务锁内重算全部 current evidence；旧
    # 进程与新预算不可能在两次读取之间穿插。
    with admission.require_owner_stopped(base, turn.parent_run_id):
        with path_transaction_lock(path):
            ledger = _read(path)
            if ledger is None:
                raise TerminalState('Stage2 review ledger 在 apply 前消失')
            matches = [
                row for row in ledger.turns
                if row.session_run_id == turn.session_run_id]
            if len(matches) != 1:
                # 事务前可能已有并发重试完成；按同决定幂等读取。
                reconciled = [
                    row for row in ledger.reconciliations
                    if row.removed_turn.session_run_id == turn.session_run_id]
                if len(reconciled) == 1 and (
                        reconciled[0].actor == actor
                        and reconciled[0].reason == reason
                        and reconciled[0].subject_sha256 == subject_sha256):
                    return reconciled[0]
                raise TerminalState('Stage2 review ledger 主体已变化；请重新 inspect')
            current = _current_no_wire_subject(
                base, path, ledger, matches[0])
            if current.subject_sha256 != subject_sha256:
                raise TerminalState(
                    'Stage2 no-wire reconcile subject SHA 已变化；'
                    f'current={current.subject_sha256}')
            reconciliation = Stage2ReviewLedgerReconciliation(
                removed_turn=matches[0], actor=actor, reason=reason,
                subject_sha256=current.subject_sha256,
                original_ledger_sha256=current.original_ledger_sha256,
                run_log_sha256=current.run_log_sha256,
                agent_sessions_sha256=current.agent_sessions_sha256,
                cost_ledger_sha256=current.cost_ledger_sha256,
                parent_ended_at=current.parent_ended_at,
                parent_exit_code=current.parent_exit_code,
                reconciled_at=int(time.time()) if now is None else now,
            )
            kept = [
                row for row in ledger.turns
                if row.session_run_id != turn.session_run_id]
            reindexed = [
                Stage2ReviewLedgerTurn.model_validate({
                    **row.model_dump(mode='json'), 'ordinal': index,
                })
                for index, row in enumerate(kept, start=1)
            ]
            state = (
                'exhausted'
                if len(reindexed) == ledger.max_calls else 'active')
            updated = Stage2ReviewLedger.model_validate({
                **ledger.model_dump(mode='json'),
                'turns': [row.model_dump(mode='json') for row in reindexed],
                'reconciliations': [
                    *[row.model_dump(mode='json')
                      for row in ledger.reconciliations],
                    reconciliation.model_dump(mode='json'),
                ],
                'state': state,
            })
            _atomic_write(path, updated)
            return reconciliation


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
                f'Stage2 Global 已用满 {ledger.max_calls} 席 Commander')
        next_ordinal = len(ledger.turns) + 1
        if purpose != 'final_review' and next_ordinal > MAX_EDIT_SEAT:
            raise Stage2ReviewBudgetRefused(
                f'Stage2 Global 前 {MAX_EDIT_SEAT} 席已用完；第 {next_ordinal} 席'
                f'只保留给只读 final_review，不能再承担 {purpose}')
        if purpose == 'final_review' and blocker_count != 0:
            raise Stage2ReviewBudgetRefused(
                'Stage2 Global 仍有未闭合 blocking 项，不能预留只读终审席')
        if any(row.progress_sha256 == progress_sha256 for row in ledger.turns):
            raise Stage2ReviewBudgetRefused(
                'Stage2 Global 未闭合项与拒因 fingerprint 没有变化，拒绝重复调用')
        previous = ledger.turns[-1] if ledger.turns else None
        # 终审 reopen 把 PASS 项重新打成 FAIL/UNVER 是独立复审的合法产出，
        # 不是模型在未知扩张上空转；只有普通 review/correction 之间才禁止反增。
        if (previous is not None
                and previous.purpose != 'final_review'
                and purpose != 'final_review'
                and blocker_count > previous.blocker_count):
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


def _locate_turn(
    ledger: Stage2ReviewLedger, session_run_id: str,
) -> tuple[int, Stage2ReviewLedgerTurn]:
    matches = [
        (index, row) for index, row in enumerate(ledger.turns)
        if row.session_run_id == session_run_id
    ]
    if len(matches) != 1:
        raise Stage2ReviewBudgetRefused('Stage2 review session 在 ledger 中不唯一')
    return matches[0]


def _replace_turn(
    path: Path, ledger: Stage2ReviewLedger, index: int,
    turn: Stage2ReviewLedgerTurn,
) -> Stage2ReviewLedgerTurn:
    turns = [row.model_dump(mode='json') for row in ledger.turns]
    turns[index] = turn.model_dump(mode='json')
    updated = Stage2ReviewLedger.model_validate({
        **ledger.model_dump(mode='json'), 'turns': turns,
    })
    _atomic_write(path, updated)
    return updated.turns[index]


def record_rejection(
    base: str | os.PathLike[str], sid: str, *,
    source_sha256: str,
    main_text_sha256: str,
    business_sha256: str,
    workpack_sha256: str,
    session_run_id: str,
    kind: str,
    request_sha256: str,
    reason: str,
    now: int | None = None,
) -> Stage2ReviewRejection:
    """追加本席回答被 typed/canonical 边界拒绝的事实。

    Commander 进程本身必须已经成功结束；started/failed 席位不能把进程失败
    伪装成业务请求拒绝。同一 request identity 的同 kind/reason 幂等返回原记录，
    但不同 kind/reason 关闭式拒绝，不能覆盖已经持久化的根因。
    """
    path = ledger_path(
        base, sid, source_sha256=source_sha256,
        main_text_sha256=main_text_sha256)
    full_reason = reason
    if not full_reason.strip():
        raise Stage2ReviewBudgetRefused('Stage2 typed/canonical 拒因不得为空')
    reason_sha256 = hashlib.sha256(full_reason.encode('utf-8')).hexdigest()
    suffix = f'\n…（完整拒因 SHA-256={reason_sha256}）'
    readable_reason = full_reason
    if len(readable_reason) > _MAX_REJECTION_REASON_CHARS:
        readable_reason = (
            readable_reason[:_MAX_REJECTION_REASON_CHARS - len(suffix)]
            + suffix)
    recorded = Stage2ReviewRejection(
        kind=kind,  # type: ignore[arg-type]
        request_sha256=request_sha256,
        reason_sha256=reason_sha256,
        reason=readable_reason,
        recorded_at=int(time.time()) if now is None else now,
    )
    with path_transaction_lock(path):
        ledger = _read(path)
        if ledger is None:
            raise Stage2ReviewBudgetRefused(
                'Stage2 typed/canonical 拒因没有对应 ledger')
        index, prior = _locate_turn(ledger, session_run_id)
        if (prior.business_sha256 != business_sha256
                or prior.workpack_sha256 != workpack_sha256):
            raise Stage2ReviewBudgetRefused(
                'Stage2 typed/canonical 拒因与席位 business/workpack identity 不符')
        if prior.outcome != 'ok' or prior.exit_code != 0:
            raise Stage2ReviewBudgetRefused(
                '只有成功结束的 Stage2 Commander 席位才能登记 typed/canonical 拒因')
        matches = [
            row for row in prior.rejections
            if row.request_sha256 == recorded.request_sha256]
        if matches:
            if len(matches) != 1:
                raise Stage2ReviewBudgetRefused(
                    '同一 Stage2 被拒请求在席位账中不唯一')
            existing = matches[0]
            if (existing.kind == recorded.kind
                    and existing.reason_sha256 == recorded.reason_sha256
                    and existing.reason == recorded.reason):
                return existing
            raise Stage2ReviewBudgetRefused(
                'Stage2 被拒请求已有不同 kind/reason，拒绝覆盖原始根因')
        updated = Stage2ReviewLedgerTurn.model_validate({
            **prior.model_dump(mode='json'),
            'rejections': [
                *[row.model_dump(mode='json') for row in prior.rejections],
                recorded.model_dump(mode='json'),
            ],
        })
        stored = _replace_turn(path, ledger, index, updated)
        return stored.rejections[-1]


def pending_rejection(
    base: str | os.PathLike[str], sid: str, *,
    source_sha256: str,
    main_text_sha256: str,
    business_sha256: str,
    workpack_sha256: str,
) -> Stage2PendingRejection | None:
    """返回最后一席尚未由后续席位消费的最新拒因；没有则为 ``None``。

    拒因历史永不删除。新 correction 一经预留就成为新的最后一席，因此旧拒因
    自动只保留为审计事实，不再被下一 parent 误认成仍待处理的 correction。
    """
    path = ledger_path(
        base, sid, source_sha256=source_sha256,
        main_text_sha256=main_text_sha256)
    ledger = _read(path)
    if ledger is None or ledger.state == 'complete' or not ledger.turns:
        return None
    latest = ledger.turns[-1]
    if (latest.business_sha256 != business_sha256
            or latest.workpack_sha256 != workpack_sha256
            or not latest.rejections):
        return None
    return Stage2PendingRejection(
        turn=latest, rejection=latest.rejections[-1])


def record_attempt(
    base: str | os.PathLike[str], sid: str, *,
    source_sha256: str,
    main_text_sha256: str,
    session_run_id: str,
    attempt_id: str,
    kind: str,
    provider_session_id: str | None = None,
    now: int | None = None,
) -> Stage2ReviewLedgerTurn:
    """在已预留席位下登记一次进程尝试（首发或 resume）；不占新席。"""
    path = ledger_path(
        base, sid, source_sha256=source_sha256,
        main_text_sha256=main_text_sha256)
    with path_transaction_lock(path):
        ledger = _read(path)
        if ledger is None:
            raise Stage2ReviewBudgetRefused('Stage2 review attempt 没有对应 ledger')
        index, prior = _locate_turn(ledger, session_run_id)
        if prior.outcome != 'started':
            raise Stage2ReviewBudgetRefused(
                'Stage2 review 席位已终态，不能再登记新的进程尝试')
        if any(row.attempt_id == attempt_id for row in prior.attempts):
            raise Stage2ReviewBudgetRefused('Stage2 review attempt_id 重复')
        attempt = Stage2ReviewAttempt(
            attempt_id=attempt_id, kind=kind,  # type: ignore[arg-type]
            started_at=int(time.time()) if now is None else now,
            provider_session_id=provider_session_id)
        updated = Stage2ReviewLedgerTurn.model_validate({
            **prior.model_dump(mode='json'),
            'attempts': [
                *[row.model_dump(mode='json') for row in prior.attempts],
                attempt.model_dump(mode='json'),
            ],
        })
        return _replace_turn(path, ledger, index, updated)


def finish_attempt(
    base: str | os.PathLike[str], sid: str, *,
    source_sha256: str,
    main_text_sha256: str,
    session_run_id: str,
    attempt_id: str,
    exit_code: int,
    error_kind: str | None,
    provider_session_id: str | None,
    wire_evidence: bool,
    now: int | None = None,
) -> Stage2ReviewLedgerTurn:
    """给一次进程尝试落终态；幂等同值，冲突关闭式拒绝。"""
    path = ledger_path(
        base, sid, source_sha256=source_sha256,
        main_text_sha256=main_text_sha256)
    with path_transaction_lock(path):
        ledger = _read(path)
        if ledger is None:
            raise Stage2ReviewBudgetRefused('Stage2 review attempt 没有对应 ledger')
        index, prior = _locate_turn(ledger, session_run_id)
        rows = [row.model_dump(mode='json') for row in prior.attempts]
        hits = [i for i, row in enumerate(rows) if row['attempt_id'] == attempt_id]
        if len(hits) != 1:
            raise Stage2ReviewBudgetRefused('Stage2 review attempt 在席位内不唯一')
        current = rows[hits[0]]
        finished = {
            **current,
            'finished_at': int(time.time()) if now is None else now,
            'exit_code': exit_code,
            'error_kind': error_kind,
            'provider_session_id': (
                provider_session_id or current.get('provider_session_id')),
            'wire_evidence': bool(current.get('wire_evidence')) or wire_evidence,
        }
        if current.get('exit_code') is not None:
            same = all(
                current.get(key) == finished[key]
                for key in ('exit_code', 'error_kind', 'wire_evidence'))
            if same:
                return prior
            raise Stage2ReviewBudgetRefused('Stage2 review attempt 已有冲突终态')
        rows[hits[0]] = finished
        updated = Stage2ReviewLedgerTurn.model_validate({
            **prior.model_dump(mode='json'), 'attempts': rows,
        })
        return _replace_turn(path, ledger, index, updated)


def reopen_archive(
    base: str | os.PathLike[str], sid: str, *,
    source_sha256: str,
    main_text_sha256: str,
    original_sha256: str,
    actor: str,
    reason: str,
    now: int | None = None,
) -> Stage2ReviewLedgerArchive:
    """把当前席位账（含 v1 三席账）整体归档，开一份空 v2 账。

    只在 ``original_sha256`` 仍逐字绑定当前账、所有席位都已终态、且它们的
    parent AgentRun 都已结束时允许；正在运行的席位不能被人从下面抽走预算。
    归档记录逐字保留旧 turns/reconciliations，新账仍按同一
    Global/MainText identity 计数。
    """
    actor = actor.strip()
    reason = reason.strip()
    if not actor or not reason:
        raise TerminalState('Stage2 review reopen 的 actor/reason 不得为空')
    path = ledger_path(
        base, sid, source_sha256=source_sha256,
        main_text_sha256=main_text_sha256)
    # 延迟导入避免 agent -> stage2_review_ledger 的模块初始化环。
    from novel.orchestrator import agent as agent_runner

    with path_transaction_lock(path):
        raw, archive = read_any_version(path)
        if archive.original_sha256 != original_sha256:
            raise TerminalState(
                'Stage2 review ledger 已在查看后变化；'
                f'expected original_sha256={original_sha256}，'
                f'current={archive.original_sha256}；请重新查看后再归档')
        if raw.get('sid') != sid or raw.get('source_sha256') != source_sha256 \
                or raw.get('main_text_sha256') != main_text_sha256:
            raise TerminalState('Stage2 review ledger 主体 identity 与请求不符')
        if any(row.outcome == 'started' for row in archive.turns):
            raise TerminalState(
                'Stage2 review ledger 仍有 started 席位；先让其 parent AgentRun '
                '终止并按 no-wire reconcile 或 finish 收口，再归档')
        records = {row.run_id: row for row in agent_runner.records(base)}
        for turn in archive.turns:
            parent = records.get(turn.parent_run_id)
            if parent is None:
                raise TerminalState(
                    f'席位 {turn.ordinal} 的 parent AgentRun {turn.parent_run_id} '
                    '不在 agent_runs 记录中，拒绝归档')
            if parent.ended is None or parent.state in (
                    'pending', 'running', 'paused', 'unknown'):
                raise TerminalState(
                    f'席位 {turn.ordinal} 的 parent AgentRun {turn.parent_run_id} '
                    f'仍是 {parent.state}，拒绝归档')
        stamped = archive.model_copy(update={
            'actor': actor, 'reason': reason,
            'archived_at': int(time.time()) if now is None else now,
        })
        stamped = Stage2ReviewLedgerArchive.model_validate(
            stamped.model_dump(mode='json'))
        prior_archives = []
        if raw.get('schema_version') == 2:
            prior_archives = list(raw.get('archived_ledgers', []))
        fresh = Stage2ReviewLedger.model_validate({
            'sid': sid,
            'source_sha256': source_sha256,
            'main_text_sha256': main_text_sha256,
            'turns': [],
            'reconciliations': [],
            'archived_ledgers': [
                *prior_archives, stamped.model_dump(mode='json')],
        })
        _atomic_write(path, fresh)
        return stamped


def _ledger_root(base: str | os.PathLike[str], sid: str) -> Path:
    checked_sid = paths.check_sid(sid)
    root = (
        paths.service_runs_dir(base) / checked_sid / 'stage2'
        / 'global_review_ledgers')
    service_root = paths.service_dir(base).absolute()
    if service_root.is_symlink():
        raise PathRefused('_service 根不能是符号链接')
    try:
        root.absolute().resolve().relative_to(service_root.resolve())
    except ValueError as exc:
        raise PathRefused('Stage2 review ledger 目录越出 _service') from exc
    return root


def list_ledgers(base: str | os.PathLike[str], sid: str) -> list[dict[str, Any]]:
    """只读列出一本书全部席位账（任意可归档版本）的无正文摘要。"""
    root = _ledger_root(base, sid)
    if not root.is_dir():
        return []
    return [
        _inspect_path(sid, candidate)
        for candidate in sorted(root.glob('*.json'))
        if candidate.is_file() and not candidate.is_symlink()
    ]


def reopen_archive_by_sha(
    base: str | os.PathLike[str], sid: str, *,
    original_sha256: str, actor: str, reason: str,
    now: int | None = None,
) -> Stage2ReviewLedgerArchive:
    """按查看模式给出的 ``original_sha256`` 精确定位并归档一份席位账。"""
    root = _ledger_root(base, sid)
    matches = []
    if root.is_dir():
        for candidate in sorted(root.glob('*.json')):
            if not candidate.is_file() or candidate.is_symlink():
                continue
            if hashlib.sha256(candidate.read_bytes()).hexdigest() == original_sha256:
                matches.append(candidate)
    if len(matches) != 1:
        raise TerminalState(
            f'original_sha256={original_sha256} 在 sid={sid} 的席位账中命中 '
            f'{len(matches)} 份；请重新查看后再归档')
    raw, _archive = read_any_version(matches[0])
    return reopen_archive(
        base, sid,
        source_sha256=str(raw.get('source_sha256')),
        main_text_sha256=str(raw.get('main_text_sha256')),
        original_sha256=original_sha256,
        actor=actor, reason=reason, now=now)


def _inspect_path(sid: str, path: Path) -> dict[str, Any]:
    raw, archive = read_any_version(path)
    return {
        'sid': sid,
        'ledger_path': str(path),
        'source_sha256': raw.get('source_sha256'),
        'main_text_sha256': raw.get('main_text_sha256'),
        'schema_version': raw.get('schema_version'),
        'max_calls': archive.original_max_calls,
        'state': archive.original_state,
        'turns': [{
            'ordinal': row.ordinal, 'purpose': row.purpose,
            'outcome': row.outcome, 'exit_code': row.exit_code,
            'parent_run_id': row.parent_run_id,
            'session_run_id': row.session_run_id,
            'attempts': len(row.attempts),
            'rejections': len(row.rejections),
            'latest_rejection': (
                row.rejections[-1].model_dump(mode='json')
                if row.rejections else None),
        } for row in archive.turns],
        'reconciliations': len(archive.reconciliations),
        'archived_ledgers': (
            len(raw.get('archived_ledgers', []))
            if raw.get('schema_version') == 2 else 0),
        'original_sha256': archive.original_sha256,
    }


def inspect_ledger(
    base: str | os.PathLike[str], sid: str, *,
    source_sha256: str, main_text_sha256: str,
) -> dict[str, Any]:
    """只读展示一份席位账（任意可归档版本）的无正文摘要。"""
    return _inspect_path(sid, ledger_path(
        base, sid, source_sha256=source_sha256,
        main_text_sha256=main_text_sha256))


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
        if (not ledger.turns or any(
                turn.outcome != 'ok' or turn.exit_code != 0
                for turn in ledger.turns)):
            raise Stage2ReviewBudgetRefused(
                'Stage2 review ledger 尚有 started/failed 席位，不能标 complete')
        updated = Stage2ReviewLedger.model_validate({
            **ledger.model_dump(mode='json'), 'state': 'complete',
        })
        _atomic_write(path, updated)


__all__ = [
    'MAX_COMMANDER_CALLS', 'MAX_EDIT_SEAT', 'Stage2NoWireReconcileSubject',
    'Stage2PendingRejection',
    'Stage2ReviewBudgetRefused', 'finish', 'finish_attempt',
    'inspect_ledger', 'inspect_no_wire_reconciliation',
    'ledger_path', 'list_ledgers', 'mark_complete', 'pending_rejection',
    'progress_with_feedback', 'read_any_version',
    'reconcile_no_wire_reservation', 'record_attempt', 'record_rejection',
    'reopen_archive', 'reopen_archive_by_sha', 'reserve',
]
