#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''stage1 分章产地。零 LLM，不剔除任何非正文。'''
from __future__ import annotations

from contracts.artifacts import (
    STAGE0_CLEAN_TEXT, STAGE0_REMOVAL_REPORT, STAGE1_HOOK_COPY,
    STAGE1_MAIN_TEXT, STAGE1_PARTITION_REPORT,
)

from collections import Counter
from dataclasses import dataclass

from contracts.invariants import BODY_RATIO_MIN, MIN_CHAPTERS, NOISY_DUPLICATE_MIN
from contracts.schema.report import CheckReport
from contracts.schema.stage0 import RemovalReport
from contracts.schema.stage1 import (
    HookCopy,
    MainText,
    MainTextSection,
    PartitionReport,
    Stage1Warning,
)
from novel import prov
from novel.core.ingest import ScanResult, scan_text
from novel.errors import (
    ApprovalRequired,
    PartitionChapterMismatch,
    PartitionInputMismatch,
    ResidualNonbody,
    Stage0BodyRejected,
    HookCopyMissing,
    TooFewChapters,
)
from novel.run import RunContext
from novel.store import ArtifactStore

STAGE0_REPORT = STAGE0_REMOVAL_REPORT
CLEAN_TEXT = STAGE0_CLEAN_TEXT
MAIN_TEXT = STAGE1_MAIN_TEXT
HOOK_COPY = STAGE1_HOOK_COPY
REPORT = STAGE1_PARTITION_REPORT


@dataclass(frozen=True)
class PartitionResult:
    main_text: MainText
    hook_copy: HookCopy
    report: PartitionReport
    tail: str
    scan: ScanResult


def _warning(labels: list[int]) -> list[Stage1Warning]:
    out: list[Stage1Warning] = []
    for label, count in sorted(Counter(labels).items()):
        if count >= NOISY_DUPLICATE_MIN:
            out.append(Stage1Warning(
                kind='noisy_duplicate',
                message=f'chapter_no={label} 出现 {count} 次（≥{NOISY_DUPLICATE_MIN}），'
                        '疑似固定标记被误判成章节号，请人工核对原文标记规律',
            ))
    return out


def partition_text(clean_text: str, *, source_sha1: str) -> PartitionResult:
    '''分章纯函数。显式终态发生在任何模型构造或磁盘写入之前。'''
    scan = scan_text(clean_text)
    if scan.nonbody:
        marker = scan.nonbody[0]
        line_no = clean_text.count('\n', 0, marker.line_start) + 1
        raise ResidualNonbody(
            f'批准后的 clean_text 第 {line_no} 行仍命中非正文标记「{marker.title}」；'
            'stage1 只断言、不代替 stage0 静默删除')

    body_limit = scan.end_marker if scan.end_marker is not None else len(clean_text)
    markers = [marker for marker in scan.chapters if marker.line_start < body_limit]
    first_marker = markers[0].line_start if markers else body_limit
    hook_text = clean_text[:first_marker]
    if not hook_text.strip():
        raise HookCopyMissing(
            '第一个正文章标记之前没有非空开篇文案；stage3、stage6、stage7 都依赖它')
    if len(markers) < MIN_CHAPTERS:
        raise TooFewChapters(
            f'只识别到 {len(markers)} 个正文章节，少于最低要求 {MIN_CHAPTERS}')

    mains: list[MainTextSection] = []
    spans: list[tuple[int, int]] = []
    for index, marker in enumerate(markers):
        start = marker.content_start
        end = markers[index + 1].line_start if index + 1 < len(markers) else body_limit
        raw = clean_text[start:end]
        if not raw.strip():
            raise PartitionChapterMismatch(
                f'第 {index + 1} 个章节标记（原章号 {marker.chapter_no}）之后没有正文；'
                f'独立计数会得到 {len(markers)} 章，但章表无法一一落盘')
        spans.append((start, end))
        mains.append(MainTextSection(
            chapter_ordinal=index + 1,
            source_chapter_no=marker.chapter_no,
            text=raw,
        ))

    main_text = MainText.model_validate(mains)
    hook_copy = HookCopy(text=hook_text)
    report = PartitionReport(
        source_sha1=source_sha1,
        input_sha1=prov.content_sha1(clean_text.encode('utf-8')),
        total_code_points=len(clean_text),
        body_code_points=body_limit,
        num_chapters=len(markers),
        has_end_marker=scan.end_marker is not None,
        warnings=_warning([marker.chapter_no for marker in markers]),
        hook_span=(0, first_marker),
        chapter_spans=spans,
    )
    return PartitionResult(
        main_text=main_text,
        hook_copy=hook_copy,
        report=report,
        tail=clean_text[body_limit:],
        scan=scan,
    )


def run_partition(
    ctx: RunContext,
    *,
    dry_run: bool = False,
) -> tuple[PartitionResult, CheckReport | None]:
    '''读取 stage0 的批准轮，分章并落三份产物。'''
    store = ArtifactStore(ctx)
    stage0 = store.read(RemovalReport, STAGE0_REPORT)
    clean_text = store.read_text(CLEAN_TEXT)
    from novel.stages.stage0.eligibility import LEGACY_AUTO_APPROVAL, block_reason
    reason = block_reason(stage0)
    if reason == LEGACY_AUTO_APPROVAL:
        raise ApprovalRequired(
            'stage0 是历史自动批准（auto_zero_hit，已停产）；人工重批'
            f'（stage0.approve）前不能启动 stage1（{LEGACY_AUTO_APPROVAL}）')
    if reason is not None:
        raise ApprovalRequired(
            f'stage0 当前状态为 {stage0.approved!r}；人工批准前不能启动 stage1')
    actual_input = prov.content_sha1(clean_text.encode('utf-8'))
    if actual_input != stage0.clean_sha1:
        raise PartitionInputMismatch(
            f'clean_text 实际 sha1={actual_input}，stage0 批准的是 {stage0.clean_sha1}；'
            '盘上正文已在批准后变化')
    ratio = stage0.body_code_points / stage0.total_code_points
    if ratio < BODY_RATIO_MIN:
        raise Stage0BodyRejected(
            f'stage0 清理后只保留 {ratio:.1%} 正文，低于 {BODY_RATIO_MIN:.0%}，'
            '即使人工点了批准也不能把疑似截断的文本送入下游')

    result = partition_text(clean_text, source_sha1=stage0.source_sha1)
    if dry_run:
        return result, None

    store.write(MAIN_TEXT, result.main_text)
    store.write(HOOK_COPY, result.hook_copy)
    store.write(REPORT, result.report)
    from novel.stages.stage1.checks import run_check_only
    return result, run_check_only(ctx)
