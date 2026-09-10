"""按历史完整配置续跑：只读来源预览，启动写锁内再验，不继承旧付费授权。"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from contracts.schema.api import AgentRunContinuationView
from contracts.schema.orchestration import AgentRunEvent, PipelineAgentProfiles, RunContinuationSource
from novel import run_identity, work_units
from novel.errors import TerminalState
from novel.llm.ledger import read_strict_jsonl_objects
from novel.orchestrator import admission, beacon


def inspect(base: str | Path, run_id: str) -> AgentRunContinuationView:
    """缺配置、坏账与未闭合 owner 都展示明确原因，不用今日默认回填。"""
    from novel.orchestrator import agent

    checked = run_identity.check_run_id(run_id)
    try:
        source_path = agent.runs_path(base)
        rows = read_strict_jsonl_objects(source_path, label='原配置续跑来源账') if source_path.is_file() else []
        events = [AgentRunEvent.model_validate(row) for row in rows]
        matches = [row for row in agent.fold(events) if row.run_id == checked]
        if len(matches) != 1:
            raise TerminalState('历史运行不存在或身份不唯一')
        run = matches[0]
        own = [event for event in events if event.run_id == checked]
        if sum(row.kind == 'started' for row in own) != 1 or sum(row.kind == 'ended' for row in own) != 1:
            raise TerminalState('原运行缺唯一开始与结束证明；先完成停止或对账')
        profiles = run.contract.profiles if run.contract is not None else run.start.profiles
        if profiles is None or (run.start.profiles is not None and profiles != run.start.profiles):
            raise TerminalState('原完整 profiles 缺失或与不可变契约冲突，不能猜默认值')
        for epoch in run.epochs:
            closes = [row for row in own if row.kind == 'epoch_closed' and row.epoch_id == epoch.epoch_id]
            if len(closes) != 1 or closes[0].drain_state != 'drained':
                raise TerminalState('原 epoch 尚未证明安全排空，不能另开运行')
        stop = admission.readonly_snapshot(base, checked)
        if stop.active_claims or stop.opened and stop.terminal_kind is None or beacon.open_executions(base, checked):
            raise TerminalState('原 owner 的 admission 或子执行尚未闭合')
        source = RunContinuationSource(run_id=checked, source_sha256=work_units._digest({
            'run': [row.model_dump(mode='json') for row in own], 'stop': asdict(stop),
            'profiles': profiles.model_dump(mode='json')}))
        # 配置尚在契约内但供应商已经不支持时，仍显示历史快照与明确拒因。
        try:
            resolved = agent._freeze_profiles(profiles)
        except TerminalState as exc:
            return AgentRunContinuationView(run_id=checked, sid=run.start.sid, available=False,
                reason=str(exc), profiles=profiles)
        if resolved != profiles:
            raise TerminalState('当前 profile 解析会改变历史配置，拒绝静默改写')
        return AgentRunContinuationView(run_id=checked, sid=run.start.sid, available=True,
            reason='复用原 Commander、默认执行者与逐节点覆盖；重新明确本次两项授权',
            profiles=profiles, source=source)
    except (TerminalState, OSError, ValueError) as exc:
        return AgentRunContinuationView(run_id=checked, available=False, reason=str(exc))


def validate(base: str | Path, sid: str, source: RunContinuationSource,
             profiles: PipelineAgentProfiles | None) -> PipelineAgentProfiles:
    current = inspect(base, source.run_id)
    if (not current.available or current.sid != sid or current.source != source
            or current.profiles is None or profiles != current.profiles):
        raise TerminalState('原配置续跑来源已漂移或不匹配；' + current.reason)
    return current.profiles
