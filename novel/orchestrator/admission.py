#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Commander 子执行的 durable admission gate。

顶层 Commander CLI 的退出只说明它自己的进程结束；它此前准许的
``run_step --execute`` 可能仍在运行，甚至可能正卡在“准备启动、尚未写 beacon”
的缝里。只扫描 ``agent_progress.jsonl`` 会把这个窗口误读成空，从而过早终验、
释放同书锁，给迟到写盘留下竞态。

本模块用一份 append-only 六态账关闭这条缝：父 run 先 ``opened``；每个 headless
``run_step --execute`` 在任何 evidence / beacon / node subprocess 前原子
``claimed``，完整 Step 序列结束后 ``released``；顶层 invocation 返回或异常时
原子 ``closed``。``claim`` 与 ``close`` 共用目标文件的同一
``append_transaction``，所以二者只有一个可重放的先后答案：close 后的新 claim
必定拒绝，close 前的 claim 必定留在 active 集合里等到 release。

这份账只管“谁被准许启动”，不替代 beacon 的“节点进程是否精确 end”。父 worker
必须同时等 active claims 与 open executions 两面归零，再落 ``drained``，才能
进入 delivery。服务崩溃遗留项只能经 hash-bound ``reconciled`` 人工收口；主体
绑定父 run 的全量 beacon 生命周期，seal 后任一迟到 release/beacon 都会使它重新
unresolved。non-LF 坏尾只能经 committed-prefix/tail 双 hash 的隔离恢复口截断。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from contracts.schema.orchestration import (
    CommanderAdmissionEvent, CommanderAdmissionTailRepairRecord, ProgressEvent,
)
from novel import run_identity
from novel.appendlog import (
    append_line_under_process_lock, append_transaction, process_append_lock,
)
from novel.errors import TerminalState
from novel.orchestrator import append_tail_repair
from novel.orchestrator.jobs import service_dir


ADMISSION_FILENAME = 'commander_admissions.jsonl'
TAIL_REPAIR_DIRNAME = 'commander_admission_tail_repairs'
OWNER_LEASE_DIRNAME = 'commander_owner_leases'
CLAIM_ID_ENV = 'NOVEL_COMMANDER_ADMISSION_CLAIM_ID'


def admission_path(base: str | os.PathLike[str]) -> Path:
    return service_dir(base) / ADMISSION_FILENAME


def owner_lease_path(base: str | os.PathLike[str], parent_run_id: str) -> Path:
    """返回父 Commander 的 OS owner lease 目标；残留文件不表示仍存活。"""
    checked = run_identity.check_run_id(parent_run_id)
    return service_dir(base) / OWNER_LEASE_DIRNAME / f'{checked}.owner'


@contextmanager
def owner_lease(base: str | os.PathLike[str], parent_run_id: str):
    """持有父 Commander 生命周期 owner lease。

    真值是 ``process_append_lock`` 的 OS byte-lock，不是 ``*.lock`` 文件存在性；
    因而服务进程崩溃时内核会自动释放，残留文件不会伪造一个永远活着的 owner。
    """
    with process_append_lock(owner_lease_path(base, parent_run_id)):
        yield


@contextmanager
def _probe_process_lock(path: Path, *, label: str):
    """非阻塞取得与 ``process_append_lock`` 相同的 byte-lock，并持到 CAS 结束。"""
    lock_path = path.with_name(path.name + '.lock')
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, 'a+b') as handle:
        message = (
            f'{label} 的 Commander owner lease 仍被持有或无法核验；'
            '即使 --force 也不得人工销账，请先 stop/等待 owner 退出')
        if os.name == 'nt':
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b'\0')
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise TerminalState(message) from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise TerminalState(message) from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def require_owner_stopped(
        base: str | os.PathLike[str], parent_run_id: str,
):
    """证明目标 parent 没有 live owner，并把证明持到调用方事务结束。"""
    checked = run_identity.check_run_id(parent_run_id)
    with _probe_process_lock(
            owner_lease_path(base, checked), label=f'parent run {checked!r}'):
        yield


@contextmanager
def _require_all_owners_stopped(base: str | os.PathLike[str]):
    """tail apply 的全局 owner 证明；调用方必须已持 admission 事务锁。"""
    root = service_dir(base) / OWNER_LEASE_DIRNAME
    with ExitStack() as stack:
        if root.is_dir():
            for lock_path in sorted(root.glob('*.owner.lock')):
                target = lock_path.with_name(lock_path.name.removesuffix('.lock'))
                stack.enter_context(_probe_process_lock(
                    target, label=f'owner lease {target.stem!r}'))
        yield


