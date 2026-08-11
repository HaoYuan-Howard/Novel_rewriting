#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
novel.report —— 校验报告的构造器。契约形态在 `contracts/schema/report.py`。

【这一层的那一句话】**`build()` 遍历判据注册表，不遍历 findings。**
报告不是由「实现产出了什么」生成的，而是由「注册了哪些判据」生成的。
⇒ 一条判据不可能因为「实现忘了调」而从报告里消失——它会变成一行 `skipped`。

四个模块：
  · `findings.py`   判据实现的产出（`Outcome`）——**只装事实，不判 status**
  · `accounting.py` `ACCOUNTING`：每层声明自己有哪些记账键。会炸的那张表
  · `builder.py`    ⭐ `build()`
  · `render.py`     `CheckReport` → 人读文本。**不落盘**（出口在报告里，stdout 只是渲染）
"""
from __future__ import annotations

from novel.report.accounting import ACCOUNTING, build_accounting
from novel.report.builder import build
from novel.report.findings import Outcome
from novel.report.render import render

__all__ = ['ACCOUNTING', 'Outcome', 'build', 'build_accounting', 'render']
