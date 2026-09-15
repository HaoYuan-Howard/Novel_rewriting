#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''S-00 / S-03 的连续文本视图。纯读、零判定、零产物写入。

Stage 0 的坐标系恒是归一化原始文件；Stage 1 的坐标系恒是 clean_text。前端只渲染
服务端已经切好的 text，不拿 Python 码点偏移去切 JavaScript UTF-16 字符串。
'''
from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass

from contracts.schema.api import (
    Stage0Report,
    Stage0Segment,
    Stage0Segments,
    Stage1Segment,
    Stage1View,
)
from contracts.schema.stage0 import Stage0Snapshot
from contracts.schema.stage1 import HookCopy, MainText
from novel import paths, prov
from novel.core.ingest import (
    DecodeProblem, ScanResult, decode_source_bytes, scan_text,
)
from novel.core.source import describe_source, resolve_recorded_source
from novel.core.warnings import describe
from novel.errors import (
    ApprovalRequired,
    ArtifactUnreadable,
    HumanEditProtected,
    PartitionChapterMismatch,
    PartitionInputMismatch,
    SourceConflict,
    UpstreamMissing,
)
from novel.orchestrator.state import readonly_ctx
from novel.stages.stage0 import removal
from novel.stages.stage1 import partition
from novel.stages.stage1.audit import Stage1Audit, audit_surface
from novel.store import ArtifactStore

STAGE0_NODE = 'stage0.remove'
STAGE1_NODE = 'stage1.partition'
_PARAGRAPH_BREAK = re.compile(r'\n[ \t　]*\n')


def _store(base: str | os.PathLike[str], sid: str, node: str) -> ArtifactStore:
    novel_dir = paths.resolve_novel_dir(base, sid)
    return ArtifactStore(readonly_ctx(novel_dir, node, prov.code_version()))


def stage0_report(base: str | os.PathLike[str], sid: str) -> Stage0Report:
    """S-00 的窄视图。状态从 revision 链派生，告警措辞在这一层现渲染。

    只返回页面实际消费的字段；告警证据和执行资格由后端唯一落点计算。
    """
    from novel.stages.stage0.eligibility import is_stage0_executable
    store = _store(base, sid, STAGE0_NODE)
    from novel.stages.stage0 import revisions as rev
    log = rev.read(store)
    if log is None:
        raise UpstreamMissing('stage0 还没跑，盘上没有 revision 链')
    report = rev.snapshot_of(log)
    return Stage0Report.model_validate({
        'original_filename': report.original_filename,
        'total_code_points': report.total_code_points,
        'warnings': [{'warning': warning.model_dump(mode='json'),
                      'message': describe(warning)}
                     for warning in report.warnings],
        'executable': is_stage0_executable(report),
        'revision_id': report.revision_id,
        'revision_origin': report.revision_origin,
    })


def _original(report: Stage0Snapshot,
              base: str | os.PathLike[str]) -> tuple[str | None, str | None]:
    """审阅屏要的那份原文。**取不到就明说不可用，绝不从别处变出一份。**

    ⚠ `source_ref` 指向书目录之外、不是产物，才可以直接摸盘（产物一律经 store）。
    ⚠ 两支的「取不到」含义相反，而 `source_ref` 让它们在类型上就分得开：
      `managed_upload` 住在 `<当前 base>/_service/uploads/<内容地址>/`，跟着 base 走，
      取不到才是真没了（且候选必须自证内容地址）；`external_file` 被删就是
      **世界真的变了**——那是 `R-0.2` 的意思：产物目录里没有第二份原文副本，
      取不到就是取不到。
    """
    source = resolve_recorded_source(report.source_ref, base)
    if source is None:
        return None, f'原始文件已不在记录位置：{describe_source(report.source_ref)}'
    try:
        raw = source.read_bytes()
    except OSError as exc:
        return None, f'原始文件读不了：{exc!r}'
    try:
        decoded = decode_source_bytes(raw)
    except DecodeProblem as exc:
        return None, f'原始文件已无法按摄入契约解码：{exc}'
    if decoded.normalized_source_sha1 != report.normalized_source_sha1:
        return (
            None,
            f'原始文件内容已变化：链根 {report.normalized_source_sha1[:10]}，'
            f'当前 {decoded.normalized_source_sha1[:10]}',
        )
    if len(decoded.text) != report.total_code_points:
        return (
            None,
            f'原始文件长度已变化：链根 {report.total_code_points}，当前 {len(decoded.text)}',
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
    nonbody_start = {marker.line_start: marker.kind for marker in scan.nonbody}
    for start, end in zip(points, points[1:]):
        if start == end:
            continue
        kind = 'end_marker' if scan.end_marker == start else 'keep'
        value = text[start:end]
        # 机器此刻新认出的非正文段（不落在当前 removed[] 里的那些）标成候选：
        # 有人工分叉的轮次不再自动三方合并（2026-08-21），「重选段」是唯一出路，
        # 出路必须看得见——否则人工只能靠肉眼在整本原文里找机器的新认知。
        # 预选集合只认 kind='remove'，这里只提示、不替人勾选。
        fresh_kind = nonbody_start.get(start) if kind != 'end_marker' else None
        out.append(Stage0Segment(
            id=f's0:{start}:{end}',
            kind=kind,
            start=start,
            end=end,
            chars=end - start,
            flagged=fresh_kind is not None,
            text=value,
            removed_kind=fresh_kind,
        ))
    return out


def _stage0_segments(report: Stage0Snapshot, text: str, clean: str) -> list[Stage0Segment]:
    actual_clean = prov.content_sha1(clean.encode('utf-8'))
    if actual_clean != report.clean_sha1:
        raise HumanEditProtected(
            f'clean_text 实际 sha1={actual_clean}，current 派生的是 {report.clean_sha1}；'
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
        if report.revision_origin == 'human_edit':
            # 人可以直接改正文；此时 source 坐标已无法诚实映射到 current clean。
            # 读屏仍展示当前正式正文，而不是因旧 source-minus-removed 恒等式崩掉。
            # 新的剔除选择仍应基于一次明确的合并/编辑计划，不能猜坐标迁移。
            return _gap_segments(clean, 0, len(clean), scan_text(clean))
        raise ArtifactUnreadable(
            '原文减去 current revision 的 removed[] 后不能逐字节重建 clean_text')
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
    from novel.stages.stage0 import revisions as rev
    log = rev.read(store)
    if log is None:
        raise UpstreamMissing('stage0 还没跑，盘上没有 revision 链')
    report = rev.snapshot_of(log)
    text, reason = _original(report, base)
    if text is None:
        return Stage0Segments(
            available=False,
            reason=reason,
        )
    clean = store.read_text(removal.CLEAN_REL)
    return Stage0Segments(
        available=True,
        reason=None,
        segments=_stage0_segments(report, text, clean),
    )


@dataclass(frozen=True)
class _Stage1Material:
    clean: str
    stage0: Stage0Snapshot
    main: MainText
    hook: HookCopy
    audit: Stage1Audit


def _stage1_material(base: str | os.PathLike[str], sid: str) -> _Stage1Material:
    store = _store(base, sid, STAGE1_NODE)
    from novel.stages.stage0 import revisions as rev
    log = rev.read(store)
    if log is None:
        raise UpstreamMissing('stage0 还没跑，盘上没有 revision 链')
    stage0 = rev.snapshot_of(log)
    from novel.stages.stage0.eligibility import is_stage0_executable
    if not is_stage0_executable(stage0):
        raise ApprovalRequired(
            f'stage0 当前状态为 {stage0.approval.status!r}；'
            '先重跑 stage0.remove 完成机械接受，再启动 stage1')
    clean = store.read_text(partition.CLEAN_TEXT)
    actual = prov.content_sha1(clean.encode('utf-8'))
    if actual != stage0.clean_sha1:
        raise PartitionInputMismatch(
            f'clean_text 实际 sha1={actual}，stage0 批准的是 {stage0.clean_sha1}')
    main = store.read_own(MainText, partition.MAIN_TEXT)[0]
    hook = store.read_own(HookCopy, partition.HOOK_COPY)[0]
    audit = audit_surface(
        clean_text=clean,
        stage0=stage0,
        main_text=main,
        hook_copy=hook,
    )
    if not audit.main_current or not audit.hook_current:
        raise PartitionChapterMismatch('Stage1 两份材料与当前 clean_text 的确定性切片不一致')
    return _Stage1Material(clean, stage0, main, hook, audit)


def _stage1_intervals(material: _Stage1Material) -> list[Stage1Segment]:
    text = material.clean
    parsed = material.audit.partition
    body_limit = parsed.body_limit
    markers = parsed.markers
    first_marker = parsed.first_marker
    numbers = [marker.chapter_no for marker in markers]
    counts = Counter(numbers)

    out: list[Stage1Segment] = []
    if first_marker:
        out.append(Stage1Segment(
            id=f's1:hook:0:{first_marker}',
            kind='hook',
            start=0,
            end=first_marker,
            chars=first_marker,
            text=text[:first_marker],
        ))
    for index, (marker, section) in enumerate(zip(markers, material.main.root)):
        end = markers[index + 1].line_start if index + 1 < len(markers) else body_limit
        delta = None if index == 0 else marker.chapter_no - markers[index - 1].chapter_no
        if delta is not None and delta <= 0:
            anomaly = 'reset'
        elif delta is not None and delta > 1:
            anomaly = 'gap'
        elif counts[marker.chapter_no] > 1:
            anomaly = 'repeat'
        else:
            anomaly = None
        out.append(Stage1Segment(
            id=f's1:chapter:{section.chapter_ordinal}:{marker.line_start}:{end}',
            kind='chapter',
            start=marker.line_start,
            end=end,
            chars=end - marker.line_start,
            text=text[marker.line_start:end],
            chapter_ordinal=section.chapter_ordinal,
            source_chapter_no=marker.chapter_no,
            label_delta=delta,
            anomaly=anomaly,
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
            text=text[body_limit:end],
        ))
        if end < len(text):
            out.append(Stage1Segment(
                id=f's1:hole:{end}:{len(text)}',
                kind='hole',
                start=end,
                end=len(text),
                chars=len(text) - end,
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


def stage1_view(base: str | os.PathLike[str], sid: str) -> Stage1View:
    material = _stage1_material(base, sid)
    segments = _stage1_intervals(material)
    return Stage1View(
        segments=segments,
        anomaly_count=sum(
            1 for segment in segments
            if segment.kind == 'chapter' and segment.anomaly is not None
        ),
    )
