"""跑书页统计：只读服务账，不产生报告、授权、恢复动作或模型请求。

总数从成本账统计；工作单元仅用于归属。没有精确关联的旧行仍进已记录总量，
另列未归属分母，不按时间邻近猜测。运行中缺尾/未完成 attempt 明示不完整。
"""
from __future__ import annotations

import time

from contracts.schema.run_metrics import (
    AttemptMetrics, RunMetricChoice, RunMetricsView, UnitMetrics,
)
from novel import agent_session_audit, work_units
from novel.errors import ArtifactUnreadable, TerminalState
from novel.orchestrator import agent, jobs
from novel.work_unit_usage import match_attempt, nonnegative, owner, read_cost_snapshot, summarize


def _runs(base: str, sid: str) -> tuple[list[RunMetricChoice], dict[str, str]]:
    choices = []
    parents = {}
    now = int(time.time())
    for run in agent.records(base):
        if run.start.sid != sid:
            continue
        for epoch in run.epochs:
            previous = parents.setdefault(epoch.session_run_id, run.run_id)
            if previous != run.run_id:
                raise ValueError('同一 producer 关联多个父运行')
        choices.append(RunMetricChoice(
            run_id=run.run_id, kind='agent', state=run.state,
            started_at=run.started, ended_at=run.ended,
            elapsed_seconds=max(0, (run.ended or now) - run.started)
            if run.ended is not None or run.state in ('running', 'paused', 'pending') else None))
    for job in jobs.records(base):
        if job.start.sid != sid or job.job_id in parents:
            continue
        choices.append(RunMetricChoice(
            run_id=job.job_id, kind='job', state=job.state,
            started_at=job.started, ended_at=job.ended,
            elapsed_seconds=max(0, (job.ended or now) - job.started)
            if job.ended is not None or job.state == 'running' else None))
    return sorted(choices, key=lambda row: (row.started_at or 0, row.run_id), reverse=True), parents


