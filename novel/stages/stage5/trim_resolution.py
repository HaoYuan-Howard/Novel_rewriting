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
    ApprovalRequired, ArtifactUnreadable, ProvenanceRefused, TerminalState,
    UpstreamMissing, UpstreamStale,
)
from novel.llm import stage5_runs
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
    return matching_current_resolution(store, audit, audit_sha256) is not None


def matching_current_resolution(
    store: ArtifactStore,
    audit: TrimAudit,
    audit_sha256: str,
) -> tuple[TrimResolution, str] | None:
    """返回绑定该 current audit/正文/root 的 resolution 及完整文件 SHA。

    ``None`` 只表示文件物理不存在，或一份**可读**回执绑定的是旧 audit/正文。
    现存但不可读的回执不能洗成“尚待人工决定”，否则 closure 会在已知坏盘上
    打开新的付费 Reviewer 席位；这类证据损坏必须原样向上关闭式传播。
    """
    rel = resolution_rel(audit.output_ordinal)
    if not store.exists(rel):
        return None
    resolution, resolution_sha256 = store.read_own_with_sha256(
        TrimResolution, rel)
    expected_chapter_rel = artifact_rel(
        'stage5.chapter', nn=audit.output_ordinal)
    identity_errors: list[str] = []
    if resolution.output_ordinal != audit.output_ordinal:
        identity_errors.append(
            f'ordinal={resolution.output_ordinal}，期望 {audit.output_ordinal}')
    if resolution.chapter_rel != expected_chapter_rel:
        identity_errors.append(
            f'chapter_rel={resolution.chapter_rel!r}，期望 {expected_chapter_rel!r}')
    if resolution.before_sha256 != resolution.chapter_sha256:
        identity_errors.append('before_sha256 与 chapter_sha256 自相矛盾')
    if resolution.provenance.root_clean_sha1 != store.ctx.root_clean_sha1:
        identity_errors.append('root_clean_sha1 不属于当前书根')
    if identity_errors:
        raise ProvenanceRefused(
            f'{rel} 的 live trim resolution 身份损坏：'
            + '；'.join(identity_errors)
            + '；不得把坏回执洗成 unresolved 后启动 Reviewer')

    # 基本自洽、同根但绑定旧 audit 的回执是合法历史：当前 Commander 可以
    # 针对新 audit 形成下一 round。只有它自称绑定**当前 exact audit** 时，
    # before 仍对不上才是不可解释的矛盾，而不是可覆盖的旧轮。
    if resolution.audit_sha256 != audit_sha256:
        return None
    if resolution.before_sha256 != audit.before_sha256:
        raise ProvenanceRefused(
            f'{rel} 声称绑定当前 audit SHA，但 before/chapter SHA 与 audit 不一致；'
            '不得把自相矛盾的 live 回执洗成 unresolved')
    chapter = _current_pretrim(store, audit)
    if chapter is None:
        return None
    chapter_rel, chapter_sha256 = chapter
    if not (
        resolution.audit_sha256 == audit_sha256
        and resolution.before_sha256 == audit.before_sha256
        and resolution.chapter_rel == chapter_rel == expected_chapter_rel
        and resolution.chapter_sha256 == chapter_sha256 == audit.before_sha256
    ):
        return None
    return resolution, resolution_sha256


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
    *,
    strict_inventory: bool = False,
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
        except ArtifactUnreadable as exc:
            if strict_inventory:
                raise TerminalState(
                    f'Stage5 trim audit inventory 含不可读现役文件：{rel}') from exc
            continue
        if rel != artifact_rel('stage5.trim_audit', nn=audit.output_ordinal):
            if strict_inventory:
                raise TerminalState(
                    'Stage5 trim audit inventory 的路径与声明章号不一致：'
                    f'{rel} / output_ordinal={audit.output_ordinal}')
            continue
        if ((chapter := _current_pretrim(store, audit)) is not None
                and chapter[1] == audit.before_sha256):
            out[audit.output_ordinal] = (audit, audit_sha256)
    return out