def _read_events(path: Path) -> list[CommanderAdmissionEvent]:
    """读完整 wire；只容忍被 kill 截断的最后一行。"""
    if not path.is_file():
        return []
    # LF 是 append-only 事件的提交标记。完整 JSON 若还没写出 LF，同样不能
    # 冒充已提交；普通 ``splitlines()`` 会把这两种形状压成一个字符串。
    raw = path.read_bytes().splitlines(keepends=True)
    out: list[CommanderAdmissionEvent] = []
    for index, raw_line in enumerate(raw):
        complete = raw_line.endswith(b'\n')
        if not complete:
            if index == len(raw) - 1:
                break
            raise ValueError(
                f'{path} 第 {index + 1} 行不是 LF 提交的完整 admission 事件')
        payload = raw_line[:-1]
        if payload.endswith(b'\r'):
            payload = payload[:-1]
        line = payload.decode('utf-8', errors='strict').strip()
        if not line:
            continue
        out.append(CommanderAdmissionEvent.model_validate(json.loads(line)))
    return out


def read_events(base: str | os.PathLike[str]) -> list[CommanderAdmissionEvent]:
    return _read_events(admission_path(base))


@dataclass(frozen=True)
class AdmissionClaim:
    parent_run_id: str
    sid: str
    claim_id: str
    node: str
    claim_sha256: str


@dataclass(frozen=True)
class AdmissionSnapshot:
    #: 历史字段名保留 wire/API 兼容；它实际是这枚 admission gate 的身份。
    parent_run_id: str
    #: 持有 owner lease、写 progress beacon、接收 AgentRun lifecycle 投影的
    #: 外层 run。新 v2 opened 直接固化；旧 v1 在 typed reconcile 时才升级。
    lifecycle_run_id: str
    sid: str
    opened: bool
    closed: bool
    active_claims: tuple[AdmissionClaim, ...]
    lifecycle_linked: bool = False
    terminal_kind: str | None = None
    terminal_subject_sha256: str | None = None
    terminal_actor: str | None = None
    terminal_reason: str | None = None
    terminal_force: bool | None = None
    reconcile_invalidated: bool = False


AdmissionTailRepairSubject = append_tail_repair.TailRepairSubject


def _claim_of(event: CommanderAdmissionEvent) -> AdmissionClaim:
    assert event.claim_id is not None
    assert event.node is not None
    assert event.claim_sha256 is not None
    return AdmissionClaim(
        parent_run_id=event.parent_run_id,
        sid=event.sid,
        claim_id=event.claim_id,
        node=event.node,
        claim_sha256=event.claim_sha256,
    )


