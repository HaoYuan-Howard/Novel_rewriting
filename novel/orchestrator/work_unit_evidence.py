"""工作单元管理共用的精确成本、会话及 owner 停止证明；全部只读。"""
from __future__ import annotations

from pathlib import Path
from contracts.schema.api import WorkUnitCostView
from contracts.schema.orchestration import AgentRunEvent
from contracts.schema.workflow import WorkUnitLedgerV3
from novel import paths, work_units
from novel.errors import PathRefused
from novel.llm import session_audit as agent_session_audit
from novel.llm.ledger import ledger_path, read_strict_jsonl_objects
from novel.orchestrator import admission, beacon, jobs
from novel.work_unit_usage import match_attempt, summarize


def read_rows(path: Path, label: str) -> list[dict]:
    return read_strict_jsonl_objects(path, label=label) if path.is_file() else []


def load_budget(base: str | Path, sid: str, unit_id: str) -> work_units.WorkUnitBudget:
    path = paths.work_unit_dir(base, sid, unit_id) / 'ledger.json'
    ledger_sid = sid
    if not path.is_file():
        # 现役 chain 的额度键是隔离根目录名，API/AgentRun 则可用短 SID。
        # 只通过唯一书目录解析别名，不按标题或字符串后缀猜同书，也不重写旧账。
        book = _book_directory(base, sid)
        if book is not None:
            ledger_sid = book.name
            path = paths.work_unit_dir(base, ledger_sid, unit_id) / 'ledger.json'
    ledger = work_units._read(base, path, WorkUnitLedgerV3)
    if ledger.sid != ledger_sid:
        raise work_units.WorkUnitRefused('工作单元账所属书键与持久路径不符')
    budget = work_units.WorkUnitBudget(base, ledger.sid, ledger.node, ledger.role, ledger.scope)
    if budget.unit_id != unit_id or unit_id not in budget._registry().unit_ids:
        raise work_units.WorkUnitRefused('工作单元未登记或身份漂移')
    budget._read()
    return budget


def _book_directory(base: str | Path, sid: str) -> Path | None:
    try:
        return paths.find_novel_dir(base, sid)
    except PathRefused as exc:
        raise work_units.WorkUnitRefused(f'工作单元所属书目录不可唯一定位：{exc}') from exc


def _same_book(base: str | Path, left: str, right: str) -> bool:
    """执行身份可以有短 SID/目录名两种引用，实际书目录必须唯一且相同。"""
    if left == right:
        return True
    left_book = _book_directory(base, left)
    right_book = _book_directory(base, right)
    return (left_book is not None and right_book is not None
            and left_book.resolve() == right_book.resolve())


def cost_evidence(budget: work_units.WorkUnitBudget, ledger: WorkUnitLedgerV3) -> tuple[WorkUnitCostView, list[dict], set[str]]:
    rows = read_rows(ledger_path(budget.base), '工作单元累计成本账')
    selected: list[dict] = []
    owners: set[str] = set()
    for turn in ledger.turns:
        turn_owners: set[str] = set()
        for attempt in turn.attempts:
            # 已收口轮（轮级 marker；receipt 在每次 `_read` 复验）容忍其历史
            # `unknown` attempt——executor 确定性拒收在旧 wire 下如此记录
            # （R10c 2026-10-02），收口后不再阻塞对账/追加。
            if attempt.outcome in ('started', 'interrupted') or (
                    attempt.outcome == 'unknown'
                    and turn.reason_code != work_units.COMMANDER_FAILURE_RECONCILED):
                raise work_units.WorkUnitRefused('内容轮仍有未知或未结束的网络尝试，须先对账')
            row = match_attempt(ledger, turn, attempt, rows)
            if row is None:
                continue
            # R8a-5：归属按 reserve 时记录的执行身份（agent 流＝AgentRun run），
            # 不再读成本行 parent_run_id——那是候选根 epoch 会话，不是 AgentRun。
            turn_owners.add(turn.agent_run_id or turn.run_id)
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


