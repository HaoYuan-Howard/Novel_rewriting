#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""已落人工作业之后，尽力而为写审批检查单的唯一 API 落点。"""
from __future__ import annotations

import warnings

from contracts.schema.api import GateKey
from novel.errors import (
    ApprovalRequired, ArtifactUnreadable, BlockingFindingsUnresolved,
    GateBlocked, UpstreamMissing, UpstreamStale, UpstreamUnproven,
)
from novel.orchestrator import approvals, jobs
from novel.orchestrator.write_admission import operation_scope


def stamp_approval_after_landed(
        base: str, sid: str, gate: GateKey) -> None:
    """短期占 writer 后盖审批章；任何失败都不改写已落作业的终态。

    产物里的 typed 决定是人工操作的真相；审批文档只是队列检查单。
    ``jobs.run`` 必须已经返回并释放自己的 writer，本函数只保护随后那一次
    ``_service/approvals`` 写入。agent 或 durable orphan 在两步之间抢锁时，
    明确披露“没盖章”但不把已经落地的 ``JobRecord`` 伪装成失败。
    """
    try:
        with operation_scope(
                base, sid, operation='approval-gate',
                details={'key': gate}):
            approvals.approve_gate(base, sid, gate)
    except jobs.JobConflict as exc:
        warnings.warn(
            f'人工决定已落盘，但审批章 {gate} 因同书 writer 冲突未写入'
            f'（{exc}）——没有并发写盘；等当前 writer 结束后在 approvals 屏补',
            RuntimeWarning,
            stacklevel=2,
        )
    except (
        ApprovalRequired, ArtifactUnreadable, BlockingFindingsUnresolved,
        GateBlocked, UpstreamMissing, UpstreamStale, UpstreamUnproven,
    ) as exc:
        warnings.warn(
            f'人工决定已落盘，但审批章 {gate} 没盖上（{exc}）——'
            '前置链在 approvals 屏补',
            RuntimeWarning,
            stacklevel=2,
        )


__all__ = ['stamp_approval_after_landed']
