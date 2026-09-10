# -*- coding: utf-8 -*-
"""Stage4A 普通策略/章单元的精确历史采用。

请求由 producer 构造，回答、session、checkpoint 和完整成本证据由通用 core
复验。每个单元独立冻结来源，因此互补的历史运行可共同完成一部书；冲突回答
保持 unknown，不用时间或命中数量代替裁决。历史会话不进入当前模型池。
"""
from __future__ import annotations

from novel import runtime_layout
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel

from novel import paths
from novel.appendlog import process_append_lock
from novel.errors import ArtifactUnreadable, ProvenanceRefused
from novel.llm import agent_runs, stage4_runs
from novel.llm.ledger import ledger_path, read_strict_jsonl_objects


def pending_response(ctx: Any, unit: stage4_runs.DesignUnitRequest, source_run_id: str):
    """只读定位本单元原回答；有回答却无 checkpoint 不能当作未执行。"""
    from novel.work_units import budget_for, executor_scope
    budget = budget_for(ctx, scope=executor_scope(
        ctx.node, stage4_runs.design_unit_purpose(unit.chapter), {'chapter': unit.chapter}))
    turns = [row for row in budget._read().turns if row.run_id == source_run_id and row.response_sha256]
    if not turns:
        return None
    turn = turns[-1]
    if turn.state == 'failed':
        return None
    if turn.state == 'adopted':
        raise ProvenanceRefused('Stage4A 已采纳回答的 checkpoint 缺失；来源保持未知')
    raw = budget.response_payload(turn.turn_id)
    return budget, turn, raw


def recover_response_checkpoint(ctx: Any, unit: stage4_runs.DesignUnitRequest, source_run_id: str,
                                *, validator: Callable, state_payload: Callable) -> bool:
    """在原 producer 身份下补 checkpoint，再由正常历史采用读取；零 driver。"""
    pending = pending_response(ctx, unit, source_run_id)
    if pending is None:
        return False
    if ctx.readonly:
        return False
    from novel.agents.session_client import SessionAgentClient
    from novel.observability import RequestProgress
    from novel.run import RunContext
    from novel.llm.ledger import CostLedger
    _budget, _turn, raw = pending
    receipt = raw.get('prepared_receipt')
    if not isinstance(receipt, dict) or receipt.get('run_id') != source_run_id:
        raise ProvenanceRefused('Stage4A 原回答缺 producer/费用回执')
    historical, _record, _path = agent_runs._historical_run_context(
        ctx, stage4_runs.design_unit_run_spec(unit.chapter), source_run_id=source_run_id,
        ledger_source_path=ledger_path(ctx.novel_dir.parent))
    if historical.code != ctx.code:
        raise ProvenanceRefused('Stage4A 未 checkpoint 的原回答须按原代码恢复')
    source = RunContext(novel_dir=ctx.novel_dir, node=ctx.node, paid=False, code=historical.code,
        run_id=source_run_id, parent_run_id=receipt['parent_run_id'], cache=ctx.cache,
        executor_profile=historical.executor_profile,
        ledger=CostLedger(path=ledger_path(ctx.novel_dir.parent), run_id=source_run_id,
                          parent_run_id=receipt['parent_run_id']))
    with stage4_runs.require_design_repair_recovery_source_stopped(
            ctx, source_run_id, producer_parent_run_id=source.parent_run_id):
        # 两个恢复者可能先后拿到 owner 停止锁；后拿锁者必须重新读取，不能
        # 对已采纳回答再造 checkpoint，也不能采用预览之后换掉的响应。
        if stage4_runs._source_has_design_checkpoint(ctx, source_run_id, unit.chapter):
            restore(ctx, unit, source_run_id, validator=validator, state_payload=state_payload)
            return True
        current = pending_response(ctx, unit, source_run_id)
        if current is None or current[1] != _turn or current[2] != raw:
            raise ProvenanceRefused('Stage4A 原回答在恢复锁内复验时漂移')
        match = agent_runs.match_historical_request(
            ctx, stage4_runs.design_unit_run_spec(unit.chapter), source_run_id=source_run_id,
            input_sha256=unit.input_sha256, system=unit.system, user=unit.user,
            response_model=unit.response_model, max_tokens=unit.max_tokens,
            use_source_profile=ctx.paid is False)
        if not match.matches:
            raise ProvenanceRefused('Stage4A 原回答的实际请求已不适用')
        client = SessionAgentClient(source, historical.executor_profile, None, chapter=unit.chapter)
        try:
            response = client.ask_json(unit.system, unit.user, model_cls=unit.response_model,
                purpose=stage4_runs.design_unit_purpose(unit.chapter), max_tokens=unit.max_tokens,
                validator=validator, recovery_policy=stage4_runs.DESIGN_UNIT_RECOVERY_POLICY,
                progress=(None if unit.chapter is None else RequestProgress(chapter=unit.chapter, index=1, total=1)))
            stage4_runs.write_design_unit_checkpoint(source, unit,
                response_payload=response.model_dump(mode='json', by_alias=True),
                acceptance=client.accepted_checkpoint(stage4_runs.design_unit_purpose(unit.chapter)),
                state_payload=state_payload(response))
            client.finish(process_state='ok', delivery_state='blocked')
        finally:
            client.close()
    return True


