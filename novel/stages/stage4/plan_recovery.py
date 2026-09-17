# -*- coding: utf-8 -*-
"""Stage4B 聚合恢复：纯读发现、精确来源锁定、独立 committer 补尾。

不拥有第二套 compiler、来源采用或业务判据。只调用 plan 的同一候选预览与
finalizer；原 run 只表示已购买成果的身份，实际补尾执行另入聚合恢复审计。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from novel import paths
from novel.errors import ArtifactUnreadable, ProvenanceRefused, TerminalState
from novel.llm import run_journal as agent_runs, aggregate_runs, stage4_runs
from novel.llm.ledger import CostLedger, ledger_path, read_strict_audit_events
from novel.run import RunContext
from novel.run_identity import check_run_id


@dataclass(frozen=True)
class PreparedRecovery:
    state: Literal['recoverable', 'complete', 'unknown']
    source_run_id: str | None = None
    proof_sha256: str | None = None
    reason: str | None = None


def _proof(record: Mapping[str, Any]) -> str:
    # 只抹去合法提交后继字段，使 prepared→committed 后同一凭据可幂等核验。
    # 主体、来源、写前基线、原 parent 与原 prepared 时间均保留。
    original = {**record, 'state': 'prepared', 'recovery': None,
                'committed_at': None, 'landed_payload_sha256s': None,
                'post_file_sha256s': None}
    original['commit_sha256'] = aggregate_runs._hash(original)
    return agent_runs.canonical_sha256(original)


def _source_context(ctx: RunContext, record: Mapping[str, Any]) -> RunContext:
    from novel.stages.stage4 import plan
    source = RunContext(
        novel_dir=ctx.novel_dir, node='stage4.plan', paid=False, code=ctx.code,
        cache=ctx.cache, run_id=record['run_id'],
        parent_run_id=record['producer_parent_run_id'], readonly=True,
        ledger=CostLedger(path=ledger_path(ctx.novel_dir.parent),
                          run_id=record['run_id'], parent_run_id=record['producer_parent_run_id']),
        observer=lambda _event: None)
    plan._restore_plan_original_profile(source)
    return source


def _inspect_source(ctx: RunContext, record: Mapping[str, Any]):
    """零写盘重建完整 typed bundle，并验证所有 live 路径仅为 baseline/expected。"""
    from novel.stages.stage4 import plan
    if record['code'] != ctx.code:
        raise ProvenanceRefused('Stage4B prepared 的代码身份改变，须保留原代码配对恢复')
    source = _source_context(ctx, record)
    stage4_runs.design_repair_recovery_source_parent(
        source, source.run_id, expected_parent_run_id=source.parent_run_id)
    prepared = plan.prepare_plan_candidate(source)
    pool = plan._PlanRecoveryPool(source)
    preview = plan._run_saved_plan_reaggregate(source, pool, prepared)
    if not isinstance(preview, plan._PlanAggregatePreview):
        raise ProvenanceRefused('Stage4B 只读预览越过了聚合边界')
    evaluation = preview.evaluation
    spec = plan._plan_commit_spec(evaluation, ctx=source, source=preview.source, prepared=prepared)
    current = aggregate_runs.commit_state(source, spec)
    if current != record:
        raise ProvenanceRefused('Stage4B 来源预览期间聚合 journal 漂移')
    finalization = plan._plan_finalization_payload(
        source, prepared, evaluation.binding, evaluation.compiled, hard_hits=evaluation.hard_hits)
    if record['finalization_payload'] != finalization:
        raise ProvenanceRefused('Stage4B 当前 compiler/判据/保留文件与冻结终态不等')
    plan._preclaim_plan_adoptions(source, preview.source, evaluation.binding,
                                 require_present=record['state'] == 'committed')
    aggregate_runs.preclaim_adoption(source, record)
    aggregate_runs.target_state(source, spec, record, plan.BINDING_REL,
                                expected_file_sha256=record['primary_file_sha256'])
    if evaluation.compiled is not None:
        # 原模型用量属于 Binding；预览也重放相同的内存结算边界，不能把
        # restore 认领的历史调用再灌进确定性 Plan 的 provenance。
        plan._materialize_binding(
            source, preview.source, prepared, evaluation.response, settle=True,
            architecture=evaluation.architecture, unit_lineage=evaluation.unit_lineage)
        with prepared.store.preview_dependency(plan.BINDING_REL, evaluation.binding):
            artifact = plan._compiled_plan_artifact(
                source, prepared, evaluation.compiled,
                binding_file_sha256=record['primary_file_sha256'])
        if evaluation.hard_hits:
            expected = agent_runs.canonical_sha256(plan._render_expected_invalid_bytes(prepared.store, artifact))
            aggregate_runs.target_state(source, spec, record, plan._INVALID, expected_file_sha256=expected)
        else:
            expected = agent_runs.canonical_sha256(prepared.store._render(plan.REL, artifact))
            aggregate_runs.target_state(source, spec, record, plan.REL, expected_file_sha256=expected)
            aggregate_runs.target_state(source, spec, record, plan._INVALID, expected_absent=True)
    return source, preview


def inspect_prepared_recovery(ctx: RunContext) -> PreparedRecovery | None:
    """供 queue/operator 的唯一只读 producer 判定；损坏显式 unknown。"""
    try:
        pending = []
        for path in stage4_runs._stage4_history_files(
                ctx, f'*/plan/{stage4_runs.PLAN_AGGREGATE_COMMIT_FILE}'):
            record = aggregate_runs.validate_envelope(
                path, run_id=path.parent.parent.name, sid=ctx.sid, node='stage4.plan')
            if record['state'] == 'prepared':
                pending.append(record)
        if not pending:
            return None
        if len(pending) != 1:
            raise ProvenanceRefused('Stage4B 同时有多份 prepared，不能猜恢复顺序')
        record = pending[0]
        _inspect_source(ctx, record)
        return PreparedRecovery('recoverable', record['run_id'], _proof(record))
    except (ArtifactUnreadable, ProvenanceRefused, TerminalState, OSError, ValueError) as exc:
        return PreparedRecovery('unknown', reason=str(exc))


def reaggregate_available(ctx: RunContext) -> bool:
    """仅当同一 compiler 证明全部章已机械确定/有完整来源，才发布免费动作。"""
    from novel.stages.stage4 import plan
    if not ctx.readonly or ctx.paid:
        raise ProvenanceRefused('免费聚合可用性查询必须使用只读上下文')
    # state 的只读账默认 path=None；来源核验需要读盘上费用证明，但仍不能
    # 沿用/污染调用方的内存来源窗口，也不能追加任何 audit 或工作单元状态。
    ctx = RunContext(
        novel_dir=ctx.novel_dir, node=ctx.node, paid=False, code=ctx.code,
        cache=ctx.cache, readonly=True,
        ledger=CostLedger(path=ledger_path(ctx.novel_dir.parent)), observer=lambda _event: None)
    try:
        if ctx.node == 'stage4.design':
            from contracts.artifacts import STAGE4_BLIND_CANDIDATE
            from novel.store import ArtifactStore
            if (not stage4_runs._plan_source_run_ids(ctx)
                    and not ArtifactStore(ctx).exists(STAGE4_BLIND_CANDIDATE)):
                return False
            return isinstance(plan.run_design_reaggregate(ctx), plan.DesignCandidateEvaluation)
        prepared = plan.prepare_plan_candidate(ctx)
        preview = plan._run_saved_plan_reaggregate(
            ctx, plan._PlanRecoveryPool(ctx), prepared)
    except TerminalState:
        return False
    if not isinstance(preview, plan._PlanAggregatePreview):
        raise ProvenanceRefused('免费聚合预览未返回完整检查结果')
    return preview.evaluation.passed


def recover_prepared(ctx: RunContext, *, source_run_id: str, proof_sha256: str):
    """新免费执行接管已停止 producer；原账与实际 committer 始终各保身份。"""
    from novel.stages.stage4 import plan
    source_id = check_run_id(source_run_id)
    if ctx.node != 'stage4.plan' or ctx.paid or ctx.readonly or source_id == ctx.run_id:
        raise ProvenanceRefused('跨执行补尾必须是新的免费 Stage4B committer')
    if ctx.executor_profile is not None or ctx.executor_backend is not None:
        raise ProvenanceRefused('跨执行补尾禁止 executor 配置与模型客户端')
    if not agent_runs.is_sha256(proof_sha256):
        raise ProvenanceRefused('聚合恢复必须绑定完整来源证明 SHA-256')
    path = paths.stage4_plan_aggregate_path(ctx.novel_dir, source_id)
    record = aggregate_runs.validate_envelope(path, run_id=source_id, sid=ctx.sid, node=ctx.node)
    if _proof(record) != proof_sha256:
        raise ProvenanceRefused('聚合恢复凭据与 source journal 不等')
    with stage4_runs.require_design_repair_recovery_source_stopped(
            ctx, source_id, producer_parent_run_id=record['producer_parent_run_id']):
        with stage4_runs.commit_ownership(ctx.novel_dir):
            source, preview = _inspect_source(ctx, record)
            if record['state'] == 'prepared':
                stage4_runs._require_design_repair_recovery_target_identity_clean(ctx, ctx.run_id)
                read_strict_audit_events(ctx.ledger)
            source.readonly = False
            pool = preview.source._pool
            pool.committer = ctx
            # 先补回已验证的原工作单元关联；不恢复 provider 进程/会话。
            for chapter, unit in sorted(preview.source._units.items()):
                if unit.source_context is None:
                    pool.restore_chapter(chapter, unit.request, validator=None)
            return plan.finalize_plan_candidate(source, preview.source, preview.prepared, preview.evaluation)
