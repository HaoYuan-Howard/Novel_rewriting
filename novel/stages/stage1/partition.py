#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage1 分章产地。零 LLM，不剔除任何非正文。"""
from __future__ import annotations

from dataclasses import dataclass

from contracts.artifacts import (
    STAGE0_CLEAN_TEXT, STAGE0_REVISIONS, STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT,
)
from contracts.invariants import BODY_RATIO_MIN, MIN_CHAPTERS
from contracts.schema.report import CheckReport
from contracts.schema.stage0 import Stage0Revisions
from contracts.schema.stage1 import HookCopy, MainText, MainTextSection
from novel import prov
from novel.errors import (
    ApprovalRequired, HookCopyMissing, PartitionChapterMismatch, PartitionInputMismatch,
    ResidualNonbody, Stage0BodyRejected, TooFewChapters,
)
from novel.run import RunContext
from novel.store import ArtifactStore
from novel.stages.stage1.audit import scan_partition

STAGE0_STATE = STAGE0_REVISIONS
CLEAN_TEXT = STAGE0_CLEAN_TEXT
MAIN_TEXT = STAGE1_MAIN_TEXT
HOOK_COPY = STAGE1_HOOK_COPY


@dataclass(frozen=True)
class PartitionResult:
    main_text: MainText
    hook_copy: HookCopy
    tail: str


def partition_text(clean_text: str) -> PartitionResult:
    """分章纯函数；显式终态发生在模型构造和写盘之前。"""
    partition = scan_partition(clean_text)
    if partition.scan.nonbody:
        marker = partition.scan.nonbody[0]
        line_no = clean_text.count('\n', 0, marker.line_start) + 1
        raise ResidualNonbody(
            f'批准后的 clean_text 第 {line_no} 行仍命中非正文标记「{marker.title}」；'
            'stage1 只断言、不代替 stage0 静默删除')

    hook_text = clean_text[:partition.first_marker]
    if not hook_text.strip():
        raise HookCopyMissing(
            '第一个正文章标记之前没有非空开篇文案；stage3、stage6、stage7 都依赖它')
    if len(partition.markers) < MIN_CHAPTERS:
        raise TooFewChapters(
            f'只识别到 {len(partition.markers)} 个正文章节，少于最低要求 {MIN_CHAPTERS}')

    sections: list[MainTextSection] = []
    for index, (marker, (start, end)) in enumerate(
        zip(partition.markers, partition.chapter_spans), start=1,
    ):
        raw = clean_text[start:end]
        if not raw.strip():
            raise PartitionChapterMismatch(
                f'第 {index} 个章节标记（原章号 {marker.chapter_no}）之后没有正文；'
                f'独立计数会得到 {len(partition.markers)} 章，但章表无法一一落盘')
        sections.append(MainTextSection(chapter_ordinal=index, text=raw))

    return PartitionResult(
        main_text=MainText.model_validate(sections),
        hook_copy=HookCopy(text=hook_text),
        tail=partition.tail,
    )


def run_partition(
    ctx: RunContext,
    *,
    dry_run: bool = False,
) -> tuple[PartitionResult, CheckReport | None]:
    """读取 Stage0 当前批准轮，分章并落两份承重材料。"""
    store = ArtifactStore(ctx)
    from novel.stages.stage0 import revisions as stage0_revisions
    stage0_log = store.read(Stage0Revisions, STAGE0_STATE)
    stage0_revisions.verify_chain(stage0_log)
    stage0 = stage0_revisions.snapshot_of(stage0_log)
    clean_text = store.read_text(CLEAN_TEXT)
    from novel.stages.stage0.eligibility import is_stage0_executable
    if not is_stage0_executable(stage0):
        raise ApprovalRequired(
            f'stage0 当前状态为 {stage0.approval.status!r}；人工批准前不能启动 stage1')
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

    result = partition_text(clean_text)
    if dry_run:
        return result, None

    # 保留 landed-red 的恢复语义：材料先落盘，再以同一审计口给出红色报告。
    store.write(MAIN_TEXT, result.main_text)
    store.write(HOOK_COPY, result.hook_copy)
    from novel.stages.stage1.checks import run_check_only
    return result, run_check_only(ctx)