def _fold(events: list[CommanderAdmissionEvent], parent_run_id: str,
          ) -> AdmissionSnapshot:
    checked = run_identity.check_run_id(parent_run_id)
    selected = [event for event in events if event.parent_run_id == checked]
    if not selected:
        return AdmissionSnapshot(
            parent_run_id=checked, lifecycle_run_id=checked,
            sid='', opened=False, closed=False,
            active_claims=(), terminal_kind=None)

    sid = selected[0].sid
    lifecycle_run_id: str | None = None
    lifecycle_linked = False
    opened = False
    closed = False
    terminal_kind: str | None = None
    terminal_subject_sha256: str | None = None
    terminal_actor: str | None = None
    terminal_reason: str | None = None
    terminal_force: bool | None = None
    reconcile_invalidated = False
    claims: dict[str, AdmissionClaim] = {}
    active_order: list[str] = []
    for event in selected:
        if event.sid != sid:
            raise ValueError(
                f'parent run {checked!r} 的 admission sid 从 {sid!r} '
                f'漂移为 {event.sid!r}')
        if event.wire_version == 2:
            assert event.lifecycle_run_id is not None
            if (lifecycle_run_id is not None
                    and lifecycle_run_id != event.lifecycle_run_id):
                raise ValueError(
                    f'admission {checked!r} 的 lifecycle_run_id 从 '
                    f'{lifecycle_run_id!r} 漂移为 {event.lifecycle_run_id!r}')
            lifecycle_run_id = event.lifecycle_run_id
            lifecycle_linked = True
        if event.kind == 'opened':
            if opened or claims or closed:
                raise ValueError(f'parent run {checked!r} 重复或迟到 opened')
            opened = True
            continue
        if not opened:
            raise ValueError(
                f'parent run {checked!r} 在 opened 之前出现 {event.kind}')
        if event.kind == 'closed':
            if closed or terminal_kind is not None:
                raise ValueError(f'parent run {checked!r} 重复 closed')
            closed = True
            continue
        if event.kind == 'drained':
            if not closed or terminal_kind is not None or reconcile_invalidated:
                raise ValueError(
                    f'parent run {checked!r} 的 drained 必须唯一、在 closed 后，'
                    '且不能覆盖已失效的人工 reconcile')
            if active_order:
                raise ValueError('drained 时仍有 active claim')
            terminal_kind = 'drained'
            terminal_subject_sha256 = None
            terminal_actor = None
            terminal_reason = None
            terminal_force = None
            continue
        if event.kind == 'reconciled':
            if not closed or terminal_kind == 'drained':
                raise ValueError(
                    f'parent run {checked!r} 的 reconciled 必须在 closed 后，'
                    '且不能覆盖正常 drained')
            if (terminal_kind == 'reconciled'
                    and terminal_subject_sha256 == event.subject_sha256):
                raise ValueError(
                    f'parent run {checked!r} 对同一主体重复 reconciled')
            # 迟到 release 或 beacon 漂移会使上一枚 seal 失效；再次 typed
            # reconcile 仍复用同一事件种类，以新主体替换旧 seal。
            terminal_kind = 'reconciled'
            terminal_subject_sha256 = event.subject_sha256
            terminal_actor = event.actor
            terminal_reason = event.reason
            terminal_force = event.force
            reconcile_invalidated = False
            continue
        claim = _claim_of(event)
        if event.kind == 'claimed':
            if closed or terminal_kind is not None:
                raise ValueError(
                    f'parent run {checked!r} 在 closed 后出现 claim '
                    f'{claim.claim_id!r}')
            if claim.claim_id in claims:
                raise ValueError(f'claim_id {claim.claim_id!r} 重复 claimed')
            claims[claim.claim_id] = claim
            active_order.append(claim.claim_id)
            continue
        prior = claims.get(claim.claim_id)
        if prior is None:
            raise ValueError(f'claim_id {claim.claim_id!r} 没有 claimed 就 released')
        if prior != claim:
            raise ValueError(f'claim_id {claim.claim_id!r} 的 release 载荷漂移')
        if claim.claim_id not in active_order:
            raise ValueError(f'claim_id {claim.claim_id!r} 重复 released')
        if terminal_kind == 'drained':
            raise ValueError('drained 后不得再出现 release')
        if terminal_kind == 'reconciled':
            terminal_kind = None
            terminal_subject_sha256 = None
            terminal_actor = None
            terminal_reason = None
            terminal_force = None
            reconcile_invalidated = True
        active_order.remove(claim.claim_id)
    return AdmissionSnapshot(
        parent_run_id=checked, lifecycle_run_id=lifecycle_run_id or checked,
        sid=sid, opened=opened, closed=closed,
        active_claims=tuple(claims[item] for item in active_order),
        lifecycle_linked=lifecycle_linked,
        terminal_kind=terminal_kind,
        terminal_subject_sha256=terminal_subject_sha256,
        terminal_actor=terminal_actor,
        terminal_reason=terminal_reason,
        terminal_force=terminal_force,
        reconcile_invalidated=reconcile_invalidated)


def _with_reconcile_lifecycle(
        current: AdmissionSnapshot, lifecycle_run_id: str | None,
) -> AdmissionSnapshot:
    """给旧 v1 gate 套用已由上层精确证明的 lifecycle 联结。

    新 v2 账中的联结是 durable truth，调用方不得覆盖；旧 v1 没有这格时，
    ``None`` 维持历史 self-run 语义，显式值则只在本次 subject/CAS 使用，并由
    后续 v2 close/reconciled 行固化。
    """
    if lifecycle_run_id is None:
        return current
    checked = run_identity.check_run_id(lifecycle_run_id)
    if current.lifecycle_linked and current.lifecycle_run_id != checked:
        raise TerminalState(
            f'admission {current.parent_run_id!r} 已绑定 lifecycle_run_id='
            f'{current.lifecycle_run_id!r}，拒绝覆盖为 {checked!r}')
    if current.lifecycle_run_id == checked:
        return current
    return replace(current, lifecycle_run_id=checked)


def _parent_beacon_events(
        events: list[ProgressEvent], parent_run_id: str,
) -> tuple[ProgressEvent, ...]:
    return tuple(event for event in events if event.run_id == parent_run_id)


