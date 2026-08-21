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
    DropRatioWarning,
    EarlyCutWarning,
    LongMarkerWarning,
    RemovalReport,
    MachineRemovalSnapshot,
    RemovedSegment,
    Stage0ConflictResolution,
    Stage0AuditEntry,
    Stage0EditPlan,
    Stage0MergeCandidate,
    Stage0MergeDecisionPlan,
    Stage0Warning,
    PendingApproval,
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
from novel.core.source import (
    describe_source, resolve_recorded_source, source_ref_for,
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
        out.append(EarlyCutWarning(
            kind='early_cut', body_code_points=body, total_code_points=total))
    if total and removed_points / total > DROP_RATIO_MAX:
        out.append(DropRatioWarning(
            kind='drop_ratio',
            removed_code_points=removed_points, total_code_points=total))
    for segment in removed:
        length = len(marker_core(segment.marker_line))
        if length > NONBODY_MARKER_WARN_CP:
            out.append(LongMarkerWarning(
                kind='long_marker',
                line_no=segment.line_no, marker_core_code_points=length))
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

    # ⚠ 「这条路径是外部语料还是本服务的上传」只在**写产物这一刻**判一次
    #   （`novel/core/source.py::source_ref_for`，唯一落点）；之后它就是产物里
    #   写死的事实，读取端不必再从字符串里推断。
    source_ref = source_ref_for(original_path).model_dump(mode='json')
    from novel.stages.stage0.merge import snapshot_digest
    snapshot_payload = {
        'created_at': datetime.now(timezone.utc).isoformat(),
        'code': code,
        'original_filename': Path(original_path).name,
        'source_ref': source_ref,
        'normalized_source_sha1': decoded.normalized_source_sha1,
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
        source_ref=snapshot.source_ref,
        code=code,
        normalized_source_sha1=decoded.normalized_source_sha1,
        clean_sha1=prov.content_sha1(clean.encode('utf-8')),
        source_encoding=decoded.encoding,
        text_norm=TEXT_NORM,
        removed=removed,
        total_code_points=len(text),
        body_code_points=len(clean),
        warnings=snapshot.warnings,
        machine_normalized_source_sha1=snapshot.normalized_source_sha1,
        machine_clean_sha1=snapshot.clean_sha1,
        machine_snapshot_sha256=snapshot.snapshot_sha256,
        approval=PendingApproval(status='pending'),
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
            old.machine_normalized_source_sha1 == result.machine_snapshot.normalized_source_sha1
            and old.machine_clean_sha1 == result.machine_snapshot.clean_sha1
            and old_snapshot.snapshot_sha256 == result.machine_snapshot.snapshot_sha256
        )
        if same_machine_result:
            kept = RemovalResult(old_clean, old, result.scan, old_snapshot)
            from novel.stages.stage0.checks import run_check_only
            return novel_dir, kept, run_check_only(ctx)

        # 机器结果变化：先分诊再谈合并（2026-08-21 裁决，P0-3）。
        from novel.stages.stage0.merge import has_human_content_divergence, make_candidate
        if result.machine_snapshot.normalized_source_sha1 != old.machine_normalized_source_sha1:
            # 原文变了 = 另一份原文：旧正式稿的 span 坐标系全部失效，diff3 无从谈起。
            # 正式稿一个字不动、仍对旧原文成立；换原文按 R-0.14 是新书的摄入。
            raise SourceConflict(
                'stage0_source_changed：本次摄入的原文与产生当前正式稿的原文不是'
                f'同一份（新 {result.machine_snapshot.normalized_source_sha1[:12]}… / 旧 '
                f'{old.machine_normalized_source_sha1[:12]}…）。三方合并只在同一原文的坐标系内'
                '有意义；正式稿未动、仍对旧原文成立。要换原文等于摄入另一本书：'
                '另起书目录，或显式废弃当前书目录后重跑（R-0.14）')
        if has_human_content_divergence(old):
            # 有人工内容分叉：accept 会把 removed[] 整体换成机器账本，人工圈选
            # 从此无处对账。正式稿不动；出路是人工重选段（服务端按原文减 span 重建）。
            raise HumanEditProtected(
                'stage0_human_edit_protected：当前正式稿含人工内容（任意正文编辑 / '
                '人工圈选过剔除段 / 正式 clean 与机器 clean 指纹不同），机器重跑的新'
                '结果不会自动三方合并。保持现稿即可继续；要采纳机器新结果，走 '
                'stage0.edit --apply-edit-plan 重新圈选——服务端会按「原文减去 '
                'span」精确重建 clean，账本保持准确')

        # 剩下唯一安全区：同一原文、正式稿无人工内容分叉（ours == base）——
        # 此时 theirs 账本可证正确，diff3 退化为「取机器新结果」。
        # 正式正文/报告一个字不动；只写可审阅的 base/ours/theirs 候选。
        candidate = make_candidate(
            expected_round=old.round,
            expected_normalized_source_sha1=old.normalized_source_sha1,
            expected_clean_sha1=old.clean_sha1,
            base_snapshot=old_snapshot,
            theirs_snapshot=result.machine_snapshot,
            base_text=old_snapshot.clean_text,
            ours_text=old_clean,
            theirs_normalized_source_sha1=result.machine_snapshot.normalized_source_sha1,
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

    # 首跑恒 pending（2026-08-21 裁决）：零命中不再自动批准。「零 removed、零 warning、
    # clean == source」恰好是纯数字番外盲区（`docs/schema/stage1.md` §五）的形状——
    # 那个盲区全链唯一的防线就是这道人工闸门，自动放行等于把防线拆了。
    if dry_run:
        return novel_dir, result, None

    with store.history_transaction(SNAPSHOT_REL, CLEAN_REL, REPORT_REL):
        store.write(SNAPSHOT_REL, result.machine_snapshot)
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
    expected_normalized_source_sha1: str | None = None,
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
        'normalized_source_sha1': expected_normalized_source_sha1,
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
        before_normalized_source_sha1=old.normalized_source_sha1, before_clean_sha1=old.clean_sha1,
        after_normalized_source_sha1=old.normalized_source_sha1, after_clean_sha1=next_clean_sha1,
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
        'approval': {'status': 'pending'},
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
        expected_normalized_source_sha1=plan.expected_normalized_source_sha1,
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
    # ⚠ 经唯一读取落点，不自己拼路径：`managed_upload` 那一支的位置**只能**由当前
    #   base 算出来（它跟着 base 走），而这里此前只认记录的绝对路径 ⇒ Output 根
    #   搬过家的上传书会在这里假报「原文没了」。
    source = resolve_recorded_source(old.source_ref, ctx.novel_dir.parent)
    if source is None:
        raise UpstreamMissing(
            f'原始小说已不在记录位置：{describe_source(old.source_ref)}')
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise ArtifactUnreadable(f'读不了原始小说 {source}：{exc!r}') from exc
    try:
        decoded = decode_source_bytes(raw)
    except DecodeProblem as exc:
        raise SourceDecodeFailed(f'{source}: {exc}') from exc
    if decoded.normalized_source_sha1 != old.normalized_source_sha1:
        raise SourceConflict(
            f'原始小说已从 {old.normalized_source_sha1[:10]} 变为 {decoded.normalized_source_sha1[:10]}；'
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

    # provenance 字段一律服务端定（2026-08-21，P0-3 恢复出口收口）：计划里只信
    # span/kind（意图）；origin/行号/标记行/预览是「已验证事实」，由这条唯一入口
    # 落定——客户端伪造 origin='machine'、伪造行号或预览都无效。两条规则：
    #   ① span 与现报告某条**机器**剔除完全一致 ⇒ 原样保留那条（人只是确认了
    #      机器的圈选，账目仍归机器）；
    #   ② 其余（人工新圈/改边）⇒ 按 `s0_removal_ledger` 的同一口径复算：
    #      标记行取 span 内首个非空行、预览取标记行之后/段尾、origin='human'。
    machine_by_span = {
        segment.span: segment for segment in old.removed if segment.origin == 'machine'}
    verified: list[RemovedSegment] = []
    for segment in plan.removed:
        prior = machine_by_span.get(segment.span)
        if prior is not None and prior.code_points == segment.code_points:
            verified.append(prior)
            continue
        lo, hi = segment.span
        piece = decoded.text[lo:hi]
        first_line = next(
            (line.strip() for line in piece.splitlines() if line.strip()), '')
        marker_line = first_line or '人工剔除'
        marker_at = piece.find(marker_line)
        line_break = piece.find('\n', marker_at if marker_at >= 0 else 0)
        after_marker = piece[line_break + 1:] if line_break >= 0 else ''
        verified.append(RemovedSegment(
            kind=segment.kind,
            marker_line=marker_line,
            line_no=decoded.text.count('\n', 0, lo) + 1,
            span=segment.span,
            code_points=hi - lo,
            head_preview=preview_text(after_marker, 80),
            tail_preview=preview_text(piece[-120:], 40),
            origin='human',
        ))

    return _apply_edit(
        ctx,
        reconstructed,
        removed=verified,
        expected_round=plan.expected_round,
        expected_normalized_source_sha1=plan.expected_normalized_source_sha1,
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
    if old.approval.status == 'approved' and old.approval.origin == 'human':
        # 同一决定的重放：幂等返回，不追加审计。
        from novel.stages.stage0.checks import run_check_only
        return old, run_check_only(ctx)
    # 走到这里的 approved 只可能是历史自动批（`auto_zero_hit`，2026-08-21 停产）：
    # 不当 no-op 返回——那会让「显示已批准」与「不具执行资格」永久并存。
    # 人工重批 = 在原 `auto_approve` 审计条目之后追加一条 `approve`、origin 改 human；
    # 正文没动，round 不变（`novel/stages/stage0/eligibility.py`）。
    from novel.stages.stage0.merge import append_audit, verify_audit_chain
    verify_audit_chain(ctx, seq=old.audit_seq, head=old.audit_head_sha256)
    at = approved_at or datetime.now(timezone.utc).isoformat()
    audit_rel = STAGE0_AUDIT_ENTRY.format(n=old.audit_seq + 1)
    with store.history_transaction(REPORT_REL, audit_rel):
        entry = append_audit(
            ctx, seq=old.audit_seq + 1, previous_sha256=old.audit_head_sha256,
            action='approve', actor=actor.strip(), reason=reason.strip(),
            before_normalized_source_sha1=old.normalized_source_sha1, before_clean_sha1=old.clean_sha1,
            after_normalized_source_sha1=old.normalized_source_sha1, after_clean_sha1=old.clean_sha1,
            at=at,
        )
        payload = old.model_dump(mode='json')
        payload.update({
            'code': ctx.code,
            'approval': {'status': 'approved', 'origin': 'human',
                         'at': at, 'by': actor.strip()},
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
    expected_normalized_source_sha1: str,
    expected_clean_sha1: str,
) -> None:
    stale: list[str] = []
    checks = (
        ('candidate_id', candidate_id, candidate.candidate_id),
        ('round', expected_round, old.round),
        ('normalized_source_sha1', expected_normalized_source_sha1, old.normalized_source_sha1),
        ('clean_sha1', expected_clean_sha1, old.clean_sha1),
    )
    for name, expected, current in checks:
        if expected != current:
            stale.append(f'{name}: 请求={expected!r}, 当前={current!r}')
    if candidate.expected_round != old.round \
            or candidate.expected_normalized_source_sha1 != old.normalized_source_sha1 \
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
    after_normalized_source_sha1: str,
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
        before_normalized_source_sha1=old.normalized_source_sha1, before_clean_sha1=old.clean_sha1,
        after_normalized_source_sha1=after_normalized_source_sha1, after_clean_sha1=after_clean_sha1,
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
    expected_normalized_source_sha1: str,
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
    if (entry.before_normalized_source_sha1, entry.before_clean_sha1) != (
            expected_normalized_source_sha1, expected_clean_sha1):
        raise HumanEditProtected('已提交 merge 的 before 哈希与本次乐观锁不同')
    wanted_round = expected_round + 1 if action == 'merge_accept' else expected_round
    if report.round != wanted_round:
        raise ArtifactUnreadable(
            f'已提交 {action} 的 report.round={report.round}，预期 {wanted_round}')
    if (report.normalized_source_sha1, report.clean_sha1) != (
            entry.after_normalized_source_sha1, entry.after_clean_sha1):
        raise ArtifactUnreadable('merge 报告的 after 哈希与审计条目不一致')
    if prov.content_sha1(clean.encode('utf-8')) != report.clean_sha1:
        raise ArtifactUnreadable('merge 报告已提交，但 clean_text 与报告 clean_sha1 不一致')
    snapshot, _ = store.read_own(MachineRemovalSnapshot, SNAPSHOT_REL)
    if snapshot_digest(snapshot) != snapshot.snapshot_sha256:
        raise ArtifactUnreadable('当前 machine_snapshot 自身指纹校验失败')
    if (
        report.machine_snapshot_sha256 != snapshot.snapshot_sha256
        or report.machine_normalized_source_sha1 != snapshot.normalized_source_sha1
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
    expected_normalized_source_sha1: str,
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
        expected_normalized_source_sha1=expected_normalized_source_sha1,
        expected_clean_sha1=expected_clean_sha1,
        actor=actor, reason=reason, finalize_candidate=True)
    if completed is not None:
        from novel.stages.stage0.checks import run_check_only
        return completed, run_check_only(ctx)

    candidate = read_merge_candidate(ctx)
    _request_matches_candidate(
        candidate, old, candidate_id=candidate_id,
        expected_round=expected_round,
        expected_normalized_source_sha1=expected_normalized_source_sha1,
        expected_clean_sha1=expected_clean_sha1)

    # 接受端二次防御（2026-08-21，P0-3）：生成端不产危险 candidate，不等于这里
    # 收不到——升级前已落盘的、WAL 崩溃恢复出来的、手工构造的都会走到这一步。
    # 唯一例外：盘上已有这条 accept 的不可覆盖 WAL 意图行（升级前崩溃在接受途中）。
    # 意图行本身就是决定时刻；拒绝它会让审计链永远不闭合、书永久卡死，
    # 而完成重放只是把已经做出的决定落完——已提交的合并同理，重放只认账不翻案。
    from novel.stages.stage0.merge import (
        has_human_content_divergence, pending_merge_audit,
        resolved_text, snapshot_digest,
    )
    if has_human_content_divergence(old):
        pending = pending_merge_audit(
            ctx, seq=old.audit_seq, head=old.audit_head_sha256)
        decision_already_durable = (
            pending is not None
            and pending.action == 'merge_accept'
            and pending.candidate_id == candidate.candidate_id)
        if not decision_already_durable:
            _retire_merge_candidate(ctx, store, candidate.candidate_id)
            raise HumanEditProtected(
                'stage0_human_edit_protected：这份 candidate 的基线（当前正式稿）'
                '含人工内容分叉，接受合并会把 removed[] 整体换成机器账本、人工圈选'
                '从此无处对账。危险 candidate 已撤销归档；出路与生成端一致：保持'
                '现稿，或走 stage0.edit --apply-edit-plan 重新圈选')

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
        after_normalized_source_sha1=snapshot.normalized_source_sha1,
        after_clean_sha1=merged_sha1, candidate_id=candidate.candidate_id,
    )
    payload = old.model_dump(mode='json')
    payload.update({
        'original_filename': snapshot.original_filename,
        'source_ref': snapshot.source_ref.model_dump(mode='json'),
        'code': ctx.code,
        'normalized_source_sha1': snapshot.normalized_source_sha1,
        'clean_sha1': merged_sha1,
        'source_encoding': snapshot.source_encoding,
        'text_norm': snapshot.text_norm,
        'removed': [item.model_dump(mode='json') for item in snapshot.removed],
        'total_code_points': snapshot.total_code_points,
        'body_code_points': len(merged),
        'warnings': [item.model_dump(mode='json') for item in warnings_for(
            snapshot.total_code_points, len(merged), snapshot.removed)],
        'machine_normalized_source_sha1': snapshot.normalized_source_sha1,
        'machine_clean_sha1': snapshot.clean_sha1,
        'machine_snapshot_sha256': snapshot.snapshot_sha256,
        'approval': {'status': 'approved', 'origin': 'human',
                     'at': entry.at, 'by': actor},
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
        expected_normalized_source_sha1=expected_normalized_source_sha1,
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
    expected_normalized_source_sha1: str,
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
        expected_normalized_source_sha1=expected_normalized_source_sha1,
        expected_clean_sha1=expected_clean_sha1,
        actor=actor, reason=reason, finalize_candidate=True)
    if completed is not None:
        from novel.stages.stage0.checks import run_check_only
        return completed, run_check_only(ctx)

    candidate = read_merge_candidate(ctx)
    _request_matches_candidate(
        candidate, old, candidate_id=candidate_id,
        expected_round=expected_round,
        expected_normalized_source_sha1=expected_normalized_source_sha1,
        expected_clean_sha1=expected_clean_sha1)
    if prov.content_sha1(clean.encode('utf-8')) != old.clean_sha1:
        raise ArtifactUnreadable('reject 恢复时正式 clean_text 与旧报告不一致')
    entry = _merge_audit_entry(
        ctx, old, action='merge_reject', actor=actor, reason=reason,
        after_normalized_source_sha1=old.normalized_source_sha1,
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
        expected_normalized_source_sha1=expected_normalized_source_sha1,
        expected_clean_sha1=expected_clean_sha1,
        actor=actor, reason=reason, finalize_candidate=False)
    if committed is None:  # pragma: no cover - guarded by the exact write above
        raise ArtifactUnreadable('merge_reject 写入后未形成可识别提交态')
    _retire_merge_candidate(ctx, store, candidate.candidate_id)
    from novel.stages.stage0.checks import run_check_only
    return committed, run_check_only(ctx)
