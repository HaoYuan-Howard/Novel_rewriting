#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hash-bound human closure for an over-length Stage5 chapter.

This module never changes chapter text and has no candidate-adoption path.
Humans may only record that the already-retained pretrim text is intentional.

⚠ 这句话曾经带一个「候选采用仍由校准自动闸门独占」的后半句。收缩臂整臂下线后
**没有任何路径能采用一个收缩候选**——自动的那条也没有了。因此本模块从「两条出路里
人的那一条」变成了**唯一的销账口**（另一条是 `stage5.write --apply-edit` 改写整章，
那会产生新的一章、也就是新的 audit，不走这里）。
"""
from __future__ import annotations

import hashlib

from contracts.artifacts import STAGE4_WRITER_CONTEXT, STAGE5_TRIM_AUDIT, artifact_rel
from contracts.schema.evaluation import TrimAudit, TrimResolution
from contracts.schema.stage4 import WriterContext
from contracts.schema.stage5 import Chapter
from novel.errors import (
    ApprovalRequired, ArtifactUnreadable, ProvenanceRefused, UpstreamMissing,
    UpstreamStale,
)
from novel.run import RunContext
from novel.stages.stage5 import measure as M
from novel.store import ArtifactStore


def resolution_rel(ordinal: int) -> str:
    return artifact_rel('stage5.trim_resolution', nn=ordinal)


def _current_pretrim(
    store: ArtifactStore,
    audit: TrimAudit,
) -> tuple[str, str] | None:
    """Return ``(formal rel, text sha256)`` only for a readable current chapter."""
    rel = artifact_rel('stage5.chapter', nn=audit.output_ordinal)
    if not store.exists(rel):
        return None
    try:
        chapter, _raw = store.read_own(Chapter, rel, require_current=True)
    # ⚠ `UpstreamStale` 是 `require_current=True` 时真正会抛的那一条（2026-08-17 从
    #   `ProvenanceRefused` 拆出来，见该类 docstring）；`ProvenanceRefused` 留在这里
    #   只覆盖来源账自相矛盾的旧行为面，不是这条读法的正常出路。
    except (ArtifactUnreadable, ProvenanceRefused, UpstreamMissing, UpstreamStale):
        return None
    if chapter.output_ordinal != audit.output_ordinal:
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
        and resolution.action == 'retain_pretrim'
        and resolution.audit_sha256 == audit_sha256
        and resolution.before_sha256 == audit.before_sha256
        and resolution.chapter_rel == chapter_rel
        and resolution.chapter_sha256 == chapter_sha256 == audit.before_sha256
        and resolution.provenance.root_clean_sha1 == store.ctx.root_clean_sha1
    )


def needs_human_resolution(
    store: ArtifactStore,
    audit: TrimAudit,
    audit_sha256: str,
) -> bool:
    """Whether this exact audit still represents the retained current draft.

    A merely present audit is not enough: the formal chapter may have changed,
    or a hash-bound retain decision may already close it.  Both Review
    de-duplication and inbox verification use this predicate so they cannot
    disagree about which audit is current.
    """
    chapter = _current_pretrim(store, audit)
    if chapter is None or chapter[1] != audit.before_sha256:
        return False
    return not matches_current_audit(store, audit, audit_sha256)


def current_trim_audits(
    store: ArtifactStore,
) -> dict[int, tuple[TrimAudit, str]]:
    """Return exact-current trim facts, even after human resolution.

    Review uses this wider set to fold ``length_over`` into its durable root
    cause.  A valid retain resolution closes the verification task, but it does
    not erase the fact that this exact over-length pretrim was deliberately
    retained; emitting ``length_over`` again on the next Review would reopen a
    second copy of the decision that was just closed.
    """
    template = STAGE5_TRIM_AUDIT
    directory, filename = template.rsplit('/', 1)
    pattern = filename.replace('{nn}', '*')
    root = store.path(directory)
    if not root.is_dir():
        return {}
    out: dict[int, tuple[TrimAudit, str]] = {}
    for path in sorted(root.glob(pattern)):
        rel = f'{directory}/{path.name}'
        try:
            audit, audit_sha256 = store.read_with_sha256(TrimAudit, rel)
        except ArtifactUnreadable:
            continue
        if rel != artifact_rel('stage5.trim_audit', nn=audit.output_ordinal):
            continue
        if ((chapter := _current_pretrim(store, audit)) is not None
                and chapter[1] == audit.before_sha256):
            out[audit.output_ordinal] = (audit, audit_sha256)
    return out


def current_unresolved_audits(
    store: ArtifactStore,
) -> dict[int, tuple[TrimAudit, str]]:
    """Current trim facts that still need a hash-bound human decision."""
    return {
        ordinal: row
        for ordinal, row in current_trim_audits(store).items()
        if needs_human_resolution(store, row[0], row[1])
    }


def verification_context(store: ArtifactStore, audit: TrimAudit) -> str:
    """Render the root-cause facts folded into one trim verification task."""
    identity = f'audit before={audit.before_sha256}'
    rel = artifact_rel('stage5.chapter', nn=audit.output_ordinal)
    try:
        chapter, _raw = store.read_own(Chapter, rel, require_current=True)
    except (ArtifactUnreadable, ProvenanceRefused, UpstreamMissing, UpstreamStale):
        return f'{identity}；当前正式稿字数/目标区间不可读'

    chars = M.chars_of(chapter.text)
    try:
        writer_context = store.read(WriterContext, STAGE4_WRITER_CONTEXT)
    except (ArtifactUnreadable, ProvenanceRefused, UpstreamMissing, UpstreamStale):
        return f'当前 pretrim {chars} 字（目标区间不可读）；{identity}'
    entry = next(
        (row for row in writer_context.chapters
         if row.output_ordinal == audit.output_ordinal),
        None,
    )
    if entry is None:
        return f'当前 pretrim {chars} 字（写作上下文无该章目标）；{identity}'
    lo, hi = M.target_range(entry.model_dump(by_alias=True))
    relation = (f'超上限 {chars - hi} 字' if chars > hi
                else f'未超上限（距上限 {hi - chars} 字）')
    return (
        f'当前 pretrim {chars} 字，目标 {lo}~{hi} 字，{relation}；{identity}')


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
    chapter = _current_pretrim(store, audit)
    if chapter is None:
        raise ApprovalRequired(
            f'第 {ordinal} 章正式稿不存在、不可读或已过期；'
            '不能用旧 trim audit 销账')
    chapter_rel, chapter_sha256 = chapter
    if chapter_sha256 != audit.before_sha256:
        raise ApprovalRequired(
            f'{chapter_rel} 已在 trim audit 之后变化：当前 {chapter_sha256}，'
            f'audit.before {audit.before_sha256}；旧 resolution 必须失效')

    resolution = TrimResolution(
        output_ordinal=ordinal,
        action='retain_pretrim',
        actor=actor,
        reason=reason,
        audit_sha256=audit_sha256,
        before_sha256=audit.before_sha256,
        chapter_rel=chapter_rel,
        chapter_sha256=chapter_sha256,
        _provenance=ctx.free_provenance(),
    )
    rel = resolution_rel(ordinal)
    if store.exists(rel):
        current, _raw = store.read_own(TrimResolution, rel)
        if current == resolution:
            return current
    store.write_round(rel, resolution)
    return resolution