def _validate_reconcile_seal(
    current: AdmissionSnapshot, admission_events: list[CommanderAdmissionEvent],
    beacon_events: tuple[ProgressEvent, ...],
) -> AdmissionSnapshot:
    """全量父 beacon/admission 事实漂移后，旧人工 seal 立即失效。"""
    if current.terminal_kind != 'reconciled':
        return current
    actual = _reconcile_subject_of(
        current.parent_run_id, current, admission_events, beacon_events)
    if actual == current.terminal_subject_sha256:
        return current
    return replace(
        current, terminal_kind=None, terminal_subject_sha256=None,
        terminal_actor=None, terminal_reason=None, terminal_force=None,
        reconcile_invalidated=True)


def snapshot(base: str | os.PathLike[str], parent_run_id: str,
             ) -> AdmissionSnapshot:
    from novel.orchestrator import beacon

    checked = run_identity.check_run_id(parent_run_id)
    path = admission_path(base)
    # 与 reconcile apply 同一固定锁序，避免把两个不同时刻拼成一张 seal 快照。
    with append_transaction(path):
        with append_transaction(beacon.beacon_path(base)):
            events = _read_events(path)
            current = _fold(events, checked)
            beacon_events = _parent_beacon_events(
                beacon.read_events(base), current.lifecycle_run_id)
            return _validate_reconcile_seal(
                current, events, beacon_events)


def unresolved(base: str | os.PathLike[str], sid: str | None = None,
               ) -> tuple[AdmissionSnapshot, ...]:
    """返回重启后也必须继续挡住新写手的 gate。

    只有明确 ``drained`` 或主体仍匹配全量父 beacon 生命周期的 ``reconciled``
    才算真正闭合；一次空快照、单独 close、claim 已 release，或已被迟到事实
    反证的 reconcile 都不够。``sid=None`` 给摄入的全局冲突面使用，因为上传尚
    不能知道最终会与哪本书争目录。
    """
    from novel.orchestrator import beacon

    path = admission_path(base)
    with append_transaction(path):
        with append_transaction(beacon.beacon_path(base)):
            events = _read_events(path)
            all_beacons = beacon.read_events(base)
            order: list[str] = []
            for event in events:
                if event.parent_run_id not in order:
                    order.append(event.parent_run_id)
            out: list[AdmissionSnapshot] = []
            for parent_run_id in order:
                current = _fold(events, parent_run_id)
                current = _validate_reconcile_seal(
                    current, events,
                    _parent_beacon_events(
                        all_beacons, current.lifecycle_run_id))
                if sid is not None and current.sid != sid:
                    continue
                if current.opened and current.terminal_kind is None:
                    out.append(current)
            return tuple(out)


def _append_locked(path: Path, event: CommanderAdmissionEvent) -> None:
    append_line_under_process_lock(
        path,
        json.dumps(event.model_dump(mode='json'), ensure_ascii=False,
                   sort_keys=True, separators=(',', ':')),
    )


def _require_appendable(path: Path) -> None:
    """坏尾只可读为未提交，绝不把下一行接到它后面。调用方已持事务锁。"""
    append_tail_repair.require_appendable(
        path, label='Commander admission 账')


def tail_repair_dir(base: str | os.PathLike[str]) -> Path:
    return service_dir(base) / TAIL_REPAIR_DIRNAME


def _tail_repair_spec(base: str | os.PathLike[str]
                      ) -> append_tail_repair.TailRepairSpec:
    return append_tail_repair.TailRepairSpec(
        protocol='novel-commander-admission-tail-repair/v1',
        log_filename=ADMISSION_FILENAME,
        filename_field='admission_filename',
        label='Commander admission 账',
        repair_dir=tail_repair_dir(base),
        record_type=CommanderAdmissionTailRepairRecord,
    )


def _tail_subject_from_hashes(
    base: str | os.PathLike[str], *, committed_size: int,
    committed_sha256: str, tail_size: int, tail_sha256: str,
) -> AdmissionTailRepairSubject:
    return append_tail_repair.subject_from_hashes(
        _tail_repair_spec(base), committed_size=committed_size,
        committed_sha256=committed_sha256, tail_size=tail_size,
        tail_sha256=tail_sha256)


def _tail_material(
    base: str | os.PathLike[str], raw: bytes,
) -> tuple[AdmissionTailRepairSubject, bytes, bytes]:
    return append_tail_repair.tail_material(_tail_repair_spec(base), raw)


def _repair_record_path(
    base: str | os.PathLike[str], subject_sha256: str, kind: str,
) -> Path:
    return append_tail_repair.repair_record_path(
        _tail_repair_spec(base), subject_sha256, kind)


