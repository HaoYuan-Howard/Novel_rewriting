#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Commander / operator 本地阶段遥测。

    <base>/_service/operator_phases/<session_id>.jsonl

这份账只回答「operator 在哪一类本地阶段、持续多久、是否完整结束」。它刻意
不并进三份相邻载体：

* ``agent_progress.jsonl`` 的身份是 ``NODES`` 命令及退出码；人工审阅、终审同步
  不是流水线节点，塞进去会污染完成度；
* ``ledger.jsonl`` 的一行等于一次真实模型调用或缓存事实；阶段事件没有 token、
  也不应被成本聚合器误数成 wire；
* ``agent_sessions.jsonl`` 记录 provider logical session / turn。operator 的本地
  阅读、预览和终审动作不拥有 provider session，不能伪造一轮模型调用。

【可观测性边界】直接交互式 Commander 的内部推理与客户端排队不在仓库进程内，
本账只能记录脚本/工具明确包住的阶段。未打点的间隔仍是 ``unattributed``，不能
改名为「模型思考」。同理，本账没有 model、usage、token 或价格字段。

【隐私边界】事件只接收固定枚举、ASCII action/error kind、run id、代码指纹与
不可逆 SHA-256；没有 prompt、正文、人工理由、argv、stdout 或绝对路径槽位。

事件流 append-only，顺序以文件序为准。``ts_ms`` 用于跨进程时间线；若同一进程
有单调时钟读数，可把 ``measured_elapsed_ms`` 传给 ``finish_phase``，否则明确记成
``wall_clock``。读侧只容忍被 kill 截断的最后一行，中间坏行关闭式失败。
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from contracts.invariants import (
    OPERATOR_DURATION_SOURCES as DURATION_SOURCES,
    OPERATOR_PHASE_EVENT_KINDS as EVENT_KINDS,
    OPERATOR_PHASES,
    OPERATOR_TELEMETRY_SOURCES as OPERATOR_SOURCES,
    OPERATOR_TERMINAL_STATES as TERMINAL_STATES,
)
from contracts.schema.orchestration import OperatorPhaseEvent
from novel import run_identity
from novel.appendlog import append_line_under_process_lock, append_transaction
from novel.paths import service_dir
from novel.orchestrator.write_admission import WriteAdmission, operation_scope


OPERATOR_PHASE_DIRNAME = 'operator_phases'
OPERATOR_PHASE_WIRE_VERSION = 1


def telemetry_dir(base: str | Path) -> Path:
    return service_dir(base) / OPERATOR_PHASE_DIRNAME


def telemetry_path(base: str | Path, session_id: str) -> Path:
    """每个 session 独占一份小型 append log；阶段追加不扫描历史 session。"""
    checked = run_identity.check_run_id(session_id)
    return telemetry_dir(base) / f'{checked}.jsonl'


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _identity(value: str, field: str) -> str:
    try:
        return run_identity.check_run_id(value)
    except Exception as exc:  # noqa: BLE001 —— 对外统一成 telemetry wire 错误
        raise ValueError(f'{field} 不是合法运行身份：{exc}') from exc


@dataclass(frozen=True)
class OperatorPhaseRecord:
    phase_execution_id: str
    phase: str
    action: str | None
    run_id: str | None
    started_at_ms: int
    ended_at_ms: int | None
    state: str | None
    exit_code: int | None
    elapsed_ms: int | None
    duration_source: str | None
    start_evidence_sha256: str | None
    end_evidence_sha256: str | None
    error_kind: str | None


@dataclass(frozen=True)
class OperatorSessionRecord:
    session_id: str
    sid: str
    source: str
    parent_run_id: str | None
    started_at_ms: int
    ended_at_ms: int | None
    state: str | None
    elapsed_ms: int | None
    duration_source: str | None
    start_code_version: str
    end_code_version: str | None
    phases: tuple[OperatorPhaseRecord, ...]


