#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''Stage0 执行资格的底层纯判据；消费者不依赖 Stage 实现。

谓词只写在这里，后端一律 import、前端只读 `Stage0Report.executable`，
不许复述条件（R-0.3b）。

v3：资格 = 机械 admission 已 passed，不是语义 approved。
'''
from __future__ import annotations

from contracts.schema.stage0 import Stage0Snapshot


def is_stage0_executable(report: Stage0Snapshot) -> bool:
    """当前 Stage0 快照是否具备放行 stage1 与付费链的资格。"""
    return (
        report.admission.status == 'passed'
        and report.admission.scope == 'mechanical'
    )
