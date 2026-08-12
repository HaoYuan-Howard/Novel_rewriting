#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''stage1 的八条确定性判据。'''
from __future__ import annotations

from collections import Counter

from contracts.invariants import MIN_CHAPTERS, NOISY_DUPLICATE_MIN
from contracts.schema.report import CheckReport, finding
from contracts.schema.stage0 import RemovalReport
from contracts.schema.stage1 import HookCopy, MainText, PartitionReport
from novel import prov
from novel.core.ingest import scan_text, tail_is_only_end_marker
from novel.report.builder import build
from novel.report.findings import Outcome, ran
from novel.run import RunContext
from novel.store import ArtifactStore

from novel.stages.stage1.partition import (
    CLEAN_TEXT,
    HOOK_COPY,
    MAIN_TEXT,
    REPORT,
    STAGE0_REPORT,
)


def _hit(rule: str, detail: str, *evidence: str):
    return finding(rule, detail=detail, evidence=list(evidence))


def check(
    report: PartitionReport,
    main_text: MainText,
    hook_copy: HookCopy,
    *,
    clean_text: str,
    stage0: RemovalReport,
) -> list[Outcome]:
    '''跨四份产物重算分章；报告字段不能拿自己给自己作证。'''
    out: list[Outcome] = []
    sections = list(main_text.root)
    scan = scan_text(clean_text)
    body_limit = scan.end_marker if scan.end_marker is not None else len(clean_text)
    markers = [marker for marker in scan.chapters if marker.line_start < body_limit]
    first_marker = markers[0].line_start if markers else body_limit
    expected_spans = [
        (
            marker.content_start,
            markers[index + 1].line_start if index + 1 < len(markers) else body_limit,
        )
        for index, marker in enumerate(markers)
    ]

    input_problems: list[str] = []
    actual_sha1 = prov.content_sha1(clean_text.encode('utf-8'))
    if report.input_sha1 != actual_sha1:
        input_problems.append(
            f'partition_report.input_sha1={report.input_sha1}，clean_text 实际={actual_sha1}')
    if stage0.clean_sha1 != actual_sha1:
        input_problems.append(
            f'stage0 批准 clean_sha1={stage0.clean_sha1}，clean_text 实际={actual_sha1}')
    if report.source_sha1 != stage0.source_sha1:
        input_problems.append(
            f'partition_report.source_sha1={report.source_sha1}，'
            f'stage0.source_sha1={stage0.source_sha1}')
    if report.total_code_points != len(clean_text):
        input_problems.append(
            f'total_code_points={report.total_code_points}，输入实际={len(clean_text)}')
    if report.body_code_points != body_limit:
        input_problems.append(
            f'body_code_points={report.body_code_points}，按首个完结标记复算={body_limit}')
    if report.has_end_marker != (scan.end_marker is not None):
        input_problems.append(
            f'has_end_marker={report.has_end_marker}，复扫结果={scan.end_marker is not None}')
    out.append(ran(
        's1_input_hash',
        [_hit('s1_input_hash', detail, detail) for detail in input_problems],
        scanned=f'2 个内容指纹 · clean_text {len(clean_text)} 码点 · 1 个正文切点',
    ))

    label_problems: list[str] = []
    ordinals = [section.chapter_ordinal for section in sections]
    expected_ordinals = list(range(1, len(sections) + 1))
    if ordinals != expected_ordinals:
        label_problems.append(
            f'chapter_ordinal={ordinals}，应为连续阅读序 {expected_ordinals}')
    labels = [section.source_chapter_label for section in sections]
    expected_labels = [str(marker.chapter_no) for marker in markers]
    if labels != expected_labels:
        label_problems.append(
            f'source_chapter_label={labels}，原文标记复扫为 {expected_labels}')
    section_ids = [section.section_id for section in sections]
    expected_ids = [f'sec_{index + 2:04d}' for index in range(len(sections))]
    if section_ids != expected_ids:
        label_problems.append(
            f'正文 section_id={section_ids}，文案占 sec_0001 后应为 {expected_ids}')
    counts = Counter(labels)
    dup = sorted(label for label, count in counts.items() if count > 1)
    parsed_labels: list[int] = []
    for label in labels:
        try:
            parsed_labels.append(int(label))
        except ValueError:
            label_problems.append(f'source_chapter_label={label!r} 不是数字章号')
    numeric = sorted(set(parsed_labels))
    gaps = list(range(numeric[0], numeric[-1] + 1)) if numeric else []
    gaps = [number for number in gaps if number not in numeric]
    strictly_increasing = all(
        left < right for left, right in zip(parsed_labels, parsed_labels[1:])
    ) if len(parsed_labels) == len(labels) else False
    out.append(ran(
        's1_chapter_labels',
        [_hit('s1_chapter_labels', detail, detail) for detail in label_problems],
        scanned=f'{len(labels)} 个原始章号 · 重号 {dup} · 缺号 {gaps} · '
                f'严格递增={strictly_increasing}',
    ))

    count_problems: list[str] = []
    independent = len(markers)
    observed_counts = {
        'partition_report.num_chapters': report.num_chapters,
        'main_text': len(sections),
        'chapter_spans': len(report.chapter_spans),
        'clean_text 章节标记': independent,
    }
    if len(set(observed_counts.values())) != 1:
        count_problems.append(f'四路章节计数不一致：{observed_counts}')
    if list(report.chapter_spans) != expected_spans:
        count_problems.append(
            f'chapter_spans 与 clean_text 复算值不同：'
            f'{list(report.chapter_spans)} != {expected_spans}')
    for index, section in enumerate(sections):
        if index >= len(report.chapter_spans):
            break
        lo, hi = report.chapter_spans[index]
        if hi > len(clean_text) or section.text != clean_text[lo:hi]:
            count_problems.append(
                f'第 {index + 1} 章 text 与 chapter_spans[{index}]={lo, hi} 的输入切片不同')
    out.append(ran(
        's1_chapter_count',
        [_hit('s1_chapter_count', detail, detail) for detail in count_problems],
        scanned=' · '.join(f'{key}={value}' for key, value in observed_counts.items()),
    ))

    residual = scan.nonbody
    out.append(ran(
        's1_nonbody_residual',
        [_hit(
            's1_nonbody_residual',
            f'clean_text 第 {clean_text.count(chr(10), 0, marker.line_start) + 1} 行'
            f'仍命中非正文标记「{marker.title}」',
            marker.kind,
            marker.title,
        ) for marker in residual],
        scanned=f'clean_text {len(clean_text)} 码点 · {len(residual)} 个命中',
    ))

    warning_findings = [
        _hit('s1_warnings', warning.message, warning.kind, warning.message)
        for warning in report.warnings
    ]
    noisy_labels = sorted(
        label for label, count in Counter(marker.chapter_no for marker in markers).items()
        if count >= NOISY_DUPLICATE_MIN
    )
    if len(report.warnings) != len(noisy_labels):
        warning_findings.append(_hit(
            's1_warnings',
            f'复扫应报告 noisy_duplicate 章号 {noisy_labels}，'
            f'partition_report 实际有 {len(report.warnings)} 条',
            str(noisy_labels),
            str(len(report.warnings)),
        ))
    out.append(ran(
        's1_warnings',
        warning_findings,
        scanned=f'{len(markers)} 个章号 · 阈值 {NOISY_DUPLICATE_MIN} 次',
    ))

    hook_problems: list[str] = []
    if hook_copy.section_id != 'sec_0001':
        hook_problems.append(f'hook_copy.section_id={hook_copy.section_id!r}，应为 sec_0001')
    if not hook_copy.text.strip():
        hook_problems.append('hook_copy.text 去空白后为空')
    if report.hook_span != (0, first_marker):
        hook_problems.append(
            f'hook_span={report.hook_span}，第一个正文章前缀应为 {(0, first_marker)}')
    lo, hi = report.hook_span
    if hi > len(clean_text) or hook_copy.text != clean_text[lo:hi]:
        hook_problems.append('hook_copy.text 与 hook_span 指向的 clean_text 切片不同')
    out.append(ran(
        's1_hook',
        [_hit('s1_hook', detail, detail) for detail in hook_problems],
        scanned=f'hook_span={report.hook_span} · 文案 {len(hook_copy.text)} 码点',
    ))

    out.append(ran(
        's1_min_chapters',
        [_hit(
            's1_min_chapters',
            f'只识别到 {independent} 个正文章节，少于 {MIN_CHAPTERS}',
            str(independent),
            str(MIN_CHAPTERS),
        )] if independent < MIN_CHAPTERS else [],
        scanned=f'{independent} 个正文章节',
    ))

    tail = clean_text[body_limit:]
    tail_problems = []
    if not tail_is_only_end_marker(tail):
        tail_problems.append(
            f'正文切点之后还有 {len(tail.strip())} 码点，且不只是一个完结标记行')
    out.append(ran(
        's1_tail',
        [_hit('s1_tail', detail, tail[:160] or '<empty>')
         for detail in tail_problems],
        scanned=f'tail {len(tail)} 码点 · has_end_marker={scan.end_marker is not None}',
    ))
    return out


def run_check_only(ctx: RunContext) -> CheckReport:
    '''复检盘上 stage1 三份产物及当前 stage0 输入，不写盘。'''
    store = ArtifactStore(ctx)
    stage0 = store.read(RemovalReport, STAGE0_REPORT)
    clean_text = store.read_text(CLEAN_TEXT)
    main_text, _main_raw = store.read_own(MainText, MAIN_TEXT)
    hook_copy, _hook_raw = store.read_own(HookCopy, HOOK_COPY)
    report, _report_raw = store.read_own(PartitionReport, REPORT)
    outcomes = check(
        report,
        main_text,
        hook_copy,
        clean_text=clean_text,
        stage0=stage0,
    )
    return build(
        sid=ctx.sid,
        node=ctx.node,
        freshness=store.freshness(REPORT),
        outcomes=outcomes,
        accounting=ctx.accounting,
    )