def _decode_lines(path: Path) -> list[OperatorPhaseEvent]:
    if not path.is_file():
        return []
    # 按**原始字节**保留最后一行有没有换行这个事实；``str.splitlines()`` 会把
    # “完整坏行 + 换行”与“被 kill 截断的坏尾”压成同一形状，进而把前者也静默吞掉。
    lines = path.read_bytes().splitlines(keepends=True)
    events: list[OperatorPhaseEvent] = []
    for index, raw_bytes in enumerate(lines):
        complete = raw_bytes.endswith(b'\n')
        if not complete:
            if index == len(lines) - 1:
                # 换行是 append-only 行的提交标记；即便 kill 恰好落在完整 JSON
                # 之后、换行之前，也不能把未提交尾冒充正式事件。
                break
            raise ValueError(
                f'{path} 第 {index + 1} 行不是 LF 提交的完整事件')
        payload_bytes = raw_bytes[:-1] if complete else raw_bytes
        if payload_bytes.endswith(b'\r'):
            payload_bytes = payload_bytes[:-1]
        try:
            raw = payload_bytes.decode('utf-8', errors='strict')
        except UnicodeDecodeError:
            if index == len(lines) - 1 and not complete:
                break
            raise
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            if index == len(lines) - 1 and not complete:
                break
            raise
        if (not isinstance(payload, dict)
                or set(payload) != set(OperatorPhaseEvent.model_fields)):
            raise ValueError(f'{path} 第 {index + 1} 行字段不完整或含未登记字段')
        events.append(OperatorPhaseEvent.model_validate(payload))
    return events


def _decode_session_start(path: Path) -> OperatorPhaseEvent | None:
    """只读每个小账首行，供终审按 sid/started_at 筛选，避免全量历史重放。"""
    if not path.is_file():
        return None
    with open(path, 'rb') as handle:
        raw_bytes = handle.readline()
    complete = raw_bytes.endswith(b'\n')
    if not complete:
        return None
    payload_bytes = raw_bytes[:-1] if complete else raw_bytes
    if payload_bytes.endswith(b'\r'):
        payload_bytes = payload_bytes[:-1]
    try:
        raw = payload_bytes.decode('utf-8', errors='strict')
    except UnicodeDecodeError:
        raise
    if not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # 只有一行且被 kill 截断，等价于尚无可读 session；完整坏首行照常抛。
        raise
    if (not isinstance(payload, dict)
            or set(payload) != set(OperatorPhaseEvent.model_fields)):
        raise ValueError(f'{path} 首行字段不完整或含未登记字段')
    event = OperatorPhaseEvent.model_validate(payload)
    if event.kind != 'session_started':
        raise ValueError(f'{path} 首行不是 session_started')
    return event


def read_events(
        base: str | Path, session_id: str | None = None,
) -> list[OperatorPhaseEvent]:
    """读一只 session，或仅在终审汇总时枚举全部小账。"""
    if session_id is not None:
        return _decode_lines(telemetry_path(base, session_id))
    root = telemetry_dir(base)
    if not root.is_dir():
        return []
    events: list[OperatorPhaseEvent] = []
    for path in sorted(root.glob('*.jsonl')):
        events.extend(_decode_lines(path))
    return events


def _line(event: OperatorPhaseEvent) -> str:
    return json.dumps(
        event.model_dump(mode='json'), ensure_ascii=False,
        sort_keys=True, separators=(',', ':'))


