#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''S-00 / S-03 的连续文本视图。纯读、零判定、零产物写入。

Stage 0 的坐标系恒是归一化原始文件；Stage 1 的坐标系恒是 clean_text。前端只渲染
服务端已经切好的 text，不拿 Python 码点偏移去切 JavaScript UTF-16 字符串。
'''
from __future__ import annotations

import os
import re
from bisect import bisect_left
from dataclasses import dataclass

from contracts.schema.api import (
    HookCopyView,
    Stage0EditRequest,
    Stage0Segment,
    Stage0Segments,
    Stage1ChapterRow,
    Stage1Chapters,
    Stage1Segment,
    Stage1Segments,
)
from contracts.schema.stage0 import (
    RemovalReport, RemovedSegment, Stage0EditPlan, Stage0MergeCandidate,
)
from contracts.schema.stage1 import HookCopy, MainText, PartitionReport
from novel import paths, prov
from novel.core.ingest import (
    DecodeProblem, ScanResult, decode_source_bytes, nonbody_kind, scan_text,
)
from novel.core.source import resolve_recorded_source
from novel.errors import (
    ApprovalRequired,
    ArtifactUnreadable,
    HumanEditProtected,
    PartitionChapterMismatch,
    PartitionInputMismatch,
    SourceConflict,
)
from novel.orchestrator.state import readonly_ctx
from novel.stages.stage0 import removal
from novel.stages.stage1 import partition
from novel.store import ArtifactStore

STAGE0_NODE = 'stage0.remove'


def stage0_merge_candidate(
    base: str | os.PathLike[str], sid: str,
) -> Stage0MergeCandidate:
    store = _store(base, sid, STAGE0_NODE)
    candidate, _ = store.read_own(Stage0MergeCandidate, removal.CANDIDATE_REL)
    return candidate
STAGE1_NODE = 'stage1.partition'
_PARAGRAPH_BREAK = re.compile(r'\n[ \t　]*\n')


def _store(base: str | os.PathLike[str], sid: str, node: str) -> ArtifactStore:
    novel_dir = paths.resolve_novel_dir(base, sid)
    return ArtifactStore(readonly_ctx(novel_dir, node, prov.code_version()))


def stage0_report(base: str | os.PathLike[str], sid: str) -> RemovalReport:
    store = _store(base, sid, STAGE0_NODE)
    return store.read_own(RemovalReport, removal.REPORT_REL)[0]


def _original(report: RemovalReport,
              base: str | os.PathLike[str]) -> tuple[str | None, str | None]:
    """审阅屏要的那份原文。**取不到就明说不可用，绝不从别处变出一份。**

    ⚠ `original_path` 指向书目录之外、不是产物，才可以直接摸盘（产物一律经 store）。
    ⚠ 记录的位置读不到时**只做一种回落**：界面上传的原文按内容地址存在
      `<base>/_service/uploads/` 底下，Output 根搬家会让记下来的绝对路径断掉，
      而文件其实没动——`resolve_recorded_source` 用当前 base 认回来，且要求它
      自证内容地址。外部语料被删则**没有**回落，那是 `R-0.2` 的意思：
      产物目录里没有第二份原文副本，取不到就是取不到。
    """
    source = resolve_recorded_source(report.original_path, base)
    if source is None:
        return None, f'原始文件已不在 original_path：{report.original_path}'
    try:
        raw = source.read_bytes()
    except OSError as exc:
        return None, f'原始文件读不了：{exc!r}'
    try:
        decoded = decode_source_bytes(raw)
    except DecodeProblem as exc:
        return None, f'原始文件已无法按摄入契约解码：{exc}'
    if decoded.source_sha1 != report.source_sha1:
        return (
            None,
            f'原始文件内容已变化：报告 {report.source_sha1[:10]}，'
            f'当前 {decoded.source_sha1[:10]}',
        )
    if len(decoded.text) != report.total_code_points:
        return (
            None,
            f'原始文件长度已变化：报告 {report.total_code_points}，当前 {len(decoded.text)}',
        )
    return decoded.text, None


def _line_end(text: str, start: int) -> int:
    newline = text.find('\n', start)
    return len(text) if newline < 0 else newline + 1


def _gap_segments(
    text: str,
    lo: int,
    hi: int,
    scan: ScanResult,
) -> list[Stage0Segment]:
    if lo >= hi:
        return []
    boundaries = {lo, hi}
    boundaries.update(
        marker.line_start
        for marker in (*scan.chapters, *scan.nonbody)
        if lo < marker.line_start < hi
    )
    if scan.end_marker is not None and lo <= scan.end_marker < hi:
        boundaries.add(scan.end_marker)
        boundaries.add(min(hi, _line_end(text, scan.end_marker)))
    for match in _PARAGRAPH_BREAK.finditer(text, lo, hi):
        if lo < match.end() < hi:
            boundaries.add(match.end())
    points = sorted(boundaries)
    out: list[Stage0Segment] = []
    for start, end in zip(points, points[1:]):
        if start == end:
            continue
        kind = 'end_marker' if scan.end_marker == start else 'keep'
        value = text[start:end]
        out.append(Stage0Segment(
            id=f's0:{start}:{end}',
            kind=kind,
            start=start,
            end=end,
            chars=end - start,
            flagged=False,
            text=value,
        ))
    return out


def _stage0_segments(report: RemovalReport, text: str, clean: str) -> list[Stage0Segment]:
    actual_clean = prov.content_sha1(clean.encode('utf-8'))
    if actual_clean != report.clean_sha1:
        raise HumanEditProtected(
            f'clean_text 实际 sha1={actual_clean}，报告登记的是 {report.clean_sha1}；'
            '不能拿两轮内容拼成审校页面')

    out: list[Stage0Segment] = []
    cursor = 0
    reconstructed: list[str] = []
    scan = scan_text(text)
    for removed in report.removed:
        lo, hi = removed.span
        if lo < cursor or hi > len(text):
            raise ArtifactUnreadable(
                f'removed span={removed.span} 与原文长度 {len(text)} 不相容')
        out.extend(_gap_segments(text, cursor, lo, scan))
        reconstructed.append(text[cursor:lo])
        value = text[lo:hi]
        if len(value) != removed.code_points:
            raise ArtifactUnreadable(
                f'removed span={removed.span} 实际长 {len(value)}，报告记 {removed.code_points}')
        out.append(Stage0Segment(
            id=f's0:{lo}:{hi}',
            kind='remove',
            start=lo,
            end=hi,
            chars=hi - lo,
            flagged=True,
            text=value,
            origin=removed.origin,
            removed_kind=removed.kind,
            marker_line=removed.marker_line,
        ))
        cursor = hi
    out.extend(_gap_segments(text, cursor, len(text), scan))
    reconstructed.append(text[cursor:])
    if ''.join(reconstructed) != clean:
        if report.edited_by_human:
            # 人可以直接改正文；此时 source 坐标已无法诚实映射到 current clean。
            # 读屏仍展示当前正式正文，而不是因旧 source-minus-removed 恒等式崩掉。
            # 新的剔除选择仍应基于一次明确的合并/编辑计划，不能猜坐标迁移。
            return _gap_segments(clean, 0, len(clean), scan_text(clean))
        raise ArtifactUnreadable(
            '原文减去 removal_report.removed[] 后不能逐字节重建 clean_text')
    if out:
        if out[0].start != 0 or out[-1].end != len(text):
            raise ArtifactUnreadable('stage0 分区没有覆盖完整原文')
        for left, right in zip(out, out[1:]):
            if left.end != right.start:
                raise ArtifactUnreadable(
                    f'stage0 分区在 {left.end} 与 {right.start} 之间重叠或留洞')
    return out


def stage0_segments(base: str | os.PathLike[str], sid: str) -> Stage0Segments:
    store = _store(base, sid, STAGE0_NODE)
    report = store.read_own(RemovalReport, removal.REPORT_REL)[0]
    text, reason = _original(report, base)
    if text is None:
        return Stage0Segments(
            source_sha1=report.source_sha1,
            available=False,
            reason=reason,
        )
    clean = store.read_text(removal.CLEAN_REL)
    return Stage0Segments(
        source_sha1=report.source_sha1,
        available=True,
        reason=None,
        segments=_stage0_segments(report, text, clean),
    )


def _removed_from_segment(
    segment: Stage0Segment,
    line_breaks: list[int],
) -> RemovedSegment:
    first = next((line.strip() for line in segment.text.splitlines() if line.strip()), '')
    marker_line = segment.marker_line or first or '人工剔除'
    kind = segment.removed_kind or nonbody_kind(marker_line) or '番外'
    marker_at = segment.text.find(marker_line)
    marker_at = 0 if marker_at < 0 else marker_at
    line_break = segment.text.find('\n', marker_at)
    after_marker = segment.text[line_break + 1:] if line_break >= 0 else ''
    return RemovedSegment(
        kind=kind,
        marker_line=marker_line,
        line_no=bisect_left(line_breaks, segment.start + marker_at) + 1,
        span=(segment.start, segment.end),
        code_points=segment.chars,
        head_preview=removal.preview_text(after_marker, 80),
        tail_preview=removal.preview_text(segment.text[-120:], 40),
        origin=segment.origin or 'human',
    )


def stage0_edit_plan(
    base: str | os.PathLike[str],
    sid: str,
    request: Stage0EditRequest,
) -> Stage0EditPlan:
    '''把分区 id 选择转成完整计划；未知/过期 id 在任何子进程启动前拒绝。'''
    report = stage0_report(base, sid)
    stale = []
    if request.expected_round != report.round:
        stale.append(f'round 页面={request.expected_round} 当前={report.round}')
    if request.expected_source_sha1 != report.source_sha1:
        stale.append('source_sha1 已变化')
    if request.expected_clean_sha1 != report.clean_sha1:
        stale.append('clean_sha1 已变化')
    if stale:
        raise HumanEditProtected('Stage 0 页面已过期，请刷新后重选：' + '；'.join(stale))

    view = stage0_segments(base, sid)
    if not view.available:
        raise SourceConflict(view.reason or '原始文件当前不可用于审校')
    by_id = {segment.id: segment for segment in view.segments}
    selected = set(request.removed_segment_ids)
    unknown = sorted(selected - set(by_id))
    if unknown:
        raise HumanEditProtected(f'页面提交了当前版本不存在的分区 id：{unknown[:8]}')
    end_markers = sorted(
        item for item in selected if by_id[item].kind == 'end_marker')
    if end_markers:
        raise HumanEditProtected('完结标记属于保留正文，不能在 Stage 0 作为剔除段选择')

    ordered = [segment for segment in view.segments if segment.id in selected]
    original = ''.join(segment.text for segment in view.segments)
    clean = ''.join(
        segment.text for segment in view.segments if segment.id not in selected)
    existing = {segment.span: segment for segment in report.removed}
    line_breaks = [index for index, char in enumerate(original) if char == '\n']
    removed = [
        existing.get((segment.start, segment.end))
        or _removed_from_segment(segment, line_breaks)
        for segment in ordered
    ]
    return Stage0EditPlan(
        expected_round=report.round,
        expected_source_sha1=report.source_sha1,
        expected_clean_sha1=report.clean_sha1,
        expected_total_code_points=report.total_code_points,
        clean_text=clean,
        clean_sha1=prov.content_sha1(clean.encode('utf-8')),
        removed=removed,
        actor=request.actor,
        reason=request.reason,
    )


@dataclass(frozen=True)
class _Stage1Material:
    clean: str
    stage0: RemovalReport
    report: PartitionReport
    main: MainText
    hook: HookCopy


def _stage1_material(base: str | os.PathLike[str], sid: str) -> _Stage1Material:
    store = _store(base, sid, STAGE1_NODE)
    stage0 = store.read_own(RemovalReport, partition.STAGE0_REPORT)[0]
    from novel.stages.stage0.eligibility import LEGACY_AUTO_APPROVAL, block_reason
    reason = block_reason(stage0)
    if reason == LEGACY_AUTO_APPROVAL:
        raise ApprovalRequired(
            'stage0 是历史自动批准（auto_zero_hit，已停产）；人工重批'
            f'（stage0.approve）前不能进入 stage1（{LEGACY_AUTO_APPROVAL}）')
    if reason is not None:
        raise ApprovalRequired(
            f'stage0 当前状态为 {stage0.approved!r}；先回 S-00 审阅并批准')
    clean = store.read_text(partition.CLEAN_TEXT)
    actual = prov.content_sha1(clean.encode('utf-8'))
    if actual != stage0.clean_sha1:
        raise PartitionInputMismatch(
            f'clean_text 实际 sha1={actual}，stage0 批准的是 {stage0.clean_sha1}')
    report = store.read_own(PartitionReport, partition.REPORT)[0]
    if report.input_sha1 != actual:
        raise PartitionInputMismatch(
            f'partition_report 输入={report.input_sha1}，当前 clean_text={actual}；区间已失效')
    if report.total_code_points != len(clean):
        raise PartitionInputMismatch(
            f'partition_report 长度={report.total_code_points}，当前 clean_text={len(clean)}')
    main = store.read_own(MainText, partition.MAIN_TEXT)[0]
    hook = store.read_own(HookCopy, partition.HOOK_COPY)[0]
    return _Stage1Material(clean, stage0, report, main, hook)


def _stage1_intervals(material: _Stage1Material) -> list[Stage1Segment]:
    text = material.clean
    report = material.report
    scan = scan_text(text)
    body_limit = scan.end_marker if scan.end_marker is not None else len(text)
    markers = [marker for marker in scan.chapters if marker.line_start < body_limit]
    if len(markers) != len(material.main.root) or len(markers) != report.num_chapters:
        raise PartitionChapterMismatch(
            f'当前识别 {len(markers)} 个章节标记，main_text 有 {len(material.main.root)} 章，'
            f'partition_report 记 {report.num_chapters} 章')
    first_marker = markers[0].line_start if markers else body_limit
    if report.hook_span != (0, first_marker) or material.hook.text != text[:first_marker]:
        raise PartitionChapterMismatch('hook_copy 与 partition_report.hook_span / clean_text 对不上')

    out: list[Stage1Segment] = []
    if first_marker:
        out.append(Stage1Segment(
            id=f's1:hook:0:{first_marker}',
            kind='hook',
            start=0,
            end=first_marker,
            chars=first_marker,
            flagged=False,
            text=text[:first_marker],
        ))
    for index, (marker, section, stored_span) in enumerate(
        zip(markers, material.main.root, report.chapter_spans)
    ):
        end = markers[index + 1].line_start if index + 1 < len(markers) else body_limit
        expected_span = (marker.content_start, end)
        if stored_span != expected_span or text[marker.content_start:end] != section.text:
            raise PartitionChapterMismatch(
                f'第 {index + 1} 章的落盘 span / 正文与当前 clean_text 对不上')
        if section.source_chapter_label != str(marker.chapter_no):
            raise PartitionChapterMismatch(
                f'第 {index + 1} 章原标号落盘为 {section.source_chapter_label!r}，'
                f'当前识别为 {marker.chapter_no!r}')
        out.append(Stage1Segment(
            id=f's1:chapter:{section.chapter_ordinal}:{marker.line_start}:{end}',
            kind='chapter',
            start=marker.line_start,
            end=end,
            chars=end - marker.line_start,
            flagged=False,
            text=text[marker.line_start:end],
            chapter_ordinal=section.chapter_ordinal,
            source_chapter_label=section.source_chapter_label,
        ))

    if body_limit < len(text):
        marker_end = _line_end(text, body_limit)
        trailing = text[marker_end:]
        end = len(text) if not trailing.strip() else marker_end
        out.append(Stage1Segment(
            id=f's1:end:{body_limit}:{end}',
            kind='end_marker',
            start=body_limit,
            end=end,
            chars=end - body_limit,
            flagged=False,
            text=text[body_limit:end],
        ))
        if end < len(text):
            out.append(Stage1Segment(
                id=f's1:hole:{end}:{len(text)}',
                kind='hole',
                start=end,
                end=len(text),
                chars=len(text) - end,
                flagged=True,
                text=text[end:],
            ))

    if out:
        if out[0].start != 0 or out[-1].end != len(text):
            raise PartitionChapterMismatch('stage1 分区没有覆盖完整 clean_text')
        for left, right in zip(out, out[1:]):
            if left.end != right.start:
                raise PartitionChapterMismatch(
                    f'stage1 分区在 {left.end} 与 {right.start} 之间重叠或留洞')
    return out


def stage1_segments(base: str | os.PathLike[str], sid: str) -> Stage1Segments:
    material = _stage1_material(base, sid)
    return Stage1Segments(
        input_sha1=material.report.input_sha1,
        segments=_stage1_intervals(material),
    )


def stage1_chapters(base: str | os.PathLike[str], sid: str) -> Stage1Chapters:
    material = _stage1_material(base, sid)
    return Stage1Chapters(chapters=[
        Stage1ChapterRow(
            chapter_ordinal=section.chapter_ordinal,
            source_chapter_label=section.source_chapter_label,
            chars=len(section.text),
            preview=removal.preview_text(section.text, 80),
        )
        for section in material.main.root
    ])


def stage1_hook_copy(base: str | os.PathLike[str], sid: str) -> HookCopyView:
    material = _stage1_material(base, sid)
    return HookCopyView(
        hook_copy=material.hook,
        note='文案段（钩子）。stage2 从不消费它——只在这里给人核对分章边界。',
    )
