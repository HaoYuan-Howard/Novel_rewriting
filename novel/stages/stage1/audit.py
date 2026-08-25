#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage1 当前材料的唯一审计口。

producer、``--check-only``、页面、freshness 与人工审批都复用这里对当前
``clean_text`` 的同一次语义：原文现扫是权威，盘上 Stage1 只能与它逐字对账。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from contracts.invariants import MIN_CHAPTERS, NOISY_DUPLICATE_MIN
from contracts.schema.stage0 import Stage0Snapshot
from contracts.schema.stage1 import HookCopy, MainText
from novel import prov
from novel.core.ingest import (
    ChapterMarker, ScanResult, chapter_number, scan_text, tail_is_only_end_marker,
)
from novel.core.warnings import describe_noisy_duplicate


@dataclass(frozen=True)
class AuditFinding:
    detail: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class AuditLine:
    findings: tuple[AuditFinding, ...]
    scanned: str


@dataclass(frozen=True)
class PartitionScan:
    scan: ScanResult
    body_limit: int
    markers: tuple[ChapterMarker, ...]
    first_marker: int
    chapter_spans: tuple[tuple[int, int], ...]
    tail: str


@dataclass(frozen=True)
class Stage1Audit:
    partition: PartitionScan
    lines: dict[str, AuditLine]
    expected_main: tuple[dict[str, object], ...]
    expected_hook: dict[str, object]
    input_current: bool
    main_current: bool | None
    hook_current: bool | None

    @property
    def hard_findings(self) -> tuple[AuditFinding, ...]:
        """Stage1 的信息项不阻断，其余现役规则均是 HARD。"""
        return tuple(
            finding
            for rule, line in self.lines.items()
            if rule != 's1_warnings'
            for finding in line.findings
        )


def scan_partition(clean_text: str) -> PartitionScan:
    """只定义一次正文切点、章标记、切片和 tail。"""
    scan = scan_text(clean_text)
    body_limit = scan.end_marker if scan.end_marker is not None else len(clean_text)
    markers = tuple(marker for marker in scan.chapters if marker.line_start < body_limit)
    first_marker = markers[0].line_start if markers else body_limit
    spans = tuple(
        (
            marker.content_start,
            markers[index + 1].line_start if index + 1 < len(markers) else body_limit,
        )
        for index, marker in enumerate(markers)
    )
    return PartitionScan(
        scan=scan,
        body_limit=body_limit,
        markers=markers,
        first_marker=first_marker,
        chapter_spans=spans,
        tail=clean_text[body_limit:],
    )


def _audit_finding(detail: str, *evidence: object) -> AuditFinding:
    return AuditFinding(detail, tuple(str(value) for value in evidence))


