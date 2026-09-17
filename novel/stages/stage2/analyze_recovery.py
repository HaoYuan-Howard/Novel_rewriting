"""人物修好后，从原联合回答免费物化 Global；新 WAL 不重开原 Analyze。

旧回答、来源账和映射必须逐项对证。UNKNOWN、删除或合并过的映射从不靠名称猜回。
prepared 后两份 owner 保持冻结；恢复只补目标、审阅与独立采用审计。
"""
from __future__ import annotations

import json
from pathlib import Path

from contracts.artifacts import (
    STAGE1_MAIN_TEXT, STAGE2_ANALYZE_RECEIPT, STAGE2_ANALYZE_RECOVERY,
    STAGE2_CHARACTERS, STAGE2_GLOBAL_GROUNDING,
)
from contracts.schema.analyze import AnalyzeReceipt, AnalyzeResponse
from contracts.schema.analyze_recovery import AnalyzeRecoveryJournal
from contracts.schema.characters import CharacterRegistry
from contracts.schema.common import LlmProvenance, SharedAnalysisSource
from contracts.schema.knowledge import GlobalProvenance, KnowledgeGrounding
from contracts.schema.stage1 import MainText
from novel import paths, prov
from novel.errors import GateBlocked, ProvenanceRefused, TerminalState
from novel.llm import agent_runs, stage2_global_runs
from novel.llm.ledger import ledger_path, read_strict_jsonl_objects
from novel.run import RunContext
from novel.stages.stage2 import analyze, characters, checks, grounding
from novel.stages.stage2.global_evidence_candidates import GlobalEvidenceCandidateTable
from novel.store import ArtifactStore

REL = STAGE2_ANALYZE_RECOVERY
GLOBAL = STAGE2_GLOBAL_GROUNDING
INVALID = paths.invalid_of(GLOBAL)


def _store(novel_dir: Path, *, code: str | None = None) -> ArtifactStore:
    from novel.orchestrator.state import readonly_ctx
    return ArtifactStore(readonly_ctx(novel_dir, 'stage2.global', code or prov.code_version()))


def refuse_pending_recovery(novel_dir: Path) -> None:
    store = _store(novel_dir)
    if store.exists(REL) and store.read_own(AnalyzeRecoveryJournal, REL)[0].state == 'prepared':
        raise ProvenanceRefused('原 GlobalDraft 免费物化尚有 prepared 尾窗，先 --recover-analyze 补尾')


def pending(base: str | Path, sid: str, *, code: str | None = None) -> str | None:
    """返回可恢复来源回执 SHA；不把检查来源失败当作不存在。"""
    # 本入口也在 operator 的只读快照内调用，必须复用调用方冻结的 code。
    # write_ledger=False 不等于 readonly，不能用可写 RunContext 越过 Store 边界。
    store = _store(paths.find_novel_dir(base, sid), code=code)
    if store.exists(REL):
        journal = store.read_own(AnalyzeRecoveryJournal, REL)[0]
        return journal.source_receipt_sha256 if journal.state == 'prepared' else None
    if store.exists(GLOBAL) or store.exists(INVALID) or not store.exists(STAGE2_ANALYZE_RECEIPT):
        return None
    if not store.exists(STAGE2_CHARACTERS) or store.exists(paths.invalid_of(STAGE2_CHARACTERS)):
        return None
    receipt, sha = store.read_own_with_sha256(AnalyzeReceipt, STAGE2_ANALYZE_RECEIPT)
    global_side = next(row for row in receipt.materializations if row.owner == 'stage2.global')
    return sha if global_side.state == 'awaiting_characters' else None


