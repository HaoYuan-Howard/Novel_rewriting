#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''stage0 非正文剔除与三个人工动作。零 LLM。

机器路径只做三件事：无损解码、BOM/换行归一、从第一个非正文标记起删除。其余字符
一个不动。人工编辑是另一条显式入口，会归档上一轮、重算 clean_sha1，并把批准状态
退回 pending；机器重跑不会静默覆盖它。
'''
from __future__ import annotations

import codecs
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from pydantic import ValidationError

from contracts.invariants import (
    BODY_RATIO_MIN,
    DROP_RATIO_MAX,
    NONBODY_MARKER_WARN_CP,
)
from contracts.schema.report import CheckReport
from contracts.schema.stage0 import (
    RemovalReport,
    RemovedSegment,
    Stage0EditPlan,
    Stage0Warning,
)
from novel import paths, prov
from novel.core.ingest import (
    DecodeProblem,
    DecodedSource,
    ScanResult,
    TEXT_NORM,
    decode_source_bytes,
    marker_core,
    nonbody_cut,
    scan_text,
)
from novel.errors import (
    ArtifactUnreadable,
    ByteFormRefused,
    HumanEditProtected,
    PathRefused,
    SourceConflict,
    SourceDecodeFailed,
    SourcePathRejected,
    UpstreamMissing,
)
from novel.llm.cache import CachePolicy
from novel.llm.ledger import CostLedger
from novel.run import RunContext
from novel.store import ArtifactStore

CLEAN_REL = 'stage0/clean_text.txt'
REPORT_REL = 'stage0/removal_report.json'


@dataclass(frozen=True)
class RemovalResult:
    clean_text: str
    report: RemovalReport
    scan: ScanResult

    @property
    def body_ratio(self) -> float:
        return self.report.body_code_points / self.report.total_code_points


def report_round_rel(round_no: int) -> str:
    return f'stage0/_rounds/removal_report.r{round_no}.json'


def clean_round_rel(round_no: int) -> str:
    return f'stage0/_rounds/clean_text.r{round_no}.txt'


def preview_text(value: str, limit: int) -> str:
    return re.sub(r'\s+', ' ', value).strip()[:limit]


def warnings_for(total: int, body: int,
                 removed: Sequence[RemovedSegment]) -> list[Stage0Warning]:
    out: list[Stage0Warning] = []
    ratio = body / total if total else 0.0
    removed_points = sum(segment.code_points for segment in removed)
    if ratio < BODY_RATIO_MIN:
        out.append(Stage0Warning(
            kind='early_cut',
            message=f'清理后正文只占 {ratio:.1%}，低于 {BODY_RATIO_MIN:.0%}'))
    if total and removed_points / total > DROP_RATIO_MAX:
        out.append(Stage0Warning(
            kind='drop_ratio',
            message=f'剔除段占全文 {removed_points / total:.1%}，高于 {DROP_RATIO_MAX:.0%}'))
    for segment in removed:
        length = len(marker_core(segment.marker_line))
        if length > NONBODY_MARKER_WARN_CP:
            out.append(Stage0Warning(
                kind='long_marker',
                message=f'第 {segment.line_no} 行标记 core 长 {length}，超过实测真标记上限'))
    return out


def remove_decoded(
    decoded: DecodedSource,
    *,
    original_path: str,
    code: str,
) -> RemovalResult:
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
            segment_text = text[start:end]
            removed.append(RemovedSegment(
                kind=marker.kind,
                marker_line=marker.title,
                line_no=text.count('\n', 0, marker.line_start) + 1,
                span=(start, end),
                code_points=len(segment_text),
                head_preview=preview_text(text[marker.content_start:end], 80),
                tail_preview=preview_text(segment_text[-120:], 40),
                origin='machine',
            ))

    absolute = str(Path(original_path).resolve())
    report = RemovalReport(
        original_filename=Path(original_path).name,
        original_path=absolute,
        code=code,
        source_sha1=decoded.source_sha1,
        clean_sha1=prov.content_sha1(clean.encode('utf-8')),
        source_encoding=decoded.encoding,
        text_norm=TEXT_NORM,
        removed=removed,
        total_code_points=len(text),
        body_code_points=len(clean),
        warnings=warnings_for(len(text), len(clean), removed),
        approved='pending',
        approved_at=None,
        round=1,
        edited_by_human=False,
    )
    return RemovalResult(clean, report, scan)


def remove_bytes(raw: bytes, *, original_path: str, code: str) -> RemovalResult:
    '''合成样本与磁盘入口共用的字节级 API。'''
    try:
        decoded = decode_source_bytes(raw)
    except DecodeProblem as exc:
        raise SourceDecodeFailed(f'{original_path}: {exc}') from exc
    return remove_decoded(decoded, original_path=original_path, code=code)


def inspect_source(source_path: str | Path, *, code: str) -> RemovalResult:
    source = Path(source_path).resolve()
    if not source.is_file():
        raise UpstreamMissing(f'原始小说不在盘上：{source}')
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise ArtifactUnreadable(f'读不了原始小说 {source}：{exc!r}') from exc
    return remove_bytes(raw, original_path=str(source), code=code)


def _context(novel_dir: Path, node: str) -> RunContext:
    return RunContext(
        novel_dir=novel_dir,
        node=node,
        paid=False,
        code=prov.code_version(),
        cache=CachePolicy.default(),
        ledger=CostLedger(path=None),
    )


def _existing(store: ArtifactStore) -> tuple[RemovalReport, str] | None:
    has_report = store.exists(REPORT_REL)
    has_clean = store.exists(CLEAN_REL)
    if has_report != has_clean:
        present = REPORT_REL if has_report else CLEAN_REL
        missing = CLEAN_REL if has_report else REPORT_REL
        raise ArtifactUnreadable(
            f'stage0 只落了一半：有 {present}，缺 {missing}。不能拿半轮产物继续')
    if not has_report:
        return None
    report, _raw = store.read_own(RemovalReport, REPORT_REL)
    return report, store.read_text(CLEAN_REL)


def _archive_current(store: ArtifactStore, round_no: int) -> None:
    # archive_bytes 对“目标已存在且字节相同”幂等：若进程恰好死在两次复制之间，
    # 重跑会保留第一份并补齐第二份；字节不同仍拒绝，绝不覆盖历史。
    store.archive_bytes(REPORT_REL, report_round_rel(round_no), keep=True)
    store.archive_bytes(CLEAN_REL, clean_round_rel(round_no), keep=True)


def _write(store: ArtifactStore, result: RemovalResult) -> None:
    # 两份产物各自原子；跨文件不假装原子。任一中断都会留下可由双哈希识别的半轮状态。
    store.write_text(CLEAN_REL, result.clean_text)
    store.write(REPORT_REL, result.report)


def run_source(source_path: str | Path, base: str | Path, *, force: bool = False,
               dry_run: bool = False, expected_sid: str | None = None) \
        -> tuple[Path, RemovalResult, CheckReport | None]:
    '''真实摄入路径：源文件推导目录，保护已有人工稿，写 stage0 两份产物。'''
    result = inspect_source(source_path, code=prov.code_version())
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
    ctx = _context(novel_dir, 'stage0.remove')
    store = ArtifactStore(ctx)
    previous = _existing(store)

    if previous is not None:
        old, old_clean = previous
        actual_old_sha1 = prov.content_sha1(old_clean.encode('utf-8'))
        same_machine_result = (
            old.source_sha1 == result.report.source_sha1
            and old.clean_sha1 == actual_old_sha1
            and old_clean == result.clean_text
        )
        if same_machine_result and not force:
            kept = RemovalResult(old_clean, old, result.scan)
            from novel.stages.stage0.checks import run_check_only
            return novel_dir, kept, run_check_only(ctx)

        if old.source_sha1 != result.report.source_sha1 and not force:
            raise SourceConflict(
                f'{novel_dir} 已属于 source_sha1={old.source_sha1[:10]}，本次是 '
                f'{result.report.source_sha1[:10]}；确认销毁式重建才可显式强制')

        human_state = (
            old.edited_by_human
            or any(segment.origin == 'human' for segment in old.removed)
            or old.clean_sha1 != actual_old_sha1
        )
        if human_state and not force:
            raise HumanEditProtected(
                f'{novel_dir} 的 stage0 含人工编辑或盘上 clean_text 已被人改过；'
                f'机器重跑不会覆盖它。先选择合并策略，或显式强制销毁式重建')

        rebuilt = result.report.model_dump(mode='json')
        rebuilt['round'] = old.round + 1
        result = RemovalResult(
            result.clean_text,
            RemovalReport.model_validate(rebuilt),
            result.scan,
        )

    if dry_run:
        return novel_dir, result, None

    if previous is not None:
        _archive_current(store, previous[0].round)
    _write(store, result)
    from novel.stages.stage0.checks import run_check_only
    return novel_dir, result, run_check_only(ctx)


def _human_text(edit_path: str | Path) -> str:
    path = Path(edit_path).resolve()
    if not path.is_file():
        raise UpstreamMissing(f'人工编辑文件不在盘上：{path}')
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ArtifactUnreadable(f'读不了人工编辑文件 {path}：{exc!r}') from exc
    if raw.startswith(codecs.BOM_UTF8):
        raise ByteFormRefused('人工 clean_text 带 UTF-8 BOM；请按无 BOM UTF-8 保存')
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ArtifactUnreadable(f'人工 clean_text 不是 UTF-8：{exc!r}') from exc
    if '\r' in text:
        raise ByteFormRefused('人工 clean_text 含 CR/CRLF；请按 LF-only 保存，不做静默归一')
    return text


def _apply_edit(
    ctx: RunContext,
    text: str,
    *,
    removed: Sequence[RemovedSegment] | None = None,
    expected_round: int | None = None,
    expected_source_sha1: str | None = None,
    expected_clean_sha1: str | None = None,
    expected_total_code_points: int | None = None,
) -> tuple[RemovalReport, CheckReport]:
    '''落一轮已校验的人工作业；乐观锁只在审校台计划路径启用。'''
    store = ArtifactStore(ctx)
    previous = _existing(store)
    if previous is None:
        raise UpstreamMissing('stage0 还没跑，没有可编辑的 clean_text')
    old, old_clean = previous
    actual_old_sha1 = prov.content_sha1(old_clean.encode('utf-8'))
    if actual_old_sha1 != old.clean_sha1:
        raise HumanEditProtected(
            '当前 removal_report 与 clean_text 不是同一轮：可能有人绕过 stage0.edit '
            '直接改了正式稿，也可能上次进程中断在两文件提交之间。'
            '请先从同轮 _rounds 归档恢复成对版本，再重跑编辑')
    expected = {
        'round': expected_round,
        'source_sha1': expected_source_sha1,
        'clean_sha1': expected_clean_sha1,
        'total_code_points': expected_total_code_points,
    }
    stale = [
        f'{name}: 页面={want!r}, 当前={getattr(old, name)!r}'
        for name, want in expected.items()
        if want is not None and getattr(old, name) != want
    ]
    if stale:
        raise HumanEditProtected(
            '这份 stage0 编辑计划基于旧页面，拒绝覆盖当前轮：' + '；'.join(stale))
    next_removed = list(removed) if removed is not None else list(old.removed)
    if text == old_clean and next_removed == old.removed:
        from novel.stages.stage0.checks import run_check_only
        return old, run_check_only(ctx)

    payload = old.model_dump(mode='json')
    payload.update({
        'code': ctx.code,
        'clean_sha1': prov.content_sha1(text.encode('utf-8')),
        'removed': [segment.model_dump(mode='json') for segment in next_removed],
        'body_code_points': len(text),
        'warnings': [warning.model_dump(mode='json')
                     for warning in warnings_for(
                         old.total_code_points, len(text), next_removed)],
        'approved': 'pending',
        'approved_at': None,
        'round': old.round + 1,
        'edited_by_human': True,
    })
    report = RemovalReport.model_validate(payload)
    _archive_current(store, old.round)
    _write(store, RemovalResult(text, report, scan_text(text)))
    from novel.stages.stage0.checks import run_check_only
    return report, run_check_only(ctx)


def run_edit(ctx: RunContext, edit_path: str | Path, *,
             removed: Sequence[RemovedSegment] | None = None) -> tuple[RemovalReport, CheckReport]:
    '''落一轮人工 clean_text；上一轮的 JSON/TXT 成对进入 stage0/_rounds。'''
    return _apply_edit(ctx, _human_text(edit_path), removed=removed)


def _read_edit_plan(plan_path: str | Path) -> Stage0EditPlan:
    path = Path(plan_path).resolve()
    if not path.is_file():
        raise UpstreamMissing(f'stage0 编辑计划不在盘上：{path}')
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ArtifactUnreadable(f'读不了 stage0 编辑计划 {path}：{exc!r}') from exc
    if raw.startswith(codecs.BOM_UTF8):
        raise ByteFormRefused('stage0 编辑计划带 UTF-8 BOM；内部 JSON 必须是无 BOM UTF-8')
    try:
        return Stage0EditPlan.model_validate_json(raw)
    except (ValidationError, UnicodeDecodeError) as exc:
        raise ArtifactUnreadable(f'stage0 编辑计划不是合法契约：{exc}') from exc


def run_edit_plan(
    ctx: RunContext,
    plan_path: str | Path,
) -> tuple[RemovalReport, CheckReport]:
    '''应用 S-00 选择计划；原文、区间、重建正文与乐观锁全部在子进程内复核。'''
    plan = _read_edit_plan(plan_path)
    store = ArtifactStore(ctx)
    previous = _existing(store)
    if previous is None:
        raise UpstreamMissing('stage0 还没跑，没有可应用编辑计划的 clean_text')
    old, _old_clean = previous
    source = Path(old.original_path).resolve()
    if not source.is_file():
        raise UpstreamMissing(f'原始小说已经不在 original_path：{source}')
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise ArtifactUnreadable(f'读不了原始小说 {source}：{exc!r}') from exc
    try:
        decoded = decode_source_bytes(raw)
    except DecodeProblem as exc:
        raise SourceDecodeFailed(f'{source}: {exc}') from exc
    if decoded.source_sha1 != old.source_sha1:
        raise SourceConflict(
            f'原始小说已从 {old.source_sha1[:10]} 变为 {decoded.source_sha1[:10]}；'
            '页面上的区间全部失效，请重新摄入并审阅')
    if len(decoded.text) != old.total_code_points:
        raise SourceConflict(
            f'原始小说当前长度 {len(decoded.text)} 与报告 {old.total_code_points} 对不上')

    cursor = 0
    kept: list[str] = []
    for segment in plan.removed:
        lo, hi = segment.span
        kept.append(decoded.text[cursor:lo])
        cursor = hi
    kept.append(decoded.text[cursor:])
    reconstructed = ''.join(kept)
    if reconstructed != plan.clean_text:
        raise ArtifactUnreadable(
            '编辑计划的 clean_text 不能由原文减去 removed[] 精确重建；拒绝写一份对不上的剔除账')

    return _apply_edit(
        ctx,
        plan.clean_text,
        removed=plan.removed,
        expected_round=plan.expected_round,
        expected_source_sha1=plan.expected_source_sha1,
        expected_clean_sha1=plan.expected_clean_sha1,
        expected_total_code_points=plan.expected_total_code_points,
    )


def run_approve(ctx: RunContext, *, approved_at: str | None = None) \
        -> tuple[RemovalReport, CheckReport]:
    '''批准当前这一轮。批准不改正文、不制造新轮次。'''
    store = ArtifactStore(ctx)
    previous = _existing(store)
    if previous is None:
        raise UpstreamMissing('stage0 还没跑，没有可批准的剔除结果')
    old, clean = previous
    actual = prov.content_sha1(clean.encode('utf-8'))
    if actual != old.clean_sha1:
        raise HumanEditProtected(
            '盘上 clean_text 已变化但 removal_report 还没登记这次人工编辑；先走 stage0.edit')
    if old.approved == 'approved':
        from novel.stages.stage0.checks import run_check_only
        return old, run_check_only(ctx)
    payload = old.model_dump(mode='json')
    payload.update({
        'code': ctx.code,
        'approved': 'approved',
        'approved_at': approved_at or datetime.now(timezone.utc).isoformat(),
    })
    report = RemovalReport.model_validate(payload)
    store.write(REPORT_REL, report)
    from novel.stages.stage0.checks import run_check_only
    return report, run_check_only(ctx)
