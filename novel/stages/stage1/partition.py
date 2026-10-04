#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage1 分章产地。零 LLM，不剔除任何非正文。"""
from __future__ import annotations

from contracts.artifacts import (
    STAGE0_CLEAN_TEXT, STAGE0_REVISIONS, STAGE1_HOOK_COPY, STAGE1_MAIN_TEXT,
)
from contracts.invariants import BODY_RATIO_MIN
from contracts.schema.report import CheckReport
from contracts.schema.stage0 import Stage0Transform
from novel import prov
from novel.errors import (
    ApprovalRequired, PartitionInputMismatch, Stage0BodyRejected,
)
from novel.run import RunContext
from novel.store import ArtifactStore
from novel.core import partition

STAGE0_STATE = STAGE0_REVISIONS
CLEAN_TEXT = STAGE0_CLEAN_TEXT
MAIN_TEXT = STAGE1_MAIN_TEXT
HOOK_COPY = STAGE1_HOOK_COPY


def run_partition(
    ctx: RunContext,
    *,
    dry_run: bool = False,
) -> tuple[partition.PartitionResult, CheckReport | None]:
    """读取 Stage0 当前机械 transform，分章并落两份承重材料。"""
    store = ArtifactStore(ctx)
    from novel.stages.stage0 import revisions as stage0_revisions
    stage0_log = store.read(Stage0Transform, STAGE0_STATE)
    stage0_revisions.verify_transform(stage0_log)
    stage0 = stage0_revisions.snapshot_of(stage0_log)
    clean_text = store.read_text(CLEAN_TEXT)
    from novel.core.eligibility import is_stage0_executable
    if not is_stage0_executable(stage0):
        raise ApprovalRequired(
            f'stage0 当前 admission 为 {stage0.admission.status!r}/'
            f'{stage0.admission.scope!r}；'
            '先重跑 stage0.remove 完成机械接受，再启动 stage1')
    actual_input = prov.content_sha1(clean_text.encode('utf-8'))
    if actual_input != stage0.clean_sha1:
        raise PartitionInputMismatch(
            f'clean_text 实际 sha1={actual_input}，stage0 记录的是 {stage0.clean_sha1}；'
            '盘上正文已在机械通过后变化')
    ratio = stage0.body_code_points / stage0.total_code_points
    if ratio < BODY_RATIO_MIN:
        raise Stage0BodyRejected(
            f'stage0 清理后只保留 {ratio:.1%} 正文，低于 {BODY_RATIO_MIN:.0%}，'
            '不能把疑似截断的文本送入下游')

    result = partition.partition_text(clean_text)
    if dry_run:
        return result, None

    # 保留 landed-red 的恢复语义：材料先落盘，再以同一审计口给出红色报告。
    store.write(MAIN_TEXT, result.main_text)
    store.write(HOOK_COPY, result.hook_copy)
    from novel.stages.stage1.checks import run_check_only
    return result, run_check_only(ctx)
