#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
novel.views —— 把盘上那些东西拼成**界面上的一屏**。纯读。

【它为什么是一个单独的包，而不是写在 `novel/api/routes/` 里】
一句话：**这样它不需要 fastapi 就能测**。一屏的组装逻辑（哪些格子来自产物、
哪些是现算的、缺上游时哪几格变成 null）是本项目真正容易错的地方，
而把它写在路由函数里，就只能靠起一个 HTTP 服务去测——
于是在没装 web 栈的机器上，这一批测试会退化成 `importorskip`，
**而 skip 掉的东西看起来像绿的**。

【这一层不许做的三件事，各由一条 AST 守卫钉着】
  · **不许写任何东西**（`R-11.3`：服务进程绝不落产物盘）
  · **不许 import `novel.llm.*`**（一切花钱的操作走子进程 ⇒ 结构上发不出请求）
  · **不许直接 import `novel.report` / `contracts.judges`**（`R-11.4`：不重算判定）

⚠⚠ **第三条守卫的边界，说清楚免得读的人高估它**：`novel/views/stage3.py`
import 了 `novel.stages.stage3.style`，而**那个模块自己 import 了 `novel.report`**
（它要为 `--check-only` 造报告）。所以「够不到判官」在**传递闭包**上并不成立，
AST 守卫查的是**直接** import。
⇒ `R-11.4` 在这一层的**主保证不是 import 守卫，是类型**：
`contracts/schema/api.py::StyleView` 里根本没有 `badge` / `passed` / `counts` 这几个格子
（`test_the_api_models_never_carry_a_verdict` 钉着），**视图没有地方放一个判定**。
import 守卫是第二道，它挡的是「顺手在这里 `build()` 一份报告」那种写法。
"""
from __future__ import annotations