def original_source(ctx: RunContext, store: ArtifactStore):
    """逐字节证明原联合来源；owner 草稿修复也必须走同一证明。"""
    receipt, receipt_sha = store.read_own_with_sha256(AnalyzeReceipt, STAGE2_ANALYZE_RECEIPT)
    main, source_sha = store.read_with_sha256(MainText, STAGE1_MAIN_TEXT)
    if source_sha != receipt.source_sha256:
        raise ProvenanceRefused('原联合回答的 MainText 已改变，不能免费追认旧 GlobalDraft')
    rows = main.model_dump(mode='json')
    table = GlobalEvidenceCandidateTable.build(rows)
    prepared = analyze.PreparedAnalyze(rows, source_sha, table, f'书名：{ctx.title}\n\n{table.render()}',
        agent_runs.canonical_sha256(dict(node=analyze.NODE, main_text_sha256=source_sha, table_sha256=table.sha256())))
    run_file = paths.stage2_analyze_run_path(ctx.novel_dir, receipt.run_id) / agent_runs.RUN_FILE
    if not run_file.exists():
        return _original_source_legacy(ctx, store, receipt, receipt_sha, prepared)
    restored = agent_runs.restore_historical_run(ctx, analyze.run_spec(), source_run_id=receipt.run_id,
        **prepared.request(), ledger_source_path=ledger_path(ctx.novel_dir.parent))
    checkpoint = restored.checkpoint
    source_commit = agent_runs.commit_state(restored.context, analyze.commit_spec())
    if source_commit is None or source_commit['state'] != 'committed':
        raise ProvenanceRefused('原 Analyze 首次提交尚未完成，必须先恢复原 run')
    if not agent_runs.verify_commit_primary(source_commit, receipt.model_dump(mode='json', by_alias=True)):
        raise ProvenanceRefused('原 Analyze 回执与首次提交不一致')
    identity = restored.context.ledger.request_identity_for_attempt(receipt.attempt_id)
    if (checkpoint.checkpoint_sha256 != receipt.checkpoint_sha256
            or checkpoint.acceptance['attempt_id'] != receipt.attempt_id
            or identity['response_sha256'] != receipt.response_sha256
            or receipt.input_sha256 != prepared.input_sha256
            or receipt.request_sha256 != identity['request_fingerprint']
            or checkpoint.state_payload['character_handles'] != receipt.character_handles
            or checkpoint.state_payload['global_handles'] != receipt.global_handles):
        raise ProvenanceRefused('原 GlobalDraft 的回答/局部映射/来源回执不一致')
    return receipt, receipt_sha, restored, prepared


def _legacy_checkpoint_path(novel_dir: Path) -> Path:
    return analyze.legacy_checkpoint_path(novel_dir)


def _load_legacy_analyze_response(ctx: RunContext, receipt: AnalyzeReceipt) -> AnalyzeResponse:
    """从 legacy 侧车或 work_unit recovery 取回已购 AnalyzeResponse。"""
    sidecar = _legacy_checkpoint_path(ctx.novel_dir)
    if sidecar.is_file():
        raw = json.loads(sidecar.read_text(encoding='utf-8'))
        if (raw.get('kind') != 'stage2-analyze-legacy-v1'
                or raw.get('attempt_id') != receipt.attempt_id
                or raw.get('response_sha256') != receipt.response_sha256
                or raw.get('checkpoint_sha256') != receipt.checkpoint_sha256):
            raise ProvenanceRefused('legacy Analyze 侧车与回执不一致')
        return AnalyzeResponse.model_validate(raw['response'])

    # 当前 CLI run 的内存 ledger 未必认领原 analyze attempt；对账书级 ledger。
    book_identity = None
    for row in read_strict_jsonl_objects(ledger_path(ctx.novel_dir.parent), label='legacy analyze'):
        if row.get('attempt_id') == receipt.attempt_id and row.get('sid') == ctx.sid:
            book_identity = row
            break
    if book_identity is None:
        raise ProvenanceRefused('legacy Analyze 书级成本账缺少原 attempt')
    if book_identity.get('response_sha256') != receipt.response_sha256:
        raise ProvenanceRefused('legacy Analyze 账上回答摘要与回执不一致')
    if book_identity.get('request_fingerprint') != receipt.request_sha256:
        raise ProvenanceRefused('legacy Analyze 账上请求指纹与回执不一致')

    wu_root = Path(ctx.novel_dir.parent) / '_service' / 'runs' / ctx.sid / 'work_units'
    if not wu_root.is_dir():
        raise ProvenanceRefused('legacy Analyze 缺少 work_units，无法免费取回原回答')
    for unit in sorted(wu_root.iterdir()):
        ledger_file = unit / 'ledger.json'
        if not ledger_file.is_file():
            continue
        ledger = json.loads(ledger_file.read_text(encoding='utf-8'))
        for turn in ledger.get('turns') or []:
            attempts = turn.get('attempts') or []
            if not any(row.get('attempt_id') == receipt.attempt_id for row in attempts):
                continue
            recovery_sha = turn.get('recovery_sha256')
            if not recovery_sha:
                raise ProvenanceRefused('legacy Analyze 原轮次缺少 recovery 证明')
            blob = json.loads((unit / f'{recovery_sha}.json').read_text(encoding='utf-8'))
            payload = blob.get('payload') or {}
            if payload.get('attempt_id') != receipt.attempt_id:
                raise ProvenanceRefused('legacy Analyze recovery 与 attempt 不一致')
            if payload.get('ledger_response_sha256') != receipt.response_sha256:
                raise ProvenanceRefused('legacy Analyze recovery 回答摘要漂移')
            text = payload.get('normalized_text')
            if not isinstance(text, str) or not text.strip():
                raise ProvenanceRefused('legacy Analyze recovery 缺 normalized_text')
            return AnalyzeResponse.model_validate(json.loads(text))
    raise ProvenanceRefused('legacy Analyze 未找到对应 attempt 的已购回答')


