#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
novel.api.routes —— 九个端点。**路由表的唯一落点是
`docs/design_brief/04_delivery.md` §路由表**（那张表的标题写着「固定，不要改」）。

⚠ 这一层只做三件事：**校验路径参数 → 调一个纯读函数 → 把结果原样交出去**。
一个格子都不许在这里算——判定在 `contracts/schema/report.py`，
组装在 `novel/views/`，贵贱在 `novel/orchestrator/nodes.py::is_paid`。
路由里出现第四种计算，那就是那件事的第二个落点。
"""
from __future__ import annotations
