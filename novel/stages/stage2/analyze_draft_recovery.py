"""已购 owner 草稿的免费 typed 修复，独立 WAL 不重新打开 Analyze 首次提交。

原回答、receipt 和冻结句柄必须完整对证。编译前错误保留在原候选，人工只替换
问题 owner；成功后进入该 owner 的正式或 INVALID 生命周期，不制造占位人物。
"""
from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_ANALYZE_RECEIPT, STAGE2_BLUEPRINT_CHARACTER_PROJECTION,
    STAGE2_CHARACTERS, STAGE2_GLOBAL_GROUNDING,
)
from contracts.schema.analyze import AnalyzeReceipt
from contracts.schema.analyze_draft import AnalyzeDraftRepairJournal, AnalyzeOwnerDraftEdit
from contracts.schema.characters import CharacterRegistry, CharactersProvenance, CharactersResponse
from contracts.schema.common import SharedAnalysisSource
from contracts.schema.knowledge import GlobalProvenance, KnowledgeGrounding
from novel import paths, prov
from novel.errors import GateBlocked, ProvenanceRefused, TerminalState, UpstreamMissing
from novel.llm import agent_runs, stage2_global_runs
from novel.run import RunContext
from novel.stages.stage2 import analyze, analyze_recovery, characters, checks, grounding
from novel.stages.stage2.character_refs import materialize_blueprint_character_projection
from novel.store import ArtifactStore

OWNERS = ('stage2.characters', 'stage2.global')
_ACTIVE_REQUEST: ContextVar[str | None] = ContextVar('analyze_draft_repair_request', default=None)


def _store(novel_dir: Path, *, code: str | None = None) -> ArtifactStore:
    from novel.orchestrator.state import readonly_ctx
    return ArtifactStore(readonly_ctx(novel_dir, 'stage2.global', code or prov.code_version()))


def _formal(owner: str) -> str:
    if owner not in OWNERS:
        raise TerminalState('联合草稿只能由 Characters 或 Global owner 修复')
    return STAGE2_CHARACTERS if owner == 'stage2.characters' else STAGE2_GLOBAL_GROUNDING


def status(novel_dir: Path, *, code: str | None = None) -> list[dict]:
    """纯读调度面；来源损坏抛出，不把未知当作可以再次购买。"""
    store = _store(novel_dir, code=code)
    if not store.exists(STAGE2_ANALYZE_RECEIPT):
        return []
    receipt, sha = store.read_own_with_sha256(AnalyzeReceipt, STAGE2_ANALYZE_RECEIPT)
    result = []
    for side in receipt.materializations:
        rel = paths.analyze_draft_repair_rel(side.owner)
        journal = store.read_own(AnalyzeDraftRepairJournal, rel)[0] if store.exists(rel) else None
        if journal is not None and journal.state == 'prepared':
            result.append(dict(owner=side.owner, state='prepared', receipt_sha256=sha,
                               request_sha256=journal.request_sha256, issues=journal.hard_hits))
        elif journal is None and side.state in ('draft_invalid', 'awaiting_characters'):
            formal = _formal(side.owner)
            if not store.exists(formal) and not store.exists(paths.invalid_of(formal)):
                result.append(dict(owner=side.owner, state=side.state,
                                   receipt_sha256=sha, issues=side.issues))
    return result


def pending(novel_dir: Path, *, code: str | None = None) -> list[str]:
    return [row['owner'] for row in status(novel_dir, code=code)]


def source_paths(novel_dir: Path, *, code: str | None = None) -> tuple[Path, ...]:
    """仅当前草稿来源及未闭合 journal；不把无关历史目录放入 dispatch seal。"""
    store = _store(novel_dir, code=code)
    rows = status(novel_dir, code=code)
    if not rows:
        return ()
    receipt = store.read_own(AnalyzeReceipt, STAGE2_ANALYZE_RECEIPT)[0]
    result = [store.path(STAGE2_ANALYZE_RECEIPT)]
    result.extend(store.path(paths.analyze_draft_repair_rel(row['owner'])) for row in rows
                  if row['state'] == 'prepared')
    run_path = paths.stage2_analyze_run_path(novel_dir, receipt.run_id)
    result.extend(sorted(path for path in run_path.rglob('*') if path.is_file()))
    # 原 raw/request/custody 保存在持久工作单元目录；复用同一来源闭集，
    # 不能只 seal checkpoint 而漏掉恢复时必须对证的原始回答。
    from novel.orchestrator.queue import analyze_initial_recovery_surface
    result.extend(analyze_initial_recovery_surface(novel_dir.parent, paths.derive_source_meta(novel_dir.name)[0],
        receipt.run_id, code=code).source_paths)
    return tuple(sorted(set(result)))