def audit_surface(
    *,
    clean_text: str,
    stage0: Stage0Snapshot,
    main_text: MainText | None,
    hook_copy: HookCopy | None,
) -> Stage1Audit:
    """对当前输入和可选的两份 Stage1 材料做完整确定性审计。"""
    partition = scan_partition(clean_text)
    markers = partition.markers
    spans = partition.chapter_spans
    sections = list(main_text.root) if main_text is not None else []
    actual_sha1 = prov.content_sha1(clean_text.encode('utf-8'))
    input_current = stage0.clean_sha1 == actual_sha1

    input_findings = () if input_current else (_audit_finding(
        f'stage0 批准 clean_sha1={stage0.clean_sha1}，clean_text 实际={actual_sha1}',
        stage0.clean_sha1, actual_sha1,
    ),)

    labels = [marker.chapter_no for marker in markers]
    ordinals = [section.chapter_ordinal for section in sections]
    want_ordinals = list(range(1, len(sections) + 1))
    label_findings: list[AuditFinding] = []
    if main_text is not None and ordinals != want_ordinals:
        label_findings.append(_audit_finding(
            f'chapter_ordinal={ordinals}，应为连续阅读序 {want_ordinals}',
            ordinals, want_ordinals,
        ))
    counts = Counter(labels)
    duplicate = sorted(label for label, count in counts.items() if count > 1)
    numeric = sorted(counts)
    gaps = [
        number for number in range(numeric[0], numeric[-1] + 1)
        if number not in counts
    ] if numeric else []
    increasing = all(left < right for left, right in zip(labels, labels[1:]))

    expected_main = tuple(
        {'chapter_ordinal': index + 1, 'text': clean_text[lo:hi]}
        for index, (lo, hi) in enumerate(spans)
    )
    actual_main = tuple(
        section.model_dump() for section in sections
    ) if main_text is not None else None
    main_current = actual_main == expected_main if actual_main is not None else None
    count_findings: list[AuditFinding] = []
    if main_text is not None and len(sections) != len(markers):
        count_findings.append(_audit_finding(
            f'main_text={len(sections)}，clean_text 章节标记={len(markers)}',
            len(sections), len(markers),
        ))
    if main_text is not None:
        for index, expected in enumerate(expected_main):
            if index >= len(sections):
                break
            if sections[index].text != expected['text']:
                count_findings.append(_audit_finding(
                    f'第 {index + 1} 章 text 与 clean_text 当前切片不同',
                    index + 1,
                ))

    decomposition_findings: list[AuditFinding] = []
    accounted = partition.first_marker
    cursor = partition.first_marker
    for index, (lo, hi) in enumerate(spans):
        marker_region = clean_text[cursor:lo]
        lines = [line for line in marker_region.split('\n') if line.strip()]
        if len(lines) != 1:
            decomposition_findings.append(_audit_finding(
                f'第 {index + 1} 章前的分隔区含 {len(lines)} 个非空行，应恰好一行章标记',
                cursor, lo,
            ))
        else:
            number = chapter_number(lines[0])
            expected_number = markers[index].chapter_no
            if number != expected_number:
                decomposition_findings.append(_audit_finding(
                    f'第 {index + 1} 章前的分隔行解析为 {number}，复扫标记为 {expected_number}',
                    lines[0], expected_number,
                ))
        accounted += len(marker_region) + hi - lo
        cursor = hi
    accounted += len(clean_text) - cursor
    if accounted != len(clean_text):
        decomposition_findings.append(_audit_finding(
            f'分解账 {accounted} 码点 != clean_text {len(clean_text)} 码点',
            accounted, len(clean_text),
        ))

    residual_findings = tuple(
        _audit_finding(
            f'clean_text 第 {clean_text.count(chr(10), 0, marker.line_start) + 1} 行'
            f'仍命中非正文标记「{marker.title}」',
            marker.kind, marker.title,
        )
        for marker in partition.scan.nonbody
    )
    warning_findings = tuple(
        _audit_finding(
            describe_noisy_duplicate(label, count),
            'noisy_duplicate', label, count,
        )
        for label, count in sorted(counts.items())
        if count >= NOISY_DUPLICATE_MIN
    )

    expected_hook = {'text': clean_text[:partition.first_marker]}
    actual_hook = hook_copy.model_dump() if hook_copy is not None else None
    hook_current = actual_hook == expected_hook if actual_hook is not None else None
    hook_findings: list[AuditFinding] = []
    if hook_copy is not None and not hook_copy.text.strip():
        hook_findings.append(_audit_finding('hook_copy.text 去空白后为空', 'empty'))
    if hook_copy is not None and not hook_current:
        hook_findings.append(_audit_finding(
            'hook_copy.text 与 clean_text 当前第一个正文章标记之前的切片不同',
            0, partition.first_marker,
        ))

    min_findings = () if len(markers) >= MIN_CHAPTERS else (_audit_finding(
        f'只识别到 {len(markers)} 个正文章节，少于 {MIN_CHAPTERS}',
        len(markers), MIN_CHAPTERS,
    ),)
    tail_findings = () if tail_is_only_end_marker(partition.tail) else (_audit_finding(
        f'正文切点之后还有 {len(partition.tail.strip())} 码点，且不只是一个完结标记行',
        partition.tail[:160] or '<empty>',
    ),)

    lines = {
        's1_input_hash': AuditLine(input_findings, f'clean_text {len(clean_text)} 码点 · 1 个批准指纹'),
        's1_chapter_labels': AuditLine(
            tuple(label_findings),
            f'{len(labels)} 个原文章标记 · 重号 {duplicate} · 缺号 {gaps} · 严格递增={increasing}',
        ),
        's1_chapter_count': AuditLine(
            tuple(count_findings),
            f'main_text={len(sections) if main_text is not None else "未提供"} · '
            f'clean_text 章节标记={len(markers)}',
        ),
        's1_decomposition': AuditLine(
            tuple(decomposition_findings),
            f'{len(spans)} 个章标记行 · {accounted}/{len(clean_text)} 码点已记账 · '
            f'tail {len(partition.tail)} 码点',
        ),
        's1_nonbody_residual': AuditLine(
            residual_findings,
            f'clean_text {len(clean_text)} 码点 · {len(residual_findings)} 个命中',
        ),
        's1_warnings': AuditLine(
            warning_findings,
            f'{len(markers)} 个章号 · 阈值 {NOISY_DUPLICATE_MIN} 次',
        ),
        's1_hook': AuditLine(
            tuple(hook_findings),
            f'文案 {len(hook_copy.text) if hook_copy is not None else "未提供"} 码点 · '
            f'切片 [0, {partition.first_marker})',
        ),
        's1_min_chapters': AuditLine(min_findings, f'{len(markers)} 个正文章节'),
        's1_tail': AuditLine(
            tail_findings,
            f'tail {len(partition.tail)} 码点 · has_end_marker={partition.scan.end_marker is not None}',
        ),
    }
    return Stage1Audit(
        partition=partition,
        lines=lines,
        expected_main=expected_main,
        expected_hook=expected_hook,
        input_current=input_current,
        main_current=main_current,
        hook_current=hook_current,
    )
