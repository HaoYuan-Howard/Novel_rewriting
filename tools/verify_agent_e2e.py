#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent-only 整书验收器。纯读、零模型、关闭式。

这个工具只把生产侧已经存在的结论和身份联结到一起：

* 完成度、七层 check 与 inbox 来自同一份 operator snapshot；
* Commander admission 已关闭，且 active claim / open execution 两面归零；
* 交付结论调用 ``delivery.assess``，不重算第二张绿灯；
* 产物版本与稳定性读数复用 ``tools/workflow_metrics.py``；
* 审批只读当前 proof 折叠后的公开投影；
* 成本账、session turn 与 artifact adoption 只按 ``attempt_id``
  逐项联结，不用默认模型、进程退出码或文件存在性补猜。

``UNVER`` 是披露，不会被算成绿灯，也不会被本工具自动改判为
失败。真正的质量结论仍只在各层 ``CheckReport`` 与 hash-bound 人工
回执里。任何缺失、多重身份或读期间漂移都不倒向 pass。

用法：

  python -X utf8 tools/verify_agent_e2e.py \
      --base <base> --sid <sid> --run-id <commander-run-id>

输出只含 sid、run id、注册 id 与数字，不含正文、人名、书名或绝对路径。
退出码：0=pass，1=fail，2=unknown。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts.invariants import (                                  # noqa: E402
    AGENT_EXECUTOR_NODES, APPROVAL_GATES, APPROVAL_SEEN,
    COMMANDER_MAX_ATTEMPTS_PER_SEAT, STAGES,
)
from novel import (                                                  # noqa: E402
    agent_session_audit, operator_telemetry, prov,
)
from novel.llm.ledger import (                                       # noqa: E402
    audit_path, ledger_path, read_jsonl_rows,
)
from novel.agent_model_identity import (                             # noqa: E402
    resolve_agent_model_identity, resolve_ledger_model_identity,
)
from novel.errors import AgentModelIdentityRefused                    # noqa: E402
from novel.orchestrator import (                                    # noqa: E402
    admission, agent, approvals, beacon, delivery, queue,
)
from novel.orchestrator.inspection import (                          # noqa: E402
    capture_operator_snapshot, read_surface_seal,
)
from novel.views.inbox import inbox_from_snapshot                    # noqa: E402
from tools import workflow_metrics                                   # noqa: E402


CHECK_STATES = ('pass', 'fail', 'unknown')
READY_OUTCOMES = ('ready', 'ready_with_disclosures')
REPO_ROOT = Path(__file__).resolve().parent.parent


def _check(check_id: str, state: str, **facts: Any) -> dict[str, Any]:
    if state not in CHECK_STATES:
        raise ValueError(f'验收项 {check_id} 含未登记状态 {state!r}')
    return {'id': check_id, 'state': state, 'facts': facts}


def _overall(checks: Sequence[Mapping[str, Any]]) -> str:
    states = [str(row.get('state')) for row in checks]
    if any(state == 'fail' for state in states):
        return 'fail'
    if any(state != 'pass' for state in states):
        return 'unknown'
    return 'pass'


def _service_seal(base: str | Path) -> str:
    """验收期间会读到的 append-only 服务账字节封印。

    operator surface seal 已覆盖书内产物、审批、ledger 与进度；
    这里只补它不消费的 agent/session/adoption/operator 服务账。
    直接哈希字节是为了发现混合时点，不解释半行语义。
    """
    root = Path(base)
    paths = [
        admission.admission_path(root),
        agent.runs_path(root),
        agent_session_audit.audit_path(root),
        audit_path(root),
    ]
    telemetry_root = operator_telemetry.telemetry_dir(root)
    if telemetry_root.is_dir():
        paths.extend(sorted(telemetry_root.glob('*.jsonl')))
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: str(item).casefold()):
        digest.update(path.name.encode('utf-8'))
        digest.update(b'\0')
        if path.is_file():
            digest.update(path.read_bytes())
        digest.update(b'\n')
    return digest.hexdigest()


def execution_lifecycle_check(
    base: str | Path, run_id: str, sid: str,
) -> dict[str, Any]:
    """证明父 run 已关 admission，且 claim / node execution 两面都归零。"""
    try:
        gate = admission.snapshot(base, run_id)
        opened = beacon.open_executions(base, run_id)
    except Exception as exc:  # noqa: BLE001 —— 坏账不能压成“没有在途执行”
        return _check(
            'commander_execution_lifecycle', 'unknown',
            error_type=type(exc).__name__)
    passed = (
        gate.opened
        and gate.closed
        and gate.terminal_kind == 'drained'
        and gate.sid == sid
        and not gate.active_claims
        and not opened
    )
    return _check(
        'commander_execution_lifecycle', 'pass' if passed else 'fail',
        gate_opened=gate.opened,
        gate_closed=gate.closed,
        terminal_kind=gate.terminal_kind,
        sid_matches=gate.sid == sid,
        active_claims=len(gate.active_claims),
        open_executions=len(opened),
    )


