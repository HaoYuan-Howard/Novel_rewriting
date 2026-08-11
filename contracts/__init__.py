#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
contracts —— 契约层。可单独 import，**不拉运行时**。

【这里为什么有一行 import 而不是空文件】判据的行必须在**任何人**碰到 `REGISTRY` 之前
就已经注册完。Python 保证 `import contracts.任何东西` 都会先执行本文件，
所以把 `judges` 挂在这里，`REGISTRY` 的内容就**不取决于谁先 import 了谁**——
而正向覆盖率、落盘闸门、报告三个消费方都在问「装了哪些判据」这个问题，
三个都不该得到不同的答案。

⚠ 它**不**破坏 `contracts/invariants.py` 的叶子性：`judges/*` 只 import `contracts.rules`，
而 `rules.py` 只 import `contracts.invariants`。整条链上一个第三方依赖都没有
（pydantic 在 `contracts/schema/`，不在这条链上）。
"""
from __future__ import annotations

from contracts import judges  # noqa: F401  —— import 即注册，副作用就是它的全部意义
