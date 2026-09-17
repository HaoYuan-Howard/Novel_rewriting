# -*- coding: utf-8 -*-
"""Analyze owner：一次联合语义购买与双侧 draft custody / recovery。

正式 API 仍从本包导出（兼容 ``from novel.stages.stage2 import analyze``）。
子模块：``purchase`` / ``recovery`` / ``draft_recovery``。
"""
from novel.stages.stage2.analyze.purchase import *  # noqa: F403
# 星号导入不导出下划线名；recovery 等经 purchase 直接引用，测试包面保留辅助。
from novel.stages.stage2.analyze.purchase import (  # noqa: F401
    _assert_current, _commit, _commit_legacy, _hard, _receipt, _LOCK,
)