def rebuild_budget_views(base: str | Path, ledger_sid: str,
                         nodes: tuple[str, ...]) -> list[dict]:
    """指定节点在本书的工作单元内容轮额度投影（`unit_view` 同一把尺；无账空表）。

    升级预览用它在应用前回答「重建第一步会不会撞额度」：额度是易变状态，
    只随预览展示、不进被 subject_sha256 覆盖的计划（否则节点一跑预览就漂移）。
    """
    units_dir = paths.work_units_dir(base, ledger_sid)
    if not units_dir.is_dir():
        return []
    out: list[dict] = []
    wanted = set(nodes)
    for ledger_file in sorted(units_dir.glob('*/ledger.json')):
        ledger = work_units._read(base, ledger_file, WorkUnitLedgerV3)
        if ledger.node in wanted:
            out.append(work_units.unit_view(ledger))
    return out


def owner_stop_proof(budget: work_units.WorkUnitBudget, ledger: WorkUnitLedgerV3,
                 owners: set[str], cost_rows: list[dict]) -> dict:
    """追加额度前的 owner 停止证明（R8a-5 重写归属轴）。

    归属按内容轮 reserve 时**记录**的执行身份三格闭合：agent 流的轮记录
    ``agent_run_id``（＝producer run＝成本行 ``run_id``）、``agent_epoch_id`` 与
    候选根 epoch 会话（成本行 ``parent_run_id`` 的真身）——证明即验证记录的
    关系成立：AgentRun 在账、sid 相符、epoch 属于它、成本行两枚身份与记录
    逐行一致。历史轮次（三格为空，R8a 之前写下的）按「成本行 run_id＝
    turn.run_id＝已结束且排空的 AgentRun」闭合；无 AgentRun 外层的手敲直连轮
    沿用终态豁免。此前把 ``parent_run_id`` 当 AgentRun id 对账，agent 流的
    追加额度证明永不闭合（canary 20260929b 实证）。
    """
    from novel.orchestrator import agent
    run_rows = read_rows(agent.runs_path(budget.base), '工作单元恢复 AgentRun 账')
    runs = agent.fold([AgentRunEvent.replay_validate(row) for row in run_rows])
    sessions = agent_session_audit.fold([
        agent_session_audit.AgentSessionEvent.from_dict(row)
        for row in read_rows(agent_session_audit.audit_path(budget.base), '工作单元恢复 session 账')])
    selected_sessions = []
    for turn in ledger.turns:
        turn_attempts = {item.attempt_id for item in turn.attempts}
        rows_for_turn = [row for row in cost_rows
                         if row.get('attempt_id') in turn_attempts]
        if turn.agent_run_id:
            matches = [run for run in runs if run.run_id == turn.agent_run_id
                       and _same_book(budget.base, run.start.sid, ledger.sid)]
            if len(matches) != 1:
                raise work_units.WorkUnitRefused(
                    f'内容轮记录的 AgentRun {turn.agent_run_id} 不在账上或 sid 不符'
                    '（agent 流执行身份不闭合）')
            if turn.agent_run_id != turn.run_id:
                raise work_units.WorkUnitRefused(
                    '内容轮记录的 AgentRun 身份与 producer run 不一致（单元账损坏）')
            if turn.agent_epoch_id not in {e.epoch_id
                                           for e in matches[0].epochs}:
                raise work_units.WorkUnitRefused(
                    f'内容轮记录的 execution epoch 不属于 AgentRun '
                    f'{turn.agent_run_id}（执行身份不闭合）')
            for row in rows_for_turn:
                if str(row.get('parent_run_id') or '') \
                        != turn.candidate_session_run_id:
                    raise work_units.WorkUnitRefused(
                        '内容轮成本行 parent_run_id 与记录的候选根 epoch 会话'
                        '不闭合（串账）')
            # 终态与排空由下方 relevant_runs 段按 owner 统一证明。
            continue
        if not turn.session_run_id:
            # R8a-5 历史轮次：agent 流在 R8a 之前写下的轮没有三格，但它的
            # producer run（成本行 run_id）就是 AgentRun run id——同 sid 唯一
            # 命中即按「已结束且排空」闭合（同样由 relevant_runs 段证明）。
            legacy_runs = [run for run in runs if run.run_id == turn.run_id]
            if len(legacy_runs) > 1:
                raise work_units.WorkUnitRefused('同一 producer run 命中多条 AgentRun（账损坏）')
            if legacy_runs:
                if not _same_book(budget.base, legacy_runs[0].start.sid, ledger.sid):
                    raise work_units.WorkUnitRefused('历史轮记录的 AgentRun 属于另一书，执行身份不闭合')
                continue
            # 手敲直连轮（无 AgentRun 外层）：内容轮已终态失败时仍须允许人工
            # 追加，否则 CLI 耗尽 4 次后会被永久卡住。在途/未知仍 fail-closed；
            # 已收口轮的历史 unknown attempt 例外（同 cost_evidence，R10c）。
            if turn.state in ('failed', 'rejected'):
                if any(item.outcome in ('started', 'interrupted') or (
                        item.outcome == 'unknown'
                        and turn.reason_code != work_units.COMMANDER_FAILURE_RECONCILED)
                       for item in turn.attempts):
                    raise work_units.WorkUnitRefused(
                        '内容轮仍有未知或未结束的网络尝试，须先对账')
                continue
            if turn.state == 'adopted':
                # 采用＝CommitBundle 封存引用（终态且证据闭合），手敲轮同样
                # 安全（R10c c1：手工 CLI 重放的轮无 agent 身份三格，但答案
                # 已被正式产物采用，不可能再写）。
                continue
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
    #: 已被人工 reconcile 封印的 run（lifecycle 末事件 reconciled）：按设计不
    #: 补造 ended/epoch_closed（fold 保持 unknown），但 admission seal 已闭、
    #: 不可能再写盘——「活动运行」闸不得把它们永久算活，否则崩溃+收口后的
    #: 根再也无法追加额度（R10c c1 实证：两次服务重启产生两条 reconciled
    #: 僵尸，grant 面死锁）。
    reconciled_runs = {
        row.get('run_id') for row in run_rows
        if row.get('kind') == 'reconciled'}
    if any(run.ended is None or run.state in ('running', 'pending', 'paused', 'unknown')
           for run in relevant_runs if run.run_id not in reconciled_runs):
        raise work_units.WorkUnitRefused('原 owner 尚未安全结束，不能修改工作单元')
    for run in relevant_runs:
        if run.run_id in reconciled_runs:
            continue  # 封印 run 无 ended/epoch_closed 可验；写入不可能性由 seal 保证。
        own = [row for row in run_rows if row.get('run_id') == run.run_id]
        if sum(row['kind'] == 'started' for row in own) != 1 or sum(row['kind'] == 'ended' for row in own) != 1:
            raise work_units.WorkUnitRefused('原 owner 的开始或结束记录不唯一')
        for epoch in run.epochs:
            closed = [row for row in own if row['kind'] == 'epoch_closed' and row.get('epoch_id') == epoch.epoch_id]
            if len(closed) != 1 or closed[0].get('drain_state') != 'drained':
                raise work_units.WorkUnitRefused('原 execution epoch 没有可靠排空证明')
    # 其他工作单元的当前 owner 也可能持有同书产物，预览不能承诺可与它并行写入。
    if any((run.ended is None or run.state in ('running', 'pending', 'paused', 'unknown'))
           and run.run_id not in reconciled_runs
           and _same_book(budget.base, run.start.sid, ledger.sid) for run in runs):
        raise work_units.WorkUnitRefused('本书仍有活动运行，须先等待安全结束')
    job_rows = jobs.records(budget.base)
    if any(row.job_id in owners and (row.ended is None or row.state == 'unknown') for row in job_rows):
        raise work_units.WorkUnitRefused('原作业缺安全终态，不能修改工作单元')
    admission_proof = []
    for owner in sorted(owners):
        snapshot = admission.readonly_snapshot(budget.base, owner)
        # 封印（reconciled）owner 的孤儿信标按 seal 语义视为已闭合：reconcile
        # 不补造 end 事件，孤儿信标没有收口面，仍算活会把该 owner 名下的
        # 工作单元永久锁死（与上面的活动运行闸同一缺陷面，R10c）。
        sealed = owner in reconciled_runs
        if (snapshot.active_claims or (snapshot.opened and not snapshot.closed)
                or (not sealed and beacon.open_executions(budget.base, owner))):
            raise work_units.WorkUnitRefused('原 owner 的 claim 或子执行尚未闭合')
        admission_proof.append((owner, snapshot.opened, snapshot.closed, snapshot.terminal_subject_sha256))
    return {'owners': sorted(owners), 'sessions': selected_sessions,
            'runs': [run.model_dump(mode='json') for run in relevant_runs], 'admission': admission_proof}
