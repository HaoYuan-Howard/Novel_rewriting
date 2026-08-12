#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
contracts.judges —— 判据的【行】。**与它守的契约同层**（`Q-12`：守卫本身要有守卫）。

【为什么行在契约层、实现在运行时】一条判据由两半组成：
  · **行**（`rule` / `sev` / `owner` / `who_checks` / `requirement`）—— 它是**契约**：
    与 `wontfix.json` 的键、`_provenance.fixes` 的引用、需求表的「谁在查」那一列绑死；
  · **实现** —— 它是代码，住在 `novel/stages/stageN/checks.py`。

分开放的收益是 `tests/` 能在**不 import 运行时**的前提下验双向覆盖率
（`contracts/` 的四个消费方里三个不需要运行时）。

⚠⚠ **本模块显式列出每一个 import，不 glob、不自动发现。**
`REGISTRY` 的内容一旦取决于文件系统或 import 顺序，「装了哪些判据」就成了一个
要去盘上查才知道的问题——而正向覆盖率、落盘闸门、报告三个消费方都在问这件事。
八层手写一张表，加一层加一行。
"""
from __future__ import annotations

from contracts.judges import stage0  # noqa: F401  —— import 即注册，副作用就是它的全部意义
from contracts.judges import stage1  # noqa: F401
from contracts.judges import stage2  # noqa: F401
from contracts.judges import stage3  # noqa: F401
from contracts.judges import stage4  # noqa: F401
from contracts.judges import stage5  # noqa: F401
from contracts.judges import stage6  # noqa: F401
from contracts.judges import stage7  # noqa: F401

#: 已经写了判据行的 stage。**必须与 `contracts.rules.PORTED_STAGES` 相等**，
#: 由 `tests/test_rules_registry.py::test_registry_covers_exactly_the_ported_stages` 两侧夹死。
#:
#: ⚠⚠ **`stage2` 是 2026-08-12（F5-5）补上的最后一层**，八层齐了。它进来那一刻
#: 同时发生四件事，每一件都是**本来就欠着、只是没有东西红**：
#:   · 那 75 条判据从「写好了没人读」变成「四张表立刻索要实现」（照 F4-4/F4-6 的分家）；
#:   · `STAGES − PORTED_STAGES` **成了空集** ⇒ `NodeNotPorted` 在生产路径上再也触发不了
#:     （用户裁决：退役它，见 `novel/errors.py`）；
#:   · 正向覆盖率开始查 `R-2` + `R-3` 两整节；
#:   · 三条「一条都不跑而照样绿」的守卫的守卫当场红——**那正是它们存在的理由**。
LOADED = ('stage0', 'stage1', 'stage2', 'stage3', 'stage4', 'stage5', 'stage6', 'stage7')