def _fold(events: Sequence[OperatorPhaseEvent]) -> tuple[OperatorSessionRecord, ...]:
    sessions: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for event in events:
        if event.kind == 'session_started':
            if event.session_id in sessions:
                raise ValueError(f'operator session {event.session_id!r} 重复 started')
            sessions[event.session_id] = {
                'start': event, 'end': None, 'phases': {}, 'phase_order': [],
            }
            order.append(event.session_id)
            continue
        slot = sessions.get(event.session_id)
        if slot is None:
            raise ValueError(f'operator event 指向不存在的 session {event.session_id!r}')
        start: OperatorPhaseEvent = slot['start']
        if event.sid != start.sid or event.source != start.source:
            raise ValueError('同一 operator session 的 sid/source 发生漂移')
        if slot['end'] is not None:
            raise ValueError('operator session 已 ended，后面又出现事件')
        if event.kind == 'phase_started':
            if event.ts_ms < start.ts_ms:
                raise ValueError('operator phase_started 早于 session_started')
            assert event.phase_execution_id is not None
            if event.phase_execution_id in slot['phases']:
                raise ValueError(
                    f'phase_execution_id {event.phase_execution_id!r} 重复 started')
            slot['phases'][event.phase_execution_id] = {'start': event, 'end': None}
            slot['phase_order'].append(event.phase_execution_id)
        elif event.kind == 'phase_ended':
            assert event.phase_execution_id is not None
            phase_slot = slot['phases'].get(event.phase_execution_id)
            if phase_slot is None:
                raise ValueError('phase_ended 指向不存在的 phase_started')
            if phase_slot['end'] is not None:
                raise ValueError('同一 operator phase 重复 ended')
            phase_start: OperatorPhaseEvent = phase_slot['start']
            if event.ts_ms < phase_start.ts_ms:
                raise ValueError('operator phase_ended 早于 phase_started')
            if (event.phase, event.action, event.run_id) != (
                    phase_start.phase, phase_start.action, phase_start.run_id):
                raise ValueError('phase_ended 的 phase/action/run_id 与 started 漂移')
            phase_slot['end'] = event
        else:
            ended_phase_times = [
                phase_slot['end'].ts_ms
                for phase_slot in slot['phases'].values()
                if phase_slot['end'] is not None
            ]
            if (event.ts_ms < start.ts_ms
                    or any(event.ts_ms < ended for ended in ended_phase_times)):
                raise ValueError(
                    'operator session_ended 早于 session_started/phase_ended')
            open_phases = [
                phase_id for phase_id, phase_slot in slot['phases'].items()
                if phase_slot['end'] is None
            ]
            if event.state == 'completed' and open_phases:
                raise ValueError('session completed 时仍有未结束 operator phase')
            if (event.state == 'completed'
                    and event.end_code_version != start.start_code_version):
                raise ValueError('代码版本漂移时 operator session 不能声明 completed')
            slot['end'] = event

    records: list[OperatorSessionRecord] = []
    for session_id in order:
        slot = sessions[session_id]
        start = slot['start']
        end = slot['end']
        phases: list[OperatorPhaseRecord] = []
        for phase_id in slot['phase_order']:
            phase_slot = slot['phases'][phase_id]
            phase_start = phase_slot['start']
            phase_end = phase_slot['end']
            phases.append(OperatorPhaseRecord(
                phase_execution_id=phase_id,
                phase=str(phase_start.phase),
                action=phase_start.action,
                run_id=phase_start.run_id,
                started_at_ms=phase_start.ts_ms,
                ended_at_ms=None if phase_end is None else phase_end.ts_ms,
                state=None if phase_end is None else phase_end.state,
                exit_code=None if phase_end is None else phase_end.exit_code,
                elapsed_ms=None if phase_end is None else phase_end.elapsed_ms,
                duration_source=(
                    None if phase_end is None else phase_end.duration_source),
                start_evidence_sha256=phase_start.evidence_sha256,
                end_evidence_sha256=(
                    None if phase_end is None else phase_end.evidence_sha256),
                error_kind=None if phase_end is None else phase_end.error_kind,
            ))
        records.append(OperatorSessionRecord(
            session_id=session_id,
            sid=start.sid,
            source=start.source,
            parent_run_id=start.parent_run_id,
            started_at_ms=start.ts_ms,
            ended_at_ms=None if end is None else end.ts_ms,
            state=None if end is None else end.state,
            elapsed_ms=None if end is None else end.elapsed_ms,
            duration_source=None if end is None else end.duration_source,
            start_code_version=str(start.start_code_version),
            end_code_version=None if end is None else end.end_code_version,
            phases=tuple(phases),
        ))
    records.sort(key=lambda item: (item.started_at_ms, item.session_id))
    return tuple(records)


def fold(events: Sequence[OperatorPhaseEvent]) -> tuple[OperatorSessionRecord, ...]:
    """按文件序折叠；身份漂移、重复结束与假 completed 一律关闭式失败。"""
    return _fold(events)


def _session_start_for_write(
        base: str | Path, session_id: str) -> OperatorPhaseEvent:
    """只读 session 首账确定 sid/parent；事务内仍会完整 fold 后再追加。"""
    path = telemetry_path(base, session_id)
    start = _decode_session_start(path)
    if start is None:
        if path.is_file() and path.stat().st_size:
            raise ValueError(
                'operator telemetry 留有被 kill 截断的无换行 session start；'
                '不能确定写入 sid')
        raise ValueError(
            f'operator session {session_id!r} 不存在，不能确定写入 sid')
    if start.session_id != session_id:
        raise ValueError('operator session 首账身份与请求 session_id 不一致')
    return start


