#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
novel.llm —— 付费调用那一层。

⚠ **B1 只落了这里面两样不碰网络的东西**：`CachePolicy`（两个开关）与 `CostLedger`（成本账）。
它们先落地是因为 `novel/run.py::RunContext` 的形状里有它们——
一个字段类型不存在的 dataclass 不叫「先建骨架」，叫「还没建」。
客户端 / 缓存键 / 降级阶梯 / `FakeTransport` / prompt 渲染归 **B2**。

⚠ 本包在 `tests/test_layering.py` 的 `json.load` 白名单里（缓存文件是它自己的格式），
**这不是给别处开的口子**：读产物仍然只有 `novel/store.py` 一条路。
"""
from __future__ import annotations