def current_unresolved_audits(
    store: ArtifactStore,
    *,
    strict_inventory: bool = False,
) -> dict[int, tuple[TrimAudit, str]]:
    """Current trim facts that still need a hash-bound human decision."""
    return {
        ordinal: row
        for ordinal, row in current_trim_audits(
            store, strict_inventory=strict_inventory).items()
        if needs_human_resolution(store, row[0], row[1])
    }


def same_retain_pretrim_decision(
    resolution: TrimResolution,
    *,
    output_ordinal: int,
    actor: str,
    reason: str,
    audit_sha256: str,
    chapter_rel: str,
    chapter_sha256: str,
    root_clean_sha1: str,
) -> bool:
    """比较 batch 重放的业务决定，而不是新旧执行 epoch 的来源账字节。

    同一 manifest 的前缀可能由上一进程写完；新进程的 code/inputs/usage 来源账
    天然可能不同。audit/chapter 双 SHA、actor/reason 与根正文都相同时，人类决定
    已经精确落地，重写一轮既不幂等也会制造伪造的新产地。
    """
    return (
        resolution.output_ordinal == output_ordinal
        and resolution.action == 'retain_pretrim'
        and resolution.actor == actor
        and resolution.reason == reason
        and resolution.audit_sha256 == audit_sha256
        and resolution.before_sha256 == chapter_sha256
        and resolution.chapter_rel == chapter_rel
        and resolution.chapter_sha256 == chapter_sha256
        and resolution.provenance.root_clean_sha1 == root_clean_sha1
    )


def _validate_batch_frontier(
    store: ArtifactStore,
    request: 'BatchTrimResolutionRequest',
    facts: list[tuple[int, TrimAudit, str, str, str]],
    *,
    minimum_resolved_prefix: int = 0,
    require_fully_resolved: bool = False,
) -> None:
    """证明 manifest 是 exact 已落前缀加 exact current unresolved 后缀。

    ``minimum_resolved_prefix`` 还把验证绑定到调用循环已经越过的 index：
    一个此前已写完的 prefix 即使被盘外删除/换旧，也不能退回成“仍合法的更短
    prefix”后让循环从后面的 item 继续。正常返回前再用
    ``require_fully_resolved`` 证明整批没有留下尾项或被末笔写后篡改。
    """
    if not 0 <= minimum_resolved_prefix <= len(request.items):
        raise ValueError('batch frontier 的 minimum_resolved_prefix 越界')
    current = current_trim_audits(store, strict_inventory=True)
    unresolved = {
        ordinal: row for ordinal, row in current.items()
        if needs_human_resolution(store, row[0], row[1])
    }
    current_suffix = [
        (ordinal, audit_sha256, audit.before_sha256)
        for ordinal, (audit, audit_sha256) in sorted(unresolved.items())
    ]
    states: list[str] = []
    for (
        (ordinal, audit, audit_sha256, chapter_rel, reason), item,
    ) in zip(facts, request.items, strict=True):
        current_audit = current.get(ordinal)
        if current_audit is None or (
            current_audit[0] != audit
            or current_audit[1] != audit_sha256
            or current_audit[0].before_sha256 != item.chapter_sha256
        ):
            raise TerminalState(
                f'批量销账 frontier 失败：第 {ordinal} 章原 manifest audit/正文'
                '已不再是 exact current 主体')
        rel = resolution_rel(ordinal)
        if store.exists(rel):
            resolution, _raw = store.read_own(TrimResolution, rel)
            if same_retain_pretrim_decision(
                resolution,
                output_ordinal=ordinal,
                actor=request.actor,
                reason=reason,
                audit_sha256=audit_sha256,
                chapter_rel=chapter_rel,
                chapter_sha256=audit.before_sha256,
                root_clean_sha1=store.ctx.root_clean_sha1,
            ):
                if ordinal in unresolved:
                    raise TerminalState(
                        f'批量销账 frontier 自相矛盾：第 {ordinal} 章同时为已落与 unresolved')
                states.append('resolved')
                continue
        unresolved_row = unresolved.get(ordinal)
        if unresolved_row is None or (
            unresolved_row[1] != item.audit_sha256
            or unresolved_row[0].before_sha256 != item.chapter_sha256
        ):
            raise TerminalState(
                f'批量销账 frontier 失败：第 {ordinal} 章既非 exact 已落决定，'
                '也非 exact current unresolved')
        states.append('unresolved')

    first_unresolved = next(
        (index for index, state in enumerate(states) if state == 'unresolved'),
        len(states),
    )
    if any(state != 'resolved' for state in states[:first_unresolved]) or any(
            state != 'unresolved' for state in states[first_unresolved:]):
        raise TerminalState(
            '批量销账 frontier 失败：manifest 不是连续的 exact 已落前缀 + '
            'current unresolved 后缀')
    if first_unresolved < minimum_resolved_prefix:
        raise TerminalState(
            '批量销账 frontier 失败：已遍历的 exact resolution 前缀发生回退'
            f'（至少应落 {minimum_resolved_prefix} 项，当前仅 {first_unresolved} 项）；'
            '不得从后续章继续写入')
    if require_fully_resolved and first_unresolved != len(request.items):
        raise TerminalState(
            '批量销账 frontier 失败：写循环结束后 manifest 未全部 exact 落地；'
            f'已落前缀={first_unresolved}/{len(request.items)}')
    manifest_suffix = [
        (item.output_ordinal, item.audit_sha256, item.chapter_sha256)
        for item in request.items[first_unresolved:]
    ]
    if manifest_suffix != current_suffix:
        raise TerminalState(
            '批量销账 frontier 失败：manifest unresolved 后缀不等于当前待销账全集；'
            '不得遗漏、夹入或重排章节')