def refuse_pending(novel_dir: Path) -> None:
    store = _store(novel_dir)
    for owner in OWNERS:
        rel = paths.analyze_draft_repair_rel(owner)
        if not store.exists(rel):
            continue
        journal = store.read_own(AnalyzeDraftRepairJournal, rel)[0]
        if journal.state == 'prepared' and _ACTIVE_REQUEST.get() != journal.request_sha256:
            raise ProvenanceRefused(f'{owner} 草稿修复仍有 prepared 尾窗，先 --recover-analyze-draft')


def _bound_request(ctx: RunContext, store: ArtifactStore):
    receipt, receipt_sha, restored, prepared = analyze_recovery.original_source(ctx, store)
    side = next(row for row in receipt.materializations if row.owner == ctx.node)
    if side.state not in ('draft_invalid', 'awaiting_characters'):
        raise TerminalState('该 owner 已有正式或 INVALID 初稿，使用现有 typed 编辑入口')
    draft = (CharactersResponse(characters=restored.checkpoint.value.characters)
             if ctx.node == 'stage2.characters'
             else grounding.materialize_knowledge_extract(restored.checkpoint.value.global_draft))
    request = AnalyzeOwnerDraftEdit(owner=ctx.node, source_receipt_sha256=receipt_sha,
        source_draft_sha256=agent_runs.canonical_sha256(draft.model_dump(mode='json')),
        source_mapping_sha256=agent_runs.canonical_sha256(dict(
            characters=receipt.character_handles, global_handles=receipt.global_handles)),
        character_bindings=receipt.character_handles if ctx.node == 'stage2.global' else {},
        characters=draft if ctx.node == 'stage2.characters' else None,
        global_draft=draft if ctx.node == 'stage2.global' else None)
    return request, receipt, restored, prepared


def workpack(base: str | Path, sid: str, owner: str) -> dict:
    """CLI/API 共用的只读修订材料；候选段落仍来自原冻结 MainText。"""
    from novel.orchestrator.state import readonly_ctx
    _formal(owner)
    novel_dir = paths.resolve_novel_dir(base, sid)
    ctx = readonly_ctx(novel_dir, owner, prov.code_version())
    rows = status(novel_dir, code=ctx.code)
    row = next((item for item in rows if item['owner'] == owner), None)
    if row is None:
        raise UpstreamMissing('该 owner 当前没有待修复的联合草稿')
    _request, _receipt, _restored, prepared = _bound_request(ctx, ArtifactStore(ctx))
    diagnostics = list(row['issues'])
    store = ArtifactStore(ctx)
    if (owner == 'stage2.global' and row['state'] == 'awaiting_characters'
            and store.exists(STAGE2_CHARACTERS)
            and not store.exists(paths.invalid_of(STAGE2_CHARACTERS))
            and store.freshness(STAGE2_CHARACTERS).state == 'fresh'):
        issue = analyze_recovery.pending_compilation_issue(base, sid, code=ctx.code)
        if issue is not None:
            diagnostics.append(issue)
    return dict(owner=owner, source_state=row['state'], template=edit_template(ctx),
        diagnostics=diagnostics, evidence_candidates=[dict(evidence_candidate_id=item.evidence_candidate_id,
            chapter_ordinal=item.chapter_ordinal, excerpt=item.text) for item in prepared.table.rows])


def edit_template(ctx: RunContext) -> dict:
    _formal(ctx.node)
    store = ArtifactStore(ctx)
    journal_rel = paths.analyze_draft_repair_rel(ctx.node)
    if store.exists(journal_rel):
        journal = store.read_own(AnalyzeDraftRepairJournal, journal_rel)[0]
        if journal.state == 'prepared':
            return journal.request.model_dump(mode='json')
        raise TerminalState('owner 草稿已完成物化；使用该产物的独立 typed 编辑')
    if store.exists(_formal(ctx.node)) or store.exists(paths.invalid_of(_formal(ctx.node))):
        raise TerminalState('已有正式或 INVALID 主体；使用该产物的独立 typed 编辑')
    return _bound_request(ctx, store)[0].model_dump(mode='json')


