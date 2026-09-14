"""工作单元管理共用的精确成本、会话及 owner 停止证明；全部只读。"""
from __future__ import annotations

from pathlib import Path
from contracts.schema.api import WorkUnitCostView
from contracts.schema.orchestration import AgentRunEvent
from contracts.schema.workflow import WorkUnitLedgerV2
from novel import paths, work_units
from novel.agents import session_audit as agent_session_audit
from novel.llm.ledger import ledger_path, read_strict_jsonl_objects
from novel.orchestrator import admission, beacon, jobs
from novel.work_unit_usage import match_attempt, summarize


def read_rows(path: Path, label: str) -> list[dict]:
    return read_strict_jsonl_objects(path, label=label) if path.is_file() else []


def load_budget(base: str | Path, sid: str, unit_id: str) -> work_units.WorkUnitBudget:
    path = paths.work_unit_dir(base, sid, unit_id) / 'ledger.json'
    ledger = work_units._read(base, path, WorkUnitLedgerV2)
    budget = work_units.WorkUnitBudget(base, sid, ledger.node, ledger.role, ledger.scope)
    if budget.unit_id != unit_id or unit_id not in budget._registry().unit_ids:
        raise work_units.WorkUnitRefused('工作单元未登记或身份漂移')
    budget._read()
    return budget


def cost_evidence(budget: work_units.WorkUnitBudget, ledger: WorkUnitLedgerV2) -> tuple[WorkUnitCostView, list[dict], set[str]]:
    rows = read_rows(ledger_path(budget.base), '工作单元累计成本账')
    selected: list[dict] = []
    owners: set[str] = set()
    for turn in ledger.turns:
        turn_owners: set[str] = set()
        for attempt in turn.attempts:
            if attempt.outcome in ('started', 'unknown', 'interrupted'):
                raise work_units.WorkUnitRefused('内容轮仍有未知或未结束的网络尝试，须先对账')
            row = match_attempt(ledger, turn, attempt, rows)
            if row is None:
                continue
            turn_owners.add(str(row.get('parent_run_id') or turn.run_id))
            selected.append(row)
        if len(turn_owners) > 1:
            raise work_units.WorkUnitRefused('同一内容轮映射到多个 owner')
        owners.update(turn_owners or {turn.run_id})
    usage = summarize(selected)
    return WorkUnitCostView(
        calls=usage.wire_calls, cached_calls=usage.cached_calls,
        failed_calls=usage.failed_calls,
        prompt_tokens=usage.prompt_tokens, completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        missing_usage_calls=usage.missing_usage_calls), selected, owners


def owner_stop_proof(budget: work_units.WorkUnitBudget, ledger: WorkUnitLedgerV2,
                 owners: set[str], cost_rows: list[dict]) -> dict:
    from novel.orchestrator import agent
    run_rows = read_rows(agent.runs_path(budget.base), '工作单元恢复 AgentRun 账')
    runs = agent.fold([AgentRunEvent.model_validate(row) for row in run_rows])
    sessions = agent_session_audit.fold([
        agent_session_audit.AgentSessionEvent.from_dict(row)
        for row in read_rows(agent_session_audit.audit_path(budget.base), '工作单元恢复 session 账')])
    selected_sessions = []
    for turn in ledger.turns:
        cost_owners = {str(row.get('parent_run_id') or turn.run_id) for row in cost_rows
                       if row.get('attempt_id') in {item.attempt_id for item in turn.attempts}}
        for owner in cost_owners - {turn.run_id}:
            matches = [run for run in runs if run.run_id == owner and run.start.sid == ledger.sid
                       and sum(epoch.session_run_id == turn.run_id for epoch in run.epochs) == 1]
            if len(matches) != 1:
                raise work_units.WorkUnitRefused('成本 owner 与原 producer 的 execution epoch 不闭合')
        if not turn.session_run_id:
            if any(item.outcome != 'not_started' for item in turn.attempts):
                raise work_units.WorkUnitRefused('真实调用缺原会话身份，不能证明已安全结束')
            continue
        found = [row for row in sessions if row.session_run_id == turn.session_run_id]
        if (len(found) != 1 or found[0].end is None
                or found[0].start.parent_run_id != turn.run_id
                or found[0].start.role != ledger.role
                or (ledger.role == 'executor' and found[0].start.node != ledger.node)):
            raise work_units.WorkUnitRefused('原内容会话缺可靠结束证明，须先恢复或对账')
        # session 账没有 sid；由成本账的 sid + run + 精确 attempt 闭合归属，
        # 不能为了这里的查询给审计账另造一个未经 producer 填写的字段。
        expected = {row.attempt_id for row in turn.attempts if row.outcome != 'not_started'}
        if not expected <= {row.attempt_id for row in (*found[0].attempts, *found[0].turns)}:
            raise work_units.WorkUnitRefused('原工作单元尝试未完整进入相应 session')
        selected_sessions.append(found[0].end.to_dict())
    relevant_runs = [run for run in runs if run.run_id in owners]
    if any(run.ended is None or run.state in ('running', 'pending', 'paused', 'unknown') for run in relevant_runs):
        raise work_units.WorkUnitRefused('原 owner 尚未安全结束，不能修改工作单元')
    for run in relevant_runs:
        own = [row for row in run_rows if row.get('run_id') == run.run_id]
        if sum(row['kind'] == 'started' for row in own) != 1 or sum(row['kind'] == 'ended' for row in own) != 1:
            raise work_units.WorkUnitRefused('原 owner 的开始或结束记录不唯一')
        for epoch in run.epochs:
            closed = [row for row in own if row['kind'] == 'epoch_closed' and row.get('epoch_id') == epoch.epoch_id]
            if len(closed) != 1 or closed[0].get('drain_state') != 'drained':
                raise work_units.WorkUnitRefused('原 execution epoch 没有可靠排空证明')
    # 其他工作单元的当前 owner 也可能持有同书产物，预览不能承诺可与它并行写入。
    if any(run.start.sid == ledger.sid and run.state in ('running', 'pending', 'paused') for run in runs):
        raise work_units.WorkUnitRefused('本书仍有活动运行，须先等待安全结束')
    job_rows = jobs.records(budget.base)
    if any(row.job_id in owners and (row.ended is None or row.state == 'unknown') for row in job_rows):
        raise work_units.WorkUnitRefused('原作业缺安全终态，不能修改工作单元')
    admission_proof = []
    for owner in sorted(owners):
        snapshot = admission.readonly_snapshot(budget.base, owner)
        if snapshot.active_claims or (snapshot.opened and not snapshot.closed) or beacon.open_executions(budget.base, owner):
            raise work_units.WorkUnitRefused('原 owner 的 claim 或子执行尚未闭合')
        admission_proof.append((owner, snapshot.opened, snapshot.closed, snapshot.terminal_subject_sha256))
    return {'owners': sorted(owners), 'sessions': selected_sessions,
            'runs': [run.model_dump(mode='json') for run in relevant_runs], 'admission': admission_proof}
