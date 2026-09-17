# -*- coding: utf-8 -*-
"""Stage4A 普通聚合补尾：原配置、原响应与原字节由新免费执行完整复验。

不建立模型池，不重新选择已经封存的来源。重用普通 A 的请求投影、validator、
compiler 和 finalizer；原生产者的费用与实际补提交者的恢复回执各自保留。
"""
from __future__ import annotations

from typing import Any, Mapping

from contracts.schema.orchestration import AgentProfile
from novel import paths
from novel.errors import ArtifactUnreadable, ProvenanceRefused, TerminalState
from novel.llm import run_journal as agent_runs, aggregate_runs, design_adoption, stage4_runs
from novel.llm.ledger import CostLedger, ledger_path
from novel.run import RunContext
from novel.run_identity import check_run_id
from novel.stages.stage4.plan_recovery import PreparedRecovery


def _proof(record: Mapping[str, Any]) -> str:
    original = {**record, 'state': 'prepared', 'recovery': None,
                'committed_at': None, 'landed_payload_sha256s': None,
                'post_file_sha256s': None}
    original['commit_sha256'] = stage4_runs._design_aggregate_hash(original)
    return agent_runs.canonical_sha256(original)


class _SavedDesignSource:
    """只持已通过通用核验的 acceptance；没有任何请求或会话入口。"""
    def __init__(self, model: str):
        self.model = model
        self.acceptances: dict[str, dict[str, Any]] = {}

    def accepted_attempt(self, purpose: str) -> str | None:
        return self.acceptances[purpose]['attempt_id']

    def accepted_models(self, *purposes: str) -> tuple[str, ...]:
        return tuple(sorted({self.acceptances[purpose]['model'] for purpose in purposes}))


def _source_context(ctx: RunContext, record: Mapping[str, Any]) -> RunContext:
    if record['version'] != 2:
        raise ProvenanceRefused('Stage4A 旧聚合缺原 parent 与冻结文件字节，不能跨执行补尾')
    if record['code'] != ctx.code:
        raise ProvenanceRefused('Stage4A prepared 代码身份改变，须保留原代码配对恢复')
    provenance = record['primary_payload']['_provenance']
    source = RunContext(
        novel_dir=ctx.novel_dir, node='stage4.design',
        paid=provenance.get('origin') != 'compiled', code=ctx.code, cache=ctx.cache,
        run_id=record['run_id'], parent_run_id=record['producer_parent_run_id'], readonly=True,
        ledger=CostLedger(path=ledger_path(ctx.novel_dir.parent), run_id=record['run_id'],
                          parent_run_id=record['producer_parent_run_id']), observer=lambda _: None)
    if source.paid:
        # 普通付费聚合即使全部采用也有原请求登记；禁止从当前 settings 取配置。
        unit = paths.stage4_design_unit_run_path(source.novel_dir, source.run_id, None) / 'run.json'
        original = agent_runs._read_json(unit, label='Stage4A 原策略请求')
        try:
            source.executor_profile = AgentProfile.model_validate(original['request_policy']['profile'])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProvenanceRefused('Stage4A 原完整 profile 无法恢复') from exc
        agent_runs._validate_run_record(source, stage4_runs.design_unit_run_spec(None), original)
    return source


