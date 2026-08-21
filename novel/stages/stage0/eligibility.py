#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''stage0 执行资格的唯一落点（2026-08-21 裁决，P0-1 关闭自动绕过）。

【为什么必须有这一个模块】
历史上有五处各自判 `report.approved == 'approved'`：s0_approval 判据、stage1 启动
前置、队列人工闸、付费审批防线、stage1 物料视图。停产 `auto_zero_hit` 之后，
「等于 approved」不再等于「可以继续」——**存量自动批产物读得、跑不得**。
五处若各自补 `and approval_origin == 'human'`，迟早漏一处；那漏掉的一处就是
自动绕过的复活口。⇒ 谓词只写在这里，消费方一律 import，不许复述条件
（`docs/LESSONS.md` 第 21 条：改向时把同一条 R 的全部守卫一次列全）。

【为什么只 import contracts】
本模块被 `novel/stages` / `novel/orchestrator` / `novel/views` 三层共用，
依赖必须压到最轻，避免把 removal.py 的整棵 import 图拖进队列与 API 进程。
'''
from __future__ import annotations

from contracts.schema.stage0 import RemovalReport

#: 历史自动批产物被拦下时的稳定理由码。前端与判据报告按它认状态，
#: 不从 message 措辞里猜（与 `STAGE0_WARNINGS`「认 kind 不认前缀」同一条纪律）。
LEGACY_AUTO_APPROVAL = 'legacy_auto_approval_requires_review'


def is_stage0_executable(report: RemovalReport) -> bool:
    """这份报告是否**当前**具备放行 stage1 与付费链的资格。

    只有「人工显式批准」一种诚实来源：`approved == 'approved'` 且
    `approval_origin == 'human'`。`auto_zero_hit` 是历史自动批取值——
    那正是纯数字番外盲区（`docs/schema/stage1.md` §五）绕过人工防线的路，
    2026-08-21 起停产；存量产物在人工重批（`stage0.approve`）后恢复资格。
    """
    return report.approved == 'approved' and report.approval_origin == 'human'


def block_reason(report: RemovalReport) -> str | None:
    """不具执行资格时的稳定理由码；可执行时为 `None`。

    两态分开报：`pending` 是「等人」；历史自动批是「等人**重新**确认」——
    它在界面上都长成「已批准」，不分开报会把一本跑不了的书显示成可继续。
    """
    if is_stage0_executable(report):
        return None
    if report.approved == 'approved':
        return LEGACY_AUTO_APPROVAL
    return 'pending'
