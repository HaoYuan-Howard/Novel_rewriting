#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""novel.stages —— 各现役层的产地。**一层一个子包**，移植一层加一个。

【本模块只放一样东西：`stage` → 它那条 0 成本复检的实现】
它是 `--check-only` 与 `GET /api/novels/:sid/check/:kind` 的**共用落点**。
两个入口各自 `from novel.stages.stageN import ...` 一次，就是同一张映射的两份。

⚠⚠ **F8c：这张表只剩 stage0／stage1 两行。** Stage2/4/5/7 的复检随 STR-06
新链切换改为**节点级**：每个 `str06.` 节点自带 `--check-only`（免费复检＝
只允许缓存／wire 采用，需要新购就停在 owner 并写 needs_authorization），
入口是 `novel.cli run <str06节点> <sid> --check-only`；盘面只读投影走
`novel/views/str06.py`（`/api/str06/candidates/{candidate}/*`）。旧链那套
「一层一个 checker 产出 CheckReport」的桥（stage2 extract／stage4 plan／
stage5 review／stage7 rename）随旧链整层退役删除——不把 `--check-only`
重新画成层级报告，那会是同一事实的第二个落点（I-16）。
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
    return {'stage0': stage0_checks.run_check_only,
            'stage1': stage1_checks.run_check_only}


def check_only(stage: str) -> Callable[[RunContext], CheckReport]:
    """返回当前层的 0 成本复检函数；没有层级复检的层（新链 stage2/4/5/7）KeyError。"""
    return _checkers()[stage]


def checkable_stages() -> frozenset[str]:
    """本表覆盖的阶段；从 `_checkers()` 派生，不另抄清单。"""
    return frozenset(_checkers())