def _original_source_legacy(ctx, store, receipt, receipt_sha, prepared):
    """DeepSeek legacy 首抽无 agent run.json；用回执 + 已购 recovery 证明原草稿。"""
    from types import SimpleNamespace

    if receipt.input_sha256 != prepared.input_sha256:
        raise ProvenanceRefused('原联合回答的输入身份已改变')
    response = _load_legacy_analyze_response(ctx, receipt)
    refs = {row.character_ref for row in response.characters}
    if set(receipt.character_handles) != refs:
        raise ProvenanceRefused('legacy Analyze 人物句柄与回执不一致')
    checkpoint = SimpleNamespace(
        checkpoint_sha256=receipt.checkpoint_sha256,
        value=response,
        acceptance={'attempt_id': receipt.attempt_id},
        state_payload={
            'character_handles': receipt.character_handles,
            'global_handles': receipt.global_handles,
        },
    )
    restored = SimpleNamespace(
        checkpoint=checkpoint,
        run_sha256=receipt_sha,
        context=ctx,
    )
    return receipt, receipt_sha, restored, prepared


def _mapping_is_current(receipt: AnalyzeReceipt, registry: CharacterRegistry) -> bool:
    mapping = receipt.character_handles
    deleted = registry.provenance.characters_edit
    retired = set(registry.provenance.retired_character_ids)
    if deleted is not None:
        retired.update(deleted.deleted_character_ids)
    return (len(set(mapping.values())) == len(mapping)
            and set(mapping.values()) <= set(registry.characters)
            and not set(mapping.values()) & retired)


def pending_compilation_issue(base: str | Path, sid: str, *, code: str | None = None) -> str | None:
    """只预检已购待编译草稿；语义绑定失配转免费 typed 编辑，来源损坏仍抛出。"""
    from novel.orchestrator.state import readonly_ctx
    ctx = readonly_ctx(paths.resolve_novel_dir(base, sid), 'stage2.global', code or prov.code_version())
    store = ArtifactStore(ctx)
    if store.exists(REL):
        return None  # prepared 的精确目标必须由原恢复入口补尾，不能改写 request。
    receipt, _sha, restored, prepared = original_source(ctx, store)
    registry = store.read_with_sha256(CharacterRegistry, STAGE2_CHARACTERS)[0]
    if not _mapping_is_current(receipt, registry):
        return '原人物句柄已经删除或合并；须在 Global 草稿中显式订正身份绑定，不能按名称猜回'
    try:
        grounding.compile_global_draft(
            grounding.materialize_knowledge_extract(restored.checkpoint.value.global_draft),
            prepared.table, registry,
            provenance=GlobalProvenance.model_validate({
                **receipt.provenance.model_dump(mode='json'), 'evidence_locator_failures': []}),
            character_handle_map=receipt.character_handles, id_map=receipt.global_handles)
    except ValueError:
        return '已购 Global 草稿仍有无法编译的引用；须先免费订正原候选，不能重复执行同一失败编译'
    return None