def _require_session_parent(
        session: OperatorPhaseEvent, admitted: WriteAdmission) -> None:
    """Commander 只能续写自己创建的 telemetry session，sid 相同也不能串账。"""
    if (admitted.parent_run_id is not None
            and session.parent_run_id != admitted.parent_run_id):
        raise ValueError(
            'operator session 的 parent_run_id 与当前 Commander frozen context '
            '不一致；拒绝跨会话追加')


def _append_transactional(
        base: str | Path, session_id: str, build: Any,
) -> OperatorPhaseEvent:
    # 每个 session 独占文件：这里的读/折叠规模只随当前 session 的 phase 数增长，
    # 不随历史书数或总运行次数增长，不把 telemetry 变成新的 Commander 冷启动税。
    path = telemetry_path(base, session_id)
    with append_transaction(path):
        # 读侧可以把 kill 留下的**无换行坏尾**解释为 unknown，但写侧绝不能把
        # 下一条 JSON 直接接在那半截后面。这里选择关闭式停止该 session；调用方可
        # 保留它作不完整证据并另开新 session，不静默截账或伪造连续历史。
        if path.is_file() and path.stat().st_size:
            with open(path, 'rb') as handle:
                handle.seek(-1, 2)
                if handle.read(1) != b'\n':
                    raise ValueError(
                        'operator telemetry 留有被 kill 截断的无换行尾；'
                        '拒绝继续拼接，请保留旧 session 并启动新 session')
        current = _decode_lines(path)
        event = build(current)
        # 把“已有账 + 新事件”完整折叠一次再追加，避免把不可闭合事件写到盘上。
        _fold([*current, event])
        append_line_under_process_lock(path, _line(event))
    return event


def start_session(
        base: str | Path, sid: str, *, start_code_version: str,
        source: str = 'interactive', parent_run_id: str | None = None,
        session_id: str | None = None, started_at_ms: int | None = None,
) -> OperatorPhaseEvent:
    session_id = session_id or str(uuid.uuid4())
    if parent_run_id is None:
        # Headless Commander 已把唯一外层身份放进 NOVEL_RUN_ID。skill 的固定
        # session-start 命令不应再手抄一遍；显式参数仍用于交互式恢复/测试。
        parent_run_id = run_identity.inherited_run_id()
    event = OperatorPhaseEvent(
        session_id=session_id, sid=sid, source=source,
        kind='session_started', ts_ms=_now_ms() if started_at_ms is None else started_at_ms,
        parent_run_id=parent_run_id, start_code_version=start_code_version,
    )
    details = {
        'session_id': session_id,
        'start_code_version': start_code_version,
        'source': source,
        'parent_run_id': parent_run_id,
        'started_at_ms': started_at_ms,
    }
    with operation_scope(
            base, sid, operation='operator-session-start',
            details=details) as admitted:
        if (admitted.parent_run_id is not None
                and event.parent_run_id != admitted.parent_run_id):
            raise ValueError(
                '新 operator session 的 parent_run_id 与当前 Commander '
                'frozen context 不一致')
        return _append_transactional(base, session_id, lambda _events: event)


def begin_phase(
        base: str | Path, session_id: str, *, phase: str,
        action: str | None = None, run_id: str | None = None,
        evidence_sha256: str | None = None,
        phase_execution_id: str | None = None, started_at_ms: int | None = None,
) -> OperatorPhaseEvent:
    checked_session = _identity(session_id, 'session_id')
    phase_execution_id = phase_execution_id or str(uuid.uuid4())
    session = _session_start_for_write(base, checked_session)

    def build(events: list[OperatorPhaseEvent]) -> OperatorPhaseEvent:
        records = {record.session_id: record for record in _fold(events)}
        record = records.get(checked_session)
        if record is None:
            raise ValueError(f'找不到 operator session {checked_session!r}')
        if (record.sid, record.parent_run_id) != (
                session.sid, session.parent_run_id):
            raise ValueError('operator session 的 sid/parent 在 admission 后发生漂移')
        if record.ended_at_ms is not None:
            raise ValueError('operator session 已结束，不能再开始 phase')
        return OperatorPhaseEvent(
            session_id=checked_session, sid=record.sid, source=record.source,
            kind='phase_started',
            ts_ms=_now_ms() if started_at_ms is None else started_at_ms,
            phase_execution_id=phase_execution_id, phase=phase, action=action,
            run_id=run_id, evidence_sha256=evidence_sha256,
        )

    details = {
        'session_id': checked_session,
        'phase_execution_id': phase_execution_id,
        'phase': phase,
        'action': action,
        'run_id': run_id,
        'evidence_sha256': evidence_sha256,
        'started_at_ms': started_at_ms,
    }
    with operation_scope(
            base, session.sid, operation='operator-phase-start',
            details=details) as admitted:
        _require_session_parent(session, admitted)
        return _append_transactional(base, checked_session, build)


