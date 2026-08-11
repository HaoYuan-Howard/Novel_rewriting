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
from novel.errors import NodeNotPorted
from novel.orchestrator.nodes import check_nodes
from novel.orchestrator.state import readonly_ctx
from novel.stages import check_only

router = APIRouter(prefix='/novels')


@router.get('/{sid}/check/{kind}', response_model=CheckReport)
def get_check(sid: str = Depends(checked_sid), kind: str = Depends(checked_kind),
              base: str = Depends(get_base)) -> CheckReport:
    """按**当前**判据复检这一层的存量产物。**0 成本：纯读，不发请求，不写盘。**

    三道闸，各答一个不同的问题——合成一道就说不清了：

      · `kind` 不在八个里            ⇒ **400**（`checked_kind`）：你给的层名不存在
      · 这一层还没移植               ⇒ **501**（`NodeNotPorted`）：后端认得这个地址，
        只是还没实现。**不是 404**——404 会让前端以为自己请求错了，而这两件事
        该做的动作完全不同（`docs/schema/_api.md` §七 的裁决 4）
      · 这一层没有产物 / 产物读不回来 ⇒ 404 / 409，由 `novel/api/errors.py` 那张表翻

    ⚠ 用 `check_nodes(kind)` 从 `NODES` 派生出「哪个节点接受 `--check-only`」，
    **不手写一张 层→节点 的表**：`:kind` 是**层**而复检是**节点**上的一条跑法，
    那座桥只许有一处。
    """
    # ⚠⚠ **顺序是有意的：先问「移植了吗」，再问「有没有复检节点」。**
    # 反过来的话，`/check/stage0` 今天会答「stage0 没有节点接受 --check-only」——
    # 那句话技术上对，但对人**没用**：stage0 整层都还没建，节点表怎么填是 F 期的事。
    # 首要事实是「这一层还没建」，那颗地雷要等到它真的建起来才该说话。
    run = check_only(kind)                       # 未移植 ⇒ NodeNotPorted ⇒ 501

    nodes = check_nodes(kind)
    if not nodes:
        # ⚠ 走到这里意味着：`PORTED_STAGES` 说这一层移植好了，而 `NODES` 里
        # 它一个节点都不接受 `--check-only`。**今天不可能发生**（stage3 有）。
        # 它是 `check_nodes` 那段注释记着的那颗地雷：stage0 三个节点的 `flags` 全空，
        # 移植 stage0 而没给它登记跑法的那一刻，这句话正是要说给那个人听的。
        raise NodeNotPorted(
            f'{kind} 已在 `PORTED_STAGES` 里，而 `contracts.invariants.NODES` 里'
            f'它一个节点都不接受 `--check-only`——**两张表分家了**。'
            f'⚠ 那几个空 `flags` 曾经是**诚实的**（没有任何东西核对过那一层的跑法面），'
            f'但移植这一层的时候必须同时把它的跑法登记上')

    novel_dir = paths.resolve_novel_dir(base, sid)
    ctx = readonly_ctx(novel_dir, nodes[0], prov.code_version())
    return run(ctx)
