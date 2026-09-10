# -*- coding: utf-8 -*-
"""A 已保存回答的免费操作入口；原身份补 checkpoint 后交普通采用/聚合。

发现过程只读，同一 producer 构造实际请求。策略尚无 checkpoint 时先恢复
策略，下一次 operator 再计算章请求；不为预览制造合成 checkpoint 或客户端。
"""
from __future__ import annotations

from novel import runtime_layout
from collections.abc import Iterator
from typing import Any

from contracts.schema.stage4 import BlindBookStrategyResponse
from novel import paths
from novel.errors import ProvenanceRefused, TerminalState
from novel.llm import agent_runs, design_adoption, stage4_runs
from novel.run import RunContext


def _units(ctx: RunContext) -> Iterator[tuple[Any, Any, Any]]:
    from novel.stages.stage4 import plan
    from novel.stages.stage4.design_rebuild import dependencies, strategy_dependency
    prepared = plan.prepare_design_candidate(ctx, ensure_strategy_unit=False)
    unit = plan._design_strategy_unit(prepared)
    parent = prepared.rebuild_parent
    if (parent is not None
            and parent.candidate.provenance.design_dependencies.strategy == strategy_dependency(prepared)):
        strategy = BlindBookStrategyResponse.model_validate(next(
            row.response_payload for row in parent.sources if row.chapter is None))
    else:
        yield unit, prepared.validator, plan._design_strategy_checkpoint_state
        previous = design_adoption.select(ctx, unit, validator=prepared.validator,
            state_payload=plan._design_strategy_checkpoint_state, persist=False)
        if previous is None:
            return
        strategy = previous.checkpoint.value
    preserved = {row.chapter for row in parent.preserved(dependencies(ctx, prepared, strategy))} if parent else set()
    for chapter in plan._design_chapter_contexts(ctx, prepared, strategy, ensure_units=False):
        if chapter.chapter_ordinal in preserved:
            continue
        def compiled_state(response: Any, current: Any = chapter) -> dict[str, Any]:
            why = current.validator(response, None)
            if why is not None or current.validator.compiled is None:
                raise ProvenanceRefused(f'原回答不能按当前章材料编译：{why}')
            return plan._design_chapter_checkpoint_state(response, current.validator.compiled)
        yield plan._design_chapter_unit(chapter), chapter.validator, compiled_state


def _sources(ctx: RunContext, unit: Any) -> Iterator[str]:
    for source_id in stage4_runs._plan_source_run_ids(ctx):
        if stage4_runs._source_has_design_checkpoint(ctx, source_id, unit.chapter):
            continue
        if not any((root / source_id / 'design' / 'units' / paths.stage4_design_unit_dirname(unit.chapter)
                    / 'run.json').is_file() for root in runtime_layout.stage_run_roots(ctx.novel_dir, 'stage4')):
            continue
        match = agent_runs.match_historical_request(ctx, stage4_runs.design_unit_run_spec(unit.chapter),
            source_run_id=source_id, input_sha256=unit.input_sha256, system=unit.system,
            user=unit.user, response_model=unit.response_model, max_tokens=unit.max_tokens,
            use_source_profile=True)
        if match.matches and design_adoption.pending_response(ctx, unit, source_id) is not None:
            yield source_id


def available(ctx: RunContext) -> bool:
    if not ctx.readonly or ctx.paid or ctx.node != 'stage4.design':
        raise ProvenanceRefused('原回答恢复发现必须使用免费只读 Stage4A 上下文')
    from novel.llm.ledger import CostLedger, ledger_path
    # 历史身份核验需读取实际费用证据；独立只读上下文不污染服务的内存账。
    ctx = RunContext(novel_dir=ctx.novel_dir, node=ctx.node, paid=False, code=ctx.code,
        cache=ctx.cache, readonly=True, ledger=CostLedger(path=ledger_path(ctx.novel_dir.parent)),
        observer=lambda _event: None)
    return any(next(_sources(ctx, unit), None) is not None for unit, _validator, _state in _units(ctx))


def recover(ctx: RunContext) -> int:
    if ctx.readonly or ctx.paid or ctx.node != 'stage4.design' or ctx.executor_profile is not None:
        raise ProvenanceRefused('原回答恢复只接受独立免费执行，不接受新模型配置')
    count = 0
    for unit, validator, state_payload in _units(ctx):
        for source_id in _sources(ctx, unit):
            count += int(design_adoption.recover_response_checkpoint(
                ctx, unit, source_id, validator=validator, state_payload=state_payload))
    if not count:
        raise TerminalState('当前没有可补 checkpoint 的原回答；重新读取操作面')
    return count