def _compile(ctx, store, request, receipt, prepared):
    shared = SharedAnalysisSource(receipt_rel=STAGE2_ANALYZE_RECEIPT,
        receipt_sha256=request.source_receipt_sha256, run_id=receipt.run_id, attempt_id=receipt.attempt_id)
    source = dict(receipt.provenance.model_dump(mode='json'), usage=None, shared_analysis_source=shared)
    characters_sha = None
    registry = None
    if request.owner == 'stage2.characters':
        compiled = characters.compile_response(request.characters, prepared.table,
            identity_map=receipt.character_handles, reserved_ids=receipt.character_handles.values())
        provenance = CharactersProvenance.model_validate(dict(source,
            retired_character_ids=sorted(set(receipt.character_handles.values()) - set(compiled.characters))))
        candidate = CharacterRegistry(characters=compiled.characters,
            evidence_anchors=compiled.evidence_anchors,
            _provenance=ctx.rewrite_provenance(provenance, by='--apply-edit'))
        outcomes = checks.check_characters(candidate.model_dump(mode='json', by_alias=True), prepared.main_text)
    else:
        registry, characters_sha = store.read_with_sha256(CharacterRegistry, STAGE2_CHARACTERS)
        if store.exists(paths.invalid_of(STAGE2_CHARACTERS)) or store.freshness(STAGE2_CHARACTERS).state != 'fresh':
            raise ProvenanceRefused('Global 草稿修复须先闭合当前人物登记')
        if analyze._hard(checks.check_characters(registry.model_dump(mode='json', by_alias=True), prepared.main_text)):
            raise ProvenanceRefused('当前人物仍有 HARD，先由人物 owner 订正')
        provenance = GlobalProvenance.model_validate(dict(source, evidence_locator_failures=[]))
        candidate = grounding.compile_global_draft(request.global_draft, prepared.table, registry,
            provenance=ctx.rewrite_provenance(provenance, by='--apply-edit'),
            character_handle_map=request.character_bindings, id_map=receipt.global_handles)
        original_keys = set()
        for key, value in receipt.global_handles.items():
            parts = key.split(':')
            original_keys.add((f'version:{receipt.global_handles["rule:" + parts[1]]}:{value}'
                               if parts[0] == 'version' else f'{parts[0]}:{value}'))
        candidate = candidate.model_copy(update={'provenance': candidate.provenance.model_copy(update={
            'retired_global_ids': sorted(original_keys - candidate.identity_keys())})})
        outcomes = checks.check_global(candidate.model_dump(mode='json', by_alias=True),
                                       prepared.main_text, characters=registry.model_dump(mode='json'))
    return candidate, analyze._hard(outcomes), characters_sha


def recover(ctx: RunContext):
    journal = ArtifactStore(ctx).read_own(AnalyzeDraftRepairJournal, paths.analyze_draft_repair_rel(ctx.node))[0]
    return repair(ctx, journal.request, actor=journal.actor, reason=journal.reason)