def read_tail_repair_records(
        base: str | os.PathLike[str],
) -> tuple[CommanderAdmissionTailRepairRecord, ...]:
    return append_tail_repair.read_records(_tail_repair_spec(base))


def _fsync_directory(path: Path) -> None:
    """尽力把原子 rename 的目录项刷盘；Windows 不支持目录 fd 时由文件 fsync 兜底。"""
    append_tail_repair.fsync_directory(path)


def _write_immutable(path: Path, payload: bytes) -> None:
    """同内容重试幂等；既有文件任何字节漂移都关闭式拒绝。"""
    append_tail_repair.write_immutable(
        path, payload, label='Commander admission 账')


def _record_matches_subject(
    record: CommanderAdmissionTailRepairRecord,
    subject: AdmissionTailRepairSubject,
) -> bool:
    return append_tail_repair.record_matches_subject(record, subject)


def _write_repair_record(
    base: str | os.PathLike[str], subject: AdmissionTailRepairSubject, *,
    kind: str, actor: str, reason: str,
) -> CommanderAdmissionTailRepairRecord:
    return append_tail_repair.write_record(
        _tail_repair_spec(base), subject, kind=kind,
        actor=actor, reason=reason)


def _pending_tail_subject(
    base: str | os.PathLike[str], raw: bytes,
) -> AdmissionTailRepairSubject | None:
    return append_tail_repair.pending_subject(_tail_repair_spec(base), raw)


def tail_repair_subject(
        base: str | os.PathLike[str],
) -> AdmissionTailRepairSubject:
    """只读形成坏尾恢复模板；也能续上 truncate 后尚未写 applied 的两相事务。"""
    return append_tail_repair.repair_subject(
        admission_path(base), _tail_repair_spec(base))


def repair_tail(
    base: str | os.PathLike[str], *, actor: str, reason: str,
    subject_sha256: str,
) -> AdmissionTailRepairSubject:
    """隔离原尾、fsync 截回 committed prefix，并留下 immutable 两相另账。"""
    # agent 必须先拿 owner lease、再尝试 open admission；因此这里先锁住
    # 所有已出现的 owner，再进入公共 repair 的 admission 事务即可封住 live
    # owner。扫描后才来的 agent 只会在 repair 之后 open，尚未启动 Commander。
    with _require_all_owners_stopped(base):
        return append_tail_repair.repair_tail(
            admission_path(base), _tail_repair_spec(base), actor=actor,
            reason=reason, subject_sha256=subject_sha256,
            # 保留这道 wrapper，让 admission 的两相 kill/retry 测试仍能在
            # applied 之前注入掉电；真正字节语义由公共模块唯一拥有。
            record_writer=lambda subject, **kwargs: _write_repair_record(
                base, subject, **kwargs),
        )


def open_gate(
        base: str | os.PathLike[str], parent_run_id: str, sid: str, *,
        lifecycle_run_id: str | None = None,
) -> AdmissionSnapshot:
    checked = run_identity.check_run_id(parent_run_id)
    checked_lifecycle = run_identity.check_run_id(
        lifecycle_run_id or checked)
    if not isinstance(sid, str) or not sid:
        raise ValueError('admission sid 不得为空')
    path = admission_path(base)
    with append_transaction(path):
        _require_appendable(path)
        current = _fold(_read_events(path), checked)
        if current.opened:
            raise TerminalState(f'parent run {checked!r} 已建立 admission gate')
        _append_locked(path, CommanderAdmissionEvent(
            wire_version=2, parent_run_id=checked,
            lifecycle_run_id=checked_lifecycle,
            sid=sid, kind='opened', ts=int(time.time())))
        return AdmissionSnapshot(
            parent_run_id=checked, lifecycle_run_id=checked_lifecycle,
            lifecycle_linked=True, sid=sid, opened=True, closed=False,
            active_claims=(), terminal_kind=None)


