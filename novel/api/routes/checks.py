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
from novel.errors import RunModeNotImplemented
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
      · 后端认得这个地址而没有实现   ⇒ **501**（`RunModeNotImplemented`）。
        **不是 404**——404 会让前端以为自己请求错了，而这两件事该做的动作完全不同
        （`docs/schema/_api.md` §七 的裁决 4）
      · 这一层没有产物 / 产物读不回来 ⇒ 404 / 409，由 `novel/api/errors.py` 那张表翻

    ⚠⚠ **这里从前有第四道：「这一层还没移植」⇒ `NodeNotPorted`。**
    F5-5 移植完最后一层（stage2）之后 `STAGES − PORTED_STAGES` 是**空集**，
    那道闸结构上再也触发不了 ⇒ **退役**（2026-08-12 用户裁决）。
    **501 这一格没有掉**，只是换了一个更准确的产地：今天答 501 的是
    「这条跑法登记在案而还没接线」，不再是「整层还没建」。

    ⚠ 用 `check_nodes(kind)` 从 `NODES` 派生出「哪个节点接受 `--check-only`」，
    **不手写一张 层→节点 的表**：`:kind` 是**层**而复检是**节点**上的一条跑法，
    那座桥只许有一处。
    """
    # ⚠ 顺序仍照旧（先取实现、再问节点），而**它原来的理由已经失效**：
    #   那条理由是「首要事实是这一层还没建」，而今天八层全建好了。
    #   留着这个顺序没有代价，但**别再照着那条失效的理由去解释它**。
    run = check_only(kind)

    nodes = check_nodes(kind)
    if not nodes:
        # ⚠ 走到这里意味着：`PORTED_STAGES` 说这一层移植好了，而 `NODES` 里
        # 它一个节点都不接受 `--check-only`。**今天不可能发生**
        # （`test_every_ported_stage_has_exactly_one_check_node` 两侧夹死）。
        # 它是 `check_nodes` 那段注释记着的那颗地雷：一层的 `flags` 全空时，
        # 这句话正是要说给移植那一层的人听的。
        raise RunModeNotImplemented(
            f'{kind} 已在 `PORTED_STAGES` 里，而 `contracts.invariants.NODES` 里'
            f'它一个节点都不接受 `--check-only`——**两张表分家了**。'
            f'⚠ 那几个空 `flags` 曾经是**诚实的**（没有任何东西核对过那一层的跑法面），'
            f'但移植这一层的时候必须同时把它的跑法登记上')

    novel_dir = paths.resolve_novel_dir(base, sid)
    ctx = readonly_ctx(novel_dir, nodes[0], prov.code_version())
    return run(ctx)
