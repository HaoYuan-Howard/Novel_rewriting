#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''stage0 非正文剔除与人工动作。零 LLM。

机器路径只做三件事：无损解码、BOM/换行归一、从第一个非正文标记起删除。其余字符
一个不动。人工编辑是另一条显式入口，会在链上落一条新 revision、把审批退回 pending；
机器重跑不会静默覆盖它。

所有写路径的形状都是
「算出一条新 `Stage0Revision` → `revisions.commit()`」，随后只把 `current.clean_text`
物化到 `clean_text.txt`。运行时报告不落盘。⇒ 归档、WAL、三方合并、崩溃重放状态机全部下线，
细节与理由见 `novel/stages/stage0/revisions.py` 与 `contracts/schema/stage0.py` 模块头。
'''
from __future__ import annotations

from contracts.artifacts import STAGE0_CLEAN_TEXT

import codecs
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from pydantic import ValidationError

from contracts.schema.report import CheckReport
from contracts.schema.stage0 import (
    ApprovedApproval,
    RemovedSegment,
    RequestedRemoval,
    Stage0Current,
    Stage0Revision,
    Stage0RevisionContent,
    Stage0Revisions,
    Stage0Snapshot,
    Stage0Source,
)
from novel import paths, prov, run_identity
from novel.core.ingest import (
    DecodeProblem,
    DecodedSource,
    ScanResult,
    decode_source_bytes,
    nonbody_cut,
    scan_text,
)
from novel.core.source import (
    describe_source, resolve_recorded_source, source_ref_for,
)
from novel.errors import (
    ArtifactUnreadable,
    ByteFormRefused,
    PathRefused,
    ProvenanceRefused,
    SourceConflict,
    SourceDecodeFailed,
    SourcePathRejected,
    TerminalState,
    UpstreamMissing,
)
from novel.llm.cache import CachePolicy
from novel.llm.ledger import CostLedger
from novel.run import RunContext
from novel.stages.stage0 import revisions as rev
from novel.store import ArtifactStore

CLEAN_REL = STAGE0_CLEAN_TEXT
REVISIONS_REL = rev.REVISIONS_REL


@dataclass(frozen=True)
class RemovalResult:
    """一次 stage0 动作之后**盘上当前那一版**长什么样（`--dry-run` 时是「将会是」）。"""

    clean_text: str
    report: Stage0Snapshot
    scan: ScanResult

    @property
    def body_ratio(self) -> float:
        return self.report.body_code_points / self.report.total_code_points


@dataclass(frozen=True)
class PreparedEditPlan:
    """同一份 edit-plan 在真正提交前可复算的精确落点。"""

    clean_text: str
    removed: tuple[RemovedSegment, ...]
    revision_id: str


def preview_text(value: str, limit: int) -> str:
    return re.sub(r'\s+', ' ', value).strip()[:limit]


def machine_content(
    decoded: DecodedSource, *, original_path: str,
) -> tuple[Stage0Source, Stage0RevisionContent, ScanResult]:
    '''对已经无损解码的文本执行唯一一次删除决策，产出一条 revision 的完整内容。'''
    text = decoded.text
    scan = scan_text(text)
    cut = nonbody_cut(text, scan)
    clean = text if cut is None else text[:cut]

    removed: list[RemovedSegment] = []
    if cut is not None:
        for index, marker in enumerate(scan.nonbody):
            start = cut if index == 0 else marker.line_start
            end = scan.nonbody[index + 1].line_start \
                if index + 1 < len(scan.nonbody) else len(text)
            segment_text = text[start:end]
            removed.append(RemovedSegment(
                kind=marker.kind,
                marker_line=marker.title,
                span=(start, end),
                origin='machine',
            ))

    # ⚠ 「这条路径是外部语料还是本服务的上传」只在**写产物这一刻**判一次
    #   （`novel/core/source.py::source_ref_for`，唯一落点）；之后它就是产物里
    #   写死的事实，读取端不必再从字符串里推断。
    source = Stage0Source(
        source_ref=source_ref_for(original_path),
        normalized_source_sha1=decoded.normalized_source_sha1,
        total_code_points=len(text),
    )
    content = Stage0RevisionContent(
        clean_text=clean,
        removed=removed,
    )
    return source, content, scan


def _provisional(source: Stage0Source, content: Stage0RevisionContent,
                 *, code: str) -> RemovalResult:
    """把一条还没落盘的内容投影成运行时快照（供 dry-run 与首跑预览共用）。"""
    revision = rev.make_revision(
        parent_revision_id=None, origin='machine', actor=None, reason=None,
        code=code, content=content)
    log = Stage0Revisions(
        source=source,
        revisions=[revision],
        current=Stage0Current(
            revision_id=revision.revision_id,
            approval=_auto_approval()),
        candidate_revision_id=None,
    )
    return RemovalResult(content.clean_text, rev.snapshot_of(log), scan_text(content.clean_text))


def remove_decoded(decoded: DecodedSource, *, original_path: str,
                   code: str) -> RemovalResult:
    source, content, scan = machine_content(decoded, original_path=original_path)
    result = _provisional(source, content, code=code)
    return RemovalResult(result.clean_text, result.report, scan)


def remove_bytes(raw: bytes, *, original_path: str, code: str) -> RemovalResult:
    '''合成样本与磁盘入口共用的字节级 API。'''
    try:
        decoded = decode_source_bytes(raw)
    except DecodeProblem as exc:
        raise SourceDecodeFailed(f'{original_path}: {exc}') from exc
    return remove_decoded(decoded, original_path=original_path, code=code)


def inspect_content(
    source_path: str | Path,
) -> tuple[Stage0Source, Stage0RevisionContent, ScanResult]:
    """读盘 + 无损解码 + 唯一一次删除决策。**一趟读完，不重复解码。**"""
    source = Path(source_path).resolve()
    if not source.is_file():
        raise UpstreamMissing(f'原始小说不在盘上：{source}')
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise ArtifactUnreadable(f'读不了原始小说 {source}：{exc!r}') from exc
    try:
        decoded = decode_source_bytes(raw)
    except DecodeProblem as exc:
        raise SourceDecodeFailed(f'{source}: {exc}') from exc
    return machine_content(decoded, original_path=str(source))


def inspect_source(source_path: str | Path, *, code: str) -> RemovalResult:
    source, content, scan = inspect_content(source_path)
    return RemovalResult(
        content.clean_text, _provisional(source, content, code=code).report, scan)


def _context(
    novel_dir: Path,
    node: str,
    *,
    run_id: str | None = None,
) -> RunContext:
    resolved_run_id = run_identity.resolve_run_id(run_id)
    return RunContext(
        novel_dir=novel_dir,
        node=node,
        paid=False,
        code=prov.code_version(),
        cache=CachePolicy.default(),
        ledger=CostLedger(path=None, run_id=resolved_run_id),
        run_id=resolved_run_id,
    )


def _require_stage0_fresh(store: ArtifactStore) -> None:
    """Refuse committing an approval against source/clean bytes that already moved.

    ⚠ 它是**前置条件**（2026-08-22 阶段 C）：链是权威、审批只改链上一格，
    先判后写就够了，不必再拿一个跨文件事务去回滚一次已经写下的批准。
    """
    freshness = store.freshness(CLEAN_REL)
    if freshness.state == 'fresh':
        return
    edges = ', '.join(
        f'{edge.key}:{edge.reason}' for edge in freshness.edges) or freshness.reason
    raise ProvenanceRefused(
        'Stage0 approval refused: current clean text is not current against '
        f'the source/revision decision surface: {freshness.state}/{freshness.reason} '
        f'({edges}). Nothing was written.')


def _result_of(log: Stage0Revisions) -> RemovalResult:
    content = rev.current_revision(log).content
    return RemovalResult(content.clean_text, rev.snapshot_of(log), scan_text(content.clean_text))


def _auto_approval() -> ApprovedApproval:
    """机械校验通过后的自动接受记录（2026-09-15 全自动裁决）。

    `by` 与人工 actor 一样只是自报身份；它记录的是「这一版由 stage0.remove 的
    机械判据放行」，非正文剔除的语义正确性（番外盲区）随交付披露（Q-13）。
    """
    return ApprovedApproval(
        status='approved', at=rev.now_iso(), by='system:stage0-auto',
        reason='机械校验通过后自动接受；剔除语义正确性未审，随交付披露')


def _checked(ctx: RunContext, log: Stage0Revisions) -> tuple[RemovalResult, CheckReport]:
    from novel.stages.stage0.checks import run_check_only
    return _result_of(log), run_check_only(ctx)


def run_source(source_path: str | Path, base: str | Path, *, force: bool = False,
               dry_run: bool = False, expected_sid: str | None = None,
               run_id: str | None = None) \
        -> tuple[Path, RemovalResult, CheckReport | None]:
    '''真实摄入路径：源文件推导目录，保护已有人工稿，在链上落一条机器 revision。'''
    decoded_source, decoded_content, scan = inspect_content(source_path)
    machine = RemovalResult(
        decoded_content.clean_text,
        _provisional(decoded_source, decoded_content, code=prov.code_version()).report, scan)
    sid, title, _platform = paths.derive_source_meta(str(source_path))
    try:
        paths.check_sid(sid)
    except PathRefused as exc:
        raise SourcePathRejected(f'原始文件名推导出的书 id 不合法：{exc}') from exc
    if not title.strip():
        raise SourcePathRejected(
            f'原始文件名 {Path(source_path).name!r} 推导不出非空书名；'
            '拒绝把产物写进 Output 根或无标题目录')
    if expected_sid is not None:
        paths.check_sid(expected_sid)
        if sid != expected_sid:
            raise SourceConflict(
                f'命令指定 sid={expected_sid!r}，但源文件名推导出 sid={sid!r}；'
                '不能把一份书静默写进另一份书的目录')
    errors = paths.dirname_roundtrip_errors(title, sid)
    if errors:
        raise SourcePathRejected('；'.join(errors))
    base_dir = Path(base).resolve()
    candidate_dir = base_dir / paths.novel_dirname(title, sid)
    try:
        existing_dir = paths.find_novel_dir(base_dir, sid)
    except PathRefused as exc:
        raise SourceConflict(
            f'无法为源文件 id={sid!r} 唯一定位已有书目录：{exc}') from exc
    if existing_dir is not None and existing_dir.resolve() != candidate_dir:
        raise SourceConflict(
            f'id={sid!r} 已属于目录 {existing_dir.name!r}，但本次源文件名会建立 '
            f'{candidate_dir.name!r}；不能让同一本书裂成两个目录')
    novel_dir = existing_dir.resolve() if existing_dir is not None else candidate_dir
    ctx = _context(novel_dir, 'stage0.remove', run_id=run_id)
    store = ArtifactStore(ctx)
    log = rev.read(store) if dry_run else rev.ensure_materialized(store)

    # 首跑即机械接受（2026-09-15 全自动裁决）：stage0.approve 人工闸退役，
    # 机械校验（s0_* HARD，含正文占比闸）通过即写 auto 批准；「这一段是不是
    # 番外」的语义盲区没有确定性防线，随交付披露（Q-13），不再挡链。
    if log is None:
        if dry_run:
            return novel_dir, machine, None
        root = rev.make_revision(
            parent_revision_id=None, origin='machine', actor=None, reason=None,
            code=ctx.code, content=decoded_content)
        fresh = Stage0Revisions(
            source=decoded_source,
            revisions=[root],
            current=Stage0Current(
                revision_id=root.revision_id,
                approval=_auto_approval()),
            candidate_revision_id=None,
        )
        rev.commit(store, fresh)
        result, report = _checked(ctx, fresh)
        return novel_dir, result, report

    current = rev.current_revision(log)
    if decoded_source.normalized_source_sha1 != log.source.normalized_source_sha1:
        # 原文变了 = 另一份原文：正式稿的 span 坐标系全部失效。正式稿一个字不动、
        # 仍对旧原文成立；换原文按 R-0.14 是新书的摄入。
        raise SourceConflict(
            'stage0_source_changed：本次摄入的原文与产生当前正式稿的原文不是'
            f'同一份（新 {decoded_source.normalized_source_sha1[:12]}… / 旧 '
            f'{log.source.normalized_source_sha1[:12]}…）。正式稿未动、'
            '仍对旧原文成立。要换原文等于摄入另一本书：另起书目录，'
            '或显式废弃当前书目录后重跑（R-0.14）')

    # 机器结果与当前稿逐字相同 ⇒ 无事可做。⚠ 比的是**整团内容**，不是两个哈希：
    # revision 内容是 `{正文, 剔除账本}`，只比正文会漏掉账本的差别；原文身份已在上面单独核对。
    if decoded_content == current.content:
        result, report = _checked(ctx, log)
        return novel_dir, result, report

    proposal = rev.make_revision(
        parent_revision_id=current.revision_id, origin='machine',
        actor=None, reason=None, code=ctx.code, content=decoded_content)

    # 人否过的候选不复活。`revision_id` 的前像不含时间与 code ⇒ 同一份内容挂在
    # 同一个 parent 上恒是同一个 id，这条判定才成立（见 `revision_digest`）。
    if proposal.revision_id in {item.revision_id for item in log.dismissed}:
        result, report = _checked(ctx, log)
        return novel_dir, result, report

    if dry_run:
        return novel_dir, _result_of(log), None

    if current.origin != 'machine':
        # 存量书含人工剔除内容（旧 stage0.edit 产物）。人工编辑入口已退役，
        # 机器重算会覆盖人工内容 ⇒ 关闭式拒绝，指引重跑，不静默覆盖。
        raise TerminalState(
            '当前 stage0 正式稿含人工剔除内容（origin=human），人工编辑入口已随'
            '全自动裁决退役；请显式废弃该书目录后重跑 stage0.remove（R-0.14）')

    # 当前稿是机器稿（旧 pending 或已批准）⇒ 换成新结果不丢人工内容：
    # 剔除是确定性算法的输出，新结果即当前算法的当前结论；旧人工批准语义
    # 已随人工闸退役（存量书按裁决重跑）。
    published = rev.published(
        log, proposal, candidate_revision_id=None, approval=_auto_approval())
    rev.commit(store, published)
    result, report = _checked(ctx, published)
    return novel_dir, result, report


def verified_removals(
    requested: Sequence[RequestedRemoval],
    source_text: str,
    prior: Sequence[RemovedSegment],
) -> list[RemovedSegment]:
    """把**意图**（span + kind）落定成**已验证事实**。唯一落点。

    计划里只信 span/kind；origin 与标记行由这条唯一入口落定。两条规则：
      ① span 与当前版本某条**机器**剔除完全一致 ⇒ 原样保留那条（人只是确认了
         机器的圈选，账目仍归机器）；
      ② 其余（人工新圈/改边）⇒ 按 `s0_removal_ledger` 的同一口径复算：
         标记行取 span 内首个非空行、origin='human'。
    """
    machine_by_span = {
        segment.span: segment for segment in prior if segment.origin == 'machine'}
    verified: list[RemovedSegment] = []
    for segment in requested:
        lo, hi = segment.span
        earlier = machine_by_span.get(segment.span)
        if earlier is not None:
            verified.append(earlier)
            continue
        piece = source_text[lo:hi]
        first_line = next(
            (line.strip() for line in piece.splitlines() if line.strip()), '')
        marker_line = first_line or '人工剔除'
        verified.append(RemovedSegment(
            kind=segment.kind,
            marker_line=marker_line,
            span=segment.span,
            origin='human',
        ))
    return verified