def _source(ctx: RunContext, store: ArtifactStore):
    receipt, receipt_sha, restored, prepared = original_source(ctx, store)
    side = next(row for row in receipt.materializations if row.owner == 'stage2.global')
    if side.state != 'awaiting_characters':
        raise TerminalState('原联合回答不在 awaiting_characters，使用现有 Global 独立修订入口')
    registry, characters_sha = store.read_with_sha256(CharacterRegistry, STAGE2_CHARACTERS)
    if store.exists(paths.invalid_of(STAGE2_CHARACTERS)) or store.freshness(STAGE2_CHARACTERS).state != 'fresh':
        raise ProvenanceRefused('先完成当前人物修订并复验，再恢复原 GlobalDraft')
    raw_characters = registry.model_dump(mode='json', by_alias=True)
    if analyze._hard(checks.check_characters(raw_characters, prepared.main_text)):
        raise ProvenanceRefused('当前人物仍有 HARD，不能恢复原 GlobalDraft')
    mapping = receipt.character_handles
    original_refs = {row.character_ref for row in restored.checkpoint.value.characters}
    if set(mapping) != original_refs or not _mapping_is_current(receipt, registry):
        raise ProvenanceRefused('原人物句柄未知、删除或合并，不能按名称猜测 GlobalDraft 的实体映射')
    return receipt, receipt_sha, restored, prepared, registry, characters_sha


