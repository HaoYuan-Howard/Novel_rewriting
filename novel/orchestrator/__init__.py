#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
novel.orchestrator —— 编排层。对应 `docs/schema/_orchestration.md`。

【它管什么】把一个 `(节点, 书, flags)` 变成一条**真的能粘进终端跑**的命令；
判定一本书跑到哪了；把一批书排成一条链；起子进程并如实记下这一次跑了什么。

【它不管什么】**不产生任何判定**（`R-13.6` → `R-11.4`：造第二个判官就是造第二个落点）。
本包一行都不许 import `novel.report`，由 `tests/test_layering.py` 的一条 AST 测试钉着。

【为什么仍然走子进程】`R-11.3` 的五条理由里，骨架 B1 消灭了三条
（进程级来源账 · import 期 `_load_dotenv` 单例 · 拿 `sys.exit` 当终态）。
**剩下两条仍独立成立，另加一条本项目特有的**：CPU 密集会阻塞界面 ·
`R-11.8` 要求展示与执行共用同一个 argv 构造函数（⇒ CLI 必须存在且与执行等价）·
批量服务里一个节点崩了不许带走整个队列。
"""
