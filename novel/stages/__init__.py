#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""novel.stages —— 七个现役层的产地。**一层一个子包**，移植一层加一个。

【本模块只放一样东西：`stage` → 它那条 0 成本复检的实现】
它是 `--check-only` 与 `GET /api/novels/:sid/check/:kind` 的**共用落点**。
两个入口各自 `from novel.stages.stageN import ...` 一次，就是同一张映射的两份，
映射覆盖七个现役层（Stage3 已退役），并由 CLI 与 HTTP 共用。
"""
from __future__ import annotations

from collections.abc import Callable

from contracts.schema.report import CheckReport
from novel.run import RunContext


def _checkers() -> dict[str, Callable[[RunContext], CheckReport]]:
    """`stage` → 复检实现。**全模块唯一一份**，`check_only` 与 `checkable_stages` 都读它。

    ⚠ **懒 import**：各 stage checker 会连带拉进报告与 LLM 模块。放在模块顶层，
    `import novel.stages` 就变成一件重活，而 API 只是想拿一个函数。
    """
    from novel.stages.stage0 import checks as stage0_checks
    from novel.stages.stage1 import checks as stage1_checks
    from novel.stages.stage2 import extract
    from novel.stages.stage4 import plan
    from novel.stages.stage5 import review
    from novel.stages.stage6 import hook
    from novel.stages.stage7 import rename
    # ⚠ stage5 挂的是**验收相**（`stage5.review`），不是写作相：判据表两相共用一张，
    #   而 `--check-only` 那条路读的是验收报告这份产物。写作节点没有 `--check-only`
    #   （它的 `flags` 里就没有），`check_nodes('stage5')` 因此恰好是一个。
    # ⚠ stage2 挂的是**逐章那一相**（`stage2.blueprint`）——`NODES` 里只有它带 `--check-only`。
    #   而那一相**两份产物一起读**（判 `canonical_unique` 要 global、
    #   判 `present_ids_dangling` 要 registry）⇒「全书那一相的判据没有 0 成本复检」
    #   这句话不成立：判据表只有一张，两个节点共用。
    return {'stage0': stage0_checks.run_check_only,
            'stage1': stage1_checks.run_check_only,
            'stage2': extract.run_check_only, 'stage4': plan.run_check_only,
            'stage5': review.run_check_only,
            'stage6': hook.run_check_only, 'stage7': rename.run_check_only}


def check_only(stage: str) -> Callable[[RunContext], CheckReport]:
    """返回当前层的 0 成本复检函数。"""
    return _checkers()[stage]


def checkable_stages() -> frozenset[str]:
    """本表覆盖的当前阶段；从 `_checkers()` 派生，不另抄清单。"""
    return frozenset(_checkers())
