#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage1 九条确定性判据；事实全部来自唯一运行时审计。"""
from __future__ import annotations

from contracts.schema.report import CheckReport, finding
from contracts.schema.stage0 import Stage0Snapshot, Stage0Transform
from contracts.schema.stage1 import HookCopy, MainText
from novel.report.builder import build
from novel.report.findings import Outcome, ran
from novel.run import RunContext
from novel.store import ArtifactStore
from novel.core.ingest_audit import Stage1Audit, audit_surface
from novel.stages.stage1.partition import CLEAN_TEXT, HOOK_COPY, MAIN_TEXT, STAGE0_STATE


def outcomes_of(audit: Stage1Audit) -> list[Outcome]:
    """把审计事实映射成报告；规则属性仍只由注册表决定。"""
    return [
        ran(
            rule,
            [
                finding(rule, detail=item.detail, evidence=list(item.evidence))
                for item in line.findings
            ],
            scanned=line.scanned,
        )
        for rule, line in audit.lines.items()
    ]


def check(
    main_text: MainText,
    hook_copy: HookCopy,
    *,
    clean_text: str,
    stage0: Stage0Snapshot,
) -> list[Outcome]:
    """兼容纯函数调用面；不再接收派生报表。"""
    return outcomes_of(audit_surface(
        clean_text=clean_text,
        stage0=stage0,
        main_text=main_text,
        hook_copy=hook_copy,
    ))


def run_check_only(ctx: RunContext) -> CheckReport:
    """复检当前输入与 Stage1 两份材料，不写盘。"""
    store = ArtifactStore(ctx)
    from novel.stages.stage0 import revisions as stage0_revisions
    stage0_log = store.read(Stage0Transform, STAGE0_STATE)
    stage0_revisions.verify_transform(stage0_log)
    stage0 = stage0_revisions.snapshot_of(stage0_log)
    clean_text = store.read_text(CLEAN_TEXT)
    main_text, _main_raw = store.read_own(MainText, MAIN_TEXT)
    hook_copy, _hook_raw = store.read_own(HookCopy, HOOK_COPY)
    audit = audit_surface(
        clean_text=clean_text,
        stage0=stage0,
        main_text=main_text,
        hook_copy=hook_copy,
    )
    return build(
        sid=ctx.sid,
        node=ctx.node,
        freshness=store.freshness(MAIN_TEXT),
        outcomes=outcomes_of(audit),
        accounting=ctx.accounting,
    )
