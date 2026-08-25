#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''stage1 的九条确定性判据。'''
from __future__ import annotations

from collections import Counter

from contracts.invariants import MIN_CHAPTERS, NOISY_DUPLICATE_MIN
from contracts.schema.report import CheckReport, finding
from contracts.schema.stage0 import Stage0Snapshot, Stage0Revisions
from contracts.schema.stage1 import HookCopy, MainText, PartitionReport
from novel import prov
from novel.core.ingest import chapter_number, scan_text, tail_is_only_end_marker
from novel.core.warnings import describe
from novel.report.builder import build
from novel.report.findings import Outcome, ran
from novel.run import RunContext
from novel.store import ArtifactStore

from novel.stages.stage1.partition import (
    CLEAN_TEXT,
    HOOK_COPY,
    MAIN_TEXT,
    REPORT,
    STAGE0_STATE,
)


def _hit(rule: str, detail: str, *evidence: str):
    return finding(rule, detail=detail, evidence=list(evidence))


def check(
    report: PartitionReport,
    main_text: MainText,
    hook_copy: HookCopy,
    *,
    clean_text: str,
    stage0: Stage0Snapshot,
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
    if report.normalized_source_sha1 != stage0.normalized_source_sha1:
        input_problems.append(
            f'partition_report.normalized_source_sha1={report.normalized_source_sha1}，'
            f'stage0.normalized_source_sha1={stage0.normalized_source_sha1}')
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
    labels = [section.source_chapter_no for section in sections]
    expected_labels = [marker.chapter_no for marker in markers]
    if labels != expected_labels:
        label_problems.append(
            f'source_chapter_no={labels}，原文标记复扫为 {expected_labels}')
    # ⚠ 这里曾另有一段「正文 section_id 应为 sec_{i+2}」的复算。字段 2026-08-22 删除
    #   （一份产物只留一个系统主身份），那段复算随之作废——不是漏掉了。
    counts = Counter(labels)
    dup = sorted(label for label, count in counts.items() if count > 1)
    # ⚠ 也曾有一圈 `int(label)` 解析 + 「不是数字章号」的分支。`source_chapter_no`
    #   改型成 int 之后**载体自己就保证了它是数字**，那个分支结构上不可达；
    #   留着它等于让一条永不可能红的判据看起来像在守什么。
    numeric = sorted(set(labels))
    gaps = list(range(numeric[0], numeric[-1] + 1)) if numeric else []
    gaps = [number for number in gaps if number not in numeric]
    strictly_increasing = all(
        left < right for left, right in zip(labels, labels[1:]))
    out.append(ran(
        's1_chapter_labels',
        [_hit('s1_chapter_labels', detail, detail) for detail in label_problems],
        scanned=f'{len(labels)} 个原始章号 · 重号 {dup} · 缺号 {gaps} · '
                f'严格递增={strictly_increasing}',
    ))

    count_problems: list[str] = []
    independent = len(markers)
    observed_counts = {
        'partition_report.detected_chapter_marker_count': report.detected_chapter_marker_count,
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

    # R-1.15 源文本覆盖不变量：把 clean_text 完整分解为 hook + 逐章标记行 + 章正文
    # + tail，游走记账。⚠ 不与 build_sections 的产物比对（那是生产算法验证自己）；
    # 用的全是更底层、无状态的件：落盘 span 之间的 gap + `chapter_number` 文法解析。
    # 它与 s1_chapter_count 的复算值比对是两条不同的路：那边问「span 对不对」，
    # 这边问「每个字节去了哪、标记行是不是真的标记行」。
    decomposition_problems: list[str] = []
    accounted = 0
    hook_lo, hook_hi = report.hook_span
    accounted += hook_hi - hook_lo
    cursor = hook_hi
    for index, (lo, hi) in enumerate(report.chapter_spans):
        marker_region = clean_text[cursor:lo]
        if not marker_region.strip():
            decomposition_problems.append(
                f'第 {index + 1} 章与前一区域之间没有章标记行'
                f'（游标 {cursor} 直接贴着章正文起点 {lo}）')
        else:
            lines = [line for line in marker_region.split('\n') if line.strip()]
            if len(lines) != 1:
                decomposition_problems.append(
                    f'第 {index + 1} 章前的分隔区含 {len(lines)} 个非空行，'
                    '应恰好一行章标记')
            else:
                number = chapter_number(lines[0])
                if number is None:
                    decomposition_problems.append(
                        f'第 {index + 1} 章前的分隔行 {lines[0]!r} 不符合章标记文法')
                elif index < len(sections) and \
                        number != sections[index].source_chapter_no:
                    decomposition_problems.append(
                        f'第 {index + 1} 章前的分隔行解析出章号 {number}，'
                        f'章表记的是 {sections[index].source_chapter_no}')
        accounted += len(marker_region) + (hi - lo)
        cursor = hi
    if cursor != report.body_code_points:
        decomposition_problems.append(
            f'最后一章止于 {cursor}，正文切点是 {report.body_code_points}——'
            '之间有既不是章正文也不是 tail 的未记账字节')
    accounted += report.total_code_points - cursor
    if accounted != report.total_code_points:
        decomposition_problems.append(
            f'分解账 {accounted} 码点 != total_code_points={report.total_code_points}')
    out.append(ran(
        's1_decomposition',
        [_hit('s1_decomposition', detail, detail)
         for detail in decomposition_problems],
        scanned=f'{len(report.chapter_spans)} 个章标记行 · '
                f'{accounted}/{report.total_code_points} 码点已记账 · '
                f'tail {report.total_code_points - report.body_code_points} 码点',
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
        _hit('s1_warnings', describe(warning), warning.kind, describe(warning))
        for warning in report.warnings
    ]
    # ⚠ 2026-08-22 起比**整条证据**（章号 + 次数），不再只比条数。此前只对长度，
    #   于是「报了一条但报错了章号/次数」照样绿——而那正是这条告警全部的内容。
    expected = sorted(
        (label, count)
        for label, count in Counter(marker.chapter_no for marker in markers).items()
        if count >= NOISY_DUPLICATE_MIN
    )
    stored = sorted((w.source_chapter_no, w.count) for w in report.warnings)
    if stored != expected:
        warning_findings.append(_hit(
            's1_warnings',
            f'复扫应报告 noisy_duplicate (章号, 次数)={expected}，'
            f'partition_report 落盘的是 {stored}',
            str(expected),
            str(stored),
        ))
    out.append(ran(
        's1_warnings',
        warning_findings,
        scanned=f'{len(markers)} 个章号 · 阈值 {NOISY_DUPLICATE_MIN} 次',
    ))

    hook_problems: list[str] = []
    # ⚠ 这里曾查 `hook_copy.section_id == 'sec_0001'`。字段已随「一份产物只留一个
    #   系统主身份」删除（2026-08-22）；文案段恒只有一段，不需要身份。
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
    from novel.stages.stage0 import revisions as stage0_revisions
    stage0_log = store.read(Stage0Revisions, STAGE0_STATE)
    stage0_revisions.verify_chain(stage0_log)
    stage0 = stage0_revisions.snapshot_of(stage0_log)
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