def recover(ctx: RunContext) -> KnowledgeGrounding:
    if ctx.node != 'stage2.global' or ctx.paid:
        raise ProvenanceRefused('--recover-analyze 只接受 stage2.global 免费入口')
    store = ArtifactStore(ctx)
    analyze.refuse_pending_analysis(ctx.novel_dir)
    with characters.ownership(ctx, allow_analysis_recovery=True), stage2_global_runs.commit_ownership(ctx.novel_dir):
        stage2_global_runs.refuse_foreign_commit_reservations(ctx)
        receipt, receipt_sha, restored, prepared, registry, characters_sha = _source(ctx, store)
        expected_source = GlobalProvenance.model_validate({
            **receipt.provenance.model_dump(mode='json'), 'usage': None, 'evidence_locator_failures': [],
            'shared_analysis_source': SharedAnalysisSource(receipt_rel=STAGE2_ANALYZE_RECEIPT,
                receipt_sha256=receipt_sha, run_id=receipt.run_id, attempt_id=receipt.attempt_id)})
        expected_source = ctx.rewrite_provenance(expected_source, by='--renormalize')
        if store.exists(REL):
            journal = store.read_own(AnalyzeRecoveryJournal, REL)[0]
            if (journal.source_receipt_sha256 != receipt_sha
                    or journal.source_checkpoint_sha256 != restored.checkpoint.checkpoint_sha256
                    or journal.source_run_sha256 != restored.run_sha256
                    or journal.source_run_id != receipt.run_id):
                raise ProvenanceRefused('免费恢复 WAL 的原始来源漂移')
            if journal.state == 'committed':
                if store.full_sha256_or_none(journal.target_rel) != journal.target_sha256:
                    raise ProvenanceRefused('原 GlobalDraft 已物化且此后目标已修订，不能重开首次恢复')
                if journal.hard_hits:
                    raise GateBlocked(f'原 GlobalDraft 已物化，仍有 HARD：{journal.hard_hits}',
                                      invalid_path=store.path(INVALID))
                return store.read_own(KnowledgeGrounding, journal.target_rel)[0]
            if journal.characters_sha256 != characters_sha or journal.code != ctx.code:
                raise ProvenanceRefused('prepared 恢复的人物基线或代码已改变，不能混代补尾')
            candidate = journal.candidate
            if candidate.provenance != expected_source:
                raise ProvenanceRefused('prepared 候选没有继承已验证的原模型来源与当前人物依赖')
        else:
            if store.exists(GLOBAL) or store.exists(INVALID):
                raise ProvenanceRefused('已有 Global 主体，不能用原联合草稿覆盖')
            try:
                candidate = grounding.compile_global_draft(
                    grounding.materialize_knowledge_extract(restored.checkpoint.value.global_draft),
                    prepared.table, registry, provenance=expected_source, character_handle_map=receipt.character_handles,
                    id_map=receipt.global_handles)
            except ValueError as exc:
                raise TerminalState(f'原 GlobalDraft 免费编译不通过：{exc}；独立修订该草稿') from exc
            hits = analyze._hard(checks.check_global(candidate.model_dump(mode='json', by_alias=True),
                                                    prepared.main_text, characters=registry.model_dump(mode='json')))
            target = INVALID if hits else GLOBAL
            target_sha = (store.prospective_invalid_sha256(GLOBAL, candidate, gates_skipped=[])
                          if hits else store.prospective_sha256(GLOBAL, candidate))
            journal = AnalyzeRecoveryJournal(state='prepared', run_id=ctx.run_id, code=ctx.code,
                source_run_id=receipt.run_id, source_receipt_sha256=receipt_sha,
                source_checkpoint_sha256=restored.checkpoint.checkpoint_sha256,
                source_run_sha256=restored.run_sha256, characters_sha256=characters_sha,
                target_rel=target, target_sha256=target_sha, candidate=candidate, hard_hits=hits)
            store.write(REL, journal)
        rebuilt = grounding.compile_global_draft(
            grounding.materialize_knowledge_extract(restored.checkpoint.value.global_draft),
            prepared.table,
            registry, provenance=candidate.provenance, character_handle_map=receipt.character_handles,
            id_map=receipt.global_handles)
        hits = analyze._hard(checks.check_global(rebuilt.model_dump(mode='json', by_alias=True),
                                                prepared.main_text, characters=registry.model_dump(mode='json')))
        if rebuilt != candidate or hits != journal.hard_hits:
            raise ProvenanceRefused('原 GlobalDraft 与 prepared 候选/判据不一致')
        expected_sha = (store.prospective_invalid_sha256(GLOBAL, candidate, gates_skipped=[])
                        if hits else store.prospective_sha256(GLOBAL, candidate))
        if expected_sha != journal.target_sha256:
            raise ProvenanceRefused('prepared 目标 SHA 与冻结候选字节不一致')
        _assert_sources(ctx, store, prepared.source_sha256, receipt_sha, characters_sha)
        current_sha = store.full_sha256_or_none(journal.target_rel)
        other = GLOBAL if journal.target_rel == INVALID else INVALID
        if store.exists(other) or (current_sha is not None and current_sha != journal.target_sha256):
            raise ProvenanceRefused('Global 恢复目标已被其他写口改变')
        if current_sha is None:
            if hits:
                store.write_invalid(GLOBAL, candidate, gates_skipped=[])
            else:
                store.write_round(GLOBAL, candidate)
        if store.full_sha256_or_none(journal.target_rel) != journal.target_sha256:
            raise ProvenanceRefused('Global 恢复写后精确字节不闭合')
        # Stage2 语义审阅退役：正式 Global 物化后不再刷新任何审阅文件；
        # 语义结论由报告 unver 行与交付披露表达。
        _assert_sources(ctx, store, prepared.source_sha256, receipt_sha, characters_sha)
        ctx.ledger.note_aggregate_adoption(sid=ctx.sid, node=ctx.node, artifact=journal.target_rel,
            artifact_fingerprint=prov.projection_fingerprint(candidate.model_dump(mode='json', by_alias=True)),
            sources_sha256=agent_runs.canonical_sha256([receipt_sha, characters_sha, restored.checkpoint.checkpoint_sha256]),
            finalization_sha256=journal.target_sha256)
        store.write(REL, journal.model_copy(update={'state': 'committed'}))
        landed = store.read_own(KnowledgeGrounding, journal.target_rel)[0]
        if hits:
            raise GateBlocked(f'原 GlobalDraft 已免费物化，仍有 HARD：{hits}', invalid_path=store.path(INVALID))
        return landed


