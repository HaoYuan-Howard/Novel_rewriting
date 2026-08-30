#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""人工终审工作包的 exception-first 报告投影。

这里只做 ``CheckReport`` 的无损裁剪：四态、来源边与所有异常行保留，正常 metric
保留读数；普通绿色行由完整扫描分母证明跑过。判定仍只来自 report builder。
"""
from __future__ import annotations

from contracts.schema.api import CompactApprovalCheck
from contracts.schema.report import CheckReport


def compact_approval_check(report: CheckReport) -> CompactApprovalCheck:
    """把生产报告裁成 Commander 需要整读的最小证据面。"""
    return CompactApprovalCheck(
        rules_scanned=len(report.rules),
        counts=report.counts,
        provenance=report.provenance,
        attention=[row for row in report.rules if row.status != 'ok'],
        metrics=[
            row for row in report.rules
            if row.sev == 'metric' and row.status == 'ok'
        ],
    )