def finish_phase(
        base: str | Path, session_id: str, phase_execution_id: str, *,
        state: str, exit_code: int | None = None,
        evidence_sha256: str | None = None, error_kind: str | None = None,
        ended_at_ms: int | None = None,
        measured_elapsed_ms: int | None = None,
) -> OperatorPhaseEvent:
    checked_session = _identity(session_id, 'session_id')
    checked_phase = _identity(phase_execution_id, 'phase_execution_id')
    session = _session_start_for_write(base, checked_session)

    def build(events: list[OperatorPhaseEvent]) -> OperatorPhaseEvent:
        records = {record.session_id: record for record in _fold(events)}
        record = records.get(checked_session)
        if record is None:
            raise ValueError(f'找不到 operator session {checked_session!r}')
        if (record.sid, record.parent_run_id) != (
                session.sid, session.parent_run_id):
            raise ValueError('operator session 的 sid/parent 在 admission 后发生漂移')
        matches = [
            item for item in record.phases
            if item.phase_execution_id == checked_phase
        ]
        if len(matches) != 1:
            raise ValueError('phase_execution_id 不存在或不唯一')
        phase_record = matches[0]
        if phase_record.ended_at_ms is not None:
            raise ValueError('operator phase 已结束，不能重复结束')
        ts_ms = _now_ms() if ended_at_ms is None else ended_at_ms
        if ts_ms < phase_record.started_at_ms:
            raise ValueError('phase ended_at 不能早于 started_at')
        if measured_elapsed_ms is None:
            elapsed_ms = ts_ms - phase_record.started_at_ms
            duration_source = 'wall_clock'
        else:
            elapsed_ms = measured_elapsed_ms
            duration_source = 'monotonic'
        return OperatorPhaseEvent(
            session_id=checked_session, sid=record.sid, source=record.source,
            kind='phase_ended', ts_ms=ts_ms,
            phase_execution_id=checked_phase, phase=phase_record.phase,
            action=phase_record.action, run_id=phase_record.run_id,
            state=state, exit_code=exit_code, elapsed_ms=elapsed_ms,
            duration_source=duration_source,
            evidence_sha256=evidence_sha256, error_kind=error_kind,
        )

    details = {
        'session_id': checked_session,
        'phase_execution_id': checked_phase,
        'state': state,
        'exit_code': exit_code,
        'evidence_sha256': evidence_sha256,
        'error_kind': error_kind,
        'ended_at_ms': ended_at_ms,
        'measured_elapsed_ms': measured_elapsed_ms,
    }
    with operation_scope(
            base, session.sid, operation='operator-phase-end',
            details=details) as admitted:
        _require_session_parent(session, admitted)
        return _append_transactional(base, checked_session, build)


def finish_session(
        base: str | Path, session_id: str, *, state: str,
        end_code_version: str, ended_at_ms: int | None = None,
) -> OperatorPhaseEvent:
    checked_session = _identity(session_id, 'session_id')
    session = _session_start_for_write(base, checked_session)

    def build(events: list[OperatorPhaseEvent]) -> OperatorPhaseEvent:
        records = {record.session_id: record for record in _fold(events)}
        record = records.get(checked_session)
        if record is None:
            raise ValueError(f'找不到 operator session {checked_session!r}')
        if (record.sid, record.parent_run_id) != (
                session.sid, session.parent_run_id):
            raise ValueError('operator session 的 sid/parent 在 admission 后发生漂移')
        if record.ended_at_ms is not None:
            raise ValueError('operator session 已结束，不能重复结束')
        ts_ms = _now_ms() if ended_at_ms is None else ended_at_ms
        if ts_ms < record.started_at_ms:
            raise ValueError('session ended_at 不能早于 started_at')
        return OperatorPhaseEvent(
            session_id=checked_session, sid=record.sid, source=record.source,
            kind='session_ended', ts_ms=ts_ms, state=state,
            elapsed_ms=ts_ms - record.started_at_ms,
            duration_source='wall_clock', end_code_version=end_code_version,
        )

    details = {
        'session_id': checked_session,
        'state': state,
        'end_code_version': end_code_version,
        'ended_at_ms': ended_at_ms,
    }
    with operation_scope(
            base, session.sid, operation='operator-session-end',
            details=details) as admitted:
        _require_session_parent(session, admitted)
        return _append_transactional(base, checked_session, build)


