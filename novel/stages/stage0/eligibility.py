#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''stage0 执行资格的唯一落点（2026-08-21 裁决，P0-1 关闭自动绕过）。

【为什么必须有这一个模块】
历史上有六处各自判 `report.approved == 'approved'`（当时还是四个平铺字段）：s0_approval 判据、stage1 启动
前置、队列人工闸、付费审批防线、stage1 物料视图，以及**前端审校台**。
外部评审点名要收口的正是这六处 —— 判「可不可以继续」的地方一多，
改向时漏掉的那一处就是绕过闸门的复活口（`docs/LESSONS.md` 第 21 条：
改向时把同一条 R 的全部守卫一次列全）。⇒ 谓词只写在这里，后端一律 import、
前端只读 `Stage0Report.executable`（本谓词的投影），**都不许复述条件**。

⚠ **2026-08-22 之后这个谓词恰好等于 `approval.status == 'approved'`**，
别把它当成「已经没用了」——收口的理由从来不是条件复杂，是「同一条 R 只有一个守卫」。
它此前一度更严（还要求 `origin == 'human'`）；那一格随 `auto_zero_hit` 死值
一起删掉了（见 `contracts/schema/stage0.py::ApprovedApproval`），
而 `R-0.3b` 下一次收紧时，收紧点仍然只有这一处。

⚠ 这里曾有第二个函数 `block_reason()`，返回稳定理由码 `pending` /
`legacy_auto_approval_requires_review`，并投影成 HTTP 的 `Stage0Report.block_reason`。
`auto_zero_hit` 删除之后它只剩一个取值 ⇒ 与 `executable` 表达的是同一个 bit
（那正是 `Stage0Report._verdict_and_reason_are_one_state` 当年校的东西），
**一个 bit 只留一个投影**，故整格下线。要再加第二种「批了却跑不得」的理由码时
把它整个加回来，不要留一个空壳在这儿等。

【为什么只 import contracts】
本模块被 `novel/stages` / `novel/orchestrator` / `novel/views` 三层共用，
依赖必须压到最轻，避免把 removal.py 的整棵 import 图拖进队列与 API 进程。
'''
from __future__ import annotations

from contracts.schema.stage0 import RemovalReport


def is_stage0_executable(report: RemovalReport) -> bool:
    """这份报告是否**当前**具备放行 stage1 与付费链的资格。

    批准只有人一种诚实来源：`approval.status == 'approved'`。
    「这一段到底是不是番外」没有结构不变量可查（`R-0.7`），
    而按本层的设计这道闸门是纯数字番外盲区（`docs/schema/stage1.md` §五）
    全链唯一的防线 ⇒ 它必须是人点的，且必须在花钱的 stage2 之前。
    """
    return report.approval.status == 'approved'