def _inspect_source(ctx: RunContext, record: Mapping[str, Any]):
    from novel.stages.stage4 import plan
    source = _source_context(ctx, record)
    stage4_runs.design_repair_recovery_source_parent(
        ctx, source.run_id, expected_parent_run_id=source.parent_run_id)
    prepared = plan.prepare_design_candidate(source, ensure_strategy_unit=False)
    rows = {row['chapter']: row for row in record['adoptions']}
    saved = _SavedDesignSource(record['primary_payload']['_provenance'].get('model', 'saved-unit-adoption'))
    historical: dict[int | None, Any] = {}
    from novel.stages.stage4.design_rebuild import preserved_sources
    preserved = preserved_sources(source, record)
    preserved_by = {row.chapter: row for row in preserved}

    def restore(unit, validator, state_payload):
        if unit.chapter in preserved_by:
            value = (unit.response_model.model_validate(preserved_by[unit.chapter].response_payload)
                     if unit.chapter is None else plan._design_source_response(
                         preserved_by[unit.chapter], next(row for row in chapters if row.chapter_ordinal == unit.chapter)))
            if validator(value, None) is not None:
                raise ProvenanceRefused('Stage4A prepared 保留源不通过当前编译')
            return value
        row = rows[unit.chapter]
        if row['source_run_id'] == source.run_id:
            checkpoint = stage4_runs.restore_design_unit_checkpoint(source, unit, validator=validator)
            if checkpoint is None:
                raise ProvenanceRefused('Stage4A prepared 缺已封存单元 checkpoint')
            saved.acceptances[stage4_runs.design_unit_purpose(unit.chapter)] = checkpoint.acceptance
        else:
            previous = design_adoption.restore(source, unit, row['source_run_id'],
                                               validator=validator, state_payload=state_payload)
            historical[unit.chapter] = previous
            checkpoint = previous.checkpoint
        if (checkpoint.revision_key != 'terminal'
                or checkpoint.checkpoint_sha256 != row['checkpoint_sha256']
                or checkpoint.acceptance['attempt_id'] != row['attempt_id']
                or checkpoint.state_payload != state_payload(checkpoint.value)):
            raise ProvenanceRefused('Stage4A prepared 的单元响应或确定性状态漂移')
        return checkpoint.value

    strategy = restore(plan._design_strategy_unit(prepared), prepared.validator,
                       plan._design_strategy_checkpoint_state)
    chapters = plan._design_chapter_contexts(source, prepared, strategy, ensure_units=False)
    if set(rows) & set(preserved_by) or set(rows) | set(preserved_by) != {None, *(chapter.chapter_ordinal for chapter in chapters)}:
        raise ProvenanceRefused('Stage4A prepared 不完整覆盖当前策略与章节')
    responses = []
    for chapter in chapters:
        def compiled_state(value, current=chapter):
            why = current.validator(value, None)
            if why is not None or current.validator.compiled is None:
                raise ProvenanceRefused(f'Stage4A prepared 当前编译失败：{why}')
            return plan._design_chapter_checkpoint_state(value, current.validator.compiled)
        response = restore(plan._design_chapter_unit(chapter), chapter.validator, compiled_state)
        responses.append((chapter.chapter_ordinal, response))
    result = plan.DesignCandidateResponses(strategy, tuple(responses), tuple(historical.items()), preserved)
    evaluation = plan.evaluate_design_candidate(source, saved, prepared, result)
    if (evaluation.candidate.model_dump(by_alias=True, mode='json') != record['primary_payload']
            or plan._design_finalization_payload(prepared, evaluation.candidate) != record['finalization_payload']):
        raise ProvenanceRefused('Stage4A prepared 与当前完整 compiler/来源账/终验不等')
    with stage4_runs.commit_ownership(source.novel_dir):
        current = stage4_runs.design_aggregate_state(source)
        if current != record:
            raise ProvenanceRefused('Stage4A prepared 在预览中漂移')
        stage4_runs._require_design_aggregate_adoptions(source, record,
                                                       require_present=record['state'] == 'committed')
        stage4_runs.design_aggregate_target_state(
            source, record, plan.CANDIDATE_REL, expected_file_sha256=record['primary_file_sha256'])
    return source, prepared, saved, evaluation


def inspect_prepared_recovery(ctx: RunContext) -> PreparedRecovery | None:
    try:
        pending = []
        for path in stage4_runs._stage4_history_files(
                ctx, f'*/design/{stage4_runs.DESIGN_AGGREGATE_COMMIT_FILE}'):
            record = stage4_runs._validate_foreign_design_aggregate_envelope(
                path, run_id=path.parent.parent.name, sid=ctx.sid)
            if record['state'] == 'prepared':
                pending.append(record)
        if not pending:
            return None
        if len(pending) != 1:
            raise ProvenanceRefused('Stage4A 存在多份 prepared，不能猜恢复顺序')
        record = pending[0]
        _inspect_source(ctx, record)
        return PreparedRecovery('recoverable', record['run_id'], _proof(record))
    except (ArtifactUnreadable, ProvenanceRefused, TerminalState, OSError, ValueError) as exc:
        return PreparedRecovery('unknown', reason=str(exc))


def recover_prepared(ctx: RunContext, *, source_run_id: str, proof_sha256: str):
    from novel.stages.stage4 import plan
    source_id = check_run_id(source_run_id)
    if ctx.readonly or ctx.run_id == source_id:
        raise ProvenanceRefused('跨执行补尾必须使用新的免费 committer')
    stage4_runs.require_design_repair_recovery_target_clean(ctx)
    path = paths.stage4_design_aggregate_path(ctx.novel_dir, source_id)
    record = stage4_runs._validate_foreign_design_aggregate_envelope(
        path, run_id=source_id, sid=ctx.sid)
    if not agent_runs.is_sha256(proof_sha256) or _proof(record) != proof_sha256:
        raise ProvenanceRefused('Stage4A prepared 恢复凭据漂移')
    with stage4_runs.require_design_repair_recovery_source_stopped(
            ctx, source_id, producer_parent_run_id=record['producer_parent_run_id']):
        with stage4_runs.commit_ownership(ctx.novel_dir):
            source, prepared, saved, evaluation = _inspect_source(ctx, record)
            source.readonly = False
            return plan.finalize_design_candidate(source, saved, prepared, evaluation, committer=ctx)
