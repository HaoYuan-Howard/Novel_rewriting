"""运行页工作单元：处理来源取持久账；Stage4 审阅进度投影已退役。

处理记录不是当前产物接受证明。Stage4 四道人工闸退役（2026-09-15 全自动
裁决）后，design/structure 的 reviewer 工作单元只剩存量书历史账，本投影
不再为其合成「审阅义务」行；运行控制仍由原服务提供。
"""
from __future__ import annotations

from typing import Any

from novel.errors import TerminalState
from novel.work_units import list_unit_views


def get(base: str, sid: str) -> tuple[list[dict[str, Any]] | None, str | None]:
    try:
        rows = list_unit_views(base, sid)
    except (TerminalState, OSError, ValueError) as exc:
        return None, str(exc)
    if rows is None:
        return None, '当前输出根尚无现役工作流身份'
    return rows, None