def summarize(
        base: str | Path, sid: str, *, started_at: int | None = None,
        session_id: str | None = None,
) -> dict[str, Any]:
    """返回终审可直接消费的无正文阶段事实；``started_at`` 是 epoch 秒。"""
    threshold_ms = None if started_at is None else int(started_at) * 1000
    if session_id is not None:
        checked = _identity(session_id, 'session_id')
        selected_events = read_events(base, checked)
    else:
        selected_events: list[OperatorPhaseEvent] = []
        root = telemetry_dir(base)
        for path in (sorted(root.glob('*.jsonl')) if root.is_dir() else ()):
            start = _decode_session_start(path)
            if (start is not None and start.sid == sid
                    and (threshold_ms is None or start.ts_ms >= threshold_ms)):
                selected_events.extend(_decode_lines(path))
    sessions = [record for record in fold(selected_events) if record.sid == sid]
    if threshold_ms is not None:
        sessions = [record for record in sessions if record.started_at_ms >= threshold_ms]

    session_rows: list[dict[str, Any]] = []
    phase_count = completed_count = unfinished_count = measured_sum = 0
    attributed_total = unattributed_total = 0
    # 没有 session 是「未打点」，不是 0ms；只有至少一份完整 session 才能给总分解。
    attribution_complete = bool(sessions)
    for session in sessions:
        phase_rows: list[dict[str, Any]] = []
        for phase in session.phases:
            phase_count += 1
            if phase.ended_at_ms is None:
                unfinished_count += 1
            else:
                completed_count += 1
                measured_sum += int(phase.elapsed_ms or 0)
            phase_rows.append(asdict(phase))
        attributed_wall_ms: int | None
        unattributed_wall_ms: int | None
        if (session.ended_at_ms is None
                or any(phase.ended_at_ms is None for phase in session.phases)):
            attributed_wall_ms = unattributed_wall_ms = None
            attribution_complete = False
        else:
            intervals = sorted(
                (phase.started_at_ms, int(phase.ended_at_ms))
                for phase in session.phases)
            attributed_wall_ms = 0
            if intervals:
                union_start, union_end = intervals[0]
                for start_ms, end_ms in intervals[1:]:
                    if start_ms <= union_end:
                        union_end = max(union_end, end_ms)
                    else:
                        attributed_wall_ms += union_end - union_start
                        union_start, union_end = start_ms, end_ms
                attributed_wall_ms += union_end - union_start
            session_wall_ms = session.ended_at_ms - session.started_at_ms
            unattributed_wall_ms = session_wall_ms - attributed_wall_ms
            attributed_total += attributed_wall_ms
            unattributed_total += unattributed_wall_ms
        session_rows.append({
            'session_id': session.session_id,
            'source': session.source,
            'parent_run_id': session.parent_run_id,
            'started_at_ms': session.started_at_ms,
            'ended_at_ms': session.ended_at_ms,
            'state': session.state,
            'elapsed_ms': session.elapsed_ms,
            'duration_source': session.duration_source,
            'start_code_version': session.start_code_version,
            'end_code_version': session.end_code_version,
            'code_version_match': (
                None if session.end_code_version is None
                else session.end_code_version == session.start_code_version),
            # 墙钟分解只用 phase 时间区间并集；不用可能重叠的 measured sum。
            'attributed_wall_ms': attributed_wall_ms,
            'unattributed_wall_ms': unattributed_wall_ms,
            'phases': phase_rows,
        })
    return {
        'schema': 'novel-operator-phase-summary-v1',
        'sid': sid,
        'started_at': started_at,
        'session_count': len(session_rows),
        'phase_count': phase_count,
        'completed_phase_count': completed_count,
        'unfinished_phase_count': unfinished_count,
        # 可能有重叠，只叫 sum，不冒充 session wall。
        'measured_phase_elapsed_ms_sum': measured_sum,
        # 任一 session/phase 未结束时总分解未知，不用部分值冒充完整账。
        'attributed_wall_ms': (
            attributed_total if attribution_complete else None),
        'unattributed_wall_ms': (
            unattributed_total if attribution_complete else None),
        # Commander 宿主内部的模型 usage 不经过仓库进程；本账不伪造 token/价格。
        'model_usage_state': 'unavailable_host_boundary',
        'sessions': session_rows,
    }


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def cli_main(argv: Sequence[str] | None = None) -> int:
    """供 operator skill 薄脚本调用；只写本账或只读 summary。"""
    parser = argparse.ArgumentParser(description='Commander/operator 阶段遥测')
    sub = parser.add_subparsers(dest='command', required=True)

    start = sub.add_parser('session-start')
    start.add_argument('--base', required=True)
    start.add_argument('--sid', required=True)
    start.add_argument('--start-code-version', required=True)
    start.add_argument('--started-at', type=int, help='session_preflight 的 epoch 秒')
    start.add_argument('--source', choices=OPERATOR_SOURCES, default='interactive')
    start.add_argument('--parent-run-id')

    phase_start = sub.add_parser('phase-start')
    phase_start.add_argument('--base', required=True)
    phase_start.add_argument('--session-id', required=True)
    phase_start.add_argument('--phase', choices=OPERATOR_PHASES, required=True)
    phase_start.add_argument('--action')
    phase_start.add_argument('--run-id')
    phase_start.add_argument('--evidence-sha256')

    phase_end = sub.add_parser('phase-end')
    phase_end.add_argument('--base', required=True)
    phase_end.add_argument('--session-id', required=True)
    phase_end.add_argument('--phase-execution-id', required=True)
    phase_end.add_argument('--state', choices=TERMINAL_STATES, required=True)
    phase_end.add_argument('--exit', dest='exit_code', type=int)
    phase_end.add_argument('--evidence-sha256')
    phase_end.add_argument('--error-kind')

    session_end = sub.add_parser('session-end')
    session_end.add_argument('--base', required=True)
    session_end.add_argument('--session-id', required=True)
    session_end.add_argument('--state', choices=TERMINAL_STATES, required=True)
    session_end.add_argument('--end-code-version', required=True)

    summary = sub.add_parser('summary')
    summary.add_argument('--base', required=True)
    summary.add_argument('--sid', required=True)
    summary.add_argument('--started-at', type=int)
    summary.add_argument('--session-id')

    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == 'session-start':
            event = start_session(
                args.base, args.sid, start_code_version=args.start_code_version,
                source=args.source, parent_run_id=args.parent_run_id,
                started_at_ms=(
                    None if args.started_at is None else args.started_at * 1000),
            )
            _print(event.model_dump(mode='json'))
        elif args.command == 'phase-start':
            event = begin_phase(
                args.base, args.session_id, phase=args.phase, action=args.action,
                run_id=args.run_id, evidence_sha256=args.evidence_sha256,
            )
            _print(event.model_dump(mode='json'))
        elif args.command == 'phase-end':
            event = finish_phase(
                args.base, args.session_id, args.phase_execution_id,
                state=args.state, exit_code=args.exit_code,
                evidence_sha256=args.evidence_sha256,
                error_kind=args.error_kind,
            )
            _print(event.model_dump(mode='json'))
        elif args.command == 'session-end':
            event = finish_session(
                args.base, args.session_id, state=args.state,
                end_code_version=args.end_code_version,
            )
            _print(event.model_dump(mode='json'))
        else:
            _print(summarize(
                args.base, args.sid, started_at=args.started_at,
                session_id=args.session_id))
    except (OSError, ValueError) as exc:
        _print({
            'schema': 'novel-operator-phase-error-v1',
            'state': 'stopped', 'error_kind': type(exc).__name__.lower(),
        })
        return 2
    return 0


__all__ = [
    'DURATION_SOURCES', 'EVENT_KINDS', 'OPERATOR_PHASES',
    'OPERATOR_PHASE_DIRNAME', 'OPERATOR_PHASE_WIRE_VERSION',
    'OPERATOR_SOURCES', 'OperatorPhaseEvent', 'OperatorPhaseRecord',
    'OperatorSessionRecord', 'TERMINAL_STATES', 'begin_phase', 'cli_main',
    'finish_phase', 'finish_session', 'fold', 'read_events', 'start_session',
    'summarize', 'telemetry_dir', 'telemetry_path',
]