def retain_pretrim_batch(
    ctx: RunContext,
    request: 'BatchTrimResolutionRequest',
) -> list[TrimResolution]:
    """全书写者锁内完成整批预检与全部 resolution 提交。"""
    from contracts.schema.api import BatchTrimResolutionRequest
    if not isinstance(request, BatchTrimResolutionRequest):
        raise ValueError('批量销账必须交 typed BatchTrimResolutionRequest')
    with stage5_runs.book_commit_ownership(ctx.novel_dir):
        return _retain_pretrim_batch_locked(ctx, request)


def _retain_pretrim_batch_locked(
    ctx: RunContext,
    request: 'BatchTrimResolutionRequest',
) -> list[TrimResolution]:
    """按显式 manifest 一次销账多章（P1-2）。

    三条契约：
      - **整批预检**：任一项的 audit/chapter SHA 与当前盘面漂移即整批不开始
        （TerminalState），绝不出现「写了一半才发现漂移」；
      - **按章序幂等写入**：已存在且逐字相等的 resolution 跳过；重交同一
        manifest 只会继续剩余项；
      - **actor 共享、reason 共享 + 单项覆盖**：与单章口同一把 hash-bound 尺子。
    """
    store = ArtifactStore(ctx)

    # ── 整批预检：一次把全部章的事实读齐并核对 SHA，任何漂移即显式终态。 ──
    facts: list[tuple[int, TrimAudit, str, str, str]] = []
    for item in request.items:
        audit_rel = artifact_rel('stage5.trim_audit', nn=item.output_ordinal)
        audit, audit_sha256 = store.read_with_sha256(TrimAudit, audit_rel)
        if audit.output_ordinal != item.output_ordinal:
            raise TerminalState(
                f'批量销账预检失败：{audit_rel} 声明 output_ordinal='
                f'{audit.output_ordinal}，与 manifest 第 {item.output_ordinal} 章不符')
        if audit_sha256 != item.audit_sha256:
            raise TerminalState(
                f'批量销账预检失败：第 {item.output_ordinal} 章 audit SHA 漂移'
                f'（manifest {item.audit_sha256[:8]}… / 盘上 {audit_sha256[:8]}…）；'
                '整批不开始，请重新抓取当前 trim 状态')
        chapter = _current_pretrim(store, audit)
        if chapter is None:
            raise TerminalState(
                f'批量销账预检失败：第 {item.output_ordinal} 章正式稿不存在或已过期；'
                '整批不开始')
        chapter_rel, chapter_sha256 = chapter
        if (chapter_sha256 != audit.before_sha256
                or chapter_sha256 != item.chapter_sha256):
            raise TerminalState(
                f'批量销账预检失败：第 {item.output_ordinal} 章正文 SHA 与 '
                f'audit/manifest 不一致（正文 {chapter_sha256[:8]}…）；整批不开始')
        facts.append((item.output_ordinal, audit, audit_sha256, chapter_rel,
                      item.reason.strip() or request.reason))

    _validate_batch_frontier(store, request, facts)

    # 第二次完整采样落在第一笔 resolution 之前。共享书锁会拦住所有现役
    # Stage5 writer；这次复核再把误接线/盘外改字节造成的混合预检关闭式挡住。
    for ordinal, expected_audit, expected_audit_sha, chapter_rel, _reason in facts:
        audit_rel = artifact_rel('stage5.trim_audit', nn=ordinal)
        current_audit, current_audit_sha = store.read_with_sha256(
            TrimAudit, audit_rel)
        chapter = _current_pretrim(store, current_audit)
        if (current_audit != expected_audit
                or current_audit_sha != expected_audit_sha
                or chapter is None
                or chapter[0] != chapter_rel
                or chapter[1] != expected_audit.before_sha256):
            raise TerminalState(
                f'批量销账首写前复核失败：第 {ordinal} 章 audit/正文在预检后漂移；'
                '整批不开始，请重新抓取当前 trim 状态')
    _validate_batch_frontier(store, request, facts)

    # ── 按章序幂等写入。每一笔前都重新证明完整 frontier。 ──
    written: list[TrimResolution] = []
    for index, (ordinal, audit, audit_sha256, chapter_rel, reason) in enumerate(
            facts):
        # 书锁覆盖现役 writer，但盘外写入/漏接线不能被当作不可能。首章落盘后，
        # 下一章写前必须重新证明「exact 已落前缀 + current unresolved 后缀」；
        # 否则旧 facts 会覆盖一份 foreign 人审或把 audit/正文漂移洗白。
        _validate_batch_frontier(
            store, request, facts, minimum_resolved_prefix=index)
        resolution = TrimResolution(
            output_ordinal=ordinal,
            action='retain_pretrim',
            actor=request.actor,
            reason=reason,
            audit_sha256=audit_sha256,
            before_sha256=audit.before_sha256,
            chapter_rel=chapter_rel,
            chapter_sha256=audit.before_sha256,
            _provenance=ctx.free_provenance(),
        )
        rel = resolution_rel(ordinal)
        if store.exists(rel):
            current, _raw = store.read_own(TrimResolution, rel)
            if same_retain_pretrim_decision(
                current,
                output_ordinal=ordinal,
                actor=request.actor,
                reason=reason,
                audit_sha256=audit_sha256,
                chapter_rel=chapter_rel,
                chapter_sha256=audit.before_sha256,
                root_clean_sha1=ctx.root_clean_sha1,
            ):
                written.append(current)
                continue
        store.write_round(rel, resolution)
        written.append(resolution)
    _validate_batch_frontier(
        store, request, facts,
        minimum_resolved_prefix=len(facts), require_fully_resolved=True)
    return written


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
    """全书写者锁内复核当前 audit/正文并提交单章 resolution。"""
    with stage5_runs.book_commit_ownership(ctx.novel_dir):
        return _retain_pretrim_locked(
            ctx, ordinal, actor=actor, reason=reason)


def _retain_pretrim_locked(
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