def _one_outer_run(base: str | Path, run_id: str) -> tuple[Any | None, dict[str, Any]]:
    matches = [
        row for row in agent.fold(agent.read_events(base), live=set())
        if row.run_id == run_id
    ]
    if len(matches) != 1:
        return None, _check(
            'outer_run', 'unknown', matching_records=len(matches))
    row = matches[0]
    profiles = row.start.profiles
    identity = row.epochs[-1] if row.epochs else row.start
    identity_current = (
        identity.identity_version == 2 and identity.skill_sha256 is None
        and identity.code_version not in (None, prov.CODE_UNKNOWN)
        and identity.code_version == prov.code_version())
    facts = {
        'process_state': row.state,
        'exit_code': row.exit_code,
        'delivery_state': None if row.outcome is None else row.outcome.state,
        'authorized': row.start.authorized,
        'egress_authorized': row.start.egress_authorized,
        'profiles_frozen': profiles is not None,
        'start_code_version': row.start.code_version,
        'identity_version': identity.identity_version,
        'runtime_identity_current': identity_current,
    }
    passed = (
        row.state == 'ok'
        and row.exit_code == 0
        and row.outcome is not None
        and row.outcome.state in READY_OUTCOMES
        and row.start.authorized
        and row.start.egress_authorized
        and profiles is not None
        and row.start.code_version not in (None, prov.CODE_UNKNOWN)
        and identity_current
    )
    state = ('pass' if passed else 'fail')
    return row, _check('outer_run', state, **facts)


def _session_index(
    records: Iterable[agent_session_audit.AgentSessionRecord],
) -> dict[str, tuple[agent_session_audit.AgentSessionRecord,
                     agent_session_audit.AgentSessionEvent]]:
    result: dict[
        str, tuple[agent_session_audit.AgentSessionRecord,
                   agent_session_audit.AgentSessionEvent]
    ] = {}
    duplicated: set[str] = set()
    for record in records:
        for turn in record.turns:
            if turn.attempt_id is None:
                continue
            if turn.attempt_id in result:
                duplicated.add(turn.attempt_id)
            result[turn.attempt_id] = (record, turn)
    if duplicated:
        raise ValueError(
            f'session audit attempt_id 重复 {len(duplicated)} 条')
    return result