def close_gate(
        base: str | os.PathLike[str], parent_run_id: str, *,
        lifecycle_run_id: str | None = None,
) -> AdmissionSnapshot:
    """原子关闭新 claim；已经 active 的 claim 继续保留到 release。"""
    checked = run_identity.check_run_id(parent_run_id)
    path = admission_path(base)
    with append_transaction(path):
        _require_appendable(path)
        current = _with_reconcile_lifecycle(
            _fold(_read_events(path), checked), lifecycle_run_id)
        if not current.opened:
            raise TerminalState(f'parent run {checked!r} 尚未建立 admission gate')
        if current.closed:
            return current                         # 重试幂等，不追加第二条 close
        _append_locked(path, CommanderAdmissionEvent(
            wire_version=2, parent_run_id=checked,
            lifecycle_run_id=current.lifecycle_run_id, sid=current.sid,
            kind='closed', ts=int(time.time())))
        return AdmissionSnapshot(
            parent_run_id=checked,
            lifecycle_run_id=current.lifecycle_run_id,
            lifecycle_linked=True,
            sid=current.sid, opened=True, closed=True,
            active_claims=current.active_claims,
            terminal_kind=current.terminal_kind)


def claim(base: str | os.PathLike[str], parent_run_id: str, sid: str, *,
          node: str, claim_sha256: str,
          claim_id: str | None = None) -> AdmissionClaim:
    """在 close 的同一事务口原子取得一次完整 Step 序列的 admission。"""
    checked = run_identity.check_run_id(parent_run_id)
    checked_claim = run_identity.check_run_id(
        claim_id or run_identity.new_run_id())
    proposed = AdmissionClaim(
        parent_run_id=checked, sid=sid, claim_id=checked_claim,
        node=node, claim_sha256=claim_sha256)
    path = admission_path(base)
    with append_transaction(path):
        _require_appendable(path)
        current = _fold(_read_events(path), checked)
        if not current.opened:
            raise TerminalState(
                f'Commander admission {checked!r} 不存在；不得执行节点')
        if current.sid != sid:
            raise TerminalState(
                f'Commander admission 属于 sid={current.sid!r}，不能 claim sid={sid!r}')
        if current.closed:
            raise TerminalState(
                'Commander 顶层 invocation 已关闭 admission；'
                '迟到的 run_step 不得再启动')
        if any(item.claim_id == checked_claim for item in current.active_claims):
            raise TerminalState(f'claim_id {checked_claim!r} 已在使用')
        event = CommanderAdmissionEvent(
            wire_version=2, parent_run_id=checked,
            lifecycle_run_id=current.lifecycle_run_id,
            sid=sid, kind='claimed',
            ts=int(time.time()), claim_id=checked_claim,
            node=node, claim_sha256=claim_sha256)
        _append_locked(path, event)
    return proposed


def release(base: str | os.PathLike[str], claim: AdmissionClaim) -> None:
    """精确释放 claim；载荷漂移、缺 claim 或重复 release 均拒绝猜测。"""
    path = admission_path(base)
    with append_transaction(path):
        _require_appendable(path)
        current = _fold(_read_events(path), claim.parent_run_id)
        matches = [item for item in current.active_claims
                   if item.claim_id == claim.claim_id]
        if len(matches) != 1:
            raise TerminalState(
                f'claim_id {claim.claim_id!r} 不是唯一 active claim，拒绝 release')
        if matches[0] != claim:
            raise TerminalState(f'claim_id {claim.claim_id!r} 的 release 载荷漂移')
        _append_locked(path, CommanderAdmissionEvent(
            wire_version=2, parent_run_id=claim.parent_run_id,
            lifecycle_run_id=current.lifecycle_run_id, sid=claim.sid,
            kind='released', ts=int(time.time()), claim_id=claim.claim_id,
            node=claim.node, claim_sha256=claim.claim_sha256))


def mark_drained(base: str | os.PathLike[str], parent_run_id: str,
                 ) -> AdmissionSnapshot:
    """父 worker 在 claim + beacon 双面归零后写下正常生命周期终态。"""
    from novel.orchestrator import beacon

    checked = run_identity.check_run_id(parent_run_id)
    path = admission_path(base)
    with append_transaction(path):
        _require_appendable(path)
        with append_transaction(beacon.beacon_path(base)):
            beacon.require_appendable_under_transaction(base)
            events = _read_events(path)
            current = _fold(events, checked)
            current = _validate_reconcile_seal(
                current, events, _parent_beacon_events(
                    beacon.read_events(base), current.lifecycle_run_id))
            if current.terminal_kind == 'drained':
                return current
            if current.terminal_kind is not None:
                raise TerminalState(
                    f'Commander admission 已由 {current.terminal_kind} 收口')
            if current.reconcile_invalidated:
                raise TerminalState(
                    '人工 reconcile 已被迟到 release/beacon 反证；'
                    '必须重新 typed reconcile，不能洗成正常 drained')
            if not current.closed or current.active_claims:
                raise TerminalState('只有 closed 且 active claims 归零后才能标记 drained')
            opened = beacon.open_executions(base, current.lifecycle_run_id)
            if opened:
                raise TerminalState(
                    f'仍有 {len(opened)} 个 open execution，不能标记 drained')
            _append_locked(path, CommanderAdmissionEvent(
                wire_version=2, parent_run_id=checked,
                lifecycle_run_id=current.lifecycle_run_id, sid=current.sid,
                kind='drained', ts=int(time.time())))
            return AdmissionSnapshot(
                parent_run_id=checked,
                lifecycle_run_id=current.lifecycle_run_id,
                lifecycle_linked=True,
                sid=current.sid, opened=True, closed=True,
                active_claims=(), terminal_kind='drained')