def _assert_sources(ctx, store, main_sha, receipt_sha, characters_sha):
    if (prov.code_version() != ctx.code
            or store.full_sha256_or_none(STAGE1_MAIN_TEXT) != main_sha
            or store.full_sha256_or_none(STAGE2_ANALYZE_RECEIPT) != receipt_sha
            or store.full_sha256_or_none(STAGE2_CHARACTERS) != characters_sha
            or store.exists(paths.invalid_of(STAGE2_CHARACTERS))):
        raise ProvenanceRefused('免费物化 Global 期间来源发生变化')


def recover_initial(ctx: RunContext, source_run_id: str) -> AnalyzeReceipt:
    """同代码下排空已有 checkpoint 的旧 owner；不请求、不续会话、不编译 raw。"""
    if ctx.node != analyze.NODE or ctx.paid:
        raise ProvenanceRefused('联合初始补尾只接受 stage2.analyze 免费入口')
    if ctx.run_id == source_run_id:
        raise ProvenanceRefused('免费初始补尾使用新的执行 run，显式指定原 source run')
    prepared = analyze.prepare(ctx)
    historical, _record, _path = agent_runs._historical_run_context(ctx, analyze.run_spec(),
        source_run_id=source_run_id, ledger_source_path=ctx.ledger.path)
    if historical.code != ctx.code or prov.code_version() != ctx.code:
        raise ProvenanceRefused('原 Analyze 代码与当前代码不同；须用冻结原代码排空旧 owner，不能重买')
    # 原 run 的可写 context 只用于已有 checkpoint/提交尾窗；禁止 driver 与新内容轮。
    source_ctx = RunContext(novel_dir=ctx.novel_dir, node=analyze.NODE, paid=False, code=ctx.code,
        cache=ctx.cache, ledger=historical.ledger, run_id=source_run_id,
        executor_profile=historical.executor_profile)
    source_prepared = analyze.prepare(source_ctx)
    if source_prepared.request() != prepared.request():
        raise ProvenanceRefused('原 Analyze 补尾请求与本次核验来源不一致')
    # agent 生产臂已退役：这里只接受旧代码已经落下的 typed checkpoint。
    checkpoint = agent_runs.restore_checkpoint(source_ctx, analyze.run_spec(), **source_prepared.request())
    if checkpoint is None:
        raise ProvenanceRefused(
            '原 Analyze agent run 缺少 typed checkpoint；现役代码不再编译 agent raw，'
            '须用冻结旧代码恢复')
    restored = agent_runs.restore_historical_run(ctx, analyze.run_spec(), source_run_id=source_run_id,
        **prepared.request(), ledger_source_path=ctx.ledger.path)
    source_store = ArtifactStore(source_ctx)
    checkpoint = restored.checkpoint
    if source_store.exists(STAGE2_ANALYZE_RECEIPT):
        receipt = source_store.read_own(AnalyzeReceipt, STAGE2_ANALYZE_RECEIPT)[0]
        if receipt.run_id != source_run_id or receipt.checkpoint_sha256 != checkpoint.checkpoint_sha256:
            raise ProvenanceRefused('已有联合回执不属于指定原回答')
    else:
        usage = restored.context.ledger.usage_for_complete_purposes([analyze.NODE], sid=ctx.sid,
            node=analyze.NODE, required_identities=[(analyze.NODE, row)
                for row in checkpoint.acceptance['attempt_links']])
        source = LlmProvenance(code=restored.context.code, root_clean_sha1=source_ctx.root_clean_sha1,
            inputs=source_ctx.edges, prompts={'ANALYZE_SYS': prov.fingerprint(analyze.SYSTEM)},
            model=restored.context.executor_profile.model,
            adopted_models=[checkpoint.acceptance['model']], usage=usage)
        receipt = analyze.receipt_from_checkpoint(restored.context, source_prepared, checkpoint, source)
    result = analyze._commit(source_ctx, source_prepared, checkpoint, receipt)
    failures = [row for row in result.materializations if row.state != 'formal']
    if failures:
        target = next((row.target_rel for row in failures if row.target_rel), analyze.REL)
        raise GateBlocked('原 Analyze 初次提交已免费补齐；问题侧按独立 owner 修订',
                          invalid_path=source_store.path(target))
    analyze.refresh_review(source_ctx)
    return result