def repair(ctx: RunContext, request: AnalyzeOwnerDraftEdit, *, actor: str, reason: str):
    if ctx.paid or ctx.node != request.owner:
        raise ProvenanceRefused('owner 草稿修复只能由对应免费入口执行')
    if not actor.strip() or not reason.strip():
        raise TerminalState('草稿 typed 编辑必须提供 actor 与 reason')
    store = ArtifactStore(ctx)
    formal = _formal(ctx.node)
    journal_rel = paths.analyze_draft_repair_rel(ctx.node)
    request_sha = agent_runs.canonical_sha256(request.model_dump(mode='json'))
    token = _ACTIVE_REQUEST.set(request_sha)
    try:
        with characters.ownership(ctx), stage2_global_runs.commit_ownership(ctx.novel_dir):
            stage2_global_runs.refuse_foreign_commit_reservations(ctx)
            expected, receipt, restored, prepared = _bound_request(ctx, store)
            for field in ('source_receipt_sha256', 'source_draft_sha256', 'source_mapping_sha256'):
                if getattr(request, field) != getattr(expected, field):
                    raise ProvenanceRefused('草稿编辑的原回答/receipt/冻结映射绑定已变化')
            journal = store.read_own(AnalyzeDraftRepairJournal, journal_rel)[0] if store.exists(journal_rel) else None
            if journal is not None:
                if (journal.request != request or journal.request_sha256 != request_sha
                        or journal.actor != actor or journal.reason != reason):
                    raise ProvenanceRefused('草稿修复 WAL 必须按原 request/actor/reason 补尾')
                if journal.state == 'committed':
                    # 初次物化完成后不能用草稿覆盖独立修订。
                    if store.full_sha256_or_none(journal.target_rel) != journal.target_sha256:
                        raise ProvenanceRefused('该 owner 已独立修订；不重开已完成草稿事务')
                    if journal.hard_hits:
                        raise GateBlocked('草稿已物化为 INVALID，使用正式 owner 编辑入口',
                                          invalid_path=store.path(journal.target_rel))
                    return store.read_own(type(journal.candidate), journal.target_rel)[0]
            elif store.exists(formal) or store.exists(paths.invalid_of(formal)):
                raise ProvenanceRefused('草稿修复目标已由其他写口建立，拒绝覆盖')
            try:
                candidate, hits, characters_sha = _compile(ctx, store, request, receipt, prepared)
            except ValueError as exc:
                raise TerminalState(f'owner 草稿仍不能编译：{exc}；原已购回答保持不变') from exc
            projection = (materialize_blueprint_character_projection(
                candidate.model_dump(mode='json')['characters'], prepared.main_text)
                if ctx.node == 'stage2.characters' and not hits else None)
            target = paths.invalid_of(formal) if hits else formal
            target_sha = (store.prospective_invalid_sha256(formal, candidate, gates_skipped=[])
                          if hits else store.prospective_sha256(formal, candidate))
            projection_sha = (store.prospective_sha256(STAGE2_BLUEPRINT_CHARACTER_PROJECTION, projection)
                              if projection is not None else None)
            if journal is None:
                if ctx.node == 'stage2.characters' and store.exists(STAGE2_BLUEPRINT_CHARACTER_PROJECTION):
                    raise ProvenanceRefused('无人物产物却有第三方称谓投影，不能由草稿修复覆盖')
                journal = AnalyzeDraftRepairJournal(state='prepared', run_id=ctx.run_id, code=ctx.code,
                    request=request, request_sha256=request_sha, actor=actor, reason=reason,
                    source_run_sha256=restored.run_sha256,
                    source_checkpoint_sha256=restored.checkpoint.checkpoint_sha256,
                    main_text_sha256=prepared.source_sha256, characters_sha256=characters_sha,
                    target_rel=target, target_sha256=target_sha, projection_sha256=projection_sha,
                    candidate=candidate, hard_hits=hits)
                store.write(journal_rel, journal)
            if (journal.code != ctx.code or journal.source_run_sha256 != restored.run_sha256
                    or journal.source_checkpoint_sha256 != restored.checkpoint.checkpoint_sha256
                    or journal.main_text_sha256 != prepared.source_sha256
                    or journal.characters_sha256 != characters_sha or journal.candidate != candidate
                    or journal.target_sha256 != target_sha or journal.projection_sha256 != projection_sha
                    or journal.hard_hits != hits or journal.target_rel != target):
                raise ProvenanceRefused('草稿 prepared 的来源、编译结果或精确目标字节漂移')
            _assert_sources(ctx, store, journal)
            current = store.full_sha256_or_none(target)
            other = formal if paths.is_invalid(target) else paths.invalid_of(formal)
            if store.exists(other) or current not in (None, target_sha):
                raise ProvenanceRefused('草稿修复目标已被第三方修改')
            if projection is not None and store.full_sha256_or_none(STAGE2_BLUEPRINT_CHARACTER_PROJECTION) not in (None, projection_sha):
                raise ProvenanceRefused('草稿人物投影已被第三方修改')
            if current is None:
                if hits:
                    store.write_invalid(formal, candidate, gates_skipped=[])
                else:
                    store.write_round(formal, candidate)
            if projection is not None and not store.exists(STAGE2_BLUEPRINT_CHARACTER_PROJECTION):
                store.write_round(STAGE2_BLUEPRINT_CHARACTER_PROJECTION, projection)
            if store.full_sha256_or_none(target) != target_sha:
                raise ProvenanceRefused('草稿 owner 物化写后精确字节不闭合')
            if projection_sha is not None and store.full_sha256_or_none(STAGE2_BLUEPRINT_CHARACTER_PROJECTION) != projection_sha:
                raise ProvenanceRefused('草稿人物投影写后精确字节不闭合')
            # Stage2 语义审阅退役：owner 草稿物化后不再刷新任何审阅文件；
            # 语义结论由报告 unver 行与交付披露表达。
            _assert_sources(ctx, store, journal)
            store.write(journal_rel, journal.model_copy(update={'state': 'committed'}))
            if hits:
                raise GateBlocked(f'owner 草稿已保存 INVALID：{hits}；使用该 owner 的免费 typed 编辑',
                                  invalid_path=store.path(target))
            return store.read_own(type(candidate), target)[0]
    finally:
        _ACTIVE_REQUEST.reset(token)


def _assert_sources(ctx, store, journal):
    if (prov.code_version() != ctx.code or store.full_sha256_or_none(STAGE1_MAIN_TEXT) != journal.main_text_sha256
            or store.full_sha256_or_none(STAGE2_ANALYZE_RECEIPT) != journal.request.source_receipt_sha256):
        raise ProvenanceRefused('owner 草稿修复期间原输入或代码发生变化')
    if journal.characters_sha256 is not None and (
            store.full_sha256_or_none(STAGE2_CHARACTERS) != journal.characters_sha256
            or store.exists(paths.invalid_of(STAGE2_CHARACTERS))):
        raise ProvenanceRefused('Global 草稿修复期间人物依赖发生变化')
