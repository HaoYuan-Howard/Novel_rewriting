# -*- coding: utf-8 -*-
"""A 的已提交后继与实际依赖：只保留当前父候选中的有效来源。

普通历史缓存可以收集未提交回答；已有正式候选时则必须以它为唯一父节点。
否则一次人工编辑或 typed 修复会被更早的普通模型回答覆盖。本模块不发请求、
不写产物、不裁决内容，依赖与编译仍复用普通 producer。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from contracts.artifacts import STAGE2_CHAPTER_BLUEPRINTS, STAGE4_BLIND_CANDIDATE
from contracts.schema.stage4 import BlindCandidate, BlindBookStrategyResponse, DesignDependencies
from novel.errors import ArtifactUnreadable, ProvenanceRefused, UpstreamStale, UpstreamUnproven
from novel.llm import agent_runs, stage4_runs
from novel.store import ArtifactStore


def _constraint_snapshot(ctx: Any, candidate: BlindCandidate, history_prefix: list[str] | None = None) -> dict:
    from contracts.invariants import DESIGN_REVIEW_CONTRACT
    from novel.orchestrator.approvals import freeze_design_constraint_history
    from novel.source_identity import artifact_body_root
    from novel.stages.stage4.design_evolution import strategy_epoch_sha256
    epoch = strategy_epoch_sha256({
        'review_contract': DESIGN_REVIEW_CONTRACT,
        'root_clean_sha1': artifact_body_root(ArtifactStore(ctx), STAGE2_CHAPTER_BLUEPRINTS),
        'truth_sha256': candidate.truth_sha256, 'alias_algorithm': candidate.alias_algorithm},
        candidate.reconstruction_strategy)
    return freeze_design_constraint_history(ctx.novel_dir.parent, ctx.sid, epoch=epoch,
        ordinals=[row.output_ordinal for row in candidate.output_chapters], history_prefix=history_prefix)


def strategy_dependency(prepared: Any) -> str:
    from novel.stages.stage4 import plan
    normal = replace(prepared, strategy_feedback=None,
                     strategy_input_sha256=plan._design_strategy_input_sha(prepared.strategy_projection),
                     system=plan.prompts.blind_book_strategy_system(),
                     user=plan._design_strategy_user(prepared.strategy_projection))
    return agent_runs.canonical_sha256(stage4_runs._design_repair_request_row(plan._design_strategy_unit(normal)))


def dependencies(ctx: Any, prepared: Any, strategy: BlindBookStrategyResponse) -> DesignDependencies:
    """普通请求、重建范围与保存的输入凭据共用一个构造器。"""
    from novel.stages.stage4 import plan
    chapters = plan._design_chapter_contexts(ctx, replace(prepared, rebuild_parent=None), strategy, ensure_units=False)
    return DesignDependencies(strategy=strategy_dependency(prepared), chapters=[
        {'chapter_ordinal': row.chapter_ordinal, 'request_sha256': agent_runs.canonical_sha256(
            stage4_runs._design_repair_request_row(plan._design_chapter_unit(row)))}
        for row in chapters])


def _by_unit(value: DesignDependencies) -> dict[int | None, str]:
    return {None: value.strategy, **{row.chapter_ordinal: row.request_sha256 for row in value.chapters}}


def _chapter_constraints(prepared: Any, chapter: Any) -> tuple[list[dict], list[dict]]:
    """原身份重绑当前目录；失去端点的历史要求保留待复审，不能静默消失。"""
    from contracts.invariants import DESIGN_REPAIR_CODE_INSTRUCTIONS
    parent = prepared.rebuild_parent
    old_to_new: dict[str, str] = {}
    global_to_local: dict[str, str] = {}
    labels = ('subject_aliases', 'endpoint_aliases', 'participant_aliases')
    parent_mapping = parent.candidate.model_dump(mode='json', include=set(labels))
    for label in labels:
        # 主体映射值可能是 typed ref，端点是 object；不能假定为可哈希字符串。
        current = {agent_runs.canonical_sha256(formal): alias
                   for alias, formal in prepared.mapping[label].items()}
        old_to_new.update({alias: current[agent_runs.canonical_sha256(formal)]
                           for alias, formal in parent_mapping[label].items()
                           if agent_runs.canonical_sha256(formal) in current})
        global_to_local.update({formal: local
                                for local, formal in chapter.local_mapping[label].items()})
    rows, pending = [], []
    catalog = parent.identity['constraints']['catalogs'][str(chapter.chapter_ordinal)]
    for item in catalog['items']:
        original = item['constraint']
        if original.get('effect') is not None or original['code'] not in DESIGN_REPAIR_CODE_INSTRUCTIONS:
            raise ProvenanceRefused('旧关系复制式返修不属于当前契约，不能静默迁移')
        referenced = set(original['opaque_aliases'])
        if any(alias not in old_to_new or old_to_new[alias] not in global_to_local for alias in referenced):
            pending.append({'constraint_sha256': item['constraint_sha256'], 'code': original['code'],
                            'reason_code': 'upstream_endpoint_changed_requires_review'})
            continue
        rows.append({'code': original['code'], 'scene_refs': original['scene_refs'],
            'opaque_aliases': [global_to_local[old_to_new[alias]] for alias in original['opaque_aliases']],
            'instruction': DESIGN_REPAIR_CODE_INSTRUCTIONS[original['code']]})
    return rows, pending


def chapter_request(prepared: Any, chapter: Any) -> Any:
    """变更章的请求带当前父设计，不能命中修复前的普通回答。"""
    from novel.stages.stage4 import plan
    parent = prepared.rebuild_parent
    if parent is None:
        return chapter
    digest = agent_runs.canonical_sha256(stage4_runs._design_repair_request_row(plan._design_chapter_unit(chapter)))
    if _by_unit(parent.candidate.provenance.design_dependencies).get(chapter.chapter_ordinal) == digest:
        return chapter
    previous = next((row.response_payload for row in parent.sources if row.chapter == chapter.chapter_ordinal), None)
    constraints, pending = _chapter_constraints(prepared, chapter)
    feedback = {'kind': 'stage4-upstream-changed-chapter/v1', 'previous_design': previous,
                'accumulated_constraints': constraints, 'pending_changed_subjects': pending,
                'constraint_history_sha256': agent_runs.canonical_sha256(
                    parent.identity['constraints']['catalogs'][str(chapter.chapter_ordinal)]),
                'instruction': '按本轮事实更新本章并满足累计 typed 约束；保留仍适用的已有设计，过期引用必须重新解析。消失端点只作为待复审义务，不凭空补事实；历史义务须后续 Reviewer 明确处置，重建不自动关闭。'}
    return replace(chapter,
        user=chapter.user + '\n【UPSTREAM_REBUILD_JSON】\n' + agent_runs.canonical_bytes(feedback).decode('utf-8'),
        input_sha256=agent_runs.canonical_sha256({'normal_input': chapter.input_sha256, 'feedback': feedback}),
        validator=chapter.validator)


@dataclass(frozen=True)
class DesignRebuildParent:
    identity: dict[str, Any]
    candidate: BlindCandidate
    sources: tuple[stage4_runs.DesignUnitSource, ...]

    def preserved(self, current: DesignDependencies) -> tuple[stage4_runs.DesignUnitSource, ...]:
        old = self.candidate.provenance.design_dependencies
        if old is None:
            raise ProvenanceRefused('已提交 A 缺实际依赖凭据，不能推断局部采用范围')
        before, after = _by_unit(old), _by_unit(current)
        return tuple(row for row in self.sources if before.get(row.chapter) == after.get(row.chapter))


def restore_parent(ctx: Any, identity: Mapping[str, Any]) -> DesignRebuildParent:
    """恢复已冻结父节点，验证原始提交字节及完整源链，不要求它仍是 live 文件。"""
    if (not isinstance(identity, Mapping)
            or set(identity) != {'kind', 'run_id', 'candidate_sha256', 'file_sha256', 'commit_sha256', 'constraints'}
            or identity.get('kind') != 'stage4-design-rebuild-parent/v2'
            or identity.get('run_id') == ctx.run_id
            or any(not agent_runs.is_sha256(identity.get(key))
                   for key in ('candidate_sha256', 'file_sha256', 'commit_sha256'))):
        raise ProvenanceRefused('Stage4A 重建父节点身份不闭合')
    _kind, _path, record = stage4_runs._committed_design_aggregate_for_run(ctx, identity['run_id'])
    if (record['commit_sha256'] != identity['commit_sha256']
            or record.get('post_file_sha256s', {}).get(STAGE4_BLIND_CANDIDATE) != identity['file_sha256']):
        raise ProvenanceRefused('Stage4A 重建父节点已非原始正式提交')
    payload, sources = stage4_runs.load_committed_design_sources(
        ctx, parent_run_id=identity['run_id'], parent_candidate_sha256=identity['candidate_sha256'])
    candidate = BlindCandidate.model_validate(payload)
    if candidate.provenance.design_dependencies is None:
        raise UpstreamStale('Stage4A 旧提交没有实际依赖凭据；不能将过期结果当作可局部重建')
    frozen = identity['constraints']
    if (not isinstance(frozen, dict) or not isinstance(frozen.get('history_prefix'), list)
            or frozen != _constraint_snapshot(ctx, candidate, frozen['history_prefix'])):
        raise ProvenanceRefused('Stage4A 重建约束与冻结的原审阅历史不闭合')
    return DesignRebuildParent(dict(identity), candidate, sources)


def current_parent(store: ArtifactStore) -> DesignRebuildParent:
    """正式槽位仅在明确 stale 且原始提交来源闭合时允许重建。"""
    ctx = store.ctx
    with ctx.validation_reads():
        candidate, _ = store.read_own(BlindCandidate, STAGE4_BLIND_CANDIDATE)
        status = store.freshness(STAGE4_BLIND_CANDIDATE)
        file_sha = store.full_sha256_or_none(STAGE4_BLIND_CANDIDATE)
    if status.state == 'unknown':
        raise UpstreamUnproven('Stage4A 来源未知，不能授权局部重建')
    if status.state != 'stale' or file_sha is None:
        raise ProvenanceRefused('Stage4A 重建要求明确过期的已提交候选')
    if candidate.provenance.design_dependencies is None:
        raise UpstreamStale('Stage4A 旧候选缺实际依赖，必须保留原提交，不能猜测局部重建')
    run_id, payload, _sources = stage4_runs.resolve_committed_design_sources(
        ctx, candidate_payload=candidate.model_dump(mode='json', by_alias=True),
        candidate_file_sha256=file_sha)
    _kind, _path, record = stage4_runs._committed_design_aggregate_for_run(ctx, run_id)
    parent = restore_parent(ctx, {
        'kind': 'stage4-design-rebuild-parent/v2', 'run_id': run_id,
        'candidate_sha256': agent_runs.canonical_sha256(payload),
        'file_sha256': file_sha, 'commit_sha256': record['commit_sha256'],
        'constraints': _constraint_snapshot(ctx, candidate)})
    if parent.candidate != candidate:
        raise ArtifactUnreadable('Stage4A 父节点与正式候选在读取期间变化')
    return parent


def preserved_sources(ctx: Any, record: Mapping[str, Any]) -> tuple[stage4_runs.DesignUnitSource, ...]:
    """同一验证口贯通 prepare、commit、恢复和后续 repair/human consumer。"""
    finalization = record.get('finalization_payload')
    rebuild = finalization.get('rebuild') if isinstance(finalization, dict) else None
    if rebuild is None:
        return ()
    if not isinstance(rebuild, dict) or set(rebuild) != {'parent', 'preserved_sources'}:
        raise ArtifactUnreadable('Stage4A 重建来源清单形状漂移')
    parent = restore_parent(ctx, rebuild['parent'])
    candidate = BlindCandidate.model_validate(record['primary_payload'])
    current = candidate.provenance.design_dependencies
    if current is None:
        raise ProvenanceRefused('Stage4A 重建缺当前实际依赖')
    expected = parent.preserved(current)
    if rebuild['preserved_sources'] != [stage4_runs._design_source_row(row) for row in expected]:
        raise ProvenanceRefused('Stage4A 采用集合不等于父候选中实际依赖未变的单元')
    return expected


def _partial_request_changed(ctx: Any) -> bool:
    """未聚合的已购工作也有不可变请求；只换身份，不丢弃仍适用的章来源。"""
    from novel.llm import design_adoption
    from novel.llm.ledger import CostLedger, ledger_path
    from novel.run import RunContext
    from novel.stages.stage4 import plan
    from contracts.schema.stage4 import EvidenceBindingResponse
    if ctx.executor_profile is None:
        return False
    root_spec = (stage4_runs.design_unit_run_spec(None) if ctx.node == 'stage4.design'
                 else stage4_runs._run_spec(ctx.node))
    if not agent_runs._read_run_file(ctx, root_spec).exists():
        return False
    probe = RunContext(novel_dir=ctx.novel_dir, node=ctx.node, code=ctx.code,
        readonly=True, paid=False, cache=ctx.cache, run_id=ctx.run_id,
        parent_run_id=ctx.parent_run_id, executor_profile=ctx.executor_profile,
        ledger=CostLedger(path=ledger_path(ctx.novel_dir.parent), run_id=ctx.run_id,
                          parent_run_id=ctx.parent_run_id), observer=lambda _event: None)

    def changed(spec: agent_runs.SingleSessionRunSpec, unit: Any) -> bool | None:
        return agent_runs.own_request_changed(probe, spec,
            input_sha256=unit.input_sha256, system=unit.system, user=unit.user,
            response_model=unit.response_model, max_tokens=unit.max_tokens)

    if probe.node == 'stage4.plan':
        prepared = plan.prepare_plan_candidate(probe)
        root_changed = agent_runs.own_request_changed(probe, stage4_runs._run_spec(probe.node),
            input_sha256=prepared.input_sha256, system=prepared.system, user=prepared.user,
            response_model=EvidenceBindingResponse,
            max_tokens=plan.prompts.EVIDENCE_BIND_MAX_TOKENS)
        baseline = plan.build_mechanical_baseline(prepared.candidate_raw, prepared.upstream.blueprints)
        # 根请求未变不代表章级 wire 的提示、schema 或策略未变。和真实 worker
        # 使用同一 builder；逐个验证记录，避免先遇到漂移便掩盖另一章损坏。
        children_changed = [changed(stage4_runs.plan_chapter_run_spec(chapter.chapter_ordinal),
            plan._prepare_plan_chapter_work(probe, prepared, chapter, chapter.chapter_ordinal).request)
            for chapter in baseline.chapters if not chapter.mechanical]
        return root_changed is True or any(value is True for value in children_changed)

    prepared = plan.prepare_design_candidate(probe, ensure_strategy_unit=False)
    strategy = plan._design_strategy_unit(prepared)
    spec = stage4_runs.design_unit_run_spec(None)
    different = changed(spec, strategy)
    if different is not False:
        return different is True
    checkpoint = agent_runs.restore_checkpoint(probe, spec,
        input_sha256=strategy.input_sha256, system=strategy.system, user=strategy.user,
        response_model=strategy.response_model, max_tokens=strategy.max_tokens,
        validator=prepared.validator)
    if checkpoint is None:
        adopted = design_adoption.select(probe, strategy, validator=prepared.validator,
            state_payload=plan._design_strategy_checkpoint_state, persist=False)
        if adopted is None:
            return False
        checkpoint = adopted.checkpoint
    if checkpoint.state_payload != plan._design_strategy_checkpoint_state(checkpoint.value):
        raise ProvenanceRefused('Stage4A 已保存策略的业务状态不闭合')
    chapters = plan._design_chapter_contexts(probe, prepared, checkpoint.value, ensure_units=False)
    # 不短路：另一章已损坏时不能因为先遇到合法漂移就宣称可安全重启。
    differences = [changed(stage4_runs.design_unit_run_spec(row.chapter_ordinal),
                           plan._design_chapter_unit(row)) for row in chapters]
    return any(value is True for value in differences)


def producer_requires_new_run(ctx: Any) -> bool:
    """调度只读探针：已提交后继或未聚合请求漂移都必须换 producer 身份。"""
    from novel import paths
    from novel.llm import aggregate_runs
    from contracts.artifacts import STAGE4_EVIDENCE_BINDING
    if ctx.node == 'stage4.design':
        journals = [value for value in (
            stage4_runs.design_aggregate_state(ctx),
            stage4_runs.design_repair_aggregate_state(ctx),
            stage4_runs.design_human_evolution_state(ctx)) if value is not None]
        if len(journals) > 1:
            raise ProvenanceRefused('同一 Stage4A producer 存在多份聚合，不能选取重建父节点')
        record = journals[0] if journals else None
        rel = STAGE4_BLIND_CANDIDATE
    elif ctx.node == 'stage4.plan':
        path = paths.stage4_plan_aggregate_path(ctx.novel_dir, ctx.run_id)
        record = aggregate_runs.validate_envelope(path, run_id=ctx.run_id, sid=ctx.sid,
            node=ctx.node) if path.is_file() else None
        rel = STAGE4_EVIDENCE_BINDING
    else:
        raise ValueError('Stage4 producer 边界只适用于 design/plan')
    if record is None:
        return _partial_request_changed(ctx)
    if record['state'] != 'committed':
        return False
    store = ArtifactStore(ctx)
    status = store.freshness(rel)
    if status.state == 'unknown':
        raise UpstreamUnproven('Stage4 已提交产物的依赖未知，不能建立新生产身份')
    if status.state != 'stale':
        return False
    if store.full_sha256_or_none(rel) != record['post_file_sha256s'].get(rel):
        raise ProvenanceRefused('Stage4 过期正式文件不等于本 run 已提交目标')
    return True
