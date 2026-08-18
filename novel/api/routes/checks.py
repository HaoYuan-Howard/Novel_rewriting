#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
checks.py —— `GET /api/novels/:sid/check/:kind`。八个层，一份 `CheckReport`。

⚠⚠ **这一层只【转发】一份别人算好的报告，它自己不造报告**（`R-11.4`）。
`badge` / `passed` / `counts` 由 `contracts/schema/report.py::badge_of` 折叠、
再由 `CheckReport` 的后置条件反过来断言——**服务端不重算，前端更不许算**。
本模块因此**不 import `novel.report`**（由 `tests/test_layering.py` 钉着）：
转发一份报告与造一份报告是两件事，而在 HTTP 这一层它们长得一样近。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from contracts.schema.report import CheckReport
from novel import paths, prov
from novel.api.deps import checked_kind, checked_sid, get_base
from novel.orchestrator.nodes import check_node
from novel.orchestrator.state import readonly_ctx
from novel.stages import check_only

router = APIRouter(prefix='/novels')


@router.get('/{sid}/check/{kind}', response_model=CheckReport)
def get_check(sid: str = Depends(checked_sid), kind: str = Depends(checked_kind),
              base: str = Depends(get_base)) -> CheckReport:
    """按**当前**判据复检这一层的存量产物。**0 成本：纯读，不发请求，不写盘。**

    三道闸，各答一个不同的问题：

      · `kind` 不在八个里            ⇒ **400**（`checked_kind`）：你给的层名不存在
      · 这一层没有产物 / 产物读不回来 ⇒ 404 / 409，由 `novel/api/errors.py` 那张表翻

    ⚠ 用 `check_node(kind)` 从 `NODES` 派生出「哪个节点接受 `--check-only`」，
    **不手写一张 层→节点 的表**：`:kind` 是**层**而复检是**节点**上的一条跑法，
    那座桥只许有一处——单数那一格由 `check_node` 自己把关，这里不再 `nodes[0]`。
    """
    run = check_only(kind)

    novel_dir = paths.resolve_novel_dir(base, sid)
    ctx = readonly_ctx(novel_dir, check_node(kind), prov.code_version())
    return run(ctx)
