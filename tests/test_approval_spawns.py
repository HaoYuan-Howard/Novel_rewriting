#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审批模块本体已删除（2026-10-04 清理批，用户裁决不保留旧产物读兼容）。

原文件守三层退役事实；本文件只保留「模块不得复活」这一方向：
  1. `novel.orchestrator.approvals` 整个模块不在盘上——存量审批账解码链
     （`_ApprovalRecordV9` 族／`read_projection`／`approval_head_bytes`）与
     写面均已删除，旧根一律 `legacy_incompatible` 拒识；
  2. 回收站侧仍保留 `_service/approvals/` 目录的互斥锁（已迁入
     `novel/orchestrator/trash.py::approval_exclusion_lock`）与路径常量，
     其行为由 `tests/test_trash.py` 覆盖；
  3. 历史审批文件对 surface seal 只以原始字节形态参与
     （`inspection.py::_legacy_approval_bytes`）。
"""
from __future__ import annotations

import importlib.util


def test_approvals_module_stays_deleted():
    """审批解码模块不得复活：旧根拒识由产物层闭合，不再有第二个解码面。"""
    assert importlib.util.find_spec(
        'novel.orchestrator.approvals') is None, (
        'novel.orchestrator.approvals 回来了——旧审批账按 2026-10-04 裁决'
        '不再解码，旧根一律 legacy_incompatible 拒识')
