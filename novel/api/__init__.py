#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
novel.api —— HTTP 那一层。**它是全仓唯一许可 import `fastapi` 的地方**
（白名单在 `tests/test_layering.py::_WEB_STACK_ALLOWED`，逐条写着理由）。

【本模块**故意是空的**，那是一条设计约束不是懒】
`novel.api.enums` 与 `novel.api.errors` 是**纯函数**，不需要 web 栈。
`__init__` 一旦 re-export 了 `app`，`import novel.api.enums` 就会连带把 fastapi
拖进来——于是 E 期那两批本来跑得动的测试全部退化成 `importorskip`，
而 `importorskip` 跳过去的东西**看起来像绿的**。
⇒ 这里一行 import 都不许有，由 `test_the_api_package_init_imports_nothing` 钉住。

【这一层能做什么、不能做什么】
  · 能：读（经 `novel.store` / `novel.views` / `novel.orchestrator`）· 转发一份别人算好的报告
  · **不能**：写任何东西（`R-11.3`：服务进程绝不落产物盘）· 自己造判定（`R-11.4`）·
    发一次 LLM 请求（`R-11.3` 的孪生条：一切花钱的操作走子进程）
  三条都由 `tests/test_layering.py` 的 AST 守卫钉着，不靠自觉。
"""
from __future__ import annotations
