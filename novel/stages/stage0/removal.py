#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''stage0 非正文剔除与三个人工动作。零 LLM。

机器路径只做三件事：无损解码、BOM/换行归一、从第一个非正文标记起删除。其余字符
一个不动。人工编辑是另一条显式入口，会归档上一轮、重算 clean_sha1，并把批准状态
退回 pending；机器重跑不会静默覆盖它。
'''
from __future__ import annotations

from contracts.artifacts import (
    STAGE0_AUDIT_ENTRY, STAGE0_CLEAN_TEXT, STAGE0_MACHINE_SNAPSHOT,
    STAGE0_MERGE_CANDIDATE,
    STAGE0_REMOVAL_REPORT,
)

import codecs
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from pydantic import ValidationError

from contracts.invariants import (
    BODY_RATIO_MIN,
    DROP_RATIO_MAX,
    NONBODY_MARKER_WARN_CP,
)
from contracts.schema.report import CheckReport
from contracts.schema.stage0 import (
    RemovalReport,
    MachineRemovalSnapshot,
    RemovedSegment,
    Stage0ConflictResolution,
    Stage0AuditEntry,
    Stage0EditPlan,
    Stage0MergeCandidate,
    Stage0MergeDecisionPlan,
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
    ProvenanceRefused,
    SourceConflict,
    SourceDecodeFailed,
    SourcePathRejected,
    UpstreamMissing,
)
from novel.llm.cache import CachePolicy
from novel.llm.ledger import CostLedger
from novel.run import RunContext
from novel.store import ArtifactStore

CLEAN_REL = STAGE0_CLEAN_TEXT
REPORT_REL = STAGE0_REMOVAL_REPORT
SNAPSHOT_REL = STAGE0_MACHINE_SNAPSHOT
CANDIDATE_REL = STAGE0_MERGE_CANDIDATE


@dataclass(frozen=True)
class RemovalResult:
    clean_text: str
    report: RemovalReport
    scan: ScanResult
    machine_snapshot: MachineRemovalSnapshot

    @property
    def body_ratio(self) -> float:
        return self.report.body_code_points / self.report.total_code_points


def report_round_rel(round_no: int) -> str:
    return paths.round_rel(REPORT_REL, round_no)


def clean_round_rel(round_no: int) -> str:
    directory, filename = CLEAN_REL.rsplit('/', 1)
    stem, dot, extension = filename.rpartition('.')
    return f'{directory}/{paths.ROUNDS_DIRNAME}/{stem}.r{round_no}{dot}{extension}'


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
    from novel.stages.stage0.merge import snapshot_digest
    snapshot_payload = {
        'created_at': datetime.now(timezone.utc).isoformat(),
        'code': code,
        'original_filename': Path(original_path).name,
        'original_path': absolute,
        'source_sha1': decoded.source_sha1,
        'clean_sha1': prov.content_sha1(clean.encode('utf-8')),
        'source_encoding': decoded.encoding,
        'text_norm': TEXT_NORM,
        'total_code_points': len(text),
        'body_code_points': len(clean),
        'clean_text': clean,
        'removed': [item.model_dump(mode='json') for item in removed],
        'warnings': [item.model_dump(mode='json') for item in warnings_for(len(text), len(clean), removed)],
    }
    snapshot = MachineRemovalSnapshot(
        **snapshot_payload,
        snapshot_sha256=snapshot_digest(snapshot_payload),
    )
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
        warnings=snapshot.warnings,
        machine_source_sha1=snapshot.source_sha1,
        machine_clean_sha1=snapshot.clean_sha1,
        machine_snapshot_sha256=snapshot.snapshot_sha256,
        approved='pending',
        approved_at=None,
        approval_origin=None,
        approved_by=None,
        round=1,
        edited_by_human=False,
        audit_seq=0,
        audit_head_sha256=None,
    )
    return RemovalResult(clean, report, scan, snapshot)


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


def _require_stage0_fresh(store: ArtifactStore) -> None:
    """Refuse committing an approval against source/clean bytes that already moved."""
    freshness = store.freshness(REPORT_REL)
    if freshness.state == 'fresh':
        return
    edges = ', '.join(
        f'{edge.key}:{edge.reason}' for edge in freshness.edges) or freshness.reason
    raise ProvenanceRefused(
        'Stage0 approval transition wrote a report that is not current against '
        f'the source/clean decision surface: {freshness.state}/{freshness.reason} '
        f'({edges}). The report and its audit entry were rolled back.')


_AUTO_APPROVAL_REASON = (
    '首次机器剔除零 removed、零 warning，且 clean 与归一化 source 完全一致'
)


def _auto_approval_eligible(report: RemovalReport) -> bool:
    return (
        not report.removed
        and not report.warnings
        and report.source_sha1 == report.clean_sha1
    )


def _with_auto_approval(
    result: RemovalResult,
    *,
    ctx: RunContext | None,
) -> RemovalResult:
    """Return the first-run auto-approved shape; persist its audit only with a ctx."""
    from novel.stages.stage0.merge import append_audit, make_audit_entry

    kwargs = {
        'seq': 1,
        'previous_sha256': None,
        'action': 'auto_approve',
        'actor': None,
        'reason': _AUTO_APPROVAL_REASON,
        'before_source_sha1': None,
        'before_clean_sha1': None,
        'after_source_sha1': result.report.source_sha1,
        'after_clean_sha1': result.report.clean_sha1,
    }
    entry = make_audit_entry(**kwargs) if ctx is None else append_audit(ctx, **kwargs)
    return RemovalResult(
        result.clean_text,
        result.report.model_copy(update={
            'approved': 'approved', 'approved_at': entry.at,
            'approval_origin': 'auto_zero_hit', 'approved_by': None,
            'audit_seq': entry.seq, 'audit_head_sha256': entry.entry_sha256,
        }),
        result.scan,
        result.machine_snapshot,
    )


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
        if actual_old_sha1 != old.clean_sha1:
            raise HumanEditProtected(
                '当前 removal_report 与 clean_text 不是同一轮；先恢复成对版本再重跑机器剔除')
        old_snapshot, _ = store.read_own(MachineRemovalSnapshot, SNAPSHOT_REL)
        if old_snapshot.snapshot_sha256 != old.machine_snapshot_sha256:
            raise ArtifactUnreadable('machine_snapshot 与 removal_report 的快照指纹不一致')
        same_machine_result = (
            old.machine_source_sha1 == result.machine_snapshot.source_sha1
            and old.machine_clean_sha1 == result.machine_snapshot.clean_sha1
            and old_snapshot.snapshot_sha256 == result.machine_snapshot.snapshot_sha256
        )
        if same_machine_result:
            kept = RemovalResult(old_clean, old, result.scan, old_snapshot)
            from novel.stages.stage0.checks import run_check_only
            return novel_dir, kept, run_check_only(ctx)

        # 机器结果变化时，正式正文/报告一个字不动；只写可审阅的 base/ours/theirs 候选。
        from novel.stages.stage0.merge import make_candidate
        candidate = make_candidate(
            expected_round=old.round,
            expected_source_sha1=old.source_sha1,
            expected_clean_sha1=old.clean_sha1,
            base_snapshot=old_snapshot,
            theirs_snapshot=result.machine_snapshot,
            base_text=old_snapshot.clean_text,
            ours_text=old_clean,
            theirs_source_sha1=result.machine_snapshot.source_sha1,
            theirs_text=result.clean_text,
        )
        if dry_run:
            return novel_dir, RemovalResult(old_clean, old, result.scan, old_snapshot), None
        if store.exists(CANDIDATE_REL):
            current, _ = store.read_own(Stage0MergeCandidate, CANDIDATE_REL)
            if current.candidate_id != candidate.candidate_id:
                raise HumanEditProtected(
                    '已有另一份待决 merge candidate；必须先接受或拒绝，不能覆盖')
        else:
            store.write(CANDIDATE_REL, candidate)
        kept = RemovalResult(old_clean, old, result.scan, old_snapshot)
        from novel.stages.stage0.checks import run_check_only
        return novel_dir, kept, run_check_only(ctx)

    # 第一次且机器零删除、零告警、clean 与归一化 source 完全相同，才可自动批准。
    # 此时盘上还不存在任何历史轮次/审计链，因此不存在历史漂移可被掩盖。
    auto_approved = _auto_approval_eligible(result.report)
    if auto_approved:
        # dry-run 也返回真实的批准决策/链头形状，但用纯内存 entry，
        # 绝不创建 novel_dir、_audit 或任何正式产物。
        result = _with_auto_approval(result, ctx=None)

    if dry_run:
        return novel_dir, result, None

    transaction_rels = [SNAPSHOT_REL, CLEAN_REL, REPORT_REL]
    if auto_approved:
        transaction_rels.append(STAGE0_AUDIT_ENTRY.format(n=result.report.audit_seq))
    with store.history_transaction(*transaction_rels):
        if auto_approved:
            from novel.stages.stage0.merge import append_audit
            entry = append_audit(
                ctx, seq=result.report.audit_seq, previous_sha256=None,
                action='auto_approve', actor=None, reason=_AUTO_APPROVAL_REASON,
                before_source_sha1=None, before_clean_sha1=None,
                after_source_sha1=result.report.source_sha1,
                after_clean_sha1=result.report.clean_sha1,
                at=result.report.approved_at,
            )
            if (entry.entry_sha256 != result.report.audit_head_sha256
                    or entry.at != result.report.approved_at):
                raise ArtifactUnreadable(
                    'Stage0 first-run auto approval found an orphan audit entry '
                    'that does not match the in-memory decision')
        store.write(SNAPSHOT_REL, result.machine_snapshot)
        _write(store, result)
        if auto_approved:
            _require_stage0_fresh(store)
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
    actor: str,
    reason: str,
) -> tuple[RemovalReport, CheckReport]:
    '''落一轮已校验的人工作业；乐观锁只在审校台计划路径启用。'''
    store = ArtifactStore(ctx)
    previous = _existing(store)
    if previous is None:
        raise UpstreamMissing('stage0 还没跑，没有可编辑的 clean_text')
    old, old_clean = previous
    if not actor.strip() or not reason.strip():
        raise HumanEditProtected('stage0 人工编辑必须显式给 actor 与 reason')
    from novel.stages.stage0.merge import append_audit, verify_audit_chain
    verify_audit_chain(ctx, seq=old.audit_seq, head=old.audit_head_sha256)
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

    next_clean_sha1 = prov.content_sha1(text.encode('utf-8'))
    entry = append_audit(
        ctx, seq=old.audit_seq + 1, previous_sha256=old.audit_head_sha256,
        action='edit', actor=actor.strip(), reason=reason.strip(),
        before_source_sha1=old.source_sha1, before_clean_sha1=old.clean_sha1,
        after_source_sha1=old.source_sha1, after_clean_sha1=next_clean_sha1,
    )
    payload = old.model_dump(mode='json')
    payload.update({
        'code': ctx.code,
        'clean_sha1': next_clean_sha1,
        'removed': [segment.model_dump(mode='json') for segment in next_removed],
        'body_code_points': len(text),
        'warnings': [warning.model_dump(mode='json')
                     for warning in warnings_for(
                         old.total_code_points, len(text), next_removed)],
        'approved': 'pending',
        'approved_at': None,
        'approval_origin': None,
        'approved_by': None,
        'round': old.round + 1,
        'edited_by_human': True,
        'audit_seq': entry.seq,
        'audit_head_sha256': entry.entry_sha256,
    })
    report = RemovalReport.model_validate(payload)
    snapshot, _ = store.read_own(MachineRemovalSnapshot, SNAPSHOT_REL)
    _archive_current(store, old.round)
    _write(store, RemovalResult(text, report, scan_text(text), snapshot))
    from novel.stages.stage0.checks import run_check_only
    return report, run_check_only(ctx)


def run_edit(ctx: RunContext, edit_path: str | Path, *,
             actor: str, reason: str,
             removed: Sequence[RemovedSegment] | None = None) -> tuple[RemovalReport, CheckReport]:
    '''落一轮人工 clean_text；上一轮的 JSON/TXT 成对进入 stage0/_rounds。'''
    return _apply_edit(
        ctx, _human_text(edit_path), removed=removed, actor=actor, reason=reason)


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


def _read_merge_plan(plan_path: str | Path) -> Stage0MergeDecisionPlan:
    path = Path(plan_path).resolve()
    if not path.is_file():
        raise UpstreamMissing(f'stage0 合并决定不在盘上：{path}')
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ArtifactUnreadable(f'读不了 stage0 合并决定 {path}：{exc!r}') from exc
    if raw.startswith(codecs.BOM_UTF8):
        raise ByteFormRefused('stage0 合并决定带 UTF-8 BOM；内部 JSON 必须无 BOM')
    try:
        return Stage0MergeDecisionPlan.model_validate_json(raw)
    except (ValidationError, UnicodeDecodeError) as exc:
        raise ArtifactUnreadable(f'stage0 合并决定不是合法契约：{exc}') from exc


def run_merge_plan(ctx: RunContext, plan_path: str | Path) -> tuple[RemovalReport, CheckReport]:
    plan = _read_merge_plan(plan_path)
    common = dict(
        candidate_id=plan.candidate_id,
        expected_round=plan.expected_round,
        expected_source_sha1=plan.expected_source_sha1,
        expected_clean_sha1=plan.expected_clean_sha1,
        actor=plan.actor,
        reason=plan.reason,
    )
    if plan.action == 'accept':
        return run_merge_accept(ctx, resolutions=plan.resolutions, **common)
    return run_merge_reject(ctx, **common)


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
        actor=plan.actor,
        reason=plan.reason,
    )


def run_approve(ctx: RunContext, *, actor: str, reason: str,
                approved_at: str | None = None) \
        -> tuple[RemovalReport, CheckReport]:
    '''批准当前这一轮。批准不改正文、不制造新轮次。'''
    store = ArtifactStore(ctx)
    previous = _existing(store)
    if previous is None:
        raise UpstreamMissing('stage0 还没跑，没有可批准的剔除结果')
    old, clean = previous
    if not actor.strip() or not reason.strip():
        raise HumanEditProtected('stage0 人工批准必须显式给 actor 与 reason')
    actual = prov.content_sha1(clean.encode('utf-8'))
    if actual != old.clean_sha1:
        raise HumanEditProtected(
            '盘上 clean_text 已变化但 removal_report 还没登记这次人工编辑；先走 stage0.edit')
    if old.approved == 'approved':
        from novel.stages.stage0.checks import run_check_only
        return old, run_check_only(ctx)
    from novel.stages.stage0.merge import append_audit, verify_audit_chain
    verify_audit_chain(ctx, seq=old.audit_seq, head=old.audit_head_sha256)
    at = approved_at or datetime.now(timezone.utc).isoformat()
    audit_rel = STAGE0_AUDIT_ENTRY.format(n=old.audit_seq + 1)
    with store.history_transaction(REPORT_REL, audit_rel):
        entry = append_audit(
            ctx, seq=old.audit_seq + 1, previous_sha256=old.audit_head_sha256,
            action='approve', actor=actor.strip(), reason=reason.strip(),
            before_source_sha1=old.source_sha1, before_clean_sha1=old.clean_sha1,
            after_source_sha1=old.source_sha1, after_clean_sha1=old.clean_sha1,
            at=at,
        )
        payload = old.model_dump(mode='json')
        payload.update({
            'code': ctx.code,
            'approved': 'approved',
            'approved_at': at,
            'approval_origin': 'human',
            'approved_by': actor.strip(),
            'audit_seq': entry.seq,
            'audit_head_sha256': entry.entry_sha256,
        })
        report = RemovalReport.model_validate(payload)
        store.write(REPORT_REL, report)
        _require_stage0_fresh(store)
    from novel.stages.stage0.checks import run_check_only
    return report, run_check_only(ctx)


def read_merge_candidate(ctx: RunContext) -> Stage0MergeCandidate:
    store = ArtifactStore(ctx)
    candidate, _ = store.read_own(Stage0MergeCandidate, CANDIDATE_REL)
    from novel.stages.stage0.merge import candidate_digest, snapshot_digest
    if candidate_digest(candidate) != candidate.candidate_id:
        raise ArtifactUnreadable('stage0 merge candidate_id 校验失败，候选已被改动')
    if snapshot_digest(candidate.theirs_snapshot) != candidate.theirs_snapshot.snapshot_sha256:
        raise ArtifactUnreadable('stage0 merge candidate 的 theirs_snapshot 指纹校验失败')
    return candidate


def _candidate_archive_rel(candidate_id: str) -> str:
    return f'stage0/_rounds/merge_candidate.{candidate_id}.json'


def _merge_checkpoint(_phase: str) -> None:
    """Fault-injection seam for the durable merge commit state machine."""


def _request_matches_candidate(
    candidate: Stage0MergeCandidate,
    old: RemovalReport,
    *,
    candidate_id: str,
    expected_round: int,
    expected_source_sha1: str,
    expected_clean_sha1: str,
) -> None:
    stale: list[str] = []
    checks = (
        ('candidate_id', candidate_id, candidate.candidate_id),
        ('round', expected_round, old.round),
        ('source_sha1', expected_source_sha1, old.source_sha1),
        ('clean_sha1', expected_clean_sha1, old.clean_sha1),
    )
    for name, expected, current in checks:
        if expected != current:
            stale.append(f'{name}: 请求={expected!r}, 当前={current!r}')
    if candidate.expected_round != old.round \
            or candidate.expected_source_sha1 != old.source_sha1 \
            or candidate.expected_clean_sha1 != old.clean_sha1:
        stale.append('候选自身的 expected_* 已不再对应正式稿')
    if candidate.base_snapshot_sha256 != old.machine_snapshot_sha256 \
            or candidate.ours_clean_sha1 != old.clean_sha1:
        stale.append('候选的 base/ours 身份已不再对应正式稿')
    if stale:
        raise HumanEditProtected('Stage0 merge 乐观锁失效：' + '；'.join(stale))


def _merge_audit_entry(
    ctx: RunContext,
    old: RemovalReport,
    *,
    action: str,
    actor: str,
    reason: str,
    after_source_sha1: str,
    after_clean_sha1: str,
    candidate_id: str,
) -> Stage0AuditEntry:
    """Append or replay the sole WAL row immediately after ``old``'s head."""
    from novel.stages.stage0.merge import append_audit, pending_merge_audit

    pending = pending_merge_audit(
        ctx, seq=old.audit_seq, head=old.audit_head_sha256)
    entry = append_audit(
        ctx, seq=old.audit_seq + 1, previous_sha256=old.audit_head_sha256,
        action=action, actor=actor, reason=reason,
        before_source_sha1=old.source_sha1, before_clean_sha1=old.clean_sha1,
        after_source_sha1=after_source_sha1, after_clean_sha1=after_clean_sha1,
        candidate_id=candidate_id,
    )
    if pending is not None and pending != entry:
        raise HumanEditProtected(
            '盘上已有另一份待恢复 merge 审计决定；不可用新请求覆盖')
    _merge_checkpoint('audit')
    return entry


def _ensure_accept_archives(
    store: ArtifactStore,
    old: RemovalReport,
    old_clean: str,
) -> None:
    """Idempotently preserve the complete pre-merge formal pair before writes."""
    report_archive = report_round_rel(old.round)
    if store.exists(report_archive):
        archived, _ = store.read_own(RemovalReport, report_archive)
        if archived != old:
            raise ArtifactUnreadable(
                f'{report_archive} 已存在但不是待合并的第 {old.round} 轮报告')
    else:
        live, _ = store.read_own(RemovalReport, REPORT_REL)
        if live != old:
            raise ArtifactUnreadable('归档前 live removal_report 已不是待合并旧轮')
        store.archive_bytes(REPORT_REL, report_archive, keep=True)
    _merge_checkpoint('report_archive')

    clean_archive = clean_round_rel(old.round)
    if store.exists(clean_archive):
        if store.read_own_text(clean_archive) != old_clean:
            raise ArtifactUnreadable(
                f'{clean_archive} 已存在但不是待合并的第 {old.round} 轮正文')
    else:
        if store.read_text(CLEAN_REL) != old_clean:
            raise ArtifactUnreadable('归档前 live clean_text 已不是待合并旧轮')
        store.archive_bytes(CLEAN_REL, clean_archive, keep=True)
    _merge_checkpoint('clean_archive')


def _retire_merge_candidate(
    ctx: RunContext,
    store: ArtifactStore,
    candidate_id: str,
) -> None:
    """Move the candidate only after the formal report is the commit marker."""
    archive_rel = _candidate_archive_rel(candidate_id)
    if store.exists(CANDIDATE_REL):
        current = read_merge_candidate(ctx)
        if current.candidate_id != candidate_id:
            raise HumanEditProtected(
                'merge 已提交，但 live candidate 已换成另一份；拒绝移动错误候选')
        store.retire_bytes(CANDIDATE_REL, archive_rel)
    elif not store.exists(archive_rel):
        raise ArtifactUnreadable(
            'merge 正式提交已存在，但 live 与归档 candidate 都缺失；审计证据不闭合')
    _merge_checkpoint('candidate_archived')


def _completed_merge(
    ctx: RunContext,
    store: ArtifactStore,
    report: RemovalReport,
    clean: str,
    *,
    action: str,
    candidate_id: str,
    expected_round: int,
    expected_source_sha1: str,
    expected_clean_sha1: str,
    actor: str,
    reason: str,
    finalize_candidate: bool,
) -> RemovalReport | None:
    """Recognize a committed replay without confusing an extra WAL row for commit."""
    from novel.stages.stage0.merge import pending_merge_audit, snapshot_digest, verify_audit_chain

    pending = pending_merge_audit(
        ctx, seq=report.audit_seq, head=report.audit_head_sha256)
    if pending is not None or report.audit_seq == 0:
        return None
    rel = f'stage0/_audit/edit_{report.audit_seq}.json'
    entry, _ = store.read_own(Stage0AuditEntry, rel)
    if entry.candidate_id != candidate_id:
        return None
    if entry.action != action:
        raise HumanEditProtected(
            f'candidate {candidate_id} 已以 {entry.action} 终结，不能重放为 {action}')
    if entry.actor != actor or entry.reason != reason:
        raise HumanEditProtected(
            'candidate 已终结，但 actor/reason 与本次重放不同；拒绝冒充原决定')
    if (entry.before_source_sha1, entry.before_clean_sha1) != (
            expected_source_sha1, expected_clean_sha1):
        raise HumanEditProtected('已提交 merge 的 before 哈希与本次乐观锁不同')
    wanted_round = expected_round + 1 if action == 'merge_accept' else expected_round
    if report.round != wanted_round:
        raise ArtifactUnreadable(
            f'已提交 {action} 的 report.round={report.round}，预期 {wanted_round}')
    if (report.source_sha1, report.clean_sha1) != (
            entry.after_source_sha1, entry.after_clean_sha1):
        raise ArtifactUnreadable('merge 报告的 after 哈希与审计条目不一致')
    if prov.content_sha1(clean.encode('utf-8')) != report.clean_sha1:
        raise ArtifactUnreadable('merge 报告已提交，但 clean_text 与报告 clean_sha1 不一致')
    snapshot, _ = store.read_own(MachineRemovalSnapshot, SNAPSHOT_REL)
    if snapshot_digest(snapshot) != snapshot.snapshot_sha256:
        raise ArtifactUnreadable('当前 machine_snapshot 自身指纹校验失败')
    if (
        report.machine_snapshot_sha256 != snapshot.snapshot_sha256
        or report.machine_source_sha1 != snapshot.source_sha1
        or report.machine_clean_sha1 != snapshot.clean_sha1
    ):
        raise ArtifactUnreadable('merge 报告与当前 machine_snapshot 不闭合')
    verify_audit_chain(ctx, seq=report.audit_seq, head=report.audit_head_sha256)
    if finalize_candidate:
        _retire_merge_candidate(ctx, store, candidate_id)
    return report


def run_merge_accept(
    ctx: RunContext,
    *,
    candidate_id: str,
    expected_round: int,
    expected_source_sha1: str,
    expected_clean_sha1: str,
    resolutions: list[Stage0ConflictResolution],
    actor: str,
    reason: str,
) -> tuple[RemovalReport, CheckReport]:
    """接受候选；immutable audit 是 WAL，report 是跨文件提交标记。"""
    actor = actor.strip()
    reason = reason.strip()
    if not actor or not reason:
        raise HumanEditProtected('接受 Stage0 合并必须显式给 actor 与 reason')
    store = ArtifactStore(ctx)
    previous = _existing(store)
    if previous is None:
        raise UpstreamMissing('stage0 还没跑，不能接受合并候选')
    old, old_clean = previous

    # A crash may happen after the formal pair is committed or even after the
    # candidate was moved, but before the CLI returned.  Recognize that exact
    # request as an idempotent replay before requiring a live candidate.
    completed = _completed_merge(
        ctx, store, old, old_clean,
        action='merge_accept', candidate_id=candidate_id,
        expected_round=expected_round,
        expected_source_sha1=expected_source_sha1,
        expected_clean_sha1=expected_clean_sha1,
        actor=actor, reason=reason, finalize_candidate=True)
    if completed is not None:
        from novel.stages.stage0.checks import run_check_only
        return completed, run_check_only(ctx)

    candidate = read_merge_candidate(ctx)
    _request_matches_candidate(
        candidate, old, candidate_id=candidate_id,
        expected_round=expected_round,
        expected_source_sha1=expected_source_sha1,
        expected_clean_sha1=expected_clean_sha1)

    from novel.stages.stage0.merge import resolved_text, snapshot_digest
    merged = resolved_text(candidate, resolutions)
    merged_sha1 = prov.content_sha1(merged.encode('utf-8'))
    snapshot = candidate.theirs_snapshot
    if snapshot_digest(snapshot) != snapshot.snapshot_sha256:
        raise ArtifactUnreadable('待接受 candidate 的 theirs_snapshot 指纹校验失败')
    actual_clean_sha1 = prov.content_sha1(old_clean.encode('utf-8'))
    if actual_clean_sha1 not in {old.clean_sha1, merged_sha1}:
        raise ArtifactUnreadable(
            'live clean_text 既不是待合并旧轮，也不是 WAL 指向的新轮；拒绝猜测恢复')

    entry = _merge_audit_entry(
        ctx, old, action='merge_accept', actor=actor, reason=reason,
        after_source_sha1=snapshot.source_sha1,
        after_clean_sha1=merged_sha1, candidate_id=candidate.candidate_id,
    )
    payload = old.model_dump(mode='json')
    payload.update({
        'original_filename': snapshot.original_filename,
        'original_path': snapshot.original_path,
        'code': ctx.code,
        'source_sha1': snapshot.source_sha1,
        'clean_sha1': merged_sha1,
        'source_encoding': snapshot.source_encoding,
        'text_norm': snapshot.text_norm,
        'removed': [item.model_dump(mode='json') for item in snapshot.removed],
        'total_code_points': snapshot.total_code_points,
        'body_code_points': len(merged),
        'warnings': [item.model_dump(mode='json') for item in warnings_for(
            snapshot.total_code_points, len(merged), snapshot.removed)],
        'machine_source_sha1': snapshot.source_sha1,
        'machine_clean_sha1': snapshot.clean_sha1,
        'machine_snapshot_sha256': snapshot.snapshot_sha256,
        'approved': 'approved',
        'approved_at': entry.at,
        'approval_origin': 'human',
        'approved_by': actor,
        'round': old.round + 1,
        'edited_by_human': merged != snapshot.clean_text,
        'audit_seq': entry.seq,
        'audit_head_sha256': entry.entry_sha256,
    })
    report = RemovalReport.model_validate(payload)

    # All recovery inputs remain durable until the report commit marker lands.
    # Each pre-state archive is independently idempotent; only known old/new
    # values may appear at a live path while replaying.
    _ensure_accept_archives(store, old, candidate.ours_text)
    current_snapshot, _ = store.read_own(MachineRemovalSnapshot, SNAPSHOT_REL)
    current_snapshot_sha = snapshot_digest(current_snapshot)
    if current_snapshot_sha != current_snapshot.snapshot_sha256:
        raise ArtifactUnreadable('恢复时 live machine_snapshot 自身指纹校验失败')
    if current_snapshot_sha not in {
            candidate.base_snapshot_sha256, snapshot.snapshot_sha256}:
        raise ArtifactUnreadable(
            'live machine_snapshot 既不是 candidate.base，也不是 candidate.theirs')
    if current_snapshot != snapshot:
        store.write(SNAPSHOT_REL, snapshot)
    _merge_checkpoint('snapshot')

    current_clean = store.read_text(CLEAN_REL)
    if prov.content_sha1(current_clean.encode('utf-8')) not in {
            candidate.ours_clean_sha1, merged_sha1}:
        raise ArtifactUnreadable('恢复时 live clean_text 出现第三种未知字节')
    if current_clean != merged:
        store.write_text(CLEAN_REL, merged)
    _merge_checkpoint('clean')

    current_report, _ = store.read_own(RemovalReport, REPORT_REL)
    if current_report != old:
        raise ArtifactUnreadable('report 提交标记写入前，live report 已不是旧轮')
    store.write(REPORT_REL, report)
    _merge_checkpoint('report')

    committed = _completed_merge(
        ctx, store, report, merged,
        action='merge_accept', candidate_id=candidate_id,
        expected_round=expected_round,
        expected_source_sha1=expected_source_sha1,
        expected_clean_sha1=expected_clean_sha1,
        actor=actor, reason=reason, finalize_candidate=False)
    if committed is None:  # pragma: no cover - guarded by the exact writes above
        raise ArtifactUnreadable('merge_accept 写入后未形成可识别提交态')
    _retire_merge_candidate(ctx, store, candidate.candidate_id)
    from novel.stages.stage0.checks import run_check_only
    return committed, run_check_only(ctx)


def run_merge_reject(
    ctx: RunContext,
    *,
    candidate_id: str,
    expected_round: int,
    expected_source_sha1: str,
    expected_clean_sha1: str,
    actor: str,
    reason: str,
) -> tuple[RemovalReport, CheckReport]:
    """拒绝候选但保留正文；audit WAL 与 report 提交同样可重放。"""
    actor = actor.strip()
    reason = reason.strip()
    if not actor or not reason:
        raise HumanEditProtected('拒绝 Stage0 合并必须显式给 actor 与 reason')
    store = ArtifactStore(ctx)
    previous = _existing(store)
    if previous is None:
        raise UpstreamMissing('stage0 还没跑，不能拒绝合并候选')
    old, clean = previous

    completed = _completed_merge(
        ctx, store, old, clean,
        action='merge_reject', candidate_id=candidate_id,
        expected_round=expected_round,
        expected_source_sha1=expected_source_sha1,
        expected_clean_sha1=expected_clean_sha1,
        actor=actor, reason=reason, finalize_candidate=True)
    if completed is not None:
        from novel.stages.stage0.checks import run_check_only
        return completed, run_check_only(ctx)

    candidate = read_merge_candidate(ctx)
    _request_matches_candidate(
        candidate, old, candidate_id=candidate_id,
        expected_round=expected_round,
        expected_source_sha1=expected_source_sha1,
        expected_clean_sha1=expected_clean_sha1)
    if prov.content_sha1(clean.encode('utf-8')) != old.clean_sha1:
        raise ArtifactUnreadable('reject 恢复时正式 clean_text 与旧报告不一致')
    entry = _merge_audit_entry(
        ctx, old, action='merge_reject', actor=actor, reason=reason,
        after_source_sha1=old.source_sha1,
        after_clean_sha1=old.clean_sha1, candidate_id=candidate.candidate_id,
    )
    report = old.model_copy(update={
        'code': ctx.code,
        'audit_seq': entry.seq,
        'audit_head_sha256': entry.entry_sha256,
    })
    current_report, _ = store.read_own(RemovalReport, REPORT_REL)
    if current_report != old:
        raise ArtifactUnreadable('merge_reject 提交前 live report 已不是旧报告')
    store.write(REPORT_REL, report)
    _merge_checkpoint('report')
    committed = _completed_merge(
        ctx, store, report, clean,
        action='merge_reject', candidate_id=candidate_id,
        expected_round=expected_round,
        expected_source_sha1=expected_source_sha1,
        expected_clean_sha1=expected_clean_sha1,
        actor=actor, reason=reason, finalize_candidate=False)
    if committed is None:  # pragma: no cover - guarded by the exact write above
        raise ArtifactUnreadable('merge_reject 写入后未形成可识别提交态')
    _retire_merge_candidate(ctx, store, candidate.candidate_id)
    from novel.stages.stage0.checks import run_check_only
    return committed, run_check_only(ctx)
