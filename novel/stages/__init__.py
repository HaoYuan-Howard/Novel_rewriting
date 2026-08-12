#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""novel.stages —— 八层的产地。**一层一个子包**，移植一层加一个。

【本模块只放一样东西：`stage` → 它那条 0 成本复检的实现】
它是 `--check-only` 与 `GET /api/novels/:sid/check/:kind` 的**共用落点**。
两个入口各自 `from novel.stages.stageN import ...` 一次，就是同一张映射的两份，
而它们分家的表现是：CLI 复检 stage2 跑得动，界面上那一屏却 501。
"""
from __future__ import annotations

from collections.abc import Callable

from contracts.rules import PORTED_STAGES
from contracts.schema.report import CheckReport
from novel.errors import NodeNotPorted, RunModeNotImplemented
from novel.run import RunContext


def _checkers() -> dict[str, Callable[[RunContext], CheckReport]]:
    """`stage` → 复检实现。**全模块唯一一份**，`check_only` 与 `checkable_stages` 都读它。

    ⚠ **懒 import**：`novel.stages.stage3.style` 会连带拉进 `novel.report` 与
    `novel.llm.client`。放在模块顶层，`import novel.stages` 就变成一件重活，
    而 `novel/api/routes/` 只是想拿一个函数。
    """
    from novel.stages.stage3 import style
    from novel.stages.stage4 import plan
    from novel.stages.stage5 import review
    from novel.stages.stage6 import hook
    from novel.stages.stage7 import rename
    # ⚠ stage5 挂的是**验收相**（`stage5.review`），不是写作相：判据表两相共用一张，
    #   而 `--check-only` 那条路读的是验收报告这份产物。写作节点没有 `--check-only`
    #   （它的 `flags` 里就没有），`check_nodes('stage5')` 因此恰好是一个。
    return {'stage3': style.run_check_only, 'stage4': plan.run_check_only,
            'stage5': review.run_check_only,
            'stage6': hook.run_check_only, 'stage7': rename.run_check_only}


def check_only(stage: str) -> Callable[[RunContext], CheckReport]:
    """这一层的 0 成本复检函数。**不跑它，只把它交出来。**

    ⚠ **两道闸，各答一个不同的问题**，合成一道就说不清了：
      · `stage not in PORTED_STAGES` ⇒ `NodeNotPorted`：整层还没移植（HTTP 501）。
        它在取值面里是**对的**——队列要排得出这一步、`/api/enums` 要发得出这个取值。
      · 在 `PORTED_STAGES` 里而本表没有 ⇒ **两张表分家了**。那不是「还没移植」，
        是「说自己移植了却没有复检实现」，报错要说出这一点。

    ⚠ 第二道今天不可能触发（`test_every_ported_stage_has_a_checker` 钉着
    `checkable_stages() == PORTED_STAGES`），留着是因为 F 期每移植一层都要来改这两处，
    而漏改的那一次**只有它会出声**。
    """
    if stage not in PORTED_STAGES:
        raise NodeNotPorted(
            f'{stage} 属 F 期，本仓还没有它的实现（已移植：{sorted(PORTED_STAGES)}）。'
            f'⚠ 它在取值面里是**对的**——`contracts.invariants.STAGES` 有八层，'
            f'而「今天跑不动」只该在真去跑它的那一刻说出来')

    got = _checkers().get(stage)
    if got is None:
        raise RunModeNotImplemented(
            f'{stage} 在 `contracts.rules.PORTED_STAGES` 里，而 '
            f'`novel/stages/__init__.py::check_only` 没有它的复检实现——'
            f'**两张表分家了**。移植一层要同时改这两处')
    return got


def checkable_stages() -> frozenset[str]:
    """本表覆盖了哪几层。**从同一份 `_checkers()` 派生**，不另抄一份清单。

    它的用途只有一个：让测试拿它与 `PORTED_STAGES` **两侧夹死**
    （多一层、少一层都红），形态照抄 `test_registry_covers_exactly_the_ported_stages`。
    """
    return frozenset(_checkers())
