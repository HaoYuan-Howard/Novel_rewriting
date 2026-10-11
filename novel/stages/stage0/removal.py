#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''stage0 非正文剔除。零 LLM。

机器路径只做三件事：无损解码、BOM/换行归一、从第一个非正文标记起删除。其余字符
一个不动。v3 落盘一次 `Stage0Transform`，再物化 `clean_text.txt`。
'''
from __future__ import annotations

from contracts.artifacts import STAGE0_CLEAN_TEXT

import hashlib
from dataclasses import dataclass
from pathlib import Path

from contracts.schema.report import CheckReport
from contracts.schema.stage0 import (
    RemovedSegment,
    Stage0Snapshot,
    Stage0Source,
    Stage0Transform,
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
from novel.core.source import source_ref_for
from novel.errors import (
    ArtifactUnreadable,
    PathRefused,
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
    """一次 stage0 动作之后盘上（或 dry-run 将要）的当前态。"""

    clean_text: str
    report: Stage0Snapshot
    scan: ScanResult

    @property
    def body_ratio(self) -> float:
        return self.report.body_code_points / self.report.total_code_points


def machine_content(
    decoded: DecodedSource,
    *,
    original_path: str,
    raw_sha256: str,
) -> tuple[Stage0Source, str, list[RemovedSegment], ScanResult]:
    '''对已经无损解码的文本执行唯一一次删除决策。'''
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
            removed.append(RemovedSegment(
                kind=marker.kind,
                marker_line=marker.title,
                span=(start, end),
                origin='machine',
            ))

    source = Stage0Source(
        source_ref=source_ref_for(original_path),
        raw_sha256=raw_sha256,
        encoding=decoded.encoding,
        normalized_source_sha1=decoded.normalized_source_sha1,
        total_code_points=len(text),
    )
    # 不变量：重建结果必须等于直接切片。
    rebuilt = rev.reconstruct_clean(text, removed)
    if rebuilt != clean:
        raise ArtifactUnreadable(
            'stage0 machine_content：直接切片与 span 重建不一致')
    return source, clean, removed, scan


def _provisional(
    source: Stage0Source,
    clean_text: str,
    removed: list[RemovedSegment],
    *,
    code: str,
) -> RemovalResult:
    transform = rev.make_transform(
        source=source, code=code, clean_text=clean_text, removed=removed)
    return RemovalResult(clean_text, rev.snapshot_of(transform), scan_text(clean_text))


def remove_decoded(
    decoded: DecodedSource,
    *,
    original_path: str,
    code: str,
    raw_sha256: str,
) -> RemovalResult:
    source, clean, removed, scan = machine_content(
        decoded, original_path=original_path, raw_sha256=raw_sha256)
    result = _provisional(source, clean, removed, code=code)
    return RemovalResult(result.clean_text, result.report, scan)


def remove_bytes(raw: bytes, *, original_path: str, code: str) -> RemovalResult:
    try:
        decoded = decode_source_bytes(raw)
    except DecodeProblem as exc:
        raise SourceDecodeFailed(f'{original_path}: {exc}') from exc
    return remove_decoded(
        decoded,
        original_path=original_path,
        code=code,
        raw_sha256=hashlib.sha256(raw).hexdigest(),
    )


def inspect_content(
    source_path: str | Path,
) -> tuple[Stage0Source, str, list[RemovedSegment], ScanResult]:
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
    return machine_content(
        decoded,
        original_path=str(source),
        raw_sha256=hashlib.sha256(raw).hexdigest(),
    )


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


def _result_of(transform: Stage0Transform, *, clean_text: str) -> RemovalResult:
    return RemovalResult(
        clean_text, rev.snapshot_of(transform), scan_text(clean_text))


def _checked(
    ctx: RunContext,
    transform: Stage0Transform,
    *,
    clean_text: str,
) -> tuple[RemovalResult, CheckReport]:
    from novel.stages.stage0.checks import run_check_only
    return _result_of(transform, clean_text=clean_text), run_check_only(ctx)


def run_source(source_path: str | Path, base: str | Path, *, force: bool = False,
               dry_run: bool = False, expected_sid: str | None = None,
               run_id: str | None = None) \
        -> tuple[Path, RemovalResult, CheckReport | None]:
    '''真实摄入路径：源文件推导目录，落一次机械 transform。'''
    del force  # 历史参数；v3 无强制覆盖语义，原文变化仍按 R-0.14 拒绝。
    decoded_source, clean, removed, scan = inspect_content(source_path)
    machine = RemovalResult(
        clean,
        _provisional(
            decoded_source, clean, removed, code=prov.code_version()).report,
        scan,
    )
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
    existing = rev.read(store) if dry_run else rev.ensure_materialized(store)

    if existing is None:
        if dry_run:
            return novel_dir, machine, None
        fresh = rev.make_transform(
            source=decoded_source, code=ctx.code, clean_text=clean, removed=removed)
        rev.commit(store, fresh, clean_text=clean)
        result, report = _checked(ctx, fresh, clean_text=clean)
        return novel_dir, result, report

    if decoded_source.normalized_source_sha1 != existing.source.normalized_source_sha1:
        raise SourceConflict(
            'stage0_source_changed：本次摄入的原文与产生当前正式稿的原文不是'
            f'同一份（新 {decoded_source.normalized_source_sha1[:12]}… / 旧 '
            f'{existing.source.normalized_source_sha1[:12]}…）。正式稿未动、'
            '仍对旧原文成立。要换原文等于摄入另一本书：另起书目录，'
            '或显式废弃当前书目录后重跑（R-0.14）')

    if (decoded_source.raw_sha256 == existing.source.raw_sha256
            and existing.clean_sha1 == machine.report.clean_sha1
            and list(existing.removed) == removed):
        live = store.read_own_text(CLEAN_REL) if store.exists(CLEAN_REL) else clean
        result, report = _checked(ctx, existing, clean_text=live)
        return novel_dir, result, report

    if dry_run:
        live = store.read_own_text(CLEAN_REL) if store.exists(CLEAN_REL) else clean
        return novel_dir, _result_of(existing, clean_text=live), None

    # v2 人工稿不会被 decode 成 v3；若将来扩展，仍关闭式拒绝覆盖。
    published = rev.make_transform(
        source=decoded_source, code=ctx.code, clean_text=clean, removed=removed)
    rev.commit(store, published, clean_text=clean)
    result, report = _checked(ctx, published, clean_text=clean)
    return novel_dir, result, report