def _reconcile_subject_of(
    checked: str, current: AdmissionSnapshot,
    events: list[CommanderAdmissionEvent],
    beacon_events: tuple[ProgressEvent, ...],
) -> str:
    selected = [event.model_dump(mode='json') for event in events
                if event.parent_run_id == checked
                and event.kind in ('opened', 'claimed', 'released')]
    lifecycle = [event.model_dump(mode='json') for event in beacon_events]
    payload = {
        'protocol': 'novel-commander-admission-reconcile/v3',
        'admission_id': checked,
        'lifecycle_run_id': current.lifecycle_run_id,
        'sid': current.sid,
        'events': selected,
        'beacon_lifecycle': lifecycle,
    }
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def reconcile_subject(
        base: str | os.PathLike[str], parent_run_id: str, *,
        lifecycle_run_id: str | None = None,
) -> str:
    """绑定 gate 历史与外层 lifecycle 全量 beacon 的原子恢复主体。"""
    from novel.orchestrator import beacon

    checked = run_identity.check_run_id(parent_run_id)
    path = admission_path(base)
    # 两账本凡需形成同一恢复主体，一律 admission -> beacon 固定锁序。
    # run_step 的 beacon end / admission release 各自是独立事务，没有反向嵌套。
    with append_transaction(path):
        _require_appendable(path)
        with append_transaction(beacon.beacon_path(base)):
            beacon.require_appendable_under_transaction(base)
            events = _read_events(path)
            current = _with_reconcile_lifecycle(
                _fold(events, checked), lifecycle_run_id)
            if not current.opened:
                raise TerminalState(f'parent run {checked!r} 没有 admission gate')
            all_beacons = beacon.read_events(base)
            # 形成模板时同时关闭式验证 start/end 配对；subject 则保留全生命周期，
            # 因而 reconciled 后哪怕新增一对已经闭合的 start/end 也会反证旧 seal。
            beacon.open_executions(base, current.lifecycle_run_id)
            return _reconcile_subject_of(
                checked, current, events,
                _parent_beacon_events(
                    all_beacons, current.lifecycle_run_id))