def source_row(unit: stage4_runs.DesignUnitRequest,
               source: agent_runs.RestoredHistoricalRun[Any]) -> dict[str, Any]:
    checkpoint = source.checkpoint
    acceptance = checkpoint.acceptance
    return {
        'chapter': unit.chapter, 'source_run_id': source.context.run_id,
        'source_code': source.context.code, 'run_sha256': source.run_sha256,
        'checkpoint_revision': checkpoint.revision,
        'checkpoint_sha256': checkpoint.checkpoint_sha256,
        'response_payload_sha256': agent_runs.canonical_sha256(
            checkpoint.value.model_dump(by_alias=True, mode='json')),
        'attempt_id': acceptance['attempt_id'], 'model': acceptance['model'],
        'request_identity': source.context.ledger.request_identity_for_attempt(acceptance['attempt_id']),
    }


def _request_row(unit: stage4_runs.DesignUnitRequest) -> dict[str, Any]:
    return stage4_runs._design_repair_request_row(unit)


def restore(ctx: Any, unit: stage4_runs.DesignUnitRequest, source_run_id: str, *,
            validator: Callable | None = None,
            state_payload: Callable[[BaseModel], Mapping[str, Any]] | None = None,
            ) -> agent_runs.RestoredHistoricalRun[Any]:
    spec = stage4_runs.design_unit_run_spec(unit.chapter)
    match = agent_runs.match_historical_request(
        ctx, spec, source_run_id=source_run_id, input_sha256=unit.input_sha256,
        system=unit.system, user=unit.user, response_model=unit.response_model,
        max_tokens=unit.max_tokens, use_source_profile=ctx.paid is False)
    if not match.matches:
        raise ProvenanceRefused('Stage4A 已选历史来源与当前实际请求漂移')
    result = agent_runs.restore_historical_run(
        ctx, spec, source_run_id=source_run_id, input_sha256=unit.input_sha256,
        system=unit.system, user=unit.user, response_model=unit.response_model,
        max_tokens=unit.max_tokens, validator=validator,
        ledger_source_path=ledger_path(ctx.novel_dir.parent))
    if result.checkpoint.revision_key != 'terminal':
        raise ProvenanceRefused('Stage4A 历史来源不是完成的单元响应')
    if state_payload is not None and result.checkpoint.state_payload != dict(
            state_payload(result.checkpoint.value)):
        raise ProvenanceRefused('Stage4A 历史来源的确定性编译状态漂移')
    return result


