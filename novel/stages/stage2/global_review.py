#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from contracts.artifacts import (
    STAGE2_GLOBAL_GROUNDING, STAGE2_GLOBAL_REVIEW, STAGE2_GLOBAL_REVIEW_DECISIONS,
)
from contracts.schema.evaluation import GlobalReview, GlobalReviewDecisionSet
from contracts.schema.stage2 import GlobalGrounding

from novel import paths
from novel.errors import PathRefused, TerminalState, UpstreamMissing
from novel.orchestrator.jobs import service_dir
from novel.quality.global_review import apply_decision_set, require_current, review_status
from novel.run import RunContext
from novel.store import ArtifactStore


REL = STAGE2_GLOBAL_REVIEW
DEFAULT_DECISIONS = STAGE2_GLOBAL_REVIEW_DECISIONS


def apply_file(ctx: RunContext, decision_path: str = '') -> GlobalReview:
    """Apply audited human decisions with per-item optimistic subject hashes."""
    store = ArtifactStore(ctx)
    if not store.exists(REL):
        raise UpstreamMissing(f'{REL} 不在盘上；先生成/重建 stage2.global')
    supplied = Path(decision_path) if decision_path else None
    if supplied is not None and supplied.is_absolute():
        candidate = supplied.resolve()
        allowed = (service_dir(ctx.novel_dir.parent) / 'edits' / ctx.sid).resolve()
        book = Path(ctx.novel_dir).resolve()
        try:
            candidate.relative_to(allowed)
        except ValueError:
            # 与 `paths.edit_rel` 同一口径：绝对路径落在当前书目录内同样合法，
            # 报错文案承诺的正是「当前书或 <allowed>」两个根。
            try:
                candidate.relative_to(book)
            except ValueError as exc:
                raise PathRefused(
                    f'复核决策文件必须在当前书或 {allowed} 内：{candidate}') from exc
        if not candidate.is_file():
            raise TerminalState(f'{candidate} 不在盘上')
        try:
            decisions = GlobalReviewDecisionSet.model_validate_json(
                candidate.read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError, ValidationError) as exc:
            raise TerminalState(
                f'复核决策文件无法读取或不合 GlobalReviewDecisionSet：{candidate}：{exc}') from exc
    else:
        rel = paths.edit_rel(ctx.novel_dir, decision_path, DEFAULT_DECISIONS)
        if not store.exists(rel):
            raise TerminalState(
                f'{rel} 不在盘上；放入 GlobalReviewDecisionSet（actor + decisions）')
        decisions = store.read(GlobalReviewDecisionSet, rel)
    grounding = store.read_own(GlobalGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    grounding_payload = grounding.model_dump(by_alias=True)
    current = store.read_own(GlobalReview, REL)[0]
    require_current(grounding_payload, current)
    updated = apply_decision_set(current, decisions)
    require_current(grounding_payload, updated)
    store.write_round(REL, updated)
    return updated


def is_approved(ctx: RunContext, review: GlobalReview) -> bool:
    """Bind approval to the complete, current Stage2 subject."""
    grounding = ArtifactStore(ctx).read_own(
        GlobalGrounding, STAGE2_GLOBAL_GROUNDING)[0]
    return review_status(
        grounding.model_dump(by_alias=True), review)[2]