def reconcile_gate(
    base: str | os.PathLike[str], parent_run_id: str, *, actor: str,
    reason: str, subject_sha256: str, force: bool = False,
    lifecycle_run_id: str | None = None,
) -> AdmissionSnapshot:
    """显式人工收口服务崩溃遗留 gate；零模型、hash-bound、append-only。"""
    if not actor.strip() or not reason.strip():
        raise TerminalState('admission reconcile 的 actor/reason 不得为空')
    checked = run_identity.check_run_id(parent_run_id)
    path = admission_path(base)
    # v2 直接读 durable link；旧 v1 的 override 必须由上层先以唯一
    # AgentRun epoch_opened 证明。先解析 owner 身份，再持有它直到最终 CAS 返回。
    with append_transaction(path):
        current = _with_reconcile_lifecycle(
            _fold(_read_events(path), checked), lifecycle_run_id)
    if not current.opened:
        raise TerminalState(f'parent run {checked!r} 没有 admission gate')
    # owner absence 不是一次瞬时布尔检查：同一枚 OS lease 一直持到 close、
    # admission+beacon 最终 CAS 和返回快照全部完成，force 也不能绕过 live owner。
    with require_owner_stopped(base, current.lifecycle_run_id):
        expected = reconcile_subject(
            base, checked, lifecycle_run_id=current.lifecycle_run_id)
        if subject_sha256 != expected:
            raise TerminalState(
                f'admission reconcile subject SHA 已变化；current={expected}')
        # 先原子 close，阻止核查期间再产生新 claim。close 不进入 subject，故不改变
        # 上面那枚 hash；release/beacon end 会改变，下面仍会重算一次。
        close_gate(
            base, checked, lifecycle_run_id=current.lifecycle_run_id)
        from novel.orchestrator import beacon
        # apply 的最终判断与 reconciled 追加必须是 admission + beacon 联合 CAS。
        # 固定 admission -> beacon 锁序，避免 release/end 在重算主体后、追加前漂移。
        with append_transaction(path):
            _require_appendable(path)
            with append_transaction(beacon.beacon_path(base)):
                beacon.require_appendable_under_transaction(base)
                events = _read_events(path)
                current = _with_reconcile_lifecycle(
                    _fold(events, checked), lifecycle_run_id)
                all_beacons = beacon.read_events(base)
                parent_beacons = _parent_beacon_events(
                    all_beacons, current.lifecycle_run_id)
                current = _validate_reconcile_seal(
                    current, events, parent_beacons)
                if current.terminal_kind is not None:
                    raise TerminalState(
                        f'admission 已由 {current.terminal_kind} 收口，不重复 reconcile')
                opened = beacon.open_executions(
                    base, current.lifecycle_run_id)
                current_subject = _reconcile_subject_of(
                    checked, current, events, parent_beacons)
                if current_subject != subject_sha256:
                    raise TerminalState(
                        f'admission 核查期间主体变化；current={current_subject}')
                if (current.active_claims or opened) and not force:
                    raise TerminalState(
                        f'admission 仍有 {len(current.active_claims)} 个 active claim / '
                        f'{len(opened)} 个 open execution；默认拒绝 reconcile。'
                        '确认操作系统中相关进程均已停止后，重取 subject 并显式 --force')
                _append_locked(path, CommanderAdmissionEvent(
                    wire_version=2, parent_run_id=checked,
                    lifecycle_run_id=current.lifecycle_run_id,
                    sid=current.sid, kind='reconciled',
                    ts=int(time.time()), actor=actor.strip(), reason=reason.strip(),
                    subject_sha256=subject_sha256, force=bool(force)))
        return snapshot(base, checked)


def env_with_claim(claim: AdmissionClaim,
                   env: Mapping[str, str] | None = None) -> dict[str, str]:
    child = dict(os.environ if env is None else env)
    child[CLAIM_ID_ENV] = run_identity.check_run_id(claim.claim_id)
    return child


def require_active_claim(
    base: str | os.PathLike[str], parent_run_id: str, sid: str, node: str,
    *, env: Mapping[str, str] | None = None,
) -> AdmissionClaim:
    """子 ``novel.cli run`` 重读盘上 gate，拒绝脱离 run_step 的直跑。"""
    source = os.environ if env is None else env
    raw = source.get(CLAIM_ID_ENV)
    if not raw:
        raise TerminalState(
            'Commander context 下的 node CLI 缺 active admission claim；'
            '所有执行必须经 operator run_step.py，不能直接 novel.cli run')
    try:
        claim_id = run_identity.check_run_id(raw)
    except ValueError as exc:
        raise TerminalState('Commander admission claim id 无效') from exc
    current = snapshot(base, parent_run_id)
    if current.reconcile_invalidated:
        raise TerminalState(
            'Commander admission 的人工 reconcile 已被迟到 release/beacon 反证；'
            '旧 claim 不得恢复执行权')
    if current.terminal_kind is not None:
        raise TerminalState(
            f'Commander admission 已由 {current.terminal_kind} 收口；'
            '旧 claim 不得再启动节点')
    matches = [item for item in current.active_claims
               if item.claim_id == claim_id]
    if len(matches) != 1:
        raise TerminalState(
            f'Commander admission claim {claim_id!r} 已释放、未落盘或不唯一；'
            '拒绝启动节点')
    claim = matches[0]
    if claim.sid != sid or claim.node != node:
        raise TerminalState(
            f'Commander admission claim 绑定 {claim.node}/{claim.sid}，'
            f'不能执行 {node}/{sid}')
    return claim


__all__ = [
    'ADMISSION_FILENAME', 'CLAIM_ID_ENV', 'OWNER_LEASE_DIRNAME',
    'TAIL_REPAIR_DIRNAME',
    'AdmissionClaim', 'AdmissionSnapshot', 'AdmissionTailRepairSubject',
    'admission_path', 'claim', 'close_gate',
    'env_with_claim', 'mark_drained', 'open_gate', 'owner_lease',
    'owner_lease_path', 'read_events',
    'read_tail_repair_records', 'reconcile_gate', 'reconcile_subject',
    'release', 'repair_tail', 'require_active_claim', 'require_owner_stopped',
    'snapshot',
    'tail_repair_dir', 'tail_repair_subject', 'unresolved',
]