def get(base: str, sid: str, run_id: str | None = None) -> RunMetricsView:
    try:
        rows, tail = read_cost_snapshot(base)
    except (OSError, ValueError) as exc:
        raise ArtifactUnreadable(f'运行统计无法读取成本账：{exc}') from exc
    issues = ['成本账有未提交尾，以下仅为已记录小计'] if tail else []
    try:
        runs, parents = _runs(base, sid)
    except (TerminalState, OSError, ValueError) as exc:
        runs, parents = [], {}
        issues.append(f'运行身份账不可读：{exc}')
    book_rows = [row for row in rows if row.get('sid') == sid]
    selected = [row for row in book_rows if run_id is None or owner(row) == run_id]
    known_runs = {run.run_id for run in runs}
    for key in sorted({owner(row) for row in book_rows if owner(row)} - known_runs):
        runs.append(RunMetricChoice(run_id=key, kind='ledger', state='unknown',
                                   started_at=None, ended_at=None, elapsed_seconds=None))
    if run_id is not None and run_id not in {run.run_id for run in runs}:
        issues.append('所选运行没有可核验的记录，不能把空结果当作零用量')
    try:
        unit_views = work_units.list_unit_views(base, sid)
    except (TerminalState, OSError, ValueError) as exc:
        unit_views = None
        issues.append(f'工作单元账不可读：{exc}')
    if unit_views is None:
        issues.append('工作单元归属尚不可用，费用行保留在未归属项')
    try:
        sessions = agent_session_audit.fold(agent_session_audit.read_events(base))
        session_index = {session.session_run_id: session for session in sessions}
    except (OSError, ValueError) as exc:
        session_index = {}
        issues.append(f'会话证据不可读：{exc}')
    linked: set[int] = set()
    units = []
    missing_total: set[str] = set()
    incomplete_units = unit_views is None
    uncertain_ownership = False
    for unit in unit_views or []:
        budget = work_units.WorkUnitBudget(base, sid, unit['node'], unit['role'], unit['scope'])
        try:
            ledger = budget._read()
        except (TerminalState, OSError, ValueError) as exc:
            issues.append(f"工作单元 {unit['node']}/{unit['scope']} 读取失败：{exc}")
            incomplete_units = True
            continue
        attempts, costs, unit_issues = [], [], []
        turns = 0
        retries = 0
        missing: set[str] = set()
        incomplete_unit = False
        for turn in ledger.turns:
            turn_owner = parents.get(turn.run_id, turn.run_id)
            matched = []
            for attempt in turn.attempts:
                error = None
                try:
                    cost = match_attempt(ledger, turn, attempt, rows)
                except work_units.WorkUnitRefused as exc:
                    cost, error = None, str(exc)
                matched.append((attempt, cost, error))
            owners = {owner(cost) for _, cost, _ in matched if cost is not None}
            if (len(owners) > 1 or None in owners
                    or turn.run_id in parents and owners and owners != {turn_owner}):
                issues.append(f'{ledger.node}/{ledger.scope} 内容轮 {turn.ordinal} 的 owner 归属冲突')
                uncertain_ownership = True
                if run_id is not None:
                    continue
                turn_owner = None
                matched = [(attempt, None, '内容轮 owner 归属不可证明') for attempt, _, _ in matched]
            elif owners:
                turn_owner = next(iter(owners))
            if run_id is not None and turn_owner != run_id:
                continue
            turns += 1
            retries += max(0, len(turn.attempts) - 1)
            if not turn.attempts and turn.state in ('reserved', 'unknown'):
                incomplete_unit = True
                unit_issues.append(f'内容轮 {turn.ordinal} 尚无已提交尝试证据')
            session = session_index.get(turn.session_run_id)
            session_valid = (session is not None and session.start.parent_run_id == turn.run_id
                             and session.start.role == ledger.role
                             and (ledger.role != 'executor' or session.start.node == ledger.node))
            for attempt, cost, error in matched:
                if cost is not None:
                    if id(cost) in linked:
                        cost, error = None, '同一成本行被多个工作单元引用'
                    else:
                        linked.add(id(cost))
                        costs.append(cost)
                if cost is None and attempt.outcome != 'not_started':
                    missing.add(attempt.attempt_id)
                data = cost or {}
                if turn.session_run_id and not session_valid:
                    error = '内容轮的会话关联不可证明' if error is None else error
                elif session_valid and attempt.outcome != 'not_started':
                    events = [event for event in (*session.turns, *session.attempts)
                              if event.attempt_id == attempt.attempt_id]
                    if not events or (attempt.provider_session_id is not None and not any(
                            event.provider_session_id == attempt.provider_session_id for event in events)):
                        error = error or '尝试未与原会话证据闭合'
                if error:
                    unit_issues.append(f'内容轮 {turn.ordinal}：{error}')
                attempts.append(AttemptMetrics(
                    attempt_id=attempt.attempt_id, turn=turn.ordinal, run_id=turn.run_id,
                    parent_run_id=turn_owner, session_run_id=turn.session_run_id or None,
                    state=attempt.outcome, cached=data.get('cached') if type(data.get('cached')) is bool else None,
                    prompt_tokens=nonnegative(data.get('prompt_tokens')),
                    completion_tokens=nonnegative(data.get('completion_tokens')),
                    reasoning_tokens=nonnegative(data.get('reasoning_tokens')),
                    latency_ms=nonnegative(data.get('latency_ms')),
                    # 当前账没有可信首 token 事件；不能由总耗时推算。
                    first_token_ms=None,
                    finished_at=data.get('at') if isinstance(data.get('at'), str) else None,
                    issue=error))
        processing = [row for row in ledger.processing
                      if run_id is None or parents.get(row.run_id, row.run_id) == run_id]
        if run_id is not None and not turns and not processing:
            continue
        units.append(UnitMetrics(
            unit_id=ledger.unit_id, node=ledger.node, role=ledger.role, scope=ledger.scope,
            content_turns=turns, network_retries=retries,
            history_adopted=sum(row.kind == 'history_adopted' for row in processing),
            mechanical=sum(row.kind == 'mechanical' for row in processing),
            cached_processing=sum(row.kind == 'cached' for row in processing),
            usage=summarize(costs, incomplete=tail or incomplete_unit,
                            missing_attempt_ids=tuple(missing)),
            attempts=attempts, issues=sorted(set(unit_issues))))
        missing_total.update(missing)
        incomplete_units = incomplete_units or incomplete_unit
    unassigned = [row for row in selected if id(row) not in linked]
    if unassigned:
        issues.append(f'{len(unassigned)} 条成本记录尚无精确工作单元归属，仍计入总量')
    return RunMetricsView(
        sid=sid, run_id=run_id, runs=runs,
        usage=summarize(selected, missing_attempt_ids=tuple(missing_total),
                        incomplete=tail or uncertain_ownership or incomplete_units
                        or run_id is not None and run_id not in {run.run_id for run in runs}),
        units=units, unassigned_rows=len(unassigned),
        unassigned_usage=summarize(unassigned, incomplete=tail), issues=issues)