def _attempt_index(rows: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    duplicated: set[str] = set()
    for row in rows:
        attempt_id = row.get('attempt_id')
        if not isinstance(attempt_id, str) or not attempt_id:
            continue
        if attempt_id in result:
            duplicated.add(attempt_id)
        result[attempt_id] = row
    if duplicated:
        raise ValueError(f'ledger attempt_id 重复 {len(duplicated)} 条')
    return result


def _same_attempt_identity(
    ledger_row: Mapping[str, Any], adoption: Mapping[str, Any],
) -> bool:
    return all(ledger_row.get(name) == adoption.get(name) for name in (
        'run_id', 'sid', 'node', 'purpose', 'attempt_id', 'chapter'))


def _parent_run_id(row: Mapping[str, Any]) -> Any:
    """新账显式给 parent；直接 job 与历史账回退到自身 run。"""
    if 'parent_run_id' not in row or row.get('parent_run_id') is None:
        return row.get('run_id')
    return row.get('parent_run_id')


def _turn_matches_ledger(
    record: agent_session_audit.AgentSessionRecord,
    turn: agent_session_audit.AgentSessionEvent,
    row: Mapping[str, Any],
) -> bool:
    profile = record.start.profile
    if profile is None:
        return False
    try:
        actual = resolve_agent_model_identity(
            profile, turn.actual_model, cli_version=turn.cli_version,
            require_driver_version=True)
        recorded = resolve_ledger_model_identity(
            profile, row.get('model'), cli_version=turn.cli_version,
            require_driver_version=True)
    except AgentModelIdentityRefused:
        return False
    return (
        record.start.parent_run_id == row.get('run_id')
        and record.start.role == 'executor'
        and record.start.node == row.get('node')
        and record.start.chapter == row.get('chapter')
        and turn.attempt_id == row.get('attempt_id')
        and actual == recorded
        and row.get('request_model', profile.model) == profile.model
        and profile.reasoning_effort == row.get('reasoning_effort')
        # 供应商未回显 effort 是明确的 unknown，不拿 requested
        # 回填 actual；一旦回显，就必须与冻结 profile 精确相等。
        and (turn.actual_effort is None
             or turn.actual_effort == profile.reasoning_effort)
    )


# 这是 R-11.26 已发行同席重试的验收表，不追溯重分类历史日志；
# authentication/usage_limit 虽可等待外部恢复，却不属于秒级同席热重试。
_COMMANDER_RECOVERABLE_FAILURES = frozenset({
    'connection', 'timeout', 'server_error', 'rate_limit', 'provider_transient',
})
_COMMANDER_USAGE_FIELDS = (
    'prompt_tokens', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens',
    'completion_tokens', 'reasoning_tokens', 'total_tokens',
)


def _commander_attempt_index(
    records: Sequence[agent_session_audit.AgentSessionRecord],
    turns: Mapping[str, tuple[agent_session_audit.AgentSessionRecord,
                             agent_session_audit.AgentSessionEvent]],
) -> dict[str, agent_session_audit.AgentSessionEvent]:
    result: dict[str, agent_session_audit.AgentSessionEvent] = {}
    for record in records:
        for event in record.attempts:
            key = event.attempt_id
            if key is None or key in result:
                raise ValueError('session audit attempt 事件身份缺失或重复')
            linked = turns.get(key)
            if linked is not None and linked[0].session_run_id != record.session_run_id:
                raise ValueError('同一 attempt 的 turn 与 attempt 事件属于不同 session')
            result[key] = event
    return result


def _commander_event_matches_start(
    record: agent_session_audit.AgentSessionRecord,
    event: agent_session_audit.AgentSessionEvent,
) -> bool:
    start = record.start
    profile = start.profile
    return bool(
        profile is not None
        and event.session_run_id == record.session_run_id == start.session_run_id
        and event.parent_run_id == start.parent_run_id
        and event.role == start.role == 'commander'
        and event.node is None and event.chapter is None
        and start.node is None and start.chapter is None
        and event.profile == profile
        and event.subject_sha256 == start.subject_sha256
        and event.requested_model == start.requested_model == profile.model
        and event.requested_effort == start.requested_effort == profile.reasoning_effort
        and event.start_code_version == start.start_code_version
        and event.protocol_version == start.protocol_version
        and event.cli_version == start.cli_version
        and event.skill_sha256 == start.skill_sha256
        and event.schema_sha256 == start.schema_sha256
    )


def _commander_delivery_chain(
    record: agent_session_audit.AgentSessionRecord,
    rows: Sequence[Mapping[str, Any]],
) -> tuple[bool, int, int, int]:
    """只证明同席运输与成本闭合；不删失败、不把未知用量补零。

    delivery_attempts 按每次 Commander 逻辑调用从 1 重计。成本账的通用
    attempt/retryable 格是 Commander 历史占位，不能拿它替代这份原生序号。
    ended.error_kind 可记运输成功之后的 typed 拒因或正常 binding_changed；
    它不是本项的批准判据。原值在联结 facts 披露，整书仍由 outer_run、
    current_hash_bound_approvals 和当前交付独立判断，不能据本项代批。
    """
    start, end = record.start, record.end
    if (start.kind != 'started' or end is None or end.kind != 'ended'
            or end.process_state != 'ok'
            or end.delivery_state not in ('delivered', 'blocked')
            or not _commander_event_matches_start(record, end)
            or end.end_code_version != start.start_code_version
            or start.start_code_version in (None, prov.CODE_UNKNOWN)
            or not start.subject_sha256 or not start.prompt_sha256 or not start.schema_sha256):
        return False, 0, 0, 0
    events = record.attempts
    row_by = {row.get('attempt_id'): row for row in rows}
    if (not events or events[0].prompt_sha256 != start.prompt_sha256
            or len(row_by) != len(rows) or len(events) != len(rows)
            or {event.attempt_id for event in events} != set(row_by)):
        return False, 0, 0, 0
    turns = {turn.attempt_id: turn for turn in record.turns}
    expected_turn_ids = [event.attempt_id for event in events
                         if event.provider_session_id is not None]
    if (not record.turns or record.turns[0].prompt_sha256 != start.prompt_sha256
            or [turn.attempt_id for turn in record.turns] != expected_turn_ids
            or len(turns) != len(record.turns)):
        return False, 0, 0, 0
    provider_ids = {event.provider_session_id for event in events
                    if event.provider_session_id is not None}
    if (len(provider_ids) != 1 or set(record.provider_session_ids) != provider_ids
            or end.provider_session_id not in provider_ids):
        return False, 0, 0, 0
    required = {
        'run_id', 'sid', 'session_run_id', 'role', 'node', 'chapter',
        'attempt_id', 'request_model', 'reasoning_effort', 'model',
        'actual_reasoning_effort', 'code', 'protocol_version', 'cli_version',
        'provider_session_id', 'outcome', 'exit_code', 'error', 'error_kind',
        'response_sha256', *_COMMANDER_USAGE_FIELDS,
    }
    expected_ordinal = 1
    failed = without_provider = unknown_usage = 0
    previous_ts = start.ts
    last_turn_index = 0
    for event in events:
        row = row_by[event.attempt_id]
        usage = (dict.fromkeys(_COMMANDER_USAGE_FIELDS) if event.usage is None
                 else event.usage.to_dict())
        if (event.kind != 'attempt' or not _commander_event_matches_start(record, event)
                or not required.issubset(row)
                or not start.ts <= previous_ts <= event.ts <= end.ts
                or event.delivery_attempts != expected_ordinal
                or not 1 <= expected_ordinal <= COMMANDER_MAX_ATTEMPTS_PER_SEAT
                or row['run_id'] != start.parent_run_id
                or row['session_run_id'] != record.session_run_id
                or row['role'] != 'commander' or row['node'] is not None or row['chapter'] is not None
                or row['request_model'] != start.profile.model
                or row['reasoning_effort'] != start.profile.reasoning_effort
                or row['model'] != event.actual_model
                or row['actual_reasoning_effort'] != event.actual_effort
                or (event.actual_model is not None and event.actual_model != start.profile.model)
                or (event.actual_effort is not None and event.actual_effort != start.profile.reasoning_effort)
                or row['code'] != event.start_code_version
                or row['protocol_version'] != event.protocol_version
                or row['cli_version'] != event.cli_version
                or row['provider_session_id'] != event.provider_session_id
                or row['error_kind'] != event.error_kind or row['error'] != event.error_kind
                or any(row[name] is not None and (
                    type(row[name]) is not int or row[name] < 0)
                    for name in _COMMANDER_USAGE_FIELDS)
                or any(row[name] != usage[name] for name in _COMMANDER_USAGE_FIELDS)
                or isinstance(row['exit_code'], bool) or not isinstance(row['exit_code'], int)
                or not event.prompt_sha256):
            return False, 0, 0, 0
        previous_ts = event.ts
        unknown_usage += int(event.usage is None)
        turn = turns.get(event.attempt_id)
        if turn is not None:
            if (turn.kind != 'turn' or not _commander_event_matches_start(record, turn)
                    or turn.provider_session_id != event.provider_session_id
                    or turn.prompt_sha256 != event.prompt_sha256
                    or turn.actual_model != event.actual_model
                    or turn.actual_effort != event.actual_effort
                    or turn.usage != event.usage or turn.error_kind != event.error_kind
                    or turn.turn_index is None or turn.turn_index <= last_turn_index
                    or not event.ts <= turn.ts <= end.ts):
                return False, 0, 0, 0
            last_turn_index = turn.turn_index
        if row['outcome'] == 'ok':
            if row['exit_code'] != 0 or event.error_kind is not None or turn is None:
                return False, 0, 0, 0
            expected_ordinal = 1
        elif row['outcome'] == 'error':
            if (row['exit_code'] == 0 or event.error_kind not in _COMMANDER_RECOVERABLE_FAILURES
                    or row['response_sha256'] is not None):
                return False, 0, 0, 0
            failed += 1
            without_provider += int(event.provider_session_id is None)
            expected_ordinal += 1
        else:
            return False, 0, 0, 0
    # 每一组的最后一项必须确实成功；ended-ok 本身不能替一笔失败补回答。
    return expected_ordinal == 1 and bool(events), failed, without_provider, unknown_usage


def linkage_check(
    *, run_id: str, sid: str,
    ledger_rows: Sequence[Mapping[str, Any]],
    audit_rows: Sequence[Mapping[str, Any]],
    sessions: Sequence[agent_session_audit.AgentSessionRecord],
    producer_run_ids: set[str] | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    """ledger ↔ session turn ↔ adoption 的逐 attempt 关闭式对账。"""
    run_ledger = [
        row for row in ledger_rows
        if _parent_run_id(row) == run_id and row.get('sid') == sid
    ]
    if producer_run_ids is None:
        producer_run_ids = {
            str(row.get('run_id')) for row in run_ledger
            if (row.get('node') in AGENT_EXECUTOR_NODES
                and isinstance(row.get('run_id'), str)
                and row.get('run_id'))
        }
        producer_run_ids.add(run_id)
    relevant_sessions = [
        record for record in sessions
        if (
            (record.start.role == 'commander'
             and record.start.parent_run_id == run_id)
            or (record.start.role == 'executor'
                and record.start.parent_run_id in producer_run_ids)
        )
    ]
    commander = [
        record for record in relevant_sessions if record.start.role == 'commander'
    ]
    executors = [
        record for record in relevant_sessions if record.start.role == 'executor'
    ]
    open_sessions = [
        record.session_run_id for record in relevant_sessions if record.end is None
    ]
    unknown_sessions = [
        record.session_run_id for record in relevant_sessions
        if record.end is not None and (
            record.end.process_state == 'unknown'
            or record.end.delivery_state == 'unknown')
    ]

    run_adoptions = [
        row for row in audit_rows
        if (row.get('kind') == 'artifact_adoption'
            and _parent_run_id(row) == run_id
            and row.get('sid') == sid
            and row.get('node') in AGENT_EXECUTOR_NODES)
    ]
    try:
        ledger_by_attempt = _attempt_index(run_ledger)
        sessions_by_attempt = _session_index(relevant_sessions)
        _commander_attempt_index(relevant_sessions, sessions_by_attempt)
    except ValueError:
        return _check(
            'ledger_session_adoption_linkage', 'unknown',
            ledger_rows=len(run_ledger), adoption_rows=len(run_adoptions),
            sessions=len(relevant_sessions), identity_collision=True)

    commander_turns = [
        (record, turn) for record in commander for turn in record.turns
    ]
    commander_rows = [
        row for row in run_ledger if row.get('role') == 'commander'
    ]
    # 无 attempt 事件的发行成功账保留原判据；失败恢复必须有新的完整尝试链。
    commander_linked = bool(commander_turns) or resume
    recovered_attempts = relaunch_failures = unknown_usage_attempts = 0
    commander_attempt_rows = sum(len(record.attempts) for record in commander)
    for record in commander:
        rows = [row for row in commander_rows
                if row.get('session_run_id') == record.session_run_id]
        if record.attempts:
            linked, recovered, relaunched, usage_unknown = _commander_delivery_chain(record, rows)
            commander_linked &= linked
            if linked:
                recovered_attempts += recovered
                relaunch_failures += relaunched
                unknown_usage_attempts += usage_unknown
            continue
        # legacy 只能证明其原有全成功 turn，绝不以缺 attempt 为理由放过失败。
        commander_linked &= len(rows) == len(record.turns) and bool(record.turns)
        for turn in record.turns:
            row = ledger_by_attempt.get(str(turn.attempt_id or ''))
            commander_linked &= (
                row is not None and row.get('role') == 'commander'
                and row.get('outcome') == 'ok'
                and record.end is not None
                and record.end.process_state == 'ok'
                and record.end.delivery_state in ('delivered', 'blocked')
                and row.get('session_run_id') == record.session_run_id
                and row.get('model') == turn.actual_model
                and record.start.profile is not None
                and row.get('request_model', record.start.profile.model)
                == record.start.profile.model
                and row.get('reasoning_effort') == record.start.profile.reasoning_effort
                and (turn.actual_model is None
                     or turn.actual_model == record.start.profile.model)
                and row.get('actual_reasoning_effort') == turn.actual_effort
                and (turn.actual_effort is None
                     or turn.actual_effort == record.start.profile.reasoning_effort)
            )
    commander_linked &= all(
        row.get('session_run_id') in {record.session_run_id for record in commander}
        for row in commander_rows)

    successful_executor_rows = [
        row for row in run_ledger
        if row.get('node') in AGENT_EXECUTOR_NODES
        and row.get('outcome') == 'ok'
    ]
    successful_without_turn = 0
    turn_mismatches = 0
    for row in successful_executor_rows:
        linked = sessions_by_attempt.get(str(row.get('attempt_id') or ''))
        if linked is None:
            successful_without_turn += 1
        elif not _turn_matches_ledger(linked[0], linked[1], row):
            turn_mismatches += 1

    orphan_executor_turns = 0
    for record in executors:
        for turn in record.turns:
            row = ledger_by_attempt.get(str(turn.attempt_id or ''))
            if row is None or not _turn_matches_ledger(record, turn, row):
                orphan_executor_turns += 1

    adoption_unlinked = 0
    adoption_not_delivered = 0
    adoption_counts: Counter[str] = Counter()
    for adoption in run_adoptions:
        node = str(adoption.get('node'))
        adoption_counts[node] += 1
        attempt_id = str(adoption.get('attempt_id') or '')
        row = ledger_by_attempt.get(attempt_id)
        linked = sessions_by_attempt.get(attempt_id)
        if (row is None or row.get('outcome') != 'ok'
                or linked is None
                or not _same_attempt_identity(row, adoption)
                or not _turn_matches_ledger(linked[0], linked[1], row)):
            adoption_unlinked += 1
            continue
        if (linked[0].end is None
                or linked[0].end.delivery_state != 'delivered'):
            adoption_not_delivered += 1

    required_nodes = (
        {str(row['node']) for row in successful_executor_rows}
        | {str(record.start.node) for record in executors}
        if resume else set(AGENT_EXECUTOR_NODES))
    missing_adoption_nodes = sorted(required_nodes - set(adoption_counts))
    passed = (
        commander_linked
        and not open_sessions
        and not unknown_sessions
        and (bool(executors) or resume)
        and successful_without_turn == 0
        and turn_mismatches == 0
        and orphan_executor_turns == 0
        and adoption_unlinked == 0
        and adoption_not_delivered == 0
        and not missing_adoption_nodes
    )
    return _check(
        'ledger_session_adoption_linkage', 'pass' if passed else 'fail',
        commander_linked=commander_linked,
        commander_attempt_rows=commander_attempt_rows,
        recovered_commander_attempts=recovered_attempts,
        commander_failures_without_provider_turn=relaunch_failures,
        commander_unknown_usage_attempts=unknown_usage_attempts,
        # 后续业务拒因独立披露；运输对账不能将它抹掉，也不能代替正式审批。
        commander_end_error_kinds=dict(sorted(Counter(
            record.end.error_kind for record in commander
            if record.end is not None and record.end.error_kind is not None).items())),
        executor_sessions=len(executors),
        open_sessions=len(open_sessions),
        unknown_sessions=len(unknown_sessions),
        successful_executor_ledger_rows=len(successful_executor_rows),
        successful_without_turn=successful_without_turn,
        turn_mismatches=turn_mismatches,
        orphan_executor_turns=orphan_executor_turns,
        adoption_rows=len(run_adoptions),
        adoption_unlinked=adoption_unlinked,
        adoption_not_delivered=adoption_not_delivered,
        adoptions_by_node=dict(sorted(adoption_counts.items())),
        missing_adoption_nodes=missing_adoption_nodes,
        scope='current_run_attempts' if resume else 'all_agent_nodes_in_run',
    )


def current_approval_check(base: Path, sid: str, approval: Any) -> dict[str, Any]:
    """独立核 producer 当前票，也要求公开投影一致；不能覆盖 API false 后验收。"""
    gates = dict(approval.gate)
    producer_gates = {
        gate: queue._human_gate_satisfied(base, sid, node)
        for gate, node in (('s6', 'stage6.approve'), ('s7', 'stage7.approve'))
    }
    projection_mismatches = sorted(
        gate for gate, current in producer_gates.items()
        if gates.get(gate) != current
    )
    producer_missing = sorted(
        gate for gate, current in producer_gates.items() if not current)
    closed = sorted(key for key, value in gates.items() if value)
    missing = sorted(set(APPROVAL_GATES) - set(closed))
    seen = sorted(key for key, value in approval.seen.items() if value)
    missing_seen = sorted(set(APPROVAL_SEEN) - set(seen))
    passed = not missing and not missing_seen and not producer_missing and not projection_mismatches
    return _check(
        'current_hash_bound_approvals', 'pass' if passed else 'fail',
        closed_gates=closed, missing_gates=missing,
        seen=seen, missing_seen=missing_seen,
        producer_gates=producer_gates, producer_missing_gates=producer_missing,
        projection_mismatches=projection_mismatches)


def epoch_lifecycle_check(events: Sequence[Any], run_id: str) -> dict[str, Any]:
    """v2 状态机认 durable epoch 开闭；没有旧 host 遥测不等于没有执行。"""
    active: str | None = None
    opened: set[str] = set()
    closed: set[str] = set()
    valid = True
    ended = False
    for event in events:
        if event.run_id != run_id:
            continue
        if event.kind == 'epoch_opened':
            epoch = event.epoch
            valid &= (not ended and active is None and epoch is not None
                      and epoch.epoch_id not in opened)
            if epoch is not None:
                active = epoch.epoch_id
                opened.add(active)
        elif event.kind == 'epoch_closed':
            valid &= (not ended and event.epoch_id == active
                      and event.epoch_id not in closed
                      and event.drain_state == 'drained'
                      and event.exit_code in (0, 1, 2))
            closed.add(event.epoch_id)
            active = None
        elif event.kind == 'ended':
            valid &= not ended and active is None and event.exit_code == 0
            ended = True
    passed = valid and ended and bool(opened) and opened == closed and active is None
    return _check(
        'operator_phase_lifecycle', 'pass' if passed else 'fail',
        source='agent_run_v2_epochs', opened_epochs=len(opened),
        closed_epochs=len(closed), ended=ended, valid_sequence=valid)


def execution_identity_check(identity: Mapping[str, Any], outer: Any, *,
                             resume: bool, ledger_lines: int) -> dict[str, Any]:
    """零 wire 续跑仍有 durable 代码身份，但不是一次模型性能实验。"""
    if resume and ledger_lines == 0 and not identity['code_identities']:
        codes = set()
        if outer is not None:
            codes.add(outer.start.code_version)
            codes.update(epoch.code_version for epoch in outer.epochs)
        valid = (outer is not None and bool(outer.epochs) and len(codes) == 1
                 and all(isinstance(code, str)
                         and re.fullmatch(r'[0-9a-f]{40}', code) for code in codes))
        return _check(
            'experiment_identity_clean', 'pass' if valid else 'fail',
            scope='zero_wire_run_identity', execution_identity_valid=valid,
            experiment_valid=False, code_identity_count=len(codes),
            source='agent_run_start_and_epochs', ledger_lines=0)
    return _check(
        'experiment_identity_clean',
        'pass' if identity['experiment_valid'] else 'fail',
        experiment_valid=identity['experiment_valid'],
        code_identity_count=len(identity['code_identities']),
        invalid_reason_count=len(identity['experiment_invalid_reasons']))


def _workflow_metrics_for_run(
    base: str | Path, sid: str, run_id: str,
) -> dict[str, Any]:
    """本轮成本/闸/CLI/身份只认精确 run；产物与人审仍由读数器看 current snapshot。"""
    return workflow_metrics.collect(base, sid, run_id=run_id)


def collect(base: str | Path, sid: str, run_id: str, *,
            resume: bool = False) -> dict[str, Any]:
    """收集一份不含书身份的机读验收报告。"""
    base_path = Path(base).resolve()
    service_before = _service_seal(base_path)
    snapshot = capture_operator_snapshot(base_path, sid)
    full_surface_before = read_surface_seal(
        base_path, sid, code=snapshot.code,
        include_final_evidence_service=True)

    checks: list[dict[str, Any]] = []
    outer, outer_check = _one_outer_run(base_path, run_id)
    checks.append(outer_check)
    checks.append(execution_lifecycle_check(base_path, run_id, sid))

    stage_states = {row.stage: row.done for row in snapshot.overview.stages}
    missing_stages = sorted(set(STAGES) - set(stage_states))
    not_done = {
        stage: stage_states.get(stage)
        for stage in STAGES if stage_states.get(stage) is not True
    }
    checks.append(_check(
        'stage0_to_stage7_complete',
        'pass' if not missing_stages and not not_done else 'fail',
        stages=stage_states, missing_stages=missing_stages))

    reports: dict[str, dict[str, Any]] = {}
    report_unknown: list[str] = []
    hard_open: list[str] = []
    unver_rows = skipped_rows = 0
    for stage in STAGES:
        observation = snapshot.check(stage)
        if observation.error is not None or observation.report is None:
            report_unknown.append(stage)
            continue
        report = observation.report
        hard = [
            row.rule for row in report.rules
            if row.sev == 'hard' and row.status in ('hit', 'skipped')
        ]
        hard_open.extend(f'{stage}:{rule}' for rule in hard)
        unver = sum(row.status == 'unver' for row in report.rules)
        skipped = sum(row.status == 'skipped' for row in report.rules)
        unver_rows += unver
        skipped_rows += skipped
        reports[stage] = {
            'badge': report.badge,
            'hard_open': len(hard),
            'unver': unver,
            'skipped': skipped,
        }
    checks.append(_check(
        'current_reports_hard_closed',
        ('unknown' if report_unknown else
         'pass' if not hard_open else 'fail'),
        reports=reports, unavailable_stages=report_unknown,
        hard_open=hard_open, unver_rows=unver_rows,
        skipped_rows=skipped_rows,
        unver_policy='disclosed_not_green'))

    inbox = inbox_from_snapshot(snapshot)
    blocking = int(inbox.delivery_counts.get('blocking', 0))
    checks.append(_check(
        'inbox_delivery_blocking_zero', 'pass' if blocking == 0 else 'fail',
        blocking=blocking,
        disclosure=int(inbox.delivery_counts.get('disclosure', 0)),
        invalid=int(inbox.counts.get('invalid', 0))))

    approval = approvals.get(
        base_path, sid, overview=snapshot.overview, code=snapshot.code)
    checks.append(current_approval_check(base_path, sid, approval))

    metrics = _workflow_metrics_for_run(base_path, sid, run_id)
    drifted = sorted(
        row['rel'] for row in metrics['artifacts']['artifacts']
        if row['version_state'] == 'drifted')
    checks.append(_check(
        'registered_artifact_versions_current',
        'pass' if not drifted else 'fail', drifted=drifted,
        registered_artifacts=sum(
            row['version_state'] != 'unregistered'
            for row in metrics['artifacts']['artifacts'])))
    identity = metrics['identity']
    checks.append(execution_identity_check(
        identity, outer, resume=resume, ledger_lines=metrics['ledger']['lines']))

    if outer is None:
        checks.append(_check(
            'fresh_delivery_assessment', 'unknown', assessed=False))
    else:
        outcome = delivery.assess(
            base_path, sid, start_code_version=outer.start.code_version)
        checks.append(_check(
            'fresh_delivery_assessment',
            'pass' if outcome.state in READY_OUTCOMES else 'fail',
            assessed=True, delivery_state=outcome.state))

    session_records = agent_session_audit.fold(
        agent_session_audit.read_events(base_path))
    ledger_rows = read_jsonl_rows(ledger_path(base_path))
    audit_rows = read_jsonl_rows(audit_path(base_path))
    producer_run_ids = {run_id}
    if outer is not None:
        producer_run_ids.update(
            epoch.session_run_id for epoch in outer.epochs)
    checks.append(linkage_check(
        run_id=run_id, sid=sid, ledger_rows=ledger_rows,
        audit_rows=audit_rows, sessions=session_records,
        producer_run_ids=producer_run_ids, resume=resume))

    started_at = None if outer is None else outer.started
    telemetry = operator_telemetry.summarize(
        base_path, sid, started_at=started_at)
    parent_sessions = [
        row for row in telemetry['sessions']
        if row['parent_run_id'] == run_id
    ]
    telemetry_passed = (
        bool(parent_sessions)
        and all(row['ended_at_ms'] is not None for row in parent_sessions)
        and all(row['state'] == 'completed' for row in parent_sessions)
        and all(row['code_version_match'] is True for row in parent_sessions)
        and all(all(phase['ended_at_ms'] is not None for phase in row['phases'])
                for row in parent_sessions)
    )
    phase_check = _check(
        'operator_phase_lifecycle',
        'pass' if telemetry_passed else 'fail',
        parent_sessions=len(parent_sessions),
        completed_parent_sessions=sum(
            row['state'] == 'completed' for row in parent_sessions),
        unfinished_parent_phases=sum(
            phase['ended_at_ms'] is None
            for row in parent_sessions for phase in row['phases']))
    checks.append(
        epoch_lifecycle_check(agent.read_events(base_path), run_id)
        if outer is not None and outer.contract is not None else phase_check)

    full_surface_after = read_surface_seal(
        base_path, sid, code=snapshot.code,
        include_final_evidence_service=True)
    service_after = _service_seal(base_path)
    stable = (
        full_surface_before == full_surface_after
        and service_before == service_after
        and snapshot.surface_seal == read_surface_seal(
            base_path, sid, code=snapshot.code)
    )
    checks.append(_check(
        'single_read_surface', 'pass' if stable else 'unknown',
        stable=stable))

    stability = {
        'ledger': metrics['ledger'],
        'first_call_landing': metrics['audit']['first_call_landing'],
        'gate_by_decision': metrics['audit']['gate_by_decision'],
        'per_node_gate_rejections': metrics['audit']['per_node_gate_rejections'],
        'resamples': metrics['audit']['resamples'],
        'artifact_adoptions': metrics['audit']['artifact_adoptions'],
        'cli': metrics['cli'],
        'human': metrics['human'],
        'operator': {
            'parent_sessions': len(parent_sessions),
            'phase_count': sum(len(row['phases']) for row in parent_sessions),
            'model_usage_state': telemetry['model_usage_state'],
        },
    }
    return {
        'schema': 'novel-agent-e2e-verification-v1',
        'sid': sid,
        'run_id': run_id,
        'mode': 'resume' if resume else 'fresh',
        'result': _overall(checks),
        'checks': checks,
        # 目标值不在这里自动 go/no-go；只把 TODO-32 P3 要求的
        # 分母和原始次数原样发出。
        'stability_readings': stability,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True)
    parser.add_argument('--sid', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--resume', action='store_true',
                        help='验收续跑：既有产物仍查 current，本轮费用只查实际调用，不宣称八个付费节点均在本轮执行')
    args = parser.parse_args(argv)
    try:
        report = collect(args.base, args.sid, args.run_id, resume=args.resume)
    except Exception as exc:  # noqa: BLE001 —— 验收器崩溃不能冒充业务 fail/pass
        report = {
            'schema': 'novel-agent-e2e-verification-v1',
            'sid': args.sid,
            'run_id': args.run_id,
            'result': 'unknown',
            'checks': [_check(
                'verifier_integrity', 'unknown',
                error_type=type(exc).__name__)],
            'stability_readings': None,
        }
    print(json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
    return {'pass': 0, 'fail': 1, 'unknown': 2}[report['result']]


if __name__ == '__main__':
    raise SystemExit(main())
