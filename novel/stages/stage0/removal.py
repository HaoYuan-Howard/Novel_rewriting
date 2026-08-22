#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''stage0 非正文剔除与人工动作。零 LLM。

机器路径只做三件事：无损解码、BOM/换行归一、从第一个非正文标记起删除。其余字符
一个不动。人工编辑是另一条显式入口，会在链上落一条新 revision、把审批退回 pending；
机器重跑不会静默覆盖它。

⚠ 2026-08-22 阶段 C：本模块不再直接改那两份 live 产物。所有写路径的形状都是
「算出一条新 `Stage0Revision` → `revisions.commit()`」，而 `commit` 只写一份文件
（`stage0/revisions.json`），随后把 `current` 物化成 `clean_text.txt` +
`removal_report.json`。⇒ 归档、WAL、三方合并、崩溃重放状态机全部下线，
细节与理由见 `novel/stages/stage0/revisions.py` 与 `contracts/schema/stage0.py` 模块头。
'''
from __future__ import annotations

from contracts.artifacts import STAGE0_CLEAN_TEXT, STAGE0_REMOVAL_REPORT

import codecs
import re
from dataclasses import dataclass
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
    ApprovedApproval,
    DropRatioWarning,
    EarlyCutWarning,
    LongMarkerWarning,
    PendingApproval,
    RemovalReport,
    RemovedSegment,
    RequestedRemoval,
    Stage0CandidatePlan,
    Stage0Current,
    Stage0EditPlan,
    Stage0Revision,
    Stage0RevisionContent,
    Stage0Revisions,
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
from novel.stages.stage0 import revisions as rev
from novel.store import ArtifactStore

CLEAN_REL = STAGE0_CLEAN_TEXT
REPORT_REL = STAGE0_REMOVAL_REPORT
REVISIONS_REL = rev.REVISIONS_REL


@dataclass(frozen=True)
class RemovalResult:
    """一次 stage0 动作之后**盘上当前那一版**长什么样（`--dry-run` 时是「将会是」）。"""

    clean_text: str
    report: RemovalReport
    scan: ScanResult

    @property
    def body_ratio(self) -> float:
        return self.report.body_code_points / self.report.total_code_points


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


def machine_content(
    decoded: DecodedSource, *, original_path: str,
) -> tuple[Stage0RevisionContent, ScanResult]:
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
    content = Stage0RevisionContent(
        original_filename=Path(original_path).name,
        source_ref=source_ref_for(original_path),
        normalized_source_sha1=decoded.normalized_source_sha1,
        source_encoding=decoded.encoding,
        text_norm=TEXT_NORM,
        total_code_points=len(text),
        clean_text=clean,
        clean_sha1=prov.content_sha1(clean.encode('utf-8')),
        body_code_points=len(clean),
        removed=removed,
        warnings=warnings_for(len(text), len(clean), removed),
    )
    return content, scan


def _provisional(content: Stage0RevisionContent, *, code: str) -> RemovalResult:
    """把一条还没落盘的内容投影成报告（`--dry-run` 与首跑预览共用）。"""
    revision = rev.make_revision(
        parent_revision_id=None, origin='machine', actor=None, reason=None,
        code=code, content=content)
    log = Stage0Revisions(
        revisions=[revision],
        current=Stage0Current(
            revision_id=revision.revision_id,
            approval=PendingApproval(status='pending')),
        candidate_revision_id=None,
    )
    return RemovalResult(content.clean_text, rev.report_of(log), scan_text(content.clean_text))


def remove_decoded(decoded: DecodedSource, *, original_path: str,
                   code: str) -> RemovalResult:
    content, scan = machine_content(decoded, original_path=original_path)
    result = _provisional(content, code=code)
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
) -> tuple[Stage0RevisionContent, ScanResult]:
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
    content, scan = inspect_content(source_path)
    return RemovalResult(content.clean_text, _provisional(content, code=code).report, scan)


def _context(novel_dir: Path, node: str) -> RunContext:
    return RunContext(
        novel_dir=novel_dir,
        node=node,
        paid=False,
        code=prov.code_version(),
        cache=CachePolicy.default(),
        ledger=CostLedger(path=None),
    )


def _require_stage0_fresh(store: ArtifactStore) -> None:
    """Refuse committing an approval against source/clean bytes that already moved.

    ⚠ 它是**前置条件**（2026-08-22 阶段 C）：链是权威、审批只改链上一格，
    先判后写就够了，不必再拿一个跨文件事务去回滚一次已经写下的批准。
    """
    freshness = store.freshness(REPORT_REL)
    if freshness.state == 'fresh':
        return
    edges = ', '.join(
        f'{edge.key}:{edge.reason}' for edge in freshness.edges) or freshness.reason
    raise ProvenanceRefused(
        'Stage0 approval refused: the materialized report is not current against '
        f'the source/clean decision surface: {freshness.state}/{freshness.reason} '
        f'({edges}). Nothing was written.')


def _result_of(log: Stage0Revisions) -> RemovalResult:
    content = rev.current_revision(log).content
    return RemovalResult(content.clean_text, rev.report_of(log), scan_text(content.clean_text))


def _checked(ctx: RunContext, log: Stage0Revisions) -> tuple[RemovalResult, CheckReport]:
    from novel.stages.stage0.checks import run_check_only
    return _result_of(log), run_check_only(ctx)


def run_source(source_path: str | Path, base: str | Path, *, force: bool = False,
               dry_run: bool = False, expected_sid: str | None = None) \
        -> tuple[Path, RemovalResult, CheckReport | None]:
    '''真实摄入路径：源文件推导目录，保护已有人工稿，在链上落一条机器 revision。'''
    decoded_content, scan = inspect_content(source_path)
    machine = RemovalResult(
        decoded_content.clean_text,
        _provisional(decoded_content, code=prov.code_version()).report, scan)
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
    log = rev.ensure_materialized(store)

    # 首跑恒 pending（2026-08-21 裁决）：零命中不再自动批准。「零 removed、零 warning、
    # clean == source」恰好是纯数字番外盲区（`docs/schema/stage1.md` §五）的形状——
    # 那个盲区全链唯一的防线就是这道人工闸门，自动放行等于把防线拆了。
    if log is None:
        if dry_run:
            return novel_dir, machine, None
        root = rev.make_revision(
            parent_revision_id=None, origin='machine', actor=None, reason=None,
            code=ctx.code, content=decoded_content)
        fresh = Stage0Revisions(
            revisions=[root],
            current=Stage0Current(
                revision_id=root.revision_id,
                approval=PendingApproval(status='pending')),
            candidate_revision_id=None,
        )
        rev.commit(store, fresh)
        result, report = _checked(ctx, fresh)
        return novel_dir, result, report

    current = rev.current_revision(log)
    if decoded_content.normalized_source_sha1 != current.content.normalized_source_sha1:
        # 原文变了 = 另一份原文：正式稿的 span 坐标系全部失效。正式稿一个字不动、
        # 仍对旧原文成立；换原文按 R-0.14 是新书的摄入。
        raise SourceConflict(
            'stage0_source_changed：本次摄入的原文与产生当前正式稿的原文不是'
            f'同一份（新 {decoded_content.normalized_source_sha1[:12]}… / 旧 '
            f'{current.content.normalized_source_sha1[:12]}…）。正式稿未动、'
            '仍对旧原文成立。要换原文等于摄入另一本书：另起书目录，'
            '或显式废弃当前书目录后重跑（R-0.14）')

    # 机器结果与当前稿逐字相同 ⇒ 无事可做。⚠ 比的是**整团内容**，不是两个哈希：
    # 聚合根是 `{原文身份, 正文, 剔除账本, 告警}`，只比正文会漏掉账本的差别。
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

    if current.origin == 'machine' and log.current.approval.status == 'pending':
        # 当前稿是机器算的、还没人看过 ⇒ 换成新结果**不丢任何东西**：
        # 没有人工内容可覆盖，也没有「人看过并批准了这一版」可绕过。
        published = rev.published(log, proposal, candidate_revision_id=None)
        rev.commit(store, published)
        result, report = _checked(ctx, published)
        return novel_dir, result, report

    # 剩下的两种都要人来裁：当前稿含人工内容（换掉就是覆盖人的作业），
    # 或者当前稿已经被人批准过（换掉就是绕过那道闸门）。
    # ⇒ 正式稿一个字不动，新结果只挂成候选，等三选一。
    entries = list(log.revisions)
    if all(item.revision_id != proposal.revision_id for item in entries):
        entries.append(proposal)
    pending = Stage0Revisions(
        revisions=entries,
        current=log.current,
        candidate_revision_id=proposal.revision_id,
        dismissed=log.dismissed,
    )
    rev.commit(store, pending)
    result, report = _checked(ctx, pending)
    return novel_dir, result, report


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


def _load(ctx: RunContext) -> tuple[ArtifactStore, Stage0Revisions]:
    store = ArtifactStore(ctx)
    log = rev.ensure_materialized(store)
    if log is None:
        raise UpstreamMissing('stage0 还没跑，盘上没有可操作的 revision 链')
    return store, log


def _apply_edit(
    ctx: RunContext,
    text: str,
    *,
    removed: Sequence[RemovedSegment] | None = None,
    expected_revision_id: str | None = None,
    expected_normalized_source_sha1: str | None = None,
    expected_total_code_points: int | None = None,
    actor: str,
    reason: str,
) -> tuple[RemovalReport, CheckReport]:
    '''在链上落一条人工 revision；乐观锁只在审校台计划路径启用。'''
    from novel.stages.stage0.checks import run_check_only
    store, log = _load(ctx)
    current = rev.current_revision(log)
    if not actor.strip() or not reason.strip():
        raise HumanEditProtected('stage0 人工编辑必须显式给 actor 与 reason')
    expected = {
        'revision_id': (expected_revision_id, current.revision_id),
        'normalized_source_sha1': (expected_normalized_source_sha1,
                                   current.content.normalized_source_sha1),
        'total_code_points': (expected_total_code_points,
                              current.content.total_code_points),
    }
    stale = [f'{name}: 页面={want!r}, 当前={have!r}'
             for name, (want, have) in expected.items()
             if want is not None and want != have]
    if stale:
        raise HumanEditProtected(
            '这份 stage0 编辑计划基于旧页面，拒绝覆盖当前版本：' + '；'.join(stale))

    next_removed = list(removed) if removed is not None else list(current.content.removed)
    if text == current.content.clean_text and next_removed == current.content.removed:
        return rev.report_of(log), run_check_only(ctx)

    before = current.content
    content = Stage0RevisionContent(
        original_filename=before.original_filename,
        source_ref=before.source_ref,
        normalized_source_sha1=before.normalized_source_sha1,
        source_encoding=before.source_encoding,
        text_norm=before.text_norm,
        total_code_points=before.total_code_points,
        clean_text=text,
        clean_sha1=prov.content_sha1(text.encode('utf-8')),
        body_code_points=len(text),
        removed=next_removed,
        warnings=warnings_for(before.total_code_points, len(text), next_removed),
    )
    revision = rev.make_revision(
        parent_revision_id=current.revision_id, origin='human_edit',
        actor=actor.strip(), reason=reason.strip(), code=ctx.code, content=content)
    # ⚠ 编辑即已对候选做出决定：人是在**当前稿**上继续动手的，那份候选相对的
    #   基线已经过去了。清指针即可，不记进 `dismissed`——下次机器重跑会挂在新的
    #   当前稿下、得到新的 id，不存在「被否掉的候选复活」这回事。
    published = rev.published(log, revision, candidate_revision_id=None)
    rev.commit(store, published)
    return rev.report_of(published), run_check_only(ctx)


def run_edit(ctx: RunContext, edit_path: str | Path, *,
             actor: str, reason: str,
             removed: Sequence[RemovedSegment] | None = None) -> tuple[RemovalReport, CheckReport]:
    '''落一版人工 clean_text；上一版留在链上不可变。'''
    return _apply_edit(
        ctx, _human_text(edit_path), removed=removed, actor=actor, reason=reason)


def _read_plan(model, plan_path: str | Path, what: str):
    path = Path(plan_path).resolve()
    if not path.is_file():
        raise UpstreamMissing(f'{what}不在盘上：{path}')
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ArtifactUnreadable(f'读不了{what} {path}：{exc!r}') from exc
    if raw.startswith(codecs.BOM_UTF8):
        raise ByteFormRefused(f'{what}带 UTF-8 BOM；内部 JSON 必须是无 BOM UTF-8')
    try:
        return model.model_validate_json(raw)
    except (ValidationError, UnicodeDecodeError) as exc:
        raise ArtifactUnreadable(f'{what}不是合法契约：{exc}') from exc


def run_edit_plan(
    ctx: RunContext,
    plan_path: str | Path,
) -> tuple[RemovalReport, CheckReport]:
    '''应用 S-00 选择计划；原文、区间、重建正文与乐观锁全部在子进程内复核。'''
    plan: Stage0EditPlan = _read_plan(Stage0EditPlan, plan_path, 'stage0 编辑计划')
    _store, log = _load(ctx)
    current = rev.current_revision(log)
    # ⚠ 经唯一读取落点，不自己拼路径：`managed_upload` 那一支的位置**只能**由当前
    #   base 算出来（它跟着 base 走），而这里此前只认记录的绝对路径 ⇒ Output 根
    #   搬过家的上传书会在这里假报「原文没了」。
    source = resolve_recorded_source(current.content.source_ref, ctx.novel_dir.parent)
    if source is None:
        raise UpstreamMissing(
            f'原始小说已不在记录位置：{describe_source(current.content.source_ref)}')
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise ArtifactUnreadable(f'读不了原始小说 {source}：{exc!r}') from exc
    try:
        decoded = decode_source_bytes(raw)
    except DecodeProblem as exc:
        raise SourceDecodeFailed(f'{source}: {exc}') from exc
    if decoded.normalized_source_sha1 != current.content.normalized_source_sha1:
        raise SourceConflict(
            f'原始小说已从 {current.content.normalized_source_sha1[:10]} 变为 '
            f'{decoded.normalized_source_sha1[:10]}；'
            '页面上的区间全部失效，请重新摄入并审阅')
    if len(decoded.text) != current.content.total_code_points:
        raise SourceConflict(
            f'原始小说当前长度 {len(decoded.text)} 与当前版本 '
            f'{current.content.total_code_points} 对不上')

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
        reconstructed,
        removed=verified_removals(plan.removed, decoded.text, current.content.removed),
        expected_revision_id=plan.expected_revision_id,
        expected_normalized_source_sha1=plan.expected_normalized_source_sha1,
        expected_total_code_points=plan.expected_total_code_points,
        actor=plan.actor,
        reason=plan.reason,
    )


def verified_removals(
    requested: Sequence[RequestedRemoval],
    source_text: str,
    prior: Sequence[RemovedSegment],
) -> list[RemovedSegment]:
    """把**意图**（span + kind）落定成**已验证事实**。唯一落点。

    provenance 字段一律服务端定（2026-08-21，P0-3 恢复出口收口；2026-08-22 阶段 C
    连类型也分了家，见 `RequestedRemoval`）：计划里只信 span/kind；origin/行号/
    标记行/预览是「已验证事实」，由这条唯一入口落定。两条规则：
      ① span 与当前版本某条**机器**剔除完全一致 ⇒ 原样保留那条（人只是确认了
         机器的圈选，账目仍归机器）；
      ② 其余（人工新圈/改边）⇒ 按 `s0_removal_ledger` 的同一口径复算：
         标记行取 span 内首个非空行、预览取标记行之后/段尾、origin='human'。
    """
    machine_by_span = {
        segment.span: segment for segment in prior if segment.origin == 'machine'}
    verified: list[RemovedSegment] = []
    for segment in requested:
        lo, hi = segment.span
        earlier = machine_by_span.get(segment.span)
        if earlier is not None and earlier.code_points == hi - lo:
            verified.append(earlier)
            continue
        piece = source_text[lo:hi]
        first_line = next(
            (line.strip() for line in piece.splitlines() if line.strip()), '')
        marker_line = first_line or '人工剔除'
        marker_at = piece.find(marker_line)
        line_break = piece.find('\n', marker_at if marker_at >= 0 else 0)
        after_marker = piece[line_break + 1:] if line_break >= 0 else ''
        verified.append(RemovedSegment(
            kind=segment.kind,
            marker_line=marker_line,
            line_no=source_text.count('\n', 0, lo) + 1,
            span=segment.span,
            code_points=hi - lo,
            head_preview=preview_text(after_marker, 80),
            tail_preview=preview_text(piece[-120:], 40),
            origin='human',
        ))
    return verified


def run_approve(ctx: RunContext, *, actor: str, reason: str,
                approved_at: str | None = None) \
        -> tuple[RemovalReport, CheckReport]:
    '''批准当前这一版。批准不改正文、不产生新 revision。'''
    from novel.stages.stage0.checks import run_check_only
    store, log = _load(ctx)
    if not actor.strip() or not reason.strip():
        raise HumanEditProtected('stage0 人工批准必须显式给 actor 与 reason')
    approval = log.current.approval
    if approval.status == 'approved' and approval.origin == 'human':
        # 同一决定的重放：幂等返回。
        return rev.report_of(log), run_check_only(ctx)
    # ⚠ 前置条件：批准的是**盘上这一刻的** source/clean 决策面。
    _require_stage0_fresh(store)
    approved = Stage0Revisions(
        revisions=log.revisions,
        current=Stage0Current(
            revision_id=log.current.revision_id,
            approval=ApprovedApproval(
                status='approved', origin='human',
                at=approved_at or rev.now_iso(),
                by=actor.strip(), reason=reason.strip())),
        candidate_revision_id=log.candidate_revision_id,
        dismissed=log.dismissed,
    )
    rev.commit(store, approved)
    try:
        # ⚠ 后置条件仍在：原文可能恰好在前置检查与提交之间被换掉。回滚只是**再写一次
        #   那一份文件**（链是权威、一次提交只写一份）——旧实现要为此维持一个跨文件
        #   history_transaction，现在一行原子写就回到批准前的状态。
        _require_stage0_fresh(store)
    except ProvenanceRefused:
        rev.commit(store, log)
        raise
    return rev.report_of(approved), run_check_only(ctx)


def read_candidate(ctx: RunContext) -> Stage0Revision:
    """待裁决的那条机器候选；没有就明说，不返回一个空壳。"""
    log = rev.read(ArtifactStore(ctx))
    if log is None or log.candidate_revision_id is None:
        raise UpstreamMissing('stage0 当前没有待裁决的机器候选')
    return rev.by_id(log, log.candidate_revision_id)


def run_candidate_plan(
    ctx: RunContext, plan_path: str | Path,
) -> tuple[RemovalReport, CheckReport]:
    """三选一里的两个写动作：采纳候选 / 否掉候选。

    ⚠ 「采纳后再编辑」不在这里：那是 adopt 之后照常走 `stage0.edit`，
    两步各自已经是一次原子提交。
    """
    from novel.stages.stage0.checks import run_check_only
    plan: Stage0CandidatePlan = _read_plan(
        Stage0CandidatePlan, plan_path, 'stage0 候选裁决')
    store, log = _load(ctx)
    current = rev.current_revision(log)

    replayed = _completed_decision(log, plan)
    if replayed is not None:
        return rev.report_of(replayed), run_check_only(ctx)

    if log.candidate_revision_id != plan.candidate_revision_id:
        raise HumanEditProtected(
            f'盘上待裁决的候选是 {log.candidate_revision_id}，'
            f'请求要裁决 {plan.candidate_revision_id}；页面已过期，请刷新')
    if plan.expected_revision_id != current.revision_id:
        raise HumanEditProtected(
            f'Stage0 候选裁决的乐观锁失效：页面当前稿={plan.expected_revision_id}，'
            f'盘上={current.revision_id}')

    candidate = rev.by_id(log, plan.candidate_revision_id)
    if plan.action == 'dismiss':
        from contracts.schema.stage0 import Stage0DismissedCandidate
        kept = Stage0Revisions(
            revisions=log.revisions,
            current=log.current,
            candidate_revision_id=None,
            dismissed=[*log.dismissed, Stage0DismissedCandidate(
                revision_id=candidate.revision_id, at=rev.now_iso(),
                by=plan.actor.strip(), reason=plan.reason.strip())],
        )
        rev.commit(store, kept)
        return rev.report_of(kept), run_check_only(ctx)

    # 采纳：内容逐字取候选，但它是**人做的一次状态转移** ⇒ 单独成一条 revision，
    # 记下谁在何时以什么理由换掉了正文（审计链折叠进来之后这是唯一落点）。
    adopted = rev.make_revision(
        parent_revision_id=current.revision_id, origin='human_adopt',
        actor=plan.actor.strip(), reason=plan.reason.strip(), code=ctx.code,
        content=candidate.content)
    published = rev.published(log, adopted, candidate_revision_id=None)
    rev.commit(store, published)
    return rev.report_of(published), run_check_only(ctx)


def _completed_decision(
    log: Stage0Revisions, plan: Stage0CandidatePlan,
) -> Stage0Revisions | None:
    """认出一次已经落完的裁决（进程崩在 commit 之后、CLI 返回之前）。

    ⚠ 这是全部的「崩溃恢复」——两个分支，不是一台状态机：链是权威且一次提交只写
    一份文件，所以「做过了没有」永远可以直接从链上读出来，不需要 WAL 意图行。
    """
    if all(item.revision_id != plan.candidate_revision_id for item in log.revisions):
        return None
    if plan.action == 'dismiss':
        if any(item.revision_id == plan.candidate_revision_id
               and item.by == plan.actor.strip() and item.reason == plan.reason.strip()
               for item in log.dismissed):
            return log
        return None
    current = rev.current_revision(log)
    if (current.origin == 'human_adopt'
            and current.parent_revision_id == plan.expected_revision_id
            and current.actor == plan.actor.strip()
            and current.reason == plan.reason.strip()
            and current.content == rev.by_id(log, plan.candidate_revision_id).content):
        return log
    return None
