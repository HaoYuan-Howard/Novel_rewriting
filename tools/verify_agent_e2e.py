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
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts.invariants import (                                  # noqa: E402
    AGENT_EXECUTOR_NODES, APPROVAL_GATES, APPROVAL_SEEN, STAGES,
)
from novel import (                                                  # noqa: E402
    agent_session_audit, operator_telemetry, prov, skill_contract,
)
from novel.llm.ledger import (                                       # noqa: E402
    audit_path, ledger_path, read_jsonl_rows,
)
from novel.orchestrator import (                                    # noqa: E402
    admission, agent, approvals, beacon, delivery,
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
    try:
        current_skill_sha256 = skill_contract.package_sha256(REPO_ROOT)
    except (OSError, skill_contract.SkillContractError):
        current_skill_sha256 = None
    skill_matches = (
        current_skill_sha256 is not None
        and row.start.skill_sha256 == current_skill_sha256
    )
    facts = {
        'process_state': row.state,
        'exit_code': row.exit_code,
        'delivery_state': None if row.outcome is None else row.outcome.state,
        'authorized': row.start.authorized,
        'egress_authorized': row.start.egress_authorized,
        'profiles_frozen': profiles is not None,
        'start_code_version': row.start.code_version,
        'skill_sha256_recorded': row.start.skill_sha256 is not None,
        'skill_matches_current': skill_matches,
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
        and skill_matches
    )
    state = ('unknown' if current_skill_sha256 is None
             else 'pass' if passed else 'fail')
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


def _turn_matches_ledger(
    record: agent_session_audit.AgentSessionRecord,
    turn: agent_session_audit.AgentSessionEvent,
    row: Mapping[str, Any],
) -> bool:
    profile = record.start.profile
    return (
        record.start.parent_run_id == row.get('run_id')
        and record.start.role == 'executor'
        and record.start.node == row.get('node')
        and record.start.chapter == row.get('chapter')
        and turn.attempt_id == row.get('attempt_id')
        and turn.actual_model == row.get('model')
        and profile is not None
        and profile.model == turn.actual_model
        and profile.reasoning_effort == row.get('reasoning_effort')
        # 供应商未回显 effort 是明确的 unknown，不拿 requested
        # 回填 actual；一旦回显，就必须与冻结 profile 精确相等。
        and (turn.actual_effort is None
             or turn.actual_effort == profile.reasoning_effort)
    )


def linkage_check(
    *, run_id: str, sid: str,
    ledger_rows: Sequence[Mapping[str, Any]],
    audit_rows: Sequence[Mapping[str, Any]],
    sessions: Sequence[agent_session_audit.AgentSessionRecord],
) -> dict[str, Any]:
    """ledger ↔ session turn ↔ adoption 的逐 attempt 关闭式对账。"""
    relevant_sessions = [
        record for record in sessions
        if record.start.parent_run_id == run_id
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

    run_ledger = [
        row for row in ledger_rows
        if row.get('run_id') == run_id and row.get('sid') == sid
    ]
    run_adoptions = [
        row for row in audit_rows
        if (row.get('kind') == 'artifact_adoption'
            and row.get('run_id') == run_id
            and row.get('sid') == sid
            and row.get('node') in AGENT_EXECUTOR_NODES)
    ]
    try:
        ledger_by_attempt = _attempt_index(run_ledger)
        sessions_by_attempt = _session_index(relevant_sessions)
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
    commander_linked = (
        len(commander) == len(commander_rows) == len(commander_turns) == 1
        and commander[0].end is not None
        and commander[0].end.process_state == 'ok'
        and commander[0].end.delivery_state == 'delivered'
        and commander_rows[0].get('attempt_id') == commander_turns[0][1].attempt_id
        and commander_rows[0].get('session_run_id') == commander[0].session_run_id
        and commander_rows[0].get('model') == commander_turns[0][1].actual_model
        and commander_rows[0].get('actual_reasoning_effort')
        == commander_turns[0][1].actual_effort
    )

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

    missing_adoption_nodes = sorted(
        set(AGENT_EXECUTOR_NODES) - set(adoption_counts))
    passed = (
        commander_linked
        and not open_sessions
        and not unknown_sessions
        and bool(executors)
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
    )


def collect(base: str | Path, sid: str, run_id: str) -> dict[str, Any]:
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
    closed_gates = sorted(key for key, value in approval.gate.items() if value)
    missing_gates = sorted(set(APPROVAL_GATES) - set(closed_gates))
    seen = sorted(key for key, value in approval.seen.items() if value)
    missing_seen = sorted(set(APPROVAL_SEEN) - set(seen))
    checks.append(_check(
        'current_hash_bound_approvals',
        'pass' if not missing_gates and not missing_seen else 'fail',
        closed_gates=closed_gates, missing_gates=missing_gates,
        seen=seen, missing_seen=missing_seen))

    metrics = workflow_metrics.collect(base_path, sid)
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
    checks.append(_check(
        'experiment_identity_clean',
        'pass' if identity['experiment_valid'] else 'fail',
        experiment_valid=identity['experiment_valid'],
        code_identity_count=len(identity['code_identities']),
        invalid_reason_count=len(identity['experiment_invalid_reasons'])))

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
    checks.append(linkage_check(
        run_id=run_id, sid=sid, ledger_rows=ledger_rows,
        audit_rows=audit_rows, sessions=session_records))

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
    checks.append(_check(
        'operator_phase_lifecycle',
        'pass' if telemetry_passed else 'fail',
        parent_sessions=len(parent_sessions),
        completed_parent_sessions=sum(
            row['state'] == 'completed' for row in parent_sessions),
        unfinished_parent_phases=sum(
            phase['ended_at_ms'] is None
            for row in parent_sessions for phase in row['phases'])))

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
    args = parser.parse_args(argv)
    try:
        report = collect(args.base, args.sid, args.run_id)
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