def select(ctx: Any, unit: stage4_runs.DesignUnitRequest, *, validator: Callable,
           state_payload: Callable[[BaseModel], Mapping[str, Any]],
           persist: bool = True) -> agent_runs.RestoredHistoricalRun[Any] | None:
    """在新 wire 前选择并冻结；损坏的候选来源不能降级成未命中。"""
    cost_path = ctx.ledger.path or ledger_path(ctx.novel_dir.parent)
    if cost_path.is_file():
        read_strict_jsonl_objects(cost_path, label='Stage4A 历史来源成本账')
    audit_path = cost_path.with_name('llm_audit.jsonl')
    if audit_path.is_file():
        read_strict_jsonl_objects(audit_path, label='Stage4A 历史来源审计账')
    path = paths.stage4_design_unit_adoption_path(ctx.novel_dir, ctx.run_id, unit.chapter)
    identity = {'version': 1, 'kind': 'stage4-design-unit-adoption/v1',
                'target_run_id': ctx.run_id, 'sid': ctx.sid, 'node': ctx.node,
                'code': ctx.code, 'request': _request_row(unit)}

    def note_selected(evidence: str) -> None:
        if persist and not ctx.readonly:
            from novel.work_units import budget_for, executor_scope
            budget_for(ctx, scope=executor_scope(ctx.node,
                stage4_runs.design_unit_purpose(unit.chapter), {'chapter': unit.chapter})).note_processing(
                    run_id=ctx.run_id, input_sha256=unit.input_sha256,
                    kind='history_adopted', evidence_sha256=evidence)

    def read_selected() -> agent_runs.RestoredHistoricalRun[Any]:
        record = agent_runs._read_json(path, label='Stage4A 历史采用')
        if (set(record) != {*identity, 'source', 'manifest_sha256'}
                or any(record.get(key) != value for key, value in identity.items())
                or record['manifest_sha256'] != agent_runs.canonical_sha256({
                    key: value for key, value in record.items() if key != 'manifest_sha256'})
                or not isinstance(record['source'], dict)):
            raise ArtifactUnreadable('Stage4A 历史采用身份或来源清单损坏')
        source = restore(ctx, unit, record['source'].get('source_run_id', ''),
                         validator=validator, state_payload=state_payload)
        if record['source'] != source_row(unit, source):
            raise ProvenanceRefused('Stage4A 已冻结历史来源发生漂移')
        note_selected(record['manifest_sha256'])
        return source

    if path.is_file():
        return read_selected()
    candidates = []
    for run_id in stage4_runs._plan_source_run_ids(ctx):
        if not any((root / run_id / 'design' / 'units' / paths.stage4_design_unit_dirname(unit.chapter)
                    / 'run.json').is_file() for root in runtime_layout.stage_run_roots(ctx.novel_dir, 'stage4')):
            continue
        match = agent_runs.match_historical_request(
            ctx, stage4_runs.design_unit_run_spec(unit.chapter), source_run_id=run_id,
            input_sha256=unit.input_sha256, system=unit.system, user=unit.user,
            response_model=unit.response_model, max_tokens=unit.max_tokens,
            use_source_profile=ctx.paid is False)
        if not match.matches:
            continue
        if not stage4_runs._source_has_design_checkpoint(ctx, run_id, unit.chapter):
            if not persist:
                pending_response(ctx, unit, run_id)
                continue
            if not recover_response_checkpoint(ctx, unit, run_id, validator=validator, state_payload=state_payload):
                continue
        candidates.append(restore(ctx, unit, run_id, validator=validator, state_payload=state_payload))
    if not candidates:
        return None
    payloads = {source_row(unit, row)['response_payload_sha256'] for row in candidates}
    if len(payloads) != 1:
        raise ProvenanceRefused('Stage4A 相同请求存在冲突的有效历史回答，不能猜选来源')
    chosen = min(candidates, key=lambda row: (row.checkpoint.checkpoint_sha256, row.context.run_id))
    if not persist:
        return chosen
    record = {**identity, 'source': source_row(unit, chosen)}
    record['manifest_sha256'] = agent_runs.canonical_sha256(record)
    with process_append_lock(path):
        if path.is_file():
            return read_selected()
        stage4_runs._atomic_json(path, record)
    note_selected(record['manifest_sha256'])
    return chosen
