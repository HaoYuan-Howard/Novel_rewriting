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
六个现役层手写一张表，加一层加一行。

现役判据表由 Stage0、Stage1 和 story-v3 的 str06 三个模块组成。
故事表只登记当前 schema/compiler/validator/repository 的实际机械职责；
语义质量另标未评，不让旧故事判据名在新契约下假装仍然有效。
"""
from __future__ import annotations

from contracts.judges import stage0  # noqa: F401  —— import 即注册，副作用就是它的全部意义
from contracts.judges import stage1  # noqa: F401
from contracts.judges import str06  # noqa: F401  —— STR-06 新链（F8a 激活）
