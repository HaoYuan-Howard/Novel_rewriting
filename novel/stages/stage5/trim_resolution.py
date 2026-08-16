#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hash-bound human closure for an unadopted Stage5 trim audit.

This module never changes chapter text and has no candidate-adoption path.
Humans may only record that the already-retained pretrim text is intentional;
candidate adoption remains exclusively owned by the calibrated automated gate.
"""
from __future__ import annotations

import hashlib

from contracts.artifacts import artifact_rel
from contracts.schema.evaluation import TrimAudit, TrimResolution
from contracts.schema.stage5 import Chapter
from novel.errors import ApprovalRequired, ArtifactUnreadable, ProvenanceRefused
from novel.run import RunContext
from novel.store import ArtifactStore


def resolution_rel(ordinal: int) -> str:
    return artifact_rel('stage5.trim_resolution', nn=ordinal)


def _current_pretrim(
    store: ArtifactStore,
    audit: TrimAudit,
) -> tuple[str, str] | None:
    """Return ``(formal rel, text sha256)`` only for a readable current chapter."""
    rel = artifact_rel(
        'stage5.chapter', mode=audit.mode, nn=audit.output_ordinal)
    if not store.exists(rel):
        return None
    try:
        chapter, _raw = store.read_own(Chapter, rel, require_current=True)
    except (ArtifactUnreadable, ProvenanceRefused):
        return None
    if chapter.output_ordinal != audit.output_ordinal or chapter.mode != audit.mode:
        return None
    digest = hashlib.sha256(chapter.text.encode('utf-8')).hexdigest()
    return rel, digest


def matches_current_audit(
    store: ArtifactStore,
    audit: TrimAudit,
    audit_sha256: str,
) -> bool:
    """True only for a readable resolution bound to every current identity."""
    rel = resolution_rel(audit.output_ordinal)
    if not store.exists(rel):
        return False
    try:
        resolution, _raw = store.read_own(TrimResolution, rel)
    except ArtifactUnreadable:
        return False
    chapter = _current_pretrim(store, audit)
    if chapter is None:
        return False
    chapter_rel, chapter_sha256 = chapter
    return (
        resolution.output_ordinal == audit.output_ordinal
        and resolution.mode == audit.mode
        and resolution.action == 'retain_pretrim'
        and resolution.audit_sha256 == audit_sha256
        and resolution.before_sha256 == audit.before_sha256
        and resolution.after_sha256 == audit.after_sha256
        and resolution.chapter_rel == chapter_rel
        and resolution.chapter_sha256 == chapter_sha256 == audit.before_sha256
        and resolution.provenance.root_clean_sha1 == store.ctx.root_clean_sha1
    )


def retain_pretrim(
    ctx: RunContext,
    ordinal: int,
    *,
    actor: str,
    reason: str,
) -> TrimResolution:
    """Record a self-reported, zero-cost decision for the current trim audit."""
    store = ArtifactStore(ctx)
    audit_rel = artifact_rel('stage5.trim_audit', nn=ordinal)
    audit, audit_sha256 = store.read_with_sha256(TrimAudit, audit_rel)
    if audit.output_ordinal != ordinal:
        raise ArtifactUnreadable(
            f'{audit_rel} 声明 output_ordinal={audit.output_ordinal}，与文件名 ch{ordinal:02d} 不符')
    if audit.adopted:
        raise ApprovalRequired(
            f'{audit_rel} 的收缩候选已经由自动校准闸门采用；人工路径不能改写采用决定')
    if not audit.deterministic_passed:
        raise ApprovalRequired(
            f'{audit_rel} 的机械回归未通过，不属于待人工销账的 verification')
    chapter = _current_pretrim(store, audit)
    if chapter is None:
        raise ApprovalRequired(
            f'{audit.mode} 臂第 {ordinal} 章正式稿不存在、不可读或已过期；'
            '不能用旧 trim audit 销账')
    chapter_rel, chapter_sha256 = chapter
    if chapter_sha256 != audit.before_sha256:
        raise ApprovalRequired(
            f'{chapter_rel} 已在 trim audit 之后变化：当前 {chapter_sha256}，'
            f'audit.before {audit.before_sha256}；旧 resolution 必须失效')

    resolution = TrimResolution(
        output_ordinal=ordinal,
        mode=audit.mode,
        action='retain_pretrim',
        actor=actor,
        reason=reason,
        audit_sha256=audit_sha256,
        before_sha256=audit.before_sha256,
        after_sha256=audit.after_sha256,
        chapter_rel=chapter_rel,
        chapter_sha256=chapter_sha256,
        _provenance=ctx.free_provenance(),
    )
    rel = resolution_rel(ordinal)
    if store.exists(rel):
        current, _raw = store.read_own(TrimResolution, rel)
        if current == resolution:
            return current
        # Copy the old current artifact into immutable history before the
        # atomic replacement.  It remains current if validation/write fails.
        store.archive_round(rel, keep=True)
    store.write(rel, resolution)
    return resolution
