#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage5 formal reviewer rewrite request 的唯一物化与 queue 查询口。

这条路把“Commander 认为某个 current 超长章需要语义整章改写”先冻结成一份
canonical、hash-bound 请求，再由 ``stage5.write`` 付费执行。request 本身不等于
执行状态：正文被 HARD 拦成 ``.INVALID`` 或被 regression 拒绝时 canonical 旧章不变，
仍须靠 append-only execution journal 证明这份请求已经消费，禁止下一 parent 再买席。
"""
from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from contracts.artifacts import STAGE5_REVIEWER_REWRITE_REQUEST, artifact_rel
from contracts.schema.api import Stage5ReviewerRewriteRequest
from contracts.schema.common import RoundLlmProvenance
from contracts.schema.evaluation import TrimAudit
from contracts.schema.stage5 import (
    Chapter, RejectedRevision, Review, ReviewerRewriteDirective,
    ReviewerRewriteDisposition, ReviewerRewriteExecutionState,
    ReviewerRewriteProbe,
    ReviewerRewriteRequest, ReviewerRewriteSubject,
    ReviewerRewriteBatchRequest, ReviewerRewriteBatchTrustedInput,
    ReviewerRewriteSuccessorJournal, ReviewerRewriteTrustedInput,
    reviewer_rewrite_request_sha256,
)
from novel import paths
from novel.errors import (
    ArtifactUnreadable, ProvenanceRefused, UpstreamMissing, UpstreamStale,
    UpstreamUnproven,
)
from novel.llm import agent_runs, stage5_runs
from novel.run import RunContext
from novel.stages.stage5 import closure, review
from novel.store import ArtifactStore


def _file_sha256(path: Path, *, label: str) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ArtifactUnreadable(f'{label} {path} 不可读：{exc}') from exc


def _load_trusted_input(path: Path) -> ReviewerRewriteBatchTrustedInput:
    """外部临时输入也拒绝 duplicate keys/NaN，不让 JSON parser 静默覆盖身份。"""
    def reject_constant(value: str) -> None:
        raise ValueError(f'JSON 不接受 {value}')

    def reject_duplicate(pairs: list[tuple[str, object]]) -> dict[str, object]:
        out: dict[str, object] = {}
        for key, value in pairs:
            if key in out:
                raise ValueError(f'JSON object 含重复键 {key!r}')
            out[key] = value
        return out

    try:
        payload = json.loads(
            path.read_text(encoding='utf-8'),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate,
        )
        return ReviewerRewriteBatchTrustedInput.model_validate(payload)
    except FileNotFoundError:
        raise ProvenanceRefused(f'reviewer rewrite trusted input 不存在：{path}') from None
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError,
            ValidationError) as exc:
        raise ArtifactUnreadable(
            f'reviewer rewrite trusted input 不符合 typed 契约：{exc}') from exc


def load_reviewer_rewrite_batch(
        ctx: RunContext,
        request_rel: str = STAGE5_REVIEWER_REWRITE_REQUEST,
) -> tuple[ReviewerRewriteBatchRequest, str]:
    """一次 Store 读取绑定整个 current v2 批次与精确文件 SHA。"""
    if request_rel != STAGE5_REVIEWER_REWRITE_REQUEST:
        raise ProvenanceRefused('reviewer rewrite 只接受 canonical request 路径')
    return ArtifactStore(ctx).read_own_with_sha256(ReviewerRewriteBatchRequest, request_rel)


def load_reviewer_rewrite_request(
        ctx: RunContext,
        request_rel: str = STAGE5_REVIEWER_REWRITE_REQUEST,
        *, request_sha256: str | None = None, target_ordinal: int | None = None,
) -> tuple[ReviewerRewriteRequest, str]:
    """显式定位批次中的单元；多目标时不得暗选第一章。"""
    batch, digest = load_reviewer_rewrite_batch(ctx, request_rel)
    matches = [row for row in batch.requests
               if (request_sha256 is None or row.request_sha256 == request_sha256)
               and (target_ordinal is None or row.target_ordinal == target_ordinal)]
    if len(matches) != 1:
        raise ProvenanceRefused('reviewer rewrite 单章读取必须唯一绑定批内目标或 SHA')
    return matches[0], digest


def _current_request_from_input(
        ctx: RunContext,
        trusted: ReviewerRewriteTrustedInput,
) -> ReviewerRewriteRequest:
    """在同一 current 快照上闭合 Review、完整 trim frontier、目标章与 evidence。"""
    store = ArtifactStore(ctx)
    pack = closure.closure_workpack(store)
    if pack.review_sha256 != trusted.review_sha256:
        raise ProvenanceRefused(
            'reviewer rewrite 输入绑定的 Review SHA 已非 current')
    subjects = tuple(
        ReviewerRewriteSubject(
            output_ordinal=row.output_ordinal,
            audit_sha256=row.audit_sha256,
            chapter_sha256=row.chapter_sha256,
        )
        for row in pack.unresolved_trim_items
    )
    if subjects != trusted.unresolved_trim_items:
        raise ProvenanceRefused(
            'reviewer rewrite 输入不是 current 完整有序 unresolved trim frontier')
    target = next(
        (row for row in subjects
         if row.output_ordinal == trusted.target_ordinal), None)
    if target is None:
        raise ProvenanceRefused('reviewer rewrite target 已离开 current unresolved frontier')
    chapter_rel = artifact_rel('stage5.chapter', nn=trusted.target_ordinal)
    chapter, chapter_file_sha256 = store.read_own_with_sha256(
        Chapter, chapter_rel, require_current=True)
    chapter_text_sha256 = hashlib.sha256(
        chapter.text.encode('utf-8')).hexdigest()
    if chapter_text_sha256 != target.chapter_sha256:
        raise ProvenanceRefused(
            'reviewer rewrite target chapter 正文 SHA 与 frontier 漂移')
    if chapter_file_sha256 != trusted.target_chapter_file_sha256:
        raise ProvenanceRefused(
            'reviewer rewrite target chapter 完整文件 SHA 与决定漂移')
    target_audit, target_audit_file_sha256 = store.read_own_with_sha256(
        TrimAudit,
        artifact_rel('stage5.trim_audit', nn=trusted.target_ordinal),
        require_current=True,
    )
    if (target_audit_file_sha256 != target.audit_sha256
            or target_audit.before_sha256 != chapter_text_sha256):
        raise ProvenanceRefused(
            'reviewer rewrite target audit/raw/text SHA 关系漂移')
    for directive in trusted.directives:
        matches = chapter.text.count(directive.current_text_evidence)
        if matches != 1:
            raise ProvenanceRefused(
                f'reviewer rewrite {directive.code} evidence 必须在 current 第 '
                f'{trusted.target_ordinal} 章正文中恰命中一次，实得 {matches}')
    raw: dict[str, Any] = {
        'version': 1,
        'kind': 'stage5-reviewer-rewrite-request-v1',
        'actor': trusted.actor,
        'reason': trusted.reason,
        'review_rel': pack.review_rel,
        'review_sha256': trusted.review_sha256,
        'unresolved_trim_items': [row.model_dump(mode='json') for row in subjects],
        'target_ordinal': trusted.target_ordinal,
        'target_chapter_file_sha256': trusted.target_chapter_file_sha256,
        'directives': [
            row.model_dump(mode='json') for row in trusted.directives],
        'reviewer_action_record_sha256': trusted.reviewer_action_record_sha256,
        'business_sha256': trusted.business_sha256,
        'reviewer_contract_sha256': trusted.reviewer_contract_sha256,
        'reviewer_session_run_id': trusted.reviewer_session_run_id,
        'reviewer_seat_turn_id': trusted.reviewer_seat_turn_id,
        'provider_response_sha256': trusted.provider_response_sha256,
        'request_sha256': '0' * 64,
    }
    raw['request_sha256'] = reviewer_rewrite_request_sha256(raw)
    return ReviewerRewriteRequest.model_validate(raw)


def require_current_reviewer_rewrite_request(
        ctx: RunContext, request_rel: str = STAGE5_REVIEWER_REWRITE_REQUEST,
        *, request_sha256: str | None = None,
) -> tuple[ReviewerRewriteRequest, str]:
    """目标 CAS 不变；兄弟章变化仅接受同批 exact formal execution 证明。"""
    # closure 的 trim inventory 会读取 audit 上游；这里仅验证既有 request 的
    # 授权，不能把这些读取混入 writer 的正文生成依赖。request 自身及旧正文
    # 仍由冻结 prompt、request SHA 和执行 journal 绑定，不因此放宽 current 闸。
    with ctx.validation_reads():
        return _require_current_reviewer_rewrite_request(
            ctx, request_rel, request_sha256=request_sha256)


def _require_current_reviewer_rewrite_request(
        ctx: RunContext, request_rel: str,
        *, request_sha256: str | None,
) -> tuple[ReviewerRewriteRequest, str]:
    request, digest = load_reviewer_rewrite_request(
        ctx, request_rel, request_sha256=request_sha256)
    batch, batch_digest = load_reviewer_rewrite_batch(ctx, request_rel)
    if batch_digest != digest:
        raise ProvenanceRefused('reviewer rewrite 批次读取期间漂移')
    _require_request_authority(ctx, request, digest)
    from novel.orchestrator import queue, reviewer_legacy
    contract = hashlib.sha256(reviewer_legacy.for_blocker(
        queue.WAITING_ON_STAGE5_CLOSURE).encode('utf-8')).hexdigest()
    if request.reviewer_contract_sha256 != contract:
        raise UpstreamStale('reviewer rewrite authority 所绑 Reviewer contract 已非 current')
    store = ArtifactStore(ctx)
    replaced: set[str] = set()
    for sibling in batch.requests:
        rel = artifact_rel('stage5.chapter', nn=sibling.target_ordinal)
        _chapter, current_sha = store.read_own_with_sha256(Chapter, rel, require_current=True)
        if current_sha == sibling.target_chapter_file_sha256:
            continue
        if sibling.target_ordinal == request.target_ordinal:
            raise ProvenanceRefused('reviewer rewrite 当前目标已变化，不能按原授权重新写作')
        terminal = _unit_execution_state(ctx, sibling, digest, request_rel, terminal_only=True)
        if terminal.state != 'landed_formal' or not terminal.terminal_current:
            raise ProvenanceRefused('兄弟章变化不是同批已正式交付的改写')
        replaced.add(rel)
    if not replaced:
        if _current_request_from_input(ctx, _trusted_from_request(request)) != request:
            raise ProvenanceRefused('canonical reviewer rewrite request 与 current frontier 漂移')
        return request, digest
    # 同批 formal 只使这些确证兄弟章的 Review 输入边过期；其余 upstream/规则、
    # Review 原字节及所有未改章节的 audit/text 都必须仍与冻结批次相同。
    latest = review.latest_review(store)
    if latest is None or review.REVIEW_TPL.format(n=latest[0]) != request.review_rel:
        raise UpstreamStale('reviewer rewrite 批次未收口前 Review 已换代')
    _review, review_sha = store.read_own_with_sha256(
        Review, request.review_rel, require_current=True, replaced_input_keys=replaced)
    if review_sha != request.review_sha256:
        raise ProvenanceRefused('reviewer rewrite 批次的原 Review 字节变化')
    _frozen_request_evidence(ctx, request)
    for subject in request.unresolved_trim_items:
        rel = artifact_rel('stage5.chapter', nn=subject.output_ordinal)
        if rel in replaced:
            continue
        chapter, chapter_sha = store.read_own_with_sha256(Chapter, rel, require_current=True)
        audit, audit_sha = store.read_own_with_sha256(
            TrimAudit, artifact_rel('stage5.trim_audit', nn=subject.output_ordinal),
            require_current=True)
        text_sha = hashlib.sha256(chapter.text.encode('utf-8')).hexdigest()
        if (text_sha != subject.chapter_sha256 or audit_sha != subject.audit_sha256
                or audit.before_sha256 != text_sha):
            raise ProvenanceRefused('reviewer rewrite 未改章节的正文/audit frontier 漂移')
        if subject.output_ordinal == request.target_ordinal:
            if chapter_sha != request.target_chapter_file_sha256:
                raise ProvenanceRefused('reviewer rewrite 目标完整文件 SHA 漂移')
            if any(chapter.text.count(row.current_text_evidence) != 1 for row in request.directives):
                raise ProvenanceRefused('reviewer rewrite 目标逐字 evidence 漂移')
    return request, digest


def _trusted_from_request(
        request: ReviewerRewriteRequest,
) -> ReviewerRewriteTrustedInput:
    return ReviewerRewriteTrustedInput(
        actor=request.actor,
        reason=request.reason,
        review_sha256=request.review_sha256,
        unresolved_trim_items=request.unresolved_trim_items,
        target_ordinal=request.target_ordinal,
        target_chapter_file_sha256=request.target_chapter_file_sha256,
        directives=request.directives,
        reviewer_action_record_sha256=request.reviewer_action_record_sha256,
        business_sha256=request.business_sha256,
        reviewer_contract_sha256=request.reviewer_contract_sha256,
        reviewer_session_run_id=request.reviewer_session_run_id,
        reviewer_seat_turn_id=request.reviewer_seat_turn_id,
        provider_response_sha256=request.provider_response_sha256,
    )


def _require_request_authority(
        ctx: RunContext, request: ReviewerRewriteRequest,
        request_file_sha256: str,
) -> None:
    """即使正文已落使 request stale，仍逐次复验其人审/席位原始证据。"""
    from novel.orchestrator import reviewer_action_evidence
    batch, digest = load_reviewer_rewrite_batch(ctx)
    if digest != request_file_sha256 or batch.by_sha256(request.request_sha256) != request:
        raise ProvenanceRefused('reviewer rewrite 单元不属于 canonical 原批次')
    reviewer_action_evidence.require_sealed_reviewer_rewrite_authority(
        Path(ctx.novel_dir).parent, ctx.sid, batch, digest)


def _terminal_locations(
        store: ArtifactStore, canonical_rel: str,
) -> tuple[tuple[str, bool], ...]:
    rels: list[tuple[str, bool]] = []
    if store.exists(canonical_rel):
        rels.append((canonical_rel, True))
    rounds_rel = paths.rounds_dir_of(canonical_rel)
    rounds_path = store.path(rounds_rel)
    for number in sorted(paths.archived_rounds(
            rounds_path, paths.stem_of(canonical_rel))):
        rels.append((paths.round_rel(canonical_rel, number), False))
    return tuple(rels)


def _artifact_request_digest(artifact: Chapter | RejectedRevision) -> str | None:
    if isinstance(artifact, Chapter):
        provenance = artifact.provenance
        return (
            provenance.reviewer_rewrite_request_sha256
            if isinstance(provenance, RoundLlmProvenance) else None)
    return artifact.reviewer_rewrite_request_sha256


def _terminal_carriers(
        store: ArtifactStore, request: ReviewerRewriteRequest,
) -> tuple[tuple[str, str, str, str, bool], ...]:
    """枚举 request 的 exact typed live/history terminal carriers。

    每项是 ``(state, canonical_rel, file_sha256, payload_sha256)``。归档只改变
    物理位置；journal 的 ``landed_rel`` 始终是当时提交的 canonical rel。
    """
    ordinal = request.target_ordinal
    found: list[tuple[str, str, str, str, bool]] = []
    for state, canonical_rel, model in (
        ('landed_formal', artifact_rel('stage5.chapter', nn=ordinal), Chapter),
        ('landed_invalid', paths.invalid_of(
            artifact_rel('stage5.chapter', nn=ordinal)), Chapter),
        ('landed_rejected', artifact_rel(
            'stage5.rejected_revision', nn=ordinal), RejectedRevision),
    ):
        for actual_rel, is_current in _terminal_locations(store, canonical_rel):
            # 候选位置一旦存在就必须能 typed 读取。坏掉的 live/history 文件可能
            # 正是本 request 的 carrier；在读出 request digest 前没有可信依据把它
            # 当成“不相关旧轮次”略过。
            artifact, file_sha256 = store.read_own_with_sha256(
                model, actual_rel, require_current=False)
            if state == 'landed_invalid' and is_current:
                assert isinstance(artifact, Chapter)
                locator = artifact.provenance.invalid_baseline_sha256
                if locator is not None:
                    baseline = store.revalidation_baseline(
                        artifact_rel('stage5.chapter', nn=ordinal), Chapter,
                        actor='reviewer-rewrite-terminal-reader',
                        reason='复验 request-bound INVALID immutable baseline')
                    audit = store.revalidation_audit(
                        artifact_rel('stage5.chapter', nn=ordinal), Chapter,
                        actor='reviewer-rewrite-terminal-reader',
                        reason='复验 request-bound INVALID immutable baseline')
                    if audit.baseline_raw_sha256 != file_sha256:
                        # live INVALID 可被人工编辑；terminal 原字节仍由它声明的
                        # content-addressed baseline 永久保存。把该 baseline 视为
                        # 当前问题候选的 carrier，不让编辑后的 raw 冒充 execution 落盘。
                        artifact = baseline
                        file_sha256 = audit.baseline_raw_sha256
            if _artifact_request_digest(artifact) != request.request_sha256:
                continue
            payload_sha256 = agent_runs.canonical_sha256(
                artifact.model_dump(by_alias=True, mode='json'))
            found.append((
                state, canonical_rel, file_sha256, payload_sha256, is_current))
    return tuple(found)


def _exact_historical_artifact(
        store: ArtifactStore, model: type[Any], canonical_rel: str,
        expected_file_sha256: str, *, label: str,
) -> Any:
    """在 live/round history 中按 raw SHA 找到至少一份 exact typed artifact。"""
    matches: list[Any] = []
    raws: set[bytes] = set()
    for actual_rel, _is_current in _terminal_locations(store, canonical_rel):
        path = store.path(actual_rel)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ArtifactUnreadable(f'{label} {path} 不可读：{exc}') from exc
        if hashlib.sha256(raw).hexdigest() != expected_file_sha256:
            continue
        artifact, file_sha256 = store.read_own_with_sha256(
            model, actual_rel, require_current=False)
        if file_sha256 != expected_file_sha256:
            raise ProvenanceRefused(f'{label} 同次读取 raw SHA 漂移')
        raws.add(raw)
        matches.append(artifact)
    if not matches or len(raws) != 1 or any(row != matches[0] for row in matches[1:]):
        raise ProvenanceRefused(f'{label} 未唯一命中同一份 exact typed 原字节')
    return matches[0]


def _frozen_request_evidence(
        ctx: RunContext, request: ReviewerRewriteRequest,
) -> Chapter:
    """终态/恢复仍须证明 request 所见 Review、audit、chapter 原字节在历史中。"""
    store = ArtifactStore(ctx)
    _exact_historical_artifact(
        store, Review, request.review_rel, request.review_sha256,
        label='reviewer rewrite Review')
    target_chapter: Chapter | None = None
    for subject in request.unresolved_trim_items:
        chapter_rel = artifact_rel('stage5.chapter', nn=subject.output_ordinal)
        if subject.output_ordinal == request.target_ordinal:
            chapter = _exact_historical_artifact(
                store, Chapter, chapter_rel,
                request.target_chapter_file_sha256,
                label=f'reviewer rewrite 第 {subject.output_ordinal} 章 target')
        else:
            # frontier 的 chapter_sha256 按契约是 rendered 正文 SHA，不是 JSON
            # 文件 SHA。非 target 没有 raw 格，只能逐份 typed 扫 live/history；
            # 任一候选坏件都关闭式失败，命中的正文必须逐字相同。
            matches: list[Chapter] = []
            for actual_rel, _is_current in _terminal_locations(store, chapter_rel):
                candidate, _file_sha = store.read_own_with_sha256(
                    Chapter, actual_rel, require_current=False)
                if hashlib.sha256(candidate.text.encode('utf-8')).hexdigest() \
                        == subject.chapter_sha256:
                    matches.append(candidate)
            if not matches:
                raise ProvenanceRefused(
                    f'reviewer rewrite 第 {subject.output_ordinal} 章未命中 frozen 正文 SHA')
            chapter = matches[0]
        if hashlib.sha256(chapter.text.encode('utf-8')).hexdigest() \
                != subject.chapter_sha256:
            raise ProvenanceRefused(
                f'reviewer rewrite 第 {subject.output_ordinal} 章 frozen 正文 SHA 漂移')
        audit = _exact_historical_artifact(
            store, TrimAudit,
            artifact_rel('stage5.trim_audit', nn=subject.output_ordinal),
            subject.audit_sha256,
            label=f'reviewer rewrite 第 {subject.output_ordinal} 章 TrimAudit')
        if (audit.before_sha256
                != hashlib.sha256(chapter.text.encode('utf-8')).hexdigest()):
            raise ProvenanceRefused(
                f'reviewer rewrite 第 {subject.output_ordinal} 章 audit/chapter 关系漂移')
        if subject.output_ordinal == request.target_ordinal:
            target_chapter = chapter
    if target_chapter is None:  # schema 已挡；保留 producer 关闭式断言。
        raise ProvenanceRefused('reviewer rewrite frozen target chapter 缺失')
    return target_chapter


def _restore_frozen_checkpoint(
        ctx: RunContext, request: ReviewerRewriteRequest,
        request_file_sha256: str, source_run_id: str,
) -> tuple[stage5_runs.HistoricalReviewerRewrite, Any]:
    """以 request 的历史原字节而非已经变化的 live chapter 重建 exact prompt。"""
    old_chapter = _frozen_request_evidence(ctx, request)
    store = ArtifactStore(ctx)
    from novel.stages.stage5 import write
    book = review.load_book(ctx, store)
    entry = book.chapters.get(request.target_ordinal)
    if entry is None:
        raise ProvenanceRefused('reviewer rewrite frozen target 已不在 plan')
    prepared = write.prepare_chapter_candidate(
        entry, book, revision=None,
        reviewer_rewrite_request=(
            request, request_file_sha256, old_chapter.text),
    )
    restored = stage5_runs.restore_historical_reviewer_rewrite(
        ctx,
        source_run_id=source_run_id,
        chapter=request.target_ordinal,
        input_sha256=prepared._input_sha256,
        system=prepared.system,
        user=prepared.user,
        request_sha256=request.request_sha256,
        request_file_sha256=request_file_sha256,
        response_model=write.ChapterResponse,
        max_tokens=write.MAX_TOKENS,
        validator=lambda obj, wire: write._response_problem(
            obj, wire, prepared._entry),
    )
    return restored, prepared


def _terminal_artifact_state(
        store: ArtifactStore, request: ReviewerRewriteRequest,
) -> tuple[str, str, str, str, bool] | None:
    """journal 尾写前硬杀时，从 typed 终态产物反推出已消费。"""
    matches = list(_terminal_carriers(store, request))
    if not matches:
        return None
    if len(matches) != 1:
        raise ProvenanceRefused(
            '同一 reviewer rewrite request 命中多个 live/history terminal artifact')
    return matches[0]


def _terminal_row_closes_artifact(
        row: Any, carriers: tuple[tuple[str, str, str, str, bool], ...],
) -> bool:
    return sum(
        1 for state, rel, file_sha256, payload_sha256, _current in carriers
        if (row.event == state and row.landed_rel == rel
            and row.landed_file_sha256 == file_sha256
            and row.landed_payload_sha256 == payload_sha256)
    ) == 1


def _restore_active_checkpoint(
        ctx: RunContext, request: ReviewerRewriteRequest,
        request_file_sha256: str, source_run_id: str,
) -> stage5_runs.HistoricalReviewerRewrite:
    """用 current prompt + source 三账深验唯一历史 accepted checkpoint。"""
    # 先把 Review/frontier/action/current old chapter 全闭合，才允许把历史响应
    # 标成 recoverable；否则即使 checkpoint 原字节完整也只能停在 unknown。
    checked, checked_file_sha256 = require_current_reviewer_rewrite_request(
        ctx, STAGE5_REVIEWER_REWRITE_REQUEST,
        request_sha256=request.request_sha256)
    if checked != request or checked_file_sha256 != request_file_sha256:
        raise ProvenanceRefused('reviewer rewrite recovery 前 canonical request 漂移')
    restored, _prepared = _restore_frozen_checkpoint(
        ctx, request, request_file_sha256, source_run_id)
    return restored


def _recovery_wal_closes(
        ctx: RunContext, request: ReviewerRewriteRequest,
        request_file_sha256: str, wal: Any,
        restored: stage5_runs.HistoricalReviewerRewrite,
        prepared: Any,
        carrier: tuple[str, str, str, str, bool] | None,
) -> None:
    """复验 free target WAL 与 source/request/terminal carrier 的完整身份。"""
    acceptance = restored.historical.checkpoint.acceptance
    run_record = restored.historical.run_record
    checkpoint = restored.historical.checkpoint
    checkpoint_path = stage5_runs.reviewer_rewrite_checkpoint_file(
        ctx, run_id=wal.source_run_id, chapter=request.target_ordinal,
        checkpoint_sha256=checkpoint.checkpoint_sha256)
    commit = restored.commit
    commit_sha = None if commit is None else commit.get('commit_sha256')
    commit_file_sha: str | None = None
    if commit is not None:
        commit_path = (
            paths.stage5_chapter_run_path(
                ctx.novel_dir, wal.source_run_id, request.target_ordinal)
            / 'committed.json')
        commit_file_sha = _file_sha256(
            commit_path, label='reviewer rewrite source commit')
    expected_adoption = stage5_runs.reviewer_rewrite_source_adoption_id(
        sid=ctx.sid,
        source_run_id=wal.source_run_id,
        chapter=request.target_ordinal,
        request_sha256=request.request_sha256,
        source_run_identity_sha256=str(run_record['identity_sha256']),
        source_run_file_sha256=restored.historical.run_sha256,
        source_checkpoint_sha256=checkpoint.checkpoint_sha256,
        source_checkpoint_file_sha256=_file_sha256(
            checkpoint_path, label='reviewer rewrite source checkpoint'),
        source_commit_sha256=None if commit_sha is None else str(commit_sha),
        source_commit_file_sha256=commit_file_sha,
        source_response_sha256=str(acceptance['ledger_response_sha256']),
    )
    expected = {
        'request_rel': STAGE5_REVIEWER_REWRITE_REQUEST,
        'request_file_sha256': request_file_sha256,
        'request_sha256': request.request_sha256,
        'reviewer_action_record_sha256': request.reviewer_action_record_sha256,
        'business_sha256': request.business_sha256,
        'reviewer_contract_sha256': request.reviewer_contract_sha256,
        'input_sha256': prepared._input_sha256,
        'source_run_id': restored.historical.context.run_id,
        'source_code': str(run_record['code']),
        'source_run_identity_sha256': str(run_record['identity_sha256']),
        'source_run_file_sha256': restored.historical.run_sha256,
        'source_checkpoint_sha256': checkpoint.checkpoint_sha256,
        'source_checkpoint_file_sha256': _file_sha256(
            checkpoint_path, label='reviewer rewrite source checkpoint'),
        'source_checkpoint_revision': checkpoint.revision,
        'source_commit_sha256': None if commit_sha is None else str(commit_sha),
        'source_commit_file_sha256': commit_file_sha,
        'source_attempt_id': str(acceptance['attempt_id']),
        'source_response_sha256': str(acceptance['ledger_response_sha256']),
        'source_request_fingerprint': str(acceptance['request_fingerprint']),
        'source_session_run_id': str(acceptance['session_run_id']),
        'source_provider_session_id': str(acceptance['provider_session_id']),
        'source_adoption_id': expected_adoption,
    }
    if any(getattr(wal, key) != value for key, value in expected.items()):
        raise ProvenanceRefused(
            'reviewer rewrite recovery WAL 与 request/source 三账身份漂移')
    stage5_runs.verify_reviewer_rewrite_recovery_source(ctx, wal)
    if wal.state == 'committed':
        if carrier is None:
            raise ProvenanceRefused(
                'reviewer rewrite committed recovery WAL 缺 terminal carrier')
        state, rel, file_sha256, payload_sha256, _current = carrier
        expected_state = f'landed_{wal.delivery_outcome}'
        if (state != expected_state or rel != wal.delivery_rel
                or file_sha256 != wal.landed_file_sha256
                or payload_sha256 != wal.delivery_payload_sha256):
            raise ProvenanceRefused(
                'reviewer rewrite committed recovery WAL/terminal artifact 漂移')


def _historical_recovery_wal_closes(
        ctx: RunContext, request: ReviewerRewriteRequest,
        request_file_sha256: str, wal: Any,
        historical: stage5_runs.HistoricalReviewerRewriteTerminal,
        carrier: tuple[str, str, str, str, bool] | None,
) -> None:
    """已 committed WAL 只按 source 当时记录的三账与 exact carrier 闭合。"""
    checkpoint = historical.checkpoint
    run_record = historical.run_record
    acceptance = checkpoint
    checkpoint_path = stage5_runs.reviewer_rewrite_checkpoint_file(
        ctx, run_id=wal.source_run_id, chapter=request.target_ordinal,
        checkpoint_sha256=str(checkpoint['checkpoint_sha256']))
    commit = historical.commit
    commit_sha = None if commit is None else str(commit['commit_sha256'])
    commit_file_sha: str | None = None
    if commit is not None:
        commit_file_sha = stage5_runs.reviewer_rewrite_source_file_sha256(
            ctx, run_id=wal.source_run_id, chapter=request.target_ordinal,
            filename=agent_runs.COMMIT_FILE)
    expected_adoption = stage5_runs.reviewer_rewrite_source_adoption_id(
        sid=ctx.sid, source_run_id=wal.source_run_id,
        chapter=request.target_ordinal,
        request_sha256=request.request_sha256,
        source_run_identity_sha256=str(run_record['identity_sha256']),
        source_run_file_sha256=historical.run_sha256,
        source_checkpoint_sha256=str(checkpoint['checkpoint_sha256']),
        source_checkpoint_file_sha256=_file_sha256(
            checkpoint_path, label='reviewer rewrite source checkpoint'),
        source_commit_sha256=commit_sha,
        source_commit_file_sha256=commit_file_sha,
        source_response_sha256=str(acceptance['ledger_response_sha256']),
    )
    expected = {
        'request_rel': STAGE5_REVIEWER_REWRITE_REQUEST,
        'request_file_sha256': request_file_sha256,
        'request_sha256': request.request_sha256,
        'reviewer_action_record_sha256': request.reviewer_action_record_sha256,
        'business_sha256': request.business_sha256,
        'reviewer_contract_sha256': request.reviewer_contract_sha256,
        'input_sha256': str(run_record['input_sha256']),
        'source_run_id': str(run_record['run_id']),
        'source_code': str(run_record['code']),
        'source_run_identity_sha256': str(run_record['identity_sha256']),
        'source_run_file_sha256': historical.run_sha256,
        'source_checkpoint_sha256': str(checkpoint['checkpoint_sha256']),
        'source_checkpoint_file_sha256': _file_sha256(
            checkpoint_path, label='reviewer rewrite source checkpoint'),
        'source_checkpoint_revision': int(checkpoint['revision']),
        'source_commit_sha256': commit_sha,
        'source_commit_file_sha256': commit_file_sha,
        'source_attempt_id': str(acceptance['attempt_id']),
        'source_response_sha256': str(acceptance['ledger_response_sha256']),
        'source_request_fingerprint': str(acceptance['request_fingerprint']),
        'source_session_run_id': str(acceptance['session_run_id']),
        'source_provider_session_id': str(acceptance['provider_session_id']),
        'source_adoption_id': expected_adoption,
    }
    if any(getattr(wal, key) != value for key, value in expected.items()):
        raise ProvenanceRefused(
            'reviewer rewrite committed recovery WAL 与 historical source 三账漂移')
    stage5_runs.verify_reviewer_rewrite_recovery_source(ctx, wal)
    if wal.state != 'committed' or carrier is None:
        raise ProvenanceRefused(
            'historical recovery terminal 缺 committed WAL/exact carrier')
    state, rel, file_sha256, payload_sha256, _current = carrier
    if (state != f'landed_{wal.delivery_outcome}'
            or rel != wal.delivery_rel
            or file_sha256 != wal.landed_file_sha256
            or payload_sha256 != wal.delivery_payload_sha256):
        raise ProvenanceRefused(
            'reviewer rewrite committed recovery WAL/terminal artifact 漂移')


def _unstarted_disposition_evidence(
        ctx: RunContext, request: ReviewerRewriteRequest,
        request_file_sha256: str,
        chains: tuple[tuple[Any, ...], ...],
        recoveries: tuple[Any, ...],
        carriers: tuple[tuple[str, str, str, str, bool], ...],
) -> dict[str, Any]:
    """在 execution lease/书锁快照内证明 request 从未执行。"""
    if recoveries or carriers:
        raise ProvenanceRefused(
            '已有 recovery/terminal 证据的 reviewer rewrite 不能退休成 unstarted')
    active = [
        chain for chain in chains
        if chain[-1].event != 'reclaimed_unstarted']
    retired = [
        chain for chain in chains
        if chain[-1].event == 'reclaimed_unstarted']
    if len(active) > 1:
        raise ProvenanceRefused('reviewer rewrite unstarted 发现多条 active chain')
    retired_evidence = [
        stage5_runs.reviewer_rewrite_unstarted_evidence(ctx, chain)
        for chain in retired]
    active_evidence: dict[str, Any] | None = None
    if active:
        chain = active[0]
        from novel.orchestrator import admission
        with admission.require_owner_stopped(
                Path(ctx.novel_dir).parent, chain[0].parent_run_id):
            active_evidence = stage5_runs.reviewer_rewrite_unstarted_evidence(
                ctx, chain)
    return {
        'kind': 'stage5-reviewer-rewrite-request-unstarted-v1',
        'sid': ctx.sid,
        'request_file_sha256': request_file_sha256,
        'request_sha256': request.request_sha256,
        'reviewer_action_record_sha256': request.reviewer_action_record_sha256,
        'target_ordinal': request.target_ordinal,
        'active_claim': active_evidence,
        'retired_claims': retired_evidence,
        'recovery_wal_count': 0,
        'terminal_carrier_count': 0,
    }


def _require_disposition_closes(
        ctx: RunContext, request: ReviewerRewriteRequest,
        request_file_sha256: str, disposition: ReviewerRewriteDisposition,
        chains: tuple[tuple[Any, ...], ...], recoveries: tuple[Any, ...],
        carriers: tuple[tuple[str, str, str, str, bool], ...],
) -> None:
    if (disposition.request_rel != STAGE5_REVIEWER_REWRITE_REQUEST
            or disposition.request_file_sha256 != request_file_sha256
            or disposition.request_sha256 != request.request_sha256
            or disposition.reviewer_action_record_sha256
            != request.reviewer_action_record_sha256
            or disposition.target_ordinal != request.target_ordinal):
        raise ProvenanceRefused(
            'reviewer rewrite disposition 与 canonical request 身份漂移')
    if disposition.disposition == 'expired_unstarted':
        current = _unstarted_disposition_evidence(
            ctx, request, request_file_sha256, chains, recoveries, carriers)
        if (disposition.unstarted_evidence != current
                or disposition.unstarted_evidence_sha256
                != agent_runs.canonical_sha256(current)):
            raise ProvenanceRefused(
                'reviewer rewrite disposition 与 current 全根 zero-execution 证据漂移')
        return
    wal = stage5_runs.reviewer_rewrite_successor_journal(
        ctx, request_sha256=request.request_sha256)
    if wal is None or wal.state != 'committed':
        raise ProvenanceRefused(
            'consumed reviewer rewrite disposition 缺 committed successor WAL')
    _require_successor_wal_closes(
        ctx, request, request_file_sha256, wal, chains, carriers,
        require_committed=True)
    receipt_evidence = {
        'kind': 'stage5-reviewer-rewrite-successor-receipt-v1',
        'successor_journal_sha256': wal.journal_sha256,
        'action_evidence_sha256': wal.action_evidence_sha256,
    }
    if (disposition.terminal_event != wal.terminal_event
            or disposition.terminal_rel != wal.terminal_rel
            or disposition.terminal_file_sha256 != wal.terminal_file_sha256
            or disposition.terminal_payload_sha256 != wal.terminal_payload_sha256
            or disposition.successor_kind != wal.successor_kind
            or disposition.successor_rel != wal.successor_rel
            or disposition.successor_file_sha256 != wal.successor_file_sha256
            or disposition.successor_payload_sha256 != wal.successor_payload_sha256
            or disposition.successor_evidence != receipt_evidence
            or disposition.successor_evidence_sha256
               != agent_runs.canonical_sha256(receipt_evidence)):
        raise ProvenanceRefused(
            'reviewer rewrite consumed disposition 与 committed successor WAL 漂移')


def _require_successor_wal_closes(
        ctx: RunContext, request: ReviewerRewriteRequest,
        request_file_sha256: str, wal: ReviewerRewriteSuccessorJournal,
        chains: tuple[tuple[Any, ...], ...],
        carriers: tuple[tuple[str, str, str, str, bool], ...], *,
        require_committed: bool,
) -> None:
    """深验 successor WAL 的 source terminal、typed target 与动作专属证据。"""
    if (wal.sid != ctx.sid
            or wal.request_rel != STAGE5_REVIEWER_REWRITE_REQUEST
            or wal.request_file_sha256 != request_file_sha256
            or wal.request_sha256 != request.request_sha256
            or wal.reviewer_action_record_sha256
               != request.reviewer_action_record_sha256
            or wal.target_ordinal != request.target_ordinal):
        raise ProvenanceRefused('reviewer rewrite successor WAL/request 身份漂移')
    if require_committed != (wal.state == 'committed'):
        raise ProvenanceRefused('reviewer rewrite successor WAL state 不符合调用边界')
    terminal_rows = [
        chain[-1] for chain in chains if chain[-1].event.startswith('landed_')]
    if len(terminal_rows) != 1:
        raise ProvenanceRefused('reviewer rewrite successor WAL 缺唯一 terminal execution')
    row = terminal_rows[0]
    if (row.event != wal.terminal_event
            or row.landed_rel != wal.terminal_rel
            or row.landed_file_sha256 != wal.terminal_file_sha256
            or row.landed_payload_sha256 != wal.terminal_payload_sha256):
        raise ProvenanceRefused('reviewer rewrite successor WAL/terminal journal 漂移')
    if sum(
            1 for state, rel, file_sha, payload_sha, _current in carriers
            if (state == wal.terminal_event and rel == wal.terminal_rel
                and file_sha == wal.terminal_file_sha256
                and payload_sha == wal.terminal_payload_sha256)) != 1:
        raise ProvenanceRefused(
            'reviewer rewrite successor WAL 未命中唯一 exact terminal carrier')
    terminal_model = (
        RejectedRevision if wal.terminal_event == 'landed_rejected' else Chapter)
    try:
        terminal = terminal_model.model_validate(wal.terminal_payload)
        successor = Chapter.model_validate(wal.successor_payload)
    except ValidationError as exc:
        raise ArtifactUnreadable(
            f'reviewer rewrite successor WAL frozen payload 不可 typed：{exc}') from exc
    if (_artifact_request_digest(terminal) != request.request_sha256
            or terminal.model_dump(by_alias=True, mode='json') != wal.terminal_payload
            or successor.model_dump(by_alias=True, mode='json') != wal.successor_payload):
        raise ProvenanceRefused('reviewer rewrite successor WAL frozen typed payload 漂移')
    changed = sorted(
        key for key in wal.terminal_payload
        if wal.terminal_payload.get(key) != wal.successor_payload.get(key))
    evidence = wal.action_evidence
    common = {
        'kind': 'stage5-reviewer-rewrite-successor-action-v1',
        'successor_kind': wal.successor_kind,
        'request_sha256': request.request_sha256,
        'terminal_file_sha256': wal.terminal_file_sha256,
        'successor_file_sha256': wal.successor_file_sha256,
    }
    if any(evidence.get(key) != value for key, value in common.items()):
        raise ProvenanceRefused('reviewer rewrite successor action evidence 身份漂移')
    if wal.successor_kind == 'recheck':
        if (wal.terminal_event != 'landed_formal'
                or changed
                or evidence.get('changed_fields') != changed):
            raise ProvenanceRefused('reviewer rewrite recheck successor 不得改写最小章主体')
    elif wal.successor_kind == 'revalidate':
        audit = successor.provenance.revalidation_audit
        if (wal.terminal_event != 'landed_invalid'
                or successor.provenance.revalidated is not True
                or audit is None
                or audit.baseline_raw_sha256 != wal.terminal_file_sha256
                or evidence.get('revalidation_audit')
                   != audit.model_dump(mode='json')):
            raise ProvenanceRefused('reviewer rewrite revalidate successor audit 不闭合')
    else:
        if (wal.terminal_event not in {'landed_formal', 'landed_rejected'}
                or not isinstance(successor.provenance, object)
                or getattr(successor.provenance, 'origin', None) != 'human'):
            raise ProvenanceRefused('reviewer rewrite apply-edit successor 类型不闭合')
        action_id = evidence.get('human_action_id')
        from novel import human_actions
        actions = [
            item for item in human_actions.read_events(Path(ctx.novel_dir).parent)
            if item.action_id == action_id]
        if (len(actions) != 1
                or actions[0].sid != ctx.sid
                or actions[0].output_ordinal != request.target_ordinal
                or actions[0].destination_rel != wal.successor_rel
                or actions[0].result_chapter_sha256 != wal.successor_file_sha256):
            raise ProvenanceRefused(
                'reviewer rewrite apply-edit successor 缺唯一 committed human action')
    if wal.state == 'committed':
        # consumption 在当前 request 被换代前必须仍由 exact successor 占 canonical；
        # 后续 request 另有自己的 key，不会再查询这份旧 WAL。
        store = ArtifactStore(ctx)
        successor_live, successor_file_sha = store.read_own_with_sha256(
            Chapter, wal.successor_rel, require_current=False)
        if (successor_live != successor
                or successor_file_sha != wal.successor_file_sha256
                or wal.post_file_sha256s is None
                or wal.post_file_sha256s.get(wal.successor_rel)
                   != wal.successor_file_sha256):
            raise ProvenanceRefused(
                'reviewer rewrite committed successor WAL/live target 漂移')


def _unit_execution_state(
        ctx: RunContext, request: ReviewerRewriteRequest, request_file_sha256: str,
        request_rel: str = STAGE5_REVIEWER_REWRITE_REQUEST,
        *, terminal_only: bool = False,
) -> ReviewerRewriteExecutionState:
    """每章仍按独立九态证明执行；整批只聚合，不压平未知与部分成功。"""
    store = ArtifactStore(ctx)
    from novel.orchestrator import reviewer_action_evidence
    if not reviewer_action_evidence.reviewer_rewrite_authority_sealed(
            Path(ctx.novel_dir).parent, ctx.sid,
            request.reviewer_action_record_sha256):
        return ReviewerRewriteExecutionState(
            request_rel=request_rel,
            request_file_sha256=request_file_sha256,
            request_sha256=request.request_sha256,
            target_ordinal=request.target_ordinal,
            state='in_flight_or_unknown',
            source_run_id=None,
            checkpoint_sha256=None,
            journal_sha256=None,
        )
    _require_request_authority(ctx, request, request_file_sha256)
    all_chains = stage5_runs.reviewer_rewrite_execution_lineage(
        stage5_runs.reviewer_rewrite_execution_chains(
        ctx,
        chapter=request.target_ordinal,
        request_sha256=request.request_sha256,
        request_file_sha256=request_file_sha256,
        ))
    recoveries = stage5_runs.reviewer_rewrite_recovery_journals(
        ctx,
        chapter=request.target_ordinal,
        request_sha256=request.request_sha256,
        request_file_sha256=request_file_sha256,
    )
    retired_chains = [
        chain for chain in all_chains
        if chain[-1].event == 'reclaimed_unstarted']
    for chain in retired_chains:
        stage5_runs.reviewer_rewrite_unstarted_evidence(ctx, chain)
    chains = tuple(
        chain for chain in all_chains
        if chain[-1].event != 'reclaimed_unstarted')
    if len(chains) > 1:
        raise ProvenanceRefused(
            '同一 reviewer rewrite request 出现第二份 paid execution chain')
    if len(recoveries) > 1:
        raise ProvenanceRefused(
            '同一 reviewer rewrite request 出现多份 target recovery WAL')
    carriers = _terminal_carriers(store, request)
    disposition = stage5_runs.reviewer_rewrite_disposition(
        ctx, request_sha256=request.request_sha256)
    if disposition is not None:
        _require_disposition_closes(
            ctx, request, request_file_sha256, disposition,
            all_chains, recoveries, carriers)
        return ReviewerRewriteExecutionState(
            request_rel=request_rel,
            request_file_sha256=request_file_sha256,
            request_sha256=request.request_sha256,
            target_ordinal=request.target_ordinal,
            state='consumed_resolved',
            source_run_id=None,
            checkpoint_sha256=None,
            journal_sha256=None,
            terminal_current=False,
        )
    terminal_artifact = _terminal_artifact_state(store, request)
    terminal_rows = [
        chain[-1] for chain in chains if chain[-1].event.startswith('landed_')]
    # set(event) 会把两次相同 outcome 的重复执行吞掉；terminal chain 必须唯一。
    if len(terminal_rows) > 1:
        raise ProvenanceRefused(
            '同一 reviewer rewrite request 出现多条 terminal execution chain')
    if terminal_rows and not _terminal_row_closes_artifact(
            terminal_rows[0], carriers):
        raise ProvenanceRefused(
            'reviewer rewrite terminal journal 未唯一命中 exact typed live/history artifact')
    if terminal_artifact is not None and terminal_rows:
        row = terminal_rows[0]
        if row.event != terminal_artifact[0]:
            raise ProvenanceRefused(
                'reviewer rewrite terminal journal/artifact outcome 矛盾')
    historical_terminal: stage5_runs.HistoricalReviewerRewriteTerminal | None = None
    if terminal_rows:
        historical_terminal = (
            stage5_runs.verify_historical_reviewer_rewrite_terminal(
                ctx, chain=chains[0], request_sha256=request.request_sha256,
                request_file_sha256=request_file_sha256))
    if recoveries and recoveries[0].state == 'committed':
        if not chains:
            raise ProvenanceRefused(
                'reviewer rewrite committed recovery WAL 缺 source execution chain')
        wal = recoveries[0]
        if wal.source_run_id != chains[0][0].run_id:
            raise ProvenanceRefused(
                'reviewer rewrite recovery WAL/source execution chain 漂移')
        if historical_terminal is None:
            historical_terminal = (
                stage5_runs.verify_historical_reviewer_rewrite_terminal(
                    ctx, chain=chains[0], request_sha256=request.request_sha256,
                    request_file_sha256=request_file_sha256))
        _historical_recovery_wal_closes(
            ctx, request, request_file_sha256, wal,
            historical_terminal, terminal_artifact)
    if (terminal_artifact is not None and chains
            and historical_terminal is None):
        # journal 尾写前硬杀只有 generic committed formal 能直接形成 terminal；
        # INVALID/rejected 没有 commit，稍后仍按 active checkpoint 重放补尾。
        candidate = stage5_runs.verify_historical_reviewer_rewrite_terminal(
            ctx, chain=chains[0], request_sha256=request.request_sha256,
            request_file_sha256=request_file_sha256)
        if candidate.commit is not None and candidate.commit.get('state') == 'committed':
            historical_terminal = candidate
    terminal_durable = bool(
        terminal_rows
        or (recoveries and recoveries[0].state == 'committed')
        or historical_terminal is not None)
    if terminal_durable:
        if terminal_artifact is None:
            raise ProvenanceRefused(
                'reviewer rewrite terminal durable evidence 缺 exact artifact carrier')
        row = terminal_rows[0] if terminal_rows else chains[0][-1]
        carrier_current = terminal_artifact[4]
        if not carrier_current:
            raise ProvenanceRefused(
                'reviewer rewrite terminal 已离开 canonical 路径但缺 typed successor receipt')
        state = terminal_artifact[0]
        return ReviewerRewriteExecutionState(
            request_rel=request_rel,
            request_file_sha256=request_file_sha256,
            request_sha256=request.request_sha256,
            target_ordinal=request.target_ordinal,
            state=state,
            source_run_id=None if row is None else row.run_id,
            checkpoint_sha256=(
                None if row is None else row.checkpoint_sha256),
            journal_sha256=None if row is None else row.journal_sha256,
            terminal_current=carrier_current,
        )
    if terminal_artifact is not None and not chains:
        raise ProvenanceRefused(
            'reviewer rewrite terminal artifact 缺 paid source execution evidence')
    if terminal_only:
        raise ProvenanceRefused('同批兄弟章已变化但没有 exact terminal execution 证据')
    active = [chain[-1] for chain in chains]
    if not active:
        try:
            require_current_reviewer_rewrite_request(
                ctx, request_rel, request_sha256=request.request_sha256)
        except ArtifactUnreadable:
            raise
        except (ProvenanceRefused, UpstreamMissing, UpstreamStale,
                UpstreamUnproven):
            state = 'expired_unstarted'
        else:
            state = 'ready_paid'
        return ReviewerRewriteExecutionState(
            request_rel=request_rel,
            request_file_sha256=request_file_sha256,
            request_sha256=request.request_sha256,
            target_ordinal=request.target_ordinal,
            state=state,
            source_run_id=None,
            checkpoint_sha256=None,
            journal_sha256=None,
        )
    row = active[0]
    if row.event == 'retry_started':
        # 上一 checkpoint 后已经 durable 声明下一次 wire intent。只要还没有
        # 后续 exact checkpoint，这一窗就可能已外发/扣费，绝不能 FREE 收旧稿。
        return ReviewerRewriteExecutionState(
            request_rel=request_rel,
            request_file_sha256=request_file_sha256,
            request_sha256=request.request_sha256,
            target_ordinal=request.target_ordinal,
            state='in_flight_or_unknown',
            source_run_id=row.run_id,
            checkpoint_sha256=row.checkpoint_sha256,
            journal_sha256=row.journal_sha256,
        )
    if row.event == 'started':
        # ``started`` 后没有 accepted checkpoint 就是诚实 unknown；包括 wire
        # 已发但尚未形成可复验响应，绝不能再开第二次 paid execution。
        reclaimable = False
        try:
            from novel.orchestrator import admission
            with admission.require_owner_stopped(
                    Path(ctx.novel_dir).parent, row.parent_run_id):
                stage5_runs.reviewer_rewrite_unstarted_evidence(ctx, chains[0])
                reclaimable = True
        except Exception:
            reclaimable = False
        if reclaimable:
            try:
                require_current_reviewer_rewrite_request(
                ctx, request_rel, request_sha256=request.request_sha256)
            except (ProvenanceRefused, UpstreamMissing, UpstreamStale,
                    UpstreamUnproven):
                reclaim_state = 'expired_unstarted'
            else:
                reclaim_state = 'ready_paid'
            return ReviewerRewriteExecutionState(
                request_rel=request_rel,
                request_file_sha256=request_file_sha256,
                request_sha256=request.request_sha256,
                target_ordinal=request.target_ordinal,
                state=reclaim_state,
                source_run_id=row.run_id,
                checkpoint_sha256=None,
                journal_sha256=row.journal_sha256,
            )
        return ReviewerRewriteExecutionState(
            request_rel=request_rel,
            request_file_sha256=request_file_sha256,
            request_sha256=request.request_sha256,
            target_ordinal=request.target_ordinal,
            state='in_flight_or_unknown',
            source_run_id=row.run_id,
            checkpoint_sha256=None,
            journal_sha256=row.journal_sha256,
        )
    try:
        # 只有 active checkpoint/prepared recovery 才用今天的 plan/prompt/schema/
        # validator 重新判定；historical terminal 已在上方完全绕开当前编译器。
        checked, checked_sha = require_current_reviewer_rewrite_request(
            ctx, request_rel, request_sha256=request.request_sha256)
        if checked != request or checked_sha != request_file_sha256:
            raise ProvenanceRefused('reviewer rewrite active recovery request 漂移')
        restored, prepared_frozen = _restore_frozen_checkpoint(
            ctx, request, request_file_sha256, row.run_id)
    except ArtifactUnreadable:
        raise
    except Exception:
        return ReviewerRewriteExecutionState(
            request_rel=request_rel,
            request_file_sha256=request_file_sha256,
            request_sha256=request.request_sha256,
            target_ordinal=request.target_ordinal,
            state='in_flight_or_unknown',
            source_run_id=row.run_id,
            checkpoint_sha256=row.checkpoint_sha256,
            journal_sha256=row.journal_sha256,
        )
    checkpoint_sha256 = restored.historical.checkpoint.checkpoint_sha256
    if (row.checkpoint_sha256 is not None
            and row.checkpoint_sha256 != checkpoint_sha256):
        raise ProvenanceRefused(
            'reviewer rewrite execution journal/checkpoint SHA 漂移')
    commit = restored.commit
    if commit is not None and commit.get('state') == 'committed':
        raise ProvenanceRefused(
            'reviewer rewrite source commit 已 committed 却缺 exact terminal carrier')
    if recoveries:
        wal = recoveries[0]
        if wal.state != 'prepared' or wal.source_run_id != chains[0][0].run_id:
            raise ProvenanceRefused(
                'reviewer rewrite active recovery WAL/source/state 漂移')
        _recovery_wal_closes(
            ctx, request, request_file_sha256, wal,
            restored, prepared_frozen, terminal_artifact)
    # source 的 generic prepared 仍是已有 checkpoint 的恢复材料，尚不存在可续的
    # target recovery WAL。只有后者已创建才发布其 target run/SHA 并进入 prepared 态。
    status = 'recoverable_prepared' if recoveries else 'recoverable_checkpoint'
    return ReviewerRewriteExecutionState(
        request_rel=request_rel,
        request_file_sha256=request_file_sha256,
        request_sha256=request.request_sha256,
        target_ordinal=request.target_ordinal,
        state=status,
        source_run_id=row.run_id,
        checkpoint_sha256=checkpoint_sha256,
        journal_sha256=row.journal_sha256,
        recovery_target_run_id=(
            recoveries[0].target_run_id if recoveries else None),
        recovery_journal_sha256=(
            recoveries[0].journal_sha256 if recoveries else None),
    )


def reviewer_rewrite_batch_probes(
        ctx: RunContext, request_rel: str = STAGE5_REVIEWER_REWRITE_REQUEST,
) -> tuple[ReviewerRewriteProbe, ...]:
    store = ArtifactStore(ctx)
    if not store.exists(request_rel):
        return ()
    batch, digest = load_reviewer_rewrite_batch(ctx, request_rel)
    probes = tuple(ReviewerRewriteProbe(
        request=request, execution=_unit_execution_state(ctx, request, digest, request_rel),
    ) for request in batch.requests)
    after, after_digest = load_reviewer_rewrite_batch(ctx, request_rel)
    if after != batch or after_digest != digest:
        raise ProvenanceRefused('reviewer rewrite 批次探测期间 canonical 漂移')
    return probes


def _next_batch_probe(probes: tuple[ReviewerRewriteProbe, ...]) -> ReviewerRewriteProbe | None:
    if not probes:
        return None
    # 先补已付费的已知 checkpoint；随后未知/负结论挡新 wire。
    # 全部终结前不会把单个成功单元当成整批已完成而放行 Review。
    priority = (
        'recoverable_prepared', 'recoverable_checkpoint', 'in_flight_or_unknown',
        'landed_invalid', 'landed_rejected', 'expired_unstarted', 'ready_paid',
        'landed_formal', 'consumed_resolved',
    )
    return min(probes, key=lambda row: (
        priority.index(row.execution.state), row.request.target_ordinal))


def current_reviewer_rewrite_request(
        ctx: RunContext, request_rel: str = STAGE5_REVIEWER_REWRITE_REQUEST,
) -> ReviewerRewriteProbe | None:
    """整批下一步的唯一投影；paid 动作消费同批全部 ready 单元。"""
    return _next_batch_probe(reviewer_rewrite_batch_probes(ctx, request_rel))


def reviewer_rewrite_execution_state(
        ctx: RunContext, request_rel: str = STAGE5_REVIEWER_REWRITE_REQUEST,
        *, request_sha256: str | None = None,
) -> ReviewerRewriteExecutionState | None:
    if request_sha256 is not None:
        request, digest = load_reviewer_rewrite_request(
            ctx, request_rel, request_sha256=request_sha256)
        return _unit_execution_state(ctx, request, digest, request_rel)
    probe = current_reviewer_rewrite_request(ctx, request_rel)
    return None if probe is None else probe.execution


def ready_reviewer_rewrite_targets(
        ctx: RunContext, request_rel: str = STAGE5_REVIEWER_REWRITE_REQUEST,
) -> tuple[int, ...]:
    probes = reviewer_rewrite_batch_probes(ctx, request_rel)
    selected = _next_batch_probe(probes)
    if selected is None or selected.execution.state != 'ready_paid':
        raise ProvenanceRefused('reviewer rewrite 批次当前不可新建付费执行')
    return tuple(row.request.target_ordinal for row in probes
                 if row.execution.state == 'ready_paid')


def matching_reviewer_rewrite_recovery_source(
        ctx: RunContext,
        request_rel: str = STAGE5_REVIEWER_REWRITE_REQUEST,
) -> str | None:
    state = reviewer_rewrite_execution_state(ctx, request_rel)
    if (state is None
            or state.state not in (
                'recoverable_checkpoint', 'recoverable_prepared')):
        return None
    if state.source_run_id is None:
        raise ProvenanceRefused('recoverable reviewer rewrite 缺 source run id')
    return state.source_run_id


def _current_batch_from_input(
        ctx: RunContext, trusted: ReviewerRewriteBatchTrustedInput,
) -> ReviewerRewriteBatchRequest:
    requests = tuple(_current_request_from_input(
        ctx, trusted.for_target(target.target_ordinal)) for target in trusted.targets)
    raw = {'version': 2, 'kind': 'stage5-reviewer-rewrite-batch-v2',
           'requests': [row.model_dump(mode='json') for row in requests]}
    raw['request_sha256'] = reviewer_rewrite_request_sha256(raw)
    return ReviewerRewriteBatchRequest.model_validate(raw)


def _record_trusted_request(
        ctx: RunContext, trusted: ReviewerRewriteBatchTrustedInput,
) -> ReviewerRewriteBatchRequest:
    if ctx.paid:
        raise ProvenanceRefused('formal reviewer rewrite request 必须由 free node 物化')
    from novel.orchestrator import reviewer_action_evidence
    reviewer_action_evidence.require_reviewer_rewrite_authority(
        Path(ctx.novel_dir).parent, ctx.sid, trusted,
        current_parent_run_id=ctx.parent_run_id)
    store = ArtifactStore(ctx)
    rel = STAGE5_REVIEWER_REWRITE_REQUEST
    existing = load_reviewer_rewrite_batch(ctx) if store.exists(rel) else None
    lease = (nullcontext() if existing is None
             else stage5_runs.reviewer_rewrite_execution_ownership(
                 ctx.novel_dir, existing[0].request_sha256))
    with lease, stage5_runs.book_commit_ownership(ctx.novel_dir):
        proposed = _current_batch_from_input(ctx, trusted)
        if store.exists(rel):
            current = load_reviewer_rewrite_batch(ctx)
            if existing != current:
                raise ProvenanceRefused('旧 reviewer rewrite 批次在取锁期间漂移')
            if current[0] == proposed:
                return proposed
            probes = reviewer_rewrite_batch_probes(ctx)
            if any(row.execution.state not in ('landed_formal', 'consumed_resolved')
                   for row in probes):
                raise ProvenanceRefused('旧 reviewer rewrite 批次仍有未解决单元；拒绝覆盖')
        store.write_round(rel, proposed)
        landed, _sha = load_reviewer_rewrite_batch(ctx)
        if landed != proposed or _current_batch_from_input(ctx, trusted) != proposed:
            raise ProvenanceRefused('reviewer rewrite 批次写后 typed/current readback 漂移')
        return landed


def record_reviewer_rewrite_request(
        ctx: RunContext, input_path: Path,
) -> ReviewerRewriteBatchRequest:
    return _record_trusted_request(ctx, _load_trusted_input(Path(input_path)))


def request_reviewer_rewrite(
        ctx: RunContext, value: Stage5ReviewerRewriteRequest, *,
        reviewer_action_record_sha256: str, business_sha256: str,
        reviewer_contract_sha256: str, reviewer_session_run_id: str,
        reviewer_seat_turn_id: str, provider_response_sha256: str,
) -> ReviewerRewriteBatchRequest:
    typed = Stage5ReviewerRewriteRequest.model_validate(value)
    trusted = ReviewerRewriteBatchTrustedInput(
        **typed.model_dump(mode='json'),
        reviewer_action_record_sha256=reviewer_action_record_sha256,
        business_sha256=business_sha256,
        reviewer_contract_sha256=reviewer_contract_sha256,
        reviewer_session_run_id=reviewer_session_run_id,
        reviewer_seat_turn_id=reviewer_seat_turn_id,
        provider_response_sha256=provider_response_sha256)
    return _record_trusted_request(ctx, trusted)


def retire_expired_reviewer_rewrite_request(
        ctx: RunContext, request_rel: str, request_sha256: str,
) -> ReviewerRewriteDisposition:
    """FREE/CAS：给已 stale 且全根零执行的 canonical request 落退休 receipt。"""
    if ctx.paid or ctx.node != 'stage5.write' or ctx.executor_profile is not None:
        raise ProvenanceRefused(
            'retire reviewer rewrite request 必须是无 executor 的 free stage5.write')
    request, initial_file_sha256 = load_reviewer_rewrite_request(ctx, request_rel, request_sha256=request_sha256)
    if request.request_sha256 != request_sha256:
        raise ProvenanceRefused('retire reviewer rewrite request SHA 与 canonical 漂移')
    batch, _digest = load_reviewer_rewrite_batch(ctx, request_rel)
    with (stage5_runs.reviewer_rewrite_execution_ownership(
            ctx.novel_dir, batch.request_sha256),
          stage5_runs.reviewer_rewrite_execution_ownership(
            ctx.novel_dir, request.request_sha256)):
        with stage5_runs.book_commit_ownership(ctx.novel_dir):
            current, request_file_sha256 = load_reviewer_rewrite_request(
                ctx, request_rel, request_sha256=request_sha256)
            if (current != request
                    or request_file_sha256 != initial_file_sha256):
                raise ProvenanceRefused(
                    'retire reviewer rewrite request 取 execution/book 锁后漂移')
            state = reviewer_rewrite_execution_state(ctx, request_rel, request_sha256=request_sha256)
            if state is None or state.state != 'expired_unstarted':
                actual = 'absent' if state is None else state.state
                raise ProvenanceRefused(
                    f'reviewer rewrite request 当前是 {actual}，不得退休成 unstarted')
            chains = stage5_runs.reviewer_rewrite_execution_chains(
                ctx, chapter=request.target_ordinal,
                request_sha256=request.request_sha256,
                request_file_sha256=request_file_sha256)
            recoveries = stage5_runs.reviewer_rewrite_recovery_journals(
                ctx, chapter=request.target_ordinal,
                request_sha256=request.request_sha256,
                request_file_sha256=request_file_sha256)
            carriers = _terminal_carriers(ArtifactStore(ctx), request)
            evidence = _unstarted_disposition_evidence(
                ctx, request, request_file_sha256,
                chains, recoveries, carriers)
            disposition = stage5_runs.write_reviewer_rewrite_disposition(
                ctx,
                request_rel=request_rel,
                request_file_sha256=request_file_sha256,
                request_sha256=request.request_sha256,
                reviewer_action_record_sha256=(
                    request.reviewer_action_record_sha256),
                target_ordinal=request.target_ordinal,
                unstarted_evidence=evidence)
            after = reviewer_rewrite_execution_state(ctx, request_rel, request_sha256=request_sha256)
            if after is None or after.state != 'consumed_resolved':
                raise ProvenanceRefused(
                    'reviewer rewrite disposition 写后未形成 consumed_resolved')
            return disposition


__all__ = [
    'load_reviewer_rewrite_batch',
    'reviewer_rewrite_batch_probes',
    'ready_reviewer_rewrite_targets',
    'current_reviewer_rewrite_request',
    'load_reviewer_rewrite_request',
    'matching_reviewer_rewrite_recovery_source',
    'record_reviewer_rewrite_request',
    'retire_expired_reviewer_rewrite_request',
    'request_reviewer_rewrite',
    'require_current_reviewer_rewrite_request',
    'reviewer_rewrite_execution_state',
]
