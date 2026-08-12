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

from contracts.schema.report import CheckReport
from novel.errors import RunModeNotImplemented
from novel.run import RunContext


def _checkers() -> dict[str, Callable[[RunContext], CheckReport]]:
    """`stage` → 复检实现。**全模块唯一一份**，`check_only` 与 `checkable_stages` 都读它。

    ⚠ **懒 import**：`novel.stages.stage3.style` 会连带拉进 `novel.report` 与
    `novel.llm.client`。放在模块顶层，`import novel.stages` 就变成一件重活，
    而 `novel/api/routes/` 只是想拿一个函数。
    """
    from novel.stages.stage0 import checks as stage0_checks
    from novel.stages.stage1 import checks as stage1_checks
    from novel.stages.stage2 import extract
    from novel.stages.stage3 import style
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
            'stage2': extract.run_check_only,
            'stage3': style.run_check_only, 'stage4': plan.run_check_only,
            'stage5': review.run_check_only,
            'stage6': hook.run_check_only, 'stage7': rename.run_check_only}


def check_only(stage: str) -> Callable[[RunContext], CheckReport]:
    """这一层的 0 成本复检函数。**不跑它，只把它交出来。**

    ⚠⚠ **这里从前有两道闸，F5-5 之后只剩一道**（2026-08-12 用户裁决）。
    第一道是 `stage not in PORTED_STAGES ⇒ NodeNotPorted`——而 stage2 是最后一层，
    它一进 `PORTED_STAGES`，`STAGES − PORTED_STAGES` 就是**空集**，
    那道闸在生产路径上**再也触发不了**。
    ⇒ **退役它，而不是留着**：一条结构上永远不执行的分支与一条不存在的分支，
    对读的人是同一件事——留着还更坏，它会让人以为「未移植」这个状态今天还在。
    ⚠ 取值面那一半**没有掉**：`contracts.invariants.STAGES` 照旧八层，
    `/api/enums` 与队列一个取值都没少。掉的只是「这一层还没建」这个**运行时**状态。

    剩下的这一道答的是另一个问题：**在 `PORTED_STAGES` 里而本表没有 ⇒ 两张表分家了。**
    那不是「还没移植」，是「说自己移植了却没有复检实现」，报错要说出这一点。
    ⚠ 它今天同样不可能触发（`test_the_checker_table_covers_exactly_the_ported_stages`
    钉着 `checkable_stages() == PORTED_STAGES`），**而它与上面那道的处境不同**：
    上面那道是**结构上**不可能（空集），这一道是**被一条测试挡住**——
    删了它，那条测试红的时候拿到的就是一个 `KeyError` 而不是一句人话。
    """
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
